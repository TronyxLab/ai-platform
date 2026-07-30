#!/usr/bin/env python3
# GREP_SUMMARY: reconciler_projects, stub-detection, ghcr-check, auto-deploy, idempotent, recovery, post-bootstrap, converge
# STRUCTURE: ▶ parse node.yaml#projects → ○ for each: _is_stub? → ◇ ghcr image exists? → ⚡ deploy_via_orchestrator → ⊕ summary
# region MODULE_CONTRACT
## @purpose  Post-bootstrap recovery: detect stub projects from node.yaml,
##           check GHCR for Docker images, deploy if found. Idempotent.
## @scope    Called from converge.sh --reconcile, bootstrap.sh --auto-reconcile,
##           or node-lifecycle.sh. Not an entrypoint.
##           Migrated from core/internal/deploy/reconcile-projects.sh per DevPlan 076.
## @invariants
##   - Reads node.yaml#projects — does NOT scan filesystem blindly
##   - For each project: is_stub_project() → check_ghcr_image() → deploy_via_orchestrator()
##   - Stub without GHCR image → WARN "awaiting first CI deploy"
##   - Already deployed (real ai-platform.yaml) → SKIP
##   - Idempotent: repeat run = no-op for deployed projects
##   - One project failure does NOT abort others
##   - All deploy operations via DeployOrchestrator (unified path, DevPlan 089)
## @rationale Extracted from shell per Strangler-Fig Tier 1 trigger (6 inline python3 calls).
##            Separate module from reconciler.py R3: R3 creates stubs locally,
##            this module deploys stubs remotely via SSH — orthogonal concerns.
## @changes 2026-07-25 | Migrated from shell to Python (DevPlan 076)
## @changes 2026-07-30 | DRIFT-AC14: deliver_payload + deploy_project removed, DeployOrchestrator is sole path
# endregion MODULE_CONTRACT

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.internal.deploy.channels import ForcedCommandChannel
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml

# DevPlan 089 T11.5: DeployOrchestrator as sole deploy path
# ⚠️ TRAP[DEBT] · 2026-07-30 · MED · _ORCHESTRATOR_AVAILABLE flag is transitional
# · Observed: Partial migration complete — deliver_payload + deploy_project removed, DeployOrchestrator is sole path
# · Suspected: _ORCHESTRATOR_AVAILABLE flag should be removed after production validation
# · Impact: dead code — flag always True; removal risk: no one validates production behavior
# · When: during DRIFT-AC14 cleanup (post-089)
_ORCHESTRATOR_AVAILABLE = True

logger = logging.getLogger(__name__)


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
        node = NodeYaml(node_yaml_path)
        projects_raw = node.get_projects()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][parse_node_yaml] Failed to parse %s: %s", node_yaml_path, exc)
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

    # Fallback: node.yaml → node.host via typed getter
    try:
        ny = NodeYaml(node_yaml_path)
        host = ny.get("node.host", default="")
        if host:
            logger.info("[IMP:8][resolve_host] Resolved from node.yaml: %s", host)
            return host
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][resolve_host] Failed to parse node.yaml for host: %s", exc)

    return None


# endregion

# ═══════════════════════════════════════════════════════════════════
# Deploy via Orchestrator
# ═══════════════════════════════════════════════════════════════════


# region FUNC_deploy_via_orchestrator
def deploy_via_orchestrator(
    ssh_host: str,
    project_name: str,
    dry_run: bool = False,
) -> bool:
    """Deploy project via DeployOrchestrator (sole deploy path).

    Uses ForcedCommandChannel over SSH to deliver and deploy the project.

    Args:
        ssh_host: SSH host.
        project_name: Project name.
        dry_run: If True, skip actual deployment.

    Returns:
        True if deployment succeeded, False otherwise.
    """
    if dry_run:
        logger.info(
            "[IMP:8][deploy_via_orchestrator][%s] DRY-RUN: would deploy via orchestrator",
            project_name,
        )
        return True

    logger.info(
        "[IMP:9][deploy_via_orchestrator] Deploying %s via DeployOrchestrator on %s",
        project_name,
        ssh_host,
    )

    try:
        channel = ForcedCommandChannel()
        channel.metadata_defaults = {"host": ssh_host}
        orchestrator = DeployOrchestrator()
        result = orchestrator.deploy(
            project_name=project_name,
            channel=channel,
        )
        if result.is_success():
            logger.info(
                "[IMP:9][deploy_via_orchestrator][%s] Deploy via orchestrator SUCCESS",
                project_name,
            )
            return True
        logger.error(
            "[IMP:10][deploy_via_orchestrator][%s] Deploy failed: %s",
            project_name,
            result.error_info,
        )
        return False
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        ConfigNotFoundError,
        ConfigParseError,
        ConfigValidationError,
    ) as e:
        logger.error(
            "[IMP:10][deploy_via_orchestrator][%s] Orchestrator error: %s",
            project_name,
            e,
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

        # Deploy via DeployOrchestrator (sole deploy path, DevPlan 089)
        if not deploy_via_orchestrator(ssh_host, spec.name, dry_run=False):
            summary.failures += 1
            summary.results.append(ReconcileResult(spec.name, "failed", "Orchestrator deploy failed"))
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
