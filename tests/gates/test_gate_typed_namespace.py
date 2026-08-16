# GREP_SUMMARY: gate typed-namespace argparse Namespace class-attr default hasattr bug W11 anti-regression
# STRUCTURE: ▶ AST-скан core/** → class(<argparse.Namespace>) с AnnAssign+значением → ⟦RED: offenders⟧ → ⎋ PASS; R5-negative: probe-файл с нарушением детектится
# region MODULE_CONTRACT
## @purpose  Гейт против argparse-Namespace class-атрибутов (DevPlan 170 W11 TRAP[BUG]):
##           подкласс argparse.Namespace с аннотированным class-атрибутом СО значением
##           (name: str = "") глушит parser-дефолты: parse_args пропускает setattr дефолта
##           для dest, существующего через hasattr(namespace, dest). Симптом 170 W11:
##           project_remover._RemoverArgs.projects_root: str = "" → --projects-root default
##           (PROJECTS_BASE-резолв) никогда не применялся → «Found 0 node.yaml files»,
##           idempotent-SKIP вместо удаления проекта.
## @scope    AST-скан всех core/**/*.py: ClassDef с базой argparse.Namespace (или любым
##           Name/Attribute, содержащим "Namespace") + AnnAssign с value != None → RED.
## @invariants
##   - Типизированный namespace-паттерн РАЗРЕШЁН в 4 формах: (1) подкласс с голыми
##     аннотациями (name: str, без значения — hasattr=False, argparse ставит дефолты);
##     (2) __init__ с self-аннотациями без значений; (3) Protocol + cast (без runtime-
##     атрибутов); (4) ClassVar-аннотации без значений.
##   - Annotation-only (value is None) — НЕ RED (декларация типа, не атрибут).
## @rationale Баг класса-A (silent wrong behavior, exit 0): 56 файлов W11 ввели паттерн
##            со значениями; регресс-гейт дешевле повторного разбора. Прямое замещение:
##            фикс волны убрал все значения; гейт держит 0 навсегда.
## @changes  2026-08-15 | DevPlan 170 W11 — Created (после регрессии test_project_remover
##                      ×3 и массового отката core/internal)
# endregion MODULE_CONTRACT

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"


def _iter_py_files(root: Path) -> list[Path]:
    """All .py files under root, excluding __pycache__."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))


def _namespace_class_attr_violations(py_file: Path) -> list[str]:
    """Find Namespace-subclass annotated class attrs WITH values (argparse default-killer).

    Returns list of "ClassName.attr" strings for each violation.
    """
    violations: list[str] = []
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return violations
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b) for b in node.bases]
        if not any("Namespace" in b for b in bases):
            continue
        violations.extend(
            f"{node.name}.{sub.target.id}"
            for sub in node.body
            if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name) and sub.value is not None
        )
    return violations


# 🧪 TRAP[TEST] · 2026-08-15 · Regression · argparse Namespace class-attr душит parser-дефолты
# · Scenario: подкласс Namespace с `projects_root: str = ""` → parse_args не применяет
#   default=_DEFAULT_PROJECTS_ROOT → тихий SKIP (exit 0) вместо удаления проекта.
# · Last fail: W11 (make check, test_unregister_removes_project_entry: got 3 проектов)
# · Remove if: типизированный-namespace паттерн исчезнет из кодовой базы
def test_no_namespace_class_attr_defaults(caplog) -> None:
    """Argparse Namespace-subclasses: annotated attrs with values are RED (default-killer)."""
    caplog.set_level(logging.INFO)
    offenders: list[str] = []
    for py_file in _iter_py_files(_CORE_DIR):
        for v in _namespace_class_attr_violations(py_file):
            rel = py_file.relative_to(_CORE_DIR)
            offenders.append(f"{rel}: {v}")
            logger.warning("[IMP:10][typed-namespace] VIOLATION: %s:%s", rel, v)
    assert not offenders, (
        "Namespace-подклассы с class-атрибутами-значениями глушат argparse-дефолты "
        "(TRAP[BUG] 170 W11):\n  " + "\n  ".join(offenders)
    )
    logger.info("[IMP:9][typed-namespace] PASS: 0 Namespace class-attr defaults in core/")


# 🧪 TRAP[TEST] · 2026-08-15 · R5-negative · детектор ловит probe-нарушение
# · Scenario: tmp-файл с class _Args(argparse.Namespace) + `root: str = ""` → violations непусто
# · Last fail: N/A (new gate)
# · Remove if: тест детектора удаляется вместе с позитивом
def test_namespace_class_attr_detector_negative(tmp_path: Path) -> None:
    """R5-negative: детектор находит нарушение в probe-файле."""
    probe = tmp_path / "probe_ns.py"
    probe.write_text(
        'import argparse\n\nclass _Args(argparse.Namespace):\n    root: str = ""\n',
        encoding="utf-8",
    )
    violations = _namespace_class_attr_violations(probe)
    assert violations == ["_Args.root"], f"Детектор должен поймать probe-нарушение, got {violations}"
    logger.info("[IMP:9][typed-namespace][negative] Detector caught %s", violations)
