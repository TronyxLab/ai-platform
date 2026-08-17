# region MODULE_CONTRACT
## @purpose  Bug Trap (TRAP[BUG]) — captures fix rationale with Symptom/Root/Fix/Prevention format to prevent agent swarm from repeating the same bug
## @scope    architect, coder, qa
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Bug trap prevents agent swarm from repeating the same non-trivial bug fix. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the change site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Bug Trap — TRAP[BUG]**

    When fixing a non-trivial bug, add a TRAP[BUG] comment at the fix location. Format:

    ```
    # ⚠️ TRAP[BUG] · YYYY-MM-DD · P1 · One-liner · Root: ... · Fix: ...
    # · Symptom: What was observed (error, wrong behavior)
    # · Root: Root cause analysis
    # · Fix: How it was fixed
    # · Prevention: How to prevent recurrence
    ```

    **Add when:** the fix changes a non-trivial algorithm/data flow, the old approach was intuitive
    but incorrect, or the bug was intermittent/environment-specific.
    **Do NOT add for:** typos, formatting, simple syntax errors, trivial one-line changes.

<!-- ai-instructions:0.7.0 -->
