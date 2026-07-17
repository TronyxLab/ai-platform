# GREP_SUMMARY: template frontend project nginx health ready node
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → healthcheck(80)

# __PROJECT_NAME__

> Frontend проект, создан из шаблона `template-frontend` (AI-platform).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта для платформы |
| `Dockerfile` | Multi-stage build (node → nginx) |
| `docker-compose.yml` | Сервис + proxy-net (внешняя сеть nginx) |
| `nginx/default.conf` | SPA routing + /health + /ready |
| `src/` | Исходный код фронтенда |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |

## Локальная разработка

```bash
docker compose up -d
curl http://localhost:80/health
```

## Параметры шаблона

| Плейсхолдер | Значение | Обязательность |
|------------|----------|---------------|
| `__PROJECT_NAME__` | Имя проекта | обязательный |
| `__ORG_NAME__` | Организация в GHCR (только **lowercase**!) | обязательный |
| `__NODE_NAME__` | Целевая нода | обязательный |
| `__DOMAIN__` | FQDN домена | опционально |

> ⚠️ **`__ORG_NAME__` ОБЯЗАТЕЛЬНО в lowercase** — GHCR registry rejects uppercase org names.
> Передавайте `--org tronyxlab`, не `--org TronyxLab`.

## Деплой

CI/CD автоматически деплоит при пуше в `main` (или `staging` бранч, если `environments.staging: true`).
