# Findings 006 · Shared mutable state

## DEP-0025 · `platform_config._loaded` flag-before-load latch — permanent empty-defaults for process lifetime
- Severity: MED · Category: mutable-state · Confidence: HIGH
- Files: `core/internal/config/platform_config.py:75,78` (`global _defaults, _loaded`; `_loaded = True` set BEFORE file read)
- Dependency chain: 6 consumers — backup_config, s3_ssl_cache, cert_orchestrator, preflight, docker_orchestrator, context_deployer
- Coupling mechanism: lazy module-level cache; first failed/absent read latches `_loaded=True` → all later `get_default()` return `""` forever
- Why dangerous: if platform-infra.yaml is absent at first call (wrong cwd / PLATFORM_ROOT resolution order), every default silently empty for whole process; also test-order leakage (PLATFORM_ROOT=A cached into test B)
- Evidence: line numbers above; consumer list verified by grep
- Scenario: bootstrap phase imports config before PLATFORM_ROOT set → cert/S3 defaults empty → downstream phases misconfigure with no retry
- Impact: silent wrong-config propagation on the deploy/bootstrap path
- Minimal decoupling: set `_loaded = True` only after successful load; add `reset_cache()` used by tests
- Code churn: S (few lines) · Regression risk: LOW · Phase: **Pre-launch candidate** (one-line latch fix)

## DEP-0026 · `decrypt_secrets._TEMP_FILES` signal handler iterates live list
- Severity: MED · Category: mutable-state · Confidence: MED (+HYPOTHESIS trigger)
- Files: `core/internal/secrets/decrypt_secrets.py:83,142-144,252,302-303`
- Dependency chain: atexit+signal handlers registered at import ↔ main-thread append/remove of temp AGE key files on /dev/shm
- Coupling mechanism: docstring claims snapshot-copy but code iterates the LIVE list inside handlers; main thread mutates concurrently
- Why dangerous: future bulk mutation (`.clear()`/extend in main thread) + SIGTERM mid-iteration → handler raises "list changed size during iteration" → dies silently → AGE master key material left un-wiped on /dev/shm
- Evidence: :252 append, :302-303 remove vs live-list iteration in cleanup
- Scenario: operator Ctrl-C during decrypt burst during launch ops
- Impact: security-adjacent residue (cleanup is dd-wipe semantics)
- Minimal decoupling: iterate `list(_TEMP_FILES)` snapshot in both handlers (matches docstring intent)
- Code churn: S · Regression risk: LOW · Phase: **Pre-launch candidate**

## DEP-0027 · bootstrap state.json last-writer-wins residual + unguarded `--force` unlink
- Severity: MED · Category: mutable-state · Confidence: HIGH (mitigation verified; residual is design decision)
- Files: `core/internal/bootstrap/lifecycle/state_store.py` (FileLock 30s, tmp+fsync+replace); `cli.py --force`
- Dependency chain: bootstrap init flow ↔ node-update flow writing same `/var/lib/platform/.bootstrap/state.json`
- Coupling mechanism: flock serializes writes (tearing fixed after real P1, DevPlan 136 T9.2, TRAP[BUG] state_store.py:281) BUT semantic clobber remains: update-flow write can erase init-flow phase progress; `--force` unlink bypasses lock entirely
- Why dangerous: two operators/automations running bootstrap+update in parallel lose phase state → retry/degrade behavior undefined
- Evidence: prior incident documented in module contract; force-unlock path lacks flock
- Scenario: launch-day parallel `node-update` while `bootstrap --mode init` reruns
- Impact: state machine confusion on node; recoverable manually
- Minimal decoupling: route `--force` through FileLock; add writer identity (flow name) to state and refuse cross-flow overwrite without flag
- Code churn: S–M · Regression risk: LOW · Phase: Pre-launch guard cheap part (--force lock), rest post-launch

## DEP-0028 · `audit_logger._PERMISSIONS_SET` process-global mutated by all audit writers
- Severity: LO · Category: mutable-state · Confidence: HIGH
- Files: `core/internal/shared/audit_logger.py:85-87,199`
- Coupling mechanism: global set as chmod-permission cache; 6+ subsystems write audit logs through it
- Why dangerous: cross-subsystem global mutation; races benign (GIL atomic add; worst case double-chmod)
- Evidence: guard pattern `if log_file not in _PERMISSIONS_SET: ...add(...)`
- Scenario: none realistic today; flagged for completeness
- Impact: negligible
- Minimal decoupling: none needed; document as intentional cache
- Code churn: — · Regression risk: — · Phase: N/A

## DEP-0029 · converge/infra.py module globals + provider_registry lru_cache staleness
- Severity: LO · Category: mutable-state · Confidence: HIGH/MED(+HYPOTHESIS)
- Files: `core/internal/bootstrap/converge/infra.py:94-194` (`drifts`, `exit_code`, globals + explicit `reset_state()`); `bootstrap/provider_registry.py:208` (`@lru_cache(maxsize=4)` on YAML load)
- Coupling mechanism: single-phase CLI process-lifetime globals (guarded by reset_state); path-keyed lru_cache never invalidated (certs-providers.yaml edits invisible to long-lived importer)
- Why dangerous: only if a long-lived process (hermes-agent/polling loop) ever imports them — HYPOTHESIS, not observed
- Evidence: reset_state exists; callers one-shot today
- Scenario: future daemonization reuses modules across cycles without reset
- Impact: latent only
- Minimal decoupling: none pre-launch; note Rev condition in TRAP-style comment when any daemonization work starts
- Code churn: — · Regression risk: — · Phase: Post-launch watch

Positive finding: no singletons/service locators anywhere in core/ (0 `_instance`, 0 `get_*manager`); dataclasses use `default_factory` correctly; only 2 module-level mutable collections in entire production tree. Mutable-state discipline is exceptionally high.
