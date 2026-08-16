"""
# GREP_SUMMARY: test_validate_cli, subprocess, CLI-contract, exit-codes, golden-baseline, byte-comparison, jsonschema-validate, validate.sh
# STRUCTURE: ▶ tmp_path fixtures → ○ subprocess python3 -m core.internal.scripts.jsonschema_validate (cwd=repo root) → ◇ rc asserts → ⊕ golden byte-comparison of stderr → ⎋ empty-output assert for valid case
# region MODULE_CONTRACT
## @purpose  CLI-contract regression tests for jsonschema_validate.py via REAL subprocess
##           invocation (DevPlan 093 W1-T4). Verifies the exact command line used by
##           validate.sh (`python3 -m core.internal.scripts.jsonschema_validate
##           --yaml-file X --schema-file Y`) with byte-level golden comparison of stderr —
##           the AC1 byte-identical contract between validate.sh and CI logs.
## @scope    Subprocess-only (CLI boundary test — the one legitimate subprocess use per
##           DevPlan W1-T4; business logic stays native in test_jsonschema_validate.py).
##           cwd = repo root (namespace-package resolution, mirrors make validate CWD).
## @invariants
##   - Valid instance → exit 0 AND stdout==stderr=="" (byte-identical silence, AC1)
##   - Missing required field → exit 1, stderr == golden "  Error at '(root)': 'modules' is a required property\n"
##   - Type mismatch → exit 1, stderr == golden "  Error at 'node > name': 123 is not of type 'string'\n"
##   - Golden strings match the PYOF heredoc format exactly (2-space prefix, " > " path)
##   - No LDD logging leaks into subprocess stderr (logger.info dropped without handlers)
## @rationale W1-T4: subprocess proves the CLI contract independent of in-process imports
##   (sys.path, namespace-package resolution, exit-code propagation through bash `if !`).
## @changes
##   2026-07-31 · Created (DevPlan 093 W1-T4)
# endregion MODULE_CONTRACT
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.static_audit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NODE_SCHEMA = _REPO_ROOT / "core" / "schemas" / "node.schema.json"

# Valid per node.schema.json (required: node, modules, contexts — DevPlan 116 B6 T1)
VALID_NODE_YAML = """\
contexts:
  - name: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
modules: []
"""

# Missing required "modules" — golden error line at root
INVALID_MISSING_FIELD_YAML = """\
contexts:
  - name: prod
node:
  name: test-node
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
"""

# node.name: 123 — golden error line with " > " absolute path
INVALID_TYPE_MISMATCH_YAML = """\
contexts:
  - name: prod
node:
  name: 123
  host: 10.0.0.1
  owner_key: ssh-ed25519 AAAA test@test
modules: []
"""

# Golden baselines — byte-identical to PYOF heredoc output (DevPlan 093 AC1)
GOLDEN_MISSING_FIELD = "  Error at '(root)': 'modules' is a required property\n"
GOLDEN_TYPE_MISMATCH = "  Error at 'node > name': 123 is not of type 'string'\n"


# region HELPER
def _run_cli(yaml_file: Path, schema_file: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI exactly as validate.sh dispatches it (subprocess, repo-root CWD)."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "core.internal.scripts.jsonschema_validate",
            "--yaml-file",
            str(yaml_file),
            "--schema-file",
            str(schema_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=30,
        check=False,
    )


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "node.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# endregion HELPER


# region TEST_CLI_VALID
def test_cli_valid_yaml_exit0_silent(tmp_path) -> None:
    """AC1: valid yaml → exit 0 with byte-identical EMPTY stdout+stderr."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T4 — AC1 byte-identical happy path
    # · Scenario: valid node.yaml → CLI must stay fully silent (PYOF printed nothing)
    # · Last fail: N/A (new CLI)
    # · Remove if: silence contract changes
    yaml_f = _write_yaml(tmp_path, VALID_NODE_YAML)

    result = _run_cli(yaml_f, _NODE_SCHEMA)

    assert result.returncode == 0, f"FAIL: valid yaml must exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert not result.stdout, f"FAIL: stdout must be empty, got: {result.stdout!r}"
    assert not result.stderr, f"FAIL: stderr must be empty (byte-identical), got: {result.stderr!r}"


# endregion TEST_CLI_VALID


# region TEST_CLI_MISSING_FIELD
def test_cli_missing_field_exit1_golden(tmp_path) -> None:
    """AC1/AC7a: missing field → exit 1, stderr byte-identical to golden baseline."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T4 — golden byte-comparison (AC1)
    # · Scenario: missing 'modules' → exact error line
    # · Last fail: N/A (new CLI)
    # · Remove if: error format contract changes
    yaml_f = _write_yaml(tmp_path, INVALID_MISSING_FIELD_YAML)

    result = _run_cli(yaml_f, _NODE_SCHEMA)

    assert result.returncode == 1, f"FAIL: must exit 1, got {result.returncode}\nstderr: {result.stderr}"
    assert not result.stdout, f"FAIL: stdout must stay empty, got: {result.stdout!r}"
    assert result.stderr == GOLDEN_MISSING_FIELD, (
        f"FAIL: stderr must match golden baseline\nEXPECTED: {GOLDEN_MISSING_FIELD!r}\nACTUAL:   {result.stderr!r}"
    )


# endregion TEST_CLI_MISSING_FIELD


# region TEST_CLI_TYPE_MISMATCH
def test_cli_type_mismatch_exit1_golden(tmp_path) -> None:
    """AC7b: type mismatch → exit 1, golden error line with ' > ' absolute path."""
    # 🧪 TRAP[TEST] · Regression: DevPlan 093 W1-T4 — absolute-path golden format
    # · Scenario: name: 123 → "  Error at 'node > name': ..." (" > " separator)
    # · Last fail: N/A (new CLI)
    # · Remove if: path-rendering contract changes
    yaml_f = _write_yaml(tmp_path, INVALID_TYPE_MISMATCH_YAML)

    result = _run_cli(yaml_f, _NODE_SCHEMA)

    assert result.returncode == 1, f"FAIL: must exit 1, got {result.returncode}\nstderr: {result.stderr}"
    assert result.stderr == GOLDEN_TYPE_MISMATCH, (
        f"FAIL: stderr must match golden baseline\nEXPECTED: {GOLDEN_TYPE_MISMATCH!r}\nACTUAL:   {result.stderr!r}"
    )


# endregion TEST_CLI_TYPE_MISMATCH
