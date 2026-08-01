# 07-DevPlan — Бриф F: Test Honesty

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 46–50 программного брифа 117 — Test Honesty: R4 enforcement (skip→fail), легитимизация sys.path.insert, R5 negative-скан, LDD-выборка IMP:9, синхронизация test_inventory.
- DESCRIPTION: 5 задач: (46) R4 NO_SERVICE=FAIL — замена skip на fail через honesty-механизм с поэтапным переходом marker→xfail→fail, (47) 65× sys.path.insert — conftest-хук + политика, (48) R5 ANTI-SURVIVORSHIP — методика парного negative-скана + создание негативных тестов для ключевых гейтов, (49) LDD IMP:9 — аудит покрытия unit/gates + добавление трасс где отсутствуют, (50) test_inventory_changes.yaml — синхронизация + устранение 2-entry расхождения.
- RATIONALE: Test Honesty R4 — фундаментальный инвариант test suite: skip при недоступности сервиса маскирует реальные проблемы и создаёт ложное ощущение зелёного CI. Метрика «14+ skips» из брифа оказалась заниженной (реально ~25+). Поэтапный переход (marker→xfail→fail) защищает CI от мгновенного красного gate при недоступности Docker на staging-раннерах. R5 (negative-тесты) — второе плечо Test Honesty, предотвращает регрессию детекторов. LDD IMP:9 и inventory sync — гигиена, завершающая волну.
- ACCEPTANCE_CRITERIA:
  - AC-F1: все @pytest.mark.skipif с причиной «docker CLI not available» → require_docker_or_fail (6 шт, test_hermes_l2_fallback.py + test_predeploy_gate.py).
  - AC-F2: inline pytest.skip с причинами «Port not reachable» / «container not accessible» → require_docker_or_fail ИЛИ require_service_healthy (≥10 шт, test_local_auth.py, test_smoke_langfuse.py, test_platform_endpoints.py).
  - AC-F3: CI gate `make gate MODE=fast` зелёный; при REQUIRE_HONESTY_MODE=marker (текущий) — поведение идентично; при =fail — честный fail при отсутствии Docker.
  - AC-F4: tests/conftest.py добавляет общие корневые пути (core/, core/internal/) через site.addsitedir; policy-раздел в tests/AGENTS.md легитимизирует sys.path.insert для module-specific путей. Новые тесты используют только легитимизированный шаблон.
  - AC-F5: методика R5-скана описана в tests/AGENTS.md; созданы negative-тесты для ключевых гейтов с bug-ссылками: R1 no-pass-tests, audit-format R2, debt-freshness, cross-layer-imports — минимум 3 гейта с R5-покрытием.
  - AC-F6: LDD-аудит: все unit-тесты (<300 LOC) имеют ≥1 IMP:9 лог; gates имеют IMP:9 в каждой тестовой функции.
  - AC-F7: test_inventory.yaml заголовок совпадает с реальным числом тестов; `python tests/tools/sync_inventory.py` — idempotent (0 diff).
  - AC-F8: `make gate MODE=fast`, `make check-manifests` зелёные.
- IMPLEMENTS: 117 01-Brief задачи 46–50.
- IMPACTS: tests/_conftest/honesty.py (новый require_service_healthy), tests/test_local_auth.py, tests/test_smoke_langfuse.py, tests/test_platform_endpoints.py, tests/test_hermes_l2_fallback.py, tests/test_predeploy_gate.py, tests/conftest.py (site.addsitedir), tests/AGENTS.md (policy), tests/gates/test_gate_r1_no_pass_tests.py (R5 negative), tests/gates/test_gate_audit_format.py (R5 negative), tests/gates/test_gate_debt_registry.py (R5 negative), tests/gates/test_gate_cross_layer_imports.py (R5 negative), test_inventory.yaml, test_inventory_changes.yaml, tests/unit/* (LDD IMP:9), tests/gates/* (LDD IMP:9).
- REQUIRES: 117 01-Brief (реестр, §T6), _conftest/honesty.py (существующий механизм), .kilo/rules/testing.md (правила R1–R5), tests/gates/AGENTS.md (инвентарь B11, R1 gate B10).

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 46 (HIGH) | «14+ skips при недоступности сервиса» + «двойной стандарт require_docker_or_fail vs skipif» — ссылка test_local_auth.py:90 | **Факт: ≥25 R4-нарушений** (не 14+). Структура: (а) 6× skipif по docker CLI (hermes_l2_fallback + predeploy_gate), (б) 11× inline skip по port unreachable (local_auth 8× + langfuse 3×), (в) 4× skip по env vars not set (HERMES_DASHBOARD_PASSWORD/API_SERVER_KEY/LITELLM_MASTER_KEY), (г) 4× skip по compose-файлам/хост-окружению. Механизм honesty уже существует (_conftest/honesty.py, режим marker/xfail/fail), но покрывает только Docker daemon (require_docker_or_fail). Нет require_service_healthy для port-check, нет require_env_or_fail для кредов. | Расширить honesty API (require_service_healthy), мигрировать все skip→honesty, сохранить поэтапность marker→xfail→fail. |
| 47 (MED) | «77× sys.path.insert — легитимизировать в политике или conftest-хук» | **Факт: 65 уникальных файлов / 76 вхождений** (на 1 меньше брифа — 76 vs 77). Паттерны: (а) ~40% — `_MODULE_DIR`/`_SHARED_DIR` (core/internal/*), (б) ~30% — `Path(__file__).parent.parent / "core" / ...`, (в) ~30% — прочие (scripts, modules). | conftest-хук для общих путей + policy-раздел в tests/AGENTS.md. |
| 48 (MED) | R5: «завершить парный negative-скан (~100 ссылок на баги)» | **Факт: >100 matches** grep «TASK-\d|DevPlan\s+\d» в test-файлах, включая docstring. Многие — исторические @changes, не bug-ссылки. Реальные bug-reference ID (U-69, U-79, и т.д.) — ~20-30 в gate-тестах. R1 gate (test_gate_r1_no_pass_tests.py) уже имеет R5 negative (строки 1-220). Несколько гейтов B11 имеют негатив-тесты (cross-layer, debt). | Создать методику, задокументировать, добавить негатив-тесты для гейтов БЕЗ R5-покрытия. |
| 49 (LOW) | «LDD-выборка: IMP:9-трассы в unit/gates» | **Факт: IMP:9 широко используется** (>100 matches в test-файлах). Unit-тесты и gates В ЦЕЛОМ покрыты. Но есть пробелы: некоторые unit-тесты имеют IMP:7-8 без IMP:9; часть gate-тестов использует только assert без LDD-логов. | Аудит (не сплошное добавление): найти unit-тесты без IMP:9 + gates без LDD → добавить точечно. |
| 50 (LOW) | «test_inventory_changes.yaml синхронизировать» | **Факт: inventory заголовок говорит «2737 tests», реально в файле 2735 nodeids** (расхождение 2). Файл изменений (test_inventory_changes.yaml) хорошо поддерживается — 743+ строк задокументированных удалений. sync_inventory.py существует и работает. | Перегенерировать test_inventory.yaml через sync_inventory.py, исправить заголовок. |

---

## 1. Технический анализ и решения

### Задача 46 (HIGH) — R4: NO_SERVICE = FAIL, не skip

**Факты (верифицированы):**

Категории R4-нарушений (skip вместо fail при недоступности сервиса):

| Категория | Файлы | Кол-во | Механизм skip |
|-----------|-------|--------|---------------|
| A — Docker daemon через skipif | test_hermes_l2_fallback.py (4×), test_predeploy_gate.py (2×) | 6 | `@pytest.mark.skipif(not shutil.which("docker") ...)` |
| B — Port unreachable | test_local_auth.py (8×: lines 90,125,169,213,259,288), test_smoke_langfuse.py (3×: 88,134,191) | 11 | `pytest.skip("Port ... not reachable")` |
| C — Env vars not set | test_local_auth.py (107,202), test_smoke_hermes.py (127,178), test_smoke_litellm.py (140) | 5 | `pytest.skip("..._PASSWORD not set")` |
| D — Compose/contexte | test_component_hermes.py (153,312), test_component_clickhouse.py (167,169,176) | 5 | `pytest.skip("compose file not found")` / `pytest.skip("Production host detected")` |

**Итого: ~27 R4-нарушений** (бриф заявлял 14+).

**Уже исправлено (honesty-механизм):**
- test_local_auth.py:34 — `require_docker_or_fail` (модульный уровень, Docker daemon check) ✅
- test_smoke_platform.py:181 — `require_docker_or_fail` ✅
- test_component_clickhouse.py:172 — `require_docker_or_fail` ✅
- test_hermes_init.py:153 — `require_docker_or_fail` ✅
- test_gate_vhost_nginx_t.py:141 — `require_docker_or_fail` ✅
- _conftest/smoke.py:760 — `require_docker_or_fail` ✅

**Проблема двойного стандарта:** 6 файлов уже используют `require_docker_or_fail`, но 6 других файлов до сих пор используют старый `@pytest.mark.skipif(docker CLI)`. Плюс inline skip для port/env — вообще без механизма.

**Существующий honesty-механизм** (`_conftest/honesty.py`):
- `REQUIRE_HONESTY_MODE` env var: `"marker"` (skip) → `"xfail"` → `"fail"`
- `require_docker_or_fail(reason)` — проверяет Docker daemon
- `require_script_or_fail(path, reason)` — проверяет наличие скрипта
- `require_env_or_fail(var, reason)` — проверяет env var

**Чего не хватает:** `require_service_healthy(host, port, reason)` — проверка доступности TCP-порта (аналог inline port-check из local_auth/langfuse).

**Решение D46:**

**D46-A — Расширение honesty API:**
Добавить в `_conftest/honesty.py` функцию `require_service_healthy(host: str, port: int, reason: str, timeout: int = 5) -> None`:
- Пытается TCP connect (socket.create_connection, timeout 5s)
- При успехе: log [IMP:9] + return
- При неудаче: dispatch через `_honesty_mode()` (marker→skip, xfail→xfail, fail→fail)

**D46-B — Миграция категорий A+B:**
- Категория A (6 skipif): заменить на `require_docker_or_fail(reason=...)` на уровне модуля/функции
- Категория B (11 port-check): заменить на `require_service_healthy(host, port, reason=...)` перед тестом
- Категория C (5 env vars): заменить на `require_env_or_fail(var, reason=...)` — уже есть в API
- Категория D (5 compose/context): заменить на `require_docker_or_fail` + `require_script_or_fail` где применимо

**D46-C — Поэтапный переход:**
- Текущий CI: `REQUIRE_HONESTY_MODE` не задан → default `"marker"` (поведение идентично текущему)
- В CI workflow добавить `REQUIRE_HONESTY_MODE: marker` явно (документирует намерение)
- Оператор переключает на `"fail"` когда CI-раннеры готовы (staging с Docker)
- Wave 2 (out-of-scope этой задачи): переключение режима → операторское решение

**D46-D — Обновление CI workflow:**
- `platform-gate-fast.yml`: добавить `REQUIRE_HONESTY_MODE: marker` в env (явное документирование)
- `platform-test.yml`: аналогично

**Файлы:** `_conftest/honesty.py` (+40 LOC, require_service_healthy), `test_hermes_l2_fallback.py` (6 замен), `test_predeploy_gate.py` (2 замены), `test_local_auth.py` (~10 замен), `test_smoke_langfuse.py` (3 замены), `test_smoke_hermes.py` (2 замены), `test_smoke_litellm.py` (1 замена), `test_component_hermes.py` (2 замены), `test_component_clickhouse.py` (3 замены), `.github/workflows/platform-gate-fast.yml`, `.github/workflows/platform-test.yml`.

**Риск:** MEDIUM. Затрагивает 11 test-файлов. Режим "marker" сохраняет обратную совместимость. CI получает явный `REQUIRE_HONESTY_MODE: marker` — никакого изменения поведения.

---

### Задача 47 (MED) — 65× sys.path.insert: легитимизация

**Факты (верифицированы):**
- 65 уникальных файлов, 76 вхождений `sys.path.insert` (бриф: 77 — расхождение 1).
- Паттерны распределения:
  - ~25 вхождений: `sys.path.insert(0, str(_MODULE_DIR))` — pre-computed константа (core/internal/bootstrap/, core/internal/deploy/)
  - ~20 вхождений: `sys.path.insert(0, str(_SHARED_DIR))` — core/internal/shared/
  - ~15 вхождений: `Path(__file__).parent.parent / "core" / ...` — относительный путь
  - ~16 вхождений: прочие (scripts, modules, gates-specific)
- Существующий tests/conftest.py — тонкий фасад (<175 LOC), реэкспортирует _conftest/*.
- pytest автоматически добавляет `tests/` в sys.path, но НЕ `core/` и поддиректории.

**Анализ:** проблема не в том, что sys.path.insert — антипаттерн (это легитимный механизм для тестов), а в том, что каждый файл делает это по-своему, создавая 65 потенциальных точек дрейфа. Два взаимоисключающих подхода:

| Подход | Плюсы | Минусы |
|--------|-------|--------|
| Option 1: conftest-хук (site.addsitedir) | Один раз → все тесты; нулевая стоимость для новых тестов | Широкая кисть — потенциальные конфликты имён; сложнее отлаживать import errors |
| Option 2: Policy (AGENTS.md) | Нулевой риск конфликтов; каждый тест контролирует свой path | 65 копий шаблона — дрейф продолжается |

**Решение D47 (гибрид):**

**D47-A — conftest-хук для общих корневых путей:**
Добавить в `tests/conftest.py` блок (перед импортами из _conftest):
```python
# ── Test import paths: canonical roots for all test files ──
# Добавляем core/ и core/internal/ в sys.path — это общие пути,
# используемые >50% тестов. Избегает 65 индивидуальных sys.path.insert.
# Модуль-специфичные пути (core/modules/<name>/) добавляются точечно.
import site
_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CORE_DIR = _PKG_ROOT / "core"
_INTERNAL_DIR = _CORE_DIR / "internal"
for _p in (_PKG_ROOT, _CORE_DIR, _INTERNAL_DIR):
    site.addsitedir(str(_p))
```

Использование `site.addsitedir` вместо `sys.path.insert`:
- Обрабатывает .pth файлы если есть
- Не дублирует пути (site.addsitedir проверяет `_NamespacePath`)
- Стандартный механизм Python для site-директорий

После этого индивидуальные `sys.path.insert` для _MODULE_DIR/_SHARED_DIR/Path(parent.parent / "core") становятся избыточными и МОГУТ быть удалены (но НЕ обязаны — idempotent при site.addsitedir).

**D47-B — Policy-раздел в tests/AGENTS.md:**
Добавить секцию «## sys.path policy»:
```markdown
## sys.path policy

**Canonical roots** (добавляются conftest.py через site.addsitedir, доступны всем тестам):
- `<repo_root>/` — import core, tests, etc.
- `<repo_root>/core/` — import internal, modules, lib, entrypoints
- `<repo_root>/core/internal/` — import bootstrap, deploy, scripts, shared

**Module-specific paths** (добавляются точечно в тестовом файле):
- `core/modules/<name>/` — для тестов, импортирующих модульный код (app.py, etc.)
- `core/internal/scripts/` — для тестов generate-скриптов
- Шаблон: `sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "modules" / "<name>"))`

**Правила:**
1. Новые тесты НЕ добавляют пути, уже покрытые conftest-хуком.
2. Module-specific пути — только когда импортируется код из `core/modules/` или `core/internal/scripts/`.
3. Запрещено: `sys.path.append`, относительные импорты за пределами tests/, манипуляции с `__path__`.
```

**D47-C — Удаление избыточных sys.path.insert (опционально, низкий приоритет):**
После добавления conftest-хука, ~40 из 76 вхождений становятся избыточными. Удаление — опциональная чистка (не блокирует gate, не создаёт регрессий).

**Файлы:** `tests/conftest.py` (+10 LOC, site.addsitedir блок), `tests/AGENTS.md` (+20 LOC, policy-секция), опционально ~40 тестовых файлов (удаление избыточных insert).

**Риск:** LOW. site.addsitedir — стандартный механизм, идемпотентен (повторные вызовы не дублируют пути). Если путь уже в sys.path, insert(0, ...) — no-op для импортов.

---

### Задача 48 (MED) — R5: ANTI-SURVIVORSHIP negative-скан

**Факты (верифицированы):**
- >100 matches grep «TASK-\d|DevPlan\s+\d» в test-файлах. Из них:
  - ~60% — docstring `@changes` (исторические, не bug-reference)
  - ~25% — TRAP[TEST]/TRAP[BUG] с конкретным bug ID (U-69, U-79, TASK-N)
  - ~15% — acceptance criteria references (AC-T4, AC4)
- Gate-тесты с bug-ссылками и статусом R5:
  - `test_gate_r1_no_pass_tests.py` (B10 T1, U-69) — **уже имеет R5 negative** (inline fixtures: assert True / bare-pass / no-assert) ✅
  - `test_gate_audit_format.py` (B11 T2, U-10/D1) — **заявлен negative в манифесте**, нужно верифицировать наличие
  - `test_gate_debt_registry.py` (B11 T7, U-82/D4) — **заявлены 2 negative-теста**, нужно верифицировать
  - `test_cross_layer_imports.py` (B11 T1, U-09) — **заявлены 2 negative-теста R5** (dotted py import RED + python3 -m RED) ✅
  - `test_gate_test_inventory.py` (B11 T6, U-79) — нет R5 negative
  - `test_gate_image_tag_form.py` (B3 T7, U-60) — нет R5 negative
  - `test_gate_volumes_sot.py` (B3 T4, U-49) — нет R5 negative

**Методика R5-скана (D48-A):**

1. **Инвентаризация:** grep `U-\d+|TASK-\d+|bug.*#\d+` по tests/gates/ → список гейтов с bug-ссылками
2. **Классификация:** для каждого гейта определить:
   - Какой конкретный bug был пойман (исходный вход)
   - Есть ли negative-тест с этим же входом
   - Если нет → создать
3. **Формат negative-теста:**
   ```python
   def test_<detector>_negative_<bug_id>(self) -> None:
       """R5 negative: исходный вход, поймавший bug U-XX, детектируется."""
       # Вход, идентичный тому, что вызвал оригинальный bug
       violations = _scan_function(bug_trigger_input)
       assert len(violations) >= 1, f"R5 FAIL: detector missed original bug U-XX trigger"
   ```
4. **Приоритет:** гейты БЕЗ R5-покрытия → гейты с частичным покрытием

**Решение D48:**

**D48-B — Аудит и верификация существующих R5:**
- Проверить `test_gate_audit_format.py` — действительно ли есть негатив-тест для direct-write и free-text pipe
- Проверить `test_gate_debt_registry.py` — действительно ли 2 негатив-теста (stale >90 дней, missing Status/Rev)
- Подтверждённые → задокументировать статус; отсутствующие → включить в D48-C

**D48-C — Создание R5 negative для гейтов без покрытия:**
1. `test_gate_test_inventory.py` (U-79): negative — симулировать удаление теста без changelog записи → gate RED
2. `test_gate_image_tag_form.py` (U-60): negative — `:latest` tag (не versioned, не digest) → gate RED
3. `test_gate_volumes_sot.py` (U-49): negative — модульный compose с top-level volume → gate RED

**D48-D — Документирование методики в tests/AGENTS.md:**
Добавить секцию «## R5 ANTI-SURVIVORSHIP Protocol» с:
- Алгоритмом скана (grep → classify → verify → create)
- Форматом negative-теста
- Списком гейтов со статусом R5-покрытия

**Файлы:** `tests/gates/test_gate_test_inventory.py` (+25 LOC, negative), `tests/gates/test_gate_image_tag_form.py` (+20 LOC, negative), `tests/gates/test_gate_volumes_sot.py` (+20 LOC, negative), `tests/AGENTS.md` (+30 LOC, R5 protocol), возможна верификация `test_gate_audit_format.py` и `test_gate_debt_registry.py`.

**Риск:** LOW. Negative-тесты — чистое добавление, не меняют поведение существующих тестов. Если negative падает → детектор сломан (это и есть цель R5).

---

### Задача 49 (LOW) — LDD IMP:9 аудит

**Факты (верифицированы):**
- IMP:9 широко используется в тестах: >100 matches, особенно в:
  - test_postgres.py (30+ IMP:9 логов)
  - test_secrets_validation.py (~12 IMP:9)
  - test_contract_entrypoints.py (~15 IMP:9)
  - test_cross_layer_imports.py (~10 IMP:9)
- Unit-тесты: покрытие неравномерное — test_state_machine.py, test_bootstrap_phases.py, test_docker_orchestrator.py имеют IMP:7-8 но не всегда IMP:9
- Gate-тесты: большинство имеют IMP:9, но есть исключения (например, некоторые тесты в test_gate_module_yaml_contract.py)
- `@ldd_trajectory` декоратор (tests/_conftest/ldd.py) автоматически проверяет IMP:9 для декорированных тестов

**Анализ:** задача не в массовом добавлении IMP:9 (это было бы нарушением «не добавлять функционал»), а в точечном аудите:
1. Найти unit-тесты с бизнес-логикой, где отсутствует IMP:9
2. Найти gate-тесты без IMP:9
3. Добавить только где это семантически оправдано (assert на бизнес-правило, не на техническую механику)

**Решение D49:**

**D49-A — Скрипт аудита (одноразовый):**
```bash
# Найти test-функции без IMP:9 в unit/ и gates/
rg -L "IMP:9" tests/unit/ tests/gates/ --include "*.py"
```
Результат → ручной просмотр → список функций для добавления.

**D49-B — Критерии добавления IMP:9:**
- Добавлять IMP:9 ТОЛЬКО в assert-блоки, проверяющие бизнес-правило (не технический факт)
- Бизнес-правило: «пароль не пуст», «секрет не в compose», «имя контейнера соответствует шаблону»
- НЕ добавлять: «файл существует», «тип — dict», «os.path.isfile вернул True»
- IMP:9 формат: `logger.info("[IMP:9][<block>] ASSERT: <business-rule>")` ИЛИ `logger.error("[IMP:9][<block>] FAIL: <business-rule>")`

**D49-C — Целевые файлы (предварительный список, уточняется аудитом):**
- `tests/unit/test_state_machine.py` — проверить бизнес-assert'ы на IMP:9
- `tests/unit/test_bootstrap_phases.py` — фазы — core business logic
- `tests/unit/test_docker_orchestrator.py` — deploy-оркестрация
- `tests/gates/test_gate_module_yaml_contract.py` — контракт module.yaml
- `tests/gates/test_gate_healthcheck_contract.py` — healthcheck-контракт

**Файлы:** ~5-10 файлов (unit + gates), +2-5 IMP:9 строк в каждом.

**Риск:** LOW. Добавление логов не меняет логику. `@ldd_trajectory` уже проверяет наличие IMP:9 — если тест декорирован, добавление лога сделает проверку строже (что правильно).

---

### Задача 50 (LOW) — test_inventory синхронизация

**Факты (верифицированы):**
- `test_inventory.yaml` заголовок: «@changes — 2026-08-01 | regenerated (2737 tests)»
- Реальное количество nodeid в файле: **2735** (расхождение: −2)
- `test_inventory_changes.yaml`: 743+ строк, хорошо поддерживается, документирует удаления от DevPlan 001 до 091
- `tests/tools/sync_inventory.py`: 300 LOC, рабочий инструмент регенерации (pytest --collect-only -q)
- `tests/gates/test_gate_test_inventory.py`: gate валидации инвентаря (бинарное сравнение + rename-детекция)

**Анализ:** расхождение в 2 теста может быть вызвано:
- Тесты добавлены без перегенерации inventory
- Тесты удалены без обновления заголовка (но changelog записи есть → значит, удаления задокументированы)
- Ошибка округления при прошлой регенерации

**Решение D50:**

**D50-A — Перегенерация inventory:**
```bash
python tests/tools/sync_inventory.py
```
Убедиться что результат idempotent (второй запуск = 0 diff).

**D50-B — Верификация заголовка:**
После регенерации заголовок автоматически обновится до актуального count. Проверить что count совпадает с `grep -c "^- 'tests/" tests/test_inventory.yaml`.

**D50-C — Проверка gate:**
```bash
pytest tests/gates/test_gate_test_inventory.py -v
```
Должен быть зелёным после регенерации.

**Файлы:** `tests/test_inventory.yaml` (регенерация).

**Риск:** LOW. sync_inventory.py читает заголовок, заменяет @changes строку с count, сохраняет исторические @changes. Idempotent по дизайну.

---

## 2. Порядок реализации

Фаза 1 — honesty API + миграция (основной объём):
1. **D46-A** — `require_service_healthy` в `_conftest/honesty.py`.
2. **D46-B** — миграция категории A (skipif→require_docker_or_fail): 2 файла, 6 замен.
3. **D46-B** — миграция категории B (port-check→require_service_healthy): 3 файла, 11 замен.
4. **D46-B** — миграция категории C (env→require_env_or_fail): 3 файла, 5 замен.
5. **D46-B** — миграция категории D (compose/context): 2 файла, 5 замен.

Фаза 2 — sys.path.insert:
6. **D47-A** — conftest-хук с site.addsitedir.
7. **D47-B** — policy-секция в tests/AGENTS.md.
8. Проверка: запустить тесты, убедиться что site.addsitedir не ломает импорты.

Фаза 3 — R5 + LDD + inventory:
9. **D48-B** — аудит существующих R5 negative в gate-тестах (верификация audit_format + debt_registry).
10. **D48-C** — создание R5 negative для 3 гейтов без покрытия.
11. **D48-D** — документирование R5 методики в tests/AGENTS.md.
12. **D49-A** — grep-аудит IMP:9 → список файлов для добавления.
13. **D49-C** — точечное добавление IMP:9 в unit/gates (~5-10 файлов).
14. **D50-A** — регенерация test_inventory.yaml через sync_inventory.py.
15. **D46-D** — обновление CI workflow (REQUIRE_HONESTY_MODE: marker).

Фаза 4 — верификация:
16. `make gate MODE=fast` — зелёный.
17. `make check-manifests` — зелёный (entrypoint-manifest не меняется для tasks 46-50, если gate-теги не трогаем — но R5 может добавить тест-функции в существующие gate-файлы, что не требует manifest-изменений).
18. `pytest tests/ -x --timeout=60` — выборочный прогон затронутых тестов.

---

## 3. Критерии приёмки (повтор из контракта)

- AC-F1: 6 skipif(docker CLI) → require_docker_or_fail. `rg "@pytest\.mark\.skipif.*docker" tests/` → 0 совпадений.
- AC-F2: 11 inline skip(port unreachable) → require_service_healthy. `rg "pytest\.skip.*Port.*not reachable" tests/` → 0 совпадений.
- AC-F3: `REQUIRE_HONESTY_MODE=marker pytest ...` — passes identically. `REQUIRE_HONESTY_MODE=fail pytest ...` (с Docker) — passes; (без Docker) — fails.
- AC-F4: tests/conftest.py содержит site.addsitedir блок; tests/AGENTS.md содержит «## sys.path policy».
- AC-F5: R5 negative созданы для ≥3 гейтов; методика задокументирована в tests/AGENTS.md.
- AC-F6: 0 unit-тестов с бизнес-логикой без IMP:9 (допустимо отсутствие в pure-mechanical тестах типа «файл существует»).
- AC-F7: `python tests/tools/sync_inventory.py` — 0 diff (idempotent). Заголовок = актуальный count.
- AC-F8: `make gate MODE=fast` + `make check-manifests` зелёные.

Дополнительные проверки:
- `rg "pytest\.skip.*docker\|pytest\.skip.*compose file\|pytest\.skip.*Port.*not reachable\|pytest\.skip.*not set.*cannot authenticate" tests/` — 0 совпадений (все перенесены в honesty API).
- `python -c "from _conftest.honesty import require_service_healthy; print('OK')"` — импорт работает.
- `pytest tests/gates/test_gate_r1_no_pass_tests.py tests/gates/test_gate_test_inventory.py -v` — все R5 negative проходят.

---

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| site.addsitedir ломает импорты в edge-case тестах (конфликт имён модулей) | Прогнать быстрый тест после добавления хука: `pytest tests/ --collect-only -q`. site.addsitedir идемпотентен, существующие sys.path.insert остаются на месте → даже при конфликте поведение не меняется. |
| require_service_healthy с таймаутом 5s замедляет тесты | Таймаут 5s — разумный компромисс. При недоступности сервиса dispatch происходит мгновенно (mode=marker → skip). Только при mode=fail и доступном сервисе — попытка connect. |
| Ручной grep-аудит IMP:9 пропускает файлы | Двухпроходный: сначала `rg -L "IMP:9"` для обнаружения кандидатов, затем ручной просмотр каждого кандидата на наличие бизнес-логики. |
| R5 negative для test_gate_test_inventory.py сложно симулировать (требует манипуляции с инвентарём) | Использовать tmp_path фикстуру: создать временный inventory + changelog, передать в _collect_tests / _load_inventory. Negative: инвентарь без changelog записи. |
| Перегенерация test_inventory.yaml в разных окружениях даёт разный count | sync_inventory.py уже заточен под idempotent. Разные окружения могут иметь разное число тестов (e2e с NODE и без) → инвентарь регенерируется в том же окружении что и gate-валидация (CI). |

---

## 5. Оценка

- Изменяемые файлы: ~20 (11 тестовых миграций R4 + 1 conftest + 1 AGENTS.md + 3 gate R5 + ~5-10 LDD + 1 inventory + 2 CI workflow).
- Новые функции: `require_service_healthy` (~30 LOC), 3 negative-теста (~65 LOC).
- Строк кода: ~200 строк добавлено, ~50 строк заменено (skip→honesty), ~100 строк удалено (избыточные sys.path.insert — опционально).
- Трудозатраты: ~0.5-0.75 дня агент-времени. Размер: **STANDARD** (9-20 файлов, бизнес-логика — test honesty enforcement) → только DevPlan.
- Сложность: MEDIUM — много файлов, но каждая замена механическая (skip→honesty API); site.addsitedir — одноразовый хук; R5 — 3 новых теста.

---

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 46 | Масштаб: 14+ → ≥27 R4-нарушений | Бриф недооценил количество. Добавлена категоризация A/B/C/D для системной миграции. |
| 46 | Добавлен `require_service_healthy` | Бриф не специфицировал механизм для port-check. Без него inline skip остаются незаменёнными. |
| 46 | Сохранён поэтапный переход (marker→fail) | Бриф не упоминал CI-риски. Прямой skip→fail сломает CI без Docker (staging). |
| 47 | 77 → 76 sys.path.insert (расхождение 1) | Реальный подсчёт через rg -c. 1 вхождение, вероятно, в комментарии/строке а не в коде. |
| 47 | Гибридное решение (conftest + policy) | Бриф предлагал «или-или». Гибрид безопаснее: общие пути через conftest, специфичные — через policy. |
| 48 | Скоуп R5 сужен до gate-тестов | Бриф говорил «~100 ссылок на баги» — большинство в docstring @changes, не bug-reference. Фокус на gate-тесты с реальными bug ID (U-XX). |
| 50 | 2737 → 2735 (расхождение 2) | Заголовок устарел относительно содержимого. Исправляется регенерацией. |

---

## Next Steps

### Wave 1
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/07-DevPlan.md, implement Wave 1: D46-A (require_service_healthy), D46-B (миграция skip→honesty), D46-D (CI workflow)

### Wave 2 (после Wave 1)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/07-DevPlan.md, implement Wave 2: D47-A (conftest site.addsitedir), D47-B (AGENTS.md policy), D48-B/C/D (R5 negative), D49 (LDD IMP:9 аудит), D50 (inventory sync)
