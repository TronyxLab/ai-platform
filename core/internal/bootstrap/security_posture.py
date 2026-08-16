#!/usr/bin/env python3
# GREP_SUMMARY: security-posture facade re-export backward-compat S1-S9 run_all_checks render_report main check_* DevPlan-134
# STRUCTURE: ▶ import core.internal.bootstrap.security (пакет) → ⊕ flat re-export через __all__ → ◇ __main__ → sys.exit(main()) → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Фасад-модуль (backward-compat, план 170 W6-D1): прямое замещение монолита
##           security_posture.py (1131 LOC) пакетом core/internal/bootstrap/security/.
##           Импорт-путь `from core.internal.bootstrap.security_posture import ...` и скриптовый
##           вызов `python3 security_posture.py` СОХРАНЯЮТСЯ (flat re-export через __all__ пакета).
## @scope    Тесты (security_posture.X), check-security.sh (exec по пути), remote_executor/
##           ssh_cmd_builder (путь), lifecycle cli.py + phases/system.py (--apply-sshd),
##           python3 -m core.internal.bootstrap.security_posture.
## @invariants  Только re-export (логика в пакете; 0 дублирования); __main__ сохраняет
##              скриптовый контракт exit 0|1|2; <30 LOC (старый контент удалён)
## @rationale  Единственная точка истины — пакет security/; фасад — дешёвая совместимость
##             импорт-путей тестов + shell-фасадов по пути файла.
## @changes 2026-08-15 | план 170 W6-D1 — монолит → фасад + пакет security/
# endregion MODULE_CONTRACT

from __future__ import annotations

import sys

from core.internal.bootstrap.security import *  # ruff: ignore[F403] — flat re-export через __all__ пакета (re-export-хаб)
from core.internal.bootstrap.security import main

if __name__ == "__main__":
    sys.exit(main())
