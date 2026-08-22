# Failures: S1 process crash / S2 machine restart — pre-launch audit

- scenario: S1 = crash отдельных контейнеров (OOM/bug); S2 = перезагрузка VPS (kernel update / хостер)
- method: research-only, evidence = file:symbol + цитата; HYPOTHESIS помечен явно
- ID range: FAIL-0400–0499; companion: findings-crash-restart-002.md (S2-матрица, verified-controls)

## Контекст, меняющий вес сценария S2

Авторебут ноды — НЕ гипотетика, а включённая политика:

- `core/internal/bootstrap/reboot_policy.py:96` — unit `platform-reboot.service` ExecStart:
  `reboot_policy.py check --execute`; таймер 04:30 daily, `Persistent=true`
  (`reboot_policy.py:98-107`, `build_unit_texts()`).
- `reboot_policy.py:373-397` (`check()`): `/var/run/reboot-required` есть + нет активных
  tty-сессий платформенных пользователей → `systemctl reboot` + TG. Активная сессия →
  отложить (retry завтра). unattended-upgrades Automatic-Reboot=false (W1-3,
  `reboot_policy.py:28-31` rationale).
- Т.е. после security-обновлений ядра нода **ребутается сама, ночью, без оператора**.
  Любая слабость self-heal после reboot становится регулярно воспроизводимым инцидентом.
  → pre-launch обязателен полный reboot-drill на test-VPS (FAIL-0400).

## Механизмы auto-recovery (карта, подтверждено кодом)

| Слой | Механизм | Evidence |
|------|----------|----------|
| docker daemon | systemd enable + override `Restart=always RestartSec=10s`; live-restore:true | `docker_installer.py:68` (SYSTEMD_OVERRIDE), `:327-338` (enable/start), `:173` (live-restore) |
| firewall после reboot | drop-in docker.service ExecStartPost → DOCKER-USER re-apply при каждом старте daemon | `docker_installer.py:211-256` (`configure_docker_user_dropin`), dir `:77` |
| контейнер exit/crash | restart policy: unless-stopped (default), always (redis/backup-cron) | compose base.yml всех модулей; гейт `test_gate_compose_restart_policies` |
| running-but-unhealthy | host-cron watchdog */5: unhealthy ≥10 мин + cooldown 30 мин + RestartCount≤5 → docker restart + TG | `lifecycle/helpers/system.py:408-413` (cron line), `watchdog.py:82-90` (константы) |
| exited контейнеры платформы | converge R9 self-heal (`docker compose up -d`) — ТОЛЬКО вручную (`make converge NODE=`) | `converge/runtime.py:187-194` (`reconcile_runtime_state`), cooldown `_COOLDOWN_RUNS=3` |
| host-сервисы | tor/privoxy systemd; privoxy имеет drop-in `Restart=on-failure` | `install_tor_proxy.py:238-262`, `:470-491` |
| ребут ОС | platform-reboot.timer 04:30 Persistent=true (см. выше) | `reboot_policy.py:86-108` |

### FAIL-0400 · HIGH · Авторебут по расписанию делает reboot-path продакшн-активным без единого reboot-drill перед launch
- scenario: S2 (плановый и рекуррентный).
- evidence: `reboot_policy.py:96` (`check --execute` в unit), `:102-103` (`OnCalendar=04:30`,
  `Persistent=true`), `:380-384` (`systemctl reboot`); триггер — apt kernel update →
  `/var/run/reboot-required`. Хаос-сьют существует (`tests/e2e/test_chaos_resilience.py`
  T1-T12, bootstrap AGENTS.md §Runbook шаг «Chaos»), но требует ручного прогона
  (`requires_node`).
- авто-recovery: весь каскад из таблицы выше обязан отработать без оператора.
- broken state: см. FAIL-0401/0402/0407 — окно деградации и тишина мониторинга.
- retry безопасен: да (ребут идемпотентен, volumes персистентны — bind `/var/lib/platform/*`
  root docker-compose.yml:53-82).
- user impact: зависит от скорости подъёма стека (оценка 2-10 мин; не измерено — HYPOTHESIS).
- alert: частично (TG от watchdog только при его срабатываниях).
- восстановление оператора: наблюдение — `make healthcheck NODE=<n>`, `make e2e-verify NODE=<n>`.
- confidence: high (код прочитан).
- action (до launch): прогнать `make test-node` + chaos T1-T12 И минимум один
  контролируемый reboot test-VPS с замером времени до healthy всего стека; зафиксировать
  окно в release-checklist.

### FAIL-0401 · HIGH · После reboot Docker стартует контейнеры без порядка зависимостей — depends_on не переоценивается
- scenario: S2.
- evidence: стек деплоится как ЕДИНЫЙ compose-проект «platform» через root compose
  (`deploy/compose_args.py:98-112` — «root compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f», U-49;
  root docker-compose.yml:28-41 include 13 модулей). При загрузке машины daemon сам
  рестартит контейнеры по своей внутренней очереди — семантика depends_on
  (`condition: service_healthy/completed_successfully`) действует ТОЛЬКО при
  `docker compose up`. Зависимости внутри проекта: pgbouncer→postgres healthy
  (postgres base.yml:96-98), grafana→prometheus healthy (monitoring base.yml:251-253),
  prometheus→init completed (monitoring:125-127), langfuse-worker→langfuse healthy
  (langfuse base.yml:145-147).
- что происходит: произвольный порядок старта; зависимые контейнеры падают на старте
  и поднимаются restart-политикой с backoff, пока зависимости не станут готовы.
- где отказ: отсутствие boot-time оркестрации — systemd-юнита вида
  `docker compose -f /opt/platform/docker-compose.yml up -d --profile … After=docker.service`
  не существует (grep systemd units: только docker/tor/privoxy/platform-reboot/cert-expiry).
- авто-recovery: да — unless-stopped/always ретраят бесконечно (Docker exponential backoff);
  финальное состояние сходится.
- broken state: транзиентный (минуты): pgbouncer unhealthy до готовности postgres;
  litellm Prisma-migrate фейлится против лежащего pgbouncer (окно усугубляется:
  migrate 45-60s, start_period 60s — litellm base.yml:131-141); prometheus-config-init
  (restart:"no", monitoring base.yml:46) НЕ перезапускается daemon'ом — но конфиг живёт
  в volume `prometheus-config-gen` (root compose:91), поэтому prometheus поднимается
  нормально (проверено: volume персистентен, init нужен только при первом создании).
- retry безопасен: да; ручной `make converge NODE=<n>` (R9 up -d) безопасен и идемпотентен.
- user impact: LLM API/langfuse/дашборды — 5xx минуты; ingress (nginx) не зависит от БД
  (healthcheck nc на себя, nginx base.yml:22,106-113) — публичные 5xx только от мёртвых upstream'ов.
- alert: алерты сами в этом окне могут не работать (grafana/prometheus стартуют) → см. FAIL-0402.
- восстановление оператора: ждать самосходимость; проверить `make healthcheck NODE=<n>`;
  ускорить — `make converge NODE=<n>`.
- confidence: high для механики (daemon ≠ compose ordering — документированное поведение
  Docker); средняя для фактической длительности окна (HYPOTHESIS: 2-10 мин, не измерено).
- action: (a) принять и задокументировать окно в runbook; либо (b) минимальный фикс —
  oneshot systemd-unit `platform-stack.service` (`ExecStart=docker compose --profile all up -d`,
  `After=docker.service`, `Requires=docker.service`) — 10 строк; (c) drill из FAIL-0400
  замеряет фактическое окно.

### FAIL-0402 · HIGH · «Кто мониторит монитор»: падение observability-стека = полная тишина, dead-man's switch отсутствует
- scenario: S1+S2.
- evidence: alerting живёт ВНУТРИ стека — Grafana contact-points Telegram
  (monitoring base.yml:201-204), исходящие TG через хостовый прокси
  `host.docker.internal:8118` (monitoring base.yml:216-218, TRAP[BUG] 141 B14/B15).
  Единственный docker-независимый канал — host-level: watchdog cron (TG notify,
  `watchdog.py` run_watchdog) + cert/reboot notify (`reboot_policy.py:257-299`,
  stdlib-only). External heartbeat/UptimeRobot-класса в репозитории нет (grep
  heartbeat/uptime — только hermes build/skills monitor-http, т.е. тоже в контейнере).
- что происходит: если после reboot/crash не поднялись prometheus/grafana/loki —
  алертов нет вообще; watchdog покрывает ТОЛЬКО «running+unhealthy ≥10 мин» и шлёт
  TG при рестарте; кейс «crash-loop (RestartCount>5)», «контейнер отсутствует»,
  «стек целиком стоит» — молчит.
- где отказ: архитектурная дыра, не символ: `watchdog.py:372` (`is_restart_candidate`)
  фильтрует; skip-ветки без notify (см. FAIL-0403).
- авто-recovery: частичный (watchdog рестарты); если рестарт не лечит — тишина бессрочно.
- retry безопасен: да.
- user impact: тихий деград — инцидент узнаётся от пользователей/проектов.
- alert: именно его и нет — это finding.
- восстановление оператора: `make status NODE=`/ssh docker ps; `make healthcheck NODE=`;
  `make converge NODE=<n>`.
- confidence: high (по коду), external-монитор мог существовать вне репо (HYPOTHESIS-low).
- action (до launch, дешёво): внешний бесплатный heartbeat на
  `https://<домен>/status-page` health-endpoint (status-page base.yml:15 — есть
  /health) со уведомлением в тот же TG; плюс закрыть FAIL-0403 notify-дыру.

<!-- SPLIT: продолжение в findings-crash-restart-002.md -->
