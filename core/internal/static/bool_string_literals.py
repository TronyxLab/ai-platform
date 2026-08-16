"""Bool-string-literals detector — strict bool-string comparisons (DevPlan 163 W-C).

# GREP_SUMMARY: static bool-string-literals strict-comparison normalization lower() node-yaml dataflow allowlist
# STRUCTURE: ▶ AST-скан core/**/*.py → ○ ast.Compare + Constant {"true","True","false","False"} справа
#            → ◇ Eq/NotEq? → ◇ левая часть .lower()? / Name-нормализация (dataflow)? → ⊕ Findings → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор строгих булевых строковых сравнений (DevPlan 163 W-C C1; порт
##           tests/gates/test_gate_bool_string_literals.py, DevPlan 123 T6): `==` / `!=`
##           со строковыми литералами {"true","True","false","False"} справа, где левая
##           часть НЕ нормализована через .lower() (inline или Name-присваивание в скоупе)
##           → Finding rule="bool-string-literals" (blocking). Единая точка нормализации
##           булевых — node_yaml CLI (core/internal/shared/node_yaml/cli.py).
## @scope    AST-скан core/**/*.py (исключая __pycache__). Комментарии/docstring'и с
##           литералом не являются ast.Compare → не триггерят (AST, не grep).
## @invariants
##   - RED: ast.Compare, op ∈ {Eq, NotEq}, comparators[0] = Constant str ∈
##     {"true","True","false","False"}, левая часть без .lower() и не Name,
##     нормализованный присваиванием .lower() ранее в том же функциональном скоупе
##   - PASS: `os.environ.get(...).lower() == "true"`, `(args.x or "").lower() == "true"`,
##     `enabled = str(...).lower(); if enabled != "true"`
##   - Per-function allowlist (rel-to-core, function): deploy_orchestrator.py::_parse_modules —
##     вход нормализован в parse_modules_from_node_yaml (DevPlan 123 T6; ключ
##     семантический — сдвиг строк не ломает allowlist, DevPlan 171 W3.8)
##   - Constant на ЛЕВОЙ стороне ("true" == x) и membership-тесты — вне скоупа
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale node_yaml CLI возвращал Python-bool "True" → строгие сравнения ломались
##            (TRAP[BUG] node-lifecycle.sh:53, RC 121 прод). Гейт не даёт дрейфу вернуться;
##            детектор — быстрое замещение для L1 (agent-check).
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created (порт T6-гейта)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)

# Строгие булевы строковые литералы (== / !=) — 8 комбинаций регистра/оператора.
_STRICT_BOOL_LITERALS: frozenset[str] = frozenset(("true", "True", "false", "False"))

# ⚠️ Семантический allowlist (DevPlan 171 W3.8): ключ — (rel-к-core, имя функции),
# НЕ строка (сдвиг строк не ломает allowlist). Вход нормализован ДО сравнения.
_ALLOWLIST_FUNCS: frozenset[tuple[str, str]] = frozenset((
    # deploy_orchestrator.py::_parse_modules — enabled нормализован
    # parse_modules_from_node_yaml (DevPlan 123 T6)
    ("internal/bootstrap/deploy/deploy_orchestrator.py", "_parse_modules"),
))


# region FUNC_contains_lower
def _contains_lower(node: ast.AST) -> bool:
    """Проверить, что AST-поддерево содержит вызов `.lower()` (нормализация в выражении).

    ## @purpose  Инлайн-нормализация: (x or "").lower() / os.environ.get(...).lower().
    ## @io       ⇥ node: ast.AST → ⎋ bool
    ## @complexity  O(N) — узлы поддерева
    """
    return any(
        isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "lower"
        for sub in ast.walk(node)
    )


# endregion FUNC_contains_lower


# region FUNC_collect_from_stmt
def _collect_from_stmt(stmt: ast.stmt, result: dict[str, int]) -> None:
    """Собрать Name-цели, присвоенные из .lower()-содержащего выражения.

    ## @purpose  Dataflow: name → lineno, если name присвоен из выражения с .lower().
    ##           Не спускается в вложенные функции (скоуп-правила Python).
    ## @io       ⇥ stmt: ast.stmt, result: dict[str, int] (мутируется)
    ## @complexity  O(N) — узлы stmt
    """
    for node in ast.walk(stmt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        lineno: int | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
            lineno = node.lineno
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
            lineno = node.lineno
        if not targets or value is None or lineno is None or not _contains_lower(value):
            continue
        for target in targets:
            if isinstance(target, ast.Name) and (target.id not in result or lineno < result[target.id]):
                result[target.id] = lineno


# endregion FUNC_collect_from_stmt


# region FUNC_enclosing_function
def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.stmt | None:
    """Найти ближайший охватывающий FunctionDef/AsyncFunctionDef (или None для module-level).

    ## @purpose  Скоуп сравнения: внутри функции — вся функция; на module-level —
    ##           только statements вне функций.
    ## @io       ⇥ node: ast.AST, parents: dict[ast.AST, ast.AST] → ⎋ ast.stmt | None
    ## @complexity  O(D) — глубина дерева
    """
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


# endregion FUNC_enclosing_function


# region FUNC_scope_normalized
def _scope_normalized(compare: ast.Compare, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> dict[str, int]:
    """Собрать {name: earliest_lineno} .lower()-нормализованных имён в скоупе сравнения.

    ## @purpose  Внутри функции — весь подскоп; на module-level — statements вне функций
    ##           (Name resolution: function-locals не видны на module level).
    ## @io       ⇥ compare, parents, tree → ⎋ dict[str, int]
    ## @complexity  O(S) — statements скоупа
    """
    scope = _enclosing_function(compare, parents)
    result: dict[str, int] = {}
    if scope is not None:
        _collect_from_stmt(scope, result)
        return result
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        _collect_from_stmt(stmt, result)
    return result


# endregion FUNC_scope_normalized


# region FUNC_left_is_normalized
def _left_is_normalized(left: ast.expr, compare_lineno: int, normalized: dict[str, int]) -> bool:
    """Левая часть сравнения нормализована? (inline .lower() ИЛИ Name с .lower()-присваиванием).

    ## @purpose  Ключевое правило T6: легитимны только нормализованные сравнения.
    ## @io       ⇥ left: ast.expr, compare_lineno: int, normalized: dict[str, int] → ⎋ bool
    ## @complexity  O(1)
    """
    if _contains_lower(left):
        return True
    if isinstance(left, ast.Name):
        assign_line = normalized.get(left.id)
        if assign_line is not None and assign_line < compare_lineno:
            return True
    return False


# endregion FUNC_left_is_normalized


# region FUNC_scan_file
def _scan_file(path: Path, core_dir: Path, rel_to_root: str) -> list[Finding]:
    """AST-скан одного .py файла на строгие булевы строковые сравнения.

    ## @purpose  Парсинг + обход ast.Compare по правилам T6 с per-line allowlist.
    ## @io       ⇥ path: Path, core_dir: Path (может отсутствовать для probe-деревьев),
    ##              rel_to_root: str → ⎋ list[Finding]
    ## @complexity  O(N) — AST-узлы
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    parents: dict[ast.AST, ast.AST] = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    rel_to_core = path.relative_to(core_dir).as_posix() if core_dir.is_dir() else rel_to_root
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not node.comparators:
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        const = node.comparators[0]
        if not (
            isinstance(const, ast.Constant) and isinstance(const.value, str) and const.value in _STRICT_BOOL_LITERALS
        ):
            continue
        # Семантический allowlist (DevPlan 171 W3.8): (файл, охватывающая функция)
        enclosing = _enclosing_function(node, parents)
        if (
            enclosing is not None
            and isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (rel_to_core, enclosing.name) in _ALLOWLIST_FUNCS
        ):
            continue
        if _left_is_normalized(node.left, node.lineno, _scope_normalized(node, parents, tree)):
            continue
        expr = ast.unparse(node).strip()
        findings.append(
            Finding(
                rule="bool-string-literals",
                file=rel_to_root,
                line=node.lineno,
                message=f"strict bool-string comparison without .lower() normalization: {expr}",
            )
        )
        logger.warning("[IMP:9][bool_string_literals][RED] %s:%d %s", rel_to_root, node.lineno, expr)
    return findings


# endregion FUNC_scan_file


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти строгие булевы строковые сравнения в core/**/*.py.

    # ▶ ┌core/**/*.py┐ → ○ AST parse → ○ walk ast.Compare → ◇ allowlist/normalized?
    #   → ⊕ offenders → ⎋ list[Finding]

    ## @purpose  Главный вход детектора (registry) — правило T6 (DevPlan 123).
    ## @io       ⇥ root: Path, changed: set[str] | None → ⎋ list[Finding]
    ## @complexity  O(F * N) — файлы × AST-узлы
    ## @invariants  Сканирует root/core/**/*.py (либо root/**/*.py, если root/core
    ##              отсутствует — probe-деревья тестов); rel-to-core для allowlist
    """
    core_dir = root / "core"
    scan_root = core_dir if core_dir.is_dir() else root
    files = sorted(p for p in scan_root.rglob("*.py") if "__pycache__" not in p.parts and p.is_file())
    findings: list[Finding] = []
    for path in files:
        rel_to_root = path.relative_to(root).as_posix()
        if changed is not None and rel_to_root not in changed:
            continue
        findings.extend(_scan_file(path, core_dir, rel_to_root))
    logger.info("[IMP:9][bool_string_literals] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][bool_string_literals] PASS: 0 strict bool-string comparisons in core/")
    return findings


# endregion FUNC_detect
