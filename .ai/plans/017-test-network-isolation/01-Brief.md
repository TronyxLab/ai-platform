<!-- GREP_SUMMARY: Brief, test-network-isolation, dns-alias, shared-net, external-net, test-overlay, f-7, container-name-isolation, superposition-open -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Background → ◇ Symptom (non-deterministic smoke failures) → ◇ Root cause (alias on external shared nets → DNS 2 IP) → ◇ Precedent fix (langfuse-redis, commit 46f9277) → ◇ Scope (providers graph + consumers graph) → ◇ Superposition: test-only network vs per-service !override → ◇ Operational constraint → ⎋ Acceptance Criteria -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Бриф архитектурного долга «Изоляция DNS-alias тестового контура на внешних shared-сетях» — фиксация диагноза и прецедента фикса (langfuse-redis, commit 46f9277) для Architect-решения: как масштабировать изоляцию на все провайдеры shared-сетей (pgbouncer, postgres, clickhouse, minio, loki) без повторения langfuse-баги.
- **DESCRIPTION:** При живом прод-стеке smoke-модули падают недетерминированно (langfuse: «Can't reach database server at pgbouncer:6432», litellm: httpx.ConnectError на старте; плавающий неудачник между прогонами). Root cause: провайдеры (pgbouncer, postgres, clickhouse, minio, loki) объявляют aliases на внешних shared-сетях в docker-compose.base.yml; test-оверлеи не оверрайдят networks → alias резолвится в 2 IP (прод + тест), DNS round-robin. Прецедент фикса: langfuse-redis (commit 46f9277) — networks !override без alias + env-override REDIS_CONNECTION_STRING. Scope решения: для каждого провайдера — networks !override без alias в test.yml; для каждого потребителя — env-override connection strings на -test hostnames; полный граф рёбер потребитель→провайдер тест-контура.
- **RATIONALE:** Проблема воспроизведена (см. commit 46f9277 langfuse-redis fix) и клинически зафиксирована. Полный граф затронутых сервисов требует архитектурного решения: (A) per-provider !override + env-override chains, (B) отдельная test-shared-* сеть, (C) split-test-overlay compose с полной изоляцией. Без решения smoke-тесты не работают при живом прод-стеке (родной дизайн фикстуры).
- **ACCEPTANCE_CRITERIA:** (1) ни один DNS alias на shared-внешней сети не дублируется между prod и test контейнерами; (2) test-контейнеры провайдеров не могут быть достигнуты prod-потребителями по DNS-имени; (3) env-override connection strings на -test hostnames везде, где применимо; (4) gate-тест test_smoke_test_isolation::test_all_base_container_names_have_test_override не пропускает новые непокрытые сервисы; (5) решение не раздувает test.yml за пределы разумного.
- **IMPLEMENTS:** skill `superposition` (FULL mode, collapse OPEN), протокол `dev-pipeline` (Brief → Architect → Coder → QA)
- **IMPACTS:** `core/modules/*/docker-compose.test.yml` — сети каждого провайдера, env-override connection strings; `tests/_conftest/smoke.py` — SMOKE_ENV тестовые хосты; `tests/test_smoke_test_isolation.py` — gate контракт; `platform-env.yaml` — возможно test-specific override defaults.
- **REQUIRES:** `AGENTS.md` (root — инварианты), `core/modules/AGENTS.md` (docker-compose.test.yml контракт), `core/modules/langfuse/docker-compose.test.yml` (прецедент), commit 46f9277, `tests/test_smoke_test_isolation.py`

$START_BRIEF

# Brief: DNS-alias изоляция тестового контура на внешних shared-сетях

## Background

| Примитив | Что делает | Ограничение |
|---|---|---|
| `test_all_base_container_names_have_test_override` (gate) | Проверяет что у каждого сервиса с `container_name` в base.yml есть `-test` оверрайд в test.yml | Не проверяет сети и DNS alias |
| `docker-compose.test.yml` langfuse-redis | networks: !override без aliases (commit 46f9277) | Прецедент фикса, только langfuse |
| SMOKE_ENV | Тестовые env-переменные для shifted портов | Нет тестовых hostname для shared-сетей |

**Разрыв:** нет системного решения для DNS-alias изоляции. Каждый провайдер на внешней shared-сети (shared-db-net, shared-cache-net, observability-net, backup-net, proxy-net) объявляет aliases в base.yml. Без networks: !override эти aliases дублируются для test-контейнера → DNS round-robin между prod и test.

## Symptom

При живом прод-стеке smoke-тесты (platform_services fixture) падают недетерминированно:

| Симптом | Конкретный кейс | Частота |
|---|---|---|
| langfuse: «Can't reach database server at pgbouncer:6432» | langfuse-test подключается к shared-db-net → pgbouncer alias резолвится в 2 IP → случайный выбор → может попасть на prod pgbouncer (который не знает тестовую БД) | Плавающая, ~50% |
| litellm: httpx.ConnectError на старте | litellm-test на shared-db-net → pgbouncer alias → может уйти на недоступный контейнер | Плавающая |
| minio: запросы к langfuse-redis уходят на prod | langfuse-redis alias на shared-db-net → DNS round-robin между prod и test | Плавающая |
| Loki: promtail-test отправляет логи в loki (прод) | Наблюдалось при параллельных smoke-прогонах | Недетерминированно |

## Root cause

```
docker-compose.base.yml:
  langfuse-redis:
    container_name: langfuse-redis
    networks:
      shared-db-net:
        aliases:
          - langfuse-redis    # ← alias на ВНЕШНЕЙ сети

docker-compose.test.yml (ДО фикса):
  langfuse-redis:           # ← отсутствовал!
  # → compose merge: test не оверрайдит networks
  # → тестовый langfuse-redis входит в shared-db-net с тем же alias
  # → DNS shared-db-net имеет 2 IP для "langfuse-redis"
  # → prod langfuse (если жив) может подключиться к test redis и наоборот
```

**Аналогичный паттерн** применим ко ВСЕМ провайдерам:

| Провайдер | Сеть | Alias(es) в base.yml | Test override? |
|---|---|---|---|
| pgbouncer | shared-db-net | pgbouncer | НЕТ |
| postgres | shared-db-net | postgres | НЕТ |
| clickhouse | shared-db-net | clickhouse | НЕТ |
| minio | backup-net, shared-db-net | minio | НЕТ |
| loki | observability-net | loki | НЕТ |

**Потребители этих провайдеров** (litellm, langfuse, hermes-agent, langfuse-redis, postgres-exporter, redis-exporter, nginx-prometheus-exporter, nginx, prometheus, grafana и др.) подключаются через connection strings вида `redis://langfuse-redis:6379`, `postgresql://user@pgbouncer:6432/...`, `http://minio:9000` — где hostname = alias на shared-сети.

## Прецедент фикса: langfuse-redis (commit 46f9277)

```yaml
# core/modules/langfuse/docker-compose.test.yml (AFTER fix)
  langfuse-redis:
    container_name: langfuse-redis-test
    restart: unless-stopped
    networks: !override
      - shared-db-net
    # Без aliases → DNS имя = container_name = langfuse-redis-test
    # Не конфликтует с prod alias langfuse-redis

  langfuse:
    environment:
      REDIS_CONNECTION_STRING: "redis://langfuse-redis-test:6379"
      # ↑ Переопределение connection string на test hostname
```

**Три шага фикса:**
1. `container_name` → `-test` суффикс (уже покрыто gate)
2. `networks: !override` без aliases — убирает DNS-дублирование
3. env-override connection strings — потребитель ходит на test hostname

## Scope решения

### Провайдеры (источники alias-коллизий)

| Модуль | Провайдер | Сеть | Alias | test needs networks !override? | Deprecated for test? |
|---|---|---|---|---|---|
| postgres | postgres | shared-db-net | postgres | ДА | — |
| postgres | pgbouncer | shared-db-net | pgbouncer | ДА | — |
| clickhouse | clickhouse | shared-db-net | clickhouse | ДА | — |
| minio | minio | backup-net, shared-db-net | minio | ДА | backup-net отвалится (minio-test не создаётся) |
| logging | loki | observability-net | loki | ДА | — |
| redis | redis | shared-cache-net | redis | ДА | — |
| infra-metrics | redis-exporter | shared-cache-net | redis-exporter | ДА | — |
| infra-metrics | postgres-exporter | shared-db-net | postgres-exporter | ДА | — |
| infra-metrics | nginx-prometheus-exporter | observability-net | nginx-prometheus-exporter | ДА | — |

### Потребители (env-override connection strings)

Test-контейнеры, которые подключаются к провайдерам через DNS alias:

| Test-контейнер | Connection string | Провайдер | Нужен env-override? |
|---|---|---|---|
| langfuse-test | `LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT` → minio:9000 | minio | ДА |
| langfuse-test | `DATABASE_URL` → pgbouncer:6432 | pgbouncer | ДА |
| langfuse-test | `REDIS_CONNECTION_STRING` → langfuse-redis:6379 | langfuse-redis | ✅ FIXED |
| langfuse-test | `CLICKHOUSE_URL` → clickhouse:8123 | clickhouse | ДА |
| litellm-test | `DATABASE_URL` → pgbouncer:6432 | pgbouncer | ДА |
| hermes-agent-test | `OPENAI_BASE_URL` → litellm:4000 | litellm | ДА |
| hermes-agent-test | `POSTGRES_HOST` → pgbouncer | pgbouncer | ДА |
| hermes-agent-test | `REDIS_HOST` → redis | redis | ДА |
| grafana-test | datasources → prometheus:9090, loki:3100 | prometheus, loki | ДА |
| prometheus-test | scrape_configs → node-exporter:9100, etc. | infra-metrics | ДА |
| promtail-test | loki:3100 | loki | ДА |

## SUPERPOSITION: варианты решения (коллапс OPEN)

### Option A: Per-provider networks !override + env-override chains [score: 7/10] ⭐ pragmatic
- **Approach:** Для каждого провайдера в графе — `networks: !override` без aliases в test.yml. Для каждого уникального consumer → env-override connection string на `-test` hostname. Connection strings для Grafana datasources и Prometheus scrape_configs — через env-шаблонизацию или script СMD.
- **Trade-offs:** Максимум точности, каждая связь обрабатывается явно; но взрывной рост test.yml (правки в 10+ файлов), ручное отслеживание графа, неполнота (всегда можно забыть один consumer).
- **Best when:** нужна бесшовная интеграция с существующим compose merge, без новой архитектурной концепции.

### Option B: Test-only networks — вся изоляция на уровне compose [score: 8/10] ⭐ recommended
- **Approach:** Ввести тестовые копии external-сетей (test-shared-db-net, test-shared-cache-net, test-observability-net). Все test.yml сервисов переключаются на них. Провайдеры — каждая тестовая сеть получает свой экземпляр провайдера (если модуль не в тесте — провайдера нет). Потребители — connection strings не меняются (сеть своя, внутри сети alias резолвится в свой контейнер).
- **Trade-offs:** Полная изоляция без env-override (DNS-имена остаются теми же, сеть другая); +1 операция `docker network create` на каждую тестовую сеть; compose merge не поддерживает rename сетей — нужно !override networks во всех сервисах test.yml; но это однотипные 3-5 строчек, а не десятки env-override.
- **Best when:** решение масштабируется на N провайдеров линейно (O(N) правок), а не квадратично (O(N×M) рёбер).
- **Rev:** если compose file merging делает !override для networks невозможным (проверить эмпирически) — откат к Option A.

### Option C: Split test compose — независимый файл без merge [score: 5/10]
- **Approach:** Вместо `-f base -f test` — отдельный `docker-compose.test-module.yml` для каждого модуля, содержащий полную копию сервиса с network изоляцией, без merge с base.yml.
- **Trade-offs:** Полный контроль; но дублирование (каждый test.yml копирует 30-50 строк из base), ломает convention `base + test`; gate-тесты на merge консистентность перестают работать.
- **Best when:** когда compose merge становится sources of non-determinism (крайний случай).

### Recommendation: **Option B** (test-only networks) — решает root cause на уровне изоляции без каскада env-override. Collapse — за Architect.

## Operational constraint (родной дизайн фикстуры)

**ДО реализации полной изоляции:** smoke-тесты гоняются только при остановленном прод-стеке. `platform_services` fixture уже выполняет `global pre-cleanup` (docker compose down всех модулей перед стартом, см. smoke.py:584-598). Текущее ограничение: after global cleanup by smokes, если какой-то модуль не может быть остановлен (например, ручной compose up вне lifecycle), разделение alias на shared-сетях не гарантируется.

Это НЕ является дефектом — дизайн фикстуры предполагает isolated test environment. Проблема проявляется только при нарушении pre-cleanup (например, ручной запуск контейнера без `-p ai-platform-test`).

## Acceptance Criteria (для DevPlan-решения)

1. Ни один DNS alias на shared-внешней сети не дублируется между prod и test контейнерами.
2. Test-контейнеры провайдеров НЕ резолвятся по prod alias-имени (отсутствие DNS-загрязнения).
3. Prod-контейнеры НЕ резолвятся по test container_name (изоляция обратная).
4. Тестовые сети создаются в platform_services/фикстурах перед compose up, удаляются при teardown.
5. Gate-тест `test_all_base_container_names_have_test_override` расширен для проверки network alias изоляции.
6. Все тестовые connection strings (DATABASE_URL, REDIS_CONNECTION_STRING, S3_ENDPOINT и т.д.) — или не меняются (Option B), или явно переопределены (Option A).
7. Решение не требует ручного отслеживания графа провайдер-потребитель при добавлении нового модуля.

## Open Questions (для коллапса)

1. **Q1 (главный):** Вариант изоляции — Option B (test-only external networks) или Option A (per-provider !override + env-override chains)?
2. **Q2:** Если Option B — как назвать тестовые сети? `test-shared-db-net` или `shared-db-net-test`?
3. **Q3:** test.yml сервисов, которые не являются провайдерами (чистые потребители — grafana, prometheus, hermes-agent) — им нужен networks !override или достаточно networks раздела с test-сетью?
4. **Q4:** Minio на 2 сетях (backup-net + shared-db-net) — backup-net не имеет alias-specific проблем (minio alias только для самой minio), но test minio не должен участвовать в backup-net. !override уберёт обе сети → нужно явно указать shared-db-net (test).

$END_BRIEF
