# 126-chaos-resilience — 04-Debt.md

$START_DEBT

$ARTIFACT_CONTRACT
PURPOSE:               Реестр долгов программы chaos-испытаний 126 (T1-T8) — актуальные статусы: D-1/D-2 закрыты DevPlan 132 (W3/W4), находки T4-T8 зафиксированы с Rev-условиями.
DESCRIPTION:           По результатам W5 (анализ прогонов T1-T8): обязательные D-1 (journald→Loki) и D-2 (Tor=SPOF Telegram) переведены в CLOSED-by-132 (реализовано в 132-fault-tolerance W3/W4). Находки прогонов, оставшиеся живыми долгами (D-3..D-8), задокументированы с фактическим Status, Observed/Suspected/Impact и Rev-условием. PARTIAL-причины T2/T3/T5 (optional-маркерные промахи) отдельно пояснены — отдельные долги не создаются (первопричина реконструируется из host-логов).
RATIONALE:             Протокол artifacts.md: Debt-реестр фиксирует только АКТУАЛЬНЫЕ статусы (без исторических сведений); «инцидент без следа = провал теста» (требование заказчика) транслируется в долги с планом фикса.
ACCEPTANCE_CRITERIA:   (1) D-1/D-2 имеют статус CLOSED-by-132 с указанием закрывающей волны; (2) каждый живой долг имеет Observed (доказательство из логов/verdict.json), Impact и Rev-условие; (3) нет выдуманных долгов — каждый подтверждён артефактами прогонов.
IMPLEMENTS:            DevPlan 126 W5 (04-Debt.md); Brief 126 AC(4)/(5); findings T1-T8 из files/T<N>/verdict.json + экспортированных логов.
IMPACTS:               Будущие DevPlan-волны (фиксы D-3..D-8); 03-VerificationReport.md (126 W5).
REQUIRES:              files/results.json + files/T<N>/verdict.json + экспортированные логи T1-T8; DevPlan 132 (статусы D-1/D-2).
$END_ARTIFACT_CONTRACT

---

## Реестр долгов — актуальные статусы (2026-08-04)

| ID | Суть | Status | Закрыто чем / Rev-условие |
|----|------|--------|---------------------------|
| D-1 | journald не попадает в Loki (docker daemon, systemd, OOM, cron — только journalctl) | **CLOSED-by-132 W3** | promtail `job_name: journal` (скрейп `/var/log/journal`, host-лейбл, max_age 24h) + `Storage=persistent` в journald.conf (кросс-бут реконструкция) |
| D-2 | Tor = SPOF Telegram-канала: при отказе tor/privoxy провал доставки не реконструируем (silent) | **CLOSED-by-132 W4** | `[IMP:9] DELIVERY FAILED: <reason> (proxy=set/none)` во всех failure-путях `send_telegram` + `notify()`; фикс лживого «Notification sent» |
| D-3 | Grafana alerting→Telegram цепочка не активна: `contact-points.yml` = пустой safe-default (0 receivers, 0 policies); алерты fire (rules state) но НЕ доставляются | **OPEN** | Активировать `contact-points.yml.telegram` (rename + TELEGRAM_* env) и проверить доставку; **Rev**: при активации — перепроверить fire/resolve цикл на реальном алерте |
| D-4 | T6: Grafana alert на падение postgres НЕ сработал (11s даунтайм; Service Down rule `for: 1m` > TTR; alerts_total=0) | **OPEN** | Решение: снизить `for:` Service Down (или добавить отдельное правило коротких падений) — **Rev**: если анти-флаппинг важнее покрытия sub-minute падений — принять как дизайн и закрыть |
| D-5 | T7: OOM-инъекция НЕ попала в clickhouse — жертвой стал процесс-аллокатор `bash` (kernel: «Killed process … (bash)», 22:15:09/22:17:41); clickhouse не падал → restart-политика clickhouse под OOM НЕ проверена (TTR=1s, clickhouse-startup не найден) | **OPEN** (verification gap) | Повторный целевой OOM-прогон (лимит memory cgroup на clickhouse, аллокатор в контейнере clickhouse); **Rev**: при подтверждённом OOMKilled→restart — закрыть |
| D-6 | T8: Grafana DiskSpaceLow rule неэффективна — expr `node_filesystem_avail_bytes/node_filesystem_size_bytes < 0.2` БЕЗ mountpoint-фильтра: reducer last берёт произвольную серию (tmpfs/overlay с ratio>0.2) → state inactive при 90% (эксперимент ratio=0.107) | **OPEN** | Фикс expr: добавить `{mountpoint="/"}` (и, при необходимости, второй rule на tmpfs/overlay) + проверка fire на 90% fill; **Rev**: после фикса — повторный T8-прогон (или ручной df-тест) |
| D-7 | T8: ENOSPC-след НЕ реконструируем из персистентных источников: journald 0 совпадений, backup-cron.log пуст (docker exec-вывод `backup-postgres.sh` не логируется в docker logs) — «инцидент без следа» для ENOSPC | **OPEN** | Маркер в `backup-postgres.sh`: явная запись результата в auditfile/файл-лог (как в T3 chaos-t3.log) ИЛИ логирование docker exec-вывода; **Rev**: при наличии ENOSPC-следа в персистентных источниках — закрыть |
| D-8 | Loki resilience: T4 — clock skew ±24h → 1943 rejected «entry too far behind» (массовая потеря логов, ingester отбрасывает realtime-записи); T8 — ingester «shutting down» весь окно (promtail 500s 22:40-23:01Z, Loki недоступен) | **OPEN** | Исследование/фикс: toleration clock jump (max_look_back_period/accept out-of-order), WAL-поведение ingester, healthcheck-критерий; **Rev**: после фикса — повторный T4/T8-прогон (skew + длительное окно) |

---

## PARTIAL-причины T2/T3/T5 — пояснение (долги НЕ создаются)

Промахи в этих тестах — optional-маркеры, первопричина инцидента при этом реконструируется из host-логов; отдельных долгов не требуется:

| Тест | Verdict | PARTIAL-причина (из verdict.json) | Почему не долг |
|------|---------|-----------------------------------|----------------|
| T2 | PARTIAL (ttr=77s) | `docker:litellm-resolv-fail` (optional) не найден — litellm не логировал собственные resolv-ошибки | Причина инцидента РЕКОНСТРУИРУЕТСЯ: apt «Temporary failure resolving» (count=16) + dockerd «[resolver] failed to query external DNS server» в journald; сайты живы (docker DNS 127.0.0.11) |
| T3 | PARTIAL (ttr=188s) | `journald:tor-proxy-healthcheck-ran` + `audit:tor-healthcheck` (optional) не найдены — tor-proxy healthcheck cron не попал в 120s окно | Причина инцидента реконструируется: `audit:backup-outbound-fail` найден (count=1) + dockerd s3.timeweb.cloud «i/o timeout»; несовпадение интервала cron с окном — не дефект |
| T5 | PARTIAL (ttr=317s) | `journald:cron-tor-check-ran` (optional) не найден — маркер cron-запуска healthcheck отсутствовал в journald | Инцидент реконструируется: tor/privoxy stop/start в journald, hermes-agent «[Telegram] connection failed / reconnect» (провал канала), `audit:tor-healthcheck-entries` найден; основной finding T5 = D-2 → CLOSED-by-132 |

---

## Сводка статусов

- **CLOSED:** D-1 (132 W3), D-2 (132 W4).
- **OPEN:** D-3 (alerting delivery chain), D-4 (alert coverage sub-minute), D-5 (OOM verification gap), D-6 (DiskSpaceLow expr), D-7 (ENOSPC trace gap), D-8 (Loki resilience).
- PARTIAL-причины T2/T3/T5 — observations, не долги (см. таблицу выше).

$END_DEBT
