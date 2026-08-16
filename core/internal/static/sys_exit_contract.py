"""Sys-exit-contract detector — sys.exit only in main()/__main__, main() -> int (DevPlan 163 W-C).

# GREP_SUMMARY: static sys-exit-contract sys.exit main-int importability U-29 AST-scan __main__-block
# STRUCTURE: ▶ AST-обход core/internal/**/*.py → ◇ ast.Call sys.exit|exit(from sys)? → ◇ в main()/__main__?
#            → ⊕ Finding | ◇ def main() -> None|без аннотации? → ⊕ Finding → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Детектор sys.exit-контракта (DevPlan 163 W-C C3; порт
##           tests/gates/test_gate_sys_exit_contract.py, DevPlan 116 B4 T6, AC2/AC3, U-29):
##           `sys.exit` в core/internal встречается ТОЛЬКО внутри `def main(...)` тела ИЛИ
##           в `if __name__ == "__main__":` блоке. Business-функции НЕ вызывают sys.exit —
##           они raise (PlatformError-иерархия) или return exit-code. Дополнительно:
##           `def main() -> None` / без аннотации → RED (контракт D3: все main() -> int).
## @scope    AST-скан всех core/internal/**/*.py. Allowlist пуст (после T3/T4).
## @invariants
##   - sys.exit разрешён только: внутри тела функции с именем main, ИЛИ внутри
##     `if __name__ == "__main__":` блока (по прямой цепочке родителей)
##   - `def main() -> None` / без аннотации → RED (должно быть -> int + sys.exit(main()) в __main__)
##   - os._exit — не sys.exit, не триггерит (child-процессы docker_orchestrator)
##   - `exit` импортированный из sys (`from sys import exit`) — тот же запрет
##   - `changed`: при --changed сканируются только изменённые файлы
## @rationale U-29: sys.exit жил в библиотечных функциях (provisioner:154, deploy_engine:953) —
##            caller не мог программно обработать. Контракт: exit только на границе CLI.
## @changes 2026-08-13 | DevPlan 163 W-C C3 — Created (порт B4 T6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import ast
import logging
from pathlib import Path

from core.internal.static.finding import Finding

logger = logging.getLogger(__name__)


# region FUNC_is_sys_exit_call
def _is_sys_exit_call(node: ast.Call, imported_sys_exit: set[int]) -> bool:
    """Call — sys.exit(...) или голый exit(...), импортированный из sys.

    ## @purpose  Attribute-форма (sys.exit) и Name-форма (exit из `from sys import exit`).
    ## @io       ⇥ node: ast.Call, imported_sys_exit: set[int] → ⎋ bool
    ## @complexity  O(1)
    """
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
        and func.attr == "exit"
    ):
        return True
    return isinstance(func, ast.Name) and func.id == "exit" and id(func) in imported_sys_exit


# endregion FUNC_is_sys_exit_call


# region FUNC_collect_imported_sys_exit
def _collect_imported_sys_exit(tree: ast.AST) -> set[int]:
    """Собрать Name-узлы `exit`, импортированного через `from sys import exit`.

    ## @purpose  Приближение: все Name-узлы с именем exit в дереве помечаются
    ##           (глобальное имя в модуле). Согласовано с гейтом.
    ## @io       ⇥ tree: ast.AST → ⎋ set[int] id(Name-узлов)
    ## @complexity  O(N) — AST-узлы
    """
    has_sys_exit_import = any(
        isinstance(node, ast.ImportFrom) and node.module == "sys" and any(alias.name == "exit" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not has_sys_exit_import:
        return set()
    return {id(n) for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "exit"}


# endregion FUNC_collect_imported_sys_exit


# region FUNC_inside_main_or_main_block
def _inside_main_or_main_block(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Узел внутри `def main` тела или `if __name__ == "__main__":` блока?

    ## @purpose  Подъём по родителям; прямой контракт границы CLI (U-29).
    ## @io       ⇥ node: ast.AST, parents: dict[ast.AST, ast.AST] → ⎋ bool
    ## @complexity  O(D) — глубина дерева
    """
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.FunctionDef) and cur.name == "main":
            return True
        if isinstance(cur, ast.If):
            test = cur.test
            if (
                isinstance(test, ast.Compare)
                and len(test.comparators) == 1
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                return True
        cur = parents.get(cur)
    return False


# endregion FUNC_inside_main_or_main_block


# region FUNC_scan_file
def _scan_file(path: Path, root: Path, changed: set[str] | None) -> list[Finding]:
    """AST-скан одного .py файла на нарушения sys.exit-контракта.

    ## @purpose  Два класса: sys.exit вне границы + def main() без -> int.
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
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    imported_sys_exit = _collect_imported_sys_exit(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _is_sys_exit_call(node, imported_sys_exit)
            and not _inside_main_or_main_block(node, parents)
        ):
            findings.append(
                Finding(
                    rule="sys-exit-contract",
                    file=rel,
                    line=node.lineno,
                    message="sys.exit outside main()/__main__ — business functions raise/return, exit only at CLI boundary",
                )
            )
            logger.warning("[IMP:9][sys_exit_contract][RED] %s:%d sys.exit outside main()/__main__", rel, node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            ret = node.returns
            if ret is None:
                findings.append(
                    Finding(
                        rule="sys-exit-contract",
                        file=rel,
                        line=node.lineno,
                        message="def main() without return annotation (contract D3: -> int)",
                    )
                )
                logger.warning("[IMP:9][sys_exit_contract][RED] %s:%d main() без аннотации", rel, node.lineno)
            elif isinstance(ret, ast.Constant) and ret.value is None:
                findings.append(
                    Finding(
                        rule="sys-exit-contract",
                        file=rel,
                        line=node.lineno,
                        message="def main() -> None (contract D3: -> int)",
                    )
                )
                logger.warning("[IMP:9][sys_exit_contract][RED] %s:%d main() -> None", rel, node.lineno)
    return findings


# endregion FUNC_scan_file


# region FUNC_detect
def detect(root: Path, changed: set[str] | None = None) -> list[Finding]:
    """Найти sys.exit вне границы и def main() без -> int.

    # ▶ ┌core/internal/**/*.py┐ → ○ walk ast.Call/FunctionDef → ◇ граница/аннотация → ⊕ Findings → ⎋

    ## @purpose  Главный вход детектора (registry) — правило B4 T6 (U-29).
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
    logger.info("[IMP:9][sys_exit_contract] Scanned %d file(s), findings=%d", len(files), len(findings))
    if not findings:
        logger.info("[IMP:9][sys_exit_contract] PASS: sys.exit только в main()/__main__, все main() -> int")
    return findings


# endregion FUNC_detect
