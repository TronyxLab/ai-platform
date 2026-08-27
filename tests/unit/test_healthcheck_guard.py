# GREP_SUMMARY: test-healthcheck-guard, F-016, NODE-guard, healthcheck-entrypoint, remote-hint, fail-loud, shell-facade, auto-detect, NODE_NAME, node_detect
# STRUCTURE: ▶ run healthcheck.sh with NODE env → ◇ NODE=prod → exit 1 + hint | ◇ NODE=local → delegate | ◇ no NODE → delegate | ◇ auto-detect NODE_NAME (node_detect: fake /opt layout → export | dev/ambiguous → skip) → ⎋ assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for healthcheck entrypoint NODE-guard (plan 012 T16 / F-016)
##           + авто-детект NODE_NAME на нодовой инсталляции (017 Phase E F-находка).
##           Операторская машина с NODE=<n> НЕ должна молча проверять локальный docker —
##           fail-loud с подсказкой (e2e-verify / ssh на ноду). Нода без env: NODE_NAME
##           авто-выводится из единственного каталога <name>/node.yaml в node-configs →
##           enabled-фильтр modules_healthcheck (иначе ложные FAIL по модулям вне node.yaml).
## @scope    Bash-facade core/entrypoints/healthcheck.sh — исполняется через subprocess
##           (static-контракт реального фасада, R5 check_tcp-паттерн test_healthcheck_lib).
## @invariants
##   - subprocess используется ТОЛЬКО для исполнения shell-фасада (легитимный статический
##     контракт — не бизнес-логика; см. test_healthcheck_lib.py прецедент)
##   - NODE=<n> (≠local) → exit 1 + hint в stderr, Python-делегат НЕ вызывается
##   - NODE=local / пустой → exit прогона делегата (Python-модуль вызван)
##   - 017 Phase E: авто-детект тестируется РЕАЛЬНЫМ node_detect против fake /opt-лейаута
##     (env NODE_CONFIGS_REMOTE_BASE — канонический path-override deploy_paths); fake python3
##     делегирует `-m core.internal.shared.node_detect` реальному интерпретатору, остальное — exit 0
##   - Явно заданный NODE_NAME НЕ перезаписывается (node_detect не вызывается)
##   - Каждый тест — TRAP[TEST] с Regression/Scenario/Remove if
## @changes 2026-08-26 · plan 012 T16 (F-016) — создан
## @changes 2026-08-27 · 017 Phase E (F-находка) — + авто-детект NODE_NAME (fake /opt layout,
##            реальный node_detect, skip на dev/ambiguous, explicit NODE_NAME не перезаписывается)
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


def _run_entrypoint(
    node: str | None,
    fake_delegate: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run healthcheck.sh with NODE env; fake_delegate → python3 в PATH подменён на fake.

    fake_delegate=True: fake python3 (см. _fake_python_bin) делегирует вызов
    `core.internal.shared.node_detect` РЕАЛЬНОМУ интерпретатору (сканирует fake /opt-лейаут
    из NODE_CONFIGS_REMOTE_BASE), а вызов делегата modules_healthcheck завершает exit 0.
    """
    env = dict(os.environ)
    env.pop("NODE", None)
    env.pop("NODE_NAME", None)
    env.pop("NODE_CONFIGS_REMOTE_BASE", None)
    if node is not None:
        env["NODE"] = node
    if extra_env:
        env.update(extra_env)
    if fake_delegate:
        # Подменяем python3 на fake — проверяем, что делегат ВЫЗВАН + эмулируем/делегируем
        # node_detect. FAKE_BIN_DIR — fake-скрипт вырезает его из PATH для реального python3.
        env["PATH"] = str(_FAKE_BIN) + os.pathsep + env.get("PATH", "")
        env["FAKE_BIN_DIR"] = str(_FAKE_BIN)
    return subprocess.run(
        ["bash", str(_ENTRYPOINT)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(_ENTRYPOINT.parent.parent.parent),
        check=False,
    )


# endregion HELPERS


# region TEST_F016_NODE_GUARD


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


# endregion TEST_F016_NODE_GUARD


# region TEST_AUTO_DETECT_NODE_NAME (017 Phase E)


# 🧪 TRAP[TEST] · REGRESSION · 017 Phase E F-находка · нода: единый node-configs → NODE_NAME авто-экспортируется
# · Scenario: fake /opt-лейаут NODE_CONFIGS_REMOTE_BASE=tmp/node-configs с РОВНО одним
#   tronyx-vps/node.yaml → node_detect (реальный) отдаёт имя → healthcheck экспортирует
#   NODE_NAME=tronyx-vps → делегат вызывается и ВИДИТ экспортированную переменную
# · Last fail: НАХОДКА 017 Phase E — make healthcheck на ноде без env перебирал ВСЕ модули
#   infra (включая log-collector вне node.yaml) → ложные FAIL «SOME MODULES UNHEALTHY»
# · Remove if: авто-детект NODE_NAME перенесён в другой слой (Makefile/Python)
def test_auto_detect_node_name_exported(tmp_path: Path) -> None:
    """017 Phase E: единый node-configs → NODE_NAME авто-детектится и доходит до делегата."""
    configs = tmp_path / "node-configs"
    (configs / "tronyx-vps").mkdir(parents=True)
    (configs / "tronyx-vps" / "node.yaml").write_text("name: tronyx-vps\n", encoding="utf-8")
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        node=None,
        fake_delegate=True,
        extra_env={"NODE_CONFIGS_REMOTE_BASE": str(configs), "FAKE_CALL_LOG": str(call_log)},
    )
    assert result.returncode == 0, f"нода: авто-детект должен завершиться делегированием, stderr={result.stderr}"
    assert "Auto-detected NODE_NAME=tronyx-vps" in (result.stderr or ""), f"нет авто-детекта: {result.stderr}"
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" in log_text, "node_detect должен быть вызван (реальный канон-детектор)"
    assert "NODE_NAME=tronyx-vps" in log_text, "делегат должен видеть экспортированный NODE_NAME"
    logger.info("[IMP:9][test][auto-detect] нода: NODE_NAME=tronyx-vps авто-экспортирован + передан делегату PASS")


# 🧪 TRAP[TEST] · REGRESSION · 017 Phase E F-находка · dev: нет /opt/node-configs → поведение не меняется
# · Scenario: NODE_CONFIGS_REMOTE_BASE указывает на пустую/несуществующую директорию →
#   node_detect exit 1 → NODE_NAME не задаётся → делегат проверяет ВСЕ модули (прежнее поведение)
# · Last fail: НАХОДКА 017 Phase E (обратный сценарий)
# · Remove if: авто-детект NODE_NAME перенесён в другой слой
def test_auto_detect_skipped_dev_no_configs(tmp_path: Path) -> None:
    """017 Phase E: dev (нет node-configs) → auto-detect skip, NODE_NAME не задаётся."""
    empty = tmp_path / "node-configs"
    empty.mkdir()
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        node=None,
        fake_delegate=True,
        extra_env={"NODE_CONFIGS_REMOTE_BASE": str(empty), "FAKE_CALL_LOG": str(call_log)},
    )
    assert result.returncode == 0, f"dev: делегат должен выполниться, stderr={result.stderr}"
    assert "auto-detection skipped" in (result.stderr or ""), f"нет skip-сообщения: {result.stderr}"
    assert "Auto-detected NODE_NAME=" not in (result.stderr or ""), (
        f"NODE_NAME не должен экспортироваться: {result.stderr}"
    )
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" in log_text, "детекция должна быть ПОПРОБОВАНА (graceful skip)"
    logger.info("[IMP:9][test][auto-detect] dev: нет node-configs → skip, все модули PASS")


# 🧪 TRAP[TEST] · REGRESSION · 017 Phase E F-находка · dev: >1 контекстов → поведение не меняется
# · Scenario: два валидных node.yaml → node_detect exit 1 (ambiguous) → NODE_NAME не задаётся
# · Last fail: НАХОДКА 017 Phase E (обратный сценарий)
# · Remove if: авто-детект NODE_NAME перенесён в другой слой
def test_auto_detect_skipped_dev_ambiguous(tmp_path: Path) -> None:
    """017 Phase E: dev (несколько контекстов) → auto-detect skip, NODE_NAME не задаётся."""
    configs = tmp_path / "node-configs"
    (configs / "node-a").mkdir(parents=True)
    (configs / "node-a" / "node.yaml").write_text("name: node-a\n", encoding="utf-8")
    (configs / "node-b").mkdir(parents=True)
    (configs / "node-b" / "node.yaml").write_text("name: node-b\n", encoding="utf-8")
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        node=None,
        fake_delegate=True,
        extra_env={"NODE_CONFIGS_REMOTE_BASE": str(configs), "FAKE_CALL_LOG": str(call_log)},
    )
    assert result.returncode == 0, f"dev/ambiguous: делегат должен выполниться, stderr={result.stderr}"
    assert "auto-detection skipped" in (result.stderr or ""), f"нет skip-сообщения: {result.stderr}"
    assert "Auto-detected NODE_NAME=" not in (result.stderr or ""), (
        f"NODE_NAME не должен экспортироваться: {result.stderr}"
    )
    logger.info("[IMP:9][test][auto-detect] dev: >1 контекстов → skip, все модули PASS")


# 🧪 TRAP[TEST] · REGRESSION · 017 Phase E F-находка · явный NODE_NAME НЕ перезаписывается
# · Scenario: NODE_NAME=explicit-node задан оператором + есть единственный node-configs →
#   node_detect НЕ вызывается, делегат видит explicit-node (уважение явного выбора)
# · Remove if: авто-детект NODE_NAME перенесён в другой слой
def test_explicit_node_name_not_overridden(tmp_path: Path) -> None:
    """017 Phase E: явный NODE_NAME не перезаписывается авто-детектом."""
    configs = tmp_path / "node-configs"
    (configs / "tronyx-vps").mkdir(parents=True)
    (configs / "tronyx-vps" / "node.yaml").write_text("name: tronyx-vps\n", encoding="utf-8")
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        node=None,
        fake_delegate=True,
        extra_env={
            "NODE_CONFIGS_REMOTE_BASE": str(configs),
            "FAKE_CALL_LOG": str(call_log),
            "NODE_NAME": "explicit-node",
        },
    )
    assert result.returncode == 0, f"explicit NODE_NAME: делегат должен выполниться, stderr={result.stderr}"
    assert "Auto-detected NODE_NAME=" not in (result.stderr or ""), (
        f"NODE_NAME не должен перезаписываться: {result.stderr}"
    )
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" not in log_text, "при заданном NODE_NAME детекция не вызывается"
    assert "NODE_NAME=explicit-node" in log_text, "делегат должен видеть явный NODE_NAME"
    logger.info("[IMP:9][test][auto-detect] explicit NODE_NAME сохранён, детекция не вызвана PASS")


# endregion TEST_AUTO_DETECT_NODE_NAME


# Fake bin dir: python3 → детекция факта вызова делегата + реальное делегирование node_detect
_FAKE_BIN = Path(os.environ.get("TMPDIR", "/tmp")) / "kilo-healthcheck-fake-bin"


@pytest.fixture(scope="session", autouse=True)
def _fake_python_bin() -> None:
    """Create fake python3 in session temp.

    * Логирует каждый вызов (NODE_NAME + argv) в $FAKE_CALL_LOG (per-test tmp_path).
    * `-m core.internal.shared.node_detect` → РЕАЛЬНЫЙ python3 (fake /opt-лейаут из
      NODE_CONFIGS_REMOTE_BASE сканируется канон-детектором — интеграционный тест).
    * Остальное → exit 0 (детекция факта вызова делегата modules_healthcheck).
    """
    _FAKE_BIN.mkdir(parents=True, exist_ok=True)
    fake = _FAKE_BIN / "python3"
    fake.write_text(
        """#!/bin/sh
# Kilo test fake python3 — healthcheck entrypoint contract (F-016 + 017 Phase E).
if [ -n "${FAKE_CALL_LOG:-}" ]; then
    printf 'NODE_NAME=%s CMD=%s\\n' "${NODE_NAME:-}" "$*" >> "$FAKE_CALL_LOG"
fi
if [ "$1" = "-m" ] && [ "$2" = "core.internal.shared.node_detect" ]; then
    _FAKE_DIR="${FAKE_BIN_DIR:-}"
    _REAL_PATH="${PATH}"
    if [ -n "$_FAKE_DIR" ]; then
        _REAL_PATH="${_REAL_PATH#$_FAKE_DIR:}"
    fi
    PATH="$_REAL_PATH" exec python3 "$@"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
