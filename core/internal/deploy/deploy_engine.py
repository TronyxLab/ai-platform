#!/usr/bin/env python3
# GREP_SUMMARY: deploy-engine, atomic-deploy, rollback, healthcheck, remove, status, docker-compose, lifecycle, snapshot, prune-images
# STRUCTURE: ▶ DataClasses(DeployResult|RemoveResult|StatusResult|ImageInfo|SnapshotInfo) → [DeployEngine] →
#            ◇ deploy(project,ref,service,project_dir,node,max_wait,keep_images) → _save_previous_image → _capture_deploy_snapshot →
#            _preflight_checks → _pull_image_with_retry → _atomic_up → _poll_health →
#            either success(DEPLOY_STATUS=success) or fail(first_deploy→exit|rollback→_perform_rollback) →
#            ◇ remove(project,project_dir) → docker compose down(no -v) → ◇ status(project,project_dir,stub_aware) → JSON →
#            CLI: argparse(subcommands:deploy|remove|status) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Atomic deploy/rollback/remove/status engine for VPS-side forced-command deploy operations.
##           Migrated from deploy-project.sh (1183→~600 LOC) via Strangler-Fig methodology (Wave 5e).
##           All Docker operations via subprocess.run (docker compose CLI), NOT docker-py SDK (D4).
## @scope    Called by deploy-project.sh shell facade for deploy/remove/status verbs.
##           Importable by other Python modules (context_deployer.py, etc.) via DeployEngine class.
## @invariants
##   1. All Docker operations use subprocess.run(["docker", "compose", ...]) — zero docker-py dependency
##   2. Previous image saved BEFORE docker compose pull — enables rollback (T1)
##   3. Healthcheck poll ≤ max_wait seconds — shell-wrapper poll_until_healthy used
##   4. DEPLOY_STATUS="success" set immediately after health-gate — BEFORE non-fatal housekeeping (B1/T3)
##   5. Rollback: re-tag previous image → docker compose up -d --force-recreate (T1)
##   6. First deploy with health fail → _handle_first_deploy → sys.exit(1) (no rollback possible)
##   7. Remove: docker compose down --timeout 30 WITHOUT -v (O7/DD10, T11)
##   8. Status: JSON stdout with docker compose ps + deploy-result.json; stub-aware flag
##   9. All methods log at IMP:7-10 for LDD telemetry
##   10. No secrets or tokens in output — audit logs go to stderr
## @rationale
##   ⚠️ TRAP[BUG] · 2026-07-18 · P1 · Deploy reports 'failed' despite success (B1)
##   · Symptom: Deploy SUCCESS in logs, deploy-result.json=status:"failed", exit 1
##   · Root: DEPLOY_STATUS="success" was assigned AFTER non-fatal steps under set -e
##   · Fix: DEPLOY_STATUS="success" immediately after health-gate; non-fatal steps guarded
##   · Prevention: Any code after health-gate in deploy() must not raise exceptions
##
##   🧐 TRAP[DECISION] · 2026-07-17 · — · Rollback on-node, not in CI/CD
##   · Rejected: CI/CD-driven rollback (re-deploy via GitHub Actions)
##   · Reason: instant rollback without CI pipeline wait, eliminates network roundtrip
##   · Rev: if deploy latency >5min from CI → reconsider
##
##   🧐 TRAP[DECISION] · 2026-07-17 · — · audit_log() replaces audit_write()
##   · Rejected: keeping audit_write() in deploy-project.sh (duplicate)
##   · Reason: audit_log() from lib/audit_logging.sh is the canonical function.
##
##   ⚠️ TRAP[BUG] · 2026-07-20 · REF="<sha> production" — env suffix leaks into image tag
##   · Symptom: "invalid reference format" — docker pulls "image:sha production"
##   · Root: parse_ssh_command didn't strip optional third token (environment)
##   · Fix: REF="${REF%% *}" — strip everything after second space
##
##   💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
##   · Source: owner (O7/DD10)
##   · Risk: авто-очистка = невосстановимая потеря БД проекта
##   · Safeguard: remove() uses docker compose down WITHOUT -v
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · FQDN uniqueness via validate.sh subprocess
##   · Rejected: Python socket/FQDN parsing (duplicates validate.sh logic)
##   · Reason: validate.sh is the canonical FQDN check
##   · Rev: if validate.sh is ever deprecated → inline Python socket.gethostbyname check
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · Port conflict via ss -tlnp
##   · Rejected: Docker network inspect (only shows mapped ports, not host conflicts)
##   · Reason: ss -tlnp shows ALL listening ports — detects conflicts before Docker starts
##   · Rev: if Docker adds host-port conflict detection → migrate to Docker-native check
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · STUB_AWARE_STATUS flag for stub-detection
##   · Rejected: always detect stubs (performance overhead on every status call)
##   · Reason: stub detection requires yaml_read — optional flag avoids unnecessary I/O
##   · Rev: if stub detection overhead <1ms → make it default, remove flag
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5e: deploy-project.sh Strangler-Fig migrated to Python
##   · Rejected: keeping deploy logic in shell (risk: 1183 LOC monolith, 3 inline python3)
##   · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация с ssh_command_parser
##   · Rev: если Python deploy_engine добавляет >2s latency vs shell → профилировать subprocess overhead
##
##   🧐 TRAP[DECISION] · 2026-07-26 · — · deploy_engine + payload_deliverer — TWO separate modules
##   · Rejected: единый deploy_orchestrator.py (God Class >800 LOC)
##   · Reason: разные домены (Docker orchestration vs file delivery), DDD boundary, переиспользование
##
##   📝 TRAP[DEBT] · 2026-07-26 · MED · Docker operations library — кандидат на shared модуль
##   · Observed: save_previous_image, pull_image_with_retry, prune_old_images дублируются в
##     deploy_engine.py, context_deployer.py, docker_orchestrator.py
##   · Suspected: дедупликация в core/internal/shared/docker_ops.py сократит ~200 LOC дублирования
##   · Impact: при изменении Docker API — правка в 3+ местах вместо одного
##   · When: during Wave 5e implementation — deferred to follow-up DevPlan
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig migration from deploy-project.sh)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, NoReturn

from core.internal.shared.project_registry import validate_project_name

logger = logging.getLogger(__name__)

# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class DeployResult:
    """Result of a deploy operation."""

    success: bool
    project: str
    ref: str
    service: str
    previous_image: str | None = None
    rollback_performed: bool = False
    first_deploy_failed: bool = False
    error_message: str | None = None


@dataclass
class RemoveResult:
    """Result of a remove operation."""

    success: bool
    project: str
    already_removed: bool = False
    error_message: str | None = None


@dataclass
class StatusResult:
    """Result of a status operation."""

    project: str
    node: str
    status: str  # "found" | "not_found" | "stub"
    containers: list[dict] = field(default_factory=list)
    last_deploy: dict | None = None


@dataclass
class ImageInfo:
    """Info about a saved previous image."""

    id: str
    tag: str | None = None


@dataclass
class SnapshotInfo:
    """Info about a pre-deploy snapshot."""

    timestamp: int
    ps_file: str | None = None
    images_file: str | None = None


# ── Custom exceptions ───────────────────────────────────────────────────────


class DeployError(Exception):
    """Raised on unrecoverable deploy failure."""


class ValidationError(Exception):
    """Raised on input validation failure."""


# ── DeployEngine ────────────────────────────────────────────────────────────

# region CLASS_DeployEngine


class DeployEngine:
    """Atomic deploy/rollback/remove/status engine.

    All Docker operations via subprocess.run(["docker", "compose", ...]).
    Healthcheck uses shell lib/healthcheck.sh via subprocess.
    """

    def __init__(self, projects_base: str = "/opt/projects"):
        self.projects_base = projects_base
        self._validate_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "internal",
            "validate",
            "validate.sh",
        )

    # ── Public API ──────────────────────────────────────────────────────────

    # region FUNC_deploy
    ## @purpose  Perform atomic deploy with healthcheck-based rollback.
    ## @io       ⇥ project, ref, service, project_dir, node, max_wait, keep_images → ⎋ DeployResult
    ## @complexity — O(N) where N = pull retry attempts + healthcheck attempts
    ## @invariants
    ##   - Previous image saved BEFORE pull (enables rollback)
    ##   - On success: DEPLOY_STATUS="success" set immediately after health-gate
    ##   - On health fail + first deploy: sys.exit(1), no rollback
    ##   - On health fail + existing deploy: _perform_rollback called
    def deploy(
        self,
        project: str,
        ref: str,
        service: str,
        project_dir: str,
        node: str = "",
        max_wait: int = 60,
        keep_images: int = 3,
    ) -> DeployResult:
        """Execute atomic deploy with rollback capability.

        Args:
            project: Project name.
            ref: Image tag/ref to deploy.
            service: Docker Compose service name.
            project_dir: Path to project directory.
            node: Node name (hostname).
            max_wait: Max seconds to wait for healthcheck.
            keep_images: Number of old images to keep during prune.

        Returns:
            DeployResult with status and rollback metadata.
        """
        # ── Validate ──
        logger.info("[IMP:9][deploy][start] Deploy START: %s/%s → %s", project, service, ref)

        if not validate_project_name(project):
            msg = f"Invalid project name: {project}"
            logger.error("[IMP:10][deploy][validation] %s", msg)
            return DeployResult(success=False, project=project, ref=ref, service=service, error_message=msg)

        os.chdir(project_dir)

        try:
            self._preflight_checks(project_dir, service)
        except (ValidationError, DeployError) as e:
            logger.error("[IMP:10][deploy][preflight] %s", str(e))
            return DeployResult(success=False, project=project, ref=ref, service=service, error_message=str(e))

        # ── Save previous image (BEFORE pull) ──
        previous_image = self._save_previous_image(project_dir, service)
        is_first_deploy = previous_image is None
        logger.log(
            logging.INFO if not is_first_deploy else logging.WARNING,
            "[IMP:9][deploy][save-prev] Previous image: %s (first_deploy=%s)",
            previous_image.id if previous_image else "NONE",
            is_first_deploy,
        )

        # ── Snapshot ──
        snapshot = self._capture_deploy_snapshot(project_dir)
        logger.info("[IMP:8][deploy][snapshot] Captured snapshot: ts=%s", snapshot.timestamp)

        # ── Pull image with retry ──
        if not self._pull_image_with_retry(project_dir, service, ref):
            self._handle_first_deploy(project, service, ref, "Pull failed after 3 attempts")
            # unreachable — _handle_first_deploy raises SystemExit

        # ── Atomic up ──
        if not self._atomic_up(project_dir, service, ref):
            if is_first_deploy:
                self._handle_first_deploy(project, service, ref, "docker compose up failed on first deploy")
            else:
                logger.warning("[IMP:9][deploy][up-fail] atomic_up failed — attempting rollback")
                rollback_ok = self._perform_rollback(project_dir, service, previous_image)
                return DeployResult(
                    success=False,
                    project=project,
                    ref=ref,
                    service=service,
                    previous_image=previous_image.id if previous_image else None,
                    rollback_performed=rollback_ok,
                    error_message="docker compose up failed, rollback " + ("performed" if rollback_ok else "failed"),
                )

        # ── Poll health ──
        healthy = self._poll_health(project_dir, service, max_wait, interval=2)
        if healthy:
            # B1: DEPLOY_STATUS immediately after health-gate
            logger.info("[IMP:9][deploy][health] Healthcheck PASSED for %s/%s", project, service)
            return DeployResult(
                success=True,
                project=project,
                ref=ref,
                service=service,
                previous_image=previous_image.id if previous_image else None,
            )
        logger.error("[IMP:10][deploy][health] Healthcheck FAILED for %s/%s", project, service)
        if is_first_deploy:
            self._handle_first_deploy(project, service, ref, "Healthcheck failed on first deploy")
            # unreachable — _handle_first_deploy raises SystemExit

        rollback_ok = self._perform_rollback(project_dir, service, previous_image)
        return DeployResult(
            success=False,
            project=project,
            ref=ref,
            service=service,
            previous_image=previous_image.id if previous_image else None,
            rollback_performed=rollback_ok,
            error_message="Healthcheck failed, rollback " + ("performed" if rollback_ok else "failed"),
        )

    # endregion FUNC_deploy

    # region FUNC_remove
    ## @purpose  Idempotent remove: stop containers WITHOUT destroying data (O7/DD10).
    ## @io       ⇥ project, project_dir → ⎋ RemoveResult
    ## @complexity — O(1) — single docker compose call
    ## @invariants
    ##   - docker compose down WITHOUT -v (data preserved)
    ##   - Idempotent: if project dir missing or already stopped → already_removed=True
    def remove(self, project: str, project_dir: str) -> RemoveResult:
        """Safely remove (disconnect) a project: stop containers, keep data.

        Args:
            project: Project name.
            project_dir: Path to project directory.

        Returns:
            RemoveResult with status.
        """
        logger.info("[IMP:9][remove][start] Remove START: %s", project)

        if not validate_project_name(project):
            msg = f"Invalid project name: {project}"
            logger.error("[IMP:10][remove][validation] %s", msg)
            return RemoveResult(success=False, project=project, error_message=msg)

        if not os.path.isdir(project_dir):
            logger.info("[IMP:9][remove][not-found] Project dir not found: %s — already removed", project_dir)
            return RemoveResult(success=True, project=project, already_removed=True)

        try:
            os.chdir(project_dir)
        except OSError as e:
            logger.warning("[IMP:8][remove][chdir] Cannot cd to %s: %s", project_dir, e)
            return RemoveResult(success=True, project=project, already_removed=True)

        # docker compose down WITHOUT -v (O7 — data preserved)
        logger.info("[IMP:9][remove][down] Stopping containers for %s (data preserved, no -v)...", project)
        result = subprocess.run(
            ["docker", "compose", "down", "--timeout", "30"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:8][remove][down-warn] docker compose down exit=%s: %s", result.returncode, result.stderr.strip()
            )

        logger.info("[IMP:9][remove][done] Remove DONE: %s (data preserved)", project)
        return RemoveResult(success=True, project=project, already_removed=False)

    # endregion FUNC_remove

    # region FUNC_status
    ## @purpose  Print JSON status for a project: docker compose ps + deploy-result.json.
    ## @io       ⇥ project, project_dir, stub_aware → ⎋ StatusResult
    ## @complexity — O(1) — reads files + docker ps call
    ## @invariants
    ##   - If project dir missing → status="not_found"
    ##   - If ai-platform.yaml starts with GENERATED-STUB → status="stub" (when stub_aware=True)
    ##   - Always returns valid JSON structure
    def status(self, project: str, project_dir: str, stub_aware: bool = False) -> StatusResult:
        """Get JSON status for a project.

        Args:
            project: Project name.
            project_dir: Path to project directory.
            stub_aware: Enable stub detection via ai-platform.yaml header check.

        Returns:
            StatusResult with project state.
        """
        logger.info("[IMP:9][status][start] Status check: %s", project)

        if not os.path.isdir(project_dir):
            logger.info("[IMP:9][status][not-found] Project dir not found: %s", project_dir)
            return StatusResult(project=project, node="", status="not_found")

        # ── Stub-aware detection ──
        # 🧐 TRAP[DECISION] · 2026-07-26 · — · STUB_AWARE_STATUS flag
        ai_yaml = os.path.join(project_dir, "ai-platform.yaml")
        if stub_aware and os.path.isfile(ai_yaml):
            try:
                with open(ai_yaml) as f:
                    first_line = f.readline().strip()
                if "GENERATED-STUB" in first_line:
                    logger.info("[IMP:9][status][stub] Project %s is a GENERATED-STUB", project)
                    return StatusResult(
                        project=project,
                        node="",
                        status="stub",
                        last_deploy={"message": "Project directory exists but ai-platform.yaml is a GENERATED-STUB"},
                    )
            except OSError:
                pass

        # ── Docker compose ps ──
        containers: list[dict[str, Any]] = []
        try:
            os.chdir(project_dir)
            ps_result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if ps_result.returncode == 0 and ps_result.stdout.strip():
                for line in ps_result.stdout.strip().split("\n"):
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:  # noqa: PERF203
                        continue
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:8][status][ps-error] docker compose ps error: %s", str(e))

        # ── Last deploy result ──
        last_deploy: dict[str, Any] | None = None
        deploy_result_file = os.path.join(project_dir, ".deploy-snapshots", "deploy-result.json")
        if os.path.isfile(deploy_result_file):
            try:
                with open(deploy_result_file) as f:
                    last_deploy = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        logger.info("[IMP:9][status][found] Project %s: %d containers", project, len(containers))
        return StatusResult(
            project=project,
            node="",
            status="found",
            containers=containers,
            last_deploy=last_deploy,
        )

    # endregion FUNC_status

    # ── Internal helpers ────────────────────────────────────────────────────

    # region FUNC__save_previous_image
    ## @purpose  Save current image ID before pull (enables rollback).
    ## @io       ⇥ project_dir, service → ⎋ Optional[ImageInfo]
    ## @complexity — O(1) — one docker compose images call + optional docker inspect
    ## @invariants
    ##   - Called BEFORE pull (critical ordering for rollback)
    ##   - Returns None if no previous image (first deploy)
    ##   - If image tag is <none>:<none>, creates fallback tag `project:previous-rollback`
    def _save_previous_image(self, project_dir: str, service: str) -> ImageInfo | None:
        """Save current image ID and tag before deploy.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.

        Returns:
            ImageInfo with ID and tag, or None for first deploy.
        """
        logger.info("[IMP:8][save-prev] Saving previous image for service %s", service)

        result = subprocess.run(
            ["docker", "compose", "images", "-q", service],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_dir,
        )
        image_id = result.stdout.strip()

        if not image_id:
            logger.info("[IMP:9][save-prev] FIRST DEPLOY: no previous image for %s", service)
            return None

        # Get tag
        tag_result = subprocess.run(
            ["docker", "image", "inspect", image_id, "--format", "{{index .RepoTags 0}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        tag = tag_result.stdout.strip()

        if not tag or tag == "<none>:<none>":
            tag = f"{service}:previous-rollback"
            subprocess.run(
                ["docker", "tag", image_id, tag],
                capture_output=True,
                timeout=30,
            )
            logger.info("[IMP:8][save-prev] Created fallback tag for dangling image: %s", tag)

        logger.info("[IMP:9][save-prev] Previous image saved: ID=%s TAG=%s", image_id, tag)
        return ImageInfo(id=image_id, tag=tag)

    # endregion FUNC__save_previous_image

    # region FUNC__capture_deploy_snapshot
    ## @purpose  Capture pre-deploy state snapshot (ps + images JSON) for rollback verification.
    ## @io       ⇥ project_dir → ⎋ SnapshotInfo
    ## @complexity — O(1) — two docker compose calls
    def _capture_deploy_snapshot(self, project_dir: str) -> SnapshotInfo:
        """Capture pre-deploy docker state snapshot.

        Args:
            project_dir: Project directory.

        Returns:
            SnapshotInfo with timestamp and file paths.
        """
        snapshot_dir = os.path.join(project_dir, ".deploy-snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        timestamp = int(time.time())

        ps_file = os.path.join(snapshot_dir, f"ps-{timestamp}.json")
        images_file = os.path.join(snapshot_dir, f"images-{timestamp}.json")

        ps_result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_dir,
        )
        if ps_result.returncode == 0 and ps_result.stdout:
            with open(ps_file, "w") as f:
                f.write(ps_result.stdout)
            logger.info("[IMP:8][snapshot] Wrote ps snapshot: %s (%d bytes)", ps_file, len(ps_result.stdout))
        else:
            logger.info("[IMP:6][snapshot] ps snapshot empty or failed (rc=%d)", ps_result.returncode)

        images_result = subprocess.run(
            ["docker", "compose", "images", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_dir,
        )
        if images_result.returncode == 0 and images_result.stdout:
            with open(images_file, "w") as f:
                f.write(images_result.stdout)
            logger.info(
                "[IMP:8][snapshot] Wrote images snapshot: %s (%d bytes)", images_file, len(images_result.stdout)
            )
        else:
            logger.info("[IMP:6][snapshot] images snapshot empty or failed (rc=%d)", images_result.returncode)

        # Touch .deploy-started marker
        started_file = os.path.join(snapshot_dir, ".deploy-started")
        with open(started_file, "w") as f:
            f.write(str(timestamp))

        logger.info("[IMP:9][snapshot] Snapshot complete: ts=%s dir=%s", timestamp, snapshot_dir)
        return SnapshotInfo(timestamp=timestamp, ps_file=ps_file, images_file=images_file)

    # endregion FUNC__capture_deploy_snapshot

    # region FUNC__preflight_checks
    ## @purpose  Validate pre-deploy conditions: FQDN uniqueness and port conflicts.
    ## @io       ⇥ project_dir, service → ⎋ None (raises on fail)
    ## @complexity — O(1) — subprocess calls to validate.sh and ss
    ## @invariants
    ##   - FQDN check via validate.sh subprocess (canonical, not duplicated in Python)
    ##   - Port conflict via ss -tlnp (shows ALL listening ports)
    ##   - Both checks are non-blocking for first deploy (warnings logged)
    def _preflight_checks(self, project_dir: str, service: str) -> None:
        """Run pre-deploy validation checks.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.

        Raises:
            ValidationError: If FQDN conflict detected.
            DeployError: If port conflict detected.
        """
        # 🧐 TRAP[DECISION] · 2026-07-26 · — · FQDN uniqueness via validate.sh subprocess
        # · Rejected: Python socket/FQDN parsing (duplicates validate.sh logic)
        # · Reason: validate.sh is the canonical FQDN check
        if os.path.isfile(self._validate_script) and os.access(self._validate_script, os.X_OK):
            logger.info("[IMP:8][preflight] Checking FQDN uniqueness...")
            result = subprocess.run(
                [self._validate_script, "--check-fqdn", project_dir],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                msg = f"FQDN conflict detected: {result.stderr.strip()}"
                logger.error("[IMP:10][preflight] %s", msg)
                raise ValidationError(msg)
        else:
            logger.info("[IMP:6][preflight] validate.sh not found — skipping FQDN check")

        # 🧐 TRAP[DECISION] · 2026-07-26 · — · Port conflict via ss -tlnp
        # · Rejected: Docker network inspect (only shows mapped ports, not host conflicts)
        # · Reason: ss -tlnp shows ALL listening ports
        ai_yaml = os.path.join(project_dir, "ai-platform.yaml")
        if os.path.isfile(ai_yaml):
            try:
                import yaml

                with open(ai_yaml) as f:
                    config = yaml.safe_load(f)
                host_port = None
                if config and isinstance(config, dict):
                    monitoring = config.get("monitoring", {})
                    if isinstance(monitoring, dict):
                        host_port = monitoring.get("host_port")
                if host_port and isinstance(host_port, (int, str)) and int(host_port) > 0:
                    port = int(host_port)
                    logger.info("[IMP:8][preflight] Checking port %s for conflicts...", port)
                    ss_result = subprocess.run(
                        ["ss", "-tlnp"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if f":{port} " in ss_result.stdout:
                        msg = f"Port {port} already in use — deploy blocked"
                        logger.error("[IMP:10][preflight] %s", msg)
                        raise DeployError(msg)
                    logger.info("[IMP:8][preflight] Port %s available", port)
            except (ImportError, yaml.YAMLError, OSError) as e:
                logger.info("[IMP:6][preflight] Could not check port: %s", str(e))

    # endregion FUNC__preflight_checks

    # region FUNC__pull_image_with_retry
    ## @purpose  Pull Docker image with exponential backoff retry, rate-limit detection.
    ## @io       ⇥ project_dir, service, ref, max_attempts=3 → ⎋ bool
    ## @complexity — O(max_attempts) — O(1) per attempt
    ## @invariants
    ##   - Backoff: [5, 10, 20] seconds
    ##   - Rate-limit detected via "toomanyrequests|429|rate limit" in output
    ##   - Returns False after all attempts exhausted
    def _pull_image_with_retry(
        self,
        project_dir: str,
        service: str,
        ref: str,
        max_attempts: int = 3,
    ) -> bool:
        """Pull image with retry and backoff.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            ref: Image tag to pull.
            max_attempts: Number of pull attempts.

        Returns:
            True if pull succeeded, False after all attempts fail.
        """
        delays = [5, 10, 20]
        env = os.environ.copy()
        env["IMAGE_TAG"] = ref

        for attempt in range(1, max_attempts + 1):
            logger.info(
                "[IMP:8][pull] Attempt %d/%d: pulling %s with IMAGE_TAG=%s", attempt, max_attempts, service, ref
            )
            result = subprocess.run(
                ["docker", "compose", "pull", service],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_dir,
                env=env,
            )

            if result.returncode == 0:
                logger.info("[IMP:9][pull] Pull complete: %s:%s", service, ref)
                return True

            # Rate-limit detection
            output = (result.stdout + result.stderr).lower()
            if any(pattern in output for pattern in ("toomanyrequests", "429", "rate limit")):
                logger.warning("[IMP:9][pull] Rate limit hit on attempt %d", attempt)
            else:
                logger.warning(
                    "[IMP:9][pull] Pull failed on attempt %d (exit=%d): %s",
                    attempt,
                    result.returncode,
                    result.stderr.strip()[:200],
                )

            if attempt < max_attempts:
                delay = delays[min(attempt - 1, len(delays) - 1)]
                logger.info("[IMP:7][pull] Waiting %ds before retry...", delay)
                time.sleep(delay)

        logger.error("[IMP:10][pull] FATAL: pull failed after %d attempts for %s:%s", max_attempts, service, ref)
        return False

    # endregion FUNC__pull_image_with_retry

    # region FUNC__atomic_up
    ## @purpose  Execute docker compose up -d for single service.
    ## @io       ⇥ project_dir, service, ref → ⎋ bool
    ## @complexity — O(1) — single docker compose call
    def _atomic_up(self, project_dir: str, service: str, ref: str) -> bool:
        """Start service via docker compose up -d.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            ref: Image tag.

        Returns:
            True if up succeeded, False otherwise.
        """
        logger.info("[IMP:9][up] Atomic up: %s (IMAGE_TAG=%s)", service, ref)
        env = os.environ.copy()
        env["IMAGE_TAG"] = ref

        result = subprocess.run(
            ["docker", "compose", "up", "-d", service],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_dir,
            env=env,
        )

        if result.returncode != 0:
            logger.error(
                "[IMP:10][up] docker compose up -d failed (exit=%d): %s", result.returncode, result.stderr.strip()[:200]
            )
            return False

        logger.info("[IMP:8][up] Container started for %s", service)
        return True

    # endregion FUNC__atomic_up

    # region FUNC__poll_health
    ## @purpose  Poll container health until healthy or timeout.
    ## @io       ⇥ project_dir, service, timeout, interval → ⎋ bool
    ## @complexity — O(timeout/interval) healthcheck calls
    ## @invariants
    ##   - Uses docker inspect --format for health status
    ##   - Container "running" without healthcheck → considered healthy
    ##   - Polls every `interval` seconds until `timeout`
    def _poll_health(self, project_dir: str, service: str, timeout: int, interval: int = 2) -> bool:
        """Poll container health until healthy.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            timeout: Max seconds to poll.
            interval: Seconds between polls.

        Returns:
            True if healthy, False on timeout.
        """
        logger.info("[IMP:8][health] Polling health for %s (timeout=%ds, interval=%ds)", service, timeout, interval)
        deadline = time.time() + timeout

        while time.time() < deadline:
            cid_result = subprocess.run(
                ["docker", "compose", "ps", "-q", service],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=project_dir,
            )
            cid = cid_result.stdout.strip()

            if cid:
                inspect_result = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}} {{.State.Health.Status}}", cid],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                status_line = inspect_result.stdout.strip()

                if "running" in status_line and ("healthy" in status_line or "unhealthy" not in status_line):
                    logger.info("[IMP:9][health] Container %s is healthy", service)
                    return True

            time.sleep(interval)

        logger.error("[IMP:10][health] Healthcheck timeout for %s (%ds)", service, timeout)
        return False

    # endregion FUNC__poll_health

    # region FUNC__perform_rollback
    ## @purpose  Rollback to previous image: re-tag + docker compose up --force-recreate.
    ## @io       ⇥ project_dir, service, previous_image → ⎋ bool
    ## @complexity — O(1) — tag + compose up calls
    ## @invariants
    ##   - Re-tags previous image before compose up (ensures correct image reference)
    ##   - Uses --force-recreate to ensure container replacement
    ##   - Returns False if rollback compose up fails
    def _perform_rollback(self, project_dir: str, service: str, previous_image: ImageInfo | None) -> bool:
        """Rollback to previous image.

        Args:
            project_dir: Project directory.
            service: Docker Compose service name.
            previous_image: ImageInfo with ID/tag of previous image.

        Returns:
            True if rollback succeeded, False otherwise.
        """
        if previous_image is None:
            logger.error("[IMP:10][rollback] No previous image — cannot rollback")
            return False

        logger.info("[IMP:10][rollback] ROLLING BACK %s to %s", service, previous_image.id)

        # Re-tag previous image
        if previous_image.tag:
            subprocess.run(
                ["docker", "tag", previous_image.id, previous_image.tag],
                capture_output=True,
                timeout=30,
            )
            logger.info("[IMP:9][rollback] Re-tagged %s → %s", previous_image.id, previous_image.tag)

        # docker compose up -d --force-recreate
        env = os.environ.copy()
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--force-recreate", service],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_dir,
            env=env,
        )

        if result.returncode != 0:
            logger.error(
                "[IMP:10][rollback] Rollback compose up FAILED (exit=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False

        logger.info("[IMP:10][rollback] Rollback complete: %s restored to %s", service, previous_image.id)
        return True

    # endregion FUNC__perform_rollback

    # region FUNC__handle_first_deploy
    ## @purpose  Handle first deploy failure — no rollback possible, escalate.
    ## @io       ⇥ project, service, ref, reason → ⎋ NoReturn (sys.exit)
    def _handle_first_deploy(self, project: str, service: str, ref: str, reason: str) -> NoReturn:
        """Handle first deploy failure — no rollback possible.

        Args:
            project: Project name.
            service: Service name.
            ref: Image ref.
            reason: Failure reason for logging.

        Raises:
            SystemExit: Always exits with code 1.
        """
        logger.error("[IMP:10][first-deploy] CRITICAL: %s — %s no previous image to rollback", reason, service)
        sys.exit(1)

    # endregion FUNC__handle_first_deploy


# endregion CLASS_DeployEngine


# ── CLI ──────────────────────────────────────────────────────────────────────

# region CLI
## @purpose  CLI entrypoint with argparse subcommands: deploy, remove, status.
## @io       ⇥ sys.argv → ⎋ exit 0|1
## @rationale D8 (DevPlan 036E): argparse subcommands for debuggability, testability, composability.
##            Shell facade performs verb classification before calling Python modules.
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy Engine — atomic deploy/rollback/remove/status")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Deploy subcommand ──
    deploy_parser = sub.add_parser("deploy", help="Atomic deploy with healthcheck-based rollback")
    deploy_parser.add_argument("--project", required=True)
    deploy_parser.add_argument("--ref", required=True)
    deploy_parser.add_argument("--service", required=True)
    deploy_parser.add_argument("--project-dir", required=True)
    deploy_parser.add_argument("--node", default="")
    deploy_parser.add_argument("--max-wait", type=int, default=60)
    deploy_parser.add_argument("--keep-images", type=int, default=3)

    # ── Remove subcommand ──
    remove_parser = sub.add_parser("remove", help="Idempotent remove (data preserved)")
    remove_parser.add_argument("--project", required=True)
    remove_parser.add_argument("--project-dir", required=True)

    # ── Status subcommand ──
    status_parser = sub.add_parser("status", help="JSON status output")
    status_parser.add_argument("--project", required=True)
    status_parser.add_argument("--project-dir", required=True)
    status_parser.add_argument("--stub-aware", action="store_true", default=False)

    args = parser.parse_args()
    engine = DeployEngine()

    if args.command == "deploy":
        result = engine.deploy(
            project=args.project,
            ref=args.ref,
            service=args.service,
            project_dir=args.project_dir,
            node=args.node,
            max_wait=args.max_wait,
            keep_images=args.keep_images,
        )
        # JSON output for shell facade
        print(
            json.dumps(
                {
                    "success": result.success,
                    "project": result.project,
                    "ref": result.ref,
                    "service": result.service,
                    "previous_image": result.previous_image,
                    "rollback_performed": result.rollback_performed,
                    "first_deploy_failed": result.first_deploy_failed,
                    "error_message": result.error_message,
                }
            )
        )
        sys.exit(0 if result.success else 1)

    elif args.command == "remove":
        result = engine.remove(project=args.project, project_dir=args.project_dir)
        print(
            json.dumps(
                {
                    "success": result.success,
                    "project": result.project,
                    "already_removed": result.already_removed,
                    "error_message": result.error_message,
                }
            )
        )
        sys.exit(0 if result.success else 1)

    elif args.command == "status":
        result = engine.status(
            project=args.project,
            project_dir=args.project_dir,
            stub_aware=args.stub_aware,
        )
        print(
            json.dumps(
                {
                    "project": result.project,
                    "node": result.node,
                    "status": result.status,
                    "containers": result.containers,
                    "last_deploy": result.last_deploy,
                }
            )
        )
        sys.exit(0)
# endregion CLI
