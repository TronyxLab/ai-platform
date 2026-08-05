#!/usr/bin/env python3
# GREP_SUMMARY: test-node-yaml-cli get get-many resolve typed-json validate mutation find-project domain-config
# STRUCTURE: ┌14 test functions┐ → ◇ --get (2) → ◇ --get-many (2) → ◇ --resolve (1) → ◇ --typed-contexts (1)
#            → ◇ --domain-config (1) → ◇ --find-project (2) → ◇ mutation (2) → ◇ --validate (2) → ◇ exit-code mapping (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/node_yaml_cli.py — CLI commands extracted from
##           node_yaml.py (DevPlan 117 G T51). Tests exercise each _cli_* function via mock NodeYaml.
## @scope    No real node.yaml parsing needed — NodeYaml methods mocked per command.
## @invariants
##   - All tests use capsys (stdout/stderr capture) + mock NodeYaml
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T51 §TEST_SPEC — node_yaml_cli direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T51 — created
# endregion MODULE_CONTRACT

import json
from unittest import mock

import pytest

from core.internal.shared import node_yaml_cli
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)


@pytest.fixture
def mock_node():
    """A NodeYaml instance with all methods mocked."""
    node = mock.MagicMock()
    node.raw.return_value = {"contexts": [{"name": "myorg"}], "node": {"host": "10.0.0.1"}}
    node.get_context.return_value = "myorg"
    node.get_domain_config.return_value = mock.MagicMock(
        platform_domain="platform.example.com",
        email="admin@example.com",
        acme_dns_plugin="dns_webnames",
        project_domains=["app.example.com"],
    )
    node.get_projects.return_value = [
        {"name": "app1", "domain": "app1.example.com"},
        {"name": "app2", "domain": "app2.example.com"},
    ]
    return node


# ══════════════════════════════════════════════════════════════════════
# TESTS: --get
# ══════════════════════════════════════════════════════════════════════


class TestCliGet:
    """Tests for _cli_get()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: --get with existing key
    # · Expect: value printed, exit 0
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get logic changes
    def test_cli_get_returns_value(self, mock_node, capsys) -> None:
        """--get with existing key → stdout value, exit 0."""
        node = mock.MagicMock()
        node.get.return_value = "10.0.0.1"
        args = mock.MagicMock(get="node.host", default=None, items=False)

        result = node_yaml_cli._cli_get(node, args)

        assert result == 0
        assert capsys.readouterr().out.strip() == "10.0.0.1"

    # 🧪 TRAP[TEST] · Regression · Scenario: --get with missing key
    # · Expect: exit 1 + stderr message
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get logic changes
    def test_cli_get_missing_key_exit1(self, capsys) -> None:
        """--get with missing key → exit 1 (shell || compatibility)."""
        node = mock.MagicMock()
        node.get.side_effect = ConfigValidationError("not found")
        args = mock.MagicMock(get="nope", default=None, items=False)

        result = node_yaml_cli._cli_get(node, args)

        assert result == 1
        assert "Key not found: nope" in capsys.readouterr().err

    # 🧪 TRAP[TEST] · Regression · Scenario: --get --items with list value
    # · Expect: JSON array on stdout
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get items logic changes
    def test_cli_get_items_json(self, capsys) -> None:
        """--items with list → JSON array output."""
        node = mock.MagicMock()
        node.get.return_value = ["a", "b"]
        args = mock.MagicMock(get="modules", default=None, items=True)

        result = node_yaml_cli._cli_get(node, args)

        assert result == 0
        assert json.loads(capsys.readouterr().out) == ["a", "b"]


# ══════════════════════════════════════════════════════════════════════
# TESTS: --get-many
# ══════════════════════════════════════════════════════════════════════


class TestCliGetMany:
    """Tests for _cli_get_many()."""

    # 🧪 TRAP[TEST] · Regression · Scenario: valid batch spec
    # · Expect: TAB-separated alias:value lines
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get_many logic changes
    def test_cli_get_many_batch(self, capsys) -> None:
        """Valid spec → alias<TAB>value lines."""
        node = mock.MagicMock()
        node.raw.return_value = {"contexts": [{"name": "myorg"}]}

        result = node_yaml_cli._cli_get_many(node, "org:contexts.0.name,missing:nope.deep")

        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line]
        assert "org\tmyorg" in lines
        assert "missing\t" in lines  # missing key → empty value, exit 0
        assert result == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: empty spec
    # · Expect: ConfigValidationError (exit 4 mapping in main)
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get_many validation changes
    def test_cli_get_many_empty_spec_exit4(self, mock_node) -> None:
        """Empty spec → ConfigValidationError."""
        with pytest.raises(ConfigValidationError):
            node_yaml_cli._cli_get_many(mock_node, "   ")

    # 🧪 TRAP[TEST] · Regression · Scenario: malformed entry (no colon)
    # · Expect: ConfigValidationError
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_get_many validation changes
    def test_cli_get_many_malformed_entry(self, mock_node) -> None:
        """Entry without ':' → ConfigValidationError."""
        with pytest.raises(ConfigValidationError):
            node_yaml_cli._cli_get_many(mock_node, "alias-only-no-colon")

    # 🧐 TRAP[DECISION] · 2026-08-05 · — · _traverse_dotted_list_aware: private-тест удалён (DevPlan 139 W2)
    # · Rejected: поднять traversal-helper в публичный API (расширение поверхности без потребителя)
    # · Reason: деталь реализации — dotted-обход с list-index. Наблюдаемый публичный контракт
    # ·   (missing key / non-dict / list-index деградация → пустое значение, exit 0) покрыт
    # ·   через публичный путь --get-many в test_node_yaml_cli_get_many.py
    # ·   (test_get_many_context_priority / test_get_many_non_dict_traversal_empty).
    # ·   Внутреннее raise ConfigValidationError — контракт хелпера, caller деградирует.
    # · Rev: при появлении прямого потребителя traversal-семантики — поднять в публичную функцию


# ══════════════════════════════════════════════════════════════════════
# TESTS: --resolve / --domain-config / --typed-contexts / --find-project
# ══════════════════════════════════════════════════════════════════════


class TestCliMisc:
    """Tests for remaining CLI commands."""

    # 🧪 TRAP[TEST] · Regression · Scenario: --resolve finds node.yaml
    # · Expect: path printed, exit 0
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_resolve logic changes
    def test_cli_resolve_prints_path(self, capsys) -> None:
        """--resolve → stdout path, exit 0."""
        resolved = mock.MagicMock(_path="/opt/node-configs/foo/node.yaml")
        with mock.patch.object(node_yaml_cli.NodeYaml, "resolve", return_value=resolved):
            args = mock.MagicMock(resolve_node="foo")
            result = node_yaml_cli._cli_resolve(args)

        assert result == 0
        assert capsys.readouterr().out.strip() == "/opt/node-configs/foo/node.yaml"

    # 🧪 TRAP[TEST] · Regression · Scenario: --resolve not found
    # · Expect: exit 2 (ConfigNotFoundError)
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_resolve error mapping changes
    def test_cli_resolve_not_found_exit2(self, capsys) -> None:
        """--resolve ConfigNotFoundError → exit 2."""
        with mock.patch.object(node_yaml_cli.NodeYaml, "resolve", side_effect=ConfigNotFoundError("no node.yaml")):
            args = mock.MagicMock(resolve_node="nope")
            result = node_yaml_cli._cli_resolve(args)

        assert result == 2
        assert "no node.yaml" in capsys.readouterr().err

    # 🧪 TRAP[TEST] · NEGATIVE (R5) · B3 — --typed-* флаги удалены (unknown flag → exit≠0)
    # · Scenario: main() с --file и --typed-contexts → argparse SystemExit (код ≠ 0),
    #   т.к. флаг больше не зарегистрирован в _build_arg_parser (волна 118 B3)
    # · Last fail: --typed-* существовал до волны 118 B3 (node_yaml_cli.py L89-95)
    # · Remove if: typed-* CLI будет восстановлен
    def test_cli_typed_flags_removed(self, tmp_path, capsys) -> None:
        """B3 R5: --typed-contexts → argparse error (exit≠0)."""
        yaml_path = tmp_path / "node.yaml"
        yaml_path.write_text("node:\n  name: test\n  host: 1.2.3.4\ncontexts: []\n")
        with (
            mock.patch.object(
                node_yaml_cli.sys,
                "argv",
                ["node_yaml", "--file", str(yaml_path), "--typed-contexts"],
            ),
            pytest.raises(SystemExit) as excinfo,
        ):
            node_yaml_cli.main()
        assert excinfo.value.code != 0, "B3 FAIL: --typed-contexts должен быть rejected (removed API)"

    # 🧪 TRAP[TEST] · Regression · Scenario: --domain-config (default field:value)
    # · Expect: field:value lines (legacy format, backward compat)
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_domain_config logic changes
    def test_cli_domain_config_lines(self, mock_node, capsys) -> None:
        """--domain-config → field:value lines (default format)."""
        args = mock.MagicMock(format="field:value")
        result = node_yaml_cli._cli_domain_config(mock_node, args)

        out = capsys.readouterr().out
        assert result == 0
        assert "platform_domain:platform.example.com" in out
        assert "email:admin@example.com" in out
        assert "acme_dns_plugin:dns_webnames" in out
        assert "project_domains:app.example.com" in out

    # 🧪 TRAP[TEST] · Regression · Scenario: --domain-config --format lines (DevPlan 118 E12, D18)
    # · Expect: 4 bare value lines in fixed order (platform_domain, email, acme_dns_plugin, project_domains)
    # · Last fail: N/A — new test for E12 (replaces shell grep|cut re-parsing in issue-cert.sh:600-619)
    # · Remove if: --format lines output contract changes
    def test_cli_domain_config_format_lines(self, mock_node, capsys) -> None:
        """--domain-config --format lines → 4 bare value lines, 0 field: prefix."""
        args = mock.MagicMock(format="lines")
        result = node_yaml_cli._cli_domain_config(mock_node, args)

        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert result == 0
        assert len(lines) == 4, f"Expected 4 bare lines, got {lines!r}"
        assert lines[0] == "platform.example.com", f"line[0] must be platform_domain, got {lines[0]!r}"
        assert lines[1] == "admin@example.com", f"line[1] must be email, got {lines[1]!r}"
        assert lines[2] == "dns_webnames", f"line[2] must be acme_dns_plugin, got {lines[2]!r}"
        assert lines[3] == "app.example.com", f"line[3] must be project_domains (space-joined), got {lines[3]!r}"
        assert "platform_domain:" not in out, "No field: prefix allowed in --format lines mode"
        assert "email:" not in out, "No field: prefix allowed in --format lines mode"

    # 🧪 TRAP[TEST] · Regression · Scenario: --find-project hit
    # · Expect: JSON + ___ORG___ + ___HOST___ markers
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_find_project logic changes
    def test_cli_find_project_hit(self, mock_node, capsys) -> None:
        """--find-project app1 → JSON + org + host markers."""
        mock_node.get_context.return_value = "myorg"
        mock_node.get_node_info.return_value = mock.MagicMock(fqdn="app1.example.com")

        result = node_yaml_cli._cli_find_project(mock_node, "app1")

        out = capsys.readouterr().out
        assert result == 0
        assert "___ORG___myorg" in out
        assert "___HOST___app1.example.com" in out

    # 🧪 TRAP[TEST] · Regression · Scenario: --find-project miss
    # · Expect: exit 1
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_find_project logic changes
    def test_cli_find_project_miss(self, mock_node, capsys) -> None:
        """--find-project nope → exit 1."""
        result = node_yaml_cli._cli_find_project(mock_node, "nope")

        assert result == 1
        assert "Project not found: nope" in capsys.readouterr().err


# ══════════════════════════════════════════════════════════════════════
# TESTS: --validate / --validate-schema / main() exit codes
# ══════════════════════════════════════════════════════════════════════


class TestCliValidateAndMain:
    """Tests for validate commands and main() exit-code mapping."""

    # 🧪 TRAP[TEST] · Regression · Scenario: --validate with errors
    # · Expect: ERROR lines on stderr, exit == len(errors)
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_validate logic changes
    def test_cli_validate_errors(self, capsys) -> None:
        """--validate with 2 errors → exit 2, ERROR lines."""
        node = mock.MagicMock()
        node.validate.return_value = ["err1", "err2"]

        result = node_yaml_cli._cli_validate(node)

        err = capsys.readouterr().err
        assert result == 2
        assert "ERROR: err1" in err
        assert "ERROR: err2" in err

    # 🧪 TRAP[TEST] · Regression · Scenario: --validate clean
    # · Expect: exit 0
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: _cli_validate logic changes
    def test_cli_validate_clean(self, capsys) -> None:
        """--validate with 0 errors → exit 0."""
        node = mock.MagicMock()
        node.validate.return_value = []

        result = node_yaml_cli._cli_validate(node)

        assert result == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: main() ConfigValidationError mapping
    # · Expect: exit 4
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: main() exception mapping changes
    def test_main_validation_error_exit4(self, capsys, tmp_path) -> None:
        """main() with validation failure during dispatch → exit 4."""
        yaml_file = tmp_path / "node.yaml"
        yaml_file.write_text("contexts: []\n", encoding="utf-8")

        with (
            mock.patch(
                "core.internal.shared.node_yaml_cli.sys.argv", ["node_yaml", "--get", "x", "--file", str(yaml_file)]
            ),
            mock.patch.object(node_yaml_cli.NodeYaml, "__init__", return_value=None),
            mock.patch(
                "core.internal.shared.node_yaml_cli._cli_get",
                side_effect=ConfigValidationError("bad value"),
            ),
        ):
            rc = node_yaml_cli.main()

        assert rc == 4
        assert "bad value" in capsys.readouterr().err

    # 🧪 TRAP[TEST] · Regression · Scenario: main() ConfigNotFoundError mapping
    # · Expect: exit 2
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: main() exception mapping changes
    def test_main_config_not_found_exit2(self, capsys, tmp_path) -> None:
        """main() with missing node.yaml → exit 2."""
        with (
            mock.patch(
                "core.internal.shared.node_yaml_cli.sys.argv",
                ["node_yaml", "--get", "x", "--file", str(tmp_path / "missing.yaml")],
            ),
            mock.patch.object(
                node_yaml_cli.NodeYaml,
                "__init__",
                side_effect=ConfigNotFoundError("missing"),
            ),
        ):
            rc = node_yaml_cli.main()

        assert rc == 2
        assert "missing" in capsys.readouterr().err

    # 🧪 TRAP[TEST] · Regression · Scenario: main() ConfigParseError mapping
    # · Expect: exit 3
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: main() exception mapping changes
    def test_main_config_parse_exit3(self, capsys, tmp_path) -> None:
        """main() with malformed node.yaml → exit 3."""
        with (
            mock.patch(
                "core.internal.shared.node_yaml_cli.sys.argv",
                ["node_yaml", "--get", "x", "--file", str(tmp_path / "bad.yaml")],
            ),
            mock.patch.object(
                node_yaml_cli.NodeYaml,
                "__init__",
                side_effect=ConfigParseError("yaml error"),
            ),
        ):
            rc = node_yaml_cli.main()

        assert rc == 3
        assert "yaml error" in capsys.readouterr().err

    # 🧪 TRAP[TEST] · Regression · Scenario: main() --resolve path (no --file)
    # · Expect: exit 0, resolve called
    # · Last fail: None (new test for DevPlan 117 G T51)
    # · Remove if: main() resolve dispatch changes
    def test_main_resolve_no_file(self, capsys) -> None:
        """main() --resolve without --file → exit 0."""
        with (
            mock.patch(
                "core.internal.shared.node_yaml_cli.sys.argv", ["node_yaml", "--resolve", "--resolve-node", "foo"]
            ),
            mock.patch(
                "core.internal.shared.node_yaml_cli._cli_resolve",
                return_value=0,
            ) as mock_resolve,
        ):
            rc = node_yaml_cli.main()

        assert rc == 0
        mock_resolve.assert_called_once()
