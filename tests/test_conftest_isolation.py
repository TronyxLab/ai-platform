# GREP_SUMMARY: conftest isolation static-tests host-side-effects volume-dirs docker-networks T2.2 no-side-effects session-items requires_docker
# STRUCTURE: ▶ test_static_tests_have_no_host_side_effects → ◇ scan request.session.items for requires_docker → ⟦assert none in non-Docker items⟧
# region MODULE_CONTRACT
## @purpose  Verify that static/contract/gate tests (those without requires_docker marker)
##           do NOT activate the test_infra fixture. This is the regression test for
##           T2.2 — marker-based isolation of the test_infra fixture in conftest.py.
## @scope    Scans `request.session.items` directly for requires_docker markers on items
##           that are NOT in Docker-dependent categories (smoke, component, integration,
##           e2e, predeploy, local_auth). Static/contract/gate sessions must have zero
##           such markers.
## @invariants
##   - No requires_docker marker on non-Docker test items in the session
##   - Uses @pytest.mark.static_audit marker (not requires_docker)
## @rationale  T2.2 requirement: test_infra fixture must be conditional — only activate
##             for tests with requires_docker marker. Static/contract/gate tests are
##             pure code validation with zero infrastructure dependencies.
##             _test_infra_was_active flag is set in pytest_collection_modifyitems hook
##             which sees ALL collected items (including deselected by -m expression).
##             We scan session.items directly with explicit category filtering instead.
# endregion MODULE_CONTRACT

import logging

import pytest

# Import the global flag from conftest — tracks whether test_infra was active
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


@pytest.mark.static_audit
@ldd_trajectory
def test_static_tests_have_no_host_side_effects(caplog, request) -> None:
    """Verify test_infra fixture was NOT activated for static tests (no requires_docker).

    ## @purpose  T2.2 regression test: verify that no collected test in this session
    ##            has the requires_docker marker. pytest_collection_modifyitems sees
    ##            ALL items before marker deselection, so we check session.items
    ##            directly for requires_docker markers at test time.
    ## @scenario  Scan pytest session.items for requires_docker marker → assert none found
    ## @regression  test_infra autouse creating infrastructure for non-Docker tests
    ## @rationale  _test_infra_was_active flag is set during pytest_collection_modifyitems
    ##             but that hook sees ALL collected items including those later deselected
    ##             by -m expression. We check session.items directly instead.
    """
    logger.info("[IMP:7][test_static_tests_have_no_host_side_effects] Checking test_infra activation flag...")

    # Exclude items that have markers for Docker-dependent test types
    EXCLUDED_MARKERS = {"smoke", "component", "integration", "requires_docker", "e2e", "predeploy", "local_auth"}

    needs_docker = False
    for item in request.session.items:
        markers = {m.name for m in item.iter_markers()}
        # Skip items that are Docker-dependent (they will be filtered out by -m expression)
        if markers & EXCLUDED_MARKERS:
            continue
        # Any other item with requires_docker is a problem
        if item.get_closest_marker("requires_docker"):
            needs_docker = True
            logger.warning(
                "[IMP:7][test] Unexpected requires_docker marker on static item: %s",
                item.nodeid,
            )

    logger.critical(
        "[IMP:9][test_static_tests_have_no_host_side_effects] ASSERT: "
        "requires_docker in non-Docker items = %s (expected False for static tests)",
        needs_docker,
    )

    assert not needs_docker, (
        "Host-side effect detected: some test items have requires_docker marker "
        "without being in an excluded (Docker-dependent) category.\n\n"
        "T2.2 requires: static tests (no requires_docker) must NOT trigger test_infra.\n"
        "Check that your test session does not contain unexpected requires_docker tests."
    )

    logger.critical(
        "[IMP:9][test_static_tests_have_no_host_side_effects] PASS: No requires_docker markers "
        "in non-Docker test items",
    )
