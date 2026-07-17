# GREP_SUMMARY: test-smoke-postgres smoke docker-compose healthcheck pg_isready postgres-test pgbouncer-test containers-healthy
# STRUCTURE: ▶ fixture(_docker_guard + foreign_guard) → fixture(postgres_up) → test_smoke_postgres_containers_healthy(◇ docker inspect health) → test_smoke_pgbouncer_pg_isready_6432(◇ docker exec) → ∑ IMP:9 logs
# region MODULE_CONTRACT
## @purpose  Smoke tests for postgres+pgbouncer compose stack — verifies container health
##           and TCP connectivity using real Docker containers.
##           Replicates healthcheck.sh deep checks because that script has hardcoded
##           base container names (postgres/pgbouncer) and cannot be called against
##           the -test stack (postgres-test/pgbouncer-test). See TRAP[DEBT] in healthcheck.sh.
## @scope    Requires Docker daemon. Uses module-scoped fixture that starts
##           postgres + pgbouncer from postgres/docker-compose.base.yml + test override.
##           All tests are marked @pytest.mark.smoke.
## @invariants
##   - postgres-test container reports healthy via Docker healthcheck
##   - pgbouncer-test container reports healthy via Docker healthcheck
##   - pg_isready on port 6432 in pgbouncer-test responds with "accepting connections"
##   - DB_NET_NAME=smoke-postgres-db-net — isolated external network (B6 T5.2)
##   - Foreign container guard: skip if postgres-test or pgbouncer-test belongs to
##     a different compose project (prevents cross-session conflicts)
##   - Safety guard: skip if hostname matches known production patterns
##   - Docker guard: skip if Docker daemon is unavailable
##   - At least one IMP:9 log per test per §TESTING LDD requirement
## @rationale Q: Why not call healthcheck.sh directly?
##            A: healthcheck.sh (core/modules/postgres/healthcheck.sh) has
##               POSTGRES_CONTAINER="postgres" and PGBOUNCER_CONTAINER="pgbouncer"
##               hardcoded. Against the -test stack (postgres-test/pgbouncer-test),
##               the script would inspect non-existent containers. Smoke tests
##               replicate the deep-check logic inline with correct -test names.
##            Q: Why smoke instead of component?
##            A: Smoke tests are a subset of component tests — they verify only
##               health and basic connectivity without full database registration.
##               They provide a fast feedback loop (<30s) for deployment verification.
##               They are marked @pytest.mark.smoke to be runnable as a subset.
##            Q: Why complex pre-flight (foreign container check)?
##            A: During parallel test sessions, a container named postgres-test
##               may exist under a different compose project. Starting a new compose
##               project with the same container name would fail with "container name
##               already in use". Foreign check prevents this by skipping instead of
##               failing, allowing the other session to complete undisturbed.
## @changes — CREATED: 2026-07-15 | T5.2: smoke tests for postgres module
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

logger = logging.getLogger(__name__)

from conftest import _ensure_volume_dirs, ensure_external_networks, is_production_host, ldd_trajectory

# ── Constants ─────────────────────────────────────────────────────────────────
COMPOSE_PROJECT_SMOKE = "ai-platform-smoke-postgres"
CONTAINER_POSTGRES = "postgres-test"
CONTAINER_PGBOUNCER = "pgbouncer-test"
COMPOSE_DIR = os.path.join(os.path.dirname(__file__), "..", "core", "modules", "postgres")

_DB_NET_NAME = "smoke-postgres-db-net"

# Environment for postgres + pgbouncer compose
# COMPOSE_PROFILES=postgres required because docker-compose.base.yml has profiles: [postgres]
# on both services — without it, "docker compose up -d" gives "no service selected" (B5 fix T5.2)
_SMOKE_ENV: dict[str, str] = {
    "PLATFORM_DOMAIN": "smoke.local",
    "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT_SMOKE,
    "COMPOSE_PROFILES": "postgres",
    "DB_NET_NAME": _DB_NET_NAME,
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "smoke-pg-pwd",
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


# region FIXTURES


@pytest.fixture(scope="module", autouse=True)
def _docker_guard() -> None:
    """Skip entire module if Docker daemon unavailable, host is production,
    or foreign containers occupy the required container names.

    ## @purpose — Module-level safety gate. Runs first among all fixtures.
    ## @io — ⎋ None (side-effect: pytest.skip or pass)
    ## @complexity — O(1) — single docker info call + hostname check + container inspect
    ## @invariants
    ##   - Skip if production host
    ##   - Skip if Docker daemon unavailable
    ##   - Skip if postgres-test or pgbouncer-test exists under a different compose project
    """
    # ── Production guard ────────────────────────────────────────────────
    if is_production_host():
        pytest.skip("Production host detected — skip postgres smoke tests")

    # ── Docker daemon guard ─────────────────────────────────────────────
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip("Docker daemon not available — skip postgres smoke tests")
    logger.info("[IMP:9][_docker_guard] Docker daemon available, host is not production")

    # ── Foreign container guard ─────────────────────────────────────────
    # If a container with -test suffix exists under a DIFFERENT compose project,
    # skip to avoid "container name already in use" errors.
    for container_name in (CONTAINER_POSTGRES, CONTAINER_PGBOUNCER):
        inspect_result = subprocess.run(
            [
                "docker",
                "inspect",
                container_name,
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if inspect_result.returncode == 0:
            project = inspect_result.stdout.strip()
            if project and project != COMPOSE_PROJECT_SMOKE:
                pytest.skip(
                    f"Foreign container '{container_name}' belongs to project "
                    f"'{project}', not '{COMPOSE_PROJECT_SMOKE}' — skip smoke"
                )
    logger.info("[IMP:9][_docker_guard] No foreign containers detected")


@pytest.fixture(scope="module")
def postgres_up() -> None:
    """Start postgres + pgbouncer compose stack for smoke testing.

    ## @purpose — Module-scoped lifecycle fixture. Creates networks, starts
    ##            compose, waits for both containers to be healthy.
    ##            Teardown: docker compose down -v.
    ## @io — ⎋ None (side-effect: Docker containers)
    ## @complexity — O(1) — single compose up + poll loops
    ## @invariants
    ##   - Volume bind-mount dirs are created before compose up
    ##   - External networks are pre-created
    ##   - Compose up is called with -f base.yml -f test.yml
    ##   - Both postgres and pgbouncer must become healthy within timeout
    ##   - On teardown, docker compose down -v removes all containers + volumes
    ## @rationale Replicates pgbouncer_up from test_component_pgbouncer.py but
    ##            without post-start database registration (smoke = health only).
    """
    compose_base = os.path.join(COMPOSE_DIR, "docker-compose.base.yml")
    compose_test = os.path.join(COMPOSE_DIR, "docker-compose.test.yml")

    if not os.path.exists(compose_base):
        pytest.skip(f"postgres docker-compose.base.yml not found: {compose_base}")

    # ── Ensure volume bind-mount directories ────────────────────────────
    _ensure_volume_dirs(_VOLUME_BIND_DIRS)

    # ── Ensure external Docker networks ─────────────────────────────────
    ensure_external_networks(_EXTERNAL_NETWORKS)

    # ── Pre-flight cleanup of own project ───────────────────────────────
    logger.info("[IMP:7][postgres_up] Pre-flight: cleaning up leftover containers ...")
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            compose_base,
            "-f",
            compose_test,
            "--project-name",
            COMPOSE_PROJECT_SMOKE,
            "down",
            "--timeout",
            "5",
        ],
        env={**os.environ, **_SMOKE_ENV},
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.info("[IMP:9][postgres_up] Pre-flight cleanup complete")

    # ── Start compose stack ─────────────────────────────────────────────
    logger.info(
        "[IMP:7][postgres_up] Starting compose from %s + %s ...",
        compose_base,
        compose_test,
    )
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                compose_base,
                "-f",
                compose_test,
                "--project-name",
                COMPOSE_PROJECT_SMOKE,
                "up",
                "-d",
            ],
            env={**os.environ, **_SMOKE_ENV},
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        logger.info(
            "[IMP:9][postgres_up] Compose up succeeded: %s",
            result.stdout.strip()[:200],
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr_tail = getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][postgres_up] Compose up failed: %s", exc)
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "network=shared-db-net", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        logger.info(
            "[IMP:7][postgres_up] Containers on shared-db-net:\n%s",
            ps_result.stdout,
        )
        pytest.fail(f"Postgres/pgbouncer compose up failed: {stderr_tail}")

    # ── Wait for both containers to be healthy ──────────────────────────
    # Poll docker inspect health status for up to 60s
    max_retries = 20
    retry_interval = 3
    all_healthy = False

    for attempt in range(1, max_retries + 1):
        statuses = {}
        for container_name in (CONTAINER_POSTGRES, CONTAINER_PGBOUNCER):
            health = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    container_name,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            statuses[container_name] = health.stdout.strip()

        if all(s == "healthy" for s in statuses.values()):
            all_healthy = True
            logger.info(
                "[IMP:9][postgres_up] Both containers healthy (attempt %d): %s",
                attempt,
                statuses,
            )
            break
        else:
            logger.info(
                "[IMP:7][postgres_up] Status (attempt %d/%d): %s",
                attempt,
                max_retries,
                statuses,
            )
        time.sleep(retry_interval)

    if not all_healthy:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={COMPOSE_PROJECT_SMOKE}", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pytest.fail(f"Containers NOT healthy after {max_retries * retry_interval}s\n{ps_result.stdout.strip()}")

    yield

    # ── Teardown: docker compose down -v + remove isolated network ──────
    logger.info("[IMP:7][postgres_up] Tearing down compose stack ...")
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            compose_base,
            "-f",
            compose_test,
            "--project-name",
            COMPOSE_PROJECT_SMOKE,
            "down",
            "-v",
            "--timeout",
            "5",
        ],
        env={**os.environ, **_SMOKE_ENV},
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
    logger.info("[IMP:9][postgres_up] Compose stack + isolated network torn down")


# endregion FIXTURES


# region TESTS

# ══════════════════════════════════════════════════════════════════════════════
# Test 1: postgres + pgbouncer Containers Healthy
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_smoke_postgres_containers_healthy
## @purpose — Verify both postgres-test and pgbouncer-test containers report
##            "healthy" via Docker healthcheck.
## @io — ⇥ caplog, postgres_up → ⎋ None (asserts health status contains "healthy")
## @complexity — O(1) — single docker inspect call per container
## @invariants
##   - docker inspect postgres-test State.Health.Status must be "healthy"
##   - docker inspect pgbouncer-test State.Health.Status must be "healthy"


@pytest.mark.smoke
@ldd_trajectory
def test_smoke_postgres_containers_healthy(caplog, postgres_up) -> None:
    """Verify both postgres-test and pgbouncer-test are healthy via docker inspect."""
    logger.info(
        "[IMP:7][test_smoke_postgres_containers_healthy] Checking health status for %s and %s ...",
        CONTAINER_POSTGRES,
        CONTAINER_PGBOUNCER,
    )

    for container_name in (CONTAINER_POSTGRES, CONTAINER_PGBOUNCER):
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        status = result.stdout.strip()
        logger.info(
            "[IMP:8][test_smoke_postgres_containers_healthy] %s health=%s",
            container_name,
            status,
        )
        assert status == "healthy", f"Container '{container_name}' is not healthy. Status: '{status}'"
        logger.critical(
            "[IMP:9][test_smoke_postgres_containers_healthy] ✅ %s is healthy",
            container_name,
        )


# endregion FUNC_test_smoke_postgres_containers_healthy


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: pgbouncer pg_isready on Port 6432
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_smoke_pgbouncer_pg_isready_6432
## @purpose — Verify pgbouncer accepts TCP connections on port 6432 via
##            docker exec pg_isready — replicates healthcheck.sh deep check
##            with correct -test container name.
## @io — ⇥ caplog, postgres_up → ⎋ None (asserts pg_isready exit 0)
## @complexity — O(1) — single docker exec call
## @invariants
##   - docker exec pgbouncer-test pg_isready -p 6432 must exit 0
##   - Output must contain "accepting connections"


@pytest.mark.smoke
@ldd_trajectory
def test_smoke_pgbouncer_pg_isready_6432(caplog, postgres_up) -> None:
    """Verify pgbouncer accepts TCP connections on port 6432 via pg_isready.

    Replicates healthcheck.sh deep check against pgbouncer-test container.
    """
    # ◇ docker exec pgbouncer-test pg_isready → ⊕ exit 0 → ⎋ pass
    logger.info(
        "[IMP:7][test_smoke_pgbouncer_pg_isready_6432] Running pg_isready through pgbouncer-test on port 6432 ..."
    )

    result = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER_PGBOUNCER,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-p",
            "6432",
            "-U",
            "postgres",
            "-t",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    logger.info(
        "[IMP:8][test_smoke_pgbouncer_pg_isready_6432] stdout: %s  stderr: %s",
        result.stdout.strip(),
        result.stderr.strip(),
    )

    assert result.returncode == 0, (
        f"pg_isready on pgbouncer port 6432 failed "
        f"(exit {result.returncode}):\n"
        f"stdout: {result.stdout.strip()}\n"
        f"stderr: {result.stderr.strip()}"
    )
    assert "accepting connections" in result.stdout, (
        f"pg_isready output does not contain 'accepting connections':\n{result.stdout.strip()}"
    )
    logger.critical("[IMP:9][test_smoke_pgbouncer_pg_isready_6432] ✅ pgbouncer accepts connections on port 6432")


# endregion FUNC_test_smoke_pgbouncer_pg_isready_6432


# endregion TESTS
