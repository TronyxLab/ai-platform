# 126-chaos-resilience — 03-VerificationReport.md

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Итоговый отчёт W5 программы chaos-испытаний 126: вердикты T1-T8 (verdict + TTR + маркер-покрытие), восстановленный вердикт T8, сводка W5, вердикты по 5 критериям приёмки Brief, статус T9-T11.
DESCRIPTION:           Анализ прогонов T1-T8 на tronyx-vps по files/results.json + files/T<N>/verdict.json + экспортированным логам (journal.log, loki.json, alerts.json, nginx.log, promtail.log, backup-cron.log и др.). T8: verdict.json отсутствовал — восстановлен по логам (реконструкция задокументирована). T9-T11 не выполнялись (операционное окно закрыто, сервер пересоздан) — зафиксировано как ОК-ограничение программы.
RATIONALE:             W5 DevPlan 126: сводный отчёт (вердикт+TTR+маркер-покрытие), Debt-реестр (04-Debt.md), VerificationReport. Критерий «инцидент без следа = провал теста» (Brief AC3) применён к T8.
ACCEPTANCE_CRITERIA:   (1) Таблица T1-T8 с verdict/ttr/маркер-покрытием; (2) восстановленный T8-вердикт с доказательствами из логов; (3) вердикты по 5 критериям приёмки Brief; (4) статус T9-T11 зафиксирован.
IMPLEMENTS:            DevPlan 126 W5 (03-VerificationReport.md); Brief 126 AC(1)-(5).
IMPACTS:               Будущие волны фиксов (D-3..D-8 из 04-Debt.md); решения по повторному прогону chaos-программы (T9-T11).
REQUIRES:              files/results.json, files/T1-T8/verdict.json (T8 — восстановлен), экспортированные логи; 04-Debt.md (126 W5).
$END_ARTIFACT_CONTRACT

🔒 Анализ выполнен на SHA `c87d24c5ac3740bf42719c875aa21ce7b6f4aed3` (чистый working tree до W5-артефактов).

---

## Section 1 — Таблица вердиктов T1-T8

| Тест | Инъекция | Verdict | TTR (s) | Маркер-покрытие (required) | PARTIAL/FAIL причина (из verdict.json / логов) |
|------|----------|---------|:-------:|:--------------------------:|------------------------------------------------|
| T1 | Рестарт Docker daemon | **SUCCESS** | 62 | 14/14 (100%) | — |
| T2 | Отказ DNS хоста (systemd-resolved stop) | **PARTIAL** | 77 | 14/14 (100%) | optional `docker:litellm-resolv-fail` не найден (litellm не логировал resolv-ошибки); первопричина реконструируется из host-логов (apt count=16, dockerd resolver) |
| T3 | Сетевая партиция наружу 120s | **PARTIAL** | 188 | 11/11 (100%) | optional `journald:tor-proxy-healthcheck-ran` + `audit:tor-healthcheck` не найдены (cron tor-healthcheck не попал в 120s окно); backup-outbound-fail найден |
| T4 | Clock skew ±24h | **PARTIAL** | 110 | 13/13 (100%) | optional `loki:nginx-logs-after-skew` не найден (streams=0): Loki отбросил realtime-записи после скачка времени — 1943 «entry too far behind» в loki.log (D-8) |
| T5 | Отказ Tor (Telegram-канал) | **PARTIAL** | 317 | 15/15 (100%) | optional `journald:cron-tor-check-ran` не найден; инцидент реконструируется (tor/privoxy stop/start, hermes telegram connection failed); основной finding = D-2 → CLOSED-by-132 |
| T6 | SIGKILL Postgres под нагрузкой | **PARTIAL** | 11 | 4/4 (100%) | optional `alerts:postgres-down` не найден (alerts_total=0): Grafana alert на 11s падение не сработал (Service Down `for: 1m` > TTR) (D-4); WAL-восстановление < 1s (postgres.log: interrupted → ready) |
| T7 | OOM-kill модуля (clickhouse) | **PARTIAL** | 1 | 9/9 (100%) | optional `docker:clickhouse-startup` не найден: OOM-жертва — `bash`-аллокатор (kernel: «Killed process … (bash)» 22:15:09/22:17:41), НЕ clickhouse → restart-политика под OOM не проверена (D-5) |
| T8 | Диск 90–93% (dd + spool pre-fill) | **FAIL** (восстановлен) | 15* | 9/12 (75%) | required `journald:enspc-evidence` + `docker:backup-enspc` отсутствуют во ВСЕХ персистентных источниках (journald 0; backup-cron.log пуст); required `state:loki` — ingester «shutting down» весь окна; optional `alerts:diskspace-fired` не найден (D-6); recovery сайтов подтверждён (200/0 5xx) |

\* T8 ttr_s — реконструированная оценка (см. Section 2).

**Сводка T1-T8:** 1 SUCCESS (T1), 6 PARTIAL (T2-T7), 1 FAIL (T8, восстановлен). Восстановление платформы — автоматическое во всех 8 тестах (0 ручных действий в логах). Маркер-покрытие required: 100% у T1-T7; T8 — 75% (ENOSPC-след + Loki).

---

## Section 2 — Восстановленный вердикт T8 (verdict.json отсутствовал)

**Факт:** `files/T8/verdict.json` отсутствует — тест не дошёл до `record_verdict` (crash на pre-record assert, наиболее вероятно `assert backup_enspc` в test_t08_disk_pressure_92:1016). Экспортированные артефакты на месте (export_logs выполнен ~23:03:09Z).

**Реконструкция** (документирована в `files/T8/verdict.json` → поле `reconstruction_note`):

| Маркер | Источник | Expected | Reconstructed | Доказательство из экспортированных логов |
|--------|----------|----------|:-------------:|-------------------------------------------|
| journald:enspc-evidence | journald | required | **NOT FOUND** | `T8/journal.log` (окно 22:58:34-23:03:09Z): 0 совпадений «No space left|ENOSPC»; только sshd/systemd/CRON/UFW |
| docker:backup-enspc | docker (backup-cron) | required | **NOT FOUND** | `T8/backup-cron.log` = 0 байт — backup-cron не пишет в stdout; docker exec-вывод `backup-postgres.sh` не логируется |
| alerts:diskspace-fired | alerts | optional | NOT FOUND | `T8/alerts.json` = `[]`; DiskSpaceLow rule expr без mountpoint-фильтра → inactive при 90% (D-6) |
| state:loki | state | required | **NOT FOUND** | promtail 500s «Ingester is shutting down» 22:40-23:01Z — Loki ingester недоступен всё окно (D-8) |
| state:postgres/nginx/redis/prometheus/clickhouse | state | required | found | nginx 200/0 5xx в реальном окне; journal без ошибок; Prometheus API отвечал (тест дошёл до export) |
| sites: 4 сайта | http | required | found | nginx.log: 200/401 в 23:03:03-07Z (после recovery) |

**Вердикт T8: FAIL** — критерий заказчика «инцидент без следа = провал теста» (Brief AC3): ENOSPC-маркеры отсутствуют во всех персистентных источниках; восстановление сайтов подтверждено, но след инцидента не реконструируем. Долги: D-6 (DiskSpaceLow rule), D-7 (ENOSPC trace gap), D-8 (Loki resilience). ratio-critical data-path (Prometheus `avail/size < 0.2` на `/`) подтверждён — тест прошёл ratio-поллинг и достиг export-стадии в рамках ~155s.

---

## Section 3 — Сводка W5

1. **D-1 (journald→Loki) — CLOSED-by-132 W3**: promtail `job_name: journal` (скрейп `/var/log/journal`, host-лейбл, max_age 24h) + journald `Storage=persistent` (кросс-бут реконструкция). Подтверждено в `core/modules/logging/config/promtail-config.yml:133-148`.
2. **D-2 (Tor SPOF → failure-маркеры) — CLOSED-by-132 W4**: `[IMP:9] DELIVERY FAILED: <reason> (proxy=set/none)` во всех failure-путях `send_telegram` + `notify()` (фикс лживого «Notification sent»). Подтверждено в `core/internal/shared/telegram_notifier.py:92,146,153,337`.
3. **Новые живые долги (открыты, фиксы — будущими волнами):** D-3 (Telegram alerting цепочка не активна — contact-points.yml пустой safe-default), D-4 (alert-покрытие sub-minute падений — T6), D-5 (OOM-верификация clickhouse не выполнена — T7), D-6 (DiskSpaceLow expr без mountpoint — T8), D-7 (ENOSPC-след не персистируется — T8), D-8 (Loki resilience — T4/T8). Полный реестр с Rev-условиями — `04-Debt.md`.
4. **Повторный прогон упавших тестов:** невозможен — операционное окно закрыто, tronyx-vps пересоздан; повторная chaos-программа — отдельный DevPlan (зафиксировано).
5. **`make check`:** зелёный (см. Section 6).

---

## Section 4 — Критерии приёмки Brief (5 пунктов)

| # | Критерий приёмки (Brief) | Вердикт | Обоснование |
|---|--------------------------|---------|-------------|
| AC1 | Все 11 тестов выполнены; для каждого вердикт + TTR | **PARTIAL** | T1-T8 выполнены, вердикты+TTR зафиксированы (T8 — восстановлен по логам). T9-T11 НЕ выполнялись — операционное окно закрыто, сервер пересоздан (зафиксировано как ОК, Section 5) |
| AC2 | Платформа восстановилась сама (0 ручных действий), где ожидалось | **MET** | Все T1-T8 восстановились автоматически: T1 daemon restart (контейнеры не пересозданы), T2 DNS (внутренний стек жив), T3 partition (автооткат), T4 clock (NTP recovery), T5 tor (сервисы перезапущены), T6 postgres (restart:unless-stopped + WAL, 11s), T7 OOM (стек жив), T8 disk (сайты 200 при 92-99% и после rm). Ручных вмешательств в логах не зафиксировано |
| AC3 | Log Audit Manifest подтверждён на 100% (инцидент реконструируется по логам) | **NOT MET** | T1 — 100%. T2-T7 — required 100% (PARTIAL по optional/Loki-промахам). T8 — FAIL: ENOSPC-след отсутствует во всех персистентных источниках («инцидент без следа») + Loki недоступен + алерт не сработал → D-6/D-7/D-8 |
| AC4 | Выявленные дыры зафиксированы как Debt-записи | **MET** | `04-Debt.md` создан: D-1/D-2 (CLOSED-by-132) + D-3..D-8 (OPEN) с Observed/Impact/Rev |
| AC5 | Дыры закрыты фикс-волной с повторным прогоном упавших тестов | **PARTIAL** | D-1/D-2 закрыты DevPlan 132 (W3/W4). D-3..D-8 остаются открытыми (зафиксированы для будущих волн). Повторный прогон упавших (T8) невозможен — окно закрыто, сервер пересоздан |

**Общий вердикт программы (126 W5): PARTIAL** — восстановление подтверждено во всех выполненных тестах, D-1/D-2 закрыты, Debt-реестр и отчёт выпущены; NOT MET по AC3 (T8 «инцидент без следа») и частично по AC1/AC5 (T9-T11 не выполнялись, повтор невозможен).

---

## Section 5 — Статус T9-T11 (не выполнялись — ОК, зафиксировано)

| Тест | Статус | Причина |
|------|--------|---------|
| T9 — Повреждение TLS cert + secrets | **NOT EXECUTED** | Операционное окно закрыто после T8; tronyx-vps пересоздан (2026-08-04). Повторная программа — отдельный DevPlan |
| T10 — Restore-drill: DROP БД → restore из S3 | **NOT EXECUTED** | То же |
| T11 — Полный reboot VPS + кросс-бут аудит | **NOT EXECUTED** | То же; кросс-бут реконструкция инцидентов T1-T8 (W4) не проводилась |

Зафиксировано как ОК-ограничение: требования AC1/AC5 по полной программе из 11 тестов остаются открытыми до повторного прогона в новом окне обслуживания. Косвенное покрытие T10/T11-механик обеспечено 132 (WAL→S3 wal_sync, Storage=persistent journald) и существующими e2e (test_failure_scenarios), но прямое chaos-подтверждение — будущим DevPlan.

---

## Section 6 — Верификация репозитория

| Check | Результат |
|-------|-----------|
| `make check` | Зелёный (exit 0) — после коммита W5-артефактов |
| Working tree | Только W5-артефакты (docs): `04-Debt.md`, `03-VerificationReport.md`, `files/T8/verdict.json`, `files/results.json` (T8 добавлен); код 132/127-131 не тронут |
| Секреты | Не экспонированы (артефакты — вердикты/логи без credentials; графana auth — только ссылки на env) |

$END_VERIFICATION_REPORT
