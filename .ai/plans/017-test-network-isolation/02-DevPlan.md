<!-- GREP_SUMMARY: DevPlan, test-network-isolation, Option-B, test-only-networks, dns-alias, network-isolation, wave-parallel -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Decisions (collapse) → ◇ $TASKS (4 waves) → ◇ $PARALLEL_GROUPS → ◇ $TEST_SPEC → ◇ $RISKS → ⎋ Completion -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** DevPlan реализации Option B: test-only external networks для изоляции DNS-alias тестового контура. Fixed-scope: 5 test-сетей, 12 модулей, 0 новых env-override.
- **DESCRIPTION:** Создание 5 test-* копий external-сетей (test-shared-db-net, test-shared-cache-net, test-observability-net, test-proxy-net, test-hermes-agent-net). Все test.yml получают `networks: !override` без aliases → каждый сервис жёстко привязан к test-сети. DNS-имена (pgbouncer, redis, clickhouse, etc.) сохраняются — изоляция на уровне сети, не на уровне имён. O(N) правок (по одной на сервис), не O(N×M).
- **RATIONALE:** Brief-рекомендация Option B (score 8/10). Коллапс суперпозиции через user interview: Option B, naming prefix test-*, !override для всех сервисов единообразно, minio только test-shared-db-net.
- **ACCEPTANCE_CRITERIA:**
  1. 5 test-* external сетей создаются перед compose up, удаляются на teardown
  2. Все test.yml сервисов получают `networks: !override` с test-* эквивалентами prod-сетей
  3. Ни один alias не дублируется между prod и test (DNS-изоляция)
  4. Gate-тест `test_all_base_container_names_have_test_override` расширен для проверки network изоляции
  5. Существующий langfuse-redis REDIS_CONNECTION_STRING env-override УДАЛЁН (не нужен при сетевой изоляции)
  6. `make gate MODE=fast` зелёный
- **IMPLEMENTS:** Brief 017-test-network-isolation, skill `superposition` (FULL→collapsed), протокол `dev-pipeline`
- **IMPACTS:** `platform-env.yaml`, `tests/_conftest/{networks,smoke}.py`, 12 `docker-compose.test.yml`, `tests/test_smoke_test_isolation.py`
- **REQUIRES:** `AGENTS.md` (root), `core/modules/AGENTS.md` (test.yml contract), commit 46f9277 (precedent)

$START_DEVPLAN

# DevPlan: DNS-alias изоляция тестового контура (Option B)

## Decisions (коллапс суперпозиции)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | Вариант изоляции | **Option B** — test-only external networks |
| D2 | Именование сетей | **Префикс `test-`**: test-shared-db-net, test-shared-cache-net, etc. |
| D3 | !override для потребителей | **!override для всех** — единообразно, исключает случайное подключение к prod-сети |
| D4 | Minio dual-network | **Только test-shared-db-net** — backup-net не нужен для тестов |

## Scope

### Test networks (5 новых)

| Test-сеть | Prod-эквивалент | Провайдеры | Потребители |
|-----------|----------------|-----------|------------|
| `test-shared-db-net` | `shared-db-net` | postgres, pgbouncer, minio, postgres-exporter, langfuse-redis | langfuse, litellm, backup-cron |
| `test-shared-cache-net` | `shared-cache-net` | redis, redis-exporter | — |
| `test-observability-net` | `observability-net` | clickhouse, loki, nginx-prometheus-exporter | promtail, cadvisor, node-exporter, redis-exporter, postgres-exporter, langfuse, litellm, prometheus, grafana, hermes-agent |
| `test-proxy-net` | `proxy-net` | — | grafana, hermes-agent, nginx |
| `test-hermes-agent-net` | `hermes-agent-net` | — | litellm, hermes-agent |

### Модули (12)

Все Docker-модули с `docker-compose.test.yml`: postgres, clickhouse, minio, redis, logging, infra-metrics, langfuse, litellm, hermes-agent, monitoring, nginx, backup-cron.

---

## $TASKS

### TASK-W1: Infrastructure — test network lifecycle
**Files:** `platform-env.yaml`, `tests/_conftest/networks.py`, `tests/_conftest/smoke.py`
**Owner:** Coder

- [ ] W1.1 `platform-env.yaml` — добавить 5 test-* сетей в `networks:` секцию
- [ ] W1.2 `tests/_conftest/networks.py` — добавить константу `TEST_NETWORKS` (5 test-* сетей)
- [ ] W1.3 `tests/_conftest/smoke.py` — в `platform_services`: pre-create test-сети через `ensure_external_networks(TEST_NETWORKS)`, удалять на teardown

### TASK-W2: Provider modules — networks !override
**Files:** 6 `docker-compose.test.yml`
**Owner:** Coder
**Depends on:** W1 (тестовые сети должны быть определены до их использования в test.yml)

- [ ] W2.1 `core/modules/postgres/docker-compose.test.yml` — добавить `networks: !override [test-shared-db-net]` для postgres и pgbouncer
- [ ] W2.2 `core/modules/clickhouse/docker-compose.test.yml` — добавить `networks: !override [test-observability-net]` для clickhouse
- [ ] W2.3 `core/modules/minio/docker-compose.test.yml` — добавить `networks: !override [test-shared-db-net]` для minio (только test-shared-db-net, без backup-net)
- [ ] W2.4 `core/modules/redis/docker-compose.test.yml` — добавить `networks: !override [test-shared-cache-net]` для redis
- [ ] W2.5 `core/modules/logging/docker-compose.test.yml` — добавить `networks: !override [test-observability-net]` для loki и promtail
- [ ] W2.6 `core/modules/infra-metrics/docker-compose.test.yml` — добавить `networks: !override` для cadvisor `[test-observability-net]`, node-exporter `[test-observability-net]`, nginx-prometheus-exporter `[test-observability-net]`, redis-exporter `[test-shared-cache-net, test-observability-net]`, postgres-exporter `[test-shared-db-net, test-observability-net]`

### TASK-W3: Consumer modules — networks !override
**Files:** 6 `docker-compose.test.yml`
**Owner:** Coder
**Depends on:** W2 (провайдеры должны быть на test-сетях до потребителей — compose dependency ordering)

- [ ] W3.1 `core/modules/langfuse/docker-compose.test.yml` — langfuse: `networks: !override [test-shared-db-net, test-observability-net]`; langfuse-redis: изменить существующий `networks: !override [shared-db-net]` → `[test-shared-db-net]`; **удалить** REDIS_CONNECTION_STRING env-override (service name `langfuse-redis` на test-shared-db-net резолвится в test-контейнер)
- [ ] W3.2 `core/modules/litellm/docker-compose.test.yml` — litellm: `networks: !override [test-shared-db-net, test-observability-net, test-hermes-agent-net]`
- [ ] W3.3 `core/modules/hermes-agent/docker-compose.test.yml` — hermes-agent: `networks: !override [test-proxy-net, test-hermes-agent-net, test-observability-net]`
- [ ] W3.4 `core/modules/monitoring/docker-compose.test.yml` — prometheus: `networks: !override [test-observability-net]`; grafana: `networks: !override [test-observability-net, test-proxy-net]`
- [ ] W3.5 `core/modules/nginx/docker-compose.test.yml` — nginx: `networks: !override [test-proxy-net, test-observability-net]`
- [ ] W3.6 `core/modules/backup-cron/docker-compose.test.yml` — backup-cron: `networks: !override [test-shared-db-net]`

### TASK-W4: Gate test expansion + verification
**Files:** `tests/test_smoke_test_isolation.py`
**Owner:** Coder
**Depends on:** W2, W3 (gate тест должен проверять реальное состояние после всех правок)

- [ ] W4.1 Новый gate-тест `test_no_prod_network_in_test_overlay` — проверяет что ни один test.yml не ссылается на prod-сети (shared-db-net, shared-cache-net, observability-net, proxy-net, hermes-agent-net, backup-net)
- [ ] W4.2 Новый gate-тест `test_test_network_consistency` — для каждого сервиса в test.yml: если prod-сервис на сети X, test-сервис должен быть на test-X
- [ ] W4.3 Расширить `test_all_base_container_names_have_test_override` — добавить проверку что каждый сервис с container_name в base.yml имеет `networks: !override` в test.yml (R5 anti-survivorship)

---

## $PARALLEL_GROUPS

```
Wave 1: [W1]                          ← независимо, только инфраструктура
         │
Wave 2: [W2.1, W2.2, W2.3,           ← 6 provider test.yml, независимы друг от друга
         W2.4, W2.5, W2.6]             (разные модули, нет file-sharing)
         │
Wave 3: [W3.1, W3.2, W3.3,           ← 6 consumer test.yml, независимы друг от друга
         W3.4, W3.5, W3.6]             (разные модули, нет file-sharing)
         │
Wave 4: [W4.1, W4.2, W4.3]           ← gate-тесты, все в одном файле → один coder
```

**Правила параллельности:**
- W1 — соло (единственный changeset в smoke.py/networks.py/platform-env.yaml)
- W2 — 6 параллельных coder'ов (разные модули = разные файлы)
- W3 — 6 параллельных coder'ов (разные модули = разные файлы)
- W4 — соло (все изменения в test_smoke_test_isolation.py)

---

## $TEST_SPEC

### Pre-existing tests (must stay green)

| Тест | Маркер | Проверяет |
|------|--------|----------|
| `test_all_docker_modules_have_test_overlay` | gate | 12 Docker-модулей имеют test.yml |
| `test_all_test_containers_have_test_suffix` | gate | Все container_name → -test |
| `test_no_container_name_collision` | gate | Нет пересечений prod/test container_name |
| `test_all_base_container_names_have_test_override` | gate | Каждый base container_name имеет test-override |

### New tests (this DevPlan)

| Тест | Маркер | Проверяет |
|------|--------|----------|
| `test_no_prod_network_in_test_overlay` | gate | Ни один test.yml не ссылается на prod-сеть |
| `test_test_network_consistency` | gate | Каждый test-сервис на test-* эквиваленте prod-сети |
| `test_all_base_container_names_have_test_override` (расширен) | gate | + проверка наличия `networks: !override` для каждого test-сервиса |

### Verification command
```bash
make gate MODE=fast
```

---

## $RISKS

| Риск | Вероятность | Mitigation |
|------|------------|-----------|
| Compose merge конфликт: !override в test.yml + external:true декларация сети в test.yml (сеть не декларирована в test.yml, только в base.yml) | LOW | Test-сети pre-created через `docker network create`, compose видит их как external. В test.yml достаточно указать имя сети в `networks:` списке, без повторной декларации `networks:` top-level (compose merge подхватит из base.yml? Нет — `!override` заменяет весь список, а external декларация сети в base.yml остаётся. Проверить на первом же compose up.) |
| hermes-agent-test не может достичь pgbouncer-test (prod hermes-agent не на shared-db-net, а на proxy-net+hermes-agent-net+observability-net; test — аналогично) | MEDIUM | В prod hermes-agent использует `POSTGRES_HOST=pgbouncer` но не подключён к shared-db-net — связь идёт через Docker gateway (bridge network routing). В test то же самое — `pgbouncer` резолвится внутри test-shared-db-net, до которого hermes-agent-test НЕ подключён. **Нужен дополнительный анализ**: либо добавить test-shared-db-net к hermes-agent-test, либо выяснить как работает межсетевая маршрутизация в Docker. |
| litellm-test DATABASE_URL → `pgbouncer:6432` не резолвится после переезда на test-shared-db-net | LOW | Оба (litellm-test и pgbouncer-test) на одной сети → DNS резолвится. |
| Langfuse REDIS_CONNECTION_STRING удаление env-override ломает связь | LOW | Оба (langfuse-test и langfuse-redis-test) на test-shared-db-net, service name `langfuse-redis` резолвится в test-контейнер. |

---

## Diff Summary (expected changes per file)

### platform-env.yaml
```yaml
# +5 test networks after existing staging networks
  - name: test-shared-db-net
    driver: bridge
  - name: test-shared-cache-net
    driver: bridge
  - name: test-observability-net
    driver: bridge
  - name: test-proxy-net
    driver: bridge
  - name: test-hermes-agent-net
    driver: bridge
```

### tests/_conftest/networks.py
```python
# + TEST_NETWORKS constant
TEST_NETWORKS: set[str] = {
    "test-shared-db-net",
    "test-shared-cache-net",
    "test-observability-net",
    "test-proxy-net",
    "test-hermes-agent-net",
}
```

### tests/_conftest/smoke.py
```python
# В platform_services:
# После создания prod external networks добавить:
from _conftest.networks import TEST_NETWORKS
for net_name in sorted(TEST_NETWORKS):
    ensure_external_networks([net_name])

# В teardown — после удаления prod сетей добавить:
for net_name in sorted(TEST_NETWORKS):
    subprocess.run(["docker", "network", "rm", net_name], ...)
```

### Каждый docker-compose.test.yml (pattern)
```yaml
services:
  <service>:
    networks: !override
      - test-<original-network>
    # БЕЗ aliases — DNS = service name из compose
```

### tests/test_smoke_test_isolation.py
```python
# + test_no_prod_network_in_test_overlay
# + test_test_network_consistency
# Расширение test_all_base_container_names_have_test_override
```

$END_DEVPLAN
