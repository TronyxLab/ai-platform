#!/usr/bin/env python3
"""Spool directory verify-only validator — W4-E1 ensure_spool_dirs reimplementation."""
# GREP_SUMMARY: spool_validator, spool_dir, spool_volume, verify-only, runtime-check, deploy-modules, strangler
# STRUCTURE: ┌modules-dir + module.yaml scan → ◇ verify_spool_dirs() → ⊕ JSON report → ⎋ exit {0,1,2}
# region MODULE_CONTRACT [DOMAIN(DEPLOY): bootstrap; CONCEPT(SPOOL): verify-only runtime check; TECH(PYTHON): argparse+yaml+json+os.path]
## @purpose  Port ensure_spool_dirs() from old deploy-modules.sh (1664 LOC) into typed Python.
##           Verify-only runtime check — confirms spool directories exist before deploy,
##           warns if any are missing (user must run `make provision`).
## @scope    Reads module.yaml files from --modules-dir. Checks existence of:
##           platform dirs, per-module spool_dir paths, wal-archive, observability dirs, fallback dirs.
##           NEVER creates directories — only verifies existence.
## @input    CLI: --action verify --modules-dir <path>
## @output   JSON on stdout: {"status": "ok|warn|error", "missing": [...], "stateless": [...], "ok": [...], "checked": N}
## @exit     0 = all ok, 1 = warnings (missing dirs), 2 = error (modules-dir not found)
## @links    REPLACES_FROM(core/internal/bootstrap/deploy-modules.sh:106-210)
## @invariants
##   - Verify-only: NEVER calls os.makedirs/mkdir. Uses os.path.isdir exclusively.
##   - spool_dir: none → stateless module, logged as INFO, not WARN.
##   - minio/langfuse use spool_dir: none (DevPlan 116 B3 T8, U-67, D3) — host-пути
##     /var/lib/platform/{minio,langfuse}-data удалены из provision; данные живут в
##     docker-томах (minio-data, langfuse-redis-data) — verify-логика НЕ меняется,
##     stateless → INFO-пропуск, они НЕ попадают в WARN-список missing.
##   - spool_volume is a Docker volume name (not a filesystem path) → skipped for existence check.
##   - File-not-found → graceful: missing module dirs, no module.yaml → empty result.
##   - Observability dirs checked only if core/modules/observability/ exists.
##   - Fallback dirs checked only if NO spool_dir found in any module.yaml (spool_found == 0).
##   - JSON output always printed to stdout for machine consumption by shell facade.
## @rationale Strangler-Fig decomposition of 1664-line deploy-modules.sh. ensure_spool_dirs() was
##            removed during W4-E1 extraction (85 lines of shell). This Python module restores the
##            verify-only runtime check with typed safety and testability.
##            Separated from secrets_validator.py (different domain: secrets vs filesystem).
##            Separated from provision-environment.sh (separation of concerns: create vs verify).
## @changes  Initial: 2026-07-22 — W4-E1 ensure_spool_dirs reimplementation
## @usecases
##   - deploy-modules.sh CLI caller: `python3 spool_validator.py --action verify --modules-dir /opt/platform/modules`
##   - Test suite: unit tests with tmp_path fixtures, no external dependencies
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants — mirrors shell ensure_spool_dirs() hardcoded paths
# ═══════════════════════════════════════════════════════════════════════════════

# 🧐 TRAP[DECISION] · 2026-07-22 · — · Platform dirs — hardcoded outside module.yaml loop
# · Rejected: dynamic discovery (would miss dirs without module.yaml)
# · Reason: /var/log/platform/backup is a platform-level log dir, not per-module.
#   It has no module.yaml — must be hardcoded.
PLATFORM_DIRS = [
    "/var/log/platform/backup",
]

# 🧐 TRAP[DECISION] · 2026-07-22 · — · Observability dirs — conditional on module existence
# · Rejected: always check (would WARN on nodes without observability module)
# · Reason: These dirs are created by observability module's provisioner.
#   If the module is not deployed, the dirs legitimately don't exist.
OBSERVABILITY_DIRS = [
    "/var/lib/platform/grafana-data",
    "/var/lib/platform/prometheus-data",
    "/var/lib/platform/loki-data",
]

# 🧐 TRAP[DECISION] · 2026-07-22 · — · Fallback dirs — only if spool_found == 0
# · Rejected: always check (would create noise for nodes with proper module.yaml)
# · Reason: These dirs cover pre-module.yaml era nodes where module.yaml hasn't been
#   updated. Only relevant when no module declares spool_dir.
FALLBACK_DIRS = [
    "/var/lib/platform/postgres-data",
    "/var/lib/platform/backup-spool",
    "/var/lib/platform/backup-spool/postgres",
    "/var/lib/platform/backup-spool/app-data",
]

# Wal-archive is always checked (postgres uses it regardless of module.yaml declaration)
WAL_ARCHIVE = "/var/lib/platform/wal-archive"


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_verify_spool_dirs
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Run the full verify-only spool directory check: platform dirs → module.yaml
##           scan → wal-archive → observability dirs → fallback dirs.
## @io       modules_dir (str) → dict with keys: status, missing, stateless, ok, checked
## @complexity 3 — O(P + M*Y + O + F) where P=platform dirs, M=module count,
##             Y=yaml parse per module, O=obs dirs, F=fallback dirs
## @invariants
##   - Returns structured dict, never raises on missing files/dirs
##   - status: "ok" if zero missing, "warn" if any missing, "error" if modules_dir not found
##   - missing list contains {"path": str, "module": str|None, "context": str}
##   - stateless list contains module names with spool_dir: none
##   - ok list contains {"path": str, "module": str|None, "context": str}
def verify_spool_dirs(modules_dir: str) -> dict:
    """Verify all spool directories exist. Verify-only — never creates dirs.

    Args:
        modules_dir: Path to core/modules/ directory.

    Returns:
        dict with status, missing[], stateless[], ok[], checked count.
    """
    logger.info("[IMP:7][verify_spool_dirs][start] modules_dir=%s", modules_dir)

    result: dict = {
        "status": "ok",
        "missing": [],
        "stateless": [],
        "ok": [],
        "checked": 0,
    }

    # ── Guard: modules_dir must exist ──
    mods = Path(modules_dir)
    if not mods.is_dir():
        logger.error("[IMP:10][verify_spool_dirs][error] modules_dir not found: %s", modules_dir)
        result["status"] = "error"
        result["missing"].append(
            {
                "path": modules_dir,
                "module": None,
                "context": "modules_dir not found",
            }
        )
        return result

    # ── Section 1: Platform dirs ──
    _verify_dirs(PLATFORM_DIRS, "platform", result)

    # ── Section 2: Per-module spool_dir/spool_volume from module.yaml ──
    spool_found = 0
    has_observability_module = False

    for mod_path in sorted(mods.iterdir()):
        if not mod_path.is_dir():
            continue

        module_name = mod_path.name
        yaml_path = mod_path / "module.yaml"

        # Track observability module existence (conditional Section 4)
        if module_name == "observability":
            has_observability_module = True

        if not yaml_path.is_file():
            logger.info(
                "[IMP:7][verify_spool_dirs][skip] %s: no module.yaml",
                module_name,
            )
            continue

        # Parse module.yaml
        with open(yaml_path) as f:
            cfg = yaml.safe_load(f) or {}

        spool_dir = cfg.get("spool_dir")
        spool_volume = cfg.get("spool_volume")

        # Determine spool path: prefer spool_dir (absolute path) over spool_volume (Docker volume name)
        spool_path = None
        if spool_dir:
            spool_path = str(spool_dir).strip()
        elif spool_volume:
            spool_path = str(spool_volume).strip()

        if spool_path:
            # spool_dir: none = stateless module (explicit declaration, INFO not WARN)
            if spool_path == "none":
                result["stateless"].append(module_name)
                logger.info(
                    "[IMP:7][verify_spool_dirs][stateless] %s: spool_dir=none (stateless module)",
                    module_name,
                )
                continue

            spool_found += 1

            if os.path.isdir(spool_path):
                result["ok"].append(
                    {
                        "path": spool_path,
                        "module": module_name,
                        "context": "module spool_dir",
                    }
                )
                result["checked"] += 1
                logger.info(
                    "[IMP:7][verify_spool_dirs][ok] %s: %s exists",
                    module_name,
                    spool_path,
                )
            else:
                result["missing"].append(
                    {
                        "path": spool_path,
                        "module": module_name,
                        "context": "module spool_dir",
                    }
                )
                logger.warning(
                    "[IMP:8][verify_spool_dirs][warn] %s spool %s not found — run make provision",
                    module_name,
                    spool_path,
                )
        else:
            # No spool_dir or spool_volume declared
            logger.warning(
                "[IMP:8][verify_spool_dirs][warn] %s: no spool_dir/spool_volume in module.yaml",
                module_name,
            )

    logger.info(
        "[IMP:7][verify_spool_dirs][section2] Scanned modules, spool_found=%d",
        spool_found,
    )

    # ── Section 3: Wal-archive (always checked) ──
    _verify_single_dir(WAL_ARCHIVE, "wal-archive", result)

    # ── Section 4: Observability dirs (conditional) ──
    if has_observability_module:
        _verify_dirs(OBSERVABILITY_DIRS, "observability", result)
    else:
        logger.info(
            "[IMP:7][verify_spool_dirs][skip] Observability module not present — skipping obs dirs",
        )

    # ── Section 5: Fallback dirs (only if spool_found == 0) ──
    if spool_found == 0:
        logger.warning(
            "[IMP:8][verify_spool_dirs][fallback] No module.yaml spool paths found — verifying hardcoded fallback dirs",
        )
        _verify_dirs(FALLBACK_DIRS, "fallback", result)
    else:
        logger.info(
            "[IMP:7][verify_spool_dirs][skip] spool_found=%d > 0 — skipping fallback dirs",
            spool_found,
        )

    # ── Final status determination ──
    if result["missing"]:
        result["status"] = "warn"

    logger.info(
        "[IMP:9][verify_spool_dirs][done] status=%s checked=%d missing=%d stateless=%d",
        result["status"],
        result["checked"],
        len(result["missing"]),
        len(result["stateless"]),
    )
    return result


# endregion FUNC_verify_spool_dirs


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC__verify_dirs (helper)
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Check existence of a list of directories, mutating the result dict
## @io       dirs (list[str]), context_label (str), result (dict) → None (mutates result)
## @complexity 1 — linear iteration over dirs list
def _verify_dirs(dirs: list[str], context_label: str, result: dict) -> None:
    """Verify multiple directories exist, mutating result dict in place."""
    for dirpath in dirs:
        _verify_single_dir(dirpath, context_label, result)


# endregion FUNC__verify_dirs


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC__verify_single_dir (helper)
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  Check existence of a single directory, mutating the result dict
## @io       dirpath (str), context_label (str), result (dict) → None (mutates result)
## @complexity 1 — single os.path.isdir call
def _verify_single_dir(dirpath: str, context_label: str, result: dict) -> None:
    """Verify a single directory exists, mutating result dict in place."""
    if os.path.isdir(dirpath):
        result["ok"].append(
            {
                "path": dirpath,
                "module": None,
                "context": context_label,
            }
        )
        result["checked"] += 1
        logger.info("[IMP:7][verify_single_dir][ok] %s %s exists", context_label, dirpath)
    else:
        result["missing"].append(
            {
                "path": dirpath,
                "module": None,
                "context": context_label,
            }
        )
        logger.warning(
            "[IMP:8][verify_single_dir][warn] %s %s not found — run make provision",
            context_label,
            dirpath,
        )


# endregion FUNC__verify_single_dir


# ═══════════════════════════════════════════════════════════════════════════════
# region FUNC_main
# ═══════════════════════════════════════════════════════════════════════════════


## @purpose  CLI entrypoint: parse args, run verify_spool_dirs, output JSON, exit with code
## @io       ⇥ sys.argv → ⎋ exit {0,1,2}; stdout: JSON report
## @complexity 1 — argparse + dispatch
## @invariants
##   - --action verify is the only action (extensible for future --action check-contract)
##   - --modules-dir defaults to /opt/platform/core/modules (standard deploy path)
##   - JSON output on stdout for machine consumption
##   - Exit code: 0=ok, 1=warn (missing dirs), 2=error (system error)
def main() -> int:
    """CLI entrypoint for spool_validator.py.

    Usage:
        python3 spool_validator.py --action verify --modules-dir /opt/platform/modules
    """
    parser = argparse.ArgumentParser(
        description="Spool directory verify-only validator — checks spool dirs exist before deploy.",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["verify"],
        help="Action to perform (verify: check spool directory existence)",
    )
    parser.add_argument(
        "--modules-dir",
        default=os.path.join(str(platform_remote_base()), "core/modules"),
        type=str,
        help="Path to modules directory (default: PLATFORM_ROOT/core/modules)",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    action = args.action
    logger.info("[IMP:9][main][dispatch] Action=%s, modules_dir=%s", action, args.modules_dir)

    if action == "verify":
        result = verify_spool_dirs(args.modules_dir)

        # Print JSON report to stdout
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # ── Print WARN/ERROR summary to stderr for shell visibility ──
        if result["missing"]:
            for entry in result["missing"]:
                mod = entry.get("module") or "(platform)"
                path = entry["path"]
                ctx = entry.get("context", "")
                print(
                    f"[spool-validator][WARN] {mod}: {path} missing ({ctx}) — run make provision",
                    file=sys.stderr,
                )
            logger.warning("[IMP:9][main][exit] WARN — %d missing dir(s)", len(result["missing"]))

        if result["stateless"]:
            logger.info(
                "[IMP:7][main][stateless] %d stateless modules: %s",
                len(result["stateless"]),
                ", ".join(result["stateless"]),
            )

        logger.info(
            "[IMP:9][main][done] Verified %d dir(s) — status=%s",
            result["checked"],
            result["status"],
        )

        if result["status"] == "error":
            return 2
        if result["status"] == "warn":
            return 1
        return 0

    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
