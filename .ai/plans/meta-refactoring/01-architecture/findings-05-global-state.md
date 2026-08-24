# Findings 05 — Hidden global state

## ARCH-0017 — Import-time hijack of process-wide SIGINT/SIGTERM handlers
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** S · **Phase:** post-launch (latent for library consumers)
- **Files:** `core/internal/secrets/decrypt_secrets.py:142-144` — `atexit.register(_cleanup_temp_files)` + `signal.signal(SIGTERM/SIGINT, _signal_handler)` at **module top level**; `_TEMP_FILES`:83; `sys.path.insert`:71-72
- **Evidence:** registration runs at import, not in `main()`. In-process importers exist today (unit tests); any future library consumer of `decrypt_sops_file` inherits global signal dispositions. Ctrl-C during pytest runs the cleanup handler, restores `SIG_DFL`, re-kills — bypassing KeyboardInterrupt handling and fixture teardown. Also `signal.signal` raises `ValueError` if first imported off the main thread.
- **Impact:** hidden contract "importing = installing signal handlers"; test-runner kills without diagnostics.
- **Minimal fix:** move registration into `main()` / explicit `install_cleanup_handlers()` called by CLI only (idempotent guard).

## ARCH-0018 — Dual import identity for `monitoring/*`: same file loaded as two distinct modules
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** M · **Phase:** post-launch
- **Files:** try/except + `sys.path.insert` fallback pattern in 9 files: `monitoring/service_reload.py:21-43`, `alert_rules.py:38-44`, `catalog_refresh.py:29-35`, `grafana_dashboards.py:37-43`, `prometheus_targets.py:32-38`, `loki_retention.py:40-46`, `langfuse_projects.py:40-46`, `config_renderer.py:45-63`; canonical importers `deploy/hooks/post_deploy_chain.py`, `makefiles/manifest.mk:99`
- **Evidence:** modules reachable as `monitoring.X` and `core.internal.monitoring.X`; both branches exercised in different invocation modes. ~45 `sys.path.insert` self-bootstrap sites across core/internal are the enabling infrastructure.
- **Scenario:** `RenderResult` exists twice → `isinstance`/`except` across identities silently fails; loggers/caches duplicate per identity. Failure class already proven in-repo: TRAP[BUG] `decrypt_secrets.py:59-63` (два разных `PlatformFatalError`, `pytest.raises` не ловит).
- **Impact:** order-dependent behavior in tests and post-deploy chain; silent class-identity mismatches.
- **Minimal fix:** canonicalize all intra-monitoring imports to `core.internal.monitoring.*`; script mode via thin `__main__` bootstrap importing through the canonical package.

## ARCH-0019 — Notification throttle registry is process-local: dedup silently no-ops for cron consumers
- **Severity:** P2 · **Confidence:** 0.7 · **Churn:** M · **Phase:** post-launch
- **Files:** `core/internal/shared/notifications.py:103` (`_THROTTLE_REGISTRY: dict`), writer `notify_event`:532,:601; consumers run as one-shot cron units (`phases/system.py` φ3 watchdog → `/etc/cron.d/platform-watchdog`, security/tor checks)
- **Evidence:** each cron run starts with an empty registry — the throttle window never spans processes.
- **Scenario:** persistently unhealthy container or expiring cert → watchdog fires every cycle → Telegram alert storm (exactly what throttling prevents); "SUPPRESSED" logs visible only within single runs.
- **Impact:** alert fatigue on production node; misleading logs.
- **Minimal fix:** file-backed throttle stamp per `(event, fingerprint)` under `/var/lib/platform/run/` (atomic write + short flock), or document per-consumer semantics.

## ARCH-0020 — Library functions mutate `os.environ` permanently (no save/restore)
- **Severity:** P3 · **Confidence:** 0.7 · **Churn:** M · **Phase:** post-launch
- **Files:** `bootstrap/s3_ssl_cache.py:118-126` (`_get_s3_client` pops `HTTPS_PROXY/HTTP_PROXY/NO_PROXY`), `bootstrap/cert_orchestrator.py:1118,1139-1142` (`_purge_proxy_env`), `bootstrap/deploy/docker_orchestrator.py:353` (`os.environ.setdefault("COMPOSE_PROFILES",…)`), `:371-373` (`os.environ["NGINX_OVERLAY_DIR"]=…`)
- **Evidence:** all on library paths; lifecycle `cli.py` runs φ7 certificates then φ8 deploy **in one process** — proxy purge is unconditional and never restored.
- **Scenario:** operator/CI machine with legitimate HTTP(S)_PROXY: after any S3/cert op, later subprocesses (git, docker pull, telegram) silently bypass proxy; `COMPOSE_PROFILES` set for module A leaks into module B's resolution.
- **Impact:** environment-dependent failures attributed to the wrong phase.
- **Minimal fix:** pass sanitized `env=` dicts to the specific subprocesses; context-manager restore where setdefault must stay.

## ARCH-0021 — `fingerprint.save_cache` uses a fixed tmp name despite invariant claiming concurrency safety
- **Severity:** P3 · **Confidence:** 0.75 · **Churn:** S · **Phase:** post-launch
- **Files:** `core/internal/check_suite/fingerprint.py:205-214` (`tmp = path.with_suffix(".json.tmp")`) vs contract `:16` («конкурентные executor'ы не портят файл»)
- **Evidence:** the platform fixed this exact bug in `state_store.save_state` (DevPlan 136 W9 T9.2 → unique-tmp `atomic_writer`); `save_cache` independently reimplements the broken pattern. Writers: every `make check`/`check-diff`; pre-push hook + agent run overlap → truncated JSON → cache silently disabled (WARNING only).
- **Minimal fix:** replace body with `shared/atomic_writer.atomic_write_json` (see ARCH-0040).
