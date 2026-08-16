# GREP_SUMMARY: status-page collectors platform PLATFORM_SERVICES platform-services port-constants dns-probe disabled check-platform-service
# STRUCTURE: ▶ PLATFORM_DOMAIN (env) → ▶ _LOCAL_PORT_* (TRAP cross-layer) → ▶ PLATFORM_SERVICES (6 static entries)
#            → ▶ check_platform_service (DNS probe → DISABLED | curl) → ⎋ check dict
# region MODULE_CONTRACT
## @purpose  Platform-services domain data + DNS-probe check — extracted from app.py:119-141 and
##           collectors.py _check_platform_service (DevPlan 170 W7-E2). Static service table (C2)
##           + DISABLED logic for not-deployed services (S-DNS A).
## @scope    Consumed by collectors/aggregate.py; PLATFORM_SERVICES re-exported via app.py
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - PLATFORM_SERVICES: 6 entries (Grafana, Prometheus, Loki, Hermes, Langfuse, LiteLLM)
##   - check_platform_service: socket.gethostbyname pre-probe — unresolved → DISABLED (T1.1, S-DNS A)
##   - Edge case (accepted, S-DNS A): probe succeeds but curl exit 6/7 → FAIL (same surface)
## @rationale  DevPlan 170 W7-E2 — доменные данные (порт-константы + сервис-таблица) перенесены
##            из app.py (домен ≠ маршрутизация); TRAP cross-layer сохранён при константах.
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from app.py/collectors.py
# endregion MODULE_CONTRACT

import os
import socket
import sys
from typing import TypedDict

from .http import CheckResult
from .http import curl_platform_service as _curl_platform_service

# ═══════════════════════════════════════════════════════════════════
# PLATFORM SERVICES (static list — Contract C2)
# ═══════════════════════════════════════════════════════════════════

PLATFORM_DOMAIN = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")


# region DATA_PlatformService
class PlatformService(TypedDict, total=False):
    """Запись платформенного сервиса (Contract C2): name/url/internal/health_path.

    ## @purpose  Статическая таблица сервисов платформы для Platform Services Table:
    ##            name (display), url (external link; None = internal only), internal
    ##            (Docker DNS), health_path (curl path).
    """

    name: str
    url: str | None
    internal: str
    health_path: str


# endregion DATA_PlatformService


# Static list of platform services for the Platform Services Table.
# Each entry: name (display), url (external link), internal (Docker DNS), health_path (curl path).
# LiteLLM has no external URL (no nginx vhost) — displayed as "internal only".
# ⚠️ TRAP[DECISION] · 2026-08-14 · — · Порт-дубли SoT platform-infra.yaml — cross-layer
# · Rejected: импорт core/internal/shared/platform_ports из модуля
# · Reason: core/AGENTS.md Cross-layer — modules НЕ импортируют core/internal (см. TRAP app.py:47-52);
# ·   модульный образ python:3.12-alpine без core/. Значения — зеркало SoT
# ·   (container-порты: grafana 3000, prometheus 9090, loki 3100, hermes 9119, langfuse 3000,
# ·   litellm 4000); parity-гейт test_gate_port_parity сверяет core/internal реестр, а этот
# ·   локальный дубль консолидируется при появлении модульного механизма инъекции портов.
# · Rev: если появится модульный конфиг-механизм (env/compose) → убрать локальные константы.
_LOCAL_PORT_GRAFANA: int = 3000
_LOCAL_PORT_PROMETHEUS: int = 9090
_LOCAL_PORT_LOKI: int = 3100
_LOCAL_PORT_HERMES: int = 9119
_LOCAL_PORT_LANGFUSE: int = 3000
_LOCAL_PORT_LITELLM: int = 4000

PLATFORM_SERVICES: list[PlatformService] = [
    {
        "name": "Grafana",
        "url": f"https://grafana.{PLATFORM_DOMAIN}",
        "internal": f"grafana:{_LOCAL_PORT_GRAFANA}",
        "health_path": "/api/health",
    },
    {
        "name": "Prometheus",
        "url": f"https://prometheus.{PLATFORM_DOMAIN}",
        "internal": f"prometheus:{_LOCAL_PORT_PROMETHEUS}",
        "health_path": "/-/healthy",
    },
    {
        "name": "Loki",
        "url": f"https://loki.{PLATFORM_DOMAIN}",
        "internal": f"loki:{_LOCAL_PORT_LOKI}",
        "health_path": "/ready",
    },
    {
        "name": "Hermes",
        "url": f"https://hermes.{PLATFORM_DOMAIN}",
        "internal": f"hermes-agent:{_LOCAL_PORT_HERMES}",
        "health_path": "/",
    },
    {
        "name": "Langfuse",
        "url": f"https://langfuse.{PLATFORM_DOMAIN}",
        "internal": f"langfuse:{_LOCAL_PORT_LANGFUSE}",
        "health_path": "/api/public/health",
    },
    {
        "name": "LiteLLM",
        "url": None,
        "internal": f"litellm:{_LOCAL_PORT_LITELLM}",
        "health_path": "/health/liveliness",
    },
]


# region FUNC_check_platform_service
def check_platform_service(internal_url: str, health_path: str, timeout: int = 5) -> CheckResult:
    """Check a platform service: DNS probe → DISABLED if unresolved, else curl (S-DNS A).

    # ▶ ┌internal_url + health_path┐ → socket.gethostbyname(host)
    #   → ◇ OSError → ⎋ DISABLED (service not deployed, DNS unresolved)
    #   → ◇ resolved → ▶ _curl_platform_service (existing flow)

    DevPlan 158 W1 T1.1: pre-check via socket.gethostbyname(internal_host) before invoking curl.
    Not-deployed services (Docker DNS returns nothing) → DISABLED, not FAIL. This prevents
    false FAIL on nodes that don't run all platform services (e.g. asi-team-vps without Grafana).

    Edge case (accepted, S-DNS A): if probe succeeds (search-domain / wildcard resolves) but
    curl returns exit 6/7 — service stays FAIL. Same surface as curl-exit-6 classification.
    """
    host = internal_url.split(":", maxsplit=1)[0]
    try:
        socket.gethostbyname(host)  # DNS probe — typically <5ms
        print(f"[IMP:9][collectors][dns-probe] {host} resolved → proceed to curl", file=sys.stderr)
    except OSError:
        print(
            f"[IMP:7][collectors][dns-probe] {host} unresolved → DISABLED (not deployed)",
            file=sys.stderr,
        )
        return {
            "target": host,
            "type": "platform_service",
            "status": "DISABLED",
            "http_code": 0,
            "duration_ms": 0,
            "error": "service not deployed (DNS unresolved)",
        }
    return _curl_platform_service(internal_url, health_path, timeout)


# endregion FUNC_check_platform_service
