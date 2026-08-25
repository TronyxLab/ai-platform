#!/usr/bin/env python3
# GREP_SUMMARY: modules-healthcheck module-orchestration iterate-modules restart-loop docker-inspect module-interface deep-mode
# STRUCTURE: ▶ init → iterate module.yaml → ◇ install_type:docker → invoke liveness + restart-loop (State.Restarting/RestartCount>5) | ◇ install_type:system → invoke liveness | ◇ MODE=deep → invoke deep → ⊕ exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  Оркестратор healthcheck всех модулей: liveness для docker-модулей (через
##           shared/module_interface.invoke), liveness для system-модулей, MODE=deep — глубокая
##           диагностика. Python-порт modules-healthcheck.sh (DevPlan 118 E4).
## @scope    Вызывается из core/entrypoints/healthcheck.sh (make healthcheck) напрямую
##           (`python3 -m core.internal.healthcheck.modules_healthcheck`) — middle-hop
##           modules-healthcheck.sh схлопнут (DevPlan 173 W1.4).
## @invariants
##   - Итерирует core/modules/*/module.yaml — единственный source of truth состава модулей
##   - exit 0 = все модули healthy; exit 1 = хотя бы один unhealthy
##   - Dispatch через shared/module_interface.invoke (C5) — typed contract, не raw bash
##   - Restart-loop детекция: State.Restarting=true ИЛИ RestartCount > 5 → FAIL (вторичная проверка)
##   - Healthcheck-критерий по канону (D5): liveness-статус берётся из module healthcheck.sh
##     (check_docker_health), restart-loop — независимая docker inspect проверка
##   - Module observability пропускается (не модуль)
## @rationale Единый агрегирующий healthcheck для make healthcheck и CI-gate'ов. Strangler E4:
##            grep install_type → YAML-парсер; dispatch → shared/module_interface (C5).
## @changes  2026-08-02 | DevPlan 118 E4 — Created (Python-порт modules-healthcheck.sh, 127 LOC)
##           2026-08-13 | DevPlan 160 E1 — +invoke_fn/docker_inspect_fn DI (module_interface.invoke +
##                      docker_ops.docker_inspect параметрами; поведение без изменений)
##           2026-08-16 | DevPlan 173 W1.4 — middle-hop modules-healthcheck.sh удалён; entrypoint вызывает напрямую
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import yaml

# T2.6: ЕДИНАЯ константа restart-loop — канон-место watchdog.RESTART_LOOP_THRESHOLD.
#   watchdog stdlib-only (@invariant 1: cron без PYTHONPATH, TRAP[BUG] 142 W2) — импортировать
#   modules_healthcheck НЕ может; обратный импорт безопасен (module-level watchdog = чистый stdlib).
#   Значение 5 (RestartCount > 5 = CrashLoopBackOff) НЕ меняется.
from core.internal.healthcheck.watchdog import RESTART_LOOP_THRESHOLD
from core.internal.shared import docker_ops  # W1: docker inspect примитив (гейт docker_sole_path)
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.module_interface import invoke as invoke_module_interface
from core.internal.shared.node_yaml import NodeYaml

# DR-H4 fix: placement-awareness healthcheck (DevPlan 010 follow-up)
from core.internal.shared.placement import (
    load_placement,
    placement_node_relative_path,
    resolve_node_modules,
)
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT, HEALTHCHECK_CMD_TIMEOUT  # REF-0103

logger = logging.getLogger(__name__)

# Модули, не являющиеся сервисными (skip)
SKIP_MODULES = {"observability"}

# W11: DI-каналы (E1) — типизированные контракты вместо Callable[..., Any]
InvokeFn = Callable[..., tuple[bool, str]]
InspectFn = Callable[..., subprocess.CompletedProcess[str]]
RestartLoopFn = Callable[[str], bool]


# region FUNC_is_restart_loop
## @purpose  Restart-loop детекция: State.Restarting=true ИЛИ RestartCount > threshold.
## @io       ⇥ restarting: bool, restart_count: int, threshold: int = 5 → ⎋ bool
## @complexity O(1)
## @invariants
##   - restarting=true → loop (независимо от count)
##   - restart_count > threshold → loop (контейнер может быть "healthy" между рестартами)
def is_restart_loop(*, restarting: bool, restart_count: int, threshold: int = RESTART_LOOP_THRESHOLD) -> bool:
    """Return True if container is in a restart loop (Restarting or RestartCount > threshold)."""
    return bool(restarting) or restart_count > threshold


# endregion FUNC_is_restart_loop


# region FUNC_discover_module_yamls
## @purpose  Найти все module.yaml под core/modules/*/module.yaml (исключая SKIP_MODULES).
## @io       ⇥ modules_dir: Path → ⎋ list[Path]
## @complexity O(M) — M = число модулей
def discover_module_yamls(modules_dir: Path) -> list[Path]:
    """Discover core/modules/*/module.yaml files (excluding SKIP_MODULES)."""
    result: list[Path] = []
    if not modules_dir.is_dir():
        return result
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        if module_yaml.parent.name in SKIP_MODULES:
            continue
        result.append(module_yaml)
    return result


# endregion FUNC_discover_module_yamls


# region FUNC_read_install_type
## @purpose  Прочитать install_type из module.yaml (YAML-парсер вместо grep, E4).
## @io       ⇥ module_yaml: Path → ⎋ str ("docker" по умолчанию)
## @complexity O(1)
def read_install_type(module_yaml: Path) -> str:
    """Read install_type from module.yaml (default 'docker' — grep-семантика)."""
    try:
        raw_yaml = cast(
            "object", yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
        )  # W11: yaml → Any → object
        if isinstance(raw_yaml, dict):
            data = cast("dict[str, object]", raw_yaml)
            itype = data.get("install_type", "docker")
            if isinstance(itype, str):
                return itype
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[IMP:7][modules-healthcheck][parse] Cannot read %s: %s", module_yaml, exc)
    return "docker"


# endregion FUNC_read_install_type


# region FUNC_read_container_names
## @purpose  Прочитать container_name из docker-compose.base.yml (YAML, fallback — имя модуля).
## @io       ⇥ module_dir: Path → ⎋ list[str]
## @complexity O(1)
def read_container_names(module_dir: Path) -> list[str]:
    """Read container_name entries from docker-compose.base.yml (fallback: module name)."""
    compose = module_dir / "docker-compose.base.yml"
    names: list[str] = []
    if compose.is_file():
        try:
            names.extend(_compose_container_names(compose))
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[IMP:7][modules-healthcheck][parse] Cannot parse %s: %s", compose, exc)
    return names or [module_dir.name]


# endregion FUNC_read_container_names


# region FUNC__compose_container_names
## @purpose  container_name-ы из docker-compose.base.yml (YAML-граница; извлечено для TRY-лимита).
## @io       ⇥ compose: Path → ⎋ list[str]
## @complexity O(S) — S = сервисов
## @changes  2026-08-15 | DevPlan 170 W11 — извлечение из try (TRY-лимит) + object-граница YAML
def _compose_container_names(compose: Path) -> list[str]:
    """Parse container_name entries from docker-compose.base.yml services."""
    raw_yaml = cast("object", yaml.safe_load(compose.read_text(encoding="utf-8")) or {})
    data = cast("dict[str, object]", raw_yaml) if isinstance(raw_yaml, dict) else cast("dict[str, object]", {})
    services_raw = data.get("services")
    services = (
        cast("dict[str, object]", services_raw) if isinstance(services_raw, dict) else cast("dict[str, object]", {})
    )
    names: list[str] = []
    for svc_raw in services.values():
        if not isinstance(svc_raw, dict):
            continue
        container_name = cast("dict[str, object]", svc_raw).get("container_name")
        if container_name:
            names.append(str(container_name))
    return names


# endregion FUNC__compose_container_names


# region FUNC_check_restart_loop
## @purpose  Проверить restart-loop по docker inspect (State.Restarting + RestartCount).
## @io       ⇥ container: str, docker_inspect_fn (DI; None = docker_ops.docker_inspect) → ⎋ bool
## @complexity O(1) — 1-2 docker inspect subprocess
## @changes 2026-08-13 | E1 (160): +docker_inspect_fn DI (тесты передают fake вместо monkeypatch
##            core.internal.shared.docker_ops.subprocess.run)
def check_restart_loop(
    container: str,
    *,
    docker_inspect_fn: InspectFn | None = None,
) -> bool:
    """Inspect container State.Restarting/RestartCount → restart loop? (W1: shared/docker_ops)."""
    inspect = (docker_inspect_fn or docker_ops.docker_inspect)(
        container,
        format="{{.State.Restarting}}|{{.RestartCount}}",
        timeout=DOCKER_CMD_TIMEOUT,
    )
    if inspect.returncode != 0:
        logger.info(
            "[IMP:7][modules-healthcheck][restart] docker inspect %s exit=%d (container absent?)",
            container,
            inspect.returncode,
        )
        return False
    try:
        restarting_raw, count_raw = inspect.stdout.strip().split("|", 1)
        restarting = restarting_raw.strip().lower() == "true"
        restart_count = int(count_raw.strip() or "0")
    except ValueError:
        return False
    loop = is_restart_loop(restarting=restarting, restart_count=restart_count)
    if loop:
        logger.warning(
            "[IMP:9][modules-healthcheck][restart] FAIL: %s restart loop (restarting=%s, restarts=%d)",
            container,
            restarting,
            restart_count,
        )
    return loop


# endregion FUNC_check_restart_loop


# region FUNC_check_module
## @purpose  Прогнать healthcheck одного модуля: dispatch (liveness/deep) + restart-loop (docker).
## @io       ⇥ module_yaml: Path, mode: str ("deep"|""), invoke_fn: Callable | None (DI),
##           restart_loop_fn: Callable | None (DI) → ⎋ bool — healthy
## @complexity O(1) — invoke + docker inspect
## @changes 2026-08-13 | E1 (160): +invoke_fn/restart_loop_fn DI (тесты без monkeypatch
##            invoke_module_interface/check_restart_loop)
def check_module(
    module_yaml: Path,
    mode: str = "",
    *,
    invoke_fn: InvokeFn | None = None,
    restart_loop_fn: RestartLoopFn | None = None,
) -> bool:
    """Run healthcheck for one module. Returns True if healthy."""
    module = module_yaml.parent.name
    install_type = read_install_type(module_yaml)
    logger.info(
        "[IMP:8][modules-healthcheck][check] Checking %s (install_type=%s, mode=%s)",
        module,
        install_type,
        mode or "liveness",
    )

    # DI (E1): invoke_fn задан (тесты) → fake dispatch; None → канонический
    # invoke_module_interface (статический контракт-тест требует literal-вызов — E4 DRIFT-H7).
    # REF-0103: liveness/deep-инвок — HEALTHCHECK_CMD_TIMEOUT=60 (канон probe), не
    # унаследованный COMPOSE_UP_TIMEOUT=180 (3× окно поллинга на каждый модуль φ11).
    if mode == "deep":
        if invoke_fn is not None:
            ok, _err = invoke_fn(module, "healthcheck", "deep")
        else:
            ok, _err = invoke_module_interface(module, "healthcheck", "deep", timeout=HEALTHCHECK_CMD_TIMEOUT)
        if not ok:
            logger.warning("[IMP:9][modules-healthcheck][check] FAIL (deep): %s", module)
            return False
        logger.info("[IMP:8][modules-healthcheck][check] PASS (deep): %s", module)
        return True

    if invoke_fn is not None:
        ok, _err = invoke_fn(module, "healthcheck", "liveness")
    else:
        ok, _err = invoke_module_interface(module, "healthcheck", "liveness", timeout=HEALTHCHECK_CMD_TIMEOUT)
    if not ok:
        logger.warning("[IMP:9][modules-healthcheck][check] FAIL (liveness): %s", module)
        return False

    # Restart-loop detection — вторичная проверка (только docker-модули)
    if install_type == "docker":
        for container in read_container_names(module_yaml.parent):
            loop = restart_loop_fn(container) if restart_loop_fn is not None else check_restart_loop(container)
            if loop:
                logger.warning(
                    "[IMP:9][modules-healthcheck][restart] FAIL: %s → %s restart loop (secondary check)",
                    module,
                    container,
                )
                return False

    logger.info("[IMP:8][modules-healthcheck][check] PASS (liveness): %s", module)
    return True


# endregion FUNC_check_module


# region FUNC__resolve_enabled_modules
def _resolve_enabled_modules() -> set[str] | None:
    """Разрешить enabled-модули ноды: node.yaml ∩ placement (DR-H4 fix); None → фильтр выключен.

    ## @purpose  Честный healthcheck на минимальных нодах: только enabled-модули (релиз 1.0.0),
    ##           а при наличии placement.yaml — ТОЛЬКО модули, размещённые на ЭТОЙ ноде
    ##           (placement авторитетен, DevPlan 010 §1.2; DR-H4 fix: раньше читался только
    ##           node.yaml → multi-node healthcheck проверял чужие модули / пропускал локальные).
    ## @io       ⇥ None → ⎋ set[str] | None
    ## @complexity  O(1) — одно чтение node.yaml (+ placement при наличии)
    ## @invariants
    ##   - node.yaml: NODE_YAML env → /opt/node-configs/<NODE_NAME>/node.yaml (нода) → None
    ##   - modules[] с enabled: true → set; нечитаемый/отсутствующий yaml → None (все модули)
    ##   - placement.yaml = parent(node.yaml dir).parent/<context>/placement.yaml (канон деривации
    ##     deploy_orchestrator._placement_for_node); отсутствует → node.yaml-фильтр без изменений
    ##     (single-node байт-совместимость)
    ##   - node_name вне placement → ConfigValidationError ловится, IMP:7 warning с repair-подсказкой,
    ##     fallback на node.yaml-фильтр (diagnostic verb — не деплой, fail-open честнее crash'а)
    """
    import os

    node_yaml = os.environ.get("NODE_YAML", "")
    if not node_yaml:
        node_name_env = os.environ.get("NODE_NAME", "")
        if node_name_env:
            candidate = Path(f"/opt/node-configs/{node_name_env}/node.yaml")
            if candidate.is_file():
                node_yaml = str(candidate)
    if not node_yaml or not Path(node_yaml).is_file():
        return None
    try:
        # node.yaml ТОЛЬКО через NodeYaml-фасад (gate node_yaml_single_source — DRIFT-088-7)
        ny = NodeYaml(node_yaml)
        modules = ny.get_modules()
    except (ConfigNotFoundError, ConfigParseError, OSError):
        return None
    enabled = {str(m.get("name")) for m in modules if isinstance(m, dict) and m.get("enabled") is not False}

    # ── DR-H4 fix: placement-awareness — пересечение с размещением этой ноды ──
    context = ""
    node_name = ""
    try:
        context = ny.get_context()
        node_name = str(ny.get("node.name", default="") or "")
    except (ConfigNotFoundError, ConfigParseError, OSError):
        context = ""
    if context and node_name:
        # DevPlan 16 T1.B: единый резолвер (была локальная деривация parent.parent/context)
        placement_path = placement_node_relative_path(node_yaml, context)
        try:
            placement = load_placement(placement_path)
        except (ConfigValidationError, ConfigNotFoundError, ConfigParseError) as exc:
            logger.warning(
                "[IMP:7][modules-healthcheck][enabled] unreadable placement %s (%s) — falling back to node.yaml filter",
                placement_path,
                exc,
            )
            placement = None
        if placement is not None:
            placed: set[str] | None
            try:
                placed = set(resolve_node_modules(placement, node_name))
            except ConfigValidationError as exc:
                logger.warning(
                    "[IMP:7][modules-healthcheck][enabled] node %r not in placement (%s) — "
                    "remove from node.yaml or add to placement.yaml; falling back to node.yaml filter",
                    node_name,
                    exc,
                )
                placed = None  # diagnostic fail-open, не деплой (healthcheck ≠ orchestrate)
            if placed is not None:
                filtered = enabled & placed
                logger.info(
                    "[IMP:9][modules-healthcheck][enabled] placement-aware filter: "
                    "node.yaml=%d ∩ placed(%s)=%d → %d checked module(s)",
                    len(enabled),
                    node_name,
                    len(placed),
                    len(filtered),
                )
                return filtered

    logger.info("[IMP:8][modules-healthcheck][enabled] node.yaml filter: %d enabled module(s)", len(enabled))
    return enabled


# endregion FUNC__resolve_enabled_modules


# region FUNC_run_healthchecks
## @purpose  Полный прогон: итерировать модули, собрать healthy-флаг.
## @io       ⇥ modules_dir: Path, mode: str = "", invoke_fn: Callable | None (DI),
##           restart_loop_fn: Callable | None (DI), enabled_modules: set[str] | None → ⎋ bool
## @complexity O(M) — M = модулей
## @changes 2026-08-13 | E1 (160): +invoke_fn/restart_loop_fn DI (проброс в check_module)
##           2026-08-16 | релиз 1.0.0: +enabled_modules (node.yaml-фильтр минимальных нод)
def run_healthchecks(
    modules_dir: Path,
    mode: str = "",
    *,
    invoke_fn: InvokeFn | None = None,
    restart_loop_fn: RestartLoopFn | None = None,
    enabled_modules: set[str] | None = None,
) -> bool:
    """Run healthchecks for modules (enabled-фильтр при наличии). Returns True if all healthy."""
    module_yamls = discover_module_yamls(modules_dir)
    all_healthy = True
    for module_yaml in module_yamls:
        if enabled_modules is not None and module_yaml.parent.name not in enabled_modules:
            logger.info("[IMP:8][modules-healthcheck][skip] %s — not enabled in node.yaml", module_yaml.parent.name)
            continue
        if not check_module(module_yaml, mode=mode, invoke_fn=invoke_fn, restart_loop_fn=restart_loop_fn):
            all_healthy = False
    if all_healthy:
        logger.info("[IMP:9][modules-healthcheck][summary] ALL MODULES HEALTHY")
    else:
        logger.warning("[IMP:9][modules-healthcheck][summary] SOME MODULES UNHEALTHY")
    return all_healthy


# endregion FUNC_run_healthchecks


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.healthcheck.modules_healthcheck [MODE=deep]`.

    ▶ ┌argv (optional MODE=deep)┐ → ○ enabled-фильтр (node.yaml, если доступен) → ○ run_healthchecks → ⎋ exit 0|1

    ## @invariants
    ##   - node.yaml доступен (NODE_YAML env или /opt/node-configs/<NODE_NAME>/node.yaml)
    ##     → проверяются ТОЛЬКО enabled-модули ноды (минимальные ноды без backup-cron/clickhouse/
    ##     hermes-agent не дают ложных FAIL) — релиз 1.0.0
    ##   - node.yaml недоступен (dev-машина) → все модули (прежнее поведение)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in {"--help", "-h"}:
        print("Usage: modules_healthcheck [MODE=deep]")
        print()
        print("Iterate platform module healthcheck scripts and run them.")
        print("Default: liveness check for all modules (или только enabled-модули ноды,")
        print("если node.yaml доступен: NODE_YAML env или /opt/node-configs/<NODE_NAME>/node.yaml).")
        print("MODE=deep: run module-specific deep diagnostics.")
        print()
        print("Returns 0 if all pass, 1 if any fail.")
        return 0
    modules_dir = Path(__file__).resolve().parents[2] / "modules"  # core/modules
    enabled: set[str] | None = _resolve_enabled_modules()
    return 0 if run_healthchecks(modules_dir, mode=mode, enabled_modules=enabled) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
