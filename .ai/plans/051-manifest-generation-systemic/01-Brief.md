# Brief 051 — Manifest Generation: трёхслойная Authoritative Graph Architecture

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Устранить системное дублирование данных между 10 манифестными файлами платформы путём внедрения трёхслойной архитектуры «Authoritative Sources → Generated Manifests → Documentation Views». Каждый факт имеет ровно один authoritative source; все производные файлы генерируются, не редактируются вручную. CI gate `make check-manifests` (один, ~50 LOC) заменяет 1466 LOC реактивных gate-тестов.
DESCRIPTION:           Четырёхволновой Strangler-Fig, расширяющий scope Brief 050 (Wave 1: secrets + platform-env) до полного манифестного ландшафта. Wave 2: Makefile `.PHONY` → `allowed_verbs` + target sections в entrypoint-manifest.yaml. Wave 3: `pytest --collect-only --marker=gate` → `gates[]` section. Wave 4: `entrypoint-manifest.yaml` → AGENTS.md canonical operations table + forbidden lists. Результат: 10 дублирований устранены, 4 новых Python-генератора (~500 LOC), 2 новых authoritative source файла (secret-definitions.yaml, platform-infra.yaml), 0 manual drift по определению.
RATIONALE:             Суперпозиция 7 вариантов (2026-07-22). Option E (трёхслойная Authoritative Graph, score 9/10) выбрана как системное решение, закрывающее 100% дублирований. Проект уже использует паттерн генерации для 2 файлов (test_inventory.yaml через sync_inventory.py, docker-compose.yml include через discover_modules.py) — расширение до полного ландшафта закономерно. Brief 050 (score 4/10 как изолированное решение) покрывает только 40% дублирований (D4-D7) — он сохранён как Wave 1 в составе общего плана. Принципиальное решение: entrypoint-manifest.yaml генерируется **частично** — `allowed_verbs` и `gates[]` вычисляются из authoritative sources, `mechanism`/`delegates_to`/`forbidden_*` остаются ручными (содержат design decisions, невыводимые из данных). AGENTS.md таблицы генерируются как human-readable проекция entrypoint-manifest.yaml.
ACCEPTANCE_CRITERIA:
  AC-1: `make generate-manifests` создаёт `core/secrets-manifest.yaml`, где `consumers` вычислены из 14 × module.yaml#env_requires (0 divergences)
  AC-2: `make generate-manifests` создаёт `platform-env.yaml`, где `profiles`, `port_mappings`, `test_ports`, `env_defaults` сгенерированы из authoritative sources; `networks`, `volumes`, `proxy`, `provides` скопированы из `platform-infra.yaml`
  AC-3: `make generate-manifests` обновляет `entrypoint-manifest.yaml#allowed_verbs` из Makefile `.PHONY` targets (все 38+ глаголов совпадают с `make -np` выводом); секции `bootstrap:`, `deploy:`, `build:`, `validate:`, `test:`, `lifecycle:` получают поля `delegates_to` и `mechanism` из существующего manifest (сохраняются при регенерации)
  AC-4: `make generate-manifests` обновляет `entrypoint-manifest.yaml#gates[]` из `pytest --collect-only --marker=gate` (все 56+ gate-тестов зарегистрированы, id = имя файла без test_gate_ и .py)
  AC-5: `make generate-manifests` обновляет AGENTS.md таблицу канонических операций из `entrypoint-manifest.yaml`
  AC-6: `make check-manifests` (CI gate) возвращает exit 1, если любой сгенерированный файл отличается от committed (git diff --exit-code на 4 файла)
  AC-7: При добавлении нового модуля с `env_requires: [NEW_SECRET]` и `docker-compose.base.yml` с портом 9090 → `make generate-manifests` автоматически обновляет `consumers`, `profiles`, `port_mappings` во всех generated файлах
  AC-8: При добавлении нового `.PHONY` target в Makefile → `allowed_verbs` обновляется автоматически
  AC-9: При добавлении нового gate-теста с `@pytest.mark.gate` → `gates[]` обновляется автоматически
  AC-10: `make gate MODE=fast` зелёный; `test_gate_manifest_integrity.py` и `test_gate_secrets_manifest.py` заменены на `test_gate_manifests_up_to_date.py` (один gate-тест: вызывает `make check-manifests`, проверяет exit code 0)
  AC-11: 0 новых inline-python3 блоков; все 4 генератора — отдельные `.py` файлы с unit-тестами (≥1 test per generator)
  AC-12: AGENTS.md (root) обновлён: новый инвариант «Manifest Generation Contract» с authoritative vs generated классификацией
IMPLEMENTS:            AGENTS.md инвариант 1 (Makefile — единый фасад), инвариант 2 (Makefile targets → entrypoint-manifest), инвариант 5 (entrypoint-manifest.yaml как реестр), языковая политика (новый код = Python), Strangler-Fig pattern. Суперпозиция от 2026-07-22: Option E Full Graph (score 9/10). Включает scope Brief 050 как Wave 1.
IMPACTS:
  ## Новые файлы (7)
  - core/secret-definitions.yaml — authoritative per-secret metadata (31 entry, без поля consumers)
  - core/platform-infra.yaml — infrastructure topology (networks, volumes, proxy, provides — выделяется из platform-env.yaml)
  - core/internal/scripts/generate_secrets_manifest.py — генератор secrets-manifest.yaml (~150 LOC)
  - core/internal/scripts/generate_platform_env.py — генератор platform-env.yaml (~200 LOC)
  - core/internal/scripts/generate_entrypoint_manifest.py — генератор entrypoint-manifest.yaml (allowed_verbs + gates) (~150 LOC)
  - core/internal/scripts/generate_agents_md.py — генератор AGENTS.md таблиц (~100 LOC)
  - tests/unit/test_generate_*.py — unit-тесты для каждого генератора (≥1 test per generator, ~200 LOC total)
  - tests/gates/test_gate_manifests_up_to_date.py — replacement gate (~50 LOC)
  ## Модифицируемые файлы (8+)
  - core/secrets-manifest.yaml — consumers секция становится generated (помечена # AUTO-GENERATED)
  - platform-env.yaml — profiles, port_mappings, test_ports, env_defaults становятся generated
  - core/entrypoint-manifest.yaml — allowed_verbs, gates[] становятся generated; добавляются новые таргеты
  - core/AGENTS.md — каноническая таблица операций, forbidden-списки становятся generated-секциями
  - AGENTS.md (root) — новый инвариант Manifest Generation Contract, список authoritative vs generated
  - Makefile (root) — +таргеты generate-manifests, check-manifests
  - core/internal/bootstrap/deploy/secrets_validator.py — обновить источник consumer-ов
  - core/internal/bootstrap/deploy/compose_preflight.py — обновить источник
  - core/internal/scripts/validate_module_yaml.py — обновить путь secrets-manifest
  - core/internal/provision-environment.sh — обновить источник env_defaults
  - tests/_conftest/infra.py — обновить источник env_defaults
  - .github/workflows/platform-test.yml — обновить CI-переменные
  - .github/workflows/platform-deploy.yml — обновить CI-переменные
  - .pre-commit-config.yaml — +hook check-manifests
  ## Удаляемые / заменяемые файлы (2)
  - tests/gates/test_gate_manifest_integrity.py (1085 LOC) — заменён на test_gate_manifests_up_to_date.py
  - tests/gates/test_gate_secrets_manifest.py (381 LOC) — заменён на test_gate_manifests_up_to_date.py
  ## Не затрагиваются
  - 14 × core/modules/*/module.yaml (уже authoritative)
  - 13 × core/modules/*/docker-compose.base.yml (уже authoritative)
  - 13 × core/modules/*/docker-compose.test.yml (уже authoritative)
  - core/templates/template-manifest.yaml (ортогонален — template registry)
  - core/schemas/*.json (validation contracts)
  - node-configs/*/node.yaml (per-node desired state)
  - templates/template-*/ai-platform.yaml (per-project manifest)
  - core/modules/hermes-agent/build/skills/.bundled_manifest (skills hash)
REQUIRES:
  - Python ≥3.10
  - PyYAML (уже в зависимостях)
  - 14 × module.yaml (существующие)
  - 13 × docker-compose.base.yml (существующие)
  - 13 × docker-compose.test.yml (существующие)
  - Существующий secrets-manifest.yaml (для миграции → secret-definitions.yaml)
  - Существующий platform-env.yaml (для миграции → platform-infra.yaml)
  - Существующий entrypoint-manifest.yaml (для миграции — сохраняет mechanism/delegates_to/forbidden)
  - Существующие AGENTS.md (root, core/)
  - GNU Make ≥4.0 (для `make -np` парсинга .PHONY targets)
  - pytest ≥7.0 (для --collect-only --marker=gate)
  - git ≥2.30 (для git diff --exit-code)
$END_ARTIFACT_CONTRACT

---

## 1. Problem Statement

### 1.1 Текущий манифестный ландшафт: 10 дублирований, 0 автоматической синхронизации

Платформа содержит 10+ манифестных файлов, многие из которых дублируют одни и те же данные. Синхронизация — ручная. Валидация — реактивная (gates ловят расхождение после commit+push → CI red → fix → push again).

**Полный каталог дублирований:**

| # | Что дублируется | Authoritative source | Duplicate в | Severity |
|---|----------------|---------------------|-------------|----------|
| **D1** | Список make-таргетов (`allowed_verbs`) | Makefile `.PHONY` | entrypoint-manifest.yaml + core/AGENTS.md canon table | **CRITICAL** |
| **D2** | `forbidden_directories`, `forbidden_scripts`, `forbidden_verbs` | entrypoint-manifest.yaml (выбрано SSoT) | core/AGENTS.md §Forbidden | HIGH |
| **D3** | Gate-регистрации (`gates[]`: id + test_file) | Файловая система (test файлы с `@pytest.mark.gate`) | entrypoint-manifest.yaml + test_inventory.yaml | HIGH |
| **D4** | `consumers[]` секретов | 14 × module.yaml `env_requires` | secrets-manifest.yaml | **CRITICAL** |
| **D5** | `env_defaults` CI-значения секретов | secret-definitions.yaml (новый) | platform-env.yaml | HIGH |
| **D6** | `profiles` (COMPOSE_PROFILES) | Directory listing `core/modules/*/` | platform-env.yaml | MEDIUM |
| **D7** | `port_mappings` / `test_ports` | docker-compose.base.yml / .test.yml ports | platform-env.yaml | MEDIUM |
| **D8** | `delegates_to` пути | Файловая система (существование entrypoint-скриптов) | entrypoint-manifest.yaml | LOW |
| **D9** | `mechanism` (shell-script / python-script / git-diff) | Makefile recipes | entrypoint-manifest.yaml | MEDIUM |
| **D10** | Каноническая таблица операций (48 строк) | entrypoint-manifest.yaml | core/AGENTS.md | HIGH |

**Почему gates недостаточно:**

Текущий подход «gates валидируют консистентность» имеет фундаментальный недостаток — это **реактивная** защита. Цикл разработчика:

```
1. Добавить module.yaml#env_requires = [NEW_SECRET]       ← правильно
2. Забыть обновить secrets-manifest.yaml#consumers        ← человек забыл
3. git push → CI red (test_gate_secrets_manifest.py)       ← gate поймал
4. Исправить → git push again                             ← потрачено 2 цикла CI
```

Правильное решение — сделать невозможным само рассогласование: derived data не редактируется, она генерируется. Разработчик правит только authoritative source, генератор обновляет всё остальное.

### 1.2 Масштаб проблемы

| Метрика | Значение |
|---------|----------|
| Всего манифестных файлов | 10 (authoritative + manual mirrors) |
| Дублирований данных | 10 |
| LOC ручной синхронизации (суммарно) | ~300 строк на каждый новый модуль |
| LOC реактивных gate-тестов | 1466 (2 файла) |
| Файлов, которые можно генерировать | 4 (secrets-manifest, platform-env, entrypoint-manifest [частично], AGENTS.md tables) |
| Новых authoritative source файлов | 2 (secret-definitions.yaml, platform-infra.yaml) |

---

## 2. Superposition: 7 вариантов решения

Полная суперпозиция проведена 2026-07-22. Варианты ранжированы по coverage (процент устранённых дублирований) и systemic depth (насколько решение устраняет корневую причину, а не симптомы).

### Option A: Brief 050 as-is — Generation for Secrets + Platform-Env [score: 4/10]
**Coverage:** 40% (D4-D7). **Systemic depth:** низкая.
- Решает самые опасные дублирования (secrets — security surface)
- Не трогает entrypoint-manifest.yaml, AGENTS.md, Makefile — крупнейшие источники дрейфа (D1, D3, D10)
- После внедрения: добавление нового таргета всё ещё требует ручной правки 3 файлов

### Option B: Makefile как SSoT → entrypoint-manifest.yaml generated [score: 5/10]
**Coverage:** 60% (D1, D4-D7, D9). **Systemic depth:** средняя.
- Парсинг Makefile для `mechanism` ненадёжен (GNU Make — не декларативный язык)
- Не решает D3 (gates), D10 (AGENTS.md)

### Option C: entrypoint-manifest.yaml как SSoT → AGENTS.md generated [score: 6/10]
**Coverage:** 50% (D2, D4-D7, D10). **Systemic depth:** низкая.
- Паллиатив: entrypoint-manifest.yaml САМ дублирует Makefile
- Устраняет symptoms (AGENTS.md drift), но не root cause (entrypoint-manifest drift)

### Option D: Module-Driven Full Generation [score: 5/10]
**Coverage:** 30% (D4, D6, D7). **Systemic depth:** низкая.
- module.yaml по определению не содержит операции, gate-регистрации, секреты, инфраструктурную топологию
- 30% coverage — необходимый, но недостаточный компонент

### Option E: Трёхслойная Authoritative Graph Architecture [score: 9/10]
**Coverage:** 100% (D1-D10). **Systemic depth:** высокая.
- Формальная иерархия: L0 (authoritative sources) → L1 (generated manifests) → L2 (documentation views)
- Каждый факт имеет ровно один authoritative source
- Generated files коммитятся для читаемости, но НИКОГДА не редактируются вручную
- Единый CI gate `make check-manifests` (~50 LOC) заменяет 1466 LOC реактивных тестов
- Проект УЖЕ использует этот паттерн: `test-inventory-sync`, `discover-modules`, `_topo_sort.py`

### Option F: Code-as-Config — замена YAML на Python [score: 3/10]
**Coverage:** технически 100%, но ломает экосистему (docker compose, yamllint, SOPS, CI — всё завязано на YAML). Не соответствует Small Simple Blocks.

### Option G: Git-Hook Auto-Sync [score: 4/10]
**Coverage:** 100% в теории. **Systemic depth:** нулевая.
- Всё ещё реактивная модель, просто автоматизированная
- Hook может быть пропущен (`--no-verify`), сломан, дать неверный результат
- Не решает проблему концептуально

### Recommendation: Option E
Обоснование: единственный вариант, который (a) покрывает 100% дублирований, (b) устраняет корневую причину (manual drift), (c) сокращает, а не добавляет сложность (1466 LOC gate-тестов → 50 LOC check-manifests), (d) соответствует существующим паттернам проекта.

---

## 3. Solution: Трёхслойная Authoritative Graph Architecture

### 3.1 Архитектура слоёв

```
╔═══════════════════════════════════════════════════════════════════════════╗
║ L0: AUTHORITATIVE SOURCES (humans/agents edit — 6 категорий источников)  ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  module.yaml          (×14) — конфигурация модуля                        ║
║    ├─ env_requires    → consumers для secrets-manifest.yaml               ║
║    ├─ depends_on      → topological sort для deploy groups                ║
║    ├─ install_type    → docker vs system классификация                    ║
║    └─ name            → имя профиля в COMPOSE_PROFILES                    ║
║                                                                           ║
║  docker-compose.base.yml (×13) + docker-compose.test.yml (×13)           ║
║    └─ services.<name>.ports → port_mappings + test_ports                  ║
║                                                                           ║
║  secret-definitions.yaml (NEW) — метаданные секретов                      ║
║    ├─ name, tier, source, charset, gen_command, ci_default, note          ║
║    └─ (поле consumers ОТСУТСТВУЕТ — вычисляется из module.yaml)           ║
║                                                                           ║
║  platform-infra.yaml (NEW) — инфраструктурная топология                   ║
║    ├─ networks, volumes, proxy, provides                                  ║
║    └─ (поля profiles, port_mappings, env_defaults ОТСУТСТВУЮТ —           ║
║         вычисляются из compose + module-dirs + secret-defs)               ║
║                                                                           ║
║  Makefile (root, core/modules/*, projects/*)                              ║
║    └─ .PHONY targets → allowed_verbs в entrypoint-manifest.yaml           ║
║                                                                           ║
║  pytest test files (tests/gates/test_gate_*.py)                           ║
║    └─ @pytest.mark.gate → gates[] в entrypoint-manifest.yaml              ║
║                                                                           ║
║  ENTRYPONT-MANIFEST.YAML (частично authoritative — ручные секции)         ║
║    ├─ mechanism: shell-script | python-script | git-diff  ← design        ║
║    ├─ delegates_to: path                                  ← design        ║
║    ├─ forbidden_directories, forbidden_scripts,           ← security      ║
║    │  forbidden_verbs, name_linter                                        ║
║    └─ description (per-target и per-gate)                 ← human-written ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
                                    │
                                    │  make generate-manifests
                                    ▼
╔═══════════════════════════════════════════════════════════════════════════╗
║ L1: GENERATED MANIFESTS (tool produces, committed, NEVER manually edited) ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  core/secrets-manifest.yaml                                               ║
║    ← secret-definitions.yaml + module.yaml#env_requires ×14               ║
║    Секция consumers помечена # AUTO-GENERATED                             ║
║                                                                           ║
║  platform-env.yaml                                                        ║
║    ← platform-infra.yaml + compose ports + module dirs + secret-defs      ║
║    Секции profiles, port_mappings, test_ports, env_defaults — generated   ║
║    Секции networks, volumes, proxy, provides — copied from infra          ║
║                                                                           ║
║  core/entrypoint-manifest.yaml (частично generated)                       ║
║    ← Makefile .PHONY → allowed_verbs                                      ║
║    ← pytest --marker=gate → gates[]                                       ║
║    ← existing manifest → mechanism, delegates_to, forbidden_* (сохраняются)║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
                                    │
                                    │  make generate-manifests
                                    ▼
╔═══════════════════════════════════════════════════════════════════════════╗
║ L2: DOCUMENTATION VIEWS (generated from L1, human-readable)               ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║  core/AGENTS.md (частично generated)                                      ║
║    ← entrypoint-manifest.yaml → Каноническая таблица операций             ║
║    ← entrypoint-manifest.yaml → Forbidden-списки                          ║
║    ← entrypoint-manifest.yaml → Разрешённые глаголы                       ║
║    (description, rationale, cross-layer rules — остаются ручным Markdown) ║
║                                                                           ║
║  AGENTS.md (root) — новый инвариант Manifest Generation Contract           ║
║    (ручной — документирует саму архитектуру генерации)                     ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### 3.2 Принцип: один факт — один владелец

| Факт | Authoritative source | Куда генерируется |
|------|---------------------|-------------------|
| Какие секреты нужны модулю | `module.yaml#env_requires` | `secrets-manifest.yaml#consumers[]` |
| Метаданные секрета (tier, charset, ci_default) | `secret-definitions.yaml` | `secrets-manifest.yaml#secrets[]` (кроме consumers), `platform-env.yaml#env_defaults` |
| Какие порты expose'ит сервис | `docker-compose.base.yml#services.<name>.ports` | `platform-env.yaml#port_mappings` |
| Какие порты expose'ит тестовый сервис | `docker-compose.test.yml#services.<name>.ports` | `platform-env.yaml#test_ports` |
| Какие Docker-сети и volumes существуют | `platform-infra.yaml` | `platform-env.yaml#networks`, `#volumes` |
| Какие make-таргеты разрешены | `Makefile .PHONY` | `entrypoint-manifest.yaml#allowed_verbs` |
| Какие gate-тесты существуют | `@pytest.mark.gate` в test файлах | `entrypoint-manifest.yaml#gates[]` |
| Какой механизм у таргета (shell/python/git) | `entrypoint-manifest.yaml` (ручная секция) | `core/AGENTS.md` каноническая таблица |
| Какие скрипты/директории/глаголы запрещены | `entrypoint-manifest.yaml` (ручная секция) | `core/AGENTS.md` forbidden-списки |

### 3.3 Почему entrypoint-manifest.yaml — частичная генерация

**Генерируется** (выводимо из данных):
- `allowed_verbs` ← `make -np | grep '^.PHONY'` — механический список целей
- `gates[]` ← `pytest --collect-only --marker=gate -q` — файловая система

**НЕ генерируется** (содержит design decisions):
- `mechanism: shell-script | python-script | git-diff` — выбор механизма — архитектурное решение (например, `scripts-audit` — shell, `templates-render` — python, `check-manifests` — git-diff)
- `delegates_to: core/entrypoints/xxx.sh` — выбор entrypoint-скрипта — архитектурное решение
- `forbidden_directories`, `forbidden_scripts`, `forbidden_verbs` — security decisions, требуют Architect approval
- `name_linter` — конфигурация линтера имён
- `description` (per-target и per-gate) — человекочитаемые описания

**Правило сохранения:** при регенерации генератор читает существующий entrypoint-manifest.yaml, извлекает ручные поля для каждого target/gate, и записывает их в новый сгенерированный файл. Таким образом, ручные поля переживают регенерацию.

### 3.4 Почему Makefile-парсинг надёжен для .PHONY, но не для recipes

```makefile
# Это можно надёжно извлечь:
.PHONY: deploy bootstrap-node context-promote test gate ...

# Это НЕЛЬЗЯ надёжно извлечь (макросы, условная логика, $(call ...)):
deploy:
	@$(call require_project,$(PROJECT))
	@$(GIT) push origin $$(git branch --show-current)
```

Генератор использует `make -np` (dry-run, print database) для извлечения списка `.PHONY` целей — это детерминированная операция, не требующая парсинга recipes. `mechanism` и `delegates_to` остаются ручными, потому что их автоматическое определение потребовало бы полного статического анализа Makefile с раскрытием всех макросов — задача на порядок сложнее и неоправданна.

---

## 4. Data Flow: сценарии до и после

### 4.1 Добавление нового модуля

**До (текущее состояние):**
```
1. Создать core/modules/new-module/module.yaml           ← 1 правка
   └── env_requires: [POSTGRES_PASSWORD, NEW_SECRET]
2. Создать core/modules/new-module/docker-compose.base.yml ← 1 правка
   └── services.new-module.ports: ["9090:9090"]
3. Обновить core/secrets-manifest.yaml                   ← РУЧНАЯ синхронизация (D4)
   └── POSTGRES_PASSWORD.consumers += new-module
   └── NEW_SECRET (новый entry если секрет новый)
4. Обновить platform-env.yaml                            ← РУЧНАЯ синхронизация (D6, D7)
   └── port_mappings += NEW_MODULE_PORT: 9090
   └── profiles += new-module
5. Обновить entrypoint-manifest.yaml                     ← РУЧНАЯ синхронизация (D1)
   └── allowed_verbs += ... (если новый таргет)
6. Обновить core/AGENTS.md                               ← РУЧНАЯ синхронизация (D10)
   └── Каноническая таблица операций += ...
7. git push → CI red (если забыли шаг 3-6) → fix → push again
```
**Ручных правок:** 4-6. **Шанс ошибки:** высокий (человек забывает 1+ шагов).

**После (с генерацией):**
```
1. Создать core/modules/new-module/module.yaml           ← 1 правка
   └── env_requires: [POSTGRES_PASSWORD, NEW_SECRET]
2. Создать core/modules/new-module/docker-compose.base.yml ← 1 правка
   └── services.new-module.ports: ["9090:9090"]
3. Если NEW_SECRET — новый: добавить в secret-definitions.yaml ← 1 правка (authoritative)
4. make generate-manifests                               ← АВТОМАТИЧЕСКИ
   └── secrets-manifest.yaml: consumers += new-module для POSTGRES_PASSWORD + NEW_SECRET
   └── platform-env.yaml: port_mappings += NEW_MODULE_PORT: 9090
   └── platform-env.yaml: profiles += new-module
   └── entrypoint-manifest.yaml: без изменений (если не добавляли таргет)
5. git add -A && git commit && git push → CI green       ← make check-manifests проходит
```
**Ручных правок:** 2-3 (только authoritative sources). **Шанс ошибки:** нулевой (generated files не редактируются вручную).

### 4.2 Добавление нового make-таргета

**До:**
```
1. Makefile: новый .PHONY target + recipe                ← 1 правка
2. entrypoint-manifest.yaml: allowed_verbs += new-target ← РУЧНАЯ синхронизация
3. entrypoint-manifest.yaml: новый target section        ← РУЧНАЯ синхронизация
4. core/AGENTS.md: каноническая таблица += new-target    ← РУЧНАЯ синхронизация
```
**Ручных правок:** 3-4.

**После:**
```
1. Makefile: новый .PHONY target + recipe                ← 1 правка
2. entrypoint-manifest.yaml: новый target section        ← 1 правка (механизм + delegates_to — design decision)
3. make generate-manifests                               ← АВТОМАТИЧЕСКИ
   └── allowed_verbs += new-target
   └── core/AGENTS.md: каноническая таблица += new-target
```
**Ручных правок:** 2 (Makefile + manifest target section). Сокращение на 40%.

### 4.3 Добавление нового gate-теста

**До:**
```
1. Создать tests/gates/test_gate_new_check.py с @pytest.mark.gate  ← 1 правка
2. entrypoint-manifest.yaml: gates[] += {id: new-check, ...}        ← РУЧНАЯ синхронизация
3. test_inventory.yaml: регенерировать                              ← make test-inventory-sync (уже авто)
```
**Ручных правок:** 2.

**После:**
```
1. Создать tests/gates/test_gate_new_check.py с @pytest.mark.gate  ← 1 правка
2. make generate-manifests                                          ← АВТОМАТИЧЕСКИ
   └── entrypoint-manifest.yaml: gates[] += {id: new-check, test_file: test_gate_new_check.py}
   └── test_inventory.yaml: регенерирован
```
**Ручных правок:** 1. Сокращение на 50%.

---

## 5. Implementation Plan (Strangler-Fig × 4 волны)

### Wave 1: Secrets + Platform-Env Generation (scope Brief 050)
**Сложность:** Средняя. **Покрытие:** D4-D7.

1. Создать `core/secret-definitions.yaml` — миграция из `secrets-manifest.yaml` (31 секрет, без поля consumers, +поле ci_default)
2. Создать `core/platform-infra.yaml` — выделить ручные секции из `platform-env.yaml` (networks, volumes, proxy, provides)
3. Написать `core/internal/scripts/generate_secrets_manifest.py` (~150 LOC)
4. Написать `core/internal/scripts/generate_platform_env.py` (~200 LOC)
5. Интегрировать в Makefile: `make generate-manifests` (вызывает оба генератора), `make check-manifests` (git diff --exit-code)
6. Зарегистрировать в `entrypoint-manifest.yaml`: новые таргеты + разрешённые глаголы
7. Обновить потребителей `env_defaults` и `secrets-manifest.yaml`
8. Unit-тесты: `tests/unit/test_generate_secrets_manifest.py`, `tests/unit/test_generate_platform_env.py`
9. Gate `test_gate_manifests_up_to_date.py` — вызывает `make check-manifests`

### Wave 2: Makefile → entrypoint-manifest.yaml (allowed_verbs + target sections)
**Сложность:** Низкая. **Покрытие:** D1, D9.

1. Написать `core/internal/scripts/generate_entrypoint_manifest.py` (~150 LOC):
   - `extract_phony_targets(makefile_path)` → `list[str]` (через `make -np --dry-run`)
   - `load_existing_manifest(path)` → `dict` (сохраняет mechanism, delegates_to, forbidden, descriptions)
   - `merge(allowed_verbs, existing)` → `str` (YAML output)
   - CLI: `--makefile`, `--existing-manifest`, `--output`
2. Интегрировать в `make generate-manifests` (вызывается после генераторов Wave 1)
3. Unit-тесты: `tests/unit/test_generate_entrypoint_manifest.py`

### Wave 3: pytest → entrypoint-manifest.yaml (gates[])
**Сложность:** Низкая. **Покрытие:** D3.

1. Расширить `generate_entrypoint_manifest.py`:
   - `collect_gate_tests(tests_dir)` → `list[GateDef]` (через `pytest --collect-only --marker=gate -q`)
   - `merge_gates(gate_defs, existing)` → обновлённая секция `gates[]`
2. Gate-тесты без `description` в manifest получают description = docstring теста
3. Unit-тесты: дополнить `test_generate_entrypoint_manifest.py`

### Wave 4: entrypoint-manifest.yaml → AGENTS.md (documentation views)
**Сложность:** Низкая. **Покрытие:** D2, D10.

1. Написать `core/internal/scripts/generate_agents_md.py` (~100 LOC):
   - `generate_canon_table(manifest)` → Markdown table rows
   - `generate_forbidden_lists(manifest)` → Markdown lists
   - `inject_into_md(md_path, table_marker, new_content)` → инъекция между маркерами `<!-- GENERATED:START -->` и `<!-- GENERATED:END -->`
2. AGENTS.md получает generated-секции, окружённые маркерами:
   ```markdown
   ## Канонические операции
   <!-- GENERATED:START:canon-operations -->
   | Канонический таргет | Операция | Сигнатура | Делегирует в (internal) |
   |---|---|------|---|
   | ... | ... | ... | ... |
   <!-- GENERATED:END:canon-operations -->
   ```
3. Unit-тесты: `tests/unit/test_generate_agents_md.py`

### CI Integration (сквозная)

1. `make check-manifests` в `.github/workflows/platform-test.yml` (gate step)
2. Pre-commit hook: `make check-manifests` (блокирует commit с ручными правками generated files)
3. `test_gate_manifests_up_to_date.py` — единственный replacement gate (~50 LOC):
   ```python
   def test_manifests_up_to_date():
       result = subprocess.run(["make", "check-manifests"], capture_output=True, text=True)
       assert result.returncode == 0, f"Generated manifests are out of date:\n{result.stdout}"
   ```
4. Удалить `test_gate_manifest_integrity.py` (1085 LOC) и `test_gate_secrets_manifest.py` (381 LOC) после подтверждения, что новый gate покрывает все их проверки

---

## 6. Файловый манифест (что будет в репозитории после Implementation)

```
core/
├── secret-definitions.yaml          ← NEW authoritative
├── platform-infra.yaml              ← NEW authoritative
├── secrets-manifest.yaml            ← (modified) generated секции помечены # AUTO-GENERATED
├── entrypoint-manifest.yaml         ← (modified) allowed_verbs, gates[] — generated
├── AGENTS.md                        ← (modified) таблицы — generated секции
├── internal/scripts/
│   ├── generate_secrets_manifest.py ← NEW (~150 LOC)
│   ├── generate_platform_env.py     ← NEW (~200 LOC)
│   ├── generate_entrypoint_manifest.py ← NEW (~150 LOC)
│   └── generate_agents_md.py       ← NEW (~100 LOC)

platform-env.yaml                    ← (modified) generated секции

AGENTS.md (root)                     ← (modified) новый инвариант

Makefile                             ← (modified) +generate-manifests, +check-manifests

tests/
├── unit/
│   ├── test_generate_secrets_manifest.py     ← NEW (~50 LOC)
│   ├── test_generate_platform_env.py         ← NEW (~50 LOC)
│   ├── test_generate_entrypoint_manifest.py  ← NEW (~50 LOC)
│   └── test_generate_agents_md.py            ← NEW (~50 LOC)
└── gates/
    ├── test_gate_manifests_up_to_date.py     ← NEW (~50 LOC)
    ├── test_gate_manifest_integrity.py       ← DELETED (1085 LOC)
    └── test_gate_secrets_manifest.py         ← DELETED (381 LOC)

.pre-commit-config.yaml              ← (modified) +hook check-manifests
.github/workflows/
├── platform-test.yml                ← (modified) CI gate step
└── platform-deploy.yml              ← (modified) CI variables source
```

**Чистый эффект:** +600 LOC нового кода (генераторы + тесты), −1466 LOC старых gate-тестов. **Нетто: −866 LOC при 10× улучшении coverage.**

---

## 7. Risk Assessment

| Риск | Вероятность | Impact | Mitigation |
|------|------------|--------|------------|
| Генератор выдаёт невалидный YAML | Низкая | **HIGH** | Unit-тесты на каждый генератор + `yamllint` в CI |
| `make -np` даёт неполный список .PHONY на macOS (BSD make) | **Высокая** | HIGH | Detection: `make --version`. Если GNU Make недоступен — fallback: парсить Makefile напрямую (grep `^.PHONY:`) |
| `pytest --collect-only` фейлится без работающего окружения | Средняя | MEDIUM | Генератор gates запускается только при наличии `pytest` в PATH; CI гарантирует его наличие. Локально — graceful skip с warning |
| Генератор перезаписывает ручные правки в generated-файлах | **Высокая** (by design) | LOW | CI gate `make check-manifests` блокирует merge. Generated-секции явно помечены `# AUTO-GENERATED`. Принятие: это фича, не баг |
| Существующие скрипты сломаны после миграции источника данных | Средняя | **MEDIUM** | Интеграционные тесты + `make test MARKER=integration` |
| `provides.dsn_template` не выводится из compose | Гарантировано | LOW | Остаётся в ручном `platform-infra.yaml` |
| `description` поля в gates[] теряются при регенерации | Низкая | LOW | Генератор сохраняет существующие описания; для новых gate — docstring теста как fallback |
| Конфликт: модуль удалён из fs, но остался в git (generated файл ссылается на него) | Средняя | MEDIUM | `make check-manifests` обнаружит расхождение (consumers ссылаются на несуществующий модуль). Разработчик должен сделать `make generate-manifests` после удаления |

---

## 8. Decision Log

| Дата | Решение | Обоснование |
|------|---------|-------------|
| 2026-07-22 | Выбрана Option E (трёхслойная Authoritative Graph) из суперпозиции 7 вариантов | Score 9/10. Покрытие 100% дублирований. Проект уже использует паттерн генерации. Каждый authoritative source имеет одного владельца. Zero manual drift по определению |
| 2026-07-22 | Brief 050 сохранён как Wave 1 в составе общего плана | Brief 050 корректно спроектирован для secrets + platform-env Scope. Не отменяется, а включается как первый шаг |
| 2026-07-22 | entrypoint-manifest.yaml — частичная генерация, не полная | `mechanism`, `delegates_to`, `forbidden_*` содержат design decisions, невыводимые из данных. Полная генерация создала бы риск неверных mechanism-значений |
| 2026-07-22 | AGENTS.md — generated секции с маркерами `<!-- GENERATED:START/END -->` | Инъекция между маркерами сохраняет ручной контент (description, rationale, cross-layer rules) и позволяет генератору обновлять только таблицы |
| 2026-07-22 | `make check-manifests` (git diff) — единственный CI gate вместо 15 специализированных | Один механизм проверки для всех generated файлов. Проще, надёжнее, покрывает все будущие generated файлы автоматически |
| 2026-07-22 | `secret-definitions.yaml` и `platform-infra.yaml` — новые authoritative файлы, не расширение существующих | Разделение на declaration (secret-defs) и projection (secrets-manifest) чище архитектурно. In-place update создал бы файлы смешанной ответственности |
| 2026-07-22 | `ci_default` в `secret-definitions.yaml`, не в `platform-infra.yaml` | `ci_default` — property секрета (его тестовое значение), логически принадлежит определению секрета, не инфраструктуре |
| 2026-07-22 | `template-manifest.yaml` и `.bundled_manifest` НЕ затрагиваются | Ортогональны — template registry и skills hash не имеют дублирований с другими манифестами |

---

## 9. Что НЕ входит в scope (explicit non-goals)

1. **Генерация template-manifest.yaml** — template registry ортогонален, не дублируется с другими манифестами
2. **Генерация module.yaml** — module.yaml уже authoritative, генерировать не из чего
3. **Генерация docker-compose файлов** — compose-файлы уже authoritative (пишутся разработчиком модуля)
4. **Генерация ai-platform.yaml (per-project)** — структура проста (7 полей), дублирование между проектами минимально, генератор не окупается
5. **Генерация node.yaml** — per-node desired state, нет дублирования с другими манифестами
6. **Удаление entrypoint-manifest.yaml** — нарушит инвариант 5; manifest остаётся как machine-readable реестр, просто часть его секций генерируется
7. **Генерация `.bundled_manifest`** — ортогональный хеш-манифест для hermes-agent skills, не связан с архитектурными манифестами

$END_BRIEF
