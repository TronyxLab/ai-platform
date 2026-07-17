# GREP_SUMMARY: sync-inventory, test-inventory, pytest-collect, nodeid, regeneration, idempotent
# STRUCTURE: ▶ collect_tests → ⚡ subprocess pytest --collect-only → ○ parse <Dir>/<Package>/<Module>/<Function> tree → ⊕ sorted(nodeids) → ▶ sync_inventory → ◇ read header → ◇ replace @changes count → ◇ serialize test_nodeids: list → ⎋ write if changed
# region MODULE_CONTRACT
## @purpose — Regenerate tests/test_inventory.yaml from pytest --collect-only. Provides both a CLI
##            entry point (`python tests/tools/sync_inventory.py`) and a native Python API
##            (`collect_tests()`, `sync_inventory()`) importable by tests.
## @scope — Parses pytest 9.x XML-like --collect-only -q output, extracts node IDs using the same
##          <Dir>/<Package>/<Module>/<Function> tree parser as the inventory gate, produces a
##          byte-compatible YAML file with manually serialized - <nodeid> list and an up-to-date
##          @changes header count. Idempotent: second run with same tests = no diff.
## @invariants
##   - Node ID collection logic EXACTLY replicates tests/gates/test_gate_test_inventory.py::_collect_tests()
##   - YAML test_nodeids list is serialized via f-strings, NOT yaml.dump (byte-identical - <nodeid> format)
##   - Exactly one @changes line with (N tests) exists in the header; all stale count lines removed
##   - The @changes line is dated today and carries the correct test count
##   - Second run with no test changes produces byte-identical output (idempotent)
##   - Non-count @changes lines (historical entries) are preserved
##   - The tool reads, compares, and only writes if content changed (idempotency by diff)
## @rationale — Closes tech debt: previously test_inventory.yaml was manually maintained (587 lines).
##              This tool automates regeneration so the inventory gate stays green without manual sync.
##              The collection logic is replicated (not imported) to keep the gate anti-tamper invariant.
## @changes — 2026-07-15 | Created per DevPlan 008 T0
# endregion MODULE_CONTRACT

import logging
import pathlib
import re
import subprocess
import sys
from datetime import date

# ── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
"""Project root, computed from tests/tools/sync_inventory.py location."""
_INVENTORY_PATH: pathlib.Path = _PROJECT_ROOT / "tests" / "test_inventory.yaml"
"""Path to the inventory YAML file."""


# region FUNC_pop_to_indent
## @purpose — Pop from path/indent stacks until indent matches the current level
## @io — ⇥ path_stack, indent_stack, indent → ⎋ None (mutates stacks in place)
## @complexity — O(D) where D = depth of tag nesting
def _pop_to_indent(path_stack: list[str], indent_stack: list[int], indent: int) -> None:
    """Pop stacks until indent matches the current level (gate-compatible parser).

    ▶ stacks → ◇ while indent_stack[-1] >= indent → ∋ pop both → ⎋ void
    """
    # TRAP[DECISION] · 2026-07-15 · — · Gate-compatible tree parser · Rejected: regex-based nodeid extraction · Reason: must exactly replicate _collect_tests() from test_gate_test_inventory.py for bijection · Rev: pytest --collect-only -q output format changes
    while indent_stack and indent_stack[-1] >= indent:
        indent_stack.pop()
        path_stack.pop()


# endregion FUNC_pop_to_indent


# region FUNC_collect_tests
## @purpose — Run pytest --collect-only and return sorted list of test node IDs.
##            Replicates the exact tree-parsing logic from test_gate_test_inventory.py::_collect_tests().
## @io — ⇥ project_root (optional overrides _PROJECT_ROOT) → ⎋ list[str] of sorted node IDs
## @complexity — O(T) where T = total test count
def collect_tests(project_root: pathlib.Path | None = None) -> list[str]:
    """Run pytest --collect-only -q and return sorted list of test node IDs.

    ⚡ subprocess(pytest --collect-only -q) → ○ parse <Dir>/<Package>/<Module>/<Function> tree
    → ⊕ sorted(nodeids) → ⎋ nodeids

    ## @purpose — Implements the authoritative collection logic matching the inventory gate.
    ## @io — ⇥ project_root: Optional[Path] (default _PROJECT_ROOT) → ⎋ list[str] sorted node IDs
    ## @complexity — O(T)
    ## @invariants
    ##   - tree parser matches tests/gates/test_gate_test_inventory.py::_collect_tests byte-for-byte
    ##   - root Dir (index 0) is stripped from node IDs
    ##   - Returned list is sorted alphabetically
    """
    root: pathlib.Path = project_root or _PROJECT_ROOT
    logger.info("[IMP:7][collect_tests] Running pytest --collect-only in %s", root)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=str(root),
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
            if len(path_stack) >= 2:
                module_path = "/".join(path_stack[1:])
                nodeid = f"{module_path}::{func_name}"
                nodeids.append(nodeid)

    nodeids.sort()
    logger.info("[IMP:9][collect_tests] Collected %d test node IDs", len(nodeids))
    return nodeids


# endregion FUNC_collect_tests


# region FUNC_sync_inventory
## @purpose — Read current tests/test_inventory.yaml, regenerate with current collected node IDs,
##            and write back only if content changed (idempotent).
## @io — ⇥ project_root: Optional[Path], inventory_path: Optional[Path] → ⎋ bool (True if file was changed)
## @complexity — O(T + L) where T = test count, L = header lines
## @invariants
##   - Exactly ONE @changes line with (N tests) exists after sync
##   - Non-count @changes lines are preserved
##   - If old content == new content → no write (idempotent by diff)
def sync_inventory(
    project_root: pathlib.Path | None = None,
    inventory_path: pathlib.Path | None = None,
) -> bool:
    """Regenerate test_inventory.yaml from current pytest collection.

    ▶ read current file → ◇ collect_tests() → ◇ replace @changes count line + nodeid list
    → ◇ old == new? → (yes) ⎋ False | (no) write → ⎋ True

    ## @purpose — Idempotent inventory regeneration: compares old vs new content before write.
    ## @io — ⇥ project_root: Optional[Path], inventory_path: Optional[Path] → ⎋ bool (True if file was written)
    ## @complexity — O(T + L)
    ## @rationale — Diff-based idempotency avoids unnecessary writes and git diffs.
    ##              Manual YAML serialization (f-strings) guarantees byte-identical - <nodeid> format.
    ##              inventory_path override exists for testability (tmp_path injection).
    ## @invariants
    ##   - Only writes if new content != old content
    ##   - Header @changes count line is always updated to reflect actual len(nodeids)
    ##   - Stale @changes count lines are removed
    ##   - Historical non-count @changes lines preserved
    """
    root: pathlib.Path = project_root or _PROJECT_ROOT
    if inventory_path is None:
        inventory_path = root / "tests" / "test_inventory.yaml"
    today: str = date.today().isoformat()

    # Read current content
    if inventory_path.exists():
        old_content: str = inventory_path.read_text()
    else:
        old_content = ""
    lines: list[str] = old_content.splitlines(keepends=True)

    # Collect current node IDs
    nodeids: list[str] = collect_tests(project_root=root)

    # Build new content
    new_lines: list[str] = []
    changes_replaced: bool = False
    test_nodeids_found: bool = False
    endregion_pos: int | None = None

    for _i, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip("\r")

        # Track # endregion position for possible insertion
        if stripped.startswith("# endregion MODULE_CONTRACT"):
            endregion_pos = len(new_lines)

        if stripped == "test_nodeids:":
            # Found the test_nodeids key — replace everything from here
            test_nodeids_found = True
            new_lines.append("test_nodeids:\n")
            new_lines.extend(f"- {nid}\n" for nid in nodeids)
            # Skip remaining old lines (the old list)
            break

        if re.search(r"@changes.*\(\d+\s+tests\)", stripped):
            # Replace stale @changes count line with today's authoritative line
            new_line: str = f"## @changes — {today} | regenerated ({len(nodeids)} tests)\n"
            new_lines.append(new_line)
            changes_replaced = True
            logger.info(
                "[IMP:9][sync_inventory] Replaced @changes count line: %s tests",
                len(nodeids),
            )
        else:
            new_lines.append(line)

    # If we never found test_nodeids:, append it
    if not test_nodeids_found:
        # Ensure trailing newline before appending section
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append("test_nodeids:\n")
        new_lines.extend(f"- {nid}\n" for nid in nodeids)

    # If no @changes count line was found and replaced, insert one before # endregion
    if not changes_replaced:
        insert_line: str = f"## @changes — {today} | regenerated ({len(nodeids)} tests)\n"
        if endregion_pos is not None:
            new_lines.insert(endregion_pos, insert_line)
        else:
            # Fallback: append before test_nodeids (which was just appended if not found)
            new_lines.append(insert_line)
        logger.info(
            "[IMP:9][sync_inventory] Inserted @changes count line: %s tests",
            len(nodeids),
        )

    new_content: str = "".join(new_lines)

    # Idempotency: only write if content differs
    if new_content == old_content:
        logger.info(
            "[IMP:9][sync_inventory] No changes — %d tests, file is current",
            len(nodeids),
        )
        return False

    # Ensure directory exists
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(new_content)
    logger.info(
        "[IMP:9][sync_inventory] Regenerated test_inventory.yaml — %d tests",
        len(nodeids),
    )
    print(f"[IMP:9][sync_inventory] Regenerated test_inventory.yaml — {len(nodeids)} tests")
    return True


# endregion FUNC_sync_inventory


# region FUNC_main
## @purpose — CLI entry point: run sync_inventory() with project root detection
## @io — ⇥ sys.argv (unused) → ⎋ exit code 0 on success, 1 on error
def main() -> None:
    """CLI entry point for `python tests/tools/sync_inventory.py`.

    ▶ detect project root → ◇ sync_inventory() → ◇ (changed?) → print result → ⎋ exit 0
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[IMP:%(levelno)s][sync_inventory] %(message)s",
    )

    try:
        changed: bool = sync_inventory()
        if changed:
            print("[IMP:9][sync_inventory] File was updated. Run `git diff tests/test_inventory.yaml` to see changes.")
        else:
            print("[IMP:9][sync_inventory] No changes needed — file is already up to date.")
    except Exception as exc:
        logger.error("[IMP:9][sync_inventory] FAILED: %s", exc)
        print(f"[IMP:9][sync_inventory] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


# endregion FUNC_main


if __name__ == "__main__":
    main()
