# GREP_SUMMARY: gate env-defaults-consistency POSTGRES_PASSWORD NEXTAUTH_SECRET unified defaults drift
# STRUCTURE: ◇ test_env_defaults_consistency → ○ load .env → ○ load hermes-agent/.env → ○ load definitions → ⊕ compare → ⎋ pass|fail
#           ◇ (W2 T2.3 merge) test_postgres_password_unified → test_nextauth_secret_precondition

# region MODULE_CONTRACT
## @purpose  Gate test: verify POSTGRES_PASSWORD and NEXTAUTH_SECRET have consistent
##           (unified) values across all config layers:
##           - .env (root project env)
##           - core/modules/hermes-agent/.env (module test env)
##           - core/secret-definitions.yaml (ci_default)
##           Drift means CI tests would use different credentials than local dev.
##           W2 T2.3 (DevPlan 160): консолидация env-семейства — поглотил consistency-темы
##           test_gate_env_example_drift.py (test_postgres_password_unified, test_nextauth_secret_precondition).
## @scope    Static YAML/.env parsing — no Docker daemon. @pytest.mark.gate.
## @invariants
##   - POSTGRES_PASSWORD must equal 'test-pg-pwd' in all 3 layers (+ .env.example/hermes consumers)
##   - NEXTAUTH_SECRET must equal 'ci-test-nextauth-secret-32-chars-min!!' in all 3 layers
##   - Failure means CI-local credential mismatch (flaky tests)
## @rationale DevPlan 078 T9/T11: POSTGRES_PASSWORD and NEXTAUTH_SECRET were found
##            to have different values across .env files and secret-definitions.yaml.
##            This gate locks the unified values and prevents future drift.
## @changes  CREATED: 2026-07-25 | DevPlan 078 Phase A T11
##           2026-08-12 | DevPlan 160 W2 T2.3 — MERGE consistency-тестов из env_example_drift
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
# ⚠️ TRAP[BUG] 2026-08-03 · CI: .env / hermes-agent/.env gitignored → тест падал
# · Symptom: CI gate-fast gates — «4 consistency error(s)» ([.env] NEXTAUTH_SECRET
#   is MISSING и т.д.); локально PASS (файлы существуют на dev-машине).
# · Root: тест читал gitignored файлы (.env, hermes-agent/.env) — на CI-раннере
#   (fresh checkout) их нет → пустые dict → MISSING.
# · Fix: канонический CI-источник — .env.example (G5, в git, генерируется из
#   platform-env.yaml + secret-definitions.yaml); .env — локальный override.
ROOT_ENV_PATH = Path(ROOT_DIR) / ".env"
if not pathlib.Path(ROOT_ENV_PATH).is_file():
    ROOT_ENV_PATH = Path(ROOT_DIR) / ".env.example"
HERMES_ENV_PATH = Path(ROOT_DIR) / "core" / "modules" / "hermes-agent" / ".env"
if not pathlib.Path(HERMES_ENV_PATH).is_file():
    HERMES_ENV_PATH = Path(ROOT_DIR) / "core" / "modules" / "hermes-agent" / ".env.example"
SECRET_DEFINITIONS_PATH = Path(ROOT_DIR) / "core" / "secret-definitions.yaml"

UNIFIED_PG_PASSWORD = "test-pg-pwd"
UNIFIED_NEXTAUTH_SECRET = "ci-test-nextauth-secret-32-chars-min!!"


# ── Helpers ────────────────────────────────────────────────────────────────────

# region HELPERS


def _parse_dotenv(filepath: str) -> dict[str, str]:
    """Parse a .env file into key → value dict. No python-dotenv dependency."""
    result: dict[str, str] = {}
    if not pathlib.Path(filepath).is_file():
        logger.warning("[IMP:4][env_defaults] File not found: %s", filepath)
        return result
    with pathlib.Path(filepath).open(encoding="utf-8") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    logger.info("[IMP:8][env_defaults] Parsed %d entries from %s", len(result), filepath)
    return result


def _get_definitions_value(key_name: str) -> str:
    """Read ci_default for a given secret name from secret-definitions.yaml."""
    if not pathlib.Path(SECRET_DEFINITIONS_PATH).is_file():
        logger.warning("[IMP:4][env_defaults] Definitions file not found: %s", SECRET_DEFINITIONS_PATH)
        return ""
    with pathlib.Path(SECRET_DEFINITIONS_PATH).open(encoding="utf-8") as f:
        defs = yaml.safe_load(f)
    for secret in defs.get("secrets", []):
        if secret.get("name") == key_name:
            return secret.get("ci_default", "")
    return ""


def _check_env_var(layer_name: str, env_dict: dict[str, str], key: str, expected: str) -> str | None:
    """Check that a key in an env dict matches expected. Returns error message or None."""
    actual = env_dict.get(key, "")
    if not actual:
        return f"[{layer_name}] {key} is MISSING"
    if actual != expected:
        return f"[{layer_name}] {key} = '{actual}', expected '{expected}'"
    return None


# endregion HELPERS


# ── Tests ──────────────────────────────────────────────────────────────────────

# region FUNC_test_env_defaults_consistency
## @purpose — Verify POSTGRES_PASSWORD and NEXTAUTH_SECRET have the same unified
##            value across root .env, hermes-agent/.env, and secret-definitions.yaml.
## @io — ⇥ caplog → ⎋ None (pytest.fail on inconsistency)
## @complexity — O(N + M) — two .env parses + one YAML parse
## @invariants
##   - Layers checked: .env, hermes-agent/.env, secret-definitions.yaml ci_default
##   - All must show the exact unified value (test-pg-pwd / ci-test-nextauth-secret-32-chars-min!!)
##   - @pytest.mark.gate, static check — no CI skip needed


@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-25 · REGRESSION · POSTGRES_PASSWORD / NEXTAUTH_SECRET unified value drift
# · Last fail: N/A (new gate — locks unified values from DevPlan 078)
# · Remove if: POSTGRES_PASSWORD and NEXTAUTH_SECRET are removed from the platform
def test_env_defaults_consistency(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """POSTGRES_PASSWORD and NEXTAUTH_SECRET must be consistent across all config layers."""
    # region BLOCK_Load
    logger.info("[IMP:7][env_defaults] Loading root .env: %s", ROOT_ENV_PATH)
    root_env = _parse_dotenv(ROOT_ENV_PATH)

    logger.info("[IMP:7][env_defaults] Loading hermes-agent .env: %s", HERMES_ENV_PATH)
    hermes_env = _parse_dotenv(HERMES_ENV_PATH)

    pg_def = _get_definitions_value("POSTGRES_PASSWORD")
    ns_def = _get_definitions_value("NEXTAUTH_SECRET")
    logger.info(
        "[IMP:8][env_defaults] Definitions ci_default: POSTGRES_PASSWORD='%s', NEXTAUTH_SECRET='%s'", pg_def, ns_def
    )
    # endregion BLOCK_Load

    # region BLOCK_Check
    errors: list[str] = []

    # POSTGRES_PASSWORD
    for name, env_dict in [(".env", root_env), ("hermes-agent/.env", hermes_env)]:
        err = _check_env_var(name, env_dict, "POSTGRES_PASSWORD", UNIFIED_PG_PASSWORD)
        if err:
            errors.append(err)
            logger.warning("[IMP:7][env_defaults] %s", err)
        else:
            logger.info("[IMP:8][env_defaults] %s POSTGRES_PASSWORD = '%s' ✓", name, UNIFIED_PG_PASSWORD)

    if pg_def != UNIFIED_PG_PASSWORD:
        errors.append(
            f"[secret-definitions.yaml] POSTGRES_PASSWORD ci_default = '{pg_def}', expected '{UNIFIED_PG_PASSWORD}'"
        )
        logger.warning("[IMP:7][env_defaults] definitions POSTGRES_PASSWORD mismatch")

    # NEXTAUTH_SECRET
    for name, env_dict in [(".env", root_env), ("hermes-agent/.env", hermes_env)]:
        err = _check_env_var(name, env_dict, "NEXTAUTH_SECRET", UNIFIED_NEXTAUTH_SECRET)
        if err:
            errors.append(err)
            logger.warning("[IMP:7][env_defaults] %s", err)
        else:
            logger.info("[IMP:8][env_defaults] %s NEXTAUTH_SECRET = '%s' ✓", name, UNIFIED_NEXTAUTH_SECRET)

    if ns_def != UNIFIED_NEXTAUTH_SECRET:
        errors.append(
            f"[secret-definitions.yaml] NEXTAUTH_SECRET ci_default = '{ns_def}', expected '{UNIFIED_NEXTAUTH_SECRET}'"
        )
        logger.warning("[IMP:7][env_defaults] definitions NEXTAUTH_SECRET mismatch")
    # endregion BLOCK_Check

    # region BLOCK_Assert
    if errors:
        logger.error("[IMP:9][env_defaults] %d consistency error(s) found", len(errors))
        pytest.fail(
            f"Env defaults consistency check failed — {len(errors)} error(s):\n"
            + "\n".join(f"  {e}" for e in errors)
            + "\n\nRun 'make fix-gate' or manually unify values to:\n"
            f"  POSTGRES_PASSWORD={UNIFIED_PG_PASSWORD}\n"
            f"  NEXTAUTH_SECRET={UNIFIED_NEXTAUTH_SECRET}"
        )

    logger.info("[IMP:9][env_defaults] ✅ All env defaults consistent across all 3 layers")
    # endregion BLOCK_Assert


# endregion FUNC_test_env_defaults_consistency


# ═══════════════════════════════════════════════════════════════════════════════
# W2 T2.3 — MERGED from test_gate_env_example_drift.py (consistency-темы)
# ═══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_postgres_password_unified
## @purpose  MERGED (W2 T2.3): POSTGRES_PASSWORD == test-pg-pwd во ВСЕХ 4 потребителях
##           (secret-definitions ci_default + .env.example + hermes/.env.example + hermes/.env).
## @io — ⇥ caplog → ⎋ None (assert)
## @complexity — O(F * L) — построчный скан 4 файлов


@pytest.mark.gate
@ldd_trajectory
def test_postgres_password_unified(caplog) -> None:
    """All POSTGRES_PASSWORD defaults match secret-definitions.yaml ci_default (test-pg-pwd)."""
    with pathlib.Path(SECRET_DEFINITIONS_PATH).open(encoding="utf-8") as f:
        sd = yaml.safe_load(f)

    pg_ci_default = None
    for s in sd.get("secrets", []):
        if s.get("name") == "POSTGRES_PASSWORD":
            pg_ci_default = s.get("ci_default", "")
            break

    assert pg_ci_default == "test-pg-pwd", f"POSTGRES_PASSWORD ci_default must be test-pg-pwd, got {pg_ci_default}"

    # .env.example
    env_example_path = Path(ROOT_DIR) / ".env.example"
    with pathlib.Path(env_example_path).open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("POSTGRES_PASSWORD="):
                val = line.split("=", 1)[1].strip()
                assert val == "test-pg-pwd", f".env.example POSTGRES_PASSWORD = {val}, expected test-pg-pwd"
                break

    # hermes-agent .env.example / .env
    for suffix in (".env.example", ".env"):
        hermes_path = Path(ROOT_DIR) / "core" / "modules" / "hermes-agent" / suffix
        if not pathlib.Path(hermes_path).is_file():
            continue
        with pathlib.Path(hermes_path).open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("POSTGRES_PASSWORD="):
                    val = line.split("=", 1)[1].strip()
                    assert val == "test-pg-pwd", (
                        f"hermes-agent/{suffix} POSTGRES_PASSWORD = {val}, expected test-pg-pwd"
                    )
                    break

    logger.info("[IMP:9][gate] PASS: POSTGRES_PASSWORD unified to test-pg-pwd across all 4 consumers")


# endregion FUNC_test_postgres_password_unified


# region FUNC_test_nextauth_secret_precondition
## @purpose  MERGED (W2 T2.3): NEXTAUTH_SECRET consistency — ci_default (secret-definitions) == .env.example.
## @io — ⇥ caplog → ⎋ None (assert)
## @complexity — O(F * L)


@pytest.mark.gate
@ldd_trajectory
def test_nextauth_secret_precondition(caplog) -> None:
    """NEXTAUTH_SECRET consistent between secret-defs ci_default and .env.example."""
    with pathlib.Path(SECRET_DEFINITIONS_PATH).open(encoding="utf-8") as f:
        sd = yaml.safe_load(f)

    nextauth_ci = None
    for s in sd.get("secrets", []):
        if s.get("name") == "NEXTAUTH_SECRET":
            nextauth_ci = s.get("ci_default", "")
            break

    env_example_path = Path(ROOT_DIR) / ".env.example"
    with pathlib.Path(env_example_path).open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("NEXTAUTH_SECRET="):
                env_val = line.split("=", 1)[1].strip()
                assert env_val == nextauth_ci, f"NEXTAUTH_SECRET .env.example={env_val} != ci_default={nextauth_ci}"
                break

    logger.info("[IMP:9][gate] PASS: NEXTAUTH_SECRET consistent between secret-defs ci_default and .env.example")


# endregion FUNC_test_nextauth_secret_precondition
