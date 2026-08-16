# GREP_SUMMARY: platform-ports, shared, grafana, prometheus, langfuse, litellm, minio, clickhouse, pgbouncer, redis, status-page, hermes, port-registry, constants, platform-infra-parity
# STRUCTURE: ▶ ┌SoT: core/platform-infra.yaml (provides + env_defaults + docker-compose container ports)┐ → ⊕ реестр int-констант → ◇ parity-гейт test_gate_port_parity (сверка) → ⎋ import targets
# region MODULE_CONTRACT
## @purpose  Единый реестр внутренних (container) портов сервисов платформы (DevPlan 170 W1-A3).
##           Единственный источник числовых значений портов для docker-DNS URL внутри core/internal
##           (http://⟨service⟩:⟨port⟩). Литералы {3000, 4000, 8123, 6432, 6379, 8080, 9090, 9119}
##           в URL-константах домена core/internal заменяются импортом отсюда
##           (гейт tests/gates/test_gate_port_parity.py enforce-ит parity с SoT).
## @scope    Все Python-модули core/internal, строящие внутренние URL сервисов платформы.
##           Константы импортируются напрямую: `from core.internal.shared.platform_ports import ...`.
##           core/modules НЕ импортируют этот модуль (cross-layer запрет) — локальные константы
##           с TRAP-комментарием (см. status-page/app.py, hermes-agent).
## @invariants
##   1. Значения = зеркало SoT core/platform-infra.yaml: provides.port (pgbouncer/redis/litellm/
##      minio/clickhouse), env_defaults (GRAFANA_PORT, PROMETHEUS_PORT, STATUS_PAGE_PORT,
##      HERMES_DASHBOARD_PORT) и container-порт docker-compose.base.yml. Сверяются parity-гейтом
##      test_gate_port_parity — изменение порта в SoT без обновления константы = RED.
##   2. ⚠️ LANGFUSE=3000 — ВНУТРЕННИЙ (container) порт: docker-compose.base.yml биндит
##      `127.0.0.1:${LANGFUSE_PORT:-3001}:3000` (host 3001 → container 3000), NEXTAUTH_URL
##      "http://langfuse:3000". provides.langfuse.port=3001 — host-порт (внешний доступ с хоста),
##      НЕ используется во внутренних URL. Parity-гейт сверяет LANGFUSE с container-портом.
##   3. NGINX (443) — не включён: единственный публичный ingress, не фигурирует во внутренних
##      URL-константах домена (нет замен). Добавлять при появлении потребителя.
##   4. LOKI (3100) — status-page и monitoring используют loki:3100; константа не создаётся,
##      т.к. замена LOKI_RELOAD_URL не в скоупе W1-A3 (брифинг ограничил monitoring/constants.py:45,49).
##   5. Константы immutable (module-level int) — мутация запрещена (ревью-стандарт).
## @rationale DevPlan 170 W1-A3 (research-D §D1): 7 RED-дублей портов (status-page, monitoring,
##            key_provisioner, context_deployer, hermes-agent ×2, prometheus_tsdb) без единого
##            реестра → порт-дубли дрейфуют от SoT. Единый реестр + parity-гейт делают значения
##            grepable и enforce-емыми (паттерн shared/timeouts.py, DevPlan 116 B5 T1).
##            Семантика: константы = container-порты (docker-DNS), не host-порты provides —
##            внутренние URL сервисов строятся по именам контейнеров в docker-сетях.
## @changes  2026-08-14 | DevPlan 170 W1-A3 — Created (реестр портов, зеркало platform-infra.yaml)
## @see      core/platform-infra.yaml (SoT), tests/gates/test_gate_port_parity.py (parity-гейт)
# endregion MODULE_CONTRACT

# ── Monitoring / Observability ─────────────────────────────────────────────

# Grafana web UI (env_defaults.GRAFANA_PORT=3000, compose container 3000)
PLATFORM_PORT_GRAFANA: int = 3000

# Prometheus metrics (env_defaults.PROMETHEUS_PORT=9090, compose container 9090)
PLATFORM_PORT_PROMETHEUS: int = 9090

# ── Hermes-agent / LLM ─────────────────────────────────────────────────────

# Langfuse tracing (container-порт 3000: compose "127.0.0.1:${LANGFUSE_PORT:-3001}:3000",
# NEXTAUTH_URL "http://langfuse:3000"; provides.langfuse.port=3001 — host-порт, см. инвариант 2)
PLATFORM_PORT_LANGFUSE: int = 3000

# LiteLLM proxy (provides.litellm.port=4000, compose container 4000)
PLATFORM_PORT_LITELLM: int = 4000

# Hermes dashboard (env_defaults.HERMES_DASHBOARD_PORT=9119, compose container 9119)
PLATFORM_PORT_HERMES: int = 9119

# ── Shared data services ───────────────────────────────────────────────────

# MinIO S3 (provides.minio.port=9000, compose container 9000)
PLATFORM_PORT_MINIO: int = 9000

# ClickHouse HTTP (provides.clickhouse.port=8123, compose container 8123)
PLATFORM_PORT_CLICKHOUSE: int = 8123

# PgBouncer (provides.postgres.port=6432 — единый PostgreSQL-фасад)
PLATFORM_PORT_PGBOUNCER: int = 6432

# Redis cache (provides.redis.port=6379)
PLATFORM_PORT_REDIS: int = 6379

# Status-page (env_defaults.STATUS_PAGE_PORT=8080, внутренний порт контейнера)
PLATFORM_PORT_STATUS_PAGE: int = 8080
