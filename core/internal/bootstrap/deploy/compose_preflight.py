#!/usr/bin/env python3
"""Docker compose preflight validation — blocks `up` if required secrets are missing or charset-invalid."""
# GREP_SUMMARY: compose-preflight, secrets-validation, docker-compose-wrapper, pre-up-check, missing-secrets, charset-validation
# STRUCTURE: ▶ parse_compose_args → ◇ resolve_modules(profiles) → ◇ load_secrets_manifest → ◇ check_secrets(modules) → ◇ validate_charsets → ⊕ missing|invalid → ⎋ exit(0|1)
# region MODULE_CONTRACT [DOMAIN(DEPLOY): bootstrap; CONCEPT(SECRETS): preflight-validation; TECH(PYTHON): argparse+yaml+re+os]
## @purpose  Docker compose preflight validation wrapper — ensures all required secrets for target modules
##           are present (in os.environ or /run/platform/secrets.env) and pass charset constraints
##           before allowing `docker compose up` to proceed.
## @scope    Called from core/entrypoints/compose-wrapper.sh before `exec docker compose "$@"`.
##           Reads secrets-manifest.yaml, checks modules resolved from compose profiles/args.
## @input    sys.argv — docker compose arguments (profiles via --profile MODULE or default COMPOSE_PROFILES env)
## @output   stdout: one line per missing/invalid secret; exit 0 = allow, exit 1 = block
## @links    REUSES_FROM(core/internal/bootstrap/deploy/secrets_validator.py:_check_env_requires, _validate_secret_charsets)
## @invariants
##   - Modules are resolved from --profile <name> args OR COMPOSE_PROFILES env var OR all manifest consumers
##   - Secrets file path: /run/platform/secrets.env (overridable via SECRETS_ENV_FILE env)
##   - Checks ALL tier ∈ {required, generated} secrets for resolved modules
##   - Charset validation only checks non-empty values (empty = caught by check_secrets)
##   - Missing manifest → WARN + exit 0 (graceful degradation, no SSoT = allow)
## @rationale Prevents silent failures when `docker compose up` is invoked manually without bootstrap.
##            One-time bootstrap runs `docker_orchestrator.py` which validates secrets already,
##            but iterative `docker compose up` during development bypasses that validation.
##            The wrapper is opt-in (compose-safe-up make target), not a global override.
## @changes 2026-07-22 | Initial — TASK-4 of Plan 049 secrets-centralization
## @usecases
##   - `make compose-safe-up MODULES=postgres,litellm` → preflight check → docker compose up
##   - Developer running `docker compose up --profile my-module` directly (bypass): no guard
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import re as _re
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# Default path for secrets.env
_SECRETS_ENV_DEFAULT = "/run/platform/secrets.env"

# Default path for secrets-manifest.yaml (relative to platform root)
_MANIFEST_DEFAULT = os.path.join(
    os.environ.get("PLATFORM_ROOT", "/opt/platform"),
    "core/secrets-manifest.yaml",
)

# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_load_env_map
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  Load key=value pairs from a secrets.env file into a dict
## @io       env_file_path (str) → dict[str, str]
## @complexity 1 — linear file read + partition per line
def load_env_map(env_file_path: str) -> dict[str, str]:
    """Load key=value pairs from an env file (stripping comments and blanks)."""
    logger.info("[IMP:7][load_env_map][start] path=%s", env_file_path)
    env_map: dict[str, str] = {}
    p = Path(env_file_path)
    if not p.is_file():
        logger.info("[IMP:7][load_env_map][missing] File %s not found — returning empty map", env_file_path)
        return env_map

    with p.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                k, _, v = stripped.partition("=")
                env_map[k.strip()] = v.strip()

    logger.info("[IMP:8][load_env_map][loaded] %d vars loaded from %s", len(env_map), env_file_path)
    return env_map


# endregion FUNC_load_env_map


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_parse_compose_args
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  Parse docker compose CLI arguments to extract profiles and compose files.
##           Looks for `--profile <name>` arguments and subcommand (up, down, config, etc.).
## @io       args_list (list[str]) → tuple[set[str], str | None]:
##           (set of profile names, subcommand e.g. "up" or None)
## @complexity 1 — linear scan over argv
## @invariants
##   - Only `--profile <name>` pairs are extracted; `--profile=<name>` is also supported
##   - Subcommand is the first non-option argument (excluding -f, --file, --profile values)
def parse_compose_args(args_list: list[str]) -> tuple[set[str], str | None]:
    """Extract profile names and subcommand from docker compose arguments."""
    logger.info("[IMP:7][parse_compose_args][start] args=%s", args_list)
    profiles: set[str] = set()
    subcommand: str | None = None
    skip_next = False

    for i, arg in enumerate(args_list):
        if skip_next:
            skip_next = False
            continue

        if arg.startswith("--profile="):
            profile_val = arg.split("=", 1)[1]
            if profile_val:
                profiles.add(profile_val)
        elif arg == "--profile" and i + 1 < len(args_list):
            profiles.add(args_list[i + 1])
            skip_next = True
        elif arg in ("-f", "--file"):
            skip_next = True  # skip the file path
        elif not arg.startswith("-") and subcommand is None:
            subcommand = arg

    return profiles, subcommand


# endregion FUNC_parse_compose_args


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_resolve_modules
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  Resolve which modules to check based on profiles from compose args,
##           COMPOSE_PROFILES env var, or fallback to ALL consumers in manifest.
## @io       profiles (set[str]) → set[str] of module names
## @complexity 1 — set union
## @invariants
##   - If profiles are explicitly passed via --profile args, use those
##   - Else if COMPOSE_PROFILES is set, parse it as comma-separated list
##   - Else return empty set (all modules will be checked by caller)
## @rationale Profiles are the canonical filter mechanism in Docker Compose.
##            When no profiles are specified, ALL modules run — so we check ALL.
def resolve_modules(profiles: set[str]) -> set[str]:
    """Resolve target module names from profiles/COMPOSE_PROFILES."""
    if profiles:
        logger.info("[IMP:8][resolve_modules][profiles] Using --profile args: %s", profiles)
        return profiles

    env_profiles = os.environ.get("COMPOSE_PROFILES", "")
    if env_profiles:
        parsed = {m.strip() for m in env_profiles.split(",") if m.strip()}
        logger.info("[IMP:8][resolve_modules][env] Using COMPOSE_PROFILES: %s", parsed)
        return parsed

    logger.info("[IMP:7][resolve_modules][all] No profiles specified — will check all modules")
    return set()


# endregion FUNC_resolve_modules


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_load_manifest
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  Load secrets-manifest.yaml and return the secrets list.
## @io       manifest_path (str) → list[dict] | None
## @complexity 1 — single YAML parse
def load_manifest(manifest_path: str) -> list[dict] | None:
    """Load secrets-manifest.yaml and return secrets list. Returns None if absent."""
    logger.info("[IMP:7][load_manifest][start] path=%s", manifest_path)
    p = Path(manifest_path)
    if not p.is_file():
        logger.warning("[IMP:5][load_manifest][missing] Manifest %s not found — graceful degradation", manifest_path)
        return None

    with p.open() as f:
        data = yaml.safe_load(f)

    if data is None:
        logger.warning("[IMP:5][load_manifest][empty] Manifest %s is empty", manifest_path)
        return None

    secrets_list = data.get("secrets", [])
    logger.info("[IMP:8][load_manifest][loaded] %d secrets entries loaded", len(secrets_list))
    return secrets_list


# endregion FUNC_load_manifest


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_check_secrets
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  For each module in target_modules, check all required/generated secrets are non-empty.
##           Combines os.environ with env_file_map (precedence: os.environ wins).
## @io       target_modules (set[str]), secrets_list (list[dict]), env_file_map (dict[str,str])
##           → list[str] of missing variable names
## @complexity 2 — O(M*S) where M=module count, S=secrets per module
## @invariants
##   - Only checks secrets tier ∈ {required, generated}
##   - Empty target_modules → checks ALL modules in manifest
##   - os.environ takes precedence over env_file_map
def check_secrets(target_modules: set[str], secrets_list: list[dict], env_file_map: dict[str, str]) -> list[str]:
    """Check that all required/generated secrets for target modules are set."""
    logger.info(
        "[IMP:7][check_secrets][start] modules=%s, secrets_count=%d", target_modules or "ALL", len(secrets_list)
    )

    missing: list[str] = []

    for s in secrets_list:
        name: str = s.get("name", "")
        tier: str = s.get("tier", "")
        consumers: list = s.get("consumers", [])

        # Only check required and generated tiers
        if tier not in ("required", "generated"):
            continue

        # Check if this secret is consumed by any target module
        if target_modules and not any(m in consumers for m in target_modules):
            continue

        # Check availability: os.environ first, then env_file_map
        env_val = os.environ.get(name, "")
        if not env_val:
            env_val = env_file_map.get(name, "")

        if not env_val:
            missing.append(name)
            logger.info("[IMP:8][check_secrets][missing] %s is empty for modules %s", name, consumers)
        else:
            logger.info("[IMP:8][check_secrets][ok] %s is present", name)

    if missing:
        logger.warning("[IMP:9][check_secrets][FAIL] Missing secrets: %s", missing)
    else:
        logger.info("[IMP:9][check_secrets][PASS] All required secrets present")

    return missing


# endregion FUNC_check_secrets


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_validate_charsets
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  Validate all secrets with charset field match their declared regex.
## @io       secrets_list (list[dict]), env_file_map (dict[str,str]) → list[str] of errors
## @complexity 2 — O(S) with re.match per secret with charset
## @invariants
##   - Only secrets with explicit charset field are validated
##   - Uses re.match (full string match, not re.search)
##   - Empty/missing values are skipped (caught by check_secrets)
## @rationale Reuses same charset logic as secrets_validator.py — prevents duplicate implementations.
def validate_charsets(secrets_list: list[dict], env_file_map: dict[str, str]) -> list[str]:
    """Validate all secrets with charset constraints match their regex."""
    logger.info("[IMP:7][validate_charsets][start] Checking %d secrets for charset compliance", len(secrets_list))
    errors: list[str] = []

    for s in secrets_list:
        charset = s.get("charset", "")
        if not charset:
            continue

        name = s.get("name", "")
        val = os.environ.get(name, "")
        if not val:
            val = env_file_map.get(name, "")

        if not val:
            # Already caught by check_secrets — skip here
            logger.info("[IMP:7][validate_charsets][skip] %s has empty value — skipping charset check", name)
            continue

        if not _re.match(charset, val):
            msg = f"[IMP:9][charset] FAIL: {name} does not match charset {charset}"
            logger.error(msg)
            errors.append(msg)
        else:
            logger.info("[IMP:8][validate_charsets][ok] %s matches charset %s", name, charset)

    if errors:
        logger.error("[IMP:9][validate_charsets][FAIL] %d charset violation(s)", len(errors))
    else:
        logger.info("[IMP:9][validate_charsets][PASS] All charset checks passed")

    return errors


# endregion FUNC_validate_charsets


# ─────────────────────────────────────────────────────────────────────────────
# region FUNC_main
# ─────────────────────────────────────────────────────────────────────────────


## @purpose  CLI entry point: parse compose args, validate secrets, exit 0/1.
## @io       test_args (list[str] | None) → int exit code (test_args for unit tests, None = sys.argv)
## @complexity 2 — main orchestration flow
## @invariants
##   - Only blocks if MANIFEST is found AND required secrets are missing
##   - Missing manifest → WARN + exit 0 (backward compat, no SSoT)
##   - Only checks when subcommand is "up" or None (down/stop/config bypass)
##   - --skip-preflight disables the check entirely
def main(test_args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docker compose preflight — validates secrets before compose up")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight check and pass through to docker compose",
    )
    parser.add_argument(
        "--manifest",
        default=_MANIFEST_DEFAULT,
        help=f"Path to secrets-manifest.yaml (default: {_MANIFEST_DEFAULT})",
    )
    parser.add_argument(
        "--secrets-env",
        default=os.environ.get("SECRETS_ENV_FILE", _SECRETS_ENV_DEFAULT),
        help="Path to secrets.env file",
    )
    parser.add_argument(
        "compose_args",
        nargs=argparse.REMAINDER,
        help="Docker compose arguments to parse for profiles",
    )

    args = parser.parse_args(test_args)

    # Log level: only IMP:5+ by default, IMP:7+ if --verbose
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("COMPOSE_PREFLIGHT_DEBUG") else logging.INFO,
        format="%(message)s",
    )

    # Parse compose args to extract subcommand and profiles
    compose_args = args.compose_args or []
    profiles, subcommand = parse_compose_args(compose_args)

    # Only block for "up" or implicit (no subcommand — ambiguous, check anyway)
    if subcommand is not None and subcommand != "up":
        logger.info("[IMP:7][main][bypass] Subcommand=%s — no preflight check needed", subcommand)
        return 0

    if args.skip_preflight:
        logger.info("[IMP:7][main][skip] --skip-preflight set — bypassing preflight")
        return 0

    # Resolve target modules
    target_modules = resolve_modules(profiles)

    # Load manifest
    secrets_list = load_manifest(args.manifest)
    if secrets_list is None:
        logger.info("[IMP:7][main][pass] No manifest — allowing compose up (graceful degradation)")
        return 0

    # Load env file
    env_file_map = load_env_map(args.secrets_env)

    # Check secrets
    missing = check_secrets(target_modules, secrets_list, env_file_map)

    # Validate charsets
    charset_errors = validate_charsets(secrets_list, env_file_map)

    # Report and exit
    if missing:
        print("ERROR: Missing required secrets for compose up:", file=sys.stderr)
        for name in sorted(missing):
            print(f"  - {name}", file=sys.stderr)
        print("Hint: Run `make secrets-unlock` or source /run/platform/secrets.env", file=sys.stderr)
        logger.error("[IMP:9][main][BLOCKED] %d missing secret(s) — blocking compose up", len(missing))
        return 1

    if charset_errors:
        print("ERROR: Secrets charset validation failed:", file=sys.stderr)
        for err in charset_errors:
            print(f"  {err}", file=sys.stderr)
        logger.error("[IMP:9][main][BLOCKED] %d charset violation(s) — blocking compose up", len(charset_errors))
        return 1

    logger.info("[IMP:9][main][PASS] Preflight check passed — allowing compose up")
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
