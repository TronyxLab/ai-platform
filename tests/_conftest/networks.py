# GREP_SUMMARY: networks, topology, Docker, external-networks, constants, utilities, conftest, TEST_NETWORKS, test-isolation
# STRUCTURE: ┌PLATFORM_NETWORKS + EXEMPT_CREATED_NETWORKS┐ → ┌docker_available┐ → ◇ is_production_host → ○ ensure_external_networks(∋name) → ⊕ inspect ∨ create → ⎋ None

# region MODULE_CONTRACT
## @purpose — Centralised network topology constants and Docker network utility functions for all test files.
## @scope — Extracted from tests/conftest.py to eliminate duplication across test_network_consistency.py and test_module_dependency_graph.py.
## @invariants
##   - PLATFORM_NETWORKS: networks pre-created by deploy-modules.sh before compose up
##   - TEST_NETWORKS: test-only network equivalents for DNS-alias isolation (DevPlan 017 Option B)
##   - EXEMPT_CREATED_NETWORKS: networks created by modules (non-external) intentionally not in PLATFORM_NETWORKS
##   - docker_available() checks CLI only (not Docker daemon responsiveness)
##   - ensure_external_networks() is idempotent — safe to call multiple times
##   - is_production_host() uses simple substring matching (not regex)
## @rationale — DRY: previously duplicated in 6+ test files; centralise in conftest submodule. Prevents drift between test_network_consistency.py and test_module_dependency_graph.py which had diverging copies.
# endregion MODULE_CONTRACT

import shutil
import socket
import subprocess

# region NETWORK_CONSTANTS
## @purpose — Centralised network topology constants for all test files.
## @rationale — Prevents drift: test_network_consistency.py and
##              test_module_dependency_graph.py had diverging copies.
## @invariants
##   - PLATFORM_NETWORKS: networks pre-created by deploy-modules.sh before compose up
##   - EXEMPT_CREATED_NETWORKS: networks created by modules (non-external) intentionally not in PLATFORM_NETWORKS

# Networks pre-created by deploy-modules.sh before any compose up.
PLATFORM_NETWORKS: set[str] = {
    "proxy-net",
    "shared-db-net",
    "backup-net",
    "hermes-agent-net",
    "shared-cache-net",
    "observability-net",
}

# Test-only external networks for DNS-alias isolation (DevPlan 017 Option B).
# Pre-created by platform_services fixture in smoke.py alongside PLATFORM_NETWORKS.
TEST_NETWORKS: set[str] = {
    "test-shared-db-net",
    "test-shared-cache-net",
    "test-observability-net",
    "test-proxy-net",
    "test-hermes-agent-net",
}

# Networks created by modules (non-external in compose) that are intentionally NOT
# in PLATFORM_NETWORKS. These are internal bridges managed by a single compose project.
EXEMPT_CREATED_NETWORKS: set[str] = {
    "integration-test-net",
}

# endregion NETWORK_CONSTANTS


# region SHARED_NETWORK_UTILITIES
## @purpose — Shared utility functions for Docker network management and host detection.
##            Migrated from individual test files to eliminate duplication.
## @scope — Used by test_component_*.py, test_smoke_*.py, and test_integration_*.py test files.
## @invariants
##   - docker_available() checks CLI only (not Docker daemon responsiveness)
##   - ensure_external_networks() is idempotent — safe to call multiple times
##   - is_production_host() uses simple substring matching (not regex)
## @rationale — DRY: previously duplicated in 6+ test files; centralise in conftest.

PRODUCTION_HOST_PATTERNS: list[str] = ["tronyx-vps", "vps.tronyx", "production"]


def docker_available() -> bool:
    """Check if docker CLI is available.

    ## @io — ⎋ bool: True if docker binary found in PATH
    ## @complexity — O(1)
    """
    return shutil.which("docker") is not None


def is_production_host() -> bool:
    """Check if current host matches production patterns.

    ## @io — ⎋ bool: True if hostname contains any PRODUCTION_HOST_PATTERNS
    ## @complexity — O(N) where N = len(PRODUCTION_HOST_PATTERNS)
    """
    hostname = socket.gethostname()
    return any(p in hostname for p in PRODUCTION_HOST_PATTERNS)


# 📝 TRAP[DEBT] · 2026-07-15 · MED · Parallel test teardown destroys shared external networks
# · Observed: docker events показали массовый destroy shared-db-net/proxy-net/etc во время
# ·   параллельной сессии; повторный compose up упал с "network declared as external, but
# ·   could not be found"
# · Suspected: teardown одного тестового прогона удаляет external-сети, на которые полагаются
# ·   другие сессии/прогоны
# · Impact: флаки при параллельных волнах/сессиях — up падает между create сети и attach
# ·   контейнера
# · When: during wave-postgres T5.2 live-verification (2026-07-15)


def ensure_external_networks(names: list[str], docker_available: bool | None = None) -> None:
    """Ensure Docker external networks exist, creating if missing.

    ## @io
    ## - input: names (list of str), docker_available (bool or None)
    ## - output: None, creates Docker networks via subprocess
    ## @complexity: O(n) per network
    ## @invariants
    ##   - Idempotent: safe to call multiple times with same names
    ##   - Does not fail if Docker is unavailable (silent no-op)
    ##   - Does not fail if network already exists (inspect succeeds)
    """
    if docker_available is None:
        docker_available = shutil.which("docker") is not None
    if not docker_available:
        return
    for name in names:
        result = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            subprocess.run(
                ["docker", "network", "create", name],
                capture_output=True,
                check=False,
            )


# endregion SHARED_NETWORK_UTILITIES
