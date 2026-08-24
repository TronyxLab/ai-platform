# Findings 07 — Duplicated business logic / competing implementations

Sanctioned facade pairs excluded (ssh_opts↔lib/ssh.sh, healthcheck_poller↔lib/healthcheck.sh, docker_ops↔lib/docker.sh). All findings are implementations **beyond** the sanctioned set.

## ARCH-0028 — Core-delivery rsync channel: CI workflow vs `core_deliverer.py` with drifted exclude sets + `--delete` flap
- **Severity:** P2 · **Confidence:** 0.9 · **Churn:** M · **Phase:** pre-launch
- **Files:** `.github/workflows/core-deploy.yml:163-171,213-220` (inline `rsync -avz --delete --exclude …`) · `core/internal/bootstrap/core_deliverer.py:55-72` (`RSYNC_EXCLUDES_CORE/NODE`, DR channel)
- **Evidence (divergence already live):** CI delivers `docker-compose.test.yml` (×13) and `.pytest_cache/` into production `/opt/platform/core/`; the DR channel filters them. Both run `--delete` → alternating channels flap (operator deliver deletes test files; next CI deploy re-adds). Documented invariant «test-compose не доставляются на прод» violated by the *primary* channel. 3 TRAP[BUG] incidents in this workflow block alone.
- **Impact:** divergent production tree depending on delivery channel; phantom configs during incident triage.
- **Minimal fix:** CI calls `python3 -m core.internal.bootstrap.core_deliverer` (or emits excludes from it) — one owner for the exclude table.

## ARCH-0029 — Container-health criterion: status-page metrics collector contradicts the canonical poller
- **Severity:** P2 · **Confidence:** 0.9 · **Churn:** S · **Phase:** post-launch
- **Files:** canonical `shared/docker_compose.py:593` (`state=="running" and health in {"healthy","","none"}`) · sanctioned facade `core/lib/healthcheck.sh:124-131` · **divergent** `healthcheck/metrics/docker_collector.py:264-274` (`_get_health_status`: only `Status=="healthy"` — no-healthcheck → False) · consumer `core/modules/status-page/collectors/checks/containers.py:46-49` (`running and not healthy → WARN`) · test-side 4th copy `tests/e2e/chaos_audit.py:339`
- **Evidence:** canon treats running+no-Health-block as healthy; collector treats it as unhealthy; `starting` (start_period window) → WARN while poller correctly waits. Gates cover orchestrator paths only — docker_collector is outside their allowlist.
- **Scenario:** litellm restart (compose `start_period: 60s`) → ~1 min false WARN on status-page per restart; any container without HEALTHCHECK → permanent WARN while canonically healthy.
- **Impact:** user-facing false degradation signals; watchdog and status page disagree.
- **Minimal fix:** collector mirrors the canonical predicate (or exposes raw status; predicate applied once).

## ARCH-0030 — Project payload file-list exists in 3 unsynchronized copies
- **Severity:** P2 · **Confidence:** 0.85 · **Churn:** S · **Phase:** pre-launch
- **Files:** `.github/workflows/deploy-project.yml:354-358` (shell `$FILES` assembly) · `deploy/payload_deliverer.py:72-76` (`WHITELIST_FILES`) · `payload_deliverer.py:79-84` (`_PAYLOAD_FILE_NAMES`) · receive-side check `deploy/receive_flow.py:394`
- **Evidence:** in-repo TRAP (`payload_deliverer.py:66-71`, B20a: practices.lock silently missing → K3 state=unmanaged forever) documents this exact failure mode; its Rev-note says «синхронизировать оба кортежа» — omitting the CI copy. No gate reads the workflow's `$FILES`.
- **Scenario:** next GENERATED practice file added to Python whitelist but not to workflow `$FILES` → CI deploys lack it; receive accepts it (whitelisted) so nothing fails loudly — B20a repeats.
- **Minimal fix:** single constant consumed by all three (Python builds the tar in CI too, or generated file + check-manifests gate).

## ARCH-0031 — org-vs-node.yaml context validation duplicated; exception handling already drifted
- **Severity:** P3 · **Confidence:** 0.85 · **Churn:** S · **Phase:** post-launch
- **Files:** `shared/project_yaml.py:342-369` (`_validate_org_vs_node_yaml`; catches ConfigNotFoundError+ConfigParseError+OSError+ValueError → skip) · `scaffold/scaffold_helpers.py:663-694` (`validate_org_against_node_yaml`; identical message/logic but catches only 2 exception types)
- **Scenario:** unreadable node.yaml during `adopt-project` → unhandled crash, while every other consumer of the same semantic gracefully skips.
- **Minimal fix:** scaffold_helpers delegates to the project_yaml implementation; align catch-tuple.

## ARCH-0032 — Name-validation regex family: 4 classes of strictness
- **Severity:** P3 · **Confidence:** 0.8 · **Churn:** S · **Phase:** post-launch
- **Files:** canon `shared/project_registry.py:105-124` (`^[a-zA-Z0-9][a-zA-Z0-9_-]*$` + verb-reserve U-56) · copy A `scaffold/vhost_renderer.py:540` (no verb-reserve) · copy B `bootstrap/deploy/context_overlay.py:73` (same, documented carve-out) · **copy C (undocumented drift)** `bootstrap/setup_node.py:82` (`NODE_NAME_RE = ^[a-zA-Z0-9_-]+$` — allows leading `-`/`_`)
- **Evidence:** validation outcome depends on code-path order; doc-level canon says kebab-case but zero validators enforce lowercase.
- **Scenario:** node name `-foo` passes `setup_node.validate_node_name` (:107-119) into sudoers/paths while violating the canon everywhere else.
- **Minimal fix:** `NODE_NAME_RE` → reuse `validate_project_name`.
