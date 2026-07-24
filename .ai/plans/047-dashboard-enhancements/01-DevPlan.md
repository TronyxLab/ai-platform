$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Расширение дашборда status-page — хостовая RAM, размеры в MB, визуальные улучшения, навигация, скрытые метрики
DESCRIPTION:           Wave 1 (багфиксы): RAM хоста, автоформат MB/GB/TB, удаление CI/CD бейджей, обрезка длинных image. Wave 2 (quick wins): swap, OS/kernel, app-data backup, sticky headers, quick-jump navbar, progress bars, responsive CSS. Wave 3 (глубже): port mappings, health-check log, docker version, deploy history, Loki-ссылки, dark mode.
RATIONALE:             Пользователь указал 4 конкретных дефекта. Анализ выявил ещё 13 фич с низким порогом входа (данные уже собраны или добавляются 1-2 функциями). Языковая политика: новый код — Python (host_collector, app.py), HTML/CSS — в Jinja2.
ACCEPTANCE_CRITERIA:   (1) Host Resources показывает RAM Total/Used/Free + Swap. (2) Размеры в таблицах — автоформат (B/KB/MB/GB/TB). (3) CI/CD бейджи удалены из футера. (4) Длинные image обрезаются с tooltip. (5) Sticky headers + quick-jump navbar + progress bars. (6) App-data backup отображается. (7) Все unit-тесты проходят, gate зелёный.
IMPLEMENTS:            Суперпозиция-анализ dashboard (сессия 2026-07-24)
IMPACTS:               core/internal/healthcheck/metrics/host_collector.py (+get_host_memory), core/internal/healthcheck/platform_export_metrics.py (+memory/s swap/OS в host dict), core/modules/status-page/app.py (_bytes_to_mb, enrich_projects/containers/host), core/modules/status-page/templates/status.html (CSS + HTML), tests/test_status_page.py (+тесты), tests/unit/test_host_collector.py (новый)
REQUIRES:              Python >= 3.10, доступ к /proc/meminfo (Linux VPS), Jinja2 autoescape (уже есть), `make fix-gate && make gate MODE=fast` перед push
$END_ARTIFACT_CONTRACT

$DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL W1-RAM: Добавить get_host_memory() в host_collector.py → GOAL_HOST_RAM
- GOAL W1-MB: Заменить _bytes_to_gb на автоформат MB/GB/TB → GOAL_AUTOFORMAT
- GOAL W1-CLEANUP: Удалить CI/CD бейджи, обрезать длинные image → GOAL_CLEANUP
- GOAL W2-SWAP: Добавить swap в host_collector.py → GOAL_HOST_SWAP
- GOAL W2-OS: Добавить OS/kernel в host_collector.py → GOAL_HOST_OS
- GOAL W2-BACKUP: Отобразить app-data backup в HTML → GOAL_APP_BACKUP
- GOAL W2-CSS: Sticky headers, quick-jump, progress bars, responsive → GOAL_CSS
- GOAL W3-DEEP: Port mappings, health logs, docker ver, deploy history, Loki links, dark mode → GOAL_W3
- GOAL TEST: Покрыть unit-тестами новые коллекторы + HTML-структуру → GOAL_TEST
**SECTION_USE_CASES:**
- USE_CASE Оператор открывает platform.tronyx.ru чтобы оценить здоровье ноды → SCENARIO_OPS
- USE_CASE Оператор ищет причину падения контейнера → SCENARIO_DEBUG
- USE_CASE Оператор оценивает остаток свободной RAM перед деплоем → SCENARIO_CAPACITY
- USE_CASE Оператор на мобильном проверяет статус → SCENARIO_MOBILE
$END_DOCUMENT_PLAN

---

# DevPlan: Расширение дашборда status-page

**План #:** 047
**Дата:** 2026-07-24
**Статус:** В разработке

---

## 1. Requirements Analysis

### Ключевые критерии успеха

1. **Host Resources дополнены:** оператор видит RAM (total/used/free), Swap, OS/Kernel версию на Host Resources таблице
2. **Размеры читаемы:** маленькие значения показываются в KB/MB, большие — в GB/TB, а не "0.00 GB" для 50MB
3. **Таблицы не разъезжаются:** длинные image-названия обрезаются с `text-overflow: ellipsis` + tooltip
4. **Навигация:** sticky headers + quick-jump navbar — не нужно скроллить вверх для перехода между таблицами
5. **Скрытые данные раскрыты:** app-data backup, port mappings, health-check failure log
6. **Тесты:** все новые коллекторы покрыты unit-тестами, gate зелёный

### Диагностика текущих проблем

| # | Проблема | Root Cause | Файл |
|---|----------|-----------|------|
| P1 | Нет RAM хоста | `host_collector.py::get_host_disk()` собирает только диск; нет `/proc/meminfo` парсера | `host_collector.py` |
| P2 | Размеры в GB нечитаемы | `_bytes_to_gb()` всегда делит на 1024³; для 50MB выводит "0.05 GB" | `app.py:616,624` |
| P3 | CI/CD бейджи — хардкод | Строки 274-279 шаблона — ручной текст от wave D067, дата 2026-07-24 зашита | `status.html:274-279` |
| P4 | Длинные image ломают таблицу | Колонка Image без CSS-ограничения ширины, нет `text-overflow` | `status.html` |
| P5 | App-data backup скрыт | `backup_collector.py` собирает `last_app_data_at`, но HTML показывает только postgres | `app.py:765-766`, `status.html:245-263` |

---

## 2. Draft Code Graph

```xml
<graph>
  <!-- Wave 1: Host RAM collector -->
  <entity id="host_collector_get_host_memory_FUNC" type="FUNCTION" file="core/internal/healthcheck/metrics/host_collector.py" line="new">
    <keyword>host</keyword>
    <keyword>memory</keyword>
    <keyword>RAM</keyword>
    <annotation>NEW: читает /proc/meminfo → MemTotal, MemAvailable, SwapTotal, SwapFree. Возвращает dict {memory_total_gb, memory_available_gb, memory_used_percent, swap_total_gb, swap_free_gb, swap_used_percent}.</annotation>
    <CrossLinks>
      <link target="host_collector_get_host_uptime_FUNC" relation="sibling"/>
      <link target="platform_export_metrics_main_FUNC" relation="called-by"/>
    </CrossLinks>
  </entity>

  <entity id="host_collector_get_host_uname_FUNC" type="FUNCTION" file="core/internal/healthcheck/metrics/host_collector.py" line="new">
    <keyword>host</keyword>
    <keyword>OS</keyword>
    <keyword>kernel</keyword>
    <annotation>NEW: os.uname() → {os_name, kernel_version, arch}. Zero-cost stdlib call.</annotation>
    <CrossLinks>
      <link target="platform_export_metrics_main_FUNC" relation="called-by"/>
    </CrossLinks>
  </entity>

  <!-- Wave 1: Auto-format bytes -->
  <entity id="app_format_bytes_human_FUNC" type="FUNCTION" file="core/modules/status-page/app.py" line="replace _bytes_to_gb + _bytes_to_gb_str">
    <keyword>format</keyword>
    <keyword>bytes</keyword>
    <keyword>MB</keyword>
    <annotation>REPLACE: _bytes_to_gb() + _bytes_to_gb_str() → единая _format_bytes(bytes, precision=1). Автовыбор единицы: B→KB→MB→GB→TB. Пороги: &lt;1024→B, &lt;1024²→KB, &lt;1024³→MB, ≥1024³→GB, ≥1024⁴→TB.</annotation>
    <CrossLinks>
      <link target="app_enrich_projects_FUNC" relation="called-by"/>
      <link target="app_enrich_containers_FUNC" relation="called-by"/>
      <link target="app_render_html_FUNC" relation="called-by"/>
    </CrossLinks>
  </entity>

  <!-- Wave 1: CSS fixes -->
  <entity id="status_html_css_fixes_STYLE" type="STYLE" file="core/modules/status-page/templates/status.html" line="9-50">
    <keyword>CSS</keyword>
    <keyword>ellipsis</keyword>
    <keyword>sticky</keyword>
    <keyword>responsive</keyword>
    <annotation>MODIFY: (1) .image-col {max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}, (2) thead {position:sticky;top:0;z-index:1}, (3) .table-wrap {overflow-x:auto}, (4) .progress-bar {height:8px;border-radius:4px;background:#eee}, (5) footer: удалить CI/CD бейджи</annotation>
  </entity>

  <!-- Wave 2: Progress bars + navbar -->
  <entity id="status_html_navbar_DIV" type="ELEMENT" file="core/modules/status-page/templates/status.html" line="after body">
    <keyword>navigation</keyword>
    <keyword>quick-jump</keyword>
    <annotation>NEW: &lt;nav class="quick-nav"&gt; с якорями: #services, #projects, #containers, #host</annotation>
  </entity>

  <!-- Wave 1: Metrics export coordinator — new memory step -->
  <entity id="platform_export_metrics_main_FUNC" type="FUNCTION" file="core/internal/healthcheck/platform_export_metrics.py" line="after host_uptime">
    <keyword>coordinator</keyword>
    <keyword>memory</keyword>
    <keyword>swap</keyword>
    <keyword>OS</keyword>
    <annotation>MODIFY: добавить шаги 6d (host memory), 6e (host uname) после шага 6c (docker images size). Всегда fresh — без кэша.</annotation>
    <CrossLinks>
      <link target="host_collector_get_host_memory_FUNC" relation="calls"/>
      <link target="host_collector_get_host_uname_FUNC" relation="calls"/>
    </CrossLinks>
  </entity>

  <!-- Wave 1: app.py — pass new host fields to template -->
  <entity id="app_render_html_FUNC" type="FUNCTION" file="core/modules/status-page/app.py" line="784-805">
    <keyword>render</keyword>
    <keyword>host</keyword>
    <keyword>backup</keyword>
    <annotation>MODIFY: host dict дополнить memory_*, swap_*, os_* полями. backup dict передать целиком (включая last_app_data_at).</annotation>
    <CrossLinks>
      <link target="status_html" relation="passes-context"/>
    </CrossLinks>
  </entity>

  <!-- Wave 2: Status HTML — Host Resources table expanded -->
  <entity id="status_html_host_table_TABLE" type="ELEMENT" file="core/modules/status-page/templates/status.html" line="199-267">
    <keyword>host</keyword>
    <keyword>memory</keyword>
    <keyword>swap</keyword>
    <keyword>OS</keyword>
    <keyword>backup</keyword>
    <annotation>MODIFY: добавить строки: RAM Total/Used/Free (с progress bar), Swap Total/Used (с progress bar), OS/Kernel, App-Data Backup. Убрать хардкод-бейджи.</annotation>
    <CrossLinks>
      <link target="app_render_html_FUNC" relation="receives-context"/>
    </CrossLinks>
  </entity>

  <!-- Tests -->
  <entity id="test_host_collector_memory_FUNC" type="TEST" file="tests/unit/test_host_collector.py" line="new">
    <keyword>unit-test</keyword>
    <keyword>host-collector</keyword>
    <keyword>memory</keyword>
    <annotation>NEW: test_get_host_memory() — mock /proc/meminfo, verify parsed values. test_get_host_uname() — mock os.uname, verify OS fields.</annotation>
  </entity>

  <entity id="test_status_page_format_bytes_FUNC" type="TEST" file="tests/test_status_page.py" line="new">
    <keyword>unit-test</keyword>
    <keyword>format-bytes</keyword>
    <annotation>NEW: test_format_bytes_autoscale() — verify 500→"500 B", 1024→"1.0 KB", 1048576→"1.0 MB", 1073741824→"1.0 GB".</annotation>
  </entity>

  <entity id="test_status_page_html_structure_FUNC" type="TEST" file="tests/test_status_page.py" line="340">
    <keyword>unit-test</keyword>
    <keyword>HTML-structure</keyword>
    <annotation>MODIFY: test_html_structure — добавить проверки на наличие memory_*, swap_*, os_* полей в host dict, отсутствие CI/CD бейджей, наличие quick-nav, progress-bar элементов.</annotation>
  </entity>
</graph>
```

---

## 3. Step-by-Step Data Flow

### Flow 1: RAM → Dashboard

```
┌─ /proc/meminfo (Linux VPS) ────────────┐
│  MemTotal:    16234500 kB               │
│  MemAvailable: 8234560 kB               │
│  SwapTotal:    4194300 kB               │
│  SwapFree:     3900123 kB               │
└────────────────────────────────────────┘
                    │
                    ▼ get_host_memory()
┌─ host_collector.py ────────────────────┐
│  def get_host_memory() -> dict:         │
│    parse /proc/meminfo (key: value kB)  │
│    → memory_total_gb (float)            │
│    → memory_available_gb (float)        │
│    → memory_used_percent (float)        │
│    → swap_total_gb (float)              │
│    → swap_free_gb (float)               │
│    → swap_used_percent (float)          │
│    Graceful: FileNotFoundError → zeros  │
└────────────────────────────────────────┘
                    │
                    ▼ step 6d in main()
┌─ platform_export_metrics.py ───────────┐
│  host.update(get_host_memory())         │
│  host.update(get_host_uname())          │
│  → status-metrics.json                 │
│    {                                     │
│      "host": {                           │
│        "disk_total_gb": 50.0,            │
│        "memory_total_gb": 15.5,          │
│        "memory_available_gb": 7.9,       │
│        "memory_used_percent": 49.1,      │
│        "swap_total_gb": 4.0,             │
│        "swap_free_gb": 3.7,              │
│        "swap_used_percent": 7.0,         │
│        "os_name": "Linux",               │
│        "kernel_version": "6.1.0",        │
│        "arch": "x86_64"                  │
│      }                                   │
│    }                                     │
└────────────────────────────────────────┘
                    │
                    ▼ _render_html()
┌─ app.py ───────────────────────────────┐
│  host = metrics.get("host", {})          │
│  context["host"]["memory_total_gb"]      │
│    = host.get("memory_total_gb", 0)      │
│  context["host"]["memory_available_gb"]  │
│    = host.get("memory_available_gb", 0)  │
│  context["host"]["memory_used_percent"]  │
│    = host.get("memory_used_percent", 0)  │
│  ... (swap, os fields аналогично)         │
│  _format_bytes(size_bytes) → "14.7 GB"  │
└────────────────────────────────────────┘
                    │
                    ▼ Jinja2 render
┌─ status.html ──────────────────────────┐
│  Host Resources table:                   │
│  ┌─────────────────────────────────────┐ │
│  │ RAM Total      │ 15.5 GB           │ │
│  │ RAM Available  │ 7.9 GB            │ │
│  │ RAM Used       │ 49.1%  ████░░░░░░ │ │
│  │ Swap Total     │ 4.0 GB            │ │
│  │ Swap Used      │ 7.0%   █░░░░░░░░░ │ │
│  │ OS / Kernel    │ Linux 6.1.0 x86_64│ │
│  │ App-Data Bkp   │ 2026-07-24T10:00Z │ │
│  └─────────────────────────────────────┘ │
└────────────────────────────────────────┘
```

### Flow 2: Auto-format bytes

```
┌─ _format_bytes(bytes_val, precision=1) ─┐
│  if bytes_val < 1024:                    │
│    return f"{bytes_val} B"               │
│  elif bytes_val < 1024**2:               │
│    return f"{bytes_val/1024:.1f} KB"     │
│  elif bytes_val < 1024**3:               │
│    return f"{bytes_val/1024**2:.1f} MB"  │
│  elif bytes_val < 1024**4:               │
│    return f"{bytes_val/1024**3:.1f} GB"  │
│  else:                                   │
│    return f"{bytes_val/1024**4:.1f} TB"  │
└──────────────────────────────────────────┘

Примеры:
  500 B        → "500 B"
  1024 B       → "1.0 KB"
  1536000 B    → "1.5 MB"
  536870912 B  → "512.0 MB"
  1073741824 B → "1.0 GB"
  0            → "0 B"
```

### Flow 3: CSS fixes

```
До:
  <td>{{ c.image }}</td>
  → ghcr.io/tronyx161/hermes-agent:l1-latest-20260724-abcdef123456
  → таблица раздвигается на 400px вправо

После:
  <td class="image-col" title="{{ c.image }}">{{ c.image }}</td>
  CSS: .image-col { max-width: 200px; overflow: hidden;
                    text-overflow: ellipsis; white-space: nowrap; }
  → "ghcr.io/tronyx161/herme..."
  → при наведении — tooltip с полным именем
```

---

## 4. $TASKS — Atomic Task Decomposition

### Task List

| ID | Задача | Роль | Файлы | Сложность | Зависит от |
|----|--------|------|-------|-----------|------------|
| **T1** | `get_host_memory()` — парсер /proc/meminfo | Coder | `host_collector.py` | 3 | — |
| **T2** | `get_host_uname()` — OS/kernel/arch | Coder | `host_collector.py` | 1 | — |
| **T3** | Coordinator: шаги 6d (memory) + 6e (uname) | Coder | `platform_export_metrics.py` | 2 | T1, T2 |
| **T4** | `_format_bytes()` — автоформат B/KB/MB/GB/TB | Coder | `app.py` | 2 | — |
| **T5** | `_enrich_projects` + `_enrich_containers` → `_format_bytes()` | Coder | `app.py` | 1 | T4 |
| **T6** | `_render_html()` — host context: memory, swap, OS, backup | Coder | `app.py` | 2 | T3 |
| **T7** | Удалить CI/CD бейджи из футера | Coder | `status.html` | 1 | — |
| **T8** | CSS: image truncation + sticky thead + responsive + progress bars | Coder | `status.html` | 2 | — |
| **T9** | Quick-jump navbar (якоря) | Coder | `status.html` | 1 | — |
| **T10** | Host Resources table: RAM, Swap, OS, App-Data Backup строки | Coder | `status.html` | 3 | T6, T8 |
| **T11** | Unit-тесты: `test_host_collector.py` — memory + uname | Coder | `tests/unit/test_host_collector.py` | 2 | T1, T2 |
| **T12** | Unit-тесты: `test_format_bytes_autoscale` | Coder | `tests/test_status_page.py` | 2 | T4 |
| **T13** | Unit-тесты: HTML-structure (memory, swap, OS, backup, no CI/CD) | Coder | `tests/test_status_page.py` | 2 | T6, T7, T10 |
| **T14** | `make fix-gate && make gate MODE=fast` | Coder | — | 1 | T1-T13 |
| **T15** | (Опционально) Wave 3: port mappings, health log, docker ver, Loki links, dark mode | Coder | `docker_collector.py`, `app.py`, `status.html` | 5 | T14 |

### Merge Rule — микро-задачи, объединяемые в родительские

- **T2** (get_host_uname, 1 файл, <10 строк) → объединить с **T1** в T1-combined (`host_collector.py` — обе функции в одном файле)
- **T5** (_enrich_ → _format_bytes, 1 файл, <5 строк) → объединить с **T4** в T4-combined
- **T7** (удаление бейджей, 1 файл, <5 строк) → объединить с **T10** в T10-combined
- **T9** (navbar, 1 файл, <10 строк) → объединить с **T8** в T8-combined

### Итоговый список задач (после merge)

| ID | Задача | Файлы | Сложность | Зависит от |
|----|--------|-------|-----------|------------|
| **T1** | `get_host_memory()` + `get_host_uname()` — /proc/meminfo + os.uname | `host_collector.py` | 3 | — |
| **T2** | Coordinator: шаги 6d (memory) + 6e (uname) | `platform_export_metrics.py` | 2 | T1 |
| **T3** | `_format_bytes()` + enrich-функции → автоформат | `app.py` | 2 | — |
| **T4** | `_render_html()` — host context: memory, swap, OS, backup поля | `app.py` | 2 | T2 |
| **T5** | CSS: image truncation + sticky + responsive + progress bars + navbar + удаление CI/CD | `status.html` | 3 | — |
| **T6** | Host Resources table: RAM, Swap, OS, App-Data Backup строки | `status.html` | 3 | T4, T5 |
| **T7** | Unit-тесты: host_collector (memory + uname) | `tests/unit/test_host_collector.py` | 2 | T1 |
| **T8** | Unit-тесты: format_bytes + HTML structure | `tests/test_status_page.py` | 2 | T3, T6 |
| **T9** | Gate: `make fix-gate && make gate MODE=fast` | — | 1 | T1-T8 |

---

## 5. $PARALLEL_GROUPS

### File Intersection Matrix

| Задача | host_collector.py | platform_export_metrics.py | app.py | status.html | tests/unit/test_host_collector.py | tests/test_status_page.py |
|--------|:--:|:--:|:--:|:--:|:--:|:--:|
| T1 | X | | | | | |
| T2 | | X | | | | |
| T3 | | | X | | | |
| T4 | | | X | | | |
| T5 | | | | X | | |
| T6 | | | | X | | |
| T7 | | | | | X | |
| T8 | | | | | | X |
| T9 | — | — | — | — | — | — |

### Wave grouping

```
### Wave 1 (independent, no shared files)
- T1 (host_collector.py — memory + uname)
- T3 (app.py — format_bytes)
- T5 (status.html — CSS + navbar + cleanup)
Комментарий: T1, T3, T5 — разные файлы, можно параллельно.

### Wave 2 (depend on Wave 1)
- T2 (platform_export_metrics.py — depends on T1)
- T4 (app.py — depends on T2, but T2 from Wave 2; фактически depends on T2⚠️)
- T7 (tests/unit/test_host_collector.py — depends on T1)
Комментарий: T2 зависит от T1. T4 зависит от T2. T7 зависит от T1.

### Wave 3 (depend on Wave 2)
- T6 (status.html — depends on T4 + T5)
- T8 (tests/test_status_page.py — depends on T3 + T6)

### Wave 4 (verification)
- T9 (gate: fix-gate + gate MODE=fast)
```

**Critical path:** T1 → T2 → T4 → T6 → T8 → T9 (6 шагов)

---

## 6. Acceptance Criteria

| # | Критерий | Проверка | Приоритет |
|---|----------|----------|-----------|
| AC1 | `get_host_memory()` возвращает memory_total_gb, memory_available_gb, swap_* из /proc/meminfo | Unit-тест T7 с mock /proc/meminfo | W1 |
| AC2 | `get_host_uname()` возвращает os_name, kernel_version, arch | Unit-тест T7 с mock os.uname | W2 |
| AC3 | `platform_export_metrics.py` включает memory + swap + OS в host dict | Интеграционный тест (dry-run на macOS: graceful zeros) | W1 |
| AC4 | `_format_bytes(1536000)` → `"1.5 MB"`, `_format_bytes(1073741824)` → `"1.0 GB"` | Unit-тест T8 | W1 |
| AC5 | HTML содержит строки RAM Total/Used/Free, Swap, OS/Kernel, App-Data Backup | Unit-тест T8: `assert "RAM Total" in html` | W1 |
| AC6 | CI/CD бейджи удалены из футера | Unit-тест T8: `assert "CI/CD Pipeline Verified" not in html` | W1 |
| AC7 | Колонка Image имеет CSS `text-overflow: ellipsis`, не раздвигает таблицу | Визуально на VPS + CSS-assert в тесте | W1 |
| AC8 | Sticky thead работает при скролле | Визуально на VPS | W2 |
| AC9 | Quick-jump navbar отображается и ссылки работают | Unit-тест: `<nav class="quick-nav">` в HTML | W2 |
| AC10 | Progress bars для Disk%, RAM%, Swap% рендерятся | Unit-тест: `<div class="progress-bar">` в HTML | W2 |
| AC11 | `make gate MODE=fast` зелёный | Локальный запуск | ALL |
| AC12 | Все размеры в dashboard отображаются в адекватных единицах (не "0.00 GB") | Визуально на VPS | W1 |

---

## 7. File Manifest

| # | Файл | Действие | Изменение |
|---|------|----------|-----------|
| 1 | `core/internal/healthcheck/metrics/host_collector.py` | **Изменить** | Добавить `get_host_memory()` (парсинг /proc/meminfo, строки ~40) + `get_host_uname()` (os.uname, строки ~10) |
| 2 | `core/internal/healthcheck/platform_export_metrics.py` | **Изменить** | Добавить шаги 6d (`host.update(get_host_memory())`) и 6e (`host.update(get_host_uname())`) после строки 183 |
| 3 | `core/modules/status-page/app.py` | **Изменить** | (a) Заменить `_bytes_to_gb()` + `_bytes_to_gb_str()` на `_format_bytes()` (~20 строк), (b) Обновить `_enrich_projects()` — `code_size`/`image_size` через `_format_bytes`, (c) Обновить `_enrich_containers()` — `memory_used`/`memory_limit`/`image_size_gb` через `_format_bytes`, (d) `_render_html()` — host dict дополнить memory_*, swap_*, os_* полями, backup передать целиком |
| 4 | `core/modules/status-page/templates/status.html` | **Изменить** | (a) CSS: `.image-col`, `thead sticky`, `.table-wrap`, `.progress-bar`, `.quick-nav`, `@media`, (b) Удалить CI/CD бейджи (строки 274-279), (c) Добавить quick-jump navbar после `<body>`, (d) Host Resources: добавить строки RAM, Swap, OS/Kernel, App-Data Backup, (e) Image колонку обернуть в `<td class="image-col" title="...">`, (f) Disk% → `<div class="progress-bar">` |
| 5 | `tests/unit/test_host_collector.py` | **Создать** | Unit-тесты для `get_host_memory()` (mock /proc/meminfo) и `get_host_uname()` (mock os.uname) |
| 6 | `tests/test_status_page.py` | **Изменить** | Добавить `test_format_bytes_autoscale()` и расширить `test_html_structure()` проверками новых полей |

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_host_collector.py` | `test_get_host_memory_parses_meminfo` | /proc/meminfo с MemTotal, MemAvailable, SwapTotal, SwapFree → verify все 6 float полей | `host_collector.get_host_memory()` |
| `tests/unit/test_host_collector.py` | `test_get_host_memory_file_not_found` | /proc/meminfo отсутствует → все поля = 0, функция не падает | `host_collector.get_host_memory()` |
| `tests/unit/test_host_collector.py` | `test_get_host_uname_returns_os_fields` | os.uname() возвращает sysname='Linux', release='6.1.0', machine='x86_64' → verify dict | `host_collector.get_host_uname()` |
| `tests/unit/test_host_collector.py` | `test_get_host_uname_os_error` | os.uname() кидает OSError → все поля = 'unknown', не падает | `host_collector.get_host_uname()` |
| `tests/test_status_page.py` | `test_format_bytes_autoscale` | _format_bytes(500) → "500 B", _format_bytes(1024) → "1.0 KB", _format_bytes(1536000) → "1.5 MB", _format_bytes(1073741824) → "1.0 GB", _format_bytes(0) → "0 B" | `app._format_bytes()` |
| `tests/test_status_page.py` | `test_html_structure_has_memory_fields` | HTML содержит "RAM Total", "RAM Available", "Swap Total" | `app._render_html()` |
| `tests/test_status_page.py` | `test_html_structure_has_os_fields` | HTML содержит "OS / Kernel" и kernel_version | `app._render_html()` |
| `tests/test_status_page.py` | `test_html_structure_no_cicd_badges` | HTML НЕ содержит "CI/CD Pipeline Verified" | `app._render_html()` |
| `tests/test_status_page.py` | `test_html_structure_has_quick_nav` | HTML содержит `<nav class="quick-nav">` и якоря `#services`, `#projects` | `app._render_html()` |
| `tests/test_status_page.py` | `test_html_structure_has_progress_bars` | HTML содержит `<div class="progress-bar">` для Disk | `app._render_html()` |
| `tests/test_status_page.py` | `test_html_structure_has_app_data_backup` | HTML содержит "App-Data Backup" (если backup.last_app_data_at не None) | `app._render_html()` |

---

## 9. Implementation Details

### T1: `get_host_memory()` + `get_host_uname()` — host_collector.py

```python
# region FUNC_get_host_memory
## @purpose  Collect host RAM + Swap from /proc/meminfo
## @io       ⇥ (none, reads /proc/meminfo) → ⎋ dict
## @complexity  O(1) — single file read
def get_host_memory() -> dict:
    """Get host memory and swap usage from /proc/meminfo.

    # ▶ /proc/meminfo → parse MemTotal, MemAvailable, SwapTotal, SwapFree
    #    → ⊕ memory_total_gb, memory_available_gb, memory_used_percent
    #    → ⊕ swap_total_gb, swap_free_gb, swap_used_percent → ⎋ dict

    Graceful degradation: zeros on FileNotFoundError or parse error.
    Uses kB values from meminfo (kernel reports in kB).
    """
    _logger = logging.getLogger(__name__)
    result = {
        "memory_total_gb": 0.0,
        "memory_available_gb": 0.0,
        "memory_used_percent": 0.0,
        "swap_total_gb": 0.0,
        "swap_free_gb": 0.0,
        "swap_used_percent": 0.0,
    }

    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val_str = parts[1].strip().split()[0]  # "16234500 kB" → "16234500"
                    try:
                        meminfo[key] = int(val_str)
                    except ValueError:
                        pass

        mem_total = meminfo.get("MemTotal", 0)
        mem_available = meminfo.get("MemAvailable", 0)
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)

        if mem_total > 0:
            result["memory_total_gb"] = round(mem_total / (1024**2), 1)  # kB → GB
            result["memory_available_gb"] = round(mem_available / (1024**2), 1)
            result["memory_used_percent"] = round(
                (1 - mem_available / mem_total) * 100, 1
            )

        if swap_total > 0:
            result["swap_total_gb"] = round(swap_total / (1024**2), 1)
            result["swap_free_gb"] = round(swap_free / (1024**2), 1)
            result["swap_used_percent"] = round(
                (1 - swap_free / swap_total) * 100, 1
            )

        _logger.info(
            "[IMP:9][host_collector][get_host_memory] RAM: %.1f/%.1f GB (%.1f%%), Swap: %.1f/%.1f GB (%.1f%%)",
            result["memory_available_gb"], result["memory_total_gb"],
            result["memory_used_percent"],
            result["swap_free_gb"], result["swap_total_gb"],
            result["swap_used_percent"],
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_memory] /proc/meminfo unreadable: %s", exc)

    return result
# endregion FUNC_get_host_memory


# region FUNC_get_host_uname
## @purpose  Collect OS name, kernel version, architecture via os.uname()
## @io       ⇥ (none) → ⎋ dict — {os_name, kernel_version, arch}
## @complexity  O(1) — single stdlib call
def get_host_uname() -> dict:
    """Get OS/kernel/arch via os.uname() — zero-cost stdlib call.

    # ▶ os.uname() → ⊕ {os_name, kernel_version, arch} → ⎋ dict
    """
    _logger = logging.getLogger(__name__)
    try:
        un = os.uname()
        result = {
            "os_name": un.sysname,
            "kernel_version": un.release,
            "arch": un.machine,
        }
        _logger.info(
            "[IMP:9][host_collector][get_host_uname] OS: %s %s %s",
            result["os_name"], result["kernel_version"], result["arch"],
        )
        return result
    except (OSError, AttributeError) as exc:
        _logger.warning("[IMP:8][host_collector][get_host_uname] os.uname failed: %s", exc)
        return {"os_name": "unknown", "kernel_version": "unknown", "arch": "unknown"}
# endregion FUNC_get_host_uname
```

### T3: `_format_bytes()` — app.py

```python
# region FUNC_format_bytes
def _format_bytes(bytes_val: int, precision: int = 1) -> str:
    """Format bytes to human-readable string with auto unit selection.

    # ▶ ┌bytes_val┐ → ◇ < 1024 → "N B"
    #                  → ◇ < 1024² → "N.M KB"
    #                  → ◇ < 1024³ → "N.M MB"
    #                  → ◇ < 1024⁴ → "N.M GB"
    #                  → ⎋ "N.M TB"

    Returns "0 B" for zero/None/negative values.
    """
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    if bytes_val < 1024:
        return f"{bytes_val} B"
    if bytes_val < 1024**2:
        return f"{bytes_val / 1024:.{precision}f} KB"
    if bytes_val < 1024**3:
        return f"{bytes_val / (1024**2):.{precision}f} MB"
    if bytes_val < 1024**4:
        return f"{bytes_val / (1024**3):.{precision}f} GB"
    return f"{bytes_val / (1024**4):.{precision}f} TB"
# endregion FUNC_format_bytes
```

**Migration:** заменить все вызовы `_bytes_to_gb()` и `_bytes_to_gb_str()` на `_format_bytes()`. Старые функции удалить.

### T5 + T6: CSS + HTML changes — status.html

```html
<style>
  /* ... existing styles ... */

  /* NEW: Quick-nav */
  .quick-nav {
    display: flex; gap: 8px; margin-bottom: 1.5em;
    flex-wrap: wrap;
  }
  .quick-nav a {
    background: #3498db; color: #fff; padding: 6px 14px;
    border-radius: 6px; text-decoration: none; font-size: .9em;
    font-weight: 500;
  }
  .quick-nav a:hover { background: #2980b9; }

  /* NEW: Sticky thead */
  thead { position: sticky; top: 0; z-index: 1; }

  /* NEW: Image column truncation */
  .image-col { max-width: 200px; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; }

  /* NEW: Progress bar */
  .progress-wrap { display: flex; align-items: center; gap: 8px; }
  .progress-bar { flex: 1; height: 8px; background: #eee;
                  border-radius: 4px; overflow: hidden; }
  .progress-fill { height: 100%; border-radius: 4px;
                   transition: width .3s; }
  .progress-fill.ok { background: #27ae60; }
  .progress-fill.warn { background: #f39c12; }
  .progress-fill.critical { background: #e74c3c; }

  /* NEW: Responsive tables */
  .table-wrap { overflow-x: auto; }

  /* NEW: Dark mode */
  @media (prefers-color-scheme: dark) {
    body { background: #1a1a2e; color: #e0e0e0; }
    table { background: #16213e; box-shadow: 0 1px 3px rgba(0,0,0,.3); }
    th { background: #0f3460; color: #a0a0a0; }
    td { border-bottom-color: #1a1a2e; }
    h2 { color: #a0a0a0; }
    a { color: #5dade2; }
    .footer { color: #666; }
  }
</style>
```

```html
<!-- NEW: Quick-jump navbar -->
<nav class="quick-nav">
  <a href="#services">Platform Services</a>
  <a href="#projects">Domains / Projects</a>
  <a href="#containers">Containers</a>
  <a href="#host">Host Resources</a>
</nav>
```

Хост-таблица (новые строки):

```html
{% if host.memory_total_gb is not none and host.memory_total_gb > 0 %}
<tr>
  <td>RAM Total</td>
  <td>{{ "%.1f"|format(host.memory_total_gb) }} GB</td>
</tr>
<tr>
  <td>RAM Available</td>
  <td>{{ "%.1f"|format(host.memory_available_gb) }} GB</td>
</tr>
<tr>
  <td>RAM Used</td>
  <td>
    <div class="progress-wrap">
      <span>{{ "%.1f"|format(host.memory_used_percent) }}%</span>
      <div class="progress-bar">
        <div class="progress-fill {% if host.memory_used_percent > 90 %}critical{% elif host.memory_used_percent > 75 %}warn{% else %}ok{% endif %}"
             style="width:{{ host.memory_used_percent }}%"></div>
      </div>
    </div>
  </td>
</tr>
{% endif %}
{% if host.swap_total_gb is not none and host.swap_total_gb > 0 %}
<tr>
  <td>Swap Total</td>
  <td>{{ "%.1f"|format(host.swap_total_gb) }} GB</td>
</tr>
<tr>
  <td>Swap Used</td>
  <td>
    <div class="progress-wrap">
      <span>{{ "%.1f"|format(host.swap_used_percent) }}%</span>
      <div class="progress-bar">
        <div class="progress-fill {% if host.swap_used_percent > 50 %}warn{% else %}ok{% endif %}"
             style="width:{{ host.swap_used_percent }}%"></div>
      </div>
    </div>
  </td>
</tr>
{% endif %}
{% if host.os_name %}
<tr>
  <td>OS / Kernel</td>
  <td>{{ host.os_name }} {{ host.kernel_version }} ({{ host.arch }})</td>
</tr>
{% endif %}
```

---

## 10. Rollback Plan

1. **T1-T2 (host_collector):** `git revert <commit>` — восстанавливает старый host_collector.py. Memory/Swap/OS поля исчезают из metrics, app.py использует `.get()` с default=0 — безопасно.
2. **T3 (format_bytes):** `git revert` — возвращает `_bytes_to_gb()`. Размеры снова в GB.
3. **T5-T6 (HTML/CSS):** `git revert` — возвращает старый шаблон. Новые Jinja2-переменные (host.memory_*) отсутствуют в контексте → `{% if ... %}` блоки просто не рендерятся.
4. **Tests:** Revert тестовых файлов вместе с production-кодом.

---

## 11. Связанные артефакты

| Файл | Роль |
|------|------|
| `core/modules/status-page/app.py` | Основной сервер — рендеринг и enrich |
| `core/modules/status-page/templates/status.html` | Jinja2 HTML-шаблон |
| `core/internal/healthcheck/metrics/host_collector.py` | Коллектор хост-метрик |
| `core/internal/healthcheck/platform_export_metrics.py` | Координатор экспорта метрик |
| `core/internal/healthcheck/metrics/backup_collector.py` | Коллектор статуса бэкапов |
| `core/internal/healthcheck/metrics/docker_collector.py` | Коллектор контейнеров |
| `tests/test_status_page.py` | Unit-тесты status-page |
| `core/AGENTS.md` | Языковая политика, слои |

$END_DEVPLAN
