# GREP_SUMMARY: post-deploy-chain, notify-hook, generate-catalog, monitoring-reconfig, module-deploy-hooks, best-effort, 170-W4-B3
# STRUCTURE: ▶ run_post_deploy_chain(project, version, status, project_dir, node_name) → ⊕ _notify_hook (Telegram, timeout) → ⊕ _generate_catalog (regen) → ⊕ _monitoring_reconfig (render) → ⊕ _module_deploy_hooks (registry module.yaml) → ⎋ WARN non-fatal на каждом шаге
# region MODULE_CONTRACT
## @purpose  Best-effort post-deploy chain (DevPlan 116 B1 T2/D4, U-24): notify-hook (Telegram)
##           + generate-catalog (regen catalog.json) + monitoring reconfig (DevPlan 138 W3)
##           + module deploy-hooks (B8 wire). Единственный прямой subprocess-потребитель
##           deploy-кластера (research-A §3 B3: orchestrator.py :1035-1127).
##           Все шаги неблокирующие: сбой → WARN, деплой НЕ фейлится (дизайн notify-hook always exit 0).
## @scope    Вынесен из монолита deploy/orchestrator.py (170 W4-B3). Public API —
##           run_post_deploy_chain (тонкий оркестратор 4 подшагов); каждый подшаг —
##           отдельная handler-функция (_notify_hook/_generate_catalog/_monitoring_reconfig/
##           _module_deploy_hooks). Вызывается DeployOrchestrator._run_post_deploy_chain (делегат)
##           и ReceiveFlow (через orchestrator-фабрику, receive_flow.py:385).
## @invariants
##   - Вызывается ТОЛЬКО после успешного деплоя (DEPLOYED/PARTIAL)
##   - notify-hook timeout 30s (CONVERGE_DOCKER_TIMEOUT), generate-catalog timeout 60s
##     (SYSTEM_CMD_TIMEOUT), module deploy-hook COMPOSE_UP_TIMEOUT
##   - Сбой цепочки → logger.warning (IMP:8), не raise
##   - module deploy-hooks (module.yaml hooks.on_project_deploy) вызываются
##     через shared/module_interface.invoke (registry-driven)
##   - monitoring reconfig (run_monitoring_reconfig, lazy-import) — ПОСЛЕ
##     generate-catalog, ДО deploy-hooks; WARN non-fatal (R5)
##   - DI (W-H DevPlan 163): run_cmd=None → subprocess.run; platform_root_override=None →
##     platform_remote_base(); reconfig_fn=None → lazy run_monitoring_reconfig (канон)
## @rationale research-A §3 B3: единственный прямой subprocess-вызов deploy-кластера
##            изолируется в hooks-модуль — orchestrator.py остаётся фасадом, subprocess-
##            граница перестаёт смешиваться с оркестрацией.
## @changes 2026-08-15 | 170 W4-B3 — extracted from deploy/orchestrator.py (1:1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import glob
import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml  # module-level (deploy-hooks читают module.yaml; YAMLError в except-ветке требует bound-имя)

# W1-A1 (план 170): литералы таймаутов post_deploy_chain → канон SoT (AMBER-зачистка research-D §D1).
# 30 (notify-hook) → CONVERGE_DOCKER_TIMEOUT; 60 (generate-catalog) → SYSTEM_CMD_TIMEOUT.
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT, SYSTEM_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# Лог-префикс сохранён от монолита ([DeployOrchestrator][...]) — тесты и исторические
# наблюдатели ищут эти подстроки ("monitoring reconfig WARN (non-fatal)", "modules dir not found").
_BLOCK = "post_deploy_chain"
_HOOKS_BLOCK = "deploy_hooks"


# region FUNC__notify_hook
## @purpose  Подшаг 1: notify-hook (Telegram) — неблокирующий (always exit 0).
##           Best-effort: сбой уведомления НЕ фейлит деплой (D4, дизайн notify-hook).
## @io       ⇥ runner: Callable (subprocess-канал), notify_hook: str (путь к скрипту),
##              project: str, version: str, status: str → ⎋ None
## @complexity — O(1) — single subprocess call with timeout
## @invariants
##   - timeout=CONVERGE_DOCKER_TIMEOUT (30s), check=False, capture_output=True
##   - OSError/TimeoutExpired/SubprocessError → WARN (IMP:8), не raise
def _notify_hook(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    notify_hook: str,
    project: str,
    version: str,
    status: str,
) -> None:
    """Run notify-hook (Telegram) — best-effort, WARN non-fatal."""
    try:
        runner(
            [
                notify_hook,
                "--severity",
                "info",
                "🚀",
                f"Deployed {project} ({version}) — {status}",
            ],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=False,
        )
        logger.info("[IMP:9][DeployOrchestrator][%s] notify-hook sent for %s", _BLOCK, project)
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        # Best-effort: сбой уведомления НЕ фейлит деплой (D4, дизайн notify-hook)
        logger.warning("[IMP:8][DeployOrchestrator][%s] notify-hook WARN (non-fatal): %s", _BLOCK, e)


# endregion FUNC__notify_hook


# region FUNC__generate_catalog
## @purpose  Подшаг 2: generate-catalog (regen catalog.json). Best-effort: сбой → WARN.
## @io       ⇥ runner: Callable (subprocess-канал), generate_catalog: str (путь к скрипту),
##              project: str → ⎋ None
## @complexity — O(1) — single subprocess call with timeout
## @invariants
##   - timeout=SYSTEM_CMD_TIMEOUT (60s), check=False, capture_output=True
##   - OSError/TimeoutExpired/SubprocessError → WARN (IMP:8), не raise
def _generate_catalog(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    generate_catalog: str,
    project: str,
) -> None:
    """Run generate-catalog (regen catalog.json) — best-effort, WARN non-fatal."""
    try:
        runner(
            [generate_catalog],
            capture_output=True,
            text=True,
            timeout=SYSTEM_CMD_TIMEOUT,
            check=False,
        )
        logger.info("[IMP:9][DeployOrchestrator][%s] generate-catalog regenerated for %s", _BLOCK, project)
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning("[IMP:8][DeployOrchestrator][%s] generate-catalog WARN (non-fatal): %s", _BLOCK, e)


# endregion FUNC__generate_catalog


# region FUNC__monitoring_reconfig
## @purpose  Подшаг 3: Monitoring reconfig — рендер мониторинга после деплоя (DevPlan 138 W3).
##           Non-blocking (R5): исключение → WARN, деплой НЕ фейлится (best-effort контракт).
## @io       ⇥ project_dir: str, project: str, node_name: str, platform_root: str,
##              reconfig_fn: Callable | None (DI; None → lazy run_monitoring_reconfig) → ⎋ None
## @complexity — O(1) — single render call
## @invariants
##   - reconfig_fn=None → lazy-import core.internal.monitoring.config_renderer.run_monitoring_reconfig
##   - Аргументы: Path(project_dir), project, node_name, Path(platform_root)
##   - Любое Exception → WARN (IMP:8), не raise (best-effort контракт chain)
def _monitoring_reconfig(
    project_dir: str,
    project: str,
    node_name: str,
    platform_root: str,
    reconfig_fn: Callable[..., object] | None,
) -> None:
    """Run monitoring reconfig (render после деплоя) — best-effort, WARN non-fatal."""
    # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализир...
    try:
        if reconfig_fn is None:
            from core.internal.monitoring.config_renderer import run_monitoring_reconfig

            reconfig_impl = run_monitoring_reconfig
        else:
            reconfig_impl = reconfig_fn
        reconfig_impl(
            Path(project_dir),
            project,
            node_name or "",
            Path(platform_root),
        )
    # ruff: ignore[BLE001] — best-effort контракт post-deploy chain (monitoring reconfig)
    except Exception as e:  # noqa: EXC — best-effort контракт post-deploy chain
        logger.warning(
            "[IMP:8][DeployOrchestrator][%s] monitoring reconfig WARN (non-fatal): %s",
            _BLOCK,
            e,
        )


# endregion FUNC__monitoring_reconfig


# region FUNC__module_deploy_hooks
## @purpose  Подшаг 4: Module deploy-hooks — deploy-hook для зарегистрированных модулей (B8 wire).
##           Registry-driven: читает core/modules/*/module.yaml (registry = файловая система),
##           НЕ хардкодит имена модулей. Best-effort: сбой → WARN, деплой не фейлится.
## @io       ⇥ project_dir: str, project: str, node_name: str → ⎋ None
## @complexity — O(M * K) где M = модули с hooks, K = hook-скрипты на модуль
## @invariants
##   - Каждый module.yaml с hooks.on_project_deploy → module_interface.invoke(module, "deploy-hook", ...)
##   - hook args: PROJECT_DIR PROJECT NODE_NAME (сигнатура nginx_reload_hook.sh)
##   - Сбой invoke → WARN (IMP:8), не raise (Best-effort контракт post-deploy chain)
##   - modules dir отсутствует → IMP:7 info, return (не WARN)
def _module_deploy_hooks(project_dir: str, project: str, node_name: str) -> None:
    """Invoke registered module deploy-hooks via shared module_interface (B8)."""
    from core.internal.shared.module_interface import invoke as invoke_module_hook

    platform_root = str(platform_remote_base())
    modules_dir = os.path.join(platform_root, "core", "modules")
    if not os.path.isdir(modules_dir):
        logger.info("[IMP:7][DeployOrchestrator][%s] modules dir not found: %s", _HOOKS_BLOCK, modules_dir)
        return

    for module_yaml in sorted(glob.glob(os.path.join(modules_dir, "*/module.yaml"))):
        module_name = Path(Path(module_yaml).parent).name
        try:
            with Path(module_yaml).open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            hooks = data.get("hooks") or {}
            if not hooks.get("on_project_deploy"):
                continue
        except (OSError, yaml.YAMLError) as e:
            logger.warning("[IMP:8][DeployOrchestrator][%s] read error %s: %s", _HOOKS_BLOCK, module_yaml, e)
            continue

        logger.info(
            "[IMP:8][DeployOrchestrator][%s] Invoking deploy-hook for module %s (project=%s)",
            _HOOKS_BLOCK,
            module_name,
            project,
        )
        ok, output = invoke_module_hook(module_name, "deploy-hook", project_dir, project, node_name)
        if not ok:
            logger.warning(
                "[IMP:8][DeployOrchestrator][%s] %s deploy-hook WARN (non-fatal): %s",
                _HOOKS_BLOCK,
                module_name,
                (output or "").strip()[-300:],
            )
        else:
            logger.info("[IMP:9][DeployOrchestrator][%s] %s deploy-hook done", _HOOKS_BLOCK, module_name)


# endregion FUNC__module_deploy_hooks


# region FUNC_run_post_deploy_chain
## @purpose  Тонкий оркестратор post-deploy chain: notify-hook + generate-catalog + monitoring
##           reconfig + module deploy-hooks (best-effort, D4). Public API модуля.
## @io       ⇥ project: str, version: str, status: str, project_dir: str | None = None,
##              node_name: str = "", *, run_cmd: Callable | None = None (DI subprocess-канал),
##              platform_root_override: str | None = None (DI), reconfig_fn: Callable | None = None
##              (DI) → ⎋ None
## @complexity — O(M * K) где M = модули с hooks, K = hook-скрипты на модуль; иначе O(1)
## @invariants
##   - Порядок подшагов фиксирован: notify-hook → generate-catalog → monitoring reconfig → deploy-hooks
##   - monitoring reconfig и module deploy-hooks выполняются только при project_dir
##   - DI (W-H DevPlan 163): run_cmd=None → subprocess.run; platform_root_override=None →
##     platform_remote_base(); reconfig_fn=None → lazy run_monitoring_reconfig (канон)
##   - Сбой любого подшага → WARN, деплой НЕ фейлится
def run_post_deploy_chain(
    project: str,
    version: str,
    status: str,
    project_dir: str | None = None,
    node_name: str = "",
    *,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    platform_root_override: str | None = None,
    reconfig_fn: Callable[..., object] | None = None,
) -> None:
    """Run notify-hook + generate-catalog + monitoring reconfig + module deploy-hooks (best-effort, D4).

    ▶ ┌project/version/status┐ → ○ _notify_hook → ○ _generate_catalog → ◇ project_dir? → ○ _monitoring_reconfig → ○ _module_deploy_hooks → ⎋ None
    """
    runner = subprocess.run if run_cmd is None else run_cmd
    platform_root = str(platform_remote_base()) if platform_root_override is None else platform_root_override
    notify_hook = os.path.join(platform_root, "core", "internal", "notify", "notify-hook.sh")
    generate_catalog = os.path.join(platform_root, "core", "internal", "catalog", "generate-catalog.sh")

    logger.info(
        "[IMP:8][DeployOrchestrator][%s] Running notify-hook + generate-catalog + deploy-hooks for %s (%s)",
        _BLOCK,
        project,
        version,
    )

    # ── notify-hook (Telegram) — неблокирующий (always exit 0) ──
    _notify_hook(runner, notify_hook, project, version, status)

    # ── generate-catalog (regen catalog.json) ──
    _generate_catalog(runner, generate_catalog, project)

    # ── Monitoring reconfig: рендер мониторинга после деплоя (non-blocking, R5) ──
    # Регистрация: module.yaml hooks.on_project_deploy (+ entrypoint-manifest module_hooks).
    # Зарегистрирован только nginx (reload-guard); monitoring/postgres —
    # Python-эквиваленты (не shell-хуки).
    if project_dir and project:
        _monitoring_reconfig(project_dir, project, node_name, platform_root, reconfig_fn)

    # ── Module deploy-hooks: deploy-hook для зарегистрированных модулей ──
    if project_dir:
        _module_deploy_hooks(project_dir, project, node_name)


# endregion FUNC_run_post_deploy_chain
