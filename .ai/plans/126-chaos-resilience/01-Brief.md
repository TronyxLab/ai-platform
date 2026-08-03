# 126-chaos-resilience — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Проверить, что платформа ai-platform на tronyx-vps выстаивает при инъекциях отказов (chaos engineering) и/или восстанавливается сама без ручного вмешательства; гарантировать, что каждый инцидент оставляет полный, реконструируемый след в логах для последующего анализа и превентивных мер.
DESCRIPTION:           Программа из 11 нестандартных тестов (5 semi-safe, 5 destructive, 1 power-cycle) на единственном production VPS tronyx-vps (103.88.243.151). Стратегия Option A: прямой прогон с окном обслуживания, свежим бэкапом и rollback-планом на каждый тест. Каждый тест = инъекция + критерий самовосстановления + Log Audit Manifest (обязательные маркеры в docker logs / journalctl / Loki / Grafana alert history). Отдельный тест Tor: весь Telegram-канал (уведомления деплоя, алерты Grafana) ходит через tor/privoxy proxy — проверяем SPOF и качество логов при его отказе.
RATIONALE:             Существующие e2e-тесты (test_failure_scenarios.py T15/T16) покрывают только SSH-timeout и forced-command receive. Self-healing механизмы (restart: always/unless-stopped, WAL recovery, healthcheck poller, Grafana alerting→Telegram, backup-cron→S3) никогда не проверялись инъекциями отказов в проде. Единственный удалённый VPS делает staging-репетицию невозможной (Option B отклонена: ложная уверенность + 1-2 дня на staging). Ключевое требование заказчика: «инцидент без следа в логах = провал теста» — платформа должна не только восстановиться, но и оставить логи, по которым инцидент реконструируется целиком.
ACCEPTANCE_CRITERIA:   (1) Все 11 тестов выполнены на tronyx-vps; для каждого зафиксирован вердикт + TTR (time to recovery). (2) Платформа восстановилась сама (0 ручных действий, кроме санкционированного rollback по плану) во всех тестах, где это ожидалось. (3) Для каждого инцидента Log Audit Manifest подтверждён на 100%: инцидент реконструируется по логам без участия очевидца. (4) Выявленные дыры (alert rules без fire, restart policy неверная, отсутствующие log-маркеры, journald не уходит в Loki, Tor=SPOF) зафиксированы как Debt-записи и (5) закрыты фикс-волной с повторным прогоном упавших тестов.
IMPLEMENTS:            Требование заказчика от 2026-08-03: «должна выстоять или восстановиться сама, штатно отработать, особое внимание логам об инцидентах» + «добавь тест Тора, через него мы в телеге общаемся».
IMPACTS:               tronyx-vps (production: tronyx-site, dance-site, botanika, legacy; 14 модулей). Риск: кратковременная деградация/даунтайм сайтов в окне прогона (минуты). Логирование: Loki (контейнерные логи, retention 7d), journald (хостовые логи), Grafana alert history, status-page.
REQUIRES:              (1) SSH-доступ оператора к tronyx-vps (ключ в node.yaml); (2) свежий валидный бэкап (postgres + app-data → S3) перед прогоном; (3) согласованное окно обслуживания (минимум 1 день, T11 reboot — 15 мин); (4) rollback-планы на каждый тест; (5) `make check` зелёный как baseline.
$END_ARTIFACT_CONTRACT

## 1. Контекст

**Нода:** tronyx-vps (103.88.243.151) — единственный удалённый VPS; он же e2e-таргет (node-configs/test-e2e — тот же хост). Отдельного staging-VPS нет.

**Стек:** проекты tronyx-site (tronyx.ru), dance-site (sexydancerostov.ru), botanika (botanika.tronyx.ru), legacy; модули nginx, platform-secrets, postgres, redis, clickhouse, minio, litellm, langfuse, monitoring (Prometheus/Grafana), logging (Loki/promtail), infra-metrics, backup-cron, status-page, hermes-agent; tor enabled.

**Self-healing механизмы под проверкой:**
| Механизм | Где | Проверяется тестом |
|----------|-----|--------------------|
| restart: always/unless-stopped | docker-compose всех модулей | T1, T6, T7, T11 |
| WAL recovery postgres | postgres | T6 |
| Healthcheck poller (unhealthy→healthy) | core/internal/deploy/healthcheck_poller.py | T1, T5, T6, T7 |
| Grafana alerting → Telegram (critical/warning) | monitoring/config/alerting/contact-points.yml.telegram | все destructive |
| backup-cron → S3 | core/modules/backup-cron | T8, T10 |
| tor_proxy_check | core/internal/healthcheck/tor_proxy_check.py | T5 |
| cert renewal / stale cert serve | nginx + cert_orchestrator | T9 |

## 2. Критичные находки предварительного аудита (влияют на дизайн тестов)

1. **Telegram-канал висит на Tor.** `shared/telegram_notifier.send_telegram` использует `proxy_url` (privoxy 127.0.0.1:8118 поверх tor). Все уведомления (деплой, алерты) идут через Tor → отказ Tor = слепой алерт-канал. Требует отдельного теста T5 и Debt-записи о SPOF.
2. **journald НЕ уходит в Loki.** promtail-config.yml собирает только docker-логи контейнеров (docker_sd). Хостовые логи (docker daemon, systemd, OOM-killer ядра, cron) доступны только через journalctl — не переживают пересоздание VPS и не видны в Grafana. → Debt D-1: добавить journal-скрейп в promtail (фаза 5).
3. **Loki retention 7d** (filesystem storage, compactor enabled). Достаточно для анализа инцидентов в рамках программы, но требует снятия логов-артефактов (export) сразу после каждого теста.

## 3. Программа испытаний (11 тестов)

| # | Тест | Фаза | Инъекция | Риск |
|---|------|------|----------|------|
| T1 | Рестарт Docker daemon | semi-safe | `systemctl restart docker` | низкий |
| T2 | Отказ DNS хоста | semi-safe | `systemctl stop systemd-resolved` | низкий |
| T3 | Сетевая партиция наружу 120 c | semi-safe | iptables OUTPUT DROP (iptables-apply, автооткат) | низкий |
| T4 | Clock skew ±24 h | semi-safe | timedatectl + date -s | средний (Loki retention) |
| T5 | **Отказ Tor (Telegram-канал)** | semi-safe | stop tor+privoxy | средний (слепой алерт-канал) |
| T6 | SIGKILL Postgres под нагрузкой | destructive | `docker kill -s KILL <postgres>` + INSERT-loop | низкий (WAL) |
| T7 | OOM-kill модуля | destructive | stress-ng → OOM killer | низкий (restart policy) |
| T8 | Диск 90–93% | destructive | dd в /tmp | средний (ENOSPC) |
| T9 | Повреждение TLS cert + secrets | destructive | подмена байтов (с cp-бэкапом) | низкий (stale serve) |
| T10 | Restore-drill: DROP БД → restore из S3 | destructive | DROP DATABASE chaos_drill | низкий (тестовая БД) |
| T11 | Полный reboot VPS | финал | `sudo reboot` | средний (даунтайм ≤15 мин) |

Полные спецификации тестов (инъекция / ожидаемое самовосстановление / Log Audit Manifest / критерий прохода) — в 02-DevPlan.md §4.

## 4. Риски и предохранители

| Риск | Митигация |
|------|-----------|
| Даунтайм сайтов в окне прогона | Согласованное окно обслуживания; T11 — в конце программы; каждый тест — по одному, с паузой на аудит |
| Потеря данных (T6, T8, T9) | Свежий полный бэкап перед прогоном + валидация restore-артефакта; T6 — только committed-строки (WAL); T10 — тестовая БД chaos_drill, не realty_db |
| Невозможность откатить | Rollback-план на каждый тест (приложение: чек-лист «что сломали → как откатить → как проверить») |
| Самозалочивание (T3, T5, T11) | iptables-apply с автооткатом 120 c; SSH allowed (established + локальная подсеть); T5/T11 — оператор в консоли VPS-панели, не только по SSH |
| Потеря логов инцидента | Log Audit Manifest с проверкой сразу после каждого теста; export логов (journalctl --since, Loki API) в files/ артефактов до следующего теста |
| Loki retention затрёт след T4 (clock skew) | Снапшот размера/границ Loki до/после; первичный аудит T4 — journalctl с фильтром по реальному времени (не по смещённому) |

## 5. Вне скоупа

- Option D (recurring nightly chaos) — после анализа результатов программы, отдельным DevPlan.
- Изменения кода платформы (фиксы) — только Debt-записи в фазе 5; реализация фиксов — следующим DevPlan (или в рамках 126 при малом объёме).
- Тесты на staging-VPS (нет инфраструктуры).

$END_BRIEF
