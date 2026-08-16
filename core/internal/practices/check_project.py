#!/usr/bin/env python3
# GREP_SUMMARY: check_project, K1-local, project-check, project-fix, facade, re-export, CLI, exit-codes
# STRUCTURE: ▶ re-export из пакета check_project/ (runner+cli+models+exec) → ⊕ __main__-блок → ⎋ CLI-контракт сохранён (python3 -m core.internal.practices.check_project)
# region MODULE_CONTRACT
## @purpose  Фасад пакета core/internal/practices/check_project/ (DevPlan 170 W10-A):
##           монолит check_project.py (1389 LOC) декомпозирован в пакет; имя файла сохранено —
##           makefiles/project-practices.mk:21-22 + entrypoint-manifest.yaml:291/300
##           (delegates_to: python3 -m ...check_project) запрещают переименование.
## @invariants  API: check_project/CheckReport/CheckResult/format_report/main/_run_check;
##              `python3 -m ...check_project` → пакет __main__.py (пакет приоритетнее файла);
##              прямое `python .../check_project.py` → этот __main__-блок. Логика — в пакете.
## @rationale  Имя файла = CLI-контракт (research-A §2); re-export — обратная совместимость.
## @changes  2026-08-15 · DevPlan 170 W10-A — монолит (1389 LOC) заменён фасадом
## ⚠️ TRAP[DECISION] · — · Фасад сохранён (177 W4 S9): имя файла = CLI-контракт
##   (makefiles/project-practices.mk + entrypoint-manifest delegates_to `python3 -m ...check_project`
##   запрещают переименование); ре-дизайн CLI (одна точка входа в пакете) не даёт
##   пользовательской ценности при живом контракте. · Rev: при смене CLI-контракта
##   project-check (новый verb/путь) — удалить фасад и мигрировать вызовы.
# endregion MODULE_CONTRACT

from core.internal.practices.check_project import (  # ruff: ignore[F401] — re-export API
    CheckReport,
    CheckResult,
    check_project,
    format_report,
    main,
)
from core.internal.practices.check_project.exec import run_check as _run_check  # ruff: ignore[F401]

if __name__ == "__main__":
    import sys

    sys.exit(main())
