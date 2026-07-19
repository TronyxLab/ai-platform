<!-- GREP_SUMMARY: Brief, status-page, deploy-verification, secret-token-url, ci-gate, rollback, healthcheck, live-service, collapsed-option-A, rate-limit, docker-sock -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Background (existing primitives) → ◇ Problem → ◇ Superposition (5 options, COLLAPSED → A) → ◇ Collapse Result → ◇ Security sub-decision → ◇ CI-gate contract → ◇ Extra ideas → ◇ Acceptance Criteria → ◇ Non-scope → ⎋ Design Notes for Architect -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Бриф фичи «Node Status Page» — единый URL на главном домене ноды с секретным токеном, который отдаёт агрегированный статус всех сайтов и сервисов ноды: человеку — HTML со ссылками для ручной проверки после деплоя, CI — машиночитаемый эндпоинт как финальный gate работоспособности (fail → rollback). Без реализации — только план.
- **DESCRIPTION:** Фиксирует существующие примитивы платформы (`make verify`, `make healthcheck`, cron `docker-healthcheck.sh`, monitoring), разрыв между ними, суперпозицию из 5 вариантов реализации (КОЛЛАПС ВЫПОЛНЕН: Option A — live-сервис, токен в path, shallow+deep в v1), суб-решение по защите URL, контракт CI-gate, обязательные требования live-сервиса и критерии приёмки.
- **RATIONALE:** После деплоя оператору нужна одна страница: кликнуть → увидеть статус всех vhosts и сервисов → перейти по ссылкам для ручной проверки. CI нужен тот же источник истины как финальный smoke: один `curl -f`, non-200 → rollback. Сейчас эти данные размазаны по `make verify` (внешний pull, только expose:true домены), `make healthcheck` (SSH), cron-healthcheck (результат не публикуется) и Grafana (не является deploy-gate).
- **ACCEPTANCE_CRITERIA:** (1) один URL на главном домене ноды защищён секретом ≥32 hex из SOPS/age; (2) HTML-вид: таблица всех vhosts + сервисов, live-статус, кликабельные ссылки; (3) JSON-вид для CI + бинарный вердикт `/health` (200 PASS / 503 FAIL), потребляемый одним `curl -f`; (4) rate-limit и timeout-бюджет — 1 внешний запрос не превращается в DoS-усилитель, зависшая проверка не подвешивает страницу; (5) status-page не зависит от проверяемых сервисов и не проверяет себя; (6) токен нигде не попадает в git/логи/Referer в открытом виде; (7) все новые операции проходят через Makefile-фасад без новых глаголов вне глоссария.
- **IMPLEMENTS:** skill `superposition` (FULL mode, collapsed 2026-07-18 → Option A), протокол `dev-pipeline` (Brief → Architect → Coder → QA)
- **IMPACTS:** `core/modules/status-page/` (НОВЫЙ модуль — module.yaml, docker-compose.base.yml, healthcheck.sh, Makefile→module.mk), `core/modules/nginx/templates/` (vhost location с токеном + rate-limit zone), `core/internal/verify/verify-domains.sh` (потребление status-эндпоинта в CI), `node.yaml` schema (источник списка проверяемых доменов/сервисов), `core/internal/bootstrap/discover_modules.py` (авто-подхват нового модуля), `core/entrypoint-manifest.yaml` (если появится новый таргет — см. Design Notes), CI workflow post-deploy шаг (gate + rollback)
- **REQUIRES:** `AGENTS.md` (root — инварианты 1, 5; secrets NEVER via git), `core/AGENTS.md` (каталог операций), `core/internal/verify/verify-domains.sh`, `core/entrypoints/healthcheck.sh`, node.yaml выбранной ноды

$START_BRIEF

# Brief: Node Status Page — deploy verification URL

## Background: что уже существует

| Примитив | Что делает | Ограничение |
|---|---|---|
| `make verify NODE=<n>` | curl всех `expose:true` доменов из node.yaml с машины оператора/CI, exit 0 только при всех HTTP 200 | Внешний pull; нет HTML; нет ссылок; не видит внутренние сервисы (postgres, redis, litellm) |
| `make healthcheck [NODE=]` | module healthcheck.sh всех модулей, локально или по SSH | Требует SSH; не HTTP-эндпоинт; не кликабельно |
| `core/internal/healthcheck/docker-healthcheck.sh` | **Уже запускается crontab'ом каждую минуту на VPS** | Результат не публикуется наружу |
| Модуль `monitoring` (Prometheus + Grafana) | TSDB + дашборды | Не «одна страница после деплоя»; не deploy-gate; вход требует логина |
| `make render-vhosts NODE=<n>` | Генерация nginx vhosts из node.yaml | — (переиспользуемый паттерн конфиг-генерации из node.yaml) |

**Разрыв:** нет одного URL на ноде, который одновременно (a) человеко-кликабелен со ссылками на все сервисы, (b) машиночитаем как CI-gate.

## Problem statement

После деплоя нужно:
1. **Человеку:** открыть один URL → увидеть статус всех vhosts и сервисов ноды → перейти по ссылкам для ручной проверки.
2. **CI:** дёрнуть тот же источник истины как финальный smoke-тест; fail → rollback.
3. **Безопасность:** страница раскрывает топологию ноды → доступ только по секрету; секрет не в git (модель доставки секретов: SOPS/age + SCP).

## SUPERPOSITION: варианты реализации (COLLAPSED → Option A, 2026-07-18)

### Option A: Микро-модуль `status-page` (live-агрегатор) [score: 6/10] ✅ CHOSEN
- **Approach:** Новый контейнер `core/modules/status-page/`: по HTTP-запросу выполняет проверки (curl vhosts изнутри, статус health контейнеров), рендерит HTML + JSON. nginx `location /_status/<token>/ → proxy_pass`.
- **Trade-offs:** Realtime по клику; но +1 рантайм-сервис (поверхность атаки), обязательный rate-limit (иначе DoS-усилитель: 1 внешний запрос → N внутренних), chicken-egg «кто проверяет проверяющего», доступ к статусам docker — отдельное security-решение.
- **Chosen because:** оператору важен realtime-статус в момент клика после деплоя, без лага cron-периода; расширяемость deep-проверок.

### Option B: Static artifact — cron публикует status.json + HTML [score: 8/10] — NOT CHOSEN
- **Approach:** Расширить существующий cron `docker-healthcheck.sh`: каждый прогон пишет `status.json` + рендерит статический `index.html` в volume, который nginx отдаёт по секретному пути. Бинарный маркер для CI: файл `health` создаётся только при полном PASS, удаляется при FAIL → `curl -f .../health` → 404 = fail.
- **Not chosen:** лаг до периода cron неприемлем для сценария «кликнул сразу после деплоя — увидел актуальный статус». Остаётся fallback-вариантом, если live-сервис окажется слишком дорогим в сопровождении; freshness-контракт из этого варианта переносится в A как деградационный режим (см. Design Notes).

### Option C: Готовый инструмент — Gatus как модуль [score: 7/10] — NOT CHOSEN
- **Not chosen:** шире исходной задачи, +1 сторонний сервис. Остаётся эволюционным путём, если понадобится история/алерты/badges.

### Option D: Pull-only — `make verify MODE=deep` + HTML-отчёт как CI-артефакт [score: 6/10] — NOT CHOSEN
- **Not chosen:** не закрывает сценарий «открыть URL с телефона после деплоя».

### Option E: nginx + client-side JS (fetch из браузера) [score: 3/10] — REJECTED
- **Rejected:** CORS-конфигурация на каждом vhost; CI не исполняет JS (headless-браузер в CI противоречит тестовым правилам); внутренние сервисы из браузера недоступны. Зафиксировано, чтобы не возвращаться.

### Collapse Result (2026-07-18, оператор)

| Вопрос | Решение |
|---|---|
| Q1 Архитектура | **Option A** — live-сервис `status-page` |
| Q2 Защита URL | **Токен в path** + обязательные митигации (см. суб-решение ниже) |
| Q3 Глубина проверок | **Shallow + deep сразу в v1** |
| Q4 История прогонов | Не в v1 (не выбран C) — при потребности отдельный бриф |

⚠️ TRAP[DECISION] · 2026-07-18 · HI · Выбран A (live) вопреки рекомендации B (static)
· Основание: realtime-статус в момент клика важнее минимализма; лаг cron неприемлем для post-deploy сценария.
· Цена: обязательные rate-limit, security-решение по доступу к docker-статусам, бюджет времени ответа, timeout-каскад.
· Rev: если сопровождение live-сервиса окажется дороже пользы — деградация до B без смены внешнего контракта (URL/JSON/health не меняются).

## Суб-решение: защита URL (секретный токен)

Токен в path — рабочий вариант при обязательных митигациях:

| Риск | Митигация |
|---|---|
| URI в nginx access_log | Маскирование/`access_log off` для location |
| `Referer` при клике по ссылкам со страницы | `Referrer-Policy: no-referrer` — **обязательно**, иначе токен утечёт на каждый проверяемый сервис |
| Индексация | `X-Robots-Tag: noindex, nofollow` |
| Подбор | Токен ≥32 hex; 404 (не 403) на неверный путь — не подтверждать существование |
| Токен в git | Хранение в SOPS/age → доставка через существующий secrets-канал (SCP); в CI — из GitHub Secrets |

**Альтернативы (не выбраны, зафиксированы):** Basic Auth (семантически чище, `curl -u` в CI — кандидат №2); signed TTL-URL через secure_link (мешает ручному использованию); IP-allowlist (убивает сценарий «кликнуть с телефона»); mTLS (overkill).

## CI-gate контракт (общий для всех вариантов)

```
post-deploy шаг CI:
  1. curl -fsS --max-time 60 --retry 3 --retry-delay 10 \
       https://<main-domain>/_status/<token>/health
     → HTTP 200 body=PASS  → деплой подтверждён
     → HTTP 503 / non-200 / timeout → rollback (существующий механизм)
  2. (опц.) curl .../status.json → приложить к job summary
```

Требования: один вызов (с retry на warm-up), ноль парсинга для вердикта, JSON — только для диагностики. `--max-time` обязан покрывать полный каскад внутренних проверок (см. Design Notes: timeout-бюджет). Retry покрывает окно старта контейнеров сразу после деплоя.

## Дополнительные идеи (brainstorm, приоритизация в DevPlan)

1. **Version drift detect** — сервисы отдают git SHA + deploy timestamp; страница сравнивает с ожидаемым SHA деплоя → ловит «деплой прошёл, контейнер старый» (silent failure, классика).
2. **Prometheus scrape** того же status.json (или /metrics у status-page) → алерты в Grafana без нового кода.
3. **История последних N прогонов** (ring buffer) → виден flapping. Не в v1 (см. Collapse Result Q4).
4. **Ссылки на внутренние админки** (Grafana, Langfuse, MinIO console) в HTML-таблице — ручная проверка «перейти на все нужные сервисы» из одной точки.
5. ⚠️ TRAP[DESIGN] **Anti-recursion:** status-page не проверяет сам себя и не зависит от проверяемых сервисов (никакого postgres/litellm/redis в цепочке рендера — только собственный процесс + исходящие проверки).
6. **Cache коротких TTL (5–15 сек)** внутри сервиса — дополнение к nginx rate-limit: повторный клик не запускает новый каскад проверок.

## Acceptance Criteria (черновик — уточняет Architect в DevPlan)

1. `https://<main-domain>/_status/<token>/` отдаёт HTML: таблица всех vhosts из node.yaml + всех модулей ноды, live-статус на момент запроса, кликабельные ссылки.
2. `.../status.json` — машиночитаемый агрегат: `{status, generated_at, duration_ms, checks[]}`, где checks[] содержит shallow (HTTP-код vhost) и deep (module health) результаты.
3. `.../health` — бинарный вердикт: HTTP 200 + body `PASS` только при «все проверки OK»; иначе HTTP 503 + body `FAIL` (live-сервис отвечает всегда; 404/connection refused означает «проверяющий мёртв» — для CI это тоже FAIL).
4. Неверный токен → 404, ответ неотличим от несуществующего пути.
5. Токен отсутствует в git, в открытых логах nginx и в Referer исходящих переходов (`Referrer-Policy: no-referrer` проверен тестом).
6. nginx rate-limit на location: превышение → 429 без запуска внутренних проверок; негативный тест обязателен.
7. Полный каскад проверок укладывается в timeout-бюджет (см. Design Notes); зависший внутренний сервис = FAIL этой проверки, не подвисание всей страницы.
8. CI post-deploy шаг: fail эндпоинта → rollback; проверено негативным тестом (R5: убить один сервис → `/health` = 503 → CI fail).
9. Падение самого status-page → CI curl fail (502 от nginx / connection refused) → rollback-триггер срабатывает. Chicken-egg закрыт: «проверяющий мёртв» = «деплой не подтверждён».
10. Модуль соответствует шаблону `core/modules/AGENTS.md` (module.yaml + interfaces, healthcheck.sh, Makefile→module.mk, обнаруживается `make discover-modules`).
11. Все операции — через Makefile-фасад; новые глаголы не вводятся либо регистрируются в entrypoint-manifest.yaml (инвариант 5).

## Non-scope

- Alerting/уведомления (существующий monitoring; либо Gatus-эволюция позже).
- Публичная status page для пользователей (это внутренний операторский инструмент).
- Автоматический rollback-механизм как таковой — используется существующий, бриф определяет только триггер.
- Мультинодная агрегация (одна страница = одна нода; агрегация — отдельный бриф при необходимости).
- История прогонов / flapping-аналитика (Q4 коллапса: не в v1).

## Design Notes for Architect (открытые инженерные решения в рамках Option A)

1. **Доступ к статусам docker** — суб-суперпозиция, решает Architect:
   - (a) docker-socket-proxy (например tecnativa) с allow только `GET /containers/*` — статусы health из compose-healthchecks;
   - (b) без docker.sock вообще: deep-статусы читаются из результата cron `docker-healthcheck.sh` (гибрид A+B: live shallow + published deep), тогда для deep обязателен freshness-контракт (`generated_at` старше 2× периода cron = FAIL);
   - (c) docker.sock read-only напрямую — наихудший по безопасности, требует явного обоснования в DevPlan.
2. **Timeout-бюджет:** total ≤ 30 сек; параллельный fan-out проверок; per-check timeout ≤ 5 сек.
3. **Источник списка проверок:** node.yaml — единственный source of truth (как у `render-vhosts` и `verify-domains.sh`). Никаких дублирующих списков в конфиге модуля.
4. **Технология сервиса:** минимальный рантайм (статический бинарь или python3-stdlib без фреймворка) — с оглядкой на Small Simple Blocks; решение за Architect.
5. **Make-глагол:** новый таргет не требуется — деплой через стандартный module lifecycle, потребление в CI — расширение `verify`. Если Architect решит иначе — регистрация в `entrypoint-manifest.yaml` обязательна (инвариант 5).
6. **Деградационный режим:** внешний контракт (URL, status.json, /health) спроектировать так, чтобы возможный откат на Option B (static) не менял потребителей — см. TRAP[DECISION] в Collapse Result.

$END_BRIEF
