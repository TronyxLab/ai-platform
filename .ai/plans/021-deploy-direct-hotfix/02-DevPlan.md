# GREP_SUMMARY: devplan 021 deploy-direct-hotfix T1 phantom-make-deploy T2 org-aware-paths T3 deploy-project entrypoint audit DEPLOY-DIRECT
# STRUCTURE: ▶ T1(make deploy fix) + T2(org-aware paths) ∥ Wave1 → ▶ T3(direct deploy entrypoint) Wave2 → ▶ T4(verification) Wave3

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть три gap в модели деплоя проектов: (1) фантомный `make deploy` — замена вызова `deploy.sh` на реальный `git push origin main` с валидацией, (2) org-aware пути на VPS — `platform-deliver <org> <project>` → `/opt/projects/<org>/<name>/` с обратной совместимостью, (3) прямой деплой минуя CI — новый `make deploy-project PROJECT=<dir> NODE=<node>` entrypoint с аудит-логом DEPLOY-DIRECT.
DESCRIPTION:           Три атомарные задачи, реализуемые последовательно-параллельно в 4 волны. T1 (фантомный make deploy) и T2 (org-aware пути) независимы и реализуются в Wave 1. T3 (прямой деплой) зависит от T2 (использует org-aware platform-deliver) — Wave 2. T4 (верификация, тесты, gate) — Wave 3.
RATIONALE:             T1: комментарий `Makefile:436` обещает «git push → CI → forced-command», но код вызывает `deploy.sh` — SSH-entrypoint, не делающий git push. При локальном вызове `deploy.sh "$PROJECT"` парсит PROJECT как путь и пытается выполнить docker compose локально. T2: на dev-машине проекты в `~/projects/<org>/<name>/`, на VPS в `/opt/projects/<name>/`. Org теряется в `handle_deliver()` → коллизия имён при росте числа org/проектов. T3: единственный путь деплоя — `git push` через GitHub Actions. При недоступности CI или срочном деплое разработчик делает ручной scp/tar — платформа не предоставляет канонического инструмента.
ACCEPTANCE_CRITERIA:
  AC-T1.1: `make deploy PROJECT=<git-repo-dir>` → `cd <dir> && git push origin main` выполнен, exit 0
  AC-T1.2: `make deploy PROJECT=<не-git-директория>` → exit 1 с диагностикой
  AC-T1.3: `make deploy PROJECT=<git-repo-без-remote>` → exit 1 с диагностикой
  AC-T2.1: `platform-deliver myorg myproject` → `/opt/projects/myorg/myproject/` создан
  AC-T2.2: `platform-deliver myproject` (старый формат) → `/opt/projects/myproject/` (backward compat)
  AC-T2.3: CI workflow `deploy-project.yml` передаёт org в `platform-deliver`
  AC-T3.1: `make deploy-project PROJECT=<dir> NODE=<node>` → успешный деплой на VPS
  AC-T3.2: В `/var/log/platform/audit.log` запись с типом `DEPLOY-DIRECT`
  AC-T3.3: `make deploy-project PROJECT=<dir> NODE=<nonexistent>` → exit 1 с диагностикой
  AC-T3.4: `make deploy-project PROJECT=<dir-без-ai-platform.yaml>` → exit 1 с диагностикой
  AC-T4.1: `make gate MODE=fast` зелёный после всех изменений
  AC-T4.2: `make lint` зелёный (shellcheck для новых/изменённых скриптов)
IMPLEMENTS:            Запрос владельца от 2026-07-21 — «Создай бриф по задачам: фантомный make deploy, асимметрия путей, нет прямого деплоя».
IMPACTS:               Makefile (deploy target: замена вызова + новый deploy-project target), core/internal/deploy/deploy-project.sh (handle_deliver сигнатура, parse_ssh_command dispatch), core/entrypoints/deploy.sh (без изменений — сохраняет контракт SSH forced-command), .github/workflows/deploy-project.yml (передача org), core/internal/bootstrap/node-lifecycle.sh (mkdir /opt/projects/<org>/), core/internal/scaffold/add-project.sh (верификация — уже org-aware), templates/template-*/Makefile (3 файла — PLATFORM_DIR reference), core/AGENTS.md (каноническая таблица: deploy row update + deploy-project row), core/entrypoint-manifest.yaml (deploy entry update + deploy-project entry), docs/projects-root-AGENTS.md (документация deploy-project), NEW: core/entrypoints/deploy-project.sh (entrypoint для прямого деплоя), NEW: tests/test_deploy_entrypoint.py (тесты валидации entrypoint), tests/gates/test_gate_thin_wrapper.py (allowlist entry), tests/gates/test_gate_no_unregistered_entrypoint.py (exception entry).
REQUIRES:              Ветка от origin/main, `make gate MODE=fast` зелёный, working tree чистый. Понимание текущего контракта platform-deliver verb (D2, DevPlan 007), node-resolver.sh API для NODE → SSH host резолвинга.
$END_ARTIFACT_CONTRACT

---

## 1. Requirements Analysis

### 1.1 Key Success Criteria

| # | Критерий | Измеримость |
|---|----------|-------------|
| S1 | `make deploy` делает реальный `git push origin main` из папки проекта | Ручной тест с тестовым репо |
| S2 | `platform-deliver` поддерживает org+project и backward compat без org | SSH-тест на VPS |
| S3 | `make deploy-project` — канонический инструмент прямого деплоя | Ручной тест с реальным VPS |
| S4 | Все изменения проходят `make gate MODE=fast` | CI/pytest (zero regressions) |
| S5 | Обратная совместимость: существующие CI-деплои не ломаются | CI workflow тест |

### 1.2 Constraints

- **Cross-layer rule:** entrypoints/ → internal/, не наоборот. Новый `deploy-project.sh` (entrypoint) вызывает существующие internal/ скрипты через SSH forced-command.
- **Single-responsibility:** `deploy.sh` сохраняет контракт «SSH forced-command entrypoint для VPS» — НЕ рефакторится для локального запуска.
- **Backward compat:** старый формат `platform-deliver <project>` (без org) продолжает работать.
- **Verb dictionary:** `deploy-project` — новый глагол, регистрируется в `entrypoint-manifest.yaml` и `core/AGENTS.md`.
- **No new SSH keys:** T3 использует существующий `ci-deploy` ключ (уже есть у разработчика для bootstrap).
- **Audit trail:** все прямые деплои (T3) маркируются `DEPLOY-DIRECT` в `/var/log/platform/audit.log`.

---

## 2. Architecture Overview

### 2.1 Draft Code Graph

```
┌─ Makefile ──────────────────────────────────────────────────────┐
│  deploy:          (T1) cd $(PROJECT) && git push origin main    │
│  deploy-project:  (T3) NEW → core/entrypoints/deploy-project.sh │
└──────────────────────────┬──────────────────────────────────────┘
                           │ T3
                           ▼
┌─ core/entrypoints/deploy-project.sh (NEW) ──────────────────────┐
│  ▶ validate PROJECT (ai-platform.yaml exists)                   │
│  ▶ resolve NODE → SSH host (node-resolver.sh)                   │
│  ▶ extract ORG from PROJECT path (~/projects/<org>/<name>/)     │
│  ▶ tar files → ssh "platform-deliver <org> <name>"              │
│  ▶ ssh "PLATFORM_DEPLOY_DIRECT=1 deploy.sh <name> <sha> prod"   │
│  ▶ verify (optional) → ssh "deploy.sh verify <node>"             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ SSH forced-command
                           ▼
┌─ core/entrypoints/deploy.sh (existing — NO changes) ────────────┐
│  parse_verb: deploy | remove | status | verify                  │
│  * case → exec deploy-project.sh $project $sha $env             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ exec
                           ▼
┌─ core/internal/deploy/deploy-project.sh (T2 — org-aware) ───────┐
│  handle_deliver(org, project):                                   │
│    org provided → ${PROJECTS_BASE}/${org}/${project}             │
│    org absent   → ${PROJECTS_BASE}/${project} (backward compat)  │
│  parse_ssh_command:                                              │
│    "platform-deliver org project" → 2-arg new format             │
│    "platform-deliver project"     → 1-arg old format             │
│  main(): DEPLOY_DIRECT env → audit_log(DEPLOY-DIRECT, ...)       │
└──────────────────────────────────────────────────────────────────┘

┌─ .github/workflows/deploy-project.yml (T2) ─────────────────────┐
│  inputs: +org (new optional input)                               │
│  Deliver step:                                                   │
│    tar czf - $FILES | ssh "platform-deliver ${{ inputs.org }}    │
│    ${{ inputs.project_name }}"                                   │
│  (backward compat: org empty → old format)                       │
└──────────────────────────────────────────────────────────────────┘

┌─ core/internal/bootstrap/node-lifecycle.sh (T2) ────────────────┐
│  step_6b: mkdir -p /opt/projects → chown ci-deploy:ci-deploy    │
│  (existing — дополнительные org-директории создаются динамически │
│   через handle_deliver mkdir -p при первом platform-deliver)     │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Module Dependency Map

```
deploy-project.sh (entrypoint, NEW)
  ├── depends on: core/lib/node-resolver.sh (NODE → SSH host)
  ├── depends on: core/lib/logging.sh
  ├── uses: ssh + tar (stdlib)
  └── calls via SSH: platform-deliver (deploy-project.sh on VPS)
                     deploy.sh (deploy-project.sh on VPS)

deploy-project.sh (internal, existing — modified)
  ├── depends on: core/lib/paths.sh, logging.sh, audit_logging.sh
  ├── handle_deliver(): signature changed (org, project)
  └── parse_ssh_command(): dispatch updated for 2-arg platform-deliver
```

---

## 3. Step-by-Step Data Flow

### 3.1 T1 — `make deploy` (исправленный)

```
User: make deploy PROJECT=~/projects/tronyx161/dance-site
  ↓
Makefile (deploy target):
  1. Validate PROJECT is set (existing)
  2. Validate PROJECT/.git exists → exit 1 if not
  3. Validate git remote origin exists → exit 1 if not
  4. Validate no uncommitted changes → warn, continue
  5. cd "$(PROJECT)" && git push origin main
  6. exit 0 (дальше — штатный CI-путь через GitHub Actions)
```

### 3.2 T2 — `platform-deliver` с org

**Новый формат (2 аргумента):**
```
CI / deploy-project.sh (dev):
  echo "platform-deliver tronyx161 dance-site" | ssh ci-deploy@vps
    ↓
deploy.sh parse_verb():
  first_token != remove/status/verify → * case (deploy, backward compat)
  exec deploy-project.sh platform-deliver tronyx161 dance-site
    ↓
deploy-project.sh parse_ssh_command():
  raw == "platform-deliver tronyx161 dance-site"
  local project="tronyx161 dance-site"
  → has space? Yes → split: org="tronyx161", project="dance-site"
  PROJECT_DIR="${PROJECTS_BASE}/tronyx161/dance-site"
    ↓
handle_deliver "dance-site" with org context:
  project_dir="/opt/projects/tronyx161/dance-site/"
  mkdir -p → extract → validate → atomic mv
```

**Старый формат (1 аргумент, backward compat):**
```
  echo "platform-deliver dance-site" | ssh ci-deploy@vps
    ↓
deploy-project.sh parse_ssh_command():
  raw == "platform-deliver dance-site"
  local project="dance-site"
  → no space → old format
  PROJECT_DIR="${PROJECTS_BASE}/dance-site"
    ↓
handle_deliver "dance-site":
  project_dir="/opt/projects/dance-site/"
  (поведение не изменено)
```

### 3.3 T3 — `make deploy-project` (прямой деплой)

```
User: make deploy-project PROJECT=~/projects/tronyx161/dance-site NODE=tronyx-vps
  ↓
Makefile (deploy-project target):
  1. Validate PROJECT and NODE are set
  2. Delegate to core/entrypoints/deploy-project.sh --project "$(PROJECT)" --node "$(NODE)"
  ↓
core/entrypoints/deploy-project.sh:
  1. Validate PROJECT dir exists + ai-platform.yaml exists
  2. Extract ORG from path: ~/projects/<org>/<name>/ → org, name
  3. Resolve NODE → SSH host (node-resolver.sh or inline python)
  4. Build file list: ai-platform.yaml + docker-compose.yml + .env.platform
  5. tar czf - $FILES | ssh ci-deploy@host "platform-deliver <org> <name>"
  6. SHA = $(git -C $PROJECT rev-parse HEAD)
  7. ssh ci-deploy@host "PLATFORM_DEPLOY_DIRECT=1 /opt/platform/core/entrypoints/deploy.sh <name> <sha> production"
  8. (optional) ssh ci-deploy@host "/opt/platform/core/entrypoints/deploy.sh verify <node>"
  9. Print result summary
  ↓
VPS side (deploy-project.sh main):
  - Detects PLATFORM_DEPLOY_DIRECT=1 env
  - audit_log("platform-deploy:${PROJECT}", "DEPLOY-DIRECT", "Direct deploy by ${USER} from ${SSH_CLIENT}")
  - Normal deploy pipeline: pull → healthcheck → tag → prune → hooks
```

---

## 4. Design Decisions

### D1 — T1: `make deploy` = прямой git push wrapper

```
@rationale Q: Почему не рефакторить deploy.sh для локального режима?
           A: deploy.sh спроектирован как SSH forced-command entrypoint (контракт deploy.sh:10).
           Добавление локального режима нарушит single-responsibility, усложнит parse_verb()
           и создаст риск вызова VPS-логики на dev-машине. Комментарий в Makefile:436 обещает
           «git push → CI → forced-command» — исправляем код под комментарий, а не наоборот.

@rationale Q: Почему git push origin main, а не git push origin $(git branch --show-current)?
           A: Деплой в production всегда из main (инвариант модели). Для staging — отдельный
           CI workflow. Push текущей ветки может случайно задеплоить feature-ветку.
```

**Implementation:**
```makefile
## deploy: Deploy project via git push → CI pipeline
##   Usage: make deploy PROJECT=<dir>
##   Pushes main branch to origin, triggering CI workflow
deploy:
	@echo "[IMP:7][make][deploy] Deploying PROJECT=$(PROJECT)..."
	@if [[ -z "$(PROJECT)" ]]; then \
		echo "[IMP:9][make][deploy] ERROR: PROJECT not set — usage: make deploy PROJECT=<dir>" >&2; \
		exit 1; \
	fi
	@if [[ ! -d "$(PROJECT)/.git" ]]; then \
		echo "[IMP:9][make][deploy] ERROR: $(PROJECT) is not a git repository" >&2; \
		exit 1; \
	fi
	@if ! git -C "$(PROJECT)" remote get-url origin >/dev/null 2>&1; then \
		echo "[IMP:9][make][deploy] ERROR: No git remote 'origin' in $(PROJECT)" >&2; \
		exit 1; \
	fi
	@cd "$(PROJECT)" && git push origin main
	@echo "[IMP:9][make][deploy] Git push complete — CI pipeline triggered"
```

### D2 — T2: org-aware platform-deliver с backward compat через количество аргументов

```
@rationale Q: Почему backward compat через количество аргументов, а не через флаг --org?
           A: Флаг --org меняет синтаксис для существующих вызовов (все CI workflow нужно
           обновить немедленно). Подсчёт аргументов (1 аргумент = старый формат, 2 аргумента =
           новый формат) сохраняет обратную совместимость без изменений в вызывающем коде.
           Единственный risk: проект с именем, содержащим пробел — но имена проектов
           валидируются как `[a-zA-Z0-9_-]+` в add-project.sh:126.

@rationale Q: Почему не использовать ai-platform.yaml для хранения org (вариант C из Brief)?
           A: DevPlan 020 удалил поле `context` из ai-platform.yaml. Возвращать org-поле
           противоречит архитектурному решению. Org определяется из пути на dev-машине
           и передаётся явно в verb — это сохраняет ai-platform.yaml как per-project манифест
           без привязки к org-контексту.
```

**parse_ssh_command() dispatch:**
```bash
# deploy-project.sh parse_ssh_command() — platform-deliver dispatch
if [[ "$raw" == "platform-deliver "* ]]; then
    local args="${raw#platform-deliver }"
    args="$(echo "$args" | xargs)"
    local org=""
    local project=""
    # Detect format: two tokens = org+project (new), one token = project (old)
    if [[ "$args" == *" "* ]]; then
        org="${args%% *}"
        project="${args#* }"
        project="$(echo "$project" | xargs)"
        log_imp 8 "args" "platform-deliver: org=${org} project=${project}"
    else
        project="$args"
        log_imp 8 "args" "platform-deliver (legacy): project=${project}"
    fi
    PROJECT="${project}"
    PROJECT_DIR="${PROJECTS_BASE}/${org:+${org}/}${project}"
    DEPLOY_STATUS="deliver"
    handle_deliver "$project" "$org"
    exit 0
fi
```

### D3 — T3: отдельный entrypoint deploy-project.sh (не расширение deploy.sh)

```
@rationale Q: Почему новый entrypoint, а не расширение существующего deploy.sh?
           A: deploy.sh — SSH forced-command entrypoint для VPS (контракт deploy.sh:10).
           Новый entrypoint deploy-project.sh — локальный инструмент разработчика.
           Разные responsibility, разное окружение (локальное vs VPS), разные контракты.
           deploy.sh оперирует SSH_ORIGINAL_COMMAND; deploy-project.sh оперирует CLI-аргументами.
           Объединение создало бы раздутый parse_verb() с трудно-тестируемой логикой.

@rationale Q: Почему две SSH-команды (platform-deliver → deploy.sh), а не одна?
           A: Повторяет CI-паттерн из deploy-project.yml: deliver payload → deploy.
           platform-deliver — легковесная операция (читает stdin, пишет файлы).
           deploy.sh — тяжеловесная (docker pull → healthcheck → tag → prune).
           Разделение позволяет отловить ошибку доставки (сеть, размер) до начала деплоя.
           При совмещении ошибка network timeout после docker pull, но до healthcheck
           оставляет контейнер в неопределённом состоянии без аудит-лога.
```

### D4 — T3: NODE_HOST_MAP резолвинг через node-resolver.sh

```
@rationale Q: Почему node-resolver.sh, а не передача SSH host напрямую?
           A: node-resolver.sh — канонический механизм резолвинга NODE → SSH host (K4/K5).
           Принимает NODE_HOST_MAP из env (org variable в CI, ~/.env или .env на dev-машине).
           Передача host напрямую требует от разработчика знания IP/DNS — нарушает UX.
           Используем: source core/lib/node-resolver.sh; resolve_node_from_env "$NODE"
```

### D5 — T3: Аудит-лог DEPLOY-DIRECT через env-переменную

```
@rationale Q: Почему PLATFORM_DEPLOY_DIRECT=1 env, а не отдельный verb в deploy.sh?
           A: deploy.sh K1-contract уже имеет 4 verb (deploy/remove/status/verify).
           Добавление пятого verb для маркировки прямого деплоя — семантически неверно
           (это тот же deploy, но с другим источником). Env-переменная передаётся
           прозрачно через SSH forced-command и читается в deploy-project.sh main().
           Если переменная не установлена → обычный CI-деплой (без изменений в поведении).
```

---

## 5. $TASKS

### Task Dependency Graph

```
TASK-T1 ──────────────────────┐
  (phantom make deploy)       │
                              ├──► TASK-T3 ──► TASK-T4
TASK-T2 ──────────────────────┘    (direct      (verification)
  (org-aware paths)                deploy)
```

### TASK-T1: Fix phantom make deploy

| Поле | Значение |
|------|----------|
| **ID** | TASK-T1 |
| **Priority** | HIGH |
| **Dependencies** | None |
| **Complexity** | 3/10 |
| **Files** | `Makefile` (deploy target, lines 433-443), `core/AGENTS.md` (deploy row), `core/entrypoint-manifest.yaml` (deploy entry) |
| **Owner** | Coder |
| **Output** | Исправленный `make deploy` таргет с валидацией |
| **Acceptance** | AC-T1.1, AC-T1.2, AC-T1.3 |

**Changes:**
1. **Makefile:433-443** — Заменить вызов `deploy.sh` на `git push origin main` с валидацией:
   - Проверка `PROJECT` не пуст (уже есть)
   - Проверка `PROJECT/.git` существует
   - Проверка `git remote origin` существует
   - `cd "$(PROJECT)" && git push origin main`
   - Обновить комментарий (убрать «Delegates to deploy.sh»)
2. **core/AGENTS.md:23** — Обновить строку `make deploy`: delegation `git push → CI → core/internal/deploy/deploy-project.sh`
3. **core/entrypoint-manifest.yaml:30** — Обновить `mechanism: git-push` (было `git-push-ci`), `delegates_to: git push → CI → ...`

**TRAP annotations:**
- Никаких TRAP — это исправление бага (фантомный таргет), не архитектурное решение.

### TASK-T2: Org-aware paths (platform-deliver + CI + bootstrap)

| Поле | Значение |
|------|----------|
| **ID** | TASK-T2 |
| **Priority** | HIGH |
| **Dependencies** | None |
| **Complexity** | 6/10 |
| **Files** | `core/internal/deploy/deploy-project.sh` (handle_deliver + parse_ssh_command), `.github/workflows/deploy-project.yml` (deliver step + inputs), `core/internal/bootstrap/node-lifecycle.sh` (step_6b), `core/internal/scaffold/add-project.sh` (verify only), `templates/template-*/Makefile` (3 файла — PLATFORM_DIR) |
| **Owner** | Coder |
| **Output** | org-aware `platform-deliver` с backward compat; CI передаёт org |
| **Acceptance** | AC-T2.1, AC-T2.2, AC-T2.3 |

**Changes:**

1. **deploy-project.sh:232-391 `handle_deliver()`** — изменить сигнатуру:
   - `handle_deliver() { local project="$1"; local org="${2:-}"; ... }`
   - `local project_dir="${PROJECTS_BASE}/${org:+${org}/}${project}"`
   - Все audit_log/log_imp сообщения включают org если передан
   - `_validate_project_name` уже валидирует имя проекта (без изменений)

2. **deploy-project.sh:428-440 `parse_ssh_command()` platform-deliver dispatch** — обновить:
   - Извлечь `args="${raw#platform-deliver }"`
   - Если args содержит пробел → org+project (новый формат)
   - Если нет → project только (старый формат, backward compat)
   - `PROJECT_DIR="${PROJECTS_BASE}/${org:+${org}/}${project}"`
   - Вызвать `handle_deliver "$project" "$org"`

3. **deploy-project.yml:76-83** — обновить deliver step:
   - Добавить `org` в `inputs:` (необязательный, default: "")
   - `tar czf - $FILES | ssh ... "platform-deliver ${{ inputs.org }} ${{ inputs.project_name }}"`
   - Если org пустой → `platform-deliver  dance-site` (два пробела подряд). 
     **Важно:** обработка двойного пробела: `xargs` в parse_ssh_command схлопнет двойной пробел в одиночный → старый формат корректно распознается.
   - Альтернативно: conditional `${{ inputs.org && format('{0} {1}', inputs.org, inputs.project_name) || inputs.project_name }}`

4. **node-lifecycle.sh:340-363 `step_6b_create_projects_base()`** — проверить:
   - Текущая реализация создаёт `/opt/projects` и chown ci-deploy. 
   - Org-поддиректории создаются динамически в `handle_deliver()` через `mkdir -p`.
   - **Изменение:** добавить документацию в comment что org-директории создаются при первом platform-deliver.
   - Без логических изменений (существующий `mkdir -p /opt/projects` + `chown ci-deploy:ci-deploy` достаточен).

5. **add-project.sh** — верификация (без изменений):
   - `PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"` — уже корректно
   - `project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"` — уже org-aware
   - TRAP-комментарий: "Org-aware path verified — no changes needed for T2"

6. **templates/template-*/Makefile** (3 файла) — проверить:
   - `PLATFORM_DIR ?= $(HOME)/projects/ai-platform` — НЕ зависит от org
   - Шаблонные Makefile используют `PLATFORM_DIR` только для делегирования `sync-env`/`status` в платформенный Makefile
   - **Изменений не требуется** — PLATFORM_DIR не меняется от org-aware путей

**TRAP annotations:**
```bash
# 🧐 TRAP[DECISION] · 2026-07-21 · — · platform-deliver backward compat via argument count
# · Rejected: --org flag (breaks existing CI calls immediately)
# · Reason: 1-arg = old format, 2-arg = new format. Project names validated as [a-zA-Z0-9_-]+
#   (no spaces) → space-separated detection is unambiguous.
# · Rev: if project names ever allow spaces → switch to explicit --org flag
```

### TASK-T3: Direct deploy entrypoint (deploy-project.sh)

| Поле | Значение |
|------|----------|
| **ID** | TASK-T3 |
| **Priority** | HIGH |
| **Dependencies** | TASK-T2 (uses org-aware platform-deliver format) |
| **Complexity** | 7/10 |
| **Files** | NEW: `core/entrypoints/deploy-project.sh`, `Makefile` (new deploy-project target), `core/AGENTS.md` (new row), `core/entrypoint-manifest.yaml` (new entry), `docs/projects-root-AGENTS.md` (documentation) |
| **Owner** | Coder |
| **Output** | Канонический `make deploy-project` с аудит-логом |
| **Acceptance** | AC-T3.1, AC-T3.2, AC-T3.3, AC-T3.4 |

**Changes:**

1. **NEW: `core/entrypoints/deploy-project.sh`** (~350 lines) — entrypoint для прямого деплоя. Entrypoint превышает стандартный лимит 150 LOC из-за 7-функционального pipeline (validation→extraction→resolution→delivery→deploy→verify). Добавлен в thin_wrapper allowlist (`test_gate_thin_wrapper.py:54`).
   ```bash
   #!/usr/bin/env bash
   # GREP_SUMMARY: deploy-project entrypoint direct-deploy emergency bypass-ci audit DEPLOY-DIRECT
   # STRUCTURE: ▶ validate(args) → ◇ resolve NODE→host → ⊕ extract org from path → ◆ tar+ssh deliver → ◆ ssh deploy → ◆ verify → ⎋ audit summary
   # region MODULE_CONTRACT
   ## @purpose  Direct project deploy bypassing CI (emergency fallback).
   ##           tar + ssh platform-deliver → ssh deploy.sh → audit DEPLOY-DIRECT.
   ## @scope    Called from Makefile: `make deploy-project PROJECT=<dir> NODE=<node>`
   ## @invariants
   ##   - PROJECT must contain ai-platform.yaml + docker-compose.yml|compose.yaml
   ##   - NODE must resolve to SSH host via NODE_HOST_MAP (K4/K5)
   ##   - ORG extracted from PROJECT path (~/projects/<org>/<name>/)
   ##   - Audit log on VPS marked DEPLOY-DIRECT
   ##   - shellcheck clean, set -euo pipefail
   # endregion MODULE_CONTRACT
   ```
   **Функции:**
   - `parse_args()` — `--project <dir> --node <name> [--skip-verify] [--dry-run]`
   - `validate_project()` — проверка ai-platform.yaml + compose file
   - `extract_org()` — извлечение org из пути `~/projects/<org>/<name>/`
   - `resolve_node_host()` — source `core/lib/node-resolver.sh`; `resolve_node_from_env "$NODE"`
   - `deliver_payload()` — tar + ssh platform-deliver
   - `ssh_deploy()` — ssh с `PLATFORM_DEPLOY_DIRECT=1` + deploy.sh
   - `verify_deploy()` — ssh `deploy.sh verify <node>` (опционально, `--skip-verify` для пропуска)

2. **Makefile** — новый target `deploy-project:` (~20 lines):
   ```makefile
   ## deploy-project: Direct project deploy bypassing CI (emergency fallback)
   ##   Usage: make deploy-project PROJECT=<dir> NODE=<node> [SKIP_VERIFY=1] [DRY_RUN=1]
   ##   Validates PROJECT has ai-platform.yaml, resolves NODE→SSH host, deploys with audit
   deploy-project:
   	@echo "[IMP:7][make][deploy-project] Direct deploy PROJECT=$(PROJECT) NODE=$(NODE)..."
   	@if [[ -z "$(PROJECT)" ]]; then \
   		echo "[IMP:9][make][deploy-project] ERROR: PROJECT not set" >&2; exit 1; \
   	fi
   	@if [[ -z "$(NODE)" ]]; then \
   		echo "[IMP:9][make][deploy-project] ERROR: NODE not set" >&2; exit 1; \
   	fi
   	@$(_platform_root)/core/entrypoints/deploy-project.sh \
   		--project "$(PROJECT)" \
   		--node "$(NODE)" \
   		$(if $(filter 1,$(SKIP_VERIFY)),--skip-verify) \
   		$(if $(filter 1,$(DRY_RUN)),--dry-run)
   	@echo "[IMP:9][make][deploy-project] Direct deploy complete"
   ```

3. **core/AGENTS.md** — новая строка в таблице канонических операций:
   ```
   | `make deploy-project` | Прямой деплой минуя CI | `make deploy-project PROJECT=<dir> NODE=<node>` | `core/entrypoints/deploy-project.sh` → SSH platform-deliver + deploy.sh |
   ```
   И обновить core/ directory structure: добавить `deploy-project.sh` в entrypoints/.

4. **core/entrypoint-manifest.yaml** — новый entry в секции `deploy:`:
   ```yaml
   - make_target: deploy-project
     mechanism: ssh+tar
     delegates_to: core/entrypoints/deploy-project.sh → ssh platform-deliver + ssh deploy.sh → core/internal/deploy/deploy-project.sh
     description: "Direct project deploy bypassing CI (emergency fallback) — tar+ssh deliver, deploy with DEPLOY-DIRECT audit, optional post-deploy verify"
   ```
   Добавить `deploy-project` в `allowed_verbs`.

5. **docs/projects-root-AGENTS.md:36** — обновить секцию «Команды»:
   ```
   **Из `ai-platform/`:** ... `make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой минуя CI, emergency) ...
   ```
   И обновить deploy-модель:
   ```
   git push → CI ... (нормальный путь)
   make deploy-project → tar+ssh → VPS (прямой путь, emergency, аудит DEPLOY-DIRECT)
   ```

6. **deploy-project.sh (internal) main()** — добавить проверку `PLATFORM_DEPLOY_DIRECT`:
   ```bash
   # В main(), после parse_ssh_command, перед audit_log START:
   if [[ "${PLATFORM_DEPLOY_DIRECT:-}" == "1" ]]; then
       local deploy_source="DEPLOY-DIRECT (from ${SSH_CLIENT:-unknown})"
       log_imp 9 "main" "Direct deploy detected — source: ${deploy_source}"
   fi
   ```
   И в audit_log:
   ```bash
   local deploy_tag="${PLATFORM_DEPLOY_DIRECT:+DEPLOY-DIRECT:}platform-deploy:${PROJECT}"
   audit_log "${deploy_tag}" "START" "Deploy ${PROJECT}/${SERVICE_NAME} → ${REF} [source: ${PLATFORM_DEPLOY_DIRECT:+direct}${PLATFORM_DEPLOY_DIRECT:-CI}]"
   ```

**TRAP annotations:**
```bash
# 🧐 TRAP[DECISION] · 2026-07-21 · — · Direct deploy uses same ci-deploy key, not separate key
# · Rejected: separate SSH key for direct deploy (adds key management burden)
# · Reason: ci-deploy already has forced-command restriction — security boundary is the
#   authorized_keys command= prefix, not the key itself. Direct deploy adds audit logging
#   (DEPLOY-DIRECT) but does not expand ci-deploy's permissions.
# · Rev: if direct deploy abuse is detected → consider dedicated key with rate limiting
```

### TASK-T4: Cross-cutting verification & tests

| Поле | Значение |
|------|----------|
| **ID** | TASK-T4 |
| **Priority** | MEDIUM |
| **Dependencies** | TASK-T1, TASK-T2, TASK-T3 |
| **Complexity** | 4/10 |
| **Files** | `tests/test_deploy_direct.py` (NEW), `core/entrypoints/deploy-project.sh` (shellcheck fixes), affected files from T1-T3 (lint fixes) |
| **Owner** | Coder |
| **Output** | Зелёный `make gate MODE=fast`, тесты проходят |
| **Acceptance** | AC-T4.1, AC-T4.2 |

**Changes:**
1. **`make gate MODE=fast`** — запустить, исправить все regression:
    - Manifest integrity gate: проверить что новый entrypoint зарегистрирован
    - Thin wrapper gate: новый entrypoint ≤350 LOC (allowlisted), ≤8 функций
    - Shellcheck: все изменённые .sh файлы
2. **Новые тесты:**
   - `test_deploy_project_validation_no_ai_platform_yaml` — PROJECT без ai-platform.yaml → exit 1
   - `test_deploy_project_validation_no_compose` — PROJECT без docker-compose.yml → exit 1
   - `test_deploy_project_invalid_node` — NODE не в NODE_HOST_MAP → exit 1
   - `test_deliver_with_org` — platform-deliver org project → PROJECT_DIR с org
   - `test_deliver_without_org` — platform-deliver project → PROJECT_DIR без org (backward compat)
3. **`make lint`** — shellcheck для deploy-project.sh (entrypoint) и изменённых участков deploy-project.sh (internal)

---

## 6. Acceptance Criteria (Summary)

| ID | Критерий | Задача | Тип проверки |
|----|----------|--------|-------------|
| AC-T1.1 | `make deploy PROJECT=<git-repo>` → git push origin main | T1 | Manual test |
| AC-T1.2 | `make deploy PROJECT=<не-git>` → exit 1 + error | T1 | Manual test |
| AC-T1.3 | `make deploy PROJECT=<без-remote>` → exit 1 + error | T1 | Manual test |
| AC-T2.1 | `platform-deliver org project` → `/opt/projects/org/project/` | T2 | SSH test / Unit test |
| AC-T2.2 | `platform-deliver project` → `/opt/projects/project/` | T2 | SSH test / Unit test |
| AC-T2.3 | CI workflow передаёт org в platform-deliver | T2 | Code review |
| AC-T3.1 | `make deploy-project PROJECT=<dir> NODE=<node>` → success | T3 | Manual test |
| AC-T3.2 | audit.log запись DEPLOY-DIRECT | T3 | SSH: grep audit.log |
| AC-T3.3 | `make deploy-project ... NODE=<bad>` → exit 1 | T3 | Unit test |
| AC-T3.4 | `make deploy-project ... PROJECT=<no-yaml>` → exit 1 | T3 | Unit test |
| AC-T4.1 | `make gate MODE=fast` зелёный | T4 | CI/pytest |
| AC-T4.2 | `make lint` зелёный (shellcheck) | T4 | CI |

---

## 7. File Manifest

| Файл | T1 | T2 | T3 | T4 | Операция | Сложность |
|------|----|----|----|----|----------|-----------|
| `Makefile` | ✏️ | | ✏️ | | edit: deploy target + новый deploy-project target | MED |
| `core/entrypoints/deploy.sh` | | | | | **БЕЗ изменений** (контракт preserved) | — |
| `core/internal/deploy/deploy-project.sh` | | ✏️ | ✏️ | | edit: handle_deliver + parse_ssh_command + DEPLOY_DIRECT env | HIGH |
| `.github/workflows/deploy-project.yml` | | ✏️ | | | edit: inputs.org + deliver step | LOW |
| `core/internal/bootstrap/node-lifecycle.sh` | | ✏️ | | | edit: step_6b documentation (логика без изменений) | LOW |
| `core/internal/scaffold/add-project.sh` | | ✓ | | | verify: уже org-aware (TRAP comment) | LOW |
| `templates/template-backend/Makefile` | | ✓ | | | verify: PLATFORM_DIR не зависит от org | LOW |
| `templates/template-frontend/Makefile` | | ✓ | | | verify: PLATFORM_DIR не зависит от org | LOW |
| `templates/template-fullstack/Makefile` | | ✓ | | | verify: PLATFORM_DIR не зависит от org | LOW |
| `core/entrypoints/deploy-project.sh` | | | ✨ | | **NEW**: entrypoint для прямого деплоя (~350 lines) | HIGH |
| `core/AGENTS.md` | ✏️ | | ✏️ | | edit: deploy row + deploy-project row + directory structure | LOW |
| `core/entrypoint-manifest.yaml` | ✏️ | | ✏️ | | edit: deploy entry + deploy-project entry + allowed_verbs | LOW |
| `docs/projects-root-AGENTS.md` | | | ✏️ | | edit: команды + deploy-модель | LOW |
| `tests/test_deploy_direct.py` | | | | ✨ | **NEW**: тесты entrypoint валидации + deliver org/backward-compat | MED |

**Легенда:** ✨ = новый файл, ✏️ = редактирование, ✓ = верификация (без изменений)

**Итого: 14 файлов** (2 новых, 8 изменяемых, 4 верифицируемых без изменений)

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/test_deploy_direct.py` | `test_deploy_project_validation_no_ai_platform_yaml` | PROJECT dir exists but ai-platform.yaml missing → exit 1, stderr contains "ai-platform.yaml" | `core/entrypoints/deploy-project.sh` validate_project() |
| `tests/test_deploy_direct.py` | `test_deploy_project_validation_no_compose` | PROJECT dir has ai-platform.yaml but no docker-compose.yml/compose.yaml → exit 1 | `core/entrypoints/deploy-project.sh` validate_project() |
| `tests/test_deploy_direct.py` | `test_deploy_project_validation_success` | PROJECT dir has ai-platform.yaml + docker-compose.yml → exit 0 | `core/entrypoints/deploy-project.sh` validate_project() |
| `tests/test_deploy_direct.py` | `test_extract_org_from_path` | Path `~/projects/myorg/myproject` → org=myorg, name=myproject | `core/entrypoints/deploy-project.sh` extract_org() |
| `tests/test_deploy_direct.py` | `test_extract_org_deep_path` | Path `~/projects/myorg/subgroup/myproject` → org=myorg (first segment after projects/) | `core/entrypoints/deploy-project.sh` extract_org() |
| `tests/test_deploy_direct.py` | `test_deliver_org_project` | `platform-deliver myorg myproject` → PROJECT_DIR contains `myorg/myproject` | `core/internal/deploy/deploy-project.sh` parse_ssh_command() + handle_deliver() |
| `tests/test_deploy_direct.py` | `test_deliver_project_only` | `platform-deliver myproject` → PROJECT_DIR is `myproject` (backward compat) | `core/internal/deploy/deploy-project.sh` parse_ssh_command() + handle_deliver() |
| `tests/test_deploy_direct.py` | `test_deliver_org_validation` | `platform-deliver my/org myproject` → exit 1 (org содержит `/`) | `core/internal/deploy/deploy-project.sh` handle_deliver() |
| `tests/test_gate_manifest_integrity.py` | (existing — auto-discovers) | deploy-project зарегистрирован в manifest.yaml + AGENTS.md | `core/entrypoint-manifest.yaml` |
| `tests/test_gate_thin_wrapper.py` | (existing — auto-discovers + allowlist) | deploy-project.sh ≤350 LOC (allowlisted), ≤8 функций | `core/entrypoints/deploy-project.sh` |

**Test notes:**
- `test_deploy_direct.py` использует `tmp_path` fixture для создания временных PROJECT-директорий
- `test_deliver_*` тесты — unit-тесты на Bash-функции через source + вызов функции. Не требуют Docker или SSH
- Gate-тесты (manifest integrity, thin wrapper) — существующие, авто-обнаруживают новые entrypoint
- Для тестов handle_deliver: мокаем `PROJECTS_BASE` через env, проверяем `PROJECT_DIR` до вызова `mktemp`

---

## 9. $PARALLEL_GROUPS

### Wave 1 (independent, minimal shared files)

**TASK-T1 + TASK-T2** — не пересекаются по файлам:
- TASK-T1: Makefile, core/AGENTS.md, core/entrypoint-manifest.yaml
- TASK-T2: deploy-project.sh (internal), deploy-project.yml, node-lifecycle.sh, add-project.sh, template Makefiles

```
## Wave 1 — parallel
### TASK-T1: Fix phantom make deploy
- Files: Makefile (deploy target), core/AGENTS.md (deploy row), core/entrypoint-manifest.yaml (deploy entry)
- Command: `coder Read DevPlan.md, implement TASK-T1: Fix phantom make deploy`

### TASK-T2: Org-aware paths
- Files: core/internal/deploy/deploy-project.sh, .github/workflows/deploy-project.yml, core/internal/bootstrap/node-lifecycle.sh, core/internal/scaffold/add-project.sh, templates/template-*/Makefile
- Command: `coder Read DevPlan.md, implement TASK-T2: Org-aware paths`
```

### Wave 2 (depends on Wave 1)

**TASK-T3** — зависит от TASK-T2 (org-aware platform-deliver); разделяет файлы с TASK-T1 (Makefile, AGENTS.md, manifest.yaml), но редактирует разные строки.

```
## Wave 2 — sequential (after Wave 1)
### TASK-T3: Direct deploy entrypoint
- Depends on: TASK-T2 (org-aware platform-deliver format)
- Files: NEW core/entrypoints/deploy-project.sh, Makefile (new target), core/AGENTS.md (new row), core/entrypoint-manifest.yaml (new entry), docs/projects-root-AGENTS.md, core/internal/deploy/deploy-project.sh (DEPLOY_DIRECT)
- Command: `coder Read DevPlan.md, implement TASK-T3: Direct deploy entrypoint`
```

### Wave 3 (depends on Wave 1 + Wave 2)

**TASK-T4** — финальная верификация.

```
## Wave 3 — sequential (after Wave 2)
### TASK-T4: Cross-cutting verification & tests
- Depends on: TASK-T1, TASK-T2, TASK-T3
- Files: NEW tests/test_deploy_direct.py, all changed files (lint fixes)
- Command: `coder Read DevPlan.md, implement TASK-T4: Verification & tests`
```

---

## 10. Migration Strategy (T2 — org-aware paths)

### Phase 1: Deploy updated code (core + CI workflow)
- Обновить `deploy-project.sh` (internal) с backward compat
- Обновить `deploy-project.yml` с поддержкой org
- Обновить `node-lifecycle.sh` (документация)
- Результат: VPS принимает оба формата — старый (без org) и новый (с org)

### Phase 2: Enable org in CI for новых проектов
- Новые проекты (созданные после обновления) автоматически передают org в CI workflow
- Существующие проекты продолжают работать без org (backward compat)

### Phase 3: Миграция существующих проектов (опционально, manual)
- Для миграции существующего проекта: добавить `org` input в project CI workflow call
- На VPS: файлы остаются в `/opt/projects/<name>/` — работает через backward compat
- При желании перенести: platform-deliver с org на следующем CI-деплое → создаст `/opt/projects/<org>/<name>/`
- Старая директория `/opt/projects/<name>/` остаётся (ручное удаление при необходимости)

### Rollback plan
- Если новый формат вызывает проблемы: CI workflow возвращается к старому формату (1 аргумент)
- deploy-project.sh на VPS сохраняет backward compat — старые вызовы продолжают работать
- Откат core-кода через git revert

---

## 11. Risk Assessment

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| **T1: git push без валидации ломает CI** | Низкая | MEDIUM | Валидация .git + remote origin + uncommitted changes warning |
| **T2: Обратная несовместимость** | Средняя | HIGH | Backward compat через подсчёт аргументов; CI workflow обновляется атомарно с VPS-кодом |
| **T2: Двойной пробел в CI (org пустой)** | Средняя | MEDIUM | `xargs` в parse_ssh_command схлопывает множественные пробелы; альтернативно — conditional в CI workflow |
| **T3: SSH-доступ с dev-машины** | Низкая | LOW | Используется существующий ci-deploy ключ (уже есть у разработчика для bootstrap) |
| **T3: Безопасность прямого деплоя (обход CI gate)** | Средняя | MEDIUM | Аудит-лог DEPLOY-DIRECT; ci-deploy forced-command ограничивает поверхность атаки; документация рекомендует `make gate MODE=fast` перед прямым деплоем |
| **T3: NODE_HOST_MAP отсутствует на dev-машине** | Средняя | MEDIUM | Документировать необходимость установки env-переменной; проверка с понятным сообщением |
| **T4: Gate regression от изменений** | Низкая | LOW | Изменения минимальны (entrypoint-manifest обновляется, а не переписывается); gate-тесты авто-обнаруживают новые entrypoint |

---

## 12. Debt Intake

Аудит существующих TRAP[DEBT] в affected modules:

| Source | Finding | Classification | Action |
|--------|---------|---------------|--------|
| `deploy-project.sh:38` | `@changes 2026-07-18 · T3.1/B1` | DEFER — не связан с текущей задачей | Без действий |
| `deploy-project.sh:40-50` | `TRAP[BUG] B1 — Deploy reports 'failed' despite success` | DEFER — исправлен, историческая запись | Без действий |
| `deploy-project.sh:409-417` | `TRAP[BUG] · 2026-07-20 · raw="deploy.sh dance-site <sha>" → PROJECT=deploy.sh` | IN_SCOPE — связан с parse_ssh_command(), который изменяется в T2 | Учесть при модификации parse_ssh_command: не сломать существующий fix |
| `node-lifecycle.sh:240-243` | `TRAP[DECISION] · 2026-07-17 · Shared bridges.txt` | DEFER — не связан с T2 | Без действий |

**IN_SCOPE action:** При модификации `parse_ssh_command()` в T2, сохранить существующий prefix-stripping logic (lines 413-417) для deploy.sh entrypoint path. Новый dispatch для platform-deliver с org добавляется ДО существующей логики, не заменяет её.

---

## 13. Configuration Consistency

### DRY-check: дублирующиеся значения

| Значение | Где используется | Статус |
|----------|-----------------|--------|
| `PROJECTS_BASE=/opt/projects` | `deploy-project.sh:57`, `node-lifecycle.sh:347` | ✅ Единый source: `deploy-project.sh:57` (readonly default), `node-lifecycle.sh` ссылается на тот же путь |
| `MAX_WAIT_SEC=60` | `deploy-project.sh:58` | ✅ Единый source |
| `AUDIT_LOG=/var/log/platform/audit.log` | `deploy-project.sh:60` | ✅ Единый source |
| `ci-deploy` SSH user | `deploy-project.yml:83`, `node-lifecycle.sh:316`, `deploy-project.sh (entrypoint, new)` | ✅ Единый user во всех местах |
| `NODE_HOST_MAP` | `node-resolver.sh`, `deploy-project.yml:49`, `deploy-project.sh (entrypoint, new)` | ✅ Единый source: `node-resolver.sh:246` (canonical resolver) |

### Новые конфигурационные значения (T3)

| Значение | Определение | Все файлы |
|----------|------------|-----------|
| `PLATFORM_DEPLOY_DIRECT=1` | Env-переменная для маркировки прямого деплоя | `deploy-project.sh (entrypoint)` — устанавливает, `deploy-project.sh (internal)` — читает |

---

## 14. Contract Formalization

### Entrypoint contract: `core/entrypoints/deploy-project.sh`

```yaml
entrypoint: deploy-project.sh
layer: entrypoints
purpose: Direct project deploy bypassing CI (emergency fallback)
arguments:
  - --project PATH    # required: project directory with ai-platform.yaml
  - --node NAME       # required: target node name
  - --skip-verify     # optional: skip post-deploy verify step
  - --dry-run         # optional: print plan without executing
env_requires:
  - NODE_HOST_MAP     # JSON map of node → SSH host (same as CI)
  - (optional) PLATFORM_CI_DEPLOY_KEY_FILE  # path to SSH key (default: ~/.ssh/ci_deploy_key)
side_effects:
  - SSH to ci-deploy@<host> (2-3 connections: deliver, deploy, [verify])
  - Writes to /var/log/platform/audit.log on VPS (via deploy-project.sh internal)
exit_codes:
  - 0: success
  - 1: validation error (missing files, invalid node)
  - 2: SSH connection error
  - 3: deploy failed (check VPS logs)
```

### Modified contract: `handle_deliver()` in `deploy-project.sh`

```yaml
function: handle_deliver
signature: handle_deliver(project: str, org: str = "")
contract_change: |
  До T2:    handle_deliver(project)
  После T2: handle_deliver(project, org="")
  org пустой → поведение идентично старому (backward compat)
  org непустой → project_dir = ${PROJECTS_BASE}/${org}/${project}
validation: _validate_project_name() вызывается для project (не для org);
            org валидируется в parse_ssh_command (без пробелов, без /, без ..)
```

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/021-deploy-direct-hotfix/02-DevPlan.md, implement Wave 1: TASK-T1 (Fix phantom make deploy) + TASK-T2 (Org-aware paths)
```

### Wave 2
```
coder Read .ai/plans/021-deploy-direct-hotfix/02-DevPlan.md, implement Wave 2: TASK-T3 (Direct deploy entrypoint)
```

### Wave 3
```
coder Read .ai/plans/021-deploy-direct-hotfix/02-DevPlan.md, implement Wave 3: TASK-T4 (Verification & tests)
```

$END_DEVPLAN
