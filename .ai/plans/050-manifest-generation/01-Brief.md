# Brief 050 — Manifest Generation: от ручной синхронизации к derived artifacts

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Устранить дублирование данных и ручную синхронизацию между манифестами платформы путём внедрения парадигмы генерации: authoritative sources (module.yaml, secret-definitions.yaml, docker-compose.base.yml) → generated manifests (secrets-manifest.yaml, platform-env.yaml#profiles/port_mappings/env_defaults).
DESCRIPTION:           Трёхволновой Strangler-Fig: (1) создание secret-definitions.yaml + auto-compute consumers из module.yaml, генерация secrets-manifest.yaml; (2) генерация platform-env.yaml из platform-infra.yaml + compose-файлов; (3) CI-интеграция + документация. Два новых Python-генератора (~350 LOC) + CI gate `make check-manifests`. В результате 7+ дублирующихся имён переменных и 3 ручные синхронизации (consumers↔env_requires, profiles↔directories, port_mappings↔compose-ports) заменяются на автоматическую генерацию.
RATIONALE:             Суперпозиция 2026-07-22 вскрыла: (a) `secrets-manifest.yaml#consumers` — это в точности инверсия `module.yaml#env_requires`, поддерживается вручную и валидируется gate; (b) `platform-env.yaml#env_defaults` дублирует имена из `secrets-manifest.yaml` с CI-значениями; (c) `platform-env.yaml#profiles` дублирует directory listing `core/modules/`; (d) `platform-env.yaml#port_mappings` дублирует порты из `docker-compose.base.yml`. Выбрана Option F (Full Generation, score 9/10) — парадигма «authoritative sources → generated artifacts» с CI-верификацией up-to-date. Проект уже использует этот паттерн: `make test-inventory-sync`, `make discover-modules`, `_topo_sort.py`. Минимальный blast radius: добавляются 2 генератора + 1 новый authoritative файл; 13 module.yaml, 13 docker-compose.base.yml, 1 entrypoint-manifest.yaml — не затрагиваются.
ACCEPTANCE_CRITERIA:
  AC-1: `make generate-manifests` создаёт `core/secrets-manifest.yaml`, идентичный текущему по структуре, но с полем `consumers`, вычисленным из 14 × module.yaml#env_requires (0 divergences, gate-подтверждено)
  AC-2: `make generate-manifests` создаёт `platform-env.yaml`, где секции `profiles`, `port_mappings`, `test_ports`, `env_defaults` сгенерированы из authoritative sources; ручные секции (networks, volumes, proxy, provides) скопированы из `platform-infra.yaml` без изменений
  AC-3: `make check-manifests` (CI gate) возвращает exit 1, если сгенерированные файлы отличаются от committed (git diff --exit-code)
  AC-4: `ci_default` значения перенесены из `platform-env.yaml#env_defaults` в `secret-definitions.yaml`; все потребители (CI workflows, `_conftest/infra.py`, `provision-environment.sh`) обновлены
  AC-5: При добавлении `POSTGRES_PASSWORD` в `env_requires` нового модуля → `make generate-manifests` → `consumers` в `secrets-manifest.yaml` автоматически включает новый модуль
  AC-6: `make gate MODE=fast` зелёный; существующие gate-тесты (`test_gate_secrets_manifest.py`, `test_gate_manifest_integrity.py`) проходят без изменений (или с минимальными правками под новый формат)
  AC-7: 0 новых inline-python3 блоков; оба генератора — отдельные `.py` файлы с unit-тестами
  AC-8: AGENTS.md (root, core/) обновлены: зафиксирован generation contract, перечислены authoritative vs generated файлы
IMPLEMENTS:            AGENTS.md инвариант 2 (Makefile — единый фасад), инвариант 5 (entrypoint-manifest.yaml как реестр), языковая политика (новый код = Python), Strangler-Fig pattern. Суперпозиция от 2026-07-22: Option F Full Generation (score 9/10).
IMPACTS:
  ## Новые файлы
  - core/secret-definitions.yaml — authoritative per-secret metadata (~31 entry, без поля consumers)
  - core/internal/scripts/generate_secrets_manifest.py — генератор secrets-manifest.yaml (~150 LOC)
  - core/internal/scripts/generate_platform_env.py — генератор platform-env.yaml (~200 LOC)
  - core/internal/scripts/platform-infra.yaml — infrastructure topology (выделяется из platform-env.yaml)
  - tests/unit/test_generate_secrets_manifest.py — unit-тесты генератора
  - tests/unit/test_generate_platform_env.py — unit-тесты генератора
  ## Модифицируемые файлы
  - core/secrets-manifest.yaml — consumers секция становится generated (помечена # AUTO-GENERATED)
  - platform-env.yaml — -env_defaults, -port_mappings, -test_ports, -profiles (переносятся в generated-секции)
  - Makefile (root) — +таргеты generate-manifests, check-manifests
  - core/entrypoint-manifest.yaml — +регистрация generate-manifests, check-manifests
  - core/AGENTS.md — +generation contract, +список authoritative vs generated
  - AGENTS.md (root) — +generation contract (инвариант или TRAP)
  - core/internal/bootstrap/deploy/secrets_validator.py — обновить путь/формат чтения consumer-ов
  - core/internal/bootstrap/deploy/compose_preflight.py — обновить путь чтения
  - core/internal/scripts/validate_module_yaml.py — обновить путь secrets-manifest
  - core/internal/provision-environment.sh — обновить источник env_defaults
  - tests/_conftest/infra.py — обновить источник env_defaults
  - .github/workflows/platform-test.yml — обновить CI-переменные если использовались env_defaults
  - .github/workflows/platform-deploy.yml — обновить CI-переменные
  ## Не затрагиваются
  - core/entrypoint-manifest.yaml (operations registry — остаётся authoritative)
  - core/templates/template-manifest.yaml (template registry — ортогонален)
  - 14 × core/modules/*/module.yaml (уже authoritative)
  - 13 × core/modules/*/docker-compose.base.yml (уже authoritative)
  - core/schemas/*.json (validation contracts)
  - node-configs/*/node.yaml (per-node desired state)
REQUIRES:
  - Python ≥3.10
  - PyYAML (уже в зависимостях — _topo_sort.py, discover_modules.py)
  - 14 × module.yaml (существующие)
  - 13 × docker-compose.base.yml (существующие)
  - 13 × docker-compose.test.yml (существующие)
  - Существующий secrets-manifest.yaml (для миграции → secret-definitions.yaml)
  - Существующий platform-env.yaml (для миграции → platform-infra.yaml)
  - pytest ≥7.0 (для unit-тестов генераторов)
  - make (GNU)
$END_ARTIFACT_CONTRACT

---

## Problem Statement

### Текущее состояние: 4 источника ручного дрейфа

Анализ полного манифестного ландшафта (7 категорий, ~20 файлов, ~3800 строк) выявил 4 критические точки дублирования данных, которые сегодня поддерживаются вручную:

| # | Дублирование | Файлы | Проверка |
|---|-------------|-------|----------|
| 1 | `consumers` = инверсия `env_requires` | `secrets-manifest.yaml` ↔ 14 × `module.yaml` | Gate post-factum |
| 2 | `env_defaults` — те же имена что в secrets | `platform-env.yaml` ↔ `secrets-manifest.yaml` | НЕТ проверки |
| 3 | `profiles` = directory listing | `platform-env.yaml` ↔ `core/modules/*/` | НЕТ проверки |
| 4 | `port_mappings` = compose ports | `platform-env.yaml` ↔ 13 × `docker-compose.base.yml` | НЕТ проверки |

**Пример дублирования #1:** `POSTGRES_PASSWORD` имеет `consumers: [postgres, litellm, backup-cron, infra-metrics]` в secrets-manifest.yaml — это в точности список модулей, где `env_requires` содержит `POSTGRES_PASSWORD`. При добавлении 14-го модуля с этим же требованием нужно вручную обновить secrets-manifest.yaml. Gate поймает расхождение, но только после commit+push → CI red → fix → push again.

**Пример дублирования #2:** `platform-env.yaml#env_defaults` содержит `POSTGRES_PASSWORD: "test-pg-pwd"` — то же имя переменной, что в `secrets-manifest.yaml#secrets[0].name`. При переименовании секрета нужно править оба файла.

### Почему gates недостаточно

Текущий подход «CI gate валидирует консистентность» имеет фундаментальный недостаток: gates — это **реактивная** защита. Они ловят ошибку после того, как разработчик (или AI-агент) уже сделал изменение и запушил. Правильное решение — сделать невозможным само рассогласование: derived data не редактируется, она генерируется.

---

## Решение: Option F — Full Generation

### Парадигма

```
Authoritative (humans/agents edit)          Generated (tool produces)
───────────────────────────────────     ─────────────────────────────────
module.yaml (14 files)              ─┐
docker-compose.base.yml (13 files)  ─┤──→ secrets-manifest.yaml
secret-definitions.yaml (1 file)    ─┘     platform-env.yaml
platform-infra.yaml (1 file)        ──→
```

**Принцип:** authoritative sources содержат declaration of truth (что существует, что чему нужно). Generated files — это **проекции** (read models), вычисляемые из authoritative sources. Они коммитятся в репозиторий для читаемости, но **никогда не редактируются вручную**. CI gate `make check-manifests` блокирует merge, если сгенерированный файл не соответствует authoritative sources.

### Почему этот паттерн уже знаком проекту

| Существующий прецедент | Генератор | Проверка |
|------------------------|-----------|----------|
| `tests/test_inventory.yaml` | `tests/tools/sync_inventory.py` | `test_gate_test_inventory.py` |
| `docker-compose.yml#include` | `discover_modules.py` | `test_gate_compose_include.py` |
| Deploy groups JSON | `_topo_sort.py` | Интеграционные тесты |

### Что именно генерируется

**`core/secrets-manifest.yaml`:**
- Источник: `secret-definitions.yaml` (metadata) + 14 × `module.yaml` (consumers)
- Логика: для каждого секрета `s` из `secret-definitions.yaml` → `consumers = {m.name | m ∈ modules где s.name ∈ m.env_requires}`
- CI-секреты (`consumers: []`) — как есть из secret-definitions

**`platform-env.yaml`:**
- Источник: `platform-infra.yaml` (networks, volumes, proxy, provides) + 13 × compose-файлов + `secret-definitions.yaml`
- Секции:
  - `networks`, `volumes`, `proxy`, `provides` → копируются из `platform-infra.yaml`
  - `profiles` → `ls core/modules/*/` (исключая system-модули, проверяя `install_type: docker`)
  - `port_mappings` → парсинг `services.<name>.ports` из `docker-compose.base.yml`
  - `test_ports` → парсинг `services.<name>.ports` из `docker-compose.test.yml`
  - `env_defaults` → `{s.name: s.ci_default for s in secret-definitions where s.ci_default != null}`

---

## Implementation Plan (Strangler-Fig, 3 волны)

### Wave 1: secret-definitions.yaml + secrets-manifest generation

1. Создать `core/secret-definitions.yaml` — миграция из `secrets-manifest.yaml`:
   - Все 31 секрет, поля: `name`, `tier`, `source`, `charset`, `gen_command`, `ci_default`, `note`, `feature`
   - Поле `consumers` — удалено
   - `ci_default` заполняется из `platform-env.yaml#env_defaults` для 7+ переменных
2. Написать `core/internal/scripts/generate_secrets_manifest.py` (~150 LOC):
   - `load_secret_definitions(path)` → `list[SecretDef]`
   - `load_module_yamls(modules_dir)` → `list[ModuleDef]` (переиспользовать логику из `_topo_sort.py` или импортировать)
   - `compute_consumers(secret_name, modules)` → `list[str]`
   - `generate(secret_defs, modules)` → `str` (YAML output)
   - CLI: `--secret-defs`, `--modules-dir`, `--output`
3. Интегрировать в Makefile:
   - `make generate-manifests` (вызывает оба генератора)
   - `make check-manifests` (git diff --exit-code на generated files)
4. Зарегистрировать в `entrypoint-manifest.yaml`:
   - `generate-manifests` → `mechanism: python-script`, `delegates_to: core/internal/scripts/generate_secrets_manifest.py + core/internal/scripts/generate_platform_env.py`
   - `check-manifests` → `mechanism: git-diff`, `delegates_to: git diff --exit-code`
5. CI gate: `test_gate_manifests_up_to_date.py` — проверяет что `make check-manifests` возвращает 0
6. Unit-тесты: `tests/unit/test_generate_secrets_manifest.py`

### Wave 2: platform-env.yaml generation

1. Создать `platform-infra.yaml` — выделить ручные секции из `platform-env.yaml`:
   - `networks`, `volumes`, `proxy`, `provides`
   - `port_mappings`, `test_ports`, `env_defaults`, `profiles` — удалены
2. Написать `core/internal/scripts/generate_platform_env.py` (~200 LOC):
   - `load_infra(path)` → `dict`
   - `scan_compose_ports(modules_dir)` → порты из `docker-compose.base.yml`
   - `scan_test_ports(modules_dir)` → порты из `docker-compose.test.yml`
   - `load_ci_defaults(secret_defs_path)` → `dict`
   - `discover_profiles(modules_dir)` → `list[str]`
   - `generate(infra, ports, test_ports, ci_defaults, profiles)` → `str` (YAML)
3. Обновить потребителей `env_defaults`:
   - `core/internal/provision-environment.sh` → читать `secrets-manifest.yaml#ci_default`
   - `tests/_conftest/infra.py` → читать `secrets-manifest.yaml#ci_default`
   - `.github/workflows/platform-test.yml` → источник CI-переменных
4. Unit-тесты: `tests/unit/test_generate_platform_env.py`

### Wave 3: документация + CI-интеграция

1. Обновить AGENTS.md (root):
   - Добавить generation contract как TRAP[DECISION] или новый инвариант
   - Перечислить authoritative vs generated файлы
2. Обновить core/AGENTS.md:
   - Актуализировать таблицу канонических операций (+`generate-manifests`, +`check-manifests`)
3. Обновить `entrypoint-manifest.yaml`:
   - Новые таргеты в allowed_verbs
   - Новые gate-тесты в секции gates
4. Pre-commit hook: `make check-manifests` (блокирует commit с ручными правками generated files)

---

## Data Flow: добавление нового модуля (после внедрения)

**До (текущее состояние):**
```
1. Создать core/modules/new-module/module.yaml
   └── env_requires: [POSTGRES_PASSWORD]
2. Обновить core/secrets-manifest.yaml  ← РУЧНАЯ синхронизация
   └── POSTGRES_PASSWORD.consumers += new-module
3. Создать docker-compose.base.yml
   └── services.new-module.ports: ["8080:8080"]
4. Обновить platform-env.yaml           ← РУЧНАЯ синхронизация
   └── port_mappings += NEW_MODULE_PORT: 8080
   └── profiles += new-module
5. git push → CI red (если забыл шаг 2 или 4) → fix → push again
```

**После (с генерацией):**
```
1. Создать core/modules/new-module/module.yaml
   └── env_requires: [POSTGRES_PASSWORD]
2. Создать docker-compose.base.yml
   └── services.new-module.ports: ["8080:8080"]
3. make generate-manifests              ← АВТОМАТИЧЕСКИ
   └── secrets-manifest.yaml: consumers += new-module
   └── platform-env.yaml: port_mappings += NEW_MODULE_PORT: 8080
   └── platform-env.yaml: profiles += new-module
4. git add -A && git commit && git push → CI green
```

---

## Risk Assessment

| Риск | Вероятность | Impact | Mitigation |
|------|------------|--------|------------|
| Генератор выдаёт невалидный YAML | Низкая | HIGH | Unit-тесты + `yamllint` в CI |
| Генератор перезаписывает ручные правки в generated-файлах | Высокая (by design) | MEDIUM | CI gate `make check-manifests` блокирует merge; generated-секции помечены `# AUTO-GENERATED` |
| `provides.dsn_template` не выводится из compose | Гарантировано | LOW | Остаётся в ручном `platform-infra.yaml` |
| Потребители `env_defaults` сломаны после миграции | Средняя | MEDIUM | Интеграционные тесты + `make test MARKER=integration` |
| Существующие gate-тесты падают из-за изменения формата | Средняя | MEDIUM | Wave 1 включает адаптацию тестов |
| Генератор не справляется с edge-cases compose-файлов | Низкая | LOW | Парсинг только `ports:` секции, fallback — skip + warn |

---

## Decision Log

| Дата | Решение | Обоснование |
|------|---------|-------------|
| 2026-07-22 | Выбрана Option F (Full Generation) из суперпозиции 5 вариантов | Score 9/10. Проект уже использует паттерн генерации (`test-inventory-sync`, `discover-modules`). Каждый authoritative source имеет одного владельца. Zero manual drift по определению. |
| 2026-07-22 | `secret-definitions.yaml` — новый authoritative source, не расширение `secrets-manifest.yaml` | Разделение на declaration (secret-definitions) и projection (secrets-manifest) чище архитектурно: declaration не зависит от модулей, projection вычисляется |
| 2026-07-22 | `platform-infra.yaml` — новый файл, не in-place модификация `platform-env.yaml` | In-place update (Option G) создаёт файлы смешанной ответственности; разделение на infra (ручной) + env (генерируемый) — чище |
| 2026-07-22 | `ci_default` в `secret-definitions.yaml`, не в `platform-infra.yaml` | `ci_default` — property секрета (его тестовое значение), логически принадлежит определению секрета, не инфраструктуре |
| 2026-07-22 | `entrypoint-manifest.yaml` НЕ генерируется | Operations registry содержит design decisions (delegation paths), невыводимые из данных. Риск генерации > пользы. |

$END_BRIEF
