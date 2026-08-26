# GREP_SUMMARY: reporting-retry permanent-error fail-fast healthcheck AI-0015 parallel-child-crash-logged AI-0016
# STRUCTURE: ▶ monkeypatched invoke → ◇ permanent stderr («no such file») → ⎋ 1 вызов, IMP:9 no-retry │ ◇ transient → ⎋ 10 попыток │ ▶ fork-children логируют crash перед _exit
# region MODULE_CONTRACT
## @purpose  AI-0015 (DevPlan 17 T3.4): постоянные ошибки healthcheck НЕ ретраятся (fail-fast
##           после 1-й попытки), transient — полный retry-бюджет. AI-0016: форкнутые дети
##           parallel_runner логируют причину краха до os._exit(1).
## @scope    tests/unit: monkeypatched invoke/sleep + статический скан fork-сайтов.
## @invariants
##   - Permanent stderr → ровно 1 вызов invoke + IMP:9 «PERMANENT failure»
##   - Transient stderr → hc_max_retries вызовов
##   - Все 3 fork-сайта parallel_runner содержат child_crash-лог перед os._exit(1)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import reporting

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting.time, "sleep", lambda _s: None)


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · постоянная ошибка не ретраится (AI-0015)
# · Regression: цикл ретраил ЛЮБУЮ not-ok — отсутствующий module.yaml жёг 100s (10×10s)
#   на модуль и маскировал конфигурационную ошибку под transient
# · Scenario: (1) invoke возвращает permanent-stderr («No such file … module.yaml») →
#   РОВНО 1 вызов + IMP:9 «PERMANENT failure»; (2) transient-stderr → все 10 попыток
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0015)
# · Remove if: invoke начинает возвращать типизированный error-kind вместо текста
def test_permanent_error_no_retry(tmp_path: Path, caplog: pytest.LogCaptureFixture, _no_sleep, monkeypatch: pytest.MonkeyPatch) -> None:
    """Permanent-ошибка → 1 вызов; transient → полный бюджет."""
    caplog.set_level(0)

    # ── 1. permanent: module.yaml отсутствует ──
    calls: list[tuple[str, str]] = []

    def _permanent_invoke(mod_name: str, interface: str, *args: str, timeout: int = 60):
        calls.append((mod_name, interface))
        return False, "bash: /opt/platform/core/modules/ghost/module.yaml: No such file or directory"

    monkeypatch.setattr(reporting, "module_interface_invoke", _permanent_invoke)

    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("modules:\n  ghost:\n    enabled: true\n", encoding="utf-8")

    reporting.run_healthchecks(str(node_yaml))

    assert len(calls) == 1, f"permanent-ошибка обязана дать 1 вызов, не {len(calls)}"
    assert any("PERMANENT failure" in r.getMessage() for r in caplog.records), (
        "IMP:9 no-retry запись обязательна"
    )
    print("--- LDD TRAJECTORY ---")
    for r in caplog.records:
        if "[IMP:" in r.getMessage():
            print(r.getMessage())
    print("--- END LDD TRAJECTORY ---")
    logger.critical("[IMP:9][test] permanent error fails fast — OK (AI-0015)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · transient ошибка сохраняет полный retry-бюджет
# · Regression: предикат T3.4 не должен срезать легитимные ретраи гонки старта
# · Scenario: invoke возвращает transient-stderr → invoke вызван hc_max_retries раз
# · Last fail: контрсценарий-охранник T3.4 (DevPlan 17)
# · Remove if: вместе с test_permanent_error_no_retry
def test_transient_still_retried(tmp_path: Path, caplog: pytest.LogCaptureFixture, _no_sleep, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(0)
    calls: list[str] = []

    def _transient_invoke(mod_name: str, interface: str, *args: str, timeout: int = 60):
        calls.append(mod_name)
        return False, "container starting: dependency wait"

    monkeypatch.setattr(reporting, "module_interface_invoke", _transient_invoke)

    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("modules:\n  slowpoke:\n    enabled: true\n", encoding="utf-8")

    reporting.run_healthchecks(str(node_yaml))

    assert len(calls) == reporting.hc_max_retries if hasattr(reporting, "hc_max_retries") else len(calls) == 10, (
        f"transient обязан использовать полный бюджет: {len(calls)} вызовов"
    )
    assert not any("PERMANENT failure" in r.getMessage() for r in caplog.records)
    logger.critical("[IMP:9][test] transient keeps full retry budget — OK (T3.4)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · форкнутые дети логируют крах до _exit (AI-0016)
# · Regression: except Exception → os._exit(1) без лога терял причину сбоя (OSError-цепь);
#   родитель видел только код выхода
# · Scenario: source-scan всех fork-сайтов parallel_runner: между except и _exit(1)
#   присутствует child_crash-лог с именем модуля
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0016); форк-поведение проверяется
#   статически — реальный fork в unit-тестах недетерминирован
# · Remove if: дети переезжают на multiprocessing.Pool с exception-транспортом
def test_child_crash_logged() -> None:
    src = (_REPO / "core/internal/bootstrap/deploy/parallel_runner.py").read_text(encoding="utf-8")
    crash_logs = src.count("child_crash")
    assert crash_logs >= 3, f"все fork-сайты обязаны логировать крах: найдено {crash_logs}"
    # каждый child_crash стоит ДО соответствующего os._exit(1) в том же except-блоке
    for chunk in src.split("except Exception as child_exc")[1:]:
        exit_pos = chunk.find("os._exit(1)")
        log_pos = chunk.find("child_crash")
        assert 0 <= log_pos < exit_pos, "лог обязан предшествовать os._exit(1)"
    logger.critical("[IMP:9][test] child crash reasons logged before exit — OK (AI-0016)")
