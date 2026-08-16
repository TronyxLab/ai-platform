# ai-platform
# GREP_SUMMARY: readme, ai-platform, quick-start
# STRUCTURE: ┌make up┐ → ┌make check┐ → ┌make gate┐ → ┌make deploy┐

AI-платформа для развёртывания и управления LLM-сервисами на выделенных VPS.

## Быстрый старт

```bash
make up          # Запустить локальный стек (docker compose)
make check       # Запустить диагностику (все проверки, батч ошибок)
make gate        # Production gate (полная проверка перед деплоем)
```

## Локальный dev-стек (runtime-файлы статус-страницы)

После `make up` на dev-локали сгенерируй runtime-файлы статус-страницы:

```bash
make dev-metrics   # status-metrics.json + .htpasswd-platform (идемпотентно)
```

Dev-локали (macOS) не имеют нодового cron (`/etc/cron.d/platform-metrics`) — файлы,
которые на ноде обновляются раз в минуту, здесь генерируются вручную. Таргет вызывает
**тот же** экспортёр, что нодовый cron (`platform_export_metrics.py`), и htpasswd через
`secrets_manager` CLI. Пути/креды берутся из `.env` (`STATUS_METRICS_JSON`,
`HTPASSWD_FILE`, `PLATFORM_MASTER_EMAIL/PASSWORD` — см. `.env.example`).
Повторный запуск безопасен: `status-metrics.json` пересоздаётся (свежесть — цель),
`.htpasswd-platform` не перезаписывается при неизменных кредах (D-12, DevPlan 130 W1).

## Архитектурные инварианты

1. **Makefile — единый фасад.** Все операции через `make <target>`.
2. **Модель деплоя: git push → CI.** `make deploy` / `make context-promote`.
3. **org = context.** Каждый контекст деплоя — отдельная GitHub-организация.
4. **Полный локальный стек.** `docker compose up` на macOS.
5. **LiteLLM — PostgreSQL** во всех окружениях.

Подробнее: [`AGENTS.md`](AGENTS.md), [`core/AGENTS.md`](core/AGENTS.md), [`core/modules/AGENTS.md`](core/modules/AGENTS.md).

## Основные операции

| Команда | Описание |
|---------|----------|
| `make check [MARKER=<suite>] [TEST_FILE=<path>]` | Единая тестовая команда: полная диагностика или один сьют/файл (DevPlan 165) |
| `make gate [MODE=fast\|full\|ci-docker]` | Production gate |
| `make deploy PROJECT=<dir>` | Деплой проекта |
| `make bootstrap-node NODE=<name>` | Bootstrap ноды |
| `make context-promote CONTEXT=<name>` | Промоут платформы в контекст |

## Требования

- Python 3.10+
- Docker + Docker Compose v2
- bash 5.0+
- make
