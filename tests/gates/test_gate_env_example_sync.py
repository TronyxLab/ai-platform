# GREP_SUMMARY: gate env-example-sync .env .env.example key-mirror bidirection drift prevention
# STRUCTURE: ◇ test_env_example_well_formed → ◇ test_env_mirrors_example_keys → ◇ test_env_mirrors_example_order → ◇ test_hermes_agent_env_example

# region MODULE_CONTRACT
## @purpose  Gate test: validate .env.example ↔ .env bidirectional key sync.
##           .env.example — canonical reference; .env — mirror (same keys, same order).
##           3 invariants: well-formed example, key mirror (left-to-right, right-to-left),
##           structural order mirror.
## @scope    Parses root .env.example, root .env (if exists), hermes-agent/.env.example (if exists).
##           No Docker daemon required — pure static analysis.
## @invariants
##   - .env.example имеет 0 дублирующихся ключей
##   - .env (если существует) имеет те же ключи, что .env.example (bidirectional)
##   - .env.example и .env имеют одинаковый порядок ключей
##   - NO_PROXY в .env ⊆ NO_PROXY в .env.example (никаких внешних хостов в .env)
## @rationale — Mirror-структура .env/.env.example предотвращает дрейф:
##              новый ключ добавлен в один файл, но забыт в другом → gate ловит.
## @changes — 2026-07-16 | Created per env-restructure task
# endregion MODULE_CONTRACT

import logging
import os

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_EXAMPLE_PATH = os.path.join(ROOT_DIR, ".env.example")
ENV_PATH = os.path.join(ROOT_DIR, ".env")
HERMES_AGENT_ENV_EXAMPLE = os.path.join(ROOT_DIR, "core", "modules", "hermes-agent", ".env.example")


def _parse_keys_in_order(env_path):
    """Parse key names in order of appearance, ignoring comments and blanks."""
    keys = []
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key = line.split("=", 1)[0].strip()
                keys.append(key)
    return keys


@pytest.mark.gate
@ldd_trajectory
def test_env_example_well_formed(caplog):
    """.env.example must have no duplicate keys."""
    assert os.path.isfile(ENV_EXAMPLE_PATH), f".env.example not found at {ENV_EXAMPLE_PATH}"

    keys = _parse_keys_in_order(ENV_EXAMPLE_PATH)
    key_set = set(keys)

    violations = []
    if len(keys) != len(key_set):
        seen = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        violations.append(f"Duplicate keys: {dups}")
        for k, v in dups.items():
            logger.error("[IMP:10][gate] DUP: key '%s' appears %d times in .env.example", k, v)

    assert not violations, "GATE_ENV_EXAMPLE_SYNC: .env.example malformed:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: .env.example well-formed (%d unique keys)", len(keys))


@pytest.mark.gate
@ldd_trajectory
def test_env_mirrors_example_keys(caplog):
    """If .env exists, all keys must appear in both files (bidirectional mirror)."""
    if not os.path.isfile(ENV_PATH):
        logger.info("[IMP:7][gate] .env not found — skipping key mirror check (CI mode)")
        pytest.skip(".env not available (CI)")

    example_keys = set(_parse_keys_in_order(ENV_EXAMPLE_PATH))
    env_keys = set(_parse_keys_in_order(ENV_PATH))

    violations = []

    # Left-to-right: keys in .env.example missing from .env
    missing_from_env = example_keys - env_keys
    if missing_from_env:
        violations.append(
            f"Keys in .env.example but missing from .env ({len(missing_from_env)}): "
            + ", ".join(sorted(missing_from_env))
        )
        for k in sorted(missing_from_env):
            logger.error("[IMP:10][gate] DRIFT: '%s' in .env.example but not in .env", k)

    # Right-to-left: keys in .env missing from .env.example
    missing_from_example = env_keys - example_keys
    if missing_from_example:
        violations.append(
            f"Keys in .env but missing from .env.example ({len(missing_from_example)}): "
            + ", ".join(sorted(missing_from_example))
        )
        for k in sorted(missing_from_example):
            logger.error("[IMP:10][gate] DRIFT: '%s' in .env but not in .env.example", k)

    assert not violations, (
        f"GATE_ENV_SYNC: {len(violations)} key drift(s) between .env and .env.example:\n  " + "\n  ".join(violations)
    )
    logger.info("[IMP:9][gate] PASS: .env ↔ .env.example keys match bidirectionally (%d keys)", len(example_keys))


@pytest.mark.gate
@ldd_trajectory
def test_env_example_key_order_matches_dotenv(caplog):
    """If .env exists, key order must be identical (structural mirror)."""
    if not os.path.isfile(ENV_PATH):
        logger.info("[IMP:7][gate] .env not found — skipping order check (CI mode)")
        pytest.skip(".env not available (CI)")

    example_keys = _parse_keys_in_order(ENV_EXAMPLE_PATH)
    env_keys = _parse_keys_in_order(ENV_PATH)

    violations = []
    max_len = min(len(example_keys), len(env_keys))
    for i in range(max_len):
        if example_keys[i] != env_keys[i]:
            violations.append(f"Position {i}: .env.example has '{example_keys[i]}', .env has '{env_keys[i]}'")
            logger.error("[IMP:10][gate] ORDER: pos %d: example='%s' env='%s'", i, example_keys[i], env_keys[i])

    if len(example_keys) != len(env_keys):
        violations.append(f"Key count mismatch: .env.example={len(example_keys)}, .env={len(env_keys)}")

    assert not violations, f"GATE_ENV_ORDER_SYNC: {len(violations)} order mismatch(es):\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: .env ↔ .env.example key order match (%d keys)", len(example_keys))


@pytest.mark.gate
@ldd_trajectory
def test_hermes_agent_env_example_well_formed(caplog):
    """hermes-agent/.env.example must have no duplicate keys (if exists)."""
    if not os.path.isfile(HERMES_AGENT_ENV_EXAMPLE):
        pytest.skip("hermes-agent/.env.example not found")

    keys = _parse_keys_in_order(HERMES_AGENT_ENV_EXAMPLE)
    key_set = set(keys)

    violations = []
    if len(keys) != len(key_set):
        seen = {}
        for k in keys:
            seen[k] = seen.get(k, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        violations.append(f"Duplicate keys in hermes-agent/.env.example: {dups}")
        for k, v in dups.items():
            logger.error("[IMP:10][gate] DUP: key '%s' appears %d times in hermes-agent/.env.example", k, v)

    assert not violations, "GATE_HERMES_AGENT_ENV_EXAMPLE: malformed:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: hermes-agent/.env.example well-formed (%d unique keys)", len(keys))


def _parse_env_value(env_path: str, key: str) -> str | None:
    """Extract value of a key from a .env-style file. Returns None if not found."""
    if not os.path.isfile(env_path):
        return None
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    return None


@pytest.mark.gate
@ldd_trajectory
def test_env_noproxy_subset(caplog):
    """.env.example NO_PROXY — канонический список. .env NO_PROXY ⊆ .env.example NO_PROXY (никаких внешних хостов)."""
    if not os.path.isfile(ENV_PATH):
        logger.info("[IMP:7][gate] .env not found — skipping NO_PROXY subset check (CI mode)")
        pytest.skip(".env not available (CI)")

    env_noproxy = _parse_env_value(ENV_PATH, "NO_PROXY")
    example_noproxy = _parse_env_value(ENV_EXAMPLE_PATH, "NO_PROXY")

    if env_noproxy is None or example_noproxy is None:
        logger.info("[IMP:7][gate] NO_PROXY not set in one or both files — skipping")
        return

    env_hosts = set(h.strip() for h in env_noproxy.split(",") if h.strip())
    example_hosts = set(h.strip() for h in example_noproxy.split(",") if h.strip())

    violations = []
    extra_hosts = env_hosts - example_hosts
    if extra_hosts:
        violations.append(
            f"NO_PROXY in .env contains hosts not in .env.example ({len(extra_hosts)}): "
            + ", ".join(sorted(extra_hosts))
        )
        for host in sorted(extra_hosts):
            logger.error("[IMP:10][gate] NO_PROXY_DRIFT: '%s' in .env but not in .env.example", host)

    assert not violations, (
        f"GATE_ENV_NOPROXY_SUBSET: {len(violations)} NO_PROXY host(s) are external:\n  "
        + "\n  ".join(violations)
        + f"\n.env.example NO_PROXY: {', '.join(sorted(example_hosts))}"
    )
    logger.info("[IMP:9][gate] PASS: .env NO_PROXY ⊆ .env.example NO_PROXY (%d hosts)", len(env_hosts))
