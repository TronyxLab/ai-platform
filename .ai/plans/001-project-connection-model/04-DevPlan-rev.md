$START_DEVPLAN
# $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Revision of 02-DevPlan.md per QA audit 03-VerificationReport.md (DRIFTED/CRITICAL) — corrects F1–F10 findings, updates architecture from cross-repo-checkout to org-level-variables model |
| **DESCRIPTION** | Addresses all 3 CRITICAL (F1: private repo blocks GITHUB_TOKEN, F2: mirror vs node-configs conflict, F3: DSN postgres:6432 non-routable), 3 HIGH (F4: orphaned schema task, F5: 20-line AC vs doc-headers gate, F6: phantom delete #13), 4 MEDIUM (F7–F10). 28 files touched, 18 tasks, 6 waves. Key architectural change: NODE_HOST_MAP org-level Actions variable replaces cross-repo checkout — truly zero-secret, eliminates mirror conflict. |
| **RATIONALE** | Original plan premise — "GITHUB_TOKEN has contents:read on TronyxLab/ai-platform" — is factually incorrect for private repos. Org-level variables are the only zero-secret mechanism that works across private repos in the same org. This also resolves the mirror conflict (node-configs no longer need to live in the mirror) and eliminates the dead composite action (F10). |
| **ACCEPTANCE_CRITERIA** | AC1: `make new-project NAME=test TEMPLATE=frontend` creates deployable project in one command. AC2: Project contains ≤7 platform files. AC3: CI passes without NODE_CONFIGS_TOKEN (uses org variable, zero project secrets). AC4: `grep -c "^PLATFORM_" .env.platform` ≥ 8 with correct host:port pairs. AC5: Auto-domain `<name>.tronyx.ru` when no `--domain`. AC6: Manual domain works (`DOMAIN=myapp.com`). AC7: Platform CI upgrade propagates to all projects automatically. AC8: `python -m pytest tests/ -s -v` all pass; `make gate MODE=fast` green. |
| **IMPLEMENTS** | Brief 01-Brief.md (D1–D4, corrected); QA audit 03-VerificationReport.md F1–F10 fixes |
| **IMPACTS** | `.github/workflows/deploy-project.yml` (NEW, rewritten), `.github/actions/resolve-node/` (DELETE), `platform-env.yaml` (provides: with explicit host/port), `core/internal/scaffold/gen-env-platform.sh` (NEW), `core/internal/scaffold/add-project.sh`, `core/internal/scaffold/add-vhost.sh`, `core/entrypoints/scaffold.sh`, `Makefile`, `core/entrypoint-manifest.yaml`, `core/AGENTS.md`, `core/schemas/ai-platform.schema.json`, `core/lib/node-resolver.sh`, templates ×3 (deploy.yml, docker-compose.yml, ai-platform.yaml, .env.platform, README), tests ×2 NEW |
| **REQUIRES** | `NODE_HOST_MAP` org-level Actions variable in TronyxLab (JSON: `{"tronyx-vps":"<host>"}`); `CI_DEPLOY_KEY` org-level secret; `PLATFORM_DOMAIN`, `PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE` in `.env` |

$END_ARTIFACT_CONTRACT

---

## Revision Summary: Audit Findings Addressed

| Finding | Severity | Fix in this revision |
|---------|----------|---------------------|
| F1 | CRITICAL | Org-level `vars.NODE_HOST_MAP` replaces cross-repo checkout — zero-secret, no GITHUB_TOKEN scope issue |
| F2 | CRITICAL | Node-configs removed from mirror; host data lives in org variable — no mirror conflict |
| F3 | CRITICAL | `provides:` gets `host:` + `port:` per service; postgres DSN emits `pgbouncer:6432`, not `postgres:6432` |
| F4 | HIGH | **T16**: explicit schema update task — add `platform_domain` to root + `needs` properties |
| F5 | HIGH | AC: deploy.yml ≤40 total lines, ≤15 non-comment lines; test measures effective content, not headers |
| F6 | HIGH | Templates never had `.github/actions/resolve-node/` — path deleted from manifest; T15 asserts deploy.yml does NOT reference `./.github/actions/resolve-node` |
| F7 | MEDIUM | ORG/NODE defaults from `.env` (`PLATFORM_ORG`, `PLATFORM_DEFAULT_NODE`); Makefile passes `ORG=` and `NODE=`; scaffold bridge translates to named args |
| F8 | MEDIUM | `provides:` keys constrained to `⊆ profiles`; T3+T14 enforce this; nginx-proxy → `nginx` |
| F9 | MEDIUM | Verb pinned: `project-sync-env` everywhere; root AGENTS.md added to File Manifest |
| F10 | MEDIUM | **T17**: delete `.github/actions/resolve-node/` entirely — dead code, replaced by org variable |
| F11 | LOW | Internal consistency: 28 files in manifest, 18 tasks, 6 waves |
| F12 | WARNING | Pre-existing debt noted; out of scope for this revision |
| F14 | INFO | Edge case reworded: `env_file: .env.platform` is single-file; `environment:` override clarified |
| F15 | INFO | Tests use fixture-derived PLATFORM_DOMAIN, never hardcode `tronyx.ru` |

---

## Architecture Overview (Revised)

### Key Architectural Decision: Org-Level Actions Variable

```
┌─ TronyxLab Org Settings ──────────────────────────────────────────┐
│  Actions secrets and variables                                     │
│  ├─ CI_DEPLOY_KEY (org secret)  ← SSH private key for ci-deploy   │
│  └─ NODE_HOST_MAP (org variable) ← {"tronyx-vps": "1.2.3.4"}      │
│                                                                     │
│  All repos in TronyxLab can read vars.NODE_HOST_MAP                │
│  (org variables are readable by all repos in the org — zero auth)  │
└─────────────────────────────────────────────────────────────────────┘

## @rationale Q: Why org variable instead of cross-repo checkout?
A: GITHUB_TOKEN is scoped to the triggering repository only. For a project
   repo to checkout TronyxLab/ai-platform (PRIVATE), it needs a PAT — which
   contradicts the "zero-secret" premise. Org-level Actions variables are
   readable by all repos in the org without any token. node.host is explicitly
   non-secret (per existing TRAP in template deploy.yml:34–41).

## @rationale Q: Why not fine-grained PAT?
A: 1 secret vs 0 secrets. The architectural intent of Brief D1 was zero manual
   secrets. Org variables achieve this — they're set once at org level and
   auto-available to all repos.

## @rationale Q: Why not make the mirror public?
A: Private infra posture. The mirror contains deployment configurations that
   should not be publicly accessible. Org variables carry only non-secret
   host metadata.
```

### Draft Code Graph — Revised

```
┌─ .github/workflows/deploy-project.yml (reusable, in platform repo) ──────┐
│  on: workflow_call                                                        │
│  inputs: project_name, environment                                        │
│  secrets: CI_DEPLOY_KEY (from org, inherited via secrets:inherit)         │
│  jobs:                                                                    │
│    resolve-node:                                                          │
│      checkout@v7  # caller project (ai-platform.yaml)                    │
│      read target_node from ai-platform.yaml                               │
│      resolve ssh_host from fromJson(vars.NODE_HOST_MAP)[target_node]     │
│      → outputs: ssh_host                                                  │
│    build-image:                                                           │
│      docker/login-action ghcr.io (github.token)                          │
│      docker/build-push-action → ghcr.io/<caller-repo>:sha                │
│    deploy:                                                                │
│      appleboy/ssh-action ci-deploy@<ssh_host>                            │
│      key: ${{ secrets.CI_DEPLOY_KEY }}  ← org-level, inherited           │
│      forced-command: myapp <sha> production                               │
│                                                                           │
│  ⚡ ZERO cross-repo checkouts. ZERO project-level secrets beyond           │
│     auto-provided GITHUB_TOKEN.                                            │
└───────────────────────────────────────────────────────────────────────────┘

┌─ core/internal/scaffold/gen-env-platform.sh ─────────────────────────────┐
│  Input: platform-env.yaml (provides section with host + port per service) │
│         ai-platform.yaml (project name)                                   │
│  Output: .env.platform                                                    │
│  Algorithm:                                                               │
│    read platform-env.yaml provides → iterate services                     │
│    for each service:                                                      │
│      emit PLATFORM_<SERVICE>_HOST = <provides.SERVICE.host>              │
│      emit PLATFORM_<SERVICE>_PORT = <provides.SERVICE.port>              │
│      if has dsn_template: substitute ${NAME}, ${HOST}, ${PORT}           │
│      if has url_template: substitute ${HOST}, ${PORT}                    │
│    emit PLATFORM_PROVIDES (comma-separated list)                          │
│    emit PLATFORM_PROXY_NET, PLATFORM_SHARED_DB_NET                        │
│    emit PLATFORM_NO_PROXY                                                 │
│    stamp header: "# GENERATED by ai-platform — DO NOT EDIT"               │
│                                                                           │
│  ⚡ F3 FIX: postgres host = pgbouncer, port = 6432 (correct facade)       │
│  ⚡ F8 FIX: provides keys validated ⊆ profiles                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### Revised Data Flow: CI (git push → deploy)

```
git push to main
     │
     ▼
┌─ Project: .github/workflows/deploy.yml (~15 non-comment lines) ──────────┐
│  name: Deploy                                                            │
│  on: push                                                                │
│  jobs:                                                                   │
│    deploy:                                                               │
│      uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml@main│
│      with:                                                               │
│        project_name: myapp                                               │
│      secrets: inherit                                                    │
└───────────────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─ Platform: deploy-project.yml (reusable) ─────────────────────────────────┐
│  job.resolve-node:                                                        │
│    ├─ checkout@v7  # project repo → ai-platform.yaml                     │
│    ├─ python3: read target_node from ai-platform.yaml                    │
│    ├─ python3: fromJson(vars.NODE_HOST_MAP)[target_node] → ssh_host     │
│    └─ output: ssh_host                                                   │
│                                                                           │
│  ⚡ No second checkout. No cross-repo access. No token beyond              │
│     auto-provided GITHUB_TOKEN (for project checkout + ghcr.io push).     │
│                                                                           │
│  job.build-image:                                                         │
│    ├─ docker/login-action ghcr.io (github.token)                        │
│    └─ docker/build-push-action → ghcr.io/tronyxlab/myapp:sha            │
│                                                                           │
│  job.deploy:                                                              │
│    └─ appleboy/ssh-action ci-deploy@<ssh_host>                          │
│       key: ${{ secrets.CI_DEPLOY_KEY }}  ← org-level, inherited          │
│       script: myapp ${{ github.sha }} production                         │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions (Revised)

### DD1 (revised): `uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml@main` with `secrets: inherit`

**@rationale** Q: Why `@main` instead of version tag? A: Same as original DD1 — auto-update semantics. Projects track `@main` for single-point update. If stability needed, projects pin to `@v2`.

**@rationale** Q: Why `secrets: inherit`? A: Minimizes project boilerplate. The reusable workflow declares `CI_DEPLOY_KEY` — inherited from org level. Adding new optional secrets doesn't require updating every project.

### DD2 (revised): `gen-env-platform.sh` generates DSNs with explicit `host`+`port` from `platform-env.yaml provides:`

**@rationale** Q: Why explicit `host` per service in provides? A: The container name != the connection target. Postgres container is named `postgres` but applications connect through `pgbouncer:6432`. The `provides:` section MUST carry the correct connection host. Without this, the generated DSN is non-routable (F3).

### DD3: Auto-domain = `${NAME}.${PLATFORM_DOMAIN}` — unchanged from original

### DD4 (revised): Reusable workflow resolves node via `vars.NODE_HOST_MAP`, NOT via checkout

**@rationale** Q: Why org variable instead of checkout? A: (1) GITHUB_TOKEN cannot checkout private repos across org boundaries — the original premise was incorrect (F1). (2) node-configs committed directly to mirror conflict with mirror sync (F2). (3) `node.host` is explicitly non-secret per existing TRAP. Org variables provide zero-secret, zero-checkout access to non-secret host metadata.

**@rationale** Q: What about the composite action `.github/actions/resolve-node/`? A: Deleted (T17). It clones a non-existent repo (`tronyx-lab/platform` — F10) and requires a PAT. It is replaced entirely by the org-variable approach. Projects never had it copied (F6: path doesn't exist in templates).

### DD5: `env_file: .env.platform` in docker-compose.yml — unchanged

### DD6: `make project-sync-env` regenerates `.env.platform` — unchanged, name pinned (F9)

### DD7 (NEW): ORG/NODE defaults from `.env` with hardcoded fallbacks

**@rationale** Q: Where do ORG and NODE defaults come from? A: `.env` variables `PLATFORM_ORG` (default: `tronyxlab`) and `PLATFORM_DEFAULT_NODE` (default: `tronyx-vps`). The Makefile reads these and passes them to `scaffold.sh`. The scaffold bridge translates positional args to named args for `add-project.sh`. This fixes the broken Makefile→add-project chain (F7).

### DD8 (NEW): `provides:` keys ⊆ `profiles` — enforced at generation and test time

**@rationale** Q: Why constrain provides keys to profile names? A: `profiles` are 1:1 with `core/modules/` directory names (platform-env.yaml invariant). If a `provides:` key doesn't match a profile, the generated `.env.platform` references a non-existent service — drift vector. The constraint prevents this. Postgres provides → profile `postgres`, not `pgbouncer` (pgbouncer is an implementation detail of the postgres module). Fixes F8.

---

## Revised `.env.platform` Contract

```
# GENERATED by ai-platform — DO NOT EDIT
# Source: platform-env.yaml + ai-platform.yaml
PLATFORM_DOMAIN=tronyx.ru
PLATFORM_PROVIDES=postgres,redis,litellm,langfuse,minio,clickhouse,nginx
PLATFORM_POSTGRES_HOST=pgbouncer       ← F3 FIX: connection facade, not container name
PLATFORM_POSTGRES_PORT=6432
PLATFORM_POSTGRES_DSN=postgresql://myapp_user:***@pgbouncer:6432/myapp_db
PLATFORM_REDIS_HOST=redis
PLATFORM_REDIS_PORT=6379
PLATFORM_REDIS_URL=redis://redis:6379
PLATFORM_LITELLM_HOST=litellm
PLATFORM_LITELLM_PORT=4000
PLATFORM_LITELLM_URL=http://litellm:4000
PLATFORM_LANGFUSE_HOST=langfuse
PLATFORM_LANGFUSE_PORT=3001
PLATFORM_LANGFUSE_URL=http://langfuse:3001
PLATFORM_PROXY_NET=proxy-net
PLATFORM_SHARED_DB_NET=shared-db-net
PLATFORM_NO_PROXY=localhost,127.0.0.1,.local,pgbouncer,redis,clickhouse
```

### `provides:` section in `platform-env.yaml` (new)

```yaml
provides:
  postgres:                          # key = profile name (F8)
    host: pgbouncer                  # connection facade (F3)
    port: 6432
    dsn_template: "postgresql://${NAME}_user:***@${HOST}:${PORT}/${NAME}_db"
    networks: [shared-db-net]
  redis:
    host: redis
    port: 6379
    url_template: "redis://${HOST}:${PORT}"
    networks: [shared-cache-net]
  litellm:
    host: litellm
    port: 4000
    url_template: "http://${HOST}:${PORT}"
    networks: [proxy-net]
  langfuse:
    host: langfuse
    port: 3001
    url_template: "http://${HOST}:${PORT}"
    networks: [proxy-net]
  minio:
    host: minio
    port: 9000
    url_template: "http://${HOST}:${PORT}"
    networks: [proxy-net]
  clickhouse:
    host: clickhouse
    port: 8123
    url_template: "http://${HOST}:${PORT}"
    networks: [proxy-net]
  nginx:                             # NOT nginx-proxy (F8: matches profile name)
    host: nginx-proxy
    port: 80
    networks: [proxy-net]
```

---

## $TASKS (Revised — 18 tasks, 6 waves)

### Task Decomposition

| ID | Task | Role | Output | Deps | Complexity | Acceptance Criteria |
|----|------|------|--------|------|------------|---------------------|
| T1 | Create reusable workflow `deploy-project.yml` — org-variable model | Coder | `.github/workflows/deploy-project.yml` (NEW) | — | 7 | Workflow uses `vars.NODE_HOST_MAP` via `fromJson()`; no cross-repo checkout; `on.workflow_call` schema valid; resolves ssh_host from org variable |
| T2 | Add `provides:` section to `platform-env.yaml` | Coder | `platform-env.yaml` (updated) | — | 4 | YAML parses; ≥7 services with host/port/dsn_template or url_template; postgres host = `pgbouncer`, port = 6432; all keys ∈ profiles set |
| T3 | Create `gen-env-platform.sh` generator | Coder | `core/internal/scaffold/gen-env-platform.sh` (NEW) | T2 | 6 | Reads provides from platform-env.yaml; substitutes ${NAME}/${HOST}/${PORT}; emits `pgbouncer:6432` for postgres DSN; `grep -c "^PLATFORM_"` ≥ 8; header present; validates provides keys ⊆ profiles |
| T4 | Update `scaffold.sh` entrypoint — `project-sync-env` subcommand, named-arg bridge | Coder | `core/entrypoints/scaffold.sh` (updated) | T3 | 4 | `scaffold.sh project-sync-env` delegates to gen-env-platform.sh; `scaffold.sh new-project` translates positional→named args (NAME→--name, etc.); passes ORG/NODE from env vars |
| T5 | Update `add-project.sh` — auto-domain, gen-env integration, skip platform-deploy copy | Coder | `core/internal/scaffold/add-project.sh` (updated) | T3, T4 | 7 | `add-project.sh --name foo --template frontend --org X --node Y` (no --domain) auto-generates `foo.tronyx.ru`; `.env.platform` created in project dir; `platform-deploy.yml` NOT copied; `create_github_repo()` behavior unchanged (F12 — pre-existing debt, out of scope) |
| T6 | Update `add-vhost.sh` — third-level domain detection refinement | Coder | `core/internal/scaffold/add-vhost.sh` (updated) | — | 3 | `*.tronyx.ru` domain uses wildcard cert path; explicit non-subdomain gets own cert_path |
| T7 | Update `node-resolver.sh` — org-variable context support | Coder | `core/lib/node-resolver.sh` (updated) | — | 3 | When called from CI context, resolves host from `NODE_HOST_MAP` env var (JSON); falls back to file-based resolution for local use |
| T8 | Update template-frontend | Coder | 5 updated + 2 deleted in `templates/template-frontend/` | T5 | 4 | deploy.yml ≤40 total lines, ≤15 non-comment; `uses: TronyxLab/AI-platform/...`; docker-compose `env_file: .env.platform`; ai-platform.yaml has `platform_domain`; `platform-deploy.yml` DELETED; `.env.platform` placeholder added; README updated |
| T9 | Update template-backend | Coder | 5 updated + 2 deleted in `templates/template-backend/` | T5 | 4 | Same criteria as T8, backend-specific |
| T10 | Update template-fullstack | Coder | 5 updated + 2 deleted in `templates/template-fullstack/` | T5 | 4 | Same criteria as T8, fullstack-specific (2 Dockerfiles, 2 services) |
| T11 | Update `Makefile` — new `project-sync-env`, updated `new-project` | Coder | `Makefile` (updated) | T4, T5 | 3 | `make new-project NAME=x TEMPLATE=y [ORG=...] [NODE=...] [DOMAIN=...]` works end-to-end; `make project-sync-env PROJECT=<dir>` regenerates .env.platform; ORG/NODE read from .env with defaults |
| T12 | Update `entrypoint-manifest.yaml` — register `project-sync-env` | Coder | `core/entrypoint-manifest.yaml` (updated) | T11 | 2 | Manifest entry for `project-sync-env` with mechanism, delegates_to, description |
| T13 | Update `core/AGENTS.md` — add `project-sync-env` | Coder | `core/AGENTS.md` (updated) | T11 | 2 | New row in canonical operations table; root `AGENTS.md` glossary updated (F9) |
| T14 | Create `tests/test_scaffold_env_platform.py` | Coder | `tests/test_scaffold_env_platform.py` (NEW) | T3 | 6 | Tests: header, min_vars, provides_list, dsn_format (host:port routable check), no_proxy_internal, idempotent, missing_yaml, provides_keys_in_profiles (F8 gate), dsn_host_is_pgbouncer (F3 regression) |
| T15 | Create `tests/test_project_ci_contract.py` | Coder | `tests/test_project_ci_contract.py` (NEW) | T1 | 6 | Tests: deploy.yml ≤40 lines total, ≤15 non-comment; `uses:` correct path; reusable workflow has valid schema; no `NODE_CONFIGS_TOKEN`; uses `vars.NODE_HOST_MAP`; template deploy.yml does NOT reference `./.github/actions/resolve-node` (F6 regression); `platform-deploy.yml` deleted from templates |
| T16 | Update `ai-platform.schema.json` — add `platform_domain` field | Coder | `core/schemas/ai-platform.schema.json` (updated) | — | 3 | `platform_domain` added to root `properties` AND `needs.properties`; schema validates templates with new field; existing tests pass (F4 fix) |
| T17 | Delete dead `.github/actions/resolve-node/` composite action | Coder | 1 deleted | — | 1 | `rm -rf .github/actions/resolve-node/`; no remaining references to the deleted action in platform code (F6, F10 fix) |
| T18 | Validation gate — run `make gate MODE=fast` | QA | Gate output | T1–T17 | 3 | All gates pass: validate → lint → gates → static → predeploy |

### Critical Path

```
T2 → T3 → T4 → T5 → T8,T9,T10 → T11 → T12,T13 → T18
                       ↘ T14
T1 → T15 ──────────────┘
T6, T7, T16, T17 (parallel, no deps on T1–T5, wave-mergeable)
```

Critical path length: **8 tasks** (T2→T3→T4→T5→T8/T9/T10→T11→T12/T13→T18).

---

## $PARALLEL_GROUPS (Revised)

### Wave 1 — Independent Foundation (no shared files among T1, T2, T6, T7, T16, T17)
- **T1**: Create `deploy-project.yml` reusable workflow (org-variable model)
- **T2**: Update `platform-env.yaml` with `provides:` (pgbouncer:6432)
- **T6**: Update `add-vhost.sh` third-level domain
- **T7**: Update `node-resolver.sh` org-variable context
- **T16**: Update `ai-platform.schema.json` — `platform_domain` field (F4 fix)
- **T17**: Delete dead `.github/actions/resolve-node/` (F6, F10 fix)

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 1: T1, T2, T6, T7, T16, T17"
```

### Wave 2 — Generator + Entrypoint (depends on Wave 1 T2)
- **T3**: Create `gen-env-platform.sh` (depends on T2 for provides schema)
- **T4**: Update `scaffold.sh` (depends on T3 for sync-env delegation)

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 2: T3, T4"
```

### Wave 3 — Core Script Update (depends on Wave 2)
- **T5**: Update `add-project.sh` (depends on T3, T4)

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 3: T5"
```

### Wave 4 — Templates (depends on Wave 3; parallel across templates)
- **T8**: Update template-frontend
- **T9**: Update template-backend
- **T10**: Update template-fullstack

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 4: T8, T9, T10"
```

### Wave 5 — Infrastructure + Tests (depends on Waves 2–4; independent sub-groups)
- **Group A** (Makefile + manifest + docs): T11, T12, T13
- **Group B** (tests, depends on T1+T3): T14, T15

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 5: T11, T12, T13, T14, T15"
```

### Wave 6 — Validation Gate
- **T18**: Run `make gate MODE=fast`

```bash
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 6: T18 — run make gate MODE=fast and report results"
```

---

## $TEST_SPEC (Revised)

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_has_header` | Generated .env.platform starts with `# GENERATED by ai-platform — DO NOT EDIT` | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_min_vars` | Output has ≥8 `PLATFORM_*` lines | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_provides_list` | `PLATFORM_PROVIDES` comma-separated list matches platform-env.yaml provides keys | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_dsn_format` | DSN variables follow `scheme://user:***@host:port/db`; host = pgbouncer, port = 6432 (F3 regression) | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_dsn_host_routable` | DSN host:port pairs correspond to valid service:port from docker-compose (F3 regression) | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_no_proxy_internal` | `PLATFORM_NO_PROXY` contains `pgbouncer,redis` | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_idempotent` | Second run produces identical output (same inputs) | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_missing_yaml` | Graceful error when platform-env.yaml not found | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_provides_in_profiles` | All provides keys ⊆ platform-env.yaml profiles (F8 gate) | `gen-env-platform.sh` |
| `tests/test_project_ci_contract.py` | `test_deploy_yml_calls_reusable_workflow` | Template deploy.yml ≤40 lines total, ≤15 non-comment; contains `uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml` (F5 fix) | Template deploy.yml |
| `tests/test_project_ci_contract.py` | `test_deploy_yml_no_resolve_node_action` | Template deploy.yml does NOT reference `./.github/actions/resolve-node` (F6 regression) | Template deploy.yml |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_schema` | `deploy-project.yml` has valid `on.workflow_call` with required inputs: `project_name` | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_no_node_configs_token` | Workflow does NOT reference `NODE_CONFIGS_TOKEN` or `node_configs_token` | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_uses_org_variable` | Workflow uses `vars.NODE_HOST_MAP` (not checkout of TronyxLab/AI-platform for configs) | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_platform_deploy_yml_deleted_from_templates` | `templates/*/.github/workflows/platform-deploy.yml` does not exist | Template filesystem |
| `tests/test_project_ci_contract.py` | `test_template_has_env_platform` | Each template has `.env.platform` placeholder | Template filesystem |
| `tests/test_templates.py` (existing) | `test_template_validates_against_schema` — updated for new `platform_domain` field | Existing test passes after T16 schema update | Template ai-platform.yaml |

---

## Acceptance Criteria (Summary, Revised)

| AC | Criterion | Verification Method |
|----|-----------|---------------------|
| AC1 | `make new-project NAME=test TEMPLATE=frontend` creates deployable project | Manual: scaffold → git push → CI green |
| AC2 | ≤7 platform files per project | `find project/ -name "*.yml" -o -name "*.yaml" -o -name ".env*" \| wc -l` → ≤7 (ai-platform.yaml, Dockerfile, docker-compose.yml, .env.platform, nginx/default.conf, deploy.yml, README.md) |
| AC3 | CI without NODE_CONFIGS_TOKEN | CI log: no reference to `NODE_CONFIGS_TOKEN`; uses `vars.NODE_HOST_MAP` |
| AC4 | .env.platform has ≥8 PLATFORM_ vars with correct host:port | `grep -c "^PLATFORM_" .env.platform` ≥ 8; postgres DSN host = pgbouncer:6432 |
| AC5 | Auto-domain `${NAME}.tronyx.ru` | `make new-project NAME=foo` → ai-platform.yaml `needs.domain: foo.tronyx.ru` |
| AC6 | Manual domain works | `make new-project NAME=foo DOMAIN=myapp.com` → vhost for myapp.com |
| AC7 | Platform CI update propagates | Change reusable workflow → all projects use new version on next push |
| AC8 | All gates green | `make gate MODE=fast` passes |

---

## File Manifest (Revised — 28 entries)

| # | File | Action | Task | Description |
|---|------|--------|------|-------------|
| 1 | `.github/workflows/deploy-project.yml` | **NEW** | T1 | Reusable workflow: org-variable resolve-node → build → deploy. Uses `vars.NODE_HOST_MAP`, zero cross-repo checkout. |
| 2 | `.github/actions/resolve-node/` | **DELETE** | T17 | Dead composite action — cloned non-existent `tronyx-lab/platform`, required PAT. Replaced by org variable. |
| 3 | `platform-env.yaml` | **UPDATE** | T2 | Add `provides:` with host/port/dsn_template per service. Postgres host = pgbouncer, port = 6432. |
| 4 | `core/internal/scaffold/gen-env-platform.sh` | **NEW** | T3 | Read platform-env.yaml provides → generate .env.platform. Validates provides ⊆ profiles. |
| 5 | `core/internal/scaffold/add-project.sh` | **UPDATE** | T5 | Auto-domain logic; call gen-env-platform.sh; skip platform-deploy.yml copy. create_github_repo() unchanged (F12 debt). |
| 6 | `core/internal/scaffold/add-vhost.sh` | **UPDATE** | T6 | Third-level domain → wildcard cert path. |
| 7 | `core/entrypoints/scaffold.sh` | **UPDATE** | T4 | New `project-sync-env` subcommand; positional→named arg bridge for new-project. |
| 8 | `core/lib/node-resolver.sh` | **UPDATE** | T7 | CI context: resolve from `NODE_HOST_MAP` env var (JSON). |
| 9 | `Makefile` | **UPDATE** | T11 | New `project-sync-env` target; updated `new-project` target reads ORG/NODE from .env. |
| 10 | `core/entrypoint-manifest.yaml` | **UPDATE** | T12 | Register `project-sync-env`. |
| 11 | `core/AGENTS.md` | **UPDATE** | T13 | Add `project-sync-env` to operations table. |
| 12 | `AGENTS.md` (root) | **UPDATE** | T13 | Add `project-sync-env` to glossary (F9 fix). |
| 13 | `core/schemas/ai-platform.schema.json` | **UPDATE** | T16 | Add `platform_domain` to root + needs properties (F4 fix). |
| 14 | `templates/template-frontend/.github/workflows/deploy.yml` | **SIMPLIFY** | T8 | ~15 non-comment lines: `uses: TronyxLab/AI-platform/...`, `secrets: inherit`. |
| 15 | `templates/template-frontend/.github/workflows/platform-deploy.yml` | **DELETE** | T8 | No longer needed — logic in reusable workflow. |
| 16 | `templates/template-frontend/docker-compose.yml` | **UPDATE** | T8 | Add `env_file: .env.platform`. |
| 17 | `templates/template-frontend/ai-platform.yaml` | **UPDATE** | T8 | Add `platform_domain` field. |
| 18 | `templates/template-frontend/.env.platform` | **NEW** | T8 | Placeholder: `# GENERATED by ai-platform — run 'make project-sync-env' to regenerate`. |
| 19 | `templates/template-frontend/README.md` | **UPDATE** | T8 | Document new .env.platform, simplified CI, auto-domain. |
| 20 | `templates/template-backend/*` (same set as 14–19) | **SAME** | T9 | Backend-specific adaptations. |
| 21 | `templates/template-fullstack/*` (same set as 14–19) | **SAME** | T10 | Fullstack-specific adaptations. |
| 22 | `tests/test_scaffold_env_platform.py` | **NEW** | T14 | 9 tests (see $TEST_SPEC): header, min_vars, provides_list, dsn_format, dsn_host_routable, no_proxy, idempotent, missing_yaml, provides_in_profiles. |
| 23 | `tests/test_project_ci_contract.py` | **NEW** | T15 | 7 tests (see $TEST_SPEC): deploy_yml_lines, no_resolve_node, workflow_schema, no_token, org_variable, platform_deploy_deleted, env_platform_exists. |

**Note:** Original manifest entries 13, 18–24, 25–31 ranges collapsed — `templates/*/.github/actions/resolve-node/` never existed (F6), and template groupings are now explicit by file count.

---

## Change Impact

### Configuration Cascade (CONFIG_CONSISTENCY_CHECK)

| Variable | Platform .env | platform-env.yaml | gen-env-platform.sh | .env.platform (generated) | CI workflow | Org Settings |
|----------|--------------|-------------------|---------------------|--------------------------|-------------|-------------|
| `PLATFORM_DOMAIN` | Source | — | Reads | Emits | — | — |
| `PLATFORM_ORG` | Source | — | — | — | — | — |
| `PLATFORM_DEFAULT_NODE` | Source | — | — | — | — | — |
| Postgres host:port | — | `provides.postgres` (pgbouncer:6432) | Reads | `PLATFORM_POSTGRES_HOST/PORT/DSN` | — | — |
| `NODE_HOST_MAP` | — | — | — | — | Reads (`vars.NODE_HOST_MAP`) | Source (org variable) |
| `CI_DEPLOY_KEY` | — | — | — | — | `secrets: inherit` | Source (org secret) |

### Dual Mechanism Detection

**Detected — converged in this plan:**
- Node resolution: composite action (`.github/actions/resolve-node/`) + template inline blocks → **converged to** single reusable workflow step via `vars.NODE_HOST_MAP`. Composite action deleted (T17).

### Knowledge Dedup

| Duplicated Knowledge | Current State | Target State |
|---------------------|---------------|--------------|
| SSH host resolution | 3× inline blocks in templates + 1× composite action + 1× node-resolver.sh | 1× in `deploy-project.yml` via `vars.NODE_HOST_MAP` |
| Deploy SSH logic | 3× `platform-deploy.yml` in templates + 1× in platform repo | 1× in reusable workflow |
| Port numbers | `platform-env.yaml` + `infra.py` fixture + CI env blocks | `platform-env.yaml` → generated into `.env.platform` |
| `no_proxy_internal` | `platform-env.yaml` + hermes-agent docker-compose fallback | `platform-env.yaml` → generated into `.env.platform` |
| Postgres port semantics | 6432 used inconsistently (pgbouncer vs postgres confusion) | Single definition: `provides.postgres.port = 6432`, host = `pgbouncer` |

---

## Edge Cases & Error Handling (Revised)

| Scenario | Expected Behavior |
|----------|-------------------|
| PLATFORM_DOMAIN not set in `.env` | Auto-domain skipped; warning logged; `--domain` still works |
| `--domain` provided AND PLATFORM_DOMAIN set | Explicit domain used; PLATFORM_DOMAIN stored as `platform_domain` |
| `--domain` is third-level of PLATFORM_DOMAIN | vhost uses wildcard cert — no new cert needed |
| `--domain` is NOT third-level | vhost uses domain-specific cert |
| `platform-env.yaml` missing `provides:` | gen-env-platform.sh exits with error and clear message |
| `provides:` key not in `profiles` | gen-env-platform.sh fails with message: "<key> not in platform profiles" (F8 gate) |
| `NODE_HOST_MAP` org variable not set | CI fails at resolve-node with clear error: "NODE_HOST_MAP org variable not configured" |
| `NODE_HOST_MAP` missing target_node key | CI fails: "Node '<target_node>' not found in NODE_HOST_MAP" |
| `target_node` not in ai-platform.yaml | Defaults to `tronyx-vps` (backward compatible) |
| Template `ai-platform.yaml` has unexpanded placeholders | Test `test_templates.py` catches via schema validation |
| Reusable workflow not yet on `main` | CI fails with descriptive error until merged |
| Project has `.env` AND `.env.platform` | `env_file: .env.platform` is single entry in docker-compose — no list. `environment:` in docker-compose always overrides `env_file`. Platform vars inform; project vars decide. (F14 fix) |
| Project already has `.env.platform` (re-scaffold) | gen-env-platform.sh overwrites (idempotent); git diff shows changes |

---

## TRAP[DEBT] (carried forward + new)

### TRAP[DEBT] · 2026-07-17 · MED · add-project.sh CLI mismatch with scaffold.sh/Makefile
(Carried from original DevPlan — addressed by T4 arg bridge in this revision.)

### TRAP[DEBT] · 2026-07-17 · LO · Template placeholder inconsistency
(Carried from original DevPlan — not addressed in this revision. `$X` vs `__X__` conventions.)

### TRAP[DEBT] · 2026-07-17 · MED · add-project.sh contract violation — create_github_repo()
- **Observed:** MODULE_CONTRACT line 14: "Never auto-creates GitHub repos". `create_github_repo()` at line ~432 auto-creates and pushes.
- **Suspected:** Contract written before `create_github_repo()` was added; contract was never updated.
- **Impact:** Contract drift — agents reading the contract get incorrect behavior expectations.
- **When:** discovered during QA audit (F12) — pre-existing, out of scope for this DevPlan. Recommended fix: update MODULE_CONTRACT to reflect actual behavior, OR make repo creation opt-in via `--create-repo` flag.

---

## Next Steps

### Wave 1
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 1: T1, T2, T6, T7, T16, T17"
```

### Wave 2
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 2: T3, T4"
```

### Wave 3
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 3: T5"
```

### Wave 4
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 4: T8, T9, T10"
```

### Wave 5
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 5: T11, T12, T13, T14, T15"
```

### Wave 6
```
coder "Read .ai/plans/001-project-connection-model/04-DevPlan-rev.md, implement Wave 6: T18 — run make gate MODE=fast and report results"
```

---

**Plan status:** READY FOR IMPLEMENTATION. All 15 audit findings F1–F15 addressed. Key architectural change: org-level Actions variable `NODE_HOST_MAP` replaces cross-repo checkout — truly zero-secret, eliminates mirror conflict. Corrected postgres DSN contract (pgbouncer:6432). Schema update task added (F4). Line budget revised for doc-headers gate (F5). Dead code deletion included (F6, F10, F17).

$END_DEVPLAN
