#!/usr/bin/env python3
# GREP_SUMMARY: reconciler_projects, stub-detection, ghcr-check, auto-deploy, idempotent, recovery, post-bootstrap, converge
# STRUCTURE: ▶ parse node.yaml#projects → ○ for each: _is_stub? → ◇ ghcr image exists? → ⚡ deploy_via_orchestrator → ⊕ summary
# region MODULE_CONTRACT
## @purpose  Post-bootstrap recovery: detect stub projects from node.yaml,
##           check GHCR for Docker images, deploy if found. Idempotent.
## @scope    Called from converge.sh --reconcile, bootstrap.sh --auto-reconcile,
##           or node-lifecycle.sh. Not an entrypoint.
## @invariants
##   - Reads node.yaml#projects — does NOT scan filesystem blindly
##   - For each project: is_stub_project() → check_ghcr_image() → deploy_via_orchestrator()
##   - Stub without GHCR image → WARN "awaiting first CI deploy"
##   - Already deployed (real ai-platform.yaml) → SKIP
##   - Idempotent: repeat run = no-op for deployed projects
##   - One project failure does NOT abort others
##   - All deploy operations via DeployOrchestrator (unified path, DevPlan 089)
## @rationale Отдельный модуль от reconciler.py R3: R3 создаёт stubs локально,
##            этот модуль деплоит stubs удалённо через SSH — ортогональные задачи.
# ⚠️ TRAP[DECISION] · 2026-08-15 · — · keep: НЕ консолидируется в converge/reconciler (172 W5.3)
# · Rejected: слияние с reconciler.py / перенос в bootstrap/converge/
# · Reason: keep-решение DevPlan 116 B9 — subprocess-вызов из converge.py:130 (DeployOrchestrator
# ·   через отдельный процесс: проектный деплой — отдельный жизненный цикл с собственным
# ·   аудит-трейлом DEPLOY-*); размещение в core/internal/ (не подкаталог) — осознанно:
# ·   модуль — оркестрационный мост converge→deploy, не доменный пакет.
# · Rev: если converge.py перестанет вызывать его subprocess'ом — пересмотреть placement.
## @changes 2026-07-25 | DevPlan 076 — создан как Python-модуль
## @changes 2026-07-30 | DeployOrchestrator — единственный путь деплоя
## @changes 2026-08-02 | DevPlan 119 A2 — timeout=30 литерал → IMAGE_CHECK_TIMEOUT (60) из shared/timeouts
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, cast

from core.internal.deploy.channels import ForcedCommandChannel
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.shared import docker_ops  # W1: docker manifest inspect примитив (гейт docker_sole_path)
from core.internal.shared.deploy_paths import projects_base
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry
from core.internal.shared.timeouts import IMAGE_CHECK_TIMEOUT

# DeployOrchestrator — единственный путь деплоя: импорт-фейл всплывает громко
# (без transitional-флага, маскирующего переходное состояние).
# Rev-правило: новый deploy-путь с opt-in — через явный выбор channel, не module-level boolean.

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════


# region DATACLASS__ProjectSpec
@dataclass
class ProjectSpec:
    """Parsed project entry from node.yaml#projects — reconcile-DTO view over canonical ProjectEntry.

    DevPlan 116 B6 T4.6: view, НЕ определение канона — name/org/domain берутся из
    shared.ProjectEntry через from_entry().
    """

    name: str
    org: str = ""
    domain: str = ""

    @classmethod
    def from_entry(cls, entry: ProjectEntry, org: str = "") -> "ProjectSpec":
        """Create a ProjectSpec view from a canonical ProjectEntry."""
        if not org and "/" in entry.repo:
            org = entry.repo.split("/")[0]
        return cls(name=entry.name, org=org, domain=entry.domain)


# endregion DATACLASS__ProjectSpec


# region DATACLASS__ReconcileResult
@dataclass
class ReconcileResult:
    """Result of reconciling a single project."""

    project: str
    status: str  # "deployed", "skipped", "warn", "failed"
    detail: str = ""


# endregion DATACLASS__ReconcileResult


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


# endregion DATACLASS__ReconcileSummary

# ═══════════════════════════════════════════════════════════════════
# Node.yaml parsing
# ═══════════════════════════════════════════════════════════════════


# region FUNC_parse_node_yaml_projects
def parse_node_yaml_projects(node_yaml_path: str) -> list[ProjectSpec]:
    """Extract project list from node.yaml via canonical NodeYaml.get_project_entries().

    DevPlan 116 B6 T4.6: manual dict/str parsing → canonical parser. str-form entries
    are rejected (decision D3 — schema requires dict records).
    Returns empty list on parse error or missing section.

    Args:
        node_yaml_path: Absolute path to node.yaml.

    Returns:
        List of ProjectSpec. Empty list if no projects or parse error.
    """
    try:
        node = NodeYaml(node_yaml_path)
        entries = node.get_project_entries()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][parse_node_yaml] Failed to parse %s: %s", node_yaml_path, exc)
        return []

    return [ProjectSpec.from_entry(e) for e in entries]


# endregion FUNC_parse_node_yaml_projects

# ═══════════════════════════════════════════════════════════════════
# Stub detection
# ═══════════════════════════════════════════════════════════════════


# region FUNC_is_stub_project
def is_stub_project(project_dir: str) -> bool:
    """Check if ai-platform.yaml in project_dir is a GENERATED-STUB.

    Тонкий делегирующий wrapper над единой реализацией shared/stub_detection
    (DevPlan 116 B9 T4, U-28) — публичный API модуля сохраняется (тесты
    test_project_reconciler не меняют вызовы). Поведение идентично прежнему
    локальному алгоритму: первая строка содержит "GENERATED-STUB" → True;
    missing/empty/OSError/IndexError → False.

    Args:
        project_dir: Path to project directory containing ai-platform.yaml.

    Returns:
        True if the ai-platform.yaml is a stub, False otherwise.
    """
    from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

    return is_stub_ai_platform_yaml(Path(project_dir) / "ai-platform.yaml")


# endregion FUNC_is_stub_project

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

    # W1 (DevPlan 128): docker manifest inspect — shared/docker_ops (non-fatal)
    return docker_ops.docker_manifest_inspect(image_ref, timeout=IMAGE_CHECK_TIMEOUT)


# endregion FUNC_check_ghcr_image

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
            # W11: json.loads returns Any → cast to {node: host} mapping boundary
            host_map = cast(dict[str, object], json.loads(node_host_map))
            host = cast(str, host_map.get(node_name, ""))
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


# endregion FUNC_resolve_ssh_host

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

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
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
    else:
        return False


# endregion FUNC_deploy_via_orchestrator

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

        # Build project directory path — единый резолвер PROJECTS_BASE
        # (env-цепочка PROJECTS_BASE → /opt/projects).
        # Совпадает с deploy_engine/payload_deliverer/orchestrator_cli (тот же канон).
        org_prefix = f"{spec.org}/" if spec.org else ""
        proj_dir = f"{projects_base()}/{org_prefix}{spec.name}"

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


# endregion FUNC_reconcile_projects

# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════


# region FUNC_main
class _ReconcilerArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    node: ClassVar[str]
    node_yaml: ClassVar[str]
    dry_run: ClassVar[bool]
    node_host_map: ClassVar[str]


def main() -> int:
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

    args = parser.parse_args(namespace=_ReconcilerArgs())

    summary = reconcile_projects(
        node_name=args.node,
        node_yaml_path=args.node_yaml,
        dry_run=args.dry_run,
        node_host_map=args.node_host_map,
    )

    if summary.is_success():
        return 0
    return 1


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
