"""
# GREP_SUMMARY: test-deploy-smoke, integration-smoke, deploy-modules-node-yaml, exit-code
# STRUCTURE: ▶ test_deploy_modules_no_node_yaml → subprocess.NODE_YAML="" → ⚡ exit 1 + "ERROR" → ▶ test_deploy_modules_missing_node_yaml_file → NODE_YAML=/nonexistent → ⚡ exit 1
# region MODULE_CONTRACT
## @purpose  Integration smoke tests for deploy-modules.sh — verify exit codes and error messages
##           for missing/invalid NODE_YAML. These tests run the actual shell facade in a subprocess
##           with mocked root check to isolate NODE_YAML validation.
## @scope    Two smoke tests covering the first validation gate in deploy-modules.sh.
##           No Docker, VPS, or network access required — script exits before reaching deployment.
## @invariants
##   - Tests mock the `id` command to simulate root (the real root check exits before NODE_YAML)
##   - Tests set NODE_YAML env var to empty or nonexistent path
##   - Both tests expect exit code 1 and "ERROR" in stderr
##   - tmp_path fixture is used for the mock wrapper script
## @rationale DevPlan 042 Option D — 2 integration smoke tests verify the shell facade behaves
##           correctly at its first validation gate. These are the minimum viable smoke tests
##           for a 91-LOC thin facade.
## @changes   2026-07-22 · DevPlan 042 — created 2 smoke tests
# endregion MODULE_CONTRACT
"""

import logging
import os
import stat as _stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DEPLOY_MODULES_SH = (
    Path(__file__).resolve().parent / ".." / "core" / "internal" / "bootstrap" / "deploy-modules.sh"
).resolve()


# region HELPER__run_with_mock_root
def _run_with_mock_root(script_path: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Run deploy-modules.sh with a mocked root `id` command.

    ## @purpose  deploy-modules.sh checks `[[ "$(id -u)" -eq 0 ]]` immediately. To test
    ##           NODE_YAML validation, we must bypass this check. This helper creates a
    ##           wrapper script that overrides `id` and runs the target script.
    ## @io       ⇥ script_path, env_extra → ⎋ CompletedProcess
    ## @complexity 2 — file write + subprocess
    ## @invariants
    ##   - Wrapper script has execute permission
    ##   - `id` function is exported via BASH_FUNC_id
    ##   - Extra env vars are merged with current environment
    """
    wrapper = Path(os.environ.get("TEST_TMPDIR", str(Path(__file__).parent))) / f"_mock_root_{os.getpid()}.sh"
    try:
        wrapper.write_text(f"""#!/usr/bin/env bash
# Mock `id` command to simulate root (bypass [[ "$(id -u)" -eq 0 ]] check)
id() {{ echo 0; }}
export -f id 2>/dev/null || true
# Run target script with overridden env
{" ".join(f'{k}="{v}"' for k, v in env_extra.items())} bash "{script_path}"
""")
        wrapper.chmod(wrapper.stat().st_mode | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)

        return subprocess.run(
            [str(wrapper)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        if wrapper.exists():
            wrapper.unlink()


# endregion HELPER__run_with_mock_root


# ══════════════════════════════════════════════════════════════════════════════
# I1: No NODE_YAML
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_deploy_modules_no_node_yaml
# 🧪 TRAP[TEST] · Smoke · I1: No NODE_YAML → exit 1 · Last fail: N/A · Remove if: NODE_YAML validation is removed from deploy-modules.sh
def test_deploy_modules_no_node_yaml(tmp_path: Path) -> None:
    """Run deploy-modules.sh without NODE_YAML — must exit 1 with ERROR in stderr.

    ## @purpose  Verify the first structural validation gate: NODE_YAML must be set.
    ##           If unset, the script must abort immediately with exit 1.
    ## @scenario I1: integration smoke — missing NODE_YAML
    ## @invariants
    ##   - Exit code == 1
    ##   - Stderr contains "ERROR"
    ##   - No deployment logic is executed (exits before provisioner)
    """
    # Run with NODE_YAML explicitly emptied (simulates unset)
    result = _run_with_mock_root(_DEPLOY_MODULES_SH, {"NODE_YAML": ""})

    logger.info("[IMP:9][I1][result] exit_code=%d, stderr=%s", result.returncode, result.stderr[:200])
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}. stderr: {result.stderr[:200]}"
    assert "ERROR" in result.stderr, f"Expected 'ERROR' in stderr. Got: {result.stderr[:200]}"

    print(f"[IMP:9][I1] ✅ No NODE_YAML → exit {result.returncode} with ERROR in stderr")


# endregion FUNC_test_deploy_modules_no_node_yaml


# ══════════════════════════════════════════════════════════════════════════════
# I2: Missing NODE_YAML file
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_deploy_modules_missing_node_yaml_file
# 🧪 TRAP[TEST] · Smoke · I2: NODE_YAML=/nonexistent → exit 1 · Last fail: N/A · Remove if: NODE_YAML file validation is removed
def test_deploy_modules_missing_node_yaml_file(tmp_path: Path) -> None:
    """Run deploy-modules.sh with NODE_YAML=/nonexistent — must exit 1.

    ## @purpose  Verify NODE_YAML points to a valid file. If the file doesn't exist,
    ##           the script must abort with exit 1.
    ## @scenario I2: integration smoke — missing NODE_YAML file
    ## @invariants
    ##   - Exit code == 1
    ##   - No deployment logic is executed (exits before provisioner)
    """
    nonexistent = tmp_path / "nonexistent.yaml"
    result = _run_with_mock_root(_DEPLOY_MODULES_SH, {"NODE_YAML": str(nonexistent)})

    logger.info("[IMP:9][I2][result] exit_code=%d, stderr=%s", result.returncode, result.stderr[:200])
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}. stderr: {result.stderr[:200]}"
    assert "ERROR" in result.stderr, f"Expected 'ERROR' in stderr. Got: {result.stderr[:200]}"

    print(f"[IMP:9][I2] ✅ NODE_YAML=/nonexistent → exit {result.returncode} with ERROR in stderr")


# endregion FUNC_test_deploy_modules_missing_node_yaml_file
