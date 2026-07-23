# GREP_SUMMARY: test-status-page-app template-rendering jinja2 _render_html _enrich_containers _curl_platform_service platform-services ssl-banner
# STRUCTURE: ▶ test_status_page_template_rendering → mock status-metrics.json → _render_html → assert HTML sections
#            ▶ test_platform_service_healthchecks → mock subprocess.run curl → assert PASS/FAIL
# @file test_status_page_app.py
# @purpose  Unit tests for status-page app.py — template rendering, container enrichment, platform service checks
# @scope    Unit-level: tests call app.py functions directly. No HTTP server — no Docker required.
# @invariants
#   - All tests use tmp_path fixture (Zero Hardcode Rule)
#   - LDD trajectory (IMP:7-10) printed before every assert
#   - All mocks use unittest.mock — no external dependencies
#   - Module loaded via importlib.util.spec_from_file_location (status-page dir has hyphen, not a valid Python package name)
# @rationale  Testing business logic (enrichment, rendering, healthcheck) directly avoids Docker dependency
#             while validating core behavior. Tests exercise _render_html with mock data.
# region MODULE_CONTRACT
## @purpose  Unit tests for status-page app.py functions
## @scope    Template rendering, container enrichment, platform service healthchecks
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - No HTTP server launched — functions called directly
##   - Module loaded via SourceFileLoader (hyphen in dir name prevents normal import)
## @changes 2026-07-24 | CREATED | D067 — new test suite for status-page enhancements
# endregion MODULE_CONTRACT

import importlib.util
import json
import os
from pathlib import Path
from unittest import mock

import pytest

# ═══════════════════════════════════════════════════════════════════
# MODULE LOADER — status-page dir has hyphen, not a valid Python package
# ═══════════════════════════════════════════════════════════════════

_STATUS_PAGE_MODULE = None


def _get_status_page_module():
    """Load core.modules.status-page.app via SourceFileLoader (hyphen in dir name)."""
    global _STATUS_PAGE_MODULE
    if _STATUS_PAGE_MODULE is not None:
        return _STATUS_PAGE_MODULE

    module_path = os.path.join(os.path.dirname(__file__), "..", "core", "modules", "status-page", "app.py")
    module_path = os.path.abspath(module_path)
    spec = importlib.util.spec_from_file_location("status_page_app", module_path, submodule_search_locations=[])
    mod = importlib.util.module_from_spec(spec)
    # We need to mock the module-global env-dependent variables BEFORE module exec
    # PLATFORM_DOMAIN is read at module level
    old_platform_domain = os.environ.get("PLATFORM_DOMAIN")
    if "PLATFORM_DOMAIN" not in os.environ:
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

    spec.loader.exec_module(mod)

    if old_platform_domain is None:
        del os.environ["PLATFORM_DOMAIN"]

    _STATUS_PAGE_MODULE = mod
    return _STATUS_PAGE_MODULE


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_status_metrics(tmp_path: Path) -> str:
    """Create a realistic status-metrics.json fixture with projects, containers, certs, host data."""
    metrics = {
        "schema_version": 2,
        "generated_at": "2026-07-24T00:00:00Z",
        "node": "test-node",
        "containers": [
            {
                "name": "nginx",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 2 hours (healthy)",
                "started_at": "2026-07-24T00:00:00.000000000Z",
                "image": "nginx:latest",
                "image_id": "sha256:abc123",
                "memory_usage_bytes": 12582912,
                "memory_limit_bytes": 1073741824,
                "cpu_percent": 0.45,
                "restart_policy": "unless-stopped",
            },
            {
                "name": "postgres",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 3 hours",
                "started_at": "2026-07-23T21:00:00.000000000Z",
                "image": "postgres:16-alpine",
                "image_id": "sha256:def456",
                "memory_usage_bytes": 52428800,
                "memory_limit_bytes": 1073741824,
                "cpu_percent": 1.2,
                "restart_policy": "always",
            },
            {
                "name": "redis",
                "running": True,
                "healthy": False,
                "exit_code": 0,
                "status_line": "Up 1 hour (health: starting)",
                "started_at": "2026-07-24T01:00:00.000000000Z",
                "image": "redis:alpine",
                "image_id": "sha256:ghi789",
                "memory_usage_bytes": 3145728,
                "memory_limit_bytes": 536870912,
                "cpu_percent": 0.1,
                "restart_policy": "unless-stopped",
            },
            {
                "name": "test-app",
                "running": False,
                "healthy": False,
                "exit_code": 137,
                "status_line": "Exited (137) 2 hours ago",
                "started_at": None,
                "image": "test-app:latest",
                "image_id": "sha256:jkl012",
                "memory_usage_bytes": 0,
                "memory_limit_bytes": 0,
                "cpu_percent": 0.0,
                "restart_policy": "no",
            },
            {
                "name": "status-page",
                "running": True,
                "healthy": True,
                "exit_code": 0,
                "status_line": "Up 10 minutes",
                "started_at": "2026-07-24T01:50:00.000000000Z",
                "image": "status-page:latest",
                "image_id": "sha256:mno345",
                "memory_usage_bytes": 8388608,
                "memory_limit_bytes": 536870912,
                "cpu_percent": 0.05,
                "restart_policy": "unless-stopped",
            },
        ],
        "certs": [
            {
                "domains": ["test-app.example.com", "www.test-app.example.com"],
                "issuer": "Fake LE",
                "not_after_iso": "2026-07-29T00:00:00Z",
                "days_remaining": 5,
                "san": ["test-app.example.com", "www.test-app.example.com"],
                "subject": "test-app.example.com",
            },
            {
                "domains": ["internal.example.com"],
                "issuer": "Fake LE",
                "not_after_iso": "2026-10-01T00:00:00Z",
                "days_remaining": 69,
                "san": ["internal.example.com"],
                "subject": "internal.example.com",
            },
            {
                "domains": ["other-app.example.com"],
                "issuer": "ZeroSSL",
                "not_after_iso": "2026-09-15T00:00:00Z",
                "days_remaining": 53,
                "san": ["other-app.example.com"],
                "subject": "other-app.example.com",
            },
        ],
        "projects": [
            {
                "name": "test-app",
                "domain": "test-app.example.com",
                "code_size_bytes": 52428800,
                "docker_image_size_bytes": 150000000,
            },
            {
                "name": "internal-app",
                "domain": "internal.example.com",
                "code_size_bytes": 10485760,
                "docker_image_size_bytes": 45000000,
            },
            {
                "name": "other-app",
                "domain": "other-app.example.com",
                "code_size_bytes": 26214400,
                "docker_image_size_bytes": 80000000,
            },
        ],
        "host": {
            "disk_total_gb": 100.0,
            "disk_free_gb": 30.0,
            "disk_used_percent": 70.0,
            "uptime_seconds": 123456.0,
            "load_1m": 0.5,
            "load_5m": 0.3,
            "load_15m": 0.1,
            "docker_images_size_gb": 2.35,
        },
        "backup": {
            "last_postgres_at": "2026-07-23T22:00:00Z",
            "last_app_data_at": "2026-07-23T21:00:00Z",
            "status": "ok",
        },
        "errors": [],
    }

    path = tmp_path / "status-metrics.json"
    with open(path, "w") as f:
        json.dump(metrics, f)
    return str(path)


# ═══════════════════════════════════════════════════════════════════
# TESTS: Template rendering (TASK-15)
# ═══════════════════════════════════════════════════════════════════


class TestTemplateRendering:
    """Tests for _render_html template rendering with enhanced data."""

    def _build_data(self, metrics_data: dict, platform_service_checks: list | None = None) -> dict:
        """Build a data dict as get_all_checks would produce."""
        return {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 1234,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": platform_service_checks or [],
            "metrics": metrics_data,
        }

    # 🧪 TRAP[TEST] · TASK-15 · Regression: SSL Summary Banner
    # · Scenario: Project with cert expiring in 5 days → banner shows "Earliest cert expires in 5 days"
    # · Last fail: never (new feature)
    # · Remove if: SSL Summary Banner removed from template
    def test_ssl_summary_banner(self, mock_status_metrics, caplog):
        """_render_html includes SSL Summary Banner when cert expires in <30 days."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _render_html = app._render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = self._build_data(metrics_data)
        html = _render_html(data)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # SSL Summary Banner should be present
        # Note: trailing space in class due to Jinja2 conditional ({% if ssl_min_days >= 7 %}ok{% endif %})
        # Check for the HTML element specifically (not just CSS class in <style>)
        assert '<div class="ssl-summary-banner ' in html, "SSL Summary Banner HTML element should be in HTML"
        assert "Earliest cert expires in" in html or "5 day" in html, (
            "SSL Summary Banner should mention earliest cert expiry"
        )

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Platform Services Table
    # · Scenario: Template includes Platform Services table with header
    # · Remove if: Platform Services section removed from template
    def test_platform_services_table(self, mock_status_metrics, caplog):
        """_render_html includes Platform Services Table with all service entries."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _render_html = app._render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = self._build_data(metrics_data)
        html = _render_html(data)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Platform Services Table should be present
        assert "Platform Services" in html, "Platform Services heading should be in HTML"
        assert "Grafana" in html
        assert "Prometheus" in html
        assert "Loki" in html
        assert "Hermes" in html
        assert "Langfuse" in html
        assert "LiteLLM" in html
        assert "internal only" in html

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Containers table columns
    # · Scenario: Template includes Uptime and Restart column headers
    # · Remove if: columns removed from container table
    def test_containers_table_new_columns(self, mock_status_metrics, caplog):
        """_render_html includes Uptime and Restart columns in Containers table."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _render_html = app._render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = self._build_data(metrics_data)
        html = _render_html(data)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Check column headers
        assert "<th>Uptime</th>" in html, "Uptime column header should be present"
        assert "<th>Restart</th>" in html, "Restart column header should be present"
        assert "<th>Domain(s)</th>" in html, "Domain(s) column header should be present"

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Host Resources — Load Average & System Uptime
    # · Scenario: Template includes Load Average and System Uptime rows
    # · Remove if: rows removed from Host Resources table
    def test_host_resources_new_rows(self, mock_status_metrics, caplog):
        """_render_html includes Load Average and System Uptime in Host Resources."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _render_html = app._render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = self._build_data(metrics_data)
        html = _render_html(data)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Check new resource rows
        assert "Load Average" in html, "Load Average row should be present"
        assert "System Uptime" in html, "System Uptime row should be present"
        # Check backup status row
        assert "Backup" in html, "Backup status row should be present"

    # 🧪 TRAP[TEST] · TASK-15 · Regression: SSL banner NOT shown when all certs fresh
    # · Scenario: All certs have >30 days remaining → no SSL banner
    # · Remove if: SSL banner logic changed
    def test_ssl_banner_not_shown_when_fresh(self, caplog):
        """_render_html does NOT show SSL banner when all certs have >30 days remaining."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _render_html = app._render_html

        metrics_data = {
            "generated_at": "2026-07-24T00:00:00Z",
            "containers": [],
            "certs": [
                {
                    "domains": ["test-app.example.com"],
                    "issuer": "Fake LE",
                    "not_after_iso": "2027-01-01T00:00:00Z",
                    "days_remaining": 161,
                    "san": ["test-app.example.com"],
                },
            ],
            "projects": [
                {"name": "test-app", "domain": "test-app.example.com"},
            ],
            "host": {"disk_total_gb": 100, "disk_free_gb": 50, "disk_used_percent": 50.0},
            "backup": {},
            "errors": [],
        }

        data = self._build_data(metrics_data)
        html = _render_html(data)

        print("--- LDD TRAJECTORY (IMP:7-10) ---")
        for record in caplog.records:
            for attr in ["message", "msg"]:
                msg = getattr(record, attr, "")
                if "[IMP:" in str(msg):
                    imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                    if imp_level >= 7:
                        print(msg)
        print("--- END LDD TRAJECTORY ---")

        # Check that no SSL banner div is present in body (CSS class in <style> is expected)
        assert '</style>' in html, "Style tag should be present"  # sanity
        body = html.split('</style>', 1)[1] if '</style>' in html else html
        assert 'ssl-summary-banner' not in body, (
            "SSL Summary Banner element should NOT be in HTML body when all certs are fresh"
        )


# ═══════════════════════════════════════════════════════════════════
# TESTS: Platform service healthchecks (TASK-16)
# ═══════════════════════════════════════════════════════════════════


class TestPlatformServiceHealthchecks:
    """Tests for _curl_platform_service and platform service integration."""

    # 🧪 TRAP[TEST] · TASK-16 · Regression: platform service PASS
    # · Scenario: curl returns HTTP 200 → status "PASS"
    # · Remove if: _curl_platform_service removed
    def test_curl_platform_service_pass(self, caplog):
        """_curl_platform_service returns PASS when curl returns HTTP 200."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _curl_platform_service = app._curl_platform_service

        with mock.patch.object(app, "subprocess") as mock_subprocess:
            mock_run = mock.Mock()
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout="200",
                stderr="",
            )
            mock_subprocess.run = mock_run

            result = _curl_platform_service("grafana:3000", "/api/health")

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert result["status"] == "PASS", f"Expected PASS, got {result['status']}"
            assert result["type"] == "platform_service"
            assert result["target"] == "grafana"
            assert result["http_code"] == 200
            assert result["error"] is None

    # 🧪 TRAP[TEST] · TASK-16 · Regression: platform service FAIL
    # · Scenario: curl returns exit code 7 (connection refused) → status "FAIL"
    # · Remove if: _curl_platform_service removed
    def test_curl_platform_service_fail(self, caplog):
        """_curl_platform_service returns FAIL when curl returns non-zero exit code."""
        caplog.set_level(0)
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        app = _get_status_page_module()
        _curl_platform_service = app._curl_platform_service

        with mock.patch.object(app, "subprocess") as mock_subprocess:
            mock_run = mock.Mock()
            mock_run.return_value = mock.Mock(
                returncode=7,
                stdout="",
                stderr="Failed to connect to host",
            )
            mock_subprocess.run = mock_run

            result = _curl_platform_service("grafana:3000", "/api/health")

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            assert result["status"] == "FAIL", f"Expected FAIL, got {result['status']}"
            assert result["type"] == "platform_service"
            assert "curl exit 7" in result.get("error", ""), (
                f"Expected 'curl exit 7' in error, got '{result.get('error')}'"
            )

    # 🧪 TRAP[TEST] · TASK-16 · Regression: get_all_checks includes platform services
    # · Scenario: mock get_all_checks — platform service checks included in results
    # · Remove if: platform service checks removed from get_all_checks
    def test_get_all_checks_includes_platform_services(self, caplog):
        """get_all_checks includes platform service checks when PLATFORM_SERVICES is populated."""
        caplog.set_level(0)
        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"
        os.environ["STATUS_METRICS_JSON"] = "/nonexistent/metrics.json"

        app = _get_status_page_module()
        get_all_checks = app.get_all_checks

        # Mock dependencies: node.yaml load, metrics load, curl_vhost, curl_platform_service
        with (
            mock.patch.object(app, "load_node_yaml", return_value={"projects": [], "modules": []}),
            mock.patch.object(
                app,
                "_load_status_metrics",
                return_value={
                    "generated_at": None,
                    "containers": [],
                    "certs": [],
                    "projects": [],
                    "host": {},
                    "errors": [],
                },
            ),
            mock.patch.object(app, "_curl_vhost") as mock_curl_vhost,
            mock.patch.object(app, "_curl_platform_service") as mock_curl_platform,
        ):
            mock_curl_vhost.return_value = {"target": "test", "type": "vhost", "status": "PASS", "error": None}
            mock_curl_platform.return_value = {
                "target": "grafana",
                "type": "platform_service",
                "status": "PASS",
                "error": None,
            }

            result = get_all_checks()

            print("--- LDD TRAJECTORY (IMP:7-10) ---")
            for record in caplog.records:
                for attr in ["message", "msg"]:
                    msg = getattr(record, attr, "")
                    if "[IMP:" in str(msg):
                        imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
                        if imp_level >= 7:
                            print(msg)
            print("--- END LDD TRAJECTORY ---")

            # Verify platform services were checked
            platform_checks = [c for c in result.get("checks", []) if c.get("type") == "platform_service"]
            assert len(platform_checks) > 0, "Platform service checks should be included"
            assert any("grafana" in str(c) for c in platform_checks), "Grafana check should be present"
