# GREP_SUMMARY: runtime, compiler pipeline, config, canon-source, walker, resolver, emitter, lock, packer, watcher, cli
# STRUCTURE: ┌runtime┐ → ○ config → ○ canon_source → ○ walker → ○ resolver → ○ emitter → ○ lock → ○ packer → ○ watcher → ⎋ cli
# region MODULE_CONTRACT
## @purpose  Runtime subpackage of the ai-instructions convention compiler
## @scope    All compiler stages: pins config, canon resolution, tree walking, override
##   resolution, emission to .kilo/, lock writing, packing, watching, CLI
## @invariants
##   - Modules depend only on stdlib + pyyaml
##   - All modules log through logging.getLogger(__name__) under the ai_instructions tree
## @rationale Stage-per-module keeps each pipeline step independently testable
# endregion MODULE_CONTRACT

"""Runtime package for the ai-instructions convention compiler."""
