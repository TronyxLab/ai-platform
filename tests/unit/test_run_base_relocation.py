# GREP_SUMMARY: run-base-relocation PLATFORM_RUN_BASE knob uniformity deploy-paths structural AI-0024
# STRUCTURE: ▶ env knob → ◇ python-резолверы (run_base/node_configs_remote) → ⎋ relocated │ ▶ source-scan shell/make потребителей → default деривирован от knob
# region MODULE_CONTRACT
## @purpose  AI-0024 (DevPlan 17 T4.1): установка PLATFORM_RUN_BASE перемещает ВСЕ run-артефакты
##           единообразно; NODE_CONFIGS_REMOTE_BASE override слышен healthcheck-фильтром и
##           secrets-glob. Структурные проверки: python-резолверы + shell/make потребители.
## @scope    tests/unit: deploy_paths-резолверы с env-DI + статический скан потребителей.
## @invariants
##   - run_base/secrets.env/status-metrics.json — все от одного knob'а
##   - node_configs_remote() уважает NODE_CONFIGS_REMOTE_BASE в обоих питон-сайтах
##   - Shell/make потребители деривируют дефолты от ${PLATFORM_RUN_BASE:-…} (не literal)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared import deploy_paths

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]

# Потребители knob'а: файл → маркер деривации дефолта от PLATFORM_RUN_BASE
_SHELL_CONSUMERS = [
    "core/internal/healthcheck/platform-export-metrics.sh",
    "core/internal/notify/notify-hook.sh",
    "core/templates/module.mk",
    "core/lib/secrets.sh",
]


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · единый relocation-knob (AI-0024)
# · Regression: PLATFORM_RUN_BASE игнорировался всеми shell/make потребителями —
#   установка канонического knob'а расщепляла state на два каталога
# · Scenario: env PLATFORM_RUN_BASE=/custom/run → run_base()/status_metrics_json()
#   указывают туда же; каждый shell/make потребитель содержит деривацию от knob'а;
#   ни один не держит голый literal /var/lib/platform/run в дефолте
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0024)
# · Remove if: shell-потребители переходят на `deploy_paths shell-exports` eval-механизм
def test_knob_relocates_all_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PLATFORM_RUN_BASE двигает все артефакты; shell-потребители деривируют дефолт."""
    custom = tmp_path / "custom-run"
    monkeypatch.setenv("PLATFORM_RUN_BASE", str(custom))

    rb = str(deploy_paths.run_base())
    assert rb == str(custom), f"run_base обязан уважать knob: {rb}"

    # производные артефакты внутри той же базы
    sm = deploy_paths.status_metrics_json()
    assert str(sm).startswith(str(custom)), f"status-metrics.json обязан жить под knob'ом: {sm}"
    se = deploy_paths.secrets_env_file()
    assert str(se).startswith(str(custom)), f"secrets.env обязан жить под knob'ом: {se}"

    # ── структурная проверка shell/make потребителей ──
    missing = []
    for rel in _SHELL_CONSUMERS:
        src = (_REPO / rel).read_text(encoding="utf-8")
        if "PLATFORM_RUN_BASE" not in src:
            missing.append(rel)
    assert not missing, f"потребители без консультации knob'а: {missing}"
    logger.critical("[IMP:9][test] single relocation knob across python+shell+make — OK (AI-0024)")


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · NODE_CONFIGS_REMOTE_BASE слышен обоими сайтами (AI-0025)
# · Regression: modules_healthcheck и decrypt_secrets резолвили /opt/node-configs локально,
#   мимо канонического резолвера — override расщеплял состояние
# · Scenario: env NODE_CONFIGS_REMOTE_BASE=/custom/nc → node_configs_remote() == /custom/nc;
#   оба сайта содержат вызов канона, а не literal /opt/node-configs
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0025)
# · Remove if: сайты переписаны на DI-инъекцию путей из вызывающего
def test_node_configs_override_heard(monkeypatch: pytest.MonkeyPatch) -> None:
    custom = "/custom/node-configs"
    monkeypatch.setenv("NODE_CONFIGS_REMOTE_BASE", custom)
    assert str(deploy_paths.node_configs_remote()) == custom

    mh_src = (_REPO / "core/internal/healthcheck/modules_healthcheck.py").read_text(encoding="utf-8")
    ds_src = (_REPO / "core/internal/secrets/decrypt_secrets.py").read_text(encoding="utf-8")
    for name, src in [("modules_healthcheck", mh_src), ("decrypt_secrets", ds_src)]:
        assert "node_configs_remote" in src, f"{name}: обязан резолвить через канон"
    logger.critical("[IMP:9][test] NODE_CONFIGS_REMOTE_BASE heard by both sites — OK (AI-0025)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · R5-негатив: без knob'а дефолт прежний (безопасная миграция)
# · Regression: унификация не должна менять поведение окружений БЕЗ переменных
# · Scenario: env пусты → run_base == /var/lib/platform/run; shell-дефолт строки сохранены
# · Last fail: контрсценарий-охранник T4.1 (DevPlan 17)
# · Remove if: вместе с test_knob_relocates_all_artifacts
def test_default_unchanged_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без env — прежние дефолты (обратная совместимость relocate-mechanism)."""
    monkeypatch.delenv("PLATFORM_RUN_BASE", raising=False)
    monkeypatch.delenv("NODE_CONFIGS_REMOTE_BASE", raising=False)
    assert str(deploy_paths.run_base()) == "/var/lib/platform/run"
    assert str(deploy_paths.node_configs_remote()) == "/opt/node-configs"
    exporter = (_REPO / _SHELL_CONSUMERS[0]).read_text(encoding="utf-8")
    assert "${PLATFORM_RUN_BASE:-/var/lib/platform/run}" in exporter, (
        "shell-дефолт обязан сохранять прежнее значение как fallback"
    )
