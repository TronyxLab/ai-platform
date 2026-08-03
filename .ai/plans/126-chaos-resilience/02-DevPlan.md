# 126-chaos-resilience — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исполнить программу chaos-испытаний из 01-Brief.md на tronyx-vps: 11 инъекций отказов, верификация самовосстановления, аудит полноты логов каждого инцидента, фиксация находок и Debt-записей.
DESCRIPTION:           5 волн: W1 — harness (pytest e2e) + baseline; W2 — semi-safe прогон (T1-T5); W3 — destructive прогон (T6-T10); W4 — T11 reboot + кросс-бут аудит логов; W5 — анализ, Debt, фикс-волна, повтор упавших. Каждый тест автономен (NodeSSHClient + asserts на log-маркеры), прогон — по одному тесту с аудитом после каждого.
RATIONALE:             Option A (подтверждена заказчиком 2026-08-03): единственный VPS → прямой прогон с окном обслуживания и rollback-планами. Harness в стиле существующего tests/e2e/test_failure_scenarios.py (переиспользует NodeSSHClient, LDD IMP:9, @pytest.mark.requires_node + новый @pytest.mark.chaos) — воспроизводимость и CI-совместимость.
ACCEPTANCE_CRITERIA:   (1) Все 11 тестов выполнены, вердикт+TTR по каждому в VerificationReport. (2) Log Audit Manifest каждого теста подтверждён (0 «инцидентов без следа») ИЛИ провалы задокументированы как Debt с планом фикса. (3) Debt-реестр создан (04-Debt.md), включая D-1 (journald→Loki) и D-2 (Tor=SPOF алерт-канала). (4) Упавшие тесты повторно прогнаны после фикс-волны. (5) `make check` зелёный после всех изменений.
IMPLEMENTS:            01-Brief.md (126-chaos-resilience), требование заказчика 2026-08-03.
IMPACTS:               tronyx-vps (production); tests/e2e/ (новый test_chaos_resilience.py); возможно core/modules/logging/config/promtail-config.yml (W5, journald scrape); core/internal/shared/telegram_notifier.py (W5, маркер провала доставки); Grafana alert rules (W5, недостающие алерты).
REQUIRES:              Решения пользователя (выполнены): Option A + Tor-тест + артефакты. Операционные: окно обслуживания, свежий бэкап, SSH-доступ, доступ к VPS-консоли (для T11), node-configs/tronyx-vps/secrets расшифрованы (NODE env, AGE key).
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <!-- W1: harness -->
  <entity name="tests_e2e_test_chaos_resilience_py" TYPE="MODULE"
    keywords="chaos,resilience,chaos-injection,self-recovery,log-audit"
    annotation="11 тестов: инъекция отказов на tronyx-vps + assert самовосстановления + Log Audit Manifest. Переиспользует NodeSSHClient из tests/_conftest/node.py. Маркеры: requires_node + chaos."
    CrossLinks="tests/_conftest/node.py; core/internal/deploy/healthcheck_poller.py; core/internal/shared/telegram_notifier.py; tests/e2e/test_failure_scenarios.py"/>
  <entity name="tests_e2e_chaos_audit_py" TYPE="MODULE"
    keywords="log-audit-manifest,marker,source,docker-logs,journalctl,loki"
    annotation="Утилиты Log Audit Manifest: проверка маркеров по источникам (docker logs/journalctl/Loki API через SSH), экспорт логов-артефактов в .ai/plans/126-chaos-resilience/files/."
    CrossLinks="tests/e2e/test_chaos_resilience.py"/>
  <!-- W5: фиксы (опционально, по находкам) -->
  <entity name="core_modules_logging_config_promtail_config_yml" TYPE="CONFIG"
    keywords="journald-scrape,journal,promtail"
    annotation="D-1: добавить journald-скрейп хоста (docker daemon, systemd, OOM-killer) в Loki. Только после подтверждения находки на прогоне."
    CrossLinks="core/modules/logging/config/promtail-config.yml"/>
  <entity name="core_internal_shared_telegram_notifier_py" TYPE="MODULE"
    keywords="delivery-failure-marker,proxy-down,IMP:9"
    annotation="D-2: при недоступности proxy (tor down) — явный [IMP:9] лог failure delivery, чтобы инцидент слепого канала был реконструируем."
    CrossLinks="core/internal/shared/telegram_notifier.py; core/modules/monitoring/config/alerting/contact-points.yml.telegram"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── make check (baseline green) ──► бэкап (postgres+app-data→S3) + валидация restore ──►
     snapshot алертов/status-page ──► test_chaos_resilience.py (harness) ──► export-утилиты логов
W2 ── T1 docker restart ─► T2 DNS ─► T3 network partition ─► T4 clock skew ─► T5 tor
     │ после каждого: Log Audit Manifest (docker logs + journalctl + Loki + alert history) + export
W3 ── T6 postgres SIGKILL ─► T7 OOM ─► T8 disk 90-93% ─► T9 cert+secrets ─► T10 restore-drill
     │ после каждого: rollback-чек-лист + аудит логов + export
W4 ── T11 reboot ─► journalctl -b (новый boot) ─► TTR ─► кросс-бут аудит: все инциденты W2/W3
     │ реконструируются из логов (journald/docker logs/Loki персистентны) ─► export
W5 ── сводный отчёт (вердикт+TTR) ─► 04-Debt.md (D-1 journald→Loki, D-2 Tor SPOF, …) ─►
     фикс-волна (promtail journald scrape, alert rules, log-маркеры) ─► повтор упавших ─►
     03-VerificationReport.md
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/plans/126-chaos-resilience/01-Brief.md` | создан | W0 |
| `.ai/plans/126-chaos-resilience/02-DevPlan.md` | создан | W0 |
| `tests/e2e/test_chaos_resilience.py` | создать | W1 |
| `tests/e2e/chaos_audit.py` | создать | W1 |
| `.ai/plans/126-chaos-resilience/files/*.log` | экспорт логов после каждого теста | W2-W4 |
| `.ai/plans/126-chaos-resilience/04-Debt.md` | создать | W5 |
| `.ai/plans/126-chaos-resilience/03-VerificationReport.md` | создать | W5 |
| `core/modules/logging/config/promtail-config.yml` | модифицировать (D-1, journald scrape) | W5 (после подтверждения) |
| `core/internal/shared/telegram_notifier.py` | модифицировать (D-2, IMP:9 failure-маркер) | W5 (после подтверждения) |
| `core/modules/monitoring/config/alerting/*` | модифицировать (недостающие alert rules) | W5 (по находкам) |

## 3. Волны

### W0 — Зафиксировано (артефакты + решения)
Окно обслуживания согласовано; Option A; Tor-тест добавлен как T5; 11 тестов.

### W1 — Harness + Baseline
1. `make check` + `make gate MODE=fast` — зелёные (baseline).
2. Полный бэкап: postgres (все БД, включая realty_db, postgres_init_databases) + app-data → S3; **валидация**: restore-артефакт скачан и проверен (pg_restore --list / checksum).
3. Снапшот: alert rules (Grafana API), contact points, status-page, `docker ps` (полный список контейнеров — эталон для T1/T11).
4. `tests/e2e/chaos_audit.py`: класс `LogAuditManifest` — список `(source, regex, window_min)`; методы `assert_markers(ssh)` по docker logs/journalctl/Loki API (`loki:3100` через docker exec или порт); `export(since)` → files/.
5. `tests/e2e/test_chaos_resilience.py`: маркеры `@pytest.mark.chaos`, `@pytest.mark.requires_node`; каждый тест = инъекция (ssh_exec) + ожидание восстановления (poll healthcheck) + LogAuditManifest.assert_markers. Таймаут на восстановление настраивается per-test. `pytest -m chaos` — отдельный прогон (не входит в обычный gate).
6. Проверка предусловий на хосте: наличие `stress-ng` (иначе установить или заменить python-аллокатором), `iptables-apply` (Debian), свободное место для dd, `systemd-resolved` активен.

**Acceptance W1:** baseline зелёный; бэкап валиден; harness запускается в dry-run (0 инъекций, только сборка manifest'ов); эталон docker ps зафиксирован.

### W2 — Semi-safe прогон (T1-T5)
Порядок исполнения по одному, аудит после каждого:

**T1 — Рестарт Docker daemon**
- Инъекция: `sudo systemctl restart docker`
- Ожидание: все контейнеры (эталон из W1) running ≤3 мин, порядок depends_on соблюдён, healthcheck green, сайты 200
- Log Audit Manifest: `docker events` (start-события), healthcheck poller unhealthy→healthy, nginx error.log без «no upstream»
- Pass: 0 ручных действий; TTR ≤3 мин; маркеры найдены в docker logs **и** в Loki

**T2 — Отказ DNS хоста**
- Инъекция: `sudo systemctl stop systemd-resolved` (60-120 c) → `start`
- Ожидание: сайты живы (docker embedded DNS 127.0.0.11); хостовые процессы (acme renew, apt) — ясные «Name resolution» fail-логи
- Log Audit Manifest: fail-логи acme/cron с первопричиной; 0 silent skip
- Pass: внутренний стек жив; каждый внешний сбой имеет лог-след

**T3 — Сетевая партиция наружу 120 c**
- Инъекция: `iptables-apply` (автооткат 120 c): OUTPUT DROP кроме established/loopback/локальной подсети; INPUT не трогаем
- Ожидание: сайты живы; исходящие (litellm→API, backup→S3, acme) — retry/ошибки; alert fire/resolve если есть outbound-правило; восстановление автоматическое
- Log Audit Manifest: litellm retry-логи, backup «network unreachable», alert history fire/resolve тайминги
- Pass: 0 silent-отказов; окно партиции читается из логов

**T4 — Clock skew ±24 h**
- Инъекция: `timedatectl set-ntp false` → `date -s "+24 hours"` → аудит → `-24 hours` → аудит → `set-ntp true`
- Ожидание: внешние TLS-вызовы (litellm cert verification) — ясные fail-причины; Loki retention под наблюдением (границы/размер до/после); после возврата — консистентность
- Log Audit Manifest: журнал в реальном времени (journalctl с фильтром по фактическому времени), retention-логи, recovery после NTP sync
- Pass: ничего не сломано «навсегда»; потери Loki задокументированы (или отсутствуют); TTR после возврата времени

**T5 — Отказ Tor (Telegram-канал)**
- Инъекция: `sudo systemctl stop tor` (+ privoxy, если отдельный unit) 3-5 мин → `start`
- Ожидание: внутренний стек жив; `tor_proxy_check` (healthcheck) → unhealthy transition; status-page degraded; telegram_notifier при попытке отправки логирует провал (не silent — design «всегда exit 0» ОБЯЗАН логировать); после start — recovery
- Log Audit Manifest: tor_proxy_check маркеры, telegram_notifier failure-логи, healthcheck transition, (если есть) alert TorDown
- Pass: 0 silent-провалов доставки; инцидент реконструируется по логам; recovery автоматический
- **Находка-ожидание:** Tor = SPOF алерт-канала → Debt D-2

**Acceptance W2:** 5 вердиктов + TTR; export логов в files/; все Manifest подтверждены или Debt-записаны.

### W3 — Destructive прогон (T6-T10)
Перед волной: свежий бэкап повторно (после T5). Rollback-чек-лист под рукой.

**T6 — SIGKILL Postgres под нагрузкой**
- Препарация: БД `chaos_drill` + таблица `t(id serial, payload text, ts timestamptz default now())`; INSERT-loop (commit каждые 50 строк, счётчик в файле/БД-таблице)
- Инъекция: `docker kill -s KILL <postgres-container>` посреди нагрузки
- Ожидание: restart: always → WAL recovery → 0 потерянных committed-строк; healthcheck unhealthy→healthy; Grafana alert PostgresDown fire→resolve (Telegram — канал жив, T5 позади)
- Log Audit Manifest: postgres «database system was interrupted» → «database system is ready», alert fire/resolve тайминги, healthcheck transition
- Pass: row-count совпадает; recovery ≤2 мин; полный WAL-след; alert-цикл в Telegram-истории

**T7 — OOM-kill модуля (clickhouse)**
- Инъекция: stress-ng (или python-аллокатор) до OOM-kill контейнера clickhouse
- Ожидание: OOMKilled → restart policy → up ≤1 мин; соседи живы; alert fire/resolve
- Log Audit Manifest: journalctl -k OOM-report (жертва, stats), docker inspect State.OOMKilled=true, docker logs «Killed»
- Pass: ядро назвало жертву в логах; восстановление без вмешательства

**T8 — Диск 90–93%**
- Инъекция: `df -h` → dd в /tmp до 92% (резерв ≥5% для docker/journald; НЕ 100%)
- Ожидание: ENOSPC-ошибки с ясной причиной (postgres WAL, backup-cron), alert DiskSpaceLow fire; Prometheus/Grafana живы; после rm — полное восстановление + resolve
- Log Audit Manifest: ENOSPC с указанием файла/устройства, alert fire/resolve, отсутствие молчаливых падений
- Pass: 0 silent failure; первопричина «диск» читается из логов; recovery полный

**T9 — Повреждение TLS cert + secrets**
- Препарация: `cp`-бэкап сертификатов nginx и `node-configs/tronyx-vps/secrets/tronyx-vps.enc.yaml` на хост
- Инъекция: подмена байтов (не удаление) в live-копии cert и enc-файла
- Ожидание: nginx продолжает serve (кешированный cert) — 0 простоя сайтов; status-page degraded; `make secrets-unlock` fail с ясной ошибкой; восстановление из бэкапа
- Log Audit Manifest: nginx error.log (ошибки только при reload/renew), unlock fail-логи, status-page degraded-маркер
- Pass: сайт отвечает весь тест; каждая затронутая операция залогирована; recovery без последствий

**T10 — Restore-drill: DROP БД → restore из S3**
- Препарация: `chaos_drill` наполнена (10k строк + checksum-таблица); backup-cron дамп → S3 (ключ зафиксирован)
- Инъекция: `DROP DATABASE chaos_drill` → restore из S3 (штатный restore-скрипт)
- Ожидание: restore успешен; row-count + checksum совпадают; audit-trail в логах бэкапа/restore
- Log Audit Manifest: backup-лог (S3-ключ, sha), restore-лог с верификацией
- Pass: 100% данных; полный аудит-след операции

**Acceptance W3:** 5 вердиктов + TTR; export логов; Manifest подтверждены или Debt.

### W4 — T11 reboot + кросс-бут аудит
- Инъекция: `sudo reboot` (оператор в VPS-консоли)
- Ожидание: systemd → docker → compose-стек → healthcheck green ≤5 мин → сайты 200; TTR зафиксирован
- Log Audit Manifest: journalctl -b (новый boot id), docker events, healthcheck transitions, nginx старт
- **Кросс-бут аудит (ключевое требование «все логи записались»):** после reboot из логов реконструировать инциденты W2/W3: journald (docker daemon/systemd) и docker logs персистентны; Loki жив. Каждый инцидент T1-T10 обязан иметь след без участия очевидца.
- Pass: 0 ручных действий; TTR ≤5 мин; кросс-бут реконструкция 100%

**Acceptance W4:** T11 вердикт + TTR; кросс-бут аудит подтверждён или Debt-записан.

### W5 — Анализ, Debt, фикс-волна
1. Сводный отчёт: таблица вердиктов (SUCCESS/PARTIAL/FAIL), TTR, качество логов (маркер-покрытие %).
2. `04-Debt.md` — обязательные записи:
   - **D-1**: journald не уходит в Loki (подтверждено предварительным аудитом) → promtail journal-скрейп
   - **D-2**: Tor = SPOF Telegram-канала → fallback-канал ИЛИ минимум IMP:9 failure-маркер в telegram_notifier (подтверждается T5)
   - D-3…: по находкам (недостающие alert rules, restart policy, log-маркеры)
3. Фикс-волна (только подтверждённые находки; малый объём — в рамках 126, иначе отдельный DevPlan): promtail journald scrape + reload, alert rules, telegram_notifier маркер.
4. Повторный прогон упавших тестов.
5. `03-VerificationReport.md` (QA): вердикт по критериям приёмки Brief.

**Acceptance W5:** Debt-реестр создан; упавшие тесты повторены; VerificationReport выпущен; `make check` зелёный.

## 4. Log Audit Methodology (обязательная для каждого теста)

**Источники и живучесть:**
| Источник | Что содержит | Живёт после reboot | Где смотреть |
|----------|--------------|--------------------|--------------|
| docker logs | stdout/stderr контейнеров | да | `docker logs <c>` / Loki (promtail docker_sd) |
| journalctl | docker daemon, systemd, kernel (OOM), cron | да (на диске) | `journalctl --since` (НЕ в Loki — D-1) |
| Loki | контейнерные логи, retention 7d | да | Grafana Explore / API |
| Grafana alert history | fire/resolve, delivery | да | Alerting → History |
| status-page | degraded-маркеры | да | HTTP-эндпоинт |

**Manifest-формат:**
```
маркер = {source: docker|journald|loki|alerts|status, regex, window_min, expected: required|optional}
```
Проверка: (1) маркер найден в первоисточнике; (2) для контейнерных маркеров — продублирован в Loki (проверка конвейера); (3) время появления внутри окна.

**Критерий «инцидент без следа» (fail даже при успешном восстановлении):**
- Обязательный маркер отсутствует во всех источниках → fail + Debt
- Маркер есть, но первопричина не читается (нельзя ответить «что сломалось и почему») → PARTIAL + Debt
- Маркер есть в первоисточнике, но отсутствует в Loki → PARTIAL (дыра конвейера) + Debt

**Экспорт:** после каждого теста — `journalctl --since <start>` + docker logs затронутых контейнеров + Loki-выборка → `.ai/plans/126-chaos-resilience/files/T<N>/*.log`.

## 5. Риски и митигации (операционные)

| Риск | Митигация |
|------|-----------|
| T3/T5/T11 — потеря доступа | iptables-apply (автооткат); established/loopback allowed; оператор в VPS-консоли для T11; SSH-ключ не трогаем |
| T8 — переполнение диска до 100% | цель 92%, контроль dd-цикла (проверка df каждые 512 MB); файл в /tmp (не на docker volume) |
| T4 — Loki retention удалит данные | снапшот до/после; первичный аудит по journalctl реального времени; лог-экспорт после каждого теста до следующего |
| T6 — потеря данных | только committed-строки; бэкап перед W3; chaos_drill изолирована от realty_db |
| Вмешательство hermes-agent в прогон | hermes-agent (мониторинг/самообновление) может «лечить» инъекции — фиксировать его действия в логах как часть аудита |
| Длительность W3 (T6-T10) | окно ≥1 дня; тесты последовательные с паузами на аудит |

## 6. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | baseline green, бэкап валиден, harness dry-run 0 инъекций, эталон docker ps |
| W2 | 5 вердиктов+TTR, export логов, Manifest подтверждены/в Debt |
| W3 | 5 вердиктов+TTR, export логов, Manifest подтверждены/в Debt |
| W4 | T11 вердикт+TTR, кросс-бут реконструкция 100% |
| W5 | Debt-реестр (D-1, D-2 обязательны), повтор упавших, VerificationReport, check green |

$END_DEVPLAN
