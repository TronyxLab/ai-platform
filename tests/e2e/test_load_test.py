# GREP_SUMMARY: loadtest e2e requires-node smoke web report vps
# STRUCTURE: ▶ requires_node fixture → ◇ runner_cli.main([web, smoke, skip-prometheus, skip-baseline])
#           → ◇ report.json в LOAD_RESULTS_DIR → ◇ verdict PASS/WARN → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  E2E-тест подсистемы нагрузочного тестирования (DevPlan 146 W5, AC1): полный
##           smoke-прогон web-сценария против тестовой VPS (requires_node) через
##           runner_cli.main() — нативный вызов CLI-оркестратора, проверка report.json
##           и exit 0. НЕ входит в make test/gate (фильтр not requires_node).
## @scope    tests/e2e/ — запуск только `make test-node NODE=<test>` (требуются NODE env,
##           SSH, деплой nginx на ноде, locust в окружении: pip install -e ".[load]").
## @invariants
##   - @pytest.mark.requires_node + fixture requires_node (R4: FAIL, не skip)
##   - --skip-prometheus/--skip-baseline: e2e проверяет прогон и отчёт, НЕ телеметрию
##     (PromQL-pull покрыт unit-тестами; saturation на ноде — ручной AC1) и НЕ пишет
##     в репо-историю (core/loadtest/history/ — коммитится, тест не должен её мутировать)
##   - LOAD_RESULTS_DIR → tmp_path (Zero Hardcode Rule; результаты теста вне репо)
##   - Smoke ≥ 90s: e2e-прогон длится ~2 минуты (инвариант 10 DevPlan 146)
##   - LDD: IMP:7-10 траектория печатается; assert IMP:9 (Anti-Illusion Rule)
## @rationale Сквозной контракт AC1 (exit 0 + report.json) на реальной ноде —
##            единственный способ поймать интеграционные сбои (резолв ноды, locust CLI,
##            CSV-парс в реальном прогоне). Вердикт FAIL на неразвёрнутой ноде — честный
##            результат (R4), а не skip: load-test требует деплоя сервисов.
## @changes  2026-08-11 | DevPlan 146 W5 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging

import pytest

from core.internal.loadtest import runner_cli

logger = logging.getLogger(__name__)


# region TEST_load_test_smoke_web
# 🧪 TRAP[TEST] · E2E · smoke web против тестовой VPS (AC1 DevPlan 146)
# · Scenario: runner_cli.main → exit 0, report.json с verdict PASS/WARN и p95
# · Last fail: N/A (new); на неразвёрнутой ноде (nginx недоступен) → честный FAIL
# · Remove if: CLI-контракт runner_cli изменён (аргументы/exit-коды)
@pytest.mark.requires_node
class TestLoadTestE2E:
    def test_smoke_web_report(self, requires_node, tmp_path, monkeypatch, caplog):
        """Полный smoke-прогон web-сценария: exit 0 + report.json (verdict PASS/WARN)."""
        monkeypatch.setenv("LOAD_RESULTS_DIR", str(tmp_path))
        monkeypatch.delenv("LOAD_RUNNER", raising=False)
        monkeypatch.delenv("LOAD_RPS", raising=False)
        monkeypatch.delenv("LOAD_DURATION", raising=False)
        caplog.set_level(logging.INFO)

        logger.info("[IMP:9][e2e][load_test] Starting smoke web against node=%s (run >= 90s)", requires_node)
        exit_code = runner_cli.main(
            [
                "--scenario",
                "web",
                "--node",
                requires_node,
                "--mode",
                "smoke",
                "--skip-prometheus",
                "--skip-baseline",
            ]
        )

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        found = False
        for record in caplog.records:
            if "[IMP:" in record.message:
                print(record.message)
                if "[IMP:9]" in record.message:
                    found = True
        print("--- END LDD TRAJECTORY ---")
        assert found, "Critical LDD Error: No IMP:9 log found in e2e run"

        # 146-m1 BUG-1 guard: locust-argv не должен содержать несуществующий rate-limit
        # флаг — иначе stderr/логи содержат "unrecognized arguments: --max-rps" (rc=2).
        run_logs = "\n".join(r.message for r in caplog.records)
        assert "unrecognized arguments" not in run_logs, (
            "locust CLI-флаг не существует (например rate-limit флаг) — runner упал с "
            "'unrecognized arguments' (146-m1 BUG-1 регрессия)"
        )

        assert exit_code == 0, f"smoke-прогон web завершился с exit={exit_code} (verdict FAIL?)"
        reports = list(tmp_path.rglob("report.json"))
        assert reports, "report.json не создан в LOAD_RESULTS_DIR"
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        assert report["scenario"] == "web" and report["mode"] == "smoke"
        assert report["verdict"] in ("PASS", "WARN"), f"verdict={report['verdict']} (FAIL при ошибках/медленном p95)"
        assert report["stats"]["p95"] is not None, "p95 отсутствует в отчёте (прогон без данных)"
        logger.info(
            "[IMP:9][e2e][load_test] PASS: verdict=%s rps=%s p95=%s errors=%d",
            report["verdict"],
            report["stats"]["rps"],
            report["stats"]["p95"],
            report["stats"]["total_failures"],
        )


# endregion TEST_load_test_smoke_web
