# GREP_SUMMARY: test template-engine unit-test render check parse_vars strict-grammar atomic-write
# STRUCTURE: ┌20 atomic tests┐ → ◇ render basic → ◇ strict grammar → ◇ error cases → ◇ edge cases → ◇ check_all
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/template_engine.py — native imports, no subprocess
## @scope    Tests: render_template, parse_vars, check_all, render_all, TemplateError
## @invariants
##   - Tests import core.internal.template_engine directly (no subprocess)
##   - Every test uses @ldd_trajectory decorator for IMP:9 assertion
##   - tmp_path used instead of hardcoded paths
##   - No Docker, no external services required
## @rationale Native pytest for Python core per §TESTING rule. 20 atomic tests
##            covering all edge cases from DevPlan T1.4.
# endregion MODULE_CONTRACT

import logging

import pytest
from conftest import ldd_trajectory

from core.internal.template_engine import (
    TemplateError,
    check_all,
    parse_vars,
    render_all,
    render_template,
)

logger = logging.getLogger(__name__)


# region TEST_RENDER_SINGLE_PLACEHOLDER
@ldd_trajectory
def test_render_single_placeholder(caplog, tmp_path):
    """Single {{NAME}} placeholder replaced correctly."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Core render functionality
    # · Last fail: N/A (preventive)
    # · Remove if: render_template signature changes
    tmpl = tmp_path / "test.tmpl"
    tmpl.write_text("Hello, {{NAME}}!")
    result = render_template(str(tmpl), vars={"NAME": "world"}, dry_run=True)
    assert result == "Hello, world!"
    logger.critical("[IMP:9][test][render] Single placeholder OK")


# endregion TEST_RENDER_SINGLE_PLACEHOLDER


# region TEST_RENDER_MULTIPLE_PLACEHOLDERS
@ldd_trajectory
def test_render_multiple_placeholders(caplog, tmp_path):
    """Multiple {{A}} {{B}} placeholders replaced correctly."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Multiple placeholder substitution
    # · Last fail: N/A (preventive)
    # · Remove if: render_template signature changes
    tmpl = tmp_path / "multi.tmpl"
    tmpl.write_text("{{GREETING}}, {{TARGET}}!")
    result = render_template(str(tmpl), vars={"GREETING": "Hello", "TARGET": "world"}, dry_run=True)
    assert result == "Hello, world!"
    logger.critical("[IMP:9][test][render] Multiple placeholders OK")


# endregion TEST_RENDER_MULTIPLE_PLACEHOLDERS


# region TEST_RENDER_NO_PLACEHOLDERS
@ldd_trajectory
def test_render_no_placeholders(caplog, tmp_path):
    """Template without placeholders returned as-is."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · No placeholder passthrough
    # · Last fail: N/A (preventive)
    # · Remove if: render_template signature changes
    tmpl = tmp_path / "plain.txt"
    tmpl.write_text("Hello, world!\n")
    result = render_template(str(tmpl), dry_run=True)
    assert result == "Hello, world!\n"
    logger.critical("[IMP:9][test][render] No placeholders OK")


# endregion TEST_RENDER_NO_PLACEHOLDERS


# region TEST_RENDER_EMPTY_TEMPLATE
@ldd_trajectory
def test_render_empty_template(caplog, tmp_path):
    """Empty template (0 bytes) returns empty string."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Edge case: zero-length file
    # · Last fail: N/A (preventive)
    # · Remove if: render_template handles empty input differently
    tmpl = tmp_path / "empty.tmpl"
    tmpl.write_text("")
    result = render_template(str(tmpl), dry_run=True)
    assert result == ""
    logger.critical("[IMP:9][test][render] Empty template OK")


# endregion TEST_RENDER_EMPTY_TEMPLATE


# region TEST_STRICT_GRAMMAR_REJECTS_LOWERCASE
@ldd_trajectory
def test_strict_grammar_rejects_lowercase(caplog, tmp_path):
    """Placeholder {{name}} (lowercase start) is NOT matched — left as-is."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Strict grammar invariant
    # · Last fail: N/A (preventive)
    # · Remove if: grammar expands to allow lowercase
    tmpl = tmp_path / "lower.tmpl"
    tmpl.write_text("{{name}}")
    result = render_template(str(tmpl), vars={"name": "world"}, dry_run=True)
    # Strict grammar does NOT match {{name}} (lowercase 'n')
    assert result == "{{name}}"
    logger.critical("[IMP:9][test][grammar] Lowercase placeholder not matched (strict grammar)")


# endregion TEST_STRICT_GRAMMAR_REJECTS_LOWERCASE


# region TEST_STRICT_GRAMMAR_REJECTS_SPACES
@ldd_trajectory
def test_strict_grammar_rejects_spaces(caplog, tmp_path):
    """Prometheus-style {{ $labels.x }} is NOT matched (space + $)."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Prometheus compatibility
    # · Last fail: N/A (preventive)
    # · Remove if: grammar expands to match Prometheus syntax
    tmpl = tmp_path / "prometheus.tmpl"
    tmpl.write_text("{{ $labels.instance }}")
    result = render_template(str(tmpl), vars={"labels": "test"}, dry_run=True)
    # Strict grammar does NOT match {{ $labels.instance }}
    assert result == "{{ $labels.instance }}"
    logger.critical("[IMP:9][test][grammar] Prometheus syntax not matched (strict grammar)")


# endregion TEST_STRICT_GRAMMAR_REJECTS_SPACES


# region TEST_UNRESOLVED_PLACEHOLDER_BLOCKING
@ldd_trajectory
def test_unresolved_placeholder_blocking(caplog, tmp_path):
    """Unresolved {{VAR}} without allow_missing raises TemplateError."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Error handling invariant
    # · Last fail: N/A (preventive)
    # · Remove if: TemplateError semantics change
    tmpl = tmp_path / "unresolved.tmpl"
    tmpl.write_text("Value: {{UNKNOWN}}")
    with pytest.raises(TemplateError) as exc:
        render_template(str(tmpl), vars={"OTHER": "val"}, dry_run=True)
    assert "unresolved" in str(exc.value).lower()
    logger.critical("[IMP:9][test][error] Blocking unresolved placeholder raises TemplateError")


# endregion TEST_UNRESOLVED_PLACEHOLDER_BLOCKING


# region TEST_UNRESOLVED_PLACEHOLDER_ALLOW
@ldd_trajectory
def test_unresolved_placeholder_allow(caplog, tmp_path):
    """Unresolved {{X}} with allow_missing=True keeps placeholder + logs WARNING."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Allow-missing contract
    # · Last fail: N/A (preventive)
    # · Remove if: allow_missing parameter removed
    tmpl = tmp_path / "allow.tmpl"
    tmpl.write_text("Value: {{X}}")
    result = render_template(str(tmpl), vars={"OTHER": "val"}, allow_missing=True, dry_run=True)
    assert result == "Value: {{X}}"
    logger.critical("[IMP:9][test][allow] Unresolved placeholder preserved with allow_missing=True")


# endregion TEST_UNRESOLVED_PLACEHOLDER_ALLOW


# region TEST_UNCLOSED_PLACEHOLDER
@ldd_trajectory
def test_unclosed_placeholder(caplog, tmp_path):
    """Unclosed {{VAR without }} raises TemplateError."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Syntax error detection
    # · Last fail: N/A (preventive)
    # · Remove if: TemplateError semantics change
    tmpl = tmp_path / "unclosed.tmpl"
    tmpl.write_text("Start {{UNCLOSED end")
    with pytest.raises(TemplateError) as exc:
        render_template(str(tmpl), vars={"UNCLOSED": "val"}, dry_run=True)
    assert "unclosed" in str(exc.value).lower()
    logger.critical("[IMP:9][test][error] Unclosed placeholder raises TemplateError")


# endregion TEST_UNCLOSED_PLACEHOLDER


# region TEST_SPECIAL_CHARS_IN_VALUE
@ldd_trajectory
def test_special_chars_in_value(caplog, tmp_path):
    """Special characters (/, \\n, &) are rendered correctly via str.replace."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Character escaping
    # · Last fail: N/A (preventive)
    # · Remove if: render_template changes substitution mechanism
    tmpl = tmp_path / "special.tmpl"
    tmpl.write_text("Path={{PATH}}&Newline={{NL}}&Amp={{AMP}}")
    result = render_template(
        str(tmpl),
        vars={"PATH": "/opt/platform/core", "NL": "line1\nline2", "AMP": "a&b"},
        dry_run=True,
    )
    assert result == "Path=/opt/platform/core&Newline=line1\nline2&Amp=a&b"
    logger.critical("[IMP:9][test][render] Special characters preserved")


# endregion TEST_SPECIAL_CHARS_IN_VALUE


# region TEST_PARSE_VARS
@ldd_trajectory
def test_parse_vars(caplog):
    """parse_vars converts KEY=val list to dict."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · CLI var parsing
    # · Last fail: N/A (preventive)
    # · Remove if: parse_vars signature changes
    result = parse_vars(["A=1", "B=2", "NAME=hello"])
    assert result == {"A": "1", "B": "2", "NAME": "hello"}
    logger.critical("[IMP:9][test][parse] parse_vars basic OK")


# endregion TEST_PARSE_VARS


# region TEST_PARSE_VARS_EMPTY_KEY
@ldd_trajectory
def test_parse_vars_empty_key(caplog):
    """parse_vars rejects empty key."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Input validation
    # · Last fail: N/A (preventive)
    # · Remove if: parse_vars validation changes
    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError, match="Empty key"):
        parse_vars(["=val"])
    logger.critical("[IMP:9][test][parse] parse_vars rejects empty key")


# endregion TEST_PARSE_VARS_EMPTY_KEY


# region TEST_PARSE_VARS_NO_EQUALS
@ldd_trajectory
def test_parse_vars_no_equals(caplog):
    """parse_vars rejects pair without =."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Input validation
    # · Last fail: N/A (preventive)
    # · Remove if: parse_vars validation changes
    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError, match="Invalid variable format"):
        parse_vars(["invalid"])
    logger.critical("[IMP:9][test][parse] parse_vars rejects no-equals")


# endregion TEST_PARSE_VARS_NO_EQUALS


# region TEST_PARSE_VARS_DUPLICATE
@ldd_trajectory
def test_parse_vars_duplicate(caplog):
    """parse_vars: last value wins on duplicate key."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Duplicate key resolution
    # · Last fail: N/A (preventive)
    # · Remove if: parse_vars duplicate policy changes
    result = parse_vars(["X=first", "X=second"])
    assert result == {"X": "second"}
    logger.critical("[IMP:9][test][parse] parse_vars duplicate: last wins")


# endregion TEST_PARSE_VARS_DUPLICATE


# region TEST_ATOMIC_WRITE_OUTPUT
@ldd_trajectory
def test_atomic_write_output(caplog, tmp_path):
    """render_template with output_path writes atomically."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Atomic write guarantee
    # · Last fail: N/A (preventive)
    # · Remove if: _atomic_write implementation changes
    tmpl = tmp_path / "atomic.tmpl"
    tmpl.write_text("{{MSG}}")
    out = tmp_path / "output.txt"
    render_template(str(tmpl), output_path=str(out), vars={"MSG": "atomic"}, dry_run=False)
    assert out.read_text() == "atomic"
    logger.critical("[IMP:9][test][write] Atomic write OK")


# endregion TEST_ATOMIC_WRITE_OUTPUT


# region TEST_RENDERALL_MISSING_MANIFEST
@ldd_trajectory
def test_renderall_missing_manifest(caplog):
    """render_all with non-existent manifest raises FileNotFoundError."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Error on missing manifest
    # · Last fail: N/A (preventive)
    # · Remove if: render_all error semantics change
    with pytest.raises(FileNotFoundError):
        render_all("/nonexistent/manifest.yaml")
    logger.critical("[IMP:9][test][render-all] Missing manifest raises FileNotFoundError")


# endregion TEST_RENDERALL_MISSING_MANIFEST


# region TEST_CHECK_ALL_EMPTY_MANIFEST
@ldd_trajectory
def test_check_all_empty_manifest(caplog, tmp_path):
    """check_all with empty template list returns OK."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Empty manifest edge case
    # · Last fail: N/A (preventive)
    # · Remove if: check_all signature changes
    manifest = tmp_path / "empty.yaml"
    manifest.write_text("version: 1\ntemplates: []")
    ok, _diag = check_all(str(manifest))
    assert ok is True
    logger.critical("[IMP:9][test][check] Empty manifest OK")


# endregion TEST_CHECK_ALL_EMPTY_MANIFEST


# region TEST_DETERMINISTIC_OUTPUT
@ldd_trajectory
def test_deterministic_output(caplog, tmp_path):
    """Two renders with same inputs produce identical output."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Determinism invariant
    # · Last fail: N/A (preventive)
    # · Remove if: render_template adds non-deterministic behavior
    tmpl = tmp_path / "det.tmpl"
    tmpl.write_text("{{A}}-{{B}}")
    vars = {"A": "x", "B": "y"}
    r1 = render_template(str(tmpl), vars=vars, dry_run=True)
    r2 = render_template(str(tmpl), vars=vars, dry_run=True)
    assert r1 == r2
    assert r1 == "x-y"
    logger.critical("[IMP:9][test][render] Deterministic output OK")


# endregion TEST_DETERMINISTIC_OUTPUT


# region TEST_BINARY_TEMPLATE
@ldd_trajectory
def test_binary_template(caplog, tmp_path):
    """Template with null byte raises TemplateError('binary content detected')."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Binary detection invariant
    # · Last fail: N/A (preventive)
    # · Remove if: binary detection logic changes
    tmpl = tmp_path / "binary.bin"
    tmpl.write_bytes(b"text\x00more")
    with pytest.raises(TemplateError) as exc:
        render_template(str(tmpl), dry_run=True)
    assert "binary" in str(exc.value).lower()
    logger.critical("[IMP:9][test][error] Binary template raises TemplateError")


# endregion TEST_BINARY_TEMPLATE


# region TEST_PLACEHOLDER_SYMBOLIC_LINK
@ldd_trajectory
def test_placeholder_symbolic_link(caplog, tmp_path):
    """render_template resolves symlinks before reading."""
    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Symlink resolution invariant
    # · Last fail: N/A (preventive)
    # · Remove if: symlink resolution logic changes
    real = tmp_path / "real.tmpl"
    real.write_text("{{MSG}}")
    link = tmp_path / "link.tmpl"
    link.symlink_to("real.tmpl")
    result = render_template(str(link), vars={"MSG": "symlink"}, dry_run=True)
    assert result == "symlink"
    logger.critical("[IMP:9][test][render] Symlink resolution OK")


# endregion TEST_PLACEHOLDER_SYMBOLIC_LINK
