$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Eliminate 3-way copy-paste of `_extract_context_from_node_yaml()` and 3-way duplicate Python heredoc blocks for project registration. Create single-source-of-truth shared modules.
DESCRIPTION:           Two independent extractions:
  (1) `_extract_context_from_node_yaml()` — 3 copies (state_machine.py:2002, steps.py:925, context_deployer.py:214) → 1 canonical in `core/internal/shared/node_yaml.py`
  (2) Python heredoc blocks for project registration (add-project.sh:719, adopt-project.sh:674, remove-project.sh:212) → 1 canonical in `core/internal/shared/project_registry.py` with 3 public functions: `register_project()`, `deregister_project()`, `list_projects()`
RATIONALE:             Identical logic (29 LOC × 3 = 87 LOC duplicate). Bug fix in one = NOT applied to others → perpetual drift (DRIFT-B5, Brief 077).
ACCEPTANCE_CRITERIA:
  1. `core/internal/shared/__init__.py` exists (empty)
  2. `core/internal/shared/node_yaml.py` exists with `extract_context_from_node_yaml(path, log_tag="context")`
  3. state_machine.py:2002 — removed local copy, imports from shared
  4. steps.py:925 — removed local copy, imports from shared
  5. context_deployer.py:214 — removed local copy, imports from shared
   6. `core/internal/shared/project_registry.py` exists with `register_project()`, `deregister_project()`, `list_projects()`
   7. add-project.sh:719 heredoc → `python3 project_registry.py register`
   8. adopt-project.sh:674 heredoc → `python3 project_registry.py register`
   9. remove-project.sh:212 heredoc → `python3 project_registry.py deregister`
   10. `list_projects()` outputs space-separated `name repo type domain` per line to stdout, exits 0
   11. Unit tests: `tests/unit/test_node_yaml.py`, `tests/unit/test_project_registry.py`
   12. `make gate MODE=fast` — green
   13. `python3 -m pytest tests/unit/test_node_yaml.py tests/unit/test_project_registry.py -v` — all pass
IMPLEMENTS:            Wave 6A — core unification P0, DRIFT-B5
IMPACTS:
  - core/internal/shared/ (NEW: __init__.py, node_yaml.py, project_registry.py)
  - core/internal/bootstrap/lifecycle/state_machine.py (remove local _extract_context → import)
  - core/internal/bootstrap/lifecycle/steps.py (remove local _extract_context → import)
  - core/internal/bootstrap/deploy/context_deployer.py (remove local extract_context → import)
  - core/internal/scaffold/add-project.sh (replace heredoc → python3 call)
  - core/internal/scaffold/adopt-project.sh (replace heredoc → python3 call)
  - core/internal/scaffold/remove-project.sh (replace heredoc → python3 call)
  - tests/unit/test_node_yaml.py (NEW)
  - tests/unit/test_project_registry.py (NEW)
REQUIRES:              None (standalone extraction, no dependencies on other DevPlans)
$END_ARTIFACT_CONTRACT

---

# DevPlan 070: Extract Shared Libraries — EXPANDED

## Source Analysis

### Three copies of `_extract_context_from_node_yaml()` — identical logic, different names

| Location | Line(s) | Function name | Visibility | Log prefix |
|----------|---------|--------------|------------|------------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | 2002–2030 | `_extract_context_from_node_yaml` | private | `[IMP:8][context]` |
| `core/internal/bootstrap/lifecycle/steps.py` | 925–953 | `_extract_context_from_node_yaml` | private (prefixed `_`) | `[IMP:8][step:context]` |
| `core/internal/bootstrap/deploy/context_deployer.py` | 214–244 | `extract_context_from_node_yaml` | **public** | `[IMP:8][context_deployer]` |

All three implement the **identical** algorithm:
1. `import yaml`
2. `yaml.safe_load(open(node_yaml_path))`
3. Check `data.get("context", "")` → string
4. Fallback: `data.get("contexts", [])[0].get("name", "")` or `contexts[0]` if str
5. Return `""` on any exception

**Differences:**
- `context_deployer.py:214` has `## @invariants` docstring (most complete version)
- `state_machine.py:2002` and `steps.py:925` have `## @io` + `## @complexity` but no invariants
- Log prefixes differ: `[context]`, `[step:context]`, `[context_deployer]`

**Decision:** Use `context_deployer.py` version as canonical (public API, has invariants). Add `log_tag` parameter to preserve log prefixes.

### Three Python heredoc blocks for project registration

**add-project.sh:719–755** — REGISTER_IN_NODE_YAML:
- Reads env vars: `REG_NAME`, `REG_REPO`, `REG_TYPE`, `REG_DOMAIN`, `REG_DATABASE`, `REG_NODE_YAML`
- Loads `node_yaml` → checks idempotency (name/repo already exists → skip)
- Creates project entry: `{name, repo, type, domain?, database?}`
- Appends to `data['projects']` → YAML dump

**adopt-project.sh:674–705** — register_in_node_yaml (FUNC_register_in_node_yaml):
- Reads env vars: `ADOPT_NAME`, `ADOPT_REPO`, `ADOPT_DOMAIN`, `ADOPT_YAML`
- Loads `node_yaml` → checks idempotency (name/repo already exists → skip)
- Creates project entry: `{name, repo, type: 'adopted', domain?}`
- Appends → YAML dump
- Difference: `type` is always `'adopted'`, no `database` field

**remove-project.sh:212–234** — unregister_from_node_yaml (FUNC_unregister_from_node_yaml):
- Reads env vars: `UNREG_NAME`, `UNREG_YAML`
- Loads `node_yaml` → filters `projects` list by name
- YAML dump with remaining projects
- Difference: filters (removes), not appends

---

## TASK-1: Create `core/internal/shared/` package

### Files to create

#### `core/internal/shared/__init__.py`
```python
"""Shared library modules for ai-platform internal/bootstrap consumers.

## @purpose — Single-source-of-truth utilities extracted from duplicate copies
##   across bootstrap/lifecycle/, bootstrap/deploy/, and scaffold/.
## @scope — node_yaml.py (YAML context extraction), project_registry.py (scaffold registration)
"""
```

#### `core/internal/shared/node_yaml.py`

**Exact contents based on `context_deployer.py:214–244` (most complete version) with `log_tag` parameter:**

```python
#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml, extract_context, shared, yaml-parser, context-extraction
# STRUCTURE: ▶ extract_context_from_node_yaml(path, log_tag) → ◇ yaml.safe_load → ◇ context str? → ◇ contexts[0]? → ⎋ ""

import logging

logger = logging.getLogger(__name__)


# region FUNC_extract_context_from_node_yaml
## @purpose — Extract context name from node.yaml. One node = one context.
##            Reads context (string) or contexts[0].name (array, first element).
## @io — ⇥ node_yaml_path: str, log_tag: str = "context" → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
## @invariants
##   - Primary: top-level context field (string)
##   - Fallback: contexts[0].name (array, first element)
##   - Returns empty string on parse error
##   - log_tag parameter controls LDD log prefix: [IMP:8][<log_tag>]
## @rationale Extracted from 3 duplicate copies (state_machine.py, steps.py,
##            context_deployer.py) — DRIFT-B5 elimination (Brief 077).
def extract_context_from_node_yaml(node_yaml_path: str, log_tag: str = "context") -> str:
    """Extract context name from node.yaml."""
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return ""
        # Primary: context field (string)
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][%s] Context from node.yaml context field: %s", log_tag, ctx)
            return ctx
        # Fallback: contexts array (first element)
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
            elif isinstance(first, str):
                ctx = first
            if ctx:
                logger.info("[IMP:8][%s] Context from node.yaml contexts[0].name: %s", log_tag, ctx)
                return ctx
    except Exception as e:
        logger.warning("[IMP:7][%s] Failed to parse %s: %s", log_tag, node_yaml_path, e)
    return ""


# endregion FUNC_extract_context_from_node_yaml
```

### Test file: `tests/unit/test_node_yaml.py`

**Test specifications:**

| Test function | Scenario | Input | Expected |
|---|---|---|---|
| `test_extract_context_string` | `context: "myorg"` | node.yaml with `context: myorg` | returns `"myorg"` |
| `test_extract_context_from_array` | `contexts: [{name: "myorg"}]` | node.yaml with `contexts: [{name: myorg, domains: [...]}]` | returns `"myorg"` |
| `test_extract_context_string_first` | `contexts: ["first", "second"]` | node.yaml with `contexts: [first, second]` | returns `"first"` |
| `test_extract_context_missing` | No context field | node.yaml with `domain: example.com` only | returns `""` |
| `test_extract_context_empty_yaml` | Empty file | `""` | returns `""` |
| `test_extract_context_missing_file` | File not found | path to nonexistent file | returns `""` (no raise) |
| `test_extract_context_log_tag` | Verify log tag | `log_tag="my_tag"` | `[IMP:8][my_tag]` in caplog |

**Test file structure (follows `tests/unit/test_secrets_manager.py` pattern):**
- Import via `sys.path.insert` (same directory structure as existing tests)
- Use `tmp_path` for YAML files
- Use `@ldd_trajectory` decorator
- Use `caplog` for log verification

---

## TASK-2: Refactor 3 consumers

### 2a. `state_machine.py` (line 2002)

**REMOVE:** Lines 1997–2030 (entire `FUNC_extract_context_from_node_yaml` region)

**ADD after imports (after line 57 `import steps as _steps`):**
```python
# Shared library import (DevPlan 070 — DRIFT-B5 elimination)
import sys
import os
_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
from node_yaml import extract_context_from_node_yaml  # noqa: E402
```

**UPDATE call sites** — find via grep `_extract_context_from_node_yaml(` in file:
- Replace `_extract_context_from_node_yaml(path)` → `extract_context_from_node_yaml(path, log_tag="context")`

### 2b. `steps.py` (line 925)

**REMOVE:** Lines 921–953 (entire `FUNC__extract_context_from_node_yaml` region)

**ADD after imports:**
```python
# Shared library import (DevPlan 070 — DRIFT-B5 elimination)
import sys, os
_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
from node_yaml import extract_context_from_node_yaml  # noqa: E402
```

**UPDATE call sites** — replace `_extract_context_from_node_yaml(path)` → `extract_context_from_node_yaml(path, log_tag="step:context")`

### 2c. `context_deployer.py` (line 214)

**REMOVE:** Lines 205–244 (entire `FUNC_extract_context_from_node_yaml` region)

**ADD after imports:**
```python
# Shared library import (DevPlan 070 — DRIFT-B5 elimination)
import sys, os
_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)
from node_yaml import extract_context_from_node_yaml  # noqa: E402
```

**UPDATE call sites** — function has same name `extract_context_from_node_yaml(path)` → call now passes through import. Add `log_tag="context_deployer"` to match previous log prefix.

---

## TASK-3: Create `core/internal/shared/project_registry.py`

### File: `core/internal/shared/project_registry.py`

**Two functions: `register_project()` and `deregister_project()`**

```python
#!/usr/bin/env python3
# GREP_SUMMARY: project_registry, node-yaml, register-project, deregister-project, scaffold, idempotent
# STRUCTURE: ▶ register_project(name, repo, type, domain, database, node_yaml) → ◇ idempotency check → ◇ append entry → ⎋ yaml.dump
#            └ deregister_project(name, node_yaml) → ◇ filter projects list → ⎋ yaml.dump

import logging
import os
import sys

logger = logging.getLogger(__name__)


# region FUNC_register_project
## @purpose — Register a project in node.yaml. Idempotent: skips if name/repo already exist.
##            Supports optional domain and database fields. Appends entry to projects list.
## @io — ⇥ name: str, repo: str, project_type: str, node_yaml_path: str,
##        domain: str = "", database: str = "" → ⎋ None (exits via sys.exit)
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Idempotent: if project name or repo already exists → print IMP:9 SKIP, sys.exit(0)
##   - Creates 'projects' key if missing
##   - Writes YAML with default_flow_style=False, sort_keys=False (preserves existing ordering)
##   - Logs to stderr at IMP:9 on success/skip
## @rationale Extracted from add-project.sh:719 heredoc and adopt-project.sh:674 heredoc
##            (DRIFT-B5 elimination, Brief 077). Idempotency check prevents duplicate entries.
def register_project(
    name: str,
    repo: str,
    project_type: str = "",
    node_yaml_path: str = "",
    domain: str = "",
    database: str = "",
    log_prefix: str = "add-project",
) -> None:
    """Register a project in node.yaml. Idempotent. Exits via sys.exit."""
    try:
        import yaml
    except ImportError:
        print(f"[IMP:10][{log_prefix}][register] PyYAML not available — cannot register", file=sys.stderr)
        sys.exit(1)

    if not name or not repo or not node_yaml_path:
        print(f"[IMP:7][{log_prefix}][register] Missing required params (name={name}, repo={repo}, yaml={node_yaml_path})", file=sys.stderr)
        sys.exit(0)

    with open(node_yaml_path) as f:
        data = yaml.safe_load(f)

    if "projects" in data:
        for p in data["projects"]:
            if p.get("name") == name or p.get("repo") == repo:
                print(
                    f"[IMP:9][{log_prefix}][register] Idempotent SKIP — {name} already in node.yaml",
                    file=sys.stderr,
                )
                sys.exit(0)

    entry: dict[str, str] = {"name": name, "repo": repo}
    if project_type:
        entry["type"] = project_type
    if domain:
        entry["domain"] = domain
    if database:
        entry["database"] = database

    if "projects" not in data:
        data["projects"] = []
    data["projects"].append(entry)

    with open(node_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(f"[IMP:9][{log_prefix}][register] Registered {name} → {node_yaml_path}", file=sys.stderr)
    sys.exit(0)


# endregion FUNC_register_project


# region FUNC_deregister_project
## @purpose — Remove a project from node.yaml by name. Idempotent.
## @io — ⇥ name: str, node_yaml_path: str → ⎋ None (exits via sys.exit)
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Idempotent: if project not found → sys.exit(0) (no error)
##   - Filters projects list, preserving all other entries
##   - Writes YAML with default_flow_style=False, sort_keys=False
##   - Reports removed count at IMP:9
## @rationale Extracted from remove-project.sh:212 heredoc (DRIFT-B5 elimination, Brief 077).
def deregister_project(
    name: str = "",
    node_yaml_path: str = "",
    log_prefix: str = "remove-project",
) -> None:
    """Remove a project from node.yaml by name. Idempotent. Exits via sys.exit."""
    try:
        import yaml
    except ImportError:
        print(f"[IMP:10][{log_prefix}][unregister] PyYAML not available — cannot deregister", file=sys.stderr)
        sys.exit(1)

    if not name or not node_yaml_path:
        print(f"[IMP:7][{log_prefix}][unregister] Missing required params (name={name}, yaml={node_yaml_path})", file=sys.stderr)
        sys.exit(0)

    with open(node_yaml_path) as f:
        data = yaml.safe_load(f)

    if "projects" not in data:
        print(f"[IMP:8][{log_prefix}][unregister] No projects section — nothing to remove", file=sys.stderr)
        sys.exit(0)

    orig_count = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p.get("name") != name]
    removed = orig_count - len(data["projects"])

    with open(node_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(
        f"[IMP:9][{log_prefix}][unregister] Removed '{name}' from {node_yaml_path} ({removed} entries removed)",
        file=sys.stderr,
    )
    sys.exit(0)


# endregion FUNC_deregister_project


# region FUNC_list_projects
## @purpose — List all projects registered in node.yaml. Outputs one line per project to stdout,
##            space-separated: name repo type domain. Empty fields output as "-".
## @io — ⇥ node_yaml_path: str → ⎋ None (writes to stdout, exits via sys.exit)
## @complexity — O(N) where N = len(projects)
## @invariants
##   - Outputs to stdout (designed for shell `grep` / `while read` consumers)
##   - Empty projects list → exits 0, no stdout output
##   - Missing projects key → exits 0, no stdout output
##   - Errors (missing file, invalid YAML) → exits 1 with message to stderr
## @rationale Extracted from duplicate project-existence checks in adopt-project.sh:687 and
##            add-project.sh:725 heredocs (DRIFT-B5 elimination, Brief 077).
def list_projects(
    node_yaml_path: str = "",
    log_prefix: str = "list-projects",
) -> None:
    """List all projects. Outputs 'name repo type domain' per line to stdout."""
    try:
        import yaml
    except ImportError:
        print(f"[IMP:10][{log_prefix}][list] PyYAML not available", file=sys.stderr)
        sys.exit(1)

    if not node_yaml_path:
        print(f"[IMP:7][{log_prefix}][list] Missing node_yaml_path", file=sys.stderr)
        sys.exit(1)

    try:
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
    except (FileNotFoundError, yaml.YAMLError) as e:
        print(f"[IMP:8][{log_prefix}][list] Failed to read {node_yaml_path}: {e}", file=sys.stderr)
        sys.exit(1)

    projects = data.get("projects", []) if isinstance(data, dict) else []
    for p in projects:
        name = p.get("name", "-") or "-"
        repo = p.get("repo", "-") or "-"
        ptype = p.get("type", "-") or "-"
        domain = p.get("domain", "-") or "-"
        print(f"{name} {repo} {ptype} {domain}")

    print(f"[IMP:9][{log_prefix}][list] Listed {len(projects)} project(s) from {node_yaml_path}", file=sys.stderr)
    sys.exit(0)


# endregion FUNC_list_projects


# region FUNC_CLI
## @purpose — CLI entrypoint. Usage:
##   python3 project_registry.py register --name X --repo Y --type Z --node-yaml N [--domain D] [--database DB] [--log-prefix P]
##   python3 project_registry.py deregister --name X --node-yaml N [--log-prefix P]
##   python3 project_registry.py list --node-yaml N [--log-prefix P]
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Project Registry — register/deregister/list projects in node.yaml")
    sub = parser.add_subparsers(dest="action", required=True)

    reg = sub.add_parser("register", help="Register a project")
    reg.add_argument("--name", required=True)
    reg.add_argument("--repo", required=True)
    reg.add_argument("--type", default="")
    reg.add_argument("--node-yaml", required=True)
    reg.add_argument("--domain", default="")
    reg.add_argument("--database", default="")
    reg.add_argument("--log-prefix", default="add-project")

    dereg = sub.add_parser("deregister", help="Deregister a project")
    dereg.add_argument("--name", required=True)
    dereg.add_argument("--node-yaml", required=True)
    dereg.add_argument("--log-prefix", default="remove-project")

    lst = sub.add_parser("list", help="List all projects")
    lst.add_argument("--node-yaml", required=True)
    lst.add_argument("--log-prefix", default="list-projects")

    args = parser.parse_args()

    if args.action == "register":
        register_project(
            name=args.name,
            repo=args.repo,
            project_type=getattr(args, "type", ""),
            node_yaml_path=getattr(args, "node_yaml", ""),
            domain=getattr(args, "domain", ""),
            database=getattr(args, "database", ""),
            log_prefix=args.log_prefix,
        )
    elif args.action == "deregister":
        deregister_project(
            name=args.name,
            node_yaml_path=getattr(args, "node_yaml", ""),
            log_prefix=args.log_prefix,
        )
    elif args.action == "list":
        list_projects(
            node_yaml_path=getattr(args, "node_yaml", ""),
            log_prefix=args.log_prefix,
        )


# endregion FUNC_CLI
```

### Test file: `tests/unit/test_project_registry.py`

| Test function | Scenario | Input | Expected |
|---|---|---|---|
| `test_register_new_project` | New project added to empty node.yaml | name, repo, type, node_yaml_path (tmp) | entry in `projects[]`, sys.exit(0) |
| `test_register_idempotent_by_name` | Same name already registered | call twice, same name | second call: sys.exit(0), `[IMP:9].*SKIP` in stderr |
| `test_register_idempotent_by_repo` | Same repo already registered | call with different name, same repo | sys.exit(0), skip |
| `test_register_with_domain_and_database` | All optional fields | domain="ex.com", database="pg" | entry has both fields |
| `test_deregister_existing` | Remove existing project | node.yaml with 3 projects, remove middle | 2 remain, `removed=1` |
| `test_deregister_nonexistent` | Remove non-existing | name not in list | sys.exit(0), `removed=0` |
| `test_deregister_empty_projects` | node.yaml has no `projects` key | deregister any | sys.exit(0), no error |
| `test_list_projects_empty` | No projects in node.yaml | node.yaml with no `projects` key | exit 0, no stdout |
| `test_list_projects_multiple` | Multiple projects | node.yaml with 3 projects | 3 lines to stdout, `name repo type domain` format |
| `test_list_projects_missing_file` | File not found | path to nonexistent file | exit 1, error to stderr |

---

## CLI Interface Specification — `core/internal/shared/project_registry.py`

### Public API Contract

| Function | Signature | Returns | Exit Code | Side Effects |
|----------|-----------|---------|-----------|-------------|
| `register_project` | `(name: str, repo: str, project_type: str = "", node_yaml_path: str = "", domain: str = "", database: str = "", log_prefix: str = "add-project") -> None` | `None` (exits) | 0 on success/skip, 1 on ImportError | Writes node.yaml |
| `deregister_project` | `(name: str = "", node_yaml_path: str = "", log_prefix: str = "remove-project") -> None` | `None` (exits) | 0 on success/not-found, 1 on ImportError | Writes node.yaml |
| `list_projects` | `(node_yaml_path: str = "", log_prefix: str = "list-projects") -> None` | `None` (exits) | 0 on success/empty-list, 1 on error | Writes to stdout |

### Error Handling Contract

| Scenario | Function(s) | Exit Code | Stderr | Stdout |
|----------|------------|-----------|--------|--------|
| PyYAML not installed | all | 1 | `[IMP:10]` message | — |
| Missing required param (name, repo, or node_yaml_path) | `register` | 0 (skip) | `[IMP:7]` warning | — |
| Missing required param (name or node_yaml_path) | `deregister` | 0 (skip) | `[IMP:7]` warning | — |
| Missing required param (node_yaml_path) | `list` | 1 | `[IMP:7]` error | — |
| FileNotFoundError / YAMLError | `list` | 1 | `[IMP:8]` error | — |
| Name/repo already exists (idempotent) | `register` | 0 (skip) | `[IMP:9]` SKIP | — |
| Name not found (idempotent) | `deregister` | 0 | `[IMP:9]` removed=0 | — |
| No `projects` key in YAML | `deregister`, `list` | 0 | `[IMP:8]` / `[IMP:9]` | empty (list) |
| YAML parse error (register/deregister) | `register`, `deregister` | falls through to exit(0) from missing-param guard | — | — |
| Successful registration | `register` | 0 | `[IMP:9]` Registered | — |
| Successful deregistration | `deregister` | 0 | `[IMP:9]` Removed | — |
| Successful list | `list` | 0 | `[IMP:9]` count | `name repo type domain` lines |

### CLI Invocation Examples

```bash
# Register a new project
python3 core/internal/shared/project_registry.py register \
    --name "myproject" \
    --repo "myorg/myproject" \
    --type "backend" \
    --node-yaml "/path/to/node.yaml" \
    --domain "example.com" \
    --database "postgres" \
    --log-prefix "add-project"

# Deregister a project
python3 core/internal/shared/project_registry.py deregister \
    --name "myproject" \
    --node-yaml "/path/to/node.yaml" \
    --log-prefix "remove-project"

# List all projects (shell-consumable output)
python3 core/internal/shared/project_registry.py list \
    --node-yaml "/path/to/node.yaml"
# Output:
# myproject myorg/myproject backend example.com
# otherproject myorg/other frontend other.com

# Shell usage: check if project exists
if python3 core/internal/shared/project_registry.py list \
    --node-yaml "$node_yaml" | grep -q "^$PROJECT_NAME "; then
    echo "Project already registered"
fi
```

### ## @rationale (list_projects)
Q: Why add `list_projects()` when the heredoc blocks don't use it?
A: Both `add-project.sh:725` and `adopt-project.sh:687` iterate over `data['projects']` to perform idempotency checks — this is the list operation inlined. Extracting it as a public function enables shell scripts to `grep` stdout for existence checks, eliminates the last remaining duplicate iteration pattern across scaffold scripts. This is forward-looking: DevPlans 079/080 will need project listing for drift detection.

### ## @rationale (CLI Contract Formalization)
Q: Why define a formal CLI contract when the Python function signatures already exist?
A: The VerificationReport (F2) identified that the CLI interface was not explicitly specified — function signatures were embedded in code blocks but not extracted as a standalone contract. Shell wrappers depend on exit codes, stderr format, and stdout format. A formal contract prevents implementation drift between the Python module and shell consumers. This is per DevPlan Step 1.9 (CONTRACT_FORMALIZATION).

---

## TASK-4: Replace heredoc blocks in 3 scaffold scripts

### 4a. `add-project.sh` — lines 712–761

**CURRENT (lines 712–761):**
```bash
REG_NAME="$name" \
REG_REPO="${org}/${name}" \
REG_TYPE="$ptype" \
REG_DOMAIN="$domain" \
REG_DATABASE="$database" \
REG_NODE_YAML="$node_yaml" \
python3 <<'PYEOF' || log_warn "Python registration failed — register manually"
import os, yaml, sys
# ... 36 lines of Python
PYEOF
```

**REPLACEMENT:**
```bash
python3 "${SCRIPT_DIR}/../shared/project_registry.py" register \
    --name "$name" \
    --repo "${org}/${name}" \
    --type "$ptype" \
    ${domain:+--domain "$domain"} \
    ${database:+--database "$database"} \
    --node-yaml "$node_yaml" \
    --log-prefix "add-project" \
    || log_warn "Python registration failed — register manually"
```
> **Path note:** `SCRIPT_DIR` = `core/internal/scaffold/` (add-project.sh:29). `../shared/` resolves to `core/internal/shared/`.

The `yq` branch (lines 697–710) remains unchanged — it's a separate yq-based fallback that doesn't use Python.

### 4b. `adopt-project.sh` — lines 669–705

**CURRENT (lines 669–705):**
```bash
ADOPT_NAME="$PROJECT_NAME" \
ADOPT_REPO="${PROJECT_ORG}/${PROJECT_NAME}" \
ADOPT_DOMAIN="$PROJECT_DOMAIN" \
ADOPT_YAML="$node_yaml" \
python3 <<'PYEOF' || log_imp 8 "-" "Python registration failed — register manually"
import os, yaml, sys
# ... 31 lines of Python
PYEOF
```

**REPLACEMENT:**
```bash
python3 "${SCRIPT_DIR}/../shared/project_registry.py" register \
    --name "$PROJECT_NAME" \
    --repo "${PROJECT_ORG}/${PROJECT_NAME}" \
    --type "adopted" \
    ${PROJECT_DOMAIN:+--domain "$PROJECT_DOMAIN"} \
    --node-yaml "$node_yaml" \
    --log-prefix "adopt" \
    || log_imp 8 "-" "Python registration failed — register manually"
```
> **Path note:** `SCRIPT_DIR` = `core/internal/scaffold/` (adopt-project.sh:29).

Note: `--type adopted` is hardcoded (matches original behavior). No `--database` flag (adopt-project doesn't set database).

### 4c. `remove-project.sh` — lines 212–234

**CURRENT (lines 212–234):**
```bash
UNREG_NAME="$name" UNREG_YAML="$node_yaml" python3 <<'PYEOF'
import os, yaml, sys
# ... 22 lines of Python
PYEOF
```

**REPLACEMENT:**
```bash
python3 "${SCRIPT_DIR}/../shared/project_registry.py" deregister \
    --name "$name" \
    --node-yaml "$node_yaml" \
    --log-prefix "remove-project"
py_rc=$?
```
> **Path note:** `SCRIPT_DIR` = `core/internal/scaffold/` (remove-project.sh:35).

**IMPORTANT:** The `yq` branch (line 205–208) remains unchanged. The replacement only affects the `elif command -v python3` branch.

---

## TASK-5: Verification

### Commands to run

```bash
# 1. Run new unit tests
python3 -m pytest tests/unit/test_node_yaml.py tests/unit/test_project_registry.py -v -s

# 2. Run existing unit tests for affected modules
python3 -m pytest tests/unit/test_state_machine.py tests/unit/test_secrets_manager.py -v

# 3. Full fast gate
make fix-gate && make gate MODE=fast
```

### Rollback procedure

If any consumer breaks:
1. **Revert each file individually** — the extracted logic is identical, so rollback is `git checkout -- <file>` for the 3 consumer files
2. **Delete new files** — `rm core/internal/shared/__init__.py core/internal/shared/node_yaml.py core/internal/shared/project_registry.py`
3. **Delete test files** — `rm tests/unit/test_node_yaml.py tests/unit/test_project_registry.py`
4. **Run gate** — `make gate MODE=fast`

State machine tests already cover the `_extract_context_from_node_yaml` behavior transitively via `test_init_flow_17_steps` and `test_update_flow_6_steps` which verify step execution including context extraction.

---

## Design Decisions

### ## @rationale (log_tag parameter)
Q: Why add `log_tag` instead of hardcoding one prefix?
A: The 3 copies use different log prefixes: `[context]`, `[step:context]`, `[context_deployer]`. Removing these would break log-based monitoring and LDD trajectory verification in tests. The `log_tag` parameter preserves backward compatibility while eliminating code duplication.

### ## @rationale (sys.exit in project_registry)
Q: Why `sys.exit(0)` instead of returning?
A: The original heredoc blocks use `sys.exit(0)` for skip and success. The shell wrappers check `$?` (exit code). Returning would require each shell wrapper to parse stdout, breaking the existing error-handling contract (`|| log_warn`).

### ## @rationale (sys.path.insert pattern)
Q: Why `sys.path.insert` instead of proper package imports?
A: The existing test files (`test_state_machine.py`, `test_secrets_manager.py`) use this pattern. The shared module lives at `core/internal/shared/` while consumers are at `core/internal/bootstrap/lifecycle/` and `core/internal/bootstrap/deploy/`. Relative imports across these paths are fragile. The `sys.path.insert` pattern is consistent with the existing codebase.

### ## @rationale (deregister vs unregister naming)
Q: Why `deregister_project()` instead of `unregister_project()`?
A: `deregister` was chosen in the original DevPlan. The original shell script uses `unregister_from_node_yaml`, but `deregister` is symmetric with `register` (both 8 chars, same prefix length) and is the more common term in REST/resource APIs. The shell wrappers map `deregister` subcommand → `deregister_project()` call. Both terms are unambiguous; consistency with the existing DevPlan takes precedence.

### ## @rationale (list_projects)
Q: Why add `list_projects()` when the heredoc blocks inline the iteration?
A: Both `add-project.sh:725` and `adopt-project.sh:687` iterate over `data['projects']` inside heredocs to perform idempotency checks — this is the list operation inlined. Extracting it as a public function enables shell scripts to `grep` stdout for existence checks, eliminates the last remaining duplicate iteration pattern. Forward-looking: DevPlans 079/080 need project listing for drift detection.

### ## @rationale (CLI Contract Formalization)
Q: Why a formal CLI specification section when signatures are in code blocks?
A: VerificationReport F2 identified the gap: function signatures were embedded in code but not extracted as a standalone contract. Shell wrappers depend on exit codes, stderr format, and stdout format. A formal contract prevents implementation drift between Python module and shell consumers — per DevPlan Step 1.9 (CONTRACT_FORMALIZATION).

---

## File Manifest

| File | Action | Lines affected |
|------|--------|---------------|
| `core/internal/shared/__init__.py` | CREATE | ~10 |
| `core/internal/shared/node_yaml.py` | CREATE | ~50 |
| `core/internal/shared/project_registry.py` | CREATE | ~250 |
| `core/internal/bootstrap/lifecycle/state_machine.py` | REMOVE 1997–2030, ADD import | -34 +7 |
| `core/internal/bootstrap/lifecycle/steps.py` | REMOVE 921–953, ADD import | -33 +7 |
| `core/internal/bootstrap/deploy/context_deployer.py` | REMOVE 205–244, ADD import | -40 +7 |
| `core/internal/scaffold/add-project.sh` | REPLACE 719–755 | -37 +8 |
| `core/internal/scaffold/adopt-project.sh` | REPLACE 674–705 | -32 +8 |
| `core/internal/scaffold/remove-project.sh` | REPLACE 212–234 | -23 +5 |
| `tests/unit/test_node_yaml.py` | CREATE | ~100 |
| `tests/unit/test_project_registry.py` | CREATE | ~130 |

**Total:** 3 new files, 3 modified Python files, 3 modified shell files, 2 new test files.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_node_yaml.py` | `test_extract_context_string` | `context: "myorg"` string field | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_from_array` | `contexts: [{name: "myorg"}]` array | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_string_first` | `contexts: ["first", "second"]` str array | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_missing` | No context field in YAML | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_empty_yaml` | Empty YAML file | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_missing_file` | Nonexistent file path | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_node_yaml.py` | `test_extract_context_log_tag` | Custom log_tag prefix in caplog | `node_yaml.extract_context_from_node_yaml` |
| `tests/unit/test_project_registry.py` | `test_register_new_project` | New project, empty node.yaml | `project_registry.register_project` |
| `tests/unit/test_project_registry.py` | `test_register_idempotent_by_name` | Same name twice → skip | `project_registry.register_project` |
| `tests/unit/test_project_registry.py` | `test_register_idempotent_by_repo` | Same repo twice → skip | `project_registry.register_project` |
| `tests/unit/test_project_registry.py` | `test_register_with_domain_and_database` | Optional domain + database fields | `project_registry.register_project` |
| `tests/unit/test_project_registry.py` | `test_deregister_existing` | Remove middle of 3 projects | `project_registry.deregister_project` |
| `tests/unit/test_project_registry.py` | `test_deregister_nonexistent` | Remove non-existing name | `project_registry.deregister_project` |
| `tests/unit/test_project_registry.py` | `test_deregister_empty_projects` | No `projects` key in YAML | `project_registry.deregister_project` |
| `tests/unit/test_project_registry.py` | `test_list_projects_empty` | No projects in node.yaml | `project_registry.list_projects` |
| `tests/unit/test_project_registry.py` | `test_list_projects_multiple` | 3 projects → 3 stdout lines | `project_registry.list_projects` |
| `tests/unit/test_project_registry.py` | `test_list_projects_missing_file` | Nonexistent file path | `project_registry.list_projects` |

---

## Status: **READY FOR IMPLEMENTATION**

All 3 VerificationReport findings addressed:
- **F1:** DevPlan restored from git. This is the ROOT dependency — no blockers.
- **F2:** CLI Interface Specification added with formal contract (function signatures, return types, exit codes, error handling). `list_projects()` function added.
- **F3:** This expanded document (02-DevPlan-expanded.md) is authoritative. 01-DevPlan.md retains the short form for quick reference.

Blockers: **NONE.** Shared directory does not exist yet — this plan creates it. All 3 duplicate copies verified at exact line numbers (VerificationReport §2). 16 related tests pass (green baseline). Dependency graph: 078, 079, 080, 081 unblocked after 070 implementation.

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- TASK-1: Create `core/internal/shared/__init__.py` + `node_yaml.py` + `project_registry.py`
- TASK-3: Create test files (`test_node_yaml.py`, `test_project_registry.py`)
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-3`

### Wave 2 (depends on Wave 1 — shared modules exist)
- TASK-2: Refactor 3 consumers (state_machine.py, steps.py, context_deployer.py)
- TASK-4: Replace heredoc blocks (add-project.sh, adopt-project.sh, remove-project.sh)
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-4`

### Wave 3 (verification — depends on all tasks)
- TASK-5: Run tests + gate
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-5`

$END_DEVPLAN
