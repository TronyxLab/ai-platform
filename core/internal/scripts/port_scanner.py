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
##            docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T56 — extracted from generate_platform_env.py
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path
from typing import cast

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
    # DR-L6 fix (аудит DevPlan 010): host-порт CH native peer отсутствовал в карте —
    # сканер печатал безымянный port_19000 вместо канонического имени
    19000: "clickhouse_native_peer",
    9090: "prometheus",
    9100: "node_exporter",
    9113: "nginx_exporter",
    9119: "hermes_dashboard",
    9121: "redis_exporter",
    9187: "postgres_exporter",
    9363: "clickhouse_metrics",
}

# 177 W2.5: канон-оверрайды имён портов (module, service, port_index → имя env_defaults SoT).
# Generic-схема (первый порт → MODULE_PORT, последующие → MODULE_SERVICE_PORT) порождает
# мусорный дубль MINIO_MINIO_PORT: 9001, хотя канон platform-infra.yaml — MINIO_CONSOLE_PORT.
# Оверрайд возвращает имя из SoT; при изменении compose-портов minio — править оба места.
# DevPlan 010 T2.2 completion: clickhouse native-peer (host 19000→container 9000) — второй порт;
# канон имени — compose-переменная CLICKHOUSE_NATIVE_PEER_PORT (TRAP §3 плана, platform_ports).
_PORT_CANON_NAMES: dict[str, dict[str, dict[int, str]]] = {
    "minio": {"minio": {1: "MINIO_CONSOLE_PORT"}},
    # DevPlan 010 T2.2 completion: native-peer host-порт (19000→container 9000) получает
    # каноническое имя compose-переменной CLICKHOUSE_NATIVE_PEER_PORT вместо мусорного
    # CLICKHOUSE_CLICKHOUSE_PORT (TRAP §3 плана; parity с shared/platform_ports).
    # ⚠️ node-metrics/service-exporters НЕ оверрайдятся — U-01 регрессионный тест
    # (test_scan_compose_ports_multi_service_regression) пиннит generic-схему
    # NODE_METRICS_NODE_EXPORTER_PORT / SERVICE_EXPORTERS_*.
    "clickhouse": {"clickhouse": {1: "CLICKHOUSE_NATIVE_PEER_PORT"}},
}


# region FUNC_extract_host_port
def extract_host_port(port_mapping: str) -> int | None:
    """Extract host port from a Docker Compose port mapping string.

    ## @purpose  Parse port mapping formats: "XXXX:YYYY", "127.0.0.1:XXXX:YYYY",
    ##            "127.0.0.1:${VAR:-XXXX}:YYYY", "${VAR:-XXXX}:YYYY",
    ##            "${SERVICE_BIND_HOST:-127.0.0.1}:${VAR:-XXXX}:YYYY" (DevPlan 010 T2.2).
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

    # Pattern 1b (DevPlan 010 T2.2): "${SERVICE_BIND_HOST:-127.0.0.1}:${VAR:-XXXX}:YYYY" —
    # параметризованный bind host-публикации (single-node default loopback, multi-node host ноды).
    # Host-сторона — IP-default env var; порт — env var default (группа 1).
    bind_var_pattern = re.compile(r"^\$\{[^:{}]+:-(?:\d+\.\d+\.\d+\.\d+)\}:\$\{[^:{}]+:-(\d+)\}:\d+$")
    m = bind_var_pattern.match(mapping)
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
            with Path(compose_path).open(encoding="utf-8") as f:
                # W11: yaml.safe_load returns Any → cast to payload boundary
                data = cast(dict[str, object] | None, yaml.safe_load(f))
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:8][scan_compose_ports][SKIP] Failed to parse %s: %s", compose_path, exc)
            continue

        if not isinstance(data, dict):
            continue

        # W11: raw get → object guard → cast to typed services boundary
        services_raw: object = data.get("services") or {}
        if not isinstance(services_raw, dict):
            continue
        services = cast(dict[str, object], services_raw)
        service_port_count = 0

        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                continue
            svc = cast(dict[str, object], service_def)

            ports_raw = svc.get("ports")
            if not isinstance(ports_raw, list):
                continue

            # W11: list[Unknown] after isinstance narrowing → cast to object list for item checks
            for port_entry in cast(list[object], ports_raw):
                if isinstance(port_entry, str):
                    host_port = extract_host_port(port_entry)
                elif isinstance(port_entry, dict):
                    # W11: isinstance-narrowed dict is dict[Unknown, Unknown] → cast typed boundary
                    port_entry_typed = cast(dict[str, object], port_entry)
                    published = cast(int | str | None, port_entry_typed.get("published"))
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
                # 177 W2.5: канон-оверрайды — generic-схема даёт MINIO_MINIO_PORT, но канон
                #   env_defaults — MINIO_CONSOLE_PORT (platform-infra.yaml) → дубль 9001 в
                #   platform-env.yaml. Оверрайд: (module, service, port_index) → каноническое имя.
                service_upper = service_name.upper().replace("-", "_")
                var_name = f"{module_upper}_PORT" if service_port_count == 0 else f"{module_upper}_{service_upper}_PORT"
                var_name = (
                    _PORT_CANON_NAMES.get(module_name, {}).get(service_name, {}).get(service_port_count, var_name)
                )
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

    def override_constructor(loader: OverrideLoader, node: yaml.Node) -> object:
        # W11: yaml construct_* are untyped in stubs → result consumed via isinstance checks below
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)  # pyright: ignore[reportArgumentType] — W11-G5: yaml.Node union vs MappingNode in stubs

    OverrideLoader.add_constructor("!override", override_constructor)

    test_port_map: dict[str, dict[str, int]] = {}
    test_files = sorted(modules_dir.glob("*/docker-compose.test.yml"))

    for test_path in test_files:
        module_name = test_path.parent.name

        try:
            with Path(test_path).open(encoding="utf-8") as f:
                # W11: yaml.load returns Any → cast to payload boundary
                data = cast(
                    dict[str, object] | None,
                    yaml.load(f, Loader=OverrideLoader),  # nosec B506 — OverrideLoader extends SafeLoader
                )
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:8][scan_test_ports][SKIP] Failed to parse %s: %s", test_path, exc)
            continue

        if not isinstance(data, dict):
            continue

        # W11: raw get → object guard → cast to typed services boundary
        services_raw: object = data.get("services") or {}
        if not isinstance(services_raw, dict):
            continue
        services = cast(dict[str, object], services_raw)
        module_ports: dict[str, int] = {}

        for service_name, service_def in services.items():
            if not isinstance(service_def, dict):
                continue
            svc = cast(dict[str, object], service_def)

            ports_raw = svc.get("ports")
            if not isinstance(ports_raw, list):
                continue

            # W11: list[Unknown] after isinstance narrowing → cast to object list for item checks
            for port_entry in cast(list[object], ports_raw):
                if isinstance(port_entry, str):
                    host_port = extract_host_port(port_entry)
                elif isinstance(port_entry, dict):
                    # W11: isinstance-narrowed dict is dict[Unknown, Unknown] → cast typed boundary
                    port_entry_typed = cast(dict[str, object], port_entry)
                    published = cast(int | str | None, port_entry_typed.get("published"))
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
