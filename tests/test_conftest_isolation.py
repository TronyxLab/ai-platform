# GREP_SUMMARY: conftest isolation static-tests host-side-effects volume-dirs docker-networks T2.2 no-side-effects test_infra_was_active
# STRUCTURE: ▶ test_static_tests_have_no_host_side_effects → ◇ import _test_infra_was_active from conftest → ⟦assert not active⟧
# region MODULE_CONTRACT
## @purpose  Verify that static/contract/gate tests (those without requires_docker marker)
##           do NOT activate the test_infra fixture. This is the regression test for
##           T2.2 — marker-based isolation of the test_infra fixture in conftest.py.
## @scope    Checks the `_test_infra_was_active` flag in conftest.py. This flag is
##           set to True only when test_infra detects at least one requires_docker
##           marker in the collected tests. For static/contract/gate sessions, the
##           flag must remain False.
## @invariants
##   - _test_infra_was_active must be False for test sessions with no requires_docker marker
##   - Uses @pytest.mark.static_audit marker (not requires_docker)
## @rationale  T2.2 requirement: test_infra fixture must be conditional — only activate
##             for tests with requires_docker marker. Static/contract/gate tests are
##             pure code validation with zero infrastructure dependencies.
# endregion MODULE_CONTRACT

import logging

import pytest

# Import the global flag from conftest — tracks whether test_infra was active
from tests.conftest import _test_infra_was_active, ldd_trajectory

logger = logging.getLogger(__name__)


@pytest.mark.static_audit
@ldd_trajectory
def test_static_tests_have_no_host_side_effects(caplog) -> None:
    """Verify test_infra fixture was NOT activated for static tests (no requires_docker).

    ## @purpose  T2.2 regression test: after the test session collects tests, the
    ##            test_infra fixture should have detected NO requires_docker markers
    ##            and skipped its setup. This test checks the conftest-level flag
    ##            _test_infra_was_active.
    ## @scenario  Import _test_infra_was_active from conftest → assert False
    ##            (test_infra skipped volume/network setup)
    ## @regression  test_infra autouse creating infrastructure for non-Docker tests
    """
    logger.info("[IMP:7][test_static_tests_have_no_host_side_effects] Checking test_infra activation flag...")

    logger.critical(
        "[IMP:9][test_static_tests_have_no_host_side_effects] ASSERT: "
        "_test_infra_was_active = %s (expected False for static tests)",
        _test_infra_was_active,
    )

    assert not _test_infra_was_active, (
        "Host-side effect detected: test_infra fixture was ACTIVATED for this test session. "
        "This means at least one collected test has the requires_docker marker, or the "
        "fixture logic incorrectly detected one.\n\n"
        "T2.2 requires: static tests (no requires_docker) must NOT trigger test_infra.\n"
        "Check that your test session does not contain requires_docker tests."
    )

    logger.critical(
        "[IMP:9][test_static_tests_have_no_host_side_effects] PASS: test_infra correctly skipped — "
        "_test_infra_was_active is False",
    )
