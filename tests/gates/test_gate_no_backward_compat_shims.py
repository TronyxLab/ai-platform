# GREP_SUMMARY: gate no-backward-compat invariant9 backward-compat compat-shim markers scan
# STRUCTURE: ▶ test_no_backward_compat_shims → grep patterns in core/ and node-configs/ → ◇ filter by extension → ◇ exclude comments/docs → ⊕ assert no functional compat markers
# region MODULE_CONTRACT
## @purpose  Gate test for Invariant 9: no backward compatibility shims in core/ or node-configs/
## @scope    Scans functional Python, Shell, and Makefile files for backward_compat, ,
##           compat_shim, deprecated markers. Architectural/design comments about compatibility
##           are excluded from the scan — only functional compat shims are flagged.
##           Перенесён из tests/unit/test_no_backward_compat_markers.py (DevPlan 119 A1 —
##           зомби-гейт вне tests/gates/; репозиторий-wide скан = gate-природа).
## @invariants
##   - Only functional source files scanned (.py, .sh, .mk) — not .md, .yml, .yaml, .txt
##   - Comments about architecture (not functional code) are excluded
##   - Backward compatibility in templates is expected (module.mk) — excluded
## @rationale  Invariant 9: "тестовый сервер может быть пересоздан заново — обратная
##             совместимость не требуется". No functional backward-compat shims should exist.
## @changes — 2026-07-18 | Created per DevPlan 011 T7
## @changes — 2026-08-02 | DevPlan 119 A1 — перенесён из tests/unit/test_no_backward_compat_markers.py
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Directories to scan
SCAN_DIRS = [
    PROJECT_ROOT / "core",
    PROJECT_ROOT / "node-configs",
]

# Allowed patterns — these are NOT functional compat shims:
# - Comments about architecture (not code)
# - Documentation references
# - Template backward-compat that is intentional (module.mk)
ALLOWED_PATTERNS = [
    # Architecture comments using backward_compat as a keyword
    r"backward_compat.*shim.*not.*needed",
    r"backward.compat.*not.*require",
    r"#.*backward.compat",
    r"//.*backward.compat",
    r"<!--.*backward.compat.*-->",
    # Template files that intentionally provide backward compat
    r"templates/module\.mk.*backward",
    # Test files testing for backward compat markers
    r"test_no_backward_compat",
]


def _is_allowed(path: Path, line: str, lineno: int) -> bool:
    """Check if a match is an allowed pattern (not a functional compat shim)."""
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in ALLOWED_PATTERNS)


@pytest.mark.gate
@ldd_trajectory
def test_no_backward_compat_shims(caplog):
    """Scan core/ and node-configs/ for functional legacy/compat/backward markers.

    ## @purpose — Validate Invariant 9: no functional backward compatibility shims
    ##            exist in core/ or node-configs/. The test server is disposable and
    ##            does not require backward compat.
    ## @io — ⎋ None (asserts no functional compat markers found)
    ## @complexity — O(F*L) where F = files scanned, L = lines per file
    """
    include_extensions = {".py", ".sh", ".mk"}
    exclude_dirs = {"__pycache__", ".git", "node_modules", ".eggs"}

    # Patterns to find functional compat shims
    search_patterns = re.compile(
        r"\b(backward_compat|compat_shim|legacy_shim|deprecated_shim)\b",
        re.IGNORECASE,
    )

    violations = []
    files_scanned = 0

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            logger.warning("[IMP:7][gate][no_compat] Scan dir not found: %s — skipping", scan_dir)
            continue

        for path in sorted(scan_dir.rglob("*")):
            # Skip excluded dirs
            if any(part in exclude_dirs for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix not in include_extensions:
                continue

            files_scanned += 1
            try:
                lines = path.read_text(errors="replace").split("\n")
            except (OSError, UnicodeDecodeError):
                continue

            for lineno, line in enumerate(lines, 1):
                if search_patterns.search(line):
                    stripped = line.strip()
                    # Skip allowed patterns (architecture comments, docs, etc.)
                    if _is_allowed(path, stripped, lineno):
                        continue
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {stripped}")
                    logger.info(
                        "[IMP:9][gate][no_compat] FAIL: compat marker in %s:%d", path.relative_to(PROJECT_ROOT), lineno
                    )

    logger.info(
        "[IMP:9][gate][no_compat] Scanned %d files across %d dirs, found %d violations",
        files_scanned,
        len(SCAN_DIRS),
        len(violations),
    )

    assert not violations, (
        f"[IMP:9][gate][no_compat] FAIL: Found {len(violations)} functional compat shims:\n" + "\n".join(violations)
    )

    logger.info("[IMP:9][gate][no_compat] PASS: No functional backward compat shims found in %d files", files_scanned)
