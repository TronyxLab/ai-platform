# GREP_SUMMARY: loadtest report unit locust-csv parse verdict json markdown junit p95 thresholds tasks duration
# STRUCTURE: ▶ fixtures (stats.csv ok/errors/empty/tasks) → ◇ parse_stats_csv (header-based, Aggregated
#           + per-task tasks) → ◇ verdict_smoke/regression/capacity + apply_warnings → ◇ build_report/
#           render_markdown (duration_s + tasks) → ◇ junit-xml → ⎋ 17 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты отчёта (DevPlan 146 W2/W3 + 148 TASK-9, tests/unit/test_loadtest_report.py):
##           парс locust CSV (header-based, Aggregated-строка + per-task словарь tasks —
##           read_query/write_query, мусорные ячейки), вердикты
##           smoke/regression/capacity по порогам SoT, WARN-семантика (missing/insufficient
##           метрики не блокируют), report.json (duration_s + tasks — 148 TASK-6/7)/markdown/junit.xml.
## @scope    Чистые функции core/internal/loadtest/report.py — CSV-фикстуры инлайн
##           (tmp_path, Zero Hardcode Rule), без subprocess и сети.
## @invariants
##   - Парс по именам колонок (не позициям) — совместимость версий Locust 2.x
##   - Вердикты: smoke 0 errors + p95<max_p95; regression дельты baseline (1.5×/+2pp);
##     capacity max_rps>0; PASS+warnings → WARN (exit 0)
##   - first_run/baseline_reset → regression PASS только при p95 < max_p95 (абсолютный порог)
##   - parse_stats_csv → (Stats, tasks): перцентили ms→s; tasks — строки Name != Aggregated
##   - LDD: IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale Вердикт → exit-код (0/1) — ядро контракта runner (инвариант 9): парс и
##            пороговые сравнения тестируются детерминированно (AC2 regression-FAIL).
##            duration_s + tasks (148) — поля сводной статистики 3×3 (SC_STATS) и per-task
##            read/write PostgreSQL (SC_DB_RW).
## @changes  2026-08-11 | DevPlan 146 W2/W3 — Created
## @changes  2026-08-12 | DevPlan 148 TASK-9 — (Stats, tasks), duration_s + tasks-тесты
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib
import xml.etree.ElementTree as ET

import pytest

from core.internal.loadtest.report import (
    BaselineBlock,
    Stats,
    apply_warnings,
    build_report,
    parse_stats_csv,
    render_markdown,
    verdict_capacity,
    verdict_regression,
    verdict_smoke,
    write_junit_xml,
    write_report_json,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── CSV-фикстуры (заголовки Locust 2.x) ───────────────────────────────────────
_CSV_HEADER = (
    "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,"
    "Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,"
    "50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%"
)
_CSV_OK = [
    "GET,/status,1500,0,90,95,10,400,120,16.6,0.0,80,90,100,110,140,180,240,320,500,800,1200",
    ",Aggregated,1500,0,90,95,10,400,120,16.6,0.0,80,90,100,110,140,180,240,320,500,800,1200",
]
_CSV_ERRORS = [
    "GET,/status,1500,80,90,95,10,400,120,16.6,0.9,80,90,100,110,140,180,240,320,500,800,1200",
    ",Aggregated,1500,80,90,95,10,400,120,16.6,0.9,80,90,100,110,140,180,240,320,500,800,1200",
]
_CSV_SLOW_P95 = [
    "GET,/status,1500,0,900,950,10,4000,120,16.6,0.0,800,900,1000,1100,1400,1800,2400,3200,5000,8000,12000",
    ",Aggregated,1500,0,900,950,10,4000,120,16.6,0.0,800,900,1000,1100,1400,1800,2400,3200,5000,8000,12000",
]
# Per-task фикстура db (148 TASK-6): read_query/write_query + Aggregated — скорость записи vs чтения
_TASKS_CSV = [
    "SELECT,read_query,900,0,10,12,5,100,0,10.0,0.0,10,11,12,13,14,15,16,17,18,19,20",
    "INSERT,write_query,900,18,40,45,10,300,0,10.0,0.2,38,40,42,44,45,48,50,55,60,70,80",
    ",Aggregated,1800,18,25,28,5,300,0,20.0,0.1,24,25,27,28,30,35,40,45,50,60,70",
]
# Per-task ожидание для build_report (148 TASK-6/7)
_DB_TASKS = {
    "read_query": {"rps": 10.0, "p95": 0.015, "p99": 0.017, "error_rate": 0.0},
    "write_query": {"rps": 10.0, "p95": 0.048, "p99": 0.055, "error_rate": 0.02},
}


# region FIXTURE_write_csv
@pytest.fixture
def write_csv(tmp_path):
    """Запись CSV-фикстуры в tmp_path → путь.

    ## @purpose — Фабрика CSV-файлов: строит файл из строк-фикстур.
    """

    def _write(rows: list[str]) -> str:
        path = tmp_path / "stats.csv"
        path.write_text("\n".join([_CSV_HEADER, *rows]) + "\n", encoding="utf-8")
        return str(path)

    return _write


# endregion FIXTURE_write_csv


# ═══════════════════════════════════════════════════════════════════════════════
# parse_stats_csv
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_parse_stats_csv
# 🧪 TRAP[TEST] · Scenario: парс locust stats.csv (Aggregated-строка, мусорные ячейки)
# · Regression: error_rate = failures/requests; p95 None на мусорной ячейке (не crash);
# ·   перцентили нормализуются ms → s (BUG-3, 146-m3: 180ms → 0.18s)
# · Last fail: 2026-08-11 — боевой прогон tronyx-vps: p95=270 (ms) >= max_p95=1.0 (s) → ложный FAIL
# · Remove if: формат stats.csv Locust изменён (перцентили в других единицах)
class TestParseStatsCsv:
    def test_parses_aggregated_row(self, write_csv, caplog):
        """Aggregated-строка: rps/p95/p99 (ms → s) + error_rate 0.0."""
        caplog.set_level(logging.INFO)
        stats, tasks = parse_stats_csv(write_csv(_CSV_OK))
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (CSV parse success)"
        assert stats.total_requests == 1500 and stats.total_failures == 0
        assert stats.error_rate == 0.0
        # locust отдаёт перцентили в ms (180ms / 320ms) — Stats нормализуется в секунды
        assert stats.rps == 16.6 and stats.p95 == 0.18 and stats.p99 == 0.32
        # per-task строка "/status" (GET) — задачи web-сценария тоже попадают в tasks
        assert tasks == {"/status": {"rps": 16.6, "p95": 0.18, "p99": 0.32, "error_rate": 0.0}}

    def test_error_rate_computed(self, write_csv):
        """Ошибки: error_rate = 80/1500 ≈ 0.0533."""
        stats, _tasks = parse_stats_csv(write_csv(_CSV_ERRORS))
        assert stats.error_rate == pytest.approx(80 / 1500)
        assert stats.total_failures == 80

    def test_missing_file_returns_empty_stats(self, tmp_path):
        """Несуществующий CSV → (Stats с нулями, {}): rps/p95 None — insufficient."""
        stats, tasks = parse_stats_csv(tmp_path / "nope.csv")
        assert stats.rps is None and stats.p95 is None and stats.error_rate == 0.0
        assert tasks == {}

    def test_garbage_cells_do_not_crash(self, write_csv):
        """Мусорные ячейки перцентилей → None (не роняют парс)."""
        rows = [
            "GET,/status,100,0,90,95,10,400,120,1.1,0.0,80,90,100,110,140,broken,240,320,500,800,1200",
            ",Aggregated,100,0,90,95,10,400,120,1.1,0.0,80,90,100,110,140,broken,240,320,500,800,1200",
        ]
        stats, _tasks = parse_stats_csv(write_csv(rows))
        assert stats.p95 is None and stats.p50 == 0.08 and stats.total_requests == 100


# endregion TEST_parse_stats_csv


# region TEST_parse_stats_csv_tasks
# 🧪 TRAP[TEST] · Scenario: per-task breakdown (read_query/write_query — 148 TASK-6, SC_DB_RW)
# · Regression: parser берёт только Aggregated → скорость записи vs чтения PostgreSQL неразличима
# · Last fail: 2026-08-12 — db-сценарий 146 W1 не имел per-task строк (заглушка GET)
# · Remove if: формат stats.csv Locust изменён (нет per-task строк)
class TestParseStatsCsvTasks:
    def test_tasks_dict_from_csv(self, write_csv, caplog):
        """read_query/write_query → tasks {name: {rps, p95, p99, error_rate}} (перцентили ms→s)."""
        caplog.set_level(logging.INFO)
        stats, tasks = parse_stats_csv(write_csv(_TASKS_CSV))
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (CSV parse success)"
        assert set(tasks) == {"read_query", "write_query"}
        # read_query: p95=15ms → 0.015s; error_rate 0/900 = 0
        assert tasks["read_query"]["rps"] == 10.0
        assert tasks["read_query"]["p95"] == 0.015
        assert tasks["read_query"]["p99"] == 0.017
        assert tasks["read_query"]["error_rate"] == 0.0
        # write_query: p95=48ms → 0.048s; error_rate 18/900 = 0.02
        assert tasks["write_query"]["rps"] == 10.0
        assert tasks["write_query"]["p95"] == 0.048
        assert tasks["write_query"]["p99"] == 0.055
        assert tasks["write_query"]["error_rate"] == pytest.approx(0.02)
        # Aggregated остаётся в stats.* (обратная совместимость)
        assert stats.rps == 20.0 and stats.total_requests == 1800


# endregion TEST_parse_stats_csv_tasks


# ═══════════════════════════════════════════════════════════════════════════════
# Вердикты
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_verdicts
# 🧪 TRAP[TEST] · Scenario: вердикты smoke/regression/capacity + WARN-семантика
# · Regression: ошибки → FAIL; p95 >= max_p95 → FAIL; дельты 1.5×/+2pp → FAIL (AC2)
# · Last fail: N/A (new)
# · Remove if: критерии вердиктов DevPlan 146 §3.3 изменены
class TestVerdicts:
    @pytest.mark.parametrize(
        ("stats", "expected"),
        [
            (Stats(rps=10.0, p50=0.1, p95=0.3, p99=0.5, error_rate=0.0), "PASS"),  # 0 errors + p95 < 1s
            (Stats(rps=10.0, p95=0.3, error_rate=0.01), "FAIL"),  # error_rate > 0 → FAIL
            (Stats(rps=10.0, p95=1.5, error_rate=0.0), "FAIL"),  # p95 >= max_p95 → FAIL
            (Stats(error_rate=0.0), "FAIL"),  # p95 None (нет данных) → FAIL
        ],
    )
    def test_smoke_verdicts(self, stats, expected):
        """Parametrized: verdict_smoke PASS/FAIL критерии (AC2, F5-reduction)."""
        assert verdict_smoke(stats, max_p95=1.0) == expected

    @pytest.mark.parametrize(
        ("baseline_kwargs", "p95", "error_rate", "expected"),
        [
            # дельта в норме → PASS
            (
                {
                    "prev": {"p95": 0.25, "error_rate": 0.0},
                    "delta_p95": 1.2,
                    "delta_error_pp": 0.0,
                    "regression_fail": False,
                },
                0.3,
                0.0,
                "PASS",
            ),
            # p95 1.6× prev > 1.5× → FAIL (AC2)
            (
                {
                    "prev": {"p95": 0.2, "error_rate": 0.0},
                    "delta_p95": 1.6,
                    "delta_error_pp": 0.0,
                    "regression_fail": True,
                },
                0.32,
                0.0,
                "FAIL",
            ),
            # error +3pp > +2pp → FAIL даже при p95-норме
            (
                {
                    "prev": {"p95": 0.3, "error_rate": 0.01},
                    "delta_p95": 1.0,
                    "delta_error_pp": 3.0,
                    "regression_fail": True,
                },
                0.3,
                0.04,
                "FAIL",
            ),
            # first_run (нет prev) → PASS при p95 < max_p95
            ({"first_run": True}, 0.3, 0.0, "PASS"),
            # first_run, но p95 >= max_p95 → FAIL (абсолютный порог сохраняется)
            ({"first_run": True}, 2.0, 0.0, "FAIL"),
        ],
    )
    def test_regression_verdicts(self, baseline_kwargs, p95, error_rate, expected):
        """Parametrized: verdict_regression PASS/FAIL критерии (AC2, F5-reduction)."""
        baseline = BaselineBlock(**baseline_kwargs)
        stats = Stats(rps=10.0, p95=p95, error_rate=error_rate)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == expected

    def test_capacity_verdict(self):
        """capacity: max_rps>0 → PASS; 0 → FAIL."""
        assert verdict_capacity(32) == "PASS"
        assert verdict_capacity(0) == "FAIL"

    def test_apply_warnings(self):
        """PASS + warnings → WARN; FAIL + warnings → FAIL (не понижается)."""
        assert apply_warnings("PASS", ["metric missing"]) == "WARN"
        assert apply_warnings("FAIL", ["metric missing"]) == "FAIL"
        assert apply_warnings("PASS", []) == "PASS"


# endregion TEST_verdicts


# ═══════════════════════════════════════════════════════════════════════════════
# build_report / markdown / junit
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_report_artifacts
# 🧪 TRAP[TEST] · Scenario: report.json/markdown/junit артефакты (AC1: markdown-сводка)
# · Regression: junit failure-элемент при FAIL; report.json переживает round-trip
# · Last fail: N/A (new)
# · Remove if: формат артефактов отчёта изменён
class TestReportArtifacts:
    def _sample_report(self) -> dict:
        return build_report(
            scenario="web",
            mode="smoke",
            node="test-vps",
            endpoint="https://test.example.com/",
            version="abc123",
            stats=Stats(rps=10.0, p50=0.1, p95=0.3, p99=0.5, error_rate=0.0),
            saturation_aggregates={"cpu_nginx": {"avg": 0.5, "max": 0.6, "pct": 50.0}},
            baseline=BaselineBlock(first_run=True),
            verdict="PASS",
            warnings=[],
            timestamp="2026-08-11T12:00:00Z",
        )

    def test_build_report_structure(self):
        """report.json: все секции присутствуют, verdict PASS."""
        report_dict = self._sample_report()
        assert report_dict["scenario"] == "web" and report_dict["mode"] == "smoke"
        assert report_dict["verdict"] == "PASS"
        assert report_dict["stats"]["p95"] == 0.3
        assert report_dict["saturation"]["cpu_nginx"]["pct"] == 50.0
        assert report_dict["baseline"]["first_run"] is True
        assert report_dict["max_rps"] is None and report_dict["capacity_profile"] == []

    def test_report_json_roundtrip(self, tmp_path):
        """write_report_json → файл парсится обратно (atomic_write_json канон)."""
        import json

        path = tmp_path / "report.json"
        write_report_json(self._sample_report(), path)
        with pathlib.Path(path).open(encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["verdict"] == "PASS" and loaded["scenario"] == "web"

    def test_markdown_contains_key_sections(self):
        """Markdown-сводка: verdict, stats-таблица, saturation, baseline-пометки."""
        md = render_markdown(self._sample_report())
        assert "Verdict: `PASS`" in md
        assert "| rps | p50 | p95 |" in md
        assert "cpu_nginx" in md
        assert "First run" in md

    def test_junit_pass_no_failure(self, tmp_path):
        """junit PASS: 0 failures, нет failure-элемента."""
        path = tmp_path / "junit.xml"
        write_junit_xml(self._sample_report(), path)
        root = ET.parse(str(path)).getroot()
        assert root.get("failures") == "0"
        assert root.find("testcase/failure") is None

    def test_junit_fail_has_failure(self, tmp_path):
        """junit FAIL: failures=1, failure-элемент с телом отчёта."""
        report_dict = self._sample_report()
        report_dict["verdict"] = "FAIL"
        path = tmp_path / "junit.xml"
        write_junit_xml(report_dict, path)
        root = ET.parse(str(path)).getroot()
        assert root.get("failures") == "1"
        assert root.find("testcase/failure") is not None


# endregion TEST_report_artifacts


# region TEST_report_duration_and_tasks
# 🧪 TRAP[TEST] · Scenario: duration_s + tasks в report.json/markdown (148 TASK-6/7, SC_STATS/SC_DB_RW)
# · Regression: duration_s/tasks отсутствуют → пользователь не видит «сколько времени» и
# ·   «скорость записи vs чтения»; поля обязательны для сводной статистики 3×3
# · Last fail: N/A (new) — 148 TASK-6 (полей не существовало)
# · Remove if: формат отчёта изменён (duration_s/tasks удалены)
class TestReportDurationAndTasks:
    def _report(self) -> dict:
        return build_report(
            scenario="db",
            mode="smoke",
            node="test-vps",
            endpoint="postgres:5432",
            version="abc123",
            stats=Stats(rps=20.0, p50=0.02, p95=0.04, p99=0.06, error_rate=0.01, total_requests=1800),
            baseline=BaselineBlock(first_run=True),
            duration_s=95.4,
            tasks=_DB_TASKS,
            verdict="PASS",
            timestamp="2026-08-12T00:00:00Z",
        )

    def test_build_report_duration_and_tasks(self, caplog):
        """report.json содержит duration_s и tasks (read_query/write_query)."""
        caplog.set_level(logging.INFO)
        report_dict = self._report()
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (build_report)"
        assert report_dict["duration_s"] == 95.4
        assert report_dict["tasks"] == _DB_TASKS
        assert report_dict["tasks"]["write_query"]["error_rate"] == pytest.approx(0.02)

    def test_build_report_defaults_none(self):
        """Обратная совместимость: без duration_s/tasks → поля None (старые вызовы не ломаются)."""
        report_dict = build_report(
            scenario="web",
            mode="smoke",
            node="test-vps",
            endpoint="https://x/",
            version="v",
            stats=Stats(rps=10.0, p95=0.3, error_rate=0.0),
            verdict="PASS",
        )
        assert report_dict["duration_s"] is None
        assert report_dict["tasks"] is None

    def test_markdown_contains_duration_and_tasks(self, caplog):
        """Markdown: строка Duration + таблица tasks (| task | rps | p95 | p99 | error_rate |)."""
        caplog.set_level(logging.INFO)
        md = render_markdown(self._report())
        logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in list(caplog.records):
            if "[IMP:" in record.message:
                logger.info("%s", record.message)
                if "[IMP:9]" in record.message:
                    found = True
        logger.info("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (render_markdown)"
        assert "Duration: `95.4s`" in md
        assert "## Tasks (per-task)" in md
        assert "| task | rps | p95 | p99 | error_rate |" in md
        assert "| read_query | 10 | 0.015 | 0.017 | 0 |" in md
        assert "| write_query | 10 | 0.048 | 0.055 | 0.02 |" in md


# endregion TEST_report_duration_and_tasks
