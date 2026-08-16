# GREP_SUMMARY: lifecycle-package, state-machine, bootstrap, phases, python-decomposition, cli, state-store, helpers
# STRUCTURE: ▶ BootstrapPhase enum (14) → ◇ precondition_check → ⊕ _phase_dependency_graph → ⚡ phases.py (14 functions → helpers/*) → ⚡ cli.py (CLI/main) → ⎋ state.json (state_store.py)
# region MODULE_CONTRACT
## @purpose  Python decomposition package for node-lifecycle.sh — consolidated 14-phase state machine.
##           Replaces shell state-machine logic with typed Python modules: state machine and phase
##           implementations. B9 T1/T2 (U-08): SRP-декомпозиция — I/O в helpers/, persistence в
##           state_store.py, CLI в cli.py; state_machine.py — чистая оркестрация (≤1200 LOC гейт T6.2).
## @scope    core/internal/bootstrap/lifecycle/ — state_machine.py (14 phases + dependency graph,
##           оркестрация), state_store.py (StepState/BootstrapState + state.json I/O), cli.py
##           (build_parser/main/run_init_mode/run_update_mode), phases.py (14 phase implementations),
##           helpers/ (7 I/O-модулей: subprocess_io/system/users/secrets/validation/domains/reporting),
##           secrets_manager.py
## @invariants
##   - state_machine.py — оркестрация; persistence в state_store.py; I/O в helpers/; CLI в cli.py
##   - state_machine → phases → helpers — односторонняя зависимость (цикл phases↔state_machine устранён)
##   - phases.py — извлечённая бизнес-логика фаз, каждая функция вызывается из state_machine
##   - Все фазы имеют pre/post-condition проверки через _phase_dependency_graph
##   - BootstrapState/StepState re-экспортируются из state_machine (публичный контракт пакета)
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph.
##             phases.py follows Single Responsibility Principle — state machine in state_machine.py,
##             business logic in phases.py. DevPlan 116 B9 (U-08): SRP-декомпозиция монолита.
## @changes  2026-08-01 · B9 T1/T2 — helpers/, state_store.py, cli.py (2284 → ~950 LOC state_machine)
# endregion MODULE_CONTRACT

"""
Modules:
  - state_machine.py   — State machine: BootstrapPhase enum, _phase_dependency_graph, оркестрация
  - state_store.py     — StepState/BootstrapState + state.json I/O (load_state/save_state)
  - cli.py             — CLI: build_parser/main/run_init_mode/run_update_mode
  - phases.py          — Phase implementation functions (14 функций: φ1-φ13 + φ8.5)
  - helpers/           — I/O-хелперы (subprocess_io, system, users, secrets, validation, domains, reporting)
  - secrets_manager.py — Secrets validation helpers for bootstrap phases
"""
