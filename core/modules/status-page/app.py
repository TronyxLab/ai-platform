#!/usr/bin/env python3
# GREP_SUMMARY: status-page app.py live-status http.server node.yaml docker-health.json html json health
# STRUCTURE: ▶ StatusPageHandler(do_GET) → ◇ path=/: render_html → ◇ path=/health: render_health → ◇ path=/status.json: render_json → ▷ _get_checks(): read node.yaml + docker-health.json + live-curl vhosts → ▷ _curl_vhost(): subprocess.run curl → ⎋ response
# region MODULE_CONTRACT
## @purpose  Live status-page HTTP server — aggregates node health (vhosts + containers),
##           renders HTML table with clickable links, exposes /health for CI post-deploy gate.
## @scope    Runs inside status-page Docker container on port 8080, internal-only (no external ports).
##           Accessed via nginx proxy_pass with Basic Auth (auth handled by nginx, not here).
## @invariants
##   - Python 3.10+ stdlib only (http.server, json, subprocess, yaml)
##   - Total check timeout ≤30s (per-check timeout ≤5s)
##   - Anti-recursion: excludes status-page container from self-checks
##   - Reads node.yaml ro, docker-health.json ro
##   - All responses include: X-Robots-Tag, Referrer-Policy, X-Data-Freshness headers
##   - /health returns 200 "PASS" or 503 "FAIL"
##   - /status.json returns full aggregate with status, generated_at, duration_ms, checks[]
## @rationale python3-stdlib chosen over frameworks to minimize image size and dependencies.
##            Live-curl vhosts approach gives real-time verification for post-deploy gate.
##            Docker-health.json read avoids docker.sock access (security).
# endregion MODULE_CONTRACT

import http.server
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

LISTEN_PORT = int(os.environ.get("STATUS_PAGE_PORT", "8080"))
LISTEN_HOST = os.environ.get("STATUS_PAGE_HOST", "0.0.0.0")
NODE_NAME = os.environ.get("NODE_NAME", "test-node")
NODE_CONFIGS_DIR = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
PLATFORM_DOMAIN = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")
DOCKER_HEALTH_JSON = os.environ.get("DOCKER_HEALTH_JSON", "/run/platform/docker-health.json")
PER_CHECK_TIMEOUT = int(os.environ.get("PER_CHECK_TIMEOUT", "5"))
TOTAL_TIMEOUT = int(os.environ.get("TOTAL_TIMEOUT", "30"))

NODE_YAML_PATH = os.path.join(NODE_CONFIGS_DIR, NODE_NAME, "node.yaml")

# ═══════════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════════


# region FUNC_load_node_yaml
def load_node_yaml(path: str) -> dict:
    """Load and parse node.yaml. Returns empty dict on failure."""
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return data if data else {}
    except Exception as e:
        print(f"[IMP:8][status-page][load-yaml] Failed to load node.yaml: {e}", file=sys.stderr)
        return {}


# endregion FUNC_load_node_yaml


# region FUNC_load_docker_health
def load_docker_health(path: str) -> dict:
    """Load docker-health.json from cron export. Returns empty containers list on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[IMP:8][status-page][load-health] Failed to load docker-health.json: {e}", file=sys.stderr)
        return {"generated_at": None, "containers": []}


# endregion FUNC_load_docker_health


# region FUNC_get_vhosts
def get_vhosts(node_data: dict) -> list[dict]:
    """Extract expose:true domains from node.yaml projects list."""
    projects = node_data.get("projects", [])
    vhosts = []
    for p in projects:
        if isinstance(p, dict) and p.get("expose", False) is True:
            domain = p.get("domain", "")
            if domain:
                vhosts.append(
                    {
                        "domain": domain,
                        "name": p.get("name", domain),
                        "repo_url": p.get("repo_url", ""),
                    }
                )
    return vhosts


# endregion FUNC_get_vhosts


# region FUNC_get_modules
def get_modules(node_data: dict) -> list[str]:
    """Get list of deployed module names from node.yaml."""
    return node_data.get("modules", [])


# endregion FUNC_get_modules


# ═══════════════════════════════════════════════════════════════════
# CHECK LAYER
# ═══════════════════════════════════════════════════════════════════


# region FUNC_curl_vhost
def _curl_vhost(domain: str, timeout: int = PER_CHECK_TIMEOUT) -> dict:
    """Live-curl a vhost domain. Returns check result dict."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout), f"https://{domain}"],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_code = result.stdout.strip()
        if result.returncode == 0 and http_code.isdigit():
            code = int(http_code)
            return {
                "target": domain,
                "type": "vhost",
                "status": "PASS" if code == 200 else "WARN",
                "http_code": code,
                "duration_ms": elapsed_ms,
                "error": None,
            }
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": int(http_code) if http_code.isdigit() else 0,
            "duration_ms": elapsed_ms,
            "error": f"curl exit {result.returncode}: {result.stderr.strip()}",
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": f"timeout after {timeout}s",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": str(e),
        }


# endregion FUNC_curl_vhost


# region FUNC_check_container
def _check_container(container: dict) -> dict:
    """Check a single container from docker-health.json data. Returns check result.

    Status logic:
    - Running & healthy → PASS
    - Running & not healthy → WARN
    - Not running, exit_code=0 OR status_line contains "Exited (0)" → PASS (oneshot/init completed)
    - Not running, exit_code>0 OR status_line contains "Exited (non-zero)" → FAIL
    - Other non-running → FAIL
    """
    name = container.get("container_name", "unknown")
    running = container.get("running", False)
    healthy = container.get("healthy", False)
    exit_code = container.get("exit_code")
    status_line = container.get("status_line", "")

    # Anti-recursion: skip self
    if name == "status-page":
        return None

    if running and healthy:
        check_status = "PASS"
    elif running and not healthy:
        check_status = "WARN"
    elif not running:
        # Determine exit code: prefer explicit field, fall back to parsing status_line
        if exit_code is None:
            # Parse "Exited (N)" from status_line
            m = re.search(r"Exited\s*\((\d+)\)", status_line)
            exit_code = int(m.group(1)) if m else None
        # Oneshot/init container that completed successfully → PASS, otherwise FAIL
        check_status = "PASS" if (exit_code is not None and exit_code == 0) else "FAIL"
    else:
        check_status = "FAIL"

    return {
        "target": name,
        "type": "container",
        "status": check_status,
        "running": running,
        "healthy": healthy,
        "exit_code": exit_code,
        "status_line": status_line,
        "error": None if check_status == "PASS" else f"status: {status_line}",
    }


# endregion FUNC_check_container


# region FUNC_get_all_checks
def get_all_checks() -> dict:
    """Run all checks (vhosts + containers) with parallel fan-out. Returns aggregate dict."""
    start = time.monotonic()
    checks = []

    node_data = load_node_yaml(NODE_YAML_PATH)
    docker_health = load_docker_health(DOCKER_HEALTH_JSON)
    freshness = docker_health.get("generated_at")

    # ── Container checks (from docker-health.json) ──
    containers = docker_health.get("containers", [])
    for c in containers:
        result = _check_container(c)
        if result is not None:
            checks.append(result)

    # ── Vhost checks (live curl, parallel fan-out) ──
    vhosts = get_vhosts(node_data)
    if vhosts:
        with ThreadPoolExecutor(max_workers=min(len(vhosts), 10)) as executor:
            futures = {executor.submit(_curl_vhost, v["domain"]): v for v in vhosts}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=TOTAL_TIMEOUT)
                    checks.append(result)
                except Exception as e:  # noqa: PERF203
                    v = futures[future]
                    checks.append(
                        {
                            "target": v["domain"],
                            "type": "vhost",
                            "status": "FAIL",
                            "http_code": 0,
                            "duration_ms": 0,
                            "error": f"future timeout: {e}",
                        }
                    )

    # ── Compute aggregate status ──
    all_pass = all(c["status"] == "PASS" for c in checks)
    duration_ms = int((time.monotonic() - start) * 1000)

    return {
        "status": "PASS" if all_pass else "FAIL",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": duration_ms,
        "docker_health_freshness": freshness,
        "checks": checks,
    }


# endregion FUNC_get_all_checks


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
        freshness = data.get("docker_health_freshness")
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
            elif path == "/status.json":
                self._handle_status_json()
            else:
                self._handle_html()
        except Exception as e:
            print(f"[IMP:8][status-page][handler] Error handling {self.path}: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"ERROR","error":"internal server error"}')

    def _handle_health(self):
        """Handle /health — binary verdict: 200 PASS or 503 FAIL."""
        data = get_all_checks()
        if data["status"] == "PASS":
            self._send_json({"status": "PASS", "checks_count": len(data["checks"]), "duration_ms": data["duration_ms"]})
        else:
            failed = [c for c in data["checks"] if c["status"] != "PASS"]
            self._send_json(
                {
                    "status": "FAIL",
                    "checks_count": len(data["checks"]),
                    "failed_count": len(failed),
                    "duration_ms": data["duration_ms"],
                    "failed": [{"target": f["target"], "status": f["status"], "error": f.get("error")} for f in failed],
                },
                status_code=503,
            )

    def _handle_status_json(self):
        """Handle /status.json — full machine-readable aggregate."""
        data = get_all_checks()
        status_code = 200 if data["status"] == "PASS" else 503
        self._send_json(data, status_code=status_code)

    def _handle_html(self):
        """Handle / — HTML table with vhosts and services, clickable links."""
        data = get_all_checks()
        freshness = data.get("docker_health_freshness", "unknown")
        vhost_checks = [c for c in data["checks"] if c["type"] == "vhost"]
        container_checks = [c for c in data["checks"] if c["type"] == "container"]

        # ── Build HTML ──
        overall = data["status"]
        status_color = "#27ae60" if overall == "PASS" else "#e74c3c"
        status_icon = "✓" if overall == "PASS" else "✗"

        html_parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>Node Status — {NODE_NAME}</title>",
            "<style>",
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:960px;margin:2em auto;padding:0 1em;background:#f5f5f5;color:#333}",
            "h1{font-size:1.5em;margin-bottom:.5em}",
            ".overall{padding:1em;border-radius:8px;margin-bottom:2em;color:#fff;background:" + status_color + "}",
            ".overall .icon{font-size:2em;margin-right:.5em}",
            "table{width:100%;border-collapse:collapse;margin-bottom:2em;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}",
            "th,td{padding:10px 16px;text-align:left;border-bottom:1px solid #eee}",
            "th{background:#fafafa;font-weight:600;font-size:.85em;text-transform:uppercase;color:#666}",
            ".PASS{color:#27ae60;font-weight:600}",
            ".FAIL{color:#e74c3c;font-weight:600}",
            ".WARN{color:#f39c12;font-weight:600}",
            "a{color:#3498db;text-decoration:none}",
            "a:hover{text-decoration:underline}",
            ".footer{font-size:.8em;color:#999;margin-top:3em;text-align:center}",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>Node Status: {NODE_NAME}</h1>",
            '<div class="overall">',
            f'<span class="icon">{status_icon}</span>',
            "<strong>Overall: {status}</strong> — {count} checks in {ms}ms".format(
                status=overall, count=len(data["checks"]), ms=data["duration_ms"]
            ),
            "</div>",
        ]

        # ── Vhosts table ──
        if vhost_checks:
            html_parts.append("<h2>VHosts</h2>")
            html_parts.append(
                "<table><thead><tr><th>Domain</th><th>Status</th><th>HTTP</th><th>Time</th></tr></thead><tbody>"
            )
            for c in vhost_checks:
                cls = c["status"]
                domain = c["target"]
                http_code = c.get("http_code", "-")
                duration = c.get("duration_ms", "-")
                html_parts.append(
                    f'<tr><td><a href="https://{domain}" target="_blank" rel="noopener">{domain}</a></td>'
                    f'<td class="{cls}">{cls}</td>'
                    f"<td>{http_code}</td>"
                    f"<td>{duration}ms</td></tr>"
                )
            html_parts.append("</tbody></table>")

        # ── Containers table ──
        if container_checks:
            html_parts.append("<h2>Services / Containers</h2>")
            html_parts.append("<table><thead><tr><th>Container</th><th>Status</th><th>Info</th></tr></thead><tbody>")
            for c in container_checks:
                cls = c["status"]
                name = c["target"]
                info = c.get("status_line", "")
                html_parts.append(f'<tr><td>{name}</td><td class="{cls}">{cls}</td><td>{info}</td></tr>')
            html_parts.append("</tbody></table>")

        # ── Footer ──
        html_parts.append('<div class="footer">')
        html_parts.append(
            "Generated: {ts} · Freshness: {fresh} · Platform: {domain}".format(
                ts=data["generated_at"], fresh=freshness, domain=PLATFORM_DOMAIN
            )
        )
        html_parts.append("</div>")
        html_parts.append("</body></html>")

        self._send_html("\n".join(html_parts), freshness)

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
    print(f"[IMP:7][status-page][main] docker-health.json: {DOCKER_HEALTH_JSON}", file=sys.stderr)

    server = http.server.HTTPServer((LISTEN_HOST, LISTEN_PORT), StatusPageHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[IMP:9][status-page][main] Shutting down", file=sys.stderr)
        server.shutdown()
