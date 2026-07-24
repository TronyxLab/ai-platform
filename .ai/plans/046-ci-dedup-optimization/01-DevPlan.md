$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Удаление подтверждённых дубликатов, мёртвого кода и дрифта в CI-пайплайне + runtime-оптимизации (pytest-xdist для static gate, ruff напрямую). Цель: 4-8% экономии CI-минут без снижения качества проверок.
DESCRIPTION:           Четыре волны: Wave 1 — удаление мёртвого кода (H2, H3) и мёртвого таргета lint, Wave 2 — исправление дрифта (M1, B2-B4), Wave 3 — структурные улучшения (L1, L2), Wave 4 — runtime-оптимизации (O2: pytest-xdist для static gate, O5: ruff напрямую). Все правки в `__original` варианты таргетов — стабы (exit 0) не трогаем. W4-2 (cache pre-commit) исключена — кэш уже реализован в CI workflows.
RATIONALE:             CI теперь платный. Аудит выявил: 3 критических дубликата, 5 drift-проблем, 2 структурные неэффективности. Аудит DevPlan (2026-07-24) скорректировал: W4-2 — no-op (уже существует), W4-1 — xdist несовместим с Docker-фикстурами (ограничен static gate), W1-3 — удаление make lint (не замена на lint.sh). Реалистичная экономия: ~4-8% (8-16s).
ACCEPTANCE_CRITERIA:   AC1: `make -f makefiles/ci.mk __gate_original MODE=fast` — зелёный (верификация через __original, стабы exit 0 — НЕ использовать). AC2: Удалённые файлы не вызывают ImportError. AC3: Manifest/test_inventory синхронизированы. AC4: pytest-xdist на static gate проходит без гонок (3 прогона). AC5: ruff напрямую даёт идентичный результат с pre-commit run ruff-check ruff-format --all-files. AC6: `grep -r "make lint"` не находит активных вызовов после удаления таргета. AC7: ruff.toml содержит required-version, pytest-xdist добавлен в pyproject.toml dev dependencies.
IMPLEMENTS:            H2, H3, M1, B2, B3, B4, L1, L2, O2, O5
IMPACTS:               core/internal/scripts-audit.sh (удаление), tests/gates/test_gate_module_schema_d4*.py (удаление), core/entrypoint-manifest.yaml (удаление lint + scripts-audit), core/entrypoints/check-doc-headers.sh, makefiles/ci.mk, tests/test_inventory.yaml, tests/helpers/gate_helpers.py (расширение), tests/gates/test_gate_ci_coverage.py, tests/gates/test_gate_workflow_consistency.py, tests/gates/test_gate_manifests_up_to_date.py, ruff.toml, pyproject.toml, core/AGENTS.md (generated canonical table)
REQUIRES:              Стабы (exit 0) не трогаем — тестовый режим до 25 июля. Все правки только в `__original` варианты таргетов. Верификация через `make -f makefiles/ci.mk __<target>_original`. pytest-xdist (pip install). Права на запись в ruff.toml и pyproject.toml.
$END_ARTIFACT_CONTRACT

$DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Wave 1 — удаление мёртвого кода: scripts-audit.sh, D4 schema gate, удаление мёртвого make lint → GOAL_DEDUP
- GOAL Wave 2 — исправление дрифта: GREP_SUMMARY порог, repair contracts → GOAL_DRIFT
- GOAL Wave 3 — структурные улучшения: shared workflow parser (в существующий gate_helpers.py), git diff вместо make → GOAL_STRUCTURAL
- GOAL Wave 4 — runtime-оптимизации: pytest-xdist для static gate, ruff напрямую → GOAL_RUNTIME
- GOAL Финальная верификация: make -f makefiles/ci.mk __gate_original MODE=fast → GOAL_VERIFY
**SECTION_USE_CASES:**
- USE_CASE Разработчик делает git push → CI запускает make gate MODE=fast → SCENARIO_CI_FAST
- USE_CASE Разработчик делает git push в main → CI запускает make gate MODE=full → SCENARIO_CI_FULL
- USE_CASE Разработчик локально запускает make gate MODE=fast перед push → SCENARIO_LOCAL_GATE
- USE_CASE CI-раннер запускает pytest-xdist на static gate-тестах → SCENARIO_XDIST_STATIC
$START_DEVPLAN

$ARTIFACT_CONTRACT

## Корректировка исходного аудита

| Проблема | Заявлено | Реальность |
|----------|---------|-----------|
| **H2**: «тройная валидация D4→D5» | 3 независимых валидации | Нет D4-схемы. Двойная валидация + расширенный D5-validator (суперсет) |
| **M2**: «почти идентичная логика» forbidden verbs | Дубликат | Разный scope (root+module vs только module) и направление (negative vs positive). **Не дубликат** — complementary checks |
| **M3**: «двойная проверка forbidden scripts» | Дубликат | Разные механизмы (`os.walk` vs rglob). Частичное пересечение, **не дубликат** |
| **M4**: «~10/20 дубликатов» в status_page | 50% файла — мусор | **3/20 (15%)** с частичным пересечением. Аудит завысил в 3.3× |
| **Пропущено B1-B6** | — | Застабленные таргеты, dangling/amber repair contracts, ruff×4, двойная repair-регистрация |

---

## Architecture Overview

### Draft Code Graph

```
                               make gate MODE=fast|full
                                       │
                     ┌─────────────────┼──────────────────┐
                     ▼                 ▼                   ▼
              pre-commit-run     validate             pytest gates
              (O5 ruff direct)  (no changes)    (O2 xdist static only)
                     │                                  │
                     ▼                                  ▼
              ruff (O5 direct)                  ┌── gate tests ───┐
              non-ruff hooks                   │ workflow parsers │
                                               │ (W3.1 → gate_helpers)│
                                               │ manifest check   │
                                               │ (W3.2 git diff)  │
                                               └──────────────────┘

     entrypoint-manifest.yaml ◄── generate_entrypoint_manifest.py
     (W1.1 del scripts-audit, W1.3 del lint, W2.2 repair contracts)

     ci.mk
     (W1.1 del scripts-audit, W1.3 del lint, W4.1 xdist static-only)
```

### Step-by-step Data Flow

1. **`make gate MODE=fast`** (локально / CI push):
   - Step 1: `pre-commit-run` → ruff check + format (O5) + non-ruff hooks
   - Step 2: `validate` → validate.sh (no changes)
   - Step 3a: `pytest tests/gates/ -m "gate and not requires_docker" -n auto` (O2: xdist только static)
   - Step 3b: `pytest tests/gates/ -m "gate and requires_docker"` (sequential, без xdist)
   - Step 4-6: contract, static, predeploy (no changes)
   - **Exit:** все шаги pass → gate green

2. **`make scripts-audit`** (W1.1):
   - Был: вызов `core/internal/scripts-audit.sh` (98 строк, хрупкий grep-based дубликат)
   - Стал: удалён вместе с таргетом. Gate #1 (`test_all_shebang_files_in_manifest`) — полный суперсет

3. **`make lint`** (W1.3):
   - Был: `validate.sh --lint` → no-op (флаг `--lint` игнорируется)
   - Стал: **удалён**. `lint.sh` (GREP_SUMMARY/namelint) остаётся pre-commit хуком, не вызывается через `make lint`. Экономия: −1 вызов make + −1 no-op validate.sh

4. **Repair Contract Resolution** (W2.2-W2.4):
   - `fix-ruff`: `gate_id: ruff-format` → ищем реальный gate (ruff-format — pre-commit хук, не gate)
   - `fix-gate`: `gate_id: check-manifests` → `gate_id: test_manifests_up_to_date` (check-manifests — make target)
   - `test_executable_bit_outside_lib`: удалить inline repair (строки 627-633) — дубликат секции `repair:`. Проверить, что `generate_entrypoint_manifest.py` не перегенерирует.

5. **Workflow Parser Unification** (W3.1):
   - `_get_on_section()` дублируется: `test_gate_ci_coverage.py:224` и `test_gate_workflow_consistency.py:119`
   - `_load_workflow()` определена в `test_gate_ci_coverage.py:211`
   - Миграция в **существующий** `tests/helpers/gate_helpers.py` (не новый файл)

6. **Manifest Check** (W3.2):
   - Был: `subprocess.run(["make", "check-manifests"])`
   - Стал: `git diff --exit-code` на generated files (список синхронизирован с `__check_manifests_original`)

---

## $TASKS

### Wave 1: Удаление мёртвого кода и подтверждённых дубликатов (🔴 HIGH)

**Оценка:** 1-2h, экономия CI: −2-4s (~1-2%)

#### TASK-W1-1 — H3: Удалить `scripts-audit.sh`

| Параметр | Значение |
|----------|----------|
| **Сложность** | 2/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (5)** | `core/internal/scripts-audit.sh` (удаление), `makefiles/ci.mk` (удалить `__scripts_audit_original`, строки 317-319; удалить из `.PHONY`, строка 12), `core/entrypoint-manifest.yaml` (удалить `scripts-audit` запись, строки 125-130; удалить из `allowed_verbs`, строка 1217), `core/AGENTS.md` (generated canonical table обновится через `make generate-manifests`), `tests/test_inventory.yaml` (обновится через `make test-inventory-sync`) |
| **AC** | `make generate-manifests` — нет `scripts-audit` в manifest. `make test-inventory-sync` — inventory синхронизирован. `grep -r "scripts-audit" core/ makefiles/` — нет упоминаний (кроме исторических комментариев). Gate #1 (`test_all_shebang_files_in_manifest`) зелёный. |
| **Риск** | Нулевой — gate #1 полный суперсет |

**Действия:**
1. Удалить `core/internal/scripts-audit.sh`
2. В `makefiles/ci.mk`: удалить `__scripts_audit_original` таргет (строки 317-319) и удалить `scripts-audit` + `__scripts_audit_original` из `.PHONY` (строка 12)
3. В `core/entrypoint-manifest.yaml`: удалить запись `scripts-audit` (строки 125-130), удалить `scripts-audit` из `allowed_verbs` (строка 1217)
4. `make generate-manifests && make test-inventory-sync`

---

#### TASK-W1-2 — H2: Удалить D4 module schema gate

| Параметр | Значение |
|----------|----------|
| **Сложность** | 1/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (2)** | `tests/gates/test_gate_module_schema_d4.py` (удаление), `tests/gates/test_gate_module_schema_d4_negative.py` (удаление — импортирует `_validate_module_yaml_d4` из d4) |
| **AC** | `pytest tests/gates/test_gate_module_yaml_contract.py -v` — все D5-тесты зелёные. `pytest tests/gates/ -m gate -v` — нет пропущенных тестов referencing d4. `make generate-manifests` — gate-записи для d4 удалены (~7 записей). `make test-inventory-sync` — inventory синхронизирован. `grep -r "_validate_module_yaml_d4" tests/` — только исторические упоминания (не импорты). |
| **Риск** | Низкий. D5 validator полный суперсет (schema + env_requires + restart-drift). Cross-file риск已验证: `test_d4_bare_string_still_valid` в `test_gate_module_yaml_contract_d5_negative.py` использует `validate_module` (D5), не импортирует `_validate_module_yaml_d4`. Единственный cross-file импорт — `test_gate_module_schema_d4_negative.py` → удаляется вместе с d4. |

**Действия:**
1. **До удаления:** `grep -rn "_validate_module_yaml_d4" tests/` — проверить cross-file импорты. Подтверждено: только `test_gate_module_schema_d4_negative.py:14` импортирует — удаляется вместе с d4 файлом.
2. Удалить `tests/gates/test_gate_module_schema_d4.py`
3. Удалить `tests/gates/test_gate_module_schema_d4_negative.py`
4. `make generate-manifests` (автоматически удалит ~7 gate-записей)
5. `make test-inventory-sync`

---

#### TASK-W1-3 — H1: Удалить `make lint` — мёртвый таргет

| Параметр | Значение |
|----------|----------|
| **Сложность** | 1/10 |
| **Роль** | Coder |
| **Зависимости** | Нет (разные строки в общих файлах с W1-1, но операция независима) |
| **Файлы (2)** | `makefiles/ci.mk` (удалить таргеты `__lint_original` + `lint`), `core/entrypoint-manifest.yaml` (удалить запись `lint`, строки 109-114; удалить из `allowed_verbs`) |
| **AC** | `grep -r "make lint" core/ makefiles/` — нет активных вызовов (кроме исторических комментариев). `make generate-manifests` — canonical table не содержит `lint`. `grep "lint" core/entrypoint-manifest.yaml` — нет записи. |
| **Риск** | Нулевой. `validate.sh --lint` был no-op (флаг `--lint` игнорируется). `lint.sh` (GREP_SUMMARY/namelint) остаётся pre-commit хуком и НЕ должен вызываться через `make lint`. |

@rationale: План изначально предлагал заменить `validate.sh --lint` → `lint.sh`. Аудит DevPlan (2026-07-24) выявил семантическую подмену: `lint.sh` — это GREP_SUMMARY/namelint pre-commit хук, а НЕ shellcheck/yamllint (как описано в entrypoint-manifest.yaml). Замена создала бы семантический дрифт между manifest-описанием и реальным поведением. Решение: удалить `make lint` полностью как исторический артефакт.

**Действия:**
1. В `makefiles/ci.mk`: удалить таргеты `__lint_original` (строки 254-257) и `lint` (stub, строка 258-260); удалить из `.PHONY`
2. В `core/entrypoint-manifest.yaml`: удалить запись `lint` (строки 109-114), удалить `lint` из `allowed_verbs`
3. `make generate-manifests`

---

### Wave 2: Исправление дрифта (🟡 MEDIUM)

**Оценка:** 30m, экономия CI: 0s (санитарная очистка)

#### TASK-W2-1 — M1: Унифицировать GREP_SUMMARY порог 5→10

| Параметр | Значение |
|----------|----------|
| **Сложность** | 1/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (1)** | `core/entrypoints/check-doc-headers.sh` (строки 52, 59, 98: `head -5` → `head -10`) |
| **AC** | `bash core/entrypoints/check-doc-headers.sh` на всех staged файлах — нет false-positive FAIL для GREP_SUMMARY на строке 6-10. Pre-commit и gate выдают идентичный результат для `check_grep_summary()`. |
| **Риск** | Нулевой |

**Действия:**
1. `core/entrypoints/check-doc-headers.sh` строка 52: `head -5 "$file"` → `head -10 "$file"`
2. Строка 59: `head -5 "$file"` → `head -10 "$file"`
3. Строка 98 (check_structure): `head -5 "$file"` → `head -10 "$file"`

> **Примечание:** `check_grep_summary` в pre-commit (строка 52) использовала `head -5`, а gate-тест `test_gate_grep_summary.py` использует `zip(range(10), fh)`. Pre-commit был строже gate — инвертированная логика. Файл с GREP_SUMMARY на строке 6: gate green, commit blocked.

---

#### TASK-W2-2 — B2+B3+B4: Fix repair contracts + dedup repair registration

| Параметр | Значение |
|----------|----------|
| **Сложность** | 3/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (1-2)** | `core/entrypoint-manifest.yaml` (строки 277-284, 292-300, 624-633) |
| **AC** | `make generate-manifests` — нет ошибок. `grep "gate_id: ruff-format" core/entrypoint-manifest.yaml` — заменён на реальный gate_id. `grep "gate_id: check-manifests" core/entrypoint-manifest.yaml` — заменён на `test_manifests_up_to_date`. `grep "repair_id: executable-bit" core/entrypoint-manifest.yaml` — ровно одно вхождение (в секции `repair:`), нет inline-дубликата в секции `gates:`. |
| **Риск** | Низкий. `test_executable_bit_outside_lib` с inline repair — DRY violation. `ruff-format` — dangling reference (нет такого gate). `check-manifests` — amber reference (make target, не gate). |

**Действия:**

**B2 — fix-ruff repair:**
1. Найти реальный gate, проверяющий ruff-форматирование. Кандидаты: `test_hook_contract_validation` для ruff-format хука. Выполнить: `grep -rl "ruff" tests/gates/` для идентификации.
2. В `core/entrypoint-manifest.yaml` строка 278: `gate_id: ruff-format` → реальный gate_id

**B3 — fix-gate repair:**
1. Строка 293: `gate_id: check-manifests` → `gate_id: test_manifests_up_to_date`
2. Верифицировать: `grep "test_manifests_up_to_date" tests/` — gate существует

**B4 — dedup executable-bit repair:**
1. Удалить inline `repair_id`/`repair_command`/`repair_description`/`repair_safe`/`repair_idempotent`/`repair_class`/`repairable` из записи `test_executable_bit_outside_lib` (строки 627-633)
2. Проверить, что `generate_entrypoint_manifest.py` не перегенерирует inline-метаданные (auto-discovered gates могут их добавлять обратно — если да, нужно править генератор или добавить suppression)

---

### Wave 3: Структурные улучшения (🟢 LOW)

**Оценка:** 2-3h, экономия CI: −2-5s (~1-3%) + maintainability

#### TASK-W3-1 — L2: Shared workflow parser (в существующий gate_helpers.py)

| Параметр | Значение |
|----------|----------|
| **Сложность** | 3/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (3)** | `tests/helpers/gate_helpers.py` (добавить `load_workflow`, `get_on_section`), `tests/gates/test_gate_ci_coverage.py` (удалить дубликаты, импортировать из helpers), `tests/gates/test_gate_workflow_consistency.py` (удалить дубликат, импортировать из helpers) |
| **AC** | `pytest tests/gates/ -m gate -v` — все gate-тесты зелёные после миграции. `_get_on_section` и `_load_workflow` определены **только** в `tests/helpers/gate_helpers.py` (один source of truth). Все workflow-тесты проходят без изменений в assertions. |
| **Риск** | Низкий. Рефакторинг 2 дублирующих функций в 2 файлах (не 7, как изначально заявлялось). Митигация: полный прогон gate после миграции. |

@rationale: Аудит DevPlan (2026-07-24) показал, что scope значительно меньше заявленного: `_get_on_section` дублируется в 2 файлах (не 7), `_load_workflow` — в 1. Файлы `test_gate_workflow_checkout_order.py`, `test_gate_ci_env_vars.py` НЕ содержат этих дубликатов. `tests/helpers/gate_helpers.py` уже существует — функции добавляются в него, новый файл не создаётся.

**Действия:**
1. Добавить в `tests/helpers/gate_helpers.py`:
   ```python
   def load_workflow(workflow_name: str) -> dict:
       """Load and parse a GitHub workflow YAML file."""
       ...

   def get_on_section(workflow: dict) -> dict:
       """Extract the 'on' (trigger) section from a workflow."""
       ...
   ```
2. Идентифицировать дубликаты:
   - Подтверждено: `test_gate_ci_coverage.py:211` (`_load_workflow`), `test_gate_ci_coverage.py:224` и `test_gate_workflow_consistency.py:119` (`_get_on_section`)
   - Проверить остальные файлы: `rg -n "def _.*workflow|def _.*_on_section" tests/gates/`
3. Удалить дублирующие private-функции, заменить на импорт из `tests.helpers.gate_helpers`
4. Прогнать `pytest tests/gates/ -m gate -v` — все зелёные

---

#### TASK-W3-2 — L1: git diff вместо subprocess `make check-manifests`

| Параметр | Значение |
|----------|----------|
| **Сложность** | 1/10 |
| **Роль** | Coder |
| **Зависимости** | Нет |
| **Файлы (1)** | `tests/gates/test_gate_manifests_up_to_date.py` |
| **AC** | Тест по-прежнему детектирует расхождение generated files. Сообщение об ошибке содержит инструкцию `make generate-manifests`. Экономия: ~0.5s (устранение make-индирекции). Список файлов синхронизирован с `__check_manifests_original` в `ci.mk`. |
| **Риск** | Низкий. `git diff --exit-code` детерминирован и быстрее. Основной риск — расхождение списка файлов с Makefile. |

@rationale: Аудит DevPlan (2026-07-24): `__check_manifests_original` уже использует `git diff --exit-code`. Тест — тонкая обёртка над `subprocess.run(["make", "check-manifests"])`. Замена на прямой `git diff --exit-code` экономит ~0.5s (fork make против fork git). Список файлов ДОЛЖЕН быть синхронизирован с `__check_manifests_original`.

**Действия:**
1. Заменить `subprocess.run(["make", "check-manifests"], ...)` на `git diff --exit-code core/secrets-manifest.yaml core/platform-env.yaml tests/smoke_env_generated.py core/internal/scripts/env_defaults_generated.py`
2. Сверить список файлов с таргетом `__check_manifests_original` в `makefiles/ci.mk`
3. Сохранить сообщение об ошибке с инструкцией `make generate-manifests`

---

### Wave 4: Runtime-оптимизации (🔴 HIGH — драйвер экономии)

**Оценка:** 1-2h, экономия CI: −5-15s (~3-5%)

#### TASK-W4-1 — O2+O5: pytest-xdist для static gate + Ruff напрямую

| Параметр | Значение |
|----------|----------|
| **Сложность** | 5/10 |
| **Роль** | Coder |
| **Зависимости** | Требует `pip install pytest-xdist` + `pyproject.toml` dev dependency |
| **Файлы (3)** | `makefiles/ci.mk` (строки 147, 177 — добавить xdist split: static `-n auto` + Docker sequential), `ruff.toml` (добавить `required-version`), `pyproject.toml` (добавить `pytest-xdist` в dev dependencies) |
| **AC** | AC4: Три прогона `pytest tests/gates/ -m "gate and not requires_docker" -n auto -v` — нет flaky-тестов. `pytest tests/gates/ -m "gate and requires_docker" -v` (sequential) — зелёный. AC5: `ruff check . && ruff format --check .` — идентичный результат с `pre-commit run ruff-check ruff-format --all-files`. __gate_original MODE=fast — зелёный. |
| **Риск** | Средний. O2 риск — gate-тесты с `requires_docker` маркером НЕ параллелятся (Docker-фикстуры: session-scoped compose stack, `.test_counter.json`, `NetworkLeaseManager` — несовместимы с xdist). O5 риск — расхождение версий ruff между локальной и pre-commit (митигация: `ruff.toml` `required-version`). |

@rationale: Аудит DevPlan (2026-07-24) выявил, что session-scoped Docker-фикстуры (`tests/_conftest/smoke.py::platform_services`, `tests/_conftest/session.py::.test_counter.json`) несовместимы с xdist: каждый воркер запустит свой compose stack → конфликт имён контейнеров/портов/сетей. Решение: xdist только для static gate-тестов (unit, contract, static_audit — без Docker). Docker-dependent тесты идут отдельным sequential-вызовом без `-n auto`.

**Действия:**

**O2 — pytest-xdist (только static gate):**
1. `pip install pytest-xdist`
2. Добавить `pytest-xdist` в `pyproject.toml` → `[project.optional-dependencies] dev`
3. В `__gate_original` (MODE=fast, строка 147): разбить на две фазы:
   ```makefile
   # Static gate: parallel (no Docker fixtures)
   PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and not requires_docker" -n auto -v
   # Docker gate: sequential (session-scoped fixtures incompatible with xdist)
   PYTEST_NO_ESCALATION=1 $(PYTHON) -m pytest tests/gates/ -m "gate and requires_docker" -v
   ```
4. Аналогично для MODE=full (строка 177)
5. Проверить 3 прогона static gate: `pytest tests/gates/ -m "gate and not requires_docker" -n auto -v`

**O5 — ruff напрямую:**
1. Добавить `required-version` в `ruff.toml` (версия из `.pre-commit-config.yaml`)
2. В `__pre-commit-run_original` (строки 299-304 `ci.mk`): заменить на:
   ```makefile
   __pre-commit-run_original:
   	@echo "[IMP:7][make][pre-commit-run] Running pre-commit checks..."
   	@echo "[IMP:7][make][pre-commit-run] Step 1/2: ruff (check + format)..."
   	@ruff check . && ruff format --check . || { echo "[IMP:9] ruff FAIL"; exit 1; }
   	@echo "[IMP:7][make][pre-commit-run] Step 2/2: pre-commit (non-ruff hooks)..."
   	@SKIP=ruff-check,ruff-format pre-commit run --all-files || { echo "[IMP:9] pre-commit FAIL"; exit 1; }
   ```
3. Верифицировать идентичность: `ruff check . && ruff format --check .` vs `pre-commit run ruff-check ruff-format --all-files`

---

## $PARALLEL_GROUPS

### File Intersection Matrix

```
            W1-1  W1-2  W1-3  W2-1  W2-2  W3-1  W3-2  W4-1
W1-1         -     E     C     -     -     -     -     C
W1-2         E     -     E     -     -     -     -     -
W1-3         C     E     -     -     -     -     -     C
W2-1         -     -     -     -     -     -     -     -
W2-2         -     -     -     -     -     -     -     -
W3-1         -     -     -     -     -     -     -     -
W3-2         -     -     -     -     -     -     -     -
W4-1         C     -     C     -     -     -     -     -
```

**Легенда:** `C` = общий файл, конфликт (ci.mk, entrypoint-manifest.yaml). `E` = generated file пересечение (manifest, inventory — решается через `make generate-manifests` после каждой задачи). `-` = нет общих файлов.

### Wave Grouping

#### Wave 1 (dedup — независимые файлы у каждой задачи)
- **Sub-group 1A (no shared files):** TASK-W1-2 (только удаление 2 файлов — нет пересечений с другими)
- **Sub-group 1B:** TASK-W1-1, TASK-W1-3 (обе редактируют `ci.mk` и `entrypoint-manifest.yaml`, но **разные строки** — конфликт при параллельном выполнении, требуют последовательного выполнения)

**Рекомендация:** выполнять W1-1 → W1-3 последовательно (общие файлы `ci.mk` + `manifest.yaml`). W1-2 параллельно с ними (нет общих файлов).

```
Wave 1A: TASK-W1-2 (D4 schema)    ─┐
Wave 1B: TASK-W1-1 → TASK-W1-3     │ параллельно с W1-2
                                    ┘
```

#### Wave 2 (drift fix — изолированные файлы)
```
Wave 2: TASK-W2-1 ∥ TASK-W2-2 (параллельно, нет общих файлов)
```

#### Wave 3 (structural — изолированные файлы)
```
Wave 3: TASK-W3-1 ∥ TASK-W3-2 (параллельно, нет общих файлов)
```

#### Wave 4 (runtime — ci.mk)
```
Wave 4: TASK-W4-1 (единственная задача в волне после удаления W4-2)
```

---

## Acceptance Criteria (summary)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | `make -f makefiles/ci.mk __gate_original MODE=fast` зелёный | Запуск через __original (НЕ через стабы exit 0) |
| AC2 | Удалённые файлы не вызывают ImportError | `pytest tests/gates/ -m gate -v` |
| AC3 | Manifest/test_inventory синхронизированы | `make generate-manifests && make test-inventory-sync` — no diff |
| AC4 | pytest-xdist static gate без гонок | 3 прогона `pytest tests/gates/ -m "gate and not requires_docker" -n auto -v` — 100% pass |
| AC5 | Ruff идентичен pre-commit | `ruff check . && ruff format --check .` ≡ `pre-commit run ruff-check ruff-format --all-files` |
| AC6 | D5 validator покрывает D4 | `pytest tests/gates/test_gate_module_yaml_contract.py -v` — все pass |
| AC7 | `make lint` удалён | `grep -r "make lint" core/ makefiles/` — нет активных вызовов |
| AC8 | GREP_SUMMARY порог 10 строк | Pre-commit не блокирует GREP_SUMMARY на строке 6 |
| AC9 | ruff.toml + pyproject.toml | `ruff.toml` содержит `required-version`; `pyproject.toml` содержит `pytest-xdist` в dev deps |

---

## File Manifest

### Удаляемые (3)
| Файл | Задача |
|------|--------|
| `core/internal/scripts-audit.sh` | W1-1 |
| `tests/gates/test_gate_module_schema_d4.py` | W1-2 |
| `tests/gates/test_gate_module_schema_d4_negative.py` | W1-2 |

### Новые (0)
Новых файлов не создаётся. W3-1 расширяет существующий `tests/helpers/gate_helpers.py`.

### Изменяемые (9)
| Файл | Задачи |
|------|--------|
| `makefiles/ci.mk` | W1-1, W1-3, W4-1 |
| `core/entrypoint-manifest.yaml` | W1-1, W1-3, W2-2 |
| `core/entrypoints/check-doc-headers.sh` | W2-1 |
| `tests/helpers/gate_helpers.py` | W3-1 |
| `tests/gates/test_gate_ci_coverage.py` | W3-1 |
| `tests/gates/test_gate_workflow_consistency.py` | W3-1 |
| `tests/gates/test_gate_manifests_up_to_date.py` | W3-2 |
| `ruff.toml` | W4-1 |
| `pyproject.toml` | W4-1 |

### Авто-генерируемые (регенерация через make)
| Файл | Обновляется |
|------|-------------|
| `core/AGENTS.md` (canonical table) | `make generate-manifests` |
| `tests/test_inventory.yaml` | `make test-inventory-sync` |

**Итого:** 3 удаляемых + 0 новых + 9 изменяемых + 2 авто-генерируемых = **12 целенаправленных + 2 generated**

---

## $TEST_SPEC

**$TEST_SPEC: NONE**

@rationale: Все изменения — удаление мёртвого кода (W1), исправление дрифта (W2), структурный рефакторинг (W3), и CI runtime-оптимизации (W4). Новые тестовые функции не требуются. Существующие gate-тесты (204 теста) служат regression-защитой:
- W1.1: gate #1 (`test_all_shebang_files_in_manifest`) — суперсет удаляемого `scripts-audit.sh`
- W1.2: D5 validator (`test_d5_validator_passes_on_all_modules`) — суперсет D4
- W1.3: `lint.sh` уже тестируется через pre-commit hook contract validation
- W3.1: существующие workflow gate-тесты — regression protection для рефакторинга
- W4.1: 3 прогона gate — верификация отсутствия flaky-тестов (не требует новых test functions)

**Верификация реализуется через:** `make generate-manifests && make gate MODE=fast` (3 прогона для O2).

---

## Оценка экономии

| Источник | MODE=fast (~185-400s) | MODE=full (~400-900s) |
|----------|----------------------|----------------------|
| **Wave 1** (dedup) | −2-4s (~1-2%) | −2-4s (~0.5-1%) |
| **Wave 2** (drift fix) | 0s | 0s |
| **Wave 3** (structural) | −1-2s (<1%) | −1-2s (<0.5%) |
| **Wave 4** (runtime) | **−5-10s (~2-4%)** | **−5-10s (~1-2%)** |
| ── O2 (pytest-xdist static only) | −2-5s | −2-5s |
| ── O5 (ruff directly) | −3-5s | −3-5s |
| **Итого реалистично** | **−8-16s (~4-8%)** | **−8-16s (~1-3%)** |

### Почему экономия ниже первоначальной оценки

План изначально заявлял 15-25% (54-114s). Аудит DevPlan (2026-07-24) выявил:

| Задача | План | Реальность | Причина |
|--------|------|-----------|---------|
| O2 (pytest-xdist) | −40-80s | −2-5s | xdist ограничен static gate (без Docker-фикстур). ~30% gate-тестов требуют Docker → sequential |
| O4 (cache) | −5-15s | **0s** | Кэш pre-commit уже реализован в CI workflows → задача удалена |
| W3-2 (git diff) | −1-2s | <0.5s | fork `make` vs fork `git` — одинаковая стоимость |
| W3-1 (shared parser) | −1-2s | <1s | Scope сокращён в 3-4× (2 файла, не 7) |

Wave 4 — основной драйвер заявленной экономии — содержал одну нереализуемую задачу (O2 без учёта Docker-фикстур) и одну нулевую (O4 уже сделана). Реалистичная экономия: **~4-8% (8-16s)**.

---

## Design Decisions

### DD1 — Стабы не трогаем (W1 decision)
@rationale: `ci.mk` содержит застабленные таргеты (exit 0) с комментарием «НЕ СПРАШИВАТЬ ДО 25 ИЮЛЯ». Все правки только в `__original` варианты. Стабы восстанавливаются после 25 июля заменой `stub → __original`. Верификация — через `make -f makefiles/ci.mk __<target>_original` в обход стабов.

### DD2 — W1-1 и W1-3 последовательно (PARALLEL_GROUPS decision)
@rationale: Обе задачи редактируют `makefiles/ci.mk` и `core/entrypoint-manifest.yaml`. Хотя строки разные, `git merge` при параллельных ветках создаст конфликт. Последовательное выполнение (W1-1 → W1-3) в одной ветке безопаснее.

### DD3 — W4-1 объединяет O2 и O5 (merge micro-task)
@rationale: O2 (pytest-xdist static only) и O5 (ruff напрямую) обе модифицируют `makefiles/ci.mk`. По Merge Rule (files_count ≤ 2 AND разные строки) — обе влезают в один таргет. Плюс `ruff.toml` и `pyproject.toml` (не пересекаются с другими задачами).

### DD4 — W3.1 scope: 2 функции в 2 файла → существующий gate_helpers.py
@rationale: Аудит DevPlan (2026-07-24) показал фактический scope: `_get_on_section` дублируется в 2 файлах, `_load_workflow` в 1 файле. Изначальная оценка «7 файлов (~2798 LOC)» завышена в 3-4×. Новый файл `gate_workflow_helpers.py` не создаётся — функции добавляются в существующий `tests/helpers/gate_helpers.py`.

### DD5 — B2 gate resolution
@rationale: `gate_id: ruff-format` не существует как gate-тест. `ruff-format` — pre-commit хук. Coder должен найти реальный gate (вероятно, `test_hook_contract_validation` с фильтром по ruff-format хуку) через `grep -rl "ruff" tests/gates/`. Если gate не найден — удалить `repairs_gates` запись (dangling reference).

### DD6 — W4-2 исключена (уже реализована)
@rationale: Аудит подтвердил: `actions/cache@v6` с `~/.cache/pre-commit` уже присутствует в `push-gate.yml:61-65` и `platform-test.yml:92-96`. Задача удалена из плана.

### DD7 — xdist только static gate (не все gate-тесты)
@rationale: `tests/_conftest/smoke.py::platform_services` (session-scoped Docker compose), `tests/_conftest/session.py::.test_counter.json` (конкурентный JSON), `NetworkLeaseManager` — несовместимы с xdist. Решение: `-n auto` только для `-m "gate and not requires_docker"`, Docker-dependent тесты — отдельный sequential вызов.

### DD8 — make lint удалён, не заменён
@rationale: `validate.sh --lint` был no-op (флаг игнорируется). `lint.sh` — GREP_SUMMARY/namelint pre-commit хук, а не shellcheck/yamllint (как описано в manifest). Замена `lint.sh` создала бы семантический дрифт. Решение: удалить таргет как исторический артефакт.

---

## Порядок выполнения

```
Wave 1 (1-2h)             Wave 2 (30m)         Wave 3 (1-2h)        Wave 4 (1-2h)
├─ W1-2 (D4 schema)      ├─ W2-1 (GREP 5→10)  ├─ W3-1 (shared)     └─ W4-1 (xdist+ruff)
├─ W1-1 (scripts-audit)  └─ W2-2 (repair fix)  └─ W3-2 (git diff)
└─ W1-3 (del lint)
     │                        │                     │                    │
     ├─ W1-2 ∥ (W1-1→W1-3)   ├─ W2-1 ∥ W2-2        ├─ W3-1 ∥ W3-2       └─ W4-1 (solo)
     │                        │                     │
     ▼                        ▼                     ▼
make generate-manifests    verify              pytest gates -v
make test-inventory-sync
__gate_original MODE=fast
```

**Критические точки верификации:**

1. После W1-2: `pytest tests/gates/test_gate_module_yaml_contract.py -v` — все D5-тесты зелёные
2. После W1-3: `grep -r "make lint" core/ makefiles/` — нет активных вызовов
3. После W4-1: три прогона `pytest tests/gates/ -m "gate and not requires_docker" -n auto -v` — нет flaky-тестов
4. После W4-1: `ruff check . && ruff format --check .` ≡ `pre-commit run ruff-check ruff-format --all-files`
5. Финально: `make generate-manifests && make -f makefiles/ci.mk __gate_original MODE=fast` — зелёный

---

## Debt Intake

### IN_SCOPE (включено в задачи DevPlan)
- B2: Dangling repair contract `fix-ruff → ruff-format` (W2-2)
- B3: Amber repair contract `fix-gate → check-manifests` (W2-2)
- B4: Двойная repair-регистрация executable-bit (W2-2)

### DEFER (отложено)
- **ruff ×4 в pre-commit config**: pre-commit вызывает ruff 4 раза (check + format для Python + check + format для Jupyter?). Требует анализа `.pre-commit-config.yaml` — может быть избыточным. DEFER до post-Wave 4 верификации: если ruff напрямую (O5) уже даёт всю нужную проверку, можно сократить хуки в `.pre-commit-config.yaml`.
  - Revision condition: после W4-1, при следующем аудите pre-commit производительности.
- **validate.sh --lint no-op**: validate.sh игнорирует `--*` флаги (18 строк фасада → `core/internal/validate/validate.sh`). Уже исправлено через W1-3 (make lint удалён). validate.sh остаётся без флагов.
  - Revision condition: resolved by W1-3.

### REMOVED (исключено из плана по результатам аудита)
- **W4-2 (cache pre-commit)**: уже реализован в `push-gate.yml:61-65` и `platform-test.yml:92-96` (`actions/cache@v6` с `~/.cache/pre-commit`). REMOVED — no-op.
- **H1 lint→lint.sh замена**: заменено на удаление `make lint`. lint.sh остаётся pre-commit хуком, не дублируется в `make lint`. REMOVED — семантический дрифт.

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/046-ci-dedup-optimization/01-DevPlan.md, implement Wave 1: TASK-W1-1, TASK-W1-2, TASK-W1-3
```

### Wave 2
```
coder Read .ai/plans/046-ci-dedup-optimization/01-DevPlan.md, implement Wave 2: TASK-W2-1, TASK-W2-2
```

### Wave 3
```
coder Read .ai/plans/046-ci-dedup-optimization/01-DevPlan.md, implement Wave 3: TASK-W3-1, TASK-W3-2
```

### Wave 4
```
coder Read .ai/plans/046-ci-dedup-optimization/01-DevPlan.md, implement Wave 4: TASK-W4-1
```

### Post-implementation verification
```
make generate-manifests && make test-inventory-sync
make -f makefiles/ci.mk __gate_original MODE=fast
pytest tests/gates/ -m "gate and not requires_docker" -n auto -v --count=3  # verify no flakes (O2 static)
ruff check . && ruff format --check .              # verify identical to pre-commit (O5)
```

$END_DEVPLAN
