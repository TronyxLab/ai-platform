<!--
$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:      Закрыть gap между синтаксической валидацией и семантической верификацией артефактов,
              обнаруженный системным анализом DevPlan 019 (11 проблем, 10 из которых — missing checks).
              Три направления: Vhost Generation Gate (D1), Context Consistency Enforcement (D2),
              CI Readiness & Integration (D3).
DESCRIPTION:  Детальный план с суперпозицией для оставшихся открытых решений.
              D1: исправление 2 багов в add-vhost.sh + 3 unit-теста + Docker-harness gate-тест +
              VHOST_CONTRACT документация.
              D2: удаление поля `context` из ai-platform.yaml, вывод контекста из пути
              (9 файлов, blast radius ~45 строк).
              D3: gate-project-env-platform тест + gate-project-context-consistency тест +
              документация инвариантов (Inv2, Inv3).
RATIONALE:    DevPlan 019 закрыл 5 системных багов в deploy pipeline. Системный анализ
              (.ai/reports/019-systemic-analysis.md) выявил, что локальные тесты прошли зелёными
              (884 static + 242 contract), но ни один не проверил артефакты времени выполнения:
              сгенерированные vhost'ы, консистентность context↔директория, наличие .env.platform.
              Ключевой урок: каждый генератор артефактов должен иметь gate-тест с валидацией
              сгенерированного артефакта.
ACCEPTANCE_CRITERIA:
  AC-D1-VHOST: `make gate MODE=fast` включает `nginx -t` в Docker для сгенерированных vhost'ов
               (проекты с дефисами в имени + поддомены PLATFORM_DOMAIN).
  AC-D1-HYPHEN: Проект с именем `my-cool-app` → vhost использует `$upstream_my_cool_app` (underscores),
                nginx -t PASS.
  AC-D1-STALE: Повторный `render-all` с node.yaml, где проект удалён → старый vhost исчезает.
  AC-D2-CTX:   `context` удалён из ai-platform.yaml (schema + writers + templates + тесты).
               adopt-project.sh выводит org из пути `projects/<org>/<project>/`.
  AC-D2-GATE:  `make gate MODE=fast` валидирует, что все проекты из node.yaml имеют консистентный
               путь (projects/<context>/<project>/).
  AC-D3-ENV:   `make gate MODE=fast` проверяет наличие `.env.platform` во всех зарегистрированных
               проектах.
  AC-GATE:     `make gate MODE=fast` зелёный до и после всех изменений.
  AC-NO-REG:   `make test MARKER=all` зелёный (все 1200+ тестов).
IMPLEMENTS:   Выводы системного анализа 019-systemic-analysis.md, разделы 4-7.
IMPACTS:      core/internal/scaffold/add-vhost.sh (2 bug fixes),
              core/schemas/ai-platform.schema.json (удаление context),
              core/internal/scaffold/add-project.sh (удаление генерации context),
              core/internal/scaffold/adopt-project.sh (вывод org из пути),
              templates/template-{backend,frontend,fullstack}/ai-platform.yaml (удаление context),
              tests/gates/test_gate_vhost_nginx_t.py (НОВЫЙ: Docker-harness gate),
              tests/gates/test_gate_project_context.py (НОВЫЙ: context consistency gate),
              tests/gates/test_gate_project_env.py (НОВЫЙ: .env.platform gate),
              tests/test_add_vhost.py (3 новых теста),
              tests/test_project_schema.py (обновление fixture),
              tests/test_upload.py (обновление fixture),
              tests/test_adopt_project_org_validation.py (обновление тестов),
              core/modules/nginx/AGENTS.md (НОВЫЙ: VHOST_CONTRACT),
              AGENTS.md (обновление Inv3).
REQUIRES:     Ветка от origin/main (019 смержен), `make gate MODE=fast` зелёный, working tree чистый.
$END_DEVPLAN
-->

# DevPlan: 020-deploy-pipeline-hardening

**Дата:** 2026-07-20
**Ветка:** `020-deploy-pipeline-hardening` (от origin/main)
**Предыдущий план:** 019-deploy-pipeline-gaps (смержен в main)

---

## 1. Requirements Analysis — Key Success Criteria

| # | Критерий | Метод проверки | Приоритет |
|---|----------|---------------|-----------|
| **SC-D1.1** | `$upstream_my_cool_app` (underscores) для проекта с дефисом | Unit тест: vhost body для `my-cool-app` → содержит `$upstream_my_cool_app` | P0 |
| **SC-D1.2** | Docker nginx -t PASS для сгенерированных vhost'ов | `make gate MODE=fast` запускает `nginx:alpine` с vhost'ами → exit 0 | P0 |
| **SC-D1.3** | `render-all` удаляет stale vhost'ы удалённых проектов | Повторный render-all без проекта → vhost исчезает из overlay dir | P0 |
| **SC-D2.1** | `context` удалён из ai-platform.schema.json required | JSON Schema валидация принимает ai-platform.yaml без context | P1 |
| **SC-D2.2** | `make new-project` не пишет `context:` в ai-platform.yaml | Создать проект → в YAML нет поля context | P1 |
| **SC-D2.3** | `make adopt-project` выводит org из пути | `adopt-project DIR=projects/tronyx-lab/foo` → org=tronyx-lab | P1 |
| **SC-D3.1** | Gate проверяет .env.platform во всех проектах | `make gate MODE=fast` → проверка наличия .env.platform | P1 |
| **SC-GATE** | `make gate MODE=fast` зелёный | exit 0 | P0 |

---

## 2. Architecture Overview

### 2.1 Три направления плана (из системного анализа)

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DevPlan 020: Hardening                          │
├───────────────────┬───────────────────┬─────────────────────────────┤
│ D1: Vhost Gate    │ D2: Context       │ D3: CI Readiness            │
│ (P1,P2,P3,P5,P10) │ (P4,P6,P11)       │ (P7,P8,P9)                  │
├───────────────────┼───────────────────┼─────────────────────────────┤
│ Fix 2 bugs        │ Remove context    │ gate-project-env-platform   │
│ +3 unit tests     │ field from YAML   │ gate-project-context        │
│ +gate-vhost-nginx │ +derive from path │ +документация Inv2/Inv3     │
│ +VHOST_CONTRACT   │ +9 files changed  │                             │
└───────────────────┴───────────────────┴─────────────────────────────┘
```

### 2.2 Data Flow — Vhost Generation & Validation (D1)

```
make render-vhosts NODE=<n>
  └─► add-vhost.sh --render-all
       ├─ read_node_yaml_projects()  ← python3 + yaml
       ├─ check_duplicate_domains()
       ├─ for each project:
       │   ├─ resolve_cert_domain()  ← wildcard vs personal cert
       │   ├─ generate_vhost_body()  ← [FIX] normalize hyphens
       │   └─ compute_body_hash()
       ├─ [FIX] grep "GENERATED"    ← было head -1 (баг)
       ├─ atomic mv → overlay dir
       └─ nginx_t_harness()         ← docker run nginx -t

make gate MODE=fast
  └─► tests/gates/test_gate_vhost_nginx_t.py  [NEW]
       ├─ Создаёт эталонный node.yaml (проекты с дефисами + поддомены)
       ├─ Вызывает add-vhost.sh --render-all
       ├─ Поднимает nginx:alpine с vhost'ами
       └─ Проверяет nginx -t exit 0
```

### 2.3 Data Flow — Context Resolution (D2)

```
ДО (019):
  add-project.sh: context="${9:-personal}"  → writes to ai-platform.yaml
  adopt-project.sh: grep context: from ai-platform.yaml → PROJECT_ORG
  converge.sh: data.get('context', '') from node.yaml (not ai-platform.yaml)

ПОСЛЕ (020):
  context = basename(dirname(dirname(realpath ai-platform.yaml)))
  Пример: projects/tronyx-lab/tronyx-site/ai-platform.yaml → tronyx-lab

  add-project.sh:      больше не пишет context в YAML
  adopt-project.sh:    выводит org из пути $PROJECT_DIR
  converge.sh:         без изменений (читает context из node.yaml, не ai-platform.yaml)
  ai-platform.schema:  context удалён из required + properties
```

---

## 3. Design Decisions — Superposition Analysis

### 3.1 D1.1: Нормализация дефисов в nginx-переменных

**Файл**: `core/internal/scaffold/add-vhost.sh:423`

**Корневая причина**: `$upstream_${project_name}` использует сырое имя проекта, которое может содержать дефисы (`my-cool-app`). Nginx парсит `$upstream_my-cool-app` как `$upstream_my` минус `cool` минус `app` → syntax error.

## SUPERPOSITION: D1.1 — Способ нормализации имён nginx-переменных

### Option A: `tr` замена дефисов на underscore в самом `project_name` [score: 9/10]
```bash
# В render_all(), перед вызовом generate_vhost_body:
local project_name_normalized="${project_name//-/_}"
# Передать normalised имя в generate_vhost_body
```
**Trade-offs**: Минимальное изменение (~3 строки). Не меняет `server_name` (там дефисы допустимы). Не меняет имя файла vhost (там дефисы допустимы).
**Best when**: нужен минимальный фикс, не затрагивающий другие части vhost.
**Риск**: нужно не забыть нормализовать везде, где project_name используется в nginx variable context.

### Option B: Нормализация внутри `generate_vhost_body()` [score: 7/10]
```bash
# Внутри функции, локально:
local nginx_safe_name="${project_name//-/_}"
```
**Trade-offs**: Локализовано в одной функции. Но project_name уже используется в server_name, ssl_certificate path и других местах — там дефисы допустимы.
**Best when**: не хотим менять интерфейс generate_vhost_body.
**Риск**: нужно явно указать, где использовать `nginx_safe_name`, а где `project_name`.

### Option C: Валидация на входе — запретить дефисы в именах проектов [score: 2/10]
**Trade-offs**: Не требует изменений в add-vhost.sh. Но ломает обратную совместимость — `tronyx-site`, `dance-site` уже используют дефисы.
**Риск**: Неприемлемо для существующих проектов.

### Recommendation: **Option A** — `bash` parameter expansion `${var//-/_}`, ~3 строки в `render_all()`.

**Collapse signal:** Автоматический выбор Option A.

---

### 3.2 D1.2: `head -1` vs GENERATED маркер на строке 2

**Файл**: `core/internal/scaffold/add-vhost.sh:828`

**Корневая причина**: `generated_header()` (стр. 119-132) выводит две строки:
```
# ============================================================   ← строка 1
# GENERATED by add-vhost.sh — DO NOT EDIT                        ← строка 2
```
`head -1` читает только первую строку → grep никогда не находит "GENERATED" → старые vhost'ы удалённых проектов не удаляются.

## SUPERPOSITION: D1.2 — Способ детекции GENERATED-маркера

### Option A: Заменить `head -1` на `head -2` [score: 3/10]
```bash
if head -2 "$gen_file" 2>/dev/null | grep -q "GENERATED by add-vhost.sh"; then
```
**Trade-offs**: Минимальное изменение (1 символ). Но хрупко: если формат заголовка снова изменится — баг вернётся.
**Best when**: нужен минимальный фикс.
**Риск**: всё ещё зависит от позиции маркера в первых 2 строках.

### Option B: Заменить `head -1` на `grep` без head [score: 9/10]
```bash
if grep -q "GENERATED by add-vhost.sh" "$gen_file" 2>/dev/null; then
```
**Trade-offs**: Читает весь файл (максимум ~50 строк для vhost), но надёжно — не зависит от позиции маркера. Простое и идемпотентное решение.
**Best when**: надёжность важнее микрооптимизации.
**Риск**: grep читает весь файл (пренебрежимо для vhost'ов).

### Option C: Отдельный маркерный файл `.generated` [score: 4/10]
**Trade-offs**: Полностью устраняет парсинг содержимого.
**Best when**: нужна максимальная надёжность.
**Риск**: усложняет логику (нужно создавать и удалять маркерный файл). Избыточно для этой задачи.

### Recommendation: **Option B** — `grep -q` без `head`.

**Collapse signal:** Автоматический выбор Option B.

---

### 3.3 D2: Consumer `context` в adopt-project.sh

**Файл**: `core/internal/scaffold/adopt-project.sh:118-121`

**Текущий код**:
```bash
PROJECT_ORG=$(grep -E '^\s*context:\s*' "$yaml_file" 2>/dev/null | head -1 | awk '{print $2}')
if [[ -z "$PROJECT_ORG" ]]; then
    PROJECT_ORG="personal"
fi
```

## SUPERPOSITION: D2 — Источник org для adopt-project.sh

### Option A: Вывод из пути [score: 9/10]
```bash
# projects/<org>/<project>/ai-platform.yaml → org = dirname(dirname(realpath))
local _project_dir_abs
_project_dir_abs="$(cd "$(dirname "$yaml_file")" && pwd -P)"
PROJECT_ORG="$(basename "$(dirname "$_project_dir_abs")")"
```
**Trade-offs**: Не зависит от содержимого YAML. Работает для любого существующего проекта. ~3 строки.
**Best when**: структура директорий `projects/<org>/<project>/` соблюдается.
**Риск**: если проект лежит не в `projects/<org>/<project>/`, логика сломается. Но это системный инвариант платформы.

### Option B: `--org` параметр обязателен [score: 6/10]
**Trade-offs**: Явный контроль.
**Best when**: оператор хочет явно указать org.
**Риск**: ломает обратную совместимость (`make adopt-project` без `--org` перестанет работать).

### Recommendation: **Option A** — вывод из пути. `--org` остаётся опциональным оверрайдом.

**Collapse signal:** Автоматический выбор Option A.

---

## 4. Step-by-Step Implementation Plan

### Wave 1: D1 bug fixes (add-vhost.sh, 2 строки)

**Task 1.1 — Нормализация дефисов в nginx-переменных**
- Файл: `core/internal/scaffold/add-vhost.sh`
- В функции `generate_vhost_body()` (строка 423) или в `render_all()`: заменить `${project_name}` на `${project_name//-/_}` в контексте nginx-переменных
- Локация: `set \$upstream_${project_name}` (строка 423) и `proxy_pass \$upstream_${project_name}` (строки 424, 437)
- Подход: создать локальную переменную `nginx_safe_name="${project_name//-/_}"` в `generate_vhost_body()` и использовать её для upstream-переменных
- Изменение: ~5 строк
- Верификация: unit-тест Wave 2 Task 2.1

**Task 1.2 — `head -1` → `grep -q` для GENERATED маркера**
- Файл: `core/internal/scaffold/add-vhost.sh`
- Строка 828: заменить `head -1 "$gen_file" 2>/dev/null | grep -q "GENERATED by add-vhost.sh"` на `grep -q "GENERATED by add-vhost.sh" "$gen_file" 2>/dev/null`
- Изменение: 1 строка
- Верификация: unit-тест Wave 2 Task 2.3

---

### Wave 2: D1 unit-тесты (test_add_vhost.py, 3 теста)

**Task 2.1 — `test_add_vhost_hyphen_normalization`**
- Файл: `tests/test_add_vhost.py`
- Сценарий: создать проект `my-cool-app` → сгенерировать vhost → assert тело содержит `$upstream_my_cool_app` (underscores) и НЕ содержит `$upstream_my-cool-app` (hyphens)
- Использует `generate_vhost_body` напрямую (bash source) или через `subprocess`
- ~50 строк

**Task 2.2 — `test_add_vhost_wildcard_cert_resolution`**
- Файл: `tests/test_add_vhost.py`
- Сценарий: `resolve_cert_domain("app.tronyx.ru", "tronyx.ru")` → wildcard cert path `*.tronyx.ru`
- Сценарий: `resolve_cert_domain("tronyx.ru", "tronyx.ru")` → wildcard cert path `*.tronyx.ru` (apex domain)
- Сценарий: `resolve_cert_domain("myapp.com", "tronyx.ru")` → personal cert path `myapp.com`
- ~60 строк

**Task 2.3 — `test_add_vhost_stale_cleanup_on_rerender`**
- Файл: `tests/test_add_vhost.py`
- Сценарий:
  1. `render_all` с node.yaml содержащим project A и project B → 2 vhost файла
  2. `render_all` с node.yaml содержащим только project A → 1 vhost файл (project B удалён)
  3. Assert: vhost project B отсутствует в overlay dir
- Проверяет fix Task 1.2 (grep находит GENERATED маркер) + общую логику render_all
- ~70 строк

---

### Wave 3: D1 gate-тест Docker-harness (НОВЫЙ файл)

**Task 3.1 — `test_gate_vhost_nginx_t.py`**
- Файл: `tests/gates/test_gate_vhost_nginx_t.py` (НОВЫЙ)
- Маркер: `@pytest.mark.gate`
- Алгоритм:
  1. Создать эталонный `node.yaml` в `tmp_path`:
     ```yaml
     domain: test-platform.local
     projects:
       - name: my-cool-app
         domain: app.test-platform.local
         repo: git@github.com:test/my-cool-app.git
       - name: simple-site
         domain: simple.test-platform.local
         repo: git@github.com:test/simple-site.git
       - name: independent-site
         domain: independent.com
         repo: git@github.com:test/independent-site.git
     ```
  2. Вызвать `add-vhost.sh --render-all --node-yaml <path> --output-dir <tmp_path>/overlays/nginx/`
  3. Создать минимальный `nginx.conf` для Docker (включает vhost'ы):
     ```
     events { worker_connections 1024; }
     http {
         resolver 127.0.0.11 valid=30s ipv6=off;
         include /etc/nginx/conf.d/*.conf;
     }
     ```
  4. `docker run --rm -v <overlay_dir>:/etc/nginx/conf.d:ro -v <nginx.conf>:/etc/nginx/nginx.conf:ro nginx:alpine nginx -t`
  5. Assert exit code 0
- Edge cases:
  - Docker недоступен → skip test (не fail)
  - `my-cool-app` → nginx -t PASS (проверяет fix Task 1.1)
  - `independent.com` → personal cert path (не wildcard)
- ~100 строк

**Task 3.2 — Регистрация в CI**
- Файл: `core/entrypoint-manifest.yaml` (добавить `gate-vhost-nginx-t` в список gate-таргетов)
- Файл: `Makefile` (добавить `.PHONY` target)
- Файл: `core/AGENTS.md` (добавить строку в таблицу канонических операций)

---

### Wave 4: D2 context removal (9 файлов)

**Task 4.1 — Schema: удалить `context` из ai-platform.schema.json**
- Файл: `core/schemas/ai-platform.schema.json`
- Строка 8: удалить `"context"` из `required`: `"required": ["name", "type", "target_node"]`
- Строки 22-27: удалить весь блок `"context"` из `properties`
- Изменение: -7 строк

**Task 4.2 — add-project.sh: убрать генерацию context**
- Файл: `core/internal/scaffold/add-project.sh`
- Строка 97: убрать `--context` из CLI-аргументов (или deprecated warning)
- Строка 129: убрать проверку `-z "$CONTEXT"` для register-режима
- Строка 236: убрать параметр `context` из функции (или игнорировать)
- Строка 260: убрать `context: ${context}` из heredoc-шаблона ai-platform.yaml
- Строки 804, 816: убрать передачу CONTEXT
- Изменение: ~6 строк

**Task 4.3 — adopt-project.sh: выводить org из пути**
- Файл: `core/internal/scaffold/adopt-project.sh`
- Строки 118-121: заменить `grep context:` на вычисление из пути:
  ```bash
  local _project_dir_abs
  _project_dir_abs="$(cd "$(dirname "$yaml_file")" && pwd -P)"
  PROJECT_ORG="$(basename "$(dirname "$_project_dir_abs")")"
  ```
- Строка 211: убрать `context: ${PROJECT_ORG}` из генерации YAML
- Изменение: ~5 строк

**Task 4.4 — Шаблоны: убрать `context: personal`**
- Файлы: `templates/template-backend/ai-platform.yaml:15`, `templates/template-frontend/ai-platform.yaml:15`, `templates/template-fullstack/ai-platform.yaml:15`
- Удалить строку `context: personal` из всех трёх
- Изменение: -3 строки

**Task 4.5 — Тесты: обновить fixture**
- Файл: `tests/test_project_schema.py` — убрать `"context": "personal"` из всех fixture (~11 точек)
- Файл: `tests/test_upload.py:86` — убрать `"context": "personal"`
- Файл: `tests/test_adopt_project_org_validation.py` — обновить тесты (org из пути, не из YAML)
- Изменение: ~15 строк

---

### Wave 5: D2 gate-тест context consistency (НОВЫЙ файл)

**Task 5.1 — `test_gate_project_context.py`**
- Файл: `tests/gates/test_gate_project_context.py` (НОВЫЙ)
- Маркер: `@pytest.mark.gate`
- Алгоритм:
  1. Найти все `ai-platform.yaml` в `projects/` (glob `projects/*/*/ai-platform.yaml`)
  2. Для каждого: извлечь контекст из пути = `basename(dirname(dirname(abspath)))` — второй сегмент пути `projects/<context>/<project>/`
  3. Проверить, что контекст соответствует ожидаемому (из node.yaml или из структуры директорий)
  4. Проверить, что в YAML больше нет поля `context` (post-D2 удаление)
- ~50 строк

**Task 5.2 — `test_gate_project_env_platform.py`**
- Файл: `tests/gates/test_gate_project_env_platform.py` (НОВЫЙ)
- Маркер: `@pytest.mark.gate`
- Алгоритм:
  1. Найти все `ai-platform.yaml` в `projects/`
  2. Для каждого: проверить существование `.env.platform` в той же директории
  3. Если `.env.platform` существует — проверить валидность: `provides ⊆ profiles` из `platform-env.yaml`
- ~50 строк

---

### Wave 6: D3 документация

**Task 6.1 — VHOST_CONTRACT в core/modules/nginx/**
- Файл: `core/modules/nginx/AGENTS.md` (НОВЫЙ или обновление существующего)
- Содержание:
  - Минимальные требования к сгенерированному vhost:
    1. Nginx variable names: только `[a-zA-Z0-9_]` (дефисы заменены на underscore)
    2. `resolver 127.0.0.11 valid=30s ipv6=off;` на уровне server
    3. `proxy_pass` через переменную (никогда статический хост)
    4. Let's Encrypt cert paths (не self-signed default.crt)
    5. GENERATED-маркер во второй строке
    6. `http2 on;` отдельной директивой (не `listen ... http2`)
  - Контракт с `add-vhost.sh`: каждый сгенерированный vhost должен проходить `nginx -t`
  - Gate-тест: `nginx -t` в Docker с эталонным `node.yaml`

**Task 6.2 — AGENTS.md: обновить Invariant 3**
- Файл: `AGENTS.md` (root)
- Инвариант 3: `org = context. tronyx161 — исходный репозиторий. Каждый контекст — отдельная GitHub-организация.`
- Добавить: `context определяется из физического пути projects/<context>/<project>/, поле context в ai-platform.yaml УДАЛЕНО (DevPlan 020).`

**Task 6.3 — AGENTS.md: документировать `make new-project` как единственный способ**
- Файл: `AGENTS.md` (root), секция scaffold или Invariant 2
- Добавить: `make new-project — единственный способ создания проекта. Ручное создание требует make project-sync-env.`

---

## 5. File Manifest

| # | Файл | Статус | Изменение | Строк | Волна |
|---|------|--------|-----------|-------|-------|
| 1 | `core/internal/scaffold/add-vhost.sh` | MODIFY | Нормализация дефисов (стр. 423) | +3 | W1 |
| 2 | `core/internal/scaffold/add-vhost.sh` | MODIFY | `head -1` → `grep -q` (стр. 828) | -1/+1 | W1 |
| 3 | `tests/test_add_vhost.py` | MODIFY | `test_add_vhost_hyphen_normalization` | +50 | W2 |
| 4 | `tests/test_add_vhost.py` | MODIFY | `test_add_vhost_wildcard_cert_resolution` | +60 | W2 |
| 5 | `tests/test_add_vhost.py` | MODIFY | `test_add_vhost_stale_cleanup_on_rerender` | +70 | W2 |
| 6 | `tests/gates/test_gate_vhost_nginx_t.py` | НОВЫЙ | Docker-harness gate тест | +100 | W3 |
| 7 | `core/schemas/ai-platform.schema.json` | MODIFY | Удалить `context` из required + properties | -7 | W4 |
| 8 | `core/internal/scaffold/add-project.sh` | MODIFY | Убрать генерацию `context:` в YAML | -6 | W4 |
| 9 | `core/internal/scaffold/adopt-project.sh` | MODIFY | Выводить org из пути | +3/-2 | W4 |
| 10 | `templates/template-backend/ai-platform.yaml` | MODIFY | Удалить `context: personal` | -1 | W4 |
| 11 | `templates/template-frontend/ai-platform.yaml` | MODIFY | Удалить `context: personal` | -1 | W4 |
| 12 | `templates/template-fullstack/ai-platform.yaml` | MODIFY | Удалить `context: personal` | -1 | W4 |
| 13 | `tests/test_project_schema.py` | MODIFY | Обновить fixture (убрать context) | -11 | W4 |
| 14 | `tests/test_upload.py` | MODIFY | Обновить fixture (убрать context) | -1 | W4 |
| 15 | `tests/test_adopt_project_org_validation.py` | MODIFY | Обновить тесты (org из пути) | ~4 | W4 |
| 16 | `tests/gates/test_gate_project_context.py` | НОВЫЙ | Context consistency gate | +50 | W5 |
| 17 | `tests/gates/test_gate_project_env.py` | НОВЫЙ | .env.platform presence gate | +50 | W5 |
| 18 | `core/modules/nginx/AGENTS.md` | НОВЫЙ | VHOST_CONTRACT | +40 | W6 |
| 19 | `AGENTS.md` (root) | MODIFY | Обновить Inv3 + new-project докум. | +5 | W6 |

**Total: 19 файлов, ~450 строк (NET: ~300 строк нового кода, ~150 строк удалений/изменений).**

**НЕ ТРЕБУЕТ изменений (подтверждено аудитом):**
- `core/internal/bootstrap/converge.sh` — читает `context` из node.yaml, не ai-platform.yaml
- `core/internal/deploy/deploy-project.sh` — не читает context
- `core/internal/catalog/generate-catalog.sh` — не читает context
- `core/schemas/node.schema.json` — `context` в node.yaml остаётся (другой контракт)
- `core/internal/bootstrap/node-lifecycle.sh` — не читает context из ai-platform.yaml

---

## 6. Verification Plan

### Pre-merge verification (локально)

| # | Проверка | Команда | Ожидаемый результат | Волна |
|---|----------|---------|---------------------|-------|
| **V1** | Unit: hyphen normalization | `pytest tests/test_add_vhost.py::test_add_vhost_hyphen_normalization -v` | PASS | W2 |
| **V2** | Unit: wildcard cert resolution | `pytest tests/test_add_vhost.py::test_add_vhost_wildcard_cert_resolution -v` | PASS | W2 |
| **V3** | Unit: stale cleanup on rerender | `pytest tests/test_add_vhost.py::test_add_vhost_stale_cleanup_on_rerender -v` | PASS | W2 |
| **V4** | Gate: vhost nginx -t | `pytest tests/gates/test_gate_vhost_nginx_t.py -v` | PASS (или SKIP если нет Docker) | W3 |
| **V5** | Schema: ai-platform.yaml без context | `make validate` на проекте без context поля | PASS | W4 |
| **V6** | Scaffold: new-project без context | `make new-project NAME=test-ctx TEMPLATE=backend` → в YAML нет `context:` | PASS | W4 |
| **V7** | Scaffold: adopt-project org из пути | `make adopt-project DIR=projects/tronyx-lab/test-org` → org=tronyx-lab | PASS | W4 |
| **V8** | Gate: context consistency | `pytest tests/gates/test_gate_project_context.py -v` | PASS | W5 |
| **V9** | Gate: .env.platform presence | `pytest tests/gates/test_gate_project_env.py -v` | PASS | W5 |
| **V10** | Full gate suite | `make gate MODE=fast` | exit 0 | All |
| **V11** | All tests | `make test MARKER=all` | exit 0 | All |

### Ручная верификация (на tronyx-vps)

| # | Проверка | Команда | Ожидаемый результат |
|---|----------|---------|---------------------|
| **M1** | render-vhosts → nginx -t | `make render-vhosts NODE=tronyx-vps && ssh tronyx-vps "docker compose exec nginx nginx -t"` | syntax is ok |
| **M2** | Проект с дефисом: nginx healthy | `make deploy PROJECT=tronyx-site` → nginx не в рестарт-лупе | healthy |
| **M3** | adopt-project: org из пути | `make adopt-project DIR=projects/tronyx-lab/new-project` | зарегистрирован с org=tronyx-lab |

---

## 7. Rollback Plan

Все изменения в D2 — деструктивные (удаление поля context). Rollback требует восстановления поля в schema, writers и тестах.

**D1 rollback (add-vhost.sh):**
- Revert Task 1.1 → переменные снова с дефисами (баг возвращается, но nginx -t покажет)
- Revert Task 1.2 → `head -1` снова не видит маркер (баг возвращается, старые vhost'ы не чистятся)
- Удалить 3 новых теста + gate

**D2 rollback (context removal):**
- Восстановить `"context"` в `required` schema (строка 8)
- Восстановить блок `"context"` в `properties` (строки 22-27)
- Восстановить генерацию `context:` в add-project.sh
- Восстановить `grep context:` в adopt-project.sh
- Восстановить `context: personal` в 3 шаблонах
- Восстановить тестовые fixture

**D3 rollback (gates + docs):**
- Удалить 2 новых gate-теста
- VHOST_CONTRACT и обновления AGENTS.md — не блокирующие (можно оставить)

---

## 8. Что НЕ входит в 020 (отложено на 021)

| Проблема | Причина отсрочки |
|----------|-----------------|
| **P4:** PLATFORM_DOMAIN рефакторинг (единая функция `resolve_platform_domain()`) | Требует выделения новой функции в `core/lib/`, затрагивает 3 файла, нужен отдельный план с аудитом всех consumers |
| **P8:** CI-деплой для трёх проектов (создание `tronyx-lab/ai-platform`, CI workflows) | Операционная задача, не код. Требует ручных действий в GitHub org |
| **P9:** Автосоздание test-сетей в component-тестах | Требует изменений в `conftest.py` (session fixture), нужен отдельный тестовый план |

---

## 9. Зависимости и порядок выполнения

```
Wave 1 (D1 bug fixes) ─────────────────────────────────────────────────┐
  ├─► Wave 2 (D1 unit tests) ── зависит от W1 ─────────────────────────┤
  │     ├─► Wave 3 (D1 gate test) ── зависит от W1 ────────────────────┤
  │                                                                     │
Wave 4 (D2 context removal) ── НЕЗАВИСИМО от W1-W3 ────────────────────┤
  ├─► Wave 5 (D2 gate tests) ── зависит от W4 ─────────────────────────┤
  │                                                                     │
Wave 6 (Docs) ── НЕЗАВИСИМО ────────────────────────────────────────────┤
                                                                        │
Все волны ──► make gate MODE=fast ──► make test MARKER=all ──► merge ──┘
```

**Параллелизм:** Wave 1-3 (D1) и Wave 4-5 (D2) могут выполняться параллельно разными агентами — они не пересекаются по файлам.
