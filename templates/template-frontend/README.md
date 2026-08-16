# GREP_SUMMARY: README frontend template vite react typescript commands services
# STRUCTURE: ┌{{PROJECT_NAME}}┐ → ◇ Быстрый старт (npm) → ◇ Платформенные сервисы → ◇ Структура → ◇ Команды → ⎋ Деплой

# {{PROJECT_NAME}}

> Frontend проект, создан из шаблона `template-frontend` (ai-platform).
> Стек: Vite 6 + React 19 + TypeScript 5.8 + ESLint 9 (flat config).

## Быстрый старт

```bash
# 1. Установить зависимости
npm ci

# 2. Запустить dev-сервер (порт 80, proxy /health + /ready)
npm run dev

# 3. Сборка для production
npm run build
```

## Платформенные сервисы

| Переменная | Сервис | Примечание |
|-----------|--------|-----------|
| `PLATFORM_DOMAIN` | Корневой домен платформы | Используется для API-URL |
| `PLATFORM_LITELLM_URL` | LiteLLM proxy | LLM-доступ через gateway |
| `PLATFORM_LANGFUSE_URL` | Langfuse tracing | LLM-трассировка |
| `PLATFORM_PROXY_NET` | nginx proxy сеть | Внешняя сеть платформы |

Полный список: `grep PLATFORM_ .env.example` (или `.env.platform` после `make sync-env`).

## Структура

| Файл | Назначение |
|------|-----------|
| `ai-platform.yaml` | Декларация проекта (генерируется при scaffold) |
| `Dockerfile` | Multi-stage build (node → nginx) |
| `docker-compose.yml` | Сервис + proxy-net (внешняя сеть nginx) + `.env.platform` |
| `nginx/default.conf` | SPA routing + /health + /ready |
| `index.html` | Vite entry point |
| `src/main.tsx` | React root |
| `src/App.tsx` | Демо-компонент (health + platform services) |
| `vite.config.ts` | Vite-конфигурация (dev-порт 80, proxy) |
| `tsconfig.json` | TypeScript strict |
| `eslint.config.js` | ESLint 9 flat config |
| `.github/workflows/deploy.yml` | CI/CD пайплайн (GitHub Actions) |
| `Makefile` | Команды `make sync-env`, `make status`, `make project-*` |
| `.env.platform` | Платформенное окружение (генерируется) |
| `AGENTS.md` | Контекст для AI-агента |

## Команды

```bash
npm run dev      # Dev-сервер (Vite, порт 80)
npm run build    # tsc + vite build → dist/
npm run preview  # Превью production-сборки
npm run lint     # ESLint 9 (flat config)

make sync-env    # Обновить .env.platform
make project-check  # Проверить практики проекта
```

## Деплой

```bash
git push  # CI/CD деплоит автоматически (deploy-project.yml)
```

## Метрики

Frontend-шаблон: `metrics: false` в ai-platform.yaml (статический контент за nginx) —
прометеевские метрики не скрейпятся; /health и /ready обслуживает nginx.
