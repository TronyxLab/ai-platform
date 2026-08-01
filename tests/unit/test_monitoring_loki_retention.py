#!/usr/bin/env python3
# GREP_SUMMARY: test-monitoring-loki-retention runtime-config idempotent catch-all insert period
# STRUCTURE: ┌4 test functions┐ → ◇ fresh file created (1) → ◇ idempotent skip (1) → ◇ insert before catch-all (1) → ◇ parse error (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/monitoring/loki_retention.py — update_loki_retention()
#            (DevPlan 117 G T54 extraction).
## @scope    No Docker — tmp_path YAML fixtures.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T54 §TEST_SPEC — loki_retention direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T54 — created
# endregion MODULE_CONTRACT

from pathlib import Path

import yaml
from monitoring.loki_retention import update_loki_retention
from monitoring_config_renderer import ProjectMonitoringConfig


def _config(**overrides) -> ProjectMonitoringConfig:
    defaults = {
        "project_name": "myapp",
        "project_type": "backend",
        "project_dir": Path("/tmp"),
        "node_name": "tronyx-vps",
        "platform_root": Path("/opt/platform"),
        "logs_retention": "7d",
    }
    defaults.update(overrides)
    return ProjectMonitoringConfig(**defaults)


# 🧪 TRAP[TEST] · Regression · Scenario: fresh runtime config
# · Expect: file created with project stream
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: update_loki_retention creation logic changes
def test_retention_fresh_file(tmp_path: Path, caplog) -> None:
    """Missing config → created with project stream."""
    caplog.set_level(0)
    target = tmp_path / "loki-runtime-config.yml"

    result = update_loki_retention(_config(), runtime_config_path=target)

    assert result.status == "updated"
    data = yaml.safe_load(target.read_text())
    streams = data["limits_config"]["retention_stream"]
    assert any(s["selector"] == '{compose_project="myapp"}' for s in streams)


# 🧪 TRAP[TEST] · Regression · Scenario: idempotent insert
# · Expect: second call → skipped, no duplicate
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: idempotency logic changes
def test_retention_idempotent_insert(tmp_path: Path, caplog) -> None:
    """Existing stream → skipped (no duplicate)."""
    caplog.set_level(0)
    target = tmp_path / "loki-runtime-config.yml"

    first = update_loki_retention(_config(), runtime_config_path=target)
    second = update_loki_retention(_config(), runtime_config_path=target)

    assert first.status == "updated"
    assert second.status == "skipped"
    data = yaml.safe_load(target.read_text())
    streams = data["limits_config"]["retention_stream"]
    assert sum(s["selector"] == '{compose_project="myapp"}' for s in streams) == 1


# 🧪 TRAP[TEST] · Regression · Scenario: insert before catch-all
# · Expect: project rule precedes compose_project=~ rule
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: catch-all ordering logic changes
def test_retention_insert_before_catchall(tmp_path: Path, caplog) -> None:
    """Project rule inserted before catch-all."""
    caplog.set_level(0)
    target = tmp_path / "loki-runtime-config.yml"
    target.write_text(
        "limits_config:\n"
        "  retention_stream:\n"
        "    - selector: '{compose_project=~\".+\"}' \n"
        "      priority: 1\n"
        "      period: 720h\n",
        encoding="utf-8",
    )

    update_loki_retention(_config(), runtime_config_path=target)

    data = yaml.safe_load(target.read_text())
    streams = data["limits_config"]["retention_stream"]
    assert streams[0]["selector"] == '{compose_project="myapp"}'
    assert "compose_project=~" in streams[1]["selector"]


# 🧪 TRAP[TEST] · Regression · Scenario: forever retention → 0h period
# · Expect: period 0h
# · Last fail: None (new test for DevPlan 117 G T54)
# · Remove if: retention parsing changes
def test_retention_forever(tmp_path: Path, caplog) -> None:
    """logs_retention='forever' → period 0h."""
    caplog.set_level(0)
    target = tmp_path / "loki-runtime-config.yml"

    update_loki_retention(_config(logs_retention="forever"), runtime_config_path=target)

    data = yaml.safe_load(target.read_text())
    streams = data["limits_config"]["retention_stream"]
    assert streams[0]["period"] == "0h"
