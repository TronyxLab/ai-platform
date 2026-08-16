# GREP_SUMMARY: check-suite, __main__, python-m, cli-entrypoint
# STRUCTURE: ▶ main() ← core.internal.check_suite → ⎋ sys.exit(rc)
# region MODULE_CONTRACT
## @purpose  Точка входа `python3 -m core.internal.check_suite` (DevPlan 170 W3): Python 3.11+
##           требует `__main__.py` для исполнения пакета через -m (логика CLI живёт в
##           __init__.py — main/_cmd_run/_cmd_list/_cmd_fingerprint).
## @scope    core/internal/check_suite/__main__.py — тонкий делегат, без логики.
## @invariants
##   - Exit code main() пробрасывается в sys.exit без изменений
## @rationale makefiles/repair.mk, ci.mk и core/entrypoint-manifest.yaml вызывают
##            `python -m core.internal.check_suite run|list|fingerprint` — контракт CLI
##            сохраняется 1:1 при переходе файл → пакет (research-A §1).
# 🧐 TRAP[DECISION] · 2026-08-15 · — · python -m требует __main__.py на Python 3.11+
# · Rejected: «пакет с __main__-логикой только в __init__.py + if __name__ == "__main__"»
# ·   (формулировка research-A §1 / wave-brief W3 — «проверить, что python -m работает»)
# · Reason: Python 3.11+ (проверено на 3.14.6) НЕ исполняет пакет через -m без __main__.py:
# ·   «No module named X.__main__; 'X' is a package and cannot be directly executed»;
# ·   CLI/логика остались в __init__.py (main/_cmd_run/_cmd_list/_cmd_fingerprint +
# ·   if __name__ == "__main__" для прямого запуска файла), __main__.py — тонкий делегат
# · Rev: если Python вернёт исполнение пакета через -m с логикой в __init__ — удалить __main__.py
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package)
# endregion MODULE_CONTRACT

import sys

from core.internal.check_suite import main

if __name__ == "__main__":
    sys.exit(main())
