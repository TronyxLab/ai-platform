#!/usr/bin/env python3
# GREP_SUMMARY: gate sys-exit-contract main-int importability SystemExit U-29 anti-drift AST-scan
# STRUCTURE: ▶ AST-обход core/internal/*.py → ◇ ast.Call sys.exit|exit? → ◇ родитель def main() / __main__-блок? → ◇ def main() -> None? → ⊕ violations → ⎋ PASS/RED
# region MODULE_CONTRACT
## @purpose  Gate sys.exit-контракта (DevPlan 116 B4 T6, AC2/AC3, U-29): `sys.exit` в
##           core/internal встречается ТОЛЬКО внутри `def main(...)` тела ИЛИ в
##           `if __name__ == "__main__":` блоке. Business-функции НЕ вызывают sys.exit —
##           они raise (PlatformError-иерархия) или return exit-code. Дополнительно:
##           `def main() -> None` → RED (контракт D3: все main() -> int).
## @scope    Все core/internal/**/*.py. Allowlist пуст (после T3/T4); state_machine.py
##           (мораторий B9) уже соответствует контракту — файл чист.
## @invariants
##   - sys.exit разрешён только: внутри тела функции с именем main, ИЛИ внутри
##     `if __name__ == "__main__":` блока (по прямой цепочке родителей).
##   - `def main() -> None` → RED (должно быть -> int + sys.exit(main()) в __main__).
##   - os._exit — не sys.exit, не триггерит (child-процессы docker_orchestrator).
## @rationale U-29: sys.exit живёт в библиотечных функциях (provisioner:154, deploy_engine:953) —
##            caller не может программно обработать. Контракт: exit только на границе CLI.
## @changes 2026-08-01 | DevPlan 116 B4 T6 — Created
# endregion MODULE_CONTRACT

import ast
import logging

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"


def _is_sys_exit_call(node: ast.Call, imported_sys_exit: set[int]) -> bool:
    """True if the call is sys.exit(...) or bare exit(...) imported from sys.

    ▶ ┌call node┐ → ◇ func Attribute sys.exit? → ◇ Name exit ∈ imported_sys_exit (id-узлов)? → ⎋ bool
    """
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "sys" and func.attr == "exit":
        return True
    return isinstance(func, ast.Name) and func.id == "exit" and id(func) in imported_sys_exit


def _collect_imported_sys_exit(tree: ast.AST) -> set[int]:
    """Collect Name nodes for `exit` imported via `from sys import exit`.

    ▶ ┌tree┐ → ○ walk ImportFrom (module=="sys", alias=="exit") → ⊕ id(Name) узлов → ⎋ set
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sys":
            for alias in node.names:
                if alias.name == "exit":
                    # собрать все Name-узлы с именем exit в дереве (приближение: имя глобально)
                    for n in ast.walk(tree):
                        if isinstance(n, ast.Name) and n.id == "exit":
                            ids.add(id(n))
    return ids


def _inside_main_or_main_block(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """True if node is inside a `def main` body or an `if __name__ == "__main__":` block.

    ▶ ┌node + parent map┐ → ○ подъём по родителям → ◇ FunctionDef name=="main"? → ◇
    │    If test (__name__ == "__main__")? → ⎋ bool
    """
    cur = parents.get(id(node))
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
        cur = parents.get(id(cur))
    return False


def _scan_sys_exit_violations() -> list[tuple[str, int, str]]:
    """Find sys.exit calls outside main()/__main__ and def main() -> None.

    ▶ ┌_CORE_INTERNAL┐ → ○ AST per file → ⊕ sys.exit-вне-границы + main()->None → ⎋ list
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
        parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent

        imported_sys_exit = _collect_imported_sys_exit(tree)
        violations.extend(
            (rel, node.lineno, "sys.exit вне main()/__main__")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _is_sys_exit_call(node, imported_sys_exit)
            and not _inside_main_or_main_block(node, parents)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                ret = node.returns
                if ret is None:
                    violations.append((rel, node.lineno, "def main() без аннотации (контракт D3: -> int)"))
                elif isinstance(ret, ast.Constant) and ret.value is None:
                    violations.append((rel, node.lineno, "def main() -> None (контракт D3: -> int)"))
    return violations


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · Regression · sys.exit only in main()/__main__ + main() -> int (DevPlan 116 B4 T6)
def test_sys_exit_only_in_main_and_main_returns_int(caplog) -> None:
    """sys.exit must live only in main()/__main__; every main() must return int."""
    violations = _scan_sys_exit_violations()
    if violations:
        for rel, lineno, reason in violations:
            logger.error("[IMP:10][sys-exit-contract][RED] %s:%d — %s", rel, lineno, reason)
        pytest.fail(
            f"Нарушения sys.exit-контракта ({len(violations)}):\n"
            + "\n".join(f"  - {rel}:{lineno} — {reason}" for rel, lineno, reason in violations)
            + "\n\nКонтракт D3: business-функции raise PlatformError / return code;"
            " sys.exit — только в main()/__main__; все main() -> int."
        )

    logger.info("[IMP:9][sys-exit-contract][done] PASS: sys.exit только в main()/__main__, все main() -> int")
