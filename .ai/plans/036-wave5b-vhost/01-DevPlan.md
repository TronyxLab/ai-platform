$START_DEVPLAN

# DevPlan 036B — Wave 5b: Strangler-Fig add-vhost.sh → vhost_renderer.py

$ARTIFACT_CONTRACT
- **PURPOSE:** Декомпозиция `add-vhost.sh` (926 LOC shell-монолит) в `core/internal/scaffold/vhost_renderer.py` (~500 LOC Python core) + shell-фасад ≤150 LOC с 0 inline python3, по методологии Strangler-Fig из DevPlan 036 Wave 2a.
- **DESCRIPTION:** add-vhost.sh — центральный генератор nginx vhost-конфигов платформы. Три режима: `--add` (один проект), `--remove` (удаление), `--render-all` (batch из node.yaml). Содержит 3 inline `python3 -c` блока, 2 inline `python3` subprocess-вызова, grep-based YAML-парсинг и docker-based nginx -t harness. Python-модуль получает бизнес-логику (YAML parsing, template generation, FQDN uniqueness, render pipeline), shell остаётся тонким фасадом (parse_args → dispatch → exit).
- **RATIONALE:** Выполнение языковой политики AGENTS.md (Python-first, двухуровневый Strangler-триггер). Устранение inline python3, повышение тестируемости vhost-генерации (сейчас 0 unit-тестов), дедупликация YAML-чтения с существующим `vhost_yaml_reader.py` (будет консолидирован в новый модуль). Снижение риска nginx template regression через snapshot-тесты до миграции.
- **ACCEPTANCE_CRITERIA:**
  - AC-1: `add-vhost.sh` ≤150 LOC, 0 inline `python3 -c` / `<<PYEOF`
  - AC-2: `make render-vhosts NODE=<test>` генерирует байт-идентичные vhost-файлы относительно pre-migration baseline (детерминизм)
  - AC-3: `make render-vhosts NODE=<test>` + docker-based nginx -t harness проходит без ошибок
  - AC-4: Режимы `--add` и `--remove` работают идентично pre-migration поведению
  - AC-5: Unit-тесты ≥8 штук, покрывают generate_vhost_body, check_duplicate_domains, resolve_cert_domain, read_project_yaml, read_node_yaml_projects, nginx_t_harness
  - AC-6: `make test` и `make gate MODE=fast` — зелёные (с учётом pre-existing BASELINE-1 gate failure в test_gate_deploy_paths.py)
  - AC-7: Все 3 TRAP из оригинального add-vhost.sh документированы в Python-модуле как docstring TRAP-комментарии
- **IMPLEMENTS:** TASK-036B (Wave 2a) из DevPlan 036 — миграция add-vhost.sh в vhost_renderer.py
- **IMPACTS:**
  - `core/internal/scaffold/add-vhost.sh` (926→~150 LOC) — shell facade
  - `core/internal/scaffold/vhost_renderer.py` (NEW, ~500 LOC) — Python core
  - `core/internal/scaffold/vhost_yaml_reader.py` (74 LOC) — консолидируется в vhost_renderer.py, файл удаляется
  - `tests/unit/test_vhost_renderer.py` (NEW, ~350 LOC) — unit-тесты
  - `core/lib/logging.sh` — не меняется (shell facade продолжает использовать)
  - `core/lib/python_deps.sh` — не меняется (shell facade source'ит)
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/internal/shared/content_hash.py` (уже существует, делегирование compute_body_hash)
  - `docker` (для nginx_t_harness — опционально, fallback WARN при отсутствии)
$END_ARTIFACT_CONTRACT

---

## Debt Intake

### TRAP audit (add-vhost.sh)

Перед проектированием проведён аудит TRAP-аннотаций в целевом файле и смежных артефактах:

| # | TRAP | Строки | Тип | Статус |
|---|------|--------|-----|--------|
| T1 | `TRAP[BUG] pipefail+\|\| chain` | L102-106 | BUG | **FIXED** — compute_body_hash уже делегирует content_hash.py. В Python-модуле делегирование сохраняется, pipefail-проблема исчезает автоматически. |
| T2 | `TRAP[BUG] DRIFT-1 flat directory` | L445-451 | BUG | **IN_SCOPE** — переносится в vhost_renderer.py как docstring TRAP для render_vhost(). Flat-directory constraint остаётся критичным (нарушение → vhost не загружается, class D12). |
| T3 | `TRAP[DECISION] harness vhost isolation` | L628-633 | DECISION | **IN_SCOPE** — переносится в vhost_renderer.py для nginx_t_harness(). Изоляция vhosts от harness support files остаётся архитектурным требованием. |
| T4 | Inline `python3 -c` в check_duplicate_domains | L548-564 | DEBT | **FIXED** — бизнес-логика извлекается в Python-метод `check_duplicate_domains()`. |
| T5 | Inline `python3 -c` в render_all loop | L779-780 | DEBT | **FIXED** — цикл render_all() полностью в Python, JSON-парсинг нативный. |
| T6 | `TRAP[DECISION] export NODE_YAML_PATH` | L733-738 | DECISION | **FIXED** — Python-модуль читает YAML напрямую, env-var хак не нужен. |

### DEBT registries

DEBT-файлов в `.ai/plans/036-wave5b-vhost/` нет (новая папка). Смежные DEBT из DevPlan 036 — все IN_SCOPE (см. таблицу выше).

### Pre-existing baseline issues

- **BASELINE-1:** `tests/gates/test_gate_deploy_paths.py:151` — pre-existing gate failure, не связан с данной миграцией. Документирован в DevPlan 036 Risk Assessment. Рекомендуется исправить ДО начала Wave 5b для чистоты gate-результатов.

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Детерминизм вывода:** повторный `render-all` при неизменном `node.yaml` → байт-идентичные `.conf` файлы (проверяется через `diff -r` pre/post миграции)
2. **Zero inline python3:** grep по shell-фасаду → 0 совпадений `python3 -c` и `<<PYEOF`
3. **Сохранение nginx -t harness:** docker-based валидация сгенерированных vhost'ов остаётся рабочей (тестируется через unit-тест с mocked docker)
4. **FQDN uniqueness enforcement:** дубликат домена → exit 2 (как сейчас), тестируется unit-тестом
5. **Консолидация vhost_yaml_reader.py:** существующий модуль (74 LOC) поглощается vhost_renderer.py, его публичный API (`read_projects`) сохраняется как метод

### Текущее состояние (baseline)

| Метрика | Значение |
|---------|----------|
| LOC shell | 926 |
| Inline python3 -c блоков | 3 (L548-564, L779, L780) |
| Inline python3 subprocess вызовов | 2 (L271, L741 — вызов vhost_yaml_reader.py) |
| Unit-тесты | 0 |
| Режимы работы | 3 (add, remove, render-all) |
| Внешние зависимости | docker (опционально), content_hash.py, vhost_yaml_reader.py, logging.sh, python_deps.sh |
| YAML-парсинг | grep-based (хрупкий) + python3/yaml fallback |

---

## Architecture Overview

### Superposition Analysis

Для миграции add-vhost.sh рассмотрены 4 стратегии:

#### Option A: Full Strangler-Fig — вся бизнес-логика в Python [score: 9/10] ⭐

**Подход:** Python-модуль получает ВСЮ бизнес-логику: YAML parsing (read_project_yaml + read_node_yaml_projects), vhost body generation, FQDN uniqueness check, render pipeline, nginx -t harness, content hash. Shell — только parse_args + dispatch.

**Trade-offs:**
- ➕ Полное устранение inline python3 (3 блока)
- ➕ Максимальная тестируемость — все функции pure Python
- ➕ Консолидация vhost_yaml_reader.py → единый модуль
- ➕ Соответствие языковой политике и Wave 4 precedent
- ➖ nginx_t_harness требует docker — сложнее тестировать (но docker — опциональный fallback)

**Best when:** команда готова к полной миграции, есть CI с docker

#### Option B: Extract-only template generator [score: 5/10]

**Подход:** Извлечь только `generate_vhost_body()` и `resolve_cert_domain()` в Python. Остальное (YAML parsing, render pipeline, harness) — остаётся в shell.

**Trade-offs:**
- ➕ Минимальный risk — только template generation меняется
- ➖ YAML parsing остаётся grep-based в shell (хрупкий)
- ➖ Inline python3 в check_duplicate_domains и render_all loop — остаются
- ➖ Не консолидирует vhost_yaml_reader.py
- ➖ Нарушает языковую политику (новый shell-код при баг-фиксах)

**Best when:** экстренный баг-фикс vhost template, нет времени на полную миграцию

#### Option C: Использовать template_engine.py [score: 3/10]

**Подход:** Делегировать генерацию nginx vhost в существующий `core/internal/template_engine.py` (Jinja2/strict regex). Vhost-шаблон → .j2 файл → render через template_engine.

**Trade-offs:**
- ➕ Переиспользование существующего механизма шаблонизации
- ➖ **FATAL:** template_engine.py использует `{{UPPER_SNAKE}}` strict regex для предотвращения коллизий с Go/Prometheus-шаблонами (`{{ $labels.x }}`, `{{instance}}`)
- ➖ **FATAL:** nginx-конфиг содержит `${host}`, `${request_uri}`, `$upstream_xxx` — nginx runtime variables. Эти переменные НЕ должны подменяться template engine. Попытка экранировать их → fragile, error-prone.
- ➖ **FATAL:** nginx-конфиг использует `set $upstream_<name>` с динамическим именем переменной — не укладывается в strict grammar `{{UPPER_SNAKE}}`
- ➖ Jinja2 режим конфликтует с `${...}` синтаксисом nginx — требует escaping, который ломается при добавлении новых nginx-директив

**Rejected:** template_engine.py предназначен для совершенно другого класса шаблонов (конфиги с UPPER_SNAKE placeholders, не nginx runtime variables). См. Design Decision D4.

#### Option D: Leave as-is [score: 2/10]

**Подход:** Оставить add-vhost.sh без изменений, только документировать TRAP'ы.

**Trade-offs:**
- ➕ Нулевой risk
- ➖ 926 LOC shell монолит остаётся в проекте
- ➖ 3 inline python3 блока — violation языковой политики при любом изменении скрипта (Tier 1 триггер)
- ➖ 0 unit-тестов для vhost генерации
- ➖ Любой баг-фикс требует правки shell с grep-based YAML parsing

**Rejected:** языковая политика требует Python для нового кода. При следующем баг-фиксе в add-vhost.sh Tier 1 триггер сработает — придётся извлекать логику в Python наспех, без плановой архитектуры.

### Multi-Dimensional Scoring

| Dimension | A (Full SF) | B (Extract) | C (template_engine) | D (Leave) |
|-----------|:---:|:---:|:---:|:---:|
| Lang policy compliance | 10 | 4 | 7 | 0 |
| Testability gain | 10 | 5 | 6 | 0 |
| Risk to production | 7 | 9 | 3 | 10 |
| Implementation speed | 7 | 8 | 5 | 10 |
| Code quality gain | 9 | 4 | 5 | 0 |
| Dedup (vhost_yaml_reader) | 10 | 2 | 5 | 0 |
| Maintenance cost (future) | 10 | 3 | 4 | 1 |
| **Composite** | **9.0** | **5.0** | **5.0** | **3.0** |

### Recommendation: Option A — Full Strangler-Fig (score: 9.0)

**Обоснование:**
1. Wave 4 успешно применил полный Strangler-Fig к топ-3 скриптам (4114→392 LOC shell). Процесс отлажен.
2. nginx_t_harness с docker — единственная сложность для unit-тестирования; решается через `subprocess.run` mock.
3. Детерминизм вывода — главный acceptance criterion — достигается естественно: Python string interpolation → byte-identical output при неизменных входах.
4. Консолидация vhost_yaml_reader.py устраняет фрагментацию ответственности между двумя модулями.
5. Option C (template_engine) категорически отклонён по D4 — механизмы несовместимы.

---

## Step-by-Step Data Flow

### Pre-migration (926 LOC shell)

```
add-vhost.sh (926 LOC)
├── parse_args() — --add/--remove/--render-all dispatch
├── compute_body_hash() — делегирует content_hash.py (уже Python)
├── generated_header() — heredoc header generation
├── read_project_yaml() — grep-based YAML parse (50 строк)
│   └── expose:true, domain, target_node extraction
├── read_node_yaml_projects() — Python yaml OR grep fallback (60 строк)
│   └── delegates to vhost_yaml_reader.py OR inline grep
├── resolve_cert_domain() — wildcard vs personal cert logic
├── generate_vhost_body() — heredoc nginx config (90 строк)
├── generate_vhost() — single-project: body→hash→header→write (60 строк)
├── remove_vhost() — delete + audit log (30 строк)
├── check_duplicate_domains() — inline python3 -c (30 строк)
├── nginx_t_harness() — docker-based nginx -t (130 строк)
├── render_all() — batch pipeline (150 строк)
│   ├── Step 1: read_node_yaml_projects → JSON lines
│   ├── Step 2: check_duplicate_domains (inline python3)
│   ├── Step 3: temp dir
│   ├── Step 4: render vhosts → temp dir (inline python3 for JSON parse)
│   ├── Step 5: nginx -t harness
│   └── Step 6: atomic mv → overlay dir
└── main() — dispatch + FQDN validation call to validate.sh
```

### Post-migration (~150 LOC shell facade + ~500 LOC Python)

```
add-vhost.sh (~150 LOC, shell facade)
├── source logging.sh + python_deps.sh
├── parse_args() — --add/--remove/--render-all dispatch
├── main():
│   ├── MODE=render-all → python3 -m core.internal.scaffold.vhost_renderer render-all --node <n> --node-configs-dir <path>
│   ├── MODE=add → python3 -m core.internal.scaffold.vhost_renderer add --project-dir <path> --node-configs-dir <path>
│   ├── MODE=remove → python3 -m core.internal.scaffold.vhost_renderer remove --project-dir <path> --node-configs-dir <path>
│   └── exit с кодом из Python
└── ⚠️ 0 inline python3 — все вызовы через `python3 -m`

core/internal/scaffold/vhost_renderer.py (~500 LOC, Python)
├── @dataclass ProjectConfig(name, domain, target_node, expose)
├── @dataclass ProjectEntry(name, domain)
├── @dataclass VhostFile(path, fqdn, project_name, body_hash)
├── @dataclass RenderResult(rendered_count, errors, harness_passed)

├── read_project_yaml(project_dir: Path) → ProjectConfig | None
│   └── PyYAML safe_load ai-platform.yaml
│       expose:true check, domain extraction, target_node extraction
│       Returns None если expose != true или domain отсутствует

├── read_node_yaml_projects(node_yaml_path: Path) → list[ProjectEntry]
│   └── PyYAML safe_load node.yaml → iterate projects[]
│       Консолидирует vhost_yaml_reader.py.read_projects() логику
│       Projects without 'domain' field → silently skipped

├── resolve_cert_domain(fqdn: str, platform_domain: str | None) → str
│   └── fqdn.endswith(f".{platform_domain}") → platform_domain (wildcard)
│       else → fqdn (personal cert)

├── generate_vhost_body(fqdn: str, project_name: str, cert_domain: str) → str
│   └── Python multi-line f-string с nginx конфигом
│       nginx_safe_name = project_name.replace('-', '_')
│       HTTP redirect (80) + HTTPS server (443 ssl, http2)
│       ssl_certificate paths, resolver 127.0.0.11, security headers include
│       rate limiting, proxy buffering, lazy DNS resolution
│       ⚠️ $host, $request_uri, $upstream_xxx — nginx runtime vars, НЕ template placeholders

├── generate_vhost_header(project_name, fqdn, node, body_hash) → str
│   └── GENERATED header block с source, domain, node, content-hash

├── compute_body_hash(content: str) → str
│   └── Делегирует core.internal.shared.content_hash (sha256)

├── check_duplicate_domains(entries: list[ProjectEntry]) → None
│   └── Set-based dedup: dict[domain, name] → raise DuplicateDomainError

├── render_vhost(entry, node, node_configs_dir) → VhostFile
│   └── resolve_cert_domain → generate_vhost_body → compute hash →
│       write vhost_file (header + body) → return VhostFile
│   ⚠️ TRAP[BUG] DRIFT-1: flat directory overlays/nginx/, no subdirectories

├── remove_vhost(fqdn, node, node_configs_dir, platform_root) → bool
│   └── Delete .conf file if exists (idempotent) + write audit-log

├── nginx_t_harness(temp_dir, nginx_version="1.28-alpine") → bool
│   └── Creates harness dir, stub nginx.conf, dev-certs, security-headers
│       SSL path swap (sed → Python re.sub), docker run nginx -t
│       ⚠️ TRAP[DECISION] harness vhost isolation: vhosts in vhosts/ subdir
│       Returns True on pass, False on fail, True if docker unavailable (WARN)

├── render_all(node_yaml_path, node_configs_dir, node) → RenderResult
│   └── 6-step pipeline:
│       1. read_node_yaml_projects(node_yaml_path) → entries
│       2. check_duplicate_domains(entries) → raise on dup
│       3. temp_dir = mkdtemp
│       4. for entry: render_vhost → temp_dir/<fqdn>.conf
│       5. nginx_t_harness(temp_dir) → if False: cleanup, raise
│       6. atomic mv: remove old GENERATED vhosts, mv new → overlay dir

└── CLI (argparse):
    ├── vhost_renderer render-all --node <n> --node-configs-dir <path>
    ├── vhost_renderer add --project-dir <path> --node-configs-dir <path>
    └── vhost_renderer remove --project-dir <path> --node-configs-dir <path>
```

### Shell facade contract

```bash
# add-vhost.sh (~150 LOC after migration)
# Все режимы:
#   render-all → python3 -m core.internal.scaffold.vhost_renderer render-all --node <n> --node-configs-dir <path>
#   add → python3 -m core.internal.scaffold.vhost_renderer add --project-dir <path> --node-configs-dir <path>
#   remove → python3 -m core.internal.scaffold.vhost_renderer remove --project-dir <path> --node-configs-dir <path>
#
# FQDN uniqueness check для режима add/remove перенесён в Python (validate.sh вызов удалён из shell-фасада —
# валидация теперь в Python-модуле через read_project_yaml + проверку FQDN)
#
# Все 3 режима возвращают exit code из Python-модуля.
# Shell facade НЕ содержит: YAML parsing, template generation, nginx harness, content hash.
```

---

## Draft Code Graph

```
core/internal/scaffold/
├── add-vhost.sh                  # → ~150 LOC (shell facade, parse_args + dispatch)
├── vhost_renderer.py             # NEW ~500 LOC (Python core)
├── vhost_yaml_reader.py          # → DELETED (логика консолидирована в vhost_renderer.py)
└── adopt-project.sh              # → вызовет vhost_renderer для configure_vhost (TASK-036C)

core/internal/shared/
└── content_hash.py               # EXISTS — делегирование compute_body_hash (без изменений)

tests/unit/
└── test_vhost_renderer.py        # NEW ~350 LOC (unit-тесты)
```

### Cross-module dependencies

```
vhost_renderer.py
├── import yaml (PyYAML — уже в проекте)
├── from core.internal.shared.content_hash import compute_hash  # делегирование
├── import subprocess (для docker run в nginx_t_harness)
├── import tempfile, os, shutil, pathlib
└── import argparse (CLI)

add-vhost.sh (shell facade)
├── source core/lib/logging.sh    # log_info, log_warn, log_crit, log_imp, log_ok, log_fail
└── source core/lib/python_deps.sh  # require_python_module (опционально)
```

---

## Design Decisions

### ## @rationale D1: Консолидация vhost_yaml_reader.py в vhost_renderer.py

**Q:** Почему не оставить vhost_yaml_reader.py как отдельный shared-модуль и импортировать его в vhost_renderer.py?

**A:** vhost_yaml_reader.py (74 LOC) — single-purpose модуль: прочитать node.yaml → отдать JSON lines проектов с domain. При Strangler-Fig миграции add-vhost.sh ВСЯ ответственность за чтение node.yaml (и read_node_yaml_projects, и render_all pipeline) переходит в vhost_renderer.py. Разделение на два файла создаёт фрагментацию:
- `read_node_yaml_projects()` используется ТОЛЬКО в контексте vhost rendering
- Никакой другой модуль не импортирует vhost_yaml_reader.py
- Консолидация устраняет 1 файл, 1 subprocess-вызов (`python3 vhost_yaml_reader.py`) и необходимость синхронизации API между двумя модулями

**Решение:** `read_projects()` из vhost_yaml_reader.py становится методом `read_node_yaml_projects()` в vhost_renderer.py. Файл vhost_yaml_reader.py удаляется.

### ## @rationale D2: validate.sh вызов удалён из shell-фасада, FQDN-валидация перенесена в Python

**Q:** Почему убран вызов `validate.sh --check-fqdn` из shell-фасада?

**A:** В оригинальном add-vhost.sh режим `--add` вызывает `validate.sh --check-fqdn` (L903-910) для проверки FQDN на дубликаты. После миграции:
- Python-модуль сам парсит `ai-platform.yaml` через PyYAML → знает domain проекта
- `check_duplicate_domains()` в Python-модуле делает ту же проверку (set-based dedup), но более надёжно (не зависит от grep)
- Вызов внешнего shell-скрипта из Python-модуля через subprocess — нарушает принцип тестируемости (требует shell-окружения)
- FQDN uniqueness для render-all уже покрывается check_duplicate_domains() внутри Python

**Решение:** shell facade НЕ вызывает validate.sh. Python-модуль самостоятельно проверяет FQDN uniqueness при add и render-all режимах.

### ## @rationale D3: nginx_t_harness — subprocess.run docker, с mocked тестами

**Q:** nginx_t_harness (130 строк shell) содержит docker run, openssl, sed, mktemp. Как это тестировать в Python?

**A:** Три уровня тестирования:
1. **Unit-тесты с mocked subprocess.run:** `test_nginx_t_harness_pass`, `test_nginx_t_harness_fail`, `test_nginx_t_harness_no_docker` — проверяют flow control (создание harness dir, вызов docker, обработка результата)
2. **Unit-тесты на хелперы:** `test_generate_harness_nginx_conf`, `test_swap_ssl_paths` — проверяют генерацию nginx.conf и замену SSL-путей (re.sub)
3. **Интеграционный тест (CI):** реальный `docker run nginx:alpine nginx -t` на сгенерированных vhost'ах — часть CI gate при `make gate MODE=full` (вне scope данного DevPlan)

**Решение:** Python-версия nginx_t_harness использует `subprocess.run(["docker", "run", ...])` с теми же аргументами, что и shell-версия. Mock-тесты покрывают flow control.

### ## @rationale D4: НЕ использовать template_engine.py для генерации nginx vhost'ов

**Q:** `generate_vhost_body()` генерирует nginx-конфиг с placeholders `${fqdn}`, `${project_name}`, `${cert_domain}`. Почему не использовать существующий `core/internal/template_engine.py`?

**A:** Фундаментальная несовместимость механизмов шаблонизации:

1. **template_engine.py использует `{{UPPER_SNAKE}}` strict regex.** Это спроектировано для предотвращения ложных срабатываний на Go/Prometheus-шаблонах (`{{ $labels.x }}`, `{{instance}}`). Nginx-конфиг не содержит `{{...}}` — он содержит nginx runtime variables.

2. **Nginx runtime variables:** `${host}`, `${request_uri}`, `$remote_addr`, `$scheme`, `$upstream_<name>` — это переменные, резолвимые nginx в runtime. Они НЕ должны подменяться никаким template engine. Синтаксис `${var}` — это стандартный nginx-синтаксис для переменных внутри строк (например, `return 301 https://$host$request_uri;`).

3. **Что требует подстановки:** `${fqdn}`, `${project_name}`, `${cert_domain}`, `${nginx_safe_name}` — это compile-time значения (известны на момент генерации vhost'а). В shell они подставляются через bash variable interpolation внутри heredoc, потому что heredoc без кавычек (`<<VHOSTBODY`) интерпретирует `${var}`.

4. **В Python — f-строки или `.format()`:** Естественный механизм — Python string interpolation. Никакой template engine не нужен — это простая подстановка 4 переменных в фиксированный шаблон.

5. **Попытка использовать template_engine.py:** Потребовала бы escape всех nginx `${var}` → `${{host}}` → fragile, ломается при добавлении новых nginx-директив с переменными. Каждый `$` в nginx-конфиге становился бы потенциальной точкой отказа.

6. **Мнение из DevPlan 036 D4 подтверждено:** «`template_engine.py` ... здесь не подходит — механизм шаблонизации nginx — это сам nginx. `generate_vhost_body()` остаётся template generator на Python (f-строки / string interpolation), но НЕ использует `template_engine.py`».

**Решение:** `generate_vhost_body()` использует Python multi-line f-string с явной подстановкой только compile-time переменных. Nginx runtime variables (`$host`, `$request_uri`, etc.) остаются как литералы в строке (экранирование не требуется, т.к. f-string использует `{var}`, а nginx — `$var`).

### ## @rationale D5: MODE dispatch — 3 отдельных subcommand вместо одного `--mode` флага

**Q:** Почему shell facade использует 3 отдельных вызова Python вместо `python3 -m vhost_renderer --mode add`?

**A:** Соответствие argparse subcommand pattern (render-all, add, remove). Три причины:
1. **Валидация аргументов на уровне argparse:** каждый subcommand имеет свой набор required/optional аргументов. `render-all` требует `--node`, `add`/`remove` требуют `--project-dir`. Единый `--mode` флаг потребовал бы ручной валидации комбинаций.
2. **Читаемость shell facade:** `python3 -m core.internal.scaffold.vhost_renderer render-all --node <n>` яснее, чем `python3 -m core.internal.scaffold.vhost_renderer --mode render-all --node <n>`.
3. **Соответствие Wave 4 precedent:** все мигрированные модули используют argparse subcommands (deploy_engine, project_adopter, overlay_deliverer).

---

## $TASKS

### TASK-036B: Strangler-Fig add-vhost.sh → vhost_renderer.py

- **Owner:** Coder
- **Output:**
  - `core/internal/scaffold/vhost_renderer.py` (~500 LOC) — Python core
  - `core/internal/scaffold/add-vhost.sh` (~150 LOC) — shell facade (обновлён)
  - `core/internal/scaffold/vhost_yaml_reader.py` — удалён
  - `tests/unit/test_vhost_renderer.py` (~350 LOC) — unit-тесты
- **Acceptance:**
  - AC-1: `add-vhost.sh` ≤150 LOC, 0 inline `python3 -c` / `<<PYEOF`
  - AC-2: `make render-vhosts NODE=<test>` генерирует байт-идентичные vhost-файлы относительно pre-migration baseline
  - AC-3: nginx -t harness проходит на сгенерированных vhost'ах
  - AC-4: `--add` / `--remove` режимы работают идентично
  - AC-5: ≥8 unit-тестов в `tests/unit/test_vhost_renderer.py`, все зелёные
  - AC-6: `make test` зелёный (с учётом BASELINE-1)
  - AC-7: Все 3 TRAP перенесены в Python docstrings
- **Dependencies:** None (не зависит от TASK-036A/verify-domains, независим от других DevPlan'ов)
- **Complexity:** 6/10
- **Critical path:** Да — TASK-036C (adopt-project) зависит от этого модуля для configure_vhost

### Implementation Workflow (single task)

Поскольку задача SMALL по файлам (≤4 файлов изменений), но STANDARD по сложности (nginx template regression risk, docker harness), реализация — единый Coder с чёткими checkpoint'ами:

1. **Checkpoint 1 (baseline capture):** Запустить `make render-vhosts NODE=<test>` → сохранить output как baseline
2. **Checkpoint 2 (Python core):** Реализовать vhost_renderer.py со всеми функциями, unit-тестами
3. **Checkpoint 3 (shell facade):** Переписать add-vhost.sh на ≤150 LOC с dispatch
4. **Checkpoint 4 (verification):** `make render-vhosts NODE=<test>` → `diff -r` с baseline → идентично
5. **Checkpoint 5 (gate):** `make test && make gate MODE=fast` → зелёный

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_platform_domain` | FQDN — поддомен PLATFORM_DOMAIN → wildcard cert path (`/etc/letsencrypt/live/<platform_domain>/`) | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_personal_domain` | FQDN — personal domain → own cert path (`/etc/letsencrypt/live/<fqdn>/`) | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_contains_nginx_vars` | Проверка, что nginx runtime variables ($host, $request_uri) НЕ подменены template engine | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_generate_vhost_body_http2_on` | `http2 on;` на отдельной строке (не `listen ... http2` — nginx deprecation) | `vhost_renderer.generate_vhost_body()` |
| `tests/unit/test_vhost_renderer.py` | `test_check_duplicate_domains_no_dup` | Список ProjectEntry без дубликатов → no exception | `vhost_renderer.check_duplicate_domains()` |
| `tests/unit/test_vhost_renderer.py` | `test_check_duplicate_domains_has_dup` | Два проекта с одинаковым domain → raise DuplicateDomainError | `vhost_renderer.check_duplicate_domains()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_project_yaml_expose_true` | ai-platform.yaml с `expose: true` + `domain: "app.example.com"` → ProjectConfig | `vhost_renderer.read_project_yaml()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_project_yaml_no_expose` | ai-platform.yaml без expose:true → None (skip) | `vhost_renderer.read_project_yaml()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_project_yaml_expose_no_domain` | expose:true но без domain → None (skip) | `vhost_renderer.read_project_yaml()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_node_yaml_projects_with_domains` | node.yaml с 3 проектами, 2 с domain → list[ProjectEntry] len=2 | `vhost_renderer.read_node_yaml_projects()` |
| `tests/unit/test_vhost_renderer.py` | `test_read_node_yaml_projects_empty` | node.yaml без projects или без domain → пустой список | `vhost_renderer.read_node_yaml_projects()` |
| `tests/unit/test_vhost_renderer.py` | `test_resolve_cert_domain_subdomain` | `app.platform.example.com` с PLATFORM_DOMAIN=`platform.example.com` → `platform.example.com` | `vhost_renderer.resolve_cert_domain()` |
| `tests/unit/test_vhost_renderer.py` | `test_resolve_cert_domain_personal` | `custom.io` с PLATFORM_DOMAIN=`platform.example.com` → `custom.io` | `vhost_renderer.resolve_cert_domain()` |
| `tests/unit/test_vhost_renderer.py` | `test_resolve_cert_domain_no_platform_domain` | PLATFORM_DOMAIN не задан → personal cert path (fqdn) | `vhost_renderer.resolve_cert_domain()` |
| `tests/unit/test_vhost_renderer.py` | `test_nginx_t_harness_pass` | Mock subprocess.run возвращает 0 → harness returns True | `vhost_renderer.nginx_t_harness()` (mocked docker) |
| `tests/unit/test_vhost_renderer.py` | `test_nginx_t_harness_fail` | Mock subprocess.run возвращает 1 → harness returns False | `vhost_renderer.nginx_t_harness()` (mocked docker) |
| `tests/unit/test_vhost_renderer.py` | `test_nginx_t_harness_no_docker` | docker отсутствует → harness returns True (WARN, не блокирует) | `vhost_renderer.nginx_t_harness()` (mocked shutil.which) |
| `tests/unit/test_vhost_renderer.py` | `test_remove_vhost_exists` | Vhost-файл существует → удаляется, audit-log записывается | `vhost_renderer.remove_vhost()` |
| `tests/unit/test_vhost_renderer.py` | `test_remove_vhost_not_exists` | Vhost-файл отсутствует → idempotent (no-op, return True) | `vhost_renderer.remove_vhost()` |
| `tests/unit/test_vhost_renderer.py` | `test_render_all_determinism` | Два вызова render_all с одинаковым node.yaml → байт-идентичный вывод | `vhost_renderer.render_all()` |

$TEST_SPEC: 20 tests specified (1 module, 1 test file)

---

## Acceptance Criteria Summary

| ID | Критерий | Метод проверки |
|----|----------|---------------|
| AC-1 | shell ≤150 LOC | `wc -l core/internal/scaffold/add-vhost.sh` |
| AC-2 | 0 inline `python3 -c` / `<<PYEOF` | `grep -E "python3 -c|<<PYEOF" core/internal/scaffold/add-vhost.sh` → 0 matches |
| AC-3 | Детерминизм render-all | `make render-vhosts NODE=<test> && cp -r overlays/ baseline/ && <migrate> && make render-vhosts NODE=<test> && diff -r overlays/ baseline/` → no differences |
| AC-4 | nginx -t harness проходит | `python3 -m pytest tests/unit/test_vhost_renderer.py::test_nginx_t_harness_pass -v` |
| AC-5 | Unit-тесты ≥8, все зелёные | `python3 -m pytest tests/unit/test_vhost_renderer.py -v` |
| AC-6 | `make test` зелёный | `make test` → exit 0 |
| AC-7 | `make gate MODE=fast` зелёный | `make gate MODE=fast` → exit 0 (с учётом BASELINE-1) |
| AC-8 | TRAP documented | grep `TRAP\[` в vhost_renderer.py → 3+ matches |

---

## Risk Assessment

| Risk | Severity | Probability | Mitigation |
|------|----------|:-----------:|------------|
| **Nginx template regression** — неправильный vhost (не те пути к сертификатам, битый синтаксис) | 🟡 MEDIUM | Low | Baseline-capture ДО миграции → `diff -r` ПОСЛЕ миграции. Snapshot-тесты generate_vhost_body. nginx -t harness в CI. |
| **Детерминизм нарушен** — повторный render-all даёт разный вывод | 🟡 MEDIUM | Low | Content-hash проверка в Python-модуле. Тест `test_render_all_determinism`. |
| **FQDN uniqueness regression** — дубликаты доменов не детектятся | 🟢 LOW | Very Low | Unit-тест `test_check_duplicate_domains_has_dup`. Set-based dedup проще grep-based аналога. |
| **nginx_t_harness: docker absent** — harness падает при отсутствии docker | 🟢 LOW | Low | Python-версия сохраняет fallback: docker not found → WARN, return True (не блокирует). |
| **vhost_yaml_reader.py consumers** — кто-то ещё вызывает vhost_yaml_reader.py напрямую | 🟢 LOW | Very Low | `grep -r "vhost_yaml_reader"` → кроме add-vhost.sh, только тесты. Тесты vhost_yaml_reader удаляются вместе с модулем. |
| **Shell facade regression** — parse_args ломается для edge-case аргументов | 🟢 LOW | Low | Shell facade сохраняет идентичную parse_args логику, меняется только main(). |
| **BASELINE-1 interference** — pre-existing gate failure маскирует regression | 🟢 LOW | — | BASELINE-1 документирован. CI gate должен исключать известный failure из verdict. Рекомендуется исправить BASELINE-1 до начала Wave 5b. |

### Composite Risk: 🟡 MEDIUM

Основной risk — nginx template regression. Митигируется baseline comparison + snapshot-тестами + nginx -t harness. Время восстановления: <10 минут (git revert).

---

## Rollback Strategy

| Шаг | Действие | Время |
|:---:|----------|:-----:|
| 1 | `git revert <merge-commit>` — восстановить add-vhost.sh и vhost_yaml_reader.py | <1 min |
| 2 | `make render-vhosts NODE=<affected>` — регенерировать vhost'ы старой версией | <5 min |
| 3 | `make test && make gate MODE=fast` — верификация | <3 min |
| **Total** | | **<10 min** |

**Важно:** Python-модуль и shell facade — чистая замена. Никакие другие подсистемы не зависят от внутренней реализации add-vhost.sh (только от его выходных .conf файлов). Откат не требует миграции данных или изменения конфигурации.

---

## TRAP Inventory (post-migration)

### TRAP, переносимые из add-vhost.sh → vhost_renderer.py

```python
# ⚠️ TRAP[BUG] · 2026-07-17 · DRIFT-1 · Flat directory (depth 1) required for vhost output
# · Symptom: vhost silently not loaded (fall-through to catch-all, class D12)
# · Root: producer wrote to conf.d/ subdir, non-recursive include overlay/*.conf
#   reads parent level — path mismatch → vhost never picked up
# · Fix: flat layout overlays/nginx/, no subdirectories
# · Prevention: static test verifies output path depth == 1 under overlays/nginx/
# Location: render_vhost() method — assert vhost_dir has no subdirectories in output path

# ⚠️ TRAP[BUG] · 2026-07-20 · P1 · || chain with pipefail evaluates ALL branches (compute_body_hash fallback)
# · Symptom: sha256sum AND shasum both output hash → body_hash=hash\nhash (129 chars)
# · Root: A && B || C && D || E — || does NOT short-circuit under set -o pipefail
# · Fix: delegation to content_hash.py (Python) — shell pipefail irrelevant
# · Prevention: content_hash.py is single source of truth for hashing
# Location: compute_body_hash() method — delegates to core.internal.shared.content_hash

# 🧐 TRAP[DECISION] · 2026-07-20 · — · nginx_t_harness: isolate overlay vhosts from harness_dir
# · Rejected: storing vhosts and harness support files (security-headers.conf) in same dir
# · Reason: mount -v ${harness_dir}:/etc/nginx/conf.d/overlay:ro exposes ALL .conf files
#   in harness_dir as vhost configs. security-headers.conf is NOT a valid vhost → nginx -t fails.
# · Rev: if harness nginx.conf is changed to use a non-glob include pattern for vhosts
# Location: nginx_t_harness() method — vhosts in vhosts/ subdir, includes in includes/ subdir
```

### Новые TRAP

```python
# 🧐 TRAP[DECISION] · 2026-07-26 · — · add-vhost.sh мигрирован в vhost_renderer.py через Strangler-Fig
# · Rejected: keeping vhost generation in shell (risk: 926 LOC monolith, grep-based YAML, inline python3)
# · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация с vhost_yaml_reader.py
# · Rev: если Python vhost_renderer генерирует vhost'ы >5% медленнее shell-версии → профилировать
# Location: module-level docstring

# 🧐 TRAP[DECISION] · 2026-07-26 · — · template_engine.py НЕ используется для nginx vhost'ов
# · Rejected: использование template_engine.py ({{UPPER_SNAKE}} strict grammar) для nginx config generation
# · Reason: nginx использует ${var} синтаксис переменных, несовместимый с {{UPPER_SNAKE}}.
#   Попытка escape nginx-переменных → fragile, error-prone. F-строки — естественный механизм.
# · Rev: если nginx перейдёт на другой синтаксис переменных без конфликта с {{}} — пересмотреть
# Location: generate_vhost_body() docstring
```

---

## File Manifest

### Modified files

| Файл | До (LOC) | После (LOC) | Сокращение |
|------|----------|-------------|------------|
| `core/internal/scaffold/add-vhost.sh` | 926 | ~150 | 84% |

### New files

| Файл | LOC | Назначение |
|------|-----|-----------|
| `core/internal/scaffold/vhost_renderer.py` | ~500 | Python core: vhost generation, render-all pipeline, nginx harness |
| `tests/unit/test_vhost_renderer.py` | ~350 | Unit-тесты (20 test functions) |

### Deleted files

| Файл | LOC | Причина |
|------|-----|--------|
| `core/internal/scaffold/vhost_yaml_reader.py` | 74 | Консолидирован в vhost_renderer.py (D1) |

---

## Implementation Commands

### Single task — Coder implementation

```
coder Read .ai/plans/036-wave5b-vhost/01-DevPlan.md, implement TASK-036B: add-vhost.sh → vhost_renderer.py
```

### Checkpoint sequence (для Coder)

1. **Baseline capture:** `make render-vhosts NODE=<test> && cp -r <node-configs>/<test>/overlays/nginx/ /tmp/vhost-baseline/`
2. **Implement vhost_renderer.py** — все функции + CLI (argparse subcommands)
3. **Implement unit-тесты** — 20 тестов в test_vhost_renderer.py
4. **Rewrite shell facade** — ≤150 LOC, только parse_args + dispatch
5. **Delete vhost_yaml_reader.py**
6. **Verify determinism:** `make render-vhosts NODE=<test> && diff -r <overlays/> /tmp/vhost-baseline/`
7. **Run tests:** `python3 -m pytest tests/unit/test_vhost_renderer.py -v`
8. **Run gate:** `make gate MODE=fast`

---

## Dependencies on This DevPlan

| DevPlan | Task | Dependency Type |
|---------|------|----------------|
| 036-wave5-strangler-shell-monoliths | TASK-036C (adopt-project) | **Hard** — project_adopter.py вызывает vhost_renderer для configure_vhost() |
| 036-wave5-strangler-shell-monoliths | TASK-036G (integration verify) | **Soft** — интеграционная верификация всей Wave 5 |

**Блокирующее условие:** TASK-036B должен быть завершён ДО начала TASK-036C (adopt-project). TASK-036A (verify-domains), TASK-036D (remote-cmd), TASK-036F (issue-cert) — независимы и могут выполняться параллельно.

**Cross-plan note (re: DevPlan 036C):** После завершения TASK-036B, поле REQUIRES в DevPlan 036C (`036-wave5c-adopt/01-DevPlan.md`) должно быть обновлено: удалить `vhost_yaml_reader.py` (файл будет удалён при выполнении TASK-036B) и оставить только `vhost_renderer.py`. Без этого обновления Coder для 036C будет искать несуществующий файл.

$END_DEVPLAN
