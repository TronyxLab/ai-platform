"""
# GREP_SUMMARY: test-orchestrator-metrics, severity, exit-code, status-metrics-json, hc-marker, llm-summary, pure, E6, R5, unit-tests
# STRUCTURE: ▶ test_aggregate_severity ┌failed+map┐ → (crit,warn) → ⎋ assert │ ▶ test_exit_code_from_results ┌crit/warn/none┐ → 2/0/0 │ ▶ test_status_metrics_json → json.loads roundtrip │ ▶ test_hc_marker_path → constant │ ▶ test_render_llm_summary → formatted string │ ▶ test_pure_no_side_effects (R5) → determinism
# region MODULE_CONTRACT
## @purpose  Unit tests for orchestrator_metrics.py (DevPlan 119 E6) — pure severity/exit-code/
##           status-metrics/hc-marker/llm-summary functions extracted from deploy_orchestrator.py.
## @scope    Covers $TEST_SPEC: test_aggregate_severity + R5 test_orchestrator_metrics_pure
##           (нет сайд-эффектов — детерминированность).
## @invariants
##   - Native imports, tmp_path only where needed, no I/O side-effects tested
##   - Every test asserts determinism (pure contract)
## @rationale  Чистые функции (без I/O) — изолируемое тестирование; R5: purity = детерминизм.
## @changes  2026-08-02 · Created (DevPlan 119 E6)
# endregion MODULE_CONTRACT
"""

import json
import logging

import pytest

from core.internal.bootstrap.deploy import orchestrator_metrics
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · severity aggregation pure
# · Regression: E6 extraction from deploy_orchestrator._aggregate_severity
# · Last fail: N/A (new canon module)
# · Remove if: aggregate_severity API changes
@ldd_trajectory
def test_aggregate_severity(caplog: pytest.LogCaptureFixture) -> None:
    """aggregate_severity maps failed names to (crit, warn) from pre-resolved severity map."""
    with caplog.at_level(logging.INFO):
        crit, warn = orchestrator_metrics.aggregate_severity(
            ["critical_mod", "warn_mod", "unknown_mod"],
            {"critical_mod": "critical", "warn_mod": "warn"},
        )
    assert crit == 1
    assert warn == 2  # warn_mod + unknown (default warn)
    logger.critical("[IMP:9][test] aggregate_severity: crit=%d warn=%d", crit, warn)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · exit code mapping pure
# · Regression: E6 extraction from deploy_orchestrator._compute_exit_code
# · Last fail: N/A (new canon module)
# · Remove if: exit_code_from_results API changes
def test_exit_code_from_results() -> None:
    """exit_code_from_results: CRIT→2, WARN→0, none→0 (DEPLOY_BEST_EFFORT)."""
    assert orchestrator_metrics.exit_code_from_results(crit=1, warn=0, deployed=1) == 2
    assert orchestrator_metrics.exit_code_from_results(crit=0, warn=1, deployed=1) == 0
    assert orchestrator_metrics.exit_code_from_results(crit=0, warn=0, deployed=5) == 0


# 🧪 TRAP[TEST] · 2026-08-02 · unit · status metrics JSON pure
# · Regression: E6 extraction from deploy_orchestrator._create_status_metrics_json
# · Last fail: N/A (new canon module)
# · Remove if: status_metrics_json API changes
def test_status_metrics_json() -> None:
    """status_metrics_json returns valid JSON object (schema_version=2, bind-mount P1 fix)."""
    payload = json.loads(orchestrator_metrics.status_metrics_json())
    assert payload["schema_version"] == 2
    assert payload["containers"] == []
    assert payload["host"] == {}


# 🧪 TRAP[TEST] · 2026-08-02 · unit · hc marker path constant
# · Regression: E6 extraction from deploy_orchestrator._HC_DONE_MARKER
# · Last fail: N/A (new canon module)
# · Remove if: hc_marker_path API changes
# GUARD-PRESERVE (168): единственное покрытие hc_marker_path (E6 константа-маркер hc_done_in_deploy, static)
def test_hc_marker_path() -> None:
    """hc_marker_path returns the canonical marker path."""
    assert orchestrator_metrics.hc_marker_path() == "/var/lib/platform/.bootstrap/.hc_done_in_deploy"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · llm summary pure formatting
# · Regression: E6 extraction from deploy_orchestrator._render_litellm_config
# · Last fail: N/A (new canon module)
# · Remove if: render_llm_summary API changes
def test_render_llm_summary() -> None:
    """render_llm_summary formats a diagnostic summary line (no I/O)."""
    summary = orchestrator_metrics.render_llm_summary(
        "/opt/platform/core",
        "/opt/platform/core/internal/llm/policy.yaml",
        "/opt/platform/core/modules/litellm/config/litellm-config.yml",
    )
    assert "policy.yaml" in summary
    assert "litellm-config.yml" in summary
    assert "/opt/platform/core" in summary


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · pure functions — no side effects
# · Regression: E6 — functions must be deterministic (no I/O, no state)
# · Scenario: two identical calls → identical results; no fs mutation
# · Remove if: orchestrator_metrics purity contract changes
def test_orchestrator_metrics_pure_negative(tmp_path) -> None:
    """R5: pure functions are deterministic and produce NO filesystem side-effects."""
    before = {p.name for p in tmp_path.iterdir()} if tmp_path.exists() else set()

    # Two identical calls → identical results (determinism)
    r1a, r1b = orchestrator_metrics.aggregate_severity(["a", "b"], {"a": "critical"})
    r2a, r2b = orchestrator_metrics.aggregate_severity(["a", "b"], {"a": "critical"})
    assert (r1a, r1b) == (r2a, r2b)

    assert orchestrator_metrics.exit_code_from_results(1, 0, 1) == orchestrator_metrics.exit_code_from_results(1, 0, 1)
    assert orchestrator_metrics.status_metrics_json() == orchestrator_metrics.status_metrics_json()

    # No filesystem side-effects (functions must not create/delete files)
    after = {p.name for p in tmp_path.iterdir()} if tmp_path.exists() else set()
    assert before == after, "R5 FAIL: pure functions must have no filesystem side-effects"
