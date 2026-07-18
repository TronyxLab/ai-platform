# GREP_SUMMARY: nginx proxy_pass upstream vhost service-name container static-audit set-upstream docker-dns loopback-test
# STRUCTURE: ┌glob nginx/config/ + nginx/dev-config/*.conf┐ → ◇ proxy_pass re.findall(target:port) → ◇ set $upstream re.findall(target:port) → ◇ target∈compose_services? → Σ loopback_assert
# region MODULE_CONTRACT
# @file test_nginx_upstream_validity.py
# @purpose  Validate that all nginx proxy_pass targets and upstream server directives
#           reference real service names declared in docker-compose files.
#           Also verify that each vhost file has a corresponding service in a compose file.
#           FORBID loopback targets (127.0.0.1, localhost) in proxy_pass and set $upstream
#           directives — nginx runs as Docker container, loopback is empty.
# @scope    nginx/config/*.conf and nginx/dev-config/*.conf files; all docker-compose.base.yml service names
# @invariants
#   - proxy_pass http://<target>:<port> — target must be a known service name (never 127.0.0.1/localhost)
#   - set $upstream_<name> <target>:<port> — target must be a known service name
#   - upstream <name> { server <addr>; } — addr must reference a real container name
#   - Each vhost file (grafana-vhost.conf, hermes-dashboard.conf, etc.) must have a
#     corresponding service in one of the compose files
#   - No proxy_pass or set $upstream target is 127.0.0.1 or localhost in any config
# @rationale  If nginx proxy_pass targets a service name that doesn't match any container name,
#             proxying silently fails (502 Bad Gateway). This catches naming drift at audit time.
#             Loopback targets inside Docker nginx container cause 502 (no service listens there).
# @changes 2026-07-16 | TASK-3 DevPlan 001: extend to parse set $upstream directives;
#                       forbid loopback targets (test_no_loopback_proxy_targets)
# endregion MODULE_CONTRACT

import logging
import os
import re

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# region CONSTANTS

# Loopback targets are NEVER valid for proxy_pass / set $upstream in Docker nginx.
# The container's loopback is empty — no service listens there.
# These are kept for upstream block validation (stub_status allow rules etc.)
_ALWAYS_VALID_TARGETS = {"127.0.0.1", "localhost", "0.0.0.0"}

# Nginx config directories (production + dev)
_NGINX_CONFIG_DIR = "nginx/config"
_NGINX_DEV_CONFIG_DIR = "nginx/dev-config"

# endregion CONSTANTS


# region HELPERS


def _get_all_compose_services(all_compose_files: dict[str, str]) -> set[str]:
    """Aggregate all docker-compose service names across all modules.

    ## @purpose — Returns {service_name, ...} from all compose files
    ## @io — ⇥ all_compose_files → ⎋ set[str]
    ## @complexity — O(N * S)
    """
    services: set[str] = set()
    for compose_path in all_compose_files.values():
        try:
            with open(compose_path) as f:
                compose_data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("[IMP:4][get_services] Failed to parse %s: %s", compose_path, exc)
            continue

        svcs = compose_data.get("services", {}) if isinstance(compose_data, dict) else {}
        for svc_name in svcs:
            services.add(svc_name)
            services.add(f"{svc_name}:")  # handle trailing colon in nginx upstream format
        # Also collect container_name if set
        for svc_cfg in svcs.values():
            if isinstance(svc_cfg, dict):
                cname = svc_cfg.get("container_name")
                if cname:
                    services.add(cname)
                    services.add(f"{cname}:")

    return services


def _get_nginx_conf_files(platform_root: str, config_dir: str = _NGINX_CONFIG_DIR) -> list[str]:
    """List all nginx config files in a specific config directory.

    ## @purpose — Returns absolute paths to *.conf files
    ## @io — ⇥ platform_root, config_dir → ⎋ list[str]
    ## @complexity — O(N)
    """
    conf_dir = os.path.join(platform_root, "core", "modules", config_dir)
    if not os.path.isdir(conf_dir):
        logger.warning("[IMP:4][get_nginx_conf] nginx config dir not found: %s", conf_dir)
        return []
    return sorted(
        os.path.join(conf_dir, f)
        for f in os.listdir(conf_dir)
        if f.endswith(".conf") and os.path.isfile(os.path.join(conf_dir, f))
    )


def _get_all_nginx_conf_files(platform_root: str) -> list[str]:
    """Return nginx config files from both config/ and dev-config/ directories.

    ## @purpose — Aggregate all nginx *.conf from both production and dev sets
    ## @io — ⇥ platform_root → ⎋ list[str]
    ## @complexity — O(D * N)
    """
    return _get_nginx_conf_files(platform_root, _NGINX_CONFIG_DIR) + _get_nginx_conf_files(
        platform_root, _NGINX_DEV_CONFIG_DIR
    )


def _get_set_upstream_targets(content: str) -> list[tuple[str, str]]:
    """Extract targets from `set $upstream_<name> <target>:<port>;` directives.

    ## @purpose — Parse Docker-DNS variable proxy pattern
    ## @io — ⇥ content → ⎋ list[(target, port)]
    ## @complexity — O(L)
    """
    return re.findall(r"set\s+\$upstream_\w+\s+([^:]+):(\d+)\s*;", content)


def _get_vhost_files(platform_root: str) -> list[str]:
    """Return nginx vhost config files (exclude nginx.conf which is the main config).

    ## @purpose — Filter out nginx.conf, return only vhost-style files
    ## @io — ⇥ platform_root → ⎋ list[str]
    ## @complexity — O(N)
    """
    all_conf = _get_nginx_conf_files(platform_root)
    return [f for f in all_conf if not f.endswith("/nginx.conf")]


# endregion HELPERS


# region --- Tests ---


@pytest.mark.static_audit
@ldd_trajectory
def test_nginx_proxy_pass_targets_exist(platform_root, all_compose_files, caplog) -> None:
    # · Scenario: proxy_pass http://new-service:8080 added to nginx config but new-service is not a compose service name → nginx returns 502 for all requests to that vhost
    # · Last fail: never — guard test
    # · Remove if: nginx config management moved to service mesh/discovery
    """Parse proxy_pass and set $upstream targets from all nginx config files and verify targets exist.

    ## @purpose — Every proxy_pass target and set $upstream target must be a known compose
    ##            service name, or a valid IP address (127.0.0.1/localhost).
    ##            Parses both config/ and dev-config/ directories.
    ## @io — ⇥ platform_root, all_compose_files → ⎋ None (assert)
    ## @complexity — O(C * L) where C=confs, L=lines per conf
    ## @changes 2026-07-16 | Extended to parse set $upstream directives and scan dev-config/ (TASK-3 DevPlan 001)
    """

    logger.info(
        "[IMP:7][test_nginx_proxy_pass_targets_exist] Parsing proxy_pass + set $upstream directives in nginx configs"
    )

    known_services = _get_all_compose_services(all_compose_files)
    known_services.update(_ALWAYS_VALID_TARGETS)
    logger.info("[IMP:8][test_nginx_proxy_pass_targets_exist] Known services: %s", sorted(known_services))

    conf_files = _get_all_nginx_conf_files(platform_root)
    assert len(conf_files) > 0, "No nginx config files found in nginx/config/ or nginx/dev-config/"

    unknown_targets: list[dict] = []
    total_directives = 0

    for conf_path in conf_files:
        with open(conf_path) as f:
            content = f.read()

        # Find all proxy_pass http://<target>:<port>
        proxy_matches = re.findall(r"proxy_pass\s+http://([^:]+):(\d+)", content)
        # Find all set $upstream_<name> <target>:<port> (Docker-DNS variable pattern)
        set_matches = _get_set_upstream_targets(content)

        for target, port in proxy_matches:
            total_directives += 1
            if target not in known_services:
                unknown_targets.append(
                    {
                        "file": os.path.basename(conf_path),
                        "target": target,
                        "port": port,
                        "directive": "proxy_pass",
                    }
                )
                logger.warning(
                    "[IMP:9][test_nginx_proxy_pass_targets_exist] UNKNOWN proxy_pass target %s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )
            else:
                logger.info(
                    "[IMP:8][test_nginx_proxy_pass_targets_exist] OK proxy_pass %s -> %s:%s in %s",
                    target,
                    target,
                    port,
                    os.path.basename(conf_path),
                )

        for target, port in set_matches:
            total_directives += 1
            if target not in known_services:
                unknown_targets.append(
                    {
                        "file": os.path.basename(conf_path),
                        "target": target,
                        "port": port,
                        "directive": "set $upstream",
                    }
                )
                logger.warning(
                    "[IMP:9][test_nginx_proxy_pass_targets_exist] UNKNOWN set $upstream target %s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )
            else:
                logger.info(
                    "[IMP:8][test_nginx_proxy_pass_targets_exist] OK set $upstream %s -> %s:%s in %s",
                    target,
                    target,
                    port,
                    os.path.basename(conf_path),
                )

    logger.info(
        "[IMP:8][test_nginx_proxy_pass_targets_exist] Found %d proxy_pass/set directives total", total_directives
    )

    if not unknown_targets:
        logger.info("[IMP:9][test_nginx_proxy_pass_targets_exist] All proxy_pass and set $upstream targets are known")
    assert len(unknown_targets) == 0, f"Unknown proxy_pass/set targets: {unknown_targets}"


@pytest.mark.static_audit
@ldd_trajectory
def test_nginx_upstream_blocks_valid(platform_root, all_compose_files, caplog) -> None:
    # · Scenario: upstream grafana_backend { server grafana:3000; } but "grafana" is not a known compose service → upstream resolves to nothing, load balancing round-robins to unreachable hosts
    # · Last fail: never — guard test
    # · Remove if: nginx upstream convention changed or service mesh replaces nginx
    """Parse upstream blocks and verify server addresses reference existing containers.

    ## @purpose — Each `upstream <name> { server <addr>; }` must reference a
    ##            real container name from compose files.
    ## @io — ⇥ platform_root, all_compose_files → ⎋ None (assert)
    ## @complexity — O(C * L)
    """

    logger.info("[IMP:7][test_nginx_upstream_blocks_valid] Parsing upstream blocks in nginx configs")

    known_services = _get_all_compose_services(all_compose_files)
    known_services.update(_ALWAYS_VALID_TARGETS)
    # Add common upstream port formats: name:port
    # This handles `server grafana:3000;` inside upstream blocks
    with_port = set()
    for svc in known_services:
        with_port.add(f"{svc}:")  # for matching svc:port patterns

    conf_files = _get_nginx_conf_files(platform_root)
    invalid_upstreams: list[dict] = []

    for conf_path in conf_files:
        with open(conf_path) as f:
            content = f.read()

        # Match upstream blocks: upstream <name> { ... }
        upstream_blocks = re.findall(r"upstream\s+(\S+)\s*\{([^}]+)\}", content, re.DOTALL)
        for block_name, block_body in upstream_blocks:
            logger.info(
                "[IMP:8][test_nginx_upstream_blocks_valid] Found upstream block '%s' in %s",
                block_name,
                os.path.basename(conf_path),
            )

            # Find server directives inside the block
            server_addrs = re.findall(r"server\s+(\S+)", block_body)
            for addr in server_addrs:
                # Strip port suffix (host:port → host)
                addr_stripped = addr.split(":")[0] if ":" in addr else addr

                if addr_stripped not in known_services:
                    invalid_upstreams.append(
                        {
                            "file": os.path.basename(conf_path),
                            "upstream": block_name,
                            "addr": addr,
                        }
                    )
                    logger.warning(
                        "[IMP:9][test_nginx_upstream_blocks_valid] INVALID server %s in upstream '%s' in %s",
                        addr,
                        block_name,
                        os.path.basename(conf_path),
                    )
                else:
                    logger.info(
                        "[IMP:8][test_nginx_upstream_blocks_valid] OK server %s in upstream '%s'", addr, block_name
                    )

    if not invalid_upstreams:
        logger.info("[IMP:9][test_nginx_upstream_blocks_valid] All upstream server addresses are valid")
    assert len(invalid_upstreams) == 0, f"Invalid upstream server addresses: {invalid_upstreams}"


@pytest.mark.static_audit
@ldd_trajectory
def test_vhost_files_have_corresponding_service(platform_root, all_compose_files, all_module_yamls, caplog) -> None:
    # · Scenario: new-vhost.conf added to nginx/config/ but no compose service or module exists to handle its proxy_pass targets → nginx configuration is dead code or will 502
    # · Last fail: never — guard test
    # · Remove if: nginx vhost management convention changed or auto-discovered via service mesh
    """Verify each nginx vhost file has a corresponding service in a compose file.

    ## @purpose — Each vhost (grafana-vhost.conf → grafana service, hermes-dashboard.conf → hermes-agent)
    ##            must have a matching service name in at least one docker-compose file.
    ##            Vhost files that proxy to localhost:PORT for non-Docker services are exempt.
    ## @io — ⇥ platform_root, all_compose_files, all_module_yamls → ⎋ None (assert)
    ## @complexity — O(V * S) where V=vhosts, S=services
    """

    logger.info("[IMP:7][test_vhost_files_have_corresponding_service] Correlating vhost files to compose services")

    known_services = _get_all_compose_services(all_compose_files)

    # Additional module name mapping: some vhosts correspond to a module name
    # (e.g., hermes-dashboard.conf → hermes-agent module)
    module_names = set(all_module_yamls.keys())

    vhost_files = _get_vhost_files(platform_root)
    logger.info("[IMP:8][test_vhost_files_have_corresponding_service] Found %d vhost files", len(vhost_files))

    # Files that serve static/default content or are shared snippets (no proxy_pass to any backend service)
    _STATIC_VHOST_FILES = {
        "platform-default.conf", "platform-http.conf", "nginx.conf",
        "security-headers.conf", "ssl-params.conf",  # shared snippets (audit 013)
    }

    unmatched_vhosts = []

    for vhost_path in vhost_files:
        vhost_basename = os.path.basename(vhost_path)
        with open(vhost_path) as f:
            content = f.read()

        # Strategy 0: Static/default configs that don't proxy to any service
        if vhost_basename in _STATIC_VHOST_FILES:
            logger.info(
                "[IMP:8][test_vhost_files_have_corresponding_service] %s → static/default config — ACCEPTED",
                vhost_basename,
            )
            continue

        # Extract proxy_pass targets and set $upstream targets
        proxy_targets = re.findall(r"proxy_pass\s+http://([^:]+):(\d+)", content)
        set_targets = _get_set_upstream_targets(content)

        # Determine if this vhost has a corresponding service
        has_match = False

        # Strategy 1: Check if any proxy_pass target is a known service
        for target, _port in proxy_targets:
            if target in known_services or target in module_names:
                has_match = True
                logger.info(
                    "[IMP:8][test_vhost_files_have_corresponding_service] %s → service '%s' FOUND via proxy_pass",
                    vhost_basename,
                    target,
                )
                break

        # Strategy 1b: Check if any set $upstream target is a known service (Docker-DNS variable pattern)
        if not has_match:
            for target, _port in set_targets:
                if target in known_services or target in module_names:
                    has_match = True
                    logger.info(
                        "[IMP:8][test_vhost_files_have_corresponding_service] %s → service '%s' FOUND via set $upstream",
                        vhost_basename,
                        target,
                    )
                    break

        # Strategy 2: Extract service hint from filename (grafana-vhost.conf → grafana)
        if not has_match:
            file_stem = vhost_basename.replace("-vhost.conf", "").replace(".conf", "")
            if file_stem in known_services or file_stem in module_names:
                has_match = True
                logger.info(
                    "[IMP:8][test_vhost_files_have_corresponding_service] %s → service/module '%s' derived from filename",
                    vhost_basename,
                    file_stem,
                )

        if not has_match:
            unmatched_vhosts.append(vhost_basename)
            logger.warning(
                "[IMP:9][test_vhost_files_have_corresponding_service] %s: no corresponding service found!",
                vhost_basename,
            )
            logger.info(
                "[IMP:8][test_vhost_files_have_corresponding_service] %s proxy_pass targets: %s",
                vhost_basename,
                proxy_targets,
            )

    if not unmatched_vhosts:
        logger.info("[IMP:9][test_vhost_files_have_corresponding_service] All vhost files match a compose service")
    assert len(unmatched_vhosts) == 0, f"Vhost files without corresponding service: {unmatched_vhosts}"


@ldd_trajectory
def test_no_loopback_proxy_targets(platform_root, caplog) -> None:
    # 🧪 TRAP[TEST] · Regression: TASK-3 DevPlan 001 — loopback inside Docker nginx causes 502 · Scenario: glob config/ and dev-config/ *.conf, parse proxy_pass + set $upstream targets; assert none point to 127.0.0.1 or localhost · Last fail: all vhosts before 2026-07-16 fix · Remove if: nginx moved to host-network or service mesh
    """Forbid loopback targets (127.0.0.1, localhost) in proxy_pass and set $upstream directives.

    ## @purpose — Nginx runs as a Docker container. The container's loopback (127.0.0.1)
    ##            has no listening services. All proxy targets must use Docker-DNS service names
    ##            via the variable pattern (set $upstream_X svc:port + proxy_pass $upstream_X).
    ##            Exemptions: stub_status allow 127.0.0.1 (ACL, not proxy), healthcheck comments.
    ## @io — ⇥ platform_root → ⎋ None (assert)
    ## @complexity — O(C * L)
    ## @changes 2026-07-16 | New test per TASK-3 DevPlan 001
    """

    logger.info("[IMP:7][test_no_loopback_proxy_targets] Scanning for loopback proxy targets in nginx configs")

    conf_files = _get_all_nginx_conf_files(platform_root)
    assert len(conf_files) > 0, "No nginx config files found in nginx/config/ or nginx/dev-config/"

    loopback_violations: list[dict] = []
    total_directives = 0

    for conf_path in conf_files:
        with open(conf_path) as f:
            content = f.read()

        # Match only proxy_pass and set $upstream — NOT allow/deny rules (stub_status)
        proxy_targets = re.findall(r"proxy_pass\s+http://([^:]+):(\d+)", content)
        set_targets = _get_set_upstream_targets(content)

        for target, port in proxy_targets:
            total_directives += 1
            if target in ("127.0.0.1", "localhost"):
                loopback_violations.append(
                    {
                        "file": os.path.basename(conf_path),
                        "target": target,
                        "port": port,
                        "directive": "proxy_pass",
                    }
                )
                logger.warning(
                    "[IMP:9][test_no_loopback_proxy_targets] VIOLATION: proxy_pass http://%s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )
            else:
                logger.info(
                    "[IMP:8][test_no_loopback_proxy_targets] OK proxy_pass -> %s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )

        for target, port in set_targets:
            total_directives += 1
            if target in ("127.0.0.1", "localhost"):
                loopback_violations.append(
                    {
                        "file": os.path.basename(conf_path),
                        "target": target,
                        "port": port,
                        "directive": "set $upstream",
                    }
                )
                logger.warning(
                    "[IMP:9][test_no_loopback_proxy_targets] VIOLATION: set $upstream -> %s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )
            else:
                logger.info(
                    "[IMP:8][test_no_loopback_proxy_targets] OK set $upstream -> %s:%s in %s",
                    target,
                    port,
                    os.path.basename(conf_path),
                )

    logger.info("[IMP:8][test_no_loopback_proxy_targets] Scanned %d directives total", total_directives)

    if not loopback_violations:
        logger.info("[IMP:9][test_no_loopback_proxy_targets] ✅ No loopback proxy targets found — all use Docker-DNS")
    else:
        logger.warning(
            "[IMP:9][test_no_loopback_proxy_targets] ❌ %d loopback violations found: %s",
            len(loopback_violations),
            loopback_violations,
        )

    assert len(loopback_violations) == 0, (
        f"Loopback proxy targets found (Docker nginx cannot reach 127.0.0.1): {loopback_violations}"
    )


# endregion
