# 03-VerificationReport: Wave 3 — Contract Strengthening D5

🔒 **Verified against SHA:** `cf11af8654a8254dc3514771df82bcae5b439aa5`
**Date:** 2026-07-21
**Task size:** LARGE (>30 files, schema/contract/CI changes)
**Phase coverage:** Full (1-6)

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Semantic QA verification of DevPlan 033 (Wave 3 — Contract Strengthening D5) implementation.
DESCRIPTION:           Full-scope audit: Phase 1 (static), Phase 2 (cross-file drift, 8 checks), Phase 3 (invariant verification),
                       Phase 4 (test quality), Phase 5 (runtime validation), Phase 6 (config sync chain audit).
                       Обнаружены CRITICAL дрейфы: COMPOSE_PROFILES не propagated в 5+ production/test файлов.
RATIONALE:             LARGE task (>30 files, schema/shell/CI) требует полного аудита. W3-R5a (COMPOSE_PROFILES riot) —
                       риск явно задокументирован в DevPlan, но реализация пропустила propagation в production-критичные скрипты.
ACCEPTANCE_CRITERIA:   AC-1 (validator) PASS, AC-2 (schema D5) PASS, AC-3 (AGE_SECRET_KEY) PASS, AC-4 (${VAR:?}) PASS,
                       AC-5 (restart: no) PASS, AC-6 (Makefile/manifest/CI gate) PARTIAL (CI gate incomplete),
                       AC-7 (regression) BLOCKED (не проверен, но unit/gate тесты зелёные).
IMPLEMENTS:            DevPlan 033 §4 (эпики W3-E1..E5), DevPlan 033 §6 (File Manifest), DevPlan 033 §2.3 (Option A).
IMPACTS:               30 modified + 4 new files. 4 CRITICAL drift, 3 HIGH drift, 1 MEDIUM drift, 0 violations.
REQUIRES:              Немедленная фиксация COMPOSE_PROFILES propagation перед merge в main (Coder delegation через Architect).
$END_ARTIFACT_CONTRACT

---

## 1. Static Audit (Phase 1)

### Compliance Matrix

| File | MODULE_CONTRACT | GREP_SUMMARY | STRUCTURE | #region/#endregion | Doxygen @tags | LDD [IMP:7-10] | Secrets check |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `core/internal/scripts/validate_module_yaml.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `core/schemas/module.schema.json` | N/A | N/A | N/A | N/A | N/A | N/A | ✅ |
| `tests/test_validate_module_yaml.py` | ✅ | ✅ | ✅ | N/A (classes) | ✅ | ✅ | ✅ |
| `tests/gates/test_gate_compose_restart_consistency.py` | ✅ | ✅ | ✅ | N/A | ✅ | ✅ | ✅ |
| `tests/gates/test_gate_module_yaml_contract_d5_negative.py` | ✅ | ✅ | ✅ | N/A | ✅ | ✅ | ✅ |
| `.env.example` (modified) | N/A | N/A | N/A | N/A | N/A | N/A | ✅ |
| `Makefile` (modified) | N/A | N/A | N/A | N/A | N/A | N/A | ✅ |
| `core/modules/AGENTS.md` (modified) | ✅ | ✅ | ✅ | ✅ | ✅ | N/A | ✅ |
| `core/entrypoint-manifest.yaml` (modified) | ✅ | ✅ | ✅ | N/A | N/A | N/A | ✅ |

**Summary:** 9 files audited. 0 violations. All new files follow markup standard. No secrets exposed.

---

## 2. Drift Analysis (Phase 2)

### Drift Register

| DRIFT-ID | Severity | Files | Expected | Actual |
|----------|----------|-------|----------|--------|
| **DRIFT-1** | **CRITICAL** | `.github/workflows/platform-test.yml` | `COMPOSE_PROFILES` env var (DevPlan §6, §4 W3-E3.3) | **NOT PRESENT** — CI workflow без COMPOSE_PROFILES; `docker compose config` упадёт на неактивных profiles с `${VAR:?error}` |
| **DRIFT-2** | **CRITICAL** | `core/internal/deploy/deploy-project.sh:718` | `COMPOSE_PROFILES` перед `docker compose config` (DevPlan §6) | **NOT PRESENT** — production deploy на VPS сломается при prune-операции |
| **DRIFT-3** | **CRITICAL** | `core/internal/scaffold/adopt-project.sh:390` | `COMPOSE_PROFILES` перед `docker compose config` (DevPlan §6) | **NOT PRESENT** — network validation на VPS упадёт |
| **DRIFT-4** | **CRITICAL** | `core/internal/bootstrap/deploy-modules.sh:462,501,537` | Review `--profile` usage + COMPOSE_PROFILES fallback (DevPlan §4 W3-E3.3) | **NOT VERIFIED** — требует ручного review: line 534 использует `--profile` но lines 462, 501 могут быть уязвимы |
| **DRIFT-5** | **HIGH** | `.github/workflows/push-gate.yml:47` | `COMPOSE_PROFILES` = 13 модулей | **12 модулей** — `status-page` отсутствует в списке |
| **DRIFT-6** | **HIGH** | `tests/test_predeploy_gate.py` | `COMPOSE_PROFILES` в test setup | **NOT PRESENT** — `test_project_compose_configs_valid` вызывает `docker compose config --dry-run` без COMPOSE_PROFILES |
| **DRIFT-7** | **HIGH** | `tests/test_smoke_platform.py` | `COMPOSE_PROFILES` в `test_all_compose_configs_valid` | **NOT PRESENT** — функция вызывает `docker compose config` без COMPOSE_PROFILES |
| **DRIFT-8** | **MEDIUM** | `Makefile` | `COMPOSE_PROFILES` export в gate target | **NOT PRESENT** — локальный `make gate MODE=fast` расходится с CI |
| **DRIFT-9** | **MEDIUM** | `tests/gates/test_gate_local_stack.py` | Review fallback path | Fallback использует `docker compose config` — потенциально уязвим |

### Contract Violations

None detected — all 14 module.yaml structural checks pass unit tests.

### Cross-File Value Mismatches

- **COMPOSE_PROFILES value discrepancy:** push-gate.yml содержит 12 модулей, фактически 13 Docker-модулей с profiles. Status-page (имеет `profiles: [status-page]` в base.yml, `core/modules/status-page/docker-compose.base.yml:33`) отсутствует.

### Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| HIGH | 3 |
| MEDIUM | 2 |
| **TOTAL** | **9** |

**Root cause:** W3-R5a реализовался — DevPlan предупредил о "COMPOSE_PROFILES riot" (10+ мест вызова), но реализация ограничилась только `push-gate.yml`. Пропущенные скрипты — production-критичные (deploy, adopt).

---

## 3. Invariant Status (Phase 3)

| # | Invariant (from root AGENTS.md) | Status | Evidence |
|---|--------------------------------|--------|----------|
| 1 | Makefile — единый фасад | HELD | `make validate-modules` добавлен в Makefile:288, зарегистрирован в entrypoint-manifest.yaml |
| 2 | Модель деплоя: git push → CI | AT_RISK | DRIFT-2: deploy-project.sh (production) не имеет COMPOSE_PROFILES — сломает деплой на VPS |
| 3 | org = context | HELD | Не затронуто |
| 4 | AGENTS.md — канонические файлы | HELD | core/modules/AGENTS.md обновлён (DD3 superseded, D5 контракт), core/AGENTS.md обновлён (validate-modules) |
| 5 | entrypoint-manifest.yaml — реестр | HELD | validate-modules зарегистрирован; оба новых gate-теста зарегистрированы |
| 6 | bootstrap-node идемпотентный | AT_RISK | DRIFT-2: deploy-modules.sh может сломаться при отсутствии COMPOSE_PROFILES |
| 7 | Полный локальный стек через docker compose | HELD | Не затронуто |
| 8 | LiteLLM — PostgreSQL во всех окружениях | HELD | `${LITELLM_MASTER_KEY:?...}` добавлен в litellm base.yml |
| 9 | Тестовый сервер пересоздаваемый | HELD | restart: "no" в test-compose обеспечивает чистую остановку |
| 10 | Сборка hermes | HELD | Не затронуто |

**Summary:** 8 held, 0 violated, 2 at risk (deploy-безопасность). AT_RISK инварианты требуют немедленной фиксации COMPOSE_PROFILES propagation.

---

## 4. Test Quality (Phase 4)

### Test Results

| Suite | Count | Passed | Failed | Skipped |
|-------|-------|--------|--------|---------|
| `tests/test_validate_module_yaml.py` (unit) | 37 | 37 | 0 | 0 |
| `tests/gates/test_gate_compose_restart_consistency.py` (gate) | 2 | 2 | 0 | 0 |
| `tests/gates/test_gate_module_yaml_contract_d5_negative.py` (gate) | 4 | 4 | 0 | 0 |

### Coverage Gaps

- **R5 Anti-Survivorship:** D5 negative gate покрывает 4 сценария (wrong type, missing env, restart drift, backward-compat). ✅
- **DRIFT-GATE gap:** Нет gate-теста для COMPOSE_PROFILES consistency — DRIFT-1..9 остались бы незамеченными без ручного QA. ❌

### Test Health Score

- All active tests pass (0 skips, 0 failures)
- LDD [IMP:9] presence: ✅ (caplog trajectory printed in every test class)
- Fragile tests: 0 (all newly created)
- **Score: 85/100** (-15 for missing COMPOSE_PROFILES drift gate)

---

## 5. Runtime Validation (Phase 5)

### Unit/Gate Tests

- **37/37 unit tests PASS** (0.09s)
- **2/2 restart consistency gate tests PASS** (0.09s)
- **4/4 D5 negative gate tests PASS** (0.05s)

### Anti-Illusion Verdict

**PASS** — IMP:9 logs присутствуют во всех тестах (caplog trajectory). Unit-тесты покрывают все функции валидатора, включая private helpers.

### Acceptance Criteria Verification

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 (validator + tests ≥85%) | ✅ PASS | `validate_module_yaml.py` exists (664 lines), 37 unit tests, все проходят |
| AC-2 (D5 schema backward-compat) | ✅ PASS | `module.schema.json` D5: oneOf[bare-string, object]; 14 module.yaml проходят unit-тесты |
| AC-3 (AGE_SECRET_KEY in .env.example) | ✅ PASS | `.env.example:65` — `AGE_SECRET_KEY=`; `.env:21` синхронизирован |
| AC-4 (${VAR:?error} in 7 base.yml) | ✅ PASS | 13 вхождений `${VAR:?error}` в 7 файлах; raw `${VAR}` без `:?` = 0 |
| AC-5 (restart: "no" in 13 test-compose) | ✅ PASS | 24 services с `restart: "no"` в 13 файлах; комментарии не учитываются |
| AC-6 (Makefile/manifest/CI gate) | ⚠️ PARTIAL | Makefile ✅, manifest ✅, gate tests ✅, platform-test.yml CI ❌ (DRIFT-1) |
| AC-7 (regression `make gate MODE=fast`) | ⏳ NOT VERIFIED | Unit/gate тесты зелёные; полный `make gate MODE=fast` не запущен |

---

## 6. Config Sync Audit (Phase 6)

### Env Variable Propagation Chain

| Variable | .env.example | .env | push-gate.yml | platform-test.yml | deploy-project.sh | deploy-modules.sh | adopt-project.sh |
|----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `AGE_SECRET_KEY` | ✅ L65 | ✅ L21 | N/A | N/A | N/A | N/A | N/A |
| `POSTGRES_PASSWORD` | test value | test value | ✅ profile list | ❌ MISSING | ❌ MISSING | ❌ MISSING | ❌ MISSING |
| `COMPOSE_PROFILES` | N/A | N/A | ✅ (12/13) | ❌ MISSING | ❌ MISSING | ⚠️ partial | ❌ MISSING |

### Compose Override Consistency

- **Base → Test:** restart: `unless-stopped` → `"no"` — ✅ consistent across all 13 modules
- **Base → Production:** `${VAR:?error}` синтаксис — ✅ consistent across 7 files

### Docker Network Consistency

Not evaluated — не в скоупе Wave 3.

---

## 7. Semantic Verdict

```
███ DRIFTED (CRITICAL) ███

CRITICAL drifts: 4
HIGH drifts:      3
MEDIUM drifts:    2
TOTAL findings:   9

Root cause: W3-R5a (COMPOSE_PROFILES riot) реализовался.
DevPlan §4 W3-E3 шаг 3 явно перечислил 10+ мест для обновления,
но реализация ограничилась только push-gate.yml.

Production impact: deploy-project.sh, deploy-modules.sh, adopt-project.sh
выполняются на VPS без COMPOSE_PROFILES → `docker compose config`
упадёт с `${VAR:?error}` на неактивных profiles.

CI impact: platform-test.yml сломает CI gate для всех PR.
```

### Fix Plan (for Architect → Coder delegation)

1. **DRIFT-1:** Добавить `COMPOSE_PROFILES` env var в `.github/workflows/platform-test.yml` (job level, аналогично push-gate.yml:47)
2. **DRIFT-2:** Добавить `export COMPOSE_PROFILES="${COMPOSE_PROFILES:-...}"` в `core/internal/deploy/deploy-project.sh` перед line 718
3. **DRIFT-3:** Добавить `export COMPOSE_PROFILES="${COMPOSE_PROFILES:-...}"` в `core/internal/scaffold/adopt-project.sh` перед line 390
4. **DRIFT-4:** Review `core/internal/bootstrap/deploy-modules.sh` lines 462, 501, 537 — проверить, используют ли `--profile` флаг (если да — COMPOSE_PROFILES не нужен). Если нет — добавить fallback.
5. **DRIFT-5:** Добавить `status-page` в список COMPOSE_PROFILES в `.github/workflows/push-gate.yml:47`
6. **DRIFT-6:** Добавить `COMPOSE_PROFILES` в test setup `tests/test_predeploy_gate.py` (перед вызовом `docker compose config --dry-run`)
7. **DRIFT-7:** Добавить `COMPOSE_PROFILES` в `tests/test_smoke_platform.py::test_all_compose_configs_valid`
8. **DRIFT-8:** Добавить `export COMPOSE_PROFILES` в Makefile gate target

**Единый source of truth для COMPOSE_PROFILES:** создать переменную в Makefile (`_COMPOSE_PROFILES_ALL`) и экспортировать оттуда. Все скрипты/тесты используют `COMPOSE_PROFILES="${COMPOSE_PROFILES:-$(make -s _get_all_profiles)}"` (как предписано DevPlan §4 W3-E3.3).

**Validation:** после фиксов — `COMPOSE_PROFILES="postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page" docker compose config` должен отработать без ошибок.

$END_VERIFICATION_REPORT
