# GREP_SUMMARY: flaky-detection, multi-run, harness, static-tests, background-load, junit, 5x-run, flaky, consistent-failure, T12.11
# STRUCTURE: ▶ 5× (start 2 load-pytest → run target pytest --junitxml → stop load) → ○ parse junit per run → ◇ classify: flaky (1-of-5) vs consistent (>=2) → ⎋ report dict
# region MODULE_CONTRACT
## @purpose  Flaky-detection harness (DevPlan 136 W12 T12.11): 5× прогон подмножества СТАТИЧЕСКИХ
##           тестов ПОД НАГРУЗКОЙ (2 параллельных pytest-прогона других файлов — CPU/IO churn),
##           фиксация flaky: тест, упавший РОВНО в 1 из 5 прогонов. Consistent-фейл (>=2) = реальный
##           баг → harness FAIL; flaky → находка (лог IMP:9 + отчёт), результат → Debt с Rev.
## @scope    Integration harness (маркер requires_node НЕ нужен — это локальные статические тесты,
##           но маркер integration ставится для изоляции от make test/gate: фильтр по маркерам).
##           Требует локальный pytest + junit-парсинг (xml.etree).
## @invariants
##   - Ровно 5 прогонов TARGET_TESTS, каждый ПОД фоновой нагрузкой (2 pytest-процесса LOAD_TESTS)
##   - Каждый прогон пишет junitxml в tmp_path/run<N>.xml — парсится для per-test outcomes
##   - Классификация: flaky = fail ровно в 1 из 5; consistent = fail в >= 2; pass = 0
##   - Harness НЕ фейлит на flaky (это находка для Debt); фейлит на consistent-fail (реальный баг)
##     и на технических ошибках harness'а (junit не создан/не распарсен)
##   - Запуск прогонов — subprocess.run(timeout=), никогда не висит
## @rationale DevPlan 136 W12 T12.11: multi-run flaky detection — эмпирическая оценка стабильности
##            статического сьюита под нагрузкой (флак параллельного прогона = баг теста, DevPlan 124).
## @changes 2026-08-05 | DevPlan 136 W12 T12.11 — создан
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

N_RUNS = 5
RUN_TIMEOUT = 600  # 10 мин на прогон статического подмножества (с запасом)

# Целевые тесты (подмножество статических): bootstrap-фазы + security-posture
TARGET_TESTS = [
    "tests/unit/test_bootstrap_phases.py",
    "tests/unit/test_security_posture.py",
]
# Фоновая нагрузка CPU/IO: два параллельных pytest-прогона других статических файлов
LOAD_TESTS = [
    "tests/unit/test_age_key.py",
    "tests/unit/test_atomic_writer.py",
    "tests/unit/test_audit_failure_paths.py",
]


# region HELPER_run_pytest_junit
def _run_pytest_junit(targets: list[str], junit_path: Path, timeout: int = RUN_TIMEOUT) -> int:
    """Один прогон pytest с junitxml-выводом. Возвращает returncode.

    ## @purpose — Изолированный pytest-прогон (без -n, single-process) с junitxml —
    ##            per-test исходы парсятся детерминированно (не текстовый вывод).
    ## @io       ⇥ targets, junit_path, timeout → ⎋ int (returncode)
    ## @complexity O(1) — один subprocess
    """
    args = [".venv/bin/python", "-m", "pytest", *targets, "-q", "--junitxml", str(junit_path), "-p", "no:cacheprovider"]
    logger.info("[IMP:8][flaky][run] %s", " ".join(args[-5:]))
    try:
        proc = subprocess.run(
            args,
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124
    return proc.returncode


# endregion HELPER_run_pytest_junit


# region HELPER_parse_junit
def _parse_junit(junit_path: Path) -> dict[str, str]:
    """Парсинг junitxml → {test_nodeid: 'passed'|'failed'|'error'|'skipped'}.

    ## @purpose — Извлечение per-test исходов из pytest junitxml (testcase/name + failure/error
    ##            элементы). Отсутствующий/битый junit → pytest.fail (harness integrity).
    ## @io       ⇥ junit_path: Path → ⎋ dict[str, str]
    ## @complexity O(T) где T = testcase-элементы
    """
    if not junit_path.is_file():
        pytest.fail(f"junit xml не создан: {junit_path} (harness integrity, T12.11)")
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as exc:
        pytest.fail(f"junit xml битый: {junit_path}: {exc}")
    outcomes: dict[str, str] = {}
    for tc in root.iter("testcase"):
        name = tc.get("classname", "") + "::" + tc.get("name", "")
        if tc.find("failure") is not None:
            outcomes[name] = "failed"
        elif tc.find("error") is not None:
            outcomes[name] = "error"
        elif tc.find("skipped") is not None:
            outcomes[name] = "skipped"
        else:
            outcomes[name] = "passed"
    return outcomes


# endregion HELPER_parse_junit


# region HELPER_start_load_pytest
def _start_load_process(load_target: str, workdir: Path) -> subprocess.Popen:
    """Запустить фоновый pytest-процесс нагрузки (CPU/IO churn, stdout в /dev/null).

    ## @purpose — Фоновая нагрузка для flaky-детекции: 2 параллельных pytest других файлов
    ##            создают CPU/IO контенцию, в которой флак проявляется (DevPlan 124).
    ## @io       ⇥ load_target, workdir → ⎋ Popen
    ## @complexity O(1)
    """
    del workdir  # unused — cwd = repo_root()
    with open(os.devnull, "w") as devnull:
        return subprocess.Popen(
            [".venv/bin/python", "-m", "pytest", load_target, "-q", "-p", "no:cacheprovider"],
            cwd=str(repo_root()),
            stdout=devnull,
            stderr=devnull,
        )


# endregion HELPER_start_load_pytest


# region TEST_flaky_detection
def test_flaky_detection_5x_under_load(caplog, tmp_path: Path) -> None:
    """5× прогон статического подмножества под нагрузкой — фиксация flaky (T12.11).

    # 🧪 TRAP[TEST] · Scenario: 5× TARGET_TESTS с 2 фоновыми pytest-нагрузками
    # · Regression: flaky тесты статического сьюита (DevPlan 124: флак параллельного прогона = баг)
    # · Last fail: N/A (новый harness T12.11)
    # · Remove if: harness заменён CI-уровневой flaky-детекцией (retry/rerun policy)
    ## @purpose — T12.11 (flaky detection): per-test исходы за 5 прогонов; классификация
    ##            flaky (1-of-5) vs consistent (>=2). Consistent → harness FAIL (реальный баг);
    ##            flaky → находка в отчёте (→ Debt с Rev), harness PASS.
    ## @io — ⇥ caplog, tmp_path → ⎋ None (asserts + report artifact)
    ## @complexity O(N_RUNS × (target + 2×load)) — ~5-15 мин суммарно
    """
    caplog.set_level(logging.DEBUG)

    per_run: dict[int, dict[str, str]] = {}
    for i in range(1, N_RUNS + 1):
        # ── Фоновая нагрузка (2 pytest-процесса) ────────────────────────────
        load_procs = [_start_load_process(t, tmp_path) for t in LOAD_TESTS[:2]]
        time.sleep(1)  # дать нагрузке разогнаться
        try:
            junit = tmp_path / f"run{i}.xml"
            _run_pytest_junit(TARGET_TESTS, junit)
            per_run[i] = _parse_junit(junit)
        finally:
            for p in load_procs:
                with contextlib.suppress(OSError):
                    p.terminate()

    # ── Агрегация per-test исходов ──────────────────────────────────────────
    all_tests: set[str] = set()
    for outcomes in per_run.values():
        all_tests.update(outcomes.keys())

    flaky: list[str] = []
    consistent_failures: list[str] = []
    for test_id in sorted(all_tests):
        fails = sum(1 for i in range(1, N_RUNS + 1) if per_run.get(i, {}).get(test_id) in ("failed", "error"))
        if fails >= 2:
            consistent_failures.append(f"{test_id} (fails={fails}/{N_RUNS})")
        elif fails == 1:
            flaky.append(f"{test_id} (fails=1/{N_RUNS})")

    # ── Отчёт (артефакт для Debt) ────────────────────────────────────────────
    report = {
        "runs": {str(i): per_run[i] for i in range(1, N_RUNS + 1)},
        "flaky_1of5": flaky,
        "consistent_failures": consistent_failures,
        "total_tests": len(all_tests),
    }
    report_path = tmp_path / "flaky_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    for test_id in flaky:
        logger.warning("[IMP:9][flaky] FLAKY (1-of-5): %s — находка для Debt (Rev-дата)", test_id)
    for test_id in consistent_failures:
        logger.error("[IMP:10][flaky] CONSISTENT FAILURE (>=2/5): %s — реальный баг", test_id)

    assert not consistent_failures, (
        f"Consistent failures in {len(consistent_failures)} test(s): {consistent_failures[:10]} — "
        "не flaky, а реальный баг (harness FAIL). Отчёт: " + str(report_path)
    )
    assert len(per_run) == N_RUNS, f"Harness integrity: ожидалось {N_RUNS} junit-прогонов, есть {len(per_run)}"
    logger.critical(
        "[IMP:9][flaky] 5× прогонов под нагрузкой: %d тестов, %d flaky (1-of-5), %d consistent",
        len(all_tests),
        len(flaky),
        len(consistent_failures),
    )
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")


# endregion TEST_flaky_detection
