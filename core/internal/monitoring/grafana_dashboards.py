#!/usr/bin/env python3
# GREP_SUMMARY: monitoring grafana-dashboards project-template render dashboard-enabled
# STRUCTURE: ▶ generate_grafana_dashboard(config, template_path, output_dir) → ◇ dashboard_enabled? → ◇ template exists? → ⊕ render → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Grafana dashboard generator — extracted from monitoring_config_renderer.py (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Skips if dashboard_enabled is False (status="noop")
##   - Skips if template file missing (status="skipped")
##   - Native render: strict {{UPPER_SNAKE}} substitution via template_engine (no sed fallback)
## @rationale  DevPlan 117 G T54 — extracted verbatim (generate_grafana_dashboard, ~53 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring_config_renderer import (
        ProjectMonitoringConfig,
        RenderResult,
        TemplateError,
    )
    from monitoring_config_renderer import (
        render_template_file as _render_template,
    )

    from monitoring.constants import (
        DEFAULT_GRAFANA_DASHBOARDS_DIR,
        DEFAULT_GRAFANA_TEMPLATE,
    )
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    from monitoring_config_renderer import (
        ProjectMonitoringConfig,
        RenderResult,
        TemplateError,
    )
    from monitoring_config_renderer import (
        render_template_file as _render_template,
    )

    from monitoring.constants import (
        DEFAULT_GRAFANA_DASHBOARDS_DIR,
        DEFAULT_GRAFANA_TEMPLATE,
    )

logger = logging.getLogger(__name__)


# region FUNC_generate_grafana_dashboard
def generate_grafana_dashboard(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Grafana dashboard JSON from template.

    ## @purpose  Render project dashboard via template_engine.render_template (native).
    ##           Skips if dashboard_enabled is False or template missing.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ template_path: Path — dashboard template JSON (default: platform-relative)
    ##   ⇥ output_dir: Path — output directory (default: /opt/grafana/provisioning/dashboards)
    ##   ⎋ RenderResult — outcome with status
    ## @complexity O(T + V) where T = template size, V = variables
    ## @invariants
    ##   - Skips if dashboard_enabled is False (status="noop")
    ##   - Skips if template file missing (status="skipped", log IMP:6)
    ##   - Output directory created if missing
    ##   - Native render: strict {{UPPER_SNAKE}} substitution (no sed fallback)
    """
    if not config.dashboard_enabled:
        logger.info("[IMP:8][grafana] Dashboard disabled for %s — skipping", config.project_name)
        return RenderResult(component="grafana", status="noop", detail="dashboard_enabled=False")

    tmpl = template_path or (config.platform_root / DEFAULT_GRAFANA_TEMPLATE)
    if not tmpl.exists():
        logger.info("[IMP:6][grafana] Dashboard template not found: %s — skipping", tmpl)
        return RenderResult(component="grafana", status="skipped", detail=f"template not found: {tmpl}")

    out_dir = output_dir or DEFAULT_GRAFANA_DASHBOARDS_DIR
    dash_file = out_dir / f"{config.project_name}.json"

    try:
        _render_template(
            template_path=tmpl,
            output_path=dash_file,
            variables={
                "PROJECT": config.project_name,
                "TYPE": config.project_type,
                "NODE": config.node_name,
            },
        )
        logger.info("[IMP:9][grafana] Dashboard generated: %s", dash_file)
        return RenderResult(component="grafana", status="created", output_path=dash_file)
    except (OSError, TemplateError) as e:
        logger.info("[IMP:6][grafana] Dashboard generation failed for %s: %s", config.project_name, e)
        return RenderResult(component="grafana", status="failed", detail=str(e))


# endregion FUNC_generate_grafana_dashboard
