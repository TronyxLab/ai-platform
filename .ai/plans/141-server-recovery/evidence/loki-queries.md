# Loki Queries Evidence — сессия 141

> Источник label-схемы: core/modules/logging/config/promtail-config.yml (docker_sd_config)
> Ключевые labels: `compose_service`, `compose_project`, `container`, `service_name`, `detected_level`, `host` (nginx)
> nginx: дополнительно `status`, `request_method`, `host`
> Прокси-путь через Grafana: /api/datasources/proxy/uid/loki/loki/api/v1/query_range
> Sweep выполнен: 2026-08-06 09:08–09:09 MSK (grafana жива)

## Статус — ✅ ВСЕ СТРИМЫ ЖИВЫ (кроме journald-лейбла)

| Контейнер | Стримы | Samples | Статус |
|-----------|--------|---------|--------|
| nginx | 1 | 10 | ✅ JSON access-logs, labels status/host/request_method |
| postgres | 1 | 10 | ✅ checkpoint/ready |
| redis | 2 (info + warn) | 10 | ✅ ⚠️ warn: "Memory overcommit must be enabled!" |
| clickhouse | 2 (trace + unknown) | 10 | ✅ |
| minio | 1 | 10 | ✅ |
| litellm | 1 | 10 | ✅ health/liveliness 200, /metrics 200 |
| langfuse | 1 | 10 | ✅ MCP features registered |
| promtail | 1 | 10 | ✅ собственные логи |
| hermes-agent | 1 | 10 | ⚠️ **Telegram ConnectError: "All connection attempts failed" (attempt 1/8, 2/8)** |
| loki (через compose_project=platform) | 3 | 10 | ✅ |
| journald | — | — | ✅ найдено по `{host="tronyx-vps"}` (лейбл НЕ job=journald, а host) |

## Проекты (допроверено 09:10–09:13 MSK)

| compose_project | Статус | Данные |
|-----------------|--------|--------|
| botanika | ✅ | nginx-proxy 200 (curl healthcheck) |
| roadmap | ✅ | nginx-proxy 200 (wget /health каждые 10s) |
| tronyx-site | ✅ | nginx-proxy 200 (curl) |
| dance-site (домен sexydancerostov.ru) | ✅ | nginx-proxy 200 (curl) |
| legacy | ⚠️ пустой стрим | не задеплоен/не пишет (context_deploy:legacy FAILED ранее — проверить) |
| tronyx / sexydancerostov (как compose_project) | пусто | ожидаемо — реальные имена: tronyx-site / dance-site |

## Ключевые находки

### 1. hermes-agent не может подключиться к Telegram ⚠️
```
WARNING hermes_plugins.telegram_platform.adapter: [Telegram] Connect attempt 1/8 failed: httpx.ConnectError: All connection attempts failed — retrying
WARNING ... Connect attempt 2/8 failed ...
```
Вероятная причина: tor-прокси (TELEGRAM_PROXY_URL) на сервере не работает/не запущен (S2 в StatusReport: tor — серверный канал). Проверить: tor-контейнер/сервис, ENV hermes-agent.

### 2. journald: лейбл — `host="tronyx-vps"`, НЕ `job="journald"` ⚠️ (не баг, а документируемое отличие)
Запрос `{host="tronyx-vps"}` вернул: UFW BLOCK (защита работает), SSH-брутфорс "Disconnected from invalid user joe 160.119.69.14 [preauth]" — sshd+ufw отработали.

### 3. redis: WARN "Memory overcommit must be enabled!" — рекомендация sysctl vm.overcommit_memory=1

### 4. nginx по host=tronyx.ru — 4 стрима по status (200/301/444/502)
- 502 в 04:17–04:37 UTC+3: период, когда grafana была в crash-loop
- 444 (nginx close): 05:40–05:42 MSK — во время converge/рестартов
- 200: сейчас — сайт работает

## Запросы для повторных проверок

| Проверка | Запрос |
|----------|--------|
| Все логи платформы | `{compose_project="platform"}` |
| Проект botanika | `{compose_project="botanika"}` или `{project="botanika"}` |
| Проект roadmap | `{compose_project="roadmap"}` или `{project="roadmap"}` |
| Проект legacy | `{compose_project="legacy"}` |
| Проект tronyx | `{compose_project="tronyx"}` |
| Ошибки платформы | `{compose_project="platform"} \|~ "(?i)error\|fatal\|panic"` |

## Выводы

1. **Пустых стримов НЕТ** — все контейнеры платформы пишут в Loki (promtail docker_sd работает корректно после восстановления).
2. hermes-agent Telegram — единственный реальный сбой в логах (ConnectError, retries).
3. journald работает, лейбл host (не job) — запросы в шаблонах скорректированы.
4. Проекты (botanika/roadmap/...) — проверить после успешного деплоя контекстов (на момент sweep контексты не задеплоены: context_deploy FAILED ранее).
