"""
# GREP_SUMMARY: check-suite, executor, manifest, diagnostic, gate-portal, check-diff, run-single, test-file, test-journal, fingerprint, cache, list, xdist, junit-merge, allow-no-tests, non-blocking, cli
# STRUCTURE: ▶ константы ┌PROJECT_ROOT/VALID_*/DIAGNOSTIC_FALSE_DEFAULT_IDS┐ → ⊕ re-export ┌models/manifest/runner/fingerprint/report/diagnostic/gate/diff/journal/single┐ → ○ CLI main ┌run|list|fingerprint┐ → ○ _cmd_run → ○ journal_run → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Пакет core/internal/check_suite/ — единый executor набора проверок из SoT-манифеста
##           core/check-suite.yaml (DevPlan 120 §3.2), декомпозиция монолита check_suite.py
##           (1666 LOC → пакет, DevPlan 170 W3). Три режима: (1) diagnostic — `make check`;
##           (2) gate — `make gate MODE=fast|full|ci-docker` (канонический арбитр, БЕЗ кэша);
##           (3) diff — `make check-diff`. Плюс (4) `--only <id>` и (5) `--test-file <path>`
##           (DevPlan 165 W2). Каждый run пишет запись в .ai/logs/runs.jsonl (CHECK_JOURNAL=0
##           отключает). Запуск: python3 -m core.internal.check_suite run|list|fingerprint.
## @scope    core/internal/check_suite/ — stdlib-only, Python 3.10+. Пакет-замена файла
##           core/internal/check_suite.py (ПРЯМОЕ замещение, тот же публичный контракт).
##           Потребители: makefiles/repair.mk (check/check-diff), makefiles/ci.mk (gate),
##           core/entrypoint-manifest.yaml (delegates_to), tests (unit + consistency-гейт).
## @invariants
##   - Манифест — единственный источник состава проверок; НИКАКИХ hardcoded списков в executor'е
##   - Diagnostic: fix-фаза ПОСЛЕДОВАТЕЛЬНО до fingerprint; кэш ТОЛЬКО diagnostic (replay при
##     байт-идентичном дереве И зелёном прогоне); gate/diff — без кэша
##   - pytest-чеки строго последовательно (1 pytest с -n auto за раз); static-чеки параллельно
##   - xdist: прямые pytest-команды получают -n auto (spec.xdist и доступность); TEST_NO_XDIST=1 off
##   - gate: allow_no_tests (exit 5 → PASS), non_blocking (провал не роняет gate),
##     junit-merge (full/ci-docker), fail-fast (fast) / accumulate
##   - Выход: 0 = зелёный, 1 = провалы, 2 = ошибка конфигурации/использования
##   - MONKEYPATCH-КОНТРАКТ (DI-HYG): tree_files, cache_path, has_xdist, docker_suite_lock,
##     run_cmd, load_manifest, test_journal живут как ПУБЛИЧНЫЕ атрибуты пакета — тесты мокают
##     через check_suite.X; субмодули резолвят их на момент ВЫЗОВА (late-binding).
##     Приватные имена (_tree_files, _cache_path, _has_xdist, _docker_suite_lock, _run_cmd,
##     _journal_run, _apply_*, _build_diff_steps, _diff_files, _PROJECT_ROOT, _VALID_*,
##     _FINGERPRINT_*) сохранены как ПРИВАТНЫЕ АЛИАСЫ публичных сущностей (паттерн U-07) —
##     совместимость атрибутов пакета с тестами (import + старые monkeypatch-пути).
## @rationale Декомпозиция god-модуля (research-A §1): 9 смешанных доменов → 10 модулей +
##            CLI. Публичный контракт (17 импортируемых символов + monkeypatch-атрибуты +
##            приватные константы + `python -m`) сохранён 1:1. `python -m` работает через
##            __main__.py (Python 3.11+: `-m package` требует __main__; __init__ сохраняет
##            `if __name__ == "__main__"` для прямого запуска файла).
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена home-модулей переименованы в публичные (U-07); __init__ держит
##           приватные алиасы для monkeypatch/import-контракта тестов (from X import name as _name)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Protocol, cast

logger = logging.getLogger(__name__)

# region CONSTANTS

# Root of the ai-platform project (4 levels up from core/internal/check_suite/__init__.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# Приватный алиас — compat-контракт тестов (check_suite._PROJECT_ROOT, U-07)
_PROJECT_ROOT = PROJECT_ROOT

VALID_TIERS = ("fix", "static", "pytest")
VALID_GATE_MODES = ("fast", "full", "ci-docker")
# Приватные алиасы — compat-контракт (check_suite._VALID_*, U-07)
_VALID_TIERS = VALID_TIERS
_VALID_GATE_MODES = VALID_GATE_MODES

# Checks that are диагностика-only (diagnostic: false) — заданы в манифесте явно,
# здесь — только константы валидации для consistency-гейта (не список проверок)
_DIAGNOSTIC_FALSE_DEFAULT_IDS = ("lint", "check-file-lines", "smoke", "component", "predeploy-docker")

# endregion CONSTANTS

# region RE_EXPORTS
# Публичные символы + monkeypatch-контракт (DI-HYG): имена живут как атрибуты пакета.
# Приватные имена (U-07) — приватные алиасы публичных сущностей (from X import name as _name):
# тесты импортируют их из пакета напрямую (test_check_suite.py:35-39) и вызывают
# (check_suite._journal_run, check_suite._FINGERPRINT_EXCLUDE_*), поэтому атрибуты сохранены.
from core.internal.check_suite.diagnostic import DEFAULT_MAX_WORKERS, run_diagnostic
from core.internal.check_suite.diagnostic import DEFAULT_MAX_WORKERS as _DEFAULT_MAX_WORKERS
from core.internal.check_suite.diff import (
    build_diff_steps,
    diff_files,
    run_diff,
)
from core.internal.check_suite.diff import (
    build_diff_steps as _build_diff_steps,
)
from core.internal.check_suite.diff import (
    diff_files as _diff_files,
)
from core.internal.check_suite.fingerprint import (
    FINGERPRINT_EXCLUDE_PARTS,
    FINGERPRINT_EXCLUDE_RE,
    cache_path,
    compute_fingerprint,
    tree_files,
)
from core.internal.check_suite.fingerprint import (
    FINGERPRINT_EXCLUDE_PARTS as _FINGERPRINT_EXCLUDE_PARTS,
)
from core.internal.check_suite.fingerprint import (
    FINGERPRINT_EXCLUDE_RE as _FINGERPRINT_EXCLUDE_RE,
)
from core.internal.check_suite.fingerprint import (
    cache_path as _cache_path,
)
from core.internal.check_suite.fingerprint import (
    tree_files as _tree_files,
)
from core.internal.check_suite.gate import run_gate
from core.internal.check_suite.journal import journal_run
from core.internal.check_suite.journal import journal_run as _journal_run
from core.internal.check_suite.manifest import list_checks, load_manifest, parse_checks, validate_manifest
from core.internal.check_suite.models import CheckOutcome, CheckSpec
from core.internal.check_suite.report import format_report
from core.internal.check_suite.report import format_report as _format_report
from core.internal.check_suite.runner import (
    apply_project_filter,
    apply_xdist,
    docker_suite_lock,
    has_xdist,
    memory_available_bytes,
    run_cmd,
    run_pytest_check,
    run_retry_once,
    xdist_worker_count,
)
from core.internal.check_suite.runner import (
    apply_project_filter as _apply_project_filter,
)
from core.internal.check_suite.runner import (
    apply_xdist as _apply_xdist,
)
from core.internal.check_suite.runner import (
    docker_suite_lock as _docker_suite_lock,
)
from core.internal.check_suite.runner import (
    has_xdist as _has_xdist,
)
from core.internal.check_suite.runner import (
    memory_available_bytes as _memory_available_bytes,
)
from core.internal.check_suite.runner import (
    run_cmd as _run_cmd,
)
from core.internal.check_suite.runner import (
    xdist_worker_count as _xdist_worker_count,
)
from core.internal.check_suite.single import run_single, run_test_file
from core.internal.shared import test_journal

# endregion RE_EXPORTS


# region CLI


# region FUNC_CliArgs_protocol
## @purpose  Типизированная проекция argparse.Namespace (W11-G4): все атрибуты заданы
##           subparser'ами run/list/fingerprint с дефолтами — прямой доступ безопасен.
##           Namespace динамический (Any) → cast на границе CLI, рантайм не меняется.
##           ВАЖНО: Protocol с АННОТАЦИЯМИ БЕЗ значений — подклассы argparse.Namespace
##           с class-атрибутами ломают hasattr/дефолты (баг), поэтому только Protocol.
## @io       ⇥ атрибуты Namespace → ⎋ Protocol-тип для _cmd_run/_cmd_list/main
## @complexity O(1)
class _CliArgs(Protocol):
    """Namespace-проекция run/list/fingerprint subparser'ов (все атрибуты с дефолтами)."""

    command: str | None
    gate_mode: str | None
    mode: str | None
    no_fix: bool
    json: bool
    workers: int
    no_cache: bool
    verbose: bool
    project: str | None
    skip_precommit: bool
    only: str | None
    test_file: str | None


# endregion FUNC_CliArgs_protocol


# region FUNC_cmd_list
## @purpose  `list [--gate-mode fast|full]`: печать id чеков в каноническом порядке.
##           Используется consistency-гейтом для golden-паритета шагов gate.
## @io       ⇥ args: Namespace, root: Path → int
## @complexity O(C)
def _cmd_list(args: argparse.Namespace, root: Path) -> int:
    """List check ids (diagnostic set or a gate mode) in canonical manifest order."""
    manifest = load_manifest(root)
    gate_mode = cast(_CliArgs, cast(object, args)).gate_mode  # W11-G4: Namespace → типизированная проекция
    specs = list_checks(manifest, gate_mode=gate_mode)
    for s in specs:
        print(s.id)
    logger.info("[IMP:9][list][result] %d check(s) для gate_mode=%s", len(specs), gate_mode)
    return 0


# endregion FUNC_cmd_list


# region FUNC_cmd_fingerprint
## @purpose  `fingerprint`: вывод fingerprint дерева (диагностика кэша).
## @io       ⇥ args, root → int
## @complexity O(N * S)
def _cmd_fingerprint(_args: argparse.Namespace, root: Path) -> int:
    """Print the tree fingerprint (cache diagnostics)."""
    fp = compute_fingerprint(root)
    if fp is None:
        print("fingerprint: unavailable (git недоступен)", file=sys.stderr)
        return 1
    print(fp)
    return 0


# endregion FUNC_cmd_fingerprint


# region FUNC_diagnostic_flags_set
## @purpose  True, если задан хотя бы один diagnostic-режимный флаг (--no-fix/--json/--workers
##           не дефолт/--no-cache/--verbose). Используется для явного отказа несовместимых
##           комбинаций (170 W10-C, research-D §D3c-1: silent no-op вне своего режима → отказ).
## @io       ⇥ args: Namespace → bool
## @complexity O(1)
def _diagnostic_flags_set(args: argparse.Namespace) -> bool:
    """Detect diagnostic-only flags for per-mode rejection."""
    a = cast(_CliArgs, cast(object, args))  # W11-G4: Namespace динамический → типизированная проекция
    return bool(a.no_fix or a.json or a.workers != DEFAULT_MAX_WORKERS or a.no_cache or a.verbose)


# endregion FUNC_diagnostic_flags_set


# region FUNC_cmd_run
## @purpose  `run` dispatch: --mode diagnostic | diff | --gate-mode fast|full|ci-docker |
##           --only \<id\> | --test-file \<path\>. Взаимоисключения: --gate-mode × --mode;
##           --only/--test-file только в diagnostic-контексте; diagnostic-флаги (--no-fix/
##           --json/--workers/--no-cache/--verbose) вне diagnostic = явный отказ (170 W10-C,
##           D3c-1); --project/--skip-precommit только в gate. Каждый прогон (кроме
##           usage-ошибок) завершается журнальной записью .ai/logs/runs.jsonl (W2).
## @io       ⇥ args: Namespace, root: Path → int
## @complexity O(1) + режим
def _cmd_run(args: argparse.Namespace, root: Path) -> int:
    """Dispatch run subcommand to diagnostic/diff/gate/single/test-file executors (+ journal)."""
    a = cast(
        _CliArgs, cast(object, args)
    )  # W11-G4: Namespace динамический → типизированная проекция (CLI-семантика не меняется)
    start_wall = time.time()
    journal_goal = "check"
    junit_paths: list[str] = []
    journal = True
    rc = 2

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        if a.only and (a.gate_mode or a.mode == "diff"):
            print("[IMP:10][run] --only несовместим с --gate-mode/--mode diff", file=sys.stderr)
            journal = False
            return rc
        if a.test_file and (a.gate_mode or a.mode == "diff" or a.only):
            print("[IMP:10][run] --test-file несовместим с --gate-mode/--mode diff/--only", file=sys.stderr)
            journal = False
            return rc
        if a.gate_mode:
            if a.mode:
                print("[IMP:10][run] --gate-mode несовместим с --mode", file=sys.stderr)
                journal = False
                return rc
            # 170 W10-C (D3c-1): diagnostic-флаги вне diagnostic-режима = явный отказ (не silent no-op)
            if _diagnostic_flags_set(args):
                print(
                    "[IMP:10][run] --no-fix/--json/--workers/--no-cache/--verbose несовместимы с --gate-mode",
                    file=sys.stderr,
                )
                journal = False
                return rc
            journal_goal = "gate"
            manifest = load_manifest(root)
            steps = list_checks(manifest, gate_mode=a.gate_mode)
            junit_paths = [s.junit for s in steps if s.junit]
            rc = run_gate(root, a.gate_mode, project=a.project, skip_precommit=a.skip_precommit)
        elif a.mode == "diff":
            if _diagnostic_flags_set(args) or a.project or a.skip_precommit:
                print("[IMP:10][run] diagnostic/gate-флаги несовместимы с --mode diff", file=sys.stderr)
                journal = False
                return rc
            journal_goal = "check-diff"
            rc = run_diff(root)
        elif a.only:
            manifest = load_manifest(root)
            spec = next((s for s in parse_checks(manifest) if s.id == a.only), None)
            junit_paths = [spec.junit] if spec and spec.junit else []
            rc = run_single(root, a.only)
        elif a.test_file:
            rc = run_test_file(root, a.test_file)
        else:
            if a.project or a.skip_precommit:
                print("[IMP:10][run] --project/--skip-precommit несовместимы с diagnostic-режимом", file=sys.stderr)
                journal = False
                return rc
            manifest = load_manifest(root)
            junit_paths = [s.junit for s in list_checks(manifest, gate_mode=None) if s.junit]
            rc = run_diagnostic(
                root,
                no_fix=a.no_fix,
                json_output=a.json,
                workers=a.workers,
                no_cache=a.no_cache,
                verbose=a.verbose,
            )
    except Exception:  # noqa: EXC — top-level CLI handler: запись журнала падения, переброс исключения
        rc = 1
        raise
    finally:
        if journal:
            journal_run(root, journal_goal, rc, junit_paths, start_wall)
    return rc


# endregion FUNC_cmd_run


# region FUNC_main
## @purpose  CLI: run/list/fingerprint + флаги diagnostic (--no-fix/--json/--workers/--no-cache/
##           --verbose), gate (--gate-mode/--project/--skip-precommit), diff (--mode diff).
## @io       ⇥ argv: list[str] | None → int (exit code)
## @complexity O(1)
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the check-suite executor."""
    parser = argparse.ArgumentParser(
        prog="check_suite",
        description="Единый executor набора проверок из core/check-suite.yaml (DevPlan 120).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Запуск executor'а: diagnostic (по умолчанию), diff или gate.")
    run_p.add_argument(
        "--mode",
        # 170 W10-C (D3c): значение "diagnostic" избыточно — default=None (else-ветка _cmd_run)
        # неотличим; остаётся только фактический переключатель режима --mode diff.
        choices=("diff",),
        default=None,
        help="Режим: diff (by default — полный diagnostic набор)",
    )
    run_p.add_argument("--gate-mode", choices=VALID_GATE_MODES, default=None, help="Канонический gate-режим (без кэша)")
    run_p.add_argument("--no-fix", action="store_true", help="Пропустить fix-фазу (fix-gate + tier=fix)")
    run_p.add_argument("--json", action="store_true", help="Машиночитаемый JSON-отчёт")
    run_p.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS, help="Воркеры static-параллелизма")
    run_p.add_argument("--no-cache", action="store_true", help="Без чтения/записи fingerprint-кэша (CHECK_CACHE=0)")
    run_p.add_argument("--verbose", "-v", action="store_true", help="Полный stdout/stderr упавших чеков")
    run_p.add_argument("--project", default=None, help=r"PROJECT=\<name\> → -k для project_filter-чеков")
    run_p.add_argument("--skip-precommit", action="store_true", help="SKIP_PRECOMMIT=1 — пропустить pre-commit шаг")
    run_p.add_argument(
        "--only",
        default=None,
        help="Запустить ОДИН чек по id из манифеста (обходит diagnostic/gate-фильтры; без кэша). "
        "Список id: python3 -m core.internal.check_suite list",
    )
    run_p.add_argument(
        "--test-file",
        default=None,
        help="pytest одного тест-файла через test_runner (compact-вывод; без кэша). "
        "Пример: --test-file tests/unit/test_foo.py",
    )

    list_p = sub.add_parser("list", help="Список id чеков (диагностический набор или gate-режим)")
    list_p.add_argument("--gate-mode", choices=VALID_GATE_MODES, default=None, help="Фильтр по gate-режиму")

    sub.add_parser("fingerprint", help="Вычислить fingerprint дерева (диагностика кэша)")

    args = parser.parse_args(argv)
    root = PROJECT_ROOT
    a = cast(_CliArgs, cast(object, args))  # W11-G4: Namespace динамический → типизированная проекция

    if a.command == "run":
        return _cmd_run(args, root)
    if a.command == "list":
        return _cmd_list(args, root)
    return _cmd_fingerprint(args, root)


# endregion FUNC_main

# endregion CLI


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
