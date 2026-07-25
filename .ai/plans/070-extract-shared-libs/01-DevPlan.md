# DevPlan 070: Extract Shared Libraries — Context + Project Registry

$ARTIFACT_CONTRACT
PURPOSE: Eliminate 3-way copy-paste of `_extract_context_from_node_yaml()` and 3-way duplicate Python heredoc blocks for project registration. Create single-source-of-truth shared modules.
DESCRIPTION: Two independent extractions:
  (1) Extract `_extract_context_from_node_yaml()` from state_machine.py, steps.py, context_deployer.py into `core/internal/shared/node_yaml.py`. All 3 consumers import from shared module. Remove 2 redundant copies.
  (2) Extract duplicate Python heredoc blocks from add-project.sh, adopt-project.sh, remove-project.sh into `core/internal/shared/project_registry.py`. Shell wrappers call `python3 project_registry.py <action>`.
RATIONALE: Identical logic (29 lines × 3 copies = 87 LOC duplicate) in context extraction. Identical project registration/deregistration logic in 3 scaffold scripts. Bug fix in one = NOT applied to others → perpetual drift.
ACCEPTANCE_CRITERIA:
  - `core/internal/shared/node_yaml.py` exists with `extract_context_from_node_yaml()` function
  - state_machine.py, steps.py, context_deployer.py import from shared module (no local copies)
  - `core/internal/shared/project_registry.py` exists with `register_project()`, `deregister_project()` functions
  - add-project.sh:719 heredoc → `python3 project_registry.py register`
  - adopt-project.sh:674 heredoc → `python3 project_registry.py register`
  - remove-project.sh:212 heredoc → `python3 project_registry.py deregister`
  - Unit tests in `tests/unit/test_node_yaml.py`, `tests/unit/test_project_registry.py`
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6A — core unification P0
IMPACTS:
  - core/internal/shared/ (new directory)
  - core/internal/bootstrap/lifecycle/state_machine.py (remove local copy)
  - core/internal/bootstrap/lifecycle/steps.py (remove local copy)
  - core/internal/bootstrap/deploy/context_deployer.py (remove local copy)
  - core/internal/scaffold/add-project.sh (replace heredoc)
  - core/internal/scaffold/adopt-project.sh (replace heredoc)
  - core/internal/scaffold/remove-project.sh (replace heredoc)
REQUIRES: None (standalone extraction)

## Tasks

### T1: Create `core/internal/shared/__init__.py` and `node_yaml.py`
- Extract `extract_context_from_node_yaml()` from context_deployer.py (most complete version — has @invariants docstring and public API)
- Preserve all 3 log prefixes via optional `log_tag` parameter: `extract_context_from_node_yaml(path, log_tag="context")`
- Add unit test: `tests/unit/test_node_yaml.py` — test string context, array context, missing file, empty YAML

### T2: Refactor 3 consumers to import from shared
- `state_machine.py:2002` → `from core.internal.shared.node_yaml import extract_context_from_node_yaml`
- `steps.py:925` → same import
- `context_deployer.py:214` → same import (rename local references)
- Remove local copies (keep only the import + call site)
- All 3 pass existing unit tests

### T3: Create `core/internal/shared/project_registry.py`
- Extract Python code from add-project.sh heredoc (line 719) as `register_project(name, template, dir)`
- Extract from remove-project.sh heredoc (line 212) as `deregister_project(name, node_yaml)`
- Add unit test: `tests/unit/test_project_registry.py`

### T4: Replace heredoc blocks in 3 scaffold scripts
- `add-project.sh` → replace heredoc with `python3 core/internal/shared/project_registry.py register ...`
- `adopt-project.sh` → same
- `remove-project.sh` → replace heredoc with `python3 core/internal/shared/project_registry.py deregister ...`

### T5: Gate + verify
- `make fix-gate && make gate MODE=fast` — green
- `python3 -m pytest tests/unit/test_node_yaml.py tests/unit/test_project_registry.py -v`
