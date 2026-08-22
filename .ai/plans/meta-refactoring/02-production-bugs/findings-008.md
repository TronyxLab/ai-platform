# Direction 8: background jobs — forensic bug hunt
Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit

Scope: backup-cron (cron + upload/retention/wal_sync), healthcheck/watchdog.py, tor_proxy_check,
platform-export-metrics collectors, logging/audit rotation, notification chains, in-process
background work (fork-based parallel deploy runner, streaming subprocess threads).
Working tree audited as-is (dirty).

---

## BUG-0801 — Parallel group deploy: blocking drain counts failed children as deployed (exit codes swallowed)
- Severity: CRITICAL
- Confidence: 95%
- File: core/internal/bootstrap/deploy/parallel_runner.py:498
- Symbol: `drain_all_count` (called from `deploy_docker_group`:338)
- Trigger: `DEPLOY_PARALLEL=true` module deploy where a forked child module-deploy fails while it is
  among the last ≤parallel_limit children reaped by the final blocking drain (e.g. single-module
  topo-group, or failures concentrated in the last batch).
- Execution path: child `deploy_module_fn` fails → child exits 1 (`os._exit(1)`, :329/:332) →
  `drain_all_count` does `os.waitpid(pids[i], 0)` (:500) and unconditionally `deployed += 1`
  (:503, comment «waitpid returned without error means process exited») → `group_failed` stays 0 →
  atomic-rollback branch `if group_failed > 0:` (:354) never fires AND `failed_names` stays empty →
  `_deploy_parallel` aggregates nothing into `failed` (deploy_orchestrator.py:537-540) → severity
  aggregation emits exit 0 on a broken/half-deployed stack.
- Actual behavior: failed module counted as deployed; no `docker compose down` rollback of the group;
  orchestrator reports success.
- Expected behavior: final drain must classify by `os.WIFEXITED/WEXITSTATUS` exactly like the two
  sibling drains (`_drain_pull_slots`:142-145, `drain_completed_count`:467-474).
- Impact: silent failed node deploys/update waves under the parallel path; broken modules left up
  with a green verdict; critical-severity exit code 2 contract defeated.
- Minimal fix: in `drain_all_count`, mirror `drain_completed_count`: on `WIFEXITED &&
  WEXITSTATUS==0` → deployed++, else failed++/failed_names.append(mod_name).
- Required regression test: `test_drain_all_count_counts_nonzero_exit_as_failed` — spawn a child
  that exits 3, call `deploy_docker_group([entry], ..., drain_all_fn=None)` with fake
  `deploy_module_fn`; assert `(group_deployed, group_failed, failed_names) == (0, 1, [mod])`.

## BUG-0802 — Spool cleanup destroys never-uploaded backups after 7 days (no auto-retry exists)
- Severity: HIGH
- Confidence: 85%
- File: core/modules/backup-cron/scripts/backup-cleanup.sh:35
- Symbol: main loop / `find -mtime +7 -print0` → `rm -f`
- Trigger: any S3 outage ≥ one backup window: nightly dump passes gzip/structure verification but
  `upload-s3.sh` exhausts 3×30min retries → dump intentionally kept in spool.
- Execution path: S3 down → `backup_postgres.run_backup` uploads via `_UPLOAD_SCRIPT` (:310),
  gets rc≠0, logs «dump retained in spool (%s) for manual retry» (:312-317), exits 0 → no code
  anywhere ever rescans the spool for re-upload (grep: only `upload.py <file> <key>` per-run calls;
  crontab has no retry job) → next day `backup-cleanup.sh` (cron 04:00, crontab:34) deletes every
  spool file older than 7d regardless of upload status (`find ... -mtime +7 ... rm -f`, :31-35).
- Actual behavior: off-site copy never created; local copy silently destroyed at day 7.
- Expected behavior: upload.py invariant holds — «File NEVER deleted from spool until confirmed S3
  upload» (upload.py:16-17). Cleanup must either skip un-uploaded files or a retry pass must exist.
- Impact: permanent loss of DR copies while the platform claims RPO 24h off-site (core/AGENTS.md
  §Безопасность данных); only evidence is IMP:9 log lines inside the container.
- Minimal fix: cleanup marks/deletes only files with an uploaded-sentinel (or retention queries S3
  HEAD before `rm`), plus a daily spool-rescan retry job.
- Required regression test: `test_cleanup_spares_unuploaded_dump` — spool file mtime 8d ago, no S3
  confirmation → run cleanup → assert file still present; with sentinel → deleted.

## BUG-0803 — Backup freshness collector judges by log mtime, which cron updates even when the job fails
- Severity: HIGH
- Confidence: 90%
- File: core/internal/healthcheck/metrics/backup_collector.py:76
- Symbol: `get_backup_status` / `_read_mtime_ts` (mtime → age<25h ⇒ "ok")
- Trigger: backup-cron container healthy (healthcheck.sh = `pgrep cron`) but the nightly job fails
  after startup (wrong POSTGRES_HOST/PORT — see TRAP[BUG] pgbouncer P1 in backup_postgres.py:174,
  auth failure, gzip failure...).
- Execution path: cron spawns `backup-postgres.sh >> /var/log/platform/backup/postgres.log 2>&1`
  (crontab:28) → shell opens/appends the redirect target at launch → mtime refreshed even though
  `run_backup` then fails at validate/dump/verify (backup_postgres.py:186-238) → collector reads
  fresh mtime (:73-84), age<25h → `status="ok"` (:126) → status-metrics.json green forever.
- Actual behavior: "ok" means «cron fired», not «backup succeeded».
- Expected behavior: freshness derived from success artifacts (latest verified dump file in spool /
  UPLOAD OK audit entry / S3 HEAD of newest key).
- Impact: monitoring blind spot exactly for the class of incidents already observed on this node
  (pgbouncer-port P1: backups silently failing); combined with BUG-0802 the DR chain can be fully
  dead while dashboards are green.
- Minimal fix: have `run_backup` touch a success stamp (e.g. `.last_ok_postgres`) only after
  verification+upload decision; collector stats that file.
- Required regression test: `test_backup_status_stale_when_job_fails_after_start` — log mtime now,
  success-stamp 30h old → assert `status == "stale"`.

## BUG-0804 — Watchdog persists restart cooldown before executing restarts; failures/timeouts burn the cooldown without action
- Severity: MEDIUM
- Confidence: 85%
- File: core/internal/healthcheck/watchdog.py:497
- Symbol: `decide_actions` → `run_watchdog`
- Trigger: ≥1 container eligible for restart and either (a) first `docker restart` returns rc≠0, or
  (b) host-cron `timeout 50` (system.py:411-414) kills the pass mid-loop (each docker/notify command
  allows DOCKER_TIMEOUT=30s, watchdog.py:91 — two commands already exceed the budget).
- Execution path: decide_actions sets `new_state["last_restart"][name] = now` for EVERY planned
  action (:497-499) → `save_state` persists all of them BEFORE any restart (:669-674) → execution
  loop `restart_container(...)`; first failure `return 1` (:678-680) skips remaining actions → their
  cooldown (default 30 min, WATCHDOG_COOLDOWN_MIN) is already armed although nothing was restarted.
- Actual behavior: unhealthy containers wait an extra full cooldown window (or indefinitely under a
  persistent kill-at-50s pattern) despite the watchdog having "decided" to heal them.
- Expected behavior: last_restart updated per-action only after its restart succeeds (state commit
  transactional with action execution), or pending actions retried next pass.
- Impact: auto-healing latency 10→40+ min for multi-container incidents; watchdog appears to work
  (logs RESTART decisions, Telegram sent for action #1) while later containers stay down.
- Minimal fix: move `new_state["last_restart"][name] = now` out of decide_actions; set it in
  `run_watchdog` right after a successful `restart_container`, then re-save state.
- Required regression test: `test_restart_failure_does_not_consume_cooldown_of_pending_actions` —
  fake run_cmd fails restart for A, healthy B planned; second pass immediately after → B restarted
  on pass 1 (not blocked by pre-armed last_restart[B]).

## BUG-0805 — Hourly wal_sync cron has no flock unlike sibling jobs; degraded-S3 runs overlap themselves
- Severity: MEDIUM
- Confidence: 70%
- File: core/modules/backup-cron/scripts/crontab:42
- Symbol: crontab entry `10 * * * * root python3 /usr/local/bin/wal_sync.py`
- Trigger: S3 latency degradation: up to WAL_MAX_UPLOAD_PER_RUN=200 PUTs/run (wal_sync.py:82), each
  PUT/HEAD bounded by connect_timeout=10/read_timeout=30 × 3 standard retries (:222) → worst-case
  run ≫ 60 min interval → cron starts a second instance while the first is mid-pass.
- Execution path: cron fires hourly with NO `flock -n` wrapper (contrast platform-metrics
  system.py:292-295 and watchdog system.py:411-414 which both use `flock -n … timeout 50`) → two
  concurrent `main()` passes → duplicate HEAD→PUT of same keys, doubled request pressure on an
  already-struggling S3, concurrent `apply_s3_retention` delete batches. Additionally the schedule
  comment «Run до 03:00 дампа — не пересекается» (crontab:41) is false: :10 fires at 03:10, inside
  the 03:00 dump/upload window (upload.py may legitimately run until ~04:35 with 3×30min retries).
- Actual behavior: overlapping wal_sync passes possible; documented collision policy («parallel
  start allowed», written for the spaced-out daily jobs) was never revisited for the hourly job.
- Expected behavior: `flock -n /run/lock/platform-wal-sync.lock timeout <N>` like sibling cron lines
  (POSIX unlink-while-open keeps data safe, so harm is duplicate load/races, not corruption).
- Impact: amplified S3 load during exactly the degradation scenario WAL sync exists for; noisy
  duplicate uploads; potential double-delete batches (idempotent but unaudited).
- Minimal fix: wrap crontab:42 in `flock -n /run/lock/wal-sync.lock timeout 3000`.
- Required regression test: static gate asserting every `/etc/cron.d/platform-backup` job line
  carries `flock -n` (parity with test_gate_status_page.py flock assertions).

## BUG-0806 — Streaming subprocess reader threads abandoned after 10s join: truncated stdout returned as complete
- Severity: LOW
- Confidence: 60%
- File: core/internal/shared/subprocess_io.py:360
- Symbol: `run_subprocess_streaming`
- Trigger: child command spawns grandchildren that inherit and keep the stdout/stderr pipes open
  past the child's exit (shell wrappers backgrounding daemons); reader loops (`for raw in pipe`,
  :297) don't EOF until ALL write-side fds close.
- Execution path: `proc.wait(timeout)` returns on child exit → `t_out.join(timeout=10)`
  (:360-361) times out because grandchild still holds pipe → function proceeds and builds
  `StreamingResult.stdout = "\n".join(stdout_lines)` (:364) from a partially drained buffer →
  callers parse/report incomplete output as if complete; orphaned daemon reader threads linger.
- Actual behavior: silent truncation of captured output; no flag distinguishes "drained" from
  "join timed out".
- Expected behavior: result should carry `truncated=True` (or raise/log IMP:8) when joins expire;
  ideally close the parent's copy of the pipes to force EOF semantics discussion explicitly.
- Impact: corrupted JSON/report parsing downstream in check_suite/test_runner consumers under rare
  grandchild-holding patterns; low frequency, high confusion cost when hit.
- Minimal fix: track join success; expose `timed_out or not drained` in StreamingResult and log it.
- Required regression test: `test_streaming_result_flags_undrained_pipes` — child forks a
  grandchild holding stdout for 15s; assert result exposes non-drained marker instead of clean rc.

---

HYPOTHESIS (below threshold, listed for completeness): container entrypoint
`write_env_file` uses fixed pid-less temp name `/etc/environment.tmp`
(core/modules/backup-cron/scripts/entrypoint.py:140) — safe today only because the entrypoint runs
exactly once per container life; would collide under any future supervisor-retry (<60%, no
concurrent-run mechanism found).

Direction coverage note: (f) fixed temp filenames across overlapping scheduled runs — no proven
instance in the audited surfaces (receive_flow/mkdtemp, issue_cert/mkstemp, watchdog/tempfile+replace
are all unique-per-run); (c) long-run growth — audit.jsonl is rotated (core/bootstrap/platform-audit.logrotate,
weekly rotate 30) and Loki has compactor retention, so no proven unbounded-growth defect beyond those
already covered above.

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|-----------|-----------|
| BUG-0801 | CRITICAL | 95% | drain_all_count игнорирует exit-коды детей: упавший модуль параллельного деплоя считается задеплоенным — rollback и exit 2 не срабатывают |
| BUG-0802 | HIGH | 85% | backup-cleanup удаляет из spool дампы старше 7д без проверки загрузки в S3 (авто-ретрая нет) — тихая потеря off-site бэкапов |
| BUG-0803 | HIGH | 90% | backup-коллектор меряет свежесть по mtime лога, который cron обновляет даже при фейле джобы — «ok» при мёртвых бэкапах |
| BUG-0804 | MEDIUM | 85% | watchdog коммитит last_restart до выполнения рестартов; фейл/timeout 50 сжигает cooldown без действия (+30 мин лечения) |
| BUG-0805 | MEDIUM | 70% | hourly wal_sync без flock (сиблинги имеют): при деградации S3 прогон >60 мин → самоперекрытие, двойные PUT/retention |
| BUG-0806 | LOW | 60% | run_subprocess_streaming после join(10) возвращает недренированный stdout как полный — тихая обрезка вывода |
