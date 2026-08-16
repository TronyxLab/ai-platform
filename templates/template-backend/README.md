# GREP_SUMMARY: README backend template quickstart services commands deploy
# STRUCTURE: ┌{{PROJECT_NAME}}┐ → ◇ Быстрый старт → ◇ Платформенные сервисы (таблица) → ◇ Структура → ◇ Команды → ⎋ Деплой

# {{PROJECT_NAME}}

> Backend проект, создан из шаблона `template-backend` (ai-platform).

## Быстрый старт

```bash
# 1. Локальные зависимости (postgres + redis платформенного стека — предварительно `make up` в ai-platform)
make dev

# 2. Синхронизировать платформенное окружение
make sync-env

# 3. Запустить приложение
pip install -r src/requirements.txt
PLATFORM_POSTGRES_DSN=postgresql://dev:dev@localhost:5432/dev python src/main.py
```

## Платформенные сервисы

| Переменная | Сервис | Пример использования |
|-----------|--------|---------------------|
| `PLATFORM_POSTGRES_DSN` | PostgreSQL (shared) | `snippets/db.py` — asyncpg pool |
| `PLATFORM_REDIS_URL` | Redis (shared) | `redis.from_url(settings.redis_url)` |
| `PLATFORM_LITELLM_URL` | LiteLLM proxy | `openai.AsyncOpenAI(base_url=settings.litellm_url)` |
| `PLATFORM_LANGFUSE_URL` | Langfuse tracing | `langfuse.Langfuse(host=settings.langfuse_url)` |
| `PLATFORM_MINIO_URL` | MinIO S3 | `boto3.client('s3', endpoint_url=settings.minio_url)` |
| `PLATFORM_CLICKHOUSE_DSN` | ClickHouse | `clickhouse_connect.get_client(dsn=settings.clickhouse_dsn)` |

Полный список: `grep PLATFORM_ .env.example` (или `.env.platform` после `make sync-env`).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта (генерируется при scaffold) |
| `Dockerfile` | Python 3.12-slim + FastAPI |
| `docker-compose.yml` | Production сервис + platform networks |
| `docker-compose.dev.yml` | Локальная разработка против платформенного стека (external networks) |
| `src/main.py` | HTTP-сервер: /health, /ready, /metrics |
| `src/config.py` | Конфигурация через pydantic-settings (`PLATFORM_*` из `.env.platform`) |
| `src/requirements.txt` | Зависимости Python |
| `snippets/` | Reference-паттерны: `db.py` (asyncpg), `metrics_prometheus.py` (кастомные метрики) |
| `.env.platform` | Платформенное окружение (генерируется `make sync-env`, НЕ редактировать) |
| `Makefile` | Команды платформы |
| `AGENTS.md` | Контекст для AI-агента |

## Команды

```bash
make sync-env               # Обновить .env.platform
make status                 # Проверить статус деплоя
make project-check          # Проверить практики проекта
make project-check --fix    # Автофикс практик
make project-sync-practices # Перегенерировать GENERATED-файлы
make project-set-practices LEVEL=full  # Установить уровень практик (baseline|full|auto)
make dev                    # Запустить dev-сервисы (требует стек платформы: make up в ai-platform)
```

## Деплой

```bash
git push  # CI/CD деплоит автоматически (deploy-project.yml)
```

## Метрики

`/metrics` отдаёт Prometheus-формат (prometheus-client обязателен; `metrics: true` в ai-platform.yaml).
Кастомные метрики (счётчики, гистограммы) — см. `snippets/metrics_prometheus.py`.
