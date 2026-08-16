# GREP_SUMMARY: test-deploy-orchestrator, routing, severity, parallel, sequential, orchestrator-cli, unit
# STRUCTURE: ▶ routing tests [_route_deploy: seq|parallel|orchestrator] → ▶ severity tests [_aggregate_severity + _compute_exit_code: crit|warn|none] → ▶ orchestrate tests [empty-noop | parse | preflight | postflight] → ▶ deploy tests [parallel topo | sequential loop] → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for deploy_orchestrator.py (DevPlan 100 TASK-4a) — routing decision,
##           severity aggregation, preflight/postflight wiring, parallel/sequential deploy paths.
##           All tests use native imports + unittest.mock.patch — NO subprocess, NO Docker.
## @scope    Covers $TEST_SPEC §9 entries for tests/unit/test_deploy_orchestrator.py (12 scenarios):
##           sequential/parallel/orchestrator routing, critical/warn/no-failure exit codes,
##           empty-modules noop, node.yaml parsing, preflight/postflight call wiring,
##           parallel topo_sort invocation, sequential module iteration.
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


def _write_module_yaml(tmp_path: Path, name: str, install_type: str = "docker", severity: str = "warn") -> Path:
    """Write modules/<name>/module.yaml with install_type + severity."""
    module_dir = tmp_path / "modules" / name
    module_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = module_dir / "module.yaml"
    yaml_path.write_text(yaml.safe_dump({"name": name, "install_type": install_type, "severity": severity}))
    return yaml_path


def _assert_ldd_imp9(caplog) -> None:
    """Print LDD trajectory (IMP:7-10) and assert ≥1 IMP:9 log present."""
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if hasattr(record, "message") and "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found in test trajectory"


# endregion HELPERS


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING (§9: _route_deploy)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_orchestrate_sequential_routing
## @purpose  deploy_parallel=False → _route_deploy dispatches to _deploy_sequential (not _deploy_parallel)
## @io       caplog → None (pytest.fail if wrong route taken)
## @complexity 1 — mocked dispatch assert
## @invariants
##   - Sequential route must NOT call _deploy_parallel
##   - Sequential route returns empty modules_info {} (severity falls back to per-module lookup)


@pytest.mark.smoke
def test_orchestrate_sequential_routing(caplog) -> None:
    """
    # ▶ mock _deploy_sequential → ◇ _route_deploy(deploy_parallel=False) → ⚡ assert seq called, parallel NOT → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orchestrate_sequential_routing] START — sequential routing check")

    with (
        mock.patch.object(orch, "_deploy_sequential", return_value=(2, [])) as mock_seq,
        mock.patch.object(orch, "_deploy_parallel", return_value=(0, [], {})) as mock_par,
    ):
        deployed, failed, modules_info = orch._route_deploy(
            ["postgres", "redis"], {}, "/mods", "/core", deploy_parallel=False, deploy_orchestrator=False
        )

    mock_seq.assert_called_once_with(["postgres", "redis"], "/mods", "/core", {})
    mock_par.assert_not_called()
    assert deployed == 2, f"Sequential route should return deployed=2, got {deployed}"
    assert failed == [], f"Sequential route should return no failures, got {failed}"
    assert modules_info == {}, "Sequential route must return empty modules_info (per-module severity fallback)"
    logger.info(
        "[IMP:9][test_orchestrate_sequential_routing] SEQUENTIAL route dispatched correctly (deployed=%d)", deployed
    )

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_PARALLEL=false must dispatch to sequential path (best-effort)
# · Scenario: _route_deploy(deploy_parallel=False) with mocked _deploy_sequential → seq called, parallel NOT
# · Last fail: N/A
# · Remove if: routing decision moves out of _route_deploy
# endregion FUNC_test_orchestrate_sequential_routing


# region FUNC_test_orchestrate_parallel_routing
## @purpose  deploy_parallel=True, deploy_orchestrator=False → _route_deploy dispatches to _deploy_parallel
## @io       caplog → None (pytest.fail if wrong route taken)
## @complexity 1 — mocked dispatch assert
## @invariants
##   - Parallel route must NOT call _deploy_sequential
##   - deploy_orchestrator flag forwarded to _deploy_parallel as positional arg (False)


@pytest.mark.smoke
def test_orchestrate_parallel_routing(caplog) -> None:
    """
    # ▶ mock _deploy_parallel → ◇ _route_deploy(deploy_parallel=True) → ⚡ assert parallel called, seq NOT → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orchestrate_parallel_routing] START — parallel routing check")

    with (
        mock.patch.object(orch, "_deploy_parallel", return_value=(1, ["postgres"], {"postgres": {}})) as mock_par,
        mock.patch.object(orch, "_deploy_sequential", return_value=(0, [])) as mock_seq,
    ):
        deployed, failed, modules_info = orch._route_deploy(
            ["postgres"], {"postgres": ""}, "/mods", "/core", deploy_parallel=True, deploy_orchestrator=False
        )

    mock_par.assert_called_once_with(["postgres"], {"postgres": ""}, "/mods", "/core", deploy_orchestrator=False)
    mock_seq.assert_not_called()
    assert deployed == 1, f"Parallel route should return deployed=1, got {deployed}"
    assert failed == ["postgres"], f"Parallel route should propagate failures, got {failed}"
    assert "postgres" in modules_info, "Parallel route must return enriched modules_info for severity lookup"
    logger.info(
        "[IMP:9][test_orchestrate_parallel_routing] PARALLEL route dispatched correctly (deployed=%d)", deployed
    )

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_PARALLEL=true must dispatch to parallel path (DevPlan 050)
# · Scenario: _route_deploy(deploy_parallel=True, deploy_orchestrator=False) → parallel called with flag False
# · Last fail: N/A
# · Remove if: routing decision moves out of _route_deploy
# endregion FUNC_test_orchestrate_parallel_routing


# region FUNC_test_orchestrate_orchestrator_routing
## @purpose  deploy_parallel=True, deploy_orchestrator=True → _route_deploy forwards flag to _deploy_parallel
## @io       caplog → None (pytest.fail if flag not forwarded)
## @complexity 1 — mocked dispatch assert
## @invariants
##   - Orchestrator flag must reach _deploy_parallel (which then calls _deploy_orchestrator)
##   - Parallel route with orchestrator flag must NOT fall to sequential


@pytest.mark.smoke
def test_orchestrate_orchestrator_routing(caplog) -> None:
    """
    # ▶ mock _deploy_parallel → ◇ _route_deploy(deploy_parallel=True, deploy_orchestrator=True) → ⚡ assert flag forwarded → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_orchestrate_orchestrator_routing] START — orchestrator routing check")

    with (
        mock.patch.object(orch, "_deploy_parallel", return_value=(0, [], {})) as mock_par,
        mock.patch.object(orch, "_deploy_sequential", return_value=(0, [])) as mock_seq,
    ):
        deployed, failed, modules_info = orch._route_deploy(
            ["postgres", "redis"], {}, "/mods", "/core", deploy_parallel=True, deploy_orchestrator=True
        )

    mock_par.assert_called_once_with(["postgres", "redis"], {}, "/mods", "/core", deploy_orchestrator=True)
    mock_seq.assert_not_called()
    assert deployed == 0 and failed == [], "Orchestrator route must propagate mocked (0, []) result"
    assert modules_info == {}, "Orchestrator route returns mocked modules_info"
    logger.info("[IMP:9][test_orchestrate_orchestrator_routing] ORCHESTRATOR flag forwarded to _deploy_parallel")

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: DEPLOY_ORCHESTRATOR=true must reach _deploy_parallel (DevPlan 089 T14)
# · Scenario: _route_deploy(deploy_parallel=True, deploy_orchestrator=True) → parallel called with flag True
# · Last fail: N/A
# · Remove if: routing decision moves out of _route_deploy
# endregion FUNC_test_orchestrate_orchestrator_routing


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


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

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · DevPlan 140 W5 (W9-T9.15) · self-heal: remove вызывается при orphan>0
# · Scenario: batch_orphan_reconciliation вернул 2 orphan → remove_orphans(orphans) вызывается с ними
# ·   (было: detect-only — orphan оставался до ручного вмешательства)
# · Last fail: N/A — detect-only gap (DevPlan 140 §3 S2, зафиксирован аудитом 2026-08-05)
# · Remove if: self-heal перенесён из _postflight или remove_orphans заменён
# endregion FUNC_test_postflight_selfheal_removes_orphans


# ══════════════════════════════════════════════════════════════════════════════
# DEPLOY PATHS (§9: parallel topo_sort, sequential iteration)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_deploy_parallel_calls_topo_sort
## @purpose  _deploy_parallel must call _topo_sort pipeline (load → filter → build_dag → kahn) and
##           deploy each topo group via deploy_docker_group.
## @io       tmp_path, caplog → None
## @complexity 2 — mocked topo pipeline + group deploy assert
## @invariants
##   - kahn output groups deployed sequentially via deploy_docker_group (one call per group)
##   - deploy_docker_group entries use module:overlay format
##   - HC marker set after parallel deploy
##   - deploy_orchestrator=False → group-based deploy path (not deploy-many)


def test_deploy_parallel_calls_topo_sort(tmp_path, caplog) -> None:
    """
    # ▶ mock topo pipeline (2 docker modules → 2 groups) → ⚡ _deploy_parallel → ◇ assert topo + group calls → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deploy_parallel_calls_topo_sort] START — parallel topo wiring")

    modules_yamls = [
        {"name": "postgres", "install_type": "docker", "severity": "critical"},
        {"name": "redis", "install_type": "docker", "severity": "warn"},
    ]
    with (
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
        deployed, failed, modules_info = orch._deploy_parallel(
            ["postgres", "redis"], {}, str(tmp_path / "modules"), str(tmp_path / "core"), deploy_orchestrator=False
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
    assert deployed == 2, f"Expected deployed=2 (1 per group), got {deployed}"
    assert failed == [], f"Expected no failures, got {failed}"
    assert modules_info["postgres"]["severity"] == "critical", "modules_info must carry severity for aggregation"
    logger.info(
        "[IMP:9][test_deploy_parallel_calls_topo_sort] topo pipeline + %d group deploys wired correctly",
        mock_group.call_count,
    )

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: parallel deploy must drive deploy order from topo groups (DevPlan 050)
# · Scenario: 2 docker modules, kahn → [[postgres],[redis]] → deploy_docker_group called once per group
# · Last fail: N/A
# · Remove if: parallel deploy order stops being topo-driven
# endregion FUNC_test_deploy_parallel_calls_topo_sort


# region FUNC_test_deploy_sequential_iterates_modules
## @purpose  _deploy_sequential iterates all enabled modules: docker → deploy_docker_module,
##           system → invoke_module_interface install (+ liveness healthcheck).
## @io       tmp_path, caplog → None
## @complexity 2 — real detect_install_type on tmp module.yamls + mocked deploy calls
## @invariants
##   - 3 modules (2 docker + 1 system) → 3 deploy attempts, deployed=3
##   - system module gets install + healthcheck liveness invocations
##   - docker modules get deploy_docker_module with modules_dir


def test_deploy_sequential_iterates_modules(tmp_path, caplog) -> None:
    """
    # ▶ tmp modules (postgres,redis docker + nginx system) → ⚡ _deploy_sequential → ◇ assert 3 deploys → ⎋ pass | fail
    """
    caplog.set_level(logging.DEBUG)
    _write_module_yaml(tmp_path, "postgres", install_type="docker", severity="critical")
    _write_module_yaml(tmp_path, "redis", install_type="docker", severity="warn")
    _write_module_yaml(tmp_path, "nginx", install_type="system", severity="critical")
    logger.info("[IMP:7][test_deploy_sequential_iterates_modules] START — sequential iteration")

    with (
        mock.patch.object(orch.secrets_validator, "check_env_requires", return_value=[]) as mock_env,
        mock.patch.object(orch.docker_orchestrator, "deploy_docker_module", return_value=True) as mock_docker,
        mock.patch.object(orch, "_invoke_module_interface", return_value=True) as mock_invoke,
    ):
        deployed, failed = orch._deploy_sequential(
            ["postgres", "redis", "nginx"], str(tmp_path / "modules"), str(tmp_path / "core")
        )

    assert mock_env.call_count == 3, f"env check must run per module, got {mock_env.call_count}"
    assert mock_docker.call_count == 2, f"docker deploy must run for 2 docker modules, got {mock_docker.call_count}"
    mock_docker.assert_any_call(
        "postgres",
        modules_dir=str(tmp_path / "modules"),
        overlay_dir=None,
        secrets_env_file=None,
        platform_root=None,
    )
    mock_docker.assert_any_call(
        "redis",
        modules_dir=str(tmp_path / "modules"),
        overlay_dir=None,
        secrets_env_file=None,
        platform_root=None,
    )
    # nginx (system): install + healthcheck liveness
    assert mock_invoke.call_count >= 2, (
        f"system module needs install + healthcheck invocations, got {mock_invoke.call_count}"
    )
    mock_invoke.assert_any_call("nginx", "install")
    mock_invoke.assert_any_call("nginx", "healthcheck", "liveness")
    assert deployed == 3, f"Expected 3 deployed modules, got {deployed}"
    assert failed == [], f"Expected no failures, got {failed}"
    logger.info(
        "[IMP:9][test_deploy_sequential_iterates_modules] sequential deploy: deployed=%d failed=%s", deployed, failed
    )

    _assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · Regression: sequential for-loop must deploy every enabled module exactly once (best-effort)
# · Scenario: 2 docker + 1 system module → 2× deploy_docker_module + 1× install + 1× liveness
# · Last fail: N/A
# · Remove if: sequential deploy path is removed
# endregion FUNC_test_deploy_sequential_iterates_modules
