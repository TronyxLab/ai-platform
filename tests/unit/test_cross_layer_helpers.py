#!/usr/bin/env python3
# GREP_SUMMARY: test-cross-layer-helpers scanner-unit-tests looks-like-path resolve-import trace-variable collect-variables shellcheck invoke-validation direct-call
# STRUCTURE: ▶ import из tests/helpers/cross_layer_linter.py → ◇ TestLooksLikePath (11) → ◇ TestResolveImport (5) → ◇ TestCollectPathVariables (4) → ◇ TestTraceVariableAssignment (6) → ◇ TestShellCheckIntegration (4) → ◇ invoke/direct-call → ⎋
# region MODULE_CONTRACT
## @purpose  Unit-тесты сканера cross-layer линтера (реализация — tests/helpers/cross_layer_linter.py).
##           Перемещены из tests/test_cross_layer_imports.py (DevPlan 139 W3 T5: 1809 → ≤600 LOC —
##           enforcement + direction-allowlist + R5-negative остались в основном файле).
## @scope    Тестируют internals сканера: _looks_like_path, resolve_import, _trace_variable_assignment,
##           _collect_path_variables, ShellCheck-интеграция, Gate #8 v2 (invoke-валидация,
##           direct module calls). Сам enforcement-гейт — в tests/test_cross_layer_imports.py.
## @invariants
##   - Native imports из helpers (никакого subprocess для бизнес-логики)
##   - tmp_path изоляция (Zero Hardcode Rule)
##   - LDD IMP:9 в каждом тесте
## @rationale Сканер-юнит-тесты переживают переписывание файла гейта — они проверяют
##            поведение helper'а, а не структуру тестового файла.
## @changes  2026-08-05 | DevPlan 139 W3 T5 — создан (перенос из test_cross_layer_imports.py)
# endregion MODULE_CONTRACT

import logging
import shutil
from pathlib import Path

from tests.helpers.cross_layer_linter import (
    CORE_DIR,
    _collect_path_variables,
    _detect_direct_module_calls,
    _detect_invoke_calls,
    _looks_like_path,
    _trace_variable_assignment,
    _validate_interfaces,
    resolve_import,
)

logger = logging.getLogger(__name__)


# region TEST_DETECT_DIRECT_CALL
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — direct module call detection
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_direct_module_call_detected(tmp_path: Path) -> None:
    """Gate #8 v2: direct bash modules/ call from internal/ file is detected."""
    # region FUNC_test_direct_module_call_detected
    test_file = tmp_path / "test.sh"
    test_file.write_text('#!/usr/bin/env bash\nbash "${CORE_DIR}/modules/postgres/healthcheck.sh" liveness\n')
    calls = _detect_direct_module_calls(test_file)
    assert len(calls) == 1, f"Expected 1 direct call, got {len(calls)}: {calls}"
    assert "modules/" in calls[0][2], f"Expected modules/ in target: {calls[0]}"
    logger.info("[IMP:9][gate8-v2][test] Direct module call detected: line %d, type=%s", calls[0][0], calls[0][1])
    # endregion FUNC_test_direct_module_call_detected


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Anti-survivorship — old gate blindness fixed
# · Last fail: old Gate #8 (blind to bash "$variable" pattern)
# · Remove if: Gate #8 v2 is superseded or variable tracking is no longer needed
def test_gate8_original_blindness_fixed(tmp_path: Path) -> None:
    """Gate #8 v2: old blind pattern `bash "$hc_script"` is now detected via variable tracking."""
    # region FUNC_test_gate8_original_blindness_fixed
    test_file = tmp_path / "test.sh"
    test_file.write_text(
        "#!/usr/bin/env bash\n"
        'local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n'
        'bash "$hc_script" liveness\n'
    )
    calls = _detect_direct_module_calls(test_file)
    assert len(calls) >= 1, (
        f"Gate #8 v2 must detect bash via variable — old gate was blind to this pattern. Calls found: {calls}"
    )
    logger.info("[IMP:9][gate8-v2][test] Old blind pattern detected: %s", calls)
    # endregion FUNC_test_gate8_original_blindness_fixed


# endregion TEST_DETECT_DIRECT_CALL


# region TEST_INVOKE_VALIDATION
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — registered interface passes
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_invoke_registered_interface_passes(tmp_path: Path) -> None:
    """Gate #8 v2: invoke_module_interface with registered interface passes."""
    # region FUNC_test_invoke_registered_interface_passes
    module_dir = CORE_DIR / "modules" / "_test_registered"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_yaml = module_dir / "module.yaml"
    module_yaml.write_text("name: _test_registered\ninstall_type: docker\ninterfaces:\n  - healthcheck\n")

    test_file = tmp_path / "deploy.sh"
    test_file.write_text("#!/usr/bin/env bash\ninvoke_module_interface _test_registered healthcheck liveness\n")

    try:
        invoke_calls = _detect_invoke_calls(test_file)
        violations: list[str] = []
        violations = _validate_interfaces(invoke_calls, violations, test_file)
        assert len(violations) == 0, f"Expected 0 violations for registered interface, got: {violations}"
        logger.info("[IMP:9][gate8-v2][test] Registered interface passes — 0 violations")
    finally:
        shutil.rmtree(module_dir, ignore_errors=True)
    # endregion FUNC_test_invoke_registered_interface_passes


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — unregistered interface fails
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_invoke_unregistered_interface_fails(tmp_path: Path) -> None:
    """Gate #8 v2: invoke_module_interface with unregistered interface fails."""
    # region FUNC_test_invoke_unregistered_interface_fails
    module_dir = CORE_DIR / "modules" / "_test_unregistered"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_yaml = module_dir / "module.yaml"
    module_yaml.write_text("name: _test_unregistered\ninstall_type: docker\ninterfaces: []\n")

    test_file = tmp_path / "deploy.sh"
    test_file.write_text("#!/usr/bin/env bash\ninvoke_module_interface _test_unregistered healthcheck liveness\n")

    try:
        invoke_calls = _detect_invoke_calls(test_file)
        violations: list[str] = []
        violations = _validate_interfaces(invoke_calls, violations, test_file)
        assert len(violations) >= 1, "Expected violation for unregistered interface"
        assert "NOT REGISTERED" in violations[0], f"Expected 'NOT REGISTERED' in: {violations[0]}"
        logger.info("[IMP:9][gate8-v2][test] Unregistered interface detected: %s", violations[0])
    finally:
        shutil.rmtree(module_dir, ignore_errors=True)
    # endregion FUNC_test_invoke_unregistered_interface_fails


# endregion TEST_INVOKE_VALIDATION


# region TEST_LOOKS_LIKE_PATH (T4.1)
class TestLooksLikePath:
    """Unit tests for _looks_like_path() — distinguishes path-like args from flags/vars."""

    def test_literal_path(self) -> None:
        """Literal path with / is detected."""
        assert _looks_like_path("modules/postgres/healthcheck.sh") is True

    def test_variable_with_path(self) -> None:
        """${VAR}/path is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/postgres/healthcheck.sh") is True

    def test_bare_variable(self) -> None:
        """Bare $variable (no /) is detected as potential path."""
        assert _looks_like_path("$hc_script") is True

    def test_bare_variable_braces(self) -> None:
        """${variable} without / is NOT detected as path."""
        assert _looks_like_path("${hc_script}") is False

    def test_flag_minus_c(self) -> None:
        """Flag argument is not a path."""
        assert _looks_like_path("-c") is False

    def test_special_vars(self) -> None:
        """Special shell variables are not paths."""
        for var in ["$?", "$#", "$$", "$!", "$@", "$*", "$-", "$0"]:
            assert _looks_like_path(var) is False, f"{var} should not be path"

    def test_empty_string(self) -> None:
        """Empty string is not a path."""
        assert _looks_like_path("") is False

    def test_quoted_bare_variable(self) -> None:
        """Quoted bare variable is detected."""
        assert _looks_like_path('"$hc_script"') is True

    def test_multiple_variables_in_string(self) -> None:
        """String with multiple $vars and / is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/${mod_name}/healthcheck.sh") is True

    def test_dollar_sign_only(self) -> None:
        """Single $ is not a path."""
        assert _looks_like_path("$") is False

    def test_positional_param(self) -> None:
        """Positional parameter $1 is not a path."""
        assert _looks_like_path("$1") is False


# endregion TEST_LOOKS_LIKE_PATH


# region TEST_RESOLVE_IMPORT (T4.2)
class TestResolveImport:
    """Unit tests for resolve_import() — auto-collected/contextual/bare variable refs."""

    def test_known_variable_substitution(self, tmp_path: Path) -> None:
        """Auto-collected variable from paths.sh is substituted."""
        source_file = tmp_path / "entrypoints" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(source_file, "${PATHS_MODULES_DIR}/postgres/healthcheck.sh", "entrypoints")
        assert result is not None
        assert "core/modules/postgres/healthcheck.sh" in str(result)

    def test_unresolved_bare_variable(self, tmp_path: Path) -> None:
        """Bare variable without assignment returns None."""
        source_file = tmp_path / "entrypoints" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(source_file, "$unknown_var", "entrypoints")
        assert result is None

    def test_bare_variable_with_trace(self, tmp_path: Path) -> None:
        """Bare variable traced to local assignment resolves correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n')
        traced = _trace_variable_assignment(f, "hc_script")
        assert traced is not None
        assert "modules/postgres/healthcheck.sh" in traced

    def test_nested_variable_substitution(self, tmp_path: Path) -> None:
        """Nested variable references are resolved recursively."""
        source_file = tmp_path / "internal" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(source_file, "${PATHS_CORE_DIR}/modules/postgres/healthcheck.sh", "internal")
        assert result is not None
        assert str(result).endswith("core/modules/postgres/healthcheck.sh")

    def test_contextual_variable(self, tmp_path: Path) -> None:
        """Contextual variable (_EP_DIR) resolves to source file directory."""
        source = CORE_DIR / "entrypoints" / "_test_ep.sh"
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("#!/bin/bash\n")
            result = resolve_import(source, "${_EP_DIR}/../lib/foo.sh", "entrypoints")
            assert result is not None
            assert str(result).endswith("core/lib/foo.sh")
        finally:
            if source.exists():
                source.unlink()
            if source.parent.exists() and not any(source.parent.iterdir()):
                source.parent.rmdir()


# endregion TEST_RESOLVE_IMPORT


# region TEST_COLLECT_PATH_VARIABLES (T4.3)
class TestCollectPathVariables:
    """Unit tests for _collect_path_variables() — auto-collection from paths.sh."""

    def test_real_paths_sh_parsed(self) -> None:
        """Real paths.sh is parsed and returns expected variables."""
        variables = _collect_path_variables()
        assert len(variables) >= 6
        for var in (
            "PATHS_LIB_DIR",
            "PATHS_CORE_DIR",
            "PATHS_MODULES_DIR",
            "PATHS_TEMPLATES_DIR",
            "PATHS_INTERNAL_DIR",
        ):
            assert var in variables, f"missing {var}"

    def test_custom_paths_file(self, tmp_path: Path) -> None:
        """Custom paths file is parsed correctly."""
        f = tmp_path / "paths.sh"
        f.write_text('readonly MY_DIR="/opt/myapp"\nexport MY_OTHER="/var/lib/myapp"\n')
        variables = _collect_path_variables(f)
        assert "MY_DIR" in variables
        assert variables["MY_DIR"] == "/opt/myapp"
        assert "MY_OTHER" in variables

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        f = tmp_path / "empty.sh"
        f.write_text("")
        variables = _collect_path_variables(f)
        assert variables == {}

    def test_only_comments(self, tmp_path: Path) -> None:
        """File with only comments returns empty dict."""
        f = tmp_path / "comments.sh"
        f.write_text("# This is a comment\n# Another comment\n")
        variables = _collect_path_variables(f)
        assert variables == {}


# endregion TEST_COLLECT_PATH_VARIABLES


# region TEST_TRACE_VARIABLE_ASSIGNMENT (T4.4)
class TestTraceVariableAssignment:
    """Unit tests for _trace_variable_assignment() — local/export/readonly tracking."""

    def test_local_assignment_found(self, tmp_path: Path) -> None:
        """local var=path is traced correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is not None
        assert "healthcheck.sh" in result

    def test_no_assignment(self, tmp_path: Path) -> None:
        """Variable not assigned locally returns None."""
        f = tmp_path / "test.sh"
        f.write_text('bash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is None

    def test_multiple_assignments_last_wins(self, tmp_path: Path) -> None:
        """Last assignment is used."""
        f = tmp_path / "test.sh"
        f.write_text('local var="/first/path.sh"\nlocal var="/second/path.sh"\nbash "$var"\n')
        result = _trace_variable_assignment(f, "var")
        assert result is not None
        assert "second" in result

    def test_assignment_without_path(self, tmp_path: Path) -> None:
        """Assignment without / in value returns None."""
        f = tmp_path / "test.sh"
        f.write_text('local flag="--verbose"\n')
        result = _trace_variable_assignment(f, "flag")
        assert result is None

    def test_export_assignment(self, tmp_path: Path) -> None:
        """export var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('export MY_SCRIPT="/opt/platform/core/modules/postgres/healthcheck.sh"\n')
        result = _trace_variable_assignment(f, "MY_SCRIPT")
        assert result is not None
        assert "healthcheck.sh" in result

    def test_readonly_assignment(self, tmp_path: Path) -> None:
        """readonly var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('readonly MY_DIR="/opt/core/modules/postgres"\n')
        result = _trace_variable_assignment(f, "MY_DIR")
        assert result is not None
        assert "postgres" in result


# endregion TEST_TRACE_VARIABLE_ASSIGNMENT


# region TEST_SHELLCHECK_INTEGRATION (T4.5)
class TestShellCheckIntegration:
    """Unit tests for tests/_conftest/shellcheck.py integration (graceful degradation)."""

    def test_check_available_returns_bool(self) -> None:
        """_check_shellcheck_available returns (bool, str)."""
        from _conftest.shellcheck import _check_shellcheck_available

        available, msg = _check_shellcheck_available()
        assert isinstance(available, bool)
        assert isinstance(msg, str)

    def test_parse_sc2154_empty_file(self, tmp_path: Path) -> None:
        """Empty file has no SC2154 diagnostics."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154

        f = tmp_path / "empty.sh"
        f.write_text("#!/bin/bash\n")
        vars_found = _parse_shellcheck_sc2154(f)
        assert vars_found == []

    def test_parse_sc2154_unassigned_var(self, tmp_path: Path) -> None:
        """Unassigned variable triggers SC2154."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154

        f = tmp_path / "test.sh"
        f.write_text('#!/bin/bash\nbash "$hc_script"\n')
        vars_found = _parse_shellcheck_sc2154(f)
        assert "hc_script" in vars_found

    def test_get_bash_calls_with_shellcheck(self, tmp_path: Path) -> None:
        """ShellCheck detects bash call with variable assigned from path."""
        from _conftest.shellcheck import get_shellcheck_bash_calls

        f = tmp_path / "test.sh"
        f.write_text(
            '#!/bin/bash\nlocal hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script" liveness\n'
        )
        calls = get_shellcheck_bash_calls(f)
        assert isinstance(calls, list)
        logger.info("[IMP:9][test][shellcheck] get_shellcheck_bash_calls returned %d calls: %s", len(calls), calls)


# endregion TEST_SHELLCHECK_INTEGRATION
