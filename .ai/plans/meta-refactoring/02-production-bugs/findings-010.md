# Direction 10: edge cases — forensic bug hunt

Date: 2026-08-22 · Commit: 10c1bf2 · Mode: read-only audit

Scope probed: `core/internal/scaffold/*` (name/domain/vhost pipeline), `core/internal/shared/project_registry.py`, `core/internal/healthcheck/metrics/*`, `core/entrypoints/*.sh`, `core/lib/*.sh`.

Surfaces verified CLEAN (no provable defect found): no `sed -i` without suffix / `readlink -f` / BSD `stat -f` in reachable dev paths (the one historical BSD-stat site was already fixed via `os.path.getmtime` — dead_code_checker.py:202); no unquoted `for x in $(...)` glob iteration in core shells; no naive `datetime.now()` comparisons in `core/internal/**`; user-controlled names in shell arg-parsing are quoted (adopt-project.sh:54 `${PROJECT_DOMAIN:+--project-domain "$PROJECT_DOMAIN"}`).

---

## BUG-1001

- **Severity:** HIGH
- **Confidence:** 90%
- **File:** core/internal/scaffold/project_scaffolder.py:83 (+ core/internal/shared/project_registry.py:105, core/internal/scaffold/vhost_renderer.py:536)
- **Symbol:** `auto_domain()` / `validate_project_name()` / `validate_vhost_identifiers()`
- **Trigger:** `make new-project NAME=<name>` where `<name>` contains `_` or is ≥62 chars (both pass `validate_project_name`).
- **Execution path:** edge-input name → `validate_project_name()` (project_registry.py:105, regex `[a-zA-Z0-9][a-zA-Z0-9_-]*` — no length cap, allows `_`) → PASS → `auto_domain()` builds `result = f"{name}.{pd}"` (project_scaffolder.py:83, no label validation) → project dir/repo/.env.platform created with domain `<name>.tronyx.ru` → later `render_vhost` → `validate_vhost_identifiers()` (vhost_renderer.py:567–570) → `_FQDN_LABEL_RE = ^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$` rejects label (>63 chars or `_`) → `ConfigValidationError` exit 4.
- **Actual behavior:** Project is admitted into lifecycle with an auto-domain that can never satisfy the FQDN validator; scaffolding proceeds (dir, repo, `.env.platform` carry the invalid domain), then every `make render-vhosts` / vhost render for that project fails permanently with exit 4.
- **Expected behavior:** Name rejected up-front in `validate_project_name()` (or `auto_domain()`), before any filesystem/repo/lifecycle mutation — single shared grammar for "project name usable as DNS label".
- **Impact:** Half-created project state; node-level `render-vhosts`/converge blocked until manual rename/cleanup; two validators disagree about the same logical entity.
- **Minimal fix:** In `validate_project_name()` enforce `len(name) <= 63` (minus reserved suffix budget) and forbid `_` (or reject `_` in `auto_domain()` with fail-fast before scaffold).
- **Required regression test:** `new-project NAME='a'*63` and `NAME=my_proj` → assert rejection BEFORE project dir creation (tmp_path empty afterwards) and before `auto_domain()` output is persisted.

## BUG-1002

- **Severity:** MEDIUM
- **Confidence:** 70%
- **File:** core/internal/healthcheck/metrics/docker_collector.py:264–274
- **Symbol:** `_get_health_status()`
- **Trigger:** Any container whose `docker inspect` State lacks `Health` (no HEALTHCHECK defined) or has `Health.Status == ""`.
- **Execution path:** edge-input inspect state (missing `Health` key) → `state.get("Health")` → `None` → `health = {}` (line 273) → `health.get("Status") == "healthy"` → `False`.
- **Actual behavior:** Container without a healthcheck is reported `healthy=False` — same signal as a genuinely failing/unhealthy container.
- **Expected behavior:** Canonical platform criterion (root/core AGENTS.md): running AND (healthy | "" | none) = healthy; only explicit `"unhealthy"` means unhealthy. Absence of `Health` must map to healthy-if-running, not `False`.
- **Impact:** Status metrics/alerts classify healthcheck-less containers as down; noise masks real failures (alarm fatigue), diverges from the single healthcheck-criterion TRAP[DECISION].
- **Minimal fix:** Return `True` when `Status` in `{None, "", "healthy", "starting"-absent}` and running; reserve `False` for explicit `"unhealthy"` (pass `State.Status == "running"` into the helper).
- **Required regression test:** Three inspect fixtures — `Health.Status="unhealthy"`, `Health` missing, `Health.Status=""` → assert only the first yields `False`.

## BUG-1003

- **Severity:** LOW
- **Confidence:** 55% (HYPOTHESIS — parser robustness; canonical docker-stats inputs are well-formed)
- **File:** core/internal/healthcheck/metrics/docker_collector.py:297–325
- **Symbol:** `_parse_bytes()`
- **Trigger:** Memory-size string with non-canonical unit casing, e.g. `"512mib"` / `"1.5Mb"` (regex is IGNORECASE; units dict is exact-case).
- **Execution path:** edge-input `"512mib"` → `re.match(r"([\d.]+)\s*([KMGTP]?i?B)", ..., re.IGNORECASE)` matches with `group(2)="mib"` → `units.get("mib", 1)` misses (dict keys `KiB/MiB/GiB/TiB/kB/MB/GB/B`) → fallback multiplier `1` → returns `512`.
- **Actual behavior:** Value silently under-reported by up to 6 orders of magnitude (bytes instead of mebibytes); no error, no log.
- **Expected behavior:** Unit lookup case-insensitive (`unit.lower()` against lowered dict) or raise/log on unknown unit instead of defaulting to ×1.
- **Impact:** Wrong memory figures flow into status metrics if any upstream/format variant changes casing (docker version drift, truncated/custom formats); thresholds compare apples to bytes.
- **Minimal fix:** `multiplier = units.get(unit, units.get(unit.lower().replace("ib","iB"), 0))` with `0 → log warning + return 0` semantics made explicit.
- **Required regression test:** Parametrized `("512MiB",536870912)`, `("512mib",536870912)`, `("",0)`, `("garbage",0)` — assert no silent ×1 fallback for matched-but-unknown units.

## BUG-1004

- **Severity:** LOW
- **Confidence:** 60%
- **File:** core/internal/scaffold/project_scaffolder.py:78–88
- **Symbol:** `auto_domain()`
- **Trigger:** `--domain` omitted AND `PLATFORM_DOMAIN` unset/empty (fresh dev machine, macOS, before node.yaml env is loaded).
- **Execution path:** edge-input `domain=""`, `platform_domain=None` → env fallback `os.environ.get("PLATFORM_DOMAIN", "")` → `""` → logs IMP:8 "Auto-domain skipped" → returns `""`.
- **Execution path continues:** caller persists scaffold plan / `.env.platform` with empty DOMAIN field (show_plan just omits the line, line 116–117) — project created with no domain and no error; surfaced only later when vhost/cert steps find nothing to bind.
- **Actual behavior:** Silent success of scaffolding with an unusable networking contract (empty domain).
- **Expected behavior:** Fail-fast (exit 4) when neither `--domain` nor `PLATFORM_DOMAIN` is resolvable, or at minimum a loud `[PRACTICES:...]`-style warning persisted into generated files.
- **Impact:** Projects provisioned without ingress; discovered at deploy time far from the cause.
- **Minimal fix:** Raise `ConfigValidationError` in `auto_domain()` when both sources are empty and caller context requires a domain.
- **Required regression test:** With `PLATFORM_DOMAIN` deleted from env → `new-project` must exit non-zero and create no project directory.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-1001 | HIGH | 90% | Auto-domain `f"{name}.{pd}"` accepts names (`_`, >63 chars) that `validate_vhost_identifiers` rejects later — permanent exit-4 vhost failure after project is already created |
| BUG-1002 | MEDIUM | 70% | `_get_health_status` maps "no healthcheck"/empty status to unhealthy, contradicting canonical criterion (none = healthy) |
| BUG-1003 | LOW | 55% (HYPOTHESIS) | `_parse_bytes` IGNORECASE regex + exact-case unit dict → silent ×1 fallback mis-scales non-canonical units |
| BUG-1004 | LOW | 60% | `auto_domain()` silently returns `""` when `PLATFORM_DOMAIN` unset — scaffold succeeds with empty domain contract |
