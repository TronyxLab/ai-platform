<!--
$START_DEVPLAN
$ARTIFACT_CONTRACT
PURPOSE:      Закрыть 5 системных проблем (S1–S5) в deploy pipeline, обнаруженных при
              бутстрапе голого сервера tronyx-vps (20.07.2026), чтобы `make bootstrap-node`
              → `make node-update` → `make deploy PROJECT=<dir>` работало одной командой.
DESCRIPTION:  Детальный план с суперпозицией вариантов для каждой из 5 проблем.
              S1: NODE_YAML_PATH export в subshell (add-vhost.sh:725) — 2 варианта.
              S2: доставка vhost-файлов на сервер при node-update (новый шаг 2.5 в lifecycle) — 3 варианта.
              S3: гарантированное попадание PLATFORM_DOMAIN в окружение nginx — 3 варианта.
              S4: nginx graceful degradation без project upstream — 3 варианта.
              S5: YAML-валидация node.yaml перед парсингом — 2 варианта.
              Для каждой проблемы: разбор корневой причины, суперпозиция вариантов,
              рекомендуемое решение, точные изменения кода.
RATIONALE:    Каждая из 5 проблем была «обойдена» ручными действиями во время деплоя.
              Без фиксов следующий бутстрап голого сервера потребует тех же ручных вмешательств.
              План минимизирует изменения: S1 (1 строка), S2 (новый шаг ~40 строк), S3 (~15 строк),
              S4 (~5 строк), S5 (~10 строк). Суммарно ~70 строк кода.
ACCEPTANCE_CRITERIA:
  AC-S2: `make render-vhosts NODE=<n> && make node-update NODE=<n>` — vhost-файлы доставлены в
         /opt/node-configs/<n>/overlays/nginx/, nginx видит project vhosts.
  AC-S3: После `make node-update` nginx healthy, PLATFORM_DOMAIN подставлен в
         prometheus.<domain>, hermes.<domain>, langfuse.<domain>, loki.<domain>.
  AC-S4: Nginx healthy даже при отсутствии project-контейнеров (до `make deploy`).
         Project vhosts отдают 502 (graceful degradation).
  AC-S5: `make bootstrap-node NODE=<n>` с синтаксически невалидным node.yaml → exit 1
         с сообщением `FATAL: node.yaml is not valid YAML`.
  AC-S1: `make render-vhosts NODE=<n>` без ручного export — рендерит все project vhosts.
  AC-GATE: `make gate MODE=fast` зелёный до и после всех изменений.
IMPLEMENTS:   Требования из Brief 019-deploy-pipeline-gaps (01-Brief.md), инварианты 1, 6, 9.
IMPACTS:      core/internal/scaffold/add-vhost.sh (S1: строка 725),
              core/internal/bootstrap/node-lifecycle.sh (S2: новый шаг update_step_2.5 + S3 + S5),
              core/internal/bootstrap/deploy-modules.sh (S3: env-файл для PLATFORM_DOMAIN),
              core/internal/provision-environment.sh (S3: --scope env для PLATFORM_DOMAIN),
              core/modules/nginx/config/nginx.conf (S4: глобальный resolver + fallback).
REQUIRES:     Ветка от origin/main, `make gate MODE=fast` зелёный до начала, working tree чистый.
$END_DEVPLAN
-->

# DevPlan: 019-deploy-pipeline-gaps

## 1. Requirements Analysis — Key Success Criteria

| # | Критерий | Метод проверки |
|---|----------|---------------|
| SC1 | `add-vhost.sh --render-all` не требует ручного `export NODE_YAML_PATH` | Запуск `make render-vhosts` без предварительного export → рендерит все vhosts |
| SC2 | Vhost-файлы появляются на сервере после `make node-update` | `ssh` → `ls /opt/node-configs/<n>/overlays/nginx/*.conf` |
| SC3 | `PLATFORM_DOMAIN` подставлен в platform-vhosts после node-update | `docker compose exec nginx cat /etc/nginx/conf.d/prometheus-vhost.conf` |
| SC4 | Nginx healthy без project-контейнеров | `docker compose exec nginx nginx -s quit && docker compose up -d nginx` → healthy без project vhost-ручного комментирования |
| SC5 | Невалидный node.yaml → понятная ошибка | `make bootstrap-node NODE=<n>` с битым YAML → `FATAL: node.yaml is not valid YAML` |
| SC6 | `make gate MODE=fast` зелёный | exit code 0 |

## 2. Architecture Overview

### 2.1 Draft Code Graph

```
                    ┌──────────────────────────────────────┐
                    │         node-lifecycle.sh             │
                    │  --mode init / --mode update          │
                    └──────────────┬───────────────────────┘
                                   │
           ┌───────────────────────┼───────────────────────────┐
           │                       │                           │
           ▼                       ▼                           ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐
│ add-vhost.sh        │  │ deploy-modules.sh   │  │ nginx/nginx.conf         │
│ ──render-all        │  │ ──env-file          │  │ resolver 127.0.0.11      │
│ S1: export          │  │ S3: secrets.env     │  │ S4: global resolver      │
│ NODE_YAML_PATH      │  │ + PLATFORM_DOMAIN   │  │ + set $upstream ""       │
└─────────────────────┘  └─────────────────────┘  └──────────────────────────┘
           │                       │                           │
           └───────────────────────┼───────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │ S2: update_step_2.5         │
                    │ deliver_overlays            │
                    │ rsync node-configs/<n>/     │
                    │   overlays/nginx/*.conf     │
                    │   → /opt/node-configs/<n>/  │
                    │     overlays/nginx/         │
                    └─────────────────────────────┘
```

### 2.2 Data Flow — Node Update with S2 fix

```
make node-update NODE=tronyx-vps
  └─► entrypoints/node-update.sh
       └─► node-lifecycle.sh --mode update
            ├─ 1. verify-core
            ├─ 2. provision (networks + volumes)
            ├─ 2.5. [NEW S2] deliver_overlays  ← rsync GENERATED vhosts
            ├─ 3. ssl-provision (PLATFORM_DOMAIN export ← S3 fix)
            ├─ 4. deploy-docker (PLATFORM_DOMAIN from secrets.env ← S3 fix)
            ├─ 5. deploy-system
            └─ 6. healthcheck
```

## 3. Design Decisions — Superposition Analysis

---

### S1. `NODE_YAML_PATH` не экспортируется в command substitution

**Файл**: `core/internal/scaffold/add-vhost.sh:725`

**Корневая причина**: переменная `NODE_YAML_PATH` присваивается в том же simple command, что и command substitution, но bash не экспортирует переменные в subshell при inline-присвоении (это не `export`, а локальное присвоение для duration команды — но только для самой оболочки, не для её subshell). Python-heredoc внутри `read_node_yaml_projects` (строка 262) читает `os.environ.get('NODE_YAML_PATH', '')` — получает пустую строку.

**Текущий код (строка 725)**:
```bash
NODE_YAML_PATH="$node_yaml" projects_json="$(read_node_yaml_projects "$node_yaml")"
```

## SUPERPOSITION: S1 — Способ передачи пути node.yaml в Python-heredoc

### Option A: Явный export перед вызовом [score: 9/10]
```bash
export NODE_YAML_PATH="$node_yaml"
projects_json="$(read_node_yaml_projects "$node_yaml")"
```
**Trade-offs**: Минимальное изменение (1 строка → 2), сохраняет обратную совместимость, не трогает Python-heredoc.
**Best when**: нужен минимальный фикс без рефакторинга, Python-heredoc остаётся с environ-чтением.
**Риск**: export «протекает» в последующие subshell'ы (но это безопасно для NODE_YAML_PATH).

### Option B: Переделать Python-heredoc на sys.argv[1] [score: 7/10]
```bash
# В read_node_yaml_projects — заменить os.environ.get на sys.argv
projects_json="$(read_node_yaml_projects "$node_yaml")"
# А внутри python3: yaml_path = sys.argv[1]
```
**Trade-offs**: Правильный инженерный подход — нет зависимости от environ; но трогает и bash-обёртку (передачу аргумента), и Python-heredoc (замена environ на argv), и grep-фолбэк (уже использует $1).
**Best when**: нужна «чистая» архитектура без environ-зависимости.
**Риск**: больше строк изменений, нужно тестировать оба пути (python3 и grep fallback).

### Recommendation: Option A — минимальный фикс, 1 строка, не ломает существующее поведение.

**Collapse signal:** Автоматический выбор Option A. Override: "B" если нужен архитектурный рефакторинг.

---

### S2. Vhost-файлы не доставляются на сервер при node-update

**Файл**: `core/internal/bootstrap/node-lifecycle.sh` (update-режим, после step 2)

**Корневая причина**: `make render-vhosts` генерирует `*.conf` в `node-configs/<node>/overlays/nginx/` локально, но update-режим `node-lifecycle.sh` не содержит шага доставки этих файлов на сервер. Context-overlay (git) не должен содержать GENERATED-файлы — они build artifact.

## SUPERPOSITION: S2 — Механизм доставки GENERATED vhost-файлов на сервер

### Option A: Новый шаг update_step_2.5 в node-lifecycle.sh [score: 9/10]
**Approach**: Добавить функцию `update_step_2_5_deliver_overlays()`, которая rsync'ит локальные `node-configs/<n>/overlays/nginx/*.conf` → `/opt/node-configs/<n>/overlays/nginx/` на сервер. Использует существующую инфраструктуру scp-deliver.sh (`scp_to_server` или прямой rsync). Вызывается между provision (step 2) и ssl-provision (step 3).
**Trade-offs**: Идемпотентный (rsync копирует только изменения), использует существующие SSH_OPTS, ~40 строк нового кода в одном месте.
**Best when**: delivery должен быть частью стандартного update-пайплайна, без отдельных команд.
**Риск**: дублирует rsync-логику из scp-deliver.sh. Но scp-deliver.sh рассчитан на delivery всего core/ и node-configs/ — не подходит для точечной доставки overlays.

### Option B: Отдельный make-таргет `make deliver-vhosts` [score: 5/10]
**Approach**: Создать отдельный `make deliver-vhosts NODE=<n>`, который делает rsync. Вызывать ДО `make node-update`.
**Trade-offs**: Явный контроль, не засоряет update-пайплайн.
**Best when**: оператор хочет явно контролировать каждый шаг.
**Риск**: оператор может забыть вызвать — pipeline не самодостаточен. Противоречит цели «одной команды».

### Option C: Включить GENERATED vhosts в git context-overlay [score: 2/10]
**Approach**: Коммитить GENERATED-файлы в git репозиторий контекста, `ensure_context_repo()` подтянет их через git pull.
**Trade-offs**: Не требует нового кода доставки.
**Best when**: GENERATED-файлы рассматриваются как source code.
**Риск**: Нарушает принцип «GENERATED — build artifact, не source code». Git diff будет зашумлён автоматическими изменениями. Файлы с хешем в заголовке меняются при каждом render-vhosts → лишние коммиты.

### Recommendation: Option A — новый шаг в lifecycle, часть стандартного update-пайплайна.

**Collapse signal:** Автоматический выбор Option A.

---

### S3. `PLATFORM_DOMAIN` не гарантированно попадает в Docker-окружение nginx

**Файлы**: `node-lifecycle.sh` (update_step_3 + update_step_4), `deploy-modules.sh`

**Корневая причина**: `export PLATFORM_DOMAIN` происходит внутри `update_step_3_ssl_provision()` (строка 769). Если шаг 3 пропущен по checkpoint — export не происходит. Шаг 4 (`deploy-docker`) вызывает `deploy-modules.sh`, который запускает `docker compose up`. `docker compose` читает переменные из process environment, но если PLATFORM_DOMAIN не экспортирован — nginx получает пустую строку.

**Хронология бага**:
1. Первый `node-update`: step 3 выполняется → export PLATFORM_DOMAIN → compose up → OK
2. Второй `node-update`: step 3 пропущен по checkpoint (checkpoint_step sees `.done` + hash match) → export НЕ происходит → compose up с пустым PLATFORM_DOMAIN → nginx падает

## SUPERPOSITION: S3 — Гарантированная передача PLATFORM_DOMAIN в docker compose up

### Option A: secrets.env как SSoT (Single Source of Truth) [score: 9/10]
**Approach**: В `deploy-modules.sh` перед `docker compose up` (или в `main()`) извлекать PLATFORM_DOMAIN из node.yaml через python3 и дописывать в `/run/platform/secrets.env`, который уже передаётся через `--env-file`. В `update_step_3_ssl_provision` — сохранить export для обратной совместимости, но не полагаться на него.
```bash
# В deploy-modules.sh main() после parse_modules_from_node_yaml:
if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
    local _domain
    _domain=$(python3 -c "import yaml; print(yaml.safe_load(open('$NODE_YAML')).get('domain',''))" 2>/dev/null)
    if [[ -n "$_domain" ]]; then
        export PLATFORM_DOMAIN="$_domain"
        # Также записать в secrets.env для docker compose --env-file
        local _envf="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
        if ! grep -q '^PLATFORM_DOMAIN=' "$_envf" 2>/dev/null; then
            echo "PLATFORM_DOMAIN=$_domain" >> "$_envf"
        fi
    fi
fi
```
**Trade-offs**: secrets.env — единый источник для docker compose, не зависит от checkpoint'ов, не зависит от порядка вызова шагов. ~15 строк кода.
**Best when**: нужна гарантированная передача env-переменных независимо от checkpoint-статуса.
**Риск**: запись в secrets.env может конфликтовать с параллельными процессами (но node-lifecycle однопоточный).

### Option B: Принудительный export в update_step_4_deploy_docker [score: 6/10]
**Approach**: Перед вызовом `deploy-modules.sh` в update_step_4 явно экспортировать PLATFORM_DOMAIN из node.yaml (дублировать логику из step_3).
**Trade-offs**: Просто, ~5 строк.
**Best when**: минимальное изменение.
**Риск**: дублирование логики (step_3 и step_4 будут иметь одинаковый код извлечения domain из node.yaml). Если кто-то вызовет `deploy-modules.sh` напрямую (без lifecycle) — export не произойдёт.

### Option C: provision-environment.sh --scope env для PLATFORM_DOMAIN [score: 7/10]
**Approach**: Добавить PLATFORM_DOMAIN в `platform-env.yaml → env_defaults`, и вызывать `make provision SCOPE=env` из `update_step_2_provision`. Тогда provision-environment.sh запишет PLATFORM_DOMAIN в GITHUB_ENV (CI) или экспортирует локально.
**Trade-offs**: Использует существующую инфраструктуру provision.
**Best when**: все env-переменные платформы управляются через provision.
**Риск**: provision-environment.sh --scope env сейчас пишет в GITHUB_ENV (CI-переменная), а не в локальный env. Для локального использования нужна доработка provision-environment.sh. Это больше изменений, чем вариант A. И domain динамический (из node.yaml), не статический (из platform-env.yaml).

### Recommendation: Option A — secrets.env как SSoT, минимальный и надёжный.

**Точное решение**: гибрид A+B. В `deploy-modules.sh main()` извлекать PLATFORM_DOMAIN из node.yaml и экспортировать его. Также в `update_step_4_deploy_docker` сохранить явный export как belt-and-suspenders. Но основная гарантия — через `deploy-modules.sh`, который всегда вызывается при docker-деплое.

**Collapse signal:** Автоматический выбор Option A с дополнением B как fallback.

---

### S4. Nginx падает при отсутствии upstream-контейнеров проектов

**Файлы**: `core/modules/nginx/config/nginx.conf`, `core/internal/scaffold/add-vhost.sh`

**Корневая причина**: vhost-шаблон уже использует resolver 127.0.0.11 + variable proxy_pass — это правильный подход для lazy DNS resolution. Однако nginx всё равно проверяет доступность resolver при старте. Если Docker DNS (127.0.0.11) недоступен в момент `nginx -t` внутри контейнера — nginx отказывается стартовать.

**Две подпричины**:
1. Resolver объявлен в каждом vhost'e (server-блок), но не в http-блоке nginx.conf. При загрузке конфигурации nginx резолвит resolver на уровне server, но если DNS временно недоступен — падает.
2. Variable proxy_pass с `set $upstream_<name>` не имеет fallback — если DNS не может резолвить хост, nginx падает при старте.

## SUPERPOSITION: S4 — Graceful degradation nginx без project upstream

### Option A: Глобальный resolver + fallback переменная [score: 9/10]
**Approach**: 
1. В `nginx.conf` (http-блок) уже есть `resolver 127.0.0.11 valid=30s ipv6=off;` (строка 110) — **уже сделано**. 
2. В шаблоне vhost (`add-vhost.sh generate_vhost_body`) добавить fallback: если переменная не определена (Docker DNS не доступен на старте), использовать пустой upstream с 502:
```nginx
# Current (строка 418):
set $upstream_${project_name} http://${project_name}:80;
# Proposed:
set $upstream_${project_name} http://${project_name}:80;
# nginx позволяет set с несколькими значениями — последнее «побеждает».
# Но fallback здесь не нужен: resolver 127.0.0.11 + valid=30s
# уже на уровне http — nginx не упадёт при старте.
```
Фактически, **основной фикс уже есть** (resolver в http-блоке nginx.conf строка 110). Проблема была в том, что раньше resolver был только в vhost'ах (server-блок), а не глобально. После переноса resolver в http-блок nginx.conf (что уже сделано в коммитах из сессии деплоя) — nginx должен стартовать без upstream.

**Дополнительно**: в harness'е `nginx_t_harness` уже есть resolver в http-блоке (строка 622). Но harness не тестирует сценарий «нет upstream» — он подменяет SSL-пути и проверяет синтаксис. Нужно добавить проверку graceful degradation.

**Trade-offs**: Минимальные изменения в nginx.conf и add-vhost.sh. ~5 строк.
**Best when**: resolver уже глобальный, нужно только убедиться, что этого достаточно.
**Риск**: не все образы nginx одинаково обрабатывают resolver в http-блоке. Alpine nginx 1.27+ — работает.

### Option B: Динамический include vhosts через map [score: 4/10]
**Approach**: Использовать nginx `map` для включения/выключения vhosts в зависимости от наличия upstream:
```nginx
map $project_deployed $vhost_include {
    default /etc/nginx/conf.d/overlay/*.conf;
    ""       /etc/nginx/conf.d/empty.conf;
}
```
**Trade-offs**: Элегантное решение на уровне nginx.
**Best when**: нужна динамическая активация vhosts без перезагрузки nginx.
**Риск**: map вычисляется при каждом запросе — overhead. Сложность конфигурации возрастает. Не решает проблему старта nginx без upstream.

### Option C: Отложенный деплой vhosts — доставлять только после деплоя проектов [score: 3/10]
**Approach**: Не доставлять project vhosts на сервер, пока проект не задеплоен. Vhost-файлы рендерятся локально, но rsync на сервер происходит только при `make deploy PROJECT=<dir>`.
**Trade-offs**: Нет проблемы «nginx падает без upstream» — vhost'ов просто нет.
**Best when**: строгий контроль порядка деплоя.
**Риск**: усложняет pipeline, ломает идемпотентность node-update, требует координации между `make render-vhosts` и `make deploy`.

### Recommendation: Option A — глобальный resolver уже есть (строка 110 nginx.conf). Добавить диагностику в harness и убедиться, что nginx стартует без upstream.

**Collapse signal:** Автоматический выбор Option A. Override: "B" если нужна динамическая активация vhosts.

---

### S5. Отсутствует YAML-валидация `node.yaml` перед парсингом

**Файл**: `core/internal/bootstrap/node-lifecycle.sh` (init-режим: step_11_read_node_yaml, update-режим: validation перед parsing)

**Корневая причина**: `step_11_read_node_yaml()` (строка 419) делает jsonschema-валидацию структуры, но не проверяет синтаксическую валидность YAML. Если YAML содержит дубликаты ключей, `yaml.safe_load()` либо молча берёт последнее значение, либо (в зависимости от версии PyYAML) падает с неочевидной ошибкой. Симптом: `FATAL: owner_key not found` — потому что дубликат ключа `context:` привёл к потере других полей при парсинге.

## SUPERPOSITION: S5 — Pre-flight YAML синтаксическая валидация

### Option A: Python3 yaml.safe_load pre-flight в начале main() [score: 9/10]
**Approach**: Перед любым шагом, который читает node.yaml (перед validate_bootstrap_env для init, перед derivation для update), добавить проверку:
```bash
# Pre-flight YAML syntax validation
if [[ -f "$NODE_YAML" ]]; then
    if ! python3 -c "
import yaml, sys
try:
    with open('$NODE_YAML') as f:
        yaml.safe_load(f)
except Exception as e:
    print(f'FATAL: node.yaml is not valid YAML: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
        echo "[IMP:10][node-lifecycle] FATAL: node.yaml is not valid YAML — check syntax at ${NODE_YAML}" >&2
        exit 1
    fi
fi
```
**Trade-offs**: ~10 строк, fail-fast до любых попыток парсинга, понятное сообщение.
**Best when**: node.yaml может быть создан вручную (ручная правка, ошибки копипасты).
**Риск**: двойной вызов `yaml.safe_load()` (pre-flight + последующий парсинг) — незначительный overhead для YAML <100KB.

### Option B: shellcheck/yamllint-level валидация [score: 4/10]
**Approach**: Добавить `yamllint` в зависимости и вызывать перед парсингом:
```bash
if ! yamllint --strict "$NODE_YAML"; then
    echo "FATAL: node.yaml failed yamllint validation"
    exit 1
fi
```
**Trade-offs**: yamllint даёт больше диагностики (дубликаты ключей, стиль, отступы).
**Best when**: yamllint уже установлен (но он не входит в apt-зависимости bootstrap — step_2 устанавливает python3-yaml, не yamllint).
**Риск**: добавляет зависимость (yamllint), которая не нужна для других операций.

### Recommendation: Option A — лёгкая pre-flight проверка через python3, который уже гарантированно установлен (step_2 bootstrap: python3-yaml).

**Collapse signal:** Автоматический выбор Option A.

---

## 4. Step-by-Step Implementation Plan

### Wave 1: S1 + S5 (низкая сложность, независимые)

**Task 1.1 — S1 fix: `add-vhost.sh:725`**
- Файл: `core/internal/scaffold/add-vhost.sh`
- Строка 725: заменить `NODE_YAML_PATH="$node_yaml" projects_json=...` на две строки с `export`
- Изменение: +1 строка
- Верификация: `make render-vhosts NODE=tronyx-vps` без предварительного export

**Task 1.2 — S5 fix: YAML validation pre-flight**
- Файл: `core/internal/bootstrap/node-lifecycle.sh`
- Добавить pre-flight в начало `main()` для init-режима (после `validate_bootstrap_env()`, до checkpoint_step)
- Добавить pre-flight в update-режим `main()` (после NODE_YAML derivation)
- Изменение: ~10 строк (по ~5 в каждом режиме)
- Верификация: создать битый node.yaml → `make bootstrap-node` → FATAL message

### Wave 2: S2 (средняя сложность, зависит от чтения scp-deliver.sh)

**Task 2.1 — S2 fix: новый шаг update_step_2_5_deliver_overlays**
- Файл: `core/internal/bootstrap/node-lifecycle.sh`
- Добавить функцию `update_step_2_5_deliver_overlays()` после `update_step_2_provision()`
- Функция:
  1. Проверяет наличие локальной директории `node-configs/<node>/overlays/nginx/`
  2. Если есть `.conf` файлы — rsync на сервер: `/opt/node-configs/<node>/overlays/nginx/`
  3. Использует `REMOTE_SSH_USER`, `REMOTE_SSH_HOST` из env или вывод из NODE_YAML (host из `node.yaml#host`)
  4. Идемпотентный — rsync копирует только изменившиеся файлы
- Вызов: вставить в update-режим `main()` между step 2 (provision) и step 3 (ssl-provision)
- Изменение: ~40 строк
- Верификация: `make render-vhosts && make node-update` → ssh ls /opt/.../overlays/nginx/

**Task 2.2 — Интеграция с checkpoint**
- Зарегистрировать новый шаг в checkpoint-системе: `CHECKPOINT_STEP_HASH=... checkpoint_step "deliver-overlays" update_step_2_5_deliver_overlays`
- Контент-хеш должен включать саму функцию + scp-deliver.sh (если используется)

### Wave 3: S3 (низкая сложность, зависит от понимания deploy-modules.sh)

**Task 3.1 — S3 fix: PLATFORM_DOMAIN в deploy-modules.sh**
- Файл: `core/internal/bootstrap/deploy-modules.sh`
- В `main()`, после `parse_modules_from_node_yaml`, добавить извлечение `domain` из node.yaml и экспорт PLATFORM_DOMAIN:
```bash
# Ensure PLATFORM_DOMAIN from node.yaml (S3 fix)
if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
    local _domain
    _domain=$(python3 -c "import yaml; print(yaml.safe_load(open('$NODE_YAML')).get('domain',''))" 2>/dev/null)
    if [[ -n "$_domain" ]]; then
        export PLATFORM_DOMAIN="$_domain"
        echo "[IMP:9][deploy-modules][S3] PLATFORM_DOMAIN=${_domain} exported from node.yaml" >&2
    fi
fi
```
- Изменение: ~10 строк
- Верификация: `docker compose exec nginx env | grep PLATFORM_DOMAIN`

**Task 3.2 — Belt-and-suspenders: сохранить export в step_3 как fallback**
- Файл: `core/internal/bootstrap/node-lifecycle.sh`
- В `update_step_3_ssl_provision` — уже есть export. Оставить.
- В `update_step_4_deploy_docker` — перед вызовом `deploy-modules.sh` убедиться, что PLATFORM_DOMAIN экспортирован (добавить проверку с WARN, если пусто).

### Wave 4: S4 (низкая сложность)

**Task 4.1 — S4 fix: верификация глобального resolver**
- Файл: `core/modules/nginx/config/nginx.conf`
- Глобальный resolver уже есть (строка 110): `resolver 127.0.0.11 valid=30s ipv6=off;`
- **Никаких изменений в nginx.conf не требуется.**
- Убедиться, что vhost-шаблон не добавляет конфликтующий resolver (сейчас добавляет свой resolver в server-блок — это нормально, server-уровень переопределяет http-уровень для этого блока, но не ломает глобальный).

**Task 4.2 — S4 fix: harness проверка graceful degradation**
- Файл: `core/internal/scaffold/add-vhost.sh` (функция `nginx_t_harness`)
- Добавить проверку: запустить nginx с vhost'ами, где upstream не существует → nginx должен стартовать и отдавать 502:
```bash
# В harness после nginx -t: проверить, что nginx стартует без upstream
# (уже тестируется косвенно — harness не имеет реальных upstream-контейнеров)
```
- Harness уже проверяет `nginx -t` без реальных upstream (использует мок-сертификаты). Если nginx -t проходит — конфигурация валидна, nginx стартует.
- **Фактически: изменений в harness не требуется.**

**Task 4.3 — S4 fix: документация/комментарий**
- Добавить комментарий в `generate_vhost_body()` о том, что глобальный resolver в nginx.conf + variable proxy_pass обеспечивают graceful degradation.

## 5. File Manifest

| Файл | Статус | Изменение | Строк | Связан с |
|------|--------|-----------|-------|----------|
| `core/internal/scaffold/add-vhost.sh` | MODIFY | строка 725: export NODE_YAML_PATH | +1 | S1 |
| `core/internal/scaffold/add-vhost.sh` | MODIFY | комментарий в generate_vhost_body() | +3 | S4 doc |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | update_step_2_5_deliver_overlays() — новый шаг | +40 | S2 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | pre-flight YAML validation в init main() | +6 | S5 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | pre-flight YAML validation в update main() | +6 | S5 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY | checkpoint-регистрация для нового шага | +4 | S2 |
| `core/internal/bootstrap/deploy-modules.sh` | MODIFY | PLATFORM_DOMAIN export из node.yaml в main() | +10 | S3 |

**Total: ~70 строк кода в 3 файлах.**

**НЕ ТРЕБУЕТ изменений:**
- `core/moducles/nginx/config/nginx.conf` — глобальный resolver уже есть (строка 110)
- `core/internal/provision-environment.sh` — не требуется для S3 (domain из node.yaml, не platform-env.yaml)

## 6. Verification Plan

### Pre-merge verification (локально)

| # | Проверка | Команда | Ожидаемый результат |
|---|----------|---------|---------------------|
| V1 | S1: render-vhosts без export | `make render-vhosts NODE=tronyx-vps` | Рендерит все project vhosts |
| V2 | S2: node-update dry-run показывает новый шаг | `make node-update NODE=tronyx-vps DRY_RUN=true` | В выводе: `2.5. deliver-overlays` |
| V3 | S3: PLATFORM_DOMAIN в docker окружении | После node-update: `ssh tronyx-vps "docker compose exec nginx sh -c 'echo \$PLATFORM_DOMAIN'"` | `tronyx.ru` |
| V4 | S4: nginx healthy без проектов | `ssh tronyx-vps "docker compose ps nginx"` | `healthy` |
| V5 | S5: невалидный YAML → fail-fast | Создать node.yaml с дубликатом ключа → `make bootstrap-node NODE=test` | `FATAL: node.yaml is not valid YAML` |
| V6 | gate | `make gate MODE=fast` | exit 0 |

### Post-deploy verification (на сервере)

| # | Проверка | Команда | Ожидаемый результат |
|---|----------|---------|---------------------|
| P1 | S2: vhost-файлы доставлены | `ls /opt/node-configs/tronyx-vps/overlays/nginx/*.conf` | Список .conf файлов |
| P2 | S3: platform-vhosts имеют правильный server_name | `docker compose exec nginx cat /etc/nginx/conf.d/prometheus-vhost.conf` | `server_name prometheus.tronyx.ru;` |
| P3 | S4: nginx не в рестарт-лупе | `docker compose ps nginx` | Status: healthy (не restarting) |

## 7. Rollback Plan

Все изменения — аддитивные (добавление шага, экспорт переменной, валидация). Нет изменений существующей логики.

- S1: revert строки 725 → исходная одна строка (потеря export — но функция и так не работала без ручного export)
- S2: удалить вызов update_step_2_5 из main() → vhosts просто не доставляются (как сейчас)
- S3: удалить блок export PLATFORM_DOMAIN из deploy-modules.sh → вернуться к зависимости от step_3 export
- S4: без изменений
- S5: удалить pre-flight блок → ошибки YAML снова неочевидны
