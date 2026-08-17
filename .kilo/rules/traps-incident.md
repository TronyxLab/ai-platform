# region MODULE_CONTRACT
## @purpose  Incident Trap (TRAP[INCIDENT]) — captures production incident rationale with Symptom/Root/Fix/Prevention format to prevent repeated firefighting
## @scope    sysadmin, qa
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Incident trap prevents agent swarm from repeating the same P0/P1 incident investigation. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the change site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Incident Trap — TRAP[INCIDENT]**

    When investigating a production incident (P0/P1), add a TRAP[INCIDENT] comment at the root cause location. Format:

    ```
    # 🔴 TRAP[INCIDENT] · YYYY-MM-DD · P0 · One-liner · Root: ... · Fix: ...
    # · Symptom: What was observed (error, wrong behavior, degraded metrics)
    # · Root: Root cause analysis
    # · Fix: How it was fixed (hotfix, config change, rollback)
    # · Prevention: How to prevent recurrence (monitoring, tests, architecture change)
    ```

    **Add when:** P0/P1 incident with high business impact and non-obvious root cause (concurrency,
    state corruption, complex dependency chain), or caused by a monitoring/alerting gap.
    **Do NOT add for:** minor incidents with obvious root cause, routine bug fixes, non-production
    issues, incidents already documented in an external system.

<!-- ai-instructions:0.7.0 -->
