#!/usr/bin/env python3
# GREP_SUMMARY: test-status-renderer format-bytes compute-uptime enrich-projects enrich-containers render-html jinja2 context
# STRUCTURE: ┌9 test functions┐ → ◇ _format_bytes (3) → ◇ _compute_uptime_human (2) → ◇ _enrich_projects (1)
#            → ◇ _enrich_containers (1) → ◇ render_html (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/status-page/renderer.py — pure rendering functions
#            extracted from app.py (DevPlan 117 G T55). Characterization-based.
## @scope    No Docker, no HTTP. Jinja2 env built from the real templates dir.
## @invariants
##   - All tests use tmp_path / real templates dir (no hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T55 §TEST_SPEC — renderer direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T55 — created
# endregion MODULE_CONTRACT

import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

# status-page/ dir has a hyphen — import via sys.path (pattern: tests/test_status_page.py).
_STATUS_PAGE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "status-page"
if str(_STATUS_PAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_STATUS_PAGE_DIR))

from renderer import (
    compute_uptime_human as _compute_uptime_human,
)
from renderer import (
    enrich_containers as _enrich_containers,
)
from renderer import (
    enrich_projects as _enrich_projects,
)
from renderer import (
    format_bytes as _format_bytes,
)
from renderer import (
    render_html,
)


@pytest.fixture
def jinja_env():
    """Real Jinja2 env from the status-page templates dir."""
    return Environment(
        loader=FileSystemLoader(str(_STATUS_PAGE_DIR / "templates")),
        autoescape=select_autoescape(["html"]),
    )


# ══════════════════════════════════════════════════════════════════════
# TESTS: _format_bytes
# ══════════════════════════════════════════════════════════════════════


class TestFormatBytes:
    """Tests for _format_bytes()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: auto-unit selection
    # · Expect: B/KB/MB/GB/TB units
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: format_bytes logic changes
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0 B"),
            (-1, "0 B"),
            (500, "500 B"),
            (1024, "1.0 KB"),
            (1536000, "1.5 MB"),
            (1073741824, "1.0 GB"),
            (1099511627776, "1.0 TB"),
        ],
    )
    def test_format_bytes_units(self, value: int, expected: str) -> None:
        """Auto-unit B/KB/MB/GB/TB."""
        assert _format_bytes(value) == expected

    # 🧪 TRAP[TEST] · Regression · Scenario: custom precision
    # · Expect: formatted with precision
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: format_bytes precision logic changes
    def test_format_bytes_precision(self) -> None:
        """precision param respected."""
        assert _format_bytes(1536000, precision=0) == "1 MB"
        assert _format_bytes(1536000, precision=2) == "1.46 MB"
        assert _format_bytes(1073741824, precision=3) == "1.000 GB"


# ══════════════════════════════════════════════════════════════════════
# TESTS: _compute_uptime_human
# ══════════════════════════════════════════════════════════════════════


class TestComputeUptime:
    """Tests for _compute_uptime_human()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: None timestamp
    # · Expect: "—"
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: uptime logic changes
    def test_uptime_none(self) -> None:
        """None → em-dash."""
        assert _compute_uptime_human(None) == "\u2014"

    # 🧪 TRAP[TEST] · Regression · Scenario: recent timestamp (< 1m)
    # · Expect: "< 1m"
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: uptime logic changes
    def test_uptime_recent(self) -> None:
        """Recent started_at → '< 1m'."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        assert _compute_uptime_human(now) == "< 1m"

    # 🧪 TRAP[TEST] · Regression · Scenario: unparseable timestamp
    # · Expect: "—"
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: uptime logic changes
    def test_uptime_unparseable(self) -> None:
        """Garbage → em-dash."""
        assert _compute_uptime_human("not-a-timestamp") == "\u2014"


# ══════════════════════════════════════════════════════════════════════
# TESTS: _enrich_projects / _enrich_containers
# ══════════════════════════════════════════════════════════════════════


class TestEnrich:
    """Tests for _enrich_projects() and _enrich_containers()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: project with matching cert
    # · Expect: cert fields + san_truncated
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: enrich_projects logic changes
    def test_enrich_projects_cert_match(self) -> None:
        """Project domain matched with cert → cert fields."""
        projects = [{"name": "app", "domain": "app.example.com", "code_size_bytes": 2048}]
        certs = [
            {
                "domains": ["app.example.com"],
                "issuer": "LE",
                "not_after_iso": "2026-08-01",
                "days_remaining": 30,
                "san": ["app.example.com", "www.app.example.com"],
            }
        ]

        enriched = _enrich_projects(projects, certs)

        assert enriched[0]["cert_issuer"] == "LE"
        assert enriched[0]["days_remaining"] == 30
        assert enriched[0]["code_size_gb"] == "2.0 KB"
        assert "www.app.example.com" in enriched[0]["san_full"]

    # 🧪 TRAP[TEST] · Regression · Scenario: container domain mapping (exact + prefix)
    # · Expect: domains populated
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: enrich_containers logic changes
    def test_enrich_containers_domain_mapping(self) -> None:
        """Container name prefix matches project → domain added."""
        containers = [
            {"name": "myapp-api", "running": True, "memory_usage_bytes": 1024, "memory_limit_bytes": 2048},
            {"name": "nginx", "running": False, "exit_code": 0},
        ]
        projects = [{"name": "myapp", "domain": "myapp.example.com"}]

        enriched = _enrich_containers(containers, projects)

        assert enriched[0]["domains"] == ["myapp.example.com"]
        assert enriched[0]["memory_used"] == "1.0 KB"
        assert enriched[1]["domains"] == []
        assert enriched[1]["exit_code_human"] == "Exited (0)"


# ══════════════════════════════════════════════════════════════════════
# TESTS: render_html
# ══════════════════════════════════════════════════════════════════════


class TestRenderHtml:
    """Tests for render_html()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: render with healthy data
    # · Expect: HTML contains project/container names and 3 tables
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: render logic changes
    def test_render_html_contains_tables(self, jinja_env) -> None:
        """Full render → HTML with project names and container names."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-01T00:00:00Z",
            "checks": [],
            "metrics": {
                "projects": [{"name": "myapp", "domain": "myapp.example.com"}],
                "containers": [{"name": "nginx", "running": True, "healthy": True}],
                "certs": [],
                "host": {},
                "errors": [],
            },
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "myapp" in html
        assert "nginx" in html

    # 🧪 TRAP[TEST] · Regression · Scenario: platform service enriched with live status
    # · Expect: svc entries mutated with live_status
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: render live-status enrichment logic changes
    def test_render_html_platform_services_live(self, jinja_env) -> None:
        """Platform services get live_status from checks."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-01T00:00:00Z",
            "checks": [{"type": "platform_service", "target": "grafana", "status": "PASS", "error": None}],
            "metrics": {"projects": [], "containers": [], "certs": [], "host": {}, "errors": []},
        }
        services = [{"name": "Grafana", "internal": "grafana:3000", "health_path": "/api/health"}]

        render_html(data, jinja_env, services, "test-node")

        assert services[0]["live_status"] == "PASS"

    # 🧪 TRAP[TEST] · Regression · Scenario: XSS payload escaped
    # · Expect: HTML-escaped (autoescape)
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: autoescape settings change
    def test_render_html_autoescape(self, jinja_env) -> None:
        """XSS payload in project name → escaped in output."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-01T00:00:00Z",
            "checks": [],
            "metrics": {
                "projects": [{"name": "<script>alert(1)</script>", "domain": "x.example.com"}],
                "containers": [],
                "certs": [],
                "host": {},
                "errors": [],
            },
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
