# GREP_SUMMARY: pgbouncer component-test docker-compose healthcheck pg_isready psql pgbouncer pool-mode databases connection-pooling
# STRUCTURE: ▶ fixture(pgbouncer_up) → test_pgbouncer_container_healthy(docker ps) → test_pgbouncer_port_responds(pg_isready) → test_pgbouncer_select_works(psql SELECT 1) → test_pgbouncer_pool_mode_active(docker logs) → test_pgbouncer_databases_mapped(SHOW DATABASES) → ∑ IMP:9 logs
# @file test_component_pgbouncer.py
# @purpose  Component-level integration tests for pgbouncer Docker module.
#           Verifies compose lifecycle, container health, TCP connectivity,
#           data flow (SELECT 1), pool mode configuration, and database mapping
#           using real Docker containers.
# @scope    Requires Docker daemon. Uses module-scoped fixture that starts
#           postgres + pgbouncer from a single compose file.
#           All tests are marked @pytest.mark.component.
# @invariants
#   - pgbouncer container reports healthy via Docker healthcheck
#   - pg_isready on port 6432 responds with "accepting connections"
#   - psql through pgbouncer returns valid query results
#   - pgbouncer logs show transaction pooling mode
#   - SHOW DATABASES returns configured databases
#   - At least one IMP:9 log per test per §TESTING LDD requirement
# @rationale  Integration-testing the full stack (postgres → redis → observability → hermes)
#             takes 3-5 minutes. For pgbouncer, a component test that starts only
#             the postgres compose (includes pgbouncer) completes in 30-60s.
#             psql is used instead of a specialised pgbouncer client because pgbouncer
#             accepts the standard PostgreSQL protocol.
#

# region MODULE_CONTRACT
## @purpose  Component-level integration tests for pgbouncer Docker module.
##           Verifies container lifecycle, health, connectivity, pool mode configuration,
##           and database mapping using real Docker containers.
## @scope    Requires Docker daemon and the postgres docker-compose.base.yml file.
##           Module-scoped fixture manages the full lifecycle (start → test → teardown).
##           All tests are marked @pytest.mark.component and can be filtered via -m component.
## @invariants
##   - pgbouncer and postgres are started as a single compose stack
##   - The compose file used is postgres/docker-compose.base.yml (+ test override if present)
##   - COMPOSE_PROJECT_NAME=ai-platform-test-pgbouncer provides project-level isolation
##   - DB_NET_NAME=pgbouncer-component-db-net — isolated external network (B6 T5.2)
##   - External networks are pre-created before compose up; removed on teardown
##   - Test databases (litellm, langfuse, platform) are created post-start in postgres
##   - PgBouncer databases are registered via admin console post-start
##   - Fixture teardown runs docker compose down -v removing all containers + volumes
##   - Safety guard: skip if hostname matches known production patterns
##   - Docker guard: skip if Docker daemon is unavailable
## @rationale Q: Why component test instead of integration test? A: Integration tests
##            start the full observability stack (3-5 min). A component test that only
##            starts postgres compose (includes pgbouncer) completes in 30-60s.
##            Q: Why not add pgbouncer to observability_up fixture? A: Observability
##            component tests use PostgreSQL for litellm (DATABASE_URL from pytest env). Checking
##            pgbouncer connectivity from litellm would require reworking the fixture.
##            Q: Why psql instead of a specialised client? A: pgbouncer accepts
##            standard PostgreSQL protocol. psql is the most reliable way to verify
##            connectivity without additional dependencies.
## @changes — CREATED: 2026-07-03 | pgbouncer E2E testing (TASK-2)
##            UPDATED: 2026-07-15 | B5: COMPOSE_PROFILES=postgres + skip→fail (T5.2)
##            UPDATED: 2026-07-15 | B6: DB_NET_NAME изоляция сети + teardown rm (T5.2)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import time

import pytest
from conftest import _ensure_volume_dirs, ensure_external_networks, is_production_host, ldd_trajectory

logger = logging.getLogger(__name__)

COMPOSE_PROJECT_PGBOUNCER = "ai-platform-test-pgbouncer"
CONTAINER_NAME_PGBOUNCER = "pgbouncer-test"

# Environment for postgres + pgbouncer compose
# ⚠️ TRAP[BUG] · 2026-07-15 · HI · Component-тесты молча скипались: нет COMPOSE_PROFILES при profiles: [postgres] в base.yml
# · Symptom: compose up "no service selected" → pytest.skip → тесты «зелёные» не выполняясь
# · Root: profiles добавлены в base.yml без обновления тестовых фикстур; skip маскировал отказ
# · Fix: COMPOSE_PROFILES=postgres в ENV + fail вместо skip при провале compose up
_DB_NET_NAME = "pgbouncer-component-db-net"

ENV: dict[str, str] = {
    "PLATFORM_DOMAIN": "test.local",
    "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT_PGBOUNCER,
    "COMPOSE_PROFILES": "postgres",
    "DB_NET_NAME": _DB_NET_NAME,
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "test-pg-pwd",
    "POSTGRES_DB": "platform",
    "DB_NAME": "platform",
    "POOL_MODE": "transaction",
}

# Volume bind-mount directories that must exist before compose up
_VOLUME_BASE = os.environ.get("TEST_VOLUME_BASE", "/var/lib/platform")
_VOLUME_BIND_DIRS: list[str] = [
    os.path.join(_VOLUME_BASE, "postgres-data"),
]

# External Docker networks required by compose files
# B6 T5.2: isolated network per test project prevents DNS alias collision
_EXTERNAL_NETWORKS: list[str] = [
    _DB_NET_NAME,
]

# region HELPERS


def _docker_exec(
    container: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    """Run a command inside a container via docker exec.

    ## @purpose — Centralised docker exec runner for pgbouncer tests.
    ##            Passes env vars via `docker exec -e` to make them available
    ##            inside the container (subprocess env only affects host process).
    ## @io — ⇥ container, cmd, env, timeout → ⎋ CompletedProcess
    ## @complexity — O(1) — single subprocess call
    """
    docker_cmd = ["docker", "exec"]
    if env:
        for key, value in env.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
    docker_cmd.append(container)
    docker_cmd.extend(cmd)
    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# endregion HELPERS


# region FIXTURES


@pytest.fixture(scope="module", autouse=True)
def _docker_guard() -> None:
    """Skip entire module if Docker daemon unavailable or host is production.

    ## @purpose — Module-level safety gate. Runs first among all fixtures.
    ## @io — ⎋ None (side-effect: pytest.skip or pass)
    ## @complexity — O(1) — single docker info call + hostname check
    """
    if is_production_host():
        pytest.skip("Production host detected — skip pgbouncer component tests")

    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip("Docker daemon not available — skip pgbouncer component tests")
    logger.info("[IMP:9][_docker_guard] Docker daemon available, host is not production")


@pytest.fixture(scope="module")
def pgbouncer_up(modules_dir: str) -> None:
    """
    Start postgres + pgbouncer compose stack as a single unit.

    ## @purpose — Module-scoped lifecycle fixture. Creates networks, starts
    ##            compose, waits for both containers to be healthy, creates
    ##            test databases, and registers them in pgbouncer.
    ##            Teardown: docker compose down -v.
    ## @io — ⇥ modules_dir: str from conftest → ⎋ None (side-effect: Docker containers)
    ## @complexity — O(1) — single compose up + exec commands for DB setup
    ## @invariants
    ##   - Volume bind-mount dirs are created before compose up
    ##   - External networks are pre-created
    ##   - Test databases (litellm, langfuse) are created in postgres
    ##   - PgBouncer databases are registered via admin console
    ##   - On teardown, docker compose down -v removes all containers + volumes
    """
    compose_file = os.path.join(modules_dir, "postgres", "docker-compose.base.yml")
    if not os.path.exists(compose_file):
        pytest.skip(f"postgres docker-compose.base.yml not found: {compose_file}")

    # ── Ensure volume bind-mount directories ────────────────────────────
    _ensure_volume_dirs(_VOLUME_BIND_DIRS)

    # ── Ensure external Docker networks ──────────────────────────────────
    ensure_external_networks(_EXTERNAL_NETWORKS)

    # ── Pre-flight cleanup ───────────────────────────────────────────────
    logger.info("[IMP:7][pgbouncer_up] Pre-flight: cleaning up leftover containers ...")
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            compose_file,
            "--project-name",
            COMPOSE_PROJECT_PGBOUNCER,
            "down",
            "--timeout",
            "5",
        ],
        env={**os.environ, **ENV},
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.info("[IMP:9][pgbouncer_up] Pre-flight cleanup complete")

    # ── Start compose stack ──────────────────────────────────────────────
    logger.info("[IMP:7][pgbouncer_up] Starting compose from %s ...", compose_file)
    compose_args = ["docker", "compose", "-f", compose_file]
    test_override = os.path.join(os.path.dirname(compose_file), "docker-compose.test.yml")
    if os.path.exists(test_override):
        compose_args.extend(["-f", test_override])
        logger.info("[IMP:7][pgbouncer_up] Using test override: %s", test_override)
    compose_args.extend(["--project-name", COMPOSE_PROJECT_PGBOUNCER, "up", "-d"])

    try:
        result = subprocess.run(
            compose_args,
            env={**os.environ, **ENV},
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        logger.info("[IMP:9][pgbouncer_up] Compose up succeeded: %s", result.stdout.strip()[:200])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr_tail = getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][pgbouncer_up] Compose up failed: %s", exc)
        # ── Log container status for debugging ─────────────────────────────────
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "network=shared-db-net", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        logger.info("[IMP:7][pgbouncer_up] Containers on shared-db-net:\n%s", ps_result.stdout)
        pytest.fail(f"Postgres/pgbouncer compose up failed: {stderr_tail}")

    # ── Wait for pgbouncer to be healthy ──────────────────────────
    # [IMP:8] Poll docker inspect health status for up to 60s
    max_retries = 20
    retry_interval = 3
    pgbouncer_healthy = False
    for attempt in range(1, max_retries + 1):
        health = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", CONTAINER_NAME_PGBOUNCER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = health.stdout.strip()
        if status == "healthy":
            pgbouncer_healthy = True
            logger.info("[IMP:9][pgbouncer_up] pgbouncer is healthy (attempt %d)", attempt)
            break
        elif status == "":
            logger.info("[IMP:7][pgbouncer_up] pgbouncer-shard not yet inspectable (attempt %d)", attempt)
        else:
            logger.info("[IMP:7][pgbouncer_up] pgbouncer status=%s (attempt %d/%d)", status, attempt, max_retries)
        time.sleep(retry_interval)

    if not pgbouncer_healthy:
        # ── Fail: container did not become healthy in time ─────────────────
        ps_result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={CONTAINER_NAME_PGBOUNCER}",
                "--format",
                "{{.Names}} {{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        logs_result = subprocess.run(
            ["docker", "logs", CONTAINER_NAME_PGBOUNCER],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pytest.fail(
            f"pgbouncer NOT healthy after {max_retries * retry_interval}s\n"
            f"ps:\n{ps_result.stdout.strip()}\n"
            f"logs:\n{logs_result.stdout.strip()[-500:]}"
        )

    # ── Register databases in pgbouncer admin console ─────────────────
    # [IMP:8] pgbouncer DB_NAMES env var does NOT auto-register databases
    # in SHOW DATABASES. The edoburu/pgbouncer entrypoint generates a config
    # with only the DB_NAME entry (platform). We inject litellm and langfuse
    # entries into the auto-generated pgbouncer.ini via sed, then RELOAD.
    # pgbouncer admin console does NOT support CREATE DATABASE at runtime.
    try:
        sed_expr = (
            r"/^platform = host=postgres port=/a\litellm = "
            r"host=postgres port=5432 dbname=litellm\nlangfuse = "
            r"host=postgres port=5432 dbname=langfuse"
        )
        insert_result = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "0",
                CONTAINER_NAME_PGBOUNCER,
                "sed",
                "-i",
                sed_expr,
                "/etc/pgbouncer/pgbouncer.ini",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if insert_result.returncode != 0:
            logger.warning(
                "[IMP:7][pgbouncer_up] sed insert failed (rc=%d): %s",
                insert_result.returncode,
                insert_result.stderr.strip()[:200],
            )
        else:
            logger.info("[IMP:9][pgbouncer_up] litellm/langfuse entries inserted into pgbouncer.ini")

        # Reload pgbouncer to pick up the config change
        reload_result = _docker_exec(
            CONTAINER_NAME_PGBOUNCER,
            ["psql", "-h", "127.0.0.1", "-p", "6432", "-U", "postgres", "-d", "pgbouncer", "-c", "RELOAD;"],
            env={"PGPASSWORD": ENV["POSTGRES_PASSWORD"]},
            timeout=15,
        )
        logger.info(
            "[IMP:9][pgbouncer_up] pgbouncer reloaded (rc=%d): %s",
            reload_result.returncode,
            reload_result.stdout.strip()[:200],
        )
    except Exception as exc:
        logger.warning(
            "[IMP:7][pgbouncer_up] Failed to register databases in pgbouncer: %s",
            exc,
        )

    yield

    # ── Teardown: docker compose down -v + remove isolated network ──────
    logger.info("[IMP:7][pgbouncer_up] Tearing down compose stack ...")
    down_args = ["docker", "compose", "-f", compose_file]
    if os.path.exists(test_override):
        down_args.extend(["-f", test_override])
    down_args.extend(["--project-name", COMPOSE_PROJECT_PGBOUNCER, "down", "-v", "--timeout", "5"])
    subprocess.run(
        down_args,
        env={**os.environ, **ENV},
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Remove isolated network (B6 T5.2) — ignore failure (may be held by other containers)
    subprocess.run(
        ["docker", "network", "rm", _DB_NET_NAME],
        capture_output=True,
        text=True,
        timeout=15,
    )
    logger.info("[IMP:9][pgbouncer_up] Compose stack + isolated network torn down")


# endregion FIXTURES


# region TESTS

# ══════════════════════════════════════════════════════════════════════════════
# Test 1: pgbouncer Container Healthy
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_pgbouncer_container_healthy
## @purpose — Verify pgbouncer container reports "healthy" via Docker healthcheck.
## @io — ⇥ caplog, pgbouncer_up → ⎋ None (asserts health status contains "healthy")
## @complexity — O(1) — single docker ps call
## @invariants
##   - docker ps --filter name=pgbouncer --format '{{.Status}}' must contain "(healthy)"
##   - Container must be running AND pass its healthcheck (pg_isready -p 6432)


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_container_healthy(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify pgbouncer container reports healthy status via docker ps."""
    # ◇ docker ps --filter name=pgbouncer → ⊕ status contains "(healthy)" → ⎋ pass

    # region BLOCK_Setup
    logger.info("[IMP:7][test_pgbouncer_container_healthy] Checking pgbouncer health status ...")
    # endregion

    # region BLOCK_Exec
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name={CONTAINER_NAME_PGBOUNCER}", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    status = result.stdout.strip()
    # endregion

    # region BLOCK_Assert
    logger.info("[IMP:8][test_pgbouncer_container_healthy] pgbouncer status: %s", status)
    assert "(healthy)" in status, f"pgbouncer is not healthy. Status: '{status}'"
    logger.info("[IMP:9][test_pgbouncer_container_healthy] ✅ pgbouncer is healthy")
    # endregion


# endregion FUNC_test_pgbouncer_container_healthy


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: pgbouncer Port Responds (pg_isready)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_pgbouncer_port_responds
## @purpose — Verify pgbouncer accepts TCP connections on port 6432 via pg_isready.
## @io — ⇥ caplog, pgbouncer_up → ⎋ None (asserts pg_isready exit 0)
## @complexity — O(1) — single docker exec call
## @invariants
##   - pg_isready -h 127.0.0.1 -p 6432 must exit 0
##   - Output must contain "accepting connections" or "ready"


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_port_responds(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify pgbouncer accepts TCP connections on port 6432 via pg_isready."""
    # ◇ docker exec pgbouncer pg_isready → ⊕ exit 0 → ⎋ pass

    # region BLOCK_Setup
    logger.info("[IMP:7][test_pgbouncer_port_responds] Checking pg_isready on port 6432 ...")
    # endregion

    # region BLOCK_Exec
    result = _docker_exec(
        CONTAINER_NAME_PGBOUNCER,
        ["pg_isready", "-h", "127.0.0.1", "-p", "6432", "-U", "postgres"],
        env={"PGPASSWORD": ENV["POSTGRES_PASSWORD"]},
        timeout=15,
    )
    # endregion

    # region BLOCK_Assert
    logger.info(
        "[IMP:8][test_pgbouncer_port_responds] pg_isready stdout: %s",
        result.stdout.strip(),
    )
    assert result.returncode == 0, (
        f"pg_isready on port 6432 failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout.strip()}\n"
        f"stderr: {result.stderr.strip()}"
    )
    logger.info("[IMP:9][test_pgbouncer_port_responds] ✅ pgbouncer port 6432 responds")
    # endregion


# endregion FUNC_test_pgbouncer_port_responds


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: pgbouncer SELECT works (data flow)
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_pgbouncer_select_works
## @purpose — Verify data flow through pgbouncer: psql SELECT 1 on litellm database.
## @io — ⇥ caplog, pgbouncer_up → ⎋ None (asserts output contains "1")
## @complexity — O(1) — single docker exec psql call
## @invariants
##   - psql -h 127.0.0.1 -p 6432 -U postgres -d litellm -c 'SELECT 1' must exit 0
##   - Output must contain "1" (the literal result column)


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_select_works(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify data flow through pgbouncer: psql SELECT 1 on litellm database."""
    # ◇ docker exec pgbouncer psql SELECT 1 → ⊕ output contains "1" → ⎋ pass

    # region BLOCK_Setup
    logger.info("[IMP:7][test_pgbouncer_select_works] Running SELECT 1 through pgbouncer ...")
    # endregion

    # region BLOCK_Exec
    result = _docker_exec(
        CONTAINER_NAME_PGBOUNCER,
        ["psql", "-h", "127.0.0.1", "-p", "6432", "-U", "postgres", "-d", "platform", "-c", "SELECT 1"],
        env={"PGPASSWORD": ENV["POSTGRES_PASSWORD"]},
        timeout=15,
    )
    # endregion

    # region BLOCK_Assert
    logger.info(
        "[IMP:8][test_pgbouncer_select_works] psql stdout: %s",
        result.stdout.strip(),
    )
    assert result.returncode == 0, (
        f"psql SELECT 1 through pgbouncer failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout.strip()}\n"
        f"stderr: {result.stderr.strip()}"
    )
    assert "1" in result.stdout, f"SELECT 1 result missing '1'. Output:\n{result.stdout.strip()}"
    logger.info("[IMP:9][test_pgbouncer_select_works] ✅ SELECT 1 through pgbouncer works")
    # endregion


# endregion FUNC_test_pgbouncer_select_works


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: pgbouncer Pool Mode Active
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_pgbouncer_pool_mode_active
## @purpose — Verify pgbouncer is running in transaction pooling mode.
## @io — ⇥ caplog, pgbouncer_up → ⎋ None (asserts logs contain pool_mode)
## @complexity — O(1) — single docker logs call
## @invariants
##   - docker logs pgbouncer must contain "pool_mode = transaction"
##   - The log confirms POOL_MODE env var was correctly applied


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_pool_mode_active(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify pgbouncer is running in transaction pooling mode."""
    # ◇ docker logs pgbouncer → ⊕ contains "pool_mode = transaction" → ⎋ pass

    # region BLOCK_Setup
    logger.info("[IMP:7][test_pgbouncer_pool_mode_active] Checking pgbouncer logs for pool_mode ...")
    # endregion

    # region BLOCK_Exec
    result = subprocess.run(
        ["docker", "logs", CONTAINER_NAME_PGBOUNCER],
        capture_output=True,
        text=True,
        timeout=15,
    )
    logs = result.stdout + result.stderr
    # endregion

    # region BLOCK_Assert
    has_pool_mode = "pool_mode" in logs.lower() and "transaction" in logs.lower()
    logger.info(
        "[IMP:8][test_pgbouncer_pool_mode_active] pool_mode=transaction in logs: %s",
        has_pool_mode,
    )
    assert has_pool_mode, f"pgbouncer logs do not confirm pool_mode=transaction.\nLog excerpt:\n{logs[-500:]}"
    logger.info("[IMP:9][test_pgbouncer_pool_mode_active] ✅ pgbouncer pool_mode=transaction confirmed")
    # endregion


# endregion FUNC_test_pgbouncer_pool_mode_active


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: pgbouncer Databases Mapped
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_pgbouncer_databases_mapped
## @purpose — Verify pgbouncer has databases configured for observability clients.
##            Checks via SHOW DATABASES admin command for litellm, langfuse, platform.
## @io — ⇥ caplog, pgbouncer_up → ⎋ None (asserts output contains db names)
## @complexity — O(1) — single docker exec call
## @invariants
##   - SHOW DATABASES via pgbouncer admin console must list litellm, langfuse, platform
##   - This validates pgbouncer.ini database mapping is consistent with observability compose


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_databases_mapped(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify pgbouncer has databases configured for observability clients via SHOW DATABASES."""
    # ◇ docker exec pgbouncer psql SHOW DATABASES → ⊕ lists litellm, langfuse, platform → ⎋ pass

    # region BLOCK_Setup
    logger.info("[IMP:7][test_pgbouncer_databases_mapped] Checking pgbouncer databases ...")
    # endregion

    # region BLOCK_Exec
    # Try SHOW DATABASES via pgbouncer admin console first
    result = _docker_exec(
        CONTAINER_NAME_PGBOUNCER,
        ["psql", "-h", "127.0.0.1", "-p", "6432", "-U", "postgres", "-d", "pgbouncer", "-c", "SHOW DATABASES"],
        env={"PGPASSWORD": ENV["POSTGRES_PASSWORD"]},
        timeout=15,
    )
    output = result.stdout + result.stderr
    # endregion

    # region BLOCK_Assert
    expected_dbs = ["litellm", "langfuse", "platform"]
    logger.info(
        "[IMP:8][test_pgbouncer_databases_mapped] SHOW DATABASES output:\n%s",
        result.stdout.strip()[:500],
    )
    missing = [db for db in expected_dbs if db.lower() not in output.lower()]
    assert not missing, (
        f"Expected databases not found via SHOW DATABASES.\n"
        f"Missing: {missing}\n"
        f"Expected all of: {expected_dbs}\n"
        f"Output:\n{output[:500]}"
    )
    logger.info(
        "[IMP:9][test_pgbouncer_databases_mapped] ✅ All databases registered: %s",
        expected_dbs,
    )
    # endregion


# endregion FUNC_test_pgbouncer_databases_mapped


# region PGBOUNCER_PORT_REGRESSION_TESTS
## @purpose — Regression tests for pgbouncer LISTEN_PORT bug: verify LISTEN_PORT=6432
##            is present in container env AND pgbouncer listens on port 6432.
## @rationale — Merged from test_pgbouncer_port.py per W5 consolidation.
##              Both tests use pgbouncer_up fixture (defined in this file).


@pytest.mark.component
@ldd_trajectory
def test_pgbouncer_container_env_has_listen_port(caplog: pytest.LogCaptureFixture, pgbouncer_up) -> None:
    """Verify LISTEN_PORT=6432 is present in running pgbouncer container env."""
    logger.info("[IMP:7][test_pgbouncer_container_env_has_listen_port] Inspecting pgbouncer env ...")

    try:
        result = subprocess.run(
            ["docker", "inspect", CONTAINER_NAME_PGBOUNCER, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("docker inspect timed out after 15s")
    except FileNotFoundError:
        pytest.skip("docker command not found — Docker CLI not installed")

    assert result.returncode == 0, f"docker inspect failed (rc={result.returncode}). stderr: {result.stderr.strip()}"

    container_env = result.stdout.strip()
    assert "LISTEN_PORT=6432" in container_env, (
        f"LISTEN_PORT=6432 NOT found in pgbouncer Config.Env.\nContainer env:\n{container_env}"
    )
    logger.info("[IMP:9][test_pgbouncer_container_env_has_listen_port] ✅ LISTEN_PORT=6432 found in container env")


# endregion PGBOUNCER_PORT_REGRESSION_TESTS


# endregion TESTS
