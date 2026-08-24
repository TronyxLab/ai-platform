# GREP_SUMMARY: gate compose-base contract x-logging container_name healthcheck docker-compose.base.yml
# STRUCTURE: ┌_get_base_ymls → discover_docker_modules → glob core/modules/*/docker-compose.base.yml┐ → ◇ test_all_base_yml_have_x_logging ∋ x-logging:&default-logging → ◇ test_container_name_matches_module_name ∋ container_name == module_name → ◇ test_healthcheck_present ∋ healthcheck block → ⊕ assertions
# region MODULE_CONTRACT
## @purpose — Gate test: validate docker-compose.base.yml contract for all docker modules
## @scope — Parses all core/modules/*/docker-compose.base.yml (via discover_docker_modules), validates:
##          1. File has x-logging: &default-logging anchor
##          2. Primary service container_name matches module directory name
##          3. Every service has a healthcheck block
##          (profiles contract is validated by the stronger test_gate_module_profiles.py)
## @invariants
##   - Module list is discovered dynamically (discover_docker_modules) — no hardcoded list
##   - Non-primary services (pgbouncer, alloy) may have different container_name
##   - Missing base.yml for a discovered module silently skipped (logged via absence)
## @rationale — Post-refactoring audit: docker-compose.base.yml contract ensures
##              consistent compose file structure across all modules for CI gate validation.
## @changes — 2026-07-14 | Created per TASK-T8.2
## @changes — 2026-07-16 | Migrated from hardcoded DOCKER_MODULES to discover_docker_modules (T7)
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml

from tests._conftest.audit import discover_docker_modules
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules"


def _get_base_ymls():
    """Return list of (module_name, path, parsed_dict) via shared discovery (T7)."""
    results = []
    for module_name in discover_docker_modules(MODULES_DIR):
        yaml_path = Path(MODULES_DIR) / module_name / "docker-compose.base.yml"
        if pathlib.Path(yaml_path).is_file():
            with pathlib.Path(yaml_path).open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data:
                results.append((module_name, yaml_path, data))
    return results


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_all_base_yml_have_x_logging(caplog):
    """Each docker-compose.base.yml has x-logging anchor."""
    modules = _get_base_ymls()
    failed = []
    for module_name, _yaml_path, data in modules:
        if "x-logging" not in data:
            failed.append(f"{module_name}: missing x-logging anchor")
            logger.info("[IMP:9][gate] FAIL: %s missing x-logging", module_name)

    assert not failed, "[IMP:9][gate] x-logging violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: All %d modules have x-logging", len(modules))


# Modules whose PRIMARY service uses a well-known tool container name instead of the module
# directory name. F1 (DevPlan 118): the primary container_name invariant is now a hard assert —
# any OTHER module without a matching container_name is a violation (drift).
_TOOL_NAMED_PRIMARY = {
    "node-metrics": "cadvisor",
    "service-exporters": "postgres-exporter",
    "logging": "loki",
    "log-collector": "alloy",
    "monitoring": "prometheus",
}


@pytest.mark.gate
@ldd_trajectory
def test_container_name_matches_module_name(caplog):
    """container_name in base.yml follows project naming convention (primary service)."""
    # 🧪 TRAP[TEST] · F1 (DevPlan 118) · Regression: pass-test → real assert
    # · Scenario: each module's base.yml primary service container_name == module name
    # · Last fail: N/A (was pass-test, no assert — R1 hole U-69 family)
    # · Remove if: container_name convention is intentionally dropped
    modules = _get_base_ymls()
    violations = []
    for module_name, _yaml_path, data in modules:
        services = data.get("services", {})
        container_names = [svc.get("container_name", "") for svc in services.values() if isinstance(svc, dict)]
        # Primary service may use module_name or a documented tool name
        expected = _TOOL_NAMED_PRIMARY.get(module_name, module_name)
        if expected not in container_names:
            violations.append(f"{module_name}: primary container_name '{expected}' not found in {container_names}")
            logger.info("[IMP:9][gate] FAIL: %s → expected '%s', got %s", module_name, expected, container_names)
        else:
            logger.info("[IMP:8][gate] PASS: %s → container_name '%s'", module_name, expected)

    assert not violations, "[IMP:9][gate] Container name violations:\n" + "\n".join(violations)
    logger.info("[IMP:9][gate] PASS: All %d modules have primary container_name convention verified", len(modules))


@pytest.mark.gate
@ldd_trajectory
def test_healthcheck_present(caplog):
    """Each service in base.yml has healthcheck."""
    modules = _get_base_ymls()
    failed = []
    for module_name, _yaml_path, data in modules:
        services = data.get("services", {})
        for svc_name, svc_config in services.items():
            # Skip init containers — они не требуют healthcheck (run-to-completion)
            if svc_config.get("restart") == "no":
                continue
            if "healthcheck" not in svc_config:
                failed.append(f"{module_name}/{svc_name}: missing healthcheck")
                logger.info("[IMP:9][gate] FAIL: %s/%s has no healthcheck", module_name, svc_name)

    assert not failed, "[IMP:9][gate] Healthcheck violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: All services have healthcheck")
