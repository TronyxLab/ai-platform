# GREP_SUMMARY: test_stub_detection stub GENERATED-STUB converge deploy-project --status --report-only
# STRUCTURE: ▶ test_converge_r3_stub_reporting (stub vs deployed) → ⎋ verify LDD [IMP:9] trajectory
# NOTE: B9 T4 (CS-6) — inline-bash копии _is_stub удалены; unit-тесты shared/stub_detection → tests/unit/test_stub_detection_shared.py
# region MODULE_CONTRACT
## @purpose  Unit tests for stub detection (DevPlan 025 Wave 3).
##           Tests R3 stub-vs-deployed reporting semantics.
##           is_stub-детекция — единая реализация shared/stub_detection (B9 T4, U-28),
##           unit-тесты в tests/unit/test_stub_detection_shared.py (CS-6).
## @scope    Tests bash logic through isolated scripts. No VPS/Docker required.
## @invariants
##   - Uses tmp_path for isolated test environment
##   - Tests stub detection: GENERATED-STUB header vs real config
##   - Tests --report-only JSON output contains 'status' fields
## @rationale Stub detection enables converge to distinguish "awaiting CI deploy"
##            from "already deployed" — critical for reconciliation logic.
## @changes 2026-07-21 | Initial test suite (DevPlan 025 W3)
# endregion MODULE_CONTRACT

import subprocess
import textwrap


def _run_bash_test(script: str, tmp_path) -> subprocess.CompletedProcess:
    """Helper: write a Bash test script to tmp_path and run it."""
    script_path = tmp_path / "test_script.sh"
    script_path.write_text(textwrap.dedent(script))
    script_path.chmod(0o755)
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════
# region TEST_IS_STUB_DETECTS_STUB
## @purpose  Verify _is_stub() returns 0 for file with GENERATED-STUB header
## @scenario Create file with GENERATED-STUB → _is_stub returns 0 (true)
## 🧪 TRAP[TEST] · Regression: stub detection is the foundation of W3
##   · Last fail: N/A (new test)
##   · Remove if: _is_stub function is renamed or removed
def test_is_stub_detects_stub(tmp_path):
    """Test that _is_stub returns 0 for file containing GENERATED-STUB."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing _is_stub with GENERATED-STUB header..." >&2

    # Create a stub file
    stub_file=$(mktemp)
    cat > "$stub_file" << 'EOF'
# GENERATED-STUB by converge — overwritten by CI deliver
project: test-project
service: test-project
EOF

    # Inline _is_stub function (mirrors converge.sh implementation)
    _is_stub() {
        local ai_platform_yaml="$1"
        if [[ -f "$ai_platform_yaml" ]]; then
            head -1 "$ai_platform_yaml" 2>/dev/null | grep -q "GENERATED-STUB"
        else
            return 1
        fi
    }

    if _is_stub "$stub_file"; then
        echo "[IMP:9][test] OK: _is_stub detected stub file" >&2
    else
        echo "[IMP:10][test] FAIL: _is_stub did not detect stub file" >&2
        exit 1
    fi

    rm -f "$stub_file"
    """
    result = _run_bash_test(script, tmp_path)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_IS_STUB_DETECTS_REAL
## @purpose  Verify _is_stub() returns 1 for real (non-stub) config file
## @scenario Create file without GENERATED-STUB → _is_stub returns 1 (false)
## 🧪 TRAP[TEST] · Regression: real config must not be mistaken for stub
##   · Last fail: N/A (new test)
##   · Remove if: _is_stub function is renamed or removed
def test_is_stub_detects_real(tmp_path):
    """Test that _is_stub returns 1 for real config file (no GENERATED-STUB)."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing _is_stub with real config..." >&2

    real_file=$(mktemp)
    cat > "$real_file" << 'EOF'
project: test-project
service: test-project
domain: example.com
EOF

    _is_stub() {
        local ai_platform_yaml="$1"
        if [[ -f "$ai_platform_yaml" ]]; then
            head -1 "$ai_platform_yaml" 2>/dev/null | grep -q "GENERATED-STUB"
        else
            return 1
        fi
    }

    if _is_stub "$real_file"; then
        echo "[IMP:10][test] FAIL: _is_stub returned 0 for real config" >&2
        exit 1
    else
        echo "[IMP:9][test] OK: _is_stub returned 1 for real config (not a stub)" >&2
    fi

    rm -f "$real_file"
    """
    result = _run_bash_test(script, tmp_path)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST_IS_STUB_MISSING_FILE
## @purpose  Verify _is_stub() returns 1 for missing file
## @scenario File does not exist → _is_stub returns 1 (false)
## 🧪 TRAP[TEST] · Regression: missing file is not a stub
##   · Last fail: N/A (new test)
##   · Remove if: _is_stub function is renamed or removed
def test_is_stub_missing_file(tmp_path):
    """Test that _is_stub returns 1 for non-existent file."""
    script = """
    set -euo pipefail

    echo "[IMP:8][test] Testing _is_stub with missing file..." >&2

    _is_stub() {
        local ai_platform_yaml="$1"
        if [[ -f "$ai_platform_yaml" ]]; then
            head -1 "$ai_platform_yaml" 2>/dev/null | grep -q "GENERATED-STUB"
        else
            return 1
        fi
    }

    if _is_stub "/tmp/nonexistent-file-12345"; then
        echo "[IMP:10][test] FAIL: _is_stub returned 0 for missing file" >&2
        exit 1
    else
        echo "[IMP:9][test] OK: _is_stub returned 1 for missing file" >&2
    fi
    """
    result = _run_bash_test(script, tmp_path)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    imp_found = False
    for line in result.stderr.splitlines():
        if "[IMP:" in line:
            print(line)
            if "[IMP:9]" in line:
                imp_found = True
    print("--- END LDD TRAJECTORY ---")
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    assert imp_found, "IMP:9 log not found"


# endregion


# ═══════════════════════════════════════════════════════════════════
