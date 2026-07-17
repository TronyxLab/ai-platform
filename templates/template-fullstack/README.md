# GREP_SUMMARY: template fullstack project backend frontend fastapi nginx docker-compose
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → backend(8000)+frontend(80)

# __PROJECT_NAME__

> Fullstack проект, создан из шаблона `template-fullstack` (AI-platform).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта для платформы |
| `docker-compose.yml` | Backend + frontend сервисы с proxy-net и shared-db-net |
| `Dockerfile.backend` | Python 3.12-slim + FastAPI |
| `Dockerfile.frontend` | Multi-stage build (node → nginx) |
| `nginx/default.conf` | SPA routing + /health + /ready |
| `backend/main.py` | HTTP-сервер с /health и /ready |
| `backend/requirements.txt` | Зависимости Python |
| `frontend/index.html` | HTML заглушка |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |
| `.github/workflows/platform-deploy.yml` | Reusable workflow деплоя |

## Параметры шаблона

| Плейсхолдер | Значение | Обязательность |
|------------|----------|---------------|
| `__PROJECT_NAME__` | Имя проекта | обязательный |
| `__ORG_NAME__` | Организация в GHCR (только **lowercase**!) | обязательный |
| `__NODE_NAME__` | Целевая нода | обязательный |
| `__DOMAIN__` | FQDN домена | опционально |
| `__DATABASE__` | Имя базы данных | опционально |

> ⚠️ **`__ORG_NAME__` ОБЯЗАТЕЛЬНО в lowercase** — GHCR registry rejects uppercase org names.

## Локальная разработка

```bash
# Backend
docker compose up -d __PROJECT_NAME__-backend
curl http://localhost:8000/health

# Frontend
docker compose up -d __PROJECT_NAME__-frontend
curl http://localhost:80/health
```

## Деплой

CI/CD автоматически деплоит при пуше в `main` (или `staging` бранч, если `environments.staging: true`).
