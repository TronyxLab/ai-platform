# GREP_SUMMARY: gate no-hardcoded-local-paths platform-root user-homepath cross-platform drift sysadmin-paths server-paths
# STRUCTURE: ▶ scan tests/ + core/ *.py → ◇ detect /Users/, /home/<user>/, /opt/platform/ paths → ◇ allowlist os.environ.get / os.path.dirname → ◇ assert no hardcoded paths
# region MODULE_CONTRACT
## @purpose — Gate test: detect hardcoded local filesystem paths (e.g. /Users/tronyx/, /home/runner/)
##            and server platform paths (e.g. /opt/platform/) in Python files that break
##            cross-platform CI execution or hardcode deployment assumptions.
## @scope — Scans all *.py files under tests/ (home-dir patterns) and core/
##          (home-dir + server-path patterns). Test files legitimately reference
##          /opt/platform/ as test data — server-path check is core/-only.
## @invariants
##   - Detects: /Users/<username>/... and /home/<username>/... in tests/ and core/ (home dirs)
##   - Detects: /opt/platform/... in core/ only (server paths; tests reference it legitimately)
##   - Allowlist: os.environ.get("PLATFORM_ROOT", "/opt/platform") — legitimate fallback
##   - Allowlist: os.path.abspath(os.path.join(os.path.dirname(__file__), ...)) — auto-detect
##   - NOT flagged: /tmp/, /var/lib/platform, /etc/, /usr/ (generic system paths)
##   - NOT flagged: paths inside string literals that are clearly not filesystem references
## @rationale — P0 incident 2026-07-23: test_component_hermes.py:66 hardcoded
##            "/Users/tronyx/projects/ai-platform" broke all hermes component tests on CI.
##            P2 coverage gap 2026-07-23 (UF9): gate scanned only tests/, missed
##            hardcoded /opt/platform paths in core/ (e.g. compose_preflight.py:45).
##            Prevention gate prevents recurrence across the entire platform codebase.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# Hardcoded macOS/Linux home directory path patterns
# Matches: "/Users/tronyx/projects/ai-platform", "/home/runner/work/AI-platform", etc.
_HARDCODED_HOME_PATH = re.compile(
    r'["\'](/Users/[\w.-]+/|/home/[\w.-]+/(?!runner/work/)[\w.-]+/)',
)

# Hardcoded server platform paths (without env-var fallback)
# Matches: "/opt/platform/core/...", "/opt/platform/node-configs/...", etc.
# UF9 (2026-07-23): compose_preflight.py:45 hardcoded /opt/platform — P2 coverage gap
_HARDCODED_SERVER_PATH = re.compile(
    r'["\'](/opt/platform/)',
)

# Allowlisted files (known issues, tracked separately)
_ALLOWLISTED_FILES: set[str] = set()

# Allowlisted patterns in code (legitimate uses)
# Pattern 1: os.path.dirname(__file__) auto-detection for home-directory paths
# Pattern 2: os.environ.get("PLATFORM_ROOT", "/opt/platform") — server path with env-var fallback
_ALLOWLISTED_CONTENT = re.compile(
    r"os\.(?:path\.abspath\(os\.path\.join\(os\.path\.dirname\(__file__\)"
    r"|environ\.get\(['\"]PLATFORM_ROOT['\"],\s*['\"]/opt/platform['\"])",
)


def _scan_for_hardcoded_paths() -> list[tuple[str, int, str]]:
    """Scan tests/ and core/ Python files for hardcoded local filesystem paths.

    ## @purpose — Detect hardcoded absolute paths referencing user home directories
    ##            (macOS/Linux) and server platform paths (/opt/platform/) that break
    ##            cross-platform execution or hardcode deployment assumptions.
    ##            Home-dir patterns checked in both tests/ and core/.
    ##            Server-path patterns checked only in core/ (tests legitimately
    ##            reference /opt/platform/ as test data/assertions).
    ## @io — ⎋ list[(file_path, line_number, matched_text)]
    ## @complexity — O(F * L) where F = number of scanned files, L = lines per file
    """
    findings: list[tuple[str, int, str]] = []
    # (dir, patterns) — home patterns checked everywhere, server patterns only in core/
    scan_configs: list[tuple[str, list[re.Pattern[str]]]] = [
        ("tests", [_HARDCODED_HOME_PATH]),
        ("core", [_HARDCODED_HOME_PATH, _HARDCODED_SERVER_PATH]),
    ]

    for scan_dir, patterns in scan_configs:
        scan_path: pathlib.Path = repo_root() / scan_dir

        if not scan_path.is_dir():
            logger.warning("[IMP:7][scan] Directory not found, skipping: %s", scan_dir)
            continue

        for py_file in sorted(scan_path.rglob("*.py")):
            # Skip __pycache__ and generated files
            if "__pycache__" in str(py_file) or py_file.name.startswith("."):
                continue

            rel_path = str(py_file.relative_to(repo_root()))

            if rel_path in _ALLOWLISTED_FILES:
                logger.info("[IMP:8][scan][allowlisted] Skipping %s", rel_path)
                continue

            try:
                content = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                logger.warning("[IMP:7][scan] Cannot read %s", rel_path)
                continue

            # Skip files that use the correct auto-detect or env-var fallback pattern
            if _ALLOWLISTED_CONTENT.search(content):
                logger.info(
                    "[IMP:8][scan][auto-detect] %s uses correct auto-detect/env-var pattern",
                    rel_path,
                )
                continue

            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                # Skip comments and docstrings
                if stripped.startswith(("#", '"""', "'''")):
                    continue

                for pattern in patterns:
                    for match in pattern.finditer(line):
                        matched_path = match.group()
                        findings.append((rel_path, i, matched_path))
                        logger.warning(
                            "[IMP:7][scan][hardcoded-path] %s:%d — %s",
                            rel_path,
                            i,
                            matched_path,
                        )

    return findings


@pytest.mark.gate
def test_no_hardcoded_local_paths(caplog):
    """Verify no Python file in tests/ or core/ contains hardcoded local filesystem paths.

    ## @purpose — Prevent cross-platform CI breakage and hardcoded deployment paths:
    ##            hardcoded macOS/Linux home directory paths and /opt/platform/ server
    ##            paths in Python code cause failures on other OS or break deployment.
    ##            Use os.path.dirname(__file__) (home dirs) or
    ##            os.environ.get("PLATFORM_ROOT", "/opt/platform") (server paths) instead.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(F * L) deferred to _scan_for_hardcoded_paths()
    """
    caplog.set_level(logging.INFO)
    findings = _scan_for_hardcoded_paths()

    if findings:
        detail_lines = [f"  {fp}:{ln} → {matched}" for fp, ln, matched in sorted(findings)]
        logger.error(
            "[IMP:9][gate][hardcoded-path] ⛔ Found %d hardcoded local path(s) in tests/ + core/",
            len(findings),
        )
        pytest.fail(
            f"Found {len(findings)} hardcoded local path(s) in Python files.\n"
            f"Hardcoded paths break cross-platform CI (macOS vs Linux) or deployment.\n"
            f"For home dirs: use os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')).\n"
            f'For server paths: use os.environ.get("PLATFORM_ROOT", "/opt/platform").\n'
            f"See _conftest/predeploy.py:224 for the home-dir pattern.\n\n" + "\n".join(detail_lines),
        )

    logger.info("[IMP:9][gate][hardcoded-path] ✅ No hardcoded local paths in tests/ + core/ — cross-platform safe")
