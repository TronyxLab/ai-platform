#!/usr/bin/env python3
# GREP_SUMMARY: test sudoers-generator visudo template-render role-mapping batch-sudoers
# STRUCTURE: ┌6 test scenarios┐ → ◇ role→username mapping → ◇ rendered line parsing → ◇ template render mock → ◇ visudo validation → ◇ write sudoers file → ◇ batch generation
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/deploy/sudoers_generator.py
## @scope    Tests: _map_role_to_username, _parse_rendered_lines, _render_sudoers_rules,
##           _validate_with_visudo, _write_sudoers_file, generate_module_sudoers, _batch_generate_sudoers
## @invariants
##   - subprocess.run is mocked for template render and visudo calls
##   - temp files use tmp_path fixture (no hardcoded paths)
##   - Each test function includes caplog-based LDD trajectory [IMP:7-10]
##   - Every test function has # 🧪 TRAP[TEST] with Regression/Scenario/Last fail/Remove if
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Module under test
from core.internal.bootstrap.deploy.sudoers_generator import (
    _MAKE_BIN,
    _batch_generate_sudoers,
    _map_role_to_username,
    _parse_rendered_lines,
    _render_sudoers_rules,
    _validate_with_visudo,
    _write_sudoers_file,
    generate_module_sudoers,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_rendered_text() -> str:
    """Produce realistic rendered template output as produced by template_engine.render_template."""
    return """# AUTO-GENERATED — do not edit directly.
# Module: test-module

# owner — full control (ALL make targets)
owner make:start    /opt/platform/core/modules/test-module/Makefile
owner make:stop     /opt/platform/core/modules/test-module/Makefile
owner make:restart  /opt/platform/core/modules/test-module/Makefile
owner make:status   /opt/platform/core/modules/test-module/Makefile
owner make:logs     /opt/platform/core/modules/test-module/Makefile
owner make:backup   /opt/platform/core/modules/test-module/Makefile

# agent — status, logs, restart, backup — NO stop
agent make:status   /opt/platform/core/modules/test-module/Makefile
agent make:logs     /opt/platform/core/modules/test-module/Makefile
agent make:restart  /opt/platform/core/modules/test-module/Makefile
agent make:backup   /opt/platform/core/modules/test-module/Makefile

# ci — start, stop, restart, status, logs
ci make:start       /opt/platform/core/modules/test-module/Makefile
ci make:stop        /opt/platform/core/modules/test-module/Makefile
ci make:restart     /opt/platform/core/modules/test-module/Makefile
ci make:status      /opt/platform/core/modules/test-module/Makefile
ci make:logs        /opt/platform/core/modules/test-module/Makefile

# monitor — read-only status and logs
monitor make:status /opt/platform/core/modules/test-module/Makefile
monitor make:logs   /opt/platform/core/modules/test-module/Makefile
"""


@pytest.fixture
def sample_rules_expected() -> list[str]:
    """Expected parsed rules from sample_rendered_text (with {MODULE_DIR} placeholder)."""
    make = _MAKE_BIN
    return [
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} start",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} stop",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} backup",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} backup",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} start",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} stop",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform-monitor ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform-monitor ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
    ]


@pytest.fixture
def modules_dir(tmp_path: Path) -> Path:
    """Create a temporary modules directory with a test-module subdirectory."""
    mods = tmp_path / "modules"
    mods.mkdir(parents=True)
    (mods / "test-module").mkdir(parents=True)
    return mods


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a temporary templates directory with sudo-whitelist.template."""
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True)
    tmpl_file = tmpl / "sudo-whitelist.template"
    tmpl_file.write_text("""# Module: {{MODULE_NAME}}
owner make:start    {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:stop     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:restart  {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:status   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:logs     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
agent make:status   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
agent make:logs     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
ci make:start       {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
ci make:stop        {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
monitor make:status {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
monitor make:logs   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
""")
    return tmpl


# ── LDD trajectory helper ──────────────────────────────────────────────────


def _assert_ldd_trajectory(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 log trajectory and assert at least one IMP:9 log is present."""
    import sys

    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---", file=sys.stderr)
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message, file=sys.stderr)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---", file=sys.stderr)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# ── Tests: _map_role_to_username ────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: role→username mapping · Scenario: known roles map to correct usernames · Last fail: N/A · Remove if: role map is removed or redefined
def test_map_role_to_username_known_roles(caplog: pytest.LogCaptureFixture) -> None:
    """All known roles map to the correct system usernames."""
    caplog.set_level(logging.DEBUG)

    assert _map_role_to_username("owner") == "platform"
    assert _map_role_to_username("agent") == "platform-agent"
    assert _map_role_to_username("ci") == "ci-deploy"
    assert _map_role_to_username("monitor") == "platform-monitor"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: unknown role pass-through · Scenario: unrecognized role returns as-is · Last fail: N/A · Remove if: fallback behavior is changed
def test_map_role_to_username_unknown_role(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown roles are returned as-is (pass-through)."""
    caplog.set_level(logging.DEBUG)

    result = _map_role_to_username("custom-role")
    assert result == "custom-role"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: empty role fallback · Scenario: empty string maps to itself · Last fail: N/A · Remove if: input validation is added upstream
def test_map_role_to_username_empty(caplog: pytest.LogCaptureFixture) -> None:
    """Empty role string maps to empty string (pass-through)."""
    caplog.set_level(logging.DEBUG)

    result = _map_role_to_username("")
    assert result == ""

    _assert_ldd_trajectory(caplog)


# ── Tests: _parse_rendered_lines ────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: parse rendered template · Scenario: full template with all roles → all rules · Last fail: N/A · Remove if: parsing logic is replaced
def test_parse_rendered_lines_full(
    caplog: pytest.LogCaptureFixture,
    sample_rendered_text: str,
    sample_rules_expected: list[str],
) -> None:
    """Full rendered text produces correct sudoers rules for all roles."""
    caplog.set_level(logging.DEBUG)

    rules = _parse_rendered_lines(sample_rendered_text)

    assert len(rules) == len(sample_rules_expected)
    for expected, actual in zip(sample_rules_expected, rules, strict=False):
        assert actual == expected, f"Mismatch: expected={expected!r}, got={actual!r}"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: skip comments and blanks · Scenario: template with comments, blank lines → only non-comment make lines parsed · Last fail: N/A · Remove if: comment skipping logic is changed
def test_parse_rendered_lines_skips_comments_and_blanks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Comment lines and blank lines are correctly skipped."""
    caplog.set_level(logging.DEBUG)

    text = """
# This is a comment
# Another comment

owner make:start /path/Makefile

# Section header
agent make:status /path/Makefile


# Trailing comment
"""
    rules = _parse_rendered_lines(text)

    assert len(rules) == 2
    assert "platform ALL=(root) NOPASSWD: /usr/bin/make -C {MODULE_DIR} start" in rules
    assert "platform-agent ALL=(root) NOPASSWD: /usr/bin/make -C {MODULE_DIR} status" in rules

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: non-make actions skipped · Scenario: lines with non-make actions → not included · Last fail: N/A · Remove if: non-make action handling changes
def test_parse_rendered_lines_skips_non_make_actions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Lines with actions not starting with 'make:' are skipped."""
    caplog.set_level(logging.DEBUG)

    text = """owner make:start /path/Makefile
owner docker:exec /path
owner ALL /path
agent make:status /path/Makefile
"""
    rules = _parse_rendered_lines(text)

    # Only make: lines produce rules
    assert len(rules) == 2

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: malformed lines skipped · Scenario: lines with insufficient parts → skipped gracefully · Last fail: N/A · Remove if: parser input validation is added
def test_parse_rendered_lines_malformed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed lines (single token, empty role) are skipped without exception."""
    caplog.set_level(logging.DEBUG)

    text = """owner
 make:start /path/Makefile

owner make:valid /path/Makefile
"""
    rules = _parse_rendered_lines(text)

    assert len(rules) == 1

    _assert_ldd_trajectory(caplog)


# ── Tests: _render_sudoers_rules ────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: render rules with mock template · Scenario: mock _render_template → parsed + resolved rules · Last fail: N/A · Remove if: _render_sudoers_rules signature changes
def test_render_sudoers_rules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    sample_rendered_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_render_sudoers_rules renders template, parses, and resolves MODULE_DIR."""
    caplog.set_level(logging.DEBUG)

    # Monkeypatch _render_template to return sample_rendered_text directly
    import core.internal.bootstrap.deploy.sudoers_generator as sg

    def mock_render_template(
        module_name: str,
        templates_dir: Path,
        platform_root: str,
    ) -> str:
        return sample_rendered_text

    monkeypatch.setattr(sg, "_render_template", mock_render_template)

    rules = _render_sudoers_rules(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert len(rules) > 0
    # Verify MODULE_DIR placeholder is resolved to the actual module path
    module_abs_dir = str((modules_dir / "test-module").resolve())
    for rule in rules:
        assert "{MODULE_DIR}" not in rule, f"Unresolved placeholder in: {rule}"
        assert module_abs_dir in rule, f"Module path not resolved in: {rule}"

    # Verify all roles present
    rule_text = " ".join(rules)
    assert "platform ALL=(root)" in rule_text
    assert "platform-agent ALL=(root)" in rule_text
    assert "ci-deploy ALL=(root)" in rule_text
    assert "platform-monitor ALL=(root)" in rule_text

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: render rules with failed template · Scenario: _render_template returns None → empty list · Last fail: N/A · Remove if: error handling logic changes
def test_render_sudoers_rules_template_failure(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When template render fails, _render_sudoers_rules returns empty list."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    def mock_render_template_fail(
        module_name: str,
        templates_dir: Path,
        platform_root: str,
    ) -> None:
        return None

    monkeypatch.setattr(sg, "_render_template", mock_render_template_fail)

    rules = _render_sudoers_rules(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert rules == []

    _assert_ldd_trajectory(caplog)


# ── Tests: _validate_with_visudo ─────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: visudo validation passes · Scenario: subprocess.run returns 0 → True · Last fail: N/A · Remove if: validation logic is replaced
def test_validate_with_visudo_ok(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo -c returns 0, _validate_with_visudo returns True."""
    caplog.set_level(logging.DEBUG)

    with patch("core.internal.bootstrap.deploy.sudoers_generator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is True
        mock_run.assert_called_once_with(
            ["visudo", "-c", "-f", "/tmp/test-sudoers"],
            capture_output=True,
            text=True,
            timeout=15,
        )

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo validation fails · Scenario: subprocess.run returns 1 → False · Last fail: N/A · Remove if: validation error handling changes
def test_validate_with_visudo_fail(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo -c returns non-zero, _validate_with_visudo returns False."""
    caplog.set_level(logging.DEBUG)

    with patch("core.internal.bootstrap.deploy.sudoers_generator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr=">>> sudoers parse error: line 3",
        )

        result = _validate_with_visudo("/tmp/bad-sudoers")

        assert result is False

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo not found · Scenario: FileNotFoundError → True (pass-through) · Last fail: N/A · Remove if: fallback behavior changes
def test_validate_with_visudo_not_found(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo binary is not found, returns True (pass-through for dev/test)."""
    caplog.set_level(logging.DEBUG)

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator.subprocess.run",
        side_effect=FileNotFoundError("visudo not found"),
    ):
        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is True

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo timeout · Scenario: TimeoutExpired → False · Last fail: N/A · Remove if: timeout handling changes
def test_validate_with_visudo_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo times out, returns False."""
    caplog.set_level(logging.DEBUG)

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="visudo", timeout=15),
    ):
        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: _write_sudoers_file ──────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: write + validate + atomic rename · Scenario: visudo passes → file written with correct content and mode · Last fail: N/A · Remove if: _write_sudoers_file logic changes
def test_write_sudoers_file_ok(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """_write_sudoers_file writes, validates, and atomically renames sudoers file."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-test-module"
    rules = [
        "platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test start",
        "agent ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test status",
    ]

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator._validate_with_visudo",
        return_value=True,
    ):
        result = _write_sudoers_file(target, rules, "test-module")

    assert result is True
    assert target.exists(), "Target sudoers file was not created"

    # Verify content
    content = target.read_text()
    assert "# platform module sudoers — test-module" in content
    assert "platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test start" in content
    assert "agent ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test status" in content

    # Verify mode 0440 (owner read only) — on macOS tmp_path, root:root may not apply
    mode = os.stat(str(target)).st_mode & 0o777
    assert mode == 0o440, f"Expected 0440, got {oct(mode)}"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: write fails visudo → no file · Scenario: visudo fails → no target file created · Last fail: N/A · Remove if: error handling changes
def test_write_sudoers_file_visudo_fail(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """When visudo validation fails, no sudoers file is written."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-test-module"
    rules = ["bad rule syntax"]

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator._validate_with_visudo",
        return_value=False,
    ):
        result = _write_sudoers_file(target, rules, "test-module")

    assert result is False
    assert not target.exists(), "File should not exist when visudo fails"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: write with empty rules · Scenario: empty rules list → file still written · Last fail: N/A · Remove if: empty rules guard is added upstream
def test_write_sudoers_file_empty_rules(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """With empty rules list, file is still created with just the header."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-empty-module"

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator._validate_with_visudo",
        return_value=True,
    ):
        result = _write_sudoers_file(target, [], "empty-module")

    assert result is True
    assert target.exists()
    content = target.read_text()
    assert "# platform module sudoers — empty-module" in content

    _assert_ldd_trajectory(caplog)


# ── Tests: generate_module_sudoers ──────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: per-module sudoers generation · Scenario: full generate flow with mocks → success · Last fail: N/A · Remove if: generate_module_sudoers interface changes
def test_generate_module_sudoers_ok(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_module_sudoers succeeds when render and write are OK."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    # Mock _render_sudoers_rules to return fake rules
    fake_rules = [
        "platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test start",
        "agent ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test status",
    ]

    original_write = sg._write_sudoers_file
    write_args = {}

    def mock_write_sudoers_file(target_path, rules, module_name):
        write_args["target"] = target_path
        write_args["rules"] = rules
        write_args["module_name"] = module_name
        return original_write(target_path, rules, module_name)

    monkeypatch.setattr(
        sg,
        "_render_sudoers_rules",
        lambda mn, md, td, pr: fake_rules,
    )
    monkeypatch.setattr(
        sg,
        "_validate_with_visudo",
        lambda tmp: True,
    )

    # Override target path to use tmp_path instead of /etc/sudoers.d/
    modules_dir.parent / "sudoers.d" / "platform-test-module"
    monkeypatch.setattr(
        sg,
        "_write_sudoers_file",
        mock_write_sudoers_file,
    )

    # Since we monkeypatched _write_sudoers_file to not write anywhere,
    # let's directly test generate_module_sudoers with the tmp path

    # Actually, generate_module_sudoers hardcodes /etc/sudoers.d/ paths
    # via _write_sudoers_file. Let's just verify the logic flow.
    # We'll mock _write_sudoers_file to capture calls without writing to /etc.
    captured = {}

    def capturing_write(target_path, rules, module_name):
        captured["target"] = target_path
        captured["rules"] = rules
        captured["module_name"] = module_name
        return True

    monkeypatch.setattr(sg, "_write_sudoers_file", capturing_write)

    result = generate_module_sudoers(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is True
    assert captured["module_name"] == "test-module"
    assert len(captured["rules"]) == 2

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: generate with no rules · Scenario: _render_sudoers_rules returns empty → False · Last fail: N/A · Remove if: empty-guard logic changes
def test_generate_module_sudoers_no_rules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no rules are generated, generate_module_sudoers returns False."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    monkeypatch.setattr(
        sg,
        "_render_sudoers_rules",
        lambda mn, md, td, pr: [],
    )

    result = generate_module_sudoers(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: _batch_generate_sudoers ───────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: batch generate all modules · Scenario: multiple modules → one file with all rules · Last fail: N/A · Remove if: _batch_generate_sudoers logic changes
def test_batch_generate_sudoers_ok(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_batch_generate_sudoers collects rules from all modules and writes once."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    # Mock per-module render to return module-specific rules
    render_results = {
        "nginx": ["platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/nginx restart"],
        "postgres": ["platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/postgres start"],
    }

    def mock_render(module_name, modules_dir, templates_dir, platform_root):
        return render_results.get(module_name, [])

    monkeypatch.setattr(sg, "_render_sudoers_rules", mock_render)

    captured = {}

    def capturing_write(target_path, rules, module_name):
        captured["target"] = target_path
        captured["rules"] = rules
        captured["module_name"] = module_name
        return True

    monkeypatch.setattr(sg, "_write_sudoers_file", capturing_write)

    result = _batch_generate_sudoers(
        module_names=["nginx", "postgres"],
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is True
    assert captured["module_name"] == "platform-modules"
    assert len(captured["rules"]) == 2
    assert "/opt/modules/nginx" in captured["rules"][0]
    assert "/opt/modules/postgres" in captured["rules"][1]

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: batch with no modules · Scenario: empty module_names → True (no-op) · Last fail: N/A · Remove if: empty-list handling changes
def test_batch_generate_sudoers_no_modules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
) -> None:
    """Empty module list returns True without any writes."""
    caplog.set_level(logging.DEBUG)

    result = _batch_generate_sudoers(
        module_names=[],
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is True

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: batch all modules fail render · Scenario: all renders return empty → False · Last fail: N/A · Remove if: empty-collection guard changes
def test_batch_generate_sudoers_all_fail(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all module renders return no rules, batch returns False."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    monkeypatch.setattr(
        sg,
        "_render_sudoers_rules",
        lambda mn, md, td, pr: [],
    )

    result = _batch_generate_sudoers(
        module_names=["nginx", "postgres"],
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: integration — real _render_template with native template_engine render ──


# 🧪 TRAP[TEST] · Regression: _render_template native render · Scenario: real render via template_engine.render_template(dry_run=True) → str with substituted vars, no temp file · Last fail: N/A · Remove if: _render_template implementation changes
def test_render_template_native_render(
    caplog: pytest.LogCaptureFixture,
    templates_dir: Path,
) -> None:
    """_render_template renders via template_engine.render_template (native import) and returns rendered text."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    rendered = sg._render_template(
        module_name="test-module",
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert rendered is not None
    assert "owner make:start" in rendered
    assert "/opt/platform/core/modules/test-module/Makefile" in rendered
    # dry_run=True returns str directly — no placeholders left, no temp file involved
    assert "{{MODULE_NAME}}" not in rendered
    assert "{{PLATFORM_ROOT}}" not in rendered

    _assert_ldd_trajectory(caplog)


# ── Tests: CLI entrypoint ────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: CLI generate action · Scenario: --action generate with --module-name → exit 0 · Last fail: N/A · Remove if: CLI interface changes
def test_cli_generate_action(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI --action generate dispatches to generate_module_sudoers correctly."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "generate",
        "--module-name",
        "test-module",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    called_action = {"name": None}

    def mock_generate(module_name, modules_dir, templates_dir, platform_root):
        called_action["name"] = module_name
        return True

    monkeypatch.setattr(sg, "generate_module_sudoers", mock_generate)
    monkeypatch.setattr(sg.sys, "argv", test_args)

    assert sg.main() == 0
    assert called_action["name"] == "test-module"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI render-rules action · Scenario: --action render-rules → prints rules to stdout · Last fail: N/A · Remove if: CLI interface changes
def test_cli_render_rules_action(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI --action render-rules prints rules to stdout."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "render-rules",
        "--module-name",
        "test-module",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    fake_rules = ["rule1", "rule2"]
    monkeypatch.setattr(sg, "_render_sudoers_rules", lambda mn, md, td, pr: fake_rules)
    monkeypatch.setattr(sg.sys, "argv", test_args)

    assert sg.main() == 0

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI batch-generate action · Scenario: --action batch-generate with --module-names → exit 0 · Last fail: N/A · Remove if: CLI interface changes
def test_cli_batch_generate_action(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI --action batch-generate dispatches to _batch_generate_sudoers correctly."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "batch-generate",
        "--module-names",
        "nginx,postgres,redis",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    called_names = {"names": None}
    monkeypatch.setattr(
        sg, "_batch_generate_sudoers", lambda mn, md, td, pr: called_names.update({"names": mn}) or True
    )
    monkeypatch.setattr(sg.sys, "argv", test_args)

    assert sg.main() == 0
    assert called_names["names"] == ["nginx", "postgres", "redis"]

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI missing required args · Scenario: --action generate without --module-name → exit 1 · Last fail: N/A · Remove if: CLI argument validation changes
def test_cli_missing_module_name(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """CLI exits with 1 when --module-name is missing for generate action."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "generate",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]
    monkeypatch.setattr(sg.sys, "argv", test_args)

    assert sg.main() == 1

    _assert_ldd_trajectory(caplog)
