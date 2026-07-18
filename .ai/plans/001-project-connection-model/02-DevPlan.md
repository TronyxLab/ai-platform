$START_DEVPLAN
# $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | DevPlan: systemic project connection model — reusable CI workflow, `.env.platform` contract, auto-domain generation, zero-secret CI (GITHUB_TOKEN) |
| **DESCRIPTION** | Implements Brief 01-Brief.md decisions D1–D4. 27 files touched across 3 layers: platform CI (reusable workflow), scaffold scripts (auto-domain + env generation), project templates (simplified to 15-line CI caller). 5 waves of implementation. |
| **RATIONALE** | Current drift: NODE_CONFIGS_TOKEN not created → CI broken; deploy.yml copied to every project → upgrade requires manual touch of 10+ repos; no environment contract for AI agents; domain management fragmented. Systemic fix reduces maintenance surface by ~70% (per-project CI from ~200 LOC to ~15 LOC). |
| **ACCEPTANCE_CRITERIA** | AC1: `make new-project NAME=test TEMPLATE=frontend` creates fully deployable project in one command. AC2: Project contains ≤5 platform files. AC3: CI passes without NODE_CONFIGS_TOKEN (GITHUB_TOKEN only). AC4: `grep -c PLATFORM_ .env.platform` ≥ 8. AC5: Auto-domain `<name>.tronyx.ru` when no --domain. AC6: Manual domain works for vhost + cert. AC7: Platform CI upgrade propagates to all projects automatically. |
| **IMPLEMENTS** | Brief 01-Brief.md (D1–D4); AGENTS.md § deploy-model invariants 1-3 |
| **IMPACTS** | `.github/workflows/deploy-project.yml` (NEW), `platform-env.yaml`, `core/internal/scaffold/gen-env-platform.sh` (NEW), `core/internal/scaffold/add-project.sh`, `core/internal/scaffold/add-vhost.sh`, `core/entrypoints/scaffold.sh`, `core/lib/node-resolver.sh`, `Makefile`, `core/entrypoint-manifest.yaml`, `core/AGENTS.md`, templates ×3 (deploy.yml, docker-compose.yml, ai-platform.yaml, .env.platform, README), tests ×2 (NEW) |
| **REQUIRES** | `PLATFORM_DOMAIN` in `.env`; write access to `TronyxLab/AI-platform` for reusable workflow publication; `CI_DEPLOY_KEY` as org-level secret in TronyxLab |

$END_ARTIFACT_CONTRACT

---

## Requirements Analysis

### Key Success Criteria

1. **Zero-secret CI**: NODE_CONFIGS_TOKEN eliminated. GITHUB_TOKEN auto-provided by GitHub. Project CI requires no manually configured secrets.
2. **Single-point CI update**: Changing the reusable workflow in `TronyxLab/AI-platform` updates all 10+ projects without touching any project repo.
3. **AI-readable environment contract**: `.env.platform` at project root — one `grep PLATFORM_` gives the AI agent full platform service information.
4. **One-command project creation**: `make new-project NAME=x TEMPLATE=y` produces a complete, deployable project with working CI, domain, and environment config.
5. **Domain flexibility**: Auto-generated third-level domain as safe default; explicit personal domain as opt-in; wildcard cert covers all third-level.

### Constraints

- **No changes to platform deploy model**: rsync/SSH core delivery stays as-is (per Brief Scope exclusions).
- **No DNS API automation**: DNS records must be created manually (out of scope).
- **No migration of existing projects**: `make project-migrate` is a separate task.
- **Org-level secrets**: `CI_DEPLOY_KEY` must be an org-level secret in TronyxLab for reusable workflow inheritance.
- **Reusable workflow location**: Must live in `TronyxLab/AI-platform/.github/workflows/` for cross-repo `uses:` references.

---

## Architecture Overview

### Draft Code Graph — New Components

```
┌─ .github/workflows/deploy-project.yml (reusable, in platform repo) ──────┐
│  on: workflow_call                                                        │
│  inputs: project_name, environment                                        │
│  secrets: CI_DEPLOY_KEY (from org-level, inherited)                       │
│  jobs:                                                                    │
│    resolve-node:                                                          │
│      checkout@v7  # caller project (Dockerfile, ai-platform.yaml)         │
│      checkout@v7 repository: TronyxLab/AI-platform token: github.token    │
│      python3 -c "yaml.safe_load → target_node → node.host"                │
│      → outputs: ssh_host                                                  │
│    build-image:                                                           │
│      docker/build-push-action (ghcr.io/<caller-repo>)                     │
│    deploy:                                                                │
│      appleboy/ssh-action → ci-deploy@<ssh_host>                           │
│      forced-command: deploy-project.sh <project> <sha> <env>             │
│  Note: CI_DEPLOY_KEY passed via secrets: inherit from caller              │
└───────────────────────────────────────────────────────────────────────────┘

┌─ core/internal/scaffold/gen-env-platform.sh ─────────────────────────────┐
│  Input: platform-env.yaml (provides section)                              │
│         ai-platform.yaml (project name)                                   │
│  Output: .env.platform                                                    │
│  Algorithm:                                                               │
│    read platform-env.yaml provides → iterate services                     │
│    for each service:                                                      │
│      if has dsn_template: substitute project name, host, port             │
│      if has url_template: substitute host, port                           │
│      emit PLATFORM_<SERVICE>_HOST, _PORT, _DSN/_URL                       │
│    emit PLATFORM_PROVIDES (comma-separated list)                          │
│    emit PLATFORM_PROXY_NET, PLATFORM_SHARED_DB_NET                        │
│    emit PLATFORM_NO_PROXY                                                 │
│    stamp header: "# GENERATED by ai-platform — DO NOT EDIT"               │
└───────────────────────────────────────────────────────────────────────────┘

┌─ core/internal/scaffold/add-project.sh (updated) ─────────────────────────┐
│  New flow:                                                                │
│    1. parse_args (unchanged)                                              │
│    2. show_plan (update: show auto-domain if not explicit)                │
│    3. resolve_domain:                                                     │
│       if --domain set → use as-is                                         │
│       elif PLATFORM_DOMAIN set → auto: ${NAME}.${PLATFORM_DOMAIN}         │
│       else → DOMAIN="" (skip vhost)                                       │
│    4. copy_template (update: skip .github/workflows/platform-deploy.yml)  │
│    5. generate_ai_platform_yaml (update: new domain + platform_domain)    │
│    6. call gen-env-platform.sh → generates .env.platform                  │
│    7. replace_placeholders (unchanged)                                    │
│    8. git_init + create_github_repo (unchanged)                           │
│    9. run_add_vhost (update: pass auto-domain if generated)               │
└───────────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Data Flow: `make new-project`

```
$ make new-project NAME=myapp TEMPLATE=frontend
     │
     ▼
scaffold.sh new-project "myapp" "frontend"
     │
     ▼
add-project.sh (updated, with named-arg bridge)
     │
     ├─ [1] parse args: NAME=myapp, TEMPLATE=frontend, NODE=default, ORG=tronyxlab
     ├─ [2] resolve_domain: PLATFORM_DOMAIN=tronyx.ru → auto-domain = myapp.tronyx.ru
     ├─ [3] copy template-frontend → ~/projects/tronyxlab/myapp/
     │      (skips: .github/workflows/platform-deploy.yml — no longer needed)
     ├─ [4] generate ai-platform.yaml:
     │      name: myapp
     │      type: frontend
     │      target_node: tronyx-vps
     │      needs.domain: myapp.tronyx.ru   ← auto-generated
     │      needs.platform_domain: tronyx.ru
     ├─ [5] gen-env-platform.sh:
     │      reads platform-env.yaml → generates .env.platform
     │      PLATFORM_PROVIDES=postgres,redis,litellm,langfuse,minio,clickhouse,nginx-proxy
     │      PLATFORM_POSTGRES_DSN=postgresql://myapp_user:***@postgres:6432/myapp_db
     │      ... (≥8 PLATFORM_* vars)
     ├─ [6] replace placeholders: __PROJECT_NAME__ → myapp, __DOMAIN__ → myapp.tronyx.ru
     ├─ [7] git init + commit
     ├─ [8] gh repo create tronyxlab/myapp --private
     ├─ [9] add-vhost: generate nginx vhost for myapp.tronyx.ru
     │      (detects *.tronyx.ru → uses wildcard cert → no new cert needed)
     └─ [10] git push → CI triggers deploy-project.yml (reusable)
```

### Step-by-Step Data Flow: CI (git push → deploy)

```
git push to main
     │
     ▼
┌─ Project: .github/workflows/deploy.yml (~15 lines) ──────────────────────┐
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
│    ├─ checkout@v7  # myapp repo → gets ai-platform.yaml                  │
│    ├─ checkout@v7 TronyxLab/AI-platform  # gets node-configs             │
│    │  token: ${{ github.token }}  ← ZERO MANUAL SECRETS                  │
│    ├─ python3: read ai-platform.yaml → target_node                       │
│    ├─ python3: read node-configs/<target_node>/node.yaml → node.host     │
│    └─ output: ssh_host                                                   │
│  job.build-image:                                                         │
│    ├─ docker/login-action ghcr.io (github.token)                         │
│    └─ docker/build-push-action → ghcr.io/tronyxlab/myapp:sha             │
│  job.deploy:                                                              │
│    └─ appleboy/ssh-action ci-deploy@<ssh_host>                           │
│       key: ${{ secrets.CI_DEPLOY_KEY }}  ← org-level, inherited          │
│       script: myapp ${{ github.sha }} production                         │
│       → forced-command: deploy-project.sh myapp <sha> production         │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### DD1: `uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml@main` with `secrets: inherit`

**@rationale** Q: Why not pin to a version tag? A: Projects track `@main` for auto-update semantics — matches Brief's "single point of update" requirement. If stability is needed later, projects can pin to `@v2`. The `@main` default embodies the architectural intent of auto-propagation.

**@rationale** Q: Why `secrets: inherit` instead of explicit per-secret passing? A: Minimizes project-side boilerplate. The reusable workflow declares only `CI_DEPLOY_KEY` as required — all other secrets (`GITHUB_TOKEN`, etc.) are auto-provided. With `inherit`, adding new optional secrets to the reusable workflow doesn't require updating every project.

### DD2: `gen-env-platform.sh` generates `PLATFORM_<SERVICE>_DSN` from templates in `platform-env.yaml`

**@rationale** Q: Why DSN templates in platform-env.yaml instead of hardcoding in the generator? A: platform-env.yaml is the single source of truth (invariant P5). Adding a new service (e.g., MinIO) requires only updating platform-env.yaml — the generator stays generic. Separation of data (what services exist) from code (how to generate env vars).

**@rationale** Q: Why `.env` format instead of YAML/JSON? A: `env_file` in docker-compose, `source` in shell, `grep` for AI agents — universally consumable without parsers. Brief D3 already decided this.

### DD3: Auto-domain = `${NAME}.${PLATFORM_DOMAIN}` — only applied when `--domain` not provided

**@rationale** Q: Why not always auto-generate? A: Personal domains are a legitimate use case (myapp.com, not myapp.tronyx.ru). The auto-domain is a safe default for projects without their own domain. If PLATFORM_DOMAIN is not set in `.env`, the auto-domain step is skipped with a warning — no domain generated.

### DD4: Reusable workflow resolves node via `GITHUB_TOKEN` checkout, not via dedicated `resolve-node` composite action

**@rationale** Q: Why not keep `.github/actions/resolve-node` and invoke it from the reusable workflow? A: The composite action uses `gh repo clone` which requires a PAT. In the reusable workflow, we use `actions/checkout@v7` with `repository: TronyxLab/AI-platform` + `token: ${{ github.token }}`. This eliminates the token entirely. The composite action becomes dead code (projects no longer have it). It stays in the platform repo for reference but is no longer copied to projects.

### DD5: `env_file: .env.platform` in docker-compose.yml, NOT `env_file: - .env.platform`

**@rationale** Q: Why single file instead of list? A: `.env.platform` is generated and should be the only platform env source. Listing it alone ensures no silent override by other env files. Projects can add their own `env_file` entries for project-specific env vars.

### DD6: `make project-sync-env` regenerates `.env.platform` for existing projects

**@rationale** Q: Why a separate target instead of automatic regeneration? A: `.env.platform` is checked into the project repo. Auto-regeneration on every `make` would cause dirty git state. Explicit regeneration via `make project-sync-env` gives the developer control over when to update the contract. This follows the Brief's design: "Не устаревает: при изменении платформы — регенерируется через `make project-sync-env`."

---

## $TASKS

### Task Decomposition

| ID | Task | Role | Output | Deps | Complexity | Acceptance Criteria |
|----|------|------|--------|------|------------|---------------------|
| T1 | Create reusable workflow `deploy-project.yml` | Coder | `.github/workflows/deploy-project.yml` | — | 8 | Workflow validates: `act pull_request --workflows .github/workflows/deploy-project.yml` OR manual review of on.workflow_call inputs/outputs/secrets schema |
| T2 | Add `provides:` section to `platform-env.yaml` | Coder | `platform-env.yaml` (updated) | — | 3 | YAML parses without errors; contains ≥6 services with host/port/dsn_template or url_template |
| T3 | Create `gen-env-platform.sh` generator | Coder | `core/internal/scaffold/gen-env-platform.sh` | T2 | 6 | Script runs without errors; output has `# GENERATED by ai-platform — DO NOT EDIT` header; `grep -c PLATFORM_` ≥ 8 |
| T4 | Update `scaffold.sh` entrypoint — new subcommand `sync-env`, fix positional→named arg bridge | Coder | `core/entrypoints/scaffold.sh` (updated) | T3 | 3 | `scaffold.sh project-sync-env` delegates to gen-env-platform.sh; `scaffold.sh new-project` passes recognized args |
| T5 | Update `add-project.sh` — auto-domain, gen-env integration, skip platform-deploy copy | Coder | `core/internal/scaffold/add-project.sh` (updated) | T3, T4 | 7 | `add-project.sh --name foo --template frontend --org X --node Y` (no --domain) auto-generates `foo.tronyx.ru`; `.env.platform` created in project dir; `platform-deploy.yml` NOT copied |
| T6 | Update `add-vhost.sh` — third-level domain detection refinement | Coder | `core/internal/scaffold/add-vhost.sh` (updated) | — | 3 | `*.tronyx.ru` domain uses wildcard cert path; explicit non-subdomain gets its own cert_path |
| T7 | Update `node-resolver.sh` — CI checkout context support | Coder | `core/lib/node-resolver.sh` (updated) | — | 3 | New function or optional param: when called from CI, resolves from `/tmp/platform/node-configs/` checkout path |
| T8 | Update template-frontend (deploy.yml, docker-compose.yml, ai-platform.yaml, .env.platform, README; delete platform-deploy.yml) | Coder | 5 updated + 1 deleted in `templates/template-frontend/` | T5 | 4 | deploy.yml ≤ 20 lines (`uses: TronyxLab/AI-platform/...`); docker-compose has `env_file: .env.platform`; ai-platform.yaml has `platform_domain` field; `platform-deploy.yml` deleted |
| T9 | Update template-backend (same set) | Coder | 5 updated + 1 deleted in `templates/template-backend/` | T5 | 4 | Same criteria as T8, adapted for backend template specifics |
| T10 | Update template-fullstack (same set) | Coder | 5 updated + 1 deleted in `templates/template-fullstack/` | T5 | 4 | Same criteria as T8, adapted for fullstack template specifics (both frontend + backend Dockerfiles) |
| T11 | Update `Makefile` — new `project-sync-env` target, updated `new-project` target | Coder | `Makefile` (updated) | T4, T5 | 3 | `make new-project NAME=x TEMPLATE=y` works end-to-end; `make project-sync-env PROJECT=<dir>` regenerates .env.platform |
| T12 | Update `entrypoint-manifest.yaml` — register `project-sync-env` | Coder | `core/entrypoint-manifest.yaml` (updated) | T11 | 2 | Manifest entry for `project-sync-env` with mechanism, delegates_to, description |
| T13 | Update `core/AGENTS.md` — add `project-sync-env` to operations table | Coder | `core/AGENTS.md` (updated) | T11 | 2 | New row in canonical operations table for `make project-sync-env` |
| T14 | Create `tests/test_scaffold_env_platform.py` | Coder | `tests/test_scaffold_env_platform.py` (NEW) | T3 | 5 | All tests pass (`python -m pytest tests/test_scaffold_env_platform.py -v`); LDD trajectory printed; at least one IMP:9 log |
| T15 | Create `tests/test_project_ci_contract.py` | Coder | `tests/test_project_ci_contract.py` (NEW) | T1 | 5 | All tests pass; validates reusable workflow has correct inputs/outputs schema; validates template deploy.yml references correct workflow path |
| T16 | Validation gate — run `make gate MODE=fast` | QA | Gate output | T1–T15 | 3 | All gates pass: validate → lint → gates → static → predeploy |

### Critical Path

```
T2 → T3 → T4 → T5 → T8,T9,T10 → T11 → T12,T13 → T16
                      ↘ T14
T1 → T15 ──────────────┘
T6, T7 (parallel, no deps on T1-T5)
```

Critical path length: **8 tasks** (T2→T3→T4→T5→T8/T9/T10→T11→T12/T13→T16).

---

## $PARALLEL_GROUPS

### Wave 1 — Foundation (independent, no shared files)
- **T1**: Create `deploy-project.yml` reusable workflow
- **T2**: Update `platform-env.yaml` with `provides:` section
- **T6**: Update `add-vhost.sh` third-level domain
- **T7**: Update `node-resolver.sh` CI context

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 1: T1, T2, T6, T7"
```

### Wave 2 — Generator + Entrypoint (depends on Wave 1)
- **T3**: Create `gen-env-platform.sh` (depends on T2 for schema)
- **T4**: Update `scaffold.sh` (depends on T3 for sync-env delegation)

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 2: T3, T4"
```

### Wave 3 — Core Script Update (depends on Wave 2)
- **T5**: Update `add-project.sh` (depends on T3, T4)

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 3: T5"
```

### Wave 4 — Templates (depends on Wave 3; parallel across templates)
- **T8**: Update template-frontend
- **T9**: Update template-backend
- **T10**: Update template-fullstack

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 4: T8, T9, T10"
```

### Wave 5 — Infrastructure + Tests (depends on Waves 2–4; parallel)
- **T11**: Update Makefile
- **T12**: Update entrypoint-manifest.yaml
- **T13**: Update core/AGENTS.md
- **T14**: Create test_scaffold_env_platform.py
- **T15**: Create test_project_ci_contract.py

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 5: T11, T12, T13, T14, T15"
```

### Wave 6 — Validation Gate
- **T16**: Run `make gate MODE=fast`

```bash
coder "Read .ai/plans/001-project-connection-model/02-DevPlan.md, implement Wave 6: T16 — run make gate MODE=fast and report results"
```

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_has_header` | Generated .env.platform starts with `# GENERATED by ai-platform — DO NOT EDIT` | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_min_vars` | Output has ≥8 `PLATFORM_*` lines | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_provides_list` | `PLATFORM_PROVIDES` is comma-separated list matching platform-env.yaml `provides:` keys | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_dsn_format` | DSN variables follow `scheme://user:***@host:port/db` pattern | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_no_proxy_internal` | `PLATFORM_NO_PROXY` contains `postgres,redis` | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_idempotent` | Second run produces identical output (given same inputs) | `gen-env-platform.sh` |
| `tests/test_scaffold_env_platform.py` | `test_gen_env_platform_missing_yaml` | Graceful error when platform-env.yaml not found | `gen-env-platform.sh` |
| `tests/test_project_ci_contract.py` | `test_deploy_yml_calls_reusable_workflow` | Template deploy.yml ≤ 20 lines and contains `uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml` | Template deploy.yml |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_schema` | `deploy-project.yml` has valid `on.workflow_call` with required inputs | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_no_node_configs_token` | Workflow does NOT reference `NODE_CONFIGS_TOKEN` or `node_configs_token` | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_reusable_workflow_uses_github_token` | Workflow checkout of platform repo uses `token: ${{ github.token }}` | `deploy-project.yml` |
| `tests/test_project_ci_contract.py` | `test_platform_deploy_yml_deleted_from_templates` | `templates/*/.github/workflows/platform-deploy.yml` does not exist | Template filesystem |
| `tests/test_project_ci_contract.py` | `test_template_has_env_platform` | Each template has `.env.platform` placeholder | Template filesystem |
| `tests/test_templates.py` (existing) | `test_template_validates_against_schema` — updated for new ai-platform.yaml fields | Existing test passes after template updates (new `platform_domain` field in schema) | Template ai-platform.yaml |

---

## Acceptance Criteria (Summary)

| AC | Criterion | Verification Method |
|----|-----------|---------------------|
| AC1 | `make new-project` creates deployable project | Manual: scaffold → git push → CI green |
| AC2 | ≤5 platform files per project | `find project/ -name "*.yml" -o -name ".env*" \| wc -l` → ≤7 (ai-platform.yaml, Dockerfile, docker-compose.yml, .env.platform, nginx/default.conf, deploy.yml, README.md) |
| AC3 | CI without NODE_CONFIGS_TOKEN | CI log: no reference to `NODE_CONFIGS_TOKEN`; uses `github.token` |
| AC4 | .env.platform has ≥8 PLATFORM_ vars | `grep -c "^PLATFORM_" .env.platform` ≥ 8 |
| AC5 | Auto-domain `${NAME}.tronyx.ru` | `make new-project NAME=foo` → ai-platform.yaml `needs.domain: foo.tronyx.ru` |
| AC6 | Manual domain works | `make new-project NAME=foo --domain myapp.com` → vhost for myapp.com |
| AC7 | Platform CI update propagates | Change reusable workflow → all projects use new version on next push |

---

## File Manifest

| # | File | Action | Description |
|---|------|--------|-------------|
| 1 | `.github/workflows/deploy-project.yml` | **NEW** | Reusable workflow: resolve-node → build-image → deploy. Uses GITHUB_TOKEN, no NODE_CONFIGS_TOKEN. |
| 2 | `platform-env.yaml` | **UPDATE** | Add `provides:` section with service descriptors (host, port, dsn_template/url_template) for postgres, redis, litellm, langfuse, minio, clickhouse, nginx-proxy |
| 3 | `core/internal/scaffold/gen-env-platform.sh` | **NEW** | Read platform-env.yaml → generate .env.platform with PLATFORM_* vars. Idempotent. |
| 4 | `core/internal/scaffold/add-project.sh` | **UPDATE** | Auto-domain logic; call gen-env-platform.sh; skip platform-deploy.yml copy; updated checklist |
| 5 | `core/internal/scaffold/add-vhost.sh` | **UPDATE** | Refined third-level domain detection; explicit wildcard cert path for `*.PLATFORM_DOMAIN` |
| 6 | `core/entrypoints/scaffold.sh` | **UPDATE** | New `sync-env` subcommand; bridge positional→named args for new-project |
| 7 | `core/lib/node-resolver.sh` | **UPDATE** | Optional CI checkout path fallback (`/tmp/platform/node-configs/`) |
| 8 | `Makefile` | **UPDATE** | New `project-sync-env` target; updated `new-project` target |
| 9 | `core/entrypoint-manifest.yaml` | **UPDATE** | Register `project-sync-env` in manifest |
| 10 | `core/AGENTS.md` | **UPDATE** | Add `project-sync-env` to canonical operations table |
| 11 | `templates/template-frontend/.github/workflows/deploy.yml` | **SIMPLIFY** | ~15-line caller: `uses: TronyxLab/AI-platform/.github/workflows/deploy-project.yml@main`, `secrets: inherit` |
| 12 | `templates/template-frontend/.github/workflows/platform-deploy.yml` | **DELETE** | No longer needed in project — logic moved to reusable workflow |
| 13 | `templates/template-frontend/.github/actions/resolve-node/` | **DELETE** | No longer needed — resolve-node is in reusable workflow |
| 14 | `templates/template-frontend/docker-compose.yml` | **UPDATE** | Add `env_file: .env.platform` |
| 15 | `templates/template-frontend/ai-platform.yaml` | **UPDATE** | Add `platform_domain` field; use proper placeholder format |
| 16 | `templates/template-frontend/.env.platform` | **NEW** | Placeholder: `# GENERATED by ai-platform — run 'make project-sync-env' to regenerate` |
| 17 | `templates/template-frontend/README.md` | **UPDATE** | Document new .env.platform, simplified CI, auto-domain |
| 18–24 | `templates/template-backend/*` | **SAME AS 11–17** | Backend-specific adaptations |
| 25–31 | `templates/template-fullstack/*` | **SAME AS 11–17** | Fullstack-specific adaptations (2 Dockerfiles, 2 services) |
| 32 | `tests/test_scaffold_env_platform.py` | **NEW** | 7 tests for gen-env-platform.sh (see $TEST_SPEC) |
| 33 | `tests/test_project_ci_contract.py` | **NEW** | 6 tests for CI contract (see $TEST_SPEC) |
| 34 | `core/schemas/ai-platform.schema.json` | **UPDATE** | Update schema for new `platform_domain` field |

---

## Change Impact

### Configuration Cascade (CONFIG_CONSISTENCY_CHECK)

| Variable | Platform .env | platform-env.yaml | gen-env-platform.sh | .env.platform (generated) | CI workflow |
|----------|--------------|-------------------|---------------------|--------------------------|-------------|
| `PLATFORM_DOMAIN` | Source | — | Reads | Emits | — |
| Postgres port: 6432 | — | `port_mappings.POSTGRES_PORT` | Reads | `PLATFORM_POSTGRES_PORT` | — |
| Redis port: 6379 | — | `port_mappings.REDIS_PORT` | Reads | `PLATFORM_REDIS_PORT` | — |
| LiteLLM port: 4000 | — | `port_mappings.LITELLM_PORT` | Reads | `PLATFORM_LITELLM_PORT` | — |
| `proxy-net` | — | `networks[proxy-net]` | Reads | `PLATFORM_PROXY_NET` | — |
| `CI_DEPLOY_KEY` | — | — | — | — | `secrets: inherit` from org |

**Drift vector eliminated:** Port mappings previously duplicated in `platform-env.yaml`, `tests/_conftest/infra.py`, and CI configs. With `.env.platform` generated from `platform-env.yaml`, all consumers read from a single file. The test fixture and docker-compose converge on `.env.platform` as the source of truth.

### Dual Mechanism Detection

**None detected** — the new `.env.platform` contract does not duplicate an existing mechanism. Current projects have no environment contract file. The `platform-deploy.yml` per-template mechanism is being replaced (not duplicated) by the reusable workflow.

### Knowledge Dedup

| Duplicated Knowledge | Current State | Target State |
|---------------------|---------------|--------------|
| SSH host resolution | 3× duplicate `resolve-node` blocks in templates + 1× composite action | 1× in reusable workflow `deploy-project.yml` |
| Deploy SSH logic | 3× duplicate `platform-deploy.yml` in templates + 1× in platform repo | 1× in reusable workflow |
| Port numbers | `platform-env.yaml` + `infra.py` fixture + CI env blocks | `platform-env.yaml` only (generated into `.env.platform`) |
| `no_proxy_internal` | `platform-env.yaml` + hermes-agent docker-compose fallback | `platform-env.yaml` only (generated into `.env.platform`) |

---

## Edge Cases & Error Handling

| Scenario | Expected Behavior |
|----------|-------------------|
| PLATFORM_DOMAIN not set in `.env` | Auto-domain skipped; warning logged; `--domain` still works for explicit domains |
| `--domain` provided AND PLATFORM_DOMAIN set | Explicit domain used; PLATFORM_DOMAIN stored as `platform_domain` in ai-platform.yaml |
| `--domain` is third-level of PLATFORM_DOMAIN (e.g., `foo.tronyx.ru` with PLATFORM_DOMAIN=tronyx.ru) | vhost uses wildcard cert (`/etc/letsencrypt/live/tronyx.ru/`) — no new cert needed |
| `--domain` is NOT third-level (e.g., `myapp.com`) | vhost uses domain-specific cert (`/etc/letsencrypt/live/myapp.com/`) |
| `platform-env.yaml` missing `provides:` section | gen-env-platform.sh exits with error and clear message |
| Template `ai-platform.yaml` has `$PROJECT_NAME` (not replaced by add-project.sh) | Test `test_templates.py` catches this via schema validation |
| Reusable workflow not yet published to `main` branch | Template deploy.yml references `@main` — CI fails with descriptive error until workflow is merged |
| Project has both `.env` and `.env.platform` | `.env.platform` loaded last in docker-compose → platform vars take precedence over project vars |
| Project already has `.env.platform` (re-scaffold) | gen-env-platform.sh overwrites (idempotent); git diff shows changes if platform-env.yaml changed |
| `github.token` lacks `contents: read` on TronyxLab/AI-platform | CI fails at resolve-node checkout step — org settings must grant Actions read access to platform repo |

---

## ⚠️ TRAP[DEBT] discoveries

### TRAP[DEBT] · 2026-07-17 · MED · add-project.sh CLI mismatch with scaffold.sh/Makefile
- **Observed:** `scaffold.sh` passes positional args `"$NAME" "$TEMPLATE"` to `add-project.sh`, but `add-project.sh` expects named args (`--name`, `--template`, `--org`, `--node`). The Makefile target `new-project` currently only passes NAME + TEMPLATE — org and node are never passed.
- **Suspected:** `add-project.sh` was designed for direct CLI use with named args; the `scaffold.sh` bridge was never properly wired. The Makefile target may have never been used in practice.
- **Impact:** `make new-project NAME=foo TEMPLATE=frontend` would fail with "Missing required arguments: --org --node". Projects can only be created via direct invocation of `add-project.sh` with full args.
- **When:** discovered during DevPlan 001 analysis — T4 addresses this by adding an arg bridge in `scaffold.sh`.

### TRAP[DEBT] · 2026-07-17 · LO · Template placeholder inconsistency
- **Observed:** `ai-platform.yaml` uses `$PROJECT_NAME`, `$NODE_NAME`, `$DOMAIN` (shell-style), while `docker-compose.yml` uses `__PROJECT_NAME__`, `__ORG_NAME__`, `__DOMAIN__` (python-style). Two different placeholder conventions in the same template.
- **Suspected:** Historical evolution — `add-project.sh`'s `replace_placeholders()` only handles `__X__` style; `generate_ai_platform_yaml()` writes to a separate file with `$X` style that never goes through `replace_placeholders`.
- **Impact:** Confusion during template authoring; two separate replacement mechanisms must stay synchronized.
- **When:** discovered during DevPlan 001 analysis — NOT addressed in this DevPlan. Recommended fix: converge on one convention in a separate cleanup task.

---

## Next Steps

### Wave 1
Use `coder` role and read `full-path-to/.ai/plans/001-project-connection-model/02-DevPlan.md`, implement Wave 1: T1, T2, T6, T7

### Wave 2
Use `coder` role and read same DevPlan, implement Wave 2: T3, T4

### Wave 3
Use `coder` role and read same DevPlan, implement Wave 3: T5

### Wave 4
Use `coder` role and read same DevPlan, implement Wave 4: T8, T9, T10

### Wave 5
Use `coder` role and read same DevPlan, implement Wave 5: T11, T12, T13, T14, T15

### Wave 6
Use `coder` role and read same DevPlan, implement Wave 6: T16 — run `make gate MODE=fast` and report results

---

**Plan status:** READY FOR IMPLEMENTATION. All 5 waves are independent enough to be executed sequentially by the same Coder agent. Wave 4 (3 template tasks) can be parallelized across 3 Coder subagents if speed is a priority.

$END_DEVPLAN
