"""
# GREP_SUMMARY: test-node-yaml-cli-get-many, node_yaml, --get-many, batch, alias, dotted-key, TAB-separator, ConfigValidationError, exit-4, bootstrap, U-52
# STRUCTURE: ┌tmp_path node.yaml fixtures → ◇ 6 scenarios ∋ (batch-values / missing-key-empty / broken-spec-exit4 / empty-spec-exit4 / context-priority / value-with-spaces) → ⎋ assert TAB-output + exit codes + LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for node_yaml.py --get-many CLI batch extraction (DevPlan 116 B3 T5, U-52).
##           Native imports — calls _cli_get_many() directly (no subprocess); capsys for stdout.
## @scope    Tests: alias:dotted-key spec parsing, TAB-separated output, empty value for missing key
##           (exit 0), malformed/empty spec → ConfigValidationError (exit 4 in main()), and the
##           context fallback priority (top-level context vs contexts.0.name).
## @invariants
##   - Missing key → "alias<TAB>" (empty value) exit 0 — shell-compatible (like --default "")
##   - Malformed/empty spec → ConfigValidationError (main() maps to exit 4, fail-fast)
##   - Values may contain spaces — TAB separator preserved
##   - Native import only — no subprocess (tests/AGENTS.md native-imports rule)
## @rationale  U-52: bootstrap.sh made 6 per-field --get calls — --get-many batches them into ONE
##             python3 process with TAB-separated output for `while IFS=$'\t' read`.
## @changes  2026-08-01 · Created (DevPlan 116 B3 T5)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import write_yaml

logger = logging.getLogger(__name__)

# Repo root on sys.path via conftest — native import of the module under test
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml, _cli_get_many

pytestmark = pytest.mark.static_audit

# ═══════════════════════════════════════════════════════════════════
# Helpers / fixtures
# ═══════════════════════════════════════════════════════════════════


# T2.16b: _write_yaml консолидирован в gate_helpers.write_yaml(path, data)


@pytest.fixture
def rich_node_yaml(tmp_path: Path) -> str:
    """node.yaml with node.owner_key, node.ci_deploy_key, domain, context AND contexts[0].name.

    Written to a dedicated subdir — tests may also write plain node.yaml files in tmp_path
    without clobbering this fixture (NodeYaml is lazy — reads happen on first access).
    """
    fixture_dir = tmp_path / "rich"
    fixture_dir.mkdir(exist_ok=True)
    p = fixture_dir / "node.yaml"
    p.write_text(
        """\
node:
  name: test-node
  owner_key: "ssh-rsa AAAA owner-key-value"
  ci_deploy_key: "ssh-rsa BBBB ci-deploy-key-value"
  host: "1.2.3.4"
domain: "example.com"
context: "primary-ctx"
contexts:
  - name: "contexts-zero-ctx"
"""
    )
    return str(p)


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · alias<TAB>value output (DevPlan 116 B3 T5, U-52)
# · Scenario: batch spec of present keys → 3 TAB-separated lines, values with spaces intact
# · Last fail: N/A (new test)
# · Remove if: --get-many output format changes
def test_get_many_returns_alias_tab_value(caplog: pytest.LogCaptureFixture, rich_node_yaml: str, capsys) -> None:
    """--get-many spec returns alias<TAB>value lines (TAB separator)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing batch extraction of present keys")

    node = NodeYaml(rich_node_yaml)
    spec = "owner_key:node.owner_key,ci_deploy_key:node.ci_deploy_key,platform_domain:domain"
    rc = _cli_get_many(node, spec)
    captured = capsys.readouterr()

    assert rc == 0, f"Expected exit 0, got {rc}"
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert len(lines) == 3, f"Expected 3 output lines, got {lines}"
    assert lines[0] == "owner_key\tssh-rsa AAAA owner-key-value", f"Unexpected line: {lines[0]!r}"
    assert lines[1] == "ci_deploy_key\tssh-rsa BBBB ci-deploy-key-value", f"Unexpected line: {lines[1]!r}"
    assert lines[2] == "platform_domain\texample.com", f"Unexpected line: {lines[2]!r}"
    # TAB separator preserved — values with spaces are NOT split
    logger.info("[IMP:9][test_get_many] Batch extraction returned alias<TAB>value lines")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · missing key → empty value, exit 0 (DevPlan 116 B3 T5)
# · Scenario: absent dotted key → 'alias<TAB>' line, rc=0 (shell-compatible, mirrors --default "")
# · Last fail: N/A (new test)
# · Remove if: --get-many missing-key semantics change
def test_get_many_missing_key_empty_value_exit0(caplog: pytest.LogCaptureFixture, rich_node_yaml: str, capsys) -> None:
    """Missing key → empty value line, exit 0 (shell-compatible, mirrors --default \"\")."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing missing key → empty value, exit 0")

    node = NodeYaml(rich_node_yaml)
    spec = "owner_key:node.owner_key,nonexistent:node.does_not_exist"
    rc = _cli_get_many(node, spec)
    captured = capsys.readouterr()

    assert rc == 0, f"Missing key must NOT fail the batch — expected exit 0, got {rc}"
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert lines[0] == "owner_key\tssh-rsa AAAA owner-key-value"
    assert lines[1] == "nonexistent\t", f"Expected empty value line, got {lines[1]!r}"
    logger.info("[IMP:9][test_get_many] Missing key → 'alias<TAB>' empty line, exit 0")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · malformed spec → ConfigValidationError (DevPlan 116 B3 T5)
# · Scenario: entry without ':' → ConfigValidationError (main() exit 4, fail-fast)
# · Last fail: N/A (new test)
# · Remove if: --get-many spec validation changes
def test_get_many_broken_spec_exit4(caplog: pytest.LogCaptureFixture, rich_node_yaml: str) -> None:
    """Malformed spec entry (no ':') → ConfigValidationError (main() → exit 4, fail-fast)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing malformed spec entry")

    node = NodeYaml(rich_node_yaml)
    with pytest.raises(ConfigValidationError, match="malformed entry"):
        _cli_get_many(node, "owner_key:node.owner_key,badentry-without-colon")
    logger.info("[IMP:9][test_get_many] Malformed spec rejected with ConfigValidationError (exit 4 in main)")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · empty spec → ConfigValidationError (DevPlan 116 B3 T5)
# · Scenario: whitespace-only spec → ConfigValidationError (exit 4)
# · Last fail: N/A (new test)
# · Remove if: --get-many spec validation changes
def test_get_many_empty_spec_exit4(caplog: pytest.LogCaptureFixture, rich_node_yaml: str) -> None:
    """Empty/whitespace spec → ConfigValidationError (exit 4, fail-fast)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing empty spec")

    node = NodeYaml(rich_node_yaml)
    with pytest.raises(ConfigValidationError, match="empty"):
        _cli_get_many(node, "   ")
    logger.info("[IMP:9][test_get_many] Empty spec rejected with ConfigValidationError (exit 4 in main)")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · context priority (DevPlan 116 B3 T5)
# · Scenario: top-level context wins over contexts.0.name; contexts.0.name as fallback
# · Last fail: N/A (new test — contexts.0.name required list-index traversal)
# · Remove if: context fallback contract changes
def test_get_many_context_priority(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, capsys, rich_node_yaml: str
) -> None:
    """context (top-level) wins over contexts.0.name — bootstrap fallback contract."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing context priority (top-level > contexts.0.name)")

    # node.yaml WITHOUT top-level context — only contexts[0].name (fallback case)
    p = write_yaml(
        tmp_path / "node.yaml",
        """\
node:
  name: test-node
  owner_key: "ssh-rsa AAAA key"
contexts:
  - name: "only-contexts-zero"
""",
    )
    node = NodeYaml(str(p))

    spec = "context:context,context0:contexts.0.name"
    rc = _cli_get_many(node, spec)
    captured = capsys.readouterr()
    assert rc == 0
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert lines[0] == "context\t", f"top-level context absent → empty, got {lines[0]!r}"
    assert lines[1] == "context0\tonly-contexts-zero", f"contexts.0.name fallback, got {lines[1]!r}"

    # Rich node.yaml (both present) — top-level context value returned directly
    node2 = NodeYaml(rich_node_yaml)
    rc2 = _cli_get_many(node2, "context:context,context0:contexts.0.name")
    captured2 = capsys.readouterr()
    assert rc2 == 0
    lines2 = [ln for ln in captured2.out.splitlines() if ln]
    assert lines2[0] == "context\tprimary-ctx", f"top-level context must win, got {lines2[0]!r}"
    logger.info("[IMP:9][test_get_many] Context priority verified: top-level > contexts.0.name")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · non-dict traversal tolerance (DevPlan 116 B3 T5)
# · Scenario: contexts as list-of-str → contexts.0.name degrades to empty value, exit 0
# · Last fail: N/A (new test)
# · Remove if: --get-many traversal semantics change
def test_get_many_non_dict_traversal_empty(caplog: pytest.LogCaptureFixture, tmp_path: Path, capsys) -> None:
    """Traversal into non-dict (e.g. contexts.0.name when contexts is a list of str) → empty, exit 0."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_get_many] Testing non-dict traversal tolerance")

    p = write_yaml(
        tmp_path / "node.yaml",
        """\
node:
  name: test-node
  owner_key: "ssh-rsa AAAA key"
contexts:
  - first
""",
    )
    node = NodeYaml(str(p))
    rc = _cli_get_many(node, "context0:contexts.0.name")
    captured = capsys.readouterr()
    assert rc == 0, f"Non-dict traversal must degrade to empty value (exit 0), got {rc}"
    assert captured.out.splitlines() == ["context0\t"], f"Expected empty value, got {captured.out!r}"
    logger.info("[IMP:9][test_get_many] Non-dict traversal → empty value, exit 0")
