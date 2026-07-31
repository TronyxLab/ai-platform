# §PRINCIPLES
**Design Principles**

    1. **Semantic Markup** — Code is read by agents, not humans. Use GREP_SUMMARY, STRUCTURE, LDD logs.
    2. **Zero-Context Survival** — Every file self-documents; the next agent sees only the file, not the chat.
    3. **Log-Driven Development** — Every function logs at [IMP:1-10] levels. IMP:9-10 = business logic assertions.
    4. **Superposition** — For ambiguous decisions, formulate 3-5 options before committing to one.
    5. **Fail-Fast** — Validate before producing. Never write semantically invalid output.
     6. **Small Simple Blocks** — Prefer linear code with moderate repetition over over-engineered abstractions. However: when an existing function can be extended with a simple, backward-compatible change (≤2 new optional parameters, no breaking contract changes), prefer extension over duplication. Duplicate business logic is technical debt — justify it explicitly with `## @rationale`.
           7. **Artifact Contract Model** — Every management artifact (Brief.md, DevPlan.md, VerificationReport.md, StatusReport.md) declares `$ARTIFACT_CONTRACT` with PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES. See doc-protocols SKILL.md for full reference.
     8. **AI-First Architecture** — Module boundaries follow the business domain, not
        technology: one module = one business responsibility; when a file gains a second
        responsibility, split it instead of growing it. Dependencies point inward
        (Domain ← Application ← Infrastructure) — domain logic never imports
        infrastructure. Modules interact only through explicit typed public contracts,
        never through each other's internals. Details: skill `arch-patterns`.
     9. **Read before Act** — Before planning, implementing, or deploying, read existing
        knowledge artifacts in affected modules: TRAP annotations, DEBT registries
        (.ai/plans/*/*-Debt.md), VerificationReports from prior waves. Knowledge
        recorded but not read is wasted.

<!-- ai-instructions:0.6.1 -->
