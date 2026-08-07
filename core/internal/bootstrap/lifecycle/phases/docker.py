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
from core.internal.shared.docker_compose import nginx_reload as shared_docker_compose_nginx_reload
from core.internal.shared.timeouts import APT_TIMEOUT, DEPLOY_TIMEOUT


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
                # T9.15 (B-12): deploy 14+ модулей может занять >300с (pull + build + healthcheck
                # per module) — канон DEPLOY_TIMEOUT (600) вместо legacy 300.
                timeout=DEPLOY_TIMEOUT,
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
    # 142 W6 (A2): re-apply конфига privoxy в update-режиме (no-op при корректном конфиге) —
    # после reboot/переустановки пакета privoxy сбрасывался к 127.0.0.1 → telegram-канал мёртв.
    non_fatal_issues |= _registry_step_privoxy_config(core_dir)
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
## @purpose  E3 sub-step 3 (nginx overlays): reload nginx ТОЛЬКО при изменении содержимого
##           overlay-директории (T9.14, B-10). Хэш ВСЕГО содержимого dir (относительные пути +
##           содержимое файлов — deletions меняют набор путей) сравнивается с маркером
##           PLATFORM_STATE_DIR/.nginx-overlay-{node}.hash → reload при mismatch.
## @io       ⇥ node_name: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(N) где N = overlay файлы (хэш) + 1 reload
## @invariants
##   - Overlay-директории нет → skip (не issue)
##   - Хэш учитывает ВСЕ содержимое dir ВКЛЮЧАЯ пустоту: удаление ВСЕХ .conf инвалидирует
##     хэш → reload применяет удаление vhost'ов (раньше early-return «no .conf» маскировал)
##   - Первый запуск с пустым overlay → базовый маркер, reload НЕ выполняется
##   - Содержимое не изменилось → НЕТ reload (no-op; раньше reload был на каждый φ11)
##   - Сбой reload → WARN + True (best-effort)
def _registry_step_nginx_overlays(node_name: str) -> bool:
    """Deliver nginx overlays + reload sub-step (content-hash gated, T9.14)."""
    overlay_dir = str(deploy_paths.node_configs_remote() / node_name / "overlays" / "nginx")
    if not os.path.isdir(overlay_dir):
        logger.info("[IMP:7][phase:registry_update] No overlay directory at %s — skipping", overlay_dir)
        return False

    # ── T9.14: content-hash ВСЕГО содержимого dir (пути + содержимое) — deletions инвалидируют ──
    import hashlib

    hasher = hashlib.sha256()
    try:
        for rel in sorted(os.listdir(overlay_dir)):
            full = os.path.join(overlay_dir, rel)
            hasher.update(rel.encode("utf-8"))
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    hasher.update(f.read())
            hasher.update(b"\0")
    except OSError as e:
        logger.warning("[IMP:7][phase:registry_update] Cannot hash overlay dir %s (non-fatal): %s", overlay_dir, e)
        return True
    dir_hash = hasher.hexdigest()

    marker = os.path.join(
        os.environ.get("PLATFORM_STATE_DIR", "/var/lib/platform/.bootstrap"), f".nginx-overlay-{node_name}.hash"
    )
    marker_exists = os.path.isfile(marker)
    previous = ""
    if marker_exists:
        try:
            with open(marker) as f:
                previous = f.read().strip()
        except OSError as e:
            logger.warning("[IMP:7][phase:registry_update] Cannot read overlay hash marker (non-fatal): %s", e)

    # ⚠️ TRAP[BUG] · 2026-08-05 · P1 · удаление ВСЕХ .conf не применялось (B-10, T9.14)
    # · Symptom: .conf удалён из overlay → early-return «No .conf files» срабатывал ДО hash-проверки
    # ·   → nginx продолжал обслуживать удалённый vhost; reload не выполнялся.
    # · Root: пустая директория считалась «нет overlay» (skip) — deletions невидимы.
    # · Fix: hash по ВСЕМУ содержимому dir (включая пустоту); reload при mismatch ИЗМЕНЁННОГО
    # ·   состояния (marker_exists); первый запуск с пустым overlay — только базовый маркер.
    # · Prevention: hash-гейт не должен иметь early-return до сравнения с маркером.
    if previous == dir_hash:
        logger.info(
            "[IMP:8][phase:registry_update] Nginx overlay %s unchanged (hash %s) — reload skipped (T9.14)",
            node_name,
            dir_hash[:12],
        )
        return False

    # Первый запуск с ПУСТЫМ overlay: нечего применять — фиксируем базовый маркер, без reload
    if not marker_exists and not os.listdir(overlay_dir):
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write(dir_hash)
        except OSError as e:
            logger.warning("[IMP:7][phase:registry_update] Cannot write overlay hash marker (non-fatal): %s", e)
        logger.info("[IMP:8][phase:registry_update] Empty overlay baseline recorded for %s — no reload", node_name)
        return False

    logger.info(
        "[IMP:8][phase:registry_update] Overlay %s changed (hash %s → %s) — reloading nginx",
        node_name,
        (previous or "-")[:12],
        dir_hash[:12],
    )
    try:
        # W1 (DevPlan 128): docker exec — shared docker_compose.nginx_reload → docker_ops.docker_exec
        # (non-fatal фасад; best-effort семантика сохраняется)
        shared_docker_compose_nginx_reload("nginx")
        # ── Обновить маркер ТОЛЬКО после успешного reload ──
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write(dir_hash)
        except OSError as e:
            logger.warning("[IMP:7][phase:registry_update] Cannot write overlay hash marker (non-fatal): %s", e)
        logger.info("[IMP:9][phase:registry_update] Nginx reloaded with overlays")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Nginx reload failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_nginx_overlays


# region FUNC__registry_step_privoxy_config
## @purpose  142 W6 (A2) sub-step: re-apply конфига privoxy в update-режиме. Privoxy после
##           reboot/переустановки пакета сбрасывался к listen-address 127.0.0.1 → grafana
##           telegram-канал (host.docker.internal:8118) мёртв (цикл 2 141, R1-корень №1).
##           Механизм 119 D3 идемпотентен: write_privoxy_config → no-op при корректном конфиге.
## @io       ⇥ core_dir: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(L) — L = строк конфига (no-op при совпадении)
## @invariants
##   - ТОЛЬКО при TOR_ENABLED=true (privoxy не установлен иначе)
##   - Несуществующий /etc/privoxy/config → no-op (не issue — privoxy может быть не установлен)
##   - write_privoxy_config True (изменения внесены) → WARN (дрейф был — теперь исправлен)
def _registry_step_privoxy_config(core_dir: str) -> bool:
    """Re-apply privoxy config (idempotent, 119 D3) — no-op при корректном конфиге (142 W6)."""
    if os.environ.get("TOR_ENABLED", "false").lower() != "true":
        logger.info("[IMP:7][phase:registry_update] TOR_ENABLED != true — skipping privoxy config re-apply")
        return False
    try:
        from core.internal.bootstrap.privoxy_config import write_privoxy_config

        changed = write_privoxy_config("/etc/privoxy/config")
        if changed:
            # Дрейф был (конфиг сброшен к дефолту пакета) — перезаписан каноном; WARN-сигнал
            logger.warning(
                "[IMP:8][phase:registry_update] Privoxy config was drifted — re-applied canonical config (142 W6)"
            )
            return True
        logger.info("[IMP:9][phase:registry_update] Privoxy config already canonical — no-op")
        return False
    except Exception as e:  # noqa: EXC — non-fatal (best-effort, как firewall/tor в φ1)
        logger.warning("[IMP:7][phase:registry_update] Privoxy config re-apply failed (non-fatal): %s", e)
        return True


# endregion FUNC__registry_step_privoxy_config


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
##   - `.hc_done_in_deploy` + суффикс контекста (per-context, T9.19) → skip + unlink (не issue)
##   - node.yaml отсутствует → WARN + True
##   - Сбой healthchecks → WARN + True (best-effort)
def _registry_step_healthcheck(node_yaml: str) -> bool:
    """Standalone healthcheck sub-step (skip if already done in deploy)."""
    # T9.19 (B-11): маркер per-context (не node-global) — единый источник пути с писателем
    # (deploy_orchestrator._set_hc_marker → orchestrator_metrics.hc_marker_path). CONTEXT env
    # задаётся при деплое контекста; деплой context A не подавляет healthcheck context B.
    from core.internal.bootstrap.deploy.orchestrator_metrics import hc_marker_path as _hc_marker_path

    hc_done_marker = _hc_marker_path(os.environ.get("CONTEXT"))
    if os.path.isfile(hc_done_marker):
        logger.info(
            "[IMP:9][phase:registry_update] Healthcheck already done during deploy "
            "(DEPLOY_PARALLEL, marker %s) — skipping standalone healthcheck",
            hc_done_marker,
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
                # T9.15 (B-12): канон DEPLOY_TIMEOUT (600) вместо legacy 300 (см. φ8)
                timeout=DEPLOY_TIMEOUT,
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

    # ── 4. Apply unattended-upgrades security policy (DevPlan 134 L1) ──
    # Пропагация политики при node-update: ensure() идемпотентен (content-match no-op) —
    # на актуальной ноде = 0 записей на диск, на устаревшей — применяет security-конфиг.
    # Non-fatal (best-effort): policy-сбой не должен ронять update-цикл.
    security_script = os.path.join(core_dir, "internal", "bootstrap", "security_updates.py")
    if os.path.isfile(security_script):
        auto_reboot = os.environ.get("SECURITY_AUTO_REBOOT", "true").lower() == "true"
        try:
            helpers_subprocess.run_subprocess(
                ["python3", security_script, "--auto-reboot", "true" if auto_reboot else "false"],
                non_fatal=True,
                fatal_rc=(127,),
                timeout=APT_TIMEOUT,
            )
            logger.info("[IMP:9][phase:deploy_update] Unattended-upgrades security policy applied")
        except Exception as e:  # noqa: EXC — non-fatal: security updates are best-effort
            logger.warning("[IMP:7][phase:deploy_update] Security updates setup failed (non-fatal): %s", e)
            non_fatal_issues = True
    else:
        logger.warning("[IMP:7][phase:deploy_update] security_updates.py not found at %s — skipping", security_script)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_update] φ12 complete — services and SSL deployed")
    return True


# endregion FUNC_phase_deploy_update
