#!/usr/bin/env python3
# GREP_SUMMARY: docker-ops docker-manager compose-down compose-pull compose-up cleanup-images stop-container container-status run-docker
# STRUCTURE: ▶ DockerManager(compose_file, project_name, module_dir) → _run_docker → compose_down/compose_pull/compose_up (shared) → cleanup_old_images → stop_container → container_status
# region MODULE_CONTRACT
## @purpose  Docker operations for the hermes-agent watchdog — extracted from agent_watchdog.py
##           (DevPlan 117 G T52). compose down/pull/up delegate to shared/docker_compose
##           (DevPlan 117 D19); raw docker commands (image ls, stop, ps, rmi) via _run_docker.
## @scope    Consumed by core/modules/hermes-agent/watchdog/agent_watchdog.py (lazy import).
##           Runs on the host node via `sudo docker`.
## @invariants
##   - compose_* (down/pull/up) → shared/docker_compose (single source, DevPlan 117 D19)
##   - raw docker commands → _run_docker (subprocess, NEVER shell=True)
##   - _run_docker: TimeoutExpired → returncode 124, FileNotFoundError → returncode 127
##   - cleanup_old_images: keeps newest `keep` images by CreatedAt, removes the rest
##   - stop_container: docker stop → fallback docker kill; already-stopped = success
## @rationale  DevPlan 117 G T52 — extracted verbatim from agent_watchdog.py (DockerManager,
##            ~202 LOC) with all LDD logs and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T52 — extracted from agent_watchdog.py
# endregion MODULE_CONTRACT

import logging
import subprocess

from core.internal.shared.docker_compose import (
    docker_compose_down,
    docker_compose_pull,
    docker_compose_up,
)  # LINT-EXEMPT: контейнерный модуль; shared — by design (D1, allowlist 116 B11 T1, DevPlan 117 D19)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# DockerManager
# ═══════════════════════════════════════════════════════════════════


# region CLASS__DockerManager
class DockerManager:
    """Manage Docker operations: compose, images, containers."""

    def __init__(self, compose_file: str, project_name: str, module_dir: str):
        self._compose_file = compose_file
        self._project = project_name
        self._module_dir = module_dir

    def _run_docker(self, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        """Run a docker/docker compose command with consistent error handling."""
        try:
            return subprocess.run(
                ["sudo", "docker", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.info("[IMP:9][watchdog][docker] Timeout: docker %s", " ".join(args))
            return subprocess.CompletedProcess(args=args, returncode=124, stdout="", stderr="timeout")
        except FileNotFoundError:
            logger.info("[IMP:9][watchdog][docker] docker not found")
            return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr="docker: not found")

    def compose_down(self, service: str) -> bool:
        """docker compose down <service> — delegated to shared/docker_compose (DevPlan 117 D19)."""
        logger.info("[IMP:8][watchdog][rollback] Step 5a: stopping %s via docker compose down", service)
        ok = docker_compose_down(
            self._module_dir,
            compose_args=["-f", self._compose_file, "--project-name", self._project],
            service=service,
        )
        if not ok:
            logger.info(
                "[IMP:9][watchdog][rollback] WARNING: docker compose down returned non-zero — continuing rollback"
            )
        return ok

    def compose_pull(self) -> bool:
        """docker compose pull — delegated to shared/docker_compose (DevPlan 117 D19)."""
        logger.info("[IMP:8][watchdog][rollback] Step 5b: pulling image via docker compose pull")
        ok = docker_compose_pull(
            self._module_dir,
            compose_args=["-f", self._compose_file, "--project-name", self._project],
        )
        if not ok:
            logger.info(
                "[IMP:9][watchdog][rollback] CRITICAL: docker compose pull failed — "
                "image may not be available in registry"
            )
        return ok

    def compose_up(self, service: str) -> bool:
        """docker compose up -d <service> — delegated to shared/docker_compose (DevPlan 117 D19)."""
        logger.info(
            "[IMP:8][watchdog][rollback] Step 5c: starting %s with previous version (docker compose up -d)",
            service,
        )
        ok = docker_compose_up(
            self._module_dir,
            compose_args=["-f", self._compose_file, "--project-name", self._project],
            service=service,
        )
        if not ok:
            logger.info("[IMP:9][watchdog][rollback] CRITICAL: docker compose up -d failed")
        return ok

    def cleanup_old_images(self, keep: int) -> int:
        """Remove old hermes-agent images beyond keep count.

        Returns number of images removed.
        """
        logger.info("[IMP:7][watchdog][cleanup] Cleaning old hermes-agent images (keep=%d)", keep)

        # List hermes-agent images sorted by creation date (newest first)
        result = self._run_docker(
            [
                "image",
                "ls",
                "--filter",
                "reference=hermes-agent",
                "--format",
                "{{.Repository}}:{{.Tag}} {{.CreatedAt}}",
            ],
            timeout=30,
        )

        if result.returncode != 0 or not result.stdout.strip():
            logger.info("[IMP:7][watchdog][cleanup] No hermes-agent images found — skipping cleanup")
            return 0

        # Parse and sort by date (newest first)
        lines = result.stdout.strip().splitlines()
        # Each line: "hermes-agent:tag 2024-01-01 12:00:00 +0000 UTC"
        images = []
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) >= 2:
                images.append((parts[0], parts[1]))

        # Sort by date descending (newest first)
        images.sort(key=lambda x: x[1], reverse=True)

        removed = 0
        for i, (img_ref, _) in enumerate(images):
            if i < keep:
                continue
            logger.info("[IMP:7][watchdog][cleanup] Removing old image: %s", img_ref)
            r = self._run_docker(["rmi", img_ref], timeout=30)
            if r.returncode == 0:
                removed += 1
            else:
                logger.info(
                    "[IMP:7][watchdog][cleanup] WARNING: Could not remove image %s (may be in use)",
                    img_ref,
                )

        logger.info(
            "[IMP:7][watchdog][cleanup] Image cleanup complete (found=%d, kept=%d, removed=%d)",
            len(images),
            min(len(images), keep),
            removed,
        )
        return removed

    def stop_container(self, name: str) -> bool:
        """Stop a Docker container (docker stop, fallback to docker kill)."""
        # Check if container is running
        ps_result = self._run_docker(
            [
                "ps",
                "--format",
                "{{.Names}}",
            ],
            timeout=10,
        )

        running_containers = ps_result.stdout.strip().splitlines()
        if name not in running_containers:
            logger.info(
                "[IMP:8][watchdog][cb:%s] Container %s is not running",
                name,
                name,
            )
            return True  # Already stopped = success

        logger.info("[IMP:9][watchdog][cb:%s] Stopping container %s", name, name)
        stop_result = self._run_docker(["stop", name], timeout=30)
        if stop_result.returncode == 0:
            logger.info("[IMP:8][watchdog][cb:%s] Container %s stopped", name, name)
            return True

        # Fallback to kill
        logger.info("[IMP:9][watchdog][cb:%s] stop failed — trying kill", name)
        kill_result = self._run_docker(["kill", name], timeout=10)
        if kill_result.returncode != 0:
            logger.info(
                "[IMP:9][watchdog][cb:%s] WARNING: Could not stop container %s",
                name,
                name,
            )
            return False
        return True

    def container_status(self, name: str) -> str:
        """Get container status for diagnostics."""
        result = self._run_docker(
            [
                "ps",
                "-a",
                "--filter",
                f"name={name}",
                "--format",
                "{{.Names}} {{.Status}} {{.Image}}",
            ],
            timeout=10,
        )
        return result.stdout.strip()


# endregion
