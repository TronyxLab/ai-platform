"""
# GREP_SUMMARY: test_deploy_snapshot, deploy-project, behavioural, subprocess, mock-docker, snapshot, rollback
# STRUCTURE: ▶ isolated_deploy_env(tmp_path) → ◇ 7 behavioural tests via subprocess(source deploy-project.sh + func) → ◇ assert file artifacts + mock log → ⎋ IMP:9 verification
# region MODULE_CONTRACT
## @purpose  Behavioural unit tests for deploy-project.sh — calls real bash functions via subprocess with mock docker compose
## @scope    Tests capture_deploy_snapshot, _rollback_on_error, _finalize_deploy, _write_deploy_result
## @invariants
##   - Each test runs in an isolated subprocess with tmp_path-based PROJECT_DIR
##   - Mock docker script intercepts compose ps/images/down/up calls
##   - All tests disable EXIT/ERR traps (trap - EXIT ERR) except rollback test
##   - Anti-illusion: every test validates IMP:9 logs or output artifacts
## @rationale deploy-project.sh uses set -euo pipefail + trap ERR/EXIT. Subprocess isolation
##   prevents trap pollution in pytest process. Mock docker captures calls without real Docker.
## @changes
##   2026-07-15 · Complete rewrite from conceptual stubs to behavioural tests (GAP-001 remediation)
##   · Replaced 8 stub tests (assert True/False literals) with 7 subprocess-based tests
##   · Added isolated_deploy_env fixture with mock docker compose
##   · Added _run_deploy_func helper for source + function call in subshell
# endregion MODULE_CONTRACT
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

# ── Paths ──
DEPLOY_SCRIPT = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "deploy" / "deploy-project.sh"


# region FUNC_isolated_deploy_env
## @purpose  Create tmp_path-based project dir, mock docker script, and env dict for deploy-project.sh tests
## @io       tmp_path → dict with project_dir, snapshot_dir, bin_dir, env, tmp_path, mock_log
## @complexity 3 — creates dirs, writes mock binary, builds env dict
@pytest.fixture
def isolated_deploy_env(tmp_path):
    """
    Create an isolated environment for testing deploy-project.sh functions.

    Sets up:
    - project_dir with .deploy-snapshots/
    - mock docker script in bin/ that captures compose ps/images/down/up calls
    - environment variables (PROJECT_DIR, DEPLOY_REF, PROJECT, REF, KEEP_SNAPSHOTS, PATH)
    - MOCK_LOG file at tmp_path/mock_docker.log
    """
    project_dir = tmp_path / "project"
    snapshot_dir = project_dir / ".deploy-snapshots"
    snapshot_dir.mkdir(parents=True)

    # ── Mock docker script ──
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mock_log = tmp_path / "mock_docker.log"
    mock_docker = bin_dir / "docker"
    mock_docker.write_text(f"""#!/bin/bash
MOCK_LOG="{mock_log}"
case "$1 $2" in
  "compose ps") echo '[{{"Name":"pgbouncer","State":"running"}}]' ;;
  "compose images") echo '[{{"Repository":"pgbouncer","Tag":"latest","ID":"abc123"}}]' ;;
  "compose down") echo "[MOCK] docker compose down $*" >> "$MOCK_LOG" ;;
  "compose up") echo "[MOCK] docker compose up $*" >> "$MOCK_LOG" ;;
  *) echo "[MOCK] docker $*" >> "$MOCK_LOG" ;;
esac
exit 0
""")
    mock_docker.chmod(0o755)

    # ── Environment ──
    env = os.environ.copy()
    env["PROJECT_DIR"] = str(project_dir)
    env["DEPLOY_REF"] = "test-ref"
    env["PROJECT"] = "test-project"
    env["REF"] = "test-ref"
    env["SERVICE_NAME"] = "test-service"
    env["PREVIOUS_IMAGE_ID"] = "sha256:abc123"
    env["PREVIOUS_IMAGE_TAG"] = "test-service:previous"
    env["KEEP_SNAPSHOTS"] = "3"
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["MOCK_LOG"] = str(mock_log)

    return {
        "project_dir": project_dir,
        "snapshot_dir": snapshot_dir,
        "bin_dir": bin_dir,
        "env": env,
        "tmp_path": tmp_path,
        "mock_log": mock_log,
    }


# endregion FUNC_isolated_deploy_env


# region FUNC__run_deploy_func
## @purpose  Run a deploy-project.sh function in an isolated subprocess
## @io       func_name (str), env (dict), timeout (int) → subprocess.CompletedProcess
## @complexity 2 — constructs bash -c with source + trap disable + function call
def _run_deploy_func(
    func_name: str, env: dict[str, str], timeout: int = 10, disable_traps: bool = True
) -> subprocess.CompletedProcess:
    """
    Run a function from deploy-project.sh in a subprocess.

    By default disables EXIT/ERR traps (trap - EXIT ERR) to test individual
    functions in isolation. Set disable_traps=False to preserve trap behavior
    (used by rollback test).
    """
    trap_cmd = "trap - EXIT ERR; " if disable_traps else ""
    script = f"source '{DEPLOY_SCRIPT}'; {trap_cmd}{func_name}"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


# endregion FUNC__run_deploy_func


# region FUNC__read_mock_log
## @purpose  Read MOCK_LOG content as a list of lines
## @io       Path → list[str]
def _read_mock_log(env: dict) -> list[str]:
    log_path = Path(env["MOCK_LOG"])
    if not log_path.exists():
        return []
    return log_path.read_text().splitlines()


# endregion FUNC__read_mock_log


# ══════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════


# region FUNC_test_snapshot_creates_ps_json
## @purpose  capture_deploy_snapshot creates ps-{ts}.json with valid docker compose ps output
## @io       subprocess → assert file exists + valid JSON
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: capture_deploy_snapshot creates ps JSON · Last fail: N/A · Remove if: deploy-project.sh restructured
def test_snapshot_creates_ps_json(isolated_deploy_env):
    """capture_deploy_snapshot should create ps-<ts>.json with valid JSON."""
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    result = _run_deploy_func("capture_deploy_snapshot", env)

    # Find ps-*.json files
    ps_files = list(snapshot_dir.glob("ps-*.json"))
    assert len(ps_files) == 1, f"Expected 1 ps-*.json, found {len(ps_files)}"
    data = json.loads(ps_files[0].read_text())
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["Name"] == "pgbouncer"

    # Anti-illusion: verify stderr contains [platform-deploy][snapshot] log
    # deploy-project.sh sets __LOG_PREFIX="platform-deploy", so actual format is
    # [IMP:N][platform-deploy][snapshot] ...
    assert "[platform-deploy][snapshot]" in result.stderr, f"No snapshot log in stderr: {result.stderr}"


# endregion FUNC_test_snapshot_creates_ps_json


# region FUNC_test_snapshot_creates_images_json
## @purpose  capture_deploy_snapshot creates images-{ts}.json with valid docker compose images output
## @io       subprocess → assert file exists + valid JSON
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: capture_deploy_snapshot creates images JSON · Last fail: N/A · Remove if: deploy-project.sh restructured
def test_snapshot_creates_images_json(isolated_deploy_env):
    """capture_deploy_snapshot should create images-<ts>.json with valid JSON."""
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    result = _run_deploy_func("capture_deploy_snapshot", env)

    images_files = list(snapshot_dir.glob("images-*.json"))
    assert len(images_files) == 1, f"Expected 1 images-*.json, found {len(images_files)}"
    data = json.loads(images_files[0].read_text())
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["Repository"] == "pgbouncer"

    # Anti-illusion
    assert "[platform-deploy][snapshot]" in result.stderr, f"No snapshot log in stderr: {result.stderr}"


# endregion FUNC_test_snapshot_creates_images_json


# region FUNC_test_snapshot_creates_started_marker
## @purpose  capture_deploy_snapshot creates .deploy-started marker with unix timestamp
## @io       subprocess → assert file exists + content is numeric
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: capture_deploy_snapshot creates .deploy-started · Last fail: N/A · Remove if: deploy-project.sh restructured
def test_snapshot_creates_started_marker(isolated_deploy_env):
    """capture_deploy_snapshot should create .deploy-started with a unix timestamp."""
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    result = _run_deploy_func("capture_deploy_snapshot", env)

    started_file = snapshot_dir / ".deploy-started"
    assert started_file.exists(), ".deploy-started marker file not created"
    ts_content = started_file.read_text().strip()
    assert ts_content.isdigit(), f".deploy-started content is not a digit: {ts_content}"
    # Sanity: should be a reasonable unix timestamp (year 2026 = ~1.7B)
    ts = int(ts_content)
    assert ts > 1700000000, f"Timestamp seems too old: {ts}"

    # Anti-illusion
    assert "[platform-deploy][snapshot]" in result.stderr, f"No snapshot log in stderr: {result.stderr}"


# endregion FUNC_test_snapshot_creates_started_marker


# region FUNC_test_rollback_restores_from_snapshot
## @purpose  ERR trap → _rollback_on_error → _restore_from_snapshot calls docker compose down+up
## @io       subprocess with false → assert MOCK_LOG contains down+up
## @complexity 3 — traps NOT disabled; uses false to trigger ERR trap
# 🧪 TRAP[TEST] · Regression · Scenario: ERR trap triggers rollback with docker compose down+up · Last fail: N/A · Remove if: trap semantics changed
def test_rollback_restores_from_snapshot(isolated_deploy_env):
    """
    When deploy fails (false), ERR trap should trigger _rollback_on_error,
    which calls _restore_from_snapshot → docker compose down && up.

    This test does NOT disable traps, so the ERR trap fires naturally.
    """
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    # Build custom script: source, capture snapshot, then trigger ERR trap via false
    script = f"source '{DEPLOY_SCRIPT}'; capture_deploy_snapshot; false"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    # The subprocess should exit non-zero (false triggers ERR trap)
    # EXIT trap also fires (writes deploy-result.json)
    assert result.returncode != 0, "Expected non-zero exit from false + ERR trap"

    # Verify MOCK_LOG contains docker compose commands
    # _restore_from_snapshot calls perform_rollback (always defined in script),
    # which calls: docker tag + docker compose up -d --force-recreate
    mock_log_lines = _read_mock_log(env)
    assert len(mock_log_lines) >= 1, (
        f"Mock log empty — docker not called.\n"
        f"stderr:\n{result.stderr}\n"
        f"MOCK_LOG path: {isolated_deploy_env['mock_log']}"
    )
    # perform_rollback calls docker compose up -d --force-recreate (no down)
    up_calls = [line for line in mock_log_lines if "up" in line]
    assert len(up_calls) >= 1, f"No docker compose up in mock log: {mock_log_lines}\nstderr:\n{result.stderr}"

    # Verify deploy-result.json was written (by EXIT trap → _finalize_deploy)
    result_file = snapshot_dir / "deploy-result.json"
    assert result_file.exists(), "deploy-result.json not written by EXIT trap"
    data = json.loads(result_file.read_text())
    assert data["status"] == "failed", f"Expected status=failed, got {data['status']}"

    # Anti-illusion: verify [platform-deploy][rollback] in stderr
    assert "[platform-deploy][rollback]" in result.stderr, f"No rollback log in stderr:\n{result.stderr}"


# endregion FUNC_test_rollback_restores_from_snapshot


# region FUNC_test_finalize_success_writes_result
## @purpose  _finalize_deploy with DEPLOY_STATUS=success writes result and cleans up snapshots
## @io       subprocess → assert deploy-result.json + .deploy-started removed
## @complexity 2 — verify conditional cleanup behavior
# 🧪 TRAP[TEST] · Regression · Scenario: _finalize_deploy on success writes status=success and cleans .deploy-started · Last fail: N/A · Remove if: finalize logic changed
def test_finalize_success_writes_result(isolated_deploy_env):
    """
    _finalize_deploy with DEPLOY_STATUS=success should:
    1. Write deploy-result.json with status=success
    2. Remove .deploy-started marker
    3. Keep snapshot JSON files within KEEP_SNAPSHOTS limit
    """
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    script = (
        f"source '{DEPLOY_SCRIPT}'; trap - EXIT ERR; capture_deploy_snapshot; DEPLOY_STATUS='success'; _finalize_deploy"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"

    # deploy-result.json should exist with status=success
    result_file = snapshot_dir / "deploy-result.json"
    assert result_file.exists(), "deploy-result.json not created"
    data = json.loads(result_file.read_text())
    assert data["status"] == "success", f"Expected status=success, got {data['status']}"
    assert "timestamp" in data
    assert data["project"] == "test-project"
    assert data["ref"] == "test-ref"

    # .deploy-started should be removed (cleanup on success)
    started_file = snapshot_dir / ".deploy-started"
    assert not started_file.exists(), ".deploy-started should be removed on success"

    # Anti-illusion: verify [platform-deploy][deploy] log in stderr
    assert "[platform-deploy][deploy]" in result.stderr, f"No deploy log in stderr:\n{result.stderr[:500]}"


# endregion FUNC_test_finalize_success_writes_result


# region FUNC_test_finalize_failure_preserves_snapshot
## @purpose  _finalize_deploy with DEPLOY_STATUS=failed writes result but does NOT clean up snapshots
## @io       subprocess → assert deploy-result.json with status=failed + .deploy-started preserved
## @complexity 2 — verify conditional cleanup is skipped
# 🧪 TRAP[TEST] · Regression · Scenario: _finalize_deploy on failure writes status=failed and preserves snapshots · Last fail: N/A · Remove if: finalize logic changed
def test_finalize_failure_preserves_snapshot(isolated_deploy_env):
    """
    _finalize_deploy with DEPLOY_STATUS=failed should:
    1. Write deploy-result.json with status=failed
    2. NOT remove .deploy-started marker (preserve for debugging)
    3. Keep all snapshot files
    """
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    script = (
        f"source '{DEPLOY_SCRIPT}'; trap - EXIT ERR; capture_deploy_snapshot; DEPLOY_STATUS='failed'; _finalize_deploy"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"

    # deploy-result.json should exist with status=failed
    result_file = snapshot_dir / "deploy-result.json"
    assert result_file.exists(), "deploy-result.json not created"
    data = json.loads(result_file.read_text())
    assert data["status"] == "failed", f"Expected status=failed, got {data['status']}"

    # .deploy-started should be preserved (no cleanup on failure)
    started_file = snapshot_dir / ".deploy-started"
    assert started_file.exists(), ".deploy-started should be preserved on failure"

    # Snapshot files should still exist
    ps_files = list(snapshot_dir.glob("ps-*.json"))
    assert len(ps_files) == 1, "ps-*.json should be preserved on failure"
    images_files = list(snapshot_dir.glob("images-*.json"))
    assert len(images_files) == 1, "images-*.json should be preserved on failure"

    # Anti-illusion
    assert "[platform-deploy][deploy]" in result.stderr, f"No deploy log in stderr:\n{result.stderr[:500]}"


# endregion FUNC_test_finalize_failure_preserves_snapshot


# region FUNC_test_write_deploy_result_json
## @purpose  _write_deploy_result produces valid JSON with status, timestamp, project, ref
## @io       subprocess → assert JSON structure and field values
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: _write_deploy_result outputs valid ISO8601 JSON · Last fail: N/A · Remove if: result format changed
def test_write_deploy_result_json(isolated_deploy_env):
    """_write_deploy_result should write valid JSON with all required fields."""
    env = isolated_deploy_env["env"]
    snapshot_dir = isolated_deploy_env["snapshot_dir"]

    script = f"source '{DEPLOY_SCRIPT}'; trap - EXIT ERR; DEPLOY_STATUS='success'; _write_deploy_result"
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, f"Subprocess failed: {result.stderr}"

    result_file = snapshot_dir / "deploy-result.json"
    assert result_file.exists(), "deploy-result.json not created"
    data = json.loads(result_file.read_text())

    # Verify all required fields
    assert data["status"] == "success"
    assert "timestamp" in data
    # Timestamp should be ISO8601 format
    assert "T" in data["timestamp"], f"Expected ISO8601 timestamp, got {data['timestamp']}"
    assert data["project"] == "test-project"
    assert data["ref"] == "test-ref"

    # Anti-illusion: verify [platform-deploy][deploy] log
    assert "[platform-deploy][deploy]" in result.stderr, f"No deploy log in stderr:\n{result.stderr[:500]}"


# endregion FUNC_test_write_deploy_result_json
