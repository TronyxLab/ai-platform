"""CLI точка входа статического слоя (DevPlan 163 W-C): `python3 -m core.internal.static`.

# GREP_SUMMARY: static cli argparse check changed json exit-code git-diff agent-check entrypoint
# STRUCTURE: ▶ argparse (subcommand check + --changed/--json/--root/--only) → ○ resolve root
#            → ○ --changed? git diff --name-only HEAD → ⊕ changed-set → ○ run_all →
#            → ◇ --json? json_report | human_report → ⎋ exit 0/1
"""
# region MODULE_CONTRACT
## @purpose  CLI статического слоя (DevPlan 163 W-C C1): `python3 -m core.internal.static
##           check [--changed] [--json]` — единый AST/структурный проход всех детекторов
##           реестра. exit 0 = чисто, exit 1 = находки. Машиночитаемый вывод через --json
##           (контракт T3.1 для agent-check, W-E).
## @scope    Только оркестрация: argparse, root-резолв, git-diff для --changed,
##           делегирование в registry.run_all, отчёты. Правила — в детекторах.
## @invariants
##   - Дефолтный root — корень репозитория (4 родителя от __file__: static→internal→core→repo)
##   - --changed: git diff --name-only HEAD (cwd=root); git-сбой → WARNING + полный проход
##   - exit 0 при 0 находках; exit 1 при находках; детектор-сбой → traceback + exit 1
##   - --json: единый JSON {findings: [...], summary: {total, by_rule}}
##   - --only: фильтр по именам правил (реестр DETECTORS); неизвестное имя → exit 2 (REF-0107:
##     тихий skip всех детекторов = false-green PASS — запрещён)
## @rationale Быстрый детерминированный сигнал для агента (замена 637 s static_audit):
##            один вызов — все классы дефектов grep-гейтов.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

# W1-A1 (план 170): timeout=30 литерал (git diff --name-only) → канон SoT
# CONVERGE_DOCKER_TIMEOUT (30, системная команда) — AMBER-зачистка research-D §D1.
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT
from core.internal.static.registry import DETECTORS, human_report, json_report, run_all

logger = logging.getLogger(__name__)

# Корень репозитория: core/internal/static/__main__.py → 4 родителя
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# region FUNC_resolve_changed_files
def _resolve_changed_files(root: Path) -> set[str] | None:
    """Собрать repo-relative пути изменённых файлов (git diff --name-only HEAD).

    ## @purpose  --changed режим: прогон детекторов только по изменённым файлам.
    ##           git-сбой (не репозиторий/нет HEAD) → None = полный проход.
    ## @io       ⇥ root: Path → ⎋ set[str] | None
    ## @complexity  O(N) — вывод git diff
    ## @invariants  Вызов: git diff --name-only HEAD --; строки вывода — repo-relative
    ##              posix-пути; пустые строки отбрасываются
    """
    git_bin = shutil.which("git")
    if git_bin is None:
        logger.warning("[IMP:7][cli][changed] git binary not found — falling back to full scan")
        return None
    try:
        result = subprocess.run(
            [git_bin, "diff", "--name-only", "HEAD", "--"],
            check=True,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=CONVERGE_DOCKER_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][cli][changed] git diff failed (%s) — falling back to full scan", exc)
        return None
    changed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    logger.info("[IMP:8][cli][changed] %d changed file(s) vs HEAD", len(changed))
    return changed


# endregion FUNC_resolve_changed_files


# region FUNC_build_parser
def _build_parser() -> argparse.ArgumentParser:
    """Построить argparse-парсер CLI.

    ## @purpose  Единый парсер: subcommand check + флаги --changed/--json/--root/--only.
    ## @io       ⎋ argparse.ArgumentParser
    ## @complexity  O(1)
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m core.internal.static",
        description="Static analysis layer — AST/structural detectors (DevPlan 163 W-C)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check_parser = sub.add_parser("check", help="Run static detectors over the tree")
    check_parser.add_argument(
        "--changed",
        action="store_true",
        help="Only scan files changed vs HEAD (git diff --name-only)",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output (agent-check contract T3.1)",
    )
    check_parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help=f"Scan root (default: repository root {_REPO_ROOT})",
    )
    check_parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Run only named detectors (registry rule ids)",
    )
    return parser


# endregion FUNC_build_parser


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI: `python3 -m core.internal.static check [--changed] [--json]`.

    # ▶ parse_args → ○ changed-set (--changed) → ○ run_all(root, changed, only)
    #   → ◇ findings? → json/human report → ⎋ exit 0|1

    ## @purpose  Связать argparse → registry → отчёт → exit code. Возвращает int
    ##           (тестируемо без sys.exit), __main__ вызывает sys.exit(main()).
    ## @io       ⇥ argv: list[str] | None → ⎋ int (0 чисто, 1 находки)
    ## @complexity  ∑ детекторов
    ## @invariants  Никаких широких except — сбой детектора виден (traceback) и
    ##              возвращает exit 1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)
    # argparse Namespace — нетипизированные атрибуты (Any) → cast на границе CLI (W11-G4);
    # --root: type=Path (runtime гарантия); --changed/--json: store_true; --only: nargs="*" → list | None
    root: Path = cast(Path, args.root).resolve()
    changed_flag = cast(bool, args.changed)
    only_flag = cast(list[str] | None, args.only)
    json_flag = cast(bool, args.json)

    changed: set[str] | None = _resolve_changed_files(root) if changed_flag else None
    only = set(only_flag) if only_flag else None

    # REF-0107: --only строго против реестра — неизвестное имя → exit 2 (fail-fast).
    # До фикса неизвестное имя тихо скипало ВСЕ детекторы → «PASS 0 findings» без единой
    # проверки (live-reproduced: `--only exception_patterns` vs детектор `exception-patterns`
    # в check-suite.yaml давал зелёный check-exception-patterns при [skip]×14).
    if only is not None:
        known = {spec.name for spec in DETECTORS}
        unknown = sorted(only - known)
        if unknown:
            known_sorted = ", ".join(sorted(known))
            logger.error(
                "[IMP:10][cli][only] unknown detector name(s): %s (known: %s)",
                ", ".join(unknown),
                known_sorted,
            )
            fail_line = f"static check: FAIL — unknown --only detector name(s): {', '.join(sorted(unknown))}"
            print(fail_line, file=sys.stderr)  # ruff: ignore[T201] — CLI stderr-канал вне logging (fail-fast контракт REF-0107)
            return 2

    logger.info("[IMP:8][cli] static check root=%s changed=%s only=%s", root, changed_flag, only)
    findings = run_all(root, changed, only)

    output = json_report(findings) if json_flag else human_report(findings)
    sys.stdout.write(output + "\n")
    logger.info("[IMP:9][cli] total findings=%d", len(findings))
    return 1 if findings else 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
