"""
# GREP_SUMMARY: test_jsonschema_validate, jsonschema-validate, Draft7Validator, exit-codes, negative-tests, R5-anti-survivorship, yaml-schema, generic-CLI
# STRUCTURE: ▶ tmp_path fixtures (valid/invalid node.yaml + real node.schema.json) → ◇ main() exit codes: 0 valid / 1 errors / 2 usage|file → ⊕ stderr error-line format asserts → ◇ multiple-errors aggregation → ⎋ LDD IMP:9 via ldd_trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/jsonschema_validate.py — generic
##           YAML↔JSON-Schema CLI extracted from validate.sh PYOF heredoc (DevPlan 093 W1).
##           Validates exit-code semantics (0/1/2), byte-identical error format
##           ("  Error at '<path>': <message>"), and error-path aggregation.
## @scope    Tests main() and validate_yaml_against_schema() natively (no subprocess —
##           business logic per .kilo/rules/testing.md). Uses the REAL node.schema.json
##           as read-only fixture (schemas are read-only consumers per DevPlan 093 §6).
## @invariants
##   - Valid node.yaml → exit 0
##   - Missing required field → exit 1 + error line mentions the field (AC7a)
##   - Type mismatch → exit 1 + error line carries "node > name" absolute path
##   - Multiple violations → ALL aggregated (iter_errors, ≥2 lines)
##   - Malformed YAML → exit 2 (AC7b)
##   - Missing schema/yaml file → exit 2 (fail-fast, AC7b)
##   - Broken schema JSON → exit 2 (AC7b — merge-conflict risk)
##   - Invalid schema structure (SchemaError) → exit 2
##   - R5 anti-survivorship: every error path has a negative test with exact trigger input
##   - Every test asserts IMP:9 verdict log via ldd_trajectory decorator
## @rationale AC7a + AC7b acceptance criteria of DevPlan 093. Negative tests prevent
##   regression of the exit-code contract consumed by validate.sh (`if ! output=...`).
## @changes
##   2026-07-31 · Created (DevPlan 093 W1-T2)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Module under test: namespace-package import from repo root (matches production
# ── invocation `python3 -m core.internal.scripts.jsonschema_validate`) ──
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.internal.scripts import jsonschema_validate

# Real schema — read-only consumer (DevPlan 093 §6: schemas NOT modified)
_NODE_SCHEMA = _REPO_ROOT / "core" / "schemas" / "node.schema.json"

# ── Static fixtures (valid per node.schema.json: required node/modules/contexts) ──
VALID_NODE_YAML = """\
contexts:
  - name: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
modules: []
"""

# Missing "modules" (required at root) — R5 negative trigger
INVALID_MISSING_FIELD_YAML = """\
contexts:
  - name: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
"""

# node.name: 123 instead of string — type-mismatch negative trigger
INVALID_TYPE_MISMATCH_YAML = """\
contexts:
  - name: prod
node:
  name: 123
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
modules: []
"""

# Missing BOTH "contexts" and "modules" — aggregation negative trigger
INVALID_MULTIPLE_ERRORS_YAML = """\
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
"""

# Not parseable as YAML — malformed-yaml negative trigger
MALFORMED_YAML = "key: [unclosed\n: : :\n"

# Broken JSON (truncated) — invalid-schema negative trigger (merge-conflict simulation)
BROKEN_SCHEMA_JSON = '{ "type": "object", "required": ['

# Valid JSON but structurally invalid schema — SchemaError negative trigger
INVALID_SCHEMA_STRUCTURE = "[1, 2, 3]"


# region HELPER
def _write(tmp_path: Path, name: str, content: str) -> Path:
    """Write fixture content into tmp_path and return the file path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# endregion HELPER


# region TEST_VALID
@ldd_trajectory
def test_valid_yaml_exit_0(tmp_path, caplog) -> None:
    """AC7a positive: valid node.yaml → exit 0, silent output, IMP:9 VALID verdict."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — exit-code 0 contract consumed by validate.sh
    # · Scenario: minimal valid node.yaml against real node.schema.json
    # · Last fail: N/A (new CLI)
    # · Remove if: exit-code contract changes
    logger.info("[IMP:7][test_valid_yaml_exit_0] START")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(_NODE_SCHEMA)])

    assert rc == 0, f"FAIL: valid yaml must exit 0, got {rc}"
    logger.info("[IMP:9][test_valid_yaml_exit_0] PASS: valid yaml → exit 0")


# endregion TEST_VALID


# region TEST_MISSING_FIELD
@ldd_trajectory
def test_missing_required_field_exit_1(tmp_path, caplog, capsys) -> None:
    """AC7a negative: missing 'modules' → exit 1, error line mentions the field."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — missing-field error path (R5 anti-survivorship)
    # · Scenario: node.yaml without required 'modules' → exit 1
    # · Last fail: N/A (new CLI)
    # · Remove if: required-field semantics change
    logger.info("[IMP:7][test_missing_required_field_exit_1] START")
    yaml_f = _write(tmp_path, "node.yaml", INVALID_MISSING_FIELD_YAML)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(_NODE_SCHEMA)])
    err = capsys.readouterr().err

    assert rc == 1, f"FAIL: missing field must exit 1, got {rc}"
    assert "Error at '(root)': 'modules' is a required property" in err, (
        f"FAIL: error line must mention 'modules', got: {err!r}"
    )
    logger.info("[IMP:9][test_missing_required_field_exit_1] PASS: missing field → exit 1 + field mention")


# endregion TEST_MISSING_FIELD


# region TEST_TYPE_MISMATCH
@ldd_trajectory
def test_type_mismatch_exit_1(tmp_path, caplog, capsys) -> None:
    """AC7b negative: node.name as int → exit 1, absolute path 'node > name' in error line."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — type-mismatch error path
    # · Scenario: name: 123 (int) where string expected → exit 1 + "node > name" path
    # · Last fail: N/A (new CLI)
    # · Remove if: path-rendering contract changes
    logger.info("[IMP:7][test_type_mismatch_exit_1] START")
    yaml_f = _write(tmp_path, "node.yaml", INVALID_TYPE_MISMATCH_YAML)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(_NODE_SCHEMA)])
    err = capsys.readouterr().err

    assert rc == 1, f"FAIL: type mismatch must exit 1, got {rc}"
    assert "Error at 'node > name': 123 is not of type 'string'" in err, (
        f"FAIL: absolute path 'node > name' expected, got: {err!r}"
    )
    logger.info("[IMP:9][test_type_mismatch_exit_1] PASS: type mismatch → exit 1 + 'node > name' path")


# endregion TEST_TYPE_MISMATCH


# region TEST_MULTIPLE_ERRORS
@ldd_trajectory
def test_multiple_errors_aggregated(tmp_path, caplog, capsys) -> None:
    """AC7b: 2+ violations → ALL reported (iter_errors aggregation, never first-error-only)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — multiple-errors aggregation (AC7b)
    # · Scenario: missing 'contexts' AND 'modules' → ≥2 error lines
    # · Last fail: N/A (new CLI)
    # · Remove if: aggregation semantics change
    logger.info("[IMP:7][test_multiple_errors_aggregated] START")
    yaml_f = _write(tmp_path, "node.yaml", INVALID_MULTIPLE_ERRORS_YAML)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(_NODE_SCHEMA)])
    err = capsys.readouterr().err

    assert rc == 1, f"FAIL: invalid yaml must exit 1, got {rc}"
    lines = [ln for ln in err.splitlines() if ln.startswith("  Error at ")]
    assert len(lines) >= 2, f"FAIL: expected ≥2 aggregated error lines, got {len(lines)}: {err!r}"
    assert any("'contexts'" in ln for ln in lines), f"FAIL: 'contexts' error missing: {lines}"
    assert any("'modules'" in ln for ln in lines), f"FAIL: 'modules' error missing: {lines}"
    logger.info("[IMP:9][test_multiple_errors_aggregated] PASS: %d errors aggregated", len(lines))


# endregion TEST_MULTIPLE_ERRORS


# region TEST_MALFORMED_YAML
@ldd_trajectory
def test_malformed_yaml_exit_2(tmp_path, caplog, capsys) -> None:
    """AC7b negative: unparseable YAML → exit 2 (file/parse error semantics)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — malformed-yaml → exit 2 (AC7b)
    # · Scenario: yaml.safe_load raises YAMLError → exit 2, NOT exit 1
    # · Last fail: N/A (new CLI)
    # · Remove if: exit-code semantics for parse errors change
    logger.info("[IMP:7][test_malformed_yaml_exit_2] START")
    yaml_f = _write(tmp_path, "node.yaml", MALFORMED_YAML)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(_NODE_SCHEMA)])
    err = capsys.readouterr().err

    assert rc == 2, f"FAIL: malformed YAML must exit 2, got {rc}"
    assert "ERROR: malformed YAML" in err, f"FAIL: parse-error message expected, got: {err!r}"
    logger.info("[IMP:9][test_malformed_yaml_exit_2] PASS: malformed YAML → exit 2")


# endregion TEST_MALFORMED_YAML


# region TEST_MISSING_FILES
@ldd_trajectory
def test_missing_schema_file_exit_2(tmp_path, caplog, capsys) -> None:
    """AC7b negative: nonexistent schema path → exit 2 (fail-fast before validation)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — missing-schema → exit 2 (AC7b)
    # · Scenario: --schema-file points at nonexistent path → exit 2
    # · Last fail: N/A (new CLI)
    # · Remove if: file-check semantics change
    logger.info("[IMP:7][test_missing_schema_file_exit_2] START")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)
    missing_schema = tmp_path / "does-not-exist.schema.json"

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(missing_schema)])
    err = capsys.readouterr().err

    assert rc == 2, f"FAIL: missing schema must exit 2, got {rc}"
    assert "ERROR: Schema file not found" in err, f"FAIL: file-error message expected, got: {err!r}"
    logger.info("[IMP:9][test_missing_schema_file_exit_2] PASS: missing schema → exit 2")


@ldd_trajectory
def test_missing_yaml_file_exit_2(tmp_path, caplog, capsys) -> None:
    """Fail-fast negative: nonexistent yaml path → exit 2 before any validation."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — missing-yaml fail-fast
    # · Scenario: --yaml-file points at nonexistent path → exit 2
    # · Last fail: N/A (new CLI)
    # · Remove if: file-check semantics change
    logger.info("[IMP:7][test_missing_yaml_file_exit_2] START")
    missing_yaml = tmp_path / "does-not-exist.yaml"

    rc = jsonschema_validate.main(["--yaml-file", str(missing_yaml), "--schema-file", str(_NODE_SCHEMA)])
    err = capsys.readouterr().err

    assert rc == 2, f"FAIL: missing yaml must exit 2, got {rc}"
    assert "ERROR: YAML file not found" in err, f"FAIL: file-error message expected, got: {err!r}"
    logger.info("[IMP:9][test_missing_yaml_file_exit_2] PASS: missing yaml → exit 2")


# endregion TEST_MISSING_FILES


# region TEST_BROKEN_SCHEMA
@ldd_trajectory
def test_invalid_json_schema_exit_2(tmp_path, caplog, capsys) -> None:
    """AC7b negative: broken schema JSON (merge-conflict simulation) → exit 2."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — invalid-json-schema → exit 2 (AC7b)
    # · Scenario: truncated JSON schema → json.load raises JSONDecodeError → exit 2
    # · Last fail: N/A (new CLI)
    # · Remove if: JSONDecodeError handling changes
    logger.info("[IMP:7][test_invalid_json_schema_exit_2] START")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)
    schema_f = _write(tmp_path, "broken.schema.json", BROKEN_SCHEMA_JSON)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(schema_f)])
    err = capsys.readouterr().err

    assert rc == 2, f"FAIL: broken schema must exit 2, got {rc}"
    assert "ERROR: malformed JSON schema" in err, f"FAIL: JSONDecodeError message expected, got: {err!r}"
    logger.info("[IMP:9][test_invalid_json_schema_exit_2] PASS: broken schema JSON → exit 2")


@ldd_trajectory
def test_invalid_schema_structure_exit_2(tmp_path, caplog, capsys) -> None:
    """Negative: valid JSON but non-schema structure → exit 2 (SchemaError guard)."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T2 — SchemaError guard
    # · Scenario: schema = JSON array (not object) → jsonschema SchemaError → exit 2
    # · Last fail: N/A (new CLI)
    # · Remove if: SchemaError handling changes
    logger.info("[IMP:7][test_invalid_schema_structure_exit_2] START")
    yaml_f = _write(tmp_path, "node.yaml", VALID_NODE_YAML)
    schema_f = _write(tmp_path, "bad-structure.schema.json", INVALID_SCHEMA_STRUCTURE)

    rc = jsonschema_validate.main(["--yaml-file", str(yaml_f), "--schema-file", str(schema_f)])
    err = capsys.readouterr().err

    assert rc == 2, f"FAIL: invalid schema structure must exit 2, got {rc}"
    assert "ERROR: invalid JSON schema structure" in err, f"FAIL: SchemaError message expected, got: {err!r}"
    logger.info("[IMP:9][test_invalid_schema_structure_exit_2] PASS: invalid schema structure → exit 2")


# endregion TEST_BROKEN_SCHEMA
