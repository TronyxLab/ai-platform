# GREP_SUMMARY: contract-test decrypt-secrets sops age python-syntax subprocess real-script
# STRUCTURE: ▶ platform_root → ∋ SCRIPT_PATH → ◇ os.path.isfile? → ◇ py_compile (syntax) → ◇ exec-bit → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Contract tests for core/internal/secrets/decrypt_secrets.py. Verify the real
##           Python script exists, is syntactically valid (py_compile), and has the exec bit
##           (platform-secrets.service ExecStart invokes it directly via shebang, DevPlan 173 W1.3).
##           These are CONTRACT tests (NOT Simulators) — they check the real file.
## @scope    Tests operate on the real decrypt_secrets.py file in the project tree.
##           No mocking, no simulation. No SOPS/age installation required.
## @invariants
##   - Script exists at core/internal/secrets/decrypt_secrets.py relative to platform root
##   - Script is a regular file
##   - py_compile returns 0 for valid syntax (Python-аналог bash -n)
##   - exec bit (100755) в git-index — ExecStart platform-secrets.service вызывает .py напрямую
## @rationale  decrypt_secrets.py вызывается при bootstrap (lib/secrets.sh step_10_decrypt_secrets,
##             platform-secrets.service ExecStart, entrypoint secrets.sh) для расшифровки SOPS/age
##             секретов. Ошибка синтаксиса блокирует bootstrap целиком. Контракт-тест ловит
##             регрессии до CI. Резолв SECRETS_FILE перенесён из удалённого decrypt-secrets.sh
##             (DevPlan 173 W1.3) — тест мигрирован с .sh на .py.
## @changes — CREATED: 2026-07-09 | TASK-4A: contract tests for deploy scripts
## @changes — 2026-08-16 | DevPlan 173 W1.3: decrypt-secrets.sh удалён → тест мигрирован на decrypt_secrets.py
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
DECRYPT_SCRIPT_REL: str = str(Path("core") / "internal" / "secrets" / "decrypt_secrets.py")
SCRIPT_PATH: str = str(Path(PLATFORM_ROOT) / DECRYPT_SCRIPT_REL)


# ── Test: File exists ──────────────────────────────────────────────────────


# region FUNC_test_decrypt_script_exists
@pytest.mark.contract
## @purpose  Verify the decrypt_secrets.py script file exists on disk.
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

    assert pathlib.Path(SCRIPT_PATH).is_file(), (
        f"[IMP:9][test_decrypt_script_exists] FAIL: script not found at {SCRIPT_PATH}"
    )
    logger.info("[IMP:8][test_decrypt_script_exists] File exists: %s", SCRIPT_PATH)

    logger.info("[IMP:9][test_decrypt_script_exists] PASS: %s exists", SCRIPT_PATH)


# endregion FUNC_test_decrypt_script_exists


# ── Test: Python syntax check (py_compile — аналог bash -n) ─────────────────


# region FUNC_test_decrypt_script_syntax
@pytest.mark.contract
## @purpose  Verify decrypt_secrets.py has valid Python syntax via py_compile.
##           A syntax error in this script blocks secret decryption during bootstrap.
## @io       — (calls py_compile via subprocess) → ⎋ None (asserts returncode == 0)
## @complexity  O(1)
## @invariants
##   - py_compile parses the script WITHOUT executing it
##   - returncode == 0 means syntactically valid Python
##   - Any syntax error produces stderr output and exit code > 0
## @rationale  SOPS/age decryption is a critical bootstrap step. Syntax regression
##             would block all deployments that require secrets (credentials, tokens).

def test_decrypt_script_syntax() -> None:
    """
    # ▶ SCRIPT_PATH → ⚡ subprocess.run([sys.executable, "-m", "py_compile", SCRIPT_PATH]) → ◇ rc == 0? → ⎋
    """
    logger.info("[IMP:7][test_decrypt_script_syntax] Running py_compile on: %s", SCRIPT_PATH)

    result: subprocess.CompletedProcess = subprocess.run(
        [sys.executable, "-m", "py_compile", SCRIPT_PATH], capture_output=True, text=True, check=False
    )

    # Print LDD trajectory
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    logger.info("%s", f"[IMP:7][test_decrypt_script_syntax] py_compile exit code: {result.returncode}")
    if result.stderr:
        for line in result.stderr.splitlines():
            logger.info("%s", f"[IMP:7][py_compile/stderr] {line}")
    logger.info("--- END LDD TRAJECTORY ---")

    assert result.returncode == 0, (
        f"[IMP:9][test_decrypt_script_syntax] FAIL: Python syntax error in {SCRIPT_PATH}\nstderr: {result.stderr}"
    )
    logger.info("[IMP:9][test_decrypt_script_syntax] PASS: %s is syntactically valid", SCRIPT_PATH)


# endregion FUNC_test_decrypt_script_syntax


# NOTE: test_decrypt_trap_cleanup_all and test_decrypt_trap_includes_wipe removed —
# decrypt-secrets.sh (shell facade) deleted in DevPlan 173 W1.3; cleanup/trap logic
# already lives in Python (decrypt_secrets.py, atexit+signal — DevPlan 086/136 W10).


# ── Test: Script executable (exec bit) ──────────────────────────────────────


# region FUNC_test_decrypt_script_executable
@pytest.mark.contract
## @purpose  Verify decrypt_secrets.py has the executable bit set in git index
##           (mode 100755). platform-secrets.service ExecStart invokes the .py
##           directly via shebang (`ExecStart=/opt/platform/core/internal/secrets/
##           decrypt_secrets.py`), so the exec bit is required (DevPlan 173 W1.3).
## @io       — checks FS os.access() + git ls-files -s mode prefix →
##           ⎋ None (asserts)
## @complexity  O(1)
## @invariants
##   - git-index mode MUST be 100755 (executable)
##   - FS exec bit SHOULD be set (secondary signal)
##   - Git check is PRIMARY: on some platforms FS bit may differ from git index
##   - A mode of 100644 means the file was committed without +x
## @rationale  systemd ExecStart с прямым вызовом скрипта требует exec-бит —
##             иначе systemd вернёт exit 203 (exec format error) и bootstrap заблокируется.
## @usecases  T5.1: platform-secrets.service exit 203 after fresh checkout

def test_decrypt_script_executable() -> None:
    """
    # ▶ DECRYPT_SCRIPT_REL → ⚡ subprocess.run(["git", "ls-files", "-s"])
    #   → ◇ mode_prefix == "100755"? → ⎋ pass | fail
    #   → also ◇ os.access(X_OK)? → secondary check
    """
    logger.info("[IMP:7][test_decrypt_script_executable] Checking exec bit for: %s", SCRIPT_PATH)

    # PRIMARY: git-index mode check
    result: subprocess.CompletedProcess = subprocess.run(
        ["git", "ls-files", "-s", DECRYPT_SCRIPT_REL], capture_output=True, text=True, cwd=PLATFORM_ROOT, check=False
    )
    git_mode_line = result.stdout.strip()
    # git ls-files -s output: "<mode> <hash> <stage>\t<path>"
    mode_prefix = git_mode_line.split()[0] if git_mode_line else "(empty)"
    is_100755 = mode_prefix == "100755"

    # SECONDARY: FS exec bit check
    fs_exec = os.access(SCRIPT_PATH, os.X_OK)

    # Print LDD trajectory
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    logger.info("%s", f"[IMP:7][test_decrypt_script_executable] git mode: {mode_prefix}")
    logger.info("%s", f"[IMP:7][test_decrypt_script_executable] FS exec bit (os.X_OK): {fs_exec}")
    if is_100755:
        logger.info("[IMP:9][test_decrypt_script_executable] PASS: git index mode is 100755")
    else:
        logger.info(
            "%s", f"[IMP:9][test_decrypt_script_executable] FAIL: git index mode={mode_prefix}, expected 100755"
        )
    if fs_exec:
        logger.info("[IMP:9][test_decrypt_script_executable] PASS: FS exec bit is set")
    else:
        logger.info("[IMP:9][test_decrypt_script_executable] FAIL: FS exec bit NOT set")
    logger.info("--- END LDD TRAJECTORY ---")

    # Git check is PRIMARY — FS bit can differ on some checkout scenarios
    assert is_100755, (
        f"[IMP:9][test_decrypt_script_executable] FAIL: "
        f"git-index mode={mode_prefix}, expected 100755. "
        f"Run: chmod +x core/internal/secrets/decrypt_secrets.py && "
        f"git add core/internal/secrets/decrypt_secrets.py"
    )
    logger.info("[IMP:9][test_decrypt_script_executable] PASS: git index mode = 100755")

    # FS check is secondary — log it but don't block on it alone
    if not fs_exec:
        logger.warning(
            "[IMP:8][test_decrypt_script_executable] FS exec bit NOT set (may differ from git index on this platform)"
        )


# endregion FUNC_test_decrypt_script_executable
