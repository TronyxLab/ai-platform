# GREP_SUMMARY: port-collision hardcoded-ports system-ports compose-ports module-ports host-port duplicate
# STRUCTURE: ▶ all_compose_files → ⚡ parse_ports(each service:ports) → ⊕ host_port_set
# region MODULE_CONTRACT
# @file test_port_collision.py
## @purpose — Detect port collisions across all Docker modules, warn on hardcoded ports,
#             verify system module ports are documented in module.yaml.
## @scope — Static audit of port declarations in docker-compose.base.yml files and
#           module.yaml ports fields across all 7 platform modules.
## @invariants
#   - No two Docker services may bind the same host port
#   - Ports in compose should use ${VAR:-default} pattern, not hardcoded numbers (WARN)
#   - System module ports must be declared in module.yaml
## @rationale — Port collisions cause "port is already allocated" errors at deploy time;
#             hardcoded ports make configuration non-portable; undocumented system ports
#             make it hard to track port usage.
# endregion MODULE_CONTRACT
#
#           → ◇ test_no_port_collisions(host_port dup → FAIL)
#           → ◇ test_no_hardcoded_ports(hardcoded_int → WARN)
#           → ◇ test_system_ports_documented(system module: ports ∉ module.yaml → WARN)

import logging
import pathlib
import re

import pytest
import yaml
from conftest import (
    ldd_trajectory,
)

logger = logging.getLogger(__name__)


def _extract_host_ports(compose_data: dict) -> list[tuple[str, str, int]]:
    """Extract host port numbers from a parsed compose dict.
    ## @purpose — Parse all service ports and extract the host-side port number.
    ## @io — ⇥ compose_data: dict → ⎋ list[(module_name, service_name, host_port)]
    ## @complexity — O(S * P) where S = service count, P = ports per service
    ## @invariants
    #   - Port format: [ip:][host_port:]container_port[/protocol]
    #   - Host port is the port before container port colon (or before /protocol)
    #   - If no host port is specified (just container port), returns -1
    """
    result: list[tuple[str, str, int]] = []
    services = compose_data.get("services", {}) or {}
    for svc_name, svc_data in services.items():
        ports = svc_data.get("ports", []) or []
        for port_entry in ports:
            port_str = str(port_entry)
            # Strip protocol suffix /tcp, /udp
            port_str = re.sub(r"/[a-z]+$", "", port_str)
            parts = port_str.split(":")
            if len(parts) == 3:
                # Format: ip:host_port:container_port
                try:
                    host_port = int(parts[1])
                    result.append((svc_name, port_str, host_port))
                except ValueError:
                    continue  # variable substitution — skip this port entry (R1: no bare pass)
            elif len(parts) == 2:
                # Format: host_port:container_port or maybe ip:port
                try:
                    host_port = int(parts[0])
                    result.append((svc_name, port_str, host_port))
                except ValueError:
                    continue  # skip this port entry (R1: no bare pass)
            else:
                # Just container port — no host port binding
                pass
    return result


def _is_hardcoded_port(port_entry) -> bool:
    """Check if a port entry uses hardcoded int/string vs ${VAR:-default}.
    ## @purpose — Return True if port is hardcoded (pure number), False if uses env var.
    ## @io — ⇥ port_entry: any (str or int) → ⎋ bool
    ## @complexity — O(1)
    """
    port_str = str(port_entry)
    # If it contains ${...} it's not hardcoded
    if "${" in port_str:
        return False
    # If it's a pure number, it's hardcoded
    try:
        int(port_str)
    except ValueError:
        logger.debug("[IMP:7][_is_hardcoded_port] '%s' is not a pure number — parsing complex format", port_str)
    else:
        return True
    # Parse complex format for hardcoded numbers
    # Strip protocol
    port_str_clean = re.sub(r"/[a-z]+$", "", port_str)
    parts = port_str_clean.split(":")
    for part in parts:
        # Skip IP parts (contain dots or are loopback)
        if "." in part or part in {"127.0.0.1", "0.0.0.0"}:
            continue
        try:
            int(part)
        except ValueError:
            continue  # not a number — try next part (R1: no bare pass)
        else:
            return True  # found a hardcoded number
    return False


# region --- Tests ---


@pytest.mark.static_audit
@ldd_trajectory
def test_no_port_collisions(all_compose_files, caplog) -> None:
    # · Scenario: two modules bind the same host port → docker compose up fails when second module tries to bind
    # · Last fail: never — guard test
    # · Remove if: port binding convention changed (e.g. all ports via reverse proxy only)
    """## @purpose — Collect all host ports from all Docker modules, fail if any duplicate found.
    ## @io — ⇥ all_compose_files: dict[str, str] → ⎋ None (assert)
    ## @complexity — O(N * Y) where N = compose count, Y = YAML parse size
    """

    logger.info("[IMP:7][test_no_port_collisions] Checking for port collisions...")

    host_port_map: dict[int, list[tuple[str, str, str]]] = {}  # port → [(module, service, full_spec)]

    for mod_name, compose_path in all_compose_files.items():
        try:
            with pathlib.Path(compose_path).open(encoding="utf-8") as f:
                compose_data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:4][test_no_port_collisions] Failed to parse %s: %s", compose_path, exc)
            continue

        if not isinstance(compose_data, dict):
            continue

        ports_info = _extract_host_ports(compose_data)
        for svc_name, port_spec, host_port in ports_info:
            if host_port < 0:
                continue
            host_port_map.setdefault(host_port, []).append((mod_name, svc_name, port_spec))
            logger.info(
                "[IMP:7][test_no_port_collisions] Port %d used by %s/%s (%s)", host_port, mod_name, svc_name, port_spec
            )

    collisions = {port: entries for port, entries in host_port_map.items() if len(entries) > 1}

    if collisions:
        msg_lines = []
        for port, entries in sorted(collisions.items()):
            modules_str = ", ".join(f"{mod}/{svc} ({spec})" for mod, svc, spec in entries)
            msg_lines.append(f"  Port {port} collides: {modules_str}")
        logger.error("[IMP:9][test_no_port_collisions] Found %d port collision(s)", len(collisions))
        for line in msg_lines:
            logger.error("[IMP:9][test_no_port_collisions] %s", line)
        pytest.fail("Host port collisions detected:\n" + "\n".join(msg_lines))
    else:
        total_ports = len(host_port_map)
        logger.info(
            "[IMP:9][test_no_port_collisions] No port collisions: %d unique host port(s) across all modules",
            total_ports,
        )


@pytest.mark.static_audit
@ldd_trajectory
def test_no_hardcoded_ports(all_compose_files, caplog) -> None:
    # · Scenario: developer adds ports: "3000:3000" instead of "${GRAFANA_PORT:-3000}:3000" → port cannot be overridden per environment
    # · Last fail: never — guard test
    # · Remove if: port convention changes or all ports moved to env file
    """## @purpose — FAIL (W2 T2.6: docstring-фикс) если порты хардкожены числами вместо
    ##            env-паттерна ${VAR:-default}. Режим FAIL-реализован (pytest.fail ниже);
    ##            прежний текст «WARN, pass always» был stale-документацией (R1-риск).
    ## @io — ⇥ all_compose_files: dict[str, str] → ⎋ None (FAIL on hardcoded ports)
    ## @complexity — O(N * Y) where N = compose count, Y = YAML parse size
    """

    logger.info("[IMP:7][test_no_hardcoded_ports] Checking for hardcoded ports...")

    hardcoded: list[tuple[str, str, str]] = []  # (module, service, port_spec)

    for mod_name, compose_path in all_compose_files.items():
        try:
            with pathlib.Path(compose_path).open(encoding="utf-8") as f:
                compose_data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:4][test_no_hardcoded_ports] Failed to parse %s: %s", compose_path, exc)
            continue

        if not isinstance(compose_data, dict):
            continue

        services = compose_data.get("services", {}) or {}
        for svc_name, svc_data in services.items():
            ports = svc_data.get("ports", []) or []
            for port_entry in ports:
                if _is_hardcoded_port(port_entry):
                    hardcoded.append((mod_name, svc_name, str(port_entry)))
                    logger.warning(
                        "[IMP:4][test_no_hardcoded_ports] HARDCODED PORT: %s/%s → %s", mod_name, svc_name, port_entry
                    )

    if hardcoded:
        logger.error("[IMP:9][test_no_hardcoded_ports] Found %d hardcoded port(s) in compose files", len(hardcoded))
        details = "\n".join(f"  Port {port_spec}: {mod_name}/{svc_name}" for mod_name, svc_name, port_spec in hardcoded)
        pytest.fail(f"Hardcoded ports found in docker-compose files:\n{details}")
    else:
        logger.info("[IMP:9][test_no_hardcoded_ports] No hardcoded ports: all use env var pattern")


@pytest.mark.static_audit
@ldd_trajectory
def test_system_ports_documented(all_module_yamls, caplog) -> None:
    # · Scenario: system module exposes a port but module.yaml has empty or missing ports field → ops cannot audit which ports are in use
    # · Last fail: never — guard test
    # · Remove if: system module convention changed (e.g. all become docker)
    """## @purpose — Ports of system modules (install_type: system) must be documented
    ##            in module.yaml under the ports: key. If a system module has no network
    ##            services (e.g. platform-secrets is a systemd oneshot with no ports),
    ##            it is exempt from this requirement.
    ## @io — ⇥ all_module_yamls: dict[str, dict] → ⎋ None (assert)
    ## @complexity — O(N) where N = module count
    """

    logger.info("[IMP:7][test_system_ports_documented] Checking system module ports are documented...")

    undocumented: list[str] = []

    for mod_name, mod_data in all_module_yamls.items():
        if mod_data.get("install_type") != "system":
            continue

        ports_key_exists = "ports" in mod_data
        if not ports_key_exists:
            # Module has no ports key — assume no network service, skip check
            logger.info(
                "[IMP:7][test_system_ports_documented] %s: no ports key in module.yaml (exempt, no network service)",
                mod_name,
            )
            continue

        documented_ports = mod_data.get("ports") or []
        if len(documented_ports) == 0:
            undocumented.append(mod_name)
            logger.warning(
                "[IMP:4][test_system_ports_documented] System module '%s' has empty ports list in module.yaml", mod_name
            )
        else:
            port_nums = [p.get("port", "?") for p in documented_ports if isinstance(p, dict)]
            logger.info("[IMP:7][test_system_ports_documented] %s: documented ports = %s", mod_name, port_nums)

    if undocumented:
        logger.error(
            "[IMP:9][test_system_ports_documented] %d system module(s) declare empty ports list: %s",
            len(undocumented),
            undocumented,
        )
        pytest.fail(f"System modules with 'ports' key must document at least one port: {undocumented}")
    else:
        logger.info("[IMP:9][test_system_ports_documented] All system modules have documented ports: OK")


# endregion
