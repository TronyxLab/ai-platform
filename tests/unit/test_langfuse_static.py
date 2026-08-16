# GREP_SUMMARY: test-langfuse-static static-audit s3-env contract-chains drift-gate langfuse docker-compose
# STRUCTURE: ▶ fixtures(langfuse_compose_path) → test_langfuse_s3_env_uses_contract_chains(◇ yaml.safe_load → 6 env chains vs CONTRACT_CHAINS → ⊕ no literals) → ⎋
# region MODULE_CONTRACT
## @purpose  Static audit of langfuse S3 environment variable chains — drift-gate for QF-2.
##           Ensures all 6 LANGFUSE_S3_EVENT_UPLOAD_* values use ${VAR:-default} interpolation
##           chains (contract from DevPlan 010 §3), not hardcoded literals.
## @scope    Single test: parses core/modules/langfuse/docker-compose.base.yml, compares
##           6 S3 env values against canonical contract chains. No Docker daemon required.
## @invariants   - All 6 values must match CONTRACT_CHAINS exactly; zero literals; @pytest.mark.static_audit; LDD mandatory
## @rationale   QF-2 was a CRITICAL regression not caught by any test. This drift-gate catches it.
## @changes — CREATED: 2026-07-15 | DevPlan 011 R3
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

CONTRACT_CHAINS = {
    "LANGFUSE_S3_EVENT_UPLOAD_BUCKET": "${LANGFUSE_S3_BUCKET:-${S3_BUCKET:-test-bucket}}",
    "LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT": "${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}",
    "LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID": "${S3_ACCESS_KEY:-${MINIO_ROOT_USER:-dummy}}",
    "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY": "${S3_SECRET_KEY:-${MINIO_ROOT_PASSWORD:-dummy}}",
    "LANGFUSE_S3_EVENT_UPLOAD_REGION": "${S3_REGION:-ru-1}",
    "LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE": "${LANGFUSE_S3_FORCE_PATH_STYLE:-true}",
}

_LANGFUSE_COMPOSE = (
    Path(__file__).resolve().parent / "../.." / "core" / "modules" / "langfuse" / "docker-compose.base.yml"
)


@pytest.mark.static_audit
@ldd_trajectory
def test_langfuse_s3_env_uses_contract_chains(caplog) -> None:
    caplog.set_level(logging.INFO)
    logger.info("[IMP:7] Parsing %s", _LANGFUSE_COMPOSE)
    assert pathlib.Path(_LANGFUSE_COMPOSE).is_file()
    with pathlib.Path(_LANGFUSE_COMPOSE).open(encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)
    env_config = compose_data["services"]["langfuse"].get("environment", {})
    failed = []
    for key, expected in CONTRACT_CHAINS.items():
        actual = env_config.get(key)
        if actual != expected:
            failed.append(f"{key}: expected '{expected}', got '{actual}'")
        elif "${" not in actual:
            failed.append(f"{key}: LITERAL '{actual}'")
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for r in list(caplog.records):
        if "[IMP:" in r.message and int(r.message.split("[IMP:")[1].split("]")[0]) >= 7:
            logger.info("%s", r.message)
    logger.info("--- END LDD TRAJECTORY ---")
    if not failed:
        logger.info("[IMP:9] all 6 S3 env values are contract chains")
    assert not failed, "S3 contract chain violations:\n" + "\n".join(failed)
