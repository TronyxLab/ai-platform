# GREP_SUMMARY: llm, package, policy_schema, LLMPolicy
# STRUCTURE: ┌package init┐ → ◇ __version__ → ◇ re-export LLMPolicy → ⎋ public API
# region MODULE_CONTRACT
## @purpose  Package init for core/internal/llm/ — LLM Gateway Python module.
##           Re-exports LLMPolicy from policy_schema as the primary public API.
## @scope    All LLM-related platform code: policy schema, config renderer, key provisioner, admin client
## @invariants
##   - __version__ follows semver — current 1.0.0
##   - Only LLMPolicy is re-exported at package level
##   - Submodules (policy_schema, config_renderer, key_provisioner, admin_client) are
##     imported explicitly by consumers, not auto-loaded here
## @rationale Clean public API surface — consumers import LLMPolicy from the package,
##            not from the specific submodule. Other submodules are imported explicitly
##            when needed (lazy import pattern).
## @changes — 2026-07-24 | Created (DevPlan 049 Phase 1)
# endregion MODULE_CONTRACT

__version__ = "1.0.0"

from core.internal.llm.policy_schema import LLMPolicy

__all__ = ["LLMPolicy"]
