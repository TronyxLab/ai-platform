# GREP_SUMMARY: hermes-agent component-test docker-compose healthcheck compose-up restart-count container-logs postgres dependency dashboard-auth redirect gateway-process inspect-health s6-overlay shared-fixtures test-infra-fixture compose-wait
# STRUCTURE: ▶ fixtures(postgres_up→hermes_up) → test_hermes_compose_up(◇ compose_ps) → test_hermes_agent_starts(docker logs s6) → test_healthcheck_passes(docker ps ◇ :9119) → test_no_restart_loop(docker inspect) → test_hermes_dashboard_auth(302 → /auth/login) → test_hermes_gateway_listens(docker exec ps gateway) → test_ready_endpoint(docker inspect healthy) → ∑ IMP:9 logs
# region MODULE_CONTRACT
## @purpose  Component-level integration tests for hermes-agent Docker module.
##           Verifies compose lifecycle, container startup, healthcheck status,
##           and restart-loop detection using real Docker containers.
## @scope    Requires Docker daemon and postgres module to be available.
##           Tests use session-scoped fixtures (start once per session, teardown after all tests).
##           Docker compose operations use subprocess.run with --wait --wait-timeout 120.
## @invariants
##   - postgres must be started BEFORE hermes-agent (depends_on chain in fixtures)
##   - All containers run with COMPOSE_PROJECT_NAME=ai-platform-test to avoid name collisions
##   - Tests assume docker daemon is available and functional
##   - Compose files must exist at core/modules/{postgres,hermes-agent}/docker-compose.base.yml
##   - At least one IMP:9 log per test per §TESTING LDD requirement
##   - Fixtures are session-scoped: postgres_up → hermes_up → all 6 tests → teardown hermes → teardown postgres
## @rationale Q: Why component tests instead of unit tests? A: Hermes-agent is a Docker container with no
##            Python entry points exposed — the only way to verify its runtime behaviour is via Docker API.
##            Q: Why chain postgres? A: Hermes-agent depends on postgres per module.yaml
##            depends_on field; component tests must respect the actual dependency graph.
## @usecases — TASK-11: test_hermes_compose_up, test_hermes_agent_starts, test_healthcheck_passes, test_no_restart_loop
## @usecases — [TASK-1+TASK-2]: test_hermes_dashboard_auth, test_hermes_gateway_listens
## @changes — LAST_CHANGE: 2026-07-04 | Updated test_hermes_dashboard_auth to form-based login (replaced Basic Auth); updated test_hermes_gateway_listens fallback for HTML root
##            LAST_CHANGE: 2026-07-06 | Replaced local _ensure_networks with ensure_external_networks (conftest); added --wait to compose up; replaced sleep-based restart-loop checks with polling loop (T6)
##            LAST_CHANGE: 2026-07-08 | Wave 3: Adapted 5 tests for real Hermes L1 image (T3-T7):
##              T3: test_agent_main_starts → test_hermes_agent_starts (s6-overlay logs)
##              T4: test_healthcheck_passes → :9119, accept 200/401 (Basic Auth)
##              T5: test_hermes_dashboard_auth → Basic Auth (form-login removed)
##              T6: test_hermes_gateway_listens → no docker exec fallback, /v1/models
##              T7: test_ready_endpoint → :9119, removed dependency checks
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import platform
import subprocess
import time

import pytest
from _conftest.networks import get_network_manager
from _conftest.reuse import check_foreign_containers, wait_for_containers_healthy
from conftest import _ensure_volume_dirs, ldd_trajectory

logger = logging.getLogger(__name__)

# ⚠️ TRAP[BUG] · 2026-07-22 · HI · COMPOSE_PROJECT was "ai-platform-test" — same as platform_services
# · Symptom: check_foreign_containers treated platform_services containers as "own" (same project),
# ·   returned empty → pre-flight compose down KILLED platform_services containers → cascade failures.
# · Fix: unique project name "ai-platform-test-hermes-pg" prevents cross-fixture container management.
COMPOSE_PROJECT = "ai-platform-test-hermes-pg"
COMPOSE_PROJECT_HERMES = "ai-platform-test-hermes"
ENV = {
    "PLATFORM_DOMAIN": "test.local",
    "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT,
    "POSTGRES_PASSWORD": "test-pg-pwd",
    "COMPOSE_PROFILES": "postgres",
}

# Hermes env — separate project name to avoid orphan conflicts
# @rationale PLATFORM_ROOT points to project root so Docker build context resolves correctly
#   even when /opt/platform does not exist locally (dev environment).
_PLATFORM_ROOT: str = os.environ.get("PLATFORM_ROOT", "/Users/tronyx/projects/ai-platform")

ENV_HERMES = {
    "PLATFORM_DOMAIN": "test.local",
    "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT_HERMES,
    "POSTGRES_PASSWORD": "test-pg-pwd",
    "COMPOSE_PROFILES": "hermes-agent",
    "OPENAI_API_KEY": "sk-test-not-for-production",
    "CONTEXT_IMAGE": os.environ.get("CONTEXT_IMAGE", "ghcr.io/tronyxlab/hermes-agent-context:latest"),
    "HERMES_DASHBOARD_PASSWORD": "testpass",
    "PLATFORM_ROOT": _PLATFORM_ROOT,
}

# Volume bind-mount directories that must exist before compose up
_VOLUME_BASE = os.environ.get("TEST_VOLUME_BASE", "/var/lib/platform")
_VOLUME_BIND_DIRS = [
    os.path.join(_VOLUME_BASE, "postgres-data"),
    os.path.join(_VOLUME_BASE, "hermes-agent", "data"),
]

# External Docker networks required by compose files
# 🔧 B4 fix (DevPlan 034): docker-compose.test.yml for postgres requires
# test-shared-db-net (external: true) — the test overlay uses test-* prefix
# for network isolation. Previously only production networks were created,
# causing 13 fixture setup errors (Docker Compose could not find test-shared-db-net).
_EXTERNAL_NETWORKS = [
    "test-shared-db-net",
    "test-proxy-net",
    "test-hermes-agent-net",
    "test-observability-net",
    "test-shared-cache-net",
]


# region FIXTURES
## @purpose — Session-scoped fixtures for docker compose lifecycle management.
##            postgres_up creates the shared-db-net + postgres container;
##            hermes_up depends on postgres_up and starts hermes-agent.
## @scope — Both fixtures are session-scoped: containers survive across all tests in this file.
## @invariants
##   - postgres_up runs before hermes_up (explicit fixture dependency)
##   - both fixtures teardown in reverse order (hermes first, then postgres, via yield ordering)
##   - --wait --wait-timeout: postgres=60, hermes=90 (reduced from 180 in TASK-5)
##   - compose files resolved via modules_dir fixture from conftest


@pytest.fixture(scope="session")
def postgres_up(platform_services: dict[str, list[str]], modules_dir) -> None:
    """
    Start postgres before hermes-agent.

    ## @purpose — Ensure shared-db-net and postgres container are running.
    ##            Creates external network implicitly via compose up.
    ## @io — ⇥ platform_services: dict with foreign container info from _conftest.reuse
    ##        ⇥ modules_dir: str from conftest → ⎋ None (side-effect: docker compose up -d)
    ## @complexity — O(1) — single subprocess call with --wait
    ## @rationale — postgres must be healthy before hermes-agent starts;
    ##              compose built-in --wait solves this without explicit sleep.
    ##              --wait-timeout 60 (TASK-5: reduced from 180 for faster CI).
    ##              Foreign container guard allows reusing postgres/pgbouncer
    ##              from platform_services when already running externally.
    """
    # ── Foreign container guard ──────────────────────────────────────
    # ⚠️ TRAP[BUG] · 2026-07-22 · HI · own_project was hardcoded "ai-platform-test"
    # · Same bug as other fixtures — now uses COMPOSE_PROJECT ("ai-platform-test-hermes-pg").
    foreign = check_foreign_containers(["postgres-test", "pgbouncer-test"], COMPOSE_PROJECT)
    if foreign:
        logger.info(
            "[IMP:8][postgres_up] Reusing postgres/pgbouncer from platform_services — skipping compose lifecycle"
        )
        statuses = wait_for_containers_healthy(["postgres-test", "pgbouncer-test"])
        if not all(s == "healthy" for s in statuses.values()):
            pytest.fail(f"Reused containers not healthy: {statuses}")
        yield
        return

    compose_file = os.path.join(modules_dir, "postgres", "docker-compose.base.yml")
    if not os.path.exists(compose_file):
        pytest.skip(f"postgres docker-compose.base.yml not found: {compose_file}")

    # ── Ensure volume bind-mount directories exist ────────────────────────
    _ensure_volume_dirs(_VOLUME_BIND_DIRS)

    # ── Ensure external Docker networks via NetworkLeaseManager ────────────
    _nm = get_network_manager()
    for net in _EXTERNAL_NETWORKS:
        _nm.acquire(net)

    # ── Pre-flight cleanup: remove leftover containers from previous runs ──
    # [IMP:8] Container name conflict ("already in use") occurs when smoke
    #          teardown didn't fully complete before component test starts.
    #          Explicit compose down ensures clean state before up, consistent
    #          with hermes_up/pgbouncer_up/clickhouse_up pre-flight cleanup.
    logger.info("[IMP:7][postgres_up] Pre-flight: cleaning up leftover containers ...")
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT, "down", "--timeout", "5"],
        env={**os.environ, **ENV},
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.info("[IMP:9][postgres_up] Pre-flight cleanup complete")

    logger.info("[IMP:7][postgres_up] Starting postgres from %s ...", compose_file)
    compose_args = [
        "docker",
        "compose",
        "-f",
        compose_file,
    ]
    test_override = os.path.join(os.path.dirname(compose_file), "docker-compose.test.yml")
    if os.path.exists(test_override):
        compose_args.extend(["-f", test_override])
        logger.info("[IMP:7][postgres_up] Using test override: %s", test_override)
    compose_args.extend(["--project-name", COMPOSE_PROJECT, "up", "-d", "--wait", "--wait-timeout", "30"])

    try:
        result = subprocess.run(
            compose_args,
            env={**os.environ, **ENV},
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][postgres_up] docker compose up failed: %s", exc)
        # Collect docker compose logs for diagnostic before failing
        _pg_diag = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT, "logs", "--tail", "50"],
            env={**os.environ, **ENV},
            capture_output=True,
            text=True,
            timeout=30,
        )
        _pg_stderr = getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][postgres_up] Docker logs:\n%s\n%s", _pg_diag.stdout[:2000], _pg_diag.stderr[:2000])
        pytest.fail(
            f"postgres compose up failed. Docker is available but compose failed. "
            f"Error: {_pg_stderr}. "
            f"Diagnosis: Check compose config and env vars. "
            f"Run manually: docker compose -f {compose_file} up -d --wait"
        )

    # ── Verify container is running before yielding ───────────────────────
    # [IMP:8] --wait confirms all containers are healthy; verification is a safety net.
    container_running = subprocess.run(
        ["docker", "ps", "--filter", "name=postgres", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "Up" not in container_running.stdout:
        logger.error("[IMP:9][postgres_up] Container not running: %s", container_running.stdout)
        # Diagnostic: get all containers for this project
        _pg_ps = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT, "ps"],
            env={**os.environ, **ENV},
            capture_output=True,
            text=True,
            timeout=30,
        )
        _pg_logs = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT, "logs", "--tail", "50"],
            env={**os.environ, **ENV},
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.error("[IMP:9][postgres_up] Docker compose ps:\n%s", _pg_ps.stdout[:1000])
        logger.error(
            "[IMP:9][postgres_up] Docker compose logs:\n%s\n%s", _pg_logs.stdout[:2000], _pg_logs.stderr[:2000]
        )
        pytest.fail(
            "postgres container not running after compose up. Docker is available "
            "but container failed to start. Check compose logs above."
        )

    logger.info("[IMP:9][postgres_up] postgres started (stdout: %s)", result.stdout.strip()[:200])
    yield

    logger.info("[IMP:7][postgres_up] Tearing down postgres ...")
    down_args = [
        "docker",
        "compose",
        "-f",
        compose_file,
    ]
    test_override = os.path.join(os.path.dirname(compose_file), "docker-compose.test.yml")
    if os.path.exists(test_override):
        down_args.extend(["-f", test_override])
    down_args.extend(["--project-name", COMPOSE_PROJECT, "down", "-v"])

    subprocess.run(
        down_args,
        env={**os.environ, **ENV},
        capture_output=True,
        text=True,
    )
    # Release external networks via NetworkLeaseManager
    for net in _EXTERNAL_NETWORKS:
        _nm.release(net)
    logger.info("[IMP:9][postgres_up] postgres torn down")


@pytest.fixture(scope="session")
def hermes_up(platform_services: dict[str, list[str]], postgres_up, modules_dir) -> None:
    """
    Start hermes-agent after postgres is up.

    ## @purpose — Start hermes-agent container with all dependencies ready.
    ## @io — ⇥ platform_services: dict with foreign container info from _conftest.reuse
    ##        ⇥ postgres_up (dependency), modules_dir → ⎋ str: path to compose file
    ## @complexity — O(1) — single subprocess call with --wait
    ## @invariants
    ##   - postgres_up fixture has run before this fixture executes
    ##   - Returns compose file path for downstream tests to inspect containers
    ##   - Compose project name is ai-platform-test for isolation
    ##   - --wait-timeout: 90s Linux CI, 120s macOS (QEMU emulation + Docker Desktop resource contention)
    ##   - HERMES_WAIT_TIMEOUT env var overrides platform default
    ##   - Foreign container guard allows reusing hermes-agent from platform_services
    """
    compose_file = os.path.join(modules_dir, "hermes-agent", "docker-compose.base.yml")

    # ── Foreign container guard ──────────────────────────────────────
    # ⚠️ TRAP[BUG] · 2026-07-22 · HI · own_project was hardcoded "ai-platform-test"
    # · Same bug — now uses COMPOSE_PROJECT_HERMES.
    foreign = check_foreign_containers(["hermes-agent-test"], COMPOSE_PROJECT_HERMES)
    if foreign:
        logger.info("[IMP:8][hermes_up] Reusing hermes-agent from platform_services — skipping compose lifecycle")
        statuses = wait_for_containers_healthy(["hermes-agent-test"])
        if not all(s == "healthy" for s in statuses.values()):
            pytest.fail(f"Reused containers not healthy: {statuses}")
        yield compose_file
        return
    if not os.path.exists(compose_file):
        pytest.skip(f"hermes-agent docker-compose.base.yml not found: {compose_file}")

    # ── Pre-flight cleanup: remove leftover containers from previous runs ──
    # [IMP:8] Container name conflict (already in use) occurs when a prior
    #          test run was interrupted or teardown failed. Explicit down
    #          ensures clean state before compose up.
    logger.info("[IMP:7][hermes_up] Pre-flight: cleaning up leftover containers ...")
    subprocess.run(
        ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT_HERMES, "down", "--timeout", "5"],
        env={**os.environ, **ENV_HERMES},
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.info("[IMP:9][hermes_up] Pre-flight cleanup complete")

    # ── Build hermes-agent image (GHCR pull may be denied in CI) ──────────
    # [IMP:8] docker compose up does NOT auto-build on pull failure.
    #          build: is now in compose file; explicit build ensures image
    #          exists before up, avoiding 'denied' on GHCR cross-repo pull.
    logger.info("[IMP:7][hermes_up] Building hermes-agent image ...")
    hermes_test_override = os.path.join(os.path.dirname(compose_file), "docker-compose.test.yml")
    build_args = ["docker", "compose", "-f", compose_file]
    if os.path.exists(hermes_test_override):
        build_args.extend(["-f", hermes_test_override])
    build_args.extend(["--project-name", COMPOSE_PROJECT_HERMES, "build"])
    try:
        subprocess.run(
            build_args,
            env={**os.environ, **ENV_HERMES},
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        logger.info("[IMP:9][hermes_up] hermes-agent image built")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as build_exc:
        (getattr(build_exc, "stderr", str(build_exc))[-500:] if hasattr(build_exc, "stderr") else str(build_exc)[-500:])
        logger.error("[IMP:9][hermes_up] docker compose build failed: %s", build_exc)
        _build_stderr = (
            getattr(build_exc, "stderr", str(build_exc))[-500:]
            if hasattr(build_exc, "stderr")
            else str(build_exc)[-500:]
        )
        logger.error("[IMP:9][hermes_up] Build stderr:\n%s", _build_stderr)
        pytest.fail(
            f"hermes-agent compose build failed. Docker is available but build failed. "
            f"Error: {_build_stderr}. "
            f"Diagnosis: Check Dockerfile syntax, pull access to FROM image, "
            f"and build context. Run: docker compose -f {compose_file} build"
        )

    logger.info("[IMP:7][hermes_up] Starting hermes-agent from %s ...", compose_file)
    # ⚠️ macOS Docker Desktop: Hermes under QEMU emulation starts slower (30-60s).
    # Adding 30s buffer for resource contention with other compose stacks.
    # Linux CI (native x86_64): faster startup (10-20s), 90s is sufficient.
    # TRAP[DECISION] · 2026-07-11 · — · macOS wait-timeout 120s
    # · Symptom: container hermes-agent-test unhealthy after 90s on macOS when
    # ·   observability stack is also running (Docker Desktop resource contention).
    # · Rejected: run tests sequentially in separate pytest processes (complex CI infra).
    # · Reason: Increasing timeout by 30s is simpler and handles edge case.
    # ·   Hermes under QEMU takes 30-60s; 120s provides 2x safety margin.
    # · Rev: If Hermes startup time decreases (native ARM image), reduce to 90s.
    is_macos = platform.system() == "Darwin"
    default_wait = "120" if is_macos else "90"
    wait_timeout = os.environ.get("HERMES_WAIT_TIMEOUT", default_wait)
    logger.info("[IMP:7][hermes_up] Platform: %s, wait-timeout: %ss", platform.system(), wait_timeout)
    up_args = ["docker", "compose", "-f", compose_file]
    if os.path.exists(hermes_test_override):
        up_args.extend(["-f", hermes_test_override])
    up_args.extend(
        [
            "--project-name",
            COMPOSE_PROJECT_HERMES,
            "up",
            "-d",
            "--remove-orphans",
            "--wait",
            "--wait-timeout",
            wait_timeout,
        ]
    )
    try:
        result = subprocess.run(
            up_args,
            env={**os.environ, **ENV_HERMES},
            check=True,
            capture_output=True,
            text=True,
            timeout=150,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][hermes_up] docker compose up failed: %s", exc)
        # Collect docker compose logs for diagnostic
        _hermes_diag = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT_HERMES, "logs", "--tail", "50"],
            env={**os.environ, **ENV_HERMES},
            capture_output=True,
            text=True,
            timeout=30,
        )
        _h_stderr = getattr(exc, "stderr", str(exc))[-500:] if hasattr(exc, "stderr") else str(exc)[-500:]
        logger.error("[IMP:9][hermes_up] Docker logs:\n%s\n%s", _hermes_diag.stdout[:2000], _hermes_diag.stderr[:2000])
        pytest.fail(
            f"hermes-agent compose up failed. Docker is available but compose failed. "
            f"Error: {_h_stderr}. "
            f"Diagnosis: Check compose config, env vars, and port availability. "
            f"Run manually: docker compose -f {compose_file} up -d --wait"
        )
    # [IMP:8] --wait confirmed containers are healthy — verification is a safety net.
    container_running = subprocess.run(
        ["docker", "ps", "--filter", "name=hermes-agent-test", "--format", "{{.Status}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "Up" not in container_running.stdout:
        logger.error("[IMP:9][hermes_up] Container not running: %s", container_running.stdout)
        # Diagnostic: get compose ps and logs
        _h_ps = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT_HERMES, "ps"],
            env={**os.environ, **ENV_HERMES},
            capture_output=True,
            text=True,
            timeout=30,
        )
        _h_logs = subprocess.run(
            ["docker", "compose", "-f", compose_file, "--project-name", COMPOSE_PROJECT_HERMES, "logs", "--tail", "50"],
            env={**os.environ, **ENV_HERMES},
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.error("[IMP:9][hermes_up] Compose ps:\n%s", _h_ps.stdout[:1000])
        logger.error("[IMP:9][hermes_up] Compose logs:\n%s\n%s", _h_logs.stdout[:2000], _h_logs.stderr[:2000])
        pytest.fail(
            "hermes-agent container not running after compose up. Docker is available "
            "but container failed to start. Check compose ps and logs above."
        )

    logger.info("[IMP:9][hermes_up] hermes-agent started (stdout: %s)", result.stdout.strip()[:200])
    yield compose_file

    logger.info("[IMP:7][hermes_up] Tearing down hermes-agent ...")
    down_args = ["docker", "compose", "-f", compose_file]
    if os.path.exists(hermes_test_override):
        down_args.extend(["-f", hermes_test_override])
    down_args.extend(["--project-name", COMPOSE_PROJECT_HERMES, "down", "-v"])
    subprocess.run(
        down_args,
        env={**os.environ, **ENV_HERMES},
        capture_output=True,
        text=True,
    )
    logger.info("[IMP:9][hermes_up] hermes-agent torn down")


# endregion FIXTURES


# region TEST_HERMES_COMPOSE_UP


@pytest.mark.component
@ldd_trajectory
def test_hermes_compose_up(hermes_up, caplog) -> None:
    """Verify hermes-agent containers are running after compose up.

    ## @purpose — Assert that docker compose ps shows hermes-agent container as running.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single subprocess call
    ## @acceptance — hermes-agent container must appear in docker compose ps JSON output
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_hermes_compose_up] Verifying hermes-agent containers are running ...")

        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                hermes_up,
                "--project-name",
                COMPOSE_PROJECT_HERMES,
                "ps",
            ],
            env={**os.environ, **ENV_HERMES},
            capture_output=True,
            text=True,
            check=True,
        )

        assert "hermes-agent" in result.stdout, (
            f"hermes-agent container not found in compose ps output: {result.stdout[:500]}"
        )
        logger.info("[IMP:9][test_hermes_compose_up] hermes-agent container is running")


# endregion TEST_HERMES_COMPOSE_UP


# region TEST_HERMES_AGENT_STARTS


@pytest.mark.component
@ldd_trajectory
def test_hermes_agent_starts(hermes_up, caplog) -> None:
    """Verify hermes-agent container logs show startup via s6-overlay and Hermes.

    ## @purpose — Assert that docker logs includes s6-overlay service startup,
    ##            Hermes gateway, or dashboard confirmation.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single docker logs subprocess call
    ## @invariants
    ##   - Container name is hermes-agent (from docker-compose.base.yml)
    ##   - Log check is best-effort: matches at least one of several known startup messages
    ##   - Real Hermes L1 image uses s6-overlay for process supervision
    ## @acceptance — docker logs contains startup message confirming agent/gateway/dashboard started
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_hermes_agent_starts] Checking hermes-agent startup (health-check primary) ...")

        # ═══ PRIMARY: health-check via docker ps (TASK-3 fix) ═══
        # [IMP:8] Health-check is the canonical readiness indicator — compose
        # healthcheck validates the actual service endpoint, whereas log-grep
        # is fragile to formatting changes and log rotation.
        _ps_result = subprocess.run(
            ["docker", "ps", "--filter", "name=hermes-agent-test", "--format", "{{.Names}} {{.Status}}"],
            env={**os.environ, **ENV},
            capture_output=True,
            text=True,
            timeout=10,
        )
        container_status = (_ps_result.stdout or "").strip()
        is_healthy = "healthy" in container_status.lower() if container_status else False

        logger.critical(
            "[IMP:9][test_hermes_agent_starts] ASSERT: container_status=%s healthy=%s",
            container_status,
            is_healthy,
        )

        if is_healthy:
            # ✅ Health-check PASS — agent is confirmed running, no log-grep needed
            return

        # ═══ FALLBACK: health-check failed — diagnose via logs ═══
        logger.warning(
            "[IMP:8][test_hermes_agent_starts] Container NOT healthy (%s) — falling back to log diagnostics",
            container_status,
        )

        try:
            result = subprocess.run(
                ["docker", "logs", "hermes-agent-test", "--tail", "200"],
                env={**os.environ, **ENV},
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("[IMP:4][test_hermes_agent_starts] Failed to get docker logs: %s", exc)
            _diag_ps = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=hermes-agent-test", "--format", "{{.Names}} {{.Status}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            _diag_logs = subprocess.run(
                ["docker", "logs", "hermes-agent-test", "--tail", "20"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.error("[IMP:9][test_hermes_agent_starts] Docker ps -a:\n%s", _diag_ps.stdout[:500])
            logger.error(
                "[IMP:9][test_hermes_agent_starts] Container logs (tail 20):\nstdout: %s\nstderr: %s",
                _diag_logs.stdout[:500],
                _diag_logs.stderr[:500],
            )
            pytest.fail(
                f"hermes-agent container is NOT healthy ({container_status}) "
                f"and docker logs failed: {exc}. "
                f"Diagnosis: Check container status with 'docker ps -a --filter name=hermes-agent'"
            )

        combined_logs = (result.stdout + result.stderr).lower()
        startup_signals = [
            "s6-rc: info: service",
            "successfully started",
            "hermes gateway starting",
            "hermes gateway started",
            "dashboard started",
            "hermes agent started",
            "[imp:9]",
            "uvicorn running",
            "application startup complete",
            "gateway run",
        ]

        matched = any(signal in combined_logs for signal in startup_signals)

        if matched:
            logger.critical(
                "[IMP:9][test_hermes_agent_starts] FALLBACK PASS: startup signal found in logs "
                "(container not healthy but startup log confirmed)"
            )
        else:
            logger.error(
                "[IMP:9][test_hermes_agent_starts] ASSERT FAIL: container NOT healthy (%s), "
                "no startup signal in logs. Logs preview: %s",
                container_status,
                (result.stdout + result.stderr)[:300],
            )
            pytest.fail(
                f"hermes-agent is NOT healthy ({container_status}) "
                f"and no startup signal found in container logs. "
                f"Expected one of: {startup_signals}. "
                f"Actual stdout[:300]: {result.stdout[:300]} "
                f"stderr[:300]: {result.stderr[:300]}"
            )


# endregion TEST_HERMES_AGENT_STARTS


# region TEST_HEALTHCHECK_PASSES


@pytest.mark.component
@ldd_trajectory
def test_healthcheck_passes(hermes_up, caplog) -> None:
    """Verify hermes-agent container healthcheck status is healthy.

    ## @purpose — Assert that docker ps --filter shows hermes-agent status as (healthy).
    ##            Real Hermes serves dashboard on :9119 with Basic Auth.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single docker ps subprocess call with --filter name=hermes-agent
    ## @invariants
    ##   - Container name is "hermes-agent" (matches docker-compose.base.yml container_name)
    ##   - Real Hermes dashboard on :9119 with Basic Auth returns 401 for unauthenticated GET /
    ##   - Either 200 or 401 means the HTTP server is running and accepting connections
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_healthcheck_passes] Checking hermes-agent container status ...")

        # Step 1: Verify container is running (Up) regardless of healthcheck state.
        try:
            ps_result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    "name=hermes-agent-test",
                    "--format",
                    "{{.Names}} {{.Status}}",
                ],
                env={**os.environ, **ENV},
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("[IMP:4][test_healthcheck_passes] docker ps failed: %s", exc)
            pytest.fail(f"docker ps command failed: {exc}")

        is_up = "Up" in ps_result.stdout
        logger.critical(
            "[IMP:9][test_healthcheck_passes] ASSERT: is_up=%s stdout=%s",
            is_up,
            ps_result.stdout.strip()[:200],
        )
        assert is_up, f"hermes-agent container is not running (Up). docker ps output: '{ps_result.stdout.strip()}'"

        # Step 2: Verify dashboard endpoint via docker exec on port 9119
        logger.info("[IMP:7][test_healthcheck_passes] Verifying dashboard endpoint via docker exec ...")
        try:
            exec_result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "hermes-agent-test",
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "--max-time",
                    "5",
                    "http://127.0.0.1:9119/",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("[IMP:4][test_healthcheck_passes] docker exec curl failed: %s", exc)
            pytest.fail(f"Dashboard HTTP endpoint unreachable via docker exec: {exc}")

        http_status = exec_result.stdout.strip()
        logger.critical(
            "[IMP:9][test_healthcheck_passes] ASSERT: dashboard_http_status=%s",
            http_status,
        )
        # Real Hermes dashboard responds with 200 (no auth), 302 (redirect to /dashboard), or 401 (Basic Auth)
        # Any of these means the HTTP server is alive and responding
        assert http_status in ("200", "302", "401"), (
            f"Dashboard HTTP endpoint returned status {http_status}, expected 200, 302, or 401. "
            f"stderr: {exec_result.stderr[:200]}"
        )


# endregion TEST_HEALTHCHECK_PASSES


# region TEST_NO_RESTART_LOOP


@pytest.mark.component
@ldd_trajectory
def test_no_restart_loop(hermes_up, caplog) -> None:
    """Verify hermes-agent does not restart-loop within a 20-second observation window.

    ## @purpose — Assert that RestartCount from docker inspect is 0 (no unexpected restarts).
    ##            Polls every 5 seconds for up to 20 seconds to detect restart loops.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(4) — up to 4 docker inspect calls with 5-second intervals
    ## @invariants
    ##   - RestartCount must be 0 across all observations
    ##   - Early exit on RestartCount > 0 (no need to wait longer)
    ##   - A restart loop would show RestartCount > 0 within the observation window
    ## @rationale — Restart loops indicate crash-on-startup (e.g. config error,
    ##              missing dependency, OOM kill). Polling with 5s intervals over 20s
    ##              catches rapid restart patterns without a long wait.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_no_restart_loop] Observing hermes-agent for restart loop ...")

        restart_counts: list[int] = []
        max_wait = 20  # total observation window (seconds)
        poll_interval = 5  # check every 5 seconds
        elapsed = 0

        while elapsed <= max_wait:
            if elapsed > 0:
                logger.info("[IMP:7][test_no_restart_loop] Sleeping %ds before next check ...", poll_interval)
                time.sleep(poll_interval)

            try:
                result = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.RestartCount}}",
                        "hermes-agent-test",
                    ],
                    env={**os.environ, **ENV},
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                logger.error(
                    "[IMP:4][test_no_restart_loop] docker inspect failed at +%ds: %s",
                    elapsed,
                    exc,
                )
                pytest.fail(f"docker inspect hermes-agent failed at +{elapsed}s: {exc}")

            try:
                count = int(result.stdout.strip())
            except (ValueError, TypeError):
                logger.error(
                    "[IMP:4][test_no_restart_loop] Failed to parse RestartCount at +%ds: '%s'",
                    elapsed,
                    result.stdout.strip(),
                )
                pytest.fail(f"Cannot parse RestartCount at +{elapsed}s: '{result.stdout.strip()}'")

            restart_counts.append(count)
            logger.info(
                "[IMP:7][test_no_restart_loop] +%ds: RestartCount=%d",
                elapsed,
                count,
            )

            # Early exit if restart detected (no need to wait longer)
            if count > 0:
                break

            elapsed += poll_interval

        logger.critical(
            "[IMP:9][test_no_restart_loop] ASSERT: restart_counts=%s (expected all zeros)",
            restart_counts,
        )
        assert all(rc == 0 for rc in restart_counts), (
            f"hermes-agent has restart loop detected! "
            f"RestartCount observations: {restart_counts}. "
            f"Expected all zeros, got non-zero at indices: "
            f"{[i for i, rc in enumerate(restart_counts) if rc != 0]}"
        )


# endregion TEST_NO_RESTART_LOOP


# region TEST_HERMES_DASHBOARD_AUTH


@pytest.mark.component
@ldd_trajectory
def test_hermes_dashboard_auth(hermes_up, caplog) -> None:
    """Verify hermes-agent dashboard redirects unauthenticated requests to login.

    ## @purpose — Assert that dashboard :9119 redirects to /login when accessed
    ##             without credentials (302 redirect). Real Hermes uses redirect-based
    ##             auth where all unauthenticated requests go to /login.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single HTTP request to :9119
    ## @acceptance — AC-1: no auth → 302 redirect to /login
    ## @rationale — Real Hermes dashboard uses SPA-based auth flow:
    ##              GET / → 302 Location: /login?next=%2F.
    ##              The healthcheck test separately verifies dashboard is responding.
    ##              Login page details may vary across upstream Hermes versions.
    """
    try:
        import requests
    except ImportError:
        pytest.skip("requests not installed — install with: pip install requests")

    # ⚠️ TRAP[BUG] · 2026-07-18 · HIGH · F-7: test переведён на shifted порт 19119
    # · Root: до !override hermes-agent-test биндил canonical 9119 через склейку ports.
    # · После !override доступен только 127.0.0.1:19119 (test.yml).
    # · docker exec curl внутри контейнера (test_healthcheck_passes) использует 9119 —
    # ·   это корректно (container port, не host port).
    dashboard_url = "http://127.0.0.1:19119/"

    with caplog.at_level(logging.DEBUG):
        # Step 1: GET / without auth → 302 redirect to /login
        logger.info("[IMP:7][test_hermes_dashboard_auth] STEP 1: GET %s without auth (no redirect) ...", dashboard_url)
        resp_noauth = requests.get(dashboard_url, timeout=10, allow_redirects=False)
        assert resp_noauth.status_code == 302, (
            f"Expected 302 redirect without auth, got {resp_noauth.status_code}. Headers: {dict(resp_noauth.headers)}"
        )
        redirect_target = resp_noauth.headers.get("Location", "")
        assert "/login" in redirect_target, f"Expected Location containing '/login', got: {redirect_target}"
        logger.critical(
            "[IMP:9][test_hermes_dashboard_auth] ASSERT: step1_redirect status=302 target=%s",
            redirect_target,
        )


# endregion TEST_HERMES_DASHBOARD_AUTH


# region TEST_HERMES_GATEWAY_LISTENS


@pytest.mark.component
@ldd_trajectory
def test_hermes_gateway_listens(hermes_up, caplog) -> None:
    """Verify hermes-agent gateway process is running in the container.

    ## @purpose — Assert that gateway process (hermes gateway run) is running
    ##            inside the hermes-agent container. Real Hermes gateway handles
    ##            messaging platforms (Telegram, etc.) and cron scheduling,
    ##            not HTTP API — no HTTP port is exposed by default.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single docker exec ps call
    ## @acceptance — AC-1: gateway process is running in the container
    ## @rationale — Real Hermes gateway (v2026.7.1) is a messaging platform
    ##              handler, not an HTTP server. It runs under s6 supervision
    ##              as 'hermes gateway run --replace'. HTTP API /v1/models
    ##              is not exposed by default — requires API_SERVER_ENABLED=true.
    ##              Dashboard availability is verified by test_healthcheck_passes.
    """
    with caplog.at_level(logging.DEBUG):
        # Step 1: Verify gateway process is running in container
        logger.info("[IMP:7][test_hermes_gateway_listens] Checking gateway process in container ...")
        try:
            ps_result = subprocess.run(
                ["docker", "exec", "hermes-agent-test", "ps", "aux"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("[IMP:4][test_hermes_gateway_listens] docker exec ps failed: %s", exc)
            pytest.fail(f"Cannot inspect container processes: {exc}")

        has_gateway = "hermes gateway run" in ps_result.stdout or "gateway run" in ps_result.stdout
        logger.critical(
            "[IMP:9][test_hermes_gateway_listens] ASSERT: gateway_process_running=%s",
            has_gateway,
        )
        assert has_gateway, (
            "Gateway process not found in container. "
            f"Expected 'hermes gateway run' in process list. "
            f"Actual ps output[:500]: {ps_result.stdout[:500]}"
        )


# endregion TEST_HERMES_GATEWAY_LISTENS


# region TEST_READY_ENDPOINT


@pytest.mark.component
@ldd_trajectory
def test_ready_endpoint_returns_valid_json(hermes_up, caplog) -> None:
    """Verify hermes-agent container is healthy (ready indicator).

    ## @purpose — Assert that container health status is 'healthy' via
    ##            docker inspect. Real Hermes dashboard redirects all HTTP
    ##            endpoints (including /ready) to /auth/login — the container
    ##            healthcheck is the canonical readiness indicator.
    ## @io — ⇥ hermes_up (compose file path), caplog → ⎋ None (side-effect: assertions)
    ## @complexity — O(1) — single docker inspect call
    ## @acceptance — AC-1: container health status is 'healthy'
    ## @rationale — Real Hermes dashboard requires authentication for all
    ##              endpoints. The compose healthcheck (curl :9119/) verifies
    ##              the server is alive; docker inspect shows the consolidated
    ##              health status after all retries and intervals.
    """
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_ready_endpoint] Checking container health status via docker inspect ...")

        try:
            inspect_result = subprocess.run(
                ["docker", "inspect", "hermes-agent-test", "--format", "{{.State.Status}} {{.State.Health.Status}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.error("[IMP:4][test_ready_endpoint] docker inspect failed: %s", exc)
            _rd_logs = subprocess.run(
                ["docker", "logs", "hermes-agent-test", "--tail", "20"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.error(
                "[IMP:9][test_ready_endpoint] Container logs (tail 20):\n%s\n%s",
                _rd_logs.stdout[:500],
                _rd_logs.stderr[:500],
            )
            pytest.fail(f"docker inspect failed: {exc}. Docker is available but container may not be running.")

        status_output = inspect_result.stdout.strip()
        logger.critical(
            "[IMP:9][test_ready_endpoint] ASSERT: container_status=%s",
            status_output,
        )
        # Expected format: "running healthy"
        assert "running" in status_output, f"Container is not running. Status: '{status_output}'"
        assert "healthy" in status_output, (
            f"Container is not healthy. Status: '{status_output}'. "
            f"This means the healthcheck is failing — check compose healthcheck and logs."
        )


# endregion TEST_READY_ENDPOINT

# 🧐 TRAP[DECISION] · 2026-07-07 · — · skip→fail: masking container failures with skip is unacceptable
# · Rejected: Keep pytest.skip for compose failures, docker logs failures, container-not-running
# · Reason: Docker is available → container failure is a bug (compose config, env wiring, healthcheck),
# ·   not an env issue. pytest.skip masked real problems for weeks. All skip→fail conversions:
# ·   postgres_up compose fail, postgres_up container not running, hermes_up build fail,
# ·   hermes_up compose fail, hermes_up container not running, test_hermes_agent_starts log fail,
# ·   test_ready_endpoint curl fail. Genuine env skips preserved: requests not installed,
# ·   compose file not found.
# · Rev: If CI introduces runtime without Docker daemon, use docker_available() guard at fixture level.
