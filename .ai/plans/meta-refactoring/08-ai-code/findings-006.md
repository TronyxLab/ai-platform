# Findings 006 — Comments/docstrings contradicting code (doc-drift)
# Wave 1 · agent: doc-drift · D1-D3 adversarially verified

## AI-0036 [LOW·LATENT] [doc-drift/broken-exclusion] VERIFIED
Files: core/internal/static/hardcoded_paths.py:20,44-47
Symbols: _HARDCODED_HOME_PATH regex
Evidence (verifier): lookahead misplaced — r'/home/[\w.-]+/(?!runner/work/)[\w.-]+/' checks exclusion AFTER first component, so /home/runner/work/… MATCHES; documented CI-path exception non-functional. LATENT: no such literals in tree today; no test covers the negative case. Errs toward false-positives only.
Minimal cleanup: move lookahead to '/home/(?!runner/work/)'. Churn ~5 lines + unit test. Post-launch OK. Confidence: high.

## AI-0037 [MEDIUM] [doc-drift/false-contract]
Files: core/internal/scaffold/project_scaffolder.py:28 vs github_ops.py:27,47+
Evidence: module docstring "Never auto-creates GitHub repos (developer runs gh manually)" while create_github_repo runs `gh repo create` as Step 7 when gh CLI present.
Problem: contract misleads operators/agents about side effects of make new-project (repo creation + push).
Minimal cleanup: fix docstring OR gate auto-create behind explicit flag. Churn <10 lines. Pre-launch: yes (surprise-repos). Confidence: high.

## AI-0038 [MED·ACTIVE-conditional] [doc-drift/stale-invalidation-guarantee] VERIFIED
Files: core/internal/bootstrap/lifecycle/state_machine.py:23,448,523 vs 525-563 (_phase_input_hash), 581-602 (phase_needs_rerun)
Evidence (verifier): hash covers phase_value + node.yaml{modules,services} + state_machine.py bytes ONLY — NOT lifecycle/phases/*.py (nor phases/__init__.py) contrary to all three docstring claims. Editing phase business logic leaves done φ8/φ8.5/φ11/φ12/φ13 "done" on bootstrap-node/node-update. NOTE: `make converge` unaffected (separate reconciler path).
Minimal cleanup: include phases/*.py mtimes+hash in hasher; align docstrings. Churn ~15 lines. Pre-launch: yes if node-update expected soon after code changes. Confidence: high.

## AI-0039 [LOW] [doc-drift/SoT-table]
Files: core/internal/shared/timeouts.py:20-21 ("deploy=600") vs :130 DEPLOY_TIMEOUT=900
Evidence: invariant table contradicts own constant post-cold-node-fix. Consumers trusting table budget wrong.
Cleanup: update table row. Churn <5 lines. Bundle with AI-0002 regen. Confidence: high.

## AI-0040 [MEDIUM] [doc-drift/return-contract]
Files: core/internal/deploy/healthcheck_poller.py:6,22 vs :118,230-236
Symbols: poll_project/_try_docker
Evidence: docstrings promise ⎋str(healthy|unhealthy), «Non-fatal: returns 'unhealthy' on failure»; actual returns HealthcheckResult dataclass with THIRD status "timeout".
Problem: consumers coded against documented contract mishandle timeout status; grep callers to confirm adaptation before editing docs.
Minimal cleanup: docstring ← reality (or adapter for str consumers). Churn <10 lines. Confidence: high.

## AI-0041 [LOW] [doc-drift/noop-validator] VERIFIED-COSMETIC
Files: core/internal/llm/policy_schema.py:2,277-293 vs from_yaml:370-376
Evidence (verifier): _validate_default_profile_exists body = `return v`; real check EXISTS in from_yaml step 4a; no-op annotated @note best-effort. STRUCTURE names load_yaml/validate_with_jsonschema don't exist (flow real, names stale). No functional gap.
Cleanup: delete no-op validator or rename honestly; fix STRUCTURE names. Churn <10 lines. Confidence: high.

## AI-0042..0045 [LOW] [doc-drift/stale-structure-cluster]
- AI-0042 shared/retry.py:31-35 TRAP says inventory row missing; row exists in shared/AGENTS.md → completed deferred work still flagged pending.
- AI-0043 bootstrap/preflight.py:3 STRUCTURE references run_all_checks() — renamed run_preflight (:493); agent grep-discovery misses entrypoint.
- AI-0044 state_machine.py:19 module invariant claims subprocess.run(timeout=120|600) policy lives here — no subprocess.run in file anymore (migrated to helpers/phases).
- AI-0045 static/dead_code.py:4 STRUCTURE names _scan_makefile_refs/_scan_precommit_refs — both removed, replaced by _scan_file_refs (:300).
Common pattern: STRUCTURE/doc headers not regenerated during renames → next agent greps dead symbols.
Cleanup batch: one doc-sweep commit. Churn <20 lines total. Post-launch OK. Confidence: high each.

# Related cross-refs: timeouts.py doc ↔ AI-0002/AI-0020; scaffold gh behavior ↔ AI-0017.
