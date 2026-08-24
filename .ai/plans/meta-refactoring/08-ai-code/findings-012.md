# Findings 012 — Adversarial verification log & late additions
# Wave 2 · two QA verifiers re-traced the strongest claims independently

## Verdicts (infra/deploy claims — verifier 2, tree @ 1272521)
| ID | Claim | Verdict | Correction |
|----|-------|---------|------------|
| AI-0001 | langfuse URL port | CONFIRMED | HIGH but LATENT; only PLATFORM_LANGFUSE_URL wrong; internal consumers fine |
| AI-0002 | env 600 overrides SoT 900 | PARTIAL → LOW·LATENT | env files never sourced in deploy paths ⇒ effective 900; inert drift only |
| AI-0003 | nginx-proxy phantom | CONFIRMED → LOW·LATENT | sole victim = generated smoke test silently skipping |
| AI-0004 | prometheus rules fallback | CONFIRMED → **HIGH·ACTIVE** | prod path config_renderer.py:702 never passes output_dir ⇒ rules land outside mount ⇒ silent alert loss |
| AI-0006 | receive payload before lock | CONFIRMED → MED·ACTIVE-cond | no workflow concurrency block; rollback clobber window real |
| AI-0010 | llm keys exit-0 swallow | PARTIAL → MED | httpx errors NOT in caught tuple ⇒ process exits 1; silence lives at phase level non_fatal=True |

## Verdicts (code-level claims — verifier 1)
| ID | Claim | Verdict |
|----|-------|---------|
| AI-0036 | hardcoded_paths lookahead broken | CONFIRMED, LOW·LATENT (no matching literals in tree; errs safe) |
| AI-0038 | phase hash misses phases/*.py | CONFIRMED, MED (affects bootstrap-node/node-update, NOT converge) |
| AI-0041 | policy_schema no-op validator | CONFIRMED, LOW cosmetic (real check exists in from_yaml; annotated) |
| AI-0023 | secrets NODE→enc_path mismatch | CONFIRMED, MED·ACTIVE (runbook's own command triggers it) |
| AI-0027 | keep_images fake knob | CONFIRMED, LOW (zero prune logic transitively) |
| AI-0026 | --no-fallback-build decorative | CONFIRMED, LOW (argparse→dropped at main) |

BOTTOM-line: weakest evidence = E4's negative claim about node-side sshd env (out-of-repo); everything else traced in-tree.

## AI-0077 [MEDIUM] [contract-drift/networks] (found during verification)
Files: core/platform-infra.yaml:131-132 (provides.langfuse.networks=[hermes-agent-net]) vs langfuse/docker-compose.base.yml:107-113 (shared-db-net + observability-net) vs templates documenting proxy-net
Evidence: 3-way inconsistency; `langfuse` DNS resolves only on nets the container actually joins.
Consequence: even with port fixed (AI-0001), project reachability depends on which net the project joins — frontend projects on proxy-net can never resolve langfuse.
Cleanup: single network membership decision in platform-infra.yaml mirrored to compose. Churn ~10 lines. Pre-launch: bundle with AI-0001. Confidence: high.

# Final tally: 77 IDs total (AI-0001…AI-0077), of which 75 confirmed findings, 2 explicitly HYPOTHESIS-class notes (file_lock threading, AI-0057 adoption gap), 1 finding partially hypothesis-flagged (AI-0070 runtime mail behavior).
