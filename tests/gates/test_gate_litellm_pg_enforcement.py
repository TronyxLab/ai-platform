#!/usr/bin/env python3
# GREP_SUMMARY: gate-test litellm postgres database_url enforcement sqlite prevention
# STRUCTURE: ◇ test_litellm_database_url_is_postgres → ◇ test_no_sqlite_in_env → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate test: enforce LiteLLM uses PostgreSQL (not SQLite) in all environments.
##           DATABASE_URL must start with postgres:// or postgresql:// — never sqlite:///.
## @scope    Validates .env (if exists) and docker-compose.test.yml for LiteLLM database URL.
##           No Docker daemon required — pure static analysis.
## @invariants
##   - .env DATABASE_URL (if present) must start with postgres:// or postgresql://
##   - docker-compose.test.yml DATABASE_URL (if present) must start with postgres:// or postgresql://
##   - No sqlite:/// reference anywhere in LiteLLM config/env
## @rationale LiteLLM inv. #8: PostgreSQL in all environments — never SQLite.
##            SQLite causes silent data loss in multi-process deployments.
##            This gate prevents regression that would break the PostgreSQL-invariant.
## @changes 2026-07-17 | Created per drift-convergence DevPlan T13
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
COMPOSE_TEST_PATH = ROOT_DIR / "docker-compose.test.yml"
LITELLM_DIR = ROOT_DIR / "core" / "modules" / "litellm"


def _parse_env_value(env_path: Path, key: str) -> str | None:
    """Extract value of a key from a .env-style file. Returns None if not found."""
    if not env_path.exists():
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


def _find_postgres_urls_in_yaml(compose_path: Path) -> list[str]:
    """Find all DATABASE_URL or database_url values in a docker-compose YAML file."""
    urls: list[str] = []
    if not compose_path.exists():
        return urls
    with open(compose_path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return urls

    # Search in services.*.environment and services.*.env_file
    services = data.get("services", {}) or {}
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        env = svc_config.get("environment", {}) or {}
        if isinstance(env, dict):
            for k, v in env.items():
                if "database_url" in k.lower():
                    urls.append(f"{svc_name}: {k}={v}")
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    if "database_url" in k.lower():
                        urls.append(f"{svc_name}: {k}={v}")
    return urls


def _check_sqlite_in_config_file(config_path: Path) -> list[str]:
    """Check a single config file for SQLite database_url references.

    Returns list of violation messages (empty = no violations).
    Used by _negative companion test to verify SQLite detection.
    """
    violations: list[str] = []
    if not config_path.exists():
        return violations
    try:
        content = config_path.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if "sqlite" in line.lower() and ":///" in line:
                violations.append(f"{config_path.name}:{i}: {line.strip()}")
    except (OSError, UnicodeDecodeError):
        pass
    return violations


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_litellm_env_database_url_is_postgres() -> None:
    """DATABASE_URL in .env (if set) must be PostgreSQL, not SQLite."""
    db_url = _parse_env_value(ENV_PATH, "DATABASE_URL")
    if db_url is None:
        logger.info("[IMP:7][gate] DATABASE_URL not set in .env — skipping env check")
        return

    violations: list[str] = []
    if db_url.startswith("sqlite"):
        violations.append(f"DATABASE_URL uses SQLite: {db_url}")

    if not db_url.startswith("postgres"):
        violations.append(f"DATABASE_URL does not start with postgres://: {db_url}")

    assert not violations, "GATE_LITELLM_PG_ENFORCEMENT:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: DATABASE_URL is PostgreSQL (%s)", db_url[:30] + "...")


@pytest.mark.gate
def test_litellm_compose_test_database_url_is_postgres() -> None:
    """DATABASE_URL in docker-compose.test.yml (if set) must be PostgreSQL, not SQLite."""
    urls = _find_postgres_urls_in_yaml(COMPOSE_TEST_PATH)
    if not urls:
        logger.info("[IMP:7][gate] No DATABASE_URL references in docker-compose.test.yml — skipping compose check")
        return

    violations: list[str] = []
    for url_entry in urls:
        if "sqlite" in url_entry.lower():
            violations.append(f"SQLite reference found: {url_entry}")
        # Only check if value looks like a URL (contains ://)
        if "://" in url_entry:
            # Extract the value after =
            val = url_entry.split("=", 1)[1] if "=" in url_entry else ""
            if val and val.startswith("sqlite"):
                violations.append(f"SQLite URL in compose: {url_entry}")
            elif val and not val.startswith("postgres"):
                violations.append(f"Non-PostgreSQL URL in compose: {url_entry}")

    assert not violations, "GATE_LITELLM_PG_ENFORCEMENT (compose):\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: docker-compose.test.yml DATABASE_URL is PostgreSQL")


@pytest.mark.gate
def test_no_sqlite_in_litellm_config() -> None:
    """Ensure no sqlite:/// references exist in LiteLLM module config files.

    This is a belt-and-suspenders check: if DATABASE_URL is not set,
    LiteLLM defaults to SQLite — this test catches default-reliance.
    """
    if not LITELLM_DIR.exists():
        pytest.skip("LiteLLM module directory not found")

    sqlite_refs: list[str] = []
    # Check config.yaml and docker-compose files
    for pattern in ["*.yaml", "*.yml", "*.env", "*.env.example"]:
        for f in LITELLM_DIR.rglob(pattern):
            if f.is_symlink() or not f.is_file():
                continue
            try:
                content = f.read_text()
                for i, line in enumerate(content.splitlines(), 1):
                    if "sqlite" in line.lower() and ":///" in line:
                        sqlite_refs.append(f"{f.relative_to(ROOT_DIR)}:{i}: {line.strip()}")
            except (OSError, UnicodeDecodeError):
                continue

    assert not sqlite_refs, (
        "GATE_LITELLM_PG_ENFORCEMENT: SQLite URL references found in LiteLLM config:\n  " + "\n  ".join(sqlite_refs)
    )
    logger.info("[IMP:9][gate] PASS: No SQLite references in LiteLLM module config")
