$START_BRIEF
# $ARTIFACT_CONTRACT

| Field | Value |
|-------|-------|
| **PURPOSE** | Системная модель подключения проектов к платформе: одна команда, минимальный дрейф файлов, контракт окружения для AI-агентов, поддержка личных и wildcard-доменов |
| **DESCRIPTION** | Решает 4 взаимосвязанные проблемы: (1) CI/CD проектов падает из-за отсутствующего `NODE_CONFIGS_TOKEN`, (2) шаблоны копируют весь CI workflow в каждый проект — апгрейд платформы требует ручного обновления 10+ проектов, (3) AI-агент в папке проекта не знает, какие сервисы предоставляет платформа, (4) управление доменами (личными, third-level, wildcard) разрознено |
| **RATIONALE** | Текущий дрейф: секретная модель сломана (токен не создан), CI-код дублирован (deploy.yml в каждом проекте), контракт окружения отсутствует. Системное решение: GITHUB_TOKEN (ноль секретов), reusable workflow (одна точка обновления), `.env.platform` (декларативный контракт), авто-генерация доменов third-level |
| **ACCEPTANCE_CRITERIA** | AC1: `make new-project` создаёт полностью готовый к деплою проект за одну команду. AC2: Проект содержит ≤5 платформенных файлов (ai-platform.yaml, Dockerfile, docker-compose.yml, .env.platform, nginx/default.conf). AC3: CI проекта проходит без ручной настройки секретов (GITHUB_TOKEN). AC4: AI-агент в папке проекта одной командой (`grep PLATFORM_ .env.platform`) получает полную информацию о доступных сервисах. AC5: Домен генерируется автоматически (`<project>.tronyx.ru`) если не указан явно, или используется указанный личный домен |
| **IMPLEMENTS** | AGENTS.md § deploy-model, invariants 1-3; platform-env.yaml как SoT для контракта окружения |
| **IMPACTS** | `core/entrypoints/scaffold.sh`, `core/internal/scaffold/add-project.sh`, `core/internal/scaffold/add-vhost.sh`, `templates/template-*/`, `.github/workflows/` (новый reusable), `core/internal/scaffold/gen-env-platform.sh` (новый), `platform-env.yaml` |
| **REQUIRES** | Доступ на запись в `TronyxLab/AI-platform` для публикации reusable workflow; `PLATFORM_DOMAIN` в `.env` |

$END_ARTIFACT_CONTRACT

---

## Source

> Пользователь: «Я сейчас работал в репозитории подключенного проекта — и у меня случилась вот такая проблема, проведи мозговой штурм, как решить ее системно и настроить подключение проектов (в том числе со своими личными доменами, в том числе и ваилдкард). Еще есть требование — надо чтобы подключение проекта — делалось одной командой, создавалась папка по шаблону, настраивался весь процесс (мы такое делали), чтобы файлы проекта требовалось минимально обновлять в случае обновления кода платформы, а то проектов планируется десять+, не хочется потом по долгу каждый из них обновлять. И в каждый проект надо класть (может ссылкой на файл в репо платформы?) максимально короткий файл с информацией о доступном окружении и шаред модулях, чтобы разрабатывая с ии агентом в папке проекта — он знал, что постгрес не надо устанавливать, он есть от платформы. Может делать это как-то через говорящие имена ENV переменных передаваемых в контейнер, чтобы они не устаревали и не дрейфовали в процессе активной разработки.»

## Clarifications

| # | Вопрос | Ответ |
|---|--------|-------|
| C1 | Модель секретов для доступа к конфигам | **GITHUB_TOKEN** — использовать авто-токен `github.token`, ноль секретов |
| C2 | Расположение node-configs | `/Users/tronyx/projects/tronyx-lab/platform/node-configs/` = часть `TronyxLab/AI-platform` (зеркала). В source-репозитории `node-configs/` пустая — конфиги коммитятся напрямую в зеркало |
| C3 | Модель доменов | Домен может быть: личный (`myapp.com`), third-level от платформы (`myapp.tronyx.ru`), или авто-сгенерированный (`<project>.tronyx.ru` если не указан) |

## Decisions

### D1: GITHUB_TOKEN вместо NODE_CONFIGS_TOKEN
**@rationale** Q: Почему не просто создать недостающий секрет? A: Потому что отсутствие секрета — симптом архитектурной проблемы. `GITHUB_TOKEN` уже существует, уже имеет `contents: read` на все репо в TronyxLab, и автоматически ротируется GitHub. Ноль секретов = ноль человеческого фактора. Проект и платформа в одной организации → GITHUB_TOKEN достаточно.

### D2: Reusable workflow вместо копирования CI в каждый проект
**@rationale** Q: Почему не оставить deploy.yml в шаблоне? A: Каждое изменение платформенного CI требует ручного обновления 10+ проектов. Reusable workflow в `TronyxLab/AI-platform/.github/workflows/deploy-project.yml` — одна точка обновления. Проект содержит только `ai-platform.yaml` + `Dockerfile` + код приложения + `.env.platform`.

### D3: `.env.platform` как контракт окружения
**@rationale** Q: Почему `.env` а не YAML/JSON/labels? A: Формат `.env` понимают все: люди, AI-агенты (`grep PLATFORM_`), docker compose (`env_file`), shell-скрипты (`source`). Генерируется из `platform-env.yaml` (единственный источник правды) при создании проекта. Не устаревает: при изменении платформы — регенерируется через `make project-sync-env`.

### D4: Авто-генерация домена третьего уровня
**@rationale** Q: Почему не требовать домен всегда? A: Не все проекты имеют свой домен на старте. Авто-генерация `<project-name>.<PLATFORM_DOMAIN>` даёт immediately-working URL. Wildcard-сертификат `*.tronyx.ru` покрывает все авто-сгенерированные домены без дополнительных действий. Личный домен — опция, не требование.

## Scope

**Включено:**
- CI/CD: миграция `resolve-node` на `GITHUB_TOKEN`, устранение `NODE_CONFIGS_TOKEN`
- Шаблоны: создание reusable workflow `deploy-project.yml` в платформе, упрощение проектных шаблонов
- Контракт: генератор `.env.platform` из `platform-env.yaml`
- Домены: авто-генерация third-level, поддержка личных доменов, интеграция с `add-vhost.sh`
- Scaffold: единая команда `make new-project` с полным циклом

**Исключено:**
- DNS-автоматизация через API регистратора (отдельная задача)
- Миграция существующих проектов (отдельная задача — `make project-migrate`)
- Изменение модели деплоя платформы (rsync/ssh) — остаётся как есть

## Severity

**CRITICAL** — блокирует деплой всех проектов (NODE_CONFIGS_TOKEN). Затрагивает архитектурный контракт между платформой и проектами. Каждый день без решения = проекты не могут деплоиться.

---

## Draft Architecture

```
┌─ make new-project ─────────────────────────────────────────────┐
│  NAME=myapp TEMPLATE=frontend [--domain myapp.com]              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 1. copy_template(template-frontend → ~/projects/org/)    │   │
│  │ 2. replace_placeholders(__NAME__, __DOMAIN__, __ORG__)   │   │
│  │ 3. generate_ai_platform_yaml(needs, monitoring)          │   │
│  │ 4. generate_env_platform(PLATFORM_PROVIDES, DSNs)        │   │
│  │ 5. generate_domain:                                      │   │
│  │    ├─ --domain set → use as-is                           │   │
│  │    └─ not set → auto: <project>.<PLATFORM_DOMAIN>        │   │
│  │ 6. add_vhost(domain) → overlays/nginx/<fqdn>.conf       │   │
│  │ 7. git_init + create_github_repo + push                  │   │
│  └─────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
                           ↓
┌─ Project repo structure (minimal) ─────────────────────────────┐
│  ai-platform.yaml        # декларация (name, type, domain)     │
│  .env.platform           # контракт окружения (PLATFORM_*)     │
│  Dockerfile              # сборка приложения                    │
│  docker-compose.yml      # сервис + сети (proxy-net, etc.)     │
│  nginx/default.conf      # SPA routing + /health               │
│  src/                    # код приложения                       │
│  .github/workflows/      # ТОЛЬКО вызов reusable workflow      │
│    deploy.yml            # 15 строк: uses: TronyxLab/...       │
└────────────────────────────────────────────────────────────────┘
                           ↓ git push
┌─ CI (reusable workflow в TronyxLab/AI-platform) ───────────────┐
│  on: workflow_call                                              │
│  jobs:                                                          │
│    resolve-node:                                                │
│      - uses: actions/checkout@v7  # текущий проект              │
│      - name: Clone platform configs                             │
│        uses: actions/checkout@v7                                │
│        with:                                                    │
│          repository: TronyxLab/AI-platform                      │
│          token: ${{ github.token }}  # ← НОЛЬ СЕКРЕТОВ         │
│          path: /tmp/platform                                    │
│      - name: Read node.yaml                                     │
│        run: python3 -c "..." /tmp/platform/node-configs/...    │
│    build-image: docker/build-push-action                        │
│    deploy: appleboy/ssh-action                                  │
└────────────────────────────────────────────────────────────────┘
```

## Data Flow: `.env.platform` generation

```
platform-env.yaml (SoT)
    │
    ├─ networks: [proxy-net, shared-db-net, ...]
    ├─ port_mappings: {POSTGRES_PORT: 6432, REDIS_PORT: 6379, ...}
    ├─ proxy: {no_proxy_internal: "..."}
    └─ profiles: [postgres, redis, litellm, ...]
         │
         ▼
gen-env-platform.sh
    │  input: platform-env.yaml, project ai-platform.yaml
    │  output: .env.platform
         │
         ▼
.env.platform (в корне проекта)
    # GENERATED by ai-platform — DO NOT EDIT
    # Source: platform-env.yaml + ai-platform.yaml
    PLATFORM_DOMAIN=tronyx.ru
    PLATFORM_PROVIDES=postgres,redis,litellm,langfuse,minio,clickhouse,nginx-proxy
    PLATFORM_POSTGRES_HOST=postgres
    PLATFORM_POSTGRES_PORT=6432
    PLATFORM_POSTGRES_DSN=postgresql://myapp_user:***@postgres:6432/myapp_db
    PLATFORM_REDIS_URL=redis://redis:6379
    PLATFORM_LITELLM_URL=http://litellm:4000
    PLATFORM_LANGFUSE_URL=http://langfuse:3001
    PLATFORM_PROXY_NET=proxy-net
    PLATFORM_SHARED_DB_NET=shared-db-net
    PLATFORM_NO_PROXY=localhost,127.0.0.1,.local,postgres,redis
```

## Acceptance Criteria Summary

| AC | Критерий | Проверка |
|----|----------|----------|
| AC1 | `make new-project NAME=test TEMPLATE=frontend` → полностью готовый проект | Ручное тестирование: scaffold → git push → CI green |
| AC2 | Проект содержит ≤5 платформенных файлов | `find project/ -name "*.yml" -o -name "*.yaml" -o -name ".env*" \| wc -l` |
| AC3 | CI проекта проходит без ручной настройки секретов | CI log: `GH_TOKEN: ***` (github.token, не NODE_CONFIGS_TOKEN) |
| AC4 | `grep PLATFORM_ .env.platform` возвращает ≥8 строк с информацией о сервисах | Авто-тест: `test_env_platform_has_required_vars` |
| AC5 | Домен авто-генерируется `<project>.tronyx.ru` если не указан | `make new-project NAME=foo` → в `ai-platform.yaml` домен = `foo.tronyx.ru` |
| AC6 | Личный домен работает: `make new-project --domain myapp.com` → vhost + cert | Ручное: деплой → curl https://myapp.com/health |
| AC7 | Обновление платформы не требует изменения файлов проектов | Изменить reusable workflow → все проекты автоматически используют новую версию |

## File Manifest

| Файл | Действие | Назначение |
|------|----------|-----------|
| `.github/workflows/deploy-project.yml` | **NEW** | Reusable workflow деплоя проекта (в TronyxLab/AI-platform) |
| `templates/template-*/.github/workflows/deploy.yml` | **SIMPLIFY** | 15-строчный файл, вызывает reusable workflow |
| `templates/template-*/ai-platform.yaml` | **UPDATE** | Добавить `platform_domain` авто-генерацию |
| `templates/template-*/.env.platform` | **NEW** | Генерируется при scaffold, в шаблоне — placeholder |
| `core/internal/scaffold/gen-env-platform.sh` | **NEW** | Генератор `.env.platform` из `platform-env.yaml` |
| `core/internal/scaffold/add-project.sh` | **UPDATE** | Интеграция gen-env-platform.sh, авто-домен, упрощение CI |
| `core/internal/scaffold/add-vhost.sh` | **UPDATE** | Поддержка third-level доменов (подпадают под wildcard → не требуют отдельного сертификата) |
| `core/entrypoints/scaffold.sh` | **UPDATE** | Новый подкоманда `sync-env` для обновления `.env.platform` |
| `platform-env.yaml` | **UPDATE** | Добавить секцию `provides:` для gen-env-platform.sh |
| `Makefile` | **UPDATE** | Новый target `project-sync-env`, обновлённый `new-project` |
| `core/lib/node-resolver.sh` | **UPDATE** | Поддержка разрешения node.yaml из checkout (CI-контекст) |
| `templates/template-*/docker-compose.yml` | **UPDATE** | `env_file: .env.platform` для автоматической загрузки переменных |
| `templates/template-*/README.md` | **UPDATE** | Документация нового процесса |
| `tests/test_scaffold_env_platform.py` | **NEW** | Тесты генерации `.env.platform` |
| `tests/test_project_ci_contract.py` | **NEW** | Тесты контракта CI (reusable workflow вызывает правильные шаги) |

## Next Steps

→ Ожидаю подтверждения Brief (CONFIRM_BRIEF), затем создаю DevPlan с $TASKS + $PARALLEL_GROUPS.

$END_BRIEF
