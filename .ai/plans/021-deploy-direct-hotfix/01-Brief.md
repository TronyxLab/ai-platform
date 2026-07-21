# GREP_SUMMARY: brief 021 deploy-direct-hotfix make-deploy phantom path-asymmetry org platform-deliver deploy-project direct CI-bypass audit

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть три gap в модели деплоя проектов: (1) фантомный `make deploy` не делает заявленный git push, (2) org теряется при доставке на VPS — асимметрия путей dev/VPS, (3) нет прямого деплоя минуя CI для экстренных случаев.
DESCRIPTION:           Три задачи: T1 — исправить таргет `make deploy` в корневом Makefile так, чтобы он делал реальный git push из папки проекта (сейчас вызывает SSH-entrypoint deploy.sh, что ломается при локальном запуске); T2 — добавить org в verb `platform-deliver` и путь `/opt/projects/<org>/<name>/` на VPS, устранив асимметрию `~/projects/<org>/<name>/` ↔ `/opt/projects/<name>/`; T3 — реализовать `make deploy-project` для прямого деплоя tar+ssh на указанную ноду (emergency fallback, минуя CI, с обязательным аудит-логом).
RATIONALE:             T1: комментарий в Makefile:436 обещает «git push → CI → forced-command», но код вызывает `deploy.sh` — SSH-entrypoint, не делающий git push. Разработчик, вызвавший `make deploy PROJECT=<dir>`, получает либо ошибку, либо (в худшем случае) локальный docker compose. T2: на dev-машине проекты лежат в `~/projects/<org>/<name>/`, на VPS — в `/opt/projects/<name>/`. Org-префикс теряется в `handle_deliver()` deploy-project.sh:232, что при росте числа org/проектов на одном VPS создаст коллизию имён. T3: сейчас единственный путь деплоя проекта — `git push`. Если GitHub Actions недоступен или нужен срочный деплой (20:00 UTC → CI queue 10 мин), разработчик вынужден делать ручной scp/tar — платформа не предоставляет канонического инструмента.
ACCEPTANCE_CRITERIA:
  AC-T1: `make deploy PROJECT=~/projects/<org>/<name>` из корня ai-platform → `cd <project_dir> && git push origin main` → exit 0 при успехе, exit 1 с диагностикой при отсутствии git remote / uncommitted changes.
  AC-T2: `platform-deliver <org> <project>` на VPS → payload извлекается в `/opt/projects/<org>/<name>/`. deploy-project.sh использует org+project во всех путях. Обратная совместимость: старый формат `platform-deliver <project>` (без org) продолжает работать (path: `/opt/projects/<project>/`).
  AC-T3: `make deploy-project PROJECT=<dir> NODE=<node>` → tar czf ai-platform.yaml docker-compose.yml .env.platform → ssh forced-command `platform-deliver <org> <project>` → аудит-лог с пометкой DEPLOY-DIRECT. Healthcheck после деплоя. Валидация: NODE присутствует в NODE_HOST_MAP, PROJECT содержит ai-platform.yaml.
IMPLEMENTS:            Запрос владельца от 2026-07-21 — «Создай бриф по задачам: фантомный make deploy, асимметрия путей, нет прямого деплоя».
IMPACTS:               Makefile (deploy target + новый deploy-project target), core/internal/deploy/deploy-project.sh (handle_deliver сигнатура, parse_ssh_command), core/entrypoints/deploy.sh (verb dispatch), .github/workflows/deploy-project.yml (передача org в platform-deliver), templates/template-*/Makefile (PLATFORM_DIR path), docs/projects-root-AGENTS.md, core/internal/scaffold/add-project.sh (новый org-aware путь), core/internal/bootstrap/node-lifecycle.sh (создание /opt/projects/<org>/).
REQUIRES:              Ветка от origin/main, make gate MODE=fast зелёный, working tree чистый. Понимание текущего контракта platform-deliver verb (D2, DevPlan 007).
$END_ARTIFACT_CONTRACT

---

## 1. Контекст

Текущая модель деплоя проектов:

```
~/projects/<org>/<name>/          (dev-машина)
    ↓ git push
CI проекта → reusable workflow    (GitHub Actions)
    ↓ tar + SSH "platform-deliver <name>"
/opt/projects/<name>/             (VPS)
    ↓ SSH "deploy.sh <name> <sha>"
docker compose pull → up → healthcheck
```

Анализ кода (`deploy.sh`, `deploy-project.sh`, `deploy-project.yml`, `add-project.sh`, `Makefile`) выявил три проблемы, не покрытые существующими планами (019, 020).

### Связанные артефакты

| Артефакт | Связь |
|----------|-------|
| DevPlan 007 (dance-site-launch) | D2: verb `platform-deliver` — оригинальный контракт |
| DevPlan 020 (deploy-pipeline-hardening) | D2: удаление поля `context` из ai-platform.yaml — смежная, не дублируется |
| DevPlan 019 (deploy-pipeline-gaps) | S2: доставка vhost-файлов — смежная, не дублируется |

---

## 2. Проблемы

### T1. Фантомный `make deploy`

**Файл**: `Makefile:433-443`

```makefile
## deploy: Deploy project via CI pipeline
##   Usage: make deploy PROJECT=<dir> [ENV=...] [BRANCH=...]
##   Delegates to core/entrypoints/deploy.sh → git push → CI → forced-command
deploy:
    @$(_platform_root)/core/entrypoints/deploy.sh "$(PROJECT)"
```

**Проблема**: комментарий обещает «git push → CI → forced-command», но код вызывает `deploy.sh` — SSH forced-command entrypoint. `deploy.sh` не делает git push (grep подтвердил: 0 вхождений `git push`). При локальном вызове:

1. `deploy.sh "$PROJECT"` — парсит как `SSH_ORIGINAL_COMMAND=""`, fallback → CLI args
2. В `parse_verb()`: `first_token` = путь к проекту (не `remove`/`status`/`verify`) → fallthrough в `*` case
3. `exec deploy-project.sh <путь>` — пытается выполнить docker compose локально

**Корневая причина**: `deploy.sh` спроектирован для VPS-стороны (SSH forced-command), не для локального запуска. Таргет `make deploy` — документационный артефакт, оставшийся от ранней версии модели.

**Варианты исправления**:
- **A (рекомендовано)**: Реализовать `cd $(PROJECT) && git push origin main` с валидацией (есть git remote, нет uncommitted изменений). Комментарий остаётся корректным.
- **B**: Исправить только комментарий, указав реальный путь деплоя («из папки проекта: `git push`»). Минимальное изменение.
- **C (отклонено)**: Сделать `deploy.sh` способным к локальному запуску — нарушает single-responsibility (entrypoint только для SSH forced-command, контракт `deploy.sh:10`).

### T2. Асимметрия путей dev ↔ VPS

**Файлы**: `deploy-project.sh:232` (`handle_deliver`), `deploy-project.yml:83` (CI workflow), `add-project.sh:31` (PROJECTS_ROOT)

| Среда | Путь | Источник |
|-------|------|----------|
| Dev (macOS) | `~/projects/<org>/<name>/` | `add-project.sh:31`: `PROJECTS_ROOT=$(dirname $PLATFORM_ROOT)` |
| VPS | `/opt/projects/<name>/` | `deploy-project.sh:57`: `PROJECTS_BASE=/opt/projects` |

Org теряется в `handle_deliver()`:

```bash
# deploy-project.sh:232
handle_deliver() {
    local project="$1"                              # ← только имя, без org
    local project_dir="${PROJECTS_BASE}/${project}"  # → /opt/projects/<name>
```

И в CI workflow:

```yaml
# deploy-project.yml:83
tar czf - $FILES | ssh ci-deploy@host "platform-deliver ${{ inputs.project_name }}"
```

`project_name` — только имя проекта (например, `dance-site`), без org.

**Риск**: при размещении на одном VPS проектов из разных org с одинаковым именем — коллизия, перезапись файлов.

**Варианты исправления**:
- **A (рекомендовано)**: Добавить org в verb: `platform-deliver <org> <project>`. Путь на VPS: `/opt/projects/<org>/<name>/`. Обратная совместимость через fallback (если один аргумент — старый формат).
- **B**: Валидировать уникальность имени проекта в рамках VPS на этапе FQDN-check (уже существующий `validate.sh --check-fqdn` в `deploy-project.sh:1006`). Без изменения путей.
- **C (отклонено)**: Хранить org в ai-platform.yaml — противоречит DevPlan 020, который удаляет поле `context` (аналог org) из ai-platform.yaml.

### T3. Отсутствие прямого деплоя минуя CI

**Текущее состояние**: единственный путь деплоя проекта — `git push` → GitHub Actions. Для платформенного кода есть прямой путь (`make bootstrap-node`, `make node-update` через SCP/SSH), для проектов — нет.

**Сценарии, где это проблема**:
- GitHub Actions недоступен (outage, rate limit)
- Срочный деплой (prod fix), а CI queue занята
- Разработчик хочет задеплоить с локальной машины без push в git

**Текущий workaround разработчика** (неканонический):
```bash
tar czf - ai-platform.yaml docker-compose.yml .env.platform | \
  ssh ci-deploy@vps "platform-deliver myproject"
ssh ci-deploy@vps "/opt/platform/core/entrypoints/deploy.sh myproject $(git rev-parse HEAD) production"
```

Это ровно то, что делает CI, но вручную. Платформа не предоставляет `make`-таргет для этого.

**Варианты исправления**:
- **A (рекомендовано)**: `make deploy-project PROJECT=<dir> NODE=<node>` — обёртка над tar+ssh, с валидацией (NODE в NODE_HOST_MAP, PROJECT содержит ai-platform.yaml), аудит-логом с пометкой `DEPLOY-DIRECT`, и healthcheck после деплоя.
- **B**: Добавить `make deploy-project PROJECT=<dir>` без указания NODE (auto-resolution из ai-platform.yaml). Меньше аргументов, но менее явно.

---

## 3. Design Decisions (предварительные)

### D1 — T1: `make deploy` = git push wrapper

```bash
# Makefile deploy target (after fix)
deploy:
    @# Validate PROJECT is a directory with git remote
    @if [[ ! -d "$(PROJECT)/.git" ]]; then
        echo "ERROR: $(PROJECT) is not a git repository"
        exit 1
    fi
    @cd "$(PROJECT)" && git push origin main
```

**Superposition**:
- S1: `git push origin main` (просто, одна команда) — **выбран**
- S2: `git push origin $(git branch --show-current)` (push текущей ветки)
- S3: полный `deploy.sh` refactor для локального режима (сложно, ломает контракт)

### D2 — T2: org-aware platform-deliver

```
Было:  platform-deliver <project>
Стало: platform-deliver <org> <project>
       platform-deliver <project>          ← обратная совместимость (без org)
```

Путь на VPS: `/opt/projects/<org>/<name>/` (с org) или `/opt/projects/<name>/` (без org, fallback).

**Изменяемые файлы**:
1. `deploy-project.sh:232` — `handle_deliver()` сигнатура
2. `deploy-project.sh:428-440` — `parse_ssh_command()` dispatch platform-deliver
3. `.github/workflows/deploy-project.yml:83` — передача org+project
4. `core/internal/bootstrap/node-lifecycle.sh:340-362` — `mkdir -p /opt/projects/<org>/`
5. `core/internal/scaffold/add-project.sh` — новый путь (уже использует org)
6. `templates/template-*/Makefile` — если ссылаются на путь

### D3 — T3: `make deploy-project`

```makefile
deploy-project:
    @# Usage: make deploy-project PROJECT=<dir> NODE=<node>
    @# Validates PROJECT has ai-platform.yaml + docker-compose.yml
    @# Resolves NODE → SSH host via NODE_HOST_MAP
    @# tar + ssh platform-deliver → ssh deploy.sh → audit
```

**Варианты**:
- **A**: `make deploy-project PROJECT=<dir> NODE=<node>` — явный NODE (рекомендовано)
- **B**: auto-resolution NODE из ai-platform.yaml → меньше аргументов
- **C**: delegate в новый `core/entrypoints/deploy-project.sh` (отдельный от `deploy.sh`)

---

## 4. Data Flow (целевой)

### T1+T3 — полный цикл деплоя (после фиксов)

```
┌─ Нормальный путь (CI) ─────────────────────────────────────┐
│  cd ~/projects/<org>/<project> && git push origin main       │
│    → CI → reusable workflow → platform-deliver → deploy.sh  │
└────────────────────────────────────────────────────────────┘

┌─ Прямой путь (emergency) ──────────────────────────────────┐
│  make deploy-project PROJECT=~/projects/<org>/<name> \       │
│    NODE=tronyx-vps                                           │
│    → resolve NODE → SSH host                                 │
│    → tar → ssh "platform-deliver <org> <name>"              │
│    → ssh "deploy.sh <name> <sha> production"                │
│    → audit log: DEPLOY-DIRECT                                │
└────────────────────────────────────────────────────────────┘

┌─ make deploy (обёртка git push) ───────────────────────────┐
│  make deploy PROJECT=~/projects/<org>/<name>                 │
│    → cd <dir> && git push origin main                        │
│    → (далее — нормальный CI-путь)                            │
└────────────────────────────────────────────────────────────┘
```

### T2 — org-aware пути

```
Dev:        ~/projects/<org>/<name>/
              ↓ platform-deliver <org> <name>
VPS:        /opt/projects/<org>/<name>/
              ↓ deploy.sh <name> <sha>
            docker compose up -d
```

---

## 5. Acceptance Criteria (измеримые)

| ID | Критерий | Проверка |
|----|----------|----------|
| AC-T1.1 | `make deploy PROJECT=<git-repo-dir>` → `git push origin main` выполнен | Ручной тест с тестовым репо |
| AC-T1.2 | `make deploy PROJECT=<не-git-директория>` → exit 1, сообщение об ошибке | Ручной тест |
| AC-T2.1 | `platform-deliver myorg myproject` → `/opt/projects/myorg/myproject/` создан | SSH-тест на VPS |
| AC-T2.2 | `platform-deliver myproject` (старый формат) → `/opt/projects/myproject/` (backward compat) | SSH-тест на VPS |
| AC-T2.3 | deploy-project.sh использует `PROJECTS_BASE/<org>/<name>` после T2 | grep по коду |
| AC-T3.1 | `make deploy-project PROJECT=<dir> NODE=<node>` → успешный деплой на VPS | Ручной тест |
| AC-T3.2 | В audit.log запись с типом `DEPLOY-DIRECT` | grep audit.log после теста |
| AC-T3.3 | `make deploy-project PROJECT=<dir> NODE=<nonexistent>` → exit 1 с диагностикой | Ручной тест |
| AC-T3.4 | `make gate MODE=fast` зелёный после всех изменений | CI/pytest |

---

## 6. Файлы (предварительный манифест)

| Файл | Задача | Операция |
|------|--------|----------|
| `Makefile` | T1, T3 | edit: deploy target + новый deploy-project target |
| `core/entrypoints/deploy.sh` | T2 | edit: dispatch platform-deliver с org |
| `core/internal/deploy/deploy-project.sh` | T2 | edit: handle_deliver сигнатура, parse_ssh_command |
| `.github/workflows/deploy-project.yml` | T2 | edit: передача org в platform-deliver |
| `core/internal/bootstrap/node-lifecycle.sh` | T2 | edit: mkdir /opt/projects/<org>/ |
| `core/entrypoints/deploy-project.sh` | T3 | new: entrypoint для прямого деплоя |
| `docs/projects-root-AGENTS.md` | T1, T3 | edit: документировать deploy + deploy-project |
| `core/AGENTS.md` | T1, T3 | edit: каноническая таблица операций |
| `core/entrypoint-manifest.yaml` | T3 | edit: регистрация deploy-project |

---

## 7. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Обратная несовместимость T2 | Средняя | HIGH — сломает деплой существующих проектов | Fallback на старый формат без org; тесты backward compat |
| T2 требует миграции существующих проектов на VPS | Высокая | MEDIUM — ручная работа | Авто-миграция при первом деплое после обновления |
| T3 требует SSH-доступа с dev-машины | Низкая | LOW — уже есть для bootstrap | Использовать существующий `ci-deploy` ключ |
| T3: безопасность прямого деплоя | Средняя | MEDIUM — обход CI gate | Аудит-лог DEPLOY-DIRECT; обязательный `make gate MODE=fast` перед прямым деплоем |

$END_BRIEF
