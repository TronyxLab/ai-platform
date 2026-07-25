# Brief 070 — Extract Shared Libraries

## $ARTIFACT_CONTRACT
- **PURPOSE:** Eliminate 3-way copy-paste of `_extract_context_from_node_yaml()` and 3-way duplicate Python heredoc blocks for project registration. Foundation for all 8 drift-unification waves.
- **DESCRIPTION:** Create `core/internal/shared/node_yaml.py` and `core/internal/shared/project_registry.py` as single-source-of-truth shared modules. All consumers import from shared.
- **RATIONALE:** 070 is the ROOT node of the dependency graph — DevPlans 078, 079, 080, 081 are BLOCKED without it.
- **ACCEPTANCE_CRITERIA:** All 12 ACs from DevPlan-expanded.md.
- **IMPLEMENTS:** DevPlan 070 (01-DevPlan.md + 02-DevPlan-expanded.md, authoritative: expanded).
- **IMPACTS:** state_machine.py, steps.py, context_deployer.py, add-project.sh, adopt-project.sh, remove-project.sh.
- **REQUIRES:** Nothing (this is the foundation).

## Current Status (Review 2026-07-25)
- **Verdict:** **READY FOR IMPLEMENTATION** — все 3 finding'а VerificationReport адресованы, блокеров нет.
- **Implementation:** 0% (не начата).
- **F1 (CRITICAL):** DevPlan 070 — ROOT node dependency graph. ✅ Восстановлен из git, DevPlans 078-081 разблокированы после имплементации 070.
- **F2 (CLI Interface):** ✅ Адресован — добавлен formal CLI Interface Specification раздел в 02-DevPlan-expanded.md: сигнатуры с типами, exit codes, error handling contract. Добавлена функция `list_projects()`.
- **F3 (Authoritative):** ✅ 02-DevPlan-expanded.md — канонический. 01-DevPlan.md — краткая форма для быстрой навигации.
