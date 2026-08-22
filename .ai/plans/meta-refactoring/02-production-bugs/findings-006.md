# Direction 6: partial failures — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit
Scope: receive/deliver path, rollback scope, context deploy loop, cert flow, postgres hook, org-secrets batch. Working tree audited as-is (dirty).

---

## BUG-0601 — Snapshots never store `compose_state`, so orchestrator-level rollback can never re-tag the previous image and always reports FAILED instead of ROLLED_BACK
- Severity: HIGH
- Confidence: 82%
- File: core/internal/deploy/orchestrator.py:549 (writer) / :1147-1153 (reader)
- Symbol: `DeployOrchestrator._verify_deploy` / `DeployOrchestrator._rollback_compose`
- Trigger: any deploy whose compose/health step fails after a previous successful deploy (the normal rollback path).
- Execution path: `receive <project> <sha>` → `_apply_deploy` → `_deploy_compose` returns False → `latest_snapshot` (written by `_verify_deploy` of the PREVIOUS deploy without `compose_state`) → `_rollback_deploy` → `_rollback_compose`: `compose_state.get("previous_image")` is always `None` → `docker_ops.docker_tag(...)` skipped → `engine.deploy(ref="previous-rollback")` → `flow.pull_images` pulls `${service}:previous-rollback`, a tag nothing ever created or pushed (CI pushes sha tags; local tag only created by `save_previous_image` dangling-image fallback) → 5 pull attempts with backoff [5,10,20,40,60] ≈ 135s wasted → nested `perform_rollback` re-ups whatever image currently runs → `result.success=False` → status FAILED, audit FAILED.
- Actual behavior: second-layer rollback can never restore a pinned previous image; burns ~2+ min per failed deploy; result is `FAILED` ("Compose deploy failed, rollback failed") even when the engine layer already restored the old image.
- Expected behavior: snapshot stores `compose_state.previous_image`; `_rollback_compose` re-tags it to `{service}:previous-rollback` and deploys it; ROLLED_BACK reported.
- Impact: rollback scope claim (`@invariants "Rollback restores compose_state from snapshot"`, orchestrator.py:21/:709) is dead code; misleading audit/CI signal + 2 min delay on every failed deploy; in the `up_atomic`-failure sub-case this broken layer is the last recovery attempt.
- Minimal fix: capture `{"previous_image": ...}` before `compose up` (e.g., via `save_previous_image`) and pass `compose_state=` in `_verify_deploy.create_snapshot`.
- Required regression test: `test_rollback_compose_retags_previous_image_from_snapshot` — create snapshot via the real `_verify_deploy` path, fail compose, assert `docker_tag(prev_id, "{service}:previous-rollback")` invoked and status == ROLLED_BACK.

## BUG-0602 — Healthcheck failure yields PARTIAL which counts as success: exit 0, post-deploy chain runs, CI green; no rollback performed despite documented policy
- Severity: HIGH
- Confidence: 85%
- File: core/internal/deploy/orchestrator.py:556 (`PARTIAL`), :151-153 (`is_success`), :250 (docstring invariant); core/internal/deploy/receive_flow.py:559-568
- Symbol: `_verify_deploy`, `OrchestratorDeployResult.is_success`, `ReceiveFlow.run`
- Trigger: engine health gate passes (docker inspect healthy) but container crash-loops/restarts before/during the orchestrator poll → `poll_until_healthy` returns timeout/unhealthy.
- Execution path: compose up OK → engine `wait_health` True → orchestrator `poll_until_healthy` unhealthy → `result_status = PARTIAL` → no rollback branch exists → `is_success()` True → `run()` prints JSON PARTIAL, **returns 0** → `.github/workflows/deploy-project.yml:362` treats SSH exit as deploy result → CI step green → `_run_post_deploy_chain` sends Telegram "🚀 Deployed … PARTIAL" (info).
- Actual behavior: degraded/unhealthy new version keeps serving behind nginx; deploy reported as success everywhere; docstring invariant "Rollback on healthcheck failure (if previous snapshot exists)" (orchestrator.py:250) is not implemented anywhere (engine rolls back only its own inspect-gate failures).
- Expected behavior: healthcheck failure at orchestrator level triggers image rollback (or at minimum non-zero exit / FAILED so CI blocks).
- Impact: user-visible outage window with green CI; contradicts root AGENTS.md «healthcheck rollback» policy.
- Minimal fix: on `healthcheck_status != "healthy"` invoke `_rollback_deploy` (snapshot exists from prior deploy) or map PARTIAL → exit 1 in `ReceiveFlow.run`.
- Required regression test: `test_receive_partial_healthcheck_returns_nonzero_and_rolls_back` — fake poller returning unhealthy after compose success; assert exit != 0 and `_rollback_deploy` called.

## BUG-0603 — `subprocess.TimeoutExpired` uncaught in deploy_context steps and LLM pipeline: crash after projects are deployed skips vhost render, nginx reload and verify
- Severity: MEDIUM
- Confidence: 88%
- File: core/internal/bootstrap/deploy/context_deployer.py:1093, :1117, :1173; core/internal/bootstrap/deploy/llm_provision.py:111, :118
- Symbol: `_step_vhosts`, `_step_nginx_reload`, `_step_verify`, `render_and_provision_llm`
- Trigger: any child command hangs past its timeout in production path (`runner=None` → raw `subprocess.run(timeout=…)`; `llm_provision` has no runner at all). `subprocess.TimeoutExpired` subclasses `SubprocessError`, not `CalledProcessError`/`OSError` — none of the five except-lists contain it (shared `run_subprocess` converts it to rc=124 only for the runner channel, context_deployer.py:435 is the sole place that catches it).
- Execution path: `deploy_context` → `_step_deploy_projects` → all N projects deployed → `deploy_context_projects:515` calls `_render_and_provision_llm()` → e.g. `provision-llm.sh` hangs > SYSTEM_CMD_TIMEOUT → `TimeoutExpired` escapes both try-blocks → escapes `_step_deploy_projects` and `deploy_context` (no try around steps) → `main()` crashes with traceback; `_step_vhosts`/`_step_nginx_reload`/`_step_verify` never run; same hole if vhost script itself hangs >60s.
- Residual state: projects deployed and healthy, certs issued, but vhosts not rendered and nginx not reloaded → sites 404/502 until next run; bootstrap φ8 marks phase failed although deploys succeeded; JSON `ContextDeployResult` summary never printed.
- Expected behavior: per invariant «All sub-steps are non-fatal» (context_deployer.py:890) each step degrades to WARN.
- Impact: partial deploy surfaced as raw crash; silent unreachable sites after every slow-command incident.
- Minimal fix: add `subprocess.TimeoutExpired` (or `subprocess.SubprocessError`) to the three step-level except lists and to `render_and_provision_llm`'s two handlers.
- Required regression test: `test_step_vhosts_timeout_is_non_fatal` — fake runner raising `TimeoutExpired`; assert WARN logged, subsequent steps executed, `deploy_context` returns normally.

## BUG-0604 — Postgres DB-provisioning hook is unreachable: role/db/GRANT auto-provisioning promised by the platform contract never executes during deploy
- Severity: CRITICAL
- Confidence: 90%
- File: core/modules/postgres/module.yaml:35-37 (no `hooks:` section); core/internal/deploy/hooks/post_deploy_chain.py:204-214; core/modules/postgres/hooks/on_project_deploy.py:485
- Symbol: `on_project_deploy.main` (zero production callers); `_module_deploy_hooks`
- Trigger: every project deploy (`git push` → CI → `receive`, or `make deploy-project`) of a project with `needs.database`.
- Execution path: receive success → `run_post_deploy_chain` → `_module_deploy_hooks` iterates `core/modules/*/module.yaml` and invokes `deploy-hook` only for modules registering `hooks.on_project_deploy` — only nginx does (nginx/module.yaml:47-49). postgres/module.yaml deliberately omits it («on_project_deploy.py — каноническая Python-реализация (operator-инвокация)») and tests/gates/test_gate_module_hooks.py:342-344 asserts `"postgres" not in registered`. Grep across core/, entrypoints/, makefiles/, .github/workflows/ finds no caller of `postgres.hooks.on_project_deploy`/`auto_create_db`.
- Residual state: database declared in `ai-platform.yaml#needs.database` is never created; role `${project}_user`/GRANTs/`.platform-db.env` never provisioned; `gen_env_platform.py:217` then substitutes a `${NAME}_user` DSN pointing at a nonexistent role.
- User-visible failure: app containers get Postgres auth errors («role does not exist» / DB missing) on first deploy despite root AGENTS.md contract «роль/БД/GRANT создаются хук-ом postgres при деплое» (DO NOT #7). The e2e test invokes `main()` directly, masking the gap.
- Expected behavior: hook invoked automatically post-deploy (registered in module.yaml like nginx) or an explicit Python call in the post-deploy chain.
- Impact: every DB-backed project deploy requires manual SQL intervention; platform contract silently broken.
- Minimal fix: register `hooks.on_project_deploy: hooks/on_project_deploy.py` in postgres/module.yaml (and update test_gate_module_hooks expectation) or invoke `auto_create_db` from `post_deploy_chain`.
- Required regression test: integration test asserting that after a full `receive` of a stub project with `needs.database`, `SELECT 1 FROM pg_roles WHERE rolname='x_user'` returns a row (currently impossible through the pipeline).

## BUG-0605 — Orphan DB role with permanently lost password: CREATE ROLE succeeds, credentials write fails → every retry early-returns success without GRANTs or credentials
- Severity: HIGH
- Confidence: 85%
- File: core/modules/postgres/hooks/on_project_deploy.py:250-271 (create/write), :237-248 (retry path)
- Symbol: `ensure_project_db_access`
- Trigger: first provisioning run where `CREATE ROLE` commits but `_write_credentials` fails (ENOSPC, EACCES on project dir, crash between steps).
- Execution path: run 1: `password = secrets.token_urlsafe(24)` → `CREATE ROLE "${project}_user" LOGIN PASSWORD '…'` succeeds (only copy of password is in memory) → `_write_credentials` OSError → log + `return 0` (hook exit 0, deploy success). Run 2..N: `role_exists=True` → `_read_credentials` empty → `logger.warning("credentials NOT refreshed")` → **`return 0` before the GRANT block** → CONNECT/SCHEMA grants never applied, credentials never written.
- Residual state: orphan role with unknown password in Postgres; project DB inaccessible; hook reports success on every rerun; only manual `DROP ROLE` heals (password cannot be recovered because idempotency forbids regeneration, line 14-15).
- Expected behavior: failed credential persistence should either roll back the just-created role (`DROP ROLE`) or set a known password from the creds file on retry.
- Impact: permanently broken DB access with green deploys — worst kind of partial failure (silent, unrecoverable by redeploy).
- Secondary defect in same function: GRANT results are unchecked (`_psql("-c", f'GRANT …')` lines 264-265 ignore output/errors entirely).
- Minimal fix: if `created and not _write_credentials(...)` → best-effort `DROP ROLE` before return; in the `role_exists and not password` branch still execute the GRANTs.
- Required regression test: `test_role_created_without_credentials_does_not_block_retry` — fake runner: CREATE ROLE ok, creds write raises OSError; assert role dropped (or grants applied on second run) and rc signals failure.

## BUG-0606 — ACME order fulfilled but install-cert failed → domain conflated with issue failure → self-signed fallback overwrites live cert material and masks success accounting
- Severity: MEDIUM
- Confidence: 75%
- File: core/internal/bootstrap/issue_cert.py:574-597 (`_install_cert_files` rc≠0 → False); core/internal/bootstrap/cert_orchestrator.py:486-497 (failed → self-signed), :891-899 (unconditional overwrite)
- Symbol: `_install_cert_files`, `_process_single_domain`, `_generate_self_signed`
- Trigger: acme.sh `--issue` succeeds; `--install-cert` fails afterwards — reloadcmd (`systemctl reload nginx && python3 s3_ssl_cache.py upload`) exits non-zero (nginx config broken/down) or exceeds INSTALL_CERT_TIMEOUT=60s.
- Execution path: DNS-01 validated, LE order consumed, cert stored under acme home → install rc≠0 → `issue_tls_cert` False → `_process_single_domain` logs «issue_cert failed, trying self-signed fallback» → `_generate_self_signed` runs `openssl req -x509 -out cert_path` over `/etc/letsencrypt/live/<domain>/fullchain.pem` + `privkey.pem` (whatever install-cert managed to write is clobbered) → returns `DomainCertResult(status="issued", source="self_signed")` → counted in `CertResult.issued`, not `failed`; `_finalize_orchestration` installs renewal cron off this count.
- Residual state: real LE certificate buried in `/opt/acme.sh/<domain>_ecc/`, live path holds self-signed bytes; next bootstrap sees non-LE cert → re-issue → duplicate-certificate rate-limit risk; monitoring shows «issued» instead of alarm-worthy self_signed downgrade path being taken after a *successful* order.
- Expected behavior: distinguish install-failure from issue-failure (retry install, keep issued cert, do not overwrite with self-signed when an LE cert was obtained).
- Impact: burned LE rate-limit budget + browsers warn + false success metrics.
- Minimal fix: in `_process_single_domain`, if `_issue_cert` result indicates issuance ok/install failed (plumb sub-status), skip self-signed; make `_generate_self_signed` refuse to overwrite an existing LE-issued file.
- Required regression test: `test_install_failure_after_issue_keeps_le_cert` — fake runner: --issue rc=0, --install-cert rc=2; assert openssl self-signed NOT written over existing live files and status != issued/self_signed.

## BUG-0607 — Batch org-secret provisioning loses per-secret outcomes: single bool returned, failures logged below default visibility, no reconciliation of what landed
- Severity: LOW
- Confidence: 80%
- File: core/internal/deploy/org_secrets_provisioner.py:265-271 (loop collapses to one bool), :213/:218 (`logger.info("[IMP:10]…")`), core/internal/deploy/context_promoter.py:309-327 (caller consumes bool only)
- Symbol: `ensure_context_secrets`, `_set_one_secret`, `promote_context`
- Trigger: `make context-promote` where some `gh secret set` calls succeed and a later one fails (token scopes, network, selected-repo not yet created).
- Execution path: secrets 1..j-1 uploaded to gh org → secret j fails (`rc!=0` → `return False`) → loop continues j+1..n (later secrets still attempted) → `ok=False` → promoter logs SUCCESS-with-WARN and audits DONE; nothing records WHICH secrets landed, and module invariant №1 claims «audit FAIL-запись», but the module writes no audit record at all; failure diagnostics use `logger.info("[IMP:10]…")` — invisible unless INFO logging is enabled.
- Residual state: partially configured org; mirror CI may fail later with empty VPS_SSH_KEY/AGE_SECRET_KEY while promote reported DONE; operator must diff gh org secrets manually.
- Expected behavior: structured per-secret result (names of uploaded/failed), WARN-or-above logging, audit entry listing gaps.
- Impact: hard-to-diagnose broken CI in fresh context orgs; low frequency, high confusion cost.
- Minimal fix: return `(uploaded: list[str], failed: list[str])` (or write audit entry per secret), log failures at WARNING.
- Required regression test: `test_ensure_context_secrets_reports_failed_subset` — run_fn failing only for `VPS_SSH_KEY`; assert failed list contains exactly that name and others reported uploaded.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0601 | HIGH | 82% | Snapshots never store compose_state → rollback can't re-tag previous image, pulls nonexistent tag ~135s, reports FAILED instead of ROLLED_BACK |
| BUG-0602 | HIGH | 85% | Healthcheck failure → PARTIAL → exit 0/CI green/post-deploy chain, no rollback despite documented policy |
| BUG-0603 | MEDIUM | 88% | TimeoutExpired uncaught in deploy_context steps + llm_provision → crash after projects deployed, vhosts/nginx-reload/verify skipped |
| BUG-0604 | CRITICAL | 90% | Postgres DB-provisioning hook has zero production callers (unregistered, gate-enforced) — needs.database contract never executes |
| BUG-0605 | HIGH | 85% | CREATE ROLE ok + creds write fail = orphan role with lost password; retries early-return success forever |
| BUG-0606 | MEDIUM | 75% | Install-cert failure after successful ACME issue → self-signed overwrites live cert, masks failure, burns LE rate-limit |
| BUG-0607 | LOW | 80% | Org-secrets batch reduces partial uploads to one bool; failures logged at INFO, no reconciliation of landed secrets |
