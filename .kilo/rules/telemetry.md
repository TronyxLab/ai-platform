# §TELEMETRY
**Token Tracking & Usage Logging**

    Token tracking is enabled via `ai-instructions.yaml` (`telemetry.token_tracking: true`). All agent sessions MUST log token usage telemetry on completion using the format below. The rule is only compiled when `token_tracking: true`.

    **Logging rule:**

    At session end, output a multi-line summary with `[IMP:9][TOKEN]` prefix on every line:

    ```
    [IMP:9][TOKEN] === Token Usage Summary ===
    [IMP:9][TOKEN]   Self:        N tokens (cache hits: N)
    [IMP:9][TOKEN]   Subagents:
    [IMP:9][TOKEN]     - name (role):    N tokens
    [IMP:9][TOKEN]     - name (role):    N tokens
    [IMP:9][TOKEN]   ─────────────────────────────
    [IMP:9][TOKEN]   Total:             N tokens
    ```

    - **Self:** prompt_tokens + completion_tokens текущей сессии. Cache hits = prompt_tokens_details.cached_tokens из API-ответа. Если кэш недоступен — `(cache hits: N/A)`.
    - **Subagents:** по одной строке на каждого дочернего task-агента. Формат: `- display_name (role): N tokens`. Display name — имя агента из frontmatter (Plan, Code, Verifier, etc.), role — исходное имя роли (architect, coder, verifier). Токены субагента берутся из его `[IMP:9][TOKEN]` лога (Self). Если субагентов не было — блок Subagents опускается.
    - **Total:** сумма Self + всех токенов субагентов.
    - **Best-effort:** если токены субагента недоступны — строка не выводится. Если нет данных о кэше — `N/A`.
    - При `token_tracking: false` в kilo.json — не логировать.

<!-- ai-instructions:0.5.3 -->
