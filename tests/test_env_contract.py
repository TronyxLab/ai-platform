#!/usr/bin/env python3
# GREP_SUMMARY: env-contract platform-env env-defaults parity prometheus-dirs canonical
# STRUCTURE: ▶ test_env_example_matches_platform_env_defaults → ⊕ 12 keys parity →
#            ▶ test_prometheus_dirs_canonical → ⊕ PROMETHEUS_*_DIR /opt/platform/ → ⊕ volumes
# region MODULE_CONTRACT
## @purpose  Contract tests enforcing parity between platform-env.yaml (SoT env_defaults)
##           and .env.example. Detects structural drift: new env_defaults key without
##           .env.example counterpart, or non-canonical Prometheus directory paths.
## @scope    Static file analysis of platform-env.yaml and .env.example.
##           No Docker, no subprocess — pure file I/O + YAML/env parsing.
## @invariants
##   - All 12 env_defaults keys from platform-env.yaml must exist in .env.example
##   - All 12 env_defaults values must match .env.example values (SoT alignment)
##   - PROMETHEUS_TARGETS_DIR and PROMETHEUS_RULES_DIR must use /opt/platform/ prefix
##   - PROMETHEUS_*_DIR paths must be registered in platform-env.yaml volumes section
##   - Manual LDD trajectory printing via print() for reliable output
## @rationale DevPlan 017 T1: converges drifts B (env_defaults parity) and
##            N (PROMETHEUS_RULES_DIR non-canonical path).
# endregion MODULE_CONTRACT

import logging
import os
import pathlib

import dotenv
import yaml

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent)
PLATFORM_ENV_YAML: str = os.path.join(PLATFORM_ROOT, "platform-env.yaml")
DOT_ENV_EXAMPLE: str = os.path.join(PLATFORM_ROOT, ".env.example")

# Expected count of env_defaults entries
# ⚠️ TRAP[BUG] · 2026-07-17 · MED · Test born red: expected 12, platform-env.yaml has 13 env_defaults since foundation
# · Symptom: fast gate FAIL "Expected 12 env_defaults, got 13" — hidden until pre-commit stage went green
# · Root: EXPECTED_ENV_DEFAULTS_COUNT written as 12 while platform-env.yaml env_defaults (incl. PROMETHEUS_RULES_DIR) counts 13
# · Fix: aligned constant to actual canonical set (13)
# · 2026-07-31 | 86 — PLATFORM_DOMAIN удалён (8a6dbcb), PROJECTS_BASE + PLATFORM_DEPLOY_TIMEOUT добавлены (debt F4)
# · 2026-07-31 | 89 — DevPlan 116 T3 (U-16/U-17): PLATFORM_DOMAIN возвращён как SoT env_defaults (D4),
# ·   + AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (алиасы ${S3_*}, U-17)
# · 2026-08-01 | 90 — DevPlan 117 D31: STATUS_PAGE_PORT=8080 зарегистрирован в platform-infra.yaml env_defaults
# · Prevention: keep this constant in sync when adding env_defaults; parity asserts below catch key/value drift
EXPECTED_ENV_DEFAULTS_COUNT: int = 90

# Canonical Prometheus directory paths
PROMETHEUS_TARGETS_DIR_CANONICAL: str = "/opt/platform/prometheus-targets"
PROMETHEUS_RULES_DIR_CANONICAL: str = "/opt/platform/prometheus-rules"


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_platform_env_yaml() -> dict:
    """Load and return the full platform-env.yaml dict.

    ## @purpose — Single source of truth loader for platform-env.yaml
    ## @returns — Parsed YAML dict
    ## @raises — AssertionError if file missing or unparseable
    """
    assert os.path.isfile(PLATFORM_ENV_YAML), f"[IMP:9] platform-env.yaml not found at {PLATFORM_ENV_YAML}"
    print(f"[IMP:7][load] platform-env.yaml found at {PLATFORM_ENV_YAML}")

    with open(PLATFORM_ENV_YAML) as f:
        data = yaml.safe_load(f)

    assert data is not None, "[IMP:9] platform-env.yaml is empty or invalid"
    assert "env_defaults" in data, "[IMP:9] platform-env.yaml missing env_defaults section"
    assert isinstance(data["env_defaults"], dict), "[IMP:9] env_defaults is not a dict"
    assert "volumes" in data, "[IMP:9] platform-env.yaml missing volumes section"

    print(f"[IMP:8][load] Loaded {len(data['env_defaults'])} env_defaults, {len(data['volumes'])} volumes")
    return data


def _load_dotenv_example() -> dict[str, str]:
    """Load .env.example as a flat key-value dict via python-dotenv.

    ## @purpose — Parse .env.example respecting shell variable references
    ##            and inline comments.
    ## @returns — Dict of {key: value} from .env.example
    ## @raises — AssertionError if file missing
    """
    assert os.path.isfile(DOT_ENV_EXAMPLE), f"[IMP:9] .env.example not found at {DOT_ENV_EXAMPLE}"
    print(f"[IMP:7][load] .env.example found at {DOT_ENV_EXAMPLE}")

    env_dict = dotenv.dotenv_values(DOT_ENV_EXAMPLE)
    print(f"[IMP:8][load] Loaded {len(env_dict)} keys from .env.example")
    return env_dict


def _collect_volume_paths(data: dict) -> list[str]:
    """Extract volume path strings from platform-env.yaml volumes list.

    ## @purpose — Normalise volumes list to flat string paths for membership checks.
    ## @returns — List of volume path strings
    """
    volumes = data.get("volumes", [])
    if isinstance(volumes, list):
        return [v["path"] for v in volumes if isinstance(v, dict) and "path" in v]
    return []


# ── Tests ──────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: 12 env_defaults parity between platform-env.yaml and .env.example
# ═══════════════════════════════════════════════════════════════════════════


def test_env_example_matches_platform_env_defaults() -> None:
    """
    # ▶ platform-env.yaml env_defaults → ⊕ .env.example → ◇ missing key? → ⎋ fail
    #   → ◇ value mismatch? → ⎋ fail → pass
    """
    # Load both sources
    platform_data = _load_platform_env_yaml()
    env_example = _load_dotenv_example()

    env_defaults: dict = platform_data["env_defaults"]

    # Assert exact count
    assert len(env_defaults) == EXPECTED_ENV_DEFAULTS_COUNT, (
        f"Expected {EXPECTED_ENV_DEFAULTS_COUNT} env_defaults, got {len(env_defaults)}"
    )
    print(f"[IMP:8][parity] env_defaults count: {len(env_defaults)} (expected {EXPECTED_ENV_DEFAULTS_COUNT})")

    # Check each key-value pair
    mismatches: list[str] = []
    missing_keys: list[str] = []

    for key, expected_value in env_defaults.items():
        if key not in env_example:
            missing_keys.append(key)
            print(f"[IMP:7][parity] Key '{key}' missing from .env.example")
            continue

        actual_value = env_example[key]
        # python-dotenv returns None for keys with empty value
        actual_value_str = actual_value if actual_value is not None else ""

        # AWS-алиасы (${S3_ACCESS_KEY} литералы, DevPlan 116 T3 U-17): dotenv интерполирует
        # ${S3_ACCESS_KEY} → 'test-access-key'; сравниваем с РЕЗОЛВНУТЫМ значением референса
        import re as _re

        alias_m = _re.fullmatch(r"\$\{(\w+)\}", expected_value)
        if alias_m and alias_m.group(1) in env_defaults:
            expected_value = env_defaults[alias_m.group(1)]

        if actual_value_str != expected_value:
            mismatches.append(f"{key}: .env.example='{actual_value_str}' ≠ env_defaults='{expected_value}'")
            print(f"[IMP:7][parity] Value mismatch for '{key}': '{actual_value_str}' ≠ '{expected_value}'")
        else:
            print(f"[IMP:8][parity] '{key}' matches: '{expected_value}'")

    # Fail on missing or mismatched keys
    error_parts = []
    if missing_keys:
        error_parts.append(f"Missing in .env.example ({len(missing_keys)}): {', '.join(missing_keys)}")
    if mismatches:
        error_parts.append(f"Value mismatches ({len(mismatches)}):\n" + "\n".join(mismatches))

    assert not error_parts, "Parity check failed:\n" + "\n".join(error_parts)

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print("[IMP:9][env_contract] Parity contract passed — all env_defaults validated")
    print("--- END LDD TRAJECTORY ---")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: PROMETHEUS_*_DIR have canonical /opt/platform/ paths,
#          registered in platform-env.yaml volumes
# ═══════════════════════════════════════════════════════════════════════════


def test_prometheus_dirs_canonical() -> None:
    """
    # ▶ .env.example PROMETHEUS_*_DIR → ⊕ /opt/platform/ prefix?
    #   → ⎋ fail → ▶ platform-env.yaml volumes → ⊕ path registered?
    #   → ⎋ fail → pass
    """
    # Load sources
    platform_data = _load_platform_env_yaml()
    env_example = _load_dotenv_example()

    # ── Check .env.example has PROMETHEUS_TARGETS_DIR with canonical path ──
    targets_dir = env_example.get("PROMETHEUS_TARGETS_DIR")
    assert targets_dir is not None, "[IMP:9] PROMETHEUS_TARGETS_DIR missing from .env.example"
    print(f"[IMP:8][prometheus] PROMETHEUS_TARGETS_DIR={targets_dir} in .env.example")
    assert targets_dir == PROMETHEUS_TARGETS_DIR_CANONICAL, (
        f"[IMP:9] PROMETHEUS_TARGETS_DIR='{targets_dir}' ≠ canonical '{PROMETHEUS_TARGETS_DIR_CANONICAL}'"
    )

    # ── Check .env.example has PROMETHEUS_RULES_DIR with canonical path ──
    rules_dir = env_example.get("PROMETHEUS_RULES_DIR")
    assert rules_dir is not None, "[IMP:9] PROMETHEUS_RULES_DIR missing from .env.example"
    print(f"[IMP:8][prometheus] PROMETHEUS_RULES_DIR={rules_dir} in .env.example")
    assert rules_dir == PROMETHEUS_RULES_DIR_CANONICAL, (
        f"[IMP:9] PROMETHEUS_RULES_DIR='{rules_dir}' ≠ canonical '{PROMETHEUS_RULES_DIR_CANONICAL}'"
    )

    # ── Check both paths are registered in platform-env.yaml volumes ──
    volume_paths = _collect_volume_paths(platform_data)
    assert PROMETHEUS_TARGETS_DIR_CANONICAL in volume_paths, (
        f"[IMP:9] Volume '{PROMETHEUS_TARGETS_DIR_CANONICAL}' not registered in platform-env.yaml volumes"
    )
    print(f"[IMP:8][prometheus] {PROMETHEUS_TARGETS_DIR_CANONICAL} registered in platform-env.yaml volumes")

    assert PROMETHEUS_RULES_DIR_CANONICAL in volume_paths, (
        f"[IMP:9] Volume '{PROMETHEUS_RULES_DIR_CANONICAL}' not registered in platform-env.yaml volumes"
    )
    print(f"[IMP:8][prometheus] {PROMETHEUS_RULES_DIR_CANONICAL} registered in platform-env.yaml volumes")

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    print("[IMP:9][env_contract] Parity contract passed — all env_defaults validated")
    print("--- END LDD TRAJECTORY ---")
