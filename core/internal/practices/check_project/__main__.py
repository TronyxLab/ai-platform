# GREP_SUMMARY: check-project-main, __main__, CLI, python -m, entrypoint
# STRUCTURE: ┌__main__.py┐ → ⊕ cli.main(argv) → ⎋ sys.exit(main()) — `python3 -m core.internal.practices.check_project` (пакет)
# region MODULE_CONTRACT
## @purpose  CLI-вход пакета (DevPlan 170 W10-A): `python3 -m core.internal.practices.
##           check_project` резолвится в ПАКЕТ (check_project/ __init__.py приоритетнее
##           фасада-файла) — __main__.py обеспечивает запуск CLI. Прямое исполнение файла
##           (`python check_project.py`) обрабатывает __main__-блок фасада.
## @io       ⇥ argv (sys.argv[1:]) → ⎋ exit 0|1|4 (CLI-контракт project-check/project-fix)
## @complexity O(1)
# endregion MODULE_CONTRACT

import sys

from core.internal.practices.check_project.cli import main

if __name__ == "__main__":
    sys.exit(main())
