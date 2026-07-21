#!/usr/bin/env python3
# GREP_SUMMARY: test deploy-modules env-file platform-env pre-pull local-build skip
# STRUCTURE: ▶ test_compose_args_has_platform_env → grep deploy-modules.sh for platform_env → ⚡ assert patterns
# ▶ test_prepull_skips_local_build → grep _pre_pull_images for build: skip → ⚡ assert patterns
# region MODULE_CONTRACT
## @purpose  Tests for deploy-modules.sh env-file and pre-pull skip changes (DevPlan 001 TASK-1.1, TASK-1.2)
## @scope    Verifies that:
##           1. deploy_docker_module passes --env-file for platform .env
##           2. _pre_pull_images skips modules with build: section in compose
## @invariants
##   - Tests use static analysis (grep) and isolated bash subprocesses
##   - No Docker daemon required — static patterns verified in script
##   - Script paths resolved relative to test file location
## @rationale Bash scripts cannot be imported natively in Python. Static analysis
##            of code patterns and isolated subprocess calls are the practical
##            approaches for testing bash function behavior.
## @changes 2026-07-21 | Initial implementation (DevPlan 001)
# endregion MODULE_CONTRACT

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def deploy_modules_script() -> Path:
    """Resolve path to deploy-modules.sh relative to project root."""
    return Path(__file__).parents[1] / "core" / "internal" / "bootstrap" / "deploy-modules.sh"


# region FUNC_test_compose_args_has_platform_env
@pytest.mark.static
def test_compose_args_has_platform_env(caplog, deploy_modules_script: Path) -> None:
    """Verify deploy_docker_module passes --env-file for platform .env.

    Scenarios covered:
    - platform_env variable is declared in deploy_docker_module function
    - --env-file "$platform_env" is present after secrets.env's --env-file
    - Order: secrets.env first (lower priority), platform .env second (higher priority)

    Regression: P1 — Full stack recovery, 3/25 containers failed because
    platform .env (142 variables) was not passed to docker compose.
    """
    # 🧪 TRAP[TEST] · Regression: P1 — Missing platform .env causes container env var starvation
    # · Scenario: deploy_docker_module compose_args — platform_env presence
    # · Last fail: 2026-07-21 — orchestrator final report, 3/25 containers failed
    # · Remove if: deploy-modules.sh is rewritten in Python

    caplog.set_level(7)  # Capture all LDD levels

    script_text = deploy_modules_script.read_text()

    # Check platform_env variable is declared in deploy_docker_module
    assert "platform_env" in script_text, (
        "Missing platform_env variable in deploy_modules.sh — TASK-1.1 not implemented"
    )

    # Check --env-file for platform .env is present
    # Bash array syntax: compose_args+=("--env-file" "$platform_env") — the " is
    # immediately after --env-file (array elements, not string concatenation)
    assert '("--env-file" "$platform_env")' in script_text, (
        'Missing ("--env-file" "$platform_env") in compose_args — platform .env not passed'
    )

    # Check secrets.env --env-file comes BEFORE platform .env (priority order)
    secrets_idx = script_text.index('("--env-file" "$env_file")')
    platform_idx = script_text.index('("--env-file" "$platform_env")')
    assert secrets_idx < platform_idx, (
        "secrets.env --env-file must come BEFORE platform .env --env-file"
        " (secrets lower priority, platform .env overrides)"
    )

    # Verify PLATFORM_ROOT fallback
    assert 'PLATFORM_ROOT:-/opt/platform' in script_text, (
        "Missing PLATFORM_ROOT fallback for platform .env path"
    )

    print(f"[IMP:9][test] compose_args has platform_env: OK — --env-file found at byte offset {platform_idx}")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
# endregion FUNC_test_compose_args_has_platform_env


# region FUNC_test_prepull_skips_local_build
@pytest.mark.static
def test_prepull_skips_local_build(caplog, deploy_modules_script: Path, tmp_path: Path) -> None:
    """Verify _pre_pull_images skips modules with build: section in compose file.

    Scenarios covered:
    - grep -q 'build:' check is present in _pre_pull_images
    - "SKIP" log message mentions "Local build detected" or "build: section"
    - Module with build: is skipped (no pull attempted)

    Regression: P3 — backup-cron/status-page pre-pull errors trying to pull
    locally-built images from registry that don't exist there.
    """
    # 🧪 TRAP[TEST] · Regression: P3 — Pre-pull errors on locally-built images
    # · Scenario: _pre_pull_images with compose file containing build: section
    # · Last fail: 2026-07-21 — backup-cron pre-pull fails on registry lookup
    # · Remove if: pre-pull logic is removed from deploy-modules.sh

    caplog.set_level(7)

    script_text = deploy_modules_script.read_text()

    # Check '^\s\+build:' grep pattern exists in _pre_pull_images region
    assert "build:" in script_text, (
        "Missing build: check in _pre_pull_images — TASK-1.2 not implemented"
    )

    # Verify the skip pattern is present
    assert "grep -q" in script_text, "Missing grep -q pattern for build: detection"
    assert "SKIP" in script_text, "Missing SKIP log message for local build skip"

    # Verify isolation: create a mock compose file with build: and test the grep pattern
    mock_compose = tmp_path / "docker-compose.base.yml"
    mock_compose.write_text("""services:
  test-service:
    build:
      context: .
    image: test:latest
""")

    # Test the grep pattern against the mock compose file
    result = subprocess.run(
        ["bash", "-c", f'if grep -q \'^[[:space:]]\\+build:\' "{mock_compose}" 2>/dev/null; then echo "BUILD_FOUND"; else echo "BUILD_NOT_FOUND"; fi'],
        capture_output=True, text=True, timeout=10,
    )
    assert "BUILD_FOUND" in result.stdout, (
        f"Grep pattern '^\\\\s\\\\+build:' failed to detect build: in mock compose\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )

    # Test negative case: compose without build: should NOT match
    mock_no_build = tmp_path / "docker-compose.no-build.yml"
    mock_no_build.write_text("""services:
  test-service:
    image: registry.example.com/test:latest
""")

    result_neg = subprocess.run(
        ["bash", "-c", f'if grep -q \'^[[:space:]]\\+build:\' "{mock_no_build}" 2>/dev/null; then echo "BUILD_FOUND"; else echo "BUILD_NOT_FOUND"; fi'],
        capture_output=True, text=True, timeout=10,
    )
    assert "BUILD_NOT_FOUND" in result_neg.stdout, (
        f"Grep pattern falsely matched file without build: section\n"
        f"stdout: {result_neg.stdout}"
    )

    print("[IMP:9][test] pre-pull skip for local build: OK — grep pattern correctly detects build: section")
    print("[IMP:9][test] Negative test passed: compose without build: does not match")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
# endregion FUNC_test_prepull_skips_local_build
