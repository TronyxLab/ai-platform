#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-deliver platform-deliver handle_deliver tar-gz stdin whitelist oversize path-traversal project-validation D2
# STRUCTURE: ▶ source deploy-project.sh → ∋ parse_ssh_command → ◇ platform-deliver dispatch → ⊕ handle_deliver ┌stdin cap┐┌extract┐┌validate┐┌atomic mv┐ → ∑ audit_log START|SUCCESS|FAIL → ⎋ exit0|exit1
# region MODULE_CONTRACT
## @purpose  Contract tests for deploy-project.sh platform-deliver verb (D2). Verifies stdin
##           tar.gz delivery: size cap (1 MiB), whitelist validation (docker-compose.yml,
##           compose.yaml, ai-platform.yaml, .env.platform), path traversal rejection,
##           project name validation, atomic move to PROJECTS_BASE/<project>.
## @scope    Five test cases per $TEST_SPEC: valid payload, path traversal rejection,
##           non-whitelisted file rejection, oversize rejection, invalid project name.
##           All use subprocess isolation with tmp_path as PROJECTS_BASE.
## @invariants
##   - stdin tar.gz ≤ 1 MiB with whitelist top-level files → exit 0, DELIVER-SUCCESS
##   - stdin tar.gz > 1 MiB → exit 1, DELIVER-FAIL, PROJECT_DIR not modified
##   - tar with ../evil or absolute path → exit 1, DELIVER-FAIL
##   - tar with non-whitelisted files (extra.sh) → exit 1, DELIVER-FAIL
##   - project name with '/' or '..' → exit 1, DELIVER-FAIL
##   - PROJECTS_BASE from env (tmp_path) — Zero Hardcode Rule
##   - All tests use tmp_path for isolation
## @rationale
##   Q: Why test deliver via subprocess not pytest-bash?
##   A: The script source+dependency chain (lib/audit_logging.sh, lib/logging.sh, etc.)
##      makes function extraction fragile. Subprocess sourcing the entire script and
##      dispatching via SSH_ORIGINAL_COMMAND is more faithful to production execution.
##   Q: Why binary input (tar.gz) on stdin?
##   A: Production delivery happens via `tar czf - ... | ssh ci-deploy@node "platform-deliver <project>"`.
##      Binary stdin is the real contract — testing with text pipes would miss encoding edge cases.
## @changes CREATED: 2026-07-17 | T2: Contract tests — deploy-project.sh platform-deliver verb
# endregion MODULE_CONTRACT

import io
import os
import pathlib
import subprocess
import tarfile

import pytest
from conftest import assert_ldd_stderr

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DEPLOY_SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, "core", "internal", "deploy", "deploy-project.sh")

# ── Constants ──────────────────────────────────────────────────────────────

ONE_MIB: int = 1048576

# ── Helpers ─────────────────────────────────────────────────────────────────


# region FUNC__make_tar
## @purpose  Create in-memory tar.gz from list of (arcname, content_bytes) tuples.
## @io       ⇥ [(arcname, bytes)] → ⎋ bytes (gzipped tar)
## @complexity O(n) where n = total content size
def _make_tar(files: list[tuple[str, bytes]]) -> bytes:
    """Create an in-memory gzipped tar archive from (arcname, contents) pairs.

    ## @purpose  Build deterministic test payloads without filesystem side effects.
    ## @io       ⇥ [(arcname, contents)] → ⎋ bytes (tar.gz)
    ## @complexity O(n)
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname, content in files:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


# endregion FUNC__make_tar


# region FUNC__run_deliver
## @purpose  Source deploy-project.sh, set SSH_ORIGINAL_COMMAND to platform-deliver,
##           pipe tar.gz data as stdin, and capture result. Models the production
##           invocation: `tar czf - ... | ssh ci-deploy@node "platform-deliver <project>"`.
## @io       ⇥ (tmp_path, project_name, tar_data, env_override) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 15s timeout
def _run_deliver(
    tmp_path: pathlib.Path,
    project_name: str,
    tar_data: bytes,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script = tmp_path / "test_deliver.sh"
    deploy_path_escaped = str(DEPLOY_SCRIPT_PATH)

    script_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # Mock logger to capture audit_log calls on stderr
        'logger() { local tag="$2"; shift 2; echo "[MOCK:logger] tag=" "$tag" "msg=$*" >&2; }\n'
        "export -f logger\n"
        f'source "{deploy_path_escaped}"\n'
        "trap - ERR EXIT\n"
        f'SSH_ORIGINAL_COMMAND="platform-deliver {project_name}"\n'
        'log_imp 9 "test" "Calling parse_ssh_command for platform-deliver"\n'
        "parse_ssh_command\n"
        # Should not reach here — handle_deliver calls exit 0|1
        'echo "UNEXPECTED_REACHED_END" >&2\n'
    )
    script.write_text(script_content)
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    full_env["PROJECTS_BASE"] = str(tmp_path)
    if env:
        full_env.update(env)

    result = subprocess.run(
        ["bash", str(script)],
        input=tar_data,
        capture_output=True,
        timeout=15,
        env=full_env,
    )
    # Normalize bytes to str for assertion convenience
    result.stdout = result.stdout.decode("utf-8", errors="replace")
    result.stderr = result.stderr.decode("utf-8", errors="replace")
    return result


# endregion FUNC__run_deliver


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: platform-deliver
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_valid_payload
@pytest.mark.contract
## @purpose  platform-deliver with valid tar.gz → whitelist files in PROJECT_DIR, DELIVER-SUCCESS
## @scenario  tar.gz with docker-compose.yml + ai-platform.yaml → exit 0, files exist, DELIVER-SUCCESS audit
def test_deliver_valid_payload(tmp_path: pathlib.Path) -> None:
    """# ▶ tar.gz with whitelist files → handle_deliver → ◇ extract + validate + mv → ⎋ DELIVER-SUCCESS"""
    tar_data = _make_tar(
        [
            ("docker-compose.yml", b"version: '3'\nservices:\n  web:\n    image: nginx\n"),
            ("ai-platform.yaml", b"service: web\nmonitoring:\n  host_port: 8080\n"),
        ]
    )

    result = _run_deliver(tmp_path, "myproject", tar_data)

    # Print LDD trajectory before assertions
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            parts = line.split("[IMP:")[1].split("]", 1)
            try:
                imp_level = int(parts[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                print(line)
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr={result.stderr[:1000]}"

    # Verify files were delivered to PROJECT_DIR
    proj_dir = tmp_path / "myproject"
    assert proj_dir.is_dir(), f"Project directory not created: {proj_dir}"
    assert (proj_dir / "docker-compose.yml").is_file(), "docker-compose.yml not found"
    assert (proj_dir / "ai-platform.yaml").is_file(), "ai-platform.yaml not found"

    # Verify LDD lifecycle markers (audit_log output is masked by 2>/dev/null in production)
    assert "=== platform-deliver START: myproject ===" in result.stderr, (
        "Missing START marker in stderr:\n" + result.stderr
    )
    assert "=== platform-deliver DONE (success) ===" in result.stderr, (
        "Missing DONE marker in stderr:\n" + result.stderr
    )
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"

    # LDD verification: IMP:9 must be present
    found_imp9 = any("[IMP:9]" in line for line in result.stderr.splitlines())
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    print("[IMP:9][test_deliver_valid_payload] PASS: files delivered, audit logged")


# endregion FUNC_test_deliver_valid_payload


# region FUNC_test_deliver_rejects_path_traversal
@pytest.mark.contract
## @purpose  platform-deliver rejects tar.gz with path traversal (../evil or absolute path)
## @scenario  tar with ../evil entry → exit 1, DELIVER-FAIL, PROJECT_DIR not created
def test_deliver_rejects_path_traversal(tmp_path: pathlib.Path) -> None:
    """# ▶ tar.gz with ../evil entry → handle_deliver → ◇ subdir file detected → ⎋ exit 1, no dir created"""
    # Create a tar with a path traversal entry
    # When extracted to tmp_dir, ../evil resolves outside tmp_dir
    tar_data = _make_tar(
        [
            ("../evil", b"malicious content"),
            ("docker-compose.yml", b"version: '3'\n"),
        ]
    )

    result = _run_deliver(tmp_path, "safe_project", tar_data)

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            parts = line.split("[IMP:")[1].split("]", 1)
            try:
                imp_level = int(parts[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                print(line)
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"

    # PROJECT_DIR should NOT exist (script cleaned up and didn't create it)
    proj_dir = tmp_path / "safe_project"
    assert not proj_dir.exists(), f"Project directory should NOT exist after failed deliver: {proj_dir}"

    # Verify LDD failure markers
    assert "=== platform-deliver START: safe_project ===" in result.stderr
    assert "FATAL:" in result.stderr, "Expected FATAL log line in stderr:\n" + result.stderr

    # LDD verification
    found_imp9 = any("[IMP:9]" in line for line in result.stderr.splitlines())
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    print("[IMP:9][test_deliver_rejects_path_traversal] PASS: path traversal rejected")


# endregion FUNC_test_deliver_rejects_path_traversal


# region FUNC_test_deliver_skips_non_whitelisted
@pytest.mark.contract
## @purpose  platform-deliver rejects tar.gz with non-whitelisted files
## @scenario  tar with docker-compose.yml + extra.sh → exit 1, DELIVER-FAIL, only whitelist NOT delivered
def test_deliver_skips_non_whitelisted(tmp_path: pathlib.Path) -> None:
    """# ▶ tar.gz with docker-compose.yml + extra.sh → handle_deliver → ◇ non-whitelisted detected → ⎋ exit 1"""
    tar_data = _make_tar(
        [
            ("docker-compose.yml", b"version: '3'\n"),
            ("extra.sh", b"#!/bin/sh\necho malicious\n"),
        ]
    )

    result = _run_deliver(tmp_path, "myproject", tar_data)

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            parts = line.split("[IMP:")[1].split("]", 1)
            try:
                imp_level = int(parts[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                print(line)
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "non-whitelisted" in result.stderr, f"Expected 'non-whitelisted' in stderr:\n{result.stderr}"

    # PROJECT_DIR should NOT exist (validation failed before mkdir)
    proj_dir = tmp_path / "myproject"
    assert not proj_dir.exists(), f"Project directory should NOT exist after failed deliver: {proj_dir}"

    # LDD verification
    found_imp9 = any("[IMP:9]" in line for line in result.stderr.splitlines())
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    print("[IMP:9][test_deliver_skips_non_whitelisted] PASS: non-whitelisted file rejected")


# endregion FUNC_test_deliver_skips_non_whitelisted


# region FUNC_test_deliver_rejects_oversize
@pytest.mark.contract
## @purpose  platform-deliver rejects stdin stream exceeding 1 MiB hard cap
## @scenario  pipe >1 MiB of data → exit 1, DELIVER-FAIL, nothing written to PROJECT_DIR
def test_deliver_rejects_oversize(tmp_path: pathlib.Path) -> None:
    """# ▶ stdin >1 MiB → handle_deliver ┌dd reads 1025 blocks > 1048576┐ → ◇ oversize detected → ⎋ exit 1"""
    # Send significantly over 1 MiB of raw data — use 2 MiB to clearly exceed
    # the dd bs=1024 count=1025 buffer (max 1,049,600 bytes).
    # Size check happens BEFORE tar extraction.
    oversize_data = b"x" * (ONE_MIB * 2)

    result = _run_deliver(tmp_path, "oversize_project", oversize_data)

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            parts = line.split("[IMP:")[1].split("]", 1)
            try:
                imp_level = int(parts[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                print(line)
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "exceeds 1 MiB" in result.stderr, f"Expected 'exceeds 1 MiB' in stderr:\n{result.stderr}"

    # PROJECT_DIR should NOT exist
    proj_dir = tmp_path / "oversize_project"
    assert not proj_dir.exists(), f"Project directory should NOT exist after oversize: {proj_dir}"

    # LDD verification
    found_imp9 = any("[IMP:9]" in line for line in result.stderr.splitlines())
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    print("[IMP:9][test_deliver_rejects_oversize] PASS: oversize payload rejected")


# endregion FUNC_test_deliver_rejects_oversize


# region FUNC_test_deliver_invalid_project_name
@pytest.mark.contract
## @purpose  platform-deliver rejects project names with '/' or '..' (path traversal via name)
## @scenario  SSH_ORIGINAL_COMMAND="platform-deliver ../x" → exit 1, DELIVER-FAIL
def test_deliver_invalid_project_name(tmp_path: pathlib.Path) -> None:
    """# ▶ SSH_ORIGINAL_COMMAND="platform-deliver ../x" → parse_ssh_command → ◇ _validate_project_name FAIL → ⎋ exit 1"""
    tar_data = _make_tar(
        [
            ("docker-compose.yml", b"version: '3'\n"),
        ]
    )

    result = _run_deliver(tmp_path, "../x", tar_data)

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            parts = line.split("[IMP:")[1].split("]", 1)
            try:
                imp_level = int(parts[0])
                if imp_level >= 7:
                    print(line)
            except (ValueError, IndexError):
                print(line)
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "invalid characters" in result.stderr, f"Expected 'invalid characters' in stderr:\n{result.stderr[:500]}"
    assert ".." in result.stderr, "Expected mention of '..' in validation error"

    # PROJECT_DIR should NOT exist
    # ../x resolves to parent of tmp_path — make sure nothing was created there
    parent_of_tmp = tmp_path.parent / "x"
    assert not parent_of_tmp.exists(), f"Directory escaped: {parent_of_tmp} exists despite invalid project name"

    # LDD verification
    found_imp9 = any("[IMP:9]" in line for line in result.stderr.splitlines())
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    print("[IMP:9][test_deliver_invalid_project_name] PASS: project name '../x' rejected")


# endregion FUNC_test_deliver_invalid_project_name
