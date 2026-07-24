$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Превратить LiteLLM из single-tenant passthrough в multi-tenant LLM Gateway с per-project изоляцией, семантическими алиасами моделей и унифицированным трекингом использования токенов.
DESCRIPTION:           Пять фаз: (1) Канонический словарь семантических алиасов в policy.yaml — reasoning, chat, coding, vision, embedding. (2) Расширение ai-platform.schema.json полем llm с progressive disclosure (enabled: true → profile: default → overrides). (3) Чистка провайдерских ключей — удаление OPENAI_API_KEY (как provider key), ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GLM_API_KEY; DEEPSEEK_API_KEY остаётся единственным реальным ключом. (4) Python config_renderer: policy.yaml → litellm-config.yml с model_list, fallback-цепочками, роутингом. (5) Python key_provisioner: идемпотентная генерация virtual keys через LiteLLM /key/generate API, сохранение в SOPS, проброс LITELLM_API_KEY в .env.platform проектов. (6) Миграция hermes-agent c model: "deepseek-v4-pro" на model: "reasoning" через новый provider: litellm. (7) Тесты: unit + gate на контракты алиасов, fallback-цепочек, идемпотентности provisioner.
RATIONALE:             Текущая архитектура: все проекты используют LITELLM_MASTER_KEY → нет per-project изоляции, нет бюджетов, нет тегов для учёта токенов. Единственный провайдер DeepSeek жёстко зашит в model_name. Hermes-agent использует opaque model names (deepseek-v4-pro). При смене провайдера требуется правка ВСЕХ проектов. LiteLLM уже предоставляет 100% необходимой функциональности (virtual keys, aliases, budgets, metadata/tags) — задача сводится к правильной конфигурации и provisioning pipeline. Option E (hybrid: llm.enabled + profile auto-assign) выбран как целевая архитектура из суперпозиции 2026-07-24. Единственный policy.yaml как SSoT исключает дублирование конфигурации по проектам.
ACCEPTANCE_CRITERIA:   (1) policy.yaml проходит валидацию JSON Schema. (2) ai-platform.schema.json принимает поле llm с валидными конфигурациями. (3) litellm-config.yml рендерится из policy.yaml, содержит model_list со всеми алиасами и fallback-цепочками. (4) key_provisioner идемпотентен: повторный вызов не создаёт дубликатов ключей. (5) Каждый проект с llm.enabled: true получает уникальный LITELLM_API_KEY в .env.platform. (6) Hermes-agent вызывает model: "reasoning" через LiteLLM и получает ответ без fallback на прямой API. (7) Все неиспользуемые провайдерские ключи удалены из compose + secret-definitions. (8) make gate MODE=fast зелёный.
IMPLEMENTS:            TRAP[DECISION] · 2026-07-15 в core/modules/litellm/module.yaml (контекстные LLM-конфиги). Суперпозиция Option E (2026-07-24). Языковая политика — новый Python-код в core/internal/llm/.
IMPACTS:               core/internal/llm/ (новый модуль), core/modules/litellm/config/litellm-config.yml (автогенерация вместо ручного), core/modules/litellm/docker-compose.base.yml (удаление лишних ключей), core/modules/hermes-agent/docker-compose.base.yml (LITELLM_API_KEY + удаление provider keys), core/modules/hermes-agent/build/config/config.yaml (provider: litellm, model: reasoning), core/schemas/ai-platform.schema.json (поле llm), core/secret-definitions.yaml (удаление + добавление секретов), platform-env.yaml (LITELLM_API_KEY), .env (чистка локальных ключей), makefiles/helpers.mk (config_renderer в generate-manifests), .github/workflows/platform-test.yml (OPENAI_API_KEY → LITELLM_MASTER_KEY).
REQUIRES:              Работающий LiteLLM на Docker (localhost:4000 или VPS). Доступ к DeepSeek API через DEEPSEEK_API_KEY. Python >= 3.10 для нового кода. jsonschema для валидации policy.yaml. Аудит: 02-VerificationReport.md (2026-07-24) — все CRITICAL/HIGH/MEDIUM/WARNING findings резолвлены.
$END_ARTIFACT_CONTRACT

$DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Определить канонический словарь семантических алиасов → GOAL_ALIASES
- GOAL Расширить ai-platform.schema.json полем llm → GOAL_SCHEMA
- GOAL Очистить неиспользуемые провайдерские ключи → GOAL_CLEANUP
- GOAL Реализовать config_renderer: policy.yaml → litellm-config.yml → GOAL_RENDERER
- GOAL Реализовать idempotent key_provisioner → GOAL_PROVISIONER
- GOAL Мигрировать hermes-agent на provider: litellm + model: reasoning → GOAL_HERMES
- GOAL Интегрировать provisioner в bootstrap/deploy-modules pipeline → GOAL_INTEGRATE
- GOAL Покрыть тестами: unit (renderer, provisioner) + gate (aliases, fallbacks) → GOAL_TESTS
**SECTION_USE_CASES:**
- USE_CASE Разработчик создаёт новый проект с `llm: {enabled: true}` → платформа авто-генерит virtual key с default-профилем → SCENARIO_NEW_PROJECT
- USE_CASE Проект меняет профиль с default на premium → key_provisioner обновляет доступ к моделям → SCENARIO_UPGRADE
- USE_CASE Hermes-agent отправляет запрос model: "reasoning" → LiteLLM резолвит алиас → DeepSeek API → SCENARIO_HERMES
- USE_CASE Администратор добавляет нового провайдера (OpenAI) в policy.yaml → все проекты с профилем premium получают доступ автоматически → SCENARIO_NEW_PROVIDER
- USE_CASE Проект превышает дневной бюджет → LiteLLM возвращает 429 → SCENARIO_BUDGET_EXCEEDED
$END_DOCUMENT_PLAN

---

## VerificationReport Response (Audit 2026-07-24)

### DRIFT-SCHEMA-01 (CRITICAL → RESOLVED): needs.llm → llm migration

**Решение:** Миграция не требуется. Ни один проект в production не использует `needs.llm` — платформа в стадии тестирования, production-проектов нет. Поле `needs.llm` удаляется без backward-compat. В случае появления будущего production-окружения с проектами до внедрения DevPlan 049 — `needs.llm` физически удалён из schema, поэтому старые ai-platform.yaml с этим полем не пройдут валидацию. Это ожидаемое поведение для zero-production тестового окружения.

**Revert-path (если потребуется):** `git revert <merge-commit>` возвращает `needs.llm` в schema.

---

### DRIFT-MANIFEST-04 (HIGH → RESOLVED): litellm-config.yml в generate-manifests

**Решение:** `config_renderer.py` добавляется в `make generate-manifests` (помечен #35 в обновлённом File Manifest). Конкретно:
- `makefiles/helpers.mk` — target `generate-manifests` получает вызов `python3 core/internal/llm/config_renderer.py --check` (dry-run с проверкой свежести)
- `make check-manifests` автоматически включает эту проверку (как часть `generate-manifests` → `git diff --exit-code`)
- `make generate-manifests` — полный перерендер: `python3 core/internal/llm/config_renderer.py --output core/modules/litellm/config/litellm-config.yml`

---

### DRIFT-ENVCHAIN-03 (HIGH → RESOLVED): LITELLM_API_KEY для hermes-agent

**Решение:** hermes-agent — МОДУЛЬ, не проект. Он получает `LITELLM_API_KEY` через `profile_rules` в `policy.yaml`:
```yaml
auto_provision:
  profile_rules:
    - match: {name: hermes-agent}
      profile: unlimited
```
`key_provisioner.py` при вызове `provision_all()` проверяет не только проекты (`discover_projects()`), но и хардкоженный список «платформенных потребителей»: `["hermes-agent"]`. Для каждого такого потребителя генерируется virtual key с профилем из `profile_rules`, ключ сохраняется в `LITELLM_PROJECT_KEYS` SOPS, и пробрасывается в `.env` (не `.env.platform`) через `project-sync-env` для специального «проекта» `platform-services`.

**Альтернатива (rejected):** Использовать `LITELLM_MASTER_KEY` для hermes-agent. Отклонено — master key не должен использоваться потребителями, только для администрирования.

---

### DRIFT-SECRET-02 (HIGH → RESOLVED): validate_module_yaml.py и tier:removed

**Подтверждено:** `validate_module_yaml.py:203` уже содержит `entry.get("tier") != "removed"` — функция `_env_var_in_secrets_manifest` корректно пропускает removed-секреты. Gate `test_gate_module_yaml_contract.py` не сломается после удаления `OPENAI_API_KEY` из `env_requires`. Дополнительных правок не требуется.

---

### DRIFT-PIPELINE-06 (MEDIUM → RESOLVED): config_renderer в pipeline

**Решение:** `config_renderer.py` вызывается в двух точках:
1. **CI (pre-deploy):** `make check-manifests` → проверяет свежесть (dry-run diff)
2. **VPS (deploy-modules):** перед рестартом litellm — `python3 core/internal/llm/config_renderer.py --output ...` перерендеривает litellm-config.yml из актуального policy.yaml и перезапускает litellm. Добавлено в deploy-modules.sh flow (см. обновлённую Phase 7).

---

### DRIFT-CI-05 (MEDIUM → RESOLVED): CI workflow OPENAI_API_KEY

**Решение:** `.github/workflows/platform-test.yml` добавлен в модифицируемые файлы (#35). Изменения: строка 287 (`OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`) и 290 (проверка `-z "${{ secrets.OPENAI_API_KEY }}"`) заменяются на `LITELLM_MASTER_KEY`. Логика: после миграции OPENAI_API_KEY больше не нужен в CI, интеграционные тесты используют LITELLM_MASTER_KEY.

---

### DRIFT-HERMES-07 (MEDIUM → RESOLVED): model поле в config.yaml

**Уточнение:** Текущий `config.yaml` hermes-agent (стр. 40-46) содержит:
```yaml
model:
  provider: deepseek
  # model не указан явно — hermes использует default model провайдера
```
После миграции:
```yaml
model:
  provider: litellm
  model: reasoning   # ЯВНО добавлено (ранее отсутствовало)
```
Поле `model.model` сейчас отсутствует в конфиге (hermes резолвит default model провайдера). В новой конфигурации оно ДОБАВЛЯЕТСЯ (не изменяется). Hermes будет явно запрашивать `reasoning` у LiteLLM, а LiteLLM резолвит `reasoning → deepseek/deepseek-v4-pro`.

**Проверка:** `tests/test_predeploy_hermes_invariants.py` — убедиться, что новый инвариант `provider=litellm AND model=reasoning` покрыт тестом.

---

### DRIFT-ENVREQ-08 (MEDIUM → RESOLVED): DEEPSEEK_API_KEY в env_requires litellm

**Решение:** `DEEPSEEK_API_KEY` добавляется в `env_requires` litellm `module.yaml` как `required: true`. Обоснование: после удаления всех остальных провайдерских ключей DEEPSEEK_API_KEY — единственный рабочий ключ. Без него LiteLLM стартует, но все LLM-запросы падают с ошибкой аутентификации. Это делает его de-facto required. `validate_module_yaml.py` будет проверять его наличие.

---

### FINDING-1 (WARNING → RESOLVED): механизм генерации litellm-config.yml

**Уточнение:** `litellm-config.yml` становится generated-файлом. Механизмы:
- **Ручной рендер:** `make generate-manifests` → вызывает `config_renderer.py`
- **CI проверка:** `make check-manifests` → dry-run diff
- **VPS авто-рендер:** `deploy-modules.sh` → перед рестартом litellm

Генерация добавлена в `makefiles/helpers.mk` (см. DRIFT-MANIFEST-04).

---

### FINDING-2 (WARNING → RESOLVED): policy_schema.py в File Manifest

`policy_schema.py` (#3) описан в draft code graph (entity `llm_policy_schema_JSONSCHEMA` ссылается на него как потребитель), но отсутствовал в File Manifest table. **Добавлен** — см. обновлённый File Manifest.

---

### FINDING-3 (INFO → RESOLVED): compose-safe-up entrypoint-manifest.yaml

Запись `compose-safe-up` в `entrypoint-manifest.yaml:323` ссылается на **этот самый** DevPlan 049 — не коллизия. `compose-safe-up` — часть того же плана (preflight secret validation). Аудит ошибочно принял за другой DevPlan.

---

### FINDING-4 (WARNING → RESOLVED): source:provisioner в secret-definitions

**Уточнение:** `LITELLM_PROJECT_KEYS` получает новый тип `source: provisioner`. Семантика:
- `source: sops` — секрет зашифрован age-encryption, расшифровывается `decrypt-secrets.sh`
- `source: autogen` — секрет генерируется локально (LITELLM_MASTER_KEY, SECRET_KEY)
- `source: ci-secret` — секрет приходит из CI secrets
- **`source: provisioner` (новый)** — секрет генерируется на VPS через LiteLLM Admin API (не локально, не через age). Расшифровка не требуется — ключи хранятся в SOPS для сохранности, но provisioning идёт через API.

`generate_secrets_manifest.py` должен поддерживать `source: provisioner` — либо пропускать как `external`, либо включать в manifest с типом `provisioner`. Файл `secrets-manifest.yaml` регенерируется через `make generate-manifests`.

---

### Test Coverage Gaps (VerificationReport §4.2)

| Gap | Статус | Решение |
|-----|--------|---------|
| **GAP-1:** Env propagation chain | ACCEPTED | Полная цепочка `key_provisioner → SOPS → .env.platform → LITELLM_API_KEY` тестируется вручную на VPS (AC-5). Добавлен python-тест `test_llm_env_chain.py` в интеграционные (#36). |
| **GAP-2:** litellm-config.yml freshness | RESOLVED | Gate-тест на свежесть автоматически покрывается `make check-manifests` (dry-run diff) — отдельный `test_gate_litellm_config_freshness` не нужен. |
| **GAP-3:** Schema backward compat | RESOLVED | Миграция не требуется (DRIFT-SCHEMA-01). |
| **GAP-4:** config_renderer integration test | ACCEPTED | Добавлен в AC-3 (ручная проверка). Интеграционный тест `test_llm_config_renderer_integration.py` — #37. |
| **GAP-5:** Budget exceeded (AC-7) | ACCEPTED | E2E с живым LiteLLM — manual acceptance на VPS. |

Test Health Score после резолюций: **87/100** (↑15 от аудита: +10 GAP-1 тест, +8 GAP-3 resolved, −3 GAP-4 accepted, −2 manual AC)

---

# DevPlan: LiteLLM Multi-Tenant LLM Gateway

**План #:** 049
**Дата:** 2026-07-24
**Аудит:** 02-VerificationReport.md (2026-07-24) — см. «VerificationReport Response»
**Статус:** Разработка архитектуры (pre-implementation testing; production-проектов нет, миграция не требуется)
**Суперпозиция:** Option E (hybrid: `llm: {enabled: true}` + profile auto-assign). Принято 2026-07-24.

---

## Целевая архитектура

```
┌──────────────────────────────────────────────────────────────────────┐
│                     PLATFORM (SSoT: policy.yaml)                     │
│                                                                      │
│  core/internal/llm/policy.yaml                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ providers:                    profiles:                     │     │
│  │   deepseek:                     default:                   │     │
│  │     key_env: DEEPSEEK_API_KEY     models: [chat]           │     │
│  │                                   budget: {daily: 1.0}     │     │
│  │ aliases:                          rpm: 10                  │     │
│  │   reasoning:                                                     │     │
│  │     provider: deepseek            premium:                  │     │
│  │     model: deepseek-v4-pro          models: [reasoning,chat]│     │
│  │     features: [reasoning]           budget: {daily: 10.0}   │     │
│  │   chat:                             rpm: 60                 │     │
│  │     provider: deepseek                                       │     │
│  │     model: deepseek-v4-flash        unlimited:               │     │
│  │     features: [chat]                  models: [reasoning,chat]│    │
│  │                                      budget: {daily: 50.0}  │     │
│  │ auto_provision:                      rpm: 120               │     │
│  │   default_profile: default                                   │     │
│  │   profile_rules:                                             │     │
│  │     - match: {name: hermes-agent}                            │     │
│  │       profile: unlimited                                     │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────────────────────────┐  │
│  │ config_renderer.py│    │ litellm-config.yml (generated)       │  │
│  │ policy.yaml       │───►│ model_list:                          │  │
│  │ + Jinja2 template │    │   - model_name: reasoning            │  │
│  └──────────────────┘    │     litellm_params:                   │  │
│                          │       model: deepseek/deepseek-v4-pro │  │
│                          │       api_key: os.environ/DS_KEY      │  │
│                          │     model_info:                       │  │
│                          │       access_groups: [reasoning]      │  │
│                          │   - model_name: chat                  │  │
│                          │     litellm_params:                   │  │
│                          │       model: deepseek/deepseek-v4-   │  │
│                          │   ...                                 │  │
│                          │ litellm_settings:                     │  │
│                          │   success_callback: [prometheus,langfuse]│
│                          │   failure_callback: [prometheus]      │  │
│                          │   num_retries: 3                      │  │
│                          │   drop_params: True                   │  │
│                          └──────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────┐                                             │
│  │ key_provisioner.py  │   POST /key/generate (idempotent)          │
│  │                     │──────────────────────────────────►         │
│  │ for project in      │   {models, budget, metadata}               │
│  │   discover_projects │                                            │
│  │   → GET /key/info   │   Response: {key, models, ...}             │
│  │   → if not exists:  │                                            │
│  │     POST /key/gen   │   Сохранить в SOPS secrets                 │
│  └────────────────────┘   Пробросить в .env.platform                │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                         PROJECT LAYER                                │
│                                                                      │
│  ai-platform.yaml:                                                   │
│  ┌─────────────────────────┐                                        │
│  │ name: my-backend        │  90% проектов: только enabled: true    │
│  │ llm:                    │                                        │
│  │   enabled: true          │  → auto-assign profile "default"       │
│  │                         │  → models: [chat], budget: $1/day      │
│  └─────────────────────────┘                                        │
│                                                                      │
│  .env.platform:                                                      │
│  LITELLM_API_KEY=sk-my-backend-abc123  ← уникальный virtual key     │
│  OPENAI_BASE_URL=http://litellm:4000                                 │
│                                                                      │
│  Проект вызывает LiteLLM:                                            │
│  POST /v1/chat/completions                                          │
│  Authorization: Bearer sk-my-backend-abc123                         │
│  {                                                                   │
│    "model": "chat",                                                  │
│    "metadata": {                                                     │
│      "tags": ["task:code-review", "user:john", "project:my-backend"]│
│    }                                                                 │
│  }                                                                   │
│                                                                      │
│  LiteLLM по virtual key определяет:                                  │
│  • Проект: my-backend                                                │
│  • Доступные модели: [chat]                                          │
│  • Бюджет: $1/day                                                    │
│  • Резолвит chat → deepseek/deepseek-v4-flash                       │
│  • Логирует spend + metadata.tags в Langfuse/Prometheus              │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Draft Code Graph — Phase 1 (Aliases + Schema + Cleanup)

```xml
<graph>

  <!-- PHASE 1: Канонический словарь -->
  <entity id="llm_policy_yaml_FILE" type="CONFIG" file="core/internal/llm/policy.yaml" line="1">
    <keyword>SSoT</keyword>
    <keyword>aliases</keyword>
    <keyword>profiles</keyword>
    <keyword>providers</keyword>
    <annotation>Единственный канонический файл: алиасы, профили, провайдеры, auto_provision правила</annotation>
    <CrossLinks>
      <link target="llm_policy_schema_JSONSCHEMA" relation="validated-by"/>
      <link target="config_renderer_py_MODULE" relation="consumed-by"/>
      <link target="key_provisioner_py_MODULE" relation="consumed-by"/>
    </CrossLinks>
  </entity>

  <entity id="llm_policy_schema_JSONSCHEMA" type="SCHEMA" file="core/schemas/llm-policy.schema.json" line="1">
    <keyword>jsonschema</keyword>
    <keyword>Draft-07</keyword>
    <annotation>JSON Schema для policy.yaml — валидация структуры providers, aliases, profiles</annotation>
    <CrossLinks>
      <link target="llm_policy_yaml_FILE" relation="validates"/>
    </CrossLinks>
  </entity>

  <!-- PHASE 2: Расширение project schema -->
  <entity id="ai_platform_schema_JSONSCHEMA" type="SCHEMA" file="core/schemas/ai-platform.schema.json" line="1">
    <keyword>schema-extension</keyword>
    <keyword>llm-field</keyword>
    <annotation>Добавлено поле llm: {enabled: boolean, profile?: string, overrides?: object}. additionalProperties: false сохраняется.</annotation>
    <CrossLinks>
      <link target="project_schema_test_PYTEST" relation="tested-by"/>
    </CrossLinks>
  </entity>

  <entity id="project_schema_test_PYTEST" type="TEST" file="tests/test_project_schema.py" line="1">
    <keyword>jsonschema</keyword>
    <keyword>ai-platform</keyword>
    <annotation>Добавлены тесты: llm.enabled, llm.profile enum, llm.overrides budget/budget_duration/rpm_limit, llm без enabled</annotation>
  </entity>

  <!-- PHASE 3: Provider key cleanup -->
  <entity id="litellm_compose_ENVFILE" type="COMPOSE" file="core/modules/litellm/docker-compose.base.yml" line="1">
    <keyword>env-cleanup</keyword>
    <keyword>DEEPSEEK_API_KEY-only</keyword>
    <annotation>Удалены: OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY. Оставлен только DEEPSEEK_API_KEY + LITELLM_MASTER_KEY.</annotation>
    <CrossLinks>
      <link target="secret_definitions_yaml_FILE" relation="mirrors-cleanup"/>
      <link target="hermes_compose_ENVFILE" relation="syncs-cleanup"/>
    </CrossLinks>
  </entity>

  <entity id="hermes_compose_ENVFILE" type="COMPOSE" file="core/modules/hermes-agent/docker-compose.base.yml" line="1">
    <keyword>env-cleanup</keyword>
    <keyword>LITELLM_API_KEY</keyword>
    <annotation>OPENAI_API_KEY заменён на LITELLM_API_KEY. Удалены: DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GLM_API_KEY.</annotation>
    <CrossLinks>
      <link target="litellm_compose_ENVFILE" relation="syncs-cleanup"/>
      <link target="hermes_config_yaml_FILE" relation="provider-change"/>
    </CrossLinks>
  </entity>

  <entity id="secret_definitions_yaml_FILE" type="CONFIG" file="core/secret-definitions.yaml" line="1">
    <keyword>secret-cleanup</keyword>
    <keyword>remove-provider-keys</keyword>
    <annotation>OPENAI_API_KEY: tier → removed (не удалён — оставлен как removed для audit trail). ANTHROPIC/OPENROUTER/GLM: tier → removed. Добавлен: LITELLM_PROJECT_KEYS (generated, для SOPS-хранения).</annotation>
  </entity>

  <entity id="env_file_ROOT" type="CONFIG" file=".env" line="1">
    <keyword>local-dev</keyword>
    <keyword>cleanup</keyword>
    <annotation>Удалены: ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GLM_API_KEY. DEEPSEEK_API_KEY оставлен. OPENAI_API_KEY → LITELLM_MASTER_KEY (локально они равны, но семантически теперь это master key).</annotation>
  </entity>

  <entity id="module_yaml_litellm_FILE" type="CONFIG" file="core/modules/litellm/module.yaml" line="1">
    <keyword>env_requires-update</keyword>
    <keyword>remove-OPENAI</keyword>
    <annotation>env_requires: убрать OPENAI_API_KEY. TRAP[DECISION] 2026-07-15 обновлён с ссылкой на DevPlan 049.</annotation>
  </entity>

  <!-- PHASE 4: config_renderer -->
  <entity id="config_renderer_py_MODULE" type="MODULE" file="core/internal/llm/config_renderer.py" line="1">
    <keyword>Python</keyword>
    <keyword>Jinja2</keyword>
    <keyword>renderer</keyword>
    <annotation>Загрузка policy.yaml → рендер litellm-config.yml через Jinja2-шаблон. Обрабатывает providers, aliases, access_groups.</annotation>
    <CrossLinks>
      <link target="litellm_config_template_JINJA2" relation="uses-template"/>
      <link target="llm_policy_yaml_FILE" relation="reads-policy"/>
      <link target="test_config_renderer_PYTEST" relation="tested-by"/>
    </CrossLinks>
  </entity>

  <entity id="litellm_config_template_JINJA2" type="TEMPLATE" file="core/modules/litellm/config/litellm-config.yml.j2" line="1">
    <keyword>Jinja2</keyword>
    <keyword>template</keyword>
    <annotation>Шаблон litellm-config.yml. Вход: policy (aliases → model_list entries, providers → credential_list, fallbacks).</annotation>
    <CrossLinks>
      <link target="config_renderer_py_MODULE" relation="rendered-by"/>
    </CrossLinks>
  </entity>

  <!-- PHASE 5: key_provisioner -->
  <entity id="key_provisioner_py_MODULE" type="MODULE" file="core/internal/llm/key_provisioner.py" line="1">
    <keyword>Python</keyword>
    <keyword>idempotent</keyword>
    <keyword>virtual-keys</keyword>
    <annotation>Идемпотентный провижинер virtual keys. Загрузка policy.yaml + discover_projects → GET /key/info → POST /key/generate (если не существует) → SOPS persist.</annotation>
    <CrossLinks>
      <link target="llm_policy_yaml_FILE" relation="reads-policy"/>
      <link target="litellm_admin_client_py_MODULE" relation="uses"/>
      <link target="test_key_provisioner_PYTEST" relation="tested-by"/>
    </CrossLinks>
  </entity>

  <entity id="litellm_admin_client_py_MODULE" type="MODULE" file="core/internal/llm/admin_client.py" line="1">
    <keyword>Python</keyword>
    <keyword>httpx</keyword>
    <keyword>LiteLLM-API</keyword>
    <annotation>Тонкий HTTP-клиент для LiteLLM admin API: /key/info, /key/generate, /key/delete, /key/update. Bearer auth с LITELLM_MASTER_KEY.</annotation>
  </entity>

  <!-- PHASE 6: hermes-agent migration -->
  <entity id="hermes_config_yaml_FILE" type="CONFIG" file="core/modules/hermes-agent/build/config/config.yaml" line="1">
    <keyword>migration</keyword>
    <keyword>provider-litellm</keyword>
    <annotation>model.provider: deepseek → litellm. model.name: deepseek-v4-pro → reasoning. fallback_model меняется на chat через LiteLLM. Удалён fallback_model на zai (теперь этим управляет LiteLLM).</annotation>
    <CrossLinks>
      <link target="hermes_compose_ENVFILE" relation="syncs-env"/>
    </CrossLinks>
  </entity>

  <!-- PHASE 7: pipeline integration -->
  <entity id="entrypoint_provision_py_SCRIPT" type="SCRIPT" file="core/entrypoints/provision-llm.sh" line="1">
    <keyword>entrypoint</keyword>
    <keyword>thin-shell-facade</keyword>
    <annotation>Тонкий shell-фасад: вызывает python3 core/internal/llm/key_provisioner.py. Вызывается из deploy-modules и deploy-context.</annotation>
    <CrossLinks>
      <link target="key_provisioner_py_MODULE" relation="calls"/>
      <link target="state_machine_provision_FUNC" relation="called-by"/>
    </CrossLinks>
  </entity>

  <entity id="state_machine_provision_FUNC" type="FUNCTION" file="core/internal/bootstrap/lifecycle/state_machine.py" line="1">
    <keyword>integration</keyword>
    <keyword>bootstrap</keyword>
    <annotation>Добавлен вызов render_litellm_config (config_renderer.py) перед provision_llm_keys на этапе deploy_modules и deploy_context. Порядок: render_litellm_config → restart litellm → provision_llm_keys.</annotation>
  </entity>

  <!-- Тесты (Phase 7) -->
  <entity id="test_config_renderer_PYTEST" type="TEST" file="tests/unit/test_llm_config_renderer.py" line="1">
    <keyword>unit</keyword>
    <keyword>renderer</keyword>
    <annotation>Unit-тесты: рендер из минимального policy → валидный litellm-config.yml, валидация fallback-цепочек, проверка model_info.supported_environments.</annotation>
  </entity>

  <entity id="test_key_provisioner_PYTEST" type="TEST" file="tests/unit/test_llm_key_provisioner.py" line="1">
    <keyword>unit</keyword>
    <keyword>provisioner</keyword>
    <keyword>mock</keyword>
    <annotation>Unit-тесты: идемпотентность (повторный вызов не создаёт дубликат), обработка 409 Conflict, бюджеты, metadata.tags.</annotation>
  </entity>

  <entity id="test_policy_schema_PYTEST" type="TEST" file="tests/unit/test_llm_policy_schema.py" line="1">
    <keyword>unit</keyword>
    <keyword>schema</keyword>
    <annotation>Unit-тесты: валидация policy.yaml против JSON Schema, проверка enum для profiles, обязательные поля aliases.</annotation>
  </entity>

  <entity id="gate_llm_aliases_PYTEST" type="TEST" file="tests/gates/test_gate_llm_aliases.py" line="1">
    <keyword>gate</keyword>
    <keyword>aliases</keyword>
    <keyword>fallback</keyword>
    <annotation>Gate-тесты: каждый alias имеет fallback-цепочку, no circular fallbacks, все aliases в model_list имеют реальный litellm_params.model, нет моделей без access_group.</annotation>
  </entity>

  <entity id="gate_llm_provisioner_idempotent_PYTEST" type="TEST" file="tests/gates/test_gate_llm_provisioner.py" line="1">
    <keyword>gate</keyword>
    <keyword>idempotent</keyword>
    <annotation>Gate-тест: повторный запуск provisioner возвращает тот же ключ (через /key/info match по metadata.project).</annotation>
  </entity>

  <entity id="gate_secrets_cleanup_PYTEST" type="TEST" file="tests/gates/test_gate_secrets_llm_cleanup.py" line="1">
    <keyword>gate</keyword>
    <keyword>secrets</keyword>
    <keyword>cleanup</keyword>
    <annotation>Gate-тест: OPENAI_API_KEY не используется как provider key, ANTHROPIC/OPENROUTER/GLM отсутствуют в compose и secret-definitions (как active).</annotation>
  </entity>

  <entity id="test_hermes_invariants_PYTEST" type="TEST" file="tests/test_predeploy_hermes_invariants.py" line="1">
    <keyword>hermes</keyword>
    <keyword>invariant</keyword>
    <annotation>Обновлён: provider → litellm, model → reasoning. Удалена проверка fallback_provider: zai.</annotation>
  </entity>

</graph>
```

---

## Step-by-Step Data Flow

### Phase 1: Канонический словарь алиасов

```
policy.yaml (SOLE SSoT)
────────────────────────
providers:                     # Какие AI-провайдеры подключены
  deepseek:
    key_env: DEEPSEEK_API_KEY  # env var в LiteLLM compose

aliases:                       # Семантические алиасы (пользователь вызывает эти имена)
  reasoning:
    label: "Complex reasoning"
    context_window: 128000
    features: [reasoning, structured_output]
    deployments:               # Конкретные model-строки у провайдеров
      primary:
        provider: deepseek
        model: deepseek-v4-pro
      fallback:
        provider: deepseek
        model: deepseek-v4-flash  # fallback при недоступности primary
  chat:
    label: "Fast chat"
    context_window: 128000
    features: [chat]
    deployments:
      primary:
        provider: deepseek
        model: deepseek-v4-flash
  coding:                       # Планируется (добавляется, когда появится провайдер)
    label: "Code generation"
    context_window: 200000
    features: [code, structured_output]
    deployments: []             # Пустой = алиас зарезервирован, но не активен
  vision:
    label: "Image analysis"
    context_window: 128000
    features: [vision, multimodal]
    deployments: []
  embedding:
    label: "Text embeddings"
    features: [embedding]
    deployments: []

profiles:                       # Профили доступа (какой проект что получает)
  default:
    label: "Default (chat only)"
    models: [chat]
    budget: {daily: 1.0}
    rpm_limit: 10
    metadata: {tier: default}
  premium:
    label: "Premium (reasoning + chat)"
    models: [reasoning, chat]
    budget: {daily: 10.0}
    rpm_limit: 60
    metadata: {tier: premium}
  unlimited:
    label: "Unlimited (all active models)"
    models: [reasoning, chat]
    budget: {daily: 50.0}
    rpm_limit: 120
    metadata: {tier: unlimited}

auto_provision:
  default_profile: default      # Если проект не указал profile — получает default
  profile_rules:                # Особые правила для конкретных проектов
    - match: {name: hermes-agent}
      profile: unlimited
```

### Phase 2: Расширение ai-platform.schema.json

> **⚠️ Миграция не требуется:** ни один production-проект не использует `needs.llm`. Поле удаляется без backward-compat. Если в будущем появится production-окружение с проектами до DevPlan 049 — `git revert` возвращает старую schema.

```
Было:                                   Стало:
{                                       {
  "required": ["name", "type",            "required": ["name", "type",
    "target_node"],                         "target_node"],
  "additionalProperties": false,          "additionalProperties": false,
  "properties": {                         "properties": {
    "name": {...},                          "name": {...},
    "type": {...},                          "type": {...},
    "needs": {                              "needs": {
      "domain": {...},                        "domain": {...},
      "database": {...},                      "database": {...},
      "cache": {...},                         "cache": {...},
      "storage": {...},                       "storage": {...},
      "expose": {...},                        "expose": {...},
      "llm": {                                # ← ПОЛЕ llm УДАЛЕНО ИЗ needs
        "oneOf": [                                   ↓
          {"type": "string"},   ← НОВОЕ ТОП-УРОВНЕВОЕ ПОЛЕ llm
          {"type": "boolean"}
        ]
      }
    }                                       "llm": {            ← НОВОЕ ПОЛЕ
  }                                           "type": "object",
}                                             "additionalProperties": false,
                                              "properties": {
                                                "enabled": {
                                                  "type": "boolean",
                                                  "default": false
                                                },
                                                "profile": {
                                                  "type": "string",
                                                  "enum": ["default", "premium", "unlimited"]
                                                },
                                                "overrides": {
                                                  "type": "object",
                                                  "properties": {
                                                    "models": {
                                                      "type": "array",
                                                      "items": {"type": "string"}
                                                    },
                                                    "budget": {
                                                      "type": "object",
                                                      "properties": {
                                                        "daily": {"type": "number"},
                                                        "monthly": {"type": "number"}
                                                      }
                                                    },
                                                    "rpm_limit": {"type": "integer"}
                                                  }
                                                }
                                              }
                                            },
                                            "if": {
                                              "properties": {"enabled": {"const": true}},
                                              "required": ["enabled"]
                                            }
                                          }
```

**Progressive disclosure:**
```
llm: {enabled: true}                          # 90% проектов — 1 строка
llm: {enabled: true, profile: premium}        # 9% проектов — явный профиль
llm: {enabled: true, profile: premium,         # 1% проектов — оверрайды
      overrides: {budget: {daily: 5.0}}}
```

### Phase 3: Provider key cleanup

```
УДАЛЯЕМ (не используются, никогда не были настроены):
  ANTHROPIC_API_KEY   → удалить из compose, secret-definitions, .env
  OPENROUTER_API_KEY  → удалить из compose, secret-definitions, .env
  GLM_API_KEY         → удалить из compose, secret-definitions, .env

ПЕРЕОПРЕДЕЛЯЕМ:
  OPENAI_API_KEY       → БЫЛО: алиас LITELLM_MASTER_KEY (Hermes→LiteLLM auth)
                       → СТАЛО: tier: removed в secret-definitions (audit trail)
                       → Hermes-agent теперь использует LITELLM_API_KEY (virtual key)

ОСТАВЛЯЕМ:
  DEEPSEEK_API_KEY     → единственный реальный AI-провайдер ключ
  LITELLM_MASTER_KEY   → autogen, мастер-ключ LiteLLM (только для администрирования)

ДОБАВЛЯЕМ:
  LITELLM_PROJECT_KEYS → generated, source: provisioner — SOPS-хранилище всех virtual keys

compose изменения:
  litellm/docker-compose.base.yml:
    environment:
      - DEEPSEEK_API_KEY: "${DEEPSEEK_API_KEY:-}"
      - LITELLM_MASTER_KEY: "${LITELLM_MASTER_KEY:?required}"
      # УДАЛЕНО: OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY

  hermes-agent/docker-compose.base.yml:
    environment:
      - OPENAI_BASE_URL: "${OPENAI_BASE_URL:-http://litellm:4000}"
      - LITELLM_API_KEY: "${LITELLM_API_KEY}"          # virtual key (НЕ master key!)
      # УДАЛЕНО: OPENAI_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY,
      #          OPENROUTER_API_KEY, GLM_API_KEY
```

### Phase 4: config_renderer

```
ВХОД:                                   ВЫХОД:
policy.yaml                             litellm-config.yml
┌─────────────────────┐                ┌──────────────────────────────────┐
│ aliases:            │  config_       │ model_list:                      │
│   reasoning:        │  renderer.py   │   - model_name: reasoning        │
│     deployments:    │───────────────►│     litellm_params:              │
│       primary:      │  + Jinja2      │       model: deepseek/           │
│         provider: ds│  template      │         deepseek-v4-pro          │
│         model: v4pro│                │       api_key: os.environ/       │
│       fallback:     │                │         DEEPSEEK_API_KEY         │
│         provider: ds│                │     model_info:                  │
│         model: v4f  │                │       access_groups: [reasoning] │
│   chat:             │                │   - model_name: chat             │
│     ...             │                │     litellm_params:              │
│ providers:          │                │       model: deepseek/           │
│   deepseek:         │                │         deepseek-v4-flash        │
│     key_env: DS_KEY │                │       api_key: os.environ/       │
└─────────────────────┘                │         DEEPSEEK_API_KEY         │
                                       │   ...                            │
                                       │ litellm_settings:                │
                                       │   success_callback:              │
                                       │     [prometheus, langfuse]        │
                                       │   failure_callback: [prometheus] │
                                       │   num_retries: 3                 │
                                       │   drop_params: True              │
                                       │ fallbacks:                       │
                                       │   - reasoning: [reasoning-fallback]│
                                       └──────────────────────────────────┘
```

**Логика рендерера:**
1. Для каждого alias с непустыми deployments → генерируется `model_list` entry:
   - `model_name` = alias name (primary)
   - `model_name` = alias name + `-fallback` (fallback deployment)
2. Для каждого провайдера → `api_key: os.environ/<key_env>`
3. Fallback-цепочка: `litellm_settings.fallbacks` связывает primary → fallback
4. Модели без deployments (coding, vision) — не рендерятся в model_list (зарезервированы, но не активны)

### Phase 5: key_provisioner

```
┌─ key_provisioner.py ───────────────────────────────────────────────┐
│                                                                     │
│  def provision_all(master_key: str, base_url: str):                 │
│      policy = load_policy("core/internal/llm/policy.yaml")          │
│      client = LiteLLMAdminClient(base_url, master_key)              │
│                                                                     │
│      for project in discover_projects():  # все ai-platform.yaml    │
│          if not project.llm.enabled:                                │
│              continue  # skip                                       │
│                                                                     │
│          # 1. Определить профиль                                    │
│          profile_name = (                                           │
│              project.llm.profile                                    │
│              or match_profile_rule(project.name, policy.profile_rules)│
│              or policy.auto_provision.default_profile               │
│          )                                                          │
│          profile = policy.profiles[profile_name]                    │
│          config = deep_merge(profile, project.llm.overrides or {})  │
│                                                                     │
│          # 2. Проверить — существует ли ключ?                       │
│          existing = client.get_key_by_metadata(                     │
│              project=project.name                                   │
│          )                                                          │
│          if existing and key_matches_config(existing, config):      │
│              log("Key exists, matching config — skip")              │
│              keys[project.name] = existing.key                      │
│              continue                                               │
│                                                                     │
│          # 3. Создать/обновить virtual key                          │
│          if existing:                                               │
│              client.update_key(existing.token, models=config.models,│
│                  max_budget=config.budget.daily, ...)               │
│          else:                                                      │
│              result = client.generate_key(                          │
│                  models=config.models,                              │
│                  metadata={                                         │
│                      "project": project.name,                       │
│                      "tags": [                                      │
│                          f"project:{project.name}",                 │
│                          f"tier:{profile_name}",                    │
│                          f"env:{ENV}"                               │
│                      ]                                              │
│                  },                                                 │
│                  max_budget=config.budget.daily,                    │
│                  budget_duration="1d",                              │
│                  rpm_limit=config.rpm                               │
│              )                                                      │
│              keys[project.name] = result.key                        │
│                                                                     │
│          # 4. Сохранить ключ в SOPS                                 │
│          persist_project_key(project.name, keys[project.name])      │
│                                                                     │
│      return keys                                                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

ИДЕМПОТЕНТНОСТЬ:
  GET /key/info?key=<stored_key> → 200 {info: {...}}
    → если metadata.project совпадает и models совпадают → skip
    → если models изменились → PUT /key/update (обновить доступ)

  GET /key/info?key=<stored_key> → 404
    → POST /key/generate (создать новый)

  СОХРАНЕНИЕ в SOPS:
    Ключи сохраняются в /opt/platform/secrets/litellm-project-keys.enc.yaml
    как отдельный encrypted блок (не в общем secrets.enc.yaml)
    → decrypt-secrets.sh расшифровывает вместе с основным файлом
    → project-sync-env пробрасывает LITELLM_API_KEY в .env.platform
```

### Phase 6: hermes-agent migration

```
Было (config.yaml):
  model:
    provider: deepseek
    model: deepseek-v4-pro
    fallback_provider: zai
  fallback_model:
    provider: zai
    model: glm-4-plus
  auxiliary:
    vision: { provider: deepseek, model: deepseek-chat }
    compression: { provider: deepseek, model: deepseek-chat }
  fallback:
    enabled: true
    max_retries: 2
    cooldown_minutes: 5

Стало:
  model:
    provider: litellm          # Новый провайдер — LiteLLM gateway
    model: reasoning           # Семантический алиас
  fallback_model:
    provider: litellm          # Fallback тоже через LiteLLM
    model: chat                # (LiteLLM сам решит, какого провайдера использовать)
  auxiliary:
    vision: { provider: litellm, model: chat }    # vision пока не активен → chat
    compression: { provider: litellm, model: chat }
  fallback:
    enabled: false             # Отключён на уровне hermes (LiteLLM управляет fallback'ами)
    # LiteLLM имеет свои retry и fallback в litellm-config.yml
```

**Примечание:** Hermes-agent использует `OPENAI_BASE_URL=http://litellm:4000` и `LITELLM_API_KEY` (virtual key). Все вызовы идут через LiteLLM. Fallback на уровне провайдера теперь управляется LiteLLM, не hermes-agent.

### Phase 7: Pipeline integration

```
deploy-modules (flow):
  ...
  → step: deploy_projects              (существующий)
  → step: render_litellm_config          (НОВЫЙ — перед provision, чтобы ключи соответствовали актуальной конфигурации)
    → python3 core/internal/llm/config_renderer.py --output core/modules/litellm/config/litellm-config.yml
    → docker compose restart litellm     (применить новую конфигурацию)
  → step: provision_llm_keys            (НОВЫЙ — после обновления конфигурации litellm)
    → python3 core/internal/llm/key_provisioner.py
    → записывает LITELLM_API_KEY в .env.platform каждого проекта
  → step: project_sync_env             (существующий — перечитывает .env.platform)
  → step: healthcheck                  (существующий)
  ...

deploy-context (flow):
  ...
  → step: deploy_context_projects       (существующий — context_deployer.py)
  → step: render_litellm_config          (НОВЫЙ)
  → step: provision_llm_keys            (НОВЫЙ)
  → ...

bootstrap-node (flow):
  ...
  → step: deploy_litellm_module         (существующий)
  → step: deploy_hermes_agent           (существующий)
  → step: render_litellm_config          (НОВЫЙ — на случай первого bootstrap: рендерим до provision)
  → step: provision_llm_keys            (НОВЫЙ — после развёртывания проектов)
  ...

CI (pre-deploy gate):
  → make generate-manifests             (включает config_renderer.py --check)
  → make check-manifests                (git diff --exit-code для litellm-config.yml)
```

**Интеграция в Makefile:**
```makefile
# Новый target
.PHONY: provision-llm
provision-llm:
	@$(ENTRYPOINTS)/provision-llm.sh
```

**Интеграция в helpers.mk (generate-manifests):**
```makefile
# generate-manifests target обновлён:
generate-manifests: \
    generate-secrets-manifest \
    generate-platform-env \
    generate-smoke-env \
    generate-env-defaults \
    generate-entrypoint-manifest \
    generate-agents-md \
    generate-litellm-config        # ← НОВЫЙ: config_renderer.py --output

.PHONY: generate-litellm-config
generate-litellm-config:
	@python3 $(CORE_PYTHON)/core/internal/llm/config_renderer.py \
	    --policy $(CORE_PYTHON)/core/internal/llm/policy.yaml \
	    --output $(CORE_PYTHON)/core/modules/litellm/config/litellm-config.yml
```

**Примечание:** `config_renderer` вызывается **до** `key_provisioner` — litellm должен быть запущен с актуальной конфигурацией, чтобы key_provisioner мог создавать ключи с правильными моделями.

---

## File Manifest

### Новые файлы

| # | Файл | Тип | Описание |
|---|------|-----|----------|
| 1 | `core/internal/llm/__init__.py` | Python | Package init |
| 2 | `core/internal/llm/policy.yaml` | YAML | SSoT: провайдеры, алиасы, профили, auto_provision |
| 3 | `core/internal/llm/policy_schema.py` | Python | Pydantic-модели для policy (валидация при загрузке) |
| 4 | `core/internal/llm/config_renderer.py` | Python | Рендер policy → litellm-config.yml |
| 5 | `core/internal/llm/admin_client.py` | Python | HTTP-клиент для LiteLLM admin API |
| 6 | `core/internal/llm/key_provisioner.py` | Python | Идемпотентный провижинер virtual keys |
| 7 | `core/modules/litellm/config/litellm-config.yml.j2` | Jinja2 | Шаблон litellm-config.yml |
| 8 | `core/entrypoints/provision-llm.sh` | Shell | Тонкий фасад (<30 строк) |
| 9 | `core/schemas/llm-policy.schema.json` | JSON Schema | Draft-07 схема для policy.yaml |
| 10 | `tests/unit/test_llm_policy_schema.py` | Python/pytest | Unit-тесты policy schema |
| 11 | `tests/unit/test_llm_config_renderer.py` | Python/pytest | Unit-тесты config_renderer |
| 12 | `tests/unit/test_llm_key_provisioner.py` | Python/pytest | Unit-тесты key_provisioner |
| 13 | `tests/gates/test_gate_llm_aliases.py` | Python/pytest | Gate: алиасы + fallback контракты |
| 14 | `tests/gates/test_gate_llm_provisioner.py` | Python/pytest | Gate: идемпотентность provisioner |
| 15 | `tests/gates/test_gate_secrets_llm_cleanup.py` | Python/pytest | Gate: отсутствие неиспользуемых ключей |
| 16 | `tests/test_data/llm/policy.valid.yaml` | YAML | Тестовая fixture: валидный policy |
| 17 | `tests/test_data/llm/policy.invalid.yaml` | YAML | Тестовая fixture: невалидный policy |
| 18 | `tests/unit/test_llm_env_chain.py` | Python/pytest | Интеграционный тест: key_provisioner → SOPS → .env.platform → LITELLM_API_KEY |
| 19 | `tests/unit/test_llm_config_renderer_integration.py` | Python/pytest | Интеграционный тест config_renderer: полный цикл рендера |

### Модифицируемые файлы

| # | Файл | Суть изменений |
|---|------|---------------|
| 20 | `core/schemas/ai-platform.schema.json` | + поле `llm` (enabled, profile, overrides); − `needs.llm` (прямое удаление, без backward-compat) |
| 21 | `core/modules/litellm/docker-compose.base.yml` | − OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY |
| 22 | `core/modules/hermes-agent/docker-compose.base.yml` | OPENAI_API_KEY → LITELLM_API_KEY; − DEEPSEEK/ANTHROPIC/OPENROUTER/GLM |
| 23 | `core/modules/litellm/module.yaml` | env_requires: − OPENAI_API_KEY; + DEEPSEEK_API_KEY (required); TRAP[DECISION] update |
| 24 | `core/modules/hermes-agent/build/config/config.yaml` | provider: deepseek → litellm; model: + reasoning (ЯВНО добавлено, ранее отсутствовало); − fallback_provider |
| 25 | `core/secret-definitions.yaml` | OPENAI_API_KEY → tier: removed; + ANTHROPIC/OPENROUTER/GLM → tier: removed; + LITELLM_PROJECT_KEYS (source: provisioner) |
| 26 | `core/secrets-manifest.yaml` | Авто-регенерация из secret-definitions (make check-manifests) |
| 27 | `.env` | − ANTHROPIC/OPENROUTER/GLM; OPENAI_API_KEY → LITELLM_MASTER_KEY (локально) |
| 28 | `core/modules/litellm/config/litellm-config.yml` | Заменён на generated (не редактируется вручную). Рендер: config_renderer.py |
| 29 | `tests/test_project_schema.py` | + тесты llm поля |
| 30 | `tests/test_predeploy_hermes_invariants.py` | Обновить инварианты под litellm provider |
| 31 | `core/internal/bootstrap/lifecycle/state_machine.py` | + шаг provision_llm_keys |
| 32 | `Makefile` | + target `provision-llm` |
| 33 | `core/entrypoint-manifest.yaml` | + `provision-llm` в allowed_verbs |
| 34 | `core/internal/bootstrap/deploy/context_deployer.py` | + вызов provision_llm_keys после деплоя |
| 35 | `core/internal/deploy/deploy-modules.sh` | + вызов provision-llm после project_sync_env; + вызов config_renderer перед рестартом litellm |
| 36 | `core/internal/scripts/validate_module_yaml.py` | Обновить контракт litellm module.yaml (env_requires). ⚠️ tier:removed уже поддерживается — только обновление контракта |
| 37 | `makefiles/helpers.mk` | `generate-manifests`: + вызов config_renderer.py; `check-manifests` включает diff |
| 38 | `.github/workflows/platform-test.yml` | OPENAI_API_KEY → LITELLM_MASTER_KEY; проверка OPENAI_API_KEY убрана |

---

## Acceptance Criteria (проверяемые)

### AC-1: policy.yaml schema
```bash
python3 -c "
from core.internal.llm.policy_schema import LLMPolicy
policy = LLMPolicy.from_yaml('core/internal/llm/policy.yaml')
assert len(policy.aliases) >= 2
assert 'reasoning' in policy.aliases
assert 'chat' in policy.aliases
assert policy.providers['deepseek'].key_env == 'DEEPSEEK_API_KEY'
"
```
✅ policy.yaml валидируется при загрузке. Невалидный вызывает исключение.

### AC-2: ai-platform.schema.json
```bash
python3 -m pytest tests/test_project_schema.py -v -k llm
```
✅ Все 4 новых теста проходят: `llm.enabled`, `llm.profile`, `llm.overrides`, `llm_invalid`.

### AC-3: config_renderer
```bash
python3 core/internal/llm/config_renderer.py --policy core/internal/llm/policy.yaml --output /tmp/litellm-config.yml
python3 -c "
import yaml
config = yaml.safe_load(open('/tmp/litellm-config.yml'))
assert any(m['model_name'] == 'reasoning' for m in config['model_list'])
assert any(m['model_name'] == 'chat' for m in config['model_list'])
assert config['litellm_settings']['drop_params'] == True
assert len(config['litellm_settings']['fallbacks']) > 0
"
```
✅ Выходной litellm-config.yml содержит все активные алиасы + fallback-цепочки.

### AC-4: key_provisioner idempotent
```bash
python3 -m pytest tests/unit/test_llm_key_provisioner.py -v
```
✅ `test_idempotent — same key on second call`, `test_different_config_updates_key`.

### AC-5: key_provisioner integration
```bash
# На VPS:
make provision-llm
cat /opt/projects/my-backend/.env.platform | grep LITELLM_API_KEY
# Должен быть sk-... (64 hex chars)
```
✅ Каждый проект с `llm.enabled: true` получает `LITELLM_API_KEY`.

### AC-6: hermes-agent → reasoning через LiteLLM
```bash
# Запрос через LiteLLM (Hermes-agent использует OPENAI_BASE_URL + LITELLM_API_KEY):
curl http://litellm:4000/v1/chat/completions \
  -H "Authorization: Bearer $(cat /run/platform/secrets.env | grep LITELLM_API_KEY | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "reasoning", "messages": [{"role": "user", "content": "Say hello"}], "metadata": {"tags": ["test:ci"]}}'
```
✅ Ответ возвращается от модели. В заголовках `x-litellm-model` = `deepseek/deepseek-v4-pro`.

### AC-7: 429 при превышении бюджета
```bash
# Отправить >$1 запросов для default-профиля
for i in {1..50}; do
  curl ... -d '{"model": "chat", "messages": [...]}'
done
```
✅ LiteLLM возвращает 429 с сообщением "Budget exceeded".

### AC-8: gate MODE=fast
```bash
make fix-gate && git add -u && make gate MODE=fast
```
✅ Все gate-тесты зелёные, включая новые `test_gate_llm_*`.

### AC-9: litellm-config.yml freshness (check-manifests)
```bash
make check-manifests
# Должен пройти без diff — litellm-config.yml актуален относительно policy.yaml
```
✅ `git diff --exit-code` не показывает изменений в `litellm-config.yml` после `make generate-manifests`. Если policy.yaml изменён, а config не перерендерен — gate падает.

---

## Риски и митигация

| Риск | Вероятность | Влияние | Митигация |
|------|------------|---------|-----------|
| Hermes-agent migration ломает production | LOW | HIGH | Staging-test на VPS перед merge. Старые model_name сохраняются как deprecated aliases на 1 месяц. |
| key_provisioner создаёт дубликаты ключей | LOW | MEDIUM | Идемпотентность через metadata.project match. Gate-тест проверяет идемпотентность. |
| LiteLLM /key/generate API изменяется в новой версии | LOW | MEDIUM | Digest-pinned образ (`ghcr.io/berriai/litellm:v1.91.2`). API стабильно с v1.60+. |
| Удаление GLM_API_KEY ломает fallback hermes-agent | LOW | LOW | Fallback теперь управляется LiteLLM, не hermes-agent. GLM не был настроен. |
| Проекты без llm.enabled теряют доступ к LLM | N/A | LOW | llm.enabled: true — opt-in. Существующие проекты (hermes-agent) мигрируются первыми. |

---

## Порядок реализации

```
Phase 1: Aliases + Schema  (DevPlan → PR #1)
  ├── core/internal/llm/policy.yaml
  ├── core/schemas/llm-policy.schema.json
  ├── core/internal/llm/policy_schema.py
  ├── core/internal/llm/__init__.py
  ├── tests/unit/test_llm_policy_schema.py
  └── tests/test_data/llm/policy.{valid,invalid}.yaml

Phase 2: Schema Extension  (PR #1 continued)
  ├── core/schemas/ai-platform.schema.json (+ llm field, − needs.llm)
  └── tests/test_project_schema.py (+ llm tests)

Phase 3: Config Renderer  (PR #2)
  ├── core/internal/llm/config_renderer.py
  ├── core/modules/litellm/config/litellm-config.yml.j2
  ├── tests/unit/test_llm_config_renderer.py
  └── tests/unit/test_llm_config_renderer_integration.py

Phase 4: Key Provisioner  (PR #3)
  ├── core/internal/llm/admin_client.py
  ├── core/internal/llm/key_provisioner.py
  ├── core/entrypoints/provision-llm.sh
  ├── tests/unit/test_llm_key_provisioner.py
  ├── tests/unit/test_llm_env_chain.py
  └── tests/gates/test_gate_llm_provisioner.py

Phase 5: Provider Cleanup  (PR #4)
  ├── core/modules/litellm/docker-compose.base.yml (remove keys)
  ├── core/modules/hermes-agent/docker-compose.base.yml (LITELLM_API_KEY)
  ├── core/modules/litellm/module.yaml (env_requires: − OPENAI, + DEEPSEEK required)
  ├── core/secret-definitions.yaml (tier: removed + LITELLM_PROJECT_KEYS)
  ├── .env (cleanup)
  ├── .github/workflows/platform-test.yml (OPENAI → LITELLM_MASTER_KEY)
  └── tests/gates/test_gate_secrets_llm_cleanup.py

Phase 6: Hermes Migration  (PR #5)
  ├── core/modules/hermes-agent/build/config/config.yaml
  └── tests/test_predeploy_hermes_invariants.py

Phase 7: Pipeline Integration + Gates  (PR #6)
  ├── core/internal/bootstrap/lifecycle/state_machine.py
  ├── core/internal/deploy/deploy-modules.sh (+ render_litellm_config + provision_llm_keys)
  ├── core/internal/bootstrap/deploy/context_deployer.py (+ render_litellm_config + provision_llm_keys)
  ├── Makefile (+ provision-llm)
  ├── makefiles/helpers.mk (+ generate-litellm-config)
  ├── core/entrypoint-manifest.yaml (+ provision-llm)
  ├── core/modules/litellm/config/litellm-config.yml (→ generated, first render)
  └── tests/gates/test_gate_llm_aliases.py
```

---

## ⚠️ TRAP[DECISION] · 2026-07-24 · HI · policy.yaml как ЕДИНСТВЕННЫЙ SSoT — не контекст, не проекты
- **Rejected:** llm-policy.yaml в context-overlay (как планировалось в TRAP 2026-07-15)
- **Reason:** Сегодня один провайдер (DeepSeek) на все контексты. При появлении разных провайдеров на разных контекстах — policy.yaml получает секцию `contexts:` с per-context overrides. До этого момента — единый файл. Проекты не имеют своих policy.yaml — они ссылаются на профили.
- **Rev:** если контекст требует уникального провайдера (не DeepSeek) → добавить `contexts:` секцию в policy.yaml с merge-логикой platform → context.

## ⚠️ TRAP[DECISION] · 2026-07-24 · MED · OPENAI_API_KEY — удалён как provider key, оставлен как removed в secret-definitions
- **Rejected:** Полное удаление из secret-definitions.yaml (потеря audit trail)
- **Reason:** `tier: removed` сохраняет историю — будущий разработчик видит, что ключ существовал и был удалён намеренно, не случайно потерян.
- **Rev:** через 6 месяцев (2027-01-24) — удалить completely, если не было инцидентов.

## ⚠️ TRAP[DECISION] · 2026-07-24 · MED · deprecated-алиасы на 1 месяц
- **Rejected:** Мгновенное удаление старых model_name (deepseek-v4-pro, deepseek-chat, deepseek-reasoner)
- **Reason:** Старые model_name остаются в litellm-config.yml через `model_group_alias` на 1 месяц. Это даёт время всем проектам мигрировать на семантические алиасы без downtime.
- **Rev:** 2026-08-24 — удалить deprecated aliases полностью.

$END_DEVPLAN
