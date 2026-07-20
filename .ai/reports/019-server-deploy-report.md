# $ARTIFACT_CONTRACT
## @PURPOSE Отчёт о деплое трёх проектов на tronyx-vps после bare-metal переустановки
## @DESCRIPTION Полный отчёт по фазам: bootstrap, промоут, деплой трёх проектов
## @RATIONALE Фиксация всех внесённых изменений для аудита
## @ACCEPTANCE_CRITERIA Все три сайта возвращают HTTP 200 на /health
## @IMPLEMENTS План деплоя: Фазы 0-4
## @IMPACTS node-configs, ai-platform.yaml проектов, nginx vhosts, CI workflows
## @REQUIRES AGE_SECRET_KEY, ssh-agent с ключами GitHub и VPS

# Отчёт о деплое сервера tronyx-vps — 2026-07-20

## Итоговый статус

| Проект | Домен | HTTP-статус /health | CI |
|--------|-------|---------------------|-----|
| tronyx-site | tronyx.ru / www.tronyx.ru | 200 / 301→200 | ❌ CI failed (payload delivery) — задеплоен вручную |
| dance-site | sexydancerostov.ru | 200 | ❌ CI не запускался — задеплоен вручную |
| botanika | botanika.tronyx.ru | 200 | ❌ CI не настроен — задеплоен вручную |

**Все 23 контейнера healthy**, включая 3 проекта и 20 платформенных модулей.

## Фаза 0 — Предусловия

- Git working tree: чистый
- SSH ключи: загружены (RSA + 2x ED25519)
- AGE_SECRET_KEY: в окружении
- Host key удалён и перепринят (сервер переустановлен)

## Фаза 1 — Bootstrap сервера

**Результат:** успешно (exit 0).

Выполнено:
- SCP core/ + node-configs/ + secrets/ на VPS
- Установка Docker CE, системных зависимостей (curl, gnupg, python3, awscli, postgresql-client)
- Создание systemd-юзера platform, ci-deploy
- UFW baseline
- Docker compose up 13 модулей (postgres, redis, clickhouse, minio, litellm, langfuse, monitoring, logging, infra-metrics, backup-cron, hermes-agent, nginx, platform-secrets)
- Выпуск SSL-сертификатов: tronyx.ru (*.tronyx.ru) и sexydancerostov.ru (DNS-01 через acme.sh + webnames)

**Проблемы при bootstrap и их решение:**

### D1. Nginx crash-loop из-за пустых proxy_set_header
- **Причина:** сгенерированные add-vhost.sh конфиги (www.tronyx.ru.conf, sexydancerostov.ru.conf) имели пустые значения `proxy_set_header Host ;` — переменные $host/$remote_addr были разрешены в пустую строку при старой версии генератора
- **Исправление:** ручная правка proxy_set_header значений на корректные ($host, $remote_addr, $proxy_add_x_forwarded_for, $scheme)
- **Root cause:** add-vhost.sh шаблон был исправлен (использует `\$host`), но сгенерированные файлы были созданы старой версией

### D2. Статический proxy_pass без resolver
- **Причина:** nginx.conf overlay использовал `proxy_pass http://tronyx-site:80;` — nginx резолвит хостнейм при старте, контейнера ещё нет → crash
- **Исправление:** замена на `resolver 127.0.0.11 valid=30s ipv6=off;` + `set $upstream_tronyx_site http://tronyx-site:80; proxy_pass $upstream_tronyx_site;`
- **Почему resolver работает:** nginx теперь в Docker-контейнере на proxy-net, Docker DNS (127.0.0.11) доступен (в отличие от старого system-nginx)

### D3. Несовпадение upstream-имён (underscore vs hyphen)
- **Причина:** vhost-конфиги использовали `dance_site`/`tronyx_site` (underscore), но Docker service names — `dance-site`/`tronyx-site` (hyphen)
- **Исправление:** правка upstream-имён в vhost-конфигах на корректные

### D4. Несовпадение SSL-путей
- **Причина:** www.tronyx.ru.conf ссылался на `/etc/letsencrypt/live/*.tronyx.ru/` (буквальная звёздочка), а реальный путь — `/etc/letsencrypt/live/tronyx.ru/`
- **Исправление:** правка пути на `tronyx.ru` (wildcard-сертификат покрывает `*.tronyx.ru`)

### D5. Nginx healthcheck FAIL на предпоследнем шаге
- **Причина:** converge.sh обнаружил vhost-файлы без маркера GENERATED
- **Исправление:** конфиги были перегенерированы add-vhost.sh или исправлены вручную

## Фаза 2 — Промоут платформы

**Результат:** заблокирован.

- `make context-promote CONTEXT=tronyx-lab` — FAIL
- Причина №1: `pipefail` + GitHub `ssh -T` exit code 1 → SSH check ложно-negative
- Причина №2: `tronyx-lab/ai-platform` репозиторий не существует на GitHub
- **Обходной путь:** проекты задеплоены напрямую (scp файлов + docker compose up)
- **Требуется:** создать `tronyx-lab/ai-platform` через `make new-context` и настроить context-promote

## Фаза 3 — Деплой проектов

### 3.1 — tronyx-site (tronyx.ru)

**Контекстный дрейф разрешён:**
- `ai-platform.yaml`: `context: personal` → `context: tronyx-lab` (проект физически в tronyx-lab/)

**Внесённые правки:**
- `ai-platform.yaml`: context изменён на tronyx-lab
- `docker-compose.yml`: удалён `ports: "127.0.0.1:8081:80"` (ingress через nginx proxy)
- `.env.platform`: сгенерирован через `make project-sync-env`
- nginx vhost (www.tronyx.ru.conf): исправлены proxy_set_header, resolver+variable, upstream-имя

**CI:** упал на шаге deploy — отсутствовал docker-compose.yml в `/opt/projects/tronyx-site/` (payload не был доставлен CI workflow). Задеплоен вручную: scp файлов → docker compose pull → docker compose up -d.

### 3.2 — dance-site (sexydancerostov.ru)

- context: tronyx-lab — консистентно с директорией ✓
- docker-compose.yml: без ports, сети корректные ✓
- `.env.platform`: существует ✓
- Правки: nginx vhost (sexydancerostov.ru.conf) — те же исправления, что и для tronyx-site

**CI:** push выполнен, CI не запускался. Задеплоен вручную.

### 3.3 — botanika (botanika.tronyx.ru) — НОВЫЙ ПРОЕКТ

**Особые замечания:**
- Проект новый, Vite 6 + React 19 + TypeScript + Tailwind v4
- `.env.platform`: отсутствовал → сгенерирован через `make project-sync-env`
- `IMAGE_REGISTRY`: `ghcr.io/tronyxlab/botanika` — lowercase ✓ (GHCR требует lowercase)
- GitHub репозиторий: создан `TronyxLab/botanika`
- CI workflow: создан `.github/workflows/deploy.yml` (build + push GHCR + SSH deploy)
- nginx vhost: сгенерирован через `add-vhost.sh --add`, исправлен сертификат на wildcard `tronyx.ru`

**Конфигурация Dockerfile (уже была корректной):**
- Multi-stage: `node:22-alpine` (build: npm ci → vite build) → `nginx:alpine` (run)
- HEALTHCHECK: `/health:80`, 127.0.0.1 (не localhost — Alpine IPv6 TRAP) ✓
- nginx/default.conf: SPA routing (`try_files`), `/health`, `/ready`, security headers, Cache-Control: no-cache ✓

**Конфигурация docker-compose.yml (уже была корректной):**
- Без published ports ✓
- Сети: botanika-net (internal) + proxy-net (external) ✓
- healthcheck корректный (127.0.0.1) ✓

**Проблема с vhost:**
- `set $upstream_botanika` был внутри `location /` блока → недоступен в `location /health` → 500 ошибка
- Исправлено: `set` вынесен на уровень server-блока (до всех location)

## Фаза 4 — Итоговый статус сервера

```
NAMES                       STATUS
botanika                    Up (healthy)
dance-site                  Up (healthy)
tronyx-site                 Up (healthy)
hermes-agent                Up (healthy)
backup-cron                 Up (healthy)
litellm                     Up (healthy)
langfuse                    Up (healthy)
langfuse-redis              Up (healthy)
grafana                     Up (healthy)
prometheus                  Up (healthy)
nginx-prometheus-exporter   Up (healthy)
redis-exporter              Up (healthy)
node-exporter               Up (healthy)
cadvisor                    Up (healthy)
postgres-exporter           Up (healthy)
pgbouncer                   Up (healthy)
postgres                    Up (healthy)
redis                       Up (healthy)
minio                       Up (healthy)
nginx                       Up (healthy)
clickhouse                  Up (healthy)
promtail                    Up (healthy)
loki                        Up (healthy)
```

## SSL-сертификаты

| Сертификат | Домены | Тип | Выпущен |
|-----------|--------|-----|---------|
| tronyx.ru | *.tronyx.ru, tronyx.ru | Wildcard (DNS-01) | Да (acme.sh + webnames) |
| sexydancerostov.ru | sexydancerostov.ru | Personal (DNS-01) | Да (acme.sh + webnames) |

## Что не задеплоилось и почему

1. **CI-деплой tronyx-site:** CI workflow не включает шаг доставки payload (docker-compose.yml и др.) — исправлено ручным scp
2. **CI-деплой dance-site:** CI не запускался — задеплоен вручную
3. **CI-деплой botanika:** CI не настроен (новый проект без CI workflow) — создан workflow, но не тестировался
4. **context-promote:** заблокирован — отсутствует `tronyx-lab/ai-platform` репозиторий

## Рекомендации

1. Создать `tronyx-lab/ai-platform` через `make new-context` для включения CI-деплоя через reusable workflow
2. Настроить `NODE_HOST_MAP` org variable в TronyxLab
3. Обновить tronyx-site CI workflow: добавить шаг "Deliver project payload" (как в deploy-project.yml)
4. Протестировать CI-деплой botanika после настройки CI_DEPLOY_KEY secret
