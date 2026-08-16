# GREP_SUMMARY: gate ssh-command-parser-sole single-test-file no-duplicate R5
# STRUCTURE: ▶ glob tests/*ssh_command_parser*.py → ◇ ровно ОДИН тестовый файл (test_ssh_command_parser.py) → ◇ дубль test_shared_ssh_command_parser.py → RED → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Sole-test gate (DevPlan 119 C5, R5): для модуля ssh_command_parser существует
##           РОВНО один тестовый файл — tests/unit/test_ssh_command_parser.py (канон после
##           переноса модуля в core/internal/deploy/ волной 118 D3). Дубль
##           tests/unit/test_shared_ssh_command_parser.py удалён (AUDIT-5 DUP-1).
## @scope    Скан tests/ на test-файлы с ssh_command_parser в имени.
## @invariants
##   - Единственный тестовый файл: tests/unit/test_ssh_command_parser.py
##   - Появление второго файла (*ssh_command_parser*) → RED (R5: регрессия дубля C5)
## @rationale DevPlan 119 C5 (AUDIT-5 DUP-1): 13 записей inventory дубля удалены;
##            канонический тест жив (AC-C5.2). Гейт структурно запрещает новый дубль.
## @changes 2026-08-02 | Created per DevPlan 119 C5 $TEST_SPEC (test_gate_ssh_command_parser_sole.py)
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CANONICAL_TEST = pathlib.Path("tests/unit/test_ssh_command_parser.py")


def _find_ssh_parser_test_files() -> list[pathlib.Path]:
    """All NON-gate test files matching *ssh_command_parser* under tests/ (C5: дубль-детекция)."""
    files: list[pathlib.Path] = []
    for p in sorted((ROOT / "tests").rglob("*ssh_command_parser*.py")):
        if "__pycache__" in p.parts:
            continue
        if p.parent.name == "gates":
            continue  # сам файл-детектор (test_gate_ssh_command_parser_sole.py)
        files.append(p)
    return files


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · только один тестовый файл ssh_command_parser (C5)
# · Scenario: DevPlan 119 C5 — дубль test_shared_ssh_command_parser.py (299 LOC, 13 записей
#   inventory) удалён; канон — test_ssh_command_parser.py (модуль переехал в deploy/ в 118 D3)
# · Last fail: до C5 — 2 тестовых файла для одного модуля (AUDIT-5 DUP-1)
# · Remove if: тестовая структура намеренно расширена (одобренный amendment)
def test_single_test_file_negative(caplog) -> None:
    """R5 negative: РОВНО один тестовый файл для ssh_command_parser (дубль → RED)."""
    files = _find_ssh_parser_test_files()
    rels = [f.relative_to(ROOT).as_posix() for f in files]

    assert rels == [_CANONICAL_TEST.as_posix()], (
        f"R5 FAIL (C5): ожидался ровно один тестовый файл {_CANONICAL_TEST}, "
        f"найдено {rels} — дубль test_shared_ssh_command_parser.py удалён (AUDIT-5 DUP-1)"
    )

    # Канонический тест существует и импортирует модуль из deploy/ (118 D3)
    canonical = ROOT / _CANONICAL_TEST
    assert canonical.exists(), f"канонический тест отсутствует: {_CANONICAL_TEST}"
    content = canonical.read_text(errors="replace")
    assert "core.internal.deploy.ssh_command_parser" in content, (
        "канонический тест должен импортировать core.internal.deploy.ssh_command_parser (118 D3)"
    )

    logger.critical("[IMP:9][ssh_parser][sole] PASS — единственный тест: %s (канон жив)", _CANONICAL_TEST)
