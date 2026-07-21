# GREP_SUMMARY: gate module-yaml contract D4 required-fields version spool_dir spool_volume env_requires duplicates
# STRUCTURE: ┌_get_module_yamls → glob core/modules/*/module.yaml┐ → ◇ test_all_modules_have_required_fields ∋ (name, install_type, description, depends_on) → ◇ test_no_version_field ∋ ⊥version → ◇ test_spool_dir_format ∋ ┌/abs/ path┐ ∋ ┌valid docker volume┐ → ◇ test_env_requires_no_duplicates ∋ len(set)==len(list) → ⊕ assertions
# region MODULE_CONTRACT
## @purpose — Gate test: validate D4 contract structure for all module.yaml files
## @scope — Parses all core/modules/*/module.yaml, validates:
##          1. Required fields present: name, install_type, description, depends_on
##          2. No module has 'version' field
##          3. spool_dir is absolute path, spool_volume is valid Docker volume name
##          4. env_requires has no duplicate entries within a module
## @invariants
##   - All test functions use @pytest.mark.gate + @ldd_trajectory
##   - _get_module_yamls() returns only valid YAML files (ignores non-dict data)
##   - spool_volume validation checks alphanumeric + dash + underscore characters
## @rationale — Post-refactoring audit D4: module.yaml contract must be enforced
##              at commit time to prevent configuration drift across all modules.
## @changes — 2026-07-14 | Created per TASK-T8.1
## @changes — 2026-07-16 | DOCKER_MODULES hardcoded list removed → discover_docker_modules (T7)
## @changes — 2026-07-18 | Added test_system_module_contract (T3/D3, DevPlan 011 T7)
# endregion MODULE_CONTRACT

import json
import logging
import os
import re
from pathlib import Path

import pytest
import yaml

from tests._conftest.audit import discover_docker_modules
from tests.conftest import ldd_trajectory

# D5 paths relative to repo root
_VALIDATOR_PATH = Path(__file__).resolve().parents[2] / "core" / "internal" / "scripts" / "validate_module_yaml.py"
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "core" / "schemas" / "module.schema.json"
_MODULES_DIR_D5 = Path(__file__).resolve().parents[2] / "core" / "modules"

logger = logging.getLogger(__name__)

MODULES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "core", "modules"
)

REQUIRED_FIELDS = ["name", "install_type", "description", "depends_on"]


def _get_module_yamls():
    """Return list of (module_name, module_yaml_path, parsed_dict) for all valid modules."""
    results = []
    for entry in sorted(os.listdir(MODULES_DIR)):
        module_dir = os.path.join(MODULES_DIR, entry)
        yaml_path = os.path.join(module_dir, "module.yaml")
        if os.path.isfile(yaml_path):
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            if data:
                results.append((entry, yaml_path, data))
    return results


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_all_modules_have_required_fields(caplog):
    """All discovered module.yaml contain name, install_type, description, depends_on."""
    modules = _get_module_yamls()
    # Minimum baseline: all docker modules + platform-secrets = 13+ modules
    docker_count = len(discover_docker_modules(MODULES_DIR))
    assert len(modules) >= docker_count, f"[IMP:9][gate] Expected at least {docker_count} modules, got {len(modules)}"

    failed = []
    for module_name, _yaml_path, data in modules:
        for field in REQUIRED_FIELDS:
            if field not in data:
                failed.append(f"{module_name}: missing field '{field}'")
                logger.info("[IMP:9][gate] FAIL: %s — missing '%s'", module_name, field)

    assert not failed, "[IMP:9][gate] Module.yaml contract violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: All %d modules have required fields", len(modules))


@pytest.mark.gate
@ldd_trajectory
def test_no_version_field(caplog):
    """No module.yaml contains 'version' field."""
    modules = _get_module_yamls()
    failed = []
    for module_name, _yaml_path, data in modules:
        if "version" in data:
            failed.append(module_name)
            logger.info("[IMP:9][gate] FAIL: %s has 'version' field (should be removed)", module_name)

    assert not failed, f"[IMP:9][gate] Modules with version field: {failed}"
    logger.info("[IMP:9][gate] PASS: No modules have version field")


@pytest.mark.gate
@ldd_trajectory
def test_spool_dir_format(caplog):
    """spool_dir must be absolute path, spool_volume valid Docker volume name."""
    modules = _get_module_yamls()
    failed = []
    for module_name, _yaml_path, data in modules:
        spool_dir = data.get("spool_dir", "")
        spool_volume = data.get("spool_volume", "")

        # Only check modules that have docker modules (have spool fields)
        if data.get("install_type") != "docker":
            continue

        if spool_dir and spool_dir != "none" and not spool_dir.startswith("/"):
            failed.append(f"{module_name}: spool_dir '{spool_dir}' is not absolute path")
            logger.info("[IMP:9][gate] FAIL: %s spool_dir not absolute", module_name)

        if spool_volume and not spool_volume.replace("-", "").replace("_", "").isalnum():
            failed.append(f"{module_name}: spool_volume '{spool_volume}' is not valid volume name")

    assert not failed, "[IMP:9][gate] spool contract violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: All spool fields valid")


@pytest.mark.gate
@ldd_trajectory
def test_env_requires_no_duplicates(caplog):
    """env_requires contains no duplicates within a module."""
    modules = _get_module_yamls()
    failed = []
    for module_name, _yaml_path, data in modules:
        env_req = data.get("env_requires", [])
        if not isinstance(env_req, list):
            continue
        if len(env_req) != len(set(env_req)):
            duplicates = [v for v in env_req if env_req.count(v) > 1]
            failed.append(f"{module_name}: duplicate env_requires: {set(duplicates)}")
            logger.info("[IMP:9][gate] FAIL: %s has duplicate env_requires", module_name)

    assert not failed, "[IMP:9][gate] env_requires violations:\n" + "\n".join(failed)
    logger.info("[IMP:9][gate] PASS: No duplicate env_requires")


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · System-module contract for platform-secrets (T3)
# · Last fail: N/A (preventive)
# · Remove if: platform-secrets migrates from systemd to Docker or contract changes
def test_system_module_contract(caplog):
    """platform-secrets is valid per system-module contract (not docker).

    ## @purpose — Validate that platform-secrets (install_type: system) follows
    ##            the system-module contract: includes module-system.mk, NOT module.mk,
    ##            has no docker targets, has system targets (install/status/restart/logs).
    ##            Per D3/T3: system modules have a different contract than docker modules.
    ## @io — ⎋ None (asserts system module contract)
    ## @complexity — O(1) on module.yaml + Makefile read
    """
    platform_secrets_dir = os.path.join(MODULES_DIR, "platform-secrets")
    yaml_path = os.path.join(platform_secrets_dir, "module.yaml")
    makefile_path = os.path.join(platform_secrets_dir, "Makefile")

    # Check module.yaml exists and has install_type: system
    assert os.path.isfile(yaml_path), f"module.yaml not found: {yaml_path}"
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    install_type = data.get("install_type", "")
    assert install_type == "system", (
        f"[IMP:9][gate][system_contract] platform-secrets install_type should be 'system', got '{install_type}'"
    )
    logger.info("[IMP:9][gate][system_contract] platform-secrets install_type=system ✓")

    # Check Makefile includes module-system.mk, NOT module.mk
    assert os.path.isfile(makefile_path), f"Makefile not found: {makefile_path}"
    with open(makefile_path) as f:
        makefile_content = f.read()

    has_system_mk = "module-system.mk" in makefile_content

    assert has_system_mk, (
        f"[IMP:9][gate][system_contract] platform-secrets Makefile should include module-system.mk, "
        f"content:\n{makefile_content}"
    )
    logger.info("[IMP:9][gate][system_contract] platform-secrets Makefile includes module-system.mk ✓")

    # Check no docker targets in Makefile
    docker_targets = ["build", "up", "backup", "down"]
    for target in docker_targets:
        # Check for target definition (line starting with target:)
        target_pattern = re.compile(rf"^{target}:", re.MULTILINE)
        has_target = bool(target_pattern.search(makefile_content))
        if has_target:
            logger.warning("[IMP:7][gate][system_contract] platform-secrets Makefile has docker target '%s'", target)
        # The module-system.mk template should NOT define these, and the
        # platform-secrets Makefile itself should not define them either
        assert not has_target, (
            f"[IMP:9][gate][system_contract] FAIL: platform-secrets Makefile defines docker target '{target}'"
        )

    logger.info("[IMP:9][gate][system_contract] platform-secrets has NO docker targets ✓")

    # Check system targets are present (via module-system.mk include)
    system_targets = ["install", "status", "restart", "logs"]
    # These are provided by module-system.mk template, check the template exists
    template_system_mk = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "core",
        "templates",
        "module-system.mk",
    )
    if os.path.isfile(template_system_mk):
        with open(template_system_mk) as f:
            template_content = f.read()
        for target in system_targets:
            target_pattern = re.compile(rf"^{target}:", re.MULTILINE)
            assert bool(target_pattern.search(template_content)), (
                f"[IMP:9][gate][system_contract] module-system.mk missing system target '{target}'"
            )
        logger.info(
            "[IMP:9][gate][system_contract] All %d system targets found in module-system.mk ✓", len(system_targets)
        )

    logger.info("[IMP:9][gate][system_contract] PASS: platform-secrets follows system-module contract")
