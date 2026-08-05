#!/usr/bin/env python3
# GREP_SUMMARY: gate bool-string-literals T6 strict-comparison normalization lower() node-yaml-cli allowlist anti-drift
# STRUCTURE: ▶ AST-скан core/**/*.py → ○ ast.Compare + Constant {"true","True","false","False"} справа → ◇ Eq/NotEq? → ◇ левая часть .lower()? / Name-нормализация (dataflow)? → ⟦RED: offenders⟧ | _ALLOWLIST_LINES (per-line) → ⎋ probe-тесты R5 (tmp_path) → PASS
# region MODULE_CONTRACT
## @purpose  Bool-string-literals gate (DevPlan 123 T6): строгие сравнения с булевыми строковыми
##           литералами {"true","True","false","False"} (== / !=) в core/**/*.py, где левая часть
##           НЕ нормализована → RED. Единая точка нормализации булевых — node_yaml CLI
##           (core/internal/shared/node_yaml_cli.py _format_cli_value, DevPlan 123 T6); потребители
##           сравнивают ТОЛЬКО через .lower()-нормализованные выражения.
## @scope    Сканирует core/**/*.py (исключая __pycache__) — AST-скан, НЕ grep (комментарии/
##           docstring'и с литералом не являются ast.Compare → не RED).
## @invariants
##   - RED: ast.Compare, op ∈ {Eq, NotEq}, comparators[0] = Constant str ∈
##     {"true","True","false","False"}, левая часть НЕ содержит вызова .lower()
##     И не является Name, нормализованным предшествующим присваиванием с .lower() в том же
##     функциональном скоупе (dataflow — паттерн «нормализуй вход на входе функции», T6).
##   - PASS: `os.environ.get(...).lower() == "true"`, `(args.x or "").lower() == "true"`,
##     `enabled = str(...).lower(); if enabled != "true"` (присваивание ДО сравнения).
##   - allowlist ПУСТ для файлов; _ALLOWLIST_LINES — per-line ("rel:lineno") с обоснованием:
##     deploy_orchestrator.py:314 `enabled == "true"` — вход нормализован в
##     secrets_validator.parse_modules_from_node_yaml (str(value).lower()).
##   - Constant на ЛЕВОЙ стороне ("true" == x) — вне скоупа (паттерн не встречается).
##   - Membership-тесты (`in ("true",)`) — вне скоупа.
## @rationale DevPlan 123 T6: node_yaml CLI возвращал Python-bool "True" → нормализация .lower()
##            размазана по потребителям, строгие сравнения ломались (TRAP[BUG] node-lifecycle.sh:53
##            2026-08-03, RC 121 прод). Единая точка нормализации в источнике (CLI) + гейт
##            не дают дрейфу вернуться; dataflow-часть закрывает легитимный паттерн
##            «нормализация на входе функции» без ложных RED.
## @changes 2026-08-03 | DevPlan 123 T6 — Created
# endregion MODULE_CONTRACT

import ast
import logging
import textwrap

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_DIR = ROOT / "core"

# Строгие булевы строковые литералы (== / !=) — все 8 комбинаций регистра/оператора.
_STRICT_BOOL_LITERALS = {"true", "True", "false", "False"}

# ⚠️ allowlist — per-line (НЕ per-file; файловый allowlist пуст). Ключ: "<rel-к-core>:<lineno>".
# Каждая запись требует обоснование-комментарий. Сжимается волнами — новые записи RED.
# deploy_orchestrator.py:315: `enabled == "true"` — вход УЖЕ нормализован в
# parse_modules_from_node_yaml (DevPlan 123 T6). Line 315 (не 314) после W9 (136):
# удалена import-time константа _HC_DONE_MARKER (per-context резолв в call-time, T9.19).
_ALLOWLIST_LINES: set[str] = {
    "internal/bootstrap/deploy/deploy_orchestrator.py:315",  # input normalized by parse_modules_from_node_yaml
}


def _contains_lower(node: ast.AST) -> bool:
    """Проверить, что AST-поддерево содержит вызов `.lower()` (нормализация в выражении)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "lower":
            return True
    return False


def _collect_from_stmt(stmt: ast.stmt, result: dict[str, int]) -> None:
    """Собрать Name-цели, присвоенные из .lower()-содержащего выражения, в result (name → lineno).

    ## @purpose  Не спускается в вложенные FunctionDef/AsyncFunctionDef (скоуп module-level —
    ##            Name на module level не видит function-locals; внутри функции — весь подскоп).
    """
    for node in ast.walk(stmt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if not targets or value is None or not _contains_lower(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and (t.id not in result or node.lineno < result[t.id]):
                result[t.id] = node.lineno


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """Найти ближайший охватывающий FunctionDef/AsyncFunctionDef (или None для module-level)."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
        cur = parents.get(cur)
    return None


def _scope_normalized(compare: ast.Compare, parents: dict[ast.AST, ast.AST], tree: ast.Module) -> dict[str, int]:
    """Собрать {name: earliest_lineno} .lower()-нормализованных имён в скоупе сравнения.

    ## @purpose  Внутри функции — весь подскоп функции; на module-level — только statements
    ##            вне функций (Python Name resolution: function-locals не видны на module level).
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


def _left_is_normalized(left: ast.expr, compare_lineno: int, normalized: dict[str, int]) -> bool:
    """Левая часть сравнения нормализована? (inline .lower() ИЛИ Name с .lower()-присваиванием до сравнения)."""
    if _contains_lower(left):
        return True
    if isinstance(left, ast.Name):
        assign_line = normalized.get(left.id)
        if assign_line is not None and assign_line < compare_lineno:
            return True
    return False


def _find_offenders(root: "object | None" = None) -> list[tuple[str, int, str]]:
    """Найти строгие булевы строковые сравнения (== / != "true"/"True"/"false"/"False") в core/**/*.py.

    ▶ ┌core/**/*.py┐ → ○ AST parse → ○ walk ast.Compare → ◇ Constant-строка справа ∈ set + Eq/NotEq
      → ◇ per-line allowlist? → ◇ левая часть нормализована (.lower() inline / Name dataflow)? → ⊕ offenders → ⎋ list
    ## @purpose  DevPlan 123 T6: гейт булевой нормализации. Пути — ОТНОСИТЕЛЬНО core/ (сканируемый
    ##            корень). Параметр root (по образцу timeout_literals 119 H): R5-тесты сканируют
    ##            probe во tmp_path — Zero Hardcode Rule, рабочее дерево не загрязняется.
    """
    base = _CORE_DIR if root is None else root
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(base.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).as_posix()
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        parents: dict[ast.AST, ast.AST] = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare) or not node.comparators:
                continue
            if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                continue
            const = node.comparators[0]
            if not (
                isinstance(const, ast.Constant)
                and isinstance(const.value, str)
                and const.value in _STRICT_BOOL_LITERALS
            ):
                continue
            key = f"{rel}:{node.lineno}"
            if key in _ALLOWLIST_LINES:
                continue
            if _left_is_normalized(node.left, node.lineno, _scope_normalized(node, parents, tree)):
                continue
            offenders.append((rel, node.lineno, ast.unparse(node).strip()))
    return offenders


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · REGRESSION · строгие булевы сравнения в core/ запрещены (DevPlan 123 T6)
# · Scenario: AST-скан core/**/*.py — `== "true"` / `!= "true"` / `== "True"` / `== "false"` без
# ·   .lower()-нормализации (inline или Name-присваивание) → RED; allowlist per-line пуст кроме
# ·   deploy_orchestrator.py:314 (вход нормализован в parse_modules_from_node_yaml)
# · Last fail: secrets_manager.py:166/575 (tor_enabled), scaffold_helpers.py:156 (database != "false"),
# ·   deploy_orchestrator.py:970-971 (args.deploy_parallel/orchestrator == "true") — зачищены волной T6
# · Remove if: гейт булевой нормализации отменяется
def test_no_strict_bool_string_comparisons(caplog) -> None:
    """0 строгих сравнений с булевыми строковыми литералами без .lower()-нормализации в core/ (T6)."""
    caplog.set_level(logging.INFO)
    offenders = _find_offenders()
    if offenders:
        for rel, lineno, expr in offenders:
            logger.error("[IMP:10][bool_string_literals] %s:%d %s", rel, lineno, expr)
        pytest.fail(
            f"Строгие булевы строковые сравнения без .lower()-нормализации ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} {expr}" for rel, lineno, expr in offenders)
            + "\n\nЕдиная точка нормализации: node_yaml CLI (_format_cli_value, DevPlan 123 T6). "
            "Сравнивай только через .lower()/нормализованный вход: `(x or '').lower() == 'true'` "
            "или `x = (x or '').lower()` на входе функции."
        )

    logger.info("[IMP:9][bool_string_literals] PASS: 0 strict bool-string comparisons in core/")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · NEGATIVE (R5) · не-нормализованное `enabled == "true"` детектится (T6)
# · Scenario: probe-файл в tmp_path (Zero Hardcode Rule — рабочее дерево не загрязняется) с
# ·   `if enabled == "true":` → сканер ловит (исходный вход: node-lifecycle.sh TRAP 2026-08-03 —
# ·   node_yaml CLI возвращал Python-bool "True", строгие сравнения ломались)
# · Last fail: deploy_orchestrator.py:309 enabled == "true" без видимой нормализации (T6)
# · Remove if: гейт булевой нормализации отменяется
def test_r5_negative_strict_bool_comparison_detected(caplog, tmp_path) -> None:
    """R5 negative: `if enabled == "true":` (без нормализации) детектируется."""
    caplog.set_level(logging.INFO)
    probe = tmp_path / "_gate_probe_bool.py"
    probe.write_text(
        textwrap.dedent(
            """\
            def deploy(enabled):
                if enabled == "true":
                    return "deploy"
                return "skip"
            """
        )
    )
    try:
        hits = [(rel, ln, expr) for rel, ln, expr in _find_offenders(root=tmp_path) if "_gate_probe_bool" in rel]
        assert hits, 'R5 FAIL: strict `enabled == "true"` (исходный вход T6) не обнаружен'
        logger.info("[IMP:9][bool_string_literals][R5] PASS: probe %s:%d %s detected", *hits[0])
    finally:
        probe.unlink(missing_ok=True)


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-03 · POSITIVE CONTROL · нормализованные сравнения НЕ детектятся (T6)
# · Scenario: probe-файл в tmp_path с тремя нормализованными паттернами: inline .lower(),
# ·   (x or "").lower() и Name, нормализованный присваиванием на входе функции → 0 offenders
# · Last fail: N/A (control-тест — доказывает, что dataflow-часть не даёт ложных RED)
# · Remove if: гейт булевой нормализации отменяется
def test_normalized_comparisons_not_detected(caplog, tmp_path) -> None:
    """PASS-контроль: `.lower()`-нормализованные сравнения (inline + вход функции) не RED."""
    caplog.set_level(logging.INFO)
    probe = tmp_path / "_gate_probe_bool_ok.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import os

            def cleanup(tor_enabled):
                tor_enabled = (tor_enabled or "").strip().lower()
                if tor_enabled != "true":
                    return "strip"
                return "keep"

            def check():
                if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
                    return 1
                if (os.environ.get("PARALLEL") or "").lower() == "true":
                    return 2
                return 0
            """
        )
    )
    try:
        hits = [(rel, ln, expr) for rel, ln, expr in _find_offenders(root=tmp_path) if "_gate_probe_bool_ok" in rel]
        assert not hits, f"PASS-control FAIL: нормализованные сравнения ошибочно RED: {hits}"
        logger.info(
            "[IMP:9][bool_string_literals][control] PASS: normalized comparisons (inline .lower() + "
            "entry-normalized Name) not flagged"
        )
    finally:
        probe.unlink(missing_ok=True)
