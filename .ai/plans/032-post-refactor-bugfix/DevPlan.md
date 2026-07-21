# 032-DevPlan: Post-Refactor Bugfix — Stack Startup Regression Fixes

**Source:** StatusReport 030 — Stack Startup Report (tronyxlab context)
**Predecessors:** DevPlan 028 (Wave 1 Immediate), DevPlan 029 (Wave 2 Dangerous), DevPlan 031 (YAML JSON Output Fix)
**Verified against codebase:** 2026-07-21 (all 5 bugs confirmed or reclassified)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить оставшиеся баги, обнаруженные при запуске стека (StatusReport 030), верифицировать фикс бага #1 (DevPlan 031), добавить тестовое покрытие для предотвращения регресса, идентифицировать системную первопричину.
DESCRIPTION:           5 багов из StatusReport 030 проверены против кодовой базы. Из них:
                       1 CRITICAL — уже исправлен (DevPlan 031, yaml_query.py JSON output), требуется верификация.
                       1 HIGH — litellm healthcheck flapping из-за Prisma migrate задержки, требуется настройка.
                       1 MEDIUM — Grafana project-template.json UID с `$PROJECT` placeholder в provisioning path.
                       1 RECLASSIFIED: не баг, а документированный swap-механизм (contact-points.yml.*).
                       1 LOW: self-resolving (SQLite locked), не требует исправления.
                       Системная первопричина: инлайн-python3 консолидация (DevPlan 028 W1-E7) создала drift между _format_item() и _cli() в yaml_query.py — оба пути вывода в одном файле, один использовал json.dumps(), другой print().
RATIONALE:             Два из пяти багов — прямые последствия рефакторинга Wave 1 (yaml_query.py regression + provision тесты). Litellm healthcheck — pre-existing, но критичен для стабильного startup. Grafana template UID — pre-existing, вызывает warning в логах при каждом startup. Contact-points и SQLite locked — переклассифицированы как не-баги (документированный механизм и self-resolving). Не терять tempo: DevPlan 031 уже исправил критический баг, этот DevPlan закрывает оставшиеся actionable проблемы.
ACCEPTANCE_CRITERIA:
  **B1 (yaml_query.py — VERIFY only):**
    1. `make gate MODE=fast` — все 9 тестов `test_unit_provision_environment.py` проходят (подтверждение фикса DevPlan 031).
    2. `pytest tests/test_unit_yaml_query.py -v` — 4 теста green (list→JSON, dict→JSON, scalar unchanged, no Python repr).
    3. `make provision --scope networks --dry-run` — exit 0, перечислены все 8 сетей.
  **B2 (litellm healthcheck):**
    4. `start_period` увеличен с 60s до 120s в `docker-compose.base.yml` — даёт Prisma migrate запас на ~50-60s + buffer.
    5. `retries` увеличены с 3 до 5 в `docker-compose.base.yml` — покрывает флап в течение 5 × 15s = 75s после start_period.
    6. Healthcheck использует python3 (не curl) — уже исправлено, только verify.
    7. `make gate MODE=fast` — `test_gate_healthcheck_contract.py::test_litellm_uses_check_http` green.
    8. `test_litellm_static.py::test_litellm_healthcheck_sh_exists` — green.
  **B3 (grafana project-template.json UID):**
    9. Файл `project-template.json` удалён из volume mounts в `docker-compose.base.yml` (строка 187) ИЛИ `GF_PROVISIONING_DASHBOARDS_PATH` настроен так, чтобы НЕ включать template-файлы.
    10. `make gate MODE=fast` — `test_monitoring_static.py` все тесты green (dashboard count адаптирован).
    11. Template рендерится только через `on-project-deploy.sh` → `/opt/grafana/provisioning/dashboards/` (уже работает корректно).
  **B4 (contact-points.yml.*):**
    12. Переклассифицирован: не баг. Документированный swap-механизм описан в `contact-points.yml` строках 13-18. Никаких изменений кода не требуется.
  **B5 (grafana SQLite locked):**
    13. Переклассифицирован: self-resolving. LOW. Никаких изменений кода не требуется.
IMPLEMENTS:            Fix for StatusReport 030 bugs #2, #3. Verification of DevPlan 031 fix (bug #1). Reclassification of bugs #4, #5.
IMPACTS:               **Modified:** `core/modules/litellm/docker-compose.base.yml` (healthcheck: start_period + retries), `core/modules/monitoring/docker-compose.base.yml` (убрать template из volume mounts), `tests/test_monitoring_static.py` (адаптировать EXPECTED_DASHBOARDS).
REQUIRES:              Чистый working tree. Docker daemon running (для smoke test litellm при желании). Python 3.10+, pytest.
TASK_SIZE:             SMALL (3 файла изменений, 1 файл verify-only, zero-risk)
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Суперпозиционный анализ по каждому из 5 багов (S1-S7 гипотезы) => GOAL_SUPERPOSITION
- GOAL Системная первопричина: связь с DevPlan 028 W1-E7 inline python3 консолидацией => GOAL_ROOT_CAUSE
- GOAL B1: верификация фикса DevPlan 031 => GOAL_B1_VERIFY
- GOAL B2: litellm healthcheck hardening => GOAL_B2_LITELLM
- GOAL B3: grafana template UID изоляция => GOAL_B3_GRAFANA
- GOAL B4, B5: реклассификация => GOAL_RECLASSIFY
- GOAL Тестовое покрытие для предотвращения регресса => GOAL_TESTS
- GOAL File Manifest + Acceptance Criteria с командами проверки => GOAL_MANIFEST
**SECTION_USE_CASES:**
- USE_CASE CI runner запускает gate MODE=fast → все тесты green (включая provision + litellm) => UC_GATE_GREEN
- USE_CASE Оператор запускает make up → litellm стартует без флапа healthcheck → Grafana логи чисты (нет warning про illegal UID) => UC_CLEAN_STARTUP
- USE_CASE Разработчик добавляет новый dashboard template → видит, что template монтируется отдельно от dashboards → не добавляет в provisioning path => UC_TEMPLATE_SAFETY
$END_DOCUMENT_PLAN
```

---

## 1. Superposition Analysis

### B1: yaml_query.py print(value) → Python repr instead of JSON

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Drift между _format_item() и _cli()** — обе функции в одном файле, одна использует `json.dumps()`, другая `print(value)` | **95%** ✅ CONFIRMED | `_format_item()` (строка 126) корректно: `json.dumps(item)`. `_cli()` (строка 195, до фикса) использовала `print(value)`. Это прямой копипаст-drift между двумя ветками вывода. |
| S2 | `json.dumps()` был пропущен при создании yaml_query.py из-за неполного понимания output-контракта `yaml_get_field` | 80% | Контракт `yaml_read.sh`: «Dict/list values serialized as JSON» — был задокументирован, но не протестирован для CLI-пути. |
| S3 | Bash→Python миграция потеряла контекст — в inline `python3 -c "print(json.dumps(...))"` формат был явным; в Python-модуле `print(value)` стал неявным | 70% | Инлайн-код: `python3 -c "import yaml,json,sys; print(json.dumps(...))"` — json.dumps() явно. Python-модуль: `print(value)` — неявно. |
| S4 | Unit-тесты `test_yaml_query.py` тестировали только Python API (yaml_get/yaml_query), но не CLI output (subprocess.run) | 60% | DevPlan 031 создал `test_unit_yaml_query.py` с subprocess-тестами для покрытия CLI output. До этого тесты были только на Python API. |
| S5 | `provision-environment.sh` json.load() — хрупкая интеграция, нет graceful error handling | 40% | `set -euo pipefail` + `json.load()` → любой невалидный JSON = silent kill. Но это by-design (fail-fast), не баг. |
| S6 | YAML библиотека `ruamel.yaml` vs `PyYAML` — difference in dict/list __str__ representation | 5% | `print(dict)` в Python всегда использует repr-формат с одиночными кавычками, независимо от YAML-библиотеки. |
| S7 | OС-dependent: macOS Python vs Linux Python repr разный | <1% | `print(dict)` идентичен на всех платформах. |

**Коллапс:** S1 CONFIRMED. Фикс уже применён в DevPlan 031. Требуется только верификация.

**Системная первопричина:** Drift между `_format_item()` и `_cli()` в рамках одного файла `yaml_query.py`. Корень — Wave 1 (DevPlan 028 W1-E7) создал Python-модуль заменой 40+ inline `python3 -c` вызовов, но output-контракт был проверен только для `--items` режима (`_format_item` использует `json.dumps()`), а `--get` режим (`_cli`) использовал `print(value)` без `json.dumps()`. Это классический drift между двумя code path в одном модуле.

---

### B2: LiteLLM healthcheck flapping из-за Prisma migrate

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **start_period=60s недостаточен для Prisma migrate (~50s) + Python startup (~10s)** — healthcheck начинает проверки, когда proxy ещё в процессе startup | **90%** ✅ PRIMARY | `prisma migrate deploy` занимает 40-60s, после этого ещё инициализация model cost map (~10s). При start_period=60s, healthcheck стартует как раз когда proxy ещё не готов. |
| S2 | retries=3 (3 × 15s = 45s buffer) может быть достаточно, но интервал 15s слишком короткий для повторных попыток во время GC/memory pressure | 30% | LiteLLM после Prisma migrate делает fetch model cost map и memory-allocation → временные GC паузы могут совпадать с healthcheck интервалом. |
| S3 | Prisma Wolfi-совместимость — регресс после обновления v1.90.2 → v1.91.2 | 20% | TRAP[DECISION] в docker-compose.base.yml:34 подтверждает, что Prisma+PostgreSQL работает корректно. Но startup latency мог измениться между версиями. |
| S4 | Healthcheck `/health/readiness` возвращает 500 в момент инициализации вместо отказа в соединении | 15% | `/health/readiness` теоретически не должен требовать model_list, но во время Prisma migrate DB может быть недоступна → 500. |
| S5 | PostgreSQL/pgbouncer connection pool exhaustion на startup | 10% | Prisma migrate открывает много connections на короткое время. Но pgbouncer должен handle это. |
| S6 | macOS Docker network latency — специфично для локальной разработки | 5% | Docker Desktop на macOS имеет известные network performance issues, но это не должно удлинять Prisma migrate на порядки. |
| S7 | TRAP[BUG] curl не установлен → healthcheck команда падает до исправления | <1% | Уже исправлено: заменён на python3 (TRAP[BUG] 2026-07-21 в docker-compose.test.yml:26-29 и docker-compose.base.yml:139). |

**Коллапс:** S1 CONFIRMED PRIMARY. Фикс: увеличить start_period до 120s, увеличить retries до 5.

---

### B3: Grafana project-template.json UID с illegal character `$`

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Template монтируется в `GF_PROVISIONING_DASHBOARDS_PATH`** — Grafana пытается импортировать его как статичный dashboard, UID `project_$PROJECT` содержит `$` (illegal по Grafana UID regex: `[a-zA-Z0-9:_-]+`) | **95%** ✅ CONFIRMED | docker-compose.base.yml:187 монтирует `project-template.json` в `/etc/grafana/provisioning/dashboards/` — путь, который Grafana читает на startup. |
| S2 | Template должен монтироваться в отдельную директорию (не в provisioning path), а рендериться через on-project-deploy.sh → /opt/grafana/provisioning/dashboards/ | 85% | on-project-deploy.sh:210 генерирует dashboard из template в `/opt/grafana/provisioning/dashboards/${HOOK_PROJECT}.json`. Это правильный путь. Template в `/etc/grafana/provisioning/dashboards/` — избыточен. |
| S3 | Grafana 11.6.16 silently игнорирует dashboard с illegal UID (warning в логах, не fatal) | 60% | StatusReport 030: severity MEDIUM, не блокирует startup. Grafana логирует warning и пропускает dashboard. |
| S4 | Переменная окружения `$PROJECT` могла бы разрешиться в docker-compose, но она не установлена | 30% | docker-compose не имеет `PROJECT` env var в окружении grafana сервиса. Даже если бы имел — `$PROJECT` в UID всё равно illegal (UID не поддерживает env substitution). |
| S5 | Template должен обрабатываться template-engine.sh ДО монтирования | 10% | Это архитектурное решение: pre-render все templates при сборке, а не при startup. Но текущий подход (render on deploy) проще. |
| S6 | `$PROJECT` валиден в Grafana UID потому что Grafana поддерживает variables в provisioning | <1% | Grafana dashboard provisioning НЕ поддерживает переменные в UID. Только в dashboard variables (templating). |
| S7 | Баг — в docker-compose.base.yml, а не в самом template | <1% | Template корректен как template. Проблема в том, КУДА он монтируется. |

**Коллапс:** S1+S2 CONFIRMED. Фикс: убрать монтирование template из `GF_PROVISIONING_DASHBOARDS_PATH`, оставить только рендеринг через `on-project-deploy.sh`.

---

### B4: contact-points.yml.telegram — «невалидный суффикс»

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Это НЕ баг** — документированный swap-механизм. `.yml.telegram` и `.yml.disabled` — невалидные для Grafana расширения (Grafana читает только `.yml` и `.yaml`), поэтому они намеренно не импортируются. Swap-инструкция описана в `contact-points.yml`:13-18. | **99%** ✅ RECLASSIFIED | Это by-design механизм переключения между "без Telegram" (пустой contact-points.yml) и "с Telegram" (contact-points.yml.telegram → переименовать в contact-points.yml). |
| S2 | Grafana 11.6.16 может начать читать файлы с любым расширением в provisioning dir | 1% | Маловероятно — Grafana provisioning parser фильтрует по расширению `.yml`/`.yaml`. |
| S3 | Файлы `.disabled` и `.telegram` замусоривают директорию | 0% | Это намеренно: swap-механизм требует наличия обоих файлов в одной директории. |

**Коллапс:** S1 — RECLASSIFIED: не баг. Изменений не требуется.

---

### B5: Grafana SQLite database locked

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Self-resolving race condition** — Grafana при startup открывает grafana.db для миграций, параллельно provisioning plugins пытаются читать → WAL lock conflict. Разрешается через ~3-5s. | **95%** ✅ SELF-RESOLVING | Это нормальное поведение SQLite в WAL-режиме. Grafana использует SQLite по умолчанию (`/var/lib/grafana/grafana.db`). |
| S2 | Grafana 11.6.16 regression — более агрессивная блокировка чем в 11.5 | 5% | Маловероятно — SQLite locking модель не менялась между минорными версиями. |
| S3 | Volume permission issue — grafana-data создан с неправильными permissions | <1% | Docker managed volume, Grafana process внутри контейнера имеет полный доступ. |
| S4 | Недостаточно ресурсов (CPU/memory) для concurrent access | <1% | Grafana 256M memory limit; database locked — это I/O lock, не memory. |
| S5 | Переход на PostgreSQL устранил бы проблему | <1% | Технически верно, но scope неоправдан для self-resolving LOW-severity проблемы. |
| S6 | Несколько реплик Grafana (не предусмотрено архитектурой) | <1% | Архитектура предполагает 1 инстанс Grafana. |
| S7 | Antivirus/файловая система macOS задерживает SQLite WAL | <1% | Docker volume на macOS через virtiofs/gRPC-FUSE — но database locked было бы permanent, не self-resolving. |

**Коллапс:** S1 — RECLASSIFIED: self-resolving, LOW. Изменений не требуется.

---

## 2. Systemic Root Cause Analysis

```
                     DevPlan 027: Architecture Modernization Program
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
          DevPlan 028 Wave 1      DevPlan 029 Wave 2
          (Immediate, zero-risk)  (Dangerous, high-risk)
                    │
          ┌─────────┼──────────┐
          │         │          │
     W1-E1       W1-E7       W1-E8
  (AGENTS.md   (inline      (baseline
  language     python3      metrics)
  policy)      consolidation
               → yaml_query.py
                    │
              ┌─────┴──────┐
              │            │
         _format_item()  _cli()
         json.dumps() ✅  print(value) ❌
              │            │
              └─────┬──────┘
                    │
              DRIFT BUG #1
                    │
          ┌─────────┼──────────┐
          ▼                    ▼
    provision-env.sh      test_yaml_query.py
    json.load() → fail    (no CLI output test)
          │
          ▼
    make up fails
    (StatusReport 030)
```

**Системная первопричина:** DevPlan 028 W1-E7 (inline python3 consolidation) создал `yaml_query.py` для замены 40+ inline `python3 -c` вызовов в shell-скриптах. Это правильное архитектурное решение (Python-модуль вместо inline-кода). Но реализация имела drift между двумя code paths в одном файле:

1. `_format_item()` (для `--items` режима) — корректно использовала `json.dumps(item)`
2. `_cli()` (для `--get` режима) — использовала `print(value)`, что для dict/list даёт Python repr (одинарные кавычки) вместо JSON (двойные кавычки)

Это drift, потому что оба code path имеют одинаковый контракт: «вывести значение в stdout». Но один путь форматирует как JSON, другой — как Python repr.

**Почему тесты не поймали:** `test_yaml_query.py` (создан в W1-E7) тестировал Python API (`yaml_get()`, `yaml_query()`, `_dotted_get()`), но НЕ тестировал CLI output через `subprocess.run()`. Это gap в test coverage: unit-тесты покрывали внутреннюю логику, но не интеграционный контракт «CLI stdout = валидный JSON».

**Уже исправлено:**
- `test_unit_yaml_query.py` (DevPlan 031) добавил subprocess-based тесты CLI output.
- `yaml_query.py:_cli()` исправлен: добавлен `elif isinstance(value, (dict, list)): print(json.dumps(value))`.

---

## 3. Fix Plan

### Fix B2: LiteLLM Healthcheck Hardening

**Файл:** `core/modules/litellm/docker-compose.base.yml`

**Текущее состояние (строка 138-143):**
```yaml
healthcheck:
  test: ["CMD", "python3", "-c", "..."]
  interval: 15s
  timeout: 10s
  retries: 3
  start_period: 60s
```

**Изменение:**
```yaml
healthcheck:
  test: ["CMD", "python3", "-c", "..."]
  interval: 15s
  timeout: 10s
  retries: 5          # 3→5: покрывает флап x5 вместо x3
  start_period: 120s  # 60→120s: даёт запас на Prisma migrate (50s) + model cost fetch (10s) + buffer (60s)
```

**Расчёт:**
- Prisma migrate: 40-60s
- Model cost map fetch: 5-10s
- Python deps + GC: 5-10s
- Buffer: 40s
- Total до readiness: 90-120s (start_period покрывает)
- После start_period: 5 retries × 15s = 75s grace → total 120+75=195s max
- Реально: 2-3 retries достаточно → healthy через ~120+30=150s

**Обоснование retries=5:** Prisma migrate может занять 50-60s, model cost map ещё 10s. Если start_period=120s, proxy готов через ~70-80s, но первый healthcheck может попасть на GC после model cost → retry. 5 retries дают запас на 2-3 GC-цикла.

**Также изменяем `docker-compose.test.yml`:**
```yaml
healthcheck:
  test: ["CMD", "python3", "-c", "..."]
  interval: 10s
  start_period: 45s   # 30→45s: test env быстрее (меньше моделей), но всё равно нужен запас
  retries: 6           # без изменений (уже консервативно)
```

---

### Fix B3: Grafana Template UID Isolation

**Файл:** `core/modules/monitoring/docker-compose.base.yml`

**Текущее состояние (строка 187):**
```yaml
- ./config/dashboards/project-template.json:/etc/grafana/provisioning/dashboards/project-template.json:ro
```

**Проблема:** Grafana читает `/etc/grafana/provisioning/dashboards/` на startup и пытается импортировать `project-template.json` как статичный dashboard. UID `project_$PROJECT` содержит `$` — illegal character.

**Варианты фикса:**

| # | Вариант | Плюсы | Минусы | Выбор |
|---|---------|-------|--------|-------|
| A | **Убрать mount** (удалить строку 187) | Минимальное изменение. Template рендерится только через on-project-deploy.sh в `/opt/grafana/provisioning/dashboards/`. | Никаких — template не нужен в provisioning path. | ✅ RECOMMENDED |
| B | **Создать отдельную директорию** для templates: `./config/dashboards-templates/` | Явное разделение templates vs dashboards. | Дополнительная директория. Overengineering для 1 template. | ⚠️ Overkill |
| C | **Изменить GF_PROVISIONING_DASHBOARDS_PATH** на `/etc/grafana/provisioning/dashboards-enabled/` и копировать туда только готовые dashboard'ы | Чистое разделение. | Ломает существующий provisioning для 6 других dashboard'ов — нужно копировать их тоже. | ❌ High impact |

**Решение: вариант A** — удалить строку 187. Template используется только через on-project-deploy.sh.

**Проверка:** `on-project-deploy.sh:210` генерирует dashboard из template в `/opt/grafana/provisioning/dashboards/${HOOK_PROJECT}.json`. GF_PROVISIONING_DASHBOARDS_PATH по-прежнему указывает на `/etc/grafana/provisioning/dashboards/`, куда монтируются 6 статических dashboard'ов. Template рендерится отдельно и кладётся в `/opt/grafana/...`.

**Сопутствующее изменение в тестах:**

Файл `tests/test_monitoring_static.py` — `EXPECTED_DASHBOARDS` (строка 63-70):
```python
EXPECTED_DASHBOARDS = [
    "ai-overview.json",
    "infrastructure.json",
    "llm-usage-breakdown.json",
    "logs-incident-inspector.json",
    "dora-ci-cd.json",
    "project-template.json",  # ← нужно удалить или заменить
]
```

**Варианты:**
- A: Удалить `project-template.json` из EXPECTED_DASHBOARDS — минус: dashboard-файл всё ещё существует в директории, просто не монтируется.
- B: Оставить в EXPECTED_DASHBOARDS, но добавить проверку что он НЕ в volume mounts. Минус: усложнение теста.
- C: **Создать отдельный список `TEMPLATE_DASHBOARDS`** для template-файлов, которые существуют но не монтируются. Минус: overengineering.

**Решение: вариант A** — удалить из EXPECTED_DASHBOARDS. Тест `test_dashboards_exist_and_valid_json` проверяет наличие файлов в директории. Мы убираем `project-template.json` из списка ожидаемых, но файл физически остаётся в `config/dashboards/`. Можно либо удалить его из списка ожиданий, либо адаптировать тест.

**Лучшее решение:** `project-template.json` остаётся в `config/dashboards/` (физически), но в EXPECTED_DASHBOARDS заменяется на отдельную константу `TEMPLATE_DASHBOARDS` — список dashboard-файлов, которые являются шаблонами и не должны монтироваться напрямую. Тест проверяет их наличие (файл существует), но валидация проходит.

```python
EXPECTED_DASHBOARDS = [
    "ai-overview.json",
    "infrastructure.json",
    "llm-usage-breakdown.json",
    "logs-incident-inspector.json",
    "dora-ci-cd.json",
]
TEMPLATE_DASHBOARDS = [
    "project-template.json",
]
```

И в `test_dashboards_exist_and_valid_json` добавить проверку template-файлов:
```python
# Validate template dashboards exist (these are templates, not direct Grafana dashboards)
for tpl_file in TEMPLATE_DASHBOARDS:
    assert tpl_file in present, f"Template dashboard missing: {tpl_file}"
```

И добавить **новый тест** `test_template_dashboards_not_in_volume_mounts` который проверяет, что template-файлы НЕ перечислены в volume mounts для `/etc/grafana/provisioning/dashboards/`.

Но это overengineering для одного файла. Прагматичный подход: просто убрать из EXPECTED_DASHBOARDS, добавить отдельную константу и минимальную проверку.

---

### Fix B1: Verify only (уже исправлен в DevPlan 031)

**Проверка:** `make gate MODE=fast` → все provision тесты green.

---

### Fix B4, B5: No code changes (reclassified)

---

## 4. File Manifest

### MODIFY

| File | Change | Lines |
|------|--------|-------|
| `core/modules/litellm/docker-compose.base.yml` | healthcheck: start_period 60→120s, retries 3→5 | 138-143 |
| `core/modules/litellm/docker-compose.test.yml` | healthcheck: start_period 30→45s | 31 |
| `core/modules/monitoring/docker-compose.base.yml` | удалить mount project-template.json (строка 187) | 187 |
| `tests/test_monitoring_static.py` | EXPECTED_DASHBOARDS: убрать project-template.json; добавить TEMPLATE_DASHBOARDS + проверку | 63-70, 346-376 |

### VERIFY ONLY (already fixed)

| File | Check |
|------|-------|
| `core/internal/scripts/yaml_query.py` | `_cli()`: `elif isinstance(value, (dict, list)): print(json.dumps(value))` присутствует |
| `tests/test_unit_yaml_query.py` | 4 теста: list→JSON, dict→JSON, scalar unchanged, no Python repr |
| `tests/test_unit_provision_environment.py` | 9 тестов DryRun + LDD проходят |

### NO CHANGE (reclassified)

| File | Reason |
|------|--------|
| `core/modules/monitoring/config/alerting/contact-points.yml.telegram` | Swap-механизм, не баг |
| `core/modules/monitoring/config/alerting/contact-points.yml.disabled` | Swap-механизм, не баг |
| `core/modules/monitoring/docker-compose.base.yml` (grafana SQLite) | Self-resolving, LOW |

---

## 5. Acceptance Criteria — Verifiable Commands

### B1 (Verify yaml_query.py fix)

```bash
# AC-1: make gate MODE=fast — all provision tests pass
make gate MODE=fast 2>&1 | grep -E "(PASSED|FAILED)"
# Expected: 9 passed (test_unit_provision_environment.py) + остальные green

# AC-2: CLI output regression tests
python -m pytest tests/test_unit_yaml_query.py -v
# Expected: 4 passed

# AC-3: dry-run provision networks
make provision --scope networks --dry-run 2>&1
# Expected: exit 0, lists 8 network names
```

### B2 (LiteLLM healthcheck)

```bash
# AC-4: start_period=120s in base.compose
grep "start_period" core/modules/litellm/docker-compose.base.yml
# Expected: start_period: 120s

# AC-5: retries=5 in base.compose
grep "retries" core/modules/litellm/docker-compose.base.yml
# Expected: retries: 5

# AC-6: start_period=45s in test.compose
grep "start_period" core/modules/litellm/docker-compose.test.yml
# Expected: start_period: 45s

# AC-7: healthcheck contract gate — litellm uses check_http
python -m pytest tests/gates/test_gate_healthcheck_contract.py::test_litellm_uses_check_http -v
# Expected: PASSED

# AC-8: static test litellm healthcheck.sh
python -m pytest tests/test_litellm_static.py::test_litellm_healthcheck_sh_exists -v
# Expected: PASSED
```

### B3 (Grafana template UID)

```bash
# AC-9: project-template.json NOT in volume mounts
grep "project-template.json" core/modules/monitoring/docker-compose.base.yml
# Expected: NO MATCH (строка 187 удалена)

# AC-10: monitoring static tests green
python -m pytest tests/test_monitoring_static.py -v
# Expected: все тесты PASSED

# AC-11: template rendered via on-project-deploy.sh (check hook exists)
grep "project-template.json" core/modules/monitoring/hooks/on-project-deploy.sh
# Expected: 1 match (template path for rendering)
```

### B4, B5 (Reclassified)

```bash
# AC-12: contact-points swap mechanism documented
grep -c "HOW TO ENABLE TELEGRAM" core/modules/monitoring/config/alerting/contact-points.yml
# Expected: 1 (swap instructions present)
```

---

## 6. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| litellm healthcheck start_period увеличен → медленнее healthy (120s вместо 60s) | LOW | На production это незаметно (однократно при старте). Docker Compose `--wait` всё равно ждёт healthy. |
| template удалён из mounts → deploy-project.sh должен работать | LOW | on-project-deploy.sh рендерит template независимо. Удаление mount не затрагивает deploy-flow. |
| EXPECTED_DASHBOARDS изменён → тесты мониторинга могут упасть | LOW | Файл физически остаётся в директории, тесты адаптированы. |

---

## 7. Pre-existing Wave 1 Failures (NOT in scope)

Следующие failures documented в VerificationReport 029 (Wave 2), но НЕ включены в этот DevPlan:

1. `tests/test_unit_provision_environment.py` — pre-existing до DevPlan 031 фикса. Должны быть исправлены фиксом B1 (если нет — отдельный fixup).
2. `tests/test_adopt_project_org_validation.py` — args.sh extraction нарушила `_extract_func('usage')`. Требует отдельного fixup (не блокирует этот DevPlan).

---

## 8. Execution Order

1. B1: verify `make gate MODE=fast` → provision тесты green (если нет — применить фикс из DevPlan 031).
2. B2: изменить litellm healthcheck параметры.
3. B3: удалить project-template.json mount, адаптировать тесты.
4. B4, B5: задокументировать реклассификацию в StatusReport 030 addendum.
5. `make gate MODE=fast` → green.
6. `make gate MODE=full` → green (кроме pre-existing macOS failures).

$END_DEVPLAN
