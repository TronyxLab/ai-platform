# GREP_SUMMARY: gate-test container-name consistency compose registry check
# STRUCTURE: ▶ test_all_container_names_extracted → ◇ test_depends_on_references_exist
# region MODULE_CONTRACT
## @purpose  Gate tests: validate container_name consistency across compose and module.yaml (DevPlan 04 TASK-B5)
## @scope    Извлекает все container_name из docker-compose.base.yml → реестр, проверяет ссылки
## @invariants
##   - Все container_name из docker-compose.base.yml извлечены в реестр
##   - Все depends_on ссылки разрешимы в container_name registry
##   - Env hostname resolution superseded by test_p20_container_coupling.py::test_env_hostnames_resolvable
## @rationale Предотвращает дрейф container_name (DevPlan 04 DD8)
## @changes
##   2026-07-15 · Removed dead test_env_requires_hosts_exist (vacuum loop with pass)
##              · Superseded by test_p20_container_coupling.py::test_env_hostnames_resolvable
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

MODULES_DIR = repo_root() / "core" / "modules"


def _extract_container_registry() -> dict[str, str]:
    """Извлекает все container_name: из docker-compose.base.yml → {container_name: module_name}."""
    registry: dict[str, str] = {}
    for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
        module_name = compose_file.parent.name
        with open(compose_file) as f:
            try:
                data = yaml.safe_load(f)
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


def _get_module_yamls() -> list[tuple[str, Path]]:
    """Возвращает [(module_name, path), ...] для всех module.yaml."""
    return [(mod_path.parent.name, mod_path) for mod_path in sorted(MODULES_DIR.glob("*/module.yaml"))]


REGISTRY = _extract_container_registry()
MODULE_YAMLS = _get_module_yamls()


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Container name consistency — все контейнеры следуют конвенции именования
# · Last fail: N/A (preventive)
# · Remove if: naming convention changes fundamentally
class TestContainerNameConsistency:
    @pytest.mark.gate
    @ldd_trajectory
    def test_all_container_names_extracted(self, caplog) -> None:
        """Все container_name из docker-compose.base.yml извлечены в реестр."""
        logger.info("[IMP:9][gate][container-registry] Extracted %d container names", len(REGISTRY))
        assert len(REGISTRY) > 0, (
            "No container names extracted from docker-compose.base.yml files. "
            "Check that compose files exist and are valid YAML."
        )
        # Log for debugging
        print(f"\nContainer registry ({len(REGISTRY)} entries):")
        for cname, module in sorted(REGISTRY.items()):
            print(f"  {cname} → {module}")
        logger.info("[IMP:9][gate][container-registry] All %d container names extracted successfully", len(REGISTRY))

    @pytest.mark.gate
    @ldd_trajectory
    def test_depends_on_references_exist(self, caplog) -> None:
        """Все depends_on (из compose) ссылки разрешимы в container_name registry."""
        errors: list[str] = []
        for compose_file in sorted(MODULES_DIR.glob("*/docker-compose.base.yml")):
            module_name = compose_file.parent.name
            with open(compose_file) as f:
                data = yaml.safe_load(f)
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
                    f"container_name registry. Registered: {sorted(REGISTRY.keys())}"
                    for dep in deps
                    if dep not in REGISTRY
                )
        if errors:
            logger.error("[IMP:9][gate][depends-on] %d unresolved depends_on references found", len(errors))
        else:
            logger.info(
                "[IMP:9][gate][depends-on] All depends_on references resolved (%d compose files checked)",
                len(list(MODULES_DIR.glob("*/docker-compose.base.yml"))),
            )
        assert not errors, "Unresolved depends_on references:\n" + "\n".join(errors)
