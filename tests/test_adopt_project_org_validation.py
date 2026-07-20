#!/usr/bin/env python3
# GREP_SUMMARY: adopt-project org validation fail-fast ghcr lowercase uses exact-case context-mismatch node-yaml
# STRUCTURE: ▶ _bash(_extract_func) / _run_adopt_bash → ○ 4 test functions → ◇ assert contract → ⊕ LDD trajectory → ⎋ IMP:9/10 assertion
# region MODULE_CONTRACT
## @purpose  Tests for Contract 4 — adopt-project.sh org/context/casing validation (DevPlan 008 T4).
##           Verifies: fail-fast without --org, lowercase ghcr paths, exact-case uses:,
##           node.yaml context mismatch detection (casing drift → WARN + adapt).
## @scope    4 test functions from $TEST_SPEC (test_adopt_fails_without_org,
##           test_ghcr_path_lowercased, test_uses_preserves_exact_case,
##           test_context_mismatch_detected). Each test extracts target bash function(s)
##           or sources the script in a sandboxed tmp_path environment.
## @invariants
##   - Zero hardcoded paths — all tests use tmp_path for PROJECTS_ROOT
##   - Fixture ai-platform.yaml/node.yaml created fresh per test
##   - LDD trajectory printed from stderr/stdout of bash subprocess
##   - Success tests assert at least one IMP:9 log; failure tests assert IMP:10
## @rationale Contract 4 prevents Debt D3 recurrence (config-drift from "personal" default).
##            Without these tests, silent org drift would re-enter on next adopt-project call.
## @changes CREATED: 2026-07-17 · T4 — Contract 4 org/context/casing tests
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
import subprocess

import pytest

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

TEST_DIR: str = os.path.dirname(__file__)
PROJECT_ROOT: str = os.path.join(TEST_DIR, "..")
PLATFORM_ROOT: str = os.path.normpath(PROJECT_ROOT)
ADOPT_SCRIPT_PATH: str = os.path.join(PROJECT_ROOT, "core", "internal", "scaffold", "adopt-project.sh")


# ── LOG STUBS (for _extract_func tests) ────────────────────────────────────

# region LOG_STUBS
LOG_STUBS = """
log_imp() {
    local imp="$1" block="$2" msg="$3"
    local prefix="${__LOG_PREFIX:-test}"
    if [ "${block}" = "-" ] || [ -z "${block}" ]; then
        block="${FUNCNAME[1]:-main}"
    fi
    echo "[IMP:${imp}][${prefix}][${block}] ${msg}" >&2
}
"""
# endregion LOG_STUBS


# ── Helpers ─────────────────────────────────────────────────────────────────

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

    ## @purpose  Isolate bash function(s) and execute them with test arguments
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
    """Print IMP:7-10 lines from bash output. Returns True if IMP:9+ found.

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


def _run_adopt_bash(
    tmp_path: pathlib.Path,
    code: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Source adopt-project.sh (main auto-execute stripped) and run test code.

    ## @purpose  Isolate the adopt-project.sh script in a tmp_path sandbox.
    ##           Strips the trailing `main "$@"` call so only function definitions load,
    ##           then runs test code that sets globals and calls functions.
    ## @io       tmp_path, code, env → CompletedProcess
    ## @complexity O(1) — single subprocess.run with 15s timeout
    ## @note     Uses re.sub to strip `main "$@"` from the end of the file.
    ##           Also unsets the main function after source as a safety guard.
    """
    with open(ADOPT_SCRIPT_PATH) as f:
        content = f.read()

    # Strip the `main "$@"` auto-execute call at end of file
    content = re.sub(r'\n\s*main\s+"\$@"\s*\n?$', "\n", content.rstrip())

    test_script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n" + content + "\n" + "trap - ERR EXIT\n" + "unset -f main\n" + code + "\n"
    )

    script_file = tmp_path / "test_adopt_runner.sh"
    script_file.write_text(test_script)
    script_file.chmod(0o755)

    merged_env = os.environ.copy()
    merged_env["__LOG_PREFIX"] = "test"
    merged_env["PLATFORM_ROOT"] = PLATFORM_ROOT
    merged_env["PROJECTS_ROOT"] = str(tmp_path)
    if env:
        merged_env.update(env)

    return subprocess.run(
        ["bash", str(script_file)],
        capture_output=True,
        text=True,
        timeout=15,
        env=merged_env,
    )


# endregion HELPERS


# ═════════════════════════════════════════════════════════════════════════════
# TESTS: Contract 4 — adopt-project.sh org/context/casing
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_adopt_derives_org_from_path
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-20 · adopt-project derives org from directory path (D2)
# · Regression: if context field is re-added to YAML, path derivation may be shadowed
# · Scenario: project at projects/testorg/myproject/ → org derived as testorg
# · Last fail: N/A (updated for D2 context removal)
# · Remove if: path-based org derivation is replaced
def test_adopt_derives_org_from_path(caplog, tmp_path) -> None:
    """adopt-project.sh derives PROJECT_ORG from directory path when no --org is given.

    # ▶ parse_args "--dir" <proj_dir_in_org> (no --org, no PLATFORM_ORG)
    #   → ◇ path derivation: basename(dirname(proj_dir)) → testorg → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "testorg" / "myproject"
    proj_dir.mkdir(parents=True)

    test_call = f"""set -euo pipefail
# Initialize globals (GLOBALS section in adopt-project.sh)
PROJECT_NAME=""
PROJECT_NODE=""
PROJECT_DOMAIN=""
FORCE=0
parse_args "--dir" "{proj_dir}"
echo "[IMP:9][test] PROJECT_ORG=${{PROJECT_ORG}}"
"""
    stdout, stderr, rc = _test_func(
        ADOPT_SCRIPT_PATH,
        ["parse_args", "usage"],
        test_call,
        env={"__LOG_PREFIX": "test"},
        preamble=LOG_STUBS,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Expected exit code 0, got {rc}: stderr={stderr}"
    assert "PROJECT_ORG=testorg" in stdout, f"Expected org derived as 'testorg', got:\n{stdout}"

    logger.info("[IMP:9][test_adopt_derives_org_from_path][assert] Org 'testorg' derived from path")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_adopt_derives_org_from_path


# region TEST_test_ghcr_path_lowercased
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-17 · ghcr IMAGE_NAME uses lowercase org
# · Regression: if ${workflow_org,,} is reverted to ${workflow_org}, ghcr paths break
# · Scenario: --org TronyxLab → IMAGE_NAME ghcr.io/tronyxlab/... (lowercase)
# · Last fail: N/A (new test)
# · Remove if: ghcr path casing requirement changes
def test_ghcr_path_lowercased(caplog, tmp_path) -> None:
    """simplify_deploy_yml() writes IMAGE_NAME with lowercase org for ghcr.io.

    # ▶ PROJECT_ORG="TronyxLab" → simplify_deploy_yml
    #   → ◇ workflow_org,${workflow_org,,} → IMAGE_NAME ghcr.io/tronyxlab/myproject → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()
    (proj_dir / ".github" / "workflows").mkdir(parents=True)
    (proj_dir / ".github" / "workflows" / "deploy.yml").write_text("name: Deploy\non: push\njobs: {}\n")

    code = f"""
PROJECT_DIR="{proj_dir}"
PROJECT_ORG="TronyxLab"
PROJECT_NAME="myproject"
FORCE=1

simplify_deploy_yml
rc=$?
echo "[IMP:9][test] exit_code=$rc"
echo "=== DEPLOY_YML ==="
cat "{proj_dir}/.github/workflows/deploy.yml"
echo "=== END ==="
"""
    result = _run_adopt_bash(tmp_path, code)

    found_imp9 = _print_ldd(result.stderr, result.stdout)
    assert result.returncode == 0, f"subprocess failed rc={result.returncode}: {result.stderr}"

    output = result.stdout
    assert "exit_code=0" in output, f"simplify_deploy_yml failed: {output}"
    # IMAGE_NAME must contain lowercase org
    assert "IMAGE_NAME: ghcr.io/tronyxlab/myproject" in output, (
        f"Expected lowercase IMAGE_NAME 'ghcr.io/tronyxlab/myproject' in:\n{output}"
    )
    # IMAGE_NAME must NOT contain uppercase org
    assert "IMAGE_NAME: ghcr.io/TronyxLab/myproject" not in output, (
        f"IMAGE_NAME must NOT contain uppercase org in ghcr path:\n{output}"
    )

    logger.info("[IMP:9][test_ghcr_path_lowercased][assert] ghcr IMAGE_NAME lowercase verified")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_ghcr_path_lowercased


# region TEST_test_uses_preserves_exact_case
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-17 · uses: workflow reference preserves exact org case
# · Regression: if uses: switches to lowercase org, GitHub workflow resolution fails
# · Scenario: --org TronyxLab → uses: TronyxLab/ai-platform/... (exact case)
# · Last fail: N/A (new test)
# · Remove if: GitHub Actions workflow resolution changes
def test_uses_preserves_exact_case(caplog, tmp_path) -> None:
    """simplify_deploy_yml() writes uses: with exact-case org (NOT lowered).

    # ▶ PROJECT_ORG="TronyxLab" → simplify_deploy_yml
    #   → ◇ workflow_org (no ,,) → uses: TronyxLab/ai-platform/...@main → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()
    (proj_dir / ".github" / "workflows").mkdir(parents=True)
    (proj_dir / ".github" / "workflows" / "deploy.yml").write_text("name: Deploy\non: push\njobs: {}\n")

    code = f"""
PROJECT_DIR="{proj_dir}"
PROJECT_ORG="TronyxLab"
PROJECT_NAME="myproject"
FORCE=1

simplify_deploy_yml
rc=$?
echo "[IMP:9][test] exit_code=$rc"
echo "=== DEPLOY_YML ==="
cat "{proj_dir}/.github/workflows/deploy.yml"
echo "=== END ==="
"""
    result = _run_adopt_bash(tmp_path, code)

    found_imp9 = _print_ldd(result.stderr, result.stdout)
    assert result.returncode == 0, f"subprocess failed rc={result.returncode}: {result.stderr}"

    output = result.stdout
    assert "exit_code=0" in output, f"simplify_deploy_yml failed: {output}"
    # uses: must contain EXACT CASE org (TronyxLab, not tronyxlab)
    assert "uses: TronyxLab/ai-platform/.github/workflows/deploy-project.yml@main" in output, (
        f"Expected exact-case 'uses: TronyxLab/ai-platform/...' in:\n{output}"
    )
    # uses: must NOT contain lowercase org
    assert "uses: tronyxlab/ai-platform/" not in output, f"uses: must NOT contain lowercase org:\n{output}"

    logger.info("[IMP:9][test_uses_preserves_exact_case][assert] uses: exact-case verified")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_uses_preserves_exact_case


# region TEST_test_context_mismatch_detected
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-17 · node.yaml context casing mismatch detected and adapted
# · Regression: if casing drift goes undetected, ghcr paths break on case-sensitive FS
# · Scenario: node.yaml context="tronyxlab" vs --org TronyxLab → WARN + node.yaml variant wins
# · Last fail: N/A (new test)
# · Remove if: node.yaml context validation is removed
def test_context_mismatch_detected(caplog, tmp_path) -> None:
    """validate_org_against_node_yaml() detects casing mismatch, warns, adopts node.yaml variant.

    # ▶ node.yaml context='tronyxlab', --org TronyxLab → validate_org_against_node_yaml
    #   → ◇ case-insensitive match (same name) → ◇ casing differs → WARN → PROJECT_ORG=tronyxlab → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()

    # Create node.yaml with lowercase context, at path matching uppercase PROJECT_ORG
    # (PROJECTS_ROOT/TronyxLab/node-configs/tronyx-vps/node.yaml)
    node_yaml_dir = tmp_path / "TronyxLab" / "node-configs" / "tronyx-vps"
    node_yaml_dir.mkdir(parents=True)
    (node_yaml_dir / "node.yaml").write_text("context: tronyxlab\n")

    test_call = f"""set -euo pipefail
# Initialize globals (GLOBALS section in adopt-project.sh)
PROJECTS_ROOT="{tmp_path}"
PROJECT_NAME=""
PROJECT_NODE=""
PROJECT_DOMAIN=""
FORCE=0
parse_args "--dir" "{proj_dir}" "--org" "TronyxLab" "--node" "tronyx-vps"
echo "[IMP:9][test] after_parse_args PROJECT_ORG=${{PROJECT_ORG}}"
validate_org_against_node_yaml
echo "[IMP:9][test] after_validation PROJECT_ORG=${{PROJECT_ORG}}"
echo "FINAL_ORG=${{PROJECT_ORG}}"
"""
    stdout, stderr, rc = _test_func(
        ADOPT_SCRIPT_PATH,
        ["parse_args", "usage", "validate_org_against_node_yaml"],
        test_call,
        env={"__LOG_PREFIX": "test", "PLATFORM_ROOT": PLATFORM_ROOT, "PROJECTS_ROOT": str(tmp_path)},
        preamble=LOG_STUBS,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"Expected exit 0, got {rc}: stderr={stderr}"

    # Verify casing mismatch WARN was emitted
    combined = stderr + "\n" + stdout
    assert "Casing mismatch" in combined, f"Expected 'Casing mismatch' WARN in output:\n{combined[:1000]}"

    # PROJECT_ORG must adopt node.yaml's casing (lowercase)
    assert "FINAL_ORG=tronyxlab" in stdout, f"Expected FINAL_ORG=tronyxlab (from node.yaml), got:\n{stdout[:500]}"

    logger.info("[IMP:9][test_context_mismatch_detected][assert] Casing drift detected and adapted")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_context_mismatch_detected
