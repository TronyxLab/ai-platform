# GREP_SUMMARY: smoke, platform, docker, compose, loki, platform_services, platform_env, SMOKE_ENV, external-networks
# STRUCTURE: ┌SMOKE_ENV constants┐ → ┌_collect_external_networks┐ ◇ ┌_run_docker_smoke┐ + ┌_wait_for_loki_ready┐ → ┌platform_env┐ → ┌platform_services┐(Docker guard → ensure_volumes → compose up → loki poll → yield → teardown)

# region MODULE_CONTRACT
## @purpose — Session-scoped fixtures and helpers for smoke platform tests, extracted from conftest.py
##            to eliminate cross-file fixture import lifecycle ambiguity (TASK: fix smoke test ordering).
## @scope — Used by test_smoke_platform.py and test_platform_endpoints.py.
## @invariants
##   - platform_services is session-scoped (not module) — containers live for entire session
##   - SMOKE_ENV contains MINIMAL test values, not production secrets
##   - Docker guard built into platform_services — skip if Docker unavailable or production
##   - Conditional activation via requires_docker marker — static tests don't trigger compose
##   - _collect_external_netwalks path resolved from tests/conftest/smoke.py (3 levels up → core/modules)
## @rationale — Cross-file module-scoped fixture imports cause non-deterministic teardown
##              ordering. Session scope extracted to dedicated module eliminates race condition.
## @changes — LAST_CHANGE: 2026-07-12 | Extracted from conftest.py SMOKE_PLATFORM_FIXTURES region
## @modulemap — _collect_external_networks → parse compose YAMLs for external:true networks
##              _run_docker_smoke → centralised docker subprocess runner with SMOKE_ENV
##              _wait_for_loki_ready → HTTP poll for Loki /ready (scratch image has no curl/wget)
##              platform_env → inject/restore SMOKE_ENV in os.environ
##              platform_services → lifecycle fixture: start/stop compose stack
# endregion MODULE_CONTRACT

import logging
import os
import platform as _platform
import subprocess
import time as _time
from pathlib import Path

import pytest
import yaml as _yaml

from _conftest.ldd import _ensure_volume_dirs
from _conftest.networks import docker_available, ensure_external_networks, is_production_host

# region SMOKE_PLATFORM_FIXTURES
## @purpose — Session-scoped fixtures and helpers for smoke platform tests.
##            Moved from test_smoke_platform.py to eliminate cross-file fixture
##            import lifecycle ambiguity (TASK: fix smoke test ordering).
## @scope — Used by test_smoke_platform.py and test_platform_endpoints.py.
## @invariants
##   - platform_services is session-scoped (not module) — containers live for entire session
##   - SMOKE_ENV contains MINIMAL test values, not production secrets
##   - Docker guard built into platform_services — skip if Docker unavailable or production
##   - Conditional activation via requires_docker marker — static tests don't trigger compose
## @rationale — Cross-file module-scoped fixture imports cause non-deterministic teardown
##              ordering. Session scope in conftest.py eliminates the race condition.

# ── Platform compose --wait timeouts with env-var override ──────────────
_IS_MACOS = _platform.system() == "Darwin"
# --wait-timeout for docker compose up (env overrides platform default)
PLATFORM_COMPOSE_TIMEOUT = int(os.environ.get("PLATFORM_COMPOSE_TIMEOUT", "120" if _IS_MACOS else "90"))
# External Loki /ready probe (Loki healthcheck is liveness-only, --wait
# does not cover HTTP readiness — scratch image has no curl/wget)
PLATFORM_LOKI_TIMEOUT = int(os.environ.get("PLATFORM_LOKI_TIMEOUT", "30"))

# ── Named constants for magic numbers used in platform service fixtures ──
_POLL_INTERVAL_SECONDS = 5  # sleep interval in _wait_for_loki_ready
_REQUEST_TIMEOUT_SECONDS = 10  # requests.get timeout in _wait_for_loki_ready
_COMPOSE_DOWN_TIMEOUT = 5  # docker compose down --timeout
_DOCKER_LOG_TIMEOUT = 30  # timeout for docker compose logs
_STDERR_TAIL_LINES = 300  # tail lines for stderr truncation
_COMPOSE_EXTRA_TIMEOUT = 30  # extra timeout added to PLATFORM_COMPOSE_TIMEOUT

SMOKE_ENV: dict[str, str] = {
    "PLATFORM_DOMAIN": "test.local",
    "COMPOSE_PROJECT_NAME": "ai-platform-test",
    "PLATFORM_DIR": "/tmp/ai-platform-test",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "testpass",
    "POSTGRES_DB": "platform",
    "HERMES_DASHBOARD_USERNAME": "admin",
    "HERMES_DASHBOARD_PASSWORD": "testpass",
    "LITELLM_MASTER_KEY": "sk-test-key",
    "OPENAI_API_KEY": "sk-test-openai-key",
    "NEXTAUTH_SECRET": "sk-test-nextauth-secret",
    "SALT": "sk-test-salt",
    "LANGFUSE_SECRET_KEY": "sk-test-langfuse-secret",
    "LANGFUSE_PUBLIC_KEY": "pk-test-langfuse-public",
    "LANGFUSE_INIT_ORG_ID": "test-org",
    "LANGFUSE_INIT_PROJECT_ID": "test-project",
    "LANGFUSE_INIT_USER_PASSWORD": "testpass",
    "GF_SECURITY_ADMIN_USER": "admin",
    "GF_SECURITY_ADMIN_PASSWORD": "testpass",
    "S3_BUCKET": "test-bucket",
    "S3_ENDPOINT_URL": "https://s3.timeweb.cloud",
    "MINIO_ROOT_USER": "minioadmin",
    "MINIO_ROOT_PASSWORD": "minioadmin",
    "S3_ACCESS_KEY": "test-access-key",
    "S3_SECRET_KEY": "test-secret-key",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_USERS": "",
}

_SMOKE_VOLUME_BIND_DIRS: list[str] = [
    "/var/lib/platform/postgres-data",
    "/var/lib/platform/backup-spool",
    "/var/lib/platform/grafana-data",
    "/var/lib/platform/prometheus-data",
    "/var/lib/platform/loki-data",
    "/var/lib/platform/hermes-agent/data",
    "/var/log/platform/backup",
]


def _collect_external_networks() -> set[str]:
    """Scan all compose files and return set of networks declared as external: true.

    ## @purpose — Parse compose YAMLs to discover which networks must be pre-created.
    ## @io — ⎋ set[str]: network names that need pre-creation
    ## @complexity — O(N * M) where N = compose files, M = networks per file
    """
    external: set[str] = set()
    compose_dir = Path(__file__).resolve().parent.parent.parent / "core" / "modules"
    for mod_dir in sorted(compose_dir.iterdir()):
        compose_path = mod_dir / "docker-compose.base.yml"
        if not compose_path.is_file():
            continue
        try:
            with open(compose_path) as f:
                data = _yaml.safe_load(f)
        except (_yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        for net_name, net_config in (data.get("networks") or {}).items():
            if isinstance(net_config, dict) and net_config.get("external") in (True, "true"):
                external.add(net_name)
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:7][conftest][_collect_external_networks] Found %d external network(s)", len(external))
    return external


def _run_docker_smoke(
    args: list[str],
    env_override: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a docker subprocess with SMOKE_ENV merged into os.environ.

    ## @purpose — Centralised docker subprocess runner for smoke tests.
    ## @io — ⇥ args, env_override, timeout → ⎋ CompletedProcess
    ## @complexity — O(1)
    """
    cmd_env = {**os.environ, **SMOKE_ENV}
    if env_override:
        cmd_env.update(env_override)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=cmd_env,
    )


def _wait_for_loki_ready(
    url: str,
    timeout: int,
    logger,
) -> bool:
    """Poll Loki /ready endpoint until HTTP 200 or timeout.

    ## @purpose — Bridge the gap between Docker liveness healthcheck
    ##            (loki -version checks only process alive) and actual
    ##            query frontend readiness. Loki scratch image has no
    ##            curl/wget — cannot HTTP-probe from inside container.
    ##            This poll runs from the host, outside the container.
    ## @io — ⇥ url, timeout → ⌋ bool (True=200 received)
    ## @complexity — O(T) where T=poll iterations
    ## @invariants
    ##   - Polls every 5 seconds
    ##   - Returns True on first HTTP 200
    ##   - Returns False on timeout — graceful degradation
    ##   - IMP:8 logs on 503, IMP:9 on 200
    ## @rationale — TRAP[BUG] in loki docker-compose.base.yml documents
    ##              that scratch image has no HTTP client for healthcheck.
    ##              This function is the external readiness probe that
    ##              complements the internal liveness healthcheck.
    """
    import requests as _requests

    deadline = _time.monotonic() + timeout
    first_poll = True

    while _time.monotonic() < deadline:
        if not first_poll:
            _time.sleep(_POLL_INTERVAL_SECONDS)
        first_poll = False

        try:
            r = _requests.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
            if r.status_code == 200:
                logger.info(
                    "[IMP:9][conftest][_wait_for_loki_ready] Loki /ready OK: HTTP 200 — %s",
                    r.text.strip(),
                )
                return True
            logger.info(
                "[IMP:8][conftest][_wait_for_loki_ready] Loki /ready returned %d: %s — waiting...",
                r.status_code,
                r.text.strip()[:100],
            )
        except _requests.RequestException as exc:
            logger.info(
                "[IMP:8][conftest][_wait_for_loki_ready] Loki /ready unreachable: %s — waiting...",
                exc,
            )

    logger.warning(
        "[IMP:9][conftest][_wait_for_loki_ready] Loki /ready timeout after %ds",
        timeout,
    )
    return False


@pytest.fixture(scope="session")
def platform_env() -> dict[str, str]:
    """Inject SMOKE_ENV into os.environ; restore on teardown.

    ## @purpose — Set environment variables required by docker-compose files.
    ##            Saves original values and restores them after the session.
    ## @io — ⇥ (os.environ snapshot) → ⌋ dict[str, str] (SMOKE_ENV copy)
    ## @complexity — O(K) where K = len(SMOKE_ENV)
    """
    _logger = logging.getLogger(__name__)
    _logger.info("[IMP:7][conftest][platform_env] Setting SMOKE_ENV environment variables")
    saved: dict[str, str | None] = {}
    for key in SMOKE_ENV:
        saved[key] = os.environ.get(key)
        os.environ[key] = SMOKE_ENV[key]

    yield SMOKE_ENV

    _logger.info("[IMP:9][conftest][platform_env] Restoring original environment")
    for key in SMOKE_ENV:
        env_value = saved[key]
        if env_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = env_value
    _logger.info("[IMP:7][conftest][platform_env] Environment restored")


@pytest.fixture(scope="session")
def platform_services(
    request: pytest.FixtureRequest,
    platform_env: dict[str, str],
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_ports: dict[str, int],
) -> dict[str, list[str]]:
    """Start all compose files in topological order; tear down after all tests.

    ## @purpose — Lifecycle fixture: creates volume dirs + external networks,
    ##            starts containers, yields started module names, then runs
    ##            docker compose down on teardown.
    ##            Session-scoped — containers live for the entire test session.
    ##            Built-in Docker guard: skips if Docker unavailable or production host.
    ##            Conditional activation: only if at least one test has requires_docker marker.
    ## @io — ⇥ request, platform_env, all_compose_files, module_graph
    ##       → ⎋ dict[str, list[str]]: {"started": [module_names], "failed": [module_names]}
    ## @complexity — O(N + M) where N = compose files, M = networks
    """
    _logger = logging.getLogger(__name__)

    # ── Built-in Docker guard ────────────────────────────────────────────────
    if is_production_host():
        pytest.skip("Production host detected — skip smoke suite to prevent container overwrite")

    if not docker_available():
        pytest.skip("Docker daemon not available — skip smoke suite")

    # ── Conditional activation (T2.2 pattern) ────────────────────────────────
    items = request.session.items
    needs_docker = any(item.get_closest_marker("requires_docker") for item in items)
    if not needs_docker:
        _logger.info("[IMP:8][conftest][platform_services] No test requires Docker — yielding no-op")
        yield {"started": [], "failed": []}
        return

    _logger.info("[IMP:7][conftest][platform_services] Starting platform services")

    # ── Ensure volume bind-mount directories ─────────────────────────────────
    _ensure_volume_dirs(_SMOKE_VOLUME_BIND_DIRS)

    # ── Pre-create external networks ─────────────────────────────────────────
    _logger.info("[IMP:8][conftest][platform_services] Collecting external networks")
    external_nets = _collect_external_networks()
    for net_name in sorted(external_nets):
        ensure_external_networks([net_name])
    _logger.info(
        "[IMP:9][conftest][platform_services] External networks ready: %d external network(s)", len(external_nets)
    )

    # ── Start compose files in topological order ─────────────────────────────
    started: list[str] = []
    failed: list[str] = []
    for module_name in module_graph:
        compose_path = all_compose_files.get(module_name)
        if compose_path is None:
            continue
        # ── Build base compose args (files + project name) ──────────────
        compose_base_args = ["docker", "compose", "-f", compose_path]
        test_override = os.path.join(os.path.dirname(compose_path), "docker-compose.test.yml")
        if os.path.exists(test_override):
            compose_base_args.extend(["-f", test_override])
        macos_override = os.path.join(os.path.dirname(compose_path), "docker-compose.macos.yml")
        if _platform.system() == "Darwin" and os.path.exists(macos_override):
            compose_base_args.extend(["-f", macos_override])
        compose_base_args.extend(["-p", "ai-platform-test"])

        # ── Pre-cleanup: docker compose down before up ──────────────
        _down_args = [*compose_base_args, "down", "--timeout", str(_COMPOSE_DOWN_TIMEOUT), "--remove-orphans"]
        _run_docker_smoke(_down_args, timeout=20, env_override={"COMPOSE_PROFILES": module_name})

        # ── Start up ──────────────────────────────────────────────────
        compose_up_args = [*compose_base_args, "up", "-d", "--wait", "--wait-timeout", str(PLATFORM_COMPOSE_TIMEOUT)]
        result = _run_docker_smoke(
            compose_up_args,
            timeout=PLATFORM_COMPOSE_TIMEOUT + _COMPOSE_EXTRA_TIMEOUT,
            env_override={"COMPOSE_PROFILES": module_name},
        )
        if result.returncode != 0:
            # ── Diagnostic: collect logs for failure analysis ─────────────
            log_args = [*compose_base_args, "logs", "--tail", "50", "--no-color"]
            logs = _run_docker_smoke(
                log_args, timeout=_DOCKER_LOG_TIMEOUT, env_override={"COMPOSE_PROFILES": module_name}
            )
            _logger.error(
                "[IMP:9][conftest][platform_services] Failed to start '%s' — "
                "returncode=%d\nstderr: %s\ndiagnostic logs:\n%s",
                module_name,
                result.returncode,
                result.stderr.strip()[-_STDERR_TAIL_LINES:],
                (logs.stdout or logs.stderr).strip()[-500:],
            )
            failed.append(module_name)
        else:
            # ── Post-up container existence check ─────────────────────────────────
            # Root cause (from CI diagnostic run): `docker compose up -d --wait`
            # returns exit code 0 on GHA runner even when containers fail to start
            # (e.g., when `--wait-timeout` expires or image pull fails silently).
            # `docker compose ps --all` returns empty despite returncode=0.
            # Add explicit container count check to distinguish true success from
            # silent compose failure. If zero containers → treat as failure.
            _ps_check = _run_docker_smoke(
                [*compose_base_args, "ps", "--all", "--format", "{{.Name}}"],
                timeout=15,
                env_override={"COMPOSE_PROFILES": module_name},
            )
            _container_count = len([cname for cname in _ps_check.stdout.strip().splitlines() if cname.strip()])
            if _container_count == 0:
                _logger.error(
                    "[IMP:9][conftest][platform_services] '%s' compose up returned 0 but "
                    "no containers exist (docker compose ps --all = empty). "
                    "CI runner silent compose failure — treating as failed.",
                    module_name,
                )
                failed.append(module_name)
            else:
                started.append(module_name)

            # ---- Loki readiness HTTP-poll ------------------------------------
            if module_name == "observability":
                _loki_ready = _wait_for_loki_ready(
                    url=f"http://localhost:{platform_ports['LOKI_PORT']}/ready",
                    timeout=PLATFORM_LOKI_TIMEOUT,
                    logger=_logger,
                )
                if not _loki_ready:
                    _logger.warning(
                        "[IMP:9][conftest][platform_services] Loki /ready timeout - proceeding",
                    )

    _logger.info("[IMP:9][conftest][platform_services] Result: %d started, %d failed", len(started), len(failed))
    yield {"started": started, "failed": failed}

    # ── Teardown: docker compose down for each module (reverse order) ────────
    all_modules = list(reversed(started + [m for m in failed if m not in started]))
    _logger.info("[IMP:7][conftest][platform_services] Tearing down %d module(s)", len(all_modules))
    for module_name in all_modules:
        compose_path = all_compose_files.get(module_name)
        if compose_path is None:
            continue
        down_args = ["docker", "compose", "-f", compose_path]
        test_override = os.path.join(os.path.dirname(compose_path), "docker-compose.test.yml")
        if os.path.exists(test_override):
            down_args.extend(["-f", test_override])
        macos_override = os.path.join(os.path.dirname(compose_path), "docker-compose.macos.yml")
        if _platform.system() == "Darwin" and os.path.exists(macos_override):
            down_args.extend(["-f", macos_override])
        down_args.extend(
            ["-p", "ai-platform-test", "down", "--timeout", str(_COMPOSE_DOWN_TIMEOUT), "--remove-orphans"]
        )

        down_result = _run_docker_smoke(down_args, timeout=20)
        if down_result.returncode != 0:
            _logger.error(
                "[IMP:9][conftest][platform_services] Cleanup failed for '%s': %s",
                module_name,
                down_result.stderr.strip()[-200:],
            )

    # ── Remove pre-created networks ──────────────────────────────────────────
    for net_name in sorted(external_nets):
        subprocess.run(
            ["docker", "network", "rm", net_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    _logger.info("[IMP:9][conftest][platform_services] Cleanup complete")


# endregion SMOKE_PLATFORM_FIXTURES
