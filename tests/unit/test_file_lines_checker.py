# GREP_SUMMARY: test-file-lines-checker line-limit scan wc-l warning non-blocking max-lines
# STRUCTURE: ▶ tmp fixtures → ◇ count_lines (byte-parity wc -l) → ◇ discover_files (exclusions) → ◇ scan (warning count) → ◇ main (exit 0) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/lint/file_lines_checker.py (DevPlan 173 W2.2 —
##           Python-порт check-file-lines.sh). Native imports, tmp_path fixtures, DI (count_fn).
## @scope    Pure unit tests — no Docker, no subprocess.
## @invariants
##   - count_lines: число `\n` (byte-parity с `wc -l`)
##   - discover_files: расширения .py/.sh/.yml/.yaml/.json/.md; исключения .venv/node_modules/__pycache__
##   - scan: возвращает число файлов > max_lines (non-blocking); main всегда exit 0
## @rationale find+wc-цикл (shell) → Python rglob — тестируемость сканера.
## @changes  2026-08-16 | DevPlan 173 W2.2 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.lint import file_lines_checker as flc

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region FUNC_test_count_lines
## @purpose — count_lines = число `\n` (byte-parity с `wc -l < file`).
def test_count_lines(tmp_path: Path) -> None:
    """count_lines returns newline count (wc -l parity)."""
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    assert flc.count_lines(f) == 3

    # Нет trailing newline → всё равно 2 перевода строки
    f2 = tmp_path / "b.py"
    f2.write_text("x\ny", encoding="utf-8")
    assert flc.count_lines(f2) == 1


# endregion FUNC_test_count_lines


# region FUNC_test_discover_files
## @purpose — discover_files: target-расширения + исключения .venv/node_modules/__pycache__.
def test_discover_files(tmp_path: Path) -> None:
    """discover_files returns only target extensions, excluding .venv/node_modules/__pycache__."""
    core = tmp_path / "core"
    core.mkdir()
    (core / "a.py").write_text("x\n", encoding="utf-8")
    (core / "b.sh").write_text("x\n", encoding="utf-8")
    (core / "c.yaml").write_text("x\n", encoding="utf-8")
    (core / "d.txt").write_text("x\n", encoding="utf-8")  # not scanned
    (core / ".venv").mkdir()
    (core / ".venv" / "e.py").write_text("x\n", encoding="utf-8")
    (core / "node_modules").mkdir()
    (core / "node_modules" / "f.py").write_text("x\n", encoding="utf-8")
    (core / "__pycache__").mkdir()
    (core / "__pycache__" / "g.py").write_text("x\n", encoding="utf-8")

    files = flc.discover_files(core)
    names = {p.name for p in files}
    assert names == {"a.py", "b.sh", "c.yaml"}, f"Unexpected files: {names}"


# endregion FUNC_test_discover_files


# region FUNC_test_scan_warning_count
## @purpose — scan: возвращает число файлов > max_lines; DI count_fn.
def test_scan_warning_count(tmp_path: Path) -> None:
    """scan returns count of files exceeding max_lines (DI count_fn)."""
    core = tmp_path / "core"
    core.mkdir()
    (core / "big.py").write_text("x\n" * 10, encoding="utf-8")
    (core / "small.py").write_text("x\n" * 2, encoding="utf-8")

    # DI: count_fn возвращает фиксированное число строк (не читая файл)
    def _fake_count(_path: Path) -> int:
        return 10 if _path.name == "big.py" else 2

    assert flc.scan(core, max_lines=5, count_fn=_fake_count) == 1
    assert flc.scan(core, max_lines=20, count_fn=_fake_count) == 0


# endregion FUNC_test_scan_warning_count


# region FUNC_test_main_exit_zero
## @purpose — main: всегда exit 0 (non-blocking контракт DevPlan 030 AC5), даже с превышением.
def test_main_exit_zero(tmp_path: Path) -> None:
    """main() returns 0 even when files exceed the limit (non-blocking)."""
    core = tmp_path / "core"
    core.mkdir()
    (core / "big.py").write_text("x\n" * 100, encoding="utf-8")

    assert flc.main(["--core-dir", str(core), "--max-lines", "10"]) == 0

    # Невалидный --max-lines → exit 1
    assert flc.main(["--core-dir", str(core), "--max-lines", "0"]) == 1


# endregion FUNC_test_main_exit_zero
