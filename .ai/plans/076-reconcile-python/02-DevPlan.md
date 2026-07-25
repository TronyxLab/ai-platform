$START_DEVPLAN
# DevPlan 076 (Expanded): reconcile-projects.sh → Python

$ARTIFACT_CONTRACT
PURPOSE: Migrate reconcile-projects.sh (~278 LOC, 6 inline python3 calls) to a Python module. The script performs post-bootstrap stub→deployed recovery: detects stub projects in /opt/projects/, checks GHCR for Docker images, and deploys if found.
DESCRIPTION: core/internal/deploy/reconcile-projects.sh is sourced by converge.sh (--reconcile flag) to recover stub projects. It reads node.yaml#projects via python3+yaml, checks each project directory for GENERATED-STUB markers, queries GHCR for Docker images via `docker manifest inspect`, delivers payload via `ssh_read` (lib/ssh.sh), and deploys via `docker compose up -d` over SSH. All 6 inline python3 calls are for node.yaml JSON parsing/structure traversal.
RATIONALE: 6 inline python3 calls = Tier 1 trigger (Strangler-Fig). The logic (parse node.yaml → diff desired vs actual → generate SSH commands → execute) maps cleanly to Python. The existing `reconciler.py` R3 (reconcile_projects) handles stub CREATION (mkdir + ai-platform.yaml stub + .env.platform) — this is a DIFFERENT function: stub→deployed RECOVERY via GHCR check + SSH delivery + compose deploy. Separate module `reconciler_projects.py` is correct — merging would add SSH deployment concern to the converge-time R3, violating single responsibility.
ACCEPTANCE_CRITERIA:
  - `core/internal/reconciler_projects.py` — Python module with `reconcile_projects()` function
  - `core/internal/deploy/reconcile-projects.sh` — reduced to <30 LOC thin wrapper
  - Zero inline `python3 -c` calls in the shell wrapper
  - Identical behavior: stub detection → GHCR check → payload deliver → compose deploy
  - SSH operations via subprocess (respects lib/ssh.sh conventions)
  - `tests/unit/test_project_reconciler.py` — 8+ test cases
  - `make gate MODE=fast` — green
IMPLEMENTS: Wave 6B — Tier 1 shell → Python migration
IMPACTS:
  - core/internal/reconciler_projects.py (NEW — ~250 LOC)
  - core/internal/deploy/reconcile-projects.sh (REDUCE — 278→~25 LOC)
  - core/internal/bootstrap/converge.sh (UPDATE — source path for shell wrapper)
  - tests/unit/test_project_reconciler.py (NEW — ~200 LOC)
REQUIRES: None (can run parallel to 070-075)

## Source Analysis

### Source file: `core/internal/deploy/reconcile-projects.sh`
- **278 LOC** bash script — sourced (not executed directly) from converge.sh
- **Entry point:** `reconcile_projects(node_name, node_yaml, dry_run)` — bash function
- **6 inline python3 calls:**
  1. Line 74-90: Parse node.yaml#projects → JSON array (yaml.safe_load, structure traversal)
  2. Line 93: Count projects from JSON (`len(json.load(sys.stdin))`)
  3. Line 112: Extract project name from JSON entry
  4. Line 114: Extract project org from JSON entry
  5. Line 116: Extract project domain from JSON entry
  6. Line 249-255: Iterate JSON array, dump each entry as JSON line
- **Business logic flow:**
  1. Validate inputs (node.yaml exists)
  2. Parse node.yaml#projects → list of {name, org, domain}
  3. For each project:
     - Check if directory exists at /opt/projects/{org/}{name}
     - Check if ai-platform.yaml is GENERATED-STUB (read first line)
     - If stub: `docker manifest inspect ghcr.io/{org}/{name}:latest`
     - If image exists:
       - Create tmp dir with real ai-platform.yaml + docker-compose.yml
       - Deliver via `ssh_read` (forced-command: platform-deliver)
       - Deploy via `ssh_exec` (docker compose pull && docker compose up -d)
     - If no image: WARN "awaiting first CI deploy"
  4. Summary: deployed/skipped/warnings/failures count
- **SSH Dependency:** Sources `core/lib/ssh.sh` for `ssh_read` and `ssh_exec`
- **Direct invocation guard:** Script exits with error if executed directly (must be sourced)
- **TRAP annotations:** None in this file

### Source file: `core/internal/bootstrap/converge/reconciler.py` (R3)
- R3 `reconcile_projects()` (lines 526-667): Creates stub files (mkdir + GENERATED-STUB ai-platform.yaml + .env.platform) — LOCAL filesystem operations only
- R3 is a PREREQUISITE for the recovery flow: R3 creates stubs → reconcile-projects.sh deploys them
- **Different concern:** R3 = local converge (stub creation), reconcile-projects.sh = remote deploy (stub→deployed recovery)
- **Integration check:** No merge opportunity — these are orthogonal concerns. R3 is local converge, reconcile-projects.sh is remote deploy. Merging would add SSH/remote-deploy concern to the local converge-time reconciler, violating single responsibility.

### Source file: `core/internal/bootstrap/converge.sh` (lines 113-123)
- converge.sh sources `reconcile-projects.sh` and calls `reconcile_projects()` when `--reconcile` flag is set
- After Python migration: converge.sh will source the thin shell wrapper instead

## Architecture Overview

### Design Decision: Separate module `reconciler_projects.py`, NOT merge into `reconciler.py`

**Decision:** Create standalone `core/internal/reconciler_projects.py`.

**@rationale:**
1. **Single Responsibility:** `reconciler.py` R3 handles LOCAL converge (mkdir + stub creation). `reconcile-projects.sh` handles REMOTE deploy (GHCR check + SSH delivery + compose deploy). Merging would add SSH/remote-deploy concern to the local converge-time reconciler.
2. **Call site isolation:** `reconciler.py` is called via `converge.sh main()` for ALL R-units. `reconcile-projects.sh` is called ONLY with `--reconcile` flag. The optional reconcile step is a separate concern from the mandatory R1-R9 converge.
3. **Locality:** The new module lives in `core/internal/` (same layer as `reconciler.py`), not in `core/internal/deploy/` — Python modules belong at the internal/ layer per platform conventions.

**Also considered:** Merge into reconciler.py as R10 (rejected: R-units are local converge; SSH deploy is different concern). Integrate into deploy-project.sh (rejected: deploy-project.sh deploys ONE project; reconcile deploys ALL stub projects from node.yaml).

### Draft Code Graph

```
┌─────────────────────────────────────────────────────────────────┐
│ core/internal/reconciler_projects.py                            │
│                                                                 │
│ @dataclass ProjectSpec                                          │
│   name: str                                                     │
│   org: str = ""                                                 │
│   domain: str = ""                                              │
│                                                                 │
│ @dataclass ReconcileResult                                      │
│   project: str                                                  │
│   status: str  # "deployed"|"skipped"|"warn"|"failed"          │
│   detail: str = ""                                              │
│                                                                 │
│ @dataclass ReconcileSummary                                     │
│   node: str                                                     │
│   deployed: int = 0                                             │
│   skipped: int = 0                                              │
│   warnings: int = 0                                             │
│   failures: int = 0                                             │
│   +is_success() → bool (failures == 0)                          │
│                                                                 │
│ parse_node_yaml_projects(node_yaml_path: str) → list[ProjectSpec]│
## @purpose  Extract projects from node.yaml#projects.            │
## @io       node_yaml_path → list[ProjectSpec]                    │
## @invariants                                                     │
##   - Supports dict entries: {name, org, domain}                  │
##   - Supports string entries: "project_name" (org="", domain="") │
##   - Returns empty list on parse error or missing section        │
##   - Uses PyYAML (available via system python3-yaml on VPS)      │
##                                                                 │
│ is_stub_project(project_dir: str) → bool                        │
## @purpose  Check if ai-platform.yaml is GENERATED-STUB.          │
## @io       project_dir → bool                                    │
## @invariants                                                     │
##   - Reads first line of ai-platform.yaml                        │
##   - Returns True if first line contains "GENERATED-STUB"        │
##   - Returns False if file missing, empty, or has real config    │
##                                                                 │
│ check_ghcr_image(org: str, project_name: str) → bool            │
## @purpose  Check if Docker image exists in GHCR.                 │
## @io       org, project_name → bool                              │
## @invariants                                                     │
##   - Runs: docker manifest inspect ghcr.io/{org}/{name}:latest   │
##   - Default org: "tronyx-lab" if empty                          │
##   - Returns True if image found, False otherwise                │
##                                                                 │
│ resolve_ssh_host(node_name: str, node_yaml_path: str,            │
│                   node_host_map: str = "") → Optional[str]       │
## @purpose  Resolve SSH host from NODE_HOST_MAP or node.yaml.     │
## @io       node_name, node_yaml_path, node_host_map → host|None  │
## @invariants                                                     │
##   - Check NODE_HOST_MAP JSON first (if provided)                │
##   - Fallback: parse node.yaml → node.host                       │
##   - Returns None if cannot resolve                              │
##                                                                 │
│ deliver_payload(ssh_host: str, project_dir: str, project_spec,   │
│                  node_name: str, ci_key: str,                    │
##                  dry_run: bool) → bool                           │
## @purpose  Build and deliver ai-platform.yaml + compose via SSH. │
## @io       ssh_host, project_dir, spec, node_name, key → bool    │
## @invariants                                                     │
##   - Creates tmp dir with ai-platform.yaml + docker-compose.yml  │
##   - Delivers via: tar czf - | ssh ... platform-deliver          │
##   - Cleans up tmp dir on both success and failure               │
##   - dry_run: skip actual delivery, return True                  │
##                                                                 │
│ deploy_project(ssh_host: str, project_dir: str,                  │
##                dry_run: bool) → bool                             │
## @purpose  Deploy via docker compose pull && up -d over SSH.     │
## @io       ssh_host, project_dir, dry_run → bool                 │
##                                                                 │
│ reconcile_projects(node_name: str, node_yaml_path: str,          │
##                     dry_run: bool = False) → ReconcileSummary    │
## @purpose  Main entry point — reconcile all stub projects.       │
## @io       node_name, node_yaml_path, dry_run → ReconcileSummary │
## @invariants                                                     │
##   - Returns ReconcileSummary with counts                        │
##   - One project failure does NOT abort others                   │
##   - All LDD logs to stderr via logger                           │
##   - Returns exit code via summary.is_success()                  │
##                                                                 │
│ CLI: main() → argparse → reconcile_projects() → sys.exit()      │
└─────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Data Flow

```
▶ reconcile_projects(node_name, node_yaml_path, dry_run)
  │
  ├─ 1. Validate: node.yaml exists → FATAL if missing
  │
  ├─ 2. parse_node_yaml_projects(node_yaml_path)
  │    └─ yaml.safe_load → data["projects"] → [ProjectSpec, ...]
  │    └─ IF empty: log SKIP → return ReconcileSummary(skipped=0)
  │
  ├─ 3. For each ProjectSpec:
  │    │
  │    ├─ Build proj_dir = /opt/projects/{org/}{name}
  │    │
  │    ├─ 3a. IF directory NOT exists: SKIP (skipped++)
  │    │
  │    ├─ 3b. Check ai-platform.yaml:
  │    │    ├─ IF NOT exists: SKIP (skipped++)
  │    │    ├─ IF exists AND NOT stub: SKIP "already deployed" (skipped++)
  │    │    └─ IF exists AND IS stub: CONTINUE to 3c
  │    │
  │    ├─ 3c. check_ghcr_image(org, name)
  │    │    └─ docker manifest inspect ghcr.io/{org}/{name}:latest
  │    │
  │    ├─ IF image NOT found:
  │    │    └─ WARN "awaiting first CI deploy" (warnings++)
  │    │
  │    └─ IF image FOUND:
  │         │
  │         ├─ IF dry_run:
  │         │    └─ log "DRY-RUN: would deploy" (deployed++)
  │         │
  │         └─ ELSE:
  │              │
  │              ├─ 3d. Resolve SSH host:
  │              │    └─ resolve_ssh_host(node_name, node_yaml, NODE_HOST_MAP)
  │              │    └─ IF None → FAIL "Cannot resolve SSH host" (failures++)
  │              │
  │              ├─ 3e. deliver_payload(ssh_host, proj_dir, spec, node_name, ci_key)
  │              │    ├─ Create tmp dir
  │              │    ├─ Write ai-platform.yaml (real, not stub)
  │              │    ├─ Copy docker-compose.yml (or create minimal compose)
  │              │    ├─ tar czf - ai-platform.yaml docker-compose.yml |
  │              │    │   ssh ... platform-deliver {org/}{name}
  │              │    └─ IF fail → FAIL (failures++)
  │              │
  │              ├─ 3f. deploy_project(ssh_host, proj_dir)
  │              │    └─ ssh ... "cd {proj_dir} && docker compose pull && docker compose up -d"
  │              │    └─ IF fail → FAIL (failures++)
  │              │
  │              └─ SUCCESS: deployed++
  │
  └─ 4. Return ReconcileSummary(deployed, skipped, warnings, failures)
```

## Detailed Module Structure

### File: `core/internal/reconciler_projects.py`

```python
#!/usr/bin/env python3
# GREP_SUMMARY: reconciler_projects, stub-detection, ghcr-check, auto-deploy, idempotent, recovery, post-bootstrap, converge
# STRUCTURE: ▶ parse node.yaml#projects → ○ for each: _is_stub? → ◇ ghcr image exists? → ⚡ deliver payload + compose up → ⊕ summary
# region MODULE_CONTRACT
## @purpose  Post-bootstrap recovery: detect stub projects from node.yaml,
##           check GHCR for Docker images, deploy if found. Idempotent.
## @scope    Called from converge.sh --reconcile, bootstrap.sh --auto-reconcile,
##           or node-lifecycle.sh. Not an entrypoint.
##           Migrated from core/internal/deploy/reconcile-projects.sh per DevPlan 076.
## @invariants
##   - Reads node.yaml#projects — does NOT scan filesystem blindly
##   - For each project: is_stub_project() → check_ghcr_image() → deliver_payload() + deploy_project()
##   - Stub without GHCR image → WARN "awaiting first CI deploy"
##   - Already deployed (real ai-platform.yaml) → SKIP
##   - Idempotent: repeat run = no-op for deployed projects
##   - One project failure does NOT abort others
##   - All SSH operations via subprocess.run (respects ci-deploy key convention)
## @rationale Extracted from shell per Strangler-Fig Tier 1 trigger (6 inline python3 calls).
##            Separate module from reconciler.py R3: R3 creates stubs locally,
##            this module deploys stubs remotely via SSH — orthogonal concerns.
## @changes 2026-07-25 | Migrated from shell to Python (DevPlan 076)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("reconcile_projects")

# Platform convention — SSH user for all remote operations (ci-deploy key)
# 🧐 TRAP[DECISION] · 2026-07-25 · — · SSH_USER as module constant · Rejected: env var per call · Reason: drift risk — two places (deliver_payload, deploy_project) used same hardcoded value; centralizing prevents future divergence · Rev: when ci-deploy key name changes → update single constant
SSH_USER = "ci-deploy"

# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════

# region DATACLASS__ProjectSpec
@dataclass
class ProjectSpec:
    """Parsed project entry from node.yaml#projects."""
    name: str
    org: str = ""
    domain: str = ""
# endregion

# region DATACLASS__ReconcileResult
@dataclass
class ReconcileResult:
    """Result of reconciling a single project."""
    project: str
    status: str  # "deployed", "skipped", "warn", "failed"
    detail: str = ""
# endregion

# region DATACLASS__ReconcileSummary
@dataclass
class ReconcileSummary:
    """Aggregate result of reconcile_projects()."""
    node: str
    deployed: int = 0
    skipped: int = 0
    warnings: int = 0
    failures: int = 0
    results: list[ReconcileResult] = field(default_factory=list)
    
    def is_success(self) -> bool:
        """Returns True if no failures occurred."""
        return self.failures == 0
# endregion

# ═══════════════════════════════════════════════════════════════════
# Node.yaml parsing
# ═══════════════════════════════════════════════════════════════════

# region FUNC_parse_node_yaml_projects
def parse_node_yaml_projects(node_yaml_path: str) -> list[ProjectSpec]:
    """Extract project list from node.yaml.
    
    Supports both dict entries (with name/org/domain keys) and string entries.
    Returns empty list on parse error or missing section.
    
    Args:
        node_yaml_path: Absolute path to node.yaml.
    
    Returns:
        List of ProjectSpec. Empty list if no projects or parse error.
    """
    try:
        import yaml
        
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("[IMP:8][parse_node_yaml] Failed to parse %s: %s", node_yaml_path, exc)
        return []
    
    if not data:
        return []
    
    projects_raw = data.get("projects", [])
    if not isinstance(projects_raw, list):
        return []
    
    out: list[ProjectSpec] = []
    for p in projects_raw:
        if isinstance(p, dict):
            out.append(ProjectSpec(
                name=p.get("name", ""),
                org=p.get("org", ""),
                domain=p.get("domain", ""),
            ))
        elif isinstance(p, str):
            out.append(ProjectSpec(name=p))
    
    return out
# endregion

# ═══════════════════════════════════════════════════════════════════
# Stub detection
# ═══════════════════════════════════════════════════════════════════

# region FUNC_is_stub_project
def is_stub_project(project_dir: str) -> bool:
    """Check if ai-platform.yaml in project_dir is a GENERATED-STUB.
    
    Reads the first line of ai-platform.yaml. Returns True if it contains
    "GENERATED-STUB". Returns False if file missing, empty, or has real config.
    
    Args:
        project_dir: Path to project directory containing ai-platform.yaml.
    
    Returns:
        True if the ai-platform.yaml is a stub, False otherwise.
    """
    ai_yaml = Path(project_dir) / "ai-platform.yaml"
    if not ai_yaml.is_file():
        return False
    try:
        first_line = ai_yaml.read_text().splitlines()[0] if ai_yaml.stat().st_size > 0 else ""
        return "GENERATED-STUB" in first_line
    except (OSError, IndexError):
        return False
# endregion

# ═══════════════════════════════════════════════════════════════════
# GHCR check
# ═══════════════════════════════════════════════════════════════════

# region FUNC_check_ghcr_image
def check_ghcr_image(org: str, project_name: str) -> bool:
    """Check if a Docker image exists in GitHub Container Registry.
    
    Runs `docker manifest inspect ghcr.io/{org}/{name}:latest`.
    Default org is "tronyx-lab" if empty.
    
    Args:
        org: GitHub organization (or username). Defaults to "tronyx-lab" if empty.
        project_name: Project/repository name.
    
    Returns:
        True if image manifest is accessible, False otherwise.
    """
    context = org if org else "tronyx-lab"
    image_ref = f"ghcr.io/{context}/{project_name}:latest"
    
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
# endregion

# ═══════════════════════════════════════════════════════════════════
# SSH host resolution
# ═══════════════════════════════════════════════════════════════════

# region FUNC_resolve_ssh_host
def resolve_ssh_host(
    node_name: str,
    node_yaml_path: str,
    node_host_map: str = "",
) -> Optional[str]:
    """Resolve SSH host for a node.
    
    Checks NODE_HOST_MAP JSON first (if provided), then falls back
    to node.yaml → node.host.
    
    Args:
        node_name: Node name to resolve.
        node_yaml_path: Path to node.yaml (fallback source).
        node_host_map: Optional JSON string of {node_name: host} mapping.
    
    Returns:
        SSH host string or None if cannot resolve.
    """
    # Check NODE_HOST_MAP first
    if node_host_map:
        try:
            import json
            host_map = json.loads(node_host_map)
            host = host_map.get(node_name, "")
            if host:
                logger.info("[IMP:8][resolve_host] Resolved from NODE_HOST_MAP: %s", host)
                return host
        except (json.JSONDecodeError, TypeError):
            logger.warning("[IMP:8][resolve_host] Failed to parse NODE_HOST_MAP JSON")
    
    # Fallback: node.yaml → node.host
    try:
        import yaml
        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        node = data.get("node", {}) if data else {}
        host = node.get("host", "")
        if host:
            logger.info("[IMP:8][resolve_host] Resolved from node.yaml: %s", host)
            return host
    except Exception as exc:
        logger.warning("[IMP:8][resolve_host] Failed to parse node.yaml for host: %s", exc)
    
    return None
# endregion

# ═══════════════════════════════════════════════════════════════════
# SSH helpers
# ═══════════════════════════════════════════════════════════════════

# region FUNC__ssh_run
def _ssh_run(
    ssh_host: str,
    ssh_user: str,
    command: str,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Execute a command over SSH.
    
    Uses the ci-deploy key for authentication. Returns CompletedProcess.
    
    Args:
        ssh_host: SSH host (user@host or just host).
        ssh_user: SSH user for key path construction.
        command: Command to execute on remote host.
        timeout: Timeout in seconds (default 600 = 10min for deploys).
    
    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    ci_key = os.environ.get(
        "CI_DEPLOY_KEY",
        os.environ.get("PLATFORM_CI_DEPLOY_KEY_FILE", os.path.expanduser("~/.ssh/ci_deploy_key")),
    )
    
    cmd = [
        "ssh",
        "-i", ci_key,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        f"{ssh_user}@{ssh_host}" if "@" not in ssh_host else ssh_host,
        command,
    ]
    
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][ssh] SSH timeout after %ds: %s", timeout, " ".join(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")
    except FileNotFoundError:
        logger.error("[IMP:10][ssh] ssh binary not found")
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr="ssh: not found")
# endregion

# ═══════════════════════════════════════════════════════════════════
# Deliver payload
# ═══════════════════════════════════════════════════════════════════

# region FUNC_deliver_payload
def deliver_payload(
    ssh_host: str,
    project_dir: str,
    spec: ProjectSpec,
    node_name: str,
    dry_run: bool = False,
) -> bool:
    """Build and deliver ai-platform.yaml + docker-compose.yml via SSH forced-command.
    
    Creates a temporary directory with real ai-platform.yaml (not stub) and
    docker-compose.yml, then delivers via tar+ssh platform-deliver forced-command.
    
    Args:
        ssh_host: SSH host for delivery.
        project_dir: Existing project directory on remote (source of compose file).
        spec: ProjectSpec with name, org, domain.
        node_name: Target node name.
        dry_run: If True, skip actual delivery.
    
    Returns:
        True if delivery succeeded, False otherwise.
    """
    if dry_run:
        logger.info(
            "[IMP:8][deliver][%s] DRY-RUN: would deliver payload to %s",
            spec.name, ssh_host,
        )
        return True
    
    tmp_dir = tempfile.mkdtemp(prefix=f"reconcile-{spec.name}-")
    tmp_path = Path(tmp_dir)
    
    try:
        # Write real ai-platform.yaml
        ai_yaml_content = f"project: {spec.name}\nservice: {spec.name}\ntarget_node: {node_name}\n"
        if spec.domain:
            ai_yaml_content += f"domain: {spec.domain}\n"
        if spec.org:
            ai_yaml_content += f"org: {spec.org}\n"
        (tmp_path / "ai-platform.yaml").write_text(ai_yaml_content)
        
        # Copy docker-compose.yml (or create minimal)
        proj_path = Path(project_dir)
        compose_src = None
        for cf in ("compose.yaml", "compose.yml", "docker-compose.yml"):
            candidate = proj_path / cf
            if candidate.is_file():
                compose_src = candidate
                break
        
        if compose_src:
            shutil.copy2(str(compose_src), str(tmp_path / "docker-compose.yml"))
        else:
            # Create minimal compose file
            org = spec.org if spec.org else "tronyx-lab"
            compose_content = (
                f"services:\n"
                f"  {spec.name}:\n"
                f"    image: ghcr.io/{org}/{spec.name}:latest\n"
                f"    restart: unless-stopped\n"
            )
            (tmp_path / "docker-compose.yml").write_text(compose_content)
        
        # Build deliver verb
        deliver_prefix = f"{spec.org} " if spec.org else ""
        deliver_verb = f"platform-deliver {deliver_prefix}{spec.name}"
        
        # Deliver via SSH: tar czf - ai-platform.yaml docker-compose.yml | ssh ...
        # We use ssh with stdin pipe
        ci_key = os.environ.get(
            "CI_DEPLOY_KEY",
            os.environ.get("PLATFORM_CI_DEPLOY_KEY_FILE", os.path.expanduser("~/.ssh/ci_deploy_key")),
        )
        
        logger.info("[IMP:8][deliver][%s] Delivering payload to %s...", spec.name, ssh_host)
        
        # Create tar and pipe to SSH
        tar_proc = subprocess.Popen(
            ["tar", "czf", "-", "-C", str(tmp_path), "ai-platform.yaml", "docker-compose.yml"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        ssh_cmd = [
            "ssh",
            "-i", ci_key,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            f"{SSH_USER}@{ssh_host}" if "@" not in ssh_host else ssh_host,
            deliver_verb,
        ]
        
        ssh_proc = subprocess.Popen(
            ssh_cmd,
            stdin=tar_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        if tar_proc.stdout:
            tar_proc.stdout.close()
        
        tar_proc.wait(timeout=30)
        ssh_stdout, ssh_stderr = ssh_proc.communicate(timeout=30)
        
        if ssh_proc.returncode != 0:
            logger.error(
                "[IMP:10][deliver][%s] FAIL: Payload delivery failed: %s",
                spec.name, ssh_stderr.strip(),
            )
            return False
        
        logger.info("[IMP:9][deliver][%s] Payload delivered successfully", spec.name)
        return True
        
    except Exception as exc:
        logger.error("[IMP:10][deliver][%s] FAIL: %s", spec.name, exc)
        return False
    finally:
        # Cleanup tmp dir
        shutil.rmtree(tmp_dir, ignore_errors=True)
# endregion

# ═══════════════════════════════════════════════════════════════════
# Deploy project
# ═══════════════════════════════════════════════════════════════════

# region FUNC_deploy_project
def deploy_project(
    ssh_host: str,
    project_dir: str,
    dry_run: bool = False,
) -> bool:
    """Deploy project via docker compose pull && up -d over SSH.
    
    Args:
        ssh_host: SSH host.
        project_dir: Project directory on remote host.
        dry_run: If True, skip actual deployment.
    
    Returns:
        True if deployment succeeded, False otherwise.
    """
    if dry_run:
        logger.info("[IMP:8][deploy] DRY-RUN: would deploy in %s", project_dir)
        return True
    
    logger.info("[IMP:9][deploy] Deploying via docker compose in %s...", project_dir)
    
    result = _ssh_run(
        ssh_host,
        SSH_USER,
        f"cd {project_dir} && docker compose pull && docker compose up -d",
        timeout=600,
    )
    
    if result.returncode == 0:
        logger.info("[IMP:9][deploy] Deploy succeeded in %s", project_dir)
        return True
    
    logger.error(
        "[IMP:10][deploy] FAIL: docker compose up failed in %s: %s",
        project_dir, result.stderr.strip(),
    )
    return False
# endregion

# ═══════════════════════════════════════════════════════════════════
# Main reconciler function
# ═══════════════════════════════════════════════════════════════════

# region FUNC_reconcile_projects
def reconcile_projects(
    node_name: str,
    node_yaml_path: str,
    dry_run: bool = False,
    node_host_map: str = "",
) -> ReconcileSummary:
    """Reconcile all stub projects from node.yaml — deploy if GHCR image exists.
    
    Main entry point. For each project in node.yaml#projects:
    1. Check if directory exists and ai-platform.yaml is GENERATED-STUB
    2. Check GHCR for Docker image
    3. If image found: deliver payload + docker compose up -d via SSH
    4. If no image: WARN "awaiting first CI deploy"
    
    Args:
        node_name: Node name for SSH host resolution and ai-platform.yaml.
        node_yaml_path: Path to node.yaml.
        dry_run: If True, print planned actions without executing.
        node_host_map: Optional JSON string {node_name: ssh_host} mapping.
    
    Returns:
        ReconcileSummary with counts and per-project results.
    """
    summary = ReconcileSummary(node=node_name)
    
    logger.info("[IMP:8][reconcile][main] START: Reconcile stub projects for node=%s", node_name)
    logger.info("[IMP:8][reconcile][main] node.yaml: %s", node_yaml_path)
    logger.info("[IMP:8][reconcile][main] dry_run: %s", dry_run)
    
    # Validate node.yaml exists
    if not Path(node_yaml_path).is_file():
        logger.error(
            "[IMP:10][reconcile][main] FATAL: node.yaml not found at %s",
            node_yaml_path,
        )
        summary.failures += 1
        return summary
    
    # Parse projects
    projects = parse_node_yaml_projects(node_yaml_path)
    if not projects:
        logger.info("[IMP:9][reconcile][main] SKIP: No projects defined in node.yaml")
        return summary
    
    logger.info("[IMP:8][reconcile][main] Found %d project(s) in node.yaml", len(projects))
    
    # Process each project
    for spec in projects:
        if not spec.name:
            continue
        
        logger.info("[IMP:7][reconcile][%s] Processing...", spec.name)
        
        # Build project directory path
        org_prefix = f"{spec.org}/" if spec.org else ""
        proj_dir = f"/opt/projects/{org_prefix}{spec.name}"
        
        # Check directory exists
        if not Path(proj_dir).is_dir():
            logger.info(
                "[IMP:7][reconcile][%s] SKIP: Project directory not found at %s",
                spec.name, proj_dir,
            )
            summary.skipped += 1
            summary.results.append(ReconcileResult(spec.name, "skipped", "Directory not found"))
            continue
        
        # Check if stub
        if not is_stub_project(proj_dir):
            # Could be real config or missing ai-platform.yaml
            ai_yaml = Path(proj_dir) / "ai-platform.yaml"
            if ai_yaml.is_file():
                logger.info(
                    "[IMP:7][reconcile][%s] SKIP: real ai-platform.yaml (already deployed)",
                    spec.name,
                )
            else:
                logger.info(
                    "[IMP:7][reconcile][%s] SKIP: no ai-platform.yaml",
                    spec.name,
                )
            summary.skipped += 1
            summary.results.append(ReconcileResult(spec.name, "skipped", "Not a stub"))
            continue
        
        logger.info("[IMP:9][reconcile][%s] Stub detected — checking GHCR for Docker image...", spec.name)
        
        # Check GHCR
        if not check_ghcr_image(spec.org, spec.name):
            logger.info(
                "[IMP:8][reconcile][%s] WARN: No image in GHCR — awaiting first CI deploy",
                spec.name,
            )
            summary.warnings += 1
            summary.results.append(ReconcileResult(spec.name, "warn", "No GHCR image"))
            continue
        
        logger.info("[IMP:9][reconcile][%s] Image found — deploying", spec.name)
        
        if dry_run:
            logger.info(
                "[IMP:8][reconcile][%s] DRY-RUN: would deliver payload and deploy",
                spec.name,
            )
            summary.deployed += 1
            summary.results.append(ReconcileResult(spec.name, "deployed", "Would deploy (dry-run)"))
            continue
        
        # Resolve SSH host
        ssh_host = resolve_ssh_host(node_name, node_yaml_path, node_host_map)
        if not ssh_host:
            logger.error(
                "[IMP:10][reconcile][%s] FAIL: Cannot resolve SSH host for node=%s",
                spec.name, node_name,
            )
            summary.failures += 1
            summary.results.append(ReconcileResult(spec.name, "failed", "Cannot resolve SSH host"))
            continue
        
        # Deliver payload
        if not deliver_payload(ssh_host, proj_dir, spec, node_name, dry_run=False):
            summary.failures += 1
            summary.results.append(ReconcileResult(spec.name, "failed", "Payload delivery failed"))
            continue
        
        # Deploy via docker compose
        if not deploy_project(ssh_host, proj_dir, dry_run=False):
            summary.failures += 1
            summary.results.append(ReconcileResult(spec.name, "failed", "Deploy failed"))
            continue
        
        logger.info("[IMP:9][reconcile][%s] DONE: stub → deployed", spec.name)
        summary.deployed += 1
        summary.results.append(ReconcileResult(spec.name, "deployed", "Successfully deployed"))
    
    # Summary
    logger.info("[IMP:9][reconcile][main] ==============================")
    logger.info("[IMP:9][reconcile][main] Reconcile complete for node=%s", node_name)
    logger.info("[IMP:9][reconcile][main]   deployed: %d", summary.deployed)
    logger.info("[IMP:9][reconcile][main]   skipped:  %d", summary.skipped)
    logger.info("[IMP:9][reconcile][main]   warnings: %d", summary.warnings)
    logger.info("[IMP:9][reconcile][main]   failures: %d", summary.failures)
    logger.info("[IMP:9][reconcile][main] ==============================")
    
    return summary
# endregion

# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════

# region FUNC_main
def main() -> None:
    """CLI entry point for reconciler_projects.py.
    
    Usage:
        python3 reconciler_projects.py --node <name> --node-yaml <path> [--dry-run] [--node-host-map '<json>']
    
    Exit codes:
        0 — all projects reconciled or skipped
        1 — one or more deployments failed
    """
    parser = argparse.ArgumentParser(
        description="Post-bootstrap project reconciler — deploy stub projects from GHCR.",
    )
    parser.add_argument(
        "--node", required=True, type=str,
        help="Node name (for SSH resolution and ai-platform.yaml)",
    )
    parser.add_argument(
        "--node-yaml", required=True, type=str,
        help="Path to node.yaml",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print planned actions without executing",
    )
    parser.add_argument(
        "--node-host-map", default="", type=str,
        help="JSON string of {node_name: ssh_host} mapping",
    )
    
    args = parser.parse_args()
    
    summary = reconcile_projects(
        node_name=args.node,
        node_yaml_path=args.node_yaml,
        dry_run=args.dry_run,
        node_host_map=args.node_host_map,
    )
    
    if summary.is_success():
        sys.exit(0)
    else:
        sys.exit(1)
# endregion

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    main()
```

## Shell Wrapper (exact code)

### File: `core/internal/deploy/reconcile-projects.sh`

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: reconcile-projects launcher python3 reconciler_projects.py converge bootstrap
# STRUCTURE: ▶ source guard → ▶ parse args → ▶ python3 reconciler_projects.py "$@" → ⊕ rc → ⎋ return
# region MODULE_CONTRACT
## @purpose  Thin shell wrapper for reconciler_projects.py — preserves backward compatibility
##           for sourcing from converge.sh. All business logic in Python.
## @scope    <30 LOC — delegates to Python module. Sourced from converge.sh.
## @invariants
##   - Zero business logic — pure delegation
##   - Zero inline python3 -c or heredoc calls
##   - Defines reconcile_projects() bash function (backward compat with converge.sh source pattern)
##   - Direct invocation guard preserved
## @rationale Shell wrapper exists because converge.sh sources this file and calls
##            reconcile_projects() as a bash function. Python module handles all logic.
## @changes 2026-07-25 | Migrated to Python (DevPlan 076) — shell reduced to <30 LOC
# endregion MODULE_CONTRACT

set -euo pipefail

# region FUNC_reconcile_projects
reconcile_projects() {
    # 💼 TRAP[BUSINESS] · 2026-07-25 · HI · exec NOT used — sourced from converge.sh
    # · Root: exec replaces the parent process — would kill converge.sh after reconcile
    # · Fix: python3 + local rc=$?; return $rc — preserves converge.sh execution
    # · Prevention: Never use exec in a sourced function
    local node_name="$1"
    local node_yaml="$2"
    local dry_run="${3:-false}"
    local node_host_map="${4:-${NODE_HOST_MAP:-}}"
    local core_dir
    core_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

    python3 "${core_dir}/internal/reconciler_projects.py" \
        --node "${node_name}" \
        --node-yaml "${node_yaml}" \
        --node-host-map "${node_host_map}" \
        $([[ "${dry_run}" == "true" ]] && echo "--dry-run")
    local rc=$?
    return $rc
}
# endregion FUNC_reconcile_projects

# Direct invocation guard
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "[IMP:10][reconcile] FATAL: This script is NOT an entrypoint — source it from converge.sh or node-lifecycle.sh" >&2
    echo "[IMP:10][reconcile] Usage: source reconcile-projects.sh && reconcile_projects <node> <node_yaml> [dry_run]" >&2
    exit 1
fi
```

## converge.sh Integration Update

### File: `core/internal/bootstrap/converge.sh` (lines 113-123 — update only)

The converge.sh already sources `reconcile-projects.sh` from `CORE_DIR/internal/deploy/`. The shell wrapper preserves the `reconcile_projects()` function signature, so converge.sh requires **zero changes** — the same `source` + `reconcile_projects` call works unchanged. The Python module is called from within the shell function.

**No changes needed to converge.sh.**

## Configuration DRY

| Config Value | Shell (old) | Python (new) | Source |
|---|---|---|---|
| CI_DEPLOY_KEY | `$CI_DEPLOY_KEY:-$PLATFORM_CI_DEPLOY_KEY_FILE:-~/.ssh/ci_deploy_key` | `os.environ.get("CI_DEPLOY_KEY", os.environ.get("PLATFORM_CI_DEPLOY_KEY_FILE", "~/.ssh/ci_deploy_key"))` | Environment |
| GHCR org fallback | `tronyx-lab` | `"tronyx-lab"` | Hardcoded (project-agnostic) |
| PROJECTS_BASE | `/opt/projects` | `/opt/projects` | Hardcoded (platform convention) |
| NODE_HOST_MAP | `$NODE_HOST_MAP` (global env) | `--node-host-map` CLI arg (shell forwards env) | Environment → CLI; shell wrapper forwards per VerificationReport #2 |
| SSH user | `ci-deploy` | `SSH_USER = "ci-deploy"` (module constant) | Hardcoded (platform convention); centralized per VerificationReport #4 |

## TRAP Annotations

No TRAP annotations in the source file `reconcile-projects.sh`. The TRAP[DECISION] in `converge.sh` (line 11) about "shell kept for flock + reconcile-projects.sh orchestration" remains valid — the shell is still the orchestration layer for the `--reconcile` flag; only the business logic moved to Python.

## Test Specification

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_project_reconciler.py` | `test_parse_node_yaml_dict_entries` | node.yaml with dict projects → list of ProjectSpec | `reconciler_projects.parse_node_yaml_projects` |
| `tests/unit/test_project_reconciler.py` | `test_parse_node_yaml_string_entries` | node.yaml with string projects → list of ProjectSpec (org="", domain="") | `reconciler_projects.parse_node_yaml_projects` |
| `tests/unit/test_project_reconciler.py` | `test_parse_node_yaml_empty` | projects: [] → empty list | `reconciler_projects.parse_node_yaml_projects` |
| `tests/unit/test_project_reconciler.py` | `test_parse_node_yaml_missing_section` | No projects key → empty list | `reconciler_projects.parse_node_yaml_projects` |
| `tests/unit/test_project_reconciler.py` | `test_is_stub_true` | ai-platform.yaml first line contains GENERATED-STUB → True | `reconciler_projects.is_stub_project` |
| `tests/unit/test_project_reconciler.py` | `test_is_stub_false_real_config` | ai-platform.yaml has real config (no GENERATED-STUB) → False | `reconciler_projects.is_stub_project` |
| `tests/unit/test_project_reconciler.py` | `test_is_stub_false_missing_file` | ai-platform.yaml does not exist → False | `reconciler_projects.is_stub_project` |
| `tests/unit/test_project_reconciler.py` | `test_check_ghcr_image_found` | Mock docker manifest inspect success → True | `reconciler_projects.check_ghcr_image` |
| `tests/unit/test_project_reconciler.py` | `test_check_ghcr_image_not_found` | Mock docker manifest inspect failure → False | `reconciler_projects.check_ghcr_image` |
| `tests/unit/test_project_reconciler.py` | `test_resolve_ssh_host_from_map` | NODE_HOST_MAP JSON provided → correct host returned | `reconciler_projects.resolve_ssh_host` |
| `tests/unit/test_project_reconciler.py` | `test_resolve_ssh_host_from_node_yaml` | Fallback to node.yaml → node.host | `reconciler_projects.resolve_ssh_host` |
| `tests/unit/test_project_reconciler.py` | `test_resolve_ssh_host_not_found` | Neither map nor node.yaml has host → None | `reconciler_projects.resolve_ssh_host` |
| `tests/unit/test_project_reconciler.py` | `test_reconcile_no_projects` | Empty projects list → summary with all zeros | `reconciler_projects.reconcile_projects` |
| `tests/unit/test_project_reconciler.py` | `test_reconcile_stub_without_ghcr` | Stub project, no GHCR image → warn status | `reconciler_projects.reconcile_projects` |
| `tests/unit/test_project_reconciler.py` | `test_summary_is_success` | failures=0 → True, failures=1 → False | `reconciler_projects.ReconcileSummary` |

**Note:** Tests for `deliver_payload` and `deploy_project` (SSH operations) require either mocking `subprocess` or are covered by integration tests on a test VPS. Unit tests cover parsing logic, stub detection, GHCR check, host resolution, and summary aggregation — the pure-business-logic layers.

## $TASKS

### T1: Create reconciler_projects.py
- **File:** `core/internal/reconciler_projects.py` (NEW)
- **Content:** All dataclasses and functions as specified: ProjectSpec, ReconcileResult, ReconcileSummary, parse_node_yaml_projects, is_stub_project, check_ghcr_image, resolve_ssh_host, deliver_payload, deploy_project, reconcile_projects, main()
- **Dependencies:** None
- **Complexity:** 7/10
- **Acceptance:** File exists, `python3 -c "from core.internal.reconciler_projects import reconcile_projects"` succeeds (or from repo root with PYTHONPATH)

### T2: Reduce reconcile-projects.sh to <30 LOC
- **File:** `core/internal/deploy/reconcile-projects.sh` (OVERWRITE)
- **Content:** Exact shell wrapper from spec
- **Dependencies:** T1
- **Complexity:** 1/10
- **Acceptance:** `wc -l < 30`, `grep "python3 -c"` returns nothing, `grep "PYEOF"` returns nothing

### T3: Verify converge.sh integration
- **File:** `core/internal/bootstrap/converge.sh` (READ-ONLY check)
- **Action:** Verify converge.sh sources the shell wrapper and calls `reconcile_projects()` — should work unchanged
- **Dependencies:** T2
- **Complexity:** 1/10
- **Acceptance:** converge.sh `source` path points to the reduced shell wrapper (same path), function signature matches

### T4: Write unit tests
- **File:** `tests/unit/test_project_reconciler.py` (NEW)
- **Content:** All test cases from $TEST_SPEC table (15 tests)
- **Dependencies:** T1
- **Complexity:** 5/10
- **Acceptance:** `python -m pytest tests/unit/test_project_reconciler.py -v` — all green

### T5: Run gate
- **Command:** `make fix-gate && make gate MODE=fast`
- **Dependencies:** T1-T4
- **Complexity:** 2/10
- **Acceptance:** Gate green, zero regressions

## $PARALLEL_GROUPS

### Wave 1 (independent)
- Tasks: T1
- T1 must complete first (T2, T4 depend on it)

### Wave 2 (after T1, parallel)
- Tasks: T2, T3, T4
- No file conflicts: T2 (shell wrapper), T3 (read-only verify), T4 (tests)
- Command: `Read DevPlan.md, implement Wave 2: T2, T3, T4`

### Wave 3
- Tasks: T5
- Gate runs after all code changes

## Next Steps

### Wave 1
```
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/076-reconcile-python/02-DevPlan.md, implement Wave 1: T1 (create reconciler_projects.py)
```

### Wave 2
```
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/076-reconcile-python/02-DevPlan.md, implement Wave 2: T2, T3, T4
```

$END_DEVPLAN
