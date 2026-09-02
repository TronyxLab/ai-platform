# GREP_SUMMARY: env, SMOKE_ENV, platform-env.yaml, env_defaults, lazy, PEP-562, platform_env, is_macos, compose-timeout
# STRUCTURE: ┌_STATIC_SMOKE_ENV + SMOKE_ENV_GENERATED┐ → load_platform_env_defaults(◇ platform-env.yaml | fallback) → get_smoke_env(lru merge) → __getattr__(lazy SMOKE_ENV) → platform_env(module-scoped inject/restore)

# region MODULE_CONTRACT
## @purpose  SMOKE_ENV construction and injection for smoke tests: static test-specific env,
##           runtime env_defaults load (platform-env.yaml, T12.4 T-7 lazy), merge order
##           env_defaults → static → generated, lazy PEP 562 module __getattr__, and the
##           module-scoped platform_env fixture (T12.3 T-6). Extracted from smoke.py (DevPlan 170 W8).
## @scope    Consumed by _conftest/compose.py (_run_docker_smoke), _conftest/__init__ and
##           tests/conftest.py re-export; the platform_env fixture is module-scoped.
## @invariants
##   - SMOKE_ENV — ЛЕНИВЫЙ (T12.4 T-7, PEP 562 __getattr__): platform-env.yaml грузится при
##     первом обращении к атрибуту (не import-time); `from X import SMOKE_ENV` НЕ ленив
##     (import machinery триггерит __getattr__ — совместимо с legacy, проверено эмпирически)
##   - fallback на env_defaults_generated.py при отсутствии platform-env.yaml (T12.4 T-7)
##   - Merge order: env_defaults → static → generated (TRAP[DECISION] 2026-07-31: static содержит
##     ТОЛЬКО тест-специфику и должен побеждать env_defaults; generated (ci_default секреты) — последний)
##   - platform_env — module-scoped (T12.3 T-6): инжектится только для модулей, запрашивающих её;
##     platform_services (session) НЕ зависит от неё (compose получает SMOKE_ENV через merge)
##   - _STATIC_SMOKE_ENV — только тест-специфика: test-порты, tmp-пути, TRAP-оверрайды
##     (дубли env_defaults УДАЛЕНЫ, DevPlan 116 T3, U-16/U-17/D2)
## @rationale  Extracted from smoke.py to isolate env-domain from compose lifecycle (W8).
## @changes    CREATED: 2026-08-15 | DevPlan 170 W8: вынесен из tests/_conftest/smoke.py
##             (T12.3/T12.4 логика сохранена 1:1; исторический MODULE_CONTRACT — в smoke.py фасаде)
# endregion MODULE_CONTRACT

import functools
import logging
import os
import platform as _platform

import pytest

from _conftest.smoke_env_generated import SMOKE_ENV_GENERATED

logger = logging.getLogger(__name__)

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

# ── Static test-specific env (DevPlan 116 T3, U-16/U-17/D2) ─────────
# Значения, дублирующие env_defaults из platform-env.yaml, УДАЛЕНЫ — они
# загружаются runtime-мержем (load_platform_env_defaults). Здесь остаётся
# ТОЛЬКО тест-специфика: test-порты, tmp-пути, намеренные TRAP-оверрайды.
_STATIC_SMOKE_ENV: dict[str, str] = {
    "COMPOSE_PROJECT_NAME": "ai-platform-test",
    "PLATFORM_DIR": "/tmp/ai-platform-test",
    # 142 W8 (R13): NGINX_OVERLAY_DIR — B23 fail-fast (${VAR:?}), в platform-env.yaml пустое
    # (прод-инжекция деплоем) → smoke-compose падал «required variable ... missing» на ВСЕХ
    # модулях (nginx включён в root include каждого стека). Статика побеждает env_defaults
    # (TRAP[DECISION] 2026-07-31 merge order) — тест-путь для overlay-каталога.
    "NGINX_OVERLAY_DIR": "/tmp/nginx-overlay-test",
    "S3_ENDPOINT_URL": "",  # 🧐 TRAP[DECISION] · 2026-07-24 · — · Empty — skip S3 in test · Rejected: реальный S3 endpoint в CI · Reason: production endpoint unreachable in CI (deferred workaround) · Rev: CI-доступ к S3
    # 142 W8 (R13): NGINX_CERT_DIR — host-директория с live/<PLATFORM_DOMAIN>/ структурой
    # (vhost-шаблоны nginx ссылаются на /etc/letsencrypt/live/...). Значение создаётся
    # в platform_services (generate_dev_certs_smoke) — старый /etc/nginx/dev-certs был
    # несуществующим host-путём → docker монтировал пустую директорию → nginx emerg.
    "NGINX_CERT_DIR": "/tmp/nginx-certs",
    # 142 W8 (R13): PROMETHEUS_TARGETS_DIR/RULES_DIR — прод-пути /opt/platform/* не шарится
    # Docker Desktop (macOS) → monitoring compose up «mounts denied» (R13 pre-existing).
    # tmp-директории создаются fixture'ой (_SMOKE_VOLUME_BIND_DIRS); статика побеждает
    # env_defaults (TRAP[DECISION] 2026-07-31 merge order).
    "PROMETHEUS_TARGETS_DIR": "/tmp/prometheus-targets",
    "PROMETHEUS_RULES_DIR": "/tmp/prometheus-rules",
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
    "CONTEXT_IMAGE": "hermes-agent-context:latest",
    "LITELLM_TEST_PORT": "14000",
    "HERMES_DASHBOARD_TEST_PORT": "19119",
    "HERMES_DESKTOP_TEST_PORT": "18642",
    "LANGFUSE_TEST_PORT": "13000",
    "PROMETHEUS_TEST_PORT": "19090",
    "GRAFANA_TEST_PORT": "13030",
}


def load_platform_env_defaults() -> dict[str, str]:
    """Load env_defaults from repo-root platform-env.yaml (runtime, D2).

    ## @purpose — Runtime-источник не-секретных env-дефолтов для smoke-тестов.
    ##            Устраняет дубли static-копий (DevPlan 116 T3, U-17): значения
    ##            (PLATFORM_DOMAIN, POSTGRES_USER, PROMETHEUS_TARGETS_DIR, ...)
    ##            читаются из generated platform-env.yaml, а не хардкодятся.
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
        logger.warning(
            "[IMP:8][env][load_platform_env_defaults] platform-env.yaml not found at %s — "
            "falling back to env_defaults_generated.py",
            env_path,
        )
        return _fallback_env_defaults()
    with env_path.open(encoding="utf-8") as f:
        data = _yaml_load.safe_load(f)
    raw = (data or {}).get("env_defaults", {})
    defaults = {str(k): str(v) for k, v in raw.items() if v is not None} if isinstance(raw, dict) else {}
    logger.info("[IMP:8][env][load_platform_env_defaults] Loaded %d env_defaults from %s", len(defaults), env_path)
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
        logger.info(
            "[IMP:8][env][fallback_env_defaults] Using %d generated CI defaults (fallback)",
            len(result),
        )
    except Exception as exc:  # ruff: ignore[BLE001] — best-effort: generated-файл отсутствует/битый — пустой fallback
        logger.warning("[IMP:7][env][fallback_env_defaults] env_defaults_generated.py unavailable: %s", exc)
        return {}
    else:
        return result


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
    platform_defaults = load_platform_env_defaults()
    return {**platform_defaults, **_STATIC_SMOKE_ENV, **SMOKE_ENV_GENERATED}


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
# Совместимость: `from _conftest.env import SMOKE_ENV` и `SMOKE_ENV` внутри модуля работают
# (import machinery триггерит __getattr__ — legacy-поведение сохранено).


def __getattr__(name: str) -> object:
    """PEP 562: ленивые SMOKE_ENV / PLATFORM_ENV_DEFAULTS (T12.4 T-7)."""
    if name == "SMOKE_ENV":
        return get_smoke_env()
    if name == "PLATFORM_ENV_DEFAULTS":
        return load_platform_env_defaults()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


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
    logger.info("[IMP:7][env][platform_env] Setting SMOKE_ENV environment variables")
    smoke_env = get_smoke_env()
    saved: dict[str, str | None] = {}
    for key in smoke_env:
        saved[key] = os.environ.get(key)
        os.environ[key] = smoke_env[key]

    yield smoke_env

    logger.info("[IMP:9][env][platform_env] Restoring original environment")
    for key in smoke_env:
        env_value = saved[key]
        if env_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = env_value
    logger.info("[IMP:7][env][platform_env] Environment restored")


# ═══════════════════════════════════════════════════════════════════
# T9 (DevPlan 029, deploy-integrity): env-hermeticity autouse fixture
# ═══════════════════════════════════════════════════════════════════

# Платформенные env-ключи, которые могут ПРОТЕКАТЬ в os.environ сессии и давать
# ложные вердикты unit/gate-тестов (класс «NODE_NAME-утечка → ложный зелёный
# DR-restore», T9 rationale; POSTGRES_PASSWORD-инжект через _conftest/e2e.py
# early-dotenv-load — test_secrets_postcondition autouse-чистка):
#   - идентичность ноды/конфигов: NODE_NAME, NODE_YAML, NODE_CONFIGS_DIR, CORE_DIR,
#     SECRETS_ENV_FILE, PLATFORM_ROOT
#   - AGE/sops master-ключ (env-канон): AGE_SECRET_KEY, AGE_SECRET_KEY_FILE, SOPS_AGE_KEY
#   - manifest-секреты platform/модулей (tier=required/generated, source=sops/autogen):
#     GHCR_PULL_TOKEN, POSTGRES_PASSWORD, POSTGRES_USER, CLICKHOUSE_PASSWORD,
#     REDIS_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, LITELLM_MASTER_KEY,
#     PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD, S3_ACCESS_KEY, S3_SECRET_KEY
# Расширение списка — с ревью (связка с secret-definitions.yaml/secret-манифестом);
# ключи, которые тест задаёт САМ через monkeypatch.setenv, остаются видимы телу теста
# (fixture удаляет ТОЛЬКО пред-существующий «фон»).
PLATFORM_ENV_LEAK_KEYS: tuple[str, ...] = (
    "NODE_NAME",
    "NODE_YAML",
    "NODE_CONFIGS_DIR",
    "CORE_DIR",
    "SECRETS_ENV_FILE",
    "PLATFORM_ROOT",
    "AGE_SECRET_KEY",
    "AGE_SECRET_KEY_FILE",
    "SOPS_AGE_KEY",
    "GHCR_PULL_TOKEN",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "CLICKHOUSE_PASSWORD",
    "REDIS_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "LITELLM_MASTER_KEY",
    "PLATFORM_MASTER_EMAIL",
    "PLATFORM_MASTER_PASSWORD",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
)


@pytest.fixture(autouse=True)
def hermetic_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """T9 (DevPlan 029): strip leaked platform env keys before every unit/gate test.

    ## @purpose — Hermeticity: никакой платформенный env-ключ не должен переживать
    ##            import/collection и попадать в os.environ следующего детерминированного
    ##            теста (NODE_NAME-утечка дала ложный зелёный DR-restore; e2e-early-dotenv
    ##            инжектил POSTGRES_* в unit-сессии). Тест, которому ключ нужен, ставит его
    ##            через monkeypatch.setenv — env-контракт явный.
    ## @io — ⇥ monkeypatch → ⎋ None (delenv на каждый ключ, restore в teardown)
    ## @complexity — O(K), K = len(PLATFORM_ENV_LEAK_KEYS)
    ## @invariants
    ##   - autouse: применяется ко ВСЕМ тестам тестам-директорий, регистрирующим fixture
    ##     (tests/unit/conftest.py + tests/gates/conftest.py — детерминированные слои;
    ##     docker/смоук-слои env-зависимы по дизайну и НЕ регистрируют)
    ##   - monkeypatch.delenv(raising=False) — restore в teardown (после теста env вернётся)
    ##   - setenv в теле теста побеждает (fixture отработал ДО тела)
    """
    for key in PLATFORM_ENV_LEAK_KEYS:
        monkeypatch.delenv(key, raising=False)
