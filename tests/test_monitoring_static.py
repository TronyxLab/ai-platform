# GREP_SUMMARY: test-monitoring-static static-audit prometheus grafana module-yaml compose healthcheck dashboards alertrules
# STRUCTURE: ▶ fixtures(*_path) → ◇ test_module_yaml_contract → ◇ test_compose_profiles → ◇ test_compose_healthcheck → ◇ test_healthcheck_sh_exists → ◇ test_prometheus_yml_valid → ◇ test_prometheus_scrape_jobs → ◇ test_grafana_datasources_yml → ◇ test_dashboards_exist → ◇ test_alert_rules_yml → ⎋
# region MODULE_CONTRACT
## @purpose  Static audit of monitoring module configuration — Prometheus + Grafana.
##           Verifies module.yaml, docker-compose, healthcheck.sh, prometheus.yml,
##           Grafana provisioning files, dashboards, and alerting rules.
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML and JSON files directly.
## @invariants
##   - module.yaml: name=monitoring, install_type=docker, env_requires=[GF_SECURITY_ADMIN_PASSWORD, LITELLM_MASTER_KEY]
##   - docker-compose.base.yml: profiles: [monitoring] on every service, healthcheck present
##   - Prometheus image: prom/prometheus:v3.13.1, Grafana image: grafana/grafana:11.6.16
##   - prometheus.yml: valid YAML with scrape_configs
##   - Grafana provisioning files exist: datasources.yml, dashboards.yml
##   - Dashboard JSON files exist under config/dashboards/
##   - alert-rules.yml and alerting/ directory present
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Owner verdict wave-monitoring 2026-07-15: РАБОТАЕТ.
##            All tests are structural contract checks (no runtime dependencies).
# endregion MODULE_CONTRACT

import json
import logging
import os

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
MONITORING_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "monitoring")
COMPOSE_BASE = os.path.join(MONITORING_DIR, "docker-compose.base.yml")
MODULE_YAML = os.path.join(MONITORING_DIR, "module.yaml")
HEALTHCHECK_SH = os.path.join(MONITORING_DIR, "healthcheck.sh")
PROMETHEUS_YML = os.path.join(MONITORING_DIR, "config", "prometheus.yml")
GRAFANA_DIR = os.path.join(MONITORING_DIR, "config", "grafana")
DATASOURCES_YML = os.path.join(GRAFANA_DIR, "datasources.yml")
DASHBOARDS_YML = os.path.join(GRAFANA_DIR, "dashboards.yml")
DASHBOARDS_DIR = os.path.join(MONITORING_DIR, "config", "dashboards")
ALERT_RULES_YML = os.path.join(MONITORING_DIR, "config", "alert-rules.yml")
ALERTING_DIR = os.path.join(MONITORING_DIR, "config", "alerting")
INFRA_METRICS_DIR = os.path.join(PROJECT_ROOT, "core", "modules", "infra-metrics")

# ── Expected values ───────────────────────────────────────────────────────────
EXPECTED_MODULE_NAME = "monitoring"
EXPECTED_INSTALL_TYPE = "docker"
EXPECTED_ENV_REQUIRES = ["GF_SECURITY_ADMIN_PASSWORD", "LITELLM_MASTER_KEY"]
EXPECTED_PROMETHEUS_IMAGE = "prom/prometheus:v3.13.1"
EXPECTED_GRAFANA_IMAGE = "grafana/grafana:11.6.16"
EXPECTED_NETWORKS = ["observability-net", "proxy-net"]
EXPECTED_SCRAPE_JOBS = [
    "prometheus",
    "litellm",
    "cadvisor",
    "node-exporter",
    "nginx-exporter",
    "clickhouse",
    "redis-exporter",
]
EXPECTED_DASHBOARDS = [
    "ai-overview.json",
    "infrastructure.json",
    "llm-usage-breakdown.json",
    "logs-incident-inspector.json",
    "dora-ci-cd.json",
]
TEMPLATE_DASHBOARDS = [
    "project-template.json",
]


# region TESTS


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: module.yaml contract
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_module_yaml_contract(caplog) -> None:
    """module.yaml has required D4 fields for monitoring.

    ## @purpose — Ensure module metadata matches the D4 schema.
    ## @io — ⇥ MODULE_YAML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(MODULE_YAML), f"module.yaml not found: {MODULE_YAML}"

    with open(MODULE_YAML) as f:
        data = yaml.safe_load(f)

    logger.info("[IMP:8][test_module_yaml] name=%s install_type=%s", data.get("name"), data.get("install_type"))

    assert data.get("name") == EXPECTED_MODULE_NAME, (
        f"module.yaml name={data.get('name')}, expected {EXPECTED_MODULE_NAME}"
    )
    assert data.get("install_type") == EXPECTED_INSTALL_TYPE, (
        f"module.yaml install_type={data.get('install_type')}, expected {EXPECTED_INSTALL_TYPE}"
    )
    assert data.get("env_requires") == EXPECTED_ENV_REQUIRES, (
        f"module.yaml env_requires={data.get('env_requires')}, expected {EXPECTED_ENV_REQUIRES}"
    )

    logger.info("[IMP:9][test_module_yaml] ✅ module.yaml contract OK: name=%s", data["name"])


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: docker-compose.base.yml has profiles
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_compose_profiles_and_networks(caplog) -> None:
    """docker-compose.base.yml has profiles: [monitoring] and correct networks.

    ## @purpose — Pluggability contract per core/modules/AGENTS.md.
    ## @io — ⇥ COMPOSE_BASE → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(N) where N = services in compose
    """
    assert os.path.exists(COMPOSE_BASE), f"compose base not found: {COMPOSE_BASE}"

    with open(COMPOSE_BASE) as f:
        data = yaml.safe_load(f)

    services = data.get("services", {})
    assert "prometheus" in services, "Missing prometheus service"
    assert "grafana" in services, "Missing grafana service"

    for svc_name, svc in services.items():
        profiles = svc.get("profiles", [])
        logger.info("[IMP:8][test_compose_profiles] %s profiles=%s", svc_name, profiles)
        assert "monitoring" in profiles, f"Service {svc_name} missing profile 'monitoring', got: {profiles}"

    # Check networks
    for svc_name in ("prometheus", "grafana"):
        networks = services[svc_name].get("networks", [])
        assert "observability-net" in networks, f"Service {svc_name} missing observability-net, got: {networks}"

    # Grafana additionally on proxy-net
    grafana_nets = services["grafana"].get("networks", [])
    assert "proxy-net" in grafana_nets, f"Grafana missing proxy-net, got: {grafana_nets}"

    logger.info("[IMP:9][test_compose_profiles] ✅ All services have profiles: [monitoring] and networks")


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: docker-compose healthcheck present
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_compose_healthcheck_present(caplog) -> None:
    """Each service in docker-compose.base.yml has a healthcheck.

    ## @purpose — Gate contract: all compose services must have HEALTHCHECK.
    ## @io — ⇥ COMPOSE_BASE → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(N)
    """
    with open(COMPOSE_BASE) as f:
        data = yaml.safe_load(f)

    for svc_name, svc in data.get("services", {}).items():
        # Skip init containers — они не требуют healthcheck (run-to-completion)
        if svc.get("restart") == "no":
            logger.info("[IMP:8][test_healthcheck] %s init container — skipping healthcheck", svc_name)
            continue
        hc = svc.get("healthcheck")
        logger.info("[IMP:8][test_healthcheck] %s healthcheck=%s", svc_name, hc is not None)
        assert hc is not None, f"Service {svc_name} missing healthcheck"
        assert "test" in hc, f"Service {svc_name} healthcheck missing 'test' command"
        assert "interval" in hc, f"Service {svc_name} healthcheck missing 'interval'"

    logger.info("[IMP:9][test_healthcheck] ✅ All services have healthcheck: interval=%s", hc.get("interval"))


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: healthcheck.sh exists and sources lib
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_healthcheck_sh_contract(caplog) -> None:
    """healthcheck.sh exists, executable, sources lib/healthcheck.sh, has deep mode.

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

    # Must have deep mode with check_http
    assert "MODE=deep" in content or 'check_http "http://127.0.0.1:9090' in content, (
        "healthcheck.sh must have deep mode with Prometheus HTTP check"
    )
    assert 'check_http "http://127.0.0.1:9090/-/healthy"' in content, (
        "healthcheck.sh deep mode must check Prometheus /-/healthy"
    )
    assert 'check_http "http://127.0.0.1:3000/api/health"' in content, (
        "healthcheck.sh deep mode must check Grafana /api/health"
    )

    logger.info("[IMP:9][test_healthcheck_sh] ✅ healthcheck.sh contract OK: executable, deep mode")


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: prometheus.yml is valid YAML
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_yml_valid(caplog) -> None:
    """prometheus.yml is valid YAML with required top-level keys.

    ## @purpose — Prometheus config must parse correctly.
    ## @io — ⇥ PROMETHEUS_YML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(PROMETHEUS_YML), f"prometheus.yml not found: {PROMETHEUS_YML}"

    with open(PROMETHEUS_YML) as f:
        data = yaml.safe_load(f)

    assert "global" in data, "prometheus.yml missing 'global' section"
    assert "scrape_configs" in data, "prometheus.yml missing 'scrape_configs' section"

    logger.info(
        "[IMP:8][test_prometheus_yml] global.scrape_interval=%s, %d scrape jobs",
        data["global"].get("scrape_interval"),
        len(data["scrape_configs"]),
    )

    logger.info("[IMP:9][test_prometheus_yml] ✅ prometheus.yml valid")


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: prometheus scrape jobs
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_scrape_jobs_present(caplog) -> None:
    """prometheus.yml has all required scrape job names.

    ## @purpose — Ensure no scrape job was accidentally removed.
    ## @io — ⇥ PROMETHEUS_YML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(N) where N = expected jobs
    """
    with open(PROMETHEUS_YML) as f:
        data = yaml.safe_load(f)

    job_names = {cfg.get("job_name") for cfg in data.get("scrape_configs", [])}
    logger.info("[IMP:8][test_scrape_jobs] Found jobs: %s", sorted(job_names))

    missing = [j for j in EXPECTED_SCRAPE_JOBS if j not in job_names]
    assert not missing, f"Missing scrape jobs in prometheus.yml: {missing}"

    logger.info("[IMP:9][test_scrape_jobs] ✅ All %d required scrape jobs present", len(EXPECTED_SCRAPE_JOBS))


# ══════════════════════════════════════════════════════════════════════════════
# Test 7: Grafana datasources.yml
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_grafana_datasources_yml(caplog) -> None:
    """Grafana provisioning datasources.yml is valid YAML with Prometheus + Loki.

    ## @purpose — Grafana must have at least Prometheus and Loki datasources.
    ## @io — ⇥ DATASOURCES_YML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(N) where N = datasources
    """
    assert os.path.exists(DATASOURCES_YML), f"datasources.yml not found: {DATASOURCES_YML}"

    with open(DATASOURCES_YML) as f:
        data = yaml.safe_load(f)

    datasources = data.get("datasources", [])
    ds_names = [ds.get("name") for ds in datasources]

    logger.info("[IMP:8][test_datasources] Datasources: %s", ds_names)

    assert "Prometheus" in ds_names, f"datasources.yml missing Prometheus datasource, got: {ds_names}"
    assert "Loki" in ds_names, f"datasources.yml missing Loki datasource, got: {ds_names}"

    logger.info("[IMP:9][test_datasources] ✅ datasources.yml has Prometheus + Loki")


# ══════════════════════════════════════════════════════════════════════════════
# Test 8: Grafana dashboards.yml
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_grafana_dashboards_yml(caplog) -> None:
    """Grafana provisioning dashboards.yml is valid YAML.

    ## @purpose — Dashboard provisioning file must exist and parse.
    ## @io — ⇥ DASHBOARDS_YML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(DASHBOARDS_YML), f"dashboards.yml not found: {DASHBOARDS_YML}"

    with open(DASHBOARDS_YML) as f:
        data = yaml.safe_load(f)

    providers = data.get("providers", [])
    assert len(providers) > 0, "dashboards.yml must have at least one provider"

    logger.info(
        "[IMP:8][test_dashboards_yml] Providers: %s",
        [p.get("name") for p in providers],
    )

    logger.info("[IMP:9][test_dashboards_yml] ✅ dashboards.yml valid")


# ══════════════════════════════════════════════════════════════════════════════
# Test 9: Dashboard JSON files exist
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_dashboards_exist_and_valid_json(caplog) -> None:
    """All expected dashboard JSON files exist and are valid JSON.

    ## @purpose — Ensure monitoring dashboards are not accidentally removed.
    ## @io — ⇥ DASHBOARDS_DIR → ⎋ None (asserts)
    ## @complexity — O(N) where N = expected dashboards
    """
    assert os.path.isdir(DASHBOARDS_DIR), f"Dashboards dir not found: {DASHBOARDS_DIR}"

    present = sorted(os.listdir(DASHBOARDS_DIR))
    logger.info("[IMP:8][test_dashboards] Dashboards present: %s", present)

    missing = [d for d in EXPECTED_DASHBOARDS if d not in present]
    assert not missing, f"Missing dashboard files: {missing}"

    # Validate template dashboards exist (these are templates with $PROJECT variables, not direct Grafana JSON)
    missing_tpl = [d for d in TEMPLATE_DASHBOARDS if d not in present]
    assert not missing_tpl, f"Missing template dashboard files: {missing_tpl}"

    # Validate each JSON — skip templates (contain $PROJECT vars, not valid Grafana JSON as-is)
    for db_file in present:
        if not db_file.endswith(".json"):
            continue
        if db_file in TEMPLATE_DASHBOARDS:
            logger.info("[IMP:8][test_dashboards] %s: template file (skipped JSON validation)", db_file)
            continue
        path = os.path.join(DASHBOARDS_DIR, db_file)
        with open(path) as f:
            try:
                json.load(f)
                logger.info("[IMP:8][test_dashboards] %s: valid JSON", db_file)
            except json.JSONDecodeError as exc:
                logger.error("[IMP:9][test_dashboards] %s: invalid JSON: %s", db_file, exc)
                pytest.fail(f"Dashboard {db_file} is not valid JSON: {exc}")

    logger.info("[IMP:9][test_dashboards] ✅ All %d dashboards present and valid JSON", len(present))


# ══════════════════════════════════════════════════════════════════════════════
# Test 10: Alert rules file exists
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_alert_rules_yml_valid(caplog) -> None:
    """alert-rules.yml is valid YAML with groups section.

    ## @purpose — Alerting rules must parse correctly.
    ## @io — ⇥ ALERT_RULES_YML → ⚡ yaml.safe_load → ⎋ None (asserts)
    ## @complexity — O(1)
    """
    assert os.path.exists(ALERT_RULES_YML), f"alert-rules.yml not found: {ALERT_RULES_YML}"

    with open(ALERT_RULES_YML) as f:
        data = yaml.safe_load(f)

    assert "groups" in data, "alert-rules.yml missing 'groups' section"
    assert len(data["groups"]) > 0, "alert-rules.yml has empty groups"

    total_rules = sum(len(g.get("rules", [])) for g in data["groups"])
    logger.info(
        "[IMP:8][test_alert_rules] %d groups, %d total rules",
        len(data["groups"]),
        total_rules,
    )

    logger.info("[IMP:9][test_alert_rules] ✅ alert-rules.yml valid: %d rules", total_rules)


# ══════════════════════════════════════════════════════════════════════════════
# Test 11: Alerting provisioning directory exists
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_alerting_provisioning_dir(caplog) -> None:
    """Grafana alerting provisioning directory exists with YAML files.

    ## @purpose — Grafana 11+ uses provisioning/alerting for contact points, etc.
    ## @io — ⇥ ALERTING_DIR → ⎋ None (asserts)
    ## @complexity — O(N)
    """
    assert os.path.isdir(ALERTING_DIR), f"Alerting provisioning dir not found: {ALERTING_DIR}"

    files = sorted(os.listdir(ALERTING_DIR))
    yaml_files = [f for f in files if f.endswith((".yml", ".yaml"))]

    logger.info("[IMP:8][test_alerting_dir] Alerting files: %s", yaml_files)
    assert len(yaml_files) > 0, f"No YAML files in alerting provisioning dir: {ALERTING_DIR}"

    logger.info("[IMP:9][test_alerting_dir] ✅ Alerting dir has %d provisioning files", len(yaml_files))


# endregion TESTS
