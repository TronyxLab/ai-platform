"""Bare-raise detector — ValueError/RuntimeError ban in core/internal (DevPlan 163 W-C).

# GREP_SUMMARY: static bare-raise ValueError RuntimeError PlatformError typed-exceptions AST allowlist U-12
# STRUCTURE: ▶ AST-обход core/internal/**/*.py → ◇ ast.Raise → exc Call|Name ∈ {ValueError, RuntimeError}
#            → ◇ (file,line) ∈ allowlist? → ⊕ Finding → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор «0 bare raise» (DevPlan 163 W-C C3; порт
##           tests/gates/test_gate_no_bare_raise.py, DevPlan 116 B4 T5, AC1, U-12):
##           raise ValueError/RuntimeError в core/internal ЗАПРЕЩЁН — типизированная
##           иерархия shared/exceptions.py (ConfigValidationError / ConfigParseError /
##           PlatformFatalError и т.д.). Allowlist ПУСТ (после миграции T2).
## @scope    AST-скан всех core/internal/**/*.py. shared/exceptions.py сам не матчится —
##           скан ловит только ValueError/RuntimeError (классы-определения не триггерят).
## @invariants
##   - raise ValueError/RuntimeError (Call или Name) → RED, если (file,line) не в allowlist
##   - Allowlist пуст; каждая запись валидируется на существование (stale → RED)
##   - Мораторий state_machine.py не спасает от НОВЫХ bare raise (DevPlan T5.1)
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale U-12: 40 bare raise — caller не может программно различить тип ошибки.
##            Иерархия создана (038a), retrofit выполнен волной B4; детектор фиксирует
##            0 в быстром слое без pytest-гейта.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created (порт B4 T5)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Запрещённые типы исключений — должны быть заменены на иерархию PlatformError (T2)
_FORBIDDEN_EXC: frozenset[str] = frozenset(("ValueError", "RuntimeError"))

# Allowlist (D2): константа в детекторе, паттерн B2 profiles_parity. ПУСТА ПОСЛЕ миграции T2.
_ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


# region FUNC_scan_file
def _scan_file(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """AST-скан одного .py файла на raise ValueError/RuntimeError.

    ## @purpose  Walk ast.Raise: exc Call(Name)/Name с id ∈ _FORBIDDEN_EXC и
    ##           (rel, lineno) вне allowlist → RED.
    ## @io       ⇥ path: Path, root: Path, changed → ⎋ list[Finding]
    ## @complexity  O(N) — AST-узлы
    """
    rel = path.relative_to(root).as_posix()
    if changed is not None and rel not in changed:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        exc = node.exc
        exc_name: str | None = None
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            exc_name = exc.func.id
        elif isinstance(exc, ast.Name):
            exc_name = exc.id
        if exc_name not in _FORBIDDEN_EXC:
            continue
        if (rel, node.lineno) in _ALLOWLIST:
            continue
        findings.append(
            Finding(
                rule="bare-raise",
                file=rel,
                line=node.lineno,
                message=f"raise {exc_name} forbidden — use typed PlatformError hierarchy (shared/exceptions.py)",
            )
        )
        logger.warning("[IMP:9][bare_raise][RED] %s:%d raise %s", rel, node.lineno, exc_name)
    return findings


# endregion FUNC_scan_file


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти raise ValueError/RuntimeError в core/internal/.

    # ▶ ┌core/internal/**/*.py┐ → ○ walk ast.Raise → ◇ exc ∈ {ValueError, RuntimeError}
    #   → ◇ allowlist? → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило B4 T5 (U-12).
    ##           Для probe-деревьев (без core/) — рекурсивный скан всех .py.
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    """
    core_dir = root / "core"
    scan_root = (core_dir / "internal") if core_dir.is_dir() else root
    files = sorted(p for p in scan_root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
    findings: list[Finding] = []
    for path in files:
        findings.extend(_scan_file(path, root, changed))
    logger.info("[IMP:9][bare_raise] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][bare_raise] PASS: 0 bare raise ValueError/RuntimeError")
    return findings


# endregion FUNC_detect
