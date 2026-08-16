# GREP_SUMMARY: AGENTS.md, gates, taxonomy, registration-protocol, marker-contract, manifest, check-workflow, check-suite
# STRUCTURE: ┌gate taxonomy┐ → ◇ registration protocol (trinity: file + marker + manifest) → ◇ check workflow → ⊕ add/remove procedure → ⎋ cross-refs
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
| **consistency** | Согласованность cross-cutting конфигураций | test_gate_env_example_drift, test_gate_structural_consistency |
| **security** | Безопасность: секреты, пароли, network policies | test_gate_security_config, test_gate_ci_env_vars |
| **drift** | Обнаружение дрейфа артефактов | test_gate_manifest_integrity, test_gate_workflow_consistency |
| **coverage** | Покрытие: все скрипты/таргеты зарегистрированы | test_gate_no_unregistered_entrypoint |
| **enforcement** | Принудительные проверки (proxyless, PostgreSQL-only) | test_gate_litellm_pg_enforcement, test_gate_env_example_drift (NO_PROXY) |
| **volumes_sot** | Volumes: root compose — единственный SoT | test_gate_volumes_sot.py |
| **image_tag_form** | ghcr tag-политика: версионный тег / digest-pin, голый :latest — RED | test_gate_image_tag_form.py |

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
- CI gate `test_all_shebang_files_in_manifest` валидирует соответствие
- `make generate-entrypoint-manifest` пересобирает gates[] автоматически из pytest markers (G3 cycle break)

### Удаление gate
1. Удалить файл из `tests/gates/`
2. Удалить запись из `core/entrypoint-manifest.yaml` секции `gates`
3. Очистить `tests/gates/__pycache__/` от остатков удалённого файла

---

## Реестр гейтов

Реестр гейтов живёт в `core/entrypoint-manifest.yaml` (секция gates, G3 auto-discovery из
`@pytest.mark.gate`). Описание каждого гейта — в его файле (MODULE_CONTRACT).

---

## Check workflow (agent-oriented gate accelerator)

`make check` — единая диагностическая команда агента: собирает ВСЕ ошибки за один проход.
`make gate MODE=fast` остаётся канонической верификацией (арбитр), но исполняется ТОЛЬКО
pre-push hook'ом при пуше — в dev-цикле ручной прогон не выполняется.

**Три фазы (единый SoT-манифест `core/check-suite.yaml`):**
1. **Fix-фаза** — `make fix-gate` + tier=fix чеки манифеста (pre-commit)
2. **Fingerprint-кэш** — повторный прогон на неизменённом дереве = replay <10s (CHECK_CACHE=0 отключает; кэш ТОЛЬКО у check, gate — без кэша)
3. **Проверки из манифеста**: static-чеки параллельно + pytest-чеки последовательно с xdist (gates, gates-docker, contract, static_audit, predeploy)

### Использование

```bash
make check                  # стандартный запуск (автофикс + все проверки манифеста)
make check SKIP_FIX=1       # без авто-фикса
make check JSON=1           # JSON-вывод для машинной обработки
make check VERBOSE=1        # полный stdout/stderr упавших проверок
make check WORKERS=8        # число параллельных воркеров
make check CHECK_CACHE=0    # без fingerprint-кэша (полный честный прогон)
make check-diff             # узкая диагностика по изменённым файлам
```

### Инварианты

- **Check НЕ заменяет gate.** Gate — каноническая верификация (арбитр), исполняется ТОЛЬКО pre-push
  hook'ом. Check — диагностический акселератор (содержательный суперсет fast-шагов: зелёный check =
  зелёный gate fast на этом дереве). Оба executor'а читают ОДИН манифест `core/check-suite.yaml`.
- **Check НЕ коммитит изменения.** Только авто-фиксы в worktree (так же как `make fix-gate`).
- **Fingerprint-кэш — только у check.** Gate/CI/pre-push — без кэша (канонический прогон всегда).
  Replay только при байт-идентичном дереве И зелёном последнем прогоне.
- **Exit code 0** = все проверки прошли; **Exit code 1** = есть ошибки — `make check` повторно.
- **Parallel-чеки read-only** — не мутируют файлы; pytest-чеки строго последовательно (1 pytest с -n auto за раз).

### Рекомендуемый agent workflow

```
1. make check                        # ОДИН запуск — все ошибки собраны
2. Прочитать отчёт — все FAIL-секции
3. Исправить ВСЕ ошибки за один проход
4. make check                        # повторный — <10s если дерево не менялось
5. git push                          # pre-push hook: make gate MODE=fast — финальная верификация (без кэша)
```

Никаких `fix → gate → fix → gate → ...` циклов.

---

## R5 probe-конвенция

- Negative-тесты пишут probe-файлы в ОБЩИЕ директории (core/, tests/) ТОЛЬКО с именем
  `_gate_probe_*` + uuid-суффиксом; в tmp_path — безопасно без исключений.
- Позитивные сканеры исключают probe по ПРЕФИКСУ `_gate_probe_`, НЕ по точному имени
  (uuid-суффикс ломает точный матч).
- 12 параллельных прогонов make gate в одном push (pre-commit always_run + per-ref) — probe
  с фиксированным именем в общей директории = гонка (negative удаляет чужой probe /
  позитивный сканер видит чужой).

**Чеклист при фейле гейта (до воспроизведения):**
1. Прочитай файл гейта из FAIL-сообщения.
2. Grep имени probe/`_gate_probe_` по tests/gates/.
3. Grep конвенции/канона по идентификатору из сообщения — в правилах `.kilo/` и файлах гейтов.
4. Только потом воспроизводи/фикси.

---

## Cross-references

| Файл | Назначение |
|------|-----------|
| [`core/entrypoint-manifest.yaml`](../../core/entrypoint-manifest.yaml) | YAML-реестр gates (секция `gates:`) |
| [`../../core/AGENTS.md`](../../core/AGENTS.md) | Канонические операции, структура слоёв |
| [`../../core/check-suite.yaml`](../../core/check-suite.yaml) | SoT-манифест набора проверок |
| [`../../core/internal/check_suite/`](../../core/internal/check_suite/) | Единый executor-пакет (diagnostic/gate/diff/fingerprint) |
| [`../../makefiles/repair.mk`](../../makefiles/repair.mk) | `make check`/`make check-diff` targets + repair-таргеты |
| `../../AGENTS.md` (root) | Архитектурные инварианты платформы |
