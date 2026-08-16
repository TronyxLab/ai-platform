#!/usr/bin/env python3
# GREP_SUMMARY: test-journal, shared, runs-jsonl, latest-log-symlink, record-run, git-context, junit-counts, cli-record, cli-latest
# STRUCTURE: ▶ record_run(goal/exit/stats/raw_log) → ◇ junit_counts(path) → ◇ git_context(cwd) → ◇ CLI record --goal/--exit-code/--junit/--raw-log → ◇ CLI latest --lines → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Единый структурированный журнал тестовых прогонов платформы (DevPlan 165 W1).
##           Каждая тестовая команда (make check / check-diff / gate / test-node /
##           e2e-verify / load-test / agent-check) добавляет JSONL-запись в
##           .ai/logs/runs.jsonl и обновляет симлинк .ai/logs/latest.log → raw-лог
##           последнего прогона (logs/make/<ts>-<goal>.log, make-log-shell.sh).
##           Агенты, работающие по одной ветке/плану, читают журнал через симлинк
##           .ai/plans/<NNN>-<slug>/logs → ../../logs (правило artifact-registry).
## @scope    check_suite executor (импорт record_run) + make-таргеты спецкоманд (CLI record).
##           Чтение агентами — CLI latest или cat runs.jsonl.
## @invariants
##   1. Запись — атомарная строка JSONL (одна строка = один прогон); журнал append-only,
##      никогда не переписывается.
##   2. Каталог журнала создаётся при первой записи; симлинк latest.log — ОТНОСИТЕЛЬНЫЙ
##      (переживает перемещение рабочей копии); без raw_log симлинк не трогается.
##   3. branch/commit — через git rev-parse; при отсутствии git/репозитория — null
##      (журналирование НИКОГДА не роняет прогон тестов).
##   4. Модуль — leaf: не импортирует bootstrap/deploy/static и НЕ импортирует
##      core.internal.test_runner (там import shared.exceptions → цикл shared↔test_runner
##      нарушил бы acyclic-internal-domains контракт .importlinter); counts из JUnit
##      считаются локальной junit_counts (см. TRAP[BUG] — iter("testsuite")).
##   5. Журнал содержит только метаданные прогона — никаких секретов/env-значений.
## @rationale Raw-логи всех make-прогонов уже пишутся make-log-shell.sh в logs/make/,
##            но агенты не могут быстро узнать «что прогонял и прошёл ли предыдущий
##            агент» — нет структурированной записи (exit/pass/fail/branch/commit).
##            Решение оператора 2026-08-13: журнал в .ai/logs/ + симлинк в папке
##            активного DevPlan. JSONL выбран за машиночитаемость и append-семантику.
## @changes  2026-08-13 | DevPlan 165 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

logger = logging.getLogger("test_journal")

# region CONSTANTS
DEFAULT_JOURNAL_DIR = ".ai/logs"
RUNS_FILENAME = "runs.jsonl"
LATEST_FILENAME = "latest.log"
_GIT_TIMEOUT_S = 5
# endregion CONSTANTS


# region TYPEDDICT_JournalEntry
# Классовый TypedDict невозможен: ключи "pass"/"error" — зарезервированные слова →
# функциональный синтаксис (W11, DevPlan 170): типизированная json-граница
# для record_run (write) и _cmd_latest (read).
_JournalEntry = TypedDict(
    "_JournalEntry",
    {
        "ts": str,
        "goal": str,
        "branch": str | None,
        "commit": str | None,
        "exit_code": int,
        "pass": int,
        "fail": int,
        "skip": int,
        "error": int,
        "duration_s": float | None,
        "raw_log": str | None,
    },
)
# endregion TYPEDDICT_JournalEntry


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace (W11): зеркало флагов record/latest.
    Аннотации без значений — cast no-op, argparse ставит свои дефолты."""

    command: str
    goal: str
    exit_code: int
    pass_count: int
    fail_count: int
    skip_count: int
    error_count: int
    duration: float
    junit: str
    raw_log: str
    dir: str
    lines: int


# endregion DATACLASS_CliArgs


# region FUNC_git_context
def git_context(cwd: str | os.PathLike[str] | None = None) -> tuple[str | None, str | None]:
    """Определить branch/commit текущей рабочей копии (fail-safe).

    ▶ ┌cwd┐ → ◇ git rev-parse --abbrev-ref HEAD → ◇ git rev-parse --short HEAD → ⎋ (branch, commit)

    ## @purpose — Поле provenance журнальной записи: агенты, работающие по одной ветке,
    ##            различают прогоны друг друга по branch/commit.
    ## @io — ⇥ cwd: путь рабочей копии | None (текущая директория) → ⎋ (branch|None, commit|None)
    ## @complexity — O(1) (2 subprocess-вызова с таймаутом)
    ## @invariants — Любой сбой (нет git, не репозиторий, таймаут) → (None, None);
    ##               исключения не всплывают к вызывающему.
    """
    result: tuple[str | None, str | None] = (None, None)
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:

        def _git(*args: str) -> str | None:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                return None
            out = proc.stdout.strip()
            return out or None

        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        commit = _git("rev-parse", "--short", "HEAD")
        result = (branch, commit)
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.info("[IMP:7][git_context][warn] git unavailable — branch/commit = null")
    return result


# endregion FUNC_git_context


# region FUNC_junit_counts
def junit_counts(path: str | os.PathLike[str]) -> tuple[int, int, int, int, float]:
    """Извлечь (pass, fail, skip, error, duration) из JUnit XML pytest'а.

    ▶ ┌path┐ → ○ iter("testsuite") атрибуты → ⎋ (pass, fail, skip, error, duration)

    ## @purpose — Статистика журнальной записи для команд с --junitxml.
    ## @io — ⇥ path: путь к JUnit XML → ⎋ кортеж из 5 чисел
    ## @complexity — O(S) где S = число <testsuite> элементов
    ## @invariants — Нулевые значения при отсутствии атрибутов; pass вычисляется
    ##              как total − fail − error − skip.
    ## ⚠️ TRAP[BUG] · 2026-08-13 · P2 · Атрибуты читаются с <testsuite>, НЕ с wrapper
    ## · Root: pytest --junitxml оборачивает вывод в <testsuites>; счётчики лежат на
    ## ·   дочерних <testsuite> (аналог TRAP[BUG] в test_runner.parse_junit_xml —
    ## ·   дублирование осознанное: импорт test_runner создал бы цикл shared↔test_runner).
    ## · Prevention: iter("testsuite") — единый паттерн обеих функций агрегации.
    """
    tree = ET.parse(path)  # nosec B314 — pytest-генерируемый локальный артефакт
    root = tree.getroot()
    total = fail = error = skip = 0
    duration = 0.0
    for testsuite in root.iter("testsuite"):
        total += int(testsuite.get("tests", 0))
        fail += int(testsuite.get("failures", 0))
        error += int(testsuite.get("errors", 0))
        skip += int(testsuite.get("skipped", 0))
        duration += float(testsuite.get("time", 0))
    return (total - fail - error - skip, fail, skip, error, duration)


# endregion FUNC_junit_counts


# region FUNC_record_run
def record_run(
    *,
    goal: str,
    exit_code: int,
    pass_count: int = 0,
    fail_count: int = 0,
    skip_count: int = 0,
    error_count: int = 0,
    duration_s: float | None = None,
    branch: str | None = None,
    commit: str | None = None,
    raw_log: str | None = None,
    journal_dir: str | os.PathLike[str] = DEFAULT_JOURNAL_DIR,
) -> Path:
    """Добавить структурированную запись прогона в журнал .ai/logs/runs.jsonl.

    ▶ ┌параметры┐ → ◇ mkdir journal_dir → ◇ git_context (если branch/commit не заданы) →
      ◇ append JSONL-строка → ◇ latest.log → relpath(raw_log) → ⎋ Path(runs.jsonl)

    ## @purpose — Единственная точка записи журнала тестовых прогонов (DevPlan 165 W1).
    ## @io — ⇥ goal/exit_code/статистика/raw_log/journal_dir → ⎋ путь файла журнала
    ## @complexity — O(1) + git_context (2 subprocess)
    ## @invariants — append-only атомарная строка; каталог создаётся; симлинк
    ##              обновляется ТОЛЬКО при заданном raw_log; исключений наружу нет.
    """
    journal_path = Path(journal_dir)
    try:
        journal_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("[IMP:7][record_run][warn] cannot create journal dir %s", journal_path)
        return journal_path / RUNS_FILENAME

    if branch is None or commit is None:
        detected_branch, detected_commit = git_context()
        branch = branch if branch is not None else detected_branch
        commit = commit if commit is not None else detected_commit

    entry: _JournalEntry = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "goal": goal,
        "branch": branch,
        "commit": commit,
        "exit_code": int(exit_code),
        "pass": int(pass_count),
        "fail": int(fail_count),
        "skip": int(skip_count),
        "error": int(error_count),
        "duration_s": round(duration_s, 1) if duration_s is not None else None,
        "raw_log": raw_log,
    }
    runs_path = journal_path / RUNS_FILENAME
    try:
        with runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("[IMP:7][record_run][warn] cannot append journal %s", runs_path)
        return runs_path

    if raw_log:
        latest_path = journal_path / LATEST_FILENAME
        rel_target = os.path.relpath(raw_log, start=journal_path)
        try:
            if latest_path.is_symlink() or latest_path.exists():
                latest_path.unlink()
            latest_path.symlink_to(rel_target)
        except OSError:
            logger.info("[IMP:7][record_run][warn] cannot update latest.log symlink")

    logger.info(
        "[IMP:9][record_run][result] journal: goal=%s exit=%d pass=%d fail=%d branch=%s",
        goal,
        exit_code,
        pass_count,
        fail_count,
        branch,
    )
    return runs_path


# endregion FUNC_record_run


# region FUNC__cmd_record
def _cmd_record(args: _CliArgs) -> int:
    """CLI-ветка record: собрать статистику (флаги | junit) и записать прогон.

    ▶ ┌args┐ → ◇ --junit → junit_counts → ◇ record_run → ⎋ 0

    ## @purpose — Обёртка для make-таргетов спецкоманд (test-node/e2e-verify/load-test/
    ##            agent-check): capture rc → journal record → exit rc.
    ## @io — ⇥ Namespace → ⎋ int (0; ошибка файла junit → 1 с диагностикой)
    ## @complexity — O(1) + парсинг junit
    """
    pass_count, fail_count, skip_count, error_count, duration = 0, 0, 0, 0, None
    if args.junit:
        try:
            pass_count, fail_count, skip_count, error_count, duration = junit_counts(args.junit)
        except (OSError, ET.ParseError) as exc:
            logger.error("[IMP:9][record][error] cannot parse junit %s: %s", args.junit, exc)
            return 1
    if args.duration is not None:
        duration = args.duration
    record_run(
        goal=args.goal,
        exit_code=args.exit_code,
        pass_count=pass_count,
        fail_count=fail_count,
        skip_count=skip_count,
        error_count=error_count,
        duration_s=duration,
        raw_log=args.raw_log,
        journal_dir=args.dir,
    )
    return 0


# endregion FUNC__cmd_record


# region FUNC__cmd_latest
def _cmd_latest(args: _CliArgs) -> int:
    """CLI-ветка latest: таблица последних прогонов для агента.

    ▶ ┌args┐ → ◇ read runs.jsonl → ○ последние N строк → ○ печать таблицы → ⎋ 0

    ## @purpose — Быстрый ответ на вопрос «что прогонял и прошёл ли предыдущий агент».
    ## @io — ⇥ Namespace (--lines, --dir) → ⎋ int (0 всегда; пустой журнал → сообщение)
    ## @complexity — O(N)
    """
    runs_path = Path(args.dir) / RUNS_FILENAME
    if not runs_path.exists():
        print(f"[test_journal] no journal yet: {runs_path} (прогонов не было)")
        return 0
    try:
        lines = runs_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        print(f"[test_journal] cannot read journal: {runs_path}")
        return 1
    if not lines:
        print(f"[test_journal] journal empty: {runs_path}")
        return 0
    print(
        f"{'ts':<20} {'branch':<16} {'goal':<12} {'exit':>4} {'pass':>5} {'fail':>5} {'skip':>5} {'err':>5} {'dur_s':>8}"
    )
    for line in lines[-args.lines :]:
        try:
            # json.loads → Any; TypedDict-граница журнальной записи (W11)
            e = cast(_JournalEntry, json.loads(line))
        except json.JSONDecodeError:
            continue
        print(
            f"{e.get('ts', '')[:19]:<20} {str(e.get('branch', ''))[:15]:<16} {str(e.get('goal', ''))[:11]:<12} "
            f"{e.get('exit_code', '?'):>4} {e.get('pass', 0):>5} {e.get('fail', 0):>5} "
            f"{e.get('skip', 0):>5} {e.get('error', 0):>5} {str(e.get('duration_s', ''))[:8]:>8}"
        )
    return 0


# endregion FUNC__cmd_latest


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: record (журнальная запись) и latest (чтение последних прогонов).

    ▶ ┌argv┐ → ◇ subcommand record|latest → ⎋ exit code (0 | 1 при ошибке junit/чтения)

    ## @purpose — Интерфейс для make-таргетов (record) и агентов (latest).
    ## @io — ⇥ argv: list[str] | None → ⎋ int
    ## @complexity — O(1)
    ## @invariants — sys.exit только здесь; record никогда не маскирует rc прогона
    ##              (make-обёртка передаёт --exit-code и сама возвращает его).
    """
    parser = argparse.ArgumentParser(
        prog="test_journal", description="Структурированный журнал тестовых прогонов (.ai/logs/runs.jsonl)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec_p = sub.add_parser("record", help="Добавить запись прогона")
    rec_p.add_argument("--goal", required=True, help="Имя тестовой команды (check/gate/test-node/...)")
    rec_p.add_argument("--exit-code", type=int, required=True, help="Exit code прогона")
    rec_p.add_argument("--pass", type=int, dest="pass_count", default=0, help="Кол-во passed")
    rec_p.add_argument("--fail", type=int, dest="fail_count", default=0, help="Кол-во failed")
    rec_p.add_argument("--skip", type=int, dest="skip_count", default=0, help="Кол-во skipped")
    rec_p.add_argument("--error", type=int, dest="error_count", default=0, help="Кол-во errors")
    rec_p.add_argument("--duration", type=float, default=None, help="Длительность, сек")
    rec_p.add_argument("--junit", default=None, help="JUnit XML — статистика из файла")
    rec_p.add_argument("--raw-log", default=None, help="Путь raw-лога (для симлинка latest.log)")
    rec_p.add_argument("--dir", default=DEFAULT_JOURNAL_DIR, help="Каталог журнала (default .ai/logs)")

    lat_p = sub.add_parser("latest", help="Таблица последних прогонов")
    lat_p.add_argument("--lines", type=int, default=10, help="Сколько последних записей показать")
    lat_p.add_argument("--dir", default=DEFAULT_JOURNAL_DIR, help="Каталог журнала (default .ai/logs)")

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))
    if args.command == "record":
        return _cmd_record(args)
    return _cmd_latest(args)


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
