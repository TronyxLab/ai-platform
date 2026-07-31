# GREP_SUMMARY: scaffold package-init exports project_lister context_initializer project_remover scaffold_helpers project_scaffolder
# STRUCTURE: ┌package-init┐ → ◇ sibling modules re-exports
# region MODULE_CONTRACT
## @purpose  Package init for core.internal.scaffold — project scaffolding operations
##            (DP-092 Wave 1-4 Strangler-Fig Python migration).
## @scope    Package-level exports: project_lister, context_initializer, project_remover,
##           scaffold_helpers, project_scaffolder, context_registry, gen_env_platform,
##           vhost_renderer, project_adopter.
## @invariants  All business logic lives in sibling .py modules.
##              Shell files are now thin facades delegating to Python modules.
# endregion MODULE_CONTRACT
