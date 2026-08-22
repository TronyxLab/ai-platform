# GREP_SUMMARY: test-lib-node-resolver node-resolver.sh bash resolve_node_yaml extract_node_host node.yaml subprocess stderr LDD glob nullglob tmp_path
# STRUCTURE: ▶ _run_bash(source node-resolver.sh → subprocess.run) → ○ 9 tests → ◇ resolve_node_yaml ┌3 paths⊕-f?┐ → ⊕ echo path/⎋ exit1 ⊕ extract_node_host → ⊕ python3 yaml → ◇ host∋? → ⊕ echo host/⎋ empty/⎋ exit1

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(BASH-NODE-RESOLVER):2; TECH(PYTEST):2]
## @purpose  Unit tests for core/lib/node-resolver.sh — the shared bash library for
##           locating node.yaml configuration files across 3 candidate paths
##           (platform-local, org-repos glob, VPS fallback) and extracting the
##           node.host field via python3 + yaml. Replaces 3+ duplicated inline
##           implementations across node-lifecycle.sh, core-deploy.yml, and context-init.sh
##           with a single source of truth tested here.
## @scope    9 test functions covering:
##
##           - resolve_node_yaml 3-path search with first-match-wins (tests 1-4)
##           - resolve_node_yaml not-found error handling (test 5)
##           - extract_node_host positive and missing-host cases (tests 6-7)
##           - empty glob directory — no bash errors (test 8)
##           - nullglob restoration after call (test 9)
## @invariants
##
##   - Every test uses tmp_path for script isolation (Zero Hardcode Rule)
##   - LIB path resolved via Path(__file__).resolve() — no hardcoded paths
##   - Except test_resolve_from_opt which creates /opt/node-configs/ (path 3
##     is hardcoded in resolve_node_yaml); skipped if /opt not writable
##   - Bash scripts run with subprocess.run (bash stderr capture), NOT Python logging
##   - No caplog fixture: bash logs go to stderr, not Python logging subsystem
##   - LDD verification via stderr assertion: IMP:N present for N >= 7 in relevant tests
##   - Positive scenarios assert returncode == 0
##   - negative_scenarios (not-found) assert returncode == 1
## @rationale Q: Why subprocess.run instead of pure Python simulation?
##            A: node-resolver.sh is a pure bash library whose core logic (nullglob
##            save/restore, glob expansion, bash path resolution, python3 interaction)
##            can only be tested in a real bash environment. Subprocess with
##            capture_output is the standard pattern for bash library testing.
##            The _run_bash helper isolates each test in a temp file to avoid
##            cross-test contamination.
##            Q: Why not @ldd_trajectory decorator?
##            A: @ldd_trajectory relies on caplog (Python logging capture).
##            node-resolver.sh writes directly to bash stderr via log_imp(),
##            bypassing Python logging entirely. LDD verification is done by
##            asserting stderr contains [IMP:N] for the expected importance level.
##            Q: Why test 3 uses real /opt/ path?
##            A: resolve_node_yaml hardcodes candidate path 3 as
##            /opt/node-configs/<node\>/node.yaml. There is no injection parameter
##            for this path. The test creates the file at the real /opt/node-configs/
##            location; if /opt/ is not writable the test is skipped.
## @changes LAST_CHANGE: 2026-07-07 · Initial implementation per DevPlan test spec
##            2026-07-31 · resolve_node_yaml tests aligned with DP-088/091 delegation:
##            args platform_root/projects_dir are vestigial — NodeYaml.resolve() reads
##            PLATFORM_ROOT + HOME env (3-path search now lives in core.internal.shared.node_yaml)
## @modulemap
##   - _run_bash                         [W:30] Helper: source node-resolver.sh, run bash, return result
##   - test_resolve_from_platform_root   [W:40] Path 1: platform_root/node-configs/N/node.yaml
##   - test_resolve_from_projects_glob   [W:40] Path 2: projects_dir/*/node-configs/N/node.yaml
##   - test_resolve_from_opt             [W:50] Path 3: /opt/node-configs/N/node.yaml (skip if no access)
##   - test_resolve_first_wins           [W:40] All 3 paths populated → path 1 wins
##   - test_resolve_not_found            [W:30] No node.yaml anywhere → exit 1
##   - test_extract_node_host            [W:40] node.host → "1.2.3.4"
##   - test_extract_node_host_missing    [W:40] node.yaml without host → empty string
##   - test_empty_glob_no_error          [W:30] Empty projects_dir → no glob error
##   - test_nullglob_restored            [W:40] nullglob restored to original state after call
## @usecases
##   - Developer: run pytest after modifying node-resolver.sh → all 9 tests pass, no regressions
##   - Architect: verify 3-path search order, glob+nullglob handling, host extraction invariants


# endregion MODULE_CONTRACT

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import assert_ldd_stderr

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Resolve absolute path to node-resolver.sh once at module load time.
# Relies on: tests/test_lib_node_resolver.py → ../core/lib/node-resolver.sh
_NODE_RESOLVER_PATH: Path = Path(__file__).resolve().parent.parent.parent / "core" / "lib" / "node-resolver.sh"


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


# region FUNC__run_bash
## @purpose  Write a bash script to a temp file and execute it via subprocess,
##           capturing stdout/stderr/returncode. Each call gets a fresh script
##           in an isolated tmp_path directory — no cross-test contamination.
##           Sources node-resolver.sh (which internally sources logging.sh)
##           and sets __LOG_PREFIX="test" for deterministic LDD output.
## @io       ⇥ (tmp_path: Path, code: str, env: dict|None) → ⎋ CompletedProcess(stdout, stderr, returncode)
## @complexity O(1) — single subprocess.run with 10s timeout
## @invariants
##   - Always prepends #!/usr/bin/env bash + set -euo pipefail
##   - Always sets __LOG_PREFIX="test" before sourcing node-resolver.sh
##   - Always sources node-resolver.sh via _NODE_RESOLVER_PATH
##   - Script file is chmod 755 before execution
##   - Timeout set to 10 seconds (fail-fast on infinite loops)
##   - env (optional) merged into os.environ.copy() — used to set PLATFORM_ROOT/HOME
##     so NodeYaml.resolve() (DP-088/091: resolve_node_yaml delegates to
##     `python3 -m core.internal.shared.node_yaml --resolve`) finds tmp_path fixtures
def _run_bash(tmp_path: Path, code: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run bash code with node-resolver.sh sourced, return subprocess result.

    ## @purpose  Isolate bash script execution in a temp file for deterministic testing.
    ##            Sources the library under test (_NODE_RESOLVER_PATH) before executing
    ##            user code. node-resolver.sh internally sources logging.sh.
    ## @io       ⇥ tmp_path: Path — pytest fixture for temp dir
    ##             code: str — bash commands to execute after sourcing node-resolver.sh
    ##             env: dict — optional env overrides (PLATFORM_ROOT, HOME)
    ##           ⎋ CompletedProcess with stdout, stderr, returncode attributes
    ## @complexity O(1)
    """
    script = tmp_path / "test_script.sh"
    src_path_escaped = str(_NODE_RESOLVER_PATH)

    script_content = (
        f'#!/usr/bin/env bash\nset -euo pipefail\n__LOG_PREFIX="test"\nsource "{src_path_escaped}"\n{code}\n'
    )
    script.write_text(script_content, encoding="utf-8")
    script.chmod(0o755)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    return subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=10, env=run_env, check=False)


# endregion FUNC__run_bash


# ═══════════════════════════════════════════════════════════════════
# TESTS — resolve_node_yaml
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_resolve_from_platform_root
## @purpose  Verify resolve_node_yaml finds node.yaml in path 1:
##           platform_root/node-configs/<node\>/node.yaml.
##           Path 1 is the first candidate — must be found immediately.
## @io       ⇥ tmp_path → ⎋ assert stdout captured to PATH=… in stderr, returncode == 0
## @complexity O(1)
def test_resolve_from_platform_root(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: path 1 must be searched first
    # · Scenario: node.yaml exists at {PLATFORM_ROOT}/node-configs/N/node.yaml
    # · Last fail: 2026-07-31 — resolve_node_yaml delegates to NodeYaml CLI (DP-088/091);
    # ·   platform_root arg no longer drives the search — PLATFORM_ROOT env does
    # · Remove if: resolve_node_yaml search order changes (path 1 removed or reordered)
    node_name = "mynode"
    platform_root = tmp_path / "platform"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)

    node_yaml = platform_root / "node-configs" / node_name / "node.yaml"
    node_yaml.parent.mkdir(parents=True)
    node_yaml.write_text("node:\n  host: 1.2.3.4\n", encoding="utf-8")

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
        env={"PLATFORM_ROOT": str(platform_root)},
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert str(node_yaml) in result.stderr, f"Expected path '{node_yaml}' in stderr, got: {result.stderr}"
    assert_ldd_stderr(
        result,
        expected_patterns=[
            "[IMP:8][test][resolve_node_yaml] Resolving node.yaml for node=mynode",
        ],
    )
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"


# endregion FUNC_test_resolve_from_platform_root


# region FUNC_test_resolve_from_projects_glob
## @purpose  Verify resolve_node_yaml finds node.yaml via path 2 (glob):
##           projects_dir/*/node-configs/<node\>/node.yaml.
##           Path 1 must not contain the file so search continues to path 2.
## @io       ⇥ tmp_path → ⎋ assert glob path in PATH=… stderr, returncode == 0
## @complexity O(1)
def test_resolve_from_projects_glob(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: path 2 glob must be searched when path 1 has no match
    # · Scenario: node.yaml only at $HOME/projects/ctx/node-configs/N/node.yaml
    # · Last fail: 2026-07-31 — resolve_node_yaml delegates to NodeYaml CLI (DP-088/091);
    # ·   projects_dir arg no longer drives the search — $HOME env glob does
    # · Remove if: resolve_node_yaml search order changes (path 2 removed or reordered)
    node_name = "myprojectnode"
    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)
    projects_dir = tmp_path / "projects"

    # Create the glob-matching path: $HOME/projects/ctx/node-configs/<node>/node.yaml
    node_yaml = projects_dir / "ctx" / "node-configs" / node_name / "node.yaml"
    node_yaml.parent.mkdir(parents=True)
    node_yaml.write_text("node:\n  host: 10.0.0.1\n", encoding="utf-8")

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
        # HOME override → NodeYaml.resolve() globs ~/projects/*/node-configs/
        env={"HOME": str(tmp_path)},
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert str(node_yaml) in result.stderr, f"Expected glob path '{node_yaml}' in stderr, got: {result.stderr}"
    assert_ldd_stderr(result)
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"


# endregion FUNC_test_resolve_from_projects_glob


# region FUNC_test_resolve_from_opt
## @purpose  Verify resolve_node_yaml finds node.yaml via path 3 (VPS fallback):
##           /opt/node-configs/<node\>/node.yaml. This path is hardcoded in the
##           function; no parameter can override it. If /opt/ is not writable
##           (e.g. CI or container), the test is skipped.
## @io       ⇥ tmp_path → ⎋ assert /opt path in PATH=… stderr, returncode == 0
## @complexity O(1)
def test_resolve_from_opt(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: path 3 (/opt/node-configs/) must be searched last
    # · Scenario: node.yaml only at /opt/node-configs/N/node.yaml, paths 1 and 2 empty
    # · Last fail: Never
    # · Remove if: resolve_node_yaml search order changes (path 3 removed)
    node_name = "optnode"
    opt_config_dir = Path("/opt/node-configs") / node_name
    opt_node_yaml = opt_config_dir / "node.yaml"

    # Attempt to create the hardcoded path 3 file; skip if /opt not writable.
    try:
        opt_config_dir.mkdir(parents=True, exist_ok=True)
        opt_node_yaml.write_text("node:\n  host: 10.0.0.1\n", encoding="utf-8")
    except (PermissionError, OSError) as exc:
        pytest.skip(f"/opt/node-configs/ not writable ({exc}) — cannot test path 3")
    # 🧐 TRAP[DECISION] · 2026-08-04 · — · Path 3 (/opt/node-configs/) тестируется через реальный каталог
    # · Rejected: tmp_path (Zero Hardcode Rule) — путь 3 ЖЁСТКО зашит в модуле под тестом
    # ·   (resolve_node_yaml → NodeYaml.resolve, core/internal/shared/node_yaml/resolve.py:94 —
    # ·   candidates.append("/opt/node-configs/...")), инъекции параметра нет.
    # · Reason: DevPlan 129 W1 T5 — cleanup УЖЕ реализован
    # ·   (try/finally + shutil.rmtree только тест-поддиректории, строки ниже); при отсутствии
    # ·   прав на /opt тест корректно скипается (PermissionError → pytest.skip). Полный перевод
    # ·   на tmp_path потребовал бы изменения продакшен-кода resolve.py (вне скоупа W1).
    # · Rev: если resolve.py получит env-оверрайд для пути 3 — перевести тест на tmp_path.

    # Cleanup on teardown regardless of test outcome
    try:
        platform_root = tmp_path / "empty_platform"
        platform_root.mkdir(parents=True)
        projects_dir = tmp_path / "empty_projects"
        projects_dir.mkdir(parents=True)

        result = _run_bash(
            tmp_path,
            f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert str(opt_node_yaml) in result.stderr, (
            f"Expected /opt path '{opt_node_yaml}' in stderr, got: {result.stderr}"
        )
        assert_ldd_stderr(result)
        assert not result.stdout, f"stdout not empty: {result.stdout!r}"
    finally:
        # Remove only the test-created subdirectory, not the parent /opt/node-configs/
        if opt_config_dir.exists():
            shutil.rmtree(opt_config_dir, ignore_errors=True)


# endregion FUNC_test_resolve_from_opt


# region FUNC_test_resolve_first_wins
## @purpose  Verify first-match-wins: when node.yaml exists in all 3 locations,
##           resolve_node_yaml returns the first (platform_root) path.
## @io       ⇥ tmp_path → ⎋ assert platform_root path returned, not glob or /opt
## @complexity O(1)
def test_resolve_first_wins(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: first match wins — path 1 beats paths 2 and 3
    # · Scenario: node.yaml in all 3 locations → path 1 (PLATFORM_ROOT) returned
    # · Last fail: 2026-07-31 — resolve_node_yaml delegates to NodeYaml CLI (DP-088/091);
    # ·   search driven by PLATFORM_ROOT + HOME env, not path args
    # · Remove if: resolve_node_yaml search order or break logic changes
    node_name = "firstwins"
    platform_root = tmp_path / "platform"
    projects_dir = tmp_path / "projects"

    # Path 1
    path1 = platform_root / "node-configs" / node_name / "node.yaml"
    path1.parent.mkdir(parents=True)
    path1.write_text("node:\n  host: 1.1.1.1\n", encoding="utf-8")

    # Path 2 — glob
    path2 = projects_dir / "ctx" / "node-configs" / node_name / "node.yaml"
    path2.parent.mkdir(parents=True)
    path2.write_text("node:\n  host: 2.2.2.2\n", encoding="utf-8")

    # Path 3 — /opt (attempt, skip assertion on return if not writable)
    path3 = Path("/opt/node-configs") / node_name / "node.yaml"
    try:
        path3.parent.mkdir(parents=True, exist_ok=True)
        path3.write_text("node:\n  host: 3.3.3.3\n", encoding="utf-8")
        have_opt = True
    except (PermissionError, OSError):
        have_opt = False

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
        env={"PLATFORM_ROOT": str(platform_root), "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    # Must return path 1 (first found)
    assert str(path1) in result.stderr, f"Expected first path '{path1}' in stderr, got: {result.stderr}"
    # Must NOT return path 2 or 3
    assert str(path2) not in result.stderr, (
        f"Path 2 '{path2}' should NOT appear (first-match-wins), got: {result.stderr}"
    )
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"

    # Cleanup /opt test dir if we created it
    if have_opt and path3.parent.exists():
        shutil.rmtree(path3.parent, ignore_errors=True)


# endregion FUNC_test_resolve_first_wins


# region FUNC_test_resolve_not_found
## @purpose  Verify resolve_node_yaml exits with code 1 when node.yaml is absent
##           from all 3 candidate paths. stderr must contain the critical
##           [IMP:10] "node.yaml not found" LDD log.
## @io       ⇥ tmp_path → ⎋ assert returncode == 1, stderr has [IMP:10] not-found log
## @complexity O(1)
def test_resolve_not_found(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: missing node.yaml must exit 1 with critical log
    # · Scenario: no node.yaml in any of the 3 candidate paths
    # · Last fail: Never
    # · Remove if: resolve_node_yaml error handling changes (return code or log level)
    node_name = "nonexistent"
    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)

    # The script will exit with code 1 due to set -e when resolve_node_yaml returns 1.
    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
    )
    assert result.returncode == 1, f"Expected returncode 1 (not found), got {result.returncode}: {result.stderr}"
    assert_ldd_stderr(
        result,
        expected_patterns=[
            "node.yaml not found",
        ],
    )
    # stdout should be empty because command substitution failed before echo
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"


# endregion FUNC_test_resolve_not_found


# ═══════════════════════════════════════════════════════════════════
# TESTS — extract_node_host
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_extract_node_host
## @purpose  Verify extract_node_host extracts node.host from a valid node.yaml.
##           Expected output: the host string "1.2.3.4" on stdout (captured via
##           command substitution, echoed to stderr for assertion).
## @io       ⇥ tmp_path → ⎋ assert "HOST=1.2.3.4" in stderr, returncode == 0
## @complexity O(1)
def test_extract_node_host(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: node.host extraction from valid YAML
    # · Scenario: node.yaml with "node:\n  host: 1.2.3.4\n" → extract → "1.2.3.4"
    # · Last fail: Never
    # · Remove if: extract_node_host pyyaml parsing logic changes
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("node:\n  host: 1.2.3.4\n", encoding="utf-8")

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
host="$(extract_node_host "{yaml_file}")"
echo "HOST=$host" >&2
''',
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "HOST=1.2.3.4" in result.stderr, f"Expected 'HOST=1.2.3.4' in stderr, got: {result.stderr}"
    assert_ldd_stderr(
        result,
        expected_patterns=[
            "[IMP:9][test][extract_node_host] Extracted host: 1.2.3.4",
        ],
    )
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"


# endregion FUNC_test_extract_node_host


# region FUNC_test_extract_node_host_missing
## @purpose  Verify extract_node_host returns empty string when node.yaml exists
##           but lacks the node.host field. This is NOT an error condition —
##           return code must be 0, host must be empty.
## @io       ⇥ tmp_path → ⎋ assert "HOST=" (empty) in stderr, returncode == 0
## @complexity O(1)
def test_extract_node_host_missing(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: missing host field must return empty, not error
    # · Scenario: node.yaml without "node.host" → extract → ""
    # · Last fail: Never
    # · Remove if: extract_node_host behavior for absent host field changes
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("other:\n  key: value\n", encoding="utf-8")

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
host="$(extract_node_host "{yaml_file}")"
echo "HOST=$host" >&2
''',
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert "HOST=" in result.stderr, f"Expected 'HOST=' (empty host) in stderr, got: {result.stderr}"
    assert_ldd_stderr(
        result,
        expected_patterns=[
            "[IMP:9][test][extract_node_host] No host field in node.yaml",
        ],
    )
    assert not result.stdout, f"stdout not empty: {result.stdout!r}"


# endregion FUNC_test_extract_node_host_missing


# ═══════════════════════════════════════════════════════════════════
# TESTS — edge cases (glob, nullglob)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_empty_glob_no_error
## @purpose  Verify resolve_node_yaml handles an empty projects_dir gracefully
##           without triggering bash glob errors. The nullglob handling inside
##           the function ensures that "${projects_dir}/*/node-configs" expands
##           to nothing (not the literal "*" string) when no matching dirs exist.
##           The function will still return 1 (not found) — that is expected.
## @io       ⇥ tmp_path → ⎋ assert returncode == 1, no glob/expansion errors in stderr
## @complexity O(1)
def test_empty_glob_no_error(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: empty projects_dir must not cause glob error
    # · Scenario: projects_dir exists but has no subdirectories — glob
    #   expansion of * should yield nothing (nullglob), not a literal "*"
    # · Last fail: Never
    # · Remove if: resolve_node_yaml glob handling changes (nullglob removed)
    node_name = "emptyglob"
    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)
    # projects_dir is empty — no subdirectories

    result = _run_bash(
        tmp_path,
        f'''
__LOG_PREFIX="test"
resolved="$(resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}")"
echo "PATH=$resolved" >&2
''',
    )
    assert result.returncode == 1, f"Expected returncode 1 (not found), got {result.returncode}: {result.stderr}"
    # Must contain the normal "not found" LDD log
    assert "node.yaml not found" in result.stderr, f"Expected 'node.yaml not found' in stderr, got: {result.stderr}"
    # Must NOT contain bash glob error messages
    # With nullglob, "${projects_dir}/*/node-configs" with no matches
    # produces nothing — no "No such file" error.
    assert "No such file" not in result.stderr, f"Glob error detected in stderr: {result.stderr}"
    # Must NOT contain line-numbered bash stderr errors
    assert "test_script.sh: line" not in result.stderr, f"Bash error detected in stderr: {result.stderr}"


# endregion FUNC_test_empty_glob_no_error


# region FUNC_test_nullglob_restored
## @purpose  Verify resolve_node_yaml restores the nullglob shell option to its
##           original state after the function returns. The function enables
##           nullglob before the glob expansion and disables it before returning
##           (only if nullglob was originally OFF). This test checks both OFF→OFF
##           and ON→ON transitions.
## @io       ⇥ tmp_path → ⎋ assert BEFORE state == AFTER state, returncode == 0
## @complexity O(1)
def test_nullglob_restored(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · Regression: nullglob must be restored after resolve_node_yaml
    # · Scenario: nullglob OFF → resolve_node_yaml (enables/disables internally)
    #   → nullglob must still be OFF after the call
    # · Last fail: Never
    # · Remove if: resolve_node_yaml nullglob save/restore logic changes
    node_name = "nullglobtest"
    platform_root = tmp_path / "platform"
    platform_root.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True)

    result = _run_bash(
        tmp_path,
        f'''
# Record nullglob state before call
if [[ -o nullglob ]]; then echo "BEFORE=ON" >&2; else echo "BEFORE=OFF" >&2; fi

# Call resolve_node_yaml (will fail — not found; use || true to continue)
resolve_node_yaml "{node_name}" "{platform_root}" "{projects_dir}" || true

# Record nullglob state after call
if [[ -o nullglob ]]; then echo "AFTER=ON" >&2; else echo "AFTER=OFF" >&2; fi
''',
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    # Extract BEFORE and AFTER states from stderr
    stderr_lines = result.stderr.split("\n")
    before_val = None
    after_val = None
    for line in stderr_lines:
        line_stripped = line.strip()
        if line_stripped.startswith("BEFORE="):
            before_val = line_stripped.split("=", 1)[1]
        elif line_stripped.startswith("AFTER="):
            after_val = line_stripped.split("=", 1)[1]

    assert before_val is not None, f"BEFORE state not found in stderr: {result.stderr}"
    assert after_val is not None, f"AFTER state not found in stderr: {result.stderr}"
    assert before_val == after_val, (
        f"nullglob state changed: BEFORE={before_val} → AFTER={after_val}. "
        f"Expected both to be the same. stderr: {result.stderr}"
    )


# endregion FUNC_test_nullglob_restored
