# GREP_SUMMARY: hermes invariants predeploy config validation context overlay security
# STRUCTURE: INVARIANTS → check_invariant → test_all_invariants_valid → test_invariant_violation(param×4)
# region MODULE_CONTRACT
## @purpose — Pre-deploy gate tests for Hermes config invariant validation.
##           Validates 4 security invariants directly via pure Python functions
##           (no subprocess, no shell, no yq dependency).
## @scope — 5 atomic test cases covering all 4 Hermes invariants plus a valid pass case.
## @invariants
##   - No subprocess.run — all validation is pure Python dict traversal
##   - No filesystem I/O — tests work with Python dict, not temp YAML files
##   - No yq dependency — YAML parsing is not needed (tests provide dicts)
##   - INVARIANTS is a single source of truth for all 4 security invariants
##   - Tests are independent and atomic — no shared mutable state
## @rationale — Context overlays can override base config values. Security-sensitive values
##   must never be weakened by overlays. These tests verify the invariant checker rejects
##   dangerous combinations before they reach production. Pure Python eliminates ~282 lines
##   of shell, subprocess overhead, and yq dependency.
## @usecases
##   1. Valid context with no security overrides → all invariants PASS
##   2. platform.dashboard.insecure: true → FAIL
##   3. security.redact_secrets: false → FAIL
##   4. tool_loop_guardrails.hard_stop_enabled: false → FAIL
##   5. terminal.backend: local → FAIL
# endregion MODULE_CONTRACT

import logging
from typing import Any

import pytest
from conftest import ldd_trajectory

# ── Invariants table ───────────────────────────────────────────────────────────────
# (description, yaml_path, expected_value)
INVARIANTS: list[tuple[str, str, Any]] = [
    ("insecure dashboard", "platform.dashboard.insecure", False),
    ("redact secrets", "security.redact_secrets", True),
    ("hard stop enabled", "tool_loop_guardrails.hard_stop_enabled", True),
    ("terminal backend", "terminal.backend", "docker"),
]


# region FUNC__check_invariant
## @purpose — Check a single Hermes config invariant against context dict.
## @io — ⇥ context_data: dict, yaml_path: str, expected: Any
##       → ⎋ tuple[bool, str] — (passed, message)
## @complexity — O(d) where d = depth of yaml_path (max 3)
## @invariants
##   - Passes if key is absent in context (inherits base config value)
##   - Passes if key is present and matches expected value
##   - Fails if key is present and differs from expected
def _check_invariant(context_data: dict, yaml_path: str, expected: Any) -> tuple[bool, str]:
    """Check Hermes config invariant.

    Passes if key is absent in context (inherits base) or matches expected value.
    Fails if key is present and differs from expected.

    Returns (passed: bool, message: str).
    """
    keys = yaml_path.split(".")
    current = context_data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return True, f"{yaml_path}: not set in context (OK — inherits base value)"

    if current == expected:
        return True, f"{yaml_path}: context sets '{current}' (OK — matches expected value)"
    return False, f"{yaml_path}: context sets '{current}' — must be '{expected}' or absent"


# endregion FUNC__check_invariant


# region FUNC_test_all_invariants_valid
## @purpose — Valid context (no security overrides) → all 4 invariants PASS.
## @rationale — Default context with only model.provider set should pass all 4 invariants.
## @usecases — UC-1: context without dangerous keys → all [PASS]
@pytest.mark.predeploy
@ldd_trajectory
def test_all_invariants_valid(caplog) -> None:
    """Context without dangerous keys should pass all 4 invariants."""
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][test_all_invariants_valid] Creating valid context (no security overrides)...")

    context_data: dict[str, Any] = {"model": {"provider": "deepseek"}}

    all_pass = True
    for desc, path, expected in INVARIANTS:
        passed, msg = _check_invariant(context_data, path, expected)
        logger.info("[IMP:7][%s] %s", desc, msg)
        if not passed:
            all_pass = False

    logger.info("[IMP:9][test_all_invariants_valid] All invariants valid: %s", all_pass)
    assert all_pass, "Expected all invariants to pass for clean context"


# endregion FUNC_test_all_invariants_valid


# region PARAMETRIZED_INVARIANT_VIOLATIONS
## @purpose — Parametrized tests for all 4 Hermes invariant violations.
##            Each variant creates a context dict with a dangerous security key
##            and asserts the corresponding invariant FAILs while others PASS.
## @rationale — All 4 violation tests share identical structure. Parametrization
##              eliminates 80% code duplication.
## @usecases — UC-2 through UC-5: all invariant violation scenarios
@pytest.mark.predeploy
@ldd_trajectory
@pytest.mark.parametrize(
    "name,context_data,expected_fail_path",
    [
        ("dashboard_insecure", {"platform": {"dashboard": {"insecure": True}}}, "platform.dashboard.insecure"),
        ("redact_secrets", {"security": {"redact_secrets": False}}, "security.redact_secrets"),
        (
            "hard_stop_disabled",
            {"tool_loop_guardrails": {"hard_stop_enabled": False}},
            "tool_loop_guardrails.hard_stop_enabled",
        ),
        ("terminal_backend_local", {"terminal": {"backend": "local"}}, "terminal.backend"),
    ],
)
def test_invariant_violation(name, context_data, expected_fail_path, caplog) -> None:
    """Context with dangerous security override should fail exactly one invariant."""
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][test_invariant_violation][%s] Creating context with dangerous key...", name)

    failed_paths: list[str] = []
    for desc, path, expected in INVARIANTS:
        passed, msg = _check_invariant(context_data, path, expected)
        logger.info("[IMP:7][%s] %s", desc, msg)
        if not passed:
            failed_paths.append(path)

    logger.info(
        "[IMP:9][test_invariant_violation][%s] Failed invariants: %s (expected: [%s])",
        name,
        failed_paths,
        expected_fail_path,
    )
    assert expected_fail_path in failed_paths, f"[{name}] Expected '{expected_fail_path}' to fail, but it passed"
    assert len(failed_paths) == 1, f"[{name}] Expected exactly 1 failure, got {len(failed_paths)}: {failed_paths}"


# endregion PARAMETRIZED_INVARIANT_VIOLATIONS
