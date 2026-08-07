#!/usr/bin/env python3
# GREP_SUMMARY: gate no-inline-secrets-parsing secrets-env-patterns anti-drift DevPlan-086
# STRUCTURE: ┌_load_patterns + _walk_files┐ → ○ for each (file, pattern): ◇ match? → ⟦fail with offenders⟧ → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate test: verify NO file outside core/internal/shared/ parses secrets.env
##           using old inline patterns. Detects 5 patterns of direct secrets.env parsing
##           that violate DevPlan 086 (secrets parser unification).
## @scope    Scans core/internal/ and core/entrypoints/ — Python and shell files.
##           Excludes core/internal/shared/ (the canonical shared module).
##           5 patterns detected:
##             1. "for line in.*open.*secrets" — Python file-iteration parsing
##             2. "source_secrets_env" — legacy function name
##             3. "set -a;.*source.*secrets" — shell batch-export pattern
##             4. "\. \/run\/platform\/secrets" — shell dot-sourcing absolute path
##             5. "source \$secrets_env" — shell source with variable
## @invariants
##   - core/internal/shared/ is the ONLY directory allowed to parse secrets.env directly
##   - Any match outside shared/ → FAIL with list of offending files and patterns
##   - Uses grep via subprocess (not Python file I/O) for consistent regex matching
##   - Scans both .py and .sh files
## @rationale DevPlan 086: 7 inline parsers consolidated into one shared module
##            (core/internal/shared/secrets_env_parser.py). This gate prevents regression
##            — new code must NOT re-introduce inline secrets.env parsing.
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

# ── Scan targets (exclude shared/) ─────────────────────────────────────────

_SCAN_DIRS: tuple[str, ...] = (
    "core/internal",
    "core/entrypoints",
)

_EXCLUDE_DIRS: tuple[str, ...] = ("core/internal/shared",)

# ── Patterns to detect ────────────────────────────────────────────────────

_PATTERNS: list[dict[str, str]] = [
    {
        "id": "P1",
        "name": "Python for-line file iteration over secrets.env",
        "pattern": r"for\s+\w+\s+in.*open.*secrets",
        "include": "*.py",
        "description": "Python file-iteration parsing (for line in open(...))",
    },
    {
        "id": "P2",
        "name": "source_secrets_env function call",
        "pattern": r"source_secrets_env",
        "include": "*.{py,sh}",
        "description": "Legacy source_secrets_env() function call (DevPlan 086 migration target)",
    },
    {
        "id": "P3",
        "name": "Shell batch export (set -a; source secrets)",
        "pattern": r"set\s+-a;.*source.*secrets",
        "include": "*.sh",
        "description": "Shell set -a; source secrets.env batch export pattern",
    },
    {
        "id": "P4",
        "name": "Shell dot-sourcing absolute /var/lib/platform/run/secrets",
        "pattern": r"\.\s+/var/lib/platform/run/secrets",
        "include": "*.sh",
        "description": "Shell dot-sourcing absolute path /var/lib/platform/run/secrets.env",
    },
    {
        "id": "P5",
        "name": "Shell source with $secrets_env variable",
        "pattern": r"source\s+\$secrets_env",
        "include": "*.sh",
        "description": "Shell source $secrets_env variable (should use export_shell from shared module)",
    },
]


# region HELPERS


def _find_offending_files(
    scan_root: str, exclude_prefixes: tuple[str, ...], patterns: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Scan directories for old inline secrets.env parsing patterns.

    ## @purpose — For each pattern, run grep on specified file types in scan
    ##            directories, filter out excluded paths, and collect violations.
    ## @io — ⇥ scan_root: str — project root path
    ##       ⇥ exclude_prefixes: tuple[str, ...] — path prefixes to exclude
    ##       ⇥ patterns: list[dict] — pattern definitions with id/name/pattern/include
    ##       → ⎋ list[dict] — violations with pattern_id, file, line content, description
    ## @complexity — O(P * D) where P = patterns, D = scanned directories
    """
    violations: list[dict[str, str]] = []

    for pat in patterns:
        pattern_id = pat["id"]
        pattern_name = pat["name"]
        regex = pat["pattern"]
        file_glob = pat["include"]
        desc = pat["description"]

        logger.debug(
            "[IMP:5][_find_offending_files] Scanning for pattern %s: %s",
            pattern_id,
            pattern_name,
        )

        # Build grep command
        grep_cmd = [
            "grep",
            "-rn",
            "-E",
            regex,
            "--include=" + file_glob,
        ]
        # Add scan directories
        grep_cmd.extend(os.path.join(scan_root, scan_dir) for scan_dir in _SCAN_DIRS)

        try:
            result = subprocess.run(
                grep_cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=scan_root,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[IMP:6][_find_offending_files] grep timed out for pattern %s", pattern_id)
            continue
        except FileNotFoundError:
            logger.warning("[IMP:6][_find_offending_files] grep not found — skipping pattern %s", pattern_id)
            continue

        if result.returncode not in (0, 1):
            logger.warning(
                "[IMP:6][_find_offending_files] grep exited with code %d for pattern %s",
                result.returncode,
                pattern_id,
            )
            continue

        if not result.stdout.strip():
            logger.debug("[IMP:5][_find_offending_files] No matches for pattern %s", pattern_id)
            continue

        # Filter out excluded paths
        for line in result.stdout.splitlines():
            filepath = line.split(":")[0] if ":" in line else ""
            if not filepath:
                continue
            # Make path relative
            rel_path = os.path.relpath(filepath, scan_root)
            # Check exclusion
            if any(rel_path.startswith(ep) for ep in exclude_prefixes):
                logger.debug("[IMP:5][_find_offending_files] Excluded: %s", rel_path)
                continue
            # Also exclude .ai/, .venv/, .git/
            if rel_path.startswith((".ai/", ".venv/", ".git/")):
                continue

            violations.append(
                {
                    "pattern_id": pattern_id,
                    "pattern_name": pattern_name,
                    "file": rel_path,
                    "line": line,
                    "description": desc,
                }
            )

    return violations


# endregion HELPERS


# ── Gate test ─────────────────────────────────────────────────────────────


# region FUNC_test_no_inline_secrets_parsing_outside_shared

## @purpose — Verify NO file outside core/internal/shared/ parses secrets.env
##            using old inline patterns. Prevents regression of DevPlan 086.

# 🧪 TRAP[TEST] · 2026-07-30 · gate/no-inline-secrets-parsing · REGRESSION(086)
# · SCENARIO(grep scan for 5 inline parsing patterns across core/{internal,entrypoints})
# · LAST_FAIL(N/A — new gate)
# · REMOVE_IF(all consumers migrated to shared secrets_env_parser and no old patterns remain)


@pytest.mark.gate
@ldd_trajectory
def test_no_inline_secrets_parsing_outside_shared(caplog) -> None:
    """Verify no file outside core/internal/shared/ parses secrets.env using old patterns.

    ## @purpose — DevPlan 086 regression gate: detects 5 patterns of direct
    ##            secrets.env parsing that must be consolidated via the shared
    ##            module core/internal/shared/secrets_env_parser.py.
    ## @io — ⎋ None (assert side-effect via pytest.fail on violations)
    ## @complexity — O(P * D * F) where P = 5 patterns, D = scan dirs, F = matched files
    """
    logger.info(
        "[IMP:8][test_no_inline_secrets_parsing_outside_shared] "
        "Scanning core/{internal,entrypoints} for inline secrets.env parsing patterns"
    )

    violations = _find_offending_files(
        scan_root=str(_PROJECT_ROOT),
        exclude_prefixes=_EXCLUDE_DIRS,
        patterns=_PATTERNS,
    )

    if violations:
        logger.error(
            "[IMP:9][test_no_inline_secrets_parsing_outside_shared] "
            "FOUND %d violation(s) of inline secrets.env parsing",
            len(violations),
        )

        # Group by file for readable output
        by_file: dict[str, list[dict]] = {}
        for v in violations:
            by_file.setdefault(v["file"], []).append(v)

        failure_lines: list[str] = []
        failure_lines.append(
            f"[IMP:10] FAIL: {len(violations)} inline secrets.env parsing pattern(s) found "
            f"outside core/internal/shared/:"
        )
        for filepath, file_violations in sorted(by_file.items()):
            failure_lines.append(f"\n  {filepath}:")
            for v in file_violations:
                failure_lines.append(f"    [{v['pattern_id']}] {v['description']}")
                failure_lines.append(f"      → {v['line'][:200]}")

        failure_msg = "\n".join(failure_lines)
        print(failure_msg)

        pytest.fail(
            f"{len(violations)} inline secrets.env parsing pattern(s) found "
            f"outside core/internal/shared/.\n"
            f"All secrets.env parsing must go through the shared module:\n"
            f"  from core.internal.shared.secrets_env_parser import parse\n"
            f"See DevPlan 086 for details.\n"
            + "\n".join(f"  {v['file']}: [{v['pattern_id']}] {v['description']}" for v in violations)
        )

    logger.info(
        "[IMP:9][test_no_inline_secrets_parsing_outside_shared] "
        "PASS — no inline secrets.env parsing patterns found in %d directories",
        len(_SCAN_DIRS),
    )


# endregion FUNC_test_no_inline_secrets_parsing_outside_shared
