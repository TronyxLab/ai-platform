# ai-platform
# GREP_SUMMARY: readme, ai-platform, quick-start
# STRUCTURE: ┌make up┐ → ┌make test┐ → ┌make gate┐ → ┌make deploy┐

AI-платформа для развёртывания и управления LLM-сервисами на выделенных VPS.

## Быстрый старт

```bash
make up          # Запустить локальный стек (docker compose)
make test        # Запустить тесты
make gate        # Production gate (полная проверка перед деплоем)
```

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
| `make test [MARKER=...]` | Тестирование (static, smoke, component, integration, predeploy, contract, e2e, all) |
| `make gate [MODE=fast\|full\|ci-docker]` | Production gate |
| `make deploy PROJECT=<dir>` | Деплой проекта |
| `make bootstrap-node NODE=<name>` | Bootstrap ноды |
| `make context-promote CONTEXT=<name>` | Промоут платформы в контекст |

## Требования

- Python 3.10+
- Docker + Docker Compose v2
- bash 5.0+
- make
