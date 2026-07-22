# GREP_SUMMARY: module-discovery, unit-test, system-filter, json-lines-format, zero-deps
# STRUCTURE: test_discovers_modules → test_filters_system → test_json_format → test_lines_format → test_empty_dir → test_missing_compose
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/module_discovery.py
## @scope    Verify docker module discovery: filtering system modules, JSON/lines output, edge cases
## @invariants
##   - Non-system modules with docker-compose.base.yml → included
##   - System modules (install_type: system) → excluded
##   - Modules without docker-compose.base.yml → excluded
##   - Output sorted alphabetically by module name
## @rationale StatusReport 046 T2 (CICD-01a): replaces 4× inline python3 in CI workflows
## @changes
##   LAST_CHANGE: 2026-07-22 | Created (StatusReport 046 T8)
# endregion MODULE_CONTRACT

import json
import pathlib
import subprocess
import sys

import pytest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "core" / "internal" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from module_discovery import discover_docker_modules  # type: ignore[import-not-found]

# region FIXTURES


def _make_module(base: pathlib.Path, name: str, *, system: bool = False, with_compose: bool = True) -> None:
    """Create a fake module directory with module.yaml + optional docker-compose.base.yml.

    ## @purpose  Test fixture helper — fabricate module skeleton
    """
    mod_dir = base / "core" / "modules" / name
    mod_dir.mkdir(parents=True, exist_ok=True)
    yaml_content = f"name: {name}\n"
    if system:
        yaml_content += "install_type: system\n"
    (mod_dir / "module.yaml").write_text(yaml_content)
    if with_compose:
        (mod_dir / "docker-compose.base.yml").write_text("services: {}\n")


# endregion FIXTURES


# region API_TESTS


def test_discovers_non_system_modules(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """discover_docker_modules returns compose paths for non-system modules.

    ## @purpose — Happy path: regular modules with compose files discovered
    # 🧪 TRAP[TEST] · Scenario · Happy-path discovery · Last fail: N/A
    # · Remove if: module_discovery.py API removed
    """
    # region SETUP
    _make_module(tmp_path, "alpha")
    _make_module(tmp_path, "beta")
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = discover_docker_modules(modules_dir)
    # endregion EXECUTE

    # region VERIFY
    found_log = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    captured = capsys.readouterr()
    for line in captured.err.splitlines():
        if "[IMP:" in line:
            imp = int(line.split("[IMP:")[1].split("]")[0])
            if imp >= 7:
                print(line)
            if imp >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "No IMP:9 business logic log found"

    assert len(result) == 2
    names = sorted(p.parent.name for p in result)
    assert names == ["alpha", "beta"]
    for p in result:
        assert p.name == "docker-compose.base.yml"
    # endregion VERIFY


def test_filters_system_modules(tmp_path: pathlib.Path) -> None:
    """System modules (install_type: system) are excluded.

    ## @purpose — Filter regression: install_type: system detection via text search
    # 🧪 TRAP[TEST] · Scenario · System module filter · Last fail: N/A
    # · Remove if: install_type semantics change
    """
    # region SETUP
    _make_module(tmp_path, "regular")
    _make_module(tmp_path, "system-one", system=True)
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = discover_docker_modules(modules_dir)
    # endregion EXECUTE

    # region VERIFY
    assert len(result) == 1
    assert result[0].parent.name == "regular"
    # endregion VERIFY


def test_excludes_modules_without_compose(tmp_path: pathlib.Path) -> None:
    """Modules without docker-compose.base.yml are skipped.

    ## @purpose — Edge case: module.yaml exists but compose missing
    # 🧪 TRAP[TEST] · Scenario · Missing compose file · Last fail: N/A
    # · Remove if: compose filename contract changes
    """
    # region SETUP
    _make_module(tmp_path, "with-compose")
    _make_module(tmp_path, "no-compose", with_compose=False)
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = discover_docker_modules(modules_dir)
    # endregion EXECUTE

    # region VERIFY
    assert len(result) == 1
    assert result[0].parent.name == "with-compose"
    # endregion VERIFY


def test_empty_modules_dir_returns_empty(tmp_path: pathlib.Path) -> None:
    """Empty modules directory returns empty list (no crash).

    ## @purpose — Edge case: no module.yaml files at all
    # 🧪 TRAP[TEST] · Scenario · Empty dir · Last fail: N/A
    # · Remove if: module_discovery.py API removed
    """
    modules_dir = tmp_path / "core" / "modules"
    modules_dir.mkdir(parents=True)

    result = discover_docker_modules(modules_dir)

    assert result == []


def test_sorted_alphabetically(tmp_path: pathlib.Path) -> None:
    """Modules are sorted alphabetically by directory name.

    ## @purpose — Determinism: sorted output for stable CI behavior
    # 🧪 TRAP[TEST] · Scenario · Sort order · Last fail: N/A
    # · Remove if: sort contract changes
    """
    # region SETUP — create in non-sorted order
    for name in ["zeta", "alpha", "middle"]:
        _make_module(tmp_path, name)
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = discover_docker_modules(modules_dir)
    # endregion VERIFY
    names = [p.parent.name for p in result]
    assert names == ["alpha", "middle", "zeta"]


# endregion API_TESTS


# region CLI_TESTS


def _run_cli(modules_dir: pathlib.Path, fmt: str) -> subprocess.CompletedProcess:
    """Run module_discovery.py as subprocess with given format.

    ## @purpose  CLI wrapper for subprocess-based format tests
    """
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "module_discovery.py"),
            "--format",
            fmt,
            "--modules-dir",
            str(modules_dir),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_cli_json_format(tmp_path: pathlib.Path) -> None:
    """CLI --format json outputs valid JSON array.

    ## @purpose — CLI contract: JSON output for CI consumption
    # 🧪 TRAP[TEST] · Scenario · JSON output · Last fail: N/A
    # · Remove if: --format json removed
    """
    # region SETUP
    _make_module(tmp_path, "alpha")
    _make_module(tmp_path, "beta")
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = _run_cli(modules_dir, "json")
    # endregion EXECUTE

    # region VERIFY
    assert result.returncode == 0, f"stderr={result.stderr}"
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    # All entries end with docker-compose.base.yml
    for entry in parsed:
        assert entry.endswith("docker-compose.base.yml")
    # endregion VERIFY


def test_cli_lines_format(tmp_path: pathlib.Path) -> None:
    """CLI --format lines outputs one file path per line.

    ## @purpose — CLI contract: lines output for shell iteration
    # 🧪 TRAP[TEST] · Scenario · Lines output · Last fail: N/A
    # · Remove if: --format lines removed
    """
    # region SETUP
    _make_module(tmp_path, "alpha")
    _make_module(tmp_path, "beta")
    modules_dir = tmp_path / "core" / "modules"
    # endregion SETUP

    # region EXECUTE
    result = _run_cli(modules_dir, "lines")
    # endregion EXECUTE

    # region VERIFY
    assert result.returncode == 0, f"stderr={result.stderr}"
    lines = [ln for ln in result.stdout.strip().splitlines() if ln]
    assert len(lines) == 2
    for ln in lines:
        assert ln.endswith("docker-compose.base.yml")
    # endregion VERIFY


def test_cli_empty_dir_json_outputs_empty_array(tmp_path: pathlib.Path) -> None:
    """CLI on empty dir outputs `[]` for JSON format (no crash).

    ## @purpose — Edge case: empty dir CLI behavior
    # 🧪 TRAP[TEST] · Scenario · Empty dir CLI · Last fail: N/A
    # · Remove if: module_discovery.py CLI removed
    """
    modules_dir = tmp_path / "core" / "modules"
    modules_dir.mkdir(parents=True)

    result = _run_cli(modules_dir, "json")

    assert result.returncode == 0
    assert json.loads(result.stdout) == []


# endregion CLI_TESTS
