# GREP_SUMMARY: lifecycle-package, state-machine, bootstrap, phases, state-migration, python-decomposition
# STRUCTURE: ▶ BootstrapPhase enum (14) → ◇ precondition_check → ⊕ _phase_dependency_graph → ⚡ phases.py (14 functions) → ⎋ state_migration.py (23→14)
# region MODULE_CONTRACT
## @purpose  Python decomposition package for node-lifecycle.sh — consolidated 14-phase state machine.
##           Replaces shell state-machine logic with typed Python modules: state machine, phase
##           implementations, and state.json migration for production node upgrades.
## @scope    core/internal/bootstrap/lifecycle/ — state_machine.py (14 phases + dependency graph),
##           phases.py (phase implementations), steps.py (legacy step implementations),
##           state_migration.py (one-shot 23→14 key migration)
## @invariants
##   - state_machine.py — единственный entrypoint для lifecycle state machine
##   - phases.py — извлечённая бизнес-логика фаз, каждая функция вызывается из state_machine
##   - steps.py — legacy step implementations, постепенно вытесняется phases.py
##   - Все фазы имеют pre/post-condition проверки через _phase_dependency_graph
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph.
##             phases.py follows Single Responsibility Principle — state machine in state_machine.py,
##             business logic in phases.py.
# endregion MODULE_CONTRACT

"""
Modules:
  - state_machine.py   — State machine: BootstrapPhase enum, _phase_dependency_graph, 14 phases
  - phases.py          — Phase implementation functions (14 functions: φ1-φ13 + φ8.5)
  - steps.py           — Legacy step implementations (being phased out)
  - state_migration.py — One-shot state.json migration: 23 old keys → 14 new phase keys
"""
