# GREP_SUMMARY: check-project-package, re-export, check_project, CheckReport, CheckResult, format_report, main, _run_check, K1-local
# STRUCTURE: ┌пакет check_project/┐ → ⊕ models.py (CheckResult/CheckReport) → ⊕ runner.py (check_project/select/exit) → ⊕ exec.py (_run_check dispatch) → ⊕ checks/ (18 handler-ов, реестр) → ⊕ drift/fixer/files/cli → ⎋ re-export API (check_project, CheckReport, CheckResult, format_report, main, _run_check)
# region MODULE_CONTRACT
## @purpose  Пакет K1-канала практик (DevPlan 170 W10-A декомпозиция check_project.py 1389 LOC):
##           runner.py (оркестрация), cli.py (CLI), exec.py (диспетчер+subprocess), files.py
##           (файловые итераторы), drift.py (дрейф-гейт), fixer.py (автофиксы), models.py
##           (dataclass-модели), checks/{tool,file,compose}.py (18 handler-ов K1). Фасад
##           check_project.py (файл рядом) re-export'ит API — импорт-путь
##           `from core.internal.practices.check_project import ...` сохранён.
## @scope    Потребители: фасад check_project.py, CLI (makefiles/project-practices.mk:21-22,
##           entrypoint-manifest.yaml:291/300 — `python3 -m core.internal.practices.
##           check_project`), tests/unit/test_practices_check_project.py (:39 — _run_check,
##           check_project). Прямых импортов из core/ НЕТ (только docstring-упоминания).
## @invariants
##   - Публичный API сохранён: check_project, CheckReport, CheckResult, format_report,
##     main, _run_check (тест :39 + CLI-контракты make/entrypoint-manifest)
##   - Импорт пакета РЕГИСТРИРУЕТ реестр handler-ов (checks/__init__ наполняет exec._HANDLERS)
##   - Модули пакета используют АБСОЛЮТНЫЕ импорты (TID252, ruff select TID)
##   - Ацикличность: models — лист; runner → exec → models; checks → exec/files/fixer/runner
## @rationale Декомпозиция монолита на SRP-модули (research-A §2): 1389 LOC → файлы <400 LOC
##            каждый; CLI-имя сохранено (org-agnostic гейт и makefiles запрещают переименование).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (декомпозиция check_project.py)
# endregion MODULE_CONTRACT

# ВАЖНО: импорт checks ДО runner/cli — регистрация реестра handler-ов в exec.HANDLERS
# (иначе run_check вернёт SKIP для всех проверок при первом прогоне).
# _run_check — приватный re-export публичной run_check (ns-гейт U-07: публичное имя +
# приватный алиас — легальный паттерн, контракт теста test_practices_check_project:39).
# 🧐 TRAP[DECISION] · 2026-08-15 · — · Сохранение приватного API _run_check через
# · `from exec import run_check as _run_check` (ns-легальный алиас) · Rejected: публичное
# · переименование _run_check → run_check в тесте :39 (research-A §2 требует сохранить
# · _run_check как API декомпозиции; тест :39 импортирует его из check_project) ·
# · Reason: ns-гейт private-imports (U-07, allowlist пуст) разрешает «публичное имя +
# · приватный алиас», приватные from-импорты без alias — RED · Rev: снятие требования
# · research-A на сохранение _run_check → публичное переименование run_check
from core.internal.practices.check_project import checks
from core.internal.practices.check_project.cli import format_report, main
from core.internal.practices.check_project.exec import run_check as _run_check
from core.internal.practices.check_project.models import CheckReport, CheckResult
from core.internal.practices.check_project.runner import check_project
