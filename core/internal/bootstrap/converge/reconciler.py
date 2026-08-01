#!/usr/bin/env python3
# GREP_SUMMARY: reconciler, converge, r1, r2, r3, r4, r5, r6, r7, r8, r9, reconcile-perms, reconcile-audit-log, reconcile-projects, reconcile-networks, detect-hosts-drift, verify-vhosts, reconcile-volumes, reconcile-sudoers, reconcile-runtime, orchestrator, exit-code, json-report
# STRUCTURE: ▶ argparse ┌--node-yaml --node-name --core-dir --templates-dir --modules-dir --dry-run --report-only --units┐ → ○ infra.unit_enabled filter → ▶ R1 perms → ▶ R2 audit → ▶ R3 projects → ▶ R4 networks → ▶ R5 hosts-drift → ▶ R6 vhosts → ▶ R7 volumes → ▶ R8 sudoers → ▶ R9 runtime → ⊕ aggregate exit_code {0,1,2} → ⎋ JSON report stdout
# region MODULE_CONTRACT
## @purpose  Оркестратор desired-state reconciler (R1-R9) — депеширует доменным модулям
##           converge/ пакета (perms/audit/projects/networks/vhosts/volumes/sudoers/runtime).
##           Инфраструктура (report/exit/subprocess/глобалы) — в converge/infra.py (B9 T2, U-31).
## @scope    R1 reconcile_perms — executable-bit fix (converge/perms.py)
##           R2 reconcile_audit_log — audit.jsonl 0664 root:adm + ci-deploy adm group (converge/audit.py)
##           R3 reconcile_projects — per-project directory + stub + .env.platform (converge/projects.py)
##           R4 reconcile_networks — proxy-net + container connectivity (converge/networks.py)
##           R5 detect_hosts_drift — /etc/hosts stale entries (converge/vhosts.py)
##           R6 verify_vhosts — nginx vhost integrity + orphans + nginx -t (converge/vhosts.py)
##           R7 reconcile_volumes — detect-only named volumes O7 (converge/volumes.py)
##           R8 reconcile_sudoers — sudoers.d drift + self-heal (converge/sudoers.py)
##           R9 reconcile_runtime_state — container state + compose up -d + cooldown (converge/runtime.py)
## @location core/internal/bootstrap/converge/reconciler.py
## @invariants
##   - R-units независимы — один unit failure НЕ прерывает остальные
##   - Exit code: 0=converged, 1=warnings, 2=errors
##   - --report-only: no mutations, exit 0, JSON drift report on stdout
##   - --dry-run: prints plan without mutations, exit 0
##   - --units R1,R3,...: comma-separated unit filter; empty = all units (default)
##   - node.yaml must be present or FATAL exit 2
##   - Модульные глобалы устанавливаются в infra (единый source of truth)
## @rationale DevPlan 116 B9 D3: 2286-LOC монолит reconciler.py → оркестратор (~250 LOC)
##            + 8 доменных модулей + infra.py. Домены получают чистые параметры.
## @changes
##   2026-07-22 · Created (W4-E3 extraction from converge.sh)
##   2026-07-22 · Added R7 reconcile_volumes (detect-only named volumes)
##   2026-07-22 · Added R8 reconcile_sudoers (sudoers.d drift + self-heal via visudo + atomic write)
##   2026-07-22 · Added R9 reconcile_runtime_state (container state + compose up -d + cooldown)
##   2026-07-30 · T9b — replaced subprocess call to gen-env-platform.sh with direct import
##   2026-08-01 · B9 T2 — SRP-декомпозиция: домены вынесены в converge/{perms,audit,projects,
##              networks,vhosts,volumes,sudoers,runtime}.py, инфраструктура — в converge/infra.py
# endregion MODULE_CONTRACT

import argparse
import logging
import sys
from pathlib import Path

import core.internal.bootstrap.converge.infra as infra
from core.internal.bootstrap.converge.audit import reconcile_audit_log
from core.internal.bootstrap.converge.networks import reconcile_networks
from core.internal.bootstrap.converge.perms import reconcile_perms
from core.internal.bootstrap.converge.projects import reconcile_projects
from core.internal.bootstrap.converge.runtime import reconcile_runtime_state
from core.internal.bootstrap.converge.sudoers import reconcile_sudoers
from core.internal.bootstrap.converge.vhosts import detect_hosts_drift, verify_vhosts
from core.internal.bootstrap.converge.volumes import reconcile_volumes

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entry point: parse args, dispatch R1-R9, aggregate exit code,
##           emit JSON report if --report-only.
## @io       ⇥ sys.argv → ⎋ exit 0|1|2; stdout: JSON report (--report-only)
## @complexity 2 — argument dispatch + unit filter loop
## @invariants
##   - Exit codes: 0=converged, 1=warnings, 2=errors
##   - --report-only: JSON report to stdout, exit 0
##   - --dry-run: LDD logs to stderr, exit 0
##   - Unit failure does NOT abort other units
def main() -> int:
    """CLI entry point for reconciler.py.

    Usage:
        python3 reconciler.py --node-yaml <path> [--node-name <name>] [--core-dir <path>]
                              [--templates-dir <path>] [--modules-dir <path>]
                              [--dry-run] [--report-only] [--units <R1,R2,...>]

    Exit codes:
        0 — fully converged (no drifts, no warnings)
        1 — warnings (non-critical drift detected)
        2 — one or more R-units failed (critical errors)
    """
    parser = argparse.ArgumentParser(
        description="Platform desired-state reconciler — converge 9 dimensions (R1-R9) from node.yaml.",
    )
    parser.add_argument(
        "--node-yaml",
        required=True,
        type=str,
        help="Path to node.yaml (required)",
    )
    parser.add_argument(
        "--node-name",
        default="",
        type=str,
        help="Node name for R6 overlay resolution (default: derived from node-yaml context)",
    )
    parser.add_argument(
        "--core-dir",
        default="",
        type=str,
        help="Path to core/ directory for R1/R2 path resolution (default: auto-detect from script location)",
    )
    parser.add_argument(
        "--templates-dir",
        default="",
        type=str,
        help="Path to templates/ directory for R8 sudoers generation (default: auto-detect from core-dir)",
    )
    parser.add_argument(
        "--modules-dir",
        default="",
        type=str,
        help="Path to modules/ directory for R9 runtime state (default: auto-detect from core-dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print planned mutations without executing (exit 0)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        default=False,
        help="Check-only mode: emit JSON drift report to stdout (exit 0)",
    )
    parser.add_argument(
        "--units",
        default="",
        type=str,
        help="Comma-separated R-unit filter (e.g., 'R1,R3'). Empty = all units.",
    )

    args = parser.parse_args()

    # ── Set module-level state (infra — единый source of truth) ──
    infra.reset_state()

    infra.node_yaml_path = args.node_yaml
    infra.node_name = args.node_name
    infra.dry_run = args.dry_run
    infra.report_only = args.report_only

    # Resolve core_dir: argument → auto-detect from __file__
    # Auto-detect: go up from .../bootstrap/converge/ to core/
    infra.core_dir = args.core_dir or str(Path(__file__).resolve().parents[3])

    # Resolve templates_dir and modules_dir from args or core_dir
    infra.templates_dir = args.templates_dir if args.templates_dir else str(Path(infra.core_dir) / "templates")
    infra.modules_dir = args.modules_dir if args.modules_dir else str(Path(infra.core_dir) / "modules")

    units_filter = args.units

    # ── Validate node.yaml exists ──
    if not Path(infra.node_yaml_path).is_file():
        logger.error("[IMP:10][converge][main] FATAL: node.yaml not found at %s", infra.node_yaml_path)
        print(f'{{"error":"node.yaml not found: {infra.node_yaml_path}","exit_code":2}}')
        return 2

    # ── Init report ──
    infra.report_init()

    # ── Print header ──
    mode_str = "DRY-RUN" if infra.dry_run else ("REPORT-ONLY" if infra.report_only else "CONVERGE")
    logger.info("[IMP:9][converge][main] ==============================")
    logger.info("[IMP:9][converge][main] Platform Converge START")
    logger.info("[IMP:9][converge][main] Node: %s", infra.node_name)
    logger.info("[IMP:9][converge][main] Mode: %s", mode_str)
    logger.info("[IMP:9][converge][main] node.yaml: %s", infra.node_yaml_path)
    logger.info("[IMP:9][converge][main] core_dir: %s", infra.core_dir)
    logger.info("[IMP:9][converge][main] units: %s", units_filter if units_filter else "ALL")
    logger.info("[IMP:9][converge][main] ==============================")

    # ── Dispatch R-units with --units filter ──
    if infra.unit_enabled(units_filter, "R1"):
        reconcile_perms(infra.core_dir, dry_run=infra.dry_run, report_only=infra.report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R1 filtered out by --units=%s", units_filter)

    if infra.unit_enabled(units_filter, "R2"):
        reconcile_audit_log(infra.core_dir, dry_run=infra.dry_run, report_only=infra.report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R2 filtered out by --units=%s", units_filter)

    if infra.unit_enabled(units_filter, "R3"):
        reconcile_projects(infra.node_yaml_path, dry_run=infra.dry_run, report_only=infra.report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R3 filtered out by --units=%s", units_filter)

    if infra.unit_enabled(units_filter, "R4"):
        reconcile_networks(infra.node_yaml_path, dry_run=infra.dry_run, report_only=infra.report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R4 filtered out by --units=%s", units_filter)

    if infra.unit_enabled(units_filter, "R5"):
        detect_hosts_drift(infra.node_yaml_path)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R5 filtered out by --units=%s", units_filter)

    if infra.unit_enabled(units_filter, "R6"):
        verify_vhosts(
            infra.node_yaml_path, infra.node_name, infra.core_dir, dry_run=infra.dry_run, report_only=infra.report_only
        )
    else:
        logger.info("[IMP:7][converge][main] SKIP: R6 filtered out by --units=%s", units_filter)

    # ── R7: reconcile_volumes (detect-only, O7) ──
    if infra.unit_enabled(units_filter, "R7"):
        reconcile_volumes(infra.node_yaml_path, dry_run=infra.dry_run, report_only=infra.report_only)
    else:
        logger.info("[IMP:7][converge][main] SKIP: R7 filtered out by --units=%s", units_filter)

    # ── R8: reconcile_sudoers (drift detection + self-heal) ──
    if infra.unit_enabled(units_filter, "R8"):
        reconcile_sudoers(
            infra.node_yaml_path, infra.templates_dir, dry_run=infra.dry_run, report_only=infra.report_only
        )
    else:
        logger.info("[IMP:7][converge][main] SKIP: R8 filtered out by --units=%s", units_filter)

    # ── R9: reconcile_runtime_state (container state + self-heal) ──
    if infra.unit_enabled(units_filter, "R9"):
        reconcile_runtime_state(
            infra.node_yaml_path, infra.modules_dir, dry_run=infra.dry_run, report_only=infra.report_only
        )
    else:
        logger.info("[IMP:7][converge][main] SKIP: R9 filtered out by --units=%s", units_filter)

    # ── Final summary ──
    logger.info("[IMP:9][converge][main] ==============================")
    if infra.has_errors:
        logger.info("[IMP:9][converge][main] ERRORS DETECTED — some R-units failed (exit 2)")
    elif infra.has_warnings:
        logger.info("[IMP:9][converge][main] WARNINGS DETECTED — non-critical drift (exit 1)")
    else:
        logger.info("[IMP:9][converge][main] FULLY CONVERGED — all R-units converged (exit 0)")
    logger.info("[IMP:9][converge][main] ==============================")

    # ── Report-only: JSON to stdout ──
    if infra.report_only:
        report_json = infra.report_emit()
        print(report_json)
        return 0

    # ── Final exit code ──
    if infra.has_errors:
        return 2
    if infra.has_warnings:
        return 1
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
