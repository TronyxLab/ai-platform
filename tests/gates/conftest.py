# GREP_SUMMARY: conftest, gates, anti-loop, pytest, counter, escalation, xdist-worker, master-guard
# STRUCTURE: ┌.test_counter.json read/write┐ → ◇ _is_xdist_worker (PYTEST_XDIST_WORKER) → ◇ pytest_sessionstart (master: increment) → ◇ pytest_sessionfinish (master: reset on 100% pass) → ⎋
# region MODULE_CONTRACT
## @purpose  Anti-Loop protocol for gate tests — tracks failed run attempts and escalates on repeated failures
## @scope    pytest hooks for session lifecycle: pytest_sessionstart (increment counter),
##           pytest_sessionfinish (reset counter on 100% PASS)
## @invariants
##   - Counter file: tests/gates/.test_counter.json
##   - Counter resets to 0 only at 100% PASS (all tests pass)
##   - On failure: counter increments; escalation messages printed at 1-2, 3, 4, 5+
##   - Individual test files NEVER call counter management functions
##   - Master-guard (DevPlan 124 T4): gates-чеки исполняются с -n auto (check-suite gates
##     xdist: true) — hooks session выполняются в каждом воркере; increment/reset — ТОЛЬКО
##     master (PYTEST_XDIST_WORKER отсутствует), воркеры — no-op
## @rationale Anti-Loop protocol (RULES.md §TESTING) prevents agents from repeating failed strategies infinitely
## @changes 2026-07-30 · DevPlan 088 Wave 4 — create gates conftest with Anti-Loop
## @changes 2026-08-03 · DevPlan 124 T4 — master-guard: _is_xdist_worker() гейт для
##           increment/reset (воркеры не искажают failed_runs при -n auto)
# endregion MODULE_CONTRACT

"""
Anti-Loop protocol for gate tests.

Prevents agents from repeating failed strategies infinitely by tracking
failed run counts in .test_counter.json.

Escalation levels:
  Attempt 1-2: Output CHECKLIST of common errors
  Attempt 3:   Suggest external help (MCP tavily or Context 7)
  Attempt 4:   Warning: looping risk — pause and reflect
  Attempt 5+:  CRITICAL ERROR: agent looping detected — STOP
"""

import json
import logging
import os
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

COUNTER_FILE = Path(__file__).resolve().parent / ".test_counter.json"
LOCK_FILE = COUNTER_FILE.parent / (COUNTER_FILE.name + ".lock")


# region FUNC_is_xdist_worker
## @purpose  Детекция xdist-воркера (PYTEST_XDIST_WORKER, DevPlan 124 T1/T4): gates-чеки
##           исполняются с -n auto (check-suite gates xdist: true, _apply_xdist) — hooks
##           session выполняются в каждом воркере; счётчик и escalation принадлежат master
##           (он видит aggregate-результат сессии).
## @io       → ⎋ bool
## @complexity O(1)
def _is_xdist_worker() -> bool:
    """True inside a pytest-xdist worker (PYTEST_XDIST_WORKER set)."""
    return bool(os.environ.get("PYTEST_XDIST_WORKER"))


# endregion FUNC_is_xdist_worker

# Checklist of common gate test errors
CHECKLIST = [
    "tmp_path not used — hardcoded paths in tests",
    "XML fixture content malformed or missing required sections",
    "REQUIRED_SECTIONS mismatch",
    "caplog level not set — IMP:7-10 logs not captured",
    "File not found — framework/ or granules/ dir missing",
    "Version stamp format incorrect",
    "merge_sections collision logic not handling duplicates",
    "yaml.safe_load called outside NodeYaml facade",
    "yq eval still present in core/ shell scripts",
]


# region CONTEXT_COUNTER_LOCK
## @purpose  Файловая блокировка flock (fcntl) вокруг counter RMW — xdist-безопасность
##           (DevPlan 120 §3.3): gates-чеки исполняются с -n auto, session-хуки — в каждом
##           worker'е; раздельные read/write .test_counter.json = гонка (потерянные обновления).
## @io       → with _CounterLock(): критическая секция
## @complexity O(1)
class _CounterLock:
    """Advisory flock around gate-counter read-modify-write (xdist-safe)."""

    def __enter__(self) -> "_CounterLock":
        import fcntl

        self._fh = open(LOCK_FILE, "a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        import fcntl

        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()


# endregion CONTEXT_COUNTER_LOCK


def _read_counter() -> int:
    """Read current attempt counter from .test_counter.json (under flock)."""
    with _CounterLock():
        if COUNTER_FILE.exists():
            try:
                data = json.loads(COUNTER_FILE.read_text())
                return data.get("failed_runs", 0)
            except (json.JSONDecodeError, KeyError):
                return 0
        return 0


def _write_counter(count: int) -> None:
    """Write attempt counter to .test_counter.json (under flock)."""
    with _CounterLock():
        COUNTER_FILE.write_text(json.dumps({"failed_runs": count}, indent=2) + "\n")
        logger.info("[IMP:7][anti-loop][counter] Set failed_runs=%d", count)


def _print_escalation(attempt: int) -> None:
    """Print escalation message based on attempt count."""
    print(f"\n{'=' * 60}")
    print(f"  ANTI-LOOP ESCALATION: Attempt #{attempt}")
    print(f"{'=' * 60}")

    if attempt <= 2:
        print("\n  CHECKLIST — common errors to verify:")
        for i, item in enumerate(CHECKLIST, 1):
            print(f"    {i}. {item}")
    elif attempt == 3:
        print("\n  >> Attempt 3: Use MCP tavily or Context 7 to find a solution online.")
    elif attempt == 4:
        print("\n  >> WARNING: Looping risk! Pause and reflect.")
        print("  >> Are you repeating a failed strategy? Consider alternatives (Superposition).")
    else:
        print("\n  >> CRITICAL ERROR: Agent looping detected. STOP.")
        print("  >> Formulate a help request for an operator.")
    print(f"{'=' * 60}\n")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Increment failed run counter at session start (master only — DevPlan 124 T4)."""
    if _is_xdist_worker():
        logger.info(
            "[IMP:7][anti-loop][start] Worker %s — counter increment skipped (master owns session)",
            os.environ.get("PYTEST_XDIST_WORKER"),
        )
        return
    count = _read_counter() + 1
    _write_counter(count)
    logger.info("[IMP:7][anti-loop][start] Session started, failed_runs=%d", count)

    if count > 1:
        _print_escalation(count)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Reset counter to 0 on 100% PASS, keep on failure (master only — DevPlan 124 T4)."""
    if _is_xdist_worker():
        logger.info(
            "[IMP:7][anti-loop][reset] Worker %s — counter reset skipped (master owns session)",
            os.environ.get("PYTEST_XDIST_WORKER"),
        )
        return
    if exitstatus == 0:
        _write_counter(0)
        logger.info("[IMP:7][anti-loop][reset] All tests passed — counter reset to 0")
    else:
        count = _read_counter()
        logger.warning("[IMP:7][anti-loop][fail] Tests failed — counter stays at %d", count)
