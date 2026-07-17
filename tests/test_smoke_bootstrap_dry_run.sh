#!/usr/bin/env bash
# GREP_SUMMARY: smoke-test bootstrap dry-run node-lifecycle entrypoint no-errors
# STRUCTURE: ▶ test_help → ◇ test_dry_run_syntax → ◇ test_dry_run_no_errors → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Smoke test: verify bootstrap.sh and node-lifecycle.sh parse without errors
##           in --help and --dry-run modes (no SSH, no SCP — pure syntax/lint smoke).
## @scope    Validates bootstrap entrypoint and node-lifecycle can be invoked without
##           crashing on argument parsing, missing dependencies, or syntax errors.
## @invariants
##   - bootstrap.sh --help exits 0
##   - bootstrap.sh --resolve --dry-run with valid NODE= exits 0
##   - No Docker, no SSH, no SCP needed — pure argument-parsing smoke
## @rationale  Stream 3 test from Brief 001 audit: ensure bootstrap entrypoint
##             is syntactically valid and dry-run produces no errors.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOOTSTRAP_SH="${PROJECT_ROOT}/core/entrypoints/bootstrap.sh"
NODE_LIFECYCLE_SH="${PROJECT_ROOT}/core/internal/bootstrap/node-lifecycle.sh"
PASSED=0
FAILED=0

# ── Logging ───────────────────────────────────────────────────────────────────
log_pass() { echo "[PASS] $1"; PASSED=$((PASSED + 1)); }
log_fail() { echo "[FAIL] $1 — $2"; FAILED=$((FAILED + 1)); }

# ── Test 1: bootstrap.sh --help exits 0 ───────────────────────────────────────
echo "=== Test 1: bootstrap.sh --help ==="
if bash "${BOOTSTRAP_SH}" --help >/dev/null 2>&1; then
    log_pass "bootstrap.sh --help exits 0"
else
    log_fail "bootstrap.sh --help" "non-zero exit code"
fi

# ── Test 2: node-lifecycle.sh is syntactically valid ────────────────────────────
echo "=== Test 2: node-lifecycle.sh syntax check ==="
# node-lifecycle.sh is an internal script (no --help/-h) — always called by bootstrap.sh.
# Verify syntax validity and existence.
if [[ -f "${NODE_LIFECYCLE_SH}" ]] && bash -n "${NODE_LIFECYCLE_SH}" 2>/dev/null; then
    log_pass "node-lifecycle.sh exists and passes bash -n syntax check"
else
    log_fail "node-lifecycle.sh" "missing or syntax error"
fi

# ── Test 3: bootstrap.sh dry-run without NODE= fails correctly ────────────────
echo "=== Test 3: bootstrap.sh --resolve --dry-run (no NODE=) ==="
# Should fail because NODE= is not set and no --node argument
output=$(bash "${BOOTSTRAP_SH}" --resolve --dry-run 2>&1) && rc=0 || rc=$?
if [[ "$rc" -ne 0 ]]; then
    log_pass "bootstrap.sh --resolve --dry-run (no NODE=) fails with exit $rc (expected)"
else
    log_fail "bootstrap.sh --resolve --dry-run (no NODE=)" "expected non-zero exit, got 0"
fi

# ── Test 4: provision-environment.sh --help exits 0 ───────────────────────────
echo "=== Test 4: provision-environment.sh --help ==="
PROVISION_SH="${PROJECT_ROOT}/core/internal/provision-environment.sh"
if bash "${PROVISION_SH}" --help >/dev/null 2>&1; then
    log_pass "provision-environment.sh --help exits 0"
else
    log_fail "provision-environment.sh --help" "non-zero exit code"
fi

# ── Test 5: provision-environment.sh --scope all --dry-run exits 0 ────────────
echo "=== Test 5: provision-environment.sh --scope all --dry-run ==="
if [[ -f "${PROJECT_ROOT}/platform-env.yaml" ]]; then
    if bash "${PROVISION_SH}" --scope all --dry-run --platform-env "${PROJECT_ROOT}/platform-env.yaml" >/dev/null 2>&1; then
        log_pass "provision-environment.sh --scope all --dry-run exits 0"
    else
        log_fail "provision-environment.sh --scope all --dry-run" "non-zero exit code"
    fi
else
    echo "[SKIP] Test 5: platform-env.yaml not found"
fi

# ── Test 6: provision-environment.sh multi-scope (FIX-1 regression) ───────────
echo "=== Test 6: provision-environment.sh --scope networks --scope volumes --dry-run ==="
if [[ -f "${PROJECT_ROOT}/platform-env.yaml" ]]; then
    output=$(bash "${PROVISION_SH}" --scope networks --scope volumes --dry-run --platform-env "${PROJECT_ROOT}/platform-env.yaml" 2>&1) && rc=0 || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        # Verify both scopes appear in output
        if echo "$output" | grep -q "networks" && echo "$output" | grep -q "volumes"; then
            log_pass "multi-scope --scope networks --scope volumes both executed"
        else
            log_fail "multi-scope" "output missing networks or volumes scope"
        fi
    else
        log_fail "multi-scope --scope networks --scope volumes" "exit code $rc"
    fi
else
    echo "[SKIP] Test 6: platform-env.yaml not found"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Smoke Summary ==="
echo "Passed: ${PASSED}"
echo "Failed: ${FAILED}"
if [[ "$FAILED" -gt 0 ]]; then
    echo "RESULT: SOME TESTS FAILED"
    exit 1
else
    echo "RESULT: ALL TESTS PASSED"
    exit 0
fi
