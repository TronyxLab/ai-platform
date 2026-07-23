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
# @rationale  Testing business logic (enrichment, rendering, healthcheck) directly avoids Docker dependency
#             while validating core behavior. Tests exercise _render_html with mock data.
# region MODULE_CONTRACT
## @purpose  Unit tests for status-page app.py functions
## @scope    Template rendering, container enrichment, platform service healthchecks
## @invariants
##   - tmp_path fixture for all file operations
##   - caplog for LDD trajectory capture
##   - No HTTP server launched — functions called directly
## @changes 2026-07-24 | CREATED | D067 — new test suite for status-page enhancements
# endregion MODULE_CONTRACT

import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest


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
            {"name": "test-app", "domain": "test-app.example.com", "code_size_bytes": 52428800, "docker_image_size_bytes": 150000000},
            {"name": "internal-app", "domain": "internal.example.com", "code_size_bytes": 10485760, "docker_image_size_bytes": 45000000},
            {"name": "other-app", "domain": "other-app.example.com", "code_size_bytes": 26214400, "docker_image_size_bytes": 80000000},
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

    # 🧪 TRAP[TEST] · TASK-15 · Regression: SSL Summary Banner
    # · Scenario: Project with cert expiring in 5 days → banner shows "Earliest cert expires in 5 days"
    # · Last fail: never (new feature)
    # · Remove if: SSL Summary Banner removed from template
    def test_ssl_summary_banner(self, mock_status_metrics, caplog):
        """_render_html includes SSL Summary Banner when cert expires in <30 days."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        # Reimport to pick up env
        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        # We need to mock the metrics JSON loading to use our fixture
        # Strategy: patch _load_status_metrics to return our fixture data
        from core.modules.status_page.app import _render_html

        # Build the data dict as get_all_checks would
        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 1234,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": [
                {"target": "nginx", "type": "container", "status": "PASS", "error": None},
                {"target": "postgres", "type": "container", "status": "PASS", "error": None},
                {"target": "redis", "type": "container", "status": "WARN", "error": "status: Up 1 hour (health: starting)"},
                {"target": "test-app", "type": "container", "status": "FAIL", "error": "status: Exited (137) 2 hours ago"},
            ],
            "metrics": metrics_data,
        }

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
        assert 'class="ssl-summary-banner"' in html, "SSL Summary Banner should be in HTML"
        assert "Earliest cert expires in" in html or "5 day" in html, (
            "SSL Summary Banner should mention earliest cert expiry"
        )

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Platform Services Table
    # · Scenario: Template includes Platform Services table with header
    # · Remove if: Platform Services section removed from template
    def test_platform_services_table(self, mock_status_metrics, caplog):
        """_render_html includes Platform Services Table with all service entries."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 1234,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": [],
            "metrics": metrics_data,
        }

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
        # Check for known services
        assert "Grafana" in html
        assert "Prometheus" in html
        assert "Loki" in html
        assert "Hermes" in html
        assert "Langfuse" in html
        assert "LiteLLM" in html
        # LiteLLM has no external URL
        assert "internal only" in html

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Containers table columns
    # · Scenario: Template includes Uptime and Restart column headers
    # · Remove if: columns removed from container table
    def test_containers_table_new_columns(self, mock_status_metrics, caplog):
        """_render_html includes Uptime and Restart columns in Containers table."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 1234,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": [],
            "metrics": metrics_data,
        }

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
        # Check container uptime values
        # nginx started at 2026-07-24T00:00:00Z — relative to now, test env-dependent
        # At minimum check the HTML structure renders

    # 🧪 TRAP[TEST] · TASK-15 · Regression: Host Resources — Load Average & System Uptime
    # · Scenario: Template includes Load Average and System Uptime rows
    # · Remove if: rows removed from Host Resources table
    def test_host_resources_new_rows(self, mock_status_metrics, caplog):
        """_render_html includes Load Average and System Uptime in Host Resources."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _render_html

        with open(mock_status_metrics) as f:
            metrics_data = json.load(f)

        data = {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 1234,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": [],
            "metrics": metrics_data,
        }

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
        assert "Backup" in html or "backup" in html, "Backup status row should be present"

    # 🧪 TRAP[TEST] · TASK-15 · Regression: SSL banner NOT shown when all certs fresh
    # · Scenario: All certs have >30 days remaining → no SSL banner
    # · Remove if: SSL banner logic changed
    def test_ssl_banner_not_shown_when_fresh(self, tmp_path, caplog):
        """_render_html does NOT show SSL banner when all certs have >30 days remaining."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _render_html

        # Build metrics with all certs fresh (>30 days)
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

        data = {
            "status": "PASS",
            "generated_at": "2026-07-24T00:00:00Z",
            "duration_ms": 100,
            "metrics_freshness": "2026-07-24T00:00:00Z",
            "staleness": None,
            "checks": [],
            "metrics": metrics_data,
        }

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

        assert 'class="ssl-summary-banner"' not in html, (
            "SSL Summary Banner should NOT be present when all certs are fresh"
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

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _curl_platform_service

        with mock.patch("core.modules.status_page.app.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout="200",
                stderr="",
            )

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

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        from core.modules.status_page.app import _curl_platform_service

        with mock.patch("core.modules.status_page.app.subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=7,
                stdout="",
                stderr="Failed to connect to host",
            )

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
            assert "curl exit 7" in result.get("error", ""), f"Expected 'curl exit 7' in error, got '{result.get('error')}'"

    # 🧪 TRAP[TEST] · TASK-16 · Regression: get_all_checks includes platform services
    # · Scenario: mock get_all_checks — platform service checks included in results
    # · Remove if: platform service checks removed from get_all_checks
    def test_get_all_checks_includes_platform_services(self, caplog):
        """get_all_checks includes platform service checks when PLATFORM_SERVICES is populated."""
        caplog.set_level(0)

        os.environ["NODE_NAME"] = "test-node"
        os.environ["PLATFORM_DOMAIN"] = "ai-platform.local"
        os.environ["STATUS_METRICS_JSON"] = "/nonexistent/metrics.json"

        for key in list(sys.modules.keys()):
            if "status_page" in key or "status-page" in key:
                del sys.modules[key]

        # Mock dependencies: node.yaml load, metrics load, curl_vhost, curl_platform_service
        with (
            mock.patch("core.modules.status_page.app.load_node_yaml", return_value={"projects": [], "modules": []}),
            mock.patch("core.modules.status_page.app._load_status_metrics", return_value={
                "generated_at": None, "containers": [], "certs": [],
                "projects": [], "host": {}, "errors": [],
            }),
            mock.patch("core.modules.status_page.app._curl_vhost") as mock_curl_vhost,
            mock.patch("core.modules.status_page.app._curl_platform_service") as mock_curl_platform,
        ):
            mock_curl_vhost.return_value = {"target": "test", "type": "vhost", "status": "PASS", "error": None}
            mock_curl_platform.return_value = {"target": "grafana", "type": "platform_service", "status": "PASS", "error": None}

            from core.modules.status_page.app import get_all_checks

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
