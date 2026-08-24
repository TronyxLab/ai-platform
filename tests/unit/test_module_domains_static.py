# GREP_SUMMARY: test-module-domains-static static-audit module-domains clickhouse logging monitoring infra-metrics litellm pgbouncer redis contract parametrized
# STRUCTURE: ▶ _MODULE_DOMAINS (7 доменов) → ◇ contract-level (parametrize: compose profiles + healthcheck.sh lib) → ◇ implementation-level (доменные config-проверки: XML/YAML/JSON/dashboards/prometheus) → ⎋ IMP:9 PASS
# region MODULE_CONTRACT
## @purpose  Единый static-аудит конфигураций 7 observability-модулей: clickhouse, logging,
##           monitoring, infra-metrics, litellm, pgbouncer (postgres), redis. Консолидировано
##           (DevPlan 139 W3 T3, 7 static/unit-пар → 1 файл) из test_clickhouse_static.py,
##           test_logging_static.py, test_monitoring_static.py, test_infra_metrics_static.py,
##           test_litellm_static.py, test_pgbouncer_static.py, test_redis_static.py.
## @scope    Два уровня проверок в одном месте:
##           (1) contract-level — параметризованные по домену: compose profiles на всех
##               сервисах, healthcheck.sh существует/executable/sources lib;
##           (2) implementation-level — доменные проверки конфигов (XML валидность,
##               loki/prometheus/grafana YAML, dashboards JSON, alert-rules,
##               redis cache-only, pgbouncer DATABASE_URLS, litellm model_list, образы).
##               config.alloy-проверки — в tests/unit/test_log_collector_module.py (010 T3.1).
##           module.yaml контракт (name/install_type/доменные поля) — в
##           tests/test_module_yaml_contract_static.py (T2, параметризованный).
## @invariants
##   - Все тесты @pytest.mark.static_audit + @ldd_trajectory — без Docker
##   - Контракт-уровень параметризован по домену (profile == module name)
##   - Каждая доменная проверка сохраняет ВСЕ assertions оригинала (AC W3e: покрытие не падает)
##   - tmp_path-изоляция для копий compose (pgbouncer/redis) — xdist-безопасность
## @rationale 7 static-файлов с ~70% идентичной структурой (module_yaml_contract/compose_profiles/
##            healthcheck_sh) — дубль без добавочной обнаруживаемости. Параметризация контракт-уровня
##            устраняет дублирование; доменные проверки остаются рядом с контрактом.
## @changes  2026-08-05 | DevPlan 139 W3 T3 — создан (консолидация 7 static/unit-пар, файлы удалены)
# endregion MODULE_CONTRACT

import json
import logging
import os
import re
import shutil
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from _conftest.ldd import ldd_trajectory

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

# ── Общие пути ─────────────────────────────────────────────────────────────
PROJECT_ROOT = repo_root()
MODULES_DIR = Path(PROJECT_ROOT) / "core" / "modules"


# region YAML_LOADER_OVERRIDE
# ⚠️ TRAP[DECISION] · 2026-07-22 · LOW · Register !override tag for Compose YAML parsing
# · Rejected: docker compose config (requires Docker daemon, breaks static-only invariant)
# · Reason: !override is compose-extension for merge-override; treating value as-is
#   preserves semantics for static analysis purposes.
# · Rev: if compose introduces new tags → extend constructor
## @purpose — Custom SafeLoader for docker-compose files using !override merge-override tags
_ComposeLoader = type("_ComposeLoader", (yaml.SafeLoader,), {})


def _compose_override_constructor(loader: yaml.Loader, node: yaml.Node) -> object:
    """Construct !override tagged value — compose merge-override, treat as-is."""
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


_ComposeLoader.add_constructor("!override", _compose_override_constructor)
# endregion YAML_LOADER_OVERRIDE


def _module_dir(module: str) -> str:
    """Absolute path to a module directory under core/modules/."""
    return Path(MODULES_DIR) / module


# ═══════════════════════════════════════════════════════════════════════════
# CONTRACT LEVEL (параметризовано по домену, DevPlan 139 W3 T3)
# ═══════════════════════════════════════════════════════════════════════════
# 9 static/unit-пар: clickhouse, node-metrics, service-exporters, litellm, log-collector,
# logging, monitoring, postgres (pgbouncer), redis. Контракт: compose profiles + healthcheck.sh.

_MODULE_DOMAINS: list[dict] = [
    {"id": "clickhouse", "module_dir": "clickhouse", "profile": "clickhouse"},
    {"id": "node-metrics", "module_dir": "node-metrics", "profile": "node-metrics"},
    {"id": "service-exporters", "module_dir": "service-exporters", "profile": "service-exporters"},
    {"id": "litellm", "module_dir": "litellm", "profile": "litellm"},
    {"id": "log-collector", "module_dir": "log-collector", "profile": "log-collector"},
    {"id": "logging", "module_dir": "logging", "profile": "logging"},
    {"id": "monitoring", "module_dir": "monitoring", "profile": "monitoring"},
    {"id": "pgbouncer", "module_dir": "postgres", "profile": "postgres"},
    {"id": "redis", "module_dir": "redis", "profile": "redis"},
]


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize("domain", _MODULE_DOMAINS, ids=lambda d: d["id"])
def test_compose_services_have_profile(domain: dict, caplog) -> None:
    """docker-compose.base.yml: каждый сервис имеет profiles: [<module>].

    ## @purpose — Pluggability contract per core/modules/AGENTS.md — контракт-уровень
    ##            (консолидирован из 7 доменных test_compose_profiles вариаций).
    ## @io — ⇥ domain {id, module_dir, profile} → ⚡ yaml.safe_load → ⎋ None
    ## @complexity — O(N) where N = services
    """
    compose = Path(_module_dir(domain["module_dir"])) / "docker-compose.base.yml"
    assert Path(compose).exists(), f"compose base not found: {compose}"

    with Path(compose).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    services = data.get("services", {})
    assert services, f"{domain['id']}: no services in compose"

    for svc_name, svc in services.items():
        profiles = svc.get("profiles", [])
        logger.info("[IMP:8][compose-profiles][%s] %s profiles=%s", domain["id"], svc_name, profiles)
        assert domain["profile"] in profiles, (
            f"Service {svc_name} ({domain['id']}) missing profile '{domain['profile']}', got: {profiles}"
        )

    logger.critical(
        "[IMP:9][compose-profiles][%s] ✅ All %d services have profile [%s]",
        domain["id"],
        len(services),
        domain["profile"],
    )


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize("domain", _MODULE_DOMAINS, ids=lambda d: d["id"])
def test_healthcheck_sh_sources_lib(domain: dict, caplog) -> None:
    """healthcheck.sh существует, executable, sources lib/healthcheck.sh.

    ## @purpose — Module healthcheck contract per core/modules/AGENTS.md — контракт-уровень
    ##            (консолидирован из test_healthcheck_sh_exists / test_healthcheck_sh_contract
    ##            / test_clickhouse_healthcheck_contract / test_litellm_healthcheck_sh_exists).
    ## @io — ⇥ domain → ⎋ None
    ## @complexity — O(1)
    """
    healthcheck_sh = Path(_module_dir(domain["module_dir"])) / "healthcheck.sh"
    assert Path(healthcheck_sh).exists(), f"healthcheck.sh not found: {healthcheck_sh}"

    is_exec = os.access(healthcheck_sh, os.X_OK)
    logger.info("[IMP:8][healthcheck-sh][%s] executable=%s", domain["id"], is_exec)
    assert is_exec, f"healthcheck.sh must be executable: {healthcheck_sh}"

    with Path(healthcheck_sh).open(encoding="utf-8") as fh:
        content = fh.read()

    assert "source" in content and "lib/healthcheck.sh" in content, (
        f"{domain['id']}: healthcheck.sh must source ../../lib/healthcheck.sh"
    )
    assert "exit 0" in content, f"{domain['id']}: healthcheck.sh missing 'exit 0' (healthy path)"

    logger.critical("[IMP:9][healthcheck-sh][%s] ✅ healthcheck.sh contract OK (executable, sources lib)", domain["id"])


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE (implementation-level, из test_clickhouse_static.py)
# ═══════════════════════════════════════════════════════════════════════════

CLICKHOUSE_DIR = _module_dir("clickhouse")
CLICKHOUSE_CONFIG_D = Path(CLICKHOUSE_DIR) / "config" / "config.d"
CLICKHOUSE_USERS_D = Path(CLICKHOUSE_DIR) / "config" / "users.d"

# Files in users.d/ auto-generated by ClickHouse Docker entrypoint (protected by .gitignore)
_AUTO_GENERATED_USERS_XML = {"default-user.xml"}


def _collect_xml_files(directory: str, exclude: set[str] | None = None) -> list[Path]:
    """Collect all .xml files in a directory, sorted by name."""
    if not Path(directory).is_dir():
        return []
    exclude = exclude or set()
    return sorted(
        p for p in Path(directory).iterdir() if p.name.endswith(".xml") and p.is_file() and p.name not in exclude
    )


@pytest.mark.static_audit
@ldd_trajectory
def test_clickhouse_config_xml_valid(caplog) -> None:
    """Все ClickHouse XML конфиги (config.d/*.xml, users.d/*.xml) well-formed."""
    xml_files = _collect_xml_files(CLICKHOUSE_CONFIG_D) + _collect_xml_files(CLICKHOUSE_USERS_D)

    if not xml_files:
        pytest.fail("No ClickHouse XML config files found — expected config.d/*.xml and users.d/*.xml")

    logger.info("[IMP:7][clickhouse-xml] Found %d XML config file(s)", len(xml_files))

    for xml_path in xml_files:
        try:
            ET.parse(xml_path)
            logger.info("[IMP:8][clickhouse-xml] Valid XML: %s", Path(xml_path).name)
        except ET.ParseError as exc:
            pytest.fail(f"Malformed XML in {Path(xml_path).name}: {exc}")

    logger.info("[IMP:9][clickhouse-xml] PASS: All %d XML config(s) well-formed", len(xml_files))


@pytest.mark.static_audit
@ldd_trajectory
def test_clickhouse_healthcheck_contract(caplog) -> None:
    """clickhouse/healthcheck.sh: sources lib, exit 0/1, docker exec (не systemctl)."""
    healthcheck_sh = Path(CLICKHOUSE_DIR) / "healthcheck.sh"
    assert Path(healthcheck_sh).is_file(), f"ClickHouse healthcheck.sh not found at {healthcheck_sh}"

    with Path(healthcheck_sh).open(encoding="utf-8") as fh:
        content = fh.read()

    sources_lib = "../../lib/healthcheck.sh" in content
    logger.info("[IMP:7][clickhouse-healthcheck] Sources lib/healthcheck.sh: %s", sources_lib)
    assert sources_lib, "healthcheck.sh does not source ../../lib/healthcheck.sh (module contract violation)"

    has_exit_0 = "exit 0" in content
    logger.info("[IMP:7][clickhouse-healthcheck] Has exit 0 (healthy): %s", has_exit_0)
    assert has_exit_0, "healthcheck.sh missing 'exit 0' — healthy path not defined"

    has_exit_1 = "exit 1" in content
    logger.info("[IMP:7][clickhouse-healthcheck] Has exit 1 (unhealthy): %s", has_exit_1)
    assert has_exit_1, "healthcheck.sh missing 'exit 1' — unhealthy path not defined"

    has_docker_exec = "docker exec" in content
    logger.info("[IMP:7][clickhouse-healthcheck] Has 'docker exec': %s", has_docker_exec)
    assert has_docker_exec, (
        "healthcheck.sh missing 'docker exec' — must use docker exec for container healthcheck "
        "(macOS-compatible, no systemctl)"
    )

    logger.info("[IMP:9][clickhouse-healthcheck] PASS: Healthcheck contract satisfied")


@pytest.mark.static_audit
@ldd_trajectory
def test_clickhouse_module_yaml_structure(caplog) -> None:
    """clickhouse/module.yaml: D4 required keys + name/install_type семантика."""
    module_yaml = Path(CLICKHOUSE_DIR) / "module.yaml"
    assert Path(module_yaml).is_file(), f"ClickHouse module.yaml not found at {module_yaml}"

    with Path(module_yaml).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    required_keys = {
        "name": str,
        "install_type": str,
        "description": str,
        "depends_on": list,
    }

    for key, expected_type in required_keys.items():
        assert key in data, f"module.yaml missing required key '{key}'"
        assert isinstance(data[key], expected_type), (
            f"module.yaml '{key}' expected {expected_type.__name__}, got {type(data[key]).__name__}"
        )
        logger.info("[IMP:8][clickhouse-module-yaml] Key '%s' present with correct type", key)

    assert data["name"] == "clickhouse", f"expected name='clickhouse', got '{data['name']}'"
    assert data["install_type"] == "docker", f"expected install_type='docker', got '{data['install_type']}'"

    has_spool_dir = "spool_dir" in data and isinstance(data["spool_dir"], str)
    has_spool_volume = "spool_volume" in data and isinstance(data["spool_volume"], str)
    logger.info("[IMP:7][clickhouse-module-yaml] spool_dir: %s, spool_volume: %s", has_spool_dir, has_spool_volume)

    logger.info("[IMP:9][clickhouse-module-yaml] PASS: module.yaml structure valid")


@pytest.mark.static_audit
@ldd_trajectory
def test_clickhouse_makefile_template(caplog) -> None:
    """clickhouse/Makefile включает стандартный module.mk шаблон."""
    makefile = Path(CLICKHOUSE_DIR) / "Makefile"
    assert Path(makefile).is_file(), f"ClickHouse Makefile not found at {makefile}"

    with Path(makefile).open(encoding="utf-8") as fh:
        content = fh.read()

    has_module_name = "MODULE_NAME :=" in content or "MODULE_NAME=" in content
    logger.info("[IMP:7][clickhouse-makefile] Defines MODULE_NAME: %s", has_module_name)
    assert has_module_name, "Makefile does not define MODULE_NAME (module contract violation)"

    includes_template = "../../templates/module.mk" in content
    logger.info("[IMP:7][clickhouse-makefile] Includes ../../templates/module.mk: %s", includes_template)
    assert includes_template, "Makefile does not include ../../templates/module.mk (module contract violation)"

    logger.info("[IMP:9][clickhouse-makefile] PASS: Makefile follows module template contract")


def _xml_has_hardcoded_password(content: str) -> bool:
    """True если users.d XML содержит empty/literal password (C901-extraction, security guard)."""
    xml_stripped = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    has_empty = "<password></password>" in xml_stripped or "<password/>" in xml_stripped
    has_literal = bool(re.search(r"<\s*password[^>]*>\s*(?:(?!<\s*/\s*password\s*>).)+", xml_stripped, re.DOTALL))
    return has_empty or has_literal


@pytest.mark.static_audit
@ldd_trajectory
def test_clickhouse_users_xml_no_hardcoded_password(caplog) -> None:
    """НИ один users.d/*.xml не содержит захардкоженного пароля (security guard)."""
    if not Path(CLICKHOUSE_USERS_D).is_dir():
        pytest.skip(f"Clickhouse users.d directory not found: {CLICKHOUSE_USERS_D}")

    xml_files = _collect_xml_files(CLICKHOUSE_USERS_D, exclude=_AUTO_GENERATED_USERS_XML)
    if not xml_files:
        pytest.fail("No ClickHouse users.d/*.xml files found — expected at least 10-users.xml")

    logger.info(
        "[IMP:7][clickhouse-password] Scanning %d file(s) for hardcoded passwords: %s",
        len(xml_files),
        [Path(f).name for f in xml_files],
    )

    # 💼 TRAP[BUSINESS] · 2026-07-12 · HI · No hardcoded password in users.d — security imperative
    # · Source: DevPlan C1 — ClickHouse security regression (post-refactoring audit)
    # · Risk: Hardcoded password overrides CLICKHOUSE_PASSWORD env var → authentication-less access

    any_violation = False

    for xml_path in xml_files:
        with Path(xml_path).open(encoding="utf-8") as fh:
            content = fh.read()
        if _xml_has_hardcoded_password(content):
            any_violation = True
            logger.error(
                "[IMP:9][clickhouse-password] FAIL: %s contains hardcoded/empty password — "
                "this overrides CLICKHOUSE_PASSWORD env var.",
                Path(xml_path).name,
            )

    sole_xml = Path(CLICKHOUSE_USERS_D) / "10-users.xml"
    if Path(sole_xml).is_file():
        with Path(sole_xml).open(encoding="utf-8") as fh:
            content_10 = fh.read()
        xml_stripped_10 = re.sub(r"<!--.*?-->", "", content_10, flags=re.DOTALL)
        has_localhost_only = all(
            ip_pat in xml_stripped_10 for ip_pat in ["<networks>", "<ip>127.0.0.1</ip>", "</networks>"]
        )
        if has_localhost_only:
            logger.warning(
                "[IMP:8][clickhouse-password] WARN: 10-users.xml restricts default user to 127.0.0.1 only. "
                "This blocks Grafana and Prometheus (on observability-net) from accessing ClickHouse."
            )

    if any_violation:
        pytest.fail(
            "[IMP:9][clickhouse-password] FAIL: One or more users.d/*.xml files contain "
            "hardcoded passwords — see LDD log for details."
        )

    logger.info("[IMP:9][clickhouse-password] PASS: No hardcoded password in any users.d/*.xml (security guard)")


# ═══════════════════════════════════════════════════════════════════════════
# NODE-METRICS + SERVICE-EXPORTERS (implementation-level; преемник test_infra_metrics_static.py,
# DevPlan 010 T3.2 split infra-metrics → node-metrics/service-exporters)
# ═══════════════════════════════════════════════════════════════════════════

NODE_METRICS_COMPOSE = Path(_module_dir("node-metrics")) / "docker-compose.base.yml"
SERVICE_EXPORTERS_COMPOSE = Path(_module_dir("service-exporters")) / "docker-compose.base.yml"

NODE_METRICS_EXPECTED_IMAGES = {
    "cadvisor": "ghcr.io/google/cadvisor:v0.60.5@sha256:1eb9bde04dab65b919bc51da9e7cf8eceb40d57e61ac9e93e373100369d90cd6",
    "node-exporter": "prom/node-exporter:v1.12.1@sha256:da83fae85603c4e47e6c68369a7d746e2dda683dc35ea2e234b4f171e0d92798",
}
SERVICE_EXPORTERS_EXPECTED_IMAGES = {
    "nginx-prometheus-exporter": "nginx/nginx-prometheus-exporter:1.5.1@sha256:9f6d963bb2b19d706d401cc3e2c3ea8de2f1c471b96a2156ca45e76f650b1625",
    "redis-exporter": "oliver006/redis_exporter:v1.88.0@sha256:ead15fa913b45314068b9237bb5eff1e97bcb41d63fbe6267befe34667b5f856",
}


def _load_compose(path: str) -> dict:
    """Load docker-compose.base.yml as parsed YAML dict."""
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _strip_cadvisor_default(image: str) -> str:
    """T1.4 (аудит 2026-08-22): cadvisor параметризован ${CADVISOR_IMAGE:-default} — гейт пинит DEFAULT."""
    if image.startswith("${CADVISOR_IMAGE:-") and image.endswith("}"):
        return image[len("${CADVISOR_IMAGE:-") : -1]
    return image


# GUARD-PRESERVE (168): static-replaceable — класс дефекта «healthcheck отсутствует» покрыт статическим слоем
@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize(
    "compose_path,expected",
    [
        ("node", NODE_METRICS_EXPECTED_IMAGES),
        ("service", SERVICE_EXPORTERS_EXPECTED_IMAGES),
    ],
    ids=["node-metrics", "service-exporters"],
)
def test_split_metrics_compose_healthcheck(compose_path: str, expected: dict, caplog) -> None:
    """Каждый сервис node-metrics/service-exporters имеет healthcheck блок."""
    path = NODE_METRICS_COMPOSE if compose_path == "node" else SERVICE_EXPORTERS_COMPOSE
    services = _load_compose(path).get("services", {})

    for svc_name in expected:
        svc = services[svc_name]
        assert "healthcheck" in svc, f"Service '{svc_name}' missing healthcheck block"
        logger.info(
            "[IMP:8][split-metrics][static] Service '%s' has healthcheck: %s", svc_name, svc["healthcheck"].get("test")
        )

    logger.info("[IMP:9][split-metrics][static] ✅ All services have healthcheck")


# GUARD-PRESERVE (168): static-replaceable — класс дефекта «exporter без digest-pin / c ports» покрыт статическим слоем
@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize(
    "compose_key,service,port",
    [
        ("node", "cadvisor", "8080"),
        ("node", "node-exporter", "9100"),
        ("service", "nginx-prometheus-exporter", "9113"),
        ("service", "redis-exporter", "9121"),
        ("service", "postgres-exporter", "9187"),
    ],
    ids=["cadvisor", "node-exporter", "nginx-exporter", "redis-exporter", "postgres-exporter"],
)
def test_split_metrics_image_and_port(compose_key: str, service: str, port: str, caplog) -> None:
    """Образы с digest-pin и host-port маппингом (SERVICE_BIND_HOST — T2.2)."""
    expected = (
        NODE_METRICS_EXPECTED_IMAGES
        if compose_key == "node"
        else dict(
            SERVICE_EXPORTERS_EXPECTED_IMAGES,
            **{
                "postgres-exporter": "quay.io/prometheuscommunity/postgres-exporter:v0.20.1@sha256:4f3d82803c1f99ea5e767890de3557d2479ebbc711f63f2e04c663daa840057a"
            },
        )
    )
    path = NODE_METRICS_COMPOSE if compose_key == "node" else SERVICE_EXPORTERS_COMPOSE
    svc = _load_compose(path)["services"][service]

    image = _strip_cadvisor_default(svc["image"])
    assert image == expected[service], f"{service} image={image}, expected {expected[service]}"
    logger.info("[IMP:8][split-metrics][static] %s image: %s", service, image)

    ports = svc.get("ports", [])
    assert any(port in p for p in ports), f"{service} missing port {port} mapping. Ports: {ports}"
    assert any("SERVICE_BIND_HOST" in p for p in ports), (
        f"{service} ports must bind via ${{SERVICE_BIND_HOST:-127.0.0.1}} (DevPlan 010 T2.2). Ports: {ports}"
    )
    logger.info("[IMP:9][split-metrics][static] ✅ %s image and ports OK", service)


@pytest.mark.static_audit
@ldd_trajectory
def test_split_metrics_networks_external(caplog) -> None:
    """Networks объявлены external: true; redis-exporter на обеих сетях (scrape+cache)."""
    nm = _load_compose(NODE_METRICS_COMPOSE)
    se = _load_compose(SERVICE_EXPORTERS_COMPOSE)

    for data, net_name, src in (
        (nm, "observability-net", "node-metrics"),
        (se, "observability-net", "service-exporters"),
        (se, "shared-cache-net", "service-exporters"),
        (se, "shared-db-net", "service-exporters"),
    ):
        networks = data.get("networks", {})
        assert net_name in networks, f"[{src}] Network '{net_name}' not found in compose"
        assert networks[net_name].get("external") is True, f"[{src}] Network '{net_name}' is not external"
        logger.info("[IMP:8][split-metrics][static] [%s] Network '%s' is external: True", src, net_name)

    redis_nets = se["services"]["redis-exporter"].get("networks", {})
    assert "observability-net" in redis_nets, "redis-exporter missing observability-net"
    assert "shared-cache-net" in redis_nets, "redis-exporter missing shared-cache-net"
    logger.info("[IMP:8][split-metrics][static] redis-exporter on networks: %s", list(redis_nets.keys()))

    logger.info("[IMP:9][split-metrics][static] ✅ Networks external OK")


# ═══════════════════════════════════════════════════════════════════════════
# LITELLM (implementation-level, из test_litellm_static.py)
# ═══════════════════════════════════════════════════════════════════════════

LITELLM_DIR = _module_dir("litellm")
LITELLM_CONFIG = Path(LITELLM_DIR) / "config" / "litellm-config.yml"


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_config_has_required_keys(caplog) -> None:
    """litellm-config.yml: required top-level keys + master_key/database_url refs."""
    assert Path(LITELLM_CONFIG).exists(), f"litellm-config.yml not found: {LITELLM_CONFIG}"
    logger.info("[IMP:7][test_litellm_static] Config found: %s", LITELLM_CONFIG)

    with Path(LITELLM_CONFIG).open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    required_keys = ["general_settings", "model_list", "litellm_settings"]
    for key in required_keys:
        assert key in config, f"Missing required config key: {key}"
        logger.info("[IMP:8][test_litellm_static] Key present: %s", key)

    gs = config["general_settings"]
    assert "master_key" in gs, "general_settings missing master_key"
    assert "os.environ/LITELLM_MASTER_KEY" in str(gs["master_key"]), (
        f"master_key should reference os.environ/LITELLM_MASTER_KEY, got: {gs['master_key']}"
    )
    assert "database_url" in gs, "general_settings missing database_url"
    assert "os.environ/DATABASE_URL" in str(gs["database_url"]), (
        f"database_url should reference os.environ/DATABASE_URL, got: {gs['database_url']}"
    )

    logger.info("[IMP:9][test_litellm_static] ✅ Config has all required keys with correct values")


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_model_list_non_empty(caplog) -> None:
    """litellm-config.yml: model_list ≥ 1 entry, каждая с model_name + litellm_params.model."""
    with Path(LITELLM_CONFIG).open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)

    model_list = config.get("model_list", [])
    assert len(model_list) > 0, "model_list is empty — LiteLLM has no models configured"
    logger.info("[IMP:8][test_litellm_static] model_list has %d entries", len(model_list))

    for entry in model_list:
        assert "model_name" in entry, f"model_list entry missing model_name: {entry}"
        assert "litellm_params" in entry, f"model_list entry missing litellm_params: {entry}"
        assert "model" in entry["litellm_params"], (
            f"model_list entry {entry['model_name']} missing litellm_params.model"
        )
        logger.info(
            "[IMP:7][test_litellm_static] Model: %s → %s", entry["model_name"], entry["litellm_params"]["model"]
        )

    logger.info("[IMP:9][test_litellm_static] ✅ model_list validated: %d models", len(model_list))


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_healthcheck_sh_contract(caplog) -> None:
    """litellm/healthcheck.sh: exists, executable, sources lib, uses check_docker_health."""
    healthcheck_sh = Path(LITELLM_DIR) / "healthcheck.sh"
    assert Path(healthcheck_sh).exists(), f"healthcheck.sh not found: {healthcheck_sh}"
    logger.info("[IMP:7][test_litellm_static] healthcheck.sh found: %s", healthcheck_sh)

    st = Path(healthcheck_sh).stat()
    is_exec = bool(st.st_mode & stat.S_IXUSR)
    assert is_exec, f"healthcheck.sh is not executable: {healthcheck_sh} (mode={oct(st.st_mode)})"
    logger.info("[IMP:8][test_litellm_static] healthcheck.sh is executable")

    with Path(healthcheck_sh).open(encoding="utf-8") as fh:
        content = fh.read()
    assert "lib/healthcheck.sh" in content, "healthcheck.sh must source lib/healthcheck.sh"
    assert "check_docker_health" in content, "healthcheck.sh must use check_docker_health"
    logger.info("[IMP:9][test_litellm_static] ✅ healthcheck.sh valid and executable")


@pytest.mark.static_audit
@ldd_trajectory
def test_litellm_module_yaml_has_required_fields(caplog) -> None:
    """litellm/module.yaml: name/install_type/env_requires (LITELLM_MASTER_KEY)/depends_on (postgres)."""
    module_yaml = Path(LITELLM_DIR) / "module.yaml"
    assert Path(module_yaml).exists(), f"module.yaml not found: {module_yaml}"

    with Path(module_yaml).open(encoding="utf-8") as fh:
        mod = yaml.safe_load(fh)

    assert mod.get("name") == "litellm", f"module name should be 'litellm', got: {mod.get('name')}"
    assert mod.get("install_type") == "docker", f"install_type should be 'docker', got: {mod.get('install_type')}"

    env_requires = mod.get("env_requires", [])
    assert "LITELLM_MASTER_KEY" in env_requires, "module.yaml env_requires must include LITELLM_MASTER_KEY"
    logger.info("[IMP:8][test_litellm_static] module.yaml env_requires: %s", env_requires)

    depends_on = mod.get("depends_on", [])
    assert "postgres" in depends_on, "module.yaml depends_on must include postgres"
    logger.info("[IMP:8][test_litellm_static] module.yaml depends_on: %s", depends_on)

    logger.info("[IMP:9][test_litellm_static] ✅ module.yaml validated successfully")


# ═══════════════════════════════════════════════════════════════════════════
# LOGGING (implementation-level, из test_logging_static.py; 010 T3.1: alloy → log-collector)
# ═══════════════════════════════════════════════════════════════════════════

LOGGING_DIR = _module_dir("logging")
LOGGING_COMPOSE = Path(LOGGING_DIR) / "docker-compose.base.yml"
LOGGING_COMPOSE_TEST = Path(LOGGING_DIR) / "docker-compose.test.yml"
LOGGING_LOKI_CONFIG = Path(LOGGING_DIR) / "config" / "loki-config.yml"
LOGGING_EXPECTED_REQUIRED_FILES = [
    "docker-compose.base.yml",
    "docker-compose.test.yml",
    "module.yaml",
    "healthcheck.sh",
    Path("config") / "loki-config.yml",
    Path("config") / "loki-runtime-config.yml",
]


@pytest.mark.static_audit
@ldd_trajectory
def test_logging_compose_contract(caplog) -> None:
    """logging compose: loki healthcheck (/usr/bin/loki -version), networks; alloy НЕ входит (010 T3.1)."""
    assert Path(LOGGING_COMPOSE).exists(), f"compose base not found: {LOGGING_COMPOSE}"

    with Path(LOGGING_COMPOSE).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    services = data.get("services", {})
    assert "loki" in services, "Missing loki service"
    assert "alloy" not in services, "Alloy must be removed from logging (010 T3.1 → log-collector)"

    loki_hc = services["loki"].get("healthcheck")
    assert loki_hc is not None, "loki service missing healthcheck"
    hc_test = loki_hc.get("test", [])
    assert "/usr/bin/loki" in hc_test, f"loki healthcheck test missing '/usr/bin/loki', got: {hc_test}"
    assert "-version" in hc_test, f"loki healthcheck test missing '-version', got: {hc_test}"

    networks = services["loki"].get("networks", [])
    assert "observability-net" in networks, f"Service loki missing observability-net, got: {networks}"

    logger.critical(
        "[IMP:9][test_compose_profiles] ✅ loki healthcheck + observability-net; alloy отсутствует (010 T3.1)"
    )


@pytest.mark.static_audit
@ldd_trajectory
def test_logging_healthcheck_deep(caplog) -> None:
    """logging healthcheck.sh: sources lib + deep mode check_http Loki /ready (env-порт, W10 T10.12)."""
    healthcheck_sh = Path(LOGGING_DIR) / "healthcheck.sh"
    assert Path(healthcheck_sh).exists(), f"healthcheck.sh not found: {healthcheck_sh}"

    with Path(healthcheck_sh).open(encoding="utf-8") as fh:
        content = fh.read()

    assert "loki" in content, "healthcheck.sh must check loki container"
    assert 'check_http "http://127.0.0.1:${LOKI_PORT}/ready"' in content, (
        "healthcheck.sh deep mode must check Loki /ready endpoint (env-параметризованный порт, W10 T10.12)"
    )
    assert "check_docker_health" in content, "Must use check_docker_health"

    logger.critical("[IMP:9][test_healthcheck_sh] ✅ healthcheck.sh contract OK: executable, sourced lib, deep mode")


@pytest.mark.static_audit
@ldd_trajectory
def test_loki_config_valid(caplog) -> None:
    """loki-config.yml: valid YAML с auth_enabled: true (T2.0b tenant-изоляция) + server/storage/compactor."""
    assert Path(LOGGING_LOKI_CONFIG).exists(), f"loki-config.yml not found: {LOGGING_LOKI_CONFIG}"

    with Path(LOGGING_LOKI_CONFIG).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert "auth_enabled" in data, "loki-config.yml missing 'auth_enabled'"
    assert data["auth_enabled"] is True, (
        f"loki-config.yml auth_enabled={data['auth_enabled']}, expected True (DevPlan 010 T2.0b tenant-изоляция)"
    )
    assert "server" in data, "loki-config.yml missing 'server' section"
    assert "storage_config" in data, "loki-config.yml missing 'storage_config' section"
    assert "compactor" in data, "loki-config.yml missing 'compactor' section"

    logger.critical("[IMP:9][test_loki_config] ✅ loki-config.yml valid: auth_enabled=true (T2.0b)")


@pytest.mark.static_audit
@ldd_trajectory
def test_logging_module_files_present(caplog) -> None:
    """Все обязательные файлы logging-модуля на диске (config.alloy перенесён в log-collector)."""
    missing = []
    for rel_path in LOGGING_EXPECTED_REQUIRED_FILES:
        full_path = Path(LOGGING_DIR) / rel_path
        exists = Path(full_path).exists()
        logger.info("[IMP:8][test_module_files] %s: exists=%s", rel_path, exists)
        if not exists:
            missing.append(rel_path)

    assert not missing, f"Missing required module files: {missing}"
    logger.critical(
        "[IMP:9][test_module_files] ✅ All %d required module files present", len(LOGGING_EXPECTED_REQUIRED_FILES)
    )


@pytest.mark.static_audit
@ldd_trajectory
def test_logging_docker_compose_test_overlay(caplog) -> None:
    """docker-compose.test.yml: container_name -test, shifted Loki port 13100, restart: no; alloy-test → log-collector."""
    assert Path(LOGGING_COMPOSE_TEST).exists(), f"compose test overlay not found: {LOGGING_COMPOSE_TEST}"

    with Path(LOGGING_COMPOSE_TEST).open(encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_ComposeLoader)  # ruff: ignore[S506] — ComposeLoader: кастомный safe-парсер для !override-тегов (не произвольный объект-инстанс)

    services = data.get("services", {})

    assert "loki" in services, "Missing loki service in test overlay"
    loki_svc = services["loki"]
    assert loki_svc.get("container_name") == "loki-test", (
        f"loki container_name={loki_svc.get('container_name')}, expected loki-test"
    )
    assert loki_svc.get("restart") == "no", f"loki restart={loki_svc.get('restart')}, expected 'no'"
    ports = loki_svc.get("ports", [])
    assert "127.0.0.1:13100:3100" in ports, f"loki ports={ports}, expected to contain 127.0.0.1:13100:3100"

    assert "alloy" not in services, "alloy-test перенесён в log-collector (010 T3.1)"

    logger.critical("[IMP:9][test_test_overlay] ✅ docker-compose.test.yml overlay contract OK (loki only)")


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING (implementation-level, из test_monitoring_static.py)
# ═══════════════════════════════════════════════════════════════════════════

MONITORING_DIR = _module_dir("monitoring")
MONITORING_COMPOSE = Path(MONITORING_DIR) / "docker-compose.base.yml"
MONITORING_PROMETHEUS_YML = Path(MONITORING_DIR) / "config" / "prometheus.yml.tmpl"
MONITORING_GRAFANA_DIR = Path(MONITORING_DIR) / "config" / "grafana"
MONITORING_DATASOURCES_YML = Path(MONITORING_GRAFANA_DIR) / "datasources.yml"
MONITORING_DASHBOARDS_YML = Path(MONITORING_GRAFANA_DIR) / "dashboards.yml"
MONITORING_DASHBOARDS_DIR = Path(MONITORING_DIR) / "config" / "dashboards"
MONITORING_ALERT_RULES_YML = Path(MONITORING_DIR) / "config" / "alert-rules.yml"
MONITORING_ALERTING_DIR = Path(MONITORING_DIR) / "config" / "alerting"

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
TEMPLATE_DASHBOARDS = ["project-template.json"]


@pytest.mark.static_audit
@ldd_trajectory
def test_monitoring_compose_networks(caplog) -> None:
    """monitoring compose: prometheus/grafana на observability-net, grafana + proxy-net."""
    with Path(MONITORING_COMPOSE).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    services = data.get("services", {})
    assert "prometheus" in services, "Missing prometheus service"
    assert "grafana" in services, "Missing grafana service"

    for svc_name in ("prometheus", "grafana"):
        networks = services[svc_name].get("networks", [])
        assert "observability-net" in networks, f"Service {svc_name} missing observability-net, got: {networks}"

    grafana_nets = services["grafana"].get("networks", [])
    assert "proxy-net" in grafana_nets, f"Grafana missing proxy-net, got: {grafana_nets}"

    logger.info("[IMP:9][test_compose_profiles] ✅ All services have profiles: [monitoring] and networks")


@pytest.mark.static_audit
@ldd_trajectory
def test_monitoring_compose_healthcheck_present(caplog) -> None:
    """Каждый сервис monitoring compose имеет healthcheck (init-контейнеры пропускаются)."""
    with Path(MONITORING_COMPOSE).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    hc = None
    for svc_name, svc in data.get("services", {}).items():
        if svc.get("restart") == "no":
            logger.info("[IMP:8][test_healthcheck] %s init container — skipping healthcheck", svc_name)
            continue
        hc = svc.get("healthcheck")
        logger.info("[IMP:8][test_healthcheck] %s healthcheck=%s", svc_name, hc is not None)
        assert hc is not None, f"Service {svc_name} missing healthcheck"
        assert "test" in hc, f"Service {svc_name} healthcheck missing 'test' command"
        assert "interval" in hc, f"Service {svc_name} healthcheck missing 'interval'"

    logger.info(
        "[IMP:9][test_healthcheck] ✅ All services have healthcheck: interval=%s", hc.get("interval") if hc else None
    )


@pytest.mark.static_audit
@ldd_trajectory
def test_monitoring_healthcheck_deep(caplog) -> None:
    """monitoring healthcheck.sh: sources lib + deep check_http Prometheus/Grafana (env-порты)."""
    healthcheck_sh = Path(MONITORING_DIR) / "healthcheck.sh"
    assert Path(healthcheck_sh).exists(), f"healthcheck.sh not found: {healthcheck_sh}"

    with Path(healthcheck_sh).open(encoding="utf-8") as fh:
        content = fh.read()

    assert "MODE=deep" in content or 'check_http "http://127.0.0.1:' in content, (
        "healthcheck.sh must have deep mode with Prometheus HTTP check"
    )
    assert 'check_http "http://127.0.0.1:${PROMETHEUS_PORT}/-/healthy"' in content, (
        "healthcheck.sh deep mode must check Prometheus /-/healthy (env-параметризованный порт, W10 T10.12)"
    )
    assert 'check_http "http://127.0.0.1:${GRAFANA_PORT}/api/health"' in content, (
        "healthcheck.sh deep mode must check Grafana /api/health (env-параметризованный порт, W10 T10.12)"
    )

    logger.info("[IMP:9][test_healthcheck_sh] ✅ healthcheck.sh contract OK: executable, deep mode")


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_yml_valid(caplog) -> None:
    """prometheus.yml.tmpl: global + scrape_configs sections."""
    assert Path(MONITORING_PROMETHEUS_YML).exists(), f"prometheus.yml not found: {MONITORING_PROMETHEUS_YML}"

    with Path(MONITORING_PROMETHEUS_YML).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert "global" in data, "prometheus.yml missing 'global' section"
    assert "scrape_configs" in data, "prometheus.yml missing 'scrape_configs' section"

    logger.info("[IMP:9][test_prometheus_yml] ✅ prometheus.yml valid")


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_scrape_jobs_present(caplog) -> None:
    """prometheus.yml.tmpl содержит все обязательные scrape jobs."""
    with Path(MONITORING_PROMETHEUS_YML).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    job_names = {cfg.get("job_name") for cfg in data.get("scrape_configs", [])}
    logger.info("[IMP:8][test_scrape_jobs] Found jobs: %s", sorted(job_names))

    missing = [j for j in EXPECTED_SCRAPE_JOBS if j not in job_names]
    assert not missing, f"Missing scrape jobs in prometheus.yml: {missing}"

    logger.info("[IMP:9][test_scrape_jobs] ✅ All %d required scrape jobs present", len(EXPECTED_SCRAPE_JOBS))


@pytest.mark.static_audit
@ldd_trajectory
def test_grafana_datasources_yml(caplog) -> None:
    """Grafana datasources.yml: Prometheus + Loki datasources."""
    assert Path(MONITORING_DATASOURCES_YML).exists(), f"datasources.yml not found: {MONITORING_DATASOURCES_YML}"

    with Path(MONITORING_DATASOURCES_YML).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    datasources = data.get("datasources", [])
    ds_names = [ds.get("name") for ds in datasources]

    logger.info("[IMP:8][test_datasources] Datasources: %s", ds_names)
    assert "Prometheus" in ds_names, f"datasources.yml missing Prometheus datasource, got: {ds_names}"
    assert "Loki" in ds_names, f"datasources.yml missing Loki datasource, got: {ds_names}"

    logger.info("[IMP:9][test_datasources] ✅ datasources.yml has Prometheus + Loki")


@pytest.mark.static_audit
@ldd_trajectory
def test_grafana_dashboards_yml(caplog) -> None:
    """Grafana provisioning dashboards.yml: ≥1 provider."""
    assert Path(MONITORING_DASHBOARDS_YML).exists(), f"dashboards.yml not found: {MONITORING_DASHBOARDS_YML}"

    with Path(MONITORING_DASHBOARDS_YML).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    providers = data.get("providers", [])
    assert len(providers) > 0, "dashboards.yml must have at least one provider"

    logger.info("[IMP:9][test_dashboards_yml] ✅ dashboards.yml valid")


@pytest.mark.static_audit
@ldd_trajectory
def test_dashboards_exist_and_valid_json(caplog) -> None:
    """Все ожидаемые dashboard JSON существуют и валидны (template-файлы пропускаются)."""
    assert Path(MONITORING_DASHBOARDS_DIR).is_dir(), f"Dashboards dir not found: {MONITORING_DASHBOARDS_DIR}"

    present = sorted(p.name for p in Path(MONITORING_DASHBOARDS_DIR).iterdir())
    logger.info("[IMP:8][test_dashboards] Dashboards present: %s", present)

    missing = [d for d in EXPECTED_DASHBOARDS if d not in present]
    assert not missing, f"Missing dashboard files: {missing}"

    missing_tpl = [d for d in TEMPLATE_DASHBOARDS if d not in present]
    assert not missing_tpl, f"Missing template dashboard files: {missing_tpl}"

    for db_file in present:
        if not db_file.endswith(".json"):
            continue
        if db_file in TEMPLATE_DASHBOARDS:
            logger.info("[IMP:8][test_dashboards] %s: template file (skipped JSON validation)", db_file)
            continue
        path = Path(MONITORING_DASHBOARDS_DIR) / db_file
        with Path(path).open(encoding="utf-8") as fh:
            try:
                json.load(fh)
                logger.info("[IMP:8][test_dashboards] %s: valid JSON", db_file)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Dashboard {db_file} is not valid JSON: {exc}")

    logger.info("[IMP:9][test_dashboards] ✅ All %d dashboards present and valid JSON", len(present))


@pytest.mark.static_audit
@ldd_trajectory
def test_alert_rules_yml_valid(caplog) -> None:
    """alert-rules.yml: groups секция с правилами."""
    assert Path(MONITORING_ALERT_RULES_YML).exists(), f"alert-rules.yml not found: {MONITORING_ALERT_RULES_YML}"

    with Path(MONITORING_ALERT_RULES_YML).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert "groups" in data, "alert-rules.yml missing 'groups' section"
    assert len(data["groups"]) > 0, "alert-rules.yml has empty groups"

    total_rules = sum(len(g.get("rules", [])) for g in data["groups"])
    logger.info("[IMP:9][test_alert_rules] ✅ alert-rules.yml valid: %d rules", total_rules)


@pytest.mark.static_audit
@ldd_trajectory
def test_alerting_provisioning_dir(caplog) -> None:
    """Grafana alerting provisioning директория с YAML файлами."""
    assert Path(MONITORING_ALERTING_DIR).is_dir(), f"Alerting provisioning dir not found: {MONITORING_ALERTING_DIR}"

    files = sorted(p.name for p in Path(MONITORING_ALERTING_DIR).iterdir())
    yaml_files = [f for f in files if f.endswith((".yml", ".yaml"))]

    logger.info("[IMP:8][test_alerting_dir] Alerting files: %s", yaml_files)
    assert len(yaml_files) > 0, f"No YAML files in alerting provisioning dir: {MONITORING_ALERTING_DIR}"

    logger.info("[IMP:9][test_alerting_dir] ✅ Alerting dir has %d provisioning files", len(yaml_files))


# ═══════════════════════════════════════════════════════════════════════════
# PGBOUNCER (implementation-level, из test_pgbouncer_static.py)
# ═══════════════════════════════════════════════════════════════════════════

PLATFORM_ENV_PATH = Path(PROJECT_ROOT) / "platform-env.yaml"


@pytest.fixture(scope="module")
def postgres_fixtures(tmp_path_factory):
    """Copy postgres module files to temp dir for isolated testing."""
    src = Path(MODULES_DIR) / "postgres"
    dst = tmp_path_factory.mktemp("pgbouncer_postgres")
    shutil.copytree(src, str(dst), dirs_exist_ok=True)
    dst_str = str(dst)
    return {
        "MODULE_DIR": dst_str,
        "COMPOSE_FILE": Path(dst_str) / "docker-compose.base.yml",
        "MODULE_YAML": Path(dst_str) / "module.yaml",
        "HEALTHCHECK_SH": Path(dst_str) / "healthcheck.sh",
        "READY_CHECK_SH": Path(dst_str) / "ready-check.sh",
    }


@pytest.fixture(scope="module")
def obs_compose_paths(tmp_path_factory):
    """Copy litellm + langfuse compose files to temp dirs — returns list of paths."""
    module_dir = MODULES_DIR
    paths = []
    for mod in ("litellm", "langfuse"):
        src = Path(module_dir) / mod / "docker-compose.base.yml"
        dst_dir = tmp_path_factory.mktemp(f"pgbouncer_obs_{mod}")
        dst = dst_dir / "docker-compose.base.yml"
        shutil.copy2(src, dst)
        paths.append(dst)
    return paths


@pytest.fixture(scope="module")
def hermes_agent_compose_path(tmp_path_factory):
    """Copy hermes-agent docker-compose.base.yml to temp dir."""
    src = Path(MODULES_DIR) / "hermes-agent" / "docker-compose.base.yml"
    dst_dir = tmp_path_factory.mktemp("pgbouncer_hermes_agent")
    dst = dst_dir / "docker-compose.base.yml"
    shutil.copy2(src, dst)
    return dst


def _parse_db_names_from_database_urls(urls_value: str) -> set[str]:
    """Parse database names from DATABASE_URLS env var value (comma-separated URLs)."""
    if not urls_value:
        return set()
    db_names: set[str] = set()
    for url_raw in urls_value.split(","):
        url = url_raw.strip()
        if "/" in url:
            db_name = url.rsplit("/", 1)[-1]
            db_names.add(db_name)
    return db_names


def _extract_databases_from_observability(obs_compose_paths: list[str]) -> set[str]:
    """Extract database names from DATABASE_URL env vars across litellm+langfuse compose files."""
    databases: set[str] = set()
    for obs_compose in obs_compose_paths:
        if not Path(obs_compose).is_file():
            continue

        with Path(obs_compose).open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        services: dict = data.get("services", {}) if isinstance(data, dict) else {}
        for svc_config in services.values():
            if not isinstance(svc_config, dict):
                continue
            databases.update(_extract_db_names_from_env(svc_config.get("environment", {})))

    return databases


def _extract_db_names_from_env(env_definitions: object) -> set[str]:
    """Извлечь имена БД из DATABASE_URL env-сервиса (PLR1702-хелпер).

    ## @io — ⇥ env_definitions (dict|list) → ⎋ set[str] имён БД
    ## @complexity — O(E) где E = env-переменные
    """
    databases: set[str] = set()
    env_defs: dict = {}
    if isinstance(env_definitions, dict):
        env_defs = env_definitions
    elif isinstance(env_definitions, list):
        for item in env_definitions:
            if isinstance(item, str) and "=" in item:
                k, _, v = item.partition("=")
                env_defs[k.strip()] = v.strip()

    for key, value in env_defs.items():
        if key == "DATABASE_URL" and isinstance(value, str):
            clean_value = _resolve_placeholders(value)
            if "@" in clean_value:
                after_at = clean_value.split("@", 1)[1]
                if "/" in after_at:
                    path_part = after_at.split("/", 1)[1]
                    dbname = path_part.split("?")[0].split("#")[0].strip()
                    if dbname:
                        databases.add(dbname)
    return databases


def _resolve_placeholders(clean_value: str) -> str:
    """Раскрыть ${VAR:-fallback} плейсхолдеры в URL (PLR1702-хелпер).

    ## @io — ⇥ clean_value → ⎋ str (URL с подставленными fallback'ами)
    ## @complexity — O(P) где P = плейсхолдеры
    """
    while "${" in clean_value and "}" in clean_value:
        start = clean_value.find("${")
        end = clean_value.find("}", start)
        if end == -1:
            break
        placeholder = clean_value[start : end + 1]
        if ":-" in placeholder:
            fallback = placeholder.split(":-", 1)[1].rstrip("}")
            clean_value = clean_value.replace(placeholder, fallback, 1)
        else:
            clean_value = clean_value.replace(placeholder, "", 1)
    return clean_value


def _get_pgbouncer_env_from_compose(compose_path: str) -> dict[str, str]:
    """Read pgbouncer environment variables from compose file path."""
    with Path(compose_path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    pgb_svc = data.get("services", {}).get("pgbouncer", {})
    return dict(pgb_svc.get("environment", {}))


def _parse_database_urls(env: dict[str, str]) -> set[str]:
    """Parse DATABASE_URLS env var into set of database names."""
    raw = env.get("DATABASE_URLS", "")
    if not raw:
        return set()
    db_names: set[str] = set()
    for url_raw in raw.split(","):
        url = url_raw.strip()
        if "/" in url:
            db_name = url.rsplit("/", 1)[-1]
            db_names.add(db_name)
    return db_names


def _parse_database_urls_detailed(env: dict[str, str]) -> dict[str, dict[str, str]]:
    """Parse DATABASE_URLS into {db_name: {host, port, dbname}}."""
    raw = env.get("DATABASE_URLS", "")
    if not raw:
        return {}
    databases: dict[str, dict[str, str]] = {}
    for url_raw in raw.split(","):
        url = url_raw.strip()
        if "/" not in url:
            continue
        db_name = url.rsplit("/", 1)[-1]
        host = "postgres"
        port = "5432"
        if "@" in url:
            after_at = url.split("@", 1)[1]
            host_part = after_at.split("/", 1)[0] if "/" in after_at else after_at
            if ":" in host_part:
                host, port = host_part.split(":", 1)
            else:
                host = host_part
        databases[db_name] = {"host": host, "port": port, "dbname": db_name}
    return databases


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_section_in_compose(postgres_fixtures, caplog) -> None:
    """pgbouncer service определён в postgres compose."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    assert Path(compose_path).is_file(), f"Compose file not found: {compose_path}"
    data = _load_compose(compose_path)
    services = data.get("services", {})
    has_pgbouncer = "pgbouncer" in services
    logger.critical("[IMP:9][test_pgbouncer][compose_section] ASSERT: pgbouncer in services=%s", has_pgbouncer)
    assert has_pgbouncer, "pgbouncer service not found in postgres compose"


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_healthcheck_port_6432(postgres_fixtures, caplog) -> None:
    """pgbouncer healthcheck использует pg_isready на порту 6432."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    data = _load_compose(compose_path)
    pgbouncer_svc = data.get("services", {}).get("pgbouncer", {})
    hc = pgbouncer_svc.get("healthcheck", {})
    test_cmd = hc.get("test", [])

    if isinstance(test_cmd, list):
        test_str = " ".join(test_cmd)
    else:
        test_str = str(test_cmd)

    has_pg_isready = "pg_isready" in test_str
    has_port_6432 = "6432" in test_str
    logger.critical(
        "[IMP:9][test_pgbouncer][healthcheck_6432] ASSERT: pg_isready=%s port_6432=%s", has_pg_isready, has_port_6432
    )
    assert has_pg_isready, f"pgbouncer healthcheck must use pg_isready, got: {test_str}"
    assert has_port_6432, f"pgbouncer healthcheck must reference port 6432, got: {test_str}"


# GUARD-PRESERVE (168): static-replaceable — класс дефекта «POOL_MODE ≠ transaction» покрыт статическим слоем
@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_pool_mode_in_env(postgres_fixtures, caplog) -> None:
    """POOL_MODE из compose env = transaction (edoburu/pgbouncer)."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    data = _load_compose(compose_path)
    pgbouncer_svc = data.get("services", {}).get("pgbouncer", {})
    pool_mode = pgbouncer_svc.get("environment", {}).get("POOL_MODE", "")

    logger.critical("[IMP:9][test_pgbouncer][pool_mode_env] ASSERT: POOL_MODE=%s (expected transaction)", pool_mode)
    assert pool_mode == "transaction", f"POOL_MODE must be 'transaction', got: '{pool_mode}'"


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_databases_match_clients(postgres_fixtures, obs_compose_paths, caplog) -> None:
    """Wildcard DATABASE_URLS (без имён БД) покрывает клиентов — auth-делегация (D5)."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    data = _load_compose(compose_path)
    pgbouncer_svc = data.get("services", {}).get("pgbouncer", {})
    database_urls = pgbouncer_svc.get("environment", {}).get("DATABASE_URLS", "")

    is_wildcard = database_urls.rstrip().endswith("/")
    pgb_databases = _parse_db_names_from_database_urls(database_urls)
    logger.info(
        "[IMP:7][test_pgbouncer][databases_match_env] pgbouncer DATABASE_URLS databases: %s (wildcard=%s)",
        pgb_databases,
        is_wildcard,
    )

    obs_databases = _extract_databases_from_observability(obs_compose_paths)
    logger.info(
        "[IMP:7][test_pgbouncer][databases_match_env] litellm+langfuse DATABASE_URL databases: %s", obs_databases
    )

    hardcoded = pgb_databases - {""}
    logger.critical(
        "[IMP:9][test_pgbouncer][databases_match_env] ASSERT: wildcard=%s hardcoded=%s clients=%s",
        is_wildcard,
        hardcoded,
        sorted(obs_databases),
    )
    assert is_wildcard, f"pgbouncer DATABASE_URLS must be the wildcard URL (D5): '{database_urls}'"
    assert not hardcoded, f"Hardcoded DB list in DATABASE_URLS (D5 violated): {hardcoded}"
    assert obs_databases >= {"litellm", "langfuse"}, f"client DB references lost: {obs_databases}"


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_no_proxy_includes(hermes_agent_compose_path, caplog) -> None:
    """Hermes-agent compose NO_PROXY fallback ⊇ platform-env.yaml proxy.no_proxy_internal."""
    assert Path(PLATFORM_ENV_PATH).is_file(), f"platform-env.yaml not found: {PLATFORM_ENV_PATH}"
    with Path(PLATFORM_ENV_PATH).open(encoding="utf-8") as fh:
        platform_env = yaml.safe_load(fh)

    proxy_config = platform_env.get("proxy", {})
    no_proxy_internal_raw: str = proxy_config.get("no_proxy_internal", "")
    sot_entries: set[str] = {e.strip() for e in no_proxy_internal_raw.split(",") if e.strip()}
    logger.info("[IMP:8][test_pgbouncer][no_proxy] SoT entries: %s", sorted(sot_entries))

    assert Path(hermes_agent_compose_path).is_file(), f"Hermes-agent compose not found: {hermes_agent_compose_path}"
    with Path(hermes_agent_compose_path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    services: dict = data.get("services", {}) if isinstance(data, dict) else {}
    fallback_value = ""

    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        env_definitions: dict = {}
        raw_env = svc_config.get("environment", {})
        if isinstance(raw_env, dict):
            env_definitions = raw_env
        elif isinstance(raw_env, list):
            for item in raw_env:
                if isinstance(item, str) and "=" in item:
                    k, _, v = item.partition("=")
                    env_definitions[k.strip()] = v.strip()

        for key, value in env_definitions.items():
            if key.upper() == "NO_PROXY" and "${NO_PROXY:-" in str(value):
                fb_match = str(value).split("${NO_PROXY:-", 1)[1].rstrip("}").strip()
                fallback_value = fb_match
                logger.info("[IMP:7][test_pgbouncer][no_proxy] Found NO_PROXY with fallback: '%s'", fallback_value)
                break
        if fallback_value:
            break

    assert fallback_value, "No NO_PROXY with ${NO_PROXY:-fallback} pattern found in hermes-agent compose"

    fallback_entries: set[str] = {e.strip() for e in fallback_value.split(",") if e.strip()}
    missing_entries = sot_entries - fallback_entries

    logger.critical(
        "[IMP:9][test_pgbouncer][no_proxy] ASSERT: fallback=%s, SoT=%s, missing=%s",
        sorted(fallback_entries),
        sorted(sot_entries),
        sorted(missing_entries),
    )
    assert not missing_entries, (
        f"Hermes-agent NO_PROXY fallback missing SoT entries: {sorted(missing_entries)}. "
        f"SoT requires: {sorted(sot_entries)}. Fallback has: {sorted(fallback_entries)}"
    )
    logger.info("[IMP:9][test_pgbouncer][no_proxy] PASS: fallback ⊇ SoT (%d entries)", len(sot_entries))


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_required_databases_present(postgres_fixtures, caplog) -> None:
    """Wildcard DATABASE_URLS покрывает platform/litellm/langfuse — 0 захардкоженных БД (D5)."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    assert Path(compose_path).is_file(), f"Compose file not found: {compose_path}"
    env = _get_pgbouncer_env_from_compose(compose_path)
    database_urls = env.get("DATABASE_URLS", "")
    dbs_from_urls = _parse_database_urls(env)

    is_wildcard = database_urls.rstrip().endswith("/")
    hardcoded = dbs_from_urls - {""}

    logger.info("[IMP:7][test_pgbouncer][required] DATABASE_URLS=%s", database_urls)
    logger.critical("[IMP:9][test_pgbouncer][required] ASSERT: wildcard=%s hardcoded=%s", is_wildcard, hardcoded)
    assert is_wildcard, f"Wildcard DATABASE_URLS required (DevPlan 133 D5): '{database_urls}'"
    assert not hardcoded, f"Required DBs must NOT be hardcoded in DATABASE_URLS (D5): {hardcoded}"


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_each_database_has_host_port_dbname(postgres_fixtures, caplog) -> None:
    """Каждая DATABASE_URLS запись имеет host/port/dbname параметры."""
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    assert Path(compose_path).is_file(), f"Compose file not found: {compose_path}"
    env = _get_pgbouncer_env_from_compose(compose_path)
    databases = _parse_database_urls_detailed(env)

    logger.info("[IMP:7][test_pgbouncer][detailed] Parsed %d database(s) from DATABASE_URLS", len(databases))
    logger.critical("[IMP:9][test_pgbouncer][detailed] ASSERT: %d database(s) with details", len(databases))
    assert len(databases) > 0, "No database entries found in DATABASE_URLS"

    for db_name, params in databases.items():
        assert "host" in params, f"Database '{db_name}' missing 'host' in DATABASE_URLS"
        assert "port" in params, f"Database '{db_name}' missing 'port' in DATABASE_URLS"
        assert "dbname" in params, f"Database '{db_name}' missing 'dbname' in DATABASE_URLS"
        assert params["host"] == "postgres", f"Database '{db_name}' host should be 'postgres', got '{params['host']}'"
        assert params["port"] == "5432", f"Database '{db_name}' port should be '5432', got '{params['port']}'"
        assert params["dbname"] == db_name, (
            f"Database '{db_name}' dbname should match key name, got '{params['dbname']}'"
        )
        logger.info(
            "[IMP:7][test_pgbouncer][detailed] Verified '%s': host=%s, port=%s", db_name, params["host"], params["port"]
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_password_charset_constraint(postgres_fixtures, caplog) -> None:
    """PgBouncer DATABASE_URLS использует ${POSTGRES_PASSWORD} напрямую (charset constraint, no ENCODED)."""
    # 🧪 TRAP[TEST] · 2026-07-21 · Regression: charset constraint guarantees ${POSTGRES_PASSWORD} safe in URL
    compose_path = postgres_fixtures["COMPOSE_FILE"]
    assert Path(compose_path).is_file(), f"Compose file not found: {compose_path}"

    compose_text = Path(compose_path).read_text(encoding="utf-8")

    has_direct_usage = bool(re.search(r"\$\{POSTGRES_PASSWORD[\}:]", compose_text))
    has_encoded = "POSTGRES_PASSWORD_ENCODED" in compose_text

    logger.critical(
        "[IMP:9][test_pgbouncer][charset_constraint] ASSERT: ${POSTGRES_PASSWORD}=%s POSTGRES_PASSWORD_ENCODED=%s",
        has_direct_usage,
        has_encoded,
    )
    assert has_direct_usage, "pgbouncer compose must use ${POSTGRES_PASSWORD} directly in DATABASE_URLS"
    assert not has_encoded, (
        "POSTGRES_PASSWORD_ENCODED must not exist — charset constraint makes it unnecessary. "
        "Remove any ENCODED references."
    )
    logger.info(
        "[IMP:8][test_pgbouncer][charset_constraint] PASS: direct=%s no_encoded=%s", has_direct_usage, not has_encoded
    )


# ═══════════════════════════════════════════════════════════════════════════
# REDIS (implementation-level, из test_redis_static.py)
# ═══════════════════════════════════════════════════════════════════════════

REDIS_MODULE_DIR = _module_dir("redis")
REDIS_INFRA_METRICS_DIR = _module_dir("service-exporters")
REDIS_MONITORING_DIR = _module_dir("monitoring")
REDIS_PROMETHEUS_YML = Path(REDIS_MONITORING_DIR) / "config" / "prometheus.yml.tmpl"
REDIS_DASHBOARDS_DIR = Path(REDIS_MONITORING_DIR) / "config" / "dashboards"


@pytest.fixture(scope="module")
def redis_compose_base_path(tmp_path_factory):
    """Copy redis docker-compose.base.yml to temp dir."""
    src = Path(REDIS_MODULE_DIR) / "docker-compose.base.yml"
    dst_dir = tmp_path_factory.mktemp("redis_static_redis")
    dst = dst_dir / "docker-compose.base.yml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_module_yaml_path(tmp_path_factory):
    """Copy redis module.yaml to temp dir."""
    src = Path(REDIS_MODULE_DIR) / "module.yaml"
    dst_dir = tmp_path_factory.mktemp("redis_static_module")
    dst = dst_dir / "module.yaml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_healthcheck_path(tmp_path_factory):
    """Copy redis healthcheck.sh to temp dir."""
    src = Path(REDIS_MODULE_DIR) / "healthcheck.sh"
    dst_dir = tmp_path_factory.mktemp("redis_static_hc")
    dst = dst_dir / "healthcheck.sh"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_infra_metrics_compose_path(tmp_path_factory):
    """Copy service-exporters docker-compose.base.yml to temp dir (redis-exporter — T3.2)."""
    src = Path(REDIS_INFRA_METRICS_DIR) / "docker-compose.base.yml"
    dst_dir = tmp_path_factory.mktemp("redis_static_infra")
    dst = dst_dir / "docker-compose.base.yml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_prometheus_yml_path(tmp_path_factory):
    """Copy prometheus.yml.tmpl to temp dir (tmpl = single source, DevPlan 116 B3 T3)."""
    src = REDIS_PROMETHEUS_YML
    dst_dir = tmp_path_factory.mktemp("redis_static_prom")
    dst = dst_dir / "prometheus.yml"
    shutil.copy2(src, dst)
    return dst


@pytest.fixture(scope="module")
def redis_dashboards_dir_path(tmp_path_factory):
    """Copy dashboards directory to temp dir for discovery."""
    dst = tmp_path_factory.mktemp("redis_static_dashboards")
    if Path(REDIS_DASHBOARDS_DIR).is_dir():
        for fname in (p.name for p in Path(REDIS_DASHBOARDS_DIR).iterdir()):
            src = Path(REDIS_DASHBOARDS_DIR) / fname
            if Path(src).is_file():
                shutil.copy2(src, dst / fname)
    return str(dst)


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_cache_only_command(redis_compose_base_path, caplog) -> None:
    """Redis command: --appendonly no, --save "", --maxmemory-policy allkeys-lfu."""
    data = load_yaml(redis_compose_base_path)
    redis_svc = data.get("services", {}).get("redis", {})
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


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize("level", ["service", "top"], ids=["service-volumes", "top-level-volumes"])
def test_redis_no_volumes(level: str, redis_compose_base_path, caplog) -> None:
    """Redis compose: НЕТ volumes (ни на сервисе, ни top-level) — cache-only."""
    data = load_yaml(redis_compose_base_path)

    if level == "service":
        redis_svc = data.get("services", {}).get("redis", {})
        has_volumes = "volumes" in redis_svc
        logger.critical("[IMP:9][test_redis][no_volumes] ASSERT: service.volumes present=%s", has_volumes)
        assert not has_volumes, (
            f"Redis service must NOT have volumes (cache-only — no persistence). Found: {redis_svc.get('volumes', [])}"
        )
    else:
        has_top_volumes = "volumes" in data
        logger.critical("[IMP:9][test_redis][top_no_volumes] ASSERT: top-level volumes present=%s", has_top_volumes)
        assert not has_top_volumes, (
            f"Top-level volumes must be absent in redis compose (cache-only). Found: {list(data.get('volumes', {}).keys())}"
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_no_ports(redis_compose_base_path, caplog) -> None:
    """Redis service НЕ имеет ports к host."""
    data = load_yaml(redis_compose_base_path)
    redis_svc = data.get("services", {}).get("redis", {})

    has_ports = "ports" in redis_svc
    logger.critical("[IMP:9][test_redis][no_ports] ASSERT: ports present=%s", has_ports)
    assert not has_ports, f"Redis service must NOT expose ports to host. Found: {redis_svc.get('ports', [])}"


# GUARD-PRESERVE (168): static-replaceable — класс дефекта «redis не изолирован на shared-cache-net» покрыт статическим слоем
@pytest.mark.static_audit
@ldd_trajectory
def test_redis_network_shared_cache_only(redis_compose_base_path, caplog) -> None:
    """Redis только на shared-cache-net."""
    data = load_yaml(redis_compose_base_path)
    redis_svc = data.get("services", {}).get("redis", {})
    networks = redis_svc.get("networks", {})

    net_names = set(networks.keys()) if isinstance(networks, dict) else set()
    logger.critical("[IMP:9][test_redis][network] ASSERT: networks=%s (expected {shared-cache-net})", net_names)
    assert net_names == {"shared-cache-net"}, f"Redis must be on shared-cache-net only, got: {net_names}"


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_image(redis_compose_base_path, caplog) -> None:
    """Redis image: redis:8.8.1-alpine."""
    data = load_yaml(redis_compose_base_path)
    image = data.get("services", {}).get("redis", {}).get("image", "")

    logger.critical("[IMP:9][test_redis][image] ASSERT: image=%s", image)
    assert image.startswith("redis:8.8.1-alpine"), f"Redis image must start with 'redis:7.4-alpine', got: '{image}'"


@pytest.mark.static_audit
@ldd_trajectory
@pytest.mark.parametrize("check", ["no_spool", "env_requires"], ids=["no-spool", "env-requires"])
def test_redis_module_yaml_contract(check: str, redis_module_yaml_path, caplog) -> None:
    """redis module.yaml: spool_dir: none (stateless) + env_requires == [] (cache-only)."""
    data = load_yaml(redis_module_yaml_path)

    if check == "no_spool":
        spool_dir = data.get("spool_dir")
        spool_volume = data.get("spool_volume")
        logger.critical(
            "[IMP:9][test_redis][module_yaml] ASSERT: spool_dir=%s spool_volume=%s", spool_dir, spool_volume
        )
        assert spool_dir == "none", (
            f"redis module.yaml must have spool_dir: none (cache-only, stateless). Found: spool_dir={spool_dir!r}"
        )
        assert not spool_volume, f"redis module.yaml must NOT have spool_volume (cache-only). Found: {spool_volume!r}"
    else:
        env_requires = data.get("env_requires", None)
        logger.critical("[IMP:9][test_redis][module_yaml] ASSERT: env_requires=%s", env_requires)
        assert env_requires == [], f"redis module.yaml env_requires must be empty list, got: {env_requires}"


@pytest.mark.static_audit
@ldd_trajectory
def test_prometheus_redis_exporter_job(redis_prometheus_yml_path, caplog) -> None:
    """prometheus.yml.tmpl: job 'redis-exporter' — file_sd (DevPlan 010 T3.3, static→file_sd).

    ⚠️ DevPlan 010 T3.3: миграция static→file_sd — job_name 'redis-exporter' сохранён 1:1
    (ЛОВУШКА: дашборды/алерты селекторят по job_name); static target redis-exporter:9121
    переехал в рендерер (generate_node_targets single-node fallback,
    core/internal/monitoring/prometheus_targets.py).
    """
    data = load_yaml(redis_prometheus_yml_path)

    scrape_configs = data.get("scrape_configs", [])
    redis_job = None
    for job in scrape_configs:
        if job.get("job_name") == "redis-exporter":
            redis_job = job
            break

    logger.critical("[IMP:9][test_redis][prometheus] ASSERT: redis-exporter job found=%s", redis_job is not None)
    assert redis_job is not None, "prometheus.yml must have a scrape job named 'redis-exporter'"

    # T3.3: job обязан быть file_sd (nodes/redis-exporter.json), не static_configs
    file_sd = redis_job.get("file_sd_configs", [])
    logger.critical("[IMP:9][test_redis][prometheus] ASSERT: redis-exporter file_sd=%s", file_sd)
    assert file_sd, "redis-exporter job must use file_sd_configs (DevPlan 010 T3.3 static→file_sd migration)"
    files = file_sd[0].get("files", [])
    assert "/prometheus-targets/nodes/redis-exporter.json" in files, (
        f"redis-exporter file_sd must reference /prometheus-targets/nodes/redis-exporter.json, got: {files}"
    )


@pytest.mark.static_audit
@ldd_trajectory
def test_infra_metrics_redis_exporter(redis_infra_metrics_compose_path, caplog) -> None:
    """infra-metrics compose: redis-exporter service с shared-cache-net и digest-pin."""
    data = load_yaml(redis_infra_metrics_compose_path)
    services = data.get("services", {})

    has_redis_exporter = "redis-exporter" in services
    logger.critical("[IMP:9][test_redis][infra_metrics] ASSERT: redis-exporter in services=%s", has_redis_exporter)
    assert has_redis_exporter, "infra-metrics docker-compose.base.yml must have 'redis-exporter' service"

    redis_exp = services.get("redis-exporter", {})
    image = redis_exp.get("image", "")
    logger.critical("[IMP:9][test_redis][infra_metrics] ASSERT: image=%s", image)
    assert (
        image
        == "oliver006/redis_exporter:v1.88.0@sha256:ead15fa913b45314068b9237bb5eff1e97bcb41d63fbe6267befe34667b5f856"
    ), f"redis-exporter image must be 'oliver006/redis_exporter:v1.86.0@sha256:...', got: '{image}'"

    networks = redis_exp.get("networks", {})
    net_set = set(networks.keys()) if isinstance(networks, dict) else set()
    has_shared_cache = "shared-cache-net" in net_set
    logger.critical("[IMP:9][test_redis][infra_metrics] ASSERT: shared-cache-net in networks=%s", has_shared_cache)
    assert has_shared_cache, f"redis-exporter must have shared-cache-net in networks. Found: {net_set}"


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_dashboard_exists(redis_dashboards_dir_path, caplog) -> None:
    """redis.json dashboard существует и валиден (≥3 panels)."""
    redis_dash_path = Path(redis_dashboards_dir_path) / "redis.json"

    exists = Path(redis_dash_path).is_file()
    logger.critical("[IMP:9][test_redis][dashboard] ASSERT: redis.json exists=%s", exists)
    assert exists, f"redis.json dashboard not found in {redis_dashboards_dir_path}"

    with Path(redis_dash_path).open(encoding="utf-8") as fh:
        dash_data = json.load(fh)
    assert isinstance(dash_data, dict), "redis.json must be a valid JSON dict"
    assert "panels" in dash_data, "redis.json must have 'panels' key"
    assert len(dash_data["panels"]) >= 3, f"redis.json must have at least 3 panels, got {len(dash_data['panels'])}"
    logger.critical("[IMP:9][test_redis][dashboard] ASSERT: panels=%d", len(dash_data["panels"]))


@pytest.mark.static_audit
@ldd_trajectory
def test_redis_healthcheck_deep_exit(redis_healthcheck_path, caplog) -> None:
    """redis healthcheck.sh: 'exit 0  # ранний выход' в deep block."""
    with Path(redis_healthcheck_path).open(encoding="utf-8") as fh:
        content = fh.read()

    has_exit_0 = "exit 0  # ранний выход" in content
    logger.critical("[IMP:9][test_redis][healthcheck] ASSERT: early exit 0 in deep block=%s", has_exit_0)
    assert has_exit_0, "redis/healthcheck.sh must have 'exit 0  # ранний выход' after deep diagnostics"
