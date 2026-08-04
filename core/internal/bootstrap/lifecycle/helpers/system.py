#!/usr/bin/env python3
# GREP_SUMMARY: system-helpers, apt-packages, is-pkg-installed, install-apt-packages, ensure-sops, ghcr-auth, install-cron-metrics, cron, metrics, dpkg, idempotent, install-cron-watchdog, watchdog-cron, journald-persistent
# STRUCTURE: ▶ is_pkg_installed ┌dpkg -s┐ → ◇ rc=0? → ⚡ install_apt_packages ┌apt-get update+install┐ → ⚡ ensure_sops ┌GitHub release download┐ → ⚡ ghcr_auth ┌docker_auth.ghcr_login┐ → ⚡ install_cron_metrics ┌flock+timeout cron.d┐ → ⚡ install_cron_watchdog ┌flock+timeout watchdog.py┐ → ⚡ ensure_journald_persistent ┌Storage=persistent┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Системные I/O-хелперы bootstrap-фаз (apt/пакеты, sops, GHCR auth, metrics cron,
##           watchdog cron, journald persistent) — извлечены из state_machine (B9 T1, U-08).
##           Все функции публичные.
## @scope    system.py: is_pkg_installed, install_apt_packages, ensure_sops, ghcr_auth,
##           install_cron_metrics (+ CRON_METRICS_FILE/CRON_METRICS_LINE константы),
##           install_cron_watchdog (+ CRON_WATCHDOG_FILE/CRON_WATCHDOG_LINE константы, DevPlan 132 W1),
##           ensure_journald_persistent (+ JOURNALD_CONF, DevPlan 132 W3).
##           Используются phases.py (φ1 system_bootstrap, φ3 platform_setup, φ6/φ11 registry auth).
## @invariants
##   - apt-get-операции идемпотентны (dpkg check перед установкой)
##   - sops-установка non-fatal (best-effort — скачивание из GitHub может быть недоступно)
##   - ghcr_auth: без GHCR_PULL_TOKEN → skip (не fatal)
##   - install_cron_metrics: идемпотентен (content match → no-op), атомарен (temp+mv),
##     non-fatal (False при сбое, никогда не raise) — φ3 контракт нефатальности (U-03, DevPlan 116 B3 T1)
##   - install_cron_watchdog: тот же паттерн (идемпотентен, атомарен, non-fatal) — DevPlan 132 W1
##   - ensure_journald_persistent: идемпотентен (Storage=persistent → no-op), атомарен (temp+mv),
##     non-fatal (False при сбое; systemctl restart best-effort) — DevPlan 132 W3, D6
##   - Все subprocess через shared/subprocess_io.run_subprocess (единый канон, B4)
##   - apt-get таймауты — канон APT_TIMEOUT (300) из shared/timeouts (DevPlan 123 T7)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита в публичные helpers
##            (DevPlan 116 B9 D1) — state_machine остаётся оркестрацией.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-01 · DevPlan 116 B3 T1 (U-03): +install_cron_metrics — /etc/cron.d/platform-metrics
##           (flock -n + timeout 50 + absolute path), вызывается из φ3 phase_platform_setup шаг 2.5
## @changes  2026-08-03 · DevPlan 123 T7 — install_apt_packages: timeout=120 legacy →
##           timeout=APT_TIMEOUT (300, канон shared/timeouts)
## @changes  2026-08-04 · DevPlan 132 W1 — +install_cron_watchdog (host-cron watchdog, паттерн
##           install_cron_metrics); DevPlan 132 W3 — +ensure_journald_persistent (D6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_text as _atomic_write_text
from core.internal.shared.docker_auth import ghcr_login as _shared_ghcr_login
from core.internal.shared.exceptions import PlatformError
from core.internal.shared.subprocess_io import run_subprocess
from core.internal.shared.timeouts import APT_TIMEOUT

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
        # B4: единый канон shared/subprocess_io (check=True = lifecycle raise-семантика);
        # apt-get update/install на свежей VPS (deadsnakes PPA) может занимать >30s —
        # канон APT_TIMEOUT (300) из shared/timeouts (DevPlan 123 T7), legacy 120 убран.
        run_subprocess(["apt-get", "update", "-qq"], check=True, timeout=APT_TIMEOUT)
        run_subprocess(["apt-get", "install", "-y", "-qq", *to_install], check=True, timeout=APT_TIMEOUT)
        for pkg in to_install:
            run_subprocess(["dpkg", "-s", pkg], check=True)
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
        # B4: check=True (raise-семантика); curl скачивание — явный timeout=120 (legacy default)
        run_subprocess(
            ["curl", "-sSL", "-o", "/usr/local/bin/sops", url],
            check=True,
            timeout=120,
        )
        run_subprocess(["chmod", "0755", "/usr/local/bin/sops"], check=True)
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

        # ── Atomic write: shared atomic_writer canon (E5 — temp + fsync + os.replace, 0644) ──
        _atomic_write_text(CRON_METRICS_FILE, cron_line + "\n", mode=0o644)

        if existing:
            logger.info("[IMP:7][cron_metrics] Cron file updated (content changed)")
        logger.info("[IMP:9][cron_metrics] Metrics cron installed at %s", CRON_METRICS_FILE)
        return True
    except OSError as e:
        logger.warning("[IMP:7][cron_metrics] Cron install failed (non-fatal): %s", e)
        return False


# endregion FUNC_install_cron_metrics


# ═══════════════════════════════════════════════════════════════════════════
# Watchdog cron (DevPlan 132 W1) — install_cron_watchdog
# ═══════════════════════════════════════════════════════════════════════════

# Cron.d target file — watchdog: auto-restart unhealthy containers (host cron).
CRON_WATCHDOG_FILE = "/etc/cron.d/platform-watchdog"
# Contract line: */5 * * * * root flock -n <lock> timeout 50 <core_dir>/internal/healthcheck/watchdog.py
# (cron.d minimal PATH → absolute binary paths; watchdog.py — stdlib-only, без PYTHONPATH).
CRON_WATCHDOG_LINE = (
    "*/5 * * * * root /usr/bin/flock -n /run/lock/platform-watchdog.lock "
    "/usr/bin/timeout 50 {core_dir}/internal/healthcheck/watchdog.py"
)


# region FUNC_install_cron_watchdog
## @purpose  Install the watchdog cron job into /etc/cron.d/platform-watchdog
##           (flock -n + timeout 50s + absolute watchdog.py path). Idempotent: existing file
##           with identical content → no-op (SKIP). Atomic: temp file → os.replace (mv).
##           Non-fatal: OSError → WARN + False — φ3 continues. Паттерн install_cron_metrics (W1).
## @io       ⇥ core_dir: platform core directory (absolute path, embedded in cron line)
##           ⎋ bool: True = installed/verified, False = failure (non-fatal — never raises)
## @complexity O(1) — single file read + atomic write
## @invariants
##   - CRON_WATCHDOG_LINE contract: */5 * * * * + flock -n + timeout 50 + watchdog.py absolute path
##   - Idempotency: identical content → SKIP (no-op, mtime unchanged)
##   - Content mutation → overwrite (IMP:7 log); fresh install → IMP:9
##   - mkdir /run/lock best-effort (tmpfs — exists on Ubuntu 24.04)
##   - Non-fatal: OSError (permission denied, read-only fs) → WARN + False — φ3 continues
## @rationale  DevPlan 132 D1: host-cron по канону install_cron_metrics (точечное расширение,
##             без новой архитектуры); watchdog.py stdlib-only → cron без PYTHONPATH.
def install_cron_watchdog(core_dir: str) -> bool:
    """Install the watchdog cron entry. Returns True on success/no-op, False on failure."""
    try:
        cron_line = CRON_WATCHDOG_LINE.format(core_dir=core_dir)
    except (KeyError, IndexError) as e:
        logger.warning("[IMP:7][cron_watchdog] Invalid CRON_WATCHDOG_LINE template (non-fatal): %s", e)
        return False

    try:
        # ── Idempotency: existing file with identical content → SKIP (no-op) ──
        try:
            with open(CRON_WATCHDOG_FILE) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        if existing == cron_line + "\n":
            logger.info("[IMP:7][cron_watchdog] Cron already installed — no-op (idempotent)")
            return True

        # ── Ensure /run/lock exists (best-effort — tmpfs, present on Ubuntu) ──
        try:
            os.makedirs("/run/lock", exist_ok=True)
        except OSError as e:
            logger.warning("[IMP:7][cron_watchdog] mkdir /run/lock failed (best-effort): %s", e)

        # ── Atomic write: shared atomic_writer canon (E5 — temp + fsync + os.replace, 0644) ──
        _atomic_write_text(CRON_WATCHDOG_FILE, cron_line + "\n", mode=0o644)

        if existing:
            logger.info("[IMP:7][cron_watchdog] Cron file updated (content changed)")
        logger.info("[IMP:9][cron_watchdog] Watchdog cron installed at %s", CRON_WATCHDOG_FILE)
        return True
    except OSError as e:
        logger.warning("[IMP:7][cron_watchdog] Cron install failed (non-fatal): %s", e)
        return False


# endregion FUNC_install_cron_watchdog


# ═══════════════════════════════════════════════════════════════════════════
# Journald persistent (DevPlan 132 W3, D6) — ensure_journald_persistent
# ═══════════════════════════════════════════════════════════════════════════

# systemd journald config — Storage=persistent обязателен для кросс-бут реконструкции
# journald-логов (Debt D-1 из 126: volatile-journal не переживает reboot).
JOURNALD_CONF = "/etc/systemd/journald.conf"


# region FUNC__set_storage_persistent
## @purpose  Чистая функция: гарантирует Storage=persistent в содержимом journald.conf.
##           Существующая активная строка Storage=... заменяется; при отсутствии ключа — append.
## @io       ⇥ content: str → ⎋ str (новое содержимое)
## @complexity O(N) — N строк конфига
## @invariants
##   - Комментированная строка (закомментированный Storage=auto) НЕ заменяется — append активной строки
##   - Повторный вызов на результате — no-op (Storage=persistent уже присутствует)
def _set_storage_persistent(content: str) -> str:
    """Return content with Storage=persistent enforced (replace active line or append)."""
    lines = content.splitlines()
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("Storage="):
            out.append("Storage=persistent")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append("Storage=persistent")
    return "\n".join(out) + "\n"


# endregion FUNC__set_storage_persistent


# region FUNC_ensure_journald_persistent
## @purpose  Установить Storage=persistent в /etc/systemd/journald.conf (идемпотентно, temp+mv)
##           + restart systemd-journald (non-fatal). Закрывает D-1 из 126 (journald → Loki).
## @io       ⎋ bool: True = настроено/no-op, False = failure (non-fatal — never raises)
## @complexity O(N) — N строк конфига + 1 subprocess
## @invariants
##   - Idempotency: Storage=persistent уже присутствует → no-op (без записи и рестарта)
##   - Atomic write через atomic_writer (temp + fsync + os.replace)
##   - systemctl restart systemd-journald — non-fatal (best-effort, канал может быть занят)
##   - OSError (read-only /etc, нет прав) → WARN + False — φ1 продолжается
## @rationale  D6 (DevPlan 132): при volatile-journal journald-скрейп не переживает reboot —
##             D-1 не закрывается без Storage=persistent.
def ensure_journald_persistent() -> bool:
    """Ensure systemd journald uses persistent storage. Returns True on success/no-op."""
    try:
        with open(JOURNALD_CONF, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning("[IMP:7][journald] Cannot read %s (non-fatal): %s", JOURNALD_CONF, e)
        return False

    if "Storage=persistent" in content:
        logger.info("[IMP:7][journald] Storage=persistent already set — no-op (idempotent)")
        return True

    try:
        _atomic_write_text(JOURNALD_CONF, _set_storage_persistent(content), mode=0o644)
    except OSError as e:
        logger.warning("[IMP:7][journald] Cannot write %s (non-fatal): %s", JOURNALD_CONF, e)
        return False
    logger.info("[IMP:9][journald] Storage=persistent set in %s", JOURNALD_CONF)

    # ── Restart journald (non-fatal — best-effort) ──
    try:
        run_subprocess(["systemctl", "restart", "systemd-journald"], check=True, timeout=60)
        logger.info("[IMP:9][journald] systemd-journald restarted")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][journald] systemd-journald restart failed (non-fatal): %s", e)

    return True


# endregion FUNC_ensure_journald_persistent
