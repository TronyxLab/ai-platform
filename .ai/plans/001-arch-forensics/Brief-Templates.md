<!-- GREP_SUMMARY: Brief, templates, unification, parameterization, {{VAR}}-standard, auto-generate, gate-drift, sudo-whitelist, template-engine -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Current State → ◇ Root Cause → ◇ Solution Design → ◇ Implementation Steps → ◇ Acceptance Criteria → ◇ Non-scope → ◇ Dependencies -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** БРИФ унификации шаблонов ai-platform — единый механизм параметризации `{{VAR}}`, автоматическая регенерация `.conf` из `.template`, gate на дрейф.
- **DESCRIPTION:** Замена 4 разрозненных механизмов подстановки на один стандарт `{{DOUBLE_CURLY}}` с единым скриптом-рендерером. Введение `make templates-render` и gate `test_gate_template_drift` для предотвращения расхождения шаблонов и сгенерированных артефактов.
- **RATIONALE:** Текущие 4 механизма (sed `{{CURLY}}`, sed `__UNDERSCORE__`, sed `${SHELL_VAR}`, heredoc) фрагментируют knowledge. `sudo-whitelist.conf` ×6 содержат нераскрытый `{{MODULE_NAME}}` и `/opt/core/` (мёртвый код). `docker-compose.test.template` — неиспользуемый reference. Отсутствует gate на дрейф шаблонов — изменение `.template` без регенерации `.conf` молча проходит CI. `PLATFORM_ROOT` никогда не передаётся в шаблоны.
- **ACCEPTANCE_CRITERIA:** Единый синтаксис `{{VAR}}` для всех шаблонов; `make templates-render` регенерирует все `.conf`; gate `test_gate_template_drift` красный при расхождении; `sudo-whitelist.conf` не содержат нераскрытых плейсхолдеров; `/opt/core/` заменён на `{{PLATFORM_ROOT}}/core/`; `docker-compose.test.template` либо имеет consumer, либо удалён.
- **IMPLEMENTS:** Superposition 1 вар. A (Unify + Auto-generate), skill `arch-patterns` (AI-First Architecture)
- **IMPACTS:** `core/templates/sudo-whitelist.template`, `core/modules/*/sudo-whitelist.conf` ×6, `core/templates/docker-compose.test.template`, `core/internal/scaffold/add-project.sh` (replace_placeholders), `core/modules/nginx/install.sh` (${PLATFORM_DOMAIN} sed), `core/modules/monitoring/hooks/on-project-deploy.sh` (${PROJECT} sed), `core/internal/template-engine.sh` (NEW), `tests/gates/test_gate_template_drift.py` (NEW)
- **REQUIRES:** `Brief.md` того же плана (архитектурные коллапсы), `01-VerificationReport.md`, `02-VerificationReport.md`

$START_BRIEF

# Brief: Template Unification & Auto-Generation

## Current State

### 4 разрозненных механизма подстановки

| # | Механизм | Синтаксис | Где используется | Consumer |
|---|----------|-----------|------------------|----------|
| 1 | `sed "s/{{VAR}}/val/g"` | `{{MODULE_NAME}}` | `sudo-whitelist.template` | НЕТ автоматического consumer — `.conf` рендерятся вручную |
| 2 | `sed "s/__VAR__/val/g"` | `__PROJECT_NAME__`, `__DOMAIN__`, `__ORG_NAME__` | `add-project.sh` → `replace_placeholders()` | templates/template-{backend,frontend,fullstack}/ |
| 3 | `sed "s/\${VAR}/val/g"` | `${PLATFORM_DOMAIN}`, `${PROJECT}` | `nginx/install.sh`, `monitoring/hook` | nginx config, alert-rules |
| 4 | heredoc `cat > file <<EOF` | `${shell_var}` | `context-init.sh`, `add-vhost.sh`, `setup-node.sh` | node.yaml, nginx vhost, sudoers |

### Проблемы

1. **`sudo-whitelist.conf` ×6 — нераскрытые копии.** Все 6 файлов идентичны `sudo-whitelist.template` и до сих пор содержат `{{MODULE_NAME}}`. Работают только потому что `generate_module_sudoers()` (deploy-modules.sh:291) читает `_path` колонку но **игнорирует** её — реальный путь для sudoers берётся через `realpath(module_dir)` (строка 284). `/opt/core/` в этих файлах — мёртвый код.

2. **`docker-compose.test.template` не используется.** Ни один скрипт или CI workflow не применяет этот шаблон. Reference-only документация.

3. **Нет gate на дрейф.** Изменение `.template` без регенерации `.conf` молча проходит CI. Разработчик может поправить шаблон, забыть перегенерировать артефакт — и узнать об этом только при деплое на VPS.

4. **`PLATFORM_ROOT` не передаётся в шаблоны.** Если платформа переедет с `/opt/platform` на другой путь, шаблоны с хардкоженными `/opt/core/` останутся невалидными.

5. **Knowledge fragmentation.** Новый разработчик должен выучить 4 разных синтаксиса подстановки.

## Root Cause

**Отсутствует единый контракт «шаблон → артефакт».** Шаблоны живут в репозитории как документация, а не как machinery. Нет: единого синтаксиса, автоматической регенерации, gate на дрейф, стандартных переменных (`PLATFORM_ROOT`, `DOMAIN`).

## Solution: Unify + Auto-generate

### Единый синтаксис `{{VAR}}`

Все шаблоны переходят на `{{DOUBLE_CURLY}}`:
```
Было: __PROJECT_NAME__, ${PLATFORM_DOMAIN}, heredoc ${var}
Стало: {{PROJECT_NAME}}, {{PLATFORM_DOMAIN}}, {{VAR}}
```

### template-engine.sh — единый рендерер

```bash
# core/internal/template-engine.sh
# Usage: template-engine.sh <template> <output> [VAR=val ...]
# Example: template-engine.sh sudo-whitelist.template sudo-whitelist.conf MODULE_NAME=postgres PLATFORM_ROOT=/opt/platform

render_template() {
    local template="$1" output="$2"
    shift 2
    local sed_script=""
    for pair in "$@"; do
        local var="${pair%%=*}" val="${pair#*=}"
        sed_script="${sed_script}s|{{${var}}}|${val}|g;"
    done
    sed "$sed_script" "$template" > "$output"
}
```

### Стандартные переменные

`template-engine.sh` автоматически подставляет из окружения/ paths.sh:
- `{{PLATFORM_ROOT}}` — из `core/lib/paths.sh:33` (по умолчанию `/opt/platform`)
- `{{PLATFORM_DOMAIN}}` — из `.env` или platform-env.yaml
- Остальные — явно через CLI

### make templates-render

```makefile
templates-render:  ## Регенерировать все .conf из .template
    @core/internal/template-engine.sh --all
```

### Gate: test_gate_template_drift

```python
# tests/gates/test_gate_template_drift.py
@pytest.mark.gate
def test_template_drift():
    """Каждый .template при рендеринге должен давать идентичный .conf."""
    for template, conf, vars in _TEMPLATE_MANIFEST:
        rendered = render(template, **vars)
        current = Path(conf).read_text()
        assert rendered == current, f"DRIFT: {template} → {conf} differs"
```

`_TEMPLATE_MANIFEST` — декларативный список в `entrypoint-manifest.yaml` или в самом тесте.

## Implementation Steps

### Фаза 1: Унификация синтаксиса

| Файл | Действие |
|------|----------|
| `core/templates/sudo-whitelist.template` | Оставить `{{MODULE_NAME}}`, `/opt/core/` → `{{PLATFORM_ROOT}}/core/` |
| `core/modules/*/sudo-whitelist.conf` ×6 | Перегенерировать: `sed 's|{{MODULE_NAME}}|<name>|g; s|{{PLATFORM_ROOT}}|/opt/platform|g'` |
| `templates/template-*/` ×3 | `__PROJECT_NAME__` → `{{PROJECT_NAME}}` |
| `core/internal/scaffold/add-project.sh` | Заменить `__...__` sed на вызов `template-engine.sh` |
| `core/modules/nginx/install.sh` | `${PLATFORM_DOMAIN}` → `{{PLATFORM_DOMAIN}}`, заменить inline-sed на `template-engine.sh` |
| `core/modules/monitoring/hooks/on-project-deploy.sh` | `${PROJECT}` → `{{PROJECT}}`, аналогично |

### Фаза 2: template-engine.sh

- Создать `core/internal/template-engine.sh` (~80 строк)
- Поддержка `--all` (рендерит всё из манифеста), `--check` (dry-run, exit 1 при дрейфе)
- Интеграция с `paths.sh` для `{{PLATFORM_ROOT}}`

### Фаза 3: Gate

- Создать `tests/gates/test_gate_template_drift.py`
- Зарегистрировать в `entrypoint-manifest.yaml`
- Интегрировать в `make validate` (pre-commit) и `make gate MODE=fast`

### Фаза 4: Очистка

- `docker-compose.test.template` — добавить consumer ИЛИ удалить, задокументировав решение
- `core/modules/*/sudo-whitelist.conf` — рассмотреть вариант C из суперпозиции (Eliminate Templates) как follow-up: заменить 6 идентичных файлов на один `core/config/sudo-whitelist-roles.conf`

## Acceptance Criteria

1. `rg '__[A-Z_]+__' templates/` → 0 результатов (кроме комментариев о миграции)
2. `rg '\$\{PLATFORM_DOMAIN\}' core/modules/nginx/install.sh` → заменён на вызов template-engine.sh
3. `make templates-render` → регенерирует все `.conf`, no-op при отсутствии изменений
4. `make templates-render --check` → exit 0 если дрейфа нет, exit 1 с diff при дрейфе
5. Gate `test_gate_template_drift` зелёный на чистом репозитории
6. `rg '/opt/core/' core/templates/` → `{{PLATFORM_ROOT}}/core/`
7. `rg '{{MODULE_NAME}}' core/modules/*/sudo-whitelist.conf` → 0 (все раскрыты)

## Non-scope

- **Генерация `.dockerignore`** — это symlink, не шаблон
- **`module.mk` / `module-system.mk`** — используют Make-переменные `$(VAR)`, не шаблоны
- **Полная замена heredoc** в `context-init.sh`, `add-vhost.sh` — только если они генерируют >20 строк (не в этом плане)
- **Удаление `sudo-whitelist.conf`** в пользу централизованного `sudo-whitelist-roles.conf` — follow-up, не в этом БРИФе

## Dependencies

| Зависимость | Статус |
|-------------|--------|
| `core/lib/paths.sh` PLATFORM_ROOT | Используется |
| `Brief.md` W3 (path-consistency gate) | Независим — gate template-drift ортогонален gate path-consistency |
| `Brief.md` W4 (path remediation) | Независим — W4 фиксит хардкод, БРИФ Templates делает его параметризованным |

$END_BRIEF
