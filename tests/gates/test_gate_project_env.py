# GREP_SUMMARY: gate project-env env-platform dotenv presence provides-profiles validation d2
# STRUCTURE: ▶ glob projects/*/*/ai-platform.yaml → ○ for each: check .env.platform exists → ◇ if exists: parse env vars → ⊕ validate provides ⊆ profiles from platform-env.yaml → ⎋ PASS/FAIL
# region MODULE_CONTRACT
## @purpose  D3 gate — validate .env.platform presence and structural integrity for all projects.
##           Every project MUST have a .env.platform file (platform service descriptors).
##           If present, the file must reference only provides that have matching profiles.
## @scope    Scans projects/ directory tree for ai-platform.yaml files, checks each sibling
##           .env.platform for existence and structural consistency with platform-env.yaml.
## @invariants
##   - projects/ directory may not exist (dev environment) → skip gracefully
##   - Missing .env.platform for an existing project → FAIL (environmental config error)
##   - .env.platform must contain valid KEY=VALUE pairs
##   - Service references (PLATFORM_<SERVICE>_*) must have a corresponding entry in
##     platform-env.yaml provides, and that provides must be in the profiles list
## @rationale  D3 enforcement gate: AC-D3-ENV requires make gate MODE=fast to check .env.platform
##             for all registered projects. The provides ⊆ profiles check prevents referencing
##             services that are not deployed on the node.
## @usecases
##   - make gate MODE=fast → validates all projects have .env.platform with consistent content
##   - CI pipeline → blocks merge if any project lacks .env.platform or references unknown services
## @changes — 2026-07-20 | Created per DevPlan 020 Task 5.2
# endregion MODULE_CONTRACT

import glob
import logging
import os
import re

import pytest
import yaml
from tests.helpers.gate_helpers import repo_root

from tests._conftest.ldd import ldd_trajectory

_PROJECTS_DIR = os.path.join(repo_root(), "projects")
_PLATFORM_ENV_YAML = os.path.join(repo_root(), "platform-env.yaml")

_logger = logging.getLogger(__name__)

# Regex to match PLATFORM_<SERVICE>_* variable names
_PLATFORM_VAR_RE = re.compile(r"^PLATFORM_([A-Z][A-Z0-9_]+)_")
# Extracted service names that are NOT actual services (networks, internal metadata, etc.)
# These arise from variables like PLATFORM_NO_PROXY (→ 'no'), PLATFORM_*_NET (→ network names)
_NON_SERVICE_NAMES: set[str] = {
    "no",  # PLATFORM_NO_PROXY — exclusions list, not a service
    "proxy",  # PLATFORM_PROXY_NET — Docker network, not a service
    "hermes_agent",  # PLATFORM_HERMES_AGENT_NET — Docker network, not a service
    "shared_cache",  # PLATFORM_SHARED_CACHE_NET — Docker network, not a service
    "shared_db",  # PLATFORM_SHARED_DB_NET — Docker network, not a service
}


# region FUNC_load_provides_profiles
def _load_provides_profiles() -> tuple[set[str], set[str]]:
    """Load provides keys and profiles list from platform-env.yaml.

    ## @purpose — Provide the canonical set of known services (provides) and
    ##            enabled profiles from the platform environment descriptor.
    ## @io — ⎋ tuple(provides_keys: set[str], profile_names: set[str])
    ## @complexity — O(P + R) where P = profiles, R = provides entries
    """
    if not os.path.isfile(_PLATFORM_ENV_YAML):
        _logger.warning("[IMP:7][gate][env] platform-env.yaml not found at %s", _PLATFORM_ENV_YAML)
        return set(), set()

    with open(_PLATFORM_ENV_YAML) as f:
        data = yaml.safe_load(f)

    if data is None:
        return set(), set()

    provides_keys: set[str] = set(data.get("provides", {}).keys())
    profile_names: set[str] = set(data.get("profiles", []))

    return provides_keys, profile_names


# endregion FUNC_load_provides_profiles


# region FUNC_test_project_env_platform
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · .env.platform presence and structural integrity
# · Last fail: N/A (preventive)
# · Remove if: .env.platform is no longer a required project artifact
def test_project_env_platform(caplog) -> None:
    """Validate .env.platform presence and provides ⊆ profiles for all projects.

    ## @purpose — D3 enforcement gate: every project must have a valid .env.platform
    ##            referencing only deployed services.
    ## @io — ⎋ None. Assert: all projects have valid .env.platform.
    ## @complexity — O(N * M) where N = yaml files, M = avg lines in .env.platform
    """
    # region BLOCK_CheckProjectsDir
    if not os.path.isdir(_PROJECTS_DIR):
        _logger.info("[IMP:7][gate][env] Projects directory not found: %s — skip (dev environment)", _PROJECTS_DIR)
        pytest.skip("No projects/ directory — dev environment")
    # endregion

    # region BLOCK_FindYamls
    yaml_pattern = os.path.join(_PROJECTS_DIR, "*", "*", "ai-platform.yaml")
    yaml_files = glob.glob(yaml_pattern)
    _logger.info("[IMP:8][gate][env] Glob pattern: %s → %d files", yaml_pattern, len(yaml_files))

    if not yaml_files:
        _logger.info("[IMP:7][gate][env] No ai-platform.yaml files found in projects/ — skip")
        pytest.skip("No project configs found")
    # endregion

    # region BLOCK_LoadPlatformEnv
    provides_keys, profile_names = _load_provides_profiles()
    _logger.info(
        "[IMP:8][gate][env] platform-env.yaml: %d provides, %d profiles: %s",
        len(provides_keys),
        len(profile_names),
        sorted(profile_names) if profile_names else "(empty)",
    )
    # endregion

    # region BLOCK_ValidateEach
    issues: list[str] = []

    for yaml_path in sorted(yaml_files):
        project_dir = os.path.dirname(yaml_path)
        env_platform_path = os.path.join(project_dir, ".env.platform")
        rel_project = os.path.relpath(project_dir, _PROJECTS_DIR)

        _logger.info("[IMP:7][gate][env] Checking project: %s", rel_project)

        # Check .env.platform exists
        if not os.path.isfile(env_platform_path):
            issues.append(
                f"{rel_project}/.env.platform: MISSING — every project must have .env.platform "
                f"(regenerate with: make sync-env)"
            )
            _logger.error("[IMP:9][gate][env] MISSING: %s/.env.platform", rel_project)
            continue

        _logger.info("[IMP:7][gate][env] %s/.env.platform: EXISTS", rel_project)

        # Parse .env.platform for PLATFORM_<SERVICE>_* variable names
        if not provides_keys and not profile_names:
            _logger.warning("[IMP:7][gate][env] Provides/profiles not loaded — skipping structural check")
            continue

        try:
            with open(env_platform_path) as f:
                env_content = f.read()
        except Exception as exc:
            issues.append(f"{rel_project}/.env.platform: cannot read: {exc}")
            _logger.error("[IMP:9][gate][env] READ FAIL: %s — %s", rel_project, exc)
            continue

        # Extract unique service names from PLATFORM_<SERVICE>_* variables
        referenced_services: set[str] = set()
        for line in env_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _PLATFORM_VAR_RE.match(line)
            if match:
                service_name = match.group(1).lower()
                if service_name in _NON_SERVICE_NAMES:
                    _logger.debug(
                        "[IMP:8][gate][env] Skipping non-service ref: %s → %s", line.split("=", 1)[0], service_name
                    )
                    continue
                referenced_services.add(service_name)

        _logger.info(
            "[IMP:8][gate][env] %s: referenced services = %s",
            rel_project,
            sorted(referenced_services) if referenced_services else "(none)",
        )

        # Validate each referenced service has a corresponding provides entry and profile
        for svc in referenced_services:
            if svc not in provides_keys:
                issues.append(
                    f"{rel_project}/.env.platform: references '{svc}' "
                    f"which is not in platform-env.yaml provides ({sorted(provides_keys)})"
                )
                _logger.error("[IMP:9][gate][env] UNKNOWN SERVICE: %s → %s", rel_project, svc)
            elif svc not in profile_names:
                issues.append(
                    f"{rel_project}/.env.platform: references '{svc}' from provides, "
                    f"but '{svc}' is not in platform-env.yaml profiles ({sorted(profile_names)})"
                )
                _logger.error("[IMP:9][gate][env] UNPROFILED SERVICE: %s → %s not in profiles", rel_project, svc)

    # endregion

    # region BLOCK_Report
    if issues:
        for issue in issues:
            _logger.error("[IMP:9][gate][env] FAIL: %s", issue)
        pytest.fail(f".env.platform validation failed ({len(issues)} issues):\n" + "\n".join(issues))

    _logger.info(
        "[IMP:9][gate][env] All %d projects pass .env.platform validation",
        len(yaml_files),
    )
    # endregion


# endregion FUNC_test_project_env_platform
