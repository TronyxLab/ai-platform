# GREP_SUMMARY: python-deps, pip3, apt, requirements, content-hash, idempotent
# STRUCTURE: ▶ ensure_python_deps → _check_content_hash → _install_pip3 → _install_requirements → ⎋ CLI

# region MODULE_CONTRACT
## @purpose  Idempotent install of pip3 + platform Python dependencies on VPS.
##           Port of node-lifecycle.sh:_ensure_python_deps() (lines 117-169).
## @scope    VPS bootstrap — python3-pip, python3-venv, requirements.txt, content-hash guard.
## @invariants
##   - Fail-soft: returns False on failure, never raises
##   - Content-hash guard: skips pip install if requirements unchanged
##   - PEP 668 workaround: --break-system-packages on Ubuntu Noble
##   - typing_extensions conflict: --ignore-installed first
##   - All subprocess calls with capture_output=True, text=True, timeout=120
## @rationale Shell→Python migration (Strangler-Fig). Keeps idempotent contract:
##            second call is no-op if requirements.txt unchanged.
## @changes
##   2026-07-25  Initial port from node-lifecycle.sh:_ensure_python_deps()
# endregion MODULE_CONTRACT

import hashlib
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

HASH_DIR = "/var/lib/platform/.bootstrap"
HASH_FILE = os.path.join(HASH_DIR, "python-deps.hash")


# region FUNC__load_saved_hash
## @purpose  Read previously saved content hash from disk
## @io       path → str | None
## @complexity O(1)
def _load_saved_hash() -> str | None:
    # endregion FUNC__load_saved_hash
    try:
        with open(HASH_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("[IMP:7][_load_saved_hash] Cannot read hash file %s", HASH_FILE)
        return None


# region FUNC__compute_content_hash
## @purpose  Compute sha256 hex digest of a file
## @io       path → str | None (None if file missing/unreadable)
## @complexity O(n) in file size, O(1) in logic
def _compute_content_hash(req_path: str) -> str | None:
    # endregion FUNC__compute_content_hash
    try:
        h = hashlib.sha256()
        with open(req_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("[IMP:7][_compute_content_hash] Cannot read %s", req_path)
        return None


# region FUNC__save_content_hash
## @purpose  Persist sha256 hex digest to HASH_FILE
## @io       hash_str → bool
## @complexity O(1)
def _save_content_hash(hash_str: str) -> bool:
    # endregion FUNC__save_content_hash
    try:
        os.makedirs(HASH_DIR, exist_ok=True)
        with open(HASH_FILE, "w") as f:
            f.write(hash_str + "\n")
        return True
    except OSError:
        logger.warning("[IMP:7][_save_content_hash] Cannot write hash to %s", HASH_FILE)
        return False


# region FUNC__check_content_hash
## @purpose  Compare current requirements.txt hash against saved hash
## @io       req_path → bool (True=matches, skip install)
## @complexity O(n) file read + hexdigest
def _check_content_hash(req_path: str) -> bool:
    # endregion FUNC__check_content_hash
    saved = _load_saved_hash()
    if saved is None:
        logger.info("[IMP:9][_check_content_hash] No saved hash — install required")
        return False

    current = _compute_content_hash(req_path)
    if current is None:
        logger.info("[IMP:9][_check_content_hash] Cannot compute hash — install required")
        return False

    if saved == current:
        logger.info("[IMP:9][_check_content_hash] Hash match — skipping pip install")
        return True

    logger.info("[IMP:9][_check_content_hash] Hash mismatch — install required")
    return False


# region FUNC__run
## @purpose  Run a subprocess with uniform error handling
## @io       cmd list → bool (True=exit 0)
## @complexity O(1) — wraps subprocess.run with timeout=120
def _run(cmd: list[str], label: str = "") -> bool:
    # endregion FUNC__run
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[:500]
            logger.warning(
                "[IMP:7][_run] %s failed (rc=%d): %s",
                label or cmd[0],
                result.returncode,
                stderr,
            )
            return False
        return True
    except FileNotFoundError:
        logger.warning("[IMP:7][_run] %s — command not found", label or cmd[0])
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][_run] %s — timed out after 120s", label or cmd[0])
        return False
    except OSError as exc:
        logger.warning("[IMP:7][_run] %s — OS error: %s", label or cmd[0], exc)
        return False


# region FUNC__install_pip3
## @purpose  Install python3-pip and python3-venv via apt-get if pip3 missing
## @io       → bool
## @complexity O(1) — 2 subprocess calls max
def _install_pip3() -> bool:
    # endregion FUNC__install_pip3
    # Quick check — if pip3 is already available, skip apt-get.
    check = subprocess.run(
        ["which", "pip3"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode == 0:
        logger.info("[IMP:9][_install_pip3] pip3 already installed")
        return True

    logger.info("[IMP:9][_install_pip3] pip3 not found — installing python3-pip + python3-venv")

    if not _run(
        ["apt-get", "update", "-qq"],
        label="apt-get update",
    ):
        return False

    return _run(
        ["apt-get", "install", "-y", "-qq", "python3-pip", "python3-venv"],
        label="apt-get install python3-pip python3-venv",
    )


# region FUNC__install_requirements
## @purpose  Install typing_extensions (--ignore-installed) then -r requirements.txt
## @io       core_dir → bool
## @complexity O(n) — 2 pip subprocess calls
def _install_requirements(core_dir: str) -> bool:
    # endregion FUNC__install_requirements
    req_path = os.path.join(core_dir, "requirements.txt")
    if not os.path.isfile(req_path):
        logger.warning("[IMP:7][_install_requirements] No requirements.txt at %s", req_path)
        return False

    # Step 1: typing_extensions with --ignore-installed (Debian conflict workaround)
    logger.info("[IMP:9][_install_requirements] Installing typing_extensions (--ignore-installed)")
    pip_typing = [
        "pip3",
        "install",
        "typing_extensions",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_typing, label="pip3 typing_extensions"):
        return False

    # Step 2: full requirements.txt
    logger.info("[IMP:9][_install_requirements] Installing -r requirements.txt")
    pip_reqs = [
        "pip3",
        "install",
        "-r",
        req_path,
        "--break-system-packages",
    ]
    return _run(pip_reqs, label="pip3 -r requirements.txt")


# region FUNC_ensure_python_deps
## @purpose  Idempotent install of pip3 + platform Python dependencies on VPS.
##           Port of node-lifecycle.sh:_ensure_python_deps().
## @io       core_dir → bool
## @complexity O(1) hash check + O(n) pip install on mismatch
def ensure_python_deps(core_dir: str) -> bool:
    """
    Idempotent Python dependency installer.

    Parameters
    ----------
    core_dir : str
        Path to the platform core directory containing requirements.txt.

    Returns
    -------
    bool
        True if all deps are satisfied (installed or already present).
    """
    # endregion FUNC_ensure_python_deps

    req_path = os.path.join(core_dir, "requirements.txt")
    logger.info("[IMP:9][ensure_python_deps] Start — core_dir=%s", core_dir)

    # ── Content-hash guard ──────────────────────────────────────────────
    if _check_content_hash(req_path):
        logger.info("[IMP:9][ensure_python_deps] Content hash match — deps already up to date")
        return True

    # ── Install pip3 if missing ─────────────────────────────────────────
    if not _install_pip3():
        logger.warning("[IMP:7][ensure_python_deps] pip3 installation failed")
        return False

    # ── Install Python requirements ─────────────────────────────────────
    if not _install_requirements(core_dir):
        logger.warning("[IMP:7][ensure_python_deps] Requirements installation failed")
        return False

    # ── Persist content hash ────────────────────────────────────────────
    current_hash = _compute_content_hash(req_path)
    if current_hash is None:
        logger.warning("[IMP:7][ensure_python_deps] Cannot compute hash after install — skipping persist")
        return False

    if not _save_content_hash(current_hash):
        logger.warning("[IMP:7][ensure_python_deps] Failed to persist content hash")
        # Not fatal — deps are installed, hash is a cache optimization

    logger.info("[IMP:9][ensure_python_deps] Complete — Python dependencies installed")
    return True


# region FUNC_CLI
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Idempotent Python dependency installer for platform VPS")
    parser.add_argument("action", choices=["ensure"])
    parser.add_argument("--core-dir", required=True)
    args = parser.parse_args()

    success = ensure_python_deps(args.core_dir)
    sys.exit(0 if success else 1)
# endregion FUNC_CLI
