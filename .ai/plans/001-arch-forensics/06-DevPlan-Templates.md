<!-- GREP_SUMMARY: DevPlan, template-unification, render-at-use, B-hypothesis, 3-waves, template-engine.py, 12-modules, strict-grammar -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ TRAP[DECISION] ×6 → ◇ Wave 1: FOUNDATION (T1.1–T1.6) → ◇ Wave 2: MIGRATION (4 parallel groups T2.A–T2.D) → ◇ Wave 3: GATES+CLEANUP (T3.1–T3.7) → ⊕ $TASKS/$PARALLEL_GROUPS/$TEST_SPEC/$FILE_MANIFEST -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Унификация 4 механизмов подстановки в ai-platform до единого синтаксиса `{{UPPER_SNAKE}}` с render-at-use архитектурой (Гипотеза B), написание template-engine на Python с bash CLI, расширение sudo-покрытия до 12 модулей, удаление docker-compose.test.template, два CI-gate'а (синтаксис + разрешимость), регистрация в 3 реестрах.
- **DESCRIPTION:** DevPlan разбит на 3 волны: FOUNDATION (engine + manifest + регистрация), MIGRATION (4 параллельные группы: scaffold, nginx, monitoring, sudo), GATES+CLEANUP (gate'и + удаление файлов + sweep).
- **RATIONALE:** Разведка вскрыла, что `.conf` ×6 — симлинки, а не копии (F1 BugCollapse). Гипотеза B устраняет класс дрейфа конструктивно: нет закоммиченного артефакта — нечему расходиться. Python-ядро — для нативной тестируемости (§TESTING запрещает subprocess). Строгая грамматика `{{UPPER_SNAKE}}` исключает коллизии с Prometheus-templating `{{ $labels.x }}` и Grafana `{{instance}}`.
- **ACCEPTANCE_CRITERIA:** (переформулированы под B) AC1: `rg '__[A-Z_]+__' templates/` → 0; AC2: `${PLATFORM_DOMAIN}` sed → engine-call; AC3: `make templates-check` → exit 0 при разрешимости всех шаблонов; AC4: `make templates-check --verbose` → exit 1 с diagnostic если unresolved; AC5: gate `test_gate_template_syntax` зелёный (единый синтаксис); AC6: gate `test_gate_template_drift` зелёный (разрешимость + отсутствие нераскрытых плейсхолдеров); AC7: `/opt/core/` → `{{PLATFORM_ROOT}}/core/` в sudo-whitelist.template; AC8: `{{MODULE_NAME}}` раскрывается в момент генерации sudoers на VPS (не в закоммиченном файле).
- **IMPLEMENTS:** Brief-Templates.md (001-arch-forensics), superposition collapse B
- **IMPACTS:** `core/internal/template_engine.py` (NEW), `core/internal/template-engine.sh` (NEW), `core/templates/template-manifest.yaml` (NEW), `tests/test_template_engine.py` (NEW), `tests/gates/test_gate_template_syntax.py` (NEW), `tests/gates/test_gate_template_drift.py` (NEW), `core/internal/bootstrap/deploy-modules.sh`, `core/internal/scaffold/add-project.sh`, `core/modules/nginx/install.sh`, `core/modules/monitoring/hooks/on-project-deploy.sh`, `core/templates/sudo-whitelist.template`, `core/modules/*/sudo-whitelist.conf` ×6 (удаление симлинков), `templates/template-{backend,frontend,fullstack}/` ×3, `templates/template-context/`, `core/modules/nginx/config/platform-default.conf` (→ .template), `core/templates/docker-compose.test.template` (удаление), `core/modules/AGENTS.md`, `core/entrypoint-manifest.yaml`, `core/AGENTS.md`, `AGENTS.md` (root), `Makefile`
- **REQUIRES:** Brief-Templates.md (дан в этом сеансе), superposition collapse (вопросы), python3 ≥3.10

$START_DEVPLAN

# 06-DevPlan-Templates: Template Unification — Render-at-Use

## $TASKS (task graph)

```
Wave 1 (FOUNDATION)
  T1.1 ─► T1.2 ─► T1.3 ─► T1.4 ─► T1.5 ─► T1.6
  engine  bash   tests  Makefile manifest registr.

Wave 2 (MIGRATION — 4 parallel groups)
  ┌ T2.A: scaffold ×4 (add-project.sh + 4 template dirs)
  ├ T2.B: nginx (install.sh + rename + deploy references)
  ├ T2.C: monitoring (hook sed → engine)
  └ T2.D: sudo ×12 (template fix + deploy-modules.sh + symlink removal)

Wave 3 (GATES + CLEANUP)
  T3.1 ─► T3.2 ─► T3.3 ─► T3.4 ─► T3.5 ─► T3.6 ─► T3.7
  syntax  drift  registr integrat delete  migrate AC sweep
  gate    gate   gates   gate    test.   knowledge
```

## $PARALLEL_GROUPS

| Wave | Group | Tasks | Rationale |
|------|-------|-------|-----------|
| 1 | G0 | T1.1→T1.6 | Sequential: each depends on prior |
| 2 | G1 | T2.A | Independent of G2–G4 (scaffold-only) |
| 2 | G2 | T2.B | Independent of G1, G3, G4 (nginx-only) |
| 2 | G3 | T2.C | Independent of G1, G2, G4 (monitoring-only) |
| 2 | G4 | T2.D | Independent of G1–G3 (sudo-only) |
| 3 | G5 | T3.1→T3.7 | Sequential: gates → register → integrate → cleanup |

## Task-level edge cases

Каждая задача T*.* в кодовой спецификации ниже снабжена картой крайних случаев: пустой/невалидный/предельно большой вход, повторный запуск (идемпотентность), частичный сбой и откат, отказ внешней зависимости, конкурентный доступ, миграция данных.

---

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Гипотеза B (Render-at-use) выбрана вместо A (Committed artifacts)

- **Context:** Бриф предлагал Гипотезу A: материализовать симлинки в отрендеренные `.conf`, gate сравнения. Разведка вскрыла, что `.conf` ×6 — симлинки (F1), а не дрейфующие копии. Единственный источник дрейфа — механизмы 1–4 с разным синтаксисом (F2).
- **Decision:** Унификация синтаксиса + render-at-use. Симлинки удаляются. Артефакты не коммитятся. Gate проверяет синтаксис и разрешимость template-файлов самих по себе.
- **Reason:** Дрейф невозможен конструктивно (нет закоммиченного артефакта — нечему расходиться). Меньше machinery (нет drift-gate сравнения рендеров, нет процедуры «перегенерируй и закоммить»). Формализует поведение, которое система уже демонстрирует через симлинки.
- **Rejected:** Гипотеза A (committed + drift-gate) — создаёт класс дрейфа, ради борьбы с которым и затевается gate; ценности промежуточного committed артефакта нет (финальный sudoers генерируется на VPS). Гипотеза C (Eliminate templates) — не решает механизмы 2–4. Гипотеза D (Formalize symlinks) — сохраняет 4 синтаксиса. Гипотеза E (envsubst/jinja2) — коллизия `${}` с runtime compose-vars или лишняя зависимость.
- **Rev:** если понадобится подписываемый/аудируемый след security-артефакта для sudoers — вернуться к A (коммитить отрендеренные `.conf` + drift-gate сравнения).

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Python-ядро вместо bash-движка

- **Context:** §TESTING (testing.md) запрещает `subprocess.run` для тестирования бизнес-логики. Bash-движок нетестируем нативно в pytest — потребовался бы python-референс внутри gate. Python-ядро `template_engine.py` тестируется нативным импортом; тонкая bash-обёртка для CLI-вызовов из скриптов.
- **Decision:** `core/internal/template_engine.py` — ядро, `core/internal/template-engine.sh` — CLI wrapper.
- **Reason:** Python≥3.10 уже является зависимостью платформы (monitoring hook, discover_modules.py). Двойная реализация (bash runtime + python reference в gate) создаёт дрейф двух рендереров.
- **Rejected:** Чистый bash по брифу — нарушает §TESTING и требует дуального рендерера.
- **Rev:** если python3 исключается из bootstrap-пути VPS — переписать ядро на bash + python-референс в gate с записью DEBT о риске дрейфа.

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Строгая грамматика `{{UPPER_SNAKE}}`

- **Context:** `{{VAR}}`-синтаксис коллизирует с Prometheus `{{ $labels.instance }}` и Grafana `{{instance}}` (F3). Бриф не рассматривает коллизию.
- **Decision:** Плейсхолдер строго `{{[A-Z][A-Z0-9_]*}}` — старт с заглавной, только uppercase + цифры + подчёркивание, без пробелов, без `$`.
- **Reason:** Исключает Prometheus `{{ $labels.x }}` (пробел + $), Grafana `{{instance}}` (lowercase), Docker Compose runtime `${VAR}` (другой синтаксис). Все наши переменные (`MODULE_NAME`, `PLATFORM_ROOT`, `PROJECT_NAME`, `ORG_NAME`, `DOMAIN`, `NODE_NAME`, `CONTEXT`, `PLATFORM_DOMAIN`, `PROJECT`) — UPPER_SNAKE.
- **Rejected:** Свободная грамматика `{{.*}}` — коллизия.
- **Rev:** если появится плейсхолдер с mixedCase — расширить regex до `{{[A-Z][A-Za-z0-9_]*}}` с эвристикой исключения `$` и пробелов.

# ⚠️ TRAP[DECISION] · 2026-07-18 · MED · Удаление docker-compose.test.template

- **Context:** Файл — орфан (ни один скрипт/CI не потребляет). 11 модулей имеют готовые `docker-compose.test.yml`; 7 gate'ов частично покрывают инварианты шаблона (test_gate_compose_no_base_image, test_restart_consistency, test_gate_container_name_consistency, test_gate_healthcheck_contract).
- **Decision:** Файл удалить. Инварианты и collision-policy перенести в `core/modules/AGENTS.md` как расширенный раздел §docker-compose.test.yml contract.
- **Reason:** Не держать орфан с неопределённым статусом. Инварианты ценны — сохранить в канонической документации модулей.
- **Rejected:** Конформанс-gate — избыточен (существующие gate'ы покрывают ключевые инварианты, а нюансы модульно-специфичны и лучше в AGENTS.md).
- **Rev:** если существующие gate'ы сдадут coverage инвариантов — добавить конформанс-gate.

# ⚠️ TRAP[DECISION] · 2026-07-18 · MED · 12 модулей sudo-покрытием

- **Context:** sudo-whitelist есть у 6 из 12 модулей. Остальные 6 (logging, minio, infra-metrics, langfuse, litellm, monitoring) молча SKIP в `generate_module_sudoers()`.
- **Decision:** Рендерить sudoers для всех 12 модулей. Шаблон един — `{{MODULE_NAME}}` раскрывается для каждого.
- **Reason:** Единая security-политика — не оставлять модули без sudoers. `generate_module_sudoers()` всегда получает белый список.
- **Rejected:** Только 6 — пробел в security-политике; будущее расширение сложнее (надо вспомнить, что для новых модулей sudoers не генерируется).
- **Rev:** если модуль объективно не нуждается в sudoers (system-модуль?) — добавить `module.yaml`-признак `sudoers: false` (follow-up).

# ⚠️ TRAP[DECISION] · 2026-07-18 · LOW · Переименование чистых шаблонов

- **Context:** `platform-default.conf` — чистый шаблон (всегда рендерится перед деплоем, никогда не деплоится копированием), но имеет расширение `.conf`. `alert-rules.yml` — dual-role (одновременно живой конфиг И шаблон для per-project рендеринга). `ssl-params.conf.template` — уже `.template`.
- **Decision:** Переименовать только чистые шаблоны: `platform-default.conf` → `platform-default.conf.template`. `alert-rules.yml` — оставить (dual-role). `ssl-params.conf.template` — без изменений.
- **Reason:** Явно отличает шаблоны от конфигов для человека и агента. `alert-rules.yml` — исключение из правила, явно маркированное в manifest (dual-role annotation).
- **Rejected:** Переименование `alert-rules.yml` → ломает live-monitoring (файл монтируется как конфиг); ничего не переименовывать → когнитивная ловушка «`.conf` — это конфиг».
- **Rev:** если dual-role паттерн размножится — ввести формальный признак `type: dual-role` в template-manifest.

---

## Кодовая спецификация

### T1.1 — `core/internal/template_engine.py`

Новый файл. Python-модуль с 2 функциями + 1 класс ошибок.

```python
# GREP_SUMMARY: template-engine Python-core render check grammars placeholder {{UPPER_SNAKE}}
# STRUCTURE: ┌parse_vars→StrictGrammar RE┐ → ◇ render_template → ◇ render_all → ◇ check_all
# region MODULE_CONTRACT
## @purpose  Core template rendering engine with strict placeholder grammar {{UPPER_SNAKE}}
## @scope    Вызывается из bash-CLI (template-engine.sh), CI-gates, и тестов
## @invariants
##   - Placeholder grammar: {{[A-Z][A-Z0-9_]*}} — uppercase start, no spaces, no dollar sign
##   - All variables resolvable or explicit allow_missing=True
##   - Output is deterministic: render(template, vars) → всегда одинаковый вывод при одинаковых входах
##   - Atomic writes: пишет во временный файл, затем os.rename (не cross-filesystem)
## @rationale Python core для нативной тестируемости (§TESTING). Strict grammar исключает
##            коллизию с Go-templating ({{ $labels.x }}) и Grafana ({{instance}}).
# endregion MODULE_CONTRACT
```

**Функции:**

1. `render_template(template_path: str, output_path: str | None, vars: dict, *, allow_missing: bool = False, dry_run: bool = False) -> str | None` — рендерит `{{VAR}}` → значение. При `output_path=None` возвращает строку. При `dry_run=True` возвращает строку, не пишет. При `allow_missing=False` и неразрешённом плейсхолдере — `TemplateError`.

2. `parse_vars(var_pairs: list[str]) -> dict` — парсит `KEY=val` из CLI-аргументов в dict.

3. `class TemplateError(Exception)` — с атрибутами `.template_path`, `.unresolved`, `.line_no`.

4. `render_all(manifest_path: str, *, extra_vars: dict | None = None, dry_run: bool = False) -> int` — читает `template-manifest.yaml`, рендерит все entries. Возвращает 0 при успехе, количество ошибок при частичном сбое.

5. `check_all(manifest_path: str, *, extra_vars: dict | None = None) -> tuple[bool, list[str]]` — dry-run всех entries; возвращает `(success, diagnostics)`. Diagnostics — список `"OK: path"` / `"UNRESOLVED: path: {{X}}"`.

**Крайние случаи:**
- Пустой шаблон (0 bytes) → возвращает "" (no-op)
- Шаблон без плейсхолдеров → copy-as-is
- Бинарный файл → `TemplateError("binary content detected")`
- Значение переменной содержит `/`, `&`, `\`, `\n` → корректно экранируется подстановкой Python (`str.replace`, не sed)
- Переменная не задана (`allow_missing=False`) → `TemplateError`; (`allow_missing=True`) → оставить `{{VAR}}` + WARNING в stderr
- Невалидный синтаксис `{{lowercase}}` → `TemplateError("invalid placeholder grammar")`
- `{{` без закрывающего `}}` → `TemplateError("unclosed placeholder")`
- `{{ $labels.x }}` → не матчится (строгая грамматика), оставляется как есть
- Дубликат переменной в одном вызове → последнее значение
- Пустое значение переменной → корректно (пустая строка)
- Путь назначения (output_path) — директория не существует → `FileNotFoundError`
- Нет прав на запись → `PermissionError`
- Два конкурентных рендера одного output → каждый использует `mktemp + rename`, последний выигрывает атомарно (промежуточных состояний нет)
- Очень большой шаблон (100MB+) → стриминг через `file.read(chunk_size)`; память не детонирует
- template_path — симлинк → разрешается `os.path.realpath`
- Манифест не найден → `FileNotFoundError`
- Манифест — невалидный YAML → `yaml.YAMLError`

### T1.2 — `core/internal/template-engine.sh`

Новый файл. Тонкая bash-обёртка для вызова template_engine.py из скриптов.

```bash
# GREP_SUMMARY: template-engine bash-CLI wrapper template_engine.py
# STRUCTURE: ┌arg parsing┐ → ◇ dispatch: render/check/all → ⊕ exit codes
```

**Режимы:**
- `template-engine.sh render <template> <output> [VAR=val ...]` — рендер одного файла
- `template-engine.sh render-all [--manifest PATH]` — рендер всех из манифеста
- `template-engine.sh check [--manifest PATH]` — dry-run проверка, exit 0 если все разрешимы, exit 1 с diagnostic

**Крайние случаи:**
- python3 не найден → `exit 2 "python3 not found in PATH"`
- template_engine.py не импортируется → `exit 2` с traceback
- Отсутствуют обязательные переменные → diagnostic в stderr, exit 1
- `check` с `--verbose` → построчный diagnostic для каждого файла
- Манифест не указан → default `core/templates/template-manifest.yaml` относительно SCRIPT_DIR

### T1.3 — `core/templates/template-manifest.yaml`

Новый файл. Единый источник: template → consumer → vars → output (null = render-at-use). Структура:

```yaml
# Single source of truth: template → consumer → vars mapping
# Used by: template-engine.py (render-all, check), CI gates (syntax, drift)

version: 1

standard_vars:
  PLATFORM_ROOT: {source: auto, default: /opt/platform, resolve_from: [env.PLATFORM_ROOT, core/lib/paths.sh]}
  PLATFORM_DOMAIN: {source: auto, default: null, resolve_from: [env.PLATFORM_DOMAIN, .env.PLATFORM_DOMAIN, platform-env.yaml]}

templates:
  - template: core/templates/sudo-whitelist.template
    output: null
    type: single
    consumer: core/internal/bootstrap/deploy-modules.sh:generate_module_sudoers()
    vars:
      MODULE_NAME: {required: true, source: per-module}
      PLATFORM_ROOT: {required: true, source: standard}

  - template: core/modules/nginx/config/ssl-params.conf.template
    output: null
    type: single
    consumer: core/modules/nginx/install.sh:deploy_shared_snippets()
    vars:
      PLATFORM_DOMAIN: {required: false, source: standard}

  - template: core/modules/nginx/config/platform-default.conf.template
    output: null
    type: single
    consumer: core/modules/nginx/install.sh:_deploy_vhost_full()
    vars:
      PLATFORM_DOMAIN: {required: false, source: standard}

  - template: core/modules/monitoring/config/alert-rules.yml
    output: null
    type: single
    dual_role: true
    consumer: core/modules/monitoring/hooks/on-project-deploy.sh:generate_alert_rules()
    vars:
      PROJECT: {required: true, source: CLI}

  - template: templates/template-backend/
    output: null
    type: directory
    recursive: true
    consumer: core/internal/scaffold/add-project.sh:replace_placeholders()
    vars:
      PROJECT_NAME: {required: true}
      ORG_NAME: {required: true}
      DOMAIN: {required: false}
      NODE_NAME: {required: true}
      PLATFORM_DOMAIN: {required: false}

  - template: templates/template-frontend/
    output: null
    type: directory
    recursive: true
    consumer: core/internal/scaffold/add-project.sh
    vars: {PROJECT_NAME: {required: true}, ORG_NAME: {required: true}, DOMAIN: {required: false}, NODE_NAME: {required: true}, PLATFORM_DOMAIN: {required: false}}

  - template: templates/template-fullstack/
    output: null
    type: directory
    recursive: true
    consumer: core/internal/scaffold/add-project.sh
    vars: {PROJECT_NAME: {required: true}, ORG_NAME: {required: true}, DOMAIN: {required: false}, NODE_NAME: {required: true}, PLATFORM_DOMAIN: {required: false}}

  - template: templates/template-context/
    output: null
    type: directory
    recursive: true
    consumer: core/internal/scaffold/context-init.sh
    vars: {CONTEXT: {required: true}, ORG_NAME: {required: true}, NODE_NAME: {required: true}}
```

### T1.4 — `tests/test_template_engine.py`

Новый файл. Нативные pytest-тесты импортом `core.internal.template_engine`.

**Тесты (≥10 atomic):**
1. `test_render_single_placeholder` — `{{NAME}}` → "world", `render_template(..., dry_run=True)` возвращает строку
2. `test_render_multiple_placeholders` — `{{A}} {{B}}` → корректно
3. `test_render_no_placeholders` — as-is copy
4. `test_render_empty_template` — "" → ""
5. `test_strict_grammar_rejects_lowercase` — `{{name}}` → `TemplateError`
6. `test_strict_grammar_rejects_spaces` — `{{ $labels.x }}` → не матчится, оставлено как есть
7. `test_unresolved_placeholder_blocking` — нет `allow_missing` → `TemplateError`
8. `test_unresolved_placeholder_allow` — `allow_missing=True` → оставляет `{{X}}` + WARNING
9. `test_unclosed_placeholder` — `{{VAR` без `}}` → `TemplateError`
10. `test_special_chars_in_value` — `/ \n &` → корректная подстановка
11. `test_parse_vars` — `["A=1", "B=2"]` → `{"A": "1", "B": "2"}`
12. `test_atomic_write_output` — проверка использования временного файла
13. `test_renderall_missing_manifest` — `FileNotFoundError`
14. `test_check_all_green` — все разрешимы → `(True, [...])`
15. `test_check_all_red` — есть unresolved → `(False, diagnostics)`
16. `test_deterministic_output` — два вызова с одинаковыми входами → одинаковый вывод
17. `test_large_template` — 10MB, не детонирует память (мониторинг `tracemalloc`)
18. `test_binary_template` — null byte → `TemplateError`

**LDD-траектория:** Все тесты через `ldd_trajectory` с IMP:9 фильтром (как требует `conftest.py`).

### T1.5 — Makefile: новые таргеты

Добавить в корневой `Makefile`:

```makefile
## templates-check: Dry-run рендер всех шаблонов из манифеста — exit 0 при разрешимости, exit 1 с diagnostic при unresolved
templates-check:
	@core/internal/template-engine.sh check --verbose

## templates-render: Рендер всех шаблонов по манифесту
templates-render:
	@core/internal/template-engine.sh render-all
```

Интеграция в `make validate` (pre-commit) и `make gate MODE=fast` (как отдельный gate-тест, не инлайн — gate-тесты запускаются pytest).

### T1.6 — Регистрация в 3 реестрах

1. **`core/entrypoint-manifest.yaml`:**
   - Добавить `templates-render` и `templates-check` в секцию `validate:` (поскольку это валидационные операции, не lifecycle/деплой)
   - Добавить в `allowed_verbs:` список
   - Добавить `test_gate_template_syntax` и `test_gate_template_drift` в секцию `gates:`

2. **`core/AGENTS.md`:**
   - Добавить строки в таблицу канонических операций:
     - `make templates-check` | Проверка разрешимости шаблонов | `make templates-check` | `core/internal/template-engine.sh check`
     - `make templates-render` | Рендер шаблонов по манифесту | `make templates-render` | `core/internal/template-engine.sh render-all`
   - В структуру `core/` добавить `internal/template_engine.py` и `internal/template-engine.sh`

3. **`AGENTS.md` (root):**
   - Добавить в глоссарий глаголов: `templates-check` (проверка), `templates-render` (рендер)
   - Добавить `core/templates/template-manifest.yaml` в таблицу навигации (статус: канонический)

### T2.A — Scaffold migration

**T2.A1. `core/internal/scaffold/add-project.sh` — `replace_placeholders()`:**

Заменить sed-обработку `__VAR__` на вызов `template-engine.sh render-all --manifest <manifest> --extra PROJECT_NAME=... ORG_NAME=... DOMAIN=... NODE_NAME=... PLATFORM_DOMAIN=...` для целевой директории:

```
replace_placeholders() → render_project_template()
  └─ template-engine.sh render <template_dir> <output_dir> \
       PROJECT_NAME=$NAME ORG_NAME=$ORG_NAME DOMAIN=$DOMAIN \
       NODE_NAME=$NODE_NAME PLATFORM_DOMAIN=$PLATFORM_DOMAIN
```

Функция `auto_domain()` и `lowercase_domain()` — сохранить, добавляют значения к `render_project_template()` аргументам.

**Крайние случаи:**
- Пустое имя проекта → validate в `validate_inputs()` ДО рендера
- Имя содержит `/`, `.`, `@` → validate, reject до рендера (существующий контракт)
- Повторный scaffold того же проекта → idempotent (engine overwrites output)
- Частичный сбой (часть файлов отрендерилась) → engine возвращает код ошибки, add-project останавливается; директория проекта — в промежуточном состоянии, перезапуск с чистого листа
- `template-engine.sh` не найден → `exit 2 "template-engine.sh not found"`
- Манифест не содержит entry для директории → `TemplateError`

**T2.A2. `templates/template-{backend,frontend,fullstack}/`:**

Заменить ВСЕ вхождения `__PROJECT_NAME__`, `__ORG_NAME__`, `__NODE_NAME__`, `__DOMAIN__`, `__PLATFORM_DOMAIN__` на `{{PROJECT_NAME}}`, `{{ORG_NAME}}`, `{{NODE_NAME}}`, `{{DOMAIN}}`, `{{PLATFORM_DOMAIN}}`.

Затрагивает: `README.md`, `AGENTS.md`, `ai-platform.yaml`, `docker-compose.yml`, `src/` (если есть), `docker/` (если есть).

Особое внимание: `docker-compose.yml` содержит **два класса переменных**:
- `__PROJECT_NAME__` → `{{PROJECT_NAME}}` (template engine)
- `${IMAGE_REGISTRY:-ghcr.io}` — runtime compose-переменная (НЕ трогать)

**Крайние случаи:**
- Файл с обеими грамматиками (e.g., `ghcr.io/__ORG_NAME__/__PROJECT_NAME__:${IMAGE_TAG:-latest}`) → после миграции `ghcr.io/{{ORG_NAME}}/{{PROJECT_NAME}}:${IMAGE_TAG:-latest}` — корректно
- `__ORG_NAME__` в lowercase (требование GHCR) — значение приходит lowercase от `auto_domain()`; placeholder регистро-нейтрален (всегда UPPER_SNAKE)
- Откат: git revert before commit

**T2.A3. `templates/template-context/`:**

`context.yaml`: `__CONTEXT__` → `{{CONTEXT}}`, `__ORG_NAME__` → `{{ORG_NAME}}`, `__NODE_NAME__` → `{{NODE_NAME}}`
`README.md`: обновить инструкции (sed → template-engine)
`modules/hermes-agent/config.yaml`: `__CONTEXT__` → `{{CONTEXT}}`, `__ORG_NAME__` → `{{ORG_NAME}}`

**T2.A3b. `core/internal/scaffold/context-init.sh`:**

Заменить inline-sed подстановки (если есть) на вызов `template-engine.sh render-all`.

### T2.B — Nginx migration

**T2.B1. `core/modules/nginx/install.sh` — 4 sed-сайта:**

| Строка | Функция | Действие |
|--------|---------|----------|
| 638 | `deploy_shared_snippets()` | `sed "s/\${PLATFORM_DOMAIN}/${domain}/g"` → `template-engine.sh render ssl-params.conf.template /tmp/rendered PLATFORM_DOMAIN=$domain` |
| 677 | `_deploy_vhost_full()` | `sed "s/\${PLATFORM_DOMAIN}/${domain}/g"` → `template-engine.sh render platform-default.conf.template /tmp/rendered PLATFORM_DOMAIN=$domain` |
| 800, 813 | `_deploy_vhost_*()` | аналогично |

Строка 163 (API_KEY sed для DNS-провайдера) — **НЕ трогать**. Это секретный путь, не шаблон.

**Крайние случаи:**
- `PLATFORM_DOMAIN` не задан (пустой) → engine с `allow_missing=True` оставляет `{{PLATFORM_DOMAIN}}` + WARN (поведение как существующая строка 642-643)
- `domain` пустая строка → `template-engine.sh` выставляет `PLATFORM_DOMAIN=""`, engine рендерит пустую строку
- Overlay-конфиг существует → skip рендера (существующее поведение строки 671-673)
- Пермиссии на /tmp → отказано → `PermissionError`
- Конкурентный рендер двух серверных блоков → разные tmp-файлы, без коллизий

**T2.B2. Переименование файла:**

`core/modules/nginx/config/platform-default.conf` → `core/modules/nginx/config/platform-default.conf.template`

**T2.B3. Обновление ссылок в скриптах:**

`install.sh`: заменить все `config/platform-default.conf` → `config/platform-default.conf.template` (4 ссылки). `nginx_reload_hook.sh` — проверить, есть ли ссылки (вероятно нет, он релоадит nginx, а не шаблоны).

### T2.C — Monitoring hook migration

**T2.C1. `core/modules/monitoring/hooks/on-project-deploy.sh`:**

Строка 371: `sed "s/\${PROJECT}/${HOOK_PROJECT}/g" "$template"` → `template-engine.sh render "$template" "$output_file" PROJECT=$HOOK_PROJECT`

Строка 211: `sed` для Prometheus targets → проверить, если `${PROJECT}` — аналогично.

**Крайние случаи:**
- `HOOK_PROJECT` пуст → hook выходит на строке 32-33 (существующая защита)
- template не найден → hook выходит на строке 366-369 (существующая защита)
- `output_file` директория не существует → `mkdir -p` в hook (уже есть строка 370)
- `alert-rules.yml` — dual-role (содержит Prometheus `{{ $labels.x }}`) → строгая грамматика не матчит Prometheus-синтаксис, рендерится только `PROJECT`

### T2.D — Sudo migration (render-at-use)

**T2.D1. `core/templates/sudo-whitelist.template` — path-колонка:**

Строки 36-58: `/opt/core/modules/{{MODULE_NAME}}/Makefile` → `{{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile`

Также обновить `@invariants` и комментарии: `sed "s/{{MODULE_NAME}}/<name>/g"` → `template-engine.sh render ... MODULE_NAME=<name> PLATFORM_ROOT=...`

**T2.D2. `core/internal/bootstrap/deploy-modules.sh` — `generate_module_sudoers()`:**

Текущая функция (строки 257-319) читает `module_dir/sudo-whitelist.conf` (симлинк). Переписать:

1. Template source: `core/templates/sudo-whitelist.template` (через `SCRIPT_DIR/../../templates/...` или `${PLATFORM_ROOT}/core/templates/...` на VPS)
2. Рендерит `MODULE_NAME` + `PLATFORM_ROOT` через `template-engine.sh render`
3. Результат — tmp sudoers, затем visudo + mv (логика строк 270-318 сохраняется)
4. `MODULE_NAME` — принимается аргументом (уже есть)

**Конструкция:**

```bash
generate_module_sudoers() {
    local module_name="$1"
    local module_dir
    module_dir="$(realpath "${SCRIPT_DIR}/../../modules/${module_name}")"

    # Render template with module vars
    local template="${SCRIPT_DIR}/../../templates/sudo-whitelist.template"
    if [[ ! -f "$template" ]]; then
        template="${PLATFORM_ROOT}/core/templates/sudo-whitelist.template"
    fi

    local rendered
    rendered="$(mktemp /tmp/platform-sudoers-rendered-XXXXXX)"
    if ! core/internal/template-engine.sh render "$template" "$rendered" \
        "MODULE_NAME=${module_name}" "PLATFORM_ROOT=${PLATFORM_ROOT}"; then
        log_step "sudoers:${module_name}" "FAIL" "Template render FAILED"
        rm -f "$rendered"
        return 1
    fi

    # Generate sudoers from rendered file (remainder of existing logic)
    # но reading from $rendered, not $whitelist
    ...
}
```

**T2.D3. Расширение покрытия до 12 модулей:**

Сейчас `generate_module_sudoers()` вызывается для каждого развёртываемого модуля. Механизм вызова (строки 798, 825, 1011, 1043) уже итерирует по всем 12 модулям — никаких изменений в логике вызова. Просто теперь для всех 12 модулей template существует (один на всех), и SKIP не случается.

Удалить guard `if [[ ! -f "$whitelist" ]]; then ... SKIP ...` — он больше не нужен (template всегда доступен как `core/templates/sudo-whitelist.template`).

**T2.D4. Удаление симлинков:**

```bash
rm core/modules/backup-cron/sudo-whitelist.conf
rm core/modules/clickhouse/sudo-whitelist.conf
rm core/modules/hermes-agent/sudo-whitelist.conf
rm core/modules/nginx/sudo-whitelist.conf
rm core/modules/postgres/sudo-whitelist.conf
rm core/modules/redis/sudo-whitelist.conf
```

**Крайние случаи:**
- `PLATFORM_ROOT` не задан на VPS → `paths.sh` всегда устанавливает default `/opt/platform` (строка 37)
- Template не доставлен на VPS (ошибка rsync) → `template-engine.sh render` возвращает error; deploy-modules пропускает модуль с FAIL, не блокирует остальные
- Конкурентный деплой двух модулей → каждый использует свой `mktemp`, атомарная `mv` в `/etc/sudoers.d/platform-{module_name}` — разные целевые файлы, коллизий нет
- Visudo валидация упала → `rm -f "$tmp_sudoers"`, оригинал не тронут (существующая защита строки 307-312)
- Два вызова для одного модуля (повтор) → idempotent (последний побеждает атомарно)
- Модуль без каталога (удалён из репозитория) → `realpath` упадёт, функция вернёт error диагностику

### T3.1 — `tests/gates/test_gate_template_syntax.py`

Новый gate-тест. Проверяет единый синтаксис `{{UPPER_SNAKE}}` во всех файлах в манифесте.

```python
@pytest.mark.gate
def test_all_templates_use_strict_grammar(manifest_path, ldd_trajectory):
    """Ни один template-файл не содержит __VAR__ или ${VAR} синтаксис."""
    # Читает template-manifest.yaml
    # Для каждого entry:
    #   - file scan: не содержит __[A-Z_]+__ (остатки старого синтаксиса)
    #   - file scan: не содержит \$\{[A-Z_]+\} (кроме runtime compose ${IMAGE_REGISTRY})
    #   - исключение: compose-файлы с ${VAR:-default} разрешены
    #   - assert: строгая грамматика {{...}} только uppercase
```

**Крайние случаи:**
- Манифест пуст → skip (early return, no failure)
- Файл не содержит плейсхолдеров → skip проверки грамматики, файл OK
- `${IMAGE_REGISTRY:-ghcr.io}` в compose → разрешён (whitelist runtime compose vars)
- `{{ $labels.x }}` в alert-rules.yml → не матчится strict grammar, оставлен как есть — **не FAIL** (валидируется только что нет старого синтаксиса `__...__` и `${...}`)
- Несколько синтаксисов в одном файле → FAIL с указанием строки и файла

### T3.2 — `tests/gates/test_gate_template_drift.py`

Новый gate-тест. Проверяет разрешимость всех шаблонов.

```python
@pytest.mark.gate
def test_all_templates_resolvable(manifest_path, ldd_trajectory):
    """Каждый template рендерится без unresolved плейсхолдеров."""
    # Вызывает template_engine.check_all()
    # Собирает diagnostics
    # assert ok == True, иначе fail с diagnostic для каждого unresolved файла
```

**Крайние случаи:**
- Манифест не найден → fail с diagnostic
- Стандартные переменные не доступны в CI (PLATFORM_DOMAIN) → gate использует default-пустое значение (allow_missing=False не применяется к опциональным переменным)
- Файл дуальной роли (alert-rules.yml) → содержит Prometheus-syntax `{{ $labels.x }}`, но engine НЕ проверяет его как плейсхолдеры (strict grammar); проверяет только UPPER_SNAKE

### T3.3 — Регистрация gate'ов в manifest

`core/entrypoint-manifest.yaml`, секция `gates:`:
```yaml
  - id: template-syntax
    description: All template files use unified {{UPPER_SNAKE}} syntax, no legacy __VAR__ or ${VAR} placeholders (except compose runtime vars)
    test_file: test_gate_template_syntax.py
  - id: template-drift
    description: All templates in template-manifest.yaml render without unresolved placeholders; dry-run check passes
    test_file: test_gate_template_drift.py
```

### T3.4 — Интеграция в gate pipeline

Gate-тесты запускаются в `make gate MODE=fast` и `make test MARKER=gates` автоматически (через `@pytest.mark.gate`). Никаких дополнительных изменений в Makefile не требуется — существующий механизм gate-pytest обнаруживает все тесты с маркером `gate` в `tests/gates/`.

### T3.5 — Удаление `core/templates/docker-compose.test.template`

Простое удаление файла. Проверить, что ни один файл не ссылается на него (F3 verification: 0 consumers).

### T3.6 — Миграция знаний в `core/modules/AGENTS.md`

Добавить раздел после строки 48 (шаблоны):

```markdown
## docker-compose.test.yml contract

Каждый Docker-модуль предоставляет `docker-compose.test.yml` — test-overlay,
параллельный production-конфигурации через container_name суффикс `-test`.

### Инварианты
- `container_name: <container>-test` для ВСЕХ контейнеров модуля — предотвращает конфликты с production
- `restart: "no"` — тестовые контейнеры не авто-перезапускаются
- Volumes: Docker-managed (не bind-mount)
- Port mappings: смещённые по правилу `1{port}` (e.g., 80→18080, 5432→15432) на 127.0.0.1
- Healthcheck: ускоренный (start_period=10s, interval=10s) для CI

### Collision policy
При коллизии `1{port}` с production портом — разработчик модуля выбирает:
(a) префикс `2` (e.g., 8000→28000)
(b) сдвиг разрядности (e.g., 3XXXX)
(c) явный свободный порт с TRAP[DECISION]

### Gate coverage
- test_gate_compose_no_base_image: нет L1-образа в production compose
- test_restart_consistency: restart-политика
- test_gate_container_name_consistency: container_name консистентность
- test_gate_healthcheck_contract: healthcheck контракты
```

### T3.7 — Acceptance criteria sweep

Итоговая проверка всех AC:

| AC | Формулировка (пересмотренная под B) | Способ проверки |
|----|--------------------------------------|-----------------|
| 1 | `rg '__[A-Z_]+__' templates/` → 0 | bash-scan |
| 2 | `${PLATFORM_DOMAIN}` sed → engine-call | grep install.sh |
| 3 | `make templates-check` → exit 0 | ручной запуск |
| 4 | `make templates-check --verbose` → exit 1 с diagnostic при unresolved | ручной + negative test |
| 5 | `test_gate_template_syntax` зелёный | pytest |
| 6 | `test_gate_template_drift` зелёный | pytest |
| 7 | `/opt/core/` → `{{PLATFORM_ROOT}}/core/` в template | grep sudo-whitelist.template |
| 8 | `{{MODULE_NAME}}` раскрыт при генерации sudoers | проверка логов deploy-modules на VPS |

---

## $TEST_SPEC

| Файл | Тип | Тесты | Маркеры |
|------|-----|-------|---------|
| `tests/test_template_engine.py` | unit | T1.4: 18 atomic тестов | static |
| `tests/gates/test_gate_template_syntax.py` | gate | T3.1: 1 параметризованный тест | gate |
| `tests/gates/test_gate_template_drift.py` | gate | T3.2: 1 параметризованный тест | gate |

Интеграция в существующие suites:
- `test_template_engine.py` → `make test MARKER=static` (автоматически, через pytest discovery)
- Gate-тесты → `make gate MODE=fast` и `make test MARKER=gates` (автоматически, через `@pytest.mark.gate`)
- `test-inventory-sync` после добавления файлов (регенерация inventory)

---

## $FILE_MANIFEST

### Создаются (5 файлов)

| Файл | Волна | Назначение |
|------|-------|------------|
| `core/internal/template_engine.py` | W1 | Python-ядро рендеринга |
| `core/internal/template-engine.sh` | W1 | Bash CLI-обёртка |
| `core/templates/template-manifest.yaml` | W1 | Единый манифест шаблонов |
| `tests/test_template_engine.py` | W1 | Unit-тесты движка |
| `tests/gates/test_gate_template_syntax.py` | W3 | Gate: единый синтаксис |
| `tests/gates/test_gate_template_drift.py` | W3 | Gate: разрешимость |

### Модифицируются (16 файлов)

| Файл | Волна | Изменения |
|------|-------|-----------|
| `Makefile` | W1 | `templates-render`, `templates-check` |
| `core/entrypoint-manifest.yaml` | W1/W3 | allowed_verbs + validate + gates |
| `core/AGENTS.md` | W1 | таблица операций + структура |
| `AGENTS.md` (root) | W1 | глоссарий + навигация |
| `core/internal/scaffold/add-project.sh` | W2 | `replace_placeholders()` → engine |
| `templates/template-backend/*` (~10 files) | W2 | `__VAR__` → `{{VAR}}` |
| `templates/template-frontend/*` (~10 files) | W2 | `__VAR__` → `{{VAR}}` |
| `templates/template-fullstack/*` (~10 files) | W2 | `__VAR__` → `{{VAR}}` |
| `templates/template-context/*` (~3 files) | W2 | `__VAR__` → `{{VAR}}` |
| `core/internal/scaffold/context-init.sh` | W2 | sed → engine |
| `core/modules/nginx/install.sh` | W2 | 4 sed-сайта → engine |
| `core/modules/monitoring/hooks/on-project-deploy.sh` | W2 | sed → engine |
| `core/templates/sudo-whitelist.template` | W2 | path fix + комментарии |
| `core/internal/bootstrap/deploy-modules.sh` | W2 | `generate_module_sudoers()` render-at-use |
| `core/modules/AGENTS.md` | W3 | §docker-compose.test.yml contract |

### Удаляются (8 файлов)

| Файл | Волна | Причина |
|------|-------|---------|
| `core/modules/backup-cron/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/modules/clickhouse/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/modules/hermes-agent/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/modules/nginx/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/modules/postgres/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/modules/redis/sudo-whitelist.conf` | W2 | Симлинк, render-at-use |
| `core/templates/docker-compose.test.template` | W3 | Орфан, знания → AGENTS.md |
| `core/modules/nginx/config/platform-default.conf` | W2 | → `platform-default.conf.template` |

### Перемещается (1 файл)

| Старый путь | Новый путь | Волна |
|-------------|------------|-------|
| `core/modules/nginx/config/platform-default.conf` | `core/modules/nginx/config/platform-default.conf.template` | W2 |

---

## Примечания по реализации

1. **`add-project.sh` — `replace_placeholders()` vs `render_project_template()`.** Текущая `replace_placeholders()` (строки 326-380) работает file-by-file. После миграции: `template-engine.sh render-all` с манифестом обрабатывает директорию рекурсивно. Сохранить `show_plan()`, `confirm()`, `checklist()` — они вне скоупа унификации.

2. **nginx `install.sh` — исключение API_KEY.** Строка 163 содержит `sed "s|^API_KEY=.*|API_KEY=\"...\"|"` для DNS-провайдера. Это секретный путь, не шаблон — оставить без изменений (упомянуть в комментарии `# NOT a template — secret injection`).

3. **`alert-rules.yml` — dual-role annotation.** В template-manifest.yaml — `dual_role: true`. Engine при `render_all` применяет strict grammar только к UPPER_SNAKE плейсхолдерам; Prometheus-синтаксис не затрагивается.

4. **`entrypoint-manifest.yaml` целостность.** `test_gate_manifest_integrity` и `test_gate_no_unregistered_entrypoint` падают на неконсистентности. После каждого добавления/удаления:
   - `make gate MODE=fast` → должен быть зелёный
   - `make test-inventory-sync` → обновить inventory

5. **Порядок изменений в W2.** Scaffold (T2.A) и Nginx (T2.B) независимы, но scaffold мигрирует КЛИЕНТОВ engine (add-project.sh) — engine должен быть стабилен из W1.

6. **BSD/GNU sed.** Engine на Python — нет проблем переносимости sed. Для bash-CLI: `mktemp` — POSIX, доступен на macOS и Linux.

---

## Сводка гипотез (для History)

| Гипотеза | Вердикт | Причина |
|----------|---------|---------|
| A (Committed + drift-gate) | Отклонена | Создаёт класс дрейфа, премисса «копии» неверна (симлинки) |
| B (Render-at-use) | **Принята** | Конструктивно исключает дрейф, формализует статус-кво |
| C (Eliminate templates) | Отклонена | Не решает механизмы 2–4; follow-up |
| D (Formalize symlinks) | Отклонена | Сохраняет 4 синтаксиса |
| E (envsubst/jinja2) | Отклонена | Коллизия `${}` с compose или лишняя зависимость |

## Открытые риски

1. **RISK[MEDIUM] `python3` доступность в bootstrap-пути VPS.** Engine на Python требует python3 на ноде. Сейчас python3 уже требуется (monitoring hook, discover_modules.py). При удалении этих зависимостей → пересмотреть TRAP[DECISION] о языке.
2. **RISK[LOW] `alert-rules.yml` dual-role.** Если Prometheus в будущем использует UPPER_SNAKE без `$` и пробелов (маловероятно, но возможно) — strict grammar может матчиться. Mitigation: в template-manifest явный `dual_role: true`, gate делает исключение для dual-role файлов.
3. **RISK[LOW] `make gate MODE=fast` performance.** Добавление 2 gate-тестов увеличивает время gate на ~2-5 секунд (рендер in-memory). Порог деградации: +15 секунд → оптимизировать параллелизацию pytest.
4. **RISK[LOW] Миграция `platform-default.conf` → `.template`.** Если overlay-конфиги или внешние скрипты ссылаются на файл по имени — сломаются. Mitigation: `rg 'platform-default.conf'` перед миграцией.

$END_DEVPLAN
