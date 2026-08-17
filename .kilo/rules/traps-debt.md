# region MODULE_CONTRACT
## @purpose  Debt Trap (TRAP[DEBT]) — preserves observations of latent codebase problems that require separate investigation, preventing context loss across sessions
## @scope    *
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Debt trap preserves observations of latent problems that need investigation but are out of scope for the current session. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the suspected problem site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Debt Trap — TRAP[DEBT]**

    When you discover a latent problem in the codebase that is out of scope for the current task and requires separate investigation, add a TRAP[DEBT] comment at the problem location. Format:

    ```
    # 📝 TRAP[DEBT] · YYYY-MM-DD · SEVERITY · One-liner
    # · Observed: what the agent noticed (symptom)
    # · Suspected: hypothesis about the cause (or "needs investigation")
    # · Impact: potential consequences if not fixed
    # · When: discovery context (during feature X implementation)
    ```

    SEVERITY: `HI` (data loss/security), `MED` (race condition/perf), `LO` (code smell).

    **Add when:** the problem is NOT caused by the current task and requires separate investigation.
    Confidence >90% → auto-create with concrete Suspected; 50-90% → auto-create with
    `Suspected: hypothesis, needs verification`.

    **Do NOT add for:** fixed problems (use TRAP[BUG]), known-fix-deferred (use TRAP[DECISION]
    `Reason: deferred`), incidents (TRAP[INCIDENT]), obvious issues (regular TODO), trivial
    observations, confidence <50% (ask the user first).

    **Lifecycle:** creation → QA verification → future investigation → TRAP[BUG] (confirmed + fixed)
    / update Observed+Suspected (confirmed, fix unknown) / TRAP[ARCHIVED] (false positive or
    prevented architecturally).

<!-- ai-instructions:0.7.0 -->
