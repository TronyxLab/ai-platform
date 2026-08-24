# GREP_SUMMARY: test-monitoring-multinode prometheus-targets file-sd node-exporter cadvisor exporters multi-node placement single-node fallback RemoteNodeDown LokiCollectorStale
# STRUCTURE: ┌4 test functions┐ → ◇ 3-node S3 render (job_name 1:1 + hosts) (1) → ◇ single-node fallback byte-identical (1) → ◇ tmpl file_sd job_name mapping (1) → ◇ alerts yml valid + RemoteNodeDown (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 010 T3.3/T3.4 monitoring multi-node:
##           generate_node_targets (file_sd node targets по placement) + platform-alerts.yml.
## @scope    No Docker — tmp_path outputs. Читает только конфиг-файлы monitoring (read-only).
## @invariants
##   - All tests use tmp_path (zero hardcoded paths)
##   - S3-фикстура: data-1/agent-1/apps-1 (10.8.0.11/12/13) — модули из §5 DevPlan 010
##   - job_name 1:1 (ЛОВУШКА T3.3): файлы nodes/*.json именованы как jobs, tmpl file_sd
##     ссылается на них
##   - single-node (nodes None) → Docker-DNS fallback, байт-паритет прежней статике
##   - LDD: IMP:7-10 траектория через caplog (assert_ldd_imp9)
## @rationale  DevPlan 010 T3.3 $TEST_SPEC-зона Coder: (a) рендер 3 нод, (b) single-node
##            идентичность, (c) alerts валидны; LDD-конвенция (grep "[IMP:" tests/).
## @changes  2026-08-22 | DevPlan 010 T3.3/T3.4 — created
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest
import yaml
from _conftest.ldd import ldd_trajectory
from monitoring.prometheus_targets import NodeInfo, generate_node_targets

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MONITORING_DIR = _PROJECT_ROOT / "core" / "modules" / "monitoring"
_PROMETHEUS_TMPL = _MONITORING_DIR / "config" / "prometheus.yml.tmpl"
_PLATFORM_ALERTS_YML = _MONITORING_DIR / "config" / "platform-alerts.yml"

# ── S3-фикстура (DevPlan 010 §5 W1 Acceptance) ───────────────────────────────
# data-1 = postgres/redis/minio/clickhouse/backup-cron/service-exporters/platform-secrets
#          + node-metrics/log-collector (все ноды)
# agent-1 = hermes-agent/litellm/langfuse + node-metrics/log-collector
# apps-1  = nginx/status-page/monitoring/logging + node-metrics/log-collector
_S3_NODES = [
    NodeInfo(
        name="data-1",
        host="10.8.0.11",
        modules=(
            "postgres",
            "redis",
            "minio",
            "clickhouse",
            "backup-cron",
            "service-exporters",
            "node-metrics",
            "log-collector",
        ),
    ),
    NodeInfo(
        name="agent-1",
        host="10.8.0.12",
        modules=("hermes-agent", "litellm", "langfuse", "node-metrics", "log-collector"),
    ),
    NodeInfo(
        name="apps-1",
        host="10.8.0.13",
        modules=("nginx", "status-page", "monitoring", "logging", "node-metrics", "log-collector"),
    ),
]

# Single-node fallback = прежний static_configs набор (target + labels из
# prometheus.yml.tmpl ДО миграции static→file_sd) — байт-паритет инварианта 2 плана.
_EXPECTED_SINGLE_NODE = {
    "node-exporter.json": {
        "targets": ["node-exporter:9100"],
        "labels": {"service": "node-exporter", "component": "host-monitor"},
    },
    "cadvisor.json": {
        "targets": ["cadvisor:8080"],
        "labels": {"service": "cadvisor", "component": "container-monitor"},
    },
    "nginx-exporter.json": {
        "targets": ["nginx-prometheus-exporter:9113"],
        "labels": {"service": "nginx", "component": "reverse-proxy"},
    },
    "redis-exporter.json": {
        "targets": ["redis-exporter:9121"],
        "labels": {"service": "redis", "component": "cache"},
    },
    "postgres-exporter.json": {
        "targets": ["postgres-exporter:9187"],
        "labels": {"service": "postgres", "component": "database"},
    },
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# 🧪 TRAP[TEST] · Regression · Scenario: 3-node S3 render (DevPlan 010 T3.3)
# · Expect: nodes/*.json — node-exporter/cadvisor на всех 3 хостах; postgres/redis-exporter
# ·   только data-1 (10.8.0.11); nginx-exporter только apps-1 (10.8.0.13); labels паритет статике
# · Last fail: None (new test for DevPlan 010 T3.3)
# · Remove if: generate_node_targets schema/логика меняется
@ldd_trajectory
def test_render_three_node_s3(tmp_path: Path, caplog) -> None:
    """S3: node targets file_sd json с сохранёнными job_name и хостами нод."""
    caplog.set_level(0)

    result = generate_node_targets(_S3_NODES, tmp_path)

    assert result.status == "created"
    assert result.output_path == tmp_path / "nodes"

    nodes_dir = tmp_path / "nodes"
    assert nodes_dir.exists()

    # node-metrics all-nodes: node-exporter + cadvisor на всех 3 хостах
    node_exporter = _read_json(nodes_dir / "node-exporter.json")
    assert sorted(node_exporter["targets"]) == ["10.8.0.11:9100", "10.8.0.12:9100", "10.8.0.13:9100"]
    assert node_exporter["labels"] == {"service": "node-exporter", "component": "host-monitor"}

    cadvisor = _read_json(nodes_dir / "cadvisor.json")
    assert sorted(cadvisor["targets"]) == ["10.8.0.11:8080", "10.8.0.12:8080", "10.8.0.13:8080"]
    assert cadvisor["labels"] == {"service": "cadvisor", "component": "container-monitor"}

    # service-exporters: только ноды с соответствующим сервис-модулем (S3: data-1)
    postgres_exporter = _read_json(nodes_dir / "postgres-exporter.json")
    assert postgres_exporter["targets"] == ["10.8.0.11:9187"], "postgres-exporter только на data-1"
    assert postgres_exporter["labels"] == {"service": "postgres", "component": "database"}

    redis_exporter = _read_json(nodes_dir / "redis-exporter.json")
    assert redis_exporter["targets"] == ["10.8.0.11:9121"], "redis-exporter только на data-1"

    # nginx-exporter — на apps-1 (nginx размещён там), НЕ на data-1
    nginx_exporter = _read_json(nodes_dir / "nginx-exporter.json")
    assert nginx_exporter["targets"] == ["10.8.0.13:9113"], "nginx-exporter только на apps-1 (nginx)"
    assert nginx_exporter["labels"] == {"service": "nginx", "component": "reverse-proxy"}

    logger.info("[IMP:9][test_multinode] S3 render OK: 3 nodes, job_name 1:1, hosts сохранены")


# 🧪 TRAP[TEST] · Regression · Scenario: single-node fallback (DevPlan 010 T3.3 инвариант 2)
# · Expect: nodes=None → Docker-DNS target'ы, байт-идентичные прежнему static_configs набору
# · Last fail: None (new test for DevPlan 010 T3.3)
# · Remove if: single-node fallback семантика меняется
@ldd_trajectory
def test_single_node_fallback_identical_to_static(tmp_path: Path, caplog) -> None:
    """nodes=None → вывод идентичен текущему статическому набору (fallback Docker-DNS)."""
    caplog.set_level(0)

    result = generate_node_targets(None, tmp_path)

    assert result.status == "created"
    nodes_dir = tmp_path / "nodes"
    for file_name, expected in _EXPECTED_SINGLE_NODE.items():
        data = _read_json(nodes_dir / file_name)
        assert data == expected, f"{file_name}: single-node fallback не совпадает со статикой"

    logger.info("[IMP:9][test_multinode] Single-node fallback byte-identical to static set")


# 🧪 TRAP[TEST] · Regression · Scenario: idempotency (DevPlan 010 T3.3 «рендер идемпотентен»)
# · Expect: повторный рендер тех же входных данных → noop (0 файлов перезаписано), содержимое байт-равно
# · Last fail: None (new test for DevPlan 010 T3.3)
# · Remove if: идемпотентность-семантика меняется
@ldd_trajectory
def test_render_idempotent_second_pass_noop(tmp_path: Path, caplog) -> None:
    """Повторный рендер с теми же данными → status=noop, файлы не перезаписаны."""
    caplog.set_level(0)

    first = generate_node_targets(_S3_NODES, tmp_path)
    assert first.status == "created"

    nodes_dir = tmp_path / "nodes"
    before = {p.name: p.read_bytes() for p in nodes_dir.glob("*.json")}

    second = generate_node_targets(_S3_NODES, tmp_path)
    assert second.status == "noop", "повторный рендер тех же данных обязан быть noop (идемпотентность)"

    after = {p.name: p.read_bytes() for p in nodes_dir.glob("*.json")}
    assert before == after, "повторный рендер изменил содержимое — идемпотентность нарушена"

    logger.info("[IMP:9][test_multinode] Idempotency OK: second pass noop, files unchanged")


# 🧪 TRAP[TEST] · Regression · Scenario: tmpl file_sd job_name 1:1 (ЛОВУШКА T3.3)
# · Expect: каждый job (node-exporter/cadvisor/.../nginx-exporter) ссылается на
# ·   /prometheus-targets/nodes/<job_name>.json — миграция static→file_sd без переименования
# · Last fail: None (new test for DevPlan 010 T3.3)
# · Remove if: file_sd-механизм нодовых target'ов меняется
def test_tmpl_node_jobs_file_sd_job_name_preserved(caplog) -> None:
    """prometheus.yml.tmpl: 5 нодовых jobs — file_sd с путём nodes/<job_name>.json (job_name 1:1)."""
    caplog.set_level(0)

    with _PROMETHEUS_TMPL.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    jobs = {cfg.get("job_name"): cfg for cfg in data.get("scrape_configs", [])}
    node_jobs = {
        "node-exporter": "node-exporter.json",
        "cadvisor": "cadvisor.json",
        "nginx-exporter": "nginx-exporter.json",
        "redis-exporter": "redis-exporter.json",
        "postgres-exporter": "postgres-exporter.json",
    }

    for job_name, file_name in node_jobs.items():
        cfg = jobs.get(job_name)
        assert cfg is not None, f"tmpl: job '{job_name}' отсутствует (job_name обязан сохраниться 1:1)"
        file_sd = cfg.get("file_sd_configs", [])
        assert file_sd, f"tmpl: job '{job_name}' обязан использовать file_sd (static→file_sd миграция T3.3)"
        files = file_sd[0].get("files", [])
        assert f"/prometheus-targets/nodes/{file_name}" in files, (
            f"tmpl: job '{job_name}' file_sd не указывает на nodes/{file_name}"
        )

    logger.info("[IMP:9][test_multinode] tmpl: 5 node jobs file_sd, job_name 1:1 сохранён")


# 🧪 TRAP[TEST] · Regression · Scenario: platform-alerts.yml валиден + RemoteNodeDown (T3.4)
# · Expect: yaml.safe_load OK; RemoteNodeDown с expr up{job="node-exporter"} == 0 for 5m;
# ·   LokiCollectorStale (Prometheus-based, НЕ Loki-log-based)
# · Last fail: None (new test for DevPlan 010 T3.4)
# · Remove if: состав multi-node алертов меняется
@ldd_trajectory
def test_platform_alerts_yaml_valid_remote_node_down(caplog) -> None:
    """platform-alerts.yml: валидный YAML + RemoteNodeDown (up{job="node-exporter"}==0, for 5m)."""
    caplog.set_level(0)

    with _PLATFORM_ALERTS_YML.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert isinstance(data, dict) and "groups" in data, "platform-alerts.yml: нет секции groups"

    alerts: dict[str, dict] = {}
    for group in data["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                alerts[rule["alert"]] = rule

    remote_down = alerts.get("RemoteNodeDown")
    assert remote_down is not None, "platform-alerts.yml не содержит RemoteNodeDown"
    assert remote_down.get("for") == "5m"
    # job "node-exporter" — фактическое имя из T3.3 (план T3.4 писал job="nodes" плейсхолдером)
    assert 'up{job="node-exporter"} == 0' in remote_down["expr"]
    assert remote_down["labels"]["severity"] == "critical"

    # LokiCollectorStale — Prometheus-based (loki job + distributor freshness), НЕ log-based
    collector_stale = alerts.get("LokiCollectorStale")
    assert collector_stale is not None, "platform-alerts.yml не содержит LokiCollectorStale"
    assert 'up{job="loki"}' in collector_stale["expr"]
    assert "loki_distributor_bytes_received_total" in collector_stale["expr"]

    logger.info("[IMP:9][test_multinode] alerts yml valid: RemoteNodeDown + LokiCollectorStale")
