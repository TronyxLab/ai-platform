# Findings: worker crash + interrupted long-running tasks — pre-launch audit

$ARTIFACT_CONTRACT
- PURPOSE: Аудит S1 (worker crash: hermes-agent, backup-cron, platform-export-metrics) и
  S2 (interrupted long-running: bootstrap/converge на середине, backup dump, cron overlap, reboot 04:30)
- SCOPE: research-only; ID FAIL-0900–0999; evidence = file:symbol + цитата
- REQUIRES: известное вне скоупа — FAIL-0703 (receive retry), FAIL-0402 (watchdog unhealthy≥10мин), FAIL-0405 (backup pgrep liveness)
- ACCEPTANCE: 9 вопросов протокола на finding; severity по 00-scope.md

## Сводка

Bootstrap и converge прерываются безопасно (--resume / flock NB + конвергентные reconcilers) — verified-safe.
Единственный HIGH — измерение свежести бэкапа по mtime ЛОГА: упавший ночной дамп выглядит свежим,
PlatformBackupStale слепнет ровно тогда, когда нужен. Cron-строки backup без flock (by design v1).

### FAIL-0900 · MED · hermes-agent: зависший (unhealthy, но живой) процесс не рестартует docker'ом; self-heal существует (converge R9), но ничем не запланирован
- scenario: hermes-agent повисает (accept-без-ответа, deadlock) → docker healthcheck unhealthy, restart-policy молчит; лечение есть только у ручного converge
- evidence: `core/modules/hermes-agent/docker-compose.base.yml:91` (`restart: unless-stopped` — рестарт ТОЛЬКО на exit); :182-187 healthcheck curl 127.0.0.1:9119 interval 15s retries 3 — docker НЕ рестартует unhealthy-контейнеры по определению; self-heal умеет unhealthy: `core/internal/bootstrap/converge/infra.py:72` (`BAD_DOCKER_STATES = {"exited","restarting","dead","unhealthy","paused"}` → compose up -d), но converge не в cron/systemd (grep converge по helpers/system.py и crontab — 0 попаданий; запуск только оператор/φ13)
- Q3: авто-recovery НЕТ (только alert-канал watchdog'а — известный FAIL-0402)
- Q4: broken state нет (процесс жив, данные целы)
- Q5: retry/restart безопасен (compose up -d пересоздаёт)
- Q6: Telegram-агент/LLM-оркестрация недоступны до ручного вмешательства; litellm (отдельный контейнер) работает
- Q7: alert ДА — watchdog unhealthy≥10мин (FAIL-0402) + PlatformServiceDown-класс правил
- Q8: `make converge NODE=<node>` (R9 вылечит unhealthy) или `docker compose -f core/modules/hermes-agent/docker-compose.base.yml up -d --force-recreate`
- Q9 (fix, минимальный): systemd timer/cron-строка раз в N минут на converge --reconcile (уже идемпотентен и под flock NB) ИЛИ add-on autoheal-контейнер
- confidence: high · action: после launch допустимо; до launch — runbook-строка «watchdog алерт → make converge»

### FAIL-0901 · LOW · backup-cron crond crash покрыт (restart always + pgrep liveness); остаточный риск — stop_grace_period 120s < времени дампа
- scenario: крэш самого контейнера backup-cron vs остановка стека во время идущего дампа
- evidence: `core/modules/backup-cron/docker-compose.base.yml:48` (`restart: always` — крэш демона самозалечивается), :115-117 healthcheck pgrep cron (ограничения — известный FAIL-0405); :49 `stop_grace_period: 120s` — комментарий обещает «finish current backup or abort+cleanup», но pg_dumpall большой БД за 120s не укладывается → SIGKILL mid-dump (мостик к FAIL-0903/0904)
- Q3: крэш демона — да (restart always); обрыв дампа при stop — нет (частичный файл, см. 0903)
- Q7: liveness pgrep — да; про обрванный дамп — нет (см. 0903)
- Q8: `make -C core/modules/backup-cron restart`; проверка логов `/var/log/platform/backup/postgres.log`
- Q9 (fix): либо честный abort-обработчик (SIGTERM → cleanup partial), либо увеличить grace и документировать окно
- confidence: high · action: вместе с 0903

### FAIL-0902 · LOW · platform-export-metrics: cron-цикл защищён (flock -n + timeout 50, атомарная запись) — verified-safe; системный фейл проявляется ложным BackupStale ≤25h, не тишиной
- scenario: одноразовый сбой экспортёра (docker недоступен секунду, таймаут сбора) и системный (битый import после core-deploy)
- evidence: `core/internal/bootstrap/lifecycle/helpers/system.py:290-294` — cron-строка `flock -n /run/lock/platform-metrics.lock timeout 50 platform-export-metrics.sh` (overlap исключён, длительность ограничена); `platform_export_metrics.py:6` — atomic_write status-metrics.json (предыдущий файл не портится); consumers — status-page app.py:233-250 выставляет age от ts из json → возраст РАСТЁТ при мёртвом экспортёре → PlatformBackupStale (platform-alerts.yml:74-84) даст ложный critical ≤25h — канарея есть, хоть и с неверной семантикой
- Q3: одноразовый — самозалечивается следующей минутой; системный — нет
- Q7: alert косвенный (ложный BackupStale), прямой на staleness status-metrics.json нет
- Q8: `make dev-metrics` локально; на ноде — проверить `/var/lib/platform/run/status-metrics.json` mtime и лог cron
- Q9 (fix): none до launch (канарея работает); опционально — отдельный gauge freshness самого json
- confidence: high · action: none

### FAIL-0903 · HIGH · Свежесть бэкапа измеряется mtime ЛОГА, а не фактом верифицированного дампа — упавшая ночная копия выглядит свежей, PlatformBackupStale слепнет
- scenario: pg_dumpall падает на середине (диск/OOM/SIGTERM от reboot или stop_grace_period — см. 0904/0901) → лог получил строки старта → метрика «свежая» → единственная защита RPO-24ч молча дырявая
- evidence: `core/internal/healthcheck/metrics/backup_collector.py:54-76` — `get_backup_status()` читает mtime `/var/log/platform/backup/postgres.log` («Log %s mtime ... age»); crontab:28 пишет лог при КАЖДОМ старте job'а (`>> postgres.log 2>&1`); PlatformBackupStale (platform-alerts.yml:74-84, age>25h critical) питается именно этим через status-page app.py:233-250. Частичный дамп: имя timestamped (`backup_postgres.py:165` `pgdumpall_{ts}.sql.gz`) — предыдущий НЕ затирается, но cleanup partial только в finally (:321-327 «Shell trap cleanup_partial EXIT parity»), который не исполняется при SIGTERM/SIGKILL → частичный .sql.gz остаётся в spool и внешне неотличим от хорошего
- Q3: авто-recovery нет — следующий дамп через 24ч
- Q4: broken state ДА: RPO тихо деградирует; частичный файл в spool может быть выбран оператором при restore (gzip -t отловит, но уже в момент аварии)
- Q5: retry (ручной `make backup`) безопасен и закрывает окно
- Q6: при последующей аварии БД восстановление возможно только на устаревшую копию (возраст неизвестен оператору)
- Q7: alert НЕТ для этого класса (alert показывает свежесть лога, не дампа)
- Q8: `grep "gzip integrity OK\|FAIL" /var/log/platform/backup/postgres.log`; вручную `make backup NODE=<n>`
- Q9 (fix, маленький): писать маркер/метрику ТОЛЬКО после «gzip integrity OK» (например touch {spool}/postgres/.last_verified) и читать в backup_collector его mtime вместо лога
- confidence: high · action: кандидат launch-blocker — фикс <10 строк, закрывает главный silent-failure резервной подсистемы

### FAIL-0904 · MED · Reboot timer 04:30 пересекает backup window (dump+upload-retry до ~05:30); reboot видит только SSH-сессии, не jobs
- scenario: pending kernel-reboot → timer стреляет 04:30 посреди затянувшегося дампа/upload-ретраев → стек убит, ночной backup потерян
- evidence: `core/internal/bootstrap/reboot_policy.py:99-108` — `OnCalendar=*-*-* 04:30:00`, Persistent=true; :362 отсрочка ТОЛЬКО по активным SSH-сессиям («Ребут отложен: активная SSH-сессия... Повторная попытка — завтра 04:30»); окно backup: дамп 03:00 (crontab:28) + upload retry 3×30мин (`upload.py:111` `_RETRY_INTERVAL_SEC = 30 * 60`, TRAP «total 90 min max») → job легитимно живёт до ~05:30; stop_grace_period 120s (backup-cron compose:49) добивает при перезапуске контейнеров
- Q3: авто-recovery job'а нет; WAL-sync hourly (crontab:42) смягчает PITR, полный дамп дня выпадает
- Q4: предыдущий дамп НЕ затирается (timestamped spool, backup_postgres.py:165) — потеря только одной ночи
- Q5: повторный `make backup` безопасен
- Q6: RPO одной ночи; user impact только при совпадении с аварией БД в те же сутки
- Q7: alert — см. FAIL-0903: сейчас тишина; после фикса 0903 BackupStale поймаёт
- Q8: `make backup NODE=<n>` после ребута
- Q9 (fix, 1 слово×2): сдвинуть OnCalendar на 05:45 (после 05:00 retention) ИЛИ добавить в reboot_policy проверку pid/lock активного backup-job'а; flock из 0905 уменьшит зону
- confidence: high (окна подтверждены конфигами) · action: сдвиг таймера — тривиальный фикс до launch

### FAIL-0905 · MED · Cron-строки backup без flock — overlap разрешён by design («parallel start allowed in v1»)
- scenario: upload-ретраи тянут backup-postgres за 04:10 wal_sync и 05:00 retention; затяжной дамп пересекается с app-data 03:30
- evidence: `core/modules/backup-cron/scripts/crontab:28-42` — все 5 строк без flock/vlockf; инвариант в шапке: «Collision risk (03 §8): parallel start allowed in v1; no queue implementation». Смягчения: разные spool-каталоги (постгрес/app-data), PUT идемпотентен, safe-delete HEAD-guarded (`wal_sync.py:350-380` «NOT in S3 — KEEP (safe-delete D3, RPO guard)») — коррупции маловероятны, но ресурсные гонки и двойные ретраи реальны
- Q3: само проходит (гонки мягкие), исход недетерминирован
- Q4: broken state маловероятен; вероятны задержки и сдвиг окна (усиливает 0904)
- Q5: следующий запуск безопасен
- Q7: alert нет (логи только)
- Q8: `tail /var/log/platform/backup/*.log`
- Q9 (fix, 1 слово на строку): префикс `/usr/bin/flock -n /run/lock/backup-<job>.lock` в crontab:28/31/37/42 — канон уже используется в platform-metrics строке (helpers/system.py:292)
- confidence: high · action: дешёвый фикс, взять до launch вместе с 0904

### FAIL-0906 · LOW · Прерванный bootstrap докатывается вторым запуском (--resume всегда; фаза переигрывается целиком; state.json коррапт-защищён) — verified-safe
- scenario: SSH disconnect посреди bootstrap-node → повторный `make bootstrap-node`
- evidence: `core/entrypoints/bootstrap.sh:72` — remote-вызов ВСЕГДА с `--resume`; `state_machine.py:26` («--resume loads existing state and continues from last checkpoint») и :248 («Sub-step resume отсутствует — фазы выполняются целиком; идемпотентность через content-hash/preconditions»); фаза в статусе running переигрывается (state_machine.py:403 — done только для done/done_with_warnings); state.json — flock+atomic write, коррапт → явная StateCorruptError, не тихий сброс (state_store.py:18, :237-258). Фазы конвергентны: install-скрипты idempotent, φ8 context_deployer skip по health-gate (context_deployer.py:368-374 «already healthy, skipping»)
- Q3: авто — второй запуск докатывает
- Q4: частичная мутация внутри фазы допустима — фаза перезапускается целиком
- Q5: retry безопасен (это штатный контракт)
- Q7: alert n/a (операторская операция)
- Q9 (fix): none; HYPOTHESIS — глобального lock у bootstrap нет (flock только на state.json), параллельные два запуска возможны, но SIGHUP при обрыве ssh обычно убивает первый — риск остаточный низкий
- confidence: high · action: none

### FAIL-0907 · LOW · Прерванный converge безопасно перезапускается (flock NB + конвергентные reconcilers) — verified-safe
- scenario: converge убит посреди R1-R10 → повторный запуск
- evidence: `core/internal/bootstrap/converge.py:21` — `flock(fcntl LOCK_EX|NB)` на /var/lock/platform-converge.lock, конфликт → exit 3 (TRAP[DECISION] :45-49 — lock переехал в Python); reconcilers работают desired-vs-actual (reconciler.py R1-R10), half-healed контейнеры дохаливаются следующим прогоном; cooldown-файл (runtime.py:39 flapping-guard «вылечен в 3 последних run'ах → global cooldown») — единственное «мягкое» место: агрессивный heal подряд притормаживает, но не ломает
- Q3/Q5: повторный converge безопасен и является штатным восстановлением
- Q7: alert n/a
- Q8: `make converge NODE=<n>` (при exit 3 — предыдущий ещё жив, подождать)
- Q9 (fix): none
- confidence: high · action: none
