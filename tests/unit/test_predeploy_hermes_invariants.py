# GREP_SUMMARY: hermes invariants predeploy config validation context overlay security litellm
# STRUCTURE: INVARIANTS → check_invariant → test_all_invariants_valid → test_invariant_violation(param×9)
# region MODULE_CONTRACT
## @purpose — Pre-deploy gate tests for Hermes config invariant validation.
##           Validates 4 security + 5 litellm invariants directly via pure Python functions
##           (no subprocess, no shell, no yq dependency).
## @scope — 5 atomic test cases covering all 9 Hermes invariants plus a valid pass case.
## @invariants
##   - No subprocess.run — all validation is pure Python dict traversal
##   - No filesystem I/O — tests work with Python dict, not temp YAML files
##   - No yq dependency — YAML parsing is not needed (tests provide dicts)
##   - INVARIANTS is a single source of truth for all 9 invariants
##   - Tests are independent and atomic — no shared mutable state
## @rationale — Context overlays can override base config values. Security-sensitive values
##   and provider routing must never be weakened by overlays. These tests verify the invariant
##   checker rejects dangerous combinations before they reach production. Pure Python eliminates
##   ~282 lines of shell, subprocess overhead, and yq dependency.
## @usecases
##   1. Valid context with no overrides → all invariants PASS
##   2. model.provider: deepseek (not litellm) → FAIL (DevPlan 049)
##   3. model.model: deepseek-v4-pro (not reasoning) → FAIL (DevPlan 049)
##   4. fallback_model.provider: zai (not litellm) → FAIL
##   5. auxiliary.vision.provider: deepseek (not litellm) → FAIL
##   6. auxiliary.compression.provider: deepseek (not litellm) → FAIL
##   7. platform.dashboard.insecure: true → FAIL
##   8. security.redact_secrets: false → FAIL
##   9. tool_loop_guardrails.hard_stop_enabled: false → FAIL
##   10. terminal.backend: local → FAIL
# endregion MODULE_CONTRACT

import logging
from typing import Any

import pytest
from conftest import ldd_trajectory

# ── Invariants table ───────────────────────────────────────────────────────────────
# (description, yaml_path, expected_value)
INVARIANTS: list[tuple[str, str, Any]] = [
    # Security invariants (must never be weakened)
    ("insecure dashboard", "platform.dashboard.insecure", False),
    ("redact secrets", "security.redact_secrets", True),
    ("hard stop enabled", "tool_loop_guardrails.hard_stop_enabled", True),
    ("terminal backend", "terminal.backend", "docker"),
    # Provider invariants (DevPlan 049 — all models through LiteLLM)
    ("model provider", "model.provider", "litellm"),
    ("model model", "model.model", "reasoning"),
    ("fallback_model provider", "fallback_model.provider", "litellm"),
    ("auxiliary vision provider", "auxiliary.vision.provider", "litellm"),
    ("auxiliary compression provider", "auxiliary.compression.provider", "litellm"),
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
## @purpose — Valid context (no security overrides) → all 9 invariants PASS.
## @rationale — Default context with litellm provider values should pass all 9 invariants.
## @usecases — UC-1: context without dangerous keys → all [PASS]
@pytest.mark.predeploy
@ldd_trajectory
def test_all_invariants_valid(caplog) -> None:
    """Context with litellm values should pass all 9 invariants."""
    logger = logging.getLogger(__name__)
    logger.info("[IMP:7][test_all_invariants_valid] Creating valid context (litellm provider)...")

    context_data: dict[str, Any] = {
        "model": {"provider": "litellm", "model": "reasoning"},
        "fallback_model": {"provider": "litellm"},
        "auxiliary": {
            "vision": {"provider": "litellm"},
            "compression": {"provider": "litellm"},
        },
    }

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
## @purpose — Parametrized tests for all 9 Hermes invariant violations.
##            Each variant creates a context dict with a dangerous key
##            and asserts the corresponding invariant FAILs while others PASS.
## @rationale — All 9 violation tests share identical structure. Parametrization
##              eliminates 80% code duplication.
## @usecases — UC-2 through UC-10: all invariant violation scenarios
@pytest.mark.predeploy
@ldd_trajectory
@pytest.mark.parametrize(
    "name,context_data,expected_fail_path",
    [
        # Security invariants (DevPlan 049 — maintained from earlier phases)
        ("dashboard_insecure", {"platform": {"dashboard": {"insecure": True}}}, "platform.dashboard.insecure"),
        ("redact_secrets", {"security": {"redact_secrets": False}}, "security.redact_secrets"),
        (
            "hard_stop_disabled",
            {"tool_loop_guardrails": {"hard_stop_enabled": False}},
            "tool_loop_guardrails.hard_stop_enabled",
        ),
        ("terminal_backend_local", {"terminal": {"backend": "local"}}, "terminal.backend"),
        # Provider invariants (DevPlan 049 Phase 6 — Hermes migration to LiteLLM)
        ("model_provider_deepseek", {"model": {"provider": "deepseek"}}, "model.provider"),
        ("model_model_raw", {"model": {"model": "deepseek-v4-pro"}}, "model.model"),
        ("fallback_provider_zai", {"fallback_model": {"provider": "zai"}}, "fallback_model.provider"),
        (
            "auxiliary_vision_provider_deepseek",
            {"auxiliary": {"vision": {"provider": "deepseek"}}},
            "auxiliary.vision.provider",
        ),
        (
            "auxiliary_compression_provider_deepseek",
            {"auxiliary": {"compression": {"provider": "deepseek"}}},
            "auxiliary.compression.provider",
        ),
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
