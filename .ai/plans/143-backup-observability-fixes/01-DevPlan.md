# GREP_SUMMARY: DevPlan 143 backup-observability promtail file-scrape backup-logs cron-env entrypoint high_memory guard alert-rules
# STRUCTURE: ┌контекст+диагноз┐ → ◇ TRAP[DECISION] (3) → ┌код-граф XML┐ → ◇ data flows (3) → ┌waves W1A/W1B/W2┐ → ◇ acceptance criteria → ⎋ verification + деплой

$START_DEVPLAN

# DevPlan 143 — Наблюдаемость бэкапов (promtail file-scrape + cron env) и guard high_memory

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 2 скрытых дефекта, найденных при восстановлении ноды (142):
                       (1) алерт Backup Freshness firing вечно — маркер BACKUP COMPLETE не
                       попадает в Loki (cron пишет в файлы, promtail скрейпит только docker
                       stdout); бонус — cron не наследует env контейнера (POSTGRES_HOST not set);
                       (2) алерт High Memory Usage выдаёт ложные +Inf у контейнеров без
                       memory limits (деление на 0).
DESCRIPTION:           Три волны: W1A — promtail static_config file-scrape тома backup-logs
                       с лейблом compose_service="backup-cron" (alert-выражение не меняется);
                       W1B — Python entrypoint backup-cron, дампящий контейнерный env в
                       /etc/environment перед exec cron -f (Debian cron читает его при старте);
                       W2 — guard `and container_spec_memory_limit_bytes > 0` в обоих
                       правилах high_memory (Grafana provisioning + per-project Prometheus шаблон).
RATIONALE:             Минимальные точечные фиксы по подтверждённому диагнозу (инвариант 11
                       не затрагивается — конфиги, не generated-файлы; language policy — новый
                       код Python: entrypoint.py; alert-выражение backup_freshness остаётся
                       неизменным — file-scrape несёт тот же лейбл compose_service).
ACCEPTANCE_CRITERIA:   AC1: Loki содержит stream {compose_service="backup-cron"} из файлового
                       скрейпа; после ручного прогона backup count_over_time(BACKUP COMPLETE
                       [1h]) >= 1; алерт Backup Freshness → Normal. AC2: cron-запуск бэкапа
                       (не docker exec) видит POSTGRES_HOST/S3_* и пишет BACKUP COMPLETE.
                       AC3: оба high_memory expr содержат guard; unit-детектор + R5 negative;
                       на ноде 0 серий +Inf в high_memory. AC4: `make check` зелёный;
                       macОS dev-стек (test overrides backup-logs-test) работает.
IMPLEMENTS:            Диагноз оператора 2026-08-08 (итог восстановления 142); D-7 (126)
                       закрыт частично (персистентный след есть, но Loki его не видит —
                       настоящий план); алерт-файлы chaos-данных 126 T1-T5 (alerts.json).
IMPACTS:               core/modules/logging/* (promtail config + compose + test.yml);
                       core/modules/backup-cron/* (Dockerfile + entrypoint.py);
                       core/modules/monitoring/config/{alert-rules.yml, alerting/alert-rules.yml};
                       tests/unit/test_promtail_config.py, test_monitoring_alert_rules.py,
                       новый test_backup_cron_entrypoint.py. Деплой: SCP/rsync core →
                       node-update φ12 (пересборка backup-cron, рестарт promtail, провижининг
                       Grafana/Prometheus правил).
REQUIRES:              Доступ к ноде (SSH) для верификации; Grafana provisioning reload
                       (мониторинг-модуль); пересборка образа backup-cron на ноде.
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и диагноз (2026-08-08, восстановление 142)

| # | Алерт | Симптом | Корень (подтверждён) |
|---|-------|---------|----------------------|
| D1 | **Backup Freshness** (firing, critical) | `No BACKUP COMPLETE in the last 26h`, firing с 2026-08-07 14:03Z | Архитектурный разрыв: crontab `backup-postgres.sh >> /var/log/platform/backup/postgres.log 2>&1` (core/modules/backup-cron/scripts/crontab:28) — маркер `[IMP:9] BACKUP COMPLETE` уходит в файл на томе `backup-logs` (bind `/var/log/platform/backup`, root docker-compose.yml:71-76), а promtail скрейпит ТОЛЬКО docker stdout (docker_sd) + journald → Loki не видит маркер никогда (count_over_time=0). Плюс: cron не наследует env контейнера → POSTGRES_HOST/S3_* не заданы в 03:00 → бэкап по расписанию падает (ручной docker exec работает — env наследуется). |
| D2 | **High Memory Usage** (pending, warning) | `+Inf`, метки `[no value]`, value_string `B=+Inf` | Проектные контейнеры деплоятся БЕЗ `deploy.resources.limits.memory` → cadvisor не экспортирует `container_spec_memory_limit_bytes` (0/absent) → `usage / 0 = +Inf` → `+Inf > 0.9` = true. Реальная память: max clickhouse 66%. Правила: alerting/alert-rules.yml:149 (Grafana provisioning) и config/alert-rules.yml:43 (per-project шаблон). |

Оба фикса первого эшелона (nginx-stub, Prometheus TSDB) УЖЕ выполнены оператором; настоящий план — только D1+D2.

---

## 2. Ключевые решения

### ⚠️ TRAP[DECISION] · 2026-08-08 · HI · W1A: file-scrape с лейблом compose_service="backup-cron" (а не изменение alert-выражения)
· Rejected: (a) переписать backup_freshness на host-метрики backup_collector.py (core/internal/healthcheck/metrics/) — смена datasource правил Grafana + потеря лог-трассы; (b) дублировать stdout в docker logs через tee — теряется персистентность файлов (D-7: файлы = след инцидента ENOSPC).
· Reason: новый static_config job `backup-logs` (glob `/var/log/platform/backup/*.log`) с labels `{compose_service="backup-cron"}` — существующее выражение `count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h]) < 1` начинает работать БЕЗ изменений. Файлы остаются персистентным следом (D-7), promtail читает с offsets (positions).
· Rev: если появится второй потребитель файлового скрейпа с хоста — вынести общий паттерн (прецедент: journal-скрейп 132 W3).

### ⚠️ TRAP[DECISION] · 2026-08-08 · HI · W1B: entrypoint.py → /etc/environment (Python, language policy)
· Rejected: (a) crontab-shim `. /etc/container-env &&` на каждую строку — меняет crontab (тесты test_backup_cron.py:340/385 пиннят его, 5 правок), идемпотентность хуже; (b) shell-entrypoint `env > /etc/environment; exec cron -f` — нарушение языковой политики (новый код = Python); (c) env в образе при build — секреты runtime (compose), в образе их нет.
· Reason: Debian cron (vixie, bookworm) читает `/etc/environment` при старте демона и наследует его переменные всеми job'ами (Debian-специфичный патч). Entrypoint: `render_env_lines(os.environ)` → `/etc/environment` (mode 0600) → `os.execvp("cron", ["cron", "-f"])`. PID 1 = cron (healthcheck `pgrep cron` не меняется). Чистая функция render_env_lines — unit-тестируема (tmp_path).
· Rev: если на ноде верификация покажет, что job'ы НЕ наследуют /etc/environment → fallback: префикс crontab-строк `. /etc/environment &&` (crontab+тесты меняются согласованно).

### ⚠️ TRAP[DECISION] · 2026-08-08 · MED · W2: guard `and container_spec_memory_limit_bytes > 0` в обоих правилах
· Rejected: (a) деплой limits на все проекты — поведенческое изменение (риск OOM-kill), требуется отдельное решение по шаблонам проектов; (b) clamp_min(limit,1) — маскирует +Inf в ~100%, guard честнее (серия отсутствует).
· Reason: PromQL: деление на 0 → +Inf; `+Inf > 0.9` — true; guard `and limit > 0` отфильтровывает серии без лимита целиком. Оба файла (Grafana provisioning + per-project Prometheus шаблон) фиксим за раз — иначе divergence (шаблон рендерится в /opt/prometheus/rules/<project>-alerts.yml, Grafana-правило живёт в provisioning).
· Rev: если проекты получат limits в шаблонах — guard останется корректным (двойная защита), пересмотр не нужен.

---

## 3. Draft Code Graph

```xml
<knowledge_graph>
  <entity name="promtail_config_yml" type="CONFIG">
    <keywords>scrape_configs static_configs job=backup-logs file-targets compose_service=backup-cron __path__ log_file labels</keywords>
    <annotation>W1A: третий scrape-конфиг; alert-expr не меняется</annotation>
    <CrossLinks>logging_docker_compose_base_yml, backup_loki_stream</CrossLinks>
  </entity>
  <entity name="logging_docker_compose_base_yml" type="CONFIG">
    <keywords>promtail volumes backup-logs:/var/log/platform/backup:ro</keywords>
    <annotation>W1A: named-volume mount (SoT root compose, U-49), host-литералов нет</annotation>
    <CrossLinks>test_gate_volumes_sot_py</CrossLinks>
  </entity>
  <entity name="logging_docker_compose_test_yml" type="CONFIG">
    <keywords>promtail-test backup-logs-test volume-rename U-62</keywords>
    <annotation>W1A: канон volume-rename (116 B7 T8) — docker-managed volume для macOS/CI</annotation>
    <CrossLinks>backup_cron_docker_compose_test_yml</CrossLinks>
  </entity>
  <entity name="entrypoint_py" type="MODULE">
    <keywords>render_env_lines write_env_file /etc/environment 0600 os.execvp cron -f</keywords>
    <annotation>W1B: Python-обёртка (language policy), PID1=exec cron</annotation>
    <CrossLinks>Dockerfile, test_backup_cron_entrypoint_py</CrossLinks>
  </entity>
  <entity name="backup_cron_Dockerfile" type="BUILD">
    <keywords>COPY entrypoint.py CMD python3 entrypoint</keywords>
    <annotation>W1B: CMD cron -f → CMD python3 /usr/local/bin/entrypoint.py</annotation>
    <CrossLinks>entrypoint_py</CrossLinks>
  </entity>
  <entity name="monitoring_alerting_alert_rules_yml" type="CONFIG">
    <keywords>high_memory container_memory_usage_bytes container_spec_memory_limit_bytes > 0 guard</keywords>
    <annotation>W2: Grafana provisioning (datasourceUid=prometheus)</annotation>
    <CrossLinks>test_monitoring_alert_rules_py</CrossLinks>
  </entity>
  <entity name="monitoring_config_alert_rules_yml" type="CONFIG">
    <keywords>HighMemoryUsage template PROJECT compose_project guard</keywords>
    <annotation>W2: per-project Prometheus шаблон (render-monitoring) — parity с provisioning</annotation>
    <CrossLinks>monitoring_config_renderer_py</CrossLinks>
  </entity>
  <entity name="test_promtail_config_py" type="TEST">
    <keywords>job_names docker journal backup-logs volumes assertions</keywords>
    <annotation>W1A: обновление 2 тестов + новый backup-logs job тест</annotation>
    <CrossLinks>promtail_config_yml</CrossLinks>
  </entity>
  <entity name="test_monitoring_alert_rules_py" type="TEST">
    <keywords>high_memory guard detector R5 negative</keywords>
    <annotation>W2: детектор guard (паттерн D-6 mountpoint) + negative R5</annotation>
    <CrossLinks>monitoring_alerting_alert_rules_yml</CrossLinks>
  </entity>
  <entity name="test_backup_cron_entrypoint_py" type="TEST">
    <keywords>render_env_lines tmp_path 0600 multiline-skip</keywords>
    <annotation>W1B: unit чистой функции render_env_lines (0 docker)</annotation>
    <CrossLinks>entrypoint_py</CrossLinks>
  </entity>
</knowledge_graph>
```

---

## 4. Step-by-step Data Flow

**Flow 1 — маркер бэкапа → Loki (W1A):**
```
cron 03:00 → backup-postgres.sh → backup_postgres.py logger.info("[IMP:9] BACKUP COMPLETE")
  → stdout → crontab `>> /var/log/platform/backup/postgres.log 2>&1`
  → том backup-logs (bind /var/log/platform/backup, root docker-compose.yml:71)
  → promtail static_config job=backup-logs (mount backup-logs:/var/log/platform/backup:ro)
  → positions.yaml (offset-трекинг) → Loki stream {job="backup-logs", compose_service="backup-cron",
    log_file="postgres.log"}
  → Grafana backup_freshness: count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h]) >= 1
  → NoData → Normal (алерт снимается, спам прекращён)
```
Инвариант: alert-выражение НЕ меняется — file-scrape несёт тот же лейбл. ВАЖНО: при первом старте promtail прочитает существующий postgres.log целиком (backfill) — ручной бэкап 2026-08-08 (UPLOAD OK) уже содержит маркер → алерт закроется сразу, без ожидания 03:00.

**Flow 2 — env для cron job'ов (W1B):**
```
container start (compose env: POSTGRES_HOST/S3_*/WAL_*/BACKUP_SPOOL_DIR)
  → python3 /usr/local/bin/entrypoint.py
  → render_env_lines(os.environ) → write /etc/environment (mode 0600, root)
  → os.execvp("cron", ["cron", "-f"])   # PID 1 = cron (healthcheck pgrep не меняется)
  → Debian cron читает /etc/environment при старте демона
  → job 03:00 наследует POSTGRES_HOST=postgres, S3_ACCESS_KEY, ... → pg_dumpall → S3 upload
  → [IMP:9] BACKUP COMPLETE → postgres.log → Flow 1
```

**Flow 3 — high_memory без ложных +Inf (W2):**
```
cadvisor → container_spec_memory_limit_bytes{...}=0 (контейнер без limits)
  → OLD: usage / 0 = +Inf → +Inf > 0.9 = true → FIRING (ложное, [no value])
  → NEW: usage / limit > 0.9 AND limit > 0 → серия отфильтрована → не срабатывает
  → реальный случай: clickhouse (limit задан) — считает честно
```

---

## 5. File Manifest

| Файл | Операция | Содержимое |
|------|----------|------------|
| `core/modules/logging/config/promtail-config.yml` | MODIFY | Третий scrape-конфиг `job_name: backup-logs`: static_configs targets `[localhost]`, labels `{compose_service="backup-cron"}`, `__path__: /var/log/platform/backup/*.log`, relabel `__path__ → log_file`; обновить GREP_SUMMARY/STRUCTURE/контракт. |
| `core/modules/logging/docker-compose.base.yml` | MODIFY | promtail volumes: `- backup-logs:/var/log/platform/backup:ro` (named-volume, SoT root compose; комментарий со ссылкой на DevPlan 143). |
| `core/modules/logging/docker-compose.test.yml` | MODIFY | promtail-test: rebind `backup-logs-test:/var/log/platform/backup:ro` (канон volume-rename U-62) + top-level `backup-logs-test: {driver: local}`. |
| `core/modules/backup-cron/scripts/entrypoint.py` | CREATE | Python entrypoint: `render_env_lines(env) -> list[str]` (KEY=VALUE, skip значений с \n), `write_env_file(env, path)` (mode 0600), `main()` → write → `os.execvp("cron", ["cron", "-f"])`. LDD [IMP:7/9] логи. |
| `core/modules/backup-cron/Dockerfile` | MODIFY | COPY `scripts/entrypoint.py /usr/local/bin/entrypoint.py`; CMD `["python3", "/usr/local/bin/entrypoint.py"]`; chmod +x. |
| `core/modules/monitoring/config/alerting/alert-rules.yml` | MODIFY | high_memory expr (строка 149): `container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9 and container_spec_memory_limit_bytes > 0`. |
| `core/modules/monitoring/config/alert-rules.yml` | MODIFY | HighMemoryUsage шаблон (строка 43): тот же guard с селектором `{compose_project="${PROJECT}"}` на обоих операндах guard'а. |
| `tests/unit/test_promtail_config.py` | MODIFY | `test_promtail_docker_job_unchanged`: `["docker", "journal", "backup-logs"]`; новый тест backup-logs job (glob, labels compose_service, log_file relabel); `test_promtail_compose_journald_volumes` → +`backup-logs:/var/log/platform/backup:ro`. |
| `tests/unit/test_monitoring_alert_rules.py` | MODIFY | Новый детектор `_assert_high_memory_guard(expr)` (guard на обоих операндах-делителе) + тест `test_provisioning_alert_rules_high_memory_guard` + R5 negative `test_high_memory_guard_negative_removed` (legacy expr без guard → AssertionError). |
| `tests/unit/test_backup_cron_entrypoint.py` | CREATE | Unit: render_env_lines (KEY=VALUE, multiline skip), write_env_file (mode 0600, tmp_path), exec-таргет константа. LDD [IMP:9]. |

Затрагиваемых generated-файлов НЕТ (инвариант 11 не в игре: entrypoint.py — новый модуль, конфиги — не generated). Crontab НЕ меняется (решение W1B) → `test_backup_cron.py` не трогается.

---

## 6. Waves

### W1A — promtail file-scrape backup-logs
1. promtail-config.yml: job `backup-logs` (static_configs + labels + relabel).
2. logging base.yml: mount `backup-logs:ro`; test.yml: rebind `backup-logs-test` + volume decl.
3. Обновить 2 теста test_promtail_config.py + добавить backup-logs job тест.
4. Локальная проверка: `docker compose -f base.yml -f test.yml config` (валидность), `make check-diff`.

### W1B — cron env через entrypoint.py
1. entrypoint.py (render_env_lines/write_env_file/main).
2. Dockerfile: COPY + CMD.
3. tests/unit/test_backup_cron_entrypoint.py.
4. Локальная проверка: `make check` (ruff/doxygen/тесты); сборка образа `make up MODULES=backup-cron` (или docker build вручную).

### W2 — guard high_memory
1. alerting/alert-rules.yml: expr + guard.
2. config/alert-rules.yml (шаблон): expr + guard (parity).
3. test_monitoring_alert_rules.py: детектор + позитив + R5 negative.
4. Локальная проверка: `pytest tests/unit/test_monitoring_alert_rules.py -q`.

---

## 7. Acceptance Criteria

| ID | Критерий | Верификация |
|----|----------|-------------|
| AC1 | Loki видит маркер из файлового скрейпа | На ноде: `docker exec backup-cron /usr/local/bin/backup-postgres.sh` (ручной прогон) → Loki: `count_over_time({compose_service="backup-cron"} \|~ "BACKUP COMPLETE" [1h]) >= 1` через 1-2 мин (backfill читает старый postgres.log сразу). |
| AC2 | Backup Freshness → Normal | Grafana: алерт `backup_freshness` state=Normal (после AC1 backfill — сразу, без ожидания 03:00). |
| AC3 | Cron-запуск бэкапа работает с env | В postgres.log за 03:00 следующий прогон: `[IMP:9] BACKUP COMPLETE` (не ошибка env). Проверка наследования env: `docker exec backup-cron sh -c 'crontab -l'` + разовая диагностика: `docker exec backup-cron sh -c '. /etc/environment && echo $POSTGRES_HOST'` → `postgres`. |
| AC4 | High memory: guard в обоих правилах | `rg "container_spec_memory_limit_bytes > 0" core/modules/monitoring/config/` → 2 файла; pytest unit (детектор+negative) PASS; на ноде Prometheus: `container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9 and container_spec_memory_limit_bytes > 0` → 0 серий +Inf (алерт high_memory не firing). |
| AC5 | Локальный стек не сломан | `make check` зелёный (включая volumes-SoT гейт: backup-logs уже в root, счётчик 12 не меняется); `docker compose -f core/modules/logging/docker-compose.base.yml -f core/modules/logging/docker-compose.test.yml config` валиден. |
| AC6 | Платформенные тесты | `make test-summary TEST_FILE=tests/unit/test_promtail_config.py` и `tests/unit/test_monitoring_alert_rules.py` и `tests/unit/test_backup_cron_entrypoint.py` — PASS. |

---

## 8. Деплой на ноду

Канал: Core SCP/rsync (push). Порядок:
1. `make fix-gate && git add -u && make check` → коммиты (docs + feat, ≤2 по Commit Policy).
2. Деплой core на ноду (`make node-update NODE=<n>` / core-deliver) — φ12 deploy_update:
   - backup-cron: пересборка образа (Dockerfile изменился) → `docker compose build backup-cron` + up;
   - logging: рестарт promtail (`docker compose up -d promtail`) — подхватит новый config + mount;
   - monitoring: провижининг Grafana alerting (reload) + render-monitoring per-project правил (перегенерация <project>-alerts.yml на ноде).
3. Выполнить AC1-AC4 на ноде. Алерт Backup Freshness должен сняться (backfill старого лога).

## 9. Out of scope / follow-ups

- **Memory limits для проектов** (корень +Inf): шаблоны проектов не задают limits — сознательно НЕ трогаем в W2 (guard достаточно, поведенческое изменение OOM-семантики — отдельное решение). Зафиксировано как Rev-условие TRAP W2.
- **Ротация файлов** `/var/log/platform/backup/*.log`: файлы растут без logrotate. Не критично (объём мал), promtail позиции устойчивы; вынести в debt-tracker при необходимости.
- **backup_collector.py** (host-метрики бэкапов) остаётся как есть — Loki-канал теперь первичный.

$END_DEVPLAN
