<!-- GREP_SUMMARY: perf_trap, TRAP, PERF, performance, latency, throughput, N+1, bottleneck, mitigation -->
# region MODULE_CONTRACT
## @purpose  Performance Trap (TRAP[PERF]) — captures performance analysis results and mitigation strategies inline
## @scope    sysadmin,coder
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Perf trap prevents performance regressions by documenting N+1 queries, hot spots, and mitigation strategies. -->
<!-- doc-only, not driver: @requires MARKUP section from constitution.xml -->
<!-- §PRINCIPLE: Knowledge locality — Trap is located next to the change site, not in a separate document. grep "TRAP\[" finds all active TRAPs -->

    **Performance Trap — TRAP[PERF]**

    After analyzing load test results or production performance data, add a TRAP[PERF] comment at the bottleneck location. Format (one-line):

    ```
    # ⚡ TRAP[PERF] · YYYY-MM-DD · >N rps · One-liner · Root: ... · Mit: ...
    ```

    **Add when:** load test or production data reveals a confirmed bottleneck with a mitigation
    (N+1 query, CPU hot spot, memory leak), or a performance-driven architecture decision.
    **Do NOT add for:** speculative concerns without data, micro-optimizations (<1% impact), issues
    fixed by scaling infrastructure only, routine query optimization.

<!-- ai-instructions:0.7.0 -->
