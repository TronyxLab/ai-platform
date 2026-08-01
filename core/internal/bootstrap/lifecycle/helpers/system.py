#!/usr/bin/env python3
# GREP_SUMMARY: system-helpers, apt-packages, is-pkg-installed, install-apt-packages, ensure-sops, ghcr-auth, install-cron-metrics, cron, metrics, dpkg, idempotent
# STRUCTURE: ▶ is_pkg_installed ┌dpkg -s┐ → ◇ rc=0? → ⚡ install_apt_packages ┌apt-get update+install┐ → ⚡ ensure_sops ┌GitHub release download┐ → ⚡ ghcr_auth ┌docker_auth.ghcr_login┐ → ⚡ install_cron_metrics ┌flock+timeout cron.d┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Системные I/O-хелперы bootstrap-фаз (apt/пакеты, sops, GHCR auth, metrics cron) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    system.py: is_pkg_installed, install_apt_packages, ensure_sops, ghcr_auth,
##           install_cron_metrics (+ CRON_METRICS_FILE/CRON_METRICS_LINE константы).
##           Используются phases.py (φ1 system_bootstrap, φ3 platform_setup, φ6/φ11 registry auth).
## @invariants
##   - apt-get-операции идемпотентны (dpkg check перед установкой)
##   - sops-установка non-fatal (best-effort — скачивание из GitHub может быть недоступно)
##   - ghcr_auth: без GHCR_PULL_TOKEN → skip (не fatal)
##   - install_cron_metrics: идемпотентен (content match → no-op), атомарен (temp+mv),
##     non-fatal (False при сбое, никогда не raise) — φ3 контракт нефатальности (U-03, DevPlan 116 B3 T1)
##   - Все subprocess через helpers.subprocess_io.run_subprocess (единый канон)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита в публичные helpers
##            (DevPlan 116 B9 D1) — state_machine остаётся оркестрацией.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-01 · DevPlan 116 B3 T1 (U-03): +install_cron_metrics — /etc/cron.d/platform-metrics
##           (flock -n + timeout 50 + absolute path), вызывается из φ3 phase_platform_setup шаг 2.5
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile

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


# ═══════════════════════════════════════════════════════════════════════════
# Metrics cron (DevPlan 116 B3 T1, U-03) — install_cron_metrics
# ═══════════════════════════════════════════════════════════════════════════

# Cron.d target file — read by cron daemon (no crontab -e needed).
# Absolute paths ONLY — cron.d runs with minimal PATH.
CRON_METRICS_FILE = "/etc/cron.d/platform-metrics"
# Contract line (gate test test_gate_status_page.py::TestGateStatusPageCrontabContract
# asserts flock -n + timeout 50 + platform-export-metrics.sh):
#   * * * * * root /usr/bin/flock -n <lock> /usr/bin/timeout 50 <core_dir>/internal/healthcheck/platform-export-metrics.sh
# {core_dir} is formatted at call time (bootstrap core lives in /opt/platform on the node).
CRON_METRICS_LINE = (
    "* * * * * root /usr/bin/flock -n /run/lock/platform-metrics.lock "
    "/usr/bin/timeout 50 {core_dir}/internal/healthcheck/platform-export-metrics.sh "
    ">/dev/null 2>&1"
)


# region FUNC_install_cron_metrics
## @purpose  Install the platform metrics export cron job into /etc/cron.d/platform-metrics
##           (flock -n + timeout 50s + absolute script path). Idempotent: existing file with
##           identical content → no-op (SKIP). Atomic: temp file → os.replace (mv).
## @io       ⇥ core_dir: platform core directory (absolute path, embedded in cron line)
##           ⎋ bool: True = installed/verified, False = failure (non-fatal — never raises)
## @complexity O(1) — single file read + atomic write
## @invariants
##   - CRON_METRICS_LINE contract: flock -n + timeout 50 + absolute path to platform-export-metrics.sh
##   - Idempotency: identical content → SKIP (no-op, mtime unchanged)
##   - Content mutation → overwrite (IMP:7 log)
##   - Fresh install → IMP:9 log «cron installed»
##   - mkdir /run/lock best-effort (tmpfs — exists on Ubuntu 24.04)
##   - Non-fatal: OSError (permission denied, read-only fs) → WARN + False — φ3 continues
## @rationale  U-03: phases.py modulemap promised «metrics cron» in φ3 but no installer existed —
##             greenfield node ended up without metrics. Pattern: install-tor-proxy.sh:324
##             install_cron_healthcheck (/etc/cron.d/). Python-first language policy.
def install_cron_metrics(core_dir: str) -> bool:
    """Install the metrics cron entry. Returns True on success/no-op, False on failure."""
    try:
        cron_line = CRON_METRICS_LINE.format(core_dir=core_dir)
    except (KeyError, IndexError) as e:
        logger.warning("[IMP:7][cron_metrics] Invalid CRON_METRICS_LINE template (non-fatal): %s", e)
        return False

    try:
        # ── Idempotency: existing file with identical content → SKIP (no-op) ──
        try:
            with open(CRON_METRICS_FILE) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        if existing == cron_line + "\n":
            logger.info("[IMP:7][cron_metrics] Cron already installed — no-op (idempotent)")
            return True

        # ── Ensure /run/lock exists (best-effort — tmpfs, present on Ubuntu) ──
        try:
            os.makedirs("/run/lock", exist_ok=True)
        except OSError as e:
            logger.warning("[IMP:7][cron_metrics] mkdir /run/lock failed (best-effort): %s", e)

        # ── Atomic write: temp file in same dir → chmod 0644 → os.replace ──
        target_dir = os.path.dirname(CRON_METRICS_FILE)
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix="platform-metrics-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(cron_line + "\n")
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, CRON_METRICS_FILE)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        if existing:
            logger.info("[IMP:7][cron_metrics] Cron file updated (content changed)")
        logger.info("[IMP:9][cron_metrics] Metrics cron installed at %s", CRON_METRICS_FILE)
        return True
    except OSError as e:
        logger.warning("[IMP:7][cron_metrics] Cron install failed (non-fatal): %s", e)
        return False


# endregion FUNC_install_cron_metrics
