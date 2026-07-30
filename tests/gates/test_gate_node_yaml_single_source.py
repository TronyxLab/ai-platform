# GREP_SUMMARY: node_yaml, single-source, gate, no-direct-yaml, safe-load, NodeYaml
# STRUCTURE: ▶ grep for yaml.safe_load in core/internal/ → ◇ filter node_yaml.py ∧ comments → ⊕ fail if violations remain
# region MODULE_CONTRACT
## @purpose  Gate test: ensure no file uses yaml.safe_load for node.yaml outside NodeYaml facade
## @scope    Scans core/internal/ for yaml.safe_load calls referencing node.yaml paths
## @invariants — Only NodeYaml facade (node_yaml.py) reads node.yaml via raw yaml.safe_load
## @rationale DevPlan 088 AC2: 0 grep "yaml.safe_load.*node" core/internal/ вне NodeYaml
## @changes 2026-07-30 · DevPlan 088 T11
# endregion MODULE_CONTRACT

"""
Gate test: NodeYaml single source of truth for node.yaml.

Verifies: No file in core/internal/ uses yaml.safe_load to read node.yaml
          outside of the NodeYaml facade module itself.
"""

import logging
import subprocess
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).resolve().parent.parent.parent / "core"
INTERNAL_DIR = CORE_DIR / "internal"
NODE_YAML_MODULE = "node_yaml.py"
EXEMPTED_PATTERNS = [
    "# LEGACY",
    "# deprecated",
    "# noqa",
    "#",
]


def _is_violation(line: str) -> bool:
    """Determine if a grep result line represents a real violation (not comment or binary)."""
    # Skip blank lines
    if not line.strip():
        return False

    # Skip __pycache__ binary matches
    if "__pycache__" in line:
        return False

    # Skip comment-only lines (docstrings, inline comments starting with #)
    # Extract the content after the file:line: part
    # Format: path/file.py:NN:content
    parts = line.split(":", 2)
    if len(parts) >= 3:
        content = parts[2].strip()
        # If the line content starts with #, it's a comment/documentation line
        if content.startswith(("#", "##")):
            return False

    return True


@pytest.mark.gate
@ldd_trajectory
def test_no_yaml_safe_load_node(caplog) -> None:
    """Fail if any file in core/internal/ uses yaml.safe_load for node.yaml outside NodeYaml.

    ## @purpose  Enforce AC2: all node.yaml reads go through NodeYaml
    ## @invariants — Exceptions: node_yaml.py itself
    # 🧪 TRAP[TEST] · 2026-07-30 · Gate(AC2) · yaml.safe_load node.yaml · Remove if: all consumers migrated
    """
    logger.info("[IMP:9][gate_node_yaml][start] Scanning %s for direct yaml.safe_load of node.yaml", INTERNAL_DIR)

    result = subprocess.run(
        ["grep", "-rn", r"yaml\.safe_load.*node", str(INTERNAL_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    violations = []
    for line in lines:
        if not line.strip():
            continue
        # Skip the NodeYaml facade module itself
        if NODE_YAML_MODULE in line:
            logger.info("[IMP:7][gate_node_yaml][skip] NodeYaml facade exempted: %s", line.strip())
            continue
        # Skip legacy comments or migration notes
        pattern_skip = any(pattern in line for pattern in EXEMPTED_PATTERNS)
        if pattern_skip:
            logger.info("[IMP:7][gate_node_yaml][skip] Comment exempted: %s", line.strip())
            continue
        # Skip binary __pycache__ matches and comment-only lines
        if not _is_violation(line):
            logger.info("[IMP:7][gate_node_yaml][skip] Non-violation (binary/comment): %s", line.strip())
            continue
        violations.append(line)

    if violations:
        logger.error("[IMP:9][gate_node_yaml][violation] Found %d direct yaml.safe_load calls", len(violations))
        print(f"\nFOUND {len(violations)} violation(s) of single-source rule:")
        for v in violations:
            print(f"  {v}")
        print("\nAll node.yaml reads must go through NodeYaml (core/internal/shared/node_yaml.py)")
        print("  python3 -m core.internal.shared.node_yaml --file <path> --get <key>\n")

    assert len(violations) == 0, (
        f"Found {len(violations)} file(s) with direct yaml.safe_load for node.yaml "
        f"(only NodeYaml facade may read node.yaml directly)"
    )

    logger.info("[IMP:9][gate_node_yaml][pass] No direct yaml.safe_load of node.yaml outside NodeYaml facade")
