# Findings 10 — DoS / resource limits / rate limiting

Checked-and-clean (verified with values): limit_req 10 r/s/IP burst=20 nodelay on ALL infra vhosts AND generated tenant vhosts; client_max_body_size at nginx default 1m everywhere (no overrides); payload cap streamed reject-before-extract at 1 GiB default; per-project deploy lock non-blocking flock fail-fast; parallel deploy bounded DEFAULT_PARALLEL_LIMIT=4; healthcheck polling windowed 20×3s with split URL budgets; watchdog flock -n + timeout 50, 10-min threshold, 30-min restart cooldown, crash-loop stop; docker_logs fixed --tail 400; prometheus tsdb retention 15d; loki 7d + compactor; backup spool deleted post-upload + daily >7d sweep; WAL local delete only after S3 HEAD confirm; S3-side 7/28/90 retention; sshd MaxStartups 30:50:200 + LoginGraceTime ceiling; platform module memory ceilings sum ≈10.5G + HighMemory alert + zram.

## SEC-0045 — Single ingress: no per-IP connection cap, no slow-client timeouts → whole-node connection-slot exhaustion
- **Severity:** HIGH · **Attack surface:** the only public entrypoint (nginx 80/443 serving all platform + tenant vhosts from one process group) · **Confidence:** 0.9 · **Must fix before launch: YES** — cheapest full-outage vector
- **Files:** `core/modules/nginx/config/nginx.conf:29` (`worker_connections 1024`), `:105-106` (only limit_req_zone defined; `limit_conn` = 0 matches repo-wide); grep client_header_timeout/client_body_timeout/send_timeout across config + vhost_renderer = 0 matches (defaults 60s resettable by trickling bytes); amplifiers `langfuse-vhost.conf:62,68`, `grafana-vhost.conf:61,67` (`proxy_buffering off` + `proxy_read_timeout 3600s`)
- **Preconditions:** none — unauthenticated internet-facing TCP/TLS.
- **Attack path:** ~2–4k slowloris sockets exhaust the slot budget (2-4 vCPU VPS) → accept() stalls → ALL vhosts die simultaneously: tenant apps, platform dashboard, CI health probes; or slow-read against grafana/langfuse holds upstream connections up to 1h each.
- **Impact:** total outage of every hosted service from one modest box.
- **Minimal fix:** `limit_conn_zone`+`limit_conn perip 20`; `client_header_timeout 10s`, `client_body_timeout 10s`, `send_timeout 30s`, `keepalive_timeout 15s`; drop SSE proxy_read_timeout ≤300s or dedicated location.
- **Regression test:** gate asserting every rendered vhost contains limit_conn + client timeouts (extends vhost-contract gate pattern).

## SEC-0046 — Receive channel: 1 GiB RAM accumulation per receive + decompression bomb into root-fs staging
- **Severity:** HIGH (requires compromised/legit-but-malicious CI_DEPLOY_KEY — keys exist ×N repos) · **Confidence:** 0.85 · **Must fix before launch: YES**
- **Files:** cap exists pre-extract and streams (`receive_flow.py:99` `_DEFAULT_MAX_PAYLOAD_BYTES = 1024**3`) BUT fully buffered in RAM first (`:172-187` `_read_stdin_limited` accumulates chunks then joins → up to 1 GiB heap per concurrent receive); extraction without expansion cap (`:316-325` `tar.extractall(filter="data")` — no uncompressed-size/count/ratio check); staging on root fs (`:530` mkdtemp prefix deploy-receive- in /tmp — same fs as postgres data/docker layers); concurrency bounded only by sshd MaxStartups 200
- **Attack path:** 1 MiB of zeros compresses ~1000:1 → 1-GiB-compliant payload expands to hundreds of GB → extractall fills /tmp until ENOSPC mid-extract → same-fs casualties (postgres WAL/dumps, docker layers, Loki/Prometheus TSDB) = node-wide outage; variant: N parallel receives × 1 GiB RAM spike.
- **Minimal fix:** stream-extract member-by-member with running uncompressed-byte ceiling (~200 MB) + entry-count cap; lower default payload cap ~64 MiB; statvfs guard before extract.
- **Regression test:** 50 KB tar.gz of zeros expanding to 1 GB → rejected with size error, staging cleaned; parallel-receive memory assertion.

## SEC-0047 — Tenant compose resource limits: L1 checks PRESENCE only — a tenant can legitimately claim the whole node
- **Severity:** MEDIUM-HIGH · **Confidence:** 0.95 · **Must fix before launch: YES** (trivial to implement; prevents accidental multi-tenant outages too)
- **Files:** explicit TRAP[DECISION] `verify_contracts.py:572-580` («limits-present: проверка НАЛИЧИЯ, не значений» — value validation rejected); `_check_limits_present`:593-625 (only `"memory" in limits and "cpus" in limits`); no aggregate capacity check anywhere (grep MemTotal/capacity = 0); platform reference sum ≈10.5G
- **Preconditions:** none beyond normal tenant workflow — `memory: 32G, cpus: "8"` passes L1 and deploys.
- **Attack path:** tenant claims node-sized limits → under load host overcommits → kernel OOM killer picks high-activity victims first (postgres), then small platform modules (nginx/status-page 128–256M) → watchdog restart storm amplifies pressure.
- **Minimal fix:** add L1/L2 bounds: memory ≤ ~25% of node MemTotal (docker info once), cpus ≤ 2; block above via [PRACTICES:BLOCK].
- **Regression test:** staging compose with `memory: 999G` → verify_project_contracts returns blocking limits-value finding.

## SEC-0048 — Langfuse→ClickHouse tables have no TTL; clickhouse-data volume unbounded — slow-motion disk fill
- **Severity:** MEDIUM · **Confidence:** 0.85 · **Must fix:** NO (DiskSpace alert covers launch window; schedule within first month). Availability angle of SEC-0020 — non-adversarial exhaustion path.
- **Files:** grep TTL across clickhouse/ + langfuse/ modules = 0 matches; no retention env (`langfuse/docker-compose.base.yml:65-74`); volume unbounded (default local driver, no quota); sole mitigation reactive alert `node_filesystem_avail_bytes < 0.2` (`alert-rules.yml:243`)
- **Attack path:** active LLM usage → traces insert continuously → disk fills → CH inserts fail, langfuse ingest errors, 03:00 UTC pg dumps start failing → RPO silently degrades while services look healthy.
- **Minimal fix:** TTL on langfuse trace tables via clickhouse init SQL (e.g., 30 DAY) or scheduled prune in observability converge.
- **Regression test:** init-SQL gate asserting every merge-tree table carries a TTL expression.

## SEC-0049 — Backup/WAL pipeline: unbounded wal-archive growth during S3 outage + backup crontab runs without flock
- **Severity:** LOW-MEDIUM · **Confidence:** 0.75 · **Must fix:** NO (rare double condition; monitoring exists)
- **Files:** safe-delete by design `backup-cron/scripts/wal_sync.py` (@invariant D3 — local WAL removed only if HEAD-confirmed in S3 ⇒ prolonged outage grows wal-archive at full rate, no local cap); retry loop blocks 90 min inside one run (`upload.py:110-117`: 3 × 1800s); container crontab has NO flock/timeout (`backup-cron/scripts/crontab`; documented «parallel start allowed in v1») vs host crons protected with flock -n + timeout
- **Attack path:** sustained S3 degradation → overlapping hourly wal_sync runs multiply → wal-archive + CPU/bandwidth waste compounds SEC-0048's failure mode.
- **Minimal fix:** flock -n on crontab lines; absolute local cap on wal-archive du with loud IMP:10 (explicit RPO trade-off decision).
- **Regression test:** simulate HEAD-failure runs → assert growth flagged past configured byte cap.
