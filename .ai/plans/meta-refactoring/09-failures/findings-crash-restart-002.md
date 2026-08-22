# Failures: S1/S2 — часть 2 (FAIL-0403–0410, verified-controls, матрица)

Продолжение findings-crash-restart.md (карта auto-recovery и FAIL-0400–0402 там).

### FAIL-0403 · MED · Watchdog молчит на crash-loop и не уведомляет о skip-решениях
- scenario: S1.
- evidence: `watchdog.py:90` `RESTART_LOOP_THRESHOLD = 5` — «RestartCount > 5 =
  CrashLoopBackOff» не рестартится (осознанно, T2.6); фильтры кандидата — health
  unhealthy + `restart != "no"` + RestartCount≤5 (`watchdog.py:372`,
  `is_restart_candidate`). TG-notify привязан к решению restart
  (`watchdog.py:3` STRUCTURE: «⚡ docker restart + Telegram notify»); skip-ветка
  (cooldown/crash-loop) notify не шлёт.
- что происходит: litellm OOM-крейтится каждые N минут → RestartCount быстро >5 →
  watchdog навсегда игнорирует контейнер, алерт не уходит (Grafana-алерт мог бы, но
  если проблема в самом observability — см. FAIL-0402).
- где отказ: `watchdog.py:372` (is_restart_candidate) + отсутствие notify в decide_actions skip.
- авто-recovery: нет (restart-политика Docker продолжает ретраить, но если причина
  персистентна — контейнер остаётся down).
- broken state: да, тихий.
- retry безопасен: ручной рестарт безопасен; converge R9 пропустит по cooldown.
- user impact: 5xx на фасаде упавшего сервиса.
- alert: нет (в кейсе crash-loop).
- восстановление оператора: `make healthcheck NODE=` → ssh → `docker logs`; фикс причины.
- confidence: high.
- action: <10 LOC — в decide_actions при skip из-за RestartCount>5 слать TG
  «crash-loop detected, не рестарчу» (канал уже есть). Кандидат в quick-win до launch.

### FAIL-0404 · MED · Транзиентное окно LLM-фасада после reboot: litellm/pgbouncer/langfuse догоняют postgres
- scenario: S2, конкретизация FAIL-0401 для главного пользовательского пути.
- evidence: litellm `DATABASE_URL` через pgbouncer:6432 (`litellm base.yml:75`),
  Prisma migrate 45-60s + start_period 60s (`:131-141`); pgbouncer depends_on postgres
  healthy (`postgres base.yml:96-98`) — не соблюдается при daemon-restart; hermes-agent
  проверяет litellm c `DEPENDENCY_CHECK_TIMEOUT=2.0` (`hermes-agent base.yml:162-163`) —
  в окне недоступности hermes-скиллы LLM деградируют.
- что происходит: порядок «litellm раньше pgbouncer/postgres» → crash-loop litellm до
  готовности БД; каждый рестарт = ещё 45-60s миграции.
- авто-recovery: да (unless-stopped), окно минуты.
- user impact: LLM API 5xx; langfuse ingest буферизуется в redis (volume есть —
  langfuse base.yml:164-186) и не теряется.
- alert: с задержкой (стек мониторинга сам поднимается).
- retry/восстановление: безопасно; `make converge NODE=` ускоряет.
- confidence: high механика / средняя длительность.
- action: покрыт FAIL-0401(b) — boot-unit с `--wait` либо принять окно после замера drill'ом.

### FAIL-0405 · MED · Backup-cron: healthcheck liveness-only (pgrep cron) — тихая смерть бэкап-конвейера
- scenario: S1.
- evidence: `backup-cron base.yml:111-120` — healthcheck `pgrep cron`, канон
  liveness-only («readiness внешний через Prometheus up{} + alert-rules», W1-4);
  факт свежести бэкапа — Loki-алерт BackupFreshness (тот же комментарий) — требует
  живых logging+monitoring; S3-upload фейлы healthcheck'ом не видны.
- что происходит: cron-демон жив, но pg_dump/S3-sync фейлится (смена кредов, сеть,
  переполнение spool) — health зелёный, бэкапы не идут. RPO 24ч (root AGENTS.md
  §Безопасность данных) незаметно нарушается.
- где отказ: `backup-cron base.yml:115-120` + зависимость алерта от живого Loki.
- авто-recovery: нет; retry безопасен (следующая ночная попытка).
- user impact: отложенный — потеря данных за окно неисправности при DR.
- alert: только пока мониторинг жив.
- восстановление: `make backup` вручную; проверка последнего объекта в S3.
- confidence: high.
- action (до launch, дёшево): host-level freshness-check в существующий
  /etc/cron.d/platform-metrics-канал (`platform_export_metrics.py` пишет
  status-metrics.json — добавить last-backup-age) ИЛИ ручная еженедельная проверка
  объекта в S3 в release-checklist. DR-drill (Debt, Rev 2026-08-31) — подтвердить.

### FAIL-0406 · MED · Ежемесячный `docker system prune -af` удаляет rollback-образы проектов старше 30 дней
- scenario: S1/S2 (деградация DR-механики).
- evidence: `lifecycle/helpers/system.py:725-728` CRON_PRUNE_LINES:
  `docker system prune -af --filter until=720h` (monthly, 04:00, volumes не трогаются —
  инвариант `:741`). Rollback-механика требует наличия предыдущего образа:
  `deploy/engine/lifecycle.py:88-90` («No previous image — cannot rollback»), fallback-тег
  `<service>:previous-rollback` (`:67`); snapshots хранятся 10 шт
  (`deploy/audit/history.py:16-19`).
- что происходит: образ, неиспользуемый >30 суток (после серии деплоев), удаляется prune'ом;
  rollback по старому снапшоту становится невозможным (compose up пересоздаст из
  текущего payload/образа — т.е. откат к старой версии не сработает).
- авто-recovery: нет (это не crash, а эрозия страховки).
- retry: безопасен; данные не затрагиваются (volumes не prune'ятся; bind-тома под
  /var/lib/platform/* — root compose:53-82).
- user impact: только при попытке rollback — отказ отката.
- alert: нет.
- восстановление: redeploy нужной версии через CI (git tag) — медленнее rollback.
- confidence: high (семантика prune -a + until документирована Docker).
- action: задокументировать в runbook («rollback глубиной ≤30 дней»); либо label-based
  protect rollback-тегов (prune не умеет label-exclude — тогда `docker image prune`
  заменить на выборочный скрипт — дороже; до launch достаточно документации).

### FAIL-0407 · MED (HYPOTHESIS: требует проверки на ноде) · Tor systemd-unit без Restart drop-in — единая цепочка TG-алертов уязвима
- scenario: S1 (host-сервис).
- evidence: privoxy защищён drop-in `Restart=on-failure`
  (`install_tor_proxy.py:238-262`, rationale `:244-247` — «краш privoxy молча убивает
  нотификации»); tor — только `systemctl enable` non-fatal + restart fatal
  (`install_tor_proxy.py:475-482`), drop-in для tor.service в коде отсутствует.
  Вся нотификация (watchdog TG, grafana TG, cert/reboot TG) идёт через privoxy:8118→tor
  (monitoring base.yml:216 TRAP 141 B14).
- что происходит: если unit tor.service на Ubuntu 24.04 не имеет дефолтного Restart
  (в Debian-пакете исторически `Restart=on-failure` — HYPOTHESIS), краш тора молча
  убывает ВСЕ алерты платформы до ручного `systemctl start tor`.
- проверка: `systemctl show tor -p Restart` на ноде (1 команда до launch).
- авто-recovery: privoxy — да; tor — неизвестно.
- user impact: скрытый (отсутствие алертов), сам пользовательский трафик не затронут.
- восстановление: `make converge`/ssh systemctl.
- confidence: механика high; дефолт unit'а — low (HYPOTHESIS).
- action: проверить на ноде; если Restart=no → скопировать паттерн privoxy drop-in
  для tor.service (5 строк, `configure_privoxy_restart_dropin` уже канон).

### FAIL-0408 · LOW · nginx healthcheck `nc -z localhost 80` нарушает канон «localhost в healthcheck запрещён»
- scenario: S1 (ложно-негативный healthcheck → лишние watchdog-рестарты ingress).
- evidence: `nginx base.yml:107` (`nc -z localhost 80`) против правила
  core/modules/AGENTS.md §Правило healthcheck («localhost запрещён: резолвится в ::1»);
  аналогичный комментарий в status-page base.yml:15. На практике работает (busybox nc +
  /etc/hosts), но при изменении образа/резолвера — false-unhealthy → watchdog рестартит
  ЗДОРОВЫЙ nginx (кратный ingress-flap).
- action: заменить на `127.0.0.1` (одна строка, гейт healthcheck-контракта не сломается).

### FAIL-0409 · LOW · Redis (основной) cache-only без volume — потеря кэша при ребуте by design
- evidence: `redis base.yml:70-71` («NO volumes — cache-only», owner verdict wave-redis
  2026-07-15); restart: always (`:38`). Отдельный langfuse-redis ПЕРСИСТЕНТЕН
  (langfuse base.yml:164-186, appendonly) — очередь ingest переживает reboot.
- impact: cold-cache + сброс счётчиков/лимитов, живущих в redis-платформы; данных нет.
- action: none (задокументировано в коде); убедиться, что ни один проект не хранит
  в платформенном redis персистентное состояние (контракт окружения — DO NOT #1/#7).

### FAIL-0410 · LOW · Prune удаляет exited init-контейнеры (prometheus-config-init, minio-createbuckets)
- scenario: S2-смежный.
- evidence: `system.py:727` `docker system prune -af` удаляет stopped-контейнеры;
  init-контейнеры restart:"no" (monitoring base.yml:46, minio base.yml:76) — в их числе.
  Безвредно для данных (volume `prometheus-config-gen` персистентен, root compose:91);
  при следующем `docker compose up` compose пересоздаст init и выполнит
  `service_completed_successfully`. Единственный нюанс: до следующего up init-контейнера
  нет в `docker ps -a` (косметика диагностики).
- action: none; знать при разборе «куда делся init-контейнер».

## Подтверждённые компенсаторы (verified-safe, без finding)

| Контрол | Evidence |
|---------|----------|
| docker daemon автостарт + рестарт самого daemon'а | `docker_installer.py:68,327-338`; live-restore `:173` |
| Firewall DOCKER-USER пере-apply при каждом старте daemon | drop-in ExecStartPost, `docker_installer.py:211-256` |
| Journald Storage=persistent (логи переживают reboot) | `lifecycle/phases/system.py:471-477`, `helpers/system.py:23-24` |
| zram (смягчение OOM-давления) | `helpers/system.py:25-26`, φ5 install_zram |
| Cron-задачи host-уровня возобновляются после reboot | /etc/cron.d/{platform-metrics,platform-watchdog,platform-prune} (`helpers/system.py:408-413,724-729`), acme renew crontab (`cron_installer.py`) |
| platform-reboot.timer Persistent=true — пропущенный 04:30 выполнится после boot | `reboot_policy.py:102-103` |
| Volumes персистентны: 5 bind (/var/lib/platform/*) + 8 named; prune volumes не трогает | root compose:50-91; `helpers/system.py:741` |
| Единый health-критерий: running AND (healthy|""|none) | `core/lib/healthcheck.sh:83-133` (D5), `deploy/healthcheck_poller.py:21` |

## Матрица S1: падение отдельного контейнера (сжато)

| Контейнер | Crash-эффект | Авто-recovery | User impact | Alert |
|-----------|--------------|---------------|-------------|-------|
| postgres (1G limit, postgres base.yml:45-55) | клиенты через pgbouncer висят; pgbouncer unhealthy | restart unless-stopped; watchdog ≥10 мин | все БД-сервисы 5xx | да, если monitoring жив |
| nginx (порты 80/443) | полный ingress-outage | restart + watchdog | недоступны ВСЕ https-домены | TG watchdog ≥10 мин |
| litellm (2G) | LLM API 5xx | restart; Prisma-migrate окно 45-60s | LLM-фасад + hermes-скиллы | да |
| status-page | статус-страница 5xx (сам индикатор) | restart + content-hash rebuild при деплое (`docker_orchestrator.py:465-475`) | только наблюдаемость | косвенно |
| hermes-agent (1G) | бот/дашборд down | restart; state в hermes-data bind | TG-бот недоступен | да |
| backup-cron (always) | бэкапы не идут | restart; см. FAIL-0405 (liveness-дыра) | отложенный (RPO) | Loki BackupFreshness |
| monitoring (prometheus/grafana) | тишина алертов | restart; см. FAIL-0402 | нет прямого | нет (дыра) |

## Итоги

| Severity | Count | IDs |
|----------|-------|-----|
| CRITICAL | 0 | — |
| HIGH | 3 | 0400, 0401, 0402 |
| MED | 5 | 0403, 0404, 0405, 0406, 0407 |
| LOW | 4 | 0408, 0409, 0410 + (0407 pending verify) |

Кандидаты в launch-blockers: FAIL-0401 (boot-ordering: принять/задокументировать окно
или 10-строчный boot-unit + замер в drill), FAIL-0402 (внешний heartbeat на status-page
/health — часы работы), FAIL-0407 (одна команда проверки + возможные 5 строк drop-in),
FAIL-0400 (reboot-drill на test-VPS — уже близок к существующему release-checklist шагу).
