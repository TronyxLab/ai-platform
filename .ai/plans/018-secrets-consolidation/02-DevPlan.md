<!--
$START_DEVPLAN
$ARTIFACT_CONTRACT
  PURPOSE:      Реализовать консолидацию секретов платформы: (1) secrets-manifest.yaml как SSoT
                с tier-моделью, (2) персистентную автогенерацию секретов через sops --set,
                (3) manifest-driven валидацию env_requires в deploy-modules.sh,
                (4) anti-drift гейт test_gate_secrets_manifest.py,
                (5) очистку .env.example от мёртвых токенов,
                (6) документирование mirror.yml flow.
  DESCRIPTION:  Детальный план реализации Brief 018-secrets-consolidation.
                Разбивка на 8 атомарных задач с параллельными волнами.
                Ключевое архитектурное решение: secrets-manifest.yaml — единственный SSoT,
                из которого выводятся module.yaml env_requires, .env.example CI-секция,
                CI workflow secrets и логика валидации в deploy-modules.sh.
  RATIONALE:    Дрейф не остановится без системного anti-drift механизма.
                Четыре несогласованных механизма валидации (required_vars, env_requires,
                _ensure_secret, validate_env) консолидируются на манифест.
                Манифест как SSoT → гейт консистентности → блокировка молчаливого
                добавления секретов.
  ACCEPTANCE_CRITERIA:
    AC1: GHCR_TOKEN удалён из .env.example; GIT_MIRROR_TOKEN задокументирован как optional
    AC2: secrets-manifest.yaml — единый SSoT: tier=required|generated|optional, consumers=[модули]
    AC3: autogen-секреты (7 шт.) персистятся в encrypted-файл при наличии через sops --set
    AC4: Гейт test_gate_secrets_manifest.py: все 4 проверки проходят на текущем коде
    AC5: SSH_KEY и CI_DEPLOY_KEY задокументированы как один ключ с разными ролями
    AC6: .env.example CI-секция синхронизирована с manifest (ci-secret source)
    AC7: _check_env_requires() в deploy-modules.sh использует manifest как источник обязательности
    AC8: make gate MODE=fast — зелёный (все существующие гейты + новый)
  IMPLEMENTS:   Plan 018 — secrets-consolidation (01-Brief.md)
  IMPACTS:      files: core/secrets-manifest.yaml (NEW), core/lib/secrets.sh,
                core/internal/bootstrap/deploy-modules.sh, .env.example,
                core/entrypoint-manifest.yaml, .github/workflows/mirror.yml,
                tests/gates/test_gate_secrets_manifest.py (NEW).
                modules: все 13 module.yaml (верификация env_requires).
  REQUIRES:     Plan 015 T3.4 (SSH primary в context-promote.sh — done).
                AGE_SECRET_KEY доступен локально для тестов sops --set.
                Python 3.10+, PyYAML (уже в зависимостях проекта).
$END_DEVPLAN
-->

# DevPlan: 018-secrets-consolidation

## 1. Requirements Analysis — Key Success Criteria

| # | Критерий | Измерение |
|---|----------|-----------|
| SC1 | secrets-manifest.yaml покрывает 100% env_requires всех 13 module.yaml | `grep -c env_requires` × module count = manifest coverage |
| SC2 | Autogen-секреты персистентны: повторный bootstrap не генерирует новые ключи | Сравнение LITELLM_MASTER_KEY до/после второго bootstrap |
| SC3 | CI gate блокирует незарегистрированный secrets.X в workflow | Добавить фейковый secrets.X → gate RED → удалить → gate GREEN |
| SC4 | .env.example не содержит GHCR_TOKEN, GIT_MIRROR_TOKEN задокументирован как optional | grep по .env.example |
| SC5 | make gate MODE=fast проходит (все gate-тесты зелёные) | exit code 0 |

## 2. Architecture Overview

### 2.1 Draft Code Graph

```
                    ┌──────────────────────────────┐
                    │  secrets-manifest.yaml (NEW)  │  ← Single Source of Truth
                    │  tier: required|generated|opt  │
                    │  consumers: [module names]     │
                    │  source: sops|autogen|ci-secret│
                    └──────┬───────────────────────┘
                           │
          ┌────────────────┼────────────────────┐
          ▼                ▼                     ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│ deploy-modules.sh│ │ secrets.sh   │ │ gate (NEW)           │
│ _check_env_      │ │ step_12b     │ │ test_gate_secrets_   │
│ requires()       │ │ ensure_      │ │ manifest.py          │
│ manifest-driven  │ │ secrets()    │ │                      │
│ ▼                │ │ ▼            │ │ 1. manifest↔module   │
│ for module M:    │ │ for each      │ │ 2. manifest↔workflow │
│  manifest.       │ │  generated:   │ │ 3. manifest↔.env     │
│  consumers→      │ │   if missing: │ │ 4. no hardcoded     │
│  check env       │ │    gen+persist│ │    secrets in core/  │
└──────────────────┘ └──────────────┘ └──────────────────────┘
          │                                        │
          ▼                                        ▼
┌──────────────────┐                    ┌──────────────────────┐
│ .env.example     │                    │ entrypoint-manifest  │
│ CI-секция        │                    │ gates: +secrets-     │
│ → derived from   │                    │ manifest-consistency │
│   manifest       │                    └──────────────────────┘
│   (ci-secret)    │
└──────────────────┘
```

### 2.2 Data Flow — Bootstrap Secrets Validation (новый)

```
1. Bootstrap: node-lifecycle.sh --mode init
2. step_10_decrypt_secrets() → sops decrypt → source secrets.env
3. step_12b_ensure_secrets() → [NEW] read secrets-manifest.yaml
   ├── tier=generated: check env → if missing: openssl rand → export
   │   └── [NEW] if encrypted file exists: sops --set "$enc_file" "$var=$value"
   └── log generated/missing
4. step_14_node_update() → deploy-modules.sh
5. _check_env_requires(module_name) → [NEW] read secrets-manifest.yaml
   ├── find all secrets where consumers includes module_name AND tier∈{required,generated}
   ├── check each env var is non-empty (process env OR secrets.env)
   └── fail-fast if any required var missing
```

### 2.3 Data Flow — CI Gate (новый)

```
make gate MODE=fast
  └── pytest -m gate tests/gates/test_gate_secrets_manifest.py
       ├── test_manifest_vs_module_yaml:
       │   for each module.yaml:
       │     for each env_requires entry:
       │       assert entry.name in manifest.secrets[].name
       │       assert manifest[entry].tier ∈ {required, generated}
       ├── test_manifest_vs_workflows:
       │   for each .github/workflows/*.yml:
       │     for each ${{ secrets.XXX }}:
       │       assert XXX in manifest.secrets[].name
       │       assert manifest[XXX].source == "ci-secret"
       ├── test_manifest_vs_env_example:
       │   parse .env.example CI section
       │   assert each documented CI secret ∈ manifest (source=ci-secret)
       │   warn if manifest has ci-secret NOT documented in .env.example
       └── test_no_hardcoded_secrets_in_core:
           scan core/**/*.sh for credential patterns
           (extends existing test_ci_no_hardcoded_secrets scope)
```

## 3. Design Decisions

### 3.1 Manifest as SSoT — module.yaml env_requires becomes derived

## @rationale
Q: Why not keep module.yaml env_requires as the authoritative list and make manifest derive from it?
A: Manifest is the anti-drift mechanism. If module.yaml is authoritative, adding a secret to a module silently bypasses the manifest. With manifest as SSoT, the gate blocks any secret not registered. Module.yaml env_requires stays for documentation/readability but the gate verifies bidirectional consistency.

### 3.2 sops --set chicken-and-egg — skip persistence on first bootstrap

## @rationale
Q: What if the encrypted file doesn't exist on first bootstrap (e.g., new node without pre-created secrets)?
A: `sops --set` requires an existing encrypted file. On first bootstrap without an encrypted file, autogen secrets are exported to env but NOT persisted. A WARN log instructs the operator to create the encrypted file. On subsequent runs with the file present, sops --set writes generated values back. This is consistent with the existing flow: first bootstrap always generates ephemeral secrets, persistence is a progressive enhancement.

### 3.3 SSH_KEY ≡ CI_DEPLOY_KEY — документация, не код

## @rationale
Q: Why not add a gate check for SSH_KEY/CI_DEPLOY_KEY deduplication?
A: These are GitHub repo secrets — their values are not accessible from code. A gate can only verify documentation consistency (both documented as the same key with different roles). The actual deduplication is a human operational task. We document the relationship in .env.example and mirror.yml, and add a TRAP[DECISION] about the expected state.

### 3.4 mirror.yml — SSH deploy key deferred, flow documented

## @rationale
Q: Why not switch mirror.yml to SSH deploy key in this plan?
A: mirror.yml currently uses GIT_MIRROR_TOKEN (HTTPS + GIT_ASKPASS). Switching to SSH requires: (a) generating a new SSH key pair, (b) adding the public key as a deploy key on TronyxLab/ai-platform, (c) adding the private key as a repo secret, (d) rewriting the push step. This is a separate operational task with GitHub-side configuration. The plan documents the desired flow and marks GIT_MIRROR_TOKEN as optional fallback. The actual SSH transition is deferred.

### 3.5 _check_env_requires() — manifest-driven lookup replaces inline Python

## @rationale
Q: Why replace the current Python script in _check_env_requires() instead of extending it?
A: The current script reads module.yaml env_requires and checks env vars. The new flow reads manifest, finds all secrets for the module via consumers field, then checks env vars. The data source changes from module.yaml (distributed) to manifest (centralized). The Python logic is replaced, not extended, because the lookup key changes (module→secrets via consumers, not module→vars via env_requires). The old env_requires field in module.yaml stays for documentation — the gate verifies it matches manifest.

## 4. $TASKS

### TASK-1: Inventory audit — собрать все env_requires из 13 module.yaml

**Описание:** Прочитать все 13 `core/modules/*/module.yaml`, извлечь все уникальные имена из полей `env_requires`, сгруппировать по модулям-потребителям. Результат — inventory-список (25+ уникальных имён), который станет основой для TASK-2.

**Затрагиваемые файлы:**
- `core/modules/*/module.yaml` (13 файлов, read-only audit)
- Output: inventory list (передаётся в TASK-2, отдельный файл не создаётся)

**Критерии приёмки:**
- Все 13 module.yaml прочитаны
- Извлечены все уникальные имена из `env_requires` (ожидается 15-20 уникальных)
- Каждое имя сопоставлено со списком модулей-потребителей
- Inventory включает также CI secrets из `.env.example` (VPS_SSH_KEY, CI_DEPLOY_KEY, etc.)
- Inventory включает autogen-секреты из `secrets.sh:_ensure_secret()` (7 шт.)
- Inventory включает инфраструктурные секреты (GHCR_PULL_TOKEN, AGE_SECRET_KEY, etc.)

**Зависимости:** нет

**Сложность:** 2/10

---

### TASK-2: Создать core/secrets-manifest.yaml

**Описание:** Создать единый YAML-манифест секретов платформы на основе inventory из TASK-1. Каждая запись: name, tier (required|generated|optional), consumers (список модулей), source (sops|autogen|ci-secret|env), note (опционально). Для generated-секретов: поле gen_command. Для optional-секретов: поле feature (опционально).

**Схема:**
```yaml
# core/secrets-manifest.yaml
# GREP_SUMMARY: secrets-manifest sso tier-model required generated optional consumers anti-drift
# STRUCTURE: ┌secrets[]┐ → ◇ tier(required|generated|optional) → ⊕ consumers[] → ⟦source(sops|autogen|ci-secret|env)⟧ → ⎋ gate-verifiable
version: 1
secrets:
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres, litellm, backup-cron, infra-metrics]
    source: sops
    note: "openssl rand -base64 32"

  - name: LITELLM_MASTER_KEY
    tier: generated
    consumers: [litellm, hermes-agent]
    source: autogen
    gen_command: 'echo "sk-$(openssl rand -hex 32)"'

  - name: TELEGRAM_BOT_TOKEN
    tier: optional
    feature: telegram
    consumers: [hermes-agent, monitoring]
    source: sops

  - name: GHCR_PULL_TOKEN
    tier: required
    consumers: [docker-login]
    source: sops
    note: "Fine-grained PAT: read:packages на все orgs"

  - name: VPS_SSH_KEY
    tier: required
    consumers: []
    source: ci-secret
    note: "SSH private key for rsync core на VPS (platform-deploy.yml)"

  - name: CI_DEPLOY_KEY
    tier: required
    consumers: []
    source: ci-secret
    note: "SSH deploy key для ci-deploy forced-command. ≡ VPS_SSH_KEY (один ключ, разные роли)"

  # ... (25+ entries total)
```

**Затрагиваемые файлы:**
- `core/secrets-manifest.yaml` (NEW, ~150 строк)

**Критерии приёмки:**
- Все секреты из inventory TASK-1 присутствуют в манифесте
- Каждый env_requires из любого module.yaml имеет соответствующую запись в манифесте
- Каждый CI secret из .env.example (секция «GitHub Actions secrets») имеет запись с source=ci-secret
- Каждый autogen-секрет (7 шт.) имеет запись с tier=generated, source=autogen
- YAML синтаксически валиден (`python3 -c "import yaml; yaml.safe_load(open('...'))"`)
- GREP_SUMMARY и STRUCTURE присутствуют

**Зависимости:** TASK-1 (inventory)

**Сложность:** 4/10

---

### TASK-3: Обновить .env.example + верифицировать SSH_KEY/CI_DEPLOY_KEY

**Описание:**
1. Удалить строку `# GHCR_TOKEN — GitHub Container Registry token...` (строка 218)
2. Обновить документацию GIT_MIRROR_TOKEN: указать optional, SSH-primary контекст
3. Добавить примечание о SSH_KEY ≡ CI_DEPLOY_KEY (один ключ, две роли: rsync + forced-command)
4. Синхронизировать список CI-секретов с манифестом (source=ci-secret записи)
5. Удалить упоминание GHCR_TOKEN из комментариев (строка 218: `used by platform-test.yml, build-platform.yml`)
6. Обновить документированные workflow-использования

**Затрагиваемые файлы:**
- `.env.example` (строки 210-226)

**Критерии приёмки:**
- `grep GHCR_TOKEN .env.example` → 0 совпадений
- `grep GIT_MIRROR_TOKEN .env.example` → содержит «optional» или «SSH fallback»
- `grep SSH_KEY .env.example` → содержит примечание о дублировании с CI_DEPLOY_KEY
- CI-секция не содержит секретов, отсутствующих в манифесте
- Все CI-секреты из манифеста (source=ci-secret) задокументированы в .env.example (AMBER если нет)

**Зависимости:** TASK-2 (манифест)

**Сложность:** 2/10

---

### TASK-4: Модифицировать core/lib/secrets.sh — персистентная автогенерация

**Описание:** Доработать `step_12b_ensure_secrets()`:
1. После `openssl rand` для каждого generated-секрета → попытаться `sops --set` в encrypted-файл
2. Если encrypted-файл не существует (первый bootstrap) — skip persistence, log WARN
3. Если `sops --set` fails — log ERROR, не блокировать bootstrap (секрет уже в env)
4. Убрать хардкоженный список из 7 `_ensure_secret` вызовов — читать generated-секреты из манифеста
5. Сохранить обратную совместимость: если манифест недоступен — fallback на хардкоженный список

**sops --set паттерн:**
```bash
# Псевдокод
if [[ -f "$enc_file" ]] && command -v sops &>/dev/null; then
    sops --set '["'"$var_name"'"] "'"$new_val"'"' "$enc_file" 2>/dev/null || {
        log_step "ensure-secrets" "ERROR" "sops --set failed for $var_name — value in env but NOT persisted"
    }
fi
```

**Затрагиваемые файлы:**
- `core/lib/secrets.sh` (функция `step_12b_ensure_secrets`, строки 194-245)

**Критерии приёмки:**
- При наличии encrypted-файла: autogen-секрет записывается через sops --set
- При отсутствии encrypted-файла: WARN, секрет экспортируется в env (старое поведение)
- При ошибке sops --set: ERROR, bootstrap продолжается (секрет в env)
- Список generated-секретов читается из манифеста (не хардкожен)
- При отсутствии манифеста: fallback на хардкоженный список из 7 секретов
- Функция `_ensure_secret()` сохранена без изменений (закрытие над bash-переменными)

**Зависимости:** TASK-2 (манифест)

**Сложность:** 5/10

---

### TASK-5: Модифицировать deploy-modules.sh — manifest-driven _check_env_requires()

**Описание:** Заменить inline Python-логику в `_check_env_requires()` (строки 683-719) на manifest-driven подход:
1. Загрузить `secrets-manifest.yaml`
2. Для данного module_name найти все секреты где `consumers` включает модуль И `tier` ∈ {required, generated}
3. Проверить каждый такой секрет на непустоту (process env → secrets.env)
4. Fail-fast при отсутствии любого required-секрета

**Новая логика (псевдокод):**
```bash
_check_env_requires() {
    local module_name="$1"
    local manifest="${CORE_DIR}/secrets-manifest.yaml"
    [[ ! -f "$manifest" ]] && return 0  # грациозная деградация

    local _missing
    _missing=$(python3 -c "
import yaml, os, sys
with open('${manifest}') as f:
    data = yaml.safe_load(f)
secrets = data.get('secrets', [])
module_secrets = [s for s in secrets if '${module_name}' in s.get('consumers', [])
                  and s.get('tier') in ('required', 'generated')]
secrets_file = os.environ.get('SECRETS_ENV_FILE', '/run/platform/secrets.env')
_env_map = {}
if os.path.isfile(secrets_file):
    with open(secrets_file) as sf:
        for line in sf:
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                _env_map[k.strip()] = v.strip()
missing = [s['name'] for s in module_secrets
           if not os.environ.get(s['name'], '') and not _env_map.get(s['name'], '')]
if missing:
    print(','.join(missing))
    sys.exit(1)
")
    # ... fail-fast logic unchanged
}
```

**Затрагиваемые файлы:**
- `core/internal/bootstrap/deploy-modules.sh` (функция `_check_env_requires`, строки 683-719)

**Критерии приёмки:**
- `_check_env_requires("postgres")` проверяет POSTGRES_USER, POSTGRES_PASSWORD (из manifest.consumers)
- `_check_env_requires("litellm")` проверяет LITELLM_MASTER_KEY, POSTGRES_PASSWORD, OPENAI_API_KEY
- При отсутствии манифеста: return 0 (грациозная деградация, не блокирует деплой)
- Неиспользуемые module.yaml поля env_requires не влияют на поведение (манифест — SSoT)
- Старая Python-логика удалена

**Зависимости:** TASK-2 (манифест)

**Сложность:** 4/10

---

### TASK-6: Создать test_gate_secrets_manifest.py + зарегистрировать в manifest

**Описание:** Создать новый gate-тест с 4 проверками:
1. **Manifest ↔ module.yaml:** каждое `env_requires` имя из любого module.yaml обязано быть в манифесте с tier=required или generated. Неизвестное имя → RED.
2. **Manifest ↔ workflows:** каждый `${{ secrets.XXX }}` в `.github/workflows/*.yml` обязан быть в манифесте с source=ci-secret. Неизвестное имя → RED.
3. **Manifest ↔ .env.example:** каждая CI-secret запись в `.env.example` обязана быть в манифесте. Секрет в манифесте без документации → AMBER (warning).
4. **No hardcoded secrets in core/: ** расширить существующий credential scan на `core/**/*.sh` (сейчас только `.github/**`). Паттерн: `(password|secret|token|api_key|key)\s*[:=]\s*["'][^"'\s]{8,}["']`.

Также зарегистрировать gate в `core/entrypoint-manifest.yaml` секция `gates`.

**Затрагиваемые файлы:**
- `tests/gates/test_gate_secrets_manifest.py` (NEW, ~200 строк)
- `core/entrypoint-manifest.yaml` (добавить запись в gates)

**Критерии приёмки:**
- `pytest tests/gates/test_gate_secrets_manifest.py -v` → все 4 теста проходят на текущем коде
- Тест 1: все 13 module.yaml проверены, нет неизвестных env_requires
- Тест 2: все workflow-файлы проверены, нет неизвестных secrets.X
- Тест 3: .env.example CI-секция синхронизирована с манифестом
- Тест 4: core/**/*.sh не содержит хардкоженных креденшалов
- Gate зарегистрирован в `entrypoint-manifest.yaml` с id: secrets-manifest-consistency
- Файл имеет `@pytest.mark.gate` декоратор, GREP_SUMMARY, MODULE_CONTRACT
- LDD-трейс: IMP:9 логи при PASS/FAIL

**Зависимости:** TASK-2 (манифест), TASK-3 (.env.example)

**Сложность:** 6/10

---

### TASK-7: Обновить mirror.yml — документирование flow и SSH deploy key

**Описание:**
1. Обновить MODULE_CONTRACT: документировать GIT_MIRROR_TOKEN как optional fallback
2. Добавить TOKEN NOTE (уже частично сделано в Brief-контексте, строки 54-58 mirror.yml)
3. TRAP[DECISION] о том, что SSH deploy key transition deferred (см. Design Decision 3.4)
4. Обновить комментарий о том, что context-promote.sh уже использует SSH primary

**Затрагиваемые файлы:**
- `.github/workflows/mirror.yml`

**Критерии приёмки:**
- MODULE_CONTRACT содержит информацию о GIT_MIRROR_TOKEN как optional
- Присутствует TRAP[DECISION] о deferred SSH transition
- Документирована связь с context-promote.sh (SSH primary)
- YAML синтаксически валиден

**Зависимости:** нет (независимая документация)

**Сложность:** 1/10

---

### TASK-8: Верификация — make gate MODE=fast

**Описание:** Запустить полный gate suite для проверки отсутствия регрессий:
1. `make gate MODE=fast` — все существующие гейты + новый
2. Отдельно: `pytest tests/gates/test_gate_secrets_manifest.py -v` — детальный вывод
3. При падении любого теста: анализ причины, исправление
4. `ruff format . && ruff check --fix .` — форматирование

**Затрагиваемые файлы:**
- Нет (runtime verification)

**Критерии приёмки:**
- `make gate MODE=fast` → exit 0
- Новый gate `test_gate_secrets_manifest.py` → все тесты PASS
- `ruff check` → нет ошибок
- Если gate падает: причина задокументирована, создан fix

**Зависимости:** TASK-2, TASK-3, TASK-4, TASK-5, TASK-6, TASK-7

**Сложность:** 3/10

---

## 5. $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks:** TASK-1, TASK-7
- **Rationale:** TASK-1 (аудит module.yaml) и TASK-7 (mirror.yml документация) не имеют общих файлов и не зависят друг от друга.
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-7`

### Wave 2 (depends on Wave 1)
- **Tasks:** TASK-2
- **Rationale:** TASK-2 требует inventory из TASK-1.
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2`

### Wave 3 (depends on Wave 2, parallel — no shared files)
- **Tasks:** TASK-3, TASK-4, TASK-5
- **Rationale:** Все три задачи зависят от манифеста (TASK-2), но изменяют разные файлы без пересечений: .env.example, secrets.sh, deploy-modules.sh.
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-3, TASK-4, TASK-5`

### Wave 4 (depends on Wave 2, Wave 3)
- **Tasks:** TASK-6
- **Rationale:** Gate test требует манифест (TASK-2) и актуальный .env.example (TASK-3).
- **Command:** `coder Read DevPlan.md, implement Wave 4: TASK-6`

### Wave 5 (depends on Wave 2-4, Wave 1)
- **Tasks:** TASK-8
- **Rationale:** Финальная верификация после всех изменений.
- **Command:** `coder Read DevPlan.md, implement Wave 5: TASK-8`

## 6. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_secrets_manifest.py` | `test_manifest_vs_module_yaml` | Каждое `env_requires` имя из любого module.yaml присутствует в манифесте (tier=required\|generated). Неизвестное имя → FAIL. | `core/secrets-manifest.yaml` ↔ `core/modules/*/module.yaml` |
| `tests/gates/test_gate_secrets_manifest.py` | `test_manifest_vs_workflows` | Каждый `${{ secrets.XXX }}` в `.github/workflows/*.yml` зарегистрирован в манифесте (source=ci-secret). Неизвестный → FAIL. | `core/secrets-manifest.yaml` ↔ `.github/workflows/*.yml` |
| `tests/gates/test_gate_secrets_manifest.py` | `test_manifest_vs_env_example` | CI-секреты в `.env.example` совпадают с manifest (source=ci-secret). Манифест-секрет без документации → WARNING. | `core/secrets-manifest.yaml` ↔ `.env.example` |
| `tests/gates/test_gate_secrets_manifest.py` | `test_no_hardcoded_secrets_in_core` | `core/**/*.sh` не содержит хардкоженных креденшалов (password=, token=, secret=, api_key= с литералами). Расширение существующего скоупа с `.github/**`. | `core/**/*.sh` |

**Примечание:** Расширение credential scan на `core/**/*.sh` может сломать `test_gate_ci_env_vars.py::test_ci_no_hardcoded_secrets` если тот тест не параметризован по директории. При реализации TASK-6: либо расширить существующий тест, либо добавить новый тест в `test_gate_secrets_manifest.py` и задокументировать разделение ответственности.

## 7. Acceptance Criteria Summary

| AC | Критерий | Покрывается задачами | Верификация |
|----|----------|---------------------|-------------|
| AC1 | GHCR_TOKEN удалён, GIT_MIRROR_TOKEN optional | TASK-3 | `grep GHCR_TOKEN .env.example` → 0 |
| AC2 | secrets-manifest.yaml — SSoT | TASK-1, TASK-2 | TASK-6 gate |
| AC3 | Autogen persistence via sops --set | TASK-4 | Ручной тест: два bootstrap подряд, сравнить ключи |
| AC4 | Gate test_gate_secrets_manifest.py | TASK-6 | `pytest tests/gates/test_gate_secrets_manifest.py -v` |
| AC5 | SSH_KEY/CI_DEPLOY_KEY документированы | TASK-3 | grep .env.example |
| AC6 | .env.example CI-секция из манифеста | TASK-3 | TASK-6 gate (test 3) |
| AC7 | _check_env_requires() manifest-driven | TASK-5 | TASK-6 gate (test 1) |
| AC8 | make gate MODE=fast зеленый | TASK-8 | exit code 0 |

## 8. File Manifest

| Файл | Статус | Задача | Строк (≈) |
|------|--------|--------|-----------|
| `core/secrets-manifest.yaml` | NEW | TASK-2 | ~150 |
| `core/lib/secrets.sh` | MODIFY | TASK-4 | +30 |
| `core/internal/bootstrap/deploy-modules.sh` | MODIFY | TASK-5 | +20 / −25 |
| `.env.example` | MODIFY | TASK-3 | +5 / −3 |
| `core/entrypoint-manifest.yaml` | MODIFY | TASK-6 | +6 |
| `.github/workflows/mirror.yml` | MODIFY | TASK-7 | +10 |
| `tests/gates/test_gate_secrets_manifest.py` | NEW | TASK-6 | ~200 |
| `core/modules/*/module.yaml` | READ (13 files) | TASK-1 | 0 |

## 9. Risk Register

| Риск | Вероятность | Влияние | Mitigation |
|------|------------|---------|------------|
| `sops --set` повреждает encrypted-файл | Низкая | Высокое | Тест: зашифровать → sops --set → расшифровать → сравнить. Dry-run перед записью. |
| Manifest рассинхронизируется с module.yaml при будущих изменениях | Средняя | Среднее | Gate (TASK-6 test 1) блокирует merge при расхождении |
| Расширение credential scan на `core/**/*.sh` даёт ложные срабатывания | Средняя | Низкое | Allowlist для известных паттернов (openssl rand, sops --set, etc.) |
| `_check_env_requires()` manifest-driven пропускает секреты из-за несоответствия consumer-имён | Низкая | Высокое | TASK-1 аудит гарантирует покрытие; gate верифицирует консистентность |

## 10. Next Steps

### Wave 1 (независимые задачи)
```
coder Read .ai/plans/018-secrets-consolidation/02-DevPlan.md, implement Wave 1: TASK-1 (inventory audit), TASK-7 (mirror.yml docs)
```

### Wave 2 (манифест)
```
coder Read .ai/plans/018-secrets-consolidation/02-DevPlan.md, implement Wave 2: TASK-2 (secrets-manifest.yaml)
```

### Wave 3 (параллельные модификации)
```
coder Read .ai/plans/018-secrets-consolidation/02-DevPlan.md, implement Wave 3: TASK-3 (.env.example), TASK-4 (secrets.sh), TASK-5 (deploy-modules.sh)
```

### Wave 4 (gate test)
```
coder Read .ai/plans/018-secrets-consolidation/02-DevPlan.md, implement Wave 4: TASK-6 (gate + registration)
```

### Wave 5 (верификация)
```
coder Read .ai/plans/018-secrets-consolidation/02-DevPlan.md, implement Wave 5: TASK-8 (make gate MODE=fast)
```
