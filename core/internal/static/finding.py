"""Finding model — единичный результат статического детектора (DevPlan 163 W-C).

# GREP_SUMMARY: static finding dataclass json serialization severity rule file line message defect-model
# STRUCTURE: ▶ Finding (frozen dataclass) ┌rule/file/line/message/severity┐ → ⊕ to_dict/from_dict/to_json → ⎋ __str__
"""
# region MODULE_CONTRACT
## @purpose  Единая модель дефекта статического слоя (core/internal/static): датакласс
##           Finding (rule, file, line, message, severity) + JSON-сериализация для
##           машиночитаемого вывода CLI (`static check --json`, DevPlan 163 T2.1/T3.1).
## @scope    Модель данных — не содержит логики сканирования. Потребляется всеми
##           детекторами (registry.py), CLI (__main__.py) и тестами R5 (tests/unit/test_static_*).
## @invariants
##   - Finding неизменяем (frozen, slots) — безопасен для параллельных проходов
##   - file — POSIX-путь ОТНОСИТЕЛЬНО корня сканирования (repo-relative), пустая строка
##     если файл неприменим (0-файловые находки, напр. env-chain на уровне конфига)
##   - line — 1-based номер строки; 0 если строка неприменима
##   - severity ∈ {"error", "warning"} — все действующие гейты-правила = "error" (blocking);
##     "warning" зарезервирован для advisory-детекторов фазы 2
##   - to_dict/from_dict — round-trip stable: from_dict(to_dict(f)) == f
## @rationale Машиночитаемый фидбек агента (rule/file/line/message) — контракт T3.1
##            (agent-check JSON). Единая модель вместо кортежей устраняет дрейф формата
##            между детекторами (проблема 3 writer'ов audit-лога, U-10 — та же механика).
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import dataclasses
import json
from typing import TypedDict

# Допустимые значения severity (blocking-канон; advisory — фаза 2)
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
_VALID_SEVERITIES: frozenset[str] = frozenset((SEVERITY_ERROR, SEVERITY_WARNING))


class FindingDict(TypedDict):
    """JSON-safe представление Finding (W11-G4): {rule, file, line, message, severity}."""

    rule: str
    file: str
    line: int
    message: str
    severity: str


class FindingParseError(ValueError):
    """Ошибка парсинга сериализованного Finding (невалидный severity/структура).

    ## @purpose  Типизированная ошибка модели Finding: fail-fast при from_dict с
    ##           неизвестным severity. Наследует ValueError (совместимость с
    ##           pytest.raises(ValueError) в тестах), но НЕ голый `raise ValueError`
    ##           (гейт no-bare-raise U-12 запрещает ValueError/RuntimeError в core/internal).
    ## @io       — (маркер-класс)
    ## @complexity  O(1)
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """Единичная находка статического детектора.

    # ▶ Finding ┌rule+file+line+message+severity┐ → ⊕ to_dict (JSON-safe) → ⎋ __str__
    """

    rule: str
    file: str
    line: int
    message: str
    severity: str = SEVERITY_ERROR

    # region FUNC_to_dict
    def to_dict(self) -> FindingDict:
        """Сериализовать находку в JSON-safe dict.

        ## @purpose  Машиночитаемое представление для `static check --json`.
        ## @io       ⎋ dict[str, Any] — {rule, file, line, message, severity}
        ## @complexity  O(1)
        """
        return {
            "rule": self.rule,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "severity": self.severity,
        }

    # endregion FUNC_to_dict

    # region FUNC_from_dict
    @classmethod
    def from_dict(cls, data: FindingDict) -> Finding:
        """Восстановить Finding из dict (round-trip с to_dict).

        ## @purpose  Обратная сериализация (парсинг JSON-вывода, инвариант round-trip).
        ## @io       ⇥ data: dict[str, Any] → ⎋ Finding
        ## @complexity  O(1)
        ## @invariants  Неизвестный severity → ValueError (fail-fast, никаких тихих значений)
        """
        severity = str(data.get("severity", SEVERITY_ERROR))
        if severity not in _VALID_SEVERITIES:
            msg = f"Unknown finding severity: {severity!r}"
            raise FindingParseError(msg)
        return cls(
            rule=str(data["rule"]),
            file=str(data["file"]),
            line=int(data["line"]),
            message=str(data["message"]),
            severity=severity,
        )

    # endregion FUNC_from_dict

    # region FUNC_to_json
    def to_json(self) -> str:
        """Сериализовать находку в одну JSON-строку.

        ## @purpose  Строковый JSONL-вывод (одна находка = одна строка).
        ## @io       ⎋ str
        ## @complexity  O(1)
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)

    # endregion FUNC_to_json

    # region FUNC___str__
    def __str__(self) -> str:
        """Человекочитаемое представление `file:line [rule] message`.

        ## @purpose  Дефолтный вывод CLI (`static check` без --json).
        ## @io       ⎋ str
        ## @complexity  O(1)
        """
        location = self.file if self.line <= 0 else f"{self.file}:{self.line}"
        return f"{location} [{self.rule}] {self.message}"

    # endregion FUNC___str__
