# GREP_SUMMARY: loadtest report unit locust-csv parse verdict json markdown junit p95 thresholds
# STRUCTURE: ▶ fixtures (stats.csv ok/errors/empty) → ◇ parse_stats_csv (header-based, Aggregated)
#           → ◇ verdict_smoke/regression/capacity + apply_warnings → ◇ build_report/render_markdown
#           → ◇ junit-xml → ⎋ 13 tests
# region MODULE_CONTRACT
## @purpose  Unit-тесты отчёта (DevPlan 146 W2/W3, tests/unit/test_loadtest_report.py):
##           парс locust CSV (header-based, Aggregated-строка, мусорные ячейки), вердикты
##           smoke/regression/capacity по порогам SoT, WARN-семантика (missing/insufficient
##           метрики не блокируют), report.json/markdown/junit.xml.
## @scope    Чистые функции core/internal/loadtest/report.py — CSV-фикстуры инлайн
##           (tmp_path, Zero Hardcode Rule), без subprocess и сети.
## @invariants
##   - Парс по именам колонок (не позициям) — совместимость версий Locust 2.x
##   - Вердикты: smoke 0 errors + p95<max_p95; regression дельты baseline (1.5×/+2pp);
##     capacity max_rps>0; PASS+warnings → WARN (exit 0)
##   - first_run/baseline_reset → regression PASS только при p95 < max_p95 (абсолютный порог)
##   - LDD: IMP:9 в успешных сценариях (Anti-Illusion Rule)
## @rationale Вердикт → exit-код (0/1) — ядро контракта runner (инвариант 9): парс и
##            пороговые сравнения тестируются детерминированно (AC2 regression-FAIL).
## @changes  2026-08-11 | DevPlan 146 W2/W3 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
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
# · Regression: error_rate = failures/requests; p95 None на мусорной ячейке (не crash)
# · Last fail: N/A (new)
# · Remove if: формат stats.csv Locust изменён
class TestParseStatsCsv:
    def test_parses_aggregated_row(self, write_csv, caplog):
        """Aggregated-строка: rps/p95/p99 + error_rate 0.0."""
        caplog.set_level(logging.INFO)
        stats = parse_stats_csv(write_csv(_CSV_OK))
        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in caplog.records:
            if "[IMP:" in record.message:
                print(record.message)
                if "[IMP:9]" in record.message:
                    found = True
        print("--- END LDD TRAJECTORY ---")
        assert found, "IMP:9 log missing (CSV parse success)"
        assert stats.total_requests == 1500 and stats.total_failures == 0
        assert stats.error_rate == 0.0
        assert stats.rps == 16.6 and stats.p95 == 180.0 and stats.p99 == 320.0

    def test_error_rate_computed(self, write_csv):
        """Ошибки: error_rate = 80/1500 ≈ 0.0533."""
        stats = parse_stats_csv(write_csv(_CSV_ERRORS))
        assert stats.error_rate == pytest.approx(80 / 1500)
        assert stats.total_failures == 80

    def test_missing_file_returns_empty_stats(self, tmp_path):
        """Несуществующий CSV → Stats с нулями (rps/p95 None — insufficient)."""
        stats = parse_stats_csv(tmp_path / "nope.csv")
        assert stats.rps is None and stats.p95 is None and stats.error_rate == 0.0

    def test_garbage_cells_do_not_crash(self, write_csv):
        """Мусорные ячейки перцентилей → None (не роняют парс)."""
        rows = [
            "GET,/status,100,0,90,95,10,400,120,1.1,0.0,80,90,100,110,140,broken,240,320,500,800,1200",
            ",Aggregated,100,0,90,95,10,400,120,1.1,0.0,80,90,100,110,140,broken,240,320,500,800,1200",
        ]
        stats = parse_stats_csv(write_csv(rows))
        assert stats.p95 is None and stats.p50 == 80.0 and stats.total_requests == 100


# endregion TEST_parse_stats_csv


# ═══════════════════════════════════════════════════════════════════════════════
# Вердикты
# ═══════════════════════════════════════════════════════════════════════════════


# region TEST_verdicts
# 🧪 TRAP[TEST] · Scenario: вердикты smoke/regression/capacity + WARN-семантика
# · Regression: ошибки → FAIL; p95 >= max_p95 → FAIL; дельты 1.5×/+2pp → FAIL (AC2)
# · Last fail: N/A (new)
# · Remove if: критерии вердиктов DevPlan 146 §3.3 изменены
class TestVerdicts:
    def test_smoke_pass(self):
        """0 errors + p95 < 1s → PASS."""
        stats = Stats(rps=10.0, p50=0.1, p95=0.3, p99=0.5, error_rate=0.0)
        assert verdict_smoke(stats, max_p95=1.0) == "PASS"

    def test_smoke_fail_on_errors(self):
        """error_rate > 0 → FAIL (даже при p95 в норме)."""
        stats = Stats(rps=10.0, p95=0.3, error_rate=0.01)
        assert verdict_smoke(stats, max_p95=1.0) == "FAIL"

    def test_smoke_fail_on_slow_p95(self):
        """p95 >= max_p95 → FAIL."""
        stats = Stats(rps=10.0, p95=1.5, error_rate=0.0)
        assert verdict_smoke(stats, max_p95=1.0) == "FAIL"

    def test_smoke_fail_no_data(self):
        """p95 None (нет данных) → FAIL (не PASS)."""
        assert verdict_smoke(Stats(error_rate=0.0), max_p95=1.0) == "FAIL"

    def test_regression_pass_with_delta(self):
        """regression_fail=False (дельта в норме) → PASS."""
        baseline = BaselineBlock(
            prev={"p95": 0.25, "error_rate": 0.0},
            delta_p95=1.2,
            delta_error_pp=0.0,
            regression_fail=False,
        )
        stats = Stats(rps=10.0, p95=0.3, error_rate=0.0)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == "PASS"

    def test_regression_fail_delta_p95(self):
        """regression_fail=True (p95 1.6× prev > 1.5×) → FAIL (AC2)."""
        baseline = BaselineBlock(
            prev={"p95": 0.2, "error_rate": 0.0},
            delta_p95=1.6,
            delta_error_pp=0.0,
            regression_fail=True,
        )
        stats = Stats(rps=10.0, p95=0.32, error_rate=0.0)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == "FAIL"

    def test_regression_fail_delta_error_pp(self):
        """regression_fail=True (error +3pp > +2pp) → FAIL даже при p95-норме."""
        baseline = BaselineBlock(
            prev={"p95": 0.3, "error_rate": 0.01},
            delta_p95=1.0,
            delta_error_pp=3.0,
            regression_fail=True,
        )
        stats = Stats(rps=10.0, p95=0.3, error_rate=0.04)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == "FAIL"

    def test_regression_first_run_pass(self):
        """first_run (нет prev) → PASS при p95 < max_p95 (сравнение недействительно)."""
        baseline = BaselineBlock(first_run=True)
        stats = Stats(rps=10.0, p95=0.3, error_rate=0.0)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == "PASS"

    def test_regression_first_run_absolute_fail(self):
        """first_run, но p95 >= max_p95 → FAIL (абсолютный порог сохраняется)."""
        baseline = BaselineBlock(first_run=True)
        stats = Stats(rps=10.0, p95=2.0, error_rate=0.0)
        assert verdict_regression(stats, max_p95=1.0, baseline=baseline) == "FAIL"

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
        with open(path, encoding="utf-8") as f:
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
