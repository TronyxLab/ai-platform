# FAIL-findings · S2 Memory pressure + Launch-blockers (research-only)

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: failure-mode memory-pressure — OOM-killer, лимиты, zram, alert-покрытие; сводка launch-blockers волны 09-failures (disk+memory)
## @scope core/modules/*/docker-compose.base.yml, lifecycle helpers/system.py, alert-rules.yml
## @rationale максимум снижения риска / минимум churn: oom_score_adj > алерты > ничего
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures (part 2)
## IMPACTS launch-blockers candidates

### FAIL-0510 · MED · Инвентарь memory-limits: все long-running покрыты, сумма лимитов > RAM
- scenario: host-OOM возможен при одновременном пике нескольких сервисов.
- evidence (limits из compose): postgres 1G (:51), pgbouncer 64M, clickhouse 3G (:72),
  litellm 2G (:55), langfuse web 1536M (:97) + worker 1024M (:134) + langfuse-redis 128M,
  redis 256M (:48), loki 512M (:46), alloy 256M (:121), prometheus 512M (:89),
  grafana 512M (:169), cadvisor 512M (:54), hermes-agent 1G (:103), nginx 256M,
  minio 512M (:64), status-page/backup-cron/exporters ≤128M. Сумма ≈ 10.5G при
  7.8G RAM + zram 4G (system.py:655 «4G (50% RAM 7.8G)»).
- 9 ответов: 1) контейнер, превысивший СВОЙ лимит, убивается внутри cgroup (изолированная
  жертва) → restart unless-stopped/always = auto-recovery минутного окна;
  2) любой сервис на пике; 3) да (cgroup OOM + restart policy + watchdog cron */5,
  system.py:407); 4) нет для stateless; postgres после OOM — crash recovery (WAL);
  5) retry безопасен; 6) кратковременные 502 от убитого бэкенда; 7) ДА:
  HighMemory >90% working_set с guard limit>0 (alert-rules.yml:191,143 W2) +
  PsiMemoryPressure (alert-rules.yml:546) + ServiceDown;
  8) n/a; 9) none — инвентарь полный (0 long-running без limits).
- confidence: HIGH.
- action: none.

### FAIL-0511 · HIGH · Нет oom_score_adj ни у одного сервиса: host-OOM выберет postgres/clickhouse
- scenario: суммарное потребление > RAM+zram → kernel OOM killer выбирает жертву по
  oom_score (грубо ~RSS) без защитных приоритетов → самые жирные = postgres (1G) /
  clickhouse (3G) / litellm (2G). Убитый postgres = outage всей БД платформы до recovery.
- evidence: grep `oom_score_adj` по core/modules/** — 0 вхождений (все compose);
  прямое признание в `system.py:685`: «swapon пуст, systemd-oomd не установлен —
  OOM-killer выбирает жертву случайно (может убить postgres)»; mitigation только
  zram (FAIL-0512) + PSI-алерт (ранний warning, не защита).
- 9 ответов: 1) host-OOM kill самого RSS-объёмного процесса; 2) любой сервис БЕЗ
  cgroup-превышения — т.е. невиновный postgres может умереть из-за чужого утечки;
  3) restart policy поднимет (минуты), но если давление сохранится — цикл убийств;
  4) данные целы (WAL fsync), доступность страдает; 5) retry безопасен после снятия давления;
  6) outage БД/CH на минуты-десятки минут; 7) ДА post-factum: ServiceDown CRITICAL +
  PsiMemoryPressure WARNING заранее (5% stalled, alert-rules.yml:534-585);
  8) перезапуск стека `make up`; 9) `oom_score_adj: -500` для postgres (+ clickhouse/litellm)
  — по 1 строке на сервис в compose; переносит риск на stateless-жертв.
- confidence: HIGH (отсутствие флага — факт; выбор жертвы ядром — документированное поведение).
- action: кандидат в launch-blockers (-002): 3 строки compose.

### FAIL-0512 · LOW · zram 4G + PSI-алерт настроены (positive control)
- evidence: `system.py:659-667` ALGO=zstd SIZE=4096 PRIORITY=100 vm.swappiness=100;
  install идемпотентен/non-fatal (φ1 шаг 5.7, phases/system.py:520-525);
  PsiMemoryPressure: rate(node_pressure_memory_stalled_seconds_total[5m])>0.05 —
  ранний сигнал ДО OOM (alert-rules.yml:534-585, node-exporter собирает node_pressure_*).
- 9 ответов: 1) давление памяти сначала уходит в zram-compression, PSI растёт;
  2) n/a; 3) частично (swap амортизирует пики); 4) нет; 5) n/a; 6) замедление вместо смерти;
  7) ДА (PSI WARNING); 8) n/a; 9) none.
- confidence: HIGH.
- action: none.

### FAIL-0513 · MED · langfuse-стек: крупнейший потребитель (≈2.7G), eviction очереди теряет трейсы
- scenario: пик ingestion → langfuse-worker упирается в 1024M (cgroup OOM, рестарты);
  langfuse-redis maxmemory 64mb allkeys-lru (langfuse compose:177-182) — при переполнении
  очередь ВЫТЕСНЯЕТ события (не noeviction) → тихая потеря in-flight трейсов.
- evidence: compose:97/134/172 лимиты; redis-очередь: `REDIS_CONNECTION_STRING:
  redis://langfuse-redis:6379` (compose:76) «event queue for ingestion pipeline»;
  общий redis — cache-only allkeys-lfu by design (redis compose:60-65, owner verdict).
- 9 ответов: 1) worker OOM-рестарты; очередь eviction при >64mb;
  2) langfuse-worker:125 / langfuse-redis:159; 3) рестарты — да; потерянные события — нет;
  4) телеметрия неполна (данные платформы не затронуты); 5) retry безопасен (идемпотентный
  инжест), НО вытесненное не вернётся; 6) деградация LLM-наблюдаемости; 7) HighMemory
  (per-container) + ServiceDownShort; на eviction алерта НЕТ (redis_exporter метрика
  evicted_keys не используется в правилах — grep alert-rules.yml: 0 вхождений evicted);
  8) raise maxmemory/лимитов worker'а; 9) опционально: алерт на rate(evicted_keys)>0.
- confidence: HIGH (конфиг), MED (частота пиков на практике).
- action: опционально, не блокер.

## Launch-blockers candidates (disk+memory; максимум risk reduction / минимум churn)
| # | Finding | Фикс | Цена |
|---|---------|------|------|
| LB-1 | FAIL-0504 | DiskSpace/HighMemory `noDataState: Alerting` (alert-rules.yml:275,223) | 2 строки; закрывает «слепой инцидент» |
| LB-2 | FAIL-0511 | `oom_score_adj: -500` postgres (опц. clickhouse/litellm) | 1-3 строки compose |
| LB-3 | FAIL-0505 | weekly prune cron (until=720h→168h или отдельная строка) | 1 строка system.py |
| LB-4 | FAIL-0502/0506 | `--storage.tsdb.retention.size=3GB` + runbook «disk-full → docker system prune -af --filter until=720h» | 1 флаг + 3 строки docs-in-code |

Не-блокеры: FAIL-0501 (SoT log-opts), FAIL-0513 (evicted-алерт), FAIL-0508/0509 (косметика).

## Позитивные контроли (проверено фактически, чинить НЕ надо)
- Лог-ротация 13/13 модулей + daemon.json (FAIL-0500).
- Loki retention compactor D9; Prometheus 15d; spool cleanup 7d; WAL safe-delete D3.
- Memory limits 100% long-running; zram 4G; watchdog cron */5; chaos T7 kernel-OOM
  прогоняется e2e (tests/e2e/test_chaos_resilience.py:939).
