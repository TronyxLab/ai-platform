# GREP_SUMMARY: test-validate json-schema node.yaml module.yaml pytest LDD IMP caplog
# STRUCTURE: fixtures → test_node_yaml_validation[3 params] → test_module_install_type_validation[2 params]

# region MODULE_CONTRACT
## @purpose  Verify node.yaml and module.yaml schema validation: accepts valid
##           declarations and rejects invalid ones with meaningful errors.
## @scope    Unit tests; uses tmp_path; does NOT invoke shell validate.sh directly
##           (uses python-jsonschema natively per testing rules §TESTING).
##           Project schema tests moved to test_project_schema.py (Low #16 merge).
## @invariants
##   - Valid node.yaml with 'context' passes
##   - node.yaml without 'context' is rejected
##   - module.yaml must have install_type in {system, docker}
##   - At least one IMP:9 log present per §TESTING
## @rationale Q: Why not test via shell validate.sh? A: Direct python-jsonschema
##            calls give precise error messages and faster iteration.
## @changes — LAST_CHANGE: 2026-07-03 | Low #16: merged project schema tests → test_project_schema.py,
##            removed test_valid_project_yaml, test_project_yml_extension_rejected, project_schema fixture.
def _module_contract():
    pass


# endregion MODULE_CONTRACT

"""
Tests for node.yaml and module.yaml JSON Schema validation.

@purpose  Verify node.yaml and module.yaml schema validation.
          Project schema (ai-platform.yaml) tests → test_project_schema.py (Low #16).
@scope    Unit tests; uses tmp_path; does NOT invoke shell validate.sh directly
          (uses python-jsonschema natively per testing rules §TESTING).
@invariants
  - Valid node.yaml with 'context' passes
  - node.yaml without 'context' is rejected
  - module.yaml must have install_type in {system, docker}
  - At least one IMP:9 log present per §TESTING
"""

import logging
import os

import jsonschema
import pytest
import yaml
from conftest import ldd_trajectory
from helpers import load_schema

logger = logging.getLogger(__name__)


# region FIXTURES
@pytest.fixture
def node_schema() -> dict:
    return load_schema("node.schema.json")


@pytest.fixture
def module_schema() -> dict:
    return load_schema("module.schema.json")


@pytest.fixture
def valid_node_yaml_path() -> str:
    """Path to the reference valid node.yaml test fixture."""
    return os.path.join(os.path.dirname(__file__), "test_data", "node.yaml")


@pytest.fixture
def valid_node_data(valid_node_yaml_path) -> dict:
    with open(valid_node_yaml_path) as f:
        return yaml.safe_load(f)


# endregion FIXTURES
# region TEST_NODE_SCHEMA
@ldd_trajectory
@pytest.mark.parametrize(
    "node_data,expect_valid,error_hint",
    [
        # valid node.yaml — must pass
        pytest.param(None, True, None, id="valid-node-yaml"),
        # missing context — must fail
        pytest.param(
            {
                "node": {
                    "name": "test-node",
                    "host": "1.2.3.4",
                    "owner_key": "ssh-ed25519 AAAA test@example.com",
                },
                "modules": [],
            },
            False,
            "context",
            id="missing-context",
        ),
        # extra field — must fail
        pytest.param(
            {
                "context": "production",
                "node": {
                    "name": "test-node",
                    "host": "1.2.3.4",
                    "owner_key": "ssh-ed25519 AAAA test@example.com",
                },
                "modules": [],
                "UNKNOWN_EXTRA_FIELD": "should be rejected",
            },
            False,
            "Additional properties",
            id="extra-fields",
        ),
    ],
)
def test_node_yaml_validation(node_data, expect_valid, error_hint, valid_node_data, node_schema, caplog) -> None:
    """Parametrized node.yaml validation: valid, missing context, extra fields."""
    with caplog.at_level(logging.DEBUG):
        data = node_data if node_data is not None else valid_node_data
        label = "valid_node_data" if node_data is None else "inline"
        logger.info(
            "[IMP:7][test_validate][test_node_yaml_validation] START: expect_valid=%s (%s)", expect_valid, label
        )

        validator = jsonschema.Draft7Validator(node_schema)
        errors = list(validator.iter_errors(data))

        logger.critical(
            "[IMP:9][test_validate][node_yaml_validation] ASSERT: errors=%d, expect_valid=%s",
            len(errors),
            expect_valid,
        )

        if expect_valid:
            error_messages = [f"{e.absolute_path}: {e.message}" for e in errors]
            assert errors == [], f"Valid node.yaml failed schema: {error_messages}"
        else:
            assert len(errors) > 0, f"Expected validation errors (hint: {error_hint})"
            if error_hint:
                error_msgs = " ".join(e.message for e in errors)
                assert error_hint in error_msgs, f"Expected '{error_hint}' in error messages, got: {error_msgs}"


# endregion TEST_NODE_SCHEMA
# region TEST_MODULE_SCHEMA
@ldd_trajectory
@pytest.mark.parametrize(
    "module_data,expect_valid",
    [
        pytest.param(
            {
                "name": "nginx",
                "version": "0.1.0",
                "install_type": "system",
                "description": "System nginx module",
            },
            True,
            id="valid-module-system",
        ),
        pytest.param(
            {
                "name": "bad-module",
                "version": "0.1.0",
                "install_type": "kubernetes",  # Not in enum {system, docker}
            },
            False,
            id="invalid-install-type",
        ),
    ],
)
def test_module_install_type_validation(module_data, expect_valid, module_schema, caplog) -> None:
    """Parametrized module.yaml validation: valid install_type vs invalid enum."""
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_validate][test_module_install_type_validation] START: expect_valid=%s", expect_valid)

        validator = jsonschema.Draft7Validator(module_schema)
        errors = list(validator.iter_errors(module_data))

        logger.critical(
            "[IMP:9][test_validate][module_install_type_validation] ASSERT: errors=%d, expect_valid=%s",
            len(errors),
            expect_valid,
        )

        if expect_valid:
            assert errors == [], f"Valid module.yaml failed: {[e.message for e in errors]}"
        else:
            assert len(errors) > 0, "Expected schema error for invalid install_type"


# endregion TEST_MODULE_SCHEMA
