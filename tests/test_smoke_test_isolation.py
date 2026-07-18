# GREP_SUMMARY: smoke-test isolation container-name conflict parallel up test
# STRUCTURE: ▶ test_all_test_containers_have_test_suffix → ◇ test_no_container_name_collision
# region MODULE_CONTRACT
## @purpose  Smoke test: verify test isolation — no container_name conflicts (DevPlan 04 TASK-G5)
## @scope    Проверяет -test суффикс во всех test.yml, отсутствие конфликтов с production
## @invariants
##   - Все контейнеры в test-проекте имеют -test суффикс
##   - Нет пересечений container_name между production и test
## @rationale Тестовая изоляция через test-overlay (DevPlan 04 DD2)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# ── YAML !override tag support ───────────────────────────────────────────────
# Docker Compose extends YAML with !override (array replacement vs concatenation).
# Standard PyYAML doesn't know this tag — register a constructor that returns
# the list as-is (the tag only affects compose merge semantics).
def _yaml_override_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> list:
    """YAML constructor for !override tag — returns the list value unchanged."""
    return loader.construct_sequence(node)


yaml.add_constructor("!override", _yaml_override_constructor, Loader=yaml.SafeLoader)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = PROJECT_ROOT / "core" / "modules"


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
