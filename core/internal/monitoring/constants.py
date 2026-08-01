#!/usr/bin/env python3
# GREP_SUMMARY: monitoring constants alert-rules-dir loki-runtime prometheus-targets grafana-dashboards templates defaults catalog reload-urls langfuse-api
# STRUCTURE: ┌module-level path/URL constants consumed by monitoring generators┐
# region MODULE_CONTRACT
## @purpose  Shared constants for the monitoring generator subpackage (DevPlan 117 G T54).
##           Single source for all paths, URLs and template locations consumed by
##           core/internal/monitoring/*.py generators and monitoring_config_renderer.py.
## @scope    Imported by monitoring/ generators + monitoring_config_renderer.py.
##           No dependencies — pure path/URL literals (layer-safe).
## @invariants
##   - No core/internal imports (pure constants, importable at any time)
##   - Values match the pre-decomposition module-level constants in monitoring_config_renderer.py
## @rationale  DevPlan 117 G T54 R5 — constants extracted to monitoring/constants.py so all
##            generators share one source (no duplication across 7 generator modules).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

from pathlib import Path

# ── module-level constants ──────────────────────────────────────────────────

# Alert rules output dir on VPS (matching original lines 371, 379)
ALERT_RULES_DIR = Path("/opt/prometheus/rules")

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
PROMETHEUS_RELOAD_URL = "http://prometheus:9090/-/reload"
LOKI_RELOAD_URL = "http://loki:3100/reload"

# Langfuse API endpoint
LANGFUSE_API_URL = "http://langfuse:3000/api/public/projects"
