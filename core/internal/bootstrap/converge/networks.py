#!/usr/bin/env python3
# GREP_SUMMARY: converge-networks, reconcile-networks, r4, proxy-net, docker-network, bridge, connectivity
# STRUCTURE: ▶ docker info → ◇ proxy-net inspect? → ⚡ create (bridge, runtime fallback) │ ◇ driver check → ⚡ check_proxy_connectivity ┌docker ps --filter label → inspect networks┐ → ⎋ drift entry {R4}
# region MODULE_CONTRACT
## @purpose  R4 reconcile_networks — proxy-net Docker network + project container connectivity.
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/networks.py: reconcile_networks, check_proxy_connectivity.
##           Вызывается оркестратором reconciler.py.
## @invariants
##   - Docker daemon недоступен → fail (не блокирует другие юниты)
##   - proxy-net существует с wrong driver → WARN, не пересоздаётся
##   - Авто-connect НЕ выполняется (compose project ответственность)
##   - Deployed-проект с 0 running-контейнеров → WARN-запись (drift) + logger.warning,
##     exit-код НЕ меняется (reconcile контейнеров — канал deploy-project, live-drill 2026-09-03)
##   - Stub/awaiting-проект (GENERATED-STUB ai-platform.yaml) с 0 контейнеров → НЕ warn
##     (детекция deployed — ТОТ ЖЕ источник, что R3 reconcile_projects: is_stub_ai_platform_yaml)
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
## @changes  2026-09-03 · live-drill (prod): check_proxy_connectivity — +deployed-детекция
##           (_project_is_deployed, R3-consistent) → deployed-проект с 0 контейнерами WARN
##           (раньше silent INFO + "FULLY CONVERGED"); exit-код не меняется
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from core.internal.bootstrap.converge.infra import (
    PROXY_NET,
    report_add,
    set_exit,
)
from core.internal.bootstrap.converge.projects import parse_projects_yaml
from core.internal.shared import docker_ops  # W1: docker info/network/ps/inspect примитивы (гейт docker_sole_path)

# R3-consistent deployed-детекция + база проектов (live-drill 2026-09-03): ai-platform.yaml
# real vs GENERATED-STUB — единый канон reconcile_projects (R3), НЕ вторая эвристика.
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE as PROJECTS_BASE
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

# R4-канон таймаута — прямой импорт из shared SoT (pyright reportPrivateLocalImportUsage)
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT as DOCKER_TIMEOUT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R4 — reconcile_networks
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_networks
## @purpose  Ensure proxy-net Docker network exists (runtime fallback).
##           For each running project container, verify proxy-net connectivity.
##           Does NOT auto-connect — that's the compose project's responsibility.
## @io       stdout/stderr: LDD logs [IMP:7-9]
##           side-effect: docker network create (if missing)
## @param node_yaml_path  Path to node.yaml for project list
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → fail unit, continue others
##   - proxy-net exists with wrong driver → WARN, don't recreate
##   - Concurrent docker network create → handled via inspect-after-create pattern
def reconcile_networks(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile Docker proxy-net and project container connectivity.

    Returns a drift entry dict with status: ok|skipped|mutated|warn|fail.
    """
    unit = "R4"
    logger.info("[IMP:8][converge][%s] START: reconcile_networks — ensuring proxy-net exists", unit)

    # ── Check docker daemon (W1: docker info — shared/docker_ops) ──
    docker_info_r = docker_ops.docker_info(timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping network reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── proxy-net: ensure exists (W1: docker network inspect/create — shared/docker_ops) ──
    net_inspect_r = docker_ops.docker_network_inspect_raw(PROXY_NET, timeout=DOCKER_TIMEOUT)
    if net_inspect_r.returncode != 0:
        # Network does not exist — create it
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD create: proxy-net (bridge)", unit)
            report_add(unit, "mutated", "proxy-net would be created")
            set_exit(1)
        else:
            logger.info("[IMP:8][converge][%s] Creating proxy-net (runtime fallback)", unit)
            if docker_ops.docker_network_create(PROXY_NET, "bridge", timeout=DOCKER_TIMEOUT):
                logger.info("[IMP:9][converge][%s] DONE: proxy-net created", unit)
                report_add(unit, "mutated", "proxy-net created")
                set_exit(1)
            else:
                logger.error("[IMP:10][converge][%s] FAIL: docker network create proxy-net failed", unit)
                report_add(unit, "fail", "proxy-net creation failed")
                set_exit(2)
                return {"unit": unit, "status": "fail", "detail": "proxy-net creation failed"}
    else:
        # Network exists — check driver
        try:
            # W11: json.loads → Any — каст к списку сетевых конфигов docker inspect
            net_info = cast(list[dict[str, str]], json.loads(net_inspect_r.stdout))
            current_driver = net_info[0].get("Driver", "unknown") if net_info else "unknown"
        except (json.JSONDecodeError, IndexError, KeyError):
            current_driver = "unknown"

        if current_driver != "bridge":
            logger.warning(
                "[IMP:9][converge][%s] WARN: proxy-net exists but driver=%s (expected=bridge)",
                unit,
                current_driver,
            )
            report_add(unit, "warn", f"proxy-net driver={current_driver} (expected=bridge)")
        else:
            logger.info("[IMP:9][converge][%s] SKIP: proxy-net already exists (driver=bridge, converged)", unit)

    # ── Check project containers for proxy-net connectivity ──
    check_proxy_connectivity(node_yaml_path, unit)

    logger.info("[IMP:9][converge][%s] DONE: networks reconciled", unit)
    return {"unit": unit, "status": "converged", "detail": "networks reconciled"}


# endregion FUNC_reconcile_networks


# region FUNC__project_is_deployed
## @purpose  R3-consistent deployed-детекция проекта для R4 check_proxy_connectivity
##           (live-drill 2026-09-03): проект СЧИТАЕТСЯ deployed, если ai-platform.yaml в
##           PROJECTS_BASE/⟨name⟩/ существует и НЕ является GENERATED-STUB — ТОТ ЖЕ источник,
##           что reconcile_projects (R3, converge/projects.py:167-172). stub/awaiting → False:
##           ожидающий CI-deploy проект с 0 контейнерами НЕ должен давать WARN (false-block
##           awaiting-deploy проектов исключён).
## @io       ⇥ pname: str → ⎋ bool (True = deployed)
## @complexity O(1) — is_file + чтение первой строки файла
## @invariants
##   - Никогда не raise: missing/OSError → False (is_stub_ai_platform_yaml graceful degradation)
##   - PROJECTS_BASE — модульный глобал (monkeypatch-точка unit-тестов, канон converge/*.py)
## @rationale Q: Почему только ai-platform.yaml, без docker-compose.yml присутствия (fallback-
##            эвристика из задания)? A: R3 (единый канон классификации деплоя) различает
##            awaiting/stub по real-vs-GENERATED-STUB ai-platform.yaml; второй, параллельный
##            источник (compose-файл + non-stub) дал бы R3/R4 РАСХОЖДЕНИЕ вердикта deployed.
##            docker-compose.yml появляется тем же CI-receive'ом, что и real ai-platform.yaml
##            (payload целиком) — отдельного окна "compose есть, yaml-якоря нет" не существует.
##            Fallback-эвристика отклонена.
def _project_is_deployed(pname: str) -> bool:
    """Return True when a node.yaml project is deployed per R3 semantics (real ai-platform.yaml)."""
    ai_platform = Path(PROJECTS_BASE) / pname / "ai-platform.yaml"
    return ai_platform.is_file() and not is_stub_ai_platform_yaml(str(ai_platform))


# endregion FUNC__project_is_deployed


# region FUNC_check_proxy_connectivity
## @purpose  Check each project's running containers for proxy-net connectivity
def check_proxy_connectivity(node_yaml_path: str, unit: str) -> None:
    """Check each project's running containers for proxy-net membership.

    For each project from node.yaml, find running containers and verify they
    are connected to proxy-net. Logs WARN for containers not connected and for
    deployed projects with no running containers (R4-honesty, live-drill 2026-09-03).
    """
    projects = parse_projects_yaml(node_yaml_path)
    if not projects:
        logger.info("[IMP:9][converge][%s] SKIP: No projects to check for proxy-net connectivity", unit)
        return

    for proj in projects:
        pname = proj.get("name", "")
        if not pname:
            continue

        # Find running containers for this project (W1: docker ps/inspect — shared/docker_ops)
        ps_r = docker_ops.docker_ps(
            filters=[f"label=com.docker.compose.project={pname}"],
            format="{{.Names}}",
            timeout=DOCKER_TIMEOUT,
        )
        containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()] if ps_r.returncode == 0 else []

        if not containers:
            # ⚠️ TRAP[BUG] · 2026-09-03 · P1 · deployed-проект с 0 контейнеров — silent rc=0
            # · Symptom: docker rm контейнера проекта → converge печатал "FULLY CONVERGED"
            #   (exit 0), контейнер оставался отсутствующим (live-drill на prod).
            # · Root: `if not containers: logger.info(...); continue` — INFO-ветка не отличала
            #   deployed-проект (контейнер ДОЛЖЕН быть) от awaiting/stub (контейнера ещё нет).
            # · Fix: deployed-детекция _project_is_deployed (R3-consistent) → WARN-запись +
            #   logger.warning [IMP:8]; exit-код НЕ меняется (reconcile контейнеров — канал
            #   deploy-project по дизайну; fail здесь false-block'ал бы awaiting-deploy проекты).
            # · Prevention: converge предупреждает о deployed-проекте без running-контейнера;
            #   самовосстановление остаётся за deploy-project (R9 работает с модулями, не проектами).
            if ps_r.returncode != 0:
                logger.info(
                    "[IMP:7][converge][%s] INFO: docker ps failed (rc=%d) for project %s — connectivity unverifiable",
                    unit,
                    ps_r.returncode,
                    pname,
                )
            elif _project_is_deployed(pname):
                warn_msg = (
                    f"Project {pname} deployed but no running containers "
                    "(converge R4 does not reconcile containers — deploy-project channel)"
                )
                logger.warning("[IMP:8][converge][%s] WARN: %s", unit, warn_msg)
                report_add(unit, "warn", warn_msg)
            else:
                logger.info(
                    "[IMP:7][converge][%s] INFO: No running containers for project %s (stub/awaiting deploy)",
                    unit,
                    pname,
                )
            continue

        for cname in containers:
            inspect_r = docker_ops.docker_inspect(
                cname,
                format="{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                timeout=DOCKER_TIMEOUT,
            )
            networks = inspect_r.stdout.strip() if inspect_r.returncode == 0 else ""
            if "proxy-net" not in networks:
                logger.warning(
                    "[IMP:9][converge][%s] WARN: Container %s (project %s) NOT connected to proxy-net",
                    unit,
                    cname,
                    pname,
                )
                report_add(unit, "warn", f"Container {cname} not connected to proxy-net")
            else:
                logger.info("[IMP:7][converge][%s] OK: Container %s connected to proxy-net", unit, cname)


# endregion FUNC_check_proxy_connectivity
