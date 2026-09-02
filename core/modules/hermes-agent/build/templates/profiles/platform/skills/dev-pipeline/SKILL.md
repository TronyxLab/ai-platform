---
name: dev-pipeline
description: Development Pipeline — full Brief → Architect → Coder → QA → Fix conveyor via subagents
---

# region MODULE_CONTRACT
## @purpose  SKILL: Development Pipeline — full Brief → Architect → Coder → QA → Fix conveyor via subagents
## @scope    architect, coder, qa, sysadmin
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## Development Pipeline — Brief → Architect → Coder → QA → Fix

  Trigger: the user provides a brief and asks to run the pipeline.
  The current session acts as ORCHESTRATOR: delegates stages to role subagents via the
  `task` tool and never implements code itself.

  ### Stage 0: INTAKE
  Capture the user's brief verbatim. Do not paraphrase business requirements.

  ### Stage 1: ARCHITECT (design)
  Delegate to the architect role. The architect:
  - Reads the codebase, classifies the task (SMALL/STANDARD/LARGE)
  - Asks the user clarifying questions BEFORE writing DevPlan if the brief has gaps
    or contradictions (question quotas per task size)
  - Produces DevPlan.md with $TASKS, $PARALLEL_GROUPS, $TEST_SPEC at .ai/plans/NNN-slug/
  GATE: DevPlan exists (or verbal plan for SMALL) → proceed.

  ### Stage 2: CODER (implementation waves)
  For each wave in $PARALLEL_GROUPS:
  - Spawn one coder subagent per parallel group (tasks within a group share no files)
  - Prompt: "Read <path>/DevPlan.md, implement Wave N: TASK-X, TASK-Y"
  - Coder creates exactly and only the tests from $TEST_SPEC
  - Wait for wave completion before starting the next wave.

  ### Stage 3: QA (verification)
   Delegate to the QA role: run tests, verify LDD trajectory
  (IMP:9 present), audit acceptance criteria from DevPlan, write VerificationReport.md
  with verdict (SUCCESS/PARTIAL/FAIL/BLOCKED).

  ### Stage 4: FIX loop
  If verdict != SUCCESS: send Issues from VerificationReport.md back to a coder
  subagent (fix scope only — no new features). Re-run Stage 3.
  Anti-loop: max 3 fix cycles. On the 3rd failed cycle — STOP, summarize open issues,
  escalate to the user.

  ### Stage 5: REPORT
  Output artifact paths (DevPlan, VerificationReport), final verdict, test summary.
  Then STOP (§COMPLETION_PROTOCOL).

<!-- ai-instructions:0.7.1 -->
