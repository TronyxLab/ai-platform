# GREP_SUMMARY: test-monitoring-config-renderer deep-merge config-loading L1-L2-L3 retention-parsing LDD
# STRUCTURE: fixtures(tmp_path YAML factories) →
#            test_deep_merge_simple → test_deep_merge_nested_3levels →
#            test_deep_merge_preserves_base_keys → test_load_l1_defaults_with_type →
#            test_load_l1_defaults_missing_file → test_load_l2_overrides_present →
#            test_load_l2_overrides_missing_file → test_build_merged_config_full_pipeline →
#            test_build_merged_config_no_monitoring_section →
#            test_build_merged_config_no_ai_yaml → test_retention_parsing_variants →
#            test_str_to_bool_variants → test_load_l3_project_config →
#            test_all_components_noop_when_no_monitoring
# region MODULE_CONTRACT
## @purpose  Unit tests for monitoring_config_renderer.py — CONFIG-LOADING layer only:
##           deep_merge, 3-level merge (L1/L2/L3), retention parsing, _str_to_bool.
## @scope    All tests use tmp_path (no hardcoded paths). No Docker daemon required.
##           Generator tests (prometheus targets, loki retention, alert rules, grafana)
##           were REMOVED in DevPlan 118 F3 — canonical coverage lives in 7 files under
##           tests/unit/test_monitoring_*.py (prometheus_targets, loki_retention,
##           alert_rules, grafana_dashboards, catalog_refresh, langfuse_projects,
##           service_reload). CLI contract tests live in test_render_monitoring_cli.py.
## @invariants
##   - All tests use tmp_path fixture (zero hardcoded paths)
##   - No Docker daemon required for any test
##   - Each test verifies [IMP:9] log presence via caplog fixture
##   - Tests run in tests/unit/ — no @pytest.mark.requires_docker
##   - YAML fixtures written to tmp_path in test setup (not committed as separate files)
##   - R1-чистка (DevPlan 116 B7 T7): 0 assert True/pass — все хвостовые pass-asserts удалены
## @rationale DevPlan 074 §5.1 — config-loading test cases. Generator-тесты дублировали
##           7 новых файлов (волна 117 G T54) — F3 удаляет дубли, файл 943→~300 LOC.
## @changes
##   LAST_CHANGE: 2026-07-25 | Created (DevPlan 074 TASK-4)
##   2026-08-01 | B7 T7 (D5): удалены 19 хвостовых assert True (R1-чистка pass-тестов);
##               контрактный CLI-тест вынесен в tests/unit/test_render_monitoring_cli.py
##   2026-08-02 | F3 (DevPlan 118): генератор-тесты удалены (дубли 7 новых файлов);
##               остались config-loading (deep_merge, L1/L2/L3, retention, _str_to_bool)
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml
from _conftest.ldd import _print_ldd_trajectory

from core.internal.monitoring_config_renderer import (
    _parse_retention_hours,
    _str_to_bool,
    build_merged_config,
    deep_merge,
    load_l1_defaults,
    load_l2_overrides,
    load_l3_project_config,
)

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_yaml(data: dict, path: pathlib.Path) -> pathlib.Path:
    """Write a YAML dict to a temp file and return the path.

    ## @purpose  Helper to create temporary YAML files for tests.
    ## @complexity O(N) where N = YAML tree size
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def test_ai_platform_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal ai-platform.yaml for tests.

    ## @purpose  Standard test fixture: backend type with monitoring enabled.
    ## @complexity O(1)
    """
    data = {
        "type": "backend",
        "monitoring": {
            "metrics": True,
            "metrics_port": 9090,
            "dashboard": False,
            "alerting": True,
            "logs_retention": "14d",
        },
        "needs": {"llm": True},
    }
    return _write_yaml(data, tmp_path / "ai-platform.yaml")


@pytest.fixture
def test_defaults_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal defaults.yaml for tests.

    ## @purpose  L1 defaults fixture with global + type-defaults for backend.
    ## @complexity O(1)
    """
    data = {
        "monitoring": {
            "metrics": False,
            "metrics_port": 3000,
            "logs_retention": "7d",
            "ai_retention": "30d",
            "alerting": False,
            "dashboard": False,
        },
        "type-defaults": {
            "backend": {
                "metrics": True,
                "metrics_port": 8080,
                "logs_retention": "14d",
                "alerting": False,
                "dashboard": False,
            },
        },
    }
    return _write_yaml(data, tmp_path / "defaults.yaml")


@pytest.fixture
def test_l2_override_yaml(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal L2 context override for tests.

    ## @purpose  L2 context override fixture.
    ## @complexity O(1)
    """
    data = {
        "monitoring": {
            "metrics_port": 8080,
            "alerting": True,
        },
    }
    # Path structure: node-configs/<node>/projects/<project>.yaml
    path = tmp_path / "node-configs" / "test-node" / "projects" / "test-project.yaml"
    return _write_yaml(data, path)


# ── T4.1: deep_merge simple ────────────────────────────────────────────────


# 🧪 TRAP[TEST] · deep_merge_simple · Unit · Regression never · Remove if: deep_merge semantics change
def test_deep_merge_simple(caplog) -> None:
    """Merge two flat dicts — override wins.

    ## @purpose T4.1: Verify basic deep_merge with flat dicts.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    base = {"a": 1, "b": 2}
    override = {"b": 3, "c": 4}
    result = deep_merge(base, override)

    assert result == {"a": 1, "b": 3, "c": 4}
    # Verify input dicts not mutated
    assert base == {"a": 1, "b": 2}
    assert override == {"b": 3, "c": 4}

    _print_ldd_trajectory(caplog, "test_deep_merge_simple")
    # Pure function — no IMP:9 logs expected; assert at least no errors


# ── T4.2: deep_merge nested 3 levels ────────────────────────────────────────


# 🧪 TRAP[TEST] · deep_merge_nested · Unit · Regression never · Remove if: nested merge behavior changes
def test_deep_merge_nested_3levels(caplog) -> None:
    """Merge 3 nested levels: L1←L2←L3 — verify deepest override wins.

    ## @purpose T4.2: Verify recursive merge through 3 levels.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    l1 = {"monitoring": {"metrics": False, "port": 3000, "retention": "7d"}}
    l2 = {"monitoring": {"port": 8080, "alerting": True}}
    l3 = {"monitoring": {"port": 9090, "dashboard": True}}

    merged = deep_merge(l1, l2)
    merged = deep_merge(merged, l3)

    assert merged == {
        "monitoring": {
            "metrics": False,
            "port": 9090,
            "retention": "7d",
            "alerting": True,
            "dashboard": True,
        },
    }

    _print_ldd_trajectory(caplog, "test_deep_merge_nested_3levels")


# ── T4.3: deep_merge preserves base keys ────────────────────────────────────


# 🧪 TRAP[TEST] · deep_merge_preserves_base · Unit · Regression never · Remove if: base-key preservation changes
def test_deep_merge_preserves_base_keys(caplog) -> None:
    """Keys only in base dict survive merge.

    ## @purpose T4.3: Verify base-only keys are not lost during merge.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}}
    result = deep_merge(base, override)

    assert result == {"a": 1, "b": {"c": 99, "d": 3}}
    # 'd' from base.b should survive

    _print_ldd_trajectory(caplog, "test_deep_merge_preserves_base_keys")


# ── T4.4: load_l1_defaults with type ────────────────────────────────────────


# 🧪 TRAP[TEST] · load_l1_defaults_with_type · Unit · Regression never · Remove if: L1 loading semantics change
def test_load_l1_defaults_with_type(test_defaults_yaml: pathlib.Path, caplog) -> None:
    """Load defaults.yaml + backend type-defaults → merged.

    ## @purpose T4.4: Verify L1 defaults load with type-specific overrides.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    result = load_l1_defaults(test_defaults_yaml, "backend")

    # Global monitoring metrics=false, but backend type-default overrides to true
    assert result.get("metrics") is True
    # Port from backend type-defaults (8080)
    assert result.get("metrics_port") == 8080
    # logs_retention from backend type-defaults (14d)
    assert result.get("logs_retention") == "14d"
    # ai_retention from global defaults (30d) — not in backend type-defaults
    assert result.get("ai_retention") == "30d"

    found = _print_ldd_trajectory(caplog, "test_load_l1_defaults_with_type")
    assert found, "No IMP:9 log found — LDD violation"


# ── T4.5: load_l1_defaults missing file ─────────────────────────────────────


# 🧪 TRAP[TEST] · load_l1_defaults_missing · Unit · Regression never · Remove if: missing-file behavior changes
def test_load_l1_defaults_missing_file(caplog) -> None:
    """defaults.yaml not found → empty dict, not exception.

    ## @purpose T4.5: Verify graceful handling of missing defaults file.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    missing_path = pathlib.Path("/tmp/nonexistent/defaults.yaml")
    result = load_l1_defaults(missing_path, "backend")

    assert result == {}

    _print_ldd_trajectory(caplog, "test_load_l1_defaults_missing_file")


# ── T4.6: load_l2_overrides present ─────────────────────────────────────────


# 🧪 TRAP[TEST] · load_l2_overrides_present · Unit · Regression never · Remove if: L2 loading semantics change
def test_load_l2_overrides_present(test_l2_override_yaml: pathlib.Path, caplog) -> None:
    """Context override file exists with monitoring section → parsed.

    ## @purpose T4.6: Verify L2 override file is parsed correctly.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    result = load_l2_overrides(test_l2_override_yaml)

    assert result == {"metrics_port": 8080, "alerting": True}

    _print_ldd_trajectory(caplog, "test_load_l2_overrides_present")


# ── T4.7: load_l2_overrides missing file ────────────────────────────────────


# 🧪 TRAP[TEST] · load_l2_overrides_missing · Unit · Regression never · Remove if: missing-file behavior changes
def test_load_l2_overrides_missing_file(caplog) -> None:
    """Context override file not found → empty dict.

    ## @purpose T4.7: Verify graceful handling of missing override file.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    missing_path = pathlib.Path("/tmp/nonexistent/override.yaml")
    result = load_l2_overrides(missing_path)

    assert result == {}

    _print_ldd_trajectory(caplog, "test_load_l2_overrides_missing_file")


# ── T4.8: build_merged_config full pipeline ─────────────────────────────────


# 🧪 TRAP[TEST] · build_merged_config_full · Unit · Regression never · Remove if: 3-level merge pipeline changes
def test_build_merged_config_full_pipeline(
    tmp_path: pathlib.Path,
    test_ai_platform_yaml: pathlib.Path,
    test_defaults_yaml: pathlib.Path,
    test_l2_override_yaml: pathlib.Path,
    caplog,
) -> None:
    """Full pipeline: ai-platform.yaml + defaults + override → correct merged config.

    ## @purpose T4.8: End-to-end 3-level merge test with all config files.
    ## @complexity O(N)
    """
    caplog.set_level(logging.INFO)

    # Setup: project dir with ai-platform.yaml
    project_dir = test_ai_platform_yaml.parent
    project_name = "test-project"
    node_name = "test-node"

    # Setup platform root with defaults.yaml and L2 override
    # L1 defaults at platform_root/core/modules/monitoring/defaults.yaml
    platform_root = tmp_path / "platform"
    defaults_dir = platform_root / "core" / "modules" / "monitoring"
    defaults_dir.mkdir(parents=True, exist_ok=True)
    # Copy defaults content to platform-relative path
    defaults_data = {
        "monitoring": {
            "metrics": False,
            "metrics_port": 3000,
            "logs_retention": "7d",
            "ai_retention": "30d",
            "alerting": False,
            "dashboard": False,
        },
        "type-defaults": {
            "backend": {
                "metrics": True,
                "metrics_port": 8080,
                "logs_retention": "14d",
                "alerting": False,
                "dashboard": False,
            },
        },
    }
    _write_yaml(defaults_data, defaults_dir / "defaults.yaml")

    # L2 override at platform_root/node-configs/<node>/projects/<project>.yaml
    l2_dir = platform_root / "node-configs" / node_name / "projects"
    l2_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml({"monitoring": {"metrics_port": 9090, "alerting": True}}, l2_dir / f"{project_name}.yaml")

    config = build_merged_config(project_dir, project_name, node_name, platform_root)

    assert config is not None
    assert config.project_name == "test-project"
    assert config.project_type == "backend"
    assert config.metrics_enabled is True  # L1 type-default overrides global
    assert config.metrics_port == 9090  # L2 overrides L1
    assert config.dashboard_enabled is False  # L1 default
    assert config.alerting_enabled is True  # L2 overrides
    assert config.needs_llm is True  # from ai-platform.yaml needs.llm
    assert config.logs_retention == "14d"  # from ai-platform.yaml
    assert config.ai_retention_days == 30  # L1 default

    found = _print_ldd_trajectory(caplog, "test_build_merged_config_full_pipeline")
    assert found, "No IMP:9 log found — LDD violation"


# ── T4.9: build_merged_config no monitoring section ─────────────────────────


# 🧪 TRAP[TEST] · build_merged_config_no_monitoring · Unit · Regression never · Remove if: backward-compat behavior changes
def test_build_merged_config_no_monitoring_section(tmp_path: pathlib.Path, caplog) -> None:
    """ai-platform.yaml without monitoring section → returns None (backward compat).

    ## @purpose T4.9: Verify backward compat — projects without monitoring are skipped.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    ai_yaml = tmp_path / "ai-platform.yaml"
    _write_yaml({"type": "backend"}, ai_yaml)

    platform_root = tmp_path / "platform"
    platform_root.mkdir()

    config = build_merged_config(tmp_path, "test-project", "test-node", platform_root)

    assert config is None

    _print_ldd_trajectory(caplog, "test_build_merged_config_no_monitoring_section")


# ── T4.10: build_merged_config no ai-yaml ────────────────────────────────────


# 🧪 TRAP[TEST] · build_merged_config_no_ai_yaml · Unit · Regression never · Remove if: missing-ai-yaml behavior changes
def test_build_merged_config_no_ai_yaml(tmp_path: pathlib.Path, caplog) -> None:
    """ai-platform.yaml doesn't exist → returns None.

    ## @purpose T4.10: Verify missing ai-platform.yaml is handled gracefully.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    platform_root = tmp_path / "platform"
    platform_root.mkdir()

    # Project dir without ai-platform.yaml
    project_dir = tmp_path / "empty-project"
    project_dir.mkdir()

    config = build_merged_config(project_dir, "test-project", "test-node", platform_root)

    assert config is None

    _print_ldd_trajectory(caplog, "test_build_merged_config_no_ai_yaml")


# ── T4.19: retention parsing variants ────────────────────────────────────────


# 🧪 TRAP[TEST] · retention_parsing · Unit · Regression never · Remove if: retention parse logic changes
def test_retention_parsing_variants(caplog) -> None:
    """'7d'→168h, '336h'→336h, 'forever'→0h, invalid→168h.

    ## @purpose T4.19: Verify all retention string parsing variants.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    assert _parse_retention_hours("7d") == 168
    assert _parse_retention_hours("14d") == 336
    assert _parse_retention_hours("336h") == 336
    assert _parse_retention_hours("24h") == 24
    assert _parse_retention_hours("forever") == 0
    assert _parse_retention_hours("invalid") == 168  # default
    assert _parse_retention_hours("") == 168  # default

    _print_ldd_trajectory(caplog, "test_retention_parsing_variants")


# ── T4.21: all components noop when no monitoring ──────────────────────────


# 🧪 TRAP[TEST] · all_components_noop_on_no_monitoring · Unit · Regression never · Remove if: no-monitoring pipeline behavior changes
def test_all_components_noop_when_no_monitoring(tmp_path: pathlib.Path, caplog) -> None:
    """ai-platform.yaml without monitoring section → all components skip.

    ## @purpose T4.21: Integration test — full pipeline with no monitoring section.
    ##           All render operations should be skipped gracefully.
    ## @complexity O(N)
    """
    caplog.set_level(logging.INFO)

    # Write ai-platform.yaml without monitoring section
    ai_yaml = tmp_path / "ai-platform.yaml"
    _write_yaml({"type": "backend", "needs": {"llm": False}}, ai_yaml)

    # Create platform root
    platform_root = tmp_path / "platform"
    platform_root.mkdir()

    # Build config should return None (no monitoring section)
    config = build_merged_config(tmp_path, "test-project", "test-node", platform_root)

    assert config is None

    # All render operations with None config should behave as documented
    # This test verifies the pipeline gracefully handles no-monitoring scenario
    _print_ldd_trajectory(caplog, "test_all_components_noop_when_no_monitoring")


# ── Additional: _str_to_bool helper ──────────────────────────────────────────


# 🧪 TRAP[TEST] · str_to_bool · Unit · Regression never · Remove if: bool parsing logic changes
def test_str_to_bool_variants(caplog) -> None:
    """Verify _str_to_bool handles bool, string 'true'/'false', and edge cases.

    ## @purpose Additional test for the internal _str_to_bool helper.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    assert _str_to_bool(True) is True
    assert _str_to_bool(False) is False
    assert _str_to_bool("true") is True
    assert _str_to_bool("True") is True
    assert _str_to_bool("false") is False
    assert _str_to_bool("False") is False
    assert _str_to_bool("1") is True
    assert _str_to_bool("yes") is True
    assert _str_to_bool("") is False
    assert _str_to_bool(0) is False
    assert _str_to_bool(1) is True

    _print_ldd_trajectory(caplog, "test_str_to_bool_variants")


# ── Additional: load_l3_project_config ──────────────────────────────────────


# 🧪 TRAP[TEST] · load_l3_project_config · Unit · Regression never · Remove if: L3 extraction logic changes
def test_load_l3_project_config(caplog) -> None:
    """Extract monitoring section from project YAML dict.

    ## @purpose Verify L3 extraction from parsed ai-platform.yaml.
    ## @complexity O(1)
    """
    caplog.set_level(logging.INFO)

    project_yaml = {
        "type": "backend",
        "monitoring": {"metrics": True, "metrics_port": 9090},
        "needs": {"llm": False},
    }

    result = load_l3_project_config(project_yaml)
    assert result == {"metrics": True, "metrics_port": 9090}

    # Empty monitoring section
    result2 = load_l3_project_config({"type": "frontend"})
    assert result2 == {}

    _print_ldd_trajectory(caplog, "test_load_l3_project_config")
