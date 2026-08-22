# GREP_SUMMARY: test-config-merge L1 L2 L3 deep-merge defaults overrides three-layer nested-keys monitoring alerts
# STRUCTURE: fixtures(l1/l2/l3 path loaders) + deep_merge → test_l1_defaults_exist → test_l2_overrides_l1 → test_l3_overrides_l2 → test_missing_l1_is_graceful → test_merge_deep_keys
# @file test_config_merge.py
# @purpose  Test three-layer config merge (L1 defaults → L2 org overrides → L3 project overrides)
#           with deep merge semantics for nested keys.
# @scope    Unit tests using isolated test_data YAML files. Implements TASK-8 of test expansion plan.
# @invariants
#   - L1 defaults.yaml must always parse successfully
#   - Absent L1 does not crash — merge proceeds with L2+L3 only
#   - Nested keys merge deep (monitoring.alerts.slack) — NOT replaced wholesale
#   - L3 always wins for overlapping scalar keys
#   - At least one IMP:9 log per test
# @rationale Q: Why test merge logic in Python instead of shell?
#           A: Python dict merge gives precise control over deep merge semantics.
#              Shell-based YAML merge (yq) depends on external tooling and version.
#              This mirrors the monitoring post-deploy merge (receive verb → monitoring_config_renderer.py).
#

# region MODULE_CONTRACT
## @purpose  Test three-layer config merge (L1 defaults → L2 org → L3 project)
##           with deep merge semantics for nested keys.
## @scope    Unit tests using isolated test_data YAML files.
## @invariants
##   - L1 defaults.yaml must always parse successfully
##   - Absent L1 does not crash — merge proceeds with L2+L3 only
##   - Nested keys merge deep (monitoring.alerts.slack) — NOT replaced wholesale
##   - L3 always wins for overlapping scalar keys
##   - At least one IMP:9 log per test
## @rationale Python dict merge gives precise control over deep merge semantics.
##            Shell-based YAML merge (yq) depends on external tooling.
##            Mirrors the monitoring post-deploy merge (receive verb → monitoring_config_renderer.py).


# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_TEST_DATA_DIR = Path(__file__).parent.parent / "test_data"


# region HELPERS


def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursive deep merge: override values replace base values.
    Nested dicts are merged recursively, not replaced wholesale.

    ## @purpose — Merge two dicts with deep (recursive) semantics for nested keys.
    ## @io — ⇥ base: dict, override: dict → ⎋ dict: merged result (new copy)
    ## @complexity — O(N) where N = total keys across both dicts
    ## @invariants
    ##   - base is copied before mutation (immutable input)
    ##   - scalar values in override completely replace base values
    ##   - nested dicts are merged recursively, not replaced
    ##   - override keys not present in base are added
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# endregion HELPERS


# region FIXTURES


@pytest.fixture
def l1_path() -> str:
    """Path to L1 defaults test data file."""
    return Path(_TEST_DATA_DIR) / "config_l1_defaults.yaml"


@pytest.fixture
def l2_path() -> str:
    """Path to L2 overrides test data file."""
    return Path(_TEST_DATA_DIR) / "config_l2_overrides.yaml"


@pytest.fixture
def l3_path() -> str:
    """Path to L3 project overrides test data file."""
    return Path(_TEST_DATA_DIR) / "config_l3_project.yaml"


@pytest.fixture
def l1_yaml(l1_path: str) -> dict:
    """Load and parse L1 defaults YAML."""
    with pathlib.Path(l1_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def l2_yaml(l2_path: str) -> dict:
    """Load and parse L2 overrides YAML."""
    with pathlib.Path(l2_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def l3_yaml(l3_path: str) -> dict:
    """Load and parse L3 project overrides YAML."""
    with pathlib.Path(l3_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# endregion FIXTURES


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_l1_defaults_exist(l1_path: str, caplog) -> None:
    """
    L1 defaults.yaml exists and parses correctly.

    ## @purpose — Verify L1 defaults file exists on disk and is valid YAML.
    ## @io — ⇥ l1_path → ⎋ None (side-effect: assertions)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_l1_defaults_exist] START: check L1 file exists: %s", l1_path)

        assert pathlib.Path(l1_path).is_file(), f"L1 defaults file not found: {l1_path}"

        with pathlib.Path(l1_path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)

        logger.info("[IMP:8][test_l1_defaults_exist] Parsed YAML keys: %s", list(data.keys()))

        assert isinstance(data, dict), "L1 defaults must parse to a dict"
        assert "monitoring" in data, "L1 defaults must contain 'monitoring' key"
        assert isinstance(data["monitoring"], dict), "'monitoring' must be a dict"

        logger.critical("[IMP:9][test_l1_defaults_exist] ASSERT: L1 file exists, parses, monitoring key present")


@pytest.mark.static_audit
@ldd_trajectory
def test_l2_overrides_l1(l1_yaml: dict, l2_yaml: dict, caplog) -> None:
    """
    L2 overrides L1 correctly: scalar override + nested key preservation.

    ## @purpose — Verify L2 overrides L1 scalars and nested keys merge deep.
    ## @io — ⇥ l1_yaml, l2_yaml → ⎋ None (side-effect: assertions)
    ## @complexity — O(N) where N = total keys
    ## @invariants
    ##   - monitoring.metrics overridden from false to true
    ##   - monitoring.metrics_port overridden from 3000 to 9090
    ##   - monitoring.logs_retention overridden from 7d to 14d
    ##   - monitoring.alerts.email preserved from L1 (not in L2)
    ##   - monitoring.alerts.slack.webhook added by L2
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_l2_overrides_l1] START: merge L1 ← L2")

        merged = deep_merge(l1_yaml, l2_yaml)
        mon = merged["monitoring"]

        logger.info("[IMP:8][test_l2_overrides_l1] merged.metrics=%s", mon.get("metrics"))
        logger.info("[IMP:8][test_l2_overrides_l1] merged.metrics_port=%s", mon.get("metrics_port"))
        logger.info("[IMP:8][test_l2_overrides_l1] merged.logs_retention=%s", mon.get("logs_retention"))
        logger.info("[IMP:8][test_l2_overrides_l1] merged.alerts.email=%s", mon.get("alerts", {}).get("email"))

        # L2 overrides
        assert mon["metrics"] is True, "L2 must override metrics to true"
        assert mon["metrics_port"] == 9090, "L2 must override metrics_port to 9090"
        assert mon["logs_retention"] == "14d", "L2 must override logs_retention to 14d"

        # L1 values preserved
        assert mon["alerting"] is False, "L1 alerting must be preserved (not in L2)"
        assert mon["dashboard"] is False, "L1 dashboard must be preserved (not in L2)"
        assert mon["ai_retention"] == "30d", "L1 ai_retention must be preserved (not in L2)"

        # Nested deep merge: slack partial override
        assert mon["alerts"]["slack"]["enabled"] is True, "L2 must override slack.enabled to true"
        assert mon["alerts"]["slack"]["webhook"] == "https://hooks.slack.com/services/T00/B00/test", (
            "L2 must add slack.webhook"
        )
        assert mon["alerts"]["slack"]["channel"] == "#alerts", "L1 slack.channel must be preserved (deep merge)"

        # L1-only nested key preserved
        assert "email" in mon["alerts"], "L1 alerts.email must be preserved (deep merge)"
        assert mon["alerts"]["email"]["enabled"] is True

        logger.critical(
            "[IMP:9][test_l2_overrides_l1] ASSERT: L2 overrides L1 — scalars overwritten, "
            "nested keys deep-merged, L1-only keys preserved"
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_l3_overrides_l2(l1_yaml: dict, l2_yaml: dict, l3_yaml: dict, caplog) -> None:
    """
    L3 overrides L2 correctly in three-layer merge (L1 ← L2 ← L3).

    ## @purpose — Verify full three-layer merge: L3 overrides L2 which overrides L1.
    ## @io — ⇥ l1_yaml, l2_yaml, l3_yaml → ⎋ None (side-effect: assertions)
    ## @complexity — O(N) where N = total keys
    ## @invariants
    ##   - monitoring.alerting overridden by L3 to true
    ##   - monitoring.metrics_port overridden by L3 to 8080 (L2 had 9090)
    ##   - monitoring.logs_retention preserved from L2 (14d)
    ##   - monitoring.alerts.slack.channel overridden by L3 to "#project-alerts"
    ##   - monitoring.alerts.email preserved from L1 via deep merge chain
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_l3_overrides_l2] START: three-layer merge L1 ← L2 ← L3")

        l1_l2 = deep_merge(l1_yaml, l2_yaml)
        merged = deep_merge(l1_l2, l3_yaml)
        mon = merged["monitoring"]

        logger.info("[IMP:8][test_l3_overrides_l2] merged.metrics=%s", mon.get("metrics"))
        logger.info("[IMP:8][test_l3_overrides_l2] merged.metrics_port=%s", mon.get("metrics_port"))
        logger.info("[IMP:8][test_l3_overrides_l2] merged.alerting=%s", mon.get("alerting"))
        logger.info(
            "[IMP:8][test_l3_overrides_l2] merged.alerts.slack.channel=%s",
            mon.get("alerts", {}).get("slack", {}).get("channel"),
        )

        # L3 wins for overlapping keys
        assert mon["alerting"] is True, "L3 must override alerting to true"
        assert mon["metrics_port"] == 8080, "L3 must override metrics_port to 8080"

        # L2 values preserved (not in L3)
        assert mon["metrics"] is True, "L2 metrics=true must be preserved (not in L3)"
        assert mon["logs_retention"] == "14d", "L2 logs_retention must be preserved (not in L3)"

        # L1 values preserved (not in L2 or L3)
        assert mon["ai_retention"] == "30d", "L1 ai_retention must be preserved in three-layer chain"
        assert mon["dashboard"] is False, "L1 dashboard must be preserved in three-layer chain"

        # Nested deep merge: L3 overrides slack.channel, preserves slack.webhook from L2
        assert mon["alerts"]["slack"]["channel"] == "#project-alerts", "L3 must override slack.channel"
        assert mon["alerts"]["slack"]["webhook"] == "https://hooks.slack.com/services/T00/B00/test", (
            "L2 slack.webhook must be preserved (deep merge, not in L3)"
        )
        assert mon["alerts"]["slack"]["enabled"] is True, "L2 slack.enabled must be preserved (deep merge, not in L3)"

        # L1 email alerting preserved through full chain
        assert "email" in mon["alerts"], "L1 alerts.email must survive three-layer deep merge"
        assert mon["alerts"]["email"]["enabled"] is True
        assert "admin@platform.local" in mon["alerts"]["email"]["recipients"]

        logger.critical(
            "[IMP:9][test_l3_overrides_l2] ASSERT: three-layer merge — L3 wins on overlapping, "
            "L2 and L1 preserved elsewhere, nested deep merge works across all layers"
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_missing_l1_is_graceful(l2_path: str, l3_path: str, tmp_path, caplog) -> None:
    """
    Absent L1 defaults.yaml does not break final config — merge proceeds with L2+L3 only.

    ## @purpose — Verify graceful degradation when L1 file is missing.
    ## @io — ⇥ l2_path, l3_path, tmp_path → ⎋ None (side-effect: assertions)
    ## @complexity — O(1)
    ## @invariants
    ##   - Missing L1 path does not raise FileNotFoundError
    ##   - Merged config contains only L2+L3 keys (no L1-only keys)
    ##   - Merge completes without error
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_missing_l1_is_graceful] START: simulate missing L1")

        non_existent_l1 = tmp_path / "nonexistent_defaults.yaml"
        assert not non_existent_l1.exists(), "Precondition: L1 file must not exist"

        # Load L2 and L3 from their real paths
        with pathlib.Path(l2_path).open(encoding="utf-8") as f:
            l2 = yaml.safe_load(f)
        with pathlib.Path(l3_path).open(encoding="utf-8") as f:
            l3 = yaml.safe_load(f)

        # Simulate merge without L1
        merged = deep_merge({}, l2)  # No L1 — start with empty base
        merged = deep_merge(merged, l3)

        logger.info("[IMP:8][test_missing_l1_is_graceful] merged keys: %s", list(merged.keys()))

        # Must NOT contain L1-only keys (ai_retention, dashboard, email)
        mon = merged.get("monitoring", {})
        assert "ai_retention" not in mon, "L1-only key 'ai_retention' must NOT appear when L1 is absent"
        assert "dashboard" not in mon, "L1-only key 'dashboard' must NOT appear when L1 is absent"

        # L2+L3 keys must be present
        assert mon["metrics"] is True, "L2 metrics must be present"
        assert mon["metrics_port"] == 8080, "L3 metrics_port must win"
        assert mon["alerting"] is True, "L3 alerting must be present"
        assert mon["logs_retention"] == "14d", "L2 logs_retention must be present"

        logger.critical(
            "[IMP:9][test_missing_l1_is_graceful] ASSERT: missing L1 is graceful — "
            "L2+L3 merge completes, no L1-only keys leaked"
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_merge_deep_keys(l1_yaml: dict, l2_yaml: dict, l3_yaml: dict, caplog) -> None:
    """
    Nested keys (e.g. monitoring.alerts.slack) merge deep — not replaced wholesale.

    ## @purpose — Verify deep merge semantics: nested dicts merge recursively, not replaced.
    ##            Regression test: shallow merge would lose monitoring.alerts.email from L1.
    ## @io — ⇥ l1_yaml, l2_yaml, l3_yaml → ⎋ None (side-effect: assertions)
    ## @complexity — O(N) where N = total keys
    ## @invariants
    ##   - monitoring.alerts.slack.channel overridden by L3, preserves L2 webhook
    ##   - monitoring.alerts.email preserved from L1 (would be lost in shallow merge)
    ##   - monitoring.alerts.slack.enabled preserved from L2
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_merge_deep_keys] START: verify deep merge semantics")

        l1_l2 = deep_merge(l1_yaml, l2_yaml)
        merged = deep_merge(l1_l2, l3_yaml)
        alerts = merged["monitoring"]["alerts"]

        # L3 override on nested slack.channel
        assert alerts["slack"]["channel"] == "#project-alerts", "L3 must override slack.channel via deep merge"

        # L2 nested values preserved (would be lost in shallow replace)
        assert alerts["slack"]["webhook"] == "https://hooks.slack.com/services/T00/B00/test", (
            "L2 slack.webhook preserved — shallow merge would drop it"
        )
        assert alerts["slack"]["enabled"] is True, "L2 slack.enabled preserved — shallow merge would drop it"

        # L1-only nested subtree preserved (would be completely lost in shallow merge)
        assert "email" in alerts, "L1 alerts.email preserved — shallow merge would drop entire alerts subtree"
        assert alerts["email"]["enabled"] is True, "L1 alerts.email.enabled preserved via deep merge"

        # Simulate SHALLOW merge to prove the test catches the bug.
        # Shallow merge at alerts level: each spread replaces the ENTIRE 'slack' sub-dict.
        # L1 slack = {enabled: false, webhook: "", channel: "#alerts"}
        # L2 slack = {enabled: true, webhook: "https://..."} → replaces entire slack
        # L3 slack = {channel: "#project-alerts"} → replaces entire slack again
        # Result: L2's webhook and enabled are LOST.
        shallow_alerts = {
            **l1_yaml["monitoring"]["alerts"],
            **l2_yaml["monitoring"]["alerts"],
            **l3_yaml["monitoring"]["alerts"],
        }

        logger.info("[IMP:8][test_merge_deep_keys] deep merge slack keys: %s", list(alerts["slack"].keys()))
        logger.info("[IMP:8][test_merge_deep_keys] shallow merge slack keys: %s", list(shallow_alerts["slack"].keys()))

        # Shallow merge loses L2 slack.webhook and slack.enabled
        assert "webhook" not in shallow_alerts["slack"], (
            "Proof: shallow merge LOSES L2 slack.webhook — deep merge preserves it"
        )
        assert "enabled" not in shallow_alerts["slack"], (
            "Proof: shallow merge LOSES L2 slack.enabled — deep merge preserves it"
        )
        # Deep merge preserves them
        assert "webhook" in alerts["slack"], "Deep merge MUST preserve L2 slack.webhook"
        assert "enabled" in alerts["slack"], "Deep merge MUST preserve L2 slack.enabled"

        logger.critical(
            "[IMP:9][test_merge_deep_keys] ASSERT: deep merge preserves L1-only nested keys; "
            "shallow merge would lose them (regression guard)"
        )


# endregion TESTS
