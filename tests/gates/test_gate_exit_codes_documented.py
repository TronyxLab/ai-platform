#!/usr/bin/env python3
# GREP_SUMMARY: gate exit-codes documented AGENTS.md contract ConfigNotFoundError ConfigValidationError PlatformFatalError U-29 anti-drift
# STRUCTURE: ▶ read core/AGENTS.md → ◇ substring-проверки кодов 2/3/4/10 + классов → ⊕ violations → ⎋ PASS/RED
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 116 B4 T7, AC5): контракт exit-кодов задокументирован в core/AGENTS.md.
##           Коды 2 (ConfigNotFoundError), 3 (ConfigParseError), 4 (ConfigValidationError),
##           10 (PlatformFatalError) — единая семантика на весь core (shared/contracts.py).
##           Если документация расходится с контрактом или удаляется — RED.
## @scope    Только core/AGENTS.md (root AGENTS.md НЕ трогается по DevPlan B4 T7).
## @invariants
##   - Строки таблицы exit-кодов содержат классы ConfigNotFoundError / ConfigParseError /
##     ConfigValidationError / PlatformFatalError (substring-сравнение, DevPlan T7.2).
##   - Секция «Exit-коды» присутствует в core/AGENTS.md.
## @rationale U-29: exit-коды были разбросаны (2/10 в разных местах, без документации).
##            Гейт фиксирует документацию как часть контракта — дрейф невозможен.
## @changes 2026-08-01 | DevPlan 116 B4 T7 — Created
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_AGENTS_MD = ROOT / "core" / "AGENTS.md"

# (код, класс-исключение) — каждая строка таблицы должна содержать оба подстроки
_EXIT_CODE_CONTRACT: list[tuple[str, str]] = [
    ("| 2 |", "ConfigNotFoundError"),
    ("| 3 |", "ConfigParseError"),
    ("| 4 |", "ConfigValidationError"),
    ("| 10 |", "PlatformFatalError"),
]


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · exit-codes documented in core/AGENTS.md (DevPlan 116 B4 T7)
def test_exit_codes_documented_in_core_agents(caplog) -> None:
    """Verify core/AGENTS.md documents exit codes 2/3/4/10 with exception classes."""
    text = _CORE_AGENTS_MD.read_text(encoding="utf-8")

    missing: list[str] = []
    for code, cls in _EXIT_CODE_CONTRACT:
        row_found = code in text and cls in text
        logger.info(
            "[IMP:8][exit-codes][%s] code=%s class=%s → %s", "ok" if row_found else "MISSING", code, cls, row_found
        )
        if not row_found:
            missing.append(f"{code} → {cls}")

    assert not missing, (
        "core/AGENTS.md не документирует контракт exit-кодов (DevPlan 116 B4 T7):\n"
        + "\n".join(f"  - missing: {m}" for m in missing)
        + "\n\nСекция «Exit-коды» должна содержать таблицу | Код | Семантика | Исключение |."
    )

    assert "Exit-коды" in text, "core/AGENTS.md не содержит секции «Exit-коды» (контракт B4 T7)"
    logger.info("[IMP:9][exit-codes][done] PASS: %d/4 exit-code строк документированы", len(_EXIT_CODE_CONTRACT))
