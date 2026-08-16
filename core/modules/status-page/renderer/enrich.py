# GREP_SUMMARY: status-page renderer enrich enrich-projects enrich-containers enrich-services cert domain-map memory-bytes
# STRUCTURE: ▶ enrich_projects (cert match → cert_issuer/expiry/SAN + size format) → ▶ enrich_containers (domain heuristic + uptime + mem)
#            → ▶ enrich_services_with_checks (NEW list — вход НЕ мутируется) → ⎋ enriched lists
# region MODULE_CONTRACT
## @purpose  Enrichment layer of status-page renderer (extracted from renderer.py, DevPlan 170 W7-E2).
##           Pure functions — all enrichments return NEW lists, inputs never mutated
##           (incl. platform services live-status enrichment, W7-E2 fix).
## @scope    Consumed by renderer/context.py (render_html)
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - enrich_services_with_checks: входной platform_services НЕ мутируется — возвращает новый список
##   - _enrich_containers: best-effort domain heuristic (exact or prefix match)
##   - _enrich_containers: memory_limit_bytes + memory_usage_bytes passed raw for P6 condition (T2.3)
## @rationale  DevPlan 170 W7-E2 — render_html 96 LOC мутировал вход (renderer.py:276-281) →
##            обогащение сервисов вынесено в enrich_services_with_checks, возвращающее НОВЫЙ
##            список; глобальная константа app.PLATFORM_SERVICES больше не мутируется.
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

from .format import compute_uptime_human, format_bytes

_SAN_TRUNCATE_MAX: int = 5  # сколько SAN показывать до "..."


# region FUNC_enrich_projects
def enrich_projects(projects: list[ProjectMetricsEntry], certs: list[CertEntry]) -> list[dict[str, object]]:
    """Enrich project data with cert info for template rendering.

    # ▶ ┌projects[] + certs[]┐ → match cert by domain → ⊕ cert_issuer, cert_expiry, days_remaining, san fields
    #    → format code_size_bytes + image_size_bytes via format_bytes → ⎋ enriched list

    Returns projects enriched with: cert_issuer, cert_expiry, days_remaining,
    san_full (all SANs), san_truncated (first 5), code_size_gb, image_size_gb (auto-formatted B/KB/MB/GB/TB).
    """
    # Build cert index: domain → cert info
    cert_index: dict[str, CertEntry] = {}
    for cert in certs:
        for domain in cert.get("domains", []):
            cert_index[domain] = cert

    enriched: list[dict[str, object]] = []
    for p in projects:
        domain = str(p.get("domain", ""))
        cert = cert_index.get(domain)

        san_list = cert.get("san", []) if cert is not None else []
        san_full = ", ".join(san_list)
        san_truncated = ", ".join(san_list[:_SAN_TRUNCATE_MAX])
        if len(san_list) > _SAN_TRUNCATE_MAX:
            san_truncated += " ..."

        enriched.append({
            "name": str(p.get("name", "")),
            "domain": domain,
            "cert_issuer": cert.get("issuer", "") if cert is not None else "",
            "cert_expiry": cert.get("not_after_iso", "") if cert is not None else "",
            "days_remaining": cert.get("days_remaining") if cert is not None else None,
            "san_full": san_full,
            "san_truncated": san_truncated,
            "code_size_gb": format_bytes(cast("int", p.get("code_size_bytes", 0))),
            "image_size_gb": format_bytes(cast("int", p.get("docker_image_size_bytes", 0))),
        })

    return enriched


# endregion FUNC_enrich_projects


# region FUNC_enrich_containers
def enrich_containers(
    containers: list[ContainerEntry], projects: list[ProjectMetricsEntry] | None = None
) -> list[dict[str, object]]:
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

    enriched: list[dict[str, object]] = []
    for c in containers:
        cname = str(c.get("name", ""))
        mem_used = cast("int", c.get("memory_usage_bytes", 0))
        mem_limit = cast("int", c.get("memory_limit_bytes", 0))
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

        enriched.append({
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
            # DevPlan 158 W2 T2.3 (P6): raw bytes for template condition (limit > 0 → show %)
            "memory_limit_bytes": mem_limit,
            "memory_usage_bytes": mem_used,
            "image": c.get("image", ""),
            "image_size_gb": format_bytes(c.get("image_size_bytes", 0)),
        })

    return enriched


# endregion FUNC_enrich_containers


# region FUNC_enrich_services_with_checks
def enrich_services_with_checks(
    platform_services: list[PlatformService], checks: list[CheckResult]
) -> list[dict[str, object]]:
    """Return NEW list of platform services enriched with live-check status (no input mutation).

    # ▶ ┌services[] + checks[]┐ → ⊕ platform_check_results (target → check)
    #    → for svc: ⊕ live_status/live_error/live_duration_ms (dict-spread, shallow copy) → ⎋ new list

    W7-E2: render_html больше НЕ мутирует входной platform_services (ранее svc["live_status"]=...
    на оригинальных dict — глобальная константа app.PLATFORM_SERVICES мутировалась между рендерами).
    """
    platform_check_results: dict[str, CheckResult] = {}
    for check in checks:
        if check.get("type") == "platform_service":
            platform_check_results[cast("str", check.get("target"))] = check

    enriched: list[dict[str, object]] = []
    for svc in platform_services:
        internal_name = (svc.get("internal") or "").split(":")[0]  # e.g. "grafana" from "grafana:3000"
        check_data = platform_check_results.get(internal_name)
        enriched.append({
            **svc,  # shallow copy — все исходные ключи (name/url/internal/health_path)
            "live_status": check_data.get("status", "UNKNOWN") if check_data is not None else "UNKNOWN",
            "live_error": check_data.get("error") if check_data is not None else None,
            "live_duration_ms": check_data.get("duration_ms") if check_data is not None else None,
        })
    return enriched


# endregion FUNC_enrich_services_with_checks
