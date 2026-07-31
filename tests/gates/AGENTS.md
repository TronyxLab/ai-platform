<!-- GREP_SUMMARY: AGENTS.md, gates, taxonomy, registration-protocol, marker-contract, manifest -->

# GREP_SUMMARY: AGENTS.md, gates, taxonomy, registration-protocol, marker-contract, manifest
# STRUCTURE: ┌gate taxonomy┐ → ◇ registration protocol (trinity: file + marker + manifest) → ⊕ add/remove procedure → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Gate taxonomy and registration protocol for the CI gate suite
## @scope    All pytest gate tests under tests/gates/ — registration, marker, and manifest contracts
## @invariants
##   1. Каждый gate-файл ДОЛЖЕН быть зарегистрирован в core/entrypoint-manifest.yaml (секция gates)
##   2. Каждый gate-тест ДОЛЖЕН иметь декоратор @pytest.mark.gate
##   3. Каждый gate-файл ДОЛЖЕН находиться в tests/gates/ (не в tests/ корне)
##   4. Триединое соответствие: файл в tests/gates/ + маркер + manifest-запись — пропуск любого = gate не запускается
##   5. Удалённый gate: удалить файл + manifest-запись + очистить __pycache__
## @rationale Единый протокол регистрации предотвращает дрейф gate-тестов.
##            Пропуск любого из трёх шагов (файл, маркер, manifest) приводит к тому,
##            что gate не запускается в make gate — и дрейф остаётся незамеченным.
# endregion MODULE_CONTRACT

# AGENTS.md — tests/gates/

---

## Gate taxonomy

Gate-тесты делятся на категории по предмету проверки:

| Категория | Описание | Примеры |
|-----------|----------|---------|
| **contract** | Контрактная валидация модулей, entrypoints, healthcheck | test_gate_module_yaml_contract, test_gate_healthcheck_contract |
| **consistency** | Согласованность cross-cutting конфигураций | test_gate_env_example_sync, test_gate_container_name_consistency |
| **security** | Безопасность: секреты, пароли, network policies | test_gate_security_config, test_gate_ci_env_vars |
| **drift** | Обнаружение дрейфа артефактов | test_gate_manifest_integrity, test_gate_workflow_consistency |
| **coverage** | Покрытие: все скрипты/таргеты зарегистрированы | test_gate_no_unregistered_entrypoint |
| **enforcement** | Принудительные проверки (proxyless, PostgreSQL-only) | test_gate_litellm_pg_enforcement, test_gate_env_example_sync (NO_PROXY) |

---

## Registration protocol

Каждый gate-тест следует трёхшаговому протоколу:

### 1. Добавить файл в `tests/gates/`
- Имя файла: `test_gate_<category>.py`
- Файл ДОЛЖЕН быть в `tests/gates/` — не в `tests/` корне
- Внутри файла: обычный pytest с `@pytest.mark.gate`

### 2. Добавить `@pytest.mark.gate`
- Каждый тест (или класс) ДОЛЖЕН иметь декоратор `@pytest.mark.gate`
- Без маркера `make gate -m gate` не запустит тест
- Исключения: только `skip_enforcement` и `e2e` (env-dependent, не gate-тесты)

### 3. Зарегистрировать в `core/entrypoint-manifest.yaml`
- Добавить запись в секцию `gates`: id, description, test_file
- `id` — краткий kebab-case идентификатор
- `test_file` — имя файла в `tests/gates/` (без пути)
- После регистрации в manifest, CI gate `test_all_shebang_files_in_manifest` валидирует соответствие

### Удаление gate
1. Удалить файл из `tests/gates/`
2. Удалить запись из `core/entrypoint-manifest.yaml` секции `gates`
3. Очистить `tests/gates/__pycache__/` от остатков удалённого файла

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`core/entrypoint-manifest.yaml`](../../core/entrypoint-manifest.yaml) | YAML-реестр gates (секция `gates:`) |
| [`../../core/AGENTS.md`](../../core/AGENTS.md) | Канонические операции, структура слоёв |
| `../../AGENTS.md` (root) | Архитектурные инварианты платформы |
