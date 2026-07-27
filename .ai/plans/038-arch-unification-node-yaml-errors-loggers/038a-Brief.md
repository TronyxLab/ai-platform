$START_BRIEF

# Brief 038a — Wave 1: Unified NodeYaml Facade + Typed Exceptions

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Декомпозиция DevPlan 038 Wave 1 — создать единый фасад `NodeYaml` для чтения node.yaml, иерархию типизированных исключений и мигрировать всех Python/shell consumers на новый API |
| **DESCRIPTION** | Wave 1 из 5-волнового DevPlan 038. Объём: ~38 файлов. Создаётся класс `NodeYaml` с lazy-load + cache, 11 методами, CLI-интерфейсом и typed exceptions (`exceptions.py`). Миграция: 26 Python-файлов (замена прямых `yaml.safe_load`) + ~10 shell-файлов (inline python3 → CLI фасада) + `yaml_read.sh` (тела функций → CLI) + `NODE_YAML_PATH` env var cleanup |
| **RATIONALE** | Wave 1 — фундамент для остальных 4 волн DevPlan 038. Без единого фасада невозможно системно заменить типизированные исключения (W4) и убрать inline python3 (W5). Lazy-load + cache даёт 99% reduction в worst-case сценарии healthcheck (7ms → 0.5µs). Dotted-key API устраняет nested dict boilerplate (5 строк → 1 вызов) |
| **ACCEPTANCE_CRITERIA** | AC1: `grep 'yaml.safe_load' core/internal/` → только в `yaml_query.py:_load_yaml` и `NodeYaml.load`. AC2: `make gate MODE=fast` passes. AC3: Все существующие тесты проходят (no regression). AC4: CLI фасада выдаёт валидный JSON для `--get` и `--items`. AC5: 20+ unit-тестов для NodeYaml + exceptions, покрытие ≥90% |
| **IMPLEMENTS** | DevPlan 038 Wave 1 (T1.1–T1.9) — Unified `node_yaml.py` Facade |
| **IMPACTS** | `core/internal/shared/exceptions.py` (NEW, 5 классов), `core/internal/shared/node_yaml.py` (расширение: 67→~350 строк), `tests/unit/test_node_yaml_facade.py` (NEW, 20+ тестов), `tests/unit/test_exceptions.py` (NEW), 26 Python-файлов в `core/internal/`, ~10 shell-файлов в `core/lib/` + `core/internal/` |
| **REQUIRES** | DevPlan 038 (02-DevPlan.md) — архитектурный blueprint, DevPlan 070 (shared libs) — COMPLETED, DevPlan 079 (bootstrap restructuring) — COMPLETED, `core/internal/shared/node_yaml.py` (существующий, 67 строк), Python 3.10+, PyYAML, pytest |

---

## $DOCUMENT_PLAN

### 1. Problem Statement

В кодовой базе платформы существует 7 различных паттернов чтения `node.yaml`:

| # | Паттерн | Пример | Где встречается |
|---|---------|--------|-----------------|
| 1 | Прямой `yaml.safe_load(f)` + dict access | `data = yaml.safe_load(f); ctx = data.get("context", "")` | 26 Python-файлов |
| 2 | `extract_context_from_node_yaml()` | `ctx = extract_context_from_node_yaml(path, log_tag)` | state_machine, steps, context_deployer |
| 3 | `yaml_read_domain_config()` shell-функция | `eval "$(python3 - ... <<'PYEOF' ...)"` | yaml_read.sh → 3+ consumers |
| 4 | Inline `python3 -c "import yaml; ..."` | `host=$(python3 -c "import yaml; print(...)")` | node-resolver.sh, verify-domains.sh, remove-project.sh, adopt-project.sh |
| 5 | `extract_yaml_field()` | `owner_key = extract_yaml_field(path, "node", "owner_key")` | converge/reconciler |
| 6 | `yaml_get()` / `yaml_get_field` lib-функции | `domain = yaml_get_field("$NODE_YAML" domain.platform)` | 5+ shell-скриптов |
| 7 | `NODE_YAML_PATH` env var + прямой `yaml.safe_load` | `path = os.environ.get("NODE_YAML_PATH"); yaml.safe_load(f)` | healthcheck, status-page, cert_collector |

**Последствия фрагментации:**
- Невозможно изменить формат node.yaml (7 точек придётся править)
- Нет кэширования — каждый consumer читает и парсит файл заново
- Inline python3 в shell нарушает языковую политику (Python-first)
- `except Exception: return []` в части consumers маскирует ошибки парсинга
- Dotted-key traversal дублируется в каждом consumer (3-5 строк boilerplate)

### 2. Scope

#### Scope In (Wave 1)
- `core/internal/shared/exceptions.py` — иерархия из 5 классов: `PlatformError`, `ConfigNotFoundError`, `ConfigParseError`, `ConfigValidationError`, `PlatformFatalError`
- `core/internal/shared/node_yaml.py` — класс `NodeYaml` с 11 методами + CLI + lazy-load + cache
- `tests/unit/test_node_yaml_facade.py` — 20+ unit-тестов
- `tests/unit/test_exceptions.py` — тесты иерархии исключений
- Миграция 26 Python-файлов: замена прямых `yaml.safe_load` → `NodeYaml(path)`
- Миграция `yaml_read.sh`: тела функций → CLI фасада (backward-compat оболочка)
- Миграция ~8 shell-файлов: inline `python3 -c "import yaml"` → CLI фасада
- Миграция ~5 файлов: `NODE_YAML_PATH` env var → `NodeYaml(path)`
- `make gate MODE=fast` passes после миграции

#### Scope Out (остальные волны DevPlan 038)
- W2: `sys.exit()` removal в `project_registry.py` → return codes
- W3: Hardcoded `getLogger("literal")` → `__name__`
- W4: Сужение `except Exception` до typed exceptions
- W5: Оставшиеся inline python3 (не-YAML: `import json`, platform detection)
- jsonschema валидация node.yaml (существует в `validate.sh`)
- Atomic write / partial update node.yaml
- Streaming/lazy section load для YAML >1MB

### 3. Stakeholders

| Стейкхолдер | Интересы |
|-------------|----------|
| Разработчики платформы | Единый Python API для YAML — `NodeYaml(path).get("key")` вместо 5 строк boilerplate |
| Shell-скрипты (CI) | CLI фасада заменяет inline python3 — улучшает отладку, даёт LDD-телеметрию |
| CI/CD система | Консистентные exit codes (2=not found, 3=parse error, 4=validation error) |
| Будущие модули | Typed contracts для YAML-чтения; новый код автоматически использует фасад |
| QA | Тестируемый фасад с покрытием ≥90%; test honesty rules (R1-R5) соблюдены |

### 4. Success Criteria

| AC | Критерий | Проверка |
|----|----------|----------|
| AC1 | Все `yaml.safe_load` для node.yaml проходят через `NodeYaml` | `grep 'yaml.safe_load' core/internal/` → только `yaml_query.py:_load_yaml` и `node_yaml.py:NodeYaml.load` |
| AC2 | `make gate MODE=fast` passes | CI green, все gate-проверки проходят |
| AC3 | Все существующие тесты проходят (no regression) | `python -m pytest tests/ -s -v` → 100% pass |
| AC4 | CLI выдаёт валидный JSON для `--get` и `--items` | `python3 -m core.internal.shared.node_yaml --file test.yaml --get projects --items | python3 -m json.tool` |
| AC5 | 20+ unit-тестов для NodeYaml + exceptions | `python -m pytest tests/unit/test_node_yaml_facade.py tests/unit/test_exceptions.py -v` → 20+ pass |
| AC6 | `yaml_read.sh` backward-compat: старые вызовы продолжают работать | `yaml_get_field`, `yaml_get_list`, `yaml_read_domain_config` возвращают те же значения |
| AC7 | 0 активных inline `python3 -c "import yaml"` в shell | `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 (кроме комментариев) |

### 5. Risks

| # | Риск | Severity | Mitigation |
|---|------|----------|------------|
| R1 | Breaking change: 26 Python + 10 shell consumers — широкий радиус поражения | HIGH | Backward-compat алиасы: `extract_context_from_node_yaml()` остаётся как `NodeYaml.get_context()`. `yaml_read.sh` функции — обёртки над CLI. `make gate MODE=fast` как regression gate |
| R2 | Shell exit code change — CLI фасада может выдать другой exit code | MEDIUM | CLI использует `exit_code` из `PlatformError` subclass. Маппинг: 2=not found, 3=parse error, 4=validation error. Совпадает с существующими кодами `yaml_query.py` |
| R3 | Неполная миграция — часть consumers остаётся на старом API | MEDIUM | `grep 'yaml.safe_load' core/internal/` после миграции → только 2 ожидаемых вхождения. `grep 'import yaml'` в shell → 0 |
| R4 | `NODE_YAML_PATH` env var очистка — 5 файлов используют переменную окружения для определения пути | LOW | Замена на `NodeYaml(path)` где path вычисляется из конфигурации, а не из env var. Тесты на `tmp_path` без моков `os.environ` |
| R5 | Конфликт с существующим `shared/node_yaml.py` (уже содержит `extract_context_from_node_yaml`) | LOW | Расширение существующего файла. `extract_context_from_node_yaml()` становится тонкой обёрткой над `NodeYaml(path).get_context()` с DeprecationWarning |

### 6. Dependencies

| Зависимость | Статус | Примечание |
|-------------|--------|------------|
| DevPlan 038 (02-DevPlan.md) | COMPLETED | Архитектурный blueprint — описан полный API, superposition, design decisions |
| DevPlan 070 (shared libs) | COMPLETED | `shared/node_yaml.py` создан (67 строк, `extract_context_from_node_yaml`) |
| DevPlan 079 (bootstrap restructuring) | COMPLETED | Файлы в `lifecycle/`, `deploy/`, `converge/` — актуальные пути |
| Python 3.10+ | AVAILABLE | Требуется для type hints (`str | None`) |
| PyYAML | AVAILABLE | Уже используется во всех 26 файлах |
| pytest | AVAILABLE | Тестовая инфраструктура существует |

### 7. File Manifest (Wave 1 only)

#### Новые файлы
| Файл | Назначение | Строк (оценка) |
|------|-----------|----------------|
| `core/internal/shared/exceptions.py` | Иерархия из 5 классов исключений с `exit_code` | ~50 |
| `tests/unit/test_node_yaml_facade.py` | Unit-тесты NodeYaml (20+) | ~350 |
| `tests/unit/test_exceptions.py` | Unit-тесты иерархии исключений | ~80 |

#### Модифицируемые Python-файлы (26)
| # | Файл | Изменение |
|---|------|-----------|
| 1 | `core/internal/shared/node_yaml.py` | Расширение: класс `NodeYaml` + CLI. 67→~350 строк |
| 2 | `core/internal/shared/project_registry.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 3 | `core/internal/bootstrap/yaml_helpers.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 4 | `core/internal/bootstrap/preflight.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 5 | `core/internal/bootstrap/lifecycle/state_machine.py` | `yaml.safe_load` → `NodeYaml(path).load()` (3 точки) |
| 6 | `core/internal/bootstrap/lifecycle/steps.py` | `yaml.safe_load` → `NodeYaml(path).load()` (2 точки) |
| 7 | `core/internal/bootstrap/deploy/context_deployer.py` | `yaml.safe_load` → `NodeYaml(path).load()` (2 точки) |
| 8 | `core/internal/bootstrap/deploy/context_overlay.py` | `yaml.safe_load` → `NodeYaml(path).load()` (2 точки) |
| 9 | `core/internal/bootstrap/s3_ssl_cache.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 10 | `core/internal/bootstrap/converge/reconciler.py` | `yaml.safe_load` → `NodeYaml(path).load()` (3 точки) |
| 11 | `core/internal/bootstrap/deploy/secrets_validator.py` | `yaml.safe_load` → `NodeYaml(path).load()` (8 точек) |
| 12 | `core/internal/bootstrap/deploy/compose_preflight.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 13 | `core/internal/bootstrap/deploy/spool_validator.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 14 | `core/internal/bootstrap/lifecycle/secrets_manager.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 15 | `core/internal/reconciler_projects.py` | `yaml.safe_load` → `NodeYaml(path).load()` (2 точки) |
| 16 | `core/internal/provisioner.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 17 | `core/internal/monitoring_config_renderer.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 18 | `core/internal/scaffold/gen_env_platform.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 19 | `core/internal/scaffold/vhost_yaml_reader.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 20 | `core/internal/scaffold/context_registry.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 21 | `core/internal/healthcheck/platform_export_metrics.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml(path)` |
| 22 | `core/internal/healthcheck/metrics/cert_collector.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml(path)` |
| 23 | `core/internal/healthcheck/metrics/project_collector.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 24 | `core/modules/status-page/app.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml(path)` |
| 25 | `core/internal/llm/policy_schema.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| 26 | `core/internal/scripts/yaml_query.py` | `yaml.safe_load` → остаётся как `_load_yaml()` (low-level, используется фасадом) |

#### Модифицируемые shell-файлы (~10)

##### Shell libs (2)
| # | Файл | Изменение |
|---|------|-----------|
| 27 | `core/lib/yaml_read.sh` | Тела функций → CLI фасада (backward-compat обёртки) |
| 28 | `core/lib/node-resolver.sh` | Inline `python3 -c "import yaml"` → CLI фасада |

##### Shell entrypoints/scaffold (5)
| # | Файл | Изменение |
|---|------|-----------|
| 29 | `core/internal/scaffold/remove-project.sh` | Inline `python3 -c "import yaml"` → CLI фасада |
| 30 | `core/internal/scaffold/adopt-project.sh` | Inline `python3 -c` blocks → CLI фасада |
| 31 | `core/internal/verify/verify-domains.sh` | Inline `python3 -c "import yaml"` → CLI фасада |
| 32 | `core/internal/catalog/generate-catalog.sh` | Inline `python3 - ... <<'PYEOF'` → CLI фасада |
| 33 | `core/modules/postgres/hooks/on-project-deploy.sh` | Inline `python3 -c` → CLI фасада |

##### NODE_YAML_PATH cleanup (3)
| # | Файл | Изменение |
|---|------|-----------|
| 34 | `core/internal/bootstrap/converge.sh` | `NODE_YAML_PATH` → `NodeYaml(path)` в Python-блоках |
| 35 | `core/internal/scaffold/add-vhost.sh` | `export NODE_YAML_PATH` → передавать путь как аргумент CLI |
| 36 | `core/internal/validate/validate.sh` | Inline `python3 -c` YAML-проверка → CLI фасада (jsonschema часть ОСТАВИТЬ) |

**Всего: 3 новых + 36 модифицируемых = ~39 файлов**

$END_BRIEF
