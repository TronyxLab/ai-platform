#!/usr/bin/env python3
# GREP_SUMMARY: status-page app.py live-status http.server node.yaml status-metrics.json jinja2 html json health refresh platform-services enrich-containers uptime format-bytes memory swap os backup
# STRUCTURE: ▶ StatusPageHandler(do_GET|do_POST) → ◇ path=/: Jinja2 render → ◇ path=/health: render_health
#            → ◇ path=/status.json: render_json → ◇ path=/refresh: POST placeholder
#            → ▷ _load_status_metrics(): read + schema check + staleness
#            → ▷ _get_checks(): read node.yaml + status-metrics.json + live-curl vhosts + live-curl platform services
#            → ▷ _curl_vhost(): subprocess.run curl --resolve → ⎋ response
#            → ▷ _curl_platform_service(): curl via Docker DNS → ⎋ response
#            → ▷ _enrich_containers(containers, projects): ⊕ domains, uptime_human, restart_policy → ⎋ enriched
# region MODULE_CONTRACT
## @purpose  Live status-page HTTP server — aggregates node health (certs + containers + host),
##           renders Jinja2 HTML with 3 tables, exposes /health for CI post-deploy gate.
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
## @rationale Jinja2 replaces inline HTML for maintainability. Autoescape prevents XSS.
##            FileSystemLoader with Path(__file__).parent prevents deployment path breakage.
##            Staleness check alerts operator when cron export is stalled.
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
# endregion MODULE_CONTRACT

import http.server
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

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
    {"name": "LiteLLM", "url": None, "internal": "litellm:4000", "health_path": "/health/readiness"},
]


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


# region FUNC_load_status_metrics
def _load_status_metrics(path: str) -> dict:
    """Load status-metrics.json with schema_version check.

    # ▶ ┌path┐ → open JSON → ◇ schema_version >= 2? → return data
    #                                          └→ log warning, return empty
    # On failure → return empty containers/certs/projects/host with errors[]

    Returns full data dict as-is from file, or fallback structure on failure.
    """
    # Protective: Docker bind mount может создать path как директорию (P1)
    if not os.path.isfile(path):
        print(f"[IMP:8][status-page][load-metrics] Path is not a file: {path}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": [f"status-metrics.json not found or is a directory at {path}"],
        }

    try:
        with open(path) as f:
            data = json.load(f)

        # Schema version check
        sv = data.get("schema_version", 0)
        if sv < 2:
            print(f"[IMP:8][status-page][load-metrics] WARNING: schema_version={sv}, expected >=2", file=sys.stderr)
            # Still return data — older schema is partially compatible

        return data
    except Exception as e:
        print(f"[IMP:8][status-page][load-metrics] Failed to load status-metrics.json: {e}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": ["Failed to load status-metrics.json"],
        }


# endregion FUNC_load_status_metrics


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
        # --resolve bypasses Docker embedded DNS (127.0.0.11) which resolves *.tronyx.ru → localhost (P4)
        result = subprocess.run(
            [
                "curl",
                "-sSk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                str(timeout),
                "--resolve",
                f"{domain}:443:nginx",
                f"https://{domain}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_code = result.stdout.strip()
        if result.returncode == 0 and http_code.isdigit():
            code = int(http_code)
            # HTTP 200 = explicit success
            # HTTP 401/403 = auth required — service IS alive and responding,
            #   just needs credentials. Treat as PASS (not WARN/FAIL).
            return {
                "target": domain,
                "type": "vhost",
                "status": "PASS" if (code == 200 or code in (401, 403)) else "WARN",
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


# region FUNC_curl_platform_service
def _curl_platform_service(internal_url: str, health_path: str, timeout: int = PER_CHECK_TIMEOUT) -> dict:
    """Live-curl a platform service via Docker internal DNS.

    # ▶ ┌internal_url (e.g. "grafana:3000") + health_path ("/api/health")┐
    #    → subprocess.run curl (без --resolve, через Docker DNS)
    #    → ⎋ check result dict: {target, type, status, duration_ms, error}

    Unlike _curl_vhost, this does NOT use --resolve — Docker DNS resolves
    internal service names directly (grafana:3000 → container IP).
    Timeout: 5s per check.
    """
    start = time.monotonic()
    url = f"http://{internal_url}{health_path}"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sSk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                str(timeout),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_code = result.stdout.strip()
        # Extract hostname for the target field
        target_host = internal_url.split(":")[0]

        if result.returncode == 0 and http_code.isdigit():
            code = int(http_code)
            # Accept 200-399 as PASS (some services return 302, 301, etc.)
            # HTTP 401/403 = auth required — service IS alive and responding,
            #   just needs credentials. Treat as PASS (not WARN/FAIL).
            status = "PASS" if (200 <= code < 400 or code in (401, 403)) else "WARN"
            return {
                "target": target_host,
                "type": "platform_service",
                "status": status,
                "http_code": code,
                "duration_ms": elapsed_ms,
                "error": None,
            }
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": int(http_code) if http_code.isdigit() else 0,
            "duration_ms": elapsed_ms,
            "error": f"curl exit {result.returncode}: {result.stderr.strip()[:100]}",
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        target_host = internal_url.split(":")[0]
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": f"timeout after {timeout}s",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        target_host = internal_url.split(":")[0]
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": str(e),
        }


# endregion FUNC_curl_platform_service


# region FUNC_check_container
def _check_container(container: dict) -> dict:
    """Check a single container from status-metrics.json data. Returns check result.

    Status logic:
    - Running & healthy → PASS
    - Running & not healthy → WARN
    - Not running, exit_code=0 OR status_line contains "Exited (0)" → PASS (oneshot/init completed)
    - Not running, exit_code>0 OR status_line contains "Exited (non-zero)" → FAIL
    - Other non-running → FAIL
    """
    name = container.get("name", "unknown")  # Δ8: container_name → name
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


# region FUNC_compute_staleness
def _compute_staleness(generated_at: str | None) -> str | None:
    """Compute staleness of metrics data. Returns None if fresh, string description if stale.

    # ▶ ┌generated_at (ISO 8601)┐ → ◇ None? → ⎋ None
    #                               → ◇ age > 5 min? → ⎋ "Xm Ys" description
    #                               → ⎋ None (fresh)
    """
    if not generated_at:
        return None

    try:
        if generated_at.endswith("Z"):
            generated_at = generated_at[:-1] + "+00:00"
        gen_time = datetime.fromisoformat(generated_at)
        now = datetime.now(timezone.utc)
        delta = now - gen_time
        if delta.total_seconds() > 300:  # 5 minutes
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            return f"{minutes}m {seconds}s"
    except (ValueError, TypeError):
        pass

    return None


# endregion FUNC_compute_staleness


# region FUNC_enrich_projects
def _enrich_projects(projects: list[dict], certs: list[dict]) -> list[dict]:
    """Enrich project data with cert info for template rendering.

    # ▶ ┌projects[] + certs[]┐ → match cert by domain → ⊕ cert_issuer, cert_expiry, days_remaining, san fields
    #    → format code_size_bytes + image_size_bytes via _format_bytes → ⎋ enriched list

    Returns projects enriched with: cert_issuer, cert_expiry, days_remaining,
    san_full (all SANs), san_truncated (first 5), code_size_gb, image_size_gb (auto-formatted B/KB/MB/GB/TB).
    """
    # Build cert index: domain → cert info
    cert_index: dict[str, dict] = {}
    for cert in certs:
        for domain in cert.get("domains", []):
            cert_index[domain] = cert

    enriched = []
    for p in projects:
        domain = p.get("domain", "")
        cert = cert_index.get(domain, {})

        san_list = cert.get("san", [])
        san_full = ", ".join(san_list)
        san_truncated = ", ".join(san_list[:5])
        if len(san_list) > 5:
            san_truncated += " ..."

        enriched.append(
            {
                "name": p.get("name", ""),
                "domain": domain,
                "cert_issuer": cert.get("issuer", ""),
                "cert_expiry": cert.get("not_after_iso", ""),
                "days_remaining": cert.get("days_remaining"),
                "san_full": san_full,
                "san_truncated": san_truncated,
                "code_size_gb": _format_bytes(p.get("code_size_bytes", 0)),
                "image_size_gb": _format_bytes(p.get("docker_image_size_bytes", 0)),
            }
        )

    return enriched


# endregion FUNC_enrich_projects


# region FUNC_enrich_containers
def _enrich_containers(containers: list[dict], projects: list[dict] | None = None) -> list[dict]:
    """Enrich container data for template rendering.

    # ▶ ┌containers[] + projects[]┐ → ⊕ domain_map (container.name → project.domain heuristic)
    #    → ⊕ uptime_human (from started_at) → ⊕ restart_policy, exit_code_human → ⎋ enriched list

    Domain mapping is best-effort heuristic (Contract C3): container name matched against
    project name (exact or prefix). Infrastructure containers (nginx, postgres) have no domain — expected.
    """
    # Build domain map: container_name → domain (best-effort heuristic)
    domain_map: dict[str, str] = {}
    if projects:
        for p in projects:
            pname = p.get("name", "")
            pdomain = p.get("domain", "")
            if pname and pdomain:
                domain_map[pname] = pdomain

    enriched = []
    for c in containers:
        cname = c.get("name", "")
        mem_used = c.get("memory_usage_bytes", 0)
        mem_limit = c.get("memory_limit_bytes", 0)
        mem_used_pct = round(mem_used / mem_limit * 100, 1) if mem_limit > 0 else 0

        # Container → Domain mapping (heuristic)
        container_domains: list[str] = []
        for pname, pdomain in domain_map.items():
            if cname == pname or cname.startswith(f"{pname}-"):
                container_domains.append(pdomain)
                break  # First match wins

        # Uptime human-readable (from started_at ISO timestamp)
        started_at = c.get("started_at")
        uptime_human = _compute_uptime_human(started_at)

        # Exit code human-readable for exited containers
        exit_code = c.get("exit_code")
        exit_code_human = None
        if exit_code is not None and not c.get("running", True):
            exit_code_human = f"Exited ({exit_code})"

        enriched.append(
            {
                "name": cname,
                "domains": container_domains,
                "status": "running" if c.get("running") else "exited",
                "status_line": c.get("status_line", ""),
                "uptime_human": uptime_human,
                "restart_policy": c.get("restart_policy", ""),
                "exit_code": exit_code,
                "exit_code_human": exit_code_human,
                "cpu_percent": c.get("cpu_percent", 0.0),
                "memory_used": _format_bytes(mem_used),
                "memory_limit": _format_bytes(mem_limit),
                "memory_used_pct": mem_used_pct,
                "image": c.get("image", ""),
                "image_size_gb": _format_bytes(c.get("image_size_bytes", 0)),
            }
        )

    return enriched


# endregion FUNC_enrich_containers


# region FUNC_compute_uptime_human
def _compute_uptime_human(started_at: str | None) -> str:
    """Convert ISO 8601 started_at timestamp to human-readable uptime string.

    # ▶ ┌started_at ISO string┐ → ◇ None? → ⎋ "—"
    #                           → ◇ parse → ⊕ timedelta → format → ⎋ "3h 15m" or "< 1m"

    Returns human-readable duration like "3h 15m", "45m", "< 1m".
    Returns "—" if started_at is None or unparseable.
    """
    if not started_at:
        return "\u2014"

    try:
        # Handle Z suffix
        if started_at.endswith("Z"):
            started_at_clean = started_at[:-1] + "+00:00"
        else:
            started_at_clean = started_at

        started = datetime.fromisoformat(started_at_clean)
        now = datetime.now(timezone.utc)
        delta = now - started
        total_seconds = delta.total_seconds()

        if total_seconds < 0:
            return "\u2014"
        if total_seconds < 60:
            return "< 1m"

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "\u2014"


# endregion FUNC_compute_uptime_human


# region FUNC_format_bytes
## @purpose  Format bytes to human-readable string with auto unit selection (B/KB/MB/GB/TB)
## @io       ⇥ bytes_val: int, precision: int = 1 → ⎋ str
## @complexity  O(1) — <5 comparisons
def _format_bytes(bytes_val: int, precision: int = 1) -> str:
    """Format bytes to human-readable string with auto unit selection.

    # ▶ ┌bytes_val┐ → ◇ < 1024 → "N B"
    #                  → ◇ < 1024² → "N.M KB"
    #                  → ◇ < 1024³ → "N.M MB"
    #                  → ◇ < 1024⁴ → "N.M GB"
    #                  → ⎋ "N.M TB"

    Returns "0 B" for zero/None/negative values.
    """
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    if bytes_val < 1024:
        return f"{bytes_val} B"
    if bytes_val < 1024**2:
        return f"{bytes_val / 1024:.{precision}f} KB"
    if bytes_val < 1024**3:
        return f"{bytes_val / (1024**2):.{precision}f} MB"
    if bytes_val < 1024**4:
        return f"{bytes_val / (1024**3):.{precision}f} GB"
    return f"{bytes_val / (1024**4):.{precision}f} TB"


# endregion FUNC_format_bytes


# region FUNC_get_all_checks
def get_all_checks() -> dict:
    """Run all checks (vhosts + containers from metrics) with parallel fan-out. Returns aggregate dict."""
    start = time.monotonic()
    checks = []

    node_data = load_node_yaml(NODE_YAML_PATH)
    metrics = _load_status_metrics(STATUS_METRICS_JSON)
    freshness = metrics.get("generated_at")

    # ── Container checks (from status-metrics.json containers) ──
    containers = metrics.get("containers", [])
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

    # ── Platform service checks (live curl via Docker DNS, parallel fan-out) ──
    if PLATFORM_SERVICES:
        with ThreadPoolExecutor(max_workers=min(len(PLATFORM_SERVICES), 10)) as executor:
            futures = {
                executor.submit(_curl_platform_service, svc["internal"], svc["health_path"]): svc
                for svc in PLATFORM_SERVICES
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=TOTAL_TIMEOUT)
                    checks.append(result)
                except Exception as e:  # noqa: PERF203
                    svc = futures[future]
                    checks.append(
                        {
                            "target": svc["internal"].split(":")[0],
                            "type": "platform_service",
                            "status": "FAIL",
                            "http_code": 0,
                            "duration_ms": 0,
                            "error": f"future timeout: {e}",
                        }
                    )

    # ── Compute aggregate status ──
    all_pass = all(c["status"] == "PASS" for c in checks)
    duration_ms = int((time.monotonic() - start) * 1000)

    # Check staleness
    staleness = _compute_staleness(freshness)
    # If stale, overall status is still WARN (not FAIL) — data exists but may be old
    overall = "PASS" if all_pass else "FAIL"

    return {
        "status": overall,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": duration_ms,
        "metrics_freshness": freshness,
        "staleness": staleness,
        "checks": checks,
        # Full metrics data for HTML template
        "metrics": metrics,
    }


# endregion FUNC_get_all_checks


# ═══════════════════════════════════════════════════════════════════
# RENDER LAYER
# ═══════════════════════════════════════════════════════════════════


# region FUNC_render_html
def _render_html(data: dict) -> str:
    """Render status page using Jinja2 template.

    # ▶ ┌data dict┐ → extract/enrich sections → ⊕ ssl_min_days, platform_services, host_extra
    #    → Jinja2 render → ⎋ HTML string
    """
    metrics = data.get("metrics", {})

    # Enrich projects with cert info
    projects = _enrich_projects(
        metrics.get("projects", []),
        metrics.get("certs", []),
    )

    # Enrich containers (with project domain mapping)
    containers = _enrich_containers(
        metrics.get("containers", []),
        metrics.get("projects", []),
    )

    # Host data
    host = metrics.get("host", {})

    # Overall status from checks
    overall_status = data.get("status", "FAIL")

    # Errors from metrics export
    metric_errors = metrics.get("errors", [])

    # SSL Summary Banner — min days_remaining across all projects
    ssl_min_days = None
    for p in projects:
        dr = p.get("days_remaining")
        if dr is not None:
            if ssl_min_days is None or dr < ssl_min_days:
                ssl_min_days = dr

    # Backup status
    backup = metrics.get("backup", {})

    # Platform services (static list from module config)
    platform_services = PLATFORM_SERVICES

    # Enrich platform services with live-check results (from checks)
    platform_check_results: dict[str, str] = {}
    for check in data.get("checks", []):
        if check.get("type") == "platform_service":
            platform_check_results[check["target"]] = check

    for svc in platform_services:
        internal_name = svc["internal"].split(":")[0]  # e.g. "grafana" from "grafana:3000"
        check_data = platform_check_results.get(internal_name, {})
        svc["live_status"] = check_data.get("status", "UNKNOWN")
        svc["live_error"] = check_data.get("error")
        svc["live_duration_ms"] = check_data.get("duration_ms")

    context = {
        "node_name": NODE_NAME,
        "overall_status": overall_status,
        "ssl_min_days": ssl_min_days,
        "projects": projects,
        "containers": containers,
        "platform_services": platform_services,
        "host": {
            "disk_total_gb": host.get("disk_total_gb", 0),
            "disk_free_gb": host.get("disk_free_gb", 0),
            "disk_used_percent": host.get("disk_used_percent", 0.0),
            "uptime_seconds": host.get("uptime_seconds"),
            "load_1m": host.get("load_1m"),
            "load_5m": host.get("load_5m"),
            "load_15m": host.get("load_15m"),
            "docker_images_size_gb": host.get("docker_images_size_gb", 0.0),
            # NEW 047: Memory, swap, OS
            "memory_total_gb": host.get("memory_total_gb", 0),
            "memory_available_gb": host.get("memory_available_gb", 0),
            "memory_used_percent": host.get("memory_used_percent", 0.0),
            "swap_total_gb": host.get("swap_total_gb", 0),
            "swap_free_gb": host.get("swap_free_gb", 0),
            "swap_used_percent": host.get("swap_used_percent", 0.0),
            "os_name": host.get("os_name"),
            "kernel_version": host.get("kernel_version"),
            "arch": host.get("arch"),
        },
        "backup": backup,
        "errors": metric_errors,
        "staleness": data.get("staleness"),
        "generated_at": data.get("metrics_freshness") or "unknown",
    }

    template = _jinja_env.get_template("status.html")
    return template.render(**context)


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

    server = http.server.HTTPServer((LISTEN_HOST, LISTEN_PORT), StatusPageHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[IMP:9][status-page][main] Shutting down", file=sys.stderr)
        server.shutdown()
