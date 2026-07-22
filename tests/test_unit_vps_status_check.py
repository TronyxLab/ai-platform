# GREP_SUMMARY: vps-status-check, unit-test, stdin-json, status-validation, output-status-only
# STRUCTURE: test_valid_status → test_invalid_status → test_malformed_json → test_empty_stdin → test_output_status_only → test_non_object_root
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/vps_status_check.py
## @scope    Verify VPS status JSON validation from stdin: valid/invalid statuses, error paths, --output-status-only
## @invariants
##   - status ∈ {found, stub} → exit 0
##   - status ∉ {found, stub} → exit 1
##   - malformed JSON → exit 2
##   - empty stdin → exit 3
##   - --output-status-only → prints bare status, exit 0
## @rationale StatusReport 046 T5 (CICD-01d): replaces inline python3 in deploy-project.yml
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

from vps_status_check import EmptyStdinError, parse_status_json  # type: ignore[import-not-found]

# region API_TESTS


def test_parse_status_json_valid() -> None:
    """parse_status_json parses valid JSON dict.

    ## @purpose — Happy path JSON parsing
    # 🧪 TRAP[TEST] · Scenario · Valid JSON · Last fail: N/A
    # · Remove if: parse_status_json API removed
    """
    result = parse_status_json('{"status": "found"}')

    assert result == {"status": "found"}


def test_parse_status_json_empty_raises() -> None:
    """parse_status_json raises EmptyStdinError on empty input.

    ## @purpose — Edge case: empty stdin detection (distinct from malformed JSON)
    # 🧪 TRAP[TEST] · Scenario · Empty stdin · Last fail: N/A
    # · Remove if: empty handling removed
    """
    with pytest.raises(EmptyStdinError):
        parse_status_json("")


def test_parse_status_json_malformed_raises() -> None:
    """parse_status_json raises JSONDecodeError on malformed JSON.

    ## @purpose — Error path: malformed JSON
    # 🧪 TRAP[TEST] · Scenario · Malformed JSON · Last fail: N/A
    # · Remove if: parse_status_json API removed
    """
    with pytest.raises(json.JSONDecodeError):
        parse_status_json("{not json")


# endregion API_TESTS


# region CLI_TESTS


def _run_cli(stdin: str, *extra_args: str) -> subprocess.CompletedProcess:
    """Run vps_status_check.py with given stdin and extra args.

    ## @purpose  CLI wrapper helper for all CLI tests
    """
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "vps_status_check.py"), *extra_args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_cli_valid_status_found_exits_zero() -> None:
    """CLI with status=found exits 0.

    ## @purpose — Happy path: found status
    # 🧪 TRAP[TEST] · Scenario · status=found · Last fail: N/A
    # · Remove if: CLI removed
    """
    result = _run_cli('{"status": "found"}')

    assert result.returncode == 0, f"stderr={result.stderr}"


def test_cli_valid_status_stub_exits_zero() -> None:
    """CLI with status=stub exits 0.

    ## @purpose — Happy path: stub status
    # 🧪 TRAP[TEST] · Scenario · status=stub · Last fail: N/A
    # · Remove if: CLI removed
    """
    result = _run_cli('{"status": "stub"}')

    assert result.returncode == 0, f"stderr={result.stderr}"


def test_cli_invalid_status_exits_one() -> None:
    """CLI with unexpected status exits 1.

    ## @purpose — Error path: invalid status value
    # 🧪 TRAP[TEST] · Scenario · invalid status · Last fail: N/A
    # · Remove if: valid status set changes
    """
    result = _run_cli('{"status": "deploying"}')

    assert result.returncode == 1
    assert "unexpected status" in result.stderr.lower()


def test_cli_missing_status_key_exits_one() -> None:
    """CLI with missing status key (defaults to '') exits 1.

    ## @purpose — Edge case: missing 'status' field
    # 🧪 TRAP[TEST] · Scenario · missing status key · Last fail: N/A
    # · Remove if: missing key handling changes
    """
    result = _run_cli('{"other": "value"}')

    assert result.returncode == 1


def test_cli_malformed_json_exits_two() -> None:
    """CLI with malformed JSON exits 2.

    ## @purpose — Error path: malformed JSON
    # 🧪 TRAP[TEST] · Scenario · malformed JSON · Last fail: N/A
    # · Remove if: exit code contract changes
    """
    result = _run_cli("{not valid json")

    assert result.returncode == 2
    assert "invalid json" in result.stderr.lower()


def test_cli_empty_stdin_exits_three() -> None:
    """CLI with empty stdin exits 3.

    ## @purpose — Edge case: empty stdin
    # 🧪 TRAP[TEST] · Scenario · empty stdin · Last fail: N/A
    # · Remove if: empty stdin exit code changes
    """
    result = _run_cli("")

    assert result.returncode == 3
    assert "empty stdin" in result.stderr.lower()


def test_cli_whitespace_stdin_exits_three() -> None:
    """CLI with whitespace-only stdin exits 3 (treated as empty).

    ## @purpose — Edge case: whitespace stdin
    # 🧪 TRAP[TEST] · Scenario · whitespace stdin · Last fail: N/A
    # · Remove if: strip() handling removed
    """
    result = _run_cli("   \n  \t  ")

    assert result.returncode == 3


def test_cli_non_object_root_exits_two() -> None:
    """CLI with JSON array root (not object) exits 2.

    ## @purpose — Error path: non-object JSON root
    # 🧪 TRAP[TEST] · Scenario · array root · Last fail: N/A
    # · Remove if: root type check removed
    """
    result = _run_cli('["not", "object"]')

    assert result.returncode == 2


def test_cli_output_status_only_found() -> None:
    """--output-status-only prints bare status value (found).

    ## @purpose — DRIFT-046-3: subshell use-case prints bare status
    # 🧪 TRAP[TEST] · Scenario · --output-status-only found · Last fail: N/A
    # · Remove if: --output-status-only flag removed
    """
    result = _run_cli('{"status": "found"}', "--output-status-only")

    assert result.returncode == 0
    assert result.stdout.strip() == "found"


def test_cli_output_status_only_invalid_status_still_zero() -> None:
    """--output-status-only prints bare status even for invalid values (exit 0).

    ## @purpose — DRIFT-046-3: print-mode never fails (used in subshell echo)
    # 🧪 TRAP[TEST] · Scenario · --output-status-only invalid · Last fail: N/A
    # · Remove if: --output-status-only validation semantics change
    """
    result = _run_cli('{"status": "deploying"}', "--output-status-only")

    assert result.returncode == 0
    assert result.stdout.strip() == "deploying"


def test_cli_output_status_only_missing_key_prints_empty() -> None:
    """--output-status-only with missing status key prints empty string.

    ## @purpose — Edge case: missing status in print mode
    # 🧪 TRAP[TEST] · Scenario · --output-status-only missing · Last fail: N/A
    # · Remove if: missing key default changes
    """
    result = _run_cli('{"other": "value"}', "--output-status-only")

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_cli_output_status_only_with_extra_fields() -> None:
    """--output-status-only extracts status from JSON with extra fields.

    ## @purpose — Forward compat: ignores extra fields in status JSON
    # 🧪 TRAP[TEST] · Scenario · extra fields · Last fail: N/A
    # · Remove if: status JSON schema becomes strict
    """
    stdin = json.dumps({"status": "stub", "project": "test", "version": "1.0", "timestamp": "2026-07-22"})
    result = _run_cli(stdin, "--output-status-only")

    assert result.returncode == 0
    assert result.stdout.strip() == "stub"


# endregion CLI_TESTS
