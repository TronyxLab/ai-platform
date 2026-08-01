#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-prometheus-targets file-sd target-json metrics-enabled labels schema
# STRUCTURE: ┌3 test functions┐ → ◇ metrics disabled (1) → ◇ created schema (1) → ◇ write failure (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/prometheus_targets.py — generate_prometheus_target()
#            (DevPlan 117 G T54 extraction from monitoring_config_renderer.py).
## @scope    No Docker — tmp_path outputs.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — prometheus_targets direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

import json
from pathlib import Path

from monitoring.prometheus_targets import generate_prometheus_target
from monitoring_config_renderer import ProjectMonitoringConfig


def _config(project_name: str = "myapp", **overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": project_name,
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "metrics_enabled": True,
        "metrics_port": 9090,
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


# 🧪 TRAP[TEST] · Regression · Scenario: metrics disabled
# · Expect: noop RenderResult, no file written
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_prometheus_target logic changes
def test_generate_target_metrics_disabled(tmp_path: Path, caplog) -> None:
    """metrics_enabled=False → noop, no file."""
    caplog.set_level(0)
    config = _config(metrics_enabled=False)

    result = generate_prometheus_target(config, output_dir=tmp_path)

    assert result.status == "noop"
    assert result.component == "prometheus"
    assert list(tmp_path.glob("*.json")) == []


# 🧪 TRAP[TEST] · Regression · Scenario: enabled → target JSON created
# · Expect: {targets, labels{project,type,node,service}} schema
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_prometheus_target schema changes
def test_generate_target_created_schema(tmp_path: Path, caplog) -> None:
    """Enabled → target JSON with correct schema."""
    caplog.set_level(0)
    config = _config()

    result = generate_prometheus_target(config, output_dir=tmp_path)

    assert result.status == "created"
    assert result.output_path is not None
    target_file = tmp_path / "myapp.json"
    assert target_file.exists()

    data = json.loads(target_file.read_text())
    assert data["targets"] == ["myapp:9090"]
    assert data["labels"]["project"] == "myapp"
    assert data["labels"]["type"] == "backend"
    assert data["labels"]["node"] == "tronyx-vps"
    assert data["labels"]["service"] == "myapp"


# 🧪 TRAP[TEST] · Regression · Scenario: write failure (read-only dir)
# · Expect: failed RenderResult
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: generate_prometheus_target error handling changes
def test_generate_target_write_failure(tmp_path: Path, caplog) -> None:
    """OSError on write → failed RenderResult."""
    caplog.set_level(0)
    config = _config()

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        result = generate_prometheus_target(config, output_dir=ro)
    finally:
        ro.chmod(0o755)

    assert result.status == "failed"
