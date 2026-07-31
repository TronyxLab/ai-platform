# GREP_SUMMARY: test deploy delivery core-deliverer scp-deliver core-deploy rsync-exclude default-user-xml env runtime-artifact
# STRUCTURE: ▶ test_rsync_excludes_runtime_artifacts → import RSYNC_EXCLUDES_CORE (core_deliverer.py) + grep scp-deliver.sh delegation + core-deploy.yml → assert both excludes present in delivery paths
# region MODULE_CONTRACT
## @purpose  Validates rsync exclude patterns for runtime artifacts (T2):
##           core_deliverer.py RSYNC_EXCLUDES_CORE and core-deploy.yml both exclude
##           'default-user.xml' and '.env' from core/ rsync delivery.
## @scope    Static audit — imports the Python delivery module constant (no execution)
##           and reads the CI workflow YAML as text
## @invariants
##   - core_deliverer.py RSYNC_EXCLUDES_CORE must contain both exclude patterns
##   - core-deploy.yml must contain both exclude patterns in its core/ rsync step
##   - scp-deliver.sh facade must delegate to core_deliverer (excludes reachable in path)
##   - Existing excludes (.git, __pycache__, .pytest_cache, *.pyc) are preserved
## @rationale Runtime artifacts (default-user.xml, .env) must never be delivered to VPS via rsync.
##   DevPlan 108: вся rsync-оркестрация переехала из scp-deliver.sh в core_deliverer.py
##   (Strangler-Fig) — единственный источник правды excludes = константа RSYNC_EXCLUDES_CORE.
##   Explicit exclude list is deterministic and testable (D2: gitignore-filter rejected as too broad).
##   Acceptance criteria A3 + AC7: rsync manifests exclude default-user.xml and .env.
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.bootstrap.core_deliverer import RSYNC_EXCLUDES_CORE
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_SCP_DELIVER_SH = repo_root() / "core" / "internal" / "bootstrap" / "scp-deliver.sh"
_CORE_DEPLOY_YML = repo_root() / ".github" / "workflows" / "core-deploy.yml"


@pytest.mark.static_audit
def test_rsync_excludes_runtime_artifacts(caplog) -> None:
    """Assert both delivery paths exclude 'default-user.xml' and '.env' from core/ rsync.

    Acceptance criteria A3 + AC7 (DevPlan 108): RSYNC_EXCLUDES_CORE in core_deliverer.py
    (single source of truth for bootstrap rsync excludes) and core-deploy.yml (CI channel)
    both exclude the runtime artifacts.
    """
    # 🧪 TRAP[TEST] · 2026-07-31 · runtime-artifact excludes moved to core_deliverer.py (DevPlan 108)
    # · Regression: removal of --exclude=default-user.xml/.env from RSYNC_EXCLUDES_CORE delivers
    # ·   runtime artifacts to VPS (default-user.xml overwrites generated state; .env leaks secrets)
    # · Scenario: assert RSYNC_EXCLUDES_CORE membership + scp-deliver.sh delegation + core-deploy.yml
    # · Last fail: 2026-07-31 — HARD FAIL (excludes looked for in scp-deliver.sh text, now in Python)
    # · Remove if: rsync excludes move to a different delivery channel
    caplog.set_level(logging.DEBUG)

    required_excludes = [
        "default-user.xml",
        ".env",
    ]

    # ── core_deliverer.py (RSYNC_EXCLUDES_CORE — single source of truth, DevPlan 108) ──
    for exclude in required_excludes:
        has_exclude = f"--exclude={exclude}" in RSYNC_EXCLUDES_CORE
        logger.critical(
            "[IMP:9][test_delivery][python] RSYNC_EXCLUDES_CORE excludes '%s': %s",
            exclude,
            has_exclude,
        )
        assert has_exclude, (
            f"RSYNC_EXCLUDES_CORE missing --exclude '{exclude}' in core/ rsync. "
            f"Without it, {exclude} may be delivered to VPS."
        )
    logger.info("[IMP:8][test_delivery][python] RSYNC_EXCLUDES_CORE has all required excludes")

    # ── scp-deliver.sh facade → core_deliverer delegation (excludes reachable in path) ──
    assert _SCP_DELIVER_SH.is_file(), f"scp-deliver.sh not found: {_SCP_DELIVER_SH}"
    scp_content = _SCP_DELIVER_SH.read_text()
    has_delegation = "core_deliverer" in scp_content and "deliver" in scp_content
    logger.critical(
        "[IMP:9][test_delivery][scp] scp-deliver.sh delegates to core_deliverer deliver: %s",
        has_delegation,
    )
    assert has_delegation, (
        "scp-deliver.sh no longer delegates to core_deliverer deliver — the bootstrap "
        "rsync excludes in RSYNC_EXCLUDES_CORE would be unreachable in the delivery path."
    )

    # ── core-deploy.yml (CI channel) ─────────────────────────────────────
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
