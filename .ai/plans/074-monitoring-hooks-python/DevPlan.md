$START_DEVPLAN

# DevPlan 074: Monitoring Hooks (on-project-deploy.sh) → Python (Expanded)

$ARTIFACT_CONTRACT
PURPOSE: Migrate `core/modules/monitoring/hooks/on-project-deploy.sh` (413 LOC, 19 inline python3 calls — WORST violator in the codebase) to Python module `core/internal/monitoring_config_renderer.py`. Eliminate all inline python3, reduce shell to <30 LOC thin wrapper.
DESCRIPTION: on-project-deploy.sh is a post-deploy hook triggered after project deployment to reconfigure monitoring infrastructure: Prometheus file-based service discovery targets, Grafana dashboards, Loki runtime retention config, Langfuse project creation, alert rules generation, catalog refresh, and Prometheus/Loki HTTP reload. The script contains 19 `python3 -c` inline calls performing YAML loading, JSON field extraction, 3-level config merge, JSON file generation, and YAML file modification. This is the single most complex inline-python3 usage in the codebase — the 3-level recursive merge (L1 defaults → L2 org overrides → L3 project config) executed via nested shell `python3 -c` calls is fragile and ungreppable.
RATIONALE: 19 inline python3 calls, more than any other file in the platform. The 3-level JSON merge logic on lines 114-123 is particularly dangerous — nested `python3 -c` calls inside `python3 -c` with shell variable interpolation. A bug in this merge silently breaks monitoring for ALL projects on a node. Python module with unit tests eliminates this entire class of risk. Additionally, lines 248-288 (Loki runtime config update) is a 40-line inline python3 block that reads, modifies, and writes YAML — far beyond the "one-liner" threshold.
ACCEPTANCE_CRITERIA:
  - `core/internal/monitoring_config_renderer.py` — typed Python module (~400 LOC)
  - `core/modules/monitoring/hooks/on-project-deploy.sh` — reduced to <30 LOC thin wrapper
  - ZERO inline `python3 -c` or `python3 <<PYEOF` calls in on-project-deploy.sh
  - 3-level config merge (L1→L2→L3) unit-tested with sample monitoring configs
  - Prometheus target JSON generation unit-tested — verify exact JSON schema
  - Loki runtime config YAML modification unit-tested — verify idempotent insertion
  - Langfuse project creation — HTTP call tested with mock
  - Grafana dashboard + alert rules rendering — tested with mock template-engine
  - Monitoring service reload (Prometheus + Loki HTTP POST) — tested with mock
  - `tests/unit/test_monitoring_config_renderer.py` — ≥15 test functions
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration (Strangler-Fig discipline)
IMPACTS:
  - core/internal/monitoring_config_renderer.py (NEW — ~400 LOC)
  - core/modules/monitoring/hooks/on-project-deploy.sh (REDUCE: 413 → ~30 LOC)
  - tests/unit/test_monitoring_config_renderer.py (NEW — ~250 LOC)
REQUIRES: core/internal/template-engine.sh (existing — called via subprocess for dashboard/alert rendering)

---

## §1. Inline Python3 Inventory

All 19 inline `python3 -c` blocks in `core/modules/monitoring/hooks/on-project-deploy.sh` (current HEAD):

| # | Line(s) | Function | Pattern | Purpose |
|---|---------|----------|---------|---------|
| 1 | 51-56 | `_load_project_config` | `python3 -c "import sys,json,yaml; data=yaml.safe_load(f); print(json.dumps(data))"` | Load ai-platform.yaml → JSON |
| 2 | 59-63 | `_load_project_config` | `python3 -c "import sys,json; print('true' if isinstance(data.get('monitoring'),dict) else 'false')"` | Check if `monitoring` section exists |
| 3 | 70-74 | `_load_project_config` | `python3 -c "import sys,json; print(data.get('type',''))"` | Extract project type |
| 4 | 81-89 | `_load_project_config` | `python3 -c "import sys,json,yaml; data=yaml.safe_load(f); result=dict(data.get('monitoring',{})); type_defaults=data.get('type-defaults',{}).get('TYPE',{}); result.update(type_defaults); print(json.dumps(result))"` | Load L1 defaults.yaml + type-specific overrides |
| 5 | 98-103 | `_load_project_config` | `python3 -c "import sys,json,yaml; data=yaml.safe_load(f); print(json.dumps(data.get('monitoring',{})))"` | Load L2 context overrides |
| 6 | 108-112 | `_load_project_config` | `python3 -c "import sys,json; print(json.dumps(data.get('monitoring',{})))"` | Extract L3 project monitoring section |
| 7-10 | 114-123 | `_load_project_config` (MERGED_CONFIG) | Nested python3 — outer merge block + 3 inner `echo \| python3 -c "sys.stdin.read()"` passthroughs | **CRITICAL:** 3-level merge L1←L2←L3. This is 4 nested `python3 -c` calls (1 outer + 3 stdin passthroughs) |
| 11 | 125-131 | `_load_project_config` | `python3 -c "import sys,json; needs=data.get('needs',{}); llm=needs.get('llm',False); print(str(bool(llm)).lower())"` | Check `needs.llm` flag |
| 12 | 142-146 | `_generate_prometheus_targets` | `python3 -c "import sys,json; print(str(data.get('metrics','false')).lower())"` | Check `metrics` enabled |
| 13 | 154-158 | `_generate_prometheus_targets` | `python3 -c "import sys,json; print(data.get('metrics_port',3000))"` | Get metrics port |
| 14 | 164-178 | `_generate_prometheus_targets` | `python3 -c "import json; target={...}; json.dump(target,f)"` | Generate Prometheus target JSON file |
| 15 | 190-194 | `_generate_grafana_dashboards` | `python3 -c "import sys,json; print(str(data.get('dashboard','false')).lower())"` | Check `dashboard` enabled |
| 16 | 233-237 | `_update_loki_retention` | `python3 -c "import sys,json; print(data.get('logs_retention','7d'))"` | Get logs retention period |
| 17 | 248-288 | `_update_loki_retention` | `python3 -c "import yaml,sys,os; ... 40-line YAML read/modify/write"` | **LARGEST BLOCK:** Read Loki runtime config YAML, idempotently insert retention stream, write back |
| 18 | 305-309 | `_create_langfuse_project` | `python3 -c "import sys,json; print(data.get('ai_retention','30d'))"` | Get AI retention days |
| 19 | 359-363 | `_generate_alert_rules` | `python3 -c "import sys,json; print(str(data.get('alerting','false')).lower())"` | Check `alerting` enabled |

**Decomposition by pattern:**
- **YAML→JSON loading (×5):** #1, #4, #5, #14, #17 — all reading YAML files into Python data structures
- **JSON field extraction (×10):** #2, #3, #6, #8-10, #11, #12, #13, #15, #16, #18, #19 — simple dict key lookups
- **Config merge (×4):** #7-10 — the nested merge block
- **File generation (×2):** #14 (Prometheus target JSON), #17 (Loki runtime YAML)

**Migration strategy:** All 19 calls consolidate into a single Python module that:
1. Reads all config files directly via PyYAML (no JSON round-trip)
2. Performs 3-level merge in Python
3. Generates Prometheus target JSON, Loki runtime YAML, Grafana dashboards, alert rules
4. Calls HTTP endpoints for Langfuse and Prometheus/Loki reload
5. Shell wrapper passes 3 args (PROJECT_DIR, PROJECT, NODE_NAME) and exits with Python's exit code

---

## §2. Python Module Structure

### 2.1 File: `core/internal/monitoring_config_renderer.py`

```
# GREP_SUMMARY: monitoring-config-renderer prometheus-targets grafana-dashboards loki-retention langfuse-project alert-rules catalog-refresh service-reload deep-merge
# STRUCTURE: ▶ cli:args→dispatch → ◇ load_all_configs→3-level-merge → ⊕ generate_prometheus_target → ⊕ generate_grafana_dashboard → ⊕ update_loki_retention(YAML modify) → ⊕ create_langfuse_project(HTTP POST) → ⊕ generate_alert_rules → ⊕ refresh_catalog(subprocess) → ⊕ reload_services(HTTP POST) → ⎋ exit 0
```

#### 2.1.1 Dataclasses

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProjectMonitoringConfig:
    """Fully resolved monitoring configuration for a single project."""
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
    """Result of a single monitoring render operation."""
    component: str  # prometheus, grafana, loki, langfuse, alerting, catalog, reload
    status: str     # "created", "skipped", "updated", "failed", "noop"
    detail: str = ""
    output_path: Optional[Path] = None
```

#### 2.1.2 Functions — Signatures

```python
# ── Config Loading ───────────────────────────────────────────────────

def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override dict into base dict.
    
    Merge rules (matching current shell behavior — shallow top-level, 
    last-writer-wins for scalar values):
    - For each key in override: if both values are dicts, recursively merge
    - Otherwise: override value replaces base value
    - Keys only in base are preserved
    
    This is a SHALLOW merge at the top level with DEEP merge for nested dicts
    (up to 3 levels: L1 defaults → L2 overrides → L3 project config).
    
    Complexity: O(n) where n = total keys across both dicts.
    """


def load_yaml_config(yaml_path: Path) -> dict:
    """
    Load and parse a YAML file. Returns empty dict on file-not-found.
    
    Non-fatal: missing file → log IMP:6 warning + return {}.
    This matches shell behavior: all config sources are optional.
    
    Raises:
        yaml.YAMLError: on malformed YAML
    """


def load_l1_defaults(defaults_path: Path, project_type: str) -> dict:
    """
    Load L1 monitoring defaults from defaults.yaml.
    
    Steps:
    1. Load defaults.yaml
    2. Extract `monitoring` global section
    3. Extract `type-defaults.<project_type>` section
    4. Merge: global base → type-specific overrides on top
    5. Return merged dict
    
    If defaults.yaml doesn't exist: return empty dict (log IMP:6).
    """


def load_l2_overrides(override_path: Path) -> dict:
    """
    Load L2 context overrides from node-configs/<node>/projects/<project>.yaml.
    
    Extracts only the `monitoring` section. Returns empty dict if file
    doesn't exist or monitoring section absent.
    """


def load_l3_project_config(project_yaml: dict) -> dict:
    """
    Extract L3 monitoring section from ai-platform.yaml (already parsed).
    
    Returns `data.get('monitoring', {})` — empty dict if no monitoring section.
    """


def build_merged_config(
    project_dir: Path,
    project_name: str,
    node_name: str,
    platform_root: Path,
) -> ProjectMonitoringConfig:
    """
    Full pipeline: load ai-platform.yaml → extract project type → 
    load L1 defaults → load L2 overrides → extract L3 config →
    merge L1←L2←L3 → extract flags → return typed config.
    
    Returns None if ai-platform.yaml doesn't exist or has no monitoring section
    (backward compat — monitoring is optional for projects).
    
    This replaces lines 42-134 of the original shell script (the entire 
    _load_project_config function).
    """


# ── Monitoring Component Renderers ───────────────────────────────────

def generate_prometheus_target(
    config: ProjectMonitoringConfig,
    output_dir: Path,
) -> RenderResult:
    """
    Generate Prometheus file-based service discovery target JSON.
    
    Output file: <output_dir>/<project_name>.json
    JSON schema:
    {
        "targets": ["<project>:<port>"],
        "labels": {
            "project": "<project>",
            "type": "<project_type>",
            "node": "<node_name>",
            "service": "<project>"
        }
    }
    
    Skips if metrics_enabled is False (returns status="noop").
    Creates output directory if it doesn't exist.
    
    Replaces: lines 138-183 (_generate_prometheus_targets)
    """


def generate_grafana_dashboard(
    config: ProjectMonitoringConfig,
    template_path: Path,
    output_dir: Path,
) -> RenderResult:
    """
    Generate Grafana dashboard JSON from template.
    
    Uses core/internal/template-engine.sh via subprocess:
      template-engine.sh render <template> <output> PROJECT=<name> TYPE=<type> NODE=<node>
    
    Falls back to sed-based substitution if template-engine.sh is unavailable.
    Creates output directory if it doesn't exist.
    Skips if dashboard_enabled is False.
    Skips if template file doesn't exist (log IMP:6).
    
    Replaces: lines 186-226 (_generate_grafana_dashboards)
    """


def update_loki_retention(
    config: ProjectMonitoringConfig,
    runtime_config_path: Path,
) -> RenderResult:
    """
    Update Loki runtime config YAML with project retention stream.
    
    Steps:
    1. Parse logs_retention string (e.g., "7d", "336h", "forever") → hours
    2. Load existing runtime config YAML (or empty dict if file missing)
    3. Navigate: limits_config → retention_stream (list)
    4. Check if stream for this project already exists (by selector match)
       Selector pattern: '{compose_project="<project>"}'
    5. If exists → status="skipped" (idempotent)
    6. If not exists → insert new rule BEFORE any catch-all rules
       (rules with 'compose_project=~' selector pattern)
    7. Write back YAML with yaml.dump (default_flow_style=False, allow_unicode=True, sort_keys=False)
    
    Retention calculation (matching shell lines 241-246):
    - "forever" → period_h = 0
    - "Nd" → period_h = N * 24
    - "Nh" → period_h = N
    - default → 168 (7 days)
    
    Replaces: lines 229-293 (_update_loki_retention)
    """


def create_langfuse_project(
    config: ProjectMonitoringConfig,
) -> RenderResult:
    """
    Create Langfuse project via HTTP API.
    
    Endpoint: POST http://langfuse:3000/api/public/projects
    Headers: Authorization: Bearer ${LANGFUSE_SECRET_KEY}
    Body: {"name": "<project>", "retention": <ai_retention_days>}
    
    Status mapping:
    - 200/201 → "created"
    - 409 / "already exists" → "skipped"
    - HTTP error / network failure → "failed" (non-fatal, logged)
    
    Skips if needs_llm is False (status="noop").
    Reads LANGFUSE_SECRET_KEY from environment.
    
    Replaces: lines 296-324 (_create_langfuse_project)
    """


def generate_alert_rules(
    config: ProjectMonitoringConfig,
    template_path: Path,
    output_dir: Path,
) -> RenderResult:
    """
    Generate Prometheus alert rules YAML from template.
    
    Uses core/internal/template-engine.sh via subprocess:
      template-engine.sh render <template> <output> PROJECT=<name>
    
    Falls back to sed-based substitution if template-engine.sh is unavailable.
    Skips if alerting_enabled is False.
    Skips if template file doesn't exist.
    
    Replaces: lines 355-388 (_generate_alert_rules)
    """


def refresh_catalog(platform_root: Path) -> RenderResult:
    """
    Invoke catalog generation script.
    
    Runs: <platform_root>/core/internal/catalog/generate-catalog.sh
    Non-fatal: failure logged at IMP:6, status="failed".
    Script not found: status="noop".
    
    Replaces: lines 327-337 (_refresh_catalog)
    """


def reload_monitoring_services() -> list[RenderResult]:
    """
    HTTP POST reload Prometheus and Loki.
    
    - Prometheus: POST http://prometheus:9090/-/reload
    - Loki: POST http://loki:3100/reload
    
    Each call is non-fatal — failures logged, continue to next.
    
    Replaces: lines 340-352 (_reload_services)
    """


# ── CLI Entry Point ──────────────────────────────────────────────────

def main() -> int:
    """
    CLI: python3 monitoring_config_renderer.py \
         --project-dir <dir> --project <name> --node <name>
    
    Orchestrates full monitoring reconfiguration pipeline:
    1. Load + merge configs → ProjectMonitoringConfig
    2. If no monitoring section → exit 0 (backward compat)
    3. Generate alert rules
    4. Generate Prometheus target
    5. Generate Grafana dashboard
    6. Update Loki retention
    7. Reload monitoring services
    8. Create Langfuse project (if LLM needed)
    9. Refresh catalog
    
    Matches execution order of original main() (lines 392-413).
    
    Exit codes: 0 = success, 1 = config parse error
    """
```

#### 2.1.3 LDD Logging Format

```python
logger.info("[IMP:9][monitoring][hook] === monitoring on-project-deploy START: %s ===", project)
logger.info("[IMP:8][monitoring][config] Loading monitoring config for %s (type=%s)", project, ptype)
logger.info("[IMP:9][monitoring][config] Merged monitoring config for %s", project)
logger.info("[IMP:9][monitoring][prometheus] Prometheus target file generated: %s (port=%d)", path, port)
logger.info("[IMP:9][monitoring][grafana] Dashboard generated: %s", path)
logger.info("[IMP:9][monitoring][loki] Loki runtime config updated for %s: %s (%dh)", project, retention, hours)
logger.info("[IMP:9][monitoring][langfuse] Langfuse project created: %s", project)
logger.info("[IMP:9][monitoring][hook] === monitoring on-project-deploy DONE: %s ===", project)
```

### 2.2 Shell Wrapper: `core/modules/monitoring/hooks/on-project-deploy.sh` (TARGET: ~30 LOC)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: monitoring hook on-project-deploy thin-wrapper config-renderer
# STRUCTURE: parse_args(PROJECT_DIR,PROJECT,NODE_NAME) → python3 monitoring_config_renderer.py → ⎋ exit
set -euo pipefail

__LOG_PREFIX="monitoring-hook"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HOOK_PROJECT_DIR="${1:-}"
HOOK_PROJECT="${2:-}"
HOOK_NODE_NAME="${3:-}"

if [[ -z "$HOOK_PROJECT_DIR" || -z "$HOOK_PROJECT" ]]; then
    echo "[IMP:6][monitoring][hook] Missing PROJECT_DIR or PROJECT — skipping monitoring hook" >&2
    exit 0
fi

PLATFORM_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

echo "[IMP:9][monitoring][hook] === monitoring on-project-deploy START: ${HOOK_PROJECT} ===" >&2

python3 "${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py" \
    --project-dir "$HOOK_PROJECT_DIR" \
    --project "$HOOK_PROJECT" \
    --node "$HOOK_NODE_NAME"

echo "[IMP:9][monitoring][hook] === monitoring on-project-deploy DONE: ${HOOK_PROJECT} ===" >&2
```

**Shell wrapper removes:**
- All `_load_project_config` logic (85 lines)
- All `_generate_prometheus_targets` logic (45 lines)
- All `_generate_grafana_dashboards` logic (41 lines)
- All `_update_loki_retention` logic (64 lines)
- All `_create_langfuse_project` logic (29 lines)
- All `_refresh_catalog` logic (11 lines)
- All `_reload_services` logic (12 lines)
- All `_generate_alert_rules` logic (33 lines)
- ALL 19 inline `python3 -c` calls

**Shell wrapper preserves:**
- Arg validation (PROJECT_DIR + PROJECT required → skip gracefully)
- PLATFORM_ROOT resolution
- IMP:9 START/DONE log markers
- Non-fatal exit behavior (exit 0 on missing args, matching original line 33-35)

---

## §3. Shell Variable → Python Parameter Mapping

| Shell variable | Python CLI parameter | Type | Notes |
|---------------|---------------------|------|-------|
| `HOOK_PROJECT_DIR` | `--project-dir <dir>` | Path | Absolute path to project directory |
| `HOOK_PROJECT` | `--project <name>` | str | Project name (e.g., "my-backend") |
| `HOOK_NODE_NAME` | `--node <name>` | str | Node name (e.g., "tronyx-vps") |
| `PLATFORM_ROOT` | `--platform-root <path>` (auto-resolved in Python: `Path(__file__).resolve().parent.parent.parent.parent`) | Path | Python resolves from its own file location |
| `ai-platform.yaml` (per-project) | Resolved as `project_dir / "ai-platform.yaml"` | Path | Python resolves from --project-dir |
| `defaults.yaml` | `platform_root / "core/modules/monitoring/defaults.yaml"` | Path | Fixed path relative to platform root |
| `node-configs/<node>/projects/<project>.yaml` | `platform_root / "node-configs" / node / "projects" / f"{project}.yaml"` | Path | Context override path template |
| `$LANGFUSE_SECRET_KEY` | `os.environ.get("LANGFUSE_SECRET_KEY")` | str\|None | Python reads from environment |
| `loki-runtime-config.yml` | `platform_root / "core/modules/logging/config/loki-runtime-config.yml"` | Path | Fixed path |
| `prometheus-targets/` | `platform_root / "prometheus-targets"` | Path | Fixed path (created if missing) |
| `project-template.json` (Grafana) | `platform_root / "core/modules/monitoring/config/dashboards/project-template.json"` | Path | Fixed path |
| `alert-rules.yml` (template) | `platform_root / "core/modules/monitoring/config/alert-rules.yml"` | Path | Fixed path |
| Alert rules output dir | `/opt/prometheus/rules` | Path | Hardcoded on VPS (matching original line 371) |

| Shell function | Python function | Equivalence |
|----------------|-----------------|-------------|
| `_load_project_config` | `build_merged_config()` | Full replacement: YAML load + type extraction + 3-level merge + flag extraction |
| `_generate_prometheus_targets` | `generate_prometheus_target()` | Full replacement |
| `_generate_grafana_dashboards` | `generate_grafana_dashboard()` | Full replacement (subprocess call to template-engine.sh) |
| `_update_loki_retention` | `update_loki_retention()` | Full replacement (YAML read/modify/write in Python) |
| `_create_langfuse_project` | `create_langfuse_project()` | Full replacement (HTTP POST via requests/urllib) |
| `_refresh_catalog` | `refresh_catalog()` | Full replacement (subprocess call to generate-catalog.sh) |
| `_reload_services` | `reload_monitoring_services()` | Full replacement (HTTP POST to Prometheus + Loki) |
| `_generate_alert_rules` | `generate_alert_rules()` | Full replacement (subprocess call to template-engine.sh) |

---

## §4. Data Flow (After Migration)

```
deploy-project.sh → on-project-deploy.sh $PROJECT_DIR $PROJECT $NODE_NAME
  → shell: validate args → call python3 monitoring_config_renderer.py
    → build_merged_config(project_dir, project, node, platform_root)
      → load_yaml_config(ai-platform.yaml) → project_yaml
      → extract project.type, project.monitoring, project.needs.llm
      → has monitoring section? NO → exit 0 (backward compat)
      → load_l1_defaults(defaults.yaml, project_type) → l1_config
      → load_l2_overrides(node-configs/<node>/projects/<project>.yaml) → l2_config
      → load_l3_project_config(project_yaml) → l3_config
      → deep_merge(l1_config, l2_config) → intermediate
      → deep_merge(intermediate, l3_config) → merged
      → extract flags: metrics, metrics_port, dashboard, alerting, logs_retention, ai_retention
      → ProjectMonitoringConfig
    → generate_alert_rules(config, template, output_dir)
    → generate_prometheus_target(config, output_dir)
    → generate_grafana_dashboard(config, template, output_dir)
    → update_loki_retention(config, runtime_config_path)
    → reload_monitoring_services()
    → create_langfuse_project(config)
    → refresh_catalog(platform_root)
    → exit 0
```

---

## §5. Test Specifications

### 5.1 File: `tests/unit/test_monitoring_config_renderer.py`

#### Test Data Structure

Sample YAML fixtures (written to `tmp_path` via fixtures):

**test_ai_platform_yaml:**
```yaml
# Minimal ai-platform.yaml for tests
type: backend
monitoring:
  metrics: true
  metrics_port: 9090
  dashboard: false
  alerting: true
  logs_retention: 14d
needs:
  llm: true
```

**test_defaults_yaml:** (from `core/modules/monitoring/defaults.yaml`, minimal subset for tests)
```yaml
# L1 defaults
monitoring:
  metrics: false
  metrics_port: 3000
  logs_retention: 7d
  ai_retention: 30d
  alerting: false
  dashboard: false
type-defaults:
  backend:
    metrics: true
    metrics_port: 8080
    logs_retention: 14d
    alerting: false
    dashboard: false
```

**test_l2_override_yaml:**
```yaml
# L2 context override
monitoring:
  metrics_port: 8080
  alerting: true
```

**test_loki_runtime_config_yaml:**
```yaml
# Pre-existing Loki runtime config
limits_config:
  retention_stream:
    - selector: '{compose_project="existing-project"}'
      priority: 0
      period: 720h
```

#### Test Cases

| # | Test function | Scenario | Module under test | Mock strategy |
|---|--------------|----------|-------------------|---------------|
| T4.1 | `test_deep_merge_simple` | Merge two flat dicts — override wins | `deep_merge()` | Pure function, no mocks needed |
| T4.2 | `test_deep_merge_nested_3levels` | Merge 3 nested levels: L1←L2←L3 — verify deepest override wins | `deep_merge()` | Pure function |
| T4.3 | `test_deep_merge_preserves_base_keys` | Keys only in base dict survive merge | `deep_merge()` | Pure function |
| T4.4 | `test_load_l1_defaults_with_type` | Load defaults.yaml + backend type-defaults → merged | `load_l1_defaults()` | tmp_path YAML fixture |
| T4.5 | `test_load_l1_defaults_missing_file` | defaults.yaml not found → empty dict, not exception | `load_l1_defaults()` | Non-existent path |
| T4.6 | `test_load_l2_overrides_present` | Context override file exists with monitoring section → parsed | `load_l2_overrides()` | tmp_path YAML fixture |
| T4.7 | `test_load_l2_overrides_missing_file` | Context override file not found → empty dict | `load_l2_overrides()` | Non-existent path |
| T4.8 | `test_build_merged_config_full_pipeline` | Full pipeline: ai-platform.yaml + defaults + override → correct merged config | `build_merged_config()` | tmp_path fixtures for all 3 files |
| T4.9 | `test_build_merged_config_no_monitoring_section` | ai-platform.yaml without monitoring section → returns None (backward compat) | `build_merged_config()` | tmp_path with only `type:` field |
| T4.10 | `test_build_merged_config_no_ai_yaml` | ai-platform.yaml doesn't exist → returns None | `build_merged_config()` | Non-existent project dir |
| T4.11 | `test_generate_prometheus_target_json_schema` | Generate target JSON → verify exact keys (targets, labels) | `generate_prometheus_target()` | tmp_path output dir, verify file contents |
| T4.12 | `test_generate_prometheus_target_metrics_disabled` | metrics_enabled=False → skip, status="noop", no file written | `generate_prometheus_target()` | Config with metrics_enabled=False |
| T4.13 | `test_update_loki_retention_new_stream` | No existing stream for project → new rule inserted | `update_loki_retention()` | tmp_path with existing config, verify YAML written |
| T4.14 | `test_update_loki_retention_idempotent` | Same project twice → second call detects EXISTS, skips | `update_loki_retention()` | tmp_path, call twice, verify no duplicate rules |
| T4.15 | `test_update_loki_retention_before_catch_all` | New rule inserted BEFORE `compose_project=~` catch-all rules | `update_loki_retention()` | tmp_path with catch-all rule, verify insertion position |
| T4.16 | `test_update_loki_retention_forever_period` | logs_retention="forever" → period_h=0 | `update_loki_retention()` | Verify period="0h" in written YAML |
| T4.17 | `test_generate_alert_rules_enabled` | alerting_enabled=True → template-engine.sh called via subprocess | `generate_alert_rules()` | Monkeypatch subprocess.run, verify args |
| T4.18 | `test_generate_alert_rules_disabled` | alerting_enabled=False → skip, status="noop" | `generate_alert_rules()` | Config with alerting_enabled=False |
| T4.19 | `test_retention_parsing_variants` | "7d"→168h, "336h"→336h, "forever"→0h, invalid→168h | retention calculation in `update_loki_retention()` | Pure function |
| T4.20 | `test_cli_missing_args` | Missing --project or --project-dir → exit 1 | `main()` | argparse parse |
| T4.21 | `test_all_components_noop_when_no_monitoring` | ai-platform.yaml without monitoring section → all components skip | `main()` (integration) | tmp_path with no-monitoring yaml |

#### Test Invariants
- All tests use `tmp_path` fixture (no hardcoded paths)
- HTTP-dependent tests (T4.?? Langfuse, reload) use `monkeypatch` or `responses` library
- No Docker daemon required for any test
- Each test verifies `[IMP:9]` log presence via `caplog` fixture
- Tests run in `tests/unit/` → no `@pytest.mark.requires_docker`
- YAML fixtures are written to `tmp_path` in test setup (not committed as separate files)

---

## §6. Make Targets & Callers Affected

| Caller | File | How it calls `on-project-deploy.sh` | Change |
|--------|------|-------------------------------------|--------|
| `deploy-project.sh` | `core/internal/deploy/deploy-project.sh` | `bash core/modules/monitoring/hooks/on-project-deploy.sh "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"` | **NO CHANGE** — wrapper interface compatible |
| Direct hook invocation | (any) | `bash on-project-deploy.sh <dir> <name> <node>` | **NO CHANGE** — 3 positional args preserved |

**No Makefile changes required.** `on-project-deploy.sh` is called directly from deploy-project.sh, not through any Make target. The hook is invoked at deploy time, not at build time.

---

## §7. Idempotency & Non-Fatality

| Component | Idempotency check | Non-fatal policy |
|-----------|-------------------|------------------|
| Prometheus target JSON | File overwritten on every deploy (always latest config) | File write failure → logged, continue |
| Grafana dashboard | File overwritten via template-engine | Template not found → logged, skip, continue |
| Loki retention stream | Check selector exists before inserting → idempotent | YAML parse failure → logged, skip, continue |
| Langfuse project | HTTP API returns 409 if exists → detected, skip | HTTP error → logged, skip, continue (may not be configured) |
| Alert rules | File overwritten via template-engine | Template not found → logged, skip, continue |
| Catalog refresh | External script, own idempotency | Script failure → logged, continue |
| Prometheus/Loki reload | HTTP POST, idempotent (no-op if unchanged) | HTTP error → logged, continue |

**Non-fatality invariant (matching original):** Errors at any step are logged at IMP:6-8 but do NOT block the remaining steps. This is a post-deploy hook — monitoring reconfiguration failures must not block project deployment.

---

## §8. Implementation Notes

### 8.1 Dependencies

- `pyyaml` — already in project (yaml_query.py)
- `argparse` — stdlib
- `subprocess` — stdlib
- `pathlib` — stdlib
- `logging` — stdlib
- `urllib.request` or `requests` — for HTTP calls (Langfuse, Prometheus reload, Loki reload). **Use `urllib.request` from stdlib** to avoid adding `requests` as a dependency.

### 8.2 Subprocess Calls

| Target | Command | Fallback |
|--------|---------|----------|
| template-engine.sh | `[platform_root]/core/internal/template-engine.sh render <tmpl> <out> KEY=VALUE...` | sed substitution (matching original lines 219-224, 384-386) |
| generate-catalog.sh | `[platform_root]/core/internal/catalog/generate-catalog.sh` | None — script not found → status="noop" |

### 8.3 Template Engine Fallback

The original shell script has a sed-based fallback (lines 219-224, 384-386) when `template-engine.sh` is unavailable. The Python module preserves this fallback:

```python
def _render_template(template_path: Path, output_path: Path, variables: dict[str, str], 
                     platform_root: Path) -> None:
    """Render template via template-engine.sh, with sed fallback."""
    engine_path = platform_root / "core/internal/template-engine.sh"
    if engine_path.is_file():
        args = [str(engine_path), "render", str(template_path), str(output_path)]
        for k, v in variables.items():
            args.append(f"{k}={v}")
        subprocess.run(args, check=True)
    else:
        # Fallback: sed-based substitution
        content = template_path.read_text()
        for k, v in variables.items():
            content = content.replace(f"${k}", v)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
```

### 8.4 Strangler-Fig Step

This is a Tier 1 extraction: the script has 19 inline python3 calls. ALL logic moves to `monitoring_config_renderer.py`. The shell wrapper handles only arg validation and dispatch.

---

## §9. Task Decomposition

### TASK-1: Implement config loading + deep merge
- **Output:** `core/internal/monitoring_config_renderer.py` (~150 LOC)
- **Deliverables:** `ProjectMonitoringConfig` dataclass, `RenderResult` dataclass, `deep_merge()`, `load_yaml_config()`, `load_l1_defaults()`, `load_l2_overrides()`, `load_l3_project_config()`, `build_merged_config()`
- **Dependencies:** None
- **Complexity:** 5/10
- **Acceptance:** `build_merged_config(tmp_path_with_fixtures)` returns correct typed config

### TASK-2: Implement monitoring component renderers
- **Output:** `core/internal/monitoring_config_renderer.py` (+200 LOC)
- **Deliverables:** `generate_prometheus_target()`, `generate_grafana_dashboard()`, `update_loki_retention()`, `create_langfuse_project()`, `generate_alert_rules()`, `refresh_catalog()`, `reload_monitoring_services()` + CLI `main()`
- **Dependencies:** TASK-1
- **Complexity:** 7/10
- **Acceptance:** Full pipeline runs with mock configs in dry-run mode

### TASK-3: Create thin shell wrapper
- **Output:** `core/modules/monitoring/hooks/on-project-deploy.sh` (rewrite: ~30 LOC)
- **Deliverables:** Arg validation + python3 dispatch
- **Dependencies:** TASK-1, TASK-2
- **Complexity:** 1/10
- **Acceptance:** `bash on-project-deploy.sh /path/to/project my-project my-node` calls python3 correctly

### TASK-4: Write unit tests
- **Output:** `tests/unit/test_monitoring_config_renderer.py` (~250 LOC)
- **Deliverables:** 21 test functions covering all scenarios from §5.1
- **Dependencies:** TASK-1, TASK-2
- **Complexity:** 6/10
- **Acceptance:** `python -m pytest tests/unit/test_monitoring_config_renderer.py -s -v` — all 21 tests green

### TASK-5: Gate validation
- **Output:** `make fix-gate && make gate MODE=fast` — green
- **Dependencies:** TASK-1 through TASK-4
- **Complexity:** 1/10
- **Acceptance:** Gate passes, zero new failures

---

## §10. Parallel Groups

### Wave 1 (independent, no shared files)
- Tasks: TASK-1
- Command: `coder Read DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (depends on Wave 1)
- Tasks: TASK-2, TASK-4 (parallel — tests can start after TASK-1, TASK-2 provides final signatures)
- Command: `coder Read DevPlan.md, implement Wave 2: TASK-2 and TASK-4`

### Wave 3 (depends on Wave 2)
- Tasks: TASK-3
- Command: `coder Read DevPlan.md, implement Wave 3: TASK-3`

### Wave 4 (final validation)
- Tasks: TASK-5
- Command: `coder Read DevPlan.md, implement Wave 4: TASK-5`

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/074-monitoring-hooks-python/01-DevPlan.md, implement Wave 1: TASK-1
```

$END_DEVPLAN
