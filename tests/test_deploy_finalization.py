#!/usr/bin/env python3
# GREP_SUMMARY: deploy finalization B1 health-gate rollback deploy-result success non-fatal audit notify
# STRUCTURE: ┌tmp_path harness + PATH stubs (fake docker/logger)┐ → ○ test_notify_missing → ○ test_audit_denied → ○ test_health_fails → ⊕ assert exit code + deploy-result.json
# region MODULE_CONTRACT
## @purpose  Shell-based integration tests for deploy-project.sh B1 finalization fix
## @scope    Tests deploy-project.sh in isolated tmp_path environment with fake docker/logger PATH stubs.
##           Uses wrapper script to override poll_until_healthy for macOS bash 3.2 compatibility.
## @invariants
##   - Tests run deploy-project.sh via wrapper as subprocess (allowed per DevPlan T3.2)
##   - Uses REAL lib/ files from project checkout — only external commands (docker, logger) are faked
##   - PROJECTS_BASE set to tmp_path to avoid polluting /opt/projects
##   - poll_until_healthy overridden in wrapper for fast test execution on macOS (bash 3.2)
## @rationale B1 root cause: DEPLOY_STATUS="success" set AFTER non-fatal steps under set -e.
##            Tests verify: (1) success even with missing notify-hook, (2) success with unwritable audit.log,
##            (3) health-gate failure still causes exit 1 (anti-regression).
## @changes 2026-07-18 · T3.2/B1 — Created for B1 regression protection
# endregion MODULE_CONTRACT

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

# region FAKE_DOCKER_SCRIPT
## @purpose  Fake docker executable that returns controlled responses.
##           Supports docker/docker-compose commands used by deploy-project.sh.
##           FAKE_HEALTH_CHECK=ok → docker inspect returns healthy
##           FAKE_HEALTH_CHECK=fail → docker inspect returns unhealthy
_FAKE_DOCKER_SCRIPT = r"""#!/bin/bash
FAKE_HEALTH_CHECK="${FAKE_HEALTH_CHECK:-ok}"
if [[ "$1" == "compose" ]]; then
    shift
    case "$1" in
        ps)
            shift
            if [[ "$1" == "-q" ]]; then
                [[ "${FAKE_HEALTH_CHECK}" == "ok" ]] && echo "testcontainer123"
                exit 0
            fi
            if [[ "$1" == "--format" && "$2" == "json" ]]; then
                echo '{"ID":"testcontainer123","Name":"testproj","Status":"running"}'
                exit 0
            fi
            echo "testproj running"
            exit 0
            ;;
        images)
            shift
            [[ "$1" == "-q" ]] && echo "sha256:testimage123" && exit 0
            [[ "$1" == "--format" && "$2" == "json" ]] && echo '{"ID":"sha256:testimage123","Repository":"testproj","Tag":"latest","CreatedAt":"2026-01-01T00:00:00Z"}' && exit 0
            exit 0
            ;;
        up) exit 0 ;;
        pull) exit 0 ;;
        down) exit 0 ;;
        config) printf 'services:\n  testproj:\n    image: ghcr.io/test/testproj:latest\n' && exit 0 ;;
        logs) echo "started" && exit 0 ;;
        *) echo "UNKNOWN COMPOSE: $*" >&2; exit 0 ;;
    esac
fi
case "$1" in
    inspect)
        shift
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --format=*)
                    fmt="${1#--format=}"; fmt="${fmt%\'}"; fmt="${fmt#\'}"
                    case "$fmt" in *Health*) [[ "${FAKE_HEALTH_CHECK}" == "ok" ]] && echo "healthy" || echo "unhealthy"; exit 0 ;;
                        *Status*) echo "running"; exit 0 ;;
                        *RepoTags*) echo "testproj:latest"; exit 0 ;;
                    esac ;;
                --format)
                    fmt="$2"; shift; fmt="${fmt%\'}"; fmt="${fmt#\'}"
                    case "$fmt" in *Health*) [[ "${FAKE_HEALTH_CHECK}" == "ok" ]] && echo "healthy" || echo "unhealthy"; exit 0 ;;
                        *Status*) echo "running"; exit 0 ;;
                        *RepoTags*) echo "testproj:latest"; exit 0 ;;
                    esac ;;
            esac
            shift
        done
        exit 0 ;;
    tag) exit 0 ;;
    rmi) exit 0 ;;
    network) exit 0 ;;
    login) exit 0 ;;
    *) echo "UNKNOWN DOCKER: $*" >&2; exit 0 ;;
esac
"""
# endregion FAKE_DOCKER_SCRIPT


# region FAKE_COMMAND_STUBS
_FAKE_LOGGER_SCRIPT = "#!/bin/bash\nexit 0"
_FAKE_SS_SCRIPT = "#!/bin/bash\nexit 0"
_FAKE_SLEEP_SCRIPT = "#!/bin/bash\nexit 0"
# endregion FAKE_COMMAND_STUBS


# region DEPLOY_WRAPPER
## @purpose  Wrapper script that sources deploy-project.sh and overrides
##           poll_until_healthy for fast execution. macOS bash 3.2 doesn't
##           support EPOCHSECONDS or %(%s)T, causing poll_until_healthy
##           to loop infinitely. This override returns immediately.
_DEPLOY_WRAPPER = """#!/bin/bash
# Wrapper: sources deploy-project.sh and overrides poll_until_healthy for test speed.
# Usage: wrapper.sh <repo_root>
# repo_root is passed explicitly because BASH_SOURCE in tmp dir doesn't resolve to the real checkout.

set -euo pipefail

REPO_ROOT="${1:?Usage: wrapper.sh <repo_root>}"

# Source the real deploy-project.sh (this also sources all libs)
source "${REPO_ROOT}/core/internal/deploy/deploy-project.sh"

# ── Override poll_until_healthy ──
# The override MUST happen AFTER source (source redefines from healthcheck.sh).
# We call the real _check_deploy_health once, and return immediately.
# For speed: single check, no polling loop, no sleep, no time dependency.
poll_until_healthy() {
    local name="$1"
    local check_command="$2"
    # Call the check function directly — split string into command as original does
    local check_cmd=()
    IFS=' ' read -ra check_cmd <<< "$check_command"
    if "${check_cmd[@]}"; then
        log_imp 9 "poll_until_healthy" "'${name}' is healthy (overridden)"
        return 0
    else
        log_imp 10 "poll_until_healthy" "'${name}' NOT healthy (overridden, immediate)"
        return 1
    fi
}

# Go!
main "$@"
"""
# endregion DEPLOY_WRAPPER


# region HARNESS_FIXTURE
@pytest.fixture
def deploy_harness(tmp_path: Path):
    """Set up isolated test harness for deploy-project.sh.

    Creates:
      - tmp_path/bin/ with fake docker/logger/ss/sleep
      - tmp_path/projects/<name>/ with project files
      - tmp_path/wrapper_deploy.sh as the entry point
    """
    # ── PATH stubs ──
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for stub_name, stub_script in [
        ("docker", _FAKE_DOCKER_SCRIPT),
        ("logger", _FAKE_LOGGER_SCRIPT),
        ("ss", _FAKE_SS_SCRIPT),
        ("sleep", _FAKE_SLEEP_SCRIPT),
    ]:
        p = bin_dir / stub_name
        p.write_text(stub_script)
        p.chmod(0o755)

    # ── Project directory ──
    project_name = "testproj"
    projects_base = tmp_path / "projects"
    project_dir = projects_base / project_name
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text(
        textwrap.dedent("""\
        version: "3.8"
        services:
          testproj:
            image: test:latest
            ports:
              - "3000:3000"
    """)
    )
    (project_dir / "ai-platform.yaml").write_text(
        textwrap.dedent("""\
        project: testproj
        service: testproj
    """)
    )

    # ── Repo root ──
    repo_root = Path(__file__).resolve().parent.parent

    # ── Wrapper script ──
    wrapper = tmp_path / "wrapper_deploy.sh"
    wrapper.write_text(_DEPLOY_WRAPPER)
    wrapper.chmod(0o755)

    return {
        "tmp_path": tmp_path,
        "bin_dir": bin_dir,
        "project_dir": project_dir,
        "project_name": project_name,
        "projects_base": projects_base,
        "deploy_script": repo_root / "core" / "internal" / "deploy" / "deploy-project.sh",
        "wrapper_script": wrapper,
        "repo_root": repo_root,
    }


# endregion HARNESS_FIXTURE


# region UTIL_RUN_DEPLOY
def _run_deploy(
    harness: dict,
    extra_env: dict | None = None,
    timeout_sec: int = 30,
) -> subprocess.CompletedProcess:
    """Run deploy wrapper in harness with optional env overrides."""
    audit_dir = harness["tmp_path"] / "audit"
    audit_dir.mkdir(exist_ok=True)

    env = {
        "PROJECTS_BASE": str(harness["projects_base"]),
        "SSH_ORIGINAL_COMMAND": f"platform-deploy {harness['project_name']} ref123",
        "PATH": f"{harness['bin_dir']}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_HEALTH_CHECK": "ok",
        "PLATFORM_LOG_DIR": str(audit_dir),
        "__LOG_PREFIX": "deploy-test",
        "HOME": str(harness["tmp_path"]),
    }
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(harness["wrapper_script"]), str(harness["repo_root"])],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        cwd=str(harness["tmp_path"]),
        env=env,
    )


# endregion UTIL_RUN_DEPLOY


# region LDD_CHECK_HELPER
def _check_ldd_trajectory(stderr: str) -> bool:
    """Print LDD trajectory and return True if IMP:9 found."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in stderr.splitlines():
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
            except (ValueError, IndexError):
                continue
            if imp_level >= 7:
                print(line)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    return found


# endregion LDD_CHECK_HELPER


# region TEST_NOTIFY_MISSING
def test_deploy_success_notify_missing(deploy_harness, caplog):
    """B1: notify-hook absent → deploy-result.json=success, exit 0."""
    proc = _run_deploy(deploy_harness)
    found_imp9 = _check_ldd_trajectory(proc.stderr or "")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    # Exit 0 expected (B1 fix: non-fatal zone does not kill deploy)
    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}. Stderr tail:\n" + "\n".join(
        (proc.stderr or "").splitlines()[-20:]
    )

    # deploy-result.json = success
    result_file = deploy_harness["project_dir"] / ".deploy-snapshots" / "deploy-result.json"
    assert result_file.exists(), f"deploy-result.json not found at {result_file}"
    result = json.loads(result_file.read_text())
    assert result["status"] == "success", f"Expected status='success', got '{result['status']}': {result}"

    assert any("Deploy result: success" in line for line in (proc.stderr or "").splitlines()), (
        "Expected 'Deploy result: success' in stderr — B1 fix not active"
    )

    print(f"[IMP:9][test] PASS: B1 notify-missing → exit {proc.returncode}, status={result['status']}")


# endregion TEST_NOTIFY_MISSING
# 🧪 TRAP[TEST] · Regression · B1 notify-hook absent → success · Last fail: 2026-07-18 (pre-fix) · Remove if: B1 regression impossible (structural fix)


# region TEST_AUDIT_DENIED
def test_deploy_success_audit_unavailable(deploy_harness, caplog):
    """B1: audit.log unwritable → deploy-result.json=success, exit 0."""
    audit_dir = deploy_harness["tmp_path"] / "unwritable-audit"
    audit_dir.mkdir()
    audit_dir.chmod(0o444)

    proc = _run_deploy(
        deploy_harness,
        extra_env={
            "PLATFORM_LOG_DIR": str(audit_dir),
        },
    )
    found_imp9 = _check_ldd_trajectory(proc.stderr or "")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    assert proc.returncode == 0, f"Expected exit 0, got {proc.returncode}. Stderr tail:\n" + "\n".join(
        (proc.stderr or "").splitlines()[-20:]
    )

    result_file = deploy_harness["project_dir"] / ".deploy-snapshots" / "deploy-result.json"
    assert result_file.exists(), f"deploy-result.json not found at {result_file}"
    result = json.loads(result_file.read_text())
    assert result["status"] == "success", f"Expected status='success', got '{result['status']}': {result}"

    print(f"[IMP:9][test] PASS: B1 audit-denied → exit {proc.returncode}, status={result['status']}")


# endregion TEST_AUDIT_DENIED
# 🧪 TRAP[TEST] · Regression · B1 audit.log unwritable → success · Last fail: 2026-07-18 (pre-fix) · Remove if: B1 regression impossible (structural fix)


# region TEST_HEALTH_FAILS
def test_deploy_health_fails_exit_1(deploy_harness, caplog):
    """Negative B1: health-gate failure → exit 1, deploy-result.json=failed."""
    proc = _run_deploy(
        deploy_harness,
        extra_env={
            "FAKE_HEALTH_CHECK": "fail",
        },
    )
    found_imp9 = _check_ldd_trajectory(proc.stderr or "")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    # Exit 1 expected (health-gate failure is still fatal)
    assert proc.returncode != 0, f"Expected non-zero exit (health failed), got 0. Stderr:\n{proc.stderr}"

    result_file = deploy_harness["project_dir"] / ".deploy-snapshots" / "deploy-result.json"
    if result_file.exists():
        result = json.loads(result_file.read_text())
        assert result["status"] != "success", (
            f"Expected status!='success' for health failure, got '{result['status']}': {result}"
        )
        print(f"[IMP:9][test] PASS: health-fail → exit {proc.returncode}, status={result['status']}")
    else:
        print(f"[IMP:9][test] PASS: health-fail → exit {proc.returncode} (no result file)")

    assert any("Healthcheck FAILED" in line for line in (proc.stderr or "").splitlines()), (
        "Expected 'Healthcheck FAILED' in stderr for health-fail test"
    )


# endregion TEST_HEALTH_FAILS
# 🧪 TRAP[TEST] · Negative · B1 health-gate still fails · Last fail: N/A · Remove if: health-gate logic changes fundamentally
