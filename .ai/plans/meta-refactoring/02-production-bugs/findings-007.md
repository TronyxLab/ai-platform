# Direction 7: restart behavior — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit
Scope: bootstrap idempotency (a), post-reboot ordering (b), stale runtime artifacts (c), state-on-disk drift (d), one-shot vs always-restart (e), converge fresh-boot assumptions (f).

---

## BUG-0700 — Restore-first SSL treats a partial S3 cache (fullchain without valid privkey) as a successful restore → nginx TLS broken after re-bootstrap/recovery

- Severity: HIGH
- Confidence: 90%
- File: core/internal/bootstrap/s3_ssl_cache.py:564-575 (and cert_orchestrator.py:539-549)
- Symbol: `download_cert` / `_plw_body__try_s3_restore` / `_process_single_domain`
- Trigger: node re-bootstrap (DR/recreate, invariant 9 "тестовый сервер пересоздаётся заново") or φ7/φ12 ssl-provision after the on-disk `/etc/letsencrypt/live/<domain>/` was lost, while the S3 cache holds fullchain.pem without a matching privkey.pem. This state arises because `upload_cert` uploads files sequentially and is non-fatal per file (s3_ssl_cache.py:392-396): a crash or transient S3 error between the fullchain upload and the privkey upload — including in the acme.sh renewal cron `--renew-hook` path — leaves a partial (or key/cert-mismatched) pair in S3.
- Execution path: reboot/rerun (φ7 certificates or φ12 deploy_update) → `_process_single_domain` step 1 checks only `fullchain.pem` validity (`cert_is_valid` validates parse+LE+domain+expiry of the CERT ONLY — shared/ssl_certs.py:364-394, never the key) → step 2 `_try_s3_restore`: `check_cert` downloads/validates fullchain only; `download_cert` restores fullchain but treats missing privkey as tolerable and still returns True:
  ```python
  if _download_s3_file(f"{s3_base}/privkey.pem", tmp_privkey_path):
      ...
      logger.info("[IMP:9]...privkey.pem restored...")
  logger.warning("[IMP:8]...privkey.pem not in S3 for %s — proceeding without it", domain)
  ```
  (the warning fires even on success — line 570 sits outside the `if`) → `download_cert` returns True (line 605) → `_plw_body__try_s3_restore` marks the domain `status="restored"` after checking only `os.path.isfile(live/<domain>/fullchain.pem)` (cert_orchestrator.py:539-542) → issue_cert fallback never runs → nginx starts with vhosts referencing `ssl_certificate_key /etc/letsencrypt/live/${PLATFORM_DOMAIN}/privkey.pem` (core/modules/nginx/config/platform-default.conf.template:50,79; ssl-params.conf.template:19) → file missing or key mismatch ("SSL_CTX_use_PrivateKey: key values mismatch" for the stale-key variant) → `nginx -t` fails / ingress crash-loops → ALL sites down.
- Actual behavior: domain reported `restored`, bootstrap continues green; nginx serves no TLS or fails to start.
- Expected behavior: a restore without a validated private key (present + matching the certificate) must be treated as a cache miss → fall through to issue_cert/self-signed fallback.
- Impact: total TLS outage of the node's single ingress exactly in the disaster-recovery scenario the S3 cache exists for; failure surfaces only at nginx start, far from the root cause.
- Minimal fix: in `download_cert`, require privkey.pem (return False if absent); add an openssl key↔cert modulus/public-key match check before declaring success; in `_plw_body__try_s3_restore`, verify both files exist before returning `"restored"`.
- Required regression test: `test_download_cert_missing_privkey_is_cache_miss` — S3 fake with fullchain only; assert `download_cert(...) is False` and `cert_orchestrator._try_s3_restore(...).status == "pending"` (not "restored"); plus `test_upload_failure_between_files_leaves_no_false_restore`.

## BUG-0701 — Converge R9 self-heal invokes isolated module compose without profile/env-file/root-compose → self-heal provably fails for every docker module (live-reproduced)

- Severity: CRITICAL
- Confidence: 95%
- File: core/internal/bootstrap/converge/runtime.py:318-322
- Symbol: `reconcile_runtime_state` (R9)
- Trigger: any container in BAD_DOCKER_STATES (exited/restarting/dead/unhealthy/paused) after VPS reboot or crash, then `make converge` (manual or bootstrap phases φ8.5/φ13 via converge.sh).
- Execution path: reboot/rerun → R9 detects bad container (runtime.py:279-299) → self-heal call:
  ```python
  _shared_docker_compose_up(
      str(compose_file.parent),
      timeout=COMPOSE_UP_TIMEOUT,
      compose_args=["-f", str(compose_file)],
  )
  ```
  → compose runs with ONLY the module's base.yml. Three independently reproducible failure modes (verified live against this working tree):
  1. No `--profile <module>` → services with `profiles:` are not selected → `docker compose -f core/modules/redis/docker-compose.base.yml up -d` prints `no service selected`, rc=1 (executed locally; rc=0 silent no-op on older compose versions — false "healed successfully" there);
  2. No `--env-file secrets.env` → `${POSTGRES_PASSWORD:?...}` interpolation error: `error while interpolating services.backup-cron.environment.POSTGRES_PASSWORD: required variable POSTGRES_PASSWORD is missing a value` (reproduced);
  3. Even with env satisfied, module base.yml intentionally declares NO top-level volumes (root compose is the volumes SoT — docker-compose.yml:12-16, gate test_gate_volumes_sot) → `service "backup-cron" refers to undefined volume backup-spool: invalid compose project` (reproduced with `--profile backup-cron config`).
  → `_shared_docker_compose_up` returns False → `report_add(unit,"fail",...)` + `set_exit(2)` (runtime.py:330-333) → converge exits 2 (drift) on every run; φ8.5/φ13 return False → phase permanently `done_with_warnings`.
- Actual behavior: dead module is never restored; converge reports fail/drift forever; watchdog cron (`docker restart`) is the only remaining healer.
- Expected behavior: heal via the canonical deploy invocation — `build_compose_args` (root-compose-first + `--profile` + `--env-file`), which fixed this exact class of bug for the deploy path (TRAP[BUG] RC 121/U-49 in deploy/compose_args.py:98-106) and for R7 (converge/volumes.py:193-199 adds `--env-file` + `--profile` with TRAP[BUG] 141 B18). R9 was missed by both fixes.
- Impact: post-reboot/converge self-heal — the mechanism φ8.5/φ13 rely on — is non-functional platform-wide; a reboot that leaves any service exited degrades the node with no automated recovery and permanent exit-2 noise masking real drift.
- Minimal fix: in runtime.py replace raw `-f <module>` args with `build_compose_args(module_name, module_dir=...)` (shared root-first + profile + env-file), mirroring volumes.py:193-199.
- Required regression test: `test_r9_self_heal_compose_args_select_service_and_resolve` — for each module dir assert `docker compose config` with R9's exact argv succeeds AND yields ≥1 service (catches all three modes: no-service-selected, interpolation, undefined volume).

## BUG-0702 — R9 resolves containers by substring name filter: whole modules invisible (monitoring/logging/infra-metrics), sibling containers cross-matched (redis)

- Severity: HIGH
- Confidence: 90%
- File: core/internal/bootstrap/converge/runtime.py:55-72
- Symbol: `resolve_container_name`
- Trigger: same as BUG-0701 (post-reboot converge / φ8.5/φ13), detection stage.
- Execution path: rerun → `docker ps -a --filter name=<module>` uses docker's substring name match (verified live):
  - `--filter name=monitoring` → **0 rows** though prometheus/grafana/prometheus-config-init are its containers → "No running containers for module monitoring — skip" → a dead Prometheus/Grafana is NEVER detected nor healed by R9; same for `logging` (loki/alloy) and `infra-metrics` (cadvisor/node-exporter/*-exporter);
  - `--filter name=redis` → matches `redis`, `redis-exporter`, and `langfuse-redis` → a restarting `langfuse-redis` (normal during boot) flags module `redis` as needing heal, triggering the broken BUG-0701 heal path and polluting cooldown;
  - `--filter name=minio` → matches `minio-createbuckets` (only the RestartPolicy="no" guard from 142 B28a prevents a spurious heal here).
- Actual behavior: detection coverage map diverges from real container names; false negatives for observability modules, false positives across modules.
- Expected behavior: resolve containers via compose project labels (`docker ps --filter label=com.docker.compose.project=<module>`) or per-service names from the resolved compose config — not substring on module name.
- Impact: after reboot, monitoring/logging stack failures go unnoticed by converge while unrelated modules get spuriously healed/blocked; combined with BUG-0701 the entire R9 unit provides neither coverage nor healing.
- Minimal fix: switch filter to `label=com.docker.compose.project=<module>` (label already relied on elsewhere: tests `_conftest` `check_foreign_containers()`).
- Required regression test: `test_resolve_container_name_uses_compose_project_label` — fake `docker_ps` asserting the filter string equals `com.docker.compose.project=monitoring` and returns prometheus/grafana; negative: langfuse-redis must not appear for module redis.

## BUG-0703 — `.hc_done_in_deploy` marker is set unconditionally even when deploy groups failed → standalone healthcheck silently skipped on update

- Severity: MEDIUM
- Confidence: 75%
- File: core/internal/bootstrap/deploy/deploy_orchestrator.py:553-554 (writer), lifecycle/phases/docker.py:600-610 (consumer)
- Symbol: `_deploy_parallel` / `_set_hc_marker`; `_registry_step_healthcheck`
- Trigger: `node-update` (φ11→φ12) where `_deploy_parallel` finished with `failed` non-empty or a group raised mid-way (best-effort except at deploy_orchestrator.py:541-542), then φ11 healthcheck step of the same or next run reads the stale marker.
- Execution path: rerun → groups deploy partially fails (healthchecks inside failed groups never ran) → step 6 `_set_hc_marker()` executes unconditionally ("HC_DONE_MARKER always set — DEPLOY_BEST_EFFORT policy", :928-938) → later `_registry_step_healthcheck` sees marker:
  ```python
  if isfile_impl(hc_done_marker):
      logger.info("[IMP:9][phase:registry_update] Healthcheck already done during deploy ... skipping")
      ...
      os.unlink(hc_done_marker)
      return False
  ```
  → the ONLY deep standalone healthcheck is skipped although no health verification actually happened for the failed modules.
- Actual behavior: update ends with unhealthy-but-running services unreported by healthcheck; only liveness/severity counters hint at failure.
- Expected behavior: set the marker only when group deployment had zero failures (or have the consumer verify deploy severity), so φ11 falls back to running standalone healthchecks.
- Impact: post-update degradation window extends until external alerting notices; violates the marker's documented meaning ("healthcheck уже выполнен внутри deploy_docker_group").
- Minimal fix: `if not failed: _set_hc_marker()`.
- Required regression test: `test_hc_marker_not_set_when_group_failed` — fake `deploy_docker_group` returning failed_names → assert marker file absent and `_registry_step_healthcheck` runs the injected healthcheck fn.

## BUG-0704 — Reboot-postpone Telegram alert marked as sent even when notification delivery fails

- Severity: LOW
- Confidence: 85%
- File: core/internal/bootstrap/reboot_policy.py:359-369
- Symbol: `check` (postpone branch)
- Trigger: daily 04:30 timer fires while `/var/run/reboot-required` exists and a platform user has an active SSH session; Telegram delivery fails (network/API outage) — precisely the conditions accompanying a degraded host.
- Execution path: systemd timer rerun → `should_notify_postpone(...)` True → `notify_telegram(...)` return value ignored:
  ```python
  notify_telegram(
      f"[platform] Ребут отложен: активная SSH-сессия ({users})...",
      ...)
  state["content_hash"] = current_hash
  state["postpone_notified_at"] = today
  save_state(state, state_file)
  ```
  → state records "notified today" despite failure → anti-spam gate suppresses retry until the next UTC day or a change of reboot-required content.
- Actual behavior: operator misses the "reboot postponed" signal for up to 24h during an incident-shaped window.
- Expected behavior: persist `postpone_notified_at` only when `notify_telegram` returned True (retry on next 5-min…—here next-day tick, or better: retry next timer run).
- Impact: lost observability of deferred security reboots; low because the timer retries daily anyway.
- Minimal fix: `if notify_telegram(...): state.update(...); save_state(...)`.
- Required regression test: `test_postpone_state_not_saved_when_notify_fails` — `notify_fn=lambda _: False` → assert `save_state` not called / state file unchanged.

---

### Verified non-findings (audited, guards present)

- AGE master key: no generation/regeneration anywhere in bootstrap (only operator-supplied env/file chain, `node_detect.py`; grep `age-keygen` → docs only); φ4 persist block removed.
- crontab mutations: metrics/watchdog/prune crons use content-match atomic writes (`lifecycle/helpers/system.py:_write_content_if_changed`); acme cron gated on `s3_ssl_cache` presence in `crontab -l` (`cron_installer.py:70,138`).
- ufw/firewall: incremental apply, explicit stale-rule reconcile, never `ufw reset` (firewall.py build_rules/collect_stale_platform_rules).
- authorized_keys: duplicate-check + forced-command reconcile T9.18 (`helpers/users.py:add_ssh_key`).
- daemon.json: merge-not-overwrite in both writers (docker_installer.configure_daemon, docker_registry_auth._write_daemon_json "Preserves existing keys").
- state.json: corrupt → fatal (not silent fresh); node-switch reset handled (state_machine.setup_state TRAP 2026-08-03); flock + unique tmp saves.
- One-shots: `prometheus-config-init`/`minio-createbuckets` are idempotent (sed/cp -f, mc mb --ignore-existing) and excluded from R9/watchdog healing via RestartPolicy="no" guards.
- SSH facade: no ControlMaster sockets (no stale socket cleanup needed); every call timeout-wrapped.

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0700 | HIGH | 90% | S3 SSL restore-first принимает полный chain без валидного privkey за «restored» → nginx без ключа падает после re-bootstrap |
| BUG-0701 | CRITICAL | 95% | R9 self-heal зовёт compose без --profile/--env-file/root-compose → «no service selected»/interpolation/undefined-volume — self-heal сломан для всех модулей |
| BUG-0702 | HIGH | 90% | R9 ищет контейнеры substring-фильтром по имени модуля: monitoring/logging/infra-metrics невидимы, redis цепляет langfuse-redis/redis-exporter |
| BUG-0703 | MEDIUM | 75% | .hc_done_in_deploy ставится даже при failed-группах → φ11 пропускает единственный глубокий healthcheck |
| BUG-0704 | LOW | 85% | reboot_policy пишет «уведомление отправлено» при упавшем Telegram-канале → алерт о отложенном ребуте теряется на сутки |
