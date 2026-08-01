# GREP_SUMMARY: lifecycle-package, state-machine, bootstrap, phases, python-decomposition
# STRUCTURE: ▶ BootstrapPhase enum (14) → ◇ precondition_check → ⊕ _phase_dependency_graph → ⚡ phases.py (14 functions) → ⎋ state.json
# region MODULE_CONTRACT
## @purpose  Python decomposition package for node-lifecycle.sh — consolidated 14-phase state machine.
##           Replaces shell state-machine logic with typed Python modules: state machine and phase
##           implementations.
## @scope    core/internal/bootstrap/lifecycle/ — state_machine.py (14 phases + dependency graph),
##           phases.py (phase implementations), secrets_manager.py
## @invariants
##   - state_machine.py — единственный entrypoint для lifecycle state machine
##   - phases.py — извлечённая бизнес-логика фаз, каждая функция вызывается из state_machine
##   - Все фазы имеют pre/post-condition проверки через _phase_dependency_graph
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph.
##             phases.py follows Single Responsibility Principle — state machine in state_machine.py,
##             business logic in phases.py. Legacy step implementations removed (U-27).
# endregion MODULE_CONTRACT

"""
Modules:
  - state_machine.py   — State machine: BootstrapPhase enum, _phase_dependency_graph, 14 phases
  - phases.py          — Phase implementation functions (14 functions: φ1-φ13 + φ8.5)
  - secrets_manager.py — Secrets validation helpers for bootstrap phases
"""
