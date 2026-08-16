"""
# GREP_SUMMARY: test-test-journal record-run jsonl-append latest-symlink junit-counts git-context-failsafe cli-record cli-latest
# STRUCTURE: ▶ record_run (append/создание каталога/симлинк) → ◇ junit_counts (wrapper/атрибуты) → ◇ git_context (non-git → null) → ◇ CLI record + latest → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/test_journal.py — структурированный журнал
##           тестовых прогонов (DevPlan 165 W1): append JSONL, симлинк latest.log
##           (относительный), статистика из JUnit, fail-safe git-context, CLI record/latest.
## @scope    Native imports (core.internal.shared.test_journal); tmp_path only (Zero Hardcode);
##           НИКАКОЙ записи в реальный .ai/logs/.
## @invariants
##   - tmp_path only — реальный журнал проекта не затрагивается
##   - Каждый тест — фальсифицируемый assert (Test Honesty R1/R2)
##   - LDD: caplog IMP:9 trajectory (декоратор @ldd_trajectory)
##   - Журнальные записи — валидный JSON, append-only (порядок записей сохраняется)
## @rationale Журнал — общая инфраструктура всех тестовых команд (W2/W3) — его контракт
##            (атомарность строки, относительный симлинк, fail-safe git) фиксируется
##            тестами ДО подключения потребителей.
## @changes 2026-08-13 | Created (DevPlan 165 W1)
# endregion MODULE_CONTRACT
"""

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core.internal.shared import test_journal
from core.internal.shared.test_journal import git_context, junit_counts, record_run
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


def _write_junit(path: Path, suite_counts: dict[str, int], time: float = 1.5) -> None:
    """Записать минимальный JUnit XML в tmp_path (фикстура-помощник)."""
    testsuite = ET.Element("testsuite", {k: str(v) for k, v in {**suite_counts, "time": time}.items()})
    tree = ET.ElementTree(testsuite)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · record_run добавляет валидную JSONL-строку
# · Scenario: два прогона подряд → две строки, обе — валидный JSON с обязательными полями
# · Last fail: N/A (new test)
# · Remove if: формат журнала меняется (DevPlan 165 W1)
@ldd_trajectory
def test_record_run_appends_jsonl(caplog, tmp_path: Path) -> None:
    """record_run дважды → 2 валидные JSONL-строки с обязательными полями."""

    record_run(goal="check", exit_code=0, pass_count=3, fail_count=1, journal_dir=tmp_path)
    record_run(goal="gate", exit_code=1, journal_dir=tmp_path)

    runs = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(runs) == 2, "Должно быть ровно 2 записи"

    first = json.loads(runs[0])
    second = json.loads(runs[1])
    for entry in (first, second):
        assert set(entry) == {
            "ts",
            "goal",
            "branch",
            "commit",
            "exit_code",
            "pass",
            "fail",
            "skip",
            "error",
            "duration_s",
            "raw_log",
        }, "Схема записи должна быть стабильной (контракт журнала)"
    assert first["goal"] == "check" and first["exit_code"] == 0
    assert first["pass"] == 3 and first["fail"] == 1
    assert second["goal"] == "gate" and second["exit_code"] == 1
    assert first["ts"] <= second["ts"], "Порядок записей — хронологический (append-only)"


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · latest.log — относительный симлинк на raw-лог
# · Scenario: запись с raw_log → symlink существует и указывает на RELATIVE путь
# · Last fail: N/A (new test)
# · Remove if: механизм latest.log удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_record_run_updates_latest_symlink_relative(caplog, tmp_path: Path) -> None:
    """latest.log — относительный симлинк, обновляется при каждом прогоне с raw_log."""

    record_run(goal="check", exit_code=0, raw_log=str(tmp_path / "a.log"), journal_dir=tmp_path)
    record_run(goal="gate", exit_code=0, raw_log=str(tmp_path / "b.log"), journal_dir=tmp_path)

    latest = tmp_path / "latest.log"
    assert latest.is_symlink(), "latest.log должен быть симлинком"
    target = latest.readlink()
    assert not target.is_absolute(), "Симлинк должен быть ОТНОСИТЕЛЬНЫМ (переживает перенос копии)"
    assert str(target) == Path("b.log").name, "Симлинк указывает на ПОСЛЕДНИЙ raw-лог"


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · запись без raw_log не трогает latest.log
# · Scenario: сначала запись с raw_log, потом без → симлинк остаётся на первый лог
# · Last fail: N/A (new test)
# · Remove if: механизм latest.log удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_record_run_without_raw_log_keeps_symlink(caplog, tmp_path: Path) -> None:
    """Запись без raw_log не должна затирать/ломать существующий latest.log."""

    record_run(goal="check", exit_code=0, raw_log=str(tmp_path / "first.log"), journal_dir=tmp_path)
    record_run(goal="check", exit_code=0, raw_log=None, journal_dir=tmp_path)

    latest = tmp_path / "latest.log"
    assert latest.is_symlink(), "Симлинк должен пережить запись без raw_log"
    assert str(latest.readlink()) == "first.log", "Симлинк указывает на последний ИЗВЕСТНЫЙ raw-лог"


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · каталог журнала создаётся автоматически
# · Scenario: journal_dir не существует → record_run создаёт его
# · Last fail: N/A (new test)
# · Remove if: mkdir-поведение удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_record_run_creates_journal_dir(caplog, tmp_path: Path) -> None:
    """Отсутствующий каталог журнала создаётся при первой записи."""

    nested = tmp_path / "deep" / "journal"
    record_run(goal="check", exit_code=0, journal_dir=nested)
    assert (nested / "runs.jsonl").exists(), "runs.jsonl создан в новом каталоге"


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · JUnit counts считываются с <testsuite>, не wrapper
# · Scenario: XML с <testsuites> wrapper → counts корректны (аналог TRAP[BUG] parse_junit_xml)
# · Last fail: N/A (new test)
# · Remove if: junit_counts удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_junit_counts_reads_testsuite_attrs(caplog, tmp_path: Path) -> None:
    """junit_counts: pass = total − fail − skip − error; duration суммируется."""

    xml_path = tmp_path / "report.xml"
    wrapper = ET.Element("testsuites")
    ET.SubElement(wrapper, "testsuite", {"tests": "10", "failures": "2", "errors": "1", "skipped": "3", "time": "1.25"})
    ET.ElementTree(wrapper).write(xml_path, encoding="utf-8", xml_declaration=True)

    pass_count, fail_count, skip_count, error_count, duration = junit_counts(xml_path)
    assert (pass_count, fail_count, skip_count, error_count) == (4, 2, 3, 1), "Агрегация счётчиков корректна"
    assert duration == pytest.approx(1.25), "Duration суммируется из <testsuite>"
    logger.critical("[IMP:9][test_junit_counts_reads_testsuite_attrs] counts агрегированы с <testsuite>")


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · git_context fail-safe вне git-репозитория
# · Scenario: cwd — tmp_path без .git → (None, None), без исключений
# · Last fail: N/A (new test)
# · Remove if: fail-safe контракт git_context меняется (DevPlan 165 W1)
@ldd_trajectory
def test_git_context_failsafe_outside_repo(caplog, tmp_path: Path) -> None:
    """Вне git-репозитория git_context возвращает (None, None) — прогон не роняется."""

    branch, commit = git_context(cwd=tmp_path)
    assert branch is None and commit is None, "Нет репозитория → нет provenance, но и нет исключения"
    logger.critical("[IMP:9][test_git_context_failsafe_outside_repo] git_context fail-safe подтверждён")


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · CLI record пишет запись через main()
# · Scenario: main(["record", ...]) → exit 0 + JSONL-строка в указанном каталоге
# · Last fail: N/A (new test)
# · Remove if: CLI record удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_cli_record_writes_entry(caplog, tmp_path: Path) -> None:
    """CLI record — exit 0, запись появляется в --dir."""

    rc = test_journal.main(["record", "--goal", "test-node", "--exit-code", "1", "--dir", str(tmp_path)])
    assert rc == 0, "CLI record возвращает 0"
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["goal"] == "test-node"


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · CLI latest на пустом журнале — exit 0
# · Scenario: журнала нет → сообщение и exit 0 (не блокирует агента)
# · Last fail: N/A (new test)
# · Remove if: CLI latest удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_cli_latest_empty_journal(caplog, tmp_path: Path) -> None:
    """CLI latest без журнала — exit 0 с сообщением."""

    rc = test_journal.main(["latest", "--dir", str(tmp_path)])
    assert rc == 0, "Отсутствие журнала — не ошибка для чтения"
    logger.critical("[IMP:9][test_cli_latest_empty_journal] пустой журнал не блокирует чтение")


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · CLI record с --junit извлекает статистику
# · Scenario: junit с 6/1/1/1 → запись с pass=4 fail=2 error=1 skip=1
# · Last fail: N/A (new test)
# · Remove if: --junit опция удаляется (DevPlan 165 W1)
@ldd_trajectory
def test_cli_record_junit_stats(caplog, tmp_path: Path) -> None:
    """CLI record --junit — статистика берётся из XML."""

    xml_path = tmp_path / "report.xml"
    _write_junit(xml_path, {"tests": 8, "failures": 2, "errors": 1, "skipped": 1}, time=0.75)

    rc = test_journal.main([
        "record",
        "--goal",
        "test-node",
        "--exit-code",
        "1",
        "--junit",
        str(xml_path),
        "--dir",
        str(tmp_path),
    ])
    assert rc == 0
    entry = json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert (entry["pass"], entry["fail"], entry["skip"], entry["error"]) == (4, 2, 1, 1)
    assert entry["duration_s"] == 0.8, "Duration округляется до 1 знака"
