$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Контекст и проблема: зачем нужны нагрузочные тесты] => G_CONTEXT
- GOAL [Цели и не-цели: что даёт система, что сознательно вне скоупа] => G_GOALS
- GOAL [Суперпозиция и решения: коллапсированные решения по 6 измерениям] => G_DECISIONS
- GOAL [Архитектурный обзор: компоненты, потоки данных, режимы] => G_ARCH
- GOAL [Критерии приёмки: как подтвердить завершённость] => G_ACCEPT
- GOAL [Риски и ограничения] => G_RISKS
**SECTION_USE_CASES:**
- USE_CASE [Оператор после деплоя запускает smoke-проверку] => SC_SMOKE
- USE_CASE [Оператор ежемесячно гоняет regression и сравнивает с прошлыми прогонами] => SC_REGRESSION
- USE_CASE [Оператор ищет максимальную нагрузку сервисов (capacity)] => SC_CAPACITY
- USE_CASE [Оператор после обновления системы проверяет насыщение всех подсистем] => SC_POST_UPDATE
$END_DOCUMENT_PLAN

$START_BRIEF
# 01-Brief — Load Testing Platform

$ARTIFACT_CONTRACT
PURPOSE:               Дать DevOps возможность регулярно (дни/месяцы/после обновлений) запускать реальные нагрузочные тесты сервисов платформы, измерять их производительность и определять максимальную нагрузку (точку насыщения) каждого сервиса.
DESCRIPTION:           Новая подсистема `core/loadtest/` + `core/internal/loadtest/`: Locust-сценарии (web, llm, llm-stream, langfuse-ingest, db, s3), три режима (smoke/regression/capacity), локальный и нодный (LOAD_RUNNER=node) генератор, post-run PromQL-анализ насыщения (cadvisor/pg/redis/nginx/clickhouse/litellm/node-exporter), baseline-хранилище history.json со сравнением прогонов по датам, make-таргет `make load-test`.
RATIONALE:             Платформа не имеет нагрузочных тестов; существующий стек Prometheus уже собирает все необходимые для анализа насыщения метрики (cadvisor, postgres/redis/nginx-экспортёры, встроенные /metrics litellm) — остаётся только генератор нагрузки и пост-обработка. Locust выбран по языковой политике (Python-first); hybrid-расположение генератора исключает конкуренцию за CPU ноды при capacity-замерах; safety-stop позволяет запускать capacity без дежурства.
ACCEPTANCE_CRITERIA:   (1) `make load-test SCENARIO=<s> NODE=<n> MODE=smoke` собирает отчёт без ошибок на тестовой VPS; (2) regression-прогон сравнивает p95/error с history.json и выдаёт вердикт FAIL при превышении 1.5× baseline; (3) capacity-режим находит точку насыщения (max RPS) и останавливается по safety-stop; (4) отчёт содержит RPS, p50/p95/p99, error rate + saturation-метрики всех сервисов; (5) все юнит-тесты и gates зелёные.
IMPLEMENTS:            U-NEW-146 (новая эксплуатационная подсистема); прецедент: tests/e2e/test_chaos_resilience.py (эксплуатационные тесты на test-VPS)
IMPACTS:               Makefile (новый таргет load-test), core/entrypoint-manifest.yaml (регистрация), core/AGENTS.md и root AGENTS.md (глоссарий), pyproject.toml (load-extra: locust), .gitignore (load-results/), tests/ (unit + e2e)
REQUIRES:              Locust ≥2.32 (pip, load-extra); доступ к тестовой/целевой ноде (SSH, как у test-node); Prometheus на ноде (уже есть); доступность эндпоинтов сценариев (nginx/litellm/langfuse); NodeSSHClient из tests/_conftest для e2e
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и проблема

Платформа — это стек из 20+ контейнеров: nginx (front), litellm (LLM-шлюз), langfuse (трассировка → postgres+clickhouse), minio (S3), status-page, hermes-agent, мониторинг. DevOps не имеет инструмента для ответа на вопросы:

- **Сколько RPS выдерживает litellm до деградации?**
- **Не деградировал ли langfuse-ingest после обновления Postgres?**
- **Какая нагрузка на nginx является предельной для текущей конфигурации?**
- **Сравнима ли производительность сейчас с месяцем назад?**

Существующие инструменты: `e2e-verify` (HTTP+TLS sweep, 1 запрос на endpoint), `chaos-resilience` (отказоустойчивость, не нагрузка), Prometheus+Grafana (пассивный мониторинг). **Нагрузочного инструмента нет.**

Ключевой факт: Prometheus уже собирает всю телеметрию для анализа насыщения — cadvisor (CPU/mem per container), postgres-exporter (connections), redis-exporter (ops), nginx-exporter (connections), clickhouse, node-exporter (system load), встроенные `litellm /metrics`. Система нагрузочных тестов должна **генерировать** нагрузку и **интерпретировать** уже существующие метрики, а не плодить новых экспортёров.

## 2. Цели и не-цели

### Цели
1. **Реальные нагрузочные сценарии** по бизнес-критичным путям: web (nginx), llm (chat/completions), llm-stream (SSE), langfuse-ingest (traces → postgres+clickhouse), db (pg через pgbouncer), s3 (minio).
2. **Три режима**: smoke (после деплоя), regression (сравнение с baseline по датам), capacity (поиск максимальной нагрузки с автостопом).
3. **DevOps-метрики**: RPS, p50/p95/p99, error rate + saturation per service (CPU/mem контейнеров, connections pool, ops/s, nginx backlog, system load).
4. **История и сравнение**: baseline-хранилище `load-results/<node>/<scenario>/history.json`, вердикт с дельтой против предыдущего прогона.
5. **Простой запуск**: `make load-test SCENARIO=<s> NODE=<n> MODE=<m>`.

### Не-цели (вне скоупа)
- Нагрузочные тесты **проектов** (payload-проекты платформы) — только сервисы платформы.
- Тест **производительности load-runner** (генератор не бенчмаркается).
- Нагрузка на **внешние LLM-провайдеров** (все LLM-сценарии — через mock-модель litellm, детерминизм).
- Постоянный load-testing в CI (прогон = ручной/плановый эксплуатационный акт).
- Grafana-дашборд реального времени для прогонов (pushgateway) — вне скоупа, см. риски.

## 3. Суперпозиция и решения (коллапсировано)

| # | Измерение | Решение | Обоснование |
|---|-----------|---------|-------------|
| D1 | Инструмент | **Locust** (Python) | Языковая политика платформы (Python-first), готовые метрики RPS/latency/errors, сценарии с произвольными HTTP-путями (LLM body, SSE), Docker-образ для нодного режима |
| D2 | Расположение генератора | **Гибрид**: default локально (реальный сетевой путь через nginx, 0 конкуренции за CPU ноды); `LOAD_RUNNER=node` — контейнер locust на ноде | Канал оператора может быть узким местом для capacity → нодный режим как опция |
| D3 | Сценарии | **Полный набор**: web, llm, llm-stream, langfuse-ingest (обязательные) + db, s3 (опциональные) | Каждый сервис получает свой профиль и свою точку насыщения |
| D4 | Метрики | **Locust-метрики + post-run PromQL pull** (cadvisor/pg/redis/nginx/clickhouse/litellm/node) → report.json + history.json + markdown | Вся saturation-телеметрия уже собирается Prometheus; нулевая новая инфраструктура экспорта |
| D5 | Режимы | **smoke + regression + capacity** (ступенчатый ramp с safety-stop: error>5% или p99>порог → автостоп) | Покрывает все сценарии использования без дежурства оператора |
| D6 | Оркестрация | `make load-test` → makefiles/loadtest.mk → core/internal/loadtest (Python) + регистрация в entrypoint-manifest + глоссарий | Канон платформы: Makefile-фасад, Python-бизнес-логика |

## 4. Архитектурный обзор

```
┌─ Operator/CI ─────────────────────────────────────────────────────┐
│ make load-test SCENARIO=llm NODE=<n> MODE=capacity                 │
│   └─ makefiles/loadtest.mk → python3 -m core.internal.loadtest.    │
│        runner_cli --scenario llm --node <n> --mode capacity        │
└───────────────────────────────┬───────────────────────────────────┘
                                │
┌─ core/internal/loadtest (Python, LDD) ─────────────────────────────┐
│ config.py      — scenarios.yaml SoT + env (NODE/SCENARIO/MODE)     │
│ runner_cli.py  — оркестрация: locust headless, режимы, exit code   │
│ capacity.py    — ступенчатый ramp + safety-stop (MODE=capacity)    │
│ runner_remote.py — LOAD_RUNNER=node: scp сценариев + docker run    │
│ prometheus_pull.py — PromQL range-запросы post-run (saturation)    │
│ report.py      — report.json + markdown + вердикт                  │
│ baseline.py    — history.json, сравнение с предыдущим прогоном     │
└───────────────┬───────────────────────────────┬────────────────────┘
                │                               │
┌─ core/loadtest/scenarios ───────────┐   ┌─ Нода (target) ────────────┐
│ scenarios.yaml (SoT: эндпоинты,     │   │ nginx → litellm/langfuse/  │
│  RPS, пороги)                       │   │  postgres+pgbouncer/       │
│ web.py llm.py llm_stream.py         │   │  clickhouse/minio/...      │
│ langfuse_ingest.py db.py s3.py      │   │ Prometheus (scrape 30s)    │
└─────────────────────────────────────┘   └────────────────────────────┘
```

**Поток прогона (все режимы):**
1. `config.py` загружает сценарий (эндпоинт, тело, заголовки, target RPS, пороги) и резолвит NODE через `shared.node_resolver`.
2. `runner_cli.py` собирает и запускает locust headless (`--run-time`, `-u users`, `-r spawn-rate`, `--csv`), локально или через `runner_remote.py`.
3. По завершении: `prometheus_pull.py` снимает range-запросы из Prometheus ноды за окно прогона (CPU/mem контейнеров, pg/redis/nginx/clickhouse метрики, system load).
4. `report.py` формирует report.json + markdown-сводку + вердикт (PASS/WARN/FAIL).
5. `baseline.py` пишет строку в history.json и сравнивает с предыдущим прогоном (регрессия: p95 > 1.5× baseline или error > baseline+2pp → FAIL).

**Режимы:**
- `smoke`: 30s фикс-RPS (10), users=5, критерий: 0 errors, p95 < max_p95 сценария. Для проверки после деплоя.
- `regression`: 300s фикс-RPS (per scenario), сравнение с history.json.
- `capacity`: старт с start_rps, шаги ×2, стабилизация 60s на шаге; safety-stop: error_rate > 5% или p99 > max_p99 → стоп; max RPS = последний успешный шаг.

**Saturation-метрики отчёта (девопс):**
| Группа | Метрика (PromQL) | Что показывает |
|--------|------------------|-----------------|
| Per-container | `rate(container_cpu_usage_seconds_total{name=...}[5m])`, `container_memory_working_set_bytes{name=...}` | CPU/mem каждого сервиса под нагрузкой |
| nginx | `rate(nginx_http_requests_total[5m])`, `nginx_connections_active` | RPS на фронте, backlog |
| postgres | `pg_stat_database_numbackends`, `pg_stat_activity_count` | Соединения vs max_connections |
| redis | `rate(redis_commands_processed_total[5m])`, `redis_connected_clients` | Ops/s, клиенты |
| clickhouse | метрики job `clickhouse` | QPS/inserts |
| litellm | встроенные `litellm_proxy_*` (/metrics) | Requests, latency, tokens |
| system | `node_load1/5/15`, `node_memory_MemAvailable_bytes`, `rate(node_network_receive_bytes_total[5m])` | Saturation хоста |

Точные имена метрик уточняются на W2 по фактическому ответу Prometheus (`/api/v1/label/__name__/values` — первый прогон снимает доступные).

## 5. Критерии приёмки (детализация)

1. **Smoke**: `make load-test SCENARIO=web NODE=<test> MODE=smoke` — exit 0, report.json содержит RPS/latency/error + saturation-секцию, 0 ошибок.
2. **Regression**: второй прогон того же сценария с искусственно повышенным p95 baseline → вердикт FAIL с дельтой; при неизменной системе → PASS.
3. **Capacity**: на тестовой ноде сценарий llm (mock) находит max RPS (насыщение по error>5% или p99>порога) и останавливается автостопом; отчёт содержит профиль шагов.
4. **Remote**: `LOAD_RUNNER=node` запускает контейнер на ноде, результаты возвращаются локально.
5. **Gate-чистота**: `make check` зелёный; новые юнит-тесты проходят; e2e smoke-тест (requires_node) проходит на тестовой VPS.
6. **Документация**: docs/load-testing.md описывает запуск, режимы, метрики и интерпретацию.

## 6. Риски и ограничения

| Риск | Митигация |
|------|-----------|
| Канал оператора — узкое место capacity (локальный режим) | Нодный режим LOAD_RUNNER=node; отчёт помечает подозрение на bottleneck канала (сравнение сетевых метрик с лимитами) |
| Mock-модель litellm не детерминирована (конфиг ещё без mock) | W1 добавляет mock-модель в litellm-config.test.yml (echo, фикс latency) — детерминизм прогонов |
| PromQL-имена метрик отличаются от ожиданий | W2: динамический discovery имён (`label/__name__/values`), fail-ранний с диагностикой |
| Capacity-тест на prod-ноде — риск деградации | Режим capacity разрешён только с флагом `LOAD_ALLOW_PROD=1`; по умолчанию target — тестовая нода |
| Locust — новая зависимость | Только `[project.optional-dependencies] load` (не runtime-ядро), версия зафиксирована |
| history.json рассинхронизируется между машинами | Коммитится в репо (как status-metrics); полные report.json — в gitignored load-results/ |

$END_BRIEF
