# GREP_SUMMARY: platform-ports, shared, grafana, prometheus, langfuse, litellm, minio, clickhouse, pgbouncer, redis, status-page, hermes, node-exporter, cadvisor, exporters, loki, clickhouse-native, peer-port, port-registry, constants, platform-infra-parity
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
##   4. LOKI_HTTP (3100) — добавлен DevPlan 010 T2.2 (peer-публикация центрального приёма логов);
##      при W1-A3 константа не создавалась (замена LOKI_RELOAD_URL вне скоупа), T2.2 — новый скоуп.
##   5. Константы БЕЗ префикса PLATFORM_PORT_ (NODE_EXPORTER/CADVISOR/*_EXPORTER/LOKI_HTTP/
##      CLICKHOUSE_NATIVE_PEER, DevPlan 010 T2.2) — host/scrape-порты кросс-нодовой публикации,
##      ВНЕ parity-скоупа test_gate_port_parity (гейт сверяет только PLATFORM_PORT_* с SoT
##      provides/env_defaults/compose); compose-публикация и расширение гейта — задачи T2.2
##      других агентов. CLICKHOUSE_NATIVE_PEER — host-порт (container 9000 остаётся; прецедент
##      host≠container — langfuse 3001/3000, TRAP §3 DevPlan 010).
##   6. Константы immutable (module-level int) — мутация запрещена (ревью-стандарт).
## @rationale DevPlan 170 W1-A3 (research-D §D1): 7 RED-дублей портов (status-page, monitoring,
##            key_provisioner, context_deployer, hermes-agent ×2, prometheus_tsdb) без единого
##            реестра → порт-дубли дрейфуют от SoT. Единый реестр + parity-гейт делают значения
##            grepable и enforce-емыми (паттерн shared/timeouts.py, DevPlan 116 B5 T1).
##            Семантика: константы = container-порты (docker-DNS), не host-порты provides —
##            внутренние URL сервисов строятся по именам контейнеров в docker-сетях.
## @changes  2026-08-14 | DevPlan 170 W1-A3 — Created (реестр портов, зеркало platform-infra.yaml)
## @changes  2026-08-22 | DevPlan 010 T2.2 (prep) — +NODE_EXPORTER/CADVISOR/POSTGRES_EXPORTER/
##            REDIS_EXPORTER/NGINX_EXPORTER/LOKI_HTTP/CLICKHOUSE_NATIVE_PEER (peer/scrape-порты)
##            2026-08-24 | REF-0010 — +PGBOUNCER_EXPORTER/LANGFUSE_REDIS_EXPORTER/ALLOY_HTTP
##            (аддитивно, frozen leaf P3 п.1: существующие имена не тронуты)
## @see      core/platform-infra.yaml (SoT), tests/gates/test_gate_port_parity.py (parity-гейт)
# endregion MODULE_CONTRACT

# ── Monitoring / Observability ─────────────────────────────────────────────

# Grafana web UI (env_defaults.GRAFANA_PORT=3000, compose container 3000)
PLATFORM_PORT_GRAFANA: int = 3000

# Prometheus metrics (env_defaults.PROMETHEUS_PORT=9090, compose container 9090)
PLATFORM_PORT_PROMETHEUS: int = 9090

# ── Hermes-agent / LLM ─────────────────────────────────────────────────────

# Langfuse tracing (container-порт 3000: compose "127.0.0.1:${LANGFUSE_PORT:-3001}:3000",
# NEXTAUTH_URL "http://langfuse:3000"; provides.langfuse.port=3001 — host-порт (внешний доступ
# с хоста), НЕ используется во внутренних URL. Parity-гейт сверяет LANGFUSE с container-портом.
PLATFORM_PORT_LANGFUSE: int = 3000

# Langfuse HOST-порт (env_defaults.LANGFUSE_PORT=3001, compose host-side публикации;
# DevPlan 010 completion T2.2/T2.3 — peer-публикация фасада tracing'а кросс-нодово).
# Вне parity-скоупа (host-порт, инвариант 5 — как CLICKHOUSE_NATIVE_PEER).
LANGFUSE_HOST: int = 3001

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

# ── Node metrics / exporters (DevPlan 010 T2.2: peer-публикация scrape-портов) ─

# Node exporter host-metrics scrape (env_defaults.NODE_EXPORTER_PORT=9100, compose container 9100)
NODE_EXPORTER: int = 9100

# cAdvisor container-metrics scrape (env_defaults.CADVISOR_PORT=8080, compose container 8080;
# значение совпадает со STATUS_PAGE_PORT намеренно — порт внутренний, см. status-page base.yml:60)
CADVISOR: int = 8080

# Postgres exporter scrape (9187; сегодня host-порта НЕ имеет — публикация T2.2)
POSTGRES_EXPORTER: int = 9187

# Redis exporter scrape (9121; сегодня host-порта НЕ имеет — публикация T2.2)
REDIS_EXPORTER: int = 9121

# Nginx exporter scrape (env_defaults.NGINX_EXPORTER_PORT=9113, compose container 9113)
NGINX_EXPORTER: int = 9113

# ── REF-0010 (2026-08-24): monitoring-honesty exporters ─────────────────────

# PgBouncer exporter scrape (host=container 9127 — дефолт порта pgbouncer_exporter;
# вне parity-скоупа test_gate_port_parity, инвариант 5 — как POSTGRES_EXPORTER)
PGBOUNCER_EXPORTER: int = 9127

# Langfuse-redis exporter HOST-порт публикации (compose "${...:-9122}:9121"; container
# 9121 занят основным redis-exporter на общей ноде; host≠container — прецедент LANGFUSE
# 3001/3000, TRAP §3 DevPlan 010). Вне parity-скоупа (инвариант 5).
LANGFUSE_REDIS_EXPORTER: int = 9122

# Grafana Alloy metrics HTTP-server (дефолт --server.http.listen-address=:12345,
# log-collector compose порт не публикует — scrape по Docker DNS observability-net).
ALLOY_HTTP: int = 12345

# ── Logging / ClickHouse cross-node peer (DevPlan 010 T2.2) ─────────────────

# Loki push endpoint (env_defaults.LOKI_PORT=3100, compose container 3100; peer-публикация T2.2 —
# центральный приём логов всех нод)
LOKI_HTTP: int = 3100

# ClickHouse native cross-node peer (HOST-порт 19000: container остаётся 9000 — коллизия с minio API
# 9000 на общей data-ноде; прецедент host≠container — langfuse 3001/3000, TRAP §3 DevPlan 010)
CLICKHOUSE_NATIVE_PEER: int = 19000

# MinIO web-console (env_defaults.MINIO_CONSOLE_PORT=9001; DR-M2 fix: константа для
# MODULE_PORTS_DENY — ранее литерал вне SoT)
MINIO_CONSOLE_PORT: int = 9001

# Hermes desktop bridge (env_defaults.HERMES_DESKTOP_PORT=8642; DR-M2 fix: константа для
# MODULE_PORTS_DENY — ранее литерал вне SoT)
HERMES_DESKTOP_PORT: int = 8642
