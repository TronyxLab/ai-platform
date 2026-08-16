#!/usr/bin/env python3
# GREP_SUMMARY: exceptions, platform-error, typed-exceptions, exit-codes, config-errors
# STRUCTURE: ▶ PlatformError(base, exit_code=1) → ◇ ConfigNotFoundError(2) / ConfigParseError(3) / ConfigValidationError(4) / PlatformFatalError(10)
# region MODULE_CONTRACT
## @purpose  Typed exception hierarchy for all ai-platform errors.
##           Every exception carries an exit_code for CLI/shell compatibility,
##           enabling fail-fast and distinguishing recoverable vs fatal errors.
## @scope    All platform Python modules use these exceptions instead of generic Exception.
##           Layer-agnostic: used by shared/, internal/ scripts, entrypoints, and CLI.
## @invariants
##   1. PlatformError is the base — never raise Exception directly.
##   2. Each subclass has a distinct exit_code matching CLI exit codes.
##   3. ConfigNotFoundError (exit_code=2): file can be created — recoverable.
##   4. ConfigParseError (exit_code=3): YAML/JSON syntax error — recoverable by fixing syntax.
##   5. ConfigValidationError (exit_code=4): structural error (missing key, wrong type) — recoverable.
##   6. PlatformFatalError (exit_code=10): requires manual intervention — non-recoverable.
## @rationale Separation of concerns: 5 classes instead of 3 because
##   ConfigNotFoundError (recoverable: create file) != ConfigParseError (recoverable: fix syntax) !=
##   ConfigValidationError (recoverable: fix structure). PlatformFatalError is clearly non-recoverable.
##   exit_code attribute enables CLI/shell consumers to map exceptions to process exit codes
##   without try/except chains.
## @changes 2026-07-26 · DevPlan 038a — Created
# endregion MODULE_CONTRACT

import logging

logger = logging.getLogger(__name__)


class PlatformError(Exception):
    """Base exception for all platform errors.

    All subclasses carry an exit_code for CLI/shell compatibility.
    """

    exit_code: int = 1

    def __init__(self, message: str = "") -> None:
        """Initialize with optional message.

        ## @purpose  Create PlatformError with message and log at IMP:9.
        ## @io — ⇥ message: str → ⎋ None
        ## @complexity — O(1)
        """
        super().__init__(message)
        logger.error("[IMP:9][PlatformError] %s (exit_code=%d)", message, self.exit_code)


class ConfigNotFoundError(PlatformError):
    """Configuration file not found (ENOENT). Recoverable: file can be created."""

    exit_code: int = 2


class ConfigParseError(PlatformError):
    """Configuration parse error (YAML syntax, JSON decode, non-dict root).
    Recoverable: fix the syntax."""

    exit_code: int = 3


class ConfigValidationError(PlatformError):
    """Configuration structure validation error (missing required key, wrong type).
    Recoverable: add the missing key or fix the type."""

    exit_code: int = 4


class PlatformFatalError(PlatformError):
    """Non-recoverable platform error (root required, preconditions violated).
    Requires manual intervention."""

    exit_code: int = 10
