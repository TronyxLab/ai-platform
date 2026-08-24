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
##           2026-08-12 | DevPlan 160 W2 T2.2 — MERGE root test_unit_module_discovery.py:
##           +test_sorted_alphabetically, +test_cli_empty_dir_json_outputs_empty_array
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

pytestmark = pytest.mark.static_audit

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
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
        (mod_dir / "docker-compose.base.yml").write_text(
            f"services:\n  {mod}:\n    image: {mod}:latest", encoding="utf-8"
        )

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
    (normal_dir / "module.yaml").write_text("name: nginx\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
    (normal_dir / "docker-compose.base.yml").write_text(
        "services:\n  nginx:\n    image: nginx:latest", encoding="utf-8"
    )

    # System module (should be excluded)
    system_dir = tmp_path / "system-agent"
    system_dir.mkdir()
    (system_dir / "module.yaml").write_text(
        "name: system-agent\ninstall_type: system\nprivileged: true", encoding="utf-8"
    )
    (system_dir / "docker-compose.base.yml").write_text(
        "services:\n  agent:\n    image: agent:latest", encoding="utf-8"
    )

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
    (has_compose_dir / "module.yaml").write_text("name: postgres\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
    (has_compose_dir / "docker-compose.base.yml").write_text(
        "services:\n  postgres:\n    image: postgres:latest", encoding="utf-8"
    )

    # Module WITHOUT compose file (should be excluded)
    no_compose_dir = tmp_path / "redis"
    no_compose_dir.mkdir()
    (no_compose_dir / "module.yaml").write_text("name: redis\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
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


# 🧪 TRAP[TEST] · Regression · U-59 (DevPlan 116 T7) · comment mentioning install_type: system NOT excluded
# · Scenario: module.yaml comment contains «install_type: system» (prose) + install_type: docker
# ·   → module INCLUDED (line-anchored regex, not substring)
# · Last fail: N/A (new test — substring false-positive fix)
# · Remove if: discover_docker_modules detection logic changes
@ldd_trajectory
def test_comment_mentioning_system_not_excluded(caplog, tmp_path):
    """Comments mentioning 'install_type: system' must NOT trigger exclusion (U-59)."""
    mod_dir = tmp_path / "nginx"
    mod_dir.mkdir()
    # Comment line starts with '#' — line-anchored regex must not match it
    content = (
        "# NOTE: install_type: system modules are managed by systemd, not compose\nname: nginx\ninstall_type: docker\n"
    )
    (mod_dir / "module.yaml").write_text(content, encoding="utf-8")
    (mod_dir / "docker-compose.base.yml").write_text("services:\n  nginx:\n    image: nginx:latest", encoding="utf-8")

    result = md.discover_docker_modules(tmp_path)

    assert len(result) == 1, f"Expected nginx included, got {len(result)} module(s)"
    assert mod_dir / "docker-compose.base.yml" in result
    logger.critical("[IMP:9][test] Comment mentioning install_type: system does not exclude module")


# 🧪 TRAP[TEST] · Regression · U-59 (DevPlan 116 T7) · quoted/indented install_type: system excluded
# · Scenario: `install_type: "system"` (quoted) and `  install_type: system` (indented) → excluded
# · Last fail: N/A (new test)
# · Remove if: discover_docker_modules detection logic changes
@ldd_trajectory
def test_quoted_and_indented_system_excluded(caplog, tmp_path):
    """install_type: system with quotes/indentation must be excluded (line-anchored regex)."""
    # Quoted form
    quoted_dir = tmp_path / "quoted-system"
    quoted_dir.mkdir()
    (quoted_dir / "module.yaml").write_text('name: quoted-system\ninstall_type: "system"\n', encoding="utf-8")
    (quoted_dir / "docker-compose.base.yml").write_text(
        "services:\n  agent:\n    image: agent:latest", encoding="utf-8"
    )

    # Indented form (inside a block)
    indented_dir = tmp_path / "indented-system"
    indented_dir.mkdir()
    (indented_dir / "module.yaml").write_text("name: indented-system\n  install_type: system\n", encoding="utf-8")
    (indented_dir / "docker-compose.base.yml").write_text(
        "services:\n  agent:\n    image: agent:latest", encoding="utf-8"
    )

    result = md.discover_docker_modules(tmp_path)

    assert len(result) == 0, f"Expected both system modules excluded, got {len(result)}"
    logger.critical("[IMP:9][test] Quoted/indented install_type: system both excluded")


# 🧪 TRAP[TEST] · MERGED (W2 T2.2) · Determinism: sorted output for stable CI behavior
# · Scenario: modules created in non-sorted order → result sorted alphabetically
# · Last fail: N/A (перенесено из tests/test_unit_module_discovery.py root)
# · Remove if: sort contract changes
@ldd_trajectory
def test_sorted_alphabetically(caplog, tmp_path):
    """Modules are sorted alphabetically by directory name (deterministic output)."""
    for name in ["zeta", "alpha", "middle"]:
        mod_dir = tmp_path / name
        mod_dir.mkdir()
        (mod_dir / "module.yaml").write_text(f"name: {name}\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
        (mod_dir / "docker-compose.base.yml").write_text(
            f"services:\n  {name}:\n    image: {name}:latest", encoding="utf-8"
        )

    result = md.discover_docker_modules(tmp_path)
    names = [p.parent.name for p in result]
    assert names == ["alpha", "middle", "zeta"], f"Expected sorted order, got {names}"
    logger.critical("[IMP:9][test] Modules sorted alphabetically: %s", names)


# 🧪 TRAP[TEST] · Regression · U-59 (DevPlan 116 T7) · bootstrap adapter uses canonical predicate
# · Scenario: bootstrap discover_modules() delegates to canonical discover_docker_modules
# ·   (same 14 modules on real core/modules — 010 T3.1: +log-collector; adapter maps Path → repo-relative strings)
# · Last fail: N/A (new test)
# · Remove if: bootstrap adapter changes
@ldd_trajectory
def test_bootstrap_uses_canonical_predicate(caplog):
    """bootstrap discover_modules must delegate to the canonical predicate (D3, one code)."""
    from core.internal.bootstrap.discover_modules import discover_modules as bootstrap_discover
    from core.internal.scripts.module_discovery import discover_docker_modules as canonical_discover

    project_root = Path(__file__).resolve().parent.parent.parent
    modules_dir = project_root / "core" / "modules"

    canonical = canonical_discover(modules_dir)
    bootstrap = bootstrap_discover(modules_dir)

    # Set-equality of module names — both consumers give identical results (13==13)
    canonical_names = {p.parent.name for p in canonical}
    bootstrap_names = {Path(p).parent.name for p in bootstrap}
    assert canonical_names == bootstrap_names, (
        f"bootstrap/canonical predicates diverged: {sorted(canonical_names)} vs {sorted(bootstrap_names)}"
    )
    assert len(canonical_names) == 15, (
        f"Expected 15 docker modules (010 T3.1: +log-collector; T3.2: split infra-metrics "
        f"into node-metrics + service-exporters), got {len(canonical_names)}"
    )
    logger.critical("[IMP:9][test] bootstrap == canonical predicate (%d docker modules)", len(canonical_names))


# endregion Tests: discover_docker_modules API


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
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
        (mod_dir / "docker-compose.base.yml").write_text(
            f"services:\n  {mod}:\n    image: {mod}:latest", encoding="utf-8"
        )

    # Run the script as a subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "module_discovery.py"), "--format", "json", "--modules-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
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
        (mod_dir / "module.yaml").write_text(f"name: {mod}\ninstall_type: docker\nversion: 1.0", encoding="utf-8")
        (mod_dir / "docker-compose.base.yml").write_text(
            f"services:\n  {mod}:\n    image: {mod}:latest", encoding="utf-8"
        )

    # Run the script as a subprocess
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_DIR / "module_discovery.py"), "--format", "lines", "--modules-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
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


# 🧪 TRAP[TEST] · MERGED (W2 T2.2) · Edge case: empty dir CLI JSON → "[]" (no crash)
# · Scenario: CLI --format json on empty modules dir → returncode 0, stdout "[]"
# · Last fail: N/A (перенесено из tests/test_unit_module_discovery.py root)
# · Remove if: module_discovery.py CLI removed
@ldd_trajectory
def test_cli_empty_dir_json_outputs_empty_array(caplog, tmp_path):
    """CLI on empty dir outputs `[]` for JSON format (no crash)."""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_DIR / "module_discovery.py"),
            "--format",
            "json",
            "--modules-dir",
            str(modules_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"CLI exited with code {result.returncode}: {result.stderr}"
    assert json.loads(result.stdout) == []
    logger.critical("[IMP:9][test] CLI empty-dir JSON output valid (empty array)")


# endregion Tests: CLI main() - subprocess
