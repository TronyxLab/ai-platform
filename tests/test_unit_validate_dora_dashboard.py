# GREP_SUMMARY: validate-dora-dashboard, unit-test, grafana, panel-validation, uid-check
# STRUCTURE: test_valid_dashboard → test_wrong_uid → test_missing_panel → test_malformed_json → test_missing_file → test_non_object_root
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/validate_dora_dashboard.py
## @scope    Verify DORA dashboard validation: uid check, required panels, error paths
## @invariants
##   - Valid dashboard (uid=dora-ci-cd + 4 required panels) → True
##   - Wrong uid → False
##   - Missing required panel → False
##   - Malformed JSON → False
##   - Missing file → False
## @rationale StatusReport 046 T3 (CICD-01b): replaces inline python3 in platform-test.yml
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

from validate_dora_dashboard import EXPECTED_UID, REQUIRED_PANELS, validate  # type: ignore[import-not-found]

# region FIXTURES


def _make_dashboard(base: pathlib.Path, *, uid: str = EXPECTED_UID, extra_panels: list | None = None) -> pathlib.Path:
    """Create a valid DORA dashboard file (or customized variant).

    ## @purpose  Test fixture — fabricate dashboard JSON
    """
    panels = [{"title": t, "type": "graph"} for t in REQUIRED_PANELS]
    if extra_panels:
        panels.extend(extra_panels)
    data = {"uid": uid, "title": "DORA CI/CD", "panels": panels}
    path = base / "dora-ci-cd.json"
    path.write_text(json.dumps(data))
    return path


# endregion FIXTURES


# region API_TESTS


def test_valid_dashboard_returns_true(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Valid dashboard (uid + 4 required panels) returns True.

    ## @purpose — Happy path validation
    # 🧪 TRAP[TEST] · Scenario · Valid dashboard · Last fail: N/A
    # · Remove if: validate() API removed
    """
    # region SETUP
    path = _make_dashboard(tmp_path)
    # endregion SETUP

    # region EXECUTE
    result = validate(path)
    captured = capsys.readouterr()
    # endregion EXECUTE

    # region VERIFY
    assert result is True

    found_log = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in captured.err.splitlines() + captured.out.splitlines():
        if "[IMP:" in line:
            imp = int(line.split("[IMP:")[1].split("]")[0])
            if imp >= 7:
                print(line)
            if imp >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "No IMP:9 success log found"
    # endregion VERIFY


def test_wrong_uid_returns_false(tmp_path: pathlib.Path) -> None:
    """Wrong uid returns False with diagnostic.

    ## @purpose — UID contract enforcement
    # 🧪 TRAP[TEST] · Scenario · Wrong UID · Last fail: N/A
    # · Remove if: uid contract removed
    """
    # region SETUP
    path = _make_dashboard(tmp_path, uid="wrong-uid")
    # endregion SETUP

    # region EXECUTE
    result = validate(path)
    # endregion VERIFY

    assert result is False


def test_missing_panel_returns_false(tmp_path: pathlib.Path) -> None:
    """Missing required panel returns False.

    ## @purpose — Required panels contract enforcement
    # 🧪 TRAP[TEST] · Scenario · Missing panel · Last fail: N/A
    # · Remove if: required panels list removed
    """
    # region SETUP — dashboard with only 3 of 4 required panels
    data = {
        "uid": EXPECTED_UID,
        "panels": [
            {"title": "Deploy Frequency"},
            {"title": "Lead Time for Changes"},
            {"title": "Mean Time to Recovery (MTTR)"},
            # Missing: Change Failure Rate (CFR)
        ],
    }
    path = tmp_path / "dora-ci-cd.json"
    path.write_text(json.dumps(data))
    # endregion SETUP

    # region EXECUTE & VERIFY
    result = validate(path)
    assert result is False
    # endregion EXECUTE & VERIFY


def test_extra_panels_allowed(tmp_path: pathlib.Path) -> None:
    """Extra panels beyond required 4 are allowed (dashboard has 5th panel).

    ## @purpose — Forward compat: extra panels don't break validation
    # 🧪 TRAP[TEST] · Scenario · Extra panel · Last fail: N/A
    # · Remove if: panel count becomes strict
    """
    # region SETUP — actual production dashboard has 5 panels (Workflow Runs 30d)
    path = _make_dashboard(tmp_path, extra_panels=[{"title": "Workflow Runs (30d)"}])
    # endregion SETUP

    # region EXECUTE & VERIFY
    result = validate(path)
    assert result is True
    # endregion EXECUTE & VERIFY


def test_malformed_json_returns_false(tmp_path: pathlib.Path) -> None:
    """Malformed JSON returns False (not crash).

    ## @purpose — Error path: invalid JSON handled gracefully
    # 🧪 TRAP[TEST] · Scenario · Malformed JSON · Last fail: N/A
    # · Remove if: JSON parsing replaced with YAML
    """
    # region SETUP
    path = tmp_path / "dora-ci-cd.json"
    path.write_text("{invalid json content")
    # endregion SETUP

    # region EXECUTE & VERIFY
    result = validate(path)
    assert result is False
    # endregion EXECUTE & VERIFY


def test_missing_file_returns_false(tmp_path: pathlib.Path) -> None:
    """Non-existent file returns False.

    ## @purpose — Error path: missing file handled gracefully
    # 🧪 TRAP[TEST] · Scenario · Missing file · Last fail: N/A
    # · Remove if: file existence check removed
    """
    path = tmp_path / "nonexistent.json"

    result = validate(path)

    assert result is False


def test_non_object_root_returns_false(tmp_path: pathlib.Path) -> None:
    """JSON root that is not an object returns False.

    ## @purpose — Error path: JSON array/number at root
    # 🧪 TRAP[TEST] · Scenario · Non-object root · Last fail: N/A
    # · Remove if: root type check removed
    """
    path = tmp_path / "dora-ci-cd.json"
    path.write_text(json.dumps(["not", "an", "object"]))

    result = validate(path)

    assert result is False


# endregion API_TESTS


# region CLI_TESTS


def test_cli_valid_dashboard_exits_zero(tmp_path: pathlib.Path) -> None:
    """CLI on valid dashboard exits 0.

    ## @purpose — CLI happy path
    # 🧪 TRAP[TEST] · Scenario · CLI valid · Last fail: N/A
    # · Remove if: CLI removed
    """
    path = _make_dashboard(tmp_path)

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "validate_dora_dashboard.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, f"stderr={result.stderr}"


def test_cli_invalid_dashboard_exits_nonzero(tmp_path: pathlib.Path) -> None:
    """CLI on invalid dashboard (wrong uid) exits 1.

    ## @purpose — CLI error path
    # 🧪 TRAP[TEST] · Scenario · CLI invalid · Last fail: N/A
    # · Remove if: CLI removed
    """
    path = _make_dashboard(tmp_path, uid="wrong")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "validate_dora_dashboard.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 1


def test_cli_default_path_works() -> None:
    """CLI with no args uses default production dashboard path.

    ## @purpose — CLI default path contract (used by platform-test.yml)
    # 🧪 TRAP[TEST] · Scenario · Default path · Last fail: N/A
    # · Remove if: default path removed
    """
    # Run from project root so default relative path resolves
    project_root = SCRIPTS_DIR.parents[2]
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "validate_dora_dashboard.py")],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(project_root),
    )

    assert result.returncode == 0, f"Default dashboard invalid: stderr={result.stderr}"


# endregion CLI_TESTS
