# GREP_SUMMARY: platform-project-contract, канон, environment, services, networks, delivery-channels, DO-NOT, instructions-priority, node-boundaries
# STRUCTURE: ┌MODULE_CONTRACT┐ → ◇ окружение (provides-сервисы, сети, каналы доставки) → ◇ команды → ◇ DO NOT → ◇ приоритет инструкций → ⎋ указатель на .env.platform
# region MODULE_CONTRACT
## @purpose  Канонический документ-инструкция платформы ai-platform для агентов, работающих
##           в репозиториях проектов. Описывает полное окружение, которое платформа
##           предоставляет проекту (provides-сервисы, сети, каналы доставки, лимиты),
##           границы DO NOT, канонические команды, приоритет инструкций.
## @scope    Ссылается из AI-PLATFORM.md каждого проекта (URL GitHub + локальный путь).
##           Не дублирует изменчивую фактуру (hosts/ports/DSN) — она в .env.platform
##           проекта (GENERATED, машиночитаемая) и в AI-PLATFORM.md (GENERATED-секция).
## @invariants
##   1. Этот документ — КАНОН: правки только через PR в ai-platform (не в проектах).
##   2. Изменчивые данные НЕ фиксируются здесь — только в platform-env.yaml (SoT) →
##      .env.platform / AI-PLATFORM.md (GENERATED).
##   3. При конфликте инструкций: AGENTS.md проекта → AI-PLATFORM.md проекта →
##      настоящий документ → ai-platform/AGENTS.md (root) → core/AGENTS.md.
##   4. Проект НЕ поднимает собственные сервисы платформенного класса (БД/редис/прокси/TLS).
## @rationale Эмпирика 2026-08-03 (DevPlan 133): агенты в репо проектов не имели
##            единой инструкции окружения — каждая сессия заново выясняла, что даёт
##            платформа и что запрещено. Гибрид «статичный канон + генерируемая
##            per-node секция» (AI-PLATFORM.md) закрывает разрыв: статика не устаревает,
##            генерация даёт актуальную фактуру ноды.
## @changes  2026-08-03 · DevPlan 133 W1 — создан (D1: docs/ рядом с projects-root-AGENTS.md)
# endregion MODULE_CONTRACT

# AI-PLATFORM — контракт проекта с платформой

> Канонический документ для агентов и разработчиков, работающих в репозитории проекта,
> подключённого к платформе ai-platform. Проектный файл `AI-PLATFORM.md` в корне репозитория
> проекта ссылается на этот документ и содержит актуальную GENERATED-секцию окружения ноды.

## Что предоставляет платформа

Платформа предоставляет каждому проекту **общий стек сервисов** (provides из
`platform-env.yaml` — единый source of truth). Машиночитаемая фактура — `.env.platform`
проекта (`PLATFORM_*` переменные) и GENERATED-секция `AI-PLATFORM.md`.

| Сервис | Фасад | Назначение |
|--------|-------|------------|
| `postgres` | `pgbouncer:6432` (`PLATFORM_POSTGRES_DSN`) | Единый PostgreSQL 16 + пулер соединений. Проект подключается своей ролью `${project}_user` к своей БД `needs.database` (роль/БД/GRANT создаются хук-ом postgres при деплое) |
| `redis` | `redis:6379` (`PLATFORM_REDIS_URL`) | Общий кэш/очереди |
| `nginx` | `nginx-proxy:443` (`PLATFORM_NGINX_URL`) | Ingress + TLS (единственная точка публикации) |
| `litellm` | `litellm:4000` (`PLATFORM_LITELLM_URL`) | LLM-прокси (единый ключ через платформу) |
| `langfuse` | `langfuse:3001` (`PLATFORM_LANGFUSE_URL`) | Трейсинг/наблюдаемость LLM |
| `minio` | `minio:9000` (`PLATFORM_MINIO_URL`) | S3-совместимое хранилище |
| `clickhouse` | `clickhouse:8123` (`PLATFORM_CLICKHOUSE_URL/DSN`) | Аналитика/логи |

Полный актуальный список сервисов, hosts, портов, DSN/URL — в `.env.platform` проекта
(регенерация: `make sync-env`).

### Сети платформы

| Сеть | Назначение |
|------|-----------|
| `proxy-net` | Внешний ingress (nginx ↔ проект) |
| `shared-db-net` | postgres/pgbouncer ↔ проекты |
| `shared-cache-net` | redis ↔ проекты |
| `hermes-agent-net` | hermes-agent, litellm, langfuse, minio, clickhouse |
| `observability-net` | мониторинг/логирование |

Проект подключается к нужным сетям через свой `docker-compose.yml` (external сети).

### Каналы доставки кода на ноду

| Канал | Механизм | Применение |
|-------|----------|------------|
| **Core** | SCP/rsync (push) | `core/`, `node-configs/`, `secrets/` — инфраструктурный код платформы |
| **Context-overlay** | git clone/pull | Контекстные overlay, конфигурации модулей |
| **Project payload** | tar по SSH forced-command (`receive`) | docker-compose.yml, ai-platform.yaml, .env.platform проекта — после `git push` проекта (CI) |

## Команды

**Из папки проекта:**

| Команда | Операция |
|---------|----------|
| `make sync-env` | (пере)генерировать `.env.platform` и GENERATED-секцию `AI-PLATFORM.md` |
| `make status` | live-статус проекта на целевой ноде |
| `make help` | все доступные команды проекта |

Деплой = `git push` (main → production, staging → staging). Секреты в проекте настраивать не нужно.

**Из платформы (`ai-platform/`):** `make new-project` · `make adopt-project` · `make remove-project`
· `make project-list` · `make project-status` · `make new-context` · `make context-promote`
· `make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой, emergency) · `make converge NODE=<node>`.

Не изобретай новые скрипты — все операции только через перечисленные make-таргеты
(`ai-platform/core/entrypoint-manifest.yaml` — реестр).

## DO NOT

1. **НЕ поднимай собственные** postgres/redis/прокси/TLS в проекте — это сервисы платформы.
2. **НЕ публикуй порты** в docker-compose проекта — ingress и TLS делает nginx-модуль
   платформы (сеть `proxy-net`, external).
3. **НЕ редактируй `.env.platform`** вручную — файл GENERATED; устарел → `make sync-env`.
4. **НЕ храни секреты/токены/ключи** в файлах проекта (в т.ч. в `.env`, коммитимом в git).
   Пароль роли БД проекта — только в `.platform-db.env` на ноде (0600, вне payload/git).
5. **НЕ удаляй** `AI-PLATFORM.md`, `Makefile`, `AGENTS.md` — контракты проекта с платформой.
6. **НЕ меняй** GENERATED-секцию `AI-PLATFORM.md` вручную — перезапишется при `make sync-env`.
7. **НЕ создавай** свои БД вне `needs.database` — роль/БД/GRANT выдаются хук-ом postgres.

## Приоритет инструкций

При конфликте инструкций (убывание приоритета):

1. `AGENTS.md` проекта — специфика проекта;
2. `AI-PLATFORM.md` проекта — контракт проекта с платформой (GENERATED-секция — фактура ноды);
3. **настоящий документ** — канон платформы (окружение, границы, команды);
4. `ai-platform/AGENTS.md` (root) — архитектурные инварианты, deploy-модель;
5. `ai-platform/core/AGENTS.md` — каталог операций, слои, forbidden-списки.

## Лимиты и границы ноды

- Память/CPU модулей платформы ограничены в `docker-compose.base.yml` каждого модуля
  (deploy.resources) — проект не может превысить лимиты общего стека.
- Проектный контейнер работает в сетях платформы (external) — без host-портов.
- Шаред-доступ к БД: роль `${project}_user` имеет `CONNECT` на свою БД и
  `CREATE, USAGE` на её схему `public` — и НИЧЕГО больше (изоляция от чужих БД).

## Машиночитаемая фактура

`.env.platform` проекта — единственный машиночитаемый источник hosts/ports/DSN/URL
(`PLATFORM_*` переменные, GENERATED). GENERATED-секция `AI-PLATFORM.md` — человекочитаемая
сводка той же фактуры (enabled-модули ноды, сервисы, сети, needs-статус проекта).
