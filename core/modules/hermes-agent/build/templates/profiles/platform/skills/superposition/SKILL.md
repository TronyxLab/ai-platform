---
name: superposition
description: Superposition Protocol — 5 decision modes (FULL, BINARY, GUIDED, ADVERSARIAL, AUTO-COLLAPSE) for multi-hypothesis exploration before committing to implementation
---

<!-- @protect: Agents will commit to first idea without exploring alternatives — autoregressive generation penalizes mid-stream correction. AUTO-COLLAPSE mode required for autonomous sessions. -->

# region MODULE_CONTRACT
## @purpose  SKILL: Superposition Protocol — 5 decision modes (FULL, BINARY, GUIDED, ADVERSARIAL, AUTO-COLLAPSE) for multi-hypothesis exploration before committing to implementation
## @scope    architect, coder, qa, sysadmin, docs
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## Superposition Protocol — 5 Modes

  For ambiguous problems, generate multiple solution hypotheses BEFORE committing to implementation. The autoregressive nature of generation penalizes mid-stream corrections — explore the space first.

  ### Mode 1: FULL Superposition (3-5 options)
  For high-ambiguity decisions. Format:
  ```
  ## SUPERPOSITION: {problem_statement}
  ### Option A: {name} [score: X/10]
  Approach: {one-line description}
  Trade-offs: {cost vs benefit}
  Best when: {conditions}
  ### Option B: {name} [score: Y/10]
  ...
  ### Recommendation: Option {X} — {one-line justification}
  **Collapse signal:** Reply with A/B/C/D/E or describe your constraint.
  ```

  ### Mode 2: BINARY Trade-off (exactly 2 options)
  For clear either-or decisions. Format:
  ```
  ## TRADE-OFF: {decision_statement}
  | Criterion | Option A: {name} | Option B: {name} |
  |-----------|-----------------|-----------------|
  | Speed     | {rating}        | {rating}        |
  | Safety    | {rating}        | {rating}        |
  | Cost      | {rating}        | {rating}        |
  **Recommendation:** Option {X} because {reason}.
  ```

  ### Mode 3: GUIDED (recommended + alternatives)
  When direction is clear but alternatives worth acknowledging. Format:
  ```
  ## APPROACH: {recommended_name} — {one-line why}
  **Also considered:** {alt_A} (rejected: {why}), {alt_B} (rejected: {why}).
  Proceeding with {recommended_name} unless overridden.
  ```

  ### Mode 4: ADVERSARIAL (steelman each option)
  For critical decisions requiring strongest-case analysis. Format:
  ```
  ## ADVERSARIAL ANALYSIS: {decision}
  ### Case for A: {strongest argument} — counter: {strongest counter}
  ### Case for B: {strongest argument} — counter: {strongest counter}
  ### Case for C: {strongest argument} — counter: {strongest counter}
  **Decision:** Option {X}. Rationale: {why X wins despite its counters}.
  ```

  ### Mode 5: AUTO-COLLAPSE (autonomous mode)
  When no user response is expected within current session. Announce: "Auto-collapsing to Option {X} (score {Y}/10) — autonomous mode. Override with option name if different choice needed."

<!-- ai-instructions:0.7.1 -->
