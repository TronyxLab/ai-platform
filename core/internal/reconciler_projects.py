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

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError

logger = logging.getLogger(__name__)

# Platform convention — SSH user for all remote operations (ci-deploy key)
# ⚠️ TRAP[DECISION] · 2026-07-25 · — · SSH_USER as module constant
# · Rejected: env var per call
# · Reason: drift risk — two places (deliver_payload, deploy_project) used same hardcoded value;
#   centralizing prevents future divergence
# · Rev: when ci-deploy key name changes → update single constant
SSH_USER = "ci-deploy"


# region FUNC__build_deliver_verb
## @purpose — Build platform-deliver verb string via shared platform_deliver module.
##            DevPlan 081 Phase B TASK-081B10: replaces inline string construction.
##            DRIFT-D5 resolved: unified platform-deliver builder.
## @io — ⇥ org: str, project: str → ⎋ str
## @complexity — O(1)
def _build_deliver_verb(org: str, project: str) -> str:
    """Build platform-deliver verb via shared module."""
    from core.internal.shared.platform_deliver import build_deliver_command

    return build_deliver_command(org, project)


# endregion FUNC__build_deliver_verb

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
    except (FileNotFoundError, yaml.YAMLError, OSError, ConfigParseError, ConfigNotFoundError) as exc:
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
            out.append(
                ProjectSpec(
                    name=p.get("name", ""),
                    org=p.get("org", ""),
                    domain=p.get("domain", ""),
                )
            )
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
) -> str | None:
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
    except (FileNotFoundError, yaml.YAMLError, OSError, ConfigParseError, ConfigNotFoundError) as exc:
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
        "-i",
        ci_key,
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
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
            spec.name,
            ssh_host,
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
                f"services:\n  {spec.name}:\n    image: ghcr.io/{org}/{spec.name}:latest\n    restart: unless-stopped\n"
            )
            (tmp_path / "docker-compose.yml").write_text(compose_content)

        # Build deliver verb via shared platform_deliver (DevPlan 081 Phase B TASK-081B10)
        # DRIFT-D5 resolved: unified platform-deliver builder
        deliver_verb = _build_deliver_verb(spec.org or "", spec.name)

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
            "-i",
            ci_key,
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
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
        _ssh_stdout, ssh_stderr = ssh_proc.communicate(timeout=30)

        if ssh_proc.returncode != 0:
            logger.error(
                "[IMP:10][deliver][%s] FAIL: Payload delivery failed: %s",
                spec.name,
                ssh_stderr.strip(),
            )
            return False

        logger.info("[IMP:9][deliver][%s] Payload delivered successfully", spec.name)
        return True

    except (subprocess.CalledProcessError, OSError, FileNotFoundError, ConfigNotFoundError, ConfigParseError) as exc:
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
        project_dir,
        result.stderr.strip(),
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
                spec.name,
                proj_dir,
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
                spec.name,
                node_name,
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
        "--node",
        required=True,
        type=str,
        help="Node name (for SSH resolution and ai-platform.yaml)",
    )
    parser.add_argument(
        "--node-yaml",
        required=True,
        type=str,
        help="Path to node.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print planned actions without executing",
    )
    parser.add_argument(
        "--node-host-map",
        default="",
        type=str,
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
