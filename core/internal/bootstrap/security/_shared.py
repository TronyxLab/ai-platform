#!/usr/bin/env python3
# GREP_SUMMARY: security-posture shared CheckResult STATUS PASS WARN FAIL probe runner subprocess graceful DI-каналы
# STRUCTURE: ▶ CheckResult(frozen) ⊕ STATUS_PASS/WARN/FAIL → ⚡ probe(cmd, timeout) → io.run_subprocess → ⎋ CompletedProcess (never raise)
# region MODULE_CONTRACT
## @purpose  Приватная общая база пакета core/internal/bootstrap/security (W6-D1, план 170):
##           единые CheckResult/STATUS-константы и graceful subprocess-раннер probe —
##           общие для всех check-модулей (apt_security/sshd_policy/docker_posture/fs_perms/
##           deploy_channel_posture/run). Вынесение в _shared исключает циклы импорта
##           (run.py ↔ check-модули) и дублирование 6 копий probe.
## @scope    Приватный модуль пакета (внешний импорт — только через core.internal.bootstrap.
##           security_posture / security). Публичные сущности: CheckResult, STATUS_*,
##           probe, AUDIT_LIST_MAX, AUDIT_DIR_LIST_MAX, SSHD_PARTS_MIN (U-07: приватные
##           имена переименованы в публичные — план 170 private-imports-фикс; импортёры
##           используют `probe as _probe` из-за конфликта с DI-параметром probe).
## @invariants
##   - CheckResult — frozen dataclass (id, status, message); status ∈ {PASS, WARN, FAIL}
##   - probe делегирует shared/subprocess_io.run_subprocess (канон B4/C10, check=False) —
##     НИКОГДА не raise (graceful; rc 0/124/127/иное)
##   - Лимиты отображения списков проблем (world-writable/secrets-аудит) — единые для S6
## @rationale DI-HYG (план 170 W6): probe — lazy-default DI-канал probe-параметров всех
##            check_* (0 monkeypatch модульных функций в тестах; E3 DevPlan 160).
##            Отдельный модуль (не __init__.py) — определение ДО re-export-импортов
##            исключает частичную инициализацию пакета при круговом доступе.
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (бывший монолит 1131 LOC)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from core.internal.shared import subprocess_io as io

logger = logging.getLogger(__name__)

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"


# region DATACLS_CheckResult
@dataclass(frozen=True)
class CheckResult:
    """Результат одной проверки S1-S9 (общий контракт всех check_* модулей)."""

    check_id: str
    status: str  # PASS | WARN | FAIL
    message: str


# endregion DATACLS_CheckResult


# region FUNC_probe
## @purpose  Graceful subprocess probe: run_subprocess (check=False, канон B4/C10).
## @io       ⇥ cmd: list[str], timeout: int → ⎋ CompletedProcess (rc 0/124/127/иное, никогда не raise)
## @complexity O(1) — делегирование
def probe(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a subprocess gracefully (never raises)."""
    return io.run_subprocess(cmd, timeout=timeout)


# endregion FUNC_probe


# Лимиты отображения списков проблем в сообщениях (world-writable/secrets-аудит) — не весь список.
AUDIT_LIST_MAX: int = 5
AUDIT_DIR_LIST_MAX: int = 4
# S4: минимальное число полей строки sshd-конфига (key value [rest])
SSHD_PARTS_MIN: int = 2
