# Findings 011 — Modules app-code sweep (status-page, backup-cron, hermes-agent, postgres, loadtest, platform-secrets, scaffold facades)
# Wave 2 · agent: modules-sweep

## AI-0069 [HIGH] [cross-file-contract/postgres-version]
Files: core/modules/postgres/module.yaml:12,20 («Shared PostgreSQL 16», «Version pinned here») vs docker-compose.base.yml:41 (postgres:18.4@sha256), :70 (PG18+ mount comment); root AGENTS.md «Единый PostgreSQL 16» repeats drift
Evidence: version pinned ONLY in compose; major = 18 ≠ 16 everywhere in docs/contracts.
Consequence: backup client choice, restore runbooks, and project docs planned against PG16 while node runs PG18 — restore-drill surprises at worst possible time.
Why AI-pattern: docs generated from older template, compose upgraded independently.
Minimal cleanup: update module.yaml description + AGENTS.md row to 18 (or pin 16 if downgrade intended — decision needed). Churn <10 lines. Pre-launch: YES. Confidence: high.

## AI-0070 [MED·HYPOTHESIS-on-runtime] [double-install/cron]
Files: core/modules/backup-cron/Dockerfile:97,101 vs scripts/crontab:28
Evidence: same crontab installed BOTH as /etc/cron.d/platform-backup (user field valid) AND via `crontab` spool install where `root` parses as command → failing run + mail noise. Double-fire factual (two installs); exact runtime mail behavior HYPOTHESIS (Debian cron specifics).
Consequence: every slot fires twice — one real + one failing (incl. hourly wal_sync).
Minimal cleanup: drop one of the two installs. Churn ~5 lines. Pre-launch: yes (backup path). Confidence: med-high.

## AI-0071 [MEDIUM] [contract-misstate/hermes-ports]
Files: core/modules/hermes-agent/module.yaml:8,14 («NO ports», proxy-net+project-net) vs docker-compose.base.yml:115-117 (127.0.0.1:{9119,8642} mappings) + networks incl. hermes-agent-net/observability-net; healthcheck.sh depends on the "forbidden" host ports
Consequence: module contract misstates exposure; consumers/gates reading module.yaml see loopback bindings as violation; doc-driven audits false-flag.
Cleanup: align module.yaml networks/exposure text with compose + document loopback-only exception. Churn ~10 lines. Confidence: high.

## AI-0072 [MEDIUM] [fake-env-requires/status-page]
Files: core/modules/status-page/module.yaml:54-55 (env_requires PLATFORM_MASTER_EMAIL/PASSWORD) vs app.py:54-66 (reads neither — Basic Auth creds consumed by nginx htpasswd); STATUS_PAGE_HOST read (:55) but never set by compose base.yml:55-62.
Consequence: secrets manifest demands creds for wrong module; dead knob suggests configurability that never varies.
Cleanup: move env_requires to nginx module or drop; remove STATUS_PAGE_HOST read. Churn ~10 lines. Confidence: high.

## AI-0073 [MEDIUM] [duplication/boto3-builders ×3]
Files: modules/backup-cron upload.py:203-204 (30/60s) vs wal_sync.py:222 (10/30s + retries max_attempts=3) vs retention.py:75-76,443-446 (_BOTO_*=30/60) — while s3_client.py:11-12 claims single construction site
Consequence: RPO-critical hourly WAL sync runs 3× tighter timeouts and different retry mode than dump upload; triple drift-prone.
Minimal cleanup: route all three through s3_client builder with explicit per-call overrides. Churn ~25 lines. Pre-launch: yes (backup reliability). Confidence: high.

## AI-0074 [LOW] [fake-scenario-knob/loadtest]
Files: loadtest runner_cli.py:451 emits LT_METHOD per scenario vs zero readers (web.py hardcodes GET; llm/llm_stream/langfuse_ingest hardcode POST)
Cleanup: drop emission or implement method dispatch. Churn <10 lines. Confidence: high (grep-verified).

## AI-0075 [LOW→security-doc] [false-security-invariant]
Files: modules/platform-secrets/module.yaml:4,10 («tmpfs, never touches disk decrypted») vs platform-secrets.service:19 Environment=SECRETS_ENV_FILE=/var/lib/platform/run/secrets.env (persistent since 142-W2, deliberately); only temp-key is tmpfs (/dev/shm)
Consequence: security invariant in module contract materially false — decrypted secrets persist across reboots. Compliance/runbook decisions based on contract are wrong.
Cleanup: fix module.yaml wording (persistent-by-design, 0600, outside payload/git). Churn <10 lines. Pre-launch: yes (doc honesty). Confidence: high on facts.

## AI-0076 [LOW] [facade-divergence/PYTHONPATH-fix-not-propagated]
Files: scaffold add-vhost.sh:26-27 (TRAP[BUG] P1 PYTHONPATH fix) vs project-list.sh:11, remove-project.sh:10 (no fix)
Consequence: documented P1 ModuleNotFoundError mode unfixed in sibling facades; safe only because Make always runs from repo root.
Cleanup: apply same two-line export to siblings or centralize launcher. Churn <10 lines. Confidence: high divergence / med impact.

# Clean areas: check_suite resolution guarantees; status-page collectors vs MetricsData schema consistent; hermes/backup/postgres healthcheck shims follow canonical deep-superset pattern.
