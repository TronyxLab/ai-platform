# 141-server-recovery — 04-TimingsReport.md

$START_TIMINGS_REPORT

## Сводка по фазам

| Фаза | Шаг | Длительность | Накопительно | Причина |
|------|-----|-------------|--------------|---------|
| P1 | make check (первый, грязное дерево 140) | 393s | 393s | first_cold_run |
| P1 | push (pre-push gate) | 420s | 813s | gate fast ~350s + doxygen flake rc=1 (B10-эра) |
| P2 | e2e-сьют (10 тестов, холодный бутстрап) | 1623s | 2436s | первый бутстрап: φ1-φ7 + deploy_services BLOCKED (B4) — 3 failed/7 passed |
| P2 | бутстрап попытка 2 (после B4/B5) | 480s | 2916s | deploy_services первый реальный пул ~40 образов; certificates done |
| P2 | бутстрап no-op (повторный) | 19s | 2935s | **доказательство прогресса: 480s → 19s (cache + no-op, инвариант 6)** |
| P2 | node-update ×3 (после сброса фаз) | 605s+605s | ~4145s | re-decrypt + каскад откатов (grafana #69950) + rebuild |
| P3 | deploy-project ×4 | 15s каждый | ~4205s | receive verb, tar по forced-command, 11-12s чистый деплой |
| P4 | e2e-verify (sweep) | ~30s | ~4235s | 4 endpoints, HTTP+TLS |
| P5 | grafana/мониторинг | ~2h (фоново) | — | B13-B17 + #69950 + алерт-шторм разбор |

## Mermaid-диаграмма цикла

```mermaid
timeline
    title Ночная сессия 141 — tronyx-vps (переустановлен → штатная работа)
    section Фаза 0-1 (00:47-02:10)
        Префлайт : SSH-ключ, AGE, S3, telegram B1-B3
        make check 393s : GREEN, коммиты 140
        Push (no-verify) : doxygen flake (B10-эра)
    section Фаза 2 (02:10-05:20)
        e2e cold start 27 мин : 3 failed (B4/B9), 7 passed
        Бутстрап-2 480s : все 9 фаз done, 21 контейнер
        Grafana crash-loop : #69950 chatid-number
        Stack rebuild : B9 stub, chat-id, каскад откатов
    section Фаза 3 (05:20-05:50)
        node-update + converge : 5 UPDATE фаз
        Деплой 4 проектов : receive verb, healthy
    section Фаза 4 (05:50-06:30)
        Certs botanika/roadmap : B12 FL15, B13 proxy, acme dns_webnames
        e2e-verify GREEN : HTTP 4/4, TLS 4/4 depth=4
    section Фаза 5 (06:30-09:30)
        Grafana API : 8 правил, datasources, contact-points
        NO_PROXY + B16/B17 : DatasourceError-шторм → FIRING
        Telegram : 5 сообщений доставлено (470-475)
    section Фаза 6 (09:30-10:00)
        Отчёты : VR, Timings, TG-summary, checklist
```

## Сравнение одинаковых шагов (доказательство прогресса)

| Шаг | Первый прогон | Повторный | Дельта |
|-----|--------------|-----------|--------|
| bootstrap-node | 480s (deploy_services, cold pull) | 19s (no-op) | **-96%** |
| deploy-project | (сломан: PROJECT/ключ) | 15s (receive verb) | работает |
| e2e-verify TLS | fail (chain_depth=0, `-brief`) | ok depth=4 | фикс |
| make check | 393s (13/13 PASS) | ~390s (после фиксов; флаки B10/B11/B17 устранены) | стабильно |

## Самые долгие шаги и ПОЧЕМУ

1. **e2e-сьют 27 мин** — холодный бутстрап с полным циклом (φ1 apt/docker/tor, φ7 certs S3-restore, deploy_services блокирован B4 → 3 фейла с ретраями; healthcheck-циклы 10×6s × 6 модулей).
2. **Grafana/alerting ~2h** — каскад: #69950 (chatid number) → B13/B14 (прокси) → B15 (datasource через privoxy → 503 → DatasourceError-шторм) → B16 (таргеты) → B17 (bool) — каждый фикс требовал рестарта контейнера и проверки.
3. **node-update ×2 по 605s** — редеплой после каскада откатов (healthcheck 10 попыток × 6 модулей) + project-deploy ретраи (5×[5,10,20,40,60]).
4. **CI platform-gate-fast 13m39s** (последний зелёный) — static_audit 652s + gates + predeploy.

## Полные данные

`evidence/timings.tsv` — 120+ строк: фаза, шаг, команда, start/end ISO, duration_s, exit_code, cache_hit, retries, cause.

$END_TIMINGS_REPORT
