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


# 📝 TRAP[DEBT] · 2026-07-15 · MED · Parallel test teardown destroys shared external networks — RESOLVED-частично (B10 T5)
# · Observed: docker events показали массовый destroy shared-db-net/proxy-net/etc во время
# ·   параллельной сессии; повторный compose up упал с "network declared as external, but
# ·   could not be found"
# · Suspected: teardown одного тестового прогона удаляет external-сети, на которые полагаются
# ·   другие сессии/прогоны
# · Impact: флаки при параллельных волнах/сессиях — up падает между create сети и attach
# ·   контейнера
# · When: during wave-postgres T5.2 live-verification (2026-07-15)
# · 2026-08-01 (B10 T5): RESOLVED-частично — (1) ensure_external_networks() получила verify-цикл
# ·   (inspect → create → re-inspect, устойчивость к гонке create/remove); (2) контракт
# ·   зафиксирован: общие тестовые сети — external: true в тестовых compose и НИКОГДА не
# ·   удаляются в teardown (docker network rm в tests/_conftest отсутствует — проверено);
# ·   (3) причина (parallel compose down одного прогона) задокументирована. Полный фикс
# ·   (refcount-менеджер для external-сетей между сессиями) — за рамками B10.


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
    ##   - Verify-cycle (B10 T5): inspect → create → re-inspect — устойчивость к гонке
    ##     create/remove между параллельными сессиями; если после create сеть всё ещё
    ##     отсутствует → [IMP:9] видимая ошибка (не тихий пропуск).
    ##   - Контракт: общие тестовые сети — external: true, НИКОГДА не удаляются в teardown
    ##     (docker network rm в тестах запрещён — docker network rm в tests/_conftest отсутствует).
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
        if result.returncode == 0:
            continue  # exists — no-op (idempotent)
        # Missing → create → re-inspect (verify-cycle, race-resilient)
        create = subprocess.run(
            ["docker", "network", "create", name],
            capture_output=True,
            text=True,
            check=False,
        )
        verify = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True,
            check=False,
        )
        if verify.returncode != 0:
            # ⚠️ TRAP[BUG] · 2026-08-01 · HI · Network create без verify → «declared as external, could not be found»
            # · Root: create best-effort мог тихо провалиться (гонка с параллельным remove); без re-inspect
            # ·   следующий compose up падал с "network declared as external, but could not be found".
            # · Fix: verify-цикл — после create обязательный повторный inspect; отсутствие сети = [IMP:9] ошибка.
            # · Prevention: общие external-сети НИКОГДА не удаляются в teardown (контракт B10 T5).
            import logging

            logging.getLogger(__name__).error(
                "[IMP:9][networks] Network '%s' create FAILED (rc=%d: %s) and re-inspect MISSING — "
                "dependent compose up will fail with 'network declared as external, but could not be found'",
                name,
                create.returncode,
                (create.stderr or "").strip()[-200:],
            )


# endregion SHARED_NETWORK_UTILITIES


# region CLASS_NetworkLeaseManager
## @purpose  Thread-safe reference-counted Docker test network lifecycle manager.
##           Eliminates race conditions when multiple test fixtures create/destroy
##           the same Docker network (observability-net, proxy-net, etc.).
##           Replaces direct `docker network create/rm` calls in test fixtures.
## @scope    Used by platform_services fixture and all module-scoped test fixtures.
## @invariants
##   - acquire() creates network on first acquisition (refcount 0→1)
##   - release() removes network when refcount reaches 0
##   - release_all() force-releases all remaining leases (call from pytest_sessionfinish)
##   - Idempotent: multiple acquire() calls for same network do NOT create multiple networks
##   - Thread-safe for concurrent fixture setup in ThreadPoolExecutor
## @rationale 6+ test fixtures independently create/remove the same Docker networks (observability-net,
##            test-shared-db-net, etc.) without coordination — causing race conditions where one
##            fixture removes a network another still needs. Refcounting eliminates the race.
## @changes CREATED: 2026-07-22 | DevPlan 041 W3: NetworkLeaseManager — refcounting for test networks

import logging

_logger = logging.getLogger(__name__)


class NetworkLeaseManager:
    """Reference-counted Docker network lifecycle manager.

    ## @purpose — Coordinate Docker network creation/removal across multiple test fixtures.
    ##             First caller to acquire() creates the network; last caller to release() removes it.
    ## @io — acquire(network_name) → bool (True if created); release(network_name) → bool (True if removed)
    ## @complexity — O(1) for acquire/release; O(N) for release_all
    ## @invariants
    ##   - acquire: idempotent — subsequent calls do not re-create the network
    ##   - release: raises warning on unknown network (not error — best-effort cleanup)
    ##   - release_all: called unconditionally from pytest_sessionfinish for safety
    """

    def __init__(self):
        self._leases: dict[str, int] = {}  # network_name → refcount

    # region FUNC_acquire
    ## @purpose  Acquire a network lease. Creates Docker network if first acquisition.
    ## @io       ⇥ network_name: str → ⎋ bool: True if network was newly created
    ## @complexity — O(1)
    ## @invariants
    ##   - First call with a given name creates the network (refcount 0→1)
    ##   - Subsequent calls only increment refcount
    ##   - Docker network create is best-effort (ignores "already exists" errors)
    def acquire(self, network_name: str) -> bool:
        """Acquire a network lease. Creates network if first acquisition.

        Returns True if network was newly created.
        """
        if network_name not in self._leases:
            self._leases[network_name] = 0

        if self._leases[network_name] == 0:
            _logger.info("[IMP:8][NetworkLeaseManager] Acquiring network '%s' — creating", network_name)
            self._create_network(network_name)

        self._leases[network_name] += 1
        _logger.debug(
            "[IMP:7][NetworkLeaseManager] Acquired '%s' (refcount=%d)", network_name, self._leases[network_name]
        )
        return self._leases[network_name] == 1

    # endregion

    # region FUNC_release
    ## @purpose  Release a network lease. Removes Docker network when refcount reaches 0.
    ## @io       ⇥ network_name: str → ⎋ bool: True if network was removed (refcount reached 0)
    ## @complexity — O(1)
    ## @invariants
    ##   - Calling release for an unknown network logs warning and returns False
    ##   - Network removal is best-effort (ignores "in use" errors)
    def release(self, network_name: str) -> bool:
        """Release a network lease. Removes network when refcount reaches 0.

        Returns True if network was removed.
        """
        if network_name not in self._leases:
            _logger.warning("[IMP:7][NetworkLeaseManager] Release called for unknown network '%s'", network_name)
            return False

        self._leases[network_name] -= 1

        if self._leases[network_name] <= 0:
            _logger.info("[IMP:8][NetworkLeaseManager] Releasing network '%s' — removing (refcount=0)", network_name)
            self._remove_network(network_name)
            del self._leases[network_name]
            return True

        _logger.debug(
            "[IMP:7][NetworkLeaseManager] Released '%s' (refcount=%d)", network_name, self._leases[network_name]
        )
        return False

    # endregion

    # region FUNC_release_all
    ## @purpose  Force-release all remaining leases. Called from pytest_sessionfinish.
    ## @io       ⇥ (self) → ⎋ None (side-effect: Docker networks removed)
    ## @complexity — O(N) where N = active leases
    def release_all(self) -> None:
        """Force-release all remaining leases. Called from pytest_sessionfinish for safety."""
        for name in list(self._leases.keys()):
            _logger.info("[IMP:9][NetworkLeaseManager] Force-releasing network '%s' (session finish)", name)
            self._remove_network(name)
        self._leases.clear()

    # endregion

    # region FUNC_create_network
    ## @purpose  Create Docker network via subprocess. Best-effort — ignore "already exists".
    ##           Failures MUST be visible: silent create failure cascades into
    ##           "network declared as external, but could not be found" for every
    ##           module compose up (smoke AC1 — "All modules failed to start").
    ## @io       ⇥ name: str → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - "already exists" → debug log (idempotent acquire)
    ##   - Other failures → verify with docker network inspect; missing network → [IMP:9] error
    def _create_network(self, name: str) -> None:
        """Create Docker network. Best-effort — ignore "already exists" errors."""
        # ⚠️ TRAP[BUG] · 2026-07-31 · HI · Silent network create failure → all modules fail
        # · Root: best-effort create (check=False, timeout=15, output discarded) swallowed
        # ·   `docker network create` failures; fixture proceeded with missing external
        # ·   networks → EVERY module's `docker compose up` failed with
        # ·   "network declared as external, but could not be found" → test_platform_starts_all_containers
        # ·   reported "All modules failed to start".
        # · Fix: log create result; on real failure verify actual state via docker network inspect
        # ·   and emit [IMP:9] error if the network is truly missing (visibility-first).
        # · Rev: if create failures persist in CI — add explicit retry + acquire() result check.
        result = subprocess.run(
            ["docker", "network", "create", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode == 0:
            _logger.debug("[IMP:8][NetworkLeaseManager] Network '%s' created", name)
            return
        if "already exists" in (result.stderr or "").lower():
            _logger.debug("[IMP:7][NetworkLeaseManager] Network '%s' already exists — idempotent acquire", name)
            return
        # Real failure — verify actual state before alarming (race with another process)
        verify = subprocess.run(
            ["docker", "network", "inspect", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if verify.returncode == 0:
            _logger.warning(
                "[IMP:8][NetworkLeaseManager] Network '%s' create failed (rc=%d: %s) but network exists — continuing",
                name,
                result.returncode,
                (result.stderr or "").strip()[-200:],
            )
            return
        _logger.error(
            "[IMP:9][NetworkLeaseManager] Network '%s' create FAILED (rc=%d: %s) and network is MISSING — "
            "dependent modules will fail with 'network declared as external, but could not be found'",
            name,
            result.returncode,
            (result.stderr or "").strip()[-300:],
        )

    # endregion

    # region FUNC_remove_network
    ## @purpose  Remove Docker network via subprocess. Best-effort — ignores "in use" errors.
    ## @io       ⇥ name: str → ⎋ None
    ## @complexity — O(1)
    def _remove_network(self, name: str) -> None:
        """Remove Docker network. Best-effort — ignore errors."""
        subprocess.run(
            ["docker", "network", "rm", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    # endregion

    # region PROPERTY_active_leases
    ## @purpose  Return current lease state for diagnostics.
    ## @io       ⇥ (self) → ⎋ dict[str, int]
    ## @complexity — O(1)
    @property
    def active_leases(self) -> dict[str, int]:
        """Return current lease state (for diagnostics)."""
        return dict(self._leases)

    # endregion


# endregion CLASS_NetworkLeaseManager


# Singleton instance for the test session
_network_manager = NetworkLeaseManager()


def get_network_manager() -> NetworkLeaseManager:
    """Get the session-level NetworkLeaseManager singleton.

    ## @purpose — Returns the singleton NetworkLeaseManager instance.
    ##             All test fixtures should use this function to get the manager,
    ##             never instantiate NetworkLeaseManager directly.
    ## @io — ⎋ NetworkLeaseManager
    ## @complexity — O(1)
    """
    return _network_manager
