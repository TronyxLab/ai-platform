#!/usr/bin/env python3
# GREP_SUMMARY: monitoring alert-rules template render alerting-enabled PROJECT variable
# STRUCTURE: ▶ generate_alert_rules(config, template_path, output_dir) → ◇ alerting_enabled? → ◇ template exists? → ⊕ render → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Prometheus alert rules generator — extracted from monitoring_config_renderer.py
##           (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - Skips if alerting_enabled is False (status="noop")
##   - Skips if template file missing (status="skipped")
##   - Single variable: PROJECT for substitution (strict {{UPPER_SNAKE}}, no sed)
## @rationale  DevPlan 117 G T54 — extracted verbatim (generate_alert_rules, ~49 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
        TemplateError,
    )
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        render_template_file as _render_template,
    )
    from monitoring.constants import (  # pyright: ignore[reportImplicitRelativeImport]
        ALERT_RULES_DIR,
        DEFAULT_ALERT_RULES_TEMPLATE,
    )
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    # W2 T2.6 (DevPlan 136, латентный класс A): канон config_renderer.py — корень репо
    # (fallback добавляет И core/internal/ для top-level monitoring-импортов, И корень
    # для core.internal.* — единый документированный канон self-bootstrap).
    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        ProjectMonitoringConfig,
        RenderResult,
        TemplateError,
    )
    from monitoring.config_renderer import (  # pyright: ignore[reportImplicitRelativeImport]
        render_template_file as _render_template,
    )
    from monitoring.constants import (  # pyright: ignore[reportImplicitRelativeImport]
        ALERT_RULES_DIR,
        DEFAULT_ALERT_RULES_TEMPLATE,
    )

logger = logging.getLogger(__name__)


# region FUNC_generate_alert_rules
def generate_alert_rules(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Prometheus alert rules YAML from template.

    ## @purpose  Render project alert rules via template_engine.render_template (native).
    ##           Skips if alerting_enabled is False or template missing.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ template_path: Path — alert rules template (default: platform-relative)
    ##   ⇥ output_dir: Path — output directory (default: /opt/prometheus/rules)
    ##   ⎋ RenderResult — outcome with status
    ## @complexity O(T + V) where T = template size, V = variables
    ## @invariants
    ##   - Skips if alerting_enabled is False (status="noop")
    ##   - Skips if template file missing (status="skipped", log IMP:6)
    ##   - Output directory created if missing
    ##   - Single variable: PROJECT for substitution
    """
    if not config.alerting_enabled:
        logger.info("[IMP:8][alerting] Alerting disabled for %s — skipping alert rules", config.project_name)
        return RenderResult(component="alerting", status="noop", detail="alerting_enabled=False")

    tmpl = template_path or (config.platform_root / DEFAULT_ALERT_RULES_TEMPLATE)
    if not tmpl.exists():
        logger.info("[IMP:6][alerting] Alert rules template not found: %s — skipping", tmpl)
        return RenderResult(component="alerting", status="skipped", detail=f"template not found: {tmpl}")

    out_dir = output_dir or ALERT_RULES_DIR
    output_file = out_dir / f"{config.project_name}-alerts.yml"

    try:
        _render_template(
            template_path=tmpl,
            output_path=output_file,
            variables={"PROJECT": config.project_name},
        )
        logger.info("[IMP:9][alerting] Alert rules generated: %s", output_file)
        return RenderResult(component="alerting", status="created", output_path=output_file)
    except (OSError, TemplateError) as e:
        logger.info("[IMP:6][alerting] Alert rules generation failed for %s: %s", config.project_name, e)
        return RenderResult(component="alerting", status="failed", detail=str(e))


# endregion FUNC_generate_alert_rules
