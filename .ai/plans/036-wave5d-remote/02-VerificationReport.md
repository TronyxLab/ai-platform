$START_VERIFICATION_REPORT

# VerificationReport 02 — DevPlan 036-Wave5d Quality Audit

🔒 Verified against SHA: `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`
📅 Date: 2026-07-26
📁 Target: `.ai/plans/036-wave5d-remote/01-DevPlan.md`
⚠️ Uncommitted changes detected: 15 files modified (including master DevPlan 036, .env.example, various Python/bootstrap modules). Report is based on committed HEAD state + the DevPlan file itself.

---

## $ARTIFACT_CONTRACT

- **PURPOSE:** Verify DevPlan 036-Wave5d for protocol compliance, content quality, design decision correctness, and cross-plan consistency
- **DESCRIPTION:** QA audit of the sub-DevPlan for TASK-036D (Strangler-Fig: remote-cmd.sh → overlay_deliverer.py). Covers protocol compliance (7-point $ARTIFACT_CONTRACT), content quality (debt intake, superposition, D3 presence, test spec), arithmetic consistency (LOC estimates), and cross-plan alignment with master DevPlan 036
- **RATIONALE:** Sub-DevPlans inherit architectural decisions from the master but refine estimates and implementation details independently. Verification ensures no drift or contradiction.
- **ACCEPTANCE_CRITERIA:** All checklist items verified with evidence; semantic verdict produced; findings tagged with severity and line references
- **IMPLEMENTS:** QA verification of DevPlan artifact per §QA workflow (targeted audit, not full Phase 1-6)
- **IMPACTS:** Findings feed into Coder implementation guidance; may require DevPlan revision before implementation
- **REQUIRES:** `01-DevPlan.md` (target), master `036-wave5-strangler-shell-monoliths/01-DevPlan.md` (reference), `remote-cmd.sh` (baseline), `core/lib/node-resolver.sh` (contract reference)

$END_ARTIFACT_CONTRACT

---

## Section 1 — DevPlan Protocol Compliance

| Check | Status | Evidence |
|-------|:------:|----------|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✅ PASS | Lines 1, 635 |
| `$ARTIFACT_CONTRACT` present | ✅ PASS | Line 5 |
| `$END_ARTIFACT_CONTRACT` present | ✅ PASS | Line 30 |
| PURPOSE field | ✅ PASS | Line 6 |
| DESCRIPTION field | ✅ PASS | Line 7 |
| RATIONALE field | ✅ PASS | Line 8 |
| ACCEPTANCE_CRITERIA field (9 ACs) | ✅ PASS | Lines 9-18 |
| IMPLEMENTS field | ✅ PASS | Line 19 |
| IMPACTS field | ✅ PASS | Lines 20-24 |
| REQUIRES field | ✅ PASS | Lines 25-29 |
| `$TASKS` section | ✅ PASS | Lines 438-467 |
| `$TEST_SPEC` section | ✅ PASS | Lines 471-496 |

**Verdict: PASS** — All 7 $ARTIFACT_CONTRACT fields present and well-formed. Task definition, test spec, and merge rule check are complete.

---

## Section 2 — Content Quality

### 2.1 Debt Intake (TRAP Audit)

All 4 TRAP[BUG] from `remote-cmd.sh` are catalogued and dispositioned:

| TRAP | Description | Disposition | Status |
|------|-------------|-------------|:------:|
| T1 | `ci_deploy_key` from node.yaml not exported (L95) | Shell: stays in `build_ssh_cmd()` | ✅ Covered |
| T2 | VPS self-SSH loop (L279) | Shell: stays in `execute_remote_update()` + documented in Python | ✅ Covered |
| T3 | `node-update` не доставлял `core/` (L294) | Python: `sync_core_to_vps()` with TRAP annotation | ✅ Covered |
| T4 | `bare ssh_exec` may silently fail under `set -e` (L360,483,650) | Shell: stays in execute-functions with `\|\| { local rc=$?; ... }` | ✅ Covered |

New TRAPs added:
- `TRAP[DECISION]` — Wave 5d: printf %q stays in shell (line 564)
- `TRAP[DEBT]` — node-resolver.sh inline `python3 -c` block (line 572)

**Verdict: PASS** — Complete TRAP coverage. All existing TRAPs dispositioned, new design decisions and debt traps properly annotated.

### 2.2 Superposition Analysis

| Option | Description | Score | Status |
|--------|-------------|:-----:|--------|
| A — Hybrid shell+Python | printf %q builders stay shell, business logic → Python | 7.5 | **SELECTED** |
| B — Full Python `subprocess.run` | All 672 LOC → Python with `shlex.quote()` | 5.8 | Rejected (quoting incompatibility) |
| C — Leave as-is | No changes, TRAP documentation only | 3.3 | Rejected (language policy violation) |
| D — Reverse Strangler | Python orchestrator + shell plugins for quoting | 6.3 | Rejected (over-engineered) |

**Verdict: PASS** — ≥3 options presented including hybrid approach. Scoring matrix with 6 dimensions. Option A correctly selected based on D3 constraint and risk profile.

### 2.3 Design Decision — D3 Verification

**Question from checklist:** "D3 about printf %q staying in shell MUST be present"

✅ **CONFIRMED** — Lines 396-405, `## @rationale D3: printf %q command builders остаются в shell`

The rationale explicitly addresses:
- `printf '%q'` is a bash-builtin, not reproducible in Python
- `shlex.quote()` uses POSIX single-quote wrapping — incompatible with `$VAR` expansion in `bash -c "..."`
- Shell handles quoting, Python handles flow control and error handling

This correctly inherits and extends master DevPlan D3 (line 409 in master DevPlan). The sub-DevPlan adds specific technical detail about edge cases that the master DevPlan omits — this is genuine value-add, not duplication.

### 2.4 Design Decisions — Other D-points

| D# | Description | Quality |
|:---|-------------|:-------:|
| D1 | `resolve_node_yaml` + `extract_node_host` → Python | ✅ 3 justifications (dedup, inline p3 removal, testability) |
| D2 | `sync_core_to_vps` → Python | ✅ 3 justifications (AC-1, TRAP T3, testability) |
| D3 | `printf %q` builders stay shell | ✅ Edge-case analysis, bash-compatible quoting |
| D4 | `_resolve_and_extract` helper | ✅ DRY, error handling consolidation |
| D5 | Full Python port for `deliver_vhost_overlays` | ✅ Nature-of-function analysis (business logic vs SSH proxy) |
| D6 | CLI subcommands, not single entrypoint | ✅ SRP, testability, shell integration |

**Verdict: PASS** — All 6 design decisions have `## @rationale` tags with explicit Q&A format. Each answers "why this approach" with concrete justifications.

---

## Section 3 — $TASKS and $TEST_SPEC

### 3.1 Task Definition

| Field | Value | Status |
|-------|-------|:------:|
| Task ID | TASK-036D | ✅ Matches master DevPlan |
| Owner | Coder | ✅ |
| Complexity | 4/10 | ✅ Reasonable for 3-file change |
| Estimated effort | 2-3 hours (1 Coder session) | ✅ Matches complexity |
| Dependencies | None | ✅ Correct — TASK-036D is parallel with Wave 2 |

### 3.2 Test Specification

| Requirement | Actual | Status |
|-------------|--------|:------:|
| Minimum tests | ≥5 | 10 specified | ✅ |
| Test file | `tests/unit/test_overlay_deliverer.py` (~150 LOC) | ✅ Correct path |
| Fixture strategy | `tmp_path` with YAML fixtures | ✅ Zero Hardcode Rule |
| Coverage target | ≥80% | ✅ With 10 tests for ~200 LOC module |

Test inventory (all 10):

| # | Test function | Scenario | Module under test |
|:--|---------------|----------|-------------------|
| 1 | `test_resolve_node_yaml_found` | node.yaml found path-1 | `resolve_node_yaml()` |
| 2 | `test_resolve_node_yaml_not_found` | No node.yaml → error | `resolve_node_yaml()` |
| 3 | `test_extract_node_host_with_host` | Valid host in YAML | `extract_node_host()` |
| 4 | `test_extract_node_host_empty` | No `node.host` field | `extract_node_host()` |
| 5 | `test_deliver_no_overlays` | Empty overlays dir → skip | `deliver_vhost_overlays()` |
| 6 | `test_deliver_dry_run` | Dry-run prints, no exec | `deliver_vhost_overlays()` |
| 7 | `test_deliver_with_overlays_mocked` | .conf files + mocked subprocess | `deliver_vhost_overlays()` |
| 8 | `test_sync_core_dry_run` | Dry-run prints rsync cmd | `sync_core_to_vps()` |
| 9 | `test_sync_core_rsync_failure` | rsync non-zero → error | `sync_core_to_vps()` |
| 10 | `test_deliver_mkdir_failure` | ssh mkdir fails → error | `deliver_vhost_overlays()` |

**Verdict: PASS** — All 10 tests have clear scenarios, specific modules under test, and follow Zero Hardcode Rule.

---

## Section 4 — Critical Design Decision Check

### 4.1 printf %q Builders Stay in Shell

**Checklist item:** "Are printf %q command builders (build_ssh_cmd, build_update_ssh_cmd, build_converge_ssh_cmd) explicitly left in shell?"

✅ **CONFIRMED**

| Function | Line | Status | LOC estimate |
|----------|------|:------:|:------------:|
| `build_ssh_cmd()` | Line 77 (table), 207 (code graph) | **STAY shell** | 65 |
| `build_update_ssh_cmd()` | Line 78 (table), 208 (code graph) | **STAY shell** | 55 |
| `build_converge_ssh_cmd()` | Line 79 (table), 209 (code graph) | **STAY shell** | 15 |

All three explicitly marked "STAY shell" in both the Requirements Analysis table (lines 77-79) and the Draft Code Graph (lines 207-209). The rationale is documented at lines 396-405 with edge-case analysis.

### 4.2 Rationale for shlex.quote Incompatibility

**Checklist item:** "Is the rationale documented (shlex.quote not identical for all edge cases)?"

✅ **CONFIRMED** — Lines 400-403:

> `printf '%q'` — bash-builtin for shell-safe quoting. Python-аналог `shlex.quote()` **не идентичен** для edge cases:
> - `printf '%q'` экранирует пробелы, кавычки, спецсимволы специфичным для bash образом
> - `shlex.quote()` использует single-quote wrapping (POSIX), что может дать другой результат для переменных с `$` и backtick'ами

This is technically accurate: `shlex.quote()` wraps in single quotes and escapes embedded single quotes, while `printf '%q'` uses bash-specific `$'...'` ANSI-C quoting or backslash escaping depending on context. For `bash -c "..."` on the remote side, bash-native quoting is required.

### 4.3 Shell Facade ≤200 LOC — Realistic?

**Checklist item:** "Shell facade target: ≤200 LOC? Realistic given printf %q builders stay?"

⚠️ **AT RISK** — The DevPlan's own estimates suggest this target is unlikely to be met.

**Analysis:**

The sum of individual post-migration LOC estimates from the reduction table (lines 270-279):

| Block | Post-migration LOC |
|-------|:---:|
| MODULE_CONTRACT + header | 12 |
| source guards | 8 |
| `build_ssh_cmd()` | 65 |
| `build_update_ssh_cmd()` | 55 |
| `build_converge_ssh_cmd()` | 15 |
| `_resolve_and_extract()` (NEW) | 12 |
| `execute_remote_update()` | 22 |
| `execute_remote_converge()` | 17 |
| `execute_remote_reconcile()` | 17 |
| `execute_remote_reconcile_entrypoint()` | 2 |
| `deliver_vhost_overlays()` | 3 |
| **Sum (logic only)** | **228** |
| Estimated inter-function whitespace + shebang | ~15-20 |
| **Estimated total `wc -l`** | **~243-248** |

**Contradiction:** The table at line 281 says "Total (логика) ~216" and line 282 says "Total (файл, с комментариями) ~200". The latter number (200) is **below** the sum of individual logic estimates (228) — a mathematical impossibility since total `wc -l` must be ≥ sum of logic sections.

**Gap analysis:**
- Shell facade AC-1 target: ≤200 LOC
- Best-case estimate from DevPlan's own numbers: 228 (logic) + 15 (whitespace) = ~243
- Gap: **43 lines over target** (minimum)

**Mitigation assessment:** The DevPlan mentions "агрессивное сжатие комментариев в execute-функциях (однострочные TRAP вместо многострочных)" at line 284. This could save ~20-30 lines across 4 execute functions + deliver facade, but that still leaves a ~15-25 line gap. The `printf %q` builders at 135 LOC are the structural floor — they cannot be reduced without violating D3.

**Additional concern:** `build_update_ssh_cmd()` estimated at 55 LOC post-migration — 3 lines MORE than the current 52 LOC, despite being labeled "— (без изменений)" at line 273. This arithmetic inconsistency further undermines confidence in the estimates.

**Impact on AC-1:** The acceptance criterion `wc -l core/internal/bootstrap/remote-cmd.sh ≤ 200` (line 448) is the hard gate for task completion. If the estimate is correct, this gate will fail on the first implementation attempt, triggering rework.

**Recommendations:**
1. **Adjust target:** Raise AC-1 to ≤250 LOC with explicit rationale (printf %q builders at 135 LOC are the structural floor, D3 prohibits their migration)
2. **OR find additional savings:** Identify 43+ lines of further reduction beyond current estimates (recommend pre-implementation dry-run counting)
3. **OR add exception clause:** Keep ≤200 LOC as stretch goal; define ≤250 LOC as acceptable threshold with documented justification

---

## Section 5 — Cross-Plan Consistency

### 5.1 Independence Declaration

**Checklist item:** "This DevPlan declares itself independent of other DevPlans — verify this is correct"

✅ **CONFIRMED CORRECT**

- Sub-DevPlan line 457: "Dependencies: None (не зависит от TASK-036A/B/C/E/F/G; может идти параллельно с Wave 2)"
- Master DevPlan line 462: "Dependencies: None (может идти параллельно с Wave 2)"
- Master DevPlan line 525: TASK-036D listed as parallel with TASK-036B in dependency graph
- TASK-036E depends ON TASK-036D (master DevPlan line 471), but TASK-036D has no incoming dependencies — correct

### 5.2 ssh_command_parser.py Reference

**Checklist item:** "Does it reference ssh_command_parser.py (from DevPlan 081)?"

✅ **CORRECTLY REFERENCED, CORRECTLY EXCLUDED**

- Line 24 (IMPACTS): "core/internal/shared/ssh_command_parser.py — НЕ затрагивается"
- Line 602 (File Manifest): "SSH_ORIGINAL_COMMAND parser (DevPlan 081) — не используется в данной миграции, без изменений"
- Line 617 (References): listed as shared module reference

**Verification:** `grep ssh_command_parser remote-cmd.sh` returns zero matches — the module is indeed NOT imported or used by remote-cmd.sh. The exclusion is correct. This prevents a future Coder from attempting to integrate it unnecessarily.

### 5.3 Master DevPlan Estimate Drift

| Parameter | Master DevPlan | Sub-DevPlan | Drift |
|-----------|:-------------:|:-----------:|:-----:|
| Python module LOC | ~150 (line 460) | ~200 (line 7) | +50 LOC |
| Shell facade LOC | ~200 (line 460) | ~200 (line 7) | None |
| Test LOC | Not specified | ~150 (line 23) | N/A |
| Task complexity | 4/10 (line 463) | 4/10 (line 458) | None |

**Finding:** Python module estimate grew from 150→200 LOC (+33%) between master and sub-DevPlan. This may be justified by the addition of `sync_core_to_vps()` and the 4-subcommand CLI structure, but there's no explicit justification in the sub-DevPlan for why the estimate increased. This is minor but worth noting for implementation scoping.

### 5.4 Previous VerificationReport Finding

**Master VerificationReport line 260:** "Clarify AC-1 exception for remote-cmd.sh (~200 LOC due to printf %q builders), or tighten shell facade"

**Status in sub-DevPlan:** The sub-DevPlan targets exactly ≤200 LOC (line 448) without explicit discussion of whether this is achievable or acknowledged as an exception. The master DevPlan AC-1 (line 10) already built in the exception ("≤200 для VPS и remote-cmd.sh"), so the target is consistent with master. However, the sub-DevPlan doesn't cross-reference this exception or the prior VerificationReport finding.

**Recommendation:** Add a note in the sub-DevPlan acknowledging the master's AC-1 exception and the prior VerificationReport recommendation.

---

## Section 6 — Risk Assessment Verification

**Checklist item:** "Risk Assessment: SSH proxy regression covered?"

✅ **CONFIRMED** — Line 504, Risk table row 1:

| Risk | Severity | Likelihood | Mitigation |
|------|:--------:|:----------:|------------|
| SSH proxy regression | 🟡 MEDIUM | Low | Unit tests for `resolve_node_yaml()` (found + not-found), shell helper `\|\| return 1`, LDD IMP:10 logging, dry-run verification |

The risk assessment covers all 6 identified risks with severity, likelihood, and concrete mitigation strategies. The SSH proxy regression is correctly rated MEDIUM (not HIGH) because:
- The change is limited to resolve/extract delegation (not command building)
- Dry-run mode allows pre-flight verification
- Unit tests cover the resolution path

---

## Section 7 — LDD Contract Assessment

| Check | Status | Evidence |
|-------|:------:|----------|
| Python module IMP levels specified | ✅ PASS | Lines 357-368: IMP:7-10 for all 4 functions |
| IMP:9 business logic coverage | ✅ PASS | `resolve_node_yaml` success, `deliver_vhost_overlays` start/complete, `sync_core_to_vps` start/complete |
| IMP:10 fatal coverage | ✅ PASS | All 4 functions have IMP:10 on failure paths |
| Shell facade IMP levels | ⚠️ MISSING | No IMP level specification for `execute_remote_*()` shell wrappers |

**Finding:** The LDD contract (lines 357-368) covers only the Python module. Shell facade `execute_remote_*()` functions should also log IMP:9-10 for resolution failures and SSH execution errors. This is a minor gap — the shell wrappers will likely inherit LDD patterns from the existing code, but explicit specification would ensure behavioral consistency.

---

## Section 8 — Summary of Findings

| # | Severity | ID | Description | Location |
|:--|:--------|:---|-------------|----------|
| F1 | **HIGH** | LOC-CONTRADICTION-1 | Total logic (228 LOC) exceeds total file estimate (200 LOC) — mathematical impossibility; AC-1 at risk | Lines 281-282 vs 270-279 |
| F2 | **HIGH** | AC-1-FEASIBILITY-1 | Shell facade ≤200 LOC likely unachievable: printf %q builders at 135 LOC floor + execute wrappers at 61 LOC + boilerplate at 32 LOC = 228 minimum. Gap: 28-48 LOC. | Lines 270-279, 448 |
| F3 | **MEDIUM** | ESTIMATE-DRIFT-1 | `build_update_ssh_cmd()` shows 52→55 LOC increase while labeled "без изменений" — arithmetic inconsistency | Line 273 |
| F4 | **MEDIUM** | CROSS-PLAN-DRIFT-1 | Python module LOC estimate grew from 150 (master) to 200 (sub-DevPlan) — no explicit justification | Line 7 vs master L460 |
| F5 | **MEDIUM** | PRIOR-FINDING-1 | Master VerificationReport recommendation #3 (D-CONTRACT-1) not explicitly addressed | Master VerificationReport L260 |
| F6 | **LOW** | IMPACTS-INCOMPLETE-1 | `core/lib/node-resolver.sh` not listed in IMPACTS despite being the contract for Python port | Line 20-24 |
| F7 | **LOW** | LDD-SHELL-MISSING-1 | Shell facade `execute_remote_*()` functions have no explicit IMP level specification | Lines 357-368 |
| F8 | **INFO** | --- | `ssh_command_parser.py` correctly excluded — verified not used by remote-cmd.sh | Line 24, 602 |

---

## Semantic Verdict

### **DEGRADED (HIGH)**

**Rationale:** The DevPlan's LOC arithmetic is internally inconsistent and the ≤200 LOC AC-1 target appears unrealistic based on the DevPlan's own estimates (228 LOC minimum logic, ~243-248 estimated total). This is a planning quality issue, not a design flaw — the architecture, superposition analysis, design decisions, TRAP coverage, and test specifications are all sound. However, an unachievable AC-1 means the task's completion gate is effectively set to fail before implementation begins.

**Blocking factors:** F1, F2 (HIGH)

**Non-blocking:** F3-F5 (MEDIUM), F6-F7 (LOW), F8 (INFO)

**Recommended action before implementation:**
1. Recalculate shell facade LOC with actual counting method (proposed: count lines of the planned shell facade skeleton)
2. Adjust AC-1 target to ≤250 LOC with explicit D3 justification, OR identify additional reduction paths
3. Address F3 (arithmetic correction for build_update_ssh_cmd)
4. Cross-reference master VerificationReport recommendation #3

$END_VERIFICATION_REPORT
