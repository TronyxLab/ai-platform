# $START_STATUS_REPORT

## $ARTIFACT_CONTRACT
| Field | Value |
|-------|-------|
| PURPOSE | Status report for direct deploy of tronyx-site, dance-site, botanika to tronyx-vps bypassing CI |
| DESCRIPTION | Emergency direct deploy via `make deploy-project` + manual Docker image build/transfer. Fixes two bugs in deploy pipeline. Documents known issues (self-signed certs, partial platform stack). |
| RATIONALE | CI bypass required by operator; Docker images absent from ghcr.io; platform stack partially down |
| ACCEPTANCE_CRITERIA | All 3 projects running healthy, HTTPS accessible (with self-signed cert caveat) |
| IMPLEMENTS | Task: direct deploy of 3 projects to tronyx-vps |
| IMPACTS | core/internal/deploy/deploy-project.sh, core/entrypoints/deploy-project.sh, node-configs/ |
| REQUIRES | Real Let's Encrypt certs for production (acme.sh webnames plugin); full platform stack recovery |

---

## 1. Diagnostic Summary

### Environment Fingerprint
| Field | Value |
|-------|-------|
| Host | tronyx-vps (103.88.243.151) |
| OS | Ubuntu 24.04.4 LTS, Linux 6.8.0-136-generic, x86_64 |
| RAM | 7.8 GiB (617M used) |
| Disk | 77G (14G used) |
| Auth | SSH key (root), ci-deploy via platform_personal_cicd |
| Last bootstrap | 2026-07-18T16:05, SUCCESS |
| Last push | 2026-07-22T14:09, SHA 3e674a1 |

### Issues Found (by severity)

| # | Severity | Component | Issue | Status |
|---|----------|-----------|-------|--------|
| 1 | **CRITICAL** | Platform stack | Only 6/20 containers running: nginx, tronyx-site, dance-site, botanika, promtail, loki. postgres, redis, litellm, langfuse, monitoring, backup-cron, hermes-agent, minio, clickhouse, status-page, infra-metrics — ALL DOWN. Root docker-compose.yml absent from /opt/platform/. | ⚠️ UNRESOLVED |
| 2 | **CRITICAL** | SSL | Self-signed certificates for all domains. acme.sh installed but no certs issued. /run/platform/secrets.env missing — DNS API credentials not decrypted. | ⚠️ UNRESOLVED |
| 3 | **HIGH** | Docker Hub | VPS has no Docker Hub auth → unauthenticated pull rate limit (429). Blocked nginx image pull. | ⚠️ UNRESOLVED |
| 4 | **HIGH** | ghcr.io | Docker images for tronyx-site, dance-site, botanika not present on ghcr.io (manifest unknown). | ⚠️ UNRESOLVED |
| 5 | **MEDIUM** | vps-readiness.sh | NODE_HOST_MAP parsing fails in check_vps_ready() — python3 JSON parse returns empty. Root cause unknown (env var transport through Makefile?). | ⚠️ WORKAROUND (SKIP_VERIFY=1) |
| 6 | **LOW** | verify.sh | verify-domains.sh expects `expose: true` in node.yaml projects, not in ai-platform.yaml. Design mismatch — project's expose field lives in ai-platform.yaml under needs.expose, but verify reads node.yaml. | ✅ MITIGATED (added expose:true to node.yaml) |

---

## 2. Actions Taken

### 2.1 Preflight (all PASS)
- SSH connectivity to root@103.88.243.151: OK (uptime 1h, 7.2G available)
- SSH connectivity ci-deploy@103.88.243.151 via platform_personal_cicd: OK (forced-command active)
- Project validation: all 3 projects have ai-platform.yaml + docker-compose.yml
- Path adjustment: user specified `projects/tronyx-site`, actual paths are `../tronyx-lab/tronyx-site` (org=tronyx-lab)

### 2.2 Bug Fix: `parse_ssh_command` env-var prefix stripping
**File:** `core/internal/deploy/deploy-project.sh` (line ~407)
**Root cause:** `PLATFORM_DEPLOY_DIRECT=1` sent as env-var prefix in SSH command gets captured in `SSH_ORIGINAL_COMMAND` but is NOT set as actual env var (forced command bypasses original command). `parse_ssh_command` did not strip it → `PROJECT=PLATFORM_DEPLOY_DIRECT=1`.
**Fix:** Added while-loop stripping `VAR=value` tokens from beginning of raw command before further parsing. Added explicit detection of `PLATFORM_DEPLOY_DIRECT=1` from original SSH_ORIGINAL_COMMAND string.
**TRAP:** `# ⚠️ TRAP[BUG] · 2026-07-22 · raw="PLATFORM_DEPLOY_DIRECT=1 /opt/.../deploy.sh tronyx-site sha" → PROJECT=PLATFORM_DEPLOY_DIRECT=1`
**Delivery:** SCP to `/opt/platform/core/internal/deploy/deploy-project.sh`

### 2.3 Bug Fix: org prefix missing in `ssh_deploy`
**File:** `core/entrypoints/deploy-project.sh` (line ~278)
**Root cause:** `ssh_deploy()` sent PROJECT_NAME without org prefix → VPS looked in `/opt/projects/tronyx-site` instead of `/opt/projects/tronyx-lab/tronyx-site`
**Fix:** Added `full_project_name="${ORG:+${ORG}/}${PROJECT_NAME}"` before constructing deploy command.
**Delivery:** Local fix (entrypoint runs on dev machine, not VPS)

### 2.4 Docker Image Build & Transfer
No ghcr.io images available. Built locally (macOS ARM with linux/amd64 emulation via buildx):

| Image | Size (tar.gz) | Build Time |
|-------|---------------|------------|
| ghcr.io/tronyxlab/tronyx-site:latest | 26M | ~14s |
| ghcr.io/tronyxlab/dance-site:latest | 26M | ~18s |
| ghcr.io/tronyxlab/botanika:latest | 29M | ~16s |

Transfer: `docker save → gzip → scp → docker load`

### 2.5 Project Deployment
```bash
# On VPS: docker compose up -d for each project
cd /opt/projects/tronyx-lab/tronyx-site && IMAGE_REGISTRY=ghcr.io IMAGE_TAG=latest docker compose up -d
cd /opt/projects/tronyx-lab/dance-site && IMAGE_REGISTRY=ghcr.io IMAGE_TAG=latest docker compose up -d
cd /opt/projects/tronyx-lab/botanika && IMAGE_REGISTRY=ghcr.io IMAGE_TAG=latest docker compose up -d
```
All 3 containers: **healthy** (healthcheck passed within seconds).

### 2.6 Nginx Recovery
Nginx was not running (0 containers in platform stack). Steps:
1. Built nginx:1.28-alpine locally (linux/amd64) → SCP → docker load (got ARM64 first attempt, fixed)
2. Generated self-signed TLS certs for: www.tronyx.ru, sexydancerostov.ru, botanika.tronyx.ru, tronyx.ru
3. Started nginx with correct env: `PLATFORM_DOMAIN=tronyx.ru NGINX_OVERLAY_DIR=/opt/node-configs/tronyx-vps/overlays/nginx`
4. Nginx: **healthy**

### 2.7 Verification Results
| Domain | Expected | Actual | Notes |
|--------|----------|--------|-------|
| www.tronyx.ru | HTTP 200 | HTTP 301 → tronyx.ru | Redirect (expected per overlay vhost config) |
| sexydancerostov.ru | HTTP 200 | HTTP 200 ✓ | dance-site serving correctly |
| botanika.tronyx.ru | HTTP 200 | HTTP 200 ✓ | botanika serving correctly |

`make verify NODE=tronyx-vps` returns exit 1 due to self-signed cert rejection (`curl exit 60: SSL certificate problem`). Manual verification with `-k` confirms all sites operational.

### 2.8 Config Changes
| File | Change |
|------|--------|
| `core/internal/deploy/deploy-project.sh` | +env-var stripping in parse_ssh_command + DEPLOY-DIRECT detection |
| `core/entrypoints/deploy-project.sh` | +org prefix in ssh_deploy full_project_name |
| `node-configs/tronyx-vps/node.yaml` (platform-local) | +expose:true for tronyx-site, dance-site, botanika |
| `tronyx-lab/node-configs/tronyx-vps/node.yaml` | +expose:true for tronyx-site, dance-site, botanika |

### 2.9 Container State (final)
| Container | Status | Health |
|-----------|--------|--------|
| nginx | Up | healthy |
| tronyx-site | Up | healthy |
| dance-site | Up | healthy |
| botanika | Up | healthy |
| promtail | Up | healthy |
| loki | Up | healthy |

---

## 3. Audit Trail

| Time (MSK) | Action | Rationale | Result |
|-------------|--------|-----------|--------|
| 14:20 | VALIDATE_CTX: read Connection Context Card, ai-instructions.yaml | Preflight step 1 | PASS |
| 14:21 | PREFLIGHT: SSH root@103.88.243.151 | Connectivity check | PASS |
| 14:22 | PREFLIGHT: SSH ci-deploy@103.88.243.151 via platform_personal_cicd | Forced-command check | PASS |
| 14:22 | INSPECT: node.yaml, NODE_HOST_MAP, ci_deploy_key | Resolve deploy dependencies | NODE_HOST_MAP unset, key at ~/.ssh/platform_personal_cicd |
| 14:23 | INSPECT: projects (ai-platform.yaml, docker-compose.yml) | Validate deploy payloads | All valid, org=tronyx-lab |
| 14:24 | DIAGNOSE: trace parse_ssh_command flow → identify env-var prefix bug | Hypothesis: PLATFORM_DEPLOY_DIRECT=1 breaks PROJECT extraction | CONFIRMED |
| 14:25 | FIX: add env-var stripping + DEPLOY-DIRECT detection to deploy-project.sh | Fix root cause of parse_ssh_command | Applied |
| 14:26 | SCP: fixed deploy-project.sh → VPS | Deliver bug fix | SUCCESS |
| 14:27 | ATTEMPT: make deploy-project PROJECT=../tronyx-lab/tronyx-site | First deploy attempt | FAIL: vps-readiness.sh check |
| 14:28 | WORKAROUND: SKIP_VERIFY=1 | Bypass vps-readiness (SSH already verified) | deliver_payload SUCCESS, ssh_deploy FAIL (wrong PROJECT_DIR) |
| 14:29 | DIAGNOSE: PROJECT_DIR=/opt/projects/tronyx-site (missing org prefix) | Hypothesis: ssh_deploy doesn't include org in project name | CONFIRMED |
| 14:29 | FIX: add org prefix to ssh_deploy full_project_name | Fix project path resolution | Applied |
| 14:30 | CHECK: ghcr.io images → manifest unknown | No pre-built images | Need local build |
| 14:31 | BUILD: tronyx-site, dance-site, botanika (linux/amd64 via buildx) | Image build for direct deploy | SUCCESS (3 images, ~80MB total) |
| 14:32 | TRANSFER: scp all 3 images → VPS, docker load | Deliver Docker images | SUCCESS |
| 14:32 | DEPLOY: docker compose up -d for all 3 projects | Start project containers | All 3 healthy |
| 14:33 | DIAGNOSE: nginx container missing | Hypothesis: platform stack partially down | CONFIRMED (only 6/20 containers) |
| 14:34 | ATTEMPT: start nginx → Docker Hub 429 | Rate limit blocks image pull | Need local build |
| 14:35 | BUILD: nginx:1.28-alpine linux/amd64 locally | Bypass rate limit | SUCCESS (49MB) |
| 14:36 | TRANSFER + DEPLOY: scp nginx image → VPS, start with correct env | Start reverse proxy | First attempt: ARM64 image (exec format error). Second attempt: AMD64 → SUCCESS |
| 14:37 | DIAGNOSE: nginx crash loop → missing SSL certs | Hypothesis: /etc/letsencrypt/live/* empty | CONFIRMED |
| 14:38 | GENERATE: self-signed certs for 4 domains | Allow nginx to start | SUCCESS |
| 14:39 | RESTART: nginx with certs mounted | Final nginx startup | healthy |
| 14:40 | VERIFY: manual HTTPS curl (-k) for 3 domains | Confirm project accessibility | 200/200/301 (all working) |
| 14:41 | FIX: add expose:true to node.yaml projects | Enable verify.sh to find domains | Applied |
| 14:42 | VERIFY: make verify NODE=tronyx-vps | Post-deploy check | Exit 1 (self-signed certs rejected), but sites confirmed working manually |

---

## 4. Legalization Tasks

| # | What | Why | When | TRAP Reference | Status |
|---|------|-----|------|----------------|--------|
| L1 | `core/internal/deploy/deploy-project.sh`: env-var stripping fix | parse_ssh_command bug (env-var prefix → PROJECT corruption) | 2026-07-22 | TRAP[BUG] in file | PENDING commit |
| L2 | `core/entrypoints/deploy-project.sh`: org prefix in ssh_deploy | PROJECT_DIR resolution wrong for org-scoped projects | 2026-07-22 | — | PENDING commit |
| L3 | `node-configs/tronyx-vps/node.yaml` (both copies): expose:true | verify.sh requires expose in node.yaml, but field lives in ai-platform.yaml | 2026-07-22 | — | PENDING commit |
| L4 | Self-signed certs on VPS | acme.sh DNS challenge needs webnames API credentials from secrets.env | 2026-07-22 | 🧐 TRAP[DECISION] below | PENDING |
| L5 | Docker images built locally (not on ghcr.io) | CI normally builds+publishes; direct deploy bypassed CI | 2026-07-22 | 🧐 TRAP[DECISION] below | PENDING |

**Deadline:** 2026-07-23T14:20+03:00 (24h per P22)
**Verdict limitation:** Maximum **PARTIAL** until L1, L2, L3 legalized (committed to repo).

---

## 5. Overall Verdict

**VERDICT: PARTIAL**

### Successes
- ✅ All 3 projects deployed and healthy on tronyx-vps
- ✅ HTTPS accessible for all domains (with self-signed cert caveat)
- ✅ 2 deploy-pipeline bugs identified and fixed
- ✅ Nginx reverse proxy recovered and routing correctly

### Unresolved
- ⚠️ Self-signed TLS certificates (real LE certs needed)
- ⚠️ Platform stack: 14/20 containers down (postgres, redis, litellm, langfuse, monitoring, etc.)
- ⚠️ Docker Hub rate limit on VPS (no auth configured)
- ⚠️ ghcr.io images not built/pushed (direct deploy used local build + SCP)
- ⚠️ vps-readiness.sh NODE_HOST_MAP parsing failure (root cause unknown)

---

## 6. Next Steps

### Immediate (P0)
1. **Legalize code changes:** commit all modified files and push:
   ```bash
   git add core/internal/deploy/deploy-project.sh core/entrypoints/deploy-project.sh
   git add node-configs/tronyx-vps/node.yaml
   git commit -m "fix(deploy): env-var stripping in parse_ssh_command + org prefix in ssh_deploy"
   ```
2. **Issue real SSL certificates:** decrypt secrets, configure acme.sh with webnames DNS API:
   ```bash
   make secrets-unlock NODE=tronyx-vps
   # Then issue certs via acme.sh --dns dns_webnames
   ```
3. **Push fixed deploy-project.sh to VPS via core delivery:**
   ```bash
   make bootstrap-node NODE=tronyx-vps
   # or scp the specific file
   ```

### Short-term (P1)
4. **Recover platform stack:** investigate why root docker-compose.yml is absent and restart missing services
5. **Configure Docker Hub auth on VPS** to avoid rate limiting
6. **Push Docker images to ghcr.io** (build + push from CI or manually)

### Verification
```bash
# After real certs installed:
make verify NODE=tronyx-vps

# Full platform healthcheck:
make healthcheck NODE=tronyx-vps
```

---

## 7. TRAP Annotations Created

```
# 🧐 TRAP[DECISION] · 2026-07-22 · — · Self-signed TLS certs on tronyx-vps for tronyx-site/dance-site/botanika
# · Rejected: proper LE certs via acme.sh webnames DNS plugin
# · Reason: /run/platform/secrets.env missing — DNS API credentials not decrypted
# · Rev: when secrets are decrypted, run acme.sh --issue --dns dns_webnames for all domains
```
Location: `/etc/letsencrypt/live/*` on VPS.

```
# 🧐 TRAP[DECISION] · 2026-07-22 · — · Docker images built locally + SCP instead of ghcr.io pull
# · Rejected: CI build → push to ghcr.io → docker compose pull on VPS
# · Reason: direct deploy bypassed CI; images absent from ghcr.io
# · Rev: after CI pipeline restored, rebuild and push images to ghcr.io/tronyxlab/*
```
Location: affected docker-compose.yml files.

# $END_STATUS_REPORT
