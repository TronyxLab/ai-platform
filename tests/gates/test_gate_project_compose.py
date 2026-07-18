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

    # ── Check 2: proxy-net declared as external network ──────────────────────
    compose_networks = data.get("networks", {})
    if not isinstance(compose_networks, dict):
        msg = "Compose file has no 'networks' section — proxy-net external required"
        errors.append(msg)
        logger.info("[IMP:9][validate] %s", msg)
    else:
        proxy_net_decl = compose_networks.get("proxy-net", None)
        if proxy_net_decl is None:
            msg = "Compose does not declare 'networks.proxy-net' — add proxy-net: { external: true, name: proxy-net }"
            errors.append(msg)
            logger.info("[IMP:9][validate] %s", msg)
        elif isinstance(proxy_net_decl, dict):
            external_val = proxy_net_decl.get("external", False)
            # external: true OR external: { name: proxy-net } with name check
            if external_val is True or (isinstance(external_val, dict) and external_val.get("name") == "proxy-net"):
                logger.info("[IMP:9][validate] PASS: proxy-net declared as external network")
            else:
                msg = (
                    "proxy-net must be external — current: "
                    + str(proxy_net_decl)
                    + " — set 'proxy-net: { external: true, name: proxy-net }'"
                )
                errors.append(msg)
                logger.info("[IMP:9][validate] %s", msg)

    # ── Check 3: proxy-net with alias ────────────────────────────────────────
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

    # ── Check 4: env_file .env.platform ──────────────────────────────────────
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


def _make_no_proxy_net_compose(compose_dir: Path) -> Path:
    """Create a compose file missing proxy-net entirely (negative fixture for M4 gate)."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "env_file": ".env.platform",
                "networks": {"app-net": {"aliases": ["myapp"]}},
            }
        },
        "networks": {
            "app-net": {"driver": "bridge"},
        },
    }
    path = compose_dir / "docker-compose.yml"
    with open(path, "w") as f:
        yaml.dump(compose_data, f)
    return path


def _make_proxy_net_not_external_compose(compose_dir: Path) -> Path:
    """Create a compose file where proxy-net is not external (negative fixture)."""
    compose_data = {
        "version": "3.8",
        "services": {
            "web": {
                "image": "nginx:alpine",
                "env_file": ".env.platform",
                "networks": {"proxy-net": {"aliases": ["myapp"]}},
            }
        },
        "networks": {
            "proxy-net": {"driver": "bridge"},
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


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · M4 gate — proxy-net external declaration required
# · Last fail: N/A (preventive)
# · Remove if: proxy-net contract is superseded
def test_proxy_net_external_declared(caplog, tmp_path):
    """Compose without proxy-net network declaration should fail validation."""
    logger.info("[IMP:7][test_proxy_net_external_declared] Creating compose without proxy-net")
    compose_path = _make_no_proxy_net_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    proxy_net_error = any("proxy-net" in e.lower() for e in errors)
    assert proxy_net_error, (
        f"[IMP:9][test_proxy_net_external_declared] FAIL: Expected proxy-net violation, got errors: {errors}"
    )
    logger.info("[IMP:9][test_proxy_net_external_declared] PASS: missing proxy-net correctly detected — errors: %s", errors)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · M4 gate — proxy-net must be external
# · Last fail: N/A (preventive)
# · Remove if: external network contract changes
def test_proxy_net_is_external(caplog, tmp_path):
    """Compose with proxy-net that is not external should fail validation."""
    logger.info("[IMP:7][test_proxy_net_is_external] Creating compose with non-external proxy-net")
    compose_path = _make_proxy_net_not_external_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    external_error = any("external" in e.lower() for e in errors)
    assert external_error, (
        f"[IMP:9][test_proxy_net_is_external] FAIL: Expected external violation, got errors: {errors}"
    )
    logger.info("[IMP:9][test_proxy_net_is_external] PASS: non-external proxy-net correctly detected — errors: %s", errors)


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · M4 gate — all template compose files must declare proxy-net external
# · Last fail: N/A (preventive)
# · Remove if: template contracts are superseded
def test_templates_declare_proxy_net(caplog):
    """All template docker-compose.yml files must declare proxy-net as external.

    Template files use {{...}} Jinja-style placeholders and are NOT valid YAML.
    This test uses grep-based string scanning to verify proxy-net external presence.
    """
    templates_dir = Path(__file__).parent.parent.parent / "templates"
    if not templates_dir.is_dir():
        pytest.skip("templates/ directory not found — running outside project root")
        return

    template_compose_files = list(templates_dir.glob("*/docker-compose.yml"))
    assert len(template_compose_files) > 0, (
        f"[IMP:9][test_templates_declare_proxy_net] No template compose files found in {templates_dir}"
    )

    logger.info(
        "[IMP:7][test_templates_declare_proxy_net] Validating %d template compose files",
        len(template_compose_files),
    )

    all_pass = True
    for compose_path in template_compose_files:
        logger.info("[IMP:7][test_templates_declare_proxy_net] Checking: %s", compose_path.name)
        content = compose_path.read_text()

        # Check 1: proxy-net declared in networks section
        has_proxy_net_decl = False
        in_networks = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("networks:"):
                in_networks = True
                continue
            if in_networks:
                if stripped.startswith("proxy-net:"):
                    has_proxy_net_decl = True
                    break
                # Non-empty line with less indentation = left networks section
                if stripped and not stripped.startswith("#") and not line.startswith(" ") and not line.startswith("\t"):
                    break

        # Check 2: external: true under proxy-net (indentation-aware)
        has_external_true = False
        proxy_net_indent = -1
        for line in content.splitlines():
            stripped = line.strip()
            # Skip empty/comments
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("proxy-net:"):
                proxy_net_indent = len(line) - len(line.lstrip())
                continue
            if proxy_net_indent >= 0:
                current_indent = len(line) - len(line.lstrip())
                # Same or less indentation = left proxy-net section
                if current_indent <= proxy_net_indent:
                    proxy_net_indent = -1
                    continue
                if stripped.startswith("external:"):
                    val = stripped.split(":", 1)[1].strip().split()[0] if stripped.split(":", 1)[1].strip() else ""
                    if val in ("true", "True"):
                        has_external_true = True
                        break
                    else:
                        logger.info(
                            "[IMP:8][test_templates_declare_proxy_net] %s: proxy-net.external=%s (not true)",
                            compose_path.name,
                            val,
                        )

        if not has_proxy_net_decl:
            logger.info(
                "[IMP:9][test_templates_declare_proxy_net] FAIL: %s — missing 'proxy-net:' in networks section",
                compose_path.name,
            )
            all_pass = False
        elif not has_external_true:
            logger.info(
                "[IMP:9][test_templates_declare_proxy_net] FAIL: %s — proxy-net exists but not external: true",
                compose_path.name,
            )
            all_pass = False
        else:
            logger.info("[IMP:9][test_templates_declare_proxy_net] PASS: %s", compose_path.name)

    assert all_pass, (
        "[IMP:9][test_templates_declare_proxy_net] FAIL: One or more template compose files failed validation"
    )
    logger.info("[IMP:9][test_templates_declare_proxy_net] PASS: All template compose files declare proxy-net external")


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · M4 negative — compose without proxy-net must fail gate
# · Last fail: N/A (preventive, Test Honesty R5 negative pair for M4 gate)
# · Remove if: proxy-net requirement is removed
def test_negative_compose_without_proxy_net(caplog, tmp_path):
    """Negative fixture: compose without proxy-net → gate fails (M4 regression guard).

    Test Honesty R5: negative pair for M4 proxy-net gate. If gate does not fail
    on this input, the gate is incomplete.
    """
    logger.info("[IMP:7][test_negative_compose_without_proxy_net] Creating negative compose without proxy-net")
    compose_path = _make_no_proxy_net_compose(tmp_path)

    errors = validate_project_compose(compose_path)

    # Gate MUST detect missing proxy-net — test fails if it doesn't
    proxy_net_missing = any("proxy-net" in e.lower() for e in errors)
    assert proxy_net_missing, (
        f"[IMP:9][test_negative_compose_without_proxy_net] CRITICAL: Gate did NOT detect missing proxy-net — "
        f"gate is incomplete (Test Honesty R5 violation). Errors: {errors}"
    )
    logger.info(
        "[IMP:9][test_negative_compose_without_proxy_net] PASS: Negative fixture correctly triggers gate — errors: %s",
        errors,
    )


# endregion TESTS
