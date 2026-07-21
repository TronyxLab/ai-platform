# GREP_SUMMARY: gate env-shared NO_PROXY proxy opt-in declare→inject consistency module.yaml env_shared base.yml platform-env
# STRUCTURE: ┌loaders (SoT, module.yaml, compose)┐ → ◇ test_env_shared_vars_injected_in_compose (forward) → ◇ test_proxy_vars_are_opt_in (reverse) → ◇ test_no_hardcoded_noproxy_in_base_yml → ◇ test_env_noproxy_covers_internal_services → ⊕ LDD summary per test
# region MODULE_CONTRACT
## @purpose — Gate test T8.5: validate «declare → inject» contract for env_shared + proxy opt-in
## @scope — Four aspects of env_shared consistency:
##          1. Forward: every env_shared var from module.yaml is injected in docker-compose.base.yml
##          2. Reverse (proxy): HTTP_PROXY/HTTPS_PROXY/NO_PROXY in compose ⇒ declared in env_shared
##             AND module ∈ platform-env.yaml proxy.consumers
##          3. NO_PROXY in base.yml uses ${NO_PROXY} variable (not hardcoded list)
##          4. .env.example (always) and .env (if exists) ⊇ proxy.no_proxy_internal
## @invariants
##   - env_shared is optional in module.schema.json — modules without it are valid
##   - Proxy vars (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) are opt-in: only proxy.consumers may declare them
##   - NO_PROXY in base.yml must reference ${NO_PROXY} variable, not hardcoded fallback list (test 3)
##   - .env.example must cover all internal services from SoT; .env checked only if exists (CI-safe)
## @rationale — DRIFT-A remediation (VerificationReport 015): NO_PROXY was fragmented across 12 module.yaml
##              and 3 base.yml files. New contract: platform-env.yaml proxy section is the SoT;
##              gate validates that runtime (compose, .env, .env.example) converges to SoT.
## @changes — 2026-07-16 | DevPlan 016: replaced 2 old tests with 4 new tests (forward+reverse+hardcode+SoT)
##            Old tests removed: test_noproxy_consistent_across_modules, test_all_base_yml_reference_noproxy
# endregion MODULE_CONTRACT

import logging
import os
import pathlib

import pytest
import yaml

from tests._conftest.audit import discover_docker_modules
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULES_DIR = os.path.join(ROOT_DIR, "core", "modules")
PLATFORM_ENV_PATH = os.path.join(ROOT_DIR, "platform-env.yaml")
ENV_EXAMPLE_PATH = os.path.join(ROOT_DIR, ".env.example")
ENV_DOT_PATH = os.path.join(ROOT_DIR, ".env")

PROXY_VARS = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}


# region LOADERS


def _load_platform_env() -> dict:
    """Load and return the full platform-env.yaml as a dict."""
    with open(PLATFORM_ENV_PATH) as f:
        return yaml.safe_load(f)


def _load_module_yaml(module_name: str) -> dict | None:
    """Load a module's module.yaml, return None if missing."""
    path = os.path.join(MODULES_DIR, module_name, "module.yaml")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def _load_compose_yaml(module_name: str) -> dict | None:
    """Load a module's docker-compose.base.yml, return None if missing."""
    path = os.path.join(MODULES_DIR, module_name, "docker-compose.base.yml")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def _get_env_shared_vars(module_yaml: dict) -> dict[str, str]:
    """Extract env_shared block from module.yaml — returns {} if absent."""
    return module_yaml.get("env_shared", {}) or {}


def _get_compose_env_vars(compose_yaml: dict) -> dict[str, str]:
    """Aggregate all environment variables from all services in compose YAML.

    Returns a flat dict of {var_name: var_value_or_template}.
    Supports both dict-style and list-style environment declarations.
    """
    env: dict[str, str] = {}
    services = compose_yaml.get("services", {}) if isinstance(compose_yaml, dict) else {}
    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        raw_env = svc_config.get("environment", {})
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                env[k] = str(v) if v is not None else ""
        elif isinstance(raw_env, list):
            for item in raw_env:
                if isinstance(item, str) and "=" in item:
                    key, _, val = item.partition("=")
                    env[key.strip()] = val.strip()
    return env


def _load_file_lines(filepath: str) -> list[str] | None:
    """Read file, return lines list, or None if file doesn't exist."""
    if not os.path.isfile(filepath):
        return None
    with open(filepath) as f:
        return f.readlines()


def _check_env_shared_consistency(module_yaml_paths: list[pathlib.Path]) -> list[str]:
    """Check for divergent env_shared declarations between modules.

    Takes a list of Path objects to module.yaml files.
    Returns list of divergence messages (empty = consistent).
    Used by _negative companion test to verify detection of divergent env_shared.
    """
    divergences: list[str] = []
    all_vars: dict[str, dict[str, str]] = {}

    for yaml_path in module_yaml_paths:
        module_name = yaml_path.parent.name
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        env_shared = data.get("env_shared", {}) or {}
        for key, value in env_shared.items():
            if key not in all_vars:
                all_vars[key] = {}
            all_vars[key][module_name] = str(value)

    for key, modules_dict in all_vars.items():
        if len(modules_dict) > 1:
            values = set(modules_dict.values())
            if len(values) > 1:
                details = "; ".join(f"{m}={v}" for m, v in modules_dict.items())
                divergences.append(f"SHARED_VAR '{key}' diverges across modules: {details}")

    return divergences


# endregion LOADERS


# ── Test 1: Forward — declare → inject ────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_env_shared_vars_injected_in_compose(caplog) -> None:
    """Forward-контракт: каждая переменная env_shared из module.yaml присутствует
    как ${VAR_NAME} в environment docker-compose.base.yml того же модуля.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][gate][forward] START: env_shared vars → compose injection")

        modules = discover_docker_modules(MODULES_DIR)
        violations: list[str] = []

        for mod in sorted(modules):
            my = _load_module_yaml(mod)
            if my is None:
                continue
            env_shared = _get_env_shared_vars(my)
            if not env_shared:
                logger.info("[IMP:7][gate][forward] %s: no env_shared — skip", mod)
                continue

            cy = _load_compose_yaml(mod)
            if cy is None:
                logger.info("[IMP:7][gate][forward] %s: no compose — skip", mod)
                continue

            compose_env = _get_compose_env_vars(cy)
            for var_name in env_shared:
                # Check that `${VAR_NAME` or `${VAR_NAME:-` appears in compose env value
                var_ref = f"${{{var_name}"
                found = False
                for val in compose_env.values():
                    if var_ref in val:
                        found = True
                        break
                if not found:
                    violations.append(f"{mod}: env_shared '{var_name}' → NOT in compose environment")

        logger.critical(
            "[IMP:9][gate][forward] ASSERT: %d violation(s) — declare→inject contract",
            len(violations),
        )
        assert not violations, (
            f"GATE_ENV_SHARED_NOT_INJECTED: {len(violations)} module(s) have env_shared vars "
            f"not referenced in their docker-compose.base.yml:\n  " + "\n  ".join(violations)
        )
        logger.info("[IMP:9][gate][forward] PASS: all env_shared vars are injected in compose")


# ── Test 2: Reverse (proxy) — opt-in ──────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_proxy_vars_are_opt_in(caplog) -> None:
    """Reverse-контракт: HTTP_PROXY/HTTPS_PROXY/NO_PROXY в compose environment
    ⇒ задекларированы в env_shared; множество модулей с прокси-декларацией ==
    platform-env.yaml proxy.consumers.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][gate][reverse] START: proxy vars opt-in check")

        platform_env = _load_platform_env()
        proxy_config = platform_env.get("proxy", {})
        allowed_consumers: set[str] = set(proxy_config.get("consumers", []))
        logger.info("[IMP:7][gate][reverse] proxy.consumers = %s", allowed_consumers)

        modules = discover_docker_modules(MODULES_DIR)
        proxy_declaring_modules: set[str] = set()

        for mod in sorted(modules):
            my = _load_module_yaml(mod)
            if my is None:
                continue
            env_shared = _get_env_shared_vars(my)
            shared_proxy_vars = PROXY_VARS & set(env_shared.keys())
            if shared_proxy_vars:
                proxy_declaring_modules.add(mod)
                logger.info(
                    "[IMP:8][gate][reverse] %s declares proxy vars: %s",
                    mod,
                    sorted(shared_proxy_vars),
                )

        logger.info(
            "[IMP:8][gate][reverse] modules declaring proxy: %s",
            sorted(proxy_declaring_modules),
        )

        # Violation A: module declares proxy but is NOT in consumers
        undeclared_consumers = proxy_declaring_modules - allowed_consumers
        # Violation B: module is in consumers but does NOT declare proxy
        missing_declarations = allowed_consumers - proxy_declaring_modules

        violations: list[str] = [
            f"{mod}: declares proxy vars in env_shared but NOT in platform-env.yaml proxy.consumers"
            for mod in sorted(undeclared_consumers)
        ]
        violations.extend(
            f"{mod}: in platform-env.yaml proxy.consumers but does NOT declare proxy vars in env_shared"
            for mod in sorted(missing_declarations)
        )

        logger.critical(
            "[IMP:9][gate][reverse] ASSERT: %d opt-in violation(s)",
            len(violations),
        )
        assert not violations, (
            f"GATE_PROXY_OPT_IN_FAILED: {len(violations)} proxy opt-in violation(s):\n  " + "\n  ".join(violations)
        )
        logger.info("[IMP:9][gate][reverse] PASS: proxy opt-in contract holds")


# ── Test 3: No hardcoded NO_PROXY in base.yml ─────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_no_hardcoded_noproxy_in_base_yml(caplog) -> None:
    """NO_PROXY в base.yml только через ${NO_PROXY...}, не хардкод-список.

    Логика прежнего test_all_base_yml_reference_noproxy сохранена.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][gate][noproxy_hardcode] START")

        modules = discover_docker_modules(MODULES_DIR)
        violations: list[str] = []

        for mod in sorted(modules):
            cy = _load_compose_yaml(mod)
            if cy is None:
                continue
            compose_env = _get_compose_env_vars(cy)
            for key, val in compose_env.items():
                if key.upper() == "NO_PROXY":
                    # If the value starts with something other than ${, it's hardcoded
                    if not val.strip().startswith("${"):
                        violations.append(f"{mod}: NO_PROXY = '{val}' — appears hardcoded (not ${{NO_PROXY...}} )")
                    else:
                        logger.info("[IMP:8][gate][noproxy_hardcode] %s: uses variable reference", mod)

        logger.critical(
            "[IMP:9][gate][noproxy_hardcode] ASSERT: %d NO_PROXY hardcode violation(s)",
            len(violations),
        )
        assert not violations, (
            f"GATE_NO_PROXY_HARDCODED: {len(violations)} module(s) have hardcoded NO_PROXY:\n  "
            + "\n  ".join(violations)
        )
        logger.info("[IMP:9][gate][noproxy_hardcode] PASS: no hardcoded NO_PROXY values")


# ── Test 4: .env / .env.example ⊇ SoT ─────────────────────────────────────


@pytest.mark.gate
@ldd_trajectory
def test_env_noproxy_covers_internal_services(caplog) -> None:
    """NO_PROXY в .env.example (always) и .env (if exists) ⊇ proxy.no_proxy_internal
    по-элементно (split по запятой).
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][gate][sot_coverage] START: .env* ⊇ SoT no_proxy_internal")

        platform_env = _load_platform_env()
        proxy_config = platform_env.get("proxy", {})
        no_proxy_internal_raw: str = proxy_config.get("no_proxy_internal", "")
        no_proxy_internal_set: set[str] = {entry.strip() for entry in no_proxy_internal_raw.split(",") if entry.strip()}
        logger.info("[IMP:8][gate][sot_coverage] SoT no_proxy_internal = %s", sorted(no_proxy_internal_set))

        # ── Validate .env.example ──
        env_example_lines = _load_file_lines(ENV_EXAMPLE_PATH)
        assert env_example_lines is not None, f".env.example not found at {ENV_EXAMPLE_PATH}"
        example_noproxy = _extract_noproxy_value(env_example_lines)
        assert example_noproxy is not None, ".env.example: NO_PROXY key not found"

        example_set: set[str] = {e.strip() for e in example_noproxy.split(",") if e.strip()}
        example_missing = no_proxy_internal_set - example_set

        logger.critical(
            "[IMP:9][gate][sot_coverage] .env.example: SoT entries missing = %s",
            sorted(example_missing),
        )
        assert not example_missing, (
            f".env.example NO_PROXY missing internal services: {sorted(example_missing)}. "
            f"SoT requires: {sorted(no_proxy_internal_set)}. "
            f".env.example has: {sorted(example_set)}"
        )
        logger.info("[IMP:9][gate][sot_coverage] .env.example PASS — covers all SoT entries")

        # ── Validate .env (if exists — CI-safe) ──
        env_dot_lines = _load_file_lines(ENV_DOT_PATH)
        if env_dot_lines is not None:
            dot_noproxy = _extract_noproxy_value(env_dot_lines)
            if dot_noproxy is not None:
                dot_set: set[str] = {e.strip() for e in dot_noproxy.split(",") if e.strip()}
                dot_missing = no_proxy_internal_set - dot_set

                logger.critical(
                    "[IMP:9][gate][sot_coverage] .env: SoT entries missing = %s",
                    sorted(dot_missing),
                )
                assert not dot_missing, (
                    f".env NO_PROXY missing internal services: {sorted(dot_missing)}. "
                    f"SoT requires: {sorted(no_proxy_internal_set)}. "
                    f".env has: {sorted(dot_set)}"
                )
                logger.info("[IMP:9][gate][sot_coverage] .env PASS — covers all SoT entries")
            else:
                logger.info("[IMP:7][gate][sot_coverage] .env: NO_PROXY key not found — skip")
        else:
            logger.info("[IMP:7][gate][sot_coverage] .env not found — CI-skip (DD-3)")


# ── Helpers ────────────────────────────────────────────────────────────────


def _extract_noproxy_value(lines: list[str]) -> str | None:
    """Extract the NO_PROXY value from env file lines (key=value format).

    Returns the value part after 'NO_PROXY=', or None if not found.
    Handles commented lines and inline comments after value.
    """
    for line in lines:
        stripped = line.strip()
        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("NO_PROXY="):
            value = stripped.split("=", 1)[1]
            # Strip inline comments (but not # in value)
            # Simple heuristic: split on first unquoted '#'
            in_quote = False
            for i, ch in enumerate(value):
                if ch == '"' or ch == "'":
                    in_quote = not in_quote
                elif ch == "#" and not in_quote:
                    value = value[:i]
                    break
            return value.strip().strip('"').strip("'")
    return None
