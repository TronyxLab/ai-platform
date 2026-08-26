# GREP_SUMMARY: test-healthcheck-guard, F-016, NODE-guard, healthcheck-entrypoint, remote-hint, fail-loud, shell-facade
# STRUCTURE: ▶ run healthcheck.sh with NODE env → ◇ NODE=prod → exit 1 + hint | ◇ NODE=local → delegate | ◇ no NODE → delegate → ⎋ assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for healthcheck entrypoint NODE-guard (plan 012 T16 / F-016).
##           Операторская машина с NODE=<n> НЕ должна молча проверять локальный docker —
##           fail-loud с подсказкой (e2e-verify / ssh на ноду).
## @scope    Bash-facade core/entrypoints/healthcheck.sh — исполняется через subprocess
##           (static-контракт реального фасада, R5 check_tcp-паттерн test_healthcheck_lib).
## @invariants
##   - subprocess используется ТОЛЬКО для исполнения shell-фасада (легитимный статический
##     контракт — не бизнес-логика; см. test_healthcheck_lib.py прецедент)
##   - NODE=<n> (≠local) → exit 1 + hint в stderr, Python-делегат НЕ вызывается
##   - NODE=local / пустой → exit прогона делегата (Python-модуль вызван)
##   - Каждый тест — TRAP[TEST] с Regression/Scenario/Remove if
## @changes 2026-08-26 · plan 012 T16 (F-016) — создан
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_ENTRYPOINT: Path = Path(__file__).resolve().parent.parent.parent / "core" / "entrypoints" / "healthcheck.sh"

pytestmark = pytest.mark.static_audit


# region HELPERS


def _run_entrypoint(node: str | None, fake_delegate: bool = False) -> subprocess.CompletedProcess:
    """Run healthcheck.sh with NODE env; fake_delegate → Python-модуль заменён на exit 0."""
    env = dict(os.environ)
    env.pop("NODE", None)
    if node is not None:
        env["NODE"] = node
    if fake_delegate:
        # Подменяем python3 на тривиальный exit 0 — проверяем, что делегат ВЫЗВАН
        env["PATH"] = str(_FAKE_BIN) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["bash", str(_ENTRYPOINT)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )


# endregion HELPERS


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T16 F-016 · NODE=prod → fail-loud + hint, делегат НЕ вызван
# · Scenario: операторская машина, NODE=<prod> → healthcheck НЕ проверяет локальный docker:
#   exit 1 + подсказка (e2e-verify/ssh); Python-делегат не запущен (fake python3 не отработал)
# · Last fail: F-016 — make healthcheck NODE=<prod> молча проверял ЛОКАЛЬНЫЙ docker (ложный успех)
# · Remove if: healthcheck получит настоящий remote-mode (SSH-exec)
def test_node_param_requires_remote_mode() -> None:
    """F-016: NODE≠local → fail-loud с подсказкой, локальный прогон невозможен."""
    result = _run_entrypoint(node="prod-node", fake_delegate=True)
    assert result.returncode == 1, f"F-016 FAIL: NODE=prod должен дать exit 1, получен {result.returncode}"
    assert "e2e-verify" in (result.stderr or ""), f"нет подсказки e2e-verify: {result.stderr}"
    assert "NODE=prod-node задан" in (result.stderr or ""), f"нет упоминания NODE: {result.stderr}"
    logger.info("[IMP:9][test][F-016] NODE=prod → exit 1 + hint PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T16 F-016 · NODE=local → делегат исполняется
# · Scenario: явный NODE=local — локальная проверка стека сохраняется (dev-машина)
# · Remove if: healthcheck NODE-семантика изменится
def test_node_local_delegates(tmp_path: Path) -> None:
    """F-016: NODE=local → делегат (python3) вызывается."""
    result = _run_entrypoint(node="local", fake_delegate=True)
    assert result.returncode == 0, f"NODE=local должен делегировать, exit={result.returncode}"
    logger.info("[IMP:9][test][F-016] NODE=local → delegate PASS")


# 🧪 TRAP[TEST] · REGRESSION · plan 012 T16 F-016 · без NODE → делегат исполняется
# · Scenario: make healthcheck без NODE (прежнее поведение) → локальный прогон
# · Remove if: healthcheck NODE-семантика изменится
def test_no_node_delegates(tmp_path: Path) -> None:
    """F-016: без NODE → делегат вызывается (обратная совместимость)."""
    result = _run_entrypoint(node=None, fake_delegate=True)
    assert result.returncode == 0, f"без NODE должен делегировать, exit={result.returncode}"
    logger.info("[IMP:9][test][F-016] no NODE → delegate PASS")


# Fake bin dir: python3 → exit 0 (детекция факта вызова делегата)
_FAKE_BIN = Path(os.environ.get("TMPDIR", "/tmp")) / "kilo-healthcheck-fake-bin"


@pytest.fixture(scope="session", autouse=True)
def _fake_python_bin() -> None:
    """Create fake python3 (exit 0) in session temp — fake_delegate детектит вызов делегата."""
    _FAKE_BIN.mkdir(parents=True, exist_ok=True)
    fake = _FAKE_BIN / "python3"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
