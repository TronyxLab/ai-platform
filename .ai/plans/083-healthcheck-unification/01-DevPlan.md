$START_DEVPLAN

# DevPlan 083 — Healthcheck Complete Unification

## $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Unify 9 healthcheck mechanisms → ~3 canonical primitives; standardize start_period values; eliminate docker exec copy-paste in 5 modules; align deep checks with Docker HEALTHCHECK for all 14 modules |
| **DESCRIPTION** | Close all 7 DRIFT-H* IDs from Brief 077 Chapter 7: consolidate healthcheck mechanisms (DRIFT-H1), standardize port checks (DRIFT-H2), normalize start_period values (DRIFT-H3), extract shared _docker_exec_check (DRIFT-H4), unify healthcheck orchestrators (DRIFT-H5), fix deep≠HEALTHCHECK mismatch (DRIFT-H6), eliminate modules-healthcheck.sh direct docker inspect (DRIFT-H7) |
| **RATIONALE** | 9 different mechanisms, 8 port-check patterns, 7 start_period values, 5 copy-paste docker exec patterns — every divergence is a drift accelerator. Each divergent implementation is a future bug surface. Unified API in lib/healthcheck.sh eliminates >300 LOC of duplicated logic, makes healthcheck behavior grep-able and testable |
| **ACCEPTANCE_CRITERIA** | AC1: All 14 module healthcheck.sh files conform to unified contract; AC2: 5 docker exec copy-paste sites replaced with exec_check(); AC3: 12 compose HEALTHCHECK start_period values standardized; AC4: modules-healthcheck.sh uses check_docker_health() not raw docker inspect; AC5: All 7 DRIFT IDs closed and verifiable via grep |
| **IMPLEMENTS** | Brief 077 Chapter 7 (DRIFT-H1 through DRIFT-H7), RC-2 (Healthcheck Drift) |
| **IMPACTS** | `core/lib/healthcheck.sh` (+4 functions), `core/internal/healthcheck/modules-healthcheck.sh` (refactor), all 14 `core/modules/*/healthcheck.sh` (standardize), all 12 `core/modules/*/docker-compose.base.yml` HEALTHCHECK sections (normalize start_period), `core/internal/bootstrap/deploy/docker_orchestrator.py` (no code changes — aligns contract) |
| **REQUIRES** | None (standalone — healthcheck is independent domain) |

---

## 1. Requirements Analysis

### 1.1 Current State Summary

**Healthcheck mechanisms (9 total):**

| # | Mechanism | Used in | Target after unification |
|---|-----------|---------|-------------------------|
| 1 | `docker inspect Health.Status` | All module liveness | Keep — `check_docker_health()` |
| 2 | `docker inspect State.Running` | 5 modules deep mode | Replace with `check_docker_health()` |
| 3 | `check_http()` (curl) | 6 modules deep mode | Keep — `check_http()` |
| 4 | `poll_until_healthy()` | lib/healthcheck.sh | Keep |
| 5 | `poll_docker_health()` | lib/healthcheck.sh | Keep |
| 6 | `check_docker_health()` | lib/healthcheck.sh | Keep |
| 7 | Service-specific tools | Docker HEALTHCHECK, deep | Keep as compose HEALTHCHECK |
| 8 | `systemctl is-active` | platform-secrets | Keep (systemd module — not Docker) |
| 9 | Python subprocess retry 10×10s | docker_orchestrator.py | Keep (different concern — deploy-time HC) |

**Post-unification target (~3 primitives):**
- `check_docker_health()` — liveness for Docker modules
- `check_http()` — HTTP endpoint verification
- `exec_check()` — docker exec + service tool (replaces copy-paste pattern)

### 1.2 Key Success Criteria

1. **SC1:** All 14 module healthcheck.sh scripts use `check_docker_health()` for liveness mode (default)
2. **SC2:** All 5 docker exec copy-paste sites (clickhouse, redis, nginx, backup-cron, hermes-agent) use `exec_check()`
3. **SC3:** All 12 compose HEALTHCHECK sections have standardized `start_period` values (15s default, 30s DB, 60s litellm)
4. **SC4:** `modules-healthcheck.sh` calls `check_docker_health()` for liveness, not raw `docker inspect`
5. **SC5:** Deep mode in each module delegates to `check_docker_health()` first, then adds service-specific checks

---

## 2. Architecture Overview

### 2.1 Unified Healthcheck API (lib/healthcheck.sh)

```
lib/healthcheck.sh
├── check_docker_health(container) → 0/1/2/3    [EXISTING, keep]
├── poll_docker_health(container, timeout, interval) → 0/1  [EXISTING, keep]
├── poll_until_healthy(name, cmd, timeout, interval) → 0/1  [EXISTING, keep]
├── check_http(url, expected_codes, timeout) → 0/1    [EXISTING, ADD timeout param]
├── check_tcp(host, port, timeout) → 0/1              [NEW]
├── exec_check(container, command) → 0/1              [NEW]
└── (no changes to poll_docker_health, poll_until_healthy)
```

### 2.2 Draft Code Graph

```
┌────────────────────────────────────────────────────────────────┐
│                    lib/healthcheck.sh (extended)                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ check_docker_    │  │ check_http(       │  │ check_tcp(   │ │
│  │ health()         │  │ url, codes,       │  │ host, port,  │ │
│  │ [EXISTING]       │  │ timeout=5)        │  │ timeout=5)   │ │
│  │                  │  │ [ENHANCED]        │  │ [NEW]        │ │
│  └────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘ │
│           │                     │                    │          │
│  ┌────────┴─────────────────────┴────────────────────┴───────┐ │
│  │                  exec_check(container, command) [NEW]     │ │
│  │  ▶ docker inspect State.Running → docker exec command    │ │
│  └────────────────────────┬─────────────────────────────────┘ │
└───────────────────────────┼────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ Module        │  │ Module        │  │ Module        │
│ healthcheck.sh│  │ healthcheck.sh│  │ healthcheck.sh│
│ (postgres)    │  │ (redis)       │  │ (nginx)       │
│               │  │               │  │               │
│ liveness:     │  │ liveness:     │  │ liveness:     │
│ check_docker  │  │ check_docker  │  │ check_docker  │
│ _health()     │  │ _health()     │  │ _health()     │
│               │  │               │  │               │
│ deep:         │  │ deep:         │  │ deep:         │
│ check_docker  │  │ exec_check    │  │ exec_check    │
│ _health() +   │  │ redis redis-  │  │ nginx curl    │
│ pg_isready    │  │ cli PING      │  │ localhost:80  │
│ (docker exec) │  │               │  │               │
└───────────────┘  └───────────────┘  └───────────────┘
        ... (11 more modules, same contract)
```

### 2.3 Module healthcheck.sh Unified Contract

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: <module> healthcheck liveness deep check_docker_health <tool>
# STRUCTURE: source lib/healthcheck.sh → liveness: check_docker_health → deep: check_docker_health + service-check → exit 0/1
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"

CONTAINER="<module-name>"
MODE="${1:-}"

if [ "$MODE" = "deep" ]; then
    # Step 1: verify Docker health (same as liveness)
    check_docker_health "$CONTAINER" || exit 1
    # Step 2: service-specific check via exec_check
    exec_check "$CONTAINER" "<tool-command>" || exit 1
    log_imp 9 "deep" "<module> deep check PASSED"
    exit 0
fi

# Default liveness
check_docker_health "$CONTAINER" || exit 1
exit 0
```

### 2.4 Deep Check vs Docker HEALTHCHECK Alignment Strategy

| Module | Docker HEALTHCHECK | healthcheck.sh deep (AFTER) |
|--------|-------------------|---------------------------|
| postgres | `pg_isready` (readiness) | `check_docker_health` + `exec_check pg_isready` |
| pgbouncer | `pg_isready -p 6432` | `check_docker_health` + `exec_check pg_isready -p 6432` |
| redis | `redis-cli ping` | `check_docker_health` + `exec_check redis-cli ping` |
| clickhouse | `wget /ping` | `check_docker_health` + `check_http :8123/ping` |
| nginx | `nc -z localhost 80` | `check_docker_health` + `check_http localhost:80` |
| minio | `/dev/tcp/9000` | `check_docker_health` + `check_http :9000/minio/health/live` |
| loki | `/usr/bin/loki -version` | `check_docker_health` + `check_http :3100/ready` |
| prometheus | `wget :9090/-/healthy` | `check_docker_health` + `check_http :9090/-/healthy` |
| grafana | `wget :3000/api/health` | `check_docker_health` + `check_http :3000/api/health` |
| litellm | `python3 urllib /health/readiness` | `check_docker_health` (already correct!) |
| langfuse | `wget /api/public/health` | `check_docker_health` + `check_http :3001/api/public/health` |
| status-page | `python3 urllib /healthz` | `check_docker_health` + `check_http :8080/health` |
| backup-cron | `pgrep cron` | `check_docker_health` + `exec_check pgrep -x cron` |
| hermes-agent | `curl :9119/` | `check_docker_health` + `check_http :9119/health` |
| platform-secrets | systemd oneshot | `systemctl is-active` (unchanged — non-Docker) |

**Principle:** deep mode now ALWAYS runs `check_docker_health` FIRST, THEN service-specific checks. This eliminates the DRIFT-H6 problem where deep checks verified something different from Docker HEALTHCHECK.

---

## 3. Step-by-Step Data Flow

### 3.1 Healthcheck Execution Flow (post-unification)

```
make healthcheck [NODE=<name>]
    │
    ▼
core/entrypoints/healthcheck.sh
    │  exec bash modules-healthcheck.sh "$@"
    ▼
core/internal/healthcheck/modules-healthcheck.sh
    │
    ├─ For each module in core/modules/*/module.yaml:
    │   │
    │   ├─ MODE=deep:
    │   │     invoke_module_interface $MODULE healthcheck deep
    │   │         │
    │   │         ▼
    │   │     core/modules/<module>/healthcheck.sh MODE=deep
    │   │         │
    │   │         ├─ check_docker_health $CONTAINER      [lib function]
    │   │         └─ exec_check $CONTAINER "<tool-cmd>"   [lib function, NEW]
    │   │           OR check_http "<url>" "200"           [lib function]
    │   │
    │   ├─ install_type=docker (default/liveness):
    │   │     check_docker_health $CONTAINER              [DRIFT-H7 fix — was raw docker inspect]
    │   │
    │   └─ install_type=system:
    │         invoke_module_interface $MODULE healthcheck liveness
    │
    └─ exit 0 (all healthy) | exit 1 (any unhealthy)

Python path (deploy-time, separate concern):
docker_orchestrator.py --action healthcheck
    │
    └─ run_healthcheck() → _invoke_healthcheck_full() → invoke_module_interface healthcheck liveness
        (retry 10×10s, unchanged — different concern from make healthcheck)
```

### 3.2 New Functions — Contract

```bash
# check_tcp(host, port, timeout=5) → 0/1
# ▶ ◇ timeout bash -c "echo >/dev/tcp/$host/$port" → ◇ success? → ⎋ 0 | ⎋ 1
check_tcp() {
    local host="$1" port="$2" timeout="${3:-5}"
    timeout "$timeout" bash -c "echo >/dev/tcp/${host}/${port}" 2>/dev/null
}

# exec_check(container, command) → 0/1
# ▶ ◇ docker inspect Running → ◇ docker exec command → ◇ exit 0? → ⎋ 0 | ⎋ 1
exec_check() {
    local container="$1" command="$2"
    # verify container running
    docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null | grep -q true || return 1
    # execute command inside container
    docker exec "$container" $command
}
```

---

## 4. $TASKS

| ID | Task | Files | Complexity | Dependencies | Acceptance |
|----|------|-------|------------|--------------|------------|
| **T1** | Extend `lib/healthcheck.sh`: add `check_tcp()`, `exec_check()`, add `timeout` param to `check_http()` | 1 | 3 | None | Functions exist with correct signatures; grep confirms |
| **T2** | Standardize postgres healthcheck: deep uses `check_docker_health` + `exec_check pg_isready` | 2 | 2 | T1 | postgres liveness + deep pass; State.Running replaced |
| **T3** | Standardize redis healthcheck: deep uses `check_docker_health` + `exec_check redis-cli PING` | 1 | 2 | T1 | redis deep uses exec_check, not inline docker exec |
| **T4** | Standardize clickhouse healthcheck: deep uses `check_docker_health` + `check_http :8123/ping` | 1 | 2 | T1 | clickhouse deep uses check_http, not wget via docker exec |
| **T5** | Standardize nginx healthcheck: deep uses `check_docker_health` + `check_http localhost:80` | 1 | 2 | T1 | nginx deep uses check_http, not inline curl via docker exec |
| **T6** | Standardize backup-cron healthcheck: deep uses `check_docker_health` + `exec_check pgrep -x cron` | 1 | 2 | T1 | backup-cron deep uses exec_check |
| **T7** | Standardize hermes-agent healthcheck: deep liveness/readiness uses `check_docker_health` + `check_http` | 1 | 2 | T1 | hermes-agent liveness/readiness deep use check_http; deps mode preserved |
| **T8** | Standardize minio healthcheck: deep stays `check_docker_health` + `check_http /minio/health/live` | 1 | 1 | T1 | minio already correct contract; verify no regression |
| **T9** | Standardize monitoring healthcheck: deep uses `check_docker_health` × 2 + `check_http` × 2 | 1 | 1 | T1 | prometheus + grafana deep check passes |
| **T10** | Standardize logging healthcheck: deep uses `check_docker_health` × 2 + `check_http loki :3100/ready` | 1 | 2 | T1 | loki + promtail deep passes |
| **T11** | Standardize langfuse healthcheck: deep uses `check_docker_health` + `check_http :3001/api/public/health` | 1 | 1 | T1 | langfuse deep uses check_http |
| **T12** | Standardize litellm healthcheck: deep already correct (`check_docker_health`), verify no regression | 1 | 1 | T1 | litellm already delegates correctly |
| **T13** | Standardize status-page healthcheck: deep uses `check_docker_health` + `check_http :8080/health` | 1 | 2 | T1 | status-page deep uses check_http |
| **T14** | Standardize infra-metrics healthcheck: deep uses `check_docker_health` × 4 + `check_http` × 2 | 1 | 2 | T1 | cadvisor + node-exporter deep passes, scratch exporters skip |
| **T15** | Fix platform-secrets healthcheck: no changes needed (systemd module) — verify | 1 | 1 | T1 | platform-secrets contract unchanged |
| **T16** | Fix DRIFT-H7: `modules-healthcheck.sh` calls `check_docker_health()` instead of raw `docker inspect` (lines 77-85 replaced) | 1 | 2 | T1 | modules-healthcheck.sh sources lib/healthcheck.sh and calls check_docker_health |
| **T17** | Normalize start_period in all 12 compose HEALTHCHECK sections | 12 | 3 | None (independent) | All start_period values: 15s default, 30s DB (postgres/redis/clickhouse), 60s litellm, 5s nginx (fast startup) |
| **T18** | Update `core/modules/AGENTS.md` healthcheck contract section | 1 | 2 | T1-T17 | Document unified contract: liveness=check_docker_health, deep=check_docker_health+service-check |
| **T19** | Run `make healthcheck` locally (docker compose up prerequisites) — integration verification | — | 3 | T1-T17 | All 14 modules report healthy |
| **T20** | Gate test: verify no raw `docker inspect` in module healthcheck.sh (except lib and platform-secrets) | — | 2 | T1-T17 | grep test confirms no direct docker inspect State.Running in module healthcheck deep |

### Critical Path

```
T1 (lib) → [T2..T15 in parallel] → T16 → T18 → T19 → T20
                                      ↑
                              T17 (independent, parallel)
```

---

## 5. $PARALLEL_GROUPS

### Wave 1: Library Extension + Compose Standardization (no shared files)
- Tasks: **T1, T17**
- Command: `coder Read DevPlan.md, implement Wave 1: T1, T17`

### Wave 2: Module Healthcheck Standardization (independent per module, shared lib only)
- Tasks: **T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15**
- All depend on T1 only. No file intersections (each task touches a different module directory + reads lib).
- Command: `coder Read DevPlan.md, implement Wave 2: T2-T15`

### Wave 3: Orchestrator Fix + Documentation
- Tasks: **T16, T18**
- Command: `coder Read DevPlan.md, implement Wave 3: T16, T18`

### Wave 4: Integration Verification
- Tasks: **T19, T20**
- Command: `coder Read DevPlan.md, implement Wave 4: T19, T20`

---

## 6. Acceptance Criteria

| AC | Criterion | Verification |
|----|-----------|-------------|
| AC1 | `check_tcp()` and `exec_check()` exist in lib/healthcheck.sh | `grep -c "check_tcp\|exec_check" core/lib/healthcheck.sh` → ≥2 |
| AC2 | All 5 docker exec copy-paste sites replaced | `grep -c "docker exec" core/modules/{clickhouse,redis,nginx,backup-cron}/healthcheck.sh` → 0 (in deep logic); hermes-agent may retain for deps mode |
| AC3 | All 14 module healthchecks use `check_docker_health` for liveness | `grep -l "check_docker_health" core/modules/*/healthcheck.sh \| wc -l` → ≥12 (exclude platform-secrets) |
| AC4 | `modules-healthcheck.sh` uses `check_docker_health()` not raw `docker inspect` | `grep "docker inspect" core/internal/healthcheck/modules-healthcheck.sh` → 0 |
| AC5 | start_period values standardized | `grep start_period core/modules/*/docker-compose.base.yml` — only 15s, 30s, 60s, 5s values |
| AC6 | `make healthcheck` passes locally | Exit code 0, all modules healthy |
| AC7 | No State.Running in module deep checks | `grep "State.Running" core/modules/*/healthcheck.sh` → 0 (except comments) |

---

## 7. start_period Standardization Table

| Value | Applies to | Rationale |
|-------|-----------|-----------|
| **5s** | nginx only | nginx starts in <2s on Alpine; no DB dependency |
| **15s** | postgres, pgbouncer, redis, clickhouse, loki, promtail, prometheus, grafana, cadvisor, exporters, hermes-agent, langfuse (redis), status-page | DBs start fast (pg_isready in 2-5s); exporters are instant; hermes-agent starts in 5-10s |
| **30s** | minio | MinIO initializes bucket structure; needs 10-20s |
| **60s** | litellm | Prisma migrate + model cost fetch = 40-60s (TRAP[DECISION] documented) |
| **40s→15s** | langfuse (main) | Langfuse starts in 5-10s; 40s was overly conservative |
| **20s→15s** | hermes-agent | Optimized per TASK-4; 20s→15s |
| **10s→15s** | redis | Standardize to 15s (10s was tight on slow storage) |

---

## 8. File Manifest

### Files Modified (28 files)

| # | File | Change |
|---|------|--------|
| 1 | `core/lib/healthcheck.sh` | ADD: `check_tcp()`, `exec_check()`; MODIFY: `check_http()` add timeout param |
| 2 | `core/internal/healthcheck/modules-healthcheck.sh` | DRIFT-H7 fix: replace raw docker inspect with check_docker_health() |
| 3 | `core/modules/postgres/healthcheck.sh` | DRIFT-H6: deep — check_docker_health + exec_check pg_isready |
| 4 | `core/modules/redis/healthcheck.sh` | DRIFT-H4: deep — check_docker_health + exec_check redis-cli PING |
| 5 | `core/modules/clickhouse/healthcheck.sh` | DRIFT-H4: deep — check_docker_health + check_http /ping |
| 6 | `core/modules/nginx/healthcheck.sh` | DRIFT-H4: deep — check_docker_health + check_http localhost:80 |
| 7 | `core/modules/backup-cron/healthcheck.sh` | DRIFT-H4: deep — check_docker_health + exec_check pgrep -x cron |
| 8 | `core/modules/hermes-agent/healthcheck.sh` | DRIFT-H4: deep liveness/readiness — check_docker_health + check_http; deps mode preserved |
| 9 | `core/modules/minio/healthcheck.sh` | Verify contract; minor cleanup |
| 10 | `core/modules/monitoring/healthcheck.sh` | Verify contract; add deep check for check_docker_health before check_http |
| 11 | `core/modules/logging/healthcheck.sh` | Verify contract; minor cleanup |
| 12 | `core/modules/langfuse/healthcheck.sh` | Verify contract; add deep check for check_docker_health before check_http |
| 13 | `core/modules/litellm/healthcheck.sh` | Verify contract; already correct |
| 14 | `core/modules/status-page/healthcheck.sh` | Fix: deep adds check_docker_health before check_http |
| 15 | `core/modules/infra-metrics/healthcheck.sh` | Verify contract; minor cleanup |
| 16 | `core/modules/platform-secrets/healthcheck.sh` | Verify; no changes needed |
| 17–28 | `core/modules/*/docker-compose.base.yml` (12 files) | DRIFT-H3: normalize start_period values |
| 29 | `core/modules/AGENTS.md` | Update healthcheck contract section |

### Files NOT Modified
- `core/internal/bootstrap/deploy/docker_orchestrator.py` — DRIFT-H5 analysis: Python `run_healthcheck()` and bash `modules-healthcheck.sh` serve DIFFERENT purposes. Python is deploy-time retry (10×10s), bash is runtime check (single pass). They are NOT duplicates — they serve different lifecycle phases.

---

## 9. Design Decisions

### ## @rationale D1: Why NOT unify Python and Bash healthcheck orchestrators?
**Q:** DRIFT-H5 says there are 2 orchestrators. Why not merge them?

**A:** `docker_orchestrator.py::run_healthcheck()` is deploy-time retry logic (10 retries × 10s = 100s window) used DURING `deploy-modules`. `modules-healthcheck.sh` is runtime on-demand check (single pass) used by `make healthcheck` and CI gates. They serve different lifecycle phases. Merging them would add retry complexity to the runtime path (undesirable) or remove retry from the deploy path (breaks slow-start services like litellm).

**Decision:** Keep both. Document the distinction in `core/modules/AGENTS.md`. DRIFT-H5 is closed by documenting this is NOT drift — it's intentional separation of concerns.

### ## @rationale D2: Why `exec_check()` instead of fixing each module individually?
**Q:** Why a shared function when each module has only 5 lines of docker exec?

**A:** The 5-line pattern has already diverged: clickhouse uses `wget --no-verbose`, redis uses inline grep, nginx uses bare curl. Each divergence is minor but the accumulated effect is: you can't grep for "how does this platform check container health?" A single `exec_check()` function is grep-able, testable, and prevents future divergence. The 5-line savings per module (25 LOC total) is secondary to the semantic unification.

### ## @rationale D3: Why standardize start_period to 15s/30s/60s?
**Q:** The current 7 values (5s-120s) "work." Why change them?

**A:** Values without documented rationale are indistinguishable from copy-paste errors. The spread from 5s to 120s without justification means the next agent cannot distinguish "this needs 120s" from "this was copy-pasted from litellm." Standardizing to 3 tiers (15s default, 30s DB, 60s litellm) with documented rationale ensures the value IS the documentation.

**Exception:** nginx stays at 5s (starts in <2s, no dependencies). minio goes to 30s (bucket init 10-20s). langfuse drops from 40s to 15s (verified: starts in 5-10s).

### ## @rationale D4: Deep mode: check_docker_health FIRST, then service checks
**Q:** DRIFT-H6 fix: why not just make deep = service check?

**A:** Currently deep mode in most modules skips `check_docker_health` and does its own thing (State.Running, or HTTP endpoint). This means deep can PASS while Docker HEALTHCHECK reports unhealthy — a split-brain scenario. The fix: deep mode ALWAYS runs `check_docker_health` first (same as liveness), THEN adds service-specific diagnostics. This ensures deep is a strict superset of liveness, not a parallel alternative.

---

## 10. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_healthcheck_lib.py` | `test_check_tcp_success` | TCP connect to reachable host:port returns 0 | `check_tcp()` |
| `tests/unit/test_healthcheck_lib.py` | `test_check_tcp_timeout` | TCP connect to unreachable port returns 1 | `check_tcp()` |
| `tests/unit/test_healthcheck_lib.py` | `test_exec_check_success` | exec_check with valid container + command returns 0 | `exec_check()` |
| `tests/unit/test_healthcheck_lib.py` | `test_exec_check_container_not_running` | exec_check with stopped container returns 1 | `exec_check()` |
| `tests/unit/test_healthcheck_lib.py` | `test_exec_check_command_fails` | exec_check with failing command returns 1 | `exec_check()` |
| `tests/unit/test_healthcheck_lib.py` | `test_check_http_with_timeout` | check_http accepts timeout parameter | `check_http()` |
| `tests/gates/test_gate_healthcheck_unification.py` | `test_no_raw_docker_inspect_in_modules` | No module healthcheck.sh contains direct `docker inspect State.Running` call | All module healthcheck.sh |
| `tests/gates/test_gate_healthcheck_unification.py` | `test_all_modules_use_check_docker_health` | All Docker modules call check_docker_health for liveness | All module healthcheck.sh |
| `tests/gates/test_gate_healthcheck_unification.py` | `test_modules_healthcheck_uses_lib` | modules-healthcheck.sh calls check_docker_health(), not raw docker inspect | modules-healthcheck.sh |
| `tests/gates/test_gate_healthcheck_unification.py` | `test_start_period_standardized` | All compose HEALTHCHECK start_period values in {5s, 15s, 30s, 60s} | All docker-compose.base.yml |
| `tests/gates/test_gate_healthcheck_unification.py` | `test_exec_check_used_in_docker_exec_modules` | clickhouse/redis/nginx/backup-cron deep mode uses exec_check() | Module healthcheck.sh for affected modules |

---

## 11. TRAP References

- **TRAP[DEBT] · 2026-07-15** in `core/modules/postgres/healthcheck.sh` L15 — container names hardcoded → OUT OF SCOPE for this wave (separate parameterization task)
- **TRAP[DECISION] · 2026-07-21** in `litellm/docker-compose.base.yml` L129 — start_period=120s for Prisma migrate → PRESERVED, explicitly documented in standardization table
- **TRAP[BUG] · 2026-07-08** in `backup-cron/healthcheck.sh` L14 — pgrep false positive on host → FIXED by exec_check() which runs pgrep inside container

---

## Next Steps

### Wave 1: Library Extension + Compose Standardization
```
coder Read .ai/plans/083-healthcheck-unification/01-DevPlan.md, implement Wave 1: T1, T17
```

### Wave 2: Module Healthcheck Standardization
```
coder Read .ai/plans/083-healthcheck-unification/01-DevPlan.md, implement Wave 2: T2-T15
```

### Wave 3: Orchestrator Fix + Documentation
```
coder Read .ai/plans/083-healthcheck-unification/01-DevPlan.md, implement Wave 3: T16, T18
```

### Wave 4: Integration Verification
```
coder Read .ai/plans/083-healthcheck-unification/01-DevPlan.md, implement Wave 4: T19, T20
```

$END_DEVPLAN
