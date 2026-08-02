# GREP_SUMMARY: test healthcheck modules_healthcheck.py iterate-all-containers restart-loop detection module-interface dispatch python
# STRUCTURE: ▶ test_healthcheck_checks_all_containers → grep modules_healthcheck.py for YAML container iteration → assert present | ▶ test_healthcheck_detects_restart_loop → grep for State.Restarting + RestartCount + threshold → assert present
# region MODULE_CONTRACT
## @purpose  Validates modules healthcheck unification (DevPlan 083 + 118 E4):
##           (a) uses shared/module_interface for primary liveness check;
##           (b) still detects restart loops via State.Restarting and RestartCount>5 → FAIL (secondary).
##           DevPlan 118 E4: бизнес-логика перенесена из modules-healthcheck.sh в
##           modules_healthcheck.py (Python). Тесты проверяют PYTHON-модуль (shell — тонкий фасад).
## @scope    Static audit — reads Python module as text, no Docker required
## @invariants
##   - Python module must contain invoke (module_interface) for liveness dispatch
##   - Python module must contain State.Restarting and RestartCount inspection (threshold > 5)
##   - Shell facade must NOT contain business logic (thin facade, R5 negative)
## @rationale DRIFT-H7 replaced raw docker inspect with invoke_module_interface. Restart loop detection
##   is preserved as a SECONDARY check — independent of module healthcheck.sh liveness.
##   E4: проверки переориентированы на Python-имплементацию (R5 anti-survivorship).
## @changes 2026-08-02 | DevPlan 118 E4 — модуль-под-тестом заменён .sh → .py
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_HEALTHCHECK_PY = repo_root() / "core" / "internal" / "healthcheck" / "modules_healthcheck.py"
_HEALTHCHECK_SH = repo_root() / "core" / "internal" / "healthcheck" / "modules-healthcheck.sh"


@pytest.mark.static_audit
def test_healthcheck_checks_all_containers(caplog) -> None:
    """Assert modules_healthcheck.py iterates ALL container_name entries (no head -1).

    Acceptance criterion A6: all containers in a module are checked.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_PY.is_file(), f"modules_healthcheck.py not found: {_HEALTHCHECK_PY}"
    content = _HEALTHCHECK_PY.read_text()

    # ── Check 1: uses shared/module_interface for docker liveness (DRIFT-H7, E4) ──
    has_invoke = bool(
        re.search(r"invoke_module_interface\s*\(.*?['\"]healthcheck['\"].*?['\"]liveness['\"]", content, re.DOTALL)
    ) or bool(re.search(r"invoke_module_interface\(module, ['\"]healthcheck['\"], ['\"]liveness['\"]\)", content))
    logger.critical(
        "[IMP:9][test_healthcheck][all] module_interface invoke liveness present: %s",
        has_invoke,
    )
    assert has_invoke, "modules_healthcheck.py must use shared/module_interface.invoke for liveness (DRIFT-H7/E4)."

    # ── Check 2: no head -1 pattern (Python YAML-парсер вместо shell pipeline) ──
    has_pipeline_head = "head -1" in content or "|head -1" in content
    logger.critical(
        "[IMP:9][test_healthcheck][all] Pipeline `head -1` present: %s",
        has_pipeline_head,
    )
    assert not has_pipeline_head, "Python module must not contain shell head -1 pipeline."

    # ── Check 3: restart loop detection iterates all containers ──
    has_container_loop = "read_container_names" in content and "for container in" in content
    logger.critical(
        "[IMP:9][test_healthcheck][all] Container iteration present (restart detection): %s",
        has_container_loop,
    )
    assert has_container_loop, "modules_healthcheck.py must iterate all container names for restart loop detection."

    # ── R5 negative: shell facade must NOT carry business logic ──
    if _HEALTHCHECK_SH.is_file():
        sh_content = _HEALTHCHECK_SH.read_text()
        sh_code = [ln for ln in sh_content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        sh_code_text = "\n".join(sh_code)
        has_biz_in_shell = (
            "State.Restarting" in sh_code_text or "RestartCount" in sh_code_text or "install_type" in sh_code_text
        )
        logger.critical(
            "[IMP:9][test_healthcheck][all] Shell facade carries business logic: %s (R5 negative — must be thin)",
            has_biz_in_shell,
        )
        assert not has_biz_in_shell, "E4 R5: modules-healthcheck.sh must be a thin facade (logic moved to Python)."

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
    """Assert modules_healthcheck.py contains State.Restarting/RestartCount → FAIL handling.

    Acceptance criterion A6: restart looping container gives exit 1.
    """
    caplog.set_level(logging.DEBUG)

    assert _HEALTHCHECK_PY.is_file(), f"modules_healthcheck.py not found: {_HEALTHCHECK_PY}"
    content = _HEALTHCHECK_PY.read_text()

    # ── Check 1: State.Restarting is inspected ────────────────────────────
    has_restarting = "State.Restarting" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] State.Restarting inspected: %s",
        has_restarting,
    )
    assert has_restarting, (
        "modules_healthcheck.py must inspect {{.State.Restarting}} to detect restart loops. "
        "Without it, a restarting container shows as 'starting' → WARN instead of FAIL."
    )

    # ── Check 2: RestartCount is inspected ────────────────────────────────
    has_restart_count = "RestartCount" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] RestartCount inspected: %s",
        has_restart_count,
    )
    assert has_restart_count, (
        "modules_healthcheck.py must inspect {{.RestartCount}} to detect restart loops. "
        "Without it, a container with high restart count may show as 'healthy' → PASS."
    )

    # ── Check 3: restart loop threshold > 5 (канон) ───────────────────────
    has_threshold = "RESTART_LOOP_THRESHOLD = 5" in content or "> threshold" in content or "> 5" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] RestartCount threshold >5 present: %s",
        has_threshold,
    )
    assert has_threshold, "modules_healthcheck.py must encode the >5 restart-count threshold (канон)."

    # ── Check 4: restart loop leads to unhealthy return ───────────────────
    has_fail = "return False" in content and "restart loop" in content
    logger.critical(
        "[IMP:9][test_healthcheck][restart] Restart-loop FAIL path present: %s",
        has_fail,
    )
    assert has_fail, (
        "modules_healthcheck.py must return unhealthy (False) when restart loop is detected. "
        "Without it, the healthcheck exits 0 despite unhealthy containers."
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
