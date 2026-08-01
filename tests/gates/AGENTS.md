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
| **volumes_sot** | Volumes: root compose — единственный SoT (DevPlan 116 B3 T4, U-49) | test_gate_volumes_sot.py |
| **image_tag_form** | ghcr tag-политика: версионный тег / digest-pin, голый :latest — RED (DevPlan 116 B3 T7, U-60) | test_gate_image_tag_form.py |

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
- `make generate-entrypoint-manifest` пересобирает gates[] автоматически из pytest markers (DevPlan 090 G3 cycle break)

### Удаление gate
1. Удалить файл из `tests/gates/`
2. Удалить запись из `core/entrypoint-manifest.yaml` секции `gates`
3. Очистить `tests/gates/__pycache__/` от остатков удалённого файла

---

## Инвентарь волны (DevPlan 116 B3)

| Гейт | Файл | Предмет |
|------|------|---------|
| metrics cron contract (код-присутствие) | `test_gate_status_page.py` (TestGateStatusPageCrontabContract — расширен) | φ3 вызывает install_cron_metrics; CRON_METRICS_LINE = flock + timeout 50 + script |
| volumes SoT | `test_gate_volumes_sot.py` (NEW) | root compose — 12 volumes; модульные top-level volumes = ∅; CONTEXT_IMAGE: "" = 0 |
| image tag form | `test_gate_image_tag_form.py` (NEW) | ghcr refs = версионный тег/digest-pin; голый :latest RED; allowlist dev/test |
| prometheus single source | `test_gate_env_chain.py` (расширен: negative prometheus.yml-дубль запрещён) | .tmpl — единственный источник (U-48) |
| P20 prometheus targets | `test_p20_container_coupling.py` (PROMETHEUS_YML → .tmpl) | targets резолвятся из .tmpl |
| R1 no-pass-tests (B10 T1, U-69) | `test_gate_r1_no_pass_tests.py` (NEW) | Test Honesty R1: ast-скан tests/**/*.py — константный assert / bare-pass except / файл без ассертов = RED. Allowlist пуст. repair_class L2 (ручная правка теста) |

---

## Preflight workflow (agent-oriented gate accelerator)

### Проблема

`make gate MODE=fast` — последовательный конвейер из 8 шагов, останавливается на первом фейле. Агент видит только ошибки текущего шага → фиксит → перезапускает → видит ошибки следующего шага → фиксит → ... Цикл из 4-5 проходов gate вместо одного.

**Корневая причина:** детекция и верификация не разделены. Gate делает и то и другое последовательно.

### Решение: `make preflight`

```bash
make preflight
```

**Три фазы:**
1. `make fix-gate` — авто-фикс (exec bits, ruff, manifests) — ~3s
2. `pre-commit run --all-files` — авто-фикс гигиены + верификация всех хуков — ~10-20s
3. **8 проверок параллельно** (ThreadPoolExecutor, 6 workers): validate, check-dead-code, check-exception-patterns, doxygen-check, gates-static, contract, static_audit, predeploy — ~20-40s

**Результат:** все ошибки собраны в ОДНОМ отчёте. Агент фиксит всё за один проход → `make gate MODE=fast` верифицирует один раз.

### Сравнение циклов

| Шаг | Старый цикл (последовательный gate) | Новый цикл (preflight) |
|-----|-------------------------------------|------------------------|
| 1 | `make gate` → fail на pre-commit | `make preflight` → собраны ВСЕ ошибки |
| 2 | Фикс pre-commit → `make gate` → fail на static_audit | Агент читает ОДИН отчёт, фиксит ВСЁ |
| 3 | Фикс static → `make gate` → fail на format | `make gate MODE=fast` → зелёный |
| 4 | Фикс format → `make gate` → fail на другое |
| ... | ... (4-5 итераций) |
| N | `make gate` → зелёный |

**Экономия:** ~60-80% времени агента на верификации (4-5 проходов → 1 preflight + 1 gate).

### Использование

```bash
# Стандартный запуск (авто-фикс + все проверки параллельно)
make preflight

# Только проверки без авто-фикса (когда файлы уже чистые)
make preflight SKIP_FIX=1

# JSON-вывод для машинной обработки
make preflight JSON=1

# Подробный вывод (полный stdout/stderr для упавших проверок)
make preflight VERBOSE=1

# Настроить число параллельных воркеров
make preflight WORKERS=8
```

### Инварианты

- **Preflight НЕ заменяет gate.** Gate остаётся канонической верификацией. Preflight — диагностический акселератор.
- **Preflight НЕ коммитит изменения.** Только авто-фиксы в worktree (так же как `make fix-gate`).
- **Exit code 0** = все проверки прошли, gate должен быть зелёным.
- **Exit code 1** = есть ошибки, нужно фиксить. После фикса: `make gate MODE=fast`.
- **Параллельные проверки read-only** — не мутируют файлы, безопасны для concurrent execution.

### Рекомендуемый agent workflow

```
1. make preflight                    # ОДИН запуск — все ошибки собраны
2. Прочитать отчёт — все FAIL-секции
3. Исправить ВСЕ ошибки за один проход
4. make gate MODE=fast               # ОДНА верификация
```

Никаких `fix → gate → fix → gate → ...` циклов.

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`core/entrypoint-manifest.yaml`](../../core/entrypoint-manifest.yaml) | YAML-реестр gates (секция `gates:`) |
| [`../../core/AGENTS.md`](../../core/AGENTS.md) | Канонические операции, структура слоёв |
| [`../../core/internal/preflight.py`](../../core/internal/preflight.py) | Preflight-модуль — параллельный сбор ошибок |
| [`../../makefiles/repair.mk`](../../makefiles/repair.mk) | `make preflight` target + repair-таргеты |
| `../../AGENTS.md` (root) | Архитектурные инварианты платформы |
