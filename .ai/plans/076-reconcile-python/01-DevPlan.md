# DevPlan 076: reconcile-projects.sh → Python

$ARTIFACT_CONTRACT
PURPOSE: Migrate reconcile-projects.sh (~300 LOC, 6 inline python3 calls) to Python. JSON parsing of node.yaml project lists + SSH loop — straightforward extraction.
DESCRIPTION: core/internal/deploy/reconcile-projects.sh compares desired state (node.yaml projects list) with actual state (docker ps on VPS) and reconciles differences. Contains 6 inline python3 calls for JSON parsing of node.yaml. The core logic (diff computation, SSH command generation) is Python-suitable.
RATIONALE: 6 inline python3 calls = Tier 1 trigger. The logic is straightforward (parse → diff → generate commands → execute via SSH) and maps cleanly to Python. Lowest effort of the 4 Tier 1 extractions.
ACCEPTANCE_CRITERIA:
  - `core/internal/reconciler_projects.py` — Python module (note: not to confuse with core/internal/bootstrap/converge/reconciler.py)
  - Or: merge into existing `reconciler.py` if scope allows
  - reconcile-projects.sh — reduced to <30 LOC thin wrapper
  - Zero inline `python3 -c` calls
  - `tests/unit/test_project_reconciler.py`
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration
IMPACTS:
  - core/internal/reconciler_projects.py (new) OR integrated into existing reconciler
  - core/internal/deploy/reconcile-projects.sh (reduce)
  - tests/unit/test_project_reconciler.py (new)
REQUIRES: None (can run parallel to 070-075)

## Tasks

### T1: Evaluate integration vs new module
- Check `core/internal/bootstrap/converge/reconciler.py` — does it already handle project reconciliation?
- Decision: if reconciler.py is solely for module-level converge, create separate `reconciler_projects.py`
- If reconciler.py handles both, extend it

### T2: Implement Python module
- `reconciler_projects.py`:
  - `parse_node_yaml_projects(node_yaml_path)` → list of project configs
  - `get_actual_projects(ssh_host)` → list of running projects (via docker ps)
  - `compute_diff(desired, actual)` → {to_start, to_stop, to_update}
  - `generate_ssh_commands(diff, ssh_host)` → list of ssh commands
  - Uses `core.lib.ssh` wrapper (or direct subprocess with ssh)

### T3: Create thin shell wrapper
- reconcile-projects.sh → parse args → call `python3 reconciler_projects.py`
- Keep <30 LOC

### T4: Unit tests
- `tests/unit/test_project_reconciler.py`:
  - `test_parse_node_yaml` — mock node.yaml, verify project list extraction
  - `test_diff_no_changes` — desired == actual → empty diff
  - `test_diff_missing_project` — project in desired but not actual → to_start
  - `test_diff_extra_project` — project in actual but not desired → to_stop

### T5: Gate
- `make fix-gate && make gate MODE=fast` — green
