# Findings 004 — Shell↔Python drift, default-value divergence
# Wave 1 · agent: shell-drift

## AI-0020 [HIGH] [default-dupe/timeout-drift]
Files: core/lib/ssh.sh:111 (`timeout="${4:-600}"`); core/internal/shared/timeouts.py:130 (DEPLOY_TIMEOUT=900); core/internal/bootstrap/remote_executor.py:63-64 (stale "parity 600s" comment)
Symbols: ssh_exec deploy-mode default
Evidence: same concept «SSH deploy timeout» = 600 in shell facade vs 900 in Python SoT; comment asserts parity with value it no longer matches; lib/ssh.sh:178-185 TRAP reasons about 600s margin.
Problem: cold-node ops via shell channel die at 600s while Python channels get 900s; misleading comments compound.
Why AI-pattern: SoT introduced post-factum; facade default not backfilled.
Minimal cleanup: lib/ssh.sh default ← 900 (or source from python -c once); fix comment/TRAP.
Code churn: <10 lines. Pre-launch: yes.
Confidence: high.

## AI-0021 [HIGH] [shell-drift/gate-blind-spot]
Files: makefiles/deploy.mk:145-146,160-165; core/internal/build/hermes_images.py:16; tests/gates/test_gate_thin_wrapper.py:89
Symbols: L2_ORG normalization, PLATFORM_VERSION extraction, dual-tag push
Evidence: org-normalization (strip dots/hyphens) + version extraction + tag/push implemented wholly in .mk recipe; hermes_images.py explicitly disclaims push logic; thin-wrapper gate scans ONLY entrypoints/*.sh.
Problem: release-critical image policy has its single implementation in the one layer no language-policy gate covers.
Why AI-pattern: logic accreted into make recipe over iterations.
Actual risk: normalization/tag bugs surface only at push time during release.
Minimal cleanup: extract to Python helper invoked by recipe (strangler), extend gate to *.mk binary-call scan.
Code churn: ~40 lines. Pre-launch: post OK (document as debt if deferred).
Confidence: high.

## AI-0022 [MEDIUM] [ssh-dup/gate-gap]
Files: .github/workflows/deploy-project.yml:342,362,372 vs core/internal/shared/ssh_opts.py:40-51; tests/gates/test_gate_ssh_opts_sole_path.py:201-243
Symbols: inline ssh option sets
Evidence: CI hand-assembles 4 of 5 canonical flags, omitting ConnectTimeout=30; gate greps only for presence of ConnectTimeout literal pattern, not completeness/SoT usage (contrast core-deploy.yml:127 which uses `ssh_opts --shell`).
Problem: project-deploy SSH has no connect timeout → black-holed host burns job-level timeout; flag edits require manual replication in 3 places.
Minimal cleanup: switch workflow steps to `python3 -m core.internal.shared.ssh_opts --shell` (pattern already exists in core-deploy.yml).
Code churn: ~6 lines/workflow. Pre-launch: yes.
Confidence: high.

## AI-0023 [MEDIUM] [flag-drift/wrong-target]
Files: makefiles/ci.mk:144; core/entrypoints/secrets.sh:19; core/internal/secrets/decrypt_secrets.py:450-456,389-391
Symbols: secrets-unlock NODE arg, enc_path positional
Evidence: `make secrets-unlock NODE=x` → NODE lands in enc_path position; zero NODE logic in backend → glob fallback picks sorted(matches[0]).
Problem: multi-node operator machine silently decrypts alphabetically-FIRST node's secrets instead of requested node.
Why AI-pattern: CLI signature drifted from wrapper contract; nobody wired the selector.
Minimal cleanup: implement NODE filter in decrypt_secrets (resolve via node registry) or reject unknown first-arg.
Code churn: ~25 lines. Pre-launch: yes (operator foot-gun).
Confidence: high.

## AI-0024 [MEDIUM] [default-dupe]
Files: core/lib/secrets.sh:35; core/internal/shared/deploy_paths.py:215; core/internal/secrets/decrypt_secrets.py:369; core/internal/healthcheck/modules_healthcheck.py:287
Symbols: NODE_CONFIGS_DIR / NODE_CONFIGS_REMOTE_BASE / raw literals
Evidence: same physical dir resolved 4 ways; sibling platform_export_metrics.py:72 uses canonical resolver with overlay; no parity gate covers literals.
Impact: overriding NODE_CONFIGS_REMOTE_BASE fixes exporter but silently ignored by healthcheck filter + secrets glob.
Minimal cleanup: route 3 stragglers through deploy_paths resolver.
Code churn: ~12 lines. Pre-launch: cheap.
Confidence: high.

## AI-0025 [MEDIUM] [default-dupe]
Files: core/internal/healthcheck/platform-export-metrics.sh:64-65; core/lib/secrets.sh:55; core/internal/notify/notify-hook.sh:41; core/templates/module.mk:51; core/internal/shared/deploy_paths.py:256
Symbols: STATUS_METRICS_JSON, SECRETS_ENV_FILE literals vs run_base() resolvers
Evidence: 142-W2 relocated run artifacts to /var/lib/platform/run with PLATFORM_RUN_BASE override; every shell/make consumer re-hardcodes literal AND export pins old path unconditionally, bypassing resolver chain.
Problem: setting canonical PLATFORM_RUN_BASE splits state across two directories (Python relocates, cron/status/secrets stay).
Minimal cleanup: shell wrappers consult PLATFORM_RUN_BASE (one eval line each) or drop the knob.
Code churn: ~15 lines.
Confidence: high mechanism / med likelihood.
