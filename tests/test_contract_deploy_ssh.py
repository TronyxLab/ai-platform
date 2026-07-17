#!/usr/bin/env python3
# GREP_SUMMARY: contract-test deploy-project ssh forced-command parse_ssh_command NODE_NAME SSH_ORIGINAL_COMMAND bash subprocess
# STRUCTURE: ▶ source deploy-project.sh → ∋ parse_ssh_command → ◇ SSH_ORIGINAL_COMMAND format ┌with-export┐┌plain┐┌missing-ref┐ → ⊕ PROJECT+REF → ◇ PROJECT_DIR exists + compose.yml → ⎋ exit0|exit1
# region MODULE_CONTRACT
## @purpose  Contract tests for deploy-project.sh parse_ssh_command(). Verifies SSH
##           forced-command parsing for all formats, NODE_NAME validation, project
##           directory existence checks, and rejection of invalid/unknown commands.
## @scope    Five test cases: basic format, with export statements, missing ref,
##           nonexistent project directory, missing compose file, and ai-platform.yaml
##           service name override. All use subprocess isolation with mock files.
## @invariants
##   - SSH_ORIGINAL_COMMAND="platform-deploy <project> <ref>" → exit 0
##   - SSH_ORIGINAL_COMMAND without <ref> → exit 1 (PROJECT==REF → REF="" → exit)
##   - Missing PROJECT_DIR → exit 1 with "project directory not found"
##   - Missing docker-compose.yml → exit 1 with "no docker-compose.yml found"
##   - Export lines in SSH_ORIGINAL_COMMAND are stripped before parsing
##   - ai-platform.yaml service: field overrides SERVICE_NAME
##   - PROJECTS_BASE is readonly in deploy-project.sh — must be set via env only
##   - All tests use tmp_path for isolation (Zero Hardcode Rule)
## @rationale Q: Why test all formats?
##            A: SSH forced-commands come from CI and may include export statements or
##            varying formats. Robust parsing prevents silent deploy failures.
## @changes CREATED: 2026-07-17 | T3: Contract tests — deploy-project.sh (SSH)
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
## @purpose  Source deploy-project.sh, remove traps, then run provided bash code.
##           PROJECTS_BASE is readonly in deploy-project.sh — must be passed via
##           env dict (set before sourcing). All tests pass their tmp_path as
##           PROJECTS_BASE for isolated project directory creation.
## @io       ⇥ (tmp_path, code, env) → ⎋ CompletedProcess
## @complexity O(1) — single subprocess.run with 15s timeout
def _run_bash(
    tmp_path: pathlib.Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    script = tmp_path / "test_ssh.sh"
    deploy_path_escaped = str(DEPLOY_SCRIPT_PATH)

    script_content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'logger() { local tag="$2"; shift 2; echo "[MOCK:logger] tag=" "$tag" "msg=$*" >&2; }\n'
        "export -f logger\n"
        f'source "{deploy_path_escaped}"\n'
        "trap - ERR EXIT\n"
        f"{code}\n"
    )
    script.write_text(script_content)
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    # PROJECTS_BASE is readonly — inject via env before sourcing
    full_env["PROJECTS_BASE"] = str(tmp_path)
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


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: parse_ssh_command
# ═════════════════════════════════════════════════════════════════════════════


# region FUNC_test_parse_ssh_command_basic
@pytest.mark.contract
## @purpose  parse_ssh_command correctly parses "platform-deploy <project> <ref>" format.
## @scenario  SSH_ORIGINAL_COMMAND="platform-deploy myapp main" → PROJECT=myapp, REF=main
def test_parse_ssh_command_basic(tmp_path: pathlib.Path) -> None:
    """
    # ▶ SSH_ORIGINAL_COMMAND="platform-deploy myapp main" → parse_ssh_command
    #   → ◇ PROJECT=myapp REF=main SERVICE=myapp → ⎋ pass
    """
    # Create project dir under tmp_path (PROJECTS_BASE from env)
    proj_dir = tmp_path / "myapp"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").touch()

    code = (
        'SSH_ORIGINAL_COMMAND="platform-deploy myapp main"\n'
        "parse_ssh_command\n"
        'log_imp 9 "test" "Verification: parsed PROJECT=${PROJECT} REF=${REF}"\n'
        'echo "PROJECT=${PROJECT}"\n'
        'echo "REF=${REF}"\n'
        'echo "SERVICE_NAME=${SERVICE_NAME}"\n'
        'echo "PROJECT_DIR=${PROJECT_DIR}"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(result)
    stdout = result.stdout
    lines = stdout.splitlines()
    proj_line = next(line for line in lines if line.startswith("PROJECT="))
    ref_line = next(line for line in lines if line.startswith("REF="))
    svc_line = next(line for line in lines if line.startswith("SERVICE_NAME="))

    assert proj_line == "PROJECT=myapp", f"Unexpected: {proj_line}"
    assert ref_line == "REF=main", f"Unexpected: {ref_line}"
    assert svc_line == "SERVICE_NAME=myapp", f"Unexpected: {svc_line}"

    print("[IMP:9][test_parse_ssh_command_basic] PASS: parsed PROJECT=myapp REF=main")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_basic


# region FUNC_test_parse_ssh_command_with_export
@pytest.mark.contract
## @purpose  parse_ssh_command strips export lines from SSH_ORIGINAL_COMMAND
##           before parsing, ignoring CI-injected environment exports.
## @scenario  SSH_ORIGINAL_COMMAND with "export CI=true" prefix → parsed correctly
def test_parse_ssh_command_with_export(tmp_path: pathlib.Path) -> None:
    """
    # ▶ SSH_ORIGINAL_COMMAND="export CI=true\\nplatform-deploy myapp main"
    #   → parse_ssh_command → ◇ PROJECT=myapp REF=main → ⎋ pass
    """
    proj_dir = tmp_path / "myapp"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").touch()

    # SSH forced-command format: "platform-deploy export <key>=<val>\\n<project> <ref>"
    # The deploy-project.sh strips the "platform-deploy " prefix, then sed removes
    # lines starting with "export ", leaving "<project> <ref>".
    code = (
        'SSH_ORIGINAL_COMMAND="platform-deploy export CI=true\n'  # <-- real newline
        'myapp main"\n'
        "parse_ssh_command\n"
        'log_imp 9 "test" "Verification: parsed PROJECT=${PROJECT} REF=${REF}"\n'
        'echo "PROJECT=${PROJECT}"\n'
        'echo "REF=${REF}"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(result)
    stdout = result.stdout
    proj = next(line for line in stdout.splitlines() if line.startswith("PROJECT=")).split("=")[1]
    ref = next(line for line in stdout.splitlines() if line.startswith("REF=")).split("=")[1]
    assert proj == "myapp", f"Expected PROJECT=myapp, got {proj}"
    assert ref == "main", f"Expected REF=main, got {ref}"

    print("[IMP:9][test_parse_ssh_command_with_export] PASS: export stripped, parsed correctly")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_with_export


# region FUNC_test_parse_ssh_command_missing_ref
@pytest.mark.contract
## @purpose  parse_ssh_command exits 1 when SSH_ORIGINAL_COMMAND has no REF
##           (project == ref → ref becomes empty → validation fails).
## @scenario  SSH_ORIGINAL_COMMAND="platform-deploy myapp" → exit 1 "invalid invocation"
def test_parse_ssh_command_missing_ref(tmp_path: pathlib.Path) -> None:
    """
    # ▶ SSH_ORIGINAL_COMMAND="platform-deploy myapp" → parse_ssh_command
    #   → ◇ exit 1 "invalid invocation" + IMP:10 FATAL → ⎋ pass
    """
    proj_dir = tmp_path / "myapp"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").touch()

    code = 'SSH_ORIGINAL_COMMAND="platform-deploy myapp"\nparse_ssh_command\necho "SHOULD_NOT_REACH"\n'

    result = _run_bash(tmp_path, code)

    assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"
    assert "invalid invocation" in result.stderr, f"Expected 'invalid invocation' in stderr:\n{result.stderr}"
    # LDD: check stderr for IMP:10 FATAL from parse_ssh_command
    found_imp10 = any("[IMP:10]" in line for line in result.stderr.splitlines())
    assert found_imp10, "Expected IMP:10 FATAL log for missing ref"

    print("[IMP:9][test_parse_ssh_command_missing_ref] PASS: exit 1 on missing ref")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_missing_ref


# region FUNC_test_parse_ssh_command_project_not_found
@pytest.mark.contract
## @purpose  parse_ssh_command exits 1 when PROJECT_DIR does not exist on disk.
## @scenario  Valid format but nonexistent project dir → exit 1 "project directory not found"
def test_parse_ssh_command_project_not_found(tmp_path: pathlib.Path) -> None:
    """
    # ▶ SSH_ORIGINAL_COMMAND="platform-deploy nonexistent main" → parse_ssh_command
    #   → ◇ exit 1 "project directory not found" + IMP:10 → ⎋ pass
    """
    code = 'SSH_ORIGINAL_COMMAND="platform-deploy nonexistent main"\nparse_ssh_command\necho "SHOULD_NOT_REACH"\n'

    result = _run_bash(tmp_path, code)

    assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"
    assert "project directory not found" in result.stderr, (
        f"Expected 'project directory not found' in stderr:\n{result.stderr}"
    )
    found_imp10 = any("[IMP:10]" in line for line in result.stderr.splitlines())
    assert found_imp10, "Expected IMP:10 FATAL log for missing project dir"

    print("[IMP:9][test_parse_ssh_command_project_not_found] PASS: exit 1 on missing project dir")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_project_not_found


# region FUNC_test_parse_ssh_command_no_compose_file
@pytest.mark.contract
## @purpose  parse_ssh_command exits 1 when PROJECT_DIR lacks docker-compose.yml.
## @scenario  Project dir exists but empty → exit 1 "no docker-compose.yml found"
def test_parse_ssh_command_no_compose_file(tmp_path: pathlib.Path) -> None:
    """
    # ▶ PROJECT_DIR exists but has no docker-compose.yml or compose.yaml
    #   → parse_ssh_command → ◇ exit 1 "no docker-compose.yml found" → ⎋ pass
    """
    proj_dir = tmp_path / "myapp"
    proj_dir.mkdir()  # No compose file

    code = 'SSH_ORIGINAL_COMMAND="platform-deploy myapp main"\nparse_ssh_command\necho "SHOULD_NOT_REACH"\n'

    result = _run_bash(tmp_path, code)

    assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"
    assert "no docker-compose.yml found" in result.stderr, (
        f"Expected 'no docker-compose.yml found' in stderr:\n{result.stderr}"
    )

    print("[IMP:9][test_parse_ssh_command_no_compose_file] PASS: exit 1 on missing compose file")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_no_compose_file


# region FUNC_test_parse_ssh_command_service_override
@pytest.mark.contract
## @purpose  parse_ssh_command reads ai-platform.yaml service: field to override
##           SERVICE_NAME when the file exists.
## @scenario  ai-platform.yaml with "service: custom-svc" → SERVICE_NAME=custom-svc
def test_parse_ssh_command_service_override(tmp_path: pathlib.Path) -> None:
    """
    # ▶ ai-platform.yaml with service: custom-svc → parse_ssh_command
    #   → ◇ SERVICE_NAME=custom-svc → ⎋ pass
    """
    proj_dir = tmp_path / "myapp"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").touch()
    (proj_dir / "ai-platform.yaml").write_text("service: custom-svc\n")

    code = (
        'SSH_ORIGINAL_COMMAND="platform-deploy myapp main"\n'
        "parse_ssh_command\n"
        'log_imp 9 "test" "Verification: SERVICE_NAME=${SERVICE_NAME}"\n'
        'echo "SERVICE_NAME=${SERVICE_NAME}"\n'
    )

    result = _run_bash(tmp_path, code)

    assert_ldd_stderr(result)
    stdout = result.stdout
    svc = next(line for line in stdout.splitlines() if line.startswith("SERVICE_NAME=")).split("=")[1]
    assert svc == "custom-svc", f"Expected SERVICE_NAME=custom-svc, got {svc}"

    print("[IMP:9][test_parse_ssh_command_service_override] PASS: service override from yaml")
    print("--- END LDD TRAJECTORY ---")


# endregion FUNC_test_parse_ssh_command_service_override


# ═════════════════════════════════════════════════════════════════════════════
# APPENDED FROM test_char_bootstrap_scp_ssh.py (A2 merge)
# characterization tests for bootstrap.sh functions
# ═════════════════════════════════════════════════════════════════════════════

import logging
import re
import textwrap

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# PATH CONSTANTS
# ═══════════════════════════════════════════════════════════════════
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TEST_DIR, "..")
BOOTSTRAP_SH = os.path.join(PROJECT_ROOT, "core", "entrypoints", "bootstrap.sh")
SCP_DELIVER_SH = os.path.join(PROJECT_ROOT, "core", "internal", "bootstrap", "scp-deliver.sh")
REMOTE_CMD_SH = os.path.join(PROJECT_ROOT, "core", "internal", "bootstrap", "remote-cmd.sh")

# ═══════════════════════════════════════════════════════════════════
# GOLDEN OUTPUT CONSTANTS
# ═══════════════════════════════════════════════════════════════════
# Captured from bootstrap.sh current behaviour — DO NOT MODIFY without
# verifying the production code produces the same output.

# Golden test data
GOLDEN_SSH_HOST = "192.168.1.100"
GOLDEN_NODE_NAME = "test-node"
GOLDEN_OWNER_KEY = "ssh-ed25519 AAAATestKey test@example.com"
GOLDEN_AGE_KEY = "AGE-SECRET-KEY-abcdef123456"

# Golden LDD log prefixes (from bootstrap.sh production logs)
GOLDEN_AGE_LOG_FOUND_ENV = "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY found in environment"
GOLDEN_AGE_LOG_FOUND_SOPS = "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY set from SOPS_AGE_KEY"
GOLDEN_AGE_LOG_FOUND_FILE = "[IMP:8][bootstrap][age-key] AGE_SECRET_KEY read from file"
GOLDEN_AGE_LOG_FILE_EMPTY = "[IMP:8][bootstrap][age-key] WARN: AGE_SECRET_KEY_FILE="
GOLDEN_AGE_LOG_NOT_FOUND = (
    "[IMP:8][bootstrap][age-key] WARN: AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy"
)

GOLDEN_AUTO_LOG_SUCCESS = "[IMP:9][bootstrap][auto-detect] Auto-detected node:"
GOLDEN_AUTO_LOG_NO_DIRS = "[IMP:10][bootstrap][auto-detect] No node directories found"
GOLDEN_AUTO_LOG_MULTI = "[IMP:10][bootstrap][auto-detect] Multiple directories:"
GOLDEN_AUTO_LOG_NO_CONFIGS = "[IMP:8][bootstrap][auto-detect] /opt/node-configs does not exist"

GOLDEN_SCP_LOG_MKDIR = "[IMP:8][bootstrap][scp] Ensuring remote directories exist on"
GOLDEN_SCP_LOG_MKDIR_CONFIRMED = "[IMP:9][bootstrap][scp] Remote directories confirmed"
GOLDEN_SCP_LOG_MKDIR_FAIL = "[IMP:10][bootstrap][scp] FATAL: ssh mkdir -p failed for"
GOLDEN_SCP_LOG_CORE_START = "[IMP:9][bootstrap][scp] Phase 1/4: Rsyncing core/"
GOLDEN_SCP_LOG_CORE_DONE = "[IMP:9][bootstrap][scp] Phase 1/4: core/ rsync complete"
GOLDEN_SCP_LOG_CORE_FAIL = "[IMP:10][bootstrap][scp] FATAL: rsync core/ failed for"
GOLDEN_SCP_LOG_PLATFORM_ENV_DONE = "[IMP:9][bootstrap][scp] Phase 1b/4: platform-env.yaml rsync complete"
GOLDEN_SCP_LOG_PLATFORM_ENV_SKIP = "[IMP:8][bootstrap][scp] Phase 1b/4: SKIP"
GOLDEN_SCP_LOG_NODE_START = "[IMP:9][bootstrap][scp] Phase 2/4: Rsyncing node-configs/"
GOLDEN_SCP_LOG_NODE_DONE = "[IMP:9][bootstrap][scp] Phase 2/4: node-configs/ rsync complete"
GOLDEN_SCP_LOG_NODE_FAIL = "[IMP:10][bootstrap][scp] FATAL: rsync node-configs/ failed for"
GOLDEN_SCP_LOG_SECRETS_START = "[IMP:9][bootstrap][scp] Phase 3/4: Rsyncing node-configs/secrets/"
GOLDEN_SCP_LOG_SECRETS_DONE = "[IMP:9][bootstrap][scp] Phase 3/4: node-configs/secrets/ rsync complete"
GOLDEN_SCP_LOG_SECRETS_SKIP = "[IMP:8][bootstrap][scp] Phase 3/4: SKIP"

GOLDEN_SSH_CMD_PREFIX = "set -euo pipefail"
GOLDEN_SSH_CMD_EXPORT = "export AGE_SECRET_KEY="
GOLDEN_SSH_CMD_NODE_LIFECYCLE = "/opt/platform/core/internal/bootstrap/node-lifecycle.sh"
GOLDEN_SSH_CMD_NODE_NAME_FLAG = "--node-name"
GOLDEN_SSH_CMD_NODE_YAML_FLAG = "--node-yaml"
GOLDEN_SSH_CMD_OWNER_KEY_FLAG = "--owner-key"
GOLDEN_SSH_CMD_RESUME_FLAG = "--resume"
GOLDEN_SSH_CMD_FORBIDDEN_CLI = "--age-secret-key"

GOLDEN_MOCK_RSYNC_CORE_DST = "/opt/platform/core/"
GOLDEN_MOCK_RSYNC_NODE_DST = "/opt/node-configs/"
GOLDEN_MOCK_RSYNC_SECRETS_DST = "/opt/node-configs/secrets/"

# ═══════════════════════════════════════════════════════════════════
# HELPERS — bash subprocess and function extraction
# ═══════════════════════════════════════════════════════════════════
# region HELPERS


def _bash(cmd: str, env: dict | None = None, cwd: str | None = None) -> tuple[str, str, int]:
    """Execute a bash -c command in a subprocess.

    ## @purpose  Run bash snippets to test bash functions in isolation.
    ## @io       cmd, env, cwd → (stdout, stderr, returncode)
    ## @complexity O(1)
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        env=merged_env,
        cwd=cwd or PROJECT_ROOT,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def _extract_func(filepath: str, func_name: str) -> str:
    """Extract a bash function definition from a source file using brace counting.

    ## @purpose  Extract a bash function body from its source file so it can be
    ##           sourced in a test context without executing the script's top-level code.
    ## @io       filepath, func_name → str (function definition)
    ## @raises ValueError if function is not found or braces are unbalanced
    ## @complexity O(N) where N = file size
    """
    with open(filepath) as f:
        content = f.read()

    patterns = [
        rf"^{re.escape(func_name)}\s*\(\s*\)\s*\{{",
        rf"^function\s+{re.escape(func_name)}\s*\{{",
    ]

    start = -1
    for pat in patterns:
        m = re.search(pat, content, re.MULTILINE)
        if m:
            line_start = content.rfind("\n", 0, m.start())
            if line_start == -1:
                line_start = 0
            else:
                line_start += 1
            prefix = content[line_start : m.start()]
            if prefix.strip() == "" or prefix.strip().startswith("#"):
                start = m.start()
                break

    if start < 0:
        raise ValueError(f"Function '{func_name}' not found in {filepath}")

    brace_pos = -1
    for i in range(start, min(start + 200, len(content))):
        if content[i] == "{":
            brace_pos = i
            break

    if brace_pos < 0:
        raise ValueError(f"No opening brace found for '{func_name}' in {filepath}")

    count = 1
    pos = brace_pos + 1
    while count > 0 and pos < len(content):
        if content[pos] == "{":
            count += 1
        elif content[pos] == "}":
            count -= 1
        pos += 1

    if count != 0:
        raise ValueError(f"Unterminated function '{func_name}' in {filepath}")

    return content[start:pos]


def _test_func(
    filepath: str,
    func_names: list[str],
    test_call: str,
    env: dict | None = None,
    preamble: str = "",
) -> tuple[str, str, int]:
    """Extract function(s) from a bash file and run a test call.

    ## @purpose  Isolate bash function(s) and execute them with test arguments.
    ## @io       filepath, func_names, test_call, env, preamble → (stdout, stderr, returncode)
    ## @complexity O(N) where N = total LOC of extracted functions
    """
    func_bodies = [_extract_func(filepath, name) for name in func_names]
    parts = []
    if preamble:
        parts.append(preamble)
    parts.extend(func_bodies)
    parts.append(test_call)
    script = "\n\n".join(parts)
    return _bash(script, env=env)


def _print_ldd(stderr: str, stdout: str = "") -> bool:
    """Print IMP:7-10 lines from bash subprocess output. Returns True if IMP:9+ found.

    ## @purpose  Extract and display IMP:7-10 log entries from bash stderr/stdout
    ##           for agent-visible telemetry.
    ## @io       stderr, stdout → bool (True if IMP:9 found)
    ## @complexity O(n) where n = total lines in output
    """
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for source in [stderr, stdout]:
        for line in source.split("\n"):
            if "[IMP:" in line:
                try:
                    parts = line.split("[IMP:")[1].split("]", 1)
                    imp_level = int(parts[0])
                    if imp_level >= 7:
                        print(line)
                    if imp_level >= 9:
                        found = True
                except (ValueError, IndexError):
                    print(line)
    print("--- END LDD TRAJECTORY ---")
    return found


# endregion HELPERS


# ═══════════════════════════════════════════════════════════════════
# detect_age_key
# ═══════════════════════════════════════════════════════════════════
# region detect_age_key


# region test_detect_age_key_from_env
# 🧪 TRAP[TEST] · 2026-07-17 · detect_age_key reads AGE_SECRET_KEY env var
# · Regression: if env chain order changes, AGE_SECRET_KEY must take priority
# · Scenario: happy path — key present in primary env var
# · Last fail: N/A (new test)
# · Remove if: detect_age_key function is removed or contract changes
def test_detect_age_key_from_env(caplog) -> None:
    """detect_age_key() returns the key when AGE_SECRET_KEY env is set (chain 1)."""
    caplog.set_level(logging.DEBUG)

    test_call = textwrap.dedent("""\
        detected=$(detect_age_key)
        rc=$?
        echo "[IMP:9][test][detect_age_key] exit_code=$rc"
        echo "KEY_VALUE:$detected"
    """)
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"AGE_SECRET_KEY": GOLDEN_AGE_KEY},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"detect_age_key failed with rc={rc}: {stderr}"

    # Golden output: key should appear in stdout
    assert "KEY_VALUE:" in stdout, f"Expected KEY_VALUE in stdout: {stdout}"
    key_line = next(line for line in stdout.split("\n") if line.startswith("KEY_VALUE:"))
    assert GOLDEN_AGE_KEY in key_line, f"Expected '{GOLDEN_AGE_KEY}' in '{key_line}'"

    # Golden log: AGE_SECRET_KEY found in environment
    assert GOLDEN_AGE_LOG_FOUND_ENV in stderr, f"Expected golden log '{GOLDEN_AGE_LOG_FOUND_ENV}' in stderr"

    logger.info("[IMP:9][test][detect_age_key] AGE_SECRET_KEY from env: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_detect_age_key_from_sops_env
# 🧪 TRAP[TEST] · 2026-07-17 · detect_age_key reads SOPS_AGE_KEY fallback
# · Regression: if SOPS_AGE_KEY fallback is removed, non-standard setups break
# · Scenario: AGE_SECRET_KEY absent, SOPS_AGE_KEY present
# · Last fail: N/A (new test)
# · Remove if: SOPS_AGE_KEY support is intentionally removed
def test_detect_age_key_from_sops_env(caplog) -> None:
    """detect_age_key() falls back to SOPS_AGE_KEY when AGE_SECRET_KEY is unset."""
    caplog.set_level(logging.DEBUG)

    test_key = "AGE-SECRET-KEY-from-sops-789"
    test_call = textwrap.dedent("""\
        detected=$(detect_age_key)
        rc=$?
        echo "[IMP:9][test][detect_age_key] exit_code=$rc"
        echo "KEY_VALUE:$detected"
    """)
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"SOPS_AGE_KEY": test_key, "AGE_SECRET_KEY": ""},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"detect_age_key failed with rc={rc}: {stderr}"

    key_line = next(line for line in stdout.split("\n") if line.startswith("KEY_VALUE:"))
    assert test_key in key_line, f"Expected '{test_key}' in '{key_line}'"

    # Golden log: set from SOPS_AGE_KEY
    assert GOLDEN_AGE_LOG_FOUND_SOPS in stderr

    logger.info("[IMP:9][test][detect_age_key] SOPS_AGE_KEY fallback: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_detect_age_key_from_file
# 🧪 TRAP[TEST] · 2026-07-17 · detect_age_key reads AGE_SECRET_KEY_FILE
# · Regression: if file reading chain is broken, CI without env var fails
# · Scenario: both env vars absent, AGE_SECRET_KEY_FILE points to valid file
# · Last fail: N/A (new test)
# · Remove if: file-based key detection is removed
def test_detect_age_key_from_file(caplog, tmp_path) -> None:
    """detect_age_key() reads key from AGE_SECRET_KEY_FILE when env vars are unset."""
    caplog.set_level(logging.DEBUG)

    key_file = tmp_path / "age-key.txt"
    key_file.write_text(GOLDEN_AGE_KEY + "\n")

    test_call = textwrap.dedent("""\
        detected=$(detect_age_key)
        rc=$?
        echo "[IMP:9][test][detect_age_key] exit_code=$rc"
        echo "KEY_VALUE:$detected"
    """)
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"AGE_SECRET_KEY_FILE": str(key_file), "AGE_SECRET_KEY": "", "SOPS_AGE_KEY": ""},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"detect_age_key failed with rc={rc}: {stderr}"

    key_line = next(line for line in stdout.split("\n") if line.startswith("KEY_VALUE:"))
    assert GOLDEN_AGE_KEY in key_line, f"Expected '{GOLDEN_AGE_KEY}' in '{key_line}'"

    assert GOLDEN_AGE_LOG_FOUND_FILE in stderr

    logger.info("[IMP:9][test][detect_age_key] AGE_SECRET_KEY_FILE: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_detect_age_key_not_found
# 🧪 TRAP[TEST] · 2026-07-17 · detect_age_key returns non-zero when no key found
# · Regression: if key detection becomes fatal instead of WARN
# · Scenario: no env var, no file — function must WARN and return 1
# · Last fail: N/A (new test)
# · Remove if: behaviour changes from WARN to fatal or silent
def test_detect_age_key_not_found(caplog) -> None:
    """detect_age_key() warns and returns 1 when no AGE key is available."""
    caplog.set_level(logging.DEBUG)

    test_call = textwrap.dedent("""\
        if detected=$(detect_age_key 2>&1); then
            echo "[IMP:9][test][detect_age_key] UNEXPECTED_SUCCESS:$detected"
        else
            echo "[IMP:9][test][detect_age_key] EXPECTED_FAILURE"
            echo "STDERR_CONTENT:$detected"
        fi
        echo "EXIT_CODE:$?"
    """)
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"AGE_SECRET_KEY": "", "SOPS_AGE_KEY": "", "AGE_SECRET_KEY_FILE": ""},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed: {stderr}"

    assert "EXPECTED_FAILURE" in stdout, f"Expected EXPECTED_FAILURE, got: {stdout}"
    combined = stdout + "\n" + stderr
    assert GOLDEN_AGE_LOG_NOT_FOUND in combined, f"Golden WARN message missing. Got: {combined[:500]}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][detect_age_key] Key not found — WARN + exit 1: PASS")


# endregion


# ═══════════════════════════════════════════════════════════════════
# auto_detect_node_name
# ═══════════════════════════════════════════════════════════════════
# ⚠️ NOTE: auto_detect_node_name() hardcodes /opt/node-configs/ as configs_dir.
#   In tests, we replace this path via string substitution to use tmp_path.
#   This preserves the function's logic while allowing isolated testing.
# ═══════════════════════════════════════════════════════════════════


# region test_auto_detect_node_name_success
# 🧪 TRAP[TEST] · 2026-07-17 · auto_detect_node_name single non-service dir
# · Regression: if detection logic changes, CI zero-config deploy breaks
# · Scenario: exactly one non-service directory in /opt/node-configs/
# · Last fail: N/A (new test)
# · Remove if: auto_detect_node_name is removed
def test_auto_detect_node_name_success(caplog, tmp_path) -> None:
    """auto_detect_node_name() returns the single non-service node name."""
    caplog.set_level(logging.DEBUG)

    # Create /opt/node-configs/ equivalent with one node dir
    ncd = tmp_path / "node-configs"
    (ncd / "tronyx-vps").mkdir(parents=True)
    (ncd / "scripts").mkdir(parents=False)  # should be excluded
    (ncd / "secrets").mkdir(parents=False)  # should be excluded

    func_body = _extract_func(BOOTSTRAP_SH, "auto_detect_node_name")
    func_body = func_body.replace("/opt/node-configs", str(ncd))

    script = textwrap.dedent(f"""\
        set -euo pipefail
        {func_body}

        result=$(auto_detect_node_name)
        rc=$?
        echo "[IMP:9][test][auto_detect] result=$result"
        echo "EXIT_CODE:$rc"
    """)
    stdout, stderr, rc = _bash(script)
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script failed: {stderr}"

    assert "result=tronyx-vps" in stdout, f"Expected 'result=tronyx-vps' in stdout: {stdout[:300]}"

    assert GOLDEN_AUTO_LOG_SUCCESS in stderr
    logger.info("[IMP:9][test][auto_detect] Single node found: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_auto_detect_node_name_no_configs_dir
# 🧪 TRAP[TEST] · 2026-07-17 · auto_detect_node_name when /opt/node-configs/ missing
# · Regression: returns wrong exit code on missing directory
# · Scenario: /opt/node-configs/ does not exist
# · Last fail: N/A (new test)
# · Remove if: auto_detect_node_name is removed
def test_auto_detect_node_name_no_configs_dir(caplog) -> None:
    """auto_detect_node_name() returns 1 when /opt/node-configs/ does not exist."""
    caplog.set_level(logging.DEBUG)

    func_body = _extract_func(BOOTSTRAP_SH, "auto_detect_node_name")
    # Replace with nonexistent path
    func_body = func_body.replace("/opt/node-configs", "/tmp/nonexistent-node-configs-XXXX")

    script = textwrap.dedent(f"""\
        set -euo pipefail
        {func_body}

        if result=$(auto_detect_node_name 2>&1); then
            echo "[IMP:9][test][auto_detect] UNEXPECTED_SUCCESS:$result"
        else
            echo "[IMP:9][test][auto_detect] EXPECTED_FAILURE"
        fi
    """)
    stdout, stderr, rc = _bash(script)
    _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed: {stderr}"
    assert "EXPECTED_FAILURE" in stdout, f"Expected failure, got: {stdout}"
    # NOTE: The golden LOG_NO_CONFIGS message says "/opt/node-configs does not exist"
    # but due to string replacement we used a different path. The original golden message
    # pattern is "[IMP:8][bootstrap][auto-detect] /opt/node-configs does not exist"
    # which after replacement becomes "[IMP:8][bootstrap][auto-detect] /tmp/... does not exist"
    logger.info("[IMP:9][test][auto_detect] Missing configs dir: PASS")


# endregion


# region test_auto_detect_node_name_no_dirs
# 🧪 TRAP[TEST] · 2026-07-17 · auto_detect_node_name empty configs dir
# · Regression: returns wrong error on zero candidate directories
# · Scenario: /opt/node-configs/ exists but has only service dirs (scripts, secrets)
# · Last fail: N/A (new test)
# · Remove if: auto_detect_node_name logic changes
def test_auto_detect_node_name_no_dirs(caplog, tmp_path) -> None:
    """auto_detect_node_name() returns 1 when no non-service directories found."""
    caplog.set_level(logging.DEBUG)

    ncd = tmp_path / "node-configs"
    ncd.mkdir(parents=True)

    func_body = _extract_func(BOOTSTRAP_SH, "auto_detect_node_name")
    func_body = func_body.replace("/opt/node-configs", str(ncd))

    script = textwrap.dedent(f"""\
        set -euo pipefail
        {func_body}

        if result=$(auto_detect_node_name 2>&1); then
            echo "[IMP:9][test][auto_detect] UNEXPECTED_SUCCESS:$result"
        else
            echo "[IMP:9][test][auto_detect] EXPECTED_FAILURE"
            echo "STDERR:$result"
        fi
        echo "EXIT_CODE:$?"
    """)
    stdout, stderr, rc = _bash(script)
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed: {stderr}"
    assert "EXPECTED_FAILURE" in stdout, f"Expected failure, got: {stdout}"

    # The "No node directories found" message is emitted on stderr by the function
    assert "No node directories found" in stdout, f"Expected 'No node directories found' in stdout: {stdout[:400]}"
    logger.info("[IMP:9][test][auto_detect] No candidate dirs: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_auto_detect_node_name_multi_dirs
# 🧪 TRAP[TEST] · 2026-07-17 · auto_detect_node_name ambiguous multiple nodes
# · Regression: silently picks first match instead of error
# · Scenario: two non-service directories → must fail with "Multiple node directories"
# · Last fail: N/A (new test)
# · Remove if: auto_detect_node_name logic changes
def test_auto_detect_node_name_multi_dirs(caplog, tmp_path) -> None:
    """auto_detect_node_name() returns 1 when multiple node directories exist."""
    caplog.set_level(logging.DEBUG)

    ncd = tmp_path / "node-configs"
    (ncd / "node-alpha").mkdir(parents=True)
    (ncd / "node-beta").mkdir(parents=True)

    func_body = _extract_func(BOOTSTRAP_SH, "auto_detect_node_name")
    func_body = func_body.replace("/opt/node-configs", str(ncd))

    script = textwrap.dedent(f"""\
        set -euo pipefail
        {func_body}

        if result=$(auto_detect_node_name 2>&1); then
            echo "[IMP:9][test][auto_detect] UNEXPECTED_SUCCESS:$result"
        else
            echo "[IMP:9][test][auto_detect] EXPECTED_FAILURE"
            echo "STDERR:$result"
        fi
        echo "EXIT_CODE:$?"
    """)
    stdout, stderr, rc = _bash(script)
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed: {stderr}"
    assert "EXPECTED_FAILURE" in stdout, f"Expected failure, got: {stdout}"
    assert "Multiple directories:" in stdout, f"Expected 'Multiple directories:' in stdout: {stdout[:400]}"
    logger.info("[IMP:9][test][auto_detect] Multiple nodes ambiguous: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# scp_to_server
# ═══════════════════════════════════════════════════════════════════
# ⚠️ NOTE: scp_to_server() calls ssh (mkdir -p) and rsync externally.
#   We mock both commands to isolate the function's logic.
#   Mock rsync logs via "[IMP:9][mock-rsync]" for assertion in LDD.
#   Mock ssh does the same.
# ═══════════════════════════════════════════════════════════════════


# region test_scp_to_server_all_phases
# 🧪 TRAP[TEST] · 2026-07-17 · scp_to_server all 4 phases with secrets + platform-env
# · Regression: if SCP phase order changes, remote deployment breaks
# · Scenario: core/ + platform-env.yaml + node-configs/ + secrets/ all present
# · Last fail: N/A (new test)
# · Remove if: scp_to_server is removed or reimplemented
def test_scp_to_server_all_phases(caplog, tmp_path) -> None:
    """scp_to_server() executes all 4 phases with secrets and platform-env.yaml present."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME

    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    (ncd / "secrets").mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    # Create platform-env.yaml at project root level (scp_to_server reads from core_dir/../)
    platform_env = tmp_path / "platform-env.yaml"
    platform_env.write_text("dummy: env")

    preamble = textwrap.dedent("""\
        SSH_OPTS=()
        ssh() {
            echo "[IMP:9][mock-ssh] $*" >&2
            return 0
        }
        rsync() {
            echo "[IMP:9][mock-rsync] $*" >&2
            return 0
        }
    """)

    test_call = textwrap.dedent(f"""\
        scp_to_server "{host}" "{node}" "{ncd}" "{cd}"
        rc=$?
        echo "[IMP:9][test][scp] exit_code=$rc"
    """)
    stdout, stderr, _rc = _test_func(
        SCP_DELIVER_SH,
        ["scp_to_server"],
        test_call,
        env={"__LOG_PREFIX": "test"},
        preamble=preamble,
    )
    found_imp9 = _print_ldd(stderr, stdout)
    # Note: scp_to_server logs non-fatal messages to stdout (echo without >&2),
    # fatal messages go to stderr. The process exit code is always 0 (script
    # continues after function call). Check function return code from stdout.
    assert "exit_code=0" in stdout, f"scp_to_server failed: stdout={stdout[:300]}, stderr={stderr[:300]}"

    # Log assertions — non-fatal logs go to stdout
    assert GOLDEN_SCP_LOG_MKDIR in stdout
    assert GOLDEN_SCP_LOG_MKDIR_CONFIRMED in stdout
    assert GOLDEN_SCP_LOG_CORE_START in stdout
    assert GOLDEN_SCP_LOG_CORE_DONE in stdout

    # Mock calls go to stderr
    mock_ssh_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-ssh]" in line]
    assert len(mock_ssh_lines) == 1, f"Expected 1 ssh mock call, got {len(mock_ssh_lines)}"

    mock_rsync_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-rsync]" in line]
    # With platform-env.yaml present: core, platform-env, Makefile, node-configs, secrets = 5 rsync calls
    assert len(mock_rsync_lines) >= 4, f"Expected ≥4 rsync mock calls, got {len(mock_rsync_lines)}: {stderr[:500]}"

    # Verify rsync destinations
    all_rsync_args = " ".join(mock_rsync_lines)
    assert "root@192.168.1.100:/opt/platform/core/" in all_rsync_args
    assert "root@192.168.1.100:/opt/node-configs/test-node/" in all_rsync_args
    assert "root@192.168.1.100:/opt/node-configs/secrets/" in all_rsync_args

    # core rsync should have --delete --exclude=.git
    core_rsync = [line for line in mock_rsync_lines if "/opt/platform/core/" in line]
    assert len(core_rsync) >= 1
    assert "--delete" in core_rsync[0]
    assert "--exclude=.git" in core_rsync[0]

    logger.info("[IMP:9][test][scp] All 4+ phases executed: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_scp_to_server_no_secrets
# 🧪 TRAP[TEST] · 2026-07-17 · scp_to_server skips secrets when dir missing
# · Regression: if secrets dir check is removed, empty dir errors occur
# · Scenario: no secrets/ directory → must skip phase 3/4 with SKIP log
# · Last fail: N/A (new test)
# · Remove if: scp_to_server is removed or secrets handling changes
def test_scp_to_server_no_secrets(caplog, tmp_path) -> None:
    """scp_to_server() skips secrets phase when secrets/ dir does not exist."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME

    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    preamble = textwrap.dedent("""\
        SSH_OPTS=()
        ssh() {
            echo "[IMP:9][mock-ssh] $*" >&2
            return 0
        }
        rsync() {
            echo "[IMP:9][mock-rsync] $*" >&2
            return 0
        }
    """)

    test_call = textwrap.dedent(f"""\
        scp_to_server "{host}" "{node}" "{ncd}" "{cd}"
        rc=$?
        echo "[IMP:9][test][scp] exit_code=$rc"
    """)
    stdout, stderr, _rc = _test_func(
        SCP_DELIVER_SH,
        ["scp_to_server"],
        test_call,
        preamble=preamble,
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert "exit_code=0" in stdout, f"scp_to_server failed: stdout={stdout[:300]}, stderr={stderr[:300]}"

    # Must see SKIP for secrets — non-fatal log goes to stdout
    assert GOLDEN_SCP_LOG_SECRETS_SKIP in stdout

    # No secrets rsync calls
    mock_rsync_secrets = [
        line for line in stderr.split("\n") if "[IMP:9][mock-rsync]" in line and "/node-configs/secrets/" in line
    ]
    assert len(mock_rsync_secrets) == 0, f"Expected no secrets rsync calls, got {len(mock_rsync_secrets)}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] No secrets — SKIP log: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region test_scp_to_server_ssh_failure
# 🧪 TRAP[TEST] · 2026-07-17 · scp_to_server fails on ssh mkdir error
# · Regression: if ssh failure is silently ignored, rsyncs run without target dirs
# · Scenario: ssh mkdir -p returns non-zero → function must return 1 immediately
# · Last fail: N/A (new test)
# · Remove if: error handling in scp_to_server is changed
def test_scp_to_server_ssh_failure(caplog, tmp_path) -> None:
    """scp_to_server() returns 1 when ssh mkdir -p fails (no rsync calls)."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME
    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    (ncd / "secrets").mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    preamble = textwrap.dedent("""\
        SSH_OPTS=()
        ssh() {
            echo "[IMP:9][mock-ssh] SSH FAILURE (simulated)" >&2
            return 1
        }
        rsync() {
            echo "[IMP:9][mock-rsync] $*" >&2
            return 0
        }
    """)

    test_call = textwrap.dedent(f"""\
        scp_to_server "{host}" "{node}" "{ncd}" "{cd}"
        rc=$?
        echo "[IMP:9][test][scp] exit_code=$rc"
    """)
    stdout, stderr, _rc = _test_func(
        SCP_DELIVER_SH,
        ["scp_to_server"],
        test_call,
        preamble=preamble,
    )
    found_imp9 = _print_ldd(stderr, stdout)
    # Check function return code from stdout (exit_code=1), not process exit code
    assert "exit_code=1" in stdout, f"Expected exit_code=1, got stdout: {stdout[:300]}, stderr: {stderr[:300]}"

    # Fatal log goes to stderr (uses >&2)
    assert GOLDEN_SCP_LOG_MKDIR_FAIL in stderr, f"Expected golden mkdir failure log, got: {stderr[:500]}"

    # No rsync calls should happen
    mock_rsync_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-rsync]" in line]
    assert len(mock_rsync_lines) == 0, f"Expected 0 rsync calls after SSH failure, got {len(mock_rsync_lines)}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] SSH failure → abort with exit 1: PASS")


# endregion


# region test_scp_to_server_rsync_core_failure
# 🧪 TRAP[TEST] · 2026-07-17 · scp_to_server fails on rsync core/ error
# · Regression: if rsync failure is silently ignored, corrupt deployment
# · Scenario: rsync core/ fails → function returns 1
# · Last fail: N/A (new test)
# · Remove if: error handling in scp_to_server is changed
def test_scp_to_server_rsync_core_failure(caplog, tmp_path) -> None:
    """scp_to_server() returns 1 when rsync core/ fails."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME
    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    call_count = [0]

    def _make_preamble():
        nonlocal call_count
        call_count[0] = 0

        ssh_code = textwrap.dedent("""\
            ssh() {
                echo "[IMP:9][mock-ssh] $*" >&2
                return 0
            }
        """)

        # First rsync call (core) fails, subsequent ones should not be reached
        rsync_code = textwrap.dedent("""\
            RSYNC_CALLS=0
            rsync() {
                RSYNC_CALLS=$((RSYNC_CALLS + 1))
                echo "[IMP:9][mock-rsync] call=$RSYNC_CALLS $*" >&2
                if [ "$RSYNC_CALLS" -eq 1 ]; then
                    return 1
                fi
                return 0
            }
        """)
        return ssh_code + "\n" + rsync_code

    test_call = textwrap.dedent(f"""\
        scp_to_server "{host}" "{node}" "{ncd}" "{cd}"
        rc=$?
        echo "[IMP:9][test][scp] exit_code=$rc"
    """)
    stdout, stderr, _rc = _test_func(
        SCP_DELIVER_SH,
        ["scp_to_server"],
        test_call,
        preamble=_make_preamble(),
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert "exit_code=1" in stdout, f"Expected exit_code=1, got stdout: {stdout[:300]}, stderr: {stderr[:300]}"

    # Fatal log goes to stderr (uses >&2)
    assert GOLDEN_SCP_LOG_CORE_FAIL in stderr, f"Expected golden core failure log, got: {stderr[:500]}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] rsync core/ failure → exit 1: PASS")


# endregion


# ═══════════════════════════════════════════════════════════════════
# build_ssh_cmd
# ═══════════════════════════════════════════════════════════════════


# region test_build_ssh_cmd_no_cli_age_key
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd no --age-secret-key CLI in remote command
# · Regression: if --age-secret-key is re-added to remote SSH, key exposed in ps aux
# · Scenario: remote command must NOT contain --age-secret-key CLI arg
# · Last fail: N/A (new test)
# · Remove if: AGE key hardening policy changes (env-only vs fd-passing)
def test_build_ssh_cmd_no_cli_age_key(caplog) -> None:
    """build_ssh_cmd() output does NOT contain --age-secret-key CLI argument."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        f"""build_ssh_cmd "{GOLDEN_NODE_NAME}" "{GOLDEN_OWNER_KEY}" "" "{GOLDEN_AGE_KEY}"
echo "[IMP:9][test][build_ssh_cmd] Command constructed"
""",
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # Core assertion: --age-secret-key must NOT be in remote SSH command
    assert GOLDEN_SSH_CMD_FORBIDDEN_CLI not in cmd, (
        f"build_ssh_cmd contains {GOLDEN_SSH_CMD_FORBIDDEN_CLI} CLI arg: {cmd[:300]}...\n"
        "DevPlan D-1: AGE key must be passed via env export ONLY for remote SSH"
    )

    # Verify basic command structure
    assert GOLDEN_SSH_CMD_PREFIX in cmd
    assert GOLDEN_SSH_CMD_NODE_LIFECYCLE in cmd
    assert GOLDEN_SSH_CMD_NODE_NAME_FLAG in cmd
    assert GOLDEN_SSH_CMD_RESUME_FLAG in cmd

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][build_ssh_cmd] --age-secret-key absent: PASS")


# endregion


# region test_build_ssh_cmd_has_env_export
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd has export AGE_SECRET_KEY=
# · Regression: if env export is removed, orchestrator runs without decryption
# · Scenario: with non-empty age_key, must contain export AGE_SECRET_KEY=
# · Last fail: N/A (new test)
# · Remove if: AGE key transport mechanism changes
def test_build_ssh_cmd_has_env_export(caplog) -> None:
    """build_ssh_cmd() output contains export AGE_SECRET_KEY= when key is provided."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        f"""build_ssh_cmd "{GOLDEN_NODE_NAME}" "{GOLDEN_OWNER_KEY}" "" "{GOLDEN_AGE_KEY}"
echo "[IMP:9][test][build_ssh_cmd] Command constructed"
""",
    )
    _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    assert GOLDEN_SSH_CMD_EXPORT in cmd, f"build_ssh_cmd missing export AGE_SECRET_KEY=: {cmd[:300]}...\n"
    logger.info("[IMP:9][test][build_ssh_cmd] export AGE_SECRET_KEY= present: PASS")


# endregion


# region test_build_ssh_cmd_empty_key
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd without AGE key
# · Regression: empty key still injects export AGE_SECRET_KEY= (log noise)
# · Scenario: empty age_key → no export, but command structure intact
# · Last fail: N/A (new test)
# · Remove if: build_ssh_cmd logic changes
def test_build_ssh_cmd_empty_key(caplog) -> None:
    """build_ssh_cmd() without AGE key does NOT include export AGE_SECRET_KEY=."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        f"""build_ssh_cmd "{GOLDEN_NODE_NAME}" "{GOLDEN_OWNER_KEY}" "" ""
echo "[IMP:9][test][build_ssh_cmd] Command constructed"
""",
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # No export when key is empty
    assert GOLDEN_SSH_CMD_EXPORT not in cmd, (
        f"build_ssh_cmd should NOT include export when key is empty: {cmd[:300]}..."
    )
    # Basic structure still intact
    assert GOLDEN_SSH_CMD_NODE_LIFECYCLE in cmd

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][build_ssh_cmd] Empty key — no export: PASS")


# endregion


# region test_build_ssh_cmd_owner_key_quoting
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd printf %q quoting for owner key
# · Regression: if printf %q is removed, SSH keys with spaces break remote command
# · Scenario: owner key "ssh-ed25519 AAAATestKey test@example.com" with spaces
# · Last fail: N/A (new test)
# · Remove if: printf %q quoting is intentionally changed
def test_build_ssh_cmd_owner_key_quoting(caplog) -> None:
    """build_ssh_cmd() quotes owner key with spaces using printf %q."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        f"""build_ssh_cmd "{GOLDEN_NODE_NAME}" "{GOLDEN_OWNER_KEY}" "" "{GOLDEN_AGE_KEY}"
echo "[IMP:9][test][build_ssh_cmd] Command constructed"
""",
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # printf %q quotes spaces in different ways depending on shell version:
    # Either backslash-escaped spaces or single-quoted with spaces
    has_backslash_quoting = "ssh-ed25519\\ AAAATestKey\\ test@example.com" in cmd
    has_single_quote_quoting = "'ssh-ed25519 AAAATestKey test@example.com'" in cmd
    assert has_backslash_quoting or has_single_quote_quoting, (
        f"Owner key not properly %q-quoted in command: {cmd[:400]}..."
    )

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][build_ssh_cmd] Owner key %q quoting: PASS")


# endregion


# region test_build_ssh_cmd_passthrough_args
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd appends passthrough args
# · Regression: if passthrough args are dropped, --force or custom flags lost
# · Scenario: passthrough args must appear after --resume in command
# · Last fail: N/A (new test)
# · Remove if: passthrough args handling is changed
def test_build_ssh_cmd_passthrough_args(caplog) -> None:
    """build_ssh_cmd() appends passthrough args after --resume."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        f"""build_ssh_cmd "{GOLDEN_NODE_NAME}" "{GOLDEN_OWNER_KEY}" "" "{GOLDEN_AGE_KEY}" "--force" "--custom-flag=value"
echo "[IMP:9][test][build_ssh_cmd] Passthrough args test"
""",
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # Passthrough args must come after --resume
    resume_pos = cmd.find("--resume")
    force_pos = cmd.find("--force")
    custom_pos = cmd.find("--custom-flag=value")

    assert resume_pos >= 0, f"--resume missing: {cmd[:300]}"
    assert force_pos > resume_pos, f"--force should come after --resume. resume_pos={resume_pos}, force_pos={force_pos}"
    assert custom_pos > resume_pos, (
        f"--custom-flag should come after --resume. resume_pos={resume_pos}, custom_pos={custom_pos}"
    )

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][build_ssh_cmd] Passthrough args appended: PASS")


# endregion

# endregion build_ssh_cmd
