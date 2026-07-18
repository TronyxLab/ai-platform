# GREP_SUMMARY: backup_config s3-config bucket context-detection env-vars timeweb-s3
# STRUCTURE: load env → detect_context(personal|corporate|project-{name}) → return dict
# region MODULE_CONTRACT
"""
Shared configuration module for backup-cron scripts (upload.py, retention.py, backup_monitor.py).

@purpose  Provide unified S3 configuration (endpoint, bucket, prefix, context)
          from environment variables. Used by all backup-cron Python scripts.
@scope    core/modules/backup-cron/scripts/; imported by upload.py, retention.py,
          and optionally by ~/.hermes/skills/backup-s3/backup_monitor.py (deployed on node).
@input    Environment variables: S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY,
          S3_BUCKET, S3_REGION, S3_PREFIX, PLATFORM_CONTEXT, NODE_NAME.
@output   dict with keys: endpoint_url, aws_access_key_id, aws_secret_access_key,
          bucket, region, prefix, context, node_name.
@invariants
  - All values validated: non-empty strings, no defaults for secrets
  - context detection: PLATFORM_CONTEXT env → "personal"|"corporate"|"project-{name}"
  - Fallback context: "personal" (safe default)
  - S3 endpoint always s3.timeweb.cloud (AR8 — only Timeweb S3)
  - Secrets must come from env, never hardcoded
@rationale Q: why separate config module?
          A: upload.py, retention.py, and backup_monitor.py share the same S3 config.
          Central config avoids duplication and ensures consistent context detection.
"""

import logging
import os
from typing import TypedDict

logger = logging.getLogger(__name__)


class BackupConfig(TypedDict):
    """TypedDict representing the backup S3 configuration."""

    endpoint_url: str
    aws_access_key_id: str
    aws_secret_access_key: str
    bucket: str
    region: str
    prefix: str
    context: str
    node_name: str


# endregion MODULE_CONTRACT

# region CONSTANTS

_DEFAULT_S3_ENDPOINT = "s3.timeweb.cloud"
_DEFAULT_S3_REGION = "us-east-1"
_DEFAULT_S3_PREFIX = "platform/backups"
_DEFAULT_CONTEXT = "personal"

# endregion CONSTANTS

# 💼 TRAP[BUSINESS] · 2026-06-11 · HI · AR8: единственное S3-хранилище — Timeweb S3, без fallback
# · Source: phase-06 requirements · Risk: при недоступности Timeweb S3 бэкапы не создаются;
#   нет репликации в другое облако; acceptable risk для non-production стадии


# region FUNC_get_backup_config
## @purpose  Load S3 backup configuration from environment variables with validation
## @io       None → BackupConfig dict
## @complexity 2
def get_backup_config() -> BackupConfig:
    """
    ▶ ┌env vars┐ → ◇ validate required (S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET) → ⊕ BackupConfig dict → ⎋ raise RuntimeError if missing

    Load backup S3 configuration from environment variables.

    Returns:
        dict with keys: endpoint_url, aws_access_key_id, aws_secret_access_key,
        bucket, region, prefix, context, node_name.

    Raises:
        RuntimeError: If required S3 credentials are missing.
    """
    logger.info("[IMP:7][backup_config][get] Loading backup configuration from environment")

    endpoint_url = os.environ.get("S3_ENDPOINT_URL", os.environ.get("S3_ENDPOINT", f"https://{_DEFAULT_S3_ENDPOINT}"))
    aws_access_key_id = os.environ.get("S3_ACCESS_KEY", os.environ.get("AWS_ACCESS_KEY_ID", ""))
    aws_secret_access_key = os.environ.get("S3_SECRET_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", ""))
    bucket = os.environ.get("S3_BUCKET", "")
    region = os.environ.get("S3_REGION", _DEFAULT_S3_REGION)
    prefix = os.environ.get("S3_PREFIX", _DEFAULT_S3_PREFIX)
    context = _detect_context()
    node_name = os.environ.get("NODE_NAME", "unknown")

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
        raise RuntimeError(msg)

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


# region FUNC_detect_context
## @purpose  Detect platform context from PLATFORM_CONTEXT env variable
## @io       None → str ("personal"|"corporate"|"project-{name}")
## @complexity 1
def _detect_context() -> str:
    """
    Detect platform context from PLATFORM_CONTEXT env variable.

    Returns "personal", "corporate", or "project-{name}". Defaults to "personal".
    """
    raw = os.environ.get("PLATFORM_CONTEXT", "").strip().lower()

    if raw in ("personal", "corporate"):
        logger.info("[IMP:7][backup_config][context] Detected context: %s", raw)
        return raw

    if raw.startswith("project-"):
        logger.info("[IMP:7][backup_config][context] Detected project context: %s", raw)
        return raw

    if raw:
        logger.warning(
            "[IMP:8][backup_config][context] Unknown PLATFORM_CONTEXT=%s, falling back to %s",
            raw,
            _DEFAULT_CONTEXT,
        )
    else:
        logger.info("[IMP:7][backup_config][context] PLATFORM_CONTEXT not set, defaulting to %s", _DEFAULT_CONTEXT)

    return _DEFAULT_CONTEXT


# endregion FUNC_detect_context
