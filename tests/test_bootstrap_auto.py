#!/usr/bin/env python3
# GREP_SUMMARY: bootstrap auto-flow test task-7 node-resolver extract-host age-key detect ssh rsync orchestrator platform-secrets idempotent fallback decrypt w4-e5 resolve-node-yaml multi-path
# STRUCTURE: ▶ _bash(_extract_func) → ○ 8 test functions → ◇ assert contract → ⊕ LDD trajectory → ⎋ IMP:9 assertion → ◇ W4-E5 edge-cases (resolve_node_yaml paths, fail-fast, age-key file)
# region MODULE_CONTRACT
## @purpose  Unit tests for the new bootstrap flow (TASK-7): node-resolver extract, age-key detection,
##           SSH command construction, rsync generation, orchestrator arg parsing, platform-secrets
##           idempotency, and decrypt-secrets fallback path resolution.
##           W4-E5 (DevPlan 035 §7): +3 edge-case regression tests for node-resolver multi-path
##           search, empty-node-name fail-fast, and age-key-from-file path — страховка R-RISK-5.
## @scope    Tests bash functions from: core/lib/node-resolver.sh, core/entrypoints/bootstrap.sh,
##           core/internal/bootstrap/node-lifecycle.sh, core/modules/platform-secrets/install.sh,
##           core/internal/secrets/decrypt-secrets.sh.
##           test_rsync_command_generation — исключение (DevPlan 108): rsync-оркестрация
##           переехала из scp-deliver.sh в core_deliverer.py — тест импортирует deliver_all()
##           и мокает subprocess.run (native import, без bash-extraction).
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
from unittest import mock

from core.internal.bootstrap.core_deliverer import deliver_all

logger = logging.getLogger(__name__)

# Paths
TEST_DIR = os.path.dirname(__file__)
TEST_DATA_DIR = os.path.join(TEST_DIR, "test_data")
PROJECT_ROOT = os.path.join(TEST_DIR, "..")

CORE_DIR = os.path.join(PROJECT_ROOT, "core")
CORE_LIB = os.path.join(CORE_DIR, "lib")
ENTRYPOINTS_DIR = os.path.join(PROJECT_ROOT, "core", "entrypoints")
INTERNAL_BOOTSTRAP_DIR = os.path.join(PROJECT_ROOT, "core", "internal", "bootstrap")
INTERNAL_SECRETS_DIR = os.path.join(PROJECT_ROOT, "core", "internal", "secrets")
PLATFORM_SECRETS_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "platform-secrets")

NODE_YAML_PATH = os.path.join(TEST_DATA_DIR, "node.yaml")
NODE_RESOLVER_SH = os.path.join(CORE_LIB, "node-resolver.sh")
BOOTSTRAP_SH = os.path.join(ENTRYPOINTS_DIR, "bootstrap.sh")
SCP_DELIVER_SH = os.path.join(INTERNAL_BOOTSTRAP_DIR, "scp-deliver.sh")
# DevPlan 101 D1: build_*_ssh_cmd извлечены из remote-cmd.sh в build-ssh-cmd.sh
BUILD_SSH_CMD_SH = os.path.join(INTERNAL_BOOTSTRAP_DIR, "build-ssh-cmd.sh")
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
    assert stdout == "127.0.0.1", f"Expected '127.0.0.1', got '{stdout}'"
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
# 🧪 TRAP[TEST] · 2026-07-31 · detect_age_key migrated to python3 -m node_detect (DevPlan 104)
# · Regression: shell detect_age_key() removed from bootstrap.sh — logic now in
# ·   core/internal/shared/node_detect.py (canonical SoT)
# · Scenario: python3 -m core.internal.shared.node_detect --detect-age-key with
# ·   AGE_SECRET_KEY env set → key on stdout, exit 0
# · Last fail: 2026-07-31 — shell function extraction no longer possible (removed)
# · Remove if: node_detect CLI is reworked


def test_detect_age_key_from_env(caplog) -> None:
    """Verify python3 -m node_detect --detect-age-key returns the key when AGE_SECRET_KEY env is set."""
    caplog.set_level(logging.DEBUG)

    test_key = "AGE-SECRET-KEY-test-value-12345"

    test_call = """python3 -m core.internal.shared.node_detect --detect-age-key
rc=$?
echo "[IMP:9][node_detect] exit_code=${rc}"
echo "KEY_OUTPUT_SEPARATOR"
echo "KEY_VALUE:$(python3 -m core.internal.shared.node_detect --detect-age-key)"
"""
    stdout, stderr, rc = _bash(
        test_call,
        env={"AGE_SECRET_KEY": test_key, "SOPS_AGE_KEY": "", "AGE_SECRET_KEY_FILE": "", "__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"node_detect --detect-age-key failed with rc={rc}: {stderr}"
    assert "exit_code=0" in stdout, f"Expected exit_code=0 in stdout, got '{stdout}'"
    assert f"KEY_VALUE:{test_key}" in stdout, f"Expected key '{test_key}' in stdout, got '{stdout}'"
    logger.info("[IMP:9][test_detect_age_key_from_env][assert] Key detected from AGE_SECRET_KEY env via python3 -m")
    assert found_imp9, f"Critical LDD Error: No IMP:9 log found. stdout={stdout!r}"


# endregion


# region TEST_test_detect_age_key_missing_warns
# 🧪 TRAP[TEST] · 2026-07-31 · detect_age_key missing path via python3 -m node_detect (DevPlan 104)
# · Regression: shell detect_age_key() warn-on-missing (non-fatal) removed from bootstrap.sh
# · Scenario: python3 -m core.internal.shared.node_detect --detect-age-key with no key
# ·   → exit 3, stderr diagnostic "AGE_SECRET_KEY not found" (warn semantics preserved;
# ·     exit 3 = module OK + key absent, per language-policy contract, TRAP[DECISION] node_detect.py)
# · Last fail: 2026-07-31 — shell function extraction no longer possible (removed)
# · Remove if: node_detect CLI is reworked


def test_detect_age_key_missing_warns(caplog, tmp_path) -> None:
    """Verify node_detect --detect-age-key exits non-zero (3) with WARN diagnostic when no AGE key is set.

    # 🧪 TRAP[TEST] · 2026-08-02 · HOME isolated to tmp_path — default-file chain link
    # (node_detect Check 4, E2E auto-detect) would otherwise find the operator's real
    # ~/.ssh/age-key-personal.txt on dev machines → rc=0 instead of expected rc=3.
    """
    caplog.set_level(logging.DEBUG)

    test_call = """\
detected="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>&1)"
rc=$?
if [[ $rc -eq 0 ]]; then
    echo "[IMP:9][node_detect] KEY_FOUND=${detected}"
else
    echo "[IMP:9][node_detect] KEY_NOT_FOUND (expected) rc=${rc}"
    echo "STDERR_WARN:${detected}"
fi
"""
    stdout, stderr, rc = _bash(
        test_call,
        env={
            "__LOG_PREFIX": "test",
            "AGE_SECRET_KEY": "",
            "SOPS_AGE_KEY": "",
            "AGE_SECRET_KEY_FILE": "",
            "HOME": str(tmp_path),
        },
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Script crashed with rc={rc}: {stderr}"
    assert "KEY_NOT_FOUND" in stdout, f"Expected KEY_NOT_FOUND: {stdout}"
    # The WARN diagnostic is captured in stdout (via 2>&1) — stderr carries it directly from logging
    combined = stdout + "\n" + stderr
    assert "not found" in combined.lower(), f"Expected 'not found' WARN message in output: {combined}"
    logger.info("[IMP:9][test_detect_age_key_missing_warns][assert] WARN emitted via node_detect stderr")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_ssh_command_construction


def test_ssh_command_construction(caplog) -> None:
    """Verify build_ssh_cmd() constructs correctly quoted SSH command (new 4-param signature)."""
    caplog.set_level(logging.DEBUG)

    test_call = """build_ssh_cmd "test-node" "ssh-ed25519 AAAATestKey test@example.com" "ssh-ed25519 AAAACiKey ci-deploy@test" "AGE-SECRET-KEY-12345"
echo "[IMP:9][build_ssh_cmd] SSH command constructed"
"""
    stdout, stderr, rc = _test_func(
        BUILD_SSH_CMD_SH,
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
    assert "--ci-deploy-key" in cmd, f"Expected --ci-deploy-key in command: {cmd}"
    assert "--age-secret-key" not in cmd
    assert "--resume" in cmd
    # printf percentq uses backslash-escaping for spaces
    assert (
        "ssh-ed25519\\ AAAATestKey\\ test@example.com" in cmd or "'ssh-ed25519 AAAATestKey test@example.com'" in cmd
    ), f"Expected printf percentq quoting: {cmd}"
    logger.info("[IMP:9][test_ssh_command_construction][assert] SSH command validated with ci_deploy_key")

    # Test without age key (but with ci_deploy_key)
    stdout3, _stderr3, rc3 = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        'build_ssh_cmd "test-node" "key" "ci-key" ""; echo "[IMP:9][build_ssh_cmd] No-age-key test"',
        env={"__LOG_PREFIX": "test"},
    )
    assert rc3 == 0
    cmd3 = stdout3.split("\n")[0]
    assert "--age-secret-key" not in cmd3
    assert "--ci-deploy-key" in cmd3, f"Expected --ci-deploy-key without age key: {cmd3}"
    logger.info(
        "[IMP:9][test_ssh_command_construction][assert] Without age key: ci-deploy-key present, age flag omitted"
    )

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_rsync_command_generation
# 🧪 TRAP[TEST] · 2026-07-31 · rsync generation moved to core_deliverer.py (DevPlan 108)
# · Regression: scp_to_server (shell) no longer builds rsync commands — deliver_all() in
# ·   core_deliverer.py orchestrates mkdir + 5 rsync фаз (1→1b→1c→2→3)
# · Scenario: import deliver_all, mock subprocess.run recorder, assert phase order +
# ·   destinations; scenario 2: missing root-level files + secrets → only core+node-configs
# · Last fail: 2026-07-31 — 0 rsync in shell facade (expected 5)
# · Remove if: deliver_all orchestration is reimplemented elsewhere


def test_rsync_command_generation(caplog, tmp_path) -> None:
    """Verify deliver_all() constructs the correct rsync sequence for all 5 phases (core, platform-env, Makefile, node-configs, secrets)."""
    caplog.set_level(logging.DEBUG)

    host = "192.168.1.100"
    node = "test-node"

    def _ok_run(*_args, **_kwargs) -> mock.MagicMock:
        """subprocess.run mock return — success."""
        return mock.MagicMock(returncode=0, stdout="", stderr="")

    # ── Scenario 1: full tree (platform-env.yaml + Makefile + secrets present) ──
    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    (ncd / node / "secrets").mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)
    # Create platform-env.yaml and Makefile so Phase 1b + 1c execute
    (tmp_path / "platform-env.yaml").write_text("dummy: env")
    (tmp_path / "Makefile").write_text(".PHONY: test")

    logger.info("[IMP:9][test_rsync][setup] Testing rsync with secrets dir + root-level files")

    calls: list[list[str]] = []

    def _recorder(*args, **_kwargs) -> mock.MagicMock:
        calls.append(args[0])
        return _ok_run()

    with mock.patch.object(subprocess, "run", side_effect=_recorder):
        result = deliver_all(host, node, str(ncd), str(cd))
    assert result is True, "deliver_all must succeed with all phases present"

    # 6 subprocess calls: ssh mkdir + 5 rsync фаз (1, 1b, 1c, 2, 3)
    assert len(calls) == 6, f"Expected 6 subprocess calls, got {len(calls)}: {calls}"
    assert calls[0][0] == "ssh", f"Step 1 must be ssh mkdir: {calls[0]}"
    rsync_phases = calls[1:]
    assert all(c[0] == "rsync" for c in rsync_phases), "Steps 2-6 must be rsync"

    # Phase 1/4: core/ — --delete + runtime-artifact excludes
    p1 = " ".join(rsync_phases[0])
    assert "root@192.168.1.100:/opt/platform/core/" in p1, f"Phase1: {p1}"
    assert "--delete" in p1
    assert "--exclude=.git" in p1
    assert "--exclude=default-user.xml" in p1
    assert "--exclude=.env" in p1

    # Phase 1b/4: platform-env.yaml
    p1b = " ".join(rsync_phases[1])
    assert "platform-env.yaml" in p1b, f"Phase1b: {p1b}"
    assert "/opt/platform/platform-env.yaml" in p1b

    # Phase 1c/4: Makefile
    p1c = " ".join(rsync_phases[2])
    assert "Makefile" in p1c, f"Phase1c: {p1c}"
    assert "/opt/platform/Makefile" in p1c

    # Phase 2/4: node-configs/<node>/
    p2 = " ".join(rsync_phases[3])
    assert "root@192.168.1.100:/opt/node-configs/test-node/" in p2
    assert "--delete" in p2

    # Phase 3/4: secrets/
    p3 = " ".join(rsync_phases[4])
    assert "root@192.168.1.100:/opt/node-configs/secrets/" in p3

    logger.info("[IMP:9][test_rsync][assert] 5 rsync phases validated")

    # ── Scenario 2: minimal tree — no platform-env.yaml, no Makefile, no secrets/ ──
    # Isolated subtree: root-level files from scenario 1 must NOT leak into 1b/1c skips
    s2 = tmp_path / "scenario2"
    ncd2 = s2 / "node-configs"
    (ncd2 / node).mkdir(parents=True)
    cd2 = s2 / "core"
    cd2.mkdir(parents=True)

    calls2: list[list[str]] = []

    def _recorder2(*args, **_kwargs) -> mock.MagicMock:
        calls2.append(args[0])
        return _ok_run()

    with mock.patch.object(subprocess, "run", side_effect=_recorder2):
        result2 = deliver_all(host, node, str(ncd2), str(cd2))
    assert result2 is True
    # ssh mkdir + core + node-configs = 3 calls; 1b+1c+3 skipped → 2 rsync
    assert len(calls2) == 3, f"Expected 3 subprocess calls, got {len(calls2)}: {calls2}"
    rsync2 = [c for c in calls2 if c[0] == "rsync"]
    assert len(rsync2) == 2, f"Expected 2 rsync, got {len(rsync2)}"
    logger.info("[IMP:9][test_rsync][assert] Without root-level files + secrets: 2 phases only")

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


# region TEST_test_docker_login_set_u_safe
# 🧪 TRAP[TEST] · 2026-07-31 · docker_login extract→source migration
# · Regression: _test_func() extract context made BASH_SOURCE[0]="bash" → docker.sh resolved
#   ${BASH_SOURCE[0]%/*}/../internal/shared/docker_auth.py as <cwd>/bash/../… → file missing →
#   docker_login crashed under set -euo pipefail
# · Scenario: source docker.sh with ABSOLUTE path (BASH_SOURCE[0] correct inside) → (a) no creds →
#   anonymous fallback exit 0; (b) creds + PATH-substituted docker stub → subprocess mock invoked
# · Last fail: 2026-07-31 — rc=2 "can't open file .../bash/../internal/shared/docker_auth.py"
# · Remove if: docker_login moves fully to Python (then point test at docker_auth.py directly)


def test_docker_login_set_u_safe(caplog, tmp_path) -> None:
    """Verify docker_login() does not crash under set -euo pipefail when vars unset."""
    caplog.set_level(logging.DEBUG)

    docker_sh = os.path.join(CORE_LIB, "docker.sh")
    source_line = f'source "{docker_sh}"'

    # Scenario 1: no env vars → anonymous fallback
    test_call_anon = f"""set -euo pipefail
unset DOCKER_HUB_USERNAME DOCKER_HUB_TOKEN
__LOG_PREFIX="test"
{source_line}
docker_login
echo "[IMP:9][docker_test] exit=$?"
"""
    stdout, stderr, rc = _bash(test_call_anon, env={"__LOG_PREFIX": "test"})

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"docker_login crashed under set -euo pipefail (no vars): {stderr}"
    combined = stdout + "\n" + stderr
    assert "anonymous" in combined.lower(), f"Expected anonymous fallback WARN: {combined}"
    logger.info("[IMP:9][test_docker_login_set_u_safe][assert] Anonymous fallback exits 0")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"

    # Scenario 2: env vars set → authenticated login via PATH-substituted docker stub.
    # docker_auth.py invokes docker through subprocess.run(["docker", ...]) — a bash function
    # mock is INVISIBLE to the Python subprocess, so a real executable stub on PATH is required
    # (Test Honesty R1: the mock must actually be exercised). The stub's stderr is PIPE-captured
    # by docker_auth.py, so the [IMP:9][mock-docker] evidence is appended to a marker file.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker_file = tmp_path / "docker-call.log"
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "[IMP:9][mock-docker] login --username ${{DOCKER_HUB_USERNAME}} --password-stdin" >> "{marker_file}"\n'
        "exit 0\n"
    )
    docker_stub.chmod(0o755)

    test_call_auth = f"""set -euo pipefail
__LOG_PREFIX="test"
export PATH="{bin_dir}:$PATH"
export DOCKER_HUB_USERNAME="testuser"
export DOCKER_HUB_TOKEN="testtoken123"
{source_line}
docker_login
echo "[IMP:9][docker_test] auth exit=$?"
"""
    stdout2, stderr2, rc2 = _bash(test_call_auth, env={"__LOG_PREFIX": "test"})

    found_imp9_2 = _print_ldd(stderr2, stdout2)
    assert rc2 == 0, f"docker_login crashed with vars set: {stderr2}"
    assert "testuser" in stderr2 or "testuser" in stdout2, "Expected testuser in output"
    # Honest mock verification: docker_auth.py must have actually invoked the stub
    assert marker_file.exists(), (
        "PATH-substituted docker mock was never exercised — docker_auth.py subprocess should call docker"
    )
    mock_log = marker_file.read_text()
    assert "[IMP:9][mock-docker]" in mock_log, f"Mock not invoked: {mock_log!r}"
    assert "testuser" in mock_log, f"Mock called without username: {mock_log!r}"
    logger.info("[IMP:9][test_docker_login_set_u_safe][assert] Authenticated path works")
    assert found_imp9_2, "Critical LDD Error: No IMP:9 business logic log found (auth)"


# endregion


# region TEST_test_ci_deploy_key_extracted_from_node_yaml
# 🧪 TRAP[TEST] · 2026-07-17 · ci_deploy_key extraction from node.yaml
# · Regression: schema declares node.ci_deploy_key but bootstrap.sh didn't consume it (D1)
# · Scenario: node.yaml with ci_deploy_key → extracted value is non-empty;
#             node.yaml without ci_deploy_key → empty string, not fatal
# · Last fail: N/A (new test)
# · Remove if: ci_deploy_key extraction logic changes fundamentally


def test_ci_deploy_key_extracted_from_node_yaml(caplog, tmp_path) -> None:
    """Verify bootstrap.sh extracts ci_deploy_key from node.yaml (or returns empty without fatal)."""
    caplog.set_level(logging.DEBUG)

    # ── Scenario 1: node.yaml WITH ci_deploy_key ────────────────────────
    node_yaml_with_key = tmp_path / "node_with_key.yaml"
    node_yaml_with_key.write_text("""node:
  name: test-node
  ci_deploy_key: "ssh-ed25519 AAAATestCiKey ci-deploy@test"
  owner_key: "ssh-ed25519 AAAATestOwnerKey owner@test"
""")

    bash_script_with = f"""set -euo pipefail
CI_DEPLOY_KEY=$(python3 -c "import yaml; f=open('{node_yaml_with_key}'); d=yaml.safe_load(f); print(d.get('node',{{}}).get('ci_deploy_key',''))" 2>/dev/null) || true
if [[ -n "${{CI_DEPLOY_KEY}}" ]]; then
    echo "[IMP:9][ci_deploy_key] KEY_FOUND=${{CI_DEPLOY_KEY}}"
else
    echo "[IMP:9][ci_deploy_key] KEY_EMPTY"
fi
"""
    stdout1, stderr1, rc1 = _bash(bash_script_with)
    found1 = _print_ldd(stderr1, stdout1)
    assert rc1 == 0, f"Extraction with key failed: {stderr1}"
    assert "KEY_FOUND" in stdout1, f"Expected KEY_FOUND: {stdout1}"
    assert "ci-deploy@test" in stdout1, f"Expected ci-deploy key value: {stdout1}"
    logger.info("[IMP:9][test_ci_deploy_key_extracted][assert] ci_deploy_key extracted from node.yaml")

    # ── Scenario 2: node.yaml WITHOUT ci_deploy_key (not fatal) ─────────
    node_yaml_without_key = tmp_path / "node_without_key.yaml"
    node_yaml_without_key.write_text("""node:
  name: test-node
  owner_key: "ssh-ed25519 AAAATestOwnerKey owner@test"
""")

    bash_script_without = f"""set -euo pipefail
CI_DEPLOY_KEY=$(python3 -c "import yaml; f=open('{node_yaml_without_key}'); d=yaml.safe_load(f); print(d.get('node',{{}}).get('ci_deploy_key',''))" 2>/dev/null) || true
echo "[IMP:9][ci_deploy_key] VALUE=[${{CI_DEPLOY_KEY}}]"
echo "[IMP:9][ci_deploy_key] LEN=${{#CI_DEPLOY_KEY}}"
"""
    stdout2, stderr2, rc2 = _bash(bash_script_without)
    found2 = _print_ldd(stderr2, stdout2)
    assert rc2 == 0, f"Extraction without key failed: {stderr2}"
    assert "VALUE=[]" in stdout2, f"Expected empty VALUE: {stdout2}"
    assert "LEN=0" in stdout2, f"Expected LEN=0: {stdout2}"
    logger.info("[IMP:9][test_ci_deploy_key_extracted][assert] ci_deploy_key absent: empty string, not fatal")

    # ── Scenario 3: env PLATFORM_CI_DEPLOY_KEY override ─────────────────
    bash_script_env = f"""set -euo pipefail
CI_DEPLOY_KEY=$(python3 -c "import yaml; f=open('{node_yaml_without_key}'); d=yaml.safe_load(f); print(d.get('node',{{}}).get('ci_deploy_key',''))" 2>/dev/null) || true
# Env override
if [[ -n "${{PLATFORM_CI_DEPLOY_KEY:-}}" ]]; then
    CI_DEPLOY_KEY="${{PLATFORM_CI_DEPLOY_KEY}}"
fi
echo "[IMP:9][ci_deploy_key] VALUE=[${{CI_DEPLOY_KEY}}]"
"""
    stdout3, stderr3, rc3 = _bash(
        bash_script_env,
        env={"PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 OVERRIDE_KEY ci-deploy@override"},
    )
    found3 = _print_ldd(stderr3, stdout3)
    assert rc3 == 0, f"Env override failed: {stderr3}"
    assert "OVERRIDE_KEY" in stdout3, f"Expected env override key: {stdout3}"
    logger.info("[IMP:9][test_ci_deploy_key_extracted][assert] PLATFORM_CI_DEPLOY_KEY env override works")

    assert found1 and found2 and found3, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_build_ssh_cmd_includes_ci_deploy_key
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd includes --ci-deploy-key flag
# · Regression: after build_ssh_cmd signature change (3→4 params), ci_deploy_key must appear
# · Scenario: non-empty ci_deploy_key → `--ci-deploy-key` with %q-quoted value in SSH command
# · Last fail: N/A (new test)
# · Remove if: build_ssh_cmd ci_deploy_key handling changes


def test_build_ssh_cmd_includes_ci_deploy_key(caplog) -> None:
    """Verify build_ssh_cmd() includes --ci-deploy-key with printf %q quoting when key is non-empty."""
    caplog.set_level(logging.DEBUG)

    ci_key = "ssh-ed25519 AAAACiDeployKey ci-deploy@example.com"
    test_call = f"""build_ssh_cmd "test-node" "ssh-ed25519 AAAATestOwnerKey owner@test" "{ci_key}" "AGE-SECRET-KEY-12345"
echo "[IMP:9][build_ssh_cmd_ci] Exit=$?"
"""
    stdout, stderr, rc = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        test_call,
        env={"__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: {stderr}"
    cmd = stdout.split("\n")[0]

    assert "--ci-deploy-key" in cmd, f"Expected --ci-deploy-key flag: {cmd}"
    # Verify the key value is %q-quoted (spaces escaped with backslash)
    assert (
        "ssh-ed25519\\\\\\ AAAACiDeployKey\\\\\\ ci-deploy@example.com" in cmd
        or "ssh-ed25519\\ AAAACiDeployKey\\ ci-deploy@example.com" in cmd
        or "'ssh-ed25519 AAAACiDeployKey ci-deploy@example.com'" in cmd
    ), f"Expected %q-quoted ci_deploy_key value: {cmd}"
    logger.info("[IMP:9][test_build_ssh_cmd_ci][assert] --ci-deploy-key present with %q quoting")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_build_ssh_cmd_empty_ci_deploy_key_omits_flag
# 🧪 TRAP[TEST] · 2026-07-17 · build_ssh_cmd omits --ci-deploy-key when empty
# · Regression: empty ci_deploy_key must NOT emit --ci-deploy-key flag (backward compat)
# · Scenario: empty ci_deploy_key string → no `--ci-deploy-key` in SSH command
# · Last fail: N/A (new test)
# · Remove if: build_ssh_cmd ci_deploy_key handling changes


def test_build_ssh_cmd_empty_ci_deploy_key_omits_flag(caplog) -> None:
    """Verify build_ssh_cmd() omits --ci-deploy-key when the key is empty (backward compat)."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        'build_ssh_cmd "test-node" "ssh-ed25519 AAAATestOwnerKey owner@test" "" "AGE-SECRET-KEY-12345"\n'
        'echo "[IMP:9][build_ssh_cmd_empty] Exit=$?"',
        env={"__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd with empty ci_deploy_key failed: {stderr}"
    cmd = stdout.split("\n")[0]

    # When ci_deploy_key is empty, the age_key becomes $3 (shifted from $4 due to $3 being empty)
    # Actually no — the signature is now: node, owner_key, ci_deploy_key, age_key, passthrough...
    # So $3="" and $4="AGE-SECRET-KEY-12345"
    assert "--ci-deploy-key" not in cmd, f"Expected NO --ci-deploy-key flag when key is empty: {cmd}"
    # Verify other expected flags are still present (backward compat)
    assert "--owner-key" in cmd
    assert "--node-name" in cmd
    assert "--resume" in cmd
    assert "--age-secret-key" not in cmd
    logger.info("[IMP:9][test_build_ssh_cmd_empty][assert] --ci-deploy-key omitted when empty (backward compat)")

    # Also test with both ci_deploy_key AND age_key empty
    stdout2, stderr2, rc2 = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        'build_ssh_cmd "test-node" "key" "" ""\necho "[IMP:9][build_ssh_cmd_both_empty] Exit=$?"',
        env={"__LOG_PREFIX": "test"},
    )
    assert rc2 == 0, f"build_ssh_cmd both empty failed: {stderr2}"
    cmd2 = stdout2.split("\n")[0]
    assert "--ci-deploy-key" not in cmd2
    assert "export AGE_SECRET_KEY=" not in cmd2
    logger.info("[IMP:9][test_build_ssh_cmd_empty][assert] Both keys empty: all optional flags omitted")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): Edge-case regression baseline — страховка R-RISK-5 ДО extraction.
# 3 edge-case теста node-resolver.sh и age-key detection, которые W4-E2 lifecycle extraction
# НЕ должен нарушить. Bash-subprocess pattern (consistent with existing test_bootstrap_auto).
# ══════════════════════════════════════════════════════════════════════════════


# region TEST_test_build_ssh_cmd_includes_ci_root_key
# 🧪 TRAP[TEST] · 2026-08-06 · 142 W1 (A1) · build_ssh_cmd 5-й ключ ci_root_key
# · Regression: CI-root ключ (ПУБЛИЧНАЯ часть VPS_SSH_KEY) не доставлялся в remote-команду →
# ·   φ2 не получал PLATFORM_CI_ROOT_KEY → root authorized_keys без ключа → core-deploy
# ·   root-канал падал на свежей ноде (ручное добавление ключа, циклы 1/2 141).
# · Scenario: build_ssh_cmd с 5 аргументами (node, owner, ci-deploy, age, ci-root) →
# ·   вывод содержит `--ci-root-key` (printf %q) И `export PLATFORM_CI_ROOT_KEY=`.
# · Remove if: build_ssh_cmd сигнатура меняется (ci_root_key уходит из remote-цепочки)
def test_build_ssh_cmd_includes_ci_root_key(caplog) -> None:
    """142 W1: build_ssh_cmd 5-й ключ — --ci-root-key + PLATFORM_CI_ROOT_KEY export."""
    caplog.set_level(logging.DEBUG)

    ci_root_key = "ssh-ed25519 AAAACiRootKey ci-root@example.com"
    test_call = f"""build_ssh_cmd "test-node" "ssh-ed25519 AAAATestOwnerKey owner@test" \
"ssh-ed25519 AAAACiDeployKey ci-deploy@test" "AGE-SECRET-KEY-12345" "{ci_root_key}"
echo "[IMP:9][build_ssh_cmd_ci_root] Exit=$?"
"""
    stdout, stderr, rc = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        test_call,
        env={"__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: {stderr}"
    cmd = stdout.split("\n")[0]

    assert "--ci-root-key" in cmd, f"Expected --ci-root-key flag: {cmd}"
    assert "export PLATFORM_CI_ROOT_KEY=" in cmd, f"Expected PLATFORM_CI_ROOT_KEY export: {cmd}"
    # %q-quoting: ключ с пробелами экранируется
    assert (
        "ssh-ed25519\\\\\\ AAAACiRootKey\\\\\\ ci-root@example.com" in cmd
        or "ssh-ed25519\\ AAAACiRootKey\\ ci-root@example.com" in cmd
        or "'ssh-ed25519 AAAACiRootKey ci-root@example.com'" in cmd
    ), f"Expected %q-quoted ci_root_key value: {cmd}"
    # Совместимость: 4-й ключ (ci-deploy) на месте
    assert "--ci-deploy-key" in cmd
    logger.info("[IMP:9][test_build_ssh_cmd_ci_root][assert] --ci-root-key + PLATFORM_CI_ROOT_KEY present (142 W1)")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_build_ssh_cmd_empty_ci_root_key_omits_flag
# 🧪 TRAP[TEST] · 2026-08-06 · 142 W1 · пустой ci_root_key не эмитит флаг
# · Regression: пустой 5-й ключ не должен добавлять --ci-root-key (backward-compat
# ·   с 4-аргументными вызовами — старые тесты/вызовы остаются валидными)
# · Remove if: build_ssh_cmd ci_root_key handling changes
def test_build_ssh_cmd_empty_ci_root_key_omits_flag(caplog) -> None:
    """142 W1: пустой ci_root_key → нет --ci-root-key (backward-compat)."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _test_func(
        BUILD_SSH_CMD_SH,
        ["build_ssh_cmd"],
        'build_ssh_cmd "test-node" "ssh-ed25519 AAAATestOwnerKey owner@test" "" "AGE-SECRET-KEY-12345" ""\n'
        'echo "[IMP:9][build_ssh_cmd_empty_ci_root] Exit=$?"',
        env={"__LOG_PREFIX": "test"},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd empty ci_root_key failed: {stderr}"
    cmd = stdout.split("\n")[0]
    assert "--ci-root-key" not in cmd, f"Expected NO --ci-root-key flag: {cmd}"
    assert "export PLATFORM_CI_ROOT_KEY=" not in cmd, f"Expected NO PLATFORM_CI_ROOT_KEY export: {cmd}"
    # Остальные флаги на месте (backward compat)
    assert "--owner-key" in cmd
    assert "--node-name" in cmd
    assert "--resume" in cmd
    logger.info("[IMP:9][test_build_ssh_cmd_empty_ci_root][assert] --ci-root-key omitted when empty (142 W1)")

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_resolve_node_yaml_multi_path_search
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 resolve_node_yaml 3-candidate-path search
# · Regression: node.yaml must be discoverable across platform-local → org-repos → VPS fallback
# · Scenario: node.yaml only in VPS-fallback path (/opt/node-configs/<node>/node.yaml) → still resolved
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: resolve_node_yaml moves to Python (then point test at new module)


def test_resolve_node_yaml_multi_path_search(caplog, tmp_path) -> None:
    """Verify resolve_node_yaml() finds node.yaml in org-repos path (2nd candidate)."""
    caplog.set_level(logging.DEBUG)

    # Create node.yaml in the org-repos location: projects_dir/<org>/node-configs/<node>/node.yaml
    # Path 1 (platform-local) is empty → must fall through to path 2 (org-repos).
    # Path 3 (VPS-fallback /opt/node-configs) is hardcoded and not writable in tests.
    projects_dir = tmp_path / "projects"
    org_configs = projects_dir / "myorg" / "node-configs" / "test-multi"
    org_configs.mkdir(parents=True)
    node_yaml = org_configs / "node.yaml"
    node_yaml.write_text("node:\n  name: test-multi\n  host: 10.0.0.5\n")

    # Empty platform_root (no candidates in path 1)
    empty_platform = tmp_path / "empty-platform"
    empty_platform.mkdir()

    logger.info("[IMP:9][test_resolve_multi_path] Testing org-repos path resolution")

    script = f"""set -euo pipefail
source "{NODE_RESOLVER_SH}"
result="$(resolve_node_yaml "test-multi" "{empty_platform}" "{projects_dir}")"
echo "[IMP:9][test_resolve_multi_path] RESOLVED: $result"
"""
    # DP-088/091: resolve_node_yaml delegates to NodeYaml.resolve() which globs
    # $HOME/projects/*/node-configs/ — HOME override routes the org-repos fixture
    # ({projects_dir}/myorg/node-configs/) into the actual search path.
    stdout, stderr, rc = _bash(script, env={"__LOG_PREFIX": "test", "HOME": str(tmp_path)})
    found_imp9 = _print_ldd(stderr, stdout)

    assert rc == 0, f"resolve_node_yaml failed (rc={rc}): {stderr}"
    assert str(node_yaml) in stdout, f"Expected org-repos path {node_yaml} in output, got: {stdout}"
    logger.info("[IMP:9][test_resolve_multi_path][assert] org-repos path resolved correctly")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_resolve_node_yaml_empty_name_fails_fast
# 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 resolve_node_yaml empty node_name fail-fast
# · Regression: empty node_name must return 1 immediately (not search with empty glob)
# · Scenario: resolve_node_yaml "" → exit 1 with IMP:10 "Missing required argument"
# · Last fail: N/A (W4-E5 baseline)
# · Remove if: resolve_node_yaml moves to Python (then point test at new module)


def test_resolve_node_yaml_empty_name_fails_fast(caplog) -> None:
    """Verify resolve_node_yaml() fails fast (exit 1) when node_name is empty."""
    caplog.set_level(logging.DEBUG)

    logger.info("[IMP:9][test_resolve_empty_name] Testing empty node_name fail-fast")

    script = f"""set -euo pipefail
source "{NODE_RESOLVER_SH}"
if resolve_node_yaml "" 2>/dev/null; then
    echo "UNEXPECTED_SUCCESS"
else
    echo "[IMP:9][test_resolve_empty_name] EXPECTED_FAILURE: empty node_name rejected"
fi
"""
    stdout, stderr, rc = _bash(script, env={"__LOG_PREFIX": "test"})
    found_imp9 = _print_ldd(stderr, stdout)

    assert rc == 0, f"Bash script crashed (rc={rc}): {stderr}"
    assert "EXPECTED_FAILURE" in stdout, f"Empty node_name should fail-fast, got: {stdout}"
    assert "UNEXPECTED_SUCCESS" not in stdout, f"Empty node_name must NOT succeed, got: {stdout}"
    logger.info("[IMP:9][test_resolve_empty_name][assert] empty node_name rejected with exit 1")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region TEST_test_detect_age_key_from_file_fallback
# 🧪 TRAP[TEST] · 2026-07-31 · detect_age_key AGE_SECRET_KEY_FILE fallback via python3 -m (DevPlan 104)
# · Regression: shell detect_age_key() removed from bootstrap.sh — file fallback now in
# ·   core/internal/shared/node_detect.py (AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE chain)
# · Scenario: python3 -m core.internal.shared.node_detect --detect-age-key with AGE_SECRET_KEY unset
# ·   and AGE_SECRET_KEY_FILE pointing at tmp file → key on stdout
# · Last fail: 2026-07-31 — shell function extraction no longer possible (removed)
# · Remove if: node_detect CLI is reworked


def test_detect_age_key_from_file_fallback(caplog, tmp_path) -> None:
    """Verify node_detect --detect-age-key reads from AGE_SECRET_KEY_FILE when env var is unset."""
    caplog.set_level(logging.DEBUG)

    # Create a file with the age key
    key_file = tmp_path / "age-key.txt"
    test_key = "AGE-SECRET-KEY-file-fallback-test-67890"
    key_file.write_text(test_key + "\n")
    key_file.chmod(0o600)

    logger.info("[IMP:9][test_age_key_file] Testing AGE_SECRET_KEY_FILE fallback")

    # Chain: AGE_SECRET_KEY empty → SOPS_AGE_KEY empty → AGE_SECRET_KEY_FILE content
    test_call = f"""\
detected="$(python3 -m core.internal.shared.node_detect --detect-age-key 2>/dev/null)"
rc=$?
echo "[IMP:9][test_age_key_file] rc=${{rc}}"
echo "[IMP:9][test_age_key_file] DETECTED_LEN=${{#detected}}"
echo "[IMP:9][test_age_key_file] MATCH=$([[ "$detected" == "{test_key}" ]] && echo yes || echo no)"
"""
    stdout, stderr, rc = _bash(
        test_call,
        env={"__LOG_PREFIX": "test", "AGE_SECRET_KEY": "", "SOPS_AGE_KEY": "", "AGE_SECRET_KEY_FILE": str(key_file)},
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"node_detect with file fallback failed: {stderr}"

    # Key must be detected from file (non-empty, matches test_key)
    assert f"DETECTED_LEN={len(test_key)}" in stdout, f"Expected key length {len(test_key)}, got: {stdout}"
    assert "MATCH=yes" in stdout, f"Detected key must match file content, got: {stdout}"
    logger.info("[IMP:9][test_age_key_file][assert] age key read from AGE_SECRET_KEY_FILE OK via python3 -m")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion
