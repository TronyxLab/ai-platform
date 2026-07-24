$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation audit of DevPlan 049 — LiteLLM Multi-Tenant LLM Gateway. Проверка плана на полноту, консистентность, соответствие архитектурным инвариантам, потенциальный drift.
DESCRIPTION:           Комплексный pre-implementation QA (все фазы 1-6): статический аудит DevPlan, cross-file drift detection между планом и существующими конфигами, инвариант-верификация, оценка тестового плана, config sync audit. Файлы ещё не созданы — аудит плана, не реализации.
RATIONALE:             LARGE task (34 файла, архитектурные изменения, schema migration). Pre-implementation аудит предотвращает проблемные архитектурные решения до написания кода.
ACCEPTANCE_CRITERIA:   (1) Все CRITICAL drift-находки документированы с предложениями по исправлению. (2) Статус всех 11 инвариантов verified. (3) Тестовый план оценён на покрытие. (4) Config propagation chain протрассирован. (5) Semantic verdict сформулирован.
IMPLEMENTS:            QA role §QA workflow, LARGE task protocol (all phases 1-6).
IMPACTS:               DevPlan 049, будущая реализация.
REQUIRES:              DevPlan 049 (.ai/plans/049-litellm-multitenant-gateway/049-DevPlan.md), root AGENTS.md, все существующие конфигурационные файлы в scope.
$END_ARTIFACT_CONTRACT

---

🔒 **Verified against SHA:** `fbb11ef3132bd64e58cb8a7d5b610833295a0a50`
⚠️ **WARNING: Dirty working tree** — 6 файлов модифицированы относительно HEAD:
- `core/.node_update_test_marker`
- `core/internal/bootstrap/lifecycle/state_machine.py`
- `core/internal/bootstrap/node-lifecycle.sh`
- `core/modules/clickhouse/docker-compose.test.yml`
- `tests/_conftest/smoke.py`
- `tests/test_component_hermes.py`

Эти изменения могут конфликтовать с реализацией DevPlan 049. Рекомендуется закоммитить или откатить перед началом.

**Task Size:** LARGE (34 файла: 17 новых + 17 модифицируемых, архитектурные/schema/contract изменения)
**Тип аудита:** PRE-IMPLEMENTATION (файлы плана ещё не созданы)

---

## Section 1 — Static Audit (Phase 1): DevPlan Completeness

### 1.1 Document Structure Compliance

| Check | Status | Evidence |
|-------|--------|----------|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✅ PASS | lines 1, 910 |
| `$ARTIFACT_CONTRACT` (7 fields) | ✅ PASS | lines 3-11 |
| `$DOCUMENT_PLAN` (GOALs + USE_CASEs) | ✅ PASS | lines 13-30 |
| Section goals correlated with phases | ✅ PASS | 8 GOALs → 7 Phases |
| TRAP[DECISION] annotations | ✅ PASS | 3 TRAPs with Rev dates |
| Acceptance Criteria (AC) | ✅ PASS | 8 AC with verification commands |
| File Manifest | ✅ PASS | 17 new + 17 modified = 34 total |
| Risk matrix | ✅ PASS | 5 рисков с митигацией |
| Implementation order (PR sequence) | ✅ PASS | 7 phases → 6 PRs |

### 1.2 File Manifest Completeness Audit

| # | File | Type | Status | Issue |
|---|------|------|--------|-------|
| 1-8 | Новые Python модули + policy.yaml + shell | New | ⚠️ MISSING from manifest | `core/internal/llm/policy_schema.py` (#3) described in draft graph but NOT in File Manifest table — only in graph entity `llm_policy_yaml_FILE` |
| 9 | `core/schemas/llm-policy.schema.json` | New Schema | ✅ |
| 16-17 | `tests/test_data/llm/policy.{valid,invalid}.yaml` | New Fixtures | ✅ |
| 18 | `core/schemas/ai-platform.schema.json` | Modified | ✅ |
| 19-20 | compose files | Modified | ✅ |
| 25 | `.env` | Modified | ✅ |
| 26 | `core/modules/litellm/config/litellm-config.yml` | Modified → Generated | ⚠️ AMBIGUOUS | Plan says "replaced by generated" — but `make generate-manifests` / `make check-manifests` doesn't include it |
| 29 | `core/internal/bootstrap/lifecycle/state_machine.py` | Modified | ⚠️ DIRTY | Already modified in working tree |
| 31 | `core/entrypoint-manifest.yaml` | Modified | ⚠️ CONFUSED | Manifest entry listed as #31 but `compose-safe-up` in manifest already says "DevPlan 049" (line 323) — this appears to be a DIFFERENT DevPlan 049 |
| 34 | `core/internal/scripts/validate_module_yaml.py` | Modified | ✅ |

**[WARNING] FINDING-1:** Plan file #26 (`litellm-config.yml`) ambiguous: says "Заменён на generated", но механизм генерации (какой make target?) не описан. `make generate-manifests` не включает config_renderer. Файл `litellm-config.yml` не упомянут в manifest generation pipeline (helpers.mk lines 52-78).

**[WARNING] FINDING-2:** `policy_schema.py` (#3) описан в draft code graph (entity `llm_policy_schema_JSONSCHEMA` ссылается на него как потребитель), но отсутствует в File Manifest table. Присутствует в графе как отдельная сущность → должен быть в таблице.

**[INFO] FINDING-3:** Entry `compose-safe-up` в `entrypoint-manifest.yaml` (line 322-323) ссылается на "DevPlan 049" — коллизия нумерации. Это другой DevPlan 049 или тот же? Если другой — нужно переименовать во избежание путаницы.

---

## Section 2 — Drift Analysis (Phase 2): Plan vs Existing Configs

### 2.1 DRIFT Register

#### DRIFT-1: `needs.llm` → `llm` schema migration BREAKING PATH
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-SCHEMA-01 |
| **Severity** | **CRITICAL** |
| **Files** | `core/schemas/ai-platform.schema.json` (current: lines 109-115) vs Plan Phase 2 |
| **Expected** | Migration path from `needs.llm: "remote"/false` to `llm: {enabled: true/false}` |
| **Actual** | Plan describes REMOVAL of `needs.llm` and ADDITION of top-level `llm` field — but no migration path for existing projects with `needs.llm: "remote"` |
| **Fix** | Add AC-9: migration script or backward-compat `if/then` that treats `needs.llm: "remote"` as `llm: {enabled: true}` and `needs.llm: false` as `llm: {enabled: false}`. Или сохранить `needs.llm` как deprecated на переходный период. |

#### DRIFT-2: OPENAI_API_KEY lifecycle gap
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-SECRET-02 |
| **Severity** | **HIGH** |
| **Files** | `core/modules/litellm/module.yaml:48` vs `core/secret-definitions.yaml:64-69` vs Plan Phase 3 |
| **Expected** | Plan: `OPENAI_API_KEY` → `tier: removed` in secret-definitions, удалён из `env_requires` litellm module.yaml |
| **Actual** | Gate `test_gate_module_yaml_contract.py` валидирует, что все `env_requires` имеют соответствующие секреты. После удаления `OPENAI_API_KEY` из `env_requires` litellm — gate должен пройти (меньше требований). **НО:** `validate_module_yaml.py` проверяет наличие секретов для `env_requires`. При `tier: removed` валидатор должен корректно обрабатывать removed-секреты (не требовать их наличия). Проверить, что `validate_module_yaml.py` поддерживает `tier: removed`. |
| **Fix** | Verify: `grep "removed" core/internal/scripts/validate_module_yaml.py` — если removed не обрабатывается, добавить skip для `tier: removed`. |

#### DRIFT-3: LITELLM_API_KEY provisioning chain for hermes-agent
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-ENVCHAIN-03 |
| **Severity** | **HIGH** |
| **Files** | Plan Phase 5 (key_provisioner) vs `core/modules/hermes-agent/docker-compose.base.yml:138-153` |
| **Expected** | `LITELLM_API_KEY` (virtual key) пробрасывается в hermes-agent через compose environment |
| **Actual** | key_provisioner генерирует ключи для ПРОЕКТОВ (`.env.platform`). Hermes-agent — МОДУЛЬ, не проект. Его env читается из `.env` / `secrets.env`. Plan не описывает, как `LITELLM_API_KEY` для hermes-agent попадает в его compose-окружение. |
| **Fix** | Clarify: (a) hermes-agent получает `LITELLM_API_KEY` через `profile_rules: [{match: {name: hermes-agent}, profile: unlimited}]` → key_provisioner генерирует ключ → сохраняет в `LITELLM_PROJECT_KEYS` SOPS → `project-sync-env` для hermes-agent (если он зарегистрирован как проект) ИЛИ (b) hermes-agent использует `LITELLM_MASTER_KEY` напрямую (не virtual key) для администрирования. |

#### DRIFT-4: litellm-config.yml generation not integrated
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-MANIFEST-04 |
| **Severity** | **HIGH** |
| **Files** | Plan Phase 4 vs `makefiles/helpers.mk:52-78` (`generate-manifests`) |
| **Expected** | `litellm-config.yml` generation integrated into `make generate-manifests` / `make check-manifests` |
| **Actual** | `generate-manifests` runs 4 Python scripts — NONE for config_renderer. Plan says file becomes "generated (not manually edited)" but doesn't specify which make target regenerates it. |
| **Fix** | Add `config_renderer.py` invocation to `generate-manifests` target, OR create separate `make render-litellm-config` target. Add to `check-manifests` freshness check. |

#### DRIFT-5: CI workflow references OPENAI_API_KEY
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-CI-05 |
| **Severity** | **MEDIUM** |
| **Files** | `.github/workflows/platform-test.yml:290` vs Plan Phase 3 |
| **Expected** | CI workflow обновлён: убрать проверку `OPENAI_API_KEY` |
| **Actual** | Plan не упоминает `.github/workflows/platform-test.yml` в File Manifest. Строка 290: `if [ -z "${{ secrets.OPENAI_API_KEY }}" ]` — после миграции этот secret может отсутствовать. |
| **Fix** | Add `.github/workflows/platform-test.yml` to modified files list. Update OPENAI_API_KEY reference to LITELLM_MASTER_KEY or remove. |

#### DRIFT-6: config_renderer invocation missing from pipeline
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-PIPELINE-06 |
| **Severity** | **MEDIUM** |
| **Files** | Plan Phase 7 vs pipeline flow |
| **Expected** | `config_renderer.py` вызывается в pipeline (deploy-modules, bootstrap, или CI) |
| **Actual** | Phase 7 описывает интеграцию `key_provisioner` (provision-llm → deploy-modules, deploy-context, bootstrap), но `config_renderer` (рендер litellm-config.yml) не упомянут в pipeline flow. Когда и где рендерится litellm-config.yml? |
| **Fix** | Add `config_renderer` invocation to pipeline: either (a) `make generate-manifests` (CI), (b) `deploy-modules` step before litellm restart, or (c) bootstrap step before litellm deploy. |

#### DRIFT-7: config.yaml model field absence
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-HERMES-07 |
| **Severity** | **MEDIUM** |
| **Files** | `core/modules/hermes-agent/build/config/config.yaml:40-46` vs Plan Phase 6 |
| **Expected** | Plan: `model.name: deepseek-v4-pro → reasoning` |
| **Actual** | Current `config.yaml` has NO explicit `model.model` field — only `model.provider: deepseek`. Hermes может использовать default model провайдера. Plan должен уточнить: добавляется ли поле `model.model: reasoning` (новое) или изменяется существующее. |
| **Fix** | Clarify: if Hermes uses provider-default model, explicitly ADD `model: reasoning` field. Document current Hermes behavior (what model does it resolve when only provider is set?). |

#### DRIFT-8: DEEPSEEK_API_KEY env_requires gap
| Поле | Значение |
|------|----------|
| **ID** | DRIFT-ENVREQ-08 |
| **Severity** | **MEDIUM** |
| **Files** | `core/modules/litellm/module.yaml:42-48` vs `core/modules/litellm/docker-compose.base.yml:100` |
| **Expected** | After OPENAI_API_KEY removal, DEEPSEEK_API_KEY становится ЕДИНСТВЕННЫМ провайдерским ключом для litellm модуля |
| **Actual** | `DEEPSEEK_API_KEY` отсутствует в `env_requires` litellm module.yaml. Сейчас он опционален (`${DEEPSEEK_API_KEY:-}` в compose). Но после удаления других ключей — это единственный рабочий ключ. Без него LiteLLM не сможет обслуживать запросы. |
| **Fix** | Add `DEEPSEEK_API_KEY` to litellm `env_requires` (required=true) ИЛИ оставить optional с документированием последствий (LiteLLM стартует, но все LLM-запросы падают). |

### 2.2 Contract Violations

| Module | Required File | Status | Evidence |
|--------|--------------|--------|----------|
| `core/modules/litellm/` | `docker-compose.base.yml` | ✅ Exists | `core/modules/litellm/docker-compose.base.yml` |
| `core/modules/litellm/` | `healthcheck.sh` | ⚠️ NOT VERIFIED | Plan doesn't mention — need to verify exists |
| `core/modules/litellm/` | `Makefile` | ⚠️ NOT VERIFIED | Plan doesn't mention — need to verify exists |
| `core/modules/litellm/` | `module.yaml` | ✅ Exists | `core/modules/litellm/module.yaml` |
| `core/modules/litellm/` | `.dockerignore` | ⚠️ NOT VERIFIED | Plan doesn't mention |

### 2.3 Summary

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 1 | DRIFT-SCHEMA-01 |
| HIGH | 3 | DRIFT-SECRET-02, DRIFT-ENVCHAIN-03, DRIFT-MANIFEST-04 |
| MEDIUM | 4 | DRIFT-CI-05, DRIFT-PIPELINE-06, DRIFT-HERMES-07, DRIFT-ENVREQ-08 |
| WARNING | 2 | FINDING-1, FINDING-2 |
| INFO | 1 | FINDING-3 |

---

## Section 3 — Invariant Verification (Phase 3)

Читаю `AGENTS.md` (root) — 11 архитектурных инвариантов. Проверяю каждый против DevPlan 049.

| # | Invariant | Status | Evidence / Risk |
|---|-----------|--------|-----------------|
| 1 | **Makefile — единый фасад.** Все операции через `make <target>`. | ⚠️ AT_RISK | Plan добавляет `provision-llm` target (#30) — целевой глагол в словаре (✅, не forbidden). Требуется регистрация в `entrypoint-manifest.yaml` (#31) + `core/AGENTS.md` canon table. **Риск:** без регистрации CI gates (`test_all_makefile_targets_in_allowed_verbs`) упадут. |
| 2 | **Модель деплоя:** git push → CI. Core через SCP/rsync. | ✅ HELD | Provisioning pipeline не меняет модель деплоя — выполняется на VPS через ssh+rsync. |
| 3 | **org = context.** | ✅ HELD | Plan не затрагивает модель контекстов. TRAP[DECISION] 2026-07-24 явно откладывает per-context policy до реальной необходимости. |
| 4 | **AGENTS.md — 3 канонических файла.** | ✅ HELD | Изменения AGENTS.md не планируются. |
| 5 | **core/entrypoint-manifest.yaml — реестр операций.** | ⚠️ AT_RISK | Plan добавляет `provision-llm` → должен быть в manifest `allowed_verbs` + `delegates_to`. Plan это упоминает (#31). **Риск:** забыть обновить `core/AGENTS.md` canon table (генерируется `generate_agents_md.py` из manifest → авто). |
| 6 | **make bootstrap-node — строго идемпотентный.** | ⚠️ AT_RISK | Plan добавляет шаг `provision_llm_keys` в bootstrap-node pipeline (Phase 7). key_provisioner идемпотентен по дизайну (GET /key/info + metadata.project match). **Риск:** если key_provisioner упадёт при первом bootstrap (LiteLLM не готов), bootstrap-node должен корректно обработать ошибку и продолжить (graceful degradation) или быть вызван после litellm healthcheck. |
| 7 | **Полный локальный стек через `docker compose up`.** | ✅ HELD | Compose изменения не ломают локальный стек — удаляются только неиспользуемые ключи. |
| 8 | **LiteLLM — PostgreSQL во всех окружениях.** | ✅ HELD | Без изменений. |
| 9 | **Тестовый сервер может быть пересоздан.** | ✅ HELD | Idempotent provisioner + config_renderer обеспечивают воспроизводимость. |
| 10 | **Сборка образов hermes.** | ✅ HELD | Hermes-agent миграция не затрагивает сборочный pipeline (только config + env). |
| 11 | **Manifest Generation Contract.** | ⚠️ AT_RISK | `litellm-config.yml` становится generated-файлом, но НЕ включён в manifest generation pipeline. `make check-manifests` не будет проверять его свежесть. **Риск:** drift между policy.yaml и litellm-config.yml не будет детектирован CI. |

### Invariant Summary

| Status | Count |
|--------|-------|
| HELD | 7 |
| AT_RISK | 4 |
| VIOLATED | 0 |
| UNVERIFIABLE | 0 |

---

## Section 4 — Test Quality Plan (Phase 4)

### 4.1 Test Coverage Evaluation

Тестовые файлы (plannned, ещё не созданы):

| # | Test File | Type | Covers | Assessment |
|---|-----------|------|--------|------------|
| 10 | `tests/unit/test_llm_policy_schema.py` | Unit | policy.yaml schema validation | ✅ Good |
| 11 | `tests/unit/test_llm_config_renderer.py` | Unit | config_renderer output | ✅ Good |
| 12 | `tests/unit/test_llm_key_provisioner.py` | Unit | key_provisioner idempotency, 409, budgets | ✅ Good |
| 13 | `tests/gates/test_gate_llm_aliases.py` | Gate | aliases + fallback contracts | ✅ Good |
| 14 | `tests/gates/test_gate_llm_provisioner.py` | Gate | idempotent provisioner integration | ✅ Good |
| 15 | `tests/gates/test_gate_secrets_llm_cleanup.py` | Gate | отсутствие неиспользуемых ключей | ✅ Good |
| 27 | `tests/test_project_schema.py` (+ llm) | Unit | ai-platform.schema.json llm field | ✅ Good |
| 28 | `tests/test_predeploy_hermes_invariants.py` | Predeploy | hermes config invariants | ✅ Good |

### 4.2 Coverage Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| **GAP-1: Env propagation chain** | HIGH | Нет теста, проверяющего полную цепочку `key_provisioner → SOPS → .env.platform → LITELLM_API_KEY`. Контракт "каждый проект с llm.enabled: true получает уникальный ключ" не покрыт автоматическим тестом. AC-5 проверяется только вручную на VPS. |
| **GAP-2: litellm-config.yml freshness** | MEDIUM | Нет gate-теста `test_litellm_config_up_to_date` (аналог `test_gate_manifests_up_to_date`). Если litellm-config.yml станет generated, должен быть gate на свежесть. |
| **GAP-3: schema migration backward compat** | HIGH | Нет теста на backward compatibility `needs.llm` → `llm`. Если migration path не описан (DRIFT-SCHEMA-01), то и тестов на него нет. |
| **GAP-4: config_renderer integration test** | MEDIUM | Нет интеграционного теста: `config_renderer.py --policy ... --output ...` → `check-litellm-config-valid`. AC-3 проверяется вручную. |
| **GAP-5: budget exceeded (AC-7)** | LOW | AC-7 проверяет 429 при превышении бюджета — это E2E тест, требует живого LiteLLM. Not practical for unit/gate. Приемлемо как manual acceptance. |

### 4.3 Test Quality Assessment

**Сильные стороны:**
- Хорошее покрытие контрактов: алиасы, fallback-цепочки, идемпотентность
- Gate-тесты на отсутствие неиспользуемых ключей (secrets cleanup) — правильный defensive подход
- Негативные тесты: `policy.invalid.yaml` для schema validation
- Unit-тесты используют mock для key_provisioner (не требуют живого LiteLLM)

**Слабые стороны:**
- 3 из 8 Acceptance Criteria проверяются только вручную (AC-3, AC-5, AC-7)
- Нет теста на полный env propagation chain (GAP-1)
- Тестовый план не включает `test_gate_litellm_config_freshness` (GAP-2)

**Test Health Score (план): 72/100**
- −10: GAP-1 (нет теста env propagation chain)
- −8: GAP-3 (нет теста schema backward compat)
- −5: GAP-2 (нет gate на litellm-config freshness)
- −3: GAP-4 (нет интеграционного теста config_renderer)
- −2: 3/8 AC manual-only

---

## Section 5 — Runtime Validation (Phase 5)

### ⛔ NOT APPLICABLE — PRE-IMPLEMENTATION

Файлы не созданы, тесты не существуют. Runtime validation невозможна.

**Recommendation:** После реализации Phase 1+2 запустить:
```bash
make gate MODE=fast
```
Убедиться, что существующие gate-тесты не сломаны изменениями schema + compose.

---

## Section 6 — Config Sync Audit (Phase 6)

### 6.1 Env Variable Propagation Chain: LITELLM_API_KEY

Трассируем предложенную цепочку:

```
key_provisioner.py (генерирует)
    ↓
LITELLM_PROJECT_KEYS (SOPS encrypted)
    ↓
decrypt-secrets.sh (расшифровывает)
    ↓
project-sync-env (пишет в .env.platform проекта)
    ↓
.env.platform (LITELLM_API_KEY=sk-...)
    ↓
docker-compose.yml проекта (env_file: .env.platform)
    ↓
контейнер проекта (LITELLM_API_KEY)
```

**Break points:**
- ❓ `decrypt-secrets.sh` должен обрабатывать `litellm-project-keys.enc.yaml` — Plan упоминает это (line 631), но текущий `decrypt-secrets.sh` этого не делает. Требуется модификация.
- ❓ `project-sync-env` должен знать, какой ключ какому проекту → Plan упоминает это (line 632), но `gen-env-platform.sh` нужно модифицировать.
- ❓ Hermes-agent не является проектом → отдельная цепочка (DRIFT-ENVCHAIN-03).

### 6.2 Compose Override Consistency

| Service | base.yml | Changes | Impact |
|---------|---------|---------|--------|
| litellm | env: OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY → REMOVED | ✅ Clean | DEEPSEEK_API_KEY остаётся единственным |
| hermes-agent | env: OPENAI_API_KEY → LITELLM_API_KEY; провайдерские ключи → REMOVED | ✅ Clean | Новый ключ, старые удалены |

**No override conflicts detected.**

### 6.3 Secret Definition Chain

```
secret-definitions.yaml (SSoT)
    ↓ generate_secrets_manifest.py
secrets-manifest.yaml (generated)
    ↓ generate_platform_env.py
platform-env.yaml (generated)
    ↓
CI workflows + .env.example + tests/_conftest/smoke_env_generated.py
```

**Plan changes to secret-definitions:**
- `OPENAI_API_KEY`: `tier: required` → `tier: removed` (line 65)
- `ANTHROPIC_API_KEY`: не существует → добавить `tier: removed`
- `OPENROUTER_API_KEY`: не существует → добавить `tier: removed`
- `GLM_API_KEY`: не существует → добавить `tier: removed`
- `LITELLM_PROJECT_KEYS`: новый → `tier: generated`, `source: provisioner` (НЕ autogen!)

**[WARNING] FINDING-4:** `LITELLM_PROJECT_KEYS` имеет `source: provisioner` — новый тип source. Все существующие `source` значения: `sops`, `autogen`, `ci-secret`. `provisioner` — новый, требует обновления `generate_secrets_manifest.py` и/или `validate_module_yaml.py` для поддержки. Альтернатива: использовать `source: sops` с `gen_command: "python3 core/internal/llm/key_provisioner.py"` (но provisioner не генерирует секрет локально — он вызывает API).

### 6.4 Network/Volume Consistency

Изменения не затрагивают networks и volumes. ✅ No drift.

---

## TRAP Analysis

### Active TRAPs in Scope

| TRAP | Location | Relevance to DevPlan 049 |
|------|----------|--------------------------|
| TRAP[DECISION] 2026-07-15 · HI · Контекстные LLM-конфиги | `core/modules/litellm/module.yaml:16-19` | **RESOLVED** этим DevPlan. Plan 049 — прямая реализация этого TRAP. Обновить TRAP → добавить ссылку на DevPlan 049. |
| TRAP[DECISION] 2026-07-24 · HI · policy.yaml как ЕДИНСТВЕННЫЙ SSoT | DevPlan 049:895-898 | Новый TRAP в плане — корректен. |
| TRAP[DECISION] 2026-07-24 · MED · OPENAI_API_KEY audit trail | DevPlan 049:900-903 | Корректен. |
| TRAP[DECISION] 2026-07-24 · MED · deprecated-алиасы на 1 месяц | DevPlan 049:905-908 | Корректен. Rev date: 2026-08-24. |

---

## ⟦CHECKPOINT 1⟧ Interim Assessment

**До начала реализации требуется разрешить:**

1. 🔴 **DRIFT-SCHEMA-01 (CRITICAL):** Определить migration path `needs.llm` → `llm`. Без этого существующие проекты с `needs.llm: "remote"` сломаются при валидации schema.

2. 🟠 **DRIFT-MANIFEST-04 (HIGH):** Определить, как `litellm-config.yml` интегрируется в `make generate-manifests` / `make check-manifests`. Без этого generated-файл будет driftовать без детекции.

3. 🟠 **DRIFT-ENVCHAIN-03 (HIGH):** Определить цепочку `LITELLM_API_KEY` для hermes-agent (модуль, не проект).

---

## Semantic Verdict

**VERDICT: DRIFTED (WARNING — 1 CRITICAL, 3 HIGH, 4 MEDIUM)**

### Breakdown

| Dimension | Score | Max | Status |
|-----------|-------|-----|--------|
| Plan completeness | 85 | 100 | 2 findings (FINDING-1, FINDING-2) |
| Drift analysis | 60 | 100 | 8 drifts (1C/3H/4M) |
| Invariant compliance | 85 | 100 | 4 AT_RISK, 0 VIOLATED |
| Test coverage (plan) | 72 | 100 | 5 gaps |
| Config sync | 70 | 100 | 3 break points |
| **Composite** | **74** | **100** | **DRIFTED (WARNING)** |

### Health Score: 74/100

```
100 - 5 (DRIFT-SCHEMA-01 CRITICAL)
    - 9 (3 HIGH drifts × 3)
    - 4 (4 MEDIUM drifts × 1)
    - 0 (0 VIOLATED invariants)
    - 5 (DRIFT-ENVCHAIN-03: no test for env chain)
    - 3 (GAP-1: uncovered invariant — env propagation)
= 74
```

### Pre-Implementation Gate: CONDITIONAL PASS

**Можно начинать реализацию при условии:**
1. DRIFT-SCHEMA-01 решён (migration path для `needs.llm`)
2. DRIFT-MANIFEST-04 решён (интеграция в manifest generation)
3. DRIFT-ENVCHAIN-03 решён (LITELLM_API_KEY для hermes-agent)

**Рекомендуется решить до соответствующих фаз:**
- DRIFT-SECRET-02 → до Phase 5 (cleanup)
- DRIFT-PIPELINE-06 → до Phase 7 (integration)
- DRIFT-CI-05 → до Phase 5 (cleanup)
- DRIFT-HERMES-07 → до Phase 6 (hermes migration)
- DRIFT-ENVREQ-08 → до Phase 5 (cleanup)

---

## Proposed Delegation

Для исправления CRITICAL drift (DRIFT-SCHEMA-01) и архитектурных неясностей рекомендуется:

1. **Architect**: дополнить DevPlan 049 migration path для `needs.llm` → `llm`, интеграцию config_renderer в manifest pipeline, и цепочку LITELLM_API_KEY для hermes-agent.

2. **Coder**: после резолва — реализация по фазам.

---

$END_VERIFICATION_REPORT
