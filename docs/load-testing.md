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

**Инварианты (DevPlan 146):**

1. **users ≠ rps.** Точный RPS задаёт `locust --max-rps`; `users` — размер пула
   (`users = rps × 2`, запас на latency ≤ 2s; для сценариев с latency > 2s пул
   увеличивается вручную в SoT). RPS = users/latency — пул `users=rps` при latency
   100ms дал бы ~10× целевой RPS.
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
| `langfuse_ingest` | `POST https://n.{domain}/api/public/traces` (Bearer `{LANGFUSE_PUBLIC_KEY}`) | включён |
| `db` | pg read через HTTP (нет нативного HTTP-пути) | **optional** — выключен |
| `s3` | minio PUT/GET через HTTP API (SigV4 presigned, **без boto3**) | **optional** — выключен |

Плейсхолдеры: `{domain}` → node.yaml `domain` (пустой → host), `{host}` → node.host,
`{model}` → `model` сценария, `{ANY_VAR}` → env (например `{LANGFUSE_PUBLIC_KEY}` ←
`LOAD_LANGFUSE_PUBLIC_KEY` или `LANGFUSE_PUBLIC_KEY`; отсутствие → exit 4).

Optional-сценарии: включение `LOAD_SCENARIO_DB=1` / `LOAD_SCENARIO_S3=1` (+ ключи
`LT_S3_ACCESS_KEY`/`LT_S3_SECRET_KEY`/`LT_S3_BUCKET`/`LT_S3_OBJECT` для s3).

## 4. Режимы

| Режим | Длительность | Критерий вердикта | Применение |
|-------|-------------|--------------------|------------|
| `smoke` | 90s (мин) | 0 errors AND p95 < max_p95 → PASS | после деплоя/обновления |
| `regression` | 300s | p95 ≤ 1.5×prev_p95 AND error ≤ prev+2pp AND p95 < max_p95 | ежемесячно, сравнение по датам |
| `capacity` | шаг 60s, max_steps=8 | автостоп (error>5% \| p99>3s); max_rps = последний успешный шаг | поиск max нагрузки |

Env-оверрайды: `LOAD_RPS` (target_rps; users масштабируются до rps×2),
`LOAD_DURATION` (длительность активного режима), `LOAD_RESULTS_DIR` (default
`load-results/`), `LOAD_PROMETHEUS_PORT` (default 9090), `LOAD_VERSION` (git-sha в
отчёте; default "unknown").

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
`shared.ssh_opts`) → `docker run --rm --network host --cpus ${LOAD_CPUS:-2} -v
/tmp/loadtest-<ts>:/lt -w /lt ${LOAD_IMAGE:-locustio/locust:2.32} -f ... --headless`
→ rsync CSV обратно; PromQL-pull и отчёт — локально.

- Генератор **вне стека**: не compose-сервис, не observability-net (инвариант 3);
- `LOAD_IMAGE` — ghcr.io-зеркало/кэш при Docker Hub rate-limit (известная проблема
  платформы, StatusReport 045); `--cpus 2` — генератор не съедает хост под capacity;
- boto3 в locust-образе отсутствует — s3-сценарий через HTTP API minio (SigV4).

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
error_rate), `saturation` (avg/max/pct по метрикам), `missing_metrics`/
`insufficient_metrics` (WARN-причины), `baseline` (prev, delta_p95, delta_error_pp,
first_run, baseline_reset), `capacity_profile` (шаги).

**Интерпретация:** PASS при нуле ошибок и p95 под порогом; WARN = PASS + диагностика
метрик (не блокирует, exit 0); FAIL = ошибки/пороги/регрессия (exit 1). Saturation
читается как пиковые значения за окно прогона (max) и средние (avg); pct CPU — доля
одного ядра (100% = ядро целиком).

## 10. Ограничения

- `db`-сценарий: PostgreSQL не имеет нативного HTTP — сценарий optional и выключен
  по умолчанию (включается при наличии HTTP-моста, endpoint — в SoT);
- s3: presigned SigV4 через stdlib — без boto3 (ограничение locust-образа);
- e2e-тест (`make test-node NODE=<test>`) требует деплоя nginx на ноде и locust
  в окружении; PromQL-pull в e2e отключается (`--skip-prometheus`) — saturation
  на ноде проверяется ручным AC1-прогоном.

## 11. Юнит-тесты

`tests/unit/test_loadtest_config.py` (парсинг/валидация/NODE-резолв),
`test_loadtest_prometheus_pull.py` (PromQL/discovery/insufficient),
`test_loadtest_report.py` (CSV/verdict/артефакты), `test_loadtest_baseline.py`
(history/host-reset/пороги), `test_loadtest_capacity.py` (детерминированная
симуляция), `tests/e2e/test_load_test.py` (smoke web на VPS, requires_node).
Запуск: `make test-summary TEST_FILE=tests/unit/test_loadtest_*.py`.
