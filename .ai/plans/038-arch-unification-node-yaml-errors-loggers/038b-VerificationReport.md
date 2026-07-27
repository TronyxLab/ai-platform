$START_VERIFICATION_REPORT

# VerificationReport — 038b DevPlan (sys.exit + loggers + typed exceptions)

🔒 Verified against SHA: `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`
⚠️ Working tree is dirty (14 modified files) — verification against committed state.

---

## Section 1 — Static Audit (Phase 1)

### Compliance Matrix

| Check | 038b-DevPlan.md | 038b-Brief.md | Verdict |
|-------|----------------|---------------|---------|
| $START / $END boundary markers | `$START_DEVPLAN` (L1) / `$END_DEVPLAN` (L612) | `$START_BRIEF` (L1) / `$END_BRIEF` (L100) | ✅ PASS |
| $ARTIFACT_CONTRACT (7 fields) | PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES — все 7 | PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES — все 7 | ✅ PASS |
| IMPLEMENTS references correct upstream | IMPLEMENTS Brief 038b ✅ | IMPLEMENTS DevPlan 038 ⚠️ (см. Finding #11) | ⚠️ WARNING |
| REQUIRES references correct dependency | REQUIRES DevPlan 038a COMPLETED ✅ | REQUIRES DevPlan 038a ✅ | ✅ PASS |

### Summary
- $START/$END tags: PASS
- $ARTIFACT_CONTRACT: PASS (оба документа)
- IMPLEMENTS direction: DevPlan→Brief direction correct ✅; Brief→DevPlan direction semantically inverted ⚠️ (LOW)

---

## Section 2 — Drift Analysis (Phase 2)

### 2.1 — File Path Existence Verification (ALL files in scope)

Всего проверено **46** уникальных путей из DevPlan 038b File Manifest + P4.5 batch list. Результаты:

| Статус | Файл | Wave |
|--------|------|------|
| ✅ | `core/internal/shared/project_registry.py` | W2 |
| ✅ | `core/internal/scaffold/add-project.sh` | W2 |
| ✅ | `core/internal/scaffold/adopt-project.sh` | W2 |
| ✅ | `core/internal/scaffold/remove-project.sh` | W2 |
| ✅ | `core/internal/provisioner.py` | W3 |
| ✅ | `core/internal/reconciler_projects.py` | W3+W4 |
| ✅ | `core/internal/shared/content_hash.py` | W3 |
| ✅ | `core/internal/shared/docker_compose.py` | W3 |
| ✅ | `core/internal/shared/audit_logger.py` | W3 |
| ✅ | `core/internal/shared/ssh_command_parser.py` | W3+W4 |
| ✅ | `core/internal/shared/platform_deliver.py` | W3 |
| ✅ | `core/internal/bootstrap/_topo_sort.py` | W3 |
| ✅ | `core/internal/bootstrap/deploy/content_hash.py` | W3 |
| ✅ | `core/internal/bootstrap/deploy/sudoers_generator.py` | W3+W4 |
| ✅ | `core/internal/bootstrap/deploy/compose_preflight.py` | W3 |
| ✅ | `core/internal/bootstrap/deploy/docker_orchestrator.py` | W3+W4 |
| ✅ | `core/internal/bootstrap/deploy/spool_validator.py` | W3 |
| ✅ | `core/internal/bootstrap/deploy/secrets_validator.py` | W3 |
| ✅ | `core/modules/hermes-agent/watchdog/agent_watchdog.py` | W3 |
| ✅ | `core/internal/bootstrap/lifecycle/state_machine.py` | W4 |
| ✅ | `core/internal/bootstrap/lifecycle/steps.py` | W4 |
| ✅ | `core/internal/bootstrap/cert_orchestrator.py` | W4 |
| ✅ | `core/internal/bootstrap/s3_ssl_cache.py` | W4 |
| ✅ | `core/internal/bootstrap/preflight.py` | W4 |
| ✅ | `core/internal/bootstrap/discover_modules.py` | W4 |
| ✅ | `core/internal/bootstrap/lifecycle/secrets_manager.py` | W4 |
| ✅ | `core/internal/healthcheck/platform_export_metrics.py` | W4 |
| ✅ | `core/internal/healthcheck/metrics/cert_collector.py` | W4 |
| ✅ | `core/internal/healthcheck/metrics/project_collector.py` | W4 |
| ✅ | `core/internal/bootstrap/deploy/context_deployer.py` | W4 |
| ✅ | `core/internal/bootstrap/deploy/context_overlay.py` | W4 |
| ✅ | `core/internal/bootstrap/converge/reconciler.py` | W4 |
| ✅ | `core/internal/bootstrap/deploy/orphan_reconciler.py` | W4 |
| ✅ | `core/internal/llm/key_provisioner.py` | W4 |
| ✅ | `core/internal/scripts/sync_env_defaults.py` | W4 |
| ✅ | `core/internal/scripts/generate_entrypoint_manifest.py` | W4 |
| ✅ | `core/internal/scripts/generate_agents_md.py` | W4 |
| ✅ | `core/internal/scaffold/context_registry.py` | W4 |
| ✅ | `core/internal/scaffold/vhost_yaml_reader.py` | W4 |
| ✅ | `core/internal/shared/node_yaml.py` | W4 |
| ✅ | `core/entrypoint-manifest.yaml` | W4 |
| ✅ | `Makefile` (root) | W4 |
| ❌ | **`core/internal/shared/exceptions.py`** | **W4 dep** |
| ❌ | **`tests/unit/test_exceptions.py`** | **W4** |
| ⚠️ | `tests/unit/test_project_registry.py` | W2 |

**Всего:** 43 EXISTS, 2 MISSING, 1 EXISTS-BUT-MARKED-AS-NEW.

### 2.2 — Drift Register

#### [CRITICAL] DRIFT-DEP-1: Missing dependency `exceptions.py`

- **Severity:** CRITICAL (BLOCKER — blocks entire W4)
- **Files:** `core/internal/shared/exceptions.py` (referenced in DevPlan 038b §W4.0, §REQUIRES L15)
- **Expected:** File exists with 5 classes: `PlatformError`, `ConfigNotFoundError`, `ConfigParseError`, `ConfigValidationError`, `PlatformFatalError`
- **Actual:** File does not exist anywhere in the project (`glob **/exceptions.py` → 0 results, `grep 'class PlatformError' --include='*.py'` → 0 results)
- **Impact:** W4 полностью заблокирована. ~30 файлов не могут быть модифицированы (P4.1-P4.5, верхнеуровневые обработчики, CI gate, тесты). 038a-DevPlan.md существует (920 строк) но не имплементирован.
- **Fix:** Завершить DevPlan 038a (Wave 1) — создать `exceptions.py`, `test_exceptions.py`, и юнифицированный `node_yaml.py` фасад. После этого 038b W2+W3 можно реализовать независимо; W4 — следом.

#### [CRITICAL] DRIFT-GATE-1: CI gate regex broken — false negative

- **Severity:** CRITICAL (BLOCKER — gate will never catch violations)
- **File:** DevPlan 038b §W4.8, L313-316 (proposed Makefile target)
- **Pattern:** `grep -rn 'except\s\+Exception' core/internal/ --include='*.py'`
- **Problem:** Without `-E` (extended regex) or `-P` (PCRE) flags, standard POSIX `grep` treats `\s` as literal `s` and `\+` as literal `+`. The pattern matches the literal string `excepts+Exception` — which does not exist in any Python code.
- **Impact:** Gate `make check-exception-patterns` всегда будет проходить (0 matches), даже если в коде сотни `except Exception` блоков. Silent false negative — код будет проходить CI с нарушением AC6.
- **Fix:** Изменить на `grep -rEn 'except[[:space:]]+Exception' core/internal/ --include='*.py'` (POSIX extended) или `grep -rPn 'except\s+Exception' core/internal/ --include='*.py'` (GNU PCRE). Также: цель должна быть в `makefiles/ci.mk` (не в корневом `Makefile`), и интегрирована в `gate MODE=fast` pipeline (шаг 2c или 3).
- **Additional:** The `@! grep ... || (echo FAIL && exit 1)` logic is correct in principle (negate + OR), but the pattern must work first.

#### [CRITICAL] DRIFT-MANIFEST-1: entrypoint-manifest.yaml syntax mismatch

- **Severity:** CRITICAL (BLOCKER — proposed YAML doesn't conform to existing schema)
- **File:** DevPlan 038b §W4.8, L329-333 (proposed manifest entry)
- **Proposed syntax:**
  ```yaml
  - verb: check-exception-patterns
    type: gate
    description: "..."
    allowed_in: [gate-fast, gate-full]
  ```
- **Actual schema:** The manifest uses top-level category sections (`ci:`, `bootstrap:`, `deploy:`, ...) with entries containing `make_target`, `mechanism`, `delegates_to`, `signature`, `operation_ru`, `description`. New make targets are added to the `allowed_verbs` flat list (L1318-1371). There is NO `verb`, `type`, or `allowed_in` field in any existing entry.
- **Impact:** Adding a non-conformant YAML entry would break manifest validation (`make check-manifests` gate) and CI.
- **Fix:** 
  1. Add `check-exception-patterns` to `allowed_verbs` list (in alphabetical order between `check-env-defaults` and `check-file-lines`)
  2. Add a new entry in the `ci` section (or appropriate category) following the existing format: `make_target: check-exception-patterns`

#### [CRITICAL] DRIFT-TEST-1: `test_exceptions.py` missing — cannot augment

- **Severity:** CRITICAL (W4 §T4.7 depends on augmenting this file)
- **File:** `tests/unit/test_exceptions.py`
- **Expected:** File exists (created by 038a), W4 augments with 8 tests
- **Actual:** File does NOT exist (`glob tests/unit/test_exceptions.py` → 0 results)
- **Impact:** §W4.9 (T4.7) cannot be executed as "augment" — file must be created from scratch. All 8 tests on exit_code mapping and inheritance must be written as new tests, not additions.
- **Fix:** If 038a creates this file → proceed as plan. If not → W4 §T4.7 should be scoped as CREATE NEW (not augment), and test descriptions adjusted accordingly.

#### [HIGH] DRIFT-LINE-1: docker_orchestrator.py logger line off by 1

- **Severity:** HIGH (wrong line number causes confusion during implementation)
- **File:** `core/internal/bootstrap/deploy/docker_orchestrator.py`
- **Plan:** L99 → `logger = logging.getLogger("docker_orchestrator")`
- **Actual:** L100 — `logger = logging.getLogger("docker_orchestrator")`
- Δ = +1

#### [HIGH] DRIFT-LINE-2: agent_watchdog.py logger line off by 1

- **Severity:** HIGH
- **File:** `core/modules/hermes-agent/watchdog/agent_watchdog.py`
- **Plan:** L39 → `logger = logging.getLogger("watchdog")`
- **Actual:** L40 — `logger = logging.getLogger("watchdog")`
- Δ = +1

#### [HIGH] DRIFT-FILE-1: test_project_registry.py already exists

- **Severity:** HIGH (scope ambiguity — create vs replace)
- **File:** `tests/unit/test_project_registry.py`
- **Plan:** §W2.4, §File Manifest — marked as NEW file
- **Actual:** File exists (413 lines, created DevPlan 070, 2026-07-25). Contains subprocess-based tests that invoke `sys.exit()` indirectly.
- **Impact:** W2 §T2.4 proposes 7 tests including `test_cli_exit_code_success/failure` and `test_negative_sys_exit_in_library`. After W2 removes `sys.exit()` from library functions, the existing tests (which use subprocess to test exit codes) may need significant rework, not just augmentation. The plan should explicitly state whether to: (a) replace existing tests entirely, (b) augment with new tests while deprecating old subprocess-based tests, or (c) modify existing tests for the new `return (bool, str)` contract.
- **Fix:** Clarify scope in DevPlan §W2.4: "MODIFY existing test_project_registry.py (413 LOC, from DevPlan 070) — replace subprocess-based tests with direct function-call tests for new `(bool, str)` return contract."

#### [MEDIUM] DRIFT-COUNT-1: sys.exit() count discrepancy

- **Severity:** MEDIUM (effort estimation off by 14%)
- **File:** `core/internal/shared/project_registry.py`
- **Plan:** Claims 14 `sys.exit()` calls (§W2.1, L45: "Точек изменений: 14 sys.exit()")
- **Actual:** Grep shows 12 `sys.exit()` calls in function bodies (lines 54, 61, 73, 91, 117, 124, 131, 144, 172, 176, 183, 194). The remaining 2 references are in docstrings/comments (lines 34, 49 — `@io` descriptions, line 17 — `@invariants` note).
- **Note:** W2.2 table correctly lists exactly 12 replacements. The discrepancy is in the scope summary count (14 vs 12).

#### [MEDIUM] DRIFT-CI-1: Inline gate logic breaks delegation pattern

- **Severity:** MEDIUM (maintenance inconsistency)
- **File:** DevPlan 038b §W4.8 (proposed Makefile target)
- **Pattern:** All existing CI gates in `makefiles/ci.mk` delegate to shell/Python entrypoints: `check-dead-code` → `check-dead-code.sh`, `check-file-lines` → `check-file-lines.sh`, `validate` → `validate.sh`. The proposed `check-exception-patterns` uses inline `grep` in the Makefile.
- **Impact:** Breaks the delegation pattern. Harder to test independently, harder to reuse in non-Makefile contexts.
- **Fix:** Extract grep logic into `core/entrypoints/check-exception-patterns.sh` (or a Python script) and delegate from Makefile. Follow existing pattern: `@bash $(_platform_root)/core/entrypoints/check-exception-patterns.sh`.

#### [MEDIUM] DRIFT-GATE-INTEGRATION: Gate integration location unspecified

- **Severity:** MEDIUM (integration ambiguity)
- **File:** DevPlan 038b §W4.8, §W4.6 (T4.6)
- **Problem:** Plan says to add `check-exception-patterns` to "gate-fast: check-exception-patterns \ ...existing-gates..." but the actual `gate MODE=fast` pipeline is in `makefiles/ci.mk` (lines 115-144, 7 numbered steps). Adding it to root `Makefile` would have no effect — the root Makefile includes `makefiles/ci.mk` for the gate target.
- **Fix:**
  1. Add `check-exception-patterns` target to `makefiles/ci.mk` (delegation pattern)
  2. Add it to `.PHONY` declaration in `makefiles/ci.mk` line 13
  3. Add it as a step in `gate MODE=fast` pipeline (e.g., as step 2c between check-dead-code and anti-drift gates)

---

## Section 3 — Invariant Status (Phase 3)

### Architectural Invariants from Root AGENTS.md

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Makefile — единый фасад. Все операции через `make <target>` | HELD | W4.8 proposes `make check-exception-patterns` — correct |
| 2 | Модель деплоя: git push → CI | NOT_IMPACTED | W2-W4 are code-level changes, not deploy model |
| 4 | AGENTS.md — 3 канонических файла | NOT_IMPACTED | No AGENTS.md changes in scope |
| 5 | entrypoint-manifest.yaml — YAML-реестр канонических операций | **AT_RISK** | Proposed manifest entry syntax doesn't conform to existing schema (DRIFT-MANIFEST-1) |
| 8 | LiteLLM — PostgreSQL во всех окружениях | NOT_IMPACTED | — |
| 11 | Manifest Generation Contract | **AT_RISK** | If manifest syntax is incorrect, `make check-manifests` will fail |

### Wave-Specific Dependency Verification

| Claim | Verdict | Evidence |
|-------|---------|----------|
| W2 независима от W1 (038a) | ✅ HELD | project_registry.py doesn't import exceptions.py or NodeYaml |
| W3 независима от W1 (038a) | ✅ HELD | 15 logger replacements touch only `getLogger("literal")` lines — no dependency on 038a |
| W4 требует только exceptions.py из 038a | ✅ HELD (conceptually) | W4 P4.1-P4.5 only need PlatformError subclasses. Does NOT need NodeYaml facade |
| W4 не требует NodeYaml из 038a | ✅ HELD | Reviewed all P4.3-P4.5 files — none import NodeYaml. The plan correctly limits 038a dependency to exceptions.py only |

---

## Section 4 — Test Quality (Phase 4)

### Coverage Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| AC2 verification (no sys.exit in library functions) | LOW | Covered by T2.4 test_negative_sys_exit_in_library (grep-based). Weak — should be a unit test calling functions directly and verifying no SystemExit raised. |
| AC3 verification (no hardcoded loggers) | LOW | Covered by T3.2 (grep-based). Weak — grep verification is not a runtime test. |
| Invariant #5 (manifest format) | HIGH | No test exists to validate that new manifest entries conform to existing schema. DRIFT-MANIFEST-1 would only be caught at CI time. |

### File Existence Paradox

| File | Plan says | Reality | Risk |
|------|----------|---------|------|
| `test_project_registry.py` | NEW (W2 §W2.4) | EXISTS (413 LOC) | Tests will conflict if plan assumes empty file |
| `test_exceptions.py` | дополнение (W4 §W4.9) | MISSING | Cannot augment non-existent file |

### Skip Rate

N/A — runtime validation (Phase 5) not performed. Plan is a design document, not implemented code.

---

## Section 5 — Runtime Validation (Phase 5)

**Skipped.** No code changes have been implemented. This is a pre-implementation semantic audit of the DevPlan. Phase 5 (pytest, LDD traces) is applicable only after code implementation.

---

## Section 6 — Config Sync (Phase 6)

### Makefile Integration Impact

| Target | Current Location | Proposed Location | Issue |
|--------|-----------------|-------------------|-------|
| `check-exception-patterns` | N/A (new) | Root Makefile (per plan §W4.8) | Should be in `makefiles/ci.mk` following existing delegation pattern |
| `gate MODE=fast` integration | `makefiles/ci.mk:115-144` | Not specified in plan | Needs explicit step number and ordering in the 7-step pipeline |
| Integration with `make fix-gate` | `makefiles/repair.mk` | Not addressed | Should `check-exception-patterns` failures be auto-fixable via `fix-gate`? |

### entrypoint-manifest.yaml Registration

| Element | Current Format | Proposed Format | Compatibility |
|---------|---------------|-----------------|---------------|
| New make target | `allowed_verbs:` list (flat, alphabetical) | `verb:` + `type:` + `allowed_in:` | ❌ INCOMPATIBLE |
| Gate registration | `gates:` section with `id`, `repair_*` fields | Not addressed in plan | Missing — gate needs Trinity registration (tests/gates/ + @pytest.mark.gate + manifest) |

### Env Variable Chain

Not applicable — this DevPlan doesn't modify environment variables.

---

## Semantic Verdict

### **DRIFTED (CRITICAL)**

Plan 038b has **4 CRITICAL drift findings** that must be resolved before any implementation begins:

| ID | Finding | Blocks |
|----|---------|--------|
| DRIFT-DEP-1 | `exceptions.py` missing — 038a not completed | W4 entirely |
| DRIFT-GATE-1 | CI gate regex produces false negatives | Gate effectiveness |
| DRIFT-MANIFEST-1 | entrypoint-manifest.yaml syntax invalid | CI gate registration |
| DRIFT-TEST-1 | `test_exceptions.py` missing — can't augment | W4 §T4.7 |

**Partially salvageable:** W2 (sys.exit removal) and W3 (logger names) are independently implementable. They don't depend on 038a, their file paths all exist (with minor line number corrections), and their scope is clear. W2+W3 can proceed as a standalone PR while 038a is being completed.

**Blocked:** W4 (typed exceptions) requires `exceptions.py` from 038a before ANY of its 7 tasks can begin.

### Action Items

1. **[BLOCKER]** Complete DevPlan 038a — create `exceptions.py` with 5 classes and `test_exceptions.py` with base tests
2. **[BLOCKER]** Fix `make check-exception-patterns` regex pattern in §W4.8 (DRIFT-GATE-1)
3. **[BLOCKER]** Fix entrypoint-manifest.yaml registration syntax in §W4.8 (DRIFT-MANIFEST-1)
4. **[HIGH]** Update W3 line numbers: docker_orchestrator.py:99→100, agent_watchdog.py:39→40
5. **[HIGH]** Clarify W2 scope for `test_project_registry.py`: MODIFY existing, not CREATE NEW
6. **[MEDIUM]** Update sys.exit count from 14→12 (§W2.1) to avoid effort mis-estimation
7. **[MEDIUM]** Extract gate logic into `core/entrypoints/check-exception-patterns.sh` (delegation pattern)
8. **[LOW]** Clarify exact step number and location for gate-fast integration in `makefiles/ci.mk`

### W2+W3 Readiness

**W2 and W3 are ready to implement NOW** (no 038a dependency). Required corrections before starting:
- Fix L99→100 for docker_orchestrator.py
- Fix L39→40 for agent_watchdog.py
- Clarify test_project_registry.py scope (MODIFY vs CREATE)
- Update sys.exit count (14→12)

$END_VERIFICATION_REPORT
