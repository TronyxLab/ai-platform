# DevPlan 051 — Manifest Generation: устранить ручной дрейф между authoritative sources и derived artifacts

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить ручную синхронизацию секретов/переменных между 5+ файлами (secrets-manifest.yaml, platform-env.yaml, SMOKE_ENV, helpers/__init__.py, .env.example, CI workflows). Каждая переменная имеет ровно один authoritative source; все derived-файлы генерируются. CI gate блокирует push если generated файлы diverged с source.
DESCRIPTION:           Три волны: (1) secret-definitions.yaml + platform-infra.yaml как authoritative → генерация secrets-manifest.yaml, platform-env.yaml, SMOKE_ENV, helpers; (2) Makefile .PHONY → entrypoint-manifest.yaml allowed_verbs + gates[] из pytest маркеров; (3) entrypoint-manifest.yaml → AGENTS.md таблица. `test_gate_manifest_integrity.py` рефакторен (freshness-проверки удалены, структурные сохранены). `test_gate_secrets_manifest.py` заменён на freshness gate. SMOKE_ENV и helpers/__init__.py читают сгенерированный источник, не хардкодят значения.
RATIONALE:             Проблема не гипотетическая: 2026-07-22 два часа потрачено на отладку отсутствия CLICKHOUSE_PASSWORD в SMOKE_ENV — переменная была в module.yaml, secrets-manifest.yaml, platform-env.yaml, helpers/__init__.py, но не в SMOKE_ENV. После правок Brief 051 (критика: потеря структурной валидации, overstated coverage, D2/D8/D9 не решаются генерацией).
ACCEPTANCE_CRITERIA:
  AC-1: `make generate-manifests` создаёт `secrets-manifest.yaml` с `consumers`, вычисленными из `module.yaml#env_requires` (0 divergences)
  AC-2: `make generate-manifests` создаёт `platform-env.yaml` с `profiles`, `port_mappings`, `test_ports`, `env_defaults` из authoritative sources
  AC-3: `make generate-manifests` обновляет `entrypoint-manifest.yaml#allowed_verbs` из Makefile .PHONY, `#gates[]` из `pytest --collect-only --marker=gate`
  AC-4: `make generate-manifests` генерирует `tests/_conftest/smoke_env_generated.py` — словарь тестовых значений для всех секретов с `ci_default` из `secret-definitions.yaml`
  AC-5: `make generate-manifests` генерирует `tests/helpers/env_defaults_generated.py` — константы тестовых паролей из `secret-definitions.yaml#ci_default`
  AC-6: `make generate-manifests` обновляет AGENTS.md (core/) таблицу канонических операций из entrypoint-manifest.yaml
  AC-7: `make check-manifests` → exit 1 если любой generated файл diverged (git diff --exit-code)
  AC-8: `test_gate_manifest_integrity.py` рефакторен: freshness-проверки удалены (covered check-manifests), структурные проверки (delegates_to paths, forbidden_* enforcement, naming, module lifecycle) сохранены, ~500 LOC
  AC-9: `test_gate_secrets_manifest.py` (381 LOC) заменён на `test_gate_manifests_up_to_date.py` (~50 LOC) — вызывает `make check-manifests` и проверяет exit code 0
  AC-10: При добавлении модуля с `env_requires: [NEW_SECRET]` и compose-портом 9090 → `make generate-manifests` автоматически обновляет: secrets-manifest.yaml#consumers, platform-env.yaml#profiles/port_mappings/env_defaults, smoke_env_generated.py, env_defaults_generated.py
  AC-11: SMOKE_ENV в smoke.py собирается как `{**STATIC_SMOKE_ENV, **smoke_env_generated.SMOKE_ENV_GENERATED}` — статические platform-переменные (PLATFORM_DOMAIN, COMPOSE_PROJECT_NAME, COMPOSE_PROFILES, etc.) остаются ручными, секреты из generated
  AC-12: `make gate MODE=fast` зелёный
  AC-13: 0 inline-python3 блоков; 4 генератора — отдельные `.py` файлы с unit-тестами (≥1 test per generator)
IMPLEMENTS:            AGENTS.md инвариант 1 (Makefile — единый фасад), инвариант 5 (entrypoint-manifest.yaml как реестр), языковая политика (Python), Strangler-Fig pattern
IMPACTS:
  ## Новые файлы (8)
  - core/secret-definitions.yaml — authoritative per-secret metadata (31 entry, без consumers)
  - core/platform-infra.yaml — infrastructure topology (networks, volumes, proxy, provides)
  - core/internal/scripts/generate_secrets_manifest.py (~150 LOC)
  - core/internal/scripts/generate_platform_env.py (~200 LOC)
  - core/internal/scripts/generate_entrypoint_manifest.py (~150 LOC)
  - core/internal/scripts/generate_agents_md.py (~100 LOC)
  - tests/_conftest/smoke_env_generated.py — AUTO-GENERATED, не редактировать вручную
  - tests/helpers/env_defaults_generated.py — AUTO-GENERATED, не редактировать вручную
  ## Модифицируемые (10)
  - core/secrets-manifest.yaml — consumers → AUTO-GENERATED
  - platform-env.yaml — profiles, port_mappings, test_ports, env_defaults → AUTO-GENERATED
  - core/entrypoint-manifest.yaml — allowed_verbs, gates[] → AUTO-GENERATED
  - core/AGENTS.md — таблица операций, forbidden-списки → generated секции между <!-- GENERATED:START/END -->
  - AGENTS.md (root) — новый инвариант Manifest Generation Contract
  - Makefile (root) — +таргеты generate-manifests, check-manifests
  - tests/_conftest/smoke.py — SMOKE_ENV собирается из статических + generated
  - tests/helpers/__init__.py — убрать хардкод паролей, импортировать из env_defaults_generated
  - tests/gates/test_gate_manifest_integrity.py — рефакторинг (freshness удалить, structural сохранить)
  - .pre-commit-config.yaml — +hook check-manifests
  ## Удаляемые (3)
  - tests/gates/test_gate_secrets_manifest.py (381 LOC) — заменён
  - tests/helpers/__init__.py — хардкодженные константы паролей (перенесены в env_defaults_generated.py)
  - tests/_conftest/smoke.py — хардкодженные секретные значения (заменены на импорт из generated)
REQUIRES:
  - Python ≥3.10, PyYAML (уже в зависимостях)
  - GNU Make ≥4.0 (gmake на macOS: /opt/homebrew/bin/gmake)
  - pytest ≥7.0
  - 14 × module.yaml, 13 × docker-compose.base.yml, 13 × docker-compose.test.yml
  - Существующий secrets-manifest.yaml (для миграции → secret-definitions.yaml)
  - Существующий platform-env.yaml (для миграции → platform-infra.yaml)
$END_ARTIFACT_CONTRACT

---

## 1. Проблема

После добавления новой переменной в `module.yaml#env_requires` разработчик должен вручную обновить **5+ файлов**. Забыл один — ошибка «variable not set» в рантайме. Пример: `CLICKHOUSE_PASSWORD` есть в module.yaml, secrets-manifest.yaml, platform-env.yaml, helpers/__init__.py, но **отсутствует в SMOKE_ENV** → 2 часа отладки 2026-07-22.

### 1.1 Текущий ландшафт: 5 manual sync points на одну переменную

```
module.yaml#env_requires        ← authoritative (кто потребляет)
        │
        ├── secrets-manifest.yaml    ← РУЧНАЯ синхронизация consumers[]
        ├── platform-env.yaml        ← РУЧНАЯ синхронизация env_defaults
        ├── tests/_conftest/smoke.py ← РУЧНАЯ синхронизация SMOKE_ENV
        ├── tests/helpers/__init__.py← РУЧНАЯ синхронизация _SECRET_VALUE
        └── .env.example             ← РУЧНАЯ синхронизация doc-секции
```

### 1.2 Что генерируется, что остаётся ручным

| Данные | Authoritative source | Генерация |
|--------|---------------------|-----------|
| consumers[] секретов | `module.yaml#env_requires` | `secrets-manifest.yaml` |
| env_defaults тестовые значения | `secret-definitions.yaml#ci_default` | `platform-env.yaml`, `smoke_env_generated.py`, `env_defaults_generated.py` |
| profiles (COMPOSE_PROFILES) | directory listing `core/modules/*/` | `platform-env.yaml` |
| port_mappings / test_ports | `docker-compose.base.yml` / `.test.yml` ports | `platform-env.yaml` |
| allowed_verbs (make targets) | `gmake -np` → `.PHONY:` | `entrypoint-manifest.yaml` |
| gates[] (gate registry) | `pytest --collect-only --marker=gate -q` | `entrypoint-manifest.yaml` |
| AGENTS.md таблица операций | `entrypoint-manifest.yaml` | `core/AGENTS.md` (generated-секции) |
| mechanism, delegates_to, forbidden_* | **РУЧНОЙ** — design decisions | НЕ генерируется, валидируется `test_gate_manifest_integrity.py` |

---

## 2. Implementation

### Wave 1: Authoritative → generated для секретов и платформенных переменных (D4-D7, SMOKE_ENV)

#### Step 1.1: Создать `core/secret-definitions.yaml`
Миграция из `secrets-manifest.yaml`: все 31+ секрет, поля `name`, `tier`, `source`, `charset`, `gen_command`, `ci_default`, `note`. Поле `consumers` **удалено**. `ci_default` заполняется из `platform-env.yaml#env_defaults`.

```yaml
# core/secret-definitions.yaml
secrets:
  - name: CLICKHOUSE_PASSWORD
    tier: required
    source: sops
    charset: "^[A-Za-z0-9._-]+$"
    ci_default: "test-clickhouse-pwd-not-for-prod"
    note: "openssl rand -base64 32"
```

#### Step 1.2: Создать `core/platform-infra.yaml`
Ручные секции из `platform-env.yaml`: `networks`, `volumes`, `proxy`, `provides`. `profiles`, `port_mappings`, `test_ports`, `env_defaults` → удалены.

#### Step 1.3: `core/internal/scripts/generate_secrets_manifest.py` (~150 LOC)
- `load_secret_definitions(path)` → `list[SecretDef]`
- `load_module_yamls(modules_dir)` → `list[ModuleDef]`
- `compute_consumers(secret_name, modules)` → `list[str]` (переиспользовать логику из `_topo_sort.py`)
- YAML output: для каждого секрета `consumers = {m.name | m ∈ modules, s.name ∈ m.env_requires}`
- CLI: `--secret-defs`, `--modules-dir`, `--output`

#### Step 1.4: `core/internal/scripts/generate_platform_env.py` (~200 LOC)
- `load_infra(path)` → `dict`
- `scan_compose_ports(modules_dir)` → порты из `docker-compose.base.yml`
- `scan_test_ports(modules_dir)` → порты из `docker-compose.test.yml`
- `load_ci_defaults(secret_defs_path)` → `dict`
- `discover_profiles(modules_dir)` → `list[str]`
- `generate(infra, ports, test_ports, ci_defaults, profiles)` → YAML output (networks/volumes/proxy/provides копируются из infra, всё остальное генерируется)

#### Step 1.5: Генерация `tests/_conftest/smoke_env_generated.py`
Генератор создаёт Python-файл с одним словарём:

```python
# AUTO-GENERATED by generate_platform_env.py. DO NOT EDIT.
SMOKE_ENV_GENERATED: dict[str, str] = {
    "CLICKHOUSE_PASSWORD": "test-clickhouse-pwd-not-for-prod",
    "POSTGRES_PASSWORD": "test-pg-pwd",
    ...
}
```

Источник: `secret-definitions.yaml#ci_default`. Ключ = `name`, значение = `ci_default`.

#### Step 1.6: Генерация `tests/helpers/env_defaults_generated.py`
Генератор создаёт Python-файл с константами:

```python
# AUTO-GENERATED by generate_platform_env.py. DO NOT EDIT.
_CLICKHOUSE_PASSWORD = "test-clickhouse-pwd-not-for-prod"
_POSTGRES_PASSWORD = "test-pg-pwd"
...
```

#### Step 1.7: Адаптация потребителей

**`tests/_conftest/smoke.py#SMOKE_ENV`:**
```python
from _conftest.smoke_env_generated import SMOKE_ENV_GENERATED

_STATIC_SMOKE_ENV: dict[str, str] = {
    "PLATFORM_DOMAIN": "test.local",
    "COMPOSE_PROJECT_NAME": "ai-platform-test",
    "PLATFORM_DIR": "/tmp/ai-platform-test",
    "POSTGRES_USER": "postgres",
    "POSTGRES_DB": "platform",
    "HERMES_DASHBOARD_USERNAME": "admin",
    "GF_SECURITY_ADMIN_USER": "admin",
    "S3_BUCKET": "test-bucket",
    "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
    "PROMETHEUS_TARGETS_DIR": "/tmp/prometheus-targets",
    "PROMETHEUS_RULES_DIR": "/tmp/prometheus-rules",
    "NGINX_CONF_DIR": "./dev-config",
    "NGINX_CERT_DIR": "/etc/nginx/dev-certs",
    "NODE_NAME": "test-node",
    "NODE_CONFIGS_DIR": "/tmp/test-node-configs",
    # test port overrides
    "LITELLM_TEST_PORT": "14000",
    "HERMES_DASHBOARD_TEST_PORT": "19119",
    "HERMES_DESKTOP_TEST_PORT": "18642",
    "LANGFUSE_TEST_PORT": "13000",
    "PROMETHEUS_TEST_PORT": "19090",
    "GRAFANA_TEST_PORT": "13030",
}

SMOKE_ENV: dict[str, str] = {**_STATIC_SMOKE_ENV, **SMOKE_ENV_GENERATED}
```

**`tests/helpers/__init__.py`:**
```python
from helpers.env_defaults_generated import (
    _CLICKHOUSE_PASSWORD,
    _POSTGRES_PASSWORD,
    ...
)
```

Хардкодженные значения удалить.

#### Step 1.8: Makefile integration
```makefile
generate-manifests:
	@python3 core/internal/scripts/generate_secrets_manifest.py --secret-defs core/secret-definitions.yaml --modules-dir core/modules --output core/secrets-manifest.yaml
	@python3 core/internal/scripts/generate_platform_env.py --infra core/platform-infra.yaml --modules-dir core/modules --secret-defs core/secret-definitions.yaml --output platform-env.yaml --smoke-env-output tests/_conftest/smoke_env_generated.py --helpers-output tests/helpers/env_defaults_generated.py

check-manifests:
	@git diff --exit-code -- core/secrets-manifest.yaml platform-env.yaml tests/_conftest/smoke_env_generated.py tests/helpers/env_defaults_generated.py || (echo "Generated files out of date. Run: make generate-manifests" && exit 1)
```

---

### Wave 2: Makefile → entrypoint-manifest.yaml + pytest → gates[] (D1, D3)

#### Step 2.1: `core/internal/scripts/generate_entrypoint_manifest.py` (~150 LOC)
- `extract_phony_targets(makefile_dir, gmake_path)` → `list[str]` через `gmake -np --dry-run 2>/dev/null | grep '^.PHONY:'` (использовать `gmake`, не системный `make`)
- `load_existing_manifest(path)` → `dict`
- `collect_gate_tests(tests_dir)` → `list[GateDef]` через `pytest --collect-only --marker=gate -q 2>/dev/null | grep '::'`
- `merge(allowed_verbs, gates, existing)` → YAML output
  - `allowed_verbs` заменяются полностью из extracted targets (минус system_exceptions: help, venv, pre-commit-*)
  - `gates[]` заменяются полностью из collected tests
  - Все остальные секции (bootstrap, deploy, build, validate, test, scaffold, secrets, lifecycle, provision, dev, module_hooks, lib, module_lifecycle, name_linter, forbidden_*) сохраняются из existing manifest без изменений
- CLI: `--makefile-dir`, `--gmake-path`, `--existing-manifest`, `--tests-dir`, `--output`

#### Step 2.2: Интегрировать в `make generate-manifests`
Вызывается после генераторов Wave 1.

---

### Wave 3: entrypoint-manifest.yaml → AGENTS.md + CI gate + test adaptation (D2, D10)

#### Step 3.1: `core/internal/scripts/generate_agents_md.py` (~100 LOC)
- `generate_canon_table(manifest)` → Markdown table rows из секций `deploy:`, `bootstrap:`, `build:`, `validate:`, `test:`, `scaffold:`, `secrets:`, `lifecycle:`, `provision:`, `dev:`
- `generate_forbidden_lists(manifest)` → Markdown lists из `forbidden_*` секций
- `inject_into_md(md_path, marker_name, new_content)` → инъекция между `<!-- GENERATED:START:<marker> -->` и `<!-- GENERATED:END:<marker> -->`

**Контракт полей:** entrypoint-manifest.yaml дополняется двумя ручными полями для каждого target:
```yaml
- make_target: deploy
  mechanism: git-push
  delegates_to: ...
  signature: "make deploy PROJECT=<dir>"   # ← новое ручное поле
  operation_ru: "Деплой проекта"            # ← новое ручное поле
  description: "Deploy a project via git push → CI → SSH forced-command on VPS"
```

Добавляются **однократно** при создании DevPlan (Wave 3) для всех существующих 40+ таргетов. При добавлении нового таргета разработчик заполняет их так же, как `delegates_to`.

#### Step 3.2: Рефакторинг `test_gate_manifest_integrity.py` (~1085 → ~500 LOC)
**Удалить:**
- Проверки `allowed_verbs` ↔ Makefile `.PHONY` (covered `make check-manifests`)
- Проверки `gates[]` ↔ файловая система (covered `make check-manifests`)

**Сохранить:**
- Direction A: `delegates_to` paths exist on disk
- Direction A: `forbidden_scripts` не найдены в core/
- Direction A: `forbidden_directories` не существуют на диске
- Direction B: AGENTS.md таблица ↔ manifest (структурная проверка, не freshness)
- Naming: module targets используют канонические имена
- Module lifecycle: targets зарегистрированы в manifest
- Entrypoint scripts упомянуты в manifest

#### Step 3.3: Новый gate `test_gate_manifests_up_to_date.py` (~50 LOC)
```python
@pytest.mark.gate
def test_manifests_up_to_date():
    result = subprocess.run(["make", "check-manifests"], capture_output=True, text=True)
    assert result.returncode == 0, f"Generated manifests are out of date:\n{result.stdout}\n\nRun: make generate-manifests"
```

#### Step 3.4: Удалить `test_gate_secrets_manifest.py` (381 LOC)
Заменён freshness gate. Проверки hardcoded-credentials и `secrets.XXX` в workflows остаются в `test_gate_ci_env_vars.py`.

#### Step 3.5: Pre-commit hook
```yaml
- repo: local
  hooks:
    - id: check-manifests
      name: check generated manifests up to date
      entry: make check-manifests
      language: system
      pass_filenames: false
```

#### Step 3.6: AGENTS.md (root) — новый инвариант
```markdown
11. Manifest Generation Contract — authoritative sources (module.yaml, secret-definitions.yaml, platform-infra.yaml, Makefile .PHONY, @pytest.mark.gate) порождают generated files (secrets-manifest.yaml, platform-env.yaml, smoke_env_generated.py, env_defaults_generated.py, entrypoint-manifest.yaml#allowed_verbs/gates, core/AGENTS.md generated-секции). Generated files коммитятся, но НЕ редактируются вручную. CI gate `make check-manifests` блокирует divergence.
```

---

## 3. Проверка: добавление нового модуля (до/после)

**До:**
```
1. module.yaml: env_requires += NEW_SECRET           ← 1 правка
2. docker-compose.base.yml: ports += 9090:9090       ← 1 правка
3. secrets-manifest.yaml: consumers += new-module    ← РУЧНАЯ
4. platform-env.yaml: profiles += new-module         ← РУЧНАЯ
5. platform-env.yaml: port_mappings += NEW_PORT:9090 ← РУЧНАЯ
6. platform-env.yaml: env_defaults += NEW_SECRET     ← РУЧНАЯ
7. tests/_conftest/smoke.py: SMOKE_ENV += NEW_SECRET ← РУЧНАЯ (часто забывают)
8. tests/helpers/__init__.py: _NEW_SECRET = "..."     ← РУЧНАЯ
9. .env.example: документировать                     ← РУЧНАЯ
```
**9 правок, 6 ручных синхронизаций.**

**После:**
```
1. module.yaml: env_requires += NEW_SECRET           ← 1 правка
2. docker-compose.base.yml: ports += 9090:9090       ← 1 правка
3. secret-definitions.yaml: +entry для NEW_SECRET    ← 1 правка (если секрет новый)
4. make generate-manifests                           ← АВТОМАТИЧЕСКИ обновляет 6 файлов
5. git add -A && git push → CI green                 ← check-manifests проходит
```
**3 правки, 0 ручных синхронизаций.**

---

## 4. Файловый манифест (после Implementation)

```
core/
├── secret-definitions.yaml              ← NEW authoritative
├── platform-infra.yaml                  ← NEW authoritative
├── secrets-manifest.yaml                ← (modified) consumers → AUTO-GENERATED
├── entrypoint-manifest.yaml             ← (modified) allowed_verbs, gates[] → AUTO-GENERATED
├── AGENTS.md                            ← (modified) generated-секции
├── internal/scripts/
│   ├── generate_secrets_manifest.py     ← NEW (~150 LOC)
│   ├── generate_platform_env.py         ← NEW (~200 LOC)
│   ├── generate_entrypoint_manifest.py  ← NEW (~150 LOC)
│   └── generate_agents_md.py           ← NEW (~100 LOC)

platform-env.yaml                        ← (modified) generated-секции

tests/
├── _conftest/
│   ├── smoke_env_generated.py          ← NEW AUTO-GENERATED
│   └── smoke.py                        ← (modified) SMOKE_ENV = static + generated
├── helpers/
│   ├── env_defaults_generated.py       ← NEW AUTO-GENERATED
│   └── __init__.py                     ← (modified) импорт из generated вместо хардкода
├── gates/
│   ├── test_gate_manifests_up_to_date.py     ← NEW (~50 LOC)
│   ├── test_gate_manifest_integrity.py       ← (modified) структурные проверки ~500 LOC
│   └── test_gate_secrets_manifest.py         ← DELETED (381 LOC)
└── unit/
    ├── test_generate_secrets_manifest.py     ← NEW
    ├── test_generate_platform_env.py         ← NEW
    ├── test_generate_entrypoint_manifest.py  ← NEW
    └── test_generate_agents_md.py            ← NEW

AGENTS.md (root)                         ← (modified) новый инвариант
Makefile                                 ← (modified) +generate-manifests, +check-manifests
.pre-commit-config.yaml                  ← (modified) +hook check-manifests
```

**Нетто LOC:** +600 (генераторы + тесты + generated) − 381 (старый gate) − ~100 (удалённый хардкод из helpers/smoke). **~+120 LOC при устранении 6 ручных синхронизаций на каждую новую переменную.**

---

## 5. Unit-тесты генераторов

Каждый генератор получает ≥1 unit-тест в `tests/unit/`:

```python
# tests/unit/test_generate_secrets_manifest.py
def test_compute_consumers():
    modules = [
        ModuleDef(name="clickhouse", env_requires=["CLICKHOUSE_PASSWORD", "POSTGRES_PASSWORD"]),
        ModuleDef(name="langfuse", env_requires=["CLICKHOUSE_PASSWORD"]),
    ]
    assert compute_consumers("CLICKHOUSE_PASSWORD", modules) == ["clickhouse", "langfuse"]
    assert compute_consumers("POSTGRES_PASSWORD", modules) == ["clickhouse"]

def test_empty_env_requires():
    modules = [ModuleDef(name="nginx", env_requires=[])]
    assert compute_consumers("POSTGRES_PASSWORD", modules) == []
```

---

## 6. CI gate

```yaml
# .github/workflows/platform-test.yml — добавить шаг после test:
- name: Check generated manifests up to date
  run: make check-manifests
```

---

## 7. Что НЕ входит в scope

1. Генерация template-manifest.yaml, module.yaml, docker-compose файлов, node.yaml, ai-platform.yaml — они уже authoritative
2. Генерация `.bundled_manifest` — ортогональный хеш-манифест hermes-agent skills
3. Удаление `entrypoint-manifest.yaml` — нарушит инвариант 5
4. Автоматическое определение `mechanism` и `delegates_to` из Makefile recipes — ненадёжный парсинг, остаются ручными
5. Генерация `module_lifecycle` и `system_module_lifecycle` секций entrypoint-manifest.yaml — targets из module.mk/Makefile.common, низкая частота изменений

---

## 8. Risk Assessment

| Риск | Mitigation |
|------|-----------|
| `gmake -np` на macOS без GNU Make | Использовать явный путь `/opt/homebrew/bin/gmake`. В CI — GNU Make всегда доступен |
| `pytest --collect-only` фейлится без Docker | Graceful skip с warning локально; CI гарантирует наличие |
| Генератор перезаписывает ручную правку в generated секции | `make check-manifests` блокирует merge. `# AUTO-GENERATED` маркеры. Pre-commit hook |
| `provides.dsn_template` не выводится из compose | Остаётся в ручном `platform-infra.yaml` |
| SMOKE_ENV_GENERATED diverges с статической частью SMOKE_ENV | Компиляция Python `{**_STATIC, **GENERATED}` — тесты падают при несовпадении ключей |

---

## 9. Decision Log

| Решение | Обоснование |
|---------|-------------|
| SMOKE_ENV = static + generated, не полностью generated | PLATFORM_DOMAIN, COMPOSE_PROJECT_NAME, test port overrides — не секреты, не извлекаются из secret-definitions/compose |
| `test_gate_manifest_integrity.py` рефакторен, не удалён | Структурные проверки (delegates_to paths, forbidden_*) — design decisions, не выводимы из данных. Их валидация не заменяется freshness check |
| `secret-definitions.yaml` и `platform-infra.yaml` — новые файлы | Разделение declaration/projection чище архитектурно. In-place update создал бы файлы смешанной ответственности |
| entrypoint-manifest.yaml дополнен полями `signature` и `operation_ru` | Без них AGENTS.md таблица не может быть сгенерирована (нужна сигнатура и русское имя операции). Добавляются однократно |
| `gmake` вместо `make` для парсинга `.PHONY` | macOS BSD make не поддерживает `-np`. Fallback grep не работает для split-Makefile |

$END_DEVPLAN
