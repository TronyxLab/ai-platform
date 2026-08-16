# GREP_SUMMARY: check-project-models, CheckResult, CheckReport, frozen-dataclass, status, exit-code
# STRUCTURE: ┌dataclasses (frozen)┐ → ⊕ CheckResult (check_id/status/message/duration_s) → ⊕ CheckReport (state/level/results/warnings/exit_code) → ⎋ контракт результатов project-check
# region MODULE_CONTRACT
## @purpose  Модели результатов project-check (DevPlan 137 §2.1A): CheckResult — результат
##           одной проверки (id + статус PASS|FAIL|WARN|SKIP + сообщение + длительность),
##           CheckReport — полный отчёт прогона (state эскалатора, level_setting, результаты,
##           warning-блок [PRACTICES:PROPOSE], exit_code). Выделены в отдельный data-only
##           модуль (DevPlan 170 W10-A декомпозиция): runner.py/exec.py/checks/* импортируют
##           модели без циклов (модели не имеют зависимостей — ацикличность пакета).
## @scope    Потребители: runner.py (сборка отчёта), exec.py (SKIP-результат для неизвестного
##           handler), checks/*.py (построение результатов), cli.py (форматирование),
##           tests/unit/test_practices_check_project.py (поля отчёта).
## @invariants
##   - Оба dataclass frozen (неизменяемый отчёт — контракт канала)
##   - status ∈ {PASS, FAIL, WARN, SKIP} (строки канона, НЕ Enum — сериализация простая)
##   - exit_code ∈ {0, 1, 4} (из shared/contracts.py: EXIT_OK/EXIT_GENERIC/EXIT_CONFIG_VALIDATION)
## @rationale Разделение моделей и логики — устранение циклической зависимости
##            runner↔exec (runner нужен _run_check, exec нужен CheckResult).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:107-139)
# endregion MODULE_CONTRACT

from __future__ import annotations

from dataclasses import dataclass


# region FUNC_CheckResult
## @purpose  Frozen-результат одной проверки (DevPlan 137 §2.1A): id + статус + сообщение + время.
## @io       ⇥ check_id/status/message/duration_s → ⎋ CheckResult
## @complexity O(1)
@dataclass(frozen=True)
class CheckResult:
    """Result of a single check execution."""

    check_id: str
    status: str  # PASS | FAIL | WARN | SKIP
    message: str
    duration_s: float


# endregion FUNC_CheckResult


# region FUNC_CheckReport
## @purpose  Frozen-отчёт прогона project-check: state + level + результаты + warning-блок.
## @io       ⇥ state/level_setting/results/warnings/exit_code → ⎋ CheckReport
## @complexity O(1)
@dataclass(frozen=True)
class CheckReport:
    """Full report of a project-check run."""

    state: str
    level_setting: str
    results: tuple[CheckResult, ...]
    warnings: tuple[str, ...]
    exit_code: int


# endregion FUNC_CheckReport
