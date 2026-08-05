# GREP_SUMMARY: test-project-schema ai-platform.yaml FQDN-conflict version-pinning type-enum target_node yml-extension llm-field pytest LDD IMP caplog
# STRUCTURE: fixtures → test_valid_project_yaml[2 params] → test_project_type_enum[3 params] → test_fqdn_* → test_version_latest → test_expose_domain_invariant → test_missing_target_node → test_llm_*[4 params]

# region MODULE_CONTRACT
## @purpose  Verify ai-platform.yaml schema: FQDN conflict (E1), version pinning (E2),
##           type enum, target_node requirement.
## @scope    Unit tests using python-jsonschema natively (§TESTING — native imports only).
##           No shell subprocess calls.
## @invariants
##   - FQDN conflict: two projects with same domain — schema does not block,
##     but test checks _simulate_fqdn_registry that models validate.sh behaviour
##   - Version: 'latest' as platform_services value is NOT auto-injected (04 §5)
##   - type: only frontend | backend | agent
##   - target_node: required field
##   - At least one IMP:9 log per §TESTING
## @rationale Q: Why test schema in Python instead of shell? A: python-jsonschema gives
##            precise error messages and is faster than shell-based validation.
## @changes — LAST_CHANGE: 2026-07-03 | Low #16: merged test_project_yml_extension_rejected from test_validate.py
def _module_contract():
    pass


# endregion MODULE_CONTRACT

"""
Tests for ai-platform.yaml schema validation (TASK-03-07).

@purpose  Verify ai-platform.yaml schema: FQDN conflict (E1), version pinning (E2),
          type enum, target_node requirement.
@scope    Unit tests using python-jsonschema natively (§TESTING — native imports only).
          No shell subprocess calls.
@invariants
  - FQDN-конфликт: два проекта с одинаковым domain — schema не блокирует (runtime validate.sh блокирует),
    но тест проверяет логику _simulate_fqdn_registry которая моделирует поведение validate.sh
  - Версия: 'latest' как значение platform_services НЕ подставляется автоматически (04 §5)
  - type: только frontend | backend | agent
  - target_node: обязательное поле
  - At least one IMP:9 log per §TESTING
"""

import logging
import os

import jsonschema
import pytest
from conftest import ldd_trajectory
from helpers import FQDNConflictError, FQDNRegistry, load_schema

logger = logging.getLogger(__name__)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


# region FIXTURES
@pytest.fixture
def project_schema() -> dict:
    return load_schema("ai-platform.schema.json")


@pytest.fixture(
    params=[
        {
            "name": "my-frontend",
            "type": "frontend",
            "target_node": "mercury",
            "environments": {"production": True, "staging": False},
            "platform_services": {"nginx-proxy": "1.3.0"},
            "needs": {
                "domain": "my-frontend.example.ru",
                "database": False,
                "cache": False,
                "storage": False,
                "expose": True,
            },
            "stop_grace_period": "30s",
        },
        {
            "name": "my-backend",
            "type": "backend",
            "target_node": "mercury",
            "environments": {"production": True, "staging": False},
            "platform_services": {"nginx-proxy": "1.3.0", "postgres": "15.2.0"},
            "needs": {
                "domain": "api.example.ru",
                "database": "myproject_db",
                "cache": False,
                "storage": False,
                "expose": True,
            },
            "stop_grace_period": "30s",
        },
    ]
)
def valid_project_yaml(request) -> dict:
    """Parametrized fixture: valid frontend and backend ai-platform.yaml data."""
    return request.param


# endregion FIXTURES
# region TESTS
@ldd_trajectory
def test_valid_project_yaml(valid_project_yaml, project_schema, caplog) -> None:
    """Parametrized: valid frontend/backend ai-platform.yaml must pass schema validation."""
    with caplog.at_level(logging.DEBUG):
        project_type = valid_project_yaml.get("type", "unknown")
        logger.info("[IMP:7][test_project_schema][valid_project] START: validating %s ai-platform.yaml", project_type)

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(valid_project_yaml))

        logger.critical(
            "[IMP:9][test_project_schema][valid_project] ASSERT: schema errors=%d (expected 0) for type=%s",
            len(errors),
            project_type,
        )

        assert errors == [], f"Valid {project_type} ai-platform.yaml failed schema: {[e.message for e in errors]}"


@ldd_trajectory
def test_fqdn_conflict_blocks_deploy(caplog) -> None:
    """
    FQDN conflict (E1): second project claiming same FQDN must be blocked.
    Simulates validate.sh behavior: first claimant owns the FQDN (06 §5.4).
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][fqdn_conflict] START: testing E1 FQDN conflict")

        registry = FQDNRegistry()
        fqdn = "shared.example.ru"

        # First project claims the FQDN — must succeed
        registry.claim(fqdn, "project-alpha")
        logger.info("[IMP:8][test_project_schema][fqdn_conflict] project-alpha claimed '%s'", fqdn)

        # Second project tries to claim same FQDN — must be blocked
        conflict_raised = False
        conflict_msg = ""
        try:
            registry.claim(fqdn, "project-beta")
        except FQDNConflictError as exc:
            conflict_raised = True
            conflict_msg = str(exc)
            logger.critical(
                "[IMP:9][test_project_schema][fqdn_conflict] ASSERT: FQDNConflictError raised: %s",
                conflict_msg,
            )

        assert conflict_raised, "E1: FQDN conflict must raise FQDNConflictError — deploy should be blocked"
        assert "E1" in conflict_msg, f"Error message should reference E1: {conflict_msg}"
        assert registry.owner_of(fqdn) == "project-alpha", "First claimant must retain ownership"


@ldd_trajectory
def test_fqdn_same_project_no_conflict(caplog) -> None:
    """Same project re-claiming its own FQDN must NOT raise a conflict."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][fqdn_no_conflict] START: idempotent claim")

        registry = FQDNRegistry()
        fqdn = "my-project.example.ru"

        registry.claim(fqdn, "my-project")
        # Second claim by same project — idempotent, no error
        registry.claim(fqdn, "my-project")

        logger.critical(
            "[IMP:9][test_project_schema][fqdn_no_conflict] ASSERT: same project re-claim is idempotent",
        )

        assert registry.owner_of(fqdn) == "my-project"


@ldd_trajectory
def test_version_latest_rejected_by_schema(project_schema, caplog) -> None:
    """
    E2 version pinning: platform_services values MUST be in semver format (e.g. '1.3.0').
    The schema enforces pattern ^[0-9]+\\.[0-9]+(\\.[0-9]+)?$ — 'latest' and empty are rejected.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][version_latest] START: E2 version 'latest' rejected by schema")

        # 'latest' as a version must be rejected — semver enforcement
        data_with_latest = {
            "name": "test-project",
            "type": "backend",
            "target_node": "mercury",
            "platform_services": {"nginx-proxy": "latest"},
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors_latest = list(validator.iter_errors(data_with_latest))

        logger.info("[IMP:8][test_project_schema][version_latest] 'latest' errors=%d (expected >0)", len(errors_latest))

        # Empty string version is rejected (minLength:1 + pattern)
        data_with_empty_version = {
            "name": "test-project",
            "type": "backend",
            "target_node": "mercury",
            "platform_services": {"nginx-proxy": ""},
        }

        errors_empty = list(validator.iter_errors(data_with_empty_version))

        # Valid semver must pass
        data_with_valid_semver = {
            "name": "test-project",
            "type": "backend",
            "target_node": "mercury",
            "platform_services": {"nginx-proxy": "1.3.0"},
        }

        errors_valid = list(validator.iter_errors(data_with_valid_semver))

        logger.critical(
            "[IMP:9][test_project_schema][version_latest] ASSERT: 'latest' errors=%d (expected >0), "
            "empty errors=%d (expected >0), valid semver errors=%d (expected 0)",
            len(errors_latest),
            len(errors_empty),
            len(errors_valid),
        )

        assert len(errors_latest) > 0, "'latest' must be rejected by schema (semver enforcement)"
        assert len(errors_empty) > 0, "Empty version string must be rejected by schema"
        assert errors_valid == [], f"Valid semver '1.3.0' should pass: {[e.message for e in errors_valid]}"


@ldd_trajectory
def test_invalid_domain_rejected_by_schema(project_schema, caplog) -> None:
    """
    FQDN validation: invalid domain strings like 'bad' or 'not_a_domain' must be rejected.
    Valid FQDNs like 'example.com' or 'my-site.example.ru' must pass.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][invalid_domain] START: FQDN pattern enforcement")

        validator = jsonschema.Draft7Validator(project_schema)

        # Invalid domain — should be rejected
        data_invalid_domain = {
            "name": "test-project",
            "type": "frontend",
            "target_node": "mercury",
            "needs": {
                "domain": "bad",
                "expose": True,
            },
        }

        errors_invalid = list(validator.iter_errors(data_invalid_domain))

        # Valid domain — should pass
        data_valid_domain = {
            "name": "test-project",
            "type": "frontend",
            "target_node": "mercury",
            "needs": {
                "domain": "example.com",
                "expose": True,
            },
        }

        errors_valid = list(validator.iter_errors(data_valid_domain))

        logger.critical(
            "[IMP:9][test_project_schema][invalid_domain] ASSERT: invalid domain 'bad' errors=%d (expected >0), "
            "valid 'example.com' errors=%d (expected 0)",
            len(errors_invalid),
            len(errors_valid),
        )

        assert len(errors_invalid) > 0, "Invalid domain 'bad' must be rejected by FQDN pattern"
        assert errors_valid == [], f"Valid domain 'example.com' should pass: {[e.message for e in errors_valid]}"


@ldd_trajectory
def test_expose_true_requires_domain_string(project_schema, caplog) -> None:
    """
    Logical invariant: if needs.expose is true, then needs.domain must be a string (not false).
    This prevents misconfiguration where a project declares HTTP exposure but has no domain.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][expose_domain_invariant] START: expose=true → domain string required")

        validator = jsonschema.Draft7Validator(project_schema)

        # expose=true with domain=false — must be rejected
        data_expose_no_domain = {
            "name": "test-project",
            "type": "frontend",
            "target_node": "mercury",
            "needs": {
                "domain": False,
                "expose": True,
            },
        }

        errors_invalid = list(validator.iter_errors(data_expose_no_domain))

        # expose=false with domain=false — must pass
        data_no_expose_no_domain = {
            "name": "test-project",
            "type": "backend",
            "target_node": "mercury",
            "needs": {
                "domain": False,
                "expose": False,
            },
        }

        errors_valid = list(validator.iter_errors(data_no_expose_no_domain))

        logger.critical(
            "[IMP:9][test_project_schema][expose_domain_invariant] ASSERT: "
            "expose=true + domain=false errors=%d (expected >0), "
            "expose=false + domain=false errors=%d (expected 0)",
            len(errors_invalid),
            len(errors_valid),
        )

        assert len(errors_invalid) > 0, "expose=true with domain=false must be rejected (invariant)"
        assert errors_valid == [], f"expose=false + domain=false should pass: {[e.message for e in errors_valid]}"


@ldd_trajectory
@pytest.mark.parametrize(
    "project_type",
    [
        "microservice",  # Not in enum {frontend, backend, agent}
        "cli-tool",
        "",
    ],
)
def test_project_type_enum(project_type, project_schema, caplog) -> None:
    """ai-platform.yaml with invalid type must be rejected."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][type_enum] START: invalid type='%s'", project_type)

        invalid_data = {
            "name": "test",
            "type": project_type,
            "target_node": "mercury",
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(invalid_data))

        logger.critical(
            "[IMP:9][test_project_schema][type_enum] ASSERT: errors=%d for type='%s' (expected >0)",
            len(errors),
            project_type,
        )

        assert len(errors) > 0, f"Invalid type '{project_type}' must be rejected"


@ldd_trajectory
def test_project_missing_target_node(project_schema, caplog) -> None:
    """ai-platform.yaml without target_node must be rejected."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_project_schema][missing_target_node] START")

        invalid_data = {
            "name": "test",
            "type": "backend",
            # target_node missing
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(invalid_data))

        logger.critical(
            "[IMP:9][test_project_schema][missing_target_node] ASSERT: errors=%d (expected >0)",
            len(errors),
        )

        assert len(errors) > 0, "Missing target_node must fail schema validation"
        error_msgs = " ".join(e.message for e in errors)
        assert "target_node" in error_msgs, f"Expected 'target_node' in error messages: {error_msgs}"


# ── LLM field tests (DevPlan 049 Phase 2) ────────────────────────────────────


@ldd_trajectory
def test_llm_enabled_true(project_schema, caplog) -> None:
    """Minimal config with llm: {enabled: true} must pass schema validation.

    ## @purpose  Verify progressive disclosure: most projects only need
    ##           llm.enabled: true to get default chat access.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_llm_enabled_true] START: testing llm: {enabled: true}")

        data = {
            "name": "test-llm-project",
            "type": "backend",
            "target_node": "mercury",
            "llm": {"enabled": True},
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_llm_enabled_true] ASSERT: llm enabled errors=%d (expected 0)",
            len(errors),
        )
        assert errors == [], f"llm: {{enabled: true}} should pass validation: {[e.message for e in errors]}"


@ldd_trajectory
def test_llm_with_profile(project_schema, caplog) -> None:
    """Config with llm.enabled and explicit profile must pass.

    ## @purpose  Verify profile enum validation: default, premium, unlimited are valid.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_llm_with_profile] START: testing llm: {enabled: true, profile: premium}")

        data = {
            "name": "test-llm-premium",
            "type": "backend",
            "target_node": "mercury",
            "llm": {"enabled": True, "profile": "premium"},
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_llm_with_profile] ASSERT: llm with profile errors=%d (expected 0)",
            len(errors),
        )
        assert errors == [], f"llm: {{enabled: true, profile: premium}} should pass: {[e.message for e in errors]}"


@ldd_trajectory
def test_llm_with_overrides(project_schema, caplog) -> None:
    """Config with llm overrides (budget, rpm_limit) must pass.

    ## @purpose  Verify overrides.budget.daily and other nested fields validate.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_llm_with_overrides] START: testing llm overrides")

        data = {
            "name": "test-llm-overrides",
            "type": "backend",
            "target_node": "mercury",
            "llm": {
                "enabled": True,
                "profile": "default",
                "overrides": {
                    "budget": {"daily": 5.0},
                    "rpm_limit": 100,
                },
            },
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_llm_with_overrides] ASSERT: llm overrides errors=%d (expected 0)",
            len(errors),
        )
        assert errors == [], f"llm with overrides should pass validation: {[e.message for e in errors]}"


@ldd_trajectory
def test_llm_invalid_profile(project_schema, caplog) -> None:
    """Config with invalid profile must be rejected by schema.

    ## @purpose  Verify enum enforcement: only 'default', 'premium', 'unlimited' are valid.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_llm_invalid_profile] START: testing invalid profile")

        data = {
            "name": "test-llm-bad-profile",
            "type": "backend",
            "target_node": "mercury",
            "llm": {"enabled": True, "profile": "nonexistent"},
        }

        validator = jsonschema.Draft7Validator(project_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_llm_invalid_profile] ASSERT: invalid profile errors=%d (expected >0)",
            len(errors),
        )
        assert len(errors) > 0, "Invalid profile 'nonexistent' must be rejected by schema"


# endregion TESTS
