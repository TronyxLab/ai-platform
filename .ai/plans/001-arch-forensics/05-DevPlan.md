<!-- GREP_SUMMARY: DevPlan, typed-contract, invoke-module-interface, module-yaml-interfaces, gate-cross-layer, call-sites, 3-waves, cross-layer-isolation -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Debt Intake → ◇ Architecture Overview → ◇ Draft Code Graph → ◇ Data Flow → ◇ §TASKS (3 waves) → ◇ §PARALLEL_GROUPS → ◇ §TEST_SPEC → ◇ Edge Cases → ◇ File Manifest → ◇ TRAP[DECISION] → ◇ Risk Register → ⎋ Next Steps -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** DevPlan внедрения Typed Contract для 6 runtime cross-layer вызовов — замена невидимого `bash "$variable"` на `invoke_module_interface` с регистрацией интерфейсов в `module.yaml.interfaces`.
- **DESCRIPTION:** 3-волновой план: W1 (Foundation) — D4-схема + lib + 13 module.yaml + 6 call sites; W2 (Gate #8 v2) — статическая валидация typed contract + manifest; W3 (Documentation) — cross-layer таблица + healthcheck.sh fix + D4-схема в modules/AGENTS.md.
- **RATIONALE:** Выбрана гипотеза H1 (Typed Contract) с двумя обязательными доработками сверх брифа: (1) dispatch читает путь хука из `module.yaml.hooks.*` (не hardcode), решая проблему nginx с нестандартным путём; (2) exit code 0 при незарегистрированном интерфейсе = graceful skip (не ошибка), решая проблему system-модуля platform-secrets без healthcheck.sh. H3 (Callback Registry) отвергнут — централизованный registry создаёт single point of contention при параллельной разработке и раздваивает source of truth (module.yaml + registry.sh).
- **ACCEPTANCE_CRITERIA:**
  1. 13 `module.yaml` содержат поле `interfaces` (массив строк, может быть пустым — minio)
  2. `core/lib/module-interface.sh` существует, содержит `invoke_module_interface()` с exit codes: 0=success/skip, 1=script-failed, 2=invalid
  3. 6 call sites используют `invoke_module_interface` вместо `bash "$variable"`
  4. `rg 'bash "\$(hc_script|install_script|healthcheck_script|hook_script)"' core/internal/` → 0 результатов
  5. `core/AGENTS.md` cross-layer правило: `internal/` может вызывать `modules/` через typed contract (`invoke_module_interface` + `interfaces`)
  6. `core/entrypoints/healthcheck.sh:12-13` консистентно с `core/AGENTS.md` — «internal → modules через typed contract»
  7. Gate #8 красный если: (a) `bash modules/<name>/...` найден в `internal/`; (b) `invoke_module_interface <name> <iface>` но `<iface>` не в `module.yaml.interfaces`
  8. Gate #8 зелёный на финальном состоянии (все 6 вызовов используют invoke + зарегистрированы)
  9. `make gate MODE=fast` зелёный
- **IMPLEMENTS:** Brief-CallSites.md (детализация W2 из 04-DevPlan.md), skill `superposition` (режим FULL, коллапс через опрос), skill `arch-patterns` (AI-First Architecture — typed public contracts)
- **IMPACTS:** `core/modules/AGENTS.md` (D4 схема + interfaces), `core/lib/module-interface.sh` (NEW), `core/lib/paths.sh` (source нового lib), `core/modules/*/module.yaml` ×13 (+ поле `interfaces`), `core/internal/bootstrap/node-lifecycle.sh:842-846`, `core/internal/bootstrap/deploy-modules.sh:333-341,538,571`, `core/internal/deploy/deploy-project.sh:729-734,757-763`, `core/entrypoints/healthcheck.sh:12-13` (контрадикция), `core/AGENTS.md` (cross-layer таблица), `tests/test_cross_layer_imports.py` (Gate #8 логика), `tests/gates/test_gate_cross_layer.py` (обновление expected behaviour), `core/entrypoint-manifest.yaml` (gate описание)
- **REQUIRES:** `Brief-CallSites.md`, `core/modules/AGENTS.md` (текущая D4 схема), `tests/test_cross_layer_imports.py` (текущий Gate #8), `core/lib/paths.sh` (PATHS_MODULES_DIR), `core/lib/yaml_read.sh` (yaml_get_list для валидации), `core/entrypoint-manifest.yaml` (gate регистрация)

$START_DEVPLAN

# DevPlan: Typed Contract for Cross-Layer Module Invocation

---

## §Debt Intake

| Finding | Location | Classification | Disposition |
|---------|----------|---------------|-------------|
| INVARIANT COLLAPSE | `core/AGENTS.md` cross-layer vs 6 runtime вызовов | CRITICAL | **IN_SCOPE** — W3 doc fix |
| `healthcheck.sh:12` контрадикция | `core/entrypoints/healthcheck.sh` | HIGH | **IN_SCOPE** — W3 doc fix |
| `_looks_like_path` слеп к переменным | `tests/test_cross_layer_imports.py:121-129` | KNOWN | **IN_SCOPE** — W2 gate v2 заменяет логику обнаружения |
| Gate #8 ложный зелёный | `test_gate_cross_layer.py` + `test_cross_layer_imports.py` | CRITICAL | **IN_SCOPE** — W2 gate v2 |
| nginx hook нестандартный путь | `core/modules/nginx/module.yaml:31` (`nginx_reload_hook.sh`) | MEDIUM | **IN_SCOPE** — dispatch читает путь из module.yaml |
| platform-secrets без healthcheck.sh | `core/modules/platform-secrets/` (install_type: system) | LOW | **IN_SCOPE** — W1 graceful skip через exit code 0 |
| TRAP[DECISION] Makefile:74-75 (up→provision) | Makefile | KNOWN | **DEFER** — задокументировано, вне скоупа |
| TRAP[DECISION] langfuse base.yml:117 | langfuse/docker-compose.base.yml | KNOWN | **DEFER** — accepted duplication |

---

## §1. Architecture Overview

### Current State: INVARIANT COLLAPSE

```
┌─── AGENTS.md cross-layer rule ──────────────────────────┐
│  internal/ → internal/, lib/  (modules/ ЗАПРЕЩЁН)       │
└─────────────────────────────────────────────────────────┘
                         ⚡ ПРОТИВОРЕЧИТ
┌─── 6 runtime вызовов в internal/ ───────────────────────┐
│  bash "$hc_script"          node-lifecycle.sh:842       │
│  bash "$install_script"     deploy-modules.sh:333       │
│  bash "$healthcheck_script" deploy-modules.sh:538,571   │
│  bash "$hook_script"        deploy-project.sh:729,757   │
└─────────────────────────────────────────────────────────┘
                         ⚡ НЕВИДИМЫ ДЛЯ
┌─── Gate #8 (test_cross_layer_imports.py) ───────────────┐
│  _looks_like_path требует "/" в строковом литерале      │
│  bash "$variable" → / нет → classified non-path → skip  │
│  Результат: «0 violations» — ЛОЖНЫЙ ЗЕЛЁНЫЙ             │
└─────────────────────────────────────────────────────────┘
```

### Target State: Typed Contract

```
┌─── AGENTS.md cross-layer rule v2 ───────────────────────┐
│  internal/ → internal/, lib/, modules/ (через typed      │
│  contract: invoke_module_interface + interfaces)         │
└─────────────────────────────────────────────────────────┘
                         ✅ КОНСИСТЕНТНО С
┌─── 6 call sites → invoke_module_interface() ────────────┐
│  node-lifecycle.sh:842  → healthcheck liveness           │
│  deploy-modules.sh:333  → install                        │
│  deploy-modules.sh:538  → healthcheck readiness           │
│  deploy-modules.sh:571  → healthcheck liveness            │
│  deploy-project.sh:729  → deploy-hook                     │
│  deploy-project.sh:757  → remove-hook                     │
└─────────────────────────────────────────────────────────┘
                         ✅ ВАЛИДИРУЕТСЯ
┌─── Gate #8 v2 ─────────────────────────────────────────┐
│  Фаза 1: grep bash/source modules/ в internal/ → RED    │
│  Фаза 2: grep invoke_module_interface → сверка           │
│          с module.yaml.interfaces → RED если mismatch     │
└─────────────────────────────────────────────────────────┘
```

### Call Site → Interface Mapping

| # | Файл:строка | Переменная (было) | Интерфейс | Аргументы |
|---|-------------|-------------------|-----------|-----------|
| 1 | `node-lifecycle.sh:842-846` | `$hc_script` | `healthcheck` | `liveness` |
| 2 | `deploy-modules.sh:333-341` | `$install_script` | `install` | — |
| 3 | `deploy-modules.sh:538` | `$healthcheck_script` | `healthcheck` | `readiness` |
| 4 | `deploy-modules.sh:571` | `$healthcheck_script` | `healthcheck` | `liveness` |
| 5 | `deploy-project.sh:729-734` | `$hook_script` | `deploy-hook` | `$PROJECT_DIR $PROJECT $NODE_NAME` |
| 6 | `deploy-project.sh:757-763` | `$hook_script` | `remove-hook` | `$PROJECT_DIR $PROJECT $NODE_NAME` |

### Module Interface Assignments

| Модуль | install_type | interfaces | Примечание |
|--------|-------------|------------|------------|
| postgres | docker | `[healthcheck, deploy-hook]` | +deploy-hook: имеет hooks.on_project_deploy |
| redis | docker | `[healthcheck]` | |
| nginx | docker | `[healthcheck, install, deploy-hook]` | install: gen-dev-certs; deploy-hook: nginx_reload_hook.sh |
| clickhouse | docker | `[healthcheck]` | |
| minio | docker | `[]` | internal/ не вызывает minio напрямую |
| logging | docker | `[healthcheck]` | |
| litellm | docker | `[healthcheck]` | |
| langfuse | docker | `[healthcheck]` | |
| backup-cron | docker | `[healthcheck]` | |
| monitoring | docker | `[healthcheck, deploy-hook]` | deploy-hook: hooks.on_project_deploy |
| infra-metrics | docker | `[healthcheck]` | |
| hermes-agent | docker | `[healthcheck]` | |
| platform-secrets | system | `[install]` | Только install; healthcheck.sh отсутствует (system-модуль) |

**Изменения против брифа:**
- `postgres`: + `deploy-hook` (ответ G3 — имеет hooks.on_project_deploy)
- `monitoring`: − `remove-hook` (ответ G4 — нет хука on_project_remove ни у одного модуля; интерфейс должен отражать реальность)
- `nginx`: + `deploy-hook` (ответ G2 — имеет hooks.on_project_deploy: nginx_reload_hook.sh)
- Количество модулей: 13, не 14 (G1 — опечатка в брифе)

---

## §2. Draft Code Graph

```
┌── core/lib/module-interface.sh (NEW) ──────────────────────────────┐
│  invoke_module_interface(module, interface, args...)                │
│  ├── validate: yaml_get_list module.yaml interfaces                │
│  ├── interface not found → return 0 (skip, not error)              │
│  ├── dispatch:                                                      │
│  │   ├── healthcheck  → bash module/healthcheck.sh args            │
│  │   ├── install      → bash module/install.sh                     │
│  │   ├── deploy-hook  → read hooks.on_project_deploy from yaml     │
│  │   │                  → bash module/<hook_path> args             │
│  │   └── remove-hook  → read hooks.on_project_remove from yaml     │
│  │                      → bash module/<hook_path> args             │
│  └── script failed → return 1                                      │
│  └── module.yaml not found/invalid → return 2                      │
└────────────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │ source                       │ calls
         ▼                              │
┌── core/lib/paths.sh ─────┐   ┌───────┴──────────────────────────┐
│  source module-interface │   │  core/internal/                   │
│  (после PATHS_MODULES_DIR)│   │  ├── bootstrap/                  │
└──────────────────────────┘   │  │   ├── node-lifecycle.sh:842    │
                               │  │   └── deploy-modules.sh:333,   │
                               │  │       538, 571                 │
                               │  └── deploy/                      │
                               │      └── deploy-project.sh:729,   │
                               │          757                      │
                               └───────────────────────────────────┘

┌── core/modules/<name>/module.yaml ────────────────────────────────┐
│  interfaces: [healthcheck, ...]   ← D4 расширение                │
│  hooks:                                                            │
│    on_project_deploy: hooks/on-project-deploy.sh                  │
│    on_project_remove: hooks/on-project-remove.sh  (опционально)   │
└────────────────────────────────────────────────────────────────────┘

┌── tests/test_cross_layer_imports.py (v2) ──────────────────────────┐
│  _IMPORT_RULES["internal"] = {"internal", "lib", "modules"}        │
│  + detect_invoke_module_interface_calls()                          │
│  + validate_interfaces_registration()                              │
│  + detect_direct_module_calls()   ← bash/source modules/ в internal│
└────────────────────────────────────────────────────────────────────┘
```

---

## §3. Data Flow

### Flow 1: Healthcheck (call sites 1, 3, 4)

```
bootstrap/node-lifecycle.sh
  │ while read mod_name mod_enabled; do
  │   invoke_module_interface "$mod_name" healthcheck liveness
  │     → yaml_get_list module.yaml interfaces
  │       → "healthcheck" in list? YES
  │         → bash module/healthcheck.sh liveness
  │           → exit 0 (healthy) или exit 1 (unhealthy)
  │       → "healthcheck" NOT in list? (platform-secrets)
  │         → return 0 (graceful skip — system module)
  ▼

deploy-modules.sh:wait_for_readiness()
  │ invoke_module_interface "$module_name" healthcheck readiness
  │   → (same dispatch, args=readiness)
  ▼

deploy-modules.sh:run_healthcheck()
  │ invoke_module_interface "$module_name" healthcheck liveness
  │   → (same dispatch, args=liveness)
  ▼
```

### Flow 2: System Module Install (call site 2)

```
deploy-modules.sh:deploy_system_module()
  │ _check_env_requires "$module_name"     ← gate T3
  │ invoke_module_interface "$module_name" install
  │   → yaml_get_list module.yaml interfaces
  │     → "install" in list? YES
  │       → bash module/install.sh
  │         → platform-secrets: cp service → systemctl daemon-reload + enable + restart
  │         → exit code passthrough
  │   → script failed? return 1
  ▼
```

### Flow 3: Deploy/Remove Hooks (call sites 5, 6)

```
deploy-project.sh:_trigger_deploy_hooks()
  │ for module_yaml in modules/*/module.yaml; do
  │   hook=$(yaml_get_field "$module_yaml" "hooks.on_project_deploy") || continue
  │   module_name=$(basename $(dirname "$module_yaml"))
  │   invoke_module_interface "$module_name" deploy-hook "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"
  │     → yaml_get_list module.yaml interfaces
  │       → "deploy-hook" in list? YES
  │         → hook_path=$(yaml_get_field module.yaml "hooks.on_project_deploy")
  │         → bash module/$hook_path "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"
  │         → если hook_path пустой или скрипт не существует → return 0 (skip)
  │       → "deploy-hook" NOT in list?
  │         → return 0 (graceful skip)
  ▼

deploy-project.sh:_trigger_remove_hooks()
  │ (аналогично, интерфейс "remove-hook", поле hooks.on_project_remove)
  ▼
```

---

## §4. $TASKS

### Wave 1: Foundation — Typed Contract + Module Interfaces

#### T1: D4 Schema Extension — `interfaces` field
**File:** `core/modules/AGENTS.md` (секция «module.yaml — D4 контракт»)
**Change:** Добавить поле `interfaces` в D4 схему:
```yaml
interfaces:                      # массив строк — интерфейсы, доступные для invoke_module_interface
  - healthcheck                  # из internal/bootstrap (healthcheck liveness/readiness)
  - install                      # из internal/bootstrap (system-модули)
  - deploy-hook                  # из internal/deploy (on_project_deploy)
  - remove-hook                  # из internal/deploy (on_project_remove)
```
**Edge cases:**
- Пустой `interfaces: []` валиден (minio)
- Отсутствующее поле `interfaces` = `[]` (backward compatibility)
- Поле не в YAML schema validation scope (см. Non-scope брифа)

#### T2: Create `core/lib/module-interface.sh`
**File:** `core/lib/module-interface.sh` (NEW, ~90 строк)
**Functions:**
- `invoke_module_interface(module, interface, args...)` — основная dispatch-функция
- `_invoke_validate_interface(module_yaml, interface)` — проверка наличия интерфейса через `yaml_get_list`
- `_invoke_dispatch_healthcheck(module_dir, args...)` — `bash healthcheck.sh args`
- `_invoke_dispatch_install(module_dir)` — `bash install.sh`
- `_invoke_dispatch_hook(module_yaml, hook_field, args...)` — читает путь хука из module.yaml, затем вызывает

**Exit codes:**
- `0` — интерфейс не зарегистрирован (graceful skip) ИЛИ выполнение успешно
- `1` — интерфейс зарегистрирован, но скрипт завершился с ошибкой
- `2` — module.yaml не найден или невалиден

**Edge cases:**
- Повторный вызов для install: idempotency — ответственность call site (`.done`-маркеры), не lib
- Конкурентный вызов: lib не блокирует — ответственность call site
- Пустой `interfaces: []` или отсутствующее поле: возврат 0 (skip)
- Несуществующий модуль (опечатка): `module.yaml` не найден → return 2
- `yaml_get_list` fallback: если python3/yaml недоступен → return 2 с IMP:9 логом
- Невалидный YAML в module.yaml → return 2
- Hook не существует для deploy-hook/remove-hook даже если интерфейс зарегистрирован → return 0 (skip, не ошибка)

#### T3: Source `module-interface.sh` in `paths.sh`
**File:** `core/lib/paths.sh`
**Change:** Добавить `source "${PATHS_LIB_DIR}/module-interface.sh"` после определения `PATHS_MODULES_DIR`.
**Edge cases:**
- Циклический source: `module-interface.sh` НЕ должен source'ить `paths.sh` (использует `PATHS_MODULES_DIR` из окружения)
- `paths.sh` уже заsource'ен во всех call sites через entrypoints → гарантированно доступен

#### T4–T16: Add `interfaces` to 13 `module.yaml` files
**Files:** `core/modules/<name>/module.yaml` ×13
**Change:** Добавить поле `interfaces` согласно таблице в §1.

**Per-module assignments (T4–T16, все параллельны):**

| Task | Модуль | interfaces | 
|------|--------|------------|
| T4 | postgres | `[healthcheck, deploy-hook]` |
| T5 | redis | `[healthcheck]` |
| T6 | nginx | `[healthcheck, install, deploy-hook]` |
| T7 | clickhouse | `[healthcheck]` |
| T8 | minio | `[]` |
| T9 | logging | `[healthcheck]` |
| T10 | litellm | `[healthcheck]` |
| T11 | langfuse | `[healthcheck]` |
| T12 | backup-cron | `[healthcheck]` |
| T13 | monitoring | `[healthcheck, deploy-hook]` |
| T14 | infra-metrics | `[healthcheck]` |
| T15 | hermes-agent | `[healthcheck]` |
| T16 | platform-secrets | `[install]` |

**Edge cases (общие для T4–T16):**
- Поле `interfaces` должно быть добавлено до `depends_on` или после `description` (консистентное расположение)
- YAML-валидность: каждый module.yaml должен оставаться валидным YAML после добавления
- Пустой массив `[]` vs отсутствующее поле: `[]` — явная декларация «нет интерфейсов», отсутствие — неявная (backward compat)

### Wave 2: Gate #8 v2 + Call Sites Refactoring

#### T17: Gate #8 v2 — Core Logic
**File:** `tests/test_cross_layer_imports.py`
**Changes:**
1. `_IMPORT_RULES["internal"]` = `{"internal", "lib", "modules"}` — modules разрешён через typed contract
2. Новая функция `_detect_invoke_calls(source_file)` → list[dict] — находит все `invoke_module_interface X Y` вызовы, возвращает [{module, interface, lineno}]
3. Новая функция `_validate_interfaces(violations, invoke_calls)` → дополняет violations — для каждого invoke-вызова проверяет `module.yaml` соответствующего модуля на наличие interface в `interfaces`
4. Новая функция `_detect_direct_module_calls(source_file)` → list[tuple] — находит `bash`, `source`, `. ` с путём, содержащим `modules/`, в файлах из `internal/`
5. Интеграция в `lint_core()`:
   - Фаза 1 (direct calls): для каждого .sh файла в internal/ → detect_direct_module_calls → violations
   - Фаза 2 (invoke validation): для каждого .sh файла → detect_invoke_calls → validate_interfaces → violations

**Edge cases:**
- `invoke_module_interface` с переменной в качестве module/interface имени: `invoke_module_interface "$mod" "$iface"` → gate не может статически проверить → WARN (не violation)
- `invoke_module_interface` в закомментированной строке: пропускать (как текущий scanner)
- `invoke_module_interface` в here-document: маловероятно, но scanner должен корректно обрабатывать
- Модуль с `interfaces: []` и invoke-вызов к нему → violation
- Модуль с отсутствующим полем `interfaces` → трактовать как `[]`
- `module.yaml` не существует для имени модуля → violation
- Gate должен быть зелёным при запуске ДО рефакторинга call sites (T18–T23): старые `bash "$variable"` вызовы в internal/ будут прямыми violations, но это ОЖИДАЕМО — gate становится красным и зеленеет по мере рефакторинга

#### T18: Gate Test — Expected Behaviour
**File:** `tests/gates/test_gate_cross_layer.py`
**Changes:**
1. Обновить docstring: Gate #8 теперь проверяет typed contract, а не полный запрет internal→modules
2. Добавить TRAP[TEST] с датой и описанием миграции
3. Импортировать новые функции из `test_cross_layer_imports` если необходимо

#### T19: Manifest Registration Update
**File:** `core/entrypoint-manifest.yaml`
**Change:** Обновить описание `cross-layer` gate entry:
```yaml
  - id: cross-layer
    description: Cross-layer typed contract enforcement — internal→modules only via invoke_module_interface with registered interfaces
    test_file: test_gate_cross_layer.py
```

#### T20: Call Site 1 — node-lifecycle.sh healthcheck
**File:** `core/internal/bootstrap/node-lifecycle.sh:842-846`
**Current:**
```bash
local hc_script="${CORE_DIR}/modules/${mod_name}/healthcheck.sh"
if [[ -f "$hc_script" ]]; then
    local attempt=0 hc_passed=0
    while [[ $attempt -lt $hc_max_retries ]]; do
        if bash "$hc_script" liveness &>/dev/null 2>&1; then
```
**Target:**
```bash
local attempt=0 hc_passed=0
while [[ $attempt -lt $hc_max_retries ]]; do
    local hc_rc=0
    invoke_module_interface "$mod_name" healthcheck liveness &>/dev/null 2>&1 || hc_rc=$?
    if [[ $hc_rc -eq 0 ]]; then
```
**Edge cases:**
- `hc_rc=0` может означать skip (интерфейс не зарегистрирован) ИЛИ success (healthcheck passed)
- Для system-модуля platform-secrets: healthcheck не зарегистрирован → rc=0 → трактуется как success (skip)
- Возврат 1 (ошибка) → retry как раньше
- Возврат 2 (невалидный module.yaml) → трактовать как ошибку, retry

#### T21: Call Sites 2, 3, 4 — deploy-modules.sh
**File:** `core/internal/bootstrap/deploy-modules.sh`

**Site 2 (line 333-341, deploy_system_module):**
```bash
# Было:
if ! bash "$install_script"; then
# Стало:
if ! invoke_module_interface "$module_name" install; then
```

**Site 3 (line 538, wait_for_readiness):**
```bash
# Было:
if bash "$healthcheck_script" readiness 2>/dev/null; then
# Стало:
if invoke_module_interface "$module_name" healthcheck readiness 2>/dev/null; then
```

**Site 4 (line 571, run_healthcheck):**
```bash
# Было:
hc_output="$(bash "$healthcheck_script" liveness 2>&1)" && {
# Стало:
hc_output="$(invoke_module_interface "$module_name" healthcheck liveness 2>&1)" && {
```

**Edge cases:**
- `invoke_module_interface ... install` для docker-модуля (у которого нет install в interfaces) → return 0 (skip) → `if ! invoke...` = `if ! true` = false → OK (не ошибка)
- `invoke_module_interface ... healthcheck` для модуля без healthcheck (platform-secrets) → return 0 → wait_for_readiness/run_healthcheck видят success → OK (skip)
- Сохранение retry-логики вокруг invoke вызовов: retry должен работать при rc=1 (script failed), но пропускать при rc=0 (skip/success)

#### T22: Call Sites 5, 6 — deploy-project.sh hooks
**File:** `core/internal/deploy/deploy-project.sh`

**Site 5 (line 729-734, _trigger_deploy_hooks):**
```bash
# Было:
local hook_script
hook_script="$(dirname "$module_yaml")/$hook"
if [[ -x "$hook_script" ]]; then
    local module_name
    module_name="$(basename "$(dirname "$module_yaml")")"
    if bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
```
**Target:**
```bash
local module_name
module_name="$(basename "$(dirname "$module_yaml")")"
if invoke_module_interface "$module_name" deploy-hook "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
```

**Site 6 (line 757-763, _trigger_remove_hooks):**
```bash
# Было:
local hook_script
hook_script="$(dirname "$module_yaml")/$hook"
if [[ -x "$hook_script" ]]; then
    local module_name
    module_name="$(basename "$(dirname "$module_yaml")")"
    if bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
```
**Target:**
```bash
local module_name
module_name="$(basename "$(dirname "$module_yaml")")"
if invoke_module_interface "$module_name" remove-hook "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
```

**Edge cases:**
- `-x` проверка удалена — `invoke_module_interface` делает свою проверку существования скрипта
- Хук не зарегистрирован в interfaces → return 0 → `if invoke...` = success → OK (skip)
- Хук зарегистрирован, но скрипт не существует → return 0 (skip) — см. T2 edge cases
- Нестандартный путь хука (nginx: `nginx_reload_hook.sh`) → dispatch читает `hooks.on_project_deploy` из module.yaml, не hardcode'ит `hooks/on-project-deploy.sh`

#### T23: Verify — `bash "$variable"` cleanup
**Action:** `rg 'bash "\$(hc_script|install_script|healthcheck_script|hook_script)"' core/internal/` → должен вернуть 0 результатов.
**Edge cases:**
- Переменные могут остаться в коде если они используются для других целей (логирование) — OK, главное чтобы не в `bash "$var"`
- Возможны другие переменные с модульными путями → не в скоупе, Gate #8 v2 поймает

### Wave 3: Documentation

#### T24: `core/AGENTS.md` Cross-Layer Rule Update
**File:** `core/AGENTS.md` (секция «Cross-layer import rules»)
**Current:**
```
| internal/ | internal/, lib/ | Всё остальное |
```
**Target:**
```
| internal/ | internal/, lib/, modules/ (через invoke_module_interface + interfaces) | Прямые вызовы modules/ без регистрации |
```
**Edge cases:**
- Формулировка должна быть консистентна с `core/entrypoints/healthcheck.sh` (T25)
- Упомянуть `core/lib/module-interface.sh` как канонический механизм

#### T25: `core/entrypoints/healthcheck.sh` Contradiction Fix
**File:** `core/entrypoints/healthcheck.sh:11-13`
**Current:**
```
## @rationale Q: Why a thin wrapper?
##            A: Compliance with core/AGENTS.md cross-layer rule: entrypoints → modules is forbidden.
##            internal/ → modules is permitted. The --help and PLATFORM_ROOT computation stay here
```
**Target:**
```
## @rationale Q: Why a thin wrapper?
##            A: Compliance with core/AGENTS.md cross-layer rule: entrypoints → modules is forbidden.
##            internal/ → modules is permitted through typed contract (invoke_module_interface + module.yaml.interfaces).
##            The --help and PLATFORM_ROOT computation stay here
```

#### T26: `core/modules/AGENTS.md` D4 Schema Update
**File:** `core/modules/AGENTS.md` (секция «module.yaml — D4 контракт»)
**Change:** Задокументировать поле `interfaces` как описано в T1.
**Edge cases:** Должен быть консистентен с T1 (один источник правды — modules/AGENTS.md, T1 и T26 — один и тот же файл, разные секции документа).

---

## §5. $PARALLEL_GROUPS

```
Wave 1 (Foundation):
  [T1] D4 Schema Extension          ──┐
  [T2] module-interface.sh            │ sequential (T2 depends on T1 for contract)
  [T3] paths.sh source                │
                                      │
  [T4 ─ T16] 13 module.yaml files    │ ALL PARALLEL (independent files)
                                      │
Wave 2 (Gate + Call Sites):
  [T17] Gate #8 v2 logic             ──┐ sequential (T17-T18-T19 depend on each other)
  [T18] Gate test wrapper              │
  [T19] Manifest registration          │
                                      │
  [T20] node-lifecycle.sh:842         │ ALL PARALLEL (different files,
  [T21] deploy-modules.sh:333,538,571 │  no shared lines)
  [T22] deploy-project.sh:729,757     │
                                      │
  [T23] Verify cleanup                │ depends on T20-T22

Wave 3 (Documentation):
  [T24] core/AGENTS.md rule           ──┐ ALL PARALLEL (different files)
  [T25] healthcheck.sh fix              │
  [T26] modules/AGENTS.md D4 schema     │
```

**Итого волн реализации:** 3 (плюс 1 QA-волна = проверка)

**Критический путь:** T1 → T2 → T3 → T4-T16 (ждём все) → T17 → T18 → T19 → T20-T22 (ждём все) → T23 → T24,T25,T26

---

## §6. $TEST_SPEC

### Существующие тесты (должны остаться зелёными)

| Test | File | Что проверять |
|------|------|---------------|
| `test_cross_layer_imports` | `tests/test_cross_layer_imports.py` | После W2: Gate #8 v2 логика. До W2: ВРЕМЕННО КРАСНЫЙ (6 прямых вызовов станут violations) |
| `test_gate_cross_layer` | `tests/gates/test_gate_cross_layer.py` | После W2: обёртка gate. До W2: ВРЕМЕННО КРАСНЫЙ |
| `make gate MODE=fast` | Makefile | После W3: ЗЕЛЁНЫЙ |

### Новые тесты (создаются в W2)

#### TEST-G1: Direct Module Call Detection
**Test:** `test_direct_module_call_detected` в `test_cross_layer_imports.py`
**Input:** Файл в `core/internal/` с `bash "${CORE_DIR}/modules/postgres/healthcheck.sh"`
**Expected:** violation string с `[internal→modules]` и `direct call`
**Edge cases:**
- Переменная без `/`: `bash "$hc_script"` → был слеп, теперь фаза 1 (direct calls) НЕ ловит переменные, но фаза 2 (invoke validation) ловит отсутствие `invoke_module_interface`
- Закомментированный `bash modules/...`: не violation
- `bash modules/...` в `core/modules/` (не internal/): не violation
- `bash modules/...` в `core/entrypoints/`: violation (entrypoints → modules запрещён)

#### TEST-G2: invoke_module_interface with Registered Interface
**Test:** `test_invoke_registered_interface_passes`
**Input:** `invoke_module_interface postgres healthcheck liveness` + `postgres/module.yaml` содержит `interfaces: [healthcheck]`
**Expected:** 0 violations
**Edge cases:**
- Интерфейс зарегистрирован, но module.yaml не существует → violation
- Интерфейс зарегистрирован, скрипт не существует → runtime error (не gate violation)

#### TEST-G3: invoke_module_interface with Unregistered Interface
**Test:** `test_invoke_unregistered_interface_fails`
**Input:** `invoke_module_interface minio healthcheck liveness` + `minio/module.yaml` содержит `interfaces: []`
**Expected:** violation string с `interface not registered`
**Edge cases:**
- `interfaces:` отсутствует в module.yaml → трактовать как `[]` → violation
- `invoke_module_interface` с переменным именем модуля: `invoke_module_interface "$mod" healthcheck` → gate выдаёт WARN, не violation (статически неразрешимо)

#### TEST-G4: Negative — Original Bug Reproduction
**Test:** `test_gate8_original_blindness_fixed`
**Input:** (a) `bash "$hc_script"` в internal/ → violation (прямой вызов без invoke)
**Expected:** Gate #8 v2 ловит то, что старый gate пропускал
**Anti-survivorship:** R5 из testing.md — для каждого gate-теста с багом должен быть negative test

#### TEST-G5: All 6 Call Sites Validated
**Test:** `test_all_call_sites_use_invoke`
**Input:** Прогнать `lint_core()` на финальном состоянии после W2
**Expected:** 0 violations
**Edge cases:**
- Тест должен запускаться после рефакторинга всех call sites
- До рефакторинга — ожидаемо красный (6 violations)

### Тесты LDD-траектории

Все тесты должны выводить IMP:7-10 логи через `caplog` и содержать assert на IMP:9 присутствие.

---

## §7. Edge Cases (сводка)

| # | Case | Resolution | Task |
|---|------|------------|------|
| EC1 | Пустой `interfaces: []` | Валиден, invoke → return 0 (skip) | T2 |
| EC2 | `interfaces` отсутствует | Трактовать как `[]` (backward compat) | T2, T17 |
| EC3 | `module.yaml` не существует | return 2 (invalid), gate → violation | T2, T17 |
| EC4 | Невалидный YAML | return 2, gate → violation | T2 |
| EC5 | `invoke_module_interface` с переменным module | Gate → WARN (не violation) | T17 |
| EC6 | Конкурентный вызов install | Ответственность call site (не lib) | T2 |
| EC7 | Повторный вызов install | `.done`-маркеры на call site | T2 |
| EC8 | Хук зарегистрирован, скрипта нет | return 0 (skip, не ошибка) | T2 |
| EC9 | System-модуль без healthcheck.sh | return 0 (graceful skip) | T2, T20, T21 |
| EC10 | Переходный период (часть sites refactored) | Gate временно красный — ожидаемо | T17 |
| EC11 | `yaml_get_list` недоступен (нет python3) | return 2 (invalid) | T2 |

---

## §8. File Manifest

| Файл | Действие | Волна | Таск |
|------|---------|-------|------|
| `core/modules/AGENTS.md` | MODIFY: D4 схема + interfaces | W1 | T1 |
| `core/lib/module-interface.sh` | **CREATE** (~90 строк) | W1 | T2 |
| `core/lib/paths.sh` | MODIFY: +source module-interface | W1 | T3 |
| `core/modules/postgres/module.yaml` | MODIFY: +interfaces | W1 | T4 |
| `core/modules/redis/module.yaml` | MODIFY: +interfaces | W1 | T5 |
| `core/modules/nginx/module.yaml` | MODIFY: +interfaces | W1 | T6 |
| `core/modules/clickhouse/module.yaml` | MODIFY: +interfaces | W1 | T7 |
| `core/modules/minio/module.yaml` | MODIFY: +interfaces | W1 | T8 |
| `core/modules/logging/module.yaml` | MODIFY: +interfaces | W1 | T9 |
| `core/modules/litellm/module.yaml` | MODIFY: +interfaces | W1 | T10 |
| `core/modules/langfuse/module.yaml` | MODIFY: +interfaces | W1 | T11 |
| `core/modules/backup-cron/module.yaml` | MODIFY: +interfaces | W1 | T12 |
| `core/modules/monitoring/module.yaml` | MODIFY: +interfaces | W1 | T13 |
| `core/modules/infra-metrics/module.yaml` | MODIFY: +interfaces | W1 | T14 |
| `core/modules/hermes-agent/module.yaml` | MODIFY: +interfaces | W1 | T15 |
| `core/modules/platform-secrets/module.yaml` | MODIFY: +interfaces | W1 | T16 |
| `tests/test_cross_layer_imports.py` | MODIFY: Gate #8 v2 logic | W2 | T17 |
| `tests/gates/test_gate_cross_layer.py` | MODIFY: обновление expected behaviour | W2 | T18 |
| `core/entrypoint-manifest.yaml` | MODIFY: gate описание | W2 | T19 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY: call site 1 | W2 | T20 |
| `core/internal/bootstrap/deploy-modules.sh` | MODIFY: call sites 2-4 | W2 | T21 |
| `core/internal/deploy/deploy-project.sh` | MODIFY: call sites 5-6 | W2 | T22 |
| `core/AGENTS.md` | MODIFY: cross-layer rule | W3 | T24 |
| `core/entrypoints/healthcheck.sh` | MODIFY: контрадикция | W3 | T25 |
| `core/modules/AGENTS.md` | MODIFY: D4 документация | W3 | T26 |

**Всего:** 1 CREATE, 24 MODIFY

---

## §9. TRAP[DECISION] — Rejected Hypotheses

### ⚠️ TRAP[DECISION] · 2026-07-18 · HI · H3 (Callback Registry) отвергнут
- **Reason:** Централизованный `core/registry.sh` создаёт single point of contention при параллельной разработке модулей. Раздваивает source of truth (module.yaml + registry.sh), что противоречит принципу «один файл на модуль — один source of truth».
- **Rejected by:** Опрос пользователя — выбрана H1 (Typed Contract)
- **Rev:** Если количество модулей превысит 30 и churn интерфейсов станет узким местом — пересмотреть в пользу централизованного registry.

### ⚠️ TRAP[DECISION] · 2026-07-18 · MED · H2 (Layer Relabeling) отвергнут
- **Reason:** Нарушает семантику entrypoints/ как thin wrappers. Не решает проблему обнаружения нелегитимных вызовов — Gate #8 остаётся слепым к `bash "$variable"`.
- **Rejected by:** Архитектор (суперпозиция) — score 5/10, слабее H1 по enforceability
- **Rev:** Если количество cross-layer вызовов превысит 20 и typed contract станет bottleneck'ом — пересмотреть в пользу перемещения оркестраторов в entrypoints/ с соответствующим изменением cross-layer правил.

### ⚠️ TRAP[DECISION] · 2026-07-18 · LOW · H4 (yaml_read at Call Sites) отвергнут
- **Reason:** Дублирует валидацию в 4 скриптах (6 call sites). Дрифт валидации неизбежен — принцип Small Simple Blocks не оправдывает дублирование бизнес-логики.
- **Rejected by:** Архитектор (суперпозиция) — score 4/10
- **Rev:** Если dispatch-логика в lib станет слишком сложной (>200 строк) — рассмотреть распределённую валидацию.

### ⚠️ TRAP[DECISION] · 2026-07-18 · LOW · H5 (Internal Module Proxy) отвергнут
- **Reason:** 13+ proxy-файлов — boilerplate без бизнес-ценности. Каждый новый интерфейс модуля требует изменения proxy.sh. Нарушает принцип «минимизация точек изменения».
- **Rejected by:** Архитектор (суперпозиция) — score 3/10
- **Rev:** Если потребуется агрессивная изоляция модулей (разные команды, разные security domains) — proxy-паттерн может быть оправдан.

---

## §10. Risk Register

| ID | Risk | Impact | Mitigation | Residual |
|----|------|--------|------------|----------|
| R1 | Gate #8 v2 ложно-положительный на легитимные вызовы | HIGH — блокирует CI | T17: фаза 1 (direct calls) использует строгий паттерн `bash.*modules/`, не `bash.*\$` | LOW |
| R2 | `invoke_module_interface` regression: падает при недоступном python3/yaml | HIGH — bootstrap fail | T2: явная проверка доступности yaml_get_list с fallback на return 2 | MEDIUM |
| R3 | nginx deploy-hook ломается: dispatch не находит `nginx_reload_hook.sh` | MEDIUM — хук нефатальный | T2: `_invoke_dispatch_hook` читает путь из module.yaml.hooks.on_project_deploy, не hardcode | LOW |
| R4 | Gate временно красный между W1 и W2 | LOW — ожидаемо | W1 добавляет module.yaml interfaces, но call sites ещё не рефакторены → gate показывает прямые вызовы | N/A (by design) |
| R5 | 13 module.yaml дрифтуют по формату `interfaces` | LOW — gate поймает | T17 фаза 2 валидирует консистентность | LOW |
| R6 | `paths.sh` циклический source через module-interface.sh | MEDIUM — shell error | T3: module-interface.sh НЕ source'ит paths.sh (использует переменные из окружения) | LOW |

---

## §11. Verification Protocol

После завершения всех волн:

```bash
# 1. Статическая проверка
rg 'bash "\$(hc_script|install_script|healthcheck_script|hook_script)"' core/internal/
# → 0 результатов

# 2. Gate #8 v2
python -m pytest tests/test_cross_layer_imports.py -s -v
# → 0 violations, все invoke_module_interface вызовы зарегистрированы

# 3. CI gate
make gate MODE=fast
# → ЗЕЛЁНЫЙ

# 4. LDD trajectory
python -m pytest tests/test_cross_layer_imports.py tests/gates/test_gate_cross_layer.py -s -v
# → IMP:9 PASS log присутствует в выводе
```

---

## §12. Next Steps (Coder)

1. **Wave 1:** Реализовать T1–T16 (Foundation). После W1: gate ВРЕМЕННО КРАСНЫЙ (но module.yaml валидны, lib готова).
2. **Wave 2:** Реализовать T17–T23 (Gate + Call Sites). После W2: gate ЗЕЛЁНЫЙ (все 6 вызовов используют invoke + зарегистрированы).
3. **Wave 3:** Реализовать T24–T26 (Documentation).
4. **QA:** Запустить `make gate MODE=fast`, верифицировать все acceptance criteria из §ARTIFACT_CONTRACT.

$END_DEVPLAN
