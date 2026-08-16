#!/usr/bin/env python3
# GREP_SUMMARY: observability, monitoring, logging, metrics, container-cleanup, pre-deploy, compose-config-services, E1, docker-orchestrator-decomposition
# STRUCTURE: ▶ cleanup_observability_containers ┌compose_file┐ → ◇ compose config --services → ◇ docker ps -a → ○ per-service: ◇ name in ps? → ⊕ docker stop+rm → ⎋ None (graceful)
# region MODULE_CONTRACT
## @purpose  Observability module pre-deploy phase — экстракция из docker_orchestrator.py
##           (DevPlan 119 E1, _cleanup_observability_containers): очистка pre-existing
##           контейнеров сервисов observability (monitoring/logging/metrics) перед
##           compose up — предотвращает name-conflict при re-deploy.
## @scope    bootstrap/deploy — вызывается docker_orchestrator.deploy_docker_module
##           (module_name == "observability") через dispatch-таблицу фаз (E1).
## @invariants
##   1. compose config --services через shared docker_compose_config (гейт docker_sole_path)
##   2. docker stop/rm через subprocess (DOCKER_CMD_TIMEOUT / DOCKER_STOP_TIMEOUT)
##   3. Все сбои non-fatal (WARN) — очистка best-effort, не блокирует деплой
##   4. str/bytes нормализация stdout (TRAP[BUG] 2026-07-22 type safety)
## @rationale E1 (DevPlan 119, AUDIT-2 M7): deploy_docker_module 195 LOC CC=25 разбивается по
##   фазам с dispatch-таблицей. Observability-фаза — отдельный модуль (аналогично
##   hermes_workflow.py из D1). Изолированное тестирование фазы.
## @changes  2026-08-02 | DevPlan 119 E1 — экстракция из docker_orchestrator.py (_cleanup_observability_containers)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

from core.internal.shared import docker_ops
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_cleanup_observability_containers
## @purpose  Clean up pre-existing containers for observability module services
##           before compose up (prevents name conflict on re-deploy).
## @io       ⇥ compose_file: Path
##           ⎋ None (side-effect: docker stop + rm for each service container)
## @complexity 2 — docker compose config --services + docker ps + per-service stop/rm
## @invariants
##   - compose config --services через shared (sole path, DevPlan 116 B5 T4)
##   - docker ps/stop/rm через shared/docker_ops (W1, non-fatal; bytes→str нормализация внутри)
##   - Сбой любого шага — WARN, не raise (best-effort: DEPLOY_BEST_EFFORT policy)
def cleanup_observability_containers(compose_file: Path) -> None:
    """Clean up pre-existing observability service containers (best-effort)."""
    logger.info("[IMP:7][_cleanup_observability_containers][start] Cleaning observability containers")
    # ── Get services from compose config (shared — sole path, DevPlan 116 B5 T4) ──
    svc_result = _shared_docker_compose_config(
        str(compose_file.parent),
        compose_args=["-f", str(compose_file)],
        flags=["--services"],
    )
    if svc_result.returncode != 0:
        logger.warning("[IMP:5][_cleanup_observability_containers][config_fail] compose config --services failed")
        return
    # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety (см. _cleanup_stale_container)
    svc_stdout = svc_result.stdout
    if isinstance(svc_stdout, bytes):
        svc_stdout = svc_stdout.decode("utf-8")
    services = [s.strip() for s in svc_stdout.splitlines() if s.strip()]

    # ── Get all container names (W1: shared/docker_ops — non-fatal, [] на сбое) ──
    all_names = docker_ops.ps_container_names(all=True, timeout=DOCKER_CMD_TIMEOUT)

    for cname in services:
        if any(re.search(re.escape(cname), name) for name in all_names):
            logger.info("[IMP:8][_cleanup_observability_containers][clean] Stopping/removing container: %s", cname)
            # W1: docker stop/rm — shared/docker_ops (non-fatal; сбой → WARN внутри слоя)
            _ = docker_ops.docker_stop(cname)
            _ = docker_ops.docker_rm(cname)


# endregion FUNC_cleanup_observability_containers
