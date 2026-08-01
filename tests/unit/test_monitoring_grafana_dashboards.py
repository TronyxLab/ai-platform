#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-grafana-dashboards dashboard-enabled template-render created skipped failed
# STRUCTURE: ┌4 test functions┐ → ◇ dashboard disabled (1) → ◇ template missing (1) → ◇ created (1) → ◇ render failure (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/grafana_dashboards.py — generate_grafana_dashboard()
#            (DevPlan 117 G T54 extraction).
## @scope    No Docker — tmp_path template/output fixtures.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — grafana_dashboards direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

from pathlib import Path

from monitoring.grafana_dashboards import generate_grafana_dashboard
from monitoring_config_renderer import ProjectMonitoringConfig


def _config(**overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": "myapp",
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "dashboard_enabled": True,
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


def _write_template(tmp_path: Path, name: str = "dashboard.json") -> Path:
    p = tmp_path / name
    p.write_text('{"title": "{{PROJECT}}", "type": "{{TYPE}}"}', encoding="utf-8")
    return p


# 🧪 TRAP[TEST] · Regression · Scenario: dashboard disabled
# · Expect: noop RenderResult
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_grafana_dashboard logic changes
def test_dashboard_disabled_noop(tmp_path: Path, caplog) -> None:
    """dashboard_enabled=False → noop."""
    caplog.set_level(0)
    result = generate_grafana_dashboard(_config(dashboard_enabled=False), template_path=_write_template(tmp_path))

    assert result.status == "noop"
    assert result.component == "grafana"


# 🧪 TRAP[TEST] · Regression · Scenario: template missing
# · Expect: skipped RenderResult
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: template-missing branch logic changes
def test_dashboard_template_missing(tmp_path: Path, caplog) -> None:
    """Template not found → skipped."""
    caplog.set_level(0)
    result = generate_grafana_dashboard(_config(), template_path=tmp_path / "missing.json")

    assert result.status == "skipped"


# 🧪 TRAP[TEST] · Regression · Scenario: enabled + template → created
# · Expect: dashboard JSON written with PROJECT substituted
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_grafana_dashboard render logic changes
def test_dashboard_created(tmp_path: Path, caplog) -> None:
    """Enabled + template → created, PROJECT substituted."""
    caplog.set_level(0)
    tmpl = _write_template(tmp_path)
    out_dir = tmp_path / "out"

    result = generate_grafana_dashboard(_config(), template_path=tmpl, output_dir=out_dir)

    assert result.status == "created"
    dash_file = out_dir / "myapp.json"
    assert dash_file.exists()
    content = dash_file.read_text()
    assert '"title": "myapp"' in content
    assert '"type": "backend"' in content


# 🧪 TRAP[TEST] · Regression · Scenario: render failure (unresolved var)
# · Expect: failed RenderResult
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_grafana_dashboard error handling changes
def test_dashboard_render_failure(tmp_path: Path, caplog) -> None:
    """Unresolved placeholder → failed RenderResult."""
    caplog.set_level(0)
    bad_tmpl = tmp_path / "bad.json"
    bad_tmpl.write_text('{"title": "{{UNRESOLVED_VAR}}"}', encoding="utf-8")

    result = generate_grafana_dashboard(_config(), template_path=bad_tmpl, output_dir=tmp_path / "out")

    assert result.status == "failed"
