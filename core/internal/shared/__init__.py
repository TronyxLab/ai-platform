#!/usr/bin/env python3
# GREP_SUMMARY: shared, node-yaml, project-registry, extract-context, single-source-of-truth
# STRUCTURE: ┌core/internal/shared/┐ → ◇ node_yaml.py (context extraction) → ◇ project_registry.py (register/deregister/list)
# region MODULE_CONTRACT
## @purpose  Shared library modules for ai-platform internal/bootstrap consumers.
##           Single-source-of-truth utilities extracted from duplicate copies
##           across bootstrap/lifecycle/, bootstrap/deploy/, and scaffold/.
## @scope    node_yaml.py (YAML context extraction), project_registry.py (scaffold registration)
## @invariants
##   1. No circular imports between shared modules
##   2. All modules are self-contained (no intra-shared dependencies)
##   3. Each module is importable standalone via sys.path.insert pattern
## @rationale DRIFT-B5 elimination (Brief 077): 3-way copy-paste reduced to single canonical source
## @changes  2026-07-25 · DevPlan 070 — Created shared package
# endregion MODULE_CONTRACT
