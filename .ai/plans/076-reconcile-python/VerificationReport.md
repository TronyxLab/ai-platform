$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Verification of DevPlan 076 — reconcile-projects.sh → Python migration
DESCRIPTION:           Plan self-consistency audit, implementation status check, cross-reference integrity, and design flaw detection
RATIONALE:             Ensure DevPlan is actionable, complete, and free of drift before Coder execution
ACCEPTANCE_CRITERIA:   All referenced files exist, ACs are measurable, prerequisites satisfied, plan self-consistent, no critical design flaws
IMPLEMENTS:            DevPlan:.ai/plans/076-reconcile-python/
IMPACTS:
  - core/internal/reconciler_projects.py (NEW — not created)
  - core/internal/deploy/reconcile-projects.sh (REDUCE — not reduced)
  - core/internal/bootstrap/converge.sh (READ-ONLY — no changes needed, confirmed)
  - tests/unit/test_project_reconciler.py (NEW — not created)
REQUIRES:              None (no external dependencies)
$END_ARTIFACT_CONTRACT

---

# Verification Report: DevPlan 076 — reconcile → Python

**Date:** 2026-07-25
**Authoritative DevPlan:** 02-DevPlan.md (authoritative per R1, highest NN)
**🔒 Verified against SHA:** `d37326afc64e505bb69f230465e83f9f5bef0d8a`
**Git state:** clean (no uncommitted diffs detected at task start)

---

## Final Verdict: **DRIFTED (WARNING)** — исправить перед реализацией

План НЕ имплементирован. Найдены 1 CRITICAL design flaw в спецификации shell-wrapper, 1 отклонение сигнатуры параметров. После исправления — план готов к исполнению Coder-ом.

---

> **STATUS UPDATE 2026-07-31:** SUPERSEDED — implementation committed (см. git log).
> `core/internal/reconciler_projects.py` (552 LOC) реализован, `core/internal/deploy/reconcile-projects.sh`
> сокращён до 48-LOC sourceable фасада (ноль inline `python3 -c` в активном коде). План 091
> верифицировал оркестраторную унификацию (71/71 тестов). Прежний вердикт «DRIFTED (WARNING) — NOT
> STARTED» отражает pre-implementation состояние (2026-07-25). Актуальный статус: DevPlan.md + планы 091/089.

---

## 1. Plan Self-Consistency Audit

### 1.1 File Referencing — All references resolvable

| File referenced in DevPlan | Exists? | LOC | Notes |
|---|---|---|---|
| `core/internal/deploy/reconcile-projects.sh` | ✅ YES | 278 | Original shell — 6 inline python3 calls confirmed |
| `core/internal/bootstrap/converge/reconciler.py` | ✅ YES | 2284 | R3 `reconcile_projects()` at line 545 — different concern (local stub creation, not remote deploy) |
| `core/internal/bootstrap/converge.sh` | ✅ YES | — | Sources `reconcile-projects.sh` at line 115, calls `reconcile_projects()` at line 118 |
| `core/internal/reconciler_projects.py` | ❌ NO | — | T1 NOT YET CREATED |
| `tests/unit/test_project_reconciler.py` | ❌ NO | — | T4 NOT YET CREATED |

### 1.2 DevPlan arefaction check (01-DevPlan.md vs 02-DevPlan.md)

- **01-DevPlan.md** (48 LOC): Stub plan — simpler scope, less detail. Calls the file `reconciler_projects.py` (undescore). Says "~300 LOC, 6 inline python3 calls".
- **02-DevPlan.md** (1044 LOC): Expanded plan — full code spec, exact dataclass definitions, test spec table. Calls the file `reconciler_projects.py` (consistent). Says "~278 LOC". Authors a different module name form but both use `reconciler_projects`.

**Finding:** 01-DevPlan says "~300 LOC" but actual shell is 278 LOC. 02-DevPlan correctly says 278. Minor factual discrepancy in 01 — irrelevant since 02 is authoritative (R1).

### 1.3 Architectural design decision consistency

DevPlan 02 §Architecture Overview (line 65-72) argues for separate module, not merge into `reconciler.py` R3. Three reasons given:

1. **Single Responsibility:** reconciler.py R3 = LOCAL converge (mkdir + stub creation). reconcile-projects.sh = REMOTE deploy (GHCR check + SSH delivery + compose deploy). ✅ **VERIFIED**: R3 at line 545-667 creates stubs locally, no SSH operations.
2. **Call site isolation:** R3 is mandatory in converge. reconcile is optional (--reconcile flag). ✅ **VERIFIED**: converge.sh line 113-123 confirms --reconcile is optional.
3. **Locality:** New module at `core/internal/` (Python layer). ✅ **CONSISTENT** with platform conventions (Python modules live in internal/, not internal/deploy/).

**Verdict:** Design decision sound. Separation is correct.

---

## 2. Implementation Status

### Implementation: **NOT STARTED** (0 of 5 tasks)

| Task | Description | Status | Evidence |
|---|---|---|---|
| T1 | Create `reconciler_projects.py` | ❌ NOT DONE | `glob **/reconciler_projects.py` — no matches |
| T2 | Reduce `reconcile-projects.sh` → <30 LOC | ❌ NOT DONE | Shell still 278 LOC, 6 inline python3 calls present |
| T3 | Verify converge.sh integration | ⬜ READ-ONLY | converge.sh sources correct path — no changes needed |
| T4 | Write unit tests | ❌ NOT DONE | `glob tests/**/test_project_reconciler*.py` — no matches |
| T5 | Run gate | ❌ NOT DONE | Depends on T1-T4 |

### Current shell state (pre-migration):

- **278 LOC** bash script (sourceable, not executable)
- **6 inline python3 calls** confirmed (lines 93, 112, 114, 116, 198, 249)
- `grep -c "python3 -c"` → 6 matches ✅ matches DevPlan count
- Sources: `logging.sh`, `paths.sh`, `docker.sh` (Shell libs — none needed by Python module)
- Direct invocation guard: present (lines 274-278)

---

## 3. Prerequisites Check

| Prerequisite | Status | Evidence |
|---|---|---|
| No external DevPlan dependencies | ✅ SATISFIED | DevPlan says "REQUIRES: None (can run parallel to 070-075)" |
| Source file exists | ✅ SATISFIED | `core/internal/deploy/reconcile-projects.sh` at 278 LOC |
| converge.sh already sources correct path | ✅ SATISFIED | converge.sh:115 → `"${CORE_DIR}/internal/deploy/reconcile-projects.sh"` |
| Python module layer available | ✅ SATISFIED | `core/internal/` exists, no conflicting module at target path |
| Test infrastructure available | ✅ SATISFIED | 64 reconcile-related tests pass (`tests/test_reconcile.py`, `tests/unit/test_reconciler.py`, etc.) |
| PyYAML available in environment | ✅ SATISFIED | Python 3.14.6; PyYAML used throughout codebase |

---

## 4. Cross-Reference Integrity

### 4.1 DevPlan code spec vs original shell — functional parity check

| Shell behavior (original) | Python spec (DevPlan) | Match? |
|---|---|---|
| Parse node.yaml#projects → JSON via python3+yaml | `parse_node_yaml_projects()` → list[ProjectSpec] | ✅ |
| Supports dict + string entries | Supports dict + string entries | ✅ |
| Stub detection via first-line grep GENERATED-STUB | `is_stub_project()` reads first line | ✅ |
| GHCR check via `docker manifest inspect` | `check_ghcr_image()` via subprocess.run | ✅ |
| Org fallback: `tronyx-lab` | Default `"tronyx-lab"` | ✅ |
| SSH host resolution: NODE_HOST_MAP JSON → node.yaml fallback | `resolve_ssh_host()` same order | ✅ |
| Payload delivery: tar + ssh platform-deliver | `deliver_payload()` same mechanism | ✅ |
| Deploy: docker compose pull && up -d via SSH | `deploy_project()` via _ssh_run | ✅ |
| One project failure doesn't abort others | `continue` pattern — same | ✅ |
| Summary: deployed/skipped/warnings/failures | ReconcileSummary dataclass | ✅ |
| Dry-run mode | Parameter propagated through all functions | ✅ |
| Direct invocation guard | Shell wrapper preserves guard | ✅ |

### 4.2 converge.sh integration — no changes needed

Converge.sh line 115-121:
```bash
local reconcile_script="${CORE_DIR}/internal/deploy/reconcile-projects.sh"
if [[ -f "${reconcile_script}" ]]; then
    source "${reconcile_script}"
    reconcile_projects "${CONVERGE_NODE}" "${NODE_YAML_PATH}" "${CONVERGE_DRY_RUN}" || {
        ...
    }
```

**Verified:** After T2, the shell wrapper preserves the same path and `reconcile_projects()` function signature → converge.sh requires zero changes. ✅

---

## 5. Findings

| # | Severity | Finding | File:Line | Recommendation |
|---|----------|---------|-----------|----------------|
| 1 | **CRITICAL** | **`exec python3` в shell wrapper убьёт родительский converge.sh.** Функция `reconcile_projects()` вызывает `exec python3 ...` на строке 921 DevPlan-спецификации. Поскольку converge.sh делает `source reconcile-projects.sh && reconcile_projects`, `exec` заменит процесс converge.sh на python3 — converge.sh никогда не завершит converge после reconcile. | DevPlan 02:921 | Заменить `exec python3` на `python3`. Строка должна быть: `python3 "${core_dir}/internal/reconciler_projects.py" ...` |
| 2 | **WARNING** | **Shell wrapper не передаёт `--node-host-map`.** Python CLI поддерживает `--node-host-map` аргумент, но shell wrapper принимает только 3 позиционных параметра (`node_name`, `node_yaml`, `dry_run`). Оригинальный shell читает `$NODE_HOST_MAP` из глобального окружения (строка 198). Python-модуль принимает это через параметр `node_host_map=""`. Shell wrapper должен либо пробрасывать `$NODE_HOST_MAP`, либо Python должен читать из `os.environ` как fallback. | DevPlan 02:861,914-924 | Добавить в shell wrapper: `local node_host_map="${4:-${NODE_HOST_MAP:-}}"` и пробросить `--node-host-map "${node_host_map}"`. Либо Python `resolve_ssh_host()` должен читать `NODE_HOST_MAP` из `os.environ` как fallback. |
| 3 | **INFO** | **Function return value: shell возвращает код, Python делает `exec`.** Оригинальный shell: `return 0` / `return 1`. Shell wrapper с `exec` (или без) должен пробросить exit code python3 обратно в converge.sh. После исправления Finding #1 (убрать `exec`), нужно добавить `return $?` после python3 вызова. | DevPlan 02:921-925 | Добавить `local rc=$?` + `return $rc` после вызова python3. Без `exec`, return code теряется. |
| 4 | **WARNING** | **`resolve_ssh_host` использует `ssh_user="ci-deploy"` хардкодом в `_ssh_run`, но `deliver_payload` хардкодит `ci-deploy` отдельно.** Два разных способа разрешения SSH user → потенциальный drift при изменении. `_ssh_run` принимает `ssh_user` параметр, но `deploy_project` вызывает его с `"ci-deploy"`. `deliver_payload` строит SSH команду вручную с тем же `ci-deploy`. | DevPlan 02:464,579-598 | Унифицировать: либо всегда использовать `_ssh_run`, либо вынести `ci-deploy` в константу модуля. |
| 5 | **INFO** | **DevPlan 01 говорит "~300 LOC shell", но фактически 278 LOC.** Minor discrepancy, авторитетен 02-DevPlan. | 01-DevPlan:4 | Игнорировать — 01 это stub, 02 авторитетен. |
| 6 | **INFO** | **`NODE_HOST_MAP` в оригинальном shell читается из окружения, не из параметра.** Строка 198: `echo "${NODE_HOST_MAP}" | python3 -c "..."`. Shell wrapper из DevPlan не экспортирует и не читает эту переменную. | Shell:198, DevPlan 02:914-925 | См. Finding #2 — нужно решение для передачи NODE_HOST_MAP. |
| 7 | **INFO** | **Implementation status: 0/5 tasks complete.** Ни один файл не создан/изменён. Все 64 существующих reconcile-тестов проходят (baseline clean). | — | Стартовать Wave 1 (T1) после исправления Finding #1-3. |

---

## 6. Test Baseline

Существующие reconcile-тесты (64 selected, все PASS):

- `tests/gates/test_gate_sequencing.py::test_gate_converge_reconcile_flag` — PASS
- `tests/gates/test_gate_sequencing.py::test_gate_reconcile_not_entrypoint` — PASS
- `tests/test_converge_exit.py::test_reconcile_idempotency` — PASS
- `tests/test_reconcile.py::test_reconcile_already_deployed_skip` — PASS
- `tests/test_reconcile.py::test_reconcile_direct_invocation_guard` — PASS
- `tests/test_reconcile.py::test_reconcile_empty_projects` — PASS
- `tests/unit/test_reconciler.py` — PASS (multiple tests)
- `tests/unit/test_docker_orchestrator.py::test_reconcile_orphan*` — PASS
- `tests/unit/test_orphan_reconciler.py` — 8 tests PASS
- `tests/unit/test_orphan_reconciler_selfheal.py` — PASS

IMPORTANT: Эти тесты проверяют СУЩЕСТВУЮЩИЙ shell и R3 reconciler.py. После миграции нужно верифицировать, что те же тесты проходят с новым Python-модулем ( behaviour не должен измениться).

---

## 7. Acceptance Criteria — Measurability

| AC | Measurable? | Measurement method |
|---|---|---|
| `core/internal/reconciler_projects.py` exists | ✅ | `test -f` / glob |
| `reconcile-projects.sh` <30 LOC | ✅ | `wc -l` |
| Zero inline `python3 -c` calls | ✅ | `grep -c "python3 -c"` → 0 |
| Identical behavior (stub→GHCR→deliver→deploy) | ⚠️ Partially | Functional tests exist for parsing/stub detection; SSH operations require integration tests or VPS |
| `tests/unit/test_project_reconciler.py` 8+ tests | ✅ | `pytest --collect-only` count |
| `make gate MODE=fast` green | ✅ | Exit code 0 |

**Partial concern:** "Identical behavior" AC for SSH operations (deliver_payload, deploy_project) cannot be verified in unit tests — requires either subprocess mocking or integration test on a test VPS. DevPlan acknowledges this (line 979): "Tests for deliver_payload and deploy_project (SSH operations) require either mocking subprocess or are covered by integration tests on a test VPS." This is acceptable — the AC doesn't require SSH integration tests in this DevPlan scope.

---

## Summary

**План корректен по логике, но имеет CRITICAL дефект в спецификации shell wrapper** (`exec` убьёт родительский процесс). После исправления Finding #1 и рекомендаций #2-3 план готов к исполнению.

**Рекомендуемый порядок действий:**
1. Исправить Finding #1 (CRITICAL): заменить `exec python3` на `python3` + `return $?` в спецификации shell wrapper (DevPlan 02:921-925)
2. Исправить Finding #2 (WARNING): добавить проброс `NODE_HOST_MAP` в shell wrapper или Python fallback
3. Делегировать Coder-у для Wave 1 (T1: создать reconciler_projects.py)

$END_VERIFICATION_REPORT
