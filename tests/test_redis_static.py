# GREP_SUMMARY: test-redis-static static-audit cache-only no-persistence allkeys-lfu shared-cache-net redis-exporter prometheus-scrape grafana-dashboard
# STRUCTURE: ▶ fixtures(redis_compose_path redis_module_yaml redis_healthcheck infra_metrics_compose prometheus_yml dashboards_dir) → ◇ test_redis_cache_only_command(◇ command args) → ◇ test_redis_no_volumes(◇ no volumes) → ◇ test_redis_no_ports(◇ no ports) → ◇ test_redis_network(◇ shared-cache-net only) → ◇ test_redis_image(◇ redis:7.4-alpine) → ◇ test_redis_module_yaml_no_spool(◇ no spool_dir/spool_volume) → ◇ test_redis_module_yaml_env_requires(◇ []) → ◇ test_prometheus_redis_exporter_job(◇ redis-exporter scrape) → ◇ test_infra_metrics_redis_exporter(◇ redis-exporter service) → ◇ test_redis_dashboard_exists(◇ redis.json) → ◇ test_redis_healthcheck_deep_exit(◇ exit 0)
# region MODULE_CONTRACT
## @purpose  Static audit of redis module configuration — cache-only contract enforcement.
##           Verifies that redis is configured as pure cache without persistence,
##           with correct eviction policy, no bind-mount volumes, and proper
##           monitoring via redis-exporter.
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML and JSON files directly.
## @invariants
##   - redis docker-compose.base.yml: --appendonly no, --save "", --maxmemory-policy allkeys-lfu
##   - redis docker-compose.base.yml: NO ports, NO volumes, network = shared-cache-net only
##   - redis docker-compose.base.yml: image = redis:7.4-alpine
##   - redis module.yaml: NO spool_dir, NO spool_volume; env_requires == []
##   - infra-metrics docker-compose.base.yml: has redis-exporter service with shared-cache-net
##   - prometheus.yml: has scrape job "redis-exporter" target "redis-exporter:9121"
##   - monitoring/config/dashboards/redis.json exists and is valid JSON
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Owner verdict wave-redis 2026-07-15: redis is cache-only — no persistence,
##            allkeys-lfu eviction, no data volumes. Monitoring via redis-exporter in infra-metrics.
##            All tests are structural contract checks (no runtime dependencies).
# endregion MODULE_CONTRACT

import json
import logging
import os
import shutil

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
REDIS_MODULE_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "redis")
INFRA_METRICS_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "infra-metrics")
MONITORING_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "monitoring")
PROMETHEUS_YML = os.path.join(MONITORING_DIR, "config", "prometheus.yml")
DASHBOARDS_DIR = os.path.join(MONITORING_DIR, "config", "dashboards")


# region FIXTURES
## @purpose — Module-scoped fixtures for isolated file access.
##            Each fixture copies the relevant module files to temp dirs
##            for isolated parsing without side effects.


@pytest.fixture(scope="module")
def redis_compose_base_path(tmp_path_factory):
    """Copy redis docker-compose.base.yml to temp dir."""
    src = os.path.join(REDIS_MODULE_DIR, "docker-compose.base.yml")
    dst_dir = tmp_path_factory.mktemp("redis_static_redis")
    dst = os.path.join(str(dst_dir), "docker-compose.base.yml")
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_module_yaml_path(tmp_path_factory):
    """Copy redis module.yaml to temp dir."""
    src = os.path.join(REDIS_MODULE_DIR, "module.yaml")
    dst_dir = tmp_path_factory.mktemp("redis_static_module")
    dst = os.path.join(str(dst_dir), "module.yaml")
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_healthcheck_path(tmp_path_factory):
    """Copy redis healthcheck.sh to temp dir."""
    src = os.path.join(REDIS_MODULE_DIR, "healthcheck.sh")
    dst_dir = tmp_path_factory.mktemp("redis_static_hc")
    dst = os.path.join(str(dst_dir), "healthcheck.sh")
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def infra_metrics_compose_path(tmp_path_factory):
    """Copy infra-metrics docker-compose.base.yml to temp dir."""
    src = os.path.join(INFRA_METRICS_DIR, "docker-compose.base.yml")
    dst_dir = tmp_path_factory.mktemp("redis_static_infra")
    dst = os.path.join(str(dst_dir), "docker-compose.base.yml")
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def prometheus_yml_path(tmp_path_factory):
    """Copy prometheus.yml to temp dir."""
    src = PROMETHEUS_YML
    dst_dir = tmp_path_factory.mktemp("redis_static_prom")
    dst = os.path.join(str(dst_dir), "prometheus.yml")
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def dashboards_dir_path(tmp_path_factory):
    """Copy dashboards directory to temp dir for discovery."""
    dst = tmp_path_factory.mktemp("redis_static_dashboards")
    if os.path.isdir(DASHBOARDS_DIR):
        for fname in os.listdir(DASHBOARDS_DIR):
            src = os.path.join(DASHBOARDS_DIR, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(str(dst), fname))
    return str(dst)


# endregion FIXTURES


# region HELPERS
## @purpose — Shared helpers for YAML loading.


def _load_yaml(path: str) -> dict:
    """Load and return parsed YAML content."""
    assert os.path.isfile(path), f"File not found: {path}"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"YAML root must be dict, got {type(data).__name__}"
    return data


# endregion HELPERS


# region REDIS_CACHE_ONLY_CONTRACT_TESTS
## @purpose — Cache-only contract enforcement for redis module.
##            These tests validate the strict cache-only configuration mandated
##            by owner verdict wave-redis 2026-07-15.
## @scope    Static audit — no Docker daemon.
## @invariants
##   - All tests use @pytest.mark.static_audit + @ldd_trajectory
##   - Tests parse docker-compose.base.yml, module.yaml, prometheus.yml
##   - Each test checks exactly one aspect of the cache-only contract
## @rationale — Building a clear failure picture in RED phase. Each assertion
##              documents a specific violation of the cache-only contract.

# ── Test 1: Redis command has cache-only flags ──────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_cache_only_command(redis_compose_base_path, caplog):
    """Redis command must contain --appendonly no, --save "", --maxmemory-policy allkeys-lfu."""
    data = _load_yaml(redis_compose_base_path)
    services = data.get("services", {})
    redis_svc = services.get("redis", {})
    command = redis_svc.get("command", "")

    if isinstance(command, list):
        cmd_str = " ".join(command)
    else:
        cmd_str = str(command)

    logger.info("[IMP:7][test_redis][cache_only_command] command=%s", cmd_str)

    has_appendonly_no = "--appendonly no" in cmd_str or "appendonly no" in cmd_str
    has_save_empty = (
        '--save ""' in cmd_str
        or 'save ""' in cmd_str
        or "--save ''" in cmd_str
        or ("--save " in cmd_str and '""' in cmd_str)
    )
    has_allkeys_lfu = "--maxmemory-policy allkeys-lfu" in cmd_str

    logger.critical(
        "[IMP:9][test_redis][cache_only_command] ASSERT: appendonly_no=%s save_empty=%s allkeys_lfu=%s",
        has_appendonly_no,
        has_save_empty,
        has_allkeys_lfu,
    )
    assert has_appendonly_no, f"Redis command must include '--appendonly no', got: {cmd_str}"
    assert has_save_empty, f"Redis command must include '--save \"\"', got: {cmd_str}"
    assert has_allkeys_lfu, f"Redis command must include '--maxmemory-policy allkeys-lfu', got: {cmd_str}"


# ── Test 2: Redis service has NO volumes ───────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_no_volumes(redis_compose_base_path, caplog):
    """Redis service must NOT have volumes key (no persistence volume)."""
    data = _load_yaml(redis_compose_base_path)
    services = data.get("services", {})
    redis_svc = services.get("redis", {})

    has_volumes = "volumes" in redis_svc
    logger.critical(
        "[IMP:9][test_redis][no_volumes] ASSERT: service.volumes present=%s",
        has_volumes,
    )
    assert not has_volumes, (
        f"Redis service must NOT have volumes (cache-only — no persistence). Found: {redis_svc.get('volumes', [])}"
    )


# ── Test 3: Top-level volumes must be absent ────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_top_level_no_volumes(redis_compose_base_path, caplog):
    """Top-level volumes block must be absent (no redis-data bind mount)."""
    data = _load_yaml(redis_compose_base_path)

    has_top_volumes = "volumes" in data
    logger.critical(
        "[IMP:9][test_redis][top_no_volumes] ASSERT: top-level volumes present=%s",
        has_top_volumes,
    )
    assert not has_top_volumes, (
        f"Top-level volumes must be absent in redis compose (cache-only). Found: {list(data.get('volumes', {}).keys())}"
    )


# ── Test 4: Redis has NO ports ─────────────────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_no_ports(redis_compose_base_path, caplog):
    """Redis service must NOT have ports exposed to host."""
    data = _load_yaml(redis_compose_base_path)
    services = data.get("services", {})
    redis_svc = services.get("redis", {})

    has_ports = "ports" in redis_svc
    logger.critical(
        "[IMP:9][test_redis][no_ports] ASSERT: ports present=%s",
        has_ports,
    )
    assert not has_ports, f"Redis service must NOT expose ports to host. Found: {redis_svc.get('ports', [])}"


# ── Test 5: Redis network is shared-cache-net only ────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_network_shared_cache_only(redis_compose_base_path, caplog):
    """Redis must be on shared-cache-net only."""
    data = _load_yaml(redis_compose_base_path)
    services = data.get("services", {})
    redis_svc = services.get("redis", {})
    networks = redis_svc.get("networks", {})

    net_names = set(networks.keys()) if isinstance(networks, dict) else set()
    logger.critical(
        "[IMP:9][test_redis][network] ASSERT: networks=%s (expected {shared-cache-net})",
        net_names,
    )
    assert net_names == {"shared-cache-net"}, f"Redis must be on shared-cache-net only, got: {net_names}"


# ── Test 6: Redis image is redis:7.4-alpine ───────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_image(redis_compose_base_path, caplog):
    """Redis image must be redis:7.4-alpine."""
    data = _load_yaml(redis_compose_base_path)
    services = data.get("services", {})
    redis_svc = services.get("redis", {})
    image = redis_svc.get("image", "")

    logger.critical(
        "[IMP:9][test_redis][image] ASSERT: image=%s",
        image,
    )
    assert image.startswith("redis:7.4-alpine"), (
        f"Redis image must start with 'redis:7.4-alpine', got: '{image}'"
    )


# ── Test 7: Module.yaml has NO spool_dir ───────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_module_yaml_no_spool_dir(redis_module_yaml_path, caplog):
    """redis module.yaml must have spool_dir: none (cache-only, stateless)."""
    data = _load_yaml(redis_module_yaml_path)

    spool_dir = data.get("spool_dir")
    spool_volume = data.get("spool_volume")

    logger.critical(
        "[IMP:9][test_redis][module_yaml] ASSERT: spool_dir=%s spool_volume=%s",
        spool_dir,
        spool_volume,
    )

    # spool_dir must be "none" (explicit stateless declaration per T4 DevPlan 004)
    assert spool_dir == "none", (
        f"redis module.yaml must have spool_dir: none (cache-only, stateless). "
        f"Found: spool_dir={spool_dir!r}"
    )
    assert not spool_volume, (
        f"redis module.yaml must NOT have spool_volume (cache-only). Found: {spool_volume!r}"
    )


# ── Test 8: Module.yaml env_requires is empty list ─────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_module_yaml_env_requires(redis_module_yaml_path, caplog):
    """redis module.yaml env_requires must be empty list."""
    data = _load_yaml(redis_module_yaml_path)

    env_requires = data.get("env_requires", None)
    logger.critical(
        "[IMP:9][test_redis][module_yaml] ASSERT: env_requires=%s",
        env_requires,
    )
    assert env_requires == [], f"redis module.yaml env_requires must be empty list, got: {env_requires}"


# ── Test 9: Prometheus has redis-exporter scrape job ───────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_redis_exporter_job(prometheus_yml_path, caplog):
    """prometheus.yml must have scrape job 'redis-exporter' targeting redis-exporter:9121."""
    data = _load_yaml(prometheus_yml_path)

    scrape_configs = data.get("scrape_configs", [])
    redis_job = None
    for job in scrape_configs:
        if job.get("job_name") == "redis-exporter":
            redis_job = job
            break

    logger.critical(
        "[IMP:9][test_redis][prometheus] ASSERT: redis-exporter job found=%s",
        redis_job is not None,
    )
    assert redis_job is not None, "prometheus.yml must have a scrape job named 'redis-exporter'"

    # Validate target
    targets = []
    for sg in redis_job.get("static_configs", []):
        targets.extend(sg.get("targets", []))
    logger.critical(
        "[IMP:9][test_redis][prometheus] ASSERT: targets=%s",
        targets,
    )
    assert "redis-exporter:9121" in targets, (
        f"redis-exporter scrape job must target 'redis-exporter:9121', got targets: {targets}"
    )


# ── Test 10: infra-metrics has redis-exporter service ──────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_infra_metrics_redis_exporter(infra_metrics_compose_path, caplog):
    """infra-metrics docker-compose.base.yml must have redis-exporter service."""
    data = _load_yaml(infra_metrics_compose_path)
    services = data.get("services", {})

    has_redis_exporter = "redis-exporter" in services
    logger.critical(
        "[IMP:9][test_redis][infra_metrics] ASSERT: redis-exporter in services=%s",
        has_redis_exporter,
    )
    assert has_redis_exporter, "infra-metrics docker-compose.base.yml must have 'redis-exporter' service"

    redis_exp = services.get("redis-exporter", {})
    image = redis_exp.get("image", "")
    logger.critical(
        "[IMP:9][test_redis][infra_metrics] ASSERT: image=%s",
        image,
    )
    assert image == "oliver006/redis_exporter:v1.86.0@sha256:2e9795be900db073e9475fdb9c5124db309b07a3e4e75a1770705cb03be1a1c8", (
        f"redis-exporter image must be 'oliver006/redis_exporter:v1.86.0@sha256:...', got: '{image}'"
    )

    # Must have shared-cache-net
    networks = redis_exp.get("networks", {})
    net_set = set(networks.keys()) if isinstance(networks, dict) else set()
    has_shared_cache = "shared-cache-net" in net_set
    logger.critical(
        "[IMP:9][test_redis][infra_metrics] ASSERT: shared-cache-net in networks=%s",
        has_shared_cache,
    )
    assert has_shared_cache, f"redis-exporter must have shared-cache-net in networks. Found: {net_set}"


# ── Test 11: redis.json dashboard exists ───────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_dashboard_exists(dashboards_dir_path, caplog):
    """redis.json dashboard must exist in monitoring dashboards directory."""
    redis_dash_path = os.path.join(dashboards_dir_path, "redis.json")

    exists = os.path.isfile(redis_dash_path)
    logger.critical(
        "[IMP:9][test_redis][dashboard] ASSERT: redis.json exists=%s",
        exists,
    )
    assert exists, f"redis.json dashboard not found in {dashboards_dir_path}"

    # Validate JSON
    with open(redis_dash_path) as f:
        dash_data = json.load(f)
    assert isinstance(dash_data, dict), "redis.json must be a valid JSON dict"
    assert "panels" in dash_data, "redis.json must have 'panels' key"
    assert len(dash_data["panels"]) >= 3, f"redis.json must have at least 3 panels, got {len(dash_data['panels'])}"
    logger.critical(
        "[IMP:9][test_redis][dashboard] ASSERT: panels=%d",
        len(dash_data["panels"]),
    )


# ── Test 12: Healthcheck deep mode has exit 0 ─────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_healthcheck_deep_exit(redis_healthcheck_path, caplog):
    """redis healthcheck.sh must have 'exit 0  # ранний выход' in deep block."""
    with open(redis_healthcheck_path) as f:
        content = f.read()

    has_exit_0 = "exit 0  # ранний выход" in content
    logger.critical(
        "[IMP:9][test_redis][healthcheck] ASSERT: early exit 0 in deep block=%s",
        has_exit_0,
    )
    assert has_exit_0, "redis/healthcheck.sh must have 'exit 0  # ранний выход' after deep diagnostics"


# endregion REDIS_CACHE_ONLY_CONTRACT_TESTS
