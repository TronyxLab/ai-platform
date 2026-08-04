#!/usr/bin/env python3
# GREP_SUMMARY: converge-runtime, reconcile-runtime-state, r9, container-state, compose-up, self-heal, cooldown
# STRUCTURE: ▶ docker info → ◇ global cooldown (last_healed < 3 runs)? → ○ for each docker module: ⚡ resolve_container_name → ⚡ get_container_state → ◇ in BAD_DOCKER_STATES? → ⚡ compose up -d (shared, COMPOSE_UP_TIMEOUT) → ⊕ cooldown record → ⎋ drift entry {R9}
# region MODULE_CONTRACT
## @purpose  R9 reconcile_runtime_state — Docker container state check + compose up -d self-heal +
##           cooldown tracking (flapping-защита). Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/runtime.py: reconcile_runtime_state, resolve_container_name, get_container_state,
##           load_cooldown, save_cooldown. Вызывается оркестратором reconciler.py.
## @invariants
##   - Self-heal ТОЛЬКО через docker compose up -d (shared/docker_compose, B5 T6/D8) — НЕ docker restart
##   - Cooldown: контейнер, вылеченный в течение 3 последних run'ов → global cooldown (skip healing)
##   - BAD_DOCKER_STATES: exited/restarting/dead/unhealthy/paused
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.internal.bootstrap.converge.infra import (
    BAD_DOCKER_STATES,
    COOLDOWN_FILE,
    DOCKER_TIMEOUT,
    report_add,
    set_exit,
)
from core.internal.bootstrap.converge.volumes import parse_node_modules_yaml
from core.internal.shared import docker_ops  # W1: docker ps/inspect/info примитивы (гейт docker_sole_path)
from core.internal.shared.compose_files import resolve_compose_file
from core.internal.shared.docker_compose import docker_compose_up as _shared_docker_compose_up
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC_resolve_container_name
## @purpose  Get container name(s) for a module via docker ps --filter.
## @param module_name  Module name (used as name filter)
## @return  List of container names matching the module
def resolve_container_name(module_name: str) -> list[str]:
    """Resolve container names for a module via docker ps --filter name.

    Returns list of container names. Empty list if no matching containers.
    """
    # W1: docker ps — shared/docker_ops (non-fatal)
    ps_r = docker_ops.docker_ps(
        filters=[f"name={module_name}"],
        format="{{.Names}}",
        timeout=DOCKER_TIMEOUT,
    )
    if ps_r.returncode != 0:
        logger.warning("[IMP:8][resolve_container_name] docker ps failed for module %s", module_name)
        return []
    containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]
    logger.info("[IMP:7][resolve_container_name] Module %s → containers: %s", module_name, containers)
    return containers


# endregion FUNC_resolve_container_name


# region FUNC_get_container_state
## @purpose  Get Docker container state via docker inspect.
## @param container_name  Container name to inspect
## @return  State string (e.g. "running", "exited"). "unknown" on failure.
def get_container_state(container_name: str) -> str:
    """Get container state via docker inspect --format '{{.State.Status}}'."""
    # W1: docker inspect — shared/docker_ops (non-fatal)
    inspect_r = docker_ops.docker_inspect(
        container_name,
        format="{{.State.Status}}",
        timeout=DOCKER_TIMEOUT,
    )
    if inspect_r.returncode != 0:
        logger.warning("[IMP:8][get_container_state] docker inspect failed for %s", container_name)
        return "unknown"
    state = inspect_r.stdout.strip()
    logger.info("[IMP:7][get_container_state] Container %s → state=%s", container_name, state)
    return state


# endregion FUNC_get_container_state


# region FUNC_load_cooldown
## @purpose  Load cooldown tracking data from JSON file.
## @return  Dict with structure: {"run": int, "containers": {name: {"last_healed_run": int}}}
def load_cooldown() -> dict:
    """Load cooldown tracking data from COOLDOWN_FILE.

    Returns default structure if file is missing or corrupted.
    """
    filepath = Path(COOLDOWN_FILE)
    if filepath.is_file():
        try:
            data = json.loads(filepath.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[IMP:8][load_cooldown] Failed to read cooldown file: %s", exc)
    return {"run": 0, "containers": {}}


# endregion FUNC_load_cooldown


# region FUNC_save_cooldown
## @purpose  Save cooldown tracking data to JSON file.
## @param data  Dict with run counter and container cooldown entries
def save_cooldown(data: dict) -> None:
    """Save cooldown tracking data to COOLDOWN_FILE."""
    filepath = Path(COOLDOWN_FILE)
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(data, indent=2))
        logger.info("[IMP:8][save_cooldown] Cooldown saved to %s", COOLDOWN_FILE)
    except OSError as exc:
        logger.warning("[IMP:8][save_cooldown] Failed to save cooldown: %s", exc)


# endregion FUNC_save_cooldown


# region FUNC_reconcile_runtime_state
## @purpose  Reconcile Docker container runtime state. For each docker module,
##           inspect container state. If state is bad (exited, restarting, dead,
##           unhealthy, paused), self-heal via `docker compose up -d`. Cooldown
##           tracking prevents repeated self-heal of flapping containers.
## @complexity O(N×C) — N=modules, C=containers per module
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: docker compose up -d (self-heal), cooldown file update
## @param node_yaml_path  Path to node.yaml
## @param modules_dir     Path to modules/ directory
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → status=fail
##   - All containers running → status=converged
##   - Container exited → self-heal via docker compose up -d (NOT docker restart)
##   - Container in cooldown (healed within last 3 runs) → skip self-heal
def reconcile_runtime_state(
    node_yaml_path: str,
    modules_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict:
    """Reconcile Docker container runtime state — self-heal via compose up -d.

    Returns a drift entry dict with status: ok|skipped|converged|mutated|warn|fail.
    """
    unit = "R9"
    logger.info("[IMP:8][converge][%s] START: reconcile_runtime_state — checking container states", unit)

    # ── Check docker daemon (W1: docker info — shared/docker_ops) ──
    docker_info_r = docker_ops.docker_info(timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping runtime reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse modules from node.yaml ──
    modules = parse_node_modules_yaml(node_yaml_path)
    if not modules:
        logger.info("[IMP:9][converge][%s] SKIP: No modules defined in node.yaml", unit)
        report_add(unit, "skipped", "No modules defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No modules defined in node.yaml"}

    # ── Load cooldown data ──
    cooldown = load_cooldown()
    current_run = cooldown.get("run", 0) + 1
    cooldown["run"] = current_run
    if "containers" not in cooldown:
        cooldown["containers"] = {}

    # ── Check for global cooldown (any container healed in last 3 runs) ──
    global_cooldown = False
    for cname, cdata in cooldown["containers"].items():
        last_healed = cdata.get("last_healed_run", 0)
        if last_healed > 0 and current_run - last_healed < 3:
            global_cooldown = True
            logger.info(
                "[IMP:7][converge][%s] Global cooldown active — %s healed at run %d (diff=%d < 3)",
                unit,
                cname,
                last_healed,
                current_run - last_healed,
            )
            break

    if global_cooldown:
        logger.info(
            "[IMP:9][converge][%s] COOLDOWN: Previously healed containers still in cooldown — skipping all healing",
            unit,
        )
        report_add(unit, "converged", "In cooldown — previously healed containers")
        return {"unit": unit, "status": "converged", "detail": "Cooldown active, no healing"}

    modules_dir_path = Path(modules_dir)
    healed = 0
    errors = 0

    for mod in modules:
        mod_name = mod.get("name", "")
        if not mod_name or not mod.get("enabled", True):
            continue

        # Check if module has a compose file (docker module) — DevPlan 118 A2:
        # единый канон shared/compose_files.resolve_compose_file (порядок включает docker-compose.base.yml —
        # реальные модули имеют ТОЛЬКО base-compose; старый кортеж их не видел → converge пропускал все docker-модули)
        mod_dir = modules_dir_path / mod_name
        compose_file = resolve_compose_file(str(mod_dir))

        if not compose_file:
            logger.info("[IMP:7][converge][%s] %s has no compose file — skipping (not docker)", unit, mod_name)
            continue

        logger.info("[IMP:7][converge][%s] Checking module: %s", unit, mod_name)

        # Get container names for this module
        containers = resolve_container_name(mod_name)
        if not containers:
            logger.info("[IMP:7][converge][%s] No running containers for module %s", unit, mod_name)
            continue

        needs_heal = False
        for cname in containers:
            state = get_container_state(cname)
            if state in BAD_DOCKER_STATES:
                logger.warning(
                    "[IMP:9][converge][%s] Container %s state=%s — needs self-heal",
                    unit,
                    cname,
                    state,
                )
                needs_heal = True
            elif state == "running":
                logger.info("[IMP:7][converge][%s] Container %s OK (running)", unit, cname)

        if not needs_heal:
            logger.info("[IMP:9][converge][%s] Module %s all containers OK", unit, mod_name)
            continue

        # ── Self-heal via docker compose up -d ──
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD heal module %s via docker compose up -d", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: would be restarted via compose up -d")
            healed += 1
            set_exit(1)
            continue

        logger.info("[IMP:8][converge][%s] Self-healing module %s via docker compose up -d", unit, mod_name)
        # T6 (DevPlan 116 B5, D8): shared docker_compose_up — sole path; timeout COMPOSE_UP_TIMEOUT=180
        # (DOCKER_TIMEOUT=30 был занижен для up с пуллом образов — стандартизация на канон)
        if _shared_docker_compose_up(
            str(compose_file.parent),
            timeout=COMPOSE_UP_TIMEOUT,
            compose_args=["-f", str(compose_file)],
        ):
            logger.info("[IMP:9][converge][%s] Module %s healed successfully", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: restarted via compose up -d")
            healed += 1
            set_exit(1)
            # Record heal in cooldown
            cooldown["containers"][mod_name] = {"last_healed_run": current_run}
        else:
            logger.error("[IMP:10][converge][%s] Failed to heal module %s via compose up -d", unit, mod_name)
            report_add(unit, "fail", f"{mod_name}: compose up -d failed")
            errors += 1
            set_exit(2)

    # ── Save cooldown data ──
    save_cooldown(cooldown)

    # ── Final report ──
    if healed > 0:
        status = "mutated"
        detail = f"{healed} module(s) healed via compose up -d"
    elif errors > 0:
        status = "fail"
        detail = f"{errors} module(s) had errors"
    else:
        status = "converged"
        detail = "All containers running"

    logger.info("[IMP:9][converge][%s] DONE: healed=%d errors=%d", unit, healed, errors)
    return {"unit": unit, "status": status, "detail": detail}


# endregion FUNC_reconcile_runtime_state
