# GREP_SUMMARY: test-logging-static static-audit loki promtail module-yaml compose healthcheck config
# STRUCTURE: ▶ fixtures(*_path) → ◇ test_module_yaml_contract → ◇ test_compose_profiles → ◇ test_healthcheck_sh_contract → ◇ test_loki_config_valid → ◇ test_promtail_config_valid → ◇ test_module_files_present → ◇ test_docker_compose_test_overlay → ⎋
# region MODULE_CONTRACT
## @purpose  Static audit of logging module configuration — Loki + Promtail.
##           Verifies module.yaml, docker-compose profiles, healthcheck.sh,
##           Loki config, Promtail config, and test overlay contract.
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML and shell files directly.
## @invariants
##   - module.yaml: name=logging, install_type=docker, spool_dir, spool_volume
##   - docker-compose.base.yml: profiles: [logging] on every service
##   - Loki healthcheck uses /usr/bin/loki -version (scratch image — no shell)
##   - Promtail depends_on loki with service_healthy condition
##   - loki-config.yml: auth_enabled: false, filesystem storage
##   - promtail-config.yml: valid YAML with positions config
##   - All module files present: compose files, module.yaml, healthcheck.sh, configs
##   - docker-compose.test.yml: container_name: loki-test, port 13100:3100, restart: "no"
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Static contract checks for logging module — verifies structural integrity
##            of all config files without requiring a running Loki/Promtail stack.
# endregion MODULE_CONTRACT

import logging
import os

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
LOGGING_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "logging")
COMPOSE_BASE = os.path.join(LOGGING_DIR, "docker-compose.base.yml")
COMPOSE_TEST = os.path.join(LOGGING_DIR, "docker-compose.test.yml")
MODULE_YAML = os.path.join(LOGGING_DIR, "module.yaml")
HEALTHCHECK_SH = os.path.join(LOGGING_DIR, "healthcheck.sh")
LOKI_CONFIG = os.path.join(LOGGING_DIR, "config", "loki-config.yml")
PROMTAIL_CONFIG = os.path.join(LOGGING_DIR, "config", "promtail-config.yml")

# ── Expected values ───────────────────────────────────────────────────────────
EXPECTED_MODULE_NAME = "logging"
EXPECTED_INSTALL_TYPE = "docker"
EXPECTED_SPOOL_DIR = "/var/lib/platform/loki-data"
EXPECTED_SPOOL_VOLUME = "loki-data"
EXPECTED_NETWORKS = ["observability-net"]
EXPECTED_REQUIRED_FILES = [
    "docker-compose.base.yml",
    "docker-compose.test.yml",
    "module.yaml",
    "healthcheck.sh",
    os.path.join("config", "loki-config.yml"),
    os.path.join("config", "promtail-config.yml"),
]


# region TESTS


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: module.yaml contract
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_module_yaml_contract(caplog) -> None:
    """module.yaml has required D4 fields for logging.

    ## @purpose — Ensure module metadata matches the D4 schema.
    ## @io — ⇥ MODULE_YAML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(MODULE_YAML), f"module.yaml not found: {MODULE_YAML}"

    with open(MODULE_YAML) as f:
        data = yaml.safe_load(f)

    logger.info(
        "[IMP:8][test_module_yaml] name=%s install_type=%s spool_dir=%s spool_volume=%s",
        data.get("name"),
        data.get("install_type"),
        data.get("spool_dir"),
        data.get("spool_volume"),
    )

    assert data.get("name") == EXPECTED_MODULE_NAME, (
        f"module.yaml name={data.get('name')}, expected {EXPECTED_MODULE_NAME}"
    )
    assert data.get("install_type") == EXPECTED_INSTALL_TYPE, (
        f"module.yaml install_type={data.get('install_type')}, expected {EXPECTED_INSTALL_TYPE}"
    )
    assert data.get("spool_dir") == EXPECTED_SPOOL_DIR, (
        f"module.yaml spool_dir={data.get('spool_dir')}, expected {EXPECTED_SPOOL_DIR}"
    )
    assert data.get("spool_volume") == EXPECTED_SPOOL_VOLUME, (
        f"module.yaml spool_volume={data.get('spool_volume')}, expected {EXPECTED_SPOOL_VOLUME}"
    )

    logger.critical(
        "[IMP:9][test_module_yaml] ✅ module.yaml contract OK: name=%s, spool=%s", data["name"], data["spool_volume"]
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: docker-compose.base.yml profiles, healthcheck list, depends_on
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_compose_profiles(caplog) -> None:
    """docker-compose.base.yml has profiles: [logging], correct healthcheck test list, and depends_on.

    ## @purpose — Pluggability contract per core/modules/AGENTS.md.
    ##            Loki healthcheck: /usr/bin/loki -version (scratch image workaround).
    ##            Promtail depends_on loki with service_healthy condition.
    ## @io — ⇥ COMPOSE_BASE → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(N) where N = services in compose
    """
    assert os.path.exists(COMPOSE_BASE), f"compose base not found: {COMPOSE_BASE}"

    with open(COMPOSE_BASE) as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "loki" in services, "Missing loki service"
    assert "promtail" in services, "Missing promtail service"

    # Profiles check: every service must have profiles: [logging]
    for svc_name, svc in services.items():
        profiles = svc.get("profiles", [])
        logger.info("[IMP:8][test_compose_profiles] %s profiles=%s", svc_name, profiles)
        assert "logging" in profiles, f"Service {svc_name} missing profile 'logging', got: {profiles}"

    # Loki healthcheck test list: must contain "/usr/bin/loki" and "-version"
    loki_hc = services["loki"].get("healthcheck")
    assert loki_hc is not None, "loki service missing healthcheck"
    hc_test = loki_hc.get("test", [])
    logger.info("[IMP:8][test_compose_profiles] loki healthcheck test=%s", hc_test)
    assert "/usr/bin/loki" in hc_test, f"loki healthcheck test missing '/usr/bin/loki', got: {hc_test}"
    assert "-version" in hc_test, f"loki healthcheck test missing '-version', got: {hc_test}"

    # Promtail depends_on loki with service_healthy condition
    promtail_depends = services["promtail"].get("depends_on", {})
    logger.info("[IMP:8][test_compose_profiles] promtail depends_on=%s", promtail_depends)
    assert "loki" in promtail_depends, "promtail missing depends_on loki"
    assert promtail_depends["loki"].get("condition") == "service_healthy", (
        f"promtail depends_on loki condition={promtail_depends['loki'].get('condition')}, expected service_healthy"
    )

    # Network check: both services on observability-net
    for svc_name in ("loki", "promtail"):
        networks = services[svc_name].get("networks", [])
        assert "observability-net" in networks, f"Service {svc_name} missing observability-net, got: {networks}"

    logger.critical(
        "[IMP:9][test_compose_profiles] ✅ All services have profiles: [logging], correct healthcheck + depends_on"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: healthcheck.sh contract
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_healthcheck_sh_contract(caplog) -> None:
    """healthcheck.sh exists, sources lib/healthcheck.sh, has check commands.

    ## @purpose — Module healthcheck contract per core/modules/AGENTS.md.
    ## @io — ⇥ HEALTHCHECK_SH → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(HEALTHCHECK_SH), f"healthcheck.sh not found: {HEALTHCHECK_SH}"

    # Check executable
    is_exec = os.access(HEALTHCHECK_SH, os.X_OK)
    logger.info("[IMP:8][test_healthcheck_sh] executable=%s", is_exec)
    assert is_exec, f"healthcheck.sh must be executable: {HEALTHCHECK_SH}"

    with open(HEALTHCHECK_SH) as f:
        content = f.read()

    # Must source lib/healthcheck.sh
    assert "source" in content and "lib/healthcheck.sh" in content, (
        "healthcheck.sh must source ../../lib/healthcheck.sh"
    )

    # Must have default mode container checks for loki and promtail
    assert "loki" in content and "promtail" in content, "healthcheck.sh must check loki and promtail containers"

    # Must have deep mode with check_http for Loki
    assert 'check_http "http://127.0.0.1:3100/ready"' in content, (
        "healthcheck.sh deep mode must check Loki /ready endpoint"
    )

    # Must have deep mode with check_docker_health for promtail
    assert 'check_docker_health "promtail"' in content, (
        "healthcheck.sh deep mode must check promtail via docker inspect"
    )

    logger.critical("[IMP:9][test_healthcheck_sh] ✅ healthcheck.sh contract OK: executable, sourced lib, deep mode")


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: loki-config.yml is valid YAML with auth_enabled: false
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_loki_config_valid(caplog) -> None:
    """loki-config.yml is valid YAML with auth_enabled: false.

    ## @purpose — Loki config must parse correctly; auth is disabled for internal use.
    ## @io — ⇥ LOKI_CONFIG → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(LOKI_CONFIG), f"loki-config.yml not found: {LOKI_CONFIG}"

    with open(LOKI_CONFIG) as f:
        data = yaml.safe_load(f)

    assert "auth_enabled" in data, "loki-config.yml missing 'auth_enabled'"
    assert data["auth_enabled"] is False, f"loki-config.yml auth_enabled={data['auth_enabled']}, expected False"

    assert "server" in data, "loki-config.yml missing 'server' section"
    assert "storage_config" in data, "loki-config.yml missing 'storage_config' section"
    assert "compactor" in data, "loki-config.yml missing 'compactor' section"

    logger.info(
        "[IMP:8][test_loki_config] auth_enabled=%s, server.http_listen_port=%s",
        data["auth_enabled"],
        data["server"].get("http_listen_port"),
    )

    logger.critical("[IMP:9][test_loki_config] ✅ loki-config.yml valid: auth_enabled=false")


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: promtail-config.yml is valid YAML with positions config
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_promtail_config_valid(caplog) -> None:
    """promtail-config.yml is valid YAML with required sections.

    ## @purpose — Promtail config must parse correctly; positions config is mandatory.
    ## @io — ⇥ PROMTAIL_CONFIG → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(PROMTAIL_CONFIG), f"promtail-config.yml not found: {PROMTAIL_CONFIG}"

    with open(PROMTAIL_CONFIG) as f:
        data = yaml.safe_load(f)

    assert "positions" in data, "promtail-config.yml missing 'positions' section"
    assert "filename" in data["positions"], "promtail-config.yml positions missing 'filename'"
    assert isinstance(data["positions"]["filename"], str), "promtail-config.yml positions.filename must be a string"
    assert len(data["positions"]["filename"]) > 0, "promtail-config.yml positions.filename must not be empty"

    assert "clients" in data, "promtail-config.yml missing 'clients' section"
    assert len(data["clients"]) > 0, "promtail-config.yml has empty clients list"

    assert "scrape_configs" in data, "promtail-config.yml missing 'scrape_configs' section"
    assert len(data["scrape_configs"]) >= 2, (
        f"promtail-config.yml has {len(data['scrape_configs'])} scrape configs, expected at least 2"
    )

    logger.info(
        "[IMP:8][test_promtail_config] positions.filename=%s, %d clients, %d scrape configs",
        data["positions"]["filename"],
        len(data["clients"]),
        len(data["scrape_configs"]),
    )

    logger.critical("[IMP:9][test_promtail_config] ✅ promtail-config.yml valid: positions config OK")


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: All module files present
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_module_files_present(caplog) -> None:
    """All required logging module files exist on disk.

    ## @purpose — Ensure no module files were accidentally removed or renamed.
    ## @io — ⇥ LOGGING_DIR + EXPECTED_REQUIRED_FILES → ⎋ None (asserts)
    ## @complexity — O(N) where N = required files
    """
    missing = []
    for rel_path in EXPECTED_REQUIRED_FILES:
        full_path = os.path.join(LOGGING_DIR, rel_path)
        exists = os.path.exists(full_path)
        logger.info("[IMP:8][test_module_files] %s: exists=%s", rel_path, exists)
        if not exists:
            missing.append(rel_path)

    assert not missing, f"Missing required module files: {missing}"

    logger.critical("[IMP:9][test_module_files] ✅ All %d required module files present", len(EXPECTED_REQUIRED_FILES))


# ══════════════════════════════════════════════════════════════════════════════
# Test 7: docker-compose.test.yml test overlay
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_docker_compose_test_overlay(caplog) -> None:
    """docker-compose.test.yml has correct test overlay settings.

    ## @purpose — Test overlay contract: container_name with -test suffix,
    ##            shifted Loki port 13100, restart: "no" for all services.
    ## @io — ⇥ COMPOSE_TEST → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(COMPOSE_TEST), f"compose test overlay not found: {COMPOSE_TEST}"

    with open(COMPOSE_TEST) as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})

    # Loki test service
    assert "loki" in services, "Missing loki service in test overlay"
    loki_svc = services["loki"]
    assert loki_svc.get("container_name") == "loki-test", (
        f"loki container_name={loki_svc.get('container_name')}, expected loki-test"
    )
    assert loki_svc.get("restart") == "no", f"loki restart={loki_svc.get('restart')}, expected 'no'"
    ports = loki_svc.get("ports", [])
    assert "127.0.0.1:13100:3100" in ports, f"loki ports={ports}, expected to contain 127.0.0.1:13100:3100"

    # Promtail test service
    assert "promtail" in services, "Missing promtail service in test overlay"
    promtail_svc = services["promtail"]
    assert promtail_svc.get("container_name") == "promtail-test", (
        f"promtail container_name={promtail_svc.get('container_name')}, expected promtail-test"
    )
    assert promtail_svc.get("restart") == "no", f"promtail restart={promtail_svc.get('restart')}, expected 'no'"

    logger.info(
        "[IMP:8][test_test_overlay] loki container=%s port=%s restart=%s",
        loki_svc.get("container_name"),
        ports,
        loki_svc.get("restart"),
    )

    logger.critical("[IMP:9][test_test_overlay] ✅ docker-compose.test.yml overlay contract OK")


# endregion TESTS
