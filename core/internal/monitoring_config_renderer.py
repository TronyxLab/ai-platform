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
##   - Template rendering via template_engine.render_template (native import, strict {{UPPER_SNAKE}} only)
## @rationale Shell script had 19 inline python3 calls, 3-level nested merge via shell eval,
##            fragile and ungreppable. Python module with typed dataclasses and unit tests
##            eliminates the entire class of risk. Strangler-Fig Tier 1 extraction.
## @changes
##   LAST_CHANGE: 2026-07-25 | Created (DevPlan 074)
##   2026-08-05 | DevPlan 138 W3: экстракция run_monitoring_reconfig(project_dir, project_name,
##               node_name, platform_root) из main() + _render_step (non-blocking шаги);
##               main() делегирует — CLI fallback жив; вызов из DeployOrchestrator post_deploy_chain
# endregion MODULE_CONTRACT

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Canonical sys.path bootstrap (pattern: config_renderer.py) — repo root needed
# for `core.internal.*` imports in monitoring/* submodules under direct-script
# invocation (`python3 core/internal/monitoring_config_renderer.py`, make target).
# File is at core/internal/monitoring_config_renderer.py → root = 3 levels up.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ⚠️ TRAP[DECISION] · 2026-07-31 · — · Native template import — dual-path (DevPlan 094 §7.1)
# · Rejected: subprocess to the deleted shell wrapper (2 extra processes, arg-marshalling, sed fallback drift)
# · Reason: direct import — 0 subprocess in Python domain; strict {{UPPER_SNAKE}} grammar enforced
# · Rev: if monitoring_config_renderer gains a packaging namespace, drop the direct-import fallback
try:
    # Imported as core.internal.monitoring_config_renderer (tests, python3 -m) — single module instance
    # TemplateError re-exported for the monitoring/* generator modules (DevPlan 117 G T54).
    from core.internal.template_engine import (
        TemplateError,
        render_template,
    )
except ImportError:  # pragma: no cover — direct-script invocation path
    # Invoked as `python3 ${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py`:
    # sys.path[0] = core/internal/ — template_engine.py lives in the same directory.
    _INTERNAL_DIR = str(Path(__file__).resolve().parent)
    if _INTERNAL_DIR not in sys.path:
        sys.path.insert(0, _INTERNAL_DIR)
    from template_engine import TemplateError, render_template  # noqa: F401  (re-exported to monitoring/* generators)

logger = logging.getLogger(__name__)

# region CONSTANTS

# ── module-level constants ──────────────────────────────────────────────────
# DevPlan 117 G T54: constants moved to core/internal/monitoring/constants.py
# (single source shared by the 7 generator modules + this orchestrator).
from monitoring.constants import (  # noqa: F401
    ALERT_RULES_DIR,
    CATALOG_SCRIPT,
    DEFAULT_ALERT_RULES_TEMPLATE,
    DEFAULT_GRAFANA_DASHBOARDS_DIR,
    DEFAULT_GRAFANA_TEMPLATE,
    DEFAULT_L1_DEFAULTS,
    DEFAULT_LOKI_RUNTIME_CONFIG,
    DEFAULT_PROMETHEUS_TARGETS_DIR,
    LANGFUSE_API_URL,
    LOKI_RELOAD_URL,
    PROMETHEUS_RELOAD_URL,
)

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
    ##   ⇥ project_type: str — project type ("backend", "frontend")
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

    # B1: чтение ai-platform.yaml через единый shared-ридер (load_yaml_config остаётся для L1/L2 файлов)
    from core.internal.shared import project_yaml as shared_project_yaml

    project_yaml = shared_project_yaml.load_project_yaml(project_dir)
    if not project_yaml:
        logger.info("[IMP:8][config] ai-platform.yaml unparseable or empty — skipping monitoring reconfig")
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


def render_template_file(template_path: Path, output_path: Path, variables: dict[str, str]) -> None:
    """Render template via template_engine.render_template (native import, no subprocess).

    ## @purpose  Shared rendering logic for Grafana dashboards and alert rules.
    ##            Uses template_engine.render_template directly — 0 subprocess,
    ##            0 sed fallback. Strict {{UPPER_SNAKE}} grammar only.
    ## @io
    ##   ⇥ template_path: Path — source template file
    ##   ⇥ output_path: Path — output file path
    ##   ⇥ variables: dict — KEY=VALUE substitution variables
    ## @raises TemplateError: on unresolved placeholders (allow_missing=False)
    ## @raises FileNotFoundError / PermissionError: on template read failure
    ## @complexity O(T + V) where T = template size, V = number of variables
    ## @invariants
    ##   - Output parent directory is created if missing
    ##   - render_template(allow_missing=False) raises on unresolved {{VAR}}
    ##   - No sed fallback — strict {{UPPER_SNAKE}} is the only grammar (DevPlan 094)
    """
    # ⚠️ TRAP[BUG] · 2026-07-31 · P2 · Removed sed-fallback — strict {{UPPER_SNAKE}} is the only grammar now
    # · Symptom: sed fallback substituted ${K} and {{{K}}} — non-strict grammars silently produced
    #   malformed dashboards/alert-rules when the shell wrapper was missing
    # · Root: fallback bypassed template_engine strict grammar (DevPlan 094 Wave 2.A)
    # · Fix: native render_template(allow_missing=False) — single rendering path, TemplateError on unresolved
    # · Prevention: never reintroduce non-strict substitution for monitoring templates
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_template(
        str(template_path),
        output_path=str(output_path),
        vars=variables,
        allow_missing=False,
    )


# endregion TEMPLATE_RENDERING


# region RETENTION_PARSING


def parse_retention_hours(retention_str: str) -> int:
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


# Legacy private alias — white-box tests import _parse_retention_hours (gate does not scan tests/).
_parse_retention_hours = parse_retention_hours


# endregion RETENTION_PARSING


# region PROMETHEUS_TARGETS


def generate_prometheus_target(
    config: ProjectMonitoringConfig,
    output_dir: Path | None = None,
) -> RenderResult:
    """Lazy facade for monitoring.prometheus_targets.generate_prometheus_target (DevPlan 117 G T54).

    ## @purpose  Backward-compatible entry point retained in monitoring_config_renderer so existing
    ##            callers (main, tests) keep the same import path. Implementation moved verbatim
    ##            to monitoring/prometheus_targets.py. Lazy import keeps start-up time unchanged (AC-G5).
    """
    from monitoring.prometheus_targets import generate_prometheus_target as _impl

    return _impl(config, output_dir)


# endregion PROMETHEUS_TARGETS


# region GRAFANA_DASHBOARDS


def generate_grafana_dashboard(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Lazy facade for monitoring.grafana_dashboards.generate_grafana_dashboard (DevPlan 117 G T54)."""
    from monitoring.grafana_dashboards import generate_grafana_dashboard as _impl

    return _impl(config, template_path, output_dir)


# endregion GRAFANA_DASHBOARDS


# region LOKI_RETENTION


def update_loki_retention(
    config: ProjectMonitoringConfig,
    runtime_config_path: Path | None = None,
) -> RenderResult:
    """Lazy facade for monitoring.loki_retention.update_loki_retention (DevPlan 117 G T54)."""
    from monitoring.loki_retention import update_loki_retention as _impl

    return _impl(config, runtime_config_path)


# endregion LOKI_RETENTION


# region LANGFUSE_PROJECTS


def create_langfuse_project(
    config: ProjectMonitoringConfig,
) -> RenderResult:
    """Lazy facade for monitoring.langfuse_projects.create_langfuse_project (DevPlan 117 G T54)."""
    from monitoring.langfuse_projects import create_langfuse_project as _impl

    return _impl(config)


# endregion LANGFUSE_PROJECTS


# region ALERT_RULES


def generate_alert_rules(
    config: ProjectMonitoringConfig,
    template_path: Path | None = None,
    output_dir: Path | None = None,
) -> RenderResult:
    """Lazy facade for monitoring.alert_rules.generate_alert_rules (DevPlan 117 G T54)."""
    from monitoring.alert_rules import generate_alert_rules as _impl

    return _impl(config, template_path, output_dir)


# endregion ALERT_RULES


# region CATALOG_REFRESH


def refresh_catalog(platform_root: Path) -> RenderResult:
    """Lazy facade for monitoring.catalog_refresh.refresh_catalog (DevPlan 117 G T54)."""
    from monitoring.catalog_refresh import refresh_catalog as _impl

    return _impl(platform_root)


# endregion CATALOG_REFRESH


# region SERVICE_RELOAD


def reload_monitoring_services() -> list[RenderResult]:
    """Lazy facade for monitoring.service_reload.reload_monitoring_services (DevPlan 117 G T54)."""
    from monitoring.service_reload import reload_monitoring_services as _impl

    return _impl()


# endregion SERVICE_RELOAD


# region CLI_ENTRY


# region FUNC__render_step
def _render_step(step_name: str, fn, *args) -> None:
    """Execute one monitoring render step non-blocking (DevPlan 138 §4.3).

    ## @purpose  Best-effort контракт post-deploy chain: ошибка шага → log WARN (IMP:8),
    ##            continue — деплой НЕ фейлится. Паритет до-B8 module-hook семантики.
    ## @io       ⇥ step_name: str — имя шага (alert_rules/prometheus/grafana/loki/reload/langfuse/catalog)
    ##           ⇥ fn: callable — render-функция (facade, возвращает RenderResult|list[RenderResult])
    ##           ⇥ *args — аргументы render-функции → ⎋ None
    ## @complexity O(1)
    ## @invariants
    ##   - Исключения НЕ пробрасываются (non-blocking, R5 — сбой рендера не роняет деплой)
    ##   - Успешный шаг логируется на IMP:8 со статусом RenderResult (или "done" для list)
    ##   - Сбой логируется на IMP:8 WARN с текстом исключения
    """
    try:
        result = fn(*args)
        status = getattr(result, "status", "done")
        logger.info("[IMP:8][hook] %s render: %s", step_name, status)
    except Exception as e:  # noqa: EXC — best-effort контракт post-deploy chain (DevPlan 138 §4.3)
        logger.warning("[IMP:8][hook] %s render WARN (non-fatal): %s", step_name, e)


# endregion FUNC__render_step


# region FUNC_run_monitoring_reconfig
def run_monitoring_reconfig(
    project_dir: Path,
    project_name: str,
    node_name: str,
    platform_root: Path,
) -> int:
    """Post-deploy monitoring reconfiguration (паритет до-B8 module-hook).

    ## @purpose  Execute full monitoring reconfig for one project after deploy.
    ##            Паритет удалённого module-hook (волна 118 B8): рендер на каждый receive,
    ##            non-blocking. Экстрагирован из main() (DevPlan 138 W3 §4.3) для вызова
    ##            из DeployOrchestrator._run_post_deploy_chain (lazy-import, WARN non-fatal).
    ## @io       ⇥ project_dir: Path — директория проекта (содержит ai-platform.yaml)
    ##           ⇥ project_name: str — имя проекта
    ##           ⇥ node_name: str — имя ноды ("" если неизвестно — O3 DevPlan 138 §10.1)
    ##           ⇥ platform_root: Path — корень платформы (node-configs/, defaults.yaml)
    ##           ⎋ int — 0 всегда (best-effort); исключения НЕ пробрасываются
    ## @complexity O(N) где N = render-операции
    ## @invariants
    ##   - build_merged_config None → return 0 (skip, log IMP:8, рендер не выполняется)
    ##   - Все render-шаги non-blocking (ошибка → log WARN, continue)
    ##   - Порядок: alert_rules → prometheus → grafana → loki → reload → langfuse → catalog
    ##   - Возвращает 0 всегда; исключения НЕ пробрасываются в orchestrator (R5)
    ##   - Логирует [IMP:9][hook] START/DONE (AC W3: receive-деплой с monitoring-секцией)
    ## @rationale Native Python, 0 subprocess. Паритет до-B8: module-hook monitoring удалён
    ##            волной 118 (B8), вызов так и не был подключён — рендер висел ручным.
    """
    logger.info("[IMP:9][hook] === monitoring on-project-deploy START: %s ===", project_name)

    # Load + merge configs (L1←L2←L3); None → skip (backward compat, без рендера)
    config = build_merged_config(project_dir, project_name, node_name, platform_root)
    if config is None:
        logger.info("[IMP:8][hook] No monitoring config — skipping hook for %s", project_name)
        return 0

    # Execute all render steps non-blocking. Порядок паритет исходного main():
    # alert_rules → prometheus → grafana → loki → reload → langfuse → catalog (DevPlan 138 §4.3).
    _render_step("alert_rules", generate_alert_rules, config)
    _render_step("prometheus", generate_prometheus_target, config)
    _render_step("grafana", generate_grafana_dashboard, config)
    _render_step("loki", update_loki_retention, config)
    _render_step("reload", reload_monitoring_services)
    _render_step("langfuse", create_langfuse_project, config)
    _render_step("catalog", refresh_catalog, platform_root)

    logger.info("[IMP:9][hook] === monitoring on-project-deploy DONE: %s ===", project_name)
    return 0


# endregion FUNC_run_monitoring_reconfig


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

    ## @purpose  Parse args → resolve platform_root → logging →
    ##            return run_monitoring_reconfig(...) (DevPlan 138 W3 §4.3).
    ##            Полная обратная совместимость CLI (make render-monitoring fallback жив).
    ## @io
    ##   CLI: --project-dir <dir> --project <name> [--node <name>]
    ##   ⎋ int — exit code (0 = success, 1 = config parse error)
    ## @complexity O(N) where N = total render operations
    ## @invariants
    ##   - Missing args → argparse handles error, exit 1
    ##   - No monitoring section → exit 0 (backward compat)
    ##   - All render steps are non-blocking (errors logged, continue)
    ##   - Execution order: alert_rules → prometheus → grafana → loki → reload → langfuse → catalog
    ##   - Логика в run_monitoring_reconfig (single source для CLI и DeployOrchestrator)
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

    return run_monitoring_reconfig(project_dir, project_name, node_name, platform_root)


if __name__ == "__main__":
    sys.exit(main())
# endregion CLI_ENTRY
