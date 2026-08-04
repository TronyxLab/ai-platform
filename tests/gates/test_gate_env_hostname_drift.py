# GREP_SUMMARY: gate env-example hostname drift container_name consistency cross-file
# STRUCTURE: ┌parse .env.example *_HOST┐ → ┌parse base.yml container_name┐ → ◇ compare → ⊕ violations → ⎋ assert

# region MODULE_CONTRACT
## @purpose  Gate test GAP-1: validate .env.example *_HOST values match actual container_name
##           in docker-compose.base.yml to prevent cross-file drift after container renames.
## @scope    Parses .env.example (root and hermes-agent/.env.example), extracts *_HOST variables,
##           compares with container_name from all core/modules/*/docker-compose.base.yml.
## @invariants
##   - Every *_HOST value in .env.example must match a container_name in at least one base.yml
##   - Комментарии-аннотации в .env.example исключаются из проверки (grep -v)
##   - Тест не требует Docker daemon — чисто статический анализ YAML + .env
## @rationale
##   Q: Why gate test instead of CI check?
##   A: Gate test интегрирован в make gate (pytest) и блокирует merge при несоответствии.
##      CI shell-check сложнее поддерживать и дублирует логику.
## @changes — 2026-07-14 | Created per DevPlan 043, GAP-1 from QAAuditReport.md
# endregion MODULE_CONTRACT

import logging
import os
import re

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULES_DIR = os.path.join(ROOT_DIR, "core", "modules")

from tests._conftest.audit import discover_docker_modules

# Known *_HOST variables that are NOT container hostnames (bind addresses, IPs, etc.)
# These are excluded from container_name consistency checks.
NON_CONTAINER_HOST_VARS = {
    "API_SERVER_HOST",  # Bind address (0.0.0.0), not a container name
}

# Known *_HOST → expected container_name mapping for strict validation.
# Key: env variable name, Value: expected container_name or None (any container_name allowed)
HOST_TO_CONTAINER = {
    "POSTGRES_HOST": "pgbouncer",  # pgbouncer is the connection pooler for postgres
    "REDIS_HOST": "redis",
}


def _parse_env_hosts(env_path):
    """Extract *_HOST=value pairs from .env.example, excluding annotation comments
    and known non-container host variables."""
    hosts = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            # Skip annotation lines and regular comments
            if line.startswith("#") or not line:
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key.endswith("_HOST") and key not in NON_CONTAINER_HOST_VARS:
                    hosts[key] = value
    return hosts


def _parse_container_names():
    """Extract container_name from all docker-compose.base.yml files via shared discovery (T7)."""
    container_names = set()
    for module_name in discover_docker_modules(MODULES_DIR):
        base_yml = os.path.join(MODULES_DIR, module_name, "docker-compose.base.yml")
        if not os.path.isfile(base_yml):
            continue
        with open(base_yml) as f:
            content = f.read()
        for match in re.finditer(r"^\s*container_name:\s*(\S+)", content, re.MULTILINE):
            container_names.add(match.group(1))
    return container_names


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_env_example_hostnames_match_containers(caplog):
    """All *_HOST values in .env.example match container_name in docker-compose.base.yml."""
    env_path = os.path.join(ROOT_DIR, ".env.example")
    assert os.path.isfile(env_path), f".env.example not found at {env_path}"

    hosts = _parse_env_hosts(env_path)
    container_names = _parse_container_names()

    logger.info("[IMP:8][gate] Parsed %d *_HOST variables from .env.example", len(hosts))
    logger.info(
        "[IMP:8][gate] Found %d container_name(s) in docker-compose.base.yml: %s",
        len(container_names),
        sorted(container_names),
    )

    violations = []
    for key, value in hosts.items():
        # Strict check: if we know the expected container for this variable, validate exact match
        if key in HOST_TO_CONTAINER:
            expected = HOST_TO_CONTAINER[key]
            if value != expected:
                violations.append(f"{key}={value} (expected '{expected}' per HOST_TO_CONTAINER mapping)")
                logger.error(
                    "[IMP:10][gate] DRIFT: %s=%s — expected '%s' per HOST_TO_CONTAINER",
                    key,
                    value,
                    expected,
                )
        elif value not in container_names:
            violations.append(f"{key}={value} (expected one of: {sorted(container_names)})")
            logger.error("[IMP:10][gate] DRIFT: %s=%s — not found in any container_name", key, value)

    assert not violations, (
        f"GATE_HOSTNAME_DRIFT: {len(violations)} host(s) in .env.example do NOT match "
        f"any container_name in docker-compose.base.yml:\n  " + "\n  ".join(violations)
    )
    logger.info("[IMP:9][gate] PASS: All *_HOST values match container_name(s)")


@pytest.mark.gate
@ldd_trajectory
def test_hermes_agent_env_matches_containers(caplog):
    """hermes-agent/.env.example *_HOST values match container_name."""
    env_path = os.path.join(MODULES_DIR, "hermes-agent", ".env.example")
    if not os.path.isfile(env_path):
        pytest.skip("hermes-agent/.env.example not found")

    hosts = _parse_env_hosts(env_path)
    container_names = _parse_container_names()

    violations = []
    for key, value in hosts.items():
        if key in HOST_TO_CONTAINER:
            expected = HOST_TO_CONTAINER[key]
            if value != expected:
                violations.append(f"{key}={value} (expected '{expected}' per HOST_TO_CONTAINER mapping)")
                logger.error(
                    "[IMP:10][gate] DRIFT: hermes-agent %s=%s — expected '%s'",
                    key,
                    value,
                    expected,
                )
        elif value not in container_names:
            violations.append(f"{key}={value} (expected one of: {sorted(container_names)})")
            logger.error("[IMP:10][gate] DRIFT: hermes-agent %s=%s — not in container_name(s)", key, value)

    assert not violations, (
        f"GATE_HERMES_AGENT_HOSTNAME_DRIFT: {len(violations)} host(s) in "
        f"hermes-agent/.env.example do NOT match container_name(s):\n  " + "\n  ".join(violations)
    )
    logger.info("[IMP:9][gate] PASS: hermes-agent *_HOST values match container_name(s)")
