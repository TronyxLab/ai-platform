# GREP_SUMMARY: status-page renderer context render-html jinja2 template platform-services live-status no-mutation
# STRUCTURE: ▶ render_html ┌data + jinja_env + platform_services + node_name┐ → enrich projects/containers/services (NEW lists)
#            → ⊕ context (host/backup/errors/staleness) → Jinja2 render status.html → ⎋ HTML
# region MODULE_CONTRACT
## @purpose  HTML rendering context for status-page — extracted from renderer.py render_html
##           (96 LOC, DevPlan 170 W7-E2). Builds Jinja2 context WITHOUT mutating any input
##           (platform services enrichment via _enrich_services_with_checks → new list).
## @scope    Consumed by app.py._render_html (node_name resolved in orchestrator)
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - jinja_env passed as parameter — renderer never imports app (no circular import, R4)
##   - Входные списки (projects/containers/platform_services) НЕ мутируются — W7-E2 fix
## @rationale  DevPlan 170 W7-E2 — context.py extracted from renderer.py; мутация входа
##            (renderer.py:276-281) устранена — _enrich_services_with_checks возвращает новый
##            список; контракт render_html(data, jinja_env, platform_services, node_name) сохранён.
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from renderer.py; mutation fix
# endregion MODULE_CONTRACT

from typing import cast

from collectors.checks.http import (  # pyright: ignore[reportImplicitRelativeImport]
    CheckResult,
)
from collectors.checks.platform import (  # pyright: ignore[reportImplicitRelativeImport]
    PlatformService,
)
from collectors.config import (  # pyright: ignore[reportImplicitRelativeImport]
    CertEntry,
    ContainerEntry,
    ProjectMetricsEntry,
)
from jinja2 import Environment

from .enrich import enrich_containers, enrich_projects
from .enrich import enrich_services_with_checks as _enrich_services_with_checks


# region FUNC_render_html
def render_html(
    data: dict[str, object],
    jinja_env: Environment,
    platform_services: list[PlatformService],
    node_name: str,
) -> str:
    """Render status page using Jinja2 template.

    # ▶ ┌data dict┐ → extract/enrich sections (NEW lists, no mutation) → ⊕ ssl_min_days, platform_services, host_extra
    #    → Jinja2 render → ⎋ HTML string

    jinja_env is passed as a parameter (not imported from app) to avoid circular imports (R4).
    """
    metrics_raw = data.get("metrics")
    metrics = cast("dict[str, object]", metrics_raw) if isinstance(metrics_raw, dict) else cast("dict[str, object]", {})

    # Enrich projects with cert info (new list — input untouched)
    projects_raw: object = metrics.get("projects")
    certs_raw: object = metrics.get("certs")
    projects = enrich_projects(
        cast("list[ProjectMetricsEntry]", projects_raw) if isinstance(projects_raw, list) else [],
        cast("list[CertEntry]", certs_raw) if isinstance(certs_raw, list) else [],
    )

    # Enrich containers (with project domain mapping; new list — input untouched)
    containers_raw: object = metrics.get("containers")
    containers = enrich_containers(
        cast("list[ContainerEntry]", containers_raw) if isinstance(containers_raw, list) else [],
        cast("list[ProjectMetricsEntry]", projects_raw) if isinstance(projects_raw, list) else [],
    )

    # Host data
    host_raw: object = metrics.get("host")
    host = cast("dict[str, object]", host_raw) if isinstance(host_raw, dict) else cast("dict[str, object]", {})

    # Overall status from checks
    overall_status = data.get("status", "FAIL")

    # Errors from metrics export
    errors_raw: object = metrics.get("errors")
    metric_errors = cast("list[object]", errors_raw) if isinstance(errors_raw, list) else []

    # SSL Summary Banner — min days_remaining across all projects
    ssl_min_days = None
    for p in projects:
        dr_raw = p.get("days_remaining")
        dr = cast("int | None", dr_raw) if dr_raw is not None else None
        if dr is not None and (ssl_min_days is None or dr < ssl_min_days):
            ssl_min_days = dr

    # Backup status
    backup_raw: object = metrics.get("backup")
    backup = cast("dict[str, object]", backup_raw) if isinstance(backup_raw, dict) else cast("dict[str, object]", {})

    # Platform services — live-check enrichment (W7-E2: NEW list, вход НЕ мутируется)
    checks_raw: object = data.get("checks")
    checks_list = cast("list[CheckResult]", checks_raw) if isinstance(checks_raw, list) else []
    platform_services_enriched = _enrich_services_with_checks(platform_services, checks_list)

    context: dict[str, object] = {
        "node_name": node_name,
        "overall_status": overall_status,
        "ssl_min_days": ssl_min_days,
        "projects": projects,
        "containers": containers,
        "platform_services": platform_services_enriched,
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
