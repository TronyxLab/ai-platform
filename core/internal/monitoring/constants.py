#!/usr/bin/env python3
# GREP_SUMMARY: monitoring constants alert-rules-dir loki-runtime prometheus-targets grafana-dashboards templates defaults catalog reload-urls langfuse-api
# STRUCTURE: ┌module-level path/URL constants consumed by monitoring generators┐
# region MODULE_CONTRACT
## @purpose  Shared constants for the monitoring generator subpackage (DevPlan 117 G T54).
##           Single source for all paths, URLs and template locations consumed by
##           core/internal/monitoring/*.py generators and monitoring_config_renderer.py.
## @scope    Imported by monitoring/ generators + monitoring_config_renderer.py.
##           Depends only on shared/platform_ports (leaf, no domain imports) — layer-safe.
## @invariants
##   - No domain core/internal imports (только leaf shared/platform_ports — layer-safe)
##   - Values match the pre-decomposition module-level constants in monitoring_config_renderer.py
## @rationale  DevPlan 117 G T54 R5 — constants extracted to monitoring/constants.py so all
##            generators share one source (no duplication across 7 generator modules).
##            DevPlan 170 W1-A3: порты → shared/platform_ports (литералы 9090/3000 удалены).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
## @changes  2026-08-14 · DevPlan 170 W1-A3 — порты из shared/platform_ports
# endregion MODULE_CONTRACT

from pathlib import Path

# DevPlan 170 W1-A3: порты из единого реестра shared/platform_ports (зеркало SoT
# platform-infra.yaml) — литералы {9090, 3000} удалены (ранее дублировали SoT).
# REF-0010 (2026-08-24): prometheus_rules_dir_sot — канонический резолвер (SoT-дефолт
# /opt/platform/prometheus-rules); прежний prometheus_rules_dir() имел fallback
# /opt/prometheus/rules, расходящийся с compose-mount → рендер мимо монтированного
# каталога = silent alert loss (AI-0004).
from core.internal.shared.deploy_paths import prometheus_rules_dir_sot
from core.internal.shared.platform_ports import (
    PLATFORM_PORT_LANGFUSE,
    PLATFORM_PORT_PROMETHEUS,
)

# ── module-level constants ──────────────────────────────────────────────────

# Alert rules output dir on VPS (matching original lines 371, 379)
# REF-0010/AI-0004: единый SoT с compose-mount (${PROMETHEUS_RULES_DIR:-/opt/platform/
# prometheus-rules}:/opt/prometheus/rules в monitoring/docker-compose.base.yml) и
# platform-infra.yaml env_defaults. Path-parity гейт: tests/unit/test_ref0010_monitoring_honesty.py
ALERT_RULES_DIR = prometheus_rules_dir_sot()

# Default Loki runtime config path relative to platform root
DEFAULT_LOKI_RUNTIME_CONFIG = "core/modules/logging/config/loki-runtime-config.yml"

# Default Prometheus targets dir relative to platform root
DEFAULT_PROMETHEUS_TARGETS_DIR = "prometheus-targets"

# Default grafana dashboards output dir (VPS path)
DEFAULT_GRAFANA_DASHBOARDS_DIR = Path("/opt/grafana/provisioning/dashboards")

# Template paths relative to platform root
DEFAULT_GRAFANA_TEMPLATE = "core/modules/monitoring/config/dashboards/project-template.json"
DEFAULT_ALERT_RULES_TEMPLATE = "core/modules/monitoring/config/alert-rules.yml"

# L1 defaults path relative to platform root
DEFAULT_L1_DEFAULTS = "core/modules/monitoring/defaults.yaml"

# Catalog script relative to platform root
CATALOG_SCRIPT = "core/internal/catalog/generate-catalog.sh"

# Service reload endpoints
PROMETHEUS_RELOAD_URL = f"http://prometheus:{PLATFORM_PORT_PROMETHEUS}/-/reload"
LOKI_RELOAD_URL = "http://loki:3100/reload"

# Langfuse API endpoint
LANGFUSE_API_URL = f"http://langfuse:{PLATFORM_PORT_LANGFUSE}/api/public/projects"
