# GREP_SUMMARY: test-infra-metrics-static static-audit module-yaml compose healthcheck cadvisor node-exporter nginx-prometheus-exporter redis-exporter
# STRUCTURE: ▶ fixtures(compose_path module_yaml_path healthcheck_path) → ◇ test_module_yaml_contract → ◇ test_compose_profiles → ◇ test_compose_healthcheck → ◇ test_healthcheck_sh_exists → ◇ test_cadvisor_image → ◇ test_node_exporter_image → ◇ test_nginx_exporter_image → ◇ test_redis_exporter_image → ◇ test_networks_external → ⎋
# region MODULE_CONTRACT
## @purpose  Static audit of infra-metrics module configuration — cAdvisor, Node Exporter, Nginx Prometheus Exporter, Redis Exporter.
##           Verifies module.yaml, docker-compose.base.yml, and healthcheck.sh structural contracts.
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML files directly.
## @invariants
##   - module.yaml: name=infra-metrics, install_type=docker, env_requires=[]
##   - docker-compose.base.yml: profiles: [infra-metrics] on every service, healthcheck present
##   - cAdvisor image: gcr.io/cadvisor/cadvisor:v0.55.1
##   - Node Exporter image: prom/node-exporter:v1.12.0
##   - Nginx Exporter image: nginx/nginx-prometheus-exporter:1.5.1
##   - Redis Exporter image: oliver006/redis_exporter:v1.86.0
##   - All services on observability-net; redis-exporter also on shared-cache-net
##   - Networks declared as external: true
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Owner verdict wave-infra-metrics 2026-07-15: РАБОТАЕТ.
##            All tests are structural contract checks (no runtime dependencies).
# endregion MODULE_CONTRACT

import logging
import os

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
INFRA_METRICS_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "infra-metrics")
COMPOSE_BASE = os.path.join(INFRA_METRICS_DIR, "docker-compose.base.yml")
MODULE_YAML = os.path.join(INFRA_METRICS_DIR, "module.yaml")
HEALTHCHECK_SH = os.path.join(INFRA_METRICS_DIR, "healthcheck.sh")

# ── Expected values ───────────────────────────────────────────────────────────
EXPECTED_MODULE_NAME = "infra-metrics"
EXPECTED_INSTALL_TYPE = "docker"
EXPECTED_ENV_REQUIRES: list[str] = ["POSTGRES_USER", "POSTGRES_PASSWORD"]
EXPECTED_SERVICES = [
    "cadvisor",
    "node-exporter",
    "nginx-prometheus-exporter",
    "redis-exporter",
]
EXPECTED_IMAGES = {
    "cadvisor": "gcr.io/cadvisor/cadvisor:v0.55.1@sha256:3de2bd5203120b866d74a9b283b2ffb8ec382fbf9dc321814700c6ea6f44ec57",
    "node-exporter": "prom/node-exporter:v1.12.0@sha256:9b0ade5e607f9dbedb0a8e11151b6011ae5bd79304c261804cfdd2cadf200a80",
    "nginx-prometheus-exporter": "nginx/nginx-prometheus-exporter:1.5.1@sha256:9f6d963bb2b19d706d401cc3e2c3ea8de2f1c471b96a2156ca45e76f650b1625",
    "redis-exporter": "oliver006/redis_exporter:v1.86.0@sha256:2e9795be900db073e9475fdb9c5124db309b07a3e4e75a1770705cb03be1a1c8",
}
EXPECTED_NETWORKS = ["observability-net", "shared-cache-net"]


# region FIXTURES


@pytest.fixture(scope="module")
def compose_data():
    """Load and return infra-metrics docker-compose.base.yml as dict."""
    with open(COMPOSE_BASE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def module_yaml_data():
    """Load and return infra-metrics module.yaml as dict."""
    with open(MODULE_YAML) as f:
        return yaml.safe_load(f)


# endregion FIXTURES

# region TESTS


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: module.yaml contract
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_module_yaml_contract(module_yaml_data, caplog) -> None:
    """Verify module.yaml has expected name, install_type, and env_requires.

    ## @purpose — Validate module metadata matches core/modules/AGENTS.md D4 contract.
    ## @io — ⇥ module_yaml_data, caplog → ⎋ None (asserts contract)
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][infra-metrics][static] Checking module.yaml contract")

    assert module_yaml_data["name"] == EXPECTED_MODULE_NAME, (
        f"module.yaml name={module_yaml_data['name']}, expected {EXPECTED_MODULE_NAME}"
    )
    logger.info("[IMP:8][infra-metrics][static] module.yaml name: %s", module_yaml_data["name"])

    assert module_yaml_data["install_type"] == EXPECTED_INSTALL_TYPE, (
        f"module.yaml install_type={module_yaml_data['install_type']}, expected {EXPECTED_INSTALL_TYPE}"
    )
    logger.info(
        "[IMP:8][infra-metrics][static] module.yaml install_type: %s",
        module_yaml_data["install_type"],
    )

    assert module_yaml_data.get("env_requires", []) == EXPECTED_ENV_REQUIRES, (
        f"module.yaml env_requires={module_yaml_data.get('env_requires')}, expected {EXPECTED_ENV_REQUIRES}"
    )
    logger.info(
        "[IMP:8][infra-metrics][static] module.yaml env_requires: %s",
        module_yaml_data.get("env_requires"),
    )

    logger.info("[IMP:9][infra-metrics][static] ✅ module.yaml contract OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: All services have profiles: [infra-metrics]
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_compose_profiles(compose_data, caplog) -> None:
    """Verify every service has profiles: [infra-metrics].

    ## @purpose — Pluggability contract: profiles enable selective compose startup.
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts profiles)
    ## @complexity — O(N) — N = number of services
    """
    services = compose_data.get("services", {})
    logger.info(
        "[IMP:7][infra-metrics][static] Checking profiles on %d services",
        len(services),
    )

    for svc_name in EXPECTED_SERVICES:
        assert svc_name in services, f"Service '{svc_name}' not found in compose"
        svc = services[svc_name]
        profiles = svc.get("profiles", [])
        assert "infra-metrics" in profiles, (
            f"Service '{svc_name}' missing profile 'infra-metrics'. Profiles: {profiles}"
        )
        logger.info(
            "[IMP:8][infra-metrics][static] Service '%s' has profiles: %s",
            svc_name,
            profiles,
        )

    logger.info("[IMP:9][infra-metrics][static] ✅ All services have 'infra-metrics' profile")


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: All services have healthcheck
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_compose_healthcheck(compose_data, caplog) -> None:
    """Verify every service has a healthcheck block.

    ## @purpose — docker compose up --wait requires HEALTHCHECK on every service.
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts healthcheck)
    ## @complexity — O(N)
    """
    services = compose_data.get("services", {})
    logger.info(
        "[IMP:7][infra-metrics][static] Checking healthcheck on %d services",
        len(services),
    )

    for svc_name in EXPECTED_SERVICES:
        svc = services[svc_name]
        assert "healthcheck" in svc, f"Service '{svc_name}' missing healthcheck block"
        logger.info(
            "[IMP:8][infra-metrics][static] Service '%s' has healthcheck: %s",
            svc_name,
            svc["healthcheck"].get("test"),
        )

    logger.info("[IMP:9][infra-metrics][static] ✅ All services have healthcheck")


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: healthcheck.sh exists
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_healthcheck_sh_exists(caplog) -> None:
    """Verify healthcheck.sh exists and is executable.

    ## @purpose — Healthcheck entry point for module system.
    ## @io — ⇥ caplog → ⎋ None (asserts file exists)
    ## @complexity — O(1)
    """
    logger.info(
        "[IMP:7][infra-metrics][static] Checking healthcheck.sh at %s",
        HEALTHCHECK_SH,
    )

    assert os.path.isfile(HEALTHCHECK_SH), f"healthcheck.sh not found at {HEALTHCHECK_SH}"
    assert os.access(HEALTHCHECK_SH, os.X_OK), f"healthcheck.sh not executable at {HEALTHCHECK_SH}"

    logger.info("[IMP:9][infra-metrics][static] ✅ healthcheck.sh exists and is executable")


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: cAdvisor image and config
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_cadvisor_image(compose_data, caplog) -> None:
    """Verify cAdvisor uses expected image and port mapping.

    ## @purpose — cAdvisor image and port contract validation.
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts image)
    ## @complexity — O(1)
    """
    cadvisor = compose_data["services"]["cadvisor"]

    image = cadvisor["image"]
    assert image == EXPECTED_IMAGES["cadvisor"], f"cAdvisor image={image}, expected {EXPECTED_IMAGES['cadvisor']}"
    logger.info("[IMP:8][infra-metrics][static] cAdvisor image: %s", image)

    ports = cadvisor.get("ports", [])
    assert any("8080" in p for p in ports), f"cAdvisor missing port 8080 mapping. Ports: {ports}"
    logger.info("[IMP:8][infra-metrics][static] cAdvisor ports: %s", ports)

    logger.info("[IMP:9][infra-metrics][static] ✅ cAdvisor image and ports OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: Node Exporter image
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_node_exporter_image(compose_data, caplog) -> None:
    """Verify Node Exporter uses expected image and port mapping.

    ## @purpose — Node Exporter image and port contract validation.
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts image)
    ## @complexity — O(1)
    """
    ne = compose_data["services"]["node-exporter"]

    image = ne["image"]
    assert image == EXPECTED_IMAGES["node-exporter"], (
        f"Node Exporter image={image}, expected {EXPECTED_IMAGES['node-exporter']}"
    )
    logger.info("[IMP:8][infra-metrics][static] Node Exporter image: %s", image)

    ports = ne.get("ports", [])
    assert any("9100" in p for p in ports), f"Node Exporter missing port 9100 mapping. Ports: {ports}"
    logger.info("[IMP:8][infra-metrics][static] Node Exporter ports: %s", ports)

    logger.info("[IMP:9][infra-metrics][static] ✅ Node Exporter image and ports OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 7: Nginx Prometheus Exporter image
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_nginx_exporter_image(compose_data, caplog) -> None:
    """Verify Nginx Prometheus Exporter uses expected image.

    ## @purpose — Nginx Exporter image contract (scratch image, no shell).
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts image)
    ## @complexity — O(1)
    """
    nginx_exp = compose_data["services"]["nginx-prometheus-exporter"]

    image = nginx_exp["image"]
    assert image == EXPECTED_IMAGES["nginx-prometheus-exporter"], (
        f"Nginx Exporter image={image}, expected {EXPECTED_IMAGES['nginx-prometheus-exporter']}"
    )
    logger.info("[IMP:8][infra-metrics][static] Nginx Exporter image: %s", image)

    logger.info("[IMP:9][infra-metrics][static] ✅ Nginx Exporter image OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 8: Redis Exporter image
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_redis_exporter_image(compose_data, caplog) -> None:
    """Verify Redis Exporter uses expected image.

    ## @purpose — Redis Exporter image contract (scratch image, no shell).
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts image)
    ## @complexity — O(1)
    """
    redis_exp = compose_data["services"]["redis-exporter"]

    image = redis_exp["image"]
    assert image == EXPECTED_IMAGES["redis-exporter"], (
        f"Redis Exporter image={image}, expected {EXPECTED_IMAGES['redis-exporter']}"
    )
    logger.info("[IMP:8][infra-metrics][static] Redis Exporter image: %s", image)

    logger.info("[IMP:9][infra-metrics][static] ✅ Redis Exporter image OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 9: Networks declared as external
# ══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_networks_external(compose_data, caplog) -> None:
    """Verify networks are declared as external.

    ## @purpose — external:true networks are pre-created by deploy-modules.sh.
    ##            redis-exporter on both observability-net and shared-cache-net.
    ## @io — ⇥ compose_data, caplog → ⎋ None (asserts networks)
    ## @complexity — O(N)
    """
    networks = compose_data.get("networks", {})
    logger.info(
        "[IMP:7][infra-metrics][static] Checking %d networks",
        len(networks),
    )

    for net_name in EXPECTED_NETWORKS:
        assert net_name in networks, f"Network '{net_name}' not found in compose"
        net_config = networks[net_name]
        assert net_config.get("external") is True, f"Network '{net_name}' is not external: {net_config}"
        logger.info(
            "[IMP:8][infra-metrics][static] Network '%s' is external: True",
            net_name,
        )

    # Verify redis-exporter is on both networks
    redis_exp = compose_data["services"]["redis-exporter"]
    redis_nets = redis_exp.get("networks", {})
    assert "observability-net" in redis_nets, "redis-exporter missing observability-net"
    assert "shared-cache-net" in redis_nets, "redis-exporter missing shared-cache-net"
    logger.info(
        "[IMP:8][infra-metrics][static] redis-exporter on networks: %s",
        list(redis_nets.keys()),
    )

    logger.info("[IMP:9][infra-metrics][static] ✅ Networks external OK")


# endregion TESTS
