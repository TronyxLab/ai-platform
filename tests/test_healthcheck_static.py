# GREP_SUMMARY: test healthcheck modules-healthcheck iterate-all-containers restart-loop detection invoke_module_interface
# STRUCTURE: ▶ test_healthcheck_checks_all_containers → grep modules-healthcheck.sh for head -1 + mapfile → assert absent/present | ▶ test_healthcheck_detects_restart_loop → grep for State.Restarting + RestartCount → assert present
# region MODULE_CONTRACT
## @purpose  Validates modules-healthcheck.sh healthcheck unification (DevPlan 083):
##           (a) uses invoke_module_interface for primary liveness check (DRIFT-H7);
##           (b) still detects restart loops via State.Restarting and RestartCount>5 → FAIL (secondary).
## @scope    Static audit — reads shell script as text, no Docker required
## @invariants
##   - Script must contain `invoke_module_interface "$MODULE" healthcheck liveness` for docker modules
##   - Script must contain State.Restarting and RestartCount inspection leading to FAILED=1
## @rationale DRIFT-H7 replaced raw docker inspect with invoke_module_interface. Restart loop detection
##   is preserved as a SECONDARY check — independent of module healthcheck.sh liveness.
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_HEALTHCHECK_SH = repo_root() / "core" / "internal" / "healthcheck" / "modules-healthcheck.sh"


@pytest.mark.static_audit
def test_healthcheck_checks_all_containers(caplog) -> None:
    """Assert modules-healthcheck.sh iterates ALL container_name entries (no head -1).

    Acceptance criterion A6: all containers in a module are checked.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_SH.is_file(), f"modules-healthcheck.sh not found: {_HEALTHCHECK_SH}"
    content = _HEALTHCHECK_SH.read_text()

    # ── Check 1: uses invoke_module_interface for docker liveness (DRIFT-H7) ──
    has_invoke_module = bool(re.search(r"invoke_module_interface\s+\"\$MODULE\"\s+healthcheck\s+liveness", content))
    logger.critical(
        "[IMP:9][test_healthcheck][all] invoke_module_interface healthcheck liveness present: %s",
        has_invoke_module,
    )
    assert has_invoke_module, (
        "modules-healthcheck.sh must use invoke_module_interface for docker module liveness check (DRIFT-H7 fix)."
    )

    # ── Check 2: no head -1 in the container_name resolution pipeline ──
    has_pipeline_head = "| head -1" in content or "|head -1" in content
    logger.critical(
        "[IMP:9][test_healthcheck][all] Pipeline `head -1` present: %s",
        has_pipeline_head,
    )
    assert not has_pipeline_head, (
        "modules-healthcheck.sh uses `head -1` in a command pipeline to limit "
        "container_name entries. All containers must be checked."
    )
    logger.info("[IMP:8][test_healthcheck][all] No head -1 in pipeline — all containers checked")

    # ── Check 3: mapfile for restart loop detection (secondary check) ─────
    has_container_loop = "mapfile -t CONTAINER_NAMES" in content or "for CONTAINER_NAME in" in content
    logger.critical(
        "[IMP:9][test_healthcheck][all] Container iteration loop present (restart detection): %s",
        has_container_loop,
    )
    assert has_container_loop, (
        "modules-healthcheck.sh must iterate over all container names using mapfile or "
        "a for loop for restart loop detection."
    )

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


@pytest.mark.static_audit
def test_healthcheck_detects_restart_loop(caplog) -> None:
    """Assert modules-healthcheck.sh contains State.Restarting/RestartCount → FAIL handling.

    Acceptance criterion A6: restart looping container gives exit 1.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_SH.is_file(), f"modules-healthcheck.sh not found: {_HEALTHCHECK_SH}"
    content = _HEALTHCHECK_SH.read_text()

    # ── Check 1: State.Restarting is inspected ────────────────────────────
    has_restarting = "State.Restarting" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] State.Restarting inspected: %s",
        has_restarting,
    )
    assert has_restarting, (
        "modules-healthcheck.sh must inspect {{.State.Restarting}} to detect restart loops. "
        "Without it, a restarting container shows as 'starting' → WARN instead of FAIL."
    )

    # ── Check 2: RestartCount is inspected ────────────────────────────────
    has_restart_count = "RestartCount" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] RestartCount inspected: %s",
        has_restart_count,
    )
    assert has_restart_count, (
        "modules-healthcheck.sh must inspect {{.RestartCount}} to detect restart loops. "
        "Without it, a container with high restart count may show as 'healthy' → PASS."
    )

    # ── Check 3: restart loop leads to FAILED=1 ───────────────────────────
    has_fail = "FAILED=1" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] FAILED=1 assignment present: %s",
        has_fail,
    )
    assert has_fail, (
        "modules-healthcheck.sh must set FAILED=1 when restart loop is detected. "
        "Without it, the healthcheck exits 0 despite unhealthy containers."
    )

    # ── Check 4: restart loop FAIL is separate from unhealthy FAIL ────────
    has_restart_fail = content.count("FAILED=1") >= 2
    logger.critical(
        "[IMP:9][test_healthcheck][restart] Multiple FAILED=1 paths (unhealthy + restart loop): %s",
        has_restart_fail,
    )
    assert has_restart_fail, (
        "modules-healthcheck.sh must have at least two FAILED=1 paths: "
        "one for unhealthy health status, one for restart loop detection. "
        "This ensures restart loop is caught even when health status appears healthy."
    )

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
