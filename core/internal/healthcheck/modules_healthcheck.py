#!/usr/bin/env python3
# GREP_SUMMARY: modules-healthcheck module-orchestration iterate-modules restart-loop docker-inspect module-interface deep-mode
# STRUCTURE: ▶ init → iterate module.yaml → ◇ install_type:docker → invoke liveness + restart-loop (State.Restarting/RestartCount>5) | ◇ install_type:system → invoke liveness | ◇ MODE=deep → invoke deep → ⊕ exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose  Оркестратор healthcheck всех модулей: liveness для docker-модулей (через
##           shared/module_interface.invoke), liveness для system-модулей, MODE=deep — глубокая
##           диагностика. Python-порт modules-healthcheck.sh (DevPlan 118 E4).
## @scope    Вызывается из core/entrypoints/healthcheck.sh (make healthcheck) через тонкий фасад
##           core/internal/healthcheck/modules-healthcheck.sh.
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
## @see      core/internal/healthcheck/modules-healthcheck.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from core.internal.shared import docker_ops  # W1: docker inspect примитив (гейт docker_sole_path)
from core.internal.shared.module_interface import invoke as invoke_module_interface
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# Restart-loop threshold: RestartCount > 5 → restart loop (канон modules-healthcheck.sh)
RESTART_LOOP_THRESHOLD = 5
# Модули, не являющиеся сервисными (skip)
SKIP_MODULES = {"observability"}


# region FUNC_is_restart_loop
## @purpose  Restart-loop детекция: State.Restarting=true ИЛИ RestartCount > threshold.
## @io       ⇥ restarting: bool, restart_count: int, threshold: int = 5 → ⎋ bool
## @complexity O(1)
## @invariants
##   - restarting=true → loop (независимо от count)
##   - restart_count > threshold → loop (контейнер может быть "healthy" между рестартами)
def is_restart_loop(restarting: bool, restart_count: int, threshold: int = RESTART_LOOP_THRESHOLD) -> bool:
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
    """Read install_type from module.yaml (default 'docker' — legacy grep-семантика)."""
    try:
        data = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
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
            data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
            services = data.get("services", {}) if isinstance(data, dict) else {}
            names.extend(
                str(svc["container_name"])
                for svc in services.values()
                if isinstance(svc, dict) and svc.get("container_name")
            )
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[IMP:7][modules-healthcheck][parse] Cannot parse %s: %s", compose, exc)
    return names or [module_dir.name]


# endregion FUNC_read_container_names


# region FUNC_check_restart_loop
## @purpose  Проверить restart-loop по docker inspect (State.Restarting + RestartCount).
## @io       ⇥ container: str → ⎋ bool — True если restart loop
## @complexity O(1) — 1-2 docker inspect subprocess
def check_restart_loop(container: str) -> bool:
    """Inspect container State.Restarting/RestartCount → restart loop? (W1: shared/docker_ops)."""
    # W1 (DevPlan 128): docker inspect — shared/docker_ops (non-fatal, never raises)
    inspect = docker_ops.docker_inspect(
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
    loop = is_restart_loop(restarting, restart_count)
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
## @io       ⇥ module_yaml: Path, mode: str ("deep"|"") → ⎋ bool — healthy
## @complexity O(1) — invoke + docker inspect
def check_module(module_yaml: Path, mode: str = "") -> bool:
    """Run healthcheck for one module. Returns True if healthy."""
    module = module_yaml.parent.name
    install_type = read_install_type(module_yaml)
    logger.info(
        "[IMP:8][modules-healthcheck][check] Checking %s (install_type=%s, mode=%s)",
        module,
        install_type,
        mode or "liveness",
    )

    if mode == "deep":
        ok, _err = invoke_module_interface(module, "healthcheck", "deep")
        if not ok:
            logger.warning("[IMP:9][modules-healthcheck][check] FAIL (deep): %s", module)
            return False
        logger.info("[IMP:8][modules-healthcheck][check] PASS (deep): %s", module)
        return True

    ok, _err = invoke_module_interface(module, "healthcheck", "liveness")
    if not ok:
        logger.warning("[IMP:9][modules-healthcheck][check] FAIL (liveness): %s", module)
        return False

    # Restart-loop detection — вторичная проверка (только docker-модули)
    if install_type == "docker":
        for container in read_container_names(module_yaml.parent):
            if check_restart_loop(container):
                logger.warning(
                    "[IMP:9][modules-healthcheck][restart] FAIL: %s → %s restart loop (secondary check)",
                    module,
                    container,
                )
                return False

    logger.info("[IMP:8][modules-healthcheck][check] PASS (liveness): %s", module)
    return True


# endregion FUNC_check_module


# region FUNC_run_healthchecks
## @purpose  Полный прогон: итерировать все модули, собрать healthy-флаг.
## @io       ⇥ modules_dir: Path, mode: str = "" → ⎋ bool — all healthy
## @complexity O(M) — M = модулей
def run_healthchecks(modules_dir: Path, mode: str = "") -> bool:
    """Run healthchecks for all modules. Returns True if all healthy."""
    module_yamls = discover_module_yamls(modules_dir)
    all_healthy = True
    for module_yaml in module_yamls:
        if not check_module(module_yaml, mode=mode):
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

    ▶ ┌argv (optional MODE=deep)┐ → ○ run_healthchecks → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode in ("--help", "-h"):
        print("Usage: modules_healthcheck [MODE=deep]")
        return 0
    modules_dir = Path(__file__).resolve().parents[2] / "modules"  # core/modules
    return 0 if run_healthchecks(modules_dir, mode=mode) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
