# FAIL-findings · S1/S2 redis/queues — часть 2 (FAIL-0204..0208 + launch-blockers)
Продолжение findings-redis-queue.md (R1: этот файл старше по NN — читать вместе).

### FAIL-0204 · HIGH · Ingestion-конвейер langfuse невидим для мониторинга — backlog необнаруживаем
- Scenario: S2. Worker/CH деградируют → очередь растёт минуты/часы; первый симптом —
  жалобы «трейсов нет». Ни одной метрики конвейера не собирается.
- Evidence: `core/modules/monitoring/config/prometheus.yml.tmpl:47-157` — полный список jobs
  (prometheus, litellm, cadvisor, node-exporter, nginx-exporter, clickhouse, status-page,
  redis-exporter, postgres-exporter, platform-projects): **langfuse/langfuse-worker отсутствуют**;
  `infra-metrics/docker-compose.base.yml:227` — exporter смотрит ТОЛЬКО
  `--redis.addr=redis://redis:6379`, langfuse-redis не скрапится; langfuse-redis живёт на
  shared-db-net (langfuse compose:189-192) — observability-net недоступен ему даже для будущего
  exporter. Docker-healthcheck worker'а (`:3030/api/health`, compose:150) — liveness, глубину
  очереди не показывает.
- Ответы: 7-alert = НЕТ полностью; 3-auto-recovery контейнеров есть, но backlog-стагнацию
  не чинит; 8-восстановление — только ручной `redis-cli -h langfuse-redis` LLEN внутри сети.
  Остальные пункты — см. S2-сводка.
- Confidence: high (отсутствие — проверяемый факт по полному списку jobs).
- Action (config, минимальный): второй инстанс redis_exporter с
  `--redis.addr=redis://langfuse-redis:6379` + job в prometheus.yml.tmpl + alert
  evictions>0 / redis_up==0. Опционально позже — scrape метрик worker'а.

### FAIL-0205 · MED · ClickHouse — терминал очереди с задокументированной историей рестарт-петли по памяти
- Scenario: S2. CH упирается в лимит/пиды → restart-петля (уже случалась) → worker не может
  писать → очередь langfuse-redis растёт → каскад в FAIL-0200 (эвикция при noeviction — громкий отказ).
- Evidence: `core/modules/clickhouse/docker-compose.base.yml:67-77` — TRAP[BUG]: «MEMORY_LIMIT_EXCEEDED
  на старте → restart-петля (17 рестартов)», «pids 256 исчерпывался… → unhealthy»; текущие
  пределы memory 3G/pids 512; :62 `stop_grace_period: 60s — merge queue flush`.
- Ответы: 1 — стагнация аналитики + рост ingestion-очереди; 2 — clickhouse service :40;
  3 — restart: unless-stopped + watchdog (до RestartCount>5); 4 — данные CH персистентны
  (volume clickhouse-data); 5-retry — безопасен, worker доиграет после починки;
  6-user impact — задержка трейсов/дашбордов; 7-alert — косвенный: Grafana ServiceDown увидит
  смерть контейнера (up==0), HighMemory (>90% 3G) предупредит заранее; 8 — `make healthcheck`,
  `make -C core/modules/clickhouse logs|restart`; 9 — фикс не требуется сверх FAIL-0200/0204
  (эвикция была единственным путем потери; петля уже лечена v1.0.1).
- Confidence: high по фактуре, medium по вероятности повтора.

### FAIL-0206 · MED · hermes-agent в prod не имеет shared-cache-net — test/prod дрейф, deps-check всегда warn
- Scenario: S1. hermes объявляет REDIS_HOST=redis и проверяет его в deps-healthcheck,
  но в production его сеть не включает shared-cache-net → DNS `redis` неразрешим.
  В test-overlay сеть добавлена вручную — поведение тестов и прода расходится.
- Evidence: prod `hermes-agent/docker-compose.base.yml:160` — `REDIS_HOST: "${REDIS_HOST:-redis}"`;
  :175-178 — networks: proxy-net, hermes-agent-net, observability-net (shared-cache-net НЕТ);
  test `docker-compose.test.yml:37` — `- test-shared-cache-net # ⚡ redis:6379 (agent state…)`;
  `healthcheck_deps.py:61` — «redis_ok — optional (warn only)» → вердикт healthy независимо.
- Ответы: 1 — постоянный warn-шум + любой redis-функционал hermes молча отключён
  (HYPOTHESIS: используется ли state/conversation-history в overlay-коде — требует проверки);
  2 — base.yml:160/175; 3 — n/a; 4 — нет; 5 — n/a; 6 — сейчас none (опциональность);
  7 — нет; 8 — n/a; 9 — либо убрать REDIS_* из prod-env (честная фактура), либо добавить
  shared-cache-net в prod-сети. До launch достаточно зафиксировать решение.
- Confidence: high (дрейф сетей — факт), hypothesis по использованию state.

### FAIL-0207 · LOW · langfuse-redis: AOF everysec — окно потери ≤1s записей очереди при crash
- Evidence: `core/modules/langfuse/docker-compose.base.yml:177-182` — `--appendonly yes
  --appendfsync everysec`; :193-194 volume `langfuse-redis-data:/data`; :168 stop_grace 30s.
- Ответы: 1 — при OOM/power-loss теряется ≤1с enqueue; 3 — auto (restart unless-stopped +
  AOF replay); 4 — короткая неконсистентность, самолечится; 5 — retry SDK покрывает
  (HYPOTHESIS); 6 — ничтожно; 7 — нет (покрывается FAIL-0204); 8 — n/a; 9 — не требуется
  (alwaysfsync = цена latency ради трейсов не оправдана).
- Confidence: high. Action: none (принятый trade-off, задокументирован этим finding'ом).

### FAIL-0208 · LOW · Общий redis: рестарт = полная потеря ключей by design — проверить консюмеров
- Evidence: `core/modules/redis/docker-compose.base.yml:14` «NO volumes», :54-59 TRAP owner
  verdict «cache-only»; modules/AGENTS.md restart-allowlist: «redis (no-volume cache-only,
  потеря = пересоздание)».
- Ответы: 1/4 — потеря всех ключей при любом рестарте ноды/контейнера — осознанное решение;
  5-retry безопасен ТОЛЬКО если консюмеры трактуют redis как чистый кеш; 6 — cache-miss storm
  после рестарта; 7 — нет; 8 — `make converge`; 9 — runbook-заметка «после рестарта redis
  ожидаем cold cache» + связь с FAIL-0203.
- Confidence: high. Action: none сверх FAIL-0203 (документация контракта).

## Launch-blockers candidates (по убыванию risk-reduction/churn)
| Приоритет | ID | Фикс | Цена |
|-----------|----|------|------|
| 1 | FAIL-0200 | noeviction на langfuse-redis | 1 строка compose |
| 2 | FAIL-0201 | alert redis_up==0/absent + evictions>0 | ~15 строк YAML |
| 3 | FAIL-0202 | maxmemory 192mb (или лимит 320M) | 1 строка |
| 4 | FAIL-0203 | правка контракта «кэш/очереди» → «кэш» | docs |
| 5 | FAIL-0204 | второй redis_exporter для langfuse-redis | config, после launch допустимо |
Позиции 1-3 рекомендованы как pre-launch (config-only, устраняют классы «тихая потеря»
и «невидимый outage»); 4 — docs-gate до первого стороннего проекта на redis.
