---
name: doc-protocols
description: Documentation Protocols for AI-Friendly Artifacts — $DOCUMENT_PLAN skeleton, $START/$END tags, $ARTIFACT_CONTRACT, DevPlan protocol, artifact lifecycle, unified severity and verdict, XML knowledge graph
---

# region MODULE_CONTRACT
## @purpose  SKILL: Documentation Protocols for AI-Friendly Artifacts — $DOCUMENT_PLAN skeleton, $START/$END tags, $ARTIFACT_CONTRACT, DevPlan protocol, artifact lifecycle, unified severity and verdict, XML knowledge graph
## @scope    architect, coder, qa, docs
## @invariants
##   - @protected  true
##   - Тело скилла — 1:1 перенос framework-source 0.6.3
# endregion MODULE_CONTRACT

  ## Documentation Protocols for AI-Friendly Artifacts

  When creating structured .md documents, use the following protocol. All artifact files follow the journal naming grammar defined in §ARTIFACT_REGISTRY: `{NN}-{Type}[-{qualifier}].md` with NN = max existing NN + 1.

  ### 1. $DOCUMENT_PLAN — Context Window Management
  Create a `$DOCUMENT_PLAN` skeleton BEFORE generating the full document body. This forces verbalization of structure before expansion (protection against context drift).
  ```
  $START_DOCUMENT_PLAN
  ### Document Plan
  **SECTION_GOALS:**
  - GOAL [description] => GOAL_ID
  **SECTION_USE_CASES:**
  - USE_CASE [scenario] => SCENARIO_ID
  $END_DOCUMENT_PLAN
  ```

  ### 2. $START_<ARTIFACT> / $END_<ARTIFACT> Tags — Semantic Boundary Markers
  Every management artifact MUST wrap its content with `$START_<ARTIFACT>` and `$END_<ARTIFACT>` paired tags. These markers provide unambiguous boundaries for autonomous agents parsing the document.

  **Required artifacts with $START/$END:**
  | Artifact | Start Tag | End Tag | Produced by |
  |---|---|---|---|
  | Brief.md | `$START_BRIEF` | `$END_BRIEF` | Architect |
  | DevPlan.md | `$START_DEVPLAN` | `$END_DEVPLAN` | Architect |
  | VerificationReport.md | `$START_VERIFICATION_REPORT` | `$END_VERIFICATION_REPORT` | QA |
  | StatusReport.md | `$START_STATUS_REPORT` | `$END_STATUS_REPORT` | Sysadmin |
  | Debt.md | `$START_DEBT` | `$END_DEBT` | Any role |

  Files use journal naming: `{NN}-{Artifact}.md` (e.g., `02-DevPlan.md`, `03-VerificationReport.md`). See §ARTIFACT_REGISTRY for full grammar.

  ### 3. $ARTIFACT_CONTRACT — Unified Contract Block
  Every management artifact MUST begin with an `$ARTIFACT_CONTRACT` block declaring all 7 mandatory fields:

  ```
  $ARTIFACT_CONTRACT
  PURPOSE:               (what — business goal)
  DESCRIPTION:           (how — approach and scope)
  RATIONALE:             (why — design decisions)
  ACCEPTANCE_CRITERIA:   (verification — how to confirm completion)
  IMPLEMENTS:            (links to requirements, issues, or parent artifacts)
  IMPACTS:               (artifacts or systems affected by this artifact)
  REQUIRES:              (prerequisites — external dependencies)
  $END_ARTIFACT_CONTRACT
  ```

  These fields make every artifact self-describing for autonomous agents. An agent opening any management artifact immediately knows what it is, why it exists, what it implements, and how to verify it.

  ### 4. DevPlan Protocol
  Development plans MUST contain:
  - Draft Code Graph (XML) — structural anchors
  - Step-by-step Data Flow — process simulation
  - Acceptance Criteria — verifiable completion tests
  - File Manifest — all files to be created/modified

  ### 5. Graph Protocol (XML Knowledge Graph)
  XML knowledge graphs follow strict naming: replace dots with underscores, add `_py`/`_CLASS`/`_FUNC`/`_METHOD` suffixes. Each entity has TYPE, keywords, annotation, and CrossLinks.

  ### 6. Artifact Lifecycle — Code and Infra Pipelines

  **Code Pipeline:**
  ```
   {NN}-Brief.md ──► {NN+1}-DevPlan.md ──► Code + Tests ──► {NN+2}-VerificationReport.md ──► {NN+3}-DevPlan-fix-* (cycle)
  ```

  **Infra Pipeline:**
  ```
   {NN}-StatusReport.md ──► (if issues) TRAP[DECISION] / TRAP[INCIDENT] placed directly
  ```

  Both pipelines follow the unified contract model — every artifact in the chain declares `$ARTIFACT_CONTRACT` and uses `$START/$END` boundary markers.

  ### 7. Unified Severity Scale

  All management artifacts MUST use the unified severity scale:

  | Level | Meaning | Applies to |
  |-------|---------|------------|
   | CRITICAL | Prevents compilation, breaks tests, data loss | Brief, VerificationReport, StatusReport |
   | HIGH | Violates acceptance criteria, security issue | Brief, VerificationReport, StatusReport |
   | MEDIUM | Semantic markup gaps, missing LDD logs, naming divergence | Brief, VerificationReport, StatusReport |
  | LOW | Cosmetic, documentation formatting | All |

  ### 8. Unified Verdict Scale

  All verification and status artifacts MUST use the unified verdict scale:

  | Verdict | Meaning |
  |---------|---------|
  | SUCCESS | All checks passed, no issues |
  | PARTIAL | Non-blocking issues found, artifacts usable with caveats |
  | FAIL | Blocking issues, cannot proceed |
  | BLOCKED | Environmental (permission denied, command not found, etc.) |

<!-- ai-instructions:0.7.1 -->
