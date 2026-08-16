# GREP_SUMMARY: gate structural-consistency dockerignore-symlink container-name depends-on restart-policy smoke-test-isolation test-overlay networks
# STRUCTURE: ┌4 структурных домена┐ → ◇ (a) .dockerignore symlink → ◇ (b) container_name registry + depends_on → ◇ (c) restart policy (test/base) → ◇ (d) test-overlay изоляция (containers/networks) → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Консолидированный структурный гейт (DevPlan 160 W2 T2.5): четыре
##           структурных домена с доказанно-уникальной защитой:
##           1. DOCKERIGNORE_SYMLINK — каждый docker-модуль имеет .dockerignore → symlink на
##              core/templates/.dockerignore (централизованное управление, анти-дрейф).
##           2. CONTAINER_NAME — container_name registry из base.yml + резолвимость depends_on.
##           3. RESTART_CONSISTENCY — test-compose restart:"no", base-compose restart ∈ {always, unless-stopped}.
##           4. SMOKE_ISOLATION — test-overlay: -test суффиксы, отсутствие коллизий с prod,
##              networks: !override с test-* эквивалентами (DevPlan 04 G5 / DevPlan 017 Option B).
##           §5 LOCAL_PATH_REMOTE (FL6) МИГРИРОВАН в core/internal/static/local_path_remote.py
##           (DevPlan 163 W-C P4, parity files/static_parity_p4.md) и удалён из этого файла.
## @scope    Read-only гейт (make gate MODE=fast, -m gate). Сканы: core/modules/*/compose*.yml,
##           core/templates/.dockerignore.
## @invariants
##   - Docker-модули (discover_docker_modules) имеют .dockerignore symlink на templates/.dockerignore
##   - container_name (или service_name) из base.yml → реестр; каждый depends_on резолвится
##   - test-compose: restart: "no" (carve-out: clickhouse unless-stopped); base-compose: restart ∈ {always, unless-stopped}
##   - test-overlay: все test-container_name заканчиваются -test; 0 коллизий с prod; test-сети
##     начинаются с test-; каждый base-сервис с container_name/networks имеет test-оверрайд
## @rationale  Структурная консистентность модулей — единая точка enforcement.
## @changes  2026-08-12 · DevPlan 160 W2 T2.5 — создан (MERGE 5 гейтов).
## @changes  2026-08-13 · DevPlan 163 W-C P4 — §5 LOCAL_PATH_REMOTE удалён (migrated → local_path_remote.py)
# endregion MODULE_CONTRACT

import logging
import os
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
MODULES_DIR = ROOT / "core" / "modules"
TEMPLATES_DOCKERIGNORE = ROOT / "core" / "templates" / ".dockerignore"

# ══════════════════════════════════════════════════════════════════════════════
# 1. DOCKERIGNORE_SYMLINK (перенесено из test_gate_dockerignore_symlink.py)
# ══════════════════════════════════════════════════════════════════════════════


def _discover_docker_modules() -> list[str]:
    """Discover docker module names via discover_docker_modules (no hardcoded list)."""
    from tests._conftest.audit import discover_docker_modules

    return discover_docker_modules(str(MODULES_DIR))


# region FUNC_test_all_docker_modules_have_dockerignore_symlink
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · .dockerignore symlink contract (T8.3)
# · Scenario: каждый docker-модуль → .dockerignore islink → realpath == templates/.dockerignore
# · Last fail: N/A (preventive)
# · Remove if: централизованный .dockerignore заменён другим механизмом
def test_all_docker_modules_have_dockerignore_symlink(caplog):
    """Все docker-модули имеют .dockerignore → symlink на ../../templates/.dockerignore."""
    failed: list[str] = []
    for module_name in _discover_docker_modules():
        symlink_path = MODULES_DIR / module_name / ".dockerignore"

        if not Path(symlink_path).is_symlink():
            failed.append(f"{module_name}: .dockerignore is not a symlink")
            continue
        target = os.path.realpath(symlink_path)
        if target != os.path.realpath(TEMPLATES_DOCKERIGNORE):
            failed.append(f"{module_name}: .dockerignore symlink points to {target}, expected {TEMPLATES_DOCKERIGNORE}")

    assert not failed, "[IMP:9][gate] dockerignore violations:\n" + "\n".join(failed)
    logger.info(
        "[IMP:9][gate] PASS: все %d модулей имеют корректный .dockerignore symlink", len(_discover_docker_modules())
    )


# endregion FUNC_test_all_docker_modules_have_dockerignore_symlink


# ══════════════════════════════════════════════════════════════════════════════
# 2. CONTAINER_NAME (перенесено из test_gate_container_name_consistency.py)
# ══════════════════════════════════════════════════════════════════════════════


def _extract_container_registry() -> dict[str, str]:
    """Извлекает все container_name: из docker-compose.base.yml → {container_name: module_name}."""
    registry: dict[str, str] = {}
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = compose_file.parent.name
        try:
            data = yaml.safe_load(compose_file.read_text())
        except yaml.YAMLError:
            continue
        if not data or "services" not in data:
            continue
        for service_name, service_config in data["services"].items():
            if service_config is None:
                continue
            cname = service_config.get("container_name", service_name)
            registry[cname] = module_name
    return registry


# region FUNC_TestContainerNameConsistency
class TestContainerNameConsistency:
    """Container name registry + depends_on resolvability (DevPlan 04 TASK-B5)."""

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · container_name registry извлечён
    # · Last fail: N/A (preventive)
    # · Remove if: naming convention changes fundamentally
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_container_names_extracted(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Все container_name из docker-compose.base.yml извлечены в реестр."""
        registry = _extract_container_registry()
        logger.info("[IMP:9][gate][container-registry] Extracted %d container names", len(registry))
        assert len(registry) > 0, (
            "No container names extracted from docker-compose.base.yml files. "
            "Check that compose files exist and are valid YAML."
        )

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · depends_on резолвимы в реестре
    # · Last fail: N/A (preventive)
    # · Remove if: naming convention changes fundamentally
    @pytest.mark.gate
    @ldd_trajectory
    def test_depends_on_references_exist(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Все depends_on (из compose) ссылки разрешимы в container_name registry."""
        registry = _extract_container_registry()
        errors: list[str] = []
        for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
            module_name = compose_file.parent.name
            data = yaml.safe_load(compose_file.read_text())
            if not data or "services" not in data:
                continue
            for service_name, service_config in data["services"].items():
                if service_config is None:
                    continue
                depends_on = service_config.get("depends_on", {}) or {}
                if isinstance(depends_on, list):
                    deps = depends_on
                elif isinstance(depends_on, dict):
                    deps = list(depends_on.keys())
                else:
                    continue
                errors.extend(
                    f"{module_name}/{service_name}: depends_on '{dep}' not found in "
                    f"container_name registry. Registered: {sorted(registry.keys())}"
                    for dep in deps
                    if dep not in registry
                )
        assert not errors, "Unresolved depends_on references:\n" + "\n".join(errors)
        logger.info(
            "[IMP:9][gate][depends-on] All depends_on references resolved (%d compose files checked)",
            len(list(MODULES_DIR.glob("*/docker-compose.base.yml"))),
        )


# endregion FUNC_TestContainerNameConsistency


# ══════════════════════════════════════════════════════════════════════════════
# 3. RESTART_CONSISTENCY (перенесено из test_gate_compose_restart_consistency.py)
# ══════════════════════════════════════════════════════════════════════════════

EXPECTED_TEST_RESTART = "no"
EXPECTED_BASE_RESTART = {"always", "unless-stopped"}

# 🧐 TRAP[DECISION] · 2026-07-24 · — · clickhouse test-compose uses unless-stopped
# · Rejected: единое правило restart (always) для clickhouse test-compose
# · Root: macOS Docker Desktop resource pressure crashes ClickHouse after healthy start.
# ·   Without restart, langfuse fails DNS resolution (clickhouse alias disappears on stop).
# ·   restart: unless-stopped = pragmatic workaround for platform-limited Docker Desktop.
# ·   Linux CI / production VPS not affected — resource pressure only on macOS dev machines.
# · Rev: if ClickHouse stability improves on macOS (native ARM image, Docker Desktop upgrade),
# ·   remove this carve-out and restore restart: "no".
TEST_RESTART_CARVE_OUT: dict[str, set[str]] = {
    "clickhouse": {"unless-stopped"},  # macOS Docker Desktop instability
}


def _load_compose(path: Path) -> dict | None:
    """Load compose YAML or return None on error."""
    try:
        data = load_yaml(path)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, yaml.YAMLError):
        return None


def _get_service_restart(svc_def: dict) -> str:
    """Get restart policy from a service definition."""
    if not isinstance(svc_def, dict):
        return ""
    r = svc_def.get("restart", "")
    return str(r) if r else ""


# region FUNC_TestComposeRestartConsistency
class TestComposeRestartConsistency:
    """Gate: test-compose restart: "no", base-compose restart: unless-stopped|always (DevPlan 033 W3-E4)."""

    # 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · test-compose restart: "no" (P08)
    # · Scenario: все core/modules/*/docker-compose.test.yml → restart == "no" (carve-out clickhouse)
    # · Last fail: N/A (new test)
    # · Remove if: test-compose contract changed or modules removed
    @pytest.mark.gate
    @ldd_trajectory
    def test_test_compose_restart_no(self, caplog):  # ruff: ignore[ARG002]
        """All test-compose services have restart: 'no' (P08 — test isolation)."""
        violations: list[str] = []
        test_files = sorted(MODULES_DIR.glob("*/docker-compose.test.yml"))
        for test_path in test_files:
            module_name = test_path.parent.name
            compose = _load_compose(test_path)
            if compose is None:
                violations.append(f"{module_name}: cannot parse test-compose")
                continue
            services = compose.get("services", {}) or {}
            for svc_name, svc_def in services.items():
                restart = _get_service_restart(svc_def)
                module_carve_out = TEST_RESTART_CARVE_OUT.get(module_name, set())
                if restart != EXPECTED_TEST_RESTART and restart not in module_carve_out:
                    violations.append(
                        f"{module_name}/{svc_name}: test-compose has restart: "
                        f"'{restart or '<missing>'}' — expected 'no'"
                    )
        assert not violations, (
            f"[restart_consistency] Test-compose restart violations ({len(violations)}):\n" + "\n".join(violations)
        )
        logger.info(
            "[IMP:9][restart_consistency] Test-compose: %d files checked — все restart:'no'",
            len(test_files),
        )

    # 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · base-compose restart production (drift detection)
    # · Scenario: все core/modules/*/docker-compose.base.yml → restart ∈ {always, unless-stopped}
    # · Carve-out: init/one-shot сервисы с restart: "no" — accepted
    # · Last fail: N/A (new test)
    # · Remove if: base-compose contract changed
    @pytest.mark.gate
    @ldd_trajectory
    def test_base_compose_restart_production(self, caplog):  # ruff: ignore[ARG002]
        """All base-compose services have restart: unless-stopped or always (production resilience)."""
        violations: list[str] = []
        base_files = sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))
        for base_path in base_files:
            module_name = base_path.parent.name
            compose = _load_compose(base_path)
            if compose is None:
                violations.append(f"{module_name}: cannot parse base-compose")
                continue
            services = compose.get("services", {}) or {}
            for svc_name, svc_def in services.items():
                restart = _get_service_restart(svc_def)
                if restart == "no":
                    continue  # init/one-shot carve-out
                if restart not in EXPECTED_BASE_RESTART:
                    violations.append(
                        f"{module_name}/{svc_name}: base-compose has restart: "
                        f"'{restart or '<missing>'}' — expected 'unless-stopped' or 'always'"
                    )
        assert not violations, (
            f"[restart_consistency] Base-compose restart violations ({len(violations)}):\n" + "\n".join(violations)
        )
        logger.info(
            "[IMP:9][restart_consistency] Base-compose: %d files checked — restart ∈ {always, unless-stopped}",
            len(base_files),
        )


# endregion FUNC_TestComposeRestartConsistency


# ══════════════════════════════════════════════════════════════════════════════
# 4. SMOKE_ISOLATION (перенесено из test_gate_smoke_test_isolation.py)
# ══════════════════════════════════════════════════════════════════════════════


def _yaml_override_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> list | dict:
    """YAML constructor for !override tag — returns the value unchanged (both list and mapping forms)."""
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


yaml.add_constructor("!override", _yaml_override_constructor, Loader=yaml.SafeLoader)

# DevPlan 017 — network isolation: prod → test network mapping
PROD_TO_TEST_NET_MAP: dict[str, str] = {
    "shared-db-net": "test-shared-db-net",
    "shared-cache-net": "test-shared-cache-net",
    "observability-net": "test-observability-net",
    "proxy-net": "test-proxy-net",
    "hermes-agent-net": "test-hermes-agent-net",
    "backup-net": "test-shared-db-net",  # backup-net → test-shared-db-net (D4: no test-backup-net)
}


def _extract_network_names(net_config: list | dict | None) -> set[str]:
    """Extract network names from a service-level networks config (dict-style with aliases or list-style)."""
    if net_config is None:
        return set()
    if isinstance(net_config, list):
        return {str(n) for n in net_config if n is not None}
    if isinstance(net_config, dict):
        return set(net_config.keys())
    return set()


def _get_test_yml_networks() -> dict[str, dict[str, set[str]]]:
    """Извлекает networks из всех docker-compose.test.yml → {module: {service: {net, ...}}}."""
    result: dict[str, dict[str, set[str]]] = {}
    for test_file in sorted(MODULES_DIR.glob("*/docker-compose.test.yml")):
        module_name = test_file.parent.name
        data = yaml.safe_load(test_file.read_text())
        if not data or "services" not in data:
            continue
        svc_networks: dict[str, set[str]] = {}
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            svc_networks[svc_name] = _extract_network_names(svc_config.get("networks", None))
        if svc_networks:
            result[module_name] = svc_networks
    return result


def _get_base_yml_networks() -> dict[str, dict[str, set[str]]]:
    """Извлекает networks из всех docker-compose.base.yml (production)."""
    result: dict[str, dict[str, set[str]]] = {}
    for base_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = base_file.parent.name
        data = yaml.safe_load(base_file.read_text())
        if not data or "services" not in data:
            continue
        svc_networks: dict[str, set[str]] = {}
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            net_names = _extract_network_names(svc_config.get("networks", None))
            if net_names:
                svc_networks[svc_name] = net_names
        if svc_networks:
            result[module_name] = svc_networks
    return result


def _get_test_yml_containers() -> dict[str, list[str]]:
    """Извлекает container_name из всех docker-compose.test.yml → {module: [container_name, ...]}."""
    result: dict[str, list[str]] = {}
    for test_file in sorted(MODULES_DIR.glob("*/docker-compose.test.yml")):
        module_name = test_file.parent.name
        data = yaml.safe_load(test_file.read_text())
        if not data or "services" not in data:
            continue
        containers = []
        for svc_config in data["services"].values():
            if svc_config is None:
                continue
            cname = svc_config.get("container_name", None)
            if cname:
                containers.append(cname)
        if containers:
            result[module_name] = containers
    return result


def _get_production_containers() -> set[str]:
    """Извлекает container_name из всех docker-compose.base.yml (production)."""
    production: set[str] = set()
    for base_file in MODULES_DIR.glob("*/docker-compose.base.yml"):
        data = yaml.safe_load(base_file.read_text())
        if not data or "services" not in data:
            continue
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            production.add(svc_config.get("container_name", svc_name))
    return production


# region FUNC_TestSmokeTestIsolation
class TestSmokeTestIsolation:
    """Test isolation: -test суффиксы, отсутствие коллизий, networks: !override (DevPlan 04 G5 / 017)."""

    # 🧪 TRAP[TEST] · REGRESSION · каждый docker-модуль имеет test-overlay (AC4 DevPlan-04)
    # · Scenario: module с base.yml (кроме platform-secrets) имеет docker-compose.test.yml
    # · Last fail: никогда (new test)
    # · Remove if: test-overlay модель меняется
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_docker_modules_have_test_overlay(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Все Docker-модули имеют docker-compose.test.yml (AC4 DevPlan-04)."""
        base_files = sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))
        test_files = {f.parent.name for f in MODULES_DIR.glob("*/docker-compose.test.yml")}

        missing_test_overlay: list[str] = []
        for base_file in base_files:
            module_name = base_file.parent.name
            if module_name == "platform-secrets":
                continue
            if module_name not in test_files:
                missing_test_overlay.append(module_name)

        assert len(base_files) >= 11, f"Expected at least 11 Docker modules, found {len(base_files)} base compose files"
        assert not missing_test_overlay, (
            f"Docker modules missing docker-compose.test.yml: {missing_test_overlay}. "
            f"Every Docker module must have a test overlay with -test container_name suffix."
        )
        logger.info("[IMP:9][gate][isolation] All %d Docker modules have test overlay ✓", len(base_files) - 1)

    # 🧪 TRAP[TEST] · REGRESSION · все test-container_name заканчиваются -test
    # · Scenario: scan test.yml container_names → все endswith('-test')
    # · Last fail: никогда (new test)
    # · Remove if: test-суффикс конвенция меняется
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_test_containers_have_test_suffix(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Все контейнеры в test-проекте имеют -test суффикс."""
        test_containers = _get_test_yml_containers()
        errors: list[str] = [
            f"{module_name}: container '{cname}' does not end with '-test'"
            for module_name, containers in sorted(test_containers.items())
            for cname in containers
            if not cname.endswith("-test")
        ]
        container_count = sum(len(c) for c in test_containers.values())
        assert not errors, "Test containers without -test suffix:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] All %d test containers have -test suffix ✓", container_count)

    # 🧪 TRAP[TEST] · REGRESSION · нет коллизий container_name prod ↔ test
    # · Scenario: test-имена ∩ prod-имена == ∅
    # · Last fail: никогда (new test)
    # · Remove if: -test суффикс конвенция меняется
    @pytest.mark.gate
    @ldd_trajectory
    def test_no_container_name_collision(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Нет пересечений container_name между production и test."""
        test_containers = _get_test_yml_containers()
        production = _get_production_containers()

        test_names: set[str] = set()
        for containers in test_containers.values():
            test_names.update(containers)

        conflicts = test_names & production
        assert not conflicts, (
            f"Container name collision between production and test: {conflicts}. "
            f"Test containers must use -test suffix to avoid conflicts."
        )
        logger.info("[IMP:9][gate][isolation] No container name collisions ✓")

    # 🧪 TRAP[TEST] · REGRESSION · base container_name имеет -test оверрайд (R5 anti-survivorship)
    # · Scenario: каждый base-сервис с container_name → test.yml container_name с -test суффиксом;
    #   каждый base-сервис с networks → test.yml networks: !override (все сети test-*)
    # · Last fail: langfuse-redis/prometheus-config-init пропускались (изоляция-гейт ловил только
    #   уже существующее в test.yml)
    # · Remove if: test-overlay модель меняется
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_base_container_names_have_test_override(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Для КАЖДОГО сервиса с container_name/networks в base.yml — test-оверрайд в test.yml."""
        errors: list[str] = []
        net_errors: list[str] = []

        for base_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
            module_name = base_file.parent.name
            test_file = base_file.parent / "docker-compose.test.yml"
            if not test_file.exists():
                continue

            base_data = yaml.safe_load(base_file.read_text())
            test_data = yaml.safe_load(test_file.read_text())

            base_services: dict[str, str] = {}
            if base_data and "services" in base_data:
                for svc_name, svc_cfg in base_data["services"].items():
                    if svc_cfg and svc_cfg.get("container_name"):
                        base_services[svc_name] = svc_cfg["container_name"]

            test_overrides: dict[str, str] = {}
            base_services_with_net: set[str] = set()
            if base_data and "services" in base_data:
                for svc_name, svc_cfg in base_data["services"].items():
                    if svc_cfg and svc_cfg.get("networks") is not None:
                        base_services_with_net.add(svc_name)
            if test_data and "services" in test_data:
                for svc_name, svc_cfg in test_data["services"].items():
                    if svc_cfg and svc_cfg.get("container_name"):
                        test_overrides[svc_name] = svc_cfg["container_name"]

            for svc_name, prod_cname in base_services.items():
                test_cname = test_overrides.get(svc_name)
                if not test_cname:
                    errors.append(
                        f"{module_name}: service '{svc_name}' has container_name "
                        f"'{prod_cname}' in base.yml but NO container_name override in test.yml"
                    )
                elif not test_cname.endswith("-test"):
                    errors.append(
                        f"{module_name}: service '{svc_name}' container_name "
                        f"'{test_cname}' in test.yml does not end with '-test'"
                    )

            test_services_with_net: dict[str, set[str]] = {}
            if test_data and "services" in test_data:
                for svc_name, svc_cfg in test_data["services"].items():
                    if svc_cfg and svc_cfg.get("networks") is not None:
                        test_services_with_net[svc_name] = _extract_network_names(svc_cfg["networks"])

            for svc_name in base_services_with_net:
                if svc_name not in test_services_with_net:
                    net_errors.append(
                        f"{module_name}: service '{svc_name}' has networks in base.yml "
                        f"but NO networks: !override in test.yml (DevPlan 017)"
                    )
                else:
                    net_errors.extend(
                        f"{module_name}: service '{svc_name}' networks contains non-test network '{tnet}' in test.yml"
                        for tnet in test_services_with_net[svc_name]
                        if not tnet.startswith("test-")
                    )

        assert not errors, "Services with container_name in base.yml missing -test override:\n" + "\n".join(errors)
        assert not net_errors, (
            "Services with networks in base.yml missing networks: !override in test.yml:\n" + "\n".join(net_errors)
        )
        logger.info("[IMP:9][gate][isolation] All base container_names have -test override and networks: !override ✓")

    # 🧪 TRAP[TEST] · Regression · новый модуль может получить test.yml с prod-сетью (DevPlan 017 W4.1)
    # · Scenario: scan all test.yml → fail if any network doesn't start with test-
    # · Last fail: never (new test)
    # · Remove if: network isolation model changes (e.g., single shared test network)
    @pytest.mark.gate
    @ldd_trajectory
    def test_no_prod_network_in_test_overlay(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Ни один docker-compose.test.yml не ссылается на production-сети."""
        test_nets = _get_test_yml_networks()
        errors: list[str] = []
        for module_name, services in sorted(test_nets.items()):
            for svc_name, networks in sorted(services.items()):
                for net_name in sorted(networks):
                    if net_name is None or not net_name:
                        continue
                    if not net_name.startswith("test-"):
                        errors.append(
                            f"{module_name}: service '{svc_name}' has non-test network '{net_name}' in test.yml"
                        )
        assert not errors, f"{len(errors)} production network(s) found in test overlay:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] No production networks in test overlay ✓")

    # 🧪 TRAP[TEST] · Regression · test-* сеть не соответствующая prod-сети (DevPlan 017 W4.2)
    # · Scenario: prod-сеть X → test-сеть test-X (PROD_TO_TEST_NET_MAP)
    # · Last fail: never (new test)
    # · Remove if: PROD_TO_TEST_NET_MAP is removed or network model fundamentally changes
    @pytest.mark.gate
    @ldd_trajectory
    def test_test_network_consistency(self, caplog) -> None:  # ruff: ignore[ARG002]
        """Для каждого test-сервиса: prod-сеть X → test-X эквивалент (DevPlan 017 W4.2)."""
        base_nets = _get_base_yml_networks()
        test_nets = _get_test_yml_networks()
        errors: list[str] = []

        for module_name, base_services in sorted(base_nets.items()):
            test_services = test_nets.get(module_name, {})
            for svc_name, prod_networks in sorted(base_services.items()):
                test_networks = test_services.get(svc_name, set())
                if not test_networks:
                    continue  # сервис без test-оверрайда допустим (Docker генерирует имя)
                for prod_net in sorted(prod_networks):
                    expected_test_net = PROD_TO_TEST_NET_MAP.get(prod_net)
                    if expected_test_net is None:
                        errors.append(
                            f"{module_name}: unknown prod network '{prod_net}' "
                            f"in service '{svc_name}' — no mapping in PROD_TO_TEST_NET_MAP"
                        )
                        continue
                    if expected_test_net not in test_networks:
                        errors.append(
                            f"{module_name}: service '{svc_name}' is on prod network "
                            f"'{prod_net}' but test overlay does not include "
                            f"'{expected_test_net}' (has: {sorted(test_networks)})"
                        )

        assert not errors, "Test network consistency violations:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] All test networks are consistent with prod equivalents ✓")


# endregion FUNC_TestSmokeTestIsolation
