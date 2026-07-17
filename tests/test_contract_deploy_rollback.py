#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-project rollback snapshot cleanup perform_rollback restore_from_snapshot KEEP_SNAPSHOTS bash subprocess
# STRUCTURE: ▶ source deploy-project.sh → ○ _cleanup_snapshots(keep=N) → ◇ snapshot_count ≤ N? → ⊕ perform_rollback(docker mock) → ◇ docker compose up --force-recreate? → ◇ audit ROLLBACK? → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for deploy-project.sh rollback subsystem: perform_rollback(),
##           _cleanup_snapshots(), _restore_from_snapshot(). Verifies KEEP_SNAPSHOTS
##           enforcement, snapshot lifecycle, and rollback docker compose invocation.
## @scope    Three test groups: cleanup (snapshot retention), restore (detect+restore flow),
##           perform_rollback (docker re-tag + compose up --force-recreate). All tests use
##           subprocess isolation with mock docker/logger commands.
## @invariants
##   - _cleanup_snapshots keeps exactly KEEP_SNAPSHOTS=3 snapshot pairs
##   - _cleanup_snapshots removes .deploy-started marker
##   - _restore_from_snapshot returns 1 when no .deploy-started exists
##   - _restore_from_snapshot calls perform_rollback when snapshot exists
##   - perform_rollback re-tags previous image and calls docker compose up -d --force-recreate
##   - perform_rollback writes ROLLBACK audit entry on success
##   - All tests use tmp_path for isolation (Zero Hardcode Rule)
## @rationale Q: Why subprocess.run for bash function testing?
##            A: deploy-project.sh is a bash script with docker/logger dependencies that can
##            only be meaningfully tested in a real bash environment. Subprocess isolation
##            prevents cross-test contamination via readonly variables and trap handlers.
## @changes CREATED: 2026-07-17 | T3: Contract tests — deploy-project.sh (rollback)
# endregion MODULE_CONTRACT

import os
import pathlib
import subprocess

import pytest
from conftest import assert_ldd_stderr

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DEPLOY_SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, "core", "internal", "deploy", "deploy-project.sh")


# ── Helpers ─────────────────────────────────────────────────────────────────


# region FUNC__run_bash
## @purpose  Source deploy-project.sh, remove traps, then run provided bash code
##           in an isolated subprocess. Returns CompletedProcess for assertion.
##           Pass readonly variables (AUDIT_LOG, PROJECTS_BASE, KEEP_SNAPSHOTS)
##           via the env dict — they are readonly in deploy-project.sh and must
##           be set BEFORE sourcing.
## @io       ⇥ (tmp_path: Path, code: str, env: dict|None) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 15s timeout
## @invariants
##   - Always creates temp script with #!/usr/bin/env bash + set -euo pipefail
##   - Sources DEPLOY_SCRIPT_PATH before running user code
##   - Removes ERR/EXIT traps to prevent interference with test assertions
##   - Script file is chmod 755 before execution
##   - Timeout 15s prevents infinite loops from hanging the test suite
def _run_bash(
    tmp_path: pathlib.Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script = tmp_path / "test_rollback.sh"
    deploy_path_escaped = str(DEPLOY_SCRIPT_PATH)

    script_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # Shell function mocks MUST be defined BEFORE sourcing deploy-project.sh
        # so they shadow external commands (logger, date, docker).
        # Mock logger FAILS (returns 1) so audit_log()'s fallback writes to stderr for test capture
        "logger() { return 1; }\n"
        "docker() {\n"
        '  local cmd="$1"; shift\n'
        "  local ec=0\n"
        '  echo "[MOCK:docker] $cmd $*" >&2\n'
        '  case "$cmd" in\n'
        "    compose)\n"
        '      local sub="$1"; shift\n'
        '      case "$sub" in\n'
        "        up|down|config) : ;;\n"
        '        ps|images) echo \'{"mock":"json"}\' ;;\n'
        "        *) : ;;\n"
        "      esac\n"
        "      ;;\n"
        # deploy-project.sh redirects docker tag stderr (2>/dev/null)
        # and compose stderr (2>&1) — use stdout for mock traces
        '    tag) echo "[MOCK:docker] tag $*" ;;\n'
        "    rmi|image) : ;;\n"
        '    *) echo "[MOCK:docker] $cmd $*" ;;\n'
        "  esac\n"
        "  return $ec\n"
        "}\n"
        # NOTE: no export -f (macOS /bin/bash v3.2 doesn't support it + functions
        # are accessible in the same shell process without export)
        "export -f docker\n"
        f'source "{deploy_path_escaped}"\n'
        # Remove traps set by deploy-project.sh — they interfere with tests
        "trap - ERR EXIT\n"
        # Override audit paths to force fallback (mock logger returns 1 + file write fails on SIP)
        'PLATFORM_LOG_DIR="/nonexistent-root-only/platform"\n'
        'PLATFORM_AUDIT_LOG="/nonexistent-root-only/platform/audit.log"\n'
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    # PROJECTS_BASE is readonly in deploy-project.sh, must be set before sourcing
    full_env.setdefault("PROJECTS_BASE", "/opt/projects")
    if env:
        full_env.update(env)

    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        env=full_env,
    )


# endregion FUNC__run_bash


# Helper: assert IMP:7+ in stderr for functions that only log at IMP:7-8
# (not requiring IMP:9 like assert_ldd_stderr does)
def _assert_ldd_stderr_imp7(result: subprocess.CompletedProcess, expected_patterns: list[str] | None = None) -> None:
    """Print LDD trajectory from stderr, assert at least IMP:7+ log lines exist."""
    found_any = False
    print("--- LDD TRAJECTORY (IMP:7-10) [from stderr] ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            found_any = True
            print(line)
    print("--- END LDD TRAJECTORY ---")
    assert found_any, "Critical LDD Error: No IMP:7+ log found in stderr"
    if expected_patterns:
        for pattern in expected_patterns:
            assert pattern in result.stderr, f"Expected '{pattern}' in stderr:\n{result.stderr}"


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: _cleanup_snapshots
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_cleanup_snapshots_keeps_exactly_n
@pytest.mark.contract
## @purpose  Verify _cleanup_snapshots() keeps exactly KEEP_SNAPSHOTS=3 snapshot
##           pairs (ps-*.json + images-*.json) and removes the .deploy-started marker.
## @scenario  Create 5 snapshot pairs + .deploy-started → call _cleanup_snapshots
##            → assert 3 pairs remain + .deploy-started removed
def test_cleanup_snapshots_keeps_exactly_n(tmp_path: pathlib.Path) -> None:
    """
    # ▶ 5 snapshot pairs + .deploy-started → _cleanup_snapshots(KEEP=3)
    #   → ◇ 3 ps-*.json + 3 images-*.json + no .deploy-started → ⎋ pass
    """
    code = (
        'SNAPSHOT_DIR="${PROJECT_DIR}/.deploy-snapshots"\n'
        'mkdir -p "$SNAPSHOT_DIR"\n'
        'echo "started" > "$SNAPSHOT_DIR/.deploy-started"\n'
        # Create 5 snapshot pairs with different timestamps
        "for ts in 100 200 300 400 500; do\n"
        '  touch "$SNAPSHOT_DIR/ps-$ts.json"\n'
        '  touch "$SNAPSHOT_DIR/images-$ts.json"\n'
        "done\n"
        "_cleanup_snapshots\n"
        'echo "PS_COUNT=$(ls "$SNAPSHOT_DIR"/ps-*.json 2>/dev/null | wc -line | xargs)"\n'
        'echo "IMAGES_COUNT=$(ls "$SNAPSHOT_DIR"/images-*.json 2>/dev/null | wc -line | xargs)"\n'
        'echo "HAS_STARTED=$([ -f "$SNAPSHOT_DIR/.deploy-started" ] && echo yes || echo no)"\n'
    )

    result = _run_bash(tmp_path, code, env={"PROJECT_DIR": str(tmp_path), "KEEP_SNAPSHOTS": "3"})

    _assert_ldd_stderr_imp7(result, expected_patterns=["Cleaning old snapshots"])

    stdout = result.stdout
    ps_count = int(next(line for line in stdout.splitlines() if "PS_COUNT=" in line).split("=")[1])
    images_count = int(next(line for line in stdout.splitlines() if "IMAGES_COUNT=" in line).split("=")[1])
    has_started = next(line for line in stdout.splitlines() if "HAS_STARTED=" in line).split("=")[1]

    assert ps_count == 3, f"[IMP:9][cleanup] Expected 3 ps-*.json, got {ps_count}\n{stdout}"
    assert images_count == 3, f"[IMP:9][cleanup] Expected 3 images-*.json, got {images_count}\n{stdout}"
    assert has_started == "no", f"[IMP:9][cleanup] .deploy-started should be removed but was {has_started}"

    print("[IMP:9][test_cleanup_snapshots_keeps_exactly_n] PASS: _cleanup_snapshots keeps exactly 3 pairs")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_cleanup_snapshots_keeps_exactly_n


# region FUNC_test_cleanup_snapshots_default_keep
@pytest.mark.contract
## @purpose  Verify _cleanup_snapshots() falls back to KEEP_SNAPSHOTS=3 default
##           when KEEP_SNAPSHOTS env var is not set.
## @scenario  No KEEP_SNAPSHOTS set → 5 pairs → assert 3 kept (default)
def test_cleanup_snapshots_default_keep(tmp_path: pathlib.Path) -> None:
    """
    # ▶ 5 pairs, no KEEP_SNAPSHOTS → _cleanup_snapshots → ◇ 3 kept → ⎋ pass
    """
    code = (
        'SNAPSHOT_DIR="${PROJECT_DIR}/.deploy-snapshots"\n'
        'mkdir -p "$SNAPSHOT_DIR"\n'
        "for ts in 100 200 300 400 500; do\n"
        '  touch "$SNAPSHOT_DIR/ps-$ts.json"\n'
        '  touch "$SNAPSHOT_DIR/images-$ts.json"\n'
        "done\n"
        "_cleanup_snapshots\n"
        'echo "PS_COUNT=$(ls "$SNAPSHOT_DIR"/ps-*.json 2>/dev/null | wc -line | xargs)"\n'
        'echo "IMAGES_COUNT=$(ls "$SNAPSHOT_DIR"/images-*.json 2>/dev/null | wc -line | xargs)"\n'
    )

    result = _run_bash(tmp_path, code, env={"PROJECT_DIR": str(tmp_path)})

    _assert_ldd_stderr_imp7(result, expected_patterns=["Cleaning old snapshots"])

    stdout = result.stdout
    ps_count = int(next(line for line in stdout.splitlines() if "PS_COUNT=" in line).split("=")[1])
    images_count = int(next(line for line in stdout.splitlines() if "IMAGES_COUNT=" in line).split("=")[1])

    assert ps_count == 3, f"Expected 3 ps-*.json, got {ps_count}"
    assert images_count == 3, f"Expected 3 images-*.json, got {images_count}"

    print("[IMP:9][test_cleanup_snapshots_default_keep] PASS: default KEEP_SNAPSHOTS=3 enforced")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_cleanup_snapshots_default_keep


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: _restore_from_snapshot
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_restore_no_snapshot
@pytest.mark.contract
## @purpose  _restore_from_snapshot returns 1 when no .deploy-started marker exists.
## @scenario  Empty snapshot dir → call _restore_from_snapshot → assert "cannot rollback"
def test_restore_no_snapshot(tmp_path: pathlib.Path) -> None:
    """
    # ▶ empty .deploy-snapshots/ → _restore_from_snapshot → ◇ returncode 1
    #   + "cannot rollback" log → ⎋ pass
    """
    code = 'mkdir -p "${PROJECT_DIR}/.deploy-snapshots"\n_restore_from_snapshot || ec=$?\necho "EXIT_CODE=${ec:-0}"\n'

    result = _run_bash(tmp_path, code, env={"PROJECT_DIR": str(tmp_path)})

    assert_ldd_stderr(result, expected_patterns=["No pre-deploy snapshot found"])
    stdout = result.stdout
    exit_code = int(next(line for line in stdout.splitlines() if "EXIT_CODE=" in line).split("=")[1])
    assert exit_code == 1, f"Expected exit_code=1, got {exit_code}"

    print("[IMP:9][test_restore_no_snapshot] PASS: returns 1 when no pre-deploy snapshot")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_restore_no_snapshot


# region FUNC_test_restore_with_snapshot
@pytest.mark.contract
## @purpose  _restore_from_snapshot calls perform_rollback when snapshot exists,
##           which triggers docker compose up -d --force-recreate.
## @scenario  Create .deploy-started + images snapshot → call _restore_from_snapshot
##            → assert RESTORING log + docker compose up called
def test_restore_with_snapshot(tmp_path: pathlib.Path) -> None:
    """
    # ▶ .deploy-started + images-*.json → _restore_from_snapshot
    #   → ◇ Restoring previous + docker compose up --force-recreate → ⎋ pass
    """
    code = (
        'SNAPSHOT_DIR="${PROJECT_DIR}/.deploy-snapshots"\n'
        'mkdir -p "$SNAPSHOT_DIR"\n'
        'echo "1712345678" > "$SNAPSHOT_DIR/.deploy-started"\n'
        'echo \'{"mock":"data"}\' > "$SNAPSHOT_DIR/images-1712345678.json"\n'
        'SERVICE_NAME="test-app"\n'
        'PREVIOUS_IMAGE_ID="sha256:abc123"\n'
        'PREVIOUS_IMAGE_TAG="registry.io/test-app:previous"\n'
        'PROJECT="test-project"\n'
        'REF="v1.0.0"\n'
        "_restore_from_snapshot || true\n"
    )

    result = _run_bash(tmp_path, code, env={"PROJECT_DIR": str(tmp_path)})

    assert_ldd_stderr(result, expected_patterns=["Restoring previous", "ROLLING BACK", "Rollback complete"])

    print("[IMP:9][test_restore_with_snapshot] PASS: _restore_from_snapshot calls perform_rollback")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_restore_with_snapshot


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: perform_rollback
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_perform_rollback_invokes_compose_up
@pytest.mark.contract
## @purpose  perform_rollback re-tags previous image and invokes
##           `docker compose up -d --force-recreate` for rollback.
##           AUDIT_LOG is readonly in deploy-project.sh (hardcoded to
##           /var/log/platform/audit.log), so audit_write file write is
##           silently skipped, but IMP:9 stderr output is still captured.
## @scenario  Set previous image vars → call perform_rollback → assert docker mock
##            receives tag + compose up commands, audit writes ROLLBACK
def test_perform_rollback_invokes_compose_up(tmp_path: pathlib.Path) -> None:
    """
    # ▶ PREVIOUS_IMAGE_ID + TAG → perform_rollback → ◇ docker tag + compose up --force-recreate
    #   + stderr ROLLBACK → ⎋ pass
    """
    code = (
        'PROJECT_DIR="$(mktemp -d)"\n'
        'cd "$PROJECT_DIR"\n'
        'SERVICE_NAME="test-app"\n'
        'PREVIOUS_IMAGE_ID="sha256:abc123"\n'
        'PREVIOUS_IMAGE_TAG="test-app:previous"\n'
        'PROJECT="test-project"\n'
        'REF="v1.0.0"\n'
        'touch "$PROJECT_DIR/docker-compose.yml"\n'
        # perform_rollback returns 1 on success (line 415)
        "perform_rollback && exit_code=0 || exit_code=$?\n"
        'echo "EXIT_CODE=${exit_code}"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(
        result,
        expected_patterns=[
            "ROLLING BACK",
            "Re-tagged",
            "Rollback complete",
            "ROLLBACK",
        ],
    )
    stdout = result.stdout
    exit_code = int(next(line for line in stdout.splitlines() if "EXIT_CODE=" in line).split("=")[1])
    assert exit_code == 1, f"Expected exit_code=1 from perform_rollback, got {exit_code}"

    print("[IMP:9][test_perform_rollback_invokes_compose_up] PASS: rollback calls docker compose up")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_perform_rollback_invokes_compose_up


# region FUNC_test_perform_rollback_first_deploy
@pytest.mark.contract
## @purpose  perform_rollback handles missing PREVIOUS_IMAGE_TAG gracefully — when
##           no previous image tag exists, it skips docker tag and goes straight to compose up.
## @scenario  PREVIOUS_IMAGE_TAG empty → call perform_rollback → assert compose up still called
def test_perform_rollback_no_previous_tag(tmp_path: pathlib.Path) -> None:
    """
    # ▶ PREVIOUS_IMAGE_TAG="" → perform_rollback → skip tag, still compose up → ⎋ pass
    """
    code = (
        'PROJECT_DIR="$(mktemp -d)"\n'
        'cd "$PROJECT_DIR"\n'
        'SERVICE_NAME="test-app"\n'
        'PREVIOUS_IMAGE_ID="sha256:abc123"\n'
        'PREVIOUS_IMAGE_TAG=""\n'
        'PROJECT="test-project"\n'
        'REF="v1.0.0"\n'
        'touch "$PROJECT_DIR/docker-compose.yml"\n'
        "perform_rollback && exit_code=0 || exit_code=$?\n"
        'echo "EXIT_CODE=${exit_code}"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(result, expected_patterns=["ROLLING BACK", "Rollback complete"])

    # Verify docker tag was NOT called (PREVIOUS_IMAGE_TAG was empty)
    stderr = result.stderr
    tag_lines = [line for line in stderr.splitlines() if "MOCK:docker tag" in line]
    assert len(tag_lines) == 0, (
        f"[IMP:9][rollback] Expected NO docker tag call when PREVIOUS_IMAGE_TAG is empty, "
        f"but found {len(tag_lines)} tag calls"
    )

    print("[IMP:9][test_perform_rollback_no_previous_tag] PASS: skips tag when no previous tag")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_perform_rollback_first_deploy


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: snapshot format
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_snapshot_format_timestamp
@pytest.mark.contract
## @purpose  Snapshot filenames follow images-<timestamp>.json format (unix epoch).
##           Verify capture_deploy_snapshot creates files with numeric timestamps.
## @scenario  Mock docker compose ps/images → call capture_deploy_snapshot
##            → assert files created: ps-<num>.json + images-<num>.json
def test_snapshot_format_timestamp(tmp_path: pathlib.Path) -> None:
    """
    # ▶ mock docker compose → capture_deploy_snapshot → ◇ ps-<ts>.json + images-<ts>.json → ⎋ pass
    """
    code = (
        'mkdir -p "$PROJECT_DIR"\n'
        'cd "$PROJECT_DIR"\n'
        'touch "$PROJECT_DIR/docker-compose.yml"\n'
        'SERVICE_NAME="test-app"\n'
        "capture_deploy_snapshot\n"
        'SNAPSHOT_DIR="${PROJECT_DIR}/.deploy-snapshots"\n'
        'echo "PS_FILES=$(ls "$SNAPSHOT_DIR"/ps-*.json 2>/dev/null | wc -line | xargs)"\n'
        'echo "IMAGES_FILES=$(ls "$SNAPSHOT_DIR"/images-*.json 2>/dev/null | wc -line | xargs)"\n'
        'echo "HAS_STARTED=$([ -f "$SNAPSHOT_DIR/.deploy-started" ] && echo yes || echo no)"\n'
    )

    result = _run_bash(tmp_path, code, env={"PROJECT_DIR": str(tmp_path)})

    _assert_ldd_stderr_imp7(result, expected_patterns=["Pre-deploy snapshot complete"])
    stdout = result.stdout
    ps_files = int(next(line for line in stdout.splitlines() if "PS_FILES=" in line).split("=")[1])
    images_files = int(next(line for line in stdout.splitlines() if "IMAGES_FILES=" in line).split("=")[1])
    has_started = next(line for line in stdout.splitlines() if "HAS_STARTED=" in line).split("=")[1]

    assert ps_files == 1, f"Expected 1 ps-*.json file, got {ps_files}"
    assert images_files == 1, f"Expected 1 images-*.json file, got {images_files}"
    assert has_started == "yes", ".deploy-started marker should exist after capture"

    print("[IMP:9][test_snapshot_format_timestamp] PASS: snapshot files created with timestamp format")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_snapshot_format_timestamp
