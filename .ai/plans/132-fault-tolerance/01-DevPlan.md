# 132-fault-tolerance — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Повысить отказоустойчивость серверов платформы простыми средствами (без смены архитектуры): авто-рестарт unhealthy-контейнеров (watchdog), снижение RPO через выгрузку WAL-архива в S3 + retention, journald→Loki (Debt D-1 из 126), Telegram failure-маркеры (Debt D-2 из 126), алерты на свежесть/провалы бэкапов. Memory limits (рекомендация B) ИСКЛЮЧЕНЫ по решению пользователя.
DESCRIPTION:           6 волн: W0 — фиксация; W1 — watchdog (host cron + Python, канон install_cron_metrics); W2 — wal_sync.py (WAL→S3 hourly, safe-delete локальных после HEAD-подтверждения, S3-side retention); W3 — promtail journald-скрейп + journald Storage=persistent; W4 — failure-маркеры IMP:9 в telegram_notifier (send_telegram + notify, фикс лживого лога «Notification sent»); W5 — Grafana-правила BackupFreshness/BackupUploadFailure/WalSyncFailure на Loki datasource; W6 — верификация (per-task test-summary → make check → make gate MODE=fast). Каждая волна автономна, с unit-тестами (R1-R5 Test Honesty).
RATIONALE:             Гэпы подтверждены аудитом 2026-08-03: (1) restart-политики перезапускают только упавшие контейнеры — «живой, но unhealthy» висит вечно (HealthcheckPoller/is_n_loop только детектят); (2) WAL-архив пишется в /var/lib/platform/wal-archive вечно (нет retention) и НЕ выгружается в S3 (rsync-строка закомментирована в backup_postgres.py:55) — RPO=24ч, риск ENOSPC; (3) journald (docker daemon, OOM, systemd) не попадает в Loki — Debt D-1; (4) telegram_notifier логирует провалы только на IMP:7 и notify() пишет «Notification sent» даже при неудаче — Debt D-2; (5) в alert-rules.yml 0 правил по бэкапам. Все решения — в существующем каноне (host cron паттерн /etc/cron.d, backup-cron модуль, promtail, Grafana provisioning, shared/telegram_notifier), без новой инфраструктуры.
ACCEPTANCE_CRITERIA:   (1) W1: watchdog.py покрыт unit-тестами (unhealthy→restart, cooldown, исключение restart:"no", dry-run, is_n_loop-guard); install_cron_watchdog идемпотентен (второй вызов = no-op). (2) W2: wal_sync.py покрыт unit-тестами (scan/HEAD/PUT, safe-delete только после S3-подтверждения, S3-side retention, dry-run, rate-limit); crontab + Dockerfile COPY + compose-монтирования wal-archive (base + test-канон U-62). (3) W3: journald-скрейп в promtail-config.yml + тома в compose; journald Storage=persistent идемпотентно. (4) W4: failure-маркер IMP:9 в send_telegram и notify; негативные тесты (R5: _negative/_original_form). (5) W5: 3 правила в alert-rules.yml (Loki datasource uid "loki"), структура валидируется тестом. (6) W6: per-task `make test-summary TEST_FILE=...` зелёный, `make check` чист, `make gate MODE=fast` зелёный. (7) 126 D-1/D-2 закрыты (ссылки в Debt-реестре 126 при наличии).
IMPLEMENTS:            Рекомендации аудита 2026-08-03 (приоритеты A, C, D, E + Debt D-1/D-2 из 126-chaos-resilience); решение пользователя 2026-08-03 («все, кроме memory limits»).
IMPACTS:               tronyx-vps (production, после деплоя волн): новый host cron /etc/cron.d/platform-watchdog; backup-cron образ (новый скрипт + crontab); promtail (journal-скрейп, тома); Grafana alert-rules; telegram_notifier (логи). Код: core/internal/healthcheck/watchdog.py (новый), core/internal/bootstrap/lifecycle/helpers/system.py, phases/system.py, core/modules/backup-cron/*, core/modules/logging/*, core/internal/shared/telegram_notifier.py, core/modules/monitoring/config/alerting/alert-rules.yml, tests/unit/*.
REQUIRES:              Зелёный baseline (make check + gate MODE=fast) до старта; решения W0 (ниже); для нодевой проверки — доступ к tronyx-vps (опционально, W6); S3_* креды уже в backup-cron (изменений secrets нет).
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <!-- W1: watchdog -->
  <entity name="core_internal_healthcheck_watchdog_py" TYPE="MODULE"
    keywords="watchdog,unhealthy,restart,cooldown,dry-run,stdlib-only"
    annotation="Host-сторона: docker ps/inspect → unhealthy ≥2 проверки подряд (10 мин) → docker restart. State: /run/platform/watchdog-state.json (unhealthy-since, last-restart, cooldown 30 мин). Исключения: restart:'no' (one-shot), RestartCount>5 (is_n_loop-канон — CrashLoopBackOff НЕ лечится рестартом). Telegram notify при действии. Stdlib-only (subprocess+json) — cron без PYTHONPATH."
    CrossLinks="core/internal/bootstrap/lifecycle/helpers/system.py; core/internal/shared/telegram_notifier.py"/>
  <entity name="core_internal_bootstrap_lifecycle_helpers_system_py" TYPE="MODULE"
    keywords="install-cron-watchdog,flock,timeout,idempotent,journald-persistent"
    annotation="+install_cron_watchdog (паттерн install_cron_metrics: /etc/cron.d/platform-watchdog, flock -n + timeout, temp+mv, content-match no-op) + ensure_journald_persistent (Storage=persistent, идемпотентно)."
    CrossLinks="core/internal/bootstrap/lifecycle/phases/system.py"/>
  <!-- W2: WAL -->
  <entity name="core_modules_backup_cron_scripts_wal_sync_py" TYPE="MODULE"
    keywords="wal-archive,s3-sync,head-object,safe-delete,retention,dry-run"
    annotation="Сканирует /var/lib/platform/wal-archive → для каждого файла HEAD в S3 (platform/backups/wal/<node>/) → 404 → PUT (без локального удаления). Локальный safe-delete: файл старше WAL_LOCAL_RETENTION_DAYS (7) И подтверждён HEAD в S3. S3-side purge: объекты старше WAL_S3_RETENTION_DAYS (14). Rate-limit WAL_MAX_UPLOAD_PER_RUN (200). --dry-run. S3 failure → IMP:10, exit 1 (WAL = RPO-гарантия, тихий отказ запрещён)."
    CrossLinks="core/modules/backup-cron/scripts/s3_client.py; core/modules/backup-cron/scripts/crontab; core/modules/backup-cron/Dockerfile"/>
  <!-- W3: journald -->
  <entity name="core_modules_logging_config_promtail_config_yml" TYPE="CONFIG"
    keywords="journald-scrape,journal,host-label,max-age"
    annotation="+job_name: journal — journal: {path: /var/log/journal, max_age: 24h}; relabel host из __meta. Закрывает 126 D-1."
    CrossLinks="core/modules/logging/docker-compose.base.yml"/>
  <!-- W4: telegram -->
  <entity name="core_internal_shared_telegram_notifier_py" TYPE="MODULE"
    keywords="delivery-failure-marker,IMP:9,notify-fix"
    annotation="send_telegram: любой провал → [IMP:9] DELIVERY FAILED (reason, proxy set/none). notify(): capture результата send_telegram — при False IMP:9 failure-маркер; фикс лживого 'Notification sent' (лог сейчас пишется безусловно). Контракт 'always exit 0' сохранён. Закрывает 126 D-2."
    CrossLinks="tests/unit/test_telegram_notifier.py"/>
  <!-- W5: alerts -->
  <entity name="core_modules_monitoring_config_alerting_alert_rules_yml" TYPE="CONFIG"
    keywords="backup-freshness,backup-upload-failure,wal-sync-failure,loki-datasource"
    annotation="+3 правила Grafana 12 (datasourceUid: loki): BackupFreshness (count_over_time BACKUP COMPLETE [26h] < 1, for 30m), BackupUploadFailure (off-site backup NOT confirmed за 24h), WalSyncFailure (WAL_SYNC FAIL за 24h)."
    CrossLinks="core/modules/monitoring/config/grafana/datasources.yml; tests/unit/test_monitoring_alert_rules.py"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W0 ── make check + gate MODE=fast (baseline green) ──► решения (D1-D6) ──► DevPlan
W1 ── watchdog.py (unit-тесты) ──► install_cron_watchdog в system.py ──► вызов в phases/system.py ──►
     /etc/cron.d/platform-watchdog: * * * * * root flock -n <lock> timeout 50 watchdog.py
     │ watchdog цикл: docker ps -q → docker inspect → State.Health.Status=unhealthy ≥2 runs →
     │   RestartCount≤5 и restart!="no" → docker restart + Telegram notify → cooldown 30 мин
W2 ── wal_sync.py (unit-тесты) ──► crontab +10 * * * * wal_sync ──► Dockerfile COPY ──►
     compose base.yml +wal-archive mount + env WAL_* ──► test.yml +wal-archive-test (канон U-62)
     │ цикл: listdir(wal-archive) → filter [0-9A-F]{24} → HEAD s3://<prefix>/wal/<node>/<f> →
     │   404 → PUT → (f старше 7д И HEAD ok) → rm local → purge S3 wal/ старше 14д
W3 ── promtail-config.yml +journal job ──► compose +/var/log/journal +/etc/machine-id ──►
     ensure_journald_persistent (Storage=persistent) → restart systemd-journald
W4 ── telegram_notifier.py: failure-маркеры + notify-fix (негативные тесты R5)
W5 ── alert-rules.yml: BackupFreshness/BackupUploadFailure/WalSyncFailure (Loki uid) ──►
     тест структуры (расширение test_monitoring_alert_rules.py)
W6 ── per-task test-summary ──► make check (до чистоты) ──► make gate MODE=fast ──►
     (опц.) smoke на ноде: watchdog.py --dry-run, wal_sync.py --dry-run, Loki {job="journal"}
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/plans/132-fault-tolerance/01-DevPlan.md` | создать | W0 |
| `core/internal/healthcheck/watchdog.py` | создать | W1 |
| `core/internal/bootstrap/lifecycle/helpers/system.py` | модифицировать (+install_cron_watchdog, +ensure_journald_persistent) | W1/W3 |
| `core/internal/bootstrap/lifecycle/phases/system.py` | модифицировать (вызов install_cron_watchdog рядом с install_cron_metrics) | W1 |
| `tests/unit/test_watchdog.py` | создать | W1 |
| `core/modules/backup-cron/scripts/wal_sync.py` | создать | W2 |
| `core/modules/backup-cron/scripts/crontab` | модифицировать (+`10 * * * *` wal_sync) | W2 |
| `core/modules/backup-cron/Dockerfile` | модифицировать (+COPY scripts/wal_sync.py) | W2 |
| `core/modules/backup-cron/docker-compose.base.yml` | модифицировать (wal-archive mount + WAL_* env) | W2 |
| `core/modules/backup-cron/docker-compose.test.yml` | модифицировать (wal-archive-test, канон U-62) | W2 |
| `tests/unit/test_wal_sync.py` | создать | W2 |
| `tests/unit/test_backup_cron_dockerfile.py` | модифицировать (COPY wal_sync.py в AC-C1.x) | W2 |
| `core/modules/logging/config/promtail-config.yml` | модифицировать (+journal scrape) | W3 |
| `core/modules/logging/docker-compose.base.yml` | модифицировать (promtail volumes: /var/log/journal, /etc/machine-id) | W3 |
| `tests/unit/test_promtail_config.py` | создать (YAML-парс + journal job + тома в compose) | W3 |
| `core/internal/shared/telegram_notifier.py` | модифицировать (failure-маркеры, notify-fix) | W4 |
| `tests/unit/test_telegram_notifier.py` | модифицировать (негативные тесты R5) | W4 |
| `core/modules/monitoring/config/alerting/alert-rules.yml` | модифицировать (+3 правила) | W5 |
| `tests/unit/test_monitoring_alert_rules.py` | модифицировать (валидация новых правил) | W5 |

## 3. Волны

### W0 — Фиксация (артефакты + решения)

Baseline: `make check` + `make gate MODE=fast` зелёные до старта. Решения (зафиксированы, отклонённые варианты):

**D1 — Watchdog живёт на хосте через cron (паттерн install_cron_metrics), stdlib-only Python.**
- Rejected: systemd timer unit как новый system-модуль (больше движущихся частей); docker.sock в backup-cron (нарушение изоляции модуля).
- Reason: существующий канон host-cron (/etc/cron.d/platform-metrics, flock -n + timeout, атомарная установка в system.py) — точечное расширение без новой архитектуры. watchdog.py без импортов core.internal → cron-строка без PYTHONPATH.

**D2 — S3 HEAD — source of truth для sync (идемпотентно), локальный state-файл НЕ нужен.**
- Rejected: state-файл uploaded-ключей (потеря при пересоздании контейнера, дрейф после restore).
- Reason: HEAD-object дёшев (≤1 req/файл), повторный прогон = no-op. Upload идемпотентен.

**D3 — Локальное удаление WAL — ТОЛЬКО safe-delete: старше 7 дней И подтверждён HEAD в S3.**
- Reason: слепое удаление по возрасту = потеря PITR-цели при недоступности S3. Никогда не удалять неподтверждённый файл (ухудшение до RPO=24ч молча).

**D4 — S3-side retention WAL живёт в wal_sync.py, retention.py НЕ трогаем.**
- Reason: retention.py группирует по дате из имени (YYYYMMDD); WAL-имена не парсятся → объекты попадают в `_unparseable_keys` и **никогда не удаляются** (проверено, retention.py:266-278). Отдельный purge wal/-префикса в wal_sync.py самодостаточен и не рискует сломать 7/28/90 для дампов.

**D5 — Алерты по бэкапам — Grafana-правила на Loki datasource (uid "loki", уже provisioned).**
- Rejected: textfile-exporter в node-exporter (новая интеграция, ещё одна точка отказа); backup_collector→status-page (диагностика, не алерт).
- Reason: backup-cron логи уже идут в Loki через promtail docker_sd (compose_service="backup-cron"); LogQL-правило = 0 новой инфраструктуры.

**D6 — journald Storage=persistent — обязательный шаг W3 (не опция).**
- Reason: при volatile-journal (дефолт) journald-скрейп не переживает reboot — D-1 (кросс-бут реконструкция) не закрывается.

### W1 — Watchdog авто-рестарта unhealthy (приоритет A)

1. `core/internal/healthcheck/watchdog.py` (stdlib-only: subprocess, json, time, os, argparse):
   - `scan_containers()` — `docker ps -q` → `docker inspect` (JSON) для каждого: извлекает `Name`, `State.Health.Status`, `State.RestartCount`, `HostConfig.RestartPolicy.Name`.
   - Фильтры: health-статус существует И != "healthy"/"none"; `RestartPolicy.Name != "no"` (исключает one-shot: prometheus-config-init, minio-createbuckets); `RestartCount <= 5` (канон is_n_loop из modules_healthcheck.py — CrashLoopBackOff рестартом не лечится).
   - State `/run/platform/watchdog-state.json` (atomic_write-паттерн, tmpfs): `unhealthy_since: {container: ts}`, `last_restart: {container: ts}`. Мусорные записи (контейнер исчез/стал healthy) чистятся.
   - Действие: unhealthy ≥ `WATCHDOG_UNHEALTHY_MIN` (default 10 мин = 2 прогона при 5-мин интервале) И cooldown 30 мин с last_restart → `docker restart <container>` + Telegram notify (`python3 -m core.internal.shared.telegram_notifier notify --severity critical --context watchdog` + failure-маркер из W4 не блокирует).
   - LDD: `[IMP:9][watchdog] RESTART <c> (unhealthy since <ts>)`, `[IMP:10]` при ошибке docker-команд; non-fatal: docker CLI недоступен → IMP:7, exit 0. `--dry-run` (только лог действий, без restart/notify). Exit 0 при отсутствии действий, exit 1 при внутренней ошибке.
2. `system.py`: `install_cron_watchdog(core_dir)` — копия паттерна install_cron_metrics (CRON_WATCHDOG_FILE=/etc/cron.d/platform-watchdog, строка `*/5 * * * * root flock -n /run/lock/platform-watchdog.lock timeout 50 <core_dir>/internal/healthcheck/watchdog.py`, temp+mv, content-match → no-op, не-fatal). Вызов в `phases/system.py` рядом с install_cron_metrics (φ1).
3. `tests/unit/test_watchdog.py` (monkeypatch subprocess/run): healthy→no-op; unhealthy 1 run → wait; unhealthy 2 runs → restart + state; cooldown; исключение restart:"no"; RestartCount>5 → skip; dry-run без restart; IMP:9 assert (LDD-канон). R5: негативный тест с оригинальной формой.
4. После W1: `make test-summary TEST_FILE=tests/unit/test_watchdog.py` + `make check-diff`.

**Acceptance W1:** unit-тесты зелёные; cron-установка идемпотентна (no-op при повторе); watchdog не трогает one-shot и CrashLoopBackOff-контейнеры.

### W2 — WAL → S3 + retention (приоритеты C + D)

1. `core/modules/backup-cron/scripts/wal_sync.py`:
   - Env-контракт (канон S3_* из upload-s3.sh): `S3_ENDPOINT_URL/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET/S3_REGION/S3_PREFIX` (default `platform/backups`), `WAL_ARCHIVE_DIR` (default `/var/lib/platform/wal-archive`), `WAL_LOCAL_RETENTION_DAYS` (7), `WAL_S3_RETENTION_DAYS` (14), `WAL_MAX_UPLOAD_PER_RUN` (200), `NODE_NAME`. S3-клиент — существующий `s3_client.py` модуля.
   - `scan_local()` — listdir → фильтр по WAL-паттерну `^[0-9A-F]{24}$` (сегменты pg_wal), mtime.
   - `sync()` — для каждого локального файла (до rate-limit): `head_object(key)`; KeyError/404 → `put_object` (без локального удаления). Повторный прогон идемпотентен (D2).
   - `apply_local_retention()` — файл старше WAL_LOCAL_RETENTION_DAYS И head_object OK → os.remove (safe-delete, D3). IMP:9-лог каждого удаления.
   - `apply_s3_retention()` — list_objects_v2(prefix=`wal/<node>/`) → mtime/LastModified старше WAL_S3_RETENTION_DAYS → delete_objects (D4).
   - S3-ошибка в sync → `[IMP:10][wal_sync] S3 FAIL` + exit 1 (громкий отказ, WAL = RPO-гарантия); retention-ошибки non-fatal (IMP:8 warning). `--dry-run` печатает план. LDD IMP:9 «WAL_SYNC OK: uploaded=N local_retained=M s3_retained=K».
2. `scripts/crontab` — добавить `10 * * * * root python3 /usr/local/bin/wal_sync.py` (до 03:00 дампа — не пересекается; после успеха upload в S3 локальный retention чистит старьё).
3. `Dockerfile` — `COPY scripts/wal_sync.py /usr/local/bin/wal_sync.py` (+s3_client.py уже COPY'ится).
4. `docker-compose.base.yml` (backup-cron сервис) — volumes: `+ wal-archive:/var/lib/platform/wal-archive`; environment: `+ WAL_ARCHIVE_DIR=/var/lib/platform/wal-archive`, `+ NODE_NAME` (уже есть), `WAL_LOCAL_RETENTION_DAYS`/`WAL_S3_RETENTION_DAYS`/`WAL_MAX_UPLOAD_PER_RUN` с дефолтами (не env_requires — без секретов). GREP_SUMMARY/STRUCTURE-заголовки обновить.
5. `docker-compose.test.yml` — `+ wal-archive-test` volume (канон volume-rename U-62, НЕ переопределение в bind) + сервис-маунт + env-оверрайд WAL_ARCHIVE_DIR.
6. `tests/unit/test_wal_sync.py` — scan-фильтр (включая .history/.backup-исключения), HEAD 404→PUT, HEAD ok→skip (идемпотентность), safe-delete: старый+в-S3 → rm; старый+НЕ-в-S3 → НЕ rm (R5-негатив); rate-limit; dry-run 0 мутаций; S3-fail → exit 1. Импорт без core.internal (контейнерный контракт, паттерн test_backup_cron_dockerfile.py).
7. `tests/unit/test_backup_cron_dockerfile.py` — AC: `COPY scripts/wal_sync.py` присутствует.

**Acceptance W2:** unit-тесты зелёные; локальный wal-archive не растёт без границы (safe-delete); S3 wal/-префикс прунится; retention.py не изменён и 7/28/90 для дампов не затронут.

### W3 — journald → Loki (126 D-1)

1. `promtail-config.yml` — новый `scrape_config`:
   ```yaml
   - job_name: journal
     journal:
       path: /var/log/journal
       max_age: 24h
     relabel_configs:
       - source_labels: ["__journal__hostname"]
         target_label: "host"
     pipeline_stages:
       - match:
           selector: '{job="journal"}'
           stages:
             - drop:
                 source: level
                 expression: "(?i)debug"
   ```
   (метка host + drop debug; docker-скрейп не трогаем). GREP_SUMMARY/STRUCTURE обновить.
2. `docker-compose.base.yml` (promtail) — volumes: `+ /var/log/journal:/var/log/journal:ro`, `+ /etc/machine-id:/etc/machine-id:ro` (контракт compose-include: host-пути вне модуля — только литералы здесь допустимы как системные; precedent: /var/run/docker.sock).
3. `system.py` — `ensure_journald_persistent()`: `/etc/systemd/journald.conf` → `Storage=persistent` (sed-идемпотентно, temp+mv-паттерн), `systemctl restart systemd-journald` (non-fatal), вызов в phases/system.py (φ1). D6.
4. `tests/unit/test_promtail_config.py` — YAML-парс: journal job присутствует, path/max_age корректны; compose: тома присутствуют; заголовки GREP_SUMMARY не сломаны (если есть гейт).

**Acceptance W3:** конфиг валиден; на ноде после деплоя Loki содержит `{job="journal"}` (проверка W6); Storage=persistent установлен идемпотентно.

### W4 — Telegram failure-маркеры (126 D-2)

1. `telegram_notifier.py`:
   - `send_telegram`: все failure-пути (missing creds, URLError/OSError, non-200) логируют `[IMP:9][telegram_notifier][send_telegram] DELIVERY FAILED: <reason> (proxy=<set|none>)` (сейчас IMP:7-лог без маркера). Success-лог остаётся IMP:9.
   - `notify()`: `ok = send_telegram(...)`; `if not ok: [IMP:9][telegram_notifier][notify] DELIVERY FAILED (severity=..., context=...)`; **фикс** текущего лживого `logger.info("Notification sent")` — он пишется безусловно (telegram_notifier.py:314-316), даже когда send_telegram вернул False. Контракт «always exit 0 / always True» сохранён (неблокирующий дизайн — D-2 требует реконструируемость, не блокировку).
2. `tests/unit/test_telegram_notifier.py` — расширить (R5): негативные тесты с оригинальной формой: `test_send_telegram_delivery_failed_marker_urlerror`, `test_send_telegram_delivery_failed_marker_http_non200`, `test_notify_delivery_failed_marker` (mock send_telegram → False, caplog assert IMP:9 DELIVERY FAILED), `test_notify_success_no_failure_marker`. LDD: IMP:9 assert в успешном сценарии.

**Acceptance W4:** любой провал доставки реконструируется по логам (маркер IMP:9 + причина + канал); тесты с негативами R5 зелёные; exit-семантика notify не изменена.

### W5 — Алерты на бэкапы и WAL (приоритет E)

1. `alert-rules.yml` — +3 правила в формате Grafana 12 (структура как существующие: data → `datasourceUid: "loki"`, model.expr LogQL, `for`, severity-лейблы, unique uid):
   - `backup_freshness`: `count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h]) < 1` → CRITICAL, `for: 30m` (защита от ложных срабатываний при рестарте стека).
   - `backup_upload_failure`: `count_over_time({compose_service="backup-cron"} |~ "off-site backup NOT confirmed" [24h]) > 0` → WARNING.
   - `wal_sync_failure`: `count_over_time({compose_service="backup-cron"} |~ "WAL_SYNC FAIL" [24h]) > 0` → WARNING.
   - Contact: наследование default notification policy (как существующие правила; telegram contact point уже provisioned — contact-points.yml.telegram). GREP_SUMMARY/STRUCTURE обновить (5 правил).
2. `tests/unit/test_monitoring_alert_rules.py` — расширить: парс YAML, uid уникальны, новые правила присутствуют, datasourceUid="loki", expr непустой.

**Acceptance W5:** 3 правила валидны и покрыты тестом; существующие 4 правила не изменены семантически.

### W6 — Верификация (последовательность обязательна)

1. Per-wave прогоны уже выполнены; финально:
   - `make test-summary TEST_FILE=tests/unit/test_watchdog.py` + `test_wal_sync.py` + `test_promtail_config.py` + `test_telegram_notifier.py` + `test_monitoring_alert_rules.py` + `test_backup_cron_dockerfile.py` (или один батч `make test-summary`).
   - `make check` (до чистоты, WORKERS=6, батч-фиксы).
   - `make gate MODE=fast` — один раз, после чистого check.
2. Опциональный smoke на ноде (tronyx-vps, окно обслуживания не требуется — все шаги read-only/идемпотентны):
   - `python3 core/internal/healthcheck/watchdog.py --dry-run` (0 действий на здоровом стеке).
   - `wal_sync.py --dry-run` в backup-cron контейнере (docker exec).
   - `curl "http://loki:3100/loki/api/v1/query?query=%7Bjob%3D%22journal%22%7D"` → серии есть (после деплоя W3).
   - Деплой волн на ноду: пересборка backup-cron (make backup-cron build/up) + restart logging (promtail) + перезаливка core (bootstrap/node-update) — в отдельном окне, по решению оператора.
3. Коммиты (лимит ≤2): `docs(132): <N> DevPlan — fault tolerance hardening` + `feat(132): <N> implementation — ...` (волны могут быть отдельными feat-коммитами — норма).
4. Ссылки на 126: после W3/W4 отметить в `.ai/plans/126-chaos-resilience/04-Debt.md` (если создан) статусы D-1/D-2 → CLOSED-by-132.

**Acceptance W6:** все проверки зелёные; smoke на ноде (если выполнен) подтверждает dry-run безопасность; коммиты в рамках лимита.

## 4. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Watchdog рестартит контейнер в CrashLoopBackOff (рестарт не помогает, thrash) | RestartCount>5 → skip (is_n_loop-канон) + cooldown 30 мин + dry-run-режим по умолчанию в первый деплой |
| WAL sync: S3 недоступен → локальный каталог растёт | safe-delete только после HEAD-ok (D3); exit 1 + IMP:10 + WalSyncFailure-алерт; локальный рост временный и bounded (WAL-объём ≤16MB × частота) |
| WAL upload перегружает S3 (list/HEAD-шторм) | rate-limit WAL_MAX_UPLOAD_PER_RUN=200; hourly интервал; HEAD до PUT |
| retention.py удалит WAL-объекты по ошибке | проверено: unparseable-ключи в `_unparseable_keys`, никогда не удаляются (retention.py:266-278); WAL под subprefix `wal/<node>/` |
| journald-скрейп: нет данных (volatile journal) | D6 — Storage=persistent обязателен в W3; проверка {job="journal"} в W6 |
| Loki-алерт: ложные срабатывания при рестарте стека (backup-cron поднимается >26h? нет — окно 26h, рестарт минуты) | `for: 30m`; окно 26h > интервал дампа 24h с запасом |
| promtail journal: рост позиций/перф | max_age 24h + drop debug; позиции в tmpfs (уже); docker-скрейп не меняется |
| Dockerfile-тест/гейты компоуз-контрактов (volumes SoT root compose) | wal-archive уже объявлен в root compose (SoT) — модуль только маунтит; канон U-62 для test-оверрайда; `make check` ловит drift |
| Grafana 12 формат новых правил | копия структуры существующих (datasourceUid/refId/model.expr/for); тест парсинга + уникальность uid |

## 5. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W0 | baseline green (check + gate fast); решения D1-D6 зафиксированы |
| W1 | watchdog unit-тесты (вкл. негативы R5); cron-установка идемпотентна |
| W2 | wal_sync unit-тесты (safe-delete, rate-limit, dry-run, S3-fail); compose/Dockerfile/crontab согласованы; Dockerfile-тест обновлён |
| W3 | journal-скрейп + тома; Storage=persistent; тест конфига |
| W4 | failure-маркеры IMP:9; notify-fix; негативные тесты |
| W5 | 3 правила, валидация тестом, существующие не тронуты |
| W6 | per-task test-summary зелёный; `make check` чист; `make gate MODE=fast` зелёный; (опц.) нодевой smoke |

$END_DEVPLAN
