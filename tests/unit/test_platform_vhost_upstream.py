# GREP_SUMMARY: test-platform-vhost-upstream nginx envsubst UPSTREAM multinode hermes langfuse grafana prometheus loki status-page devplan-010-t2-8
# STRUCTURE: ▶ multinode_runtime_env(S3, node) → ◇ remote-сервис? → ⊕ UPSTREAM_<SVC>=host:port → ⎋ asserts + статика шаблонов/compose
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T2.8 (TEST_SPEC): vhost upstream'ы платформенных сервисов — bare
##           ${UPSTREAM_*} в шаблонах (nginx envsubst), compose-дефолты Docker DNS (single-node),
##           multi-node значения из placement (deploy_orchestrator.multinode_runtime_env).
## @scope    core/internal/bootstrap/deploy/deploy_orchestrator.multinode_runtime_env +
##           core/modules/nginx/{config,docker-compose.base.yml}
## @invariants
##   - Native imports; fixture s3.yaml через Path(__file__) relative
##   - Статические проверки читают файлы репо read-only
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path

import yaml

from core.internal.bootstrap.deploy.deploy_orchestrator import multinode_runtime_env
from core.internal.shared.placement import load_placement

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "placement"
_NGINX_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "nginx"

_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("config/grafana-vhost.conf", "${UPSTREAM_GRAFANA}"),
    ("config/prometheus-vhost.conf", "${UPSTREAM_PROMETHEUS}"),
    ("config/loki-vhost.conf", "${UPSTREAM_LOKI}"),
    ("config/langfuse-vhost.conf", "${UPSTREAM_LANGFUSE}"),
    ("config/hermes-dashboard.conf", "${UPSTREAM_HERMES}"),
    ("config/platform-vhost.conf.template", "${UPSTREAM_STATUS_PAGE}"),
)

_COMPOSE_DEFAULTS: dict[str, str] = {
    "UPSTREAM_GRAFANA": "grafana:3000",
    "UPSTREAM_PROMETHEUS": "prometheus:9090",
    "UPSTREAM_LOKI": "loki:3100",
    "UPSTREAM_LANGFUSE": "langfuse:3000",
    "UPSTREAM_HERMES": "hermes-agent:9119",
    "UPSTREAM_STATUS_PAGE": "status-page:8080",
}


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · TEST_SPEC строка 435 (test_hermes_vhost_upstream_remote)
# · Scenario: S3, nginx на apps-1 → UPSTREAM_HERMES=10.8.0.12:9119 (agent-нода), UPSTREAM_LANGFUSE
# ·   =10.8.0.12:3001; локальные сервисы (loki на apps-1) НЕ получают UPSTREAM_* (compose-дефолт)
# · Last fail: N/A — T2.8 отсутствовал в реализации eb97ef6 (vhost'ы захардкожены на Docker DNS)
# · Remove if: cross-node vhost upstream'ы перестанут быть acceptance-критерием W2
def test_hermes_vhost_upstream_remote() -> None:
    """S3: hermes-dashboard upstream apps-1 = agent-1 host; локальный loki — без переопределения."""
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None

    env = multinode_runtime_env(placement, "apps-1")
    # Acceptance W2: «hermes-dashboard.conf на apps-1 резолвит upstream agent-1»
    assert env["UPSTREAM_HERMES"] == "10.8.0.12:9119", f"remote hermes upstream обязателен: {env}"
    assert env["UPSTREAM_LANGFUSE"] == "10.8.0.12:3001"
    # monitoring/logging/status-page размещены НА apps-1 → co-located → compose-дефолт (ключа нет)
    assert "UPSTREAM_GRAFANA" not in env, "co-located сервис не должен получать remote upstream"
    assert "UPSTREAM_LOKI" not in env
    # T2.2/T2.5 проводка той же функции
    assert env["SERVICE_BIND_HOST"] == "10.8.0.13"
    assert env["LOKI_TENANT"] == placement.context


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · Acceptance W2 — dependency-hosts модулей (T2.7)
# · Scenario: S3, agent-1 размещает langfuse/litellm → POSTGRES_HOST=10.8.0.11,
# ·   CLICKHOUSE_HOST=10.8.0.11 + CLICKHOUSE_NATIVE_PORT=19000 (host≠container, TRAP §3);
# ·   hermes-agent → REDIS_HOST=10.8.0.11; apps-1 (без consumer-модулей) — без dep-хостов
# · Last fail: N/A — Acceptance W2 «langfuse на agent-1 получает DATABASE_URL с host 10.8.0.11»
# · Remove if: dependency-routing модулей перестанет быть каноном T2.7
def test_module_dependency_hosts_remote() -> None:
    """S3: dep-хосты langfuse/litellm/hermes указывают на data-ноду; CH native 19000."""
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None

    env = multinode_runtime_env(placement, "agent-1")
    assert env["POSTGRES_HOST"] == "10.8.0.11"
    assert env["CLICKHOUSE_HOST"] == "10.8.0.11"
    assert env["CLICKHOUSE_NATIVE_PORT"] == "19000", "CH native peer: host 19000 ≠ container 9000"
    assert env["REDIS_HOST"] == "10.8.0.11"

    env_apps = multinode_runtime_env(placement, "apps-1")
    for key in ("POSTGRES_HOST", "CLICKHOUSE_HOST", "REDIS_HOST", "CLICKHOUSE_NATIVE_PORT"):
        assert key not in env_apps, f"apps-1 не размещает consumer-модулей: {key} не нужен"


# 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · не-ingress нода: 0 UPSTREAM_* (только базовые переменные)
# · Scenario: data-1 (без nginx) — upstream'ы бессмысленны (vhost'ов нет): SERVICE_BIND_HOST/
# ·   LOKI_TENANT/EXTRA_NO_PROXY присутствуют, все UPSTREAM_* отсутствуют
# · Last fail: N/A
# · Remove if: UPSTREAM_* станет общеконтекстным контрактом (не nginx-scoped)
def test_data_node_has_no_upstream_vars() -> None:
    """S3, node=data-1: nginx отсутствует → ни одного UPSTREAM_* (compose-дефолты не применяются)."""
    placement = load_placement(_FIXTURES / "s3.yaml")
    assert placement is not None

    env = multinode_runtime_env(placement, "data-1")
    assert env["SERVICE_BIND_HOST"] == "10.8.0.11"
    assert env["LOKI_TENANT"] == placement.context
    assert env["EXTRA_NO_PROXY"].startswith(","), "EXTRA_NO_PROXY контракт: ведущая запятая"
    upstream_keys = [key for key in env if key.startswith("UPSTREAM_")]
    assert not upstream_keys, f"data-нода без nginx не должна получать upstream'ы: {upstream_keys}"


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · single-node дефолты = Docker DNS алиасы (байт-паритет)
# · Scenario: nginx base.yml environment несёт UPSTREAM_<SVC>: ${VAR:-<docker-dns>} для всех 6;
# ·   отсутствие env от deploy → контейнер получает легаси-значения (инвариант 1 плана)
# · Last fail: N/A
# · Remove if: nginx envsubst получит нативную поддержку ${VAR:-default}
def test_compose_defaults_preserve_single_node() -> None:
    """nginx base.yml: compose-дефолты UPSTREAM_* равны прежним Docker-DNS литералам."""
    compose = yaml.safe_load((_NGINX_DIR / "docker-compose.base.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["nginx"]["environment"]
    for var_name, default in _COMPOSE_DEFAULTS.items():
        assert environment.get(var_name) == f"${{{var_name}:-{default}}}", (
            f"{var_name} обязан резолвить single-node дефолт {default} на compose-уровне"
        )


# 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · шаблоны используют bare ${UPSTREAM_*} (envsubst-контракт)
# · Scenario: каждый vhost-шаблон содержит bare ${UPSTREAM_X}; nginx envsubst НЕ понимает
# ·   ${VAR:-default} — дефолты ТОЛЬКО на compose-уровне (T2.8, TRAP механизма)
# · Last fail: N/A
# · Remove if: механизм рендера nginx-шаблонов изменится (не official-image entrypoint)
def test_templates_use_bare_upstream_vars() -> None:
    """Все 6 vhost-шаблов ссылаются на bare ${UPSTREAM_*}; ${UPSTREAM_X:-default} форма запрещена."""
    for rel_path, var_ref in _TEMPLATES:
        text = (_NGINX_DIR / rel_path).read_text(encoding="utf-8")
        assert var_ref in text, f"{rel_path} обязан использовать bare {var_ref}"
        assert ":-grafana:" not in text and ":-hermes-agent:" not in text and ":-loki:" not in text, (
            f"{rel_path}: bash-дефолты в nginx-шаблоне запрещены (envsubst их не раскрывает)"
        )
