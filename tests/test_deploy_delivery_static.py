# GREP_SUMMARY: test deploy delivery scp-deliver core-deploy rsync-exclude default-user-xml env runtime-artifact
# STRUCTURE: ▶ test_rsync_excludes_runtime_artifacts → grep scp-deliver.sh + core-deploy.yml for exclude patterns → assert both excludes present
# region MODULE_CONTRACT
## @purpose  Validates rsync exclude patterns for runtime artifacts (T2):
##           scp-deliver.sh and core-deploy.yml both exclude 'default-user.xml' and '.env'
##           from core/ rsync delivery.
## @scope    Static audit — reads shell script and workflow YAML as text
## @invariants
##   - Both files must contain both exclude patterns in their core/ rsync commands
##   - Existing excludes (.git, __pycache__, .pytest_cache, *.pyc) are preserved
## @rationale Runtime artifacts (default-user.xml, .env) must never be delivered to VPS via rsync.
##   Explicit exclude list is deterministic and testable (D2: gitignore-filter rejected as too broad).
##   Acceptance criterion A3: rsync manifests exclude default-user.xml and .env.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCP_DELIVER_SH = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "scp-deliver.sh"
_CORE_DEPLOY_YML = _PROJECT_ROOT / ".github" / "workflows" / "core-deploy.yml"


@pytest.mark.static_audit
def test_rsync_excludes_runtime_artifacts(caplog) -> None:
    """Assert both delivery files exclude 'default-user.xml' and '.env' from core/ rsync.

    Acceptance criterion A3: rsync manifests (scp-deliver.sh, core-deploy.yml) exclude both.
    """
    caplog.set_level(logging.DEBUG)

    required_excludes = [
        "default-user.xml",
        ".env",
    ]

    # ── scp-deliver.sh ────────────────────────────────────────────────────
    assert _SCP_DELIVER_SH.is_file(), f"scp-deliver.sh not found: {_SCP_DELIVER_SH}"
    scp_content = _SCP_DELIVER_SH.read_text()

    # Find the Phase 1 core/ rsync block: look for the specific rsync command
    for exclude in required_excludes:
        has_exclude = f"--exclude='{exclude}'" in scp_content or f'--exclude="{exclude}"' in scp_content
        logger.critical(
            "[IMP:9][test_delivery][scp] scp-deliver.sh excludes '%s': %s",
            exclude,
            has_exclude,
        )
        assert has_exclude, (
            f"scp-deliver.sh missing --exclude '{exclude}' in core/ rsync. "
            f"Without it, {exclude} may be delivered to VPS."
        )
    logger.info("[IMP:8][test_delivery][scp] scp-deliver.sh has all required excludes")

    # ── core-deploy.yml ───────────────────────────────────────────────────
    assert _CORE_DEPLOY_YML.is_file(), f"core-deploy.yml not found: {_CORE_DEPLOY_YML}"
    yml_content = _CORE_DEPLOY_YML.read_text()

    for exclude in required_excludes:
        has_exclude = f"'{exclude}'" in yml_content
        logger.critical(
            "[IMP:9][test_delivery][ci] core-deploy.yml excludes '%s': %s",
            exclude,
            has_exclude,
        )
        assert has_exclude, (
            f"core-deploy.yml missing --exclude '{exclude}' in core/ rsync step. "
            f"Without it, CI may deliver {exclude} to VPS."
        )
    logger.info("[IMP:8][test_delivery][ci] core-deploy.yml has all required excludes")

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
