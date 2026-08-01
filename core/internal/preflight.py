#!/usr/bin/env python3
# GREP_SUMMARY: preflight, parallel-checks, gate-replacement, agent-workflow, fix-gate, error-collection
# STRUCTURE: ┌Phase 1: fix-gate┐ → ◇ Phase 2: pre-commit → ◇ Phase 3: parallel-checks → ⊕ report → ⎋ CLI
# region MODULE_CONTRACT
## @purpose  Preflight — run ALL gate checks in parallel, collect errors, report once.
##           Eliminates the iterative fix→gate→fix→gate→... cycle for AI agents.
## @scope    Developer/agent tool — complements `make gate`, does NOT replace it.
##           Gate remains the authoritative verification; preflight is a diagnostic accelerator.
## @invariants
##   - Phase 1 (fix-gate) runs FIRST, sequentially — mutates files (auto-fix)
##   - Phase 2 (pre-commit) runs SECOND, sequentially — mutates files (hygiene auto-fix) + verify
##   - Phase 3 (read-only checks) runs in PARALLEL via ThreadPoolExecutor
##   - All errors are collected and reported in ONE structured output
##   - Exit code 0 = all checks pass (gate would be green)
##   - Exit code 1 = errors collected (agent should fix, then verify with gate)
##   - NEVER replaces gate — gate is authoritative. Preflight is a pre-verification accelerator.
## @rationale AI agents waste 60-80% of verification time on iterative fix→gate cycles
##            because `make gate` is sequential and stops at first failure.
##            Preflight runs all checks in parallel, collects ALL errors,
##            and reports them in one pass. Agent fixes everything once, verifies once.
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import concurrent.futures
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# endregion IMPORTS

# region CONSTANTS

logger = logging.getLogger(__name__)

# Root of the ai-platform project (2 levels up from core/internal/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Number of parallel workers for Phase 3 checks
_DEFAULT_MAX_WORKERS = 6

def _resolve_python() -> str:
    """Resolve the canonical Python interpreter (venv if available, else sys.executable).

    ## @purpose  The gate uses .venv/bin/python which has pytest-xdist and other deps.
    ##           sys.executable may be a different Python without those packages.
    ## @io       → ⎋ str: path to python3 binary
    """
    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _has_xdist(python_path: str) -> bool:
    """Check if pytest-xdist is available for the given Python."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import xdist"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:  # noqa: EXC — best-effort availability check, any failure = unavailable
        return False


# endregion CONSTANTS


# region DATA_MODELS

@dataclass
class CheckResult:
    """Result of a single preflight check."""

    name: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    auto_fixed: bool = False
    fixed_by: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    @property
    def failed(self) -> bool:
        return self.exit_code != 0

    def error_summary(self, max_lines: int = 20) -> str:
        """Extract key error lines for reporting."""
        combined = (self.stderr + "\n" + self.stdout).strip()
        if not combined:
            return "(no output)"
        lines = combined.split("\n")
        # Filter to error-relevant lines
        key_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(
                kw in lower
                for kw in ("fail", "error", "warning:", "could not", "unable", "ref", "undefined")
            ):
                key_lines.append(stripped)
        if not key_lines:
            # Return last few non-empty lines as fallback
            non_empty = [l.strip() for l in lines if l.strip()]
            key_lines = non_empty[-max_lines:]
        return "\n".join(key_lines[:max_lines])


@dataclass
class PreflightReport:
    """Aggregated preflight check results."""

    status: str  # "green" | "failed"
    total_checks: int = 0
    passed: int = 0
    auto_fixed: int = 0
    failed: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "total_checks": self.total_checks,
                "passed": self.passed,
                "auto_fixed": self.auto_fixed,
                "failed": self.failed,
                "checks": self.checks,
                "duration_ms": self.duration_ms,
            },
            indent=2,
        )


# endregion DATA_MODELS


# region CHECK_RUNNERS

def _run_check(
    cmd: list[str],
    name: str,
    timeout: int = 120,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> CheckResult:
    """Run a single check command and return its result.

    ## @purpose  Execute a subprocess, capture stdout/stderr, time it.
    ## @io       ⇥ cmd: command + args as list
    ##           ⇥ name: human-readable check name
    ##           ⇥ timeout: seconds before timeout
    ##           → ⎋ CheckResult with exit_code, stdout, stderr, duration_ms
    ## @invariants
    ##   - Timeout → exit_code = 124, stderr = "Timeout after Ns"
    ##   - Non-zero exit is NOT raised as exception — caller collects
    """
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
        duration = (time.monotonic() - start) * 1000
        return CheckResult(
            name=name,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration,
        )
    except subprocess.TimeoutExpired:
        duration = (time.monotonic() - start) * 1000
        return CheckResult(
            name=name,
            exit_code=124,
            stderr=f"Timeout after {timeout}s",
            duration_ms=duration,
        )
    except FileNotFoundError:
        duration = (time.monotonic() - start) * 1000
        return CheckResult(
            name=name,
            exit_code=127,
            stderr=f"Command not found: {cmd[0]}",
            duration_ms=duration,
        )


def _run_auto_fix_phase() -> list[CheckResult]:
    """Phase 1+2: Run auto-fix steps sequentially (mutate files).

    ## @purpose  Apply all auto-fixes before read-only checks.
    ##           Phase 1: make fix-gate (exec bits + ruff + manifests)
    ##           Phase 2: pre-commit run --all-files (hygiene + verify)
    ## @io       → ⎋ list[CheckResult]: results of auto-fix steps
    """
    results: list[CheckResult] = []

    # Phase 1: make fix-gate
    print("[IMP:7][preflight] Phase 1/3: make fix-gate (auto-fix)...", file=sys.stderr)
    r = _run_check(
        ["make", "fix-gate"],
        "fix-gate",
        timeout=120,
        cwd=_PROJECT_ROOT,
    )
    results.append(r)
    if r.failed:
        print(f"[IMP:9][preflight] fix-gate FAILED (exit {r.exit_code})", file=sys.stderr)
        print(r.stderr[:2000], file=sys.stderr)
    else:
        print("[IMP:7][preflight] fix-gate PASSED", file=sys.stderr)

    # Phase 2: pre-commit run --all-files
    print("[IMP:7][preflight] Phase 2/3: pre-commit run --all-files (hygiene + verify)...", file=sys.stderr)
    r = _run_check(
        ["pre-commit", "run", "--all-files"],
        "pre-commit",
        timeout=120,
        cwd=_PROJECT_ROOT,
    )
    results.append(r)
    if r.failed:
        # Pre-commit auto-fixes hygiene files (trailing-whitespace, end-of-file-fixer).
        # Run it AGAIN to see if it's now clean after auto-fix.
        print("[IMP:8][preflight] pre-commit had issues — re-running to apply auto-fixes...", file=sys.stderr)
        r2 = _run_check(
            ["pre-commit", "run", "--all-files"],
            "pre-commit (retry)",
            timeout=120,
            cwd=_PROJECT_ROOT,
        )
        # Only replace if retry passed, otherwise keep original errors
        if r2.passed:
            results[-1] = CheckResult(
                name="pre-commit",
                exit_code=0,
                stdout="Auto-fixed by pre-commit (hygiene hooks)",
                auto_fixed=True,
                fixed_by="pre-commit run --all-files (auto-fix hygiene)",
            )
        else:
            results[-1] = r2  # Keep retry result with actual errors
    else:
        print("[IMP:7][preflight] pre-commit PASSED", file=sys.stderr)

    return results


def _run_parallel_checks(max_workers: int) -> dict[str, CheckResult]:
    """Phase 3: Run all read-only checks in parallel.

    ## @purpose  Execute all static verification checks concurrently.
    ##           No file mutations — safe for parallelism.
    ## @io       ⇥ max_workers: ThreadPoolExecutor worker count
    ##           → ⎋ dict[str, CheckResult]: name → result
    """
    python = _resolve_python()
    use_xdist = _has_xdist(python)

    # Build pytest args — add -n auto only if xdist is available.
    # Must be AFTER "pytest" (pytest argument, not python -m argument).
    gates_cmd = [
        python, "-m", "pytest", "tests/gates/",
        "-m", "gate and not requires_docker",
        "-q", "--tb=line", "--no-header",
    ]
    if use_xdist:
        # Insert after "pytest" at index 3: python -m pytest -n auto tests/gates/ ...
        gates_cmd.insert(3, "-n")
        gates_cmd.insert(4, "auto")

    predeploy_cmd = [
        python, "-m", "pytest", "tests/",
        "-m", "predeploy and not requires_docker",
        "-q", "--tb=line", "--no-header",
    ]

    # Define checks as (name, command_list, timeout_seconds)
    checks: list[tuple[str, list[str], int]] = [
        (
            "validate",
            ["bash", str(_PROJECT_ROOT / "core/entrypoints/validate.sh")],
            60,
        ),
        (
            "check-dead-code",
            ["make", "check-dead-code"],
            60,
        ),
        (
            "check-exception-patterns",
            ["make", "check-exception-patterns"],
            30,
        ),
        (
            "doxygen-check",
            ["make", "doxygen-check"],
            30,
        ),
        (
            "gates (static)",
            gates_cmd,
            180,
        ),
        (
            "contract",
            [python, "-m", "core.internal.test_runner", "--marker", "contract"],
            180,
        ),
        (
            "static_audit",
            [python, "-m", "core.internal.test_runner", "--marker", "static_audit"],
            300,
        ),
        (
            "predeploy",
            predeploy_cmd,
            180,
        ),
    ]

    results: dict[str, CheckResult] = {}
    env = os.environ.copy()
    env.setdefault("PYTEST_NO_ESCALATION", "1")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[concurrent.futures.Future[CheckResult], str] = {}
        for name, cmd, timeout in checks:
            future = executor.submit(
                _run_check, cmd, name, timeout, _PROJECT_ROOT, env
            )
            futures[future] = name

        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:  # noqa: EXC — best-effort thread pool wrapper, must not crash
                results[name] = CheckResult(
                    name=name,
                    exit_code=1,
                    stderr=f"Internal error collecting result for {name}: {exc}",
                )

    return results


# endregion CHECK_RUNNERS


# region REPORTING

def _format_report(
    phase_results: list[CheckResult],
    parallel_results: dict[str, CheckResult],
    total_duration_ms: float,
    json_output: bool = False,
) -> tuple[str, PreflightReport]:
    """Build the final report from all check results.

    ## @purpose  Aggregate all results, compute statistics, format output.
    ## @io       ⇥ phase_results: Phase 1+2 results (sequential)
    ##           ⇥ parallel_results: Phase 3 results (parallel)
    ##           ⇥ total_duration_ms: overall elapsed time
    ##           → ⎋ (formatted_string, PreflightReport dataclass)
    """
    all_results: list[CheckResult] = list(phase_results)
    all_results.extend(parallel_results.values())

    passed = sum(1 for r in all_results if r.passed)
    auto_fixed = sum(1 for r in all_results if r.auto_fixed)
    failed = sum(1 for r in all_results if r.failed and not r.auto_fixed)

    report = PreflightReport(
        status="green" if failed == 0 else "failed",
        total_checks=len(all_results),
        passed=passed,
        auto_fixed=auto_fixed,
        failed=failed,
        checks=[
            {
                "name": r.name,
                "exit_code": r.exit_code,
                "passed": r.passed,
                "auto_fixed": r.auto_fixed,
                "fixed_by": r.fixed_by,
                "duration_ms": round(r.duration_ms),
                "error_summary": r.error_summary() if r.failed else "",
            }
            for r in all_results
        ],
        duration_ms=total_duration_ms,
    )

    if json_output:
        return (report.to_json(), report)

    # Human-readable format
    lines: list[str] = []
    sep = "=" * 64
    subsep = "-" * 64

    lines.append(f"\n{sep}")
    lines.append(f"  PREFLIGHT REPORT: {report.status.upper()}")
    lines.append(f"{sep}")
    lines.append(
        f"  Duration: {total_duration_ms/1000:.1f}s  |  "
        f"Checks: {report.total_checks} total  |  "
        f"{report.passed} passed  |  "
        f"{report.auto_fixed} auto-fixed  |  "
        f"{report.failed} failed"
    )
    lines.append("")

    # Per-check status
    for r in all_results:
        if r.passed and not r.auto_fixed:
            marker = "PASS"
        elif r.auto_fixed:
            marker = "FIXED"
        else:
            marker = "FAIL"
        icon = {"PASS": "OK", "FIXED": "FX", "FAIL": "!!"}[marker]
        lines.append(f"  [{icon}] {r.name}: {marker} ({r.duration_ms/1000:.1f}s)")
        if r.fixed_by:
            lines.append(f"       Fixed by: {r.fixed_by}")

    # Failed details
    failed_checks = [r for r in all_results if r.failed and not r.auto_fixed]
    if failed_checks:
        lines.append(f"\n{subsep}")
        lines.append(f"  FAILED CHECKS ({len(failed_checks)}):")
        lines.append(f"{subsep}")
        for r in failed_checks:
            lines.append(f"\n  ### {r.name} (exit {r.exit_code})")
            summary = r.error_summary(max_lines=15)
            for line in summary.split("\n"):
                lines.append(f"      {line}")

    lines.append(f"\n{subsep}")
    if report.status == "green":
        lines.append("  RESULT: All checks PASS. Gate should be green.")
        lines.append("  NEXT:   make gate MODE=fast  (single verification)")
    else:
        lines.append(f"  RESULT: {report.failed} check(s) failed.")
        lines.append("  NEXT:   Fix ALL errors above, then:")
        lines.append("          make gate MODE=fast  (single verification)")
    lines.append(f"{sep}\n")

    return ("\n".join(lines), report)


# endregion REPORTING


# region MAIN

def main() -> int:
    """Preflight CLI entrypoint.

    ## @purpose  Parse args, run 3-phase preflight, output report.
    ## @io       ⇥ sys.argv: [--json] [--workers N] [--skip-fix]
    ##           → ⎋ int: 0 if all pass, 1 if errors collected
    """
    parser = argparse.ArgumentParser(
        description="Preflight — run all gate checks in parallel, collect errors once.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_MAX_WORKERS,
        help=f"Number of parallel workers (default: {_DEFAULT_MAX_WORKERS}).",
    )
    parser.add_argument(
        "--skip-fix",
        action="store_true",
        help="Skip Phase 1+2 auto-fix (fix-gate + pre-commit). Use when files are already clean.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output — show full stdout/stderr for failed checks.",
    )

    args = parser.parse_args()

    start = time.monotonic()
    all_phase_results: list[CheckResult] = []

    # Phase 1+2: Auto-fix (sequential)
    if not args.skip_fix:
        print("[IMP:7][preflight] Running auto-fix phases...", file=sys.stderr)
        fix_results = _run_auto_fix_phase()
        all_phase_results.extend(fix_results)

        # If fix-gate itself failed, we can't trust subsequent checks
        fix_gate_result = fix_results[0] if fix_results else None
        if fix_gate_result and fix_gate_result.failed:
            print(
                "[IMP:9][preflight] fix-gate failed — skipping Phase 3 (environment not clean)",
                file=sys.stderr,
            )
            total_ms = (time.monotonic() - start) * 1000
            report_str, _ = _format_report(
                all_phase_results, {}, total_ms, json_output=args.json
            )
            print(report_str)
            return 1

    # Phase 3: Read-only checks (parallel)
    print(f"[IMP:7][preflight] Phase 3/3: Running {8} checks in parallel "
          f"(workers={args.workers})...", file=sys.stderr)
    parallel_results = _run_parallel_checks(max_workers=args.workers)

    # Report
    total_ms = (time.monotonic() - start) * 1000
    report_str, report = _format_report(
        all_phase_results, parallel_results, total_ms, json_output=args.json
    )

    if args.verbose and not args.json:
        # Append full output for failed checks
        for r in [*all_phase_results, *parallel_results.values()]:
            if r.failed:
                report_str += f"\n\n=== FULL OUTPUT: {r.name} ===\n"
                report_str += r.stdout + "\n" + r.stderr

    print(report_str)

    return 0 if report.status == "green" else 1


if __name__ == "__main__":
    sys.exit(main())

# endregion MAIN
