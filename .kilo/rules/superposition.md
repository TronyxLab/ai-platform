# region MODULE_CONTRACT
## @purpose  SUPERPOSITION, STATE_MANAGEMENT
## @scope    architect, sysadmin
## @invariants
##   - @protected  true
##   - Контент — 1:1 перенос framework-source 0.6.3 (только XML-обёртка → markdown)
# endregion MODULE_CONTRACT

<!-- @protect: Shared superposition protocol — used by architect (design decisions) and sysadmin (infrastructure mutations). -->

    **Superposition Protocol — 5 Modes**

    Before any irreversible decision or mutation, generate multiple solution hypotheses BEFORE committing.

    **Mode 1: FULL Superposition (3-5 options)**
    For high-ambiguity decisions. Format:
    ```
    ## SUPERPOSITION: {problem_statement}
    ### Option A: {name} [score: X/10]
    Approach: {one-line description}
    Trade-offs: {cost vs benefit}
    Best when: {conditions}
    ...
    ### Recommendation: Option {X} — {one-line justification}
    **Collapse signal:** Reply with A/B/C/D/E or describe your constraint.
    ```

    **Mode 2: BINARY Trade-off (exactly 2 options)**
    For clear either-or decisions. Format:
    ```
    ## TRADE-OFF: {decision_statement}
    | Criterion | Option A: {name} | Option B: {name} |
    |-----------|-----------------|-----------------|
    ...
    **Recommendation:** Option {X} because {reason}.
    ```

    **Mode 3: GUIDED (recommended + alternatives)**
    When direction is clear but alternatives worth acknowledging. Format:
    ```
    ## APPROACH: {recommended_name} — {one-line why}
    **Also considered:** {alt_A} (rejected: {why}), {alt_B} (rejected: {why}).
    Proceeding with {recommended_name} unless overridden.
    ```

    **Mode 4: ADVERSARIAL (steelman each option)**
    For critical decisions requiring strongest-case analysis. Format:
    ```
    ## ADVERSARIAL ANALYSIS: {decision}
    ### Case for A: {strongest argument} — counter: {strongest counter}
    ### Case for B: {strongest argument} — counter: {strongest counter}
    **Decision:** Option {X}. Rationale: {why X wins despite its counters}.
    ```

    **Mode 5: AUTO-COLLAPSE (autonomous mode)**
    When no user response is expected within the current session, proceed with the best-scored option.
    Announce: "Auto-collapsing to Option {X} (score {Y}/10) — autonomous mode. Override with option name if a different choice is needed."

    Always use superposition before mutations that affect production state, security policies, or irreversible data changes.
    **State Snapshot Protocol**

    SNAPSHOT before every mutation → DIFF after → ROLLBACK on failure.

    **Snapshot scope:** Config checksums, service states, permissions, package versions.

    **Diff format:** Changed/Unchanged/New/Removed per category with before/after values.

    **Rollback triggers:** Service failed/inactive, unexpected file change, health check FAILS, critical config REMOVED.

    **Rollback plan:** Documented BEFORE mutation with revert steps, service restore, and verification.

    **Checkpoint persistence:** Write snapshot to `.ai/snapshot_<timestamp>.json`. Update Connection Context Card `last_state` (both conditional on `save_server_state: true`).

    See RULES.md §SYADMIN §State Snapshot Automation for batch snapshot scripts, JSON bundle format, diff output template, and rollback execution protocol.

<!-- ai-instructions:0.7.1 -->
