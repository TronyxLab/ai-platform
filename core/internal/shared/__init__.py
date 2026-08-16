#!/usr/bin/env python3
# GREP_SUMMARY: shared, node-yaml, project-registry, audit-logger, extract-context, single-source-of-truth
# STRUCTURE: ┌core/internal/shared/┐ → ◇ node_yaml/ (пакет: контекст/домены/проекты — DevPlan 119 H1) → ◇ project_registry.py (register/deregister/list) → ◇ audit_logger.py (JSON-lines audit)
# region MODULE_CONTRACT
## @purpose  Shared library modules for ai-platform internal/bootstrap consumers.
##           Single-source-of-truth utilities extracted from duplicate copies
##           across bootstrap/lifecycle/, bootstrap/deploy/, and scaffold/.
## @scope    node_yaml/ (пакет NodeYaml-фасада: domains/projects/modules/node/validation/resolve —
##           DevPlan 119 H1, монолит node_yaml.py декомпозирован), project_registry.py (scaffold
##           registration), audit_logger.py (JSON-lines audit trail)
## @invariants
##   1. No circular imports between shared modules
##   2. All modules are self-contained (no intra-shared dependencies)
##   3. Each module is importable standalone via sys.path.insert pattern
## @rationale DRIFT-B5 elimination (Brief 077): 3-way copy-paste reduced to single canonical source.
##            DevPlan 081B5: audit_logger.py added for JSON-lines audit trail.
## @changes  2026-07-25 · DevPlan 070 — Created shared package
##           2026-07-26 · DevPlan 081B5 — Added audit_logger module
# endregion MODULE_CONTRACT
