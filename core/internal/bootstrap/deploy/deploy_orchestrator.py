#!/usr/bin/env python3
# GREP_SUMMARY: deploy-orchestrator, routing, severity, parallel, sequential, orchestrator-cli, import-native, deploy-modules, kahn, linearize
# STRUCTURE: ▶ orchestrate [preflight → parse → route → postflight → severity] → _deploy_parallel [linearize → pre_pull → batch_check_env → deploy-many|groups → system → hc_marker] | _deploy_sequential [linearize(kahn) → for-loop ordered + abort-on-critical] → _aggregate_severity → _compute_exit_code → ⎋ {0,1,2,4=config}
# region MODULE_CONTRACT
## @purpose  Routing + severity orchestrator for module deployment (DevPlan 100). Extracts the
##           PARALLEL/ORCHESTRATOR/SEQUENTIAL routing decision and severity aggregation from
##           deploy-modules.sh (260 LOC shell) into typed Python. IMPORTS existing Python modules
##           in deploy/ + topo_sort — no subprocess for business logic. CLI + importable
##           orchestrate() function. Returns exit code {0,1,2}.
## @scope    Called by core/internal/bootstrap/deploy-modules.sh (thin shell facade, ≤50 LOC) via
##           `exec python3 deploy/deploy_orchestrator.py`. Covers: preflight (context overlay,
##           spool verify, status-metrics pre-create, charset validation), module parsing from
##           node.yaml, routing, per-module deploy (docker via docker_orchestrator, system via
##           invoke_module_interface), postflight (sudoers, orphans, litellm config), severity-based
##           exit code aggregation.
## @location core/internal/bootstrap/deploy/deploy_orchestrator.py — DevPlan 100 (F1)
## @invariants
##   - NO subprocess for business logic — existing Python modules are IMPORTED (D1)
##   - Subprocess allowed ONLY for: orchestrator_cli deploy-many (separate CLI layer, D1 exception)
##     and invoke_module_interface (shell function from core/lib/module-interface.sh, D4)
##   - exit code contract: CRIT>0 → 2, WARN>0 → 0 (logged), no failures → 0 (shell parity);
##     ConfigValidationError (топо-цикл/неизвестная зависимость, REF-0110) → exit 4 через main()
##   - Deploy failures are non-fatal — orchestrator continues and aggregates severity.
##     ИСКЛЮЧЕНИЕ (REF-0110): critical-failure модуля прерывает деплой ОСТАЛЬНЫХ
##     (dependents не стартуют против отсутствующих зависимостей); невыполненные — в failed
##   - Depends on PYTHONPATH=<project root> from the shell facade; also self-bootstraps sys.path
##     (4 levels up) for direct-script invocation (TRAP[BUG] pattern from sudoers_generator.py)
## @rationale D1: Python-import faster than subprocess (no fork+exec per call), testable via
##            unittest.mock.patch, gives typed interface (ruff validates signatures), all modules
##            already live in deploy/ package. D2: shell facade uses `exec python3` — same PID,
##            automatic exit-code propagation. D3: JSON interop via native json.loads/json.dumps (Python).
## @changes   2026-07-31 · Created (DevPlan 100 TASK-1)
## @changes   2026-08-03 · DevPlan 123 T8 — контракт sequential/parallel: _deploy_sequential прокидывает
##            secrets_env_file/platform_root в deploy_docker_module (паритет с parallel_runner);
##            docstring-инварианты приведены к фактическому поведению (overlay передаётся, не «NOT passed»)
## @changes   2026-08-25 · REF-0110 (meta-refactoring S-пакет) — kahn-линеаризация и для sequential
##            (порядок node.yaml больше не авторитетен); topo-failure → fail-fast ConfigValidationError
##            (без деградации в unordered fallback); critical-failure → abort remaining
## @modulemap
##   ModuleDeployResult [W:1] — dataclass: deployed, failed, crit_count, warn_count, exit_code
##   ModuleLists [W:1] — dataclass: all_names, enabled_names, overlays
##   orchestrate [W:5] — main entry point: preflight → parse → route → postflight → severity → exit_code
##   main [W:2] — CLI entry: argparse → orchestrate() → exit code
##   _preflight [W:3] — context_overlay.ensure_context_repo + spool_validator.verify_spool_dirs + status-metrics + charset validation
##   _parse_modules [W:2] — secrets_validator.parse_modules_from_node_yaml + enabled/filter + overlay resolution
##   _route_deploy [W:3] — PARALLEL → _deploy_parallel, else → _deploy_sequential
##   _deploy_parallel [W:5] — linearize (kahn) → pre_pull → batch_check_env → deploy-many | groups → system → hc_marker
##   _deploy_orchestrator [W:2] — subprocess orchestrator_cli deploy-many --scp (docker modules only, R4)
##   _linearize_deploy_order [W:3] — kahn-линеаризация enabled по depends_on (REF-0110); ConfigValidationError на цикл/неизвестную зависимость
##   _deploy_docker_groups [W:3] — sequential topo-группы + abort remaining после critical-failure (REF-0110)
##   _deploy_sequential [W:4] — kahn-ordered for-loop: check_env → detect_type → deploy_docker_module | invoke_module_interface; abort remaining после critical-failure
##   _deploy_system_modules [W:2] — sequential system deploy via invoke_module_interface
##   _postflight [W:3] — sudoers batch + orphans detect + litellm config render
##   _aggregate_severity [W:2] — enriched modules dict lookup, fallback per-module metadata call
##   _compute_exit_code [W:1] — CRIT>0 → 2, WARN>0 → 0, else → 0
##   _set_hc_marker [W:1] — failed==[] ? touch run-scoped .hc_done_in_deploy[.`ctx`].`run-id` : skip (REF-0005)
##   _create_status_metrics_json [W:1] — pre-create /var/lib/platform/run/status-metrics.json (P1 fix)
##   _invoke_module_interface [W:2] — bash subprocess wrapper for system module dispatch (D4)
## @usecases
##   - deploy-modules.sh facade → exec python3 deploy_orchestrator.py --node-yaml ... (prod bootstrap)
##   - import deploy_orchestrator; orchestrate(node_yaml, modules_dir, core_dir, templates_dir) (tests, tools)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

# ── sys.path bootstrap for direct-script invocation (DevPlan 100, mirror of sudoers_generator.py) ──
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · direct-script invocation needs project root in sys.path
# · Symptom: python3 deploy/deploy_orchestrator.py raises ModuleNotFoundError: No module named 'core'
# · Root: script dir (deploy/) is sys.path[0]; deploy → bootstrap → internal → core → root = 4 levels up
# · Fix: insert root into sys.path before core.* imports (pattern from sudoers_generator.py:42-56)
# · Prevention: shell facade also exports PYTHONPATH (defense in depth); tests import via pytest rootdir
_PLATFORM_ROOT = os.environ.get(
    "PLATFORM_ROOT",
    os.path.join(Path(Path(__file__).resolve()).parent, "..", "..", "..", ".."),
)
if not Path(os.path.join(_PLATFORM_ROOT, "core", "internal")).is_dir():
    # Осознанный пере-биндинг: PLATFORM_ROOT env может указывать на не-корень → канонический
    # module-relative fallback (TRAP[BUG] выше). reportConstantRedefinition — by design.
    _PLATFORM_ROOT = os.path.join(Path(Path(__file__).resolve()).parent, "..", "..", "..", "..")  # pyright: ignore[reportConstantRedefinition]
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

# ── Existing Python module imports (DevPlan 100 D1 — import-native, NO subprocess) ──
# B9 T3: приватные `as _x` алиасы убраны — публичные имена (гейт T6.1)
from core.internal.bootstrap import topo_sort
from core.internal.bootstrap.deploy import (
    context_overlay,
    docker_orchestrator,
    orphan_reconciler,
    secrets_validator,
    spool_validator,
    sudoers_generator,
)
from core.internal.bootstrap.deploy.compose_args import build_compose_args  # plan 012 T10 fix: root-compose-first

# DevPlan 119 E6: чистые функции severity/exit-code/status-metrics/hc-marker/llm-summary —
# извлечены в orchestrator_metrics.py (AUDIT-2 M5). I/O-обёртки здесь делегируют вычисления.
from core.internal.bootstrap.deploy.orchestrator_metrics import (
    aggregate_severity as _metrics_aggregate_severity,
)
from core.internal.bootstrap.deploy.orchestrator_metrics import (
    exit_code_from_results as _metrics_exit_code,
)
from core.internal.bootstrap.deploy.orchestrator_metrics import (
    hc_marker_path as _metrics_hc_marker_path,
)
from core.internal.bootstrap.deploy.orchestrator_metrics import (
    render_llm_summary as _metrics_render_llm_summary,
)
from core.internal.bootstrap.deploy.orchestrator_metrics import (
    status_metrics_json as _metrics_status_metrics_json,
)
from core.internal.llm import config_renderer
from core.internal.shared import deploy_paths  # 142 W2: status-metrics.json → persistent run
from core.internal.shared.compose_files import resolve_compose_file  # plan 012 T10: публичный резолвер
from core.internal.shared.compose_profiles import load_profiles as compose_profiles_load_profiles  # plan 012 T10

# DevPlan 116 B4 T1 (U-39): deploy-политика best-effort — контракт, а не комментарии.
# DEPLOY_BEST_EFFORT=True: failing step → WARN, деплой продолжается; WARN→exit 0; HC_DONE_MARKER всегда.
# REF-0110: topo-failure (цикл/неизвестная зависимость depends_on) — ИСКЛЮЧЕНИЕ из best-effort:
# ConfigValidationError пробрасывается до первого деплоя (fail-fast), main() маппит в exit 4.
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
from core.internal.shared.llm_paths import litellm_config_path

# DevPlan 118 C5: единая bash-обёртка invoke_module_interface — shared/module_interface.py (вход для B8).
from core.internal.shared.module_interface import invoke as module_interface_invoke
from core.internal.shared.node_yaml import NodeYaml

# DevPlan 010 T1.1: placement-авторитетный резолв модулей ноды (multi-node)
from core.internal.shared.placement import (
    Placement,
    lint_drift,
    load_placement,
    placement_node_relative_path,
    resolve_node_modules,
    service_host,
    validate_topology,
)

# DevPlan 010 T2.2/T2.8: host-порты peer-публикации и vhost-upstream'ов — только из SoT
from core.internal.shared.platform_ports import (
    CLICKHOUSE_NATIVE_PEER,
    LANGFUSE_HOST,
    LOKI_HTTP,
    PLATFORM_PORT_GRAFANA,
    PLATFORM_PORT_HERMES,
    PLATFORM_PORT_PROMETHEUS,
    PLATFORM_PORT_STATUS_PAGE,
)

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11, гейт timeout_literals)
# REF-0103: +HEALTHCHECK_CMD_TIMEOUT — liveness-инвок модулей (60s) вместо полного COMPOSE_UP_TIMEOUT
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT, DEPLOY_TIMEOUT, HEALTHCHECK_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants (paths mirror deploy-modules.sh facade / docker_orchestrator.py) ──
# E6: единственные источники констант — orchestrator_metrics.py (чистые функции).
# Локальные копии _HC_DONE_MARKER/_STATUS_METRICS_TEMPLATE УДАЛЕНЫ (дубли).
# Маркер резолвится В CALL-TIME через _metrics_hc_marker_path(os.environ.get("CONTEXT")) —
# import-time константа убрана (T9.19: per-context путь зависит от env на момент деплоя).
_STATUS_METRICS_PATH = str(deploy_paths.status_metrics_json())
# C5 (DevPlan 118): сборка bash -c делегирована в shared/module_interface.invoke — локальные
# константы путей (paths.sh/module-interface.sh) УДАЛЕНЫ (единый источник в shared).


# region FUNC_ModuleDeployResult
## @purpose  Structured result of one orchestrate() run — consumed by callers for exit code + telemetry
## @io       ⇥ (constructed by orchestrate) → ⎋ dataclass
## @complexity 1 — plain data container
@dataclass
class ModuleDeployResult:
    """Result of a full orchestrate() run.

    ## @invariants
    ##   - exit_code: 0=success (warnings allowed), 2=critical failures. 1 is RESERVED —
    ##     shell mapped WARN to exit 0 (DevPlan 100 §3 Phase 5), never emitted 1.
    ##   - failed lists module names that failed deploy AND contribute to severity aggregation
    """

    deployed: int
    failed: list[str] = field(default_factory=list)
    crit_count: int = 0
    warn_count: int = 0
    exit_code: int = 0


# endregion FUNC_ModuleDeployResult


# region FUNC_ModuleLists
## @purpose  Parsed module lists from node.yaml + resolved overlay dirs
## @io       ⇥ (constructed by _parse_modules) → ⎋ dataclass
## @complexity 1 — plain data container
@dataclass
class ModuleLists:
    """Parsed module inventory: all declared names, enabled-only names, per-module overlay dirs."""

    all_names: list[str] = field(default_factory=list)
    enabled_names: list[str] = field(default_factory=list)
    overlays: dict[str, str] = field(default_factory=dict)


# endregion FUNC_ModuleLists


# region FUNC_orchestrate
## @purpose  Main orchestration entry point — importable AND CLI-callable (DevPlan 100 §6.1).
##           Phase flow: PREFLIGHT → PARSE → ROUTE&DEPLOY → POSTFLIGHT → SEVERITY → exit_code.
## @io       ⇥ node_yaml: str, modules_dir: str, core_dir: str, templates_dir: str,
##           modules_filter: str = "", deploy_parallel: bool = False, deploy_orchestrator: bool = False
##           ⎋ ModuleDeployResult (caller should sys.exit(result.exit_code))
## @complexity 3 — linear phase pipeline; routing branch dispatches parallel or sequential deploy
## @invariants
##   - Preflight + parse run before routing — empty enabled set → early return exit_code 0
##   - _postflight runs even if deploy had failures (sudoers/orphans/litellm are independent)
##   - severity aggregation happens after postflight so ALL failure names are accounted
EXIT_CRITICAL: int = 2  # exit-код критической ошибки (DEPLOY_BEST_EFFORT contract)


def orchestrate(
    node_yaml: str,
    modules_dir: str,
    core_dir: str,
    templates_dir: str,
    *,
    modules_filter: str = "",
    deploy_parallel: bool = False,
    deploy_orchestrator: bool = False,
    strict_init: bool = False,
) -> ModuleDeployResult:
    """Main orchestration entry point — importable and CLI-callable.

    strict_init (plan 012 T9 / F-015b): init-режим — failed≠∅ ИЛИ crit>0 → exit 2
    (resumable); update-режим (False) сохраняет контракт DEPLOY_BEST_EFFORT (WARN→0).
    """
    logger.info(
        "[IMP:7][orchestrate][start] node_yaml=%s modules_dir=%s strict_init=%s", node_yaml, modules_dir, strict_init
    )

    # PHASE 1: PREFLIGHT (all steps non-fatal — shell used `|| true` semantics)
    _preflight(core_dir, node_yaml, modules_dir)

    # PHASE 2: PARSE MODULES
    modules = _parse_modules(node_yaml, modules_dir, modules_filter)
    if not modules.enabled_names:
        logger.info("[IMP:9][orchestrate][skip] No enabled modules declared in %s — SKIP deploy", node_yaml)
        return ModuleDeployResult(deployed=0, failed=[], crit_count=0, warn_count=0, exit_code=0)
    logger.info("[IMP:8][orchestrate][parse] enabled=%d all=%d", len(modules.enabled_names), len(modules.all_names))

    # PHASE 2.5: MULTI-NODE RUNTIME ENV (DevPlan 010 T2.2/T2.5/T2.8) — placement-авторитетные
    # SERVICE_BIND_HOST / LOKI_TENANT / EXTRA_NO_PROXY / UPSTREAM_* ДО деплоя модулей;
    # single-node (placement None) → no-op, os.environ не трогается (байт-совместимость §1.1).
    mn_placement, mn_node = _placement_for_node(node_yaml, modules_dir=modules_dir)
    if mn_placement is not None:
        _apply_multinode_runtime_env(mn_placement, mn_node)

    # PHASE 2.7 (plan 012 T10/D8): node-side interpolation dry-run — unsatisfied ${VAR:?}
    # ловится ДО создания контейнеров; strict (init) → FAIL со списком, update → WARN.
    _interpolation_dryrun(modules.enabled_names, modules.overlays, modules_dir, strict=strict_init)

    # PHASE 3: ROUTE & DEPLOY
    deployed, failed, modules_info = _route_deploy(
        modules.enabled_names,
        modules.overlays,
        modules_dir,
        core_dir,
        deploy_parallel=deploy_parallel,
        deploy_orchestrator=deploy_orchestrator,
    )

    # PHASE 4: POSTFLIGHT (independent of deploy outcome)
    _postflight(modules.all_names, modules.enabled_names, modules_dir, core_dir, templates_dir)

    # PHASE 5: SEVERITY → EXIT CODE
    crit, warn = _aggregate_severity(failed, modules_info, modules_dir)
    exit_code = _compute_exit_code(crit, warn, deployed, failed=failed, strict_init=strict_init)
    logger.info(
        "[IMP:9][orchestrate][done] deployed=%d failed=%s crit=%d warn=%d exit_code=%d",
        deployed,
        failed,
        crit,
        warn,
        exit_code,
    )
    return ModuleDeployResult(
        deployed=deployed,
        failed=failed,
        crit_count=crit,
        warn_count=warn,
        exit_code=exit_code,
    )


# endregion FUNC_orchestrate


# region FUNC__preflight
## @purpose  PHASE 1: run non-fatal pre-deploy checks — context overlay ensure, spool dirs verify,
##           status-metrics.json pre-create, secrets charset validation.
## @io       ⇥ core_dir: str, node_yaml: str, modules_dir: str → ⎋ None
## @complexity 2 — 4 independent guarded calls (each wrapped in try/except, non-fatal)
## @invariants
##   - Every step is best-effort: a failing step logs WARN and does NOT abort deploy (best-effort)
##   - Order preserved from deploy-modules.sh: overlay → spool → status-metrics → charsets
##   - secrets-manifest path derived as core_dir/secrets-manifest.yaml
def _preflight(core_dir: str, node_yaml: str, modules_dir: str) -> None:
    """Run non-fatal preflight steps (`|| true` semantics preserved)."""
    # ── context overlay ensure (clone/pull with S9 cache) ──
    try:
        rc = context_overlay.ensure_context_repo(node_yaml)
        logger.info("[IMP:8][_preflight][context_overlay] ensure_context_repo rc=%d", rc)
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — non-fatal preflight step (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_preflight][context_overlay] error (non-fatal): %s", exc)

    # ── spool dirs verify (verify-only runtime check) ──
    try:
        report = spool_validator.verify_spool_dirs(modules_dir)
        logger.info(
            "[IMP:8][_preflight][spool] status=%s missing=%d",
            report.get("status"),
            len(report.get("missing", [])),
        )
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — non-fatal preflight step (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_preflight][spool] error (non-fatal): %s", exc)

    # ── status-metrics.json pre-create (prevents Docker bind-mount dir creation) ──
    _create_status_metrics_json()

    # ── secrets charset validation (charset violations logged, deploy continues) ──
    try:
        failed, _errors = secrets_validator.validate_secret_charsets(os.path.join(core_dir, "secrets-manifest.yaml"))
        if failed:
            logger.warning(
                "[IMP:8][_preflight][charset] %d secret(s) failed charset validation — continuing with deploy",
                failed,
            )
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — non-fatal preflight step (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_preflight][charset] error (non-fatal): %s", exc)


# endregion FUNC__preflight


# region FUNC__interpolation_dryrun
## @purpose  Node-side interpolation dry-run (plan 012 T10 / D8): docker compose config --quiet
##           по каждому enabled-модулю с собранным env ДО создания контейнеров.
## @io       ⇥ enabled_names list[str] · overlays dict[str,str] · modules_dir str · strict bool
##           → ⎋ list[str] (broken modules); strict+broken → PlatformFatalError
## @complexity O(M) compose config вызовов (~0.5s/модуль; <60s на init)
## @invariants
##   - Собираются ВСЕ проблемные модули за один проход (не first-fail-abort)
##   - strict=False — update-режим WARN+continue (DEPLOY_BEST_EFFORT)
##   - strict=True — init-режим FAIL со списком проблемных модулей
##   - COMPOSE_PROFILES для dry-run = полный infra-список (паритет с CI compose config)
def _interpolation_dryrun(
    enabled_names: list[str],
    overlays: dict[str, str],
    modules_dir: str,
    *,
    strict: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    # endregion FUNC__interpolation_dryrun
    run = subprocess.run if runner is None else runner
    secrets_env = os.environ.get("SECRETS_ENV_FILE") or str(deploy_paths.secrets_env_file())
    docker_orchestrator.ensure_nginx_overlay_env(overlays.get("nginx") or os.environ.get("NGINX_OVERLAY_DIR"))
    # plan 012 T10: публичный SoT-резолвер профилей (shared/compose_profiles) — БЕЗ приватного
    # доступа docker_orchestrator._resolve_* (private-imports гейт).
    full_profiles = ",".join(compose_profiles_load_profiles())
    if not full_profiles:
        logger.warning(
            "[IMP:7][_interpolation_dryrun][profiles] COMPOSE_PROFILES empty in platform-infra.yaml (SoT) — skip dry-run"
        )
        return []

    broken: list[tuple[str, str]] = []
    for name in enabled_names:
        compose_file = resolve_compose_file(os.path.join(modules_dir, name))
        if compose_file is None:
            continue  # отсутствие compose-файла репортует сам деплой
        # plan 012 T10 fix (F-07): канонический build_compose_args (root-compose-first, U-49).
        # Изолированный -f <module>/base.yml давал «undefined volume <name>-data» — volumes
        # объявлены в root docker-compose.yml (единственный SoT), не в модульных base.yml.
        cmd = [
            "docker",
            "compose",
            *build_compose_args(compose_file, secrets_env, None, overlays.get(name), name),
            "config",
            "--quiet",
        ]
        dry_env = {**os.environ, "COMPOSE_PROFILES": full_profiles}
        try:
            result = run(cmd, capture_output=True, text=True, timeout=60, env=dry_env, check=False)
            rc, err = result.returncode, (result.stderr or "")[-400:]
        except (OSError, subprocess.TimeoutExpired) as exc:
            rc, err = 1, f"dry-run failed: {exc}"
        if rc != 0:
            logger.error("[IMP:9][_interpolation_dryrun][fail] %s: interpolation/config error: %s", name, err.strip())
            broken.append((name, err.strip()))
        else:
            logger.info("[IMP:8][_interpolation_dryrun][ok] %s: interpolation OK", name)

    if not broken:
        return []

    names = [n for n, _ in broken]
    if strict:
        logger.error(
            "[IMP:10][_interpolation_dryrun] FAIL: %d module(s) with unsatisfied interpolation BEFORE "
            "container creation (D8): %s",
            len(broken),
            ", ".join(names),
        )
        msg = (
            "Interpolation dry-run failed for modules: "
            + ", ".join(names)
            + " — check secrets matrix / env_defaults before deploy"
        )
        raise PlatformFatalError(msg)
    for name, err in broken:
        logger.warning("[IMP:7][_interpolation_dryrun][warn-nonstrict] %s: %s", name, err)
    return names


# region FUNC__read_node_yaml_projects
def _read_node_yaml_projects(node_yaml_path: Path) -> list[dict[str, object]]:
    """Read node.yaml#projects with fail-fast error wrapping (DevPlan 16 T2.A).

    ## @purpose  projects_scan-хелпер: ошибка чтения ЛЮБОГО node.yaml контекста →
    ##            ConfigValidationError (fail-fast; молчаливый partial-скан скрыл бы
    ##            чужие exposed-проекты). Вынесен из цикла — PERF203 без потери семантики.
    ## @io        ⇥ node_yaml_path → ⎋ list[dict] (поля name/domain/expose/target_node)
    ## @raises    ConfigValidationError: нечитаемый/невалидный node.yaml
    """
    try:
        return NodeYaml(str(node_yaml_path)).get_projects()
    except (ConfigNotFoundError, ConfigParseError, OSError, ConfigValidationError) as exc:
        msg = f"projects_scan: node.yaml unreadable ({node_yaml_path}): {exc}"
        raise ConfigValidationError(msg) from exc


# endregion FUNC__read_node_yaml_projects


# region FUNC__placement_for_node
## @purpose  DevPlan 010 T1.1: locate + load placement.yaml для ноды (single-node → None, no-op);
##           при переданном modules_dir — fail-fast validate_topology (DR-C1 fix: production
##           wiring валидатора, ранее вызывался только из тестов)
## @io       ⇥ node_yaml: str (путь); modules_dir: str | None (инвентарь core/modules;
##           None/"" → валидация пропущена — легаси-вызовы и unit-фикстуры) →
##           ⎋ tuple[Placement | None, str] (placement, node_name)
## @complexity 1-2 — path derivation + load_placement (+ topology validation при modules_dir)
## @invariants
##   - placement.yaml живёт в node-configs-репозитории рядом с директориями нод:
##     ROOT/CONTEXT/placement.yaml, где root = parent директории ноды (§2 плана)
##   - невалидный placement → ConfigValidationError ПРОПАГИРУЕТСЯ (fail-fast до деплоя,
##     инвариант 3 плана) — никаких best-effort заглушек
##   - отсутствие файла = легаси single-node путь (инвариант 1 плана)
##   - validate_topology выполняется ТОЛЬКО при truthy modules_dir: проверяет инвентарь,
##     node.yaml нод, полноту записей, exposed↔nginx, off-deps против опечаток топологии
## @rationale Q: почему резолв здесь, а не в secrets_validator? A: _parse_modules — единственная
##            точка формирования enabled/all списков деплоя; валидатор секретов остаётся
##            node.yaml-ориентированным (drift-проверки — его lint-слой).
def _placement_for_node(node_yaml: str, *, modules_dir: str | None = None) -> tuple[Placement | None, str]:
    """Resolve context-scoped placement.yaml for this node; None when absent (legacy)."""
    node = NodeYaml(node_yaml)
    context = node.get_context()
    node_name = str(node.get("node.name", default="") or "")
    if not context or not node_name:
        logger.info(
            "[IMP:7][_placement_for_node][skip] no context/node name in %s — legacy resolve",
            node_yaml,
        )
        return None, ""
    # DevPlan 16 T1.B: единый резолвер (была локальная деривация parent.parent/context);
    # файл по этому пути создаёт deliver_placement (core_deliverer, Phase 2b)
    placement_path = placement_node_relative_path(node_yaml, context)
    placement = load_placement(placement_path)
    if placement is None:
        # [IMP:8] single-node no-op: файла нет → легаси-путь байт-идентичен (инвариант 1)
        logger.info("[IMP:8][_placement_for_node][noop] no placement.yaml at %s", placement_path)
    else:
        logger.info(
            "[IMP:9][_placement_for_node][loaded] context=%s nodes=%d modules=%d node=%s",
            placement.context,
            len(placement.nodes),
            len(placement.modules),
            node_name,
        )
        # DR-C1 fix (DevPlan 010 follow-up): fail-fast валидация топологии в production-контуре.
        # Ранее validate_topology вызывался ТОЛЬКО из тестов — топологические ошибки
        # (неполнота, чужой context, exposed вне nginx, off-deps) ловились тестами, не деплоем.
        if modules_dir:
            context_root = Path(node_yaml).parent.parent

            def _scan_context_projects() -> list[dict[str, object]]:
                """Скан проектов контекста (node.yaml#projects) для exposed-валидации (T2.A).

                ## @purpose  DevPlan 16 T2.A (P1-1): прод-вызов validate_topology получает
                ##            projects_scan — тот же источник, что vhost_renderer/project_registry
                ##            (node.yaml#projects каждой ноды контекста). Без скана
                ##            exposed target_node/FQDN-инварианты проверялись только тестами.
                ## @io        ⇥ — → ⎋ list[dict] (поля name/domain/expose/target_node)
                ## @invariants  Ошибка чтения ЛЮБОГО node.yaml → ConfigValidationError
                ##              (fail-fast: невалидный инвентарь = невалидная топология).
                """
                return [
                    proj
                    for ny_path in sorted(context_root.glob("*/node.yaml"))
                    for proj in _read_node_yaml_projects(ny_path)
                ]

            validate_topology(
                placement,
                modules_dir=modules_dir,
                node_configs_dir=str(context_root),
                projects_scan=_scan_context_projects,
            )
    return placement, node_name


# endregion FUNC__placement_for_node


# region FUNC_multinode_runtime_env
## @purpose  DevPlan 010 T2.2/T2.5/T2.8: runtime-env ноды из placement (pure, без мутации os.environ).
##           SERVICE_BIND_HOST — host-бинды публикуемых портов (peer-доступ режет ufw T2.3);
##           LOKI_TENANT — tenant контекста (T2.0b: alloy push + grafana datasource + loki-vhost);
##           EXTRA_NO_PROXY — адреса всех нод контекста (T2.5: прокси не трогает cross-node трафик);
##           UPSTREAM_* — vhost upstream'ы сервисов, размещённых на ДРУГОЙ ноде (T2.8).
## @io       ⇥ placement: Placement, node_name: str → ⎋ dict[str, str] (env-переменные)
## @complexity O(S×K) — S = upstream-сервисы, K = размер nodes[]-списков
## @invariants
##   - Pure-функция (DI-seam для тестов); мутацию os.environ делает _apply_multinode_runtime_env
##   - UPSTREAM_* выставляются ТОЛЬКО для remote-сервисов (локальные — compose-дефолты Docker DNS)
##   - Порты только из shared/platform_ports.py (0 литералов)
##   - node_name вне placement → ConfigValidationError (из resolve/service_host)
_UPSTREAM_VARS: dict[str, tuple[tuple[str, int], ...]] = {
    "logging": (("UPSTREAM_LOKI", LOKI_HTTP),),
    "langfuse": (("UPSTREAM_LANGFUSE", LANGFUSE_HOST),),
    "hermes-agent": (("UPSTREAM_HERMES", PLATFORM_PORT_HERMES),),
    "status-page": (("UPSTREAM_STATUS_PAGE", PLATFORM_PORT_STATUS_PAGE),),
    "monitoring": (
        ("UPSTREAM_GRAFANA", PLATFORM_PORT_GRAFANA),
        ("UPSTREAM_PROMETHEUS", PLATFORM_PORT_PROMETHEUS),
    ),
}

# DevPlan 010 T2.7 (Acceptance W2): dependency-hosts модулей при кросс-нодовом размещении.
# Модуль M размещён на этой ноде, его data-plane зависимость D — на ДРУГОЙ → deploy подставляет
# env-хост D (compose-дефолт Docker DNS указывал бы на несуществующий локальный сервис).
# Инфра-зависимости НЕ включены (канон §2.2 п.8); exporter'ы co-located с сервисами (§3) —
# им кросс-нодовые хосты не нужны.
_MODULE_DEP_ENV: dict[str, tuple[tuple[str, str], ...]] = {
    "litellm": (("POSTGRES_HOST", "postgres"),),
    "langfuse": (("POSTGRES_HOST", "postgres"), ("CLICKHOUSE_HOST", "clickhouse")),
    "hermes-agent": (("POSTGRES_HOST", "postgres"), ("REDIS_HOST", "redis")),
}


def multinode_runtime_env(placement: Placement, node_name: str) -> dict[str, str]:
    """Compute multi-node runtime env for a node from placement (pure)."""
    env = {
        "SERVICE_BIND_HOST": placement.nodes[node_name],
        # T2.0b: tenant = имя контекста — ЕДИНЫЙ по всем нодам (alloy data-ноды пушит
        # в центральный Loki; расхождение tenant'ов = невидимые логи в Grafana)
        "LOKI_TENANT": placement.context,
        # T2.5: все адреса нод — прокси-канал не должен перехватывать cross-node вызовы.
        # КОНТРАКТ значения: ведущая запятая (",10.8.0.11,...") — интерполяция плоская
        # (см. monitoring base.yml T2.5-комментарий).
        "EXTRA_NO_PROXY": "," + ",".join(sorted(placement.nodes.values())),
    }
    # T2.8: UPSTREAM_* осмысленны ТОЛЬКО на нодах с nginx (vhost-потребители); на остальных
    # нодах переменные не выставляются — compose-дефолты никуда не применяются.
    placed_modules = resolve_node_modules(placement, node_name)
    if "nginx" in placed_modules:
        for module, upstream_vars in _UPSTREAM_VARS.items():
            remote_host = service_host(placement, module, node_name)
            if remote_host is None:
                continue  # co-located / off / не в placement — Docker DNS дефолт из compose
            for var_name, port in upstream_vars:
                env[var_name] = f"{remote_host}:{port}"
    # T2.7: dependency-hosts размещённых модулей (langfuse на agent-1 → POSTGRES_HOST=10.8.0.11;
    # CH native peer 19000 — CLICKHOUSE_NATIVE_PORT для мигратора, TRAP §3 host≠container).
    for module in placed_modules:
        for env_key, dep_module in _MODULE_DEP_ENV.get(module, ()):
            remote_dep = service_host(placement, dep_module, node_name)
            if remote_dep is not None:
                env[env_key] = remote_dep
        if module == "langfuse" and service_host(placement, "clickhouse", node_name) is not None:
            env["CLICKHOUSE_NATIVE_PORT"] = str(CLICKHOUSE_NATIVE_PEER)
    logger.info(
        "[IMP:8][orchestrate][multinode-env] node=%s vars=%d (upstream=%s)",
        node_name,
        len(env),
        sorted(k for k in env if k.startswith("UPSTREAM_")),
    )
    return env


# endregion FUNC_multinode_runtime_env


# region FUNC__apply_multinode_runtime_env
## @purpose  Императивная обёртка multinode_runtime_env(): выставляет os.environ перед деплоем
##           модулей (docker_orchestrator читает os.environ при deploy_docker_module).
## @io       ⇥ placement, node_name → ⎋ None (side-effect: os.environ)
## @invariants
##   - placement None (single-node легаси) → no-op: os.environ НЕ трогается (байт-совместимость
##     §1.1; guard локален — контракт не зависит от дисциплины call-site)
def _apply_multinode_runtime_env(placement: Placement | None, node_name: str) -> None:
    """Apply multi-node runtime env to os.environ (placement-authoritative set, not setdefault)."""
    if placement is None:
        return  # single-node no-op (инвариант 1 плана)
    for key, value in multinode_runtime_env(placement, node_name).items():
        os.environ[key] = value


# endregion FUNC__apply_multinode_runtime_env


# region FUNC__parse_modules
## @purpose  PHASE 2: parse node.yaml modules section, filter enabled + modules_filter, resolve overlays
## @io       ⇥ node_yaml: str, modules_dir: str, modules_filter: str
##           ⎋ ModuleLists (all_names, enabled_names, overlays)
## @complexity 2 — parse + linear filter + per-module overlay filesystem check
## @invariants
##   - enabled == "true" only (string form returned by parse_modules_from_node_yaml)
##   - modules_filter (comma/space-separated) intersects enabled set — applied BEFORE topo-sort
##   - Overlays resolved via node.yaml context + /opt/\<ctx\>/platform/modules/\<name\> filesystem check
##     (shell pattern — config_overlay field from node.yaml is NOT used for deploy)
##   - DevPlan 010 T1.1: при наличии placement.yaml enabled/all берутся из resolve_node_modules
##     (placement авторитетен); оверлеи остаются из node.yaml; drift node.yaml↔placement —
##     WARNING с repair-подсказкой, НЕ ошибка. Без placement.yaml путь байт-идентичен легаси.
def _parse_modules(node_yaml: str, modules_dir: str, modules_filter: str) -> ModuleLists:
    """Parse node.yaml modules and apply enabled/filter/overlay resolution."""
    raw = secrets_validator.parse_modules_from_node_yaml(node_yaml)

    all_names: list[str] = []
    enabled_names: list[str] = []
    raw_overlays: dict[str, str] = {}
    for name, enabled, overlay in raw:
        if name not in all_names:
            all_names.append(name)
        # input normalized by parse_modules_from_node_yaml (secrets_validator) — DevPlan 123 T6:
        # enabled уже lowercase "true"/"false" (str(value).lower()); .lower() повторно — идемпотентная защита
        if enabled.lower() == "true" and name not in enabled_names:
            enabled_names.append(name)
        # config_overlay из node.yaml (fallback для NGINX_OVERLAY_DIR — RC 121: прод-nginx не
        # монтировал node-configs оверлеи, когда контекст-оверлей отсутствует)
        if overlay:
            raw_overlays[name] = overlay

    # ── modules_filter: comma/space-separated intersect with enabled set ──
    # 🧐 TRAP[DECISION] · 2026-07-31 · — · --modules filter now APPLIED in orchestrator
    # · Rejected: keep inert behavior (260-LOC shell parsed --modules but never consumed it)
    # · Reason: DevPlan 100 §3 Phase 2 contract "Filter: enabled_only, modules_filter" + AGENTS.md
    #   documents "--modules postgres,redis → развернуть только указанные". Applying the filter
    #   restores documented semantics; intersection keeps behavior identical when flag is absent.
    # · Rev: if phases.py starts pre-filtering module lists, intersection remains idempotent.
    filter_set = {m.strip() for m in modules_filter.replace(",", " ").split() if m.strip()}
    if filter_set:
        enabled_names = [n for n in enabled_names if n in filter_set]

    # ── DevPlan 010 T1.1: placement-authoritative resolve (multi-node) ──
    # placement.yaml есть → enabled/all из resolve_node_modules (placement авторитетен,
    # node.yaml#modules для деплоя не читается); drift → WARNING (T1.2), не RED.
    placement, placement_node = _placement_for_node(node_yaml, modules_dir=modules_dir)
    if placement is not None and placement_node:
        # [IMP:9] drift-сигнал: node.yaml объявляет модули, которые placement не размещает
        # на этой ноде — repair-подсказка в каждой строке (удали из node.yaml или перенеси)
        for warning in lint_drift(enabled_names, placement, placement_node):
            logger.warning("[IMP:8][_parse_modules][drift] %s", warning)
        resolved = resolve_node_modules(placement, placement_node)
        all_names = list(dict.fromkeys(resolved))
        enabled_names = [n for n in resolved if n in filter_set] if filter_set else list(resolved)
        logger.info(
            "[IMP:9][_parse_modules][placement] authoritative resolve: enabled=%d all=%d",
            len(enabled_names),
            len(all_names),
        )

    overlays = _resolve_overlay_dirs(node_yaml, enabled_names, raw_overlays)
    logger.info("[IMP:9][_parse_modules][result] enabled=%d all=%d", len(enabled_names), len(all_names))
    return ModuleLists(all_names=all_names, enabled_names=enabled_names, overlays=overlays)


# endregion FUNC__parse_modules


# region FUNC__resolve_overlay_dirs
## @purpose  Resolve per-module context overlay dirs: /opt/\<ctx\>/platform/modules/\<name\> if exists.
## @io       ⇥ node_yaml: str, enabled_names: list[str] → ⎋ dict[str, str] (name → overlay path or "")
## @complexity 2 — NodeYaml context read + N filesystem checks
## @invariants
##   - No context in node.yaml → all overlays empty (grep "^context:" semantics)
##   - Overlay only set when the directory actually exists on disk
##   - NodeYaml.get_context() returns "" (no raise) when context absent
def _resolve_overlay_dirs(
    node_yaml: str, enabled_names: list[str], config_overlays: dict[str, str] | None = None
) -> dict[str, str]:
    """Resolve context overlay dirs from node.yaml context + filesystem existence.

    ⚠️ TRAP[BUG] · 2026-08-03 · P1 · config_overlay fallback (RC 121 прод)
    · Symptom: прод-nginx монтировал default ./overlays (core/modules/nginx/overlays) — NGINX_OVERLAY_DIR
    ·   не устанавливался: контекст пуст (нет contexts[]), config_overlay из node.yaml игнорировался.
    · Fix: если контекст-оверлей не найден — используется config_overlay из node.yaml (абсолютный путь).
    """
    ctx = ""
    try:
        ctx = NodeYaml(node_yaml).get_context()
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — overlay resolution is best-effort
        logger.warning("[IMP:7][_resolve_overlay_dirs][warn] Cannot read context from %s: %s", node_yaml, exc)

    overlays: dict[str, str] = {}
    for name in enabled_names:
        overlay = ""
        if ctx:
            candidate = f"/opt/{ctx}/platform/modules/{name}"
            if os.path.isdir(candidate):
                overlay = candidate
        if not overlay and config_overlays and config_overlays.get(name):
            overlay = config_overlays[name]
            logger.info(
                "[IMP:8][_resolve_overlay_dirs][config_overlay] Using node.yaml config_overlay for %s: %s",
                name,
                overlay,
            )
        overlays[name] = overlay
    return overlays


# endregion FUNC__resolve_overlay_dirs


# region FUNC__route_deploy
## @purpose  PHASE 3 routing decision (DevPlan 100 §2.3): PARALLEL → _deploy_parallel, else → sequential.
## @io       ⇥ enabled_names, overlays, modules_dir, core_dir, deploy_parallel, deploy_orchestrator
##           ⎋ tuple[int, list[str], dict] — (deployed, failed, modules_info severity lookup)
## @complexity 1 — flag-based dispatch
## @invariants
##   - deploy_parallel=True AND enabled modules → _deploy_parallel (which may further route to
##     _deploy_orchestrator when deploy_orchestrator=True, per DevPlan §3 Phase 3)
##   - Sequential path returns empty modules_info {} → severity falls back to per-module metadata
def _route_deploy(
    enabled_names: list[str],
    overlays: dict[str, str],
    modules_dir: str,
    core_dir: str,
    *,
    deploy_parallel: bool,
    deploy_orchestrator: bool,
) -> tuple[int, list[str], dict[str, dict[str, str]]]:
    """Route to parallel or sequential deploy based on DEPLOY_PARALLEL flag."""
    if deploy_parallel and enabled_names:
        logger.info(
            "[IMP:9][_route_deploy][route] PARALLEL route (DEPLOY_PARALLEL=true) — %d modules", len(enabled_names)
        )
        return _deploy_parallel(
            enabled_names,
            overlays,
            modules_dir,
            core_dir,
            deploy_orchestrator=deploy_orchestrator,
        )
    logger.info("[IMP:9][_route_deploy][route] SEQUENTIAL route (DEPLOY_PARALLEL != true) — for-loop")
    deployed, failed = _deploy_sequential(enabled_names, modules_dir, core_dir, overlays)
    return deployed, failed, {}


# endregion FUNC__route_deploy


# region FUNC__deploy_parallel
## @purpose  DEPLOY_PARALLEL=true path: linearize (kahn) → pre-pull → batch-check-env →
##           (deploy-many | groups) → system modules → HC_DONE_MARKER. Returns enriched
##           modules dict for severity lookup.
## @io       ⇥ enabled_names, overlays, modules_dir, core_dir, deploy_orchestrator
##           ⎋ tuple[int, list[str], dict[str, dict[str, str]]] — (deployed, failed, modules_info)
## @complexity 4 — topo DAG + parallel group deploy + system dispatch
## @invariants
##   - topo_sort failure (dependency cycle / unknown depends_on) → ConfigValidationError
##     ПРОПАГИРУЕТСЯ (fail-fast до первого деплоя, REF-0110) — никакой unordered fallback
##   - pre-pull + batch-check-env are best-effort (`|| true` parity)
##   - deploy_orchestrator=True → orchestrator_cli deploy-many for DOCKER modules only (R4), which
##     REPLACES group-based deploy (DevPlan §3 either/or — see TRAP[DECISION])
##   - Group deploy failures ARE aggregated into failed (see TRAP[DECISION]);
##     critical-failure группы abort'ит оставшиеся группы (REF-0110)
##   - HC_DONE_MARKER always set at the end of the parallel path (best-effort)


# region FUNC__linearize_deploy_order
def _linearize_deploy_order(
    enabled_names: list[str],
    modules_dir: str,
) -> tuple[list[list[str]], dict[str, dict[str, str]]]:
    """kahn-линеаризация enabled-модулей по depends_on (REF-0110) → deploy groups + enriched dict.

    ▶ load_module_yamls → ◇ unknown-dep guard → filter_docker_modules → build_dag → kahn → ⟦(groups, modules_info)⟧

    Единый источник порядка для ОБЕИХ веток (parallel-группы И sequential-плоский порядок):
    раньше sequential шёл в порядке списка node.yaml, игнорируя depends_on (REF-0110).

    🧐 TRAP[DECISION] · 2026-08-25 · — · kahn-линеаризация для sequential + fail-fast topo-failure (REF-0110)
    · Rejected: сохранить node.yaml-порядок с WARN+sequential-fallback при ошибке topo (статус-кво)
    · Reason: канонический fresh-node путь (DEPLOY_PARALLEL=false) стартовал зависимые модули
      против отсутствующих зависимостей (crash-loop postgres-less litellm), а ошибка topo-sort
      молча деградировала в unordered; детерминированный порядок — precondition честного деплоя.
    · Rev: если появится легитимная циклическая soft-dep семантика между модулями — ввести
      dep_type=soft в module.yaml вместо возврата к unordered fallback.

    ## @invariants
    ##   - Цикл depends_on → ConfigValidationError из kahn_topological_sort (fail-fast ДО деплоя)
    ##   - Неизвестная зависимость (нет module.yaml ни у одного загруженного модуля) → ConfigValidationError;
    ##     зависимость на известный не-enabled/системный модуль молча отбрасывается (канон build_dag)
    ##   - enabled-модуль без module.yaml НЕ попадает в DAG — вызывающий добирает его хвостом
    ##     (легаси-путь: detect_install_type → docker → честный failed, поведение сохранено)
    ## @raises ConfigValidationError  На цикл или неизвестную зависимость.
    """
    all_modules = topo_sort.load_module_yamls(modules_dir)

    # ── unknown-dep guard: зависимость должна резолвиться в ЗАГРУЖЕННЫЙ модуль ──
    # build_dag молча роняет всё, что вне docker-set; опечатка в depends_on стала бы
    # невидимым отсутствием порядка — здесь она громкая конфигурационная ошибка.
    known_names = {str(m.get("name", "")) for m in all_modules if m.get("name")}
    enabled_set = set(enabled_names)
    for entry in all_modules:
        name = str(entry.get("name") or "")
        if name not in enabled_set:
            continue
        unknown = [d for d in topo_sort.extract_depends_on(entry) if d not in known_names]
        if unknown:
            msg = f"Module '{name}' declares unknown depends_on entries {unknown} — no module.yaml found for them"
            raise ConfigValidationError(msg)

    docker_modules = topo_sort.filter_docker_modules(all_modules)
    dag = topo_sort.build_dag(docker_modules, filter_names=enabled_names)
    groups: list[list[str]] = topo_sort.kahn_topological_sort(dag) if dag else []
    modules_info: dict[str, dict[str, str]] = {}
    for m in all_modules:
        name = m.get("name", "")
        if name:
            modules_info[name] = {
                "install_type": m.get("install_type", "unknown"),
                "severity": m.get("severity", "warn"),
            }
    logger.info("[IMP:9][_linearize_deploy_order][ok] Topo-sorted into %d deploy group(s)", len(groups))
    return groups, modules_info


# endregion FUNC__linearize_deploy_order


# region FUNC__deploy_docker_groups
def _deploy_docker_groups(
    groups: list[list[str]],
    overlays: dict[str, str],
    modules_dir: str,
    modules_info: dict[str, dict[str, str]] | None = None,
) -> tuple[int, list[str]]:
    """Последовательный деплой topo-групп с агрегацией failures (C901-extraction).

    REF-0110: critical-failure группы → abort оставшихся групп — зависимые не стартуют
    против откаченной зависимости; невыполненные группы добавляются в failed ЧЕСТНО
    (REF-0005 failed-учёт), severity-агрегация видит весь незадеплоенный хвост.

    🧐 TRAP[DECISION] · 2026-07-31 · — · Group failures aggregated into severity
    · Rejected: shell dropped deploy_docker_group failures (WARN only → exit 0 always)
    · Reason: DevPlan 100 §2.3 contract returns (deployed, failed); dropping failed_names
      made severity aggregation inert in parallel mode.
    · Rev: if deploy_docker_group semantics change (rollback makes failures non-actionable).
    """
    deployed = 0
    failed: list[str] = []
    info = modules_info or {}
    logger.info("[IMP:7][_deploy_parallel][groups] Deploying %d docker group(s) sequentially", len(groups))
    for g_idx, group in enumerate(groups):
        group_entries = _build_entries(group, overlays)
        logger.info(
            "[IMP:8][_deploy_parallel][group] Deploying group %d/%d: %s",
            g_idx,
            len(groups) - 1,
            group_entries,
        )
        fnames: list[str] = []
        try:
            d, _f, fnames, _rolled = docker_orchestrator.deploy_docker_group(group_entries, modules_dir)
            deployed += d
            failed.extend(fnames)
        # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: group failure continues to next group
        except Exception as exc:  # noqa: EXC — best-effort policy
            # QA R3/T2.C: сбой группы (включая OSError fork-фейлов) — честный failed-учёт
            # всей группы (паттерн :911-920 «все недоказанные = failed»); continue к следующей
            # группе сохранён (DEPLOY_BEST_EFFORT), но severity-агрегация больше не слепа.
            logger.error(
                "[IMP:10][_deploy_docker_groups][group] Group %d deploy error — marking %d module(s) failed: %s (%s)",
                g_idx,
                len(group),
                group,
                exc,
            )
            failed.extend(group)
        # ── REF-0110: critical-failure → dependents в следующих группах не стартуют ──
        critical_failed = [n for n in fnames if info.get(n, {}).get("severity", "warn") == "critical"]
        if critical_failed:
            remaining = [name for later in groups[g_idx + 1 :] for name in later]
            logger.error(
                "[IMP:10][_deploy_docker_groups][abort] Critical failure(s) %s in group %d — "
                "aborting %d remaining module(s): %s",
                critical_failed,
                g_idx,
                len(remaining),
                remaining,
            )
            failed.extend(remaining)
            break
    return deployed, failed


# endregion FUNC__deploy_docker_groups


def _deploy_parallel(
    enabled_names: list[str],
    overlays: dict[str, str],
    modules_dir: str,
    core_dir: str,
    *,
    deploy_orchestrator: bool,
) -> tuple[int, list[str], dict[str, dict[str, str]]]:
    """Parallel deploy: topo-sort groups + pre-pull + batch env check + group/orchestrator deploy."""
    secrets_manifest = os.path.join(core_dir, "secrets-manifest.yaml")
    logger.info("[IMP:9][_deploy_parallel][start] DEPLOY_PARALLEL=true — topo_sort + pre-pull + batch deploy")

    # REF-0110: ConfigValidationError (цикл/неизвестная зависимость) пробрасывается из
    # _linearize_deploy_order — fail-fast до первого деплоя, без unordered-фолбэка.
    groups, modules_info = _linearize_deploy_order(enabled_names, modules_dir)

    # ── 2. pre-pull docker images (best-effort — compose up retries pull) ──
    try:
        ok, fail = docker_orchestrator.pre_pull_images(_build_entries(enabled_names, overlays), modules_dir)
        logger.info("[IMP:9][_deploy_parallel][pre_pull] Pre-pull complete: success=%d failed=%d", ok, fail)
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — pre-pull non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_deploy_parallel][pre_pull] Pre-pull error (non-fatal): %s", exc)

    # ── 3. batch-check-env (one call replaces per-module check-env) ──
    # 📝 TRAP[DEBT] · 2026-08-26 · MED · batch-check-env результат не гейтит parallel-deploy
    # · Observed: env_results вычисляется и логируется, но не читается ни одной веткой деплоя
    #   (deploy-many и групповые ветки не получают env-вердиктов)
    # · Suspected: гейтинг потерян при декомпозиции deploy-modules.sh; параллельный путь
    #   исторически полагается на серверные проверки
    # · Impact: required-secrets gate фактически отсутствует в DEPLOY_PARALLEL=true пути
    #   (краш валидатора сейчас громкий, но pass-результаты всё равно не применяются)
    # · When: DevPlan 17 T1.1 (фикс sequential-сайта; parallel-гейтинг — отдельный триаж)
    env_results: list[dict[str, str]] = []
    try:
        env_results = secrets_validator.batch_check_env(modules_dir, secrets_manifest)
        logger.info(
            "[IMP:9][_deploy_parallel][batch_check_env] batch-check-env completed for %d modules", len(env_results)
        )
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT/best-effort: краш batch-check-env НЕ эквивалентен
    # «нет missing» (AI-0011 класс): делаем краш громким (fail-closed сигнал в лог), но
    # параллельный путь исторически не гейтится env_results (см. DEBT ниже) — поведение не меняем
    except Exception as exc:  # noqa: EXC — loud non-fatal, best-effort policy: env gate unavailable, risk surfaced
        logger.error(
            "[IMP:9][_deploy_parallel][batch_check_env_error] validator crashed — env gate NOT enforced this run: %s",
            exc,
        )
        env_results = []

    deployed = 0
    failed: list[str] = []

    # ── 4. deploy docker modules: orchestrator_cli deploy-many XOR group-based deploy ──
    docker_names = [n for n in enabled_names if modules_info.get(n, {}).get("install_type") == "docker"]
    if deploy_orchestrator and docker_names:
        # 🧐 TRAP[DECISION] · 2026-07-31 · — · DeployOrchestrator path REPLACES group deploy (either/or)
        # · Rejected: 260-LOC shell ran deploy-many AND group deploy (comment said "Skip the rest"
        #   but no skip was implemented → double deploy). Also passed ALL module names incl. system.
        # · Reason: DevPlan 100 §3 Phase 3 specifies IF deploy_orchestrator → deploy-many, ELSE → groups.
        #   R4 risk confirms orchestrator is for docker modules only. Either/or fixes the double-deploy.
        # · Rev: if DeployOrchestrator gains system-module support → extend docker_names filter.
        orch_deployed, orch_failed = _deploy_orchestrator(docker_names)
        deployed += orch_deployed
        failed.extend(orch_failed)
    elif groups:
        g_deployed, g_failed = _deploy_docker_groups(groups, overlays, modules_dir, modules_info)
        deployed += g_deployed
        failed.extend(g_failed)
    else:
        logger.info("[IMP:5][_deploy_parallel][groups] No docker groups from topo_sort — skipping group-based deploy")

    # ── 5. system modules (sequential via invoke_module_interface) ──
    system_names = [n for n in enabled_names if modules_info.get(n, {}).get("install_type") == "system"]
    if system_names:
        sys_deployed, sys_failed = _deploy_system_modules(system_names)
        deployed += sys_deployed
        failed.extend(sys_failed)

    # ── 6. HC_DONE_MARKER — signals state_machine.py to skip standalone healthcheck ──
    # REF-0005: только при failed==[] — упавший деплой не имеет права гасить φ11 healthcheck.
    _set_hc_marker(failed)

    logger.info("[IMP:9][_deploy_parallel][done] deployed=%d failed=%s", deployed, failed)
    return deployed, failed, modules_info


# endregion FUNC__deploy_parallel


# region FUNC__deploy_orchestrator
## @purpose  DeployOrchestrator CLI path (DEPLOY_ORCHESTRATOR=true): subprocess orchestrator_cli
##           deploy-many. Separate CLI layer (core/internal/deploy/) — subprocess by design (D1 exception).
##           DevPlan 116 B1 T6 (D7): канал = LocalChannel (БЕЗ --scp) — на-ноде операция: payload
##           уже в /opt/projects/\<module\>/; SCP-доставка самой себе бессмысленна (тот же прецедент
##           TRAP[DECISION] receive 2026-07-31). JSON-вывод deploy-many парсится → честные
##           (deployed, failed) вместо всегда (0, []) (U-30).
## @io       ⇥ docker_names: list[str] → ⎋ tuple[int, list[str]] — (deployed, failed)
## @complexity 1 — single subprocess + JSON-парсинг вывода
## @invariants
##   - Only DOCKER module names passed (R4) — names = docker compose project names
##   - НЕТ --scp: build_channel в orchestrator_cli вернёт LocalChannel (D7, T6)
##   - JSON-массив ModuleDeployResult парсится: deployed = count(status == DEPLOYED),
##     failed = [project for status in (FAILED, ROLLED_BACK)] (U-30, честная наблюдаемость)
##   - Failure is WARN-only (best-effort: orchestrator failures never added to FAILED severity,
##     но наблюдаемы через failed-список — DEPLOY_BEST_EFFORT, B4)
##   - returncode != 0 → WARN + честный (deployed, failed)-из-JSON
def _deploy_orchestrator(
    docker_names: list[str], run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None
) -> tuple[int, list[str]]:
    """Call orchestrator_cli deploy-many via subprocess (separate CLI, separate concern — D1 exception).

    D7: LocalChannel — deploy-many выполняется НА ноде (payload уже на месте), SCP-транспорт
    самому себе бессмыслен; поэтому `--scp` НЕ передаётся (build_channel → LocalChannel).

    DI (W-H DevPlan 163): run_cmd=None → subprocess.run (канон); тесты передают fake-канал.
    """
    if not docker_names:
        logger.info("[IMP:8][_deploy_orchestrator][skip] No docker modules — skipping deploy-many")
        return 0, []

    projects = ",".join(docker_names)
    # D7/T6: БЕЗ --scp/--forced-command/--host → build_channel вернёт LocalChannel (на-ноде).
    cmd = [
        sys.executable,
        "-m",
        "core.internal.deploy.orchestrator_cli",
        "deploy-many",
        "--projects",
        projects,
    ]
    logger.info("[IMP:9][_deploy_orchestrator][start] DeployOrchestrator deploy-many: %s (LocalChannel, D7)", projects)
    runner = subprocess.run if run_cmd is None else run_cmd
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=DEPLOY_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        # REF-0103: таймаут/ошибка запуска → ВСЕ незавершённые проекты = failed (честный сигнал).
        # Прежний return (0, []) маскировал убитый deploy-many как «0 фейлов» → exit 0.
        logger.warning("[IMP:5][_deploy_orchestrator][error] deploy-many error (non-fatal): %s", exc)
        logger.warning(
            "[IMP:7][_deploy_orchestrator][error] deploy-many incomplete — marking ALL %d project(s) failed: %s",
            len(docker_names),
            docker_names,
        )
        return 0, list(docker_names)

    # ── Парсинг JSON-вывода deploy-many (U-30): JSON-массив ModuleDeployResult ──
    deployed = 0
    failed: list[str] = []
    seen_deployed: set[str] = set()
    seen_total: set[str] = set()
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        # W11: json.loads → Any — каст к object, isinstance-гейт и per-entry каст сохраняются
        parsed = cast(object, json.loads(result.stdout) if result.stdout.strip() else [])
        if isinstance(parsed, list):
            for entry in parsed:  # pyright: ignore[reportUnknownVariableType] — W11: элемент JSON-массива deploy-many
                if not isinstance(entry, dict):
                    continue
                edict = cast(dict[str, str], entry)
                project_name = edict.get("project", "?")
                seen_total.add(project_name)
                status = edict.get("status", "")
                if status == "DEPLOYED":
                    deployed += 1
                    seen_deployed.add(project_name)
                elif status in {"FAILED", "ROLLED_BACK"}:
                    failed.append(project_name)
    except json.JSONDecodeError as exc:
        # QA R3/T2.C: битый вывод = НОЛЬ доказательств успеха. Прежний WARN с
        # deployed=0/failed=[] маскировал полный провал как «всё чисто».
        logger.error(
            "[IMP:10][_deploy_orchestrator][parse] deploy-many stdout не JSON (%.120r): %s "
            "— zero proof of success, marking ALL %d project(s) failed",
            result.stdout,
            exc,
            len(docker_names),
        )
        return 0, list(docker_names)

    if result.returncode != 0:
        # QA R3/T2.C: rc≠0 → честный failed-учёт + severity CRIT (не WARN-only).
        # Проекты БЕЗ DEPLOYED-записи в выводе (упали до записи результата) — недоказанные,
        # идут в failed наравне с явными FAILED/ROLLED_BACK.
        unproven = [n for n in docker_names if n not in seen_deployed]
        for name in unproven:
            if name not in failed:
                failed.append(name)
        logger.critical(
            "[IMP:10][_deploy_orchestrator][fail] deploy-many exit=%d — %d unproven project(s) "
            "added to failed: %s (total failed=%s)",
            result.returncode,
            len(unproven),
            unproven,
            failed,
        )
    logger.info(
        "[IMP:9][_deploy_orchestrator][done] deploy-many: deployed=%d failed=%s",
        deployed,
        failed,
    )
    return deployed, failed


# endregion FUNC__deploy_orchestrator


# region FUNC__deploy_sequential
## @purpose  sequential path (DEPLOY_PARALLEL != true): kahn-ordered for-loop over enabled modules —
##           linearize(depends_on) → check-env → detect-type → deploy_docker_module |
##           invoke_module_interface. REF-0110: порядок = топологическая линеаризация (НЕ порядок
##           списка node.yaml); critical-failure → abort remaining.
## @io       ⇥ enabled_names, modules_dir, core_dir, overlays, secrets_env_file, platform_root
##           ⎋ tuple[int, list[str]] — (deployed, failed)
## @complexity 3 — linearize + for-loop with per-module env/type/deploy dispatch
## @invariants
##   - Порядок деплоя docker-модулей = flatten(kahn groups); хвост (system-модули и enabled
##     без module.yaml) добирается в исходном относительном порядке как виртуальная
##     финальная группа (индекс len(groups))
##   - ConfigValidationError (цикл/неизвестная зависимость) ПРОПАГИРУЕТСЯ — fail-fast до деплоя
##   - Critical-failure модуля → abort ПОСЛЕДУЮЩИХ групп (+хвост); соседи по той же группе
##     независимы (kahn) и продолжают деплоиться; невыполненные добавляются в failed (честный
##     учёт), severity-агрегация даёт exit 2; warn-failure продолжает цикл (DEPLOY_BEST_EFFORT)
##   - Missing env vars → module FAILED + skipped; краш валидатора (env_check_error) → ТОЖЕ
##     module FAILED (sentinel missing, fail-closed) — AI-0011: required-secrets L1-класс,
##     DEPLOY_BEST_EFFORT не распространяется; счётчик env_check_errors в [done]-сводке
##   - install_type "system" → invoke_module_interface install + best-effort healthcheck liveness
##   - Everything else (docker/unknown) → deploy_docker_module (module.yaml missing → docker path,
##     best-effort — compose resolution fails there)
##   - Overlay dir IS passed in sequential path: overlay_dir=(overlays or {}).get(m_name) — паритет
##     с параллельным путём (deploy_docker_group), DevPlan 121 fix 812592b
##   - secrets_env_file/platform_root прокидываются в deploy_docker_module (паритет с
##     parallel_runner.deploy_docker_group, DevPlan 123 T8). Оба пути сходятся на одних и тех же
##     дефолтах _build_compose_args на ноде: platform_root or platform_remote_base() == /opt/platform,
##     secrets_env_file or "/var/lib/platform/run/secrets.env" — значения идентичны (дефолты совпадают).
def _deploy_sequential(
    enabled_names: list[str],
    modules_dir: str,
    core_dir: str,
    overlays: dict[str, str] | None = None,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
) -> tuple[int, list[str]]:
    """Sequential deploy in topological order (kahn по depends_on — REF-0110)."""
    secrets_manifest = os.path.join(core_dir, "secrets-manifest.yaml")
    deployed = 0
    failed: list[str] = []
    env_check_errors = 0
    logger.info(
        "[IMP:9][_deploy_sequential][start] DEPLOY_PARALLEL=false — sequential for-loop over %d modules",
        len(enabled_names),
    )

    # ── REF-0110: kahn-линеаризация (ConfigValidationError пробрасывается — fail-fast) ──
    groups, topo_info = _linearize_deploy_order(enabled_names, modules_dir)
    # Хвост вне DAG: system-модули + enabled без module.yaml — легаси-пер-модульный путь,
    # виртуальная финальная группа (индекс len(groups)).
    docker_names_set = {name for group in groups for name in group}
    tail = [n for n in enabled_names if n not in docker_names_set]
    order_plan: list[tuple[int, str]] = [(g_idx, name) for g_idx, group in enumerate(groups) for name in group]
    order_plan += [(len(groups), n) for n in tail]
    ordered = [name for _, name in order_plan]
    logger.info("[IMP:9][_deploy_sequential][order] Deploy order (depends_on-aware): %s", ordered)

    for idx, m_name in enumerate(ordered):
        # ⚠️ TRAP[BUG] · 2026-08-26 · P1 · краш env-check валидатора молча пропускал модуль без required secrets
        # · Symptom: secrets_validator.check_env_requires raise → warning IMP:8 → missing=[] → модуль деплоился
        #   без проверки required secrets (неотличим от pass)
        # · Root: except-ветка DEPLOY_BEST_EFFORT глотала краш валидатора как «нет missing»
        # · Fix: sentinel missing=["<env_check_error>: …"] → module FAILED (fail-closed) + счётчик
        #   env_check_errors в [done]-сводке; DEPLOY_BEST_EFFORT не распространяется на L1-класс
        # · Prevention: tests/unit/test_deploy_orchestrator_envcheck.py (crash_is_loud + pass_deploys R5)
        # ── env check (missing vars → fail module, skip deploy) ──
        try:
            missing = secrets_validator.check_env_requires(m_name, secrets_manifest)
        # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT/best-effort НЕ покрывает required-secrets
        # (L1-класс: безопасность блокирует на любом уровне) → краш конвертируется в громкий
        # fail-closed фейл модуля, а не в тихий pass (AI-0011)
        except Exception as exc:  # noqa: EXC — DEPLOY_BEST_EFFORT не покрывает L1 required-secrets → loud fail-closed
            env_check_errors += 1
            logger.error(
                "[IMP:9][_deploy_sequential][env_check_error] validator crashed for %s — failing module (fail-closed): %s",
                m_name,
                exc,
            )
            missing = [f"<env_check_error>: {exc}"]
        if missing:
            logger.warning(
                "[IMP:8][_deploy_sequential][env_fail] Missing env vars for %s (%s) — skipping deploy",
                m_name,
                ",".join(missing),
            )
            failed.append(m_name)
            continue

        # ── install type detect (module.yaml path — verified signature) ──
        itype = secrets_validator.detect_install_type(os.path.join(modules_dir, m_name, "module.yaml"))
        if itype == "system":
            if _invoke_module_interface(m_name, "install"):
                deployed += 1
            else:
                failed.append(m_name)
            _ = _invoke_module_interface(
                m_name, "healthcheck", "liveness", timeout=HEALTHCHECK_CMD_TIMEOUT
            )  # best-effort, non-fatal (REF-0103: 60s probe)
        else:
            try:
                ok = docker_orchestrator.deploy_docker_module(
                    m_name,
                    modules_dir=modules_dir,
                    overlay_dir=(overlays or {}).get(m_name),
                    secrets_env_file=secrets_env_file,
                    platform_root=platform_root,
                )
            # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
            except Exception as exc:  # noqa: EXC — docker deploy failure → module failed, continue (best-effort: DEPLOY_BEST_EFFORT policy)
                logger.warning("[IMP:8][_deploy_sequential][docker] deploy error for %s: %s", m_name, exc)
                ok = False
            if ok:
                deployed += 1
            else:
                failed.append(m_name)

        # ── REF-0110: critical-failure → dependents в ПОСЛЕДУЮЩИХ группах не стартуют ──
        # (соседи по текущей группе независимы по построению kahn — продолжают деплоиться)
        if m_name in failed and topo_info.get(m_name, {}).get("severity", "warn") == "critical":
            failed_group_idx = order_plan[idx][0]
            remaining = [name for g_idx2, name in order_plan[idx + 1 :] if g_idx2 > failed_group_idx]
            logger.error(
                "[IMP:10][_deploy_sequential][abort] Critical failure: %s — aborting %d dependent module(s): %s",
                m_name,
                len(remaining),
                remaining,
            )
            failed.extend(remaining)
            break

    logger.info(
        "[IMP:9][_deploy_sequential][done] deployed=%d failed=%s env_check_errors=%d",
        deployed,
        failed,
        env_check_errors,
    )
    return deployed, failed


# endregion FUNC__deploy_sequential


# region FUNC__deploy_system_modules
## @purpose  Deploy system modules sequentially via invoke_module_interface install + liveness healthcheck.
## @io       ⇥ system_names: list[str] → ⎋ tuple[int, list[str]] — (deployed, failed)
## @complexity 2 — linear loop with per-module shell dispatch
## @invariants
##   - healthcheck liveness is best-effort (`2>/dev/null || true` parity)
##   - install failure → module added to failed list (affects severity aggregation)
def _deploy_system_modules(system_names: list[str]) -> tuple[int, list[str]]:
    """Sequential system module deploy via invoke_module_interface (shell wrapper call — D4)."""
    deployed = 0
    failed: list[str] = []
    logger.info(
        "[IMP:7][_deploy_system_modules][start] Deploying %d system module(s): %s", len(system_names), system_names
    )
    for m_name in system_names:
        logger.info("[IMP:8][_deploy_system_modules][deploy] Installing system module: %s", m_name)
        if _invoke_module_interface(m_name, "install"):
            deployed += 1
        else:
            failed.append(m_name)
        _ = _invoke_module_interface(
            m_name, "healthcheck", "liveness", timeout=HEALTHCHECK_CMD_TIMEOUT
        )  # best-effort (REF-0103: 60s probe)
    logger.info("[IMP:9][_deploy_system_modules][done] deployed=%d failed=%s", deployed, failed)
    return deployed, failed


# endregion FUNC__deploy_system_modules


# region FUNC__invoke_module_interface
## @purpose  Invoke a module interface (install/healthcheck/...) — ДЕЛЕГИРУЕТ в единую bash-обёртку
##           shared/module_interface.invoke (DevPlan 118 C5). D4 — intentional subprocess для
##           shell-операций (module-interface.sh поверх module.yaml#interfaces).
## @io       ⇥ module_name: str, interface: str, *args: str → ⎋ bool (True on exit 0)
## @complexity 1 — single bash subprocess (делегирование в shared)
## @invariants
##   - Сборка bash -c и экранирование — в shared/module_interface (единый источник, C5)
##   - Failure/timeout → False (never raises)
##   - Timeout 180s (COMPOSE_UP_TIMEOUT канон) — install can take minutes for system services
##   - REF-0103: healthcheck liveness-инвок передаёт timeout=HEALTHCHECK_CMD_TIMEOUT (60) —
##     полный COMPOSE_UP_TIMEOUT=180 предназначен для install, не для probe
def _invoke_module_interface(module_name: str, interface: str, *args: str, timeout: int = COMPOSE_UP_TIMEOUT) -> bool:
    """Call invoke_module_interface (bash) — delegating to shared/module_interface.invoke (C5)."""
    success, _ = module_interface_invoke(module_name, interface, *args, timeout=timeout)
    return success


# endregion FUNC__invoke_module_interface


# region FUNC__postflight
## @purpose  PHASE 4: post-deploy housekeeping — sudoers batch generation, orphan container
##           reconciliation (detect + self-heal remove, S2-A DevPlan 140 W5), litellm-config.yml
##           render. Independent of deploy outcome.
## @io       ⇥ all_names, enabled_names, modules_dir, core_dir, templates_dir → ⎋ None
## @complexity 2 — 3 guarded calls (each non-fatal, `|| true` parity)
## @invariants
##   - sudoers batch uses ALL module names (--module-names "$ALL_NAMES" parity)
##   - orphan reconciliation uses ENABLED module names (--module-entries "$ENABLED_NAMES" parity)
##   - remove_orphans вызывается внутри того же try (self-heal); remove_orphans сам безопасен —
##     логирует «No orphans to remove» при пустом списке (orphan_reconciler.py remove_orphans)
##   - platform_root for sudoers derived as core_dir parent (core/.. == project root on VPS)
def _postflight(
    all_names: list[str],
    enabled_names: list[str],
    modules_dir: str,
    core_dir: str,
    templates_dir: str,
) -> None:
    """Run post-deploy housekeeping (all steps non-fatal)."""
    # ── sudoers batch generation (single /etc/sudoers.d/platform-modules file) ──
    try:
        ok = sudoers_generator.batch_generate_sudoers(
            all_names,
            Path(modules_dir),
            Path(templates_dir),
            str(Path(core_dir).parent),
        )
        logger.info("[IMP:8][_postflight][sudoers] batch_generate ok=%s", ok)
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — sudoers non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_postflight][sudoers] error (non-fatal): %s", exc)

    # ── orphan container reconciliation (batch: detect → self-heal remove, S2-A DevPlan 140 W5) ──
    try:
        orphans = orphan_reconciler.batch_orphan_reconciliation(enabled_names, modules_dir)
        logger.info("[IMP:8][_postflight][orphans] %d orphan(s) detected", len(orphans))
        removed = orphan_reconciler.remove_orphans(orphans)
        logger.info("[IMP:9][_postflight][orphans] removed %d orphan(s)", removed)
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — orphan detection non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_postflight][orphans] error (non-fatal): %s", exc)

    # ── plan 012 T14 (F-027): контейнеры ВЫКЛЮЧЕННЫХ модулей (enabled:false → снять;
    #    volumes НЕ затрагиваются — docker rm без -v) ──
    _reconcile_disabled_module_containers(enabled_names, modules_dir)

    # ── litellm-config.yml render (best-effort — existing config kept on failure) ──
    _render_litellm_config(core_dir)


# endregion FUNC__postflight


# region FUNC__reconcile_disabled_module_containers
## @purpose  plan 012 T14 (F-027): детекция + снятие контейнеров ВЫКЛЮЧЕННЫХ модулей
##           (containers only — volumes НЕ затрагиваются, docker rm без -v). Non-fatal.
## @io       ⇥ enabled_names: list[str], modules_dir: str → ⎋ None
## @complexity O(D) — D выключенных модулей (compose config + docker ps)
## @invariants
##   - ЛЮБОЙ сбой → WARN, never raise (DEPLOY_BEST_EFFORT policy)
##   - Volumes сохраняются (remove_orphans → docker rm -f, без -v)
def _reconcile_disabled_module_containers(enabled_names: list[str], modules_dir: str) -> None:
    """Remove containers of disabled modules (plan 012 T14 / F-027), volumes kept."""
    try:
        disabled_orphans = orphan_reconciler.detect_disabled_module_containers(enabled_names, modules_dir)
        if not disabled_orphans:
            logger.info("[IMP:7][_postflight][disabled-modules] no disabled-module containers")
            return
        removed = orphan_reconciler.remove_orphans(disabled_orphans)
        logger.info(
            "[IMP:9][_postflight][disabled-modules] removed %d disabled-module container(s) (volumes kept)",
            removed,
        )
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — disabled-module orphan detection non-fatal (best-effort policy)
        logger.warning("[IMP:5][_postflight][disabled-modules] error (non-fatal): %s", exc)


# endregion FUNC__reconcile_disabled_module_containers


# region FUNC__render_litellm_config
## @purpose  Render litellm-config.yml from policy.yaml (non-fatal). Резюме-строка — из
##           orchestrator_metrics.render_llm_summary (E6, pure); I/O (render_to_file) здесь.
## @io       ⇥ core_dir: str → ⎋ None (side-effect: litellm-config.yml)
## @complexity 1 — path resolution + render call
## @invariants
##   - policy: core_dir/internal/llm/policy.yaml; output: core_dir/modules/litellm/config/litellm-config.yml
##   - Render failure logs WARN and keeps existing config (`|| { echo WARNING }` parity)
def _render_litellm_config(core_dir: str) -> None:
    """Render litellm-config.yml from policy.yaml (non-fatal)."""
    policy_path = Path(core_dir) / "internal" / "llm" / "policy.yaml"
    output_path = litellm_config_path(core_dir)  # C6: единый путь shared/llm_paths
    logger.info(
        "[IMP:7][_render_litellm_config][start] %s",
        _metrics_render_llm_summary(core_dir, str(policy_path), str(output_path)),
    )
    try:
        config_renderer.render_to_file(policy_path, output_path)
        logger.info("[IMP:9][_render_litellm_config][done] litellm-config.yml rendered")
    # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
    except Exception as exc:  # noqa: EXC — render non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:8][_render_litellm_config][warn] render failed (non-fatal): %s", exc)


# endregion FUNC__render_litellm_config


# region FUNC__aggregate_severity
## @purpose  PHASE 5: map failed modules to severity (critical|warn). I/O-обёртка: резолвит
##           severity_map (enriched modules dict из topo_sort + per-module module.yaml fallback),
##           чистая агрегация делегирована в orchestrator_metrics.aggregate_severity (E6).
## @io       ⇥ failed: list[str], modules_info: dict[str, dict[str, str]], modules_dir: str
##           ⎋ tuple[int, int] — (crit_count, warn_count)
## @complexity 2 — linear lookup with per-module fallback (резолв) + pure aggregation
## @invariants
##   - severity defaults to "warn" for unknown modules (default warn severity)
##   - fallback reads module.yaml severity field (secrets_validator.get_module_severity)
##   - Чистая агрегация — в orchestrator_metrics (E6, R5: test_orchestrator_metrics_pure)
def _aggregate_severity(
    failed: list[str],
    modules_info: dict[str, dict[str, str]],
    modules_dir: str,
) -> tuple[int, int]:
    """Aggregate failed module severities into (crit_count, warn_count)."""
    severity_map: dict[str, str] = {}
    for name in failed:
        severity = "warn"
        if name in modules_info:
            severity = modules_info[name].get("severity", "warn")
        else:
            try:
                severity = secrets_validator.get_module_severity(os.path.join(modules_dir, name, "module.yaml"))
            # ruff: ignore[BLE001] — DEPLOY_BEST_EFFORT: широкий спектр helper-API (git/yaml/jinja/subprocess/docker)
            except Exception as exc:  # noqa: EXC — severity fallback failure → default warn (best-effort: DEPLOY_BEST_EFFORT policy)
                logger.warning("[IMP:7][_aggregate_severity][fallback] severity lookup failed for %s: %s", name, exc)
        severity_map[name] = severity
    crit, warn = _metrics_aggregate_severity(failed, severity_map)
    logger.info("[IMP:9][_aggregate_severity][result] crit=%d warn=%d", crit, warn)
    return crit, warn


# endregion FUNC__aggregate_severity


# region FUNC__compute_exit_code
## @purpose  Compute final exit code: CRIT>0 → 2, WARN>0 → 0 (logged), no failures → 0.
##           Чистое вычисление — orchestrator_metrics.exit_code_from_results (E6); здесь логгинг.
##           plan 012 T9 (F-015b): strict_init — failed≠∅ ИЛИ crit>0 → 2 (init fail-loud,
##           state.json=failed, resumable); update сохраняет WARN→0 (DEPLOY_BEST_EFFORT).
## @io       ⇥ crit: int, warn: int, deployed: int, failed: list | None, strict_init: bool → ⎋ int
## @complexity 1 — delegation + logging
## @invariants
##   - WARN maps to exit 0 in update mode (DEPLOY_BEST_EFFORT — warnings non-critical)
##   - strict_init: ЛЮБОЙ failed-модуль (включая warn-severity) или crit>0 → exit 2
##   - IMP:9 summary deployed=N failed=[...] в обоих режимах
##   - _metrics_exit_code (orchestrator_metrics.exit_code_from_results) принимает (crit, warn) —
##     deployed удалён волной 17 T7.1 (был неиспользуем, ruff: ignore[ARG001])
def _compute_exit_code(
    crit: int,
    warn: int,
    deployed: int,
    *,
    failed: list[str] | None = None,
    strict_init: bool = False,
) -> int:
    """Severity-based exit code; strict_init escalates any failure to exit 2 (plan 012 T9)."""
    if strict_init and (crit > 0 or failed):
        logger.error(
            "[IMP:9][_compute_exit_code][strict-init] INIT failed≠∅ (deployed=%d failed=%s crit=%d) "
            "→ exit 2 (resumable: повтор bootstrap доводит)",
            deployed,
            failed,
            crit,
        )
        return EXIT_CRITICAL
    code = _metrics_exit_code(crit, warn)
    if code == EXIT_CRITICAL:
        logger.error("[IMP:10][_compute_exit_code][critical] Critical:%d Warn:%d → exit 2", crit, warn)
    elif warn > 0:
        logger.warning(
            "[IMP:8][_compute_exit_code][warn] Warn:%d (non-critical — continuing) → exit 0 "
            "[IMP:9][summary] deployed=%d failed=%s",
            warn,
            deployed,
            failed,
        )
    else:
        logger.info("[IMP:9][_compute_exit_code][done] Deploy complete: %d modules (warnings: 0) → exit 0", deployed)
    return code


# endregion FUNC__compute_exit_code


# region FUNC__set_hc_marker
## @purpose  Touch the run-scoped healthcheck-done marker — signals state_machine.py to skip
##           the standalone healthcheck (healthcheck already ran inside deploy_docker_group).
##           REF-0005: маркер пишется ТОЛЬКО при failed==[] (success-marker после доказательства)
##           и содержит run-id (YYYYMMDDTHHMMSS-pid) — чужой запуск не гасит наш healthcheck;
##           stale-варианты снимаются свипом на старте φ8/φ12 (phases/docker._sweep_stale_hc_markers).
## @io       ⇥ failed: list[str] | None — имена упавших модулей параллельного пути
##           ⎋ None (side-effect: marker file при failed==[])
## @complexity 1 — guard + mkdir + touch with graceful failure
## @invariants
##   - failed непустой → маркер НЕ пишется (φ11 выполнит standalone healthcheck)
##   - Путь = orchestrator_metrics.hc_marker_path(context) + "." + run-id (единый SoT базы;
##     читатель docker.py резолвит тот же префикс — формат суффикса 8digits-T-6digits-digits)
##   - DEPLOY_BEST_EFFORT: сбой записи — WARN, не raise
def _set_hc_marker(failed: list[str] | None = None) -> None:
    """Create the run-scoped healthcheck-done marker ONLY on zero failures (REF-0005 honesty)."""
    if failed:
        logger.warning(
            "[IMP:8][_set_hc_marker][skip] %d failed module(s) %s — marker NOT written; "
            "standalone healthcheck will run in registry-update",
            len(failed),
            failed,
        )
        return
    base_path = _metrics_hc_marker_path(os.environ.get("CONTEXT"))
    # Run-id: время+PID деплой-процесса — уникален в пределах прогона, self-describing в логах.
    marker_path = f"{base_path}.{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    try:
        Path(Path(marker_path).parent).mkdir(exist_ok=True, parents=True)
        Path(marker_path).touch(exist_ok=True)
        logger.info("[IMP:9][_set_hc_marker][done] Created %s — standalone healthcheck will be skipped", marker_path)
    except OSError as exc:
        logger.warning("[IMP:7][_set_hc_marker][warn] Cannot create marker %s: %s", marker_path, exc)


# endregion FUNC__set_hc_marker


# region FUNC__create_status_metrics_json
## @purpose  Pre-create /var/lib/platform/run/status-metrics.json as valid empty JSON (P1 fix) — prevents
##           Docker from creating it as a directory during bind mount. Сериализация шаблона —
##           orchestrator_metrics.status_metrics_json (E6, pure).
## @io       ⇥ None → ⎋ None (side-effect: JSON file)
## @complexity 1 — existence check + mkdir + write
def _create_status_metrics_json() -> None:
    """Pre-create status-metrics.json if absent (non-fatal on failure)."""
    if Path(_STATUS_METRICS_PATH).exists():
        return
    try:
        Path(Path(_STATUS_METRICS_PATH).parent).mkdir(exist_ok=True, parents=True)
        with Path(_STATUS_METRICS_PATH).open("w", encoding="utf-8") as fh:
            _ = fh.write(_metrics_status_metrics_json())
        logger.info("[IMP:8][_create_status_metrics_json][done] Created %s placeholder", _STATUS_METRICS_PATH)
    except OSError as exc:
        logger.warning("[IMP:7][_create_status_metrics_json][warn] Cannot create %s: %s", _STATUS_METRICS_PATH, exc)


# endregion FUNC__create_status_metrics_json


# region FUNC__build_entries
## @purpose  Build "module:overlay" entry strings for docker_orchestrator (pre-pull / deploy-group).
## @io       ⇥ names: list[str], overlays: dict[str, str] → ⎋ list[str]
## @complexity 1 — linear formatting
def _build_entries(names: list[str], overlays: dict[str, str]) -> list[str]:
    """Build module:overlay entry strings (bare name when no overlay)."""
    entries: list[str] = []
    for name in names:
        overlay = overlays.get(name, "")
        entries.append(f"{name}:{overlay}" if overlay else name)
    return entries


# endregion FUNC__build_entries


# region FUNC_main
class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    node_yaml: str
    modules_dir: str
    core_dir: str
    templates_dir: str
    modules_filter: str
    deploy_parallel: str
    deploy_orchestrator: str
    strict_init: str


## @purpose  CLI entry point: argparse → orchestrate() → exit code (called via `exec python3` from the facade).
## @io       sys.argv → int exit code {0,1,2,4} — 4 = ConfigValidationError (REF-0110 topo fail-fast)
## @complexity 1 — argparse + delegation
## @invariants
##   - --deploy-parallel / --deploy-orchestrator accept "true"/"false" strings (shell flag parity)
##   - PlatformError (ConfigValidationError топо-цикла/неизвестной зависимости) → e.exit_code
##     без traceback (canon exit-code контракт: 4 = ConfigValidation)
##   - Logging to stderr (keeps stdout clean for any tooling)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point — orchestrate() wrapper (facade executes this via `exec python3`)."""
    parser = argparse.ArgumentParser(
        description="Deploy modules orchestrator — routing + severity aggregation (DevPlan 100)"
    )
    _ = parser.add_argument("--node-yaml", required=True, help="Path to node.yaml (NODE_YAML env from facade)")
    _ = parser.add_argument("--modules-dir", required=True, help="Path to core/modules/ (PATHS_MODULES_DIR)")
    _ = parser.add_argument("--core-dir", required=True, help="Path to core/ (PATHS_CORE_DIR)")
    _ = parser.add_argument("--templates-dir", required=True, help="Path to core/templates/ (PATHS_TEMPLATES_DIR)")
    _ = parser.add_argument("--modules-filter", default="", help="Comma/space-separated module filter (--modules)")
    _ = parser.add_argument(
        "--deploy-parallel",
        default="false",
        choices=["true", "false"],
        help="DEPLOY_PARALLEL flag (default: false → sequential)",
    )
    _ = parser.add_argument(
        "--deploy-orchestrator",
        default="false",
        choices=["true", "false"],
        help="DEPLOY_ORCHESTRATOR flag (default: false)",
    )
    _ = parser.add_argument(
        "--strict-init",
        default="false",
        choices=["true", "false"],
        help="plan 012 T9: init-режим fail-loud — failed≠∅ ИЛИ crit>0 → exit 2 (default: false)",
    )
    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logger.info("[IMP:7][main][start] node_yaml=%s", args.node_yaml)

    # DevPlan 123 T6: --deploy-parallel/--deploy-orchestrator приходят из shell-строк
    # (deploy-modules.sh прокидывает DEPLOY_PARALLEL/DEPLOY_ORCHESTRATOR env). argparse
    # choices=["true","false"] уже ограничивает регистр, но нормализация сравнения
    # (вместо строгого == "true") устойчива к любому регистру и проходит гейт булевых
    # литералов — обоснование выбора: нормализация предпочтительнее per-line allowlist.
    try:
        result = orchestrate(
            node_yaml=args.node_yaml,
            modules_dir=args.modules_dir,
            core_dir=args.core_dir,
            templates_dir=args.templates_dir,
            modules_filter=args.modules_filter,
            deploy_parallel=(args.deploy_parallel or "").lower() == "true",
            deploy_orchestrator=(args.deploy_orchestrator or "").lower() == "true",
            strict_init=(args.strict_init or "").lower() == "true",
        )
    except PlatformError as exc:
        # REF-0110: топо-цикл / неизвестная зависимость → exit 4 (ConfigValidation) без traceback.
        logger.error("[IMP:10][main][config-error] %s (exit_code=%d)", exc, exc.exit_code)
        return exc.exit_code
    logger.info("[IMP:9][main][exit] exit_code=%d", result.exit_code)
    return result.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
