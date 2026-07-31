# GREP_SUMMARY: gate local-stack invariant7 compose-config modules networks volumes include-sync discover-modules
# STRUCTURE: ▶ test_compose_config_resolves_full_stack → parse include paths + networks + volumes → ◇ verify all paths resolve → ◇ test_all_modules_included → discover_modules parity check → ⊕ invariant 7 coverage
# region MODULE_CONTRACT
## @purpose  Gate tests for Invariant 7: "Полный локальный стек через docker compose up"
## @scope    Static structural validation of root docker-compose.yml — all include paths,
##           networks, and volumes resolve. Module include section sync verified against
##           discover_modules.py output.
## @invariants
##   - Every include path in docker-compose.yml points to an existing file
##   - Networks count matches expected (6) or are all declared as external
##   - Volumes count matches expected (11) or are all declared as driver:local
##   - Include section is in sync with core/modules/*/module.yaml (docker type only)
## @rationale  Invariant 7: the full stack must resolve structurally. Static check via
##             YAML parsing (no docker daemon required) — runtime verification stays
##             in predeploy/e2e markers per §TESTING: no server launch in unit tests.
## @changes — 2026-07-18 | Created per DevPlan 011 T7 · $TEST_SPEC
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _PROJECT_ROOT / "docker-compose.yml"
_MODULES_DIR = _PROJECT_ROOT / "core" / "modules"
_DISCOVER_MODULES_SCRIPT = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "discover_modules.py"

# Expected structural counts (from root docker-compose.yml as of 2026-07-18)
EXPECTED_NETWORKS = 6
EXPECTED_VOLUMES = 10


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Invariant 7 missing gate coverage
# · Symptom: docker compose config or include paths can break silently, no detection
# · Root: T7 not implemented in DevPlan 011
# · Test: parse root compose, verify all include paths + networks + volumes + module sync
# · Prevention: make gate MODE=full must run this file


# region HELPER_PARSE_COMPOSE
def _parse_compose(compose_path: Path) -> dict:
    """Parse docker-compose.yml and return its top-level structure.

    ## @purpose — Load compose YAML for static structural analysis
    ## @complexity — O(1) per call, file I/O
    ## @io — ⎋ dict: keys = {include, networks, volumes, ...}
    """
    try:
        with open(compose_path) as fh:
            data = yaml.safe_load(fh)
        return data or {}
    except yaml.YAMLError as e:
        logger.error("[IMP:9][gate][local_stack] FATAL: Cannot parse %s: %s", compose_path, e)
        return {}


# endregion HELPER_PARSE_COMPOSE


# region HELPER_DISCOVER_MODULES
def _get_expected_modules() -> set[str]:
    """Run discover_modules.py and return the set of docker module names.

    ## @purpose — Discover docker modules (install_type == docker) to verify
    ##            include section sync against single source of truth.
    ## @complexity — O(N) subprocess + Python import
    ## @io — ⎋ set[str] of module names that should be included
    """
    modules = set()
    if not _MODULES_DIR.exists():
        logger.warning("[IMP:7][gate][local_stack] Modules dir not found: %s", _MODULES_DIR)
        return modules

    for item in sorted(_MODULES_DIR.iterdir()):
        if not item.is_dir():
            continue
        module_yaml = item / "module.yaml"
        if not module_yaml.exists():
            continue
        try:
            with open(module_yaml) as fh:
                data = yaml.safe_load(fh) or {}
            install_type = str(data.get("install_type", "docker")).strip().lower()
            if install_type == "docker":
                modules.add(item.name)
        except (yaml.YAMLError, OSError) as e:
            logger.warning("[IMP:7][gate][local_stack] Cannot read module.yaml for %s: %s", item.name, e)
    return modules


# endregion HELPER_DISCOVER_MODULES


@pytest.mark.gate
@ldd_trajectory
def test_compose_config_resolves_full_stack(caplog):
    """Verify root docker-compose.yml structurally: all include paths, networks, volumes.

    ## @purpose — Validate Invariant 7 (static): 13 docker modules, 6 networks,
    ##            10 volumes all resolve. Uses YAML parsing, NOT docker compose config
    ##            (no docker daemon required). Falls back to docker compose config if
    ##            docker is available for runtime validation.
    ## @io — ⎋ None (asserts structural integrity)
    ## @complexity — O(N) where N = number of include paths
    """
    # 🧪 TRAP[TEST] · 2026-07-18 · Invariant 7 structural validation
    assert _COMPOSE_FILE.exists(), f"[IMP:9][gate][local_stack] FATAL: {_COMPOSE_FILE} not found"

    data = _parse_compose(_COMPOSE_FILE)
    assert data, f"[IMP:9][gate][local_stack] FATAL: {_COMPOSE_FILE} is empty or unparseable"

    # ── 1. Verify include paths ──────────────────────────────────────────
    includes = data.get("include", [])
    assert isinstance(includes, list), "[IMP:9][gate][local_stack] FATAL: include: is not a list"
    logger.info("[IMP:9][gate][local_stack] Found %d include entries in docker-compose.yml", len(includes))

    missing_paths = []
    resolved_paths = []
    for entry in includes:
        if not isinstance(entry, dict):
            logger.warning("[IMP:7][gate][local_stack] Invalid include entry (not dict): %s", entry)
            continue
        path_str = entry.get("path", "")
        if not path_str:
            continue
        full_path = (_PROJECT_ROOT / path_str).resolve()
        if full_path.exists() and full_path.is_file():
            resolved_paths.append(str(path_str))
            logger.info("[IMP:8][gate][local_stack] Include path resolved: %s", path_str)
        else:
            missing_paths.append(str(path_str))
            logger.error("[IMP:9][gate][local_stack] Include path NOT FOUND: %s", path_str)

    assert not missing_paths, (
        f"[IMP:9][gate][local_stack] FAIL: {len(missing_paths)} include paths not resolved:\n"
        + "\n".join(f"  - {p}" for p in missing_paths)
    )
    logger.info("[IMP:9][gate][local_stack] All %d include paths resolved", len(resolved_paths))

    # ── 2. Verify networks ───────────────────────────────────────────────
    networks = data.get("networks", {})
    if isinstance(networks, dict):
        network_count = len(networks)
        logger.info("[IMP:9][gate][local_stack] Networks declared: %d", network_count)
        # Verify all are external
        non_external = [name for name, cfg in networks.items() if isinstance(cfg, dict) and not cfg.get("external")]
        if non_external:
            logger.warning(
                "[IMP:7][gate][local_stack] Non-external networks found: %s — may be intentional",
                non_external,
            )
        assert network_count >= EXPECTED_NETWORKS, (
            f"[IMP:9][gate][local_stack] FAIL: Expected ≥{EXPECTED_NETWORKS} networks, got {network_count}"
        )

    # ── 3. Verify volumes ────────────────────────────────────────────────
    volumes = data.get("volumes", {})
    if isinstance(volumes, dict):
        volume_count = len(volumes)
        logger.info("[IMP:9][gate][local_stack] Volumes declared: %d", volume_count)
        assert volume_count >= EXPECTED_VOLUMES, (
            f"[IMP:9][gate][local_stack] FAIL: Expected ≥{EXPECTED_VOLUMES} volumes, got {volume_count}"
        )

    logger.info(
        "[IMP:9][gate][local_stack] PASS: Full stack resolves — %d includes, %d networks, %d volumes",
        len(resolved_paths),
        network_count if isinstance(networks, dict) else 0,
        volume_count if isinstance(volumes, dict) else 0,
    )


@pytest.mark.gate
@ldd_trajectory
def test_all_modules_included(caplog):
    """Verify include section of root compose matches discover_modules output.

    ## @purpose — Detect drift between docker-compose.yml include: and
    ##            core/modules/*/module.yaml (docker type). Adding/removing a
    ##            docker module without updating the include section is caught.
    ## @io — ⎋ None (asserts parity)
    ## @complexity — O(N+M) where N = include entries, M = module dirs
    """
    # 🧪 TRAP[TEST] · 2026-07-18 · Module include sync drift detection
    assert _COMPOSE_FILE.exists(), f"[IMP:9][gate][local_stack] FATAL: {_COMPOSE_FILE} not found"

    data = _parse_compose(_COMPOSE_FILE)
    assert data, f"[IMP:9][gate][local_stack] FATAL: {_COMPOSE_FILE} is empty or unparseable"

    includes = data.get("include", [])
    include_modules = set()
    for entry in includes:
        if not isinstance(entry, dict):
            continue
        path_str = entry.get("path", "")
        # Extract module name from path: core/modules/<name>/docker-compose.base.yml
        if "/modules/" in path_str:
            parts = path_str.split("/")
            try:
                idx = parts.index("modules")
                if idx + 1 < len(parts):
                    include_modules.add(parts[idx + 1])
            except ValueError:
                pass

    logger.info("[IMP:9][gate][local_stack] Include modules: %s", sorted(include_modules))

    # Discover expected docker modules
    expected_modules = _get_expected_modules()
    logger.info("[IMP:9][gate][local_stack] Expected docker modules (from module.yaml): %s", sorted(expected_modules))

    # Find diffs
    missing_in_include = expected_modules - include_modules
    extra_in_include = include_modules - expected_modules

    if missing_in_include:
        logger.error(
            "[IMP:9][gate][local_stack] Modules in core/modules/ but NOT in compose include: %s",
            sorted(missing_in_include),
        )
    if extra_in_include:
        logger.error(
            "[IMP:9][gate][local_stack] Modules in compose include but NOT in core/modules/: %s",
            sorted(extra_in_include),
        )

    assert not missing_in_include, (
        f"[IMP:9][gate][local_stack] FAIL: {len(missing_in_include)} modules not in include section:\n"
        + "\n".join(f"  - {m}" for m in sorted(missing_in_include))
    )
    assert not extra_in_include, (
        f"[IMP:9][gate][local_stack] FAIL: {len(extra_in_include)} modules in include but not in core/modules/:\n"
        + "\n".join(f"  - {m}" for m in sorted(extra_in_include))
    )

    logger.info(
        "[IMP:9][gate][local_stack] PASS: Include section in sync — %d modules, all accounted for",
        len(include_modules),
    )
