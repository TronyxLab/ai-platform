<!-- GREP_SUMMARY: AGENTS.md, gates, taxonomy, registration-protocol, marker-contract, manifest, check-workflow, check-suite -->

# GREP_SUMMARY: AGENTS.md, gates, taxonomy, registration-protocol, marker-contract, manifest, check-workflow, check-suite
# STRUCTURE: ┌gate taxonomy┐ → ◇ registration protocol (trinity: file + marker + manifest) → ◇ check workflow (DevPlan 120) → ⊕ add/remove procedure → ⎋ cross-refs
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
| R1 no-pass-tests (B10 T1, U-69; F1 per-function 118) | `test_gate_r1_no_pass_tests.py` (NEW) | Test Honesty R1: ast-скан tests/**/*.py — константный assert / bare-pass except / тест-функция без fail-механизма = RED. **F1 (118): скан по-функции** — каждая `test_*` функция обязана иметь assert/raise/pytest.fail/raises/mock-assert в теле; файловый скан не заменяет функцию (pass-функции прятались за asserting-соседями). Exemptions через декораторы: `@pytest.fixture`, `@r1_delegates` (tests/_conftest/r1.py — документированная делегация fail-механизма helper/fixture-raise), pure-skip функции (R3-домен). Allowlist пуст. repair_class L2 (ручная правка теста) |

## Инвентарь волны (DevPlan 116 B11)

| Гейт | Файл | Предмет |
|------|------|---------|
| cross-layer dotted + python3 -m (B11 T1, U-09) | `tests/test_cross_layer_imports.py` (расширен) | dotted-импорты (core.internal.X) + `python3 -m core.internal.*` детектируются; строгий allowlist (9 записей — расширен до 9: 117 D19/D29/T52; 118 B8 добавил on_project_deploy); LINT-EXEMPT легитимен ТОЛЬКО с allowlist-записью; 2 негатив-теста R5 |
| audit-format R2 (B11 T2, U-10/D1) | `test_gate_audit_format.py` (NEW) | единый writer shared/audit_logger: 0 прямых f.write на audit-файлы вне shared; 0 free-text pipe; JSONL-валидация (json.loads построчно); негатив R5 |
| glossary G4 (B11 T3, U-45/D3) | check-manifests G4-root (makefiles/manifest.mk) | root AGENTS.md глоссарий из allowed_verbs (68 строк), GENERATED-маркеры, байт-сверка через --check |
| inventory rename (B11 T6, U-79) | `test_gate_test_inventory.py` (расширен) | rename-детекция (нормализованные file+func) → PASS+warning; удаление без пары → changelog RED; single-source регенерации (нет второго вызова sync_inventory) |

---

## Check workflow (agent-oriented gate accelerator, DevPlan 120)

### Проблема

`make gate MODE=fast` — последовательный конвейер, останавливается на первом фейле. Агент видит только ошибки текущего шага → фиксит → перезапускает → видит ошибки следующего шага → фиксит → ... Цикл из 4-5 проходов gate вместо одного.

**Корневая причина:** детекция и верификация не разделены. Gate делает и то и другое последовательно.

### Решение: `make check` (экс-preflight)

```bash
make check
```

**Три фазы (единый SoT-манифест `core/check-suite.yaml`, DevPlan 120):**
1. **Fix-фаза** — `make fix-gate` + tier=fix чеки манифеста (pre-commit) — ~25s
2. **Fingerprint-кэш** — повторный прогон на неизменённом дереве = replay <10s (CHECK_CACHE=0 отключает; кэш ТОЛЬКО у check, gate — без кэша)
3. **Проверки из манифеста**: static-чеки параллельно (validate, check-dead-code, check-exception-patterns, doxygen-check, check-manifests, ruff check .) + pytest-чеки последовательно с xdist (gates, gates-docker, contract, static_audit, predeploy) — ~90s на 12 ядрах (static_audit xdist: 254s → ~60s)

**Результат:** все ошибки собраны в ОДНОМ отчёте. Агент фиксит всё за один проход → `make gate MODE=fast` верифицирует один раз.

**Узкий таргет:** `make check-diff` — pre-commit --files + ruff по изменённым .py + pytest изменённых test-файлов (без кэша, без изменений → exit 0).

### Сравнение циклов

| Шаг | Старый цикл (последовательный gate) | Новый цикл (check) |
|-----|-------------------------------------|--------------------|
| 1 | `make gate` → fail на pre-commit | `make check` → собраны ВСЕ ошибки |
| 2 | Фикс pre-commit → `make gate` → fail на static_audit | Агент читает ОДИН отчёт, фиксит ВСЁ |
| 3 | Фикс static → `make gate` → fail на format | `make gate MODE=fast` → зелёный |
| 4 | Фикс format → `make gate` → fail на другое |
| ... | ... (4-5 итераций) |
| N | `make gate` → зелёный |

**Экономия:** ~60-80% времени агента на верификации (4-5 проходов → 1 check + 1 gate); повторный check на чистом дереве — <10s (fingerprint replay).

### Использование

```bash
# Стандартный запуск (автофикс + все проверки манифеста)
make check

# Только проверки без авто-фикса (когда файлы уже чистые)
make check SKIP_FIX=1

# JSON-вывод для машинной обработки
make check JSON=1

# Подробный вывод (полный stdout/stderr для упавших проверок)
make check VERBOSE=1

# Настроить число параллельных воркеров
make check WORKERS=8

# Без fingerprint-кэша (полный честный прогон)
make check CHECK_CACHE=0

# Узкая диагностика по изменённым файлам
make check-diff

# Deprecated-алиас (обратная совместимость; мигрируйте на check)
make preflight
```

### Инварианты

- **Check НЕ заменяет gate.** Gate остаётся канонической верификацией (арбитр). Check — диагностический акселератор. Оба executor'а читают ОДИН манифест `core/check-suite.yaml` — дрейф невозможен конструктивно (DevPlan 120 §3.2).
- **Check НЕ коммитит изменения.** Только авто-фиксы в worktree (так же как `make fix-gate`).
- **Fingerprint-кэш — только у check.** Gate/CI/pre-push — без кэша (канонический прогон всегда). Replay только при байт-идентичном дереве И зелёном последнем прогоне.
- **Exit code 0** = все проверки прошли, gate должен быть зелёным.
- **Exit code 1** = есть ошибки, нужно фиксить. После фикса: `make gate MODE=fast`.
- **Parallel-чеки read-only** — не мутируют файлы, безопасны для concurrent execution; pytest-чеки строго последовательно (1 pytest с -n auto за раз).

### Рекомендуемый agent workflow

```
1. make check                        # ОДИН запуск — все ошибки собраны (~90s)
2. Прочитать отчёт — все FAIL-секции
3. Исправить ВСЕ ошибки за один проход
4. make check                        # повторный — <10s если дерево не менялось, ~90s если фиксили
5. make gate MODE=fast               # ОДНА финальная верификация
```

Никаких `fix → gate → fix → gate → ...` циклов.

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`core/entrypoint-manifest.yaml`](../../core/entrypoint-manifest.yaml) | YAML-реестр gates (секция `gates:`) |
| [`../../core/AGENTS.md`](../../core/AGENTS.md) | Канонические операции, структура слоёв |
| [`../../core/check-suite.yaml`](../../core/check-suite.yaml) | **SoT-манифест набора проверок (DevPlan 120)** |
| [`../../core/internal/check_suite.py`](../../core/internal/check_suite.py) | Единый executor (diagnostic/gate/diff/fingerprint) |
| [`../../makefiles/repair.mk`](../../makefiles/repair.mk) | `make check`/`make check-diff` targets + repair-таргеты |
| `../../AGENTS.md` (root) | Архитектурные инварианты платформы |
