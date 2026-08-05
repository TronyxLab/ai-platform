#!/usr/bin/env python3
# GREP_SUMMARY: status-page app.py live-status http.server node.yaml status-metrics.json jinja2 html json health refresh orchestrator collectors renderer
# STRUCTURE: ▶ Config constants → ▶ _jinja_env (once) → ▶ PLATFORM_SERVICES → ▶ wrappers (get_all_checks/_render_html/_format_bytes/...)
#            → ▶ StatusPageHandler(do_GET|do_POST) → path=/: Jinja2 render → /health: render_health → /status.json: render_json → /refresh: POST placeholder → ▶ main
# region MODULE_CONTRACT
## @purpose  Live status-page HTTP server — aggregates node health (certs + containers + host),
##           renders Jinja2 HTML with 3 tables, exposes /health for CI post-deploy gate.
##           Orchestrator only (DevPlan 117 G T55): collectors and renderer extracted to
##           sibling modules; app.py keeps Config, Jinja2 env, StatusPageHandler, main.
##           W10 T10.11 (M-1): ThreadingHTTPServer — «медленный апстрим + /healthz» больше не
##           блокирует fast-path readiness-пробы (load-тест: unit с mock-медленным хендлером).
##           W10 T10.13 (M-7): /healthz возвращает 503 при staleness > порога (синхронизация с /health).
## @scope    Runs inside status-page Docker container on port 8080, internal-only (no external ports).
##           Accessed via nginx proxy_pass with Basic Auth (auth handled by nginx, not here).
## @invariants
##   - Jinja2 autoescape=select_autoescape(['html']) — XSS protection (Δ12, AC14-M)
##   - FileSystemLoader with absolute path (Path(__file__).parent / "templates") — Δ19
##   - Environment created ONCE at module level, not per request
##   - Total check timeout ≤30s (per-check timeout ≤5s)
##   - Anti-recursion: excludes status-page container from self-checks
##   - Reads status-metrics.json (ro, mounted from host tmpfs), checks schema_version ≥ 2
##   - Staleness check: generated_at > 5 min → warning banner on HTML + WARN in /health
##   - All responses include: X-Robots-Tag, Referrer-Policy, X-Data-Freshness headers
##   - /health returns 200 "PASS" or 503 "FAIL" (unchanged contract AC3-M)
##   - /status.json returns full aggregate with certs, projects, host, schema_version: 2 (AC4-M)
##   - container_name → name: contract broken consciously (Δ8, AC12-M)
## @rationale DevPlan 117 G T55: collectors (~450 LOC) → collectors.py, renderer (~270 LOC) → renderer.py.
##            app.py = thin orchestrator (~355 LOC). Wrapper functions preserve the module-level
##            app.* API consumed by existing tests and the HTTP handler.
## @changes
##   2026-07-23 | META Δ2 | Atomic write → readers never see partial JSON
##   2026-07-23 | META Δ4 | schema_version check at read time
##   2026-07-23 | META Δ5 | Modular collectors (docker/cert/project/host)
##   2026-07-23 | META Δ8 | container_name → name
##   2026-07-23 | META Δ12 | Jinja2 autoescape
##   2026-07-23 | META Δ19 | FileSystemLoader with absolute path
##   2026-07-24 | D067 W1 | _enrich_containers(containers, projects) — domain mapping, uptime_human, exit_code_human
##   2026-07-24 | D067 W1 | _render_html — SSL Summary Banner, Platform Services Table, new host fields
##   2026-07-24 | D067 W3 | _curl_platform_service() + platform service checks in get_all_checks()
##   2026-07-24 | 047 W1  | _format_bytes() replaces _bytes_to_gb()/_bytes_to_gb_str() — auto-unit B/KB/MB/GB/TB
##   2026-07-24 | 047 W1  | _enrich_projects/_enrich_containers use _format_bytes() instead of _bytes_to_gb*
##   2026-07-24 | 047 W2  | _render_html() — host context extended with memory_*, swap_*, os_* fields
##   2026-08-01 | 117 G T55 | collectors → collectors.py, renderer → renderer.py; app.py = orchestrator
##   2026-08-05 | 136 W10 T10.11 | ThreadingHTTPServer (M-1: slow upstream не блокирует /healthz)
##   2026-08-05 | 136 W10 T10.13 | /healthz staleness → 503 (синхронизация с /health, M-7)
# ⚠️ TRAP[DECISION] · 2026-08-01 · — · status-page stays on raw yaml.safe_load — exception from NodeYaml facade invariant
# · Rejected: migrating load_node_yaml() to core.internal.shared.node_yaml (risk: layer violation + image bloat)
# · Reason: module image is python:3.12-alpine WITHOUT core/; modules→internal import is forbidden
#   (core/AGENTS.md Cross-layer — modules can only import lib/ and templates/). node.yaml is mounted
#   ro as data. The "single read point" invariant (DRIFT-088-7) applies to core/ only. status-page is
#   EXCLUDED from the "single project parser" gate (DevPlan 116 B6 T9, decision D1).
# · Rev: if node.yaml reading moves into a core-owned service (e.g. internal API) → remove raw parsing here.
# endregion MODULE_CONTRACT

import http.server
import json
import os
import sys
from pathlib import Path

# DevPlan 117 G T55: business logic extracted to sibling modules (same directory — no
# core/internal import, layer-safe). app.py imports them at module level (Docker container,
# single process — start-up time not critical, per DevPlan AC-G5 exception).
# Private-import gate (test_gate_no_private_cross_module_imports) requires public-name imports;
# private module-level aliases preserve the legacy app.* re-export API for existing tests.
from collectors import (
    compute_staleness as _compute_staleness,
)
from collectors import (
    get_all_checks as _get_all_checks_impl,
)
from collectors import (
    load_status_metrics as _load_status_metrics,  # noqa: F401  (re-exported — app._load_status_metrics)
)
from jinja2 import Environment, FileSystemLoader, select_autoescape
from renderer import (
    compute_uptime_human as _compute_uptime_human,  # noqa: F401  (re-exported — app._compute_uptime_human)
)
from renderer import (
    enrich_containers as _enrich_containers,  # noqa: F401  (re-exported — app._enrich_containers)
)
from renderer import (
    enrich_projects as _enrich_projects,  # noqa: F401  (re-exported — app._enrich_projects)
)
from renderer import (
    format_bytes as _format_bytes,  # noqa: F401  (re-exported — app._format_bytes)
)
from renderer import (
    render_html as _render_html_impl,
)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

LISTEN_PORT = int(os.environ.get("STATUS_PAGE_PORT", "8080"))
LISTEN_HOST = os.environ.get("STATUS_PAGE_HOST", "0.0.0.0")
NODE_NAME = os.environ.get("NODE_NAME", "test-node")
NODE_CONFIGS_DIR = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
PLATFORM_DOMAIN = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")
STATUS_METRICS_JSON = os.environ.get("STATUS_METRICS_JSON", "/run/platform/status-metrics.json")
PER_CHECK_TIMEOUT = int(os.environ.get("PER_CHECK_TIMEOUT", "5"))
TOTAL_TIMEOUT = int(os.environ.get("TOTAL_TIMEOUT", "30"))

NODE_YAML_PATH = os.path.join(NODE_CONFIGS_DIR, NODE_NAME, "node.yaml")

# ═══════════════════════════════════════════════════════════════════
# JINJA2 ENVIRONMENT (initialized once at module level)
# ═══════════════════════════════════════════════════════════════════

_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)

# ═══════════════════════════════════════════════════════════════════
# PLATFORM SERVICES (static list — Contract C2)
# ═══════════════════════════════════════════════════════════════════

# Static list of platform services for the Platform Services Table.
# Each entry: name (display), url (external link), internal (Docker DNS), health_path (curl path).
# LiteLLM has no external URL (no nginx vhost) — displayed as "internal only".
PLATFORM_SERVICES = [
    {
        "name": "Grafana",
        "url": f"https://grafana.{PLATFORM_DOMAIN}",
        "internal": "grafana:3000",
        "health_path": "/api/health",
    },
    {
        "name": "Prometheus",
        "url": f"https://prometheus.{PLATFORM_DOMAIN}",
        "internal": "prometheus:9090",
        "health_path": "/-/healthy",
    },
    {"name": "Loki", "url": f"https://loki.{PLATFORM_DOMAIN}", "internal": "loki:3100", "health_path": "/ready"},
    {"name": "Hermes", "url": f"https://hermes.{PLATFORM_DOMAIN}", "internal": "hermes-agent:9119", "health_path": "/"},
    {
        "name": "Langfuse",
        "url": f"https://langfuse.{PLATFORM_DOMAIN}",
        "internal": "langfuse:3000",
        "health_path": "/api/public/health",
    },
    {"name": "LiteLLM", "url": None, "internal": "litellm:4000", "health_path": "/health/liveliness"},
]


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATOR WRAPPERS (DevPlan 117 G T55 — thin delegation layer)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_get_all_checks
def get_all_checks() -> dict:
    """Run all checks (vhosts + containers from metrics) with parallel fan-out. Returns aggregate dict.

    ## @purpose — Thin orchestrator wrapper over collectors.get_all_checks (DevPlan 117 G T55).
    ##            Config passed explicitly — no env coupling in the collectors module.
    """
    return _get_all_checks_impl(
        node_yaml_path=NODE_YAML_PATH,
        status_metrics_json=STATUS_METRICS_JSON,
        platform_services=PLATFORM_SERVICES,
        per_check_timeout=PER_CHECK_TIMEOUT,
        total_timeout=TOTAL_TIMEOUT,
    )


# endregion FUNC_get_all_checks


# region FUNC_render_html
def _render_html(data: dict) -> str:
    """Render status page using Jinja2 template (wrapper over renderer.render_html).

    ## @purpose — Thin orchestrator wrapper (DevPlan 117 G T55). jinja_env, platform_services
    ##            and node_name passed as parameters to avoid circular imports (R4).
    """
    return _render_html_impl(
        data,
        _jinja_env,
        platform_services=PLATFORM_SERVICES,
        node_name=NODE_NAME,
    )


# endregion FUNC_render_html


# ═══════════════════════════════════════════════════════════════════
# HTTP HANDLER
# ═══════════════════════════════════════════════════════════════════


# region CLASS_StatusPageHandler
class StatusPageHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for status-page endpoints."""

    def _send_common_headers(self, content_type: str, freshness: str | None = None):
        """Send common security and metadata headers."""
        self.send_header("Content-Type", content_type)
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        if freshness:
            self.send_header("X-Data-Freshness", freshness)

    def _send_json(self, data: dict, status_code: int = 200):
        """Send JSON response."""
        body = json.dumps(data, indent=2).encode("utf-8")
        freshness = data.get("metrics_freshness")
        self.send_response(status_code)
        self._send_common_headers("application/json", freshness)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, freshness: str | None = None):
        """Send HTML response."""
        body = html.encode("utf-8")
        self.send_response(200)
        self._send_common_headers("text/html; charset=utf-8", freshness)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Route GET requests to appropriate handlers."""
        try:
            path = self.path.rstrip("/") or "/"
            if path == "/health":
                self._handle_health()
            elif path == "/healthz":
                self._handle_healthz()
            elif path == "/status.json":
                self._handle_status_json()
            else:
                self._handle_html()
        except Exception as e:
            print(f"[IMP:8][status-page][handler] Error handling {self.path}: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"ERROR","error":"internal server error"}')

    def do_POST(self):
        """Route POST requests — /refresh placeholder."""
        try:
            path = self.path.rstrip("/") or "/"
            if path == "/refresh":
                self._handle_refresh()
            else:
                self.send_response(405)
                self.end_headers()
                self.wfile.write(b"Method Not Allowed")
        except Exception as e:
            print(f"[IMP:8][status-page][handler] POST error: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"ERROR"}')

    def _handle_health(self):
        """Handle /health — binary verdict: 200 PASS or 503 FAIL."""
        data = get_all_checks()
        if data["status"] == "PASS" and not data.get("staleness"):
            self._send_json({"status": "PASS", "checks_count": len(data["checks"]), "duration_ms": data["duration_ms"]})
        else:
            failed = [c for c in data["checks"] if c["status"] != "PASS"]
            response = {
                "status": "FAIL",
                "checks_count": len(data["checks"]),
                "failed_count": len(failed),
                "duration_ms": data["duration_ms"],
                "failed": [{"target": f["target"], "status": f["status"], "error": f.get("error")} for f in failed],
            }
            if data.get("staleness"):
                response["staleness"] = data["staleness"]
            self._send_json(response, status_code=503)

    # region FUNC_handle_healthz
    def _handle_healthz(self):
        """Handle /healthz — lightweight readiness probe for Docker HEALTHCHECK.

        # ▶ GET /healthz → ◇ check status-metrics.json exists + readable
        #                   → ◇ check staleness (generated_at ≤ 5 min ago)
        #                   → ◇ 200 "PASS" | 503 "FAIL"
        #
        # Unlike /health (full system checks: vhosts, containers, platform services),
        # this is a fast (~50ms) readiness probe that verifies the data pipeline
        # is functional — status-page is useless without fresh metrics data.
        #
        # Used by: Docker HEALTHCHECK (docker-compose.base.yml + Dockerfile)
        """
        import time as _time

        start = _time.monotonic()
        path = STATUS_METRICS_JSON

        # Check 1: file exists and is a regular file (not dir, not symlink-to-nothing)
        if not os.path.isfile(path):
            duration_ms = int((_time.monotonic() - start) * 1000)
            self._send_json(
                {
                    "status": "FAIL",
                    "reason": "metrics_file_missing",
                    "message": f"{path} not found or not a regular file",
                    "duration_ms": duration_ms,
                },
                status_code=503,
            )
            return

        # Check 2: file is readable (can open and parse JSON)
        try:
            with open(path) as f:
                metrics = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            duration_ms = int((_time.monotonic() - start) * 1000)
            self._send_json(
                {
                    "status": "FAIL",
                    "reason": "metrics_file_unreadable",
                    "message": f"Cannot read {path}: {e}",
                    "duration_ms": duration_ms,
                },
                status_code=503,
            )
            return

        # Check 3: freshness — data must not be older than 5 minutes
        generated_at = metrics.get("generated_at")
        staleness = _compute_staleness(generated_at)

        duration_ms = int((_time.monotonic() - start) * 1000)

        if staleness:
            # W10 T10.13 (M-7): stale data → 503 FAIL — синхронизация с /health (stale pipeline
            # означает, что status-page бесполезен: метрики не обновляются). Docker HEALTHCHECK
            # увидит unhealthy → рестарт/алерт, а не ложный PASS.
            self._send_json(
                {
                    "status": "FAIL",
                    "reason": "stale_data",
                    "staleness": staleness,
                    "schema_version": metrics.get("schema_version", 0),
                    "duration_ms": duration_ms,
                },
                status_code=503,
            )
        else:
            self._send_json(
                {
                    "status": "PASS",
                    "schema_version": metrics.get("schema_version", 0),
                    "generated_at": generated_at,
                    "duration_ms": duration_ms,
                },
                status_code=200,
            )

    # endregion FUNC_handle_healthz

    def _handle_status_json(self):
        """Handle /status.json — full machine-readable aggregate with metrics."""
        data = get_all_checks()
        # Include full metrics data
        metrics = data.get("metrics", {})
        full_data = {
            "status": data["status"],
            "generated_at": data["generated_at"],
            "duration_ms": data["duration_ms"],
            "metrics_freshness": data["metrics_freshness"],
            "staleness": data.get("staleness"),
            "checks": data["checks"],
            # Extended fields (AC4-M)
            "schema_version": metrics.get("schema_version", 0),
            "node": metrics.get("node", NODE_NAME),
            "containers": metrics.get("containers", []),
            "certs": metrics.get("certs", []),
            "projects": metrics.get("projects", []),
            "host": metrics.get("host", {}),
            "backup": metrics.get("backup", {}),
            "errors": metrics.get("errors", []),
        }
        status_code = 200 if data["status"] == "PASS" else 503
        self._send_json(full_data, status_code=status_code)

    def _handle_html(self):
        """Handle / — HTML page with Jinja2 template."""
        print("[IMP:7][status-page][html] Rendering status page", file=sys.stderr)
        data = get_all_checks()
        freshness = data.get("metrics_freshness", "unknown")

        html = _render_html(data)
        self._send_html(html, freshness)

    def _handle_refresh(self):
        """Handle /refresh — placeholder for manual metric refresh.

        # ▶ POST /refresh → redirect to / with temp message
        # Future: trigger cron export on host via SSH or signal
        """
        print("[IMP:7][status-page][refresh] Manual refresh requested", file=sys.stderr)
        # Placeholder: redirect to /
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        """Override to use LDD logging format."""
        print(f"[IMP:7][status-page][http] {self.client_address[0]} - {format % args}", file=sys.stderr)


# endregion CLASS_StatusPageHandler


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"[IMP:9][status-page][main] Starting status-page on {LISTEN_HOST}:{LISTEN_PORT}", file=sys.stderr)
    print(f"[IMP:7][status-page][main] node.yaml: {NODE_YAML_PATH}", file=sys.stderr)
    print(f"[IMP:7][status-page][main] status-metrics.json: {STATUS_METRICS_JSON}", file=sys.stderr)

    server = http.server.ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), StatusPageHandler)
    # W10 T10.11 (M-1): ThreadingHTTPServer — медленный /health (полный агрегат) НЕ блокирует
    # fast-path /healthz (readiness-проба Docker HEALTHCHECK). daemon_threads — быстрый выход.
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[IMP:9][status-page][main] Shutting down", file=sys.stderr)
        server.shutdown()
