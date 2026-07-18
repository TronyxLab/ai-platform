<!-- GREP_SUMMARY: Brief, status-page, deploy-verification, secret-token-url, ci-gate, rollback, healthcheck, freshness-contract, superposition-open -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Background (existing primitives) → ◇ Problem → ◇ Superposition (5 options, collapse OPEN) → ◇ Security sub-decision → ◇ CI-gate contract → ◇ Extra ideas → ◇ Acceptance Criteria → ◇ Non-scope → ⎋ Open Questions -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Бриф фичи «Node Status Page» — единый URL на главном домене ноды с секретным токеном, который отдаёт агрегированный статус всех сайтов и сервисов ноды: человеку — HTML со ссылками для ручной проверки после деплоя, CI — машиночитаемый эндпоинт как финальный gate работоспособности (fail → rollback). Без реализации — только план.
- **DESCRIPTION:** Фиксирует существующие примитивы платформы (`make verify`, `make healthcheck`, cron `docker-healthcheck.sh`, monitoring), разрыв между ними, суперпозицию из 5 вариантов реализации (коллапс НЕ выполнен — открытый вопрос), суб-решение по защите URL, контракт CI-gate, дополнительные идеи и критерии приёмки.
- **RATIONALE:** После деплоя оператору нужна одна страница: кликнуть → увидеть статус всех vhosts и сервисов → перейти по ссылкам для ручной проверки. CI нужен тот же источник истины как финальный smoke: один `curl -f`, non-200 → rollback. Сейчас эти данные размазаны по `make verify` (внешний pull, только expose:true домены), `make healthcheck` (SSH), cron-healthcheck (результат не публикуется) и Grafana (не является deploy-gate).
- **ACCEPTANCE_CRITERIA:** (1) один URL на главном домене ноды защищён секретом ≥32 hex из SOPS/age; (2) HTML-вид: таблица всех vhosts + сервисов, статус, кликабельные ссылки; (3) JSON-вид для CI + бинарный маркер PASS/FAIL, потребляемый одним `curl -f`; (4) freshness-контракт — устаревший статус = FAIL; (5) страница не зависит от проверяемых сервисов и не проверяет себя; (6) токен нигде не попадает в git/логи в открытом виде; (7) все новые операции проходят через Makefile-фасад без новых глаголов вне глоссария.
- **IMPLEMENTS:** skill `superposition` (FULL mode, collapse OPEN), протокол `dev-pipeline` (Brief → Architect → Coder → QA)
- **IMPACTS:** `core/internal/healthcheck/docker-healthcheck.sh` (crontab, генерация статуса — Option B), `core/modules/nginx/templates/` (vhost location с токеном), `core/internal/verify/verify-domains.sh` (потребление status-эндпоинта в CI), `node.yaml` schema (источник списка проверяемых доменов/сервисов), `core/entrypoint-manifest.yaml` (если появится новый таргет — см. Open Questions), CI workflow post-deploy шаг (gate + rollback)
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

## SUPERPOSITION: варианты реализации (коллапс OPEN)

### Option A: Микро-модуль `status-page` (live-агрегатор) [score: 6/10]
- **Approach:** Новый контейнер `core/modules/status-page/`: по HTTP-запросу выполняет проверки (curl vhosts изнутри, docker inspect health), рендерит HTML + JSON. nginx `location /_status/<token>/ → proxy_pass`.
- **Trade-offs:** Realtime по клику; но +1 рантайм-сервис (поверхность атаки), обязательный rate-limit (иначе DoS-усилитель: 1 внешний запрос → N внутренних), chicken-egg «кто проверяет проверяющего», доступ к docker.sock из контейнера — отдельное security-решение.
- **Best when:** нужны on-demand deep-проверки и расширяемость логики.

### Option B: Static artifact — cron публикует status.json + HTML [score: 8/10] ⭐ recommended
- **Approach:** Расширить существующий cron `docker-healthcheck.sh`: каждый прогон пишет `status.json` + рендерит статический `index.html` в volume, который nginx отдаёт по секретному пути. Бинарный маркер для CI: файл `health` создаётся только при полном PASS, удаляется при FAIL → `curl -f .../health` → 404 = fail.
- **Trade-offs:** Ноль нового рантайма, минимальная поверхность (чистая статика); но лаг до периода cron (≤60 сек) и обязательный **freshness-контракт**: `generated_at` в JSON, статус старше 2× периода cron = FAIL (иначе умерший cron неотличим от «всё хорошо»).
- **Best when:** Small Simple Blocks, максимум реиспользования существующей инфраструктуры. Базовый вариант.

### Option C: Готовый инструмент — Gatus как модуль [score: 7/10]
- **Approach:** Gatus (declarative uptime dashboard) как модуль платформы; список endpoints генерируется из node.yaml по паттерну `render-vhosts`. Из коробки: история, flapping-детект, alerting, badges, `/api/v1/endpoints/statuses` для CI. Токен-защита через nginx.
- **Trade-offs:** Фичи бесплатно; но +1 сторонний сервис, новый контур конфиг-генерации, шире исходной задачи.
- **Best when:** понадобится история/алерты/badges — эволюционный путь B→C без выбрасывания B (Gatus заменяет генератор, nginx-контур остаётся).

### Option D: Pull-only — `make verify MODE=deep` + HTML-отчёт как CI-артефакт [score: 6/10]
- **Approach:** Никакой страницы на ноде. CI после деплоя гоняет расширенный verify (домены + `/health` сервисов через SSH) и генерирует HTML-отчёт со ссылками как артефакт workflow.
- **Trade-offs:** Zero attack surface на VPS; но нет «открыть URL с телефона», отчёт живёт в CI UI, не live.
- **Best when:** безопасность абсолютный приоритет, CI — единственный потребитель.

### Option E: nginx + client-side JS (fetch из браузера) [score: 3/10] — REJECTED
- **Approach:** Статичный HTML, JS fetch'ит `/health` каждого домена из браузера пользователя.
- **Rejected:** CORS-конфигурация на каждом vhost; CI не исполняет JS (headless-браузер в CI противоречит тестовым правилам); внутренние сервисы из браузера недоступны. Зафиксировано, чтобы не возвращаться.

### Recommendation: **Option B**, эволюционный путь B→C. Коллапс — за оператором.

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
  1. sleep <период cron + запас>          # только для Option B
  2. curl -fsS https://<main-domain>/_status/<token>/health
     → HTTP 200 body=PASS  → деплой подтверждён
     → HTTP 404 / non-200 / timeout → rollback (существующий механизм)
  3. (опц.) curl .../status.json → приложить к job summary
```

Требования: один вызов, ноль парсинга для вердикта, JSON — только для диагностики. Freshness проверяется на стороне генератора (stale → маркер удаляется), CI об этом знать не должен.

## Дополнительные идеи (brainstorm, приоритизировать при коллапсе)

1. **Version drift detect** — сервисы отдают git SHA + deploy timestamp; страница сравнивает с ожидаемым SHA деплоя → ловит «деплой прошёл, контейнер старый» (silent failure, классика).
2. **Shallow vs deep уровни** — shallow: HTTP 200 vhosts; deep: redis PING, LiteLLM liveliness, pg SELECT 1 через существующие module healthcheck.sh.
3. **Prometheus textfile-экспорт** того же status.json → алерты в Grafana без нового кода.
4. **История последних N прогонов** (ring buffer в JSON) → виден flapping. Если нужно всерьёз — это сигнал коллапса в Option C.
5. **Ссылки на внутренние админки** (Grafana, Langfuse, MinIO console) в HTML-таблице — ручная проверка «перейти на все нужные сервисы» из одной точки.
6. ⚠️ TRAP[DESIGN] **Anti-recursion:** страница не проверяет сама себя; генератор не зависит от проверяемых сервисов (никакого postgres/litellm в цепочке рендера — только shell + статика).
7. **Глоссарий глаголов:** новый make-глагол не требуется — генерация = расширение `healthcheck`, потребление в CI = расширение `verify`. Если Architect решит иначе — регистрация в `entrypoint-manifest.yaml` обязательна (инвариант 5).

## Acceptance Criteria (черновик — уточняет Architect в DevPlan)

1. `https://<main-domain>/_status/<token>/` отдаёт HTML: таблица всех vhosts из node.yaml + всех модулей ноды, статус, кликабельные ссылки.
2. `.../status.json` — машиночитаемый агрегат: `{status, generated_at, checks[]}`.
3. `.../health` — бинарный маркер: 200+PASS только при «все проверки OK и статус свежий», иначе 404.
4. Неверный токен → 404, ответ неотличим от несуществующего пути.
5. Токен отсутствует в git, в открытых логах nginx и в Referer исходящих переходов.
6. CI post-deploy шаг: fail эндпоинта → rollback; проверено негативным тестом (R5: убить один сервис → маркер исчезает → CI fail).
7. Умерший генератор статуса (cron остановлен) → `.../health` = 404 не позже 2× периода генерации.
8. Все операции — через Makefile-фасад; новые глаголы не вводятся либо регистрируются в entrypoint-manifest.yaml.

## Non-scope

- Alerting/уведомления (существующий monitoring; либо Option C позже).
- Публичная status page для пользователей (это внутренний операторский инструмент).
- Автоматический rollback-механизм как таковой — используется существующий, бриф определяет только триггер.
- Мультинодная агрегация (одна страница = одна нода; агрегация — отдельный бриф при необходимости).

## Open Questions (для коллапса)

1. **Q1 (главный):** Вариант реализации — B (static artifact, recommended) / C (Gatus) / A (live-сервис) / D (pull-only)?
2. **Q2:** Защита — токен в path (с митигациями) или Basic Auth?
3. **Q3:** Уровень проверки в v1 — shallow (HTTP 200) или сразу deep (module healthchecks)?
4. **Q4:** Нужна ли история прогонов в v1 (если да — сильный аргумент за C)?

$END_BRIEF
