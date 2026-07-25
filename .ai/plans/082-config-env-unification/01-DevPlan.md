$START_DEVPLAN

# DevPlan 082 — Configuration & Env Defaults Unification

## $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Eliminate 7 systemic configuration drift points (DRIFT-E1 through E8, E3 deferred to 078) plus 1 language policy violation (F4 inline python3 heredoc) in ai-platform environment defaults by establishing a canonical Source-of-Truth hierarchy and auto-generation pipeline for `.env.example`. |
| **DESCRIPTION** | Define `platform-infra.yaml` as SoT for non-secret env defaults, `secret-definitions.yaml` as SoT for secret CI defaults, extend `generate_platform_env.py` to merge both into `platform-env.yaml`, create `sync_env_defaults.py` to auto-generate `.env.example`, add CI gate for drift detection. Fix 7 drift categories (E3 deferred to 078): POSTGRES_PASSWORD (4 conflicting defaults), S3_ENDPOINT_URL (cyclic fallback + 2 hosts), NEXTAUTH_SECRET (deferred to 078), 3 Jinja2 mechanisms (document + gate), variable naming conflicts (6 pairs), PLATFORM_DOMAIN default divergence, NO_PROXY list drift, GF_SECURITY_ADMIN_USER chain fallback. |
| **RATIONALE** | Configuration drift is the #1 source of "works locally, fails on VPS" bugs. 4 different POSTGRES_PASSWORD defaults means a forgotten override silently uses the wrong password in any of 4 contexts. Cyclic S3_ENDPOINT_URL fallback creates a dependency loop that Docker Compose may resolve differently across versions. Without a single SoT and auto-generation, manual sync between .env, .env.example, and compose files is guaranteed to diverge. |
| **ACCEPTANCE_CRITERIA** | 1. `make check-env-defaults` passes (generated .env.example matches SoT). 2. 7 DRIFT-E categories closed, E3 deferred to 078 (verified by grep-invariant tests). 3. `make generate-manifests` regenerates platform-env.yaml with merged env_defaults. 4. `make sync-env-defaults` regenerates .env.example identically. 5. S3_ENDPOINT variable removed from production code — zero references in Python, shell, compose, .env (test files may reference in monkeypatch setup). 6. POSTGRES_PASSWORD has exactly ONE ci_default (`test-pg-pwd`) in secret-definitions.yaml — all 4 consumers reference it. 7. Existing gate tests (`make gate MODE=fast`) remain green. 8. New CI gate `test_gate_env_example_drift.py` passes (with NEXTAUTH_SECRET precondition skip). 9. gen-env-platform.sh has zero inline python3 blocks (thin facade <90 LOC). |
| **IMPLEMENTS** | DevPlan 077 (Systemic Drift Unification) Chapter 6 — Configuration & Env domain |
| **IMPACTS** | `core/platform-infra.yaml` (new `env_defaults` section), `core/internal/scripts/generate_platform_env.py` (extend merge logic), `core/internal/scripts/sync_env_defaults.py` (NEW), `core/internal/scaffold/gen_env_platform.py` (NEW — Tier 1 Strangler extraction), `platform-env.yaml` (regenerated), `.env.example` (regenerated), `.env` (align defaults), `core/modules/hermes-agent/.env` + `.env.example` (align POSTGRES_PASSWORD), `core/modules/backup-cron/docker-compose.base.yml` (remove S3_ENDPOINT), `core/modules/backup-cron/scripts/upload-s3.sh` (fix default), `core/modules/backup-cron/scripts/backup_config.py` (remove fallback), `core/modules/langfuse/docker-compose.base.yml` (fix S3 default), `core/modules/monitoring/docker-compose.base.yml` (simplify chain), `core/internal/bootstrap/preflight.py` (remove fallback), `core/internal/bootstrap/s3_ssl_cache.py` (remove fallback), `tests/test_backup_config.py` (update tests), `tests/test_ssl_s3_cache.py` (update tests), `core/internal/scaffold/gen-env-platform.sh` (fix domain + extract → thin facade), `Makefile` (2 new targets), `tests/gates/test_gate_env_example_drift.py` (NEW), `tests/unit/test_gen_env_platform.py` (NEW), `AGENTS.md` (template mechanism docs) |
| **REQUIRES** | DevPlan 078 (secret defaults unified) — SOFT dependency: NEXTAUTH_SECRET validation skipped in gate test if 078 marker absent (exit 0 with skip message). 082 can merge and deploy independently. See §14. |

---

## 1. Requirements Analysis

### Key Success Criteria

1. **Single SoT per variable type** — every env var with a default has exactly ONE canonical definition site
2. **Auto-generation eliminates manual sync** — `.env.example` is never edited manually after this plan
3. **CI gate catches drift immediately** — `make check-env-defaults` fails if any generated file diverges
4. **S3_ENDPOINT_URL alias eliminated** — zero references to `S3_ENDPOINT` (without `_URL` suffix) in production code (Python, shell, compose, .env)
5. **Backward compatible** — existing `make gate MODE=fast`, `make test`, and `docker compose up` workflows continue working
6. **Language policy compliance** — gen-env-platform.sh inline python3 heredoc extracted to Python module (Tier 1 Strangler), shell becomes thin facade

### Dependency: DevPlan 078

DRIFT-E3 (NEXTAUTH_SECRET) is **scoped OUT** of this DevPlan and deferred to DevPlan 078. The TASK-8 gate test includes a precondition check: if 078 verification marker is absent, NEXTAUTH_SECRET validation is skipped (exit 0). This allows 082 to merge and deploy independently of 078. See §14 for full rationale.

---

## 2. Architecture Overview

### 2.1 SoT Hierarchy (Target State)

```
┌──────────────────────────────────────┐
│ secret-definitions.yaml              │  SoT: ALL secrets (ci_default, charset, gen_command)
│ 31 entries, each with ci_default     │
└──────────────┬───────────────────────┘
               │ ci_default values
               ▼
┌──────────────────────────────────────┐
│ platform-infra.yaml                  │  SoT: ALL non-secret env defaults (NEW env_defaults section)
│ + networks, volumes, proxy, provides │        proxy.no_proxy_internal (canonical NO_PROXY list)
│ + env_defaults (NEW)                 │
└──────────────┬───────────────────────┘
               │ merge
               ▼
┌──────────────────────────────────────┐
│ generate_platform_env.py             │  GENERATOR: reads both SoTs + module discovery
│ → platform-env.yaml                  │  output: merged env_defaults (secret + non-secret)
│ → smoke_env_generated.py             │
│ → env_defaults_generated.py          │
└──────────────┬───────────────────────┘
               │ merged env_defaults + module discovery
               ▼
┌──────────────────────────────────────┐
│ sync_env_defaults.py (NEW)           │  GENERATOR: reads platform-env.yaml + composes .env.example
│ → .env.example                       │  output: documented .env template with all defaults
└──────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│ .env                                 │  MANUAL COPY of .env.example → operator fills real values
│ (gitignored, chmod 600)             │  CI copies .env.example → .env for test runs
└──────────────────────────────────────┘
```

### 2.2 Variable Resolution Order (Runtime)

For any variable `$VAR` at runtime:
1. **Environment** (already set in shell / docker compose `environment:` / `--env-file`) — highest priority
2. **`.env` file** (loaded by docker compose) — operator override
3. **`.env.example` default** — CI/test fallback
4. **compose-level `${VAR:-default}`** — last-resort hardcoded default

The ${VAR:-default} fallback in docker-compose.base.yml remains as the ultimate safety net,
but the DEFAULT value in that fallback MUST match the SoT value in platform-infra.yaml or secret-definitions.yaml.

### 2.3 Draft Code Graph

```
┌─────────────────────────────────────────────────────────────────────┐
│ sync_env_defaults.py (NEW ~200 LOC)                                 │
│ ▶ parse_args(platform_env, secret_defs, modules_dir, output)       │
│   → load_platform_env() ─── read platform-env.yaml                 │
│   → load_secret_defaults() ─ read secret-definitions.yaml          │
│   → discover_module_env_vars() ─ scan docker-compose.base.yml      │
│   → merge_defaults() ──── ⊕ secret ∪ non-secret, dedup           │
│   → generate_env_example() ─ produce .env.example with sections   │
│   → write_atomic() ────── tempfile + os.rename                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ generate_platform_env.py (EXTEND ~40 LOC)                           │
│ ▶ load_infra() ──────── already reads platform-infra.yaml          │
│   → NEW: extract env_defaults from infra dict                      │
│   → NEW: merge env_defaults into output dict                       │
│ ▶ generate_platform_env_yaml() ── NEW: includes env_defaults non-secret │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Step-by-Step Data Flow

### Step 1: Operator runs `make sync-env-defaults`
```
make sync-env-defaults
  → python3 core/internal/scripts/sync_env_defaults.py
      --platform-env platform-env.yaml
      --secret-defs core/secret-definitions.yaml
      --modules-dir core/modules
      --output .env.example
```

### Step 2: `sync_env_defaults.py` loads SoT values
```
load_platform_env() → extract env_defaults dict {VAR: value}
load_secret_defaults() → extract ci_default dict {SECRET: value}
merge → combined dict with all defaults, secrets marked with charset constraints
```

### Step 3: Generate `.env.example` with sections
```
generate_env_example() → write structured output:
  - Header (MODULE_CONTRACT, invariants)
  - Section: Platform / Context
  - Section: Platform secrets
  - Section: Postgres
  - Section: PgBouncer
  - ... (same section order as current .env.example)
  - Section: GitHub Actions secrets (documentation block)
```

### Step 4: CI gate `make check-env-defaults`
```
make check-env-defaults
  → python3 core/internal/scripts/sync_env_defaults.py ... --check
  → if generated content ≠ existing .env.example → fail with diff
  → git diff --exit-code -- .env.example
```

---

## 4. Design Decisions

### DD-1: Non-secret env_defaults live in platform-infra.yaml
**## @rationale**
**Q:** Why platform-infra.yaml and not a separate file?
**A:** platform-infra.yaml is already the SoT for networks, volumes, proxy, and provides — all non-secret infrastructure configuration. Adding env_defaults here keeps exactly ONE non-secret SoT file. Creating a separate env-defaults.yaml would add a third SoT file for the generator to read — unnecessary complexity. platform-infra.yaml is already consumed by generate_platform_env.py, so the plumbing exists.

### DD-2: .env.example generated from template + SoT, not fully dynamic
**## @rationale**
**Q:** Why not generate .env.example entirely from YAML metadata?
**A:** .env.example contains extensive documentation comments (constraint regexes, generation commands, inline warnings) that are valuable human-readable context. Embedding all these as YAML string fields would make the YAML unreadable and lose the benefit of inline documentation. Instead, sync_env_defaults.py uses a structured Python template embedded in the script itself — section order, comment blocks, and CONSTRAINT annotations are defined there, while VALUES are pulled from SoT. This preserves documentation quality while ensuring value consistency.

**Also considered:** External .env.example.tmpl file (rejected: adds another drift vector — template comments can get out of sync with SoT metadata). Pure YAML metadata generation (rejected: loses documentation quality).

### DD-3: S3_ENDPOINT variable eliminated entirely, not deprecated
**## @rationale**
**Q:** Why remove S3_ENDPOINT rather than just unify the default?
**A:** The existence of TWO variable names for the same concept (S3_ENDPOINT_URL and S3_ENDPOINT) creates a cyclic fallback (each falls back to the other when unset). This is not just a naming inconsistency — it's a functional bug: if BOTH are unset, Docker Compose may enter a resolution loop depending on version. Removing the alias eliminates the mechanism that enables the bug. All code already uses S3_ENDPOINT_URL as the primary variable — S3_ENDPOINT is always the fallback target. Zero consumers rely exclusively on S3_ENDPOINT.

### DD-4: GF_SECURITY_ADMIN_USER chain simplified by removing HERMES_DASHBOARD_USERNAME layer
**## @rationale**
**Q:** Why remove HERMES_DASHBOARD_USERNAME from the Grafana fallback chain?
**A:** The current 3-layer chain `${GF_SECURITY_ADMIN_USER:-${HERMES_DASHBOARD_USERNAME:-admin@${PLATFORM_DOMAIN}}}` masks configuration errors. When an operator sets both GF_SECURITY_ADMIN_USER and HERMES_DASHBOARD_USERNAME to different values, removing one silently changes behavior. Grafana should use its own variable or fall back to the platform domain directly — not transitively through an unrelated service's variable. The unified auth model already ensures consistent values through secrets-init.sh — the compose fallback chain should reflect the direct hierarchy, not a cross-service dependency.

### DD-5: 3 Jinja2 mechanisms NOT consolidated — different use cases
**## @rationale**
**Q:** Why keep 3 different template mechanisms?
**A:** Each mechanism serves a fundamentally different domain:
1. `template_engine.py` ({{UPPER_SNAKE}} strict regex) — for nginx config templates where `{{$labels.x}}` (Go/Prometheus templating) and `{{instance}}` (Grafana) must NOT be treated as placeholders. Strict regex prevents false matches.
2. Jinja2 (config_renderer.py) — for LiteLLM config where complex data structures (model_list, fallbacks) require loops, conditionals, and filters that Jinja2 provides.
3. Jinja2 (status-page/app.py) — for HTML rendering where autoescape and template inheritance are required for XSS protection.

Consolidating them into one mechanism would either lose functionality (strict regex can't do loops) or introduce false matches (Jinja2 would match Go/Prometheus templates). The cost of false matches (silent config corruption) exceeds the cost of maintaining 3 mechanisms.

### DD-6: NO_PROXY SoT is platform-infra.yaml, NOT .env.example
**## @rationale**
**Q:** Why move NO_PROXY SoT from .env.example to platform-infra.yaml?
**A:** NO_PROXY is infrastructure configuration (which services are internal), not a secret or operator preference. It should live alongside proxy config (already in platform-infra.yaml proxy section). The current state where platform-infra.yaml defines `no_proxy_internal` with 6 services but .env.example and hermes-agent compose have 11+ creates a drift vector. Making platform-infra.yaml the single SoT and having all consumers reference (or validate against) it eliminates this drift. The gate validates: `.env.example NO_PROXY ⊇ platform-infra no_proxy_internal`.

---

## 5. $TASKS

### TASK-1: Add env_defaults section to platform-infra.yaml + fix no_proxy_internal
- **Owner:** Coder
- **Output:** Updated `core/platform-infra.yaml`
- **Complexity:** 3
- **Dependencies:** None
- **Acceptance Criteria:**
  - New `env_defaults:` section with ALL non-secret vars and their canonical defaults
  - `proxy.no_proxy_internal` expanded to canonical full list: `localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse,litellm,langfuse,minio,grafana,prometheus`
  - YAML is valid (`python3 -c "import yaml; yaml.safe_load(open('core/platform-infra.yaml'))"` succeeds)
- **Files:** `core/platform-infra.yaml` (~50 lines added)

### TASK-2: Extend generate_platform_env.py to merge non-secret env_defaults
- **Owner:** Coder
- **Output:** Updated `core/internal/scripts/generate_platform_env.py`
- **Complexity:** 4
- **Dependencies:** TASK-1 (needs new env_defaults section to exist)
- **Acceptance Criteria:**
  - `generate_platform_env_yaml()` output includes `env_defaults` section merged from BOTH platform-infra.yaml env_defaults AND secret-definitions.yaml ci_defaults
  - Secret ci_defaults take precedence over non-secret env_defaults when both define the same key
  - Generated `platform-env.yaml` contains both secret and non-secret defaults
  - Existing unit tests pass: `python -m pytest tests/unit/test_generate_platform_env.py -v`
- **Files:** `core/internal/scripts/generate_platform_env.py` (~40 lines)

### TASK-3: Create sync_env_defaults.py — .env.example generator
- **Owner:** Coder
- **Output:** NEW `core/internal/scripts/sync_env_defaults.py`
- **Complexity:** 7
- **Dependencies:** TASK-1 (needs env_defaults to exist)
- **Acceptance Criteria:**
  - CLI: `--platform-env`, `--secret-defs`, `--modules-dir`, `--output`, `--check` flags
  - `--check` mode: generates to temp, compares with existing .env.example byte-for-byte, exit 0 if identical, exit 1 with diff on divergence
  - Generated .env.example preserves all current sections, CONSTRAINT comments, and documentation
  - All values in generated output match SoT defaults
  - Atomic write (tempfile + os.rename)
  - Output is valid for `docker compose --env-file .env.example config`
- **Files:** `core/internal/scripts/sync_env_defaults.py` (NEW, ~200 lines)

### TASK-4: Fix POSTGRES_PASSWORD — unify 4 different defaults to ONE
- **Owner:** Coder
- **Output:** Updated .env, hermes-agent/.env, hermes-agent/.env.example, docker-compose.test.yml files
- **Complexity:** 3
- **Dependencies:** None (can run in parallel with TASK-1)
- **Acceptance Criteria:**
  - `grep -r "POSTGRES_PASSWORD" --include="*.env" --include="*.env.example" --include="*.yml" --include="*.yaml" | grep -v "test-pg-pwd"` returns NO matches in default values (compose fallback chains excepted)
  - `.env` line 25: `POSTGRES_PASSWORD=test-pg-pwd`
  - `hermes-agent/.env` line 45: `POSTGRES_PASSWORD=test-pg-pwd`
  - `hermes-agent/.env.example` line 70: `POSTGRES_PASSWORD=test-pg-pwd`
  - All `docker-compose.test.yml` files: POSTGRES_PASSWORD default matches `test-pg-pwd`
- **Files:** `.env`, `core/modules/hermes-agent/.env`, `core/modules/hermes-agent/.env.example`, `core/modules/postgres/docker-compose.test.yml`, `core/modules/backup-cron/docker-compose.test.yml`, `core/modules/langfuse/docker-compose.test.yml`, `core/modules/litellm/docker-compose.test.yml` (~7 files, ~15 lines)

### TASK-5: Fix S3_ENDPOINT_URL — remove S3_ENDPOINT alias, unify defaults
- **Owner:** Coder
- **Output:** Updated compose files, upload script, .env, .env.example, Python modules with S3_ENDPOINT fallbacks, test files
- **Complexity:** 5
- **Dependencies:** None (can run in parallel with TASK-1)
- **Acceptance Criteria:**
  - `grep -r "S3_ENDPOINT[^_]" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.env" --include="*.py"` returns ZERO matches in production code (test files may reference S3_ENDPOINT in monkeypatch setup only — those are cleaned up separately)
  - **Production Python files:** `os.environ.get("S3_ENDPOINT_URL", os.environ.get("S3_ENDPOINT", ...))` fallback pattern simplified to `os.environ.get("S3_ENDPOINT_URL", default)` in:
    - `core/modules/backup-cron/scripts/backup_config.py` (lines 95, 169)
    - `core/internal/bootstrap/preflight.py` (line 424)
    - `core/internal/bootstrap/s3_ssl_cache.py` (line 91)
  - **Test files:** S3_ENDPOINT references in test setup removed or updated to test new single-variable behavior:
    - `tests/test_backup_config.py` (lines 118, 120)
    - `tests/test_ssl_s3_cache.py` (lines 165, 176, 179, 199)
  - `backup-cron/docker-compose.base.yml`: line 67 (S3_ENDPOINT definition) removed, line 66 simplified to `${S3_ENDPOINT_URL:-https://s3.timeweb.cloud}`
  - `backup-cron/scripts/upload-s3.sh:40`: default changed from `https://s3.twcstorage.ru` to `https://s3.timeweb.cloud`
  - `langfuse/docker-compose.base.yml:86`: empty default `""` changed to `https://s3.timeweb.cloud`
  - `.env`: S3_ENDPOINT line removed, S3_ENDPOINT_URL kept with value `https://s3.timeweb.cloud`
  - `.env.example`: S3_ENDPOINT line removed
- **Files:** `core/modules/backup-cron/docker-compose.base.yml`, `core/modules/backup-cron/scripts/upload-s3.sh`, `core/modules/backup-cron/scripts/backup_config.py`, `core/modules/langfuse/docker-compose.base.yml`, `core/internal/bootstrap/preflight.py`, `core/internal/bootstrap/s3_ssl_cache.py`, `tests/test_backup_config.py`, `tests/test_ssl_s3_cache.py`, `.env`, `.env.example` (~10 files, ~40 lines)

### TASK-6: Fix remaining drift points (E5 naming, E6 domain, E7 proxy, E8 chain) + extract inline python3 from gen-env-platform.sh (Tier 1 Strangler)
- **Owner:** Coder
- **Output:** Updated compose files, gen-env-platform.sh, NEW gen_env_platform.py, code references
- **Complexity:** 6
- **Dependencies:** TASK-1 (needs canonical no_proxy_internal to exist), TASK-5 (S3_ENDPOINT already removed)
- **Acceptance Criteria:**
  - **E5 (NODE_NAME):** `grep -r "\bNODE\b" --include="*.sh" --include="*.yml" --include="*.yaml" | grep -v "NODE_NAME\|NODE_HOST_MAP\|NODE_EXPORTER\|NODE_OPTIONS\|_NODE\|node-\|/node"` returns only Makefile argument references (`NODE=<name>` target args) and comments — no variable assignments use bare NODE
  - **E6 (PLATFORM_DOMAIN):** `gen-env-platform.sh:92` default `tronyx.ru` → `ai-platform.local`
  - **E7 (NO_PROXY):** `.env.example` NO_PROXY line unchanged (already has full list), CI gate validates superset relationship
  - **E8 (GF_SECURITY_ADMIN_USER):** `monitoring/docker-compose.base.yml:158` simplified to `${GF_SECURITY_ADMIN_USER:-admin@${PLATFORM_DOMAIN:-ai-platform.local}}` (HERMES_DASHBOARD_USERNAME layer removed)
  - **F4 (inline python3 extraction):** Inline `python3 -c "..."` block (lines 95-189 in gen-env-platform.sh) extracted into NEW `core/internal/scaffold/gen_env_platform.py` with `generate(platform_env_yaml, domain, project_name) → list[str]` function. Shell calls `python3 core/internal/scaffold/gen_env_platform.py --yaml ... --domain ... [--name ...]`. Output is byte-identical to current inline heredoc output. Shell script reduced from 255→~85 lines (thin facade).
  - `make gate MODE=fast` passes after all changes
- **Files:** `core/internal/scaffold/gen-env-platform.sh` (E6 fix + python3 extraction → facade), `core/internal/scaffold/gen_env_platform.py` (**NEW** — extracted business logic), `core/modules/monitoring/docker-compose.base.yml` (E8), grep scan for NODE→NODE_NAME references (E5), `.env.example` (E7 — gate validation only)

### TASK-7: Document 3 template mechanisms in AGENTS.md
- **Owner:** Coder
- **Output:** Updated `AGENTS.md` (root) with template mechanism decision table
- **Complexity:** 2
- **Dependencies:** None
- **Acceptance Criteria:**
  - New section in root AGENTS.md: "## Template Mechanisms" with decision table mapping use case → mechanism → rationale
  - Table covers: nginx configs → template_engine.py ({{UPPER_SNAKE}}), LiteLLM config → config_renderer.py (Jinja2), status-page HTML → Jinja2, Docker Compose → ${VAR}, envsubst → ${VAR}
  - CI gate rule documented: template files in a directory MUST use one mechanism consistently
- **Files:** `AGENTS.md` (~40 lines added)

### TASK-8: Add make targets and CI gate
- **Owner:** Coder
- **Output:** Updated Makefile, NEW CI gate test, regenerated files
- **Complexity:** 6
- **Dependencies:** TASK-2, TASK-3, TASK-5, TASK-6 (generation pipeline + drift fixes must be complete)
- **Acceptance Criteria:**
  - `make sync-env-defaults` regenerates `.env.example` (invokes sync_env_defaults.py)
  - `make check-env-defaults` fails with diff if .env.example diverges from SoT
  - `make check-manifests` extended to include `.env.example` in diff check
  - `make generate-manifests` runs successfully and produces valid platform-env.yaml
  - NEW `tests/gates/test_gate_env_example_drift.py`:
    - Validates .env.example NO_PROXY ⊇ platform-infra.yaml no_proxy_internal
    - Validates all POSTGRES_PASSWORD defaults match secret-definitions.yaml ci_default (`test-pg-pwd`)
    - Validates S3_ENDPOINT variable does not exist in production code (Python, shell, compose, .env)
    - Validates .env.example is byte-identical to sync_env_defaults.py --check output
    - **NEXTAUTH_SECRET precondition:** checks if DevPlan 078 verification marker exists (e.g., `NEXTAUTH_SECRET` ci_default in secret-definitions.yaml with non-placeholder value). If NOT present → skip NEXTAUTH_SECRET validation (`exit 0` with `SKIP: DevPlan 078 not merged — NEXTAUTH_SECRET validation deferred`). If present → validate NEXTAUTH_SECRET ci_default matches .env.example.
  - `make gate MODE=fast` passes
- **Files:** `Makefile` (~25 lines), `tests/gates/test_gate_env_example_drift.py` (NEW, ~100 lines), regenerated `platform-env.yaml`, `.env.example`

### TASK-9: Regenerate all generated files and verify
- **Owner:** Coder
- **Output:** All generated files consistent with SoT
- **Complexity:** 2
- **Dependencies:** TASK-1 through TASK-8 (ALL prior tasks)
- **Acceptance Criteria:**
  - `make generate-manifests && make sync-env-defaults` produces clean git status (no uncommitted changes in generated files)
  - `make check-manifests && make check-env-defaults` both pass
  - `make gate MODE=fast` green
  - `python -m pytest tests/ -s -v -k "not (integration or e2e)"` passes
- **Files:** `platform-env.yaml`, `.env.example`, `tests/_conftest/smoke_env_generated.py`, `tests/helpers/env_defaults_generated.py` (regenerated)

---

## 6. $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks:** TASK-1, TASK-4, TASK-5, TASK-7
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-4, TASK-5, TASK-7`

### Wave 2 (depends on TASK-1)
- **Tasks:** TASK-2, TASK-3, TASK-6
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-6`

### Wave 3 (depends on Wave 2)
- **Tasks:** TASK-8
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-8`

### Wave 4 (depends on Wave 3)
- **Tasks:** TASK-9
- **Command:** `coder Read DevPlan.md, implement Wave 4: TASK-9`

---

## 7. Acceptance Criteria (Summary)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC1 | `make check-env-defaults` passes | CI gate |
| AC2 | 7 DRIFT-E categories closed (E3 deferred to 078), F4 (inline python3) closed | grep-invariant tests in TASK-8 gate |
| AC3 | `make generate-manifests` produces merged env_defaults | platform-env.yaml contains both secret + non-secret defaults |
| AC4 | `make sync-env-defaults` produces byte-identical .env.example | `--check` mode |
| AC5 | S3_ENDPOINT removed from production code — zero references | `grep -r "S3_ENDPOINT[^_]" --include="*.py" --include="*.yml"...` returns empty |
| AC6 | POSTGRES_PASSWORD has ONE ci_default (`test-pg-pwd`) | All 4 consumers match `test-pg-pwd` |
| AC7 | `make gate MODE=fast` green | Existing gate tests pass |
| AC8 | New gate test passes (with NEXTAUTH_SECRET precondition skip) | `test_gate_env_example_drift.py` |
| AC9 | gen-env-platform.sh is a thin facade (<90 LOC), zero inline python3 | `test_no_inline_python3_in_scaffold` gate test |

---

## 8. File Manifest

| File | Action | Lines |
|------|--------|-------|
| `core/platform-infra.yaml` | Modify — add env_defaults section, expand no_proxy_internal | +50 |
| `core/internal/scripts/generate_platform_env.py` | Modify — merge env_defaults from infra | +40 |
| `core/internal/scripts/sync_env_defaults.py` | **CREATE** — .env.example generator | +200 |
| `Makefile` | Modify — add sync-env-defaults, check-env-defaults targets | +25 |
| `tests/gates/test_gate_env_example_drift.py` | **CREATE** — CI gate (with NEXTAUTH_SECRET precondition skip) | +120 |
| `platform-env.yaml` | Regenerate — merged env_defaults | auto |
| `.env.example` | Regenerate — from SoT | auto |
| `.env` | Modify — align POSTGRES_PASSWORD, remove S3_ENDPOINT | ~5 |
| `core/modules/hermes-agent/.env` | Modify — align POSTGRES_PASSWORD | ~3 |
| `core/modules/hermes-agent/.env.example` | Modify — align POSTGRES_PASSWORD (line 70) | ~2 |
| `core/modules/backup-cron/docker-compose.base.yml` | Modify — remove S3_ENDPOINT, fix cyclic fallback | ~5 |
| `core/modules/backup-cron/scripts/upload-s3.sh` | Modify — fix default S3 endpoint | ~2 |
| `core/modules/backup-cron/scripts/backup_config.py` | Modify — remove S3_ENDPOINT fallback (lines 95, 169) | ~5 |
| `core/modules/langfuse/docker-compose.base.yml` | Modify — fix S3 default | ~2 |
| `core/modules/monitoring/docker-compose.base.yml` | Modify — simplify GF_SECURITY_ADMIN_USER chain | ~2 |
| `core/internal/scaffold/gen-env-platform.sh` | Modify — fix PLATFORM_DOMAIN default + extract inline python3 to facade | ~170 |
| `core/internal/scaffold/gen_env_platform.py` | **CREATE** — extracted business logic from gen-env-platform.sh (Tier 1 Strangler) | +180 |
| `core/internal/bootstrap/preflight.py` | Modify — remove S3_ENDPOINT fallback (line 424) | ~2 |
| `core/internal/bootstrap/s3_ssl_cache.py` | Modify — remove S3_ENDPOINT fallback (line 91) | ~2 |
| `tests/test_backup_config.py` | Modify — update S3_ENDPOINT test setup (lines 118, 120) | ~5 |
| `tests/test_ssl_s3_cache.py` | Modify — update S3_ENDPOINT test setup (lines 165,176,179,199) | ~8 |
| `AGENTS.md` (root) | Modify — add template mechanisms section | +40 |
| `core/modules/postgres/docker-compose.test.yml` | Modify — align POSTGRES_PASSWORD (if hardcoded) | ~2 |
| `tests/_conftest/smoke_env_generated.py` | Regenerate | auto |
| `tests/helpers/env_defaults_generated.py` | Regenerate | auto |

**Total: 25 files (8 new/modified logic, 5 auto-regenerated, 12 drift-fixed)**

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_env_example_drift.py` | `test_no_proxy_superset` | `.env.example NO_PROXY ⊇ platform-infra no_proxy_internal` | sync_env_defaults.py |
| `tests/gates/test_gate_env_example_drift.py` | `test_postgres_password_unified` | All POSTGRES_PASSWORD defaults match secret-definitions ci_default (`test-pg-pwd`) | Drift-E1 |
| `tests/gates/test_gate_env_example_drift.py` | `test_s3_endpoint_removed` | Zero references to S3_ENDPOINT (without _URL) in production code | Drift-E2 |
| `tests/gates/test_gate_env_example_drift.py` | `test_env_example_fresh` | .env.example byte-identical to sync_env_defaults.py --check output | sync_env_defaults.py |
| `tests/gates/test_gate_env_example_drift.py` | `test_platform_domain_default` | PLATFORM_DOMAIN default is `ai-platform.local` in gen-env-platform.sh | Drift-E6 |
| `tests/gates/test_gate_env_example_drift.py` | `test_nextauth_secret_precondition` | Skip NEXTAUTH_SECRET validation if 078 marker absent; validate if present | Drift-E3 (deferred) |
| `tests/gates/test_gate_env_example_drift.py` | `test_no_inline_python3_in_scaffold` | gen-env-platform.sh contains zero inline python3 heredoc blocks (thin facade only) | Drift-F4 |
| `tests/unit/test_generate_platform_env.py` | `test_env_defaults_merged` | platform-env.yaml env_defaults contains both secret + non-secret entries | generate_platform_env.py |
| `tests/unit/test_sync_env_defaults.py` | `test_load_platform_env` | Loads and parses platform-env.yaml env_defaults | sync_env_defaults.py |
| `tests/unit/test_sync_env_defaults.py` | `test_load_secret_defaults` | Loads ci_default from secret-definitions.yaml | sync_env_defaults.py |
| `tests/unit/test_sync_env_defaults.py` | `test_generate_output` | Generated .env.example contains required sections | sync_env_defaults.py |
| `tests/unit/test_sync_env_defaults.py` | `test_check_mode_detects_divergence` | --check fails when .env.example diverges | sync_env_defaults.py |
| `tests/unit/test_sync_env_defaults.py` | `test_atomic_write` | Corrupt partial write is not left on disk on error | sync_env_defaults.py |
| `tests/unit/test_gen_env_platform.py` | `test_generate_output_matches_original` | gen_env_platform.py output matches original shell heredoc byte-for-byte | gen_env_platform.py |
| `tests/unit/test_gen_env_platform.py` | `test_cli_args` | CLI --yaml, --domain, --name arguments parse correctly | gen_env_platform.py |

---

## 10. Debt Intake

### DEBT INTAKE AUDIT

**From TRAP scan:** `grep "TRAP\[DEBT\]\|TRAP\[DECISION\]"` across affected files:

| File | TRAP | Classification | Action |
|------|------|---------------|--------|
| `core/modules/monitoring/docker-compose.base.yml:158` | GF_SECURITY_ADMIN_USER chain (not a TRAP — just the drift point) | IN_SCOPE | Fixed in TASK-6 |
| `core/platform-infra.yaml:92` | no_proxy_internal incomplete list | IN_SCOPE | Fixed in TASK-1 |
| Various | No other DEBT TRAPs in env config domain | — | — |

**From .ai/plans/*/Debt.md scan:** No existing Debt.md files in env/config domains. Previous waves (045-077) focused on different domains.

---

## 11. Configuration DRY

### DRY Audit Results

| Variable | Current duplicates | Post-plan state |
|----------|-------------------|-----------------|
| POSTGRES_PASSWORD default | 4 files with different values (.env, .env.example, hermes-agent/.env, hermes-agent/.env.example) + test compose files | 1 SoT (secret-definitions.yaml ci_default) → all consumers reference |
| S3_ENDPOINT_URL default | 3 files with different hosts (backup-cron compose, upload-s3.sh, langfuse compose) | 1 SoT (platform-infra.yaml env_defaults) → all consumers reference |
| PLATFORM_DOMAIN default | 2 files (gen-env-platform.sh, compose chains) | 1 SoT (platform-infra.yaml env_defaults) |
| NO_PROXY list | 3 files (platform-infra.yaml, .env.example, hermes-agent compose) | 1 SoT (platform-infra.yaml proxy.no_proxy_internal) → others validate ⊇ |
| GF_SECURITY_ADMIN_USER chain | 1 file but 3-layer fallback | Simplified to 2-layer direct fallback |

---

## 12. Change Impact (Cascade Check)

**Adding `env_defaults` to platform-infra.yaml cascades to:**
1. `generate_platform_env.py` — must read and merge (TASK-2)
2. `platform-env.yaml` — regenerated output (TASK-9)
3. `sync_env_defaults.py` — reads merged env_defaults (TASK-3)
4. `.env.example` — regenerated output (TASK-9)
5. `tests/unit/test_generate_platform_env.py` — must test new merge logic (TASK-2 scope)
6. `tests/gates/test_gate_env_example_drift.py` — must validate consistency (TASK-8)

**Removing S3_ENDPOINT cascades to:**
1. `backup-cron/docker-compose.base.yml` — remove line 67, fix line 66 (TASK-5)
2. `backup-cron/scripts/upload-s3.sh` — fix line 40 default (TASK-5)
3. `backup-cron/scripts/backup_config.py` — remove S3_ENDPOINT fallback at lines 95, 169 (TASK-5)
4. `preflight.py` — remove S3_ENDPOINT fallback at line 424 (TASK-5)
5. `s3_ssl_cache.py` — remove S3_ENDPOINT fallback at line 91 (TASK-5)
6. `langfuse/docker-compose.base.yml` — fix line 86 default (TASK-5)
7. `.env` — remove S3_ENDPOINT line (TASK-5)
8. `.env.example` — remove S3_ENDPOINT line (TASK-5, then regenerated by TASK-9)
9. `tests/test_backup_config.py` — update S3_ENDPOINT test setup (TASK-5)
10. `tests/test_ssl_s3_cache.py` — update S3_ENDPOINT test setup (TASK-5)

**Extracting inline python3 from gen-env-platform.sh cascades to:**
1. `core/internal/scaffold/gen_env_platform.py` — NEW Python module with business logic (TASK-6)
2. `core/internal/scaffold/gen-env-platform.sh` — reduced to thin facade ~85 LOC (TASK-6)
3. `tests/unit/test_gen_env_platform.py` — NEW unit tests for extracted module ($TEST_SPEC)
4. All callers of gen-env-platform.sh — CLI interface preserved, no callers need changes

---

## 13. Contracts

### sync_env_defaults.py CLI Contract

```
usage: sync_env_defaults.py --platform-env PATH --secret-defs PATH
                             [--modules-dir PATH] --output PATH [--check]

Required:
  --platform-env PATH    Path to platform-env.yaml (generated)
  --secret-defs PATH     Path to core/secret-definitions.yaml
  --output PATH          Path to write .env.example

Optional:
  --modules-dir PATH     Path to core/modules/ (default: core/modules)
  --check                Dry-run: generate to temp, diff with --output, exit 0 if identical

Exit codes:
  0 — success (or --check passed)
  1 — generation error (YAML parse failure, missing sections)
  2 — --check mode: divergence detected (writes diff to stderr)
```

### generate_platform_env.py Extended Contract

```
Existing contract unchanged. New behavior:
  - load_infra() now extracts `env_defaults` dict from platform-infra.yaml
  - generate_platform_env_yaml() merges:
      1. Non-secret env_defaults from platform-infra.yaml (lower priority)
      2. Secret ci_defaults from secret-definitions.yaml (higher priority)
    When a key exists in both, secret-definitions ci_default wins.
```

### gen_env_platform.py Contract (NEW — TASK-6 Tier 1 Strangler extraction)

```
usage: gen_env_platform.py --yaml PATH [--name NAME] [--domain DOMAIN]

Required:
  --yaml PATH    Path to platform-env.yaml

Optional:
  --name NAME    Project name (replaces {NAME} placeholder in DSN templates)
  --domain DOMAIN Platform domain (default: ai-platform.local)

Output:
  Writes PLATFORM_* env vars to stdout (one per line).
  Output is byte-identical to current inline python3 heredoc in gen-env-platform.sh.

Exit codes:
  0 — success
  1 — YAML parse error or missing provides: section

Functions:
  generate(platform_env_yaml: dict, domain: str, project_name: str | None) -> list[str]
```

---

## 14. DRIFT-E3 — Scoped OUT, Deferred to DevPlan 078

DRIFT-E3 (NEXTAUTH_SECRET — 4 different test values across .env.example and compose test files) is **scoped OUT** of DevPlan 082 and **deferred to DevPlan 078** (Secret Defaults Unification). Since 078 does not yet exist at the time of 082 implementation:

- **Gate precondition:** `test_gate_env_example_drift.py` includes a precondition check: if the DevPlan 078 verification marker is NOT present (NEXTAUTH_SECRET ci_default in secret-definitions.yaml is still a placeholder or 078's artifacts are absent), the NEXTAUTH_SECRET validation is **skipped** with `exit 0` and message `SKIP: DevPlan 078 not merged — NEXTAUTH_SECRET validation deferred`.
- **When 078 is later merged:** the precondition is satisfied automatically — the gate test will validate NEXTAUTH_SECRET without any modification to 082 code or tests.
- **No DRIFT-E3 fixes in this plan** — no .env.example changes, no compose changes for NEXTAUTH_SECRET. All E3 work belongs to 078.

## @rationale (E3 deferral)
**Q:** Why not include E3 in 082 with a fallback?
**A:** NEXTAUTH_SECRET is a secret — its ci_default must be defined in secret-definitions.yaml alongside all other secrets (31 entries). Adding it here without the unified secret model from 078 creates a drift vector: 082 would define the single SoT, but 078 might redefine it differently. The precondition-based skip ensures 082 can merge independently while 078 provides the authoritative default later. The gate self-heals — once 078 is merged, the precondition passes and NEXTAUTH_SECRET is validated without touching 082.

---

## Next Steps

### Wave 1
```
coder role: Read /Users/tronyx/projects/ai-platform/.ai/plans/082-config-env-unification/01-DevPlan.md, implement Wave 1: TASK-1, TASK-4, TASK-5, TASK-7
```

### Wave 2
```
coder role: Read /Users/tronyx/projects/ai-platform/.ai/plans/082-config-env-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-6
```

### Wave 3
```
coder role: Read /Users/tronyx/projects/ai-platform/.ai/plans/082-config-env-unification/01-DevPlan.md, implement Wave 3: TASK-8
```

### Wave 4
```
coder role: Read /Users/tronyx/projects/ai-platform/.ai/plans/082-config-env-unification/01-DevPlan.md, implement Wave 4: TASK-9
```

$END_DEVPLAN
