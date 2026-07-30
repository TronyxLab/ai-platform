"""
# GREP_SUMMARY: test_generate_agents_md, generate_canon_table, generate_forbidden_lists, inject_into_md, tmp_path
# STRUCTURE: ▶ generate_canon_table 2× (all-sections/empty) → ▶ generate_forbidden_lists 2× (full/empty) → ▶ inject_into_md 3× (replace/missing-markers/no-file) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for generate_agents_md.py — generate_canon_table(), generate_forbidden_lists(),
##           inject_into_md(). No subprocess calls.
## @scope    Tests Markdown table generation, forbidden list generation, and marker-based content injection.
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for temp file creation
## @rationale DevPlan 051 §5: Unit coverage for generate_agents_md generator
## @changes 2026-07-22 | Created (DevPlan 051 Wave 3)
# endregion MODULE_CONTRACT
"""

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import generate_agents_md as gam

# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_canon_table
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_canon_table produces rows for all canonical sections
# · Scenario: Manifest with targets across deploy, bootstrap, build → table rows for each
# · Last fail: N/A (new test)
# · Remove if: generate_canon_table logic changes
@ldd_trajectory
def test_generate_canon_table(caplog):
    """generate_canon_table should produce table rows for all canonical sections."""
    manifest = {
        "deploy": [
            {
                "make_target": "deploy",
                "mechanism": "git-push",
                "description": "Deploy a project via git push",
                "signature": "make deploy PROJECT=<dir>",
                "operation_ru": "Деплой проекта через git push",
            },
        ],
        "bootstrap": [
            {
                "make_target": "bootstrap-node",
                "mechanism": "ssh+rsync",
                "description": "Idempotent bootstrap of a new node",
                "signature": "make bootstrap-node NODE=<name>",
                "operation_ru": "Идемпотентный bootstrap ноды",
            },
        ],
        "build": [
            {
                "make_target": "hermes-build-platform",
                "mechanism": "docker-build",
                "description": "Build L1 locally",
                "signature": "make hermes-build-platform",
                "operation_ru": "Сборка L1 локально",
            },
        ],
    }

    result = gam.generate_canon_table(manifest)

    assert "make deploy" in result, "Should contain deploy target"
    assert "make bootstrap-node" in result, "Should contain bootstrap-node target"
    assert "make hermes-build-platform" in result, "Should contain hermes-build-platform target"
    assert "Деплой проекта" in result, "Should contain operation_ru for deploy"
    assert "Идемпотентный bootstrap" in result, "Should contain operation_ru for bootstrap"
    assert "Сборка L1 локально" in result, "Should contain operation_ru field"

    lines = result.strip().split("\n")
    assert len(lines) == 3, f"Expected 3 table rows, got {len(lines)}"

    logger.critical("[IMP:9][test] generate_canon_table produced %d rows", len(lines))


# 🧪 TRAP[TEST] · Regression · Empty manifest produces empty string
# · Scenario: Empty manifest dict → returns empty string
# · Last fail: N/A (new test)
# · Remove if: generate_canon_table logic changes
@ldd_trajectory
def test_generate_canon_table_empty(caplog):
    """generate_canon_table should return empty string for empty manifest."""
    result = gam.generate_canon_table({})
    assert result == "", f"Expected empty string, got {result!r}"

    logger.critical("[IMP:9][test] generate_canon_table empty manifest returns ''")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generate_forbidden_lists
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · generate_forbidden_lists produces organized markdown sections
# · Scenario: Manifest with forbidden_directories, forbidden_scripts, forbidden_verbs → 3 sections
# · Last fail: N/A (new test)
# · Remove if: generate_forbidden_lists logic changes
@ldd_trajectory
def test_generate_forbidden_lists(caplog):
    """generate_forbidden_lists should produce forbidden sections for all present categories."""
    manifest = {
        "forbidden_directories": ["core/scripts/e2e", "core/scripts"],
        "forbidden_scripts": ["dev.sh", "platform-push.sh"],
        "forbidden_verbs": ["push-core", "deploy-node"],
    }

    result = gam.generate_forbidden_lists(manifest)

    assert "Запрещённые директории" in result, "Should have directories header"
    assert "Запрещённые скрипты" in result, "Should have scripts header"
    assert "Запрещённые глаголы" in result, "Should have verbs header"
    assert "`core/scripts/e2e`" in result, "Should list forbidden directory"
    assert "dev.sh" in result, "Should list forbidden script"
    assert "`push-core`" in result, "Should list forbidden verb"

    logger.critical("[IMP:9][test] generate_forbidden_lists produced content with %d chars", len(result))


# 🧪 TRAP[TEST] · Regression · Empty forbidden sections produce no output
# · Scenario: Manifest with no forbidden_* keys → returns empty string
# · Last fail: N/A (new test)
# · Remove if: generate_forbidden_lists logic changes
@ldd_trajectory
def test_generate_forbidden_lists_empty(caplog):
    """generate_forbidden_lists should return empty string if no forbidden sections defined."""
    manifest = {"allowed_verbs": ["deploy"], "gates": []}
    result = gam.generate_forbidden_lists(manifest)
    assert result == "", f"Expected empty string, got {result!r}"

    logger.critical("[IMP:9][test] generate_forbidden_lists empty returns ''")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: inject_into_md
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · inject_into_md replaces content between existing markers
# · Scenario: File with START and END markers → replaces content between them, preserves markers
# · Last fail: N/A (new test)
# · Remove if: inject_into_md logic changes
@ldd_trajectory
def test_inject_into_md_replaces_content(caplog, tmp_path):
    """inject_into_md should replace content between existing markers."""
    md_file = tmp_path / "test.md"
    md_file.write_text(
        textwrap.dedent("""\
        # Header
        Some text before
        <!-- GENERATED:START:canon-operations -->
        | old | content |
        <!-- GENERATED:END:canon-operations -->
        Footer text
    """)
    )

    new_content = "| `make deploy` | Deploy project |"
    gam.inject_into_md(str(md_file), "canon-operations", new_content)

    result = md_file.read_text()
    assert "<!-- GENERATED:START:canon-operations -->" in result, "Start marker should be preserved"
    assert "<!-- GENERATED:END:canon-operations -->" in result, "End marker should be preserved"
    assert "| `make deploy` | Deploy project |" in result, "New content should be injected"
    assert "| old | content |" not in result, "Old content should be replaced"
    assert "Some text before" in result, "Text before marker should be preserved"
    assert "Footer text" in result, "Text after marker should be preserved"

    logger.critical("[IMP:9][test] inject_into_md replaced content between markers")


# 🧪 TRAP[TEST] · Regression · Missing markers results in appended content
# · Scenario: File without markers → markers + content appended at end
# · Last fail: N/A (new test)
# · Remove if: inject_into_md logic changes
@ldd_trajectory
def test_inject_into_md_appends_when_no_markers(caplog, tmp_path):
    """inject_into_md should append markers and content when no markers exist."""
    md_file = tmp_path / "test.md"
    md_file.write_text("# Header\nExisting content\n")

    new_content = "| `make deploy` | Deploy |"
    gam.inject_into_md(str(md_file), "canon-operations", new_content)

    result = md_file.read_text()
    assert "<!-- GENERATED:START:canon-operations -->" in result, "Start marker should be added"
    assert "<!-- GENERATED:END:canon-operations -->" in result, "End marker should be added"
    assert "| `make deploy` | Deploy |" in result, "Content should be in result"
    assert "Existing content" in result, "Original content should be preserved"

    logger.critical("[IMP:9][test] inject_into_md appended content when no markers found")


# 🧪 TRAP[TEST] · Regression · Missing file raises FileNotFoundError
# · Scenario: Non-existent file path → FileNotFoundError raised
# · Last fail: N/A (new test)
# · Remove if: inject_into_md logic changes
@ldd_trajectory
def test_inject_into_md_missing_file(caplog):
    """inject_into_md should raise FileNotFoundError for missing file."""
    with pytest.raises(FileNotFoundError):
        gam.inject_into_md("/tmp/nonexistent_test_file.md", "test", "content")

    logger.critical("[IMP:9][test] inject_into_md raises FileNotFoundError for missing file")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _inject_content (string-based, --check mode)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _inject_content replaces content between existing markers
# · Scenario: String with START/END markers → replaces content, preserves markers
# · Last fail: N/A (new test)
# · Remove if: _inject_content logic changes
@ldd_trajectory
def test_inject_content_replaces(caplog):
    """_inject_content should replace content between existing markers."""
    original = textwrap.dedent("""\
        # Header
        Before
        <!-- GENERATED:START:canon-operations -->
        | old | content |
        <!-- GENERATED:END:canon-operations -->
        After
    """)
    new_content = "| `make deploy` | Deploy project |"

    result = gam._inject_content(original, "canon-operations", new_content)

    assert "<!-- GENERATED:START:canon-operations -->" in result, "Start marker preserved"
    assert "<!-- GENERATED:END:canon-operations -->" in result, "End marker preserved"
    assert "| `make deploy` | Deploy project |" in result, "New content present"
    assert "| old | content |" not in result, "Old content replaced"
    assert "Before" in result, "Text before marker preserved"
    assert "After" in result, "Text after marker preserved"

    logger.critical("[IMP:9][test] _inject_content replaced content between markers")


# 🧪 TRAP[TEST] · Regression · _inject_content appends markers when none exist
# · Scenario: String without markers → markers + content appended at end
# · Last fail: N/A (new test)
# · Remove if: _inject_content logic changes
@ldd_trajectory
def test_inject_content_appends(caplog):
    """_inject_content should append markers and content when no markers exist."""
    original = "# Header\nExisting content\n"
    new_content = "| `make test` | Test |"

    result = gam._inject_content(original, "test-marker", new_content)

    assert "<!-- GENERATED:START:test-marker -->" in result, "Start marker added"
    assert "<!-- GENERATED:END:test-marker -->" in result, "End marker added"
    assert "| `make test` | Test |" in result, "Content added"
    assert "Existing content" in result, "Original content preserved"

    logger.critical("[IMP:9][test] _inject_content appended markers when none found")


# 🧪 TRAP[TEST] · Regression · _inject_content handles multiple markers independently
# · Scenario: String with two different marker pairs → both replaced correctly
# · Last fail: N/A (new test)
# · Remove if: _inject_content logic changes
@ldd_trajectory
def test_inject_content_multiple_markers(caplog):
    """_inject_content should handle multiple marker pairs independently."""
    original = textwrap.dedent("""\
        # Header
        <!-- GENERATED:START:canon -->
        | old | canon |
        <!-- GENERATED:END:canon -->
        Middle
        <!-- GENERATED:START:forbidden -->
        - old_script
        <!-- GENERATED:END:forbidden -->
        Footer
    """)
    new_canon = "| `make deploy` | Deploy |"
    new_forbidden = "- dev.sh"

    # Apply both injections sequentially
    result = gam._inject_content(original, "canon", new_canon)
    result = gam._inject_content(result, "forbidden", new_forbidden)

    assert "| `make deploy` | Deploy |" in result, "Canon content replaced"
    assert "- dev.sh" in result, "Forbidden content replaced"
    assert "| old | canon |" not in result, "Old canon content removed"
    assert "- old_script" not in result, "Old forbidden content removed"
    assert "Middle" in result, "Text between sections preserved"
    assert "Footer" in result, "Text after preserved"

    logger.critical("[IMP:9][test] _inject_content handles multiple markers independently")


# endregion
