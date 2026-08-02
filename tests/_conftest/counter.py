# GREP_SUMMARY: counter, .test_counter.json, anti-loop, attempt tracking, conftest, flock, xdist-safe
# STRUCTURE: _CounterLock(flock) → _read_counter → json.load(.test_counter.json) → _increment_counter (atomic RMW) → _write_counter → json.dump

# region MODULE_CONTRACT
## @purpose  Counter read/write functions for .test_counter.json — used by Anti-Loop protocol to track failed test attempts across sessions
## @scope    Reading and writing the attempt counter file; no orchestration or escalation logic
## @invariants
##   - _read_counter always returns a dict with key "attempts" (int >= 0)
##   - _write_counter overwrites the file atomically via json.dump
##   - Missing or malformed counter file silently defaults to {"attempts": 0}
##   - ВСЕ операции под файловой блокировкой flock (fcntl) — xdist-безопасность (DevPlan 120 §3.3):
##     при -n auto session-хуки выполняются в каждом worker'е → конкурентные RMW без lock = гонка
##   - _increment_counter — атомарный read-modify-write (lock → read → inc → write → unlock)
## @rationale Extracted from tests/conftest.py COUNTER_IO region to isolate counter persistence logic;
##            path adjusted from __file__ (conftest/) to (conftest/..) so .test_counter.json remains in tests/
## @changes 2026-08-02 | DevPlan 120 Wave 1: flock-блокировка (fcntl) на .test_counter.json +
##            атомарный _increment_counter — устранение гонок при xdist (3106 static_audit тестов)
# endregion MODULE_CONTRACT

import json
import os

# region COUNTER_IO

_COUNTER_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".test_counter.json")
_LOCK_FILE = _COUNTER_FILE + ".lock"


# region CONTEXT_COUNTER_LOCK
## @purpose  Файловая блокировка flock (fcntl) на .lock-файл счётчика. fcntl доступен на
##           POSIX (darwin/linux — обе платформы проекта); контекст-менеджер гарантирует unlock.
## @io       → with _CounterLock(): критическая секция RMW
## @complexity O(1)
class _CounterLock:
    """Advisory file lock (flock) around counter read-modify-write (xdist-safe)."""

    def __enter__(self) -> "_CounterLock":
        import fcntl

        self._fh = open(_LOCK_FILE, "a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        import fcntl

        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()


# endregion CONTEXT_COUNTER_LOCK


def _read_counter() -> dict:
    """Read attempt counter from .test_counter.json. Returns {'attempts': int}."""
    with _CounterLock():
        if not os.path.isfile(_COUNTER_FILE):
            return {"attempts": 0}
        try:
            with open(_COUNTER_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"attempts": 0}


def _write_counter(data: dict) -> None:
    """Write attempt counter to .test_counter.json (under flock)."""
    with _CounterLock(), open(_COUNTER_FILE, "w") as f:
        json.dump(data, f)
        f.write("\n")


def _increment_counter() -> int:
    """Атомарный read-modify-write: попытка = прочитанное значение + 1 (xdist-безопасно).

    ## @purpose  Единая критическая секция increment: при xdist каждый worker вызывает
    ##            sessionstart конкурентно — раздельные read/write давали бы потерянные
    ##            обновления (N воркеров записали бы одно и то же значение).
    ## @io       → ⎋ int: новое значение attempts
    ## @complexity O(1)
    """
    with _CounterLock():
        counter = {"attempts": 0}
        if os.path.isfile(_COUNTER_FILE):
            try:
                with open(_COUNTER_FILE) as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("attempts"), int):
                    counter = loaded
            except (json.JSONDecodeError, OSError):
                counter = {"attempts": 0}
        counter["attempts"] = counter.get("attempts", 0) + 1
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter, f)
            f.write("\n")
        return counter["attempts"]


# endregion COUNTER_IO
