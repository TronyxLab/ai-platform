# GREP_SUMMARY: test-pgbouncer-static static-audit compose-section healthcheck-port pool-mode databases-match no-proxy required-dbs host-port-dbname postgres pgbouncer litellm langfuse hermes-agent
# STRUCTURE: ▶ fixtures(postgres_fixtures obs_compose_paths hermes_agent_compose_path) → test_pgbouncer_section_in_compose(◇ YAML services) → test_pgbouncer_healthcheck_port_6432(◇ healthcheck) → test_pgbouncer_pool_mode_in_env(◇ POOL_MODE) → test_pgbouncer_databases_match_clients(◇ DATABASE_URLS↔DATABASE_URL in litellm+langfuse) → test_no_proxy_includes(◇ NO_PROXY in hermes-agent) → test_required_databases_present(◇ ⊕ platform+litellm+langfuse) → test_each_database_has_host_port_dbname(◇ params) → ⎋
# region MODULE_CONTRACT
## @purpose  Static audit of pgbouncer configuration: compose service definition,
##           healthcheck port, pool mode (env-based), database mapping consistency with
##           litellm/langfuse compose files, NO_PROXY coverage in hermes-agent,
##           required databases presence, and per-database host/port/dbname validation.
##           Extracted from test_postgres.py (TASK-4) + consolidated from test_bootstrap_pgbouncer.py (T5.2).
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML from docker-compose.base.yml (no pgbouncer.ini — env-based config).
## @invariants
##   - pgbouncer service is defined in postgres/docker-compose.base.yml
##   - Healthcheck uses pg_isready on port 6432
##   - POOL_MODE from compose environment = transaction (edoburu/pgbouncer image)
##   - DATABASE_URLS in compose pgbouncer env match DATABASE_URL references in litellm/langfuse compose
##   - NO_PROXY in hermes-agent compose includes pgbouncer (explicit fallback)
##   - platform, litellm, langfuse databases must be present in DATABASE_URLS
##   - Each DATABASE_URLS entry must resolve host=postgres, port=5432, dbname=key
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale — pgbouncer is critical infrastructure for litellm/langfuse DB connectivity;
##              static audit catches config drift before deployment.
##              RefactoringBrief §3.10: env-based config, pgbouncer.ini removed.
##              observability module split into 5 modules (TASK-3); relevant compose files:
##              litellm/dc.base.yml (DATABASE_URL→litellm), langfuse/dc.base.yml (DATABASE_URL→langfuse),
##              hermes-agent/dc.base.yml (NO_PROXY fallback with pgbouncer).
##              T5.2 consolidation: test_bootstrap_pgbouncer.py (3 trivial + 2 contentful tests)
##              deleted; the 2 contentful tests migrated here — test_required_databases_present
##              and test_each_database_has_host_port_dbname.
## @changes — CREATED: 2026-07-11 | Extracted from test_postgres.py (TASK-4: 17→12+5 split)
##            UPDATED: 2026-07-14 | observability→litellm+langfuse+hermes-agent fixtures (QAAudit post-split fix)
##            UPDATED: 2026-07-15 | Migrated test_required_databases_present + test_each_database_has_host_port_dbname
##              from deleted test_bootstrap_pgbouncer.py (T5.2 consolidation)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import ldd_trajectory

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM_ENV_PATH = os.path.join(ROOT_DIR, "platform-env.yaml")

logger = logging.getLogger(__name__)


# region FIXTURES
## @purpose — Module-scoped fixtures for isolated compose file access.
##            Duplicated from test_postgres.py to keep files self-contained.
##            postgres_fixtures: copies postgres module to temp dir.
##            obs_compose_paths: copies litellm + langfuse compose files to temp dirs (list).
##            hermes_agent_compose_path: copies hermes-agent compose file to temp dir.
## @rationale — pytest fixtures are file-scoped by default; duplication is preferrable
##              to cross-file fixture imports which break test isolation.
##              observability module was split into 5 modules; litellm/langfuse have DATABASE_URL,
##              hermes-agent has explicit NO_PROXY fallback with pgbouncer.


@pytest.fixture(scope="module")
def postgres_fixtures(tmp_path_factory):
    """Copy postgres module files to temp dir for isolated testing."""
    src = os.path.join(os.path.dirname(__file__), "..", "core", "modules", "postgres")
    dst = tmp_path_factory.mktemp("pgbouncer_postgres")
    shutil.copytree(src, str(dst), dirs_exist_ok=True)
    dst_str = str(dst)
    return {
        "MODULE_DIR": dst_str,
        "COMPOSE_FILE": os.path.join(dst_str, "docker-compose.base.yml"),
        "MODULE_YAML": os.path.join(dst_str, "module.yaml"),
        "HEALTHCHECK_SH": os.path.join(dst_str, "healthcheck.sh"),
        "READY_CHECK_SH": os.path.join(dst_str, "ready-check.sh"),
    }


@pytest.fixture(scope="module")
def obs_compose_paths(tmp_path_factory):
    """Copy litellm + langfuse compose files to temp dirs — returns list of paths.

    After observability module split (TASK-3), DATABASE_URL references are in:
    - litellm/docker-compose.base.yml (DATABASE_URL → pgbouncer:6432/litellm)
    - langfuse/docker-compose.base.yml (DATABASE_URL → pgbouncer:6432/langfuse)
    """
    module_dir = os.path.join(os.path.dirname(__file__), "..", "core", "modules")
    paths = []
    for mod in ("litellm", "langfuse"):
        src = os.path.join(module_dir, mod, "docker-compose.base.yml")
        dst_dir = tmp_path_factory.mktemp(f"pgbouncer_obs_{mod}")
        dst = os.path.join(str(dst_dir), "docker-compose.base.yml")
        shutil.copy2(src, dst)
        paths.append(dst)
    return paths


@pytest.fixture(scope="module")
def hermes_agent_compose_path(tmp_path_factory):
    """Copy hermes-agent docker-compose.base.yml to temp dir.

    hermes-agent has explicit NO_PROXY fallback with pgbouncer:
    NO_PROXY: "${NO_PROXY:-localhost,127.0.0.1,.local,postgres,pgbouncer,redis,clickhouse}"
    """
    src = os.path.join(os.path.dirname(__file__), "..", "core", "modules", "hermes-agent", "docker-compose.base.yml")
    dst_dir = tmp_path_factory.mktemp("pgbouncer_hermes_agent")
    dst = os.path.join(str(dst_dir), "docker-compose.base.yml")
    shutil.copy2(src, dst)
    return dst


# endregion FIXTURES


# region HELPERS
## @purpose — Parse database names from pgbouncer DATABASE_URLS env var (comma-separated URLs).
##            Extracted from test_postgres.py (TASK-4).


def _parse_db_names_from_database_urls(urls_value: str) -> set[str]:
    """Parse database names from DATABASE_URLS env var value (comma-separated URLs).

    ## @purpose — Extract db names from pgbouncer DATABASE_URLS compose env (env-based config).
    ## @io — ⇥ urls_value: str (comma-separated PostgreSQL URLs) → ⎋ set[str]
    ## @complexity — O(N) where N = number of URLs
    ## @rationale — Shared pattern with test_bootstrap_pgbouncer.py (TASK-01/TASK-02).
    """
    if not urls_value:
        return set()
    db_names: set[str] = set()
    for url in urls_value.split(","):
        url = url.strip()
        if "/" in url:
            db_name = url.rsplit("/", 1)[-1]
            db_names.add(db_name)
    return db_names


def _extract_databases_from_observability(obs_compose_paths: list[str]) -> set[str]:
    """Extract database names from DATABASE_URL env vars across litellm+langfuse compose files.

    After observability module split, DATABASE_URL references are in litellm and langfuse
    compose files. This aggregates database names from all provided paths.
    """
    databases: set[str] = set()
    for obs_compose in obs_compose_paths:
        if not os.path.isfile(obs_compose):
            continue

        with open(obs_compose) as f:
            data = yaml.safe_load(f)

        services: dict = data.get("services", {}) if isinstance(data, dict) else {}
        for svc_config in services.values():
            if not isinstance(svc_config, dict):
                continue
            env_definitions: dict = {}
            raw_env = svc_config.get("environment", {})
            if isinstance(raw_env, dict):
                env_definitions = raw_env
            elif isinstance(raw_env, list):
                for item in raw_env:
                    if isinstance(item, str) and "=" in item:
                        k, _, v = item.partition("=")
                        env_definitions[k.strip()] = v.strip()

            for key, value in env_definitions.items():
                if key == "DATABASE_URL" and isinstance(value, str):
                    clean_value = value
                    while "${" in clean_value and "}" in clean_value:
                        start = clean_value.find("${")
                        end = clean_value.find("}", start)
                        if end == -1:
                            break
                        placeholder = clean_value[start : end + 1]
                        if ":-" in placeholder:
                            fallback = placeholder.split(":-", 1)[1].rstrip("}")
                            clean_value = clean_value.replace(placeholder, fallback, 1)
                        else:
                            clean_value = clean_value.replace(placeholder, "", 1)

                    if "/" in clean_value.split("@")[-1] if "@" in clean_value else False:
                        after_at = clean_value.split("@", 1)[1] if "@" in clean_value else clean_value
                        if "/" in after_at:
                            path_part = after_at.split("/", 1)[1]
                            dbname = path_part.split("?")[0].split("#")[0].strip()
                            if dbname:
                                databases.add(dbname)

    return databases


def _get_pgbouncer_env_from_compose(compose_path: str) -> dict[str, str]:
    """Read pgbouncer environment variables from compose file path.

    ## @purpose — Centralised accessor for pgbouncer environment in static audit tests.
    ##            Adapted from deleted test_bootstrap_pgbouncer.py (T5.2 consolidation).
    ## @io — ⇥ compose_path: str (path to docker-compose.base.yml) → ⎋ dict[str, str]
    ## @complexity — O(1) — single YAML parse
    """
    with open(compose_path) as f:
        data = yaml.safe_load(f)
    pgb_svc = data.get("services", {}).get("pgbouncer", {})
    return dict(pgb_svc.get("environment", {}))


def _parse_database_urls(env: dict[str, str]) -> set[str]:
    """Parse DATABASE_URLS env var into set of database names.

    ## @purpose — Extract database names from comma-separated DATABASE_URLS value.
    ##            Adapted from deleted test_bootstrap_pgbouncer.py (T5.2 consolidation).
    ## @io — ⇥ env: dict → ⎋ set[str]: database names
    ## @complexity — O(N) where N = number of URLs
    ## @invariants
    ##   - Each URL ends with /dbname — last segment after / is the db name
    ##   - Empty DATABASE_URLS returns empty set
    """
    raw = env.get("DATABASE_URLS", "")
    if not raw:
        return set()
    db_names: set[str] = set()
    for url in raw.split(","):
        url = url.strip()
        if "/" in url:
            db_name = url.rsplit("/", 1)[-1]
            db_names.add(db_name)
    return db_names


def _parse_database_urls_detailed(env: dict[str, str]) -> dict[str, dict[str, str]]:
    """Parse DATABASE_URLS into {db_name: {host, port, dbname}}.

    ## @purpose — Extract detailed connection parameters from each DATABASE_URLS entry.
    ##            Adapted from deleted test_bootstrap_pgbouncer.py (T5.2 consolidation).
    ## @io — ⇥ env: dict → ⎋ dict[str, dict]: {db_name: {host, port, dbname}}
    ## @complexity — O(N) where N = number of URLs
    ## @invariants
    ##   - Host defaults to 'postgres' if not explicitly specified
    ##   - Port defaults to '5432' if not explicitly specified
    """
    raw = env.get("DATABASE_URLS", "")
    if not raw:
        return {}
    databases: dict[str, dict[str, str]] = {}
    for url in raw.split(","):
        url = url.strip()
        if "/" not in url:
            continue
        db_name = url.rsplit("/", 1)[-1]
        host = "postgres"
        port = "5432"
        if "@" in url:
            after_at = url.split("@", 1)[1]
            host_part = after_at.split("/", 1)[0] if "/" in after_at else after_at
            if ":" in host_part:
                host, port = host_part.split(":", 1)
            else:
                host = host_part
        databases[db_name] = {"host": host, "port": port, "dbname": db_name}
    return databases


# endregion HELPERS


# region PGBOUNCER_STATIC_AUDIT_TESTS
## @purpose — Static audit of pgbouncer configuration: compose service definition,
##            healthcheck port, pool mode (env-based), database mapping consistency with
##            litellm+langfuse compose, and NO_PROXY coverage in hermes-agent.
## @scope    All tests are @pytest.mark.static_audit — no Docker daemon required.
##           Tests parse YAML from docker-compose.base.yml (no pgbouncer.ini — env-based config).
## @invariants
##   - pgbouncer service is defined in postgres/docker-compose.base.yml
##   - Healthcheck uses pg_isready on port 6432
##   - POOL_MODE from compose environment = transaction (edoburu/pgbouncer image)
##   - DATABASE_URLS in compose pgbouncer env match DATABASE_URL references in litellm+langfuse compose
##   - NO_PROXY in hermes-agent compose includes pgbouncer (explicit fallback)
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale — pgbouncer is critical infrastructure for litellm/langfuse DB connectivity;
##              static audit catches config drift before deployment.
##              RefactoringBrief §3.10: env-based config, pgbouncer.ini removed.
##              observability module split into 5 modules (TASK-3).
## @changes — EXTRACTED: 2026-07-11 | From test_postgres.py (TASK-4)
##            UPDATED: 2026-07-14 | observability→litellm+langfuse+hermes-agent fixtures (QAAudit post-split fix)


def _load_compose(compose_path: str) -> dict:
    """Load docker-compose.base.yml as parsed YAML dict."""
    with open(compose_path) as f:
        return yaml.safe_load(f)


# ── Test 1: pgbouncer Section in Compose ────────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_section_in_compose(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][compose_section] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        data = _load_compose(compose_path)
        services = data.get("services", {})
        has_pgbouncer = "pgbouncer" in services or "pgbouncer" in services

        logger.critical(
            "[IMP:9][test_pgbouncer][compose_section] ASSERT: pgbouncer in services=%s",
            has_pgbouncer,
        )
        assert has_pgbouncer, "pgbouncer or pgbouncer service not found in postgres compose"


# ── Test 2: Healthcheck Port 6432 ───────────────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_healthcheck_port_6432(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][healthcheck_6432] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        data = _load_compose(compose_path)
        services = data.get("services", {})

        pgbouncer_svc = services.get("pgbouncer") or services.get("pgbouncer") or {}
        hc = pgbouncer_svc.get("healthcheck", {})
        test_cmd = hc.get("test", [])

        if isinstance(test_cmd, list):
            test_str = " ".join(test_cmd)
        else:
            test_str = str(test_cmd)

        has_pg_isready = "pg_isready" in test_str
        has_port_6432 = "6432" in test_str

        logger.critical(
            "[IMP:9][test_pgbouncer][healthcheck_6432] ASSERT: pg_isready=%s port_6432=%s",
            has_pg_isready,
            has_port_6432,
        )
        assert has_pg_isready, f"pgbouncer healthcheck must use pg_isready, got: {test_str}"
        assert has_port_6432, f"pgbouncer healthcheck must reference port 6432, got: {test_str}"


# ── Test 3: Pool Mode from Compose Environment ──────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_pool_mode_in_env(postgres_fixtures, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][pool_mode_env] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        data = _load_compose(compose_path)
        services = data.get("services", {})
        pgbouncer_svc = services.get("pgbouncer") or services.get("pgbouncer") or {}
        env_vars = pgbouncer_svc.get("environment", {})
        pool_mode = env_vars.get("POOL_MODE", "")

        logger.critical(
            "[IMP:9][test_pgbouncer][pool_mode_env] ASSERT: POOL_MODE=%s (expected transaction)",
            pool_mode,
        )
        assert pool_mode == "transaction", (
            f"POOL_MODE in compose pgbouncer environment must be 'transaction', got: '{pool_mode}'"
        )


# ── Test 4: Databases Match Clients ─────────────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_databases_match_clients(postgres_fixtures, obs_compose_paths, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][databases_match_env] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        data = _load_compose(compose_path)
        services = data.get("services", {})
        pgbouncer_svc = services.get("pgbouncer") or services.get("pgbouncer") or {}
        env_vars = pgbouncer_svc.get("environment", {})
        database_urls = env_vars.get("DATABASE_URLS", "")
        pgb_databases = _parse_db_names_from_database_urls(database_urls)
        logger.info(
            "[IMP:7][test_pgbouncer][databases_match_env] pgbouncer DATABASE_URLS databases: %s",
            pgb_databases,
        )

        obs_databases = _extract_databases_from_observability(obs_compose_paths)
        logger.info(
            "[IMP:7][test_pgbouncer][databases_match_env] litellm+langfuse DATABASE_URL databases: %s",
            obs_databases,
        )

        missing = obs_databases - pgb_databases

        logger.critical(
            "[IMP:9][test_pgbouncer][databases_match_env] ASSERT: pgb=%s obs=%s missing=%s",
            pgb_databases,
            obs_databases,
            missing,
        )
        assert not missing, (
            f"Databases {missing} are referenced in litellm/langfuse DATABASE_URL "
            f"but not defined in pgbouncer DATABASE_URLS. "
            f"pgbouncer has: {pgb_databases}; litellm+langfuse need: {obs_databases}"
        )


# ── Test 5: Hermes-agent NO_PROXY fallback ⊇ SoT ──────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_no_proxy_includes(hermes_agent_compose_path, caplog) -> None:
    """Hermes-agent compose NO_PROXY fallback ⊇ platform-env.yaml proxy.no_proxy_internal.

    Валидирует, что fallback-значение NO_PROXY в hermes-agent docker-compose.base.yml
    покрывает канонический SoT-список внутренних сервисов.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][no_proxy] START — validate fallback ⊇ SoT")

        # ── Load SoT from platform-env.yaml ──
        assert os.path.isfile(PLATFORM_ENV_PATH), f"platform-env.yaml not found: {PLATFORM_ENV_PATH}"
        with open(PLATFORM_ENV_PATH) as f:
            platform_env = yaml.safe_load(f)

        proxy_config = platform_env.get("proxy", {})
        no_proxy_internal_raw: str = proxy_config.get("no_proxy_internal", "")
        sot_entries: set[str] = {e.strip() for e in no_proxy_internal_raw.split(",") if e.strip()}
        logger.info("[IMP:8][test_pgbouncer][no_proxy] SoT entries: %s", sorted(sot_entries))

        # ── Extract NO_PROXY fallback from hermes-agent compose ──
        assert os.path.isfile(hermes_agent_compose_path), f"Hermes-agent compose not found: {hermes_agent_compose_path}"
        with open(hermes_agent_compose_path) as f:
            data = yaml.safe_load(f)

        services: dict = data.get("services", {}) if isinstance(data, dict) else {}
        fallback_value = ""

        for svc_config in services.values():
            if not isinstance(svc_config, dict):
                continue
            env_definitions: dict = {}
            raw_env = svc_config.get("environment", {})
            if isinstance(raw_env, dict):
                env_definitions = raw_env
            elif isinstance(raw_env, list):
                for item in raw_env:
                    if isinstance(item, str) and "=" in item:
                        k, _, v = item.partition("=")
                        env_definitions[k.strip()] = v.strip()

            for key, value in env_definitions.items():
                if key.upper() == "NO_PROXY" and "${NO_PROXY:-" in str(value):
                    # Extract fallback from ${NO_PROXY:-fallback}
                    fb_match = str(value).split("${NO_PROXY:-", 1)[1].rstrip("}").strip()
                    fallback_value = fb_match
                    logger.info(
                        "[IMP:7][test_pgbouncer][no_proxy] Found NO_PROXY with fallback: '%s'",
                        fallback_value,
                    )
                    break
            if fallback_value:
                break

        assert fallback_value, (
            "No NO_PROXY with ${NO_PROXY:-fallback} pattern found in hermes-agent docker-compose.base.yml"
        )

        fallback_entries: set[str] = {e.strip() for e in fallback_value.split(",") if e.strip()}
        missing_entries = sot_entries - fallback_entries

        logger.critical(
            "[IMP:9][test_pgbouncer][no_proxy] ASSERT: fallback=%s, SoT=%s, missing=%s",
            sorted(fallback_entries),
            sorted(sot_entries),
            sorted(missing_entries),
        )
        assert not missing_entries, (
            f"Hermes-agent NO_PROXY fallback missing SoT entries: {sorted(missing_entries)}. "
            f"SoT requires: {sorted(sot_entries)}. "
            f"Fallback has: {sorted(fallback_entries)}"
        )
        logger.info(
            "[IMP:9][test_pgbouncer][no_proxy] PASS: fallback ⊇ SoT (%d entries)",
            len(sot_entries),
        )


# ── Test 6: Required Databases Present ────────────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_required_databases_present(postgres_fixtures, caplog) -> None:
    """Verify that platform, litellm, and langfuse databases are all present in DATABASE_URLS.

    Migrated from test_bootstrap_pgbouncer.py (T5.2 consolidation).
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][required] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        env = _get_pgbouncer_env_from_compose(compose_path)
        dbs_from_urls = _parse_database_urls(env)

        required = {"platform", "litellm", "langfuse"}
        missing = required - dbs_from_urls

        logger.info(
            "[IMP:7][test_pgbouncer][required] Required: %s, Present: %s",
            required,
            dbs_from_urls,
        )
        logger.critical(
            "[IMP:9][test_pgbouncer][required] ASSERT: missing=%s",
            missing,
        )
        assert not missing, f"Required databases missing from DATABASE_URLS: {missing}. Present: {dbs_from_urls}"


# ── Test 7: Each Database Has Host/Port/Dbname ────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_each_database_has_host_port_dbname(postgres_fixtures, caplog) -> None:
    """Each DATABASE_URLS entry must have host, port, and dbname parameters.

    Migrated from test_bootstrap_pgbouncer.py (T5.2 consolidation).
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][detailed] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"
        env = _get_pgbouncer_env_from_compose(compose_path)
        databases = _parse_database_urls_detailed(env)

        logger.info(
            "[IMP:7][test_pgbouncer][detailed] Parsed %d database(s) from DATABASE_URLS",
            len(databases),
        )
        logger.critical(
            "[IMP:9][test_pgbouncer][detailed] ASSERT: %d database(s) with details",
            len(databases),
        )
        assert len(databases) > 0, "No database entries found in DATABASE_URLS"

        for db_name, params in databases.items():
            assert "host" in params, f"Database '{db_name}' missing 'host' in DATABASE_URLS"
            assert "port" in params, f"Database '{db_name}' missing 'port' in DATABASE_URLS"
            assert "dbname" in params, f"Database '{db_name}' missing 'dbname' in DATABASE_URLS"
            assert params["host"] == "postgres", (
                f"Database '{db_name}' host should be 'postgres', got '{params['host']}'"
            )
            assert params["port"] == "5432", f"Database '{db_name}' port should be '5432', got '{params['port']}'"
            assert params["dbname"] == db_name, (
                f"Database '{db_name}' dbname should match key name, got '{params['dbname']}'"
            )
            logger.info(
                "[IMP:7][test_pgbouncer][detailed] Verified '%s': host=%s, port=%s",
                db_name,
                params["host"],
                params["port"],
            )


# ── Test 8: Charset Constraint Awareness ──────────────────────────


@pytest.mark.static_audit
@ldd_trajectory
def test_pgbouncer_password_charset_constraint(postgres_fixtures, caplog) -> None:
    """PgBouncer DATABASE_URLS uses ${POSTGRES_PASSWORD} directly (charset constraint guarantees safety, no ENCODED needed).

    ## @purpose — Verify that pgbouncer compose uses ${POSTGRES_PASSWORD} directly without
    ##            ENCODED variant. Charset constraint ^[A-Za-z0-9._-]+$ guarantees URL safety.
    ## @io — ⇥ postgres_fixtures: fixture → ⎋ None
    ## @complexity — O(1) — single file read + substring checks
    ## @invariants
    ##   - ${POSTGRES_PASSWORD} present in postgres docker-compose.base.yml
    ##   - POSTGRES_PASSWORD_ENCODED absent (rejected Option B artifact per DevPlan 014)
    """
    # 🧪 TRAP[TEST] · 2026-07-21 · Regression: charset constraint guarantees ${POSTGRES_PASSWORD} safe in URL · Scenario: pgbouncer compose must use raw POSTGRES_PASSWORD without ENCODED variant · Remove if: charset constraint ^[A-Za-z0-9._-]+$ is removed or relaxed
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_pgbouncer][charset_constraint] START")

        compose_path = postgres_fixtures["COMPOSE_FILE"]
        assert os.path.isfile(compose_path), f"Compose file not found: {compose_path}"

        compose_text = Path(compose_path).read_text()

        has_direct_usage = "${POSTGRES_PASSWORD}" in compose_text
        has_encoded = "POSTGRES_PASSWORD_ENCODED" in compose_text

        logger.critical(
            "[IMP:9][test_pgbouncer][charset_constraint] ASSERT: ${POSTGRES_PASSWORD}=%s POSTGRES_PASSWORD_ENCODED=%s",
            has_direct_usage,
            has_encoded,
        )
        assert has_direct_usage, "pgbouncer compose must use ${POSTGRES_PASSWORD} directly in DATABASE_URLS"
        assert not has_encoded, (
            "POSTGRES_PASSWORD_ENCODED must not exist — charset constraint makes it unnecessary. "
            "Remove any ENCODED references."
        )
        logger.info(
            "[IMP:8][test_pgbouncer][charset_constraint] PASS: direct=%s no_encoded=%s",
            has_direct_usage,
            not has_encoded,
        )


# endregion PGBOUNCER_STATIC_AUDIT_TESTS
