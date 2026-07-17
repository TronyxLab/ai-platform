# GREP_SUMMARY: docker-network external-net created-net platform-networks topology internal-conflict orphan naming-convention
# STRUCTURE: ▶ all_compose_files + all_module_yamls → ⚡ collect_networks_by_compose
# region MODULE_CONTRACT
# @file test_network_consistency.py
## @purpose — Validate Docker network topology consistency: every external network has a
#             creator module, created networks are registered in PLATFORM_NETWORKS,
#             no internal/external conflicts, no orphan platform networks, naming convention.
## @scope — Static audit of network declarations across all module compose files and
#           module.yaml config.network fields, validated against PLATFORM_NETWORKS
#           constant from deploy-modules.sh (line 36).
## @invariants
#   - Every network declared as external:true in compose must either be created by
#     a module (non-external) or be in PLATFORM_NETWORKS (pre-created by deploy-modules.sh)
#   - Every non-external network in compose must be in PLATFORM_NETWORKS
#   - No network may be declared as internal in one compose and external in another
#   - Every PLATFORM_NETWORKS entry must be used by at least one module
#   - All network names must follow the pattern *-net
## @rationale — Network misconfiguration (orphan networks, missing external declarations)
#             causes docker compose up failures and cross-module connectivity issues.
#             PLATFORM_NETWORKS is the single source of truth for pre-created networks.
# endregion MODULE_CONTRACT
#
#           → ◇ test_all_external_networks_created(external:true → creator∃)
#           → ◇ test_all_created_networks_in_platform_list(non-external → ∈PLATFORM_NETWORKS)
#           → ◇ test_no_internal_external_conflict(same_net ≠ internal⊕external)
#           → ◇ test_no_orphan_platform_networks(PLATFORM_NETWORKS → ∃user)
#           → ◇ test_network_naming_convention(name ≡ *-net)

import logging
import re

import pytest
import yaml
from conftest import (
    EXEMPT_CREATED_NETWORKS,
    PLATFORM_NETWORKS,
    ldd_trajectory,
)

logger = logging.getLogger(__name__)

# PLATFORM_NETWORKS and EXEMPT_CREATED_NETWORKS are now defined in conftest.py
# (NETWORK_CONSTANTS region). Imported above to ensure single source of truth.


def _parse_compose_networks(compose_path: str) -> dict:
    """Parse top-level networks from a docker-compose.base.yml.
    ## @purpose — Extract {network_name: network_config} from compose, normalising external flag.
    ## @io — ⇥ compose_path: str → ⎋ dict[str, dict]: {net_name: {external: bool, internal: bool, ...}}
    ## @complexity — O(Y) where Y = compose YAML size
    """
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    compose_nets = (data or {}).get("networks", {}) or {}
    result: dict = {}
    for net_name, net_conf in compose_nets.items():
        if net_conf is None:
            result[net_name] = {}
        elif isinstance(net_conf, bool):
            result[net_name] = {"external": net_conf}
        elif isinstance(net_conf, dict):
            result[net_name] = net_conf
        else:
            result[net_name] = {}
    return result


# region --- Tests ---


@pytest.mark.static_audit
@ldd_trajectory
def test_all_external_networks_created(all_compose_files, caplog) -> None:
    # · Scenario: module declares network external:true but it's neither created by another module nor pre-created by deploy-modules.sh → docker compose up fails with "network not found"
    # · Last fail: never — guard test
    # · Remove if: external network convention removed from platform
    """## @purpose — Every network declared as external:true in any compose must have a
    #             creator — either a module that declares it without external:true,
    #             or be in PLATFORM_NETWORKS (pre-created by deploy-modules.sh).
    ## @io — ⇥ all_compose_files: dict[str, str] → ⎋ None (assert)
    ## @complexity — O(N * Y) where N = compose count, Y = YAML parse size
    """

    logger.info("[IMP:7][test_all_external_networks_created] Checking external networks have creators...")

    # Collect all external network references from compose files
    external_nets: set[str] = set()  # networks declared as external:true
    created_nets: set[str] = set()  # networks declared WITHOUT external:true (module creates them)

    for compose_path in all_compose_files.values():
        try:
            nets = _parse_compose_networks(compose_path)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:4][test_all_external_networks_created] Failed to parse %s: %s", compose_path, exc)
            continue

        for net_name, net_conf in nets.items():
            ext_val = net_conf.get("external", False)
            if ext_val is True:
                external_nets.add(net_name)
            else:
                # external is False or absent — compose will create this network
                created_nets.add(net_name)

    logger.info("[IMP:7][test_all_external_networks_created] External networks: %s", sorted(external_nets))
    logger.info("[IMP:7][test_all_external_networks_created] Module-created networks: %s", sorted(created_nets))

    # Each external network must be EITHER created by a module (in created_nets)
    # OR be in PLATFORM_NETWORKS (pre-created by deploy-modules.sh)
    uncreated_external = external_nets - created_nets - PLATFORM_NETWORKS

    if uncreated_external:
        logger.error(
            "[IMP:9][test_all_external_networks_created] External networks without creator: %s",
            sorted(uncreated_external),
        )
        pytest.fail(
            f"External network(s) have no creator module and are not in PLATFORM_NETWORKS: "
            f"{sorted(uncreated_external)}. Either add a module that creates the network "
            f"(without external:true), or add to PLATFORM_NETWORKS in deploy-modules.sh"
        )
    else:
        logger.info("[IMP:9][test_all_external_networks_created] All external networks have a creator: OK")


@pytest.mark.static_audit
@ldd_trajectory
def test_all_created_networks_in_platform_list(all_compose_files, caplog) -> None:
    # · Scenario: module creates a network (non-external) that is not in PLATFORM_NETWORKS → deploy script won't pre-create it, other modules referencing it as external will fail
    # · Last fail: never — guard test
    # · Remove if: PLATFORM_NETWORKS constant removed from deploy-modules.sh
    """## @purpose — Every network declared WITHOUT external:true in compose must be in PLATFORM_NETWORKS.
    ##            A module creating a new network must register it in deploy-modules.sh.
    ## @io — ⇥ all_compose_files: dict[str, str] → ⎋ None (assert)
    ## @complexity — O(N * Y) where N = compose count, Y = YAML parse size
    """

    logger.info(
        "[IMP:7][test_all_created_networks_in_platform_list] Checking created networks are in PLATFORM_NETWORKS..."
    )

    # Collect all created (non-external) networks from compose files
    created_networks: set[str] = set()

    for mod_name, compose_path in all_compose_files.items():
        try:
            nets = _parse_compose_networks(compose_path)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning(
                "[IMP:4][test_all_created_networks_in_platform_list] Failed to parse %s: %s", compose_path, exc
            )
            continue

        for net_name, net_conf in nets.items():
            ext_val = net_conf.get("external", False)
            if ext_val is not True:
                created_networks.add(net_name)
                logger.info(
                    "[IMP:7][test_all_created_networks_in_platform_list] %s creates network '%s'", mod_name, net_name
                )

    unregistered = created_networks - PLATFORM_NETWORKS - EXEMPT_CREATED_NETWORKS

    if unregistered:
        logger.error(
            "[IMP:9][test_all_created_networks_in_platform_list] Created network(s) not in PLATFORM_NETWORKS: %s",
            sorted(unregistered),
        )
        pytest.fail(
            f"Module-created network(s) not registered in PLATFORM_NETWORKS (deploy-modules.sh): "
            f"{sorted(unregistered)}. Add them to the PLATFORM_NETWORKS array."
        )
    else:
        logger.info(
            "[IMP:9][test_all_created_networks_in_platform_list] All created networks registered in PLATFORM_NETWORKS: OK"
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_no_internal_external_conflict(all_compose_files, caplog) -> None:
    # · Scenario: module A declares network X as internal (creates it), module B declares same network X as external → docker compose up for B fails because X already exists as internal
    # · Last fail: never — guard test
    # · Remove if: network scoping convention changed (e.g. all networks become external)
    """## @purpose — No network may be declared as internal in one compose and external in another.
    ## @io — ⇥ all_compose_files: dict[str, str] → ⎋ None (assert)
    ## @complexity — O(N * Y) where N = compose count, Y = YAML parse size
    """

    logger.info("[IMP:7][test_no_internal_external_conflict] Checking no internal/external conflicts...")

    # Track per-network: set of modules where internal:true, set where external:true/false
    net_internal: dict[str, set[str]] = {}
    net_external: dict[str, set[str]] = {}
    net_all: dict[str, set[str]] = {}

    for mod_name, compose_path in all_compose_files.items():
        try:
            nets = _parse_compose_networks(compose_path)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:4][test_no_internal_external_conflict] Failed to parse %s: %s", compose_path, exc)
            continue

        for net_name, net_conf in nets.items():
            net_all.setdefault(net_name, set()).add(mod_name)
            if net_conf.get("internal") is True:
                net_internal.setdefault(net_name, set()).add(mod_name)
            ext_val = net_conf.get("external")
            if ext_val is True:
                net_external.setdefault(net_name, set()).add(mod_name)

    conflicts: list[str] = []
    for net_name in net_all:
        is_internal = net_name in net_internal
        is_external = net_name in net_external
        if is_internal and is_external:
            conflicts.append(net_name)
            logger.warning(
                "[IMP:4][test_no_internal_external_conflict] Network '%s' is internal in %s and external in %s",
                net_name,
                net_internal[net_name],
                net_external[net_name],
            )

    if conflicts:
        logger.error(
            "[IMP:9][test_no_internal_external_conflict] Found %d internal/external conflict(s): %s",
            len(conflicts),
            conflicts,
        )
        pytest.fail(f"Networks declared both internal and external: {conflicts}")
    else:
        logger.info("[IMP:9][test_no_internal_external_conflict] No internal/external conflicts: OK")


@pytest.mark.static_audit
@ldd_trajectory
def test_no_orphan_platform_networks(all_networks, caplog) -> None:
    # · Scenario: PLATFORM_NETWORKS contains a network that no module uses → docker may fail to pre-create an unused network, or the entry is dead config
    # · Last fail: never — guard test
    # · Remove if: PLATFORM_NETWORKS in deploy-modules.sh removed
    """## @purpose — Every network in PLATFORM_NETWORKS must be used by at least one module.
    ## @io — ⇥ all_networks: dict[str, set[str]] → ⎋ None (assert)
    ## @complexity — O(P) where P = |PLATFORM_NETWORKS|
    """

    logger.info("[IMP:7][test_no_orphan_platform_networks] Checking no orphan platform networks...")

    orphan_nets = PLATFORM_NETWORKS - set(all_networks.keys())

    if orphan_nets:
        logger.error(
            "[IMP:9][test_no_orphan_platform_networks] Orphan platform networks (no module uses them): %s",
            sorted(orphan_nets),
        )
        pytest.fail(
            f"PLATFORM_NETWORKS entries not used by any module: {sorted(orphan_nets)}. "
            f"Either connect a module to these networks or remove from PLATFORM_NETWORKS."
        )
    else:
        logger.info("[IMP:9][test_no_orphan_platform_networks] All platform networks are in use: OK")
        logger.info(
            "[IMP:7][test_no_orphan_platform_networks] Network usage: %s",
            {net: sorted(mods) for net, mods in all_networks.items() if net in PLATFORM_NETWORKS},
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_network_naming_convention(all_networks, caplog) -> None:
    # · Scenario: developer adds network "my_network" instead of "my-net" → inconsistent naming makes grep/automation harder
    # · Last fail: never — guard test
    # · Remove if: naming convention officially changed or removed
    """## @purpose — All network names must follow the pattern *-net (e.g., proxy-net, shared-db-net).
    ## @io — ⇥ all_networks: dict[str, set[str]] → ⎋ None (assert)
    ## @complexity — O(N) where N = unique network count
    """

    logger.info("[IMP:7][test_network_naming_convention] Checking network naming convention...")

    pattern = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-net$")
    violations: list[str] = []

    for net_name in sorted(all_networks.keys()):
        if not pattern.match(net_name):
            violations.append(net_name)
            logger.warning(
                "[IMP:4][test_network_naming_convention] Network '%s' does not follow *-net pattern", net_name
            )

    if violations:
        logger.error(
            "[IMP:9][test_network_naming_convention] %d network(s) violate naming convention: %s",
            len(violations),
            violations,
        )
        pytest.fail(f"Network names must follow pattern *-net: {violations}")
    else:
        logger.info(
            "[IMP:9][test_network_naming_convention] All %d network(s) follow *-net naming convention: OK",
            len(all_networks),
        )


# endregion
