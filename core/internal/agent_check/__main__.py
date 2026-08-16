# GREP_SUMMARY: agent-check, __main__, python-m, cli-entrypoint
# STRUCTURE: ▶ main() ← core.internal.agent_check → ⎋ sys.exit(rc)
# region MODULE_CONTRACT
## @purpose  Точка входа `python3 -m core.internal.agent_check` (DevPlan 170 W10-C):
##           Python 3.11+ требует `__main__.py` для исполнения пакета через -m
##           (логика CLI живёт в __init__.py — main/run/_human_report).
## @scope    core/internal/agent_check/__main__.py — тонкий делегат, без логики.
## @invariants
##   - Exit code main() пробрасывается в sys.exit без изменений
## @rationale makefiles/dev.mk (agent-check target) и core/entrypoint-manifest.yaml вызывают
##            `python -m core.internal.agent_check` — контракт CLI сохраняется 1:1 при
##            переносе модуль → пакет (170 W10-C; паритет check_suite/__main__.py, W3).
## @changes 170 W10-C — agent_check.py (модуль) → agent_check/__init__.py (пакет) + __main__.py
# endregion MODULE_CONTRACT

import sys

from core.internal.agent_check import main

if __name__ == "__main__":
    sys.exit(main())
