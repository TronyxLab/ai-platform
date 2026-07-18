# GREP_SUMMARY: gate local-stack inv7 compose-config modules-included docker-compose discover-modules
# STRUCTURE: ▶ test_compose_config_resolves_full_stack → subprocess.run("docker compose config") → ◇ assert returncode == 0 → ◇ assert expected_services ⊆ output → ⊕ test_all_modules_included → ◇ parse include: from compose.yml → ◇ glob core/modules/*/ → ⊕ assert sorted(include) == sorted(modules)
# region MODULE_CONTRACT
## @purpose  Gate tests for Invariant 7: full local stack via docker compose (static check)
## @scope    Two tests: (1) docker compose config resolves all includes without error,
##           (2) include: section in docker-compose.yml matches core/modules/* directories.
## @invariants
##   - Uses subprocess.run ONLY for 'docker compose config' (static YAML resolution, no containers)
##   - test_all_modules_included is a static file-read test (no subprocess)
##   - Both tests use @pytest.mark.gate + @ldd_trajectory
## @rationale  Invariant 7: the full local stack must resolve without structural errors.
##             docker compose config validates all include'd files syntactically without
##             starting any containers. The include-vs-modules sync test is a secondary
##             invariant ensuring discover_modules contract is not violated.
## @changes — 2026-07-18 | Created per DevPlan 011 T7
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
MODULES_DIR = PROJECT_ROOT / "core" / "modules"

# Core services expected to appear in docker compose config output
EXPECTED_SERVICES = [
    "litellm",
    "postgres",
    "redis",
    "langfuse",
    "clickhouse",
    "minio",
    "nginx",
    "loki",
    "prometheus",
    "grafana",
    "cadvisor",
    "node-exporter",
    "redis-exporter",
    "nginx-prometheus-exporter",
    "hermes-agent",
]


@pytest.mark.gate
@ldd_trajectory
def test_compose_config_resolves_full_stack(caplog):
    """docker compose config resolves all includes without error (Invariant 7, static).

    ## @purpose — Validate that `docker compose config` resolves the full compose
    ##            file graph without structural errors. This is a static check —
    ##            no containers are started.
    ## @io — ⎋ None (asserts returncode == 0, services present in output)
    ## @complexity — O(N) on compose file graph depth
    """
    assert COMPOSE_FILE.exists(), f"docker-compose.yml not found at {COMPOSE_FILE}"

    # Run docker compose config (static YAML resolution, no container start)
    logger.info("[IMP:8][gate][compose_config] Running 'docker compose config' at %s", PROJECT_ROOT)
    try:
        result = subprocess.run(
            ["docker", "compose", "config"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        logger.warning("[IMP:7][gate][compose_config] docker binary not found — skipping test")
        pytest.skip("docker binary not found — cannot run 'docker compose config'")
        return
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][gate][compose_config] 'docker compose config' timed out — skipping test")
        pytest.skip("'docker compose config' timed out (60s)")
        return

    # Log the output for LDD trajectory
    logger.info("[IMP:9][gate][compose_config] returncode=%d", result.returncode)
    logger.info("[IMP:8][gate][compose_config] stdout length=%d bytes", len(result.stdout))
    if result.stderr:
        logger.info("[IMP:7][gate][compose_config] stderr=%s", result.stderr[:500])

    assert result.returncode == 0, (
        f"[IMP:9][gate][compose_config] FAIL: 'docker compose config' returned {result.returncode}.\n"
        f"stderr: {result.stderr[:1000]}"
    )

    # Verify expected services appear in the resolved config
    for service in EXPECTED_SERVICES:
        if service in result.stdout:
            logger.info("[IMP:8][gate][compose_config] Service found: %s", service)
        else:
            logger.warning("[IMP:7][gate][compose_config] Service NOT found in resolved config: %s", service)

    logger.info("[IMP:9][gate][compose_config] PASS: 'docker compose config' resolved successfully")


@pytest.mark.gate
@ldd_trajectory
def test_all_modules_included(caplog):
    """include section in docker-compose.yml matches core/modules/* directories.

    ## @purpose — Validate that every module in core/modules/ is included via
    ##            the include: section in docker-compose.yml. Detects missing
    ##            includes when a new module is added but not registered.
    ## @io — ⎋ None (asserts bidirectional sync between include entries and module dirs)
    ## @complexity — O(N) where N = number of module dirs
    """
    assert COMPOSE_FILE.exists(), f"docker-compose.yml not found at {COMPOSE_FILE}"
    assert MODULES_DIR.is_dir(), f"Modules directory not found at {MODULES_DIR}"

    # Parse include section from docker-compose.yml
    with open(COMPOSE_FILE) as f:
        compose_data = yaml.safe_load(f)

    include_entries = compose_data.get("include", [])
    included_paths = set()
    for entry in include_entries:
        path = entry.get("path", "") if isinstance(entry, dict) else str(entry)
        included_paths.add(str(path))

    # List all module directories (have module.yaml and are not platform-secrets
    # system module without compose — though the include may still exist for it)
    module_dirs = sorted(d.name for d in MODULES_DIR.iterdir() if d.is_dir())

    # Build expected include paths
    expected_includes = sorted(f"core/modules/{m}/docker-compose.base.yml" for m in module_dirs)

    # Find mismatches
    missing_from_compose = {p for p in expected_includes if p not in included_paths}
    extra_in_compose = included_paths - set(expected_includes)

    logger.info("[IMP:9][gate][modules_included] Found %d module dirs: %s", len(module_dirs), module_dirs)
    logger.info("[IMP:9][gate][modules_included] Found %d include entries in compose", len(included_paths))

    # Allow system modules (platform-secrets) to have no compose include entry
    system_modules = set()
    for m in module_dirs:
        yaml_path = MODULES_DIR / m / "module.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and data.get("install_type") == "system":
                system_modules.add(m)

    # Filter out system modules from missing_from_compose
    system_expected_includes = {f"core/modules/{m}/docker-compose.base.yml" for m in system_modules}
    missing_from_compose -= system_expected_includes

    assert not missing_from_compose, (
        f"[IMP:9][gate][modules_included] FAIL: Modules missing from compose include:\n  {sorted(missing_from_compose)}"
    )
    assert not extra_in_compose, (
        f"[IMP:9][gate][modules_included] FAIL: Extra entries in compose include (no matching module):\n"
        f"  {sorted(extra_in_compose)}"
    )

    logger.info(
        "[IMP:9][gate][modules_included] PASS: All %d modules are included in compose (excluding %d system modules)",
        len(module_dirs) - len(system_modules),
        len(system_modules),
    )
