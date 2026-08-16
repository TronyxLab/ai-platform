#!/usr/bin/env python3
# GREP_SUMMARY: lifecycle-exceptions, phase-dependency-error, phase-precondition-error, bootstrap, leaf, import-cycle-break
# STRUCTURE: ▶ exceptions.py (leaf) → ┌PhaseDependencyError (exit_code=1)┐ → ┌PhasePreconditionError┐ → ⎋ state_machine/state_store/preconditions import отсюда
# region MODULE_CONTRACT
## @purpose  Доменные исключения bootstrap lifecycle (план 170 W5-C1, W10-design п.1):
##           PhaseDependencyError + PhasePreconditionError — вынесены из state_machine.py
##           в LEAF-модуль exceptions.py для разрыва import-цикла state_store ↔ state_machine
##           (state_store больше не импортирует state_machine лениво — ошибки приходят отсюда).
## @scope    Только классы исключений — НИКАКОЙ другой логики (leaf, без зависимостей).
##           state_machine.py re-export'ит оба класса (from .exceptions import ...) —
##           публичный контракт импортёров (cli.py, тесты) сохраняется без изменений.
## @invariants
##   - exceptions.py НЕ импортирует state_machine/state_store/phases (leaf — 0 зависимостей)
##   - PhaseDependencyError.exit_code = 1 — CLI (lifecycle/cli.py) читает e.exit_code
##     через hasattr-guard-паттерн (структурный отказ фазы = код 1, не конфигурационная ошибка)
##   - Оба класса наследуют Exception (НЕ PlatformError) — перехват по имени в cli.py
##     (except (PhaseDependencyError, PhasePreconditionError, PlatformFatalError))
## @rationale W10-design п.1: state_store.py:211 импортировал PhasePreconditionError лениво
##            из state_machine (единственная точка цикла state_machine ↔ state_store,
##            research-A §4, импорт-граф A9 п.2). Общий leaf exceptions.py — оба модуля
##            импортируют отсюда, цикл исчезает; ignore-ребро .importlinter:156 удаляется.
##            ⚠️ ПОБОЧНЫЙ ЭФФЕКТ: появление модуля exceptions в пакете lifecycle создаёт
##            pyright-неоднозначность для ЛЕГАСИ fallback-импорта secrets_manager.py:124-128
##            (`from exceptions import ConfigNotFoundError...` — режим запуска как скрипта из
##            shared/, см. TRAP[DECISION] ниже). Runtime-семантика НЕ меняется (fallback-ветка
##            работает только при скрипт-запуске, где lifecycle/exceptions не в sys.path).
## @changes  2026-08-15 · план 170 W5-C1 — создан (перенос из state_machine.py:90-108)
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-08-15 · — · lifecycle/exceptions.py создаёт pyright-неоднозначность
# для легаси fallback-импорта secrets_manager.py:124-128 (`from exceptions import ...` —
# режим скрипт-запуска из shared/; sys.path.insert(_SHARED_DIR), где exceptions.py = shared).
# · Rejected: правка secrets_manager.py (fallback-импорт → `from shared.exceptions import ...`)
#   и ослабление pyrightconfig (reportImplicitRelativeImport/reportAttributeAccessIssue) —
#   оба вне файл-раздела W5-core (secrets_manager.py запрещён к правке; pyrightconfig — общий конфиг)
# · Reason: W5-C1 требование «lifecycle/exceptions.py» vs запрет «не трогай secrets_manager.py» —
#   неустранимое статическое противоречие в рамках файл-раздела; runtime безопасен (ветка
#   недостижима при пакетном импорте), страдает только basedpyright (4 errors на 124-128)
# · Rev: W5-cli/следующая волна: fallback-импорт secrets_manager.py → `from shared.exceptions import`
#   (или полные пути) — устранит неоднозначность, basedpyright вернётся к 0 errors


class PhaseDependencyError(Exception):
    """Raised when a phase's dependency graph check fails — a prerequisite phase is not done.

    ## @purpose — Distinguish structural phase ordering violations from intra-phase precondition failures.
    ##             Operator sees: "Phase φ6 requires φ4, but φ4 is pending".
    """

    # CLI (lifecycle/cli.py) читает e.exit_code для exit-кода процесса (hasattr-guard-шаблон);
    # структурный отказ фазы = код 1 (не конфигурационная ошибка).
    exit_code: int = 1


class PhasePreconditionError(Exception):
    """Raised when a phase's precondition_check() fails — intra-phase condition not met.

    ## @purpose — Intra-phase condition (root access, file exists, network available).
    ##             Operator sees: "Phase φ1 precondition failed: must run as root (euid=0)".
    """
