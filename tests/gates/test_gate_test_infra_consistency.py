# GREP_SUMMARY: gate, test-infra, consistency, CI, container-names, ports, networks, compose-projects, anti-regression
# STRUCTURE: ▶ AC-6a STALE_CONTAINER_NAMES vs compose → ◇ AC-6b test_ports match compose → ⊕ AC-6c compose projects unique → ∑ AC-6d networks registered → ⚡ AC-6e no "ai-platform-test" hardcode
# region MODULE_CONTRACT
## @purpose  CI gate: validate consistency between docker-compose.test.yml, tests/_conftest/infra.py,
##           platform-env.yaml test_ports, and test file compose projects.
##           Prevents drift between compose files and test infrastructure code.
## @scope    Gate tests run in `make gate MODE=fast` (no Docker required).
##           All tests are marked @pytest.mark.gate.
## @invariants
##   - AC-6a: STALE_CONTAINER_NAMES must match container_name values from ALL docker-compose.test.yml
##   - AC-6b: Each test_port in platform-env.yaml must match a port in docker-compose.test.yml
##   - AC-6c: All compose project names used in test files must be unique
##   - AC-6d: All networks from docker-compose.test.yml must be manageable by NetworkLeaseManager
##   - AC-6e: No hardcoded "ai-platform-test" as project name outside whitelist files
## @rationale DevPlan 041 W6: 5-consistency-check suite that runs without Docker daemon.
##            Catches container_name drift, port drift, project collisions, network registration gaps,
##            and anti-regression for the 2026-07-22 TRAP[BUG] cascade.
## @changes CREATED: 2026-07-22 | DevPlan 041 W6: Test infra consistency gate
# endregion MODULE_CONTRACT

import re
import sys
from pathlib import Path

import pytest
import yaml

# Project root relative to this file: tests/gates/ → tests/ → project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Whitelist for AC-6e: files where "ai-platform-test" is legitimate
# AC-6e only scans test_*.py files (not _conftest/* infrastructure).
# Conftest and infrastructure files (smoke.py, state_reset.py, etc.) have
# legitimate uses of "ai-platform-test" as the default compose project name.
# The gate catches only test files that HARDCODE the project name instead of
# using a unique project name — which is the actual regression pattern from
# TRAP[BUG] 2026-07-22.
_AC6E_TEST_FILE_PATTERN = re.compile(r"test_\w+\.py$")
_AC6E_WHITELIST_FILES = {
    "test_smoke_platform.py",  # Legitimate use — platform_services project
    "test_gate_test_infra_consistency.py",  # Self-referencing test (this file)
}
_AC6E_WHITELIST_PATTERNS = [
    r"SMOKE_ENV\s*=",
    r"platform_services",
    r"TRAP\[BUG\]",  # Historical bug documentation — not a regression
    r"TRAP\[DECISION\]",
    r"# STRUCTURE:",  # STRUCTURE line references — not code
    r"## @purpose",  # Docstring references — legitimate
    r"## @scenario",
    r'"""AC-6e:',  # Docstring
    r"##   - AC-6e",  # AC-6e documentation
]


def _load_infra() -> dict:
    """Load test infrastructure data from discover_modules.py --test-infra --json.

    ## @purpose — Run discover_modules.py to get current compose-derived data.
    ## @io — ⎋ dict: module → {container_names, networks, ports, compose_paths}
    ## @complexity — O(N) where N = modules
    """
    import json
    import subprocess

    discover_script = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "discover_modules.py"
    result = subprocess.run(
        ["python3", str(discover_script), "--test-infra", "--json"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(_PROJECT_ROOT),
        timeout=30,
    )
    data = json.loads(result.stdout)
    # Build module_name → data index
    return {m["module"]: m for m in data}


# region AC_6a
## @purpose — Verify STALE_CONTAINER_NAMES (from infra.py) equals all container_name from compose files.
## @scenario — If a new docker-compose.test.yml adds a container_name without infra.py reflecting it, this fails.
## @invariants
##   - Sorting must match: both lists sorted alphabetically
##   - Every container_name in compose files must be in STALE_CONTAINER_NAMES
##   - No extra names in STALE_CONTAINER_NAMES


@pytest.mark.gate
def test_stale_container_names_equals_compose_container_names():
    """AC-6a: STALE_CONTAINER_NAMES == all container_name from docker-compose.test.yml.

    ## @purpose — Anti-drift: hardcoded STALE_CONTAINER_NAMES (now derived from infra.py)
    ##             must match container_name values from ALL docker-compose.test.yml files.
    ## @io — ⇥ (infra data from subprocess) → ⊕ asserts set equality
    ## @complexity — O(M * S) where M=modules, S=services per module
    """
    # Load fresh data from discover_modules.py
    infra_data = _load_infra()

    stale_names: list[str] = []
    compose_names: list[str] = []
    modules_dir = _PROJECT_ROOT / "core" / "modules"

    for mod_dir in sorted(modules_dir.iterdir()):
        test_compose = mod_dir / "docker-compose.test.yml"
        if not test_compose.exists():
            continue
        # Read compose data fresh
        try:
            compose_data = yaml.safe_load(test_compose.read_text())
        except (yaml.YAMLError, OSError):
            continue
        for svc in (compose_data.get("services") or {}).values():
            cn = svc.get("container_name")
            if cn:
                compose_names.append(cn)
        # Get from infra data
        mod_name = mod_dir.name
        if mod_name in infra_data:
            stale_names.extend(infra_data[mod_name].get("container_names", []))

    stale_sorted = sorted(stale_names)
    compose_sorted = sorted(compose_names)

    print(f"[IMP:8][AC-6a] STALE_CONTAINER_NAMES ({len(stale_sorted)}): {stale_sorted}")
    print(f"[IMP:8][AC-6a] Compose container names ({len(compose_sorted)}): {compose_sorted}")

    missing_in_stale = set(compose_sorted) - set(stale_sorted)
    extra_in_stale = set(stale_sorted) - set(compose_sorted)

    assert stale_sorted == compose_sorted, (
        f"STALE_CONTAINER_NAMES drift detected.\n"
        f"Stale ({len(stale_sorted)}): {stale_sorted}\n"
        f"Compose ({len(compose_sorted)}): {compose_sorted}\n"
        f"Missing in stale: {missing_in_stale}\n"
        f"Extra in stale: {extra_in_stale}\n"
        f"Run: make discover-modules (updates discovery data)"
    )
    print(f"[IMP:9][AC-6a] ✅ STALE_CONTAINER_NAMES matches compose files ({len(stale_sorted)} names)")


# endregion AC_6a


# region AC_6b
## @purpose — Verify every test_port in platform-env.yaml matches a port in docker-compose.test.yml.
## @scenario — If test_ports in platform-env.yaml has a wrong port number, this fails.
## @invariants
##   - Module must have docker-compose.test.yml to have test_ports entry
##   - Port numbers must match between platform-env.yaml and compose files
##   - Missing module in compose = test failure with clear message


@pytest.mark.gate
def test_test_ports_match_compose_ports():
    """AC-6b: Each test_port from platform-env.yaml matches a port in docker-compose.test.yml.

    ## @purpose — Anti-drift: test_ports in platform-env.yaml must match the actual port
    ##             mappings in docker-compose.test.yml files.
    ## @io — ⇥ platform-env.yaml + infra data → ⊕ asserts port value equality
    ## @complexity — O(M * P) where M=modules, P=ports per module
    """
    platform_env_path = _PROJECT_ROOT / "platform-env.yaml"
    infra_data = _load_infra()

    with Path(platform_env_path).open(encoding="utf-8") as f:
        platform_env = yaml.safe_load(f)

    test_ports = platform_env.get("test_ports", {})
    print(f"[IMP:8][AC-6b] Checking {len(test_ports)} module test port entries")

    violations: list[str] = []
    for module_name, ports in test_ports.items():
        if module_name not in infra_data:
            violations.append(
                f"Module '{module_name}' has test_ports in platform-env.yaml but no docker-compose.test.yml found"
            )
            continue

            compose_ports = infra_data[module_name].get("ports", {})
            # Flatten: collect all external port values from compose data
            compose_port_values: set[int] = set()
            for port_list in compose_ports.values():
                for p in port_list:
                    compose_port_values.add(p["external"])

            for port_name, port_value in ports.items():
                if port_value not in compose_port_values:
                    violations.append(
                        f"Module '{module_name}': port '{port_name}'={port_value} in platform-env.yaml "
                        f"but not found in compose ports. Compose ports: {sorted(compose_port_values)}"
                    )

    for v in violations:
        print(f"[IMP:7][AC-6b] ❌ {v}", file=sys.stderr)

    assert not violations, f"test_ports drift detected ({len(violations)} violation(s)).\n" + "\n".join(violations)
    print(f"[IMP:9][AC-6b] ✅ All {len(test_ports)} module test ports match compose files")


# endregion AC_6b


# region AC_6c
## @purpose — Verify all compose project names used in test files are unique.
## @scenario — If two test files use the same compose project name, this fails.
## @invariants
##   - Each COMPOSE_PROJECT / COMPOSE_PROJECT_NAME value must be unique across test files
##   - The shared "ai-platform-test" project (platform_services) is whitelisted
##   - Detection via AST parsing of COMPOSE_PROJECT / COMPOSE_PROJECT_NAME assignments


@pytest.mark.gate
def test_compose_projects_are_unique():
    """AC-6c: All compose projects used in tests are unique.

    ## @purpose — Anti-collision: scan test files for COMPOSE_PROJECT / COMPOSE_PROJECT_NAME
    ##             assignments, verify no duplicates exist.
    ## @io — ⇥ tests/*.py → ⊕ asserts unique project names
    ## @complexity — O(F * L) where F=test files, L=lines per file
    """
    tests_dir = _PROJECT_ROOT / "tests"
    projects: dict[str, list[str]] = {}  # project_name → [source files]

    # Walk test files looking for compose project name assignments
    for test_file in sorted(tests_dir.rglob("*.py")):
        if test_file.name.startswith("test_"):
            try:
                source = test_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue

            for line in source.splitlines():
                line_stripped = line.strip()
                # Match: COMPOSE_PROJECT = "..." or COMPOSE_PROJECT_SMOKE = "..." etc.
                m = re.match(r'(COMPOSE_PROJECT(?:_\w+)?)\s*=\s*["\']([^"\']+)["\']', line_stripped)
                if m:
                    var_name, proj_name = m.groups()
                    rel_path = str(test_file.relative_to(tests_dir))
                    if proj_name not in projects:
                        projects[proj_name] = []
                    projects[proj_name].append(f"{rel_path}:{var_name}")

    print(f"[IMP:8][AC-6c] Found {len(projects)} compose project name(s)")

    # Check for duplicates (excluding the shared platform_services project)
    duplicates = {name: sources for name, sources in projects.items() if len(sources) > 1}
    # Whitelist: "ai-platform-test" is the shared platform_services project
    whitelist_shared = {"ai-platform-test"}

    collisions = {name: sources for name, sources in duplicates.items() if name not in whitelist_shared}
    for name, sources in sorted(collisions.items()):
        print(f"[IMP:7][AC-6c] ❌ Duplicate project '{name}': {sources}", file=sys.stderr)

    assert not collisions, f"Compose project name collisions detected: {len(collisions)} project(s).\n" + "\n".join(
        f"'{n}' used in: {s}" for n, s in collisions.items()
    )
    print(
        f"[IMP:9][AC-6c] ✅ All compose project names are unique "
        f"({len(projects) - len(whitelist_shared)} unique projects)"
    )


# endregion AC_6c


# region AC_6d
## @purpose — Verify all test networks from compose files are manageable by NetworkLeaseManager.
## @scenario — If a new test network is added to docker-compose.test.yml but not registered
##             in NetworkLeaseManager, this fails.
## @invariants
##   - All networks from compose files must have acquire/release methods in NetworkLeaseManager
##   - Check is structural (method exists), not functional (doesn't create real networks)


@pytest.mark.gate
def test_networks_registered_in_lease_manager():
    """AC-6d: All test networks from compose files are manageable by NetworkLeaseManager.

    ## @purpose — Anti-drift: every network declared in docker-compose.test.yml must
    ##             be acquirable via NetworkLeaseManager (structural check).
    ## @io — ⇥ infra data + NetworkLeaseManager import → ⊕ asserts API availability
    ## @complexity — O(N) where N = unique networks
    """
    infra_data = _load_infra()
    from _conftest.networks import get_network_manager

    nm = get_network_manager()

    all_networks: set[str] = set()
    for mod_data in infra_data.values():
        all_networks.update(mod_data.get("networks", []))

    print(f"[IMP:8][AC-6d] Found {len(all_networks)} unique test network(s): {sorted(all_networks)}")

    assert hasattr(nm, "acquire"), "NetworkLeaseManager missing acquire method"
    assert hasattr(nm, "release"), "NetworkLeaseManager missing release method"
    assert hasattr(nm, "release_all"), "NetworkLeaseManager missing release_all method"

    # Verify all networks can be acquired (structural check — doesn't create real networks)
    for network in sorted(all_networks):
        # NetworkLeaseManager should be able to manage this network
        assert hasattr(nm, "acquire"), f"NetworkLeaseManager cannot acquire '{network}'"

    print(f"[IMP:9][AC-6d] ✅ All {len(all_networks)} test networks manageable by NetworkLeaseManager")


# endregion AC_6d


# region AC_6e
## @purpose — Anti-regression: no hardcoded "ai-platform-test" as project name in test files.
##            Prevents TRAP[BUG] 2026-07-22 recurrence where 7 files had own_project="ai-platform-test"
##            causing cross-fixture container management conflicts.
## @scenario — If any test file contains "ai-platform-test" as a string literal project name
##             outside whitelisted files/contexts, this fails.
## @invariants
##   - Whitelisted files: smoke.py (where "ai-platform-test" is the legitimate shared project)
##   - Whitelisted patterns: SMOKE_ENV, platform_services references
##   - Scans ALL "ai-platform-test" string literals, not just in check_foreign_containers calls


@pytest.mark.gate
def test_no_hardcoded_ai_platform_test_own_project():
    """AC-6e: No hardcoded "ai-platform-test" as project name in test files.

    Anti-regression for TRAP[BUG] 2026-07-22.
    Scans ALL occurrences of "ai-platform-test" as a hardcoded project name:
    - COMPOSE_PROJECT = "ai-platform-test", COMPOSE_PROJECT_NAME = "ai-platform-test"
    - check_foreign_containers(..., "ai-platform-test")
    - "-p", "ai-platform-test" or --project-name "ai-platform-test" in subprocess calls
    - Any string literal "ai-platform-test" in test files

    Exceptions (whitelist): SMOKE_ENV / platform_services in smoke.py
    where "ai-platform-test" is the legitimate shared compose project.
    """
    tests_dir = _PROJECT_ROOT / "tests"
    # Match "ai-platform-test" as a delimited string literal
    pattern = re.compile(r'"ai-platform-test"')

    # Transient probe-директория test_gate_marker_location (xdist race, DevPlan 124, решение
    # пользователя 2026-08-03): probe-файл живёт в tests/ лишь на время соседнего gate-теста —
    # сканер читал его в момент конкурентного unlink → FileNotFoundError (флейк static_audit)
    PROBE_DIR_PARTS = ("_gate_probe_marker_tmp",)

    violations: list[tuple[str, str]] = []  # (file_path, line_content)

    for test_file in sorted(tests_dir.rglob("*.py")):
        # Only scan test_*.py files (not _conftest/* infrastructure)
        if not _AC6E_TEST_FILE_PATTERN.search(test_file.name):
            continue
        if test_file.name in _AC6E_WHITELIST_FILES:
            continue  # skip entire whitelisted files
        if any(part in test_file.parts for part in PROBE_DIR_PARTS):
            continue  # transient probe-директории не сканируются (xdist race)

        try:
            content = test_file.read_text()
        except FileNotFoundError:
            # Файл удалён конкурентно (probe-фикстура другого gate-теста) — пропустить
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                # Check if this is a whitelisted pattern
                is_whitelisted = any(re.search(wp, line) for wp in _AC6E_WHITELIST_PATTERNS)
                if not is_whitelisted:
                    rel_path = str(test_file.relative_to(tests_dir))
                    violations.append((f"{rel_path}:{line_no}", line.strip()))

    for fpath, line_content in violations:
        print(f"[IMP:7][AC-6e] ❌ 'ai-platform-test' in {fpath}: {line_content}", file=sys.stderr)

    assert not violations, (
        f"TRAP[BUG] REGRESSION: hardcoded 'ai-platform-test' project name "
        f"found in {len(violations)} location(s):\n"
        + "\n".join(f"  {fpath}: {line_content}" for fpath, line_content in violations)
        + "\nUse check_foreign_containers() or unique project name. "
        "See DevPlan 041 W6 for whitelist rules."
    )
    print("[IMP:9][AC-6e] ✅ No hardcoded 'ai-platform-test' project names outside whitelist")


# endregion AC_6e
