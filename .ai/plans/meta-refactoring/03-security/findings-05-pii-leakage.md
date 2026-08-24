# Findings 05 — PII / sensitive data leakage & information disclosure

## SEC-0020 — LLM prompts/completions traced to Langfuse with unbounded retention — end-user PII accumulates indefinitely
- **Severity:** HIGH · **Attack surface:** langfuse UI (app-auth only, no nginx basic auth), ClickHouse default user, S3 bucket · **Confidence:** 0.85 · **Must fix before launch: YES** (only disclosure class with legal/data-protection exposure and no self-healing)
- **Files:** `core/internal/llm/config_renderer.py:279` (`"success_callback": ["prometheus","langfuse"]` — ships full input/output by default); `core/modules/langfuse/docker-compose.base.yml:54-73` (no `LANGFUSE_S3_EVENT_UPLOAD_RETENTION*` anywhere); grep TTL/retention across clickhouse config = 0 matches; nginx langfuse-vhost.conf:79-83 (no auth_basic)
- **Attack path:** any LLM project → LiteLLM callback persists prompt+completion (user PII) → ClickHouse rows + S3 blobs retained forever → any future cred leak exposes years of user content via UI or clickhouse-client.
- **Impact:** unbounded PII retention; contradicts platform's bounded posture elsewhere (Loki 7d, prometheus 15d).
- **Minimal fix:** TTL on Langfuse CH tables + S3 lifecycle/retention envs; decide trace-retention policy in secret-definitions; consider input-masking for consumer projects. (Availability angle of the same gap: SEC-0048.)
- **Regression test:** gate asserting clickhouse config.d carries a TTL block for langfuse DB and compose declares retention env.

## SEC-0021 — One shared Basic-Auth credential gates status-page inventory + Prometheus metrics + Loki (all container logs)
- **Severity:** MEDIUM · **Attack surface:** platform./prometheus./loki vhosts — all `.htpasswd-platform` from PLATFORM_MASTER_EMAIL/PASSWORD · **Confidence:** 0.9 · **Must fix:** NO (strong random cred + TLS + rate limit); recommended before external consumers
- **Files:** `core/modules/nginx/config/platform-vhost.conf.template:84-85`, `prometheus-vhost.conf:77-78` (comment: htpasswd-monitoring → platform unification), `loki-vhost.conf:74-75`; autogen `secrets_manager.py:742,751`
- **Preconditions:** single credential compromise (browser manager theft, phishing, accidental paste; same cred transits CI healthcheck curl per template :13).
- **Attack path:** obtain cred → loki query_range → full 7-day stdout of every container incl. project app logs (session_id/user_id structured metadata per alloy config) → PII, accidentally-logged DSNs, internal hostnames; same cred unlocks /status.json and all Prometheus series.
- **Minimal fix:** split htpasswd per surface or add read-only account for Loki/Prometheus.
- **Regression test:** gate asserting each public vhost references its own auth_basic_user_file (or explicit shared-cred allowlist).

## SEC-0022 — One S3 credential covers both Postgres dumps (all project DBs) and Langfuse trace blobs
- **Severity:** MEDIUM-LOW · **Confidence:** 0.9 · **Must fix:** NO
- **Files:** `core/modules/langfuse/docker-compose.base.yml:68-71` (`LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: "${S3_ACCESS_KEY:-…}"`, bucket defaults `${S3_BUCKET}`); `backup-cron/scripts/backup_config.py:14-22` (same env names)
- **Attack path:** leak langfuse container env → same key downloads nightly pgdumpall of every tenant DB; reverse direction reads LLM traces.
- **Minimal fix:** separate keys/prefix-scoped policies for backup-* vs langfuse-*.
- **Regression test:** compose-lint gate asserting LANGFUSE key does not default to ${S3_ACCESS_KEY}.

## SEC-0023 — `/status.json` + HTML disclose complete internal inventory (auth-gated)
- **Severity:** LOW-MEDIUM · **Confidence:** 0.95 · **Must fix:** NO
- **Files:** `core/modules/status-page/app.py:259-274` (fields: checks/containers/certs/projects/host/backup/errors/node); templates :292-310 (domains, cert issuer/expiry/**SAN**, images), :323-352 (container names, exit codes), :373-375 (**OS/kernel version**); collectors/checks/platform.py:134-141 (internal Docker-DNS targets in checks[] and 503 bodies)
- **Preconditions:** valid .htpasswd-platform cred (or SEC-0021 compromise). No auth bypass found.
- **Impact:** recon enrichment for cred holders: kernel for exploit matching, SANs enumerating unrelated domains, private project names via images.
- **Minimal fix:** drop san_full/errors[] from public JSON/HTML or gate /status.json+/metrics on separate read-only cred.
- **Regression test:** renderer context test asserting SAN/error-text absent when public_mode flag set.

## SEC-0024 — Grafana/Langfuse/Hermes surfaces rely solely on app-level auth; `LANGFUSE_INIT_USER_PASSWORD` missing from module.yaml#env_requires
- **Severity:** LOW · **Confidence:** 0.7 · **Must fix:** NO (defense-in-depth)
- **Files:** nginx config grafana-vhost.conf, langfuse-vhost.conf (no auth_basic); `langfuse/module.yaml:44-50` env_requires omits LANGFUSE_INIT_USER_PASSWORD while compose requires it non-optionally (`docker-compose.base.yml:52`) — enforcement falls to manifest runtime check only (`secret-definitions.yaml:249`); mitigations verified: AUTH_DISABLE_SIGNUP=true, no GF anonymous auth, hermes has own basic-auth layer with nginx intercept
- **Risk:** misconfiguration-induced weak/absent app auth on the surface storing SEC-0020's PII.
- **Minimal fix:** add LANGFUSE_INIT_USER_PASSWORD to env_requires (one line).
- **Regression test:** parity test: every `${VAR}` without :-default in a module compose must appear in that module's env_requires.

## SEC-0025 — `.env.platform` embeds the real project DB password — diverges from documented claim «пароль роли БД проекта — только в .platform-db.env»
- **Severity:** LOW · **Confidence:** 0.8 · **Must fix:** NO
- **Files:** `gen_env_platform.py:209-224`; claim at root AGENTS.md §Контракт окружения; mitigations verified: gitignored everywhere, payload-delivered only
- **Impact:** dev-machine file scan / accidental archive yields DSN with password (gitignored ≠ never copied). Choose one truth: keep `***` and inject node-side, or update AGENTS.md.
- **Regression test:** unit test pinning chosen `_apply_credentials_to_dsn` behavior + doc-parity check.

Checked-and-clean: telegram messages contain alertname/severity/summary + entity-escaped fields only (no emails/IPs/passwords/key prefixes; bot token never logged; egress forced via Tor/privoxy); error surfaces generic (500 JSON, PlatformFatalError CLI-only, nginx 444 unknown hosts, generic 50x pages); log hygiene sweep found no f-string logs interpolating DSN/token values; Loki retention bounded 168h+compactor; host collector carries no IPs; cAdvisor --docker_only, loopback binds; file_sd labels limited to project/type/node/service; S3 object names generic (no project leakage); gitleaks pre-commit + CI present.
