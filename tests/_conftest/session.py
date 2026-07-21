# GREP_SUMMARY: session, conftest, pytest, session-hooks, escalation, anti-loop, attempt-counter, sessionstart, sessionfinish
# STRUCTURE: pytest_sessionstart(read_counter→increment→write_counter) → run_tests → pytest_sessionfinish(◇exitstatus==0→reset|◇exitstatus!=0→check_escalation(attempts≤2→checklist|==3→external|==4→reflection|≥5→critical))
# region MODULE_CONTRACT
## @purpose  Pytest session hooks (sessionstart/sessionfinish) + escalation dispatch for Anti-Loop protocol.
##           Increments attempt counter on session start, resets on 100% PASS, escalates on failure.
## @scope    Session-level hooks extracted from tests/conftest.py. Counter read/write delegated to
##           conftest.counter; escalation messages delegated to conftest.checklist.
## @invariants
##   - .test_counter.json stored in tests/ directory (managed by conftest.counter)
##   - Counter increments on every non-100% session (in sessionstart)
##   - Counter resets to 0 only when exitstatus == 0 (all tests passed, in sessionfinish)
##   - Escalation levels: 1-2=checklist, 3=external help, 4=reflection, 5+=critical
##   - PYTEST_NO_ESCALATION env var suppresses escalation output (used by git hooks)
##   - retention module loaded via importlib from core/modules/backup-cron/scripts/
## @rationale  Extracted from tests/conftest.py to reduce file size and isolate session lifecycle logic.
##             Path adjusted from __file__ (conftest/) → (conftest/../..) so core/ resolves correctly.
## @changes
##   LAST_CHANGE: 2026-07-12 | Extracted from tests/conftest.py — ESCALATION_DISPATCH + PYTEST_SESSION_HOOKS regions
# endregion MODULE_CONTRACT

import importlib.util
import json
import os
import pathlib
import sys

import jsonschema
import pytest
import yaml

from _conftest.checklist import _print_checklist, _print_escalation, _print_external_help, _print_reflection
from _conftest.counter import _read_counter, _write_counter

# region FIXTURE_SCHEMA_VALIDATION


# Mapping: test_data fixture filename → schema file (relative to project root)
_FIXTURE_SCHEMA_MAP = {
    "node.yaml": "core/schemas/node.schema.json",
    # Future fixtures — add entries here
}


def _validate_test_fixtures() -> None:
    """Validate all test fixtures against their schemas at session start.

    Fails fast with readable message BEFORE any test runs.
    Called from pytest_sessionstart in this module.

    Design decisions:
    - Uses jsonschema.validate (not Draft7Validator) for version-agnostic validation
    - Missing fixture file → skip (optional fixtures)
    - Missing schema file → pytest.exit (configuration error)
    - yaml.safe_load (not FullLoader) for security
    """
    # Resolve paths relative to tests/_conftest/session.py
    # session.py → tests/_conftest/ → tests/ → project root
    conftest_dir = pathlib.Path(__file__).resolve().parent  # tests/_conftest/
    test_data_dir = conftest_dir.parent / "test_data"  # tests/test_data/
    project_root = conftest_dir.parent.parent  # project root

    errors: list[str] = []
    for fixture_name, schema_relpath in _FIXTURE_SCHEMA_MAP.items():
        fixture_path = test_data_dir / fixture_name
        schema_path = project_root / schema_relpath

        if not fixture_path.exists():
            continue  # Optional fixture — skip silently

        if not schema_path.exists():
            pytest.exit(
                f"\n[IMP:10][sessionstart] Schema file not found: {schema_path}\n"
                f"Check _FIXTURE_SCHEMA_MAP in tests/_conftest/session.py\n"
            )

        with open(fixture_path) as f:
            data = yaml.safe_load(f)
        with open(schema_path) as f:
            schema = json.load(f)

        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            errors.append(f"  {fixture_path}: {e.message}")

    if errors:
        pytest.exit(
            "\n[IMP:10][sessionstart] Test fixture schema validation FAILED:\n"
            + "\n".join(errors)
            + "\n\nUpdate test fixtures to match current schemas.\n"
        )


# endregion FIXTURE_SCHEMA_VALIDATION

# region ESCALATION_DISPATCH


def _handle_escalation(attempts: int) -> None:
    """Print appropriate escalation message based on attempt count."""
    if attempts <= 2:
        _print_checklist()
    elif attempts == 3:
        _print_external_help()
    elif attempts == 4:
        _print_reflection()
    else:
        _print_escalation()


# endregion ESCALATION_DISPATCH


# region PYTEST_SESSION_HOOKS


def pytest_sessionstart(session: pytest.Session) -> None:
    """
    Session start hook: validate fixtures, increment attempt counter + conditional import.

    Read .test_counter.json, increment attempts, write back.
    Import retention.py ONLY when backup or test_retention marker is active —
    fail-fast: if retention.py is broken, it's discovered only when needed.
    """
    # ── FAIL-FAST: validate test fixtures BEFORE any test runs ──
    _validate_test_fixtures()

    # Conditional import: only for backup/retention tests
    _marker_option = session.config.getoption("-m", "")
    _is_backup_test = "backup" in _marker_option or "test_retention" in _marker_option
    if _is_backup_test:
        _backup_cron_scripts = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "core", "modules", "backup-cron", "scripts")
        )
        _retention_path = os.path.join(_backup_cron_scripts, "retention.py")
        if os.path.isfile(_retention_path):
            spec = importlib.util.spec_from_file_location("retention", _retention_path)
            if spec is not None and spec.loader is not None:
                retention_module = importlib.util.module_from_spec(spec)
                sys.modules["retention"] = retention_module
                spec.loader.exec_module(retention_module)
                print("[IMP:7][session] retention.py imported (backup/retention marker active)", file=sys.stderr)
            else:
                print("[IMP:9][session] retention.py found but spec/loader is None", file=sys.stderr)
        else:
            print("[IMP:8][session] retention.py not found — backup tests may fail", file=sys.stderr)
    else:
        print("[IMP:7][session] retention.py import skipped (no backup marker)", file=sys.stderr)

    counter = _read_counter()
    counter["attempts"] = counter.get("attempts", 0) + 1
    _write_counter(counter)
    print(
        f"[IMP:9][conftest][sessionstart] Attempt #{counter['attempts']} — running tests...",
        file=sys.stderr,
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """
    Session finish hook: reset counter on 100% PASS, else increment escalation.

    - exitstatus == 0 → all passed → reset counter to 0
    - exitstatus != 0 → failures → keep incremented counter, print escalation
    """
    counter = _read_counter()
    attempts = counter.get("attempts", 1)

    if exitstatus == pytest.ExitCode.OK:
        # Reset counter on full pass
        _write_counter({"attempts": 0})
        print(
            "[IMP:9][conftest][sessionfinish] 100% PASS — counter reset to 0",
            file=sys.stderr,
        )
    else:
        print(
            f"[IMP:9][conftest][sessionfinish] FAILURES DETECTED — attempt #{attempts}",
            file=sys.stderr,
        )
        # Suppress anti-loop escalation when PYTEST_NO_ESCALATION is set (git hooks)
        if not os.environ.get("PYTEST_NO_ESCALATION"):
            _handle_escalation(attempts)
        # Counter already incremented in sessionstart — persist as-is


# endregion PYTEST_SESSION_HOOKS
