#!/usr/bin/env python3
# GREP_SUMMARY: monitoring-config-renderer prometheus-targets grafana-dashboards loki-retention langfuse-project alert-rules catalog-refresh service-reload deep-merge
# STRUCTURE: ▶ cli:args→dispatch → ◇ load_all_configs→3-level-merge → ⊕ generate_prometheus_target → ⊕ generate_grafana_dashboard → ⊕ update_loki_retention(YAML modify) → ⊕ create_langfuse_project(HTTP POST) → ⊕ generate_alert_rules → ⊕ refresh_catalog(subprocess) → ⊕ reload_services(HTTP POST) → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Post-deploy monitoring reconfiguration: Prometheus targets, Grafana dashboards, Loki retention,
##           Langfuse projects, alert rules, catalog refresh, service reload.
##           Migrates all logic from core/modules/monitoring/hooks/on-project-deploy.sh (19 inline python3 calls).
## @scope    Invoked by on-project-deploy.sh thin wrapper after successful project deploy.
##           Receives --project-dir, --project, --node via CLI args.
## @invariants
##   - Non-fatal: errors at any step are logged (IMP:6-8) but do NOT block remaining steps
##   - 3-level config merge: L1 defaults ← L2 org overrides ← L3 project config
##   - Prometheus target JSON follows exact schema: {targets, labels{project, type, node, service}}
##   - Loki retention: idempotent insertion (skips if selector exists); new rules before catch-all
##   - Langfuse project creation: HTTP POST with Bearer token; 409 → skipped (idempotent)
##   - All YAML loading returns empty dict on file-not-found (non-fatal)
##   - Missing ai-platform.yaml or no monitoring section → exit 0 (backward compat)
##   - template-engine.sh call with sed fallback for dashboard/alert rendering
## @rationale Shell script had 19 inline python3 calls, 3-level nested merge via shell eval,
##            fragile and ungreppable. Python module with typed dataclasses and unit tests
##            eliminates the entire class of risk. Strangler-Fig Tier 1 extraction.
## @changes
##   LAST_CHANGE: 2026-07-25 | Created (DevPlan 074)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# region CONSTANTS

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

# Template engine script relative to platform root
TEMPLATE_ENGINE_SCRIPT = "core/internal/template-engine.sh"

# Service reload endpoints
PROMETHEUS_RELOAD_URL = "http://prometheus:9090/-/reload"
LOKI_RELOAD_URL = "http://loki:3100/reload"

# Langfuse API endpoint
LANGFUSE_API_URL = "http://langfuse:3000/api/public/projects"


# endregion CONSTANTS


# region DATACLASSES


@dataclass
class ProjectMonitoringConfig:
    """Fully resolved monitoring configuration for a single project.

    ## @purpose  Typed container for merged monitoring config after L1←L2←L3 merge.
    ##            Replaces the shell-level MERGED_CONFIG JSON string + separate
    ##            PROJECT_TYPE, NEEDS_LLM globals.
    ## @scope    Created by build_merged_config(), consumed by all render functions.
    ## @invariants
    ##   - merged_config contains the full merged dict (for future extensibility)
    ##   - All boolean flags default to False (safe defaults)
    ##   - logs_retention defaults to "7d" matching original Default
    ##   - metrics_port defaults to 3000 matching original
    """

    project_name: str
    project_type: str
    project_dir: Path
    node_name: str
    platform_root: Path

    # Flags extracted from merged config
    metrics_enabled: bool = False
    metrics_port: int = 3000
    dashboard_enabled: bool = False
    alerting_enabled: bool = False
    needs_llm: bool = False

    # Retention settings
    logs_retention: str = "7d"
    ai_retention_days: int = 30

    # Full merged dict (for future extensibility)
    merged_config: dict = field(default_factory=dict)


@dataclass
class RenderResult:
    """Result of a single monitoring render operation.

    ## @purpose  Collect outcome of each render step for logging and debugging.
    ## @scope    Returned by every render function. Caller logs each result.
    ## @invariants
    ##   - status is one of: "created", "skipped", "updated", "failed", "noop"
    ##   - output_path is set only when a file was written
    """

    component: str  # prometheus, grafana, loki, langfuse, alerting, catalog, reload
    status: str  # "created", "skipped", "updated", "failed", "noop"
    detail: str = ""
    output_path: Path | None = None


# endregion DATACLASSES


# region CONFIG_LOADING


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override dict into base dict.

    ## @purpose  Merge L1←L2←L3 monitoring config layers. Last-writer-wins for scalars,
    ##            recursive merge for nested dicts. Matching original shell behavior:
    ##            simple top-level update with dict-vs-dict recursion.
    ## @io
    ##   ⇥ base: dict — base config (lower priority)
    ##   ⇥ override: dict — overriding config (higher priority)
    ##   ⎋ dict — merged result (new dict, no mutation of inputs)
    ## @complexity O(n) where n = total keys across both dicts
    ## @invariants
    ##   - Input dicts are NOT mutated (new dict returned)
    ##   - For each key in override: if both values are dicts, recursively merge
    ##   - Otherwise: override value replaces base value
    ##   - Keys only in base are preserved
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_yaml_config(yaml_path: Path) -> dict:
    """Load and parse a YAML file. Returns empty dict on file-not-found.

    ## @purpose  Safe YAML loading for all monitoring config files.
    ##            Non-fatal: missing file → log IMP:6 warning + return {}.
    ##            Malformed YAML → raise (fail fast).
    ## @io
    ##   ⇥ yaml_path: Path — path to YAML file
    ##   ⎋ dict — parsed YAML content (empty if file missing)
    ## @raises yaml.YAMLError: on malformed YAML syntax
    ## @complexity O(1) file read + O(N) parse
    """
    if not yaml_path.exists():
        logger.info("[IMP:6][config] YAML file not found: %s — returning empty dict", yaml_path)
        return {}
    raw = yaml_path.read_text(encoding="utf-8")
    return yaml.safe_load(raw) or {}


def load_l1_defaults(defaults_path: Path, project_type: str) -> dict:
    """Load L1 monitoring defaults from defaults.yaml with type-specific overrides.

    ## @purpose  Load platform-wide monitoring defaults, merge with type-specific
    ##            type-defaults.<project_type> section.
    ## @io
    ##   ⇥ defaults_path: Path — path to defaults.yaml
    ##   ⇥ project_type: str — project type ("backend", "frontend", "fullstack")
    ##   ⎋ dict — merged L1 config (empty if defaults file missing)
    ## @complexity O(N) where N = keys in defaults.yaml
    ## @invariants
    ##   - Missing defaults.yaml → empty dict (log IMP:6)
    ##   - Unknown project_type → only global monitoring section returned
    ##   - type-defaults overrides are merged ON TOP of global monitoring section
    """
    data = load_yaml_config(defaults_path)
    if not data:
        return {}

    monitoring = dict(data.get("monitoring", {}))
    type_defaults = data.get("type-defaults", {}).get(project_type, {})
    if type_defaults:
        logger.info("[IMP:9][config] L1 defaults merged with type-defaults for '%s'", project_type)
    monitoring.update(type_defaults)
    return monitoring


def load_l2_overrides(override_path: Path) -> dict:
    """Load L2 context overrides from node-configs/<node>/projects/<project>.yaml.

    ## @purpose  Load org/node-level monitoring overrides from context file.
    ## @io
    ##   ⇥ override_path: Path — path to context override YAML
    ##   ⎋ dict — monitoring section only (empty if file missing or no monitoring section)
    ## @complexity O(1)
    ## @invariants
    ##   - Missing file → empty dict (log IMP:6)
    ##   - Extracts ONLY the `monitoring` top-level section
    """
    data = load_yaml_config(override_path)
    if not data:
        return {}
    return dict(data.get("monitoring", {}))


def load_l3_project_config(project_yaml: dict) -> dict:
    """Extract L3 monitoring section from ai-platform.yaml (already parsed).

    ## @purpose  Simple extraction: data.get('monitoring', {})
    ## @io
    ##   ⇥ project_yaml: dict — full ai-platform.yaml content
    ##   ⎋ dict — monitoring section (empty if absent)
    ## @complexity O(1)
    """
    return dict(project_yaml.get("monitoring", {}))


def build_merged_config(
    project_dir: Path,
    project_name: str,
    node_name: str,
    platform_root: Path,
) -> ProjectMonitoringConfig | None:
    """Full pipeline: load ai-platform.yaml → extract project type → load L1 defaults →
    load L2 overrides → extract L3 config → merge L1←L2←L3 → extract flags → return typed config.

    ## @purpose  Complete monitoring config loading and 3-level merge.
    ##            Replaces _load_project_config() from original shell (lines 42-134).
    ## @io
    ##   ⇥ project_dir: Path — project root directory containing ai-platform.yaml
    ##   ⇥ project_name: str — project name
    ##   ⇥ node_name: str — node name
    ##   ⇥ platform_root: Path — platform root directory
    ##   ⎋ Optional[ProjectMonitoringConfig] — merged config, or None if no monitoring section
    ## @complexity O(N) where N = total YAML keys loaded
    ## @invariants
    ##   - ai-platform.yaml missing → log IMP:8 + return None (backward compat)
    ##   - No monitoring section → log IMP:8 + return None (backward compat)
    ##   - Missing L1/defaults.yaml → skipped (empty dict)
    ##   - Missing L2/override.yaml → skipped (empty dict)
    ##   - Merged config retains all keys from all layers
    """
    ai_yaml_path = project_dir / "ai-platform.yaml"

    if not ai_yaml_path.exists():
        logger.info("[IMP:8][config] No ai-platform.yaml found in %s — skipping monitoring reconfig", project_dir)
        return None

    project_yaml = load_yaml_config(ai_yaml_path)
    if not project_yaml:
        return None

    monitoring_section = project_yaml.get("monitoring")
    if not isinstance(monitoring_section, dict):
        logger.info("[IMP:8][config] No monitoring section in ai-platform.yaml — skipping (backward compat)")
        return None

    project_type = str(project_yaml.get("type", ""))
    logger.info("[IMP:8][config] Loading monitoring config for %s (type=%s)", project_name, project_type)

    # L1 defaults
    defaults_path = platform_root / DEFAULT_L1_DEFAULTS
    l1_config = load_l1_defaults(defaults_path, project_type)
    if l1_config:
        logger.info("[IMP:8][config] L1 defaults loaded")
    else:
        logger.info("[IMP:6][config] L1 defaults file not found: %s", defaults_path)

    # L2 context overrides
    override_path = platform_root / "node-configs" / node_name / "projects" / f"{project_name}.yaml"
    l2_config = load_l2_overrides(override_path)
    if l2_config:
        logger.info("[IMP:8][config] L2 context overrides loaded")

    # L3 project config
    l3_config = load_l3_project_config(project_yaml)

    # 3-level merge: L1 ← L2 ← L3
    merged = deep_merge(l1_config, l2_config)
    merged = deep_merge(merged, l3_config)

    # Extract flags
    needs = project_yaml.get("needs", {})
    config = ProjectMonitoringConfig(
        project_name=project_name,
        project_type=project_type,
        project_dir=project_dir,
        node_name=node_name,
        platform_root=platform_root,
        metrics_enabled=_str_to_bool(merged.get("metrics", False)),
        metrics_port=int(merged.get("metrics_port", 3000)),
        dashboard_enabled=_str_to_bool(merged.get("dashboard", False)),
        alerting_enabled=_str_to_bool(merged.get("alerting", False)),
        needs_llm=_str_to_bool(needs.get("llm", False)),
        logs_retention=str(merged.get("logs_retention", "7d")),
        ai_retention_days=_parse_ai_retention(merged.get("ai_retention", "30d")),
        merged_config=merged,
    )

    logger.info("[IMP:9][config] Merged monitoring config for %s", project_name)
    return config


def _str_to_bool(val) -> bool:
    """Convert string or bool to bool. Handles 'true'/'false' strings from YAML.

    ## @purpose  Normalise mixed bool/string values from YAML parsing.
    ## @complexity O(1)
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def _parse_ai_retention(val) -> int:
    """Parse ai_retention value to integer days.

    ## @purpose  Extract integer days from "30d" format.
    ## @complexity O(1)
    """
    if isinstance(val, int):
        return val
    s = str(val).strip().lower().rstrip("d")
    try:
        return int(s)
    except (ValueError, TypeError):
        return 30


# endregion CONFIG_LOADING


# region TEMPLATE_RENDERING


def _render_template(template_path: Path, output_path: Path, variables: dict[str, str], platform_root: Path) -> None:
    """Render template via template-engine.sh, with sed fallback.

    ## @purpose  Shared rendering logic for Grafana dashboards and alert rules.
    ##            Uses core/internal/template-engine.sh if available, falls back
    ##            to sed-based $VAR substitution.
    ## @io
    ##   ⇥ template_path: Path — source template file
    ##   ⇥ output_path: Path — output file path
    ##   ⇥ variables: dict — KEY=VALUE substitution variables
    ##   ⇥ platform_root: Path — platform root for resolving template-engine.sh
    ## @complexity O(T + V) where T = template size, V = number of variables
    ## @invariants
    ##   - Output parent directory is created if missing
    ##   - template-engine.sh called with `render` subcommand and KEY=VALUE args
    ##   - sed fallback: replaces $KEY with VALUE for each variable
    ##   - Missing template → raises FileNotFoundError
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    engine_path = platform_root / TEMPLATE_ENGINE_SCRIPT
    if engine_path.is_file():
        args = [str(engine_path), "render", str(template_path), str(output_path)]
        for k, v in variables.items():
            args.append(f"{k}={v}")
        subprocess.run(args, check=True)
    else:
        logger.info("[IMP:6][template] template-engine.sh not found at %s — falling back to sed", engine_path)
        content = template_path.read_text(encoding="utf-8")
        for k, v in variables.items():
            content = content.replace(f"${{{k}}}", v)
            content = content.replace(f"${k}", v)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")


# endregion TEMPLATE_RENDERING


# region RETENTION_PARSING


def _parse_retention_hours(retention_str: str) -> int:
    """Parse logs_retention string to hours.

    ## @purpose  Convert retention strings like "7d", "336h", "forever" to hours.
    ##           Matching original shell lines 241-246.
    ## @io
    ##   ⇥ retention_str: str — e.g., "7d", "336h", "forever"
    ##   ⎋ int — hours (0 for forever, 168 default)
    ## @complexity O(1)
    ## @invariants
    ##   - "forever" → 0
    ##   - "Nd" → N * 24
    ##   - "Nh" → N
    ##   - Invalid/unparseable → 168 (7 days default)
    """
    s = retention_str.strip().lower()
    if s == "forever":
        return 0
    if s.endswith("d"):
        try:
            return int(s[:-1]) * 24
        except (ValueError, IndexError):
            return 168
    if s.endswith("h"):
        try:
            return int(s[:-1])
        except (ValueError, IndexError):
            return 168
    try:
        # Plain number → treat as hours
        return int(s)
    except (ValueError, TypeError):
        return 168


# endregion RETENTION_PARSING


# region PROMETHEUS_TARGETS


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


# endregion PROMETHEUS_TARGETS


# region GRAFANA_DASHBOARDS


def generate_grafana_dashboard(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Grafana dashboard JSON from template.

    ## @purpose  Render project dashboard using template-engine.sh (or sed fallback).
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
    ##   - template-engine.sh fallback to sed: $PROJECT, $TYPE, $NODE substitution
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
            platform_root=config.platform_root,
        )
        logger.info("[IMP:9][grafana] Dashboard generated: %s", dash_file)
        return RenderResult(component="grafana", status="created", output_path=dash_file)
    except (OSError, subprocess.CalledProcessError) as e:
        logger.info("[IMP:6][grafana] Dashboard generation failed for %s: %s", config.project_name, e)
        return RenderResult(component="grafana", status="failed", detail=str(e))


# endregion GRAFANA_DASHBOARDS


# region LOKI_RETENTION


def update_loki_retention(
    config: ProjectMonitoringConfig,
    runtime_config_path: Path | None = None,
) -> RenderResult:
    """Update Loki runtime config YAML with project retention stream.

    ## @purpose  Idempotently add or verify a retention stream rule for the project
    ##           in Loki's runtime config YAML. New rules are inserted BEFORE any
    ##           catch-all rules (selectors containing 'compose_project=~').
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⇥ runtime_config_path: Path — Loki runtime config path (default: platform-relative)
    ##   ⎋ RenderResult — outcome: "updated", "skipped" (exists), "failed", "noop"
    ## @complexity O(S) where S = number of existing retention streams
    ## @invariants
    ##   - Retention is always applied (no flag gate — Loki retention is universal)
    ##   - Idempotent: if selector for this project already exists → status="skipped"
    ##   - New rules inserted before catch-all (compose_project=~) rules
    ##   - Missing runtime config file → created with just this project's stream
    ##   - Non-fatal: YAML/dict errors logged, continue
    """
    retention_hours = _parse_retention_hours(config.logs_retention)
    config_path = runtime_config_path or (config.platform_root / DEFAULT_LOKI_RUNTIME_CONFIG)
    project = config.project_name

    try:
        # Load existing config or start fresh
        existing = load_yaml_config(config_path)

        streams = existing.setdefault("limits_config", {}).setdefault("retention_stream", [])

        # Check if selector already exists (idempotent)
        selector = '{compose_project="' + project + '"}'
        exists = any(isinstance(s, dict) and s.get("selector", "") == selector for s in streams)

        if exists:
            logger.info("[IMP:8][loki] Retention stream already exists for %s — skipping", project)
            return RenderResult(component="loki", status="skipped", detail=f"stream for {project} already exists")

        # Build new rule
        new_rule = {
            "selector": selector,
            "priority": 0,
            "period": str(retention_hours) + "h",
        }

        # Insert before catch-all (compose_project=~) rules
        inserted = False
        for i, s in enumerate(streams):
            if isinstance(s, dict) and "compose_project=~" in s.get("selector", ""):
                streams.insert(i, new_rule)
                inserted = True
                break

        if not inserted:
            streams.append(new_rule)

        # Write back
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        logger.info(
            "[IMP:9][loki] Loki runtime config updated for %s: %s (%dh)",
            project,
            config.logs_retention,
            retention_hours,
        )
        return RenderResult(
            component="loki", status="updated", detail=f"retention={config.logs_retention} ({retention_hours}h)"
        )
    except (OSError, yaml.YAMLError) as e:
        logger.info("[IMP:6][loki] Failed to update Loki retention for %s: %s", project, e)
        return RenderResult(component="loki", status="failed", detail=str(e))


# endregion LOKI_RETENTION


# region LANGFUSE_PROJECTS


def create_langfuse_project(
    config: ProjectMonitoringConfig,
) -> RenderResult:
    """Create Langfuse project via HTTP API.

    ## @purpose  POST to Langfuse API to create a project for LLM monitoring.
    ##           Skips if needs_llm is False.
    ## @io
    ##   ⇥ config: ProjectMonitoringConfig — resolved monitoring config
    ##   ⎋ RenderResult — outcome: "created", "skipped" (exists or no LLM), "failed"
    ## @complexity O(1) HTTP call
    ## @invariants
    ##   - Skips if needs_llm is False (status="noop")
    ##   - Uses urllib.request (stdlib) — no requests dependency
    ##   - LANGFUSE_SECRET_KEY read from environment (missing → status="failed")
    ##   - HTTP 409 / "already exists" → status="skipped" (idempotent)
    ##   - Non-fatal: HTTP/network errors logged, continue
    """
    if not config.needs_llm:
        logger.info("[IMP:8][langfuse] No LLM needs declared — skipping Langfuse project")
        return RenderResult(component="langfuse", status="noop", detail="needs_llm=False")

    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not secret_key:
        logger.info("[IMP:6][langfuse] LANGFUSE_SECRET_KEY not set — skipping Langfuse project creation")
        return RenderResult(component="langfuse", status="failed", detail="LANGFUSE_SECRET_KEY not set")

    body = json.dumps(
        {
            "name": config.project_name,
            "retention": config.ai_retention_days,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        LANGFUSE_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — internal Langfuse API (localhost)
            status_code = resp.status
            if status_code in (200, 201):
                logger.info("[IMP:9][langfuse] Langfuse project created: %s", config.project_name)
                return RenderResult(component="langfuse", status="created", detail=f"HTTP {status_code}")
            logger.info("[IMP:6][langfuse] Langfuse API returned HTTP %s for %s", status_code, config.project_name)
            return RenderResult(component="langfuse", status="failed", detail=f"HTTP {status_code}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            logger.info("[IMP:8][langfuse] Langfuse project '%s' already exists — skipping", config.project_name)
            return RenderResult(component="langfuse", status="skipped", detail="HTTP 409 already exists")
        logger.info("[IMP:6][langfuse] Langfuse HTTP error %s for %s: %s", e.code, config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        logger.info("[IMP:6][langfuse] Langfuse network error for %s: %s", config.project_name, e)
        return RenderResult(component="langfuse", status="failed", detail=str(e))


# endregion LANGFUSE_PROJECTS


# region ALERT_RULES


def generate_alert_rules(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Generate Prometheus alert rules YAML from template.

    ## @purpose  Render project alert rules using template-engine.sh (or sed fallback).
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
            platform_root=config.platform_root,
        )
        logger.info("[IMP:9][alerting] Alert rules generated: %s", output_file)
        return RenderResult(component="alerting", status="created", output_path=output_file)
    except (OSError, subprocess.CalledProcessError) as e:
        logger.info("[IMP:6][alerting] Alert rules generation failed for %s: %s", config.project_name, e)
        return RenderResult(component="alerting", status="failed", detail=str(e))


# endregion ALERT_RULES


# region CATALOG_REFRESH


def refresh_catalog(platform_root: Path) -> RenderResult:
    """Invoke catalog generation script.

    ## @purpose  Run core/internal/catalog/generate-catalog.sh to refresh service catalog.
    ##           Non-fatal: script not found or failure → logged, continue.
    ## @io
    ##   ⇥ platform_root: Path — platform root for resolving catalog script
    ##   ⎋ RenderResult — outcome
    ## @complexity O(1) subprocess call
    ## @invariants
    ##   - Script must be executable (check with is_file())
    ##   - Script not found → status="noop"
    ##   - Script failure → status="failed", logged at IMP:6
    """
    script_path = platform_root / CATALOG_SCRIPT
    if not script_path.is_file():
        logger.info("[IMP:7][catalog] Catalog script not found: %s — skipping", script_path)
        return RenderResult(component="catalog", status="noop", detail=f"script not found: {script_path}")

    try:
        subprocess.run([str(script_path)], check=True, capture_output=True, text=True, timeout=60)
        logger.info("[IMP:8][catalog] Catalog refresh invoked")
        return RenderResult(component="catalog", status="created", detail="Catalog refreshed")
    except subprocess.CalledProcessError as e:
        logger.info("[IMP:6][catalog] Catalog generation failed (exit %s): %s", e.returncode, e.stderr.strip())
        return RenderResult(component="catalog", status="failed", detail=e.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.info("[IMP:6][catalog] Catalog generation error: %s", e)
        return RenderResult(component="catalog", status="failed", detail=str(e))


# endregion CATALOG_REFRESH


# region SERVICE_RELOAD


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


# endregion SERVICE_RELOAD


# region CLI_ENTRY


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose  Parse CLI arguments for the monitoring config renderer.
    ## @io
    ##   ⎋ argparse.ArgumentParser — configured parser
    """
    parser = argparse.ArgumentParser(
        description="Monitoring Config Renderer — post-deploy monitoring reconfiguration",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Absolute path to project directory (containing ai-platform.yaml)",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name (e.g., 'my-backend')",
    )
    parser.add_argument(
        "--node",
        default="",
        help="Node name (e.g., 'tronyx-vps')",
    )
    return parser


def main() -> int:
    """CLI entry point: orchestrate full monitoring reconfiguration pipeline.

    ## @purpose  Parse args → resolve platform_root → build merged config →
    ##            execute all monitoring component renderers → exit.
    ##            Matches execution order of original main() (lines 392-413).
    ## @io
    ##   CLI: --project-dir <dir> --project <name> [--node <name>]
    ##   ⎋ int — exit code (0 = success, 1 = config parse error)
    ## @complexity O(N) where N = total render operations
    ## @invariants
    ##   - Missing args → argparse handles error, exit 1
    ##   - No monitoring section → exit 0 (backward compat)
    ##   - All render steps are non-blocking (errors logged, continue)
    ##   - Execution order: alert_rules → prometheus → grafana → loki → reload → langfuse → catalog
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    project_dir = Path(args.project_dir).resolve()
    project_name = args.project
    node_name = args.node

    # Resolve platform_root from this file's location:
    # core/internal/monitoring_config_renderer.py → core/internal/ → core/ → platform_root
    platform_root = Path(__file__).resolve().parent.parent.parent

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:9][hook] === monitoring on-project-deploy START: %s ===", project_name)

    # Load + merge configs
    config = build_merged_config(project_dir, project_name, node_name, platform_root)
    if config is None:
        logger.info("[IMP:8][hook] No monitoring config — skipping hook for %s", project_name)
        return 0

    # Execute all render steps (order matches original main)
    generate_alert_rules(config)
    generate_prometheus_target(config)
    generate_grafana_dashboard(config)
    update_loki_retention(config)
    reload_monitoring_services()
    create_langfuse_project(config)
    refresh_catalog(platform_root)

    logger.info("[IMP:9][hook] === monitoring on-project-deploy DONE: %s ===", project_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
# endregion CLI_ENTRY
