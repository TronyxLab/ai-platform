#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S1 S2 apt unattended-upgrades security-updates apt-check dpkg AUTO_UPDATES_FILE UNATTENDED_FILE
# STRUCTURE: ▶ S1: dpkg -s unattended-upgrades + read 20auto-upgrades/50unattended-upgrades → ◇ FAIL/WARN/PASS → ⎋ CheckResult ┤
#            ○ S2: apt-check --human-readable → ◇ regex (total|security) → ○ security>0 → WARN / PASS → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  S1/S2 apt-сектор security-постуры ноды (DevPlan 134 L2): автопатчинг
##           unattended-upgrades (S1 — пакет + 20auto-upgrades + 50unattended-upgrades security-only)
##           и pending security-апдейты через update-notifier (S2). Извлечено из монолита
##           security_posture.py (план 170 W6-D1).
## @scope    Вызывается run_all_checks (run.py) и напрямую (DI-тесты). Импортирует только
##           _shared (CheckResult/STATUS/_probe) и shared/timeouts — циклических зависимостей нет.
## @invariants
##   - S1: FAIL = автопатчинг сломан/отсутствует (пакет, Unattended-Upgrade "1", security-ориджины);
##     директивы сверяются с каноном security_updates.py (DevPlan 134 W1) — дрейф = FAIL
##   - S2: >0 security → WARN (норма между daily-кронами, unattended-upgrades применит);
##     недоступность apt-check → WARN (graceful, cannot assess)
##   - subprocess через _probe (check=False, graceful); таймауты из shared/timeouts
##     (CONVERGE_DOCKER_TIMEOUT для dpkg, APT_TIMEOUT для apt-check — W1-A1 план 170)
## @rationale Разделение по бизнес-домену (AI-First, архит. принцип 8): apt-политика —
##            отдельный модуль от sshd/docker/perms (разные SoT, разные частоты дрейфа).
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S1/S2, 1:1 тела)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from core.internal.shared.timeouts import APT_TIMEOUT, CONVERGE_DOCKER_TIMEOUT

from ._shared import STATUS_FAIL, STATUS_PASS, STATUS_WARN, CheckResult
from ._shared import probe as _probe

logger = logging.getLogger(__name__)

# Пути — модульные константы (тесты переопределяют через paths= DI-параметр)
AUTO_UPDATES_FILE = "/etc/apt/apt.conf.d/20auto-upgrades"
UNATTENDED_FILE = "/etc/apt/apt.conf.d/50unattended-upgrades"
APT_CHECK_BIN = "/usr/lib/update-notifier/apt-check"

_APT_CHECK_RE = re.compile(r"(\d+)\s+updates can be applied immediately")
_APT_CHECK_SEC_RE = re.compile(r"(\d+)\s+of these updates are security updates")


# region FUNC_check_unattended_upgrades
## @purpose  S1: unattended-upgrades активен — пакет + 20auto-upgrades (Unattended-Upgrade "1")
##           + 50unattended-upgrades (security-only origins). FAIL = автопатчинг сломан/отсутствует.
## @io       ⇥ probe: Callable | None (lazy default _probe), paths: Mapping | None
##              (override AUTO_UPDATES_FILE/UNATTENDED_FILE — E3 DI) → ⎋ CheckResult
## @complexity O(1) — dpkg probe + 2 file reads
## @invariants  Директивы сверяются с каноном security_updates.py (DevPlan 134 W1) — дрейф = FAIL
def check_unattended_upgrades(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
) -> CheckResult:
    """S1: unattended-upgrades policy active (package + both config files)."""
    probe = probe or _probe
    paths_ = paths or {}
    auto_file_str = paths_.get("AUTO_UPDATES_FILE") or AUTO_UPDATES_FILE
    unattended_file_str = paths_.get("UNATTENDED_FILE") or UNATTENDED_FILE
    pkg = probe(["dpkg", "-s", "unattended-upgrades"], timeout=CONVERGE_DOCKER_TIMEOUT)
    if pkg.returncode != 0:
        return CheckResult("S1", STATUS_FAIL, "unattended-upgrades package NOT installed")
    auto = Path(auto_file_str)
    unattended = Path(unattended_file_str)
    problems: list[str] = []
    if not auto.is_file() or 'APT::Periodic::Unattended-Upgrade "1"' not in auto.read_text(encoding="utf-8"):
        problems.append(f"{auto_file_str} missing or Unattended-Upgrade disabled")
    if not unattended.is_file() or "-security" not in unattended.read_text(encoding="utf-8"):
        problems.append(f"{unattended_file_str} missing or no security origins")
    if problems:
        return CheckResult("S1", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S1] Unattended-upgrades active")
    return CheckResult("S1", STATUS_PASS, "unattended-upgrades active (security-only origins)")


# endregion FUNC_check_unattended_upgrades


# region FUNC_check_pending_security_updates
## @purpose  S2: pending security-апдейты через update-notifier apt-check. >0 → WARN (норма между
##           daily-кронами, unattended-upgrades применит; алерт оператору). Недоступность apt-check → WARN.
## @io       ⇥ probe: Callable | None (lazy default _probe) → ⎋ CheckResult
## @complexity O(1) — один subprocess
def check_pending_security_updates(
    *, probe: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> CheckResult:
    """S2: pending security updates (update-notifier apt-check --human-readable)."""
    probe = probe or _probe
    result = probe([APT_CHECK_BIN, "--human-readable"], timeout=APT_TIMEOUT)
    if result.returncode != 0:
        return CheckResult("S2", STATUS_WARN, f"apt-check unavailable (rc={result.returncode}) — cannot assess")
    output = str(getattr(result, "stdout", ""))
    total_m = _APT_CHECK_RE.search(output)
    sec_m = _APT_CHECK_SEC_RE.search(output)
    total = int(total_m.group(1)) if total_m else 0
    security = int(sec_m.group(1)) if sec_m else 0
    if security > 0:
        logger.info("[IMP:8][posture][S2] %d security updates pending of %d total", security, total)
        return CheckResult("S2", STATUS_WARN, f"{security} security updates pending (of {total} total)")
    logger.info("[IMP:9][posture][S2] No pending security updates")
    return CheckResult("S2", STATUS_PASS, f"no pending security updates (total pending: {total})")


# endregion FUNC_check_pending_security_updates
