$START_DEVPLAN

# DevPlan 073: provision-environment.sh → Python (Expanded)

$ARTIFACT_CONTRACT
PURPOSE: Migrate provision-environment.sh (442 LOC, 13 inline python3 calls) to Python module `core/internal/provisioner.py`. Eliminate all inline python3 from shell script, reduce shell to <50 LOC thin wrapper.
DESCRIPTION: provision-environment.sh orchestrates Docker networks, volumes, and CI environment variable provisioning from `platform-env.yaml`. Current implementation: 442 LOC shell script with 13 `python3 -c "import json..."` inline calls for JSON parsing/iteration. Python module replaces YAML parsing, JSON iteration, and Docker resource creation. Shell remains as thin CLI arg parser.
RATIONALE: 13 inline python3 calls = Tier 1 trigger under language policy (§AGENTS.md — any inline python3 in modified script must be extracted). The current JSON-iteration pattern (`while read; python3 -c json.load... done`) is fragile and ungreppable. Moving to Python eliminates: (a) YAML → JSON → shell iteration round-trips, (b) duplicated json.load(sys.stdin) extraction per field, (c) count-then-dispatch inconsistency.
ACCEPTANCE_CRITERIA:
  - `core/internal/provisioner.py` — typed Python module with PlatformEnv dataclass, per-scope provision functions
  - `core/internal/provision-environment.sh` — reduced to <50 LOC thin wrapper (arg parsing + audit_step + dispatch)
  - ZERO inline `python3 -c` or `python3 <<PYEOF` in provision-environment.sh
  - `make provision SCOPE=all` — identical behavior (networks created, volumes dirs, env printed)
  - `make provision SCOPE=networks --dry-run` — prints actions without executing
  - `make provision SCOPE=volumes --dry-run` — prints actions without executing
  - `make provision SCOPE=env` — exports to GITHUB_ENV (CI) or stderr (local)
  - `make up` (modules.mk) — calls provision --scope networks --scope volumes → no regression
  - `tests/unit/test_provisioner.py` — unit tests for parsing, planning, dry-run, idempotency
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration (Strangler-Fig discipline)
IMPACTS:
  - core/internal/provisioner.py (NEW — ~250 LOC)
  - core/internal/provision-environment.sh (REDUCE: 442 → ~45 LOC)
  - tests/unit/test_provisioner.py (NEW — ~180 LOC)
  - makefiles/helpers.mk (UNCHANGED — wrapper interface compatible)
  - makefiles/modules.mk (UNCHANGED — wrapper interface compatible)
REQUIRES: None (can run parallel to 070-074)

---

## §1. Inline Python3 Inventory

All 13 inline `python3 -c` blocks in `core/internal/provision-environment.sh` (current HEAD):

| # | Line | Function | Pattern | Purpose |
|---|------|----------|---------|---------|
| 1 | 77-82 | `_load_platform_env_yaml` | `yq -o=json \| python3 -c "import json,sys; data=json.load(sys.stdin); section=data.get('${section}',[]); print(json.dumps(section))"` | Fallback YAML→JSON extraction when yaml_read.sh unavailable |
| 2 | 127 | `_provision_networks` | `python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))"` | Extract `name` field from JSON object in loop |
| 3 | 129 | `_provision_networks` | `python3 -c "import json,sys; print(json.load(sys.stdin).get('driver','bridge'))"` | Extract `driver` field from JSON object in loop |
| 4 | 146-151 | `_provision_networks` | `python3 -c "import json,sys; nets=json.load(sys.stdin); for n in nets: print(json.dumps(n))"` | Iterate JSON list → print each item as JSON line |
| 5 | 175 | `_provision_volumes` | `python3 -c "import json,sys; print(json.load(sys.stdin).get('path',''))"` | Extract `path` field from JSON object in loop |
| 6 | 196-201 | `_provision_volumes` | Same as #4 for volumes list | Iterate JSON list → print each item as JSON line |
| 7 | 223-228 | `_provision_env` (dry-run) | `python3 -c "import json,sys; envs=json.load(sys.stdin); for k,v in envs.items(): print(f'DRY-RUN: Would export {k}={v}')"` | Dry-run env var display |
| 8 | 229 | `_provision_env` (dry-run count) | `python3 -c "import json,sys; print(len(json.load(sys.stdin)))"` | Count env vars |
| 9 | 237-242 | `_provision_env` (GITHUB_ENV) | `python3 -c "import json,sys; envs=json.load(sys.stdin); for k,v in envs.items(): print(f'{k}={v}')"` | Export KEY=VALUE to `$GITHUB_ENV` |
| 10 | 243 | `_provision_env` (count) | Same as #8 | Count exported env vars |
| 11 | 248-253 | `_provision_env` (local mode) | `python3 -c "import json,sys; envs=json.load(sys.stdin); for k,v in envs.items(): print(f'  {k}={v}')"` | Print env vars to stderr |
| 12 | 269 | `_provision_profiles` | Same as #8 | Count profiles |
| 13 | 415 | `main` (count) | Same as #8 | Count env_defaults |

**Root cause:** All 13 calls use `json.load(sys.stdin)` — the shell reads YAML via `yaml_get_field` (which returns JSON), then iterates/extracts fields via inline python3. The "JSON → shell → python3-parse-JSON → shell-iterate" round-trip is the core inefficiency.

**Migration strategy:** Python module reads `platform-env.yaml` directly via PyYAML. No JSON round-trip. No shell iteration loops. Per-scope functions receive typed dataclass, call subprocess for docker/mkdir, return typed result.

---

## §2. Python Module Structure

### 2.1 File: `core/internal/provisioner.py`

```
# GREP_SUMMARY: provisioner, platform-env, docker-network, volume-dir, ci-env, idempotent, dry-run
# STRUCTURE: ▶ cli:args→dispatch → ◇ load_platform_env→PlatformEnv → ⊕ provision_networks(subprocess docker) → ⊕ provision_volumes(mkdir -p) → ⊕ provision_env(GITHUB_ENV|stderr) → ⊕ provision_profiles → ⎋ exit 0|1|2
```

#### 2.1.1 Dataclasses

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class NetworkConfig:
    """Single Docker network definition from platform-env.yaml."""
    name: str
    driver: str = "bridge"
    internal: bool = False


@dataclass
class VolumeConfig:
    """Single volume directory definition from platform-env.yaml."""
    path: str


@dataclass
class PlatformEnv:
    """Parsed platform-env.yaml structure."""
    networks: list[NetworkConfig]
    volumes: list[VolumeConfig]
    env_defaults: dict[str, str]
    profiles: list[str]


@dataclass
class ProvisionResult:
    """Result of a single scope provision operation."""
    scope: str
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
```

#### 2.1.2 Functions — Signatures

```python
def load_platform_env(yaml_path: Path) -> PlatformEnv:
    """
    Parse platform-env.yaml into typed PlatformEnv.
    
    Reads YAML via PyYAML. Extracts 4 sections: networks, volumes, 
    env_defaults, profiles. Handles missing sections gracefully (empty lists/dicts).
    
    Raises:
        FileNotFoundError: yaml_path does not exist
        yaml.YAMLError: malformed YAML
    """


def provision_networks(
    platform_env: PlatformEnv,
    dry_run: bool = False,
) -> ProvisionResult:
    """
    Create Docker networks from platform-env.networks.
    
    IDEMPOTENT: docker network inspect → exists → skip, else docker network create.
    Reports created/skipped counts. Uses subprocess.run for docker commands.
    
    Returns ProvisionResult with scope="networks", created/skipped counts.
    If dry_run=True, prints planned actions without executing.
    """


def provision_volumes(
    platform_env: PlatformEnv,
    dry_run: bool = False,
) -> ProvisionResult:
    """
    Create volume directories from platform-env.volumes.
    
    IDEMPOTENT: os.path.isdir → exists → skip, else mkdir -p.
    On permission error: log warning, add to skipped count (non-fatal).
    
    Returns ProvisionResult with scope="volumes", created/skipped counts.
    """


def provision_env(
    platform_env: PlatformEnv,
    dry_run: bool = False,
    github_env: Optional[str] = None,
) -> ProvisionResult:
    """
    Export CI environment variables.
    
    - If github_env is set (GITHUB_ENV file path): write KEY=VALUE lines to file
    - If github_env is None and not dry_run: print to stderr (local mode)
    - If dry_run: print "DRY-RUN: Would export KEY=VALUE" to stdout
    
    Returns ProvisionResult with scope="env", count of exported vars.
    """


def provision_profiles(
    platform_env: PlatformEnv,
) -> ProvisionResult:
    """
    Report available profiles count.
    Logs profile names at IMP:8.
    
    Returns ProvisionResult with scope="profiles", count of profiles.
    """
```

#### 2.1.3 CLI Entry Point

```python
def main() -> int:
    """
    CLI entry point: python3 provisioner.py --scope <scope> --platform-env <path> [--dry-run]
    
    Args:
        --scope: networks|volumes|env|profiles (one per invocation)
        --platform-env: path to platform-env.yaml
        --dry-run: print actions without executing (optional flag)
    
    Exit codes:
        0 — success (all resources created or already exist)
        1 — parse error (YAML invalid, file not found, unknown scope)
        2 — docker unavailable (for --scope networks)
    
    Uses argparse. Shell wrapper iterates scopes; Python handles single scope.
    """
```

**Single-scope-per-invocation rationale:** Shell wrapper iterates scopes and wraps each call in `audit_step` (from `audit_logging.sh`). This preserves the existing audit trail granularity without reimplementing audit_logging in Python. The alternative (multi-scope in Python with internal audit) would require porting `audit_logging.sh` → Python, which is out of scope.

### 2.2 Shell Wrapper: `core/internal/provision-environment.sh` (TARGET: ~45 LOC)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: provision-environment thin-wrapper provisioner.py scope dispatch
# STRUCTURE: parse_args(--scope,--platform-env,--dry-run) → for scope ∈ scopes: audit_step "provision:$scope" python3 provisioner.py → ⎋ exit
set -euo pipefail

__PROVISION_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__PROVISION_PLATFORM_ROOT="$(cd "${__PROVISION_SCRIPT_DIR}/../.." && pwd)"

source "${__PROVISION_SCRIPT_DIR}/../lib/audit_logging.sh"

__PROVISION_DEFAULT_PLATFORM_ENV="${__PROVISION_PLATFORM_ROOT}/platform-env.yaml"

# ── Arg parsing ──────────────────────────────────────────────────────
SCOPE="all"
PLATFORM_ENV="$__PROVISION_DEFAULT_PLATFORM_ENV"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scope)     SCOPE="${2:-all}"; shift 2 ;;
        --platform-env) PLATFORM_ENV="$2"; shift 2 ;;
        --dry-run)   DRY_RUN="true"; shift ;;
        --help|-h)   echo "Usage: $0 --scope <networks|volumes|env|profiles|all> [--platform-env <path>] [--dry-run]"; exit 0 ;;
        *)           echo "ERROR: Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# ── Expand 'all' → concrete scopes ───────────────────────────────────
SCOPES=()
case "$SCOPE" in
    all) SCOPES=(networks volumes env profiles) ;;
    networks|volumes|env|profiles) SCOPES=("$SCOPE") ;;
    *) echo "ERROR: Unknown scope: $SCOPE" >&2; exit 1 ;;
esac

# ── Dispatch ──────────────────────────────────────────────────────────
DRY_RUN_ARG=""
[[ "$DRY_RUN" == "true" ]] && DRY_RUN_ARG="--dry-run"

for s in "${SCOPES[@]}"; do
    audit_step "provision:${s}" \
        python3 "${__PROVISION_SCRIPT_DIR}/provisioner.py" \
            --scope "$s" \
            --platform-env "$PLATFORM_ENV" \
            $DRY_RUN_ARG
done

echo "[IMP:9][provision] Provision complete (scope=$SCOPE)"
```

**Shell wrapper preserves:**
- `audit_step` wrapping from `audit_logging.sh` (per-scope granularity)
- `--scope all` expansion to concrete scopes
- `--dry-run` flag propagation
- `--platform-env` path resolution
- Exit code propagation (set -euo pipefail)

**Shell wrapper removes:**
- All `_load_platform_env_yaml` logic (yq/grep fallback chains)
- All `_provision_networks`, `_provision_volumes`, `_provision_env`, `_provision_profiles` functions
- All inline `python3 -c` calls
- All `while IFS= read -r` JSON iteration loops
- Docker availability check (Python module checks before creating)
- Parse count info display (Python module logs equivalent IMP messages)

---

## §3. Shell Variable → Python Parameter Mapping

| Shell variable (provision-environment.sh) | Python parameter (provisioner.py CLI) | Type | Notes |
|-------------------------------------------|---------------------------------------|------|-------|
| `$1` (yaml_path in functions) | `--platform-env <path>` | Path | Absolute path to platform-env.yaml |
| `$2` (dry_run="true"/"false") | `--dry-run` flag | bool flag | Presence = true, absence = false |
| `${GITHUB_ENV:-}` | `os.environ.get("GITHUB_ENV")` | str\|None | Python reads from environment directly |
| `$scope` (shell loop var) | `--scope <scope>` | str | One of: networks, volumes, env, profiles |

| Shell function | Python function | Mapping |
|----------------|-----------------|---------|
| `_load_platform_env_yaml "$yaml" "networks"` | `load_platform_env(yaml_path)` → `.networks` | Direct: PyYAML reads entire file once |
| `_provision_networks "$yaml" "$dry_run"` | `provision_networks(platform_env, dry_run)` | Shell passes file → Python parses + creates |
| `_provision_volumes "$yaml" "$dry_run"` | `provision_volumes(platform_env, dry_run)` | Same pattern |
| `_provision_env "$yaml" "$dry_run"` | `provision_env(platform_env, dry_run, github_env)` | Python reads GITHUB_ENV from environ |
| `_provision_profiles "$yaml"` | `provision_profiles(platform_env)` | Direct |
| `audit_step "provision:$scope" _do_provision ...` | Shell wrapper: `audit_step "provision:$s" python3 provisioner.py ...` | audit_step stays in shell |

---

## §4. Data Flow (After Migration)

```
make provision SCOPE=all
  → makefiles/helpers.mk:70-72 → bash provision-environment.sh --scope all --platform-env platform-env.yaml
    → shell: expand "all" → (networks, volumes, env, profiles)
    → for s in (networks, volumes, env, profiles):
        → audit_step "provision:$s" python3 provisioner.py --scope $s --platform-env platform-env.yaml
          → provisioner.py:
            → load_platform_env(Path("platform-env.yaml")) → PlatformEnv
            → provision_networks(platform_env, dry_run=False)
              → for net in platform_env.networks:
                → subprocess.run(["docker", "network", "inspect", net.name], check=False)
                  → exists → skip, log "SKIP: network {name} already exists"
                  → not exists → subprocess.run(["docker", "network", "create", "--driver", net.driver, net.name])
              → return ProvisionResult(scope="networks", created=N, skipped=M)
            → (same for volumes, env, profiles)
          → exit 0|1|2
        → audit_step captures exit code → writes audit log
    → done
```

---

## §5. Test Specifications

### 5.1 File: `tests/unit/test_provisioner.py`

#### Test Data Structure

Sample YAML for tests (in-memory, written to `tmp_path` via fixture):

```yaml
# test_platform_env.yaml (minimal)
networks:
  - name: proxy-net
    driver: bridge
  - name: shared-db-net
    driver: bridge
volumes:
  - path: /var/lib/platform/postgres-data
  - path: /var/lib/platform/prometheus-data
env_defaults:
  POSTGRES_PASSWORD: test-pg-pwd
  POSTGRES_USER: postgres
profiles:
  - backup-cron
  - monitoring
```

#### Test Cases

| # | Test function | Scenario | Module under test | Mock strategy |
|---|--------------|----------|-------------------|---------------|
| T4.1 | `test_load_platform_env_parses_all_sections` | Parse valid platform-env.yaml → verify all 4 sections populated | `load_platform_env()` | Real YAML file in tmp_path |
| T4.2 | `test_load_platform_env_missing_file` | Parse non-existent path → FileNotFoundError | `load_platform_env()` | Path that doesn't exist |
| T4.3 | `test_load_platform_env_malformed_yaml` | Parse invalid YAML → yaml.YAMLError | `load_platform_env()` | Invalid YAML content in tmp_path |
| T4.4 | `test_load_platform_env_missing_sections` | Parse YAML with no networks/volumes → empty lists | `load_platform_env()` | YAML with only profiles section |
| T4.5 | `test_provision_networks_dry_run` | Dry-run mode → no subprocess calls, output printed | `provision_networks()` | Monkeypatch subprocess.run |
| T4.6 | `test_provision_networks_creates_new` | Network does not exist → docker network create called | `provision_networks()` | Monkeypatch: inspect returns error, create succeeds |
| T4.7 | `test_provision_networks_skips_existing` | Network already exists → skip, zero docker create calls | `provision_networks()` | Monkeypatch: inspect returns success |
| T4.8 | `test_provision_networks_idempotent` | Same config twice → same result (created=0 on second run) | `provision_networks()` | Monkeypatch: first run creates, second run all exist |
| T4.9 | `test_provision_volumes_dry_run` | Dry-run mode → no mkdir calls | `provision_volumes()` | Monkeypatch os.makedirs, os.path.isdir |
| T4.10 | `test_provision_volumes_creates_dirs` | Directories do not exist → mkdir -p called | `provision_volumes()` | tmp_path, monkeypatch os.path.isdir → False |
| T4.11 | `test_provision_volumes_skips_existing` | Directories exist → skip | `provision_volumes()` | tmp_path with pre-created dirs |
| T4.12 | `test_provision_volumes_permission_error_nonfatal` | mkdir fails with PermissionError → logged, not fatal | `provision_volumes()` | Monkeypatch os.makedirs → raise PermissionError |
| T4.13 | `test_provision_env_dry_run` | Dry-run mode → vars printed with "DRY-RUN:" prefix | `provision_env()` | Capture stdout |
| T4.14 | `test_provision_env_github_env` | GITHUB_ENV file path set → KEY=VALUE written to file | `provision_env()` | tmp_path as GITHUB_ENV target, verify file contents |
| T4.15 | `test_provision_env_local_mode` | No GITHUB_ENV → vars printed to stderr | `provision_env()` | Capture stderr |
| T4.16 | `test_provision_profiles_count` | Profiles list parsed → correct count returned | `provision_profiles()` | Real YAML |
| T4.17 | `test_cli_unknown_scope` | `--scope invalid` → exit 1 | `main()` | argparse parse |
| T4.18 | `test_cli_dry_run_flag` | `--dry-run` flag → dry_run=True propagated | `main()` | argparse parse |

#### Test Invariants
- All tests use `tmp_path` fixture (no hardcoded paths)
- Docker-dependent tests (T4.6-T4.8) use `monkeypatch` for `subprocess.run` (no Docker daemon required)
- Volume tests (T4.9-T4.12) use `tmp_path` for directories
- Each test verifies `[IMP:9]` log presence via `caplog` fixture
- Tests run in `tests/unit/` directory → no `requires_docker` marker

---

## §6. Make Targets Affected

| Target | File | Line(s) | Change |
|--------|------|---------|--------|
| `make provision` | `makefiles/helpers.mk` | 63-73 | **NO CHANGE** — wrapper interface compatible: `bash provision-environment.sh --scope <s> --platform-env <path>` |
| `make up` | `makefiles/modules.mk` | 26-42 | **NO CHANGE** — calls `bash provision-environment.sh --scope networks --scope volumes` |
| `make node-update` | `makefiles/bootstrap.mk` | 35-39 | **NO CHANGE** — calls provision via node-lifecycle.sh which calls provision-environment.sh |

**No new Make targets.** The existing `make provision` target is preserved unchanged because the shell wrapper interface is backward-compatible.

---

## §7. Docker Resource Creation Idempotency

| Resource | Check | Create | Idempotency guarantee |
|----------|-------|--------|----------------------|
| Docker network | `docker network inspect <name>` (subprocess.run, check=False) | `docker network create --driver <driver> <name>` | Docker itself is idempotent — creating an existing network returns error. Python checks existence first. |
| Volume directory | `os.path.isdir(path)` | `os.makedirs(path, exist_ok=True)` | `exist_ok=True` makes this idempotent by design |
| CI env vars (GITHUB_ENV) | N/A (append mode) | Write `KEY=VALUE\n` lines to file | File append — duplicates may occur if run twice. This matches current shell behavior (no dedup check). |

**Docker availability check:** `provision_networks()` checks `shutil.which("docker")` before any network operations. Returns exit code 2 (Docker unavailable) matching current shell behavior (line 393-398).

**Concurrency note:** Docker network creation under concurrent `make up` calls is protected by Docker daemon's internal locking. No application-level mutex needed.

---

## §8. Implementation Notes

### 8.1 LDD Logging Format

All Python functions use the established LDD log format:
```python
logger.info("[IMP:7][provisioner][networks] Reading networks from %s", yaml_path)
logger.info("[IMP:9][provisioner][networks] Networks provisioned: %d created, %d skipped", created, skipped)
logger.error("[IMP:10][provisioner][networks] FATAL: Docker is not available")
```

### 8.2 Dependencies

- `pyyaml` — already in project dependencies (used by yaml_query.py, discover_modules.py)
- `argparse` — stdlib
- `subprocess` — stdlib
- `pathlib` — stdlib
- `logging` — stdlib

### 8.3 Strangler-Fig Step

This is a Tier 1 extraction: the script has 13 inline python3 calls. Each call's logic moves to `provisioner.py`. The shell wrapper iterates scopes and calls Python per scope, preserving `audit_step` wrapping.

---

## §9. Task Decomposition

### TASK-1: Implement `provisioner.py` dataclasses + YAML parsing
- **Output:** `core/internal/provisioner.py` (~100 LOC)
- **Deliverables:** `PlatformEnv`, `NetworkConfig`, `VolumeConfig`, `ProvisionResult` dataclasses; `load_platform_env()` function
- **Dependencies:** None
- **Complexity:** 3/10
- **Acceptance:** `load_platform_env(Path("platform-env.yaml"))` returns typed PlatformEnv with all 4 sections

### TASK-2: Implement provision functions
- **Output:** `core/internal/provisioner.py` (+150 LOC)
- **Deliverables:** `provision_networks()`, `provision_volumes()`, `provision_env()`, `provision_profiles()` + CLI `main()`
- **Dependencies:** TASK-1
- **Complexity:** 6/10
- **Acceptance:** All 4 provision functions work with real platform-env.yaml in dry-run mode

### TASK-3: Create thin shell wrapper
- **Output:** `core/internal/provision-environment.sh` (rewrite: ~45 LOC)
- **Deliverables:** Arg parsing, scope dispatch, audit_step integration
- **Dependencies:** TASK-1, TASK-2
- **Complexity:** 2/10
- **Acceptance:** `bash provision-environment.sh --scope networks --dry-run` calls python3 provisioner.py correctly

### TASK-4: Write unit tests
- **Output:** `tests/unit/test_provisioner.py` (~180 LOC)
- **Deliverables:** 18 test functions covering all scenarios from §5.1
- **Dependencies:** TASK-1, TASK-2
- **Complexity:** 5/10
- **Acceptance:** `python -m pytest tests/unit/test_provisioner.py -s -v` — all 18 tests green

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
- Tasks: TASK-2, TASK-4 (parallel — TASK-4 can start after TASK-1 completes)
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
coder Read .ai/plans/073-provision-python/01-DevPlan.md, implement Wave 1: TASK-1
```

$END_DEVPLAN
