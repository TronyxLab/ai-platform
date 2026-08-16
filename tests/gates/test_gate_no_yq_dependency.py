# GREP_SUMMARY: yq, gate, no-dependency, deprecated, removed, core
# STRUCTURE: ▶ grep for "yq eval" in core/ → ◇ .sh files → ⊕ fail if found
# region MODULE_CONTRACT
## @purpose  Gate test: ensure no shell script in core/ uses yq eval for node.yaml operations
## @scope    Scans core/ for yq eval in .sh and all files
## @invariants — yq is completely removed as a dependency for node.yaml operations
## @rationale DevPlan 088 AC3: 0 grep "yq.*node" core/ — yq removed
## @changes 2026-07-30 · DevPlan 088 T12
# endregion MODULE_CONTRACT

"""
Gate test: yq dependency removed.

Verifies: No file in core/ uses yq eval for node.yaml operations.
          yq should be replaced with NodeYaml CLI.
"""

import logging
import subprocess
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"
EXCLUDED_PATTERNS = [
    "__pycache__",
    "entrypoint-manifest.yaml",
    "core/modules/",
]
EXCLUDED_EXTENSIONS = [".pyc"]


def _is_violation(line: str) -> bool:
    """Determine if a grep result line represents a real yq eval violation."""
    if not line.strip():
        return False
    # Exclude binary __pycache__ matches
    if any(p in line for p in EXCLUDED_PATTERNS):
        return False
    # Exclude by file extension
    if any(line.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return False
    # Split file:line:content to check the content portion
    parts = line.split(":", 2)
    if len(parts) >= 3:
        content = parts[2].strip()
        # Skip if it's a comment/docstring (just mentioning yq in text)
        if content.startswith(("#", "##")):
            return False
    return True


@pytest.mark.gate
@ldd_trajectory
def test_no_yq_operations_on_node(caplog) -> None:
    """Fail if any file in core/ uses yq for node.yaml operations.

    ## @purpose  Enforce AC3: yq removed as dependency for node.yaml operations
    ## @invariants — yq is no longer required for node.yaml operations
    # 🧪 TRAP[TEST] · 2026-07-30 · Gate(AC3) · yq.*node in core/ · Remove if: all yq artifacts removed
    """
    logger.info("[IMP:9][gate_no_yq][start] Scanning %s for yq operations on node.yaml", CORE_DIR)

    result = subprocess.run(
        ["grep", "-rn", r"yq.*node", str(CORE_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # grep rc=1 (no match) is the PASS condition
    )

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    violations = []
    for line in lines:
        if not line.strip():
            continue
        # Skip our own test registration in manifest
        if "entrypoint-manifest.yaml" in line and "test_no_yq" in line:
            continue
        if not _is_violation(line):
            logger.info("[IMP:7][gate_no_yq][skip] Non-violation: %s", line.strip())
            continue
        violations.append(line)

    if violations:
        logger.error("[IMP:9][gate_no_yq][violation] Found %d yq+node reference(s) in core/", len(violations))
        print(f"\nFOUND {len(violations)} yq+node reference(s) in core/:")
        for v in violations:
            print(f"  {v}")
        print("\nyq should be removed — use NodeYaml CLI instead:")
        print("  python3 -m core.internal.shared.node_yaml --file <path> --get <key>\n")

    assert len(violations) == 0, (
        f"Found {len(violations)} yq+node reference(s) in core/. "
        f"Replace with NodeYaml CLI (python3 -m core.internal.shared.node_yaml)"
    )

    logger.info("[IMP:9][gate_no_yq][pass] No yq operations on node.yaml found in core/")
