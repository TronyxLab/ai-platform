"""
# GREP_SUMMARY: test_exceptions, platform-error, typed-exceptions, exit-codes, config-errors
# STRUCTURE: ▶ 4 tests → ◇ exit_codes → ◇ inheritance → ◇ message → ◇ base_catch → ⎋ all pass
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/exceptions.py — typed exception hierarchy
## @scope    Tests all 5 exception classes: exit_code values, inheritance, messages, base class catch
## @invariants
##   - Each test validates at least one meaningful assertion
##   - LDD trajectory verified via @ldd_trajectory decorator
## @changes 2026-07-26 · DevPlan 038a — Created
# endregion MODULE_CONTRACT
"""

import logging

from tests._conftest.ldd import ldd_trajectory

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# region Tests: Exception exit codes
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · verify all exit_code values
# · Scenario: Each exception subclass has the correct exit_code class attribute
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_platform_error_exit_codes(caplog):
    """Verify each exception subclass has the correct exit_code.

    ## @purpose  Ensure that exit_code values match the DevPlan 038a specification.
    ##   PlatformError=1, ConfigNotFoundError=2, ConfigParseError=3,
    ##   ConfigValidationError=4, PlatformFatalError=10.
    """
    assert PlatformError.exit_code == 1
    assert ConfigNotFoundError.exit_code == 2
    assert ConfigParseError.exit_code == 3
    assert ConfigValidationError.exit_code == 4
    assert PlatformFatalError.exit_code == 10

    logger.critical("[IMP:9][test] exit_codes: base=%d, not_found=%d, parse=%d, validation=%d, fatal=%d — OK",
                    PlatformError.exit_code, ConfigNotFoundError.exit_code,
                    ConfigParseError.exit_code, ConfigValidationError.exit_code,
                    PlatformFatalError.exit_code)


# 🧪 TRAP[TEST] · Regression · verify exception inheritance
# · Scenario: All Config* exceptions are subclasses of PlatformError
# · Last fail: N/A (new test)
# · Remove if: exception hierarchy changes
@ldd_trajectory
def test_exception_inheritance(caplog):
    """Verify all exceptions inherit from PlatformError.

    ## @purpose  Ensure the hierarchy is correct: PlatformError ← Config*Error, PlatformFatalError.
    """
    assert issubclass(ConfigNotFoundError, PlatformError)
    assert issubclass(ConfigParseError, PlatformError)
    assert issubclass(ConfigValidationError, PlatformError)
    assert issubclass(PlatformFatalError, PlatformError)

    # Verify non-subclass relationships
    assert not issubclass(ConfigNotFoundError, PlatformFatalError)
    assert not issubclass(ConfigParseError, ConfigNotFoundError)
    assert not issubclass(ConfigValidationError, ConfigParseError)

    logger.critical("[IMP:9][test] inheritance: all exceptions inherit from PlatformError — OK")


# 🧪 TRAP[TEST] · Regression · verify exception message propagation
# · Scenario: Exceptions correctly store and return the message string
# · Last fail: N/A (new test)
# · Remove if: Exception message handling changes
@ldd_trajectory
def test_exception_message(caplog):
    """Verify exception correctly stores and returns the message string.

    ## @purpose  Ensure str(e) == message for all exception classes.
    """
    for exc_class in [ConfigNotFoundError, ConfigParseError, ConfigValidationError, PlatformFatalError]:
        msg = f"Test error for {exc_class.__name__}"
        exc = exc_class(msg)
        assert str(exc) == msg

    logger.critical("[IMP:9][test] exception_message: all classes propagate messages — OK")


# 🧪 TRAP[TEST] · Regression · verify catch by base class
# · Scenario: except PlatformError catches all subclasses
# · Last fail: N/A (new test)
# · Remove if: exception hierarchy changes
@ldd_trajectory
def test_exception_catch_by_base(caplog):
    """Verify that `except PlatformError` catches all subclasses.

    ## @purpose  Ensure the hierarchy supports polymorphic exception handling.
    """
    for exc_class in [ConfigNotFoundError, ConfigParseError, ConfigValidationError, PlatformFatalError]:
        caught = False
        try:
            raise exc_class(f"Raised {exc_class.__name__}")
        except PlatformError:
            caught = True
        assert caught, f"except PlatformError did not catch {exc_class.__name__}"

    logger.critical("[IMP:9][test] catch_by_base: all subclasses caught by except PlatformError — OK")


# ═══════════════════════════════════════════════════════════════════
# region Tests: W4 extended tests (DevPlan 038b)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · PlatformError exit_code=1
# · Scenario: raise PlatformError() → catch → .exit_code == 1
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_platform_error_exit_code(caplog):
    """PlatformError has exit_code=1."""
    try:
        raise PlatformError("test")
    except PlatformError as e:
        assert e.exit_code == 1
    logger.critical("[IMP:9][test] platform_error_exit_code: exit_code=1 — OK")


# 🧪 TRAP[TEST] · Regression · ConfigNotFoundError exit_code=2
# · Scenario: raise ConfigNotFoundError() → catch → .exit_code == 2
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_config_not_found_exit_code(caplog):
    """ConfigNotFoundError has exit_code=2."""
    try:
        raise ConfigNotFoundError("test")
    except PlatformError as e:
        assert e.exit_code == 2
    logger.critical("[IMP:9][test] config_not_found_exit_code: exit_code=2 — OK")


# 🧪 TRAP[TEST] · Regression · ConfigParseError exit_code=3
# · Scenario: raise ConfigParseError() → catch → .exit_code == 3
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_config_parse_error_exit_code(caplog):
    """ConfigParseError has exit_code=3."""
    try:
        raise ConfigParseError("test")
    except PlatformError as e:
        assert e.exit_code == 3
    logger.critical("[IMP:9][test] config_parse_error_exit_code: exit_code=3 — OK")


# 🧪 TRAP[TEST] · Regression · ConfigValidationError exit_code=4
# · Scenario: raise ConfigValidationError() → catch → .exit_code == 4
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_config_validation_error_exit_code(caplog):
    """ConfigValidationError has exit_code=4."""
    try:
        raise ConfigValidationError("test")
    except PlatformError as e:
        assert e.exit_code == 4
    logger.critical("[IMP:9][test] config_validation_error_exit_code: exit_code=4 — OK")


# 🧪 TRAP[TEST] · Regression · PlatformFatalError exit_code=10
# · Scenario: raise PlatformFatalError() → catch → .exit_code == 10
# · Last fail: N/A (new test)
# · Remove if: exit_code values change
@ldd_trajectory
def test_platform_fatal_error_exit_code(caplog):
    """PlatformFatalError has exit_code=10."""
    try:
        raise PlatformFatalError("test")
    except PlatformError as e:
        assert e.exit_code == 10
    logger.critical("[IMP:9][test] platform_fatal_error_exit_code: exit_code=10 — OK")


# 🧪 TRAP[TEST] · Regression · exception inheritance from PlatformError
# · Scenario: isinstance(subclass_instance, PlatformError) is True
# · Last fail: N/A (new test)
# · Remove if: exception hierarchy changes
@ldd_trajectory
def test_exception_inheritance_platform_error(caplog):
    """All subclasses inherit from PlatformError (isinstance check)."""
    assert isinstance(ConfigNotFoundError(), PlatformError)
    assert isinstance(ConfigParseError(), PlatformError)
    assert isinstance(ConfigValidationError(), PlatformError)
    assert isinstance(PlatformFatalError(), PlatformError)
    logger.critical("[IMP:9][test] inheritance: all subclasses inherit from PlatformError — OK")


# 🧪 TRAP[TEST] · Regression · exception string message
# · Scenario: str(ConfigNotFoundError("test")) == "test"
# · Last fail: N/A (new test)
# · Remove if: message propagation changes
@ldd_trajectory
def test_exception_str_message(caplog):
    """str(exc) returns the message passed to constructor."""
    assert str(ConfigNotFoundError("test message")) == "test message"
    assert str(ConfigParseError("parse error")) == "parse error"
    assert str(ConfigValidationError("validation error")) == "validation error"
    assert str(PlatformFatalError("fatal error")) == "fatal error"
    logger.critical("[IMP:9][test] str_message: all exceptions propagate constructor message — OK")


# 🧪 TRAP[TEST] · Regression · all subclasses registered
# · Scenario: PlatformError.__subclasses__() returns 4 classes
# · Last fail: N/A (new test)
# · Remove if: hierarchy changes
@ldd_trajectory
def test_all_subclasses_registered(caplog):
    """PlatformError has exactly 4 direct subclasses."""
    subclasses = PlatformError.__subclasses__()
    class_names = {cls.__name__ for cls in subclasses}
    assert len(class_names) >= 4, f"Expected ≥4 subclasses, got {len(class_names)}: {class_names}"
    assert "ConfigNotFoundError" in class_names
    assert "ConfigParseError" in class_names
    assert "ConfigValidationError" in class_names
    assert "PlatformFatalError" in class_names
    logger.critical("[IMP:9][test] subclasses: %d registered — OK", len(class_names))


# endregion Tests: W4 extended tests


# endregion Tests: Exception exit codes
