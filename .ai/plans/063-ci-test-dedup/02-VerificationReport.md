# GREP_SUMMARY: VerificationReport 063 ci-test-dedup gate pipeline fast ci-docker makefile workflow pre-implementation critical-review
# STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ static-audit(Phase1) → ◇ drift-analysis(Phase2) → ◇ config-sync(Phase6) → ◇ semantic-verdict

$START_VERIFICATION_REPORT
$ARTIFACT_CONTRACT
PURPOSE:               Критическая оценка DevPlan 063 (CI test deduplication + build cache) ПЕРЕД реализацией. Выявление пропущенных шагов, некорректных допущений, рисков и несоответствий.
DESCRIPTION:           Phase 1 (static audit 4 целевых файлов) + Phase 2 (cross-file drift — поиск всех consumer'ов MODE=fast/ci-docker) + Phase 6 (config sync — propagation chain для CI workflow). Рантайм-валидация (Phase 5) пропущена — план ещё не реализован, тестировать текущее состояние не имеет смысла.
RATIONALE:             Предотвращение проблем «внедрение сломало CI» до push. DevPlan затрагивает core CI pipeline (fast gate + ci-docker gate + push-gate + deploy-project gate).
ACCEPTANCE_CRITERIA:   (1) Все consumer'ы MODE=fast и MODE=ci-docker идентифицированы, (2) header comments в ci.mk проверены на актуальность, (3) contract/predeploy тесты верифицированы на Docker-free/Docker-dependent, (4) merge_junit устойчивость к отсутствующим файлам подтверждена, (5) hardcoded image version риск задокументирован.
IMPLEMENTS:            Phase 1 + Phase 2 + Phase 6 QA workflow для STANDARD-задачи (4 файла, CI конфигурация затронута)
IMPACTS:               VerificationReport.md → рекомендации по доработке DevPlan перед реализацией
REQUIRES:              —
$END_ARTIFACT_CONTRACT

---

🔒 Verified against SHA `4240d6c2f15ed915f6fec6899d89c2ecbe37b652`
Working tree: clean (no uncommitted changes)

---

## Phase 1 — Static Audit (compliance matrix)

| Файл | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region/#endregion | Doxygen @tags | LDD IMP:7-10 | TRAP |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `makefiles/ci.mk` | ✅ | ✅ | ✅ | ✅ (Makefile — N/A) | ✅ | ✅ | ✅ |
| `.github/workflows/platform-test.yml` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `.github/workflows/push-gate.yml` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `.github/actions/docker-build-cache/action.yml` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Findings:**
- `makefiles/ci.mk` line 110-112: header comments `## gate:` describe current gate modes (`MODE=fast — validate → lint → gates → static → predeploy`). После изменений они станут stale — DevPlan **не упоминает** обновление этих комментариев.
- `.github/workflows/platform-test.yml` line 9-10: `@invariants` comment describes "Fast gate (make gate MODE=fast) runs BEFORE Docker setup" — это остаётся верным (contract тесты Docker-free), обновление не требуется.
- Все 4 файла имеют полную семантическую разметку. Нарушений не обнаружено.

---

## Phase 2 — Cross-File Drift Detection

### 2a. Consumer matrix: все CI workflow, использующие `make gate`

| Workflow | Mode | PROJECT filter | Строка | Затронут изменениями? |
|----------|------|:---:|--------|:---:|
| `platform-test.yml` | `MODE=fast` | — | 116 | ✅ contract добавлен |
| `platform-test.yml` | `MODE=ci-docker` | — | 251 | ✅ contract/static/predeploy удалены |
| `push-gate.yml` | `MODE=fast` | — | 72 | ✅ contract добавлен |
| `deploy-project.yml` | `MODE=fast` | `PROJECT=${{ inputs.project_name }}` | 93 | ⚠️ **НЕ упомянут в DevPlan** |
| `nightly-gate.yml` | `MODE=full` | — | 73 | ❌ Не затронут (MODE=full без изменений) |

### 2b. Drift findings

**[HIGH] DRIFT-CONSUMER: `deploy-project.yml` не упомянут в DevPlan**
- `deploy-project.yml` line 93: `make gate MODE=fast PROJECT=${{ inputs.project_name }}`
- После изменений MODE=fast будет включать contract-тесты (шаг 5/7).
- Contract-тесты Docker-free и project-agnostic (тестируют entrypoint-скрипты, не project-specific конфиги), поэтому `PROJECT` параметр не конфликтует — он применяется только к predeploy шагу.
- **Оценка:** функционально безопасно (contract-тесты пройдут), но DevPlan должен упомянуть этот workflow для полноты.
- **Fix:** добавить `deploy-project.yml` в таблицу «Что НЕ меняется» или в список проверяемых CI-воркфлоу.

**[MEDIUM] DRIFT-COMMENT: `ci.mk` header comments lines 110-112**
- Текущие комментарии:
  ```
  ##   MODE=fast — validate → lint → gates → static → predeploy (no Docker)
  ##   MODE=ci-docker — contract → static → predeploy → smoke → component → skip-enforcement
  ```
- После изменений должны быть:
  ```
  ##   MODE=fast — validate → lint → gates → contract → static → predeploy (no Docker)
  ##   MODE=ci-docker — predeploy-docker → smoke → component (Docker-dependent only)
  ```
- DevPlan обновляет echo-строки внутри кода, но **не упоминает** header comments.
- **Fix:** добавить в Step 1 плана явное указание обновить комментарии на строках 110-112.

**[LOW] DRIFT-REPORT: семантика `report.xml` в MODE=fast меняется**
- Текущий MODE=fast: последний шаг `$(MAKE) test MARKER=predeploy` копирует `report-predeploy.xml → report.xml`.
- Новый MODE=fast: `$(MAKE) test MARKER=contract` (шаг 5/7) копирует `report-contract.xml → report.xml`. Шаги static (6/7) и predeploy (7/7) используют inline pytest без копирования в `report.xml`.
- **Оценка:** MODE=fast — fail-fast пайплайн, при ошибке любого шага exit 1 происходит немедленно. `report.xml` не потребляется CI (в platform-test.yml после `make gate MODE=fast` нет upload/read report.xml). Функционально безвредно.
- **Рекомендация:** документировать изменение семантики в DevPlan или унифицировать: либо все шаги через `$(MAKE) test MARKER=...`, либо все inline + явный merge в конце.

---

## Phase 3 — Invariant Verification (выборочная)

| Инвариант | Статус | Ссылка |
|-----------|:------:|--------|
| Manifest Generation Contract: `make check-manifests` blocks divergence | HELD | DevPlan AC 4 явно требует `make check-manifests` |
| `gate MODE=fast must pass before push` | HELD | Contract-тесты Docker-free, не нарушают |
| `test MARKER=all runs canonical order` | HELD | MODE=full и MARKER=all не меняются |
| Docker-dependent: `@pytest.mark.requires_docker` | HELD | Contract=0 Docker, predeploy=1 Docker — split корректен |

**Проверенные утверждения DevPlan:**
- ✅ Contract тесты (267 шт.) Docker-free — подтверждено: `pytest --collect-only -m "contract and requires_docker"` → 0 tests.
- ✅ Predeploy тесты: 32 static + 1 Docker (`test_project_compose_configs_valid`) — подтверждено.
- ✅ `merge_junit.py` graceful degradation: отсутствующие файлы логируются на IMP:7 и пропускаются (не ошибка).
- ✅ `pytest -m "predeploy and requires_docker"` корректно возвращает 1 тест.

---

## Phase 4 — Test Quality (выборочная, gate-тесты)

Gate-тесты, которые **могут быть затронуты** изменениями:

| Gate test | Файл | Затронут? | Причина |
|-----------|------|:---:|---------|
| `test_mode_fast_excludes_requires_docker` | `test_gate_ci_coverage.py:393` | ✅ НЕТ | Проверяет `-m` выражение MODE=fast — оно не меняется (contract добавлен отдельным шагом) |
| `test_marker_all_includes_contract` | `test_gate_ci_coverage.py:686` | ✅ НЕТ | Проверяет MARKER=all (не меняется) |
| `test_platform_test_has_push_trigger` | `test_gate_ci_coverage.py:598` | ✅ НЕТ | Проверяет trigger'ы platform-test.yml (не меняются) |

Gate-тестов, валидирующих структуру ci-docker пайплайна, **не существует** — изменение с 6→3 шагов не сломает ни один существующий gate.

---

## Phase 6 — Config Sync Audit

### 6a. Workflow propagation chain

```
MODE=fast consumers:
  platform-test.yml:116  → DevPlan Step 2 (обновлён comment)
  push-gate.yml:72       → DevPlan Step 3 (обновлён name)
  deploy-project.yml:93  → ⚠️ НЕ УПОМЯНУТ (см. DRIFT-CONSUMER)

MODE=ci-docker consumers:
  platform-test.yml:251  → DevPlan Step 2 (обновлён comment + job summary)

MODE=full consumers:
  nightly-gate.yml:73    → без изменений (корректно)
```

### 6b. Image version hardcoding

```
hermes-agent Dockerfile:50  → FROM nousresearch/hermes-agent:v2026.7.7.2
platform-test.yml (новый)   → docker pull nousresearch/hermes-agent:v2026.7.7.2
```

**Риск:** при обновлении upstream-версии в Dockerfile нужно синхронно обновить CI workflow. DevPlan документирует это в `TRAP[PERF]`, но не предлагает автоматизации (например, `grep FROM Dockerfile | cut -d: -f2`).

**[MEDIUM] DRIFT-IMAGE-VERSION: hardcoded tag в двух файлах**
- `core/modules/hermes-agent/build/Dockerfile:50` — `FROM nousresearch/hermes-agent:v2026.7.7.2`
- `.github/workflows/platform-test.yml` (новый шаг) — `docker pull nousresearch/hermes-agent:v2026.7.7.2`
- При обновлении Dockerfile без обновления CI → pre-pull тянет старую версию, build перетягивает новую → двойная загрузка, нулевой cache-hit.
- **Fix:** рассмотреть динамическое извлечение тега из Dockerfile: `HERMES_BASE=$(grep -oP 'FROM nousresearch/hermes-agent:\K\S+' core/modules/hermes-agent/build/Dockerfile)` и затем `docker pull "nousresearch/hermes-agent:${HERMES_BASE}"`.

### 6c. Pre-pull error handling

```yaml
# DevPlan proposal (no error handling):
docker pull nousresearch/hermes-agent:v2026.7.7.2 2>&1 | tail -5

# Existing pattern (postgres pre-pull, line 174):
docker pull postgres:16-alpine 2>&1 || echo "[warn] postgres:16-alpine pull failed — continuing"
```

**[MEDIUM] DRIFT-ERROR-HANDLING:** pre-pull для hermes-agent не имеет `|| echo "[warn]..."` в отличие от postgres pre-pull. При rate-limit Docker Hub (fork PR, 100 pulls/6h) шаг покажет ошибку в логах, но не упадёт (continue-on-error на всём pre-pull шаге). Однако отсутствие явного `|| echo "[warn]"` снижает diagnosability.

---

## Semantic Verdict

**VERDICT: STABLE (с замечаниями HIGH и MEDIUM)**

DevPlan **принципиально корректен**: устранение дублирования обосновано статистикой (53% времени CI на ci-docker, из которых ~110 с — дубликаты), contract-тесты действительно Docker-free (0 `requires_docker`), predeploy-сплит точен (32 static + 1 Docker), merge_junit устойчив к отсутствующим файлам.

**Блокирующих проблем нет.** Все замечания — полнота документации и устойчивость к будущим изменениям.

### Обязательные доработки (перед реализацией):

1. **[HIGH]** Добавить `deploy-project.yml` в область анализа DevPlan (Part 3 «Что НЕ меняется» или отдельным пунктом).
2. **[HIGH]** Обновить header comments `ci.mk` lines 110-112 (описания MODE=fast и MODE=ci-docker) — добавить в Step 1 плана.
3. **[MEDIUM]** Добавить error handling в hermes-agent pre-pull: `|| echo "[warn] hermes-agent base image pre-pull failed — continuing"`.
4. **[MEDIUM]** Рассмотреть динамическое извлечение тега `nousresearch/hermes-agent` из Dockerfile вместо хардкода.

### Рекомендации (необязательные):

5. **[LOW]** Документировать изменение семантики `report.xml` в MODE=fast.
6. **[INFO]** Рассмотреть унификацию: все шаги MODE=fast через `$(MAKE) test MARKER=...` для консистентности генерации JUnit-отчётов.

### Проверка acceptance criteria DevPlan:

| AC | Оценка | Комментарий |
|----|:------:|-------------|
| (1) `make gate MODE=fast` зелёный с contract | ✅ | Contract-тесты Docker-free, должны работать в fast gate |
| (2) `make gate MODE=ci-docker` зелёный без contract/static/predeploy | ✅ | 3 шага: predeploy-docker(1) + smoke(38) + component(10) |
| (3) smoke + component не затронуты | ✅ | Количество и маркеры не меняются |
| (4) `make check-manifests` проходит | ✅ | Изменения не затрагивают generated files |
| (5) CI platform-test <700 с | ⚠️ | Оценка валидна при условии cache-hit на hermes-agent-base. Без pre-pull ошибки — ~620 с; при промахе — ~680-720 с |

---

$END_VERIFICATION_REPORT
