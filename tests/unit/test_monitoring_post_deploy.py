# GREP_SUMMARY: test-monitoring-post-deploy run-monitoring-reconfig post-deploy-chain DeployOrchestrator non-blocking render-steps skip-no-monitoring LDD
# STRUCTURE: fixtures(tmp_path YAML factory) → ◇ test_run_monitoring_reconfig_all_steps → ◇ test_skip_no_monitoring_section → ◇ test_render_step_failure_non_fatal → ◇ test_post_deploy_chain_calls_reconfig → ◇ test_post_deploy_chain_reconfig_failure_non_fatal → ⎋ LDD IMP:9 assert
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 138 W3 — автоматизация render-monitoring:
##           run_monitoring_reconfig (экстракция из main, паритет до-B8 module-hook)
##           + вызов из DeployOrchestrator._run_post_deploy_chain (non-blocking, WARN).
## @scope    Native imports, tmp_path only, 0 subprocess для бизнес-логики (правило testing.md),
##           render-шаги и reconfig-вызов мокаются (DI > mocks на внутреннее состояние).
## @invariants
##   - Контракт §4.3: build_merged_config None → return 0 (skip, IMP:8); шаги non-blocking
##     (ошибка → WARN, continue); порядок alert_rules → prometheus → grafana → loki →
##     reload → langfuse → catalog; возвращает 0 всегда (best-effort)
##   - Test Honesty R1-R5: negative-тесты (сбой render → non-fatal; сбой в chain → WARN),
##     0 pass-тестов, каждый тест — # 🧪 TRAP[TEST]
##   - LDD: assert ≥1 IMP:9-лог в успешном сценарии (Anti-Illusion rule)
## @rationale DevPlan 138 W3 §5 W3 / §4.3 — юнит-покрытие автоматизации render-monitoring
##           до полного прогона тестов (после всех волн).
## @changes
##   LAST_CHANGE: 2026-08-05 | Created (DevPlan 138 W3)
# endregion MODULE_CONTRACT

import logging
import pathlib
from unittest.mock import MagicMock

import yaml
from _conftest.ldd import _print_ldd_trajectory

import core.internal.deploy.orchestrator as orch
import core.internal.monitoring_config_renderer as mcr

logger = logging.getLogger(__name__)

# Порядок render-шагов (контракт DevPlan 138 §4.3): alert_rules → prometheus → grafana →
# loki → reload → langfuse → catalog.
_RENDER_STEPS: tuple[tuple[str, str], ...] = (
    ("generate_alert_rules", "alert_rules"),
    ("generate_prometheus_target", "prometheus"),
    ("generate_grafana_dashboard", "grafana"),
    ("update_loki_retention", "loki"),
    ("reload_monitoring_services", "reload"),
    ("create_langfuse_project", "langfuse"),
    ("refresh_catalog", "catalog"),
)


def _write_yaml(data: dict, path: pathlib.Path) -> pathlib.Path:
    """Write a YAML dict to a temp file and return the path.

    ## @purpose  Helper to create temporary YAML files for tests (tmp_path zero-hardcode).
    ## @complexity O(N) where N = YAML tree size
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


def _make_project(tmp_path: pathlib.Path, *, monitoring: bool) -> pathlib.Path:
    """Create a project dir with ai-platform.yaml (monitoring section optional).

    ## @purpose  Standard project fixture: backend type; monitoring-секция если monitoring=True.
    ## @complexity O(1)
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    data: dict = {"type": "backend", "name": "test-app", "needs": {"llm": True}}
    if monitoring:
        data["monitoring"] = {
            "metrics": True,
            "metrics_port": 9090,
            "dashboard": True,
            "alerting": True,
            "logs_retention": "14d",
        }
    _write_yaml(data, project_dir / "ai-platform.yaml")
    return project_dir


def _patch_render_steps(monkeypatch, mcr_module) -> dict[str, MagicMock]:
    """Patch all 7 render-step facades in monitoring_config_renderer with mocks.

    ## @purpose  DI: render-шаги заменяются MagicMock (возвращают RenderResult status="updated").
    ##            Возвращает {имя_атрибута: mock} для per-step assert.
    ## @complexity O(N) где N = len(_RENDER_STEPS)
    """
    mocks: dict[str, MagicMock] = {}
    for attr, component in _RENDER_STEPS:
        mock = MagicMock(return_value=mcr.RenderResult(component=component, status="updated"))
        mocks[attr] = mock
        monkeypatch.setattr(mcr_module, attr, mock)
    return mocks


# region FUNC_test_run_monitoring_reconfig_all_steps
## @purpose  Успешный сценарий: monitoring-конфиг → все 7 render-шагов вызваны, return 0,
##            IMP:9 START/DONE присутствуют (AC W3).
# 🧪 TRAP[TEST] · run_monitoring_reconfig_all_steps · Contract (§4.3) · Regression: рендер не исполняется
# · Scenario: ai-platform.yaml с monitoring-секцией → каждый render-шаг вызван ровно 1 раз, return 0
# · Last fail: N/A (новый тест, DevPlan 138 W3)
# · Remove if: контракт run_monitoring_reconfig меняется (шаги перестают вызываться на monitoring-конфиге)
def test_run_monitoring_reconfig_all_steps(tmp_path, monkeypatch, caplog) -> None:
    """run_monitoring_reconfig с monitoring-конфигом → все render-шаги вызваны (mock), return 0."""
    caplog.set_level(logging.INFO)
    project_dir = _make_project(tmp_path, monitoring=True)
    platform_root = tmp_path / "platform"

    mocks = _patch_render_steps(monkeypatch, mcr)

    result = mcr.run_monitoring_reconfig(project_dir, "test-app", "test-node", platform_root)

    assert result == 0, f"best-effort: ожидался return 0, got {result}"
    for mock in mocks.values():
        mock.assert_called_once()  # AssertionError с деталями вызова при нарушении

    found = _print_ldd_trajectory(caplog, "test_run_monitoring_reconfig_all_steps")
    assert found, "LDD: в успешном сценарии обязан быть IMP:9-лог (START/DONE)"
    logger.info("[IMP:9][test] run_monitoring_reconfig: все 7 render-шагов вызваны, return 0 ✓")


# endregion FUNC_test_run_monitoring_reconfig_all_steps


# region FUNC_test_skip_no_monitoring_section
## @purpose  Нет monitoring-секции → skip без рендера (AC W3), return 0, лог IMP:8.
# 🧪 TRAP[TEST] · skip_no_monitoring_section · Contract (§4.3) · Regression: рендер без monitoring-секции
# · Scenario: ai-platform.yaml БЕЗ monitoring → build_merged_config None → return 0, 0 render-вызовов, IMP:8 skip
# · Last fail: N/A (новый тест, DevPlan 138 W3)
# · Remove if: контракт «без секции → skip» меняется
def test_skip_no_monitoring_section(tmp_path, monkeypatch, caplog) -> None:
    """build_merged_config → None (нет monitoring-секции) → skip, return 0, лог IMP:8."""
    caplog.set_level(logging.INFO)
    project_dir = _make_project(tmp_path, monitoring=False)
    platform_root = tmp_path / "platform"

    mocks = _patch_render_steps(monkeypatch, mcr)

    result = mcr.run_monitoring_reconfig(project_dir, "test-app", "test-node", platform_root)

    assert result == 0, f"skip должен вернуть 0, got {result}"
    for mock in mocks.values():
        mock.assert_not_called()  # AssertionError с деталями вызова при нарушении

    skip_msgs = [r.message for r in caplog.records if "[IMP:8][hook] No monitoring config" in r.message]
    assert skip_msgs, "Ожидался IMP:8 skip-лог (No monitoring config)"

    _print_ldd_trajectory(caplog, "test_skip_no_monitoring_section")
    logger.info("[IMP:9][test] skip без monitoring-секции: return 0, рендер не исполнялся ✓")


# endregion FUNC_test_skip_no_monitoring_section


# region FUNC_test_render_step_failure_non_fatal
## @purpose  Сбой render-шага → WARN, return 0 (non-fatal), последующие шаги исполняются (R5).
# 🧪 TRAP[TEST] · render_step_failure_non_fatal · NEGATIVE (R5) · Regression: сбой рендера роняет reconfig
# · Scenario: generate_prometheus_target → RuntimeError → WARN (prometheus render WARN), остальные шаги
# ·   продолжаются, return 0, IMP:9 DONE присутствует
# · Last fail: N/A (новый negative-тест, DevPlan 138 W3; до экстракции main() пробрасывал исключение)
# · Remove if: non-blocking контракт шагов меняется (ошибка начинает блокировать/пробрасываться)
def test_render_step_failure_non_fatal(tmp_path, monkeypatch, caplog) -> None:
    """Сбой render-шага → лог WARN, return 0 (non-fatal), continue."""
    caplog.set_level(logging.INFO)
    project_dir = _make_project(tmp_path, monitoring=True)
    platform_root = tmp_path / "platform"

    mocks = _patch_render_steps(monkeypatch, mcr)
    mocks["generate_prometheus_target"].side_effect = RuntimeError("prometheus render exploded (test)")

    result = mcr.run_monitoring_reconfig(project_dir, "test-app", "test-node", platform_root)

    assert result == 0, f"сбой шага не должен менять return (best-effort), got {result}"
    warn_msgs = [
        r.message
        for r in caplog.records
        if r.levelno == logging.WARNING and "prometheus render WARN (non-fatal)" in r.message
    ]
    assert warn_msgs, "Ожидался WARN-лог о сбое render-шага (non-fatal)"
    # Continue: последующие шаги (grafana → loki → reload → langfuse → catalog) исполнены
    for attr in (
        "generate_grafana_dashboard",
        "update_loki_retention",
        "reload_monitoring_services",
        "create_langfuse_project",
        "refresh_catalog",
    ):
        mocks[attr].assert_called_once()  # continue после сбоя предшествующего шага
    done_msgs = [r.message for r in caplog.records if "monitoring on-project-deploy DONE" in r.message]
    assert done_msgs, "Ожидался IMP:9 DONE после сбоя шага (цепочка завершилась)"

    _print_ldd_trajectory(caplog, "test_render_step_failure_non_fatal")
    logger.info("[IMP:9][test] сбой render-шага: WARN + continue + return 0 ✓")


# endregion FUNC_test_render_step_failure_non_fatal


# region FUNC_test_post_deploy_chain_calls_reconfig
## @purpose  _run_post_deploy_chain вызывает reconfig с корректными аргументами (O3: node_name доступен).
# 🧪 TRAP[TEST] · post_deploy_chain_calls_reconfig · Contract (W3 задача 2) · Regression: вызов не подключён
# · Scenario: chain с project_dir+project+node_name → run_monitoring_reconfig вызван с
# ·   Path(project_dir)/project/node_name/Path(platform_root) (lazy-import mock, assert call)
# · Last fail: N/A (до W3 вызов отсутствовал — рендер висел ручным, паритет до-B8)
# · Remove if: вызов reconfig из post_deploy_chain удаляется
def test_post_deploy_chain_calls_reconfig(tmp_path, monkeypatch, caplog) -> None:
    """post_deploy_chain вызывает reconfig с корректными аргументами (mock, assert call)."""
    caplog.set_level(logging.INFO)
    project_dir = _make_project(tmp_path, monitoring=True)
    platform_root = tmp_path / "platform"

    # Изолировать chain от /opt/platform: subprocess (notify/generate-catalog) → no-op,
    # platform_remote_base → tmp_path (модульная привязка имени в orchestrator).
    monkeypatch.setattr("core.internal.deploy.orchestrator.subprocess.run", MagicMock())
    monkeypatch.setattr(orch, "platform_remote_base", lambda: platform_root)

    reconfig_mock = MagicMock(return_value=0)
    monkeypatch.setattr(mcr, "run_monitoring_reconfig", reconfig_mock)

    orch.DeployOrchestrator(projects_base=str(tmp_path))._run_post_deploy_chain(
        "test-app",
        "sha123",
        "DEPLOYED",
        project_dir=str(project_dir),
        node_name="test-node",
    )

    reconfig_mock.assert_called_once()
    args = reconfig_mock.call_args.args
    assert args[0] == pathlib.Path(project_dir), f"project_dir пробрасывается, got {args[0]!r}"
    assert args[1] == "test-app", f"project пробрасывается, got {args[1]!r}"
    assert args[2] == "test-node", f"node_name пробрасывается, got {args[2]!r}"
    assert args[3] == platform_root, f"platform_root пробрасывается, got {args[3]!r}"

    found = _print_ldd_trajectory(caplog, "test_post_deploy_chain_calls_reconfig")
    assert found, "LDD: chain должен логировать IMP:9 (notify-hook/generate-catalog)"
    logger.info(
        "[IMP:9][test] post_deploy_chain → run_monitoring_reconfig(project_dir, project, node_name, platform_root) ✓"
    )


# endregion FUNC_test_post_deploy_chain_calls_reconfig


# region FUNC_test_post_deploy_chain_reconfig_failure_non_fatal
## @purpose  Сбой reconfig в chain → WARN, деплой жив, цепочка продолжается (R5, AC W3).
# 🧪 TRAP[TEST] · post_deploy_chain_reconfig_failure_non_fatal · NEGATIVE (R5) · Regression: сбой reconfig роняет деплой
# · Scenario: run_monitoring_reconfig → RuntimeError → chain WARN (monitoring reconfig WARN non-fatal),
# ·   исключение НЕ пробрасывается, deploy-hooks продолжает (modules dir check IMP:7)
# · Last fail: N/A (новый negative-тест, DevPlan 138 W3; R5 — сбой рендера не роняет деплой)
# · Remove if: best-effort контракт chain меняется (сбой reconfig начинает фейлить receive)
def test_post_deploy_chain_reconfig_failure_non_fatal(tmp_path, monkeypatch, caplog) -> None:
    """Сбой reconfig в chain → лог WARN, исключение не пробрасывается, деплой жив."""
    caplog.set_level(logging.INFO)
    project_dir = _make_project(tmp_path, monitoring=True)
    platform_root = tmp_path / "platform"

    monkeypatch.setattr("core.internal.deploy.orchestrator.subprocess.run", MagicMock())
    monkeypatch.setattr(orch, "platform_remote_base", lambda: platform_root)

    reconfig_mock = MagicMock(side_effect=RuntimeError("renderer exploded in chain (test)"))
    monkeypatch.setattr(mcr, "run_monitoring_reconfig", reconfig_mock)

    # Исключение НЕ должно проброситься — chain best-effort (WARN, continue)
    orch.DeployOrchestrator(projects_base=str(tmp_path))._run_post_deploy_chain(
        "test-app",
        "sha123",
        "DEPLOYED",
        project_dir=str(project_dir),
        node_name="test-node",
    )

    warn_msgs = [
        r.message
        for r in caplog.records
        if r.levelno == logging.WARNING and "monitoring reconfig WARN (non-fatal)" in r.message
    ]
    assert warn_msgs, "Ожидался WARN-лог о сбое monitoring reconfig (non-fatal)"
    # Цепочка продолжилась: deploy-hooks достигнут (modules dir не найден → IMP:7)
    hooks_msgs = [r.message for r in caplog.records if "modules dir not found" in r.message]
    assert hooks_msgs, "Цепочка должна продолжиться к deploy-hooks после сбоя reconfig"

    _print_ldd_trajectory(caplog, "test_post_deploy_chain_reconfig_failure_non_fatal")
    logger.info("[IMP:9][test] сбой reconfig в chain: WARN non-fatal, деплой жив ✓")


# endregion FUNC_test_post_deploy_chain_reconfig_failure_non_fatal
