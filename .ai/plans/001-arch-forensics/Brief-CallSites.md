<!-- GREP_SUMMARY: Brief, call-sites, typed-contract, cross-layer, module.yaml.interfaces, invoke_module_interface, gate-enforcement, invariant-collapse -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Current State (6 calls) → ◇ Root Cause → ◇ Solution (typed contract) → ◇ Implementation → ◇ Gate Design → ◇ Acceptance Criteria → ◇ Non-scope -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** БРИФ рефакторинга 6 runtime cross-layer вызовов — замена невидимого `bash "$variable"` на typed contract `invoke_module_interface` с регистрацией в `module.yaml.interfaces`.
- **DESCRIPTION:** Введение поля `interfaces` в D4-схему `module.yaml`, lib-функции `invoke_module_interface` как единственного легитимного способа вызова modules/ из internal/, обновление cross-layer правил в `core/AGENTS.md`, устранение контрадикции в `healthcheck.sh`, расширение Gate #8 для обнаружения нелегитимных вызовов.
- **RATIONALE:** Все 6 runtime-вызовов легитимны (оркестрация bootstrap/deploy), но архитектурная модель объявляет их запрещёнными. Gate #8 слеп к вызовам через переменные. Результат: INVARIANT COLLAPSE — модель недостоверна, gate даёт ложную гарантию. Typed contract делает границу внутренних слоёв enforceable без отказа от принципа изоляции.
- **ACCEPTANCE_CRITERIA:** 14 `module.yaml` содержат поле `interfaces`; 6 call sites используют `invoke_module_interface`; `core/AGENTS.md` cross-layer правило консистентно; `healthcheck.sh:12-13` контрадикция устранена; Gate #8 красный при вызове modules/ из internal/ без регистрации в `interfaces`; Gate #8 зелёный на текущем стеке (все 6 вызовов зарегистрированы).
- **IMPLEMENTS:** Superposition 2 вар. A (Typed Contract), skill `arch-patterns` (AI-First Architecture — typed public contracts)
- **IMPACTS:** `core/modules/<name>/module.yaml` ×14 (+ поле `interfaces`), `core/internal/bootstrap/node-lifecycle.sh:842-846`, `core/internal/bootstrap/deploy-modules.sh:333-341,538,571`, `core/internal/deploy/deploy-project.sh:729-734,757-763`, `core/entrypoints/healthcheck.sh:12-13`, `core/AGENTS.md` (cross-layer таблица), `core/modules/AGENTS.md` (D4 схема), `core/lib/module-interface.sh` (NEW), `tests/test_cross_layer_imports.py` (Gate #8 логика)
- **REQUIRES:** `Brief.md` того же плана (W2 Model Surgery), `core/modules/AGENTS.md` (D4 schema), `tests/test_cross_layer_imports.py` (текущий Gate #8)

$START_BRIEF

# Brief: Typed Contract for Cross-Layer Module Invocation

## Current State

### 6 runtime call sites

Все используют паттерн `bash "$variable"`, где переменная собирается из префикса (`CORE_DIR`/`PATHS_MODULES_DIR`) + имя модуля + суффикс скрипта:

| # | Файл:строка | Переменная | Вызываемый скрипт | Контекст |
|---|-------------|-----------|-------------------|----------|
| 1 | `node-lifecycle.sh:842-846` | `$hc_script` | `modules/<name>/healthcheck.sh` | Healthcheck после bootstrap |
| 2 | `deploy-modules.sh:333-341` | `$install_script` | `modules/<name>/install.sh` | Установка system-модуля |
| 3 | `deploy-modules.sh:538` | `$healthcheck_script` | `modules/<name>/healthcheck.sh` | Readiness poll при деплое |
| 4 | `deploy-modules.sh:571` | `$healthcheck_script` | `modules/<name>/healthcheck.sh` | Liveness check после деплоя |
| 5 | `deploy-project.sh:729-734` | `$hook_script` | `modules/<name>/hooks/<hook>` | Deploy hook |
| 6 | `deploy-project.sh:757-763` | `$hook_script` | `modules/<name>/hooks/<hook>` | Remove hook |

### Как строится путь

```bash
# site 1: node-lifecycle.sh:842
local hc_script="${CORE_DIR}/modules/${mod_name}/healthcheck.sh"
bash "$hc_script" liveness

# site 2: deploy-modules.sh:333
local install_script="${PATHS_MODULES_DIR}/${module_name}/install.sh"
bash "$install_script"

# sites 3-4: deploy-modules.sh:538,571
local healthcheck_script="${PATHS_MODULES_DIR}/${module_name}/healthcheck.sh"
bash "$healthcheck_script" readiness  # site 3
bash "$healthcheck_script" liveness   # site 4

# sites 5-6: deploy-project.sh:729,757
local hook_script="$(dirname "$module_yaml")/$hook"
bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"
```

### Что не так

1. **Модель отрицает реальность.** `core/AGENTS.md` cross-layer таблица: `internal/ → только internal/, lib/`. Но 6 легитимных вызовов нарушают это правило — оркестрация bootstrap/deploy невозможна без них.

2. **Документация противоречит себе.** `core/entrypoints/healthcheck.sh:12-13`: `"internal/ → modules is permitted"` — прямо противоположное утверждение.

3. **Gate #8 слеп.** `_looks_like_path()` требует `/` в строковом литерале. `bash "$hc_script"` — переменная без `/` → классифицируется как non-path → игнорируется. Gate репортит "0 violations".

4. **Модель недостоверна.** INVARIANT COLLAPSE — два ключевых источника (AGENTS.md и код) дают противоположные ответы на вопрос «может ли internal/ вызывать modules/?».

## Root Cause

**Фиктивный запрет.** Архитектурная модель декларирует границу (`internal ↛ modules`), которая в принципе не может соблюдаться при текущей архитектуре оркестрации. Вместо того чтобы признать это и создать enforceable контракт, система поддерживает иллюзию границы через gate, который заведомо не может её проверить.

## Solution: Typed Contract

### Принцип

`internal/` МОЖЕТ вызывать `modules/`, но **только через зарегистрированные интерфейсы**, явно объявленные модулем в `module.yaml`. Это не отказ от изоляции — это замена неработающего запрета на enforceable contract.

### module.yaml.interfaces (D4 расширение)

```yaml
# core/modules/postgres/module.yaml
name: postgres
install_type: docker
severity: critical
interfaces:
  - healthcheck    # вызывается из node-lifecycle.sh и deploy-modules.sh
  - install        # вызывается из deploy-modules.sh (если system-модуль)
```

```yaml
# core/modules/monitoring/module.yaml
name: monitoring
install_type: docker
interfaces:
  - healthcheck
  - deploy-hook    # вызывается из deploy-project.sh (on-project-deploy)
  - remove-hook    # вызывается из deploy-project.sh (on-project-remove)
```

```yaml
# core/modules/minio/module.yaml
name: minio
install_type: docker
interfaces: []     # internal/ не вызывает этот модуль
```

### invoke_module_interface — lib-обёртка

```bash
# core/lib/module-interface.sh (NEW)
# Использование:
#   invoke_module_interface <module_name> <interface> [args...]
# Пример:
#   invoke_module_interface postgres healthcheck liveness
#   invoke_module_interface monitoring deploy-hook /opt/projects/foo foo tronyx-vps

invoke_module_interface() {
    local module="$1" interface="$2"
    shift 2

    local module_dir="${PATHS_MODULES_DIR}/${module}"
    local module_yaml="${module_dir}/module.yaml"

    # Валидация: интерфейс зарегистрирован в module.yaml
    if ! grep -q "  - ${interface}" "$module_yaml" 2>/dev/null; then
        log_imp 9 "invoke_module_interface" \
            "INTERFACE VIOLATION: ${module} does not expose interface '${interface}'"
        return 1
    fi

    # Диспетчеризация
    case "$interface" in
        healthcheck)
            bash "${module_dir}/healthcheck.sh" "$@"
            ;;
        install)
            bash "${module_dir}/install.sh" "$@"
            ;;
        deploy-hook|remove-hook)
            local hook="on-project-${interface%-hook}.sh"
            bash "${module_dir}/hooks/${hook}" "$@"
            ;;
        *)
            log_imp 9 "invoke_module_interface" "Unknown interface: ${interface}"
            return 1
            ;;
    esac
}
```

### Рефакторинг call sites

```bash
# Было (site 1, node-lifecycle.sh:842-846):
local hc_script="${CORE_DIR}/modules/${mod_name}/healthcheck.sh"
if [[ -f "$hc_script" ]]; then
    bash "$hc_script" liveness
fi

# Стало:
invoke_module_interface "$mod_name" healthcheck liveness
```

```bash
# Было (site 2, deploy-modules.sh:333-341):
local install_script="${PATHS_MODULES_DIR}/${module_name}/install.sh"
if ! bash "$install_script"; then

# Стало:
if ! invoke_module_interface "$module_name" install; then
```

```bash
# Было (sites 5-6, deploy-project.sh:729-734):
local hook_script="$(dirname "$module_yaml")/$hook"
bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"

# Стало:
invoke_module_interface "$module_name" deploy-hook "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"
```

### Обновление документации

**`core/AGENTS.md` cross-layer таблица:**
```
| Слой         | Может импортировать                  | Запрещено              |
|-------------|--------------------------------------|------------------------|
| internal/   | internal/, lib/, modules/ (через     | Прямые вызовы modules/ |
|             | invoke_module_interface + interfaces)| без регистрации        |
```

**`core/entrypoints/healthcheck.sh:12-13`:**
```
# Было: "internal/ → modules is permitted"
# Стало: "internal/ → modules через typed contract (module.yaml.interfaces)"
```

## Gate Design

### Принцип

Gate #8 (`test_cross_layer_imports`) больше не запрещает `internal → modules`. Вместо этого:
- Разрешает: `invoke_module_interface <module> <interface>` — grep'абельный паттерн
- Запрещает: `bash.*modules/`, `source.*modules/`, `\. .*modules/` из `internal/`
- Проверяет: для каждого `invoke_module_interface` вызова — `module.yaml` соответствующего модуля содержит `interfaces: [..., <interface>, ...]`

### Логика Gate #8 v2

```python
# tests/test_cross_layer_imports.py

# Новое правило для internal/
_IMPORT_RULES["internal"] = {"internal", "lib", "modules"}  # modules разрешён через typed contract

# Фаза 1: найти все прямые вызовы modules/ из internal/ (bash/source/.)
# Такие вызовы → violation (должны использовать invoke_module_interface)

# Фаза 2: найти все invoke_module_interface вызовы
# Для каждого — проверить module.yaml.interfaces
# Если интерфейс не зарегистрирован → violation
```

### Что ловит новый gate

| Паттерн | Старый gate | Новый gate |
|---------|------------|------------|
| `bash "$hc_script"` (переменная) | ❌ Слеп | ✅ Ловит: прямой вызов modules/ без invoke |
| `invoke_module_interface postgres healthcheck` | ❌ Не существовал | ✅ Валидирует interfaces |
| `bash modules/postgres/healthcheck.sh` | ✅ Ловит | ✅ Ловит |
| `invoke_module_interface minio healthcheck` | ❌ | ✅ Ловит: minio interfaces=[] |

## Implementation Steps

### Фаза 1: Расширение D4 схемы

- `core/modules/AGENTS.md` — добавить поле `interfaces` в D4 схему
- `core/modules/<name>/module.yaml` ×14 — добавить поле `interfaces`:
  - `postgres`: `[healthcheck]`
  - `redis`: `[healthcheck]`
  - `nginx`: `[healthcheck, install]`
  - `clickhouse`: `[healthcheck]`
  - `minio`: `[]`
  - `logging`: `[healthcheck]`
  - `litellm`: `[healthcheck]`
  - `langfuse`: `[healthcheck]`
  - `backup-cron`: `[healthcheck]`
  - `monitoring`: `[healthcheck, deploy-hook, remove-hook]`
  - `infra-metrics`: `[healthcheck]`
  - `hermes-agent`: `[healthcheck]`
  - `platform-secrets`: `[install]`

### Фаза 2: lib/module-interface.sh

- Создать `core/lib/module-interface.sh` (~60 строк)
- Реализовать `invoke_module_interface()`
- Source в `paths.sh` или отдельных скриптах

### Фаза 3: Рефакторинг call sites

- `node-lifecycle.sh:842-846` → `invoke_module_interface "$mod_name" healthcheck liveness`
- `deploy-modules.sh:333-341` → `invoke_module_interface "$module_name" install`
- `deploy-modules.sh:538,571` → `invoke_module_interface "$module_name" healthcheck readiness/liveness`
- `deploy-project.sh:729-734` → `invoke_module_interface "$module_name" deploy-hook ...`
- `deploy-project.sh:757-763` → `invoke_module_interface "$module_name" remove-hook ...`

### Фаза 4: Обновление документации

- `core/AGENTS.md` cross-layer таблица
- `core/entrypoints/healthcheck.sh:12-13` контрадикция
- `core/modules/AGENTS.md` D4 схема

### Фаза 5: Gate #8 v2

- `tests/test_cross_layer_imports.py` — обновить `_IMPORT_RULES`, добавить проверку interfaces
- `tests/gates/test_gate_cross_layer.py` — обновить expected behaviour

## Acceptance Criteria

1. Все 14 `module.yaml` содержат поле `interfaces` (массив строк, может быть пустым)
2. `core/lib/module-interface.sh` существует, содержит `invoke_module_interface()`
3. 6 call sites используют `invoke_module_interface` вместо `bash "$variable"`
4. `rg 'bash "\$(hc_script|install_script|healthcheck_script|hook_script)"' core/internal/` → 0 результатов
5. `core/AGENTS.md` cross-layer правило консистентно (internal → modules через typed contract OK)
6. `core/entrypoints/healthcheck.sh:12-13` не противоречит `core/AGENTS.md`
7. Gate #8 красный если:
   - `bash modules/<name>/...` найден в internal/
   - `invoke_module_interface <name> <iface>` но `<iface>` не в `module.yaml.interfaces`
8. Gate #8 зелёный на текущем стеке (все 6 вызовов используют invoke + зарегистрированы)

## Non-scope

- **Рефакторинг `notify-hook.sh`** (deploy-project.sh:167-173) — это `internal→internal` вызов, не cross-layer
- **Добавление новых interfaces** сверх 4 базовых (healthcheck, install, deploy-hook, remove-hook) — при необходимости, но не в этом БРИФе
- **Валидация содержимого interfaces в CI** (схема YAML) — D4 схема обновляется, но отдельный schema-гейт на `interfaces` — follow-up
- **Удаление `_looks_like_path`** — функция остаётся для других слоёв (entrypoints, modules)

## Dependencies

| Зависимость | Статус |
|-------------|--------|
| `Brief.md` W2 (Model Surgery) | Этот БРИФ = детализация W2 |
| `Brief.md` W3 (Gate Hardening) | Этот БРИФ включает Gate #8 v2 |
| `Brief-DataFlow.md` | Ортогонален — DataFlow улучшает общую детекцию, Typed Contract даёт конкретный паттерн для поиска |

$END_BRIEF
