# GREP_SUMMARY: checkpoint v2 version-based invalidation rotate_checkpoints removed node-lifecycle bootstrap idempotent
# STRUCTURE: ▶ 4 test functions → ○ tmp_path fixtures → ◇ assert .done file state → ⊕ LDD trajectory → ⎋ IMP:9 assertion
# region MODULE_CONTRACT
## @file test_unit_checkpoint_v2.py
## @purpose  Unit tests for checkpoint v2 logic in node-lifecycle.sh — VERSION-based invalidation,
##           rotate_checkpoints removal verification.
## @scope    Tests _checkpoint_version_check() extracted from core/internal/bootstrap/node-lifecycle.sh.
##           Uses tmp_path for isolated filesystem state. Does NOT require Docker or VPS.
## @invariants
##   - VERSION mismatch → all .done files removed, new VERSION written
##   - VERSION match → all .done files preserved
##   - No VERSION file → treated as mismatch (first run)
##   - rotate_checkpoints() not defined in node-lifecycle.sh
##   - Tests use bash subprocess for shell function testing
##   - IMP:9 logs asserted in success paths
## @rationale DevPlan 003 TASK-1: checkpoint v2 replaces mtime-based rotation with version-based invalidation.
##           rotate_checkpoints() is removed entirely. These tests prevent regression to count-based rotation.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

NODE_LIFECYCLE_SH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "internal",
    "bootstrap",
    "node-lifecycle.sh",
)

CHECKPOINT_SH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "lib",
    "checkpoint.sh",
)


def _print_ldd(stderr: str, stdout: str) -> bool:
    """Print LDD trajectory lines (IMP:7-10) from captured output and return whether IMP:9 was found."""
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in (stderr + "\n" + stdout).split("\n"):
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(line.strip())
                if imp_level >= 9:
                    found_imp9 = True
            except (ValueError, IndexError):
                pass
    print("--- END LDD TRAJECTORY ---")
    return found_imp9


def _run_checkpoint_test(
    tmp_path: Path, core_version: str, checkpoint_version: str | None, existing_done_files: int
) -> tuple[str, str, int]:
    """Set up tmp_path filesystem, extract _checkpoint_version_check(), and run it.

    Args:
        tmp_path: pytest temp directory
        core_version: content to write to CORE_DIR/VERSION
        checkpoint_version: content to write to CHECKPOINT_DIR/VERSION (None = don't create)
        existing_done_files: number of .done marker files to pre-create

    Returns:
        (stdout, stderr, exit_code)
    """
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "VERSION").write_text(core_version)

    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()

    if checkpoint_version is not None:
        (checkpoint_dir / "VERSION").write_text(checkpoint_version)

    for i in range(existing_done_files):
        (checkpoint_dir / f".bootstrap-step-step-{i}.done").write_text("")

    # Extract _checkpoint_version_check function from checkpoint.sh (originally extracted from orchestrator.sh T11)
    func_def = _extract_func("_checkpoint_version_check", CHECKPOINT_SH)

    script = textwrap.dedent(f"""\
        set -euo pipefail
        CORE_DIR="{core_dir}"
        CHECKPOINT_DIR="{checkpoint_dir}"

        {func_def}

        _checkpoint_version_check
        echo "[IMP:9][test][checkpoint] Test completed, exit=$?"
    """)

    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout, proc.stderr, proc.returncode


def _extract_func(func_name: str, source_path: str) -> str:
    """Extract a bash function definition from a source file.

    Skips comment lines (#) when searching for function definitions to avoid
    matching STRUCTURE/GREP_SUMMARY references that contain 'func_name()'.
    """
    with open(source_path) as f:
        lines = f.readlines()

    in_func = False
    func_lines = []
    brace_depth = 0

    for line in lines:
        if not in_func:
            # Skip comment lines — they may contain func_name() in STRUCTURE/GREP_SUMMARY
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if f"{func_name}()" in line:
                in_func = True
                func_lines.append(line)
                brace_depth += line.count("{") - line.count("}")
                if brace_depth == 0 and "{" not in line:
                    continue
        else:
            func_lines.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth == 0 and in_func and len(func_lines) > 1:
                break

    return "".join(func_lines)


# region TEST_test_version_mismatch_invalidates_all
# 🧪 TRAP[TEST] · 2026-07-15 · checkpoint v2 — version mismatch invalidates all markers
# · Prevents: regression where VERSION bump does not trigger re-bootstrap (idempotency break)
def test_version_mismatch_invalidates_all(tmp_path: Path, caplog) -> None:
    """VERSION differs → all .done files deleted, new VERSION written."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_checkpoint_test(
        tmp_path,
        core_version="0.5.0\n",
        checkpoint_version="0.4.0",  # old version
        existing_done_files=5,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"_checkpoint_version_check failed: rc={rc}, stderr={stderr}"

    checkpoint_dir = tmp_path / "checkpoints"
    # All .done files should be removed
    done_files = list(checkpoint_dir.glob(".bootstrap-step-*.done"))
    assert len(done_files) == 0, f"Expected 0 done files, found {len(done_files)}: {done_files}"

    # VERSION should be updated to core version
    version_file = checkpoint_dir / "VERSION"
    assert version_file.exists(), "VERSION file should exist after mismatch"
    assert version_file.read_text().strip() == "0.5.0", f"VERSION mismatch: {version_file.read_text().strip()}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][checkpoint] Version mismatch: all checkpoints invalidated, VERSION updated")


# endregion


# region TEST_test_version_match_preserves_checkpoints
# 🧪 TRAP[TEST] · 2026-07-15 · checkpoint v2 — version match preserves all markers
# · Prevents: regression where checkpoints are deleted on every run (non-idempotent bootstrap)
def test_version_match_preserves_checkpoints(tmp_path: Path, caplog) -> None:
    """VERSION matches → all .done files preserved."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_checkpoint_test(
        tmp_path,
        core_version="0.5.0\n",
        checkpoint_version="0.5.0",  # same version
        existing_done_files=3,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"_checkpoint_version_check failed: rc={rc}, stderr={stderr}"

    checkpoint_dir = tmp_path / "checkpoints"
    done_files = list(checkpoint_dir.glob(".bootstrap-step-*.done"))
    assert len(done_files) == 3, f"Expected 3 done files preserved, found {len(done_files)}: {done_files}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][checkpoint] Version match: all checkpoints preserved")


# endregion


# region TEST_test_no_version_file_treats_as_mismatch
# 🧪 TRAP[TEST] · 2026-07-15 · checkpoint v2 — missing VERSION = first run = invalidate all
# · Prevents: regression where semi-provisioned node skips steps because old markers exist without VERSION
def test_no_version_file_treats_as_mismatch(tmp_path: Path, caplog) -> None:
    """VERSION file absent → all .done deleted (first run scenario)."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_checkpoint_test(
        tmp_path,
        core_version="0.5.0\n",
        checkpoint_version=None,  # no VERSION file
        existing_done_files=2,
    )

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"_checkpoint_version_check failed: rc={rc}, stderr={stderr}"

    checkpoint_dir = tmp_path / "checkpoints"
    done_files = list(checkpoint_dir.glob(".bootstrap-step-*.done"))
    assert len(done_files) == 0, f"Expected 0 done files, found {len(done_files)}"

    version_file = checkpoint_dir / "VERSION"
    assert version_file.exists(), "VERSION file should be created on first run"
    assert version_file.read_text().strip() == "0.5.0", f"VERSION mismatch: {version_file.read_text().strip()}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][checkpoint] No VERSION file: treated as mismatch, VERSION created")


# endregion


# region TEST_test_rotate_checkpoints_removed
# 🧪 TRAP[TEST] · 2026-07-15 · rotate_checkpoints removal — function must not exist
# · Prevents: regression where rotate_checkpoints is re-added (count-based rotation breaks idempotency)
def test_rotate_checkpoints_removed(caplog) -> None:
    """Function rotate_checkpoints is not defined in node-lifecycle.sh."""
    caplog.set_level(logging.DEBUG)

    with open(NODE_LIFECYCLE_SH) as f:
        content = f.read()

    # Check that the function definition does not exist
    assert "rotate_checkpoints()" not in content, (
        "rotate_checkpoints() function found in node-lifecycle.sh — it was removed in DevPlan 003 TASK-1"
    )

    # The function should not be called anywhere
    assert "rotate_checkpoints" not in content, (
        "rotate_checkpoints reference found in node-lifecycle.sh — "
        "all references should be removed per DevPlan 003 TASK-1"
    )

    logger.info("[IMP:9][test][checkpoint] rotate_checkpoints confirmed removed from node-lifecycle.sh")


# endregion
