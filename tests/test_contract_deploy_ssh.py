#!/usr/bin/env python3
# GREP_SUMMARY: contract-test scp-deliver facade core_deliverer python3-module delegation scp_to_server PATH-stub subprocess
# STRUCTURE: ▶ source scp-deliver.sh (real facade) → ∋ PATH-intercept python3 stub → ◇ scp_to_server delegate → ◇ stub argv: -m core_deliverer deliver + 5 flags → ⊕ exit passthrough (0|1) + stderr diagnostics → ⎋ exit0|exit1
# region MODULE_CONTRACT
## @purpose  Contract tests for scp-deliver.sh scp_to_server() facade (DevPlan 108): verifies
##           delegation to `python3 -m core.internal.bootstrap.core_deliverer deliver` with the
##           exact CLI contract (--host/--node/--node-configs-dir/--core-dir/--remote-user),
##           exit-code passthrough (0|1) and stderr diagnostics passthrough.
## @scope    Four test cases: full-args delegation, no-secrets pass-through (SKIP decision is
##           Python's), failure → exit 1 passthrough, FATAL diagnostic passthrough.
##           All run the REAL scp-deliver.sh facade with a PATH-intercepted python3 stub.
## @invariants
##   - scp_to_server invokes python3 with `-m core.internal.bootstrap.core_deliverer deliver`
##   - All 5 flags forwarded: --host/--node/--node-configs-dir/--core-dir/--remote-user
##   - DRY_RUN=true → --dry-run appended; DRY_RUN=false/unset → no --dry-run
##   - python3 exit code 0|1 propagated (shell || return 1 passthrough contract)
##   - python3 stderr diagnostics NOT swallowed by the facade
##   - All tests use tmp_path for isolation (Zero Hardcode Rule)
## @rationale DevPlan 108 (Strangler-Fig Tier 2): scp_to_server стал тонким фасадом над
##            core_deliverer.py — shell-моки rsync/ssh больше не работают (логика в Python).
##            Контракт фасада = точная python3-инвокация: тест FAILs при изменении имени
##            модуля или флагов. Skip/fail-fast логика покрыта tests/unit/test_core_deliverer.py.
## @changes 2026-07-31 | DevPlan 108 — 4 теста переориентированы с shell-моков на
##           PATH-intercept python3 stub (контракт фасада)
# endregion MODULE_CONTRACT

import os
import pathlib
import subprocess

import pytest

# DevPlan 116 B5 T2 (D1): SSH_OPTS — единый SoT (для моделирования ssh_opts --shell в stub)
from core.internal.shared.ssh_opts import SSH_OPTS

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)


# ── Helpers ─────────────────────────────────────────────────────────────────


# NOTE: _run_bash helper (sourcing the legacy deploy shell) removed in DevPlan 116
# B8 T5.5 — the file it sourced was deleted (DevPlan 089); the helper had no callers.
# parse_ssh_command tests removed — the legacy deploy shell was deleted (DevPlan 089),
# replaced by core/internal/deploy/orchestrator.py/orchestrator_cli.py (Python).
# The SSH forced-command parsing logic is now in the Python orchestrator.


# ═════════════════════════════════════════════════════════════════════════════
# APPENDED FROM test_char_bootstrap_scp_ssh.py (A2 merge)
# characterization tests for bootstrap.sh functions
# ═════════════════════════════════════════════════════════════════════════════

import logging
import re

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# PATH CONSTANTS
# ═══════════════════════════════════════════════════════════════════
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(TEST_DIR, "..")
# BOOTSTRAP_SH removed (DevPlan 104) — auto_detect_node_name migrated to
# python3 -m core.internal.shared.node_detect; coverage lives in tests/unit/test_node_detect.py.
SCP_DELIVER_SH = os.path.join(PROJECT_ROOT, "core", "internal", "bootstrap", "scp-deliver.sh")
# REMOTE_CMD_SH removed — build_ssh_cmd deleted (DevPlan 089: legacy deploy shell migrated to Python)

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

# Golden facade delegation contract (DevPlan 108) — the python3 module invocation that
# scp_to_server() MUST produce. Changing the module name or the subcommand breaks the
# facade contract and silently kills the whole Core delivery channel.
GOLDEN_DELIVER_MODULE = "-m core.internal.bootstrap.core_deliverer deliver"

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


# NOTE: detect_age_key tests removed — bootstrap.sh detect_age_key() now delegates to
# core/internal/shared/node_detect.py (Python; age_key.py compat-шим удалён волной 118 D3).
# Shell golden log messages are no longer produced.
# The Python module has its own unit tests in tests/unit/.
# ═══════════════════════════════════════════════════════════════════
# ⚠️ NOTE: auto_detect_node_name tests removed (DevPlan 104) — the shell function was
#   deleted from bootstrap.sh/converge.sh and replaced by python3 -m
#   core.internal.shared.node_detect --detect-node-name. Coverage (single/multiple/no-nodes/
#   scripts-secrets-exclusion) is fully duplicated by tests/unit/test_node_detect.py
#   (TestAutoDetectNodeName ×4) per QA Review §Оставшиеся риски recommendation.
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# scp_to_server
# ═══════════════════════════════════════════════════════════════════
# ⚠️ NOTE (DevPlan 108): scp_to_server() is now a THIN FACADE — it delegates to
#   `python3 -m core.internal.bootstrap.core_deliverer deliver` (mkdir + 5 rsync фаз
#   живут в Python). Shell-моки rsync/ssh/ssh_exec больше НЕ работают: Python-процесс
#   их не видит. Контракт фасада = точная python3-инвокация + exit/stderr passthrough.
#   Мы тестируем его через PATH-intercept python3 stub: stub записывает argv в stderr
#   (формат "[IMP:9][mock-python3] ...") и моделирует exit-код Python-модуля.
#   Skip/fail-fast логика фаз покрыта tests/unit/test_core_deliverer.py (14 тестов).
# ═══════════════════════════════════════════════════════════════════

# region FACADE_HELPER


def _run_scp_facade(
    tmp_path: pathlib.Path,
    host: str,
    node: str,
    ncd: pathlib.Path,
    cd: pathlib.Path,
    stub_exit: int = 0,
    stub_stderr: str = "",
    env_extra: dict | None = None,
    dry_run: str = "false",
) -> tuple[str, str, int]:
    """Run the REAL scp-deliver.sh scp_to_server() with a PATH-intercepted python3 stub.

    ## @purpose — Contract-test the facade delegation (DevPlan 108): a stub `python3`
    ##            executable on PATH captures the invocation argv (stderr) and models the
    ##            Python module's exit code (0 success / 1 CoreDeliveryError → `|| return 1`).
    ## @io — args → (stdout, stderr, returncode)
    ## @complexity O(1) — single subprocess.run with 15s timeout
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "python3"
    stub_extra = f'echo "{stub_stderr}" >&2\n' if stub_stderr else ""
    # DevPlan 116 B5 T2 (D1): lib/ssh.sh SSH_OPTS_COMMON генерируется из
    # core.internal.shared.ssh_opts (python3 -m ... --shell). Стуб моделирует ЭТОТ вызов
    # (флаги в stdout, без mock-маркера — как реальный python3), чтобы ssh.sh-фасад
    # успешно загрузил SSH_OPTS_COMMON (иначе fail-fast return 1 рвёт source под set -e).
    ssh_opts_flags = " ".join(SSH_OPTS)
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$*" == *"core.internal.shared.ssh_opts --shell"* ]]; then echo "{ssh_opts_flags}"; exit 0; fi\n'
        'echo "[IMP:9][mock-python3] $*" >&2\n' + stub_extra + f"exit {stub_exit}\n"
    )
    stub.chmod(0o755)

    script = tmp_path / "facade_test.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'export PATH="{bin_dir}:$PATH"\n'
        f'export DRY_RUN="{dry_run}"\n'
        f'source "{SCP_DELIVER_SH}"\n'
        # NOTE: sourcing scp-deliver.sh activates `set -euo pipefail` (via paths.sh →
        # module-interface.sh). `|| rc=$?` is the set -e-safe way to capture the
        # function's return code instead of letting bash exit on non-zero.
        "rc=0\n"
        f'scp_to_server "{host}" "{node}" "{ncd}" "{cd}" || rc=$?\n'
        'echo "[IMP:9][test][scp] exit_code=$rc"\n'
    )
    script.chmod(0o755)

    full_env = os.environ.copy()
    full_env["__LOG_PREFIX"] = "test"
    if env_extra:
        full_env.update(env_extra)

    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        env=full_env,
    )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


# endregion FACADE_HELPER


# region test_scp_to_server_all_phases
# 🧪 TRAP[TEST] · 2026-07-31 · scp_to_server facade → core_deliverer delegation (DevPlan 108)
# · Regression: if the facade stops delegating to python3 -m core.internal.bootstrap.core_deliverer
# ·   deliver, the whole Core delivery channel (mkdir + 5 rsync фаз) silently drops
# · Scenario: source real scp-deliver.sh, PATH-intercept python3 stub, call scp_to_server with
# ·   full delivery tree → stub must receive module + all 5 flags
# · Last fail: 2026-07-31 — shell-mock tests broke (rsync/ssh moved to Python, exit_code=1)
# · Remove if: scp_to_server is removed or reimplemented
@pytest.mark.contract
def test_scp_to_server_all_phases(caplog, tmp_path) -> None:
    """scp_to_server() delegates to core_deliverer deliver with all 5 flags."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME

    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    (ncd / node / "secrets").mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    stdout, stderr, _rc = _run_scp_facade(tmp_path, host, node, ncd, cd)
    found_imp9 = _print_ldd(stderr, stdout)
    assert "exit_code=0" in stdout, f"scp_to_server failed: stdout={stdout[:300]}, stderr={stderr[:300]}"

    mock_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-python3]" in line]
    assert len(mock_lines) == 1, f"Expected exactly 1 python3 stub invocation, got {len(mock_lines)}"
    invocation = mock_lines[0]
    assert GOLDEN_DELIVER_MODULE in invocation, f"Facade must invoke core_deliverer deliver: {invocation}"
    assert f"--host {host}" in invocation
    assert f"--node {node}" in invocation
    assert f"--node-configs-dir {ncd}" in invocation
    assert f"--core-dir {cd}" in invocation
    assert "--remote-user root" in invocation, f"Default remote-user must be root: {invocation}"
    assert "--dry-run" not in invocation, f"DRY_RUN=false must NOT pass --dry-run: {invocation}"

    logger.info("[IMP:9][test][scp] Facade delegated with all flags: PASS")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion test_scp_to_server_all_phases


# region test_scp_to_server_no_secrets
# 🧪 TRAP[TEST] · 2026-07-31 · scp_to_server passes node-configs-dir through when secrets absent
# · Regression: if the facade pre-checks secrets locally (shell) it would duplicate/break the
# ·   Python skip decision (deliver_secrets IMP:8 SKIP) — the facade must stay a pass-through
# · Scenario: no secrets/ dir locally → facade still delegates with --node-configs-dir/--node
# · Last fail: 2026-07-31 — shell-mock tests broke (secrets SKIP now decided in Python)
# · Remove if: scp_to_server is removed or secrets handling changes
@pytest.mark.contract
def test_scp_to_server_no_secrets(caplog, tmp_path) -> None:
    """scp_to_server() passes node/node-configs-dir through — secrets SKIP is Python's decision."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME

    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    stdout, stderr, _rc = _run_scp_facade(tmp_path, host, node, ncd, cd)
    found_imp9 = _print_ldd(stderr, stdout)
    assert "exit_code=0" in stdout, f"scp_to_server failed: stdout={stdout[:300]}, stderr={stderr[:300]}"

    mock_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-python3]" in line]
    assert len(mock_lines) == 1, f"Expected exactly 1 python3 stub invocation, got {len(mock_lines)}"
    invocation = mock_lines[0]
    # Facade must forward the dirs so Python's deliver_secrets() can make the SKIP decision
    assert GOLDEN_DELIVER_MODULE in invocation
    assert f"--node {node}" in invocation
    assert f"--node-configs-dir {ncd}" in invocation

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] Facade pass-through without secrets: PASS")


# endregion test_scp_to_server_no_secrets


# region test_scp_to_server_ssh_failure
# 🧪 TRAP[TEST] · 2026-07-31 · scp_to_server propagates python3 exit 1 (fail-fast passthrough)
# · Regression: if the facade swallows the Python module's non-zero exit, a failed ssh mkdir
# ·   (or any CoreDeliveryError) would report success while the rsync phases never ran
# · Scenario: python3 stub exits 1 (models CoreDeliveryError from ensure_remote_dirs) →
# ·   facade must propagate exit_code=1, delegation still complete
# · Last fail: 2026-07-31 — shell-mock tests broke (mkdir failure now raised in Python)
# · Remove if: error handling in scp_to_server is changed
@pytest.mark.contract
def test_scp_to_server_ssh_failure(caplog, tmp_path) -> None:
    """scp_to_server() propagates the python3 module's exit 1 (fail-fast passthrough)."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME
    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    (ncd / "secrets").mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    stdout, stderr, _rc = _run_scp_facade(tmp_path, host, node, ncd, cd, stub_exit=1)
    found_imp9 = _print_ldd(stderr, stdout)
    # Facade return-code passthrough: python3 exit 1 → scp_to_server exit 1
    assert "exit_code=1" in stdout, f"Expected exit_code=1, got stdout: {stdout[:300]}, stderr: {stderr[:300]}"

    # Delegation still happened (the failure decision lives inside the Python module)
    mock_lines = [line for line in stderr.split("\n") if "[IMP:9][mock-python3]" in line]
    assert len(mock_lines) == 1
    assert GOLDEN_DELIVER_MODULE in mock_lines[0]

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] Python failure → facade exit 1 passthrough: PASS")


# endregion test_scp_to_server_ssh_failure


# region test_scp_to_server_rsync_core_failure
# 🧪 TRAP[TEST] · 2026-07-31 · scp_to_server passes python3 stderr diagnostics through (rsync core FATAL)
# · Regression: if the facade swallowed python3 stderr, the [IMP:10] FATAL rsync core/ diagnostic
# ·   would be invisible in bootstrap logs → silent deploy corruption
# · Scenario: python3 stub exits 1 + emits the Python FATAL line (deliver_core format) →
# ·   facade exit_code=1 and the diagnostic visible in stderr
# · Last fail: 2026-07-31 — shell-mock tests broke (rsync core failure now raised in Python)
# · Remove if: error handling in scp_to_server is changed
@pytest.mark.contract
def test_scp_to_server_rsync_core_failure(caplog, tmp_path) -> None:
    """scp_to_server() passes the Python module's FATAL rsync core/ diagnostic through."""
    caplog.set_level(logging.DEBUG)

    host = GOLDEN_SSH_HOST
    node = GOLDEN_NODE_NAME
    ncd = tmp_path / "node-configs"
    (ncd / node).mkdir(parents=True)
    cd = tmp_path / "core"
    cd.mkdir(parents=True)

    fatal_line = "[IMP:10][deliver_core][error] FATAL: rsync core/ failed for"
    stdout, stderr, _rc = _run_scp_facade(
        tmp_path,
        host,
        node,
        ncd,
        cd,
        stub_exit=1,
        stub_stderr=fatal_line,
    )
    found_imp9 = _print_ldd(stderr, stdout)
    assert "exit_code=1" in stdout, f"Expected exit_code=1, got stdout: {stdout[:300]}, stderr: {stderr[:300]}"
    # Stderr diagnostics from the Python module must NOT be swallowed by the facade
    assert fatal_line in stderr, f"Expected FATAL core diagnostic passthrough, got: {stderr[:500]}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][scp] rsync core/ failure diagnostic passthrough: PASS")


# endregion test_scp_to_server_rsync_core_failure


# -- build_ssh_cmd tests removed (DevPlan 089: legacy deploy shell scripts deleted,
# migrated to DeployOrchestrator Python). The 5 tests were:
#   test_build_ssh_cmd_no_cli_age_key, test_build_ssh_cmd_has_env_export,
#   test_build_ssh_cmd_empty_key, test_build_ssh_cmd_owner_key_quoting,
#   test_build_ssh_cmd_passthrough_args
# All tested functions from remote-cmd.sh which no longer exists.
