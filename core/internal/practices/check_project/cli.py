# GREP_SUMMARY: check-project-cli, main, argparse, format-report, PRACTICES-output, exit-0-1-4, project-check, project-fix
# STRUCTURE: ▶ main(argv) → logging.basicConfig (LOG_LEVEL, stderr) → argparse (--project-dir required, --level, --fix) → check_project() → ◇ ConfigValidationError → exit 4 │ PlatformError → exc.exit_code → ⊕ print(format_report) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  CLI-слой K1-канала практик (DevPlan 137 §2.1A/§4.7, 170 W10-A декомпозиция):
##           `python3 -m core.internal.practices.check_project --project-dir DIR
##           [--level baseline|full|auto] [--fix]` — форматирование [PRACTICES:...] отчёта
##           (stdout-контракт для агента), exit-коды 0/1/4 (L1-блок; L2/L3 в active-full;
##           ConfigValidationError → 4). main() -> int; sys.exit ТОЛЬКО в __main__-блоках
##           (фасад check_project.py / пакет __main__.py — контракт core/AGENTS.md).
## @scope    Потребители: makefiles/project-practices.mk:21-22 (project-check/project-fix),
##           core/entrypoint-manifest.yaml:291/300, фасад check_project.py + пакет __main__.py,
##           tests (format_report — формат [PRACTICES:...] строк).
## @invariants
##   - stdout — ТОЛЬКО format_report (машиночитаемый контракт); stderr — LDD-логи
##   - ConfigValidationError → [PRACTICES:ERROR] в stderr + exit 4 (EXIT_CONFIG_VALIDATION)
##   - PlatformError → [PRACTICES:ERROR] + exc.exit_code (единый контракт exceptions.py)
##   - argparse --level: '' → None (нет override — берётся из ai-platform.yaml quality.level)
##   - main() НЕ вызывает sys.exit — возвращает int (__main__ решает)
## @rationale Выделение CLI из монолита: format_report — stdout-контракт (агент-видимый),
##            main — только парсинг + обработка типизированных ошибок; логика — в runner.
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:1334-1382)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import cast

from core.internal.practices.check_project.models import CheckReport
from core.internal.practices.check_project.runner import check_project
from core.internal.shared.contracts import EXIT_CONFIG_VALIDATION
from core.internal.shared.exceptions import ConfigValidationError, PlatformError

logger = logging.getLogger(__name__)


# region FUNC_format_report
## @purpose  Форматирование [PRACTICES:...] вывода для агента (stdout): state-строка,
##           варнинг эскалатора (PROPOSE), по одной строке на проверку, exit-сводка.
## @io       ⇥ report: CheckReport → ⎋ str
## @complexity O(R)
def format_report(report: CheckReport) -> str:
    """Render [PRACTICES:...] report lines (agent-visible)."""
    lines: list[str] = [f"[PRACTICES:STATE][{report.state}][level:{report.level_setting}]"]
    lines.extend(report.warnings)
    lines.extend(
        f"[PRACTICES:CHECK][{r.check_id}] {r.status} ({r.duration_s:.1f}s) — {r.message}" for r in report.results
    )
    lines.append(f"[PRACTICES:RESULT] exit={report.exit_code} ({len(report.results)} checks)")
    return "\n".join(lines)


# endregion FUNC_format_report


# region FUNC_main
## @purpose  CLI entry point: python3 -m core.internal.practices.check_project
##           --project-dir DIR [--level baseline|full|auto] [--fix].
## @io       stdout: [PRACTICES:...] report; stderr: LDD logs
## @exitcode 0 — зелёный; 1 — L1-блок или L2/L3-блок в active-full; 4 — ConfigValidationError
def main(argv: list[str] | None = None) -> int:
    """CLI for project-check / project-fix (exit 0/1/4)."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),  # pyright: ignore[reportAny] W11-G4: getattr(logging, str) возвращает Any — logging-уровень из env (contract: имя уровня)
        format="[%(levelname)s][practices_check] %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(description="Check project practices (K1 local channel)")
    parser.add_argument("--project-dir", required=True, type=str, help="Project directory to check")
    parser.add_argument("--level", type=str, default="", help="Override level: baseline | full | auto")
    parser.add_argument("--fix", action="store_true", help="Apply auto-fix checks (project-fix)")
    args = parser.parse_args(argv)

    try:
        # argparse Namespace — нетипизированные атрибуты (Any) → cast на границе CLI (W11-G4)
        level_override = cast(str | None, args.level)
        fix_flag = cast(bool, args.fix)
        report = check_project(Path(cast(str, args.project_dir)), level=level_override or None, fix=fix_flag)
    except ConfigValidationError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_VALIDATION
    except PlatformError as exc:
        print(f"[PRACTICES:ERROR] {exc}", file=sys.stderr)
        return exc.exit_code

    print(format_report(report))
    logger.info("[IMP:9][check_project][main] exit=%d", report.exit_code)
    return report.exit_code


# endregion FUNC_main
