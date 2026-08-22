# Direction 3: async/concurrency — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit

Scope audited: `core/internal/deploy/` (orchestrator, receive_flow, hooks/post_deploy_chain, audit/history), `core/internal/bootstrap/deploy/` (parallel_runner fork pool, deploy_orchestrator routing), `core/internal/shared/file_lock.py`, `subprocess_io.py`, `check_suite/diagnostic.py` + `fingerprint.py`, `status-page/collectors/aggregate.py`, `monitoring/config_renderer|loki_retention|prometheus_targets`, `catalog/generate_catalog.py`, `bootstrap/converge{,.py,/projects.py}`, `healthcheck/{watchdog,metrics/json_writer}.py`, `secrets/decrypt_secrets.py`.

---

## BUG-0301 — drain_all_count counts failed children as deployed; group-failure signal silently dropped in final drain

- Severity: HIGH
- Confidence: 95%
- File: core/internal/bootstrap/deploy/parallel_runner.py:498
- Symbol: `drain_all_count` (default `drain_all_fn` of `deploy_docker_group`)
- Trigger: `DEPLOY_PARALLEL=true` topo-group deploy where a module's forked child exits non-zero and is reaped by the FINAL drain (all groups with ≤ `parallel_limit`=4 modules — the common topo-wave case; slot-waiter drain never runs).
- Execution path: fork child → `deploy_module_fn` fails → `os._exit(1)` (parallel_runner.py:329) → parent loop ends → line 338 picks default `drain_all_count` → line 500 `os.waitpid(pids[i], 0)` returns (reap ≠ success) → **line 503 `deployed += 1` unconditionally** → `group_failed` stays 0 → rollback gate at line 354 (`if group_failed > 0:`) false → atomic W5-E1 rollback skipped; consumer `deploy_orchestrator.py:531-539` gets empty `fnames` → `_aggregate_severity` sees no CRIT → exit 0.
```python
# parallel_runner.py:498-503
for i in range(len(pids) - 1, -1, -1):
    try:
        _ = os.waitpid(pids[i], 0)
        mod_name = pid_to_name.pop(pids[i], "?")
        # Success — waitpid returned without error means process exited
        deployed += 1
```
- Actual behavior: failing module counted as deployed; no `docker compose down` group rollback; severity-based exit code {0,1,2} reports success for a broken group. Contrast: sibling drains `_drain_pull_slots` (:142) and `drain_completed_count` (:470) both check `WIFEXITED/WEXITSTATUS`; only the final blocking drain drops status.
- Expected behavior: `WEXITSTATUS(status) != 0` → `failed += 1`, name into `failed_names`.
- Impact: node-update/bootstrap φ8/φ12 in parallel mode continue on a half-deployed module group with exit 0; healthcheck phase only WARNs (parallel_runner.py:425); broken modules stay down until next converge R9.
- Minimal fix: in `drain_all_count`, mirror `drain_completed_count`: inspect `os.WIFEXITED/WEXITSTATUS` before counting.
- Required regression test: `tests/unit/test_parallel_runner.py::test_drain_all_failure_status` — mock `waitpid → (pid, 1)` (non-zero exit): assert `failed == 1`, `names == ["mod"]`, `deployed == 0`. Plus a `deploy_docker_group` test using the REAL default drain (no `drain_all_fn` fake — current rollback tests inject a fake that masks this, test_parallel_runner.py:157) asserting `rolled_back != []` when the forked child exits 1.

## BUG-0302 — DeployOrchestrator.rollback()/remove() bypass the per-project flock that guards deploy()

- Severity: HIGH
- Confidence: 85%
- File: core/internal/deploy/orchestrator.py:711 (rollback), :817 (remove)
- Symbol: `DeployOrchestrator.rollback`, `DeployOrchestrator.remove`
- Trigger: operator runs `rollback`/`remove` while a CI receive of the same project holds `/var/lock/platform-deploy-{project}.lock`.
- Execution path: receive A acquires lock (orchestrator.py:295) → compose up running → rollback B enters `rollback()` directly → B `_restore_payload_files()` overwrites docker-compose.yml/.env.platform mid-deploy (line 744) → B `_rollback_compose()` re-ups previous image (line 752) interleaved with A's `compose up --force-recreate` → container churn/recreate ping-pong; same for `remove()`'s `docker compose down` deleting A's freshly created containers during its healthcheck poll.
```python
# orchestrator.py:295-297 — only deploy() takes the guard
lock = _FileLock(_platform_lock_path(project_name), timeout=0.0)
try:
    lock.acquire()
```
`rollback()` (:711-771) and `remove()` (:817-874) contain no `_FileLock` acquisition anywhere.
- Actual behavior: mutators of identical state run concurrently; invariant #6 of the class ("Concurrent guard: file lock") holds only for `deploy()`.
- Expected behavior: rollback/remove acquire the same per-project lock (blocking or non-blocking→FAILED) before touching payload/compose.
- Impact: rollback during in-flight deploy corrupts payload↔container pairing (old image + new env or vice versa); remove kills a just-deployed stack mid-healthcheck → false PARTIAL/FAILED results and manual cleanup.
- Minimal fix: wrap bodies of `rollback()`/`remove()` in `with _FileLock(_platform_lock_path(project_name), timeout=30.0):` (reentrant-safe vs nested history calls).
- Required regression test: `test_orchestrator_rollback_remove_take_deploy_lock` — inject recording lock factory; assert `rollback()` and `remove()` attempt `acquire()` on `platform_lock_path(project)`; negative interleave: with lock held, second operation returns FAILED "locked" instead of executing compose.

## BUG-0303 — FileLock silently degrades to no-lock; root-owned lock files from bootstrap permanently disable the CI concurrent-deploy guard

- Severity: HIGH
- Confidence: 75%
- File: core/internal/shared/file_lock.py:168
- Symbol: `FileLock._open_fd` / `FileLock.acquire`
- Trigger: lock file exists with owner root, mode 0644 (created during bootstrap φ8/φ12, which run as root via `context_deployer` → `DeployOrchestrator.deploy()`); later forced-command receives run as `ci-deploy`.
- Execution path: bootstrap (root) `orchestrator.deploy` → `_open_fd` creates `/var/lock/platform-deploy-{P}.lock` 0644 root:root (file_lock.py:167) → CI receive (ci-deploy) `os.open(path, O_RDWR|O_CREAT)` on existing root-owned 0644 → `PermissionError` → except branch logs one WARN and **returns None = success without any lock** (lines 168-175) → `deploy()` proceeds unlocked → two simultaneous pushes of project P both run `docker compose up --force-recreate` concurrently.
```python
# file_lock.py:166-175
self.path.parent.mkdir(parents=True, exist_ok=True)
return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
except PermissionError as e:
    logger.warning(
        "[IMP:7][FileLock][degrade] Cannot create lock dir %s (%s) — running WITHOUT lock "
        "(dev machine; node /var/lock is writable)", ...)
    return None
```
- Actual behavior: fail-open degradation. The docstring assumes degradation happens only off-node, but an existing unwritable lock FILE (not the dir — `/var/lock` is 1777) triggers it on-node. Same OSError branch swallows ENOSPC. Nothing ever chowns/chmods these lock files (repo-wide grep: zero hits), unlike the analogous snapshot-dir bug fixed as TRAP[BUG] B19 in `audit/history.py:180-190`.
- Expected behavior: on a node, inability to OPEN the lock must fail closed (raise) or fall back to a writable lock dir — not silently skip mutual exclusion whose sole purpose is serializing deploys (T9.1).
- Impact: the concurrency guard for the primary delivery channel (CI receive) is disabled for every project bootstrapped as root; double-push races produce compose conflicts/partial stacks with no error signal beyond one WARN log.
- Minimal fix: distinguish dir-unwritable (dev degrade OK) from file-open EACCES/EACCES-on-existing (fail closed); or create lock files world-writable/group-writable at bootstrap and chown in φ2 like other artifacts.
- Required regression test: `test_file_lock_existing_root_owned_file_fails_closed` — pre-create lock file mode 0644 owned by another user; monkeypatch `os.open` to raise `PermissionError`; assert `acquire()` raises `FileLockError` (or `held() is False AND callers abort`), i.e. degraded acquire never reports success for deploy-guard usage.

## BUG-0304 — Post-deploy chain runs outside the deploy lock and does unsynchronized read-modify-write + non-atomic writes to SHARED monitoring files

- Severity: MEDIUM
- Confidence: 80%
- File: core/internal/monitoring/loki_retention.py:89 (load) → :135 (write); core/internal/catalog/generate_catalog.py:145; chain call outside lock: core/internal/deploy/receive_flow.py:559-564
- Symbol: `update_loki_retention`, `generate_catalog`, `run_post_deploy_chain`
- Trigger: two CI receives of DIFFERENT projects overlap (independent sshd sessions are inherently concurrent); each runs `run_monitoring_reconfig` after its own `deploy()` released the per-project lock.
- Execution path: A `load_yaml_config(loki-runtime-config.yml)` (shared file `core/modules/logging/config/loki-runtime-config.yml`, constants.py:37) → B loads same → A inserts P1 retention stream, `open("w")` truncate+dump (loki_retention.py:135-136) → B inserts P2 into ITS stale copy, truncates and dumps → **P1 rule lost** (lost update); if writes interleave, torn YAML → next step POSTs config to `http://loki:3100/reload` (config_renderer.py:705-706 order: loki → reload) → reload fails or applies incomplete retention. Same window for `catalog.json`: both chains `Path(catalog_file).open("w")` + `json.dump` (generate_catalog.py:145-146) → torn JSON for external consumers.
```python
# loki_retention.py:133-136
# Write back
config_path.parent.mkdir(parents=True, exist_ok=True)
with Path(config_path).open("w", encoding="utf-8") as f:
    yaml.dump(existing, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
```
- Actual behavior: shared monitoring state mutated by unserialized writers with plain truncate-write (no tmp+os.replace, no flock); lost updates self-heal only on the NEXT redeploy of the losing project.
- Expected behavior: post-deploy chain steps touching shared artifacts serialize via a platform-level lock (e.g., `/var/lock/platform-monitoring.lock`) and write atomically (tmp+replace) like `state_store.save_state` does.
- Impact: silent log-retention loss (disk growth), torn catalog.json/loki YAML consumed by agents/monitoring; reload WARNs are swallowed as best-effort.
- Minimal fix: flock around `update_loki_retention` RMW + `atomic_write_text` for loki/catalog/alert/target files.
- Required regression test: `test_concurrent_reconfig_preserves_both_streams` — two threads run `update_loki_retention(P1)`/`(P2)` against one tmp config 100×; assert final file parses AND contains both `{compose_project="P1"}` and `{compose_project="P2"}` selectors.

## BUG-0305 — fingerprint cache save uses fixed tmp filename; two concurrent `make check` corrupt each other's write

- Severity: LOW
- Confidence: 85%
- File: core/internal/check_suite/fingerprint.py:210
- Symbol: `save_cache`
- Trigger: two `make check` processes (e.g., dev terminal + pre-push hook, or two worktree checks sharing `.git`) finish within the same seconds-wide window.
- Execution path: A `tmp = path.with_suffix(".json.tmp")` → open("w") → writing → B opens THE SAME fixed path with "w" (truncate) → interleaved `json.dump` at independent offsets → A `Path(tmp).replace(path)` publishes mixed/truncated bytes as check-cache.json.
```python
# fingerprint.py:210-214
tmp = path.with_suffix(".json.tmp")
with Path(tmp).open("w", encoding="utf-8") as f:
    json.dump(data, f)
    f.write("\n")
Path(tmp).replace(path)
```
- Actual behavior: violates the module's own invariant «save_cache атомарно: tmp + os.replace (конкурентные executor'ы не портят файл)» (docstring line 16) — unique-tmp discipline is exactly what `state_store.py:27-31` documented as the T9.2 fix for the identical bug pattern ("фиксированный tmp … гонка writers"), but `fingerprint.save_cache` predates/escapes the shared `atomic_writer` canon.
- Expected behavior: unique temp (mkstemp in git-dir) + replace, or shared `atomic_write_json`.
- Impact: bounded — corrupted cache → `load_cache` JSONDecodeError → None → replay disabled (perf loss, no wrong verdicts: replay additionally requires exact fingerprint match). Still a latent trap if cache format gains consumers trusting validity.
- Minimal fix: switch to `atomic_write_json(path, data)` from `core/internal/shared/atomic_writer`.
- Required regression test: `test_save_cache_concurrent_writers_leave_valid_json` — two threads call `save_cache` with distinct payloads N times; every completed call's subsequent `load_cache` returns parseable dict (never raises, never None due to torn file while writer alive).

## BUG-0306 — converge reconcile_env_platform TOCTOU: missing-check then overwrite can clobber a freshly delivered .env.platform

- Severity: MEDIUM
- Confidence: 60%
- File: core/internal/bootstrap/converge/projects.py:278
- Symbol: `reconcile_env_platform`
- Trigger: converge (R3) processes project P while CI receive for P is between staging-copy and `os.replace` of `.env.platform` (receive_flow.py:452-460).
- Execution path: converge `env_file.is_file()` → False (projects.py:278) → receive delivers real .env.platform via `os.replace` (receive_flow.py:460) → converge proceeds past the guard → `env_file.write_text(...)` (line 300) truncates the just-delivered GENERATED file; worst case: generation raises (platform-env.yaml transiently missing) → `create_empty_env_file` fallback writes an EMPTY .env.platform over the delivered one (line 237) → project loses all `PLATFORM_*` DSN/URL vars on next restart.
```python
# projects.py:277-280
env_file = Path(proj_dir) / ".env.platform"
if env_file.is_file():
    logger.info("[IMP:7][converge][%s] SKIP: %s already exists (if-missing policy)", unit, env_file)
    return
```
- Actual behavior: classic check-then-act; the "if-missing policy" has no enforcement at write time (no O_EXCL, no re-check under lock, plain truncate-write).
- Expected behavior: create-if-missing semantics enforced by `os.open(O_WRONLY|O_CREAT|O_EXCL)` (or write-to-temp + link) so a file materialized between check and write is never overwritten.
- Impact: narrow race window but high blast radius (empty/regenerated env breaks project DB/cache wiring until next sync-env); converge runs unattended on every node-update/converge while CI pushes anytime.
- Minimal fix: O_EXCL create for both generated and fallback paths; treat EEXIST as SKIP.
- Required regression test: `test_reconcile_env_platform_never_overwrites_delivered_file` — pre-create `.env.platform` AFTER stubbing `is_file` to return False once; assert file content unchanged after reconcile (and fallback path refuses to truncate existing file).

---

## HYPOTHESIS-03 — import-time SIGTERM/SIGINT handler registration in decrypt_secrets hijacks disposition of every importer process

- Type: HYPOTHESIS (confidence <60%)
- Confidence: 40%
- File: core/internal/secrets/decrypt_secrets.py:143
- Symbol: module-level `signal.signal(signal.SIGTERM, _signal_handler)` (lines 141-144)
- Trigger: any process importing `decrypt_secrets` (today: bootstrap phases/secrets helpers in main thread, unit tests) gets its SIGTERM/SIGINT replaced globally at import; handler performs non-trivial work (`_cleanup_temp_files` → `subprocess.run(["dd"...])`, logging) inside the signal handler and mutates module-global `_TEMP_FILES` (:83,:125-127).
- Why not proven: no current importer imports it from a non-main thread (would crash with `ValueError: signal only works in main thread`), and handler re-raises default disposition, so observable behavior change requires a threaded importer or a signal arriving mid-`subprocess.run` inside another handler invocation. Kept as direction-(f) watch item: registering signal handlers at import contradicts the platform's own no-import-side-effects canon (cf. D-I4 in generate_catalog.py:15-16).
- Required regression test if promoted: `test_importing_decrypt_secrets_does_not_install_signal_handlers` — fresh subprocess imports module, asserts `signal.getsignal(SIGTERM) is signal.SIG_DFL`; handler installation moves into `main()`.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0301 | HIGH | 95% | `drain_all_count` игнорирует WEXITSTATUS — фейлы модулей в финальном drain считаются deployed: атомарный rollback группы пропускается, exit 0 вместо 2 |
| BUG-0302 | HIGH | 85% | `rollback()`/`remove()` обходят per-project flock деплоя — конкурентный restore-payload/compose с in-flight receive |
| BUG-0303 | HIGH | 75% | FileLock молча деградирует в no-lock при EACCES: root-owned lock-файлы φ8 навсегда отключают guard у ci-deploy receive |
| BUG-0304 | MEDIUM | 80% | Post-deploy chain вне лока делает RMW/non-atomic записи общих файлов (loki-runtime-config.yml lost update, catalog.json tear) |
| BUG-0305 | LOW | 85% | `fingerprint.save_cache` — фиксированный tmp `.json.tmp`: два конкурентных `make check` рвут кэш (нарушение собственного инварианта модуля) |
| BUG-0306 | MEDIUM | 60% | converge `reconcile_env_platform` TOCTOU: is_file()-чек → write_text перезаписывает доставленный receive'ом .env.platform (fallback — пустой файл) |
| HYP-03 | — | 40% | decrypt_secrets регистрирует SIGTERM/SIGINT на импорте: захват диспозиции всех импортёров + subprocess в handler (нет доказанного триггера сегодня) |
