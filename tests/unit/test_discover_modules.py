"""
# GREP_SUMMARY: test_discover_modules, idempotency, update_compose_include, compose, include-section
# STRUCTURE: ▶ tmp_path + docker-compose.yml stub → ◇ update_compose_include 3× (changed/idempotent/preserves) → ⎋ assert bool + section integrity
# region MODULE_CONTRACT
## @purpose  Unit tests for update_compose_include() idempotency and section preservation
## @scope    Direct Python import of discover_modules.py; tests compose include update logic
## @invariants
##   - update_compose_include only modifies the include: section
##   - networks:, volumes: sections are preserved verbatim
##   - Second call with same modules returns False (idempotent)
##   - Third call with additional modules returns True (changed)
## @rationale Pure Python function without side effects — direct import is idiomatic and faster than subprocess
## @changes
##   2026-07-15 · Created (GAP-002 remediation)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# Direct import of the pure Python function
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap"))
from discover_modules import update_compose_include

SAMPLE_COMPOSE = """networks:
  platform:
    external: true

volumes:
  postgres-data:

include:
  - path: core/modules/postgres/docker-compose.base.yml
  - path: core/modules/redis/docker-compose.base.yml
"""


# region FUNC_temp_compose_file
## @purpose  Create a temporary docker-compose.yml with known include section
## @io       tmp_path → Path
@pytest.fixture
def temp_compose_file(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(SAMPLE_COMPOSE)
    return compose


# endregion FUNC_temp_compose_file


# region FUNC_test_update_compose_include_changed
## @purpose  First call with new modules returns True and updates file content
## @io       temp_compose_file → assert True + litellm in content + other sections preserved
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: update_compose_include with new modules returns True · Last fail: N/A · Remove if: update_compose_include behavior changed
@ldd_trajectory
def test_update_compose_include_changed(temp_compose_file, caplog):
    """First call with new modules should return True and update the file."""
    new_modules = [
        "core/modules/postgres/docker-compose.base.yml",
        "core/modules/redis/docker-compose.base.yml",
        "core/modules/litellm/docker-compose.base.yml",
    ]
    result = update_compose_include(temp_compose_file, new_modules)
    logger.info("[IMP:9][unit][discover] update_compose_include with %d modules → result=%s", len(new_modules), result)
    assert result is True, "Expected True when adding new modules"

    content = temp_compose_file.read_text()
    assert "litellm" in content, "litellm module not found in updated content"
    logger.info("[IMP:9][unit][discover] litellm found in updated content ✓")
    # Verify networks/volumes sections are preserved
    assert "networks:" in content
    assert "volumes:" in content
    logger.info("[IMP:9][unit][discover] networks/volumes sections preserved ✓")


# endregion FUNC_test_update_compose_include_changed


# region FUNC_test_update_compose_include_idempotent
## @purpose  Second call with same modules returns False (no-op)
## @io       temp_compose_file → both calls return False (fixture already in correct format)
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: update_compose_include with same modules 2× returns False · Last fail: N/A · Remove if: update_compose_include behavior changed
@ldd_trajectory
def test_update_compose_include_idempotent(temp_compose_file, caplog):
    """Multiple calls with the same modules should return False (no change, idempotent)."""
    modules = [
        "core/modules/postgres/docker-compose.base.yml",
        "core/modules/redis/docker-compose.base.yml",
    ]
    # SAMPLE_COMPOSE now uses the same 2-space indent as generation output,
    # so both calls return False (content already matches generated format)
    result1 = update_compose_include(temp_compose_file, modules)
    logger.info("[IMP:9][unit][discover] update_compose_include call 1 → %s (expected False)", result1)
    assert result1 is False, "First call should return False (fixture already in correct format)"

    # Second call: already correct format → no change
    result2 = update_compose_include(temp_compose_file, modules)
    logger.info("[IMP:9][unit][discover] update_compose_include call 2 → %s (expected False — idempotent)", result2)
    assert result2 is False, "Second call should return False (idempotent)"
    logger.info("[IMP:9][unit][discover] update_compose_include idempotent ✓")


# endregion FUNC_test_update_compose_include_idempotent


# region FUNC_test_update_compose_include_preserves_other_sections
## @purpose  After include update, networks/volumes sections remain untouched
## @io       temp_compose_file → assert networks + volumes + specific keys
## @complexity 1
# 🧪 TRAP[TEST] · Regression · Scenario: update preserves networks/volumes sections · Last fail: N/A · Remove if: update_compose_include behavior changed
@ldd_trajectory
def test_update_compose_include_preserves_other_sections(temp_compose_file, caplog):
    """After updating include section, networks and volumes should be untouched."""
    modules = ["core/modules/postgres/docker-compose.base.yml"]
    update_compose_include(temp_compose_file, modules)

    content = temp_compose_file.read_text()
    assert "networks:" in content
    assert "  platform:" in content
    assert "    external: true" in content
    assert "volumes:" in content
    assert "  postgres-data:" in content
    logger.info("[IMP:9][unit][discover] include update preserves networks/volumes sections ✓")


# endregion FUNC_test_update_compose_include_preserves_other_sections
