#!/usr/bin/env python3
# GREP_SUMMARY: phases-docker, registry-auth, deploy-services, registry-update, deploy-update, bootstrap-phase, ghcr, deploy-modules, E3, T2.1, ghcr-auth-step, apply-policy-script, hc-done-marker, run-scope, REF-0005, stale-sweep
# STRUCTURE: ▶ docker-фазы (φ6 φ8 φ11 φ12) → ◇ each: pre-check → [φ8/φ12: _sweep_stale_hc_markers] → execute → post-check → ⊕ LDD logs → ⎋ bool/exception
#           → ◇ T2.1: GHCR-блок φ6/φ11 → общий _ghcr_auth_step; policy-блоки φ12 → _apply_policy_script
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
## @changes  2026-08-13 · DevPlan 160 E3 — _registry_step_firewall +runner/+env (DI)
## @changes  2026-08-22 · T2.1 — GHCR-блок φ6/φ11 → общий _ghcr_auth_step
##           (_registry_step_ghcr_auth удалён — дубль); policy-блоки φ12 → _apply_policy_script
## @changes  2026-08-24 · REF-0005 (DevPlan 11 W0) — run-scoped hc_done: читатель находит
##           `base`[.`ctx`].`run-id` формы; свип stale-маркеров на старте φ8/φ12 (unlink-on-init)
# endregion MODULE_CONTRACT
from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared import deploy_paths, llm_paths
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)

# DR-H1 fix: peer-firewall --placement args (DevPlan 010 T2.3 wiring)
from core.internal.shared.placement import firewall_placement_args

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
import pathlib

from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains
from core.internal.bootstrap.lifecycle.helpers import reporting as helpers_reporting
from core.internal.bootstrap.lifecycle.helpers import system as helpers_system
from core.internal.shared import (
    subprocess_io as helpers_subprocess,  # B4: единый канон (копия lifecycle/helpers удалена)
)
from core.internal.shared.docker_compose import nginx_reload as shared_docker_compose_nginx_reload
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

# W1-A1 (план 170): литералы таймаутов lifecycle-фаз → канон SoT (AMBER-зачистка research-D §D1).
# 120 (provision/firewall/reboot bash-скрипты) → LIFECYCLE_CMD_TIMEOUT; 60 (systemctl restart privoxy)
# → SYSTEM_CMD_TIMEOUT.
from core.internal.shared.timeouts import APT_TIMEOUT, DEPLOY_TIMEOUT, LIFECYCLE_CMD_TIMEOUT, SYSTEM_CMD_TIMEOUT


# region FUNC__ghcr_auth_step
## @purpose  GHCR auth best-effort (T2.1): близнец φ6/φ11 различался ТОЛЬКО phase-тегом логов —
##           консолидирован. True = non-fatal issue (φ6 → non_fatal_issues; φ11 → issue-флаг).
## @io       ⇥ env: Mapping | None (DI, W-H DevPlan 163; None = os.environ),
##              tag: str ("registry_auth"|"registry_update") → ⎋ bool (True = non-fatal issue)
## @complexity O(1) — env check + ghcr_auth
## @invariants — GHCR_PULL_TOKEN отсутствует → skip (не issue); сбой ghcr_auth → WARN + True
def _ghcr_auth_step(env: Mapping[str, str] | None, *, tag: str) -> bool:
    """GHCR auth best-effort — shared by φ6/φ11 (T2.1). True = non-fatal issue."""
    source: Mapping[str, str] = os.environ if env is None else env
    token = source.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][phase:%s] GHCR_PULL_TOKEN not set — skipping ghcr auth", tag)
        return False
    try:
        helpers_system.ghcr_auth()
        logger.info("[IMP:9][phase:%s] GHCR auth successful", tag)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:%s] GHCR auth failed (non-fatal): %s", tag, e)
        return True
    else:
        return False


# endregion FUNC__ghcr_auth_step


# region FUNC_phase_registry_auth
## @purpose φ6: Container registry authentication — GHCR (GitHub Container Registry) login
##           for image pulls. Docker Hub auth выполняется ТОЛЬКО в φ3 (docker_registry_auth.py,
##           ранний этап до pull) — дубль вызова убран (волна 117 D2).
##           Corresponds to init steps: ghcr_auth (16).
##           T2.1: GHCR-блок — общий шаг _ghcr_auth_step (с φ11), фаза тонкая.
## @io      ⇥ core_dir, node_name, node_yaml, env: Mapping | None = None (DI, W-H DevPlan 163 —
##              GHCR_PULL_TOKEN из дикта вместо os.environ; None = os.environ) → ⎋ bool
## @complexity O(1) + subprocess
## @invariants
##   - GHCR auth uses GHCR_PULL_TOKEN env var — skip if not set
##   - GHCR auth is non-fatal (best-effort)
##   - Docker Hub auth НЕ выполняется в φ6 (единственная точка — φ3 phase_platform_setup, D2)
def phase_registry_auth(
    core_dir: str,  # ruff: ignore[ARG001]
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """φ6: Registry auth — GHCR login (T2.1: общий шаг _ghcr_auth_step с φ11).

    Pre-check: None (auth is best-effort, no hard precondition).
    Execute: GHCR auth.
    Post-check: registry credentials configured (best-effort).
    """
    non_fatal_issues = False

    # ── 1. GHCR auth (общий шаг φ6/φ11, T2.1) ──
    non_fatal_issues |= _ghcr_auth_step(env=env, tag="registry_auth")

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
##   - INIT: deploy-modules.sh БЕЗ --skip-provision (встроенный provision networks/volumes —
##     v1.0.1 TRAP[BUG]: φ3 сети не создаёт, external-сети на свежей ноде отсутствовали)
def phase_deploy_services(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ8: Deploy services — modules + context projects.

    Pre-check: node.yaml exists, core_dir exists.
    Execute: deploy-modules.sh (docker + system) → deploy context projects.
    Post-check: subprocess exit codes.
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        msg = f"node.yaml not found: {node_yaml} — cannot deploy services"
        raise ConfigNotFoundError(msg)
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    non_fatal_issues = False

    # ── REF-0005: unlink-on-init — stale hc-done маркеры прошлых прогонов снимаются ДО деплоя,
    # иначе маркер чужого запуска гасит глубокий healthcheck (success-marker до доказательства).
    _ = _sweep_stale_hc_markers()

    # ── 1. Deploy modules (docker + system) via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            # v1.0.1 TRAP[BUG] (Фаза 6, bootstrap tronyx-vps): --skip-provision полагался на
            # «provision done in platform_setup» (φ3) — но φ3 сети/volumes НЕ создаёт →
            # «network observability-net declared as external, but could not be found» →
            # deploy всех модулей FAILED на свежей ноде. INIT-режим запускает deploy-modules.sh
            # БЕЗ --skip-provision (встроенный provision networks/volumes, Fail-Fast,
            # идемпотентен); UPDATE (φ12) сохраняет --skip-provision (сети уже существуют).
            #
            # plan 012 T9 (F-015b): INIT — strict-init семантика. failed≠∅ ИЛИ crit>0 →
            # exit 2 → PlatformFatalError → фаза failed в state.json (resumable, повтор
            # доводит). UPDATE (φ12) флаг НЕ передаёт — WARN→0 контракт CI сохранён (D2).
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--strict-init"],
                # T9.15 (B-12): deploy 14+ модулей может занять >300с (pull + build + healthcheck
                # per module) — канон DEPLOY_TIMEOUT (900, холодная нода) вместо 300.
                timeout=DEPLOY_TIMEOUT,
                check=True,
            )
            logger.info("[IMP:9][phase:deploy_services] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_services] Module deployment failed: %s", e)
            msg = f"Module deployment failed: {e}"
            raise PlatformFatalError(msg) from e
    else:
        logger.warning("[IMP:7][phase:deploy_services] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. Deploy context projects ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_services] Context projects deployed")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: context deploy is best-effort
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
## @io      ⇥ core_dir, node_name, node_yaml, env: Mapping | None = None (DI, W-H DevPlan 163 —
##              GHCR_PULL_TOKEN из дикта; None = os.environ) → ⎋ bool
## @complexity O(M * R + N) where M = modules in healthcheck, R = retries, N = overlay files
## @invariants
##   - GHCR auth is best-effort (no token = skip)
##   - Environment provision (networks + volumes) is non-fatal (may already exist)
##   - Nginx overlay reload is non-fatal (nginx may not be running)
##   - LLM key provisioning is non-fatal (optional component)
##   - Healthcheck is STANDALONE (skipped if .hc_done_in_deploy marker present)
##   - Каждый sub-step возвращает True при non-fatal issue (агрегация в оркестраторе)
def phase_registry_update(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """φ11: Registry and services update — GHCR auth, provision, overlays, LLM, healthcheck (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: GHCR auth → provision env → deliver overlays → LLM keys → healthcheck.
    Post-check: healthcheck results (best-effort, warnings collected).
    """
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    non_fatal_issues = False
    # E3: каждый шаг — отдельный хелпер (CC 23 → ≤10). Порядок сохранён (best-effort).
    # T2.1: GHCR-блок — общий шаг _ghcr_auth_step (с φ6); _registry_step_ghcr_auth удалён (дубль).
    non_fatal_issues |= _ghcr_auth_step(env=env, tag="registry_update")
    non_fatal_issues |= _registry_step_provision_env(core_dir)
    non_fatal_issues |= _registry_step_nginx_overlays(node_name)
    # 142 W6 (A2): re-apply конфига privoxy в update-режиме (no-op при корректном конфиге) —
    # после reboot/переустановки пакета privoxy сбрасывался к 127.0.0.1 → telegram-канал мёртв.
    non_fatal_issues |= _registry_step_privoxy_config(core_dir)
    # 142 B34: re-apply firewall baseline в update-режиме (W6 Фикс 2) — ufw-правило
    # tor-privoxy 8118 (172.16.0.0/12) могло не примениться при bootstrap (B30: Bad port)
    # или дрейфовать; firewall.sh инкрементален (никогда ufw disable/reset), non-fatal.
    non_fatal_issues |= _registry_step_firewall(core_dir)
    non_fatal_issues |= _registry_step_llm_provision(core_dir)
    non_fatal_issues |= _registry_step_healthcheck(node_yaml)

    if non_fatal_issues:
        logger.info("[IMP:8][phase:registry_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:registry_update] φ11 complete — registry and services updated")
    return True


# endregion FUNC_phase_registry_update


# region FUNC__registry_step_provision_env
## @purpose  E3 sub-step 2 (provision env): networks + volumes через provision-environment.sh.
## @io       ⇥ core_dir: str → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(1) + subprocess
## @invariants
##   - Скрипт отсутствует → WARN + True (non-fatal)
##   - Сбой provision → WARN + True (best-effort)
def _registry_step_provision_env(core_dir: str, run_subprocess_fn: Callable[..., object] | None = None) -> bool:
    """Provision environment (networks + volumes) sub-step."""
    provision_script = os.path.join(core_dir, "internal", "provision-environment.sh")
    if not os.path.isfile(provision_script):
        logger.warning(
            "[IMP:7][phase:registry_update] provision-environment.sh not found at %s — skipping",
            provision_script,
        )
        return True
    runner = helpers_subprocess.run_subprocess if run_subprocess_fn is None else run_subprocess_fn
    try:
        runner(
            ["bash", provision_script, "--scope", "networks", "--scope", "volumes"],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=LIFECYCLE_CMD_TIMEOUT,
        )
        logger.info("[IMP:9][phase:registry_update] Environment provisioned (networks + volumes)")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Environment provision failed (non-fatal): %s", e)
        return True
    else:
        return False


# endregion FUNC__registry_step_provision_env


# region FUNC__registry_step_nginx_overlays
## @purpose  E3 sub-step 3 (nginx overlays): reload nginx ТОЛЬКО при изменении содержимого
##           overlay-директории (T9.14, B-10). Хэш ВСЕГО содержимого dir (относительные пути +
##           содержимое файлов — deletions меняют набор путей) сравнивается с маркером
##           PLATFORM_STATE_DIR/.nginx-overlay-{node}.hash → reload при mismatch.
## @io       ⇥ node_name: str, node_configs_remote_base: str | None = None (DI, W-H DevPlan 163 —
##              override NODE_CONFIGS_REMOTE_BASE; None = deploy_paths канон),
##              state_dir: str | None = None (DI — PLATFORM_STATE_DIR; None = os.environ/дефолт),
##              reload_fn: Callable | None = None (DI — nginx_reload; None = shared канон)
##              → ⎋ bool (True = non-fatal issue occurred)
## @complexity O(N) где N = overlay файлы (хэш) + 1 reload
## @invariants
##   - Overlay-директории нет → skip (не issue)
##   - Хэш учитывает ВСЕ содержимое dir ВКЛЮЧАЯ пустоту: удаление ВСЕХ .conf инвалидирует
##     хэш → reload применяет удаление vhost'ов (раньше early-return «no .conf» маскировал)
##   - Первый запуск с пустым overlay → базовый маркер, reload НЕ выполняется
##   - Содержимое не изменилось → НЕТ reload (no-op; раньше reload был на каждый φ11)
##   - Сбой reload → WARN + True (best-effort)
##   - DI: node_configs_remote_base/state_dir/reload_fn=None → каноны (поведение без изменений);
##     тесты передают tmp_path/fake-reload (0 патчей env/функций, W-H)
# region FUNC__plw_body__registry_step_nginx_overlays
## @purpose  Тело try-блока (PLW0717 extraction из _registry_step_nginx_overlays) — семантика except не меняется.
## @io       ⇥ dir_hash, e, marker, reload_impl → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__registry_step_nginx_overlays(
    dir_hash: str,
    marker: str,
    reload_impl: Callable[[str], object],
) -> None:
    reload_impl("nginx")
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with pathlib.Path(marker).open("w", encoding="utf-8") as f:
            f.write(dir_hash)
    except OSError as e:
        logger.warning("[IMP:7][phase:registry_update] Cannot write overlay hash marker (non-fatal): %s", e)
    logger.info("[IMP:9][phase:registry_update] Nginx reloaded with overlays")


# endregion FUNC__plw_body__registry_step_nginx_overlays


def _registry_step_nginx_overlays(
    node_name: str,
    *,
    node_configs_remote_base: str | None = None,
    state_dir: str | None = None,
    reload_fn: Callable[..., object] | None = None,
) -> bool:
    """Deliver nginx overlays + reload sub-step (content-hash gated, T9.14)."""
    if node_configs_remote_base is not None:
        overlay_dir = str(pathlib.Path(node_configs_remote_base) / node_name / "overlays" / "nginx")
    else:
        overlay_dir = str(deploy_paths.node_configs_remote() / node_name / "overlays" / "nginx")
    if not os.path.isdir(overlay_dir):
        logger.info("[IMP:7][phase:registry_update] No overlay directory at %s — skipping", overlay_dir)
        return False

    # ── T9.14: content-hash ВСЕГО содержимого dir (пути + содержимое) — deletions инвалидируют ──
    import hashlib

    hasher = hashlib.sha256()
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        for rel in sorted(os.listdir(overlay_dir)):
            full = os.path.join(overlay_dir, rel)
            hasher.update(rel.encode("utf-8"))
            if os.path.isfile(full):
                with pathlib.Path(full).open("rb") as f:
                    hasher.update(f.read())
            hasher.update(b"\0")
    except OSError as e:
        logger.warning("[IMP:7][phase:registry_update] Cannot hash overlay dir %s (non-fatal): %s", overlay_dir, e)
        return True
    dir_hash = hasher.hexdigest()

    resolved_state_dir = (
        os.environ.get("PLATFORM_STATE_DIR", "/var/lib/platform/.bootstrap") if state_dir is None else state_dir
    )
    marker = os.path.join(resolved_state_dir, f".nginx-overlay-{node_name}.hash")
    marker_exists = os.path.isfile(marker)
    previous = ""
    if marker_exists:
        try:
            with pathlib.Path(marker).open(encoding="utf-8") as f:
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
            with pathlib.Path(marker).open("w", encoding="utf-8") as f:
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
    reload_impl = shared_docker_compose_nginx_reload if reload_fn is None else reload_fn
    try:
        # W1 (DevPlan 128): docker exec — shared docker_compose.nginx_reload → docker_ops.docker_exec
        # (non-fatal фасад; best-effort семантика сохраняется)
        _plw_body__registry_step_nginx_overlays(dir_hash, marker, reload_impl)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Nginx reload failed (non-fatal): %s", e)
        return True
    else:
        return False


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
def _registry_step_privoxy_config(_core_dir: str) -> bool:
    """Re-apply privoxy config (idempotent, 119 D3) — no-op при корректном конфиге (142 W6)."""
    if os.environ.get("TOR_ENABLED", "false").lower() != "true":
        logger.info("[IMP:7][phase:registry_update] TOR_ENABLED != true — skipping privoxy config re-apply")
        return False
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        from core.internal.bootstrap.privoxy_config import write_privoxy_config

        changed = write_privoxy_config("/etc/privoxy/config")
        if changed:
            # Дрейф был (конфиг сброшен к дефолту пакета) — перезаписан каноном; WARN-сигнал
            logger.warning(
                "[IMP:8][phase:registry_update] Privoxy config was drifted — re-applied canonical config (142 W6)"
            )
            # 142 B35: после записи конфига сервис обязан перечитать его — systemctl restart
            # (reload недостаточно: privoxy 3.0.34 перечитывает listen-address только на старте).
            # Без рестарта 0.0.0.0:8118 не вступает в силу → grafana telegram канал мёртв (C6).
            from core.internal.shared import subprocess_io as helpers_subprocess

            helpers_subprocess.run_subprocess(
                ["systemctl", "restart", "privoxy"], non_fatal=True, fatal_rc=(127,), timeout=SYSTEM_CMD_TIMEOUT
            )
            logger.info("[IMP:9][phase:registry_update] Privoxy restarted after config re-apply")
            return True
        logger.info("[IMP:9][phase:registry_update] Privoxy config already canonical — no-op")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort, как firewall/tor в φ1)
        logger.warning("[IMP:7][phase:registry_update] Privoxy config re-apply failed (non-fatal): %s", e)
        return True
    else:
        return False


# endregion FUNC__registry_step_privoxy_config


# region FUNC__registry_step_firewall
## @purpose  142 B34: re-apply firewall baseline в update-режиме (W6 Фикс 2 расширение).
##           ufw-правило tor-privoxy (172.16.0.0/12 → 8118) могло не примениться при bootstrap
##           (B30: «Bad port '8118'») или задрейфовать; firewall.sh инкрементален (S-14:
##           никогда ufw disable/reset — только add/delete точечные). Best-effort, non-fatal.
## @io       ⇥ core_dir → ⎋ bool (True = non-fatal issue)
## @complexity O(F) — F = ufw-команды (малый константный набор)
## @invariants — только при TOR_ENABLED=true (правило privoxy актуально только с tor)
##              — отсутствующий firewall.sh → WARN (не issue)
##              — E3 (DevPlan 160): runner/env keyword-only параметры (DI — тест без
##                monkeypatch subprocess_io.run_subprocess / os.environ)
def _registry_step_firewall(
    core_dir: str,
    *,
    runner: CommandRunner | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Re-apply ufw baseline (incremental) в update-режиме — 142 B34 (W6 Фикс 2)."""
    source: Mapping[str, str] = os.environ if env is None else env
    if source.get("TOR_ENABLED", "false").lower() != "true":
        logger.info("[IMP:7][phase:registry_update] TOR_ENABLED != true — skipping firewall re-apply")
        return False
    firewall_script = os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")
    if not os.path.isfile(firewall_script):
        logger.warning("[IMP:7][phase:registry_update] firewall.sh not found at %s — skipping", firewall_script)
        return False
    try:
        runner = runner if runner is not None else default_command_runner()
        node_yaml = str(source.get("NODE_YAML", "") or "")
        # DR-H1 fix (DevPlan 010 T2.3): peer-rules применяются и в update-режиме —
        # --placement пробрасывается когда у ноды есть placement.yaml ([] при single-node)
        placement_args = firewall_placement_args(node_yaml) if node_yaml else []
        runner.run(
            ["bash", firewall_script, *placement_args],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=LIFECYCLE_CMD_TIMEOUT,
        )
        logger.info("[IMP:9][phase:registry_update] Firewall baseline re-applied (incremental)")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort, как privoxy/firewall в φ1)
        logger.warning("[IMP:7][phase:registry_update] Firewall re-apply failed (non-fatal): %s", e)
        return True
    else:
        return False


# endregion FUNC__registry_step_firewall


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
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
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
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] LLM key provisioning failed (non-fatal): %s", e)
        return True
    else:
        return False


# endregion FUNC__registry_step_llm_provision


# region FUNC__find_run_scoped_hc_markers
## @purpose  REF-0005: найти run-scoped hc-done маркеры данного scope — `stem`.`run-id`,
##           run-id = YYYYMMDDTHHMMSS-`pid` (писатель: deploy_orchestrator._set_hc_marker).
##           stem — ПОЛНАЯ база маркера БЕЗ run-id (уже с суффиксом контекста при наличии):
##           orchestrator_metrics.hc_marker_path(context); единый SoT читателя и писателя.
## @io       ⇥ stem: str (полный путь-база), glob_fn DI → ⎋ list[str] (отсортированные пути)
## @complexity O(M) где M = число маркеров в state-dir
## @invariants
##   - Формат run-id однозначен: контекстные имена (kebab/org) не матчатся шаблону 8digits-T-6digits-digits
##   - Scope задаётся stem'ом: чужие контексты не попадают в результат (T9.19)
_HC_RUN_ID_RE = re.compile(r"\d{8}T\d{6}-\d+$")


def _find_run_scoped_hc_markers(
    stem: str,
    glob_fn: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """Return sorted run-scoped hc-done marker paths under the full marker-base ``stem``."""
    prefix = f"{stem}."
    finder = glob.glob if glob_fn is None else glob_fn
    found: list[str] = []
    for path in sorted(finder(glob.escape(prefix) + "*")):
        suffix = path[len(prefix) :]
        if suffix and _HC_RUN_ID_RE.fullmatch(suffix):
            found.append(path)
    return found


# endregion FUNC__find_run_scoped_hc_markers


# region FUNC__sweep_stale_hc_markers
## @purpose  REF-0005 unlink-on-init/update: снять stale hc-done маркеры СВОЕГО scope до старта
##           деплоя (φ8/φ12) — legacy точные формы (bare / per-context, писатели до REF-0005)
##           и run-scoped маркеры прошлых прогонов. Без свипа маркер чужого запуска гасит
##           единственный глубокий healthcheck φ11 (unlink→rewrite цикл, BUG-0501≡0703).
## @io       ⇥ context: str | None (None → CONTEXT env; "" → single-node canon),
##              glob_fn/unlink_fn DI (W-H) → ⎋ int — число удалённых маркеров
## @complexity O(M) где M = число маркеров в state-dir
## @invariants
##   - Scope строго свой: context="" → bare + bare-run-scoped; context=X → X-legacy + X-run-scoped;
##     чужие контексты НЕ затрагиваются (паритет с читателем _registry_step_healthcheck)
##   - Best-effort: OSError → WARN, не raise (DEPLOY_BEST_EFFORT); FileNotFoundError — гонка снятия
def _sweep_stale_hc_markers(
    context: str | None = None,
    *,
    glob_fn: Callable[[str], list[str]] | None = None,
    unlink_fn: Callable[[str], None] | None = None,
) -> int:
    """Remove stale hc-done markers of this scope at init/update start (REF-0005)."""
    from core.internal.bootstrap.deploy.orchestrator_metrics import hc_marker_path as _hc_marker_path

    resolved_context = os.environ.get("CONTEXT") if context is None else context
    base = _hc_marker_path(resolved_context)
    remover = os.unlink if unlink_fn is None else unlink_fn

    candidates: list[str] = []
    # Legacy точная форма этого scope (до REF-0005): hc_marker_path(context) — bare при
    # пустом контексте, per-context иначе.
    if os.path.isfile(base):
        candidates.append(base)
    candidates.extend(_find_run_scoped_hc_markers(base, glob_fn=glob_fn))

    removed = 0
    for path in candidates:
        try:
            remover(path)
            removed += 1
            logger.info("[IMP:8][phase:docker][hc_sweep] Removed stale hc-done marker: %s", path)
        except FileNotFoundError:  # ruff: ignore[PERF203] — unlink per stale-marker; гонка снятия не фатальна
            continue
        except OSError as exc:
            logger.warning("[IMP:7][phase:docker][hc_sweep] Cannot remove %s: %s", path, exc)
    logger.info(
        "[IMP:9][phase:docker][hc_sweep] Sweep done (context=%r): removed=%d",
        resolved_context or "",
        removed,
    )
    return removed


# endregion FUNC__sweep_stale_hc_markers


# region FUNC__registry_step_healthcheck
## @purpose  E3 sub-step 5 (healthcheck): standalone healthcheck, skip если маркер уже стоит.
## @io       ⇥ node_yaml: str, run_start_ts: float | None (QA R2/T2.B) → ⎋ bool (True = non-fatal issue)
## @complexity O(M * R) where M = modules, R = retries
## @invariants
##   - `.hc_done_in_deploy` + суффикс контекста (per-context, T9.19) → skip + unlink (не issue)
##   - REF-0005: run-scoped маркеры текущего прогона (`base`[.`ctx`].`run-id`) → skip + unlink;
##     свип на старте φ8/φ12 гарантирует, что найденный маркер — этого прогона
##   - QA R2 (DevPlan 14 T2.B): freshness — маркер принимается ТОЛЬКО при mtime ≥ run-start
##     (φ11 reader исполняется ДО φ12-писателя; найденный маркер = прошлый прогон → НЕ
##     подавляет healthcheck). run_start_ts=None → legacy-семантика (нет знания о старте).
##   - node.yaml отсутствует → WARN + True
##   - Сбой healthchecks → WARN + True (best-effort)
def _registry_step_healthcheck(
    node_yaml: str,
    *,
    context: str | None = None,
    run_start_ts: float | None = None,
    run_healthchecks_fn: Callable[..., object] | None = None,
    isfile_fn: Callable[[str], bool] | None = None,
    glob_fn: Callable[[str], list[str]] | None = None,
) -> bool:
    """Standalone healthcheck sub-step (skip if already done in THIS run)."""
    # T9.19 (B-11): маркер per-context (не node-global) — единый источник пути с писателем
    # (deploy_orchestrator._set_hc_marker → orchestrator_metrics.hc_marker_path). CONTEXT env
    # задаётся при деплое контекста; деплой context A не подавляет healthcheck context B.
    # W-H (DevPlan 163): context/run_healthchecks_fn/isfile_fn DI (None = каноны os.environ/
    # helpers_reporting.run_healthchecks/os.path.isfile) — тесты без патчей env/функций.
    from core.internal.bootstrap.deploy.orchestrator_metrics import hc_marker_path as _hc_marker_path

    resolved_context = os.environ.get("CONTEXT") if context is None else context
    isfile_impl = os.path.isfile if isfile_fn is None else isfile_fn
    # QA R2/T2.B: run-start — явный param > run_context-регистрация > None (legacy семантика).
    # Leaf run_context (НЕ state_machine) — разрыв цикла импортов phases ↔ state_machine.
    effective_run_start = run_start_ts
    if effective_run_start is None:
        from core.internal.bootstrap.lifecycle import run_context as _run_context

        effective_run_start = _run_context.get_run_start_ts()
        if effective_run_start is None:
            logger.info(
                "[IMP:8][phase:registry_update][freshness] run-start unknown — "
                "legacy marker semantics (sweep at φ12 will clear stale)"
            )

    def _marker_fresh(marker: str) -> bool:
        """QA R2/T2.B: маркер подавляет healthcheck только если он ТЕКУЩЕГО прогона."""
        if effective_run_start is None:
            return True
        try:
            fresh = pathlib.Path(marker).stat().st_mtime >= effective_run_start
        except OSError:
            return False
        if not fresh:
            logger.info(
                "[IMP:9][phase:registry_update][freshness] stale marker %s (mtime < run-start) "
                "— past-run marker does NOT suppress deep healthcheck",
                marker,
            )
        return fresh

    hc_done_marker = _hc_marker_path(resolved_context)
    # ⚠️ TRAP[BUG] · 2026-08-24 · P0 · вечный hc_done-маркер гасил последний healthcheck (REF-0005)
    # · Symptom: φ11 пропускал единственный глубокий healthcheck по маркеру чужого/прошлого
    #   запуска; параллельный путь писал маркер даже при failed-группах.
    # · Root: success-marker до доказательства + маркер без run-scoping.
    # · Fix: писатель пишет только при failed==[] и с run-id; читатель находит run-scoped формы;
    #   свип на старте φ8/φ12 снимает stale-маркеры прошлого прогона.
    # · Prevention: tests/unit/test_hc_marker_run_scope.py (honesty + run-scope + sweep).
    # · QA R2/T2.B: третий слой — mtime ≥ run-start (reader исполняется РАНЬШЕ писателя φ12,
    #   поэтому найденный маркер физически не может быть этого прогона без retry-семантики).
    run_scoped_all = _find_run_scoped_hc_markers(hc_done_marker, glob_fn=glob_fn)
    stale_run_scoped = [m for m in run_scoped_all if not _marker_fresh(m)]
    run_scoped_markers = [m for m in run_scoped_all if _marker_fresh(m)]
    legacy_present_raw = isfile_impl(hc_done_marker)
    legacy_stale = legacy_present_raw and not _marker_fresh(hc_done_marker)
    legacy_present = legacy_present_raw and _marker_fresh(hc_done_marker)
    # QA R2/T2.B: stale-маркеры прошлого прогона снимаются читателем (гигиена — иначе они
    # переживут до свипа следующего φ12).
    if stale_run_scoped or legacy_stale:
        import contextlib

        for stale_marker in stale_run_scoped:
            with contextlib.suppress(OSError):
                os.unlink(stale_marker)
        if legacy_stale:
            with contextlib.suppress(OSError):
                os.unlink(hc_done_marker)
        logger.info(
            "[IMP:9][phase:registry_update][freshness] removed %d stale past-run marker(s)",
            len(stale_run_scoped) + (1 if legacy_stale else 0),
        )
    if legacy_present or run_scoped_markers:
        logger.info(
            "[IMP:9][phase:registry_update] Healthcheck already done during deploy "
            "(DEPLOY_PARALLEL, marker %s%s) — skipping standalone healthcheck",
            hc_done_marker,
            f" + {len(run_scoped_markers)} run-scoped" if run_scoped_markers else "",
        )
        import contextlib

        with contextlib.suppress(OSError):
            if legacy_present:
                os.unlink(hc_done_marker)
        for stale in run_scoped_markers:
            with contextlib.suppress(OSError):
                os.unlink(stale)
        return False
    if not node_yaml or not isfile_impl(node_yaml):
        logger.warning("[IMP:7][phase:registry_update] node.yaml not found — skipping healthchecks")
        return True
    hc_runner = helpers_reporting.run_healthchecks if run_healthchecks_fn is None else run_healthchecks_fn
    try:
        hc_runner(node_yaml)
        logger.info("[IMP:9][phase:registry_update] Healthchecks completed")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:registry_update] Healthchecks failed (non-fatal): %s", e)
        return True
    else:
        return False


# endregion FUNC__registry_step_healthcheck


# region FUNC__apply_policy_script
## @purpose  Best-effort policy-скрипт (T2.1): security_updates/reboot_policy блоки φ12 были
##           попарными близнецами — консолидированы. True = non-fatal issue.
## @io       ⇥ script: str, args: list[str], timeout: int (SoT), ok_msg/warn_msg/missing_msg: str,
##              missing_non_fatal: bool (missing → issue или WARN-без-issue, канон 5.6) → ⎋ bool
## @complexity O(1) + 1 subprocess
## @invariants
##   - missing → WARN + missing_non_fatal (не raise); сбой → WARN + True (best-effort)
##   - НЕ переиспользует _run_best_effort_script (system.py): иной I/O-канал
##     (module-level helpers_subprocess, без runner/facts/IssueCollector DI)
def _apply_policy_script(
    script: str,
    args: list[str],
    *,
    timeout: int,
    ok_msg: str,
    warn_msg: str,
    missing_msg: str,
    missing_non_fatal: bool,
) -> bool:
    """Run a best-effort policy script (security_updates/reboot_policy) — shared φ12 steps (T2.1)."""
    if not os.path.isfile(script):
        logger.warning(missing_msg)
        return missing_non_fatal
    try:
        helpers_subprocess.run_subprocess(
            ["python3", script, *args],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=timeout,
        )
        logger.info(ok_msg)
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: policy script is best-effort
        logger.warning(warn_msg, e)
        return True
    else:
        return False


# endregion FUNC__apply_policy_script


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
        msg = f"node.yaml not found: {node_yaml} — cannot deploy update"
        raise ConfigNotFoundError(msg)
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    non_fatal_issues = False

    # ── REF-0005: unlink-on-update — stale hc-done маркеры прошлых прогонов снимаются ДО деплоя,
    # иначе φ11 пропустит глубокий healthcheck по чужому success-marker'у.
    _ = _sweep_stale_hc_markers()

    # ── 1. Deploy modules via deploy-modules.sh ──
    deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
    if os.path.isfile(deploy_script):
        try:
            helpers_subprocess.run_subprocess(
                ["bash", deploy_script, "--skip-provision"],
                # T9.15 (B-12): канон DEPLOY_TIMEOUT (600) вместо 300 (см. φ8)
                timeout=DEPLOY_TIMEOUT,
                check=True,
            )
            logger.info("[IMP:9][phase:deploy_update] Modules deployed successfully")
        except (PlatformError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][phase:deploy_update] Module deployment failed: %s", e)
            msg = f"Module deployment failed during update: {e}"
            raise PlatformFatalError(msg) from e
    else:
        logger.warning("[IMP:7][phase:deploy_update] deploy-modules.sh not found at %s — skipping", deploy_script)
        non_fatal_issues = True

    # ── 2. SSL provision via cert_orchestrator ──
    try:
        helpers_domains.ssl_provision_via_orchestrator(core_dir, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] SSL certificates provisioned")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal: SSL is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:deploy_update] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 3. Deploy context projects (incremental) ──
    try:
        helpers_domains.import_deploy_context(core_dir, node_name, node_yaml)
        logger.info("[IMP:9][phase:deploy_update] Context projects deployed incrementally")
    except (OSError, PlatformError) as e:  # noqa: EXC — non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][phase:deploy_update] Context deploy failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 4. Apply unattended-upgrades security policy (DevPlan 134 L1) ──
    # Пропагация политики при node-update: ensure() идемпотентен (content-match no-op) —
    # на актуальной ноде = 0 записей на диск, на устаревшей — применяет security-конфиг.
    # Non-fatal (best-effort): policy-сбой не должен ронять update-цикл.
    # 164 W1-3: default auto_reboot=false — Automatic-Reboot "false" (платформенный таймер
    # platform-reboot.timer — единственный ребут-канал, вариант A).
    security_script = os.path.join(core_dir, "internal", "bootstrap", "security_updates.py")
    auto_reboot = os.environ.get("SECURITY_AUTO_REBOOT", "false").lower() == "true"
    non_fatal_issues |= _apply_policy_script(
        security_script,
        ["--auto-reboot", "true" if auto_reboot else "false"],
        timeout=APT_TIMEOUT,
        ok_msg="[IMP:9][phase:deploy_update] Unattended-upgrades security policy applied",
        warn_msg="[IMP:7][phase:deploy_update] Security updates setup failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:deploy_update] security_updates.py not found at {security_script} — skipping",
        missing_non_fatal=True,
    )

    # ── 5. Reboot-policy units (DevPlan 164 W1-3) ──
    # Пропагация при node-update: reboot_policy.py install идемпотентен (content-match no-op).
    # Non-fatal (канон security_updates шаг 4).
    reboot_script = os.path.join(core_dir, "internal", "bootstrap", "reboot_policy.py")
    non_fatal_issues |= _apply_policy_script(
        reboot_script,
        ["install"],
        timeout=LIFECYCLE_CMD_TIMEOUT,
        ok_msg="[IMP:9][phase:deploy_update] Reboot-policy units installed (idempotent)",
        warn_msg="[IMP:7][phase:deploy_update] Reboot-policy install failed (non-fatal): %s",
        missing_msg=f"[IMP:7][phase:deploy_update] reboot_policy.py not found at {reboot_script} — skipping",
        # Отсутствие скрипта (тест-окружения/tmp CORE_DIR) — WARN, НЕ non_fatal (канон
        # security_posture.py φ1 шаг 5.6): на реальной ноде скрипт гарантирован core-доставкой;
        # reboot-policy best-effort, фаза не уходит в done_with_warnings из-за него.
        missing_non_fatal=False,
    )

    if non_fatal_issues:
        logger.info("[IMP:8][phase:deploy_update] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:deploy_update] φ12 complete — services and SSL deployed")
    return True


# endregion FUNC_phase_deploy_update
