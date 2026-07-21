# GREP_SUMMARY: projects-root, AGENTS.md, ai-platform, контракт проекта, контексты, deploy-model, команды
# STRUCTURE: ▶ структура ~/projects/ → ◇ контракт платформы (5 правил) → ⊕ команды → ⚡ deploy-модель → ⎋ приоритет инструкций
# region MODULE_CONTRACT
## @purpose  Корневой AGENTS.md для ~/projects/ — общий контракт платформы, наследуемый агентами во всех проектах и контекстах (walk-up по дереву каталогов)
## @scope    Структура папки, что даёт платформа, чего НЕ делать в проекте, канонические команды, deploy-модель
## @invariants
##   1. Канонический файл живёт в ai-platform/docs/projects-root-AGENTS.md; ~/projects/AGENTS.md — symlink на него (single source of truth, автообновление с git pull).
##   2. Изменчивые данные (сервисы, hosts, порты) НЕ дублируются здесь — они в .env.platform каждого проекта.
## @rationale Агент, открытый в папке любого проекта, получает контракт платформы без объяснений; специфика проекта — в AGENTS.md самого проекта.
# endregion MODULE_CONTRACT

# AGENTS.md — ~/projects/ (проекты и контексты ai-platform)



## Структура папки

| Путь | Что это |
|------|---------|
| `ai-platform/` | Платформа (source-репозиторий). Полные правила: `ai-platform/AGENTS.md` |
| `<org>/<project>/` | Подключённый проект. **org = контекст** (отдельная GitHub-организация) |
| `<context>/` | Служебная папка контекста (node-configs, hermes-agent) — создаётся `make new-context` |

## Контракт для работы в папке проекта

1. **Платформа уже предоставляет сервисы** — узнай списком: `grep PLATFORM_ .env.platform`. Postgres подключай через `PLATFORM_POSTGRES_DSN` (façade `pgbouncer:6432`), redis/litellm/langfuse/minio/clickhouse — через `PLATFORM_*_URL`.
2. **НЕ устанавливай** postgres, redis, прокси или свой TLS в проект — это сервисы платформы.
3. **НЕ публикуй порты** в docker-compose — ingress и TLS делает nginx-модуль платформы (сеть `proxy-net`, external).
4. **`.env.platform` — GENERATED, не редактировать.** Устарел → `make sync-env` из папки проекта.
5. **Домен**: авто `<project>.tronyx.ru` (wildcard-cert) или личный — задан в `ai-platform.yaml`.

## Команды

**Из папки проекта:** `make sync-env` (обновить .env.platform), `make status` (live-статус на ноде). Деплой = `git push` (main → production, staging → staging). Секреты в проекте настраивать не нужно.

**Из `ai-platform/`:** `make new-project NAME=<n> TEMPLATE=<t> [DOMAIN=<d>]` · `make project-adopt PROJECT_DIR=<dir> [DOMAIN=<d>]` · `make remove-project PROJECT=<n> NODE=<node>` (безопасный: данные/volumes/репо не удаляются) · `make project-list` · `make project-status PROJECT=<n>` · `make new-context NODE=<n>` · `make context-promote CONTEXT=<ctx>` · `make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой минуя CI, emergency).

Не изобретай новые скрипты — все операции только через перечисленные make-таргеты (`ai-platform/core/entrypoint-manifest.yaml` — реестр).

## Deploy-модель (кратко)

```
git push → CI проекта (≤15 строк) → reusable workflow из <org>/ai-platform@main
        → build ghcr.io → SSH forced-command → атомарный деплой на VPS + healthcheck rollback
        (нормальный путь)

make deploy-project → tar+ssh (platform-deliver + deploy.sh) → VPS
        (прямой путь, emergency, аудит DEPLOY-DIRECT)
```

Обновление платформенного CI не требует правок проектов (workflow подтягивается `@main`; в org — зеркало `<org>/ai-platform`, обновляется `make context-promote`).

## Приоритет инструкций

При конфликте: `AGENTS.md` проекта → этот файл → `ai-platform/AGENTS.md`.
