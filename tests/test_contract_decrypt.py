#!/usr/bin/env python3
# GREP_SUMMARY: contract-test decrypt-secrets sops age bash-syntax subprocess real-script
# STRUCTURE: ▶ platform_root → ∋ SCRIPT_PATH → ◇ os.path.isfile? → ◇ bash -n (syntax) → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for core/internal/secrets/decrypt-secrets.sh. Verify the real bash
##           script exists and is syntactically valid via bash -n. These are CONTRACT tests
##           (NOT Simulators) — they call the real bash via subprocess.
## @scope    Tests operate on the real decrypt-secrets.sh file in the project tree.
##           No mocking, no simulation. No SOPS/age installation required.
## @invariants
##   - Script exists at core/internal/secrets/decrypt-secrets.sh relative to platform root
##   - Script is a regular file
##   - bash -n returns 0 for valid syntax
##   - Syntax regression would block all secret decryption during bootstrap
## @rationale  decrypt-secrets.sh is called during bootstrap step to decrypt SOPS-encrypted
##             secrets. A syntax error here blocks bootstrap entirely. The contract test
##             catches syntax regressions before they reach CI.
## @changes — CREATED: 2026-07-09 | TASK-4A: contract tests for deploy scripts
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
DECRYPT_SCRIPT_REL: str = os.path.join("core", "internal", "secrets", "decrypt-secrets.sh")
SCRIPT_PATH: str = os.path.join(PLATFORM_ROOT, DECRYPT_SCRIPT_REL)


# ── Test: File exists ──────────────────────────────────────────────────────


# region FUNC_test_decrypt_script_exists
@pytest.mark.contract
## @purpose  Verify the decrypt-secrets.sh script file exists on disk.
## @io       — (uses SCRIPT_PATH global) → ⎋ None (asserts)
## @complexity  O(1)
## @invariants
##   - SCRIPT_PATH must be a regular file (not directory)
##   - Failure means the script was moved or deleted

def test_decrypt_script_exists() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ os.path.isfile? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_decrypt_script_exists] Checking script: %s", SCRIPT_PATH)

    assert os.path.isfile(SCRIPT_PATH), f"[IMP:9][test_decrypt_script_exists] FAIL: script not found at {SCRIPT_PATH}"
    logger.info("[IMP:8][test_decrypt_script_exists] File exists: %s", SCRIPT_PATH)

    logger.info("[IMP:9][test_decrypt_script_exists] PASS: %s exists", SCRIPT_PATH)


# endregion FUNC_test_decrypt_script_exists


# ── Test: bash -n syntax check ─────────────────────────────────────────────


# region FUNC_test_decrypt_script_syntax
@pytest.mark.contract
## @purpose  Verify decrypt-secrets.sh has valid bash syntax via `bash -n`.
##           A syntax error in this script blocks secret decryption during bootstrap.
## @io       — (calls bash -n via subprocess) → ⎋ None (asserts returncode == 0)
## @complexity  O(1)
## @invariants
##   - bash -n reads and parses the script WITHOUT executing it
##   - returncode == 0 means syntactically valid bash
##   - Any syntax error produces stderr output and exit code > 0
## @rationale  SOPS/age decryption is a critical bootstrap step. Syntax regression
##             would block all deployments that require secrets (credentials, tokens).
##             This is a CONTRACT test: it calls the real bash binary, not a simulation.

def test_decrypt_script_syntax() -> None:
    """
    # ▶ SCRIPT_PATH → ⚡ subprocess.run(["bash", "-n", SCRIPT_PATH]) → ◇ returncode == 0? → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_decrypt_script_syntax] Running bash -n on: %s", SCRIPT_PATH)

    result: subprocess.CompletedProcess = subprocess.run(
        ["bash", "-n", SCRIPT_PATH],
        capture_output=True,
        text=True,
    )

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_decrypt_script_syntax] bash -n exit code: {result.returncode}")
    if result.stderr:
        for line in result.stderr.splitlines():
            print(f"[IMP:7][bash-n/stderr] {line}")
    print("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, (
        f"[IMP:9][test_decrypt_script_syntax] FAIL: bash syntax error in {SCRIPT_PATH}\nstderr: {result.stderr}"
    )
    logger.info("[IMP:9][test_decrypt_script_syntax] PASS: %s is syntactically valid", SCRIPT_PATH)


# endregion FUNC_test_decrypt_script_syntax


# ── Test: Single cleanup_all function and single trap in main() ─────────────


# region FUNC_test_decrypt_trap_cleanup_all
@pytest.mark.contract
## @purpose  Verify decrypt-secrets.sh has a single `cleanup_all()` function and
##           a single `trap cleanup_all` in main(). This prevents trap overwrite
##           (C2 fix) where the second trap was overwriting the first, causing
##           temp key leaks on certain error paths.
## @rationale C2: trap overwrite → temp age key left on disk.
def test_decrypt_trap_cleanup_all() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep for `cleanup_all` function + single trap line
    #   → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_decrypt_trap_cleanup_all] Checking cleanup_all() and single trap in: %s", SCRIPT_PATH)

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # Check cleanup_all function exists
    has_cleanup_func = "cleanup_all()" in content

    # Count trap lines in main() — should be exactly 1
    # We want trap cleanup_all EXIT INT TERM — not the original dual-trap pattern
    trap_lines = [line for line in content.splitlines() if "trap" in line and "cleanup_all" in line]

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_decrypt_trap_cleanup_all] has_cleanup_func={has_cleanup_func}, trap_count={len(trap_lines)}")
    if has_cleanup_func and len(trap_lines) == 1:
        print(
            f"[IMP:9][test_decrypt_trap_cleanup_all] PASS: cleanup_all() exists, single trap: {trap_lines[0].strip()}"
        )
    elif has_cleanup_func:
        print(
            f"[IMP:9][test_decrypt_trap_cleanup_all] FAIL: {len(trap_lines)} trap lines referencing cleanup_all (expected 1)"
        )
    else:
        print("[IMP:9][test_decrypt_trap_cleanup_all] FAIL: cleanup_all() function not found")
    print("--- END LDD TRAJECTORY ---")

    assert has_cleanup_func, "[IMP:9][test_decrypt_trap_cleanup_all] FAIL: cleanup_all() function not found"
    assert len(trap_lines) == 1, (
        f"[IMP:9][test_decrypt_trap_cleanup_all] FAIL: expected 1 trap cleanup_all, found {len(trap_lines)}"
    )
    logger.info("[IMP:9][test_decrypt_trap_cleanup_all] PASS: single cleanup_all trap in main()")


# endregion FUNC_test_decrypt_trap_cleanup_all


# ── Test: Trap includes wipe_temp_key ───────────────────────────────────────


# region FUNC_test_decrypt_trap_includes_wipe
@pytest.mark.contract
## @purpose  Verify the trap in main() references wipe_temp_key, ensuring the
##           temp age key is always wiped on exit (success, error, or signal).
## @rationale C2: trap must guarantee key wipe on any exit path.
def test_decrypt_trap_includes_wipe() -> None:
    """
    # ▶ SCRIPT_PATH → ◇ grep for cleanup_all body containing wipe_temp_key
    #   → ⎋ pass | fail
    """
    logger.info("[IMP:7][test_decrypt_trap_includes_wipe] Checking cleanup_all calls wipe_temp_key in: %s", SCRIPT_PATH)

    with open(SCRIPT_PATH) as f:
        content = f.read()

    # Extract cleanup_all function body
    lines = content.splitlines()
    in_cleanup = False
    cleanup_body = []
    for line in lines:
        if "cleanup_all()" in line:
            in_cleanup = True
            continue
        if in_cleanup:
            # Stop at next function or region end
            if line.strip().startswith("main()") or line.strip().startswith("#"):
                break
            cleanup_body.append(line)

    cleanup_text = "\n".join(cleanup_body)
    has_wipe = "wipe_temp_key" in cleanup_text

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    if has_wipe:
        print("[IMP:9][test_decrypt_trap_includes_wipe] PASS: cleanup_all includes wipe_temp_key")
    else:
        print("[IMP:9][test_decrypt_trap_includes_wipe] FAIL: cleanup_all does not include wipe_temp_key")
    print("--- END LDD TRAJECTORY ---")

    assert has_wipe, "[IMP:9][test_decrypt_trap_includes_wipe] FAIL: cleanup_all does not appear to call wipe_temp_key"
    logger.info("[IMP:9][test_decrypt_trap_includes_wipe] PASS: cleanup_all includes wipe_temp_key")


# endregion FUNC_test_decrypt_trap_includes_wipe


# ── Test: Script executable (exec bit) ──────────────────────────────────────


# region FUNC_test_decrypt_script_executable
@pytest.mark.contract
## @purpose  Verify decrypt-secrets.sh has the executable bit set in git index
##           (mode 100755). secrets.sh does `exec PATHS_INTERNAL_DIR/secrets/
##           decrypt-secrets.sh` (not `bash …`), so the exec bit is required for
##           `make secrets-unlock` to work. A fresh checkout or rsync without
##           exec bit = exit 126.
## @io       — checks FS os.access() + git ls-files -s mode prefix →
##           ⎋ None (asserts)
## @complexity  O(1)
## @invariants
##   - git-index mode MUST be 100755 (executable)
##   - FS exec bit SHOULD be set (secondary signal)
##   - Git check is PRIMARY: on some platforms FS bit may differ from git index
##   - A mode of 100644 means the file was committed without +x
## @rationale  secrets.sh line 15: `exec "…/decrypt-secrets.sh"` requires the
##             file to be executable. bash(1) exec fails with errno EACCES →
##             exit 126 if the target file lacks the exec bit. The bootstrap
##             path (node-lifecycle.sh:408 `bash …decrypt-secrets.sh`) is NOT
##             affected — it sources the file as a bash argument, not exec.
##             Only this contract test catches the regression.
## @usecases  T5.1: make secrets-unlock exit 126 after fresh checkout

def test_decrypt_script_executable() -> None:
    """
    # ▶ DECRYPT_SCRIPT_REL → ⚡ subprocess.run(["git", "ls-files", "-s"])
    #   → ◇ mode_prefix == "100755"? → ⎋ pass | fail
    #   → also ◇ os.access(X_OK)? → secondary check
    """
    logger.info("[IMP:7][test_decrypt_script_executable] Checking exec bit for: %s", SCRIPT_PATH)

    # PRIMARY: git-index mode check
    result: subprocess.CompletedProcess = subprocess.run(
        ["git", "ls-files", "-s", DECRYPT_SCRIPT_REL],
        capture_output=True,
        text=True,
        cwd=PLATFORM_ROOT,
    )
    git_mode_line = result.stdout.strip()
    # git ls-files -s output: "<mode> <hash> <stage>\t<path>"
    mode_prefix = git_mode_line.split()[0] if git_mode_line else "(empty)"
    is_100755 = mode_prefix == "100755"

    # SECONDARY: FS exec bit check
    fs_exec = os.access(SCRIPT_PATH, os.X_OK)

    # Print LDD trajectory
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print(f"[IMP:7][test_decrypt_script_executable] git mode: {mode_prefix}")
    print(f"[IMP:7][test_decrypt_script_executable] FS exec bit (os.X_OK): {fs_exec}")
    if is_100755:
        print("[IMP:9][test_decrypt_script_executable] PASS: git index mode is 100755")
    else:
        print(f"[IMP:9][test_decrypt_script_executable] FAIL: git index mode={mode_prefix}, expected 100755")
    if fs_exec:
        print("[IMP:9][test_decrypt_script_executable] PASS: FS exec bit is set")
    else:
        print("[IMP:9][test_decrypt_script_executable] FAIL: FS exec bit NOT set")
    print("--- END LDD TRAJECTORY ---")

    # Git check is PRIMARY — FS bit can differ on some checkout scenarios
    assert is_100755, (
        f"[IMP:9][test_decrypt_script_executable] FAIL: "
        f"git-index mode={mode_prefix}, expected 100755. "
        f"Run: chmod +x core/internal/secrets/decrypt-secrets.sh && "
        f"git add core/internal/secrets/decrypt-secrets.sh"
    )
    logger.info("[IMP:9][test_decrypt_script_executable] PASS: git index mode = 100755")

    # FS check is secondary — log it but don't block on it alone
    if not fs_exec:
        logger.warning(
            "[IMP:8][test_decrypt_script_executable] FS exec bit NOT set (may differ from git index on this platform)"
        )


# endregion FUNC_test_decrypt_script_executable
