#!/usr/bin/env python3
# GREP_SUMMARY: test-runner pytest wrapper junit xml compact summary marker static_audit test_file junit-output parse all merge
# STRUCTURE: ▶ main ┌marker→args | test_file→args_file┐ → ◇ static ? validate.sh→lint→pytest : ◇ all ? sequential suites + merge_junit : run(pytest --junitxml) → ⊕ parse_junit_xml → ⟦format_summary (compressed >20 fails)⟧ → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Тонкая Python-обёртка над pytest (DevPlan 098, Уровень A): один вызов bash-tool
##           возвращает компактный machine-readable результат — PASS/FAIL/SKIP/ERROR counts +
##           список failed тестов с first-line message. Решает C1 (timeout 120s), C2 (усечение
##           2000 строк), C3 (лишний --collect-only), C6 (единый формат) проблемы bash-tool.
## @scope    core/internal/test_runner.py — stdlib-only, Python 3.10+. PEP 420 namespace package
##           (core/ и core/internal/ БЕЗ __init__.py). Запуск: python -m core.internal.test_runner
## @invariants
##   - stdout = ТОЛЬКО machine-readable summary; IMP-логи идут в stderr через logging
##   - Вывод < 100 строк ВСЕГДА (AC1): compression — MAX_FAIL_DETAILS=20 при >20 failures
##   - PYTEST_NO_ESCALATION=1 ВСЕГДА в env subprocess (AC10: anti-loop контракт, _conftest/session.py:255)
##   - subprocess timeout default 1800s (AC8) — wrapper не висит при зависшем Docker healthcheck
##   - JUnit XML temp dir автоочищается в finally-блоке (AC4); --junit-output → без автоочистки
##   - exit code = pytest returncode (0 pass / 1 fail / 2 error) | 124 timeout (AC6)
##   - Два режима: marker (маркерный фильтр на tests/) | test_file (один файл, --test-file, AC6)
## @rationale Уровень A DevPlan 098: <100 строк вывода укладывается в лимиты bash-tool
##            (120s / 2000 строк / 51200 bytes), counts в первой строке устраняют --collect-only,
##            единый формат для всех MARKER'ов. stdout/stderr разделение сохраняет
##            machine-readability summary для агента.
## @changes 2026-07-31 | Wave 1: core wrapper (F1) per DevPlan 098 §7
## @changes 2026-07-31 | DevPlan 099: --junit-output (явный путь, для 'make test' routing),
##            --test-file (один файл, AC6), _build_pytest_args_file()
## @changes 2026-07-31 | DevPlan 095 AC9/AC12: static_audit expr исключает requires_node
##            (E2E pipeline тесты не входят в gate/test-all — иначе 11 тестов FAIL без NODE, Rule R4)
## @changes 2026-07-31 | DevPlan 098 close-out (VR 098): MARKER=all (AC2, DRIFT-2) —
##            _run_all_suites + merge_junit агрегация; FAIL compression (AC1) —
##            MAX_FAIL_DETAILS=20 при >20 failures; doxygen docstring XML-escape (DevPlan 097)
## @changes 2026-08-02 | DevPlan 120 §3.3 (Wave 1): xdist (-n auto) во ВСЕ pytest-инвокации
##            (marker-режим, _run_static_full, _run_all_suites) при доступности pytest-xdist;
##            TEST_NO_XDIST=1 отключает (слабые машины, диагностика гонок). test_file-режим
##            (один файл) — без xdist (нет выигрыша). Корень ускорения preflight 254s → ~60s.
## @changes 2026-08-03 | DevPlan 124 T2a/T2c (A2+): docker-маркеры {smoke, component, integration,
##            predeploy-docker} исключены из -n auto (_xdist_args(marker)) — single-process стек;
##            docker-pytest-процессы обёрнуты в процессный flock tests/.docker-suite.lock
##            (_docker_suite_lock/_run_docker_pytest) — межсессионная сериализация (F4)
# endregion MODULE_CONTRACT

import argparse
import contextlib
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

from core.internal.shared.exceptions import ConfigValidationError, PlatformError

logger = logging.getLogger(__name__)

# ── Marker → pytest expression mapping ──────────────────────────────────────
# Зеркало ci.mk строки 24-105 (make test MARKER=...). См. TRAP[DESIGN] ниже.
# not requires_node: DevPlan 095 AC9/AC12 — E2E pipeline тесты (tests/e2e/, маркер
# requires_node) НЕ входят в make test MARKER=static_audit/static, MARKER=all и
# make gate MODE=fast. Без этого фильтра 11 E2E-тестов подхватываются выражением
# (у них нет e2e/component/smoke/... маркеров) и FAIL без NODE env (Rule R4).
_STATIC_AUDIT_EXPR = (
    "static_audit or (not e2e and not component and not smoke "
    "and not integration and not local_auth and not requires_docker "
    "and not requires_node)"
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
# · all (None) = special handler → _run_all_suites (AC2: sequential suites + merge_junit).
# ·   DRIFT-2 close-out (VR 098): "all" больше не Unknown MARKER.
MARKER_MAP: dict[str, list[str] | None] = {
    "static": None,  # special handler: validate.sh → lint → pytest (AC7)
    "all": None,  # special handler: sequential suites + merge_junit aggregation (AC2)
    "static_audit": ["-m", _STATIC_AUDIT_EXPR],
    "smoke": ["-m", "smoke", "-rs"],
    "component": ["-m", "component", "-rs"],
    "integration": ["-m", "integration", "-rs"],
    "predeploy": ["-m", "predeploy", "-rs"],
    "contract": ["-m", "contract"],
    "e2e": ["-m", "e2e", "-rs"],
    "local_auth": ["-m", "local_auth"],
}

# MARKER=all suite order — зеркало `make test MARKER=all` pytest-шагов (ci.mk строки 74-84):
# contract → static_audit → predeploy → smoke → component → integration.
# e2e исключён (external *.tronyx.ru manual, ci.mk:59-63 — НЕ входит в make test MARKER=all);
# local_auth не входит в make test MARKER=all (ci.mk full suite); static — special handler
# (pytest-часть покрыта static_audit; validate+lint — отдельный прогон, не дублируются).
_ALL_SUITES_ORDER: list[str] = [
    "contract",
    "static_audit",
    "predeploy",
    "smoke",
    "component",
    "integration",
]

# AC1 close-out (VR 098): при > MAX_FAIL_DETAILS failures вывод обрезается до первых N +
# "... and M more" — компактный режим держит < 100 строк при ЛЮБОМ числе failures (ранее
# 2 строки на failure: 119 failures → 244 строки, DevPlan 098 AC1 нарушен).
MAX_FAIL_DETAILS = 20

# DevPlan 124 T2a (A2+, решение пользователя 2026-08-03): docker-сьюты исключены из -n auto —
# single-process (один compose-стек на машину). xdist на docker-сьютах давал гонку стека
# (эксперимент 2026-08-03: 5 passed / 7 errors при -n 2): воркеры конкурентно поднимали/сносили
# один стек (smoke.py:832 pre-cleanup `down --remove-orphans` + smoke.py:858 `rm -f` — факт 6).
# "predeploy-docker" — defensive (check-suite id, в test_runner не приходит; маркер "predeploy"
# НЕ docker-сьют — его xdist-политику контролирует check-suite через не-requires_docker выражение).
_DOCKER_MARKERS = {"smoke", "component", "integration", "predeploy-docker"}


# region FUNC_HAS_XDIST
## @purpose  Проверка доступности pytest-xdist (DevPlan 120 §3.3): локальный дубль
##           _has_xdist из прежнего preflight.py (предписан планом: «перенос в shared
##           или локальный дубль»). Любая ошибка = недоступен (best-effort).
## @io       ⇥ python_path: str → bool
## @complexity O(1) — subprocess python -c "import xdist"
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


# endregion FUNC_HAS_XDIST


# region FUNC_XDIST_ARGS
## @purpose  pytest-аргументы xdist: ["-n", "auto"] при доступности xdist, отсутствии
##           TEST_NO_XDIST=1 и НЕ docker-маркере; [] иначе (DevPlan 124 T2a, A2+).
##           Docker-сьюты (smoke/component/integration/predeploy-docker) — single-process:
##           их исключение ДЕЙСТВУЕТ независимо от TEST_NO_XDIST, т.к. агентский путь
##           `make test-summary MARKER=smoke` идёт через test_runner, а НЕ через check_suite
##           (F11 — TEST_NO_XDIST прокидывается только check_suite-инвокациями).
## @io       ⇥ marker: str | None (имя маркерной суиты) → ⎋ list[str] ([] или ["-n", "auto"])
## @complexity O(1)
def _xdist_args(marker: str | None = None) -> list[str]:
    """Return pytest xdist args (`-n auto`) unless docker marker, TEST_NO_XDIST=1, or xdist unavailable."""
    if marker in _DOCKER_MARKERS:
        # Docker-сьюты — single-process по построению (DevPlan 124 T2a): воркеры конкурентно
        # поднимают/сносят один стек (гонка факта 6). Меняется ТОЛЬКО способ исполнения,
        # не набор тестов (AC-9 честность).
        return []
    if os.environ.get("TEST_NO_XDIST") == "1":
        return []
    if _has_xdist(sys.executable):
        return ["-n", "auto"]
    return []


# endregion FUNC_XDIST_ARGS


# region FUNC_DOCKER_SUITE_LOCK
## @purpose  Процессный advisory flock на tests/.docker-suite.lock (DevPlan 124 T2c, A2+):
##           межсессионная сериализация docker-pytest-процессов. Два агента, одновременно
##           гоняющих docker-сьюты, НЕ пересекаются по compose-стеку (F4): master-клинер
##           одной сессии не сносит активный стек другой. Единый lock-файл для ВСЕХ
##           docker-pytest-процессов машины (test_runner + check_suite — зеркало).
##           Реализация — fcntl.flock (прецедент _CounterLock в _conftest/counter.py,
##           DevPlan 120 §3.3), НЕ flock-CLI: утилита отсутствует на macOS по умолчанию,
##           fcntl доступен на darwin/linux и держит инвариант stdlib-only. Лок снимается
##           ядром при закрытии fd или завершении процесса-держателя — retry/release не нужны.
## @io       ⇥ platform_root: Path (корень репо) → contextmanager (lock удерживается внутри with)
## @complexity O(1)
# ⚠️ TRAP[DECISION] · 2026-08-03 · — · process-лок через fcntl.flock вместо flock-CLI
# · Rejected: префикс `flock tests/.docker-suite.lock` (буквальный текст DevPlan 124 T2c) —
# ·   на dev-машине macOS flock отсутствует (`which flock` → not found, 2026-08-03);
# ·   REQUIRES-запись плана «flock (coreutils, доступен на macOS)» фактически неверна;
# ·   flock-CLI сломал бы `make test-summary MARKER=smoke` на macOS (command not found)
# · Reason: fcntl.flock — тот же механизм ядра (advisory lock на открытом файле, flock(2)),
# ·   прецедент counter.py (DevPlan 120 §3.3), stdlib-only инвариант test_runner, одна
# ·   реализация на macOS/Linux. Единый lock-файл tests/.docker-suite.lock сохранён.
# · Rev: при появлении shell-потребителя лока (вне Python) — вынести в shared-модуль с CLI.
@contextlib.contextmanager
def _docker_suite_lock(platform_root: Path):
    """Context manager holding the process-level docker-suite flock (T2c)."""
    import fcntl  # lazy — POSIX-only (darwin/linux); не-docker пути работают на любой платформе

    lock_path = platform_root / "tests" / ".docker-suite.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[IMP:8][docker_lock][acquire] flock held: %s", lock_path)
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            logger.info("[IMP:8][docker_lock][release] flock released: %s", lock_path)


# endregion FUNC_DOCKER_SUITE_LOCK


# region FUNC_RUN_DOCKER_PYTEST
## @purpose  Запуск docker-сьюты под процессным локом (DevPlan 124 T2c): тот же
##           subprocess.run, что и обычный pytest, но wrapped в _docker_suite_lock —
##           docker-pytest-процессы на машине сериализуются по единому lock-файлу.
##           Лок удерживается на ВЕСЬ процесс (до возврата subprocess.run) — воркер B
##           ждёт завершения воркера A по тому же docker-стеку.
## @io       ⇥ pytest_args (list[str]), env (dict), timeout (int), platform_root (Path)
##             → ⎋ subprocess.CompletedProcess[str]
## @complexity O(1) + время subprocess
def _run_docker_pytest(
    pytest_args: list[str],
    env: dict[str, str],
    timeout: int,
    platform_root: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a docker-suite pytest process under the process-level docker-suite flock (T2c)."""
    with _docker_suite_lock(platform_root):
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(platform_root / "tests"), *pytest_args],
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )


# endregion FUNC_RUN_DOCKER_PYTEST


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
## @purpose  Парсинг JUnit XML → TestSummary: агрегация counts с \<testsuite\> и сбор
##           failed_tests из \<testcase\> с \<failure\>/\<error\> (DevPlan §4 data flow step 6)
## @io — path (str, JUnit XML file) → TestSummary
## @complexity — O(T) где T = общее число \<testcase\> по всем \<testsuite\>
## @rationale — root.iter("testsuite") вместо root.get(): pytest --junitxml оборачивает
##              вывод в \<testsuites\>, атрибуты живут на дочерних \<testsuite\> (см. TRAP[BUG])
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
##           (DevPlan §5 AC1). 2 строки на failure, но при > MAX_FAIL_DETAILS (20)
##           failures вывод сжимается: первые 20 + "... and M more" (AC1 close-out:
##           < 100 строк при ЛЮБОМ числе failures, 119 failures → 244 → 48 строк)
## @io — summary (TestSummary), marker (str), duration (float wall-clock s) → str
## @complexity — O(min(F, MAX_FAIL_DETAILS)) где F = число failed tests
def format_summary(summary: TestSummary, marker: str, duration: float) -> str:
    """Format compact summary: counts block + failed/error sections (compressed > 20)."""
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
        for f in failed[:MAX_FAIL_DETAILS]:
            detail = _first_line(f["message"]) or _first_line(f["text"])
            lines.append(f"FAIL {f['name']}")
            lines.append(f"     {detail}")
        hidden = len(failed) - MAX_FAIL_DETAILS
        if hidden > 0:
            lines.append(f"... and {hidden} more failures")
    if errors:
        lines.append("")
        lines.append("--- ERRORS ---")
        for e in errors[:MAX_FAIL_DETAILS]:
            detail = _first_line(e["message"]) or _first_line(e["text"])
            lines.append(f"ERROR {e['name']}")
            lines.append(f"       {detail}")
        hidden = len(errors) - MAX_FAIL_DETAILS
        if hidden > 0:
            lines.append(f"... and {hidden} more errors")

    logger.info("[IMP:9][format_summary][result] %s", " | ".join(lines[:6]))
    return "\n".join(lines)


# endregion FUNC_FORMAT_SUMMARY


# region FUNC_BUILD_PYTEST_ARGS
## @purpose  Маппинг marker → pytest CLI args (зеркало ci.mk строки 24-105, DevPlan §6).
##           static и all возвращают None — main() делегирует в специальные handlers
##           (_run_static_full / _run_all_suites). DRIFT-2 close-out: "all" больше не Unknown.
## @io — marker (str) → list[str] pytest args | None для static/all
## @complexity — O(1)
## @rationale — Номинальная аннотация DevPlan §7 — list[str], но static/all-path возвращает
##              None (special handlers). Аннотация list[str] | None честнее: специальные
##              маркеры обрабатываются отдельными диспетчерами, а не pytest-аргументами
def _build_pytest_args(marker: str) -> list[str] | None:
    """Resolve marker → pytest CLI args; None for `static`/`all` (special handlers)."""
    if marker in ("static", "all"):
        logger.info("[IMP:7][build_args][special] Marker=%s → special handler", marker)
        return None
    args = MARKER_MAP.get(marker)
    if args is None:
        valid = ", ".join(sorted(MARKER_MAP))
        logger.critical("[IMP:9][build_args][error] Unknown MARKER=%r. Valid values: %s", marker, valid)
        # T3.6 (DevPlan 116 B4): business sys.exit → raise ConfigValidationError (main ловит PlatformError)
        raise ConfigValidationError(f"Unknown MARKER='{marker}'. Valid values: {valid}")
    logger.info("[IMP:7][build_args][resolve] Marker=%s → %d arg(s): %s", marker, len(args), " ".join(args))
    return args


# endregion FUNC_BUILD_PYTEST_ARGS


# region FUNC_BUILD_PYTEST_ARGS_FILE
## @purpose  Построить pytest CLI args для одного тестового файла (без маркерного фильтра).
##           Use-case: make test-summary TEST_FILE=tests/unit/test_foo.py (AC6)
## @io — test_file (str, путь относительно platform_root) → list[str] (pytest args)
## @complexity — O(1)
def _build_pytest_args_file(test_file: str) -> list[str]:
    """Build pytest args for a single test file: quiet mode, short traceback."""
    logger.info("[IMP:7][build_args_file][resolve] TEST_FILE=%s → quiet mode + short traceback", test_file)
    return ["-q", "--tb=short", test_file]


# endregion FUNC_BUILD_PYTEST_ARGS_FILE


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

        pytest_args = [*_xdist_args("static"), "-m", _STATIC_AUDIT_EXPR, "--junitxml", str(junit_path)]
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


# region FUNC_RUN_ALL_SUITES
## @purpose  Имплементация `all` маркера (AC2, DRIFT-2 close-out): последовательный прогон
##           всех pytest-суит из _ALL_SUITES_ORDER (зеркало make test MARKER=all, ci.mk
##           строки 74-84), агрегация через tests/merge_junit.py (DevPlan §6: reuse,
##           НЕ новая логика агрегации), merged report пишется в junit_path → main()
##           парсит и печатает стандартный компактный summary.
## @io — platform_root (Path), junit_path (Path, merged report destination), timeout (int s) → int exit code
## @complexity — O(S) subprocess-вызовов (S = число суит + 1 merge)
## @invariants
##   - Порядок суит = _ALL_SUITES_ORDER (canonical ci.mk order)
##   - e2e/local_auth/static НЕ входят: external manual / вне make test MARKER=all / special handler
##   - Любой fail (exit != 0) → общий exit = max(exit codes) — fail не маскируется
##   - timeout на каждую суиту отдельно (AC8) — одна зависшая суита не блокирует остальные
##   - merge_junit пропускает missing files; если ни один junit не создан → fallback
## @rationale — AC2 требовал "all" без Unknown MARKER. ci.mk делает это через $(MAKE) test
##              recursion + merge_junit; wrapper повторяет последовательность нативно.
def _run_all_suites(platform_root: Path, junit_path: Path, timeout: int) -> int:
    """Run all MARKER_MAP suites sequentially; aggregate via merge_junit (AC2)."""
    env = {**os.environ, "PYTEST_NO_ESCALATION": "1"}
    suite_dir = junit_path.parent
    junit_files: list[Path] = []
    exit_codes: list[int] = []

    for marker in _ALL_SUITES_ORDER:
        args = MARKER_MAP[marker]
        assert args is not None, f"_ALL_SUITES_ORDER содержит special handler: {marker}"
        suite_junit = suite_dir / f"junit-{marker}.xml"
        pytest_args = [*_xdist_args(marker), *args, "--junitxml", str(suite_junit)]
        logger.info("[IMP:7][all_suites][run] Suite marker=%s", marker)
        proc: subprocess.CompletedProcess[str] | None = None
        try:
            if marker in _DOCKER_MARKERS:
                # DevPlan 124 T2c: docker-сьюты внутри MARKER=all — под процессным локом
                # (межсессионная гонка F4; T2a уже убрал их из -n auto)
                proc = _run_docker_pytest(pytest_args, env, timeout, platform_root)
            else:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", str(platform_root / "tests"), *pytest_args],
                    env=env,
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                )
            exit_codes.append(proc.returncode)
        except subprocess.TimeoutExpired:
            logger.critical("[IMP:9][all_suites][timeout] Suite marker=%s TIMEOUT after %ds", marker, timeout)
            print(f"TIMEOUT after {timeout}s (suite={marker})", file=sys.stderr)
            exit_codes.append(124)
        if suite_junit.exists():
            junit_files.append(suite_junit)
            logger.info("[IMP:7][all_suites][junit] Suite marker=%s → %s", marker, suite_junit)
        else:
            # Fallback: suite crashed before writing XML — tail captured output
            captured = proc.stderr or proc.stdout or "" if proc is not None else ""
            _print_no_xml_fallback(marker, captured)
            logger.warning("[IMP:7][all_suites][junit] Suite marker=%s produced no JUnit XML", marker)

    overall = max(exit_codes, default=2)
    if not junit_files:
        logger.critical("[IMP:9][all_suites][merge] No JUnit XML produced by any suite — no aggregation")
        return overall

    # Агрегация: tests/merge_junit.py (DevPlan §6 — reuse, missing files skip)
    merged = suite_dir / "merged.xml"
    merge_proc = subprocess.run(
        [
            sys.executable,
            str(platform_root / "tests" / "merge_junit.py"),
            *(str(f) for f in junit_files),
            "-o",
            str(merged),
        ],
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
    )
    if merge_proc.returncode != 0 or not merged.exists():
        logger.critical("[IMP:9][all_suites][merge] merge_junit failed exit=%d", merge_proc.returncode)
        return overall
    shutil.copy2(merged, junit_path)  # main() парсит merged report по стандартному пути
    logger.info("[IMP:9][all_suites][merge] Merged %d suite(s) → %s (exit=%d)", len(junit_files), junit_path, overall)
    return overall


# endregion FUNC_RUN_ALL_SUITES


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
##           --platform-root (default: авто-детект из расположения файла),
##           --junit-output (явный путь для JUnit XML, вместо temp dir),
##           --test-file (путь к конкретному тестовому файлу, AC6)
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
    parser.add_argument(
        "-o",
        "--junit-output",
        default=None,
        help="Write JUnit XML to explicit path (no temp dir, no auto-cleanup). For 'make test' routing.",
    )
    parser.add_argument(
        "-f",
        "--test-file",
        default=None,
        help="Run pytest on a single test file (no marker filter). Overrides --marker.",
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
## @purpose  Entry point: argparse → mode selection (marker или test_file) → subprocess pytest
##           (env с PYTEST_NO_ESCALATION=1, timeout) → parse JUnit → print summary → cleanup
## @io — argv (list[str] | None) → int exit code (pytest returncode | 124 timeout)
## @complexity — O(1) + время subprocess
## @rationale — stdout = только summary (machine-readable); IMP-логи в stderr. exit code
##              прокидывается как есть (AC6): 0 pass / 1 fail / 2 error / 124 timeout.
##              Режимы: marker (маркерный фильтр на tests/) | test_file (один файл, AC6).
##              --junit-output управляет размещением JUnit XML (temp dir или явный путь).
def main(argv: list[str] | None = None) -> int:
    """Run pytest with marker filter or single file, print compact JUnit-XML summary."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    platform_root = _resolve_platform_root(args.platform_root)
    log_label = f"marker={args.marker}" if not args.test_file else f"test_file={args.test_file}"
    logger.info(
        "[IMP:7][main][init] %s timeout=%ds platform_root=%s",
        log_label,
        args.timeout,
        platform_root,
    )

    # ── JUnit XML placement: explicit path or temp dir ──
    if args.junit_output:
        junit_path = Path(args.junit_output)
        tmpdir = None  # no temp dir → no cleanup needed
        logger.info("[IMP:7][main][junit] Explicit JUnit output: %s", junit_path)
    else:
        tmpdir = tempfile.mkdtemp(prefix="test-runner-")
        junit_path = Path(tmpdir) / "report.xml"
        # ensure parent directory exists (tmpdir всегда свежесозданная, но для явного пути — нужно)
        junit_path.parent.mkdir(parents=True, exist_ok=True)

    run_start = time.monotonic()
    proc: subprocess.CompletedProcess[str] | None = None
    try:
        # ── Mode selection: test_file (AC6) vs marker ──
        if args.test_file:
            # Single-file mode (AC6): no marker filter, run pytest on specific file
            pytest_args = _build_pytest_args_file(args.test_file)
            pytest_args = [*pytest_args, "--junitxml", str(junit_path)]
            test_target = str(platform_root / args.test_file)
            env = {**os.environ, "PYTEST_NO_ESCALATION": "1"}
            try:
                logger.info(
                    "[IMP:7][main][pytest] Running: python -m pytest %s %s",
                    test_target,
                    " ".join(pytest_args),
                )
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", test_target, *pytest_args],
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
        else:
            # Marker mode: resolve marker → pytest args
            if args.marker == "static":
                # static special handler (AC7): validate.sh → lint → pytest
                result_code = _run_static_full(platform_root, junit_path, args.timeout)
            elif args.marker == "all":
                # all special handler (AC2, DRIFT-2 close-out): sequential suites + merge_junit
                result_code = _run_all_suites(platform_root, junit_path, args.timeout)
            else:
                pytest_args = _build_pytest_args(args.marker)
                assert pytest_args is not None, f"marker={args.marker} не special handler, но вернул None"
                pytest_args = [*_xdist_args(args.marker), *pytest_args, "--junitxml", str(junit_path)]
                env = {**os.environ, "PYTEST_NO_ESCALATION": "1"}
                try:
                    logger.info(
                        "[IMP:7][main][pytest] Running: python -m pytest %s %s",
                        platform_root / "tests",
                        " ".join(pytest_args),
                    )
                    if args.marker in _DOCKER_MARKERS:
                        # DevPlan 124 T2c: docker-сьюты — под процессным локом (агентский путь
                        # make test-summary MARKER=smoke; межсессионная гонка F4)
                        proc = _run_docker_pytest(pytest_args, env, args.timeout, platform_root)
                    else:
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
        display_label = args.test_file if args.test_file else args.marker
        if junit_path.exists():
            summary = parse_junit_xml(str(junit_path))
            print(format_summary(summary, display_label, duration))
        elif proc is not None:
            # Fallback: pytest crashed before writing XML (collection error) — tail stderr
            _print_no_xml_fallback(display_label, proc.stderr or proc.stdout or "")
        else:
            # static/all special-handler paths — fallback уже напечатан внутри
            # _run_static_full / _run_all_suites
            logger.info("[IMP:9][main][parse] special handler path: JUnit XML not produced (fallback printed)")

        logger.info("[IMP:9][main][exit] exit=%d duration=%.1fs", result_code, duration)
        return result_code
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)  # AC4: авто-очистка только для temp dir


# endregion FUNC_MAIN


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
