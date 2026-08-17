---
name: role-retrospective
description: Role Retrospective — critical analysis of agent logic after an inefficient session to identify systemic problems in the role
---

<!-- @protect: After a long inefficient session, the agent does not analyze its own errors — lack of retrospective leads to repeating the same patterns. The skill is activated via skill tool for structured self-analysis. -->

# region MODULE_CONTRACT
## @purpose  SKILL: Role Retrospective — critical analysis of agent logic after an inefficient session to identify systemic problems in the role
## @scope    architect, coder, qa, sysadmin
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  # SKILL: Role Retrospective

  Role retrospective after an inefficient session — critical analysis of agent logic for handoff to the framework architect.

  ## Bias Warning

  You are analyzing your own work — risk of confirmation bias. LOW confidence on uncertain findings. Without concrete evidence from the session, the finding is rejected.

  ## F1 — Problem Analysis

  The agent scans its context and finds:
  - Useless instructions (present in the role but never used)
  - Contradictions between instructions and practice
  - Redundant steps (where a shorter approach was possible)
  - Loops (repeated unsuccessful attempts)
  - Wrong decisions (where a suboptimal path was chosen)
  - Missing rules (situations for which no instructions existed)
  - Repeated actions (candidates for standardization)
  - Automation candidates

  Each item: problem + evidence from session + reason + confidence [HIGH|MEDIUM|LOW].

  ## F2 — Classification

  Categorizes findings:
  - **Add** — new rules
  - **Change** — rewrite existing ones
  - **Remove** — instructions that degrade performance
  - **Skill** — what should become a separate skill
  - **SOP** — standard operating procedures
  - **Automate** — tools/scripts instead of textual instructions

  ## F3 — Meta-Rules

  For each change — search for a general principle that prevents an entire class of errors.
  Prefer one universal rule over several specific ones.
  If a meta-rule cannot be formulated → the change is likely too narrow.

  ## F4 — Quality Gate

  Evaluation table for each change:
  - Frequency of use
  - Token reduction
  - Speed improvement
  - Error probability reduction
  - Specificity (relevant beyond this session?)

  Changes specific only to the current session → discarded.

  ## F5 — Self-Criticism

  - Can it be shortened further?
  - Are there repetitions?
  - Have new exceptions appeared instead of general principles?

  ## Output Format

  ### Part A — Role Retrospective

  ```markdown
  # Role Retrospective {role_name}

  ## 1. Problems Found
  - [{severity}] {problem} [confidence: HIGH|MEDIUM|LOW]
    - Evidence: {specific moment from session}
    - Reason: {why}
    - Proposed fix: {what}

  ## 2. Classification
  ### Add: ...
  ### Change: ...
  ### Remove: ...
  ### Extract to Skill: ...
  ### Standardize (SOP): ...
  ### Automate: ...

  ## 3. Meta-Rules
  - {general principle} → prevents error class: {which}

  ## 4. Quality Gate
  | Change | Frequency | Tokens ↓ | Speed ↑ | Errors ↓ | Specificity | Verdict |
  |--------|-----------|----------|---------|----------|-------------|---------|

  ## 5. Self-Criticism
  - Can it be shortened further? ...
  - Are there repetitions? ...
  - Have new exceptions appeared? ...
  ```

  ### Part B — Framework Architect Assignment

  ```markdown
  # Task: Verification of Role Retrospective {role_name}

  ## Context
  A retrospective of a session in the role {role_name} was conducted.
  Potential problems in the agent's logic were found.
  Your task is to critically verify each finding.

  ## Input Data
  - Compiled role: .kilo/agents/{agent_name}.md
  - Role rules: .kilo/rules/*.md
  - Retrospective report (Part A)

  ## Instructions
  1. Verify each finding from Part A
  2. Assess whether the problem is systemic or session-specific
  3. For systemic problems: decide whether to include the change in the framework
  4. Consider meta-rules — prefer general principles over specific rules
  5. Check whether the change would create new problems in other scenarios

  ## Evaluation Criteria
  - Frequency: how often will it manifest?
  - Tokens: will it reduce consumption?
  - Speed: will it speed up the role's work?
  - Errors: will it reduce error probability?
  - Specificity: is it too narrow?

  ## Expected Result
  - List of accepted changes (with justification)
  - List of rejected changes (with reason)
  - Final edits to framework-source role (if applicable)

  ## Important
  You know the framework better than the agent that conducted the retrospective.
  Don't trust findings blindly — verify independently.
  ```

  ## Anti-Degradation Measures

  1. **Confidence tagging** — each finding: HIGH/MEDIUM/LOW
  2. **Bias warning** — "You are analyzing your own work — risk of confirmation bias. LOW confidence on uncertain findings"
  3. **Evidence requirement** — without concrete evidence from the session, the finding is rejected
  4. **Meta-rule extraction** — prevents accumulation of exceptions
  5. **Quality gate** — discards session-specific changes
  6. **Self-criticism** — check for repetitions, redundancy, new exceptions
  7. **Architect review** — independent verification by architect (Part B)

<!-- ai-instructions:0.7.0 -->
