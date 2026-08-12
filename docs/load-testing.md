# GREP_SUMMARY: load-testing, docs, load-test, smoke, regression, capacity, locust, promql, saturation, baseline, remote-runner
# STRUCTURE: ┌quick-start┐ → ◇ architecture (SoT + runner) → ◇ scenarios → ◇ modes → ◇ saturation → ◇ baseline
#           → ◇ remote → ◇ mock-model → ◇ report → ◇ limitations → ⎋ unit-tests

# Load Testing (DevPlan 146)

Система нагрузочного тестирования платформы: Locust-генератор, 3 режима прогона,
PromQL-анализ насыщения из существующего Prometheus, baseline-сравнение по датам.

**GREP_SUMMARY:** load-testing docs load-test smoke regression capacity locust promql saturation baseline remote-runner

---

## 1. Быстрый старт

```bash
# Установка генератора (load extra — НЕ runtime-зависимость платформы)
pip install -e ".[load]"        # или: make venv && .venv/bin/pip install -e ".[load]"

# Smoke-прогон web-сценария против тестовой VPS (>= 90s, инвариант 10)
make load-test SCENARIO=web NODE=test-e2e MODE=smoke

# Regression (300s, сравнение с previous-прогоном)
make load-test SCENARIO=web NODE=test-e2e MODE=regression

# Capacity (поиск max RPS, автостоп по error>5% | p99>3s)
make load-test SCENARIO=llm NODE=test-e2e MODE=capacity
```

Таргет `load-test` — тонкий фасад: `python3 -m core.internal.loadtest.runner_cli
--scenario <s> --node <n> --mode <m>`. Exit-коды по контракту платформы
(`core/internal/shared/contracts.py`):

| Код | Семантика | Ситуация |
|-----|-----------|----------|
| 0 | ok | PASS и WARN (WARN не блокирует) |
| 1 | generic error | вердикт FAIL (regression/capacity), ошибка прогона, недоступный Prometheus, отсутствие locust |
| 2 | ConfigNotFound | scenarios.yaml не найден |
| 3 | ConfigParse | битый YAML/JSON (scenarios.yaml, history.json) |
| 4 | ConfigValidation | неизвестный сценарий, пустые пороги, rps<=0 (fail-fast) |
| 10 | Fatal — ручное вмешательство | capacity на нетестовой ноде без `LOAD_ALLOW_PROD=1` |

## 2. Архитектура

```
core/loadtest/scenarios.yaml          — ЕДИНЫЙ SoT: endpoint, target_rps, users, пороги
core/loadtest/scenarios/*.py          — locust-сценарии (web, llm, llm_stream,
                                        langfuse_ingest, db*, s3*) — читают env LT_*
core/internal/loadtest/config.py      — SoT + env-оверрайды + NODE-резолв + валидация (exit 4)
core/internal/loadtest/runner_cli.py  — CLI-оркестратор (exit по контракту)
core/internal/loadtest/prometheus_pull.py — post-run PromQL saturation (инвариант 5)
core/internal/loadtest/report.py      — report.json + markdown + junit
core/internal/loadtest/baseline.py    — history.json + регрессионные дельты
core/internal/loadtest/capacity.py    — ступенчатый ramp (start×2, max_steps=8, safety-stop)
core/internal/loadtest/runner_remote.py — LOAD_RUNNER=node (docker run на ноде)
core/loadtest/history/                — baseline (КОММИТИТСЯ в репо)
load-results/                         — полные отчёты (gitignored целиком)
```

**Инварианты (DevPlan 146 + 146-m1):**

1. **users ≠ rps.** Точный RPS задаёт `constant_throughput` (locust.wait_time) через env
   `LT_TARGET_RPS`/`LT_USERS`: сценарии строят `wait_time = _rps_wait_time(LT_TARGET_RPS,
   LT_USERS)` — единый helper `core/loadtest/scenarios/__init__.py` (146-m1 BUG-1:
   CLI-флаг rate-limit в locust отсутствует). `users` — размер пула
   (`users = rps × 2`, запас на latency ≤ 2s; для сценариев с latency > 2s пул
   увеличивается вручную в SoT). RPS = users/latency — пул `users=rps` при latency
   100ms дал бы ~10× целевой RPS. `constant_throughput` latency-адаптивен
   (wait = max(0, 1/per_user − run_time)); per-user RPS = target/users.
2. **Длительности ≥ scrape_interval Prometheus** (30s global, 60s cadvisor/node-exporter):
   smoke ≥ 90s (≥3 сэмпла 30s-метрик, ≥2 по 60s); rate-окна запросов ≤ run_time/2
   (smoke/capacity → `1m`, regression → `2m`); метрика с <2 сэмплами →
   `insufficient_metrics` → WARN (не FAIL).
3. **Ноль новой мониторинговой инфраструктуры:** saturation — только post-run
   PromQL pull из существующего Prometheus (порт 9090, `LOAD_PROMETHEUS_PORT`).
4. **LLM-детерминизм:** сценарии llm/llm_stream гоняются только против mock-модели
   `mock-echo` (openai/echo, фикс latency ~50ms). Без mock на ноде — ранний FAIL
   с сообщением (даже при `LOAD_ALLOW_PROD=1`).

## 3. Сценарии (SoT: `core/loadtest/scenarios.yaml`)

| Сценарий | Описание | По умолчанию |
|----------|----------|--------------|
| `web` | nginx front: `https://{domain}/` + `/status` | включён |
| `llm` | `POST http://{host}:4000/chat/completions` (mock-echo, non-stream) | включён |
| `llm_stream` | SSE `stream=true`, chunk-timeout 10s (кастомный клиент) | включён |
| `langfuse_ingest` | `POST https://langfuse.{domain}/api/public/traces` (Bearer `{LANGFUSE_PUBLIC_KEY}`; per-node override — `LOAD_ENDPOINT_LANGFUSE_INGEST`) | включён |
| `db` | pg **read/write** через PG wire protocol (stdlib socket+hmac, без драйверов/прокси): read_query (`SELECT count(*)`) / write_query (`INSERT INTO loadtest_metrics`) вес 1:1 | **optional** — выключен |
| `s3` | minio PUT/GET через HTTP API (SigV4 presigned, **без boto3**) | **optional** — выключен |

Плейсхолдеры: `{domain}` → node.yaml `domain` (пустой → host), `{host}` → node.host,
`{model}` → `model` сценария, `{ANY_VAR}` → env (например `{LANGFUSE_PUBLIC_KEY}` ←
`LOAD_LANGFUSE_PUBLIC_KEY` или `LANGFUSE_PUBLIC_KEY`; отсутствие → exit 4).

Optional-сценарии: включение `LOAD_SCENARIO_DB=1` / `LOAD_SCENARIO_S3=1` (+ ключи
`LT_S3_ACCESS_KEY`/`LT_S3_SECRET_KEY`/`LT_S3_BUCKET`/`LT_S3_OBJECT` для s3).

**db (PostgreSQL read/write, DevPlan 148):** endpoint `postgres:5432` — DNS-алиас
docker-сети `shared-db-net` (postgres/pgbouncer публикуются ТОЛЬКО в docker-сеть,
NO ports: directive). Env: `LT_PG_USER` (default `postgres`), `LT_PG_PASSWORD`,
`LT_PG_DB` (default `platform`), `LT_PG_TABLE` (default `loadtest_metrics`).
Прогон **только** с `LOAD_RUNNER=node` + `LOAD_NETWORK=shared-db-net` (контейнер
генератора входит в docker-сеть postgres — `docker run --network shared-db-net`;
локальный запуск с SSH-туннелем к docker-сети невозможен: предупреждается
logger.warning, но не блокируется). На старте каждого пользователя — идемпотентная
чистая таблица (`CREATE TABLE IF NOT EXISTS` + `DELETE FROM`), ошибки SQL/auth →
failure locust (error_rate). Статистика per-task: `read_query`/`write_query` отдельно
в отчёте (скорость записи vs чтения).

## 4. Режимы

| Режим | Длительность | Критерий вердикта | Применение |
|-------|-------------|--------------------|------------|
| `smoke` | 90s (мин) | 0 errors AND p95 < max_p95 → PASS | после деплоя/обновления |
| `regression` | 300s | p95 ≤ 1.5×prev_p95 AND error ≤ prev+2pp AND p95 < max_p95 | ежемесячно, сравнение по датам |
| `capacity` | шаг 60s, max_steps=8 | автостоп (error>5% \| p99>3s); max_rps = последний успешный шаг | поиск max нагрузки |

Capacity доступен для `web`, `s3`, `db` и `llm` (`capacity_start_rps` задан в SoT —
иначе exit 4). На тестовой ноде (`NODE=test-e2e`, `contexts[0].name: test`) — штатный
guard без `LOAD_ALLOW_PROD`; на production-ноде — только с осознанным
`LOAD_ALLOW_PROD=1` (exit 10 иначе).

Env-оверрайды: `LOAD_RPS` (target_rps; users масштабируются до rps×2),
`LOAD_DURATION` (длительность активного режима), `LOAD_RESULTS_DIR` (default
`load-results/`), `LOAD_PROMETHEUS_PORT` (default 9090), `LOAD_VERSION` (git-sha в
отчёте; default "unknown"), `LOAD_ENDPOINT_<SCENARIO>` (per-scenario override
endpoint — escape hatch для нод с нестандартной топологией, например
`LOAD_ENDPOINT_LANGFUSE_INGEST=https://n.test.local`; рендерится теми же
плейсхолдерами, что и SoT-endpoint), `LOAD_NETWORK` (docker-сеть контейнера
генератора; default — из SoT: `host` для web/s3, `shared-db-net` для db;
allowlist `host`\|`shared-db-net` — иное → exit 4).

**Guard-ы:**
- capacity на нетестовой ноде (нет `node.role: test` и `contexts[0].name != "test"`)
  без `LOAD_ALLOW_PROD=1` → **exit 10** до любой нагрузки;
- timeout-guard прогона = `run_time × 2 + 60s`; capacity суммарный =
  `max_steps × (step_duration + 30s) + 120s`;
- preflight: отсутствие locust → exit 1 с инструкцией `pip install -e ".[load]"`.

## 5. Saturation-секция (PromQL pull)

Post-run `query_range` в окне `[t0-60s, t1+60s]`, шаг 30s. Пул запросов — CPU/mem
контейнеров (cadvisor, label `name="nginx"` и т.д.), `nginx_rps`, `nginx_conns`,
`pg_backends`, `redis_ops`, `redis_clients`, `litellm_reqs`, `litellm_err`,
`load1`, `mem_avail`, `net_rx`. CPU-rate-метрики дополнительно дают `pct` (avg × 100 —
проценты одного ядра).

- Метрика вне discovery-набора ноды (`label/__name__/values`) → `missing_metrics` → **WARN** (экспортёр выключен);
- найдена, но <2 сэмплов за окно → `insufficient_metrics` → **WARN** (статистически недостоверно);
- Prometheus недоступен → **exit 1** (guard-таблица).

## 6. Baseline и regression

`core/loadtest/history/<node>/<scenario>/history.json` — компактные строки прогонов
(коммитится; полные отчёты — в gitignored `load-results/`). Поле `host` —
детекция пересоздания тестовой VPS (инвариант 9 платформы): смена host →
`baseline_reset` → вердикт PASS с пометкой «node recreated» (сравнение с другим
железом — мусор), НЕ FAIL. Previous = последний прогон **того же режима**
(smoke-90s vs regression-300s несравнимы). Пороги регрессии из SoT:
`baseline_delta_p95: 1.5` (×), `baseline_delta_error_pp: 2.0` (пп). Первый прогон →
PASS + пометка «first run». Регенерация истории — только через прогон.

**Проверка regression (AC2):** два последовательных прогона — второй PASS с delta≈0;
искусственный baseline (поднятый prev_p95) → FAIL, exit 1.

## 7. Remote-режим (LOAD_RUNNER=node)

Генератор выполняется в docker-контейнере **на ноде** (слабый канал dev-машины):

```bash
LOAD_RUNNER=node make load-test SCENARIO=web NODE=test-e2e MODE=smoke
```

Механика: rsync `core/loadtest/` → `/tmp/loadtest-<ts>/` (SSH через канон
`shared.ssh_opts`) → `docker run --rm --network <net> --cpus ${LOAD_CPUS:-2} -v
/tmp/loadtest-<ts>:/lt -w /lt ${LOAD_IMAGE:-locustio/locust:2.32.10} -f ... --headless`
→ rsync CSV обратно; PromQL-pull и отчёт — локально.

- Генератор **вне стека**: не compose-сервис, не observability-net (инвариант 3);
- `LOAD_IMAGE` — ghcr.io-зеркало/кэш при Docker Hub rate-limit (известная проблема
  платформы, StatusReport 045); `--cpus 2` — генератор не съедает хост под capacity;
- boto3 в locust-образе отсутствует — s3-сценарий через HTTP API minio (SigV4).
- **`--network` (DevPlan 148):** docker-сеть контейнера из `scenarios.yaml#network`
  (override — `LOAD_NETWORK`). `host` (default) — web/s3: эндпоинты сервисов ноды
  на host-сети. `shared-db-net` — db: PostgreSQL публикуется ТОЛЬКО в docker-сеть
  (NO ports: directive), контейнер входит в неё и достаёт `postgres:5432` по
  DNS-алиасу. Сеть для db:

```bash
LOAD_SCENARIO_DB=1 LOAD_RUNNER=node LOAD_NETWORK=shared-db-net \
LT_PG_USER=postgres LT_PG_PASSWORD=<secret> LT_PG_DB=platform \
make load-test SCENARIO=db NODE=test-e2e MODE=smoke
```

## 8. Mock-модель litellm (установка на тестовую ноду)

Сценарии llm/llm_stream требуют модель `mock-echo` (детерминизм AC6).
`core/modules/litellm/config/litellm-config.mock.yml` — отдельный конфиг
(НЕ policy.yaml — инвариант «providers: только DeepSeek»; НЕ litellm-config.test.yml):

```bash
# 1. Забрать конфиг с dev-машины на ноду (core/... в репо, копируется в /opt/platform/core)
scp core/modules/litellm/config/litellm-config.mock.yml root@<node>:/opt/platform/core/modules/litellm/config/

# 2. Заменить монтируемый конфиг litellm (на ноде)
ssh root@<node> "cp /opt/platform/core/modules/litellm/config/litellm-config.mock.yml \
  /opt/platform/core/modules/litellm/config/litellm-config.yml"

# 3. Перезапустить litellm
make restart MODULES=litellm
```

Верификация: первый smoke-прогон llm (mock-probe POST до генерации). Если версия
litellm на ноде отклоняет `openai/echo` — fallback `model: "echo"` в mock-конфиге
(фиксируется по фактическому ответу). Прод-конфиг не затрагивается; на проде
mock-модель отсутствует → ранний FAIL с сообщением (даже при `LOAD_ALLOW_PROD=1`).

## 9. Отчёт и интерпретация

`load-results/<node>/<scenario>/<mode>/<ts>/` (gitignored):
`report.json` (машиночитаемый), `report.md` (сводка в stdout), `junit.xml` (опция
`--junit` для CI). Ключевые поля: `verdict` (PASS/WARN/FAIL), `stats` (rps, p50/p95/p99,
error_rate), `duration_s` (t1−t0 прогона, s — «что сколько времени выполняется»),
`tasks` (per-task breakdown: `{name: {rps, p95, p99, error_rate}}` — для db отдельно
`read_query`/`write_query`, скорость записи vs чтения), `saturation` (avg/max/pct по
метрикам), `missing_metrics`/`insufficient_metrics` (WARN-причины), `baseline` (prev,
delta_p95, delta_error_pp, first_run, baseline_reset), `capacity_profile` (шаги).
`history.json` (smoke/regression) дополнительно хранит `duration_s` и `tasks` —
источник сводной статистики по волнам (web/s3/db × smoke/regression/capacity).

**Интерпретация:** PASS при нуле ошибок и p95 под порогом; WARN = PASS + диагностика
метрик (не блокирует, exit 0); FAIL = ошибки/пороги/регрессия (exit 1). Saturation
читается как пиковые значения за окно прогона (max) и средние (avg); pct CPU — доля
одного ядра (100% = ядро целиком).

## 10. Ограничения

- `db`-сценарий: PostgreSQL публикуется ТОЛЬКО в docker-сеть `shared-db-net`
  (NO ports: directive) → прогон **только** node-runner'ом (`LOAD_RUNNER=node` +
  `LOAD_NETWORK=shared-db-net`); локальный dev-запуск без SSH-туннеля к docker-сети
  невозможен (предупреждение, не блокирует). Transport — чистый stdlib PG wire
  protocol (`pgwire.py`): auth SCRAM-SHA-256 + md5 (по коду сервера), cleartext
  (код 3) отклоняется;
- s3: presigned SigV4 через stdlib — без boto3 (ограничение locust-образа);
- e2e-тест (`make test-node NODE=<test>`) требует деплоя nginx на ноде и locust
  в окружении; PromQL-pull в e2e отключается (`--skip-prometheus`) — saturation
  на ноде проверяется ручным AC1-прогоном.

## 11. Юнит-тесты

`tests/unit/test_loadtest_config.py` (парсинг/валидация/NODE-резолв/endpoint-override),
`test_loadtest_runner.py` (build locust-argv без rate-limit флага + env LT_TARGET_RPS +
helper `_rps_wait_time`, 146-m1),
`test_loadtest_prometheus_pull.py` (PromQL/discovery/insufficient),
`test_loadtest_report.py` (CSV/verdict/артефакты), `test_loadtest_baseline.py`
(history/host-reset/пороги), `test_loadtest_capacity.py` (детерминированная
симуляция), `tests/e2e/test_load_test.py` (smoke web на VPS, requires_node).
Запуск: `make test-summary TEST_FILE=tests/unit/test_loadtest_*.py`.
