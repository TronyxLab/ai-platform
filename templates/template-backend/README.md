# GREP_SUMMARY: template backend project health ready docker docker-compose fastapi sync-env
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → healthcheck(8000)

# __PROJECT_NAME__

> Backend проект, создан из шаблона `template-backend` (AI-platform).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта для платформы |
| `Dockerfile` | Python 3.12-slim + FastAPI |
| `docker-compose.yml` | Сервис + shared-db-net (внешняя сеть PostgreSQL) + `.env.platform` |
| `src/main.py` | HTTP-сервер с /health и /ready |
| `src/requirements.txt` | Зависимости Python |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |
| `Makefile` | Команды `make sync-env`, `make status` |
| `.env.platform` | Платформенное окружение (генерируется) |
| `AGENTS.md` | Контекст для AI-агента |

## Локальная разработка

```bash
docker compose up -d
curl http://localhost:8000/health
```

## Платформенные команды

```bash
# Синхронизировать платформенное окружение
make sync-env

# Проверить статус деплоя
make status
```

`.env.platform` генерируется автоматически командой `make sync-env`. Не редактируйте его вручную.

## Параметры шаблона

| Плейсхолдер | Значение | Обязательность |
|------------|----------|---------------|
| `__PROJECT_NAME__` | Имя проекта | обязательный |
| `__ORG_NAME__` | Организация в GHCR (только **lowercase**!) | обязательный |
| `__NODE_NAME__` | Целевая нода | обязательный |
| `__DOMAIN__` | FQDN домена | опционально |
| `__PLATFORM_DOMAIN__` | Домен платформы | опционально |
| `__DATABASE__` | Имя базы данных | опционально |

> ⚠️ **`__ORG_NAME__` ОБЯЗАТЕЛЬНО в lowercase** — GHCR registry rejects uppercase org names.
> Передавайте `--org tronyxlab`, не `--org TronyxLab`.

## Деплой

CI/CD автоматически деплоит при пуше в `main`.
