# GREP_SUMMARY: test deploy delivery core-deliverer scp-deliver core-deploy rsync-exclude default-user-xml env runtime-artifact grep-gate ci-deliver
# STRUCTURE: ▶ test_rsync_excludes_runtime_artifacts → import RSYNC_EXCLUDES_CORE (core_deliverer.py) + grep scp-deliver.sh delegation + core-deploy.yml → assert module-call ci-deliver (single-owner excludes, REF-0112) + NEGATIVE no inline rsync
# region MODULE_CONTRACT
## @purpose  Validates rsync exclude patterns for runtime artifacts (T2) + REF-0112 single-owner
##           exclude contract: core_deliverer.py RSYNC_EXCLUDES_CORE owns BOTH delivery channels;
##           core-deploy.yml delivers via module call `ci-deliver` (no inline rsync).
## @scope    Static audit — imports the Python delivery module constant (no execution)
##           and reads the CI workflow YAML as text
## @invariants
##   - core_deliverer.py RSYNC_EXCLUDES_CORE must contain runtime artifacts (.env,
##     default-user.xml) AND prod-tree hygiene (.git, .pytest_cache, docker-compose.test.yml)
##   - core-deploy.yml must invoke `core.internal.bootstrap.core_deliverer` with `ci-deliver`
##     (excludes reachable in path through module constants — REF-0112)
##   - core-deploy.yml must NOT contain inline `rsync -avz --delete` (divergent exclude-set bug)
##   - scp-deliver.sh facade must delegate to core_deliverer (excludes reachable in path)
## @rationale Runtime artifacts (default-user.xml, .env) must never be delivered to VPS via rsync.
##   DevPlan 108: вся rsync-оркестрация переехала из scp-deliver.sh в core_deliverer.py.
##   REF-0112: CI-канал больше НЕ дублирует exclude-логику shell'ом — раньше CI-rsync и
##   core_deliverer тянули РАЗНЫЕ наборы в один prod-tree (--delete): 13 docker-compose.test.yml
##   + .pytest_cache попадали в /opt/platform/core основным каналом. Один owner = константы модуля.
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
    """Assert single-owner excludes + module-call CI channel (REF-0112).

    Acceptance criteria A3 + AC7 (DevPlan 108) + REF-0112: RSYNC_EXCLUDES_CORE in
    core_deliverer.py is the single source of truth for BOTH channels; core-deploy.yml
    invokes `ci-deliver` instead of duplicating rsync/exclude logic inline.
    """
    # 🧪 TRAP[TEST] · 2026-07-31 · runtime-artifact excludes moved to core_deliverer.py (DevPlan 108)
    # · Regression: removal of --exclude=default-user.xml/.env from RSYNC_EXCLUDES_CORE delivers
    # ·   runtime artifacts to VPS (default-user.xml overwrites generated state; .env leaks secrets)
    # · Scenario: assert RSYNC_EXCLUDES_CORE membership + scp-deliver.sh delegation + core-deploy.yml
    # · Last fail: 2026-07-31 — HARD FAIL (excludes looked for in scp-deliver.sh text, now in Python)
    # · 2026-08-25 · REF-0112 · CI-channel section rewritten: text-excludes assertion → module-call
    # ·   grep-gate (inline-rsync с дивергентным exclude-set'ом = исходный баг REF-0112)
    # · Remove if: rsync excludes move to a different delivery channel
    caplog.set_level(logging.DEBUG)

    required_excludes = [
        "default-user.xml",
        ".env",
        ".git",
        ".pytest_cache",
        "docker-compose.test.yml",
    ]

    # ── core_deliverer.py (RSYNC_EXCLUDES_CORE — SINGLE OWNER both channels, REF-0112) ──
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

    # ── core-deploy.yml (CI channel — REF-0112 grep-gate: module call, no inline rsync) ──
    assert _CORE_DEPLOY_YML.is_file(), f"core-deploy.yml not found: {_CORE_DEPLOY_YML}"
    yml_content = _CORE_DEPLOY_YML.read_text()

    # Позитив: файловая фаза CI — модульный вызов deliverer (excludes достижимы через константу)
    has_module_call = "core.internal.bootstrap.core_deliverer" in yml_content and "ci-deliver" in yml_content
    logger.critical(
        "[IMP:9][test_delivery][ci] core-deploy.yml invokes core_deliverer ci-deliver: %s",
        has_module_call,
    )
    assert has_module_call, (
        "core-deploy.yml must deliver files via 'python3 -m "
        "core.internal.bootstrap.core_deliverer ci-deliver' (REF-0112 single-owner excludes). "
        "Inline rsync in the workflow re-introduces the divergent exclude-set bug."
    )

    # Негатив (R5): исходный вход бага REF-0112 — inline `rsync -avz --delete` в workflow
    has_inline_delete_rsync = "rsync -avz --delete" in yml_content
    logger.critical(
        "[IMP:9][test_delivery][ci] core-deploy.yml free of inline --delete-rsync: %s",
        not has_inline_delete_rsync,
    )
    assert not has_inline_delete_rsync, (
        "core-deploy.yml contains inline 'rsync -avz --delete' — divergent exclude-set channel "
        "(REF-0112 regression): file delivery must go through core_deliverer ci-deliver."
    )
    logger.info("[IMP:8][test_delivery][ci] core-deploy.yml module-call contract verified")

    # LDD trajectory
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
