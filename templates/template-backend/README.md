# GREP_SUMMARY: template backend project health ready docker docker-compose fastapi sync-env
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → healthcheck(8000)

# {{PROJECT_NAME}}

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
| `{{PROJECT_NAME}}` | Имя проекта | обязательный |
| `{{ORG_NAME}}` | Организация в GHCR (только **lowercase**!) | обязательный |
| `{{NODE_NAME}}` | Целевая нода | обязательный |
| `{{DOMAIN}}` | FQDN домена | опционально |
| `{{PLATFORM_DOMAIN}}` | Домен платформы | опционально |
| `{{DATABASE}}` | Имя базы данных | опционально |

> ⚠️ **`{{ORG_NAME}}` ОБЯЗАТЕЛЬНО в lowercase** — GHCR registry rejects uppercase org names.
> Передавайте `--org tronyxlab`, не `--org TronyxLab`.

## Деплой

CI/CD автоматически деплоит при пуше в `main`.
