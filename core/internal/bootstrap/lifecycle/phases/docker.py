#!/usr/bin/env python3
# GREP_SUMMARY: phases-docker, registry-auth, deploy-services, registry-update, deploy-update, bootstrap-phase, ghcr, deploy-modules, E3
# STRUCTURE: ▶ docker-фазы (φ6 φ8 φ11 φ12) → ◇ each: pre-check → execute → post-check → ⊕ LDD logs → ⎋ bool/exception
# region MODULE_CONTRACT
## @purpose  Docker/registry-domain bootstrap phases (DevPlan 119 E3) — φ6 registry_auth, φ8
##           deploy_services, φ11 registry_update, φ12 deploy_update. Интерфейс
##           (core_dir, node_name, node_yaml) -> bool сохранён.
## @scope    Consumed by lifecycle/phases/__init__.py (агрегатор) → state_machine.py execute_phase.
##           Извлечено из lifecycle/phases.py (DevPlan 119 E3, AUDIT-2 M3).
## @invariants
##   1. Every phase is idempotent — safe to re-run on a provisioned node.
##   2. Non-fatal failures log WARN and return False — do NOT raise.
##   3. Fatal failures raise PlatformFatalError.
##   4. All subprocess calls use helpers_subprocess.run_subprocess() (B4 единый канон).
##   5. No direct state mutation — phases do NOT write state.json.
## @rationale E3: phases.py 1080 LOC → доменные модули. docker-фазы — registry/deploy-домен
##           (ghcr-auth, deploy-modules, overlays, llm-keys, healthcheck).
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared import deploy_paths, llm_paths
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains
from core.internal.bootstrap.lifecycle.helpers import reporting as helpers_reporting
from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.shared import (
    subprocess_io as helpers_subprocess,  # B4: единый канон (копия lifecycle/helpers удалена)
)


# region FUNC_phase_registry_auth
## @purpose φ6: Container registry authentication — GHCR (GitHub Container Registry) login
##           for image pulls. Docker Hub auth выполняется ТОЛЬКО в φ3 (docker_registry_auth.py,
##           ранний этап до pull) — дубль вызова убран (волна 117 D2).
##           Corresponds to init steps: ghcr_auth (16).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - GHCR auth uses GHCR_PULL_TOKEN env var — skip if not set
##   - GHCR auth is non-fatal (best-effort)
##   - Docker Hub auth НЕ выполняется в φ6 (единственная точка — φ3 phase_platform_setup, D2)
def phase_registry_auth(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ6: Registry auth — GHCR login.

    Pre-check: None (auth is best-effort, no hard precondition).
    Execute: GHCR auth.
    Post-check: registry credentials configured (best-effort).
    """
    non_fatal_issues = False

    # ── 1. GHCR auth ──
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][phase:registry_auth] GHCR_PULL_TOKEN not set — skipping ghcr auth")
    else:
        try:
            helpers_system.ghcr_auth()
            logger.info("[IMP:9][phase:registry_auth] GHCR auth successful")
        except Exception as e:  # noqa: EXC — non-fatal: ghcr auth is best-effort
            logger.warning("[IMP:7][phase:registry_auth] GHCR auth failed (non-fatal): %s", e)
            non_fatal_issues = True

    # ── 2. Docker Hub auth — ТОЛЬКО в φ3 (волна 117 D2) ──
    # docker_registry_auth.py выполняется единственный раз за init в phase_platform_setup (φ3,
    # ранний этап до pull). Повторный вызов здесь удалён — он давал 2-й systemctl restart docker.
    logger.info(
        "[IMP:7][phase:registry_auth] Docker Hub auth handled in φ3 (docker_registry_auth.py) — skipped in φ6 (D2)"
    )

    if non_fatal_issues:
        logger.info("[IMP:8][phase:registry_auth] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:registry_auth] φ6 complete — registry auth configured")
    return True


# endregion FUNC_phase_registry_auth


# region FUNC_phase_deploy_services
## @purpose φ8: Deploy platform services — run deploy-modules.sh for docker/system modules,
##           then deploy context projects via context_deployer. Corresponds to init steps:
##           node_update (19 — partial, deploy-modules), deploy_context (23).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * D) where M = modules, D = deploy operations per module
## @invariants
##   - deploy-modules.sh is FATAL (core service deployment)
##   - deploy_context is non-fatal (projects are best-effort)
##   - deploy-modules.sh is called with --skip-provision (provision done in platform_setup)
def phase_deploy_services(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ8: Deploy services — modules + context projects.

    Pre-check: node.yaml exists, core_dir exists.
    Execute: deploy-modules.sh (docker + system) → deploy context projects.
    Post-check: subprocess exit codes.
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot deploy services")
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. Deploy modules (docker + system) via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--skip-provision"],
                timeout=300,
                check=True,
            )
            logger.info("[IMP:9][phase:deploy_services] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_services] Module deployment failed: %s", e)
            raise PlatformFatalError(f"Module deployment failed: {e}") from e
    else:
        logger.warning("[IMP:7][phase:deploy_services] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. Deploy context projects ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_services] Context projects deployed")
    except Exception as e:  # noqa: EXC — non-fatal: context deploy is best-effort
        logger.warning("[IMP:7][phase:deploy_services] Context deploy failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_services] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_services] φ8 complete — all services deployed")
    return True


# endregion FUNC_phase_deploy_services


# region FUNC_phase_registry_update
## @purpose φ11: Registry update (UPDATE mode) — GHCR auth, provision environment (networks +
##            volumes), deliver nginx overlays, provision LLM keys, run healthchecks.
##            Corresponds to update steps: provision (2), deliver_overlays (2.5/3),
##            provision_llm_keys (6), healthcheck (7).
##            DevPlan 119 E3: registry-логика декомпозирована на 5 sub-step-хелперов
##            (_registry_step_*) — CC 23 → ≤10 (AC-E3.3).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * R + N) where M = modules in healthcheck, R = retries, N = overlay files
## @invariants
##   - GHCR auth is best-effort (no token = skip)
##   - Environment provision (networks + volumes) is non-fatal (may already exist)
##   - Nginx overlay reload is non-fatal (nginx may not be running)
##   - LLM key provisioning is non-fatal (optional component)
##   - Healthcheck is STANDALONE (skipped if .hc_done_in_deploy marker present)
##   - Каждый sub-step возвращает True при non-fatal issue (агрегация в оркестраторе)
def phase_registry_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ11: Registry and services update — GHCR auth, provision, overlays, LLM, healthcheck (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: GHCR auth → provision env → deliver overlays → LLM keys → healthcheck.
    Post-check: healthcheck results (best-effort, warnings collected).
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False
    # E3: каждый шаг — отдельный хелпер (CC 23 → ≤10). Порядок сохранён (legacy parity).
    non_fatal_issues |= _registry_step_ghcr_auth()
    non_fatal_issues |= _registry_step_provision_env(core_dir)
    non_fatal_issues |= _registry_step_nginx_overlays(node_name)
    non_fatal_issues |= _registry_step_llm_provision(core_dir)
    non_fatal_issues |= _registry_step_healthcheck(node_yaml)

    if non_fatal_issues:
        logger.info("[IMP:8][phase:registry_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:registry_update] φ11 complete — registry and services updated")
    return True


# endregion FUNC_phase_registry_update


# region FUNC__registry_step_ghcr_auth
## @purpose  E3 sub-step 1 (GHCR auth): best-effort docker login к GHCR при GHCR_PULL_TOKEN.
## @io       ⇥ None → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(1) — env check + ghcr_auth
## @invariants
##   - GHCR_PULL_TOKEN отсутствует → skip (не issue)
##   - Сбой ghcr_auth → WARN + True (best-effort)
def _registry_step_ghcr_auth() -> bool:
    """GHCR auth sub-step. Returns True if a non-fatal issue occurred."""
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][phase:registry_update] GHCR_PULL_TOKEN not set — skipping ghcr auth")
        return False
    try:
        helpers_system.ghcr_auth()
        logger.info("[IMP:9][phase:registry_update] GHCR auth successful")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] GHCR auth failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_ghcr_auth


# region FUNC__registry_step_provision_env
## @purpose  E3 sub-step 2 (provision env): networks + volumes через provision-environment.sh.
## @io       ⇥ core_dir: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(1) + subprocess
## @invariants
##   - Скрипт отсутствует → WARN + True (non-fatal)
##   - Сбой provision → WARN + True (best-effort)
def _registry_step_provision_env(core_dir: str) -> bool:
    """Provision environment (networks + volumes) sub-step."""
    provision_script = os.path.join(core_dir, "internal", "provision-environment.sh")
    if not os.path.isfile(provision_script):
        logger.warning(
            "[IMP:7][phase:registry_update] provision-environment.sh not found at %s — skipping",
            provision_script,
        )
        return True
    try:
        helpers_subprocess.run_subprocess(
            ["bash", provision_script, "--scope", "networks", "--scope", "volumes"],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=120,
        )
        logger.info("[IMP:9][phase:registry_update] Environment provisioned (networks + volumes)")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Environment provision failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_provision_env


# region FUNC__registry_step_nginx_overlays
## @purpose  E3 sub-step 3 (nginx overlays): reload nginx если есть *.conf в overlay-директории.
## @io       ⇥ node_name: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(N) where N = overlay .conf files
## @invariants
##   - Overlay-директории нет / .conf нет → skip (не issue)
##   - Сбой reload → WARN + True (best-effort)
def _registry_step_nginx_overlays(node_name: str) -> bool:
    """Deliver nginx overlays + reload sub-step."""
    overlay_dir = str(deploy_paths.node_configs_remote() / node_name / "overlays" / "nginx")
    if not os.path.isdir(overlay_dir):
        logger.info("[IMP:7][phase:registry_update] No overlay directory at %s — skipping", overlay_dir)
        return False
    conf_files = list(Path(overlay_dir).glob("*.conf"))
    if not conf_files:
        logger.info("[IMP:7][phase:registry_update] No .conf files in %s — skipping nginx reload", overlay_dir)
        return False
    logger.info(
        "[IMP:8][phase:registry_update] Found %d overlay(s) in %s — reloading nginx",
        len(conf_files),
        overlay_dir,
    )
    try:
        helpers_subprocess.run_subprocess(
            ["docker", "exec", "nginx", "nginx", "-s", "reload"],
            non_fatal=True,
            fatal_rc=(127,),
        )
        logger.info("[IMP:9][phase:registry_update] Nginx reloaded with overlays")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Nginx reload failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_nginx_overlays


# region FUNC__registry_step_llm_provision
## @purpose  E3 sub-step 4 (LLM keys): render litellm-config.yml + provision virtual keys.
## @io       ⇥ core_dir: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(render + provision)
## @invariants
##   - config_renderer.py отсутствует → skip (не issue)
##   - provision-llm.sh отсутствует → skip (не issue)
##   - Сбой любого шага → WARN + True (best-effort)
def _registry_step_llm_provision(core_dir: str) -> bool:
    """Provision LLM keys (render config + virtual keys) sub-step."""
    llm_dir = os.path.join(core_dir, "internal", "llm")
    renderer_script = os.path.join(llm_dir, "config_renderer.py")
    config_output = str(llm_paths.litellm_config_path(core_dir))  # C6: единый путь shared/llm_paths
    if not os.path.isfile(renderer_script):
        logger.info("[IMP:7][phase:registry_update] config_renderer.py not found — skipping LLM provision")
        return False
    try:
        helpers_subprocess.run_subprocess(
            ["python3", renderer_script, "--output", config_output],
            non_fatal=True,
            fatal_rc=(127,),
        )
        logger.info("[IMP:9][phase:registry_update] LiteLLM config rendered")

        provision_entrypoint = os.path.join(core_dir, "entrypoints", "provision-llm.sh")
        if not os.path.isfile(provision_entrypoint):
            logger.info(
                "[IMP:7][phase:registry_update] provision-llm.sh not found at %s — skipping LLM key provision",
                provision_entrypoint,
            )
            return False
        helpers_subprocess.run_subprocess(
            ["bash", provision_entrypoint],
            non_fatal=True,
            fatal_rc=(127,),
        )
        logger.info("[IMP:9][phase:registry_update] LLM virtual keys provisioned")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] LLM key provisioning failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_llm_provision


# region FUNC__registry_step_healthcheck
## @purpose  E3 sub-step 5 (healthcheck): standalone healthcheck, skip если маркер уже стоит.
## @io       ⇥ node_yaml: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(M * R) where M = modules, R = retries
## @invariants
##   - .hc_done_in_deploy маркер → skip + unlink (не issue)
##   - node.yaml отсутствует → WARN + True
##   - Сбой healthchecks → WARN + True (best-effort)
def _registry_step_healthcheck(node_yaml: str) -> bool:
    """Standalone healthcheck sub-step (skip if already done in deploy)."""
    hc_done_marker = "/var/lib/platform/.bootstrap/.hc_done_in_deploy"
    if os.path.isfile(hc_done_marker):
        logger.info(
            "[IMP:9][phase:registry_update] Healthcheck already done during deploy "
            "(DEPLOY_PARALLEL) — skipping standalone healthcheck"
        )
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(hc_done_marker)
        return False
    if not node_yaml or not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][phase:registry_update] node.yaml not found — skipping healthchecks")
        return True
    try:
        helpers_reporting.run_healthchecks(node_yaml)
        logger.info("[IMP:9][phase:registry_update] Healthchecks completed")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Healthchecks failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_healthcheck


# region FUNC_phase_deploy_update
## @purpose φ12: Deploy update (UPDATE mode) — deploy modules via deploy-modules.sh, provision
##            SSL certificates, deploy context projects incrementally.
##            Corresponds to update steps: ssl_provision (3/4), deploy_modules (4/5),
##            deploy_context (8).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(M * D + D_cert * T) where M = modules, D = deploy ops, D_cert = domains
## @invariants
##   - deploy-modules.sh is called with --skip-provision (provision done in registry_update)
##   - SSL provision is via cert_orchestrator (unified entrypoint)
##   - Context deploy is incremental (only changed projects)
def phase_deploy_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ12: Deploy update — modules, SSL, context (UPDATE mode).

    Pre-check: node.yaml exists, core_dir exists.
    Execute: deploy-modules.sh → SSL provision → deploy context.
    Post-check: all deployment operations completed.
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot deploy update")
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    non_fatal_issues = False

    # ── 1. Deploy modules via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--skip-provision"],
                timeout=300,
                check=True,
            )
            logger.info("[IMP:9][phase:deploy_update] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_update] Module deployment failed: %s", e)
            raise PlatformFatalError(f"Module deployment failed during update: {e}") from e
    else:
        logger.warning("[IMP:7][phase:deploy_update] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. SSL provision via cert_orchestrator ──
    try:
        helpers_domains.ssl_provision_via_orchestrator(core_dir, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] SSL certificates provisioned")
    except Exception as e:  # noqa: EXC — non-fatal: SSL is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:deploy_update] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Deploy context projects (incremental) ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] Context projects deployed incrementally")
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:deploy_update] Context deploy failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_update] φ12 complete — services and SSL deployed")
    return True


# endregion FUNC_phase_deploy_update
