# GREP_SUMMARY: platform-project-contract, канон, environment, services, networks, delivery-channels, DO-NOT, instructions-priority, node-boundaries, practices, levels, classes, escalator, warnings
# STRUCTURE: ┌MODULE_CONTRACT┐ → ◇ окружение (provides-сервисы, сети, каналы доставки) → ◇ команды → ◇ practices (уровни/классы/эскалатор/варнинги) → ◇ DO NOT → ◇ приоритет инструкций → ⎋ указатель на .env.platform
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
## @changes  2026-08-05 · DevPlan 137 W5 — секция «Practices: уровни, классы, эскалатор, варнинги»
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
| **Project payload** | tar по SSH forced-command (`receive`) | docker-compose.yml, ai-platform.yaml, .env.platform, practices.lock проекта — после `git push` проекта (CI) |

## Команды

**Из папки проекта:**

| Команда | Операция |
|---------|----------|
| `make sync-env` | (пере)генерировать `.env.platform` и GENERATED-секцию `AI-PLATFORM.md` |
| `make project-check` | Проверить практики проекта локально (K1, PLATFORM_DIR-делегирование) — состояние, maturity, [PRACTICES:...] отчёт |
| `make project-fix` | Автофикс практик (auto_fix-проверки канона: hygiene, ruff format) |
| `make project-sync-practices` | Перегенерация GENERATED-файлов практик + practices.lock до канона (repair дрейфа) |
| `make project-set-practices LEVEL=<baseline\|full\|auto>` | Установить уровень практик в `ai-platform.yaml#quality.level` (full — ТОЛЬКО по явному согласию) |
| `make status` | live-статус проекта на целевой ноде |
| `make help` | все доступные команды проекта |

Деплой = `git push` (main → production, staging → staging). Секреты в проекте настраивать не нужно.

**Из платформы (`ai-platform/`):** `make new-project` · `make adopt-project` · `make remove-project`
· `make project-list` · `make project-status` · `make new-context` · `make context-promote`
· `make deploy-project PROJECT=<dir> NODE=<node>` (прямой деплой, emergency) · `make converge NODE=<node>`.

Не изобретай новые скрипты — все операции только через перечисленные make-таргеты
(`ai-platform/core/entrypoint-manifest.yaml` — реестр).

## Practices: уровни, классы, эскалатор, варнинги

Проект **наследует поведение, а не код**: проверки исполняются платформенными каналами
(DevPlan 137), в репозитории проекта — только тонкие GENERATED-файлы практик.

### Наследуемые файлы (GENERATED, DO NOT EDIT)

Генерируются из единого канона `ai-platform/core/internal/practices/practices_manifest.yaml`
(аналог `check-suite.yaml`) — в проекте нет копий платформенного кода:

| Файл проекта | Назначение |
|--------------|------------|
| `pyproject.toml` | ruff-конфиг (baseline: только `format`; full: полный набор правил) + pytest options |
| `.pre-commit-config.yaml` | ТОЛЬКО upstream-хуки (pre-commit-hooks, gitleaks, conventional-pre-commit, ruff-pre-commit, shellcheck-py) + pre-push K5-хук `project-push-check` |
| `tests/conftest.py` | чтение `.env.platform` + health-фикстура (TCP-probe, skip при недоступном сервисе) |
| `tests/test_health.py` | smoke-тест /health |
| `practices.lock` | снапшот канона: version, level, state, maturity, generator_hash — коммитится в git проекта |
| `ai-platform.yaml#quality.level` | уровень практик: `baseline` \| `full` \| `auto` (default `auto`) |

Repair дрейфа: `make project-sync-practices`. Ручные правки GENERATED-файлов детектируются
(drift-gate) и перезаписываются.

### Уровни (BASELINE / FULL)

| Уровень | Состав | Цель |
|---------|--------|------|
| **BASELINE** | gitleaks, hygiene (автофикс), conventional-commits, compose config, ruff format, shellcheck, pytest (если тесты есть), build/tsc | Моки/MVP: overhead цикла правки ≤60s, churn=0 (автофикс + разовые детекты) |
| **FULL** | + ruff check, pyright, eslint, pytest-full (strict/LDD), grep-summary, drift-gate | Долгоживущие проекты: максимальная защита; включается плавно, по согласию |

### Классы проверок (L1/L2/L3) — политика блокировки

Класс определяет блокировку на деплое (verify, VPS), уровень — исполнение локально/в CI:

| Класс | Примеры | baseline | proposed | active-full |
|-------|---------|----------|----------|-------------|
| **L1 — безопасность платформы** | секреты в compose/env, публикация портов, отсутствие healthcheck, external-сети вне канона, env-файл ≠ `.env.platform` | 🔴 блок | 🔴 блок | 🔴 блок |
| **L2 — контракт качества** | compose config невалиден, build --check, дрейф practices.lock (version) | 🟡 warning | 🟡 warning | 🔴 блок |
| **L3 — код-стандарты** | ruff check, pyright, eslint, LDD, grep-summary, hygiene | — (baseline: только автофикс) | 🟡 warning | 🔴 блок |

L1-контракты исполняются при ЛЮБОМ уровне — это защита платформы, не качество проекта.
Реализация: `ai-platform/core/internal/deploy/verify_contracts.py` (verify verb, K3).

### Эскалатор зрелости (3 состояния, БЕЗ автопромоута)

Состояние вычисляется там, где есть git (локально `project-check`/pre-push, CI проекта);
на VPS применяется готовый `state` из `practices.lock` (evaluate() не вызывается):

```
baseline ──maturity: age>30d ∨ files>50──► proposed ──согласие (project-set-practices full)──► active-full
   ▲                                                                                              │
   └────────────────────────── project-set-practices baseline ◄───────────────────────────────────┘
```

| Состояние | Проверки | Блокировка деплоя | Варнинг |
|-----------|----------|-------------------|---------|
| **baseline** | только BASELINE + L1 | L1 | — |
| **proposed** | FULL исполняется, non-blocking | L1; L2/L3 warning | `[PRACTICES:PROPOSE]` + рекомендация `make project-set-practices full` |
| **active-full** | FULL, все классы | L1+L2+L3 | — (состояние видно в `[PRACTICES:STATE][active-full]`) |

**Автопромоута НЕТ** (решение пользователя 2026-08-05): active-full включается ТОЛЬКО по
явному согласию `make project-set-practices full`. Переходы аудируются (audit_logger).

### Варнинги [PRACTICES:...]

Единый формат для агента (в выводе project-check, pre-push, CI summary, verify, AI-PLATFORM.md):

- `[PRACTICES:PROPOSE][level:full][reason:age=41d,files=87]` — предложение включить full (non-blocking)
- `[PRACTICES:LEGACY]` — practices.lock отсутствует (legacy-проект); grace-режим: L1 warning-only
  при `PRACTICES_LEGACY_GRACE=1` (2-стадийный rollout, TRAP §10.2)
- `[PRACTICES:BLOCK][L1][<contract>]` — блокирующее нарушение контракта (деплой остановлен)
- `[PRACTICES:DRIFT-VERSION]` — lock.version < версии канона на ноде (дрейф; repair: `make project-sync-practices`)
- `[PRACTICES:STATE]`, `[PRACTICES:CHECK]`, `[PRACTICES:RESULT]` — отчёт project-check

### practices.lock и доставка на VPS

`practices.lock` — GENERATED-снапшот (коммитится в git проекта): `version`, `level`, `state`,
`maturity` (age_days/code_files — носитель для VPS, где git недоступен), `generator_hash`,
`language`, sha256 по GENERATED-файлам. Доставляется на VPS payload'ом receive
(deploy-project.yml шаг Deliver: `FILES += practices.lock`) — без него K3 не имеет носителя
state для L2/L3-блокировки и дрейф-детекта.

### Каналы исполнения (K1–K5)

| Канал | Механика | Где |
|-------|----------|-----|
| **K1** | `make project-check` (PLATFORM_DIR-делегирование) | локально |
| **K2** | inline quality-шаги deploy-project.yml (lint/test по language/level из ai-platform.yaml, maturity-warn) | CI проекта |
| **K3** | verify verb → `verify_contracts.py` (L1 всегда; L2/L3 по state из lock) | VPS |
| **K4** | maturity + escalator (state-машина, без автопромоута) | локально/CI |
| **K5** | pre-push хук проекта `project-push-check` → `make project-check` | локально |

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
