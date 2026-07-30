# GREP_SUMMARY: counter, .test_counter.json, anti-loop, attempt tracking, conftest
# STRUCTURE: _read_counter → json.load(.test_counter.json) → _write_counter → json.dump

# region MODULE_CONTRACT
## @purpose  Counter read/write functions for .test_counter.json — used by Anti-Loop protocol to track failed test attempts across sessions
## @scope    Reading and writing the attempt counter file; no orchestration or escalation logic
## @invariants
##   - _read_counter always returns a dict with key "attempts" (int >= 0)
##   - _write_counter overwrites the file atomically via json.dump
##   - Missing or malformed counter file silently defaults to {"attempts": 0}
## @rationale Extracted from tests/conftest.py COUNTER_IO region to isolate counter persistence logic;
##            path adjusted from __file__ (conftest/) to (conftest/..) so .test_counter.json remains in tests/
# endregion MODULE_CONTRACT

import json
import os

# region COUNTER_IO

_COUNTER_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".test_counter.json")


def _read_counter() -> dict:
    """Read attempt counter from .test_counter.json. Returns {'attempts': int}."""
    if not os.path.isfile(_COUNTER_FILE):
        return {"attempts": 0}
    try:
        with open(_COUNTER_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"attempts": 0}


def _write_counter(data: dict) -> None:
    """Write attempt counter to .test_counter.json."""
    with open(_COUNTER_FILE, "w") as f:
        json.dump(data, f)
        f.write("\n")


# endregion COUNTER_IO
