#!/usr/bin/env python3
# GREP_SUMMARY: gate no-bare-raise ValueError RuntimeError PlatformError typed-exceptions AST-scan allowlist U-12 anti-drift
# STRUCTURE: ▶ AST-обход core/internal/*.py → ◇ ast.Raise → exc.func.id in {ValueError, RuntimeError}? → ◇ allowlist (file,line)? → ⊕ violations → ⎋ PASS/RED
# region MODULE_CONTRACT
## @purpose  Gate «0 bare raise» (DevPlan 116 B4 T5, AC1, U-12): raise ValueError/RuntimeError
##           в core/internal ЗАПРЕЩЁН — типизированная иерархия shared/exceptions.py
##           (ConfigValidationError / ConfigParseError / PlatformFatalError и т.д.).
##           _ALLOWLIST — пустое множество ПОСЛЕ миграции T2 (механика D2: константа в тесте,
##           паттерн B2 test_gate_profiles_parity.py; сжимается волнами правкой теста).
## @scope    Все core/internal/**/*.py. shared/exceptions.py сам не матчится — скан
##           ловит только ValueError/RuntimeError (классы-определения не триггерят).
## @invariants
##   - raise ValueError/RuntimeError (Call или Name) → RED, если (file, line) не в _ALLOWLIST.
##   - Каждая запись _ALLOWLIST валидируется: (file,line) существует и содержит raise
##     (stale-запись → fail, сжимается).
##   - Мораторий state_machine.py не спасает от НОВЫХ bare raise (DevPlan T5.1).
## @rationale U-12: 40 bare raise — caller не может программно различить тип ошибки.
##            Иерархия создана (038a), retrofit выполнен волной B4; гейт фиксирует 0.
## @changes 2026-08-01 | DevPlan 116 B4 T5 — Created
# endregion MODULE_CONTRACT

import ast
import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"

# Запрещённые типы исключений — должны быть заменены на иерархию PlatformError (T2)
_FORBIDDEN_EXC: frozenset[str] = frozenset({"ValueError", "RuntimeError"})

# Allowlist (D2): константа в тесте, паттерн B2 profiles_parity. ПУСТА ПОСЛЕ миграции T2.
# Записи: (relative_posix_path, lineno). Сжимается волнами правкой этого файла.
_ALLOWLIST: set[tuple[str, int]] = set()


def _scan_bare_raises() -> list[tuple[str, int, str]]:
    """Find raise ValueError/RuntimeError in core/internal.

    ▶ ┌_CORE_INTERNAL┐ → ○ AST walk → ◇ ast.Raise → exc Call|Name с id ∈ _FORBIDDEN_EXC
    │    → ◇ (file,line) ∈ _ALLOWLIST? → ⊕ violations → ⎋ list
    """
    violations: list[tuple[str, int, str]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            exc = node.exc
            exc_name: str | None = None
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                exc_name = exc.func.id
            elif isinstance(exc, ast.Name):
                exc_name = exc.id
            if exc_name in _FORBIDDEN_EXC and (rel, node.lineno) not in _ALLOWLIST:
                violations.append((rel, node.lineno, exc_name))
    return violations


def _validate_allowlist() -> list[str]:
    """Validate each _ALLOWLIST entry still exists and contains a bare raise (stale → fail).

    ▶ ┌_ALLOWLIST┐ → ○ read file → ◇ строка lineno содержит "raise"? → ⊕ stale → ⎋ list
    """
    stale: list[str] = []
    for rel, lineno in sorted(_ALLOWLIST):
        p = ROOT / rel
        if not p.is_file():
            stale.append(f"{rel}:{lineno} — файл не существует")
            continue
        lines = p.read_text(errors="replace").splitlines()
        if lineno - 1 >= len(lines) or "raise" not in lines[lineno - 1]:
            stale.append(f"{rel}:{lineno} — запись устарела (raise отсутствует)")
    return stale


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · 0 bare ValueError/RuntimeError in core/internal (DevPlan 116 B4 T5)
def test_no_bare_valueerror_runtimeerror(caplog) -> None:
    """No bare raise ValueError/RuntimeError in core/internal — typed hierarchy only."""
    stale = _validate_allowlist()
    assert not stale, "Stale _ALLOWLIST entries:\n" + "\n".join(f"  - {s}" for s in stale)

    violations = _scan_bare_raises()
    if violations:
        for rel, lineno, exc_name in violations:
            logger.error("[IMP:10][no-bare-raise][RED] %s:%d raise %s", rel, lineno, exc_name)
        pytest.fail(
            f"Bare raise ValueError/RuntimeError найдены ({len(violations)}):\n"
            + "\n".join(f"  - {rel}:{lineno} — raise {exc_name}" for rel, lineno, exc_name in violations)
            + "\n\nЗамените на иерархию PlatformError (shared/exceptions.py): ConfigValidationError,"
            " ConfigParseError, PlatformFatalError — см. DevPlan 116 B4 T2 маппинг."
        )

    logger.info("[IMP:9][no-bare-raise][done] PASS: 0 bare raise ValueError/RuntimeError (allowlist=%d)", len(_ALLOWLIST))
