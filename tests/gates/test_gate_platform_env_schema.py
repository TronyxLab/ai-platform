# GREP_SUMMARY: platform-env, schema-gate, yaml-validation, networks-schema, volumes-schema, profiles-match, no-duplicates
# STRUCTURE: ◇ test_platform_env_yaml_exists → ◇ test_networks_schema → ◇ test_volumes_schema → ◇ test_profiles_match_modules → ◇ test_no_duplicate_networks → ◇ test_no_duplicate_volumes
# region MODULE_CONTRACT
## @purpose  Schema validation gate for platform-env.yaml — ensures canonical
##           environment descriptor is structurally valid and internally consistent.
## @scope    Anti-drift gate; runs as part of `make gate MODE=fast` and CI static-gate.
## @invariants
##   - Every network has required `name` field (str)
##   - Every volume has required `path` field (str, absolute)
##   - Every profile corresponds to an existing core/modules/ directory
##   - No duplicate network names or volume paths
##   - YAML is valid and parseable by PyYAML
## @rationale  platform-env.yaml is the Single Source of Truth for all consumers.
##             Schema validation prevents structural drift from being committed.
# endregion MODULE_CONTRACT

import logging
import re

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# Path resolution
PLATFORM_ENV_PATH = repo_root() / "platform-env.yaml"
MODULES_DIR = repo_root() / "core" / "modules"

from tests._conftest.audit import discover_docker_modules


@pytest.fixture(scope="module")
def env_data() -> dict:
    """Load and parse platform-env.yaml once per module.

    ## @purpose — Single parse for all tests in this module.
    ## @io — ⎋ dict: parsed YAML content
    """
    assert PLATFORM_ENV_PATH.exists(), f"platform-env.yaml not found at {PLATFORM_ENV_PATH}"
    with open(PLATFORM_ENV_PATH) as f:
        data = yaml.safe_load(f)
    assert data is not None, "platform-env.yaml is empty"
    return data


# ── File Existence ────────────────────────────────────────────────────────────


class TestFileStructure:
    """Verify platform-env.yaml exists and is valid YAML."""

    @ldd_trajectory
    def test_platform_env_yaml_exists(self, caplog) -> None:
        """platform-env.yaml must exist at project root."""
        logger.info("[IMP:9][gate][platform-env] Checking platform-env.yaml exists at %s", PLATFORM_ENV_PATH)
        assert PLATFORM_ENV_PATH.exists(), f"Missing: {PLATFORM_ENV_PATH}"
        assert PLATFORM_ENV_PATH.is_file(), f"Not a file: {PLATFORM_ENV_PATH}"
        logger.info("[IMP:9][gate][platform-env] platform-env.yaml exists ✓")

    @ldd_trajectory
    def test_platform_env_yaml_is_valid_yaml(self, caplog) -> None:
        """platform-env.yaml must be valid YAML (parseable by PyYAML)."""
        with open(PLATFORM_ENV_PATH) as f:
            data = yaml.safe_load(f)
        logger.info("[IMP:9][gate][platform-env] YAML parsed, top-level type: %s", type(data).__name__)
        assert isinstance(data, dict), "platform-env.yaml must contain a dict at top level"
        logger.info("[IMP:9][gate][platform-env] platform-env.yaml is valid YAML ✓")


# ── Networks Schema ───────────────────────────────────────────────────────────


class TestNetworksSchema:
    """Validate networks section of platform-env.yaml."""

    @ldd_trajectory
    def test_networks_section_exists(self, env_data: dict, caplog) -> None:
        """networks key must exist and be a list."""
        networks = env_data.get("networks")
        logger.info("[IMP:9][gate][platform-env] Checking networks section exists")
        assert networks is not None, "Missing 'networks' section in platform-env.yaml"
        assert isinstance(networks, list), "'networks' must be a list"
        logger.info("[IMP:9][gate][platform-env] networks section exists with %d entries ✓", len(networks))

    @ldd_trajectory
    def test_networks_name_field_required(self, env_data: dict, caplog) -> None:
        """Every network entry must have a 'name' field of type str."""
        networks = env_data.get("networks", [])
        logger.info("[IMP:9][gate][platform-env] Validating name field on %d networks", len(networks))
        for net in networks:
            assert isinstance(net, dict), f"Network entry must be a dict: {net}"
            assert "name" in net, f"Network missing 'name' field: {net}"
            assert isinstance(net["name"], str), f"Network 'name' must be str: {net['name']}"
        logger.info("[IMP:9][gate][platform-env] All %d networks have valid name fields ✓", len(networks))

    @ldd_trajectory
    def test_networks_optional_fields(self, env_data: dict, caplog) -> None:
        """Network optional fields (driver, internal) must be correct types if present."""
        networks = env_data.get("networks", [])
        logger.info("[IMP:9][gate][platform-env] Validating optional fields on %d networks", len(networks))
        for net in networks:
            if "driver" in net:
                assert isinstance(net["driver"], str), f"Network 'driver' must be str: {net['driver']}"
            if "internal" in net:
                assert isinstance(net["internal"], bool), f"Network 'internal' must be bool: {net['internal']}"
        logger.info("[IMP:9][gate][platform-env] Optional network fields valid ✓")

    @ldd_trajectory
    def test_networks_minimum_count(self, env_data: dict, caplog) -> None:
        """Must have at least 1 network (dynamic: checked against discovery)."""
        networks = env_data.get("networks", [])
        docker_count = len(discover_docker_modules(str(MODULES_DIR)))
        min_expected = max(1, docker_count // 2)
        logger.info(
            "[IMP:9][gate][platform-env] Networks: %d (min expected: %d based on %d docker modules)",
            len(networks),
            min_expected,
            docker_count,
        )
        assert len(networks) >= min_expected, (
            f"Expected >= {min_expected} networks (based on {docker_count} docker modules), got {len(networks)}"
        )
        logger.info("[IMP:9][gate][platform-env] Minimum network count met ✓")

    @ldd_trajectory
    def test_no_duplicate_networks(self, env_data: dict, caplog) -> None:
        """No duplicate network names."""
        net_names = [n["name"] for n in env_data.get("networks", [])]
        duplicates = {n for n in net_names if net_names.count(n) > 1}
        logger.info("[IMP:9][gate][platform-env] Checking duplicates in %d network names", len(net_names))
        assert not duplicates, f"Duplicate network names: {duplicates}"
        logger.info("[IMP:9][gate][platform-env] No duplicate network names ✓")

    @ldd_trajectory
    def test_network_names_format(self, env_data: dict, caplog) -> None:
        """Network names must follow kebab-case pattern."""
        pattern = re.compile(r"^[a-z][a-z0-9-]+$")
        networks = env_data.get("networks", [])
        logger.info("[IMP:9][gate][platform-env] Validating kebab-case format for %d network names", len(networks))
        for net in networks:
            name = net["name"]
            assert pattern.match(name), f"Network name '{name}' must be kebab-case"
        logger.info("[IMP:9][gate][platform-env] All network names are valid kebab-case ✓")


# ── Volumes Schema ────────────────────────────────────────────────────────────


class TestVolumesSchema:
    """Validate volumes section of platform-env.yaml."""

    @ldd_trajectory
    def test_volumes_section_exists(self, env_data: dict, caplog) -> None:
        """volumes key must exist and be a list."""
        volumes = env_data.get("volumes")
        logger.info("[IMP:9][gate][platform-env] Checking volumes section exists")
        assert volumes is not None, "Missing 'volumes' section in platform-env.yaml"
        assert isinstance(volumes, list), "'volumes' must be a list"
        logger.info("[IMP:9][gate][platform-env] volumes section exists with %d entries ✓", len(volumes))

    @ldd_trajectory
    def test_volumes_path_field_required(self, env_data: dict, caplog) -> None:
        """Every volume entry must have a 'path' field of type str, absolute."""
        volumes = env_data.get("volumes", [])
        logger.info("[IMP:9][gate][platform-env] Validating path field on %d volumes", len(volumes))
        for vol in volumes:
            assert isinstance(vol, dict), f"Volume entry must be a dict: {vol}"
            assert "path" in vol, f"Volume missing 'path' field: {vol}"
            assert isinstance(vol["path"], str), f"Volume 'path' must be str: {vol['path']}"
            assert vol["path"].startswith("/"), f"Volume 'path' must be absolute: {vol['path']}"
        logger.info("[IMP:9][gate][platform-env] All %d volumes have valid absolute paths ✓", len(volumes))

    @ldd_trajectory
    def test_volumes_minimum_count(self, env_data: dict, caplog) -> None:
        """Must have at least 1 volume (dynamic: checked against discovery)."""
        volumes = env_data.get("volumes", [])
        docker_count = len(discover_docker_modules(str(MODULES_DIR)))
        min_expected = max(1, docker_count // 2)
        logger.info(
            "[IMP:9][gate][platform-env] Volumes: %d (min expected: %d based on %d docker modules)",
            len(volumes),
            min_expected,
            docker_count,
        )
        assert len(volumes) >= min_expected, (
            f"Expected >= {min_expected} volumes (based on {docker_count} docker modules), got {len(volumes)}"
        )
        logger.info("[IMP:9][gate][platform-env] Minimum volume count met ✓")

    @ldd_trajectory
    def test_no_duplicate_volumes(self, env_data: dict, caplog) -> None:
        """No duplicate volume paths."""
        vol_paths = [v["path"] for v in env_data.get("volumes", [])]
        duplicates = {p for p in vol_paths if vol_paths.count(p) > 1}
        logger.info("[IMP:9][gate][platform-env] Checking duplicates in %d volume paths", len(vol_paths))
        assert not duplicates, f"Duplicate volume paths: {duplicates}"
        logger.info("[IMP:9][gate][platform-env] No duplicate volume paths ✓")


# ── Env Defaults Schema ───────────────────────────────────────────────────────


class TestEnvDefaultsSchema:
    """Validate env_defaults section of platform-env.yaml."""

    @ldd_trajectory
    def test_env_defaults_section_exists(self, env_data: dict, caplog) -> None:
        """env_defaults key must exist and be a dict."""
        env_defaults = env_data.get("env_defaults")
        logger.info("[IMP:9][gate][platform-env] Checking env_defaults section exists")
        assert env_defaults is not None, "Missing 'env_defaults' section in platform-env.yaml"
        assert isinstance(env_defaults, dict), "'env_defaults' must be a dict"
        logger.info("[IMP:9][gate][platform-env] env_defaults section exists with %d keys ✓", len(env_defaults))

    @ldd_trajectory
    def test_env_defaults_keys_format(self, env_data: dict, caplog) -> None:
        """Env var keys must be UPPER_SNAKE_CASE."""
        pattern = re.compile(r"^[A-Z][A-Z0-9_]+$")
        env_defaults = env_data.get("env_defaults", {})
        logger.info("[IMP:9][gate][platform-env] Validating format of %d env_defaults keys", len(env_defaults))
        for key in env_defaults:
            assert pattern.match(key), f"Env var key '{key}' must be UPPER_SNAKE_CASE"
        logger.info("[IMP:9][gate][platform-env] All env_defaults keys are UPPER_SNAKE_CASE ✓")

    @ldd_trajectory
    def test_env_defaults_values_are_strings(self, env_data: dict, caplog) -> None:
        """All env_defaults values must be strings."""
        env_defaults = env_data.get("env_defaults", {})
        logger.info("[IMP:9][gate][platform-env] Validating value types of %d env_defaults keys", len(env_defaults))
        for key, value in env_defaults.items():
            assert isinstance(value, str), f"Env var '{key}' value must be str, got {type(value).__name__}"
        logger.info("[IMP:9][gate][platform-env] All env_defaults values are strings ✓")

    @ldd_trajectory
    def test_env_defaults_minimum_count(self, env_data: dict, caplog) -> None:
        """Must have at least 1 env var (dynamic: scaled to docker module count)."""
        env_defaults = env_data.get("env_defaults", {})
        docker_count = len(discover_docker_modules(str(MODULES_DIR)))
        min_expected = max(1, docker_count)
        logger.info(
            "[IMP:9][gate][platform-env] Env defaults: %d (min expected: %d based on %d docker modules)",
            len(env_defaults),
            min_expected,
            docker_count,
        )
        assert len(env_defaults) >= min_expected, (
            f"Expected >= {min_expected} env vars (based on {docker_count} docker modules), got {len(env_defaults)}"
        )
        logger.info("[IMP:9][gate][platform-env] Minimum env_defaults count met ✓")


# ── Profiles Schema ───────────────────────────────────────────────────────────


class TestProfilesSchema:
    """Validate profiles section of platform-env.yaml."""

    @ldd_trajectory
    def test_profiles_section_exists(self, env_data: dict, caplog) -> None:
        """profiles key must exist and be a list."""
        profiles = env_data.get("profiles")
        logger.info("[IMP:9][gate][platform-env] Checking profiles section exists")
        assert profiles is not None, "Missing 'profiles' section in platform-env.yaml"
        assert isinstance(profiles, list), "'profiles' must be a list"
        logger.info("[IMP:9][gate][platform-env] profiles section exists with %d entries ✓", len(profiles))

    @ldd_trajectory
    def test_profiles_match_modules_dir(self, env_data: dict, caplog) -> None:
        """Every profile must correspond to a core/modules/ directory (excluding platform-secrets)."""
        assert MODULES_DIR.is_dir(), f"Modules dir not found: {MODULES_DIR}"
        existing_modules = {d.name for d in MODULES_DIR.iterdir() if d.is_dir()}
        # platform-secrets has no compose profile
        existing_modules.discard("platform-secrets")

        profiles = set(env_data.get("profiles", []))
        missing = profiles - existing_modules
        logger.info(
            "[IMP:9][gate][platform-env] Profiles: %d, Module dirs: %d",
            len(profiles),
            len(existing_modules),
        )
        assert not missing, (
            f"Profiles without matching module directory: {missing}. "
            f"Add directory to core/modules/ or remove from profiles in platform-env.yaml"
        )
        logger.info("[IMP:9][gate][platform-env] All profiles match module directories ✓")

    @ldd_trajectory
    def test_profiles_minimum_count(self, env_data: dict, caplog) -> None:
        """Must have at least 1 profile (dynamic: should match or exceed docker module count)."""
        profiles = env_data.get("profiles", [])
        docker_count = len(discover_docker_modules(str(MODULES_DIR)))
        # Profiles should at least cover all docker modules (excluding platform-secrets)
        min_expected = max(1, docker_count)
        logger.info(
            "[IMP:9][gate][platform-env] Profiles: %d (min expected: %d based on %d docker modules)",
            len(profiles),
            min_expected,
            docker_count,
        )
        assert len(profiles) >= min_expected, (
            f"Expected >= {min_expected} profiles (based on {docker_count} docker modules), got {len(profiles)}"
        )
        logger.info("[IMP:9][gate][platform-env] Minimum profile count met ✓")


# ── Cross-Section Consistency ─────────────────────────────────────────────────


class TestCrossSectionConsistency:
    """Verify consistency between sections of platform-env.yaml."""

    @ldd_trajectory
    def test_env_defaults_do_not_contain_dot_env_keys(self, env_data: dict, caplog) -> None:
        """env_defaults contract: PLATFORM_DOMAIN present (SoT per DevPlan 116 D4), no forbidden keys.

        ⚠️ DevPlan 116 D4 (U-16): PLATFORM_DOMAIN ПЕРЕЕХАЛ в env_defaults как SoT
        (platform-infra.yaml) — позитивная проверка: ключ ОБЯЗАН присутствовать.
        Production-only ключи, которые НЕ должны иметь YAML-дефолт, — в
        forbidden_in_defaults (сейчас пусто: секреты идут через ci_default).
        """
        env_defaults = env_data.get("env_defaults", {})
        # D4: PLATFORM_DOMAIN — SoT env_defaults (генерируется из platform-infra.yaml)
        assert "PLATFORM_DOMAIN" in env_defaults, (
            "PLATFORM_DOMAIN must be in platform-env.yaml env_defaults — SoT per DevPlan 116 D4 "
            "(run `make generate-platform-env`)"
        )
        assert env_defaults["PLATFORM_DOMAIN"] == "ai-platform.local", (
            f"env_defaults.PLATFORM_DOMAIN = {env_defaults['PLATFORM_DOMAIN']!r}, expected ai-platform.local"
        )
        # Запрещённые production-only ключи (на данный момент нет ни одного)
        forbidden_in_defaults: set[str] = set()
        found = forbidden_in_defaults & set(env_defaults.keys())
        logger.info("[IMP:9][gate][platform-env] Checking forbidden keys in env_defaults: found=%s", found)
        assert not found, f"Forbidden env_defaults keys (production-only): {found}"
        logger.info("[IMP:9][gate][platform-env] PLATFORM_DOMAIN SoT present; no forbidden keys ✓")
