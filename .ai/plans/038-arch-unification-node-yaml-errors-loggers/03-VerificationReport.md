$START_VERIFICATION_REPORT

# VerificationReport 03 — DevPlan 038a Pre-Implementation Audit

🔒 Verified against SHA `d6ba7d6c4d1f4ac5b7cbd9ec5bf492a4351c1b89`

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Pre-implementation semantic QA of DevPlan 038a (Wave 1: Unified NodeYaml Facade + Typed Exceptions) — проверка статической полноты, cross-file/cross-plan drift, инвариантов, API-контракта с 038b/038c, тестовой спецификации, графа зависимостей |
| **DESCRIPTION** | Полный 6-фазный аудит: статический анализ DevPlan 038a, верификация всех 33+ файловых путей на файловой системе, сравнение API-контракта с зависимыми планами 038b/038c, проверка архитектурных инвариантов, анализ тестовой спецификации на honesty rules, проверка графа задач на цикличность |
| **RATIONALE** | DevPlan 038a — фундамент для 038b (W2+W3+W4) и 038c (W5). Ошибки в API-контракте 038a каскадно блокируют реализацию зависимых волн. Cross-plan drift detection критичен для предотвращения ситуации, когда Coder 038c не может реализовать задачи из-за отсутствующих CLI-флагов |
| **ACCEPTANCE_CRITERIA** | Все CRITICAL drift зафиксированы с точными ссылками на строки. Предложена делегация в Architect для исправления |
| **IMPLEMENTS** | QA pre-implementation gate для DevPlan 038a + cross-plan audit 038a→038b→038c |
| **IMPACTS** | `.ai/plans/038-arch-unification-node-yaml-errors-loggers/` |
| **REQUIRES** | DevPlan 038a (038a-DevPlan.md), Brief 038a (038a-Brief.md), Brief 038b, DevPlan 038b, Brief 038c, DevPlan 038c, parent DevPlan 038 (02-DevPlan.md), parent VerificationReport (02-VerificationReport.md), filesystem state at SHA d6ba7d6 |

---

## Phase 1 — Static Audit (DevPlan 038a completeness)

### Compliance Matrix

| Check | Status | Evidence |
|-------|--------|----------|
| $START_DEVPLAN / $END_DEVPLAN | ✅ PASS | Lines 1, 920 |
| $ARTIFACT_CONTRACT (7 fields) | ✅ PASS | Lines 5-15: PURPOSE, DESCRIPTION, RATIONALE, ACCEPTANCE_CRITERIA, IMPLEMENTS, IMPACTS, REQUIRES — все 7 |
| IMPLEMENTS → Brief 038a | ✅ PASS | "Brief 038a — Wave 1: Unified NodeYaml Facade + Typed Exceptions" — точное совпадение |
| DD1 @rationale | ✅ PASS | Line 23-25 |
| DD2 @rationale | ✅ PASS | Line 29-31 |
| DD3 @rationale | ✅ PASS | Line 35-37 |
| DD4 @rationale | ✅ PASS | Line 41-46 |
| File Manifest completeness | ✅ PASS | 3 новых + 26 Python + ~10 shell = ~39, все с путями |
| Acceptance Criteria (5) | ✅ PASS | AC1-AC5, lines 12 |
| Risk Matrix | ✅ PASS | R1-R6, lines 876-884 |
| Migration Guide (5 patterns) | ✅ PASS | Patterns 1-5, lines 452-556 |
| Test spec | ✅ PASS | 21 NodeYaml + 7 CLI + 4 exceptions = 32 тестов |
| Task decomposition (T1.1-T1.9) | ✅ PASS | Каждая задача с AC, зависимостями, файлами |
| Implementation Sequence | ✅ PASS | DAG, lines 837-862 |
| Rollback Plan | ✅ PASS | Lines 888-898, включая частичный откат |
| Verification Checklist | ✅ PASS | 13 пунктов, lines 906-918 |

### Structural findings

| # | Severity | Check | Detail |
|---|----------|-------|--------|
| S1 | **LOW** | AC1 naming | Brief 038a AC1 (line 12) says `NodeYaml.load` as the location of `yaml.safe_load`, but the actual internal method is `NodeYaml._load()` (private). `NodeYaml.load()` is a public wrapper that delegates to `_load()`. The grep-based check `grep 'yaml.safe_load' core/internal/` finds the file, not the function — semantics not affected. |
| S2 | INFO | UNDER-PROMISE | DevPlan claims "20+ unit-тестов" (AC5, line 12), actual spec has 32 тестов (21 NodeYaml + 7 CLI + 4 exceptions). Exceeds minimum. |

---

## Phase 2 — Cross-File Drift Detection

### File Path Verification

Все 33 файловых пути из DevPlan 038a + Brief 038a проверены на файловой системе:

| # | Путь | Статус | Примечание |
|---|------|--------|------------|
| 1 | `core/internal/shared/exceptions.py` | ❌ MISSING | **Ожидаемо** — NEW, создаётся в T1.1 |
| 2 | `core/internal/shared/node_yaml.py` | ✅ EXISTS | 67 строк, будет расширен |
| 3-26 | 24 Python-файла в `core/internal/` | ✅ EXISTS | Все 24 файла подтверждены (lifecycle/, deploy/, converge/, scaffold/, healthcheck/, llm/, scripts/, modules/) |
| 27 | `core/lib/yaml_read.sh` | ✅ EXISTS | |
| 28 | `core/lib/node-resolver.sh` | ✅ EXISTS | |
| 29-33 | 5 shell-файлов (scaffold/, verify/, catalog/, postgres/, bootstrap/, validate/) | ✅ EXISTS | Все shell-файлы подтверждены |

**Итого: 32/33 существующих файлов подтверждены. 1 файл (exceptions.py) ожидаемо отсутствует (NEW). 0 неверных путей.**

Сравнение с parent 02-VerificationReport.md (DRIFT-1: 8 path mismatches): **ВСЕ 8 проблем исправлены.** DevPlan 038a использует актуальные пути post-DevPlan 079 (lifecycle/, deploy/, converge/ поддиректории).

### DRIFT-CP-1: Cross-plan CLI flag `--json-output` — CRITICAL

| Параметр | Значение |
|----------|---------|
| **DRIFT-ID** | DRIFT-CP-1 |
| **Severity** | **CRITICAL** |
| **Тип** | Cross-plan API contract violation |
| **Источник** | 038c-Brief.md:15, 038c-DevPlan.md:68,98,144,148,233,236,364,367,490,495,497,499,507,674,711,715,755 |
| **Описание** | CLI-флаг `--json-output` заявлен как часть API NodeYaml фасада в 038c (22 упоминания), но **отсутствует** в спецификации CLI 038a-DevPlan.md (строки 414-424). 038a определяет только флаги: `--file`, `--get`, `--default`, `--items`, `--domain-config`, `--context`, `--validate`. |
| **Impact** | 038c не сможет реализовать задачи #5 (validate.sh YAML→JSON), #14 (adopt-project.sh YAML→JSON), и другие, зависящие от `--json-output`. Блокирует закрытие AC7 (DevPlan 038). |
| **Fix** | Добавить `--json-output` (action="store_true", выводит весь YAML-документ как JSON) в CLI-спецификацию 038a-DevPlan.md строка 414-424 и в реализацию T1.3. Альтернативно: заменить все ссылки в 038c на `--get . --items` (весь документ как JSON array) или использовать `--json-output` как синоним для вывода всего документа. Рекомендация: добавить флаг `--json-output` в 038a — это минимальное изменение API. |

### DRIFT-CP-2: Cross-plan CLI flag `--find-project <name>` — CRITICAL

| Параметр | Значение |
|----------|---------|
| **DRIFT-ID** | DRIFT-CP-2 |
| **Severity** | **CRITICAL** |
| **Тип** | Cross-plan API contract violation |
| **Источник** | 038c-Brief.md:99, 038c-DevPlan.md:101,115,147,340,343,674,714,755 |
| **Описание** | CLI-флаг `--find-project <name>` заявлен как часть API NodeYaml фасада в 038c (9 упоминаний), но **отсутствует** в спецификации CLI 038a-DevPlan.md. Флаг должен искать проект по имени, выводить JSON + org + host, exit code 0/1. |
| **Impact** | 038c не сможет реализовать задачу #6 (remove-project.sh project lookup). Блокирует закрытие AC7. |
| **Fix** | Добавить `--find-project <name>` в CLI-спецификацию 038a-DevPlan.md (строка 414-424) и в реализацию T1.3. Метод должен искать проект в `node.get_projects()` по полю `name`, выводить JSON с полями проекта + `___ORG___<org>` + `___HOST___<host>` (формат, совместимый с существующим remove-project.sh). Exit code: 0=найдено, 1=не найдено. |

### DRIFT-CP-3: `extract_context_from_node_yaml` consumers count — MEDIUM

| Параметр | Значение |
|----------|---------|
| **DRIFT-ID** | DRIFT-CP-3 |
| **Severity** | MEDIUM |
| **Тип** | Factual inaccuracy |
| **Источник** | 038a-DevPlan.md:882 (Risk R3) |
| **Описание** | Risk R3 утверждает: "`extract_context_from_node_yaml` consumers (3 файла) не обновлены на новый API". Фактический grep по кодовой базе показывает **1 файл** (context_deployer.py, 2 call sites на строках 710, 851). state_machine.py и steps.py НЕ используют extract_context_from_node_yaml — они используют прямой `yaml.safe_load`. |
| **Impact** | Завышенная оценка risk surface. Mitigation (DeprecationWarning) всё ещё корректен, но количество затронутых файлов неверно. |
| **Fix** | Исправить Risk R3: "consumers (1 файл, context_deployer.py — 2 call sites)". |

### DRIFT-CP-4: `get_repo_url()` API contraction — LOW

| Параметр | Значение |
|----------|---------|
| **DRIFT-ID** | DRIFT-CP-4 |
| **Severity** | LOW |
| **Тип** | API contraction from parent plan |
| **Источник** | 02-DevPlan.md:342 vs 038a-DevPlan.md API methods |
| **Описание** | Родительский 02-DevPlan.md определяет метод `.get_repo_url() → str` в API NodeYaml (строка 342). 038a-DevPlan.md **не включает** этот метод в список из 11 методов. Ни 038b, ни 038c не ссылаются на `get_repo_url()`. |
| **Impact** | Если будущая волна потребует `get_repo_url()`, придётся расширять API 038a. Текущие зависимые планы (038b, 038c) не используют этот метод — риск низкий. |
| **Fix** | Опционально: добавить `get_repo_url()` в API 038a для forward-compatibility, либо явно пометить как DEFERRED в секции Scope Out. |

### DRIFT-CP-5: `docker_orchestrator.py` conditional unresolved — LOW

| Параметр | Значение |
|----------|---------|
| **DRIFT-ID** | DRIFT-CP-5 |
| **Severity** | LOW |
| **Тип** | Unresearched conditional |
| **Источник** | 038a-DevPlan.md:692 (Batch F) |
| **Описание** | T1.5 Batch F условно включает `docker_orchestrator.py` с пометкой "(если есть yaml.safe_load)". Фактическая проверка: `core/internal/bootstrap/deploy/docker_orchestrator.py` **не содержит** `yaml.safe_load`. Файл можно исключить из Batch F. |
| **Impact** | Coder потратит время на проверку файла, который не требует миграции. |
| **Fix** | Исключить `docker_orchestrator.py` из Batch F. Заменить на точный список: только `reconciler.py` + оставшиеся файлы без условия. |

---

## Phase 3 — Invariant Verification

Проверка DevPlan 038a против архитектурных инвариантов из root `AGENTS.md`:

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Makefile — единый фасад | ✅ HELD | 038a не добавляет новых make-таргетов. T1.9 добавляет entrypoint в `entrypoint-manifest.yaml` для `python3 -m` режима — соответствует контракту. |
| 2 | Модель деплоя (git push → CI) | ✅ HELD | Изменения — internal refactoring, не затрагивают deploy model. |
| 3 | org = context | ✅ HELD | `get_context()` сохраняет существующую логику извлечения контекста. |
| 4 | AGENTS.md канонические файлы | ✅ HELD | Не затрагивает. |
| 5 | core/entrypoint-manifest.yaml | ✅ HELD | T1.9 (CI Gate Impact) добавляет entrypoint для `node_yaml.py:CLI` — соответствует инварианту. |
| 6 | make bootstrap-node идемпотентный | ✅ HELD | Не затрагивает. |
| 7 | Полный локальный стек через docker compose up | ✅ HELD | Не затрагивает. |
| 8 | LiteLLM — PostgreSQL | ✅ HELD | Не затрагивает. |
| 9 | Тестовый сервер пересоздаваем | ✅ HELD | Не затрагивает. |
| 10 | hermes-build-platform/context | ✅ HELD | Не затрагивает. |
| 11 | Manifest Generation Contract | ✅ HELD | `exceptions.py` (новый) и `node_yaml.py` (расширение) не конфликтуют с generated files. |
| **Языковая политика** | Python-first, Strangler-Fig | ✅ HELD | Новый код — Python (`NodeYaml` класс, `exceptions.py`). Shell-миграция (T1.6-T1.8) заменяет inline python3 на CLI-вызовы, что УСИЛИВАЕТ политику. |

**Вывод:** Все 11 архитектурных инвариантов + языковая политика соблюдены. 0 нарушений.

---

## Phase 4 — API Contract Consistency

### Cross-plan API matrix (038a → 038b → 038c)

| API Surface | 038a (source) | 038b (consumer) | 038c (consumer) | Status |
|-------------|---------------|-----------------|-----------------|--------|
| `exceptions.py` (5 классов) | ✅ Defined | ✅ REQUIRES (line 15) | N/A | ✅ CONSISTENT |
| `--file` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--get <key>` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--domain-config` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--validate` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--items` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--context` | ✅ Defined | N/A | N/A (038c doesn't use) | ✅ CONSISTENT |
| `--default <val>` | ✅ Defined | N/A | ✅ Uses | ✅ CONSISTENT |
| `--json-output` | ❌ NOT DEFINED | N/A | ✅ REQUIRES (22 refs) | ❌ DRIFT-CP-1 |
| `--find-project <name>` | ❌ NOT DEFINED | N/A | ✅ REQUIRES (9 refs) | ❌ DRIFT-CP-2 |
| `NodeYaml(path).load()` | ✅ Defined | Implicit (via 038a) | N/A | ✅ CONSISTENT |
| `NodeYaml(path).get()` | ✅ Defined | Implicit | N/A | ✅ CONSISTENT |
| `NodeYaml(path).get_context()` | ✅ Defined | Implicit | N/A | ✅ CONSISTENT |
| `get_repo_url()` | ❌ NOT DEFINED | N/A | N/A | ⚠️ DRIFT-CP-4 |

### Exit Code Consistency

| Exit Code | 038a CLI spec (line 429-436) | 038a Exception classes (line 338-362) | 038c expected (line 99) | Status |
|-----------|------------------------------|---------------------------------------|------------------------|--------|
| 0 | Success | N/A | Success | ✅ |
| 1 | Generic PlatformError | PlatformError.exit_code=1 | Not found (project) | ⚠️ **SEMANTIC MISMATCH** |
| 2 | ConfigNotFoundError | ConfigNotFoundError.exit_code=2 | File not found | ✅ |
| 3 | ConfigParseError | ConfigParseError.exit_code=3 | Parse error | ✅ |
| 4 | ConfigValidationError | ConfigValidationError.exit_code=4 | N/A | ✅ |
| 10 | PlatformFatalError | PlatformFatalError.exit_code=10 | N/A | ✅ |

**Exit code 1 MISMATCH:** 038c ожидает exit code 1 = "not found" (для `--find-project`), но 038a определяет exit code 1 = "Generic PlatformError (unexpected)". Для `--find-project` "project not found" — это **ожидаемое** поведение, не ошибка платформы. Рекомендация: использовать exit code 0 для "project not found" (как `grep` — не найдено = не ошибка) и выводить пустой вывод, либо выделить отдельный exit code (например, 5) для семантики "not found".

### NamedTuple consistency

| NamedTuple | 038a Definition (line 296-308) | Parent 02-DevPlan (line 350-360) | Status |
|------------|-------------------------------|----------------------------------|--------|
| `DomainConfig` | platform_domain, email, acme_dns_plugin, project_domains | platform_domain, email, acme_dns_plugin, project_domains | ✅ CONSISTENT |
| `NodeInfo` | fqdn, owner_key, docker_mirror | fqdn, owner_key, docker_mirror | ✅ CONSISTENT |

---

## Phase 5 — Test Spec Audit

### Test Count

| Категория | Заявлено | В спецификации | Статус |
|-----------|---------|----------------|--------|
| NodeYaml unit | 20+ ("20+") | 21 тест | ✅ EXCEEDS |
| CLI tests | — | 7 тестов | ✅ |
| Exceptions | 3+ ("3+") | 4 теста | ✅ EXCEEDS |
| **Всего** | **20+** | **32 теста** | ✅ |

### Test Data Fixtures

| Fixture | Coverage |
|---------|----------|
| `node_yaml_valid.yaml` | Полный валидный документ: node, context, domain, 2 projects, 2 modules |
| `node_yaml_contexts.yaml` | Array fallback test (contexts[0].name) |
| `node_yaml_invalid.yaml` | Malformed: projects is string, not list |

✅ Фикстуры покрывают: context string, context array fallback, валидные данные, malformed данные, empty/missing keys. **Gap:** отсутствует фикстура для `node.yaml` с `null` root (test #6 ожидает `null` YAML → `{}`).

### Test Honesty Rules (R1-R5) Prospective Check

| Rule | Ожидаемый статус | Обоснование |
|------|-----------------|-------------|
| R1 (NO pass-tests) | ✅ PASS | Все 32 теста имеют assert'ы: assertEqual, assertRaises, assert isinstance |
| R2 (NO unfalsifiable) | ✅ PASS | Нет assert'ов на language guarantees (нет `assert isinstance(x, object)`) |
| R3 (STALE SKIP) | ✅ PASS | Новые тесты — нет skip-маркеров. В существующих тестах: 10 `@pytest.mark.skipif` (условные, не stale) |
| R4 (NO_SERVICE = FAIL) | ✅ PASS | Новые тесты используют `tmp_path`, не требуют внешних сервисов |
| R5 (ANTI-SURVIVORSHIP) | N/A | Не применимо к новым тестам (нет bug ID references) |

### Test Design Gaps

| # | Gap | Severity | Описание |
|---|-----|----------|----------|
| TG1 | Integration test: shell→CLI | MEDIUM | Ни один тест не проверяет, что shell-скрипты после миграции (T1.7) корректно вызывают CLI фасада и парсят вывод. Покрывается AC8/AC10 косвенно, но желателен smoke test. |
| TG2 | `reload()` race condition | LOW | Тест #8 (`test_reload_invalidates_cache`) проверяет базовый reload, но не проверяет сценарий: внешнее изменение файла между конструктором и первым `.get()` (Risk R5). Тест мог бы покрыть: создать NodeYaml, изменить файл через `tmp_path`, вызвать `.get()` — ожидается старые данные (кэш), затем `.reload()` — новые данные. |
| TG3 | `null` YAML fixture | LOW | Тест #6 ожидает `null` YAML → `{}`, но фикстура `node_yaml_valid.yaml` не содержит null-root варианта. Нужен отдельный файл `node_yaml_null.yaml` с содержимым `null`. |

---

## Phase 6 — Task Dependency Graph

### DAG Analysis

```
T1.1 (exceptions.py)           ← Нет зависимостей
  │
  ▼
T1.2 (NodeYaml class)         ← Зависит от T1.1 ✓
  │
  ▼
T1.3 (CLI)                    ← Зависит от T1.2 ✓
  │
  ▼
T1.4 (unit tests)            ← Зависит от T1.3 ✓
  │
  ├──────────────────────┐
  ▼                      ▼
T1.5 (Python consumers)   T1.6 (yaml_read.sh)    ← Оба зависят от T1.4, параллельны ✓
  │                         │
  │                         ▼
  │                       T1.7 (shell consumers) ← Зависит от T1.6 ✓
  │                         │
  │                         ▼
  │                       T1.8 (NODE_YAML_PATH)  ← Зависит от T1.5 ✓
  │                         │
  └────────┬────────────────┘
           ▼
       T1.9 (gate verification) ← Зависит от T1.5-T1.8 ✓
```

- **Циклы:** 0 (DAG валиден)
- **Transitive зависимости:** корректны (T1.7→T1.6→T1.3→T1.2→T1.1)
- **Параллелизм:** T1.5 (Batch A+B) и T1.6 могут выполняться параллельно — корректно
- **Критический путь:** T1.1→T1.2→T1.3→T1.4→T1.5 (Batch C→D→E→F)→T1.9 = 9 tasks
- **Общее количество задач:** 9 (T1.1–T1.9)

### Batch Order Validation (T1.5)

| Batch | Файлы | Причина порядка | Валидация |
|-------|-------|-----------------|-----------|
| Batch A (4) | scaffold | Наименьший risk surface | ✅ Корректно — scaffold не участвует в CI |
| Batch B (4) | healthcheck/status-page | Read-only consumers | ✅ Корректно — не изменяют состояние |
| Batch C (4) | shared libs | Много downstream consumers | ✅ Корректно —隔离 risk |
| Batch D (6) | bootstrap helpers | Участвуют в CI | ✅ Корректно — после стабилизации API |
| Batch E (5) | bootstrap core | CRITICAL | ✅ Корректно — максимальная осторожность |
| Batch F (3→2) | converge | Финал | ⚠️ Batch F: содержит неопределённость (`docker_orchestrator.py` если есть), см. DRIFT-CP-5 |

---

## Parent VerificationReport Cross-Check

Сравнение с `02-VerificationReport.md` (parent audit of DevPlan 038):

| Parent Finding | Severity | Status in 038a |
|---------------|----------|----------------|
| S1 — Missing Brief.md | CRITICAL | ✅ **FIXED**: `01-Brief.md` создан. `038a-Brief.md` существует |
| DRIFT-1 — 8 path mismatches | CRITICAL | ✅ **FIXED**: Все 33+ путей в 038a используют актуальные поддиректории (lifecycle/, deploy/, converge/) |
| DRIFT-2 — Internal inconsistency (W3 vs P1.1) | HIGH | ✅ **FIXED**: 038a не имеет разночтений — все секции консистентны |
| S2 — Non-standard section tags | WARNING | ✅ **FIXED**: 038a использует стандартные теги ($START_DEVPLAN, $ARTIFACT_CONTRACT) |
| S3 — REQUIRES annotations | INFO | ✅ **FIXED**: REQUIRES field явно указывает статус: "DevPlan 070/079 — COMPLETED" |
| DRIFT-3 — Line numbers ±1 | MINOR | ✅ N/A — 038a не ссылается на номера строк старого кода |
| DRIFT-4 — Debt line 15 vs 11 | MINOR | ✅ N/A |
| DRIFT-5 — except count 94 vs 91 | MINOR | ✅ N/A |

**Все CRITICAL и HIGH findings из parent VerificationReport ИСПРАВЛЕНЫ в 038a.** Однако 038a вводит новые cross-plan drift (DRIFT-CP-1, DRIFT-CP-2) с зависимыми планами 038b/038c.

---

## Summary

### Findings by Severity

| Severity | Count | IDs |
|----------|-------|-----|
| **CRITICAL** | 2 | DRIFT-CP-1 (`--json-output` flag), DRIFT-CP-2 (`--find-project` flag) |
| **HIGH** | 0 | — |
| **MEDIUM** | 1 | DRIFT-CP-3 (consumers count: 3→1) |
| **LOW** | 3 | S1 (AC1 naming), DRIFT-CP-4 (get_repo_url contraction), DRIFT-CP-5 (docker_orchestrator conditional) |
| **WARNING** | 3 | TG1 (no integration test), TG2 (reload race test), TG3 (null YAML fixture missing) |
| **INFO** | 2 | S2 (under-promise 20+→32), exit code 1 semantic mismatch |

### Blocking Issues

| # | Blocker | Обоснование |
|---|---------|-------------|
| 1 | **DRIFT-CP-1** | `--json-output` флаг требуется 038c для 4+ задач, но не определён в API 038a. Без него 038c не может быть реализован. |
| 2 | **DRIFT-CP-2** | `--find-project <name>` флаг требуется 038c для задачи #6 (remove-project.sh), но не определён в API 038a. Без него 038c не может быть реализован. |

### Semantic Verdict

**DRIFTED (CRITICAL)**

DevPlan 038a имеет отличное качество внутренней консистентности: все файловые пути верифицированы (0 ошибок), все инварианты AGENTS.md соблюдены, граф задач — валидный DAG, тестовая спецификация избыточна (32 vs 20+). Однако обнаружен CRITICAL cross-plan drift: два CLI-флага (`--json-output`, `--find-project`), требуемые зависимым планом 038c, отсутствуют в API-спецификации 038a. Если 038a будет реализован как спроектировано, 038c не сможет выполнить свои задачи.

**Рекомендация:** НЕ начинать реализацию 038a без добавления `--json-output` и `--find-project` в API-спецификацию. Делегировать в Architect для обновления DevPlan 038a (CLI-секция) или 038c (замена на существующие флаги).

---

## Proposed Delegation

```text
task(subagent_type="Architect",
     description="Fix 038a/038c cross-plan CLI flag drift",
     prompt="Review VerificationReport 03 at .ai/plans/038-arch-unification-node-yaml-errors-loggers/03-VerificationReport.md.

CRITICAL cross-plan drift to fix in DevPlan 038a and/or 038c:

1. [DRIFT-CP-1] Add --json-output flag to 038a CLI specification (038a-DevPlan.md lines 414-424):
   - Flag: --json-output (action="store_true")
   - Behavior: outputs entire YAML document as JSON (equivalent to yaml.safe_load + json.dumps)
   - Without --get: outputs whole doc
   - With --get: outputs specific key as JSON (existing --items behavior for lists)
   - Exit code: 0=success, 2=file not found, 3=parse error
   Update 22 references in 038c if semantics differ.

2. [DRIFT-CP-2] Add --find-project <name> flag to 038a CLI specification:
   - Flag: --find-project NAME
   - Behavior: searches projects array for matching 'name' field
   - Output: JSON project dict + ___ORG___<org> + ___HOST___<host> (format matching existing remove-project.sh lines 169-171)
   - Exit code: 0=found, 1=not found
   Update 9 references in 038c if semantics differ.

3. [DRIFT-CP-3] Fix Risk R3 in 038a-DevPlan.md line 882:
   - Change 'consumers (3 файла)' to 'consumers (1 файл, context_deployer.py — 2 call sites)'

4. [EXIT-CODE] Resolve exit code 1 semantic mismatch:
   - 038a defines code 1 = 'Generic PlatformError (unexpected)'
   - 038c expects code 1 = 'not found'
   - Recommendation: --find-project returns 0 with empty output when not found (grep semantics),
     OR use exit code 5 for 'not found' to avoid collision with PlatformError

5. [DRIFT-CP-4] Optional: either add get_repo_url() to 038a API or mark as DEFERRED in Scope Out

6. [DRIFT-CP-5] Remove docker_orchestrator.py conditional from Batch F (file has no yaml.safe_load)

After fixing, verify with:
- grep 'json-output' in 038a-DevPlan.md → should find the new flag definition
- grep 'find-project' in 038a-DevPlan.md → should find the new flag definition
- grep '3 файла' in 038a-DevPlan.md → should be '1 файл'")
```

$END_VERIFICATION_REPORT
