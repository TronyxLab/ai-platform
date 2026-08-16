# GREP_SUMMARY: gate stub-detection-sole is_stub_ai_platform_yaml shared-stub-detection no-inline-reimplementation R5 C4
# STRUCTURE: ▶ scan core/ + tests/ for "is_stub_ai_platform_yaml" → ◇ def только в shared/stub_detection.py → ◇ каждый потребитель импортирует из shared → ◇ inline-reimplementation → RED → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Sole-path gate (DevPlan 119 C4, R5): is_stub-детекция имеет РОВНО одну
##           реализацию — core/internal/shared/stub_detection.py::is_stub_ai_platform_yaml
##           (DevPlan 116 B9 T4, U-28). Запрещает тестовые inline-копии (bash _is_stub /
##           собственные def) — регрессия удалённого tests/test_stub_detection.py
##           (тестировал сам себя через inline-bash копию).
## @scope    Скан core/ + tests/ (*.py): определения is_stub_ai_platform_yaml вне shared →
##           RED; потребители символа без импорта из shared/stub_detection → RED.
## @invariants
##   - is_stub_ai_platform_yaml ОПРЕДЕЛЯЕТСЯ только в core/internal/shared/stub_detection.py
##   - Любой файл, использующий символ, импортирует его из core.internal.shared.stub_detection
##     (тонкие обёртки-реэкспорты допустимы — reconciler_projects.is_stub_project)
##   - GENERATED-STUB как ВХОДНЫЕ данные тестов (test_stub_detection_shared.py) — НЕ триггер;
##     триггер — собственное определение функции / inline bash _is_stub с grep GENERATED-STUB
## @rationale DevPlan 119 C4 (AUDIT-5 DUP-2): тест тестировал сам себя. Гейт структурно
##            запрещает возврат inline-реализаций — единая точка правды shared/stub_detection.
## @changes 2026-08-02 | Created per DevPlan 119 C4 $TEST_SPEC (test_gate_stub_detection_sole.py)
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_ALLOWED_FILE = pathlib.Path("core/internal/shared/stub_detection.py")
_SYMBOL = "is_stub_ai_platform_yaml"

# inline bash-копия (как в удалённом test_stub_detection.py): функция _is_stub с grep GENERATED-STUB
_INLINE_BASH_STUB_RE = re.compile(r"_\w*is_stub\w*\(\)\s*\{[\s\S]*?grep\s+-q\s+\"?GENERATED-STUB")


def _scan_py_files() -> list[pathlib.Path]:
    """All .py files under core/ and tests/ (excluding __pycache__ и сам файл-детектор)."""
    self_rel = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()
    files: list[pathlib.Path] = []
    for base in (ROOT / "core", ROOT / "tests"):
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            if p.relative_to(ROOT).as_posix() == self_rel:
                continue  # детектор сам упоминает символ в docstring — не «потребитель»
            files.append(p)
    return files


def _find_stub_definitions_outside_shared() -> list[tuple[str, int]]:
    """def is_stub_ai_platform_yaml вне shared/stub_detection.py → RED (реализация-дубль)."""
    offenders: list[tuple[str, int]] = []
    for p in _scan_py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # def <symbol>( ...  (не в комментарии/docstring)
            if re.match(rf"^def\s+{_SYMBOL}\s*\(", stripped):
                offenders.append((rel, i))
    return offenders


def _find_stub_usage_without_shared_import() -> list[tuple[str, int]]:
    """Файл использует символ, но НЕ импортирует его из shared/stub_detection → RED."""
    offenders: list[tuple[str, int]] = []
    for p in _scan_py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            text = p.read_text(errors="replace")
            lines = text.splitlines()
        except OSError:
            continue
        if _SYMBOL not in text:
            continue
        imports_from_shared = any(
            line.strip().startswith("from core.internal.shared.stub_detection import")
            or line.strip().startswith("import core.internal.shared.stub_detection")
            for line in lines
            if not line.strip().startswith("#")
        )
        if imports_from_shared:
            continue
        for i, line in enumerate(lines, 1):
            if _SYMBOL in line and not line.strip().startswith("#"):
                offenders.append((rel, i))
    return offenders


def _find_inline_bash_stub_copies() -> list[tuple[str, int]]:
    """Inline bash _is_stub() с grep GENERATED-STUB в тестах → RED (self-testing паттерн C4)."""
    offenders: list[tuple[str, int]] = []
    for p in _scan_py_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for m in _INLINE_BASH_STUB_RE.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            offenders.append((rel, lineno))
    return offenders


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · is_stub определён только в shared (C4)
# · Scenario: DevPlan 119 C4 — единая реализация stub-детекции (U-28)
# · Last fail: N/A (preventive — новый sole-гейт)
# · Remove if: stub-детекция намеренно перенесена из shared
def test_stub_detection_sole_definition(caplog) -> None:
    """is_stub_ai_platform_yaml определяется ТОЛЬКО в shared/stub_detection.py."""
    offenders = _find_stub_definitions_outside_shared()
    if offenders:
        for rel, lineno in offenders:
            logger.error("[IMP:10][stub][def] %s:%d — определение вне shared", rel, lineno)
        pytest.fail(
            f"is_stub_ai_platform_yaml определён вне shared/stub_detection.py ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in offenders)
            + "\n\nЕдиная реализация: core/internal/shared/stub_detection.py (B9 T4, U-28)."
        )
    logger.info("[IMP:9][stub][def] PASS: is_stub_ai_platform_yaml определён только в shared/")
    assert (ROOT / _ALLOWED_FILE).exists(), "shared/stub_detection.py должен существовать"


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · потребители импортируют из shared (C4)
# · Scenario: тест-файл с СОБСТВЕННОЙ реализацией (как удалённый test_stub_detection.py)
#   детектируется → RED (R5 anti-survivorship)
# · Last fail: до C4 — tests/test_stub_detection.py содержал inline-bash копию _is_stub
# · Remove if: инлайн-реализации запрещены структурно иначе
def test_stub_detection_sole_import_negative(caplog) -> None:
    """R5 negative: использование символа без импорта из shared → RED; inline bash копия → RED."""
    # (1) Файл, использующий символ без импорта из shared
    usage = _find_stub_usage_without_shared_import()
    if usage:
        for rel, lineno in usage:
            logger.error("[IMP:10][stub][import] %s:%d — использование без импорта из shared", rel, lineno)
        pytest.fail(
            f"is_stub_ai_platform_yaml используется без импорта из shared/stub_detection "
            f"({len(usage)}):\n" + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in usage)
        )
    logger.info("[IMP:9][stub][import] PASS: все потребители импортируют из shared/")

    # (2) Inline bash _is_stub копии (само-тестирующий паттерн удалённого файла C4)
    copies = _find_inline_bash_stub_copies()
    if copies:
        for rel, lineno in copies:
            logger.error("[IMP:10][stub][inline] %s:%d — inline bash _is_stub копия", rel, lineno)
        pytest.fail(
            f"Inline bash _is_stub() копии найдены ({len(copies)}):\n"
            + "\n".join(f"  - {rel}:{lineno}" for rel, lineno in copies)
            + "\n\nИспользуй core.internal.shared.stub_detection.is_stub_ai_platform_yaml (C4, U-28)."
        )
    logger.info("[IMP:9][stub][inline] PASS: 0 inline bash _is_stub копий в core/ + tests/")
