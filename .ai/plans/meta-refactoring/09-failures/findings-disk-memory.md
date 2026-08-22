# FAIL-findings · S1 Disk full (research-only, код не менялся)

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: failure-mode disk-full — рост логов/WAL/TSDB/дампов/образов, порядок отказа, alert-покрытие
## @scope core/modules/* (compose+config), docker_installer/docker_registry_auth, lifecycle helpers, monitoring alerts
## @rationale максимум снижения риска / минимум churn: config > runbook > точечный код
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures
## IMPACTS launch-blockers candidates (см. findings-disk-memory-002.md §Launch-blockers)

## Контекст диска (единый fs)
Single-VPS: все named volumes — docker-managed на /var/lib/docker; bind-тома —
/var/lib/platform/{postgres-data,wal-archive,backup-spool,hermes-agent/data} +
/var/log/platform/backup (docker-compose.yml:53-82). Всё на mountpoint=/ —
DiskSpace-алерт по `mountpoint="/"` покрывает весь стек (alert-rules.yml:243).

### FAIL-0500 · LOW · Контейнерные логи ротируются на всех уровнях (positive control)
- scenario: рост json-file логов docker — классический disk-full драйвер.
- evidence: x-logging `json-file, max-size 50m, max-file 3` во ВСЕХ 13 compose
  (напр. core/modules/postgres/docker-compose.base.yml:30-34; postgres:55 `logging: *default-logging`);
  daemon.json default — `docker_installer.py:64-65` `"log-driver": "json-file", "log-opts": {max-size 50m, max-file 5}`.
- 9 ответов: 1) логи капятся per-container ≤150M; 2) отказ невозможен; 3) n/a; 4) нет; 5) n/a;
  6) нет; 7) n/a; 8) n/a; 9) ничего.
- confidence: HIGH (grep по всем compose — 13/13 с якорем).
- action: none.

### FAIL-0501 · MED · daemon.json — два писателя с разными log-opts (drift)
- scenario: порядок фаз решает, какой дефолт достанется контейнерам БЕЗ своего logging-якоря.
- evidence: `docker_installer.py:64-65` пишет 50m×5; `docker_registry_auth.py:267`
  `_write_daemon_json` добавляет `{"max-size": "10m", "max-file": "3"}` — skip-гейт
  (строка 259) сравнивает именно с 10m×3. configure_daemon (installer:156-173) на
  существующем файле мёржит ТОЛЬКО live-restore, log-opts не трогает.
- 9 ответов: 1) при φ1-до-φ6 порядке побеждает 50m×5; при пред-существующем daemon.json
  без log-driver registry_auth впишет 10m×3 навсегда; 2) docker_registry_auth.py:_write_daemon_json:241;
  3) нет (тихая конвергенция в один из вариантов); 4) не broken, но недетерминизм;
  5) n/a; 6) нет (все платформенные сервисы перекрыты якорями 50m×3); 7) нет;
  8) ручная правка /etc/docker/daemon.json + restart; 9) единый SoT log-opts-констант
  (shared-модуль или ссылка installer→registry_auth).
- confidence: HIGH (оба писателя прочитаны).
- action: выровнять константы (5 строк), не блокер.

### FAIL-0502 · HIGH · Disk-full: postgres — первый отказник; restart-петля до ручной чистки
- scenario: заполнение / → pg_wal не может расти → postgres PANIC/shutdown → restart-loop.
- evidence: `postgresql.conf:84` `archive_command = 'cp %p /var/lib/platform/wal-archive/%f'`
  (cp на тот же fs), `:86` wal_keep_size=1024; volume postgres-data bind
  /var/lib/platform/postgres-data (docker-compose.yml:53-58). P0-прецедент порчи WAL
  закрыт: TRAP[BUG] 2026-08-03 postgresql.conf:69-83 (pg_archivecleanup удалён из
  archive_command; восстановление сегмента вручную — «0 потерянных committed-строк»).
- 9 ответов: 1) ENOSPC на WAL-записи → crash + recovery; при продолжающемся disk-full —
  crash-loop; 2) postgres service (postgres/docker-compose.base.yml:37) + archive_command;
  3) restart: unless-stopped — бесконечные рестарты, сами не излечатся;
  4) данные целы (WAL fsync, crash-safe), НО недоступность всех БД платформы
  (pgbouncer:98 depends service_healthy → весь shared-db-net деградирует);
  5) retry безопасен ПОСЛЕ освобождения места; до — петля; 6) полный outage БД-зависимых
  сервисов (litellm, langfuse, проекты);
  7) ДА: DiskSpace CRITICAL <20% (alert-rules.yml:243, for 0s) + ServiceDown
  (alert-rules.yml:81) — НО см. FAIL-0504 (мессенджер может умереть первым);
  8) docker system prune -af --filter until=720h (канон system.py:727) + чистка
  backup-spool/wal-archive; runbook RTO/RPO core/AGENTS.md §«Безопасность данных»;
  9) early-warning tier алерта (<35% warning) — см. FAIL-0504.
- confidence: HIGH (механика PG ENOSPC — документированное поведение; путь томов подтверждён).
- action: алерт-tier + runbook-строка «disk-full → prune» (см. -002).

### FAIL-0503 · MED · wal-archive растёт при недоступности S3 (safe-delete блокирует retention)
- scenario: S3 outage N дней → wal_sync exit 1 → локальные WAL не удаляются (D3:
  удаление только после HEAD-подтверждения) → накопление в wal-archive volume.
- evidence: `wal_sync.py:13-14` (D3 safe-delete), `:19` «S3-ошибка в sync → IMP:10 + exit 1»,
  `:80-82` retention 7d local / 14d S3, rate-limit 200 PUT/прогон (:82);
  `postgresql.conf:85` archive_timeout=60 — сегменты генерятся непрерывно даже idle;
  crontab:42 — hourly прогон.
- 9 ответов: 1) при write-heavy >200 сегментов/час (~3.2GB/ч) локальный архив обгоняет
  upload; при idle — рост медленный (мелкие partial-сегменты); 2) wal-archive volume
  (docker-compose.yml:59-64); 3) нет — by design (RPO-гарантия важнее диска);
  4) не broken, диск-давление; 5) retry (hourly cron) безопасен и идемпотентен (D2 HEAD);
  6) косвенный — вклад в disk-full (FAIL-0502); 7) ДА: WalSyncFailure WARNING
  (alert-rules.yml:486-532) + DiskSpace; 8) восстановление S3 → догоняет за часы;
  9) none (осознанный trade-off D3; алерт есть).
- confidence: HIGH.
- action: none (документированный trade-off; следить через DiskSpace).

### FAIL-0504 · HIGH · Disk-full ослепляет мониторинг: DiskSpace noDataState=OK — тишина
- scenario: prometheus-data на том же fs; при ENOSPC Prometheus перестаёт аппендить
  сэмплы → DiskSpace/HighMemory возвращают stale/пусто → noDataState OK → алерт НЕ уходит.
- evidence: `monitoring/docker-compose.base.yml:104-105` `--storage.tsdb.path=/prometheus`,
  `:105` retention.time=15d (флага retention.size НЕТ); volume prometheus-data
  (docker-compose.yml:85); `alert-rules.yml:275` DiskSpace `noDataState: "OK"`
  (и HighMemory:223 `noDataState: "OK"`).
- 9 ответов: 1) постепенный рост → DiskSpace CRITICAL сработает заранее (20% free);
  быстрый скачок (build-cache+дамп+логи за час) → fs заполняется между скрейпами →
  Prometheus немеет → 0 уведомлений; 2) monitoring/prometheus + alert-rules.yml:275;
  3) нет; 4) broken: слепое пятно ровно в момент инцидента; 5) n/a;
  6) outage без alert (определение HIGH); 7) частично: BackupFreshness CRITICAL
  (alert-rules.yml:348, noDataState Alerting) сработает на следующее утро (time-gate 07:00 МСК,
  :343-347) — единственный независимый сигнал, окно задержки ~сутки; 8) ручной ssh df -h;
  9) noDataState: Alerting для DiskSpace/HighMemory (2 строки) + опционально
  host-level df-канарейка (systemd timer → Telegram) вне docker-стека.
- confidence: HIGH (noDataState прочитан; поведение Prometheus при ENOSPC — HYPOTHESIS
  по документации TSDB: append-fail без деградации процесса).
- action: noDataState Alerting — кандидат в launch-blockers (-002).

### FAIL-0505 · MED · Build cache и образы: monthly prune не поспевает за rebuild-циклом
- scenario: каждый hermes L2 rebuild = ~+4GB build cache (system.py:746-747);
  редеплои копят неиспользуемые образы; prune — раз в МЕСЯЦ, until=720h.
- evidence: `system.py:725-729` CRON_PRUNE_LINES `0 4 1 * * docker system prune -af
  --filter until=720h` + apt-get clean; `:740` «build cache 1.6G, +4G/rebuild»;
  `:746-747` rationale «нет prune-политики... Monthly cron — компромисс»;
  Makefile/makefiles — 0 prune-таргетов (grep).
- 9 ответов: 1) между 1-ми числами месяца несколько rebuild'ов → +8-12GB cache;
  2) /var/lib/docker (build-cache, dangling layers); 3) только monthly cron;
  4) не broken, но главный диск-драйвер после логов; 5) n/a; 6) косвенный → FAIL-0502;
  7) только общий DiskSpace; 8) ручной `docker system prune -af --filter until=720h`
  (задокументирован только в коде system.py:727, в Makefile/runbook НЕТ);
  9) weekly prune cron (1 строка) или `docker builder cache prune -f` после hermes-push-l2.
- confidence: HIGH.
- action: weekly-частота или post-deploy cache-prune; runbook-строка.

### FAIL-0506 · LOW · TSDB/Loki retention настроены (positive control, один нюанс Prometheus)
- evidence: prometheus `--storage.tsdb.retention.time=15d` (monitoring compose:105) —
  но БЕЗ retention.size: кардинальный рост серий за 15d не ограничен по байтам;
  loki `retention_period: 168h` + compactor retention_enabled (loki-config.yml:88-93,117,
  D9: без compactor retention молча игнорируется — закрыто).
- 9 ответов: 1) prometheus-data растёт по числу серий×15d, не по календарю диска;
  2) prometheus-data; 3) нет size-гейта; 4) нет; 5) n/a; 6) вклад в disk-full;
  7) DiskSpace; 8) prune вручную; 9) `--storage.tsdb.retention.size=3GB` (1 флаг).
- confidence: HIGH.
- action: retention.size флаг (опционально).

### FAIL-0507 · LOW · Backup spool: пик = размер дампа + застрявшие неудачные upload'ы
- evidence: `backup_postgres.py:199-221` pg_dumpall|gzip стримит в spool (без второй
  копии); `:311-317` upload-fail → дамп ОСТАЁТСЯ в spool для retry; cleanup 04:00
  7-дней (crontab:34); BackupFreshness/BackupUploadFailure алерты (alert-rules.yml:348,433).
- 9 ответов: 1) N неудачных upload'ов × размер дампа до cleanup; 2) backup-spool volume;
  3) daily cleanup + upload.py retry 3×90мин; 4) нет; 5) retry безопасен (C1);
  6) нет (диск-давление при большой БД); 7) ДА (оба алерта); 8) ручная чистка spool;
  9) none.
- confidence: HIGH.
- action: none.

### FAIL-0508 · LOW · journald persistent без явного SystemMaxUse (дефолтные кэпы systemd)
- evidence: `system.py:496,514` Storage=persistent; SystemMaxUse НЕ пишется — действуют
  дефолты journald (≤10% fs, кэп 4G). Host-логи не станут driver'ом disk-full, но
  кэп неявный.
- 9 ответов: 1) bounded дефолтом; 2) /var/log/journal; 3) journald vacuum; 4) нет;
  5) n/a; 6) нет; 7) нет; 8) journalctl --vacuum-size; 9) явный SystemMaxUse=1G (1 строка).
- confidence: MED (поведение дефолтов по документации systemd, не проверено на ноде).
- action: опционально.

### FAIL-0509 · LOW · ClickHouse: keep-free 1GB защищает фс; устаревший комментарий ratio
- evidence: `clickhouse/config/config.d/40-storage.xml` `keep_free_space_bytes>1073741824`
  (CH откажется писать при <1GB free — фс защищена, инжест langfuse начнёт падать);
  `20-memory.xml` ratio 0.80 с комментарием «512MB * 0.80» при фактическом лимите 3G
  (compose:72) → реальный кэп ~2.4G. TTL на langfuse-таблицах — зона langfuse (не конфигом CH).
- 9 ответов: 1) при <1GB free CH перестаёт вставлять (ошибки инжеста), сервер жив;
  2) clickhouse service; 3) нет (после освобождения места пишет дальше);
  4) потеря трейсов за окно disk-full; 5) retry безопасен; 6) деградация телеметрии;
  7) DiskSpace + ServiceDown не сработают (CH жив) — только Nginx5xx/langfuse-ошибки;
  8) освободить место; 9) поправить комментарий (doc-drift).
- confidence: HIGH (конфиг), MED (поведение langfuse при ошибках инжеста).
- action: комментарий (косметика).
