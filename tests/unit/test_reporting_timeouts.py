# GREP_SUMMARY: reporting-timeouts healthcheck-invoke single-budget HEALTHCHECK_CMD_TIMEOUT AI-0012r module-interface
# STRUCTURE: ▶ inspect source (reporting/module_interface) → ◇ literal timeout=30 отсутствует → ◇ fake runner: healthcheck→60, install→180 → ⎋ единый бюджет
# region MODULE_CONTRACT
## @purpose  AI-0012r (DevPlan 17 T1.5): один бюджет healthcheck-invoke — reporting.run_healthchecks
##           и module_interface.invoke/dispatch используют HEALTHCHECK_CMD_TIMEOUT (60),
##           а не literal 30 / COMPOSE_UP_TIMEOUT 180 — один probe, одно время на всех путях.
## @scope    tests/unit: inspect.getsource + monkeypatched run_subprocess_streaming; без subprocess.
## @invariants
##   - В healthcheck-пути reporting.py нет literal `timeout=30`
##   - invoke/dispatch c interface="healthcheck" без явного timeout → HEALTHCHECK_CMD_TIMEOUT
##   - invoke/dispatch c interface="install" без явного timeout → COMPOSE_UP_TIMEOUT (compose-up канон)
# endregion MODULE_CONTRACT

import inspect
import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import reporting
from core.internal.shared import module_interface
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT, HEALTHCHECK_CMD_TIMEOUT

logger = logging.getLogger(__name__)


def _capturing_runner(captured: list[int]):
    """Fake run_subprocess_streaming: пишет timeout, возвращает rc=0."""

    class _Result:
        returncode = 0
        stderr = ""

    def _run(cmd: list[str], *, timeout: int = 0, **kwargs: object) -> _Result:
        captured.append(timeout)
        return _Result()

    return _run


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · единый бюджет healthcheck-invoke (AI-0012r)
# · Regression: reporting.py держал literal timeout=30, module_interface дефолт 180 —
#   тот же `<mod> healthcheck liveness` получал 30 или 60/180 в зависимости от пути
# · Scenario: (1) source run_healthchecks без `timeout=30`; (2) invoke/dispatch
#   healthcheck без явного timeout → 60; (3) install без явного timeout → 180;
#   (4) явный timeout перекрывает дефолт
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0012 partial-остаток)
# · Remove if: healthcheck-бюджет переезжает в конфигурируемый контракт с одним ридером
def test_healthcheck_invoke_single_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """reporting + module_interface используют HEALTHCHECK_CMD_TIMEOUT для healthcheck."""
    # ── 1. Source-инвариант reporting: literal 30 исчез, константа присутствует ──
    src = inspect.getsource(reporting.run_healthchecks)
    assert "timeout=30" not in src, "literal timeout=30 обязан быть заменён на HEALTHCHECK_CMD_TIMEOUT"
    assert "HEALTHCHECK_CMD_TIMEOUT" in src

    # ── 2. Поведенческий: invoke healthcheck (без явного timeout) → 60 ──
    captured: list[int] = []
    monkeypatch.setattr(module_interface, "run_subprocess_streaming", _capturing_runner(captured))
    ok, _err = module_interface.invoke("postgres", "healthcheck", "liveness")
    assert ok is True
    assert captured[-1] == HEALTHCHECK_CMD_TIMEOUT, (
        f"healthcheck-invoke обязан получить {HEALTHCHECK_CMD_TIMEOUT}: {captured}"
    )

    # ── 3. dispatch healthcheck через hermetic tmp-модуль (без явного timeout) → 60 ──
    import json

    mod_dir = tmp_path / "modules" / "herm"
    mod_dir.mkdir(parents=True)
    (mod_dir / "module.yaml").write_text(json.dumps({"interfaces": ["healthcheck"]}), encoding="utf-8")
    (mod_dir / "healthcheck.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    captured.clear()
    rc, dispatch_err = module_interface.dispatch("herm", "healthcheck", modules_dir=str(tmp_path / "modules"))
    assert rc == 0, f"healthcheck.sh exit 0 обязан дать rc 0: {dispatch_err}"
    assert captured[-1] == HEALTHCHECK_CMD_TIMEOUT, (
        f"dispatch healthcheck без явного timeout обязан получить {HEALTHCHECK_CMD_TIMEOUT}: {captured}"
    )

    captured.clear()
    monkeypatch.setattr(
        module_interface,
        "run_subprocess_streaming",
        _capturing_runner(captured),
    )
    module_interface.invoke("postgres", "install")
    assert captured[-1] == COMPOSE_UP_TIMEOUT, "install без явного timeout получает compose-up канон"

    # ── 4. Явный timeout перекрывает интерфейсный дефолт ──
    captured.clear()
    module_interface.invoke("postgres", "install", timeout=7)
    assert captured[-1] == 7, "явный timeout обязан иметь приоритет над дефолтом"

    logger.critical("[IMP:9][test] single healthcheck budget across paths — OK (AI-0012r)")
