# GREP_SUMMARY: DevPlan 144 backup-freshness loki-binop noDataState high-memory labels.name memory-limits cadvisor loki clickhouse alert-rules
# STRUCTURE: ┌контекст+диагноз (3 D)┐ → ◇ TRAP[DECISION] (2) → ┌код-граф XML┐ → ◇ data flows (2) → ┌waves W1/W2/W3┐ → ◇ acceptance criteria → ⎋ verification + деплой

$START_DEVPLAN

# DevPlan 144 — Фикс ложного алерта Backup Freshness (Loki binop) и реальных превышений памяти

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 2 категории алертов, приходящих в Telegram каждые 5 минут:
                       (1) Backup Freshness CRITICAL «No BACKUP COMPLETE in the last 26h» —
                       ЛОЖНЫЙ алерт: маркер BACKUP COMPLETE ЕСТЬ в Loki (count=2 за 26h),
                       но Grafana-правило сконфигурировано так, что firing ВЕЧНО из-за
                       бинарной операции `< 1` внутри Loki-выражения + noDataState: Alerting;
                       (2) High Memory WARNING «Container no value memory usage exceeds 90%» —
                       РЕАЛЬНЫЕ превышения: cadvisor 100% (127.3/128MiB), loki 91.3%
                       (216/256MiB), clickhouse 91.5% (1GiB лимит); плюс текст «no value» —
                       метка `container` отсутствует у cAdvisor-метрик (есть `name`).
DESCRIPTION:           Три волны: W1 — Backup Freshness: убрать бинарную операцию `< 1`
                       из Loki-выражения (сравнение оставить в Grafana threshold expression),
                       окно [26h] и noDataState: Alerting сохраняются; W2 — High Memory:
                       аннотации summary/description перевести с `{{ $labels.container }}`
                       на `{{ $labels.name }}` (cAdvisor экспортирует name, не container)
                       в ОБОИХ файлах правил (Grafana provisioning + per-project шаблон);
                       W3 — поднять memory limits: cadvisor 128M→256M, loki 256M→512M,
                       clickhouse 1G→2G (docker-compose.base.yml + module.yaml resources
                       sync по канону) — реальные превышения гасятся с запасом.
RATIONALE:             Минимальные точечные фиксы по подтверждённому на ноде диагнозу
                       (инвариант 11 не затрагивается — конфиги; W1 — контракт Grafana:
                       Loki expr должен возвращать метрику, сравнение — в threshold expression;
                       guard 143 W2 остаётся в силе (working set 84-86% показывает, что
                       лимиты тесные, но не OOM-критичные). Данные Loki подтверждают: W1A 143
                       работает, проблема — в выражении правила, а не в доставке логов.
ACCEPTANCE_CRITERIA:   AC1: Grafana-правило backup_freshness → Normal (не firing) при
                       наличии BACKUP COMPLETE в 26h окне; expr Loki БЕЗ бинарного оператора,
                       сравнение только в threshold expression (lt 1). AC2: аннотации
                       high_memory используют `{{ $labels.name }}` (0 упоминаний
                       `$labels.container` в обоих alert-rules.yml); на ноде алерт показывает
                       имя контейнера (cadvisor/loki/clickhouse), не «no value».
                       AC3: лимиты в compose: cadvisor ≥256M, loki ≥512M, clickhouse ≥2G;
                       module.yaml resources синхронизированы; `docker compose config`
                       валиден; на ноде docker stats: cadvisor ≤50%, loki ≤45%, clickhouse ≤50%.
                       AC4: `make check` зелёный; R5-тесты (positive + negative) на каждый фикс.
IMPLEMENTS:            Диагноз оператора 2026-08-09 (алерты каждые 5 мин); 143 W1A/W2
                       подтверждены рабочими на ноде (файловый скрейп даёт count=2, guard
                       отфильтровал +Inf серии) — настоящий план закрывает остаточные причины.
IMPACTS:               core/modules/monitoring/config/alerting/alert-rules.yml;
                       core/modules/monitoring/config/alert-rules.yml;
                       core/modules/infra-metrics/docker-compose.base.yml (+ module.yaml);
                       core/modules/logging/docker-compose.base.yml (+ module.yaml);
                       core/modules/clickhouse/docker-compose.base.yml (+ module.yaml);
                       tests/unit/test_monitoring_alert_rules.py (+ новые тесты лимитов).
                       Деплой: SCP/rsync core → нода; рестарт/провижининг Grafana (правила),
                       docker compose recreate для infra-metrics/logging/clickhouse (лимиты).
REQUIRES:              Доступ к ноде (SSH) для верификации; Grafana provisioning reload;
                       recreate контейнеров с новыми лимитами (без дата-потери — named volumes).
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и диагноз (2026-08-09, алерты каждые 5 мин в Telegram)

| # | Алерт | Симптом | Корень (подтверждён на ноде) |
|---|-------|---------|------------------------------|
| D1 | **Backup Freshness** (firing, critical) | `No BACKUP COMPLETE in the last 26h`, повтор каждые ~5 мин | **Ложный алерт из-за бинарной операции в Loki expr.** Файловый скрейп W1A (143) РАБОТАЕТ: `count_over_time({compose_service="backup-cron"} \|~ "BACKUP COMPLETE" [26h])` = **2** (записи 2026-08-08T06:26Z и 2026-08-09T03:00Z). НО Grafana-правило (alerting/alert-rules.yml:306) содержит `... [26h]) < 1` ВНУТРИ Loki-выражения. Loki для range-запросов с бинарной операцией возвращает ТОЛЬКО точки, где условие истинно: при count=1-2 (маркеры есть) `count < 1` ложно → **пустая матрица** → Grafana reduce → NoData → `noDataState: "Alerting"` (строка 338) → firing. Подтверждение на ноде: ручной vector-query `count_over_time(...)` → value 1; `count_over_time(...) < 1` → пусто; `count_over_time(...) > 1` → только точки count=2. Grafana API: `Backup Freshness | firing | ok`, since 2026-08-07T14:03:50Z — с момента создания правила. |
| D2 | **High Memory Usage** (firing, warning) | `Container no value memory usage exceeds 90%` ×3 (каждые 5 мин) | **Реальные превышения + дефект аннотации.** Guard 143 W2 работает (+Inf серии отфильтрованы: 6→3). Остались: **cadvisor 100%** (127.3/128MiB — лимит тесный!), **loki 91.3%** (216/256MiB), **clickhouse 91.5%** (1GiB лимит; docker stats 62% — cAdvisor usage включает page cache, но алерт всё равно firing). Текст «no value»: summary `Container {{ $labels.container }}` (строка 192), а у cAdvisor-метрик метки `container` НЕТ — есть `name` и `container_label_com_docker_compose_service` (проверено: `container_memory_usage_bytes / container_spec_memory_limit_bytes > 0.9` → metric: name=loki/cadvisor/clickhouse, container отсутствует). Тот же дефект в per-project шаблоне config/alert-rules.yml:51-57. |
| D3 | (сопутствующее) | — | W1A 143 подтверждён рабочим (count=2, серии backup-logs в Loki). Guard W2 143 подтверждён рабочим (0 серий +Inf). НЕ трогать — работают. |

Итог: 143 W1A/W2 устранили первопричины доставки/фильтрации; остались (1) неверный контракт Loki-выражения Grafana-правила (D1) и (2) реальная нехватка лимитов + неверная метка в аннотациях (D2).

---

## 2. Ключевые решения

### ⚠️ TRAP[DECISION] · 2026-08-09 · HI · W1: сравнение `< 1` убрать из Loki expr, оставить в Grafana threshold
· Rejected: (a) сменить noDataState Alerting→OK («тихий» алерт при полном отсутствии логов — маскирует мёртвый promtail); (b) оставить как есть («погодит, вдруг само» — алерт шлёт ложный CRITICAL каждые 5 минут); (c) переписать expr на `count_over_time(...) == 0` — та же проблема (Loki binop возвращает только истинные точки).
· Reason: контракт Grafana alerting для Loki: выражение data-запроса возвращает МЕТРИКУ (count), сравнение выполняет threshold expression (refId C, evaluator lt params [1] — уже настроен!). Loki binop-семантика: range query с `x < 1` возвращает только точки, где истинно (значение исходного expr) — при наличии маркеров это пусто, а NoData → Alerting. Убираем `< 1` из expr → Loki всегда возвращает count (0, 1, 2...) → threshold `lt 1`: count=0 → firing (бэкап не работал), count≥1 → Normal, NoData → Alerting (нет логов вообще — промtail мёртв). Все 3 состояния корректны.
· Rev: если Grafana изменит семантику binop для Loki (range → возврат 0/1) — вернуть `< 1` в expr.

### ⚠️ TRAP[DECISION] · 2026-08-09 · HI · W2: аннотации `{{ $labels.name }}` вместо `{{ $labels.container }}`
· Rejected: (a) relabel в prometheus scrape config (container←name) — трогает prometheus.yml.tmpl, лишняя сложность, label может конфликтовать с docker_sd; (b) использовать `container_label_com_docker_compose_service` — длинная метка, но для не-compose контейнеров (cadvisor) пустая; (c) оставить container — «no value» в каждом сообщении.
· Reason: cAdvisor экспортирует `name` = имя контейнера (cadvisor/loki/clickhouse/...) у всех контейнеров. `{{ $labels.name }}` — правильная метка для cAdvisor-источника. Меняем в ОБОИХ файлах (Grafana provisioning + per-project шаблон) — иначе divergence (шаблон рендерится в /opt/prometheus/rules/<project>-alerts.yml).
· Rev: если появится второй источник container_memory_* (не cAdvisor) без name — пересмотреть.

### ⚠️ TRAP[DECISION] · 2026-08-09 · MED · W3: лимиты cadvisor 128→256M, loki 256→512M, clickhouse 1G→2G
· Rejected: (a) не трогать лимиты, подавить алерт (guard >0.95) — маскирует OOM-риск (cadvisor на 99.4%!); (b) перейти на container_memory_working_set_bytes в expr — меняет контракт метрики (working set 84-86% у cadvisor/loki — всё ещё >80%), требует ре-верификации, риск расхождения с dashboards (используют usage_bytes); (c) лимиты 512M/1G/4G — избыточно (хост 7.9G total, 4.1G used, 3.8G available).
· Reason: фактические потребления: cadvisor 127.3MiB → 256M даёт 50% запас; loki 216MiB → 512M даёт 42%; clickhouse 639MiB (docker stats) → 2G даёт 32% (с учётом page cache cAdvisor usage 91.5% — 2G достаточно). Суммарный прирост лимитов +768M — безопасен для хоста. module.yaml resources синхронизируются (канон «синхронизировано с base.yml»): infra-metrics 224M→352M (256+96? — по сумме сервисов модуля), logging 384M→640M (512+128), clickhouse 512M→1024M.
· Rev: если фактические usage вырастут >70% новых лимитов — пересмотреть (мониторинг продолжит сигналить).

---

## 3. Draft Code Graph

```xml
<devplan144>
  <diagnosis>
    <d1 id="backup-freshness-false-positive">
      <evidence>Loki count=2 / Grafana firing since 08-07T14:03:50Z / range binop -> empty</evidence>
      <file>core/modules/monitoring/config/alerting/alert-rules.yml</file>
      <line>306 (expr), 338 (noDataState)</line>
    </d1>
    <d2 id="high-memory-real+label">
      <evidence>cadvisor 100% / loki 91.3% / clickhouse 91.5% / no container label</evidence>
      <file>core/modules/monitoring/config/alerting/alert-rules.yml</file>
      <file>core/modules/monitoring/config/alert-rules.yml</file>
    </d2>
  </diagnosis>
  <wave id="W1" name="backup-freshness-loki-expr">
    <file>core/modules/monitoring/config/alerting/alert-rules.yml</file>
    <change>expr: "count_over_time({compose_service=\"backup-cron\"} |~ \"BACKUP COMPLETE\" [26h])" (без "< 1")</change>
    <keep>threshold C lt 1, noDataState Alerting, relativeTimeRange 93600</keep>
  </wave>
  <wave id="W2" name="high-memory-annotations">
    <file>core/modules/monitoring/config/alerting/alert-rules.yml</file>
    <file>core/modules/monitoring/config/alert-rules.yml</file>
    <change>summary/description: {{ $labels.container }} -> {{ $labels.name }} (HighMemory + per-project HighMemoryUsage)</change>
  </wave>
  <wave id="W3" name="memory-limits">
    <file>core/modules/infra-metrics/docker-compose.base.yml</file>
    <file>core/modules/infra-metrics/module.yaml</file>
    <file>core/modules/logging/docker-compose.base.yml</file>
    <file>core/modules/logging/module.yaml</file>
    <file>core/modules/clickhouse/docker-compose.base.yml</file>
    <file>core/modules/clickhouse/module.yaml</file>
    <change>cadvisor 128M->256M; loki 256M->512M; clickhouse 1G->2G (+ resources sync)</change>
  </wave>
  <tests>
    <file>tests/unit/test_monitoring_alert_rules.py</file>
    <change>R5 negative: expr с "< 1" в Loki -> AssertionError; positive: expr без binop</change>
    <change>детектор аннотаций: name, не container (оба файла) + R5 negative</change>
    <file>tests/unit/test_memory_limits.py (NEW)</file>
    <change>лимиты compose: cadvisor>=256M, loki>=512M, clickhouse>=2G + module.yaml sync + R5 negative</change>
  </tests>
</devplan144>
```

---

## 4. Data flows (диагноз → фикс)

```
DF1 Backup Freshness (было):  backup-cron >> backup-logs volume >> promtail file-scrape
  >> Loki stream {compose_service="backup-cron"} [count=2] >> Grafana expr "... [26h]) < 1"
  >> Loki range: binop фильтрует (count<1 ложно) >> ПУСТО >> reduce NoData
  >> noDataState Alerting >> FIRING (ложно, каждые 5 мин)
DF1 (станет): Loki expr без binop >> count=2 >> threshold lt 1: 2<1 false >> NORMAL
  [бэкап не работал: count=0 → 0<1 true → firing; промtail мёртв: NoData → Alerting]

DF2 High Memory (было): cAdvisor metrics (name=loki, container ОТСУТСТВУЕТ)
  >> expr usage/limit > 0.9 and limit > 0 (guard 143 работает)
  >> summary "Container {{ $labels.container }}" >> "no value" >> firing ×3
DF2 (станет): summary "Container {{ $labels.name }}" >> "Container loki/cadvisor/clickhouse"
  + лимиты 256M/512M/2G >> cadvisor 50%, loki 42%, clickhouse 32% >> Normal
```

---

## 5. Waves

### W1 — Backup Freshness: убрать binop из Loki expr (D1)

**Файл:** `core/modules/monitoring/config/alerting/alert-rules.yml` (uid: backup_freshness)

```yaml
# БЫЛО (строка 306):
expr: 'count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h]) < 1'
# СТАНЕТ:
expr: 'count_over_time({compose_service="backup-cron"} |~ "BACKUP COMPLETE" [26h])'
```

**НЕ менять:** threshold expression C (evaluator lt params [1]), noDataState: Alerting, relativeTimeRange from: 93600, for: 30m. Контракт: Loki возвращает метрику (count), сравнение — threshold (lt 1). Обновить MODULE_CONTRACT (rationale: Loki binop-семантика + контракт Grafana).

### W2 — High Memory: аннотации name (D2)

**Файл:** `core/modules/monitoring/config/alerting/alert-rules.yml` (uid: high_memory)
- summary: `Container {{ $labels.container }} memory usage exceeds 90%` → `Container {{ $labels.name }} memory usage exceeds 90%`
- description: `Memory usage for container {{ $labels.container }} on {{ $labels.instance }}...` → `{{ $labels.name }}`

**Файл:** `core/modules/monitoring/config/alert-rules.yml` (per-project шаблон, alert: ${PROJECT}HighMemoryUsage)
- summary: `Container {{ $labels.container }} memory > 90% (project: ${PROJECT})` → `Container {{ $labels.name }} memory > 90% (project: ${PROJECT})`
- description: аналогично.

Обновить MODULE_CONTRACT/GREP_SUMMARY в обоих файлах. Guard W2 (143) НЕ трогать.

### W3 — Memory limits (D2, реальные превышения)

| Сервис | Файл | Было | Станет |
|--------|------|------|--------|
| cadvisor | infra-metrics/docker-compose.base.yml (~строка 45) | 128M | 256M |
| loki | logging/docker-compose.base.yml (~строка 43) | 256M | 512M |
| clickhouse | clickhouse/docker-compose.base.yml (~строка 66) | 1G | 2G |

**module.yaml resources sync (канон «синхронизировано с base.yml»):**
- infra-metrics/module.yaml: limits 224M → 352M (сумма сервисов модуля: cadvisor 256M + node-exporter 96M? — проверить фактический состав сервисов модуля и пересчитать по сумме limits)
- logging/module.yaml: limits 384M → 640M (loki 512M + promtail 128M)
- clickhouse/module.yaml: limits 512M → 2G (compose 1G→2G; module.yaml был 512M — рассинхрон, исправить до 2G)

Комментарии в файлах (строки вида `##   - ... 128M memory limit`) — обновить согласованно. Состав сервисов модуля проверить по docker-compose.base.yml каждого модуля (сумма limits всех сервисов = module.yaml resources.limits).

---

## 6. Тесты (R5 на каждый фикс)

### tests/unit/test_monitoring_alert_rules.py (MODIFY)
1. **R5-D1 positive:** `test_provisioning_alert_rules_loki_expr_no_binop` — для всех 3 backup-правил (backup_freshness, backup_upload_failure, wal_sync_failure) expr = чистый `count_over_time(...)` БЕЗ `< 1`/`> 0` внутри (детектор: regex `count_over_time\([^)]*\)[^"]*$` — строка expr заканчивается на `]`), threshold expression C сохраняет evaluator (lt/gt).
2. **R5-D1 negative:** `test_loki_expr_binop_negative_removed` — legacy expr `'... [26h]) < 1'` → детектор бросает AssertionError (запрещённая форма).
3. **R5-D2 positive:** детектор `_assert_high_memory_label_name(rules)` — summary/description обоих файлов содержат `{{ $labels.name }}` и НЕ содержат `{{ $labels.container }}` (для high_memory и per-project HighMemoryUsage).
4. **R5-D2 negative:** `test_high_memory_container_label_negative_removed` — legacy `{{ $labels.container }}` → AssertionError.
5. Существующие тесты (loki datasource, backup rules present, high_memory guard) — НЕ ломаются (expr по-прежнему содержит compose_service="backup-cron", guard на месте).

### tests/unit/test_memory_limits.py (CREATE)
1. Читает 3 docker-compose.base.yml (infra-metrics, logging, clickhouse), парсит YAML, проверяет: cadvisor limits.memory ≥ 256M, loki ≥ 512M, clickhouse ≥ 2G.
2. Синхронизация module.yaml: resources.limits.memory ≥ суммы limits сервисов соответствующего compose (по каждому из 3 модулей) — детектор рассинхрона.
3. R5 negative: если лимит меньше канона (например 128M) → AssertionError (в тесте через фикстуру/конструктор детектора, не модифицируя реальные файлы).

---

## 7. Acceptance Criteria

| AC | Критерий | Проверка |
|----|----------|----------|
| AC1 | backup_freshness expr БЕЗ binop; threshold lt 1; на ноде правило → Normal при count≥1 | unit (детектор + R5 negative); на ноде: Grafana API state=normal |
| AC2 | 0 упоминаний `{{ $labels.container }}` в обоих alert-rules.yml; на ноде алерт показывает имя контейнера | `rg "labels.container" core/modules/monitoring/config/` → 0; Grafana API аннотации |
| AC3 | Лимиты: cadvisor≥256M, loki≥512M, clickhouse≥2G; module.yaml синхронизирован; compose config валиден | unit test_memory_limits; `docker compose config` на ноде; docker stats ≤50% |
| AC4 | `make check` ALL PASS; R5-тесты положительные+отрицательные | `make check` до чистоты |
| AC5 | Telegram-алерты прекращаются (оба типа) | наблюдение 2h после деплоя |

---

## 8. Верификация и деплой

### Верификация (батчами)
1. `make test-summary TEST_FILE=tests/unit/test_monitoring_alert_rules.py` — PASS
2. `make test-summary TEST_FILE=tests/unit/test_memory_limits.py` — PASS
3. `make check` — до ЧИСТОТЫ (финальный арбитр)
4. `rg "labels.container" core/modules/monitoring/config/` → 0
5. `rg "count_over_time" core/modules/monitoring/config/alerting/alert-rules.yml` → 3 expr без `<`/`>`

### Деплой (sysadmin)
1. SCP/rsync core → tronyx-vps (`make core-deliver NODE=tronyx-vps` или rsync эквивалент)
2. Grafana: провижининг правил — рестарт grafana (или hot-reload) → `curl -u admin:... http://localhost:3000/api/prometheus/grafana/api/v1/rules` → backup_freshness state=normal, high_memory state=normal (после W3)
3. Лимиты: `docker compose up -d --force-recreate cadvisor loki clickhouse` (или node-update φ12) — без дата-потери (named volumes)
4. Верификация: `docker stats` — cadvisor ≤50%, loki ≤45%, clickhouse ≤50%; Grafana rules → все normal
5. Наблюдение 2h: Telegram молчит

---

## 9. Риски

| Риск | Митигация |
|------|-----------|
| Grafana не перечитает provisioning без рестарта | Деплой: рестарт grafana (контейнер, 5 сек) |
| Loki binop-семантика изменится в будущих версиях | TRAP[W1] Rev-условие; threshold остаётся единственной точкой сравнения |
| Recreate clickhouse/loki сбросит in-memory кэши | Named volumes сохраняют данные; простоя <30s; healthcheck переживёт |
| module.yaml resources sync — неверная сумма сервисов | Тест test_memory_limits сверяет сумму limits с module.yaml (детектор) |
| Хост 7.9G: сумма лимитов растёт на +768M | Фактическое потребление не меняется (лимиты ≠ резервирование); available 3.8G |

$END_DEVPLAN
