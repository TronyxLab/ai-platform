# Findings 08 — Network / API boundaries

Checked-and-clean (verified): production port-publish audit — every base compose bind except nginx ingress loopback-pinned (litellm/langfuse/hermes/minio/loki/prometheus/grafana/cadvisor/exporters all 127.0.0.1; postgres/pgbouncer/redis/status-page publish nothing); MODULE_PORTS_DENY covers published ports; DOCKER-USER FORWARD policy closes ufw-bypass for accidental publishes; SSRF sweep clean — HealthcheckPoller builds URLs from kebab-validated names host-side (dead path → falls back to inspect), telegram fixed api.telegram.org, no user-controlled URL fetchers; TLS verification defaults everywhere (no verify=False/CERT_NONE/curl -k in prod paths; loadtest gated behind LT_SSL_VERIFY=false); proxy headers complete on platform+generated vhosts, rate-limit keys on $binary_remote_addr never forwarded headers, logs record $remote_addr; no CORS emitted anywhere; DNS-as-auth-boundary absent (pgbouncer scram, litellm keys, htpasswd).

## SEC-0034 — Data-plane services (LiteLLM, Langfuse, MinIO) reachable by every tenant on shared-db-net — contradicts documented hermes-agent-net isolation; facade URL port broken
- **Severity:** HIGH · **Attack surface:** cross-tenant network reachability · **Confidence:** 0.9 · **Must fix before launch: YES** (stated security boundary does not exist for multi-tenant deployments)
- **Files:** SoT claim `core/platform-infra.yaml:121-145` (provides.*.networks: [hermes-agent-net]); runtime: `core/modules/litellm/docker-compose.base.yml:119-126` (attached shared-db-net), `langfuse/docker-compose.base.yml:122-128` (langfuse+worker on shared-db-net), `minio/docker-compose.base.yml:38-45` (backup-net + shared-db-net); tenant join `templates/template-backend/docker-compose.yml:43-47` + allowlist `practices_manifest.yaml:33-39`; intra-bridge traffic never traverses DOCKER-USER/ufw (`docker_user_policy.py:94-102` governs FORWARD between bridges only)
- **Preconditions:** any deployed backend project (default template topology). No compromise needed.
- **Attack path:** tenant container on shared-db-net → `curl http://minio:9000` (root S3 API surface incl. backup bucket with pg_dumpall dumps + trace blobs), `http://langfuse:3000`, `http://litellm:4000` — full unauthenticated API surfaces exposed tenant-wide without nginx TLS/rate-limit; credential probing outside any limit_req zone.
- **Bonus defect:** emitted `PLATFORM_LANGFUSE_URL=http://langfuse:3001` targets the HOST publish port — unreachable in-container (container listens :3000); facade contract broken.
- **Impact:** invalidates root AGENTS.md network canon («hermes-agent-net: hermes-agent, litellm, langfuse, minio, clickhouse»); one compromised tenant container gets network reach into backup + trace stores.
- **Minimal fix:** move langfuse/minio off shared-db-net onto hermes-agent-net per SoT (pgbouncer stays sole tenant-facing service there) OR update SoT+AGENTS.md declaring exposure intentional and add auth boundaries; fix PLATFORM_LANGFUSE_URL port.
- **Regression test:** manifest-parity gate parsing every module compose: services named in provides.*.networks attach exactly to the declared set.

## SEC-0035 — Privoxy/Tor forward-proxy binds `0.0.0.0:8118` with `permit-access 172.16.0.0/12` — anonymizing open proxy for all tenants
- **Severity:** MEDIUM · **Confidence:** 0.85 · **Must fix:** NO vs external attackers (multi-layer mitigation holds); YES if untrusted tenants in scope
- **Files:** `core/internal/bootstrap/privoxy_config.py:43` (`DEFAULT_LISTEN_ADDR = f"0.0.0.0:{PRIVOXY_PORT}"`), `:107-113` (permit-access blanket /12); `firewall.py:145,308-322`; `docker_user_policy.py:77-78,188-208` (INPUT ACCEPT from 172.16.0.0/12); TRAP[BUGFIX] 2026-06-24 upgraded listen 127.0.0.1→0.0.0.0 silently generalizing grafana/hermes egress design to ALL containers
- **Attack path:** any container on any bridge allocating from 172.16–172.31 sets http_proxy=http://<gateway>:8118 → traffic exits via Tor decoupled from node identity, invisible to project egress expectations, consuming node Tor bandwidth.
- **Minimal fix:** narrow permit-access to exact declared platform-network subnets from placement state; document tenant reachability as accepted or deny project-owned networks explicitly.
- **Regression test:** unit test asserting no wildcard listen when restricted list supplied; gate asserting permit-access ⊆ declared bridge subnets.

## SEC-0036 — Docker-subnet identity split across three inconsistent CIDRs; nginx allowlists PUBLIC space
- **Severity:** MEDIUM · **Confidence:** 0.9 · **Must fix:** NO, but fix the /8 before anything publishes 8081
- **Files:** `core/modules/nginx/config/nginx.conf:161-164` (`allow 172.0.0.0/8` — ~240M public addresses incl. 172.217.0.0/16 Google; real containers excluded); `docker_installer.py:55-66` (daemon.json default-address-pools base **10.32.0.0/16**) vs privoxy/INPUT rules pinned 172.16.0.0/12
- **Failure paths:** (1) future publish of an internal-only port whitelists half the public internet; (2) containers on recreated 10.32.x networks denied stub_status scraping and privoxy access — silent breakage inviting ad-hoc firewall widening (the antipattern DevPlan 142 W6 killed).
- **Minimal fix:** shared `DOCKER_BRIDGE_NETS` constant set (platform_ports.py-style SoT): stub_status `allow 172.16.0.0/12; allow 10.32.0.0/16;` (drop /8); include 10.32.0.0/16 in TOR_PRIVOXY_NET/INPUT/permit-access.
- **Regression test:** parity gate diffing CIDR literals across nginx.conf + firewall domain against DOCKER_BRIDGE_NETS.

## SEC-0037 — Status-page test overlay publishes `18082:8080` on all interfaces — violates repo's own loopback test-port canon
- **Severity:** LOW-MED · **Confidence:** 0.9 · **Must fix:** NO (masked on managed nodes by ufw default-deny + DOCKER-USER)
- **Files:** `core/modules/status-page/docker-compose.test.yml:29-30` (`ports: !override ["18082:8080"]`) vs canon core/modules/AGENTS.md §test-compose (смещённые порты на 127.0.0.1) and every sibling override (nginx/grafana .test.yml all 127.0.0.1-pinned)
- **Attack path:** unmanaged environments (dev machines, CI runners, debug nodes) → host reaches full status JSON (container inventory, certs, projects) without Basic Auth.
- **Minimal fix:** `- "127.0.0.1:18082:8080"` (one line).
- **Regression test:** gate scanning all docker-compose.test.yml port entries for missing 127.0.0.1 prefix.
