# GREP_SUMMARY: ai-instructions, framework, convention compiler, runtime, version
# STRUCTURE: ┌ai_instructions package┐ → ⚡ __version__ = 0.7.0 → ⎋ runtime modules
# region MODULE_CONTRACT
## @purpose  Top-level package for AI Instructions — a convention compiler that walks a
##   canon tree plus a consumer `.ai/` tree, resolves overrides, and emits `.kilo/` outputs
## @scope    Package metadata only; all logic lives in ai_instructions.runtime.*
## @invariants
##   - __version__ MUST match the VERSION file consumed by setuptools dynamic version
##   - No imports of bundlekit-era machinery from this package
## @rationale The compiler replaces the old bundlekit XML compiler; a single version
##   constant keeps packaging and runtime metadata in sync
# endregion MODULE_CONTRACT

"""AI Instructions Framework — convention compiler (walk → resolve → emit → lock)."""

__version__ = "0.7.0"
