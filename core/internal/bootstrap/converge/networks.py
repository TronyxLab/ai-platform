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
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from typing import cast

from core.internal.bootstrap.converge.infra import (
    PROXY_NET,
    report_add,
    set_exit,
)
from core.internal.bootstrap.converge.projects import parse_projects_yaml
from core.internal.shared import docker_ops  # W1: docker info/network/ps/inspect примитивы (гейт docker_sole_path)

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


# region FUNC_check_proxy_connectivity
## @purpose  Check each project's running containers for proxy-net connectivity
def check_proxy_connectivity(node_yaml_path: str, unit: str) -> None:
    """Check each project's running containers for proxy-net membership.

    For each project from node.yaml, find running containers and verify they
    are connected to proxy-net. Logs WARN for containers not connected.
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
        containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]

        if not containers:
            logger.info("[IMP:7][converge][%s] INFO: No running containers for project %s", unit, pname)
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
