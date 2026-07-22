"""
# GREP_SUMMARY: test_vps_status_check, unit, stdin-json, parse-status, cli-exit-codes, empty-stdin, malformed-json
# STRUCTURE: ▶ parse_status_json() direct calls ⊗ subprocess CLI invocations → ◇ valid status {found,stub} → ⊕ empty/malformed → ⊕ invalid status → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for vps_status_check.py — CLI-валидатор статуса проекта на VPS.
##           Проверяет parse_status_json() (API) и main() (CLI с stdin pipe).
## @scope    T3.1–T3.5: parse_status_json direct calls (valid, empty, whitespace, malformed)
##           T3.6–T3.10: CLI subprocess.run (exit codes, --output-status-only)
## @invariants
##   - EmptyStdinError (subclass of ValueError) raised before json.JSONDecodeError for empty/whitespace
##   - CLI exit codes: 0=valid, 1=invalid status, 2=malformed/non-dict, 3=empty stdin
##   - --output-status-only prints bare status to stdout, exit 0
##   - All tests use @ldd_trajectory decorator with IMP:9 log assertions
## @rationale DevPlan 048 TASK-3: unit-тесты для vps_status_check.py — заменяет inline python3 в CI.
##            Без тестов модуль беззащитен перед регрессиями статус-валидации.
## @changes  2026-07-22 | DevPlan 048 TASK-3 — Created
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

# ═══════════════════════════════════════════════════════════════════
# region IMPORTS: module under test
# ═══════════════════════════════════════════════════════════════════

_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_MODULE_DIR))

import vps_status_check as vsc

# Ссылка на скрипт для CLI subprocess тестов
_SCRIPT = _MODULE_DIR / "vps_status_check.py"

# endregion

# ═══════════════════════════════════════════════════════════════════
# region T3.1–T3.5: parse_status_json (direct API)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · parse_status_json with status="found" returns dict
# · Scenario: Direct call with valid JSON containing status="found"
# · Last fail: N/A (new test)
# · Remove if: parse_status_json implementation changes
@ldd_trajectory
def test_parse_valid_status_found(caplog):
    """parse_status_json('{"status": "found"}') → dict with status='found'."""
    result = vsc.parse_status_json('{"status": "found"}')
    assert isinstance(result, dict)
    assert result["status"] == "found"
    logger.critical("[IMP:9][test] parse_status_json valid status 'found' — returned dict with status='found'")


# 🧪 TRAP[TEST] · Regression · parse_status_json with status="stub" returns dict
# · Scenario: Direct call with valid JSON containing status="stub"
# · Last fail: N/A (new test)
# · Remove if: parse_status_json implementation changes
@ldd_trajectory
def test_parse_valid_status_stub(caplog):
    """parse_status_json('{"status": "stub"}') → dict with status='stub'."""
    result = vsc.parse_status_json('{"status": "stub"}')
    assert isinstance(result, dict)
    assert result["status"] == "stub"
    logger.critical("[IMP:9][test] parse_status_json valid status 'stub' — returned dict with status='stub'")


# 🧪 TRAP[TEST] · Regression · parse_status_json with empty string raises EmptyStdinError
# · Scenario: Direct call with "" → EmptyStdinError (not json.JSONDecodeError)
# · Last fail: N/A (new test)
# · Remove if: exception priority changes
@ldd_trajectory
def test_parse_empty_stdin(caplog):
    """parse_status_json('') → raises EmptyStdinError."""
    with pytest.raises(vsc.EmptyStdinError):
        vsc.parse_status_json("")
    logger.critical("[IMP:9][test] parse_status_json empty stdin — EmptyStdinError raised (correct priority)")


# 🧪 TRAP[TEST] · Regression · parse_status_json with whitespace-only raises EmptyStdinError
# · Scenario: Direct call with "   " → EmptyStdinError (validates the strip() check)
# · Last fail: N/A (new test)
# · Remove if: whitespace handling changes
@ldd_trajectory
def test_parse_whitespace_stdin(caplog):
    """parse_status_json('   ') → raises EmptyStdinError."""
    with pytest.raises(vsc.EmptyStdinError):
        vsc.parse_status_json("   ")
    logger.critical("[IMP:9][test] parse_status_json whitespace stdin — EmptyStdinError raised")


# 🧪 TRAP[TEST] · Regression · parse_status_json with malformed string raises json.JSONDecodeError
# · Scenario: Direct call with "not json" → json.JSONDecodeError
# · Last fail: N/A (new test)
# · Remove if: JSON parsing changes
@ldd_trajectory
def test_parse_malformed_json(caplog):
    """parse_status_json('not json') → raises json.JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        vsc.parse_status_json("not json")
    logger.critical("[IMP:9][test] parse_status_json malformed JSON — json.JSONDecodeError raised")

# endregion

# ═══════════════════════════════════════════════════════════════════
# region T3.6–T3.10: CLI subprocess tests
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · CLI with valid status exits 0
# · Scenario: subprocess.run with stdin='{"status":"found"}' → exit 0
# · Last fail: N/A (new test)
# · Remove if: CLI exit code logic changes
@ldd_trajectory
def test_cli_valid_status(caplog):
    """CLI with valid status → exit 0."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input='{"status": "found"}',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    logger.critical("[IMP:9][test] CLI valid status — exit 0")


# 🧪 TRAP[TEST] · Regression · CLI with invalid status exits 1
# · Scenario: subprocess.run with stdin='{"status":"dead"}' → exit 1
# · Last fail: N/A (new test)
# · Remove if: CLI exit code logic changes
@ldd_trajectory
def test_cli_invalid_status(caplog):
    """CLI with invalid status → exit 1."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input='{"status": "dead"}',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    logger.critical("[IMP:9][test] CLI invalid status — exit 1")


# 🧪 TRAP[TEST] · Regression · CLI with empty stdin exits 3
# · Scenario: subprocess.run with empty stdin → exit 3
# · Last fail: N/A (new test)
# · Remove if: CLI empty stdin handling changes
@ldd_trajectory
def test_cli_empty_stdin(caplog):
    """CLI with empty stdin → exit 3."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3
    logger.critical("[IMP:9][test] CLI empty stdin — exit 3")


# 🧪 TRAP[TEST] · Regression · CLI with malformed JSON exits 2
# · Scenario: subprocess.run with stdin="bad" → exit 2 (malformed JSON/non-dict)
# · Last fail: N/A (new test)
# · Remove if: CLI malformed JSON handling changes
@ldd_trajectory
def test_cli_malformed_json(caplog):
    """CLI with malformed JSON → exit 2."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="bad",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    logger.critical("[IMP:9][test] CLI malformed JSON — exit 2")


# 🧪 TRAP[TEST] · Regression · CLI --output-status-only prints bare status, exits 0
# · Scenario: subprocess.run with --output-status-only flag → stdout="found", exit 0
# · Last fail: N/A (new test)
# · Remove if: --output-status-only logic changes
@ldd_trajectory
def test_cli_output_status_only(caplog):
    """CLI --output-status-only prints bare status, exit 0."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--output-status-only"],
        input='{"status": "found"}',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "found"
    logger.critical("[IMP:9][test] CLI --output-status-only — stdout='found', exit 0")

# endregion
