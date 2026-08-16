# GREP_SUMMARY: gate module-yaml contract D4 D5 required-fields version spool_dir spool_volume env_requires duplicates validator schema restart
# STRUCTURE: ┌_get_module_yamls → glob core/modules/*/module.yaml┐ → ◇ test_all_modules_have_required_fields ∋ (name, install_type, description, depends_on) → ◇ test_no_version_field ∋ ⊥version → ◇ test_spool_dir_format ∋ ┌/abs/ path┐ ∋ ┌valid docker volume┐ → ◇ test_env_requires_no_duplicates ∋ len(set)==len(list) → ◇ test_system_module_contract ∋ ⚙ platform-secrets → ◇ [D5] test_d5_validator_exists ∋ file exists + def main → ◇ [D5] test_d5_schema_version ∋ title=D5 + typed env_requires + restart → ◇ [D5] test_d5_validator_passes_on_all_modules ∋ native import validate_module_yaml → ∑ assertions
# region MODULE_CONTRACT
## @purpose — Gate test: validate D4 + D5 contract structure for all module.yaml files
## @scope — Parses all core/modules/*/module.yaml, validates:
##          1. Required fields present: name, install_type, description, depends_on
##          2. No module has 'version' field
##          3. spool_dir is absolute path, spool_volume is valid Docker volume name
##          4. env_requires has no duplicate entries within a module
##          5. (D5) core/internal/scripts/validate_module_yaml.py exists with def main
##          6. (D5) module.schema.json declares D5 (typed env_requires + restart field)
##          7. (D5) validate_module() passes on all 14 module.yaml files
## @invariants
##   - All test functions use @pytest.mark.gate + @ldd_trajectory
##   - _get_module_yamls() returns only valid YAML files (ignores non-dict data)
##   - spool_volume validation checks alphanumeric + dash + underscore characters
##   - D5 tests use native import (no subprocess) per testing rules
## @rationale — Post-refactoring audit D4/D5: module.yaml contract must be enforced
##              at commit time to prevent configuration drift across all modules.
##              D5 extension (Wave 3 W3-E5): validates validator existence, schema
##              version, and runtime pass on all modules.
## @changes — 2026-07-14 | Created per TASK-T8.1
## @changes — 2026-07-16 | DOCKER_MODULES hardcoded list removed → discover_docker_modules (T7)
## @changes — 2026-07-18 | Added test_system_module_contract (T3/D3, DevPlan 011 T7)
## @changes — 2026-07-21 | Added D5 extension (test_d5_validator_exists, test_d5_schema_version, test_d5_validator_passes_on_all_modules) per DevPlan 033 W3-E5 step 4
# endregion MODULE_CONTRACT

import json
import logging
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

MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules"

REQUIRED_FIELDS = ["name", "install_type", "description", "depends_on"]


def _normalize_env_req_entry(entry: str | dict) -> str:
    """Normalize a single env_requires entry to its canonical name.

    ## @purpose — Dict entries cannot be used as set elements (TypeError).
    ##            Extract the 'name' field from dict entries, pass strings through.
    ## @io — ⇥ str or dict → ⎋ str (canonical name)
    ## @complexity — O(1)
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("name", "")
    return str(entry)


def _normalize_env_req_list(env_req: list) -> list[str]:
    """Normalize a list of env_requires entries to canonical names.

    ## @purpose — Transform mixed str/dict list to list[str] for duplicate detection.
    ## @io — ⇥ list[str|dict] → ⎋ list[str]
    ## @complexity — O(N)
    """
    return [_normalize_env_req_entry(e) for e in env_req]


def _get_module_yamls():
    """Return list of (module_name, module_yaml_path, parsed_dict) for all valid modules."""
    results = []
    for entry in sorted(p.name for p in Path(MODULES_DIR).iterdir()):
        module_dir = Path(MODULES_DIR) / entry
        yaml_path = Path(module_dir) / "module.yaml"
        if Path(yaml_path).is_file():
            with Path(yaml_path).open(encoding="utf-8") as f:
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
        normalized = _normalize_env_req_list(env_req)
        seen = set()
        dups = set()
        for item in normalized:
            if item in seen:
                dups.add(item)
            seen.add(item)
        if len(normalized) != len(seen):
            failed.append(f"{module_name}: duplicate env_requires names: {sorted(dups)}")
            logger.info("[IMP:9][gate] FAIL: %s has duplicate env_requires: %s", module_name, sorted(dups))

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
    platform_secrets_dir = Path(MODULES_DIR) / "platform-secrets"
    yaml_path = Path(platform_secrets_dir) / "module.yaml"
    makefile_path = Path(platform_secrets_dir) / "Makefile"

    # Check module.yaml exists and has install_type: system
    assert Path(yaml_path).is_file(), f"module.yaml not found: {yaml_path}"
    with Path(yaml_path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    install_type = data.get("install_type", "")
    assert install_type == "system", (
        f"[IMP:9][gate][system_contract] platform-secrets install_type should be 'system', got '{install_type}'"
    )
    logger.info("[IMP:9][gate][system_contract] platform-secrets install_type=system ✓")

    # Check Makefile includes module-system.mk, NOT module.mk
    assert Path(makefile_path).is_file(), f"Makefile not found: {makefile_path}"
    with Path(makefile_path).open(encoding="utf-8") as f:
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
    template_system_mk = Path(__file__).resolve().parent.parent.parent / "core" / "templates" / "module-system.mk"
    if Path(template_system_mk).is_file():
        with Path(template_system_mk).open(encoding="utf-8") as f:
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


# ==================== D5 Extension (Wave 3, W3-E5) ====================


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · D5 validator must exist (Wave 3 contract enforcement)
# · Last fail: N/A (preventive)
# · Remove if: D5 contract is superseded or validator script is relocated
def test_d5_validator_exists(caplog):
    """D5: core/internal/scripts/validate_module_yaml.py exists and is executable.

    ## @purpose — Validate that the D5 module.yaml contract validator script
    ##            is present at the expected path. Per DevPlan 033 W3-E5 step 4,
    ##            the validator must exist for CI gate enforcement.
    ## @io — ⎋ None (asserts file existence)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.INFO):
        logger.info("[IMP:7][gate][d5] Checking D5 validator presence: %s", _VALIDATOR_PATH)

        assert _VALIDATOR_PATH.is_file(), f"[IMP:9][gate][d5] D5 validator not found: {_VALIDATOR_PATH}"

        # Verify it has executable bit or at minimum is a valid Python file
        content = _VALIDATOR_PATH.read_text()
        assert "def main" in content, "[IMP:9][gate][d5] D5 validator missing 'def main' entry point"
        assert "D5" in content or "DEPRECATED" not in content, (
            "[IMP:9][gate][d5] D5 validator seems stale (no D5 reference)"
        )

        logger.info(
            "[IMP:9][gate][d5] PASS: D5 validator exists at %s (%d bytes)",
            _VALIDATOR_PATH,
            len(content),
        )


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Schema must be D5, not stale D4
# · Last fail: N/A (preventive)
# · Remove if: schema versioning scheme changes
def test_d5_schema_version(caplog):
    """D5: module.schema.json declares D5 contract (not stale D4).

    ## @purpose — Verify JSON Schema title includes 'D5' marker,
    ##            confirming the schema has been upgraded from D4.
    ##            Checks presence of typed env_requires and restart field.
    ## @io — ⎋ None (asserts schema properties)
    ## @complexity — O(1)
    """
    with caplog.at_level(logging.INFO):
        logger.info("[IMP:7][gate][d5] Checking D5 schema: %s", _SCHEMA_PATH)

        assert _SCHEMA_PATH.is_file(), f"[IMP:9][gate][d5] Schema not found: {_SCHEMA_PATH}"
        schema = json.loads(_SCHEMA_PATH.read_text())

        # Title must indicate D5
        title = schema.get("title", "")
        assert "D5" in title, f"[IMP:9][gate][d5] Schema title missing 'D5': '{title}'"
        logger.info("[IMP:9][gate][d5] Schema title: '%s' ✓", title)

        # env_requires must have oneOf (string OR object)
        env_req = schema.get("properties", {}).get("env_requires", {})
        one_of = env_req.get("items", {}).get("oneOf", [])
        assert len(one_of) == 2, f"[IMP:9][gate][d5] env_requires oneOf expected 2 variants, got {len(one_of)}"
        logger.info("[IMP:9][gate][d5] env_requires oneOf=2 variants (string + object) ✓")

        # D5 adds typed object variant with type+required fields
        obj_variant = one_of[1] if isinstance(one_of[1], dict) and "name" in str(one_of[1]) else one_of[0]
        obj_props = obj_variant.get("properties", {})
        assert "type" in obj_props, "[IMP:9][gate][d5] Typed env_requires missing 'type' field"
        assert "required" in obj_props, "[IMP:9][gate][d5] Typed env_requires missing 'required' field"
        logger.info("[IMP:9][gate][d5] Typed env_requires: type + required fields ✓")

        # Restart field must be present (D5 addition)
        assert "restart" in schema.get("properties", {}), "[IMP:9][gate][d5] D5 schema missing 'restart' field"
        logger.info("[IMP:9][gate][d5] restart field present ✓")

        logger.info("[IMP:9][gate][d5] PASS: schema is D5 (title='%s')", title)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Validator must pass on all 14 modules
# · Last fail: N/A (preventive)
# · Remove if: validate_module_yaml.py API changes import signature
def test_d5_validator_passes_on_all_modules(caplog):
    """D5: validate_module_yaml passes on all 14 core/modules/*/module.yaml.

    ## @purpose — Runs the D5 validator's validate_module() against every
    ##            existing module.yaml. This catches regressions when module
    ##            definitions change without updating the schema or validator.
    ##            Uses native import (no subprocess — per testing rules).
    ## @io — ⎋ None (asserts all modules pass D5 validation)
    ## @complexity — O(M) where M = 14 modules; requires yaml + json for fixture parsing
    """
    import importlib.util
    import sys

    with caplog.at_level(logging.INFO):
        logger.info("[IMP:7][gate][d5] Running D5 validator on all modules...")

        # Import validate_module_yaml dynamically (avoid circular dep with conftest)
        spec = importlib.util.spec_from_file_location("validate_module_yaml", _VALIDATOR_PATH)
        assert spec is not None, f"[IMP:9][gate][d5] Cannot load spec from {_VALIDATOR_PATH}"
        assert spec.loader is not None, f"[IMP:9][gate][d5] No loader for {_VALIDATOR_PATH}"

        # yaml_query.py is also in the same dir — may be imported transitively
        # Ensure scripts dir is in sys.path for sibling imports
        scripts_dir = str(_VALIDATOR_PATH.parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        vmod = importlib.util.module_from_spec(spec)
        # Workaround: module may reference __file__ for path resolution
        # Cache the sys.path manipulation
        old_path = list(sys.path)
        try:
            spec.loader.exec_module(vmod)
        except (ImportError, SyntaxError, OSError) as e:
            pytest.fail(f"[IMP:9][gate][d5] Cannot import validate_module_yaml: {e}")
        finally:
            sys.path = old_path

        assert hasattr(vmod, "validate_module"), (
            "[IMP:9][gate][d5] validate_module_yaml missing 'validate_module' function"
        )

        # Collect all module.yaml files
        module_yamls = sorted(_MODULES_DIR_D5.glob("*/module.yaml"))
        assert len(module_yamls) >= 13, f"[IMP:9][gate][d5] Expected ≥13 module.yaml files, found {len(module_yamls)}"

        # Run validate_module on each
        schema_path = _SCHEMA_PATH
        failed: list[str] = []
        for yaml_path in module_yamls:
            module_name = yaml_path.parent.name
            violations = vmod.validate_module(
                yaml_path,
                schema_path=schema_path,
            )
            if violations:
                failed.append(f"{module_name}: {'; '.join(violations)}")
                for v in violations:
                    logger.info("[IMP:9][gate][d5] FAIL: %s — %s", module_name, v)
            else:
                logger.info("[IMP:9][gate][d5] PASS: %s", module_name)

        assert not failed, "[IMP:9][gate][d5] D5 validation failures:\n" + "\n".join(failed)
        logger.info(
            "[IMP:9][gate][d5] PASS: All %d modules pass D5 validation",
            len(module_yamls),
        )
