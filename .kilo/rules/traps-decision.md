<!-- GREP_SUMMARY: decision_trap, TRAP, DECISION, rejected, alternative, rationale, debate, adr -->
# region MODULE_CONTRACT
## @purpose  Decision Trap (TRAP[DECISION]) — captures rejected alternatives and their rationale to prevent debate loops
## @scope    architect,coder,sysadmin
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Decision trap prevents repeated debate on rejected alternatives by documenting rejected options and rationale. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the change site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Decision Trap — TRAP[DECISION]**

    When a non-obvious design decision is made and a plausible alternative was rejected, add a TRAP[DECISION] comment at the decision point. Format (one-line):

    ```
    # 🧐 TRAP[DECISION] · YYYY-MM-DD · — · One-liner · Rejected: ... · Reason: ... · Rev: ...
    ```

    **Deferred workaround example:**
    ```
    # 🧐 TRAP[DECISION] · 2026-06-09 · — · DNS workaround: /etc/hosts · Rejected: fixed IP in docker-compose · Reason: deferred, out of scope · Rev: container restart invalidates hosts
    ```

    **Add when:** a plausible alternative was explicitly considered and rejected, or a temporary
    workaround was applied with a known deferred proper fix (`Reason: deferred`).
    **Do NOT add for:** obvious decisions where the rejected alternative has no merit, personal
    preferences without technical rationale, decisions already covered by ADR/design doc, trivial
    choices between equivalent options, unknown proper fix (needs investigation first).

<!-- ai-instructions:0.7.0 -->
