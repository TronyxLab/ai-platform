"""
# GREP_SUMMARY: check-suite, diff, check-diff, diff-scope, diff-files, build-diff-steps, pre-commit-files, ruff-diff
# STRUCTURE: ▶ run_diff → ◇ diff_files ┌git diff --name-only HEAD + ls-files -o┐ → ◇ пусто?→exit 0 → ○ build_diff_steps ┌pre-commit --files + ruff .py + pytest test_*┐ → ○ loop cs.run_cmd → format_report → ⎋ 0|1
# region MODULE_CONTRACT
## @purpose  Diff-скоуп executor пакета check_suite (`make check-diff`, DevPlan 170 W3 —
##           извлечено из монолита core/internal/check_suite.py): diff-файлы → узкие шаги
##           (pre-commit --files + ruff по изменённым .py + pytest изменённых test-файлов) →
##           последовательный прогон → отчёт. Без кэша (узкий честный таргет, DevPlan §3.5).
## @scope    core/internal/check_suite/diff.py — stdlib-only. Потребитель: __init__.py (CLI run).
## @invariants
##   - pre-commit --files заменяет --all-files (9.9s → ~2s на узком diff)
##   - ruff только по изменённым .py; pytest только по tests/**/test_*.py
##   - Нет изменений → пустой список → exit 0 («Nothing to diff»); git недоступен → exit 1
## @rationale Извлечение run_diff/diff_files/build_diff_steps из монолита (research-A §1),
##            тела функций не меняются.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           приватные имена переименованы в публичные (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from core.internal import check_suite as cs
from core.internal.check_suite.report import format_report

logger = logging.getLogger(__name__)


# region RUN_DIFF


# region FUNC_diff_files
## @purpose  Файлы diff-скоупа: git diff --name-only HEAD (tracked) + git ls-files -o
##           --exclude-standard (untracked). None = git недоступен.
## @io       ⇥ root: Path → ⎋ list[str] | None
## @complexity O(N)
def diff_files(root: Path) -> list[str] | None:
    """Collect changed files: tracked (vs HEAD) + untracked non-ignored."""
    changed: list[str] = []
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        r1 = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=30,
            check=False,
        )
        if r1.returncode != 0:
            return None
        changed.extend(line for line in r1.stdout.splitlines() if line.strip())
        r2 = subprocess.run(
            ["git", "ls-files", "-o", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=30,
            check=False,
        )
        if r2.returncode != 0:
            return None
        changed.extend(line for line in r2.stdout.splitlines() if line.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return sorted(set(changed))


# endregion FUNC_diff_files


# region FUNC_build_diff_steps
## @purpose  Diff-скоуп (DevPlan §3.5): (1) pre-commit run --files \<изменённые\> — ВСЕГДА при
##           diff; (2) ruff check \<изменённые .py\>; (3) pytest \<изменённые test-файлы\>
##           (tests/**/test_*.py). Без кэша. Пустой diff → [] (exit 0 «nothing to diff»).
## @io       ⇥ root: Path, changed: list[str] → list[tuple[str, str, int]] (name, cmd, timeout)
## @complexity O(N)
## @invariants
##   - pre-commit --files заменяет --all-files (9.9s → ~2s на узком diff)
##   - ruff только по изменённым .py; pytest только по tests/**/test_*.py
##   - Нет изменений → пустой список → exit 0
def build_diff_steps(_root: Path, changed: list[str]) -> list[tuple[str, str, int]]:
    """Build the narrow diff-step list (pre-commit --files + ruff diff + pytest diff)."""
    if not changed:
        return []
    steps: list[tuple[str, str, int]] = []
    files_arg = " ".join(shlex.quote(f) for f in changed)
    steps.append(("pre-commit (diff)", f"pre-commit run --files {files_arg}", 120))
    py_files = [f for f in changed if f.endswith(".py")]
    if py_files:
        py_arg = " ".join(shlex.quote(f) for f in py_files)
        steps.append(("ruff check (diff)", f"ruff check {py_arg}", 60))
    test_files = [f for f in changed if re.match(r"^tests/.*test_.*\.py$", f)]
    if test_files:
        test_arg = " ".join(shlex.quote(f) for f in test_files)
        steps.append(("pytest (diff)", f"pytest {test_arg} -q --tb=short", 300))
    return steps


# endregion FUNC_build_diff_steps


# region FUNC_run_diff
## @purpose  check-diff executor: diff-файлы → узкие шаги → последовательный прогон → отчёт.
##           Пустой diff → exit 0. Без кэша (узкий честный таргет, DevPlan §3.5).
## @io       ⇥ root: Path → int
## @complexity O(N + t)
def run_diff(root: Path) -> int:
    """Diff-scope executor: pre-commit --files + ruff diff + pytest changed test files."""
    start = time.monotonic()
    changed = diff_files(root)
    if changed is None:
        print("[IMP:9][check-diff] git недоступен — diff-скоуп не определим", file=sys.stderr)
        return 1
    if not changed:
        print("[IMP:7][check-diff] Nothing to diff — exit 0", file=sys.stderr)
        return 0

    print(f"[IMP:7][check-diff] {len(changed)} изменённых файлов", file=sys.stderr)
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")
    outcomes = []
    for name, cmd_str, timeout in build_diff_steps(root, changed):
        print(f"[IMP:7][check-diff] {name}...", file=sys.stderr)
        r = cs.run_cmd(cmd_str, timeout, env, root)  # late-binding: DI-HYG
        outcomes.append(r)

    total_ms = (time.monotonic() - start) * 1000
    report_str, report_dict = format_report(outcomes, total_ms)
    print(report_str)
    return 0 if report_dict["status"] == "green" else 1


# endregion FUNC_run_diff

# endregion RUN_DIFF
