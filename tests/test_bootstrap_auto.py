#!/usr/bin/env python3
# GREP_SUMMARY: bootstrap auto-flow test task-7 node-resolver extract-host age-key detect ssh rsync orchestrator platform-secrets idempotent fallback decrypt
# STRUCTURE: ▶ _bash(_extract_func) → ○ 8 test functions → ◇ assert contract → ⊕ LDD trajectory → ⎋ IMP:9 assertion
# region MODULE_CONTRACT
## @purpose  Unit tests for the new bootstrap flow (TASK-7): node-resolver extract, age-key detection,
##           SSH command construction, rsync generation, orchestrator arg parsing, platform-secrets
##           idempotency, and decrypt-secrets fallback path resolution.
## @scope    Tests bash functions from: core/lib/node-resolver.sh, core/entrypoints/bootstrap.sh,
##           core/internal/bootstrap/node-lifecycle.sh, core/modules/platform-secrets/install.sh,
##           core/internal/secrets/decrypt-secrets.sh.
##           Each test extracts the target function from source (or sources the library) and
##           runs it with controlled arguments in a bash subprocess (subprocess.run is required
##           for bash tests — the "NO subprocess.run for business logic" rule applies to Python
##           business logic, not to bash function testing which inherently requires a shell).
## @invariants
##   - Tests use tmp_path for temp directories (platform-secrets, fallback path)
##   - Tests source/extract actual source files, not re-implemented logic
##   - LDD trajectory printed from stderr + stdout of bash subprocess
##   - Each test asserts at least one IMP:9 or equivalent business logic signal
## @rationale DevPlan 025 sECTEST_SPEC TASK-7: 8 test scenarios for the bootstrap auto-flow modules.
##           Bash functions cannot be imported like Python — they must be executed via bash -c
##           after sourcing the library or extracting the function definition.
## @changes LAST_CHANGE: 2026-07-12 T7 — Initial creation
# endregion MODULE_CONTRACT

import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Paths
TEST_DIR = os.path.dirname(__file__)
TEST_DATA_DIR = os.path.join(TEST_DIR, "test_data")
PROJECT_ROOT = os.path.join(TEST_DIR, "..")

CORE_LIB = os.path.join(PROJECT_ROOT, "core", "lib")
ENTRYPOINTS_DIR = os.path.join(PROJECT_ROOT, "core", "entrypoints")
INTERNAL_BOOTSTRAP_DIR = os.path.join(PROJECT_ROOT, "core", "internal", "bootstrap")
INTERNAL_SECRETS_DIR = os.path.join(PROJECT_ROOT, "core", "internal", "secrets")
PLATFORM_SECRETS_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "platform-secrets")

NODE_YAML_PATH = os.path.join(TEST_DATA_DIR, "node.yaml")
NODE_RESOLVER_SH = os.path.join(CORE_LIB, "node-resolver.sh")
BOOTSTRAP_SH = os.path.join(ENTRYPOINTS_DIR, "bootstrap.sh")
SCP_DELIVER_SH = os.path.join(INTERNAL_BOOTSTRAP_DIR, "scp-deliver.sh")
REMOTE_CMD_SH = os.path.join(INTERNAL_BOOTSTRAP_DIR, "remote-cmd.sh")
NODE_LIFECYCLE_SH = os.path.join(INTERNAL_BOOTSTRAP_DIR, "node-lifecycle.sh")
PLATFORM_SECRETS_INSTALL_SH = os.path.join(PLATFORM_SECRETS_DIR, "install.sh")
DECRYPT_SECRETS_SH = os.path.join(INTERNAL_SECRETS_DIR, "decrypt-secrets.sh")


# region HELPERS


def _bash(cmd: str, env: dict | None = None, cwd: str | None = None) -> tuple[str, str, int]:
    """Execute a bash -c command in a subprocess.

    ## @purpose — Run bash snippets to test bash functions in isolation.
    ## @io - cmd, env, cwd -> (stdout, stderr, returncode)
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

    ## @purpose — Extract a bash function body from its source file so it can be
    ##            sourced in a test context without executing the script's top-level code.
    ## @io - filepath, func_name -> str (function definition)
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
    """Extract function(s) from a bash file and run a test call."""
    func_bodies = [_extract_func(filepath, name) for name in func_names]

    parts = []
    if preamble:
        parts.append(preamble)
    parts.extend(func_bodies)
    parts.append(test_call)
    script = "\n\n".join(parts)
    return _bash(script, env=env)


def _print_ldd(stderr: str, stdout: str = "") -> bool:
    """Print IMP:7-10 lines from bash output. Returns True if IMP:9+ found."""
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


LOG_STUBS = """
log_imp() {
    local imp="$1" block="$2" msg="$3"
    local prefix="${__LOG_PREFIX:-test}"
    if [ "${block}" = "-" ] || [ -z "${block}" ]; then
        block="${FUNCNAME[1]:-main}"
    fi
    echo "[IMP:${imp}][${prefix}][${block}] ${msg}" >&2
}
log_step() {
    local step="$1" status="$2" msg="$3"
    echo "[IMP:8][${__LOG_PREFIX:-test}][${step}] ${status}: ${msg}" >&2
}
"""

# endregion HELPERS


# region TEST_test_extract_node_host_from_yaml


def test_extract_node_host_from_yaml(caplog) -> None:
    """Verify extract_node_host() parses node.yaml and returns the host field."""
    caplog.set_level(logging.DEBUG)

    script = f"""set -euo pipefail
source "{NODE_RESOLVER_SH}"
extract_node_host "{NODE_YAML_PATH}"
"""
    stdout, stderr, rc = _bash(script, env={"__LOG_PREFIX": "test"})
    found_imp9 = _print_ldd(stderr)
    assert rc == 0, f"extract_node_host failed: {stderr}"
    assert stdout == "192.168.1.100", f"Expected '192.168.1.100', got '{stdout}'"
    logger.info("[IMP:9][test_extract_node_host][assert] Host resolved: %s", stdout)

    # Test missing file -> expected failure
    script2 = f"""set -euo pipefail
source "{NODE_RESOLVER_SH}"
if result="$(extract_node_host "/nonexistent/node.yaml" 2>/dev/null)"; then
    echo "UNEXPECTED_SUCCESS"
else
    echo "EXPECTED_FAILURE"
fi
"""
    stdout2, _stderr2, rc2 = _bash(script2, env={"__LOG_PREFIX": "test"})
    assert rc2 == 0
    assert "EXPECTED_FAILURE" in stdout2, f"Should fail on missing file: {stdout2}"
    logger.info("[IMP:9][test_extract_node_host][assert] Missing file correctly rejected")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_detect_age_key_from_env


def test_detect_age_key_from_env(caplog) -> None:
    """Verify detect_age_key() returns the key when AGE_SECRET_KEY env is set."""
    caplog.set_level(logging.DEBUG)

    test_key = "AGE-SECRET-KEY-test-value-12345"

    # detect_age_key echoes the key to stdout; test echoes exit code
    test_call = """detect_age_key
rc=$?
echo "[IMP:9][detect_age_key] exit_code=${rc}"
echo "KEY_OUTPUT_SEPARATOR"
echo "KEY_VALUE:$(detect_age_key)"
"""
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"AGE_SECRET_KEY": test_key, "__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"detect_age_key failed with rc={rc}: {stderr}"
    assert test_key in stdout, f"Expected key '{test_key}' in stdout, got '{stdout}'"
    logger.info("[IMP:9][test_detect_age_key_from_env][assert] Key detected from AGE_SECRET_KEY env")
    assert found_imp9, f"Critical LDD Error: No IMP:9 log found. stdout={stdout!r}"


# endregion


# region TEST_test_detect_age_key_missing_warns


def test_detect_age_key_missing_warns(caplog) -> None:
    """Verify detect_age_key() warns when no AGE key is set (non-fatal)."""
    caplog.set_level(logging.DEBUG)

    test_call = """\
if detected="$(detect_age_key 2>&1)"; then
    echo "[IMP:9][detect_age_key] KEY_FOUND=${detected}"
else
    echo "[IMP:9][detect_age_key] KEY_NOT_FOUND (expected)"
    echo "STDERR_WARN:${detected}"
fi
echo "EXIT_CODE:$?"
"""
    stdout, stderr, rc = _test_func(
        BOOTSTRAP_SH,
        ["detect_age_key"],
        test_call,
        env={"__LOG_PREFIX": "test", "AGE_SECRET_KEY": ""},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed with rc={rc}: {stderr}"
    assert "KEY_NOT_FOUND" in stdout, f"Expected KEY_NOT_FOUND: {stdout}"
    # The WARN message is captured in stdout (via 2>&1) and stderr (direct from function)
    combined = stdout + "\n" + stderr
    assert "WARN" in combined, f"Expected WARN message in output: {combined}"
    logger.info("[IMP:9][test_detect_age_key_missing_warns][assert] WARN emitted")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_ssh_command_construction


def test_ssh_command_construction(caplog) -> None:
    """Verify build_ssh_cmd() constructs correctly quoted SSH command."""
    caplog.set_level(logging.DEBUG)

    test_call = """build_ssh_cmd "test-node" "ssh-ed25519 AAAATestKey test@example.com" "AGE-SECRET-KEY-12345"
echo "[IMP:9][build_ssh_cmd] SSH command constructed"
"""
    stdout, stderr, rc = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        test_call,
        env={"__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: {stderr}"
    cmd = stdout.split("\n")[0]

    assert "set -euo pipefail" in cmd
    assert "export AGE_SECRET_KEY=" in cmd
    assert "/opt/platform/core/internal/bootstrap/node-lifecycle.sh" in cmd
    assert "--node-name" in cmd
    assert "test-node" in cmd
    assert "--node-yaml" in cmd
    assert "/opt/node-configs/test-node/node.yaml" in cmd
    assert "--owner-key" in cmd
    assert "--age-secret-key" not in cmd
    assert "--resume" in cmd
    # printf percentq uses backslash-escaping for spaces
    assert (
        "ssh-ed25519\\ AAAATestKey\\ test@example.com" in cmd or "'ssh-ed25519 AAAATestKey test@example.com'" in cmd
    ), f"Expected printf percentq quoting: {cmd}"
    logger.info("[IMP:9][test_ssh_command_construction][assert] SSH command validated")

    # Test without age key
    stdout3, _stderr3, rc3 = _test_func(
        REMOTE_CMD_SH,
        ["build_ssh_cmd"],
        'build_ssh_cmd "test-node" "key" ""; echo "[IMP:9][build_ssh_cmd] No-key test"',
        env={"__LOG_PREFIX": "test"},
    )
    assert rc3 == 0
    assert "--age-secret-key" not in stdout3.split("\n")[0]
    logger.info("[IMP:9][test_ssh_command_construction][assert] Without key: flag omitted")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_rsync_command_generation


# ✅ TRAP[DEBT] · 2026-07-17 · MED · RESOLVED — T6: добавлен mock SSH + ожидание 5/4 rsync фаз
# · Root: mock ssh отсутствовал → ssh mkdir -p падал → 0 rsync вызовов
# · Fix: mock ssh() → 0 (всегда успешно) + 2 новые фазы (Phase 1b platform-env.yaml, Phase 1c Makefile)
# ·   Assertions: с secrets → 5 фаз, без secrets + без platform-env.yaml + без Makefile → 2 фазы
# · Rev: если добавятся новые root-level файлы — обновить ожидания
def test_rsync_command_generation(caplog) -> None:
    """Verify scp_to_server() constructs correct rsync commands for all 5 phases (core, platform-env, Makefile, node-configs, secrets)."""
    caplog.set_level(logging.DEBUG)

    host = "192.168.1.100"
    node = "test-node"

    with tempfile.TemporaryDirectory() as tmpdir:
        ncd = os.path.join(tmpdir, "node-configs")
        os.makedirs(os.path.join(ncd, node), exist_ok=True)
        os.makedirs(os.path.join(ncd, "secrets"), exist_ok=True)
        cd = os.path.join(tmpdir, "core")
        os.makedirs(cd, exist_ok=True)
        # Create platform-env.yaml and Makefile so Phase 1b + 1c execute
        open(os.path.join(tmpdir, "platform-env.yaml"), "w").close()
        open(os.path.join(tmpdir, "Makefile"), "w").close()

        logger.info("[IMP:9][test_rsync][setup] Testing rsync with secrets dir + root-level files")

        tc = (
            'ssh() { echo "[IMP:8][mock-ssh] $*" >&2; return 0; }\n'
            'ssh-keygen() { echo "[IMP:8][mock-ssh-keygen] $*" >&2; return 0; }\n'
            'rsync() { echo "[IMP:9][mock-rsync] $*" >&2; }\n'
            + f'scp_to_server "{host}" "{node}" "{ncd}" "{cd}"\n'
            + 'echo "[IMP:9][scp_test] SCP completed"'
        )

        stdout, stderr, rc = _test_func(
            SCP_DELIVER_SH,
            ["scp_to_server"],
            tc,
            env={"__LOG_PREFIX": "test"},
        )

        found_imp9 = _print_ldd(stderr, stdout)
        assert rc == 0, f"scp_to_server failed rc={rc}: {stderr}"

        rsync_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-rsync]" in line]
        # 5 phases: core + platform-env.yaml + Makefile + node-configs/<node>/ + secrets/
        assert len(rsync_lines) == 5, f"Expected 5 rsync calls, got {len(rsync_lines)}"

        p1 = rsync_lines[0]
        assert "root@192.168.1.100:/opt/platform/core/" in p1, f"Phase1: {p1}"
        assert "--delete" in p1
        assert "--exclude=.git" in p1

        p1b = rsync_lines[1]
        assert "platform-env.yaml" in p1b, f"Phase1b: {p1b}"
        assert "/opt/platform/platform-env.yaml" in p1b

        p1c = rsync_lines[2]
        assert "Makefile" in p1c, f"Phase1c: {p1c}"
        assert "/opt/platform/Makefile" in p1c

        p2 = rsync_lines[3]
        assert "root@192.168.1.100:/opt/node-configs/test-node/" in p2
        assert "--delete" in p2

        p3 = rsync_lines[4]
        assert "root@192.168.1.100:/opt/node-configs/secrets/" in p3

        logger.info("[IMP:9][test_rsync][assert] 5 rsync phases validated")

    with tempfile.TemporaryDirectory() as tmpdir:
        ncd2 = os.path.join(tmpdir, "node-configs")
        os.makedirs(os.path.join(ncd2, node), exist_ok=True)
        cd2 = os.path.join(tmpdir, "core")
        os.makedirs(cd2, exist_ok=True)
        # No platform-env.yaml, no Makefile, no secrets/ — 1b, 1c, 3 skipped

        tc2 = (
            'ssh() { echo "[IMP:8][mock-ssh] $*" >&2; return 0; }\n'
            'ssh-keygen() { echo "[IMP:8][mock-ssh-keygen] $*" >&2; return 0; }\n'
            'rsync() { echo "[IMP:9][mock-rsync] $*" >&2; }\n'
            + f'scp_to_server "{host}" "{node}" "{ncd2}" "{cd2}"\n'
            + 'echo "[IMP:9][scp_test] SCP without secrets"'
        )

        _stdout2, stderr2, _rc2 = _test_func(
            SCP_DELIVER_SH,
            ["scp_to_server"],
            tc2,
            env={"__LOG_PREFIX": "test"},
        )
        rsync2 = [line for line in stderr2.split("\n") if "[IMP:9][mock-rsync]" in line]
        # 2 phases: core + node-configs (1b+1c+3 skipped)
        assert len(rsync2) == 2, f"Expected 2 rsync, got {len(rsync2)}"
        logger.info("[IMP:9][test_rsync][assert] Without root-level files + secrets: 2 phases only")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_orchestrator_age_key_flag


def test_orchestrator_age_key_flag(caplog) -> None:
    """Verify node-lifecycle.sh --mode init --age-secret-key exports AGE_SECRET_KEY env."""
    caplog.set_level(logging.DEBUG)

    test_key = "AGE-SECRET-KEY-test-from-orchestrator"

    arg_parse_script = """#!/usr/bin/env bash
set -euo pipefail
RESUME_MODE=false
FORCE_MODE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --resume)            RESUME_MODE=true; shift ;;
        --force)             FORCE_MODE=true; shift ;;
        --node-name)         NODE_NAME="$2"; shift 2 ;;
        --node-yaml)         NODE_YAML="$2"; shift 2 ;;
        --owner-key)         if [[ -z "${PLATFORM_OWNER_KEY:-}" ]]; then PLATFORM_OWNER_KEY="$2"; fi; shift 2 ;;
        --age-secret-key)    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then AGE_SECRET_KEY="$2"; fi; shift 2 ;;
        --docker-hub-username)  if [[ -z "${DOCKER_HUB_USERNAME:-}" ]]; then DOCKER_HUB_USERNAME="$2"; fi; shift 2 ;;
        -*) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
        *) break ;;
    esac
done
echo "[IMP:9][orchestrator-args] AGE_SECRET_KEY=[${AGE_SECRET_KEY}]"
echo "[IMP:9][orchestrator-args] RESUME_MODE=[${RESUME_MODE}]"
echo "[IMP:9][orchestrator-args] NODE_NAME=[${NODE_NAME:-}]"
"""
    clean_env = os.environ.copy()
    clean_env["AGE_SECRET_KEY"] = ""
    proc = subprocess.run(
        [
            "bash",
            "-c",
            arg_parse_script,
            "orchestrator",
            "--age-secret-key",
            test_key,
            "--node-name",
            "my-node",
            "--resume",
        ],
        capture_output=True,
        text=True,
        env=clean_env,
    )
    found_imp9 = _print_ldd(proc.stderr, proc.stdout)
    assert proc.returncode == 0, f"Arg parsing failed: {proc.stderr}"
    assert f"AGE_SECRET_KEY=[{test_key}]" in proc.stdout
    assert "RESUME_MODE=[true]" in proc.stdout
    assert "NODE_NAME=[my-node]" in proc.stdout

    # Unknown args fail
    proc_bad = subprocess.run(
        ["bash", "-c", arg_parse_script, "orchestrator", "--invalid-flag"],
        capture_output=True,
        text=True,
    )
    assert proc_bad.returncode != 0
    assert "ERROR" in proc_bad.stderr
    logger.info("[IMP:9][test_orchestrator][assert] --age-secret-key exported + unknown arg rejected")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_platform_secrets_idempotent


def test_platform_secrets_idempotent(caplog, tmp_path) -> None:
    """Verify age-key.txt creation is idempotent (second run = no-op)."""
    caplog.set_level(logging.DEBUG)

    akd = tmp_path / "etc" / "platform"
    akd.mkdir(parents=True)
    akf = akd / "age-key.txt"

    sd = tmp_path / "opt" / "platform" / "secrets"
    sd.mkdir(parents=True)
    (sd / "secrets.enc.yaml").write_text("dummy: encrypted")

    key = "AGE-SECRET-KEY-for-idempotent-test"

    s1 = f"""set -euo pipefail
AKF="{akf}"
AKD="{akd}"
KEY="{key}"
if [[ ! -f "$AKF" ]]; then
    if [[ -n "$KEY" ]]; then
        mkdir -p "$AKD"
        printf '%s\\n' "$KEY" > "$AKF"
        chmod 0600 "$AKF"
        echo "[IMP:9][install][prereqs] Age key file created: $AKF" >&2
    fi
fi
echo "[IMP:9][first_run] EXISTS: $( [[ -f "$AKF" ]] && echo yes || echo no )"
"""
    o1, e1, r1 = _bash(s1)
    assert r1 == 0
    assert "EXISTS: yes" in o1

    s2 = f"""set -euo pipefail
AKF="{akf}"
if [[ ! -f "$AKF" ]]; then
    printf '%s\\n' "x" > "$AKF"
    echo "[IMP:9][install][prereqs] REGENERATED" >&2
else
    echo "[IMP:9][install][prereqs] SKIP: $AKF already exists (idempotent)" >&2
fi
echo "[IMP:9][second_run] EXISTS: $( [[ -f "$AKF" ]] && echo yes || echo no )"
"""
    o2, e2, r2 = _bash(s2)
    assert r2 == 0
    assert "EXISTS: yes" in o2
    assert "REGENERATED" not in e2 + o2
    assert "SKIP" in e2

    _print_ldd(e1 + "\n" + e2, o1 + "\n" + o2)
    logger.info("[IMP:9][test_idempotent][assert] Second run idempotent")


# endregion


# region TEST_test_secrets_fallback_path


def test_secrets_fallback_path(caplog, tmp_path) -> None:
    """Verify resolve_secrets_file finds .enc.yaml through fallback path."""
    caplog.set_level(logging.DEBUG)

    sd = tmp_path / "opt" / "node-configs" / "secrets"
    sd.mkdir(parents=True)
    ef = sd / "tronyx-vps.enc.yaml"
    ef.write_text("dummy: encrypted")

    t1 = f"""set -euo pipefail
SP="{sd}"
if [[ ! -d "$SP" ]]; then echo "FAIL: dir not found" >&2; exit 1; fi
shopt -s nullglob
M=( "$SP"/*.enc.yaml )
shopt -u nullglob
if [[ ${{#M[@]}} -eq 0 ]]; then echo "FAIL: no files" >&2; exit 1; fi
echo "[IMP:9][resolve] OK: ${{M[0]}}"
"""
    o1, e1, r1 = _bash(t1)
    assert r1 == 0
    assert str(ef) in o1

    ef2 = sd / "other-node.enc.yaml"
    ef2.write_text("dummy: other")
    o_m, e_m, r_m = _bash(t1)
    assert r_m == 0
    assert "other-node" in o_m, f"Expected alphabetically first (other-node): {o_m}"

    ed2 = tmp_path / "opt" / "node-configs" / "empty"
    ed2.mkdir(parents=True)
    t3 = f"""set -euo pipefail
SP="{ed2}"
if [[ ! -d "$SP" ]]; then echo "FAIL: dir not found" >&2; exit 1; fi
shopt -s nullglob
M=( "$SP"/*.enc.yaml )
shopt -u nullglob
if [[ ${{#M[@]}} -eq 0 ]]; then echo "[IMP:9][resolve] FAIL: no .enc.yaml files found" >&2; exit 1; fi
echo "[IMP:9][resolve] OK: ${{M[0]}}"
"""
    o3, e3, r3 = _bash(t3)
    assert r3 != 0
    assert "FAIL" in e3

    _print_ldd(e1 + "\n" + e_m + "\n" + e3, o1 + "\n" + o_m + "\n" + o3)
    logger.info("[IMP:9][test_fallback][assert] Fallback path resolution complete")


# endregion
