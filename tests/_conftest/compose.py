# GREP_SUMMARY: compose, platform_services, docker, compose-up, wave-pipeline, retry-stats, smoke, _start_single_module, teardown, lifecycle
# STRUCTURE: ┌_compose_file_args(root SoT + test)┐ → ┌_run_docker_smoke(SMOKE_ENV merge)┐ → _start_single_module(env → args → down → up-retry → ps-check → health-poll) → platform_services(guard → host-artifacts → pre-cleanup → waves → yield → teardown)

# region MODULE_CONTRACT
## @purpose  Compose lifecycle domain for smoke tests: compose file arg chains (root SoT U-49),
##           centralised docker subprocess runner, retry-until-green accounting (T12.7 T-11),
##           per-module start (_start_single_module → 5 блоков) and the session-scoped
##           platform_services fixture (→ 6 блоков). Extracted from smoke.py (DevPlan 170 W8).
## @scope    Consumed by _conftest/__init__ re-export (platform_services, platform_env) and
##           tests/conftest.py; retry-stats consumed by _conftest/session.py + gate tests.
## @invariants
##   - _compose_file_args: root docker-compose.yml ПЕРВЫМ в цепочке -f (volumes/networks SoT,
##     U-49, 142 W8 R13); модульный base НЕ передаётся явно — root include'ит его
##   - _run_docker_smoke: НЕ использует `**SMOKE_ENV` (module-global LOAD_GLOBAL не триггерит
##     PEP 562 __getattr__ — TRAP[BUG] 2026-08-06) → явный get_smoke_env()
##   - platform_services session-scoped — контейнеры живут всю сессию; Docker guard built-in
##     (skip при production host / недоступном daemon); условная активация requires_docker
##   - started/failed — СНИМОК под _WAVE_STATE_LOCK перед yield (T12.2 T-3); финальный wave-event
##     сигналится в finally основного потока (T12.2 T-4)
##   - Retry-rate компоуз-стартов трекается (_RETRY_STATS, T12.7 T-11) — проверяется в sessionfinish
##   - Teardown: compose stop (не down — быстрее, сохраняет volumes, DevPlan 040 Wave 3); финальный
##     down — pytest_sessionfinish (session.py); T12.9 host-артефакты cleanup (только созданные нами)
## @rationale  Extracted from smoke.py to isolate compose lifecycle from env/health/container domains (W8).
## @changes    CREATED: 2026-08-15 | DevPlan 170 W8: вынесен из tests/_conftest/smoke.py
##             (T12.2/T12.7/T12.9 логика сохранена 1:1; historical MODULE_CONTRACT — в smoke.py фасаде)
##             platform_ports alias-фикстура УДАЛЕНА (Rev TRAP[DECISION] 2026-07-22 выполнен):
##             platform_services/_start_single_module используют platform_port_mappings_dict напрямую
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import os
import platform as _platform
import subprocess
import sys
import textwrap
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import yaml as _yaml

from _conftest.env import (
    PLATFORM_COMPOSE_TIMEOUT,
    PLATFORM_LOKI_TIMEOUT,
    get_smoke_env,
)
from _conftest.health import (
    _loki_ready_aggregate,
    _record_loki_ready,
    _wait_for_loki_ready,
    _wait_for_minio_healthy,
)
from _conftest.honesty import require_docker_or_fail
from _conftest.infra import infra as _infra
from _conftest.ldd import _ensure_volume_dirs
from _conftest.networks import TEST_NETWORKS, get_network_manager, is_production_host
from _conftest.shared import build_waves
from _conftest.wave_pipeline import _init_wave_events, signal_wave_ready

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Root compose SoT (142 W8, R13)
# ═══════════════════════════════════════════════════════════════════════════
# Модульные docker-compose.base.yml ссылаются на volumes, объявленные ТОЛЬКО в root
# docker-compose.yml (U-49: root — единственный SoT volumes). Изолированная инвокация
# модульного compose → «undefined volume X» (pre-existing R13: ci-docker давно красная).
# Канон: root compose ПЕРВЫМ в цепочке -f (include'ит base.yml, объявляет volumes/networks),
# COMPOSE_PROFILES=«module» изолирует один модуль (DD3-канон CI).

_ROOT_COMPOSE: Path = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"


def _compose_file_args(compose_path: str | Path, test_override: str | Path | None = None) -> list[str]:
    """Построить цепочку -f для compose-инвокаций: [root, (test)?].

    ## @purpose — 142 W8 (R13): все smoke-инвокации обязаны включать root compose
    ##            (volumes/networks SoT, U-49). Модульный base НЕ передаётся явно —
    ##            root include'ит его (FL1-контракт: include сохраняет базу резолюции
    ##            относительных путей каждого файла; явный -f base первым ломал
    ##            include-пути root, а root первым ломал модульные ./config/ пути).
    ## @io — ⇥ compose_path: модульный base.yml (для директории/имени), test_override: test.yml
    ##       → ⎋ list[str] — аргументы -f для docker compose
    ## @complexity — O(1)
    ## @invariants — root compose обязателен (U-49); COMPOSE_PROFILES=«module» изолирует модуль
    ##               (DD3-канон CI); верифицировано: loki-test Healthy на root+test цепочке (142 W8)
    """
    args = ["-f", str(_ROOT_COMPOSE)]
    if test_override is not None and Path(str(test_override)).exists():
        args.extend(["-f", str(test_override)])
    return args


# ── Named constants for magic numbers used in compose lifecycle ─────────
_COMPOSE_DOWN_TIMEOUT = 5  # docker compose down --timeout
_DOCKER_LOG_TIMEOUT = 30  # timeout for docker compose logs
_STDERR_TAIL_LINES = 300  # tail lines for stderr truncation
_COMPOSE_EXTRA_TIMEOUT = 30  # extra timeout added to PLATFORM_COMPOSE_TIMEOUT

# ── Host bind-mount directories pre-created by platform_services (T12.9 T-14) ──
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
    # ⚠️ TRAP[BUG] · 2026-08-06 · HI · R13 (142 W8): `**SMOKE_ENV` — NameError
    # · Symptom: ci-docker smoke-шаг падал «NameError: name 'SMOKE_ENV' is not defined»
    # ·   в _run_docker_smoke (после 139-волны: lazy SMOKE_ENV через PEP 562 __getattr__).
    # · Root: LOAD_GLOBAL внутри модуля НЕ триггерит module __getattr__ (PEP 562 работает
    # ·   только для attribute access извне) — глобал SMOKE_ENV не существует.
    # · Fix: явный вызов get_smoke_env() (тот же кэш, тот же мерж — T12.4).
    cmd_env = {**os.environ, **get_smoke_env()}
    if env_override:
        cmd_env.update(env_override)
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=cmd_env, check=False)


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
            with Path(compose_path).open(encoding="utf-8") as f:
                data = _yaml.safe_load(f)
        except (_yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for net_name, net_config in (data.get("networks") or {}).items():
            if isinstance(net_config, dict) and net_config.get("external") in {True, "true"}:
                external.add(net_name)
    logger.info("[IMP:7][compose][_collect_external_networks] Found %d external network(s)", len(external))
    return external


# region START_SINGLE_MODULE
## @purpose — Per-module compose lifecycle: env-подготовка → compose-аргументы → pre-cleanup down
##            → start (retry-until-green + minio workaround) → health-поллинг (minio/loki).
##            Извлечён из platform_services для wave-parallel исполнения (ThreadPoolExecutor).
##            Каждый модуль — отдельный compose subprocess: GIL не bottleneck (I/O-bound).


# region FUNC_module_start_env
## @purpose  env-подготовка модуля: COMPOSE_PROFILES=«module» (DD3-изоляция одного модуля в
##            контексте root) + TEST_MODULE_DIR — абсолютный путь модуля для относительных
##            bind-маунтов тестовых оверрайдов (${TEST_MODULE_DIR:-./...} в test.yml; relative-пути
##            в явных -f файлах резолвятся от project-directory root compose, не от модуля — 142 W8 R13).
## @io       ⇥ module_name: str, compose_path: str → ⎋ dict[str, str | Path] — env-оверрайды для subprocess
## @complexity O(1)
def _module_start_env(module_name: str, compose_path: str) -> dict[str, str | Path]:
    """Build per-module env overrides (COMPOSE_PROFILES + TEST_MODULE_DIR)."""
    return {"COMPOSE_PROFILES": module_name, "TEST_MODULE_DIR": Path(compose_path).parent}


# endregion FUNC_module_start_env


# region FUNC_module_compose_args
## @purpose  compose-аргументы модуля: [docker, compose, -f root(SoT), -f test.yml, (-f macos.yml
##            на Darwin), -p ai-platform-test]. Root compose первым (volumes/networks SoT, U-49);
##            test.yml — если существует; macos-оверрайд — только на Darwin при наличии файла.
## @io       ⇥ compose_path: str → ⎋ list[str] — base compose args
## @complexity O(1)
def _module_compose_args(compose_path: str) -> list[str]:
    """Build compose base args (files + project name) for a module."""
    test_override = Path(compose_path).parent / "docker-compose.test.yml"
    args = ["docker", "compose", *_compose_file_args(compose_path, test_override)]
    macos_override = Path(compose_path).parent / "docker-compose.macos.yml"
    if _platform.system() == "Darwin" and Path(macos_override).exists():
        args.extend(["-f", macos_override])
    args.extend(["-p", "ai-platform-test"])
    return args


# endregion FUNC_module_compose_args


# region FUNC_module_pre_cleanup
## @purpose  teardown-подшаг старта: pre-cleanup `compose down` (SAME module only).
##            НАМЕРЕННО без --remove-orphans — флаг в per-module down убил бы контейнеры
##            ранее запущенных модулей (все модули — один compose project ai-platform-test,
##            но определены в разных compose-файлах; глобальный cleanup уже убрал orphans).
##            Вызывается до старта И повторно перед retry (T3b fix).
## @io       ⇥ compose_base_args, test_module_env, compose_down_timeout → ⎋ None
## @complexity O(1) — один subprocess
def _module_pre_cleanup(
    compose_base_args: list[str],
    test_module_env: dict[str, str | Path],
    compose_down_timeout: int,
) -> None:
    """Run docker compose down (SAME module) before up / before retry (T3b)."""
    down_args = [*compose_base_args, "down", "--timeout", str(compose_down_timeout)]
    _run_docker_smoke(down_args, timeout=20, env_override=test_module_env)  # type: ignore[arg-type]


# endregion FUNC_module_pre_cleanup


# region FUNC_module_start_with_retry
## @purpose  start-блок: retry-until-green цикл compose up (MAX_RETRIES=2, cooldown 5s).
##            MinIO — up -d без --wait + отдельный health-poll (one-shot createbuckets контейнер
##            ломает --wait контракт, D5). Post-up container existence check — silent CI compose
##            failure детект (rc=0 но 0 контейнеров). Diagnostic logs при финальном фейле.
## @io       ⇥ module_name, compose_base_args, test_module_env, compose_timeout,
##            compose_extra_timeout, compose_down_timeout, docker_log_timeout, stderr_tail_lines
##            → ⎋ bool — start_ok
## @complexity O(R) где R = retry-попытки (≤2) + subprocess на попытку
## @invariants
##   - Retry-until-green (TRAP[DECISION] 2026-07-23): один retry после 5s cooldown решает
##     >90% транзиентных фейлов (macOS ресурсная контенция волны 0)
##   - T12.7 (T-11): честный учёт attempts/retries через _bump_retry_stats
##   - MinIO health poll — ВНУТРИ цикла (после up -d), timeout=compose_timeout
def _module_start_with_retry(
    module_name: str,
    compose_base_args: list[str],
    test_module_env: dict[str, str | Path],
    compose_timeout: int,
    compose_extra_timeout: int,
    compose_down_timeout: int,
    docker_log_timeout: int,
    stderr_tail_lines: int,
) -> bool:
    """Start module compose up with retry-until-green + minio workaround + ps check."""
    # ⚠️ TRAP[DECISION] · 2026-07-23 · → · Retry on transient compose failures
    # · Rationale: Docker Desktop on macOS has resource contention when 6+ modules
    # ·   start in parallel (Wave 0). Network creation, image pulls, and healthchecks
    # ·   compete for VM resources → some composes time out. A single retry after
    # ·   5s cooldown resolves >90% of transient failures without masking real bugs.
    # · Rev: if retry count >1 in >15% of CI runs → investigate root cause
    # ·   (resource limits, network contention).
    MAX_RETRIES = 2
    RETRY_DELAY = 5  # seconds between retries
    start_ok = False

    for attempt in range(MAX_RETRIES):
        # T12.7 (T-11): честный учёт retry-rate (attempts/retries) для gate-проверки в sessionfinish
        _bump_retry_stats(retried=attempt > 0)
        if attempt > 0:
            logger.warning(
                "[IMP:8][compose][_start_single_module] Retry %d/%d for '%s' — transient failure cooldown",
                attempt + 1,
                MAX_RETRIES,
                module_name,
            )
            _time.sleep(RETRY_DELAY)
            # ── Re-run pre-cleanup before retry ──────────────────────────
            _module_pre_cleanup(compose_base_args, test_module_env, compose_down_timeout)

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
                env_override=test_module_env,  # type: ignore[arg-type]
            )
            if result.returncode == 0:
                minio_ok = _wait_for_minio_healthy(
                    compose_base_args=compose_base_args,
                    timeout=compose_timeout,
                    logger=logger,
                )
                if not minio_ok:
                    logger.error(
                        "[IMP:9][compose][_start_single_module] MinIO did not become healthy within %ds",
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
                env_override=test_module_env,  # type: ignore[arg-type]
            )

        if result.returncode != 0:
            if attempt < MAX_RETRIES - 1:
                logger.warning(
                    "[IMP:8][compose][_start_single_module] '%s' compose up failed (attempt %d/%d, rc=%d) — will retry",
                    module_name,
                    attempt + 1,
                    MAX_RETRIES,
                    result.returncode,
                )
                continue
            # ── Diagnostic: collect logs for failure analysis ─────────────
            log_args = [*compose_base_args, "logs", "--tail", "50", "--no-color"]
            logs = _run_docker_smoke(log_args, timeout=docker_log_timeout, env_override=test_module_env)  # type: ignore[arg-type]
            logger.error(
                "[IMP:9][compose][_start_single_module] Failed to start '%s' — "
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
        ps_check = _run_docker_smoke(
            [*compose_base_args, "ps", "--all", "--format", "{{.Name}}"],
            timeout=15,
            env_override=test_module_env,  # type: ignore[arg-type]
        )
        container_count = len([cname for cname in ps_check.stdout.strip().splitlines() if cname.strip()])
        if container_count == 0:
            if attempt < MAX_RETRIES - 1:
                logger.warning(
                    "[IMP:8][compose][_start_single_module] '%s' compose up returned 0 but "
                    "no containers exist (attempt %d/%d) — will retry",
                    module_name,
                    attempt + 1,
                    MAX_RETRIES,
                )
                continue
            logger.error(
                "[IMP:9][compose][_start_single_module] '%s' compose up returned 0 but "
                "no containers exist (docker compose ps --all = empty). "
                "CI runner silent compose failure — treating as failed.",
                module_name,
            )
            break  # out of retry loop → fail

        # ── Success ───────────────────────────────────────────────────
        start_ok = True
        if attempt > 0:
            logger.warning(
                "[IMP:9][compose][_start_single_module] '%s' succeeded on retry %d/%d",
                module_name,
                attempt + 1,
                MAX_RETRIES,
            )
        break

    return start_ok


# endregion FUNC_module_start_with_retry


# region FUNC_module_health_poll
## @purpose  health-поллинг после успешного старта: Loki /ready HTTP-poll для observability-модуля
##            (T12.7 T-10). Loki timeout больше НЕ «silent proceed» — loki_ready явно отражается
##            в результате модуля и агрегируется в платформенном результате (потребитель —
##            loki_ready фикстура / sessionfinish диагностика). Ложь = loki-зависимые тесты
##            будут падать осмысленно, а не с каскадом недоступности.
## @io       ⇥ module_name, compose_base_args, platform_ports_dict, compose_timeout, loki_timeout
##            → ⎋ bool — loki_ready (True если модуль не observability / poll прошёл)
## @complexity O(T) где T = poll итераций (только для observability)
# 📝 TRAP[DEBT] · 2026-08-15 · LO · Мёртвая observability-ветка: модуль переименован в logging
# · Observed: module_name == "observability" никогда не совпадает — core/modules/observability
# ·   отсутствует (переименован в logging, 169-схватка), loki-поллинг и loki_ready флаг
# ·   фактически не исполняются (loki_ready всегда True от дефолта)
# · Suspected: ветка осталась от эпохи observability-модуля; platform_ports_dict['LOKI_PORT']
# ·   дал бы KeyError если бы ветка сработала (LOKI_PORT в env_defaults, НЕ в port_mappings)
# · Impact: loki-зависимые тесты не получают честный флаг готовности (T12.7 T-10 не работает
# ·   для logging-модуля); при ре-активации Loki-poll нужно переключить на module_name == "logging"
# ·   и источник LOKI_PORT на get_smoke_env()
# · When: during W8 decomposition (перенос 1:1 по требованию «Семантика 1:1»)
def _module_health_poll(
    module_name: str,
    compose_base_args: list[str],
    platform_ports_dict: dict[str, int],
    compose_timeout: int,
    loki_timeout: int,
) -> bool:
    """Post-start health poll: Loki /ready для observability-модуля (T12.7 T-10)."""
    loki_ready = True
    if module_name == "observability":
        loki_ready = _wait_for_loki_ready(
            url=f"http://localhost:{platform_ports_dict['LOKI_PORT']}/ready",
            timeout=loki_timeout,
            logger=logger,
        )
        _record_loki_ready(loki_ready)
        if not loki_ready:
            logger.error(
                "[IMP:9][compose][_start_single_module] Loki /ready timeout after %ds — "
                "loki-dependent tests will fail (T12.7 T-10)",
                loki_timeout,
            )
        else:
            logger.info("[IMP:9][compose][_start_single_module] Loki /ready OK — loki-dependent tests enabled")
    return loki_ready


# endregion FUNC_module_health_poll


def _start_single_module(
    module_name: str,
    compose_path: str,
    platform_ports_dict: dict[str, int],
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
    ##            DevPlan 170 W8: тело декомпозировано на 5 блоков —
    ##            _module_start_env (env-подготовка) + _module_compose_args (compose-аргументы)
    ##            + _module_pre_cleanup (teardown) + _module_start_with_retry (start)
    ##            + _module_health_poll (health-поллинг). Семантика 1:1.
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
    if compose_path is None:
        logger.warning(
            "[IMP:8][compose][_start_single_module] No compose path for '%s'",
            module_name,
        )
        return {"success": False, "module_name": module_name}

    # ── 1) env-подготовка + 2) compose-аргументы (142 W8 R13: root первым, SoT volumes) ──
    test_module_env = _module_start_env(module_name, compose_path)
    compose_base_args = _module_compose_args(compose_path)

    # ── 3) teardown-подшаг: pre-cleanup down (SAME module, без --remove-orphans, T3b) ──
    _module_pre_cleanup(compose_base_args, test_module_env, compose_down_timeout)

    # ── 4) start: retry-until-green + minio workaround + ps existence check ──
    start_ok = _module_start_with_retry(
        module_name=module_name,
        compose_base_args=compose_base_args,
        test_module_env=test_module_env,
        compose_timeout=compose_timeout,
        compose_extra_timeout=compose_extra_timeout,
        compose_down_timeout=compose_down_timeout,
        docker_log_timeout=docker_log_timeout,
        stderr_tail_lines=stderr_tail_lines,
    )
    if not start_ok:
        return {"success": False, "module_name": module_name}

    # ── 5) health-поллинг: Loki /ready (T12.7 T-10) ──
    loki_ready = _module_health_poll(
        module_name=module_name,
        compose_base_args=compose_base_args,
        platform_ports_dict=platform_ports_dict,
        compose_timeout=compose_timeout,
        loki_timeout=loki_timeout,
    )

    return {"success": True, "module_name": module_name, "loki_ready": loki_ready}


# endregion START_SINGLE_MODULE


# region PLATFORM_SERVICES
## @purpose — Lifecycle fixture: guard/activation → host-artifacts → pre-cleanup → wave-parallel
##            start (Wave 0 sync + Wave 1+ background) → snapshot yield → teardown (stop + release).
##            DevPlan 170 W8: тело декомпозировано на 6 блоков (_guard_and_activate,
##            _setup_host_artifacts, _pre_cleanup, _start_waves, _teardown + оркестратор).
##            Семантика 1:1 (волновой алгоритм, ретраи, логи).


# region FUNC_guard_and_activate
## @purpose  Docker guard + условная активация (T2.2 pattern). Возвращает True если стек
##            следует стартовать: НЕ production host, Docker доступен, хотя бы один тест
##            сессии имеет маркер requires_docker (static-тесты не триггерят compose).
## @io       ⇥ request: pytest.FixtureRequest → ⎋ bool — needs_docker
## @complexity O(I) где I = собранные items сессии
def _guard_and_activate(request: pytest.FixtureRequest) -> bool:
    """Docker guard + conditional activation (T2.2): True = start the stack."""
    if is_production_host():
        pytest.skip("Production host detected — skip smoke suite to prevent container overwrite")

    require_docker_or_fail(reason="smoke suite requires Docker daemon")

    items = request.session.items
    return any(item.get_closest_marker("requires_docker") for item in items)


# endregion FUNC_guard_and_activate


# region FUNC_generate_dev_certs_smoke
## @purpose  142 W8 (R13): генерация dev-сертификатов для smoke nginx-test.
##           Создаёт /tmp/nginx-certs/live/ai-platform.local/{fullchain,privkey}.pem
##           (структура, ожидаемая vhost-шаблонами: /etc/letsencrypt/live/${PLATFORM_DOMAIN}/).
## @io       ⇥ None → ⎋ None (best-effort; лог при сбое)
## @complexity O(1) + 1 subprocess (dev_cert_generator)
def _generate_dev_certs_smoke() -> None:
    """Generate dev certs for nginx smoke (live/${PLATFORM_DOMAIN}/ layout, 142 W8)."""
    cert_root = Path(os.environ.get("NGINX_CERT_DIR", "/tmp/nginx-certs"))
    domain = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")
    live_dir = cert_root / "live" / domain
    try:
        live_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "core.modules.nginx.dev_cert_generator",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "DEV_CERTS_DIR": str(live_dir)},
            check=True,
        )
        logger.info("[IMP:9][compose][platform_services] Dev certs generated at %s", live_dir)
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("[IMP:7][compose][platform_services] Dev certs generation failed (nginx-test may fail): %s", exc)


# endregion FUNC_generate_dev_certs_smoke


# region FUNC_setup_host_artifacts
## @purpose  host-артефакты для bind-mount контейнеров: volume-директории (_SMOKE_VOLUME_BIND_DIRS,
##            T12.9 T-14), test-data файлы status-page (node.yaml, status-metrics.json) и
##            dev-сертификаты nginx (live/${PLATFORM_DOMAIN}/ структура). Возвращает created_host_dirs —
##            список директорий, созданных нами (для best-effort cleanup в teardown).
## @io       → ⎋ list[str] — host-директории, созданные этой фикстурой
## @complexity O(D) где D = bind-dirs + 2 файла + 1 subprocess certs
def _setup_host_artifacts() -> list[str]:
    """Create volume dirs + test data files + dev certs (T12.9 T-14)."""
    # T12.9 (T-14): созданные host-директории (не существовавшие до старта) трекаются
    # в created_host_dirs и удаляются в teardown (только пустые, best-effort) — тест не
    # оставляет артефактов на host.
    created_host_dirs: list[str] = [bind_dir for bind_dir in _SMOKE_VOLUME_BIND_DIRS if not Path(bind_dir).is_dir()]
    _ensure_volume_dirs(_SMOKE_VOLUME_BIND_DIRS)

    # ── Generate test data files for status-page bind-mount ─────────────────
    # status-page docker-compose.test.yml mounts /tmp/test-node-configs/test-node/node.yaml
    # and /tmp/run/platform/status-metrics.json into the container. These files
    # must exist on the host (macOS Docker Desktop requires /tmp paths).
    test_node_yaml = Path("/tmp/test-node-configs/test-node/node.yaml")
    test_status_metrics = Path("/tmp/run/platform/status-metrics.json")

    test_node_yaml.parent.mkdir(parents=True, exist_ok=True)
    test_node_yaml.write_text(
        textwrap.dedent("""\
            node:
              name: test-node
              platform_domain: test.local
            projects: []
            modules: {}
    """),
        encoding="utf-8",
    )
    test_status_metrics.parent.mkdir(parents=True, exist_ok=True)
    test_status_metrics.write_text(
        json.dumps({
            "schema_version": 2,
            "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "node": "test-node",
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {"disk_total_gb": 100.0, "disk_free_gb": 50.0, "disk_used_percent": 50.0},
            "errors": [],
        }),
        encoding="utf-8",
    )

    logger.info("[IMP:8][compose][platform_services] Test data files created for status-page bind-mount")

    # ── 142 W8 (R13): dev-сертификаты для nginx-test (live/${PLATFORM_DOMAIN}/ структура) ──
    # vhost-шаблоны nginx (platform-default.conf.template) ссылаются на
    # /etc/letsencrypt/live/${PLATFORM_DOMAIN}/ — NGINX_CERT_DIR монтируется как
    # /etc/letsencrypt:ro, источник обязан содержать live/ai-platform.local/{fullchain,privkey}.pem.
    _generate_dev_certs_smoke()

    return created_host_dirs


# endregion FUNC_setup_host_artifacts


# region FUNC_pre_cleanup
## @purpose  Глобальная подготовка перед стартом волн: acquire external+test сетей
##            (NetworkLeaseManager), глобальный compose down --remove-orphans всех модулей
##            (TRAP[BUG] 2026-07-17: per-module down --remove-orphans убивал ранее
##            запущенные модули), safety-net удаление stale test-контейнеров (TRAP[BUG]
##            2026-07-18/22), pre-build образов build-модулей (TRAP[BUG] 2026-07-23 cold-cache).
## @io       ⇥ all_compose_files → ⎋ tuple[NetworkLeaseManager, list[str]] — (nm, all_nets)
## @complexity O(N * M) где N = модули, M = compose-файлов на down + stale sweep + pre-build
def _pre_cleanup(
    all_compose_files: dict[str, str],
) -> tuple[object, list[str]]:
    """Acquire networks, global down, stale container sweep, pre-build images."""
    # ── Acquire external + test networks via NetworkLeaseManager ──────────────
    logger.info("[IMP:8][compose][platform_services] Acquiring external and test networks via NetworkLeaseManager")
    nm = get_network_manager()
    all_nets = sorted(_collect_external_networks() | TEST_NETWORKS)
    for net_name in all_nets:
        nm.acquire(net_name)
    logger.info("[IMP:9][compose][platform_services] All %d networks acquired via NetworkLeaseManager", len(all_nets))

    # ── Global pre-cleanup: down ALL compose files before starting ─────────
    # ⚠️ TRAP[BUG] · 2026-07-17 · HI · per-module `down --remove-orphans` killed
    #    previously started modules (all share project=ai-platform-test). Fix:
    #    global cleanup at start, per-module down WITHOUT --remove-orphans.
    logger.info("[IMP:8][compose][platform_services] Global pre-cleanup: down all compose files")
    for _cleanup_name, cleanup_path in sorted(all_compose_files.items()):
        # 142 W8 (R13): root compose в цепочке (volumes SoT, U-49)
        test_override = Path(cleanup_path).parent / "docker-compose.test.yml"
        cleanup_args = ["docker", "compose", *_compose_file_args(cleanup_path, test_override)]
        cleanup_args.extend([
            "-p",
            "ai-platform-test",
            "down",
            "--timeout",
            str(_COMPOSE_DOWN_TIMEOUT),
            "--remove-orphans",
        ])
        _run_docker_smoke(cleanup_args, timeout=20)
    logger.info("[IMP:8][compose][platform_services] Global pre-cleanup complete")

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
    STALE_CONTAINER_NAMES = _infra.stale_container_names
    for cname in STALE_CONTAINER_NAMES:
        subprocess.run(["docker", "rm", "-f", cname], capture_output=True, text=True, timeout=10, check=False)
    logger.info("[IMP:8][compose][platform_services] Safety net: stale containers removed")

    # ── Pre-build images for modules with build: directive ──────────────────
    # ⚠️ TRAP[BUG] · 2026-07-23 · MED · status-page cold-cache build timeouts smoke test
    # · status-page uses build: (not pre-built image) — docker compose up -d --wait
    # · may timeout on first gate run when Docker layer cache is cold (image pull +
    # · pip install + app copy). Pre-build ensures image exists before --wait timer starts.
    # · Rev: if more build-based modules added, add them here or switch to auto-detection.
    logger.info("[IMP:8][compose][platform_services] Pre-building images for build-based modules")
    PRE_BUILD_MODULES = ["status-page"]  # modules with build: that are started in smoke tests
    for pb_module in PRE_BUILD_MODULES:
        pb_compose = all_compose_files.get(pb_module)
        if pb_compose is None:
            continue
        # 142 W8 (R13): root compose в цепочке (volumes SoT, U-49)
        pb_test_override = Path(pb_compose).parent / "docker-compose.test.yml"
        pb_build_args = ["docker", "compose", *_compose_file_args(pb_compose, pb_test_override)]
        pb_build_args.extend(["-p", "ai-platform-test", "build", "--no-cache"])
        pb_result = _run_docker_smoke(
            pb_build_args,
            timeout=180,
            env_override={
                "COMPOSE_PROFILES": pb_module,
                "TEST_MODULE_DIR": Path(pb_compose).parent,
            },
        )
        if pb_result.returncode != 0:
            logger.warning(
                "[IMP:8][compose][platform_services] Pre-build failed for '%s' (returncode=%d) — "
                "compose up will attempt build anyway",
                pb_module,
                pb_result.returncode,
            )
        else:
            logger.info("[IMP:8][compose][platform_services] Pre-build complete for '%s'", pb_module)

    return nm, all_nets


# endregion FUNC_pre_cleanup


# region FUNC_record_wave_result
## @purpose  Извлечение тела try (PLW0717): future.result() → append под _WAVE_STATE_LOCK;
##            исключение → failed. Конкурентная мутация started_list/failed_list с основным
##            потоком (снимок, T12.2 T-3).
## @io       ⇥ future, wm_module_name, started_list, failed_list → ⎋ None
## @complexity O(1)
def _record_wave_result(future, wm_module_name: str, started_list: list[str], failed_list: list[str]) -> None:
    """Append future outcome to started/failed under _WAVE_STATE_LOCK (T12.2 T-3)."""
    try:
        wm_result = future.result()
        success = isinstance(wm_result, dict) and wm_result.get("success")
        with _WAVE_STATE_LOCK:
            (started_list if success else failed_list).append(wm_module_name)
    except Exception:  # ruff: ignore[BLE001] — best-effort: исключение future → failed (T12.2)
        with _WAVE_STATE_LOCK:
            failed_list.append(wm_module_name)


# endregion FUNC_record_wave_result


# region FUNC_start_remaining
## @purpose  Запуск волн 1+ в фоновом daemon-потоке (DevPlan 040 Wave 4): каждая волна
##            сигналит готовность после завершения (signal_wave_ready), разблокируя тесты.
##            started_list/failed_list мутируются под _WAVE_STATE_LOCK (T12.2 T-3 — основной
##            поток делает снимок перед yield). Финальный wave-event (len(wave_list)) —
##            «all waves done» для platform_services-тестов (TRAP[BUG] 2026-07-23).
## @io       ⇥ wave_list, started_list, failed_list, all_compose_files, module_graph,
##            platform_ports_dict → ⎋ None
## @complexity O(W * M) где W = волны, M = модулей в волне
def _start_remaining(
    wave_list: list[list[str]],
    started_list: list[str],
    failed_list: list[str],
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_ports_dict: dict[str, int],
) -> None:
    """Start remaining waves in background thread (wave-parallel, T12.2)."""
    for wave_idx in range(1, len(wave_list)):
        wave_modules = wave_list[wave_idx]
        logger.info(
            "[IMP:8][compose][platform_services] Wave %d (background): starting %d module(s)",
            wave_idx,
            len(wave_modules),
        )

        with ThreadPoolExecutor(max_workers=len(wave_modules)) as executor:
            futures = {}
            for wm_module_name in wave_modules:
                wm_compose_path = all_compose_files.get(wm_module_name)
                if wm_compose_path is None:
                    continue
                future = executor.submit(
                    _start_single_module,
                    wm_module_name,
                    wm_compose_path,
                    platform_ports_dict,
                    PLATFORM_COMPOSE_TIMEOUT,
                    _COMPOSE_EXTRA_TIMEOUT,
                    _COMPOSE_DOWN_TIMEOUT,
                    _DOCKER_LOG_TIMEOUT,
                    _STDERR_TAIL_LINES,
                    PLATFORM_LOKI_TIMEOUT,
                )
                futures[future] = wm_module_name

            for future in as_completed(futures):
                wm_module_name = futures[future]
                _record_wave_result(future, wm_module_name, started_list, failed_list)

            logger.info(
                "[IMP:9][compose][platform_services] Wave %d (background) complete: %d started, %d failed",
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


# endregion FUNC_start_remaining


# region FUNC_start_waves
## @purpose  Wave-parallel старт: Wave 0 — синхронно (критический путь setup фикстуры),
##            волны 1+ — фоновый daemon-поток (оверлап старта контейнеров с исполнением
##            тестов). Возвращает списки started/failed, waves и bg_thread (для join в finally).
## @io       ⇥ module_graph, all_compose_files, platform_ports_dict
##            → ⎋ tuple[list[str], list[str], list[list[str]], threading.Thread | None]
## @complexity O(W * M) где W = волны, M = модулей в волне (subprocess-bound)
def _start_waves(
    module_graph: dict[str, list[str]],
    all_compose_files: dict[str, str],
    platform_ports_dict: dict[str, int],
) -> tuple[list[str], list[str], list[list[str]], threading.Thread | None]:
    """Start compose files in wave-parallel order (Wave 0 sync → Wave 1+ background)."""
    started: list[str] = []
    failed: list[str] = []
    waves = build_waves(module_graph)
    logger.info(
        "[IMP:8][compose][platform_services] Built %d wave(s) from %d module(s)",
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
        logger.info(
            "[IMP:8][compose][platform_services] Wave 0 (sync): starting %d module(s) in parallel",
            len(wave_0_modules),
        )

        with ThreadPoolExecutor(max_workers=len(wave_0_modules)) as executor:
            futures = {}
            for wm_module_name in wave_0_modules:
                wm_compose_path = all_compose_files.get(wm_module_name)
                if wm_compose_path is None:
                    logger.warning(
                        "[IMP:8][compose][platform_services] Wave 0: no compose path for '%s' — skipping",
                        wm_module_name,
                    )
                    continue
                future = executor.submit(
                    _start_single_module,
                    wm_module_name,
                    wm_compose_path,
                    platform_ports_dict,
                    PLATFORM_COMPOSE_TIMEOUT,
                    _COMPOSE_EXTRA_TIMEOUT,
                    _COMPOSE_DOWN_TIMEOUT,
                    _DOCKER_LOG_TIMEOUT,
                    _STDERR_TAIL_LINES,
                    PLATFORM_LOKI_TIMEOUT,
                )
                futures[future] = wm_module_name

            for future in as_completed(futures):
                wm_module_name = futures[future]
                try:
                    wm_result = future.result()
                    if isinstance(wm_result, dict) and wm_result.get("success"):
                        started.append(wm_module_name)
                    else:
                        failed.append(wm_module_name)
                except Exception as exc:  # ruff: ignore[BLE001] — best-effort: сбой волны → failed, не крах сессии
                    logger.error(
                        "[IMP:9][compose][platform_services] Wave 0 module '%s' raised: %s",
                        wm_module_name,
                        exc,
                    )
                    failed.append(wm_module_name)

        logger.info(
            "[IMP:9][compose][platform_services] Wave 0 complete: %d started, %d failed",
            len(started),
            len(failed),
        )
        signal_wave_ready(0)

        # Wave 1+ — background thread (overlaps container start with test execution)
        bg_thread = threading.Thread(
            target=_start_remaining,
            args=(waves, started, failed, all_compose_files, module_graph, platform_ports_dict),
            daemon=True,
        )
        bg_thread.start()

    return started, failed, waves, bg_thread


# endregion FUNC_start_waves


# region FUNC_teardown
## @purpose  teardown: compose stop (не down — быстрее, сохраняет volumes, DevPlan 040 Wave 3;
##            финальный down — pytest_sessionfinish), release сетей NetworkLeaseManager и
##            T12.9 (T-14) host-артефакты cleanup (test-файлы + пустые директории, только
##            созданные фикстурой; rmdir падает если в директории осталось содержимое —
##            такие директории НЕ удаляем, их создал не тест).
## @io       ⇥ started, failed, all_compose_files, nm, all_nets, created_host_dirs → ⎋ None
## @complexity O(M) где M = модули (stop subprocess) + O(N) сетей release + O(D) dirs
def _teardown(
    started: list[str],
    failed: list[str],
    all_compose_files: dict[str, str],
    nm: object,
    all_nets: list[str],
    created_host_dirs: list[str],
) -> None:
    """Stop modules, release networks, clean host artifacts (T12.9)."""
    # ── Teardown: compose stop (not down) — faster, preserves volumes ────────
    # DevPlan 040 Wave 3: compose down → compose stop saves ~50s.
    # Final compose down happens in pytest_sessionfinish (session.py).
    all_modules = list(reversed(started + [m for m in failed if m not in started]))
    logger.info("[IMP:7][compose][platform_services] Stopping %d module(s) (compose stop, not down)", len(all_modules))
    for module_name in all_modules:
        compose_path = all_compose_files.get(module_name)
        if compose_path is None:
            continue
        stop_args = _module_compose_args(compose_path)
        stop_args.extend(["-p", "ai-platform-test", "stop", "--timeout", str(_COMPOSE_DOWN_TIMEOUT)])

        stop_result = _run_docker_smoke(stop_args, timeout=20)
        if stop_result.returncode != 0:
            logger.error(
                "[IMP:9][compose][platform_services] Stop failed for '%s': %s",
                module_name,
                stop_result.stderr.strip()[-200:],
            )

    # ── Release networks via NetworkLeaseManager ──────────────────────────────
    logger.info("[IMP:8][compose][platform_services] Releasing %d network(s) via NetworkLeaseManager", len(all_nets))
    for net_name in reversed(all_nets):
        nm.release(net_name)  # type: ignore[attr-defined]

    # ── T12.9 (T-14): host-артефакты teardown (best-effort, только созданные нами) ──
    # Удаляем тестовые файлы и пустые директории, созданные fixture'ой. rmdir падает
    # если в директории осталось содержимое (контейнерные volume-данные) — это ожидаемо,
    # такие директории НЕ удаляем (их создал не тест).
    test_artifacts = [
        Path("/tmp/test-node-configs/test-node/node.yaml"),
        Path("/tmp/run/platform/status-metrics.json"),
    ]
    for artifact in test_artifacts:
        try:
            artifact.unlink(missing_ok=True)
        except OSError as exc:
            logger.info("[IMP:7][compose][platform_services] T12.9: artifact cleanup skip: %s", exc)
    for dir_path in reversed(created_host_dirs):
        with contextlib.suppress(OSError):
            Path(dir_path).rmdir()  # только пустые — OSError если не пусто (данные не наши)
    if created_host_dirs:
        logger.info("[IMP:8][compose][platform_services] T12.9: removed %d empty host dir(s)", len(created_host_dirs))

    logger.info("[IMP:9][compose][platform_services] Cleanup complete")


# endregion FUNC_teardown


# 🧐 TRAP[DECISION] · 2026-08-15 · — · platform_ports alias-фикстура удалена (Rev TRAP 2026-07-22 выполнен)
# · Rejected: сохранить backward-compat alias (делегирование platform_port_mappings_dict)
# · Reason: Rev-условие наступило — потребители alias'а были ТОЛЬКО _conftest-внутренние
# ·   (platform_services + _start_single_module; grep по tests/: внешние тесты используют
# ·   platform_port_mappings_dict напрямую или локальные переменные с именем platform_ports)
# · Rev: если внешний тест запросит фикстуру platform_ports — восстановить alias в _conftest/env
@pytest.fixture(scope="session")
def platform_services(
    request: pytest.FixtureRequest,
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_port_mappings_dict: dict[str, int],
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
    ##            DevPlan 170 W8: оркестратор 6 блоков (_guard_and_activate, _setup_host_artifacts,
    ##            _pre_cleanup, _start_waves, _teardown) — семантика 1:1.
    ## @io — ⇥ request, all_compose_files, module_graph, platform_port_mappings_dict
    ##       → ⎋ dict[str, list[str]]: {"started": [module_names], "failed": [module_names],
    ##                                  "loki_ready": bool} — СНИМОК под lock (T12.2 T-3)
    ## @complexity — O(N + M) where N = compose files, M = networks
    ## @invariants
    ##   - started/failed — СНИМОК (list-копия) под _WAVE_STATE_LOCK перед yield (T12.2 T-3):
    ##     фоновый поток волн дописывает списки ПОСЛЕ старта — «живые» списки = гонка чтения
    ##   - Финальный wave-event сигналится в try/finally основного потока (T12.2 T-4): даже если
    ##     фоновый поток умер, тесты platform_services-волны не висят 600s до timeout
    ##   - Только master (session-фикстура): воркеры не создают свой стек
    ##   - platform_ports alias удалён (Rev TRAP[DECISION] 2026-07-22) — порты из
    ##     platform_port_mappings_dict напрямую (потребители alias'а были только внутренние)
    """
    # ── 1) Guard + conditional activation (T2.2) ──────────────────────────────
    if not _guard_and_activate(request):
        logger.info("[IMP:8][compose][platform_services] No test requires Docker — yielding no-op")
        yield {"started": [], "failed": [], "loki_ready": _loki_ready_aggregate()}
        return

    logger.info("[IMP:7][compose][platform_services] Starting platform services")

    # ── 2) Host artifacts: volume dirs + test data files + dev certs (T12.9) ──
    created_host_dirs = _setup_host_artifacts()

    # ── 3) Pre-cleanup: networks + global down + stale sweep + pre-build ──────
    nm, all_nets = _pre_cleanup(all_compose_files)

    # ── 4) Wave-parallel start (Wave 0 sync → Wave 1+ background) ─────────────
    started, failed, waves, bg_thread = _start_waves(module_graph, all_compose_files, platform_port_mappings_dict)

    # T12.2 (T-3): СНИМОК started/failed под lock перед yield — тесты получают
    # консистентный снимок, не «живые» списки (фон дописывает их после старта).
    with _WAVE_STATE_LOCK:
        result_snapshot = {"started": list(started), "failed": list(failed), "loki_ready": _loki_ready_aggregate()}
    logger.info(
        "[IMP:9][compose][platform_services] Result: %d started, %d failed",
        len(result_snapshot["started"]),
        len(result_snapshot["failed"]),
    )
    try:
        yield result_snapshot
    finally:
        # T12.2 (T-4): финальный wave-event сигналится в main thread finally — даже при
        # падении фонового потока тесты platform_services-волны не ждут 600s до timeout.
        signal_wave_ready(len(waves))

        # ── Wait for background thread (if any) before teardown ─────────────────
        if bg_thread is not None:
            bg_thread.join(timeout=600)

    # ── 5) Teardown: compose stop + network release + host-artifact cleanup ────
    _teardown(started, failed, all_compose_files, nm, all_nets, created_host_dirs)


# endregion PLATFORM_SERVICES
