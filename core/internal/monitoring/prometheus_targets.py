#!/usr/bin/env python3
# GREP_SUMMARY: monitoring prometheus-targets file-sd target-json metrics-enabled labels
# STRUCTURE: ▶ generate_prometheus_target(config, output_dir) → ◇ metrics_enabled? → ⊕ write {targets,labels} JSON → ⎋ RenderResult
# region MODULE_CONTRACT
## @purpose  Prometheus file-based service discovery target generator — extracted from
##           monitoring_config_renderer.py (DevPlan 117 G T54).
## @scope    Consumed by monitoring_config_renderer.main() (lazy import). Non-fatal.
## @invariants
##   - JSON schema: {"targets": ["<project>:<port>"], "labels": {"project","type","node","service"}}
##   - Skips if metrics_enabled is False (status="noop")
##   - Non-fatal: file write failure → logged, continue
## @rationale  DevPlan 117 G T54 — extracted verbatim (generate_prometheus_target, ~54 LOC).
## @changes  2026-08-01 · DevPlan 117 G T54 — extracted from monitoring_config_renderer.py
# endregion MODULE_CONTRACT

import json
import logging
import sys
from pathlib import Path

# Dual-import pattern (monitoring_config_renderer L43-52): core.* path under pytest/python3 -m,
# parent-dir bootstrap for direct-script invocation.
try:
    from monitoring_config_renderer import ProjectMonitoringConfig, RenderResult

    from monitoring.constants import DEFAULT_PROMETHEUS_TARGETS_DIR
except ImportError:  # pragma: no cover — direct-script invocation path
    _INTERNAL_DIR = str(Path(__file__).resolve().parent.parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    from monitoring_config_renderer import ProjectMonitoringConfig, RenderResult

    from monitoring.constants import DEFAULT_PROMETHEUS_TARGETS_DIR

logger = logging.getLogger(__name__)


# region FUNC_generate_prometheus_target
def generate_prometheus_target(
    config: ProjectMonitoringConfig,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Prometheus file-based service discovery target JSON.

    ## @purpose  Create project target JSON for Prometheus file_sd_config.
    ##           Skips if metrics_enabled is False.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ output_dir: Path — output directory (default: platform_root/prometheus-targets)
    ##   ⎋ RenderResult — outcome with status and output path
    ## @complexity O(1)
    ## @invariants
    ##   - JSON schema: {"targets": ["<project>:<port>"], "labels": {"project", "type", "node", "service"}}
    ##   - Creates output directory if missing
    ##   - Non-fatal: file write failure → logged, continue
    """
    if not config.metrics_enabled:
        logger.info("[IMP:8][prometheus] Metrics disabled for %s — skipping Prometheus target", config.project_name)
        return RenderResult(component="prometheus", status="noop", detail="metrics_enabled=False")

    port = config.metrics_port
    targets_dir = output_dir or (config.platform_root / DEFAULT_PROMETHEUS_TARGETS_DIR)
    targets_dir.mkdir(parents=True, exist_ok=True)

    target_file = targets_dir / f"{config.project_name}.json"
    target = {
        "targets": [f"{config.project_name}:{port}"],
        "labels": {
            "project": config.project_name,
            "type": config.project_type,
            "node": config.node_name,
            "service": config.project_name,
        },
    }

    try:
        target_file.write_text(json.dumps(target, indent=2), encoding="utf-8")
        logger.info("[IMP:9][prometheus] Prometheus target file generated: %s (port=%d)", target_file, port)
        return RenderResult(
            component="prometheus",
            status="created",
            output_path=target_file,
            detail=f"targets=[{config.project_name}:{port}]",
        )
    except OSError as e:
        logger.info("[IMP:6][prometheus] Failed to write Prometheus target file %s: %s", target_file, e)
        return RenderResult(component="prometheus", status="failed", detail=str(e))


# endregion FUNC_generate_prometheus_target
