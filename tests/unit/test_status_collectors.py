#!/usr/bin/env python3
# GREP_SUMMARY: test-status-collectors load-node-yaml load-status-metrics vhosts modules curl-vhost curl-platform-service check-container staleness get-all-checks
# STRUCTURE: ┌10 test functions┐ → ◇ load_node_yaml (1) → ◇ _load_status_metrics (2) → ◇ get_vhosts (2) → ◇ _check_container (3) → ◇ _curl_vhost (1) → ◇ _compute_staleness (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/status-page/collectors.py — pure data collectors
#            extracted from app.py (DevPlan 117 G T55). Characterization-based: all tests
##           reproduce pre-refactor behavior of the identical code in app.py.
## @scope    No Docker, no HTTP — subprocess/threading mocked; tmp_path for files.
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T55 §TEST_SPEC — collectors direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T55 — created
# endregion MODULE_CONTRACT

import json
import sys
import time
from pathlib import Path
from unittest import mock

# status-page/ dir has a hyphen — not a valid package name. Import via sys.path
# (same pattern as tests/test_status_page.py).
_STATUS_PAGE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page"
if str(_STATUS_PAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_STATUS_PAGE_DIR))

from collectors import (
    _check_container,
    _curl_vhost,
    get_all_checks,
    get_modules,
    get_vhosts,
    load_node_yaml,
)
from collectors import (
    compute_staleness as _compute_staleness,
)
from collectors import (
    load_status_metrics as _load_status_metrics,
)

# ══════════════════════════════════════════════════════════════════════
# TESTS: load_node_yaml
# ══════════════════════════════════════════════════════════════════════


class TestLoadNodeYaml:
    """Tests for load_node_yaml()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid node.yaml
    # · Expect: dict returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: load_node_yaml logic changes
    def test_load_node_yaml_reads_file(self, tmp_path: Path) -> None:
        """Valid node.yaml → dict."""
        p = tmp_path / "node.yaml"
        p.write_text("projects:\n  - name: app\n    domain: app.example.com\n    expose: true\n", encoding="utf-8")

        data = load_node_yaml(str(p))

        assert data["projects"][0]["name"] == "app"

    # 🧪 TRAP[TEST] · Regression · Scenario: missing file
    # · Expect: {} (graceful)
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: load_node_yaml logic changes
    def test_load_node_yaml_missing_file(self, tmp_path: Path, capsys) -> None:
        """Missing file → {} (no raise)."""
        data = load_node_yaml(str(tmp_path / "missing.yaml"))

        assert data == {}


# ══════════════════════════════════════════════════════════════════════
# TESTS: _load_status_metrics
# ══════════════════════════════════════════════════════════════════════


class TestLoadStatusMetrics:
    """Tests for _load_status_metrics()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid v2 metrics file
    # · Expect: full data returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_valid(self, tmp_path: Path) -> None:
        """Valid v2 metrics → data dict."""
        p = tmp_path / "status-metrics.json"
        p.write_text(json.dumps({"schema_version": 2, "containers": []}), encoding="utf-8")

        data = _load_status_metrics(str(p))

        assert data["schema_version"] == 2

    # 🧪 TRAP[TEST] · Regression · Scenario: path is a directory (P1)
    # · Expect: fallback structure with errors[]
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_dir_fallback(self, tmp_path: Path) -> None:
        """Directory path → fallback with errors."""
        data = _load_status_metrics(str(tmp_path))

        assert data["errors"]
        assert data["containers"] == []

    # 🧪 TRAP[TEST] · Regression · Scenario: unreadable/corrupt JSON
    # · Expect: fallback structure
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: metrics loading logic changes
    def test_load_metrics_corrupt(self, tmp_path: Path) -> None:
        """Corrupt JSON → fallback."""
        p = tmp_path / "status-metrics.json"
        p.write_text("{not json", encoding="utf-8")

        data = _load_status_metrics(str(p))

        assert data["errors"] == ["Failed to load status-metrics.json"]


# ══════════════════════════════════════════════════════════════════════
# TESTS: get_vhosts / get_modules
# ══════════════════════════════════════════════════════════════════════


class TestVhostsAndModules:
    """Tests for get_vhosts() and get_modules()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: expose:true only
    # · Expect: only exposed projects with domain returned
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: get_vhosts logic changes
    def test_get_vhosts_exposed_only(self) -> None:
        """Only expose:true projects with domain → vhosts."""
        node_data = {
            "projects": [
                {"name": "a", "domain": "a.example.com", "expose": True},
                {"name": "b", "domain": "b.example.com", "expose": False},
                {"name": "c", "expose": True},  # no domain → skipped
            ]
        }

        vhosts = get_vhosts(node_data)

        assert len(vhosts) == 1
        assert vhosts[0]["domain"] == "a.example.com"

    def test_get_vhosts_empty(self) -> None:
        """No projects → []."""
        assert get_vhosts({}) == []

    def test_get_modules(self) -> None:
        """get_modules returns module list."""
        assert get_modules({"modules": ["nginx", "redis"]}) == ["nginx", "redis"]


# ══════════════════════════════════════════════════════════════════════
# TESTS: _check_container
# ══════════════════════════════════════════════════════════════════════


class TestCheckContainer:
    """Tests for _check_container()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: running + healthy
    # · Expect: PASS
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_running_healthy(self) -> None:
        """Running + healthy → PASS."""
        result = _check_container({"name": "nginx", "running": True, "healthy": True})

        assert result is not None
        assert result["status"] == "PASS"

    # 🧪 TRAP[TEST] · Regression · Scenario: exited with code 0 (oneshot)
    # · Expect: PASS
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_exited_zero_oneshot(self) -> None:
        """Exited(0) → PASS (oneshot completed)."""
        result = _check_container({"name": "backup", "running": False, "healthy": False, "exit_code": 0})

        assert result is not None
        assert result["status"] == "PASS"

    # 🧪 TRAP[TEST] · Regression · Scenario: exited non-zero + status_line parse
    # · Expect: FAIL
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: container check logic changes
    def test_check_exited_nonzero_status_line(self) -> None:
        """Exited(1) parsed from status_line → FAIL."""
        result = _check_container(
            {"name": "svc", "running": False, "healthy": False, "exit_code": None, "status_line": "Exited (1)"}
        )

        assert result is not None
        assert result["status"] == "FAIL"
        assert result["exit_code"] == 1

    # 🧪 TRAP[TEST] · Regression · Scenario: self-recursion guard
    # · Expect: None (status-page excluded)
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: anti-recursion logic changes
    def test_check_status_page_excluded(self) -> None:
        """status-page container → None (anti-recursion)."""
        assert _check_container({"name": "status-page", "running": True, "healthy": True}) is None


# ══════════════════════════════════════════════════════════════════════
# TESTS: _curl_vhost / _compute_staleness
# ══════════════════════════════════════════════════════════════════════


class TestCurlAndStaleness:
    """Tests for _curl_vhost() and _compute_staleness()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: curl returns HTTP 200
    # · Expect: PASS result
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: curl vhost logic changes
    def test_curl_vhost_success(self) -> None:
        """HTTP 200 → PASS."""
        with mock.patch(
            "collectors.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout="200", stderr=""),
        ):
            result = _curl_vhost("app.example.com")

        assert result["status"] == "PASS"
        assert result["http_code"] == 200

    # 🧪 TRAP[TEST] · Regression · Scenario: curl times out
    # · Expect: FAIL with timeout error
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: curl vhost timeout handling changes
    def test_curl_vhost_timeout(self) -> None:
        """TimeoutExpired → FAIL."""
        with mock.patch(
            "collectors.subprocess.run",
            side_effect=__import__("subprocess").TimeoutExpired("curl", 5),
        ):
            result = _curl_vhost("app.example.com")

        assert result["status"] == "FAIL"
        assert "timeout" in result["error"]

    # 🧪 TRAP[TEST] · Regression · Scenario: fresh generated_at
    # · Expect: None
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: staleness logic changes
    def test_staleness_fresh(self) -> None:
        """Fresh timestamp → None."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        assert _compute_staleness(now) is None

    def test_staleness_none(self) -> None:
        """None timestamp → None."""
        assert _compute_staleness(None) is None

    # 🧪 TRAP[TEST] · Regression · Scenario: old generated_at
    # · Expect: "Xm Ys" description
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: staleness logic changes
    def test_staleness_old(self) -> None:
        """10-min-old timestamp → staleness description."""
        import datetime

        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
        result = _compute_staleness(old)

        assert result is not None
        assert "m" in result


# ══════════════════════════════════════════════════════════════════════
# TESTS: get_all_checks (integration)
# ══════════════════════════════════════════════════════════════════════


class TestGetAllChecks:
    """Tests for get_all_checks()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: healthy container + no vhosts
    # · Expect: PASS aggregate
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: get_all_checks logic changes
    def test_get_all_checks_structure(self, tmp_path: Path) -> None:
        """get_all_checks returns aggregate keys + PASS for healthy container."""
        node_yaml = tmp_path / "node.yaml"
        node_yaml.write_text("projects: []\nmodules: []\n", encoding="utf-8")
        metrics = tmp_path / "status-metrics.json"
        metrics.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "containers": [{"name": "nginx", "running": True, "healthy": True}],
                }
            ),
            encoding="utf-8",
        )

        result = get_all_checks(str(node_yaml), str(metrics), [], per_check_timeout=2, total_timeout=10)

        assert result["status"] == "PASS"
        assert "checks" in result
        assert "metrics" in result
        assert result["checks"][0]["target"] == "nginx"
