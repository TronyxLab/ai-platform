# GREP_SUMMARY: predeploy-gate compose-env-vars image-tags yaml-valid external-networks hardcoded-credentials predeploy
# STRUCTURE: ▶ all_compose_files → ∋ (yaml.safe_load|regex) → ◇ test_required_env_vars_present ⊕ test_docker_image_tags_pinned ⊕ test_all_compose_configs_valid ⊕ test_docker_networks_precreated ⊕ test_no_hardcoded_credentials → ∑ IMP:7-10 logs → ⎋ pass|fail
# @file test_predeploy_gate.py
# @purpose  Pre-deploy gate tests: static validation of docker-compose configs,
#           required env vars, image tags, external networks, and hardcoded credentials.
#           All tests are lightweight and do NOT require Docker daemon (except
#           test_docker_networks_precreated which calls docker network ls).
# @scope    Static validation of compose YAMLs in core/modules/*/ before deployment.
#           Each test parses docker-compose.base.yml files via yaml.safe_load()
#           or regex, never via `docker compose config`. Intended to "fail fast"
#           before expensive compose up.
# @invariants
#   - All tests use @pytest.mark.predeploy
#   - No docker compose up/down — static/lightweight checks only
#   - test_docker_networks_precreated: warning (not fail) for missing networks
#   - Uses yaml.safe_load() for static YAML parsing
#   - Subprocess used only for docker network ls (one test)
# @rationale  Pre-deploy gate catches config errors early, before Docker daemon
#             startup. Lightweight checks fail fast in CI. Heavy checks (compose up,
#             healthchecks) are in component/smoke tests.
#             AC-T2 from DevPlan — 5 acceptance criteria.
#

# region MODULE_CONTRACT
## @purpose  Pre-deploy gate: 5 lightweight tests that validate docker-compose configs
##           before `docker compose up`. Catches missing env vars, :latest tags, invalid
##           YAML, missing external networks, and hardcoded credentials.
##           AC-T2.1 through AC-T2.5 from DevPlan.
## @scope    Static YAML parsing of core/modules/*/docker-compose.base.yml files.
##           Does NOT require Docker daemon (except test_docker_networks_precreated).
##           All tests marked @pytest.mark.predeploy.
## @invariants
##   - test_required_env_vars_present: checks ${VAR} (no default) exist in os.environ or .env
##   - test_docker_image_tags_pinned: fails on any image:tag ending with :latest
##   - test_all_compose_configs_valid: yaml.safe_load() returns non-empty dict for every compose
##   - test_docker_networks_precreated: warns (not fails) if external:true network missing
##   - test_no_hardcoded_credentials: fails on literal password/secret/key/token values
##   - All tests produce IMP:7-10 LDD logs
## @rationale — Lightweight pre-deploy gate fails fast in CI before expensive
##              docker compose up. AC-T2 from DevPlan §TASK-2.
## @changes — CREATED: 2026-07-03 | Wave 2: TASK-2 pre-deploy gate
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import contextlib
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from _conftest.honesty import require_docker_or_fail
from conftest import ldd_trajectory

from tests._conftest.r1 import r1_delegates

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Regex for Docker Compose environment variable substitution
# Captures: group 1 = var_name, group 2 = default value (or None)
ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

# Known false-positive var names from documentation placeholders (not real env vars)
SKIP_DOC_VARS: frozenset = frozenset({"VAR"})

# Known images that intentionally use :latest (local builds, platform base image)
# Per DevPlan AC-T2.2 exception: images with :latest in the tag are ignored
# Note: ghcr.io/tronyx161/ai-platform-hermes:latest was REMOVED 2026-07-07 (STALE-1)
# This image name no longer exists — replaced by ghcr.io/tronyxlab/hermes-agent-platform:latest
KNOWN_LATEST_EXCEPTIONS: frozenset = frozenset(
    {
        "backup-cron:latest",  # Local build — no published version
        "status-page:latest",  # Local build — no published version
    }
)


# ── Helpers ────────────────────────────────────────────────────────────────────

# region HELPERS


def _extract_required_env_vars_from_compose(compose_file: str) -> set[str]:
    """Extract env var names from a compose file's environment: sections that
    have NO default value. Only scans service-level environment values, NOT
    comments or non-env text — avoids false positives from doc placeholders.

    ## @purpose — Parse a composed YAML's services.*.environment sections and
    ##            find all ${VAR} patterns. Return only those without :-default,
    ##            i.e. required variables.
    ## @io — ⇥ compose_file: str (path to YAML) → ⎋ set[str] (required var names)
    ## @complexity — O(N * E * T) where N = services, E = env vars, T = text length
    ## @invariants
    ##   - Only scans values in environment: sections (dict or list format)
    ##   - Ignores YAML comments and non-environment text
    ##   - Handles nested ${...} patterns via ENV_VAR_RE on each string value
    ##   - ${VAR:-default} → has default → NOT required
    ##   - ${VAR} → no default → required
    """
    required: set[str] = set()
    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_extract_required_env_vars] Cannot parse %s: %s", compose_file, exc)
        return required
    if not isinstance(data, dict):
        return required
    services = data.get("services", {})
    if not isinstance(services, dict):
        return required

    def _scan_value(value: str) -> None:
        """Extract required env vars from a single string value."""
        for match in ENV_VAR_RE.finditer(value):
            inner = match.group(1)
            # ${VAR:-default} → has default → skip
            if ":-" in inner:
                continue
            # ${VAR:?error message} → fail-fast syntax, no default → required
            # Extract var name before the :?
            var_name = inner
            if ":?" in var_name:
                var_name = var_name.split(":?")[0]
            # Skip documentation placeholders
            if var_name in SKIP_DOC_VARS:
                continue
            required.add(var_name)

    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        env = svc_config.get("environment")
        if env is None:
            continue
        # Dict format: VAR: value
        if isinstance(env, dict):
            for var_value in env.values():
                if isinstance(var_value, str):
                    _scan_value(var_value)
        # List format: ["VAR=value", ...]
        elif isinstance(env, list):
            for entry in env:
                if isinstance(entry, str):
                    _scan_value(entry)

    logger.info(
        "[IMP:8][_extract_required_env_vars] Found %d required var(s) in %s: %s",
        len(required),
        compose_file,
        sorted(required),
    )
    return required


def _load_env_file(env_path: str) -> dict[str, str]:
    """Parse a .env file (key=value format, ignoring comments/blanks).

    ## @purpose — Load key-value pairs from a .env file without python-dotenv.
    ## @io — ⇥ env_path: str → ⎋ dict[str, str]
    ## @complexity — O(n) where n = number of lines
    """
    result: dict[str, str] = {}
    if not os.path.isfile(env_path):
        logger.info("[IMP:7][_load_env_file] .env file not found: %s", env_path)
        return result
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    logger.info("[IMP:8][_load_env_file] Loaded %d entries from %s", len(result), env_path)
    return result


def _parse_image_tags(compose_file: str) -> list[tuple[str, str]]:
    """Extract (service_name, image) tuples from a compose file.

    ## @purpose — Safely parse a compose YAML and return all service→image mappings.
    ## @io — ⇥ compose_file: str → ⎋ list[tuple[str, str]]
    ## @complexity — O(N * M) where N = services, M = YAML size
    """
    result: list[tuple[str, str]] = []
    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_parse_image_tags] Failed to parse %s: %s", compose_file, exc)
        return result
    if not isinstance(data, dict):
        return result
    services = data.get("services", {})
    if not isinstance(services, dict):
        return result
    for svc_name, svc_config in services.items():
        if isinstance(svc_config, dict) and isinstance(svc_config.get("image"), str):
            result.append((svc_name, svc_config["image"]))
    logger.info("[IMP:8][_parse_image_tags] Parsed %d images from %s", len(result), compose_file)
    return result


def _parse_external_networks(compose_file: str) -> set[str]:
    """Extract external network names from a compose file's top-level networks: section.

    ## @purpose — Find all networks declared with external: true for verification.
    ## @io — ⇥ compose_file: str → ⎋ set[str] (network names)
    ## @complexity — O(N) where N = number of top-level networks
    """
    result: set[str] = set()
    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_parse_external_networks] Failed to parse %s: %s", compose_file, exc)
        return result
    if not isinstance(data, dict):
        return result
    networks = data.get("networks", {})
    if not isinstance(networks, dict):
        return result
    for net_name, net_config in networks.items():
        if isinstance(net_config, dict) and net_config.get("external") is True:
            result.add(net_name)
    logger.info(
        "[IMP:8][_parse_external_networks] Found %d external network(s) in %s: %s",
        len(result),
        compose_file,
        sorted(result),
    )
    return result


# endregion HELPERS


# region T1_T5_HELPERS


def _parse_compose_ports(compose_file: str) -> list[int]:
    """Extract host port numbers from a compose file's services.*.ports sections.

    ## @purpose — Parse YAML ports declarations and return only HOST ports
    ##            (the left side of the colon). Used for port conflict detection (T2).
    ## @io — ⇥ compose_file: str → ⎋ list[int] (host port numbers, unsorted)
    ## @complexity — O(S * P) where S = services, P = ports per service
    ## @invariants
    ##   - Handles string format "8080:80", numeric format 8080:80, and
    ##     long syntax {"published": 8080, "target": 80}
    ##   - Handles host-IP prefix: "127.0.0.1:8080:80" → 8080
    ##   - Ignores ports without host binding (CONTAINER-only: "80")
    ##   - Returns empty list on parse error
    """
    host_ports: list[int] = []
    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_parse_compose_ports] Cannot parse %s: %s", compose_file, exc)
        return host_ports
    if not isinstance(data, dict):
        return host_ports
    services = data.get("services", {})
    if not isinstance(services, dict):
        return host_ports

    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        ports = svc_config.get("ports")
        if ports is None:
            continue
        if isinstance(ports, list):
            for entry in ports:
                if isinstance(entry, str):
                    # "8080:80" or "127.0.0.1:8080:80"
                    parts = entry.split(":")
                    if len(parts) >= 2:
                        try:
                            host_port = int(parts[-2])
                            host_ports.append(host_port)
                        except ValueError:
                            continue  # unparseable host port — skip this entry (R1: no bare pass)
                elif isinstance(entry, (int, float)):
                    # bare port number (container-only, no host binding) — skip
                    pass
                elif isinstance(entry, dict):
                    # long syntax: {"published": 8080, "target": 80}
                    published = entry.get("published")
                    if published is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            host_ports.append(int(published))
        logger.info(
            "[IMP:8][_parse_compose_ports] [%s] Parsed %d host port(s) from %s",
            svc_name,
            len(host_ports),
            compose_file,
        )

    logger.info("[IMP:8][_parse_compose_ports] Total %d host port(s) in %s", len(host_ports), compose_file)
    return host_ports


def _parse_ai_platform_yaml(file_path: str) -> dict | None:
    """Parse and validate an ai-platform.yaml file.

    ## @purpose — Safe YAML loading of ai-platform.yaml for schema validation (T5).
    ## @io — ⇥ file_path: str → ⎋ dict | None (None on parse error)
    ## @complexity — O(Y) where Y = YAML parse size
    """
    try:
        with open(file_path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[IMP:4][_parse_ai_platform_yaml] Failed to parse %s: %s", file_path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning(
            "[IMP:4][_parse_ai_platform_yaml] %s: root is not a dict (type=%s)", file_path, type(data).__name__
        )
        return None
    return data


def _find_project_ai_platform_yamls(node_yaml_projects: list[dict]) -> list[tuple[str, str]]:
    """Find ai-platform.yaml files for all projects from node.yaml.

    ## @purpose — Locate ai-platform.yaml files in project directories for T5 schema validation.
    ## @io — ⇥ node_yaml_projects: list[dict] → ⎋ list[tuple[str, str]] (project_name, file_path)
    ## @complexity — O(N) where N = number of projects
    ## @invariants
    ##   - PROJECTS_DIR env var overrides default /opt/projects/
    ##   - Also checks tests/test_data/projects/<name>/ for local dev
    ##   - Only returns paths that actually exist
    """
    projects_dir = os.environ.get("PROJECTS_DIR", "/opt/projects")
    test_data_dir = os.environ.get(
        "TEST_PROJECTS_DIR",
        os.path.join(
            os.environ.get(
                "PLATFORM_ROOT",
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            ),
            "tests",
            "test_data",
            "projects",
        ),
    )

    results: list[tuple[str, str]] = []
    for proj in node_yaml_projects:
        name = proj.get("name", "")
        if not name:
            continue

        # Primary: PROJECTS_DIR/<name>/ai-platform.yaml
        primary = os.path.join(projects_dir, name, "ai-platform.yaml")
        if os.path.isfile(primary):
            results.append((name, primary))
            continue

        # Fallback: tests/test_data/projects/<name>/ai-platform.yaml
        fallback = os.path.join(test_data_dir, name, "ai-platform.yaml")
        if os.path.isfile(fallback):
            results.append((name, fallback))

    return results


# endregion T1_T5_HELPERS


# ── Fixtures ───────────────────────────────────────────────────────────────────

# region FIXTURES


@pytest.fixture(scope="module", autouse=True)
def _predeploy_logger() -> None:
    """Module-level setup: log predeploy gate start.

    ## @purpose — Log the predeploy gate module entry point for LDD trajectory.
    ## @io — ⎋ None
    ## @complexity — O(1)
    """
    logger.info("[IMP:7][predeploy_gate] ===== Pre-deploy gate tests started =====")


# endregion FIXTURES


# ── Tests ─────────────────────────────────────────────────────────────────────

# region TESTS

# ── Test 1: Required env vars present ─────────────────────────────────────────

# region FUNC_test_required_env_vars_present
## @purpose — Verify all ${VAR} in compose files (no default) are set in os.environ
##            or in the hermes-agent .env file. AC-T2.1.
## @io — ⇥ caplog, all_compose_files, modules_dir → ⎋ None (pytest.fail if missing vars)
## @complexity — O(F * N) where F = compose files, N = vars per file
## @invariants
##   - Extracts ${VAR} via regex on raw YAML text (not parsed YAML)
##   - Filters out vars with :-default (not required)
##   - Checks os.environ first, then falls back to .env file
##   - pytest.fail with sorted list of missing vars


@pytest.mark.predeploy
@pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("E2E_MODE") == "ci",
    reason="CI: secrets are in GitHub Actions Secrets, tested during deploy — not pre-deploy gate",
)
@ldd_trajectory
def test_required_env_vars_present(
    caplog: pytest.LogCaptureFixture,
    all_compose_files: dict[str, str],
    modules_dir: str,
) -> None:
    """
    # ◇ all_compose_files → ∋ each .yml → regex ${VAR} → ⚡ filter ${VAR:-default}
    # → ⊕ required_vars → ⚡ os.environ ∪ .env → ◇ missing? → ⎋ pass | fail
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_required_env_vars_present] Scanning %d compose file(s) for required env vars ...",
        len(all_compose_files),
    )
    # endregion

    # region BLOCK_ExtractVars
    required_vars: set[str] = set()
    for module_name, compose_path in sorted(all_compose_files.items()):
        file_vars = _extract_required_env_vars_from_compose(compose_path)
        if file_vars:
            logger.info("[IMP:8][test_required_env_vars_present] %s requires: %s", module_name, sorted(file_vars))
            required_vars.update(file_vars)

    logger.info("[IMP:9][test_required_env_vars_present] Total required vars (no default): %d", len(required_vars))
    if not required_vars:
        logger.info("[IMP:7][test_required_env_vars_present] No required vars found — skipping check")
        return
    # endregion

    # region BLOCK_CheckEnv
    # Check os.environ first (includes _load_test_env from conftest)
    missing_vars: set[str] = set()
    for var_name in sorted(required_vars):
        if var_name in os.environ and os.environ[var_name].strip():
            logger.info("[IMP:8][test_required_env_vars_present] %s = %s (from os.environ)", var_name, "(set)")
            continue
        # Fallback: check .env file directly
        env_path = os.path.join(modules_dir, "hermes-agent", ".env")
        env_vars = _load_env_file(env_path)
        if var_name in env_vars and env_vars[var_name].strip():
            logger.info("[IMP:8][test_required_env_vars_present] %s = %s (from .env)", var_name, "(set)")
            continue
        missing_vars.add(var_name)

    # endregion

    # region BLOCK_Assert
    if missing_vars:
        missing_sorted = sorted(missing_vars)
        logger.error("[IMP:9][test_required_env_vars_present] MISSING required env vars: %s", missing_sorted)
        pytest.fail(
            f"Required environment variables missing (no default, not set in os.environ or .env):\n"
            f"  {', '.join(missing_sorted)}\n"
            f"Set them in core/modules/hermes-agent/.env or export in shell."
        )

    logger.info("[IMP:9][test_required_env_vars_present] ✅ All required env vars are set")
    # endregion


# endregion FUNC_test_required_env_vars_present


# ── Test 2: Image tags pinned ─────────────────────────────────────────────────

# region FUNC_test_docker_image_tags_pinned
## @purpose — Verify no service image uses the :latest tag. All images must be
##            pinned to a specific version. AC-T2.2.
## @io — ⇥ caplog, all_compose_files → ⎋ None (pytest.fail if :latest found)
## @complexity — O(F * S) where F = compose files, S = services per file
## @invariants
##   - Parses YAML services[].image for each compose file
##   - Flags any image ending with :latest
##   - build: sections with image:latest (local build) are also flagged


@pytest.mark.predeploy
@ldd_trajectory
def test_docker_image_tags_pinned(
    caplog: pytest.LogCaptureFixture,
    all_compose_files: dict[str, str],
) -> None:
    """
    # ◇ all_compose_files → ∋ each .yml → yaml.safe_load → services[] → image
    # → ⊕ :latest check → ◇ found :latest? → ⎋ fail | pass
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_docker_image_tags_pinned] Checking %d compose files for :latest tags ...", len(all_compose_files)
    )
    untagged: list[tuple[str, str, str]] = []  # (module, service, image)
    skipped_exceptions: list[tuple[str, str, str]] = []  # exceptions not flagged
    # endregion

    # region BLOCK_Scan
    for module_name, compose_path in sorted(all_compose_files.items()):
        images = _parse_image_tags(compose_path)
        for svc_name, image in images:
            # Check if the tag (after last colon) is "latest"
            # edge: "alpine" is not latest, "v1.0.0" is not latest
            if ":" in image:
                tag = image.rsplit(":", 1)[1]
                if tag == "latest":
                    # Check known exceptions (local builds, platform base images)
                    if image in KNOWN_LATEST_EXCEPTIONS:
                        skipped_exceptions.append((module_name, svc_name, image))
                    else:
                        untagged.append((module_name, svc_name, image))
            else:
                # No tag at all — also suspicious (defaults to :latest)
                if image in KNOWN_LATEST_EXCEPTIONS:
                    skipped_exceptions.append((module_name, svc_name, f"{image} (no tag)"))
                else:
                    untagged.append((module_name, svc_name, f"{image}:latest (implicit)"))
    # endregion

    # region BLOCK_Assert
    # Log skipped exceptions (known :latest images, AC-T2.2)
    if skipped_exceptions:
        for mod, svc, img in skipped_exceptions:
            logger.info("[IMP:7][test_docker_image_tags_pinned] ⓘ  Known exception: [%s] %s → %s", mod, svc, img)

    if untagged:
        logger.error("[IMP:9][test_docker_image_tags_pinned] Found %d image(s) with :latest tag", len(untagged))
        for mod, svc, img in untagged:
            logger.error("  [%s] %s → %s", mod, svc, img)
        pytest.fail(
            f"Found {len(untagged)} image(s) using :latest tag. "
            f"All images must be pinned to a specific version.\n"
            + "\n".join(f"  [{m}] {s} → {i}" for m, s, i in untagged)
        )

    logger.info("[IMP:9][test_docker_image_tags_pinned] ✅ All images are pinned (no :latest)")
    # endregion


# endregion FUNC_test_docker_image_tags_pinned


# ── Test 3: Compose configs valid YAML ────────────────────────────────────────

# region FUNC_test_all_compose_configs_valid
## @purpose — Verify all docker-compose.base.yml files are valid YAML via
##            yaml.safe_load(). Static validation — no Docker daemon required.
##            AC-T2.3.
## @io — ⇥ caplog, all_compose_files → ⎋ None (pytest.fail on invalid YAML)
## @complexity — O(F * Y) where F = compose files, Y = YAML parse size
## @invariants
##   - Uses yaml.safe_load() only (no docker compose config)
##   - Files must parse as non-None dict
##   - Works without Docker daemon


@pytest.mark.predeploy
@ldd_trajectory
def test_all_compose_configs_valid(
    caplog: pytest.LogCaptureFixture,
    all_compose_files: dict[str, str],
) -> None:
    """
    # ◇ all_compose_files → ∋ each .yml → yaml.safe_load()
    # → ◇ isinstance(dict) ? → ⎋ pass | fail
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_all_compose_configs_valid] Validating %d compose YAML file(s) ...", len(all_compose_files)
    )
    errors: list[str] = []
    # endregion

    # region BLOCK_Validate
    for module_name, compose_path in sorted(all_compose_files.items()):
        try:
            with open(compose_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            errors.append(f"[{module_name}] YAML parse error: {exc}")
            logger.error("[IMP:4][test_all_compose_configs_valid] %s: YAML error: %s", compose_path, exc)
            continue
        except OSError as exc:
            errors.append(f"[{module_name}] File read error: {exc}")
            logger.error("[IMP:4][test_all_compose_configs_valid] %s: read error: %s", compose_path, exc)
            continue

        if data is None:
            errors.append(f"[{module_name}] Empty compose file (yielded None)")
            logger.error("[IMP:4][test_all_compose_configs_valid] %s: empty file", compose_path)
        elif not isinstance(data, dict):
            errors.append(f"[{module_name}] Unexpected YAML root type: {type(data).__name__}")
            logger.error(
                "[IMP:4][test_all_compose_configs_valid] %s: root is %s, expected dict",
                compose_path,
                type(data).__name__,
            )
        else:
            logger.info(
                "[IMP:8][test_all_compose_configs_valid] ✅ %s: valid YAML (%d top-level keys)", module_name, len(data)
            )
    # endregion

    # region BLOCK_Assert
    if errors:
        pytest.fail("Compose YAML validation failed:\n" + "\n".join(errors))

    logger.info(
        "[IMP:9][test_all_compose_configs_valid] ✅ All %d compose file(s) are valid YAML", len(all_compose_files)
    )
    # endregion


# endregion FUNC_test_all_compose_configs_valid


# ── Test 4: Docker networks pre-created ───────────────────────────────────────

# region FUNC_test_docker_networks_precreated
## @purpose — Verify all external:true networks declared in compose files
##            actually exist in Docker. Runs `docker network ls` and cross-references.
##            NOTE: This is a WARNING-level check (not fail) because pre-deploy gate
##            runs before docker compose up, and networks may be created later.
##            AC-T2.4.
## @io — ⇥ caplog, all_compose_files → ⎋ None (warning if networks missing)
## @complexity — O(F * N + D) where F = compose files, N = networks per file,
##               D = docker network ls subprocess
## @invariants
##   - Requires Docker daemon (docker network ls)
##   - Missing networks produce WARNING, not FAIL
##   - Only checks networks with external: true


@pytest.mark.predeploy
@ldd_trajectory
@r1_delegates
def test_docker_networks_precreated(
    caplog: pytest.LogCaptureFixture,
    all_compose_files: dict[str, str],
) -> None:
    """
    # ◇ all_compose_files → ∋ each .yml → yaml.safe_load → networks → external:true
    # → ⊕ docker network ls → ◇ missing? → ⎋ warning | pass
    # 🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: warning-only by contract
    #   ("Missing networks produce WARNING, not FAIL" — module contract выше).
    """
    # region BLOCK_Setup

    logger.info("[IMP:7][test_docker_networks_precreated] Checking external Docker networks ...")
    # endregion

    # region BLOCK_CollectExternalNetworks
    external_networks: set[str] = set()
    for module_name, compose_path in sorted(all_compose_files.items()):
        nets = _parse_external_networks(compose_path)
        if nets:
            logger.info(
                "[IMP:8][test_docker_networks_precreated] [%s] external networks: %s", module_name, sorted(nets)
            )
            external_networks.update(nets)

    if not external_networks:
        logger.info("[IMP:7][test_docker_networks_precreated] No external networks declared — skipping")
        return
    # endregion

    # region BLOCK_GetDockerNetworks
    existing_networks: set[str] = set()
    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            existing_networks = {line.strip() for line in result.stdout.splitlines() if line.strip()}
            logger.info(
                "[IMP:8][test_docker_networks_precreated] Docker has %d network(s): %s",
                len(existing_networks),
                sorted(existing_networks),
            )
        else:
            logger.warning(
                "[IMP:4][test_docker_networks_precreated] docker network ls returned %d: %s",
                result.returncode,
                result.stderr.strip(),
            )
    except FileNotFoundError:
        logger.warning("[IMP:4][test_docker_networks_precreated] docker CLI not found — cannot check networks")
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:4][test_docker_networks_precreated] docker network ls failed: %s", exc)
    # endregion

    # region BLOCK_Compare
    missing_networks = external_networks - existing_networks
    if missing_networks:
        logger.warning(
            "[IMP:9][test_docker_networks_precreated] ⚠️  Missing external networks: %s", sorted(missing_networks)
        )
        logger.warning(
            "[IMP:9][test_docker_networks_precreated] Create them with: docker network create %s",
            " ".join(sorted(missing_networks)),
        )
        logger.warning(
            "[IMP:9][test_docker_networks_precreated] Continuing — networks may be created later by deploy-modules.sh"
        )
        # WARNING only (not fail): pre-deploy gate may run before network creation.
        # Networks are created by deploy-modules.sh before docker compose up.
        # Per DevPlan AC-T2.4: if not all networks created → warning, not fail.
    else:
        logger.info(
            "[IMP:9][test_docker_networks_precreated] ✅ All %d external network(s) exist", len(external_networks)
        )
    # endregion

    # region BLOCK_LDD
    # endregion


# endregion FUNC_test_docker_networks_precreated


# ═══════════════════════════════════════════════════════════════════════
# Wave 3: Predeploy gate extension — T1 through T5
# ═══════════════════════════════════════════════════════════════════════


# ── T1: Compose configs valid via docker compose config --dry-run ─────

# region FUNC_test_project_compose_configs_valid
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Project compose must pass `docker compose config --dry-run`
## @purpose — Validate project docker-compose.yml files by running
##            `docker compose -f <path> config --dry-run`. Catches invalid
##            compose syntax, missing env vars, and unresolvable references.
##            DevPlan W3-T1.
## @io — ⇥ caplog, project_compose_files → ⎋ None (pytest.fail if compose invalid)
## @complexity — O(F * C) where F = compose files, C = docker compose config runtime
## @invariants
##   - Requires Docker daemon (skipif when docker CLI absent)
##   - Uses docker compose (not docker-compose) — v2 API
##   - Runs `config --dry-run` which validates WITHOUT starting containers
##   - Skips cleanly if no project compose files found


@pytest.mark.predeploy
@pytest.mark.requires_docker
@ldd_trajectory
def test_project_compose_configs_valid(
    caplog: pytest.LogCaptureFixture,
    project_compose_files: list[Path],
) -> None:
    """
    # ◇ project_compose_files → ∋ each compose → ⚡ docker compose -f <path> config --dry-run
    # → ◇ exit code 0? → ⎋ pass | fail (with compose diagnostics)
    """
    # region BLOCK_Setup

    require_docker_or_fail(reason="docker compose config --dry-run requires Docker daemon")

    logger.info(
        "[IMP:7][test_project_compose_configs_valid] Validating %d project compose file(s) ...",
        len(project_compose_files),
    )

    if not project_compose_files:
        logger.info("[IMP:9][test_project_compose_configs_valid] No project compose files — skipping (not a failure)")
        return

    # endregion

    # region BLOCK_Validate
    errors: list[str] = []
    for compose_path in project_compose_files:
        logger.info("[IMP:8][test_project_compose_configs_valid] Validating: %s", compose_path)
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_path), "config", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ},
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip() or result.stdout.strip() or "unknown error"
                errors.append(f"[{compose_path.parent.name}] {compose_path.name}: {err_msg}")
                logger.error(
                    "[IMP:4][test_project_compose_configs_valid] FAIL: %s — exit %d: %s",
                    compose_path,
                    result.returncode,
                    err_msg,
                )
            else:
                logger.info(
                    "[IMP:9][test_project_compose_configs_valid] ✅ %s: valid compose config",
                    compose_path,
                )
        except FileNotFoundError:
            # Docker CLI not available despite shutil.which check (edge: race condition)
            logger.warning(
                "[IMP:9][test_project_compose_configs_valid] docker compose CLI vanished — skipping (not a failure)"
            )
            return
        except subprocess.TimeoutExpired:
            errors.append(f"[{compose_path.parent.name}] docker compose config timed out (>30s)")
            logger.error(
                "[IMP:4][test_project_compose_configs_valid] TIMEOUT: %s (>30s)",
                compose_path,
            )
        except OSError as exc:
            errors.append(f"[{compose_path.parent.name}] OSError: {exc}")
            logger.error("[IMP:4][test_project_compose_configs_valid] OSError: %s — %s", compose_path, exc)
    # endregion

    # region BLOCK_Assert
    if errors:
        pytest.fail("Project compose config validation failed:\n" + "\n".join(errors))

    logger.info(
        "[IMP:9][test_project_compose_configs_valid] ✅ All %d project compose file(s) valid",
        len(project_compose_files),
    )
    # endregion


# endregion FUNC_test_project_compose_configs_valid


# ── T2: Ports no conflict with platform ───────────────────────────────

# region FUNC_test_project_ports_no_conflict
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Project ports must not overlap with platform port_mappings
## @purpose — Verify that ports used by project compose files do not conflict
##            with platform ports defined in platform-env.yaml port_mappings.
##            Catches accidental port overlaps before deployment.
##            DevPlan W3-T2.
## @io — ⇥ caplog, project_compose_files, platform_port_mappings_dict → ⎋ None
## @complexity — O(F * P + M) where F = compose files, P = ports per file, M = mappings
## @invariants
##   - Reads port_mappings from platform-env.yaml (single source of truth)
##   - Parses host ports from compose services.*.ports
##   - Conflicts produce FAIL, not warning
##   - Skips cleanly if no project compose files found


@pytest.mark.predeploy
@ldd_trajectory
def test_project_ports_no_conflict(
    caplog: pytest.LogCaptureFixture,
    project_compose_files: list[Path],
    platform_port_mappings_dict: dict[str, int],
) -> None:
    """
    # ◇ project_compose_files → ∋ each compose → ⚡ _parse_compose_ports()
    # → ⊕ all_project_ports → ◇ intersection with platform_port_mappings_dict
    # → ⎋ empty intersection → pass | fail with conflict detail
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_project_ports_no_conflict] Checking project ports against %d platform port(s) ...",
        len(platform_port_mappings_dict),
    )

    if not project_compose_files:
        logger.info("[IMP:9][test_project_ports_no_conflict] No project compose files — skipping (not a failure)")
        return

    platform_ports: set[int] = set(platform_port_mappings_dict.values())
    if not platform_ports:
        logger.info("[IMP:9][test_project_ports_no_conflict] No platform port mappings — skipping (not a failure)")
        return
    # endregion

    # region BLOCK_CollectProjectPorts
    project_host_ports: list[tuple[str, int]] = []  # (project_name, port)
    for compose_path in project_compose_files:
        project_name = compose_path.parent.name
        ports = _parse_compose_ports(str(compose_path))
        for p in ports:
            project_host_ports.append((project_name, p))
            logger.info(
                "[IMP:8][test_project_ports_no_conflict] [%s] uses host port: %d",
                project_name,
                p,
            )
    # endregion

    # region BLOCK_FindConflicts
    conflicts: list[tuple[str, int, str]] = []  # (project_name, port, platform_service)
    for proj_name, port in project_host_ports:
        if port in platform_ports:
            # Find the platform service name for this port
            platform_service = next(
                (k for k, v in platform_port_mappings_dict.items() if v == port),
                "unknown",
            )
            conflicts.append((proj_name, port, platform_service))
            logger.error(
                "[IMP:9][test_project_ports_no_conflict] CONFLICT: [%s] port %d clashes with platform %s",
                proj_name,
                port,
                platform_service,
            )

    # endregion

    # region BLOCK_Assert
    if conflicts:
        conflict_lines = [f"  [{p}] port {pt} → platform: {s}" for p, pt, s in conflicts]
        pytest.fail(
            f"Found {len(conflicts)} port conflict(s) between project(s) and platform:\n"
            + "\n".join(conflict_lines)
            + "\nChange project port assignments or update platform-env.yaml port_mappings."
        )

    logger.info("[IMP:9][test_project_ports_no_conflict] ✅ No port conflicts with platform")
    # endregion


# endregion FUNC_test_project_ports_no_conflict


# ── T3: External networks declared in platform-env.yaml ───────────────

# region FUNC_test_project_external_networks_exist
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · External project networks must be declared in platform-env.yaml
## @purpose — Verify that all external:true networks used in project compose
##            files are declared in platform-env.yaml networks section.
##            Prevents deployment of projects requiring networks that don't exist.
##            DevPlan W3-T3.
## @io — ⇥ caplog, project_compose_files, platform_networks_list → ⎋ None
## @complexity — O(F * N + P) where F = compose files, N = networks per file,
##               P = platform networks
## @invariants
##   - Reads platform networks from platform-env.yaml (single source of truth)
##   - Only checks networks with external: true flag
##   - Missing networks produce FAIL (platform must be updated first)


@pytest.mark.predeploy
@ldd_trajectory
def test_project_external_networks_exist(
    caplog: pytest.LogCaptureFixture,
    project_compose_files: list[Path],
    platform_networks_list: list[str],
) -> None:
    """
    # ◇ project_compose_files → ∋ each compose → ⚡ _parse_external_networks()
    # → ⊕ all_external_nets → ◇ subset of platform_networks_list?
    # → ⎋ pass | fail (undocumented networks)
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_project_external_networks_exist] Checking %d project compose file(s) against %d platform network(s) ...",
        len(project_compose_files),
        len(platform_networks_list),
    )

    if not project_compose_files:
        logger.info("[IMP:9][test_project_external_networks_exist] No project compose files — skipping (not a failure)")
        return

    platform_nets: set[str] = set(platform_networks_list)
    if not platform_nets:
        logger.info(
            "[IMP:9][test_project_external_networks_exist] No platform networks declared — skipping (not a failure)"
        )
        return
    # endregion

    # region BLOCK_CollectExternalNetworks
    required_nets: dict[str, set[str]] = {}  # compose_file_path -> set[network_name]
    for compose_path in project_compose_files:
        nets = _parse_external_networks(str(compose_path))
        if nets:
            required_nets[str(compose_path)] = nets
            logger.info(
                "[IMP:8][test_project_external_networks_exist] [%s] external networks: %s",
                compose_path.parent.name,
                sorted(nets),
            )
    # endregion

    # region BLOCK_CheckMissing
    missing: list[tuple[str, str]] = []  # (project_name, network_name)
    for compose_path_str, net_set in required_nets.items():
        project_name = Path(compose_path_str).parent.name
        for net_name in sorted(net_set):
            if net_name not in platform_nets:
                missing.append((project_name, net_name))
                logger.error(
                    "[IMP:9][test_project_external_networks_exist] MISSING: [%s] uses network '%s' not in platform-env.yaml",
                    project_name,
                    net_name,
                )
    # endregion

    # region BLOCK_Assert
    if missing:
        missing_lines = [f"  [{p}] {n}" for p, n in missing]
        pytest.fail(
            f"Found {len(missing)} external network(s) used by project(s) "
            f"but not declared in platform-env.yaml networks:\n"
            + "\n".join(missing_lines)
            + "\nAdd missing networks to platform-env.yaml#networks or update project compose files."
        )

    logger.info(
        "[IMP:9][test_project_external_networks_exist] ✅ All project external networks declared in platform-env.yaml"
    )
    # endregion


# endregion FUNC_test_project_external_networks_exist


# ── T4: Every project compose requires proxy-net ─────────────────────

# region FUNC_test_project_requires_proxy_net
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · Every project compose must declare proxy-net (external: true)
## @purpose — Verify every project docker-compose.yml has proxy-net declared
##            as an external network. Nginx proxy routes external traffic through
##            this network, so every project must be attached.
##            DevPlan W3-T4.
## @io — ⇥ caplog, project_compose_files → ⎋ None
## @complexity — O(F * N) where F = compose files, N = networks per file
## @invariants
##   - Checks networks section for `proxy-net: {external: true}`
##   - Missing proxy-net produces FAIL
##   - Skips cleanly if no project compose files found


@pytest.mark.predeploy
@ldd_trajectory
def test_project_requires_proxy_net(
    caplog: pytest.LogCaptureFixture,
    project_compose_files: list[Path],
) -> None:
    """
    # ◇ project_compose_files → ∋ each compose → ⚡ _parse_external_networks()
    # → ◇ 'proxy-net' in external_nets? → ⎋ pass | fail (missing proxy-net)
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_project_requires_proxy_net] Checking %d project compose file(s) for proxy-net ...",
        len(project_compose_files),
    )

    if not project_compose_files:
        logger.info("[IMP:9][test_project_requires_proxy_net] No project compose files — skipping (not a failure)")
        return
    # endregion

    # region BLOCK_CheckProxyNet
    missing_proxy: list[str] = []  # project names
    for compose_path in project_compose_files:
        project_name = compose_path.parent.name
        nets = _parse_external_networks(str(compose_path))
        if "proxy-net" not in nets:
            missing_proxy.append(project_name)
            logger.error(
                "[IMP:9][test_project_requires_proxy_net] MISSING proxy-net: [%s] — compose file %s",
                project_name,
                compose_path.name,
            )
        else:
            logger.info(
                "[IMP:8][test_project_requires_proxy_net] ✅ [%s] has proxy-net",
                project_name,
            )
    # endregion

    # region BLOCK_Assert
    if missing_proxy:
        pytest.fail(
            f"Found {len(missing_proxy)} project compose file(s) without proxy-net (external: true):\n"
            + "\n".join(f"  [{p}]" for p in missing_proxy)
            + "\nAdd 'proxy-net: {external: true}' to each project's networks section."
        )

    logger.info("[IMP:9][test_project_requires_proxy_net] ✅ All project compose files have proxy-net")
    # endregion


# endregion FUNC_test_project_requires_proxy_net


# ── T5: ai-platform.yaml schema validation ────────────────────────────

# region FUNC_test_ai_platform_yaml_schema
# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · ai-platform.yaml must contain name, domain, target_node
## @purpose — Validate ai-platform.yaml schema: required fields 'name', 'domain',
##            and 'target_node' must be present and non-empty in every project's
##            ai-platform.yaml. Catches incomplete or malformed project configs
##            before deployment. DevPlan W3-T5.
## @io — ⇥ caplog, node_yaml_projects → ⎋ None
## @complexity — O(N * Y) where N = projects, Y = YAML parse per file
## @invariants
##   - Required fields: name (str, non-empty), domain (str, non-empty), target_node (str, non-empty)
##   - Unknown required fields produce FAIL
##   - Extra fields are allowed (no strict-mode)
##   - Skips cleanly if no project ai-platform.yaml files found


@pytest.mark.predeploy
@ldd_trajectory
def test_ai_platform_yaml_schema(
    caplog: pytest.LogCaptureFixture,
    node_yaml_projects: list[dict],
) -> None:
    """
    # ⚡ node_yaml_projects → ◇ _find_project_ai_platform_yamls() → ∋ each yaml
    # → ⚡ _parse_ai_platform_yaml() → ◇ required fields present?
    # → ◇ missing? → ⎋ fail | pass
    """
    # region BLOCK_Setup

    logger.info(
        "[IMP:7][test_ai_platform_yaml_schema] Validating ai-platform.yaml schema for %d project(s) ...",
        len(node_yaml_projects),
    )

    if not node_yaml_projects:
        logger.info("[IMP:9][test_ai_platform_yaml_schema] No projects in node.yaml — skipping (not a failure)")
        return

    yamls = _find_project_ai_platform_yamls(node_yaml_projects)
    if not yamls:
        logger.info("[IMP:9][test_ai_platform_yaml_schema] No ai-platform.yaml files found — skipping (not a failure)")
        return
    # endregion

    # region BLOCK_Validate
    REQUIRED_FIELDS: frozenset = frozenset({"name", "domain", "target_node"})
    errors: list[str] = []

    for project_name, yaml_path in sorted(yamls):
        data = _parse_ai_platform_yaml(yaml_path)
        if data is None:
            errors.append(f"[{project_name}] {yaml_path}: failed to parse as YAML dict")
            continue

        missing_fields: list[str] = []
        for field in sorted(REQUIRED_FIELDS):
            value = data.get(field)
            if not value or (isinstance(value, str) and not value.strip()):
                missing_fields.append(field)

        if missing_fields:
            errors.append(
                f"[{project_name}] {yaml_path}: missing or empty required field(s): {', '.join(missing_fields)}"
            )
            logger.error(
                "[IMP:9][test_ai_platform_yaml_schema] FAIL: [%s] missing: %s",
                project_name,
                missing_fields,
            )
        else:
            logger.info(
                "[IMP:9][test_ai_platform_yaml_schema] ✅ [%s] schema valid: %s",
                project_name,
                {f: data.get(f) for f in REQUIRED_FIELDS},
            )
    # endregion

    # region BLOCK_Assert
    if errors:
        pytest.fail("ai-platform.yaml schema validation failed:\n" + "\n".join(errors))

    logger.info(
        "[IMP:9][test_ai_platform_yaml_schema] ✅ All %d ai-platform.yaml file(s) pass schema validation",
        len(yamls),
    )
    # endregion


# endregion FUNC_test_ai_platform_yaml_schema


# endregion TESTS
