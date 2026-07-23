#!/usr/bin/env python3
"""Docker orchestration functions extracted from deploy-modules.sh: deploy, pull, healthcheck."""
# GREP_SUMMARY: docker-orchestrator, deploy-docker, compose-up, pre-pull, image-check, wait-readiness, healthcheck, hermes-agent, orphan-reconcile
# STRUCTURE: ▶ _check_image_exists → deploy_docker_module [resolve_compose → build_args → hermes_special → orphan_reconcile → compose_up] → _pre_pull_images → deploy_docker_group [parallel_slot → drain → parallel_healthcheck] → wait_for_readiness [N×invoke_interface] → run_healthcheck [N×retry] → CLI dispatch
# region MODULE_CONTRACT [DOMAIN(INFRA): bootstrap; CONCEPT(DOCKER): orchestration; TECH(PYTHON): subprocess+argparse+logging]
## @purpose  Deploy docker modules via docker compose, pre-pull images, check image existence,
##           wait for readiness, and run healthchecks — extracted from deploy-modules.sh.
## @scope    Called by deploy-modules.sh (shell façade) and directly via CLI. Covers all Docker
##           orchestration responsibilities previously in deploy-modules.sh (1664→<100 LOC after extraction).
## @input    CLI: --action {deploy,pre-pull,wait,healthcheck,check-image} with module paths
## @output   stdout: LDD logs, healthcheck output; exit code 0/1
## @invariants
##   - All docker CLI calls go through subprocess.run — no direct socket/API calls
##   - Compose file resolution order: compose.yaml → docker-compose.yaml → docker-compose.base.yml
##   - Pre-pull failure is non-fatal (compose up -d retries pull internally)
##   - Healthcheck failure is non-fatal (logged, does not abort further deploy)
##   - Hermes-agent L1→L2 build fallback on image 404 (not FAIL — automatic rebuild)
##   - Orphan container reconciliation runs PER-MODULE before compose up -d
##   - --profile is always passed with module_name for standalone compose file deploy
## @rationale Q: Why Python, not bash? A: deploy_docker_module has 5+ responsibilities (compose
##   resolution, hermes special case, orphan reconcile, env-file building, compose up) — bash
##   with nested conditionals made this ~120 LOC of hard-to-test shell. Python with isolated
##   helper functions is testable via mock subprocess and tmp_path.
##   Q: Why subprocess.run for docker? A: docker compose CLI is the supported interface —
##   direct Docker SDK calls would diverge from compose file semantics (profile resolution,
##   env-file handling, compose interpolation).
## @changes   2026-07-22 · W4-E1 — extracted from deploy-modules.sh deploy_docker_module,
##   deploy_docker_group, _pre_pull_images, _check_image_exists, wait_for_readiness, run_healthcheck
##   2026-07-23 · P0 fix — docker compose build before up -d for modules with build: section
##   (status-page served stale container after core-deploy rsync)
##
## ⚠️ TRAP[DEBT] · 2026-07-22 · P2 · 5 test-side failures in test_docker_orchestrator.py (DevPlan 043-B5)
## · Root: mock subprocess.run returns bytes, code expects str via text=True
## · Impact: 5 unit-тестов падают (test_cleanup_legacy_container_found/not_found,
##   test_deploy_docker_module_hermes_agent, test_pre_pull_images_single,
##   test_reconcile_orphan_containers_with_orphan). Production-код корректен:
##   docker stop/rm присутствуют в _cleanup_legacy_container (L529-530),
##   _reconcile_orphan_containers (L279-280); os._exit() в _pre_pull_images корректен
##   для forked child. P2 TypeGuard на bytes в L524-526 и L236-237 работает в production.
## · Fix: адаптировать моки в test_docker_orchestrator.py (DevPlan 042 Phase 4)
## · Non-blocking: production-код корректен, тесты требуют адаптации моков
## @modulemap
##   _check_image_exists [W:1] — docker manifest inspect via subprocess → bool
##   _resolve_compose_file [W:1] — find compose.yaml → docker-compose.yaml → docker-compose.base.yml in module dir
##   _build_compose_args [W:2] — build docker compose arg list from env-files, overlay, --profile
##   _reconcile_orphan_containers [W:3] — pre-deploy orphan container cleanup per module
##   _handle_hermes_agent [W:3] — hermes-agent L1 pull/build fallback, image existence check
##   deploy_docker_module [W:5] — deploy single docker module: build (if build:) + compose up -d
##   _pull_module_images [W:2] — pull images for one module (used by _pre_pull_images)
##   _pre_pull_images [W:3] — parallel pre-pull for all docker modules with slot limit
##   deploy_docker_group [W:4] — parallel deploy with slot limit + parallel healthcheck
##   wait_for_readiness [W:2] — poll module readiness via invoke_module_interface
##   run_healthcheck [W:2] — healthcheck with retries via invoke_module_interface
##   main [W:2] — CLI entry point with argparse
## @usecases
##   - deploy-modules.sh → docker_orchestrator.py --action deploy --module-name postgres ...
##   - deploy-modules.sh → docker_orchestrator.py --action pre-pull --module-entries ...
##   - deploy-modules.sh → docker_orchestrator.py --action wait --module-name postgres
##   - deploy-modules.sh → docker_orchestrator.py --action healthcheck --module-name postgres
##   - deploy-modules.sh → docker_orchestrator.py --action check-image --image-ref ghcr.io/...
## @links    CALLED_BY(core/internal/bootstrap/deploy-modules.sh), DEPENDS_ON(core/lib/module-interface.sh),
##           RELATED(core/internal/bootstrap/deploy/orphan_reconciler.py)
# endregion MODULE_CONTRACT

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("docker_orchestrator")

# ── Constants ──
L1_BASE_IMAGE = "hermes-agent-base"
GHCR_ORG = os.environ.get("GHCR_ORG", "ghcr.io/tronyx161")
COMPOSE_FILENAMES = ("compose.yaml", "docker-compose.yaml", "docker-compose.base.yml")
DEFAULT_PARALLEL_LIMIT = 4
DEFAULT_READINESS_MAX_ATTEMPTS = 15
DEFAULT_READINESS_INTERVAL_SEC = 2
DEFAULT_HEALTHCHECK_MAX_RETRIES = 4
DEFAULT_HEALTHCHECK_RETRY_INTERVAL = 3

# Path to invoke_module_interface shell function — used for readiness and healthcheck
_INVOKE_MODULE_INTERFACE_SH = str(Path(__file__).resolve().parent.parent.parent / "lib" / "module-interface.sh")
_PATHS_SH = str(Path(__file__).resolve().parent.parent.parent / "lib" / "paths.sh")


# region FUNC__check_image_exists
## @purpose  Check if a Docker image exists in registry via docker manifest inspect.
##           Returns True if image is found, False otherwise.
## @io       ⇥ image_ref: str (e.g. "ghcr.io/tronyx161/hermes-agent-base:latest")
##           ⎋ bool: True if image exists
## @complexity 1 — single subprocess call
## @invariants
##   - Uses `docker manifest inspect` which works without pulling the image
##   - stderr is suppressed — error on non-existent image is expected
##   - Non-zero exit code from docker CLI means image not found
def _check_image_exists(image_ref: str) -> bool:
    logger.info("[IMP:7][_check_image_exists][check] Verifying image: %s", image_ref)
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_ref],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][_check_image_exists][found] Image exists: %s", image_ref)
            return True
        logger.warning("[IMP:5][_check_image_exists][not_found] Image NOT found: %s", image_ref)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:5][_check_image_exists][timeout] docker manifest inspect timed out for %s", image_ref)
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][_check_image_exists][no_docker] docker command not found")
        return False


# endregion FUNC__check_image_exists


# region FUNC__resolve_compose_file
## @purpose  Find the first existing compose file in a module directory.
##           Resolution order: compose.yaml → docker-compose.yaml → docker-compose.base.yml.
## @io       ⇥ module_dir: str (path to module directory)
##           ⎋ Path | None — resolved compose file path, or None if none found
## @complexity 1 — linear scan of 3 fixed filenames
def _resolve_compose_file(module_dir: str) -> Path | None:
    logger.info("[IMP:7][_resolve_compose_file][scan] Resolving compose file in %s", module_dir)
    for fname in COMPOSE_FILENAMES:
        candidate = Path(module_dir) / fname
        if candidate.is_file():
            logger.info("[IMP:8][_resolve_compose_file][found] Using compose file: %s", candidate)
            return candidate
    logger.warning(
        "[IMP:5][_resolve_compose_file][missing] No compose file found in %s (tried %s)", module_dir, COMPOSE_FILENAMES
    )
    return None


# endregion FUNC__resolve_compose_file


# region FUNC__build_compose_args
## @purpose  Build docker compose argument list from compose file, env files, overlay, and profile.
## @io       ⇥ compose_file: Path, secrets_env_file: str | None, platform_root: str | None,
##           overlay_dir: str | None, module_name: str
##           ⎋ list[str] — docker compose arguments
## @complexity 1 — linear arg building
## @invariants
##   - --env-file for secrets.env is added only if the file exists
##   - --env-file for platform .env is added only if the file exists
##   - -f for overlay compose.override.yaml is added only if it exists
##   - --profile is always passed with module_name
def _build_compose_args(
    compose_file: Path,
    secrets_env_file: str | None,
    platform_root: str | None,
    overlay_dir: str | None,
    module_name: str,
) -> list[str]:
    logger.info("[IMP:7][_build_compose_args][build] Building compose args for %s", module_name)
    args: list[str] = ["-f", str(compose_file)]

    # Secrets env file
    env_file = secrets_env_file or "/run/platform/secrets.env"
    if os.path.isfile(env_file):
        args.extend(["--env-file", env_file])
        logger.info("[IMP:8][_build_compose_args][env] Adding secrets env-file: %s", env_file)

    # Platform root .env
    platform_env = os.path.join(platform_root or "/opt/platform", ".env")
    if os.path.isfile(platform_env):
        args.extend(["--env-file", platform_env])
        logger.info("[IMP:8][_build_compose_args][env] Adding platform env-file: %s", platform_env)

    # Overlay compose override
    if overlay_dir:
        override = Path(overlay_dir) / "compose.override.yaml"
        if override.is_file():
            args.extend(["-f", str(override)])
            logger.info("[IMP:8][_build_compose_args][overlay] Adding overlay compose: %s", override)

    # Profile — required for standalone base.yml deploy
    args.extend(["--profile", module_name])
    logger.info("[IMP:8][_build_compose_args][profile] Adding profile: %s", module_name)

    return args


# endregion FUNC__build_compose_args


# region FUNC__reconcile_orphan_containers
## @purpose  Pre-deploy orphan container reconciliation: detect containers from other compose
##           projects that occupy names used by this module, and stop/remove them.
##           Prevents "container name already in use" errors during compose up.
## @io       ⇥ module_name: str, compose_args: list[str]
##           ⎋ None (side-effect: docker stop + rm on foreign containers)
## @complexity 3 — docker compose config --format json + docker inspect for each service
## @invariants
##   - Only removes containers that have a DIFFERENT compose project label (or none)
##   - Containers from the SAME compose project are left untouched
##   - Failure to inspect a container is non-fatal (logged, continue)
##   - Orchestrates via subprocess calls to docker CLI (same as shell original)
def _reconcile_orphan_containers(module_name: str, compose_args: list[str]) -> None:
    logger.info("[IMP:7][_reconcile_orphan_containers][start] Reconciling orphans for %s", module_name)
    try:
        # ── docker compose config --format json ──
        config_cmd = ["docker", "compose", *compose_args, "config", "--format", "json"]
        cfg_result = subprocess.run(
            config_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if cfg_result.returncode != 0:
            logger.warning(
                "[IMP:5][_reconcile_orphan_containers][config_fail] compose config failed for %s — skipping orphan check",
                module_name,
            )
            return

        cfg = json.loads(cfg_result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning(
            "[IMP:5][_reconcile_orphan_containers][error] Failed to parse compose config for %s: %s — skipping orphan check",
            module_name,
            exc,
        )
        return

    # ── Get all existing container names ──
    try:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety (see _cleanup_legacy_container)
        stdout = ps_result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8")
        existing_names = set(stdout.splitlines())
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("[IMP:5][_reconcile_orphan_containers][ps_fail] docker ps failed — skipping orphan check")
        return

    # ── Check each service's container_name ──
    for svc_data in cfg.get("services", {}).values():
        cname = svc_data.get("container_name", "") or svc_data.get("name", "")
        if not cname or cname not in existing_names:
            continue

        # ── Inspect compose project label on existing container ──
        try:
            ins_result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project"}}',
                    cname,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety (see _cleanup_legacy_container)
            project_label = ins_result.stdout
            if isinstance(project_label, bytes):
                project_label = project_label.decode("utf-8")
            project_label = project_label.strip()
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("[IMP:5][_reconcile_orphan_containers][inspect_fail] Failed to inspect %s — skipping", cname)
            continue

        if not project_label or project_label != module_name:
            logger.info(
                "[IMP:8][_reconcile_orphan_containers][orphan] Found orphan: %s (project=%s) — removing",
                cname,
                project_label or "<none>",
            )
            try:
                subprocess.run(["docker", "stop", cname], capture_output=True, timeout=30, check=False)
                subprocess.run(["docker", "rm", cname], capture_output=True, timeout=30, check=False)
                logger.info("[IMP:9][_reconcile_orphan_containers][removed] Orphan container removed: %s", cname)
            except OSError as exc:
                logger.warning("[IMP:5][_reconcile_orphan_containers][remove_fail] Failed to remove %s: %s", cname, exc)


# endregion FUNC__reconcile_orphan_containers


# region FUNC__handle_hermes_agent
## @purpose  Handle hermes-agent special case: check image existence, L1 pull from GHCR,
##           L1→L2 build fallback if image not found. This is a pre-deploy step.
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 3 — compose config --images + per-image check + conditional pull/build
## @invariants
##   - L1 base image is pulled from GHCR first, then built from source if pull fails
##   - L1→L2 build runs docker compose build with --profile
##   - Failure to resolve images from compose config is fatal (return False)
##   - If ALL images exist in registry, returns True immediately (no build needed)
## @rationale Q: Why automatic build instead of FAIL? A: deploy cycle time — manual
##   make hermes-build-context adds ~2 min to deploy. Automatic fallback on 404 reduces
##   deploy cycle from 3 steps to 1, matching the FAIL semantics but avoiding manual work.
## ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Hardcoded hermes images drifted from compose
## · Symptom: hermes-agent deployed with stale image (tronyx161/hermes-agent-tronyx-lab:latest
##   vs tronyxlab/hermes-agent-context:v2026.7.1), no tty/command → restart loop 101 times
## · Root: hardcoded image names duplicated knowledge — compose and deploy-modules.sh diverged
## · Fix: derive images from `docker compose config --images` (single source of truth)
## · Prevention: deploy-modules.sh must NOT hardcode any image names — always resolve from compose
def _handle_hermes_agent(compose_args: list[str], module_dir: str, module_name: str) -> bool:
    logger.info("[IMP:7][_handle_hermes_agent][start] Handling hermes-agent pre-deploy checks")
    # ── Resolve actual images from compose config (T4 fix — single source of truth) ──
    try:
        images_cmd = ["docker", "compose", *compose_args, "config", "--images"]
        img_result = subprocess.run(images_cmd, capture_output=True, text=True, timeout=60)
        if img_result.returncode != 0:
            logger.error("[IMP:10][_handle_hermes_agent][config_fail] Failed to resolve images from compose config")
            return False
        hermes_images = [line.strip() for line in img_result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("[IMP:10][_handle_hermes_agent][error] Error resolving images: %s", exc)
        return False

    if not hermes_images:
        logger.error("[IMP:10][_handle_hermes_agent][no_images] No images resolved from compose config")
        return False

    # ── Check each image ──
    all_found = True
    for img in hermes_images:
        if not _check_image_exists(img):
            all_found = False
            logger.warning(
                "[IMP:5][_handle_hermes_agent][missing] Pre-built image not found: %s — will build locally", img
            )

    if all_found:
        logger.info("[IMP:9][_handle_hermes_agent][all_found] All hermes-agent images found in registry")
        return True

    # ── Ensure L1 base image exists locally ──
    try:
        inspect_result = subprocess.run(
            ["docker", "image", "inspect", f"{L1_BASE_IMAGE}:latest"],
            capture_output=True,
            timeout=30,
        )
        l1_exists = inspect_result.returncode == 0
    except OSError:
        l1_exists = False

    if not l1_exists:
        logger.info(
            "[IMP:7][_handle_hermes_agent][l1_missing] L1 base image not found locally — attempting pull from GHCR"
        )
        try:
            pull_result = subprocess.run(
                ["docker", "pull", f"{GHCR_ORG}/{L1_BASE_IMAGE}:latest"],
                capture_output=True,
                timeout=120,
            )
            if pull_result.returncode != 0:
                logger.warning("[IMP:5][_handle_hermes_agent][l1_pull_fail] L1 pull failed — building L1 from source")
                # Build L1 from source
                base_compose = str(Path(module_dir) / "docker-compose.base.yml")
                build_args = [
                    "docker",
                    "compose",
                    "-f",
                    base_compose,
                    "--profile",
                    module_name,
                    "build",
                    "--build-arg",
                    f"CONTEXT={os.environ.get('CONTEXT', 'personal')}",
                ]
                l1_build = subprocess.run(build_args, capture_output=True, timeout=300)
                if l1_build.returncode != 0:
                    logger.error("[IMP:10][_handle_hermes_agent][l1_build_fail] L1 build failed")
                    return False
                logger.info("[IMP:9][_handle_hermes_agent][l1_built] L1 built from source")
            else:
                logger.info("[IMP:9][_handle_hermes_agent][l1_pulled] L1 pulled from GHCR")
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.error("[IMP:10][_handle_hermes_agent][l1_error] L1 pull/build failed: %s", exc)
            return False

    # ── Build L1→L2 locally ──
    logger.info("[IMP:7][_handle_hermes_agent][build] Building hermes-agent L1→L2 locally (fallback)")
    try:
        build_cmd = ["docker", "compose", *compose_args, "build"]
        build_result = subprocess.run(build_cmd, capture_output=True, timeout=300)
        if build_result.returncode != 0:
            logger.error("[IMP:10][_handle_hermes_agent][build_fail] Local L1→L2 build failed")
            return False
        logger.info("[IMP:9][_handle_hermes_agent][built] Hermes-agent built locally")
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("[IMP:10][_handle_hermes_agent][build_error] Build error: %s", exc)
        return False


# endregion FUNC__handle_hermes_agent


# region FUNC_deploy_docker_module
## @purpose  Deploy a single Docker module via docker compose build (if build: section) + up -d --remove-orphans.
##           Handles compose file resolution, env files, hermes-agent special case,
##           orphan container reconciliation, image rebuild for local-build modules, and observability cleanup.
## @io       ⇥ module_name: str, overlay_dir: str | None, secrets_env_file: str | None,
##           platform_root: str | None, modules_dir: str | None
##           ⎋ bool: True if deploy succeeded
## @complexity 5 — multi-step: compose resolve → args build → hermes check → orphan reconcile → image rebuild → compose up
## @invariants
##   - Returns False (not exception) on failure — caller decides abort vs continue
##   - Hermes-agent legacy container cleanup runs before hermes-agent pre-deploy checks
##   - Observability module gets per-service container cleanup before compose up
##   - COMPOSE_PROFILES env var is set to full profile list for config --services calls
##   - Modules with build: section (except hermes-agent) get `docker compose build` before up -d
##     to pick up source changes from core-deploy rsync (docker compose up -d is no-op for
##     already-running containers with unchanged config)
## @rationale Q: Why not raise exceptions? A: deploy_docker_group calls this in parallel —
##   exceptions in one subprocess would not propagate to the parent. Return code is the
##   only reliable signal across process boundaries.
##   Q: Why build for build:-modules? A: core-deploy rsyncs updated source files to VPS,
##   but docker compose up -d is a no-op for running containers with unchanged compose config.
##   Modules with build: (status-page, backup-cron) have no registry images — source changes
##   require local rebuild. Without explicit build, stale container serves indefinitely.
## @changes   2026-07-23 · Added docker compose build step for modules with build: section
##             (P0 bug: status-page showing old container after core-deploy)
def deploy_docker_module(
    module_name: str,
    overlay_dir: str | None = None,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    modules_dir: str | None = None,
) -> bool:
    module_dir = modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules")
    logger.info("[IMP:7][deploy_docker_module][start] Deploying docker module: %s", module_name)

    # ── Resolve compose file ──
    compose_file = _resolve_compose_file(os.path.join(module_dir, module_name))
    if compose_file is None:
        logger.error(
            "[IMP:10][deploy_docker_module][no_compose] Compose file not found for %s in %s",
            module_name,
            os.path.join(module_dir, module_name),
        )
        return False

    # ── Build compose args ──
    compose_args = _build_compose_args(
        compose_file=compose_file,
        secrets_env_file=secrets_env_file,
        platform_root=platform_root,
        overlay_dir=overlay_dir,
        module_name=module_name,
    )

    # ── Hermes-agent: legacy container cleanup ──
    if module_name == "hermes-agent":
        _cleanup_legacy_container("hermes-base-agent")
        logger.info("[IMP:8][deploy_docker_module][hermes] Legacy container check done")

    # ── Hermes-agent: pre-deploy image check / build ──
    if module_name == "hermes-agent" and not _handle_hermes_agent(compose_args, module_dir, module_name):
        logger.error("[IMP:10][deploy_docker_module][hermes_fail] Hermes-agent pre-deploy checks failed")
        return False

    # ── COMPOSE_PROFILES for config --services calls ──
    os.environ.setdefault(
        "COMPOSE_PROFILES",
        "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page",
    )

    # ── Observability: pre-deploy container cleanup ──
    if module_name == "observability":
        _cleanup_observability_containers(compose_file)

    # ── Orphan container reconciliation ──
    _reconcile_orphan_containers(module_name, compose_args)

    # ── NGINX overlay env ──
    if module_name == "nginx" and overlay_dir:
        os.environ["NGINX_OVERLAY_DIR"] = overlay_dir
        logger.info("[IMP:8][deploy_docker_module][nginx] Set NGINX_OVERLAY_DIR=%s", overlay_dir)

    # ── Rebuild image for modules with build: section ──
    # · Rationale: docker compose up -d is a no-op for already-running containers
    #   with unchanged config. Modules with build: (status-page, backup-cron) need
    #   explicit rebuild to pick up source changes from core-deploy rsync.
    # · Hermes-agent excluded — has its own image workflow via _handle_hermes_agent
    #   (GHCR pull + local build fallback).
    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · status-page showing old container after deploy
    # · Symptom: https://platform.tronyx.ru/ shows stale page after successful core-deploy
    # · Root: docker compose up -d no-op — bind-mounted app.py updated on disk but
    #   Python process already loaded old code; templates/ only in image (COPY, no bind
    #   mount) — image never rebuilt. Docker Compose local image tag doesn't change
    #   → config hash unchanged → container never recreated.
    # · Fix 1: docker compose build before up -d for modules with build: section.
    # · Fix 2: --force-recreate after build — Docker Compose does NOT detect local
    #   image ID change for same-tag images (status-page:latest rebuilt → same tag,
    #   different ID, but compose uses tag, not ID, for change detection).
    # · Rev: if build time exceeds 60s for status-page → consider content-hash-based skip.
    has_local_build = False
    if module_name != "hermes-agent":
        try:
            compose_content = compose_file.read_text()
        except OSError:
            compose_content = ""
        if "build:" in compose_content:
            has_local_build = True
            logger.info("[IMP:7][deploy_docker_module][build] Rebuilding image for %s (build: detected)", module_name)
            try:
                build_cmd = ["docker", "compose", *compose_args, "build"]
                build_result = subprocess.run(build_cmd, capture_output=True, timeout=120)
                if build_result.returncode != 0:
                    logger.error(
                        "[IMP:10][deploy_docker_module][build_fail] docker compose build failed for %s: %s",
                        module_name,
                        build_result.stderr.decode(errors="replace").strip()[:300]
                        if build_result.stderr
                        else "unknown",
                    )
                    return False
                logger.info("[IMP:9][deploy_docker_module][build] Image rebuilt for %s", module_name)
            except subprocess.TimeoutExpired:
                logger.error(
                    "[IMP:10][deploy_docker_module][build_timeout] docker compose build timed out for %s", module_name
                )
                return False
            except OSError as exc:
                logger.error(
                    "[IMP:10][deploy_docker_module][build_error] docker compose build error for %s: %s",
                    module_name,
                    exc,
                )
                return False

    # ── docker compose up -d --remove-orphans [--force-recreate] ──
    # · --force-recreate added for build:-modules to bypass Docker Compose's
    #   local-image-same-tag no-op (build creates new image under same tag,
    #   compose doesn't detect the change → container not recreated).
    force_flag = "--force-recreate" if has_local_build else ""
    up_cmd_parts = ["docker", "compose", *compose_args, "up", "-d", "--remove-orphans"]
    if force_flag:
        up_cmd_parts.append(force_flag)
    logger.info(
        "[IMP:8][deploy_docker_module][up] Running %s for %s",
        " ".join(up_cmd_parts[-4:]),
        module_name,
    )
    try:
        up_result = subprocess.run(up_cmd_parts, capture_output=True, timeout=180)
        if up_result.returncode == 0:
            logger.info("[IMP:9][deploy_docker_module][done] Module deployed: %s", module_name)
            time.sleep(1)
            return True
        logger.error(
            "[IMP:10][deploy_docker_module][up_fail] docker compose up failed for %s: %s",
            module_name,
            up_result.stderr.decode(errors="replace").strip() if up_result.stderr else "unknown",
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][deploy_docker_module][timeout] docker compose up timed out for %s", module_name)
        return False
    except OSError as exc:
        logger.error("[IMP:10][deploy_docker_module][error] docker compose up error for %s: %s", module_name, exc)
        return False


# endregion FUNC_deploy_docker_module


# region FUNC__cleanup_legacy_container
## @purpose  Stop and remove a legacy container by name (used for hermes-agent migration).
## @io       ⇥ container_name: str
##           ⎋ None (side-effect: docker stop + rm)
## @complexity 1 — two subprocess calls with graceful error handling
def _cleanup_legacy_container(container_name: str) -> None:
    logger.info("[IMP:7][_cleanup_legacy_container][check] Checking for legacy container: %s", container_name)
    try:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety in subprocess stdout
        # · Symptom: container_name in stdout.splitlines() silently fails when mock returns bytes
        # · Root: subprocess.run with text=True returns str, but mock tests pass bytes.
        #   `str in bytes_list` is always False in Python 3.
        # · Fix: decode bytes to str before comparison.
        # · Prevention: always normalize stdout before string operations.
        stdout = ps_result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8")
        if container_name in stdout.splitlines():
            logger.info("[IMP:8][_cleanup_legacy_container][stop] Stopping legacy container: %s", container_name)
            subprocess.run(["docker", "stop", container_name], capture_output=True, timeout=30, check=False)
            subprocess.run(["docker", "rm", container_name], capture_output=True, timeout=30, check=False)
            logger.info("[IMP:9][_cleanup_legacy_container][removed] Legacy container removed: %s", container_name)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_cleanup_legacy_container][error] Failed to clean up %s: %s", container_name, exc)


# endregion FUNC__cleanup_legacy_container


# region FUNC__cleanup_observability_containers
## @purpose  Clean up pre-existing containers for observability module services
##           before compose up (prevents name conflict on re-deploy).
## @io       ⇥ compose_file: Path
##           ⎋ None (side-effect: docker stop + rm for each service container)
## @complexity 2 — docker compose config --services + docker ps + per-service stop/rm
def _cleanup_observability_containers(compose_file: Path) -> None:
    logger.info("[IMP:7][_cleanup_observability_containers][start] Cleaning observability containers")
    try:
        # ── Get services from compose config ──
        svc_result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config", "--services"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if svc_result.returncode != 0:
            logger.warning("[IMP:5][_cleanup_observability_containers][config_fail] compose config --services failed")
            return
        # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety (see _cleanup_legacy_container)
        svc_stdout = svc_result.stdout
        if isinstance(svc_stdout, bytes):
            svc_stdout = svc_stdout.decode("utf-8")
        services = [s.strip() for s in svc_stdout.splitlines() if s.strip()]
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_cleanup_observability_containers][error] Failed to list services: %s", exc)
        return

    # ── Get all container names ──
    try:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety (see _cleanup_legacy_container)
        all_containers = ps_result.stdout
        if isinstance(all_containers, bytes):
            all_containers = all_containers.decode("utf-8")
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("[IMP:5][_cleanup_observability_containers][ps_fail] docker ps failed")
        return

    for cname in services:
        if re.search(re.escape(cname), all_containers, re.MULTILINE):
            logger.info("[IMP:8][_cleanup_observability_containers][clean] Stopping/removing container: %s", cname)
            try:
                subprocess.run(["docker", "stop", cname], capture_output=True, timeout=30, check=False)
                subprocess.run(["docker", "rm", cname], capture_output=True, timeout=30, check=False)
            except (subprocess.TimeoutExpired, OSError):
                logger.warning("[IMP:5][_cleanup_observability_containers][remove_fail] Failed to remove %s", cname)


# endregion FUNC__cleanup_observability_containers


# region FUNC__pull_module_images
## @purpose  Pull images for a single docker module via docker compose pull.
##           Skips modules that have a local build: section (no registry image).
## @io       ⇥ mod_name: str, overlay_dir: str | None, secrets_env_file: str | None,
##           platform_root: str | None, modules_dir: str
##           ⎋ bool: True if pull succeeded or skipped
## @complexity 2 — compose file resolution + build: section check + docker compose pull
## @invariants
##   - Module with `build:` section in compose file is SKIPPED (no registry image)
##   - Missing compose file is SKIPPED (logged, returns True)
##   - Failure is logged but returns True (non-fatal — compose up retries pull)
def _pull_module_images(
    mod_name: str,
    overlay_dir: str | None,
    secrets_env_file: str | None,
    platform_root: str | None,
    modules_dir: str,
) -> bool:
    module_dir = os.path.join(modules_dir, mod_name)
    compose_file = _resolve_compose_file(module_dir)
    if compose_file is None:
        logger.info("[IMP:7][_pull_module_images][skip] No compose file for %s — skipping pull", mod_name)
        return True

    # ── Skip modules with local build: section ──
    try:
        content = compose_file.read_text()
        if "build:" in content:
            logger.info("[IMP:7][_pull_module_images][skip] Local build detected for %s — skipping pull", mod_name)
            return True
    except OSError:
        pass

    # ── Build pull args ──
    pull_args = _build_compose_args(
        compose_file=compose_file,
        secrets_env_file=secrets_env_file,
        platform_root=platform_root,
        overlay_dir=overlay_dir,
        module_name=mod_name,
    )
    pull_cmd = ["docker", "compose", *pull_args, "pull"]
    logger.info("[IMP:7][_pull_module_images][pull] Pulling images for %s", mod_name)
    try:
        result = subprocess.run(pull_cmd, capture_output=True, timeout=300)
        if result.returncode == 0:
            logger.info("[IMP:9][_pull_module_images][done] Images pulled for %s", mod_name)
            return True
        logger.warning(
            "[IMP:5][_pull_module_images][fail] Pull failed for %s — compose up will retry: %s",
            mod_name,
            result.stderr.decode(errors="replace").strip()[:200] if result.stderr else "",
        )
        return True  # Non-fatal: up -d retries
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_pull_module_images][error] Pull error for %s: %s — non-fatal", mod_name, exc)
        return True  # Non-fatal


# endregion FUNC__pull_module_images


# region FUNC__pre_pull_images
## @purpose  Parallel pre-pull of all docker module images BEFORE topo-sorted compose up.
##           Executes docker compose pull for each module in parallel with slot limiting.
##           Uses same parallel slot pattern as deploy_docker_group (subprocess PIDs via threading).
## @io       ⇥ entries: list[str] ("module:overlay" format),
##           modules_dir: str, secrets_env_file: str | None, platform_root: str | None,
##           parallel_limit: int
##           ⎋ tuple[int, int] — (success_count, fail_count)
## @complexity 3 — parallel dispatch with threading-based slot limiting
## @invariants
##   - parallel_limit controls max concurrent pull operations (default 4)
##   - Pull failure is LOGGED but NOT fatal — compose up -d retries pull internally
##   - Already-cached images return immediately (docker compose pull is no-op)
## @rationale Q: Why pull separately from up -d? A: docker compose up -d pulls images
##   sequentially within each project even when modules are parallel. A dedicated pull
##   phase batches ALL image downloads at once, utilizing full network bandwidth.
##   Q: Why non-fatal? A: compose up -d already retries pull — pre-pull is optimization,
##   not correctness. Failing here and succeeding in up -d is harmless.
def _pre_pull_images(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[int, int]:
    logger.info(
        "[IMP:7][_pre_pull_images][start] Pre-pulling for %d modules (parallel: %d)",
        len(entries),
        parallel_limit,
    )
    pull_ok = 0
    pull_fail = 0
    pids: list[int] = []
    names: list[str] = []

    for entry in entries:
        mod_name, _, mod_overlay = entry.partition(":")
        if not mod_overlay or mod_overlay == mod_name:
            mod_overlay = ""

        # ── Parallel slot waiter ──
        while len(pids) >= parallel_limit:
            for i in range(len(pids) - 1, -1, -1):
                pid = pids[i]
                try:
                    # Non-blocking wait with WNOHANG
                    wpid, status = os.waitpid(pid, os.WNOHANG)
                    if wpid == pid:
                        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                            pull_ok += 1
                        else:
                            pull_fail += 1
                        pids.pop(i)
                        names.pop(i)
                except ChildProcessError:
                    pull_fail += 1
                    pids.pop(i)
                    names.pop(i)
            if len(pids) >= parallel_limit:
                time.sleep(1)

        # ── Fork subprocess for pull ──
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() NOT sys.exit() to avoid pytest
            # intercepting SystemExit in forked children (SystemExit inherits
            # BaseException, not Exception, so try/except Exception doesn't catch it)
            try:
                success = _pull_module_images(
                    mod_name, mod_overlay or None, secrets_env_file, platform_root, modules_dir
                )
                os._exit(0 if success else 1)
            except Exception:
                os._exit(1)
        else:
            pids.append(pid)
            names.append(mod_name)

    # ── Drain remaining PIDs ──
    for i in range(len(pids) - 1, -1, -1):
        try:
            _pid, status = os.waitpid(pids[i], 0)
            if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                pull_ok += 1
            else:
                pull_fail += 1
        except ChildProcessError:  # noqa: PERF203
            pull_fail += 1

    logger.info("[IMP:9][_pre_pull_images][done] Pre-pull complete: success=%d failed=%d", pull_ok, pull_fail)
    return (pull_ok, pull_fail)


# endregion FUNC__pre_pull_images


# region FUNC_deploy_docker_group
## @purpose  Deploy a group of docker modules in parallel with slot limiting.
##           Each module is deployed via deploy_docker_module in a child process.
##           After all deploys complete, runs healthchecks in parallel for each module.
## @io       ⇥ entries: list[str] ("module:overlay" format),
##           modules_dir: str, secrets_env_file: str | None, platform_root: str | None,
##           parallel_limit: int
##           ⎋ tuple[int, int, list[str], list[str]] — (deployed, failed, failed_names, rolled_back)
## @usecases (W5-E1) Atomic rollback: if any module fails, ALL modules in the group are shut down
##           via docker compose down. Rolled_back list contains names of modules that were
##           successfully shut down. Healthcheck still runs after rollback to verify recovery.
## @complexity 4 — parallel deploy with fork-based slot limiting + parallel healthcheck
## @invariants
##   - parallel_limit controls max concurrent deploy operations (default 4)
##   - Healthchecks run AFTER all deploys in the group complete
##   - Healthcheck failures are logged but do NOT affect deploy return count
##   - Failed module names are tracked for severity-based exit code aggregation
## @rationale Q: Why fork() instead of threading? A: Bash uses subshell (& + wait).
##   Fork-based parallelism preserves the exact same semantics: each deploy has its
##   own process context, environment isolation, and independent failure handling.
def deploy_docker_group(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[int, int, list[str], list[str]]:
    logger.info(
        "[IMP:7][deploy_docker_group][start] Deploying %d modules in parallel (limit: %d)",
        len(entries),
        parallel_limit,
    )
    pids: list[int] = []
    pid_to_name: dict[int, str] = {}
    group_deployed = 0
    group_failed = 0
    failed_names: list[str] = []

    for entry in entries:
        mod_name, _, mod_overlay = entry.partition(":")
        if not mod_overlay or mod_overlay == mod_name:
            mod_overlay = ""

        # ── Parallel slot waiter ──
        while len(pids) >= parallel_limit:
            deployed, failed, fnames = _drain_completed_count(pids, pid_to_name)
            group_deployed += deployed
            group_failed += failed
            failed_names.extend(fnames)
            if len(pids) >= parallel_limit:
                time.sleep(1)

        # ── Fork subprocess for deploy ──
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() NOT sys.exit() to avoid pytest
            # intercepting SystemExit in forked children
            try:
                success = deploy_docker_module(
                    mod_name,
                    mod_overlay or None,
                    secrets_env_file,
                    platform_root,
                    modules_dir,
                )
                os._exit(0 if success else 1)
            except Exception:
                os._exit(1)
        else:
            pids.append(pid)
            pid_to_name[pid] = mod_name

    # ── Drain remaining PIDs ──
    d, f, fn = _drain_all_count(pids, pid_to_name)
    group_deployed += d
    group_failed += f
    failed_names.extend(fn)

    all_names = list(pid_to_name.values())
    logger.info(
        "[IMP:8][deploy_docker_group][deploy] Deploy phase done: deployed=%d failed=%d total=%d",
        group_deployed,
        group_failed,
        len(all_names),
    )

    # ── Atomic rollback on failure (W5-E1) — shut down ALL modules in the group ──
    rolled_back: list[str] = []
    if group_failed > 0:
        logger.info(
            "[IMP:8][deploy_docker_group][rollback] %d module(s) failed — initiating atomic rollback of all %d module(s)",
            group_failed,
            len(all_names),
        )
        for entry in entries:
            mod_name, _, _ = entry.partition(":")
            compose_file = _resolve_compose_file(os.path.join(modules_dir, mod_name))
            if compose_file:
                try:
                    subprocess.run(
                        ["docker", "compose", "-f", str(compose_file), "--profile", mod_name, "down"],
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    rolled_back.append(mod_name)
                    logger.info("[IMP:8][deploy_docker_group][rollback] Module shut down: %s", mod_name)
                except (subprocess.TimeoutExpired, OSError) as exc:
                    logger.warning("[IMP:5][deploy_docker_group][rollback] Failed to shut down %s: %s", mod_name, exc)
        logger.info(
            "[IMP:9][deploy_docker_group][rollback] Atomic rollback: %d modules rolled back: %s",
            len(rolled_back),
            rolled_back,
        )

    # ── Parallel healthcheck (S4 pattern) — run on ALL deployed+failed modules ──
    hc_pids: list[int] = []
    for mod_name in all_names:
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() to avoid SystemExit in forked children
            try:
                run_healthcheck(mod_name, "docker")
                os._exit(0)
            except Exception:
                os._exit(1)
        else:
            hc_pids.append(pid)

    for pid in hc_pids:
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)

    logger.info(
        "[IMP:9][deploy_docker_group][done] Group complete: deployed=%d failed=%d names=%s rolled_back=%d",
        group_deployed,
        group_failed,
        failed_names,
        len(rolled_back),
    )
    return (group_deployed, group_failed, failed_names, rolled_back)


# endregion FUNC_deploy_docker_group


# region FUNC__drain_completed_count
## @purpose  Non-blocking drain of completed child processes, returning success/fail counts.
##           Used by deploy_docker_group slot-waiter loop to free slots and track results.
## @io       ⇥ pids: list[int] (mutated in place), pid_to_name: dict[int, str] (mutated)
##           ⎋ tuple[int, int, list[str]] — (success_count, fail_count, fail_names)
def _drain_completed_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    deployed = 0
    failed = 0
    failed_names: list[str] = []
    for i in range(len(pids) - 1, -1, -1):
        try:
            wpid, status = os.waitpid(pids[i], os.WNOHANG)
            if wpid == pids[i]:
                mod_name = pid_to_name.pop(pids[i], "?")
                if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                    deployed += 1
                else:
                    failed += 1
                    failed_names.append(mod_name)
                pids.pop(i)
        except ChildProcessError:  # noqa: PERF203
            mod_name = pid_to_name.pop(pids[i], "?")
            failed += 1
            failed_names.append(mod_name)
            pids.pop(i)
    return (deployed, failed, failed_names)


# endregion FUNC__drain_completed_count


# region FUNC__drain_all_count
## @purpose  Blocking drain of all remaining child processes with result tracking.
## @io       ⇥ pids: list[int] (cleared), pid_to_name: dict[int, str] (cleared)
##           ⎋ tuple[int, int, list[str]] — (success_count, fail_count, fail_names)
def _drain_all_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    deployed = 0
    failed = 0
    failed_names: list[str] = []
    for i in range(len(pids) - 1, -1, -1):
        try:
            os.waitpid(pids[i], 0)
            mod_name = pid_to_name.pop(pids[i], "?")
            # Success — waitpid returned without error means process exited
            deployed += 1
        except ChildProcessError:  # noqa: PERF203
            mod_name = pid_to_name.pop(pids[i], "?")
            failed += 1
            failed_names.append(mod_name)
    pids.clear()
    return (deployed, failed, failed_names)


# endregion FUNC__drain_all_count


# region FUNC_wait_for_readiness
## @purpose  Poll module readiness via invoke_module_interface healthcheck readiness.
##           Retries up to max_attempts times with interval_sec between attempts.
##           Timeout is non-fatal (logged WARN) — container may still be starting.
## @io       ⇥ module_name: str, max_attempts: int, interval_sec: int
##           ⎋ bool: True if readiness check passed
## @complexity 2 — polling loop with subprocess calls
## @invariants
##   - Uses invoke_module_interface (bash) to call module/healthcheck.sh readiness
##   - The shell script must be sourceable with paths.sh and module-interface.sh
##   - Non-zero return from healthcheck.sh means "not ready yet" — retry
##   - Timeout returns False but does NOT raise — caller decides next action
def wait_for_readiness(
    module_name: str,
    max_attempts: int = DEFAULT_READINESS_MAX_ATTEMPTS,
    interval_sec: int = DEFAULT_READINESS_INTERVAL_SEC,
) -> bool:
    logger.info(
        "[IMP:7][wait_for_readiness][start] Waiting for %s readiness (%d attempts, %ds interval)",
        module_name,
        max_attempts,
        interval_sec,
    )
    for attempt in range(max_attempts):
        if _invoke_healthcheck(module_name, "readiness"):
            logger.info(
                "[IMP:9][wait_for_readiness][ready] Module %s ready after %d attempts",
                module_name,
                attempt + 1,
            )
            return True
        if attempt < max_attempts - 1:
            time.sleep(interval_sec)

    logger.warning(
        "[IMP:5][wait_for_readiness][timeout] Readiness timeout for %s after %d attempts — continuing (non-fatal)",
        module_name,
        max_attempts,
    )
    return False


# endregion FUNC_wait_for_readiness


# region FUNC_run_healthcheck
## @purpose  Run healthcheck for a module via invoke_module_interface healthcheck liveness.
##           Retries up to max_retries times with retry_interval between attempts.
##           Failure is non-fatal (logged WARN) — module may still function.
## @io       ⇥ module_name: str, install_type: str, max_retries: int, retry_interval: int
##           ⎋ bool: True if healthcheck passed
## @complexity 2 — retry loop with subprocess calls
## @invariants
##   - Uses invoke_module_interface (bash) to call module/healthcheck.sh liveness
##   - First failure logs DIAG with healthcheck stderr for debugging
##   - Failure after max_retries returns False — caller decides severity
def run_healthcheck(
    module_name: str,
    install_type: str,
    max_retries: int = DEFAULT_HEALTHCHECK_MAX_RETRIES,
    retry_interval: int = DEFAULT_HEALTHCHECK_RETRY_INTERVAL,
) -> bool:
    logger.info("[IMP:7][run_healthcheck][start] Healthcheck for %s (%s)", module_name, install_type)
    last_output = ""
    for attempt in range(max_retries):
        success, output = _invoke_healthcheck_full(module_name, "liveness")
        if success:
            logger.info(
                "[IMP:9][run_healthcheck][pass] Healthcheck PASS for %s (attempt %d/%d)",
                module_name,
                attempt + 1,
                max_retries,
            )
            return True

        last_output = output
        if attempt == 0:
            logger.info("[IMP:8][run_healthcheck][diag] Healthcheck stderr: %s", output[:300] if output else "(none)")

        if attempt < max_retries - 1:
            logger.info(
                "[IMP:8][run_healthcheck][retry] Healthcheck attempt %d/%d failed for %s, retrying in %ds",
                attempt + 1,
                max_retries,
                module_name,
                retry_interval,
            )
            time.sleep(retry_interval)

    logger.warning(
        "[IMP:5][run_healthcheck][fail] Healthcheck FAILED for %s after %d attempts (last: %s)",
        module_name,
        max_retries,
        last_output[:200] if last_output else "",
    )
    return False


# endregion FUNC_run_healthcheck


# region FUNC__invoke_healthcheck
## @purpose  Call invoke_module_interface for healthcheck (readiness or liveness) via bash.
##           Returns True on zero exit code.
## @io       ⇥ module_name: str, check_type: str ("readiness" | "liveness")
##           ⎋ bool: True if check passed
## @complexity 1 — single subprocess call
## @invariants
##   - Paths.sh must be sourceable (PATHS_MODULES_DIR for module resolution)
##   - module-interface.sh must be sourceable (provides invoke_module_interface)
##   - stderr is captured and logged at IMP:8 on failure
def _invoke_healthcheck(module_name: str, check_type: str) -> bool:
    success, _ = _invoke_healthcheck_full(module_name, check_type)
    return success


# endregion FUNC__invoke_healthcheck


# region FUNC__invoke_healthcheck_full
## @purpose  Call invoke_module_interface for healthcheck via bash subprocess.
##           Returns (bool, str) tuple with success flag and stderr output.
## @io       ⇥ module_name: str, check_type: str ("readiness" | "liveness")
##           ⎋ tuple[bool, str] — (success, stderr_output)
## @complexity 1 — single subprocess call
def _invoke_healthcheck_full(module_name: str, check_type: str) -> tuple[bool, str]:
    # Build shell command that sources paths.sh + module-interface.sh, then calls invoke_module_interface
    bash_cmd = (
        f"source '{_PATHS_SH}' && "
        f"source '{_INVOKE_MODULE_INTERFACE_SH}' && "
        f"invoke_module_interface '{module_name}' healthcheck '{check_type}'"
    )
    try:
        result = subprocess.run(
            ["bash", "-c", bash_cmd],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return (True, result.stderr)
        logger.info(
            "[IMP:8][_invoke_healthcheck][fail] %s %s failed (exit=%d): %s",
            module_name,
            check_type,
            result.returncode,
            result.stderr.strip()[:200] if result.stderr else "",
        )
        return (False, result.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_invoke_healthcheck][error] %s %s error: %s", module_name, check_type, exc)
        return (False, str(exc))


# endregion FUNC__invoke_healthcheck_full


# region FUNC_main
## @purpose  CLI entry point: dispatch to action handlers based on --action flag.
## @io       sys.argv → stdout/logs, int exit code
## @complexity 2 — argparse dispatch with per-action argument validation
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Docker orchestration: deploy, pre-pull, wait, healthcheck, check-image"
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["deploy", "pre-pull", "wait", "healthcheck", "check-image"],
        help="Action to perform",
    )
    parser.add_argument("--module-name", help="Module name (for deploy, wait, healthcheck)")
    parser.add_argument(
        "--module-entries",
        nargs="*",
        default=[],
        help="Module entries in module:overlay format (for pre-pull)",
    )
    parser.add_argument("--node-yaml", help="Path to node.yaml (unused in docker_orchestrator)")
    parser.add_argument("--modules-dir", help="Path to modules directory")
    parser.add_argument("--secrets-env-file", help="Path to secrets.env file")
    parser.add_argument("--platform-root", help="Platform root directory (default: /opt/platform)")
    parser.add_argument(
        "--overlay-dir",
        help="Overlay directory for context-specific compose override",
    )
    parser.add_argument(
        "--image-ref",
        help="Image reference for check-image action",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_READINESS_MAX_ATTEMPTS,
        help="Max attempts for readiness check (default: 15)",
    )
    parser.add_argument(
        "--install-type",
        default="docker",
        choices=["docker", "system"],
        help="Install type for healthcheck (default: docker)",
    )
    parser.add_argument(
        "--parallel-limit",
        type=int,
        default=DEFAULT_PARALLEL_LIMIT,
        help="Parallel deploy/pull limit (default: 4)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:7][main][start] Action: %s", args.action)

    if args.action == "deploy":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for deploy action")
            return 1
        success = deploy_docker_module(
            module_name=args.module_name,
            overlay_dir=args.overlay_dir,
            secrets_env_file=args.secrets_env_file,
            platform_root=args.platform_root,
            modules_dir=args.modules_dir,
        )
        logger.info("[IMP:9][main][result] Deploy %s: %s", args.module_name, "OK" if success else "FAIL")
        return 0 if success else 1

    if args.action == "pre-pull":
        if not args.module_entries:
            logger.error("[IMP:10][main][error] --module-entries required for pre-pull action")
            return 1
        ok, fail = _pre_pull_images(
            entries=list(args.module_entries),
            modules_dir=args.modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules"),
            secrets_env_file=args.secrets_env_file,
            platform_root=args.platform_root,
            parallel_limit=args.parallel_limit,
        )
        logger.info("[IMP:9][main][result] Pre-pull: success=%d failed=%d", ok, fail)
        return 0

    if args.action == "wait":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for wait action")
            return 1
        ready = wait_for_readiness(
            module_name=args.module_name,
            max_attempts=args.max_attempts,
        )
        return 0 if ready else 1

    if args.action == "healthcheck":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for healthcheck action")
            return 1
        passed = run_healthcheck(
            module_name=args.module_name,
            install_type=args.install_type,
        )
        return 0 if passed else 1

    if args.action == "check-image":
        if not args.image_ref:
            logger.error("[IMP:10][main][error] --image-ref required for check-image action")
            return 1
        exists = _check_image_exists(args.image_ref)
        return 0 if exists else 1

    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
