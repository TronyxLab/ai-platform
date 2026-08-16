#!/usr/bin/env python3
# GREP_SUMMARY: converge-perms, reconcile-perms, r1, executable-bit, chmod, ug+x, defense-in-depth
# STRUCTURE: ▶ rglob *.sh (skip lib/) → ◇ not os.access X_OK? → ⊕ non_exec_files → ◇ dry_run/report_only? WOULD-fix │ ⚡ chmod ug+x → ⎋ drift entry {R1}
# region MODULE_CONTRACT
## @purpose  R1 reconcile_perms — executable-bit fix для *.sh вне core/lib/ (M1 defense-in-depth).
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/perms.py: reconcile_perms. Вызывается оркестратором reconciler.py.
## @invariants
##   - Только файлы вне lib/ (symlink не следует — is_file() + parts check)
##   - dry_run/report_only → мутации не выполняются (статус mutated + exit 1)
##   - 0 неисполняемых файлов → skipped (не mutated)
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям — reconciler.py оркестратор.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.internal.bootstrap.converge.infra import report_add, set_exit, try_chmod

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R1 — reconcile_perms
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_perms
## @purpose  Reconcile executable bit on *.sh files outside core/lib/.
##           M1 defense-in-depth: if rsync delivered files with 644,
##           this restores ug+x. Gate test (test_gate_executable_bit.py)
##           covers the git-index layer; this is the runtime layer.
## @io       stdout/stderr: LDD logs [IMP:7-9], count of fixed files
##           side-effect: chmod ug+x on non-executable scripts
## @param core_dir    Path to the core/ directory
## @param dry_run     If True, only report planned mutations
## @param report_only If True, skip mutations entirely
## @return  Drift report entry dict: {"unit":"R1","status":"...","detail":"..."}
## @edge-cases
##   - 0 non-executable files → SKIP
##   - Symlink → not followed (-type f)
##   - File disappears between find and chmod (rsync race) → graceful skip
##   - Large file tree → single exec find piped to while-read chmod
def reconcile_perms(
    core_dir: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile executable bits on *.sh files outside core/lib/.

    Returns a drift entry dict with status: ok|skipped|mutated|fail.
    """
    unit = "R1"
    logger.info("[IMP:8][converge][%s] START: reconcile_perms — fixing executable bits outside core/lib/", unit)

    core_path = Path(core_dir)
    if not core_path.is_dir():
        logger.error("[IMP:10][converge][%s] FATAL: core_dir does not exist: %s", unit, core_dir)
        entry = {"unit": unit, "status": "fail", "detail": f"Core directory not found: {core_dir}"}
        report_add(unit, "fail", f"Core directory not found: {core_dir}")
        set_exit(2)
        return entry

    # Find non-executable *.sh files outside lib/
    non_exec_files: list[Path] = []
    for sh_file in core_path.rglob("*.sh"):
        # Skip files under lib/
        if "lib" in sh_file.parts:
            continue
        if sh_file.is_file() and not os.access(str(sh_file), os.X_OK):
            non_exec_files.append(sh_file)

    fix_count = len(non_exec_files)

    if fix_count == 0:
        logger.info("[IMP:9][converge][%s] SKIP: All scripts already executable", unit)
        entry = {"unit": unit, "status": "skipped", "detail": "All scripts already executable"}
        report_add(unit, "skipped", "All scripts already executable")
        return entry

    logger.info("[IMP:9][converge][%s] Found %d non-executable script(s)", unit, fix_count)
    for f in non_exec_files:
        logger.info("[IMP:7][converge][%s]   %s", unit, f)

    # ── Dry-run / report-only: no mutation ──
    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD fix %d file(s) with chmod ug+x", unit, fix_count)
        entry = {"unit": unit, "status": "mutated", "detail": f"{fix_count} files would get ug+x"}
        report_add(unit, "mutated", f"{fix_count} files would get ug+x")
        set_exit(1)
        return entry

    # ── Actual mutation ──
    fixed = 0
    for f in non_exec_files:
        if try_chmod(str(f), unit):
            fixed += 1
            logger.info("[IMP:7][converge][%s] Fixed: %s", unit, f)

    if fixed == 0:
        logger.info("[IMP:9][converge][%s] SKIP: No files could be fixed", unit)
        entry = {"unit": unit, "status": "skipped", "detail": "No files could be fixed"}
        report_add(unit, "skipped", "No files could be fixed")
    else:
        logger.info("[IMP:9][converge][%s] DONE: Fixed %d file(s) — chmod ug+x applied", unit, fixed)
        entry = {"unit": unit, "status": "mutated", "detail": f"{fixed} files fixed with ug+x"}
        report_add(unit, "mutated", f"{fixed} files fixed with ug+x")
        set_exit(1)

    return entry


# endregion FUNC_reconcile_perms
