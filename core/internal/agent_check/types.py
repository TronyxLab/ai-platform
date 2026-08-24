"""agent_check.types — data-контракты L1-сигнала (T3.2 декомпозиция __init__.py).

# GREP_SUMMARY: agent-check types TypedDict AgentFinding ChangedFiles severity contract T3.2
# STRUCTURE: ┌severity-константы (SEVERITY_ERROR/WARNING, VERDICTS)┐ → ⊕ REPORT_TYPES
#            (14 TypedDict JSON-границ) → ⊕ CLASS_AgentFinding → ⊕ CLASS_ChangedFiles → ⎋ leaf
"""
# region MODULE_CONTRACT
## @purpose  Типы/контракты L1-сигнала agent-check (T3.2): TypedDict-границы JSON-отчёта и
##           внешних тулов (W11-G4), frozen-датаклассы находок (AgentFinding/ChangedFiles),
##           severity-константы. Выделены из __init__.py — чистый leaf: 0 I/O, 0 оркестрации.
## @scope    core/internal/agent_check/types.py — импортируется runners.py (исполнение) и
##           __init__.py (фасад/реэкспорт). Ничего не импортирует из пакета agent_check.
## @invariants
##   - Только декларации: TypedDict + dataclass + константы; никакой логики/подпроцессов
##   - SEVERITY_ERROR/SEVERITY_WARNING и VERDICTS — единый источник severity-строк
##   - Машиночитаемый контракт T3.1: {rule, file, line, message, fixable, severity, source}
##   - Все имена ПУБЛИЧНЫЕ (U-07): межмодульный доступ легален только через публичные имена
##     (детектор static private-imports; приватные префиксы были в монолите __init__.py,
##     при декомпозиции сняты — прецедент check_suite U-07)
## @rationale Слои зависимостей: types (leaf) ← runners (исполнение) ← __init__ (facade+CLI) —
##            порядок гарантирует ацикличность пакета (import-linter acyclic-internal-domains).
##            Данные отделены от исполнения по связности (прецедент check_suite/models.py).
## @changes 2026-08-22 | T3.2 (1787342045763) — extracted from __init__.py (1092 LOC → пакет);
##           имена TypedDict-контрактов → публичные (U-07, детектор private-imports)
# endregion MODULE_CONTRACT

from __future__ import annotations

import dataclasses
from typing import TypedDict

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
VERDICTS = ("blocking", "advisory", "off")


# region REPORT_TYPES
# TypedDict-границы JSON-отчёта/внешних тулов (W11-G4): машиночитаемый контракт T3.1
class AgentFindingDict(TypedDict):
    """JSON-safe представление AgentFinding ({rule, file, line, message, fixable, severity, source})."""

    rule: str
    file: str
    line: int
    message: str
    fixable: bool
    severity: str
    source: str


class ChangedFilesDict(TypedDict):
    """changed-секция отчёта ({py, sh, makefiles, total})."""

    py: list[str]
    sh: list[str]
    makefiles: list[str]
    total: int


class CheckEntryDict(TypedDict):
    """Секция checks.* (ruff/basedpyright/static/bespoke): status + findings + duration."""

    status: str
    findings: list[AgentFindingDict]
    duration_ms: float


class AdvisoryEntryDict(TypedDict):
    """Секция checks.advisory: status + verdicts + findings + duration."""

    status: str
    verdicts: dict[str, str]
    findings: list[AgentFindingDict]
    duration_ms: float


class ChecksDict(TypedDict):
    """checks-секция отчёта (5 шагов L1-сигнала)."""

    ruff: CheckEntryDict
    advisory: AdvisoryEntryDict
    basedpyright: CheckEntryDict
    static: CheckEntryDict
    bespoke: CheckEntryDict


class SummaryDict(TypedDict):
    """summary-секция отчёта (счётчики + clean + duration)."""

    blocking: int
    advisory: int
    total: int
    clean: bool
    duration_ms: float


class AgentCheckReport(TypedDict):
    """Корневой отчёт agent-check (контракт T3.1)."""

    schema_version: str
    tool: str
    changed: ChangedFilesDict
    checks: ChecksDict
    findings: list[AgentFindingDict]
    summary: SummaryDict


class RuffItemDict(TypedDict):
    """Элемент JSON-вывода ruff --output-format json (обязательные ключи всегда присутствуют)."""

    code: str
    filename: str
    location: dict[str, int]  # {row, column}
    message: str
    fix: dict[str, object]  # presence-only ("fix" in item)


class BasedpyrightDiagDict(TypedDict):
    """Элемент generalDiagnostics basedpyright --outputjson (severity=error фильтр)."""

    rule: str | None
    file: str
    range: dict[str, dict[str, int]]  # {start: {line, character}, end: {...}}
    message: str
    severity: str


class BasedpyrightOutputDict(TypedDict, total=False):
    """Корень JSON basedpyright --outputjson."""

    summary: dict[str, int]
    generalDiagnostics: list[BasedpyrightDiagDict]


class StaticFindingDict(TypedDict):
    """Элемент findings JSON статического слоя (registry.json_report → FindingDict)."""

    rule: str
    file: str
    line: int
    message: str
    severity: str


class StaticOutputDict(TypedDict, total=False):
    """Корень JSON `static check --json`."""

    findings: list[StaticFindingDict]


class FpRegistryEntryDict(TypedDict, total=False):
    """Запись rules[] в fp_registry.yaml."""

    rule: str
    verdict: str


class FpRegistryDict(TypedDict, total=False):
    """Корень fp_registry.yaml."""

    rules: list[FpRegistryEntryDict]


# endregion REPORT_TYPES


# region CLASS_AgentFinding
@dataclasses.dataclass(frozen=True, slots=True)
class AgentFinding:
    """Единичная находка agent-check (контракт T3.1: rule/file/line/message/fixable).

    # ▶ AgentFinding ┌rule+file+line+message+fixable+severity+source┐ → ⊕ to_dict → ⎋
    """

    rule: str
    file: str
    line: int
    message: str
    fixable: bool = False
    severity: str = SEVERITY_ERROR
    source: str = "agent-check"

    # region FUNC_to_dict
    def to_dict(self) -> AgentFindingDict:
        """Се­риализовать находку в JSON-safe dict (машиночитаемый фидбек агента).

        ## @purpose  Единый формат находки всех шагов (ruff/basedpyright/static/bespoke)
        ##           — {rule, file, line, message, fixable} + severity/source.
        ## @io       ⎋ dict[str, Any]
        ## @complexity  O(1)
        """
        return {
            "rule": self.rule,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "fixable": self.fixable,
            "severity": self.severity,
            "source": self.source,
        }

    # endregion FUNC_to_dict

    # region FUNC___str__
    def __str__(self) -> str:
        """Человекочитаемое представление `file:line [rule] message (fixable?)`.

        ## @purpose  human-отчёт: стабильная строка находки.
        ## @io       ⎋ str
        ## @complexity  O(1)
        """
        location = self.file if self.line <= 0 else f"{self.file}:{self.line}"
        marker = " [autofix]" if self.fixable else ""
        return f"{location} [{self.rule}]{marker} {self.message}"

    # endregion FUNC___str__


# endregion CLASS_AgentFinding


# region CLASS_ChangedFiles
@dataclasses.dataclass(frozen=True, slots=True)
class ChangedFiles:
    """Изменённые файлы по категориям (repo-relative posix-пути).

    # ▶ ChangedFiles ┌py+sh+makefiles┐ → ⊕ total / to_dict → ⎋
    """

    py: tuple[str, ...]
    sh: tuple[str, ...]
    makefiles: tuple[str, ...]

    # region FUNC_total
    @property
    def total(self) -> int:
        """Суммарное число изменённых файлов.

        ## @purpose  Отчётный счётчик changed (L1-контекст).
        ## @io       ⎋ int
        ## @complexity  O(1)
        """
        return len(self.py) + len(self.sh) + len(self.makefiles)

    # endregion FUNC_total

    # region FUNC_to_dict
    def to_dict(self) -> ChangedFilesDict:
        """JSON-safe представление для отчёта.

        ## @purpose  changed-секция JSON/human отчёта.
        ## @io       ⎋ dict[str, Any]
        ## @complexity  O(1)
        """
        return {
            "py": list(self.py),
            "sh": list(self.sh),
            "makefiles": list(self.makefiles),
            "total": self.total,
        }

    # endregion FUNC_to_dict


# endregion CLASS_ChangedFiles
