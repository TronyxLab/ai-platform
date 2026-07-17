"""
# GREP_SUMMARY: test-sync-inventory, unit-test, collect-tests, yaml-roundtrip, nodeids, static-audit
# STRUCTURE: ▶ test_sync_output_matches_collect → ⚡ import collect_tests + subprocess pytest --collect-only → ⊕ compare sets → ⎋ assert_equal ∥ ▶ test_sync_yaml_format_roundtrip → ⚡ sync_inventory(tmp_path) → ◇ yaml.safe_load → ◇ assert test_nodeids list[str] + header count == len() → ⎋ pass
# region MODULE_CONTRACT
## @purpose  Unit tests for tests/tools/sync_inventory.py: verify collection logic matches
##           pytest --collect-only output, and the generated YAML satisfies the gate loader contract.
## @scope    Two tests: output correctness (node IDs match collection) and format roundtrip
##           (YAML header count == list length, sorted list[str] structure).
## @invariants
##   - Native Python imports only (NO subprocess for test logic)
##   - tmp_path for file IO (NO hardcoded paths)
##   - LDD telemetry (caplog) printed before assertions
##   - At least one IMP:9 log in each successful test
##   - Tests use @pytest.mark.static_audit (no Docker requirement)
## @rationale  DevPlan T0 acceptance: выход хелпера == pytest --collect-only nodeids,
##             и формат читается gate-загрузчиком.
## @changes — 2026-07-15 | Created per DevPlan 008 T0
# endregion MODULE_CONTRACT
"""

import logging
import pathlib
import subprocess
import sys

import pytest
import yaml

from tests.tools.sync_inventory import collect_tests, sync_inventory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]


# region FUNC_test_sync_output_matches_collect
## @purpose  Verify collect_tests() returns node IDs matching direct pytest --collect-only
## @io       caplog → assert nodeids is sorted list[str] with valid format
## @complexity  O(T) where T = total test count


# 🧪 TRAP[TEST] · 2026-07-15 · unit/sync-inventory · collect_tests node IDs match
# · Regression: collection parser drift from gate _collect_tests()
# · Scenario: pytest --collect-only output parsed by collect_tests()
# · Last fail: N/A (first run)
# · Remove if: collect_tests is replaced by shared library
@pytest.mark.static_audit
def test_sync_output_matches_collect(caplog) -> None:
    """Verify collect_tests() returns all test node IDs matching direct pytest collection.

    ⚡ import collect_tests → ⊕ call → ⚡ subprocess(pytest --collect-only -q) for reference
    → ⊕ compare sets → ◇ all nodeids match format tests/...::test_... → ⎋ assert equal

    ## @purpose — DevPlan T0 acceptance: tool output == pytest --collect-only nodeids.
    ##            Also verifies the result is a sorted list[str] with valid format.
    ## @io — caplog → asserts; IMP:9 log present on success
    ## @complexity — O(T)
    """
    caplog.set_level(logging.INFO)
    logger.info("[IMP:8][test_sync_output_matches_collect] === Starting collection parity check ===")

    # --- Act: collect via tool ---
    nodeids: list[str] = collect_tests(project_root=_PROJECT_ROOT)
    logger.info("[IMP:8][test_sync_output_matches_collect] collect_tests() returned %d node IDs", len(nodeids))

    # --- Assert sorted list[str] ---
    assert isinstance(nodeids, list), "collect_tests() must return a list"
    assert all(isinstance(n, str) for n in nodeids), "All node IDs must be strings"
    assert nodeids == sorted(nodeids), "Node IDs must be sorted alphabetically"

    # --- Assert valid format: tests/...::test_... ---
    for nid in nodeids:
        assert "::" in nid, f"Node ID missing '::' separator: {nid}"
        assert nid.startswith("tests/"), f"Node ID must start with 'tests/': {nid}"

    # --- Compare with direct pytest --collect-only ---
    logger.info("[IMP:8][test_sync_output_matches_collect] Running direct pytest --collect-only for comparison...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )

    reference_nodeids: list[str] = []
    path_stack: list[str] = []
    indent_stack: list[int] = []
    for line in result.stdout.splitlines():
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("<Dir "):
            name = stripped[5:-1]
            while indent_stack and indent_stack[-1] >= indent:
                indent_stack.pop()
                path_stack.pop()
            path_stack.append(name)
            indent_stack.append(indent)
        elif stripped.startswith("<Package "):
            name = stripped[9:-1]
            while indent_stack and indent_stack[-1] >= indent:
                indent_stack.pop()
                path_stack.pop()
            path_stack.append(name)
            indent_stack.append(indent)
        elif stripped.startswith("<Module "):
            name = stripped[8:-1]
            while indent_stack and indent_stack[-1] >= indent:
                indent_stack.pop()
                path_stack.pop()
            path_stack.append(name)
            indent_stack.append(indent)
        elif stripped.startswith("<Function "):
            func_name = stripped[10:-1]
            if len(path_stack) >= 2:
                module_path = "/".join(path_stack[1:])
                nodeid = f"{module_path}::{func_name}"
                reference_nodeids.append(nodeid)

    reference_nodeids.sort()
    logger.info(
        "[IMP:8][test_sync_output_matches_collect] Reference: %d node IDs from direct pytest",
        len(reference_nodeids),
    )

    # --- LDD trajectory ---
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_imp9: bool = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message, flush=True)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    # --- Assertions ---
    assert len(nodeids) == len(reference_nodeids), (
        f"Tool collected {len(nodeids)} tests but direct pytest found {len(reference_nodeids)}"
    )
    assert nodeids == reference_nodeids, "Node ID mismatch between tool and direct pytest! First diff: " + str(
        set(nodeids) ^ set(reference_nodeids)
    )

    logger.info(
        "[IMP:9][test_sync_output_matches_collect] PASS — %d node IDs match direct pytest --collect-only",
        len(nodeids),
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_sync_output_matches_collect


# region FUNC_test_sync_yaml_format_roundtrip
## @purpose  Verify sync_inventory produces a YAML file readable by the gate loader:
##           test_nodeids is a sorted list[str], header count matches list length.
## @io       caplog, tmp_path → asserts; IMP:9 log present on success
## @complexity  O(T + L) where T = test count, L = header lines


# 🧪 TRAP[TEST] · 2026-07-15 · unit/sync-inventory · YAML format gate-loader contract
# · Regression: format drift (quoting, count mismatch, indentation)
# · Scenario: sync_inventory writes to tmp_path, then yaml.safe_load + count check
# · Last fail: N/A (first run)
# · Remove if: gate loader format changes
@pytest.mark.static_audit
def test_sync_yaml_format_roundtrip(caplog, tmp_path) -> None:
    """Verify sync_inventory output satisfies the gate loader contract.

    ⚡ sync_inventory(inventory_path=tmp_path/test_inventory.yaml)
    → ◇ yaml.safe_load → ◇ assert test_nodeids is sorted list[str]
    → ◇ parse header @changes count → ◇ assert count == len(test_nodeids)
    → ⎋ pass

    ## @purpose — DevPlan T0 acceptance: format is readable by gate loader.
    ##            No hardcoded paths — uses pytest tmp_path.
    ## @invariants
    ##   - test_nodeids key exists and is a list
    ##   - All elements are strings
    ##   - List is sorted
    ##   - Header @changes (N tests) count equals len(list)
    """
    caplog.set_level(logging.INFO)
    logger.info("[IMP:8][test_sync_yaml_format_roundtrip] === Starting YAML format roundtrip ===")

    # --- Arrange: construct inventory path in tmp ---
    tmp_tests_dir: pathlib.Path = tmp_path / "tests"
    tmp_tests_dir.mkdir(parents=True, exist_ok=True)
    inv_path: pathlib.Path = tmp_tests_dir / "test_inventory.yaml"

    logger.info(
        "[IMP:8][test_sync_yaml_format_roundtrip] Writing inventory to %s",
        inv_path,
    )

    # --- Act: sync inventory into tmp path ---
    # This collects from real project (project_root) but writes to tmp_path
    logger.info("[IMP:8][test_sync_yaml_format_roundtrip] Running sync_inventory with tmp inventory_path...")
    changed: bool = sync_inventory(
        project_root=_PROJECT_ROOT,
        inventory_path=inv_path,
    )

    # --- Assert the file was created ---
    assert inv_path.exists(), f"Inventory file was not created at {inv_path}"
    logger.info("[IMP:8][test_sync_yaml_format_roundtrip] File created, changed=%s", changed)

    # --- Read and parse the YAML ---
    raw_content: str = inv_path.read_text()
    logger.info("[IMP:8][test_sync_yaml_format_roundtrip] File size: %d bytes", len(raw_content))

    data: dict = yaml.safe_load(raw_content)
    logger.info("[IMP:8][test_sync_yaml_format_roundtrip] Parsed YAML: keys=%s", list(data.keys()))

    # --- Assert test_nodeids is a sorted list[str] ---
    assert "test_nodeids" in data, "YAML must contain 'test_nodeids' key"
    test_nodeids: list = data["test_nodeids"]
    assert isinstance(test_nodeids, list), "test_nodeids must be a list"
    assert len(test_nodeids) > 0, "test_nodeids must not be empty"
    assert all(isinstance(n, str) for n in test_nodeids), "All node IDs must be strings"
    assert test_nodeids == sorted(test_nodeids), "test_nodeids must be sorted alphabetically"

    logger.info(
        "[IMP:8][test_sync_yaml_format_roundtrip] Loaded %d node IDs, sorted=%s",
        len(test_nodeids),
        test_nodeids == sorted(test_nodeids),
    )

    # --- Assert header count matches ---
    # Parse @changes (N tests) pattern from raw file (not from parsed YAML — header is a comment)
    import re

    matches = re.findall(r"@changes.*\((\d+)\s+tests\)", raw_content)
    assert len(matches) >= 1, "Header must contain at least one @changes (N tests) entry"
    header_count: int = int(matches[-1])  # Last match = most recent
    actual_count: int = len(test_nodeids)
    assert header_count == actual_count, f"Header declares {header_count} tests but list has {actual_count}"

    logger.info(
        "[IMP:8][test_sync_yaml_format_roundtrip] Header count=%d matches actual count=%d",
        header_count,
        actual_count,
    )

    # --- LDD trajectory ---
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_imp9: bool = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message, flush=True)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    logger.info(
        "[IMP:9][test_sync_yaml_format_roundtrip] PASS — %d node IDs, header count=%d matches",
        actual_count,
        header_count,
    )
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_sync_yaml_format_roundtrip
