# GREP_SUMMARY: gate path-consistency opt-core opt-platform path-detector crontab docker-compose makefile
# STRUCTURE: ┌_scan_for_opt_core(paths) → regex (?<!/platform)/opt/core/┐ → ◇ test_no_opt_core_references(glob all .sh .yml .yaml crontab Makefile .mk .service) → ◇ test_gate_path_consistency_negative_original_form(tmp_path) → ⊕ assert violations==0
# region MODULE_CONTRACT
## @purpose  CI gate: detect orphan /opt/core/ references that should be /opt/platform/core/
##           Canonical PLATFORM_ROOT = /opt/platform (defined in core/lib/paths.sh)
## @scope    Scans all *.sh, crontab, *.yml, *.yaml, Makefile, *.mk, *.service files under core/
##           for bare /opt/core/ references NOT preceded by /platform.
## @invariants
##   - Canonical root for VPS deployment is /opt/platform (core/lib/paths.sh PLATFORM_ROOT)
##   - /opt/core/ is NEVER valid — only /opt/platform/core/ is allowed
##   - Detector uses regex: (?<!/platform)/opt/core/ — negative lookbehind ensures /platform/core/ passes
##   - ALLOWLIST can suppress known false positives with documented rationale
##   - Must be registered in core/entrypoint-manifest.yaml and use @pytest.mark.gate
##   - MUST have negative test covering the original bug strings from crontab (R5 ANTI-SURVIVORSHIP)
## @rationale ISS-R1 drift audit found cron jobs silently failing because paths used /opt/core/
##            instead of /opt/platform/core/. This gate prevents reintroduction of orphan paths.
## @changes 2026-07-18 | Created per ISS-R3 drift-audit fix
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# ALLOWLIST: explicitly document any paths that legitimately contain /opt/core/
# (not preceded by /platform). These are documentation references to the OLD (broken) path,
# NOT runtime paths. Only file paths are matched — not line contents.
# Extend with rationale if a false positive is confirmed.
ALLOWLIST: list[str] = [
    # core/internal/bootstrap/install-tor-proxy.sh — comment describing historical bug:
    # "Раньше было: 'CRON' и ${PLATFORM_ROOT}/core/ — не работало после rsync в /opt/core/"
    # This is documentation about the old path, not a runtime reference.
    "core/internal/bootstrap/install-tor-proxy.sh",
    # core/modules/backup-cron/scripts/crontab — TRAP[BUG] comments documenting the ISS-R1 fix.
    # All references to /opt/core/ are in comments describing the ORIGINAL bug path, not runtime.
    "core/modules/backup-cron/scripts/crontab",
    # core/entrypoint-manifest.yaml — gate description text mentions "orphan /opt/core/ references".
    # This is a description of what the gate detects, not a runtime path.
    "core/entrypoint-manifest.yaml",
]

# Regex: match /opt/core/ NOT preceded by /platform
# (?<!/platform) — negative lookbehind ensures /platform/core/ passes
_OPT_CORE_RE = re.compile(r"(?<!/platform)/opt/core/")

# Project root (two levels up from tests/gates/)
_PROJECT_ROOT = os.path.normpath(Path(__file__).resolve().parent / ".." / "..")

# Directories to scan (relative to project root)
_SCAN_DIRS = ["core"]

# File patterns to scan
_SCAN_GLOBS = [
    "**/*.sh",
    "**/crontab",
    "**/*.yml",
    "**/*.yaml",
    "**/Makefile",
    "**/*.mk",
    "**/*.service",
]

# ── Detector ───────────────────────────────────────────────────────────────────


def _scan_file_for_opt_core(file_path: str) -> list[tuple[str, str]]:
    """Scan a single file for orphan /opt/core/ references.

    ## @purpose — Detect lines containing bare /opt/core/ (not preceded by /platform).
    ##            Returns list of (file_path, line_content) tuples for each violation.
    ## @io — ⇥ file_path: str path to file → ⎋ list[(file_path, line)] violations
    ## @complexity — O(n) where n = lines in file
    """
    violations: list[tuple[str, str]] = []
    try:
        with pathlib.Path(file_path).open(encoding="utf-8", errors="replace") as f:
            for line_num, line in enumerate(f, 1):
                stripped = line.rstrip("\n")
                if _OPT_CORE_RE.search(stripped):
                    violations.append((file_path, stripped))
                    logger.info(
                        "[IMP:8][path-detector] VIOLATION: %s:%d — %s",
                        file_path,
                        line_num,
                        stripped,
                    )
    except (OSError, UnicodeDecodeError) as exc:
        # Log but don't fail — binary files or permission errors are non-blocking
        logger.info("[IMP:7][path-detector] SKIP %s: %s", file_path, exc)
    return violations


def scan_core_for_opt_core() -> list[tuple[str, str]]:
    """Scan all relevant files under core/ for orphan /opt/core/ references.

    ## @purpose — Aggregate violations from all scanned files.
    ## @io — ⇥ None (uses _SCAN_DIRS, _SCAN_GLOBS) → ⎋ list[(file_path, line)] violations
    ## @complexity — O(f * n) where f = files matched, n = avg lines per file
    """
    all_violations: list[tuple[str, str]] = []
    seen_paths: set[str] = set()

    for scan_dir in _SCAN_DIRS:
        abs_scan_dir = Path(_PROJECT_ROOT) / scan_dir
        if not pathlib.Path(abs_scan_dir).is_dir():
            logger.info("[IMP:7][path-detector] SKIP directory not found: %s", abs_scan_dir)
            continue

        for pattern in _SCAN_GLOBS:
            matched_files = sorted(pathlib.Path(abs_scan_dir).rglob(pattern))
            for file_path in matched_files:
                str_path = str(file_path)
                # Skip symlinks to avoid duplicates
                if Path(str_path).is_symlink():
                    continue
                # Avoid double-scanning the same real path
                real_path = os.path.realpath(str_path)
                if real_path in seen_paths:
                    continue
                seen_paths.add(real_path)

                violations = _scan_file_for_opt_core(str_path)
                all_violations.extend(violations)

    # Filter out ALLOWLIST entries — match by relative path (from PROJECT_ROOT)
    filtered: list[tuple[str, str]] = []
    for file_path, line_content in all_violations:
        # Compute relative path from project root
        try:
            rel_path = os.path.relpath(file_path, _PROJECT_ROOT)
        except ValueError:
            rel_path = file_path
        signature = f"{file_path}:{line_content.strip()}"
        if (
            signature not in ALLOWLIST
            and line_content not in ALLOWLIST
            and file_path not in ALLOWLIST
            and rel_path not in ALLOWLIST
        ):
            filtered.append((file_path, line_content))

    return filtered


# ── Positive Gate Test ─────────────────────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — no orphan /opt/core/ references
# · Scenario: scan all *.sh, crontab, *.yml, *.yaml, Makefile, *.mk, *.service under core/
# ·   for bare /opt/core/ (not preceded by /platform). Any match is a violation.
# · Last fail: N/A (preventive — created after ISS-R1 fix)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_no_opt_core_references(caplog) -> None:
    """Positive gate: ensure no orphan /opt/core/ references exist in core/.

    ## @purpose — Scans repository for bare /opt/core/ paths (canonical root:
    ##            /opt/platform from core/lib/paths.sh). Any match = violation.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(f * n) where f = files, n = lines per file
    """
    logger.info("[IMP:8][gate-path-consistency] Scanning core/ for orphan /opt/core/ references")
    logger.info("[IMP:8][gate-path-consistency] Scan dirs: %s", _SCAN_DIRS)
    logger.info("[IMP:8][gate-path-consistency] Scan globs: %s", _SCAN_GLOBS)

    violations = scan_core_for_opt_core()

    print("\n" + "=" * 70)
    print("  GATE: PATH CONSISTENCY — ORPHAN /opt/core/ DETECTOR")
    print("=" * 70)

    if not violations:
        print("  ✅ 0 violations — all paths use /opt/platform/core/ canonical root\n")
        logger.info("[IMP:9][gate-path-consistency] PASS — 0 orphan /opt/core/ references")
    else:
        print(f"  ❌ {len(violations)} violation(s) found:\n")
        for file_path, line_content in violations:
            print(f"    FILE: {file_path}")
            print(f"    LINE: {line_content}")
            print()
        logger.info(
            "[IMP:9][gate-path-consistency] FAIL — %d violation(s) found",
            len(violations),
        )
    print("=" * 70 + "\n")

    if violations:
        violation_report = "\n".join(f"  {fp}: {lc}" for fp, lc in violations)
        pytest.fail(
            f"[IMP:9][gate-path-consistency] Gate FAILED: {len(violations)} orphan "
            f"/opt/core/ reference(s). Canonical root is /opt/platform/:\n{violation_report}"
        )


# ── Negative Test (R5 ANTI-SURVIVORSHIP) ──────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-18 · ANTI-SURVIVORSHIP · R5 — negative test for ISS-R1 original bug
# · Scenario: feed detector function with exact original bug string
# ·   `* * * * *   root /opt/core/internal/healthcheck/docker-healthcheck.sh >> ...`
# ·   and assert it IS detected. Also verify that /opt/platform/core/... passes cleanly.
# · Last fail: N/A (preventive — created after ISS-R1 fix)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_gate_path_consistency_negative_original_form(caplog) -> None:
    """Negative test (R5 ANTI-SURVIVORSHIP): original bug string must be detected.

    ## @purpose — Verify the detector catches the exact original bug strings from
    ##            ISS-R1 (crontab lines 44, 46) and rejects valid /opt/platform/core/ paths.
    ## @io — ⇥ tmp_path with test files → ⎋ None (assert side-effect)
    ## @invariants
    ##   - Original bug string `/opt/core/...` MUST be detected
    ##   - Canonical path `/opt/platform/core/...` MUST NOT be detected
    ## @complexity — O(1) — fixed number of test cases
    """
    import pathlib as pl
    import tempfile

    logger.info("[IMP:8][gate-path-consistency-negative] Testing original bug string detection")

    # Create a temporary directory with test files
    with tempfile.TemporaryDirectory() as tmpdir:
        # ── File 1: exact original bug string (crontab line 44) ────────
        bug_crontab = pl.Path(tmpdir) / "crontab"
        bug_crontab.write_text(
            "# [IMP:8][crontab] * * * * * — Docker daemon healthcheck (TASK-11)\n"
            "* * * * *   root /opt/core/internal/healthcheck/docker-healthcheck.sh >> /var/log/platform/backup/docker-healthcheck.log 2>&1\n"
            "# [IMP:8][crontab] 0 * * * * — Disk space monitor + docker prune (TASK-12)\n"
            "0   *   *   *   *   root /opt/core/modules/backup-cron/scripts/disk-monitor.sh >> /var/log/platform/backup/disk-monitor.log 2>&1\n"
        )
        logger.info("[IMP:8][gate-path-consistency-negative] Created bug crontab: %s", bug_crontab)

        # ── File 2: valid canonical path (should NOT be detected) ──────
        valid_script = pl.Path(tmpdir) / "valid.sh"
        valid_script.write_text(
            "#!/usr/bin/env bash\n"
            "# Canonical path — should NOT trigger detector\n"
            'PLATFORM_ROOT="/opt/platform"\n'
            'HEALTHCHECK="/opt/platform/core/internal/healthcheck/docker-healthcheck.sh"\n'
        )
        logger.info("[IMP:8][gate-path-consistency-negative] Created valid script: %s", valid_script)

        # ── Scan both files ────────────────────────────────────────────
        all_violations: list[tuple[str, str]] = []
        for fpath in [str(bug_crontab), str(valid_script)]:
            violations = _scan_file_for_opt_core(fpath)
            all_violations.extend(violations)

        print("\n" + "=" * 70)
        print("  NEGATIVE TEST: ORIGINAL BUG STRING DETECTION")
        print("=" * 70)

        for fp, lc in all_violations:
            print(f"    DETECTED: {fp}")
            print(f"    LINE:     {lc}")
            print()

        # ── Assert the bug strings ARE detected (2 violations from crontab) ──
        bug_violations = [(fp, lc) for fp, lc in all_violations if "opt/core/" in lc and "opt/platform/core/" not in lc]
        num_bug_violations = len(bug_violations)

        logger.info(
            "[IMP:9][gate-path-consistency-negative] Found %d bug violations (expected 2)",
            num_bug_violations,
        )
        print(f"  Bug violations found: {num_bug_violations} (expected: 2)")
        print(f"  Valid path false positives: {len(all_violations) - num_bug_violations} (expected: 0)")
        print("=" * 70 + "\n")

        assert num_bug_violations == 2, (
            f"[IMP:9][gate-path-consistency-negative] FAIL: Expected 2 violations from original bug "
            f"strings, got {num_bug_violations}. Original crontab lines should have been detected."
        )

        # ── Assert that the valid canonical path is NOT detected ──────────
        valid_violations = [
            (fp, lc)
            for fp, lc in all_violations
            if "opt/platform/core/" in lc and "opt/core/" not in lc.replace("opt/platform/core/", "")
        ]
        valid_false_positives = len(valid_violations)
        assert valid_false_positives == 0, (
            f"[IMP:9][gate-path-consistency-negative] FAIL: {valid_false_positives} false positive(s) "
            f"detected for valid /opt/platform/core/ paths: {valid_violations}"
        )

        logger.info("[IMP:9][gate-path-consistency-negative] PASS — all assertions met")


# ── DEV HELPERS (not tests, not imported by test runners) ──────────────────────

if __name__ == "__main__":
    # Manual run: python -m tests.gates.test_gate_path_consistency
    logging.basicConfig(level=logging.INFO)
    violations = scan_core_for_opt_core()
    if violations:
        print(f"Found {len(violations)} violation(s):")
        for fp, lc in violations:
            print(f"  {fp}: {lc}")
    else:
        print("No violations found.")
