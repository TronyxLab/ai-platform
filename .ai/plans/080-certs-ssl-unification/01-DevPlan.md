$START_DEVPLAN

# DevPlan 080 — Certificates & SSL Complete Unification

## $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Eliminate 8 certificate/SSL drift points catalogued in Brief 077 Chapter 4, unify to a single cert issuance pipeline, harmonize dev cert naming, fix template paths, and remove 1107 LOC of dead code |
| **DESCRIPTION** | Consolidate cert_orchestrator.py as the single entry point for all TLS certificate operations (issue, renew, S3 cache, cron, project certs). Delete nginx/install.sh (DEPRECATED, dead code). Unify dev cert filenames. Align platform-vhost to wildcard cert. Document template syntax contract with CI gate enforcement. |
| **RATIONALE** | 4 production issuance paths, 3 renewal mechanisms, 2 reloadcmd variants, 2 dev cert naming conventions, and a 1107-LOC dead file create systemic risk: agent confusion, cron mismatch (S3 sync or not), wrong cert paths served by nginx. Single entry point eliminates ambiguity and reduces change cost. |
| **ACCEPTANCE_CRITERIA** | (1) `nginx/install.sh` deleted — zero references remain. (2) `cert_orchestrator.py` is the ONLY entry point for production cert issuance. (3) `update_step_3_ssl_provision()` delegates to `cert_orchestrator`. (4) ALL vhosts use same wildcard cert path. (5) Dev certs use single naming convention. (6) Template syntax CI gate passes. (7) All existing cert tests pass with updated references. (8) `migrate_cron_if_needed()` updates old cron entries to include S3 sync. |
| **IMPLEMENTS** | Brief 077 §4 — DRIFT-C1 through DRIFT-C8 + DRIFT-B2 (ssl provision implementations) |
| **IMPACTS** | `core/modules/nginx/install.sh` (DELETE), `core/modules/nginx/templates/platform-default.conf.template` (DELETE), `core/internal/bootstrap/cert_orchestrator.py` (MODIFY), `core/internal/bootstrap/issue-cert.sh` (MODIFY), `core/internal/bootstrap/lifecycle/steps.py` (MODIFY), `core/internal/bootstrap/lifecycle/state_machine.py` (MODIFY), `core/internal/bootstrap/node-lifecycle.sh` (MODIFY), `core/modules/nginx/generate-dev-certs.sh` (MODIFY), `core/modules/nginx/dev-config/ssl-dev.conf` (MODIFY), `core/internal/scaffold/add-vhost.sh` (MODIFY), `core/modules/nginx/config/platform-vhost.conf.template` (MODIFY), `core/modules/nginx/AGENTS.md` (MODIFY), `tests/test_nginx_acme.py` (REFACTOR), `tests/test_tls_wildcard.py` (MODIFY), `tests/unit/test_cert_orchestrator.py` (MODIFY), `tests/test_template_syntax_gate.py` (NEW), `tests/unit/test_cert_cron_migration.py` (NEW) |
| **REQUIRES** | DevPlan 070 (shared/ exists), DevPlan 078 (secrets unified), DevPlan 079 (bootstrap unified). Sequenced AFTER 078 and 079. |

---

## Debt Intake

### Pre-existing TRAP[DEBT] in scope

| ID | File | Severity | Decision |
|----|------|----------|----------|
| DEBT-nginx-install | `core/modules/nginx/install.sh:34` | MED | **RESOLVED** — file deleted (C1). DEBT was about confusion risk from dead code; deletion is the permanent fix. |
| DEBT-dual-vhost | `core/modules/nginx/config/nginx.conf:106` | MED | **DEFER** — dual config/dev-config vhost topology is out of scope for cert unification. C7 addresses cert paths within this topology; full vhost convergence is a separate task. Recorded for future wave. |

### New DEBT discovered during analysis

| ID | File | Severity | Decision |
|----|------|----------|----------|
| DEBT-platform-vhost-020 | `core/modules/nginx/config/platform-vhost.conf.template:55-62` | LO | **IN_SCOPE** — platform-vhost uses separate cert `platform.${PLATFORM_DOMAIN}` despite wildcard `*.${PLATFORM_DOMAIN}` covering it. Resolved in C7. |
| DEBT-steps-ssl-provision | `core/internal/bootstrap/lifecycle/steps.py:697-779` | MED | **IN_SCOPE** — `_ssl_cert_provision()` is deprecated (broken S3 credential propagation). Removed in B2. |

---

## Requirements Analysis

### Key Success Criteria

1. **Single entry point:** All cert issuance flows converge on `cert_orchestrator.orchestrate_certs()`. No more shell functions that can be called independently.
2. **Zero dead code:** `nginx/install.sh` and `templates/platform-default.conf.template` (Docker variant) deleted without regressions.
3. **Cron integrity:** After this wave, all acme.sh cron entries include `--renew-hook` with S3 upload. `migrate_cron_if_needed()` fixes existing nodes.
4. **Template safety:** CI gate prevents `${VAR}` in `{{VAR}}`-zone templates and vice versa.
5. **Dev cert harmony:** Single naming convention across `generate-dev-certs.sh`, `ssl-dev.conf`, and `add-vhost.sh` harness.

### Drift Point → Solution Mapping

| Drift | Severity | Solution |
|-------|----------|----------|
| C1 | HI | DELETE `core/modules/nginx/install.sh` — all 1107 LOC. Refactor tests that reference it. |
| C2 | MED | DELETE `core/modules/nginx/templates/platform-default.conf.template` — orphaned, unused. |
| C3 | HI | Absorb issue-cert.sh cron + project-certs logic into cert_orchestrator. issue-cert.sh becomes thin wrapper. |
| C4 | HI | Add `migrate_cron_if_needed()` to cert_orchestrator. Called during bootstrap init to fix any old cron entries. |
| C5 | HI | Auto-resolved by C1 deletion. Verify issue-cert.sh reloadcmd already includes S3. |
| C6 | LO | Rename `generate-dev-certs.sh` output to `fullchain.pem`/`privkey.pem`. Update `ssl-dev.conf`. Fix `add-vhost.sh` harness to match. |
| C7 | MED | Switch `platform-vhost.conf.template` to use same wildcard cert path as other vhosts. |
| C8 | LO | Add documentation + CI gate for template syntax contract. |
| B2 | HI | Delete `_ssl_cert_provision()` from steps.py. Ensure all paths go through `_ssl_provision_via_orchestrator()`. |

---

## Architecture Overview — Unified Cert Issuance Pipeline

### Draft Code Graph

```
                     ┌──────────────────────────────────────┐
                     │    cert_orchestrator.py               │
                     │    SINGLE ENTRY POINT                 │
                     │                                      │
                     │  orchestrate_certs(domains)           │
                     │    ├── disk check → upload to S3      │
                     │    ├── S3 restore (direct import)     │
                     │    ├── issue-cert.sh (subprocess)     │
                     │    │     ├── DNS-01 primary           │
                     │    │     └── HTTP-01 fallback         │
                     │    ├── self-signed fallback           │
                     │    └── _install_cron(acme_home)       │
                     │         └── --renew-hook: S3 upload   │
                     │                                      │
                     │  migrate_cron_if_needed()             │
                     │    └── detect old cron → replace      │
                     │                                      │
                     │  _issue_project_certs(domains)        │
                     │    └── filter subdomains → issue      │
                     └──────────┬───────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                  │
    ┌─────────▼────────┐  ┌────▼──────────┐  ┌───▼──────────────────┐
    │ state_machine.py │  │node-lifecycle  │  │ issue-cert.sh         │
    │ _ssl_provision   │  │ .sh ssl_step   │  │ (THIN WRAPPER)        │
    │ _via_orchestrator│  │ → orchestrator │  │ calls orchestrator    │
    │ (CANONICAL)      │  │                │  │ for backward compat   │
    └──────────────────┘  └───────────────┘  └──────────────────────┘
              │                                          │
              │ (DELETED)                                │
    ┌─────────▼────────┐                       ┌────────▼──────────┐
    │ steps.py          │                       │ s3-ssl-cache.sh    │
    │ _ssl_cert_provisi │                       │ (CLI FACADE)       │
    │ on() ❌ REMOVED   │                       │ → s3_ssl_cache.py  │
    └───────────────────┘                       └───────────────────┘
```

### Contracts

#### cert_orchestrator.py Contract (expanded)

```python
def orchestrate_certs(
    domains: list[str],
    issue_cert_script: str,
    secrets_env: str = "",
) -> CertResult:
    """Unified entry point: restore from S3 → issue via acme.sh → self-signed fallback.

    NEW: Also handles cron install and project cert orchestration.
    """

def migrate_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Check crontab for old (no-S3) acme.sh cron entry and replace with new one.

    Detects: crontab entry matching 'acme.sh --cron' but WITHOUT 's3_ssl_cache' in
    the same line (indicating no S3 sync). Replaces with --install-cronjob + --renew-hook.
    Idempotent: if new cron already present, returns True without changes.
    Returns True if migration succeeded or was not needed.
    """
```

### Data Flow — Bootstrap SSL Provision

```
bootstrap-node → node-lifecycle.sh --mode update
  └─ update_step_3_ssl_provision()
      └─ python3 state_machine.py --run-step 4  [ssl_provision]
          └─ _ssl_provision_via_orchestrator(core_dir, node_yaml)
              ├─ _extract_domains(node_yaml, context="")  → [platform, project1, project2]
              ├─ importlib: cert_orchestrator
              └─ cert_orchestrator.orchestrate_certs(domains, issue_cert_script, secrets_env)
                  ├─ for each domain:
                  │   ├─ disk check (LE issuer + >30 days) → upload to S3 → SKIP
                  │   ├─ S3 restore via s3_ssl_cache (direct import, no subshell)
                  │   ├─ issue-cert.sh (bash subprocess, DNS-01/HTTP-01)
                  │   └─ self-signed (last resort)
                  ├─ _install_cron() → --install-cronjob + --renew-hook (S3 upload)
                  └─ return CertResult

bootstrap-node --mode init (ADDITIONAL)
  └─ step_18_deploy_context (index 23) → context_deployer.py
      └─ cert_orchestrator.orchestrate_certs(domains)  [idempotent second call]
          └─ migrate_cron_if_needed() → detect old cron → replace with S3-aware cron
```

### Data Flow — Dev Certs (C6 — harmonized)

```
make dev-certs → generate-dev-certs.sh
  └─ cert_is_current() → check SAN + expiry
  └─ generate_mkcert() / generate_openssl()
      → output: dev-certs/fullchain.pem  ← CHANGED from _local.pem
      → output: dev-certs/privkey.pem    ← CHANGED from _local-key.pem

ssl-dev.conf (dev vhosts include):
  ssl_certificate     /etc/nginx/dev-certs/fullchain.pem;   ← CHANGED
  ssl_certificate_key /etc/nginx/dev-certs/privkey.pem;     ← CHANGED

add-vhost.sh harness:
  → generates dev-certs/fullchain.pem, dev-certs/privkey.pem  ← ALREADY uses these names
  → harness already uses fullchain/privkey — no change needed
  → BUT: harness should REUSE canonical dev certs instead of generating own
```

---

## §TASKS

### Critical Path

```
TASK-1 (DELETE dead code) → TASK-3 (refactor tests) → TASK-5 (unify orchestration) → TASK-9 (gate)
                                                                      ↓
                              TASK-4 (migrate cron) ←─────────────────┘
                              TASK-6 (fix vhost cert paths)
                              TASK-7 (harmonize dev certs)
                              TASK-8 (template syntax contract)
```

---

### TASK-1 — DELETE: nginx/install.sh and templates/platform-default.conf.template

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 3/10 |
| **Dependencies** | None (independent) |
| **Artifact** | Two files deleted |

**Steps:**
1. Delete `core/modules/nginx/install.sh` (1107 LOC). All cert functions are dead code; nginx is `install_type: docker`.
2. Verify no production path references this file: `grep -r "nginx/install.sh" core/ Makefile` → confirm it's only referenced in comments or AGENTS.md.
3. Delete `core/modules/nginx/templates/platform-default.conf.template` (18 LOC). Orphaned file with `{{PLATFORM_DOMAIN}}` syntax and shadow `/etc/nginx/ssl/` cert paths — unreferenced by docker-compose, template-manifest, or any script.
4. Remove reference from `core/modules/nginx/AGENTS.md` if present.
5. Remove `nginx/install.sh` from `core/templates/template-manifest.yaml` consumer reference if present (line 62 — references `_deploy_vhost_full()` which was in install.sh).

**Acceptance criteria:**
- `git status` shows both files deleted
- `grep -r "nginx/install\.sh" core/` returns zero matches in non-comment, non-AGENTS lines
- `grep -r "templates/platform-default\.conf\.template" core/` returns zero matches (the `config/` version is preserved)

---

### TASK-2 — CLEANUP: Remove references in AGENTS.md and entrypoint-manifest.yaml

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 2/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | Cleaned AGENTS.md files |

**Steps:**
1. Remove `nginx/install.sh` references from `core/modules/nginx/AGENTS.md` (any table entries listing it).
2. Remove `nginx/install.sh` reference from `core/modules/AGENTS.md` if present.
3. Update `core/templates/template-manifest.yaml` line 62: remove consumer `core/modules/nginx/install.sh:_deploy_vhost_full()` — the entire consumer entry for this template should be removed or reassigned.
4. Update `core/AGENTS.md` if it lists `nginx/install.sh` anywhere.

**Acceptance criteria:**
- `grep -rn "install\.sh" core/modules/nginx/AGENTS.md` returns zero matches (unless about a different install.sh)
- `grep -rn "nginx/install" core/AGENTS.md core/modules/AGENTS.md` returns zero matches

---

### TASK-3 — REFACTOR: Update tests that reference nginx/install.sh

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 6/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | Updated test files, all tests pass |

**Steps:**
1. **`tests/test_nginx_acme.py`**: This test sources `nginx/install.sh` for shell function testing via `_source_and_run()`. The test has two test groups:
   - Tests for `nginx/install.sh` functions (`_issue_acme_cert`, `_acme_install_cron`, `_acme_verify_cert`) — **DELETE** these test functions. These are exact duplicates of issue-cert.sh tests.
   - Tests for `issue-cert.sh` functions (already separate via `_source_and_run_issue_cert_no_main()`) — **PRESERVE**.
   - The `test_install_sh_syntax` test → **DELETE** (checks `bash -n` on deleted file).
   - The `test_install_sh_declares_functions` test → **DELETE**.
2. **`tests/test_tls_wildcard.py`**: Remove `nginx/install.sh` from file existence checks and script inventory (around line 698, 737).
3. **`tests/test_no_hardcoded_credentials.py`**: Update line 47 reference from `nginx/install.sh` to `issue-cert.sh`.

**Acceptance criteria:**
- `python -m pytest tests/test_nginx_acme.py -s -v` — remaining tests pass (only issue-cert.sh tests)
- `python -m pytest tests/test_tls_wildcard.py -s -v` — passes
- No test file references `nginx/install.sh`

---

### TASK-4 — ADD: migrate_cron_if_needed() to cert_orchestrator.py

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 5/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | New function in cert_orchestrator.py + test |

**Before (problem):**
```text
crontab entry from old nginx/install.sh _acme_install_cron():
  0 0 * * * "/opt/acme.sh/acme.sh" --cron --home "/opt/acme.sh" > /dev/null
  → NO --renew-hook → cert renewed but NOT uploaded to S3
```

**After (solution):**
```python
def migrate_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Check crontab for old acme.sh entry (no S3 sync) → replace with new one.

    ▶ ┌crontab -l┐ → ◇ grep acme.sh --cron → ◇ grep -v s3_ssl_cache → ◇ found?
    → ⚡ --install-cronjob + --renew-hook → ⎋ return True
    """
```

**Design:**
```python
def migrate_cron_if_needed(acme_home: str = "/opt/acme.sh") -> bool:
    """Detect and fix old (no-S3) acme.sh cron entry.

    Returns True if migration succeeded or cron was already correct.
    Never raises — non-fatal, logs warnings on failure.

    ## @rationale DRIFT-C4: old nginx/install.sh _acme_install_cron() installed
    ##            cron WITHOUT --renew-hook for S3 upload. This function detects
    ##            the old entry and reinstalls cron with --renew-hook.
    ## @invariants
    ##   - Idempotent: if cron already has s3_ssl_cache reference, skips
    ##   - Non-fatal: failure logs WARN, returns False
    ##   - Runs on bootstrap init (step_18_deploy_context) and update
    """
    import subprocess

    acme_sh = os.path.join(acme_home, "acme.sh")
    if not os.path.isfile(acme_sh):
        logger.info("[IMP:7][cron_migrate] acme.sh not found — skipping cron migration")
        return False

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.info("[IMP:8][cron_migrate] No crontab — nothing to migrate")
            return True  # No crontab = nothing to fix

        cron_content = result.stdout
        if "s3_ssl_cache" in cron_content:
            logger.info("[IMP:8][cron_migrate] Cron already has S3 sync — no migration needed")
            return True

        if f"{acme_sh}" not in cron_content or "--cron" not in cron_content:
            logger.info("[IMP:8][cron_migrate] No acme.sh cron entry — nothing to migrate")
            return True

        # ── Old entry found — reinstall cron ──
        logger.warning("[IMP:8][cron_migrate] Old acme.sh cron without S3 sync — migrating")
        subprocess.run(
            [acme_sh, "--install-cronjob", "--home", acme_home],
            capture_output=True, text=True, timeout=30, check=True
        )

        # Install renew-hook for S3
        s3_cache_py = os.path.join(os.path.dirname(__file__), "s3_ssl_cache.py")
        if os.path.isfile(s3_cache_py):
            subprocess.run(
                [acme_sh, "--renew-hook",
                 f"python3 '{s3_cache_py}' upload \"$Le_Domain\""],
                capture_output=True, text=True, timeout=30
            )
            logger.info("[IMP:9][cron_migrate] Cron migration complete — S3 sync enabled")
        else:
            logger.warning("[IMP:7][cron_migrate] s3_ssl_cache.py not found — --renew-hook skipped")
        return True

    except Exception as e:
        logger.warning("[IMP:7][cron_migrate] Migration failed: %s", e)
        return False
```

**Acceptance criteria:**
- `migrate_cron_if_needed()` detects crontab entries with `acme.sh --cron` but without `s3_ssl_cache`
- On detection, calls `acme.sh --install-cronjob` + `--renew-hook` with S3 upload
- Idempotent: second call is no-op
- `tests/unit/test_cert_cron_migration.py` (NEW) verifies with mock crontab

---

### TASK-5 — UNIFY: cert_orchestrator.py as single entry point + issue-cert.sh reduction

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 8/10 |
| **Dependencies** | TASK-4 |
| **Artifact** | Modified cert_orchestrator.py + reduced issue-cert.sh |

**What changes:**

#### 5A. cert_orchestrator.py — absorb cron + project cert logic

After successful cert issuance for all domains, call `_install_cron()`:

```python
# In orchestrate_certs(), after processing all domains:
if result.restored > 0 or result.issued > 0 or result.skipped > 0:
    _install_cron(acme_home="/opt/acme.sh")
```

Add `_install_cron()` function (ported from issue-cert.sh `_acme_install_cron`):

```python
def _install_cron(acme_home: str = "/opt/acme.sh") -> bool:
    """Install acme.sh --install-cronjob + --renew-hook with S3 upload.

    Idempotent: checks crontab first, skips if already present.
    Includes --renew-hook to upload certs to S3 after each renewal.
    Non-fatal: failure logs WARN.
    """
```

Call `migrate_cron_if_needed()` during bootstrap init flow (via a parameter or env flag). The `state_machine.py` calls `_ssl_provision_via_orchestrator()` which calls `orchestrate_certs()`. Add an optional `migrate_cron` parameter:

```python
def orchestrate_certs(
    domains: list[str],
    issue_cert_script: str,
    secrets_env: str = "",
    migrate_cron: bool = False,
) -> CertResult:
```

#### 5B. issue-cert.sh — reduce to thin wrapper (or keep as-is)

**TRAP[DECISION] · 2026-07-25 · — · issue-cert.sh: thin wrapper vs absorb**

Two options:
- **Option A (absorb):** Port all issue-cert.sh logic to Python in cert_orchestrator.py. Shell becomes zero lines. Risk: webnames API key injection (sed + shred) is complex to port; DNS-01 credential propagation already handled by `_source_secrets_env()`.
- **Option B (thin wrapper, Recommended):** Keep issue-cert.sh for the acme.sh invocation (subprocess from cert_orchestrator), but remove standalone entrypoint. cert_orchestrator calls issue-cert.sh, not the other way around. This preserves the battle-tested acme.sh interaction while centralizing orchestration logic.

**Recommendation: Option B** — issue-cert.sh remains the acme.sh executor (~716 LOC preserved), but its `main()` entrypoint is no longer called directly from any bootstrap step. All orchestration (cron, project certs, domain iteration) lives in cert_orchestrator. issue-cert.sh is called ONLY via cert_orchestrator's subprocess.

**Changes to issue-cert.sh:**
1. Remove `main()` standalone entrypoint (keep functions: `_issue_acme_cert`, `_issue_http01_cert`, `_acme_install_cron`, `_acme_verify_cert`, `_is_subdomain`, `_issue_project_certs`, `issue_tls_cert`).
2. Remove cron install from `main()` — cron is now managed by cert_orchestrator.
3. Mark script as "called by cert_orchestrator.py — do NOT call directly".
4. Keep `issue_tls_cert()` as the public function for subprocess invocation from cert_orchestrator.

**Simplify node-lifecycle.sh update_step_3_ssl_provision():**
```bash
update_step_3_ssl_provision(){
    # Source secrets.env for WEBNAMES_API_KEY (needed by cert_orchestrator internals)
    local secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    [[ -f "$secrets_env" ]] && { set -a; source "$secrets_env"; set +a; unset_platform_proxy
        echo "[IMP:9][ssl-provision] WEBNAMES_API_KEY loaded from ${secrets_env}" >&2; }
    # Delegate to Python state machine → cert_orchestrator
    _delegate --mode "${MODE}" --run-step 4
}
```

#### 5C. steps.py — remove _ssl_cert_provision()

Delete lines 697-779 from `core/internal/bootstrap/lifecycle/steps.py`. This function is:
- DEPRECATED — has broken S3 credential propagation (subshell)
- Only handles platform domain (not projects)
- Replaced by `state_machine.py._ssl_provision_via_orchestrator()`

Verify no caller references `_ssl_cert_provision`:
```bash
grep -rn "_ssl_cert_provision" core/ tests/
```
If any callers exist, redirect to `_ssl_provision_via_orchestrator`.

**Acceptance criteria:**
- `cert_orchestrator.orchestrate_certs()` installs cron after successful cert processing
- `cert_orchestrator.orchestrate_certs()` accepts optional `migrate_cron` parameter
- `_ssl_cert_provision()` does NOT exist in steps.py
- `update_step_3_ssl_provision()` in node-lifecycle.sh delegates to state_machine → cert_orchestrator (no direct issue-cert.sh call)
- `issue-cert.sh` is only called by cert_orchestrator.py subprocess, never standalone from bootstrap
- `python -m pytest tests/unit/test_cert_orchestrator.py -s -v` passes

---

### TASK-6 — FIX: platform-vhost.conf.template → wildcard cert (C7)

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 3/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | Modified platform-vhost.conf.template |

**Before:**
```nginx
# platform-vhost uses separate cert — platform.${PLATFORM_DOMAIN} issued separately
ssl_certificate     /etc/letsencrypt/live/platform.${PLATFORM_DOMAIN}/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/platform.${PLATFORM_DOMAIN}/privkey.pem;
# + inline SSL settings (ssl_session_cache, protocols, ciphers) — duplicated
```

**After:**
```nginx
# platform-vhost uses wildcard cert (same as all other vhosts)
# *.${PLATFORM_DOMAIN} covers platform.${PLATFORM_DOMAIN}
include /etc/nginx/conf.d/ssl-params.conf;
```

Replace lines 61-70 (inline SSL block) with `include /etc/nginx/conf.d/ssl-params.conf;` — same as grafana, loki, prometheus, langfuse, hermes vhosts.

**Rationale:** The wildcard cert `*.${PLATFORM_DOMAIN}` covers `platform.${PLATFORM_DOMAIN}`. The separate cert was introduced due to a historical DNS-01 issue (TRAP[BUG] at line 55-60). This is no longer relevant — wildcard certs via DNS-01 are working.

**Migration note:** No data migration needed — nginx doesn't care if the cert file at the old path disappears; it only reads the new path. The old `platform.${PLATFORM_DOMAIN}` certs in `/etc/letsencrypt/live/` can remain (harmless) or be cleaned up manually.

**Acceptance criteria:**
- `platform-vhost.conf.template` SSL block matches other vhosts (`include ssl-params.conf`)
- `grep "platform.\${PLATFORM_DOMAIN}" core/modules/nginx/config/platform-vhost.conf.template` returns zero matches in cert paths
- Manual `nginx -t` (or docker compose config check) valid

---

### TASK-7 — HARMONIZE: Dev cert filenames → fullchain.pem / privkey.pem (C6)

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 4/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | 3 modified files |

**Change 1: `generate-dev-certs.sh` — rename output files**

Lines 28-29:
```bash
# Before:
_CERT_FILE="${DEV_CERTS_DIR}/_local.pem"
_KEY_FILE="${DEV_CERTS_DIR}/_local-key.pem"

# After:
_CERT_FILE="${DEV_CERTS_DIR}/fullchain.pem"
_KEY_FILE="${DEV_CERTS_DIR}/privkey.pem"
```

**Change 2: `dev-config/ssl-dev.conf` — update cert paths**

Lines 13-14:
```nginx
# Before:
ssl_certificate     /etc/nginx/dev-certs/_local.pem;
ssl_certificate_key /etc/nginx/dev-certs/_local-key.pem;

# After:
ssl_certificate     /etc/nginx/dev-certs/fullchain.pem;
ssl_certificate_key /etc/nginx/dev-certs/privkey.pem;
```

**Change 3: `add-vhost.sh` harness — align cert path expectations**

The harness already uses `fullchain.pem`/`privkey.pem` (lines 671-678, 695-696). BUT it generates its own self-signed certs instead of reusing the canonical dev certs. The harness should:
- Check if canonical dev certs exist (`dev-certs/fullchain.pem` via `make dev-certs`)
- If yes: use them as-is (the harness sed replacement already maps to these paths)
- If no: generate a minimal self-signed cert for the harness (existing fallback behavior)

No changes needed to add-vhost.sh — it already uses `fullchain.pem`/`privkey.pem`. The path sed replacement at lines 695-696 already maps production LE paths → `dev-certs/fullchain.pem`.

**Change 4: Update `core/modules/nginx/docker-compose.base.yml` — no changes needed**

The mount `./dev-certs:/etc/nginx/dev-certs:ro` (line 71) doesn't reference specific filenames. This is fine.

**Acceptance criteria:**
- `generate-dev-certs.sh` outputs `fullchain.pem` and `privkey.pem` (not `_local.pem`/`_local-key.pem`)
- `ssl-dev.conf` references `/etc/nginx/dev-certs/fullchain.pem` and `privkey.pem`
- `add-vhost.sh` harness references same paths (already true)
- `python -m pytest tests/test_nginx_dev_certs.py -s -v` passes (may need test update for filename change)
- `make dev-certs` completes successfully

---

### TASK-8 — DOCUMENT: Template syntax contract + CI gate (C8)

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 4/10 |
| **Dependencies** | TASK-1 |
| **Artifact** | AGENTS.md update + CI gate test |

**Change 1: Document the contract in `core/modules/nginx/AGENTS.md`**

Add a new section after VHOST_CONTRACT:

```markdown
## 6. Template Syntax Contract (DRIFT-C8)

Два типа `.template` файлов в nginx-модуле:

| Директория | Синтаксис | Рендерер | Когда применяется |
|-----------|-----------|----------|-------------------|
| `config/*.conf.template` | `${PLATFORM_DOMAIN}` | `envsubst` (nginx Docker entrypoint `20-envsubst-on-templates.sh`) | При старте nginx-контейнера |
| `templates/*.conf.template` | `{{PLATFORM_DOMAIN}}` | `template_engine.py` | При рендере шаблонов через `make templates-render` |

**Правило:** Никогда не смешивать `${}` и `{{}}` в одном файле.
Каждая директория использует СТРОГО свой синтаксис.

**CI gate:** `test_template_syntax_gate.py` проверяет, что:
- Файлы в `config/` НЕ содержат `{{`
- Файлы в `templates/` НЕ содержат `${`
- Исключение: nginx-переменные вида `${host}`, `${request_uri}` в `config/` разрешены
```

**Change 2: Create CI gate test `tests/test_template_syntax_gate.py`**

New test with `@pytest.mark.gate`:
```python
# @purpose: Verify template syntax contract — no ${} in templates/, no {{}} in config/
def test_config_templates_use_envsubst_syntax():
    """All .conf.template files in config/ use ${} syntax (NOT {{}})."""
    # Scan core/modules/nginx/config/*.conf.template
    # Assert no line contains '{{' (except comments)

def test_template_templates_use_jinja_syntax():
    """All .conf.template files in templates/ use {{}} syntax (NOT ${})."""
    # Scan core/modules/nginx/templates/*.conf.template
    # Assert no line contains '${' (except nginx variables)

def test_no_mixed_syntax_in_single_file():
    """No single .template file contains both {{}} and ${}."""
```

**Acceptance criteria:**
- `core/modules/nginx/AGENTS.md` contains template syntax contract section
- `tests/test_template_syntax_gate.py` exists with 3 gate tests
- `python -m pytest tests/test_template_syntax_gate.py -s -v` passes

---

### TASK-9 — FINALIZE: Run complete test suite, fix any regressions

| Field | Value |
|-------|-------|
| **Owner** | Coder |
| **Complexity** | 5/10 |
| **Dependencies** | TASK-1 through TASK-8 |
| **Artifact** | All tests green |

**Steps:**
1. Run `python -m pytest tests/ -s -v -k "cert or nginx or ssl or tls or template"` — targeted test run
2. Run `python -m pytest tests/ -s -v` — full suite
3. Fix any regressions found
4. Run `make fix-gate && make gate MODE=fast` — CI pre-flight check
5. Verify no new `TRAP[DEBT]` issues introduced

**Acceptance criteria:**
- Full test suite passes (204+ tests)
- `make gate MODE=fast` passes
- No new `nginx/install.sh` references in any source file

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_cert_orchestrator.py` | `test_orchestrate_certs_installs_cron` | After successful cert issuance, acme.sh cron is installed | `cert_orchestrator._install_cron()` |
| `tests/unit/test_cert_orchestrator.py` | `test_orchestrate_certs_noop_when_no_certs` | Empty domain list → no cron install | `cert_orchestrator.orchestrate_certs()` |
| `tests/unit/test_cert_cron_migration.py` (NEW) | `test_migrate_old_cron_detected` | Crontab with acme.sh --cron but no s3_ssl_cache → migration triggered | `cert_orchestrator.migrate_cron_if_needed()` |
| `tests/unit/test_cert_cron_migration.py` (NEW) | `test_migrate_already_s3_aware` | Crontab with acme.sh --cron AND s3_ssl_cache → no-op | `cert_orchestrator.migrate_cron_if_needed()` |
| `tests/unit/test_cert_cron_migration.py` (NEW) | `test_migrate_no_crontab` | No crontab at all → no-op | `cert_orchestrator.migrate_cron_if_needed()` |
| `tests/test_nginx_acme.py` | `test_issue_cert_sh_*` (existing, PRESERVE) | issue-cert.sh functions still work after install.sh removal | `issue-cert.sh` |
| `tests/test_nginx_acme.py` | `test_install_sh_*` (DELETE) | N/A — removed with install.sh | N/A (deleted) |
| `tests/test_tls_wildcard.py` | `test_scripts_exist` | Verify cert-relevant scripts exist (issue-cert.sh, cert_orchestrator.py, s3_ssl_cache.py) — NOT install.sh | Script inventory |
| `tests/test_nginx_dev_certs.py` | `test_dev_certs_generated` | verify `fullchain.pem`/`privkey.pem` exist (not `_local.pem`/`_local-key.pem`) | `generate-dev-certs.sh` |
| `tests/test_template_syntax_gate.py` (NEW) | `test_config_templates_use_envsubst_syntax` | No `{{` in config/*.conf.template | Template syntax contract |
| `tests/test_template_syntax_gate.py` (NEW) | `test_template_templates_use_jinja_syntax` | No `${` in templates/*.conf.template | Template syntax contract |
| `tests/test_template_syntax_gate.py` (NEW) | `test_no_mixed_syntax_in_single_file` | No single file has both `{{}}` and `${}` | Template syntax contract |
| `tests/unit/test_cert_upload_on_skip.py` | All existing | Verify no regressions after refactoring | `cert_orchestrator._upload_to_s3()` |
| `tests/test_cert_backup_gap.py` | All existing | Verify no regressions | S3 cert backup |
| `tests/test_cert_collector.py` | All existing | Verify no regressions | Cert metrics collector |

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: TASK-1 (DELETE dead code)
- Command: `coder Read DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (sequential, no shared files)
- Tasks: TASK-2 (AGENTS.md cleanup), TASK-3 (test refactor), TASK-4 (migrate cron)
- These can run in parallel after TASK-1 since they edit different files
- Command: `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4`

### Wave 3 (depends on Wave 2)
- Tasks: TASK-5 (unify orchestration), TASK-6 (fix vhost cert), TASK-7 (harmonize dev certs), TASK-8 (template contract)
- TASK-5 depends on TASK-4 (migrate_cron_if_needed must exist before orchestrate_certs uses it)
- TASK-6, TASK-7, TASK-8 are independent of each other but all need TASK-1 done
- Command: `coder Read DevPlan.md, implement Wave 3: TASK-5, TASK-6, TASK-7, TASK-8`

### Wave 4 (final validation)
- Tasks: TASK-9 (run full test suite)
- Command: `coder Read DevPlan.md, implement Wave 4: TASK-9`

---

## Design Decisions

### ## @rationale C1: Delete nginx/install.sh entirely, not just the cert functions

**Q:** Why delete the entire file rather than surgically removing cert-related functions?
**A:** The file is marked DEPRECATED (`install_type: docker`). Every function in it is dead code — nginx runs as a Docker container via `docker-compose.base.yml`, not via `systemctl`. The remaining vhost deploy functions (`deploy_vhost`, `write_config_atomic`) are also dead because vhost management is handled by `add-vhost.sh` on the operator side and delivered via rsync. Keeping a partial file invites future agent confusion ("maybe this function is still called?"). Full deletion is the cleanest signal.

### ## @rationale C3: cert_orchestrator as single entry point — why subprocess to issue-cert.sh instead of absorbing?

**Q:** Why keep issue-cert.sh as a subprocess instead of porting to pure Python?
**A:** `issue-cert.sh` contains 716 lines of battle-tested acme.sh integration: webnames API key injection (sed + shred protocol), DNS-01/HTTP-01 challenge logic, acme.sh binary interaction with specific flags. The webnames `shred -u` protocol for API key cleanup is security-critical and has been debugged across 3 production incidents (see TRAP[BUG] entries). Porting to Python would require re-testing all acme.sh edge cases (DNS propagation timeouts, LE rate limits, staging server interactions). The subprocess approach preserves this investment while centralizing orchestration (domain iteration, S3 logic, cron management) in Python. This is Strangler-Fig: Python absorbs orchestration, shell stays as the acme.sh executor.

### ## @rationale C6: Harmonize to fullchain.pem/privkey.pem rather than _local.pem/_local-key.pem

**Q:** Why `fullchain.pem`/`privkey.pem` over `_local.pem`/`_local-key.pem`?
**A:** Three factors:
1. `add-vhost.sh` harness already uses `fullchain.pem`/`privkey.pem` — this is the majority consumer.
2. Production Let's Encrypt certs use the same naming (`fullchain.pem`/`privkey.pem`). Consistent naming between dev and production reduces cognitive load: an agent seeing `fullchain.pem` in a dev vhost immediately recognizes it as "the certificate file" without needing to know it's a dev environment.
3. The underscore-prefix convention (`_local.pem`) is non-standard in the nginx ecosystem — it was an arbitrary choice in the first implementation.

### ## @rationale C7: Align platform-vhost to wildcard cert — why not keep separate cert

**Q:** Why not keep a separate cert for `platform.${PLATFORM_DOMAIN}` for isolation?
**A:** The wildcard cert `*.${PLATFORM_DOMAIN}` already covers `platform.${PLATFORM_DOMAIN}`. Having a separate cert:
1. Adds an unnecessary acme.sh API call (separate LE issuance).
2. Creates a failure mode: if the wildcard cert is issued but the platform subdomain cert fails, the platform dashboard is unreachable while other services work — a confusing partial-failure state.
3. The original reason for the separate cert (TRAP[BUG] lines 55-60: DNS-01 was unreliable for wildcards) is no longer applicable — DNS-01 via webnames works reliably.
4. All 5 other vhosts (grafana, loki, prometheus, langfuse, hermes) already use the wildcard. Platform vhost should not be the exception.

### ## @rationale B2: Remove _ssl_cert_provision() from steps.py — why not keep as fallback

**Q:** Why delete `_ssl_cert_provision()` rather than fix it?
**A:** `_ssl_cert_provision()` has a structural flaw — it calls s3-ssl-cache.sh via subprocess, which causes S3 credential propagation failure (the original bug behind DevPlan 052). The fix (`_ssl_provision_via_orchestrator()`) already exists in state_machine.py and uses direct `s3_ssl_cache` Python import (no subshell). Keeping the broken version as "fallback" would mean it never gets called (it's already unreachable from the bootstrap state machine), but its presence in the codebase is misleading. Dead code with known bugs is worse than no code — it invites accidental reuse.

---

## File Manifest

| Action | File | Drift |
|--------|------|-------|
| DELETE | `core/modules/nginx/install.sh` | C1 |
| DELETE | `core/modules/nginx/templates/platform-default.conf.template` | C2 |
| MODIFY | `core/internal/bootstrap/cert_orchestrator.py` | C3, C4 |
| MODIFY | `core/internal/bootstrap/lifecycle/steps.py` | B2 |
| MODIFY | `core/internal/bootstrap/lifecycle/state_machine.py` | B2 (minor — add migrate_cron call) |
| MODIFY | `core/internal/bootstrap/node-lifecycle.sh` | B2 |
| MODIFY | `core/internal/bootstrap/issue-cert.sh` | C3 |
| MODIFY | `core/modules/nginx/config/platform-vhost.conf.template` | C7 |
| MODIFY | `core/modules/nginx/generate-dev-certs.sh` | C6 |
| MODIFY | `core/modules/nginx/dev-config/ssl-dev.conf` | C6 |
| MODIFY | `core/modules/nginx/AGENTS.md` | C8 |
| MODIFY | `core/templates/template-manifest.yaml` | C1 (remove consumer) |
| REFACTOR | `tests/test_nginx_acme.py` | C1 (remove install.sh tests) |
| MODIFY | `tests/test_tls_wildcard.py` | C1 (remove install.sh refs) |
| MODIFY | `tests/test_no_hardcoded_credentials.py` | C1 |
| MODIFY | `tests/unit/test_cert_orchestrator.py` | C3 (add cron/migrate tests) |
| MODIFY | `tests/test_nginx_dev_certs.py` | C6 (update filename checks) |
| NEW | `tests/unit/test_cert_cron_migration.py` | C4 |
| NEW | `tests/test_template_syntax_gate.py` | C8 |

**Total: 19 files (2 DELETE, 14 MODIFY, 3 NEW)**

---

## TRAP Annotations

### TRAP[DECISION] to add

```python
# 🧐 TRAP[DECISION] · 2026-07-25 · — · issue-cert.sh kept as acme.sh executor (not absorbed into Python)
# · Rejected: full Python port of acme.sh DNS-01/HTTP-01 interaction
# · Reason: webnames API key shred protocol + acme.sh edge cases are battle-tested in shell.
#   Python absorption would require re-testing all LE staging/production edge cases.
#   Strangler-Fig: Python absorbs orchestration, shell stays as executor.
# · Rev: when acme.sh interaction stabilizes (no changes for 6+ months), port to Python.
```
(Located in cert_orchestrator.py `_issue_cert()` docstring)

```nginx
# 🧐 TRAP[DECISION] · 2026-07-25 · — · platform-vhost now uses wildcard cert (same as other vhosts)
# · Rejected: keep separate cert for platform.${PLATFORM_DOMAIN}
# · Reason: wildcard *.${PLATFORM_DOMAIN} already covers platform subdomain.
#   Separate cert was a historical workaround for unreliable DNS-01 (resolved).
#   Single cert = simpler failure modes, fewer LE API calls.
# · Rev: if wildcard cert is ever revoked or platform needs different TLS policy, revert.
```
(Located in platform-vhost.conf.template near the `ssl-params.conf` include)

### TRAP[BUSINESS] to verify

Existing TRAP[BUSINESS] at `issue-cert.sh:149` (API key cleanup) — preserved. This security requirement survives the refactoring because issue-cert.sh continues to be the acme.sh executor.

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/080-certs-ssl-unification/01-DevPlan.md, implement Wave 1: TASK-1
```

### Wave 2
```
coder Read .ai/plans/080-certs-ssl-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4
```

### Wave 3
```
coder Read .ai/plans/080-certs-ssl-unification/01-DevPlan.md, implement Wave 3: TASK-5, TASK-6, TASK-7, TASK-8
```

### Wave 4
```
coder Read .ai/plans/080-certs-ssl-unification/01-DevPlan.md, implement Wave 4: TASK-9
```

$END_DEVPLAN
