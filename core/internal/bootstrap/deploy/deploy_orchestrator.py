#!/usr/bin/env python3
# GREP_SUMMARY: deploy-orchestrator, routing, severity, parallel, sequential, orchestrator-cli, import-native, deploy-modules
# STRUCTURE: ▶ orchestrate [preflight → parse → route → postflight → severity] → _deploy_parallel [topo_sort → pre_pull → batch_check_env → deploy-many|groups → system → hc_marker] | _deploy_sequential [for-loop] → _aggregate_severity → _compute_exit_code → ⎋ {0,1,2}
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
##   - exit code contract: CRIT>0 → 2, WARN>0 → 0 (logged), no failures → 0 (legacy shell parity)
##   - Deploy failures are non-fatal — orchestrator continues and aggregates severity
##   - Depends on PYTHONPATH=<project root> from the shell facade; also self-bootstraps sys.path
##     (4 levels up) for direct-script invocation (TRAP[BUG] pattern from sudoers_generator.py)
## @rationale D1: Python-import faster than subprocess (no fork+exec per call), testable via
##            unittest.mock.patch, gives typed interface (ruff validates signatures), all modules
##            already live in deploy/ package. D2: shell facade uses `exec python3` — same PID,
##            automatic exit-code propagation. D3: JSON interop via native json.loads/json.dumps (Python).
## @changes   2026-07-31 · Created (DevPlan 100 TASK-1)
## @modulemap
##   ModuleDeployResult [W:1] — dataclass: deployed, failed, crit_count, warn_count, exit_code
##   ModuleLists [W:1] — dataclass: all_names, enabled_names, overlays
##   orchestrate [W:5] — main entry point: preflight → parse → route → postflight → severity → exit_code
##   main [W:2] — CLI entry: argparse → orchestrate() → exit code
##   _preflight [W:3] — context_overlay.ensure_context_repo + spool_validator.verify_spool_dirs + status-metrics + charset validation
##   _parse_modules [W:2] — secrets_validator.parse_modules_from_node_yaml + enabled/filter + overlay resolution
##   _route_deploy [W:3] — PARALLEL → _deploy_parallel, else → _deploy_sequential
##   _deploy_parallel [W:5] — topo_sort → pre_pull → batch_check_env → deploy-many | groups → system → hc_marker
##   _deploy_orchestrator [W:2] — subprocess orchestrator_cli deploy-many --scp (docker modules only, R4)
##   _deploy_sequential [W:4] — for-loop: check_env → detect_type → deploy_docker_module | invoke_module_interface
##   _deploy_system_modules [W:2] — sequential system deploy via invoke_module_interface
##   _postflight [W:3] — sudoers batch + orphans detect + litellm config render
##   _aggregate_severity [W:2] — enriched modules dict lookup, fallback per-module metadata call
##   _compute_exit_code [W:1] — CRIT>0 → 2, WARN>0 → 0, else → 0
##   _set_hc_marker [W:1] — touch /var/lib/platform/.bootstrap/.hc_done_in_deploy
##   _create_status_metrics_json [W:1] — pre-create /run/platform/status-metrics.json (P1 fix)
##   _invoke_module_interface [W:2] — bash subprocess wrapper for system module dispatch (D4)
## @usecases
##   - deploy-modules.sh facade → exec python3 deploy_orchestrator.py --node-yaml ... (prod bootstrap)
##   - import deploy_orchestrator; orchestrate(node_yaml, modules_dir, core_dir, templates_dir) (tests, tools)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── sys.path bootstrap for direct-script invocation (DevPlan 100, mirror of sudoers_generator.py) ──
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · direct-script invocation needs project root in sys.path
# · Symptom: python3 deploy/deploy_orchestrator.py raises ModuleNotFoundError: No module named 'core'
# · Root: script dir (deploy/) is sys.path[0]; deploy → bootstrap → internal → core → root = 4 levels up
# · Fix: insert root into sys.path before core.* imports (pattern from sudoers_generator.py:42-56)
# · Prevention: shell facade also exports PYTHONPATH (defense in depth); tests import via pytest rootdir
_PLATFORM_ROOT = os.environ.get(
    "PLATFORM_ROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."),
)
if not os.path.isdir(os.path.join(_PLATFORM_ROOT, "core", "internal")):
    _PLATFORM_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
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
from core.internal.llm import config_renderer

# DevPlan 116 B4 T1 (U-39): deploy-политика legacy parity — контракт, а не комментарии.
# DEPLOY_BEST_EFFORT=True: failing step → WARN, деплой продолжается; WARN→exit 0; HC_DONE_MARKER всегда.
from core.internal.shared.node_yaml import NodeYaml

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11, гейт timeout_literals)
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT, DEPLOY_TIMEOUT

logger = logging.getLogger(__name__)

# ── Constants (paths mirror deploy-modules.sh facade / docker_orchestrator.py) ──
_HC_DONE_MARKER = "/var/lib/platform/.bootstrap/.hc_done_in_deploy"
_STATUS_METRICS_PATH = "/run/platform/status-metrics.json"
_STATUS_METRICS_TEMPLATE = {
    "schema_version": 2,
    "generated_at": None,
    "containers": [],
    "certs": [],
    "projects": [],
    "host": {},
}
_INVOKE_MODULE_INTERFACE_SH = str(Path(__file__).resolve().parent.parent.parent / "lib" / "module-interface.sh")
_PATHS_SH = str(Path(__file__).resolve().parent.parent.parent / "lib" / "paths.sh")


# region FUNC_ModuleDeployResult
## @purpose  Structured result of one orchestrate() run — consumed by callers for exit code + telemetry
## @io       ⇥ (constructed by orchestrate) → ⎋ dataclass
## @complexity 1 — plain data container
@dataclass
class ModuleDeployResult:
    """Result of a full orchestrate() run.

    ## @invariants
    ##   - exit_code: 0=success (warnings allowed), 2=critical failures. 1 is RESERVED —
    ##     legacy shell mapped WARN to exit 0 (DevPlan 100 §3 Phase 5), never emitted 1.
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
def orchestrate(
    node_yaml: str,
    modules_dir: str,
    core_dir: str,
    templates_dir: str,
    *,
    modules_filter: str = "",
    deploy_parallel: bool = False,
    deploy_orchestrator: bool = False,
) -> ModuleDeployResult:
    """Main orchestration entry point — importable and CLI-callable."""
    logger.info("[IMP:7][orchestrate][start] node_yaml=%s modules_dir=%s", node_yaml, modules_dir)

    # PHASE 1: PREFLIGHT (all steps non-fatal — legacy shell used `|| true` semantics)
    _preflight(core_dir, node_yaml, modules_dir)

    # PHASE 2: PARSE MODULES
    modules = _parse_modules(node_yaml, modules_dir, modules_filter)
    if not modules.enabled_names:
        logger.info("[IMP:9][orchestrate][skip] No enabled modules declared in %s — SKIP deploy", node_yaml)
        return ModuleDeployResult(deployed=0, failed=[], crit_count=0, warn_count=0, exit_code=0)
    logger.info("[IMP:8][orchestrate][parse] enabled=%d all=%d", len(modules.enabled_names), len(modules.all_names))

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
    exit_code = _compute_exit_code(crit, warn, deployed)
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
##   - Every step is best-effort: a failing step logs WARN and does NOT abort deploy (legacy parity)
##   - Order preserved from legacy deploy-modules.sh: overlay → spool → status-metrics → charsets
##   - secrets-manifest path derived as core_dir/secrets-manifest.yaml
def _preflight(core_dir: str, node_yaml: str, modules_dir: str) -> None:
    """Run non-fatal preflight steps (legacy `|| true` semantics preserved)."""
    # ── context overlay ensure (clone/pull with S9 cache) ──
    try:
        rc = context_overlay.ensure_context_repo(node_yaml)
        logger.info("[IMP:8][_preflight][context_overlay] ensure_context_repo rc=%d", rc)
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
    except Exception as exc:  # noqa: EXC — non-fatal preflight step (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_preflight][charset] error (non-fatal): %s", exc)


# endregion FUNC__preflight


# region FUNC__parse_modules
## @purpose  PHASE 2: parse node.yaml modules section, filter enabled + modules_filter, resolve overlays
## @io       ⇥ node_yaml: str, modules_dir: str, modules_filter: str
##           ⎋ ModuleLists (all_names, enabled_names, overlays)
## @complexity 2 — parse + linear filter + per-module overlay filesystem check
## @invariants
##   - enabled == "true" only (string form returned by parse_modules_from_node_yaml)
##   - modules_filter (comma/space-separated) intersects enabled set — applied BEFORE topo-sort
##   - Overlays resolved via node.yaml context + /opt/\<ctx\>/platform/modules/\<name\> filesystem check
##     (legacy shell pattern — config_overlay field from node.yaml is NOT used for deploy)
def _parse_modules(node_yaml: str, modules_dir: str, modules_filter: str) -> ModuleLists:
    """Parse node.yaml modules and apply enabled/filter/overlay resolution."""
    raw = secrets_validator.parse_modules_from_node_yaml(node_yaml)

    all_names: list[str] = []
    enabled_names: list[str] = []
    for name, enabled, _overlay in raw:
        if name not in all_names:
            all_names.append(name)
        if enabled == "true" and name not in enabled_names:
            enabled_names.append(name)

    # ── modules_filter: comma/space-separated intersect with enabled set ──
    # 🧐 TRAP[DECISION] · 2026-07-31 · — · --modules filter now APPLIED in orchestrator
    # · Rejected: keep legacy inert behavior (260-LOC shell parsed --modules but never consumed it)
    # · Reason: DevPlan 100 §3 Phase 2 contract "Filter: enabled_only, modules_filter" + AGENTS.md
    #   documents "--modules postgres,redis → развернуть только указанные". Applying the filter
    #   restores documented semantics; intersection keeps behavior identical when flag is absent.
    # · Rev: if phases.py starts pre-filtering module lists, intersection remains idempotent.
    filter_set = {m.strip() for m in modules_filter.replace(",", " ").split() if m.strip()}
    if filter_set:
        enabled_names = [n for n in enabled_names if n in filter_set]

    overlays = _resolve_overlay_dirs(node_yaml, enabled_names)
    logger.info("[IMP:9][_parse_modules][result] enabled=%d all=%d", len(enabled_names), len(all_names))
    return ModuleLists(all_names=all_names, enabled_names=enabled_names, overlays=overlays)


# endregion FUNC__parse_modules


# region FUNC__resolve_overlay_dirs
## @purpose  Resolve per-module context overlay dirs: /opt/\<ctx\>/platform/modules/\<name\> if exists.
## @io       ⇥ node_yaml: str, enabled_names: list[str] → ⎋ dict[str, str] (name → overlay path or "")
## @complexity 2 — NodeYaml context read + N filesystem checks
## @invariants
##   - No context in node.yaml → all overlays empty (legacy grep "^context:" semantics)
##   - Overlay only set when the directory actually exists on disk
##   - NodeYaml.get_context() returns "" (no raise) when context absent
def _resolve_overlay_dirs(node_yaml: str, enabled_names: list[str]) -> dict[str, str]:
    """Resolve context overlay dirs from node.yaml context + filesystem existence."""
    ctx = ""
    try:
        ctx = NodeYaml(node_yaml).get_context()
    except Exception as exc:  # noqa: EXC — overlay resolution is best-effort
        logger.warning("[IMP:7][_resolve_overlay_dirs][warn] Cannot read context from %s: %s", node_yaml, exc)

    overlays: dict[str, str] = {}
    for name in enabled_names:
        overlay = ""
        if ctx:
            candidate = f"/opt/{ctx}/platform/modules/{name}"
            if os.path.isdir(candidate):
                overlay = candidate
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
            deploy_orchestrator,
        )
    logger.info("[IMP:9][_route_deploy][route] SEQUENTIAL route (DEPLOY_PARALLEL != true) — legacy for-loop")
    deployed, failed = _deploy_sequential(enabled_names, modules_dir, core_dir)
    return deployed, failed, {}


# endregion FUNC__route_deploy


# region FUNC__deploy_parallel
## @purpose  DEPLOY_PARALLEL=true path: topo_sort → pre-pull → batch-check-env → (deploy-many | groups)
##           → system modules → HC_DONE_MARKER. Returns enriched modules dict for severity lookup.
## @io       ⇥ enabled_names, overlays, modules_dir, core_dir, deploy_orchestrator
##           ⎋ tuple[int, list[str], dict[str, dict[str, str]]] — (deployed, failed, modules_info)
## @complexity 4 — topo DAG + parallel group deploy + system dispatch
## @invariants
##   - topo_sort failure (e.g. dependency cycle) → WARN + sequential fallback, empty modules_info
##   - pre-pull + batch-check-env are best-effort (legacy `|| true` parity)
##   - deploy_orchestrator=True → orchestrator_cli deploy-many for DOCKER modules only (R4), which
##     REPLACES group-based deploy (DevPlan §3 either/or — see TRAP[DECISION])
##   - Group deploy failures ARE aggregated into failed (see TRAP[DECISION])
##   - HC_DONE_MARKER always set at the end of the parallel path (legacy parity)
def _deploy_parallel(
    enabled_names: list[str],
    overlays: dict[str, str],
    modules_dir: str,
    core_dir: str,
    deploy_orchestrator: bool,
) -> tuple[int, list[str], dict[str, dict[str, str]]]:
    """Parallel deploy: topo-sort groups + pre-pull + batch env check + group/orchestrator deploy."""
    secrets_manifest = os.path.join(core_dir, "secrets-manifest.yaml")
    logger.info("[IMP:9][_deploy_parallel][start] DEPLOY_PARALLEL=true — topo_sort + pre-pull + batch deploy")

    # ── 1. topo_sort → deploy groups + enriched modules dict (S10) ──
    modules_info: dict[str, dict[str, str]] = {}
    groups: list[list[str]] = []
    try:
        all_modules = topo_sort.load_module_yamls(modules_dir)
        docker_modules = topo_sort.filter_docker_modules(all_modules)
        dag = topo_sort.build_dag(docker_modules, filter_names=enabled_names)
        if dag:
            groups = topo_sort.kahn_topological_sort(dag)
        for m in all_modules:
            name = m.get("name", "")
            if name:
                modules_info[name] = {
                    "install_type": m.get("install_type", "unknown"),
                    "severity": m.get("severity", "warn"),
                }
        logger.info("[IMP:9][_deploy_parallel][topo_sort] Topo-sorted into %d deploy groups", len(groups))
    except Exception as exc:  # noqa: EXC — topo failure falls back to sequential (legacy parity)
        logger.warning(
            "[IMP:5][_deploy_parallel][topo_sort] topo_sort failed (%s) — falling back to sequential deploy",
            exc,
        )
        deployed, failed = _deploy_sequential(enabled_names, modules_dir, core_dir)
        return deployed, failed, {}

    # ── 2. pre-pull docker images (best-effort — compose up retries pull) ──
    try:
        ok, fail = docker_orchestrator.pre_pull_images(_build_entries(enabled_names, overlays), modules_dir)
        logger.info("[IMP:9][_deploy_parallel][pre_pull] Pre-pull complete: success=%d failed=%d", ok, fail)
    except Exception as exc:  # noqa: EXC — pre-pull non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_deploy_parallel][pre_pull] Pre-pull error (non-fatal): %s", exc)

    # ── 3. batch-check-env (one call replaces per-module check-env) ──
    try:
        env_results = secrets_validator.batch_check_env(modules_dir, secrets_manifest)
        logger.info(
            "[IMP:9][_deploy_parallel][batch_check_env] batch-check-env completed for %d modules", len(env_results)
        )
    except Exception as exc:  # noqa: EXC — batch env check non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_deploy_parallel][batch_check_env] error (non-fatal): %s", exc)

    deployed = 0
    failed: list[str] = []

    # ── 4. deploy docker modules: orchestrator_cli deploy-many XOR group-based deploy ──
    docker_names = [n for n in enabled_names if modules_info.get(n, {}).get("install_type") == "docker"]
    if deploy_orchestrator and docker_names:
        # 🧐 TRAP[DECISION] · 2026-07-31 · — · DeployOrchestrator path REPLACES group deploy (either/or)
        # · Rejected: legacy 260-LOC shell ran deploy-many AND group deploy (comment said "Skip the rest"
        #   but no skip was implemented → double deploy). Also passed ALL module names incl. system.
        # · Reason: DevPlan 100 §3 Phase 3 specifies IF deploy_orchestrator → deploy-many, ELSE → groups.
        #   R4 risk confirms orchestrator is for docker modules only. Either/or fixes the double-deploy.
        # · Rev: if DeployOrchestrator gains system-module support → extend docker_names filter.
        orch_deployed, orch_failed = _deploy_orchestrator(docker_names)
        deployed += orch_deployed
        failed.extend(orch_failed)
    elif groups:
        logger.info("[IMP:7][_deploy_parallel][groups] Deploying %d docker group(s) sequentially", len(groups))
        for g_idx, group in enumerate(groups):
            group_entries = _build_entries(group, overlays)
            logger.info(
                "[IMP:8][_deploy_parallel][group] Deploying group %d/%d: %s",
                g_idx,
                len(groups) - 1,
                group_entries,
            )
            try:
                d, _f, fnames, _rolled = docker_orchestrator.deploy_docker_group(group_entries, modules_dir)
                deployed += d
                # 🧐 TRAP[DECISION] · 2026-07-31 · — · Group failures aggregated into severity
                # · Rejected: legacy shell dropped deploy_docker_group failures (WARN only → exit 0 always)
                # · Reason: DevPlan 100 §2.3 _deploy_parallel returns (deployed, failed); §3 Phase 5
                #   aggregates severity for "each failed module". deploy_docker_group returns failed_names
                #   precisely for this purpose — dropping them made severity aggregation inert in parallel mode.
                # · Rev: if deploy_docker_group semantics change (rollback makes failures non-actionable).
                failed.extend(fnames)
            except Exception as exc:  # noqa: EXC — group failure continues to next group (legacy parity)
                logger.warning("[IMP:5][_deploy_parallel][group] Group %d deploy error (non-fatal): %s", g_idx, exc)
    else:
        logger.info("[IMP:5][_deploy_parallel][groups] No docker groups from topo_sort — skipping group-based deploy")

    # ── 5. system modules (sequential via invoke_module_interface) ──
    system_names = [n for n in enabled_names if modules_info.get(n, {}).get("install_type") == "system"]
    if system_names:
        sys_deployed, sys_failed = _deploy_system_modules(system_names)
        deployed += sys_deployed
        failed.extend(sys_failed)

    # ── 6. HC_DONE_MARKER — signals state_machine.py to skip standalone healthcheck ──
    _set_hc_marker()

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
##   - Failure is WARN-only (legacy parity: orchestrator failures never added to FAILED severity,
##     но наблюдаемы через failed-список — DEPLOY_BEST_EFFORT, B4)
##   - returncode != 0 → WARN + честный (deployed, failed)-из-JSON
def _deploy_orchestrator(docker_names: list[str]) -> tuple[int, list[str]]:
    """Call orchestrator_cli deploy-many via subprocess (separate CLI, separate concern — D1 exception).

    D7: LocalChannel — deploy-many выполняется НА ноде (payload уже на месте), SCP-транспорт
    самому себе бессмыслен; поэтому `--scp` НЕ передаётся (build_channel → LocalChannel).
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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEPLOY_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_deploy_orchestrator][error] deploy-many error (non-fatal): %s", exc)
        return 0, []

    # ── Парсинг JSON-вывода deploy-many (U-30): JSON-массив ModuleDeployResult ──
    deployed = 0
    failed: list[str] = []
    try:
        parsed = json.loads(result.stdout) if result.stdout.strip() else []
        if isinstance(parsed, list):
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                status = entry.get("status", "")
                if status == "DEPLOYED":
                    deployed += 1
                elif status in ("FAILED", "ROLLED_BACK"):
                    failed.append(entry.get("project", "?"))
    except json.JSONDecodeError as exc:
        logger.warning(
            "[IMP:5][_deploy_orchestrator][parse] deploy-many stdout не JSON (%.120r): %s",
            result.stdout,
            exc,
        )

    if result.returncode != 0:
        logger.warning(
            "[IMP:5][_deploy_orchestrator][fail] deploy-many had failures (exit=%d) — continuing (DEPLOY_BEST_EFFORT)",
            result.returncode,
        )
    logger.info(
        "[IMP:9][_deploy_orchestrator][done] deploy-many: deployed=%d failed=%s",
        deployed,
        failed,
    )
    return deployed, failed


# endregion FUNC__deploy_orchestrator


# region FUNC__deploy_sequential
## @purpose  Legacy sequential path (DEPLOY_PARALLEL != true): for-loop over enabled modules —
##           check-env → detect-type → deploy_docker_module | invoke_module_interface.
## @io       ⇥ enabled_names, modules_dir, core_dir → ⎋ tuple[int, list[str]] — (deployed, failed)
## @complexity 3 — linear for-loop with per-module env/type/deploy dispatch
## @invariants
##   - Missing env vars → module FAILED + skipped (legacy parity)
##   - install_type "system" → invoke_module_interface install + best-effort healthcheck liveness
##   - Everything else (docker/unknown) → deploy_docker_module (module.yaml missing → docker path,
##     legacy parity — compose resolution fails there)
##   - Overlay dir NOT passed in sequential path (legacy parity — overlay was computed but unused)
def _deploy_sequential(
    enabled_names: list[str],
    modules_dir: str,
    core_dir: str,
) -> tuple[int, list[str]]:
    """Sequential deploy for-loop (legacy path — unchanged semantics)."""
    secrets_manifest = os.path.join(core_dir, "secrets-manifest.yaml")
    deployed = 0
    failed: list[str] = []
    logger.info(
        "[IMP:9][_deploy_sequential][start] DEPLOY_PARALLEL=false — sequential for-loop over %d modules",
        len(enabled_names),
    )

    for m_name in enabled_names:
        # ── env check (missing vars → fail module, skip deploy) ──
        try:
            missing = secrets_validator.check_env_requires(m_name, secrets_manifest)
        except Exception as exc:  # noqa: EXC — env check failure treated as non-blocking (best-effort: DEPLOY_BEST_EFFORT policy)
            logger.warning("[IMP:8][_deploy_sequential][env_check] env check error for %s: %s", m_name, exc)
            missing = []
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
            _invoke_module_interface(m_name, "healthcheck", "liveness")  # best-effort, non-fatal
        else:
            try:
                ok = docker_orchestrator.deploy_docker_module(m_name, modules_dir=modules_dir)
            except Exception as exc:  # noqa: EXC — docker deploy failure → module failed, continue (best-effort: DEPLOY_BEST_EFFORT policy)
                logger.warning("[IMP:8][_deploy_sequential][docker] deploy error for %s: %s", m_name, exc)
                ok = False
            if ok:
                deployed += 1
            else:
                failed.append(m_name)

    logger.info("[IMP:9][_deploy_sequential][done] deployed=%d failed=%s", deployed, failed)
    return deployed, failed


# endregion FUNC__deploy_sequential


# region FUNC__deploy_system_modules
## @purpose  Deploy system modules sequentially via invoke_module_interface install + liveness healthcheck.
## @io       ⇥ system_names: list[str] → ⎋ tuple[int, list[str]] — (deployed, failed)
## @complexity 2 — linear loop with per-module shell dispatch
## @invariants
##   - healthcheck liveness is best-effort (legacy `2>/dev/null || true` parity)
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
        _invoke_module_interface(m_name, "healthcheck", "liveness")  # best-effort
    logger.info("[IMP:9][_deploy_system_modules][done] deployed=%d failed=%s", deployed, failed)
    return deployed, failed


# endregion FUNC__deploy_system_modules


# region FUNC__invoke_module_interface
## @purpose  Invoke a module interface (install/healthcheck/...) via the shell function
##           invoke_module_interface from core/lib/module-interface.sh (D4 — intentional subprocess).
## @io       ⇥ module_name: str, interface: str, *args: str → ⎋ bool (True on exit 0)
## @complexity 1 — single bash subprocess with graceful error handling
## @invariants
##   - Sources paths.sh + module-interface.sh inside the bash -c (same pattern as docker_orchestrator._invoke_healthcheck_full)
##   - Failure/timeout → False (never raises)
##   - Timeout 180s (install can take minutes for system services)
def _invoke_module_interface(module_name: str, interface: str, *args: str) -> bool:
    """Call invoke_module_interface (bash) — intentional subprocess for shell operations (D4)."""
    bash_cmd = (
        f"source '{_PATHS_SH}' && "
        f"source '{_INVOKE_MODULE_INTERFACE_SH}' && "
        f"invoke_module_interface '{module_name}' '{interface}'"
    )
    if args:
        bash_cmd += " " + " ".join(shlex.quote(a) for a in args)
    logger.info("[IMP:8][_invoke_module_interface][invoke] %s %s", module_name, interface)
    try:
        result = subprocess.run(["bash", "-c", bash_cmd], capture_output=True, text=True, timeout=COMPOSE_UP_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:7][_invoke_module_interface][error] %s %s error: %s", module_name, interface, exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:8][_invoke_module_interface][fail] %s %s exit=%d", module_name, interface, result.returncode
        )
        return False
    logger.info("[IMP:9][_invoke_module_interface][done] %s %s OK", module_name, interface)
    return True


# endregion FUNC__invoke_module_interface


# region FUNC__postflight
## @purpose  PHASE 4: post-deploy housekeeping — sudoers batch generation, orphan container detection,
##           litellm-config.yml render. Independent of deploy outcome.
## @io       ⇥ all_names, enabled_names, modules_dir, core_dir, templates_dir → ⎋ None
## @complexity 2 — 3 guarded calls (each non-fatal, legacy `|| true` parity)
## @invariants
##   - sudoers batch uses ALL module names (legacy --module-names "$ALL_NAMES" parity)
##   - orphan detection uses ENABLED module names (legacy --module-entries "$ENABLED_NAMES" parity)
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
    except Exception as exc:  # noqa: EXC — sudoers non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_postflight][sudoers] error (non-fatal): %s", exc)

    # ── orphan container detection (batch, detect-only — self-heal not enabled) ──
    try:
        orphans = orphan_reconciler.batch_orphan_reconciliation(enabled_names, modules_dir)
        logger.info("[IMP:8][_postflight][orphans] %d orphan(s) detected", len(orphans))
    except Exception as exc:  # noqa: EXC — orphan detection non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:5][_postflight][orphans] error (non-fatal): %s", exc)

    # ── litellm-config.yml render (best-effort — existing config kept on failure) ──
    _render_litellm_config(core_dir)


# endregion FUNC__postflight


# region FUNC__render_litellm_config
## @purpose  Render litellm-config.yml from policy.yaml via config_renderer (native import).
## @io       ⇥ core_dir: str → ⎋ None (non-fatal)
## @complexity 1 — single render_to_file call in try/except
## @invariants
##   - policy: core_dir/internal/llm/policy.yaml; output: core_dir/modules/litellm/config/litellm-config.yml
##   - Render failure logs WARN and keeps existing config (legacy `|| { echo WARNING }` parity)
def _render_litellm_config(core_dir: str) -> None:
    """Render litellm-config.yml from policy.yaml (non-fatal)."""
    policy_path = Path(core_dir) / "internal" / "llm" / "policy.yaml"
    output_path = Path(core_dir) / "modules" / "litellm" / "config" / "litellm-config.yml"
    logger.info("[IMP:7][_render_litellm_config][start] Rendering litellm-config.yml from %s", policy_path)
    try:
        config_renderer.render_to_file(policy_path, output_path)
        logger.info("[IMP:9][_render_litellm_config][done] litellm-config.yml rendered")
    except Exception as exc:  # noqa: EXC — render non-fatal (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:8][_render_litellm_config][warn] render failed (non-fatal): %s", exc)


# endregion FUNC__render_litellm_config


# region FUNC__aggregate_severity
## @purpose  PHASE 5: map failed modules to severity (critical|warn). Uses enriched modules dict from
##           topo_sort when available; falls back to per-module module.yaml metadata call.
## @io       ⇥ failed: list[str], modules_info: dict[str, dict[str, str]], modules_dir: str
##           ⎋ tuple[int, int] — (crit_count, warn_count)
## @complexity 2 — linear lookup with per-module fallback
## @invariants
##   - severity defaults to "warn" for unknown modules (default warn severity)
##   - fallback reads module.yaml severity field (secrets_validator.get_module_severity)
def _aggregate_severity(
    failed: list[str],
    modules_info: dict[str, dict[str, str]],
    modules_dir: str,
) -> tuple[int, int]:
    """Aggregate failed module severities into (crit_count, warn_count)."""
    crit = 0
    warn = 0
    for name in failed:
        severity = "warn"
        if name in modules_info:
            severity = modules_info[name].get("severity", "warn")
        else:
            try:
                severity = secrets_validator.get_module_severity(os.path.join(modules_dir, name, "module.yaml"))
            except Exception as exc:  # noqa: EXC — severity fallback failure → default warn (best-effort: DEPLOY_BEST_EFFORT policy)
                logger.warning("[IMP:7][_aggregate_severity][fallback] severity lookup failed for %s: %s", name, exc)
        if severity == "critical":
            crit += 1
        else:
            warn += 1
    logger.info("[IMP:9][_aggregate_severity][result] crit=%d warn=%d", crit, warn)
    return crit, warn


# endregion FUNC__aggregate_severity


# region FUNC__compute_exit_code
## @purpose  Compute final exit code: CRIT>0 → 2, WARN>0 → 0 (logged), no failures → 0.
## @io       ⇥ crit: int, warn: int, deployed: int → ⎋ int
## @complexity 1 — two comparisons
## @invariants
##   - WARN maps to exit 0 (DEPLOY_BEST_EFFORT policy — warnings are non-critical by definition)
##   - Only CRIT failures escalate to exit 2
def _compute_exit_code(crit: int, warn: int, deployed: int) -> int:
    """Severity-based exit code (DEPLOY_BEST_EFFORT contract: CRIT→2, WARN→0, DONE→0)."""
    if crit > 0:
        logger.error("[IMP:10][_compute_exit_code][critical] Critical:%d Warn:%d → exit 2", crit, warn)
        return 2
    if warn > 0:
        logger.warning("[IMP:8][_compute_exit_code][warn] Warn:%d (non-critical — continuing) → exit 0", warn)
        return 0
    logger.info("[IMP:9][_compute_exit_code][done] Deploy complete: %d modules (warnings: 0) → exit 0", deployed)
    return 0


# endregion FUNC__compute_exit_code


# region FUNC__set_hc_marker
## @purpose  Touch /var/lib/platform/.bootstrap/.hc_done_in_deploy — signals state_machine.py to skip
##           the standalone healthcheck (healthcheck already ran inside deploy_docker_group).
##           HC_DONE_MARKER always set (DEPLOY_BEST_EFFORT policy — healthcheck был выполнен
##           внутри деплоя даже при частичных сбоях).
## @io       ⇥ None → ⎋ None (side-effect: marker file)
## @complexity 1 — mkdir + touch with graceful failure
def _set_hc_marker() -> None:
    """Create the healthcheck-done marker file (non-fatal on failure).

    Поведение определяется политикой DEPLOY_BEST_EFFORT (shared/contracts.py, U-39):
    маркер ставится всегда — healthcheck уже выполнен внутри deploy_docker_group.
    """
    try:
        os.makedirs(os.path.dirname(_HC_DONE_MARKER), exist_ok=True)
        Path(_HC_DONE_MARKER).touch(exist_ok=True)
        logger.info(
            "[IMP:9][_set_hc_marker][done] Created %s — standalone healthcheck will be skipped", _HC_DONE_MARKER
        )
    except OSError as exc:
        logger.warning("[IMP:7][_set_hc_marker][warn] Cannot create marker %s: %s", _HC_DONE_MARKER, exc)


# endregion FUNC__set_hc_marker


# region FUNC__create_status_metrics_json
## @purpose  Pre-create /run/platform/status-metrics.json as valid empty JSON (P1 fix) — prevents
##           Docker from creating it as a directory during bind mount.
## @io       ⇥ None → ⎋ None (side-effect: JSON file)
## @complexity 1 — existence check + mkdir + write
def _create_status_metrics_json() -> None:
    """Pre-create status-metrics.json if absent (non-fatal on failure)."""
    if os.path.exists(_STATUS_METRICS_PATH):
        return
    try:
        os.makedirs(os.path.dirname(_STATUS_METRICS_PATH), exist_ok=True)
        with open(_STATUS_METRICS_PATH, "w") as fh:
            json.dump(_STATUS_METRICS_TEMPLATE, fh)
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
## @purpose  CLI entry point: argparse → orchestrate() → exit code (called via `exec python3` from the facade).
## @io       sys.argv → int exit code {0,1,2}
## @complexity 1 — argparse + delegation
## @invariants
##   - --deploy-parallel / --deploy-orchestrator accept "true"/"false" strings (shell flag parity)
##   - Logging to stderr (keeps stdout clean for any tooling)
def main(argv: list[str] | None = None) -> int:
    """CLI entry point — orchestrate() wrapper (facade executes this via `exec python3`)."""
    parser = argparse.ArgumentParser(
        description="Deploy modules orchestrator — routing + severity aggregation (DevPlan 100)"
    )
    parser.add_argument("--node-yaml", required=True, help="Path to node.yaml (NODE_YAML env from facade)")
    parser.add_argument("--modules-dir", required=True, help="Path to core/modules/ (PATHS_MODULES_DIR)")
    parser.add_argument("--core-dir", required=True, help="Path to core/ (PATHS_CORE_DIR)")
    parser.add_argument("--templates-dir", required=True, help="Path to core/templates/ (PATHS_TEMPLATES_DIR)")
    parser.add_argument("--modules-filter", default="", help="Comma/space-separated module filter (--modules)")
    parser.add_argument(
        "--deploy-parallel",
        default="false",
        choices=["true", "false"],
        help="DEPLOY_PARALLEL flag (default: false → sequential)",
    )
    parser.add_argument(
        "--deploy-orchestrator",
        default="false",
        choices=["true", "false"],
        help="DEPLOY_ORCHESTRATOR flag (default: false)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    logger.info("[IMP:7][main][start] node_yaml=%s", args.node_yaml)

    result = orchestrate(
        node_yaml=args.node_yaml,
        modules_dir=args.modules_dir,
        core_dir=args.core_dir,
        templates_dir=args.templates_dir,
        modules_filter=args.modules_filter,
        deploy_parallel=args.deploy_parallel == "true",
        deploy_orchestrator=args.deploy_orchestrator == "true",
    )
    logger.info("[IMP:9][main][exit] exit_code=%d", result.exit_code)
    return result.exit_code


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
