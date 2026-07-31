# GREP_SUMMARY: smoke-test isolation container-name conflict parallel up test network-isolation
# STRUCTURE: ▶ test_all_test_containers_have_test_suffix → ◇ test_no_container_name_collision → ◇ test_all_base_container_names_have_test_override → ◇ test_no_prod_network_in_test_overlay → ◇ test_test_network_consistency
# region MODULE_CONTRACT
## @purpose  Smoke test: verify test isolation — no container_name conflicts + network isolation (DevPlan 04 TASK-G5, DevPlan 017)
## @scope    Проверяет -test суффикс во всех test.yml, отсутствие конфликтов с production,
##           отсутствие prod-сетей в test.yml, консистентность test-* сетей с prod-эквивалентами
## @invariants
##   - Все контейнеры в test-проекте имеют -test суффикс
##   - Нет пересечений container_name между production и test
##   - Ни один test.yml не ссылается на production-сети (все должны быть с префиксом test-)
##   - Для каждого сервиса: prod-сеть X → test-сеть test-X
## @rationale Тестовая изоляция через test-overlay (DevPlan 04 DD2) + DNS-alias изоляция (DevPlan 017 Option B)
# endregion MODULE_CONTRACT

import logging

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)


# ── YAML !override tag support ───────────────────────────────────────────────
# Docker Compose extends YAML with !override (array replacement vs concatenation).
# Standard PyYAML doesn't know this tag — register a constructor that returns
# the list as-is (the tag only affects compose merge semantics).
def _yaml_override_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> list | dict:
    """YAML constructor for !override tag — returns the value unchanged.

    Handles BOTH forms used across test overlays:
    - sequence: `networks: !override [test-proxy-net, ...]`
    - mapping:  clickhouse test.yml `networks: !override {test-observability-net: {aliases: [...]}}`
      (mapping form preserves the 'clickhouse' alias for langfuse DNS — TRAP[FIX] 2026-07-22).
    """
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


yaml.add_constructor("!override", _yaml_override_constructor, Loader=yaml.SafeLoader)

MODULES_DIR = repo_root() / "core" / "modules"

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
    """Extract network names from a service-level networks config.

    ## @purpose — Normalise both dict-style (with aliases) and list-style network declarations.
    ##            base.yml uses dict: {net_name: {aliases: [...]}} or list: [net_name, ...].
    ##            test.yml uses !override list: [test-net-name, ...].
    ## @io — ⇥ net_config (list|dict|None) → ⎋ set[str] of network names
    ## @complexity — O(N) where N = networks count
    """
    if net_config is None:
        return set()
    if isinstance(net_config, list):
        return {str(n) for n in net_config if n is not None}
    if isinstance(net_config, dict):
        return set(net_config.keys())
    return set()


def _get_test_yml_networks() -> dict[str, dict[str, set[str]]]:
    """Извлекает networks из всех docker-compose.test.yml.
    Возвращает {module_name: {service_name: {network, ...}}}."""
    result: dict[str, dict[str, set[str]]] = {}
    for test_file in sorted(MODULES_DIR.glob("*/docker-compose.test.yml")):
        module_name = test_file.parent.name
        with open(test_file) as f:
            data = yaml.safe_load(f)
        if not data or "services" not in data:
            continue
        svc_networks: dict[str, set[str]] = {}
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            net_config = svc_config.get("networks", None)
            svc_networks[svc_name] = _extract_network_names(net_config)
        if svc_networks:
            result[module_name] = svc_networks
    return result


def _get_base_yml_networks() -> dict[str, dict[str, set[str]]]:
    """Извлекает networks из всех docker-compose.base.yml (production).
    Возвращает {module_name: {service_name: {network, ...}}}."""
    result: dict[str, dict[str, set[str]]] = {}
    for base_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = base_file.parent.name
        with open(base_file) as f:
            data = yaml.safe_load(f)
        if not data or "services" not in data:
            continue
        svc_networks: dict[str, set[str]] = {}
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            net_config = svc_config.get("networks", None)
            net_names = _extract_network_names(net_config)
            if net_names:
                svc_networks[svc_name] = net_names
        if svc_networks:
            result[module_name] = svc_networks
    return result


def _get_test_yml_containers() -> dict[str, list[str]]:
    """Извлекает container_name из всех docker-compose.test.yml.
    Возвращает {module_name: [container_name, ...]}."""
    result: dict[str, list[str]] = {}
    for test_file in sorted(MODULES_DIR.glob("*/docker-compose.test.yml")):
        module_name = test_file.parent.name
        with open(test_file) as f:
            data = yaml.safe_load(f)
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
        with open(base_file) as f:
            data = yaml.safe_load(f)
        if not data or "services" not in data:
            continue
        for svc_name, svc_config in data["services"].items():
            if svc_config is None:
                continue
            cname = svc_config.get("container_name", svc_name)
            production.add(cname)
    return production


class TestSmokeTestIsolation:
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_docker_modules_have_test_overlay(self, caplog) -> None:
        """Все 11 Docker-модулей имеют docker-compose.test.yml (AC4 DevPlan-04).

        ## @purpose — Enforce that every module with docker-compose.base.yml
        ##            (except platform-secrets = system module) has a test overlay.
        ##            Glob silently skips missing files — this test catches the gap.
        ## @io — ⎋ None (assert side-effect)
        ## @rationale — AC4 from DevPlan-04: "11 modules have test-overlay".
        ##              Without this gate, new Docker modules can slip through
        ##              without test isolation.
        """
        base_files = sorted(MODULES_DIR.glob("*/docker-compose.base.yml"))
        test_files = {f.parent.name for f in MODULES_DIR.glob("*/docker-compose.test.yml")}

        missing_test_overlay: list[str] = []
        for base_file in base_files:
            module_name = base_file.parent.name
            # platform-secrets is a system module, not a Docker service
            if module_name == "platform-secrets":
                continue
            if module_name not in test_files:
                missing_test_overlay.append(module_name)

        total_base = len(base_files)
        total_test = len(test_files)
        logger.info("[IMP:9][gate][isolation] Base compose files: %d, Test overlays: %d", total_base, total_test)
        assert len(base_files) >= 11, f"Expected at least 11 Docker modules, found {len(base_files)} base compose files"
        assert not missing_test_overlay, (
            f"Docker modules missing docker-compose.test.yml: {missing_test_overlay}. "
            f"Every Docker module must have a test overlay with -test container_name suffix."
        )
        logger.info("[IMP:9][gate][isolation] All %d Docker modules have test overlay ✓", total_base - 1)

    @pytest.mark.gate
    @ldd_trajectory
    def test_all_test_containers_have_test_suffix(self, caplog) -> None:
        """Все контейнеры в test-проекте имеют -test суффикс."""
        test_containers = _get_test_yml_containers()
        errors: list[str] = []

        for module_name, containers in sorted(test_containers.items()):
            errors.extend(
                f"{module_name}: container '{cname}' does not end with '-test'. "
                f"All test containers must have -test suffix."
                for module_name, containers in sorted(test_containers.items())
                for cname in containers
                if not cname.endswith("-test")
            )

        container_count = sum(len(c) for c in test_containers.values())
        logger.info("[IMP:9][gate][isolation] Checking %d test containers for -test suffix", container_count)
        assert not errors, "Test containers without -test suffix:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] All %d test containers have -test suffix ✓", container_count)

    @pytest.mark.gate
    @ldd_trajectory
    def test_no_container_name_collision(self, caplog) -> None:
        """Нет пересечений container_name между production и test."""
        test_containers = _get_test_yml_containers()
        production = _get_production_containers()

        test_names: set[str] = set()
        for containers in test_containers.values():
            test_names.update(containers)

        conflicts = test_names & production
        logger.info(
            "[IMP:9][gate][isolation] Test names: %d, Production names: %d, Conflicts: %d",
            len(test_names),
            len(production),
            len(conflicts),
        )
        assert not conflicts, (
            f"Container name collision between production and test: {conflicts}. "
            f"Test containers must use -test suffix to avoid conflicts."
        )
        logger.info("[IMP:9][gate][isolation] No container name collisions ✓")

    @pytest.mark.gate
    @ldd_trajectory
    def test_all_base_container_names_have_test_override(self, caplog) -> None:
        """Для КАЖДОГО сервиса с container_name в base.yml — соответствующий -test оверрайд в test.yml.

        ## @purpose — R5 anti-survivorship: isolation-gate (test_all_test_containers_have_test_suffix)
        ##            проверял только то, что УЖЕ есть в test.yml, но НЕ ловил пропущенные
        ##            сервисы (langfuse-redis, prometheus-config-init). Эта проверка идёт
        ##            от base.yml: если в production-конфиге сервис объявил container_name,
        ##            тестовый overlay ОБЯЗАН переопределить его с -test суффиксом.
        ## @rationale — init/oneshot-контейнеры (prometheus-config-init) тоже конфликтуют
        ##              по container_name с прод-стеком — им тоже нужен -test оверрайд.
        ##              Сервисы без явного container_name (Docker генерирует имя из
        ##              compose project + service name) не конфликтуют — пропускаются.
        """
        errors: list[str] = []

        for base_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
            module_name = base_file.parent.name
            test_file = base_file.parent / "docker-compose.test.yml"
            if not test_file.exists():
                continue

            # Читаем base.yml — собираем сервисы с явным container_name
            with open(base_file) as f:
                base_data = yaml.safe_load(f)
            base_services: dict[str, str] = {}
            if base_data and "services" in base_data:
                for svc_name, svc_cfg in base_data["services"].items():
                    if svc_cfg and svc_cfg.get("container_name"):
                        base_services[svc_name] = svc_cfg["container_name"]

            if not base_services:
                continue

            # Читаем test.yml — собираем container_name оверрайды
            with open(test_file) as f:
                test_data = yaml.safe_load(f)
            test_overrides: dict[str, str] = {}
            if test_data and "services" in test_data:
                for svc_name, svc_cfg in test_data["services"].items():
                    if svc_cfg and svc_cfg.get("container_name"):
                        test_overrides[svc_name] = svc_cfg["container_name"]

            # Проверяем: каждый сервис из base с container_name должен иметь -test оверрайд
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

        logger.info(
            "[IMP:9][gate][isolation] Checked %d modules for base container_name coverage",
            len(list(MODULES_DIR.glob("*/docker-compose.base.yml"))),
        )
        assert not errors, "Services with container_name in base.yml missing -test override:\n" + "\n".join(errors)

        # ── R5 anti-survivorship expansion (DevPlan 017 W4.3): также проверяем networks: !override ──
        net_errors: list[str] = []
        for base_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
            module_name = base_file.parent.name
            test_file = base_file.parent / "docker-compose.test.yml"
            if not test_file.exists():
                continue

            with open(base_file) as f:
                base_data = yaml.safe_load(f)
            base_services_with_net: set[str] = set()
            if base_data and "services" in base_data:
                for svc_name, svc_cfg in base_data["services"].items():
                    if svc_cfg and svc_cfg.get("networks") is not None:
                        base_services_with_net.add(svc_name)

            if not base_services_with_net:
                continue

            with open(test_file) as f:
                test_data = yaml.safe_load(f)
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
                    test_nets = test_services_with_net[svc_name]
                    net_errors.extend(
                        f"{module_name}: service '{svc_name}' networks contains non-test network '{tnet}' in test.yml"
                        for tnet in test_nets
                        if not tnet.startswith("test-")
                    )

        if net_errors:
            logger.info(
                "[IMP:8][gate][isolation] Networks check failed for %d service(s)",
                len(net_errors),
            )
        assert not net_errors, (
            "Services with networks in base.yml missing networks: !override in test.yml:\n" + "\n".join(net_errors)
        )
        logger.info("[IMP:9][gate][isolation] All base container_names have -test override and networks: !override ✓")

    @pytest.mark.gate
    @ldd_trajectory
    def test_no_prod_network_in_test_overlay(self, caplog) -> None:
        # 🧪 TRAP[TEST] · Regression: новый модуль может получить test.yml с prod-сетью
        # · Scenario: scan all test.yml → fail if any network doesn't start with test-
        # · Last fail: never (new test)
        # · Remove if: network isolation model changes (e.g., single shared test network)
        """Ни один docker-compose.test.yml не ссылается на production-сети (DevPlan 017 W4.1).

        ## @purpose — Gate проверяет что test-изоляция полная: все networks в test.yml
        ##            имеют префикс test-. Любая prod-сеть (shared-db-net, observability-net, etc.)
        ##            в test.yml = нарушение изоляции (DNS-загрязнение).
        ## @rationale — DevPlan 017 AC3: "Ни один alias не дублируется между prod и test".
        ##              Этот тест — автоматическая проверка, предотвращающая регрессию.
        """
        test_nets = _get_test_yml_networks()
        errors: list[str] = []

        for module_name, services in sorted(test_nets.items()):
            for svc_name, networks in sorted(services.items()):
                for net_name in sorted(networks):
                    if net_name is None or net_name == "":
                        continue
                    if not net_name.startswith("test-"):
                        errors.append(
                            f"{module_name}: service '{svc_name}' has non-test network '{net_name}' in test.yml"
                        )

        logger.info(
            "[IMP:9][gate][isolation] Checked %d test modules for prod network references",
            len(test_nets),
        )
        assert not errors, f"{len(errors)} production network(s) found in test overlay:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] No production networks in test overlay ✓")

    @pytest.mark.gate
    @ldd_trajectory
    def test_test_network_consistency(self, caplog) -> None:
        # 🧪 TRAP[TEST] · Regression: новый модуль может получить test-* сеть не соответствующую prod-сети
        # · Scenario: compare each test service networks → verify test-* equivalent exists for every prod network
        # · Last fail: never (new test)
        # · Remove if: PROD_TO_TEST_NET_MAP is removed or network model fundamentally changes
        """Для каждого test-сервиса: если prod-сервис на сети X, test-сервис должен быть на test-X (DevPlan 017 W4.2).

        ## @purpose — Сверяет соответствие prod→test сетей: если в production сервис на
        ##            shared-db-net, в test он должен быть на test-shared-db-net.
        ##            Проверка идёт от base.yml → test.yml, обнаруживает пропущенные
        ##            или неконсистентные network-оверрайды.
        ## @invariants
        ##   - Каждая prod-сеть X должна иметь test-X эквивалент в PROD_TO_TEST_NET_MAP
        ##   - Test-сервис может иметь ДОПОЛНИТЕЛЬНЫЕ test-сети (например, exporters на observability)
        ##   - Test-сервис НЕ может иметь prod-сети (это покрывается test_no_prod_network_in_test_overlay)
        ## @rationale — DevPlan 017 AC2: "Все test.yml сервисов получают networks: !override
        ##              с test-* эквивалентами prod-сетей". Bез этой проверки новый модуль
        ##              может получить networks: !override с неправильной test-сетью.
        """
        base_nets = _get_base_yml_networks()
        test_nets = _get_test_yml_networks()
        errors: list[str] = []

        for module_name, base_services in sorted(base_nets.items()):
            test_services = test_nets.get(module_name, {})
            for svc_name, prod_networks in sorted(base_services.items()):
                test_networks = test_services.get(svc_name, set())
                if not test_networks:
                    # Сервис есть в base.yml но не переопределён в test.yml — допустимо
                    # если у сервиса нет container_name (Docker генерирует уникальное имя)
                    continue

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

        logger.info(
            "[IMP:9][gate][isolation] Checked network consistency across %d modules",
            len(base_nets),
        )
        assert not errors, "Test network consistency violations:\n" + "\n".join(errors)
        logger.info("[IMP:9][gate][isolation] All test networks are consistent with prod equivalents ✓")
