# GREP_SUMMARY: status-page collectors config load-node-yaml resolve-node-yaml-path extract-node-name load-status-metrics vhosts modules schema-version
# STRUCTURE: ▶ resolve_node_yaml_path (exact→glob F1) → ▶ load_node_yaml → ▶ extract_node_name (S-NAME B) → ▶ load_status_metrics (schema ≥2)
#            → ▶ get_vhosts (expose:true) → ▶ get_modules → ⎋ data layer
# region MODULE_CONTRACT
## @purpose  Data layer of status-page collectors package — node.yaml/metrics loading + extraction
##           (extracted from collectors.py, DevPlan 170 W7-E2). Pure functions — all paths passed
##           as parameters by the orchestrator. No module-level env coupling.
## @scope    Consumed by collectors/aggregate.py and core/modules/status-page/app.py
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - resolve_node_yaml_path: exact path wins; else glob NODE_CONFIGS_DIR/*/node.yaml (F1)
##   - load_node_yaml: returns {} on failure (graceful, no raise)
##   - load_status_metrics: checks schema_version ≥ 2 (warning, still returns data)
## @rationale  DevPlan 170 W7-E2 — config.py extracted verbatim from collectors.py with all LDD
##            logs and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from collectors.py
# endregion MODULE_CONTRACT

import json
import os
import pathlib
import sys
from typing import TypedDict, cast

import yaml  # module-level — except-ветка читает yaml.YAMLError (reportPossiblyUnboundVariable)

# AI-0068 (DevPlan 17 T5.4): публичное имя для кросс-модульных потребителей (readiness);
# приватный алиас оставлен для внутренней совместимости
SCHEMA_VERSION_MIN: int = 2  # минимальная schema_version метрик (старые частично совместимы)
_SCHEMA_VERSION_MIN = SCHEMA_VERSION_MIN


# region DATA_ContainerEntry
class ContainerEntry(TypedDict, total=False):
    """Запись контейнера из status-metrics.json (граница JSON, зеркало healthcheck docker_collector)."""

    name: str
    running: bool
    healthy: bool
    exit_code: int | None
    status_line: str
    started_at: str | None
    image: str
    image_id: str
    memory_usage_bytes: int
    memory_limit_bytes: int
    cpu_percent: float
    restart_policy: str
    image_size_bytes: int


# endregion DATA_ContainerEntry


# region DATA_CertEntry
class CertEntry(TypedDict, total=False):
    """Сертификат из status-metrics.json (граница JSON, зеркало healthcheck cert_collector)."""

    cert_id: str
    issuer: str
    subject: str
    not_after_iso: str
    days_remaining: int
    san: list[str]
    source_path: str
    domains: list[str]


# endregion DATA_CertEntry


# region DATA_ProjectMetricsEntry
class ProjectMetricsEntry(TypedDict, total=False):
    """Проект из status-metrics.json (граница JSON, зеркало healthcheck project_collector)."""

    name: str
    domain: str
    code_size_bytes: int
    docker_image: str
    docker_image_size_bytes: int


# endregion DATA_ProjectMetricsEntry


# region DATA_VhostInfo
class VhostInfo(TypedDict):
    """Vhost (expose:true проект из node.yaml) — единица вывода get_vhosts."""

    domain: str
    name: str
    repo_url: str


# endregion DATA_VhostInfo


# region DATA_MetricsData
class MetricsData(TypedDict, total=False):
    """status-metrics.json — корневой документ метрик (граница JSON).

    ## @purpose  Типизированная граница файла метрик: контейнеры/серты/проекты/host/backup
    ##            + schema_version + errors. Потребляется aggregate/readiness/renderer.
    """

    schema_version: int
    generated_at: str | None
    node: str
    containers: list[ContainerEntry]
    certs: list[CertEntry]
    projects: list[ProjectMetricsEntry]
    host: dict[str, object]
    backup: dict[str, object]
    deploy: dict[str, object]  # 170 W12 C5: DeployStatus (last_deploy_at/success/duration_s/status)
    platform_services: list[object]
    errors: list[str]


# endregion DATA_MetricsData


# region FUNC_resolve_node_yaml_path
def resolve_node_yaml_path(path: str) -> str | None:
    """Resolve node.yaml path with glob-fallback for broken mount (F1, S-PATH A).

    # ▶ ┌path┐ → ◇ isfile? → ⎋ exact path
    #          → ◇ listdir(base) skip scripts/secrets → ◇ single candidate? → ⎋
    #          → ◇ multiple → log warning (1 node = 1 context invariant) → ⎋ None

    Exact path wins. Else glob NODE_CONFIGS_DIR/*/node.yaml (1 node = 1 context invariant,
    node_detect convention). Skips scripts/secrets dirs. Returns None if no file found.

    DevPlan 158 W1 T1.3 (F1 fix): on prod, NODE_NAME does not reach compose-env → mount
    resolves to /opt/node-configs/unknown/node.yaml → Docker creates empty dir → load_node_yaml
    reads a directory → {}. This resolver finds the real node.yaml via glob.
    """
    if os.path.isfile(path):
        print(f"[IMP:9][collectors][yaml-path] exact={path} resolved=exact", file=sys.stderr)
        return path
    # path = <node-configs>/<name>/node.yaml. Glob siblings: base = <node-configs> (2 levels up from file).
    name_dir = pathlib.Path(path).parent  # .../node-configs/<name>
    base = pathlib.Path(name_dir).parent  # .../node-configs
    candidates: list[str] = []
    if os.path.isdir(base):
        for entry in sorted(os.listdir(base)):
            if entry in {"scripts", "secrets"}:
                continue
            candidate = os.path.join(base, entry, "node.yaml")
            if os.path.isfile(candidate):
                candidates.append(candidate)
    if len(candidates) == 1:
        print(
            f"[IMP:7][collectors][yaml-path] exact={path} resolved={candidates[0]} (glob fallback F1)",
            file=sys.stderr,
        )
        return candidates[0]
    if len(candidates) > 1:
        print(
            f"[IMP:7][collectors][yaml-path] Multiple node.yaml candidates: {candidates} "
            f"(1 node = 1 context invariant violated?)",
            file=sys.stderr,
        )
    print(f"[IMP:7][collectors][yaml-path] exact={path} resolved=None (no candidates)", file=sys.stderr)
    return None


# endregion FUNC_resolve_node_yaml_path


# region FUNC_load_node_yaml
def load_node_yaml(path: str) -> dict[str, object]:
    """Load and parse node.yaml. Returns empty dict on failure.

    DevPlan 158 W1 T1.3: uses resolve_node_yaml_path() for glob-fallback when the
    exact path is broken (F1 — Docker creates empty dir on missing NODE_NAME).
    """
    resolved = resolve_node_yaml_path(path)
    if resolved is None:
        # None → keep original path for the try/except below (graceful {})
        resolved = path
    try:
        with pathlib.Path(resolved).open(encoding="utf-8") as f:
            raw_data = cast("object", yaml.safe_load(f))  # W11: yaml → Any → object
    except (OSError, ValueError, ImportError, yaml.YAMLError) as e:
        print(f"[IMP:8][status-page][load-yaml] Failed to load node.yaml: {e}", file=sys.stderr)
        return {}
    else:
        return cast("dict[str, object]", raw_data) if isinstance(raw_data, dict) else {}


# endregion FUNC_load_node_yaml


# region FUNC_extract_node_name
def extract_node_name(node_data: dict[str, object], fallback: str = "unknown") -> str:
    """Extract node.name from node.yaml with fallback (S-NAME B).

    # ▶ ┌node_data┐ → ◇ node.name (nested)? → ⎋
    #              → ◇ name (top-level)? → ⎋
    #              → ⎋ fallback

    Primary source: node.yaml `node.name` (nested object). Fallback: top-level `name`,
    then the `fallback` parameter (env NODE_NAME or "unknown").

    DevPlan 158 W1 T1.3 (S-NAME B): primary = node.yaml, NOT metrics["node"].
    Format confirmed T1.6 — node.name is nested in node object.
    """
    node_raw = node_data.get("node")
    node_section = cast("dict[str, object]", node_raw) if isinstance(node_raw, dict) else cast("dict[str, object]", {})
    name = node_section.get("name") or node_data.get("name") or fallback
    result = str(name) if name else fallback
    print(f"[IMP:9][collectors][node-name] node.name={result}", file=sys.stderr)
    return result


# endregion FUNC_extract_node_name


# region FUNC_load_status_metrics
def load_status_metrics(path: str) -> MetricsData:
    """Load status-metrics.json with schema_version check.

    # ▶ ┌path┐ → open JSON → ◇ schema_version >= 2? → return data
    #                                          └→ log warning, return empty
    # On failure → return empty containers/certs/projects/host with errors[]

    Returns full data dict as-is from file, or fallback structure on failure.
    """
    # Protective: Docker bind mount может создать path как директорию (P1)
    if not os.path.isfile(path):
        print(f"[IMP:8][status-page][load-metrics] Path is not a file: {path}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": [f"status-metrics.json not found or is a directory at {path}"],
        }

    try:
        with pathlib.Path(path).open(encoding="utf-8") as f:
            data = cast("MetricsData", json.load(f))  # W11: json → Any → MetricsData

        # Schema version check
        sv = data.get("schema_version", 0)
        if sv < _SCHEMA_VERSION_MIN:
            print(
                f"[IMP:8][status-page][load-metrics] WARNING: schema_version={sv}, expected >=2",
                file=sys.stderr,
            )
            # Still return data — older schema is partially compatible

    except (OSError, ValueError) as e:
        print(f"[IMP:8][status-page][load-metrics] Failed to load status-metrics.json: {e}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": ["Failed to load status-metrics.json"],
        }
    else:
        return data


# endregion FUNC_load_status_metrics


# region FUNC_get_vhosts
def get_vhosts(node_data: dict[str, object]) -> list[VhostInfo]:
    """Extract expose:true domains from node.yaml projects list."""
    projects_raw = node_data.get("projects")
    projects = cast("list[object]", projects_raw) if isinstance(projects_raw, list) else []
    vhosts: list[VhostInfo] = []
    for p in projects:
        if not isinstance(p, dict):
            continue
        project = cast("dict[str, object]", p)
        if project.get("expose", False) is True:
            domain = str(project.get("domain", ""))
            if domain:
                vhosts.append({
                    "domain": domain,
                    "name": str(project.get("name", domain)),
                    "repo_url": str(project.get("repo_url", "")),
                })
    return vhosts


# endregion FUNC_get_vhosts


# region FUNC_get_modules
def get_modules(node_data: dict[str, object]) -> list[str]:
    """Get list of deployed module names from node.yaml."""
    modules_raw = node_data.get("modules")
    return cast("list[str]", modules_raw) if isinstance(modules_raw, list) else []


# endregion FUNC_get_modules
