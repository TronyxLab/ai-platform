# 08-ai-code — Audit of AI-written code patterns

## $ARTIFACT_CONTRACT
- PURPOSE: Pre-launch audit of characteristic AI-generated software defects; maximize production risk reduction per unit of code churn.
- DESCRIPTION: Evidence-verified findings of duplication, contradiction, dead code, fake config, exception swallowing, doc/code drift, test mirroring, contract drift.
- RATIONALE: Week before launch — no rewrites; only provable defects with minimal cleanup paths.
- ACCEPTANCE_CRITERIA: every finding has severity/category/files/symbols/evidence/scenario/impact/confidence/action; hypotheses explicitly marked HYPOTHESIS; final TOP-30 ranked by risk/churn.
- IMPLEMENTS: pre-launch audit wave (meta-refactoring).
- IMPACTS: none (read-only audit, no code changes).
- REQUIRES: repo working tree at audit start commit.

## Method
- Wave model: up to 10 parallel read-only subagents per wave, narrow missions, no cross-context.
- Verification: finding = confirmed by reading code with exact file:line evidence; otherwise tagged HYPOTHESIS.
- Dedup + ID assignment centralized here (IDs AI-0001… sequential in write order).

## Severity scale
- CRITICAL: breaks prod / data loss / security hole.
- HIGH: probable incident or silent misbehavior.
- MEDIUM: contradiction / maintenance risk with realistic scenario.
- LOW: cleanup value only.

## Exclusions (by-design, not findings)
GENERATED files (entrypoint-manifest.yaml, secrets-manifest.yaml, platform-env.yaml, generated AGENTS.md sections); templates/template-* payload copies; thin shell facades exec'ing `python3 -m …`; .env.platform / practices.lock; anything already enforced by a passing tests/gates/ check (unless the gate itself is wrong).

## Scoring for TOP-30
rank = (production risk × evidence strength) ÷ code churn; Pre-launch actionable = small churn + high risk.

## Files
- findings-001.md … : incremental batches (~theme per batch)
- AI-TOP30.md: final ranking
