# GREP_SUMMARY: drift-report compose image network volume port drift detection
# STRUCTURE: ┌image drift┐ → network drift → volume drift → port drift → environment drift → appendix
# Drift Detection Report — docker-compose*.yml files
**Date:** 2026-07-17T22:43+03:00
**Scope:** 30 files (root + 12 modules × 2)
**Method:** Static analysis of image:, networks:, volumes:, ports:, environment:, services: blocks

---

## A. Image Version Drift — 0 drifts

All 22 `image:` declarations across 30 files:

| Image | File | Version |
|---|---|---|
| `backup-cron` (local build) | `backup-cron/base.yml:43` | `backup-cron:latest` |
| `clickhouse/clickhouse-server` | `clickhouse/base.yml:57` | `24.12@sha256:a65ca...` |
| `ghcr.io/tronyxlab/hermes-agent-context` | `hermes-agent/base.yml:64` | `${CONTEXT_IMAGE:-ghcr.io/...v2026.7.1}` |
| `hermes-agent-base` (local L1) | `platform-dev.yml:32` | `hermes-agent-base:latest` |
| `gcr.io/cadvisor/cadvisor` | `infra-metrics/base.yml:36` | `v0.55.1@sha256:3de2b...` |
| `prom/node-exporter` | `infra-metrics/base.yml:93` | `v1.12.0@sha256:9b0ad...` |
| `nginx/nginx-prometheus-exporter` | `infra-metrics/base.yml:140` | `1.5.1@sha256:9f6db...` |
| `oliver006/redis_exporter` | `infra-metrics/base.yml:189` | `v1.86.0@sha256:2e979...` |
| `langfuse/langfuse` | `langfuse/base.yml:28` | `3.212.0@sha256:35461...` |
| `redis` (in langfuse) | `langfuse/base.yml:121` | `7.4-alpine@sha256:6ab0b...` |
| `ghcr.io/berriai/litellm` | `litellm/base.yml:45` | `v1.91.2@sha256:f46d6...` |
| `grafana/loki` | `logging/base.yml:32` | `3.7.3@sha256:70b9f...` |
| `grafana/promtail` | `logging/base.yml:94` | `3.6.11@sha256:a761b...` |
| `minio/minio` | `minio/base.yml:31` | `latest@sha256:14cea...` |
| `minio/mc` | `minio/base.yml:71` | `latest@sha256:a7fe3...` |
| `prom/prometheus` | `monitoring/base.yml:32` | `v3.13.1@sha256:3c42b...` |
| `grafana/grafana` | `monitoring/base.yml:98` | `11.6.16@sha256:d67af...` |
| `nginx` | `nginx/base.yml:36` | `1.28-alpine@sha256:a8b39...` |
| `postgres` | `postgres/base.yml:32` | `16@sha256:17e67...` |
| `edoburu/pgbouncer` | `postgres/base.yml:79` | `${PGBOUNCER_IMAGE:-edoburu/pgbouncer:v1.25.2-p0@sha256:7d7a2...}` |
| `redis` (standalone) | `redis/base.yml:36` | `7.4-alpine@sha256:6ab0b...` |

**Duplicate check:** `redis:7.4-alpine@sha256:6ab0b6e738...` appears in `redis/base.yml:36` and `langfuse/base.yml:121` — **same version and digest. No drift.**

**Conclusion:** All same-named images use identical versions across files. No drift.

---

## B. Network Consistency

### Network usage matrix

| Network | Declared in root | Used by (file) |
|---|---|---|
| `proxy-net` | `docker-compose.yml:33` | `hermes-agent/base.yml`, `monitoring/base.yml`, `nginx/base.yml` |
| `shared-db-net` | `docker-compose.yml:34` | `backup-cron/base.yml`, `langfuse/base.yml`, `litellm/base.yml`, `minio/base.yml`, `postgres/base.yml` |
| `shared-cache-net` | `docker-compose.yml:35` | `infra-metrics/base.yml`, `redis/base.yml` |
| `hermes-agent-net` | `docker-compose.yml:36` | `hermes-agent/base.yml`, `litellm/base.yml` |
| `observability-net` | `docker-compose.yml:37` | `clickhouse/base.yml`, `hermes-agent/base.yml`, `infra-metrics/base.yml`, `langfuse/base.yml`, `litellm/base.yml`, `logging/base.yml`, `monitoring/base.yml`, `nginx/base.yml` |
| `backup-net` | `docker-compose.yml:38` | `backup-cron/base.yml`, `minio/base.yml` |

**Findings:**
- All 6 networks declared in root are consumed by ≥1 module ✓
- No module declares a network not listed in root ✓
- All network `name:` declarations match the compose key ✓

**Conclusion:** No network drift detected.

---

## C. Volume Consistency

### Named volumes declared in root vs. usage

| Volume | Declared in root | Used by service | Status |
|---|---|---|---|
| `postgres-data` | `docker-compose.yml:41` | `postgres/base.yml:60` | ✓ |
| `wal-archive` | `docker-compose.yml:42` | `postgres/base.yml:64` | ✓ |
| `redis-data` | `docker-compose.yml:43` | — | **❌ ORPHAN (see C1)** |
| `hermes-data` | `docker-compose.yml:44` | `hermes-agent/base.yml:174` | ✓ |
| `grafana-data` | `docker-compose.yml:45` | `monitoring/base.yml:141` | ✓ |
| `prometheus-data` | `docker-compose.yml:46` | `monitoring/base.yml:61` | ✓ |
| `loki-data` | `docker-compose.yml:47` | `logging/base.yml:55` | ✓ |
| `backup-spool` | `docker-compose.yml:48` | `backup-cron/base.yml:81` | ✓ |
| `backup-logs` | `docker-compose.yml:49` | `backup-cron/base.yml:82` | ✓ |
| `clickhouse-data` | `docker-compose.yml:50` | `clickhouse/base.yml:81` | ✓ |
| `minio-data` | `docker-compose.yml:51` | `minio/base.yml:57` | ✓ |

### Volume definition conflicts (root vs. module driver_opts)

Volumes defined in root as `{ driver: local }` but redefined with `driver_opts` (bind-mount) in modules:

| Volume | Root definition | Module definition (wins on merge) |
|---|---|---|
| `postgres-data` | `{ driver: local }` | `postgres/base.yml:114-120` — bind to `/var/lib/platform/postgres-data` |
| `wal-archive` | `{ driver: local }` | `postgres/base.yml:122-127` — bind to `/var/lib/platform/wal-archive` |
| `backup-spool` | `{ driver: local }` | `backup-cron/base.yml:104-110` — bind to `/var/lib/platform/backup-spool` |
| `backup-logs` | `{ driver: local }` | `backup-cron/base.yml:112-117` — bind to `/var/log/platform/backup` |
| `hermes-data` | `{ driver: local }` | `hermes-agent/base.yml:196-201` — bind to `/var/lib/platform/hermes-agent/data` |

These are NOT drifts — Docker Compose v2 deep-merges top-level volumes, and the more specific `driver_opts` definition wins. Root provides minimum definition for validation; modules add platform-specific bind paths.

### Internal volumes (module-only, not in root)

| Volume | Defined in | Status |
|---|---|---|
| `langfuse-redis-data` | `langfuse/base.yml:160` | ✓ internal to langfuse module |

### Test-only volumes

| Volume | Defined in | Status |
|---|---|---|
| `backup-spool-test` | `backup-cron/test.yml:48` | ✓ test-only |
| `backup-logs-test` | `backup-cron/test.yml:50` | ✓ test-only |
| `clickhouse-data-test` | `clickhouse/test.yml:45` | ✓ test-only |
| `hermes-data-test` | `hermes-agent/test.yml:38` | ✓ test-only |
| `postgres-data-test` | `postgres/test.yml:73` | ✓ test-only |
| `wal-archive-test` | `postgres/test.yml:75` | ✓ test-only |

### Bind mount path verification

All module-relative paths exist:
- `nginx/config/*.conf` ✓, `nginx/error-pages/` ✓, `nginx/dev-certs/` ✓, `nginx/dev-config/` ✓, `nginx/overlays/` ✓ (empty with .gitkeep)
- `postgres/config/*` ✓, `postgres/init/` ✓
- `clickhouse/config/config.d/*` ✓, `clickhouse/config/users.d/*` ✓
- All other modules' `./config/` paths ✓

**❌ C1 — Orphan volume redis-data**
**Severity:** MEDIUM
**File:** `docker-compose.yml:43`
**Details:** `redis-data` volume is declared in root compose but never referenced by any service. Redis was changed to cache-only (no persistence, no volume) per owner verdict wave-redis 2026-07-15, but the root volume declaration was not removed.
**Fix:** Remove `redis-data` from `docker-compose.yml` volumes section.

**❌ C2 — clickhouse/test.yml re-introduces directory bind for users.d/**
**Severity:** MEDIUM
**File:** `clickhouse/test.yml:42`
**Details:** `clickhouse/base.yml:83` explicitly uses per-file ro mount (`10-users.xml:ro`) to avoid default-user.xml leaking to host filesystem (TRAP comment lines 84-90). The test.yml overrides with directory bind (`users.d:/etc/clickhouse-server/users.d`). Evidence: `default-user.xml` already present in `clickhouse/config/users.d/` on host, confirming the leak.
**Fix:** Change `clickhouse/test.yml:42` to per-file mount matching base.yml pattern, or suppress with explicit TRAP rationale.

---

## D. Port Conflicts — 0 conflicts

### Production default ports (127.0.0.1 binding)

| Host Port | Service | File |
|---|---|---|
| 8080 | cadvisor | `infra-metrics/base.yml:48` |
| 9100 | node-exporter | `infra-metrics/base.yml:105` |
| 9113 | nginx-prometheus-exporter | `infra-metrics/base.yml:157` |
| 9119 | hermes-agent dashboard | `hermes-agent/base.yml:102` |
| 8642 | hermes-agent desktop | `hermes-agent/base.yml:103` |
| 3001 | langfuse | `langfuse/base.yml:41` |
| 4000 | litellm | `litellm/base.yml:63` |
| 3100 | loki | `logging/base.yml:47` |
| 9000 | minio S3 API | `minio/base.yml:36` |
| 9001 | minio console | `minio/base.yml:37` |
| 9090 | prometheus | `monitoring/base.yml:50` |
| 3000 | grafana | `monitoring/base.yml:111` |
| 80 / 443 | nginx | `nginx/base.yml:48-49` |

All host ports are unique. ✓

### Test ports (127.0.0.1 binding)

| Host Port | Service | File |
|---|---|---|
| 18081 | cadvisor-test | `infra-metrics/test.yml:25` |
| 19100 | node-exporter-test | `infra-metrics/test.yml:31` |
| 19113 | nginx-prometheus-exporter-test | `infra-metrics/test.yml:37` |
| 13000 | langfuse-test | `langfuse/test.yml:17` |
| 14000 | litellm-test | `litellm/test.yml:19` |
| 13100 | loki-test | `logging/test.yml:18` |
| 18080 / 18443 | nginx-test | `nginx/test.yml:20-21` |
| 19090 | prometheus-test | `monitoring/test.yml:18` |
| 13030 | grafana-test | `monitoring/test.yml:24` |
| 18123 / 19363 | clickhouse-test | `clickhouse/test.yml:36-37` |

All test host ports are unique. ✓

**Conclusion:** No port conflicts.

---

## E. Environment Variable Presence — 0 missing vars

Cross-referenced all `${VAR}` and `${VAR:-default}` references in compose files against `.env`.

All referenced variables are defined in `.env` (directly or via fallback). Specifically checked:

- `POSTGRES_PASSWORD` (no fallback) — `.env:21` ✓
- `S3_BUCKET` (no fallback in backup-cron) — `.env:51` ✓
- `NEXTAUTH_SECRET` (no fallback) — `.env:74` ✓
- `SALT` (no fallback) — `.env:76` ✓
- `LANGFUSE_INIT_USER_PASSWORD` (no fallback) — `.env:80` ✓
- `LITELLM_MASTER_KEY` (no fallback) — `.env:65` ✓
- `MINIO_ROOT_USER` (no fallback) — `.env:43` ✓
- `MINIO_ROOT_PASSWORD` (no fallback) — `.env:44` ✓

All env vars with defaults (`${X:-default}`) have fallback values that work in dev. ✓

**Conclusion:** No missing env vars.

---

## F. Service Naming Consistency — 0 drifts

| Logical service | base.yml name | test.yml name | Consistent? |
|---|---|---|---|
| backup-cron | `backup-cron` | `backup-cron` | ✓ |
| clickhouse | `clickhouse` | `clickhouse` | ✓ |
| hermes-agent | `hermes-agent` | `hermes-agent` | ✓ |
| cadvisor | `cadvisor` | `cadvisor` | ✓ |
| node-exporter | `node-exporter` | `node-exporter` | ✓ |
| nginx-prometheus-exporter | `nginx-prometheus-exporter` | `nginx-prometheus-exporter` | ✓ |
| redis-exporter | `redis-exporter` | `redis-exporter` | ✓ |
| langfuse | `langfuse` | `langfuse` | ✓ |
| langfuse-redis | `langfuse-redis` | — | ✓ (no test override needed) |
| litellm | `litellm` | `litellm` | ✓ |
| loki | `loki` | `loki` | ✓ |
| promtail | `promtail` | `promtail` | ✓ |
| minio | `minio` | `minio` | ✓ |
| minio-createbuckets | `minio-createbuckets` | — | ✓ (one-shot, no test override) |
| prometheus | `prometheus` | `prometheus` | ✓ |
| grafana | `grafana` | `grafana` | ✓ |
| nginx | `nginx` | `nginx` | ✓ |
| postgres | `postgres` | `postgres` | ✓ |
| pgbouncer | `pgbouncer` | `pgbouncer` | ✓ |
| redis | `redis` | `redis` | ✓ |

**Conclusion:** All service names are consistent between base.yml and test.yml within each module.

---

## G. Module Compose Detection — ALL 12 modules complete

| Module | `base.yml` | `test.yml` | Status |
|---|---|---|---|
| backup-cron | ✓ | ✓ | OK |
| clickhouse | ✓ | ✓ | OK |
| hermes-agent | ✓ | ✓ | OK |
| infra-metrics | ✓ | ✓ | OK |
| langfuse | ✓ | ✓ | OK |
| litellm | ✓ | ✓ | OK |
| logging | ✓ | ✓ | OK |
| minio | ✓ | ✓ | OK |
| monitoring | ✓ | ✓ | OK |
| nginx | ✓ | ✓ | OK |
| postgres | ✓ | ✓ | OK |
| redis | ✓ | ✓ | OK |

Root `docker-compose.yml:20-31` includes all 12 modules. ✓

**Conclusion:** No missing module compose files.

---

## Summary

| Section | Findings | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|---|
| A. Image version drift | 0 | 0 | 0 | 0 | 0 |
| B. Network consistency | 0 | 0 | 0 | 0 | 0 |
| C. Volume consistency | 2 | 0 | 0 | 2 | 0 |
| D. Port conflicts | 0 | 0 | 0 | 0 | 0 |
| E. Env var presence | 0 | 0 | 0 | 0 | 0 |
| F. Service naming | 0 | 0 | 0 | 0 | 0 |
| G. Module detection | 0 | 0 | 0 | 0 | 0 |
| **Total** | **2** | **0** | **0** | **2** | **0** |

### Items requiring action

**MEDIUM C1 — Orphan volume `redis-data`**
`docker-compose.yml:43` — declared but unused since redis became cache-only.
**Fix:** Remove line `redis-data: { driver: local }` from root docker-compose.yml.

**MEDIUM C2 — clickhouse/test.yml directory bind re-introduces users.d leak**
`clickhouse/test.yml:42` — uses directory bind for users.d, which base.yml explicitly avoids (per-file mount). `default-user.xml` already present on host filesystem at `config/users.d/`, confirming leak.
**Fix:** Change to per-file mount: `./config/users.d/10-users.xml:/etc/clickhouse-server/users.d/10-users.xml:ro`

### Items noted (no action required)
- All 12 modules have both base and test compose files ✓
- Root includes all 12 modules ✓
- 6 root networks all consumed; no orphan networks ✓
- All port mappings unique across production and test configurations ✓
- All env vars present in .env or have working defaults ✓
- Service names consistent across base/test overlays ✓
- Image versions consistent across files ✓
