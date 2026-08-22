# Findings: DB failure-modes (S1/S2) — часть 2 (FAIL-0105–0108)

$ARTIFACT_CONTRACT
- PURPOSE: Продолжение findings-db.md (split >150 строк); тот же аудит-протокол, 9 вопросов на finding
- SCOPE: S2-каскады (litellm, backup) и операционные ловушки конфигурации БД
- REQUIRES: findings-db.md (FAIL-0100–0104)

### FAIL-0105 · MED · LiteLLM переживает падение БД «зелёным»: healthcheck без БД → слепая зона LLM-фасада
- scenario: S2 — postgres/pgbouncer down при работающем litellm
- evidence: `core/modules/litellm/docker-compose.base.yml:136-141` (healthcheck `/health/liveliness`); TRAP :125-130 — readiness (проверяет БД) сознательно отклонён («сбой БД = рестарт-цикл»); `DATABASE_URL … pgbouncer:6432/litellm` :75; scrape litellm `/metrics` требует Bearer (`prometheus.yml.tmpl:59`)
- Q1: контейнер остаётся healthy и в стеке; операции, требующие PG (virtual-key auth, spend_logs, dashboard), фейлятся; passthrough под master-key продолжает проксировать (HYPOTHESIS: поведение Prisma при потере соединения — reconnect после восстановления PG)
- Q2: точка — prisma→DATABASE_URL внутри litellm (compose:75)
- Q3: авто-да после восстановления postgres/pgbouncer (переподключение пула)
- Q4: частичная потеря spend_logs за окно простоя (не критично для launch)
- Q5: клиентский retry безопасен
- Q6: проекты с virtual keys видят 5xx/auth-error от LLM-фасада без видимой причины «БД»
- Q7: alert косвенный ЕСТЬ: LLMAPIErrors warning >0.1/s (`alerting/alert-rules.yml:293`)
- Q8: `make -C core/modules/litellm logs`; `make -C core/modules/litellm restart` после восстановления БД
- Q9 (fix, ops-runbook): задокументировать симптом «litellm 5xx + ServiceDown postgres-exporter» → чинить postgres, litellm НЕ трогать; менять healthcheck не предлагать (осознанный TRAP)
- confidence: med · action: строка в runbook

### FAIL-0106 · MED · Ночной pg_dumpall при недоступном postgres пропускается молча до суточного окна алерта — RPO-дыра до ~48ч
- scenario: S1-runtime × backup — postgres down в окно 03:00 UTC
- evidence: `core/modules/backup-cron/scripts/crontab:28` (0 3 * * *); `backup_postgres.py:222-224` (`pg_dumpall failed… exit 1`, IMP:10 в лог контейнера); алерт только freshness: Grafana BackupFreshness — for 30m + time-gate 07:00–07:59 МСК + repeat_interval 24h (`alerting/alert-rules.yml:58-61,348-426`); native PlatformBackupStale >25h (`platform-alerts.yml:74-79`)
- Q1: дамп не создаётся; следующий шанс — сутки; RPO-гарантия «24ч» нарушается незаметно для оператора днём
- Q2: cron-job backup-postgres.sh → run_backup (backup_postgres.py:148)
- Q3: авто — только следующей ночью; если postgres к тому моменту жив — дамп будет
- Q4: broken state нет, но RPO-хвост растёт; WAL-sync (hourly, crontab:42) продолжает страховать PITR-слой
- Q5: ручной `make backup` после восстановления postgres — да, безопасен
- Q6: оператор узнаёт не раньше утреннего окна BackupFreshness (critical) или PlatformBackupStale (>25h)
- Q7: alert есть, но с задержкой ≥26ч по конструкции (осознанно: анти-флаппинг)
- Q8: восстановление данных — `make restore DUMP_FILE=<последний валидный>` (postgres Makefile:49, pre-restore snapshot)
- Q9 (fix, ops-runbook): пункт в release/incident-runbook: «после любого простоя postgres — немедленный ручной make backup»; опционально алерт на лог `CRITICAL: pg_dumpall failed` (Loki, паттерн BackupUploadFailure)
- confidence: high · action: runbook + опциональный Loki-алерт

### FAIL-0107 · LOW · Restart-policy drift: module.yaml объявляет always, compose — unless-stopped (DR/ребут-ловушка)
- scenario: S1 — нода ребутнулась после того, как postgres был остановлен вручную (docker stop / make down)
- evidence: `core/modules/postgres/module.yaml:25-27` («restart: always… carve-out W3-R7») vs `docker-compose.base.yml:45` (`restart: unless-stopped`)
- Q1/Q6: при `unless-stopped` Docker ПОМНИТ ручной stop: после ребута ноды postgres НЕ стартует сам → полный каскад S2 при «вроде рабочей» ноде
- Q2: расхождение двух деклараций политики
- Q3: нет (это и есть суть unless-stopped); Q4: данные целы; Q5: retry = `make up MODULES=postgres`
- Q7: alert появится только через ServiceDown (если monitoring поднялся раньше и exporter скрейпится — HYPOTHESIS порядок старта)
- Q8: `make up MODULES=postgres` + `make healthcheck NODE=<n>`
- Q9 (fix, config one-liner): выровнять base.yml до `always` (carve-out уже задокументирован в module.yaml) — устраняет drift и DR-ловушку
- confidence: high · action: однострочный конфиг-коммит

### FAIL-0108 · LOW · Ёмкость коннектов: max_connections=30 (postgres) против MAX_CLIENT_CONN=50 (pgbouncer) без мониторинга сатурации
- scenario: S1/S2-деградация — рост числа проектов/пулов → queueing в pgbouncer
- evidence: `config/postgresql.conf` (`max_connections = 30`); `docker-compose.base.yml:110-112` (`MAX_CLIENT_CONN: "50"`, `DEFAULT_POOL_SIZE: "10"`); pool_mode=transaction (:109)
- Q1: при исчерпании server-коннектов pgbouncer ставит клиентов в очередь → таймауты приложений при формально здоровых контейнерах
- Q2: пароемтры пулера/лимиты PG выше
- Q3: само рассасывается при спаде нагрузки; Q4: broken state нет; Q5: retry безопасен
- Q6: спорадические таймауты запросов у проектов; Q7: alert НЕТ на сатурацию (pgbouncer вне scrape — см. FAIL-0100); косвенно postgres-exporter даёт pg_stat_activity-метрики для дашборда
- Q8: `docker logs pgbouncer`; тюнинг через env compose
- Q9 (fix, config+мониторинг): закрыт общим действием FAIL-0100 (экспортёр pgbouncer) + при необходимости raise max_connections до 50 (память позволяет после фикса FAIL-0103)
- confidence: low-med · action: вместе с FAIL-0100

## Сводка
| Severity | IDs |
|----------|-----|
| CRITICAL | 0101 |
| HIGH | 0100, 0102, 0104 |
| MED | 0103, 0105, 0106 |
| LOW | 0107, 0108 |

Launch-blocker кандидаты: FAIL-0101 (конфиг+wrapper), FAIL-0100 (мониторинг pgbouncer), FAIL-0102 (PARTIAL≠success).
Позитивный фон (для полноты): postgres-down детектируется ServiceDown critical ≤1m через postgres-exporter; restart-политики поднимают упавшие контейнеры; данные защищены volume+WAL+nightly dump+hourly WAL-sync; restore-таргет существует.
