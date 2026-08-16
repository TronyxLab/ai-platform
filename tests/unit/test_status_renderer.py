# GREP_SUMMARY: test-status-renderer format-bytes compute-uptime enrich-projects enrich-containers memory-bytes render-html jinja2 context disabled swap no-limit backup
# STRUCTURE: ┌test functions┐ → ◇ _format_bytes (3) → ◇ _compute_uptime_human (2) → ◇ _enrich_projects (1)
#            → ◇ _enrich_containers (1) → ◇ enrich_containers_memory_bytes (1) → ◇ render_html (5)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/status-page/renderer.py — pure rendering functions
##           extracted from app.py (DevPlan 117 G T55). Characterization-based.
## @scope    No Docker, no HTTP. Jinja2 env built from the real templates dir.
## @invariants
##   - All tests use tmp_path / real templates dir (no hardcoded paths)
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T55 §TEST_SPEC — renderer direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T55 — created
## @changes  2026-08-12 · DevPlan 158 W2 T2.4 — memory_bytes, DISABLED badge, swap, no-limit, backup
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

pytestmark = pytest.mark.static_audit


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
    """Tests for _compute_uptime_human() (edge cases parametrized, F5-reduction)."""

    @pytest.mark.parametrize(
        ("started_at", "expected"),
        [
            (None, "\u2014"),  # None → em-dash
            ("not-a-timestamp", "\u2014"),  # unparseable → em-dash
        ],
    )
    def test_uptime_edges(self, started_at, expected) -> None:
        """None/unparseable timestamps → em-dash (regression guard)."""
        assert _compute_uptime_human(started_at) == expected

    # 🧪 TRAP[TEST] · Regression · Scenario: recent timestamp (< 1m)
    # · Expect: "< 1m"
    # · Last fail: None (new test for DevPlan 117 G T55)
    # · Remove if: uptime logic changes
    def test_uptime_recent(self) -> None:
        """Recent started_at → '< 1m'."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        assert _compute_uptime_human(now) == "< 1m"


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
    # · Expect: live_status rendered in HTML; входной services НЕ мутируется (W7-E2)
    # · Last fail: renderer.py:276-281 мутировал входные svc dict (live_status на оригинале)
    # · Remove if: render live-status enrichment logic changes
    def test_render_html_platform_services_live(self, jinja_env) -> None:
        """Platform services get live_status from checks (rendered in HTML, no input mutation)."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-01T00:00:00Z",
            "checks": [{"type": "platform_service", "target": "grafana", "status": "PASS", "error": None}],
            "metrics": {"projects": [], "containers": [], "certs": [], "host": {}, "errors": []},
        }
        services = [{"name": "Grafana", "internal": "grafana:3000", "health_path": "/api/health"}]

        html = render_html(data, jinja_env, services, "test-node")

        # W7-E2: вход НЕ мутируется — live-статус рендерится из НОВОГО списка (возвращаемое значение)
        assert "live_status" not in services[0], "render_html must not mutate input services"
        assert "status-label pass" in html, "HTML must render live PASS status for Grafana"

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · входной services НЕ мутируется (W7-E2 mutation fix)
    # · Last fail: renderer.py:276-281 — svc["live_status"]=... на оригинальных dict →
    # ·   глобальная константа app.PLATFORM_SERVICES мутировалась между рендерами
    # · Remove if: render_html снова начнёт мутировать входной список сервисов
    def test_render_html_services_input_not_mutated(self, jinja_env) -> None:
        """R5 negative: входной список services НЕ мутируется render_html (W7-E2)."""
        import copy

        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-01T00:00:00Z",
            "checks": [
                {"type": "platform_service", "target": "grafana", "status": "FAIL", "error": "boom"},
                {"type": "platform_service", "target": "prometheus", "status": "DISABLED", "error": "not deployed"},
            ],
            "metrics": {"projects": [], "containers": [], "certs": [], "host": {}, "errors": []},
        }
        services = [
            {"name": "Grafana", "internal": "grafana:3000", "health_path": "/api/health"},
            {"name": "Prometheus", "internal": "prometheus:9090", "health_path": "/-/healthy"},
        ]
        snapshot = copy.deepcopy(services)

        render_html(data, jinja_env, services, "test-node")

        assert services == snapshot, f"R5 FAIL: render_html мутировал входной список сервисов (W7-E2): {services}"

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


# ══════════════════════════════════════════════════════════════════════
# TESTS: enrich_containers memory bytes (DevPlan 158 W2 T2.3 — P6)
# ══════════════════════════════════════════════════════════════════════


class TestEnrichContainersMemoryBytes:
    """Tests for memory_limit_bytes/memory_usage_bytes in enriched containers (T2.3, P6)."""

    # 🧪 TRAP[TEST] · Regression · Scenario: memory bytes passed raw (P6 T2.3)
    # · Expect: enriched container has memory_limit_bytes and memory_usage_bytes
    # · Last fail: None (new test for DevPlan 158 W2 T2.3)
    # · Remove if: enrich_containers stops passing raw bytes
    def test_enrich_containers_includes_memory_bytes(self) -> None:
        """Enriched container contains memory_limit_bytes and memory_usage_bytes."""
        containers = [
            {
                "name": "app",
                "running": True,
                "memory_usage_bytes": 524288000,  # 500 MB
                "memory_limit_bytes": 1073741824,  # 1 GB
            }
        ]

        enriched = _enrich_containers(containers)

        assert enriched[0]["memory_limit_bytes"] == 1073741824
        assert enriched[0]["memory_usage_bytes"] == 524288000


# ══════════════════════════════════════════════════════════════════════
# TESTS: render_html DISABLED + P5/P6/P7 fixes (DevPlan 158 W2 T2.4)
# ══════════════════════════════════════════════════════════════════════


class TestRenderHtmlDisabledAndFixes:
    """Tests for DISABLED badge rendering and P5/P6/P7 template fixes."""

    # 🧪 TRAP[TEST] · Regression · Scenario: DISABLED service rendered with distinct visual
    # · Expect: status-dot disabled + status-label disabled in HTML
    # · Last fail: None (new test for DevPlan 158 W2 T2.4)
    # · Remove if: DISABLED visual changes
    def test_render_html_shows_disabled_badge(self, jinja_env) -> None:
        """DISABLED platform service → rendered with disabled dot/label."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-12T00:00:00Z",
            "checks": [
                {
                    "type": "platform_service",
                    "target": "grafana",
                    "status": "DISABLED",
                    "error": "service not deployed (DNS unresolved)",
                }
            ],
            "metrics": {"projects": [], "containers": [], "certs": [], "host": {}, "errors": []},
        }
        services = [
            {"name": "Grafana", "internal": "grafana:3000", "health_path": "/api/health", "url": "https://g.test"}
        ]

        html = render_html(data, jinja_env, services, "test-node")

        assert "disabled" in html.lower(), "HTML must render DISABLED status"
        assert "status-dot disabled" in html or "status-label disabled" in html
        assert "not deployed" in html, "DISABLED error reason should be visible"

    # 🧪 TRAP[TEST] · Regression · Scenario: swap hidden when swap_total_gb=0 (P5 T2.2)
    # · Expect: no "Swap" row when swap_total_gb is 0
    # · Last fail: None (new test for DevPlan 158 W2 T2.2)
    # · Remove if: swap conditional logic changes
    def test_render_html_hides_swap_when_zero(self, jinja_env) -> None:
        """swap_total_gb=0 → no swap row in HTML."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-12T00:00:00Z",
            "checks": [],
            "metrics": {
                "projects": [],
                "containers": [],
                "certs": [],
                "host": {"swap_total_gb": 0, "swap_free_gb": 0, "memory_total_gb": 8},
                "errors": [],
            },
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "Swap Used" not in html, "Swap row should be hidden when swap_total_gb=0 (P5)"

    # 🧪 TRAP[TEST] · Regression · Scenario: memory "no limit" when limit=0 (P6 T2.2)
    # · Expect: "no limit" text instead of "0% / 0 B"
    # · Last fail: None (new test for DevPlan 158 W2 T2.2)
    # · Remove if: memory-limit-zero handling changes
    def test_render_html_shows_no_limit_when_memory_limit_zero(self, jinja_env) -> None:
        """Container with memory_limit_bytes=0 → 'no limit' in HTML."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-12T00:00:00Z",
            "checks": [],
            "metrics": {
                "projects": [],
                "containers": [
                    {
                        "name": "app",
                        "running": True,
                        "memory_usage_bytes": 524288000,
                        "memory_limit_bytes": 0,  # no limit
                    }
                ],
                "certs": [],
                "host": {},
                "errors": [],
            },
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "no limit" in html, "Container with limit=0 should show 'no limit' (P6)"

    # 🧪 TRAP[TEST] · Regression · Scenario: backup not configured info-note (P7 T2.2)
    # · Expect: "Backups not configured" when backup.status=unknown + null timestamps
    # · Last fail: None (new test for DevPlan 158 W2 T2.2)
    # · Remove if: backup-unknown handling changes
    def test_render_html_shows_backup_not_configured(self, jinja_env) -> None:
        """backup.status=unknown + null timestamps → info-note 'Backups not configured'."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-12T00:00:00Z",
            "checks": [],
            "metrics": {
                "projects": [],
                "containers": [],
                "certs": [],
                "host": {},
                "backup": {"status": "unknown", "last_postgres_at": None, "last_app_data_at": None},
                "errors": [],
            },
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "Backups not configured" in html, (
            "backup unknown + null timestamps → info-note 'Backups not configured' (P7)"
        )

    # 🧪 TRAP[TEST] · Regression · Scenario: no /refresh button (P8 T2.2)
    # · Expect: /refresh form/button removed
    # · Last fail: None (new test for DevPlan 158 W2 T2.2)
    # · Remove if: /refresh button restored
    def test_render_html_no_refresh_button(self, jinja_env) -> None:
        """HTML has no /refresh form/button (P8 — removed placeholder)."""
        data = {
            "status": "PASS",
            "metrics_freshness": "2026-08-12T00:00:00Z",
            "checks": [],
            "metrics": {"projects": [], "containers": [], "certs": [], "host": {}, "errors": []},
        }

        html = render_html(data, jinja_env, [], "test-node")

        assert "/refresh" not in html, "HTML should not contain /refresh button (P8)"
        assert "Refresh Metrics" not in html, "HTML should not contain Refresh Metrics button (P8)"
