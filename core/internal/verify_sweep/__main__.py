# GREP_SUMMARY: verify-sweep, __main__, python-m, cli-entrypoint, sweep
# STRUCTURE: ▶ main() ← core.internal.verify_sweep → ⎋ sys.exit(rc)
# region MODULE_CONTRACT
## @purpose  Точка входа `python3 -m core.internal.verify_sweep` (DevPlan 170 W7-E1): Python 3.11+
##           требует `__main__.py` для исполнения пакета через -m (логика CLI живёт в
##           __init__.py — main/_build_parser).
## @scope    core/internal/verify_sweep/__main__.py — тонкий делегат, без логики.
## @invariants
##   - Exit code main() пробрасывается в sys.exit без изменений (0/1/2 exit-контракт)
## @rationale makefiles/ci.mk:63 и core/entrypoint-manifest.yaml:215 вызывают
##            `python -m core.internal.verify_sweep sweep` — контракт CLI сохраняется 1:1
##            при переходе файл → пакет (research-A §7, W7-E1).
# 🧐 TRAP[DECISION] · 2026-08-15 · — · python -m требует __main__.py на Python 3.11+
# · Rejected: «пакет с __main__-логикой только в __init__.py + if __name__ == "__main__"»
# ·   (формулировка research-A §7 — «__init__ re-export + __main__»)
# · Reason: Python 3.11+ (проверено на 3.14.6) НЕ исполняет пакет через -m без __main__.py:
# ·   «No module named X.__main__; 'X' is a package and cannot be directly executed»;
# ·   CLI/логика остались в __init__.py (main/_build_parser + if __name__ == "__main__"
# ·   для прямого запуска файла), __main__.py — тонкий делегат (паритет check_suite W3)
# · Rev: если Python вернёт исполнение пакета через -m с логикой в __init__ — удалить __main__.py
## @changes 2026-08-15 | План 170 W7-E1 — verify_sweep.py (монолит 1284 LOC) → пакет
# endregion MODULE_CONTRACT

import sys

from core.internal.verify_sweep import main

if __name__ == "__main__":
    sys.exit(main())
