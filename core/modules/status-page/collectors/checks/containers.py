# GREP_SUMMARY: status-page collectors containers check-container anti-recursion exit-code status-line
# STRUCTURE: ▶ ┌container dict┐ → ◇ name=="status-page"? → ⎋ None (anti-recursion)
#            → ◇ running+healthy → PASS → ◇ running+!healthy → WARN
#            → ◇ !running → exit_code parse (field|status_line) → PASS(0)|FAIL(>0) → ⎋ check dict
# region MODULE_CONTRACT
## @purpose  Container status check for status-page — extracted from collectors.py (DevPlan 170 W7-E2).
##           Pure function over status-metrics.json container entries.
## @scope    Consumed by collectors/aggregate.py (get_all_checks container phase)
## @invariants
##   - Anti-recursion: "status-page" container → None (excluded from self-checks)
##   - Running & healthy → PASS; running & not healthy → WARN
##   - Not running: exit_code=0 OR "Exited (0)" → PASS (oneshot/init completed); exit_code>0 → FAIL
## @rationale  DevPlan 170 W7-E2 — containers.py extracted verbatim from collectors.py (AC-G7).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from collectors.py
# endregion MODULE_CONTRACT

import re
from typing import cast

from collectors.config import ContainerEntry  # pyright: ignore[reportImplicitRelativeImport]

from .http import CheckResult


# region FUNC_check_container
def check_container(container: ContainerEntry) -> CheckResult | None:
    """Check a single container from status-metrics.json data. Returns check result.

    Status logic:
    - Running & healthy → PASS
    - Running & not healthy → WARN
    - Not running, exit_code=0 OR status_line contains "Exited (0)" → PASS (oneshot/init completed)
    - Not running, exit_code>0 OR status_line contains "Exited (non-zero)" → FAIL
    - Other non-running → FAIL
    """
    name = container.get("name", "unknown")  # Δ8: container_name → name
    running = container.get("running", False)
    healthy = container.get("healthy", False)
    exit_code = container.get("exit_code")
    status_line = container.get("status_line", "")

    # Anti-recursion: skip self
    if name == "status-page":
        return None

    if running and healthy:
        check_status = "PASS"
    elif running and not healthy:
        check_status = "WARN"
    elif not running:
        # Determine exit code: prefer explicit field, fall back to parsing status_line
        if exit_code is None:
            m = re.search(r"Exited\s*\((\d+)\)", cast("str", status_line))
            exit_code = int(m.group(1)) if m else None
        # Oneshot/init container that completed successfully → PASS, otherwise FAIL
        check_status = "PASS" if (exit_code is not None and exit_code == 0) else "FAIL"
    else:
        check_status = "FAIL"

    return {
        "target": name,
        "type": "container",
        "status": check_status,
        "running": running,
        "healthy": healthy,
        "exit_code": exit_code,
        "status_line": status_line,
        "error": None if check_status == "PASS" else f"status: {status_line}",
    }


# endregion FUNC_check_container
