# GREP_SUMMARY: test-converge-guard, 017-E2, F-015-class, self-env, secrets.env, NGINX_OVERLAY_DIR, node_detect, converge-entrypoint, shell-facade, node-configs
# STRUCTURE: ▶ run converge.sh с fake /opt-лейаутом + SECRETS_ENV_FILE → ◇ валидная нода (node_detect → NODE_NAME export → NGINX_OVERLAY_DIR export → делегат видит оба) | ◇ нет ноды (auto-detection skipped → НЕ экспортируется → реальный remote_dispatch FATAL exit 1) → ⎋ assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for converge entrypoint self-env guard (017 E2 / класс F-015):
##           converge НА НОДЕ исполняется с чистым env — compose-introspection R9-fallback
##           (compose_defined_containers → build_compose_args c root-compose include nginx
##           ${NGINX_OVERLAY_DIR:?required}) падала «required variable NGINX_OVERLAY_DIR is
##           missing» → пустой конфиг → disabled-модуль не останавливался. Фасад самодостаточен:
##           source secrets.env + экспорт NGINX_OVERLAY_DIR через канон node_detect
##           (по образцу nginx_reload_hook.sh plan 012 T11 / healthcheck.sh 017 Phase E).
## @scope    Bash-facade core/entrypoints/converge.sh — исполняется через subprocess
##           (static-контракт реального фасада, паттерн test_healthcheck_guard.py).
## @invariants
##   - subprocess используется ТОЛЬКО для исполнения shell-фасада (легитимный статический
##     контракт — не бизнес-логика; прецедент test_healthcheck_guard.py)
##   - 017 E2: self-env тестируется РЕАЛЬНЫМ node_detect против fake /opt-лейаута
##     (env NODE_CONFIGS_REMOTE_BASE — канонический path-override deploy_paths); fake python3
##     делегирует `-m core.internal.shared.node_detect` реальному интерпретатору
##   - Валидная нода → NODE_NAME + NGINX_OVERLAY_DIR экспортируются и видны делегату (call-log)
##   - Нет ноды → auto-detection skipped, NGINX_OVERLAY_DIR НЕ экспортируется (фейковый путь
##     /opt/node-configs//overlays/nginx не создаётся); реальный remote_dispatch → exit 1 + FATAL
##     (ошибка ${NGINX_OVERLAY_DIR:?} остаётся явной downstream — «как сейчас»)
##   - Явно заданный NODE_NAME НЕ перезаписывается (node_detect не вызывается)
##   - Каждый тест — TRAP[TEST] с Regression/Scenario/Remove if
## @changes 2026-08-27 | 017 E2 (P1, класс F-015) — создан (паттерн test_healthcheck_guard.py)
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

_ENTRYPOINT: Path = Path(__file__).resolve().parent.parent.parent / "core" / "entrypoints" / "converge.sh"

pytestmark = pytest.mark.static_audit


# region HELPERS


def _run_entrypoint(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run converge.sh with fake python3 в PATH (self-env контракт, F-015-class).

    fake python3 (см. _fake_python_bin) делегирует `-m core.internal.shared.node_detect`
    РЕАЛЬНОМУ интерпретатору (сканирует fake /opt-лейаут из NODE_CONFIGS_REMOTE_BASE),
    а вызов делегата remote_dispatch.py — exit 0 (детекция вызова + env-снимок в call-log)
    ИЛИ реальный интерпретатор при FAKE_REAL_DELEGATE=1 (кейс «нет ноды» — реальная FATAL).
    """
    env = dict(os.environ)
    env.pop("NODE", None)
    env.pop("NODE_NAME", None)
    env.pop("NGINX_OVERLAY_DIR", None)
    env.pop("SECRETS_ENV_FILE", None)
    env.pop("NODE_CONFIGS_REMOTE_BASE", None)
    env.pop("CONVERGE_SENTINEL", None)
    if extra_env:
        env.update(extra_env)
    # Подменяем python3 на fake — проверяем, что self-env-детект выполнен РЕАЛЬНЫМ node_detect
    # (fake /opt-лейаут) + env-снимок (NODE_NAME/NGINX_OVERLAY_DIR/sentinel) на делегате.
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


def _print_ldd_trajectory(result: subprocess.CompletedProcess) -> None:
    """Печать [IMP:7-10]-траектории фасада из stderr (Anti-Illusion: видна реальная траектория)."""
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    if result.stderr:
        for line in result.stderr.splitlines():
            if "[IMP:" in line:
                print(line)
    print("--- END LDD TRAJECTORY ---")


# endregion HELPERS


# region TEST_SELF_ENV_VALID_NODE


# 🧪 TRAP[TEST] · REGRESSION · 017 E2 F-015-class · валидная нода → secrets.env sourced + NGINX_OVERLAY_DIR экспортирован
# · Scenario: fake /opt-лейаут NODE_CONFIGS_REMOTE_BASE=tmp/node-configs с РОВНО одним
#   tronyx-vps/node.yaml + SECRETS_ENV_FILE=tmp/secrets.env (sentinel) → node_detect (реальный)
#   отдаёт имя → converge.sh source-ит secrets.env и экспортирует NODE_NAME + NGINX_OVERLAY_DIR →
#   делегат (remote_dispatch.py) вызывается и ВИДИТ обе переменные + sentinel из secrets.env
# · Last fail: P1 017 E2 — converge НА НОДЕ с чистым env: R9-fallback compose-интроспекция
#   «required variable NGINX_OVERLAY_DIR is missing» → пустой конфиг → disabled-модуль не остановлен
# · Remove if: self-env перенесён в другой слой (Makefile/Python)
def test_self_env_exports_secrets_and_overlay_valid_node(tmp_path: Path) -> None:
    """017 E2: валидная нода → secrets.env sourced + NGINX_OVERLAY_DIR экспортирован делегату."""
    configs = tmp_path / "node-configs"
    (configs / "tronyx-vps").mkdir(parents=True)
    (configs / "tronyx-vps" / "node.yaml").write_text("name: tronyx-vps\n", encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    secrets.write_text("CONVERGE_SENTINEL=from-secrets.env\n", encoding="utf-8")
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        extra_env={
            "NODE_CONFIGS_REMOTE_BASE": str(configs),
            "SECRETS_ENV_FILE": str(secrets),
            "FAKE_CALL_LOG": str(call_log),
        }
    )
    _print_ldd_trajectory(result)
    assert result.returncode == 0, f"валидная нода: делегат должен завершиться exit 0, stderr={result.stderr}"
    assert "Auto-detected NODE_NAME=tronyx-vps" in (result.stderr or ""), f"нет авто-детекта: {result.stderr}"
    assert "NGINX_OVERLAY_DIR=/opt/node-configs/tronyx-vps/overlays/nginx" in (result.stderr or ""), (
        f"NGINX_OVERLAY_DIR не экспортирован: {result.stderr}"
    )
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" in log_text, "node_detect должен быть вызван (реальный канон-детектор)"
    assert "NGINX_OVERLAY_DIR=/opt/node-configs/tronyx-vps/overlays/nginx" in log_text, (
        "делегат должен видеть экспортированный NGINX_OVERLAY_DIR"
    )
    assert "NODE_NAME=tronyx-vps" in log_text, "делегат должен видеть экспортированный NODE_NAME"
    assert "CONVERGE_SENTINEL=from-secrets.env" in log_text, "делегат должен видеть sourced secrets.env (sentinel)"
    logger.info(
        "[IMP:9][test][self-env] валидная нода: secrets.env sourced + NODE_NAME/NGINX_OVERLAY_DIR экспортированы PASS"
    )


# endregion TEST_SELF_ENV_VALID_NODE


# region TEST_SELF_ENV_NO_NODE


# 🧪 TRAP[TEST] · REGRESSION · 017 E2 F-015-class · нет ноды → НЕ экспортируется фейковый путь, реальная FATAL «как сейчас»
# · Scenario: NODE_CONFIGS_REMOTE_BASE=пустая директория → node_detect exit 1 → NODE_NAME не задаётся →
#   NGINX_OVERLAY_DIR НЕ экспортируется (фейковый путь /opt/node-configs//overlays/nginx не создаётся) →
#   реальный remote_dispatch (FAKE_REAL_DELEGATE=1) auto-detect тоже фейлится → exit 1 + FATAL (прежнее поведение)
# · Last fail: P1 017 E2 (обратный сценарий — фейковый экспорт маскировал бы ошибку)
# · Remove if: self-env перенесён в другой слой
def test_self_env_no_node_no_fake_overlay_clear_error(tmp_path: Path) -> None:
    """017 E2: нет ноды → NGINX_OVERLAY_DIR НЕ экспортируется, реальная FATAL-ошибка (как сейчас)."""
    empty = tmp_path / "node-configs"
    empty.mkdir()
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        extra_env={
            "NODE_CONFIGS_REMOTE_BASE": str(empty),
            "FAKE_CALL_LOG": str(call_log),
            "FAKE_REAL_DELEGATE": "1",
        }
    )
    _print_ldd_trajectory(result)
    assert result.returncode == 1, f"нет ноды: реальный remote_dispatch должен дать exit 1, stderr={result.stderr}"
    assert "Node auto-detection skipped" in (result.stderr or ""), f"нет skip-сообщения self-env: {result.stderr}"
    assert "Auto-detected NODE_NAME=" not in (result.stderr or ""), (
        f"NODE_NAME не должен экспортироваться: {result.stderr}"
    )
    assert "FATAL: --node is required (auto-detect failed" in (result.stderr or ""), (
        f"реальный remote_dispatch должен дать явную FATAL (как сейчас): {result.stderr}"
    )
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" in log_text, "детекция должна быть ПОПРОБОВАНА (graceful skip)"
    assert "/opt/node-configs/" not in log_text, "фейковый путь NGINX_OVERLAY_DIR не должен создаваться"
    logger.info("[IMP:9][test][self-env] нет ноды: фейковый путь НЕ создан, реальная FATAL сохранена PASS")


# endregion TEST_SELF_ENV_NO_NODE


# region TEST_SELF_ENV_EXPLICIT_NODE_NAME


# 🧪 TRAP[TEST] · REGRESSION · 017 E2 F-015-class · явный NODE_NAME НЕ перезаписывается
# · Scenario: NODE_NAME=explicit-node задан оператором + есть единственный node-configs →
#   node_detect НЕ вызывается, NGINX_OVERLAY_DIR=/opt/node-configs/explicit-node/overlays/nginx
#   (уважение явного выбора — healthcheck.sh 017 Phase E симметричный прецедент)
# · Remove if: self-env перенесён в другой слой
def test_self_env_explicit_node_name_not_overridden(tmp_path: Path) -> None:
    """017 E2: явный NODE_NAME не перезаписывается авто-детектом, overlay строится от него."""
    configs = tmp_path / "node-configs"
    (configs / "tronyx-vps").mkdir(parents=True)
    (configs / "tronyx-vps" / "node.yaml").write_text("name: tronyx-vps\n", encoding="utf-8")
    call_log = tmp_path / "call-log"

    result = _run_entrypoint(
        extra_env={
            "NODE_CONFIGS_REMOTE_BASE": str(configs),
            "FAKE_CALL_LOG": str(call_log),
            "NODE_NAME": "explicit-node",
        }
    )
    _print_ldd_trajectory(result)
    assert result.returncode == 0, f"explicit NODE_NAME: делегат должен выполниться, stderr={result.stderr}"
    assert "Auto-detected NODE_NAME=" not in (result.stderr or ""), (
        f"NODE_NAME не должен перезаписываться: {result.stderr}"
    )
    log_text = call_log.read_text(encoding="utf-8")
    assert "core.internal.shared.node_detect" not in log_text, "при заданном NODE_NAME детекция не вызывается"
    assert "NGINX_OVERLAY_DIR=/opt/node-configs/explicit-node/overlays/nginx" in log_text, (
        "overlay должен строиться от явного NODE_NAME"
    )
    logger.info("[IMP:9][test][self-env] явный NODE_NAME сохранён, детекция не вызвана, overlay от explicit-node PASS")


# endregion TEST_SELF_ENV_EXPLICIT_NODE_NAME


# Fake bin dir: python3 → детекция факта вызова делегата + реальное делегирование node_detect
_FAKE_BIN = Path(os.environ.get("TMPDIR", "/tmp")) / "kilo-converge-fake-bin"


@pytest.fixture(scope="session", autouse=True)
def _fake_python_bin() -> None:
    """Create fake python3 in session temp.

    * Логирует каждый вызов (NODE_NAME + NGINX_OVERLAY_DIR + sentinel + argv) в $FAKE_CALL_LOG.
    * `-m core.internal.shared.node_detect` → РЕАЛЬНЫЙ python3 (fake /opt-лейаут из
      NODE_CONFIGS_REMOTE_BASE сканируется канон-детектором — интеграционный тест).
    * FAKE_REAL_DELEGATE=1 → остальные вызовы (remote_dispatch.py) делегируются реальному
      интерпретатору (кейс «нет ноды»: реальная FATAL-ошибка «как сейчас»).
    * Остальное → exit 0 (детекция факта вызова делегата remote_dispatch).
    """
    _FAKE_BIN.mkdir(parents=True, exist_ok=True)
    fake = _FAKE_BIN / "python3"
    fake.write_text(
        """#!/bin/sh
# Kilo test fake python3 — converge entrypoint self-env contract (017 E2 / F-015-class).
if [ -n "${FAKE_CALL_LOG:-}" ]; then
    printf 'NODE_NAME=%s NGINX_OVERLAY_DIR=%s CONVERGE_SENTINEL=%s CMD=%s\\n' \\
        "${NODE_NAME:-}" "${NGINX_OVERLAY_DIR:-}" "${CONVERGE_SENTINEL:-}" "$*" >> "$FAKE_CALL_LOG"
fi
if [ "$1" = "-m" ] && [ "$2" = "core.internal.shared.node_detect" ]; then
    _FAKE_DIR="${FAKE_BIN_DIR:-}"
    _REAL_PATH="${PATH}"
    if [ -n "$_FAKE_DIR" ]; then
        _REAL_PATH="${_REAL_PATH#$_FAKE_DIR:}"
    fi
    PATH="$_REAL_PATH" exec python3 "$@"
fi
if [ "${FAKE_REAL_DELEGATE:-0}" = "1" ]; then
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
