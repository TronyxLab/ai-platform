#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-alert-rules alerting-enabled template-render created skipped failed
# STRUCTURE: ┌4 test functions┐ → ◇ alerting disabled (1) → ◇ template missing (1) → ◇ created (1) → ◇ render failure (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/alert_rules.py — generate_alert_rules()
#            (DevPlan 117 G T54 extraction).
## @scope    No Docker — tmp_path template/output fixtures.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — alert_rules direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

from pathlib import Path

from monitoring.alert_rules import generate_alert_rules
from monitoring_config_renderer import ProjectMonitoringConfig


def _config(**overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": "myapp",
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "alerting_enabled": True,
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


def _write_template(tmp_path: Path, name: str = "alert-rules.yml") -> Path:
    p = tmp_path / name
    p.write_text("groups:\n  - name: {{PROJECT}}\n", encoding="utf-8")
    return p


# 🧪 TRAP[TEST] · Regression · Scenario: alerting disabled
# · Expect: noop
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules logic changes
def test_alert_rules_disabled_noop(tmp_path: Path, caplog) -> None:
    """alerting_enabled=False → noop."""
    caplog.set_level(0)
    result = generate_alert_rules(_config(alerting_enabled=False), template_path=_write_template(tmp_path))

    assert result.status == "noop"
    assert result.component == "alerting"


# 🧪 TRAP[TEST] · Regression · Scenario: template missing
# · Expect: skipped
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: template-missing branch logic changes
def test_alert_rules_template_missing(tmp_path: Path, caplog) -> None:
    """Template not found → skipped."""
    caplog.set_level(0)
    result = generate_alert_rules(_config(), template_path=tmp_path / "missing.yml")

    assert result.status == "skipped"


# 🧪 TRAP[TEST] · Regression · Scenario: created
# · Expect: alert rules YAML written with PROJECT substituted
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules render logic changes
def test_alert_rules_created(tmp_path: Path, caplog) -> None:
    """Enabled + template → created, PROJECT substituted."""
    caplog.set_level(0)
    tmpl = _write_template(tmp_path)
    out_dir = tmp_path / "out"

    result = generate_alert_rules(_config(), template_path=tmpl, output_dir=out_dir)

    assert result.status == "created"
    out_file = out_dir / "myapp-alerts.yml"
    assert out_file.exists()
    assert "- name: myapp" in out_file.read_text()


# 🧪 TRAP[TEST] · Regression · Scenario: render failure
# · Expect: failed
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_alert_rules error handling changes
def test_alert_rules_render_failure(tmp_path: Path, caplog) -> None:
    """Unresolved placeholder → failed."""
    caplog.set_level(0)
    bad_tmpl = tmp_path / "bad.yml"
    bad_tmpl.write_text("groups:\n  - name: {{UNRESOLVED_VAR}}\n", encoding="utf-8")

    result = generate_alert_rules(_config(), template_path=bad_tmpl, output_dir=tmp_path / "out")

    assert result.status == "failed"
