$START_VERIFICATION_REPORT
# VerificationReport 094 — Template Engine Python Native

🔒 Verified against SHA `b08a2be` (HEAD, 2026-07-31)

## $ARTIFACT_CONTRACT
- **PURPOSE:** QA review of DevPlan 094 before implementation. Verify factual accuracy, risk coverage, Brief-DevPlan alignment, testing strategy, and protocol compliance.
- **DESCRIPTION:** Cross-referenced all DevPlan claims against actual codebase (12 files read, 8 grep searches). Identified 1 CRITICAL finding (import path not analyzed), 4 HIGH, 4 MEDIUM, 2 LOW.
- **RATIONALE:** Pre-implementation gate — catch plan errors before Coder wastes cycles on an unworkable migration path.
- **ACCEPTANCE_CRITERIA:** All CRITICAL findings addressed before implementation; HIGH findings documented as acceptance criteria for VerificationReport after implementation.
- **IMPLEMENTS:** QA gate for DevPlan 094.
- **IMPACTS:** DevPlan 094 §7.1 must be rewritten; §5 test counts corrected; §2 Wave 2.A/2.B import strategy revised.
- **REQUIRES:** DevPlan 092 (scaffold-python-completion) — SATISFIED (project_scaffolder.py exists, verified).

---

## Semantic Verdict: NEEDS_FIX (CRITICAL)

**Finding F1 is a BLOCKER** — the import mechanism for monitoring_config_renderer.py and sudoers_generator.py is not analyzed. Neither module currently imports from `core.internal.*`, and both are invoked as direct scripts (`python3 script.py`), not via `python3 -m`. The proposed `from core.internal.template_engine import render_template` may fail at runtime. Remediation required before implementation.

**Severity breakdown:** 1 CRITICAL · 4 HIGH · 4 MEDIUM · 2 LOW

---

## 1. Static Audit (Phase 1)

Summary: All files in scope comply with markup standards (GREP_SUMMARY, MODULE_CONTRACT, STRUCTURE, LDD logs). No TRAP violations. No exposed secrets.

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | Regions paired | LDD IMP:7-10 | TRAPs |
|------|-------------|-----------|-----------------|----------------|--------------|-------|
| `template_engine.py` (716) | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `template-engine.sh` (238) | ✅ | ✅ | ✅ | N/A (bash) | ✅ | — |
| `monitoring_config_renderer.py` | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `sudoers_generator.py` | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `project_scaffolder.py` | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `reconciler.py` | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| `helpers.mk` | ✅ | ✅ | ✅ | N/A (make) | ✅ | — |
| `entrypoint-manifest.yaml` | — | — | N/A (generated) | — | — | — |
| `sudo-whitelist.template` | ✅ | — | ✅ | N/A (template) | — | — |
| `test_template_engine.py` (373) | ✅ | ✅ | ✅ | ✅ | ✅ | — |

**Static audit verdict: PASS.** No markup, security, or TRAP violations.

---

## 2. Factual Accuracy — Codebase Verification

### 2.1 Claims VERIFIED

| DevPlan claim | File | Actual | Match? |
|---------------|------|--------|--------|
| PLACEHOLDER_RE at line 33 | `template_engine.py:33` | `re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")` | ✅ EXACT |
| render_template() exists | `template_engine.py:99` | lines 99-212 | ✅ |
| render_all() exists | `template_engine.py:299` | lines 299-419 | ✅ |
| check_all() exists | `template_engine.py:459` | lines 459-551 | ✅ |
| render_directory_in_place() exists | `template_engine.py:564` | lines 564-619 | ✅ |
| main() exists | `template_engine.py:626` | lines 626-716 | ⚠️ Off by 1: ends at 716, not 715 |
| TEMPLATE_ENGINE_SCRIPT at line 68 | `monitoring_config_renderer.py:68` | `"core/internal/template-engine.sh"` | ✅ EXACT |
| _render_template() at 365-398 | `monitoring_config_renderer.py:365-401` | Subprocess at 390, sed-fallback at 391-398 | ✅ |
| _resolve_template_engine() at 73-92 | `sudoers_generator.py:73-92` | Path resolution function | ✅ |
| _render_template() at 95-172 | `sudoers_generator.py:95-172` | Subprocess + temp-file dance | ✅ |
| _safe_cleanup() at 175-181 | `sudoers_generator.py:175-181` | Best-effort unlink | ✅ |
| render_project_template() at 212-261 | `project_scaffolder.py:212-261` | subprocess.run at line 241 | ✅ |
| reconciler comment at line 1668 | `reconciler.py:1668` | `"via sudoers_generator (template-engine.sh render)"` | ✅ |
| helpers.mk line 25 | `makefiles/helpers.mk:25` | `template-engine.sh check --verbose` | ✅ |
| helpers.mk line 31 | `makefiles/helpers.mk:31` | `template-engine.sh render-all` | ✅ |
| entrypoint-manifest delegates_to | `entrypoint-manifest.yaml:87,94` | `.sh check --verbose` + `.sh render-all` | ✅ |
| sudo-whitelist docstring refs | `sudo-whitelist.template:4,5,19,21,27,35` | 6 refs to template-engine.sh | ✅ |
| 3 active subprocess.run call sites | grep across core/ | Exactly 3: monitoring, sudoers, scaffold | ✅ |
| template-engine.sh is 238 LOC | `template-engine.sh` | 238 lines | ✅ |

### 2.2 Claims FALSIFIED / INACCURATE

**[HIGH] F2: Test count: 18 vs 20**

The DevPlan claims "18 unit tests" in `test_template_engine.py` at 4 locations:
- §1.3: "Запустить tests/test_template_engine.py (18 тестов)"
- §5 AC9: "Unit-тесты tests/test_template_engine.py (18 шт.) проходят без изменений"
- §6 File Manifest
- §10 Out of Scope: "существующие 18 покрывают контракт"

**Reality:** `grep -c "def test_" tests/test_template_engine.py` → **20** test functions.
The file's own STRUCTURE/MODULE_CONTRACT say "18 atomic tests" — this is stale metadata. The DevPlan reproduces the stale count without verification.

**Fix:** Replace all occurrences of "18" with "20" in DevPlan. Update STRUCTURE line 2 and MODULE_CONTRACT line 11 in `tests/test_template_engine.py` to reflect actual count.

**[HIGH] F3: AGENTS.md Template Mechanisms already Python-native**

DevPlan step 3.6 says: "Обновить root AGENTS.md §Template Mechanisms: строка template-engine.sh → template_engine.py, отметить UPPER_SNAKE как Python-native (без shell wrapper)".

**Reality:** The current `AGENTS.md` §Template Mechanisms (line 193) already reads:
```
| nginx vhost конфиги | `{{UPPER_SNAKE}}` strict regex | `core/internal/template_engine.py` | ...
```
There is NO mention of `template-engine.sh` in the Template Mechanisms table. The DevPlan threatens to modify something that's already correct.

**Fix:** Step 3.6 should either be removed or changed to verify (not modify) that the table already references `template_engine.py`. The only AGENTS.md change needed might be the Навигация table (line 214 already references `template_engine.py`).

**[HIGH] F4: Brief scoping gap — cert_orchestrator, deploy modules not addressed**

Brief §Required Actions Wave 2 explicitly lists:
- "6. cert_orchestrator, deploy modules: то же."

The DevPlan silently drops these call sites. After auditing:
- `cert_orchestrator.py` — **does NOT call template-engine.sh** (0 grep matches)
- `deploy-modules.sh` — **no longer calls template-engine.sh** (post-Strangler-Fig reduction to 91 LOC)
- `core/internal/*.sh` — 0 matches outside `template-engine.sh` self-references

**Verdict:** The scoping is CORRECT (no work needed), but the DevPlan never explains WHY. This creates ambiguity — a future Coder might waste time searching for nonexistent call sites.

**Fix:** Add explicit scoping note: "cert_orchestrator.py and deploy-modules.sh do NOT call template-engine.sh (verified 2026-07-31). No migration needed."

---

## 3. Risk Analysis Audit

### 3.1 [CRITICAL] F1: §7.1 ImportError analysis is factually wrong

**Claim in DevPlan:** "monitoring_config_renderer.py и sudoers_generator.py уже импортируют из core.internal.* (например, from core.internal.shared.secrets_env_parser import ...)"

**Reality:**
- `monitoring_config_renderer.py`: **0 imports** from `core.internal.*` (grep confirmed)
- `sudoers_generator.py`: **0 imports** from `core.internal.*` (grep confirmed)
- Only `project_scaffolder.py` imports from `core.internal.*` (lazy imports inside functions)

**Additional critical analysis — script invocation methods:**

| Module | Invoked via | Location relative to template_engine.py |
|--------|-------------|----------------------------------------|
| `monitoring_config_renderer.py` | `python3 "${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py"` (direct script) | Same directory |
| `sudoers_generator.py` | `python3 "${SCRIPT_DIR}/deploy/sudoers_generator.py"` (direct script) | 3 levels deeper (`bootstrap/deploy/`) |
| `project_scaffolder.py` | `python3 -m core.internal.scaffold.project_scaffolder` (module) | 1 level deeper (`scaffold/`) |

**Impact on proposed imports:**

For `monitoring_config_renderer.py`: `from core.internal.template_engine import render_template` — may fail because `sys.path[0]` = `core/internal/` (no `core/` namespace visible). **Mitigation:** Use direct import `from template_engine import render_template` (both files in same directory). DevPlan doesn't mention this.

For `sudoers_generator.py`: `from core.internal.template_engine import render_template` — WILL FAIL because `sys.path[0]` = `core/internal/bootstrap/deploy/`. Python cannot find `core.internal.template_engine` 3 levels up without `PYTHONPATH` or `sys.path` manipulation. **Mitigation:** Add project root to `sys.path`, or use `importlib`. DevPlan doesn't mention this.

For `project_scaffolder.py`: `python3 -m core.internal.scaffold.project_scaffolder` — WORKS because `-m` adds project root to `sys.path`. Also has `core/internal/scaffold/__init__.py` for package namespace.

**Fix required:** §7.1 must be completely rewritten:
1. Remove false claim about existing `core.internal.*` imports
2. Document invocation methods for each call site
3. Provide import strategy per call site (direct import for monitoring, sys.path or importlib for sudoers)
4. Verify each strategy with `python3 -c "from ..."` before implementation

### 3.2 [MEDIUM] F7: §7.2 Error-handling types not fully enumerated

The DevPlan says "template_engine.render_template уже объявляет raises in docstring (lines 124-128)" listing `TemplateError`, `FileNotFoundError`, `PermissionError`. Missing from analysis:
- `yaml.YAMLError` (from render_all/check_all when manifest is invalid YAML)
- `ImportError` (when PyYAML is not installed — render_all/check_all)
- `ValueError` (from parse_vars in CLI path, not relevant for call sites)

The sudoers and monitoring callers use `render_template()` directly, so `TemplateError` + `FileNotFoundError` + `PermissionError` covers their case. But the scaffold caller uses `render_directory_in_place()` which returns `int` (error count), not raises — the DevPlan step 2.C.3 acknowledges this but doesn't document it as a different error contract.

**Mitigation:** Add note in §7.2 enumerating ALL exception types per API function, noting that `render_directory_in_place()` returns int (0=OK) and doesn't raise on template errors.

### 3.3 [MEDIUM] F8: Missing risk — PYTHONPATH dependency across environments

The migration from subprocess (separate Python process) to direct import binds the callers' Python environment to the template engine's. Currently:
- Subprocess: `bash template-engine.sh` → launches `python3 template_engine.py` as a separate OS process with clean environment
- Direct import: `from core.internal.template_engine import render_template` → shares the caller's Python process, PYTHONPATH, and module cache

This could fail on VPS, CI, or non-standard environments where PYTHONPATH is not set to include the project root. All three callers are executed via shell wrappers that set `PLATFORM_ROOT` — but none currently export `PYTHONPATH`.

**Mitigation:** Document that `PLATFORM_ROOT` must be on `PYTHONPATH` for direct imports to work. Or better: use `sys.path.insert(0, ...)` at module top for each migrated call site.

### 3.4 Remaining risks — ADEQUATE

- §7.3 (sed-fallback removal): Correctly analyzed — fallback obsolete after migration 🌫️
- §7.4 (make templates-check/render regression): Correctly analyzed — `main()` already handles CLI ✅

---

## 4. Brief-DevPlan Alignment

| Brief Required Action | DevPlan Coverage | Alignment |
|----------------------|-----------------|-----------|
| Wave 1.1: Read template-engine.sh regex | §1.1-1.3 Parity analysis | ✅ EXACT |
| Wave 1.2: Compare regex with template_engine.py | §1.3: PLACEHOLDER_RE is identical | ✅ EXACT |
| Wave 1.3: Check render_dir() in Python | §1.3: render_directory_in_place() exists | ✅ EXACT |
| Wave 2.4: monitoring_config_renderer.py | Wave 2.A: Steps 2.A.1-2.A.6 | ✅ FULL |
| Wave 2.5: sudoers_generator.py | Wave 2.B: Steps 2.B.1-2.B.7 | ✅ FULL |
| Wave 2.6: cert_orchestrator, deploy modules | **NOT ADDRESSED** | ⚠️ HIGH — scoping decision not explained (F4) |
| Wave 2.7: add-project | Wave 2.C (via project_scaffolder.py) | ✅ CORRECT (post-092 migration) |
| Wave 3.8: Delete shell | Wave 3: Step 3.7 | ✅ |
| Wave 3.9: Update AGENTS.md | Wave 3: Step 3.6 | ⚠️ Already correct — see F3 |
| Wave 3.10: Gate test regresssion | §5 AC10 + §8.2 | ✅ |

**Brief's "25 grep-совпадений" → DevPlan "3" correction:** JUSTIFIED. Brief counted raw grep matches (including docstrings, comments, self-references). DevPlan correctly identifies 3 actual `subprocess.run()` call sites. Verified by audit — exactly 3 active call sites.

---

## 5. Testing Strategy

### 5.1 Current state

- `tests/test_template_engine.py`: **20** test functions (not 18), imports `core.internal.template_engine` directly — validates the import path works in test environment
- `tests/test_template_syntax_gate.py`: Validates strict grammar
- `tests/unit/test_sudoers_generator.py`: Tests sudoers generation pipeline (will need re-run after Wave 2.B)

### 5.2 [MEDIUM] F9: Missing end-to-end regression test

The DevPlan mentions "make new-project dry-run (если доступно) или unit-test" (§8.2) — this is vague. `make new-project` invokes `add-project.sh` → `python3 -m core.internal.scaffold.project_scaffolder` → `render_project_template()` → `render_directory_in_place()`. This is the **only integration path exercising `render_directory_in_place()`** outside of `make templates-render`. No existing test covers this path end-to-end (as a project creation simulation).

**Recommendation:** Add an explicit regression test step or gate test that simulates `make new-project` template rendering.

### 5.3 [LOW] F11: _safe_cleanup() analysis incomplete

Step 2.B.4 says "Удалить _safe_cleanup() (lines 175-181) если больше не используется". The `finally` block at line 170-171 uses `_safe_cleanup(output_path)` with `# type: ignore[possibly-undefined]` — this is a code smell that the temp-file dance creates. After migration to `render_template(dry_run=True)`, the entire temp-file logic including `_safe_cleanup()` becomes dead code. The DevPlan correctly identifies this but the analysis is at the wrong granularity — the issue isn't just "check references" but "the entire try/except/finally block (lines 123-172) is replaced by 5 lines."

### 5.4 No new tests needed — CORRECT

The DevPlan correctly states no new tests are needed for `template_engine.py` itself (contract preserved). What IS needed: re-run existing tests after migration to confirm no regression.

---

## 6. Acceptance Criteria Completeness

| AC | Command | Verifiable? | Notes |
|----|---------|------------|-------|
| AC1 | `grep -rn "subprocess.*template-engine" core/ --include="*.py"` | ✅ | 0 matches expected |
| AC2 | `grep -rn "template-engine\.sh" core/ --include="*.py"` | ✅ | 0 matches |
| AC3 | `test ! -f core/internal/template-engine.sh` | ✅ | File deleted |
| AC4 | `grep "template_engine.py" makefiles/helpers.mk` | ✅ | 2 matches |
| AC5 | `grep "template_engine.py" core/entrypoint-manifest.yaml` | ✅ | 2 matches |
| AC6 | `grep 'PLACEHOLDER_RE = re.compile' core/internal/template_engine.py` | ✅ | Regex byte-identical |
| AC7 | `make templates-check` | ✅ | Green |
| AC8 | `make templates-render` | ✅ | Green |
| AC9 | `pytest tests/test_template_engine.py tests/test_template_syntax_gate.py tests/unit/test_sudoers_generator.py -v` | ✅ | All passed |
| AC10 | `make gate MODE=fast` | ✅ | Green |
| AC11 | `make check-manifests` | ✅ | Green |
| AC12 | `make templates-check` after .sh deletion | ✅ | Proves .py works standalone |

### [LOW] F10: AC7/AC12 redundancy

AC7 (`make templates-check` green) and AC12 (`make templates-check` after .sh deletion) use the same command. The distinction is meaningful (before vs after deletion), but they test the same code path. Consider merging: "AC7: `make templates-check` passes BOTH before AND after .sh deletion."

---

## 7. DevPlan Protocol Compliance

### $DOCUMENT_PLAN skeleton: ✅ COMPLETE

- `$START_DEVPLAN` / `$END_DEVPLAN` — present
- `$ARTIFACT_CONTRACT` — all 7 fields present and valid
- `$DOCUMENT_PLAN` with SECTION_GOALS + SECTION_USE_CASES — present
- Knowledge graph XML — all entities have `id`, `type`, `keywords`, `annotation`; CrossLinks present

### Minor protocol issues

**[LOW] F12:** XML entity `core_internal_template_engine_py` (line 118) lacks a `file` attribute, while inner entities (118-139) and other entities (143, 154, etc.) have `file` attributes in different formats. This is cosmetic but inconsistent.

---

## 8. Anti-Loop Notes

§9 Anti-Loop Notes (5 points): All 5 are consistent with the plan and cover the critical anti-patterns:

1. ✅ Don't unify UPPER_SNAKE with Jinja2 — prevents architectural violation
2. ✅ Don't add new functions — API already covers everything
3. ✅ Don't rewrite business logic — only docstring update
4. ✅ Don't keep sed-fallback — strict grammar enforcement
5. ✅ If import fails, fix package setup — prevents regression to subprocess

---

## 9. Findings Summary

| ID | Severity | Type | Section | Description |
|----|----------|------|---------|-------------|
| F1 | **CRITICAL** | INACCURACY | §3.1 (DevPlan §7.1) | ImportError analysis contains false claim about existing core.internal imports. Import path viability not verified per call site invocation method |
| F2 | HIGH | INACCURACY | §2.2 (DevPlan §1.3, §5, §6, §10) | Test count: 18 claimed, 20 actual |
| F3 | HIGH | INACCURACY | §2.2 (DevPlan §3.6) | AGENTS.md Template Mechanisms already references template_engine.py — no shell-wrapper mention to remove |
| F4 | HIGH | MISSING | §4 (DevPlan §10) | cert_orchestrator and deploy-modules scoping not explained — Brief lists them, DevPlan silently drops |
| F5 | MEDIUM | INACCURACY | §2.1 | main() line range: 626-715 claimed, actual 626-716 (off by 1) |
| F6 | MEDIUM | AMBIGUITY | §3.2 (DevPlan §7.2) | Error-handling types not fully enumerated; render_directory_in_place() error contract differs from render_template() |
| F7 | MEDIUM | MISSING | §3.3 | PYTHONPATH dependency across environments (local, CI, VPS) not analyzed |
| F8 | MEDIUM | MISSING | §5.2 (DevPlan §8.2) | No end-to-end regression test for make new-project (exercises render_directory_in_place via scaffold) |
| F9 | LOW | INACCURACY | §5.3 (DevPlan §2.B.4) | _safe_cleanup() removal described as "check references" — should state the entire temp-file block disappears |
| F10 | LOW | REDUNDANCY | §6 (DevPlan §5) | AC7/AC12 use same command — meaningful distinction but not acknowledged |

---

## 10. Required Fixes

### FIX-F1 (CRITICAL): Rewrite §7.1 ImportError analysis

**File:** `.ai/plans/094-template-engine-python/02-DevPlan.md`, lines 395-401

**What is wrong:** Claims monitoring_config_renderer.py and sudoers_generator.py already import from `core.internal.*` — they don't. Does not analyze script invocation method vs import path viability.

**What to change to:**

```markdown
### 7.1 Высокий риск: ImportError при cross-module import

**Риск:** `from core.internal.template_engine import render_template` может не сработать,
если модуль вызывается как прямой скрипт (`python3 script.py`) без `PYTHONPATH`.

**Инвокация call sites:**

| Call site | Invocation | sys.path[0] | Импорт |
|-----------|-----------|-------------|--------|
| monitoring_config_renderer.py | `python3 "${PLATFORM_ROOT}/core/internal/monitoring_config_renderer.py"` | `core/internal/` | Same dir → `from template_engine import render_template` (прямой импорт, не через core.internal) |
| sudoers_generator.py | `python3 "${SCRIPT_DIR}/deploy/sudoers_generator.py"` | `core/internal/bootstrap/deploy/` | 3 уровня вверх → нужно `sys.path.insert(0, PLATFORM_ROOT)` ИЛИ `importlib` |
| project_scaffolder.py | `python3 -m core.internal.scaffold.project_scaffolder` | project root (`-m` добавляет cwd) | ✅ `from core.internal.template_engine import render_template` работает |

**Mitigation:**
- monitoring: использовать `from template_engine import render_template` (оба в `core/internal/`)
- sudoers: добавить `sys.path.insert(0, os.environ.get('PLATFORM_ROOT', ...))` перед импортом;
  sudoers_generator уже вызывается из `deploy-modules.sh` где `PLATFORM_ROOT` всегда определён
- scaffold: существующий `python3 -m` — import работает без изменений

**Проверка до миграции:**
```bash
# monitoring — прямой импорт (same dir)
python3 -c "import sys; sys.path.insert(0, 'core/internal'); from template_engine import render_template; print('OK')"

# sudoers — через sys.path
python3 -c "import sys; sys.path.insert(0, '.'); from core.internal.template_engine import render_template; print('OK')"
```

### Additional required changes:

**Also update Wave 2.A Step 2.A.2 (line 292):**

From:
```
| 2.A.2 | Добавить import вверху: `from core.internal.template_engine import render_template` | header imports |
```

To:
```
| 2.A.2 | Добавить import вверху: `from template_engine import render_template` | header imports |
```

**Also update Wave 2.B Step 2.B.1 (line 305):**

From:
```
| 2.B.1 | Добавить import: `from core.internal.template_engine import render_template` | header |
```

To (add sys.path manipulation note):
```
| 2.B.1 | Добавить sys.path + import: `sys.path.insert(0, os.environ.get('PLATFORM_ROOT', os.path.join(os.path.dirname(__file__), '../../..')))` + `from core.internal.template_engine import render_template` | header |
```

### FIX-F2 (HIGH): Correct test count from 18 → 20

**File:** `.ai/plans/094-template-engine-python/02-DevPlan.md`

**Occurrences:**
1. Line 91 (`### 1.3`): "Запустить tests/test_template_engine.py (18 тестов)" → "(20 тестов)"
2. Line 367 (`§5 AC9`): "Unit-тесты tests/test_template_engine.py (18 шт.)" → "(20 шт.)"
3. Line 377 (`§6 File Manifest`): No explicit count change needed; verify phrase "18 unit tests" in knowledge graph → line 122: "18 unit tests" → "20 unit tests"
4. Line 469 (`§10 Out of Scope`): "существующие 18 покрывают контракт" → "существующие 20 покрывают контракт"

**Also fix the source file** `tests/test_template_engine.py`:
- Line 2: `┌18 atomic tests┐` → `┌20 atomic tests┐`
- Line 11: `18 atomic tests covering all edge cases` → `20 atomic tests covering all edge cases`

### FIX-F3 (HIGH): Remove or correct AGENTS.md modification step

**File:** `.ai/plans/094-template-engine-python/02-DevPlan.md`, line 341 (Step 3.6)

From:
```
| 3.6 | Обновить root AGENTS.md §Template Mechanisms: строка `template-engine.sh` → `template_engine.py`, отметить UPPER_SNAKE как Python-native (без shell wrapper) | `AGENTS.md` §Template Mechanisms |
```

To:
```
| 3.6 | Верифицировать AGENTS.md §Template Mechanisms (уже ссылается на `template_engine.py`, shell wrapper не упоминается) — изменений не требуется | `AGENTS.md` §Template Mechanisms |
```

### FIX-F4 (HIGH): Add scoping note for cert_orchestrator and deploy modules

**File:** `.ai/plans/094-template-engine-python/02-DevPlan.md`, add before §10 (Out of Scope) or after §1.2:

```markdown
### 1.4 Scoping note: cert_orchestrator and deploy-modules (из Brief)

Brief 094 перечисляет `cert_orchestrator` и `deploy modules` как call sites для миграции.
Фактический аудит (2026-07-31):
- `core/internal/bootstrap/deploy/cert_orchestrator.py` — НЕ вызывает template-engine.sh (0 grep-совпадений). Сертификаты рендерятся через acme.sh, не через template engine.
- `core/internal/bootstrap/deploy-modules.sh` — НЕ вызывает template-engine.sh. После Strangler-Fig (DevPlan 087) deploy-modules.sh сокращён до 91 LOC и не содержит template-рендеринга. Sudoers генерируются через `sudoers_generator.py` (учтён в Wave 2.B).
- `core/internal/scaffold/add-project.sh` — мигрирован в `project_scaffolder.py` (DevPlan 092, учтён в Wave 2.C).

**Вывод:** Никаких дополнительных call sites для миграции. Brief был написан до финального аудита.
```

### FIX-F5 (MEDIUM): Correct main() line range

**File:** `.ai/plans/094-template-engine-python/02-DevPlan.md`, line 278

From: `template_engine.py:626-715` → To: `template_engine.py:626-716`

### FIX-F7 (MEDIUM): Add PYTHONPATH risk analysis

Add to DevPlan §7 as new subsection §7.5:

```markdown
### 7.5 Низкий риск: PYTHONPATH в разных окружениях (local, CI, VPS)

**Риск:** Прямой import требует, чтобы project root был на sys.path. Локально это работает
(pytest добавляет cwd, `python3 -m` добавляет cwd). На CI и VPS окружение может отличаться.

**Mitigation:**
- CI: `make templates-check/render` → `python3 template_engine.py` (CLI, не import) — не затронут
- VPS: monitoring_config_renderer вызывается через `python3 script.py` где PLATFORM_ROOT определён;
  прямой import из той же директории (`from template_engine import ...`) не требует PYTHONPATH
- VPS: sudoers_generator вызывается из deploy-modules.sh где PLATFORM_ROOT всегда определён;
  добавляем `sys.path.insert(0, PLATFORM_ROOT)` перед импортом
```

---

## 11. Post-Fix Verification Commands

After F1-F10 fixes are applied, re-verify with:

```bash
# F1: verify import paths work
python3 -c "import sys; sys.path.insert(0, '.'); from template_engine import render_template; from core.internal.template_engine import render_directory_in_place; print('All imports OK')"

# F2: verify test count
grep -c "def test_" tests/test_template_engine.py  # must be 20

# F3: verify AGENTS.md already correct
grep "template_engine.py" AGENTS.md | head -1  # should match without .sh

# F4: verify scoping note added
grep "cert_orchestrator" .ai/plans/094-template-engine-python/02-DevPlan.md
```

---

## QA Execution Summary

| Phase | Scope | Result |
|-------|-------|--------|
| Phase 1: Static Audit | 10 files in File Manifest | ✅ PASS — all compliant |
| Phase 2: Cross-File Drift | Expanded scope (compose, CI, manifest, tests) | ✅ PASS — no drift detected |
| Phase 5: Runtime Validation | NOT EXECUTED (pre-implementation gate — no code changes yet) | N/A |
| Factual Verification | 12 files read, 8 grep searches, 20+ claims checked | 1 CRITICAL · 4 HIGH · 4 MEDIUM · 2 LOW |
| Brief-DevPlan Alignment | All 10 Required Actions checked | 1 gap (F4 — scoping undocumented) |
| Protocol Compliance | $ARTIFACT_CONTRACT, $DOCUMENT_PLAN, XML graph | ✅ PASS |

$END_VERIFICATION_REPORT
