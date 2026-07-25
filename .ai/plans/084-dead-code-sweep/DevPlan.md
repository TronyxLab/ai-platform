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
| 2 | `core/internal/bootstrap/ssl-provision.sh` | 40 | 0 file-level callers (verified 2026-07-25) | WEBNAMES_API_KEY loading ALREADY migrated to `$secrets_env` (node-lifecycle.sh L84). Remaining refs: whitelist + test exceptions + comment mentions. |
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
│  ├─ grep callers → 0 file-level callers (verified 2026-07-25)│
│  │   └─ WEBNAMES_API_KEY loading ALREADY migrated:           │
│  │      update_step_3_ssl_provision() sources $secrets_env   │
│  │      directly (L84). No code sources ssl-provision.sh.    │
│  ├─ scripts-audit.sh → whitelist entry (remove)              │
│  ├─ test exceptions → 2 test files (remove exceptions)       │
│  └─ DECISION: DELETE — no migration needed, git rm directly  │
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
│  │ ▶ WEBNAMES_API_KEY ALREADY loaded from $secrets_env │  │
│  │   (node-lifecycle.sh L84) — NO migration needed     │  │
│  │ ▶ git rm core/internal/bootstrap/ssl-provision.sh    │  │
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

`ssl-provision.sh` has **zero file-level callers** (verified 2026-07-25):

- `grep -r "ssl-provision\.sh" core/internal/bootstrap/node-lifecycle.sh` → **0 hits**
- `update_step_3_ssl_provision()` sources `$secrets_env` directly (L84), NOT `ssl-provision.sh`
- WEBNAMES_API_KEY loading migration was completed in a prior wave — no action needed

Remaining references are all non-execution artifacts:
- `scripts-audit.sh` L43 — whitelist entry (remove)
- `test_gate_dead_code.py` L98 — known dead code exception (remove)
- `test_gate_no_unregistered_entrypoint.py` L67 — exception entry (remove)
- Comment references in `issue-cert.sh`, `install-acme.sh`, `steps.py`, `test_node_lifecycle_static.py`, `nginx/install.sh` — update/remove as T3 cleanup

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

Phase 2: Delete ssl-provision.sh (NO migration needed)
    ├── WEBNAMES_API_KEY ALREADY loaded from $secrets_env (node-lifecycle.sh L84)
    ├── Verify: grep -r "ssl-provision\.sh" core/internal/bootstrap/node-lifecycle.sh → 0
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
| **T2** | Delete `ssl-provision.sh` (NO migration needed — WEBNAMES_API_KEY already loaded from `$secrets_env` at node-lifecycle.sh L84) | 1 | 1 | 080-cert-unification | `git status` shows deleted; `grep -r "ssl-provision.sh" core/internal/bootstrap/node-lifecycle.sh` returns 0 hits |
| **T3** | Update all ssl-provision.sh references: steps.py, scripts-audit.sh, test files, comment references in issue-cert.sh + install-acme.sh | 6 | 3 | T2 | All references updated; `grep -r "ssl-provision.sh" --include="*.sh" --include="*.py" --include="*.yaml" .` returns 0 (except .ai/ plans) |
| **T4** | Remove `LITELLM_METRICS_TOKEN=` from `.env.example` | 1 | 1 | None | `grep LITELLM_METRICS_TOKEN .env.example` returns 0 |
| **T5** | Create `core/entrypoints/check-dead-code.sh` — CI gate that detects DEPRECATED markers > 30 days | 1 | 3 | None | `make check-dead-code` exits 0 on clean state, 1 on stale DEPRECATED |
| **T6** | Add `make check-dead-code` to Makefile + CI gate workflow | 2 | 2 | T5 | `make check-dead-code` is a valid target; gate includes it |
| **T7** | Fix `scp-deliver.sh` L84: remove misleading "DEPRECATED" from echo log (function `prepare_ssh_opts()` has 8 active callers — NOT dead code). Rename echo to "BACKWARD-COMPAT" and update @deprecated tag at L78-80. | 1 | 1 | None | `grep "DEPRECATED" core/internal/bootstrap/scp-deliver.sh` returns 0; `@deprecated` tag updated to reflect actual status |
| **T8** | Run full gate: `make fix-gate && make gate MODE=fast` | — | 2 | T1-T7 | All gates green |

### Critical Path

```
T2 (independent) ──┐
T4 (independent) ──┤
                    ├──> T8
T1 (independent) ──┤
                    │
T3 ────────────────┤
                    │
T5 → T6 ───────────┤
                    │
T7 ────────────────┘
```

---

## 5. $PARALLEL_GROUPS

### Wave 1: Independent Deletions + Gate Creation (no shared files)
- Tasks: **T1, T4, T5, T7**
- No file intersections. T1 touches nginx/ + template-manifest.yaml; T4 touches .env.example; T5 creates new file; T7 touches scp-deliver.sh.
- Command: `coder Read DevPlan.md, implement Wave 1: T1, T4, T5, T7`

### Wave 2: ssl-provision.sh Deletion (dependency-free — T2 is just git rm)
- Tasks: **T2**
- Changed: NO dependency on T1 (ssl-provision.sh has no file-level callers; WEBNAMES_API_KEY loading already migrated)
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
| 4 | `core/internal/bootstrap/node-lifecycle.sh` | Remove checkpoint name strings "ssl-provision" from echo/log_step (L85-86) — no file-level source reference to remove (WEBNAMES_API_KEY already loaded from $secrets_env L84) |
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

### ## @rationale D3: Why delete ssl-provision.sh now?
**Q:** ssl-provision.sh is only 40 LOC. Why spend effort deleting it?

**A:** The file exists ONLY for backward compatibility. It sources two other scripts and does nothing else. WEBNAMES_API_KEY loading was already migrated in a prior wave — `update_step_3_ssl_provision()` in `node-lifecycle.sh` now sources `$secrets_env` directly (L84), NOT `ssl-provision.sh`. With zero file-level callers, the wrapper is pure dead code. A 40-line wrapper that delegates to two other scripts is exactly the kind of "thin wrapper that never dies" — it accumulates technical debt indefinitely. Deleting it removes a dead file and the exception entries it requires in 3 test/audit files.

### ## @rationale D4: Why rename scp-deliver.sh DEPRECATED echo — not delete the function?
**Q:** scp-deliver.sh L84 echoes "DEPRECATED: prepare_ssh_opts()". Why not just delete the function?

**A:** `prepare_ssh_opts()` has 8 active callers (bootstrap.sh, remote-cmd.sh x4, scp-deliver.sh x3) and performs host-key management (`ssh-keygen -R` in init mode) that `SSH_OPTS_COMMON` from `lib/ssh.sh` does NOT cover. It delegates SSH options to `SSH_OPTS_COMMON` but adds host-key cleanup — a distinct concern. The word "DEPRECATED" in the echo log is misleading: it makes agents scan the file and conclude "dead code" when the function is actively used. Fix: rename echo from "DEPRECATED" to "BACKWARD-COMPAT" and update the `@deprecated` JSDoc tag at L78-80 to remove the stale "Removed in Wave 3" claim. The function is a backward-compat layer, not dead code.

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
- **TRAP[DECISION] · 2026-07-17** in `ssl-provision.sh` L23 — backward-compat wrapper → **RESOLVED**: file deleted (no dependency migration needed — already done)
- **DEPRECATED in `scp-deliver.sh`** — echo message "DEPRECATED" at L84 is misleading; `prepare_ssh_opts()` has 8 active callers and performs host-key management not covered by `SSH_OPTS_COMMON`. Fixed by T7: rename echo to "BACKWARD-COMPAT", update @deprecated tag.

---

## Next Steps

### Wave 1: Independent Deletions
```
coder Read .ai/plans/084-dead-code-sweep/01-DevPlan.md, implement Wave 1: T1, T4, T5, T7
```

### Wave 2: ssl-provision.sh Deletion (direct git rm — no migration)
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
