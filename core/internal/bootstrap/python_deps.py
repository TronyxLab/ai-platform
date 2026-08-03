# GREP_SUMMARY: python-deps, python3.14, deadsnakes, pip, apt, requirements, content-hash, idempotent, ensurepip, symlink
# STRUCTURE: ▶ ensure_python_deps → _check_content_hash(hash+pyver) → _install_python314(PPA) → _install_requirements(/usr/local/bin/python3 -m pip) → ⎋ CLI

# region MODULE_CONTRACT
## @purpose  Idempotent install of Python 3.14 (deadsnakes PPA) + platform Python
##           dependencies on VPS. Ensures bare `python3` resolves to 3.14 via the
##           /usr/local/bin/python3 → /usr/bin/python3.14 symlink (PATH order).
## @scope    VPS bootstrap — Python 3.14 via deadsnakes PPA (Ubuntu 24.04),
##           python3.14-venv, ensurepip, requirements.txt, content-hash guard (hash + pyver).
## @invariants
##   - Fail-soft: returns False on failure, never raises
##   - Stdlib-only imports (hashlib, logging, os, re, subprocess, sys) — this module
##     runs under the OLD system python3 (3.12) to install 3.14
##   - Content-hash guard keyed by (requirements.txt hash + python version); old-format
##     markers (hash only) are treated as mismatch → reinstall (correct for 3.12→3.14)
##   - PEP 668 workaround: --break-system-packages (deadsnakes python3.14 is externally-managed)
##   - typing_extensions conflict: --ignore-installed first
##   - /usr/bin/python3 (system 3.12) is NEVER touched — only /usr/local/bin/python3 symlink
##   - Ubuntu != 24.04 → WARN + fallback to apt python3-pip (system python 3.12)
##   - All subprocess calls with capture_output=True, text=True
## @rationale Shell→Python migration (Strangler-Fig). User decision 2026-08-01: deadsnakes
##            PPA for Python 3.14 on Ubuntu 24.04 (see TRAP[DECISION] below).
## @changes
##   2026-07-25  Initial port from node-lifecycle.sh:_ensure_python_deps()
##   2026-08-01  Python 3.14 via deadsnakes PPA; pip via /usr/local/bin/python3 -m pip;
##               hash marker now includes python version (old-format marker = mismatch)
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-08-01 · HI · Python 3.14 через deadsnakes PPA (Ubuntu 24.04)
# · Rejected: uv (новый инструмент + curl-скрипт с GitHub — внешняя поставка вне apt) /
# ·           source build (5-15 мин компиляции на голом сервере)
# · Reason: deadsnakes = официальный PPA-канал, apt-управляемый, ~30-60s установка;
# ·         ensurepip --upgrade гарантирует pip без отдельного pip-пакета
# · Rev: если 3.14 исчезнет из deadsnakes или появится в официальном universe →
# ·      перейти на стандартный apt-репозиторий Ubuntu, убрать PPA-ветку

import hashlib
import logging
import os
import re
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


# region FUNC__compute_python_version
## @purpose  Report the version of /usr/local/bin/python3 (the interpreter that will run
##           platform code after 3.14 install). 'unknown' if not yet installed.
## @io       → str like '3.14.5' or 'unknown'
## @complexity O(1) — single subprocess
def _compute_python_version() -> str:
    # endregion FUNC__compute_python_version
    try:
        result = subprocess.run(
            ["/usr/local/bin/python3", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return "unknown"
        version_str = (result.stdout or result.stderr).strip()
        match = re.match(r"Python (\d+\.\d+(?:\.\d+)?)", version_str)
        if match:
            return match.group(1)
        return "unknown"
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        logger.info("[IMP:7][_compute_python_version] /usr/local/bin/python3 not available yet")
        return "unknown"


# region FUNC__python314_installed
## @purpose  Idempotent quick check: is /usr/local/bin/python3 already Python 3.14.x?
## @io       → bool (True = 3.14 active, skip install)
## @complexity O(1) — delegates to _compute_python_version
def _python314_installed() -> bool:
    # endregion FUNC__python314_installed
    version = _compute_python_version()
    installed = version.startswith("3.14")
    if installed:
        logger.info("[IMP:9][_python314_installed] /usr/local/bin/python3 = Python %s — no-op", version)
    else:
        logger.info(
            "[IMP:8][_python314_installed] /usr/local/bin/python3 not 3.14 (got %r) — install required", version
        )
    return installed


# region FUNC__detect_ubuntu_version
## @purpose  Read VERSION_ID from /etc/os-release (e.g. '24.04'). None if unreadable/not Ubuntu.
## @io       → str | None
## @complexity O(n) — line scan of /etc/os-release
def _detect_ubuntu_version() -> str | None:
    # endregion FUNC__detect_ubuntu_version
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("VERSION_ID="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        logger.warning("[IMP:7][_detect_ubuntu_version] Cannot read /etc/os-release")
    return None


# region FUNC__check_content_hash
## @purpose  Compare (requirements.txt hash + python version) against saved marker.
##           Old-format markers (hash only, pre-3.14 era) are treated as mismatch →
##           forces reinstall, which is the correct transition for 3.12→3.14.
## @io       req_path → bool (True=matches, skip install)
## @complexity O(n) file read + hexdigest + version probe
def _check_content_hash(req_path: str) -> bool:
    # endregion FUNC__check_content_hash
    saved = _load_saved_hash()
    if saved is None:
        logger.info("[IMP:9][_check_content_hash] No saved marker — install required")
        return False

    # New marker format: "<sha256>\n<python_version>". Old format = hash only → mismatch.
    parts = saved.split("\n")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        logger.info(
            "[IMP:9][_check_content_hash] Old-format marker (no python version) — reinstall required (3.12→3.14)"
        )
        return False

    saved_hash, saved_pyver = parts[0], parts[1]

    current = _compute_content_hash(req_path)
    if current is None:
        logger.info("[IMP:9][_check_content_hash] Cannot compute hash — install required")
        return False

    current_pyver = _compute_python_version()
    if saved_hash == current and saved_pyver == current_pyver:
        logger.info("[IMP:9][_check_content_hash] Hash + python version match — skipping pip install")
        return True

    logger.info(
        "[IMP:9][_check_content_hash] Hash or python version mismatch (saved=%s, current=%s) — install required",
        saved_pyver,
        current_pyver,
    )
    return False


# region FUNC__run
## @purpose  Run a subprocess with uniform error handling
## @io       cmd list → bool (True=exit 0)
## @complexity O(1) — wraps subprocess.run
def _run(cmd: list[str], label: str = "", env: dict[str, str] | None = None, timeout: int = 120) -> bool:
    # endregion FUNC__run
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
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
        logger.warning("[IMP:7][_run] %s — timed out after %ds", label or cmd[0], timeout)
        return False
    except OSError as exc:
        logger.warning("[IMP:7][_run] %s — OS error: %s", label or cmd[0], exc)
        return False


# region FUNC__install_pip3
## @purpose  FALLBACK branch (Ubuntu != 24.04): install python3-pip and python3-venv
##           via apt-get for the SYSTEM python3.12. Not used on 24.04 — there the
##           canonical path is _install_python314() (deadsnakes PPA).
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


# region FUNC__resolve_python_bin
## @purpose  Resolve the python interpreter used for pip: prefer /usr/local/bin/python3
##           (the 3.14 symlink) when present, else bare system python3 (non-24.04 fallback).
## @io       → str (binary path or bare name)
## @complexity O(1)
def _resolve_python_bin() -> str:
    # endregion FUNC__resolve_python_bin
    if os.path.isfile("/usr/local/bin/python3"):
        return "/usr/local/bin/python3"
    logger.info("[IMP:8][_resolve_python_bin] /usr/local/bin/python3 absent — using system python3")
    return "python3"


# region FUNC__install_python314
## @purpose  Install Python 3.14 from deadsnakes PPA on Ubuntu 24.04. Idempotent:
##           fast no-op if /usr/local/bin/python3 already reports 3.14.x.
##           Non-24.04 → WARN + fallback to _install_pip3() (system python).
## @io       → bool
## @complexity O(1) probe + O(6) apt/ensurepip/symlink subprocess calls
## @invariants
##   - /usr/bin/python3 (system 3.12) is NEVER modified — only /usr/local/bin/python3 symlink
##   - DEBIAN_FRONTEND=noninteractive for all apt operations (bare-server bootstrap)
##   - ensurepip --upgrade guarantees pip for 3.14 (deadsnakes has no pip package)
def _install_python314() -> bool:
    # endregion FUNC__install_python314
    if _python314_installed():
        return True

    version_id = _detect_ubuntu_version()
    if version_id != "24.04":
        logger.warning(
            "[IMP:7][_install_python314] Ubuntu version %r != 24.04 — falling back to system python3-pip "
            "(Python 3.14 PPA install skipped)",
            version_id,
        )
        return _install_pip3()

    logger.info("[IMP:9][_install_python314] Ubuntu 24.04 — installing Python 3.14 from deadsnakes PPA")

    # DEBIAN_FRONTEND=noninteractive prevents interactive prompts on a bare server.
    env: dict[str, str] = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}

    # 1. add-apt-repository lives in software-properties-common
    if not _run(
        ["apt-get", "install", "-y", "-qq", "software-properties-common"],
        label="apt-get install software-properties-common",
        env=env,
        timeout=600,
    ):
        return False

    # 2. Add deadsnakes PPA
    if not _run(
        ["add-apt-repository", "-y", "ppa:deadsnakes/ppa"],
        label="add-apt-repository ppa:deadsnakes/ppa",
        env=env,
        timeout=600,
    ):
        return False

    # 3. Refresh package index with the new PPA
    if not _run(
        ["apt-get", "update", "-qq"],
        label="apt-get update",
        env=env,
        timeout=600,
    ):
        return False

    # 4. Install Python 3.14 interpreter + venv module
    if not _run(
        ["apt-get", "install", "-y", "-qq", "python3.14", "python3.14-venv"],
        label="apt-get install python3.14 python3.14-venv",
        env=env,
        timeout=600,
    ):
        return False

    # 5. ensurepip — deadsnakes does not guarantee a pip package; ensurepip is the safe path
    if not _run(
        ["/usr/bin/python3.14", "-m", "ensurepip", "--upgrade"],
        label="python3.14 -m ensurepip --upgrade",
        env=env,
        timeout=300,
    ):
        return False

    # 6. Symlink /usr/local/bin/python3 → /usr/bin/python3.14 (PATH: /usr/local/bin precedes /usr/bin)
    if not _run(
        ["ln", "-sfn", "/usr/bin/python3.14", "/usr/local/bin/python3"],
        label="ln -sfn /usr/bin/python3.14 /usr/local/bin/python3",
        env=env,
        timeout=60,
    ):
        return False

    if not _python314_installed():
        logger.warning("[IMP:7][_install_python314] Post-install verification failed — 3.14 not active")
        return False

    logger.info("[IMP:9][_install_python314] Python 3.14 installed and active at /usr/local/bin/python3")
    return True


# region FUNC__install_requirements
## @purpose  Install typing_extensions (--ignore-installed) then -r requirements.txt
##           into the ACTIVE platform interpreter via `python -m pip` (no pip3 binaries).
## @io       core_dir → bool
## @complexity O(n) — 2 pip subprocess calls
def _install_requirements(core_dir: str) -> bool:
    # endregion FUNC__install_requirements
    req_path = os.path.join(core_dir, "requirements.txt")
    if not os.path.isfile(req_path):
        logger.warning("[IMP:7][_install_requirements] No requirements.txt at %s", req_path)
        return False

    python_bin = _resolve_python_bin()
    logger.info(
        "[IMP:9][_install_requirements] Using interpreter %s for pip (bare `python3` resolves here)", python_bin
    )

    # Step 1: typing_extensions with --ignore-installed (Debian conflict workaround)
    logger.info("[IMP:9][_install_requirements] Installing typing_extensions (--ignore-installed)")
    pip_typing = [
        python_bin,
        "-m",
        "pip",
        "install",
        "typing_extensions",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_typing, label="pip typing_extensions"):
        return False

    # Step 1b: jsonschema with --ignore-installed — RC-сессия 2026-08-03 (e2e φ1 fail на bare VPS)
    # · Symptom: pip -r requirements.txt: "Cannot uninstall jsonschema 4.10.3 (installed by debian,
    #   no RECORD)" — φ1 ставит apt python3-jsonschema (4.10.3, без RECORD), requirements требует
    #   >=4.17 → pip не может заменить debian-пакет.
    # · Fix: тот же паттерн, что typing_extensions (--ignore-installed) — ставит свежую версию
    #   поверх, не трогая debian-пакет.
    logger.info("[IMP:9][_install_requirements] Installing jsonschema (--ignore-installed)")
    pip_jsonschema = [
        python_bin,
        "-m",
        "pip",
        "install",
        "jsonschema",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_jsonschema, label="pip jsonschema"):
        return False

    # Step 1c: pyopenssl with --ignore-installed — RC-сессия 2026-08-03 (e2e preflight fail)
    # · Symptom: preflight PanicException pyo3_runtime — import OpenSSL (debian pyOpenSSL 23.2)
    #   падает с cryptography 41.0.7 (pip, --break-system-packages) на Python 3.14
    # · Root: boto3 (dist-packages) → botocore → pyopenssl (debian 23.2) — несовместим с новой
    #   cryptography + 3.14. requirements.txt не пинит pyopenssl → остаётся debian-версия.
    # · Fix: тот же паттерн --ignore-installed (свежий pyopenssl поверх debian-пакета).
    logger.info("[IMP:9][_install_requirements] Installing pyopenssl (--ignore-installed)")
    pip_openssl = [
        python_bin,
        "-m",
        "pip",
        "install",
        "pyopenssl",
        "--ignore-installed",
        "--break-system-packages",
    ]
    if not _run(pip_openssl, label="pip pyopenssl"):
        return False

    # Step 2: full requirements.txt
    logger.info("[IMP:9][_install_requirements] Installing -r requirements.txt")
    pip_reqs = [
        python_bin,
        "-m",
        "pip",
        "install",
        "-r",
        req_path,
        "--break-system-packages",
    ]
    return _run(pip_reqs, label="pip -r requirements.txt")


# region FUNC_ensure_python_deps
## @purpose  Idempotent install of Python 3.14 + platform Python dependencies on VPS.
##           Port of node-lifecycle.sh:_ensure_python_deps(), extended with 3.14 (deadsnakes).
## @io       core_dir → bool
## @complexity O(1) marker check + O(P) interpreter install + O(n) pip install on mismatch
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

    # ── Content-hash guard (requirements hash + python version) ────────────
    if _check_content_hash(req_path):
        logger.info("[IMP:9][ensure_python_deps] Hash + python version match — deps already up to date")
        return True

    # ── Install Python 3.14 (deadsnakes PPA on Ubuntu 24.04) ───────────────
    if not _install_python314():
        logger.warning("[IMP:7][ensure_python_deps] Python 3.14 / pip installation failed")
        return False

    # ── Install Python requirements into the active interpreter ────────────
    if not _install_requirements(core_dir):
        logger.warning("[IMP:7][ensure_python_deps] Requirements installation failed")
        return False

    # ── Persist marker (requirements hash + python version) ────────────────
    current_hash = _compute_content_hash(req_path)
    if current_hash is None:
        logger.warning("[IMP:7][ensure_python_deps] Cannot compute hash after install — skipping persist")
        return False

    marker = f"{current_hash}\n{_compute_python_version()}"
    if not _save_content_hash(marker):
        logger.warning("[IMP:7][ensure_python_deps] Failed to persist content hash")
        # Not fatal — deps are installed, hash is a cache optimization

    logger.info("[IMP:9][ensure_python_deps] Complete — Python 3.14 + dependencies installed")
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
