# FAIL-findings · S1 Redis unavailable / S2 Queue backlog

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: failure-modes redis и очередей (research-only, код не менялся)
## @scope core/modules/{redis,langfuse,clickhouse,infra-metrics,monitoring}, hermes-agent, contracts
## @rationale максимум снижения риска / минимум churn: config > runbook > точечный код
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов сценария, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures
## IMPACTS launch-blockers candidates (см. findings-redis-queue-002.md)

## ФАКТ по очередям (S2, обязательная проверка)
celery/rq/arq/bullmq/kombu/dramatiq/sidekiq/amqp — grep по `*.yml/*.yaml/*.txt/*.toml`
и pyproject.toml всего репо: **0 совпадений**. Платформенных worker-очередей НЕТ.
Единственная реальная очередь уровня приложения — **ingestion-pipeline langfuse**:
web → `langfuse-redis` (dedicated sidecar, event queue) → `langfuse-worker` v4 →
ClickHouse/S3/Postgres (evidence: langfuse/docker-compose.base.yml:76,123-126).
Внутренние merge-очереди ClickHouse — не app-queue. litellm очередей не имеет
(PostgreSQL, инвариант 8; grep redis по core/modules/litellm/*.yml — пусто).

## S1 сводка (redis unavailable): 9 ответов
1. Что происходит: крэш redis → restart: always поднимает за секунды (потеря всех ключей —
   cache-only by design); «зависший, но живой» redis → unhealthy → host-watchdog рестартует
   через ≥10 мин (watchdog.py:82-90, cron */5, RestartCount>5 → сдаётся).
2. Где отказ: core/modules/redis/docker-compose.base.yml:29 service redis;
   langfuse-ветка — langfuse/docker-compose.base.yml:159 langfuse-redis.
3. Auto-recovery: да (docker policy + watchdog + Telegram-notify о самом рестарте).
4. Broken state: общий redis — нет (cache); langfuse-redis — очередь живёт в AOF+volume,
   потеря ≤1s записей (appendfsync everysec).
5. Retry: безопасен для cache-консюмеров; проекты с PLATFORM_REDIS_URL-as-queue теряют
   элементы безвозвратно (см. FAIL-0203).
6. User impact: hermes — none (redis optional/warn-only, healthcheck_deps.py:61);
   проекты — cache-miss или тихая потеря queue-элементов; langfuse — не зависит от общего redis.
7. Alert: НЕТ (FAIL-0201): Grafana ServiceDown (`up == bool 0`, alert-rules.yml:81) ловит
   смерть exporter-контейнера, но НЕ смерть redis-демона (exporter остаётся up,
   внутри лишь redis_up=0 — правила на него отсутствуют: platform-alerts.yml/alert-rules.yml
   — 0 вхождений redis).
8. Восстановление: `make healthcheck NODE=<n>` → `make -C core/modules/redis status|logs`
   → `make -C core/modules/redis restart` (soft) / `restart-hard`; стек: `make converge NODE=<n>`.
9. Минимальный фикс: Prometheus-rule `redis_up == 0 or absent()` + maxmemory-headroom (FAIL-0202).

## S2 сводка (queue backlog): 9 ответов
1. Рост очереди langfuse-ingestion при деградации consumer'ов (worker/clickhouse/s3);
   платформенных очередей нет (факт выше).
2. Где отказ: producer web (compose:76 REDIS_CONNECTION_STRING), consumer langfuse-worker
   (compose:125), терминал clickhouse (compose:40); ёмкость буфера — langfuse-redis:181
   `--maxmemory 64mb`.
3. Auto-recovery: worker/ch контейнеры — restart: unless-stopped + watchdog; очередь
   персистентна (AOF+volume) — восстановление потребления после починки.
4. Broken state: ДА — при переполнении 64mb allkeys-lru молча ЭВИКТУЕТ элементы очереди
   (compose:182) — тихая потеря трейсов, очередь «здорова».
5. Retry: повторная отправка клиентом SDK возможна (HYPOTHESIS: зависит от интеграции
   проекта); уже эвиктурованное — невосстановимо.
6. User impact: задержка/потеря трейсов — теряется наблюдаемость LLM ровно во время инцидента.
7. Alert: НЕТ — в prometheus.yml.tmpl нет job для langfuse/worker/langfuse-redis вообще;
   backlog/evictions не считаются нигде.
8. Восстановление: устранить причину (CH память — FAIL-0205), `make -C core/modules/langfuse
   restart|logs`; контроль глубины — вручную redis-cli LLEN внутри контейнера.
9. Минимальный фикс: `--maxmemory-policy noeviction` на langfuse-redis (одна строка,
   переполнение станет громким отказом записи вместо тихой потери).

---

### FAIL-0200 · CRITICAL · langfuse-redis: allkeys-lru на очереди ingestion — тихая эвикция трейсов
- Scenario: S2. Backlog (деградация worker/clickhouse/s3 или всплеск трафика) → used_memory
  упирается в 64mb → LRU начинает выселять ключи очереди; ingestion продолжает отвечать 200.
- Evidence: `core/modules/langfuse/docker-compose.base.yml:181-182` — `--maxmemory 64mb
  --maxmemory-policy allkeys-lru`; :76 — `REDIS_CONNECTION_STRING: "redis://langfuse-redis:6379"`
  (event queue); :75 комментарий «Redis — event queue for ingestion pipeline». Контраст с
  каноном платформы: `core/modules/redis/docker-compose.base.yml:58` — «queues get noeviction
  Redis elsewhere» (owner verdict wave-redis) — принцип признан, но применён наоборот.
- Ответы 1-9: см. S2-сводка (пункты 1-6, 8-9); alert — нет (FAIL-0204).
- Auto-recovery: контейнер здоров; данные уже потеряны — recovery невозможен.
- Broken state: постоянная недоставка событий без единого симптома в метриках.
- Confidence: high (политика эвикции в конфиге — факт; точное поведение BullMQ/Lisocab-
  очередей langfuse при эвикции — high-probability, HYPOTHESIS на уровне internals образа).
- Action (config, 1 строка): `--maxmemory-policy noeviction`; опционально maxmemory 96mb
  в лимите 128M. Переполнение → видимая ошибка записи вместо тихой порчи.

### FAIL-0201 · HIGH · Отсутствует alert на недоступность обоих redis — outage молча
- Scenario: S1. redis-демон упал/завис; redis-exporter и Grafana продолжают работать.
- Evidence: `core/modules/monitoring/config/prometheus.yml.tmpl:126-133` — единственный job
  `redis-exporter` → `redis-exporter:9121` (метрики `redis_up`, `redis_evicted_keys_total`);
  `platform-alerts.yml` — 4 правила (deploy/image/backup), redis-метрик нет;
  `monitoring/config/alerting/alert-rules.yml:81` — `up == bool 0`: срабатывает ТОЛЬКО при
  смерти самого exporter-контейнера, а не redis. `dashboards/redis.json` — только визуал.
  Для langfuse-redis exporter нет вовсе (FAIL-0204).
- Ответы: 7-alert = НЕТ; 3-auto-recovery = да, но 10+ мин окно watchdog (watchdog.py:82),
  при RestartCount>5 watchdog сдаётся молча (:90); 6-user impact — неопределённо долго.
- Broken state: затяжной невидимый outage кеша/очереди.
- Retry: n/a. Confidence: high (проверены все три файла правил + шаблон prometheus).
- Action (config): добавить в platform-alerts.yml native-rule:
  `expr: redis_up{instance~".*"} == 0 or absent(redis_up)` (for: 2m, severity critical);
  вторым правилом — `rate(redis_evicted_keys_total[5m]) > 0` (warning) — ранний сигнал
  и для FAIL-0200 до появления noeviction.

### FAIL-0202 · HIGH · Общий redis: maxmemory == cgroup limit — OOM-kill рестарт-петля
- Scenario: S1. allkeys-lfu держит dataset у maxmemory=256mb; RSS = dataset + фрагментация +
  client buffers + COW → регулярно превышает deploy.resources.limits.memory: 256M
  → cgroup OOM-kill → restart: always → петля «поднялся-наполнился-убит».
- Evidence: `core/modules/redis/docker-compose.base.yml:47-48` — `limits: memory: 256M`;
  :64 — `--maxmemory 256mb`; значения равны байт-в-байт (268435456). Каноническая практика —
  maxmemory ≈ 75-80% контейнерного лимита. Watchdog лечит только unhealthy, OOM-killed
  контейнер успевает перезапускаться healthy между смертями (RestartCount>5 → сдача, :90).
- Ответы: 1 — циклическая деградация кеша (hit-rate обнуляется каждые N минут);
  2 — redis/docker-compose.base.yml:35-65; 3 — формально да (рестарт), фактически outage;
  4 — нет данных (cache); 5 — retry безопасен; 6 — деградация производительности проектов;
  7 — НЕТ (FAIL-0201); 8 — `make -C core/modules/redis restart-hard` после поднятия лимитов;
  9 — фикс ниже.
- Confidence: medium-high (геометрия лимитов — факт; частота срабатывания зависит от нагрузки).
- Action (config, 2 строки): `maxmemory 192mb` ИЛИ `limits.memory: 320M`.

### FAIL-0203 · HIGH · Контракт PLATFORM_REDIS_URL обещает «кэш/очереди» — фактура cache-only
- Scenario: S1+S2. Проект по канону берёт `PLATFORM_REDIS_URL` и кладёт туда очередь/состояние
  → элементы теряются при eviction (allkeys-lfu!) и при каждом рестарте (no persistence).
- Evidence: root `AGENTS.md:208` — «`redis` | … (`PLATFORM_REDIS_URL`) | Общий кэш/**очереди**»;
  `core/platform-infra.yaml:107-112` — provides.redis url_template `redis://redis:6379/0`,
  сети shared-cache-net; фактура: `core/modules/redis/docker-compose.base.yml:12-14` —
  `--save "" --appendonly no`, «NO volumes»; :65 — `allkeys-lfu`. Шаблон потребителя:
  `templates/template-backend/README.md:27` — `redis.from_url(settings.redis_url)`.
  Owner-verdict (:17-19) прямо предполагает очереди «elsewhere» — но elsewhere не существует.
- Ответы: 5-retry НЕ безопасен для queue-консюмера (потеря безвозвратна, тишина — lfu);
  6-user impact — тихая порча данных проекта; 7-alert — нет; 9-фикс — правка одной строки
  доков + guard.
- Confidence: high (все цитаты дословные).
- Action (runbook/docs, 0 кода): в AGENTS.md:208 и AI-PLATFORM-каноне заменить назначение
  на «Общий кэш (НЕ для очередей/состояния: eviction+no persistence)»; queues — только
  выделенный redis с noeviction. Опционально: пункт в DO-NOT списка контракта окружения.
