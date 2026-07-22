"""
# GREP_SUMMARY: test_module_discovery, discover_docker_modules, module-yaml, CLI, tmp_path, system-exclude, compose-discovery
# STRUCTURE: ▶ tmp_path mock modules → ◇ discover_docker_modules API 4× (all/system/no-compose/empty) → ◇ CLI 2× (json/lines) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for module_discovery.py — discover_docker_modules() API + CLI main().
## @scope    Tests exclude-system, exclude-no-compose, empty-dir, JSON/Lines CLI output.
## @invariants
##   - All tests create mock module directories under tmp_path
##   - API tests import discover_docker_modules directly via sys.path.insert
##   - CLI tests run via subprocess.run against main() as __main__
##   - Each test is decorated with @ldd_trajectory and asserts IMP:9 log presence
## @rationale DevPlan 048 TASK-1: Unit coverage for CICD-01 module discovery module
## @changes  2026-07-22 | Created (DevPlan 048 TASK-1)
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import module_discovery as md

# ═══════════════════════════════════════════════════════════════════
# region Tests: discover_docker_modules API
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · discover_docker_modules returns all non-system modules with compose file
# · Scenario: 3 mock module dirs with module.yaml + docker-compose.base.yml → 3 compose paths returned
# · Last fail: N/A (new test)
# · Remove if: discover_docker_modules logic changes
@ldd_trajectory
def test_discover_all_non_system_modules(caplog, tmp_path):
    """discover_docker_modules should return all modules that are not system-typed and have compose files."""
    modules = ["postgres", "redis", "litellm"]
    for mod in modules:
        mod_dir = tmp_path / mod
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0")
        (mod_dir / "docker-compose.base.yml").write_text("services:\n  {mod}:\n    image: {mod}:latest")

    result = md.discover_docker_modules(tmp_path)

    assert len(result) == len(modules), f"Expected {len(modules)} modules, got {len(result)}"
    for mod in modules:
        expected = tmp_path / mod / "docker-compose.base.yml"
        assert expected in result, f"Expected {expected} in result"
    logger.critical("[IMP:9][test] All non-system modules discovered — count=%d", len(result))


# 🧪 TRAP[TEST] · Regression · System modules excluded from discovery
# · Scenario: 1 normal + 1 system module → only the normal module returned
# · Last fail: N/A (new test)
# · Remove if: discover_docker_modules system-filter logic changes
@ldd_trajectory
def test_exclude_system_modules(caplog, tmp_path):
    """discover_docker_modules should exclude modules with install_type: system."""
    # Normal module
    normal_dir = tmp_path / "nginx"
    normal_dir.mkdir()
    (normal_dir / "module.yaml").write_text("name: nginx\ninstall_type: docker\nversion: 1.0")
    (normal_dir / "docker-compose.base.yml").write_text("services:\n  nginx:\n    image: nginx:latest")

    # System module (should be excluded)
    system_dir = tmp_path / "system-agent"
    system_dir.mkdir()
    (system_dir / "module.yaml").write_text("name: system-agent\ninstall_type: system\nprivileged: true")
    (system_dir / "docker-compose.base.yml").write_text("services:\n  agent:\n    image: agent:latest")

    result = md.discover_docker_modules(tmp_path)

    assert len(result) == 1, f"Expected 1 module, got {len(result)}"
    assert normal_dir / "docker-compose.base.yml" in result
    assert system_dir / "docker-compose.base.yml" not in result
    logger.critical("[IMP:9][test] System modules correctly excluded")


# 🧪 TRAP[TEST] · Regression · Modules without docker-compose.base.yml excluded
# · Scenario: 1 module with compose + 1 module without → only the one with compose returned
# · Last fail: N/A (new test)
# · Remove if: discover_docker_modules compose-check logic changes
@ldd_trajectory
def test_exclude_no_compose_file(caplog, tmp_path):
    """discover_docker_modules should exclude modules that lack docker-compose.base.yml."""
    # Module with compose file
    has_compose_dir = tmp_path / "postgres"
    has_compose_dir.mkdir()
    (has_compose_dir / "module.yaml").write_text("name: postgres\ninstall_type: docker\nversion: 1.0")
    (has_compose_dir / "docker-compose.base.yml").write_text("services:\n  postgres:\n    image: postgres:latest")

    # Module WITHOUT compose file (should be excluded)
    no_compose_dir = tmp_path / "redis"
    no_compose_dir.mkdir()
    (no_compose_dir / "module.yaml").write_text("name: redis\ninstall_type: docker\nversion: 1.0")
    # No docker-compose.base.yml created here

    result = md.discover_docker_modules(tmp_path)

    assert len(result) == 1, f"Expected 1 module, got {len(result)}"
    assert has_compose_dir / "docker-compose.base.yml" in result
    assert no_compose_dir / "docker-compose.base.yml" not in result
    logger.critical("[IMP:9][test] No-compose modules correctly excluded")


# 🧪 TRAP[TEST] · Regression · Empty modules directory returns empty list
# · Scenario: Empty tmp_path → empty list returned
# · Last fail: N/A (new test)
# · Remove if: discover_docker_modules empty-dir handling changes
@ldd_trajectory
def test_empty_modules_dir(caplog, tmp_path):
    """discover_docker_modules should return empty list for an empty directory."""
    # tmp_path is empty — no modules at all
    result = md.discover_docker_modules(tmp_path)

    assert isinstance(result, list), "Result should be a list"
    assert len(result) == 0, f"Expected empty list, got {len(result)} modules"
    logger.critical("[IMP:9][test] Empty modules dir → empty result")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: CLI main() - subprocess
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI --format json produces valid JSON array
# · Scenario: 3 mock modules → CLI prints JSON array of 3 strings → parseable + correct length
# · Last fail: N/A (new test)
# · Remove if: CLI output format changes
@ldd_trajectory
def test_cli_json_output(caplog, tmp_path):
    """CLI --format json should output valid JSON array of module paths in stdout."""
    # Create mock modules
    modules = ["postgres", "redis", "litellm"]
    for mod in modules:
        mod_dir = tmp_path / mod
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0")
        (mod_dir / "docker-compose.base.yml").write_text(f"services:\n  {mod}:\n    image: {mod}:latest")

    # Run the script as a subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "module_discovery.py"), "--format", "json", "--modules-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI exited with code {result.returncode}: {result.stderr}"

    # Parse JSON
    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        pytest.fail(f"CLI output is not valid JSON: {e}\nOutput: {result.stdout!r}")

    assert isinstance(parsed, list), "JSON output should be a list"
    assert len(parsed) == len(modules), f"Expected {len(modules)} modules, got {len(parsed)}"

    # Verify each module compose path is present in the output
    for mod in modules:
        expected_path = str(tmp_path / mod / "docker-compose.base.yml")
        assert any(expected_path in entry for entry in parsed), f"Expected path {expected_path} in JSON output"

    logger.critical("[IMP:9][test] CLI JSON output valid")


# 🧪 TRAP[TEST] · Regression · CLI --format lines produces one line per module
# · Scenario: 2 mock modules → CLI prints 2 lines → line count matches
# · Last fail: N/A (new test)
# · Remove if: CLI output format changes
@ldd_trajectory
def test_cli_lines_output(caplog, tmp_path):
    """CLI --format lines should output one path per line matching module count."""
    # Create mock modules
    modules = ["postgres", "redis"]
    for mod in modules:
        mod_dir = tmp_path / mod
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0")
        (mod_dir / "docker-compose.base.yml").write_text(f"services:\n  {mod}:\n    image: {mod}:latest")

    # Run the script as a subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "module_discovery.py"), "--format", "lines", "--modules-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI exited with code {result.returncode}: {result.stderr}"

    # Count lines (excluding empty trailing line from split)
    lines = [line for line in result.stdout.strip().split("\n") if line]
    assert len(lines) == len(modules), f"Expected {len(modules)} lines, got {len(lines)}:\n{result.stdout}"

    # Verify each module compose path is present
    for mod in modules:
        expected_path = str(tmp_path / mod / "docker-compose.base.yml")
        assert any(expected_path in line for line in lines), f"Expected path {expected_path} in lines output"

    logger.critical("[IMP:9][test] CLI lines output valid")


# endregion
