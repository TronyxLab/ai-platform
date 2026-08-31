#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S1 S2 apt unattended-upgrades security-updates apt-check apt-get dist-upgrade fallback dpkg AUTO_UPDATES_FILE UNATTENDED_FILE
# STRUCTURE: ▶ S1: dpkg -s unattended-upgrades + read 20auto-upgrades/50unattended-upgrades → ◇ FAIL/WARN/PASS → ⎋ CheckResult ┤
#            ○ S2: apt-check --human-readable → ◇ rc==0? regex (total|security) ┤ fallback apt-get -s dist-upgrade (^Inst / -security)
#            ○ security>0 → WARN / PASS → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  S1/S2 apt-сектор security-постуры ноды (DevPlan 134 L2): автопатчинг
##           unattended-upgrades (S1 — пакет + 20auto-upgrades + 50unattended-upgrades security-only)
##           и pending security-апдейты (S2 — update-notifier apt-check с fallback
##           `apt-get -s dist-upgrade` для Ubuntu 24.04). Извлечено из монолита
##           security_posture.py (план 170 W6-D1).
## @scope    Вызывается run_all_checks (run.py) и напрямую (DI-тесты). Импортирует только
##           _shared (CheckResult/STATUS/_probe) и shared/timeouts — циклических зависимостей нет.
## @invariants
##   - S1: FAIL = автопатчинг сломан/отсутствует (пакет, Unattended-Upgrade "1", security-ориджины);
##     директивы сверяются с каноном security_updates.py (DevPlan 134 W1) — дрейф = FAIL
##   - S2: >0 security → WARN (норма между daily-кронами, unattended-upgrades применит);
##     apt-check недоступен (Ubuntu 24.04) → fallback apt-get -s dist-upgrade (^Inst);
##     недоступны ОБА источника → WARN (graceful, cannot assess ≠ небезопасно)
##   - subprocess через _probe (check=False, graceful); таймауты из shared/timeouts
##     (CONVERGE_DOCKER_TIMEOUT для dpkg, APT_TIMEOUT для apt-check/apt-get -s — W1-A1 план 170)
## @rationale Разделение по бизнес-домену (AI-First, архит. принцип 8): apt-политика —
##            отдельный модуль от sshd/docker/perms (разные SoT, разные частоты дрейфа).
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S1/S2, 1:1 тела)
## @changes 2026-09-01 | S2 fallback — Ubuntu 24.04 удалил /usr/lib/update-notifier/apt-check
##            (проверено на живой ноде rc=127): источник — apt-get -s dist-upgrade dry-run
##            (^Inst total / -security); недоступны оба → WARN; классификация security>0 → WARN сохранена
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-09-01 · — · S2 fallback-источник: apt-get -s dist-upgrade (dry-run ^Inst/-security)
# · Rejected: /usr/lib/ubuntu-advantage/apt-esm-hook (только с UA/ESM-подпиской, на bare ноде отсутствует),
# ·            /var/lib/update-notifier/updates-available (legacy, стагнирует без update-notifier)
# · Reason: apt-get -s — каноничный apt-путь, есть на любой Debian/Ubuntu, не требует UA
# · Rev: если update-notifier вернёт официальный successor apt-check — вернуть первичным

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
# S2 fallback (Ubuntu 24.04): apt-check удалён → apt-get -s dist-upgrade dry-run (счётчик ^Inst).
APT_GET_SIM_CMD = ["apt-get", "-s", "dist-upgrade"]

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


# region FUNC__evaluate_pending
## @purpose  Общая классификация S2 для ОБОИХ источников (apt-check и apt-get -s dist-upgrade):
##           security>0 → WARN (норма между daily-кронами, unattended-upgrades применит; алерт
##           оператору), иначе PASS. Единая политика сохранена (прежняя семантика).
## @io       ⇥ total: int, security: int → ⎋ CheckResult
## @complexity O(1)
def _evaluate_pending(total: int, security: int) -> CheckResult:
    if security > 0:
        logger.info("[IMP:8][posture][S2] %d security updates pending of %d total", security, total)
        return CheckResult("S2", STATUS_WARN, f"{security} security updates pending (of {total} total)")
    logger.info("[IMP:9][posture][S2] No pending security updates")
    return CheckResult("S2", STATUS_PASS, f"no pending security updates (total pending: {total})")


# endregion FUNC__evaluate_pending


# region FUNC__count_inst_lines
## @purpose  Счётчик обновлений из dry-run `apt-get -s dist-upgrade`: total = строки `^Inst`,
##           security = подмножество, чей origin содержит `-security` (noble-security/jammy-security).
## @io       ⇥ output: str → ⎋ (total: int, security: int)
## @complexity O(L) — построчный скан
def _count_inst_lines(output: str) -> tuple[int, int]:
    total = 0
    security = 0
    for line in output.splitlines():
        if line.startswith("Inst "):
            total += 1
            if "-security" in line:
                security += 1
    return total, security


# endregion FUNC__count_inst_lines


# region FUNC_check_pending_security_updates
## @purpose  S2: pending security-апдейты. Первичный источник — update-notifier apt-check
##           (Ubuntu ≤22.04). Ubuntu 24.04 удалил /usr/lib/update-notifier/apt-check (rc=127,
##           проверено на живой ноде) → fallback: `apt-get -s dist-upgrade` dry-run (счётчик
##           ^Inst / origin -security). >0 security → WARN. Недоступны ОБА источника → WARN
##           (graceful: оценка недоступна ≠ небезопасно — НЕ FAIL).
## @io       ⇥ probe: Callable | None (lazy default _probe) → ⎋ CheckResult
## @complexity O(1) — ≤2 subprocess
def check_pending_security_updates(
    *, probe: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> CheckResult:
    """S2: pending security updates (apt-check → fallback apt-get -s dist-upgrade)."""
    probe = probe or _probe
    result = probe([APT_CHECK_BIN, "--human-readable"], timeout=APT_TIMEOUT)
    if result.returncode == 0:
        output = str(getattr(result, "stdout", ""))
        total_m = _APT_CHECK_RE.search(output)
        sec_m = _APT_CHECK_SEC_RE.search(output)
        total = int(total_m.group(1)) if total_m else 0
        security = int(sec_m.group(1)) if sec_m else 0
        return _evaluate_pending(total, security)

    # apt-check недоступен (Ubuntu 24.04: /usr/lib/update-notifier/apt-check удалён) →
    # каноничный современный источник: apt-get -s dist-upgrade dry-run (счётчик ^Inst).
    logger.info(
        "[IMP:8][posture][S2] apt-check unavailable (rc=%s) — falling back to apt-get -s dist-upgrade",
        result.returncode,
    )
    sim = probe(APT_GET_SIM_CMD, timeout=APT_TIMEOUT)
    if sim.returncode != 0:
        return CheckResult(
            "S2",
            STATUS_WARN,
            f"apt-check (rc={result.returncode}) and apt-get -s dist-upgrade (rc={sim.returncode}) unavailable — cannot assess",
        )
    total, security = _count_inst_lines(str(getattr(sim, "stdout", "")))
    return _evaluate_pending(total, security)


# endregion FUNC_check_pending_security_updates
