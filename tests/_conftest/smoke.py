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

import json
import logging
import os
import platform as _platform
import subprocess
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def is_macos() -> bool:
    """Detect macOS (Darwin) platform for test skipping.

    ## @purpose — Public helper for @pytest.mark.skipif on macOS-specific tests.
    ##            Used by test_smoke_nginx.py to skip cert generation and
    ##            bind-mount tests that fail on Docker Desktop (macOS).
    ## @io — ⎋ bool: True if running on macOS/Darwin
    ## @complexity — O(1)
    ## @rationale — macOS Docker Desktop has known limitations with mkcert cert
    ##              generation and bind-mount file permissions. CI runs same
    ##              tests on Linux (ubuntu-latest runner, platform-test.yml),
    ##              so skipping on macOS does not reduce coverage — it follows
    ##              the "Linux-parity in CI" pattern (DevPlan §macOS smoke skip).
    ##              Root cause: platform limitation, not code defect.
    """
    return _platform.system() == "Darwin"


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
    "PROMETHEUS_TARGETS_DIR": "/tmp/prometheus-targets",
    "PROMETHEUS_RULES_DIR": "/tmp/prometheus-rules",
    "NGINX_CONF_DIR": "./dev-config",
    "NGINX_CERT_DIR": "/etc/nginx/dev-certs",
}

_SMOKE_VOLUME_BIND_DIRS: list[str] = [
    "/var/lib/platform/postgres-data",
    "/var/lib/platform/backup-spool",
    "/var/lib/platform/grafana-data",
    "/var/lib/platform/prometheus-data",
    "/var/lib/platform/loki-data",
    "/var/lib/platform/hermes-agent/data",
    "/var/log/platform/backup",
    "/tmp/prometheus-targets",
    "/tmp/prometheus-rules",
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


def _wait_for_minio_healthy(
    compose_base_args: list[str],
    timeout: int,
    logger: logging.Logger,
) -> bool:
    """Poll docker compose ps --format json until minio container is healthy.

    ## @purpose — Wait for minio (not minio-createbuckets one-shot init container)
    ##            to become healthy. minio-createbuckets exits 0 after creating
    ##            buckets, which makes `docker compose up --wait` return 1 even
    ##            though minio itself is healthy. This function polls only the
    ##            minio container's Health status.
    ## @io — ⇥ compose_base_args, timeout, logger → ⎋ bool (healthy within timeout)
    ## @complexity — O(T) where T = timeout / poll_interval
    ## @rationale — D5: one-shot init container exits 0, breaking --wait contract.
    ##              Separate health poll avoids coupling to createbuckets lifecycle.
    """
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            ps_result = subprocess.run(
                [*compose_base_args, "ps", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if ps_result.returncode != 0:
                logger.warning("[IMP:8][conftest][_wait_for_minio_healthy] docker compose ps failed")
                _time.sleep(2)
                continue

            # Parse JSONL output — one JSON object per line (one per container)
            for line in ps_result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    container = json.loads(line)
                except json.JSONDecodeError:
                    continue

                service = container.get("Service", "")
                state = container.get("State", "")
                health = container.get("Health", "")

                if service == "minio" and state == "running" and health == "healthy":
                    logger.info(
                        "[IMP:9][conftest][_wait_for_minio_healthy] MinIO is healthy (service=%s state=%s health=%s)",
                        service,
                        state,
                        health,
                    )
                    return True

            _time.sleep(2)

        except subprocess.TimeoutExpired:
            logger.warning("[IMP:8][conftest][_wait_for_minio_healthy] docker compose ps timed out")
            _time.sleep(2)

    logger.warning("[IMP:9][conftest][_wait_for_minio_healthy] MinIO not healthy within %ds", timeout)
    return False


def _build_waves(module_graph: dict[str, list[str]]) -> list[list[str]]:
    """Group modules into waves by dependency depth.

    ## @purpose — Convert module_graph (topologically sorted) into waves of
    ##            independent modules that can be started in parallel.
    ##            Wave 0: modules with no dependencies.
    ##            Wave N: modules whose dependencies are all in waves < N.
    ## @io — ⇥ module_graph {module → [deps]} → ⎋ list[list[str]] waves
    ## @complexity — O(M * D) where M=modules, D=avg dependencies per module
    ## @invariants
    ##   - module_graph is already topologically sorted (dict insertion order)
    ##   - Unassigned dependencies default to wave -1 (safe fallback)
    ##   - Result preserves module order within each wave
    """
    _logger = logging.getLogger(__name__)
    assigned: dict[str, int] = {}

    for module_name, deps in module_graph.items():
        if not deps:
            assigned[module_name] = 0
        else:
            # Find the highest wave among all dependencies
            max_dep_wave = max(assigned.get(dep, -1) for dep in deps)
            assigned[module_name] = max_dep_wave + 1

    if not assigned:
        return []

    max_wave = max(assigned.values())
    waves: list[list[str]] = [[] for _ in range(max_wave + 1)]
    for module_name, wave_idx in assigned.items():
        waves[wave_idx].append(module_name)

    _logger.info(
        "[IMP:8][conftest][_build_waves] Built %d wave(s) from %d module(s)",
        len(waves),
        len(assigned),
    )
    return waves


def _start_single_module(
    module_name: str,
    compose_path: str,
    all_compose_files: dict[str, str],
    module_graph: dict[str, list[str]],
    platform_ports: dict[str, int],
    compose_timeout: int,
    compose_extra_timeout: int,
    compose_down_timeout: int,
    docker_log_timeout: int,
    stderr_tail_lines: int,
    loki_timeout: int,
) -> dict:
    """Start one module's compose stack, return success/failure status.

    ## @purpose — Extracted from platform_services loop for wave-parallel execution.
    ##            Each call handles one module's lifecycle: pre-cleanup down,
    ##            compose up (with minio --wait workaround), post-up container
    ##            existence check, and loki readiness HTTP poll.
    ## @io — ⇥ module params → ⎋ dict with "success": bool, "module_name": str
    ## @complexity — O(1) per module (subprocess calls + optional HTTP poll)
    ## @invariants
    ##   - compose_path must be a valid file path (validated by caller)
    ##   - Pre-cleanup down intentionally WITHOUT --remove-orphans (T3b fix)
    ##   - MinIO uses up -d without --wait, then polls health directly (D5)
    ##   - Post-up container existence check catches silent CI compose failures
    ##   - Loki readiness poll only runs for module_name == "observability"
    ## @rationale — Extracting to a standalone function allows ThreadPoolExecutor
    ##              to call it in parallel threads. Each module runs its own
    ##              compose subprocess, so GIL is not a bottleneck (I/O-bound).
    """
    _logger = logging.getLogger(__name__)

    if compose_path is None:
        _logger.warning(
            "[IMP:8][conftest][_start_single_module] No compose path for '%s'",
            module_name,
        )
        return {"success": False, "module_name": module_name}

    # ── Build compose base args (files + project name) ──────────────
    compose_base_args = ["docker", "compose", "-f", compose_path]
    test_override = os.path.join(os.path.dirname(compose_path), "docker-compose.test.yml")
    if os.path.exists(test_override):
        compose_base_args.extend(["-f", test_override])
    macos_override = os.path.join(os.path.dirname(compose_path), "docker-compose.macos.yml")
    if _platform.system() == "Darwin" and os.path.exists(macos_override):
        compose_base_args.extend(["-f", macos_override])
    compose_base_args.extend(["-p", "ai-platform-test"])

    # ── Pre-cleanup: docker compose down before up (SAME module only) ─
    # NOTE: intentionally WITHOUT --remove-orphans — that flag in a per-module
    # down would kill previously started modules' containers since all modules
    # share the same compose project name (ai-platform-test) but are defined
    # in different compose files. Global cleanup above already removed orphans.
    _down_args = [*compose_base_args, "down", "--timeout", str(compose_down_timeout)]
    _run_docker_smoke(_down_args, timeout=20, env_override={"COMPOSE_PROFILES": module_name})

    # ── Start up ──────────────────────────────────────────────────
    if module_name == "minio":
        # D5: MinIO has a one-shot init container (minio-createbuckets) that
        # exits 0 after creating buckets. `docker compose up --wait` considers
        # the exited container a failure (returncode 1) even though minio
        # itself is healthy. Start without --wait, then poll for minio health.
        # ⚠️ TRAP[DECISION] · 2026-07-17 · — · MinIO --wait workaround
        # · Rejected: depends_on condition: service_completed_successfully
        # · Reason: deferred — that would require compose schema v3.8+ changes
        # · Rev: if compose schema is ever upgraded, use depends_on instead.
        compose_up_args = [*compose_base_args, "up", "-d"]
        result = _run_docker_smoke(
            compose_up_args,
            timeout=compose_timeout + compose_extra_timeout,
            env_override={"COMPOSE_PROFILES": module_name},
        )
        if result.returncode == 0:
            _minio_ok = _wait_for_minio_healthy(
                compose_base_args=compose_base_args,
                timeout=compose_timeout,
                logger=_logger,
            )
            if not _minio_ok:
                _logger.error(
                    "[IMP:9][conftest][_start_single_module] MinIO did not become healthy within %ds",
                    compose_timeout,
                )
                # Force failure path after the if-else block
                result = subprocess.CompletedProcess(
                    args=compose_up_args, returncode=1, stdout="", stderr="MinIO health check timeout"
                )
    else:
        compose_up_args = [
            *compose_base_args,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            str(compose_timeout),
        ]
        result = _run_docker_smoke(
            compose_up_args,
            timeout=compose_timeout + compose_extra_timeout,
            env_override={"COMPOSE_PROFILES": module_name},
        )

    if result.returncode != 0:
        # ── Diagnostic: collect logs for failure analysis ─────────────
        log_args = [*compose_base_args, "logs", "--tail", "50", "--no-color"]
        logs = _run_docker_smoke(log_args, timeout=docker_log_timeout, env_override={"COMPOSE_PROFILES": module_name})
        _logger.error(
            "[IMP:9][conftest][_start_single_module] Failed to start '%s' — "
            "returncode=%d\nstderr: %s\ndiagnostic logs:\n%s",
            module_name,
            result.returncode,
            result.stderr.strip()[-stderr_tail_lines:],
            (logs.stdout or logs.stderr).strip()[-500:],
        )
        return {"success": False, "module_name": module_name}

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
            "[IMP:9][conftest][_start_single_module] '%s' compose up returned 0 but "
            "no containers exist (docker compose ps --all = empty). "
            "CI runner silent compose failure — treating as failed.",
            module_name,
        )
        return {"success": False, "module_name": module_name}

    # ---- Loki readiness HTTP-poll ------------------------------------
    if module_name == "observability":
        _loki_ready = _wait_for_loki_ready(
            url=f"http://localhost:{platform_ports['LOKI_PORT']}/ready",
            timeout=loki_timeout,
            logger=_logger,
        )
        if not _loki_ready:
            _logger.warning(
                "[IMP:9][conftest][_start_single_module] Loki /ready timeout - proceeding",
            )

    return {"success": True, "module_name": module_name}


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

    # ── Global pre-cleanup: down ALL compose files before starting ─────────
    # ⚠️ TRAP[BUG] · 2026-07-17 · HI · per-module `down --remove-orphans` killed
    #    previously started modules (all share project=ai-platform-test). Fix:
    #    global cleanup at start, per-module down WITHOUT --remove-orphans.
    _logger.info("[IMP:8][conftest][platform_services] Global pre-cleanup: down all compose files")
    for _cleanup_name, _cleanup_path in sorted(all_compose_files.items()):
        _cleanup_args = ["docker", "compose", "-f", _cleanup_path]
        _test_override = os.path.join(os.path.dirname(_cleanup_path), "docker-compose.test.yml")
        if os.path.exists(_test_override):
            _cleanup_args.extend(["-f", _test_override])
        _cleanup_args.extend(
            ["-p", "ai-platform-test", "down", "--timeout", str(_COMPOSE_DOWN_TIMEOUT), "--remove-orphans"]
        )
        _run_docker_smoke(_cleanup_args, timeout=20)
    _logger.info("[IMP:8][conftest][platform_services] Global pre-cleanup complete")

    # ── Safety net: remove stale test containers from crashed previous runs ──
    # ⚠️ TRAP[BUG] · 2026-07-18 · D3 · stale containers block test run
    # · docker compose down does not remove containers created with a different
    # · set of compose files (project labels don't match). After crash (Ctrl+C,
    # · OOM) containers remain and block container names ("already in use").
    # · Safety net: docker rm -f by known test container names — unconditional,
    # · idempotent.
    _STALE_CONTAINER_NAMES = [
        "nginx-test",
        "prometheus-test",
        "grafana-test",
        "hermes-agent-test",
        "prometheus-config-init",
    ]
    for _cname in _STALE_CONTAINER_NAMES:
        subprocess.run(
            ["docker", "rm", "-f", _cname],
            capture_output=True,
            text=True,
            timeout=10,
        )
    _logger.info("[IMP:8][conftest][platform_services] Safety net: stale containers removed")

    # ── Start compose files in wave-parallel order ──────────────────────────
    started: list[str] = []
    failed: list[str] = []
    waves = _build_waves(module_graph)
    _logger.info(
        "[IMP:8][conftest][platform_services] Built %d wave(s) from %d module(s)",
        len(waves),
        len(module_graph),
    )

    for wave_idx, wave_modules in enumerate(waves):
        _logger.info(
            "[IMP:8][conftest][platform_services] Wave %d: starting %d module(s) in parallel",
            wave_idx,
            len(wave_modules),
        )

        with ThreadPoolExecutor(max_workers=len(wave_modules)) as executor:
            futures = {}
            for _wm_module_name in wave_modules:
                _wm_compose_path = all_compose_files.get(_wm_module_name)
                if _wm_compose_path is None:
                    _logger.warning(
                        "[IMP:8][conftest][platform_services] Wave %d: no compose path for '%s' — skipping",
                        wave_idx,
                        _wm_module_name,
                    )
                    continue
                future = executor.submit(
                    _start_single_module,
                    _wm_module_name,
                    _wm_compose_path,
                    all_compose_files,
                    module_graph,
                    platform_ports,
                    PLATFORM_COMPOSE_TIMEOUT,
                    _COMPOSE_EXTRA_TIMEOUT,
                    _COMPOSE_DOWN_TIMEOUT,
                    _DOCKER_LOG_TIMEOUT,
                    _STDERR_TAIL_LINES,
                    PLATFORM_LOKI_TIMEOUT,
                )
                futures[future] = _wm_module_name

            for future in as_completed(futures):
                _wm_module_name = futures[future]
                try:
                    _wm_result = future.result()
                    if isinstance(_wm_result, dict) and _wm_result.get("success"):
                        started.append(_wm_module_name)
                    else:
                        failed.append(_wm_module_name)
                except Exception as exc:
                    _logger.error(
                        "[IMP:9][conftest][platform_services] Wave %d module '%s' raised: %s",
                        wave_idx,
                        _wm_module_name,
                        exc,
                    )
                    failed.append(_wm_module_name)

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
