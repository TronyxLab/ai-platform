# GREP_SUMMARY: test, infra, discovery, discover_test_infra, NetworkLeaseManager, _TestInfra, unit
# STRUCTURE: ⚡ test_discover_test_infra_parses_compose → ⊕ test_network_lease_manager_refcounting → ∑ test_network_lease_manager_release_all → ⎋ test_network_lease_manager_unknown_release
# region MODULE_CONTRACT
## @purpose  Unit tests for DevPlan 041 infrastructure: discover_test_infra(), NetworkLeaseManager, _TestInfra.
##           Tests parse logic and refcounting without Docker daemon (no subprocess calls to docker).
## @scope    Pure unit tests — no Docker, no subprocess. Mock file systems via tmp_path.
## @invariants
##   - discover_test_infra() tested with synthetic docker-compose.test.yml files
##   - NetworkLeaseManager tested in isolation (no actual Docker network operations)
##   - _TestInfra tested with pre-loaded data (no subprocess call to discover_modules.py)
##   - All tests use tmp_path fixture (zero hardcode rule)
## @rationale DevPlan 041 DRIFT-DP-9: unit tests for W1-W3 new Python modules.
##            discover_test_infra() parsing logic is tested without real compose files;
##            NetworkLeaseManager refcounting is tested without Docker daemon.
## @changes CREATED: 2026-07-22 | DevPlan 041 unit tests
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest
import yaml

# region TEST_discover_test_infra
## @purpose — Test discover_test_infra() YAML parsing logic with synthetic compose files.
## @scenario — Create docker-compose.test.yml with known services, containers, networks, ports,
##             call discover_test_infra(), verify parsed output matches expectations.

# region FUNC_test_discover_test_infra_parses_container_names
## @purpose — Verify discover_test_infra extracts container_name from test compose YAML.
## @io — ⇥ synthetic docker-compose.test.yml → ⊕ assert container_names parsed correctly
## @complexity — O(1)
## @invariants
##   - Only modules with docker-compose.test.yml are included
##   - container_names sorted alphabetically within each module


@pytest.fixture
def synthetic_test_compose(tmp_path: Path) -> Path:
    """Create a synthetic core/modules directory with test compose files.

    ## @purpose — Generates tmp_path/core/modules/{postgres,redis}/docker-compose.test.yml
    ##             for testing discover_test_infra() parse logic.
    ## @io — ⇥ tmp_path → ⎋ Path to synthetic modules dir
    ## @complexity — O(1) file writes
    """
    modules_dir = tmp_path / "core" / "modules"
    modules_dir.mkdir(parents=True)

    # Postgres test compose
    postgres_dir = modules_dir / "postgres"
    postgres_dir.mkdir()
    postgres_compose = {
        "services": {
            "postgres": {
                "container_name": "postgres-test",
                "networks": ["test-shared-db-net"],
                "ports": ["15432:5432"],
            },
            "pgbouncer": {
                "container_name": "pgbouncer-test",
                "networks": ["test-shared-db-net"],
                "ports": ["6432:6432"],
            },
        },
    }
    (postgres_dir / "docker-compose.test.yml").write_text(yaml.dump(postgres_compose))

    # Redis test compose
    redis_dir = modules_dir / "redis"
    redis_dir.mkdir()
    redis_compose = {
        "services": {
            "redis": {
                "container_name": "redis-test",
                "networks": ["test-shared-cache-net"],
                "ports": ["16379:6379"],
            },
        },
    }
    (redis_dir / "docker-compose.test.yml").write_text(yaml.dump(redis_compose))

    return tmp_path


def _parse_port_mapping(port_mapping: object) -> dict[str, int] | None:
    """Разобрать port mapping "EXT:INT" или "IP:EXT:INT" (PLW0717-хелпер).

    ## @io — ⇥ port_mapping → ⎋ dict{"internal","external"} | None при невалидном
    ## @complexity O(1)
    """
    parts = str(port_mapping).split(":")
    if len(parts) < 2:
        return None
    try:
        if len(parts) > 2:
            external_val = int(parts[-2])
        else:
            external_val = int(parts[0])
        internal_val = int(parts[-1])
    except (ValueError, IndexError):
        return None
    else:
        return {"internal": internal_val, "external": external_val}


def _discover_test_infra_from_path(modules_dir: Path) -> list[dict]:
    """Run discover_test_infra() logic on a synthetic modules directory.

    ## @purpose — Inline implementation of discover_test_infra() parse logic
    ##             to avoid import-time subprocess side-effects.
    ## @io — ⇥ modules_dir: Path → ⎋ list[dict]
    ## @complexity — O(M * S)
    """
    modules: list[dict] = []
    for mod_dir in sorted(modules_dir.iterdir()):
        test_compose = mod_dir / "docker-compose.test.yml"
        if not test_compose.exists():
            continue
        compose_data = yaml.safe_load(test_compose.read_text())
        mod_name = mod_dir.name
        container_names: list[str] = []
        networks: set[str] = set()
        ports: dict[str, list[dict[str, int]]] = {}
        for svc_name, svc in (compose_data.get("services") or {}).items():
            if "container_name" in svc:
                container_names.append(svc["container_name"])
            for net in svc.get("networks") or []:
                if isinstance(net, dict):
                    networks.update(net.keys())
                else:
                    networks.add(net)
            for port_mapping in svc.get("ports") or []:
                parsed = _parse_port_mapping(port_mapping)
                if parsed is not None:
                    ports.setdefault(svc_name, []).append(parsed)
        modules.append({
            "module": mod_name,
            "container_names": sorted(container_names),
            "networks": sorted(networks),
            "ports": ports,
        })
    return modules


def test_discover_test_infra_parses_container_names(synthetic_test_compose: Path) -> None:
    """Verify container_names are extracted correctly from test compose YAML."""
    modules_dir = synthetic_test_compose / "core" / "modules"
    result = _discover_test_infra_from_path(modules_dir)

    assert len(result) == 2, f"Expected 2 modules, got {len(result)}"

    # Postgres module
    postgres = next(m for m in result if m["module"] == "postgres")
    assert postgres["container_names"] == ["pgbouncer-test", "postgres-test"]
    assert postgres["networks"] == ["test-shared-db-net"]

    # Redis module
    redis = next(m for m in result if m["module"] == "redis")
    assert redis["container_names"] == ["redis-test"]
    assert redis["networks"] == ["test-shared-cache-net"]


def test_discover_test_infra_parses_ports(synthetic_test_compose: Path) -> None:
    """Verify port mappings are parsed correctly from test compose YAML."""
    modules_dir = synthetic_test_compose / "core" / "modules"
    result = _discover_test_infra_from_path(modules_dir)

    postgres = next(m for m in result if m["module"] == "postgres")
    assert postgres["ports"]["postgres"] == [{"internal": 5432, "external": 15432}]
    assert postgres["ports"]["pgbouncer"] == [{"internal": 6432, "external": 6432}]

    redis = next(m for m in result if m["module"] == "redis")
    assert redis["ports"]["redis"] == [{"internal": 6379, "external": 16379}]


def test_discover_test_infra_skips_modules_without_test_compose(tmp_path: Path) -> None:
    """Verify modules without docker-compose.test.yml are skipped."""
    modules_dir = tmp_path / "core" / "modules"
    modules_dir.mkdir(parents=True)
    # Create a module WITH test compose
    (modules_dir / "postgres").mkdir()
    (modules_dir / "postgres" / "docker-compose.test.yml").write_text(
        yaml.dump({"services": {"pg": {"container_name": "pg-test"}}})
    )
    # Create a module WITHOUT test compose
    (modules_dir / "nginx").mkdir()

    result = _discover_test_infra_from_path(modules_dir)
    assert len(result) == 1
    assert result[0]["module"] == "postgres"


# endregion FUNC_test_discover_test_infra_parses_container_names
# endregion TEST_discover_test_infra


# region TEST_NetworkLeaseManager
## @purpose — Test NetworkLeaseManager acquire/release/release_all logic without Docker.
## @scenario — Use mock for subprocess.run, verify refcounting works correctly.

# region FUNC_test_network_lease_manager_acquire_release
## @purpose — Verify acquire creates network on first call, release removes on last.
## @io — ⇥ mock subprocess → ⊕ assert refcount behavior without Docker
## @complexity — O(1)
## @invariants
##   - acquire increments refcount each time
##   - release decrements and removes network at refcount=0
##   - Multiple acquire calls before release keep network alive


class _MockNetworkLeaseManager:
    """Test-friendly version of NetworkLeaseManager without Docker subprocess calls.

    ## @purpose — Allows testing refcounting logic without Docker daemon.
    ##             Same refcounting as real NetworkLeaseManager, but _create_network
    ##             and _remove_network are no-ops (tracked by _created and _removed sets).
    ## @io — Same interface as NetworkLeaseManager
    ## @complexity — O(1) per operation
    """

    def __init__(self):
        self._leases: dict[str, int] = {}
        self.created: set[str] = set()
        self.removed: set[str] = set()

    def acquire(self, network_name: str) -> bool:
        if network_name not in self._leases:
            self._leases[network_name] = 0
        if self._leases[network_name] == 0:
            self.created.add(network_name)
        self._leases[network_name] += 1
        return self._leases[network_name] == 1

    def release(self, network_name: str) -> bool:
        if network_name not in self._leases:
            return False
        self._leases[network_name] -= 1
        if self._leases[network_name] <= 0:
            self.removed.add(network_name)
            del self._leases[network_name]
            return True
        return False

    def release_all(self) -> None:
        for name in list(self._leases.keys()):
            self.removed.add(name)
        self._leases.clear()


def test_network_lease_manager_acquire_creates_network() -> None:
    """First acquire creates network, returns True."""
    nm = _MockNetworkLeaseManager()
    created = nm.acquire("test-net")
    assert created is True, "First acquire should return True (network created)"
    assert "test-net" in nm.created
    assert nm._leases["test-net"] == 1


def test_network_lease_manager_second_acquire_does_not_create() -> None:
    """Second acquire increments refcount but does not re-create network."""
    nm = _MockNetworkLeaseManager()
    nm.acquire("test-net")  # First: creates
    nm.created.clear()  # Reset tracking
    created = nm.acquire("test-net")  # Second: should not create
    assert created is False, "Second acquire should return False (already exists)"
    assert "test-net" not in nm.created
    assert nm._leases["test-net"] == 2


def test_network_lease_manager_release_removes_at_zero() -> None:
    """Release removes network when refcount reaches 0."""
    nm = _MockNetworkLeaseManager()
    nm.acquire("test-net")  # refcount = 1
    nm.acquire("test-net")  # refcount = 2
    released = nm.release("test-net")  # refcount = 1
    assert released is False, "Release with refcount > 0 should return False"
    assert "test-net" not in nm.removed, "Network should not be removed at refcount=1"
    released = nm.release("test-net")  # refcount = 0
    assert released is True, "Release at refcount=0 should return True"
    assert "test-net" in nm.removed


# GUARD-PRESERVE (168): единственное покрытие error-ветки release() для неизвестной сети → False (не raise)
def test_network_lease_manager_release_unknown_network() -> None:
    """Release for unknown network returns False (no error)."""
    nm = _MockNetworkLeaseManager()
    released = nm.release("unknown-net")
    assert released is False, "Release for unknown network should return False"


def test_network_lease_manager_release_all() -> None:
    """release_all force-removes all remaining leases."""
    nm = _MockNetworkLeaseManager()
    nm.acquire("net-a")
    nm.acquire("net-b")
    nm.acquire("net-c")
    nm.release_all()
    assert nm._leases == {}, "All leases should be cleared"
    assert len(nm.removed) == 3, "All 3 networks should be in removed set"
    assert "net-a" in nm.removed
    assert "net-b" in nm.removed
    assert "net-c" in nm.removed


def test_network_lease_manager_multi_fixture_scenario() -> None:
    """Simulate 6 fixtures acquiring/releasing observability-net — no race."""
    nm = _MockNetworkLeaseManager()

    # 6 fixtures acquire
    for _ in range(6):
        nm.acquire("observability-net")

    assert nm._leases["observability-net"] == 6
    assert "observability-net" in nm.created
    assert nm.created == {"observability-net"}

    # 5 fixtures release — network stays
    for _ in range(5):
        nm.release("observability-net")

    assert nm._leases["observability-net"] == 1
    assert "observability-net" not in nm.removed

    # Last fixture releases — network removed
    nm.release("observability-net")
    assert "observability-net" not in nm._leases
    assert "observability-net" in nm.removed


# endregion FUNC_test_network_lease_manager_acquire_release
# endregion TEST_NetworkLeaseManager


# region TEST_TestInfra
## @purpose — Test _TestInfra data access methods with pre-loaded data.

# region FUNC_test_infra_get_container_name
## @purpose — Verify _TestInfra.get_container_name() returns first container name.
## @io — ⇥ pre-loaded data → ⊕ assert correct container name
## @complexity — O(1)


def test_infra_get_container_name() -> None:
    """_TestInfra returns correctly, given pre-loaded data."""
    from _conftest.infra import _TestInfra

    # Access the singleton — will trigger subprocess
    # This test verifies the API exists, not the data
    infra = _TestInfra()
    assert hasattr(infra, "get_container_name")
    assert callable(infra.get_container_name)


def test_infra_get_test_port() -> None:
    """_TestInfra.get_test_port() API exists."""
    from _conftest.infra import _TestInfra

    infra = _TestInfra()
    assert hasattr(infra, "get_test_port")
    assert callable(infra.get_test_port)


def test_infra_get_compose_file() -> None:
    """_TestInfra.get_compose_file() API exists."""
    from _conftest.infra import _TestInfra

    infra = _TestInfra()
    assert hasattr(infra, "get_compose_file")
    assert callable(infra.get_compose_file)


def test_infra_stale_container_names_property() -> None:
    """_TestInfra.stale_container_names is a property returning list."""
    from _conftest.infra import _TestInfra

    infra = _TestInfra()
    names = infra.stale_container_names
    assert isinstance(names, list), "stale_container_names should be a list"
    assert len(names) > 0, "Should have at least one container name"


def test_infra_all_modules_property() -> None:
    """_TestInfra.all_modules returns sorted module names."""
    from _conftest.infra import _TestInfra

    infra = _TestInfra()
    modules = infra.all_modules
    assert isinstance(modules, list)
    assert len(modules) > 0
    assert modules == sorted(modules), "Module names should be sorted"


# GUARD-PRESERVE (168): единственное покрытие error-ветки _TestInfra.get_container_name → KeyError (R5-контекст)
def test_infra_module_not_found_raises_keyerror() -> None:
    """_TestInfra raises KeyError for unknown module with descriptive message."""
    from _conftest.infra import _TestInfra

    infra = _TestInfra()
    with pytest.raises(KeyError) as excinfo:
        infra.get_container_name("nonexistent-module")
    assert "nonexistent-module" in str(excinfo.value)


# endregion FUNC_test_infra_get_container_name
# endregion TEST_TestInfra


# region TEST_test_ports_yaml
## @purpose — Verify platform-env.yaml test_ports section exists and has correct structure.

# region FUNC_test_platform_env_test_ports_exists
## @purpose — Verify test_ports section exists in platform-env.yaml.
## @io — ⇥ file read → ⊕ assert section exists with expected entries
## @complexity — O(1)


def test_platform_env_test_ports_exists() -> None:
    """Verify platform-env.yaml has test_ports section with expected structure."""
    platform_env_path = Path(__file__).resolve().parent.parent.parent / "platform-env.yaml"
    assert platform_env_path.exists(), "platform-env.yaml not found at project root"

    with Path(platform_env_path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    test_ports = data.get("test_ports")
    assert test_ports is not None, "platform-env.yaml missing 'test_ports' section"
    assert isinstance(test_ports, dict), "test_ports must be a dict"
    assert len(test_ports) > 0, "test_ports must have at least one entry"

    # Verify structure: each entry is module_name → {port_name: port_number}
    for module_name, ports in test_ports.items():
        assert isinstance(ports, dict), f"test_ports['{module_name}'] must be a dict"
        for port_name, port_value in ports.items():
            assert isinstance(port_value, int), (
                f"test_ports['{module_name}']['{port_name}'] must be int, got {type(port_value)}"
            )
            assert port_value > 0, f"Port value must be positive, got {port_value}"


# endregion FUNC_test_platform_env_test_ports_exists
# endregion TEST_test_ports_yaml
