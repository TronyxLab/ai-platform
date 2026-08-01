#!/usr/bin/env python3
# GREP_SUMMARY: status-page renderer enrich-projects enrich-containers compute-uptime format-bytes render-html jinja2 context
# STRUCTURE: ▶ _format_bytes (auto-unit) → ▶ _compute_uptime_human → ▶ _enrich_projects (cert match) → ▶ _enrich_containers (domain map)
#            → ▶ render_html(data, jinja_env, platform_services, node_name) → ⊕ context → Jinja2 render → ⎋ HTML
# region MODULE_CONTRACT
## @purpose  Rendering layer for the status-page module, extracted from app.py (DevPlan 117 G T55).
##           Pure functions — jinja_env, platform_services and node_name are passed as parameters
##           by the orchestrator (app.py) to avoid circular imports (risk R4 mitigation).
## @scope    Consumed by core/modules/status-page/app.py. Runs inside the status-page container.
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - jinja_env passed as parameter — renderer never imports app (no circular import, R4)
##   - _format_bytes auto-unit B/KB/MB/GB/TB, "0 B" for zero/None/negative
##   - _compute_uptime_human returns "—" for None/unparseable, "< 1m" under 60s
##   - _enrich_containers: best-effort domain heuristic (exact or prefix match)
##   - _render_html: enriches services with live-status from checks
## @rationale  DevPlan 117 G T55 — extracted verbatim from app.py renderer (~270 LOC) with all
##            LDD logs and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T55 — extracted from app.py
# endregion MODULE_CONTRACT

from datetime import datetime, timezone


# region FUNC_enrich_projects
def enrich_projects(projects: list[dict], certs: list[dict]) -> list[dict]:
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
                "code_size_gb": format_bytes(p.get("code_size_bytes", 0)),
                "image_size_gb": format_bytes(p.get("docker_image_size_bytes", 0)),
            }
        )

    return enriched


# endregion FUNC_enrich_projects


# region FUNC_enrich_containers
def enrich_containers(containers: list[dict], projects: list[dict] | None = None) -> list[dict]:
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
        uptime_human = compute_uptime_human(started_at)

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
                "memory_used": format_bytes(mem_used),
                "memory_limit": format_bytes(mem_limit),
                "memory_used_pct": mem_used_pct,
                "image": c.get("image", ""),
                "image_size_gb": format_bytes(c.get("image_size_bytes", 0)),
            }
        )

    return enriched


# endregion FUNC_enrich_containers


# region FUNC_compute_uptime_human
def compute_uptime_human(started_at: str | None) -> str:
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
        started_at_clean = started_at[:-1] + "+00:00" if started_at.endswith("Z") else started_at

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
def format_bytes(bytes_val: int, precision: int = 1) -> str:
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


# region FUNC_render_html
def render_html(
    data: dict,
    jinja_env,
    platform_services: list[dict],
    node_name: str,
) -> str:
    """Render status page using Jinja2 template.

    # ▶ ┌data dict┐ → extract/enrich sections → ⊕ ssl_min_days, platform_services, host_extra
    #    → Jinja2 render → ⎋ HTML string

    jinja_env is passed as a parameter (not imported from app) to avoid circular imports (R4).
    """
    metrics = data.get("metrics", {})

    # Enrich projects with cert info
    projects = enrich_projects(
        metrics.get("projects", []),
        metrics.get("certs", []),
    )

    # Enrich containers (with project domain mapping)
    containers = enrich_containers(
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
        if dr is not None and (ssl_min_days is None or dr < ssl_min_days):
            ssl_min_days = dr

    # Backup status
    backup = metrics.get("backup", {})

    # Platform services (static list from module config)
    platform_services = platform_services

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
        "node_name": node_name,
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

    template = jinja_env.get_template("status.html")
    return template.render(**context)


# endregion FUNC_render_html
