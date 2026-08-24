# Findings 02 — Authorization & access control

## SEC-0006 — No credential↔project binding: any CI key holder can `receive`/`remove`/`status` ANY project
- **Severity:** HIGH · **Attack surface:** forced-command dispatch, all project-scoped verbs · **Confidence:** 0.95 · **Must fix before launch: YES**
- **Files:** `core/internal/shared/verbs.py:29-36`; `deploy/orchestrator_cli.py:457-467,556-564`; `deploy/receive_flow.py:349-365`; `bootstrap/lifecycle/helpers/users.py:39` (один repo-level deploy key × N проектов); `bootstrap/security/deploy_channel_posture.py:14`
- **Symbols:** `_handle_receive`, `ReceiveFlow.validate`
- **Preconditions:** possession of ANY project's `CI_DEPLOY_KEY` (compromise of one repo/org secret, leaked runner temp file, ex-employee copy).
- **Attack path:** one canonical authorized_keys entry, no per-key environment/restriction → `ssh -i leaked_key ci-deploy@node "receive victim-proj <sha>"` overwrites another tenant's payload with attacker images (L1-passing benign-looking composes); or `"remove victim-proj"` → cross-tenant destruction; or `"status"` recon.
- **Impact:** trust boundary "one leaked secret = every project on the node".
- **Evidence:** dispatch validates name syntax only, never which project the presenting key owns; target_dir resolved with no ownership comparison.
- **Minimal fix:** per-project authorized_keys lines with `environment="PLATFORM_ALLOWED_PROJECT=<name>"` enforced in `_dispatch`/`validate`; or per-repo payload signatures verified against project registry.
- **Regression test:** receive for project B with key bound to A returns FAILED; gate test asserting every authorized_keys line carries the binding.

## SEC-0007 — Shared Redis: no AUTH/ACL, single DB 0 — FLUSHALL/read/poison across tenants
- **Severity:** HIGH (if ≥2 mutually distrusting tenants) · **Attack surface:** shared-cache-net (allowlisted for every project) · **Confidence:** 0.9–0.95 · **Must fix:** NO for single-operator launch; **blocker before external tenants**
- **Files:** `core/modules/redis/docker-compose.base.yml` command block (`--save "" --appendonly no --maxmemory 256mb --allkeys-lfu`; no requirepass/ACL); `platform-infra.yaml:109-114` (`url_template …/0` same DB for all); `practices_manifest.yaml:33-39`
- **Attack path:** any tenant container → `redis-cli -h redis FLUSHALL` (global eviction DoS) or `KEYS *` reads other tenants' cached sessions/tokens or SET forged keys (poisoning).
- **Minimal fix:** `--requirepass ${REDIS_PASSWORD}` from secrets via `.env.platform`; optionally ACL users per project; emit per-project logical DB index.
- **Regression test:** component test asserting unauthenticated `redis-cli ping` fails from a shared-cache-net container; static gate requiring auth in redis base.yml.

## SEC-0008 — Postgres PUBLIC CONNECT never revoked: documented per-project DB isolation doesn't hold at CONNECT layer
- **Severity:** MEDIUM · **Confidence:** 0.85 · **Must fix:** NO today (object grants still protect data) — before mutually distrusting tenants
- **Files:** `core/modules/postgres/config/pg_hba.conf` (`host all all 172.16.0.0/12 md5`); `core/modules/postgres/hooks/on_project_deploy.py:263-266` (hook only ADDS grants; zero REVOKE CONNECT FROM PUBLIC anywhere); canon contradiction with AGENTS.md «CONNECT на свою БД и НИЧЕГО больше»
- **Attack path:** valid own credentials → connect to another project's DB name via pgbouncer wildcard route → catalog/metadata enumeration; widens with any future mis-grant.
- **Minimal fix:** in `ensure_project_db_access`: `REVOKE CONNECT ON DATABASE "<db>" FROM PUBLIC`; align AGENTS.md wording.
- **Regression test:** negative e2e in `tests/e2e/test_shared_db_access.py`: roleA psql -d projB_db → permission denied.

## SEC-0009 — `pull_request_target` checks out PR head and runs PR-authored code with static secrets in job env
- **Severity:** MEDIUM (HIGH if repo ever public/forkable) · **Confidence:** 0.85 · **Must fix:** YES if visibility changes; otherwise NO
- **Files:** `.github/workflows/platform-test.yml:52` (trigger), `:107` (head-sha checkout), `:87,434-435,484-486` (DOCKER_HUB_*, DEEPSEEK_API_KEY, LITELLM_MASTER_KEY, TELEGRAM_* in env)
- **Attack path:** fork PR edits its own Makefile/test to print `${LITELLM_MASTER_KEY}` during `make gate` — guards ("if -z skip") are themselves PR-modifiable; least-privilege GITHUB_TOKEN caps token damage, not static secrets.
- **Minimal fix:** split jobs: unprivileged PR build-job; secrets-consuming jobs gated on `!= 'pull_request_target'` or environment approval for forks.
- **Regression test:** workflow-lint gate forbidding `secrets.*` reachable in runs whose checkout ref is PR head.

## SEC-0010 — Reusable `deploy-project.yml` lacks `permissions:` and interpolates inputs unquoted into shell
- **Severity:** LOW-MEDIUM · **Confidence:** 0.9 · **Must fix:** NO
- **Files:** `.github/workflows/deploy-project.yml` (no permissions block; `:374` raw `inputs.project_name` in run:, runner holds `$RUNNER_TEMP/deploy_key`)
- **Impact:** org-default token scopes for arbitrary caller repos; script injection on runner converts malicious-maintainer into shared-key extraction.
- **Minimal fix:** `permissions: {}`; env-indirect quoted interpolation; validate `inputs.project_name` against `[a-z0-9-]+`.
- **Regression test:** lint gate: every reusable workflow has permissions block; no `${{ inputs.` inside run:.

Checked-and-clean: verb dictionary closed with import-time parity assert (no fallback-to-deploy; platform-wide verbs unreachable via ci-deploy channel); server-side T9.7/H7 name validation + shlex.quote client-side; payload size cap streamed reject-before-extract; SKIP_PREFLIGHT cannot bypass receive L1 gate; root-channel triggers gated on workflow_run success + SHA re-verification; authorized_keys hygiene enforced for ci-deploy (S7, restrict flag); `.platform-db.env` correct 0600 outside payload whitelist; audit trail unified JSON-lines 0640 root:adm; SQL names regex-gated before interpolation; projects registry not deliverable via ci-deploy channel.
