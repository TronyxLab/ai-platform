# GREP_SUMMARY: test-deploy-orchestrator, routing, severity, parallel, sequential, orchestrator-cli, public-observable, unit
# STRUCTURE: ▶ routing tests [orchestrate: seq|parallel|orchestrator → public observables] → ▶ severity tests [_aggregate_severity + _compute_exit_code: crit|warn|none] → ▶ orchestrate tests [empty-noop | parse | preflight | postflight] → ▶ deploy tests [parallel topo | sequential loop — через orchestrate()] → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy_orchestrator.py (DevPlan 100 TASK-4a) — routing decision,
##           severity aggregation, preflight/postflight wiring, parallel/sequential deploy paths.
##           All tests use native imports + unittest.mock.patch — NO subprocess, NO Docker.
## @scope    Covers $TEST_SPEC §9 entries for tests/unit/test_deploy_orchestrator.py (12 scenarios):
##           sequential/parallel/orchestrator routing, critical/warn/no-failure exit codes,
##           empty-modules noop, node.yaml parsing, preflight/postflight call wiring,
##           parallel topo_sort invocation, sequential module iteration.
##           T8.2 (DevPlan 016 TASK-6): routing/deploy tests drive the PUBLIC orchestrate() API
##           and assert on ModuleDeployResult observables (deployed/failed/exit_code) + public
##           docker-worker seams — patches of _deploy_sequential/_deploy_parallel removed.
## @invariants
##   - Native imports only — no subprocess.run for business logic (RULES §TESTING)
##   - tmp_path fixture for node.yaml/module.yaml fixtures (Zero Hardcode Rule)
##   - unittest.mock.patch for external module functions (no Docker, no /etc writes)
##   - LDD caplog: set_level(logging.DEBUG) + ≥1 [IMP:9] log asserted per test (Anti-Illusion)
##   - Test Honesty R1: every test has real asserts (no pass-tests)
##   - Test Honesty R2: asserts verify meaningful routing/severity properties
## @rationale DevPlan 100 D1: orchestrator is import-native → unit-testable via mock, no fork.
##           Routing + severity are pure logic — direct function calls with mocked deps.
## @changes   2026-07-31 · Created (DevPlan 100 TASK-4a)
## @usecases
##   - CI unit run: pytest tests/unit/test_deploy_orchestrator.py -v (no Docker required)
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path
from unittest import mock

import pytest
import yaml

from core.internal.bootstrap.deploy import deploy_orchestrator as orch
from tests.helpers.gate_helpers import assert_ldd_imp9

logger = logging.getLogger(__name__)


# region HELPERS
## @purpose  Shared fixtures: node.yaml / module.yaml writers + LDD trajectory assert
## @complexity 1 — file writes + caplog scan


def _write_node_yaml(tmp_path: Path, modules: dict, context: str = "") -> Path:
    """Write a node.yaml with a modules dict (name → {enabled, config_overlay})."""
    data: dict = {}
    if context:
        data["context"] = context
    data["modules"] = modules
    node_yaml = tmp_path / "node.yaml"
    # sort_keys=False: preserve module declaration order (dict iteration order in parse)
    node_yaml.write_text(yaml.safe_dump(data, sort_keys=False))
    return node_yaml


def _write_module_yaml(
    tmp_path: Path,
    name: str,
    install_type: str = "docker",
    severity: str = "warn",
    depends_on: list[str] | None = None,
) -> Path:
    """Write modules/<name>/module.yaml with install_type + severity (+ optional depends_on)."""
    module_dir = tmp_path / "modules" / name
    module_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = module_dir / "module.yaml"
    data: dict = {"name": name, "install_type": install_type, "severity": severity}
    if depends_on is not None:
        data["depends_on"] = depends_on
    yaml_path.write_text(yaml.safe_dump(data))
    return yaml_path


# T2.16a: _assert_ldd_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# endregion HELPERS


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING (§9: DEPLOY_PARALLEL / DEPLOY_ORCHESTRATOR маршруты — через публичный orchestrate())
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_orchestrate_sequential_routing
## @purpose  deploy_parallel=False → orchestrate() routes to the sequential deploy path: modules
##           deployed via docker_orchestrator.deploy_docker_module (public worker seam), the
##           parallel worker deploy_docker_group is NOT used; result observables honest.
## @io       tmp_path, caplog → None (pytest.fail if wrong route taken)
## @complexity 2 — real orchestrate() + real sequential loop + mocked external I/O phases
## @invariants
##   - Sequential route must NOT call deploy_docker_group (parallel worker)
##   - result.deployed counts every deployed module; result.failed stays empty


@pytest.mark.smoke
def test_orchestrate_sequential_routing(tmp_path, caplog) -> None:
    """
    # ▶ node.yaml (postgres,redis) → ⚡ orchestrate(deploy_parallel=False) → ◇ assert seq worker used, parallel NOT → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    logger.info("[IMP:7][test_orchestrate_sequential_routing] START — sequential routing check")

    # T8.2: приватные воркеры маршрута (_deploy_sequential/_deploy_parallel) НЕ патчатся —
    # маршрут тестируется через ПУБЛИЧНЫЙ orchestrate() + публичные observable результата
    # (result.deployed/failed/exit_code) + публичные seam'ы docker-воркеров.
    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_seq_deploy,
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_group", return_value=(2, 0, [], [])) as mock_group,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=False,
        )

    # AI-0047 (DevPlan 17 T8.2): маршрут выбран через наблюдаемые исходы — sequential-воркер
    # получил те же модули, параллельный воркер не вызван, результат публично честен
    assert mock_seq_deploy.call_count == 2, (
        f"sequential worker must deploy both modules, got {mock_seq_deploy.call_count}"
    )
    deployed_names = {call.args[0] for call in mock_seq_deploy.call_args_list}
    assert deployed_names == {"postgres", "redis"}, f"sequential route обязан получить те же модули: {deployed_names}"
    mock_group.assert_not_called()
    assert result.deployed == 2, f"Sequential route should report deployed=2, got {result.deployed}"
    assert result.failed == [], f"Sequential route should report no failures, got {result.failed}"
    assert result.exit_code == 0, f"Clean sequential deploy must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_orchestrate_sequential_routing] SEQUENTIAL route dispatched correctly (deployed=%d)",
        result.deployed,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_PARALLEL=false must dispatch to sequential path (best-effort)
# · Scenario: orchestrate(deploy_parallel=False) → deploy_docker_module per module, deploy_docker_group NOT called
# · Last fail: N/A
# · Remove if: routing decision moves out of orchestrate()
# endregion FUNC_test_orchestrate_sequential_routing


# region FUNC_test_orchestrate_parallel_routing
## @purpose  deploy_parallel=True, deploy_orchestrator=False → orchestrate() routes to the parallel
##           deploy path: topo groups deployed via docker_orchestrator.deploy_docker_group (public
##           worker seam), the sequential worker deploy_docker_module is NOT used.
## @io       tmp_path, caplog → None (pytest.fail if wrong route taken)
## @complexity 2 — real orchestrate() + real topo on tmp module.yamls + mocked group deploy
## @invariants
##   - Parallel route must NOT call deploy_docker_module (sequential worker)
##   - deploy_orchestrator=False → group-based deploy path (not deploy-many)
##   - result.deployed/failed mirror the group worker outcome (public observables)


@pytest.mark.smoke
def test_orchestrate_parallel_routing(tmp_path, caplog) -> None:
    """
    # ▶ node.yaml (postgres,redis) → ⚡ orchestrate(deploy_parallel=True) → ◇ assert group worker used, seq NOT → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    logger.info("[IMP:7][test_orchestrate_parallel_routing] START — parallel routing check")

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "pre_pull_images", return_value=(2, 0)),
        mock.patch.object(orch.secrets_validator, "batch_check_env", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_group", return_value=(2, 0, [], [])) as mock_group,
        mock.patch.object(orch, "_deploy_system_modules", return_value=(0, [])),
        mock.patch.object(orch, "_set_hc_marker"),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_seq_deploy,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=True,
            deploy_orchestrator=False,
        )

    # AI-0047 (T8.2): observable forwarding — флаг доезжает до группового воркера
    mock_group.assert_called_once()
    group_entries = mock_group.call_args.args[0]
    assert set(group_entries) == {"postgres", "redis"}, f"parallel route modules: {group_entries}"
    mock_seq_deploy.assert_not_called()
    assert result.deployed == 2, f"Parallel route should report deployed=2, got {result.deployed}"
    assert result.failed == [], f"Parallel route should report no failures, got {result.failed}"
    assert result.exit_code == 0, f"Clean parallel deploy must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_orchestrate_parallel_routing] PARALLEL route dispatched correctly (deployed=%d)",
        result.deployed,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_PARALLEL=true must dispatch to parallel path (DevPlan 050)
# · Scenario: orchestrate(deploy_parallel=True, deploy_orchestrator=False) → deploy_docker_group, seq worker NOT called
# · Last fail: N/A
# · Remove if: routing decision moves out of orchestrate()
# endregion FUNC_test_orchestrate_parallel_routing


# region FUNC_test_deploy_uses_public_observable
## @purpose  deploy_parallel=True, deploy_orchestrator=True → orchestrate() routes to the
##           DeployOrchestrator CLI path (deploy-many): the docker module list reaches the
##           deploy-many seam, group-based deploy and sequential worker are NOT used.
##           Asserts on PUBLIC observables: result.deployed/result.failed/result.exit_code
##           and public worker call patterns (T8.2 — no _deploy_sequential/_deploy_parallel patches).
## @io       tmp_path, caplog → None (pytest.fail if deploy-many path not taken)
## @complexity 2 — real orchestrate() + real topo + mocked deploy-many subprocess seam
## @invariants
##   - Orchestrator flag must route to deploy-many (replaces group deploy — either/or)
##   - Only DOCKER module names forwarded to deploy-many (R4)
##   - result.deployed/failed mirror the deploy-many result (public observables)


@pytest.mark.smoke
def test_deploy_uses_public_observable(tmp_path, caplog) -> None:
    """
    # ▶ node.yaml (postgres,redis) → ⚡ orchestrate(deploy_parallel=True, deploy_orchestrator=True)
    # → ◇ assert deploy-many gets the docker module list; group+seq workers NOT used → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    logger.info("[IMP:7][test_deploy_uses_public_observable] START — DeployOrchestrator routing check")

    # T8.2: маршрут тестируется через ПУБЛИЧНЫЙ orchestrate() + публичные observable результата.
    # _deploy_orchestrator — subprocess-шов к внешнему CLI (orchestrator_cli deploy-many):
    # единственная точка, где реальный subprocess заменён моком (DI-seam run_cmd недоступен
    # через orchestrate()) — это НЕ пиннинг приватной декомпозиции маршрута.
    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "pre_pull_images", return_value=(2, 0)),
        mock.patch.object(orch.secrets_validator, "batch_check_env", return_value=[]),
        mock.patch.object(orch, "_deploy_orchestrator", return_value=(2, [])) as mock_deploy_many,
        mock.patch.object(orch, "_deploy_system_modules", return_value=(0, [])),
        mock.patch.object(orch, "_set_hc_marker"),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_group", return_value=(2, 0, [], [])) as mock_group,
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_seq_deploy,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=True,
            deploy_orchestrator=True,
        )

    # DeployOrchestrator (deploy-many) получил список docker-модулей — observable маршрута
    mock_deploy_many.assert_called_once()
    passed_modules = mock_deploy_many.call_args.args[0]
    assert list(passed_modules) == ["postgres", "redis"], f"deploy-many должен получить docker-модули: {passed_modules}"
    mock_group.assert_not_called()
    mock_seq_deploy.assert_not_called()
    assert result.deployed == 2, f"Orchestrator route must report deployed=2, got {result.deployed}"
    assert result.failed == [], f"Orchestrator route must report no failures, got {result.failed}"
    assert result.exit_code == 0, f"Clean orchestrator deploy must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_deploy_uses_public_observable] DEPLOY_ORCHESTRATOR=true → deploy-many (deployed=%d failed=%s)",
        result.deployed,
        result.failed,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_ORCHESTRATOR=true must route to DeployOrchestrator deploy-many
# · Scenario: orchestrate(deploy_parallel=True, deploy_orchestrator=True) → deploy-many gets
# ·   ["postgres","redis"]; group-based deploy + sequential worker NOT called; deployed=2 failed=[]
# · Last fail: N/A
# · Remove if: DEPLOY_ORCHESTRATOR routing moves out of orchestrate()
# endregion FUNC_test_deploy_uses_public_observable


# ══════════════════════════════════════════════════════════════════════════════
# SEVERITY (§9: _aggregate_severity + _compute_exit_code)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_severity_critical_modules_exit_2
## @purpose  Failed module with severity=critical → crit_count=1 → exit code 2
## @io       tmp_path, caplog → None
## @complexity 1 — direct calls with enriched modules_info (no fallback)
## @invariants
##   - severity from modules_info dict (topo_sort enriched output) wins over fallback
##   - CRIT>0 must map to exit 2


def test_severity_critical_modules_exit_2(tmp_path, caplog) -> None:
    """
    # ▶ modules_info[critical_mod]={severity: critical} → ⚡ _aggregate_severity → ⚡ _compute_exit_code → ◇ assert 2 → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_severity_critical_modules_exit_2] START — critical severity aggregation")

    modules_info = {"critical_mod": {"install_type": "docker", "severity": "critical"}}
    crit, warn = orch._aggregate_severity(["critical_mod"], modules_info, str(tmp_path))
    exit_code = orch._compute_exit_code(crit, warn, deployed=1)

    assert crit == 1, f"Expected 1 critical failure, got {crit}"
    assert warn == 0, f"Expected 0 warnings, got {warn}"
    assert exit_code == 2, f"Critical failure must exit 2, got {exit_code}"
    logger.info(
        "[IMP:9][test_severity_critical_modules_exit_2] critical module → crit=%d warn=%d exit=%d",
        crit,
        warn,
        exit_code,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: critical module failure must escalate to exit 2 (severity contract)
# · Scenario: _aggregate_severity(["critical_mod"], enriched dict) → (1, 0); _compute_exit_code(1, 0, 1) == 2
# · Last fail: N/A
# · Remove if: severity-based exit contract changes
# endregion FUNC_test_severity_critical_modules_exit_2


# region FUNC_test_severity_warn_modules_exit_0
## @purpose  Failed module with severity=warn → warn_count=1 → exit code 0 (warnings are non-critical)
## @io       tmp_path, caplog → None
## @complexity 1 — direct calls
## @invariants
##   - WARN>0 must map to exit 0 with WARN log (best-effort — warnings never fail deploy)


def test_severity_warn_modules_exit_0(tmp_path, caplog) -> None:
    """
    # ▶ modules_info[warn_mod]={severity: warn} → ⚡ _aggregate_severity → ⚡ _compute_exit_code → ◇ assert 0 → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_severity_warn_modules_exit_0] START — warn severity aggregation")

    modules_info = {"warn_mod": {"install_type": "docker", "severity": "warn"}}
    crit, warn = orch._aggregate_severity(["warn_mod"], modules_info, str(tmp_path))
    exit_code = orch._compute_exit_code(crit, warn, deployed=1)

    assert crit == 0, f"Expected 0 critical failures, got {crit}"
    assert warn == 1, f"Expected 1 warning, got {warn}"
    assert exit_code == 0, f"Warning failure must exit 0 (non-critical), got {exit_code}"
    logger.info(
        "[IMP:9][test_severity_warn_modules_exit_0] warn module → crit=%d warn=%d exit=%d",
        crit,
        warn,
        exit_code,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: warn module failure must NOT escalate (exit 0, contract)
# · Scenario: _aggregate_severity(["warn_mod"], enriched dict) → (0, 1); _compute_exit_code(0, 1, 1) == 0
# · Last fail: N/A
# · Remove if: severity-based exit contract changes
# endregion FUNC_test_severity_warn_modules_exit_0


# region FUNC_test_severity_no_failures_exit_0
## @purpose  No failed modules → (0, 0) → exit 0
## @io       tmp_path, caplog → None
## @complexity 1 — direct calls
## @invariants
##   - Empty failure list must produce zero crit/warn counts
##   - Successful deploy must exit 0


def test_severity_no_failures_exit_0(tmp_path, caplog) -> None:
    """
    # ▶ failed=[] → ⚡ _aggregate_severity → ⚡ _compute_exit_code → ◇ assert 0 → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_severity_no_failures_exit_0] START — no-failure severity")

    crit, warn = orch._aggregate_severity([], {}, str(tmp_path))
    exit_code = orch._compute_exit_code(crit, warn, deployed=5)

    assert crit == 0, f"Expected 0 critical, got {crit}"
    assert warn == 0, f"Expected 0 warnings, got {warn}"
    assert exit_code == 0, f"Successful deploy must exit 0, got {exit_code}"
    logger.info("[IMP:9][test_severity_no_failures_exit_0] no failures → crit=0 warn=0 exit=%d", exit_code)

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: clean deploy must exit 0 (happy path)
# · Scenario: _aggregate_severity([], {}) → (0, 0); _compute_exit_code(0, 0, 5) == 0
# · Last fail: N/A
# · Remove if: severity-based exit contract changes
# endregion FUNC_test_severity_no_failures_exit_0


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATE INTEGRATION (§9: empty noop, parse, preflight, postflight)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_empty_modules_noop
## @purpose  node.yaml with no enabled modules → orchestrate() early-returns DeployResult(0, [], 0, 0, 0)
## @io       tmp_path, caplog → None
## @complexity 2 — orchestrate with mocked preflight/postflight + real parse
## @invariants
##   - No enabled modules → exit_code 0, deployed 0, failed []
##   - _postflight must NOT run for empty module set (early return)


def test_empty_modules_noop(tmp_path, caplog) -> None:
    """
    # ▶ node.yaml (modules: {}) → ⚡ orchestrate() with mocked preflight/postflight → ◇ assert early return → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    node_yaml = _write_node_yaml(tmp_path, {})
    logger.info("[IMP:7][test_empty_modules_noop] START — empty modules noop")

    with (
        mock.patch.object(orch, "_preflight", return_value=None) as mock_pre,
        mock.patch.object(orch, "_postflight", return_value=None) as mock_post,
    ):
        result = orch.orchestrate(
            str(node_yaml), str(tmp_path / "modules"), str(tmp_path / "core"), str(tmp_path / "templates")
        )

    mock_pre.assert_called_once()
    mock_post.assert_not_called()
    assert result.deployed == 0, f"Expected deployed=0, got {result.deployed}"
    assert result.failed == [], f"Expected no failures, got {result.failed}"
    assert result.crit_count == 0 and result.warn_count == 0
    assert result.exit_code == 0, f"Empty module set must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_empty_modules_noop] empty modules → deployed=%d exit=%d", result.deployed, result.exit_code
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: no enabled modules must not crash or postflight (`exit 0` parity)
# · Scenario: node.yaml with empty modules dict → orchestrate → DeployResult(0, [], 0, 0, 0), postflight skipped
# · Last fail: N/A
# · Remove if: empty-module semantics change
# endregion FUNC_test_empty_modules_noop


# region FUNC_test_parse_modules_from_node_yaml
## @purpose  Valid node.yaml (dict format) → _parse_modules returns correct enabled/all lists + filter
## @io       tmp_path, caplog → None
## @complexity 2 — real parse via secrets_validator.parse_modules_from_node_yaml
## @invariants
##   - enabled=true modules only in enabled_names; all declared in all_names
##   - modules_filter intersects enabled set (postgres,redis filter drops nginx)
##   - overlays empty (no context in node.yaml)


def test_parse_modules_from_node_yaml(tmp_path, caplog) -> None:
    """
    # ▶ node.yaml (postgres=true, redis=false, nginx=true) → ⚡ _parse_modules(filter=postgres) → ◇ assert lists → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    node_yaml = _write_node_yaml(
        tmp_path,
        {
            "postgres": {"enabled": True},
            "redis": {"enabled": False},
            "nginx": {"enabled": True},
        },
    )
    logger.info("[IMP:7][test_parse_modules_from_node_yaml] START — module parsing")

    modules = orch._parse_modules(str(node_yaml), str(tmp_path / "modules"), modules_filter="postgres")

    assert modules.all_names == ["postgres", "redis", "nginx"], f"all_names mismatch: {modules.all_names}"
    assert modules.enabled_names == ["postgres"], f"filter+enabled intersection mismatch: {modules.enabled_names}"
    assert modules.overlays == {"postgres": ""}, f"overlays mismatch: {modules.overlays}"
    logger.info(
        "[IMP:9][test_parse_modules_from_node_yaml] parsed enabled=%s all=%s",
        modules.enabled_names,
        modules.all_names,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: parse must filter enabled + apply modules_filter (AGENTS.md --modules contract)
# · Scenario: 3 modules (2 enabled, 1 disabled) + filter=postgres → enabled=[postgres]
# · Last fail: N/A
# · Remove if: node.yaml module parsing moves out of secrets_validator
# endregion FUNC_test_parse_modules_from_node_yaml


# region FUNC_test_preflight_calls_all_steps
## @purpose  _preflight contract: context_overlay + spool + charset валидация + status-metrics.json
##           W5 T5.2 (wiring→behavior): _create_status_metrics_json РЕАЛЬНО исполняется — ассерт
##           на созданный файл-артефакт (state/metrics), не на вызов private-метода. Мокаются
##           только внешние I/O шаги (git clone / FS-verify / secrets-validate) — их эффект в
##           unit-тесте не наблюдаем.
## @io       tmp_path, caplog → None
## @complexity 1 — mocked external sub-steps + real metrics-file effect
## @invariants
##   - context_overlay/spool/charset вызываются с ожидаемыми аргументами
##   - status-metrics.json СОЗДАЁТСЯ реально (перенаправление _STATUS_METRICS_PATH в tmp_path) —
##     файл существует, schema_version == 2 (orchestrator_metrics template)


def test_preflight_calls_all_steps(tmp_path, caplog) -> None:
    """
    # ▶ mock 3 external I/O шагов → ⚡ _preflight(tmp core/node/modules) → ◇ metrics-file создан → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_preflight_calls_all_steps] START — preflight wiring")

    # W5 T5.2: status-metrics.json пишется по-настоящему — redirect модульной константы в tmp_path
    metrics_path = tmp_path / "run" / "status-metrics.json"
    with (
        mock.patch.object(orch.context_overlay, "ensure_context_repo", return_value=0) as mock_ctx,
        mock.patch.object(
            orch.spool_validator, "verify_spool_dirs", return_value={"status": "ok", "missing": []}
        ) as mock_spool,
        mock.patch.object(orch.secrets_validator, "validate_secret_charsets", return_value=(0, [])) as mock_charsets,
        mock.patch.object(orch, "_STATUS_METRICS_PATH", str(metrics_path)),
    ):
        orch._preflight(str(tmp_path / "core"), str(tmp_path / "node.yaml"), str(tmp_path / "modules"))

    mock_ctx.assert_called_once_with(str(tmp_path / "node.yaml"))
    mock_spool.assert_called_once_with(str(tmp_path / "modules"))
    mock_charsets.assert_called_once_with(str(tmp_path / "core" / "secrets-manifest.yaml"))

    # ── Наблюдаемый эффект: status-metrics.json создан реально (не mock-вызов) ──
    assert metrics_path.is_file(), f"status-metrics.json artifact missing: {metrics_path}"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics.get("schema_version") == 2, f"status-metrics schema_version mismatch: {metrics}"
    assert "generated_at" in metrics and "containers" in metrics, (
        f"status-metrics template keys missing: {list(metrics)}"
    )
    logger.info("[IMP:9][test_preflight_calls_all_steps] preflight steps + status-metrics.json file artifact ✓")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: preflight must keep step order + non-fatal semantics
# · Scenario: _preflight с mocked external шагами → каждый вызван once + status-metrics.json создан реально
# · Last fail: N/A
# · Remove if: preflight composition changes
# endregion FUNC_test_preflight_calls_all_steps


# region FUNC_test_postflight_calls_all_steps
## @purpose  _postflight must invoke sudoers batch, orphan detection, litellm config render
## @io       tmp_path, caplog → None
## @complexity 1 — mocked sub-step wiring assert
## @invariants
##   - sudoers gets ALL module names (--module-names parity)
##   - orphans get ENABLED module names (--module-entries parity)
##   - litellm render called once


def test_postflight_calls_all_steps(tmp_path, caplog) -> None:
    """
    # ▶ mock 3 sub-steps → ⚡ _postflight(all, enabled, dirs) → ◇ assert each called → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_postflight_calls_all_steps] START — postflight wiring")

    with (
        mock.patch.object(orch.sudoers_generator, "batch_generate_sudoers", return_value=True) as mock_sudoers,
        mock.patch.object(orch.orphan_reconciler, "batch_orphan_reconciliation", return_value=[]) as mock_orphans,
        mock.patch.object(orch.orphan_reconciler, "remove_orphans", return_value=0) as mock_remove,
        mock.patch.object(orch, "_render_litellm_config") as mock_litellm,
    ):
        orch._postflight(
            ["a", "b"],
            ["a"],
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
        )

    mock_sudoers.assert_called_once_with(
        ["a", "b"], Path(str(tmp_path / "modules")), Path(str(tmp_path / "templates")), str(tmp_path)
    )
    mock_orphans.assert_called_once_with(["a"], str(tmp_path / "modules"))
    # DevPlan 140 W5 (S2-A): self-heal — remove_orphans вызывается с результатом детекта
    # даже при пустом списке (remove_orphans сам логирует «No orphans to remove» и возвращает 0)
    mock_remove.assert_called_once_with([])
    mock_litellm.assert_called_once_with(str(tmp_path / "core"))
    logger.info("[IMP:9][test_postflight_calls_all_steps] all 4 postflight steps wired correctly")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: postflight must run regardless of deploy outcome (best-effort)
# · Scenario: _postflight(all_names, enabled_names, dirs) → sudoers(all), orphans(enabled)+remove, litellm(core_dir)
# · Last fail: N/A
# · Remove if: postflight composition changes
# endregion FUNC_test_postflight_calls_all_steps


# region FUNC_test_postflight_selfheal_removes_orphans
## @purpose  DevPlan 140 W5 (W9-T9.15, S2-A): _postflight вызывает remove_orphans(orphans) —
##           self-heal orphan-контейнеров при orphan>0 (detect-only gap закрыт).
## @io       tmp_path, caplog → None (assert remove called with detected orphans)
## @complexity 1 — mocked sub-step wiring assert


def test_postflight_selfheal_removes_orphans(tmp_path, caplog) -> None:
    """_postflight self-heal: remove_orphans invoked with the detected orphans (orphan>0)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_postflight_selfheal_removes_orphans] START — orphan>0 self-heal")

    detected_orphans = [
        {"container_name": "orphan-pg", "project": "old-project"},
        {"container_name": "orphan-redis", "project": ""},
    ]
    with (
        mock.patch.object(orch.sudoers_generator, "batch_generate_sudoers", return_value=True) as mock_sudoers,
        mock.patch.object(
            orch.orphan_reconciler, "batch_orphan_reconciliation", return_value=detected_orphans
        ) as mock_orphans,
        mock.patch.object(orch.orphan_reconciler, "remove_orphans", return_value=2) as mock_remove,
        mock.patch.object(orch, "_render_litellm_config") as mock_litellm,
    ):
        orch._postflight(
            ["a", "b"],
            ["a"],
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
        )

    mock_orphans.assert_called_once_with(["a"], str(tmp_path / "modules"))
    mock_remove.assert_called_once_with(detected_orphans)
    mock_sudoers.assert_called_once()
    mock_litellm.assert_called_once()
    logger.info(
        "[IMP:9][test_postflight_selfheal_removes_orphans] remove_orphans invoked with %d orphan(s)",
        len(detected_orphans),
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · DevPlan 140 W5 (W9-T9.15) · self-heal: remove вызывается при orphan>0
# · Scenario: batch_orphan_reconciliation вернул 2 orphan → remove_orphans(orphans) вызывается с ними
# ·   (было: detect-only — orphan оставался до ручного вмешательства)
# · Last fail: N/A — detect-only gap (DevPlan 140 §3 S2, зафиксирован аудитом 2026-08-05)
# · Remove if: self-heal перенесён из _postflight или remove_orphans заменён
# endregion FUNC_test_postflight_selfheal_removes_orphans


# ══════════════════════════════════════════════════════════════════════════════
# DEPLOY PATHS (§9: parallel topo_sort, sequential iteration — через orchestrate())
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_parallel_path_wires_topo_pipeline
## @purpose  DEPLOY_PARALLEL=true path через ПУБЛИЧНЫЙ orchestrate(): _linearize_deploy_order
##           pipeline (load → filter → build_dag → kahn) wired; each topo group deployed via
##           docker_orchestrator.deploy_docker_group; result.deployed/failed — публичные observable.
## @io       tmp_path, caplog → None
## @complexity 2 — mocked topo pipeline + group deploy assert through orchestrate()
## @invariants
##   - kahn output groups deployed sequentially via deploy_docker_group (one call per group)
##   - deploy_docker_group entries use module:overlay format
##   - HC marker set after parallel deploy
##   - deploy_orchestrator=False → group-based deploy path (not deploy-many)


def test_parallel_path_wires_topo_pipeline(tmp_path, caplog) -> None:
    """
    # ▶ mock topo pipeline (2 docker modules → 2 groups) → ⚡ orchestrate(deploy_parallel=True) → ◇ assert topo + group calls → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    logger.info("[IMP:7][test_parallel_path_wires_topo_pipeline] START — parallel topo wiring")

    modules_yamls = [
        {"name": "postgres", "install_type": "docker", "severity": "critical"},
        {"name": "redis", "install_type": "docker", "severity": "warn"},
    ]
    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.topo_sort, "load_module_yamls", return_value=modules_yamls) as mock_load,
        mock.patch.object(orch.topo_sort, "filter_docker_modules", return_value=modules_yamls) as mock_filter,
        mock.patch.object(orch.topo_sort, "build_dag", return_value={"postgres": [], "redis": []}) as mock_dag,
        mock.patch.object(orch.topo_sort, "kahn_topological_sort", return_value=[["postgres"], ["redis"]]) as mock_kahn,
        mock.patch.object(orch.docker_orchestrator, "pre_pull_images", return_value=(2, 0)),
        mock.patch.object(orch.secrets_validator, "batch_check_env", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_group", return_value=(1, 0, [], [])) as mock_group,
        mock.patch.object(orch, "_deploy_system_modules", return_value=(0, [])) as mock_sys,
        mock.patch.object(orch, "_set_hc_marker") as mock_hc,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=True,
            deploy_orchestrator=False,
        )

    mock_load.assert_called_once_with(str(tmp_path / "modules"))
    mock_filter.assert_called_once_with(modules_yamls)
    mock_dag.assert_called_once_with(modules_yamls, filter_names=["postgres", "redis"])
    mock_kahn.assert_called_once_with({"postgres": [], "redis": []})
    assert mock_group.call_count == 2, f"Expected 2 group deploys (one per topo group), got {mock_group.call_count}"
    mock_group.assert_any_call(["postgres"], str(tmp_path / "modules"))
    mock_group.assert_any_call(["redis"], str(tmp_path / "modules"))
    # No system modules in mocked topo output → system deploy correctly skipped
    mock_sys.assert_not_called()
    mock_hc.assert_called_once()
    assert result.deployed == 2, f"Expected deployed=2 (1 per group), got {result.deployed}"
    assert result.failed == [], f"Expected no failures, got {result.failed}"
    assert result.exit_code == 0, f"Clean parallel topo deploy must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_parallel_path_wires_topo_pipeline] topo pipeline + %d group deploys wired correctly",
        mock_group.call_count,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: parallel deploy must drive deploy order from topo groups (DevPlan 050)
# · Scenario: 2 docker modules, kahn → [[postgres],[redis]] → deploy_docker_group called once per group
# · Last fail: N/A
# · Remove if: parallel deploy order stops being topo-driven
# endregion FUNC_test_parallel_path_wires_topo_pipeline


# region FUNC_test_sequential_path_iterates_modules
## @purpose  orchestrate(deploy_parallel=False) iterates all enabled modules: docker →
##           deploy_docker_module, system → invoke_module_interface install (+ liveness
##           healthcheck). Assertions через ПУБЛИЧНЫЕ observable result.deployed/result.failed.
## @io       tmp_path, caplog → None
## @complexity 2 — real detect_install_type on tmp module.yamls + mocked deploy calls through orchestrate()
## @invariants
##   - 3 modules (2 docker + 1 system) → 3 deploy attempts, result.deployed=3
##   - system module gets install + healthcheck liveness invocations
##   - docker modules get deploy_docker_module with modules_dir


def test_sequential_path_iterates_modules(tmp_path, caplog) -> None:
    """
    # ▶ tmp modules (postgres,redis docker + nginx system) → ⚡ orchestrate(deploy_parallel=False) → ◇ assert 3 deploys → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    _write_module_yaml(tmp_path, "nginx", install_type="system", severity="critical")
    node_yaml = _write_node_yaml(
        tmp_path,
        {"postgres": {"enabled": True}, "redis": {"enabled": True}, "nginx": {"enabled": True}},
    )
    logger.info("[IMP:7][test_sequential_path_iterates_modules] START — sequential iteration")

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]) as mock_env,
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_docker,
        mock.patch.object(orch, "_invoke_module_interface", return_value=True) as mock_invoke,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=False,
        )

    assert mock_env.call_count == 3, f"env check must run per module, got {mock_env.call_count}"
    assert mock_docker.call_count == 2, f"docker deploy must run for 2 docker modules, got {mock_docker.call_count}"
    mock_docker.assert_any_call(
        "postgres",
        modules_dir=str(tmp_path / "modules"),
        # через публичный orchestrate() _resolve_overlay_dirs возвращает overlay="" (не None)
        overlay_dir="",
        secrets_env_file=None,
        platform_root=None,
    )
    mock_docker.assert_any_call(
        "redis",
        modules_dir=str(tmp_path / "modules"),
        overlay_dir="",
        secrets_env_file=None,
        platform_root=None,
    )
    # nginx (system): install + healthcheck liveness
    assert mock_invoke.call_count >= 2, (
        f"system module needs install + healthcheck invocations, got {mock_invoke.call_count}"
    )
    mock_invoke.assert_any_call("nginx", "install")
    # REF-0103: liveness-инвок несёт канонный probe-timeout 60s (HEALTHCHECK_CMD_TIMEOUT),
    # не унаследованный COMPOSE_UP_TIMEOUT=180
    from core.internal.shared.timeouts import HEALTHCHECK_CMD_TIMEOUT

    mock_invoke.assert_any_call("nginx", "healthcheck", "liveness", timeout=HEALTHCHECK_CMD_TIMEOUT)
    assert result.deployed == 3, f"Expected 3 deployed modules, got {result.deployed}"
    assert result.failed == [], f"Expected no failures, got {result.failed}"
    assert result.exit_code == 0, f"Clean sequential deploy must exit 0, got {result.exit_code}"
    logger.info(
        "[IMP:9][test_sequential_path_iterates_modules] sequential deploy: deployed=%d failed=%s",
        result.deployed,
        result.failed,
    )

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: sequential for-loop must deploy every enabled module exactly once (best-effort)
# · Scenario: 2 docker + 1 system module → 2× deploy_docker_module + 1× install + 1× liveness
# · Last fail: N/A
# · Remove if: sequential deploy path is removed
# endregion FUNC_test_sequential_path_iterates_modules


# ══════════════════════════════════════════════════════════════════════════════
# REF-0110 (meta-refactoring S-пакет): kahn-линеаризация sequential + fail-fast topo + abort remaining
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_sequential_order_follows_depends_on_two_level_dag
## @purpose  TEST-29 (карточка REF-0110): order-test через ПУБЛИЧНЫЙ orchestrate() с РЕАЛЬНЫМ
##           build_dag+kahn на 2-level DAG — порядок входного списка node.yaml ("app" раньше
##           "base") НЕ авторитетен; деплой идёт по depends_on: base → app. До REF-0110
##           sequential шёл в порядке списка.
## @io       tmp_path, caplog → None (реальный topo-пайплайн, моки только I/O-деплоя)
## @complexity 2 — real load_module_yamls+build_dag+kahn через orchestrate()
## @invariants
##   - deploy_docker_module вызывается в топологическом порядке ["base", "app"]
##   - Оба модуля задеплоены, result.failed пуст


def test_sequential_order_follows_depends_on_two_level_dag(tmp_path, caplog) -> None:
    """
    # ▶ module.yamls: app depends_on [base] → ⚡ orchestrate(deploy_parallel=False) (real kahn)
    # → ◇ assert call order == [base, app] → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "base", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "app", install_type="docker", severity="warn", depends_on=["base"])
    # входной порядок node.yaml: app раньше base — НЕ авторитетен (REF-0110)
    node_yaml = _write_node_yaml(tmp_path, {"app": {"enabled": True}, "base": {"enabled": True}})
    logger.info("[IMP:7][test_sequential_order_follows_depends_on] START — TEST-29 order check")

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_docker,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=False,
        )

    call_order = [call.args[0] for call in mock_docker.call_args_list]
    assert call_order == ["base", "app"], f"Deploy order must follow depends_on (base first), got {call_order}"
    assert result.deployed == 2, f"Both modules must deploy, got {result.deployed}"
    assert result.failed == [], f"No failures expected, got {result.failed}"
    logger.info("[IMP:9][test_sequential_order_follows_depends_on] order=%s (depends_on-aware)", call_order)

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · TEST-29/REF-0110 · sequential deploy order is depends_on-aware
# · Last fail: карточка REF-0110 — sequential шёл в порядке node.yaml (depends_on только в parallel)
# · Remove if: sequential ordering stops being topo-driven
# endregion FUNC_test_sequential_order_follows_depends_on_two_level_dag


# region FUNC_test_sequential_critical_failure_aborts_remaining_groups
## @purpose  REF-0110 abort semantics через orchestrate(): critical-failure в группе G → все
##           модули ПОСЛЕДУЮЩИХ групп добавляются в result.failed и НЕ деплоятся; сосед по
##           группе G (независим, kahn) продолжается; warn-failure НЕ прерывает цикл
##           (DEPLOY_BEST_EFFORT сохранён).
## @io       tmp_path, caplog → None
## @complexity 2 — 3-module DAG: cache(no deps), db(critical, no deps), web(depends_on db)
## @invariants
##   - db critical fail → web aborted (в failed, deploy NOT attempted); cache (сосед по группе) deployed
##   - result.deployed/result.failed — честные публичные observable; IMP:10 abort log присутствует


def test_sequential_critical_failure_aborts_remaining_groups(tmp_path, caplog) -> None:
    """
    # ▶ DAG cache|db(critical) → web → ⚡ orchestrate(deploy_parallel=False), db fails → ◇ assert web aborted + IMP:10 → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "cache", install_type="docker", severity="warn")
    _write_module_yaml(tmp_path, "db", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "web", install_type="docker", severity="warn", depends_on=["db"])
    node_yaml = _write_node_yaml(
        tmp_path,
        {"cache": {"enabled": True}, "db": {"enabled": True}, "web": {"enabled": True}},
    )
    logger.info("[IMP:7][test_sequential_critical_failure_aborts] START — critical abort check")

    def _fail_db(name: str, **_kwargs: object) -> bool:
        return name != "db"

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", side_effect=_fail_db) as mock_docker,
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=False,
        )

    # cache — сосед db по первой kahn-группе (независим) → деплоится; web (зависимая группа) — abort
    attempted = [call.args[0] for call in mock_docker.call_args_list]
    assert "cache" in attempted and "db" in attempted, f"cache+db must be attempted, got {attempted}"
    assert "web" not in attempted, f"Dependent 'web' must be aborted after critical 'db' failure, got {attempted}"
    assert result.deployed == 1, f"Only cache deploys, got {result.deployed}"
    assert result.failed == ["db", "web"], f"Honest failed accounting: [db, web], got {result.failed}"
    assert "[abort]" in caplog.text and "[IMP:10]" in caplog.text, "Missing IMP:10 abort log"
    logger.info("[IMP:9][test_sequential_critical_failure_aborts] attempted=%s failed=%s", attempted, result.failed)

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · REF-0110 · critical failure stops dependents, honest failed accounting
# · Last fail: карточка REF-0110 — failed группа откатывалась и цикл продолжался (web crash-loop без db)
# · Remove if: best-effort continue restored for critical failures
# endregion FUNC_test_sequential_critical_failure_aborts_remaining_groups


# region FUNC_test_sequential_warn_failure_continues
## @purpose  Негатив к abort-semantics (R5): warn-failure НЕ прерывает sequential-цикл —
##           DEPLOY_BEST_EFFORT сохранён для некритических отказов (через orchestrate()).


def test_sequential_warn_failure_continues(tmp_path, caplog) -> None:
    """warn-fail первого модуля → второй всё равно деплоится (best-effort, no abort)."""
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "m1", install_type="docker", severity="warn")
    _write_module_yaml(tmp_path, "m2", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"m1": {"enabled": True}, "m2": {"enabled": True}})
    logger.info("[IMP:7][test_sequential_warn_failure_continues] START — warn continues")

    def _fail_m1(name: str, **_kwargs: object) -> bool:
        return name != "m1"

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]),
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", side_effect=_fail_m1),
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=False,
        )

    assert result.deployed == 1 and result.failed == ["m1"], (
        f"warn-failure must continue cycle: {result.deployed}, {result.failed}"
    )
    assert "[abort]" not in caplog.text, "warn-failure must NOT trigger abort log"
    logger.info("[IMP:9][test_sequential_warn_failure_continues] warn continued (deployed=%d)", result.deployed)

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · abort-semantics — warn-failure does NOT abort the cycle
# · Scenario: m1 (warn) fails → m2 still deploys; deployed=1 failed=[m1]; no [abort] log
# · Last fail: N/A — negative to test_sequential_critical_failure_aborts_remaining_groups
# · Remove if: warn failures start aborting dependents
# endregion FUNC_test_sequential_warn_failure_continues


# region FUNC_test_linearize_unknown_dependency_raises
## @purpose  REF-0110 fail-fast: depends_on на имя без module.yaml → ConfigValidationError
##           (build_dag молча ронял неизвестные зависимости → невидимый отсутствующий порядок).


def test_linearize_unknown_dependency_raises(tmp_path, caplog) -> None:
    """depends_on=['ghost'] (нет module.yaml ghost) → ConfigValidationError от _linearize_deploy_order."""
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "app", install_type="docker", severity="warn", depends_on=["ghost"])
    logger.info("[IMP:7][test_linearize_unknown_dependency_raises] START — unknown-dep guard")

    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError, match="ghost"):
        orch._linearize_deploy_order(["app"], str(tmp_path / "modules"))

    logger.info("[IMP:9][test_linearize_unknown_dependency_raises] unknown dep rejected fail-fast")

    assert_ldd_imp9(caplog)


# endregion FUNC_test_linearize_unknown_dependency_raises


# region FUNC_test_orchestrate_cycle_propagates_config_validation_error
## @purpose  REF-0110 сквозной контракт: топо-цикл через orchestrate() ПРОПАГАЦИРУЕТСЯ как
##           ConfigValidationError (раньше деградировал в WARN + unordered sequential fallback);
##           main() маппит PlatformError → exit 4 (canon exit-code контракт).


def test_orchestrate_cycle_propagates_config_validation_error(tmp_path, caplog) -> None:
    """Цикл a↔b: orchestrate() raises ConfigValidationError; main([...]) → exit 4."""
    caplog.set_level(logging.DEBUG)
    node_yaml = _write_node_yaml(tmp_path, {"a": {"enabled": True}, "b": {"enabled": True}})
    _write_module_yaml(tmp_path, "a", install_type="docker", severity="critical", depends_on=["b"])
    _write_module_yaml(tmp_path, "b", install_type="docker", severity="critical", depends_on=["a"])
    logger.info("[IMP:7][test_orchestrate_cycle_propagates] START — cycle fail-fast")

    from core.internal.shared.exceptions import ConfigValidationError

    argv = [
        "--node-yaml",
        str(node_yaml),
        "--modules-dir",
        str(tmp_path / "modules"),
        "--core-dir",
        str(tmp_path / "core"),
        "--templates-dir",
        str(tmp_path / "templates"),
    ]
    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        pytest.raises(ConfigValidationError, match=r"[Cc]ycle"),
    ):
        orch.orchestrate(str(node_yaml), str(tmp_path / "modules"), str(tmp_path / "core"), str(tmp_path / "templates"))

    rc = orch.main(argv)
    assert rc == 4, f"main must map ConfigValidationError to exit 4, got {rc}"
    logger.info("[IMP:9][test_orchestrate_cycle_propagates] cycle → ConfigValidationError → main exit=4")

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · REF-0110 · topo failure no longer degrades to unordered fallback
# · Last fail: карточка REF-0110 — ошибка topo-sort деградировала в unordered sequential
# · Remove if: topo failures become non-fatal again
# endregion FUNC_test_orchestrate_cycle_propagates_config_validation_error


# region FUNC_test_parallel_group_critical_failure_aborts_remaining_groups
## @purpose  REF-0110 parallel-path abort через orchestrate(): critical-failure группы →
##           последующие группы в result.failed, deploy_docker_group для них НЕ вызывается.


def test_parallel_group_critical_failure_aborts_remaining_groups(tmp_path, caplog) -> None:
    """groups=[[postgres],[redis]], postgres critical fails → redis aborted, одна group-call."""
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    node_yaml = _write_node_yaml(tmp_path, {"postgres": {"enabled": True}, "redis": {"enabled": True}})
    modules_yamls = [
        {"name": "postgres", "install_type": "docker", "severity": "critical"},
        {"name": "redis", "install_type": "docker", "severity": "warn"},
    ]
    logger.info("[IMP:7][test_parallel_group_critical_abort] START — group abort check")

    with (
        mock.patch.object(orch, "_preflight", return_value=None),
        mock.patch.object(orch, "_postflight", return_value=None),
        mock.patch.object(orch, "_interpolation_dryrun", return_value=[]),
        mock.patch.object(orch.topo_sort, "load_module_yamls", return_value=modules_yamls),
        mock.patch.object(orch.topo_sort, "filter_docker_modules", return_value=modules_yamls),
        mock.patch.object(orch.topo_sort, "build_dag", return_value={"postgres": [], "redis": []}),
        mock.patch.object(orch.topo_sort, "kahn_topological_sort", return_value=[["postgres"], ["redis"]]),
        mock.patch.object(orch.docker_orchestrator, "pre_pull_images", return_value=(2, 0)),
        mock.patch.object(orch.secrets_validator, "batch_check_env", return_value=[]),
        mock.patch.object(
            orch.docker_orchestrator, "deploy_docker_group", return_value=(0, 0, ["postgres"], True)
        ) as mock_group,
        mock.patch.object(orch, "_deploy_system_modules", return_value=(0, [])),
        mock.patch.object(orch, "_set_hc_marker"),
    ):
        result = orch.orchestrate(
            str(node_yaml),
            str(tmp_path / "modules"),
            str(tmp_path / "core"),
            str(tmp_path / "templates"),
            deploy_parallel=True,
            deploy_orchestrator=False,
        )

    assert mock_group.call_count == 1, f"Only first group deploys, got {mock_group.call_count} calls"
    assert result.deployed == 0, f"No successful deploys expected, got {result.deployed}"
    assert result.failed == ["postgres", "redis"], (
        f"Honest failed accounting incl. aborted dependents, got {result.failed}"
    )
    assert result.exit_code == 2, f"Critical failure must exit 2, got {result.exit_code}"
    assert "[IMP:10][_deploy_docker_groups][abort]" in caplog.text, "Missing IMP:10 group-abort log"
    logger.info("[IMP:9][test_parallel_group_critical_abort] failed=%s (redis aborted)", result.failed)

    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression · REF-0110 · parallel group critical failure aborts remaining groups
# · Scenario: groups=[[postgres],[redis]], postgres critical fails → redis in failed (one group-call), exit 2
# · Last fail: карточка REF-0110 — group-failures не агрегировались, dependents стартовали против отката
# · Remove if: parallel abort semantics changes
# endregion FUNC_test_parallel_group_critical_failure_aborts_remaining_groups
