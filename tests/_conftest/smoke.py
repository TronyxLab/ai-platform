# GREP_SUMMARY: smoke, platform, docker, compose, loki, platform_services, platform_env, SMOKE_ENV, external-networks, facade, re-export
# STRUCTURE: ┌historical MODULE_CONTRACT┐ → re-export _conftest/{env,compose,health,containers,shared} → __getattr__(lazy SMOKE_ENV/PLATFORM_ENV_DEFAULTS) → ⎋ facade <100 LOC

# region MODULE_CONTRACT
## @purpose  FACADE over the decomposed _conftest smoke domain (DevPlan 170 W8). Preserves the
##           historical smoke.py import surface (`from _conftest.smoke import ...`) for
##           backward compatibility while all logic lives in _conftest/{env,compose,health,
##           containers,shared}.py. <100 LOC by design.
## @scope    Backward-compat re-export hub; direct importers migrated to canonical modules:
##           _conftest/compose.py (_compose_file_args, retry_stats), _conftest/containers.py
##           (_module_container_running), _conftest/env.py (SMOKE_ENV, platform_env, is_macos),
##           _conftest/health.py (loki_ready), _conftest/shared.py (волновой алгоритм).
## @invariants
##   - ВСЕ публичные имена legacy smoke.py доступны через этот фасад (re-export / __getattr__)
##   - SMOKE_ENV / PLATFORM_ENV_DEFAULTS — ленивые через PEP 562 __getattr__ (T12.4 T-7):
##     import-time НЕ грузят platform-env.yaml; `from _conftest.smoke import SMOKE_ENV`
##     триггерит __getattr__ (eager при import — legacy-поведение, проверено эмпирически)
##   - Фасад НЕ содержит логики — только re-export (дрейф логики → источник в подмодуле)
## @rationale  Декомпозиция монолита 1475 LOC (research-A §8) без слома импорт-графа:
##             фасад даёт время потребителям мигрировать на канонические модули.
## @changes    HISTORICAL MODULE_CONTRACT (до декомпозиции, DevPlan 136 W12) — см. git history
##             tests/_conftest/smoke.py до commit W8: T12.2 (wave lock+snapshot+finally),
##             T12.3 (platform_env module-scope), T12.4 (lazy SMOKE_ENV + fallback),
##             T12.7 (loki_ready, retry-stats), T12.9 (host-dirs cleanup); 142 W8 (R13 root
##             compose SoT); platform_ports alias УДАЛЁН (Rev TRAP[DECISION] 2026-07-22)
## @modulemap — env.py → SMOKE_ENV, platform_env, is_macos, PLATFORM_*_TIMEOUT, get_smoke_env
##              compose.py → platform_services, retry_stats, _compose_file_args, _run_docker_smoke
##              health.py → loki_ready, _wait_for_loki_ready, _wait_for_minio_healthy
##              containers.py → _module_container_running
##              shared.py → _is_xdist_worker, compute_module_waves, build_waves
# endregion MODULE_CONTRACT

# ── env domain ──────────────────────────────────────────────────────────────
# ── compose domain ──────────────────────────────────────────────────────────
from _conftest.compose import (  # ruff: ignore[F401]
    _RETRY_RATE_THRESHOLD,
    _bump_retry_stats,
    _compose_file_args,
    _run_docker_smoke,
    _set_retry_stats,
    platform_services,
    retry_stats,
)

# ── containers domain ───────────────────────────────────────────────────────
from _conftest.containers import _module_container_running  # ruff: ignore[F401]
from _conftest.env import (  # ruff: ignore[F401]
    PLATFORM_COMPOSE_TIMEOUT,
    PLATFORM_LOKI_TIMEOUT,
    get_smoke_env,
    is_macos,
    load_platform_env_defaults,
    platform_env,
)

# ── health domain ───────────────────────────────────────────────────────────
from _conftest.health import (  # ruff: ignore[F401]
    _record_loki_ready,
    _wait_for_loki_ready,
    _wait_for_minio_healthy,
    loki_ready,
)

# ── shared domain ───────────────────────────────────────────────────────────
from _conftest.shared import (  # ruff: ignore[F401]
    _is_xdist_worker,
    build_waves,
    compute_module_waves,
)


def __getattr__(name: str) -> object:
    """PEP 562: ленивые SMOKE_ENV / PLATFORM_ENV_DEFAULTS (T12.4 T-7) + legacy _build_waves alias.

    ## @purpose — Фасад сохраняет lazy-семантику legacy smoke.py: SMOKE_ENV/PLATFORM_ENV_DEFAULTS
    ##            грузятся при первом доступе к атрибуту (не import-time). _build_waves —
    ##            legacy-алиас на shared.build_waves (прямых потребителей нет, совместимость).
    ## @io       ⇥ name → ⎋ объект | AttributeError
    ## @complexity O(1)
    """
    if name == "SMOKE_ENV":
        return get_smoke_env()
    if name == "PLATFORM_ENV_DEFAULTS":
        return load_platform_env_defaults()
    if name == "_build_waves":
        return build_waves
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
