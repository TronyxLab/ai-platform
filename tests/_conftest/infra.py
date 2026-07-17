# GREP_SUMMARY: test-infra, Docker, networks, volumes, fixture, session-scoped, autouse
# STRUCTURE: ┌VOLUME_DIRS + DOCKER_NETWORKS┐ → ◇ test_infra(session,autouse) → ⊞ needs_docker → ├── no → yield(no-op) → └── yes → ┌_ensure_volume_dirs┐ → ⊕ ensure_external_networks → yield → ⎋ teardown networks

# region MODULE_CONTRACT
## @purpose  Session-scoped autouse fixture that creates Docker networks and volume
##           directories before test session, cleans up networks after.
##           Replaces former make test-infra-up/test-infra-down targets.
## @scope    All tests; fixture checks Docker availability and creates infra only if
##           Docker is available. Volume dirs are created with os.makedirs (exist_ok).
## @invariants
##   - VOLUME_DIRS and DOCKER_NETWORKS are moved from Makefile to this fixture
##   - Docker networks are created only if docker CLI is available
##   - On teardown, only Docker networks are removed (volumes persist)
##   - Idempotent: safe to call multiple times
##   - T2.2: No host-side effects for static/contract/gate tests (requires_docker check)
## @rationale Infrastructure management is a test precondition, not a platform-level
##           operation. Moving it from Makefile to conftest eliminates the class of
##           errors where developers forgot to run `make test-infra-up` before tests.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess

import pytest
import yaml as _yaml

from _conftest.ldd import _ensure_volume_dirs
from _conftest.networks import docker_available, ensure_external_networks


# region PLATFORM_ENV_LOADER
## @purpose — Read platform-env.yaml and return networks + volumes lists.
##            Replaces hardcoded VOLUME_DIRS + DOCKER_NETWORKS.
## @rationale — P5: Single Source of Truth — canonical environment descriptor.
def _load_platform_env() -> dict:
    """Parse platform-env.yaml, return {'networks': [...], 'volumes': [...]}.

    ## @io — ⇥ (reads platform-env.yaml from project root) → ⎋ dict with lists
    ## @complexity — O(N + M) where N = networks, M = volumes
    ## @invariants
    ##   - PLATFORM_ROOT env var overrides auto-detection (for CI)
    ##   - PyYAML is always available (project dependency)
    """
    _platform_root = os.environ.get(
        "PLATFORM_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    _yaml_path = os.path.join(_platform_root, "platform-env.yaml")

    with open(_yaml_path) as _f:
        _data = _yaml.safe_load(_f)

    return {
        "networks": [n["name"] for n in _data.get("networks", [])],
        "volumes": [v["path"] for v in _data.get("volumes", [])],
    }


# endregion PLATFORM_ENV_LOADER

# region PLATFORM_PORTS_FIXTURE
## @purpose — Load port_mappings from platform-env.yaml and expose as a session-scoped fixture.
##            Tests that need port numbers use this fixture instead of hardcoded values.


@pytest.fixture(scope="session")
def platform_ports() -> dict[str, int]:
    """Read port_mappings from platform-env.yaml.

    ## @purpose — Single source of truth for port numbers in tests.
    ##            When a new service port is added, update platform-env.yaml
    ##            and this fixture automatically reflects the change.
    ##            A new port without updating platform-env.yaml = loud test failure.
    ## @io — ⇥ (reads platform-env.yaml from project root) → ⎋ dict[str, int]
    ## @complexity — O(N) where N = number of port_mappings entries
    ## @invariants
    ##   - Port values are integers (cast from YAML)
    ##   - Returns a dict that tests can mutate safely (own copy per session)
    ##   - PLATFORM_ROOT env var overrides auto-detection (for CI)
    """
    _logger = logging.getLogger(__name__)
    _platform_root = os.environ.get(
        "PLATFORM_ROOT",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    _yaml_path = os.path.join(_platform_root, "platform-env.yaml")

    with open(_yaml_path) as _f:
        _data = _yaml.safe_load(_f)

    _mappings = _data.get("port_mappings", {})
    # Cast to int in case YAML parsed as str
    _ports: dict[str, int] = {k: int(v) for k, v in _mappings.items()}

    _logger.info(
        "[IMP:9][conftest][platform_ports] Loaded %d port mappings from platform-env.yaml: %s",
        len(_ports),
        _ports,
    )
    assert len(_ports) > 0, "[IMP:9][conftest][platform_ports] CRITICAL: port_mappings section is empty or missing in platform-env.yaml"
    return _ports


# endregion PLATFORM_PORTS_FIXTURE

# region TEST_INFRA_FIXTURE
## @purpose — Session-scoped autouse fixture that creates Docker networks and volume
##            directories before test session, cleans up networks after.
##            Replaces former make test-infra-up/test-infra-down targets.
## @scope — All tests; fixture checks Docker availability and creates infra only if
##          Docker is available. Volume dirs are created with os.makedirs (exist_ok).
## @invariants
##   - Networks and volumes read from platform-env.yaml (Single Source of Truth)
##   - Docker networks are created only if docker CLI is available
##   - On teardown, only Docker networks are removed (volumes persist)
##   - Idempotent: safe to call multiple times
## @rationale — Infrastructure management is a test precondition, not a platform-level
##              operation. Moving it from Makefile to conftest eliminates the class of
##              errors where developers forgot to run `make test-infra-up` before tests.


# T2.2: Global flag for test_infra activation tracking.
# Set to True when test_infra fixture actually runs its setup (needs_docker=True).
# Used by test_conftest_isolation.py to verify static tests don't trigger infra.
_test_infra_was_active: bool = False


@pytest.fixture(scope="session", autouse=True)
def test_infra(request) -> None:
    """Create Docker networks and volume dirs before tests, cleanup after.

    ## @purpose — Replaces make test-infra-up / test-infra-down.
    ##            Creates volume directories and Docker external networks
    ##            needed by Docker-dependent tests (smoke, component, integration, predeploy).
    ##            CONDITIONAL activation: only if at least one collected test has
    ##            the `requires_docker` marker. Static/contract/gate tests without
    ##            this marker will NOT trigger infrastructure setup.
    ## @io — ⎋ None (side-effect: directories created, Docker networks created/removed)
    ## @complexity — O(N + M) where N = volume dirs, M = Docker networks
    ## @invariants
    ##   - T2.2: No host-side effects for static/contract/gate tests
    ##   - requires_docker marker checked via request.session.items
    ##   - If no Docker-dependent test: yields immediately (no-op), skips teardown
    """
    global _test_infra_was_active

    _logger = logging.getLogger(__name__)

    # ── T2.2: Check if any collected test requires Docker ──
    items = request.session.items
    _DOCKER_MARKERS = {"requires_docker", "component", "smoke", "integration", "predeploy"}
    needs_docker = any(any(item.get_closest_marker(m) for m in _DOCKER_MARKERS) for item in items)

    if not needs_docker:
        _test_infra_was_active = False
        _logger.info(
            "[IMP:8][conftest][test_infra] SKIP: no test requires Docker — "
            "skipping volume/network setup (T2.2 isolation)"
        )
        yield  # no-op for static/contract/gate tests
        return

    _test_infra_was_active = True

    # ── Setup: read platform-env.yaml (Single Source of Truth) ──
    _logger.info("[IMP:7][conftest][test_infra] Setting up test infrastructure (requires_docker present)...")
    _platform_env = _load_platform_env()
    _networks = _platform_env["networks"]
    _volumes = _platform_env["volumes"]
    _logger.info(
        "[IMP:8][conftest][test_infra] Loaded %d networks, %d volumes from platform-env.yaml",
        len(_networks),
        len(_volumes),
    )

    # Create volume directories
    _ensure_volume_dirs(_volumes)

    # Create Docker external networks (idempotent — safe to call multiple times)
    _docker_avail = docker_available()
    if _docker_avail:
        ensure_external_networks(_networks, docker_available=_docker_avail)
        _logger.info("[IMP:7][conftest][test_infra] Docker networks ensured: %s", _networks)
    else:
        _logger.info("[IMP:7][conftest][test_infra] Docker not available — skipping network creation")

    _logger.info("[IMP:9][conftest][test_infra] Test infrastructure ready")

    # ── Yield: tests run here ──
    yield

    # ── Teardown: remove Docker networks (volumes persist) ──
    _logger.info("[IMP:7][conftest][test_infra] Tearing down test infrastructure...")
    if _docker_avail:
        for net in _networks:
            subprocess.run(
                ["docker", "network", "rm", net],
                capture_output=True,
                check=False,
            )
            _logger.info("[IMP:7][conftest][test_infra] Removed Docker network: %s", net)
    _logger.info("[IMP:9][conftest][test_infra] Test infrastructure teardown complete")


# endregion TEST_INFRA_FIXTURE
