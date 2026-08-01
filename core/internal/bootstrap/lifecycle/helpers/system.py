#!/usr/bin/env python3
# GREP_SUMMARY: system-helpers, apt-packages, is-pkg-installed, install-apt-packages, ensure-sops, ghcr-auth, dpkg, idempotent
# STRUCTURE: ▶ is_pkg_installed ┌dpkg -s┐ → ◇ rc=0? → ⚡ install_apt_packages ┌apt-get update+install┐ → ⚡ ensure_sops ┌GitHub release download┐ → ⚡ ghcr_auth ┌docker_auth.ghcr_login┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Системные I/O-хелперы bootstrap-фаз (apt/пакеты, sops, GHCR auth) — извлечены
##           из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    system.py: is_pkg_installed, install_apt_packages, ensure_sops, ghcr_auth.
##           Используются phases.py (φ1 system_bootstrap, φ6/φ11 registry auth).
## @invariants
##   - apt-get-операции идемпотентны (dpkg check перед установкой)
##   - sops-установка non-fatal (best-effort — скачивание из GitHub может быть недоступно)
##   - ghcr_auth: без GHCR_PULL_TOKEN → skip (не fatal)
##   - Все subprocess через helpers.subprocess_io.run_subprocess (единый канон)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита в публичные helpers
##            (DevPlan 116 B9 D1) — state_machine остаётся оркестрацией.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess

from core.internal.bootstrap.lifecycle.helpers.subprocess_io import run_subprocess
from core.internal.shared.docker_auth import ghcr_login as _shared_ghcr_login
from core.internal.shared.exceptions import PlatformError

logger = logging.getLogger(__name__)


# region FUNC_is_pkg_installed
## @purpose  Check if a single dpkg package is installed, handling errors gracefully
## @io       ⇥ pkg: str → ⎋ bool (True = installed)
## @complexity O(1)
def is_pkg_installed(pkg: str) -> bool:
    """Check dpkg status for a package. Returns True if installed, False on error."""
    try:
        result = subprocess.run(
            ["dpkg", "-s", pkg],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_is_pkg_installed


# region FUNC_install_apt_packages
## @purpose  Idempotent apt-get install: checks dpkg, only installs missing.
## @io       ⇥ packages: list[str] → ⎋ None
## @complexity O(N) where N = packages
def install_apt_packages(packages: list[str]) -> None:
    """Install apt packages if not already installed."""
    to_install: list[str] = [pkg for pkg in packages if not is_pkg_installed(pkg)]

    if to_install:
        logger.info("[IMP:9][apt] Installing %d packages: %s", len(to_install), " ".join(to_install))
        run_subprocess(["apt-get", "update", "-qq"], "apt_update")
        run_subprocess(["apt-get", "install", "-y", "-qq", *to_install], "apt_install")
        for pkg in to_install:
            run_subprocess(["dpkg", "-s", pkg], f"verify_{pkg}", check_required=True)
    else:
        logger.info("[IMP:7][apt] All packages already installed — skipping")


# endregion FUNC_install_apt_packages


# region FUNC_ensure_sops
## @purpose  Install sops (v3.9.4) from GitHub if not present. Non-fatal.
## @io       ⇥ None → ⎋ None (side-effect: downloads and installs sops binary)
## @complexity O(1)
def ensure_sops() -> None:
    """Install sops (v3.9.4) from GitHub if not present."""
    try:
        result = subprocess.run(["command", "-v", "sops"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info("[IMP:7][sops] Already installed")
            return
    except FileNotFoundError:
        pass

    logger.info("[IMP:8][sops] Installing sops v3.9.4 from GitHub")
    try:
        # Detect architecture
        arch_result = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        arch = arch_result.stdout.strip() if arch_result.returncode == 0 else "amd64"
        if arch not in ("amd64", "arm64"):
            arch = "amd64"

        url = f"https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.{arch}"
        run_subprocess(
            ["curl", "-sSL", "-o", "/usr/local/bin/sops", url],
            "sops_download",
        )
        run_subprocess(["chmod", "0755", "/usr/local/bin/sops"], "sops_chmod")
        logger.info("[IMP:9][sops] sops v3.9.4 installed")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][sops] Failed to install sops: %s", e)


# endregion FUNC_ensure_sops


# region FUNC_ghcr_auth
## @purpose  Docker login to ghcr.io using GHCR_PULL_TOKEN for ci-deploy user. Non-fatal.
## @io       ⇥ None → ⎋ None (non-fatal if token not set)
## @complexity O(1)
## @changes 2026-07-30 | T20a — Replaced inline subprocess with shared docker_auth.ghcr_login()
##           (runs as root directly, no sudo needed in bootstrap context)
def ghcr_auth() -> None:
    """Configure GHCR docker login for ci-deploy user."""
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][ghcr_auth] GHCR_PULL_TOKEN not set — skipping ghcr auth")
        return
    success = _shared_ghcr_login(token, user="ci-deploy")
    if success:
        logger.info("[IMP:9][ghcr_auth] GHCR auth successful")


# endregion FUNC_ghcr_auth
