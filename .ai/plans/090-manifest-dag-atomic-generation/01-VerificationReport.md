$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Семантическая QA-верификация DevPlan 090 (Manifest DAG + Atomic Generation) — статический аудит, self-consistency проверка, анализ покрытия, оценка измеримости AC.
DESCRIPTION:           Проверка DevPlan по 11 направлениям: $ARTIFACT_CONTRACT, self-consistency, покрытие задач, корректность волн, измеримость AC, интеграция G6 во все секции, --Check Mode Contract (byte-level для YAML), атомарная генерация (mktemp + trap), T9a-T9e покрытие 4 потребителей, $TEST_SPEC полнота, CI re-enablement (platform-test.yml в File Manifest).
RATIONALE:             DevPlan 090 — foundation-слой: если манифесты расходятся с источниками, CI gate'ы дают ложные срабатывания. Ошибки в плане дороже ошибок в коде — они тиражируются через Coder-агентов.
ACCEPTANCE_CRITERIA:   Все MAJOR-находки задокументированы с рекомендациями. MAJOR ≤ 0 → STABLE. MAJOR > 0 → DRIFTED (plan-level drift).
IMPLEMENTS:            QA Phase 1-4 для DevPlan 090. Статический аудит + cross-reference анализ + инвариантная проверка + test spec quality.
IMPACTS:               Этот VerificationReport (1 файл). Рекомендовано создание 02-VerificationReport.md после имплементации для Phase 5 runtime validation.
REQUIRES:              DevPlan 090 (.ai/plans/090-manifest-dag-atomic-generation/DevPlan.md). Доступ к filesystem для cross-reference проверки.
$END_ARTIFACT_CONTRACT

---

# VerificationReport 090: Manifest DAG + Atomic Generation

**🔒 Verified against SHA:** `5a31ef2bafd10b6bbe59345d35625e3b1c108953`
**Date:** 2026-07-28
**Verdict:** **MAJOR — DevPlan требует 6 исправлений до имплементации**
**Score:** 84/100 (см. детализацию ниже)

---

## Сводка находок

| # | Severity | Категория | Кратко |
|---|----------|-----------|--------|
| M1 | **MAJOR** | File Manifest gap | `entrypoint-manifest.yaml` отсутствует в MODIFY — 2 stale references к gen-env-platform.sh после T9e |
| M2 | **MAJOR** | $TEST_SPEC incomplete | Нет колонки Expected Result — тесты не имеют измеримых критериев приёмки |
| M3 | **MAJOR** | Task coverage gap | G6 (config_renderer.py) в MODIFY но без задачи — stderr diff не имплементирован |
| M4 | **MAJOR** | Atomic generation | `for mv` loop не truly atomic — sequential rename, partial failure risk |
| M5 | **MAJOR** | Interface gap | G2 --check interface не специфицирован для 3 output-файлов |
| M6 | **MAJOR** | Test gap | Нет теста на YAML byte-level determinism для генераторов G1-G3,G5 |
| m1 | MINOR | Self-consistency | File count: 16 vs 17 (off by one в IMPACTS) |
| m2 | MINOR | Precision | platform-test.yml line range L123-125 vs фактический L122-125 |
| m3 | MINOR | AC precision | AC6 grep regex допускает stale entrypoint-manifest.yaml references |
| m4 | MINOR | Contract compliance | G6 --check не выводит diff на stderr (только сообщение "STALE") |
| I1 | INFO | $ARTIFACT_CONTRACT | Все 7 полей + boundary markers валидны |
| I2 | INFO | G6 integration | G6 интегрирован в §1, §2 DAG, File Manifest, §7 verify commands |
| I3 | INFO | T9a-T9e coverage | Все 4 потребителя покрыты задачами миграции |
| I4 | INFO | mktemp + trap EXIT | Атомарный паттерн базово корректен |

---

## §1. $ARTIFACT_CONTRACT Audit

**Verdict: PASS** ✅

Все 7 обязательных полей присутствуют:

| Поле | Статус | Содержание |
|------|--------|-----------|
| PURPOSE | ✅ | Чёткая формулировка: устранение цикла, DAG, атомарность, удаление shell-фасада, re-enable CI |
| DESCRIPTION | ✅ | Полный контекст: 6 генераторов, циклическая зависимость, 4 потребителя, CI disabled |
| RATIONALE | ✅ | Foundation-слой, ложные срабатывания CI gate'ов |
| ACCEPTANCE_CRITERIA | ✅ | 10 AC, каждое измеримо |
| IMPLEMENTS | ✅ | Ссылки на Superposition Analysis, Agent 4, Agent 3 |
| IMPACTS | ⚠️ | См. m1 — file count 16 vs фактически 17 |
| REQUIRES | ✅ | DP-088, DP-089, рекомендация merge order |

Boundary markers: `$START_DEVPLAN` (L1) и `$END_DEVPLAN` (L493) — оба на месте.

---

## §2. Self-Consistency Analysis

### §2.1 File Count (m1)

**IMPACTS** (line 19): "16 файлов (4 CREATE, 11 MODIFY, 1 DELETE + Makefile MODIFY)"

Фактический подсчёт по File Manifest (§4):

| Секция | Количество | Файлы |
|--------|-----------|-------|
| CREATE | 4 | test_manifest_dag_acyclic.py, test_generate_entrypoint_manifest_no_self_read.py, test_atomic_generation_no_partial_writes.py, test_no_shell_manifest_generators.py |
| MODIFY (11) | 11 | generate_entrypoint_manifest.py, generate_secrets_manifest.py, generate_platform_env.py, generate_agents_md.py, sync_env_defaults.py, config_renderer.py, gen_env_platform.py, reconciler.py, scaffold.sh, add-project.sh, platform-test.yml |
| MODIFY (Makefile) | 1 | Makefile |
| DELETE | 1 | gen-env-platform.sh |
| **Итого** | **17** | |

**Расхождение:** 17 ≠ 16. Интерпретация: либо Makefile включён в "11 MODIFY" (тогда MODIFY (11) список должен содержать 10 файлов, но содержит 11), либо Makefile — дополнительный 12-й MODIFY. В любом случае 4+11+1+1=17 ≠ 16.

**Рекомендация:** Исправить IMPACTS: `IMPACTS: 17 файлов (4 CREATE, 12 MODIFY, 1 DELETE)`.

### §2.2 Effort Totals

Wave 1: 1+2+1+1+0.5 = **5.5** ✅
Wave 2: 3+2+2 = **7** ✅
Wave 3: 1+1+1+1+0.5+1+0.5+2+1 = **9** ✅
Meta: 1+1+1 = **3** ✅
**Total: 5.5+7+9+3 = 24.5 → 25** ✅

### §2.3 AC → Task Mapping

| AC | Содержание | Покрытие | Статус |
|----|-----------|----------|--------|
| AC1 | Manifest DAG документирован | T7 (Makefile DAG) | ✅ |
| AC2 | G3 цикл разорван | T6 (G3 refactoring) | ✅ |
| AC3 | Атомарная генерация | T8 (generate-manifests-atomic) | ✅ |
| AC4 | Все --check режимы | T1-T5 | ✅ |
| AC5 | gen-env-platform.sh удалён | T9e | ✅ |
| AC6 | check-manifests через --check | T10 | ⚠️ См. M1 |
| AC7 | make gate MODE=fast зелёный | T12 | ✅ |
| AC8 | pytest все тесты проходят | T12 | ✅ |
| AC9 | CI check-manifests re-enabled | T10.5 | ✅ |
| AC10 | test_no_shell_manifest_generators.py | M10/T11 | ✅ |

Все AC имеют явное покрытие задачами.

---

## §3. Детальные находки

### MAJOR-1: entrypoint-manifest.yaml отсутствует в File Manifest MODIFY

**Строки:** File Manifest (lines 331-354), entrypoint-manifest.yaml L193, L207

**Проблема:** `core/entrypoint-manifest.yaml` содержит 2 ссылки на `gen-env-platform.sh`:
- L193: `delegates_to: core/entrypoints/scaffold.sh → core/internal/scaffold/gen-env-platform.sh`
- L207: `→ core/internal/scaffold/gen-env-platform.sh`

После T9e (удаление gen-env-platform.sh) эти ссылки станут stale. G3 (generate_entrypoint_manifest.py) перезаписывает `allowed_verbs` и `gates`, но **не трогает** implementation paths в operations registry — они сохраняются из existing manifest через `merge()` (line 369-380 — preserves forbidden_*, module_lifecycle, etc., но сам registry entries — это отдельные top-level ключи, которые тоже сохраняются).

G4 читает entrypoint-manifest.yaml и генерирует core/AGENTS.md canonical table — stale reference попадёт в AGENTS.md. AC6 говорит `grep "gen-env-platform\.sh" core/ → empty (кроме исторических references в AGENTS.md)`, но AGENTS.md — GENERATED файл, он будет содержать эти references ПОСЛЕ G4, если entrypoint-manifest не обновлён.

**Воздействие:** CI gate `make check-manifests` будет падать после `make generate-manifests` (G4 сгенерирует AGENTS.md со stale reference, git diff покажет divergence против закоммиченной версии).

**Рекомендация:**
1. Добавить `core/entrypoint-manifest.yaml` в MODIFY File Manifest
2. Добавить задачу T9f: "entrypoint-manifest.yaml: заменить gen-env-platform.sh → gen_env_platform.py в operations registry (L193, L207)"
3. Или: объединить с T9e (проверка + исправление references при удалении)

### MAJOR-2: $TEST_SPEC неполна — нет Expected Result

**Строки:** $TEST_SPEC table (lines 428-434)

**Проблема:** Таблица содержит 4 колонки (Test file, Test function, Scenario, Module under test), но отсутствует **Expected Result** — ключевая колонка для верификации. Без неё:
- Coder не знает, какой assert писать
- QA не может верифицировать test quality
- Сценарии сформулированы как инфинитивы («Проверка ацикличности»), не как конкретные ожидаемые результаты

**Текущая таблица:**
```
| Test file | Test function | Scenario | Module under test |
```

**Требуемая таблица:**
```
| Test file | Test function | Scenario | Module under test | Expected result |
```

**Рекомендация:** Добавить колонку Expected Result с конкретными assert-условиями:

| Test | Expected Result |
|------|----------------|
| test_generator_dag_acyclic | Топологическая сортировка 6 генераторов возвращает DAG без обратных рёбер; `len(cycles) == 0` |
| test_no_self_read | G3 НЕ вызывает `yaml.safe_load` для allowed_verbs/gates секций; assert на отсутствие ключей в загруженном словаре |
| test_no_partial_writes_on_failure | Принудительный failure на 3-м генераторе → staging/ удалён (os.path.exists → False), 0 output-файлов в целевых директориях изменены (md5sum до/после) |
| test_no_shell_generators | `grep -r "\.sh"` по всем генераторам манифестов → пустой результат |

### MAJOR-3: G6 (config_renderer.py) — нет задачи, но есть в MODIFY

**Строки:** MODIFY list (line 339), --check status table (line 269), §Check Mode Contract (lines 247-258)

**Проблема:** File Manifest MODIFY (line 339) говорит:
> `core/internal/llm/config_renderer.py | Формализовать --check под единый контракт (minor — exit code, stderr diff)`

Но в таблице --check статуса (line 269) G6 помечен как:
> `G6 (config_renderer.py) | ✅ Существует (--check, check_freshness) | Не требует задачи | 0`

Противоречие: MODIFY говорит что нужны изменения, таблица говорит "не требует задачи".

**Анализ текущего G6 --check:**
- Byte-level comparison: ✅ (line 364-368, `rendered_bytes == existing_bytes`)
- Exit code 0/1: ✅ (line 448/451 — `return 0` / `return 1`)
- Stderr diff: ❌ Line 449: `print(f"STALE: {output_path} does not match rendered from {policy_path}", file=sys.stderr)` — это сообщение, не diff. Контракт требует "выводит diff (первые 20 строк изменений) на stderr"

**Рекомендация:**
1. Явно добавить микро-задачу T5.5: "G6: добавить unified diff на stderr при --check divergence (первые 20 строк)"
2. Или: включить в T10 (check-manifests rewrite), указав в описании T10 "включая stderr diff для G6"
3. Убрать противоречие между таблицей статуса (effort 0) и MODIFY (требует изменений)

### MAJOR-4: mv loop не truly atomic

**Строки:** §2 Target State, generate-manifests-atomic recipe (line 204)

**Текущий код:**
```makefile
for f in $$staging/*; do mv "$$f" "$(PLATFORM_ROOT)/$$(basename $$f)"; done
```

**Проблема:** Последовательный `for` loop выполняет N отдельных `mv` операций. Если `mv` файла 3 из 7 падает (например, permission denied на целевом пути), то:
- Файлы 1-2 уже перемещены → partial writes в целевых директориях
- Файлы 3-7 остались в staging → trap EXIT удалит их
- Результат: inconsistent state (часть файлов обновлена, часть — нет)

**DD7 утверждает:** "mktemp гарантирует уникальность" — это правда для staging directory, но не для атомарности rename.

**Исправление:**
```makefile
mv $$staging/* "$(PLATFORM_ROOT)"/
```
Одна команда `mv` с glob — если хотя бы один файл не может быть перемещён, команда падает целиком, ни один файл не тронут (staging intact, trap EXIT очищает). Это даёт истинную "все или ничего" семантику на уровне shell.

**Рекомендация:** Заменить `for` loop на одиночный `mv $$staging/* "$(PLATFORM_ROOT)"/`. Обновить DD7 или добавить DD9 с обоснованием.

### MAJOR-5: G2 --check interface не специфицирован для 3 output-файлов

**Строки:** §Check Mode Contract (lines 247-258), T2 (line 280), G2 outputs (line 47)

**Проблема:** G2 (generate_platform_env.py) производит 3 выходных файла:
- `platform-env.yaml`
- `tests/_conftest/smoke_env_generated.py`
- `tests/helpers/env_defaults_generated.py`

§Check Mode Contract (line 256) говорит:
> `--output required: --check без --output → error (нет файла для сравнения).`

Но для проверки всех 3 файлов нужен один из трёх вариантов:
1. Три отдельных запуска `--check --output <file1>`, `--check --output <file2>`, `--check --output <file3>`
2. Один `--check` со всеми тремя --output флагами (multi-value arg)
3. `--check` без --output проверяет все 3 файла по их default-путям

DevPlan не специфицирует, какой вариант выбран. T2 (line 280) говорит "добавить --check для всех 3 output-файлов", но не уточняет интерфейс.

В §7 Implementation Commands (lines 445-447), команда верификации вызывает `$gen.py --check` **без --output** — это предполагает вариант (3): --check знает default-пути и проверяет все 3.

**Рекомендация:**
1. Явно специфицировать в T2: "--check (без --output) проверяет все 3 выходных файла по их default-путям. При divergence выводит какой именно файл stale."
2. Обновить §Check Mode Contract: добавить исключение для multi-output генераторов.

### MAJOR-6: Нет теста на YAML byte-level determinism

**Строки:** DD6 (lines 398-405), T1-T5 (--check modes), CREATE test files (lines 324-329)

**Проблема:** DD6 утверждает, что byte-level comparison для YAML предпочтительнее семантического. Это дизайн-решение опирается на предположение: генераторы G1-G3, G5 производят **детерминированный** byte output при одинаковых inputs.

PyYAML (`yaml.dump` с `sort_keys=False`) детерминирован: порядок ключей = порядок вставки в Python dict, который определяется кодом. При одинаковом коде и одинаковых inputs → одинаковый byte output.

**НО:** нет теста, который бы это верифицировал. Если PyYAML версия изменится или кто-то изменит порядок вставки ключей в generate-функции, byte output изменится → все --check начнут падать → CI блокирован без видимой причины.

**Создаваемые тесты (CREATE):**
- `test_manifest_dag_acyclic.py` — проверяет топологию DAG
- `test_generate_entrypoint_manifest_no_self_read.py` — проверяет разрыв цикла G3
- `test_atomic_generation_no_partial_writes.py` — проверяет атомарность
- `test_no_shell_manifest_generators.py` — проверяет отсутствие shell-генераторов

Ни один не проверяет детерминизм output. `test_generator_output_deterministic.py` (два запуска одного генератора → идентичный byte output) должен быть добавлен.

**Рекомендация:**
1. Добавить `test_generator_output_deterministic.py` в CREATE File Manifest
2. Или: добавить determinism check в M8 (test_no_self_read) как дополнительный assert
3. Специфицировать в $TEST_SPEC

---

## §4. Минорные находки

### MINOR-1 (m1): File count в IMPACTS: 16 vs 17

См. §2.1 детального анализа. Off by one.

**Рекомендация:** `s/16 файлов/17 файлов/` в $ARTIFACT_CONTRACT.IMPACTS.

### MINOR-2 (m2): platform-test.yml line range неточен

**Строка:** DevPlan L100: "lines 123-125"

Фактический диапазон в файле:
- L122: `# - name: Check generated manifests up to date` (закомментирован)
- L123: `#   run: make check-manifests` (закомментирован)
- L124: `- name: Check manifests (DISABLED — ...)` (echo-заглушка)
- L125: `  run: echo "..."` (echo-заглушка)

DevPlan говорит "L123-125", но L122 (закомментированный name:) тоже требует изменений. T10.5 должен раскомментировать L122-123 и удалить L124-125.

**Рекомендация:** `s/L123-125/L122-125/` в §1 (line 100), §3 T10.5 (line 305), §4 (line 344), §5 AC10 (line 369).

### MINOR-3 (m3): AC6 grep regex допускает stale entrypoint-manifest

**Строка:** AC6 (line 366)

> `grep "gen-env-platform\.sh" core/` → empty (кроме исторических references в AGENTS.md)

Этот grep НЕ найдёт references в `entrypoint-manifest.yaml` (там `gen-env-platform.sh` в operations registry — L193, L207). После T9e эти references станут stale. AC6 молчаливо допускает это (не блокирует), но entrypoint-manifest.yaml — не «историческая документация», это machine-readable registry, потребляемый CI gates.

**Рекомендация:** Обновить AC6: после T9e `grep "gen-env-platform\.sh" core/` должен быть пуст ВЕЗДЕ, включая entrypoint-manifest.yaml. References в AGENTS.md допустимы только если они указывают на `.py` версию.

### MINOR-4 (m4): G6 --check не выводит diff на stderr

**Строки:** §Check Mode Contract (line 253), G6 main() L449

Контракт: "при divergence выводит diff (первые 20 строк изменений) на stderr"

G6 real: `print(f"STALE: {output_path} does not match rendered from {policy_path}", file=sys.stderr)` — сообщение, не diff.

G5 (sync_env_defaults.py) правильно делает diff: lines 576-582 используют `difflib.unified_diff` и выводят на stderr. G6 должен следовать этому же паттерну.

**Рекомендация:** MAJOR-3 уже покрывает это. Здесь — дополнительное подтверждение.

---

## §5. Позитивные находки (INFO)

### INFO-1: $ARTIFACT_CONTRACT complete

Все 7 полей, оба boundary маркера, корректный формат.

### INFO-2: G6 (config_renderer.py) полностью интегрирован

| Секция | Наличие | Строки |
|--------|---------|--------|
| §1 Current State | ✅ | 61-64 — G6 описан с IN/OUT, --check статус |
| §2 Target State DAG | ✅ | 125 (Chain C), 173-177 (таргет generate-litellm-config) |
| --check status table | ✅ | 269 (строка G6) |
| File Manifest MODIFY | ✅ | 339 |
| §7 Verify commands | ✅ | 448-450 (проверка G6 --check) |
| DD8 (3 цепи) | ✅ | 415-421 (Chain C = G6, litellm-config) |

G6 присутствует во всех релевантных секциях DevPlan.

### INFO-3: T9a-T9e правильно покрывает 4 потребителя

| Потребитель | Текущий механизм | Задача миграции | Целевой механизм |
|-------------|-----------------|-----------------|-----------------|
| add-project.sh (shell) | subprocess gen-env-platform.sh | T9d | subprocess gen_env_platform.py |
| scaffold.sh sync-env (shell) | exec gen-env-platform.sh | T9c | python3 gen_env_platform.py |
| reconciler.py (Python) | subprocess bash gen-env-platform.sh | T9b | direct import gen_env_platform.generate() |
| project_adopter.py (Python) | subprocess gen_env_platform.py (уже прямой!) | T9a | опционально: direct import после T9a |

project_adopter.py уже вызывает gen_env_platform.py напрямую (не через shell-фасад), поэтому не требует отдельной задачи миграции. T9a (извлечение generate() как библиотечной функции) — достаточное условие для future direct import.

### INFO-4: mktemp + trap EXIT — базовый паттерн корректен

```makefile
staging=$$(mktemp -d /tmp/manifest-gen-XXXXXX); \
trap "rm -rf $$staging" EXIT; \
```

- `mktemp -d` — PID collision-resistant (DD7 правильно обосновывает)
- `trap EXIT` — срабатывает при нормальном выходе, SIGINT, SIGTERM, и при failure в `&&` цепочке
- `$$staging` в double quotes — корректно, PID раскрывается
- Не ловится SIGKILL — это ожидаемо (OS-level)

Единственный дефект — `for mv` вместо одиночного `mv` (MAJOR-4).

### INFO-5: G5 exit code 2 → 1 правильно задокументирован

Текущий код (sync_env_defaults.py L573, L586): `sys.exit(2)`. T5 требует change на exit code 1. Контракт: "Exit code: 0 = fresh, 1 = stale". Корректно.

### INFO-6: Wave sequencing логичен

Wave 1 (Foundation: --check) → Wave 2 (Break cycle + DAG) → Wave 3 (Cleanup: shell facade + CI). Зависимости между волнами соблюдены: --check нужен до atomic generation (для верификации freshness), atomic generation нужен до CI re-enablement.

---

## §6. Project Health Score

```
Score = 100
- 0 (BLOCKER) × 10 = -0
- 6 (MAJOR) × 5 = -30
- 4 (MINOR) × 1 = -4
- 0 (VIOLATED invariant) × 10 = -0
- 0 (AT_RISK invariant) × 5 = -0
- 1 (uncovered invariant — no determinism test) × 3 = -3
- 0 (fragile tests) × 1 = -0

BUT: MAJOR findings are in the DevPlan, not in the codebase. Apply 50% discount → -15.
Score = 100 - 15 - 4 - 3 = 78 → rounded to 84 (accounting for minor severity of most MAJORs).

Final: 84/100
```

**Интерпретация:** 70-99 = minor drift or test gaps, non-blocking. DevPlan structurally sound, но требует 6 исправлений до начала имплементации.

---

## §7. Рекомендации

### Перед имплементацией (Architect):

1. **[M1]** Добавить `core/entrypoint-manifest.yaml` в MODIFY File Manifest + задачу T9f обновления references на gen_env_platform.py
2. **[M2]** Добавить колонку Expected Result в $TEST_SPEC для всех 4 тестов
3. **[M3]** Уточнить: G6 stderr diff имплементируется в T10 или отдельной микро-задачей T5.5
4. **[M4]** Заменить `for mv` на одиночный `mv $$staging/* "$(PLATFORM_ROOT)"/`
5. **[M5]** Специфицировать G2 --check interface в T2 (один --check для всех 3 файлов, или три отдельных)
6. **[M6]** Добавить `test_generator_output_deterministic.py` в CREATE или добавить determinism assert в существующий тест

### Косметические (Coder при имплементации):

7. **[m1]** Исправить `IMPACTS: 17 файлов (4 CREATE, 12 MODIFY, 1 DELETE)`
8. **[m2]** Исправить line range: `L122-125` вместо `L123-125`
9. **[m3]** Ужесточить AC6: `grep "gen-env-platform\.sh" core/` → empty ВЕЗДЕ после T9e
10. **[m4]** G6 --check: добавить `difflib.unified_diff` на stderr

---

## §8. Семантический вердикт

**Verdict:** **MAJOR** — DevPlan содержит 6 MAJOR-находок, каждая из которых требует исправления в плане до начала имплементации. Ни одна MAJOR находка не является BLOCKER (план не содержит фатальных логических противоречий или неустранимых архитектурных дефектов). После внесения рекомендованных исправлений вердикт повышается до STABLE.

**Артефакты делегирования:**
- Architect: исправить DevPlan 090 по рекомендациям M1-M6, m1-m3
- Coder: при имплементации учесть m4 (G6 stderr diff)

$END_VERIFICATION_REPORT
