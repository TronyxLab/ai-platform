$START_DEVPLAN

# DevPlan 084 — Dead Code Sweep

## $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Remove all dead/deprecated code remaining after waves 071 (.done migration), 072 (.env cleanup), and 080 (cert unification) |
| **DESCRIPTION** | Delete `nginx/install.sh` (1107 LOC), `ssl-provision.sh` (40 LOC), `LITELLM_METRICS_TOKEN` from `.env.example`; verify .done file cleanup complete; add CI gate `make check-dead-code` to prevent DEPRECATED marker accumulation |
| **RATIONALE** | Dead code misleads agents — they read it as source of truth (RC-4). `nginx/install.sh` contains divergent cert functions that differ from canonical `cert_orchestrator.py` (wave 080). Every DEPRECATED marker older than 30 days that hasn't been removed is architectural debt with an expiry date already passed |
| **ACCEPTANCE_CRITERIA** | AC1: `nginx/install.sh` deleted with 0 remaining callers; AC2: `ssl-provision.sh` deleted (or kept if node-lifecycle.sh still calls it); AC3: `LITELLM_METRICS_TOKEN` removed from `.env.example`; AC4: `make check-dead-code` gate exists and passes; AC5: Zero DEPRECATED markers in project .sh/.py files (excluding .venv) |
| **IMPLEMENTS** | Brief 077 RC-4 (Dead Code) |
| **IMPACTS** | `core/modules/nginx/install.sh` (DELETE), `core/internal/bootstrap/ssl-provision.sh` (DELETE or verify), `.env.example` (modify), `Makefile` (add check-dead-code target), `tests/gates/test_gate_dead_code.py` (update), possibly `core/internal/bootstrap/node-lifecycle.sh` and `core/internal/bootstrap/lifecycle/steps.py` |
| **REQUIRES** | 080-cert-unification (cert functions migrated to cert_orchestrator.py), 071-done-migration (.done files deprecated) |

---

## 1. Requirements Analysis

### 1.1 Current State — Dead Code Inventory

| # | File | LOC | Status | Callers |
|---|------|-----|--------|---------|
| 1 | `core/modules/nginx/install.sh` | 1107 | DEPRECATED (L25) | 0 direct callers in code; nginx is now docker-type module; deploy-modules.sh calls install.sh only for system modules |
| 2 | `core/internal/bootstrap/ssl-provision.sh` | 40 | Backward-compat wrapper | Referenced in: `node-lifecycle.sh` L85-86 (WEBNAMES_API_KEY load + checkpoint_step), `scripts-audit.sh` L43 (whitelist), `test_gate_no_unregistered_entrypoint.py` L67 (whitelist exception), `test_gate_dead_code.py` L98 (known dead code) |
| 3 | `LITELLM_METRICS_TOKEN` | 1 line | Defined in `.env.example` L129 as empty string | 0 consumers — `core/modules/monitoring/docker-compose.base.yml` L39 references it in a comment noting migration to `LITELLM_MASTER_KEY` |
| 4 | `.done` checkpoint files | — | Deprecated by 071 (state_machine.py state.json) | `node-lifecycle.sh` line 178 references `.done` in a backup mechanism |

### 1.2 Key Success Criteria

1. **SC1:** `nginx/install.sh` deleted — no code, no tests, no CI references remain
2. **SC2:** `ssl-provision.sh` deleted after verifying `node-lifecycle.sh` doesn't depend on its existence (the wrapper delegates to `install-acme.sh` + `issue-cert.sh`)
3. **SC3:** `.env.example` no longer contains `LITELLM_METRICS_TOKEN`
4. **SC4:** `make check-dead-code` gate valid — blocks merge if DEPRECATED markers > 30 days old
5. **SC5:** `grep -r "DEPRECATED" --include="*.sh" --include="*.py" .` returns 0 project hits (excluding .venv, .git, .ai)

---

## 2. Architecture Overview

### 2.1 Deletion Decision Matrix

```
┌─────────────────────────────────────────────────────────────┐
│                    Dead Code Decision Flow                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  nginx/install.sh (1107 LOC, DEPRECATED)                     │
│  ├─ grep callers → 0 direct callers                          │
│  ├─ cert functions → migrated to cert_orchestrator.py (080)  │
│  ├─ nginx systemd install → nginx is now Docker module       │
│  └─ DECISION: DELETE ✓                                       │
│                                                              │
│  ssl-provision.sh (40 LOC, backward-compat wrapper)          │
│  ├─ grep callers → node-lifecycle.sh (WEBNAMES_API_KEY)      │
│  │   └─ ssl-provision.sh is SOURCED for WEBNAMES_API_KEY     │
│  │      load, NOT executed. If we move key loading to        │
│  │      install-acme.sh or issue-cert.sh → safe to delete.   │
│  ├─ scripts-audit.sh → whitelist entry (remove)              │
│  ├─ test exceptions → 2 test files (remove exceptions)       │
│  └─ DECISION: DELETE after moving WEBNAMES_API_KEY load      │
│                                                              │
│  LITELLM_METRICS_TOKEN in .env.example                       │
│  ├─ grep consumers → 0 (monitoring compose uses MASTER_KEY)  │
│  ├─ secret-definitions.yaml → NOT present (already removed)  │
│  └─ DECISION: DELETE from .env.example ✓                     │
│                                                              │
│  check-dead-code CI gate (NEW)                               │
│  ├─ grep -r "DEPRECATED" *.sh *.py                           │
│  ├─ For each hit: git log → date of DEPRECATED addition      │
│  ├─ If > 30 days → FAIL                                      │
│  └─ make check-dead-code added to Makefile + CI workflow     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Draft Code Graph

```
┌────────────────────────────────────────────────────────────┐
│                     Deletion Wave                           │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  T1: Delete nginx/install.sh                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▶ verify 0 callers (grep) → rm install.sh            │  │
│  │ ▶ verify 0 .gitignore entries → clean up if needed   │  │
│  │ ▶ remove from template-manifest.yaml if present      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  T2: Delete ssl-provision.sh                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▶ move WEBNAMES_API_KEY load to issue-cert.sh        │  │
│  │ ▶ rm ssl-provision.sh                                │  │
│  │ ▶ update node-lifecycle.sh: remove ssl-provision     │  │
│  │   references (L85-86, L220)                          │  │
│  │ ▶ update steps.py: remove ssl_provision step mention │  │
│  │ ▶ remove from scripts-audit.sh whitelist             │  │
│  │ ▶ remove from test_gate_dead_code.py known list      │  │
│  │ ▶ remove from test_gate_no_unregistered_entrypoint   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  T3: Remove LITELLM_METRICS_TOKEN                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▶ sed delete LITELLM_METRICS_TOKEN= from .env.example│  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  T4: Add make check-dead-code gate                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▶ Add check-dead-code.sh (grep DEPRECATED + git log) │  │
│  │ ▶ Add Makefile target                                 │  │
│  │ ▶ Add to CI workflow (make gate fast)                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  T5: Cleanup remaining DEPRECATED markers                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ ▶ grep -r "DEPRECATED" --include="*.sh" --include=   │  │
│  │   "*.py" . (excl .venv) → 3 hits in project code     │  │
│  │ ▶ scp-deliver.sh — verify if truly deprecated        │  │
│  │ ▶ test files — DEPRECATED in test code ≠ dead code   │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 2.3 ssl-provision.sh Dependency Analysis

`ssl-provision.sh` IS referenced by `node-lifecycle.sh` in two places:

1. **Line 85-86:** `source` for WEBNAMES_API_KEY loading from secrets.env
2. **Line 220:** `checkpoint_step "ssl-provision"` — state machine checkpoint name

Neither requires `ssl-provision.sh` to exist as a file. The checkpoint name is just a string. The WEBNAMES_API_KEY loading logic (load from `${secrets_env}` file) can be moved to `issue-cert.sh` or done inline in `node-lifecycle.sh`.

The actual `ssl-provision.sh` content (40 lines) is a thin wrapper that:
- Sources `install-acme.sh` and calls `install_acme()` (for init mode)
- Sources `issue-cert.sh` and calls `issue_cert()` (for update mode)

After wave 080, `cert_orchestrator.py` handles cert provisioning, making this wrapper fully redundant.

---

## 3. Step-by-Step Data Flow

### 3.1 Deletion Flow

```
Phase 1: Verify callers
    ├── T1-A: grep -r "install.sh" core/internal/ --include="*.sh" | grep -v "platform-secrets\|template"
    │   → 0 nginx/install.sh callers → safe to delete
    ├── T1-B: grep -r "nginx/install" . --include="*.sh" --include="*.py" --include="*.yaml" --include="*.yml"
    │   → template-manifest.yaml references install.sh as consumer of templates → clean up
    └── T1-C: git rm core/modules/nginx/install.sh

Phase 2: Migrate WEBNAMES_API_KEY loading
    ├── Copy key-load logic from ssl-provision.sh to issue-cert.sh (or node-lifecycle.sh inline)
    ├── Verify: grep "WEBNAMES_API_KEY" core/internal/bootstrap/issue-cert.sh → present and functional
    └── git rm core/internal/bootstrap/ssl-provision.sh

Phase 3: Update references
    ├── node-lifecycle.sh: remove ssl-provision source lines, update checkpoint_step name if needed
    ├── steps.py: remove ssl_provision mention
    ├── scripts-audit.sh: remove whitelist entry
    ├── test_gate_dead_code.py: remove ssl-provision.sh from known list
    └── test_gate_no_unregistered_entrypoint.py: remove exception

Phase 4: Clean .env.example
    └── Remove LITELLM_METRICS_TOKEN= line

Phase 5: Add CI gate
    ├── Create core/entrypoints/check-dead-code.sh
    ├── Add make check-dead-code target
    └── Add to CI gate workflow

Phase 6: Run full gate
    └── make gate MODE=fast → green
```

---

## 4. $TASKS

| ID | Task | Files | Complexity | Deps | Acceptance |
|----|------|-------|------------|------|------------|
| **T1** | Delete `nginx/install.sh` + clean up template-manifest.yaml reference | 2 | 2 | 080-cert-unification | `git status` shows deleted; `grep -r "nginx/install" core/` returns 0 hits |
| **T2** | Move WEBNAMES_API_KEY loading from ssl-provision.sh → issue-cert.sh; delete ssl-provision.sh | 1+D | 3 | T1 | `grep -r "ssl-provision.sh" core/internal/bootstrap/node-lifecycle.sh` returns 0 hits; ssl-provision.sh deleted |
| **T3** | Update all ssl-provision.sh references: node-lifecycle.sh, steps.py, scripts-audit.sh, test files | 5 | 3 | T2 | All references updated; `grep -r "ssl-provision.sh" --include="*.sh" --include="*.py" --include="*.yaml" .` returns 0 (except .ai/ plans) |
| **T4** | Remove `LITELLM_METRICS_TOKEN=` from `.env.example` | 1 | 1 | None | `grep LITELLM_METRICS_TOKEN .env.example` returns 0 |
| **T5** | Create `core/entrypoints/check-dead-code.sh` — CI gate that detects DEPRECATED markers > 30 days | 1 | 3 | None | `make check-dead-code` exits 0 on clean state, 1 on stale DEPRECATED |
| **T6** | Add `make check-dead-code` to Makefile + CI gate workflow | 2 | 2 | T5 | `make check-dead-code` is a valid target; gate includes it |
| **T7** | Verify remaining DEPRECATED markers: `scp-deliver.sh` — update or remove marker | 1 | 2 | None | Zero DEPRECATED markers in project .sh/.py (excluding .venv, .git, .ai) |
| **T8** | Run full gate: `make fix-gate && make gate MODE=fast` | — | 2 | T1-T7 | All gates green |

### Critical Path

```
T4 (independent) ──────────────────────────────┐
                                                ├──> T8
T1 → T2 → T3 ──────────────────────────────────┤
                                                │
T5 → T6 ───────────────────────────────────────┤
                                                │
T7 ────────────────────────────────────────────┘
```

---

## 5. $PARALLEL_GROUPS

### Wave 1: Independent Deletions + Gate Creation (no shared files)
- Tasks: **T1, T4, T5, T7**
- No file intersections. T1 touches nginx/ + template-manifest.yaml; T4 touches .env.example; T5 creates new file; T7 touches scp-deliver.sh.
- Command: `coder Read DevPlan.md, implement Wave 1: T1, T4, T5, T7`

### Wave 2: ssl-provision.sh Cascade (depends on T1 for template-manifest, T2 depends on nothing else)
- Tasks: **T2**
- Dependency: T1 (template-manifest.yaml already cleaned)
- Command: `coder Read DevPlan.md, implement Wave 2: T2`

### Wave 3: Reference Updates (depends on T2 — file is deleted)
- Tasks: **T3, T6**
- Command: `coder Read DevPlan.md, implement Wave 3: T3, T6`

### Wave 4: Final Verification
- Tasks: **T8**
- Command: `coder Read DevPlan.md, implement Wave 4: T8`

---

## 6. Acceptance Criteria

| AC | Criterion | Verification |
|----|-----------|-------------|
| AC1 | `nginx/install.sh` deleted | `ls core/modules/nginx/install.sh` → "No such file" |
| AC2 | `ssl-provision.sh` deleted | `ls core/internal/bootstrap/ssl-provision.sh` → "No such file" |
| AC3 | All ssl-provision references cleaned | `grep -r "ssl-provision.sh" --include="*.sh" --include="*.py" --include="*.yaml" . \| grep -v '.ai/\|.venv/\|.git/'` → 0 |
| AC4 | `LITELLM_METRICS_TOKEN` removed from .env.example | `grep LITELLM_METRICS_TOKEN .env.example` → 0 |
| AC5 | `make check-dead-code` exits 0 | Run command → exit 0 |
| AC6 | Zero DEPRECATED markers in project code | `grep -r "DEPRECATED" --include="*.sh" --include="*.py" . \| grep -v '.venv/\|.git/\|.ai/'` → 0 (or all remaining have git-log date ≤ 30 days) |
| AC7 | `make gate MODE=fast` passes | All gate tests green |

---

## 7. File Manifest

### Files Deleted

| # | File | LOC | Reason |
|---|------|-----|--------|
| 1 | `core/modules/nginx/install.sh` | 1107 | DEPRECATED, cert functions migrated to cert_orchestrator (080), nginx is Docker module |
| 2 | `core/internal/bootstrap/ssl-provision.sh` | 40 | Backward-compat wrapper, all logic migrated to install-acme.sh + issue-cert.sh + cert_orchestrator.py |

### Files Modified

| # | File | Change |
|---|------|--------|
| 3 | `.env.example` | Remove `LITELLM_METRICS_TOKEN=` line |
| 4 | `core/internal/bootstrap/node-lifecycle.sh` | Remove ssl-provision.sh source + checkpoint references |
| 5 | `core/internal/bootstrap/lifecycle/steps.py` | Remove ssl_provision step mention |
| 6 | `core/internal/scripts-audit.sh` | Remove ssl-provision.sh whitelist entry |
| 7 | `core/templates/template-manifest.yaml` | Remove nginx/install.sh consumer references |
| 8 | `tests/gates/test_gate_dead_code.py` | Remove ssl-provision.sh from known dead code list |
| 9 | `tests/gates/test_gate_no_unregistered_entrypoint.py` | Remove ssl-provision.sh exception |

### Files Created

| # | File | Purpose |
|---|------|---------|
| 10 | `core/entrypoints/check-dead-code.sh` | CI gate: detects DEPRECATED markers > 30 days old |
| 11 | `Makefile` (modify) | Add `check-dead-code` target |

---

## 8. Design Decisions

### ## @rationale D1: Why delete nginx/install.sh rather than keep as reference?
**Q:** install.sh is 1107 LOC of working code. Why not keep it?

**A:** The cert functions in install.sh (`_issue_acme_cert`, `_acme_install_cron`) are DIVERGENT from canonical versions in `cert_orchestrator.py` (080). Specifically, install.sh's cron setup lacks `--renew-hook` for S3 backup — if an agent reads install.sh as source of truth, it will create cert renewal without S3 backup. Dead code is not neutral — it's an active source of drift. Deleting it removes the risk entirely.

### ## @rationale D2: Why 30-day grace period for DEPRECATED markers?
**Q:** Why not 0 days — delete immediately?

**A:** A DEPRECATED marker is a migration signal: "this will be removed." Consumers need time to migrate. 30 days is one release cycle — enough for other waves to complete their migrations (e.g., wave 080 cert unification). After 30 days, the grace period is over and the marker itself is dead code.

### ## @rationale D3: Why move WEBNAMES_API_KEY load to issue-cert.sh?
**Q:** ssl-provision.sh is 40 LOC. Why spend effort moving key loading instead of keeping the file?

**A:** The file exists ONLY for backward compatibility. It sources two other scripts and does nothing else. Keeping a 40-line wrapper that delegates to two other scripts is exactly the kind of "thin wrapper that never dies" — it accumulates technical debt indefinitely. Moving the ONE remaining dependency (WEBNAMES_API_KEY load) eliminates the wrapper entirely.

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_dead_code.py` | `test_no_deprecated_markers_stale` | All DEPRECATED markers in project code are ≤30 days old | `check-dead-code.sh` gate |
| `tests/gates/test_gate_dead_code.py` | `test_nginx_install_sh_deleted` | nginx/install.sh does not exist on disk | File system |
| `tests/gates/test_gate_dead_code.py` | `test_ssl_provision_sh_deleted` | ssl-provision.sh does not exist on disk | File system |
| `tests/gates/test_gate_dead_code.py` | `test_litellm_metrics_token_removed` | LITELLM_METRICS_TOKEN not in .env.example | .env.example |
| `tests/gates/test_gate_dead_code.py` | `test_no_ssl_provision_references` | No code references ssl-provision.sh by path | All project .sh/.py/.yaml files |
| `tests/gates/test_gate_no_unregistered_entrypoint.py` | `test_no_ssl_provision_exception` | ssl-provision.sh is NOT in the exception list | Gate test itself |

---

## 10. TRAP References

- **TRAP[DEBT] · 2026-07-16** in `nginx/install.sh` L34 — legacy system-nginx installer → **RESOLVED**: file deleted
- **TRAP[DECISION] · 2026-07-17** in `ssl-provision.sh` L23 — backward-compat wrapper → **RESOLVED**: file deleted after dependency migration
- **DEPRECATED in `scp-deliver.sh`** — verify current status; if truly deprecated and >30 days, remove or migrate

---

## Next Steps

### Wave 1: Independent Deletions
```
coder Read .ai/plans/084-dead-code-sweep/01-DevPlan.md, implement Wave 1: T1, T4, T5, T7
```

### Wave 2: ssl-provision Cascade
```
coder Read .ai/plans/084-dead-code-sweep/01-DevPlan.md, implement Wave 2: T2
```

### Wave 3: Reference Updates
```
coder Read .ai/plans/084-dead-code-sweep/01-DevPlan.md, implement Wave 3: T3, T6
```

### Wave 4: Final Verification
```
coder Read .ai/plans/084-dead-code-sweep/01-DevPlan.md, implement Wave 4: T8
```

$END_DEVPLAN
