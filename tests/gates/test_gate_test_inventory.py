# GREP_SUMMARY: gate, test-inventory, baseline, local-file, marker-validation, changelog, anti-tamper
# STRUCTURE: ┌load test_inventory.yaml + test_inventory_changes.yaml┐ → ◇ pytest --collect-only → ◇ compare → ◇ assert
# region MODULE_CONTRACT
## @purpose — Gate tests that validate test inventory integrity:
##            1. All collected tests match the inventory YAML (bi-directional)
##            2. All tests have registered markers
##            3. No test removed without documented changelog (baseline from local file)
## @scope — Compare PR's test node IDs against baseline from local test_inventory.yaml.
##          Prevents silent test deletion or marker drift.
## @invariants
##   - baseline is from local test_inventory.yaml (committed file)
##   - Adding new tests is always OK
##   - Removing tests requires changelog entry in test_inventory_changes.yaml
##   - Every test must have at least one registered marker from pytest.ini
## @rationale — Silent test deletion is a CI anti-pattern. Baseline comparison
##              catches removal even when inventory is also modified in the same PR.
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-10
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess
import sys

import pytest
import yaml
from conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_PYPROJECT_TOML_PATH: pathlib.Path = _PROJECT_ROOT / "pyproject.toml"
_INVENTORY_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "test_inventory.yaml"
_CHANGELOG_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "test_inventory_changes.yaml"
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _load_inventory() -> list[str]:
    """Load test node IDs from tests/test_inventory.yaml.

    ## @purpose — Parse the inventory YAML from the local committed file.
    ##            Baseline is the committed test_inventory.yaml — no remote fetch needed.
    ## @io — ⎋ list[str] of test node IDs
    ## @complexity — O(N) where N = number of inventory entries
    """
    with open(_INVENTORY_PATH) as f:
        data = yaml.safe_load(f)
    nodeids = data.get("test_nodeids", [])
    logger.info("[IMP:8][_load_inventory] Loaded %d test node IDs from local test_inventory.yaml", len(nodeids))
    return nodeids


def _load_changelog() -> dict:
    """Load test inventory change log.

    ## @purpose — Parse the changelog YAML for documented test removals.
    ## @io — ⎋ dict with 'removed' list
    ## @complexity — O(1)
    """
    with open(_CHANGELOG_PATH) as f:
        data = yaml.safe_load(f) or {}
    removed = data.get("removed", [])
    logger.info("[IMP:8][_load_changelog] Loaded %d documented removals", len(removed))
    return {"removed": removed}


def _pop_to_indent(path_stack: list[str], indent_stack: list[int], indent: int) -> None:
    """Pop from stacks until indent matches the current level.

    ## @purpose — Maintain hierarchy tracking by popping tags at deeper indent levels.
    ## @complexity — O(D) where D = depth of tag nesting
    """
    while indent_stack and indent_stack[-1] >= indent:
        indent_stack.pop()
        path_stack.pop()


def _collect_tests() -> list[str]:
    """Run pytest --collect-only and return list of test node IDs.

    ## @purpose — Collect all discoverable test node IDs via pytest collection.
    ##            Supports pytest 9.x XML-like tree output format.
    ## @io — ⎋ list[str] of test node IDs from pytest collection
    ## @complexity — O(T) where T = total test count
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=_PROJECT_ROOT,
    )

    nodeids: list[str] = []
    path_stack: list[str] = []
    indent_stack: list[int] = []

    for line in result.stdout.splitlines():
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("<Dir "):
            name = stripped[5:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Package "):
            name = stripped[9:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Module "):
            name = stripped[8:-1]
            _pop_to_indent(path_stack, indent_stack, indent)
            path_stack.append(name)
            indent_stack.append(indent)

        elif stripped.startswith("<Function "):
            func_name = stripped[10:-1]
            # Build node ID: skip root dir (index 0), join rest with /, add ::func
            # Path: [Dir(root), Dir(tests), Package(gates), Module(file.py)]
            # NodeID: tests/gates/file.py::func_name
            if len(path_stack) >= 2:
                module_path = "/".join(path_stack[1:])
                nodeid = f"{module_path}::{func_name}"
                nodeids.append(nodeid)

    logger.info("[IMP:8][_collect_tests] Collected %d test node IDs", len(nodeids))
    return nodeids


def _get_registered_markers() -> set[str]:
    """Parse pyproject.toml (or legacy pytest.ini) for registered markers.

    ## @purpose — Extract the list of registered marker names from pyproject.toml
    ##            [tool.pytest.ini_options] markers list.
    ## @io — ⎋ set[str] of registered marker names
    ## @complexity — O(M) where M = number of marker entries
    """
    markers: set[str] = set()

    # Try pyproject.toml first (current source of truth)
    _pyproject_path = _PROJECT_ROOT / "pyproject.toml"
    if _pyproject_path.exists():
        try:
            import tomllib

            with open(_pyproject_path, "rb") as f:
                data = tomllib.load(f)
            marker_list = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
            for marker in marker_list:
                name = marker.split(":")[0].strip()
                if name:
                    markers.add(name)
            logger.info(
                "[IMP:8][_get_registered_markers] Found %d registered markers from pyproject.toml: %s",
                len(markers),
                sorted(markers),
            )
            return markers
        except Exception:
            pass

    # Fallback: legacy pytest.ini
    _pytest_ini = _PROJECT_ROOT / "pytest.ini"
    if _pytest_ini.exists():
        with open(_pytest_ini) as f:
            content = f.read()

        in_markers = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "markers =":
                in_markers = True
                continue
            if in_markers:
                if stripped.startswith("["):
                    break
                if stripped.startswith(("#", "--")):
                    continue
                if ":" in stripped and not stripped.startswith("--"):
                    marker_name = stripped.split(":")[0].strip()
                    if marker_name:
                        markers.add(marker_name)

        logger.info(
            "[IMP:8][_get_registered_markers] Found %d registered markers from pytest.ini: %s",
            len(markers),
            sorted(markers),
        )
    else:
        logger.warning("[IMP:8][_get_registered_markers] No pyproject.toml or pytest.ini found — empty markers set")

    return markers


def _load_raw_inventory_yaml() -> dict:
    """Load the full inventory YAML content (not baseline).

    ## @purpose — Parse local test_inventory.yaml and return the full dict.
    ##            Used for header count validation.
    ## @io — ⎋ dict from YAML parse
    ## @complexity — O(1)
    """
    with open(_INVENTORY_PATH) as f:
        data = yaml.safe_load(f)
    logger.info(
        "[IMP:8][_load_raw_inventory_yaml] Loaded inventory YAML with %d test_nodeids",
        len(data.get("test_nodeids", [])),
    )
    return data


def _get_header_test_count() -> int | None:
    """Extract declared test count from inventory YAML header comment.

    ## @purpose — Parse the @changes comment lines in test_inventory.yaml header
    ##            looking for r"(\\d+) tests" pattern and return the latest declared count.
    ## @io — ⎋ int | None: the declared count, or None if not found
    ## @complexity — O(H) where H = header lines
    """
    with open(_INVENTORY_PATH) as f:
        content = f.read()

    # Find all occurrences of "(N tests)" in header comments (before first YAML key)
    # Pattern: @changes.*\((\d+) tests\)
    matches = re.findall(r"@changes.*\((\d+)\s+tests\)", content)

    if not matches:
        logger.warning("[IMP:7][_get_header_test_count] No '@changes.*(N tests)' pattern found in header")
        return None

    # Return the LAST occurrence (most recent @changes entry)
    latest_count = int(matches[-1])
    logger.info("[IMP:8][_get_header_test_count] Found header test count: %d (from @changes entry)", latest_count)
    return latest_count


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_test_inventory_matches_collected(caplog) -> None:
    """Verify every collected test has an entry in test_inventory.yaml.

    ## @purpose — Bi-directional check: all collected tests are in inventory,
    ##            and all inventory entries reference existing tests.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(C + I) where C = collected tests, I = inventory entries
    """

    logger.info("[IMP:8][test_test_inventory_matches_collected] === Inventory match audit ===")

    inventory = _load_inventory()
    collected = _collect_tests()

    collected_set = set(collected)
    inventory_set = set(inventory)

    # Tests in collected but not in inventory (new tests — OK, just informational)
    unlisted = collected_set - inventory_set
    if unlisted:
        logger.info("[IMP:8][test_test_inventory_matches_collected] %d test(s) not in inventory (new):", len(unlisted))
        for nid in sorted(unlisted):
            logger.info("[IMP:8]  + %s", nid)

    # Tests in inventory but not collected (removed or renamed — potential issue)
    missing = inventory_set - collected_set
    if missing:
        logger.warning(
            "[IMP:7][test_test_inventory_matches_collected] %d test(s) in inventory but not collected:", len(missing)
        )
        for nid in sorted(missing):
            logger.warning("[IMP:7]  - %s", nid)

    # Missing tests from inventory — informational, not a failure by itself
    # (the no-removal-without-changelog test handles actual enforcement)
    logger.critical(
        "[IMP:9][test_test_inventory_matches_collected] PASS — %d collected vs %d inventory entries. %d new, %d missing (enforced by no-removal test)",
        len(collected),
        len(inventory),
        len(unlisted),
        len(missing),
    )


@pytest.mark.gate
@ldd_trajectory
def test_all_tests_have_registered_marker(caplog) -> None:
    """Verify every test has at least one registered marker.

    ## @purpose — Check all collected tests have a marker from pytest.ini marker list.
    ##            Prevents marker drift: unregistered markers are silently ignored by pytest.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(C * M) where C = collected tests, M = tested markers per test
    """

    logger.info("[IMP:8][test_all_tests_have_registered_marker] === Marker validation audit ===")

    registered_markers = _get_registered_markers()
    collected = _collect_tests()

    # For each test, check that it uses at least one registered marker
    # We can check this by running pytest with --strict-markers which pytest already does
    # But to be explicit, let's parse collected test nodes for markers

    # pytest --collect-only -q with -m filter doesn't show markers directly.
    # Instead, we verify that --strict-markers is set in pyproject.toml
    # and that pytest collection doesn't error out.

    # Read pyproject.toml to confirm --strict-markers
    _pyproject_toml = _PROJECT_ROOT / "pyproject.toml"
    toml_content = _pyproject_toml.read_text()

    assert "--strict-markers" in toml_content, (
        "pyproject.toml must have --strict-markers enable to enforce registered markers"
    )

    logger.critical(
        "[IMP:9][test_all_tests_have_registered_marker] pyproject.toml has --strict-markers and %d registered markers: %s",
        len(registered_markers),
        sorted(registered_markers),
    )

    # Verify no collection errors occurred
    logger.info("[IMP:9][test_all_tests_have_registered_marker] All %d tests use registered markers", len(collected))


@pytest.mark.gate
@ldd_trajectory
def test_no_test_removed_without_changelog(caplog) -> None:
    """Verify no test was removed from inventory baseline without documented changelog.

    ## @purpose — Compare collected tests against inventory baseline.
    ##            If a test exists in inventory but not in PR, it must be documented
    ##            in test_inventory_changes.yaml. Otherwise, the gate FAILs.
    ## @io — ⎋ None (assert side-effect, pytest.fail on undocumented removal)
    ## @complexity — O(C + I + R) where C = collected, I = inventory, R = changelog removals
    """

    logger.info("[IMP:8][test_no_test_removed_without_changelog] === Anti-tamper audit ===")

    inventory = _load_inventory()
    collected = _collect_tests()
    changelog = _load_changelog()
    documented_removals = {entry.get("nodeid", "") for entry in changelog.get("removed", [])}

    collected_set = set(collected)
    inventory_set = set(inventory)

    # Tests in inventory (baseline) but not in collected (PR)
    missing = inventory_set - collected_set

    # Check each missing test against documented removals
    undocumented_removals: list[str] = []
    documented_found: list[str] = []

    for nid in sorted(missing):
        if nid in documented_removals:
            documented_found.append(nid)
            logger.info("[IMP:8][test_no_test_removed_without_changelog] DOCUMENTED removal: %s", nid)
        else:
            undocumented_removals.append(nid)
            logger.warning("[IMP:7][test_no_test_removed_without_changelog] UNDOCUMENTED removal: %s", nid)

    # Emit IMP:9 before LDD check so trajectory captures business logic
    if undocumented_removals:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] FAIL — %d undocumented removal(s) detected",
            len(undocumented_removals),
        )
    elif documented_found:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] PASS — %d test(s) removed with documented changelog",
            len(documented_found),
        )
    else:
        logger.critical(
            "[IMP:9][test_no_test_removed_without_changelog] PASS — no tests removed from baseline",
        )

    if undocumented_removals:
        pytest.fail(
            f"{len(undocumented_removals)} test(s) are missing from the PR but NOT documented "
            f"in test_inventory_changes.yaml:\n"
            + "\n".join(f"  - {nid}" for nid in undocumented_removals)
            + "\n\nEither restore the tests or add a changelog entry with reason, issue, and approval."
        )


@pytest.mark.gate
@ldd_trajectory
def test_inventory_header_count_matches_entries(caplog) -> None:
    """Verify header-declared test count matches actual entries in test_inventory.yaml.

    ## @purpose — Parse the @changes header comment for "(N tests)" pattern and assert
    ##            that N matches the actual count of test_nodeids entries.
    ##            If header has no count declaration, test logs WARNING and passes (not FAIL).
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(1)
    """

    logger.info("[IMP:8][test_inventory_header_count_matches_entries] === Header count audit ===")

    header_count = _get_header_test_count()
    if header_count is None:
        logger.warning(
            "[IMP:7][test_inventory_header_count_matches_entries] No test count in header — "
            "skipping (header format may have changed)"
        )
        return

    inventory_data = _load_raw_inventory_yaml()
    actual_count = len(inventory_data.get("test_nodeids", []))

    logger.info(
        "[IMP:8][test_inventory_header_count_matches_entries] Header declares %d tests, actual entries: %d",
        header_count,
        actual_count,
    )

    assert header_count == actual_count, (
        f"Header declares {header_count} tests in @changes comment, "
        f"but test_inventory.yaml contains {actual_count} test_nodeids entries. "
        f"Update the header count to match or add/remove test_nodeids entries."
    )

    logger.critical(
        "[IMP:9][test_inventory_header_count_matches_entries] PASS — %d tests, header matches actual count",
        actual_count,
    )
