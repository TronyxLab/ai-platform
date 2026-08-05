# GREP_SUMMARY: counter, .test_counter.json, anti-loop, attempt tracking, conftest, flock, xdist-safe, reset, scope, full-session
# STRUCTURE: _CounterLock(flock) → _read_counter → json.load(.test_counter.json) → _increment_counter (atomic RMW) → _write_counter → json.dump → _reset_counter (100% full-session PASS)
# region MODULE_CONTRACT
## @purpose  ЕДИНЫЙ counter-модуль для .test_counter.json — Anti-Loop protocol (attempt tracking)
##           и scope-тег полной сессии (T12.1, T-1/T-2 DevPlan 136 §12.4). Унифицирует ДВА
##           counter-модуля (tests/_conftest/counter.py + собственный в tests/gates/conftest.py):
##           один путь файла (tests/.test_counter.json), один ключ ("attempts"), один flock.
## @scope    Чтение/запись attempt-counter + scope-тег; НЕ содержит orchestration/escalation
##           (escalation — _conftest/checklist.py, session-хуки — _conftest/session.py).
## @invariants
##   - _read_counter всегда возвращает dict с ключом "attempts" (int >= 0)
##   - _write_counter перезаписывает файл атомарно (json.dump)
##   - Missing/malformed counter file → {"attempts": 0} (безопасный дефолт)
##   - ВСЕ операции под файловой блокировкой flock (fcntl) — защита от ПАРАЛЛЕЛЬНЫХ
##     pytest-сессий (2 агента одновременно, DevPlan 120 §3.3): конкурентные RMW без lock
##     = потерянные обновления
##   - _increment_counter — атомарный read-modify-write (lock → read → inc → write → unlock)
##   - _reset_counter — атомарный сброс attempts→0 (+ опциональный scope-тег); вызывается
##     ТОЛЬКО при 100% PASS ПОЛНОЙ сессии (T12.1: не поднабора — гейт _is_full_session в session.py)
##   - scope-тег ("last_scope") — сигнатура последней сессии (marker-expr + item count);
##     пишется при инкременте, читается для диагностики subset-pass без сброса
##   - Master-семантика (DevPlan 124 T1/T4): session-уровневые вызовы инкремента/сброса
##     выполняет ТОЛЬКО master-воркер (гейт PYTEST_XDIST_WORKER у вызовов в _conftest/session.py).
##     flock здесь НЕ про xdist-воркеры (их гейтит caller), а про параллельные независимые
##     pytest-процессы.
##   - tests/gates/conftest.py — ТОНКИЙ РЕ-ЭКСПОРТ этого модуля (T12.1): НЕ содержит собственных
##     session-хуков/counter-файла — root-conftest session-хуки (session.py) покрывают gates-прогоны.
## @rationale DevPlan 136 W12 T12.1 (T-1/T-2): dual counter (tests/.test_counter.json + tests/gates/.test_counter.json,
##            разные ключи "attempts"/"failed_runs") = расщеплённое anti-loop состояние и ложные сбросы
##            поднабором. Один модуль + один файл + reset только по полной сессии.
## @changes 2026-08-02 | DevPlan 120 Wave 1: flock-блокировка (fcntl) на .test_counter.json +
##            атомарный _increment_counter — устранение гонок при xdist (3106 static_audit тестов)
## @changes 2026-08-03 | DevPlan 124 T1/T4: уточнена master-семантика — инкремент/сброс вызывает
##            ТОЛЬКО master (PYTEST_XDIST_WORKER гейт у вызовов); flock остаётся защитой
##            параллельных независимых сессий (честный Attempt #N при -n auto)
## @changes 2026-08-05 | DevPlan 136 W12 T12.1: _reset_counter + scope-тег; gates/conftest →
##            ре-экспорт (dual counter удалён)
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
    """Атомарный read-modify-write: попытка = прочитанное значение + 1 (xdist-safe).

    ## @purpose  Единая критическая секция increment: конкурентные независимые pytest-сессии
    ##            (2 агента) не теряют обновления. Xdist-воркеры НЕ доходят до этой функции —
    ##            master-guard в _conftest/session.py (DevPlan 124 T1): воркеры — no-op;
    ##            иначе -n auto давал Attempt #N за один прогон.
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


def _record_scope(scope: str) -> None:
    """Сохранить scope-тег текущей сессии в counter-файл (под flock).

    ## @purpose  T12.1 (T-2): scope-тег (marker-expr + item count) пишется при sessionstart,
    ##            чтобы sessionfinish мог отличить «полную сессию» от «поднабора» и НЕ сбрасывать
    ##            attempts при 100% PASS поднабора (ложный сброс anti-loop evidence).
    ## @io       ⇥ scope: str — сигнатура сессии → ⎋ None
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
        counter["last_scope"] = scope
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter, f)
            f.write("\n")


def _read_scope() -> str | None:
    """Прочитать scope-тег последней сессии (для диагностики, под flock).

    ## @io → ⎋ str | None — "last_scope" или None если не записан
    ## @complexity O(1)
    """
    counter = _read_counter()
    scope = counter.get("last_scope")
    return scope if isinstance(scope, str) else None


def _reset_counter(scope: str | None = None) -> None:
    """Атомарный сброс attempts → 0 (опционально с новым scope-тегом).

    ## @purpose  T12.1 (T-2): reset вызывается ТОЛЬКО при 100% PASS ПОЛНОЙ сессии
    ##            (гейт _is_full_session в _conftest/session.py) — поднабор (gates/static_audit/
    ##            отдельный файл) НЕ сбрасывает attempts, иначе проходящий поднабор стирает
    ##            evidence фейла полного прогона.
    ## @io       ⇥ scope: str | None → ⎋ None
    ## @complexity O(1)
    """
    with _CounterLock():
        counter: dict = {"attempts": 0}
        if scope is not None:
            counter["last_scope"] = scope
        with open(_COUNTER_FILE, "w") as f:
            json.dump(counter, f)
            f.write("\n")


# endregion COUNTER_IO
