# GREP_SUMMARY: gate project-compose validate docker-compose.yml ports proxy-net alias env_file
# STRUCTURE: ┌validate_project_compose┐ → ◇ check ports absent ∋ any service.ports → ⊕ [err] → ◇ check proxy-net+alias ∋ all services no proxy-net or no aliases → ⊕ [err] → ◇ check env_file ∋ no service.env_file == .env.platform → ⊕ [err] → ⎋ [errors]
# region MODULE_CONTRACT
## @purpose — Gate test: validate project-level docker-compose.yml for platform invariants
## @scope — Parses a project docker-compose.yml (Path via tmp_path), validates:
##          1. No service exposes `ports:` (must use proxy-net)
##          2. At least one service has `networks.proxy-net.aliases` (deterministic hostname)
##          3. At least one service has `env_file: .env.platform` (platform env injection)
## @invariants
##   - Validation is purely static YAML analysis — no Docker daemon required
##   - validate_project_compose returns list of error strings (empty = valid)
##   - Each test creates its own compose file via tmp_path (Zero Hardcode Rule)
## @rationale — Fail-fast at developer machine (`make gate MODE=fast`) catches project-compose
##              drift before deploy, identical to check-compose-spec pre-commit paradigm.
##              See DevPlan §Design Decisions #3.
## @changes — 2026-07-18 | Created per TASK-4 (DevPlan 001)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)


# region FUNC_validate_project_compose
## @purpose — Static validation of project docker-compose.yml against platform invariants
## @io — ⇥ compose_path: Path to docker-compose.yml → ⎋ list[str]: error messages (empty = valid)
## @complexity — O(S × N) where S = services, N = networks per service
## @invariants
##   - Returns [] on valid compose
##   - Returns list of descriptive error messages on violation
##   - Input file must be valid YAML (parses via yaml.safe_load)
##   - Empty compose file (no services) is treated as invalid (no proxy-net alias)
## @rationale — Separate function enables direct unit testing without pytest fixture overhead
def validate_project_compose(compose_path: Path) -> list[str]:
    """Validate project docker-compose.yml against platform invariants.

    Returns empty list if all checks pass. Returns one or more error strings otherwise.
    """
    errors: list[str] = []

    with open(compose_path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error("[IMP:9][validate] YAML parse error: %s", e)
            return [f"Invalid YAML: {e}"]

    if not isinstance(data, dict):
        return ["Compose file is empty or not a mapping"]

    services = data.get("services", {})
    if not isinstance(services, dict):
        return ["Compose file has no 'services' section"]

    logger.info("[IMP:7][validate] Checking %d service(s) in %s", len(services), compose_path)

    # ── Check 1: No ports published ──────────────────────────────────────────
    services_with_ports = []
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        if "ports" in svc_config:
            services_with_ports.append(svc_name)
            logger.info("[IMP:8][validate] FAIL: service '%s' has ports: %s", svc_name, svc_config["ports"])

    if services_with_ports:
        msg = f"Ports published in service(s): {', '.join(services_with_ports)} — use proxy-net instead"
        errors.append(msg)
        logger.info("[IMP:9][validate] %s", msg)
    else:
        logger.info("[IMP:9][validate] PASS: No services expose ports")

    # ── Check 2: proxy-net with alias ────────────────────────────────────────
    has_proxy_alias = False
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        networks = svc_config.get("networks", {})
        if not isinstance(networks, dict):
            continue
        proxy_net = networks.get("proxy-net", {})
        if isinstance(proxy_net, dict):
            aliases = proxy_net.get("aliases", [])
            if isinstance(aliases, list) and len(aliases) > 0 and all(isinstance(a, str) and a for a in aliases):
                has_proxy_alias = True
                logger.info("[IMP:8][validate] PASS: service '%s' has proxy-net alias(es): %s", svc_name, aliases)
                break

    if not has_proxy_alias:
        msg = "No service has 'networks.proxy-net.aliases' — add at least one alias for deterministic hostname"
        errors.append(msg)
        logger.info("[IMP:9][validate] %s", msg)
    else:
        logger.info("[IMP:9][validate] PASS: proxy-net with alias found")

    # ── Check 3: env_file .env.platform ──────────────────────────────────────
    has_env_platform = False
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        env_file = svc_config.get("env_file")
        if isinstance(env_file, str) and env_file == ".env.platform":
            has_env_platform = True
            logger.info("[IMP:8][validate] PASS: service '%s' has env_file: .env.platform", svc_name)
            break
        # env_file can also be a list in docker-compose spec
        if isinstance(env_file, list) and ".env.platform" in env_file:
            has_env_platform = True
            logger.info("[IMP:8][validate] PASS: service '%s' has env_file list containing .env.platform", svc_name)
            break

    if not has_env_platform:
        msg = "No service has 'env_file: .env.platform' — platform environment injection required"
        errors.append(msg)
        logger.info("[IMP:9][validate] %s", msg)
    else:
        logger.info("[IMP:9][validate] PASS: env_file .env.platform found")

    return errors


# endregion FUNC_validate_project_compose


# region FUNC_helpers
## @purpose — Factory functions for test compose YAML files


def _make_invalid_ports(compose_dir: Path) -> Path:
    """Create a compose file with `ports:` in a service."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "ports": ["80:80"],
                "networks": {"proxy-net": {"aliases": ["myapp"]}},
                "env_file": ".env.platform",
            }
        },
        "networks": {
            "proxy-net": {"external": True, "name": "proxy-net"},
        },
    }
    path = compose_dir / "docker-compose.yml"
    with open(path, "w") as f:
        yaml.dump(compose_data, f)
    return path


def _make_valid_compose(compose_dir: Path) -> Path:
    """Create a valid compose file meeting all platform invariants."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "networks": {"proxy-net": {"aliases": ["myapp"]}},
                "env_file": ".env.platform",
            }
        },
        "networks": {
            "proxy-net": {"external": True, "name": "proxy-net"},
        },
    }
    path = compose_dir / "docker-compose.yml"
    with open(path, "w") as f:
        yaml.dump(compose_data, f)
    return path


def _make_no_alias_compose(compose_dir: Path) -> Path:
    """Create a compose file missing proxy-net alias."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "networks": {"proxy-net": None},
                "env_file": ".env.platform",
            }
        },
        "networks": {
            "proxy-net": {"external": True, "name": "proxy-net"},
        },
    }
    path = compose_dir / "docker-compose.yml"
    with open(path, "w") as f:
        yaml.dump(compose_data, f)
    return path


def _make_no_env_compose(compose_dir: Path) -> Path:
    """Create a compose file missing env_file: .env.platform."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "networks": {"proxy-net": {"aliases": ["myapp"]}},
            }
        },
        "networks": {
            "proxy-net": {"external": True, "name": "proxy-net"},
        },
    }
    path = compose_dir / "docker-compose.yml"
    with open(path, "w") as f:
        yaml.dump(compose_data, f)
    return path


# endregion FUNC_helpers


# region TESTS
## @purpose — 4 atomic gate tests for project docker-compose.yml validation
## @scope — Each test creates its own compose file via tmp_path, calls validate_project_compose, checks result
## @usecases — AC-4: make gate MODE=fast FAIL on ports / missing proxy-net alias / missing env_file


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — `ports:` blocks proxy-net usage
# · Last fail: N/A (preventive)
# · Remove if: project-compose contract is superseded by a newer mechanism
def test_no_ports_published(caplog, tmp_path):
    """Project compose with `ports:` should fail validation."""
    logger.info("[IMP:7][test_no_ports_published] Creating invalid compose with ports section")
    compose_path = _make_invalid_ports(tmp_path)

    errors = validate_project_compose(compose_path)

    # Assert that "ports" is detected
    port_error_found = any("ports" in e.lower() for e in errors)
    assert port_error_found, f"[IMP:9][test_no_ports_published] FAIL: Expected ports violation, got errors: {errors}"
    logger.info("[IMP:9][test_no_ports_published] PASS: ports violation correctly detected — errors: %s", errors)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — proxy-net alias required for deterministic hostname
# · Last fail: N/A (preventive)
# · Remove if: proxy-net contract is superseded
def test_proxy_net_with_alias(caplog, tmp_path):
    """Project compose without proxy-net alias should fail validation."""
    logger.info("[IMP:7][test_proxy_net_with_alias] Creating compose without proxy-net alias")
    compose_path = _make_no_alias_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    alias_error_found = any("alias" in e.lower() for e in errors)
    assert alias_error_found, (
        f"[IMP:9][test_proxy_net_with_alias] FAIL: Expected proxy-net alias violation, got errors: {errors}"
    )
    logger.info("[IMP:9][test_proxy_net_with_alias] PASS: missing alias correctly detected — errors: %s", errors)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — env_file .env.platform required for platform env injection
# · Last fail: N/A (preventive)
# · Remove if: platform env injection mechanism changes
def test_env_file_platform_present(caplog, tmp_path):
    """Project compose without env_file: .env.platform should fail validation."""
    logger.info("[IMP:7][test_env_file_platform_present] Creating compose without .env.platform")
    compose_path = _make_no_env_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    env_error_found = any("env_file" in e.lower() or ".env.platform" in e for e in errors)
    assert env_error_found, (
        f"[IMP:9][test_env_file_platform_present] FAIL: Expected env_file violation, got errors: {errors}"
    )
    logger.info(
        "[IMP:9][test_env_file_platform_present] PASS: missing env_file correctly detected — errors: %s",
        errors,
    )


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — valid template compose must pass all checks
# · Last fail: N/A (preventive)
# · Remove if: project-compose contract is superseded
def test_valid_project_passes(caplog, tmp_path):
    """Valid project compose (template-style) should pass validation."""
    logger.info("[IMP:7][test_valid_project_passes] Creating valid template-style compose")
    compose_path = _make_valid_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    assert len(errors) == 0, f"[IMP:9][test_valid_project_passes] FAIL: Expected no errors, got: {errors}"
    logger.info("[IMP:9][test_valid_project_passes] PASS: valid compose produces no errors")


# endregion TESTS
