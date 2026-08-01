#!/usr/bin/env python3
# GREP_SUMMARY: monitoring service-reload prometheus loki HTTP-POST reload non-fatal
# STRUCTURE: ▶ reload_monitoring_services() → ⊕ POST /-/reload (Prometheus) → ⊕ POST /reload (Loki) → ⎋ list[RenderResult]
# region MODULE_CONTRACT
## @purpose  Monitoring service reload (Prometheus + Loki) — extracted from
##           monitoring_config_renderer.py (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Prometheus: POST http://prometheus:9090/-/reload
##   - Loki: POST http://loki:3100/reload
##   - Each failure is logged and continued
## @rationale  DevPlan 117 G T54 — extracted verbatim (reload_monitoring_services, ~41 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring_config_renderer import RenderResult

    from monitoring.constants import LOKI_RELOAD_URL, PROMETHEUS_RELOAD_URL
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    from monitoring_config_renderer import RenderResult

    from monitoring.constants import LOKI_RELOAD_URL, PROMETHEUS_RELOAD_URL

logger = logging.getLogger(__name__)


# region FUNC_reload_monitoring_services
def reload_monitoring_services() -> list[RenderResult]:
    """HTTP POST reload Prometheus and Loki.

    ## @purpose  Send reload signals to Prometheus and Loki after config changes.
    ##           Each call is non-fatal — failures logged, continue to next.
    ## @io
    ##   ⎋ list[RenderResult] — one result per service
    ## @complexity O(1) per service (2 HTTP calls)
    ## @invariants
    ##   - Prometheus: POST http://prometheus:9090/-/reload
    ##   - Loki: POST http://loki:3100/reload
    ##   - Each failure is logged and continued
    """
    results: list[RenderResult] = []

    # Prometheus reload
    try:
        req = urllib.request.Request(PROMETHEUS_RELOAD_URL, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — internal Prometheus API (localhost)
            logger.info("[IMP:8][reload] Prometheus reload: HTTP %s", resp.status)
            results.append(RenderResult(component="reload", status="created", detail=f"Prometheus HTTP {resp.status}"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.info("[IMP:6][reload] Prometheus reload failed: %s", e)
        results.append(RenderResult(component="reload", status="failed", detail=f"Prometheus: {e}"))

    # Loki reload
    try:
        req = urllib.request.Request(LOKI_RELOAD_URL, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — internal Loki API (localhost)
            logger.info("[IMP:8][reload] Loki reload: HTTP %s", resp.status)
            results.append(RenderResult(component="reload", status="created", detail=f"Loki HTTP {resp.status}"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.info("[IMP:6][reload] Loki reload failed: %s", e)
        results.append(RenderResult(component="reload", status="failed", detail=f"Loki: {e}"))

    return results


# endregion FUNC_reload_monitoring_services
