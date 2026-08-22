# Findings 08 — Initialization / lifecycle architecture

## ARCH-0033 — Content-hash invalidation keyed to the wrong file set → platform updates silently no-op on production nodes
- **Severity:** P1 · **Confidence:** 0.85 · **Churn:** S–M · **Phase:** post-launch (primary update channel)
- **Files:** `core/internal/bootstrap/lifecycle/state_machine.py:525-563` (`_phase_input_hash`: hashes only phase_value + node.yaml modules/services + **bytes of state_machine.py itself**) · docstring :511-515 claims phases/__init__.py is included — code never reads it (doc-vs-code divergence) · `_HASH_INVALIDATED_PHASES`:265-271 (φ8/φ8.5/φ11/φ12/φ13) · `cli.py:773-807` (done + hash-match → "already done — skipping") · delivery channel `core-deploy.yml:263` (`ssh … make node-update` on every main push)
- **Evidence:** the hash input does NOT include `phases/docker.py`, `deploy/docker_orchestrator.py`, `deploy_orchestrator.py`, module compose files, or `lib/*.sh` — everything φ12 actually executes. `GITHUB_SHA` is not mixed in either.
- **Failure scenario:** release changes docker_orchestrator/compose base → CI rsyncs to `/opt/platform/core` → `make node-update`: phases marked done, hash unchanged → all 5 update phases log "already done — skipping", exit 0 → **new code sits inert on disk; deployed stacks keep old behavior** until state_machine.py or node.yaml changes.
- **Impact:** silent divergence between delivered core and deployed state on the main update channel; audit shows success.
- **Sub-defect:** hash computed against `os.environ` NODE_YAML while execution runs against merged env (`cli.py:924/927` without `env=` vs `state_machine.py:756`) — split-brain for non-default callers.
- **Minimal fix:** mix a delivered-payload fingerprint (rsync checksum / CI SHA persisted beside state.json) into `_phase_input_hash`; align hashing env with executing env.

## ARCH-0034 — Dependency ordering exists only in parallel mode; topo failure silently degrades to unordered deploy
- **Severity:** P2 · **Confidence:** 0.75 · **Churn:** S · **Phase:** pre-launch (fresh-node bootstrap)
- **Files:** `bootstrap/deploy/deploy_orchestrator.py:479-485` (parallel path except → WARN + `_deploy_sequential` fallback) · `:669-730` (sequential = plain for-loop over **node.yaml list order**, never consults `module.yaml#depends_on`) · ordering lives only in `topo_sort.kahn_topological_sort` on the parallel path · `deploy-modules.sh:74` (`DEPLOY_PARALLEL:-false`)
- **Evidence:** cross-file `depends_on` explicitly unsupported (TRAP, `hermes-agent/docker-compose.base.yml:22-32`); canonical `make bootstrap-node` → φ8 does NOT set DEPLOY_PARALLEL → default fresh-node path has **no dependency resolution**; node.yaml list order is a load-bearing hidden timing dependency.
- **Failure scenario:** fresh node with hermes-agent listed before minio/clickhouse → starts against absent dependencies; or malformed module.yaml on parallel path → topo raises → WARN → falls back to the *unordered* list exactly when dependency metadata is broken.
- **Minimal fix:** run kahn topological linearization for sequential too; topo failure → fail-fast ConfigValidationError, not best-effort fallback.

## ARCH-0035 — Failed deploy group doesn't gate subsequent groups — dependents deploy into rolled-back dependencies
- **Severity:** P2 · **Confidence:** 0.7 · **Churn:** S · **Phase:** pre-launch
- **Files:** `deploy_orchestrator.py:520-542` (group loop: `failed.extend(fnames)` then `continue` on both failure and exception paths) · `parallel_runner.py:352-377` (atomic `compose down` of the failed group)
- **Failure scenario:** G1=[postgres] fails healthcheck → rolled back (down) → loop proceeds: G2=[langfuse, litellm, hermes-agent…] deploys anyway → crash-looping against absent DB/S3 while deploy reports per-module warnings; root cause buried.
- **Impact:** cascading misleading failures; longer MTTR.
- **Minimal fix:** previous group's critical failures abort remaining groups (or gate on declared-dependency readiness).

## ARCH-0036 — Secrets chain: FATAL advertised, WARN delivered — inconsistent fail-open/fail-closed across one pipeline
- **Severity:** P2 · **Confidence:** 0.8 · **Churn:** M · **Phase:** post-launch
- **Files:** loud path `lifecycle/helpers/secrets.py:49-75` (decrypt FATAL; TRAP[BUG] 2026-07-23 P0) vs silent paths inside the same φ4 step wrapped as FATAL by `phases/secrets.py:130-135`: `helpers/secrets.py:121-123` (`source_secrets_env` parse failure → broad `except Exception` → WARN), `:133-135` (autogen incl. htpasswd → WARN) · downstream: φ6 GHCR token warn-only (`phases/preconditions.py:168-182`); φ8 batch-check error → WARN (`deploy_orchestrator.py:495-503`); **sequential env-check error → `missing = []` fail-open** (`:688-693`)
- **Failure scenario:** secrets.env corrupt or autogen fails → φ4 returns True, marked done; φ6 proceeds without token; φ8's validator erroring yields `missing=[]` → modules deploy without creds; status-page htpasswd silently stale; failures surface later as opaque compose errors far from the cause.
- **Minimal fix:** narrow the broad excepts; raise on `tier=required` autogen/source failure; validator exceptions → `missing=["<validator-error>"]` (fail-closed).

## ARCH-0037 — `bootstrap.sh` direct-invocation path drops passthrough args and flips execution site
- **Severity:** P3 · **Confidence:** 0.6 · **Churn:** S · **Phase:** post-launch (ops)
- **Files:** `core/entrypoints/bootstrap.sh:34-48` — arg loop consumes `$#` into `PASSTHROUGH_ARGS`, then non-resolve branch execs `"$@"` which is empty post-parse (resolve path :78 appends them correctly)
- **Evidence:** canonical Makefile always passes `--resolve` (`makefiles/bootstrap.mk:32`) so CI is unaffected; direct `bootstrap.sh --node x --auto-reconcile` silently drops `--auto-reconcile` AND runs node-lifecycle **locally** even if node.yaml points at a remote host — same command, opposite execution site depending on invocation form.
- **Minimal fix:** forward `${PASSTHROUGH_ARGS[@]+…}` or reject non-resolve invocation with usage error.

Context (folded, no separate ID): two python readiness stacks coexist — `healthcheck_runner.wait_for_readiness` 15×2s + `run_healthcheck` 20×3s vs `HealthcheckPoller` 20×3s HTTP-first; hc-marker set only on parallel path (`deploy_orchestrator.py:554`) though docstring claims "always" — sequential bootstraps double-healthcheck through different windows. Benign today; the stacks will drift.
