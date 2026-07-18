# GREP_SUMMARY: template frontend project nginx health ready node Makefile sync-env
# STRUCTURE: ┌template vars┐ → scaffold(bash) → project_dir → healthcheck(80)

# {{PROJECT_NAME}}

> Frontend проект, создан из шаблона `template-frontend` (AI-platform).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта для платформы |
| `Dockerfile` | Multi-stage build (node → nginx) |
| `docker-compose.yml` | Сервис + proxy-net (внешняя сеть nginx) + `.env.platform` |
| `nginx/default.conf` | SPA routing + /health + /ready |
| `src/` | Исходный код фронтенда |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |
| `Makefile` | Команды `make sync-env`, `make status` |
| `.env.platform` | Платформенное окружение (генерируется) |
| `AGENTS.md` | Контекст для AI-агента |

## Локальная разработка

```bash
docker compose up -d
curl http://localhost:80/health
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

> ⚠️ **`{{ORG_NAME}}` ОБЯЗАТЕЛЬНО в lowercase** — GHCR registry rejects uppercase org names.
> Передавайте `--org tronyxlab`, не `--org TronyxLab`.

## Деплой

CI/CD автоматически деплоит при пуше в `main`.
