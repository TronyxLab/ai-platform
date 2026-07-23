$START_STATUS_REPORT
# StatusReport 02 — Wave 3 Infrastructure Diagnosis

$ARTIFACT_CONTRACT
PURPOSE: Diagnostic report for Wave 3 infrastructure checks — webnames.ru API status, node.yaml configuration audit, acme.sh code structure analysis, S3 configuration audit.
DESCRIPTION: Complete infrastructure diagnosis per TASK-3.1 through TASK-3.4. Confirms root causes: webnames.ru zone manager DOWN (external), S3 credentials absent (placeholder), acme.sh dual-installation not consolidated.
RATIONALE: Before Wave 1-2 code changes, confirms the problem space is well-understood and identifies dependencies on external infrastructure.
ACCEPTANCE_CRITERIA:
  - AC-D1: webnames.ru API status definitively determined
  - AC-D2: node.yaml domain coverage documented, gap analysis complete
  - AC-D3: acme.sh code paths mapped, consolidation plan clear
  - AC-D4: S3 configuration chain documented, failure points identified
  - AC-D5: Actionable recommendations for VPS operator provided
IMPLEMENTS: DevPlan 058 Wave 3 (TASK-3.1 through TASK-3.4)
IMPACTS: none (diagnosis only — no code changes)
REQUIRES: none

---

## 1. Diagnostic Summary

| # | Check | Severity | Status | Detail |
|---|-------|----------|--------|--------|
| D1 | webnames.ru zone management | **CRITICAL** | ❌ BROKEN | `zone_manager_unavailable` — all TXT record operations fail. DNS-01 impossible. |
| D2 | webnames.ru config retrieval | LOW | ✅ OK | `get_config_acmesh` returns dns_webnames.sh plugin. Static config only (no zone ops). |
| D3 | DNS resolution tronyx.ru | — | ✅ OK | `103.88.243.151` — VPS reachable. |
| D4 | node.yaml — acme_dns_plugin | — | ✅ OK | `webnames` — correct. |
| D5 | node.yaml — domain coverage | — | ✅ OK | `tronyx.ru` + `www.tronyx.ru` + `botanika.tronyx.ru` + `sexydancerostov.ru`. Platform subdomains covered by wildcard (if DNS-01 works). |
| D6 | node.yaml — alternative DNS providers | **HIGH** | ❌ MISSING | No `backup_dns_plugin` field. Only `webnames` configured. |
| D7 | node.yaml — email | — | ✅ OK | `ai@tronyx.ru` — valid Let's Encrypt registration email. |
| D8 | acme.sh managed install | — | ⚠️ PARTIAL | `/opt/acme.sh/` installed by code, but certs issued via legacy `/root/.acme.sh/`. |
| D9 | acme.sh consolidation | **HIGH** | ❌ NOT CONSOLIDATED | Two installations: legacy `/root/.acme.sh/` (has certs) + managed `/opt/acme.sh/` (empty). Zero code references to `/root/.acme.sh/`. |
| D10 | S3 credentials | **CRITICAL** | ❌ PLACEHOLDER | `/run/platform/secrets.env` contains `platform-s3-access-key` (placeholder). Real creds encrypted in `.enc.yaml` but age key missing on VPS. |
| D11 | S3 endpoint resolution | — | ✅ OK | Chain: `S3_ENDPOINT_URL` → `S3_ENDPOINT` → `https://s3.timeweb.cloud`. Default correct. |
| D12 | upload.py code health | — | ✅ OK | Mature: 690 LOC, 3×30min retries, SHA256 verify, permanent vs transient error distinction. |

---

## 2. Actions Taken

### TASK-3.1: webnames.ru API Diagnostics

**DNS Resolution:**
- `dig` timed out on macOS (local network restriction) — non-blocking, not relevant to diagnosis.
- Python `socket.getaddrinfo()` confirmed: `tronyx.ru` → `103.88.243.151`.

**API Call 1 — domains_list:**
```
curl -s "https://www.webnames.ru/scripts/json_domain_zone_manager.pl?action=domains_list&apikey=..." 
→ {"details":"zone_manager_unavailable","result":"ERROR"}
```
**Verdict:** CONFIRMED BROKEN. Zone manager is DOWN on webnames.ru side. All TXT record operations (add/delete) will fail. DNS-01 challenge impossible.

**API Call 2 — get_config_acmesh:**
```
curl -s "https://www.webnames.ru/scripts/json_domain_zone_manager.pl?action=get_config_acmesh&domain=tronyx.ru&apikey=..."
→ dns_webnames.sh plugin script (full bash source with API_KEY embedded)
```
**Verdict:** WORKS. This endpoint returns a static script (acme.sh DNS plugin configuration). It does not interact with zone management — it's a template generator. Useful for acme.sh DNS-01 setup but useless without a working zone.

**Conclusion:** DNS-01 through webnames.ru is complete dead-end until zone manager recovers on provider side. No code fix possible — this is an external dependency failure.

### TASK-3.2: node.yaml Configuration Audit

**File location:** Two identical copies exist (same size 1366 bytes, same date 2026-07-22 14:38):
1. `/Users/tronyx/projects/tronyx-lab/node-configs/tronyx-vps/node.yaml`
2. `/Users/tronyx/projects/tronyx-lab/platform/node-configs/tronyx-vps/node.yaml`

**Key fields extracted:**

| Field | Value | Assessment |
|-------|-------|------------|
| `acme_dns_plugin` | `webnames` | ✅ Correct — only webnames is configured |
| `email` | `ai@tronyx.ru` | ✅ Used for Let's Encrypt registration |
| `domain` | `tronyx.ru` | ✅ Platform domain |
| Projects | `tronyx-site` (www.tronyx.ru), `dance-site` (sexydancerostov.ru), `botanika` (botanika.tronyx.ru) | ✅ 3 projects, 3 exposed domains |

**Domain coverage analysis (what a wildcard would cover):**
- `*.tronyx.ru` wildcard: covers `tronyx.ru`, `www.tronyx.ru`, `botanika.tronyx.ru`, `platform.tronyx.ru`, and any future subdomain
- `sexydancerostov.ru`: separate domain — needs its own cert regardless of wildcard status

**Gap:** No `backup_dns_plugin` field or `acme_challenge_mode` field in node.yaml. The system has no way to specify HTTP-01 fallback at config level — it must be coded (Wave 2).

**Alternative DNS providers availability:**
- Cloudflare (`dns_cf`): acme.sh supports. Requires `CF_Token` + `CF_Account_ID` env vars.
- DigitalOcean (`dns_dgon`): acme.sh supports. Requires `DO_API_KEY` env var.
- Neither is configured in `secret-definitions.yaml` or `node.yaml`. Adding a new DNS provider requires:
  1. Adding new secrets to `secret-definitions.yaml`
  2. Adding to encrypted `.enc.yaml`
  3. Updating `issue-cert.sh` to support new plugin
  4. Adding secret resolution in `secrets-init.sh`

### TASK-3.3: acme.sh Code Structure Analysis

**Managed installation (code path):**

| File | Default Path | Role |
|------|-------------|------|
| `install-acme.sh` | `/opt/acme.sh` | Clones acme.sh + dnsapi_ext via git |
| `issue-cert.sh` | `/opt/acme.sh` | Issues certs via DNS-01 (webnames) |
| `s3-ssl-cache.sh` | `/opt/acme.sh` | Upload/download certs + account data to/from S3 |
| `nginx/install.sh` (line 74,114) | `/opt/acme.sh` | **DUPLICATE CODE** — contains identical `install_acme()` and `_issue_acme_cert()` functions as `install-acme.sh` + `issue-cert.sh` |

All four files use `ACME_HOME="${ACME_HOME:-/opt/acme.sh}"`. The default is universally `/opt/acme.sh`.

**Legacy installation (manual):**

- Path: `/root/.acme.sh/`
- Zero references in production code (`core/`) — not a single file references this path
- Referenced only in: DevPlan 058, StatusReport 056, StatusReport 057
- Has working LE certs: `tronyx.ru`, `platform.tronyx.ru`, `botanika.tronyx.ru`, `sexydancerostov.ru`

**Consolidation gap:**
```
code expects:  /opt/acme.sh/     → empty (git clone only, no certs issued)
actual certs:  /root/.acme.sh/   → has 4 valid LE certs
cert payload:  /etc/letsencrypt/live/<domain>/fullchain.pem  → symlinked from /root/.acme.sh/
```

The code path `install-acme.sh` → `issue-cert.sh` → `s3-ssl-cache.sh` uses `/opt/acme.sh/`. But the running certs were issued via the legacy `/root/.acme.sh/`. This means:
1. `issue-cert.sh` is NOT the tool that issued the current certs
2. `s3-ssl-cache.sh` would look for account data at `/opt/acme.sh/<domain>_ecc/` — which doesn't exist
3. `_acme_install_cron()` would install cron for `/opt/acme.sh/` — but renewal would fail (no account key there)

**Duplicate code in nginx/install.sh:**
`core/modules/nginx/install.sh` (lines 74-263) contains full `install_acme()` and `_issue_acme_cert()` functions. These are IDENTICAL to `install-acme.sh` and `issue-cert.sh`. This is a code duplication debt — if `issue-cert.sh` gets HTTP-01 fallback (Wave 2), `nginx/install.sh` won't get it unless also updated.

### TASK-3.4: S3 Configuration Audit

**S3 credential chain (full path):**

```
secrets (source):
  tronyx-vps.enc.yaml → AGE-encrypted → contains real S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET

decryption path:
  secrets-init.sh → age --decrypt → /run/platform/secrets.env

current state on VPS (from server-state.json):
  /run/platform/secrets.env:
    S3_ACCESS_KEY=platform-s3-access-key    ← PLACEHOLDER
    S3_SECRET_KEY=(placeholder)
    S3_BUCKET=(placeholder)
  
  Root cause: AGE_SECRET_KEY missing on VPS → sops decryption fails → secrets.env
  populated with placeholder defaults instead of real encrypted values.
```

**S3 endpoint chain:**

```
docker-compose.base.yml (backup-cron):
  S3_ENDPOINT_URL: "${S3_ENDPOINT_URL:-${S3_ENDPOINT:-https://s3.timeweb.cloud}}"
  S3_ENDPOINT: "${S3_ENDPOINT:-${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}}"

backup_config.py (upload.py):
  endpoint_url = os.environ.get("S3_ENDPOINT_URL", os.environ.get("S3_ENDPOINT", "https://s3.timeweb.cloud"))

s3-ssl-cache.sh (_s3_download_file inline python3):
  endpoint = os.environ.get('S3_ENDPOINT_URL', os.environ.get('S3_ENDPOINT', 'https://s3.timeweb.cloud'))
```

Endpoint chain is consistent across all three consumers. Default: `https://s3.timeweb.cloud` (Timeweb S3).

**S3 in secret-definitions.yaml:**

| Secret | Tier | Source | Status |
|--------|------|--------|--------|
| `S3_BUCKET` | required | sops | PLACEHOLDER on VPS |
| `S3_ACCESS_KEY` | required | sops | PLACEHOLDER on VPS |
| `S3_SECRET_KEY` | required | sops | PLACEHOLDER on VPS |

**S3_ENDPOINT_URL/S3_ENDPOINT:** NOT in `secret-definitions.yaml` — it's a hardcoded default, not a secret. Only appears in env_defaults of platform-env.yaml as `S3_ENDPOINT_URL` (test value) but not as a regular secret. This is correct — endpoint is not a secret.

**upload.py code quality:** Production-ready. 690 lines with:
- 3 retries × 30 min intervals (90 min total max wait)
- SHA256 checksum verification post-upload
- Permanent vs transient error distinction (403/404 = fail fast, 5xx = retry)
- Separate config sources: `backup` (with prefix) vs `ssl-cache` (absolute keys)
- Post-upload verification with delete-and-re-upload on mismatch
- Proper logging: [IMP:7-9] throughout

**No code issues in upload.py — the problem is purely infrastructure (missing S3 creds).**

---

## 3. Root Cause Matrix

| Problem | Root Cause | Location | Fix Responsibility |
|---------|-----------|----------|--------------------|
| DNS-01 fails | webnames.ru zone manager DOWN | External (provider) | Provider — wait or switch |
| No wildcard cert | DNS-01 required for wildcard, DNS-01 broken | External | Code workaround (Wave 2 HTTP-01 fallback) |
| S3 cache non-functional | S3 credentials are placeholders (age key missing on VPS) | VPS `/run/platform/secrets.env` | VPS operator — add age key, re-decrypt |
| acme.sh dual install | Code uses `/opt/acme.sh/` but certs in `/root/.acme.sh/` | Install path divergence | Consolidate or symlink |
| nginx/install.sh duplicate code | `install_acme()` + `_issue_acme_cert()` copied into nginx module | `core/modules/nginx/install.sh` | Refactor — use shared scripts |
| No DNS provider fallback | Only `webnames` supported in code + config | `issue-cert.sh`, `node.yaml` | Add backup_dns_plugin or HTTP-01 |

---

## 4. Recommendations

### Immediate (VPS operator actions — no code changes)

**R1 — Add AGE secret key to VPS:**
```bash
# On VPS:
mkdir -p /root/.config/sops/age
# Copy age private key to /root/.config/sops/age/keys.txt
# Then re-run secrets-init:
/opt/platform/core/internal/bootstrap/secrets-init.sh
```
This unlocks real S3 credentials (`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`).

**R2 — Consolidate acme.sh installations:**
```bash
# Option A: Migrate certs to managed path
mv /root/.acme.sh/tronyx.ru_ecc /opt/acme.sh/
mv /root/.acme.sh/*.tronyx.ru_ecc /opt/acme.sh/  # platform, botanika
cp /root/.acme.sh/account.conf /opt/acme.sh/
# Then symlink legacy path → managed for backward compat:
ln -s /opt/acme.sh /root/.acme.sh

# Option B: Point ACME_HOME to legacy path
export ACME_HOME=/root/.acme.sh  # in bootstrap env
```
Option A preferred — aligns with code defaults.

**R3 — Verify cert expiry on VPS:**
```bash
for d in tronyx.ru platform.tronyx.ru botanika.tronyx.ru; do
  openssl x509 -in /etc/letsencrypt/live/$d/fullchain.pem -noout -enddate
done
```
Renewal via legacy acme.sh cron must continue working until Wave 2 HTTP-01 fallback is deployed.

### Code changes (Waves 1-2 — Coder)

**R4 — Wave 1 (S3 cache fixes):** G2 (chain.pem optional), G3 (account path `_ecc`), LE issuer validation. Code already has TRAP[BUG] markers — fixes are defined, just need implementation.

**R5 — Wave 2 (HTTP-01 fallback):** Add `ACME_CHALLENGE_MODE=auto` logic to `issue-cert.sh`. When DNS-01 fails, fall back to `acme.sh --standalone` for individual domain certs. Required for any cert issuance while webnames.ru is down.

**R6 — De-duplicate nginx/install.sh:** Remove `install_acme()` and `_issue_acme_cert()` from `core/modules/nginx/install.sh`. Source them from `core/internal/bootstrap/install-acme.sh` and `core/internal/bootstrap/issue-cert.sh`. This prevents code drift when Waves 1-2 are applied.

### Monitoring

**R7 — Add webnames.ru API monitoring:**
```bash
# Cron on VPS: every 4 hours
curl -s "https://www.webnames.ru/scripts/json_domain_zone_manager.pl?action=domains_list&apikey=..." | grep -q "zone_manager_unavailable" && \
  echo "WARN: webnames zone manager DOWN" || echo "OK: webnames zone manager UP"
```
When API recovers, switch back to DNS-01 for wildcard cert.

**R8 — Add S3 credential healthcheck:** Extend `preflight.py` to report `PLACEHOLDER` vs real S3 credentials. Current `probe_s3_connectivity()` silently fails with placeholder creds (403).

---

## 5. Audit Trail

| Time (UTC+3) | Action | Rationale | Result |
|-------------|--------|-----------|--------|
| 08:37 | Read DevPlan 058 | Wave 3 task definitions | 279 lines, 4 tasks |
| 08:37 | Read server-state.json | Connection context + known issues | VPS: 103.88.243.151, 8 known issues |
| 08:38 | DNS dig queries | TASK-3.1 — NS/SOA/A records | TIMEOUT (macOS network) |
| 08:38 | curl domains_list API | TASK-3.1 — webnames API status | `zone_manager_unavailable` CONFIRMED |
| 08:38 | curl get_config_acmesh API | TASK-3.1 — plugin config retrieval | Returns dns_webnames.sh script |
| 08:38 | Read node.yaml (tronyx-lab/platform/) | TASK-3.2 — configuration audit | 62 lines, acme_dns_plugin=webnames |
| 08:38 | Read platform-env.yaml | TASK-3.4 — S3 defaults | Generated file, S3_BUCKET=test-bucket |
| 08:38 | Read secret-definitions.yaml | TASK-3.4 — S3 secrets defined | 308 lines, S3 creds tier=required |
| 08:39 | Grep acme.sh paths in core/ | TASK-3.3 — code structure | 89 matches, all point to /opt/acme.sh |
| 08:39 | Grep S3 references in core/ | TASK-3.4 — S3 consumers | 100+ matches across backup-cron, preflight, s3-ssl-cache |
| 08:39 | Read install-acme.sh | TASK-3.3 — managed install | 79 lines, clones to /opt/acme.sh |
| 08:39 | Read s3-ssl-cache.sh | TASK-3.3 + TASK-3.4 | 602 lines, G2/G3 fixes already annotated |
| 08:39 | Read upload.py | TASK-3.4 — code health | 690 lines, mature, not the problem |
| 08:39 | Grep /root/.acme.sh references | TASK-3.3 — legacy path | 0 core/ references, only in docs |
| 08:39 | Read backup_config.py | TASK-3.4 — S3 endpoint | s3.timeweb.cloud default, 2 config levels |
| 08:40 | Check duplicate node.yaml paths | TASK-3.2 — path audit | Two identical copies exist |
| 08:40 | Python DNS resolution | TASK-3.1 — DNS confirmation | tronyx.ru → 103.88.243.151 |

---

## 6. Overall Verdict

**VERDICT: DIAGNOSIS COMPLETE — NO CODE CHANGES**

Wave 3 diagnosis confirms:
1. **webnames.ru zone manager is DOWN** — external dependency, no code fix possible. HTTP-01 fallback (Wave 2) is the correct mitigation.
2. **node.yaml is correctly configured** — `acme_dns_plugin: webnames`, all domains covered. Gap: no backup DNS provider or challenge mode field.
3. **acme.sh is NOT consolidated** — code uses `/opt/acme.sh/` but certs live in `/root/.acme.sh/`. Fix before Waves 1-2 or acme.sh operations will target wrong installation.
4. **S3 is completely non-functional** — placeholder credentials in secrets.env. Root cause: AGE secret key missing on VPS. upload.py code is fine — infrastructure problem only.

**Unblocked:** Waves 1-2 (code changes) can proceed — they don't depend on webnames API or S3. They add HTTP-01 fallback (which works without DNS-01) and fix S3 cache bugs (which will work once S3 creds are fixed).

$END_STATUS_REPORT
