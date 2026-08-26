#!/usr/bin/env python3
# GREP_SUMMARY: converge-volumes, reconcile-volumes, r7, named-volumes, detect-only, o7, docker-compose-config
# STRUCTURE: ▶ docker info → ◇ parse modules → ○ for each compose: ⚡ docker_compose_config --format json → ⊕ extract_named_volumes (type=volume) → ◇ docker volume inspect? → ⎋ drift entry {R7} (detect-only, O7)
# region MODULE_CONTRACT
## @purpose  R7 reconcile_volumes — detect-only named volume check (O7 invariant: NEVER creates).
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/volumes.py: reconcile_volumes, parse_node_modules_yaml, extract_named_volumes.
##           Вызывается оркестратором reconciler.py.
## @invariants
##   - O7: detect-only — docker volume create НИКОГДА не вызывается
##   - Bind mounts (type=bind) исключаются из проверки
##   - docker compose config — через shared/docker_compose.docker_compose_config (sole path, B5 T6)
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast

from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.converge.infra import report_add, set_exit
from core.internal.shared import (
    deploy_paths,  # 142 W2: secrets.env → persistent /var/lib/platform/run
    docker_ops,  # W1: docker info/volume inspect примитивы (гейт docker_sole_path)
)
from core.internal.shared.compose_files import resolve_compose_file
from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml

# R7-канон таймаута — прямой импорт из shared SoT (pyright reportPrivateLocalImportUsage)
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT as DOCKER_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC__preview_note
def _preview_note(unit: str, *, dry_run: bool, report_only: bool, plan: str) -> bool:
    """Печатать план dry-run/report-only; вернуть True если прогон preview-режима (AI-0031).

    ## @purpose  Общая ветка флагов для detect/verify-only юнитов converge — C901
    ##            основных функций не растёт.
    """
    if not (dry_run or report_only):
        return False
    logger.info("[IMP:9][converge][%s] DRY-RUN/REPORT-ONLY: %s", unit, plan)
    return True


# endregion FUNC__preview_note

# pyright: reportUnusedParameter = false
# R7-контракт оркестратора: reconcile_volumes обязан принимать dry_run/report_only (единая сигнатура
# R-юнитов); параметры не используются — detect-only (O7); ruff: ignore[ARG001] — тот же контракт
# (tool-conflict: pyright требует pyright-лидирующий комментарий, ruff — ruff-лидирующий).


# region FUNC_parse_node_modules_yaml
## @purpose  Parse enabled modules from node.yaml for docker module detection.
##           Returns list of dicts with name, enabled status.
## @param node_yaml_path  Path to node.yaml
## @return  List of module dicts (name, enabled). Empty list on parse error.
def parse_node_modules_yaml(node_yaml_path: str) -> list[dict[str, object]]:
    """Parse enabled modules from node.yaml.

    Supports dict entries (with name/enabled keys).
    Returns empty list on parse error or missing section.
    """
    # Импорты исключений/NodeYaml подняты на уровень модуля (reportPossiblyUnboundVariable);
    # ruff: ignore[PLW0717] — тело try >5 операторов (длинный parse-блок) — извлечение неразумно
    try:
        modules_raw = NodeYaml(node_yaml_path).get_list("modules")
        out: list[dict[str, object]] = []
        for m in modules_raw:
            if isinstance(m, dict):
                # W11-G1 cross-file: NodeYaml.get_list → list[object] — каст элемента к dict-контракту
                mdict = cast(dict[str, object], m)
                out.append({
                    "name": mdict.get("name", ""),
                    "enabled": mdict.get("enabled", True),
                })
            elif isinstance(m, str):
                out.append({"name": m, "enabled": True})
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][parse_node_modules_yaml] Failed to parse modules from %s: %s", node_yaml_path, exc)
        return []
    else:
        return out


# endregion FUNC_parse_node_modules_yaml


# region FUNC_extract_named_volumes
## @purpose  Extract named volume source names from docker compose config JSON.
##           Filters out bind mounts (type=bind). Only returns named volumes
##           (type=volume or no type specified).
## @param compose_json  Parsed docker compose config dict (from --format json)
## @return  List of named volume source names (deduplicated)
## @invariant O7 — detect-only, never create volumes
def extract_named_volumes(compose_json: dict[str, object]) -> list[str]:
    """Extract named volume source names from compose config JSON.

    Only returns volumes with type: volume or no type (not bind mounts).
    """
    volumes_set: set[str] = set()
    services = compose_json.get("services", {})
    if not isinstance(services, dict):
        return list(volumes_set)

    # W11: json.loads → Any — каст элементов compose-config JSON к строковым полям
    for svc_config in cast(dict[str, dict[str, object]], services).values():
        vol_entries = svc_config.get("volumes", [])
        if not isinstance(vol_entries, list):
            continue
        for entry in cast(list[object], vol_entries):
            if not isinstance(entry, dict):
                continue
            ventry = cast(dict[str, object], entry)
            vol_type = cast(str, ventry.get("type", "volume"))  # default type is volume
            vol_source = cast(str, ventry.get("source", ""))
            if vol_type == "volume" and vol_source:
                volumes_set.add(vol_source)

    return list(volumes_set)


# endregion FUNC_extract_named_volumes


# region FUNC_reconcile_volumes
## @purpose  Detect-only volume reconciliation (O7 invariant). Reads node.yaml
##           to find docker modules, inspects compose config for named volumes,
##           and verifies they exist via `docker volume inspect`. NEVER creates
##           volumes — only reports missing ones.
## @complexity O(N×M×V) — N=modules, M=volumes per module, V=volume inspect
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: subprocess calls to docker compose config + volume inspect
## @param node_yaml_path  Path to node.yaml
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @invariant O7 — detect-only, NEVER docker volume create
## @edge-cases
##   - Docker daemon unavailable → status=fail, no further checks
##   - Module without canonical compose file (shared/compose_files) → skipped (not a docker module)
##   - All volumes exist → status=converged
##   - One or more volumes missing → status=warn, never create
##   - Bind mounts (type=bind) → excluded from inspection
def reconcile_volumes(
    node_yaml_path: str,
    *,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile Docker named volumes — detect-only (O7 invariant).

    Returns a drift entry dict with status: ok|skipped|converged|warn|fail.

    AI-0031 (DevPlan 17 T7.1): флаги подключены по образцу networks/runtime — юнит
    detect-only (мутаций нет), при dry_run/report_only печатается план («мутаций
    не будет») и detail помечается [dry-run] для отчёта оркестратора.
    """
    unit = "R7"
    preview = bool(dry_run or report_only)
    if preview:
        logger.info(
            "[IMP:9][converge][%s] DRY-RUN/REPORT-ONLY: план мутаций ПУСТ (O7 detect-only) — тома не создаются", unit
        )
    logger.info("[IMP:8][converge][%s] START: reconcile_volumes — detect-only named volume check (O7)", unit)

    # ── Check docker daemon (W1: docker info — shared/docker_ops) ──
    docker_info_r = docker_ops.docker_info(timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping volume reconciliation"
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

    # Derive modules directory from infra.core_dir
    modules_dir = Path(infra.core_dir) / "modules"

    missing_volumes: list[str] = []
    checked_modules = 0

    for mod in modules:
        # W11: parse_node_modules_yaml → dict[str, object] — каст строкового поля
        mod_name = cast(str, mod.get("name", ""))
        if not mod_name or not mod.get("enabled", True):
            continue

        # Find compose file for this module — DevPlan 118 A2: единый канон
        # shared/compose_files.resolve_compose_file (включает docker-compose.base.yml —
        # реальные модули имеют ТОЛЬКО base-compose; старый кортеж их не видел)
        mod_dir = modules_dir / mod_name
        compose_file = resolve_compose_file(str(mod_dir))

        if not compose_file:
            logger.info("[IMP:7][converge][%s] Module %s has no compose file — skipping (not docker)", unit, mod_name)
            continue

        checked_modules += 1
        logger.info("[IMP:7][converge][%s] Checking module: %s (compose: %s)", unit, mod_name, compose_file)

        # Run docker compose config to get resolved JSON (shared — sole path, DevPlan 116 B5 T6)
        # ⚠️ TRAP[BUG] · 2026-08-06 · HI · R7: config без secrets env-file слеп (141 B18)
        # · Symptom: "required variable POSTGRES_PASSWORD is missing" (26 вхождений/прогон) —
        # ·   ${VAR:?} в base.yml падал → R7 detect-only мимо (0 сервисов), orphan-детекция слепа.
        # · Fix: --env-file /var/lib/platform/run/secrets.env если существует (канон _build_compose_args).
        compose_args = ["-f", str(compose_file), "--profile", mod_name]
        secrets_env = str(deploy_paths.secrets_env_file())
        if os.path.isfile(secrets_env):
            compose_args = ["--env-file", secrets_env, *compose_args]
        config_r = _shared_docker_compose_config(
            str(compose_file.parent),
            timeout=DOCKER_TIMEOUT,
            compose_args=compose_args,
            flags=["--format", "json"],
        )
        if config_r.returncode != 0:
            logger.warning(
                "[IMP:8][converge][%s] docker compose config failed for %s: %s",
                unit,
                mod_name,
                config_r.stderr.strip(),
            )
            continue

        try:
            # W11: json.loads → Any — каст к compose-config dict (services/volumes)
            compose_json = cast(dict[str, object], json.loads(config_r.stdout))
        except json.JSONDecodeError as exc:
            logger.warning("[IMP:8][converge][%s] Failed to parse compose JSON for %s: %s", unit, mod_name, exc)
            continue

        named_volumes = extract_named_volumes(compose_json)
        if not named_volumes:
            logger.info("[IMP:7][converge][%s] No named volumes in %s", unit, mod_name)
            continue

        logger.info("[IMP:7][converge][%s] Named volumes in %s: %s", unit, mod_name, named_volumes)

        for vol_name in named_volumes:
            # W1: docker volume inspect — shared/docker_ops (detect-only, O7)
            if not docker_ops.docker_volume_inspect(vol_name, timeout=DOCKER_TIMEOUT):
                logger.warning(
                    "[IMP:9][converge][%s] VOLUME MISSING: %s (module: %s) — detect-only, NOT creating (O7)",
                    unit,
                    vol_name,
                    mod_name,
                )
                missing_volumes.append(vol_name)
            else:
                logger.info("[IMP:7][converge][%s] Volume OK: %s", unit, vol_name)

    # ── Report ──
    if not checked_modules:
        logger.info("[IMP:9][converge][%s] SKIP: No docker modules with compose files found", unit)
        report_add(unit, "skipped", "No docker modules with compose files")
        return {"unit": unit, "status": "skipped", "detail": "No docker modules with compose files"}

    if missing_volumes:
        logger.warning(
            "[IMP:9][converge][%s] DONE: %d named volume(s) missing (detect-only — O7) — %s",
            unit,
            len(missing_volumes),
            missing_volumes,
        )
        report_add(unit, "warn", f"{len(missing_volumes)} named volume(s) missing: {missing_volumes}")
        set_exit(1)
        return {
            "unit": unit,
            "status": "warn",
            "detail": f"{len(missing_volumes)} named volume(s) missing",
        }

    logger.info("[IMP:9][converge][%s] DONE: All named volumes exist (converged)", unit)
    detail = "All named volumes exist" + (" [dry-run]" if preview else "")
    report_add(unit, "converged", "All named volumes exist" + (" [dry-run]" if preview else ""))
    return {"unit": unit, "status": "converged", "detail": detail}


# endregion FUNC_reconcile_volumes
