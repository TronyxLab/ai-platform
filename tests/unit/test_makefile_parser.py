"""
# GREP_SUMMARY: test_makefile_parser, extract-targets, phony, include-chains, negative, tmp_path
# STRUCTURE: ▶ extract_makefile_targets 2× (real/none) → ▶ get_all_targets 2× (chains/no-chains) → ▶ negative 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for tests/helpers/makefile_parser.py (DevPlan 171 W1.7) — shared
##           Makefile target parsers extracted from two gates.
## @scope    tests/helpers/makefile_parser.py
## @invariants
##   - Native imports (no subprocess); tmp_path for fixture Makefiles
##   - ldd_trajectory + IMP:9 asserts
##   - R5-negative: pseudo-targets (variables, .PHONY with $(), dots) are NOT captured
## @rationale Единый канон парсеров требует собственного покрытия: до W1.7 парсеры
##            покрывались только интегрально через гейты (ошибка парсера маскировалась).
## @changes 2026-08-15 | DevPlan 171 W1.7 — created
# endregion MODULE_CONTRACT
"""

import logging

import pytest

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.makefile_parser import extract_makefile_targets, get_all_targets

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit


# ═══════════════════════════════════════════════════════════════════
# region Tests: extract_makefile_targets
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · extract_makefile_targets captures real targets, skips .PHONY/vars
# · Scenario: Makefile with real target, .PHONY decl, variable assignment → only real target
# · Last fail: N/A (new test)
# · Remove if: makefile_parser.py is superseded
@ldd_trajectory
def test_extract_makefile_targets_real_targets(caplog, tmp_path):
    """extract_makefile_targets should return real targets, skipping .PHONY and variables."""
    mf = tmp_path / "Makefile"
    mf.write_text(
        ".PHONY: deploy install\n"
        "deploy:\n"
        "\techo deploy\n"
        "PROJECT := myproj\n"
        "FLAG = value\n"
        "help: ## show help\n"
        "\techo help\n"
        "UP:\n"
    )

    result = extract_makefile_targets(str(mf))

    assert result == ["deploy", "help"], f"Expected ['deploy', 'help'], got {result}"
    logger.critical("[IMP:9][test] extract_makefile_targets returned %s", result)


# 🧪 TRAP[TEST] · Regression · No real targets → empty list
# · Scenario: Makefile with only .PHONY and variables → []
# · Last fail: N/A (new test)
# · Remove if: makefile_parser.py is superseded
@ldd_trajectory
def test_extract_makefile_targets_none(caplog, tmp_path):
    """extract_makefile_targets should return [] for Makefile with only .PHONY/variables."""
    mf = tmp_path / "Makefile"
    mf.write_text(".PHONY: all\nALL := x\nVAR = y\n")

    result = extract_makefile_targets(str(mf))

    assert result == [], f"Expected [], got {result}"
    logger.critical("[IMP:9][test] extract_makefile_targets empty result OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: get_all_targets
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_all_targets follows include chains by default
# · Scenario: root Makefile includes inc.mk with .PHONY target → both root and included targets
# · Last fail: N/A (new test)
# · Remove if: makefile_parser.py is superseded
@ldd_trajectory
def test_get_all_targets_include_chains(caplog, tmp_path):
    """get_all_targets(include_chains=True) should merge targets from included Makefiles."""
    (tmp_path / "inc.mk").write_text(".PHONY: deploy-context\ndeploy-context:\n\techo ctx\n")
    mf = tmp_path / "Makefile"
    mf.write_text("include inc.mk\n.PHONY: check\ndeploy:\n\techo d\n")

    result = get_all_targets(str(mf))

    assert "check" in result, "root .PHONY target missing"
    assert "deploy" in result, "root explicit target missing"
    assert "deploy-context" in result, "included .PHONY target missing"
    logger.critical("[IMP:9][test] get_all_targets include_chains=%s", sorted(result))


# 🧪 TRAP[TEST] · Regression · include_chains=False ignores included files
# · Scenario: same fixture, flag False → included targets absent
# · Last fail: N/A (new test)
# · Remove if: makefile_parser.py is superseded
@ldd_trajectory
def test_get_all_targets_no_chains(caplog, tmp_path):
    """get_all_targets(include_chains=False) should NOT merge included targets."""
    (tmp_path / "inc.mk").write_text(".PHONY: deploy-context\ndeploy-context:\n\techo ctx\n")
    mf = tmp_path / "Makefile"
    mf.write_text("include inc.mk\n.PHONY: check\ndeploy:\n\techo d\n")

    result = get_all_targets(str(mf), include_chains=False)

    assert "check" in result, "root .PHONY target missing"
    assert "deploy" in result, "root explicit target missing"
    assert "deploy-context" not in result, "included target must be excluded with include_chains=False"
    logger.critical("[IMP:9][test] get_all_targets no-chains=%s", sorted(result))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: R5-negative
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · R5-negative · 171-W1.7 · pseudo-targets are NOT captured as targets
# · Original form: Makefile-строки, которые НЕ являются таргетами (variables, .PHONY,
# ·   dot-directives, $-references) — парсер должен их отбрасывать.
# · Scenario: inject pseudo-target lines → they must NOT appear in results
# · Last fail: N/A (new test)
# · Remove if: makefile_parser.py is superseded
@ldd_trajectory
def test_makefile_parser_negative_pseudo_targets(caplog, tmp_path):
    """R5-negative: variables/.PHONY/dot-directives/$-refs are NOT captured as targets."""
    mf = tmp_path / "Makefile"
    mf.write_text(
        "VAR := val\n"
        "VAR2 = val2\n"
        "VAR3 ?= val3\n"
        "VAR4 += val4\n"
        ".PHONY: fake-phony\n"
        ".DEFAULT_GOAL := help\n"
        "$(TEMPLATE):\n"
        "target: $$(DEP)\n"
    )

    line_targets = extract_makefile_targets(str(mf))
    set_targets = get_all_targets(str(mf), include_chains=False)

    for pseudo in ("VAR", "VAR2", "VAR3", "VAR4", ".DEFAULT_GOAL", "$(TEMPLATE)"):
        assert pseudo not in line_targets, f"pseudo-target {pseudo!r} leaked into extract_makefile_targets"
        assert pseudo not in set_targets, f"pseudo-target {pseudo!r} leaked into get_all_targets"
    assert "target" in line_targets, "real target with $-dep missing from extract_makefile_targets"
    assert "fake-phony" in set_targets, ".PHONY target must be captured by get_all_targets"

    logger.critical("[IMP:9][test] R5-negative: pseudo-targets correctly rejected")


# endregion
