#!/usr/bin/env python3
# GREP_SUMMARY: test-runner pytest wrapper junit xml compact summary marker static_audit parse
# STRUCTURE: ▶ main ┌marker→args┐ → ◇ static ? validate.sh→lint→pytest : run(pytest --junitxml) → ⊕ parse_junit_xml → ⟦format_summary⟧ → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Тонкая Python-обёртка над pytest (DevPlan 098, Уровень A): один вызов bash-tool
##           возвращает компактный machine-readable результат — PASS/FAIL/SKIP/ERROR counts +
##           список failed тестов с first-line message. Решает C1 (timeout 120s), C2 (усечение
##           2000 строк), C3 (лишний --collect-only), C6 (единый формат) проблемы bash-tool.
## @scope    core/internal/test_runner.py — stdlib-only, Python 3.10+. PEP 420 namespace package
##           (core/ и core/internal/ БЕЗ __init__.py). Запуск: python -m core.internal.test_runner
## @invariants
##   - stdout = ТОЛЬКО machine-readable summary; IMP-логи идут в stderr через logging
##   - Вывод < 100 строк для нормальных прогонов, NEVER > 2000 строк даже при 100+ failures (AC3)
##   - PYTEST_NO_ESCALATION=1 ВСЕГДА в env subprocess (AC10: anti-loop контракт, _conftest/session.py:255)
##   - subprocess timeout default 1800s (AC8) — wrapper не висит при зависшем Docker healthcheck
##   - JUnit XML temp dir автоочищается в finally-блоке (AC4)
##   - exit code = pytest returncode (0 pass / 1 fail / 2 error) | 124 timeout (AC6)
## @rationale Уровень A DevPlan 098: <100 строк вывода укладывается в лимиты bash-tool
##            (120s / 2000 строк / 51200 bytes), counts в первой строке устраняют --collect-only,
##            единый формат для всех MARKER'ов. stdout/stderr разделение сохраняет
##            machine-readability summary для агента.
## @changes 2026-07-31 | Wave 1: core wrapper (F1) per DevPlan 098 §7
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Marker → pytest expression mapping ──────────────────────────────────────
# Зеркало ci.mk строки 24-105 (make test MARKER=...). См. TRAP[DESIGN] ниже.
_STATIC_AUDIT_EXPR = (
    "static_audit or (not e2e and not component and not smoke "
    "and not integration and not local_auth and not requires_docker)"
)

# ⚠️ TRAP[DESIGN] · 2026-07-31 · MED · MARKER_MAP дублирует ci.mk строки 24-105 — сознательный компромисс
# · Проблема: mapping marker→pytest expression существует в двух местах (ci.mk test target
# ·   и MARKER_MAP). Нарушение DRY.
# · Альтернатива A: test_runner.py парсит ci.mk Makefile-синтаксис для извлечения mapping →
# ·   хрупко, shell-условия не парсятся надёжно.
# · Альтернатива B (выбрано в DevPlan §6): вынести mapping в YAML SoT, оба потребителя читают
# ·   оттуда — правильно, но вне рамок Уровня A. Зарегистрировано как debt для Уровня B/C.
# · Rev: при добавлении 4-го маркера или первом расхождении с ci.mk → рефакторинг в YAML SoT.
# · static (None) = special handler → _run_static_full (AC7: validate.sh → lint → pytest).
MARKER_MAP: dict[str, list[str] | None] = {
    "static": None,  # special handler: validate.sh → lint → pytest (AC7)
    "static_audit": ["-m", _STATIC_AUDIT_EXPR],
    "smoke": ["-m", "smoke", "-rs"],
    "component": ["-m", "component", "-rs"],
    "integration": ["-m", "integration", "-rs"],
    "predeploy": ["-m", "predeploy", "-rs"],
    "contract": ["-m", "contract"],
    "e2e": ["-m", "e2e", "-rs"],
    "local_auth": ["-m", "local_auth"],
}


# region FUNC_TESTSUMMARY
## @purpose  Иммутабельный результат прогона: counts + failed-детали (DevPlan §2 test_summary_CLASS)
## @io — pass/fail/skip/error counts, failed_tests (list[dict]), duration; total — computed property
## @complexity — O(1) доступ к полям
@dataclass
class TestSummary:
    """JUnit XML parse result — counts + failed test details (DevPlan 098)."""

    pass_count: int
    fail_count: int
    skip_count: int
    error_count: int
    failed_tests: list[dict] = field(default_factory=list)
    duration: float = 0.0

    # region FUNC_TESTSUMMARY_TOTAL
    ## @purpose  Полное число тестов прогона — computed property (pass+fail+skip+error),
    ##           соответствует `tests` attr pytest'а в JUnit XML. Не поле dataclass'а —
    ##           всегда согласовано с counts (иммутабельный результат)
    ## @io — ⎋ int
    ## @complexity — O(1)
    @property
    def total(self) -> int:
        """Total tests executed: pass + fail + skip + error."""
        return self.pass_count + self.fail_count + self.skip_count + self.error_count

    # endregion FUNC_TESTSUMMARY_TOTAL


# endregion FUNC_TESTSUMMARY


# region FUNC_FIRST_LINE
## @purpose  Извлечь первую непустую строку message/traceback, обрезанную до max_chars.
##           Гарантия AC3: 1 строка на failure — вывод никогда не превышает 2000 строк
## @io — text (str) → str (первая непустая строка, усечена до max_chars)
## @complexity — O(L) где L = длина text
def _first_line(text: str, max_chars: int = 200) -> str:
    """First non-empty line of a message/traceback, truncated."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:max_chars]
    return ""


# endregion FUNC_FIRST_LINE


# region FUNC_PARSE_JUNIT_XML
## @purpose  Парсинг JUnit XML → TestSummary: агрегация counts с <testsuite> и сбор
##           failed_tests из <testcase> с <failure>/<error> (DevPlan §4 data flow step 6)
## @io — path (str, JUnit XML file) → TestSummary
## @complexity — O(T) где T = общее число <testcase> по всем <testsuite>
## @rationale — root.iter("testsuite") вместо root.get(): pytest --junitxml оборачивает
##              вывод в <testsuites>, атрибуты живут на дочерних <testsuite> (см. TRAP[BUG])
def parse_junit_xml(path: str) -> TestSummary:
    """Parse JUnit XML report into a TestSummary."""
    # pytest-generated JUnit XML (trusted local artifact, not untrusted input);
    # defusedxml не требуется: файл создаётся самим pytest в tests/report-*.xml
    tree = ET.parse(path)  # nosec B314
    root = tree.getroot()
    total_tests = 0
    fail_count = 0
    error_count = 0
    skip_count = 0
    duration = 0.0
    suite_count = 0
    failed_tests: list[dict] = []

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Атрибуты считывались с <testsuites> wrapper
    # · Symptom: (tests/merge_junit.py:38) counts = 0 при валидном JUnit XML — tests=0, failures=0
    # · Root: pytest --junitxml оборачивает вывод в <testsuites>; атрибуты (tests, errors,
    # ·   failures, skipped, time) лежат на ДОЧЕРНИХ <testsuite> элементах, НЕ на wrapper
    # · Fix: root.iter("testsuite") — работает и с <testsuites> wrapper, и с одиночным <testsuite> корнем
    # · Prevention: структурное правило — все функции агрегации JUnit в этом модуле
    # ·   используют iter("testsuite") для извлечения атрибутов
    for testsuite in root.iter("testsuite"):
        suite_count += 1
        total_tests += int(testsuite.get("tests", 0))
        fail_count += int(testsuite.get("failures", 0))
        error_count += int(testsuite.get("errors", 0))
        skip_count += int(testsuite.get("skipped", 0))
        duration += float(testsuite.get("time", 0))
        for tc in testsuite.iter("testcase"):
            failure = tc.find("failure")
            error = tc.find("error")
            if failure is not None or error is not None:
                node = failure if failure is not None else error
                # НЕ включаем <system-out>/<system-err> — только failure/error контент
                failed_tests.append(
                    {
                        "name": tc.get("name", ""),
                        "type": "FAIL" if failure is not None else "ERROR",
                        "message": node.get("message", ""),
                        "text": (node.text or "").strip(),
                    }
                )

    pass_count = total_tests - fail_count - error_count - skip_count
    logger.info(
        "[IMP:7][parse_junit_xml][aggregate] Parsed %d testsuite(s), %d testcases total",
        suite_count,
        total_tests,
    )
    logger.info(
        "[IMP:9][parse_junit_xml][result] pass=%d fail=%d skip=%d error=%d total=%d",
        pass_count,
        fail_count,
        skip_count,
        error_count,
        total_tests,
    )
    return TestSummary(
        pass_count=pass_count,
        fail_count=fail_count,
        skip_count=skip_count,
        error_count=error_count,
        failed_tests=failed_tests,
        duration=duration,
    )


# endregion FUNC_PARSE_JUNIT_XML


# region FUNC_FORMAT_SUMMARY
## @purpose  Компактный machine-readable summary: header + counts + failed list
##           (DevPlan §5 AC1). 2 строки на failure (имя + first-line message) — AC3
## @io — summary (TestSummary), marker (str), duration (float wall-clock s) → str
## @complexity — O(F) где F = число failed tests
def format_summary(summary: TestSummary, marker: str, duration: float) -> str:
    """Format compact summary: counts block + failed/error sections."""
    lines = [
        f"=== TEST SUMMARY (marker={marker}, {duration:.1f}s) ===",
        f"PASS:  {summary.pass_count}",
        f"FAIL:  {summary.fail_count}",
        f"SKIP:  {summary.skip_count}",
        f"ERROR: {summary.error_count}",
        f"TOTAL: {summary.total}",
    ]
    failed = [f for f in summary.failed_tests if f["type"] == "FAIL"]
    errors = [f for f in summary.failed_tests if f["type"] == "ERROR"]

    if failed:
        lines.append("")
        lines.append("--- FAILED TESTS ---")
        for f in failed:
            detail = _first_line(f["message"]) or _first_line(f["text"])
            lines.append(f"FAIL {f['name']}")
            lines.append(f"     {detail}")
    if errors:
        lines.append("")
        lines.append("--- ERRORS ---")
        for e in errors:
            detail = _first_line(e["message"]) or _first_line(e["text"])
            lines.append(f"ERROR {e['name']}")
            lines.append(f"       {detail}")

    logger.info("[IMP:9][format_summary][result] %s", " | ".join(lines[:6]))
    return "\n".join(lines)


# endregion FUNC_FORMAT_SUMMARY


# region FUNC_BUILD_PYTEST_ARGS
## @purpose  Маппинг marker → pytest CLI args (зеркало ci.mk строки 24-105, DevPlan §6).
##           static возвращает None — main() делегирует в _run_static_full (AC7)
## @io — marker (str) → list[str] pytest args | None для static
## @complexity — O(1)
## @rationale — Номинальная аннотация DevPlan §7 — list[str], но static-path возвращает None
##              (special handler). Аннотация list[str] | None честнее: static обрабатывается
##              отдельным диспетчером, а не pytest-аргументами
def _build_pytest_args(marker: str) -> list[str] | None:
    """Resolve marker → pytest CLI args; None for `static` (special handler)."""
    if marker == "static":
        logger.info("[IMP:7][build_args][static] Marker=static → special handler (validate+lint+pytest)")
        return None
    args = MARKER_MAP.get(marker)
    if args is None:
        valid = ", ".join(sorted(k for k in MARKER_MAP if k != "static")) + ", static"
        logger.critical("[IMP:9][build_args][error] Unknown MARKER=%r. Valid values: %s", marker, valid)
        print(f"[IMP:9][test_runner] ERROR: Unknown MARKER='{marker}'. Valid values: {valid}", file=sys.stderr)
        sys.exit(1)
    logger.info("[IMP:7][build_args][resolve] Marker=%s → %d arg(s): %s", marker, len(args), " ".join(args))
    return args


# endregion FUNC_BUILD_PYTEST_ARGS


# region FUNC_RUN_STATIC_FULL
## @purpose  Имплементация `static` маркера (AC7): ПОЛНАЯ эквивалентность
##           `make test MARKER=static` — validate.sh → validate.sh --lint → pytest static_audit
## @io — platform_root (Path), junit_path (Path), timeout (int s) → int exit code
## @complexity — O(1) subprocess-вызовов (3)
## @rationale — validate/lint failure → bail (возвращаем их exit code, не идём к pytest)
def _run_static_full(platform_root: Path, junit_path: Path, timeout: int) -> int:
    """Run validate.sh → validate.sh --lint → pytest static_audit (AC7)."""
    validate = platform_root / "core" / "entrypoints" / "validate.sh"
    if not validate.exists():
        logger.critical("[IMP:9][static_full][error] validate.sh not found: %s", validate)
        print(f"[IMP:9][test_runner] ERROR: validate.sh not found at {validate}", file=sys.stderr)
        return 2

    env = {**os.environ, "PYTEST_NO_ESCALATION": "1"}
    try:
        logger.info("[IMP:7][static_full][validate] Running schema validation: %s", validate)
        r1 = subprocess.run(["bash", str(validate)], env=env, timeout=timeout, capture_output=True, text=True)
        if r1.returncode != 0:
            logger.critical("[IMP:9][static_full][validate] FAIL: validate.sh exit=%d", r1.returncode)
            print(f"[IMP:9][test_runner] FAIL: validate.sh exited {r1.returncode}", file=sys.stderr)
            print((r1.stderr or r1.stdout or "")[-4000:], file=sys.stderr)
            return r1.returncode

        logger.info("[IMP:7][static_full][lint] Running lint: validate.sh --lint")
        r2 = subprocess.run(["bash", str(validate), "--lint"], env=env, timeout=timeout, capture_output=True, text=True)
        if r2.returncode != 0:
            logger.critical("[IMP:9][static_full][lint] FAIL: lint exit=%d", r2.returncode)
            print(f"[IMP:9][test_runner] FAIL: validate.sh --lint exited {r2.returncode}", file=sys.stderr)
            print((r2.stderr or r2.stdout or "")[-4000:], file=sys.stderr)
            return r2.returncode

        pytest_args = ["-m", _STATIC_AUDIT_EXPR, "--junitxml", str(junit_path)]
        logger.info("[IMP:7][static_full][pytest] Running pytest static_audit: %s", " ".join(pytest_args))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(platform_root / "tests"), *pytest_args],
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if not junit_path.exists():
            _print_no_xml_fallback("static", proc.stderr or proc.stdout or "")
        return proc.returncode
    except subprocess.TimeoutExpired:
        logger.critical("[IMP:9][static_full][timeout] TIMEOUT after %ds", timeout)
        print(f"TIMEOUT after {timeout}s")
        return 124


# endregion FUNC_RUN_STATIC_FULL


# region FUNC_PRINT_NO_XML_FALLBACK
## @purpose  Fallback когда pytest завершился ДО создания JUnit XML (collection error / crash):
##           печатаем tail stderr + exit code (риск-table DevPlan §8)
## @io — marker (str), captured (str: stderr или stdout) → None (print)
## @complexity — O(S) где S = строк в captured
def _print_no_xml_fallback(marker: str, captured: str) -> None:
    """Print fallback summary when pytest exited before writing JUnit XML."""
    print(f"=== TEST SUMMARY (marker={marker}) ===")
    print("JUnit XML not produced — pytest exited before reporting (collection error or crash)")
    tail = [line for line in captured.splitlines() if line.strip()][-20:]
    if tail:
        print("--- PYTEST OUTPUT TAIL ---")
        for line in tail:
            print(f"  {line}")
    else:
        print("(no captured output)")


# endregion FUNC_PRINT_NO_XML_FALLBACK


# region FUNC_PARSE_ARGS
## @purpose  argparse: --marker (default static_audit per AC5), --timeout (default 1800s per AC8),
##           --platform-root (default: авто-детект из расположения файла)
## @io — argv (list[str]) → argparse.Namespace
## @complexity — O(1)
def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="test_runner",
        description="Compact pytest wrapper: JUnit XML → counts + failed list (<100 lines output).",
    )
    parser.add_argument(
        "--marker",
        default="static_audit",
        help="Test marker (default: static_audit — безопасный default без Docker, AC5)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Subprocess timeout in seconds (default 1800, AC8 — нет бесконечного hang)",
    )
    parser.add_argument(
        "--platform-root",
        default=None,
        help="Platform root (default: auto-detect as parent.parent.parent of this file)",
    )
    return parser.parse_args(argv)


# endregion FUNC_PARSE_ARGS


# region FUNC_RESOLVE_PLATFORM_ROOT
## @purpose  Определить корень платформы: явный --platform-root или авто-детект.
##           core/internal/test_runner.py → core/ → platform_root/ (3 уровня parent)
## @io — args_root (str | None) → Path
## @complexity — O(1)
def _resolve_platform_root(args_root: str | None) -> Path:
    """Resolve platform root from CLI arg or auto-detect from this file's location."""
    if args_root:
        root = Path(args_root).resolve()
        logger.info("[IMP:7][resolve_root][arg] platform_root=%s", root)
        return root
    root = Path(__file__).resolve().parent.parent.parent
    logger.info("[IMP:7][resolve_root][auto] platform_root=%s", root)
    return root


# endregion FUNC_RESOLVE_PLATFORM_ROOT


# region FUNC_MAIN
## @purpose  Entry point: argparse → marker resolution → subprocess pytest (env с
##           PYTEST_NO_ESCALATION=1, timeout) → parse JUnit → print summary → cleanup в finally
## @io — argv (list[str] | None) → int exit code (pytest returncode | 124 timeout)
## @complexity — O(1) + время subprocess
## @rationale — stdout = только summary (machine-readable); IMP-логи в stderr. exit code
##              прокидывается как есть (AC6): 0 pass / 1 fail / 2 error / 124 timeout
def main(argv: list[str] | None = None) -> int:
    """Run pytest with marker filter and print compact JUnit-XML summary."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    platform_root = _resolve_platform_root(args.platform_root)
    logger.info(
        "[IMP:7][main][init] marker=%s timeout=%ds platform_root=%s",
        args.marker,
        args.timeout,
        platform_root,
    )

    tmpdir = tempfile.mkdtemp(prefix="test-runner-")
    junit_path = Path(tmpdir) / "report.xml"
    run_start = time.monotonic()
    proc: subprocess.CompletedProcess[str] | None = None
    try:
        pytest_args = _build_pytest_args(args.marker)
        if pytest_args is None:
            # static special handler (AC7): validate.sh → lint → pytest
            result_code = _run_static_full(platform_root, junit_path, args.timeout)
        else:
            pytest_args = [*pytest_args, "--junitxml", str(junit_path)]
            env = {**os.environ, "PYTEST_NO_ESCALATION": "1"}
            try:
                logger.info(
                    "[IMP:7][main][pytest] Running: python -m pytest %s %s",
                    platform_root / "tests",
                    " ".join(pytest_args),
                )
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", str(platform_root / "tests"), *pytest_args],
                    env=env,
                    timeout=args.timeout,
                    capture_output=True,
                    text=True,
                )
                result_code = proc.returncode
            except subprocess.TimeoutExpired:
                logger.critical("[IMP:9][main][timeout] TIMEOUT after %ds", args.timeout)
                print(f"TIMEOUT after {args.timeout}s")
                return 124

        duration = time.monotonic() - run_start
        if junit_path.exists():
            summary = parse_junit_xml(str(junit_path))
            print(format_summary(summary, args.marker, duration))
        elif proc is not None:
            # Fallback: pytest crashed before writing XML (collection error) — tail stderr
            _print_no_xml_fallback(args.marker, proc.stderr or proc.stdout or "")
        else:
            # static path — fallback уже напечатан внутри _run_static_full
            logger.info("[IMP:9][main][parse] static path: JUnit XML not produced (fallback printed)")

        logger.info("[IMP:9][main][exit] exit=%d duration=%.1fs", result_code, duration)
        return result_code
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # AC4: авто-очистка JUnit XML temp dir


# endregion FUNC_MAIN


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
