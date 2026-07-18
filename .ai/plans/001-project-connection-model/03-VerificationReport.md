$START_VERIFICATION_REPORT
# $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Pre-implementation QA audit of 02-DevPlan.md (project connection model) — verify plan claims against codebase reality, architectural invariants, and existing CI gates before any wave starts |
| **DESCRIPTION** | LARGE-scope audit (34 manifest files, CI/config/schema changes). Phases executed: static artifact audit, cross-file drift detection (plan ↔ codebase), invariant verification, test-spec quality review. Runtime validation (Phase 5) N/A — nothing implemented yet. 3 CRITICAL, 3 HIGH, 4 MEDIUM, 3 WARNING/LOW, 2 INFO findings. |
| **RATIONALE** | The DevPlan's central design premise (zero-secret cross-repo checkout via GITHUB_TOKEN) is contradicted by verified facts (mirror repo is PRIVATE); the generated `.env.platform` DSN contract points to a non-routable host:port; a schema-gated field has no owning task. Implementing waves as written would fail at Wave 1 CI design, Wave 4 validation gate, and produce broken scaffolded projects. |
| **ACCEPTANCE_CRITERIA** | Every finding has evidence (file:line or verified command output); verdict per QA scale; each CRITICAL/HIGH has a concrete fix direction; delegation target named |
| **IMPLEMENTS** | QA §BEHAVIOR Phases 1–4, ⟦CHECKPOINT 1⟧ (STOP on CRITICAL before implementation) |
| **IMPACTS** | .ai/plans/001-project-connection-model/02-DevPlan.md (revision required); 01-Brief.md D1/C2 (premise correction) |
| **REQUIRES** | 01-Brief.md, 02-DevPlan.md, codebase state at commit bf9804c |

$END_ARTIFACT_CONTRACT

---

## Section 1 — Static Audit (Phase 1)

Artifact under audit: `.ai/plans/001-project-connection-model/02-DevPlan.md` (авторитетный DevPlan, highest NN).

| Check | Result |
|-------|--------|
| $START_DEVPLAN / $END_DEVPLAN boundary markers | PASS |
| $ARTIFACT_CONTRACT — 7 mandatory fields | PASS |
| $TASKS with ID/Role/Deps/AC | PASS |
| $PARALLEL_GROUPS waves | PASS |
| $TEST_SPEC table | PASS |
| Edge cases section | PASS |
| TRAP[DEBT] format compliance (2 entries) | PASS — both verified accurate (see Section 2, F7 and template evidence) |
| Internal consistency of contract fields | **FAIL** — see F11 |

**[LOW] F11 · Internal contract inconsistencies:**
- DESCRIPTION claims «27 files touched … 5 waves»; File Manifest enumerates **34** entries; plan defines **6** waves (02-DevPlan.md:7 vs :328–351, :237–287).
- AC2 criterion «≤5 platform files» vs verification method «→ ≤7» listing 7 files (02-DevPlan.md:317). Also `find … -name "*.yml" -o -name ".env*"` does not match `ai-platform.yaml` (`*.yaml` missing from pattern).
- Wave 6 Next Steps says «Use `coder` role» while T16 Role = QA (02-DevPlan.md:220 vs :436).

---

## Section 2 — Drift Analysis (Phase 2): Plan ↔ Codebase Reality

### CRITICAL

**[CRITICAL] F1 · DRIFT-CONTRACT · Zero-secret checkout of node-configs will fail: TronyxLab/ai-platform is PRIVATE**
- Evidence: `gh repo view TronyxLab/ai-platform --json visibility` → `{"isPrivate":true,"visibility":"PRIVATE"}` (verified 2026-07-17).
- DevPlan T1 / DD4 / data-flow (02-DevPlan.md:50, 148–151, 185–187) and Brief D1 (01-Brief.md:33) rely on `actions/checkout` with `repository: TronyxLab/AI-platform` + `token: ${{ github.token }}` from a project repo. `GITHUB_TOKEN` is scoped to the **triggering repository only**. The org Actions-access setting («Accessible from repositories in the organization») enables `uses:` of the reusable workflow and composite actions — it does **not** grant `contents: read` for `actions/checkout` of another private repo.
- Brief D1 premise «GITHUB_TOKEN уже имеет contents: read на все репо в TronyxLab» — factually incorrect for private repos. DevPlan's own edge case (02-DevPlan.md:398) frames this as a fixable org setting; no such setting exists for contents checkout.
- Impact: `resolve-node` job fails on first run for every project → AC1, AC3, AC7 unachievable as designed.
- Fix directions (Architect decision required):
  a. **Org-level Actions variable** (`vars.*`) holding node→host map (node.host is explicitly non-secret per template TRAP: templates/template-frontend/.github/workflows/deploy.yml:34–41). Truly zero-secret, no cross-repo checkout at all.
  b. Fine-grained PAT (contents:read on ai-platform only) as org secret — 1 secret, honest trade-off.
  c. GitHub App installation token.
  d. Make mirror public — contradicts private infra posture.

**[CRITICAL] F2 · DRIFT-ARCH · node-configs delivery model conflicts with mirror sync**
- Brief C2 (01-Brief.md:27): node-configs коммитятся **напрямую в зеркало** TronyxLab/ai-platform; в source-репозитории `node-configs/` пустая (verified: glob `node-configs/**` → 0 files).
- mirror.yml pushes `HEAD:main` **without force** (.github/workflows/mirror.yml:135). Any direct commit to the mirror diverges history → mirror sync fails non-fast-forward; the documented recovery is `git push -f` (mirror.yml:156–160), which **erases node-configs commits**.
- The reusable workflow reads node-configs from `TronyxLab/AI-platform@main` — its data source is architecturally unstable. DevPlan does not address this at all.
- Fix directions: dedicated config repo, org-level variables (converges with F1a), or a non-mirrored branch for configs.

**[CRITICAL] F3 · DRIFT-CONFIG · Generated Postgres DSN is non-routable: `postgres:6432`**
- Brief (01-Brief.md:134), DevPlan data flow (02-DevPlan.md:118) and CONFIG_CONSISTENCY table (02-DevPlan.md:362) emit `PLATFORM_POSTGRES_DSN=postgresql://…@postgres:6432/…`.
- Reality: **6432 is the pgbouncer LISTEN_PORT** (core/modules/postgres/docker-compose.base.yml:90); container `postgres` listens on **5432** (core/modules/postgres/Dockerfile:38; DATABASE_URLS upstreams `postgres:5432`, docker-compose.base.yml:87).
- `postgres:6432` connects nowhere. Correct contract: `pgbouncer:6432` (canonical facade) or `postgres:5432`. Since `.env.platform` is the AI-agent contract, every scaffolded project would ship a broken DSN — the exact drift class this plan exists to eliminate.
- Fix: T2 `provides:` descriptors must carry explicit `host:` per service (postgres → `pgbouncer`); T14 must add a test asserting DSN host:port matches a reachable service:port pair.

### HIGH

**[HIGH] F4 · ORPHANED-TASK · File Manifest #34 (`core/schemas/ai-platform.schema.json`) has no owning task**
- Schema has `additionalProperties: false` at root **and** inside `needs` (core/schemas/ai-platform.schema.json:9, :108). Templates gain `platform_domain` in Wave 4 (T8–T10) → `test_template_validates_against_schema` (tests/test_templates.py:77) and `make validate` fail from Wave 4 until the schema is updated — and **no task T1–T16 covers file #34**.
- Fix: add explicit task (schema update) sequenced into Wave 1, or as prerequisite of Wave 4.

**[HIGH] F5 · GATE-CONFLICT · «deploy.yml ≤ 20 lines» is unsatisfiable under the doc-headers gate**
- check-doc-headers.sh requires GREP_SUMMARY + STRUCTURE + MODULE_CONTRACT (region pair) + `## @purpose` for **every** staged `.yml` with no templates exemption (core/entrypoints/check-doc-headers.sh:160–199). Mandatory headers alone ≈ 10+ lines; a compliant caller workflow cannot fit 20 total lines.
- Conflicts: T8/T9/T10 AC («deploy.yml ≤ 20 lines», 02-DevPlan.md:212) and T15 test `test_deploy_yml_calls_reusable_workflow` (02-DevPlan.md:302).
- Fix: count non-comment lines in the test, or raise the budget to ~40 with headers mandatory.

**[HIGH] F6 · PHANTOM-DELETE · File Manifest #13 deletes a path that does not exist; the real defect is unaddressed**
- `templates/*/.github/actions/resolve-node/` — **absent in all 3 templates** (verified glob). DD4's «projects no longer have it» is wrong — templates never had it.
- Actual defect: template deploy.yml references a **local** action `uses: ./.github/actions/resolve-node` (templates/template-frontend/.github/workflows/deploy.yml:90) that is never copied into projects → scaffolded projects currently fail CI with «action not found» regardless of any token. The plan's RATIONALE understates present breakage.
- Fix: replace manifest row #13 (and mirrored rows in 18–24, 25–31 ranges) with an explicit note; add a T15 test asserting project deploy.yml does **not** reference `./.github/actions/resolve-node`.

### MEDIUM

**[MEDIUM] F7 · SPEC-GAP · Makefile→CLI argument bridge underspecified (confirms plan's own TRAP[DEBT] #1)**
- Verified: Makefile passes only NAME+TEMPLATE positionally (Makefile:431); add-project.sh hard-requires `--org`/`--node` (core/internal/scaffold/add-project.sh:90–99). TRAP[DEBT] #1 is accurate.
- Gaps: (a) no task defines the **source of defaults** for ORG/NODE (data flow 02-DevPlan.md:105 asserts «NODE=default, ORG=tronyxlab» from nowhere — `.env`? `platform-env.yaml`? hardcode?); (b) AC6 verification `make new-project NAME=foo --domain myapp.com` is invalid make syntax — must be `DOMAIN=myapp.com`, and T11 must specify DOMAIN/ORG/NODE pass-through.

**[MEDIUM] F8 · DRIFT-VECTOR · `provides:` keys vs `profiles` naming unconstrained**
- platform-env.yaml invariant: «profiles are 1:1 with core/modules/ directory names» (platform-env.yaml:13). Brief example emits `PLATFORM_PROVIDES=…,nginx-proxy` (01-Brief.md:131) while the module/profile is `nginx`. T2 AC («≥6 services») imposes no naming rule → new drift vector inside the SoT file itself.
- Fix: constrain `provides:` keys ⊆ `profiles`; add gate assertion in T14.

**[MEDIUM] F9 · NAMING · `sync-env` vs `project-sync-env` + root AGENTS.md glossary missing from manifest**
- File #6 says subcommand `sync-env` (02-DevPlan.md:335); T4 AC says `scaffold.sh project-sync-env` (02-DevPlan.md:208). Pin one form.
- Gate test_gate_manifest_integrity enforces Makefile ↔ manifest ↔ **core/**AGENTS.md triad (covered by T11–T13), but the **root** AGENTS.md «Глоссарий глаголов» also registers every canonical verb — root AGENTS.md is not in the File Manifest. Doc drift (not gate-blocking).

**[MEDIUM] F10 · DEAD-REF · Composite action clones a non-existent repo; DD4 keeps it «for reference»**
- `.github/actions/resolve-node/action.yml:53` clones `tronyx-lab/platform` — repo does not resolve (verified: `gh repo view tronyx-lab/platform` → not found). add-project.sh checklist (line 385) meanwhile names `TronyxLab/ai-platform`. Two conflicting repo identities in current code.
- DD4 leaves the action in the platform repo as dead reference code. Fix: delete it or update the reference; keeping a dead cross-repo pointer is documented drift.

---

## Section 3 — Invariant Status (Phase 3, root AGENTS.md)

| Invariant | Status | Evidence |
|-----------|--------|----------|
| 1. Makefile — единый фасад | HELD (plan-compliant) | T11+T12+T13 cover the parity triad enforced by tests/gates/test_gate_manifest_integrity.py |
| 2. Модель деплоя: git push → CI; core NEVER via git on VPS | HELD | Reusable workflow only SSHes forced-command; no git surface added on VPS |
| 3. org = context (tronyx161 source) | AT_RISK | F2: node-configs-in-mirror model conflicts with mirror.yml non-force push |
| Dual delivery: secrets never via git | HELD | CI_DEPLOY_KEY stays org secret; no keys in repos |
| Глоссарий глаголов: все таргеты в манифесте | AT_RISK | F9: root glossary not in File Manifest; verb name unpinned |
| platform-env.yaml — единственное место определения env | AT_RISK | F8: provides/profiles naming unconstrained; F3: port semantics (pgbouncer vs postgres) undocumented in port_mappings |
| add-project.sh contract «Never auto-creates GitHub repos» | **VIOLATED (pre-existing)** | **[WARNING] F12**: create_github_repo() auto-creates and pushes (add-project.sh:432–469) contradicting its own MODULE_CONTRACT invariant (add-project.sh:14); DevPlan step 8 keeps it «unchanged». T5 should reconcile contract or behavior. |

---

## Section 4 — Test Quality ($TEST_SPEC review, Phase 4)

**[WARNING] F13 · Coverage gaps in planned tests:**
- No test that project deploy.yml does **not** reference `./.github/actions/resolve-node` (the actual current breakage, F6).
- No test that `provides:` DSN host:port pairs are routable/consistent with module compose definitions (would have caught F3).
- No drift gate `provides:` keys ⊆ `profiles` (F8).
- `test_deploy_yml_calls_reusable_workflow` asserts ≤ 20 lines — unsatisfiable per F5; must be redefined before T15.

**[INFO] F15 · Environment assumption:** `.env.example` sets `PLATFORM_DOMAIN=ai-platform.local`; AC5 examples assume `tronyx.ru` (production `.env`). T14 tests must derive expected auto-domain from the fixture env, never hardcode `tronyx.ru` (existing pattern: tests/test_add_vhost.py uses `test.local` — follow it).

Positive: LDD trajectory + IMP:9 requirements present in T14 AC; idempotency and negative-path tests (missing yaml) included; `test_reusable_workflow_no_node_configs_token` is a good regression gate.

---

## Section 5 — Runtime Validation (Phase 5)

N/A — pre-implementation audit; no target code exists. Existing suite not run (out of scope; last commit bf9804c reports green fast gate).

---

## Section 6 — Config Sync (Phase 6, planned chain)

**[INFO] F14 · Precedence claim inconsistency:** Edge case «`.env.platform` loaded last in docker-compose → platform vars take precedence» (02-DevPlan.md:396) contradicts DD5, which mandates a **single** `env_file: .env.platform` (no list, 02-DevPlan.md:189–191). Also note compose semantics: `environment:` always overrides `env_file` regardless of order. Reword the edge case.

Planned chain `platform-env.yaml → gen-env-platform.sh → .env.platform → docker-compose/env_file` is sound and eliminates the port-duplication drift vector claimed in CONFIG_CONSISTENCY — **provided F3 (host semantics) is fixed at the source**.

---

## Semantic Verdict

**DRIFTED (CRITICAL)** — план не готов к реализации в текущем виде.

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 3 | F1 (private repo breaks zero-secret checkout), F2 (mirror vs node-configs), F3 (DSN postgres:6432) |
| HIGH | 3 | F4 (orphaned schema task), F5 (20-line AC vs doc-headers gate), F6 (phantom delete #13) |
| MEDIUM | 4 | F7, F8, F9, F10 |
| WARNING | 2 | F12, F13 |
| LOW | 1 | F11 |
| INFO | 2 | F14, F15 |

⟦CHECKPOINT 1⟧ verdict: **STOP** — не запускать Wave 1 до пересмотра DevPlan. F1+F2 требуют архитектурного решения (модель доставки node-configs), F3 — исправления контракта данных в T2/T3, F4/F5/F6 — правок декомпозиции и AC.

**Delegation:** Architect (Plan) — ревизия 02-DevPlan.md по F1–F10 → новый артефакт `{NN}-DevPlan-rev.md`.

$END_VERIFICATION_REPORT
