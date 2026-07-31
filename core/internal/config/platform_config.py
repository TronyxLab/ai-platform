#!/usr/bin/env python3
# GREP_SUMMARY: platform_config, config-facade, defaults, SoT, S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT, PLATFORM_CONTEXT
# STRUCTURE: ▶ load platform-env.yaml → env_defaults dict → ◇ typed accessors → ⎋ get_default(key, fallback)
# region MODULE_CONTRACT
## @purpose  Единый Python-фасад для чтения default-значений из platform-env.yaml.
##           Все consumers платформы получают default'ы только через этот модуль.
## @scope    Импортируется backup_config.py, s3_ssl_cache.py, cert_orchestrator.py,
##           preflight.py, docker_orchestrator.py, agent_watchdog.py, context_deployer.py
## @invariants
##   - Единственный Source of Truth для default-значений в Python-коде
##   - Загружает platform-env.yaml при первом импорте (lazy-load с кэшированием)
##   - Все accessors возвращают str; числовые значения — ответственность вызывающего
##   - Если platform-env.yaml недоступен — использует жёстко закодированные fallback'и,
##     идентичные значениям в platform-infra.yaml (defence-in-depth)
##   - ИСКЛЮЧЕНИЕ (DevPlan 116 B6 D4): CONTEXT не имеет литерального fallback'а —
##     default_context() возвращает "" при отсутствии platform-env.yaml (fail-visible)
##   - default_s3_bucket_sentinel() возвращает "" с явной семантикой sentinel
##     («S3 не сконфигурирован — graceful degradation»)
##   - default_context_sentinel() возвращает "" с семантикой валидационного sentinel
##     («CONTEXT обязателен — требуй явного указания»)
## @rationale Устраняет класс дрейфа «SoT обновлён, consumers — нет».
##            Централизованный фасад делает default'ы grepable и тестируемыми.
## @changes   CREATED: 2026-07-26 · DevPlan 037 — config defaults unification
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── Fallback values (defence-in-depth) ──────────────────────────────────────
# These MUST match values in platform-infra.yaml env_defaults section.
# Updated when SoT changes.
_FALLBACK_S3_REGION = "ru-1"
_FALLBACK_S3_PREFIX = "platform/backups"
_FALLBACK_S3_BUCKET = "test-bucket"
# NOTE (DevPlan 116 B6 D4): _FALLBACK_CONTEXT удалён — CONTEXT не имеет литерального
# fallback'а. default_context() → get_default("CONTEXT", "") — при отсутствии
# platform-env.yaml возвращает "" (fail-visible вместо тихой лжи "test").
_FALLBACK_PLATFORM_CONTEXT = "personal"

# Sentinel values (not from SoT — documented semantics)
_SENTINEL_S3_BUCKET = ""  # «S3 not configured — graceful degradation»
_SENTINEL_CONTEXT = ""  # «CONTEXT required — explicit validation upstream»

# ── Module-level cache (lazy-loaded) ────────────────────────────────────────
_defaults: dict[str, str] = {}
_loaded = False


# region FUNC__load_defaults
## @purpose  Load env_defaults from platform-env.yaml, cache in module-level dict
## @io       None (reads file) → None (populates _defaults)
## @complexity  O(N) where N = number of env_defaults entries
## @invariants
##   - Idempotent: second call is no-op (guarded by _loaded flag)
##   - Non-fatal on missing/parse errors — logs warning, uses fallbacks
def _load_defaults() -> None:
    """Load env_defaults from platform-env.yaml, cache in module-level dict.

    Searches for platform-env.yaml in the project root (current directory,
    then parent directories up to 3 levels). Falls back to fallback values
    if file not found or parse error.
    """
    global _defaults, _loaded
    if _loaded:
        return
    _loaded = True

    # Search paths: cwd → parent → grandparent (up to 3 levels up)
    search_dir = Path.cwd()
    yaml_path: Path | None = None
    for _ in range(4):
        candidate = search_dir / "platform-env.yaml"
        if candidate.is_file():
            yaml_path = candidate
            break
        if search_dir.parent == search_dir:
            break
        search_dir = search_dir.parent

    if yaml_path is None:
        # Also check relative to this script's location
        script_dir = Path(__file__).resolve().parent.parent.parent.parent  # core/
        candidate = script_dir.parent / "platform-env.yaml"
        if candidate.is_file():
            yaml_path = candidate

    if yaml_path is None:
        logger.warning("[IMP:7][platform_config] platform-env.yaml not found — using fallback values")
        return

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        env_defaults = data.get("env_defaults", {})
        if not isinstance(env_defaults, dict):
            logger.warning(
                "[IMP:7][platform_config] env_defaults section in %s is not a dict",
                yaml_path,
            )
            return
        _defaults = {str(k): str(v) for k, v in env_defaults.items()}
        logger.info(
            "[IMP:8][platform_config] Loaded %d defaults from %s",
            len(_defaults),
            yaml_path,
        )
    except (FileNotFoundError, yaml.YAMLError, OSError) as e:
        logger.warning(
            "[IMP:7][platform_config] Failed to load %s: %s — using fallback values",
            yaml_path,
            e,
        )


# endregion FUNC__load_defaults


# region FUNC_get_default
## @purpose  Get a default value by key, with fallback
## @io       ⇥ key: str, fallback: str → ⎋ str
## @complexity  O(1)
def get_default(key: str, fallback: str = "") -> str:
    """Get a default value by key from the cached env_defaults.

    Args:
        key: Environment variable name (e.g. "S3_REGION")
        fallback: Value to return if key not found in loaded defaults

    Returns:
        str: The default value, or fallback if not found
    """
    _load_defaults()
    return _defaults.get(key, fallback)


# endregion FUNC_get_default


# region TYPED ACCESSORS


# region FUNC_default_s3_region
## @purpose  Get default S3 region (SoT: ru-1)
## @io       None → ⎋ str
## @complexity  O(1)
def default_s3_region() -> str:
    """Get default S3 region.

    Returns default from platform-env.yaml env_defaults.S3_REGION,
    or fallback 'ru-1' if not found.
    """
    return get_default("S3_REGION", _FALLBACK_S3_REGION)


# endregion FUNC_default_s3_region


# region FUNC_default_s3_prefix
## @purpose  Get default S3 prefix for backups (SoT: platform/backups)
## @io       None → ⎋ str
## @complexity  O(1)
def default_s3_prefix() -> str:
    """Get default S3 prefix for backups.

    Returns default from platform-env.yaml env_defaults.S3_PREFIX,
    or fallback 'platform/backups' if not found.
    """
    return get_default("S3_PREFIX", _FALLBACK_S3_PREFIX)


# endregion FUNC_default_s3_prefix


# region FUNC_default_s3_bucket_sentinel
## @purpose  Get S3_BUCKET sentinel value — empty string signals graceful degradation
## @io       None → ⎋ "" (str)
## @complexity  O(1)
## @rationale  Python-код использует "" как sentinel «S3 не сконфигурирован».
##             В production S3_BUCKET всегда задаётся через secrets. Использование
##             test-bucket как fallback создаст риск: если secrets не загружены,
##             система начнёт писать в несуществующий бакет вместо graceful degradation.
##             Паттерн: if not bucket: logger.warning("S3 not configured"); return False.
def default_s3_bucket_sentinel() -> str:
    """Get S3_BUCKET sentinel value — empty string.

    Returns "" as sentinel for «S3 not configured — graceful degradation».
    NOT the SoT value "test-bucket", because Python consumers use
    "" to detect S3 absence and skip operations gracefully.
    """
    return _SENTINEL_S3_BUCKET


# endregion FUNC_default_s3_bucket_sentinel


# region FUNC_default_context
## @purpose  Get default CONTEXT value (SoT: platform-infra.yaml env_defaults.CONTEXT; без fallback — fail-visible)
## @io       None → ⎋ str
## @complexity  O(1)
## @invariants  Нет литерального fallback'а (DevPlan 116 B6 D4): при отсутствии
##              platform-env.yaml → "" (fail-visible), не "test".
def default_context() -> str:
    """Get default CONTEXT value.

    Returns default from platform-env.yaml env_defaults.CONTEXT (SoT:
    platform-infra.yaml env_defaults.CONTEXT), or "" if not found — fail-visible
    instead of the silent "test" lie (DevPlan 116 B6 T1, decision D4).
    """
    return get_default("CONTEXT", "")


# endregion FUNC_default_context


# region FUNC_default_context_sentinel
## @purpose  Get CONTEXT sentinel — empty string signals "must be provided explicitly"
## @io       None → ⎋ "" (str)
## @complexity  O(1)
## @rationale  Used by context_deployer.py where CONTEXT must be explicitly provided
##             (for security/validation). Empty sentinel forces upstream validation.
def default_context_sentinel() -> str:
    """Get CONTEXT sentinel — empty string.

    Returns "" as sentinel for «CONTEXT must be explicitly provided».
    Used by context_deployer.py for validation purposes.
    """
    return _SENTINEL_CONTEXT


# endregion FUNC_default_context_sentinel


# region FUNC_default_platform_context
## @purpose  Get default PLATFORM_CONTEXT value (SoT: personal)
## @io       None → ⎋ str
## @complexity  O(1)
def default_platform_context() -> str:
    """Get default PLATFORM_CONTEXT value.

    Returns default from platform-env.yaml env_defaults.PLATFORM_CONTEXT,
    or fallback 'personal' if not found.
    """
    return get_default("PLATFORM_CONTEXT", _FALLBACK_PLATFORM_CONTEXT)


# endregion FUNC_default_platform_context

# endregion TYPED ACCESSORS
