#!/usr/bin/env python3
# GREP_SUMMARY: port-scanner, host-port, compose-ports, test-ports, port-mapping, docker-compose, override-loader
# STRUCTURE: ┌_PORT_NAME_MAP┐ → ◇ extract_host_port (5 regex patterns) → ◇ scan_compose_ports (base.yml) → ◇ scan_test_ports (test.yml, !override loader)
# region MODULE_CONTRACT
## @purpose  Docker Compose port scanner extracted from generate_platform_env.py (DevPlan 117 G T56).
##           Parses docker-compose.base.yml (production host ports) and docker-compose.test.yml
##           (test ports, custom !override YAML tag) into typed port maps consumed by
##           generate_platform_env_yaml() and platform-env.yaml generation.
## @scope    Consumed by core/internal/scripts/generate_platform_env.py (lazy facade) and the
##           deterministic-output gate (tests/gates/test_gate_yaml_deterministic_output.py).
## @invariants
##   - extract_host_port: pure regex parser — returns int | None, never raises
##   - scan_compose_ports: variable naming scheme MODULE_PORT / MODULE_SERVICE_PORT (DevPlan 116 T1)
##   - scan_test_ports: custom OverrideLoader handles the !override tag; B506-nosec (extends SafeLoader)
##   - _PORT_NAME_MAP lives HERE (single source of truth — generate_platform_env no longer defines it)
## @rationale  DevPlan 117 G T56 — extracted verbatim (L187-402, ~210 LOC) with all LDD logs,
##            TRAP[BUG] comment and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T56 — extracted from generate_platform_env.py
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Canonical port name map for well-known service ports
_PORT_NAME_MAP: dict[int, str] = {
    80: "http",
    443: "https",
    3000: "grafana",
    3001: "langfuse",
    3100: "loki",
    4000: "litellm",
    5432: "postgres",
    6379: "redis",
    6432: "pgbouncer",
    8080: "cadvisor",
    8123: "clickhouse_http",
    8642: "hermes_desktop",
    9000: "clickhouse_native",
    9090: "prometheus",
    9100: "node_exporter",
    9113: "nginx_exporter",
    9119: "hermes_dashboard",
    9121: "redis_exporter",
    9187: "postgres_exporter",
    9363: "clickhouse_metrics",
}


# region FUNC_extract_host_port
def extract_host_port(port_mapping: str) -> int | None:
    """Extract host port from a Docker Compose port mapping string.

    ## @purpose  Parse port mapping formats: "XXXX:YYYY", "127.0.0.1:XXXX:YYYY",
    ##            "127.0.0.1:${VAR:-XXXX}:YYYY", "${VAR:-XXXX}:YYYY".
    ##            Returns the resolved host port number.
    ## @io        ⇥ port_mapping: str → ⎋ int | None: host port or None
    ## @complexity O(1) — multi-pattern regex
    """
    mapping = port_mapping.strip()

    # Pattern 1: "127.0.0.1:${VAR:-XXXX}:YYYY" with env var default
    ip_var_pattern = re.compile(r"^\d+\.\d+\.\d+\.\d+:\$\{[^:}]+:-(\d+)\}:\d+$")
    m = ip_var_pattern.match(mapping)
    if m:
        return int(m.group(1))

    # Pattern 2: "${VAR:-XXXX}:YYYY" with env var default (bare)
    var_pattern = re.compile(r"^\$\{[^:}]+:-(\d+)\}:\d+$")
    m = var_pattern.match(mapping)
    if m:
        return int(m.group(1))

    # Pattern 3: "127.0.0.1:XXXX:YYYY" or "0.0.0.0:XXXX:YYYY"
    ip_pattern = re.compile(r"^\d+\.\d+\.\d+\.\d+:(\d+):\d+$")
    m = ip_pattern.match(mapping)
    if m:
        return int(m.group(1))

    # Pattern 4: "XXXX:YYYY" (bare mapping)
    bare_pattern = re.compile(r"^(\d+):\d+$")
    m = bare_pattern.match(mapping)
    if m:
        return int(m.group(1))

    # Pattern 5: "${VAR}:YYYY" (no default — skip)
    var_only_pattern = re.compile(r"^\$\{[^}]+\}:\d+$")
    if var_only_pattern.match(mapping):
        logger.debug("[IMP:8][extract_host_port][SKIP] Variable-only mapping (no default): %s", mapping)
        return None

    logger.warning("[IMP:8][extract_host_port][UNKNOWN] Cannot parse port mapping: %s", mapping)
    return None


# endregion FUNC_extract_host_port


# region FUNC_scan_compose_ports
def scan_compose_ports(modules_dir: Path) -> dict[str, int]:
    """Scan all docker-compose.base.yml files and extract host port mappings.

    ## @purpose  For each module with a docker-compose.base.yml, parse services
    ##            and extract host port numbers. Maps to upper-cased variable names
    ##            based on the service and port context.
    ## @io        ⇥ modules_dir: Path → ⎋ dict[str, int]: {VAR_NAME: port}
    ## @complexity O(M * S * P) where M = modules, S = services, P = ports
    """
    logger.info("[IMP:7][scan_compose_ports][START] Scanning docker-compose.base.yml for port mappings")

    port_map: dict[str, int] = {}
    compose_files = sorted(modules_dir.glob("*/docker-compose.base.yml"))

    for compose_path in compose_files:
        module_name = compose_path.parent.name
        module_upper = module_name.upper().replace("-", "_")

        try:
            with open(compose_path) as f:
                data: dict[str, Any] = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:8][scan_compose_ports][SKIP] Failed to parse %s: %s", compose_path, exc)
            continue

        if not isinstance(data, dict):
            continue

        services: dict[str, Any] = data.get("services") or {}
        service_port_count = 0

        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                continue

            ports_raw = service_def.get("ports")
            if not isinstance(ports_raw, list):
                continue

            for port_entry in ports_raw:
                if isinstance(port_entry, str):
                    host_port = extract_host_port(port_entry)
                elif isinstance(port_entry, dict):
                    published = port_entry.get("published")
                    host_port = int(published) if published is not None else None
                else:
                    continue

                if host_port is None:
                    continue

                # Generate variable name
                # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Second port overwrites first when service==module
                # · Symptom: port_mappings.MINIO_PORT: 9001 while env_defaults.MINIO_PORT: 9000 —
                #   minio's second port (9001 console) overwrote the first (9000 S3 API).
                # · Root: service_port_count incremented only in the `elif service_port_count == 0`
                #   branch. When service_upper == module_upper (minio service inside minio module)
                #   the counter never grew AND the first branch matched again for the second port →
                #   port_map[MINIO_PORT] overwritten. Same latent bug: nginx (80/443 → NGINX_PORT=443),
                #   hermes-agent (9119/8642 → HERMES_AGENT_PORT=8642).
                # · Fix: increment the counter for the FIRST port of every service (including
                #   service==module). First port → MODULE_PORT; subsequent → MODULE_SERVICE_PORT
                #   (e.g. MINIO_MINIO_PORT). Scheme per DevPlan 116 T1 (U-01).
                # · Prevention: T1 unit tests (minio-style fixture, infra-metrics-style regression).
                service_upper = service_name.upper().replace("-", "_")
                var_name = f"{module_upper}_PORT" if service_port_count == 0 else f"{module_upper}_{service_upper}_PORT"
                service_port_count += 1

                port_map[var_name] = host_port
                logger.info(
                    "[IMP:9][scan_compose_ports][PORT] %s → %s = %d",
                    module_name,
                    var_name,
                    host_port,
                )

    logger.info("[IMP:9][scan_compose_ports][OK] Extracted %d port mappings", len(port_map))
    return port_map


# endregion FUNC_scan_compose_ports


# region FUNC_scan_test_ports
def scan_test_ports(modules_dir: Path) -> dict[str, dict[str, int]]:
    """Scan all docker-compose.test.yml files and extract test port mappings.

    ## @purpose  For each module with docker-compose.test.yml, parse services
    ##            and extract host port numbers. Uses a custom YAML loader
    ##            that handles the !override tag used in test compose files.
    ## @io        ⇥ modules_dir: Path → ⎋ dict[str, dict[str, int]]: test port map
    ## @complexity O(M * S * P) where M = modules, S = services, P = ports
    """
    logger.info("[IMP:7][scan_test_ports][START] Scanning docker-compose.test.yml for port mappings")

    # Custom YAML loader that handles !override tag
    class OverrideLoader(yaml.SafeLoader):
        pass

    def override_constructor(loader, node):
        return (
            loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)
        )

    OverrideLoader.add_constructor("!override", override_constructor)

    test_port_map: dict[str, dict[str, int]] = {}
    test_files = sorted(modules_dir.glob("*/docker-compose.test.yml"))

    for test_path in test_files:
        module_name = test_path.parent.name

        try:
            with open(test_path) as f:
                data: dict[str, Any] = yaml.load(f, Loader=OverrideLoader)  # nosec B506 — OverrideLoader extends SafeLoader
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:8][scan_test_ports][SKIP] Failed to parse %s: %s", test_path, exc)
            continue

        if not isinstance(data, dict):
            continue

        services: dict[str, Any] = data.get("services") or {}
        module_ports: dict[str, int] = {}

        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                continue

            ports_raw = service_def.get("ports")
            if not isinstance(ports_raw, list):
                continue

            for port_entry in ports_raw:
                if isinstance(port_entry, str):
                    host_port = extract_host_port(port_entry)
                elif isinstance(port_entry, dict):
                    published = port_entry.get("published")
                    host_port = int(published) if published is not None else None
                else:
                    continue

                if host_port is None:
                    continue

                # Derive port name from port number
                port_name = _PORT_NAME_MAP.get(host_port, f"port_{host_port}")
                module_ports[port_name] = host_port
                logger.info(
                    "[IMP:9][scan_test_ports][PORT] %s/%s → %s = %d",
                    module_name,
                    service_name,
                    port_name,
                    host_port,
                )

        if module_ports:
            test_port_map[module_name] = module_ports

    logger.info(
        "[IMP:9][scan_test_ports][OK] Extracted test ports for %d modules",
        len(test_port_map),
    )
    return test_port_map


# endregion FUNC_scan_test_ports
