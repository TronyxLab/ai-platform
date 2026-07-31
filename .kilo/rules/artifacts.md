# §ARTIFACT_REGISTRY
## $ARTIFACT_REGISTRY

    Every management artifact follows the journal naming model: sequential NN prefix within a NNN-slug task folder.

    ### Naming Grammar (single source of truth — do NOT repeat in roles/skills)

    **Folder:** `.ai/plans/{NNN:03d}-{slug}/`
    - NNN  — zero-padded 3-digit sequence. Allocation rule: re-glob `.ai/plans/*` IMMEDIATELY before mkdir; NNN = max existing + 1; if taken at mkdir time → increment and retry.
      Post-merge collisions (parallel worktrees) are TOLERATED: folder identity = full `NNN-slug` string, never NNN alone. Do NOT renumber existing folders.
    - slug — 2-4 kebab-case lowercase words.

    **File:** `{NN}-{Type}[-{qualifier}].md`
    - NN        — 2-digit GLOBAL creation-order sequence within the task folder (01, 02, ...);
                 next NN = max existing NN in folder + 1.
    - Type      — CLOSED vocabulary: Brief | DevPlan | VerificationReport | StatusReport | Debt.
    - qualifier — optional, kebab-case lowercase [a-z0-9-] only (no dots/underscores/uppercase);
                 wave/phase/fix context: -fix-d12, -wave-t5-1, -phase2, -preimpl.
    - NN collisions from parallel sessions are TOLERATED: file identity = full filename, never NN alone. Do NOT renumber existing files; R1 (highest NN) resolves authority.

    ### Rules

    | Rule | Description |
    |------|-------------|
    | R1 AUTHORITATIVE | The authoritative artifact of type T = highest NN matching `{NN}-{Type}*.md`. |
    | R2 BAN LIST | Forbidden type names (converge to VerificationReport): QAAuditReport, QAImplReport, GateAudit, AuditReport, QAReport. Any type outside the closed vocabulary is a violation. |
    | R3 PAYLOADS | Non-artifact files (backups, quarantine, data, .bak) go into a subfolder (e.g., files/); root-level *.md is reserved for canonical artifacts. |
    | R4 SINGLE SOURCE | This grammar is defined ONLY in artifact-registry; roles/skills keep one example + a pointer. |

    ### Artifact Table

    | Artifact | Path Pattern | Created by | Trigger |
    |----------|-------------|-----------|---------|
    | Brief | .ai/plans/{NNN:03d}-{slug}/{NN}-Brief.md | Architect | LARGE task |
    | DevPlan | .ai/plans/{NNN:03d}-{slug}/{NN}-DevPlan.md | Architect | STANDARD or LARGE task |
    | VerificationReport | .ai/plans/{NNN:03d}-{slug}/{NN}-VerificationReport.md | QA | After verification |
    | StatusReport | .ai/plans/{NNN:03d}-{slug}/{NN}-StatusReport.md | Sysadmin | After operations |
    | Debt | .ai/plans/{NNN:03d}-{slug}/{NN}-Debt.md | Any role | On discovery of deferred design debt |

    ### Task Size Rules

    | Size | Criteria | Folder | Artifacts |
    |------|----------|--------|-----------|
    | SMALL | ≤8 files, no arch/API/schema changes | None | None |
    | STANDARD | 9-20 files, business logic | .ai/plans/NNN-slug/ | 01-DevPlan.md only |
    | LARGE | >20 files OR arch/schema/contract changes | .ai/plans/NNN-slug/ | 01-Brief.md + 02-DevPlan.md |

    ### Path Rules

    - SMALL tasks: no folder, no artifacts — verbal only
    - All artifacts for one task share the same .ai/plans/NNN-slug/ folder
    - NN starts at 01 and increments globally across the folder
    - Readers resolve "the DevPlan" as the highest-NN `*-DevPlan*.md` (R1)

<!-- ai-instructions:0.6.1 -->
