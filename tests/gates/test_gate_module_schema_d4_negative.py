# GREP_SUMMARY: test, gate, module-schema, D4, negative, R5, missing-fields
# STRUCTURE: ▶ test_module_yaml_missing_required_fields → ◇ tmp_path bad module.yaml → ⊕ _validate_module_yaml_d4 → ⎋ assert errors
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_module_schema_d4.
##           Детектит module.yaml без required D4 полей.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib

import pytest

from tests.gates.test_gate_module_schema_d4 import _validate_module_yaml_d4


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · R5 companion — MUST detect missing D4 fields in module.yaml
# · Last fail: N/A (preventive)
# · Remove if: D4 schema is removed or module.yaml format is superseded
def test_module_yaml_missing_required_fields(tmp_path: pathlib.Path) -> None:
    """R5: validator must REPORT missing required D4 fields."""
    # Construct module.yaml without env_requires (required by D4)
    bad_module = tmp_path / "module.yaml"
    bad_module.write_text(
        "name: test-module\nversion: 1.0.0\n# missing: install_type, description, env_requires, restart, healthcheck\n"
    )

    errors = _validate_module_yaml_d4(bad_module)

    assert errors, "[IMP:9][gate][negative] validator FAILED to report missing fields"
