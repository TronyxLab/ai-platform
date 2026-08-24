#!/usr/bin/env python3
# GREP_SUMMARY: fix-executable-bit repair git-add-chmod git-update-index executable-bit 100644 100755 core-lib-exclusion DRY_RUN
# STRUCTURE: ▶ argv/env DRY_RUN → ◇ MSYS-diagnostic (core.fileMode=false warning) → ◇ pass1: staged .sh (git add --chmod=+x) → ◇ pass2: tracked 100644 .sh (git update-index --chmod=+x) → ∑ fixed → ⎋ [REPAIR:FIXED|NOOP|DRYRUN|ERROR] exit 0|1
# region MODULE_CONTRACT
## @purpose  Python-порт makefiles/repair.mk `fix-executable-bit` (Strangler T3.4 Wave 3):
##           двухпроходный fix executable bit для .sh вне core/lib/ — pass 1: staged/new .sh
##           через `git add --chmod=+x`; pass 2: tracked 100644 .sh через
##           `git update-index --chmod=+x`. Вывод [REPAIR:*] — байт-в-байт с прежним shell-рецептом.
## @scope    CLI (`python3 -m core.internal.scripts.fix_executable_bit`) + importable функции;
##           вызывается из `make fix-executable-bit` (тонкий рецепт) и `make fix-gate` (composite).
## @invariants
##   - Формат вывода [REPAIR:DRYRUN|FIXED|NOOP|ERROR|WARNING] — байт-в-байт с repair.mk (T3.4)
##   - DRY_RUN=1 (env) или --dry-run: только вывод "would +x", ноль мутаций git
##   - core/lib/*.sh исключены (sourced-only — 100644 валиден)
##   - Pass 1: `git diff --cached --name-only --diff-filter=ACM -z -- '*.sh'` → git add --chmod=+x
##   - Pass 2: `git ls-files -s -z -- '*.sh'` → mode 100644 → git update-index --chmod=+x
##   - Ноль мутаций git history — только index (git add/update-index); сетевых вызовов нет
##   - Windows diagnostic: MINGW64_NT/MSYS_NT + core.fileMode=false → warning. В shell-оригинале
##     проверка `uname -s = "MINGW64_NT"` была мёртвым кодом (uname возвращает суффикс-версию);
##     здесь — prefix-match, намерение восстановлено (см. TRAP[DECISION] ниже)
##   - Exit: 0 (успех/NOOP/DRYRUN), 1 (исключение/guard — [REPAIR:ERROR])
## @rationale Языковая политика (AGENTS.md): новый код — Python-first. 55 LOC shell/awk
##            (позиционный парсинг полей, xargs -0, пересклейка 4..NF) → тестируемый модуль
##            с DI-runner. Null-терминированный git-вывод (-z) парсится надёжнее в Python
##            (split("\0")) — устойчиво к пробелам/спецсимволам в путях без awk-хрупкости.
## @changes  2026-08-22 | Strangler T3.4 Wave 3 — Created (порт repair.mk fix-executable-bit)
# ⚠️ TRAP[DECISION] · 2026-08-22 · — · Windows-diagnostic: prefix-match вместо равенства
# · Rejected: дословный перенос `uname -s == "MINGW64_NT"` (мёртвый код — uname возвращает
# ·            "MINGW64_NT-10.0-…", равенство никогда не матчится)
# · Reason: восстановление намерения (warning о core.fileMode=false на Windows) без изменения
# ·          формата выводимых строк; байт-в-байт контракт [REPAIR:*] сохранён
# · Rev: если MSYS2 изменит формат uname -s (перестанет начинаться с MINGW64_NT/MSYS_NT) —
# ·       обновить _WIN_KERNELS
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# W11: DI-тип runner (паритет hermes_images BuildRunner) — subprocess.run-контракт.
GitRunner = Callable[..., subprocess.CompletedProcess[str]]

# ── sys.path bootstrap for direct-script invocation (паритет validate_module_yaml.py) ──
_PLATFORM_ROOT = str(Path(__file__).resolve().parents[3])
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

_TAG = "[fix-executable-bit]"
_LIB_PREFIX = "core/lib/"
# Режим «не-executable» в git index → подлежит починке (pass 2)
_MODE_NON_EXEC = "100644"
# uname -s префиксы MSYS2/MINGW64 (lowercase) — Windows diagnostic
_WIN_KERNELS = ("mingw64_nt", "msys_nt")


# region DATA_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений —
    значения заполняет parse_args(namespace=CliArgs()).
    """

    dry_run: bool  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills
    repo_root: Path  # pyright: ignore[reportUninitializedInstanceVariable]


# endregion DATA_CliArgs


# region FUNC_run_git
def _run_git(
    args: list[str],
    repo_root: Path,
    runner: GitRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Выполнить git-команду (cwd=repo_root, capture, check=False) — shell `2>/dev/null`-паритет.

    ## @purpose  Единая точка git-вызовов: listing-команды (shell глушил stderr через
    ##            2>/dev/null) и мутирующие (git add/update-index). Non-zero rc НЕ бросается —
    ##            caller решает (параллель `cmd && { echo; }`-guard в shell-оригинале).
    ## @io       ⇥ args: list[str], repo_root: Path, runner → ⎋ CompletedProcess[str]
    ## @complexity O(1) — single subprocess.run (без shell — S603-политика)
    """
    run = runner if runner is not None else subprocess.run
    return run(args, cwd=repo_root, text=True, capture_output=True, check=False)


# endregion FUNC_run_git


# region FUNC_is_msys_windows
def _is_msys_windows(runner: GitRunner | None = None) -> bool:
    """uname -s → MSYS2/MINGW64? (prefix-match; shell-оригинал использовал равенство — мёртвый код).

    ## @purpose  Детект Windows-diagnostic: на MSYS2/MINGW64 uname возвращает
    ##            "MINGW64_NT-<версия>" / "MSYS_NT-<версия>" — prefix-match восстанавливает намерение.
    ## @io       ⇥ runner → ⎋ bool
    ## @complexity O(1) — single uname subprocess
    """
    run = runner if runner is not None else subprocess.run
    try:
        res = run(["uname", "-s"], text=True, capture_output=True, check=False)
    except OSError:
        return False
    return res.stdout.strip().lower().split("-", 1)[0] in _WIN_KERNELS


# endregion FUNC_is_msys_windows


# region FUNC_file_mode_is_false
def _file_mode_is_false(repo_root: Path, runner: GitRunner | None = None) -> bool:
    """git config --get core.fileMode == "false" (Windows checkout warning-условие)."""
    res = _run_git(["git", "config", "--get", "core.fileMode"], repo_root, runner)
    return res.stdout.strip() == "false"


# endregion FUNC_file_mode_is_false


# region FUNC_staged_sh_files
def _staged_sh_files(repo_root: Path, runner: GitRunner | None = None) -> list[str]:
    """Pass 1 список: staged/new .sh (--diff-filter=ACM, -z) вне core/lib/ (shell `case`-паритет)."""
    res = _run_git(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z", "--", "*.sh"],
        repo_root,
        runner,
    )
    return [f for f in res.stdout.split("\0") if f and not f.startswith(_LIB_PREFIX)]


# endregion FUNC_staged_sh_files


# region FUNC_tracked_sh_entries
def _tracked_sh_entries(repo_root: Path, runner: GitRunner | None = None) -> list[tuple[str, str]]:
    """Pass 2: (mode, path) из `git ls-files -s -z -- '*.sh'`; path устойчив к пробелам.

    ## @purpose  Формат записи `<mode> <object> <stage>\t<path>` (NUL-разделитель). Awk-пересклейка
    ##            полей 4..NF (shell) заменена partition("\t") — эквивалентно и надёжнее.
    ## @io       ⇥ repo_root, runner → ⎋ list[tuple[str, str]]: (mode, path)
    ## @complexity O(N) — N = число tracked .sh
    """
    res = _run_git(["git", "ls-files", "-s", "-z", "--", "*.sh"], repo_root, runner)
    entries: list[tuple[str, str]] = []
    for line in res.stdout.split("\0"):
        if not line:
            continue
        meta, _, path = line.partition("\t")
        mode = meta.split(" ", 1)[0] if meta else ""
        entries.append((mode, path))
    return entries


# endregion FUNC_tracked_sh_entries


# region FUNC_fix_executable_bit
def fix_executable_bit(
    dry_run: bool,
    repo_root: Path | None = None,
    runner: GitRunner | None = None,
) -> int:
    """Двухпроходный fix: pass1 staged (git add --chmod=+x), pass2 tracked 100644 (git update-index).

    ▶ DRY_RUN? → ◇ pass1 staged .sh → ◇ pass2 tracked 100644 .sh → ∑ fixed → ⎋ count

    Args:
        dry_run: True = только вывод "would +x", ноль мутаций.
        repo_root: git work-tree root (None = _PLATFORM_ROOT).
        runner: Optional subprocess.run override (DI). None = real subprocess.run.

    Returns:
        Число исправленных файлов (для summary-строки; в DRY_RUN всегда 0).
    """
    repo_root = repo_root if repo_root is not None else Path(_PLATFORM_ROOT)

    if dry_run:
        print(f"[REPAIR:DRYRUN]{_TAG} Would set +x on .sh files outside core/lib/")

    # Windows diagnostic (shell: `uname -s` = MINGW64_NT — здесь prefix-match, см. TRAP[DECISION])
    if _is_msys_windows(runner) and _file_mode_is_false(repo_root, runner):
        print(f"[REPAIR:WARNING]{_TAG} core.fileMode=false detected on Windows.")
        print("  git update-index --chmod=+x will NOT persist on next checkout.")
        print("  Consider: git config core.fileMode true")

    fixed = 0

    # Pass 1: staged/new .sh (ACM)
    for f in _staged_sh_files(repo_root, runner):
        if dry_run:
            print(f"  [DRY RUN] would +x (staged) {f}")
            continue
        if not (repo_root / f).is_file():
            continue
        res = _run_git(["git", "add", "--chmod=+x", "--", f], repo_root, runner)
        if res.returncode == 0:
            print(f"  [REPAIR:FIXED] +x (staged) {f}")
            fixed += 1

    # Pass 2: tracked 100644 .sh вне core/lib/
    for mode, f in _tracked_sh_entries(repo_root, runner):
        if f.startswith(_LIB_PREFIX):
            continue
        if dry_run:
            if mode == _MODE_NON_EXEC:
                print(f"  [DRY RUN] would +x (tracked) {f}")
            continue
        if mode != _MODE_NON_EXEC:
            continue
        res = _run_git(["git", "update-index", "--chmod=+x", "--", f], repo_root, runner)
        if res.returncode == 0:
            print(f"  [REPAIR:FIXED] +x (tracked) {f}")
            fixed += 1

    if dry_run:
        print(f"[REPAIR:DRYRUN]{_TAG} DRY RUN — no files modified.")
    elif fixed == 0:
        print(f"[REPAIR:NOOP]{_TAG} No .sh files needed fixing.")
    else:
        print(f"[REPAIR:FIXED]{_TAG} {fixed} file(s) fixed.")
    return fixed


# endregion FUNC_fix_executable_bit


# region FUNC_main
def main(
    argv: list[str] | None = None,
    runner: GitRunner | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """CLI entry: `python3 -m core.internal.scripts.fix_executable_bit [--dry-run] [--repo-root PATH]`.

    ▶ ┌argv/env┐ → ◇ --dry-run | DRY_RUN=1 → ◇ fix_executable_bit() → ⎋ exit 0|1

    Args:
        argv: Optional CLI args override (DI — DevPlan 167 D1). None = sys.argv.
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional env override (DI): DRY_RUN/PLATFORM_ROOT. None/пусто = os.environ fallback.
    """
    env = env if env is not None else {}
    parser = argparse.ArgumentParser(
        description="Fix executable bit on .sh files outside core/lib/ (repair.mk порт, Strangler T3.4)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="DRY_RUN=1 parity: only print 'would +x', no git mutation"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="git work-tree root (default: platform root / PLATFORM_ROOT env)",
    )
    args = parser.parse_args(argv, namespace=CliArgs())

    dry_run = args.dry_run or env.get("DRY_RUN", os.environ.get("DRY_RUN", "")) == "1"
    repo_root = (
        args.repo_root
        if args.repo_root is not None
        else Path(env.get("PLATFORM_ROOT", os.environ.get("PLATFORM_ROOT", _PLATFORM_ROOT)))
    )
    try:
        fix_executable_bit(dry_run=dry_run, repo_root=repo_root, runner=runner)
    except OSError as exc:
        # shell-паритет: trap ERR → "[REPAIR:ERROR]..."; формат байт-в-байт, причина — в сообщении
        print(f"[REPAIR:ERROR]{_TAG} {exc}")
        return 1
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
