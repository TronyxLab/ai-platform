# GREP_SUMMARY: scaffold, package-init
# STRUCTURE: ┌package-init┐ → ◇ sibling modules
# region MODULE_CONTRACT
## @purpose  Package init for core.internal.scaffold — project scaffolding operations
## @scope    Package-level exports and documentation
## @invariants  This is a thin init; all business logic lives in sibling .py modules
# endregion MODULE_CONTRACT
# MODULE_CONTRACT — scaffold is a Python package for project scaffolding operations.
# This file makes core.internal.scaffold importable as a package.
# All business logic lives in sibling .py modules (gen_env_platform, vhost_renderer, etc.).
