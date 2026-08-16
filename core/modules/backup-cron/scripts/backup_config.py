# GREP_SUMMARY: backup_config s3-config bucket context-detection env-vars timeweb-s3
# STRUCTURE: load env → detect_context(personal|corporate|project-{name}) → return dict
# region MODULE_CONTRACT
"""
Shared configuration module for backup-cron scripts (upload.py, retention.py, backup_monitor.py).

@purpose  Provide unified S3 configuration (endpoint, bucket, prefix, context)
          from environment variables. Used by all backup-cron Python scripts
          and by s3_ssl_cache.py via upload.py --config-source ssl-cache.
          Two config levels: S3Config (base, 5 fields) for generic S3 operations,
          BackupConfig (extends S3Config, 8 fields) for backup-specific operations.
@scope    core/modules/backup-cron/scripts/; imported by upload.py, retention.py,
          and optionally by ~/.hermes/skills/backup-s3/backup_monitor.py (deployed on node).
@input    Environment variables: S3_ENDPOINT_URL (canonical), S3_ACCESS_KEY, S3_SECRET_KEY,
          S3_BUCKET, S3_REGION, S3_PREFIX, PLATFORM_CONTEXT, NODE_NAME.
@output   S3Config dict (5 fields: endpoint_url, aws_access_key_id, aws_secret_access_key,
          bucket, region) or BackupConfig dict (extends S3Config with prefix, context, node_name).
@invariants
  - All values validated: non-empty strings, no defaults for secrets
  - context detection: PLATFORM_CONTEXT env → "personal"|"corporate"|"project-{name}"
  - Fallback context: "personal" (safe default)
  - S3 endpoint always s3.timeweb.cloud (AR8 — only Timeweb S3)
  - Secrets must come from env, never hardcoded
  - S3Config returned by get_s3_config(); BackupConfig returned by get_backup_config()
@rationale Q: why separate config levels?
          A: upload.py originally only used for backup-cron with backup-specific prefix.
          New ssl-cache feature (DevPlan 024 Wave 1) uses raw S3 keys without backup prefix.
          S3Config provides the base fields for all S3 operations; BackupConfig adds
          backup-specific fields. Both load from the same env vars — zero config overhead.
@changes 2026-08-02 | DevPlan 119 C1 — удалена зависимость от core.internal.config
          (platform_config) — контейнерный модуль без core/internal в образе; inline
          _DEFAULT_S3_REGION="ru-1" согласован с docker-compose.base.yml.
"""

import logging
import os
from collections.abc import Mapping
from typing import TypedDict

logger = logging.getLogger(__name__)


# region CLASS_BackupConfigError
## @purpose  Доменный класс ошибки конфигурации backup-cron (170 W2-A2 B3): замена голых
##           RuntimeError (backup_config.py:127,202) — исключение с идентифицируемым типом
##           для except-обработчиков (retention.py main, тесты). Наследует Exception (НЕ
##           RuntimeError): доменные классы backup-cron вне PlatformError-иерархии (контейнерный
##           модуль, 0 зависимостей от core/internal — .importlinter D1-allowlist запрещает
##           modules→shared; локальный класс вместо shared.exceptions.ConfigValidationError).
## @scope    Только backup-cron/scripts: raise в get_backup_config()/get_s3_config(),
##           except в retention.py::main + тесты.
class BackupConfigError(Exception):
    """Raised when required S3 configuration is missing or invalid."""


# endregion CLASS_BackupConfigError


class S3Config(TypedDict):
    """Base S3 configuration with common fields for all S3 operations."""

    endpoint_url: str
    aws_access_key_id: str
    aws_secret_access_key: str
    bucket: str
    region: str


class BackupConfig(S3Config):
    """Backup-specific S3 configuration extending base S3Config.

    Adds backup-specific fields: prefix, context, node_name.
    """

    prefix: str
    context: str
    node_name: str


# endregion MODULE_CONTRACT

# region CONSTANTS

_DEFAULT_S3_ENDPOINT_URL = "s3.timeweb.cloud"
# DevPlan 119 C1 (AC-C1.2): inline default вместо platform_config.default_s3_region() —
# core.internal.config отсутствует в backup-cron образе (контейнерный модуль, 0 зависимостей
# от core/internal). Значение согласовано с docker-compose.base.yml (${S3_REGION:-ru-1}).
_DEFAULT_S3_REGION = "ru-1"
_DEFAULT_S3_PREFIX = "platform/backups"
_DEFAULT_PLATFORM_CONTEXT = "personal"

# endregion CONSTANTS

# 💼 TRAP[BUSINESS] · 2026-06-11 · HI · AR8: единственное S3-хранилище — Timeweb S3, без fallback
# · Source: phase-06 requirements · Risk: при недоступности Timeweb S3 бэкапы не создаются;
#   нет репликации в другое облако; acceptable risk для non-production стадии


# region FUNC_get_backup_config
## @purpose  Load S3 backup configuration from environment variables with validation
## @io       ⇥ env: Mapping[str, str] | None (None = os.environ, DI для тестов) → ⎋ BackupConfig dict
## @complexity 2
def get_backup_config(env: Mapping[str, str] | None = None) -> BackupConfig:
    """
    ▶ ┌env vars┐ → ◇ validate required (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET) → ⊕ BackupConfig dict → ⎋ raise BackupConfigError if missing

    Load backup S3 configuration from environment variables.

    Args:
        env: Optional mapping override (DI — DevPlan 160 W4e). None = os.environ.

    Returns:
        dict with keys: endpoint_url, aws_access_key_id, aws_secret_access_key,
        bucket, region, prefix, context, node_name.

    Raises:
        BackupConfigError: If required S3 credentials are missing.
    """
    logger.info("[IMP:7][backup_config][get] Loading backup configuration from environment")

    source: Mapping[str, str] = os.environ if env is None else env
    endpoint_url = source.get("S3_ENDPOINT_URL", f"https://{_DEFAULT_S3_ENDPOINT_URL}")
    aws_access_key_id = source.get("S3_ACCESS_KEY", "")
    aws_secret_access_key = source.get("S3_SECRET_KEY", "")
    bucket = source.get("S3_BUCKET", "")
    region = source.get("S3_REGION", _DEFAULT_S3_REGION)
    prefix = source.get("S3_PREFIX", _DEFAULT_S3_PREFIX)
    context = _detect_context(source)
    node_name = source.get("NODE_NAME", "unknown")

    # Validate required vars
    missing: list[str] = []
    if not aws_access_key_id:
        missing.append("S3_ACCESS_KEY")
    if not aws_secret_access_key:
        missing.append("S3_SECRET_KEY")
    if not bucket:
        missing.append("S3_BUCKET")

    if missing:
        msg = f"[IMP:9][backup_config][validate] Missing required S3 environment variables: {', '.join(missing)}"
        logger.critical(msg)
        raise BackupConfigError(msg)

    logger.info(
        "[IMP:8][backup_config][get] Config loaded: endpoint=%s bucket=%s region=%s prefix=%s context=%s",
        endpoint_url,
        bucket,
        region,
        prefix,
        context,
    )

    return {
        "endpoint_url": endpoint_url,
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "bucket": bucket,
        "region": region,
        "prefix": prefix,
        "context": context,
        "node_name": node_name,
    }


# endregion FUNC_get_backup_config


# region FUNC_get_s3_config
## @purpose  Load base S3 configuration (no backup-specific fields) from environment variables
## @io       ⇥ env: Mapping[str, str] | None (None = os.environ, DI для тестов) → ⎋ S3Config dict
## @complexity 2
## @rationale  Used by upload.py with --config-source ssl-cache. Does NOT include prefix,
##             context, or node_name — ssl-cache uses absolute S3 keys like
##             platform/ssl-certs/<domain\>/fullchain.pem (no backup prefix).
## @invariants
##   - Same env vars as get_backup_config() — zero config overhead
##   - Validates S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET (same required vars)
##   - Returns S3Config (5 fields) — no backup-specific fields
def get_s3_config(env: Mapping[str, str] | None = None) -> S3Config:
    """
    ▶ ┌env vars┐ → ◇ validate required → ⊕ S3Config dict → ⎋ raise BackupConfigError if missing

    Load base S3 configuration from environment variables.
    Returns only S3 connection fields (no backup-specific prefix/context/node_name).

    Args:
        env: Optional mapping override (DI — DevPlan 160 W4e). None = os.environ.

    Returns:
        dict with keys: endpoint_url, aws_access_key_id, aws_secret_access_key,
        bucket, region.

    Raises:
        BackupConfigError: If required S3 credentials are missing.
    """
    logger.info("[IMP:7][s3_config][get] Loading S3 configuration from environment")

    source: Mapping[str, str] = os.environ if env is None else env
    endpoint_url = source.get("S3_ENDPOINT_URL", f"https://{_DEFAULT_S3_ENDPOINT_URL}")
    aws_access_key_id = source.get("S3_ACCESS_KEY", "")
    aws_secret_access_key = source.get("S3_SECRET_KEY", "")
    bucket = source.get("S3_BUCKET", "")
    region = source.get("S3_REGION", _DEFAULT_S3_REGION)

    # Validate required vars (same as get_backup_config)
    missing: list[str] = []
    if not aws_access_key_id:
        missing.append("S3_ACCESS_KEY")
    if not aws_secret_access_key:
        missing.append("S3_SECRET_KEY")
    if not bucket:
        missing.append("S3_BUCKET")

    if missing:
        msg = f"[IMP:9][s3_config][validate] Missing required S3 environment variables: {', '.join(missing)}"
        logger.critical(msg)
        raise BackupConfigError(msg)

    logger.info(
        "[IMP:8][s3_config][get] Config loaded: endpoint=%s bucket=%s region=%s",
        endpoint_url,
        bucket,
        region,
    )

    return {
        "endpoint_url": endpoint_url,
        "aws_access_key_id": aws_access_key_id,
        "aws_secret_access_key": aws_secret_access_key,
        "bucket": bucket,
        "region": region,
    }


# endregion FUNC_get_s3_config


# region FUNC_detect_context
## @purpose  Detect platform context from PLATFORM_CONTEXT env variable
## @io       ⇥ env: Mapping[str, str] (источник; os.environ если не передан) → ⎋ str ("personal"|"corporate"|"project-{name}")
## @complexity 1
def _detect_context(env: Mapping[str, str] | None = None) -> str:
    """
    Detect platform context from PLATFORM_CONTEXT env variable.

    Returns "personal", "corporate", or "project-{name}". Defaults to "personal".
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("PLATFORM_CONTEXT", "").strip().lower()

    if raw in {"personal", "corporate"}:
        logger.info("[IMP:7][backup_config][context] Detected context: %s", raw)
        return raw

    if raw.startswith("project-"):
        logger.info("[IMP:7][backup_config][context] Detected project context: %s", raw)
        return raw

    if raw:
        logger.warning(
            "[IMP:8][backup_config][context] Unknown PLATFORM_CONTEXT=%s, falling back to %s",
            raw,
            _DEFAULT_PLATFORM_CONTEXT,
        )
    else:
        logger.info(
            "[IMP:7][backup_config][context] PLATFORM_CONTEXT not set, defaulting to %s", _DEFAULT_PLATFORM_CONTEXT
        )

    return _DEFAULT_PLATFORM_CONTEXT


# endregion FUNC_detect_context
