# GREP_SUMMARY: template fullstack project backend frontend fastapi nginx docker-compose sync-env
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → backend(8000)+frontend(80)

# {{PROJECT_NAME}}

> Fullstack проект, создан из шаблона `template-fullstack` (AI-platform).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта для платформы |
| `docker-compose.yml` | Backend + frontend сервисы с proxy-net и shared-db-net + `.env.platform` |
| `Dockerfile.backend` | Python 3.12-slim + FastAPI |
| `Dockerfile.frontend` | Multi-stage build (node → nginx) |
| `nginx/default.conf` | SPA routing + /health + /ready |
| `backend/main.py` | HTTP-сервер с /health и /ready |
| `backend/requirements.txt` | Зависимости Python |
| `frontend/index.html` | HTML заглушка |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |
| `Makefile` | Команды `make sync-env`, `make status` |
| `.env.platform` | Платформенное окружение (генерируется) |
| `AGENTS.md` | Контекст для AI-агента |

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

## Локальная разработка

```bash
# Backend
docker compose up -d {{PROJECT_NAME}}-backend
curl http://localhost:8000/health

# Frontend
docker compose up -d {{PROJECT_NAME}}-frontend
curl http://localhost:80/health
```

## Деплой

CI/CD автоматически деплоит при пуше в `main`.
