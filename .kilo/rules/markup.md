# region MODULE_CONTRACT
## @purpose  Core constitution section — MARKUP directives for all agents
## @scope    architect, coder, qa, sysadmin, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Core constitution — changing these directives breaks all agent behavior. -->

    **Semantic Markup Standard — Overview**

    Apply the full standard from RULES.md §MARKUP. Core requirements:
    - MODULE_CONTRACT region with ## @purpose, @scope, @invariants, @rationale
    - GREP_SUMMARY and STRUCTURE on every file
    - # region/#endregion paired markers for functions and classes
    - LDD logs with [IMP:1-10][FUNC][BLOCK] format in every non-trivial function
    - # ⚠️ TRAP[BUG] comments on non-trivial bug fixes
    - Language adaptation: ## @tags for Python, /** @tags */ for TypeScript, -- @tags for SQL
    - `$ARTIFACT_CONTRACT` with 7 mandatory fields on all management artifacts

<!-- ai-instructions:0.7.1 -->
