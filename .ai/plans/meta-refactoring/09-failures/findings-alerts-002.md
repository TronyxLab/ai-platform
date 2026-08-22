# Findings: Alerts/detection — новые дыры (FAIL-1000–1099) + операторский cheat-sheet

$ARTIFACT_CONTRACT
- PURPOSE: Детальные findings observability-покрытия вне известного списка + cheat-sheet восстановления
- SCOPE: research-only; ID-диапазон FAIL-1000–1099; evidence = file:symbol
- REQUIRES: findings-alerts.md (матрица 17 сценариев, инвентарь детекции)
- ACCEPTANCE: каждый finding — severity/evidence/action; cheat-sheet — топ-8 поломок → команда

### FAIL-1000 · HIGH · minio — единственный storage-модуль без scrape-job и без алертов
- scenario: minio crash/OOM/network-изоляция → S3-совместимое хранилище недоступно (langfuse-трейсы, hermes-артефакты), все алерты молчат
- evidence: `core/modules/monitoring/config/prometheus.yml.tmpl:47-157` — jobs: prometheus/litellm/cadvisor/node-exporter/nginx-exporter/clickhouse/status-page/redis-exporter/postgres-exporter/platform-projects; **minio отсутствует**; `config/alerting/alert-rules.yml` — ни одного minio-правила; единственный сигнал — таблица containers на status-page (`core/internal/healthcheck/metrics/docker_collector.py::get_containers`, cron 1м, display-only)
- Q7 (alert): НЕТ прямого и косвенного (5xx не порождает, если потребители деградируют тихо)
- Q8 (recovery): `make converge NODE=<n>`
- action: добавить job `minio` (`minio:9000`, path `/minio/v2/metrics/health`) + alert `up{job="minio"} == 0`; до launch — да (данные трейсинга теряются молча)

### FAIL-1001 · HIGH · нет алерта pg_up — живой postgres-exporter при мёртвом postgres = тишина
- scenario: postgres контейнер crashed/looping/не отвечает по wire-протоколу, exporter-процесс жив → `up{job="postgres-exporter"} == 1`, алертов нет до watchdog ≥10мин (если контейнер помечен unhealthy) или вечно (порт слушает, БД не отвечает)
- evidence: `config/prometheus.yml.tmpl:139-146` (job postgres-exporter); `config/alerting/alert-rules.yml` — правила только up==bool 0/memory/disk/llm/backup/psi/nginx, метки `pg_up` нет нигде (grep); exporter экспортирует `pg_up 0` при недоступности DSN (`infra-metrics/docker-compose.base.yml:13`, DATA_SOURCE_NAME → postgres:5432); fallback — `core/internal/healthcheck/watchdog.py::run_watchdog` (≥10мин, только при unhealthy)
- класс тот же, что [FAIL-0201] для redis, но критичность выше (stateful-ядро, severity=critical в module.yaml)
- action: alert `pg_up == 0 for 2m` critical; до launch — да

### FAIL-1002 · MEDIUM · loki+alloy вне scrape — лог-пайплайн умирает молча, канарейка с задержкой ~31ч
- scenario: loki/alloy crash или зависли → Nginx5xxErrors/BackupUploadFailure/WalSyncFailure переходят в NoData (noDataState OK = тишина); единственный детект — BackupFreshness noDataState Alerting, но окно 26h + for 30m + суточный time-gate 07:00–07:59 МСК → обнаружение до ~31ч, и то лишь утром
- evidence: `config/prometheus.yml.tmpl:47-157` — jobs loki/alloy отсутствуют; `config/alerting/alert-rules.yml::backup_freshness` (:419 noDataState Alerting; :343-347 time-gate D `(hour() >= 4 and hour() < 5)`), `::nginx_5xx_errors`/:632 и `::backup_upload_failure`/:472 noDataState OK
- action: scrape `loki:3100/metrics` + alloy metrics endpoint, alert up==0; канарейку оставить как second line

### FAIL-1003 · MEDIUM · продолжающийся critical-инцидент напоминает о себе раз в 24ч
- scenario: нода в огне (например postgres down, watchdog не может поднять) — оператор, пропустивший/проспавший первый пуш, получит повтор не раньше чем через сутки
- evidence: `config/alerting/contact-points.yml:88-91` — route critical `repeat_interval: "24h"`, `sendReminder: false` (DevPlan 161 W1 — анти-спам for-пробросов); group_interval 1s не помогает — повторы гасятся repeat_interval'ом
- action: компромисс 2–4h ИЛИ эскалационная лестница (первый повтор через 30м, далее 24h); анти-спам исходный кейс закрыть resolve-сообщениями (disableResolveMessage уже false)

### FAIL-1004 · MEDIUM · все warning-алерты доставляются БЕЗ push-уведомления
- scenario: LLMAPIErrors / PsiMemoryPressure / Nginx5xxErrors / BackupUploadFailure / WalSyncFailure приходят в TG silent — телефон не звонит; ранние сигналы (PSI, 5xx, upload fail) фактически прочитываются только при активном открытии чата
- evidence: `config/alerting/contact-points.yml:67` — receiver telegram-warning `disable_notifications: true` (critical-ветка :52 — false)
- HYPOTHESIS: осознанный анти-шум, но нигде не задокументирован как решение
- action: либо включить push (group_wait 5m уже гасит штормы), либо зафиксировать TRAP[DECISION] «warnings читаются асинхронно»

### FAIL-1005 · LOW · status-page stale/unhealthy никто не опрашивает автоматически
- scenario: platform-metrics cron умер / status-metrics.json перестал обновляться → страница показывает устаревшие данные молча (freshness — только HTTP-заголовок человеку); /healthz отдаёт 503 на stale, но вызывателей нет
- evidence: `core/modules/status-page/app.py::_handle_healthz` (503 при stale) и `::_handle_health` (503 при FAIL) — автоматических потребителей 0; prometheus скрейпит ТОЛЬКО `/metrics` (`prometheus.yml.tmpl:113-120`), а `_handle_metrics` возвращает 200 всегда (NaN-гейджи при stale)
- побочный эффект: stale JSON → `platform_backup_last_postgres_age_seconds` растёт → ложный PlatformBackupStale critical (`platform-alerts.yml:74`)
- action: cron/blackbox-проба `/healthz` или алерт на freshness из status.json; live-checks покрывают только 6 сервисов (`collectors/checks/platform.py::PLATFORM_SERVICES` — Grafana/Prometheus/Loki/Hermes/Langfuse/LiteLLM; БД/redis/minio/clickhouse/nginx — нет)

### FAIL-1006 · MEDIUM · низкотрафиковый vhost может отдавать 502 неделями — нет внешнего пробы эндпоинтов
- scenario: у проекта мало трафика → условие «>2 ошибок за 5м» не набирается месяцами; полный отказ одного vhost (битый upstream, истёкший internal-сертификат проекта) невидим; e2e-verify — ручной verb
- evidence: `config/alerting/alert-rules.yml::nginx_5xx_errors` (threshold C gt params [2], :600-627); blackbox-exporter в `prometheus.yml.tmpl` отсутствует; `make e2e-verify NODE=` / `make verify-domains NODE=` — manual-only (`core/entrypoint-manifest.yaml`)
- action: переиспользовать `core/internal/verify_sweep` периодическим cron'ом на ноде (без новой инфраструктуры) ИЛИ blackbox-exporter на проектные vhosts

### FAIL-1007 · LOW · retention-окно post-mortem: Prometheus 15d, Loki 7d
- scenario: инцидент замечен по графику спустя >15 дней (или лог >7 дней) — данных для разбора нет; week-over-week сравнение невозможно
- evidence: `core/modules/monitoring/docker-compose.base.yml:105` (`--storage.tsdb.retention.time=15d`); `core/modules/logging/config/loki-config.yml` (retention_period: 7d, compactor retention_enabled)
- action: принять как явную операционную границу (зафиксировать в AGENTS.md §DevOps) или поднять Prometheus до 30d при запасе диска (оценить рост TSDB перед изменением)

### FAIL-1008 · LOW · пустой LITELLM_MASTER_KEY → ложный ServiceDown(litellm) critical
- scenario: HYPOTHESIS — секрет не задан на этапе init → sed подставляет пустой bearer_token → каждый scrape 401 → `up{job="litellm"} == 0` → ServiceDown critical «litellm is down» при полностью живом litellm
- evidence: `core/modules/monitoring/docker-compose.base.yml:58-65` (sed `'s/$${LITELLM_MASTER_KEY}/'"${LITELLM_MASTER_KEY}"'/g'` + `environment: LITELLM_MASTER_KEY: "${LITELLM_MASTER_KEY:-}"` — default пустой, set -eu не падает); `config/prometheus.yml.tmpl:59` (`bearer_token: "${LITELLM_MASTER_KEY}"`)
- action: guard в init («token empty → exit 1») или отдельный LITELLM_METRICS_TOKEN с проверкой непустоты

## Операторский cheat-sheet — топ-8 поломок → восстановление

| # | Поломка (сигнал) | Восстановление |
|---|------------------|----------------|
| 1 | Postgres down (ServiceDown/watchdog.restart, TG critical) | Диагноз: `make healthcheck NODE=<n>` → реконсиляция: `make converge NODE=<n>`; данные побиты: `make restore DUMP_FILE=<path> NODE=<n>` |
| 2 | Redis down (watchdog.restart / ServiceDownShort) | `make converge NODE=<n>` — cache-only, потеря допустима; verify: `make healthcheck NODE=<n>` |
| 3 | Проект отдаёт 502/сломанный релиз (Nginx5xxErrors, жалобы) | `make project-status NAME=<n>` → emergency-редеплой: `make deploy-project PROJECT=<dir> NODE=<n>` → verify: `make verify-domains NODE=<n> PROJECT=<n>` |
| 4 | Vhost/nginx конфиг сломан после деплоя | `make render-vhosts NODE=<n>` → `make verify-domains NODE=<n>` → `make converge NODE=<n>` |
| 5 | Диск <20% (DiskSpace critical) | На ноде: `docker system prune -f` (+ старые образы `docker image prune -a`) → verify: `make status NODE=<n>`, `make e2e-verify NODE=<n>` |
| 6 | Миграция сломала проект (deploy.failed/deploy.rollback critical) | Образ откатился автоматически (healthcheck-rollback); схема НЕ откатывается → fix-forward: `git push` исправления; статус: `make project-status NAME=<n>` |
| 7 | Нода после reboot не поднялась / стек частично жив | `make node-update NODE=<n>` (φ9–φ13 идемпотентно) → full sweep: `make e2e-verify NODE=<n>`; крайний случай: `make bootstrap-node NODE=<n>` |
| 8 | Креды/ключи (cert.expiry critical / auth-failure в CI) | TLS: acme.sh renew (cron auto) → `make verify-domains NODE=<n>`; AGE/secrets: `make secrets-unlock NODE=<n>`; LiteLLM keys: `make provision-llm`; ротация SSH/CI — runbook core/AGENTS.md §«Ротация SSH/CI-ключей» |

Правило канала: тишина в Telegram ≠ «всё хорошо» — канал имеет SPOF [FAIL-0303], warnings приходят без пуша [FAIL-1004], мёртвый монитор сам не алертит [FAIL-0402]. Периодическая ручная сверка — `make e2e-verify NODE=<n>` + status-page.
