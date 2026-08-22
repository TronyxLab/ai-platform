# Findings: Observability coverage matrix — pre-launch failure-mode audit (alerts/detection)

$ARTIFACT_CONTRACT
- PURPOSE: Полная матрица «сценарий отказа → авто-детект → чем → как оператор узнаёт → recovery verb» для 17 сценариев (вопросы №7 alert / №8 восстановление)
- SCOPE: research-only; ID-диапазон FAIL-1000–1099; evidence = file:symbol; дубли известных дыр (FAIL-0100/0201/0204/0300/0301/0303/0402/0405/0504) помечены [known], новые — FAIL-10NN
- REQUIRES: core/modules/monitoring/config/{platform-alerts.yml, alert-rules.yml, alerting/*}, config/prometheus.yml.tmpl, status-page/app.py, healthcheck/watchdog.py, bootstrap/cert_expiry_check.py, notification-catalog.yaml
- ACCEPTANCE: 17 строк матрицы; каждый новый finding имеет evidence file:symbol + action; cheat-sheet оператора
- RELATED: findings-alerts-002.md (детальные findings + cheat-sheet)

## Инвентарь детекции (что существует)

**Scrape-jobs** (`config/prometheus.yml.tmpl:47-157`): prometheus, litellm, cadvisor,
node-exporter, nginx-exporter, clickhouse, status-page, redis-exporter, postgres-exporter,
platform-projects (file_sd). **Вне scrape:** minio (FAIL-1000), loki/alloy (FAIL-1002),
langfuse+worker [known FAIL-0204], pgbouncer [known FAIL-0100], grafana само-скрейпа нет.

**Алерты Grafana** (`config/alerting/alert-rules.yml` — 10 правил): ServiceDown (up==bool 0,
critical, 1m), ServiceDownShort (warning, 15s), HighMemory (working_set >90% лимита, guard
limit>0), DiskSpace (<20%, critical, noDataState OK [known FAIL-0504]), LLMAPIErrors
(litellm failure-rate >0.1/s), BackupFreshness (Loki, 26h, noDataState Alerting — канарейка),
BackupUploadFailure (Loki), WalSyncFailure (Loki), PsiMemoryPressure (PSI stalled >5%),
Nginx5xxErrors (Loki, >2/5m).

**Алерты Prometheus native**: per-project `${PROJECT}ServiceDown/HighMemoryUsage/HighCPUUsage`
(`config/alert-rules.yml`), PlatformDeployBurnRate (<75%/24h) + PlatformImageSizeBudget +
PlatformBackupStale (`config/platform-alerts.yml`).

**Routing**: Alertmanager отключён (`prometheus.yml.tmpl:41-45`); Grafana Alerting → Telegram
Critical (push, repeat 24h [FAIL-1003]) / Telegram Warning (**без пуша**, `contact-points.yml:67`
[FAIL-1004]); quiet hours / mute timings — НЕТ; получатель — чаты оператора
(TELEGRAM_CHAT_ID_CRITICAL/WARNING, secrets.env).

**Прочее**: watchdog cron каждые 5м — unhealthy ≥10мин → docker restart + TG critical
(`core/internal/healthcheck/watchdog.py::run_watchdog`) [границы известны: FAIL-0402];
TLS-expiry — cert.expiry daily, threshold 14d (`core/internal/bootstrap/cert_expiry_check.py`,
platform-reboot.service ExecStart) — алерт СУЩЕСТВУЕТ, но script-based, не Prom-metric;
retention: Prometheus 15d (`monitoring/docker-compose.base.yml:105`), Loki 7d
(`logging/config/loki-config.yml`).

## Матрица 17 сценариев

| # | Сценарий | Авто-детект | Чем (file:symbol) | Оператор узнаёт | Recovery verb |
|---|----------|-------------|-------------------|-----------------|---------------|
| 1 | database unavailable | **partial** | postgres-exporter job (`prometheus.yml.tmpl:139`); смерть exporter'а → ServiceDown; `pg_up==0` алерта НЕТ (FAIL-1001); pgbouncer вслепую [FAIL-0100]; fallback watchdog ≥10мин | TG critical (ServiceDown/watchdog.restart) или **тишина** (exporter жив, БД мертва) | `make healthcheck NODE=` → `make converge NODE=`; порча данных — `make restore DUMP_FILE=` |
| 2 | redis unavailable | **partial** | redis-exporter job (`prometheus.yml.tmpl:126`); сам redis при живом exporter не алертится [FAIL-0201]; watchdog ≥10мин | TG critical (watchdog.restart) или тишина | `make converge NODE=` (cache-only — потеря допустима) |
| 3 | external API timeout | **partial** | LLMAPIErrors (`alerting/alert-rules.yml::llm_api_errors`, litellm failures); S3 — BackupUploadFailure/WalSyncFailure (Loki); внешние API проектов — ничего | TG warning **без пуша** [FAIL-1004] | fix-forward проекта; проверка провайдера |
| 4 | network partition | **partial** | внутренняя: ServiceDown (up==bool 0); исходящая к Telegram — SPOF канала [FAIL-0303]; полная изоляция ноды извне — НЕ детектируется [FAIL-0402] | TG может молчать (жертва и есть канал) — только заход оператором | ssh → `make e2e-verify NODE=` → `make converge NODE=` |
| 5 | process crash | **да** (с дырами) | restart-политики + watchdog (`watchdog.py::run_watchdog`); target-смерть → ServiceDownShort/ServiceDown; слепые зоны: minio (FAIL-1000), langfuse-worker [FAIL-0204] | TG critical | `make converge NODE=` / module `restart-hard` |
| 6 | machine restart | **partial** | docker restart-политики (unless-stopped/always); platform-reboot.service → reboot.executed/postponed (`notification-catalog.yaml`); нода не поднялась → никому не известно [FAIL-0402] | TG info (reboot.executed) или тишина | `make node-update NODE=`; крайний случай — `make bootstrap-node NODE=` (идемпотентен) |
| 7 | disk full | **да** (оговорка) | DiskSpace <20% critical (`alert-rules.yml::disk_space`); noDataState OK → смерть node-exporter при полном диске = тишина [FAIL-0504]; рост сдерживают retention 7d/15d | TG critical | `docker system prune` на ноде; расширение диска; verify `make e2e-verify NODE=` |
| 8 | memory pressure | **да** | HighMemory (per-container >90%) + PsiMemoryPressure (PSI stalled >5%, `alert-rules.yml::psi_memory_pressure`) + per-project HighMemoryUsage; OOM→рестарт→ServiceDownShort | TG warning / critical при down | поднять `deploy.resources.limits` в module compose → `make converge NODE=` |
| 9 | malformed response | **partial** | litellm `status="failure"` → LLMAPIErrors warning; HTTP-200 с битым телом — не детектируется | TG warning (без пуша) | fix-forward; upstream провайдер |
| 10 | duplicate request | **нет** | платформенных метрик/алертов на дубли нет (HYPOTHESIS: forced-command receive идемпотентен, но сигнала нет) | только ручной разбор `/var/log/platform/audit.jsonl` | разбор audit.jsonl вручную |
| 11 | corrupted state | **partial** (косвенно) | порча БД → pg_dump fail → BackupFreshness ≤26ч+time-gate 07:00 МСК (`alert-rules.yml::backup_freshness`); bootstrap state.json — тихое перевыполнение фаз (`state_machine.py::phase_needs_rerun`) | TG critical через ~сутки | `make restore DUMP_FILE=`; state.json — `rm` + `make bootstrap-node NODE=` |
| 12 | migration failure | **да** | ci.failure/project.deploy_failed/deploy.failed (`notification-catalog.yaml`); healthcheck-rollback образа | TG critical | fix-forward коммит (миграции НЕ откатываются, C5) |
| 13 | rollback | **да** | deploy.rollback critical (`notification-catalog.yaml`) + PlatformDeployBurnRate <75%/24h (`platform-alerts.yml:43`) | TG critical + warning | investigate → fix-forward |
| 14 | worker crash | **нет** | langfuse/langfuse-worker вне scrape и алертов [FAIL-0204]; cAdvisor-метрики есть, правила — нет; status-page containers покажет exited (cron 1м, display-only) | тишина (или заметно на странице статуса) | `make converge NODE=` / restart langfuse |
| 15 | queue backlog | **нет** | глубина очереди не метрифицируется и не алертится (смежно [FAIL-0201]/[FAIL-0204]) | тишина | restart langfuse/worker → `make healthcheck NODE=` |
| 16 | interrupted task | **partial** | CI-сторона: ci.failure (TG critical); сторона ноды: state machine возобновит при следующем update, зависший deploy — без алерта (HYPOTHESIS) | TG critical от GitHub Actions | повторить `make deploy-project PROJECT= NODE=` / `make node-update NODE=` |
| 17 | expired credential | **partial** | TLS: cert.expiry daily, threshold 14d (`cert_expiry_check.py::main`, TG critical; self-signed/S3 вне скана [FAIL-0300/0301]); AGE/SSH/GHCR/TG-токены/LiteLLM-keys expiry НЕ мониторятся — отказ проявляется косвенно (ci.failure, up==0 шум) | TG critical за ≤14 дней до истечения TLS | ротация §«Ротация SSH/CI-ключей» core/AGENTS.md; `make provision-llm`; `acme.sh --renew` |

## Итог по покрытию

- **Полный авто-детект: 5/17** — №5 (process crash\*), №7 (disk full\*), №8 (memory pressure), №12 (migration failure), №13 (rollback). \* с известными оговорками FAIL-0504/слепыми зонами.
- **Partial: 9/17** — database, redis, external API timeout, network partition, machine restart, malformed response, corrupted state, interrupted task, expired credential.
- **Слепые зоны (нет): 3/17** — duplicate request, worker crash [FAIL-0204], queue backlog.
- Сквозные слепые пятна поверх матрицы: postgres при живом exporter (FAIL-1001), minio (FAIL-1000), loki/alloy (FAIL-1002), канал доставки TG (SPOF [FAIL-0303]), внешний наблюдатель ([FAIL-0402]).

Детальные findings и операторский cheat-sheet → `findings-alerts-002.md`.
