# Direction 1: error handling — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit

---

## BUG-0100 — Pull failure on EXISTING deploy escalates to "first deploy" FATAL (exit 10) instead of FAILED result

- Severity: HIGH
- Confidence: 85%
- File: core/internal/deploy/engine/engine.py:218
- Symbol: `DeployEngine.deploy` → `handle_first_deploy`
- Trigger: routine update deploy of a project that already runs (previous image exists); registry transient (GHCR 429/DNS) survives all 5 pull attempts (~2 min window, flow.py:54-61).
- Execution path: CI push → `ReceiveFlow.deploy` (receive_flow.py:472) → `DeployOrchestrator._apply_deploy` (orchestrator.py:492) → `_deploy_compose` → `DeployEngine.deploy` (engine.py:218) → `pull_images` returns False → `handle_first_deploy(project, service, ref, "Pull failed after 5 attempts")` (engine.py:219) → `PlatformFatalError("First deploy failed — no rollback possible")` (first_deploy.py:55-56) → caught as generic engine failure at orchestrator.py:1071-1076 → `False` misrouted into the "compose up failed" rollback branch.
- Code:
  ```python
  if not pull_images(project_dir, service, ref):
      handle_first_deploy(project, service, ref, "Pull failed after 5 attempts")
      # unreachable — handle_first_deploy raises PlatformFatalError
  ```
- Actual behavior: pull failure is unconditionally classified as first-deploy-fatal regardless of `is_first_deploy` (computed at engine.py:207-208 and correctly branched in the up-fail path at engine.py:223-225, proving intent). Log says "no previous image to rollback" while `previous_image` exists; direct `DeployEngine.deploy` callers (engine/cli.py) get exit 10 "manual intervention".
- Expected behavior: `return ServiceDeployResult(success=False, error_message="Pull failed after 5 attempts")` — old container keeps serving (no `up` was executed), deploy reported FAILED, CI retry possible without "manual intervention" semantics.
- Impact: every transient registry outage during a production update is diagnosed as an unrecoverable first-deploy failure; audit/CI signal corrupted; operator misled during incident.
- Minimal fix: `if not pull_images(...): if is_first_deploy: handle_first_deploy(...); else: return ServiceDeployResult(success=False, ..., error_message="Pull failed after 5 attempts")`.
- Required regression test: `test_deploy_pull_failure_existing_service_returns_failed_not_fatal` — fake `pull_images`→False with pre-existing image; assert returned `ServiceDeployResult.success is False`, `pytest.raises(PlatformFatalError)` NOT raised, and old container untouched.

## BUG-0101 — Orchestrated rollback re-pulls a local-only tag from the registry; ROLLED_BACK is unreachable, every rollback reported FAILED

- Severity: HIGH
- Confidence: 75%
- File: core/internal/deploy/orchestrator.py:1155
- Symbol: `DeployOrchestrator._rollback_compose`
- Trigger: any deploy of an existing project where compose up fails or healthcheck fails (the exact scenario rollback exists for), with a DeployHistory snapshot present (created on every `_verify_deploy`, orchestrator.py:549-554).
- Execution path: `_apply_deploy` sees `compose_ok=False` (orchestrator.py:493) → snapshot exists → `_rollback_deploy` (orchestrator.py:504) → `_rollback_compose` (orchestrator.py:613-614) → `engine.deploy(ref="previous-rollback")` (orchestrator.py:1155-1160) → `pull_images(project_dir, service, "previous-rollback")` (engine.py:218) → `retry_pull` sets `IMAGE_TAG=previous-rollback` (flow.py:60) → `docker compose pull` resolves `ghcr.io/<org>/<proj>:previous-rollback` (template: `image: "${IMAGE_REGISTRY:-ghcr.io}/{{ORG}}/{{PROJ}}:${IMAGE_TAG:-latest}"`) → manifest unknown ×5 attempts with backoff [5,10,20,40,60] ≈ 2.5 min (docker_compose.py:658-665) → `handle_first_deploy` → `PlatformFatalError` → caught orchestrator.py:1161-1164 → `return False` → `DeployStatus.FAILED` (orchestrator.py:624).
- Actual behavior: `previous-rollback` is a LOCAL-only tag (`docker_ops.docker_tag` at orchestrator.py:1153; lifecycle.py:67-68), never pushed to the registry, yet rollback re-enters the full deploy pipeline whose first step is a registry pull of exactly that tag. Pull is guaranteed to fail; `rollback_ok=False`; audit logs `FAILED` and result is `DeployStatus.FAILED` even when the engine-level `perform_rollback` (lifecycle.py:86-113, no pull) already restored the old container successfully.
- Expected behavior: rollback compose step must not pull (e.g. skip `pull_images` for the `previous-rollback` ref, or `docker compose up -d --force-recreate` with `IMAGE_TAG` directly); successful rollback → `ROLLED_BACK`.
- Impact: the orchestrator-level healthcheck-rollback contract (root AGENTS.md "healthcheck rollback") can never report success; ~2.5 min doomed pull-retries per failed deploy; CI red + audit `FAILED` while the service is actually healthy on the previous version.
- Minimal fix: in `_rollback_compose`, bypass the pull phase — call `perform_rollback(project_dir, service, ImageInfo(id=str(prev_image_id), tag=f"{service}:previous-rollback"))` (or add `skip_pull` flag to `DeployEngine.deploy`).
- Required regression test: `test_rollback_compose_does_not_pull_local_only_tag` — fake runner asserting no `docker compose pull` invocation when ref=`previous-rollback`; assert `_rollback_deploy` returns `DeployStatus.ROLLED_BACK` on successful re-up.

## BUG-0102 — φ4 secrets: corrupt secrets.env passes silently (source+autogen failures swallowed), phase marked done → skipped forever

- Severity: HIGH
- Confidence: 70%
- File: core/internal/bootstrap/lifecycle/helpers/secrets.py:122
- Symbol: `ensure_secrets_exist`
- Trigger: `secrets.env` exists but is unparseable (ValueError in parser, partial write, manual edit on node) at φ4 `secrets_provision` (or φ9 update).
- Execution path: φ4 → `decrypt_secrets` OK (writes secrets.env) → `ensure_secrets_exist` → Step 1 file-exists check passes (helpers/secrets.py:102) → `source_secrets_env` catches `(OSError, ValueError)` and returns `{}` (secrets_manager.py:199-201) → broad `except Exception ... logger.warning` swallows the empty-source condition (helpers/secrets.py:122-123) → autogen failures also swallowed (helpers/secrets.py:134-135) → `_run_secrets_step` FATAL wrapper never fires (it only catches `PlatformError`/`TimeoutExpired`, phases/secrets.py:67 — nothing propagates) → `phase_secrets_provision` returns True → state.json marks φ4 `done` → idempotent re-run SKIPs φ4 forever.
- Code:
  ```python
  except Exception as e:  # noqa: EXC — non-fatal: secrets source failure is recoverable ...
      logger.warning("[IMP:7][ensure_secrets] Failed to source secrets.env: %s", e)
  ```
- Actual behavior: node proceeds to φ6/φ8 with zero secrets sourced into `os.environ`; docstring claims "Post-check: secrets.env file present (validated by _ensure_secrets_exist)" (phases/secrets.py:107) but no such post-check exists. This is the same failure class as TRAP[BUG] 2026-07-23 P0 ("non_fatal=True swallowed decrypt failures → checkpoint .done created → --resume skipped decrypt forever"), resurfaced one layer deeper.
- Expected behavior: existing-but-unparseable/empty-source `secrets.env` (with enc-file present) must be FATAL (`ConfigParseError` → `PlatformFatalError`), not a WARN; or a post-check asserting ≥1 var sourced.
- Impact: bootstrapped/node-updated node silently runs without GHCR_PULL_TOKEN/TELEGRAM_*/DB creds; modules deploy with missing env; permanent skip masks the fault on every subsequent run.
- Minimal fix: in `ensure_secrets_exist`, treat `env_vars == {}` when `Path(secrets_env).is_file()` as `raise ConfigParseError(...)`; narrow the two broad excepts to `(ImportError, OSError)`.
- Required regression test: `test_ensure_secrets_corrupt_env_is_fatal` — write malformed secrets.env to tmp_path, run `phase_secrets_provision`; assert `PlatformFatalError` raised and state.json does NOT contain `secrets_provision: done`.

## BUG-0103 — Autogen overwrite destroys sops-tier secrets from secrets.env when parse failed (merged = {} + generated)

- Severity: MEDIUM
- Confidence: 60%
- File: core/internal/bootstrap/lifecycle/secrets_manager.py:521
- Symbol: `ensure_secrets` (Step 3.5 atomic overwrite)
- Trigger: same precondition as BUG-0102 — `secrets.env` unparseable at φ4 — plus at least one `tier=generated` secret absent from `os.environ`.
- Execution path: `source_secrets_env` → `{}` (secrets_manager.py:457) → Step 3 loop: every generated secret missing from `os.environ` → regenerated (secrets_manager.py:483-505) → Step 3.5: `merged: dict[str, str] = dict(env_vars)` — i.e. `{}` (secrets_manager.py:521) → `tmp_path.replace(secrets_path)` (secrets_manager.py:537) → file now contains ONLY regenerated keys.
- Code:
  ```python
  merged: dict[str, str] = dict(env_vars)  # copy existing (non-generated + previously generated)
  merged.update(generated_vars)  # add/overwrite newly generated
  ...
  tmp_path.replace(secrets_path)
  ```
- Actual behavior: operator/sops-decrypted entries (GHCR_PULL_TOKEN, TELEGRAM_*, PLATFORM_MASTER_*) are wiped from the node's plaintext env file; regeneration cannot restore them (they are not `gen_command`-able). The TRAP[BUG] 2026-07-25 P1 fix (atomic merge) preserves entries only when the file parsed.
- Expected behavior: refuse to write when `env_vars == {}` but the file exists and is non-empty (parse failed) — abort instead of replace; or merge by re-reading raw lines.
- Impact: irreversible loss of decrypted operator secrets on the node (recoverable only via re-decrypt from sops, which φ4 will not re-run — see BUG-0102 skip); registry auth and notifications break downstream.
- Minimal fix: guard Step 3.5: `if not env_vars and secrets_path.exists() and secrets_path.stat().st_size > 0: raise/abort` before `tmp_path.replace`.
- Required regression test: `test_ensure_secrets_never_overwrites_unparseable_file` — secrets.env with one valid sops var + one malformed line; run `ensure_secrets`; assert file still contains the sops var and no replace occurred.

## BUG-0104 — FileLock degrades to NO-LOCK on any OSError (incl. ENOSPC/EACCES on the node), silently disabling the concurrent-deploy guard — HYPOTHESIS

- Severity: MEDIUM
- Confidence: 55%
- File: core/internal/shared/file_lock.py:176
- Symbol: `FileLock._open_fd` / `FileLock.acquire`
- Trigger: on the VPS (not only dev machine), `/var/lock/platform` (lock parent dir) becomes unwritable — disk full (`OSError: ENOSPC` on `O_CREAT`), chattr/permissions drift, read-only remount.
- Execution path: `DeployOrchestrator.deploy` Step 0 concurrent guard (orchestrator.py:295-297) → `lock.acquire()` → `_open_fd()` catches broad `OSError` → WARN + `return None` (file_lock.py:176-180) → `acquire()` treats None as success ("контракт no-lock", file_lock.py:196-199) → deploy proceeds with the T9.1 double-deploy protection silently OFF.
- Code:
  ```python
  except OSError as e:
      logger.warning("[IMP:7][FileLock][degrade] Cannot open lock file %s (%s) — running WITHOUT lock", ...)
      return None
  ```
- Actual behavior: only `PermissionError` is documented as the dev-machine degrade case; any other `OSError` on the node takes the same silent no-lock path. Two concurrent `receive` deploys of the same project then interleave `docker compose` operations — the exact race T9.1 was built to prevent, with a single WARN line as the only trace.
- Expected behavior: distinguish dev-machine degrade (documented) from node-side failure; on a node (or for non-PermissionError `OSError`) raise instead of degrade.
- Impact: concurrent-deploy corruption window opens precisely when the node is unhealthy (disk full), compounding the incident.
- Minimal fix: split handlers — `PermissionError` → degrade (dev); other `OSError` → `raise FileLockError(...)`.
- Required regression test: `test_file_lock_enospc_raises_not_degrades` — patch `os.open` to raise `OSError(errno.ENOSPC, ...)`; assert `FileLockError` raised and `acquire()` does not return silently.

## BUG-0105 — _step_certs logs "All domains have valid certs — skipping cert orchestration" immediately AFTER running cert orchestration

- Severity: LOW
- Confidence: 95%
- File: core/internal/bootstrap/deploy/context_deployer.py:1016
- Symbol: `_step_certs`
- Trigger: every φ8 `deploy-context` run where ≥1 domain cert is invalid and `do_certs` executes.
- Execution path: invalid domains detected (context_deployer.py:1005-1009) → `do_certs(...)` runs issuance (context_deployer.py:1015) → unconditionally logs `"[IMP:9][_step_certs] Cert orchestration: %d domains"` (context_deployer.py:1016) → then unconditionally logs `"All %d domains have valid certs ... — skipping cert orchestration (D3)"` (context_deployer.py:1017-1020).
- Code:
  ```python
  cert_result = do_certs(domains, issue_cert_script, secrets_env, runner=runner, facts=facts_obj)
  logger.info("[IMP:9][_step_certs] Cert orchestration: %d domains", len(cert_result.domains))
  logger.info(
      "[IMP:9][_step_certs] All %d domains have valid certs (≥30 days, LE) — skipping cert orchestration (D3)",
      len(domains),
  )
  ```
- Actual behavior: contradictory IMP:9 telemetry — the "skipping" line prints on every orchestration run regardless of what happened; per-domain issuance failures inside `do_certs` are not reflected (outer `except (OSError, CalledProcessError)` at context_deployer.py:1021-1022 is warning-only by design).
- Expected behavior: the skip-line prints only when `invalid_domains` is empty; otherwise log per-domain outcomes from `cert_result`.
- Impact: operator/agent diagnosing TLS incidents gets false "all valid" signal in logs (the TRAP[BUG] 2026-07-26 webnames false-diagnosis incident shows how costly TLS misdiagnosis is here).
- Minimal fix: move the "skipping" log into the `if not invalid_domains:` branch (or log `cert_result` per-domain statuses).
- Required regression test: `test_step_certs_skip_log_only_when_no_invalid_domains` — caplog: run `_step_certs` with one invalid domain; assert "skipping cert orchestration" NOT in records while `do_certs` stub was called.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0100 | HIGH | 85% | engine.py:218 — pull failure on existing deploy unconditionally raises "first deploy" FATAL (exit 10) instead of FAILED result |
| BUG-0101 | HIGH | 75% | orchestrator.py:1155 — rollback re-pulls local-only tag `previous-rollback` from registry → guaranteed fatal → ROLLED_BACK unreachable, rollback always reported FAILED |
| BUG-0102 | HIGH | 70% | helpers/secrets.py:122 — corrupt secrets.env silently swallowed in φ4; phase marked done → skipped forever, node runs without secrets |
| BUG-0103 | MEDIUM | 60% | secrets_manager.py:521 — autogen overwrite with `merged={}` destroys sops-tier operator secrets after parse failure |
| BUG-0104 | MEDIUM | 55% | file_lock.py:176 — any OSError (ENOSPC/EACCES on node) degrades FileLock to no-lock, silently disabling concurrent-deploy guard (HYPOTHESIS) |
| BUG-0105 | LOW | 95% | context_deployer.py:1016 — "All domains valid — skipping" IMP:9 log prints after (not instead of) cert orchestration, corrupting TLS diagnostics |
