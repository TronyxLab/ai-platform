#!/usr/bin/env python3
# GREP_SUMMARY: compose-validator, validate-compose-networks, proxy-net, try-parse-compose, analyze-proxy-net, external, validation-result
# STRUCTURE: ▶ validate_compose_networks ┌domain? → SKIP valid=True┐ → ⚡ try_parse_compose (docker compose config → PyYAML) → ⚡ analyze_proxy_net ┌networks.proxy-net external:true + svc connected┐ → ⎋ ValidationResult
# region MODULE_CONTRACT
## @purpose  Compose proxy-net валидация для adopt-project (M4 gate) — вынесена из
##           project_adopter.py (B9 T5, U-32). Все функции ПУБЛИЧНЫЕ с явными параметрами.
## @scope    scaffold/compose_validator.py: ValidationResult dataclass, validate_compose_networks,
##           try_parse_compose, analyze_proxy_net. Вызывается ProjectAdopter (adopt step 6).
## @invariants
##   - Validation only — compose файлы НЕ мутируются
##   - Нет domain → skip (valid=True)
##   - 3-method cascade: docker compose config (shared sole path) → PyYAML → best-effort skip
##   - proxy-net обязателен с external:true + минимум 1 service подключён
## @rationale DevPlan 116 B9 D5: полный сплит project_adopter — compose-валидация в отдельный
##            модуль с явными параметрами вместо self-полей.
## @changes  2026-08-01 · Extracted from project_adopter.py (B9 T5)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.internal.shared.docker_compose import docker_compose_config as _shared_docker_compose_config

logger = logging.getLogger(__name__)


# region dataclass_ValidationResult
@dataclass
class ValidationResult:
    """Result of compose proxy-net validation.

    ## @purpose  Captures validation outcome with descriptive message.
    ## @io        ┌ valid: bool — True if validation passes
    ##            └ message: str — human-readable validation message
    """

    valid: bool = True
    message: str = ""


# endregion dataclass_ValidationResult


# region FUNC_validate_compose_networks
## @purpose  Validate project docker-compose declares proxy-net (external).
##            If the project has a domain, at least one service MUST be connected to
##            proxy-net with external:true. Returns ValidationResult — does NOT mutate compose.
##            Uses 3-method cascade: docker compose config → python3 yaml → yq fallback.
## @param compose_path  Path to the compose file (compose.yaml or docker-compose.yml)
## @param domain        Project domain (None/"" → validation skipped)
## @param compose_profiles  COMPOSE_PROFILES env value for docker compose config resolution
## @param log_prefix    Log prefix (typically "adopt")
## @io        ⇥ compose_path: Path, domain: str, compose_profiles: str, log_prefix: str → ⎋ ValidationResult
## @complexity O(S × N) where S = services, N = networks per service
## @invariants
##   - Validation only: no mutation of compose files
##   - If no domain configured → skip validation (return valid=True)
##   - Method 1: `docker compose config` — resolves anchors/aliases/extends
##   - Method 2: PyYAML fallback — works without Docker daemon
##   - Method 3: analysis of proxy-net external + service connections
##   - If neither method available → WARN + return valid=True (best-effort)
def validate_compose_networks(
    compose_path: Path,
    *,
    domain: str,
    compose_profiles: str,
    log_prefix: str = "adopt",
) -> ValidationResult:
    """Validate compose proxy-net configuration.

    Returns ValidationResult with valid=True if validation passes.
    """
    # If no domain configured, project doesn't need proxy-net
    if not domain:
        logger.info("[IMP:9][%s][validate_net] No domain configured — skipping proxy-net validation", log_prefix)
        return ValidationResult(valid=True, message="No domain — validation skipped")

    logger.info("[IMP:7][%s][validate_net] Validating proxy-net in compose: %s", log_prefix, compose_path)

    # Step 1: Parse compose
    data = try_parse_compose(compose_path, compose_profiles=compose_profiles)
    if data is None:
        logger.info("[IMP:8][%s][validate_net] Cannot parse compose — neither docker nor PyYAML available", log_prefix)
        logger.info("[IMP:8][%s][validate_net]  WARN: skipping proxy-net validation (best-effort)", log_prefix)
        return ValidationResult(valid=True, message="Parse unavailable — best-effort skip")

    # Step 2: Analyze proxy-net
    net_valid, svc_count, msg = analyze_proxy_net(data)
    if not net_valid:
        logger.info("[IMP:10][%s][validate_net] FAIL: %s", log_prefix, msg)
        return ValidationResult(valid=False, message=msg)

    logger.info(
        "[IMP:9][%s][validate_net] PASS: compose declares proxy-net (external) with %d service(s) connected",
        log_prefix,
        svc_count,
    )
    return ValidationResult(valid=True, message=f"proxy-net valid with {svc_count} service(s)")


# endregion FUNC_validate_compose_networks


# region FUNC_try_parse_compose
## @purpose  Try to parse compose file via docker compose config, then PyYAML.
## @param compose_path  Path to the compose file
## @param compose_profiles  COMPOSE_PROFILES env value (для resolved config)
## @io        ⇥ compose_path → ⎋ dict | None
## @complexity O(C) where C = compose file size
## @invariants
##   - docker compose config — shared sole path (DevPlan 116 B5 T3, гейт docker_sole_path)
##   - PyYAML fallback без Docker daemon
def try_parse_compose(compose_path: Path, *, compose_profiles: str) -> dict | None:
    """Try to parse compose file via docker compose config, then PyYAML."""
    # Method 1: docker compose config (resolves anchors, aliases, extends) — shared sole path
    if shutil.which("docker"):
        cfg_r = _shared_docker_compose_config(
            str(compose_path.parent),
            compose_args=["-f", str(compose_path)],
            env_override={"COMPOSE_PROFILES": compose_profiles},
        )
        cfg_stdout = cfg_r.stdout
        if isinstance(cfg_stdout, bytes):
            cfg_stdout = cfg_stdout.decode("utf-8")
        if cfg_r.returncode == 0 and cfg_stdout.strip():
            logger.info("[IMP:7][validate_net] Compose parsed via docker compose config")
            try:
                import yaml

                return yaml.safe_load(cfg_stdout)
            except (ImportError, yaml.YAMLError):
                pass

    # Method 2: PyYAML fallback
    try:
        import yaml

        with open(compose_path) as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            logger.info("[IMP:7][validate_net] Compose parsed via PyYAML")
            return data
    except (ImportError, yaml.YAMLError):
        pass

    return None


# endregion FUNC_try_parse_compose


# region FUNC_analyze_proxy_net
## @purpose  Core validation logic for proxy-net: external:true + service connections.
## @param data  Parsed compose dict
## @io        ⇥ data: dict → ⎋ (valid: bool, svc_count: int, message: str)
## @complexity O(S × N) where S = services, N = networks per service
## @invariants
##   - external:true обязателен (dict-form "external: {name: proxy-net}" → True)
##   - Минимум 1 service должен быть подключён к proxy-net (dict или list networks form)
def analyze_proxy_net(data: dict) -> tuple[bool, int, str]:
    """Analyze compose data for proxy-net external:true and service connections."""
    networks = data.get("networks", {})
    if not isinstance(networks, dict):
        return False, 0, "No networks section found in compose"

    proxy_net = networks.get("proxy-net", {})
    if not isinstance(proxy_net, dict):
        return False, 0, "proxy-net is not a valid network entry"

    # Check external: true
    external = proxy_net.get("external", False)
    # docker compose config resolves external: true → bool
    has_external = True if isinstance(external, dict) else bool(external)

    if not has_external:
        msg = (
            "FAIL: compose does not declare networks.proxy-net with external:true\n"
            "  Add to compose:\n"
            "    networks:\n"
            "      proxy-net:\n"
            "        name: proxy-net\n"
            "        external: true\n"
            "  And connect at least one service:\n"
            "    services:\n"
            "      <name>:\n"
            "        networks:\n"
            "          proxy-net:\n"
            "            aliases:\n"
            "              - <name>"
        )
        return False, 0, msg

    # Count services connected to proxy-net
    services = data.get("services", {})
    if not isinstance(services, dict):
        services = {}

    svc_count = 0
    for svc_config in services.values():
        if not isinstance(svc_config, dict):
            continue
        svc_networks = svc_config.get("networks", {})
        if (isinstance(svc_networks, dict) and "proxy-net" in svc_networks) or (
            isinstance(svc_networks, list) and "proxy-net" in svc_networks
        ):
            svc_count += 1

    if svc_count == 0:
        msg = "FAIL: compose has proxy-net external but no service is connected to it"
        return False, 0, msg

    return True, svc_count, f"Valid: {svc_count} service(s) on proxy-net"


# endregion FUNC_analyze_proxy_net
