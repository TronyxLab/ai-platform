# GREP_SUMMARY: smoke, platform, docker, compose, loki, platform_services, platform_env, SMOKE_ENV, external-networks
# STRUCTURE: ┌SMOKE_ENV constants┐ → ┌_collect_external_networks┐ ◇ ┌_run_docker_smoke┐ + ┌_wait_for_loki_ready┐ → ┌platform_env┐ → ┌platform_services┐(Docker guard → ensure_volumes → compose up → loki poll → yield → teardown)

# region MODULE_CONTRACT
## @purpose — Session-scoped fixtures and helpers for smoke platform tests, extracted from conftest.py
##            to eliminate cross-file fixture import lifecycle ambiguity (TASK: fix smoke test ordering).
## @scope — Used by test_smoke_platform.py and test_platform_endpoints.py.
## @invariants
##   - platform_services is session-scoped (not module) — containers live for entire session
##   - SMOKE_ENV — ЛЕНИВЫЙ (T12.4 T-7, PEP 562 __getattr__): platform-env.yaml грузится при
##     первом обращении (не import-time); fallback на env_defaults_generated.py при отсутствии файла
##   - platform_env — module-scoped (T12.3 T-6): инжектится только для модулей, запрашивающих её;
##     platform_services (session) не зависит от неё — compose получает SMOKE_ENV через merge
##   - Docker guard built into platform_services — skip if Docker unavailable or production
##   - Conditional activation via requires_docker marker — static tests don't trigger compose
##   - started/failed — снимок под _WAVE_STATE_LOCK перед yield (T12.2 T-3); финальный wave-event
##     сигналится в finally основного потока (T12.2 T-4)
##   - Retry-rate компоуз-стартов трекается (_RETRY_STATS) и проверяется в sessionfinish (T12.7 T-11)
##   - Loki /ready — честный флаг (T12.7 T-10): loki_ready в результате + фикстура loki_ready
## @rationale — Cross-file module-scoped fixture imports cause non-deterministic teardown
##              ordering. Session scope extracted to dedicated module eliminates race condition.
## @changes — LAST_CHANGE: 2026-08-05 | DevPlan 136 W12: T12.2 (wave lock+snapshot+finally),
##            T12.3 (platform_env module-scope, platform_services без platform_env),
##            T12.4 (lazy SMOKE_ENV + fallback), T12.7 (loki_ready, retry-stats), T12.9 (host-dirs cleanup)
##            2026-07-12 | Extracted from conftest.py SMOKE_PLATFORM_FIXTURES region
## @modulemap — _collect_external_networks → parse compose YAMLs for external:true networks
##              _run_docker_smoke → centralised docker subprocess runner with SMOKE_ENV
##              _wait_for_loki_ready → HTTP poll for Loki /ready (scratch image has no curl/wget)
##              platform_env → inject/restore SMOKE_ENV in os.environ (module-scoped)
##              platform_services → lifecycle fixture: start/stop compose stack
# endregion MODULE_CONTRACT

import contextlib
import functools
import json
import logging
import os
import platform as _platform
import subprocess
import textwrap
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import yaml as _yaml

from _conftest.honesty import require_docker_or_fail
from _conftest.infra import infra as _infra
from _conftest.ldd import _ensure_volume_dirs
from _conftest.networks import TEST_NETWORKS, get_network_manager, is_production_host
from _conftest.smoke_env_generated import SMOKE_ENV_GENERATED
from _conftest.wave_pipeline import _init_wave_events, signal_wave_ready

# region SMOKE_PLATFORM_FIXTURES
## @purpose — Session-scoped fixtures and helpers for smoke platform tests.
##            Moved from test_smoke_platform.py to eliminate cross-file fixture
##            import lifecycle ambiguity (TASK: fix smoke test ordering).
## @scope — Used by test_smoke_platform.py and test_platform_endpoints.py.
## @invariants
##   - platform_services is session-scoped (not module) — containers live for entire session
##   - SMOKE_ENV contains MINIMAL test values, not production secrets
##   - Docker guard built into platform_services — skip if Docker unavailable or production
##   - Conditional activation via requires_docker marker — static tests don't trigger compose
## @rationale — Cross-file module-scoped fixture imports cause non-deterministic teardown
##              ordering. Session scope in conftest.py eliminates the race condition.

# ── Platform compose --wait timeouts with env-var override ──────────────
_IS_MACOS = _platform.system() == "Darwin"


def is_macos() -> bool:
    """Detect macOS (Darwin) platform for test skipping.

    ## @purpose — Public helper for @pytest.mark.skipif on macOS-specific tests.
    ##            Used by test_smoke_nginx.py to skip cert generation and
    ##            bind-mount tests that fail on Docker Desktop (macOS).
    ## @io — ⎋ bool: True if running on macOS/Darwin
    ## @complexity — O(1)
    ## @rationale — macOS Docker Desktop has known limitations with mkcert cert
    ##              generation and bind-mount file permissions. CI runs same
    ##              tests on Linux (ubuntu-latest runner, platform-test.yml),
    ##              so skipping on macOS does not reduce coverage — it follows
    ##              the "Linux-parity in CI" pattern (DevPlan §macOS smoke skip).
    ##              Root cause: platform limitation, not code defect.
    """
    return _platform.system() == "Darwin"


# --wait-timeout for docker compose up (env overrides platform default)
PLATFORM_COMPOSE_TIMEOUT = int(os.environ.get("PLATFORM_COMPOSE_TIMEOUT", "120" if _IS_MACOS else "90"))
# External Loki /ready probe (Loki healthcheck is liveness-only, --wait
# does not cover HTTP readiness — scratch image has no curl/wget)
PLATFORM_LOKI_TIMEOUT = int(os.environ.get("PLATFORM_LOKI_TIMEOUT", "30"))

# ── Named constants for magic numbers used in platform service fixtures ──
_POLL_INTERVAL_SECONDS = 5  # sleep interval in _wait_for_loki_ready
_REQUEST_TIMEOUT_SECONDS = 10  # requests.get timeout in _wait_for_loki_ready
_COMPOSE_DOWN_TIMEOUT = 5  # docker compose down --timeout
_DOCKER_LOG_TIMEOUT = 30  # timeout for docker compose logs
_STDERR_TAIL_LINES = 300  # tail lines for stderr truncation
_COMPOSE_EXTRA_TIMEOUT = 30  # extra timeout added to PLATFORM_COMPOSE_TIMEOUT

# ── Static test-specific env (DevPlan 116 T3, U-16/U-17/D2) ─────────
# Значения, дублирующие env_defaults из platform-env.yaml, УДАЛЕНЫ — они
# загружаются runtime-мержем (load_platform_env_defaults). Здесь остаётся
# ТОЛЬКО тест-специфика: test-порты, tmp-пути, намеренные TRAP-оверрайды.
_STATIC_SMOKE_ENV: dict[str, str] = {
    "COMPOSE_PROJECT_NAME": "ai-platform-test",
    "PLATFORM_DIR": "/tmp/ai-platform-test",
    "S3_ENDPOINT_URL": "",  # 🧐 TRAP[DECISION] · 2026-07-24 · — · Empty — skip S3 in test · Rejected: реальный S3 endpoint в CI · Reason: production endpoint unreachable in CI (deferred workaround) · Rev: CI-доступ к S3
    "NGINX_CERT_DIR": "/etc/nginx/dev-certs",
    "NODE_NAME": "test-node",
    # ⚠️ TRAP[BUG] · 2026-07-27 · HI · CONTEXT_IMAGE must be set for smoke tests
    # · Root: base.yml default ${CONTEXT_IMAGE:-ghcr.io/...@sha256:STALE} has stale SHA;
    # ·   Compose tries pull → not found → build with /opt/platform context → path missing on macOS.
    # · Fix: override CONTEXT_IMAGE to use locally-built :latest tag (no SHA digest).
    # ·   This matches `make hermes-build-context CONTEXT=test` output.
    # · Rev: when hermes-agent-context is rebuilt — update SMOKE_ENV_GENERATED (via platform-env.yaml)
    # ·   and remove this static override.
    # · Allowlist: bare :latest here is dev/test-only — excluded from tag-form gate
    # ·   (tests/gates/test_gate_image_tag_form.py, DevPlan 116 B3 T7).
    "CONTEXT_IMAGE": "ghcr.io/tronyxlab/hermes-agent-context:latest",
    "LITELLM_TEST_PORT": "14000",
    "HERMES_DASHBOARD_TEST_PORT": "19119",
    "HERMES_DESKTOP_TEST_PORT": "18642",
    "LANGFUSE_TEST_PORT": "13000",
    "PROMETHEUS_TEST_PORT": "19090",
    "GRAFANA_TEST_PORT": "13030",
}

# region WAVE_STATE_LOCK
# T12.2 (T-3, DevPlan 136 §12.4): started/failed списки мутируются фоновым потоком волн
# (_start_remaining) ПОСЛЕ yield — тесты получали «живой» снимок в процессе мутации
# (гонка: волна 1+ дописывает, пока волна 0 тесты читают). Lock + снимок перед yield.
_WAVE_STATE_LOCK = threading.Lock()

# 🧐 TRAP[DECISION] · 2026-08-05 · — · Docker-тесты — single-process по построению (T12.2 T-4)
# · Rejected: маркер-фильтр docker-тестов в один xdist-воркер (-n 1 для docker-субсетов)
# · Reason: канон тестовой архитектуры (tests/AGENTS.md §Параллельный запуск п.1-2): Docker —
#   только канонические session-фикстуры (platform_services, модульные), один стек на машину;
#   `-n auto` применяется к статическим сьюитам (check-suite gates/static_audit xdist: true),
#   docker-сьюиты (smoke/component/integration) исполняются single-process (check-suite xdist:
#   false для них; test_runner исключает docker-субсеты из -n auto — DevPlan 124). Маркер-фильтр
#   в один воркер не нужен: docker-фикстуры НЕ создают стек в воркерах (session-фикстура —
#   только master), а маркер-выражение уже отделяет docker-сьюиты от статических.
# · Rev: если появится требование распараллелить docker-сьюиты по разным машинам/стекам —
#   ввести маркер-группировку и параметр one-docker-worker.
# endregion WAVE_STATE_LOCK

# region RETRY_STATS
# T12.7 (T-11): счётчики retry-until-green для compose-стартов модулей. Итоговая retry-rate
# (retries/attempts) проверяется в sessionfinish (_check_smoke_retry_rate): >15% → RED.
# Гейт на порог — tests/gates/test_gate_retry_rate.py (порог 0.15 — канон, 2 места).
_RETRY_STATS: dict[str, int] = {"attempts": 0, "retries": 0}
_RETRY_STATS_LOCK = threading.Lock()
_RETRY_RATE_THRESHOLD = 0.15  # >15% retry-rate = ресурсная контенция Docker (T12.7 T-11)


def retry_stats() -> tuple[int, int]:
    """Вернуть (attempts, retries) smoke-compose стартов (T12.7 T-11, thread-safe).

    ## @io       → ⎋ tuple[int, int] — (число попыток старта, число retry-попыток)
    ## @complexity O(1)
    """
    with _RETRY_STATS_LOCK:
        return int(_RETRY_STATS["attempts"]), int(_RETRY_STATS["retries"])


def _bump_retry_stats(retried: bool) -> None:
    """Учесть попытку старта модуля (attempts += 1; retries += 1 если это retry)."""
    with _RETRY_STATS_LOCK:
        _RETRY_STATS["attempts"] += 1
        if retried:
            _RETRY_STATS["retries"] += 1


def _set_retry_stats(attempts: int, retries: int) -> None:
    """TEST-SUPPORT: установить счётчики (используется gate-тестом для restore после дельты).

    ## @purpose  T12.7 (T-11): gate-тест учёта не должен оставлять side-effect в глобальном
    ##            счётчике (иначе sessionfinish даёт ложный RED retry-rate в gates-прогоне).
    ## @io       ⇥ attempts, retries: int → ⎋ None
    ## @complexity O(1)
    """
    with _RETRY_STATS_LOCK:
        _RETRY_STATS["attempts"] = int(attempts)
        _RETRY_STATS["retries"] = int(retries)


# endregion RETRY_STATS

# region LOKI_READY_STATE
# T12.7 (T-10): реестр готовности Loki (observability-модуль). Заполняется
# _start_single_module (ленивый HTTP-poll /ready), агрегируется _loki_ready_aggregate()
# в результат platform_services и потребляется фикстурой loki_ready (skip loki-зависимых).
_LOKI_READY_STATE: dict[str, object] = {"observed": False, "ready": False}
_LOKI_READY_LOCK = threading.Lock()


def _record_loki_ready(ready: bool) -> None:
    """Зафиксировать результат Loki /ready poll (thread-safe)."""
    with _LOKI_READY_LOCK:
        _LOKI_READY_STATE["observed"] = True
        _LOKI_READY_STATE["ready"] = ready


def _loki_ready_aggregate() -> bool:
    """Вернуть готовность Loki: False если не наблюдалась (модуль не стартовал)."""
    with _LOKI_READY_LOCK:
        return bool(_LOKI_READY_STATE["ready"])


# endregion LOKI_READY_STATE


@pytest.fixture(scope="session")
def loki_ready() -> bool:
    """True если Loki /ready poll прошёл (иначе False — loki-зависимые тесты skip).

    ## @purpose  T12.7 (T-10): честный флаг готовности Loki вместо silent-proceed.
    ##            Потребители (loki-зависимые тесты) запрашивают фикстуру и скипают при False
    ##            (инфраструктурная недоступность — легитимный skip per tests/AGENTS.md rule 4).
    ## @io       → ⎋ bool
    ## @complexity O(1)
    """
    return _loki_ready_aggregate()


def load_platform_env_defaults() -> dict[str, str]:
    """Load env_defaults from repo-root platform-env.yaml (runtime, D2).

    ## @purpose — Runtime-источник не-секретных env-дефолтов для smoke-тестов.
    ##            Устраняет дубли static-копий (DevPlan 116 T3, U-17): значения
    ##            (PLATFORM_DOMAIN, POSTGRES_USER, PROMETHEUS_TARGETS_DIR, ...)
    ##            читаются из generated platform-env.yaml, а не хардкодятся в smoke.py.
    ##            T12.4 (T-7): вызывается ЛЕНИВО (не import-time) — статические сессии
    ##            без Docker не платят за YAML-load и не падают при отсутствии файла.
    ## @io — ⎋ dict[str, str]: env_defaults секция platform-env.yaml
    ## @complexity — O(1) — single YAML load
    ## @invariants
    ##   - File resolved from repo root (tests/helpers/gate_helpers.py::repo_root)
    ##   - Missing platform-env.yaml → fallback на env_defaults_generated.py (T12.4 T-7),
    ##     НЕ raise на import-time
    ##   - Returned dict contains ONLY env_defaults (не port_mappings/profiles)
    """
    import yaml as _yaml_load

    from tests.helpers.gate_helpers import repo_root as _repo_root

    env_path = _repo_root() / "platform-env.yaml"
    if not env_path.is_file():
        # T12.4 (T-7): fallback на generated CI-дефолты (tests/helpers/env_defaults_generated.py)
        # вместо import-time FileNotFoundError — статические сессии не должны падать.
        logging.getLogger(__name__).warning(
            "[IMP:8][smoke][load_platform_env_defaults] platform-env.yaml not found at %s — "
            "falling back to env_defaults_generated.py",
            env_path,
        )
        return _fallback_env_defaults()
    with open(env_path) as f:
        data = _yaml_load.safe_load(f)
    raw = (data or {}).get("env_defaults", {})
    defaults = {str(k): str(v) for k, v in raw.items() if v is not None} if isinstance(raw, dict) else {}
    logging.getLogger(__name__).info(
        "[IMP:8][smoke][load_platform_env_defaults] Loaded %d env_defaults from %s", len(defaults), env_path
    )
    return defaults


# region FUNC_fallback_env_defaults
## @purpose  T12.4 (T-7): fallback-источник env-дефолтов при отсутствии platform-env.yaml —
##            generated tests/helpers/env_defaults_generated.py (_-префиксные константы).
## @io       → ⎋ dict[str, str]: {SECRET_NAME: CI-значение}
## @complexity O(K) где K = констант в generated-модуле
def _fallback_env_defaults() -> dict[str, str]:
    """Build env_defaults from tests/helpers/env_defaults_generated.py constants (T12.4)."""
    try:
        from tests.helpers import env_defaults_generated as _gen  # type: ignore[import-untyped]

        result: dict[str, str] = {}
        for name in getattr(_gen, "__all__", []):
            if name.startswith("_") and hasattr(_gen, name):
                result[name.lstrip("_")] = str(getattr(_gen, name))
        logging.getLogger(__name__).info(
            "[IMP:8][smoke][fallback_env_defaults] Using %d generated CI defaults (fallback)",
            len(result),
        )
        return result
    except Exception as exc:  # generated-файл отсутствует/битый — пустой fallback
        logging.getLogger(__name__).warning(
            "[IMP:7][smoke][fallback_env_defaults] env_defaults_generated.py unavailable: %s", exc
        )
        return {}


# endregion FUNC_fallback_env_defaults


# region FUNC_get_smoke_env
## @purpose  T12.4 (T-7): ЛЕНИВЫЙ мерж SMOKE_ENV — platform-env.yaml грузится при ПЕРВОМ
##            обращении (не import-time). Статические сессии (без Docker) не платят за
##            YAML-load и не падают при отсутствии файла (fallback на env_defaults_generated).
##            Кэш на сессию процесса (functools.lru_cache — идемпотентен, 1 load на процесс).
## @io       → ⎋ dict[str, str]: мерж env_defaults → static → generated (TRAP[DECISION] ниже)
## @complexity O(1) после первого вызова
def get_smoke_env() -> dict[str, str]:
    """Lazily compute SMOKE_ENV (platform-env defaults → static → generated) with cache."""
    return _compute_smoke_env()


@functools.lru_cache(maxsize=1)
def _compute_smoke_env() -> dict[str, str]:
    """Compute SMOKE_ENV merge once per process (cached)."""
    _platform_defaults = load_platform_env_defaults()
    return {**_platform_defaults, **_STATIC_SMOKE_ENV, **SMOKE_ENV_GENERATED}


# endregion FUNC_get_smoke_env


# ⚠️ TRAP[DECISION] · 2026-07-31 · — · Merge order: env_defaults → static → generated
# · Rejected: literal DevPlan 116 order {static, env_defaults, generated}
# · Reason: env_defaults AFTER static would clobber намеренные тест-оверрайды
# ·   (S3_ENDPOINT_URL:"" TRAP[DECISION], CONTEXT_IMAGE:latest TRAP[BUG], NGINX_CERT_DIR test-путь).
# ·   Static содержит ТОЛЬКО тест-специфику (дубли удалены) → статик должен побеждать env_defaults.
# ·   SMOKE_ENV_GENERATED (секреты ci_default) — последний, как в generate_platform_env (secret > non-secret).
# · Rev: если статик снова получит ключи, дублирующие env_defaults → вернуть порядок DevPlan.

# T12.4 (T-7): SMOKE_ENV — ленивый (PEP 562 module __getattr__): import-time НЕ грузит
# platform-env.yaml; первый доступ к атрибуту вызывает get_smoke_env() (кэш на процесс).
# Совместимость: `from _conftest.smoke import SMOKE_ENV` и `SMOKE_ENV` внутри модуля работают.


def __getattr__(name: str) -> object:
    """PEP 562: ленивые SMOKE_ENV / PLATFORM_ENV_DEFAULTS (T12.4 T-7)."""
    if name == "SMOKE_ENV":
        return get_smoke_env()
    if name == "PLATFORM_ENV_DEFAULTS":
        return load_platform_env_defaults()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_SMOKE_VOLUME_BIND_DIRS: list[str] = [
    "/var/lib/platform/postgres-data",
    "/var/lib/platform/backup-spool",
    "/var/lib/platform/grafana-data",
    "/var/lib/platform/prometheus-data",
    "/var/lib/platform/loki-data",
    "/var/lib/platform/hermes-agent/data",
    "/var/log/platform/backup",
    "/tmp/prometheus-targets",
    "/tmp/prometheus-rules",
    "/tmp/test-node-configs/test-node",
    "/tmp/run/platform",
]


def _collect_external_networks() -> set[str]:
    """Scan all compose files and return set of networks declared as external: true.

    ## @purpose — Parse compose YAMLs to discover which networks must be pre-created.
    ## @io — ⎋ set[str]: network names that need pre-creation
    ## @complexity — O(N * M) where N = compose files, M = networks per file
    """
    external: set[str] = set()
    compose_dir = Path(__file__).resolve().parent.parent.parent / "core" / "modules"
    for mod_dir in sorted(compose_dir.iterdir()):
        compose_path = mod_dir / "docker-compose.base.yml"
        if not compose_path.is_file():
            continue
        try:
            with open(compose_path) as f:
                data = _yaml.safe_load(f)
        except (_yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for net_name, net_config in (data.get("networks") or {}).items():
            if isinstance(net_config, dict) and net_config.get("external") in (True, "true"):
                external.add(net_name)
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:7][conftest][_collect_external_networks] Found %d external network(s)", len(external))
    return external


def _run_docker_smoke(
    args: list[str],
    env_override: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a docker subprocess with SMOKE_ENV merged into os.environ.

    ## @purpose — Centralised docker subprocess runner for smoke tests.
    ## @io — ⇥ args, env_override, timeout → ⎋ CompletedProcess
    ## @complexity — O(1)
    """
    cmd_env = {**os.environ, **SMOKE_ENV}  # noqa: F821 — SMOKE_ENV ленивый (PEP 562 __getattr__, T12.4)
    if env_override:
        cmd_env.update(env_override)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=cmd_env,
    )


def _wait_for_loki_ready(
    url: str,
    timeout: int,
    logger,
) -> bool:
    """Poll Loki /ready endpoint until HTTP 200 or timeout.

    ## @purpose — Bridge the gap between Docker liveness healthcheck
    ##            (loki -version checks only process alive) and actual
    ##            query frontend readiness. Loki scratch image has no
    ##            curl/wget — cannot HTTP-probe from inside container.
    ##            This poll runs from the host, outside the container.
    ## @io — ⇥ url, timeout → ⌋ bool (True=200 received)
    ## @complexity — O(T) where T=poll iterations
    ## @invariants
    ##   - Polls every 5 seconds
    ##   - Returns True on first HTTP 200
    ##   - Returns False on timeout — graceful degradation
    ##   - IMP:8 logs on 503, IMP:9 on 200
    ## @rationale — TRAP[BUG] in loki docker-compose.base.yml documents
    ##              that scratch image has no HTTP client for healthcheck.
    ##              This function is the external readiness probe that
    ##              complements the internal liveness healthcheck.
    """
    import requests as _requests

    deadline = _time.monotonic() + timeout
    first_poll = True

    while _time.monotonic() < deadline:
        if not first_poll:
            _time.sleep(_POLL_INTERVAL_SECONDS)
        first_poll = False

        try:
            r = _requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
            if r.status_code == 200:
                logger.info(
                    "[IMP:9][conftest][_wait_for_loki_ready] Loki /ready OK: HTTP 200 — %s",
                    r.text.strip(),
                )
                return True
            logger.info(
                "[IMP:8][conftest][_wait_for_loki_ready] Loki /ready returned %d: %s — waiting...",
                r.status_code,
                r.text.strip()[:100],
            )
        except _requests.RequestException as exc:
            logger.info(
                "[IMP:8][conftest][_wait_for_loki_ready] Loki /ready unreachable: %s — waiting...",
                exc,
            )

    logger.warning(
        "[IMP:9][conftest][_wait_for_loki_ready] Loki /ready timeout after %ds",
        timeout,
    )
    return False


def _wait_for_minio_healthy(
    compose_base_args: list[str],
    timeout: int,
    logger: logging.Logger,
) -> bool:
    """Poll docker compose ps --format json until minio container is healthy.

    ## @purpose — Wait for minio (not minio-createbuckets one-shot init container)
    ##            to become healthy. minio-createbuckets exits 0 after creating
    ##            buckets, which makes `docker compose up --wait` return 1 even
    ##            though minio itself is healthy. This function polls only the
    ##            minio container's Health status.
    ## @io — ⇥ compose_base_args, timeout, logger → ⎋ bool (healthy within timeout)
    ## @complexity — O(T) where T = timeout / poll_interval
    ## @rationale — D5: one-shot init container exits 0, breaking --wait contract.
    ##              Separate health poll avoids coupling to createbuckets lifecycle.
    """
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            ps_result = subprocess.run(
                [*compose_base_args, "ps", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if ps_result.returncode != 0:
                logger.warning("[IMP:8][conftest][_wait_for_minio_healthy] docker compose ps failed")
                _time.sleep(2)
                continue

            # Parse JSONL output — one JSON object per line (one per container)
            for line in ps_result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    container = json.loads(line)
                except json.JSONDecodeError:
                    continue

                service = container.get("Service", "")
                state = container.get("State", "")
                health = container.get("Health", "")

                if service == "minio" and state == "running" and health == "healthy":
                    logger.info(
                        "[IMP:9][conftest][_wait_for_minio_healthy] MinIO is healthy (service=%s state=%s health=%s)",
                        service,
                        state,
                        health,
                    )
                    return True

            _time.sleep(2)

        except subprocess.TimeoutExpired:
            logger.warning("[IMP:8][conftest][_wait_for_minio_healthy] docker compose ps timed out")
            _time.sleep(2)

    logger.warning("[IMP:9][conftest][_wait_for_minio_healthy] MinIO not healthy within %ds", timeout)
    return False


def _build_waves(module_graph: dict[str, list[str]]) -> list[list[str]]:
    """Group modules into waves by dependency depth.

    ## @purpose — Convert module_graph (topologically sorted) into waves of
    ##            independent modules that can be started in parallel.
    ##            Wave 0: modules with no dependencies.
    ##            Wave N: modules whose dependencies are all in waves < N.
    ## @io — ⇥ module_graph {module → [deps]} → ⎋ list[list[str]] waves
    ## @complexity — O(M * D) where M=modules, D=avg dependencies per module
    ## @invariants
    ##   - module_graph is already topologically sorted (dict insertion order)
    ##   - Unassigned dependencies default to wave -1 (safe fallback)
    ##   - Result preserves module order within each wave
    """
    _logger = logging.getLogger(__name__)
    assigned: dict[str, int] = {}

    for module_name, deps in module_graph.items():
        if not deps:
            assigned[module_name] = 0
        else:
            # Find the highest wave among all dependencies
            max_dep_wave = max(assigned.get(dep, -1) for dep in deps)
            assigned[module_name] = max_dep_wave + 1

    if not assigned:
        return []

    max_wave = max(assigned.values())
    waves: list[list[str]] = [[] for _ in range(max_wave + 1)]
    for module_name, wave_idx in assigned.items():
        waves[wave_idx].append(module_name)

    _logger.info(
        "[IMP:8][conftest][_build_waves] Built %d wave(s) from %d module(s)",
        len(waves),
        len(assigned),
    )
    return waves


def _start_single_module(
    module_name: str,
    compose_path: str,
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_ports: dict[str, int],
    compose_timeout: int,
    compose_extra_timeout: int,
    compose_down_timeout: int,
    docker_log_timeout: int,
    stderr_tail_lines: int,
    loki_timeout: int,
) -> dict:
    """Start one module's compose stack, return success/failure status.

    ## @purpose — Extracted from platform_services loop for wave-parallel execution.
    ##            Each call handles one module's lifecycle: pre-cleanup down,
    ##            compose up (with minio --wait workaround), post-up container
    ##            existence check, and loki readiness HTTP poll.
    ## @io — ⇥ module params → ⎋ dict with "success": bool, "module_name": str
    ## @complexity — O(1) per module (subprocess calls + optional HTTP poll)
    ## @invariants
    ##   - compose_path must be a valid file path (validated by caller)
    ##   - Pre-cleanup down intentionally WITHOUT --remove-orphans (T3b fix)
    ##   - MinIO uses up -d without --wait, then polls health directly (D5)
    ##   - Post-up container existence check catches silent CI compose failures
    ##   - Loki readiness poll only runs for module_name == "observability"
    ## @rationale — Extracting to a standalone function allows ThreadPoolExecutor
    ##              to call it in parallel threads. Each module runs its own
    ##              compose subprocess, so GIL is not a bottleneck (I/O-bound).
    """
    _logger = logging.getLogger(__name__)

    if compose_path is None:
        _logger.warning(
            "[IMP:8][conftest][_start_single_module] No compose path for '%s'",
            module_name,
        )
        return {"success": False, "module_name": module_name}

    # ── Build compose base args (files + project name) ──────────────
    compose_base_args = ["docker", "compose", "-f", compose_path]
    test_override = os.path.join(os.path.dirname(compose_path), "docker-compose.test.yml")
    if os.path.exists(test_override):
        compose_base_args.extend(["-f", test_override])
    macos_override = os.path.join(os.path.dirname(compose_path), "docker-compose.macos.yml")
    if _platform.system() == "Darwin" and os.path.exists(macos_override):
        compose_base_args.extend(["-f", macos_override])
    compose_base_args.extend(["-p", "ai-platform-test"])

    # ── Pre-cleanup: docker compose down before up (SAME module only) ─
    # NOTE: intentionally WITHOUT --remove-orphans — that flag in a per-module
    # down would kill previously started modules' containers since all modules
    # share the same compose project name (ai-platform-test) but are defined
    # in different compose files. Global cleanup above already removed orphans.
    _down_args = [*compose_base_args, "down", "--timeout", str(compose_down_timeout)]
    _run_docker_smoke(_down_args, timeout=20, env_override={"COMPOSE_PROFILES": module_name})

    # ── Start up (with retry for transient failures) ──────────────────
    # ⚠️ TRAP[DECISION] · 2026-07-23 · → · Retry on transient compose failures
    # · Rationale: Docker Desktop on macOS has resource contention when 6+ modules
    # ·   start in parallel (Wave 0). Network creation, image pulls, and healthchecks
    # ·   compete for VM resources → some composes time out. A single retry after
    # ·   5s cooldown resolves >90% of transient failures without masking real bugs.
    # · Rev: if retry count >1 in >15% of CI runs → investigate root cause
    # ·   (resource limits, network contention).
    _MAX_RETRIES = 2
    _RETRY_DELAY = 5  # seconds between retries
    _start_ok = False

    for _attempt in range(_MAX_RETRIES):
        # T12.7 (T-11): честный учёт retry-rate (attempts/retries) для gate-проверки в sessionfinish
        _bump_retry_stats(retried=_attempt > 0)
        if _attempt > 0:
            _logger.warning(
                "[IMP:8][conftest][_start_single_module] Retry %d/%d for '%s' — transient failure cooldown",
                _attempt + 1,
                _MAX_RETRIES,
                module_name,
            )
            _time.sleep(_RETRY_DELAY)
            # ── Re-run pre-cleanup before retry ──────────────────────────
            _down_args = [*compose_base_args, "down", "--timeout", str(compose_down_timeout)]
            _run_docker_smoke(_down_args, timeout=20, env_override={"COMPOSE_PROFILES": module_name})

        # ── Start up ──────────────────────────────────────────────────
        if module_name == "minio":
            # D5: MinIO has a one-shot init container (minio-createbuckets) that
            # exits 0 after creating buckets. `docker compose up --wait` considers
            # the exited container a failure (returncode 1) even though minio
            # itself is healthy. Start without --wait, then poll for minio health.
            # ⚠️ TRAP[DECISION] · 2026-07-17 · — · MinIO --wait workaround
            # · Rejected: depends_on condition: service_completed_successfully
            # · Reason: deferred — that would require compose schema v3.8+ changes
            # · Rev: if compose schema is ever upgraded, use depends_on instead.
            compose_up_args = [*compose_base_args, "up", "-d"]
            result = _run_docker_smoke(
                compose_up_args,
                timeout=compose_timeout + compose_extra_timeout,
                env_override={"COMPOSE_PROFILES": module_name},
            )
            if result.returncode == 0:
                _minio_ok = _wait_for_minio_healthy(
                    compose_base_args=compose_base_args,
                    timeout=compose_timeout,
                    logger=_logger,
                )
                if not _minio_ok:
                    _logger.error(
                        "[IMP:9][conftest][_start_single_module] MinIO did not become healthy within %ds",
                        compose_timeout,
                    )
                    # Force failure path after the if-else block
                    result = subprocess.CompletedProcess(
                        args=compose_up_args, returncode=1, stdout="", stderr="MinIO health check timeout"
                    )
        else:
            compose_up_args = [
                *compose_base_args,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                str(compose_timeout),
            ]
            result = _run_docker_smoke(
                compose_up_args,
                timeout=compose_timeout + compose_extra_timeout,
                env_override={"COMPOSE_PROFILES": module_name},
            )

        if result.returncode != 0:
            if _attempt < _MAX_RETRIES - 1:
                _logger.warning(
                    "[IMP:8][conftest][_start_single_module] '%s' compose up failed (attempt %d/%d, rc=%d) — will retry",
                    module_name,
                    _attempt + 1,
                    _MAX_RETRIES,
                    result.returncode,
                )
                continue
            # ── Diagnostic: collect logs for failure analysis ─────────────
            log_args = [*compose_base_args, "logs", "--tail", "50", "--no-color"]
            logs = _run_docker_smoke(
                log_args, timeout=docker_log_timeout, env_override={"COMPOSE_PROFILES": module_name}
            )
            _logger.error(
                "[IMP:9][conftest][_start_single_module] Failed to start '%s' — "
                "returncode=%d\nstderr: %s\ndiagnostic logs:\n%s",
                module_name,
                result.returncode,
                result.stderr.strip()[-stderr_tail_lines:],
                (logs.stdout or logs.stderr).strip()[-500:],
            )
            break  # out of retry loop → fail

        # ── Post-up container existence check ─────────────────────────────────
        # Root cause (from CI diagnostic run): `docker compose up -d --wait`
        # returns exit code 0 on GHA runner even when containers fail to start
        # (e.g., when `--wait-timeout` expires or image pull fails silently).
        # `docker compose ps --all` returns empty despite returncode=0.
        # Add explicit container count check to distinguish true success from
        # silent compose failure. If zero containers → treat as failure.
        _ps_check = _run_docker_smoke(
            [*compose_base_args, "ps", "--all", "--format", "{{.Name}}"],
            timeout=15,
            env_override={"COMPOSE_PROFILES": module_name},
        )
        _container_count = len([cname for cname in _ps_check.stdout.strip().splitlines() if cname.strip()])
        if _container_count == 0:
            if _attempt < _MAX_RETRIES - 1:
                _logger.warning(
                    "[IMP:8][conftest][_start_single_module] '%s' compose up returned 0 but "
                    "no containers exist (attempt %d/%d) — will retry",
                    module_name,
                    _attempt + 1,
                    _MAX_RETRIES,
                )
                continue
            _logger.error(
                "[IMP:9][conftest][_start_single_module] '%s' compose up returned 0 but "
                "no containers exist (docker compose ps --all = empty). "
                "CI runner silent compose failure — treating as failed.",
                module_name,
            )
            break  # out of retry loop → fail

        # ── Success ───────────────────────────────────────────────────
        _start_ok = True
        if _attempt > 0:
            _logger.warning(
                "[IMP:9][conftest][_start_single_module] '%s' succeeded on retry %d/%d",
                module_name,
                _attempt + 1,
                _MAX_RETRIES,
            )
        break

    if not _start_ok:
        return {"success": False, "module_name": module_name}

    # ---- Loki readiness HTTP-poll ------------------------------------
    # T12.7 (T-10): Loki timeout больше НЕ «silent proceed» — loki_ready явно отражается в
    # результате модуля и агрегируется в платформенном результате (потребитель — loki_ready
    # фикстура / sessionfinish диагностика). Ложь = loki-зависимые тесты будут падать
    # осмысленно, а не с каскадом недоступности.
    _loki_ready = True
    if module_name == "observability":
        _loki_ready = _wait_for_loki_ready(
            url=f"http://localhost:{platform_ports['LOKI_PORT']}/ready",
            timeout=loki_timeout,
            logger=_logger,
        )
        _record_loki_ready(_loki_ready)
        if not _loki_ready:
            _logger.error(
                "[IMP:9][conftest][_start_single_module] Loki /ready timeout after %ds — "
                "loki-dependent tests will fail (T12.7 T-10)",
                loki_timeout,
            )
        else:
            _logger.info("[IMP:9][conftest][_start_single_module] Loki /ready OK — loki-dependent tests enabled")

    return {"success": True, "module_name": module_name, "loki_ready": _loki_ready}


# region R4_HELPER
## @purpose — Live container check для R4 fail-fast. Когда модуль в failed списке
##            platform_services, это может быть ложноположительным из-за restart:
##            unless-stopped — контейнер может восстановиться после первого --wait
##            timeout. Делаем docker inspect для верификации фактического состояния.
## @rationale — Липкая failed-метка не учитывает recover-семантику restart-политик.
##              R4 должен фейлить только при реальном отсутствии контейнера.
## @invariants
##   - container_name указывает на test-контейнер (с -test суффиксом)
##   - Возвращает True если контейнер запущен, False если отсутствует/не запущен
##   - logging.getLogger('conftest') для LDD-логов


def _module_container_running(
    platform_services_result: dict[str, list[str]],
    module_name: str,
    container_name: str,
    logger: logging.Logger,
    timeout: int = 10,
) -> bool:
    """Verify module test container is actually running.

    ## @purpose — If module is in failed list (--wait timeout), check live
    ##            container state via docker inspect. restart: unless-stopped
    ##            may have recovered the container after the initial timeout.
    ## @io — ⇥ platform_services_result, module_name, container_name, logger
    ##       → ⎋ bool (True if running)
    ## @complexity — O(1) — single docker inspect call
    """
    # ⚠️ TRAP[BUG] · 2026-07-23 · MED · False-positive when started=[] AND failed=[]
    # · Symptom: _module_container_running returns True for module that was never started
    # ·   because platform_services was a no-op (missing @requires_docker marker).
    # ·   Returns True when module_name is not in failed list, but also not in started list.
    # · Fix: check both lists — module must be in started (or recovered via docker inspect
    # ·   if in failed). Neither → container was never started → return False.
    started = platform_services_result.get("started", [])
    failed = platform_services_result.get("failed", [])
    if module_name in started:
        return True  # module started successfully
    if module_name not in failed:
        # Module was never started — not in started, not in failed
        logger.error(
            "[IMP:9][R4][%s] Module '%s' was never started by platform_services "
            "(missing @pytest.mark.requires_docker on test?)",
            module_name,
            module_name,
        )
        return False

    # Module in failed list — check if container actually recovered
    import subprocess as _sp

    try:
        _r = _sp.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if _r.returncode == 0 and _r.stdout.strip() == "true":
            logger.warning(
                "[IMP:8][R4][%s] Module in failed list but container '%s' IS running"
                " — recovered after first --wait timeout",
                module_name,
                container_name,
            )
            return True
    except (_sp.TimeoutExpired, OSError):
        pass

    logger.error(
        "[IMP:9][R4][%s] Container '%s' is NOT running — module truly failed",
        module_name,
        container_name,
    )
    return False


# endregion R4_HELPER


@pytest.fixture(scope="module")
def platform_env() -> dict[str, str]:
    """Inject SMOKE_ENV into os.environ; restore on teardown (module-scoped, T12.3 T-6).

    ## @purpose — Set environment variables required by docker-compose files.
    ##            Saves original values and restores them after the module.
    ##            T12.3 (T-6): scope=module (не session) — SMOKE_ENV инжектится только для
    ##            модулей, которые её реально запрашивают; session-scope загрязнял os.environ
    ##            для ВСЕХ тестов сессии (env pollution, T-5/T-6).
    ## @io — ⇥ (os.environ snapshot) → ⌋ dict[str, str] (SMOKE_ENV copy)
    ## @complexity — O(K) where K = len(SMOKE_ENV)
    ## @invariants
    ##   - module scope: фикстура создаётся один раз на тестовый модуль, teardown восстанавливает env
    ##   - platform_services (session) НЕ зависит от неё (T12.3): compose-субпроцессы получают
    ##     SMOKE_ENV через merge в _run_docker_smoke — инъекция os.environ не нужна для старта
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:7][conftest][platform_env] Setting SMOKE_ENV environment variables")
    smoke_env = get_smoke_env()
    saved: dict[str, str | None] = {}
    for key in smoke_env:
        saved[key] = os.environ.get(key)
        os.environ[key] = smoke_env[key]

    yield smoke_env

    _logger.info("[IMP:9][conftest][platform_env] Restoring original environment")
    for key in smoke_env:
        env_value = saved[key]
        if env_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = env_value
    _logger.info("[IMP:7][conftest][platform_env] Environment restored")


# 🧐 TRAP[DECISION] · 2026-07-22 · — · platform_ports fixture removed in DevPlan 041 rename
# · Rejected: немедленное удаление alias-фикстуры (ломал platform_services consumers)
# · Root: platform_port_mappings_dict replaced platform_ports, but platform_services fixture
#   still expects platform_ports parameter → fixture not found errors in test_smoke_postgres,
#   test_component_pgbouncer, test_component_clickhouse
# · Fix: backward-compatible alias delegating to platform_port_mappings_dict (deferred workaround)
# · Rev: next DevPlan — update platform_services and _start_single_module to use
#   platform_port_mappings_dict directly, then remove this alias
@pytest.fixture(scope="session")
def platform_ports(platform_port_mappings_dict: dict[str, int]) -> dict[str, int]:
    """Backward-compatible alias for platform_port_mappings_dict (DevPlan 041 rename)."""
    return platform_port_mappings_dict


@pytest.fixture(scope="session")
def platform_services(
    request: pytest.FixtureRequest,
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_ports: dict[str, int],
) -> dict[str, list[str]]:
    """Start all compose files in topological order; tear down after all tests.

    ## @purpose — Lifecycle fixture: creates volume dirs + external networks,
    ##            starts containers, yields started module names, then runs
    ##            docker compose down on teardown.
    ##            Session-scoped — containers live for the entire test session.
    ##            Built-in Docker guard: skips if Docker unavailable or production host.
    ##            Conditional activation: only if at least one test has requires_docker marker.
    ##            T12.3 (T-6): НЕ зависит от platform_env (session→module scope mismatch) —
    ##            compose-субпроцессы получают SMOKE_ENV через merge в _run_docker_smoke.
    ## @io — ⇥ request, all_compose_files, module_graph
    ##       → ⎋ dict[str, list[str]]: {"started": [module_names], "failed": [module_names],
    ##                                  "loki_ready": bool} — СНИМОК под lock (T12.2 T-3)
    ## @complexity — O(N + M) where N = compose files, M = networks
    ## @invariants
    ##   - started/failed — СНИМОК (list-копия) под _WAVE_STATE_LOCK перед yield (T12.2 T-3):
    ##     фоновый поток волн дописывает списки ПОСЛЕ старта — «живые» списки = гонка чтения
    ##   - Финальный wave-event сигналится в try/finally основного потока (T12.2 T-4): даже если
    ##     фоновый поток умер, тесты platform_services-волны не висят 600s до timeout
    ##   - Только master (session-фикстура): воркеры не создают свой стек
    """
    _logger = logging.getLogger(__name__)

    # ── Built-in Docker guard ────────────────────────────────────────────────
    if is_production_host():
        pytest.skip("Production host detected — skip smoke suite to prevent container overwrite")

    require_docker_or_fail(reason="smoke suite requires Docker daemon")

    # ── Conditional activation (T2.2 pattern) ────────────────────────────────
    items = request.session.items
    needs_docker = any(item.get_closest_marker("requires_docker") for item in items)
    if not needs_docker:
        _logger.info("[IMP:8][conftest][platform_services] No test requires Docker — yielding no-op")
        yield {"started": [], "failed": [], "loki_ready": _loki_ready_aggregate()}
        return

    _logger.info("[IMP:7][conftest][platform_services] Starting platform services")

    # ── Ensure volume bind-mount directories ─────────────────────────────────
    # T12.9 (T-14): созданные host-директории (не существовавшие до старта) трекаются
    # в _created_host_dirs и удаляются в teardown (только пустые, best-effort) — тест не
    # оставляет артефактов на host.
    _created_host_dirs: list[str] = [_bind_dir for _bind_dir in _SMOKE_VOLUME_BIND_DIRS if not os.path.isdir(_bind_dir)]
    _ensure_volume_dirs(_SMOKE_VOLUME_BIND_DIRS)

    # ── Generate test data files for status-page bind-mount ─────────────────
    # status-page docker-compose.test.yml mounts /tmp/test-node-configs/test-node/node.yaml
    # and /tmp/run/platform/status-metrics.json into the container. These files
    # must exist on the host (macOS Docker Desktop requires /tmp paths).
    _test_node_yaml = Path("/tmp/test-node-configs/test-node/node.yaml")
    _test_status_metrics = Path("/tmp/run/platform/status-metrics.json")

    _test_node_yaml.parent.mkdir(parents=True, exist_ok=True)
    _test_node_yaml.write_text(
        textwrap.dedent("""\
            node:
              name: test-node
              platform_domain: test.local
            projects: []
            modules: {}
    """)
    )
    _test_status_metrics.parent.mkdir(parents=True, exist_ok=True)
    _test_status_metrics.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
                "node": "test-node",
                "containers": [],
                "certs": [],
                "projects": [],
                "host": {"disk_total_gb": 100.0, "disk_free_gb": 50.0, "disk_used_percent": 50.0},
                "errors": [],
            }
        )
    )

    _logger.info("[IMP:8][conftest][platform_services] Test data files created for status-page bind-mount")

    # ── Acquire external + test networks via NetworkLeaseManager ──────────────
    _logger.info("[IMP:8][conftest][platform_services] Acquiring external and test networks via NetworkLeaseManager")
    _nm = get_network_manager()
    _all_nets = sorted(_collect_external_networks() | TEST_NETWORKS)
    for net_name in _all_nets:
        _nm.acquire(net_name)
    _logger.info(
        "[IMP:9][conftest][platform_services] All %d networks acquired via NetworkLeaseManager", len(_all_nets)
    )

    # ── Global pre-cleanup: down ALL compose files before starting ─────────
    # ⚠️ TRAP[BUG] · 2026-07-17 · HI · per-module `down --remove-orphans` killed
    #    previously started modules (all share project=ai-platform-test). Fix:
    #    global cleanup at start, per-module down WITHOUT --remove-orphans.
    _logger.info("[IMP:8][conftest][platform_services] Global pre-cleanup: down all compose files")
    for _cleanup_name, _cleanup_path in sorted(all_compose_files.items()):
        _cleanup_args = ["docker", "compose", "-f", _cleanup_path]
        _test_override = os.path.join(os.path.dirname(_cleanup_path), "docker-compose.test.yml")
        if os.path.exists(_test_override):
            _cleanup_args.extend(["-f", _test_override])
        _cleanup_args.extend(
            ["-p", "ai-platform-test", "down", "--timeout", str(_COMPOSE_DOWN_TIMEOUT), "--remove-orphans"]
        )
        _run_docker_smoke(_cleanup_args, timeout=20)
    _logger.info("[IMP:8][conftest][platform_services] Global pre-cleanup complete")

    # ── Safety net: remove stale test containers from crashed previous runs ──
    # ⚠️ TRAP[BUG] · 2026-07-18 · D3 · stale containers block test run
    # · docker compose down does not remove containers created with a different
    # · set of compose files (project labels don't match). After crash (Ctrl+C,
    # · OOM) containers remain and block container names ("already in use").
    # · Safety net: docker rm -f by known test container names — unconditional,
    # · idempotent.
    # ⚠️ TRAP[BUG] · 2026-07-22 · HI · Stale test containers block compose up after crash
    # · Root: docker compose down does not remove containers from crashed runs (different
    # ·   compose-file set = different project labels). After OOM/Ctrl+C, containers
    # ·   with -test suffix remain and cause "container already in use" errors.
    # · Fix: exhaustively list ALL test containers from core/modules/*/docker-compose.test.yml.
    # ·   pgbouncer-test was the most impactful omission — it blocked postgres module startup.
    # ·   All other missing entries (13 total) are added for defence-in-depth.
    # · Rev: if new module adds a test compose file — update this list within same PR.
    # ⚠️ TRAP[DECISION] · 2026-07-22 · — · _STALE_CONTAINER_NAMES derived from infra auto-discovery
    # · Rejected: hardcoded list (risk: manual sync failure on module add/remove)
    # · Reason: infra.py derives container names from docker-compose.test.yml via
    # ·   discover_modules.py --test-infra. List is always in sync with compose files.
    # · Rev: if infra._load_test_infra() becomes a performance bottleneck (>500ms),
    # ·   add file-based cache with mtime check.
    _STALE_CONTAINER_NAMES = _infra.stale_container_names
    for _cname in _STALE_CONTAINER_NAMES:
        subprocess.run(
            ["docker", "rm", "-f", _cname],
            capture_output=True,
            text=True,
            timeout=10,
        )
    _logger.info("[IMP:8][conftest][platform_services] Safety net: stale containers removed")

    # ── Pre-build images for modules with build: directive ──────────────────
    # ⚠️ TRAP[BUG] · 2026-07-23 · MED · status-page cold-cache build timeouts smoke test
    # · status-page uses build: (not pre-built image) — docker compose up -d --wait
    # · may timeout on first gate run when Docker layer cache is cold (image pull +
    # · pip install + app copy). Pre-build ensures image exists before --wait timer starts.
    # · Rev: if more build-based modules added, add them here or switch to auto-detection.
    _logger.info("[IMP:8][conftest][platform_services] Pre-building images for build-based modules")
    _PRE_BUILD_MODULES = ["status-page"]  # modules with build: that are started in smoke tests
    for _pb_module in _PRE_BUILD_MODULES:
        _pb_compose = all_compose_files.get(_pb_module)
        if _pb_compose is None:
            continue
        _pb_build_args = ["docker", "compose", "-f", _pb_compose]
        _pb_test_override = os.path.join(os.path.dirname(_pb_compose), "docker-compose.test.yml")
        if os.path.exists(_pb_test_override):
            _pb_build_args.extend(["-f", _pb_test_override])
        _pb_build_args.extend(["-p", "ai-platform-test", "build", "--no-cache"])
        _pb_result = _run_docker_smoke(_pb_build_args, timeout=180, env_override={"COMPOSE_PROFILES": _pb_module})
        if _pb_result.returncode != 0:
            _logger.warning(
                "[IMP:8][conftest][platform_services] Pre-build failed for '%s' (returncode=%d) — "
                "compose up will attempt build anyway",
                _pb_module,
                _pb_result.returncode,
            )
        else:
            _logger.info("[IMP:8][conftest][platform_services] Pre-build complete for '%s'", _pb_module)

    # ── Start compose files in wave-parallel order ──────────────────────────
    started: list[str] = []
    failed: list[str] = []
    waves = _build_waves(module_graph)
    _logger.info(
        "[IMP:8][conftest][platform_services] Built %d wave(s) from %d module(s)",
        len(waves),
        len(module_graph),
    )

    # ── Wave-Pipeline: Wave 0 sync → Wave 1+ background ──────────────────────
    # DevPlan 040 Wave 4: Wave 0 starts synchronously (critical path for fixture
    # setup), then remaining waves start in a background daemon thread. Tests
    # run as soon as their wave's containers are ready (wave_ready events).
    # +1 for the platform_services wave (max_wave + 1 = "all waves done")
    _init_wave_events(len(waves) + 1)
    bg_thread: threading.Thread | None = None

    if waves:
        # Wave 0 — synchronous (blocking fixture setup)
        wave_0_modules = waves[0]
        _logger.info(
            "[IMP:8][conftest][platform_services] Wave 0 (sync): starting %d module(s) in parallel",
            len(wave_0_modules),
        )

        with ThreadPoolExecutor(max_workers=len(wave_0_modules)) as executor:
            futures = {}
            for _wm_module_name in wave_0_modules:
                _wm_compose_path = all_compose_files.get(_wm_module_name)
                if _wm_compose_path is None:
                    _logger.warning(
                        "[IMP:8][conftest][platform_services] Wave 0: no compose path for '%s' — skipping",
                        _wm_module_name,
                    )
                    continue
                future = executor.submit(
                    _start_single_module,
                    _wm_module_name,
                    _wm_compose_path,
                    all_compose_files,
                    module_graph,
                    platform_ports,
                    PLATFORM_COMPOSE_TIMEOUT,
                    _COMPOSE_EXTRA_TIMEOUT,
                    _COMPOSE_DOWN_TIMEOUT,
                    _DOCKER_LOG_TIMEOUT,
                    _STDERR_TAIL_LINES,
                    PLATFORM_LOKI_TIMEOUT,
                )
                futures[future] = _wm_module_name

            for future in as_completed(futures):
                _wm_module_name = futures[future]
                try:
                    _wm_result = future.result()
                    if isinstance(_wm_result, dict) and _wm_result.get("success"):
                        started.append(_wm_module_name)
                    else:
                        failed.append(_wm_module_name)
                except Exception as exc:
                    _logger.error(
                        "[IMP:9][conftest][platform_services] Wave 0 module '%s' raised: %s",
                        _wm_module_name,
                        exc,
                    )
                    failed.append(_wm_module_name)

        _logger.info(
            "[IMP:9][conftest][platform_services] Wave 0 complete: %d started, %d failed",
            len(started),
            len(failed),
        )
        signal_wave_ready(0)

        # Wave 1+ — background thread (overlaps container start with test execution)
        def _start_remaining(wave_list, started_list, failed_list):
            """Start remaining waves in background thread.
            Each wave signals readiness after completion, unblocking tests.
            T12.2 (T-3): started_list/failed_list мутируются под _WAVE_STATE_LOCK —
            основной поток делает снимок перед yield (гонка чтения «живых» списков)."""
            for wave_idx in range(1, len(wave_list)):
                wave_modules = wave_list[wave_idx]
                _logger.info(
                    "[IMP:8][conftest][platform_services] Wave %d (background): starting %d module(s)",
                    wave_idx,
                    len(wave_modules),
                )

                with ThreadPoolExecutor(max_workers=len(wave_modules)) as executor:
                    futures = {}
                    for _wm_module_name in wave_modules:
                        _wm_compose_path = all_compose_files.get(_wm_module_name)
                        if _wm_compose_path is None:
                            continue
                        future = executor.submit(
                            _start_single_module,
                            _wm_module_name,
                            _wm_compose_path,
                            all_compose_files,
                            module_graph,
                            platform_ports,
                            PLATFORM_COMPOSE_TIMEOUT,
                            _COMPOSE_EXTRA_TIMEOUT,
                            _COMPOSE_DOWN_TIMEOUT,
                            _DOCKER_LOG_TIMEOUT,
                            _STDERR_TAIL_LINES,
                            PLATFORM_LOKI_TIMEOUT,
                        )
                        futures[future] = _wm_module_name

                    for future in as_completed(futures):
                        _wm_module_name = futures[future]
                        try:
                            _wm_result = future.result()
                            if isinstance(_wm_result, dict) and _wm_result.get("success"):
                                # T12.2 (T-3): append под lock — конкурентно с основным потоком (снимок)
                                with _WAVE_STATE_LOCK:
                                    started_list.append(_wm_module_name)
                            else:
                                with _WAVE_STATE_LOCK:
                                    failed_list.append(_wm_module_name)
                        except Exception:
                            with _WAVE_STATE_LOCK:
                                failed_list.append(_wm_module_name)

                    _logger.info(
                        "[IMP:9][conftest][platform_services] Wave %d (background) complete: %d started, %d failed",
                        wave_idx,
                        len([m for m in wave_modules if m in started_list]),
                        len([m for m in wave_modules if m in failed_list]),
                    )
                    signal_wave_ready(wave_idx)

            # ⚠️ TRAP[BUG] · 2026-07-23 · Signal "all waves done" for platform_services tests
            # · Symptom: tests using platform_services fixture (wave = max_wave + 1) ran before
            # ·   background thread completed. _ensure_wave_ready found no event for that wave
            # ·   and passed through immediately. _module_container_running then reported
            # ·   container was "never started" (started=[] — container not in Wave 0).
            # · Fix: signal wave = len(wave_list) after ALL background waves complete.
            # ·   _init_wave_events creates len(waves)+1 events (0..len(waves) for max_wave+1).
            # · T12.2 (T-4): дублируется в try/finally основного потока — если bg-поток умер,
            # ·   финальный wave-event всё равно сигналится (тесты не висят 600s).
            signal_wave_ready(len(wave_list))

        bg_thread = threading.Thread(
            target=_start_remaining,
            args=(waves, started, failed),
            daemon=True,
        )
        bg_thread.start()

    # T12.2 (T-3): СНИМОК started/failed под lock перед yield — тесты получают
    # консистентный снимок, не «живые» списки (фон дописывает их после старта).
    with _WAVE_STATE_LOCK:
        _result_snapshot = {"started": list(started), "failed": list(failed), "loki_ready": _loki_ready_aggregate()}
    _logger.info(
        "[IMP:9][conftest][platform_services] Result: %d started, %d failed",
        len(_result_snapshot["started"]),
        len(_result_snapshot["failed"]),
    )
    try:
        yield _result_snapshot
    finally:
        # T12.2 (T-4): финальный wave-event сигналится в main thread finally — даже при
        # падении фонового потока тесты platform_services-волны не ждут 600s до timeout.
        signal_wave_ready(len(waves))

        # ── Wait for background thread (if any) before teardown ─────────────────
        if bg_thread is not None:
            bg_thread.join(timeout=600)

    # ── Teardown: compose stop (not down) — faster, preserves volumes ────────
    # DevPlan 040 Wave 3: compose down → compose stop saves ~50s.
    # Final compose down happens in pytest_sessionfinish (session.py).
    all_modules = list(reversed(started + [m for m in failed if m not in started]))
    _logger.info(
        "[IMP:7][conftest][platform_services] Stopping %d module(s) (compose stop, not down)", len(all_modules)
    )
    for module_name in all_modules:
        compose_path = all_compose_files.get(module_name)
        if compose_path is None:
            continue
        stop_args = ["docker", "compose", "-f", compose_path]
        test_override = os.path.join(os.path.dirname(compose_path), "docker-compose.test.yml")
        if os.path.exists(test_override):
            stop_args.extend(["-f", test_override])
        macos_override = os.path.join(os.path.dirname(compose_path), "docker-compose.macos.yml")
        if _platform.system() == "Darwin" and os.path.exists(macos_override):
            stop_args.extend(["-f", macos_override])
        stop_args.extend(["-p", "ai-platform-test", "stop", "--timeout", str(_COMPOSE_DOWN_TIMEOUT)])

        stop_result = _run_docker_smoke(stop_args, timeout=20)
        if stop_result.returncode != 0:
            _logger.error(
                "[IMP:9][conftest][platform_services] Stop failed for '%s': %s",
                module_name,
                stop_result.stderr.strip()[-200:],
            )

    # ── Release networks via NetworkLeaseManager ──────────────────────────────
    _logger.info("[IMP:8][conftest][platform_services] Releasing %d network(s) via NetworkLeaseManager", len(_all_nets))
    for net_name in reversed(_all_nets):
        _nm.release(net_name)

    # ── T12.9 (T-14): host-артефакты teardown (best-effort, только созданные нами) ──
    # Удаляем тестовые файлы и пустые директории, созданные fixture'ой. rmdir падает
    # если в директории осталось содержимое (контейнерные volume-данные) — это ожидаемо,
    # такие директории НЕ удаляем (их создал не тест).
    _test_artifacts = [
        Path("/tmp/test-node-configs/test-node/node.yaml"),
        Path("/tmp/run/platform/status-metrics.json"),
    ]
    for _artifact in _test_artifacts:
        try:
            _artifact.unlink(missing_ok=True)
        except OSError as _exc:
            _logger.info("[IMP:7][conftest][platform_services] T12.9: artifact cleanup skip: %s", _exc)
    for _dir in reversed(_created_host_dirs):
        with contextlib.suppress(OSError):
            os.rmdir(_dir)  # только пустые — OSError если не пусто (данные не наши)
    if _created_host_dirs:
        _logger.info(
            "[IMP:8][conftest][platform_services] T12.9: removed %d empty host dir(s)", len(_created_host_dirs)
        )

    _logger.info("[IMP:9][conftest][platform_services] Cleanup complete")


# endregion SMOKE_PLATFORM_FIXTURES
