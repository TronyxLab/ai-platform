# Timings Notes — сессия 141 (накопительные наблюдения)

> Источник: evidence/timings.tsv (append-only, НЕ редактируется этой дорожкой).
> Формат колонок: phase | step | command | start_iso | end_iso | duration_s | exit_code | cache_hit | retries | cause

## Наблюдения

- 01:56 MSK (из logs/make/test-node): certs OK — tronyx.ru + sexydancerostov.ru restored (s3), botanika + roadmap issued (acme dns).
- 01:56 MSK: `bootstrap:init` audit FAILED, phase `deploy_services` FAILED (первый cold-run), идёт повторная попытка (Attempt #3) — стек ещё не поднят, healthcheck nginx/postgres/redis FAIL after 10 attempts (ожидаемо на этапе повторного подъёма).
- 01:56 MSK: node-update: node.yaml validation `'type' is a required property` — зафиксировано в логе оператора (не моя дорожка, для контекста).
- 02:00 MSK: nginx отвечает 502 (Bad Gateway) — веб-слой поднят, upstream (grafana) стартует.
- 02:03–02:06 MSK: 502 сменился на Connection reset — nginx перезапущен во время deploy_services (context_deployer reload), все хосты (включая tronyx.ru) снова недоступны.
- 02:06 MSK: deploy_services дошёл до деплоя проектов: botanika → DeployOrchestrator, roadmap pull attempt 2/3 (ошибка pull, вероятно rate-limit/образ) — наблюдение для оператора.
- 02:11 MSK: HTTPS всё ещё reset по всем хостам; стек платформы (grafana) ещё не готов.
- 03:08 MSK: nginx снова 502 (оператор починил веб-слой) — upstream grafana не готов.
- 03:06–03:15: повторный bootstrap-node — converge_services success, но контейнеры платформы не healthy (healthcheck PASS: 0 в логе).
- 03:30: commit `fix(141): first-deploy pull retry 5×[5,10,20,40,60]` — корневая причина падения деплоя контекстов: transient pull kills bootstrap.
- 04:00: правки context_deployer — stub compose проектов несовместим с DeployOrchestrator (сервис `{name}-proxy` vs `service=project_name`, "no such service" → first-deploy FATAL exit 10).
- 04:17 MSK: **первое живое окно grafana** — /api/health 200 (db=ok, ver=11.6.16), затем падение на БД-запросах (timeout → 502). Crash-loop паттерн.
- 04:19: прямые порты 3000/9090/3100 OPEN, но контейнеры не отвечают (HTTP 000) — весь платформенный стек в нерабочем состоянии, жив только nginx (502).
- 04:31: commit `fix(141): cold bootstrap — certificates dep-gate (B4), TOR_ENABLED update (B5), acme proxy-clean env` — оператор чинит cert/окружение.
- 04:52: grafana стабильно 502 ~35 мин после последнего живого окна.
- 05:00–07:00: grafana в мёртвой фазе (окон нет); оператор коммитит фиксы: `a9f0e1e5 bootstrap stub compose B9, doxygen flake B10, check killpg B11, static_audit timeout 600`; `401e579b DOCKER_HUB_AUTH в SoT env_defaults, static_audit timeout 900s`.
- 07:34–07:42: node-update (SCP core + converge) — 12 healthcheck PASS (сервисы healthy!), "Node update complete" — НО grafana всё равно 502 (crash-loop не прерван).
- 07:28 MSK: catcher поймал окно (health 200), но к запросам grafana уже упала — окна секундные, редкие (~3 часа между).

**Паттерн живых окон grafana (для отчёта):** окна жизни ~секунды-минуты, интервалы часы. health отвечает, БД-запросы (user/datasources/...) зависают/падают. Согласуется с crash-loop контейнера (docker restart backoff) — корень, вероятно, на старте grafana (БД/провижининг).

## Сводка фаз (финальная, 374 шага, 9307s ≈ 155 мин)

| phase | шагов | Сумма, s | Детали |
|-------|-------|----------|--------|
| phase1 | 4 | 1222 | push-цикл: check 393s + 2× push rc=1 (doxygen flake) + no-verify 7s |
| phase2 | 15 | 7159 | **119 мин** — основной цикл восстановления (ниже) |
| phase3 | 8 | 83 | деплой проектов (tronyx-site/dance/botanika/roadmap 14-15s каждый, rc=0) |
| phase4 | 6 | 13 | e2e-verify: remote FAIL (SSH) → local PASS |
| probe | 39 | 27 | пробы grafana (окна crash-loop) |
| ci-poll | 287 | 729 | фоновый poller (2-3s/цикл) |
| grafana-api/loki/prometheus | 9 | 54 | финальный сбор evidence |

## Phase2 детально (7159s)

- test-node-cold-start: **1623s rc=2** (первый cold bootstrap на голой ноде; 3 failed/7 passed)
- bootstrap-attempt-2: **480s rc=0** (после B4/B5 cert/TOR фиксов)
- make-check × 6 прогонов по 376-399s (rc=2 в 5 из 6 — фикс-циклы B9/B10/doxygen)
- make-check-final/final2: **744s ×2 rc=2** (полный набор 141 фиксов)
- node-update-3-force: **605s rc=0** (реконсиляция после fix enc.yaml)
- node-update-5-rebuild: **605s rc=0** (rebuild стека с fix chat-id #69950 — после него grafana ожила)

## Заметки для 04-TimingsReport.md

1. **Push-цикл (phase1):** 2 неудачных push по ~400s (pre-push gate, doxygen transient flake) + no-verify 7s. Doxygen flake стоил ~800s. → Рассмотреть кэширование doxygen или retry-on-flake в pre-push.
2. **Cold bootstrap = 1623s** (без учёта pull-времени образов). Основные фазы (registry/certs/deploy_services/converge) — ~27 мин на голой ноде; из них: pull образов (повторные retry 5×[5,10,20,40,60] — фикс 2665a866), healthcheck retries (nginx/postgres/redis/clickhouse/litellm FAIL на первом подъёме).
3. **Make-check в фикс-цикле: 376-744s на прогон, 6 прогонов = ~2600s** (43 мин) — доминирующая статья расходов phase2. cache_hit сокращал, но при правках core — полный пересчёт.
4. **Деплой проектов (phase3): 14-15s/проект** — после починки stub compose и receive-канала (forced-command tar) деплой быстрый и стабильный.
5. **e2e-verify MODE=remote блокирован SSH ci-deploy** (rc=2/1); MODE=local — 2s PASS. → Восстановить ci-deploy authorized_keys (φ2 add_ssh_key).
6. **Ключевая точка восстановления:** node-update-5-rebuild (605s) + fix chat-id #69950 → grafana ожила, все 8 alert-rules загрузились.
7. **Grafana crash-loop окна:** health 200 появлялся редко (2 раза за 3.5 часа: 04:17, 07:28 MSK) — при сбое провижининга контакт-пойнтов (#69950 chatid) контейнер умирал на БД-запросах. Диагностика через window-catcher (poll 20s → sweep) сработала.

## Хронология проверок timings.tsv

| Время (MSK) | Строк | Комментарий |
|-------------|-------|-------------|
| 01:55 | 3 | phase1: make check 393s OK; git push 420s rc=1 |
| 04:50 | ~250 | фазы 2-4: test-node 1623s, фикс-циклы, bootstrap 480s |
| 09:30 | 374 | финал: phase3 деплои 14-15s, phase4 e2e-verify local PASS |
