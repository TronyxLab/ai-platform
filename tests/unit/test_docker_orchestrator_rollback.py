"""
# GREP_SUMMARY: test_rollback, transactional, deploy_docker_group, atomic, audit-trail, W5-E1
# STRUCTURE: ▶ mock drain+run+healthcheck → ◇ deploy_docker_group (failures) → ⊕ assert compose down all → ⊕ audit IMP:9 → ⎋ assert rolled_back
# region MODULE_CONTRACT
## @purpose  TDD для W5-E1: transactional rollback в deploy_docker_group.
## @scope    Mock drain-функций подставляют controlled failure results,
##           проверяет docker compose down на всех siblings + audit record.
## @rationale DevPlan 039 W5-E1 AC-1: atomic success-or-rollback (P05 ERROR_HANDLING).
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import docker_orchestrator as dorch

# ═══════════════════════════════════════════════════════════════════
# Helper: call deploy_docker_group with mocked drain + os.fork + subprocess
# ═══════════════════════════════════════════════════════════════════


def _call_group_deploy(
    entries: list[str],
    deploy_results: dict[str, bool],
    modules_dir: str,
    compose_down_calls: list[str],
) -> tuple:
    """Mock all externalities to test deploy_docker_group rollback logic.

    Creates fake compose.yaml files for each module so _resolve_compose_file
    finds them (needed for rollback's docker compose down).
    """
    # ── Create compose.yaml files for each module ──
    for entry in entries:
        name = entry.split(":")[0]
        mod_dir = Path(modules_dir) / name
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "compose.yaml").write_text(f"services:\n  {name}:\n    image: test/{name}:latest\n")
    # ── Mock subprocess.run (capture compose down) ──

    def _fake_run(cmd, *args, **kwargs):
        cmd_str = " ".join(c for c in cmd if isinstance(c, str))
        if "compose" in cmd_str and "down" in cmd_str:
            compose_down_calls.append(cmd_str)
        return mock.MagicMock(returncode=0, stdout="", stderr="", spec=subprocess.CompletedProcess)

    # ── Mock os.fork ──
    def _fake_fork():
        # Return non-zero PID (parent path), child will never run
        return 12345

    # Build controlled drain results
    # _drain_completed_count is called in the while loop (slot-waiter).
    # _drain_all_count is called after loop to collect remaining.
    # Each call pops entries from the list.
    drain_queue: list[dict] = []
    for entry in entries:
        name = entry.split(":")[0]
        success = deploy_results.get(name, True)
        drain_queue.append({"name": name, "success": success})

    def _fake_drain_completed(pids, pid_to_name):
        if not drain_queue:
            # No more expected results — clear pids to break the while loop
            pids.clear()
            pid_to_name.clear()
            return (0, 0, [])
        item = drain_queue.pop(0)
        # Simulate PID removal (the real function uses os.waitpid with WNOHANG)
        if pids:
            pids.pop(0)
            if pids and pids[0] in pid_to_name:
                del pid_to_name[pids[0]]
        if item["success"]:
            return (1, 0, [])
        return (0, 1, [item["name"]])

    def _fake_drain_all(pids, pid_to_name):
        d, f, fn = 0, 0, []
        for item in drain_queue:
            if item["success"]:
                d += 1
            else:
                f += 1
                fn.append(item["name"])
        drain_queue.clear()
        pids.clear()
        pid_to_name.clear()
        return (d, f, fn)

    # Mock healthcheck (skip)

    def _fake_hc(*args, **kwargs):
        return True

    with (
        mock.patch.object(subprocess, "run", side_effect=_fake_run),
        mock.patch.object(os, "fork", side_effect=_fake_fork),
        mock.patch.object(os, "waitpid", return_value=(0, 0)),
        mock.patch.object(dorch, "_drain_completed_count", side_effect=_fake_drain_completed),
        mock.patch.object(dorch, "_drain_all_count", side_effect=_fake_drain_all),
        mock.patch.object(dorch, "run_healthcheck", side_effect=_fake_hc),
    ):
        return dorch.deploy_docker_group(
            entries=entries,
            modules_dir=modules_dir,
            parallel_limit=1,
        )


# ═══════════════════════════════════════════════════════════════════
# W5-E1 Tests
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_rollback_on_failure
## 🧪 TRAP[TEST] · W5-E1 rollback · Scenario: 1 failure in 3 → compose down all 3
def test_rollback_on_failure(tmp_path):
    """W5-E1: 1 failure → docker compose down on ALL siblings + rolled_back list."""
    mod_base = tmp_path / "modules"
    mod_base.mkdir(parents=True)
    entries = ["mod_a:", "mod_b:", "mod_c:"]
    compose_down_calls: list[str] = []

    result = _call_group_deploy(
        entries=entries,
        deploy_results={"mod_a": True, "mod_b": False, "mod_c": True},
        modules_dir=str(mod_base),
        compose_down_calls=compose_down_calls,
    )

    deployed, failed, failed_names = result[:3]
    assert deployed == 2, f"deployed={deployed}, failed={failed}, failed_names={failed_names}"
    assert failed == 1
    assert "mod_b" in failed_names

    # W5-E1: return tuple must include rolled_back list
    assert len(result) >= 4, f"Expected 4-tuple, got {len(result)}-tuple: {result}"
    rolled_back = result[3]
    assert sorted(rolled_back) == sorted(["mod_a", "mod_b", "mod_c"]), (
        f"All 3 modules should be in rolled_back: {rolled_back}"
    )

    # compose down called for all 3 modules
    assert len(compose_down_calls) == 3, (
        f"Expected 3 compose down calls, got {len(compose_down_calls)}: {compose_down_calls}"
    )
    for m in ["mod_a", "mod_b", "mod_c"]:
        assert any(m in c for c in compose_down_calls), f"Missing {m} in {compose_down_calls}"


# endregion FUNC_test_rollback_on_failure


# region FUNC_test_no_rollback_on_success
## 🧪 TRAP[TEST] · W5-E1 success · Scenario: all succeed → no compose down
def test_no_rollback_on_success(tmp_path):
    """W5-E1: all success → no rollback, no compose down calls."""
    mod_base = tmp_path / "modules"
    mod_base.mkdir(parents=True)
    entries = ["mod_a:", "mod_b:"]
    compose_down_calls: list[str] = []

    result = _call_group_deploy(
        entries=entries,
        deploy_results={"mod_a": True, "mod_b": True},
        modules_dir=str(mod_base),
        compose_down_calls=compose_down_calls,
    )

    deployed, failed, _ = result[:3]
    assert deployed == 2
    assert failed == 0
    assert len(compose_down_calls) == 0, f"No compose down on success: {compose_down_calls}"


# endregion FUNC_test_no_rollback_on_success


# region FUNC_test_rollback_audit_log
## 🧪 TRAP[TEST] · W5-E1 audit · Scenario: rollback emits IMP:9 audit log
def test_rollback_audit_log(tmp_path, caplog):
    """W5-E1: rollback produces IMP:9 log with 'rollback' marker."""
    caplog.set_level(logging.DEBUG)

    mod_base = tmp_path / "modules"
    mod_base.mkdir(parents=True)
    entries = ["mod_a:", "mod_b:"]
    compose_down_calls: list[str] = []

    result = _call_group_deploy(
        entries=entries,
        deploy_results={"mod_a": True, "mod_b": False},
        modules_dir=str(mod_base),
        compose_down_calls=compose_down_calls,
    )

    # Verify rolled_back tuple
    assert len(result) >= 4, f"Expected 4-tuple: {result}"

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
    assert found_imp9, "No IMP:9 log found"

    # Rollback audit marker
    audit_logs = [r.message for r in caplog.records if "[IMP:9]" in r.message and "rollback" in r.message.lower()]
    assert len(audit_logs) > 0, (
        f"Expected IMP:9 rollback audit log. IMP:9 logs: "
        f"{[r.message for r in caplog.records if '[IMP:9]' in r.message]}"
    )


# endregion FUNC_test_rollback_audit_log
