# GREP_SUMMARY: gate fixture-schema test-data validation coherence jsonschema
# STRUCTURE: ▶ test_all_test_fixtures_match_schemas → ◇ call _validate_test_fixtures() → ⊕ verify LDD [IMP:9] trajectory → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate test: validate all test_data/*.yaml fixtures against their jsonschema schemas.
##           Ensures test fixtures stay coherent with evolving platform schemas.
## @scope    All .yaml fixtures in tests/test_data/ checked against core/schemas/.
##           Mapping defined in _FIXTURE_SCHEMA_MAP (tests/_conftest/session.py).
## @invariants
##   - Every fixture in _FIXTURE_SCHEMA_MAP must validate against its schema
##   - Test must NOT pass (anti-R1) — calls _validate_test_fixtures() explicitly
##   - Registered in core/entrypoint-manifest.yaml gates section
## @rationale Prevents silent fixture drift. Without this gate, a developer can change
##            node.schema.json without updating test_data/node.yaml — and the only
##            signal is cryptic test failures (F3-F5 in Brief 026). The gate makes
##            the failure explicit and localized.
## @changes 2026-07-21 | Created (DevPlan 026 W2)
# endregion MODULE_CONTRACT
import pytest
from _conftest.session import _FIXTURE_SCHEMA_MAP, _validate_test_fixtures


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-21 · Fixture-schema coherence gate
# · Regression: after schema evolution (node.schema.json adds required fields),
#   test_data/*.yaml fixtures must be updated or gate catches the drift.
# · Scenario: _validate_test_fixtures() is called explicitly — same function
#   used by pytest_sessionstart. Empty _FIXTURE_SCHEMA_MAP is valid.
# · Last fail: N/A (new gate)
# · Remove if: fixture validation mechanism changes fundamentally
def test_all_test_fixtures_match_schemas() -> None:
    """Gate: every test_data/*.yaml fixture must validate against its schema.

    Calls _validate_test_fixtures() — same function used by pytest_sessionstart.
    If fixtures are invalid, pytest.exit is raised (caught by pytest as failure).

    Empty _FIXTURE_SCHEMA_MAP is a valid state (no fixtures to validate).
    The test implicitly validates that the function itself runs without error.
    """
    # Verify the map is importable (configuration check)
    assert isinstance(_FIXTURE_SCHEMA_MAP, dict), (
        f"[IMP:9][gate][fixture-schema] _FIXTURE_SCHEMA_MAP must be dict, got {type(_FIXTURE_SCHEMA_MAP).__name__}"
    )

    # Run validation — pytest.exit on failure, no return value on success
    # _validate_test_fixtures() either completes (fixtures valid) or raises
    # pytest.exit (invalid fixtures) — reaching this point is the assertion.
    # R1 (B10 T1): trailing `assert True` removed — it was a pass-assert.
    _validate_test_fixtures()
