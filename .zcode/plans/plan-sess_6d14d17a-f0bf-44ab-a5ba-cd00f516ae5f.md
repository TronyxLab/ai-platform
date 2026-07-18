# Plan: Architecture Forensics Report — ai-platform (full re-run, 2026-07-18)

## Scope
Apply the `arch-forensics` SKILL protocol (7 core tasks + S7–S15 superposition + 6 collapse signals) to the ai-platform repository at `/Users/tronyx/projects/ai-platform`. Produce an objective model — **no fixes, no refactoring, no code proposals** (skill principle #2). Output to a single artifact.

## Phase 1 summary (already complete)
Three Explore agents mapped: (1) top-level arch + Makefile facade + Triple Delivery Model evidence + bootstrap pipeline + 9 CI workflows + entry points + 4 templates; (2) 14 modules + module.mk contract + docker-compose topology + 6 backbone networks + shared infra + hermes L0/L1/L2 build + Prometheus scrape graph + discover_modules.py; (3) 8 AGENTS.md hierarchy + entrypoint-manifest.yaml (34 verbs, 41 gates, 5 forbidden) + 10 invariants with code-level evidence + 42 gate files + TRAP[DECISION] log + verb-glossary drift + 874-test inventory.

I then **directly verified** the load-bearing claims:
- ✅ Prior CRITICAL finding (`internal ↛ modules` INVARIANT COLLAPSE) **still stands**: `core/entrypoints/healthcheck.sh:12-13` still contradicts `core/AGENTS.md`; `_looks_like_path()` at `test_cross_layer_imports.py:121` still can't see calls through intermediate vars (`hc_script` @ node-lifecycle.sh:842, `install_script` @ deploy-modules.sh:333-341, `hook_script` @ deploy-project.sh:167-170/729).
- 🔧 **Corrected an Explore-agent error**: `restart` IS defined (`core/Makefile.common:14` `restart: stop start`, included by module.mk:60) — not drift.
- ✅ `restore` at module level is legitimately absent (intentional per module.mk:15, enforced by sudo-whitelist) — not drift.
- ✅ Confirmed observability gap: postgres + hermes-agent have **zero** Prometheus scrape coverage; only `postgres` module declares `severity: critical`.
- ✅ Found `$ARTIFACT_CONTRACT` convention (`.kilo/rules/markup.md:11`, `principles.md:10` — 7 mandatory fields) and the existing report at `.ai/plans/001-arch-forensics/01-VerificationReport.md`.
- ⚠️ The "42 gate files vs 41 manifest `- id:` entries" discrepancy is **unconfirmed** — I verified the two files the third agent suspected as orphans (`test_p20_container_coupling`, `test_restart_consistency`) ARE both in the manifest (count=1 each). The 1-unit gap will be reported as "unconfirmed, requires running `test_gate_manifest_integrity`" rather than overclaimed.

## Phase 2 — Report structure (will be written to the artifact)
1. `$ARTIFACT_CONTRACT` header block (7 fields per `.kilo/rules/markup.md`), framed as the 2nd verification of plan 001-arch-forensics, with a delta-vs-01 section.
2. **Executive Summary** — counts, verdict.
3. **Task 1 — System Architecture**: components table, data-flow ASCII (Triple Delivery + CI fan-in via `workflow_run`), lifecycle (17 init steps + 6 update steps).
4. **Task 2 / S7 — Architectural Boundaries** (6 boundaries): Makefile-facade (POROUS — provision bypassed ×5), entrypoints→internal→lib (ENFORCED), **internal↛modules (FRACTURED/BROKEN)**, modules↛internal (POROUS — cron/systemd invisible to linter), Core SCP vs Context-overlay git (ENFORCED), module isolation (ENFORCED).
5. **Task 3 — Component Inventory**: node-lifecycle.sh, deploy-modules.sh, provision-environment.sh (5 callers), platform-env.yaml, tests/gates/; full 14-module contract matrix.
6. **Task 4 — Violations**: the persisted cross-layer violation (CRITICAL), the blind gate (CRITICAL), healthcheck.sh doc contradiction (HIGH), cron/systemd hidden reverse deps (HIGH), sandwich cycle internal→modules→internal (MEDIUM), provision SRP overload (MEDIUM), doc drift (`verify` verb missing from root glossary; `static` vs `static_audit` marker; template `sync-env` vs canonical `project-sync-env`), etc.
7. **Task 5 / S14 — Fragility Map**: `core/lib/paths.sh` (PLATFORM_ROOT cascade ~25 files), module.yaml D4 schema (5+ gates + deploy orchestrator), entrypoint-manifest.yaml (enforced triad), provision-environment.sh (5 callers), hardcoded `/opt/platform/core/internal/*` prod paths (cron/systemd hidden breakage).
8. **Task 6 / S10 / S11 — Risk Map**: per-component L×I÷D scores; postgres blast radius (4+ writers: litellm/langfuse/backup-cron/hermes-readiness — **NO exporter, blind to Prometheus**); nginx (entire public ingress); platform-secrets (recovery dependency for deploy + alerting); hermes-agent (NEW: no metrics exposure — observability gap).
9. **S8 Coupling / S9 Ownership / S12 Invariants / S13 Dependency / S15 Hidden Deps**: the 10 root invariants each with declared/verified/violated/implicit/missing; NO_PROXY SoT (HELD by T8.5); redis digest duplication (acknowledged TRAP); provision multi-updater conflict; hidden deps via crontab/systemd/secrets.env shared resource.
10. **Collapse Detection — 6 signals**:
    - ⚡ **INVARIANT COLLAPSE — CRITICAL** (persists): S12 + S15 + S7 concur — declared `internal↛modules` rule is fictitious, gate gives false assurance, docs contradict.
    - ⚡ **BOUNDARY COLLAPSE — HIGH** (persists): modules↛internal POROUS via invisible cron/systemd channels.
    - NEW candidate: **OBSERVABILITY COLLAPSE** (HIGH) — postgres is `severity: critical` + multi-writer + blast radius 4 modules, yet has **no metrics exporter and zero Prometheus visibility**; hermes-agent similarly unscraped. Failures in these components are invisible to the monitoring stack until they become hard outages. (Reported as a candidate collapse, evidence-grounded, since the skill allows collapse-type findings beyond the fixed 6 when 3+ dimensions concur.)
    - NON-fires: OWNERSHIP, RISK, FRAGILITY, CIRCULAR — each with one-line justification.
11. **Strengths section** (objectivity): digest-pinned images, manifest↔Makefile↔AGENTS.md triad parity, 36 anti-drift gates, dual-delivery invariant held, DAG acyclic, 0 forbidden-verb violations.
12. **Delta vs 01-VerificationReport.md**: what changed, what persisted, what's new.
13. `$END_VERIFICATION_REPORT`. No fixes, no "want me to fix this?" closer (skill protocol).

## Phase 3 — Write the artifact (single file)
- **Path:** `.ai/plans/001-arch-forensics/02-VerificationReport.md` (per skill rule: CRITICAL collapse found → VerificationReport artifact required; NN = existing max+1 in the plan dir).
- **Format:** `$ARTIFACT_CONTRACT` (7 fields) → `$START_VERIFICATION_REPORT` → report → `$END_VERIFICATION_REPORT`, matching the existing `01-VerificationReport.md` envelope exactly.
- **No code changes.** No AGENTS.md edits. No test edits. Single new markdown file only.

## Adherence to skill protocol (explicit)
- No fixes, no refactoring, no code proposals (skill §Запрещается).
- Every assertion cites `file:line` (skill principle #5).
- All 7 tasks + ≥3 superposition modes covered (actually all 9: S7–S15) (skill §Запрещается).
- All 6 collapse signals checked + 1 new candidate (skill §Коллапс суперпозиции).
- Verdict grounded in evidence, not "ARCHITECTURALLY_SOUND" shortcut.
- Ends with the report — no "shall I fix?" closer (skill §5 Completion).

## What I will NOT do
- Modify any source, Makefile, manifest, AGENTS.md, or test.
- Propose fixes inline (a "Recommendations" section would violate skill principle #2; omitted by design).
- Run `make gate` or any mutating command — analysis is read-only.
- Pin the unconfirmed gate-count orphan — reported as "unconfirmed, gate run needed."