#!/usr/bin/env python3
# GREP_SUMMARY: run-context run-start timestamp lifecycle leaf hc-marker freshness state-machine cli docker
# STRUCTURE: ▶ set_run_start_ts(cli._run_phases) → ◇ get_run_start_ts(phases/docker reader) → ⎋ float | None
# region MODULE_CONTRACT
## @purpose  QA R2 (DevPlan 14 T2.B): leaf-хранилище run-start timestamp текущего режима
##           init/update. Разрывает цикл импортов phases/docker ↔ state_machine (import-linter
##           «internal domains are acyclic»): и оркестратор, и фаза-читатель импортируют ЭТОТ
##           модуль (0 зависимостей) вместо друг друга.
## @scope    core/internal/bootstrap/lifecycle/ — писатель: cli._run_phases; читатель:
##           lifecycle/phases/docker._registry_step_healthcheck; тесты: test_hc_marker_run_scope.
## @invariants
##   - Значение None = «run-start неизвестен» (standalone исполнение фазы) — читатель
##     применяет legacy-семантику подавления маркера
##   - Один прогон = одно значение; перезапись при новом прогоне — норма (retry)
## @rationale Q: почему не переменная в state_machine? A: state_machine уже импортирует
##   пакет phases (PHASE_DISPATCH) — обратный import из docker создаёт цикл, который ловит
##   import-linter; leaf-модуль — канонический разрыв (DevPlan 14 T2.B, wave2 gate-cycle).
# endregion MODULE_CONTRACT

from __future__ import annotations

_run_start_ts: float | None = None


def set_run_start_ts(ts: float) -> None:
    """Record mode start time (called once by cli._run_phases at run entry)."""
    global _run_start_ts  # ruff: ignore[PLW0603]
    _run_start_ts = ts


def reset_run_start_ts() -> None:
    """Reset to unknown (tests / standalone phase execution)."""
    global _run_start_ts  # ruff: ignore[PLW0603]
    _run_start_ts = None


def get_run_start_ts() -> float | None:
    """Return the recorded mode start time, or None if not set (legacy semantics)."""
    return _run_start_ts
