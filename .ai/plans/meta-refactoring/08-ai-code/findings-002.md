# Findings 002 — Races, hidden invariants, security surface
# Wave 1 · agent: invariant-race

## AI-0006 [MEDIUM·ACTIVE-conditional] [invariant-race] VERIFIED
Files: core/internal/deploy/receive_flow.py:423-462; orchestrator.py:295-297,472,1094-1121
Symbols: ReceiveFlow.deploy payload replacement vs DeployOrchestrator.deploy Step-0 _FileLock; _restore_payload_files rollback
Evidence (verifier): replacement precedes lock (lock at :295-297 inside deploy(), called at receive_flow.py:472 AFTER os.replace loop); deploy-project.yml has NO `concurrency:` block; forced-command has no flock wrapper; rollback does non-atomic os.remove+shutil.copy2 over current target.
Scenario: concurrent receive of SAME project (CI retry overlapping newer push) ⇒ mixed v2/v3 payload composed up; failed-A rollback clobbers B's fresh payload. Compose ops themselves stay per-project locked.
Why AI-pattern: lock added at orchestrator layer while file mutation stayed earlier in flow.
Actual risk: payload-file mix / cross-deploy revert on retry-overlap.
Minimal cleanup: acquire per-project flock at start of receive handler (before staging replace); optionally add workflow concurrency group.
Code churn: ~20 lines. Pre-launch: yes. Confidence: high.

## AI-0007 [MEDIUM] [secret-surface]
Files: core/internal/shared/crypto.py:86
Symbols: hash_apr1
Evidence: password appended to openssl argv (`cmd.append(password)`).
Scenario: master password visible in world-readable /proc/<pid>/cmdline during hashing windows (φ4/node-update/dev-metrics); repo convention elsewhere is --password-stdin/env.
Minimal cleanup: pass via stdin (openssl passwd -apr1 reads stdin when arg omitted... verify flag form) — small patch + test.
Code churn: ~10 lines. Pre-launch: yes if multi-account nodes in scope.
Confidence: high mechanism / med exploit window.

## AI-0008 [LOW] [invariant-race]
Files: bootstrap/lifecycle/htpasswd.py:148-150; bootstrap/converge/projects.py:300-302
Symbols: write_htpasswd_file, reconcile_env_platform
Evidence: write_text + chmod-after (umask window 0644, non-atomic) while shared/atomic_writer.atomic_write_text(mode=…) is canon used by siblings.
Scenario: concurrent nginx auth_basic read sees partial htpasswd (transient 401s); brief world-readable hash.
Minimal cleanup: switch both to atomic_write_text with mode.
Code churn: <10 lines. Pre-launch: trivial.
Confidence: med.

## AI-0009 [LOW] [invariant-contradiction]
Files: healthcheck/metrics/json_writer.py:122-125 vs modules/status-page/collectors/config.py:239-240; platform_export_metrics.py:15 claim vs TRAP[DOCKER-BIND-MOUNT]
Symbols: atomic_write(in-place truncate), load_status_metrics
Evidence: writer truncates in place; reader json.load per request; exporter comment claims "status-page never sees partial file" while own TRAP documents the trade-off.
Scenario: cron export ∩ status request ⇒ JSONDecodeError → transient 503/readiness blip (~60s cadence).
Minimal cleanup: either temp-file+os.replace here, or fix the false comment.
Code churn: <10 lines.
Confidence: med.

## HYPOTHESIS (not counted): shared/file_lock._REENTRANT + notifications._THROTTLE_REGISTRY lack thread-locks — no threaded consumer found today (deploy parallelism is fork-based). Verify before any future ThreadPool adoption of FileLock.
# Verified clean: shell=True absent repo-wide in core/**; forced-command parser exact-match; project-name regex blocks traversal; tar filter="data"; pollers bounded.
