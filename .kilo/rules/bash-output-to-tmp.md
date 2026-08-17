# region MODULE_CONTRACT
## @purpose  Long-Running Command Output — redirect stdout/stderr to temp file so agent can grep results on timeout instead of re-running
## @scope    coder, qa, sysadmin
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Without temp file output, timeout on long-running commands forces full re-run — wastes minutes of agent time. -->

    **Long-Running Command Output**

    For any bash command expected to run >30 seconds (test suites, builds,
    doxygen, data processing), redirect stdout/stderr to a timestamped
    temp file:

    ```
    OUTPUT="/tmp/cmd_$(date +%s)_$$.log" && <command> > "$OUTPUT" 2>&1; echo "OUTPUT_FILE=$OUTPUT"
    ```

    If the command times out — grep/read the temp file for results instead
    of re-running. The `OUTPUT_FILE=` line tells you the exact path.

<!-- ai-instructions:0.7.0 -->
