#!/usr/bin/env python3
# GREP_SUMMARY: platform_config, config-facade, defaults, SoT, S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT, PLATFORM_CONTEXT, fail-visible, PLATFORM_ROOT
# STRUCTURE: ▶ load platform-infra.yaml (PLATFORM_ROOT env → script-relative root) → env_defaults dict → ◇ typed accessors → ⎋ get_default(key)
# region MODULE_CONTRACT
## @purpose  Единый Python-фасад для чтения default-значений из platform-infra.yaml (SoT).
##           Все consumers платформы получают default'ы только через этот модуль.
## @scope    Импортируется backup_config.py, s3_ssl_cache.py, cert_orchestrator.py,
##           preflight.py, docker_orchestrator.py, context_deployer.py
## @invariants
##   - Единственный Source of Truth для default-значений в Python-коде
##   - Загружает platform-infra.yaml (SoT, env_defaults секция) при первом импорте
##     (lazy-load с кэшированием; DevPlan 117 D23 — НЕ generated platform-env.yaml)
##   - Все accessors возвращают str; числовые значения — ответственность вызывающего
##   - ЛИТЕРАЛЬНЫХ fallback'ов НЕТ (fail-visible): отсутствие
##     platform-infra.yaml → "" + громкий WARNING
##   - Path-резолвинг: (1) env PLATFORM_ROOT → Path(PLATFORM_ROOT)/core/platform-infra.yaml;
##     (2) script-relative корень репо (parents[3]/core/) — без cwd-эвристики
##   - default_s3_bucket_sentinel() возвращает "" с явной семантикой sentinel
##     («S3 не сконфигурирован — graceful degradation»)
##   - default_context_sentinel() возвращает "" с семантикой валидационного sentinel
##     («CONTEXT обязателен — требуй явного указания»)
## @rationale Устраняет класс дрейфа «SoT обновлён, consumers — нет».
##            Fallback-копий SoT нет — fail-visible вместо тихой лжи. Cwd-эвристика
##            заменена каноническим резолвингом (PLATFORM_ROOT env → script-relative
##            корень репо). Чтение — из SoT platform-infra.yaml: единый loader
##            устраняет расхождение SoT↔generated.
## @changes   CREATED: 2026-07-26 · DevPlan 037 — config defaults unification
##            2026-08-01 · DevPlan 116 B5 T8 — 4 fallback-константы удалены (D2, fail-visible);
##                       cwd-эвристика удалена; accessors без fallback-аргумента
##            2026-08-01 · DevPlan 117 D23 — чтение platform-env.yaml → platform-infra.yaml (SoT)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)

# Sentinel values (не из SoT — документированная семантика ""):
#   "" для S3_BUCKET = «S3 не сконфигурирован — graceful degradation»
#   "" для CONTEXT   = «CONTEXT обязателен — explicit validation upstream»
_SENTINEL_S3_BUCKET = ""
_SENTINEL_CONTEXT = ""

# ── Module-level cache (lazy-loaded) ────────────────────────────────────────
_defaults: dict[str, str] = {}
_loaded = False


# region FUNC__load_defaults
## @purpose  Load env_defaults from platform-infra.yaml (SoT, DevPlan 117 D23), cache in module-level dict
## @io       None (reads file) → None (populates _defaults)
## @complexity  O(N) where N = number of env_defaults entries
## @invariants
##   - Idempotent: second call is no-op (guarded by _loaded flag)
##   - Читает platform-infra.yaml (SoT), НЕ generated platform-env.yaml:
##     генерированная копия может расходиться с SoT; единый loader устраняет источник дрейфа
##   - Path-резолвинг: (1) PLATFORM_ROOT env → Path(PLATFORM_ROOT)/core/platform-infra.yaml;
##     (2) script-relative корень репо; без cwd-эвристики
##   - Non-fatal on missing/parse errors — logs громкий WARNING, defaults остаются "" (fail-visible)
def _load_defaults() -> None:
    """Load env_defaults from platform-infra.yaml (SoT), cache in module-level dict.

    Чтение — из канонического platform-infra.yaml (env_defaults секция). Path-резолвинг:
    (1) env PLATFORM_ROOT → Path(PLATFORM_ROOT)/core/platform-infra.yaml (VPS layout:
    /opt/platform/core/platform-infra.yaml);
    (2) script-relative корень репо (core/internal/config/ → 4 уровня вверх → repo_root/core/).
    При отсутствии файла — "" (fail-visible).
    """
    global _defaults, _loaded
    if _loaded:
        return
    _loaded = True

    yaml_path: Path | None = None

    # (1) env PLATFORM_ROOT — канонический override (тесты/деплой задают явно)
    platform_root = os.environ.get("PLATFORM_ROOT")
    if platform_root:
        candidate = Path(platform_root) / "core" / "platform-infra.yaml"
        if candidate.is_file():
            yaml_path = candidate

    # (2) script-relative: core/internal/config/platform_config.py → parents[3] = корень репо
    if yaml_path is None:
        repo_root = Path(__file__).resolve().parents[3]
        candidate = repo_root / "core" / "platform-infra.yaml"
        if candidate.is_file():
            yaml_path = candidate

    if yaml_path is None:
        logger.warning(
            "[IMP:7][platform_config] platform-infra.yaml not found (PLATFORM_ROOT=%s, script-relative) — "
            "defaults = '' (fail-visible, D2)",
            platform_root or "<unset>",
        )
        return

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        with Path(yaml_path).open(encoding="utf-8") as f:
            # W11: yaml.safe_load returns Any → cast to payload boundary
            data = cast(dict[str, object] | None, yaml.safe_load(f)) or {}
        env_defaults: object = data.get("env_defaults", {})
        if not isinstance(env_defaults, dict):
            logger.warning(
                "[IMP:7][platform_config] env_defaults section in %s is not a dict",
                yaml_path,
            )
            return
        # W11: isinstance-narrowed dict is dict[Unknown, Unknown] → cast typed boundary
        _defaults = {str(k): str(v) for k, v in cast(dict[str, object], env_defaults).items()}
        logger.info(
            "[IMP:8][platform_config] Loaded %d defaults from %s",
            len(_defaults),
            yaml_path,
        )
    except (FileNotFoundError, yaml.YAMLError, OSError) as e:
        logger.warning(
            "[IMP:7][platform_config] Failed to load %s: %s — defaults = '' (fail-visible, D2)",
            yaml_path,
            e,
        )


# endregion FUNC__load_defaults


# region FUNC_get_default
## @purpose  Get a default value by key (без литерального fallback — "" если отсутствует)
## @io       ⇥ key: str → ⎋ str
## @complexity  O(1)
def get_default(key: str) -> str:
    """Get a default value by key from the cached env_defaults.

    Args:
        key: Environment variable name (e.g. "S3_REGION")

    Returns:
        str: The default value, or "" if not found (fail-visible — НЕ литеральный fallback)
    """
    _load_defaults()
    return _defaults.get(key, "")


# endregion FUNC_get_default


# region TYPED ACCESSORS


# region FUNC_default_s3_region
## @purpose  Get default S3 region (SoT: platform-infra.yaml env_defaults.S3_REGION)
## @io       None → ⎋ str ("" при отсутствии файла — fail-visible)
## @complexity  O(1)
def default_s3_region() -> str:
    """Get default S3 region.

    Returns default from platform-env.yaml env_defaults.S3_REGION,
    or "" if not found (fail-visible — без литерального fallback).
    """
    return get_default("S3_REGION")


# endregion FUNC_default_s3_region


# region FUNC_default_s3_prefix
## @purpose  Get default S3 prefix for backups (SoT: platform-infra.yaml env_defaults.S3_PREFIX)
## @io       None → ⎋ str ("" при отсутствии файла — fail-visible)
## @complexity  O(1)
def default_s3_prefix() -> str:
    """Get default S3 prefix for backups.

    Returns default from platform-env.yaml env_defaults.S3_PREFIX,
    or "" if not found (fail-visible — без литерального fallback).
    """
    return get_default("S3_PREFIX")


# endregion FUNC_default_s3_prefix


# region FUNC_default_s3_bucket_sentinel
## @purpose  Get S3_BUCKET sentinel value — empty string signals graceful degradation
## @io       None → ⎋ "" (str)
## @complexity  O(1)
## @rationale  Python-код использует "" как sentinel «S3 не сконфигурирован».
##             Паттерн: if not bucket: logger.warning("S3 not configured"); return False.
##             Это НЕ fallback SoT — документированная sentinel-семантика (T8.1).
def default_s3_bucket_sentinel() -> str:
    """Get S3_BUCKET sentinel value — empty string.

    Returns "" as sentinel for «S3 not configured — graceful degradation».
    NOT a literal SoT copy — Python consumers detect S3 absence via "" and skip gracefully.
    """
    return _SENTINEL_S3_BUCKET


# endregion FUNC_default_s3_bucket_sentinel


# region FUNC_default_context
## @purpose  Get default CONTEXT value (SoT: platform-infra.yaml env_defaults.CONTEXT; без fallback — fail-visible)
## @io       None → ⎋ str
## @complexity  O(1)
## @invariants  Нет литерального fallback'а: при отсутствии
##              platform-env.yaml → "" (fail-visible), не "test".
def default_context() -> str:
    """Get default CONTEXT value.

    Returns default from platform-env.yaml env_defaults.CONTEXT (SoT:
    platform-infra.yaml env_defaults.CONTEXT), or "" if not found — fail-visible
    instead of the silent "test" lie.
    """
    return get_default("CONTEXT")


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
## @purpose  Get default PLATFORM_CONTEXT value (SoT: platform-infra.yaml env_defaults.PLATFORM_CONTEXT)
## @io       None → ⎋ str ("" при отсутствии файла — fail-visible)
## @complexity  O(1)
## @invariants  Без литерального fallback'а — fail-visible.
##              Потребителей вне platform_config нет.
def default_platform_context() -> str:
    """Get default PLATFORM_CONTEXT value.

    Returns default from platform-env.yaml env_defaults.PLATFORM_CONTEXT,
    or "" if not found (fail-visible — без литерального fallback).
    """
    return get_default("PLATFORM_CONTEXT")


# endregion FUNC_default_platform_context

# endregion TYPED ACCESSORS
