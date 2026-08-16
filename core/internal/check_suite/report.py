"""
# GREP_SUMMARY: check-suite, report, format-report, check-marker, failed-section, footer, json-output
# STRUCTURE: ▶ format_report ┌outcomes┐ → ◇ counters (passed/auto_fixed/failed/blocked) → ◇ json? → ⎋ dict └→ ○ per-check _check_marker ┌PASS/FIXED/BLOCKED/FAIL┐ → ○ _render_failed_section ┌error_summary + REPAIR┐ → ○ _render_footer ┌NEXT┐ → ⎋ (str, dict)
# region MODULE_CONTRACT
## @purpose  Единый отчёт пакета check_suite (DevPlan 170 W3 — извлечено из монолита
##           core/internal/check_suite.py): статус, длительность, счётчики, per-check
##           PASS/FAIL/FIXED/BLOCKED, секция провалов с error_summary, NEXT-подсказка.
##           json_output → машиночитаемый dict. Возвращает (str, dict).
## @scope    core/internal/check_suite/report.py — stdlib-only. Потребители: diagnostic.py,
##           gate.py, diff.py, single.py.
## @invariants
##   - blocked (non_blocking) провалы НЕ роняют статус (паритет ci.mk `|| true`)
##   - status = "green" ⇔ failed == 0; failed НЕ учитывает passed_no_tests/blocked
##   - error_summary(max_lines=15) в FAILED-секции; REPAIR-строка — L1 → make fix-gate
## @rationale Извлечение из format_report монолита: _check_marker/_render_failed_section/
##            _render_footer — механическое разделение одного тела (research-A §1), семантика
##            и формат вывода не меняются.
## @changes 170 W3 — extracted from check_suite.py (monolith 1666→package); 170 private-imports:
##           format_report переименована в публичное имя (U-07)
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import json
from typing import TypedDict

from core.internal.check_suite.models import CheckOutcome


# region REPORT_TYPES
# TypedDict-граница JSON-отчёта/кэша (W11-G4): formatter → diagnostic/gate/diff/single
class CheckPayload(TypedDict, total=False):
    """Один элемент checks[] в отчёте (машиночитаемый per-check статус)."""

    name: str
    exit_code: int
    passed: bool
    auto_fixed: bool
    no_tests: bool
    blocked: bool
    duration_ms: int
    error_summary: str


class CheckReportDict(TypedDict):
    """Корневой dict отчёта format_report — ВСЕ ключи обязательны (format_report их всегда
    выставляет: status/счётчики/checks/duration/replayed)."""

    status: str
    total_checks: int
    passed: int
    auto_fixed: int
    failed: int
    checks: list[CheckPayload]
    duration_ms: float
    replayed: bool


# endregion REPORT_TYPES

# region REPORTING


# region FUNC_check_marker
## @purpose  Marker/icon-пара строки чека в отчёте (извлечено из format_report, DevPlan 170 W3).
##           Приоритет: passed_no_tests → auto_fixed → passed → blocked → FAIL.
## @io       ⇥ r: CheckOutcome → ⎋ tuple[str, str] (marker, icon)
## @complexity O(1)
def _check_marker(r: CheckOutcome) -> tuple[str, str]:
    """Marker/icon pair for a check line in the report (extracted, DevPlan 170 W3)."""
    if r.passed_no_tests:
        return "PASS", "OK"
    if r.auto_fixed:
        return "FIXED", "FX"
    if r.passed:
        return "PASS", "OK"
    if r.blocked:
        return "BLOCKED", "!!"
    return "FAIL", "!!"


# endregion FUNC_check_marker


# region FUNC_render_failed_section
## @purpose  Секция провалов отчёта (извлечено из format_report, DevPlan 170 W3):
##           заголовок FAILED CHECKS + per-check «### name (exit N)» + error_summary
##           + REPAIR-подсказка.
## @io       ⇥ failed_checks: list[CheckOutcome], subsep: str → ⎋ list[str] (строки секции)
## @complexity O(F * L) где F = провалы, L = строки summary
def _render_failed_section(failed_checks: list[CheckOutcome], subsep: str) -> list[str]:
    """Render the FAILED CHECKS section lines (extracted, DevPlan 170 W3)."""
    lines: list[str] = [f"\n{subsep}", f"  FAILED CHECKS ({len(failed_checks)}):", f"{subsep}"]
    for r in failed_checks:
        lines.append(f"\n  ### {r.name} (exit {r.exit_code})")
        summary = r.error_summary(max_lines=15)
        lines.extend(f"      {line}" for line in summary.split("\n"))
    lines.append(
        "  [REPAIR] L1-ошибки (executable-bit/ruff/манифесты) → make fix-gate; "
        "L2/L3 → repair-поля в core/entrypoint-manifest.yaml"
    )
    return lines


# endregion FUNC_render_failed_section


# region FUNC_render_footer
## @purpose  Футер отчёта (извлечено из format_report, DevPlan 170 W3): RESULT + NEXT-подсказка.
## @io       ⇥ status: str, failed: int, sep: str, subsep: str → ⎋ list[str]
## @complexity O(1)
def _render_footer(status: str, failed: int, sep: str, subsep: str) -> list[str]:
    """Render the report footer lines (extracted, DevPlan 170 W3)."""
    lines: list[str] = [f"\n{subsep}"]
    if status == "green":
        lines.append("  RESULT: All checks PASS.")
        lines.append("  NEXT:   make gate MODE=fast  (single verification)")
    else:
        lines.append(f"  RESULT: {failed} check(s) failed.")
        lines.append("  NEXT:   Fix ALL errors above, then:")
        lines.append("          make gate MODE=fast  (single verification)")
    lines.append(f"{sep}\n")
    return lines


# endregion FUNC_render_footer


# region FUNC_format_report
## @purpose  Единый отчёт (формат отчёта): статус, длительность, счётчики,
##           per-check PASS/FAIL/FIXED, секция провалов с error_summary, NEXT-подсказка.
##           json_output → машиночитаемый dict. Возвращает (str, dict).
## @io       ⇥ outcomes: list[CheckOutcome], duration_ms: float, json_output: bool,
##             replayed: bool → (str, dict)
## @complexity O(R) где R = результаты
def format_report(
    outcomes: list[CheckOutcome],
    duration_ms: float,
    json_output: bool = False,
    replayed: bool = False,
) -> tuple[str, CheckReportDict]:
    """Build the unified check report (human or JSON)."""
    passed = sum(1 for r in outcomes if r.passed or r.passed_no_tests)
    auto_fixed = sum(1 for r in outcomes if r.auto_fixed)
    # blocked (non_blocking) провалы НЕ роняют статус — gate остаётся зелёным (паритет ci.mk `|| true`)
    failed = sum(1 for r in outcomes if not r.passed and not r.passed_no_tests and not r.blocked)
    status = "green" if failed == 0 else "failed"

    checks_payload: list[CheckPayload] = [
        {
            "name": r.name,
            "exit_code": r.exit_code,
            "passed": r.passed,
            "auto_fixed": r.auto_fixed,
            "no_tests": r.passed_no_tests,
            "blocked": r.blocked,
            "duration_ms": round(r.duration_ms),
            "error_summary": r.error_summary() if (not r.passed and not r.passed_no_tests) else "",
        }
        for r in outcomes
    ]
    report_dict: CheckReportDict = {
        "status": status,
        "total_checks": len(outcomes),
        "passed": passed,
        "auto_fixed": auto_fixed,
        "failed": failed,
        "checks": checks_payload,
        "duration_ms": duration_ms,
        "replayed": replayed,
    }

    if json_output:
        return (json.dumps(report_dict, indent=2), report_dict)

    lines: list[str] = []
    sep = "=" * 64
    subsep = "-" * 64
    lines.append(f"\n{sep}")
    lines.append(f"  CHECK REPORT: {status.upper()}" + (" (replayed from cache)" if replayed else ""))
    lines.append(f"{sep}")
    lines.append(
        f"  Duration: {duration_ms / 1000:.1f}s  |  "
        f"Checks: {len(outcomes)} total  |  "
        f"{passed} passed  |  "
        f"{auto_fixed} auto-fixed  |  "
        f"{failed} failed"
    )
    lines.append("")

    for r in outcomes:
        marker, icon = _check_marker(r)
        lines.append(f"  [{icon}] {r.name}: {marker} ({r.duration_ms / 1000:.1f}s)")

    failed_checks = [r for r in outcomes if not r.passed and not r.passed_no_tests and not r.blocked]
    if failed_checks:
        lines.extend(_render_failed_section(failed_checks, subsep))

    lines.extend(_render_footer(status, failed, sep, subsep))
    return ("\n".join(lines), report_dict)


# endregion FUNC_format_report

# endregion REPORTING
