#!/usr/bin/env python3
# GREP_SUMMARY: docker-compose, shared, pull, build, up, healthcheck, retry, image-exists, bootstrap
# STRUCTURE: ▶ ┌compose_dir┐ → ◇ docker compose pull|build|up -d → ⊕ subprocess.run → ⎋ bool
#            ▶ healthcheck_poll ┌project_name┐ → ○ loop [interval] → ◇ docker ps → healthy? → ⎋ str
#            ▶ retry_pull ┌compose_dir┐ → ○ max_attempts: ◇ pull? → success / backoff → ⎋ bool
#            ▶ check_image_exists ┌image_ref┐ → ◇ docker manifest inspect → returncode? → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Shared Docker Compose operations library for bootstrap pipeline.
##           Replaces duplicate docker compose operations in context_deployer.py
##           and docker_orchestrator.py with a single canonical implementation.
## @scope    Low-level infrastructure layer (by DDD): pull, build, up, healthcheck,
##           retry_pull, check_image_exists. NOT business logic — does not know about
##           projects, contexts, or modules. Business orchestration stays in callers.
## @invariants
##   1. All functions operate via subprocess.run(['docker', 'compose', ...]) — no SDK
##   2. All functions log via standard logging.getLogger("docker_compose")
##   3. Non-fatal: failures return False/empty, never raise (caller decides severity)
##   4. Standard timeouts: pull 120s, build 300s, up 120s, healthcheck composite
##   5. Directory existence is validated before operations (returns False if missing)
## @rationale  D2 from DevPlan: Pulling infrastructure (docker compose pull/build/up) into
##             a shared module eliminates DRIFT-B6 — two independent implementations of
##             the same docker operations in context_deployer.py and docker_orchestrator.py.
##             This is the infrastructure layer; business orchestration (which modules,
##             skip logic, parallel deploy) stays in the callers.
## @changes    2026-07-25 | DevPlan 079 DRIFT-B6 — Created as shared module
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import time

logger = logging.getLogger("docker_compose")

# ── Default timeouts ──
PULL_TIMEOUT = 120
BUILD_TIMEOUT = 300
UP_TIMEOUT = 120
HEALTHCHECK_TIMEOUT = 60
IMAGE_CHECK_TIMEOUT = 60


# region FUNC_docker_compose_pull


def docker_compose_pull(
    compose_dir: str,
    timeout: int = PULL_TIMEOUT,
    compose_args: list[str] | None = None,
) -> bool:
    """Run docker compose pull in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose pull → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Pull container images for a docker compose project.
    ## @io — ⇥ compose_dir: str, timeout: int, compose_args: list[str] | None → ⎋ bool (True = success)
    ## @complexity — O(1) + network I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure/exception
    ##   - Validates directory existence before running
    ##   - compose_args (if provided) are inserted BEFORE "pull" subcommand
    ##     (e.g. ["-f", "compose.yaml", "--env-file", ".env", "--profile", "name"])
    ## @changes 2026-07-26 · DevPlan 079 TASK-9 — Added compose_args parameter for
    ##           docker_orchestrator._pull_module_images() migration
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose_pull] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_pull] Pulling images in %s", compose_dir)
    cmd = ["docker", "compose"]
    if compose_args:
        cmd.extend(compose_args)
    cmd.append("pull")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=compose_dir,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker_compose_pull] Pull succeeded in %s", compose_dir)
            return True
        logger.warning(
            "[IMP:7][docker_compose_pull] Pull failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_compose_pull] Pull timed out (%ds) in %s", timeout, compose_dir)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][docker_compose_pull] docker command not found")
        return False


# endregion FUNC_docker_compose_pull


# region FUNC_docker_compose_build


def docker_compose_build(compose_dir: str, timeout: int = BUILD_TIMEOUT) -> bool:
    """Run docker compose build in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose build → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Build container images locally for a docker compose project.
    ## @io — ⇥ compose_dir: str, timeout: int → ⎋ bool (True = success)
    ## @complexity — O(1) + build I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure
    ##   - Longer timeout (300s) for potentially slow builds
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose_build] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_build] Building images in %s", compose_dir)
    try:
        result = subprocess.run(
            ["docker", "compose", "build"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=compose_dir,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker_compose_build] Build succeeded in %s", compose_dir)
            return True
        logger.warning(
            "[IMP:7][docker_compose_build] Build failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_compose_build] Build timed out (%ds) in %s", timeout, compose_dir)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][docker_compose_build] docker command not found")
        return False


# endregion FUNC_docker_compose_build


# region FUNC_docker_compose_up


def docker_compose_up(compose_dir: str, timeout: int = UP_TIMEOUT) -> bool:
    """Run docker compose up -d in a directory.

    ▶ ┌compose_dir┐ → ◇ exists? → subprocess docker compose up -d → ⊕ returncode==0 → ⎋ bool

    ## @purpose — Start containers for a docker compose project in detached mode.
    ## @io — ⇥ compose_dir: str, timeout: int → ⎋ bool (True = success)
    ## @complexity — O(1) + startup I/O
    ## @invariants
    ##   - Non-fatal: returns False on failure
    ##   - Uses -d (detached) mode
    """
    if not os.path.isdir(compose_dir):
        logger.warning("[IMP:7][docker_compose_up] Directory not found: %s", compose_dir)
        return False

    logger.info("[IMP:7][docker_compose_up] Starting containers in %s", compose_dir)
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=compose_dir,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker_compose_up] Containers started in %s", compose_dir)
            return True
        logger.warning(
            "[IMP:7][docker_compose_up] Up failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_compose_up] Up timed out (%ds) in %s", timeout, compose_dir)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][docker_compose_up] docker command not found")
        return False


# endregion FUNC_docker_compose_up


# region FUNC_healthcheck_poll


def healthcheck_poll(
    project_name: str,
    timeout: int = HEALTHCHECK_TIMEOUT,
    interval: int = 3,
    use_inspect: bool = True,
) -> str:
    """Poll container health for a project until healthy or timeout.

    ▶ ┌project_name + timeout┐ → ○ loop [interval]: ◇ docker ps --filter name → all healthy? → "healthy"
    │                                                                         → timeout? → "unhealthy"

    ## @purpose — Wait for a docker compose project to become healthy.
    ## @io — ⇥ project_name: str, timeout: int, interval: int, use_inspect: bool → ⎋ str ("healthy"|"unhealthy")
    ## @complexity — O(T/I) where T = timeout, I = interval
    ## @invariants
    ##   - Polls container health status every `interval` seconds
    ##   - Returns "healthy" when ALL matching containers are Up (none unhealthy/restarting)
    ##   - Returns "unhealthy" if timeout expires before all containers healthy
    ##   - Non-fatal: returns "unhealthy" on docker errors
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={project_name}", "--format", "{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.debug("[IMP:6][healthcheck_poll] docker ps returned %d for %s", result.returncode, project_name)
                time.sleep(interval)
                continue

            lines = result.stdout.strip().splitlines()
            if not lines:
                logger.debug("[IMP:6][healthcheck_poll] No containers for %s yet", project_name)
                time.sleep(interval)
                continue

            # Check for unhealthy or restarting containers
            if not any("unhealthy" in line.lower() or "restarting" in line.lower() for line in lines):
                logger.info("[IMP:9][healthcheck_poll] %s — healthy", project_name)
                return "healthy"

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug("[IMP:6][healthcheck_poll] Docker check failed for %s: %s", project_name, e)
            time.sleep(interval)
            continue

        time.sleep(interval)

    logger.warning("[IMP:7][healthcheck_poll] %s — timeout (%ds) — unhealthy", project_name, timeout)
    return "unhealthy"


# endregion FUNC_healthcheck_poll


# region FUNC_retry_pull


def retry_pull(
    compose_dir: str,
    max_attempts: int = 3,
    backoff_seconds: list[int] | None = None,
    timeout: int = PULL_TIMEOUT,
) -> bool:
    """Pull images with retries and backoff.

    ▶ ┌compose_dir┐ → ○ attempt 1..max: pull → success? → ⎋ True
    │                                            → fail → sleep backoff → retry

    ## @purpose — Resilient pull with configurable retries and exponential-style backoff.
    ## @io — ⇥ compose_dir: str, max_attempts: int, backoff_seconds: list[int] → ⎋ bool
    ## @complexity — O(max_attempts * pull_time)
    ## @invariants
    ##   - Default backoff: [5, 10, 20] seconds between retries
    ##   - If backoff list shorter than attempts, all remaining attempts use last value
    ##   - All attempts must fail for final False
    """
    if backoff_seconds is None:
        backoff_seconds = [5, 10, 20]

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "[IMP:7][retry_pull] Attempt %d/%d for %s",
            attempt,
            max_attempts,
            compose_dir,
        )
        if docker_compose_pull(compose_dir, timeout=timeout):
            logger.info(
                "[IMP:9][retry_pull] Pull succeeded on attempt %d/%d for %s", attempt, max_attempts, compose_dir
            )
            return True

        if attempt < max_attempts:
            backoff_idx = min(attempt - 1, len(backoff_seconds) - 1)
            sleep_sec = backoff_seconds[backoff_idx]
            logger.warning(
                "[IMP:7][retry_pull] Pull attempt %d failed — retrying in %ds",
                attempt,
                sleep_sec,
            )
            time.sleep(sleep_sec)

    logger.warning("[IMP:7][retry_pull] All %d attempts failed for %s", max_attempts, compose_dir)
    return False


# endregion FUNC_retry_pull


# region FUNC_check_image_exists


def check_image_exists(image_ref: str, timeout: int = IMAGE_CHECK_TIMEOUT) -> bool:
    """Check if a Docker image exists in registry via docker manifest inspect.

    ▶ ┌image_ref┐ → ◇ docker manifest inspect → returncode==0? → ⎋ True
    │                                                       → else → ⎋ False

    ## @purpose — Verify image existence without pulling (uses manifest inspect).
    ## @io — ⇥ image_ref: str, timeout: int → ⎋ bool (True = image exists)
    ## @complexity — O(1) + network
    ## @invariants
    ##   - Uses `docker manifest inspect` which does not pull the image
    ##   - stderr is suppressed (expected on non-existent image)
    ##   - Non-fatal: returns False on errors/timeout
    """
    logger.info("[IMP:7][check_image_exists] Checking image: %s", image_ref)
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][check_image_exists] Image exists: %s", image_ref)
            return True
        logger.warning("[IMP:5][check_image_exists] Image NOT found: %s", image_ref)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:5][check_image_exists] docker manifest inspect timed out for %s", image_ref)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][check_image_exists] docker command not found")
        return False


# endregion FUNC_check_image_exists
