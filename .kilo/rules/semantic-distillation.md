# region MODULE_CONTRACT
## @purpose  Semantic Distillation from Plans to Code — extract business goals, constraints, architectural decisions from .md plans into Doxygen contracts (@purpose, @invariants, @rationale, @usecases)
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Business context from ephemeral .md plans will be lost — code contracts survive context loss, markdown does not. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Code = single source of truth — .md plans are ephemeral, Doxygen contracts in code survive context loss -->

    **Semantic Distillation from Plans to Code**

    Markdown plans (DevPlan.md) are Chain of Thought (CoT) artifacts. You MUST extract business requirements from DevPlan.md and transfer them directly into the code. Do NOT extract from Brief.md or business_requirements.md — those are Architect planning artifacts, not implementation specs.

    **Extraction targets:**
    - Business goals → ## @purpose (module and function level)
    - Constraints and edge cases → ## @invariants
    - Architectural decisions → ## @rationale (Q: why? A: because...)
    - Acceptance criteria → ## @usecases and test assertions

    **Why:** Markdown plans are ephemeral CoT artifacts — they may not be preserved. Code with built-in Doxygen contracts survives context loss. The next agent opening the file sees the full business context without needing to find the original plan.

    **Process:**
    1. Read DevPlan.md fully
    2. For each entity in Draft Code Graph → create corresponding module/function with distilled contracts
    3. For each acceptance criterion → create corresponding test with @purpose referencing the criterion
    4. For each data flow step → create corresponding LDD log checkpoint at IMP:8-9

<!-- ai-instructions:0.7.0 -->
