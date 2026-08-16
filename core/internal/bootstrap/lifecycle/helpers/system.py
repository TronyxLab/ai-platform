#!/usr/bin/env python3
# GREP_SUMMARY: system-helpers, apt-packages, is-pkg-installed, install-apt-packages, ensure-sops, ghcr-auth, install-cron-metrics, cron, metrics, dpkg, idempotent, install-cron-watchdog, watchdog-cron, journald-persistent, install-zram, zram, swappiness, install-cron-prune, prune, retry, backoff, purge-cruft, cruft, purge-provider-repos, fstab, fstrim, defaults, DevPlan-162, DevPlan-164
# STRUCTURE: ▶ is_pkg_installed ┌dpkg -s┐ → ◇ rc=0? → ⚡ install_apt_packages ┌_run_with_retry apt-get update+install┐ → ⚡ ensure_sops ┌GitHub release download┐ → ⚡ ghcr_auth ┌docker_auth.ghcr_login┐ → ⚡ install_cron_metrics ┌flock+timeout cron.d┐ → ⚡ install_cron_watchdog ┌flock+timeout watchdog.py┐ → ⚡ ensure_journald_persistent ┌Storage=persistent┐ → ⚡ install_zram ┌zram-tools + zramswap + sysctl┐ → ⚡ install_cron_prune ┌docker/apt monthly prune┐ → ⚡ purge_cruft ┌apt purge only-installed┐ → ⚡ purge_provider_repos ┌rm timeweb-* + apt update┐ → ⚡ ensure_fstab_policy ┌defaults + fstrim.timer┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Системные I/O-хелперы bootstrap-фаз (apt/пакеты, sops, GHCR auth, metrics cron,
##           watchdog cron, journald persistent, zram, prune cron, cruft purge) — извлечены
##           из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    system.py: is_pkg_installed, install_apt_packages, ensure_sops, ghcr_auth,
##           install_cron_metrics (+ CRON_METRICS_FILE/CRON_METRICS_LINE константы),
##           install_cron_watchdog (+ CRON_WATCHDOG_FILE/CRON_WATCHDOG_LINE константы, DevPlan 132 W1),
##           ensure_journald_persistent (+ JOURNALD_CONF, DevPlan 132 W3),
##           install_zram (W4-1, DevPlan 162), install_cron_prune (W4-4, DevPlan 162),
##           purge_cruft (W10-1, DevPlan 162), _run_with_retry (W7-4, DevPlan 162),
##           purge_provider_repos (164 W0-3.2), ensure_fstab_policy (164 W0-3.4).
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
##   - install_zram (162 W4-1): non-fatal (False при сбое apt/записи, никогда не raise);
##     файлы идемпотентны (content-match no-op), атомарны (temp+mv, 0644)
##   - install_cron_prune (162 W4-4): тот же паттерн (content-match no-op, атомарен, non-fatal);
##     CRON_PRUNE_LINES — monthly docker system prune (без volumes, until=720h) + apt-get clean
##   - purge_cruft (162 W10-1): purges ТОЛЬКО установленные пакеты из CRUFT_PURGE_PACKAGES
##     (dpkg-gate); non-fatal (False при сбое apt, никогда не raise); sysstat НЕ в списке
##     (не устанавливается платформой — 0 потребителей, решение оператора 164 Q5)
##   - purge_provider_repos (164 W0-3.2): удаляет ТОЛЬКО timeweb-* файлы из sources.list.d +
##     apt-get update; идемпотентен (0 совпадений = no-op), non-fatal
##   - ensure_fstab_policy (164 W0-3.4): data-строки fstab → `defaults` (атомарно, content-match
##     no-op) + fstrim.timer enable; /boot и swap не трогаются; non-fatal
##   - _run_with_retry (162 W7-4): retry transient apt/git failures (attempts=RETRY_COUNT+1,
##     backoff=RETRY_BACKOFF_SECONDS — канон timeouts), делегат shared/retry.py (177 W3.1),
##     raise PlatformError на финальный провал (check=True семантика сохраняется у вызывающего)
##   - Все subprocess через shared/subprocess_io.run_subprocess (единый канон, B4)
##   - apt-get таймауты — канон APT_TIMEOUT (300) из shared/timeouts (DevPlan 123 T7)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита в публичные helpers
##            (DevPlan 116 B9 D1) — state_machine остаётся оркестрацией.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-01 · DevPlan 116 B3 T1 (U-03): +install_cron_metrics — /etc/cron.d/platform-metrics
##           (flock -n + timeout 50 + absolute path), вызывается из φ3 phase_platform_setup шаг 2.5
## @changes  2026-08-03 · DevPlan 123 T7 — install_apt_packages: timeout=120 →
##           timeout=APT_TIMEOUT (300, канон shared/timeouts)
## @changes  2026-08-04 · DevPlan 132 W1 — +install_cron_watchdog (host-cron watchdog, паттерн
##           install_cron_metrics); DevPlan 132 W3 — +ensure_journald_persistent (D6)
## @changes  2026-08-13 · DevPlan 162 — +install_zram (W4-1), +install_cron_prune (W4-4),
##           +purge_cruft (W10-1), +_run_with_retry (W7-4, install_apt_packages переведён на retry)
## @changes  2026-08-16 · DevPlan 177 W3.1 — _run_with_retry → делегат shared/retry.py
##           (retry-loop/backoff/sleep/logging консолидированы; attempts/backoff — из timeouts)
## @changes  2026-08-13 · DevPlan 164 — +purge_provider_repos (W0-3.2, ex-162 W2-6: timeweb-* репо),
##           +ensure_fstab_policy (W0-3.4, ex-162 W4-2: defaults + fstrim.timer); sysstat-комментарий
##           обновлён (Q5: не устанавливается, 0 потребителей)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
from collections.abc import Callable

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
from core.internal.shared.atomic_writer import atomic_write_text as _atomic_write_text
from core.internal.shared.docker_auth import ghcr_login as _shared_ghcr_login
from core.internal.shared.exceptions import PlatformError
from core.internal.shared.retry import retry as _shared_retry
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 177 W3.1: retry-политики _run_with_retry (attempts/backoff) — из того же реестра (U-11/D34).
from core.internal.shared.timeouts import (
    APT_TIMEOUT,
    CONVERGE_DOCKER_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
    LIFECYCLE_CMD_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
    RETRY_COUNT,
    SYSTEM_CMD_TIMEOUT,
)

# 177 W3.1: канонический backoff-дефолт _run_with_retry — (5, 10, 20) из timeouts;
# модульная константа, а не вызов в default-параметре (pyright reportCallInDefaultInitializer)
_RUN_WITH_RETRY_BACKOFF: tuple[int, ...] = tuple(RETRY_BACKOFF_SECONDS)

logger = logging.getLogger(__name__)


# region FUNC_is_pkg_installed
## @purpose  Check if a single dpkg package is installed, handling errors gracefully
## @io       ⇥ pkg: str, runner: CommandRunner | None = None (DI, W-H DevPlan 163) → ⎋ bool (True = installed)
## @complexity O(1)
## @invariants
##   - DI: runner=None → default_command_runner() (канон run_subprocess — поведение без изменений)
##   - Тесты передают fake-раннер вместо monkeypatch subprocess.run
def is_pkg_installed(pkg: str, *, runner: CommandRunner | None = None) -> bool:
    """Check dpkg status for a package. Returns True if installed, False on error."""
    active = runner if runner is not None else default_command_runner()
    try:
        result = active.run(["dpkg", "-s", pkg], timeout=CONVERGE_DOCKER_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    else:
        return result.returncode == 0


# endregion FUNC_is_pkg_installed


# region FUNC__run_with_retry
## @purpose  Retry subprocess with backoff (DevPlan 162 W7-4): transient apt/git failures
##           (network, dpkg-lock, mirror 5xx) не должны валить bootstrap с первого раза.
##           Тонкий делегат ЕДИНОГО retry-цикла shared/retry.py (DevPlan 177 W3.1):
##           retry-loop/backoff/sleep/logging — в shared.retry (result-mode);
##           здесь — адаптация к CommandRunner-канону (check=False — graceful) и
##           raise PlatformError на финальный провал (check=True семантика вызывающего).
## @io       ⇥ cmd: list[str], attempts: int = RETRY_COUNT+1 (3 — канон timeouts),
##           backoff: tuple[int, ...] = _RUN_WITH_RETRY_BACKOFF ((5,10,20) — канон timeouts),
##           timeout: int | None = None, runner: CommandRunner | None = None (DI, W-H) → ⎋ subprocess.CompletedProcess
##           ⚡ PlatformError — финальный провал после attempts-попыток
## @complexity O(attempts * M) где M = время выполнения команды
## @invariants
##   - Retry только на транзиентные сбои (rc != 0); timeout (rc=124) тоже retry'ится
##   - Backoff: clamp на последний элемент (канон shared.retry: last value repeats)
##   - Логи IMP:7-9 — в shared.retry; финальный провал — PlatformError + IMP-контекст
##   - НЕ retry'ит fatal_rc — run_subprocess вызывается с fatal_rc=() (graceful полностью)
##   - DI: runner=None → default_command_runner() (поведение без изменений); тесты передают
##     fake-раннер вместо monkeypatch run_subprocess/time.sleep
def _run_with_retry(
    cmd: list[str],
    attempts: int = RETRY_COUNT + 1,
    backoff: tuple[int, ...] = _RUN_WITH_RETRY_BACKOFF,
    timeout: int | None = None,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with retry+backoff (DevPlan 162 W7-4). Raises PlatformError on final failure."""
    active = runner if runner is not None else default_command_runner()

    def _attempt() -> subprocess.CompletedProcess[str]:
        # run_subprocess: timeout=None → канонический дефолт (30s); явный timeout — пробрасывается.
        # PlatformError (если вдруг raise) пропагируется напрямую — shared.retry result-mode
        # исключения НЕ перехватывает (канон не retry'ится — try/except не нужен).
        return active.run(cmd, check=False, timeout=timeout) if timeout is not None else active.run(cmd, check=False)

    last = _shared_retry(
        _attempt,
        attempts=attempts,
        backoff_seconds=backoff,
        retryable=lambda result: result.returncode != 0,
    )
    if last.returncode == 0:
        return last
    msg = f"Command {' '.join(cmd)} failed after {attempts} attempts (last rc={last.returncode})"
    raise PlatformError(msg)


# endregion FUNC__run_with_retry


# region FUNC_install_apt_packages
## @purpose  Idempotent apt-get install: checks dpkg, only installs missing. W7-4 (DevPlan 162):
##           apt-get update/install через _run_with_retry (3 попытки, backoff) — transient
##           apt-сбои (network/lock) не валят bootstrap с первого раза.
## @io       ⇥ packages: list[str], runner: CommandRunner | None = None (DI, W-H DevPlan 163) → ⎋ None
##           ⚡ PlatformError — финальный провал apt (после retry) — check=True семантика сохранена
## @complexity O(N * attempts) где N = packages
## @invariants
##   - DI: runner=None → default_command_runner() (поведение без изменений); тесты передают
##     fake-раннер/пакмэн-стейт вместо monkeypatch is_pkg_installed/_run_with_retry
def install_apt_packages(packages: list[str], *, runner: CommandRunner | None = None) -> None:
    """Install apt packages if not already installed."""
    active = runner if runner is not None else default_command_runner()
    to_install: list[str] = [pkg for pkg in packages if not is_pkg_installed(pkg, runner=active)]

    if to_install:
        logger.info("[IMP:9][apt] Installing %d packages: %s", len(to_install), " ".join(to_install))
        # B4: единый канон shared/subprocess_io; apt-get update/install на свежей VPS (deadsnakes PPA)
        # может занимать >30s — канон APT_TIMEOUT (300) из shared/timeouts (DevPlan 123 T7).
        # DevPlan 162 W7-4: транзиентные apt-сбои → _run_with_retry (3 попытки, backoff 5/10/20).
        _run_with_retry(["apt-get", "update", "-qq"], timeout=APT_TIMEOUT, runner=active)
        _run_with_retry(["apt-get", "install", "-y", "-qq", *to_install], timeout=APT_TIMEOUT, runner=active)
        for pkg in to_install:
            active.run(["dpkg", "-s", pkg], check=True)
    else:
        logger.info("[IMP:7][apt] All packages already installed — skipping")


# endregion FUNC_install_apt_packages


# region FUNC__install_sops_binary
## @purpose  Скачивание+установка бинарника sops (PLW0717 extraction из ensure_sops).
## @io       ⇥ active: CommandRunner → ⎋ None ⚡ PlatformError/TimeoutExpired (проброс в except)
## @complexity O(1) — 2-3 subprocess-операции
## @invariants — check=True (raise-семантика, B4); curl timeout=120 (default)
def _install_sops_binary(active: CommandRunner) -> None:
    """Install sops binary via arch detection + curl download + chmod."""
    # Detect architecture
    arch_result = active.run(["dpkg", "--print-architecture"], timeout=DOCKER_CMD_TIMEOUT, check=False)
    arch = arch_result.stdout.strip() if arch_result.returncode == 0 else "amd64"
    if arch not in {"amd64", "arm64"}:
        arch = "amd64"

    url = f"https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.{arch}"
    # B4: check=True (raise-семантика); curl скачивание — явный timeout=120 (default)
    active.run(
        ["curl", "-sSL", "-o", "/usr/local/bin/sops", url],
        check=True,
        timeout=LIFECYCLE_CMD_TIMEOUT,
    )
    active.run(["chmod", "0755", "/usr/local/bin/sops"], check=True)
    logger.info("[IMP:9][sops] sops v3.9.4 installed")


# endregion FUNC__install_sops_binary


# region FUNC_ensure_sops
## @purpose  Install sops (v3.9.4) from GitHub if not present. Non-fatal.
## @io       ⇥ which: Callable[[str], str | None] | None = None (DI, W-H DevPlan 163 — резолвер бинаря;
##              None = shutil.which), runner: CommandRunner | None = None (DI — subprocess-канал)
##              → ⎋ None (side-effect: downloads and installs sops binary)
## @complexity O(1)
## @invariants
##   - Идемпотентность: sops в PATH (which-резолвер) → no-op (БЕЗ re-download)
##   - Установка non-fatal (best-effort — GitHub может быть недоступен)
## @changes 2026-08-05 | DevPlan 136 W9 T9.12 (B-4): `/bin/bash -c "command -v sops"` →
##           shutil.which — `command` это bash-builtin, прямой subprocess.run(["command", ...])
##           ВСЕГДА FileNotFoundError → sops перекачивался на КАЖДОМ φ1 (B-4: повторный φ1
##           без re-download нарушался); паттерн TRAP[BUG] state_store._check_command_exists
## @changes 2026-08-13 | DevPlan 163 W-H — +which/runner параметры (DI вместо monkeypatch)
def ensure_sops(
    *,
    which: Callable[[str], str | None] | None = None,
    runner: CommandRunner | None = None,
) -> None:
    """Install sops (v3.9.4) from GitHub if not present."""
    import shutil

    which_resolver = shutil.which if which is None else which
    active = runner if runner is not None else default_command_runner()

    if which_resolver("sops"):
        logger.info("[IMP:7][sops] Already installed (%s)", which_resolver("sops"))
        return

    logger.info("[IMP:8][sops] Installing sops v3.9.4 from GitHub")
    try:
        _install_sops_binary(active)
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


# region FUNC__install_cron_metrics_body
## @purpose  Идемпотентная установка metrics-cron записи (PLW0717 extraction из install_cron_metrics).
## @io       ⇥ cron_line: str, target_file: str, lock_dir: str | None = None (DI — 167 D4:
##              путь /run/lock вместо патча os.makedirs; None = канонический /run/lock),
##              write_fn: Callable[[str, str, int], None] | None = None (DI — open-fn шов:
##              инжектируемая запись вместо патча os.replace в atomic_writer; None = atomic_writer)
##              → ⎋ None ⚡ OSError (проброс в except)
## @complexity O(1) — read + atomic write
## @invariants — identical content → no-op; atomic_writer канон (E5); mkdir lock_dir best-effort
def _install_cron_metrics_body(
    cron_line: str,
    target_file: str,
    *,
    lock_dir: str | None = None,
    write_fn: Callable[[str, str, int], None] | None = None,
) -> None:
    """Write the metrics cron entry atomically (no-op when content identical)."""
    # ── Idempotency: existing file with identical content → SKIP (no-op) ──
    try:
        with pathlib.Path(target_file).open(encoding="utf-8") as f:
            existing = f.read()
    except FileNotFoundError:
        existing = ""
    if existing == cron_line + "\n":
        logger.info("[IMP:7][cron_metrics] Cron already installed — no-op (idempotent)")
        return

    # ── Ensure lock dir exists (best-effort — tmpfs, present on Ubuntu) ──
    try:
        os.makedirs(lock_dir if lock_dir is not None else "/run/lock", exist_ok=True)
    except OSError as e:
        logger.warning("[IMP:7][cron_metrics] mkdir %s failed (best-effort): %s", lock_dir or "/run/lock", e)

    # ── Atomic write: shared atomic_writer canon (E5 — temp + fsync + os.replace, 0644) ──
    if write_fn is not None:
        write_fn(target_file, cron_line + "\n", 0o644)
    else:
        _atomic_write_text(target_file, cron_line + "\n", mode=0o644)

    if existing:
        logger.info("[IMP:7][cron_metrics] Cron file updated (content changed)")
    logger.info("[IMP:9][cron_metrics] Metrics cron installed at %s", target_file)


# endregion FUNC__install_cron_metrics_body


# region FUNC_install_cron_metrics
## @purpose  Install the platform metrics export cron job into /etc/cron.d/platform-metrics
##           (flock -n + timeout 50s + absolute script path). Idempotent: existing file with
##           identical content → no-op (SKIP). Atomic: temp file → os.replace (mv).
## @io       ⇥ core_dir: platform core directory (absolute path, embedded in cron line)
##              cron_file: str | None = None (DI — путь cron-файла; None = CRON_METRICS_FILE)
##              lock_dir: str | None = None (DI — путь lock-dir; None = /run/lock)
##              write_fn: Callable[[str, str, int], None] | None = None (DI — open-fn шов записи;
##              None = shared/atomic_writer) → ⎋ bool: True = installed/verified, False = failure
##              (non-fatal — never raises)
## @complexity O(1) — single file read + atomic write
## @invariants
##   - CRON_METRICS_LINE contract: flock -n + timeout 50 + absolute path to platform-export-metrics.sh
##   - Idempotency: identical content → SKIP (no-op, mtime unchanged)
##   - Content mutation → overwrite (IMP:7 log)
##   - Fresh install → IMP:9 log «cron installed»
##   - mkdir lock_dir best-effort (tmpfs — exists on Ubuntu 24.04)
##   - Non-fatal: OSError (permission denied, read-only fs) → WARN + False — φ3 continues
##   - DI (W-H, DevPlan 163 + 167 D4): cron_file/lock_dir/write_fn — опциональные kwargs,
##     публичная сигнатура обратно-совместима (дефолты = прежнее поведение); тесты инжектят
##     tmp_path + fake-write вместо monkeypatch CRON_METRICS_FILE/os.makedirs/os.replace
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · install_cron_metrics FS-граница (write/lock-dir) инжектируема
## · Rejected: прямой вызов os.makedirs/_atomic_write_text (тест патчил CRON_METRICS_FILE +
## ·   os.makedirs + os.replace — 3 monkeypatch.setattr, включая глобальный os.replace)
## · Reason: seam = тестируемость реального вызова install_cron_metrics (cron_file= tmp_path,
## ·   lock_dir= tmp_path, write_fn= фейл-фейк для non-fatal контракта) — 0 патчей
## · Rev: при переходе на общий cron-инсталлер (cron_installer.py) — перенести швы туда
## @rationale  U-03: phases.py modulemap promised «metrics cron» in φ3 but no installer existed —
##             greenfield node ended up without metrics. Pattern: install-tor-proxy.sh:324
##             install_cron_healthcheck (/etc/cron.d/). Python-first language policy.
def install_cron_metrics(
    core_dir: str,
    cron_file: str | None = None,
    *,
    lock_dir: str | None = None,
    write_fn: Callable[[str, str, int], None] | None = None,
) -> bool:
    """Install the metrics cron entry. Returns True on success/no-op, False on failure."""
    target_file = CRON_METRICS_FILE if cron_file is None else cron_file
    try:
        cron_line = CRON_METRICS_LINE.format(core_dir=core_dir)
    except (KeyError, IndexError) as e:
        logger.warning("[IMP:7][cron_metrics] Invalid CRON_METRICS_LINE template (non-fatal): %s", e)
        return False

    try:
        _install_cron_metrics_body(cron_line, target_file, lock_dir=lock_dir, write_fn=write_fn)
    except OSError as e:
        logger.warning("[IMP:7][cron_metrics] Cron install failed (non-fatal): %s", e)
        return False
    else:
        return True


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


# region FUNC__install_cron_watchdog_body
## @purpose  Идемпотентная установка watchdog-cron записи (PLW0717 extraction из install_cron_watchdog).
## @io       ⇥ cron_line: str, target_file: str → ⎋ None ⚡ OSError (проброс в except)
## @complexity O(1) — read + atomic write
## @invariants — identical content → no-op; atomic_writer канон (E5); mkdir /run/lock best-effort
def _install_cron_watchdog_body(cron_line: str, target_file: str) -> None:
    """Write the watchdog cron entry atomically (no-op when content identical)."""
    # ── Idempotency: existing file with identical content → SKIP (no-op) ──
    try:
        with pathlib.Path(target_file).open(encoding="utf-8") as f:
            existing = f.read()
    except FileNotFoundError:
        existing = ""
    if existing == cron_line + "\n":
        logger.info("[IMP:7][cron_watchdog] Cron already installed — no-op (idempotent)")
        return

    # ── Ensure /run/lock exists (best-effort — tmpfs, present on Ubuntu) ──
    try:
        os.makedirs("/run/lock", exist_ok=True)
    except OSError as e:
        logger.warning("[IMP:7][cron_watchdog] mkdir /run/lock failed (best-effort): %s", e)

    # ── Atomic write: shared atomic_writer canon (E5 — temp + fsync + os.replace, 0644) ──
    _atomic_write_text(target_file, cron_line + "\n", mode=0o644)

    if existing:
        logger.info("[IMP:7][cron_watchdog] Cron file updated (content changed)")
    logger.info("[IMP:9][cron_watchdog] Watchdog cron installed at %s", target_file)


# endregion FUNC__install_cron_watchdog_body


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
##   - DI (W-H, DevPlan 163): cron_file: str | None = None — путь целевого cron-файла
##     (None = /etc/cron.d/platform-watchdog) — тесты инжектят tmp_path вместо патча константы
## @rationale  DevPlan 132 D1: host-cron по канону install_cron_metrics (точечное расширение,
##             без новой архитектуры); watchdog.py stdlib-only → cron без PYTHONPATH.
def install_cron_watchdog(core_dir: str, cron_file: str | None = None) -> bool:
    """Install the watchdog cron entry. Returns True on success/no-op, False on failure."""
    target_file = CRON_WATCHDOG_FILE if cron_file is None else cron_file
    try:
        cron_line = CRON_WATCHDOG_LINE.format(core_dir=core_dir)
    except (KeyError, IndexError) as e:
        logger.warning("[IMP:7][cron_watchdog] Invalid CRON_WATCHDOG_LINE template (non-fatal): %s", e)
        return False

    try:
        _install_cron_watchdog_body(cron_line, target_file)
    except OSError as e:
        logger.warning("[IMP:7][cron_watchdog] Cron install failed (non-fatal): %s", e)
        return False
    else:
        return True


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


# region FUNC__journald_persistent_active
## @purpose  Чистая функция: True если в содержимом journald.conf есть АКТИВНАЯ (не
##           закомментированная) строка `Storage=persistent`. Используется как идемпотентность-гейт.
## @io       ⇥ content: str → ⎋ bool
## @complexity O(N) — N строк конфига
## @invariants
##   - Комментированная строка (`#Storage=persistent` / `# Storage=persistent`) НЕ считается активной
##   - Активная `Storage=` с иным значением (например, `Storage=auto`) → False (нужна перезапись)
##   - Значение сравнивается по active-line: строка начинается (после пробелов) с `Storage=persistent`
##   ⚠️ TRAP[BUG] · 2026-08-05 · P1 · substring-проверка «Storage=persistent» ловила комментарии
##   · Symptom: journald.conf с закомментированной строкой `#Storage=persistent` → ensure_journald_persistent
##   ·   считал конфиг уже настроенным (no-op) → journald оставался volatile (D-1 не закрывался).
##   · Root: `"Storage=persistent" in content` — substring match не различает активную строку
##   ·   и комментарий (B-8, DevPlan 136 W9 T9.13).
##   · Fix: active-line проверка (не substring): строка без ведущих пробелов начинается с
##   ·   `Storage=persistent` И не начинается с '#'. (backticks — doxygen не резолвит `Storage`)
##   · Prevention: идемпотентность-гейты конфигов — active-line match, не substring (см. выше).
def _journald_persistent_active(content: str) -> bool:
    """Return True if an ACTIVE (non-commented) Storage=persistent line exists."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("Storage=") and stripped == "Storage=persistent":
            return True
    return False


# endregion FUNC__journald_persistent_active


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
##   - DI (W-H, DevPlan 163): conf: str | None = None — путь journald.conf
##     (None = /etc/systemd/journald.conf) — тесты инжектят tmp_path вместо патча константы
##   - DI (W-H, DevPlan 163): runner: CommandRunner | None = None — subprocess-канал рестарта
##     (None = default_command_runner); тесты передают fake-раннер вместо monkeypatch run_subprocess
## @rationale  D6 (DevPlan 132): при volatile-journal journald-скрейп не переживает reboot —
##             D-1 не закрывается без Storage=persistent.
def ensure_journald_persistent(
    conf: str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> bool:
    """Ensure systemd journald uses persistent storage. Returns True on success/no-op."""
    active = runner if runner is not None else default_command_runner()
    target_file = JOURNALD_CONF if conf is None else conf
    try:
        with pathlib.Path(target_file).open(encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning("[IMP:7][journald] Cannot read %s (non-fatal): %s", target_file, e)
        return False

    if _journald_persistent_active(content):
        logger.info("[IMP:7][journald] Storage=persistent already set (active line) — no-op (idempotent)")
        return True

    try:
        _atomic_write_text(target_file, _set_storage_persistent(content), mode=0o644)
    except OSError as e:
        logger.warning("[IMP:7][journald] Cannot write %s (non-fatal): %s", target_file, e)
        return False
    logger.info("[IMP:9][journald] Storage=persistent set in %s", target_file)

    # ── Restart journald (non-fatal — best-effort) ──
    try:
        active.run(["systemctl", "restart", "systemd-journald"], check=True, timeout=SYSTEM_CMD_TIMEOUT)
        logger.info("[IMP:9][journald] systemd-journald restarted")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][journald] systemd-journald restart failed (non-fatal): %s", e)

    return True


# endregion FUNC_ensure_journald_persistent


# ═══════════════════════════════════════════════════════════════════════════
# Shared content-match atomic writer (DevPlan 162) — _write_content_if_changed
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC__write_content_if_changed
## @purpose  Content-match idempotent atomic write: существующий файл с идентичным содержимым
##           → no-op (SKIP, mtime неизменен); иначе temp+fsync+os.replace (atomic_writer канон, 0644).
## @io       ⇥ path: str, desired: str → ⎋ bool (True = no-op/записано, False = OSError)
## @complexity O(N) — чтение + при необходимости запись (N = размер содержимого)
## @invariants
##   - Идемпотентность: идентичное содержимое → НИКОГДА не пишет на диск (канон install_cron_metrics)
##   - Атомарность: shared/atomic_writer (temp + fsync + os.replace)
##   - OSError (read-only /etc, нет прав) → WARN + False — вызывающий (фаза) продолжает
## @rationale  DevPlan 162: install_zram/install_cron_prune используют единый content-match
##             паттерн install_cron_metrics — вынесен в общий хелпер для новых функций
##             (существующие cron-инсталлеры не трогаются — покрыты gate-тестами).
def _write_content_if_changed(path: str, desired: str, mode: int = 0o644) -> bool:
    """Content-match idempotent atomic write. True = no-op or written, False = OSError."""
    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            existing = f.read()
    except FileNotFoundError:
        existing = ""
    if existing == desired:
        logger.info("[IMP:7][atomic] %s already up-to-date — no-op (idempotent)", path)
        return True
    try:
        _atomic_write_text(path, desired, mode=mode)
    except OSError as e:
        logger.warning("[IMP:7][atomic] Cannot write %s (non-fatal): %s", path, e)
        return False
    logger.info("[IMP:9][atomic] %s %s", path, "updated" if existing else "created")
    return True


# endregion FUNC__write_content_if_changed


# ═══════════════════════════════════════════════════════════════════════════
# zram swap (DevPlan 162 W4-1) — install_zram
# ═══════════════════════════════════════════════════════════════════════════

# zram-tools пакет + конфиги: 4G (50% RAM 7.8G), zstd, priority 100, vm.swappiness=100.
ZRAM_PACKAGE = "zram-tools"
ZRAM_DEFAULT_FILE = "/etc/default/zramswap"
ZRAM_SYSCTL_FILE = "/etc/sysctl.d/90-platform-zram.conf"
ZRAM_DEFAULT_LINES = (
    "# Generated by ai-platform install_zram (DevPlan 162 W4-1) — DO NOT EDIT MANUALLY\n"
    "ALGO=zstd\n"
    "SIZE=4096\n"
    "PRIORITY=100\n"
)
ZRAM_SYSCTL_LINES = (
    "# Generated by ai-platform install_zram (DevPlan 162 W4-1) — DO NOT EDIT MANUALLY\nvm.swappiness=100\n"
)


# region FUNC_install_zram
## @purpose  Установить zram swap (DevPlan 162 W4-1): zram-tools пакет + /etc/default/zramswap
##           (ALGO=zstd, SIZE=4096 — 50% RAM, PRIORITY=100) + vm.swappiness=100 (sysctl.d).
##           Идемпотентен (content-match no-op), атомарен (temp+mv, 0644), non-fatal.
## @io       ⎋ bool: True = настроено/no-op, False = failure (non-fatal — never raises)
## @complexity O(1) + apt-операции (install_apt_packages → _run_with_retry, W7-4)
## @invariants
##   - Пакет: install_apt_packages (is_pkg_installed gate, APT_TIMEOUT канон)
##   - Конфиги: content-match no-op + атомарная запись 0644 (atomic_writer канон)
##   - Non-fatal: PlatformError (apt) / OSError (запись) → WARN + False — φ1 продолжается
##   - OOM-риск (суммарные лимиты контейнеров ~10.5G > 7.8G RAM): zram 4G — буфер пиков LLM-трафика
##   - DI (W-H, DevPlan 163): default_file/sysctl_file: str | None = None — пути конфигов
##     (None = канонические /etc/...) — тесты инжектят tmp_path вместо патча констант
##   - DI (W-H, DevPlan 163): runner: CommandRunner | None = None — apt-канал
##     (None = default_command_runner); тесты передают fake-раннер вместо патча is_pkg_installed
## @rationale DevPlan 162 W4-1: swapon пуст, systemd-oomd не установлен — OOM-killer выбирает жертву
##            случайно (может убить postgres). zram-tools — Ubuntu-канон: modprobe zram + swap на boot.
def install_zram(
    default_file: str | None = None,
    sysctl_file: str | None = None,
    *,
    runner: CommandRunner | None = None,
) -> bool:
    """Install zram swap (4G, zstd, priority 100, swappiness=100). Returns True on success/no-op."""
    active = runner if runner is not None else default_command_runner()
    target_default = ZRAM_DEFAULT_FILE if default_file is None else default_file
    target_sysctl = ZRAM_SYSCTL_FILE if sysctl_file is None else sysctl_file
    try:
        if not is_pkg_installed(ZRAM_PACKAGE, runner=active):
            logger.info("[IMP:8][zram] Installing %s", ZRAM_PACKAGE)
            install_apt_packages([ZRAM_PACKAGE], runner=active)
            logger.info("[IMP:7][zram] %s already installed", ZRAM_PACKAGE)
    except PlatformError as e:
        logger.warning("[IMP:7][zram] %s install failed (non-fatal): %s", ZRAM_PACKAGE, e)
        return False

    ok_default = _write_content_if_changed(target_default, ZRAM_DEFAULT_LINES)
    ok_sysctl = _write_content_if_changed(target_sysctl, ZRAM_SYSCTL_LINES)
    if not (ok_default and ok_sysctl):
        logger.warning("[IMP:7][zram] zram config write failed (non-fatal)")
        return False
    logger.info("[IMP:9][zram] zram swap configured (4G, zstd, priority 100, swappiness=100)")
    return True


# endregion FUNC_install_zram


# ═══════════════════════════════════════════════════════════════════════════
# Prune cron (DevPlan 162 W4-4) — install_cron_prune
# ═══════════════════════════════════════════════════════════════════════════

# Cron.d target file — monthly docker system prune (без volumes, until=720h защищает свежие
# кэши L2-сборок) + apt-get clean. Absolute paths ONLY (cron.d minimal PATH).
CRON_PRUNE_FILE = "/etc/cron.d/platform-prune"
CRON_PRUNE_LINES = (
    "0 4 1 * * root /usr/bin/flock -n /run/lock/platform-prune.lock "
    "/usr/bin/docker system prune -af --filter until=720h >/dev/null 2>&1\n"
    "15 4 1 * * root /usr/bin/apt-get clean >/dev/null 2>&1\n"
)


# region FUNC_install_cron_prune
## @purpose  Установить ежемесячный prune cron в /etc/cron.d/platform-prune: docker system prune
##           (без volumes — контейнерные данные не трогаются) + apt-get clean. Идемпотентен
##           (content-match no-op), атомарен (temp+mv), non-fatal. Паттерн install_cron_metrics.
## @io       ⎋ bool: True = установлено/verified, False = failure (non-fatal — never raises)
## @complexity O(1) — чтение + атомарная запись
## @invariants
##   - CRON_PRUNE_LINES: flock -n + docker system prune -af --filter until=720h (monthly, день 1, 04:00)
##   - until=720h (30 дней) защищает свежие кэши L2-сборок hermes (build cache 1.6G, +4G/rebuild)
##   - volumes НЕ prune'ятся (--volumes=false семантика: -af не включает volumes)
##   - Idempotency: identical content → SKIP; content mutation → overwrite; fresh install → IMP:9
##   - Non-fatal: OSError → WARN + False — φ1 продолжается
##   - DI (W-H, DevPlan 163): cron_file: str | None = None — путь cron-файла
##     (None = /etc/cron.d/platform-prune) — тесты инжектят tmp_path вместо патча константы
## @rationale DevPlan 162 W4-4: нет prune-политики — build cache растёт ~4GB на каждый hermes L2
##            rebuild; apt-кэш не чистится. Monthly cron — компромисс retention vs автоматизация.
def install_cron_prune(cron_file: str | None = None) -> bool:
    """Install monthly docker/apt prune cron. Returns True on success/no-op, False on failure."""
    target_file = CRON_PRUNE_FILE if cron_file is None else cron_file
    ok = _write_content_if_changed(target_file, CRON_PRUNE_LINES)
    if ok:
        logger.info("[IMP:9][cron_prune] Prune cron installed at %s", target_file)
    return ok


# endregion FUNC_install_cron_prune


# ═══════════════════════════════════════════════════════════════════════════
# Cruft purge (DevPlan 162 W10-1) — purge_cruft
# ═══════════════════════════════════════════════════════════════════════════

# Консервативный список cruft базового образа провайдера (DevPlan 162 W10-1, аудит 2026-08-13).
# sysstat не в списке purg'а и НЕ устанавливается платформой (0 потребителей — решение оператора
# 164 Q5: baseline-замеры используют другие каналы); docker-buildx НЕ в списке — требуется для
# контекстных L2-сборок (hermes-build-context).
CRUFT_PURGE_PACKAGES = [
    "open-vm-tools",
    "multipath-tools",
    "open-iscsi",
    "bcache-tools",
    "apport",
    "update-manager",
    "landscape-common",
    "fwupd",
    "motd-news",
    "byobu",
    "tmux",
    "screen",
    "sosreport",
    "ubuntu-pro-client",
    "cloud-init",
]


# region FUNC_purge_cruft
## @purpose  Очистить cruft-пакеты базового образа (DevPlan 162 W10-1): apt-get purge
##           ТОЛЬКО установленных пакетов из CRUFT_PURGE_PACKAGES (dpkg-gate) + autoremove
##           (старые ядра) + clean. Идемпотентен, non-fatal, через _run_with_retry (W7-4).
## @io       ⎋ bool: True = очищено/no-op, False = failure (non-fatal — never raises)
## @complexity O(N * attempts) где N = установленные cruft-пакеты
## @invariants
##   - Purge только installed (is_pkg_installed gate) — отсутствующий пакет НЕ передаётся в apt
##     (apt-get purge несуществующего пакета → rc=100 — избегаем ложного WARN)
##   - autoremove -y убирает старые ядра (6.8.0-136 уйдёт сам после следующего обновления)
##   - Non-fatal: PlatformError (apt, после retry) → WARN + False — φ1 продолжается
##   - sysstat не устанавливается (164 Q5, 0 потребителей); docker-buildx НЕ в списке
##     (контекстные L2-сборки)
##   - DI (W-H, DevPlan 163): runner: CommandRunner | None = None — apt-канал
##     (None = default_command_runner); тесты передают fake-раннер вместо патча is_pkg_installed
## @rationale DevPlan 162 W10-1: ~1.2GB cruft базового образа (LLVM/bpftrace 240MB, open-vm-tools,
##            multipath/iscsi/bcache, apport, cloud-init пост-bootstrap, update-manager, landscape,
##            fwupd, motd-news, byobu/tmux/screen, sosreport, ubuntu-pro-client).
def purge_cruft(*, runner: CommandRunner | None = None) -> bool:
    """Purge installed cruft packages + autoremove + clean. Returns True on success/no-op."""
    active = runner if runner is not None else default_command_runner()
    installed = [pkg for pkg in CRUFT_PURGE_PACKAGES if is_pkg_installed(pkg, runner=active)]
    if not installed:
        logger.info("[IMP:7][purge] No cruft packages installed — no-op (idempotent)")
        return True
    logger.info("[IMP:8][purge] Purging %d cruft packages: %s", len(installed), " ".join(installed))
    try:
        _purge_cruft_apt(installed, runner=active)
        # ⚠️ TRAP[BUG] · 1.0.0 · HI · purge update-manager удаляет update-notifier-common
        # · (/usr/lib/update-notifier/apt-check) — инструмент S2 check-security (rc=127, «cannot
        # · assess»). Restore: update-notifier-common — маленький пакет, НЕ update-manager (cruft).
        if "update-manager" in installed:
            _run_with_retry(
                ["apt-get", "install", "-y", "-qq", "update-notifier-common"], timeout=APT_TIMEOUT, runner=active
            )
            logger.info("[IMP:9][purge] update-notifier-common restored (check-security S2 apt-check)")
    except PlatformError as e:
        logger.warning("[IMP:7][purge] Cruft purge failed (non-fatal): %s", e)
        return False
    logger.info("[IMP:9][purge] Cruft purged (%d packages) + autoremove + clean", len(installed))
    return True


# region FUNC__purge_cruft_apt
def _purge_cruft_apt(installed: list[str], *, runner: CommandRunner | None = None) -> None:
    """Три apt-шага purge-цепочки (выделено из try — PYTHON-too-many-statements)."""
    active = runner if runner is not None else default_command_runner()
    _run_with_retry(["apt-get", "purge", "-y", "--auto-remove", *installed], timeout=APT_TIMEOUT, runner=active)
    _run_with_retry(["apt-get", "autoremove", "-y"], timeout=APT_TIMEOUT, runner=active)
    _run_with_retry(["apt-get", "clean"], timeout=APT_TIMEOUT, runner=active)


# endregion FUNC__purge_cruft_apt


# endregion FUNC_purge_cruft


# ═══════════════════════════════════════════════════════════════════════════
# Provider apt-repo purge (DevPlan 164 W0-3.2, ex-162 W2-6) — purge_provider_repos
# ═══════════════════════════════════════════════════════════════════════════

# Репо провайдера на базовом образе Timeweb: timeweb-mirror.list (trusted=yes — unsigned),
# timeweb-zabbix.list (focal на noble). Ноды пересоздаются с нуля — платформенные apt-источники
# (Ubuntu archive + deadsnakes PPA) достаточны, репо провайдера удаляются прямым замещением.
PROVIDER_REPO_PREFIXES: tuple[str, ...] = ("timeweb-",)
APT_SOURCES_DIR = "/etc/apt/sources.list.d"


# region FUNC_purge_provider_repos
## @purpose  Удалить apt-репо провайдера из /etc/apt/sources.list.d/ (файлы с префиксом из
##           PROVIDER_REPO_PREFIXES) и выполнить apt-get update (источник исчезает из индексов).
##           Идемпотентен (0 совпадений → no-op), non-fatal.
## @io       ⇥ sources_dir: str | None = None (None = /etc/apt/sources.list.d),
##           runner: CommandRunner | None = None (DI, W-H DevPlan 163) →
##           ⎋ bool: True = очищено/no-op, False = failure (non-fatal — never raises)
## @complexity O(F + U) — F = файлов в sources.list.d, U = apt-get update
## @invariants
##   - Удаляются ТОЛЬКО файлы с префиксом из PROVIDER_REPO_PREFIXES (timeweb-*); сторонние
##     sources (.list/.sources) не трогаются
##   - apt-get update выполняется ТОЛЬКО после реального удаления (иначе no-op)
##   - Non-fatal: OSError/PlatformError → WARN + False — φ1 продолжается
##   - Идемпотентность: повторный вызов на чистом дереве = no-op True
## @rationale DevPlan 162 W2-6: timeweb-mirror trusted=yes (unsigned) + timeweb-zabbix
##            focal-на-noble на базовых образах провайдера; платформа от этих репо не зависит —
##            прямое замещение удалением при bootstrap на голой ОС.
def purge_provider_repos(*, sources_dir: str | None = None, runner: CommandRunner | None = None) -> bool:
    """Remove provider apt repos (timeweb-*) + apt-get update. Returns True on success/no-op."""
    active = runner if runner is not None else default_command_runner()
    target_dir = APT_SOURCES_DIR if sources_dir is None else sources_dir
    removed = 0
    try:
        entries = sorted(os.scandir(target_dir), key=lambda e: e.name) if pathlib.Path(target_dir).is_dir() else []
    except OSError as e:
        logger.warning("[IMP:7][provider-repos] Cannot list %s (non-fatal): %s", target_dir, e)
        return False
    for entry in entries:
        if not entry.name.startswith(PROVIDER_REPO_PREFIXES):
            continue
        try:
            pathlib.Path(entry.path).unlink()
            removed += 1
            logger.info("[IMP:8][provider-repos] Removed provider repo: %s", entry.name)
        except OSError as e:
            logger.warning("[IMP:7][provider-repos] Cannot remove %s (non-fatal): %s", entry.name, e)
            return False
    if removed == 0:
        logger.info("[IMP:7][provider-repos] No provider repos found — no-op (idempotent)")
        return True
    try:
        _run_with_retry(["apt-get", "update"], timeout=APT_TIMEOUT, runner=active)
    except PlatformError as e:
        logger.warning("[IMP:7][provider-repos] apt-get update failed after purge (non-fatal): %s", e)
        return False
    logger.info("[IMP:9][provider-repos] Provider repos purged (%d files) + apt indexes refreshed", removed)
    return True


# endregion FUNC_purge_provider_repos


# ═══════════════════════════════════════════════════════════════════════════
# fstab policy (DevPlan 164 W0-3.4, ex-162 W4-2) — ensure_fstab_policy
# ═══════════════════════════════════════════════════════════════════════════

FSTAB_PATH = "/etc/fstab"
# Filesystems, которым применяется канон `defaults` (relatime, commit=5 — НЕ nobarrier).
# swap-строки и спец-монтирования (proc/sysfs/tmpfs) не трогаются.
_FSTAB_FIELDS_MIN: int = 6  # fstab-строка: device mount type opts freq pass
FSTAB_DATA_TYPES: tuple[str, ...] = ("ext4", "xfs")


# region FUNC_normalize_fstab_lines
## @purpose  Чистая функция нормализации /etc/fstab: data-строки (ext4/xfs, не /boot) →
##           опции `defaults`. /boot не трогаем (grub/mkinitramfs-совместимость).
## @io       ⇥ text: str → ⎋ tuple[str, bool] — (новый текст, был ли изменён)
## @complexity O(L) — L = строк fstab
## @invariants
##   - Комментарии/пустые строки сохраняются без изменений
##   - Опции заменяются только если != "defaults" (идемпотентность)
##   - swap-строки (fstype swap) НЕ нормализуются
##   - Строка с невалидным числом полей (<6) сохраняется как есть (fail-safe)
def normalize_fstab_lines(text: str) -> tuple[str, bool]:
    """Normalize data-mount options to `defaults`. Returns (new_text, changed)."""
    changed = False
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out_lines.append(line)
            continue
        fields = line.split()
        if len(fields) < _FSTAB_FIELDS_MIN or fields[2] not in FSTAB_DATA_TYPES:
            out_lines.append(line)
            continue
        mountpoint, options = fields[1], fields[3]
        if mountpoint == "/boot":
            out_lines.append(line)
            continue
        if options == "defaults":
            out_lines.append(line)
            continue
        fields[3] = "defaults"
        out_lines.append(" ".join(fields))
        changed = True
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else ""), changed


# endregion FUNC_normalize_fstab_lines


# region FUNC_ensure_fstab_policy
## @purpose  Применить fstab-политику (162 W4-2): data-строки → `defaults` (атомарная запись
##           при изменении) + включить fstrim.timer (weekly TRIM, Ubuntu default). Non-fatal.
## @io       ⇥ fstab_path: str | None = None (None = /etc/fstab),
##           runner: CommandRunner | None = None (DI, W-H DevPlan 163) →
##           ⎋ bool: True = применено/no-op, False = failure (non-fatal — never raises)
## @complexity O(L + T) — L = строк fstab, T = systemctl enable
## @invariants
##   - Запись только при реальном изменении (content-match no-op)
##   - fstrim.timer enable — идемпотентен (systemctl enable повторно = no-op), non-fatal
##   - Ошибки чтения/записи fstab → WARN + False; bootstrap продолжается
## @rationale DevPlan 162 W4-2: fstab с неканоническими опциями (nobarrier) на нодах провайдера;
##            канон — `defaults` (relatime, commit=5). Weekly fstrim поддерживает SSD-disk.
##            До/после замеры (iostat/pg_test_fsync) — на новой ноде (план 165).
def ensure_fstab_policy(*, fstab_path: str | None = None, runner: CommandRunner | None = None) -> bool:
    """Normalize fstab data mounts to defaults + enable weekly fstrim. Returns True on success/no-op."""
    active = runner if runner is not None else default_command_runner()
    target = FSTAB_PATH if fstab_path is None else fstab_path
    try:
        with pathlib.Path(target).open(encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        logger.warning("[IMP:7][fstab] Cannot read %s (non-fatal): %s", target, e)
        return False
    new_text, changed = normalize_fstab_lines(text)
    if changed:
        try:
            _atomic_write_text(target, new_text)
        except OSError as e:
            logger.warning("[IMP:7][fstab] Cannot write %s (non-fatal): %s", target, e)
            return False
        logger.info("[IMP:9][fstab] fstab normalized: data mounts → defaults")
    else:
        logger.info("[IMP:7][fstab] fstab already canonical — no-op (idempotent)")
    try:
        active.run(["systemctl", "enable", "--now", "fstrim.timer"], timeout=SYSTEM_CMD_TIMEOUT, check=False)
        logger.info("[IMP:9][fstab] fstrim.timer enabled (weekly TRIM)")
    except (PlatformError, FileNotFoundError) as e:
        logger.warning("[IMP:7][fstab] fstrim.timer enable failed (non-fatal): %s", e)
        return False
    return True


# endregion FUNC_ensure_fstab_policy
