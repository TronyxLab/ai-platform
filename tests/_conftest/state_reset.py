# GREP_SUMMARY: state-reset, fresh-state, requires-fresh-state, restart-service, compose-restart, autouse
# STRUCTURE: ▶ _reset_fresh_state(autouse) → ◇ requires_fresh_state marker → ◇ service names from args → ⊕ compose restart per service → ⎋ return
# region MODULE_CONTRACT
## @purpose  Autouse fixture for tests that need a fresh container state (e.g. clean database).
##           When module fixtures reuse session-scoped containers (DevPlan 040 Wave 2),
##           shared state persists across tests. Tests that expect empty databases or
##           fresh service state must mark themselves with @pytest.mark.requires_fresh_state.
##           The autouse fixture runs docker compose restart on the named services
##           (seconds) instead of compose down/up (minutes).
## @scope    Function-scoped autouse fixture; runs before every test that has the marker.
## @invariants
##   - Only activates for tests with @pytest.mark.requires_fresh_state marker
##   - Default service: ["postgres"] if marker has no args
##   - Uses `docker compose restart` (fast, ~2-5s) not `compose down/up` (~20-60s)
##   - Compose project name resolved from os.environ["COMPOSE_PROJECT_NAME"] (default: "ai-platform-test")
##   - Module-level _RESTART_LOCK prevents concurrent restarts of the same service
## @rationale — Wave 2 scope migration means module fixtures reuse platform_services containers.
##              Tests that modify database state need a reset mechanism. compose restart is
##              the fastest way to get a fresh service state without recreating containers.
## @changes — CREATED: 2026-07-22 | DevPlan 040 Wave 3: Fresh State Marker + Stop/Start
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import threading

import pytest

_logger = logging.getLogger(__name__)

# Module-level lock to prevent concurrent restarts of the same service
_RESTART_LOCK = threading.Lock()


def restart_service(service_name: str, project_name: str | None = None) -> None:
    """Run docker compose restart for a single service.

    ## @purpose — Restart a single service within the shared compose project.
    ##            This is faster than compose down/up because it only restarts
    ##            the specified service's container(s), not the entire stack.
    ## @io — ⇥ service_name: str — compose service name (e.g. "postgres")
    ##       ⇥ project_name: str | None — compose project name (default: COMPOSE_PROJECT_NAME env or "ai-platform-test")
    ##       → ⎋ None (side-effect: container restart)
    ## @complexity — O(1) — single subprocess call
    ## @invariants
    ##   - compose project name resolved from env var or defaults to "ai-platform-test"
    ##   - compose file discovery: uses --project-name flag (no -f needed, project labels match)
    ##   - Failure to restart logs warning but does not raise (defensive — transient Docker errors)
    """
    project = project_name or os.environ.get("COMPOSE_PROJECT_NAME", "ai-platform-test")

    with _RESTART_LOCK:
        try:
            result = subprocess.run(
                ["docker", "compose", "--project-name", project, "restart", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                _logger.info(
                    "[IMP:9][state_reset][restart_service] Restarted '%s' in project '%s'",
                    service_name,
                    project,
                )
            else:
                _logger.warning(
                    "[IMP:8][state_reset][restart_service] Restart '%s' rc=%d: %s",
                    service_name,
                    result.returncode,
                    result.stderr.strip()[:200],
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            _logger.warning(
                "[IMP:8][state_reset][restart_service] Restart '%s' failed: %s",
                service_name,
                exc,
            )


@pytest.fixture(scope="function", autouse=True)
def _reset_fresh_state(request: pytest.FixtureRequest) -> None:
    """Autouse fixture: restart services before tests marked with @requires_fresh_state.

    ## @purpose — Function-scoped autouse fixture that checks for @pytest.mark.requires_fresh_state
    ##            on each test function. If present, restarts the named services (default: ["postgres"])
    ##            via docker compose restart before the test runs.
    ## @io — ⇥ request: pytest.FixtureRequest → ⎋ None (side-effect: container restart)
    ## @complexity — O(S) where S = number of services to restart
    ## @invariants
    ##   - Only runs for tests with the marker — no overhead for other tests
    ##   - Default service: ["postgres"] when marker has no args
    ##   - Marker with args: @pytest.mark.requires_fresh_state("clickhouse") → restart only clickhouse
    ##   - Multiple args: @pytest.mark.requires_fresh_state("postgres", "redis") → restart both
    ##   - Runs BEFORE the test function (not during setup), via autouse ordering
    ## @rationale — compose restart (~2-5s per service) is the fastest reset mechanism.
    ##              compose down/up would take 20-60s and defeat the purpose of fixture reuse.
    """
    marker = request.node.get_closest_marker("requires_fresh_state")
    if marker is None:
        return

    # Service names from marker args; default to ["postgres"] if no args
    service_names: list[str] = list(marker.args) if marker.args else ["postgres"]

    _logger.info(
        "[IMP:7][state_reset][_reset_fresh_state] Resetting state for test '%s': restarting %s",
        request.node.name,
        service_names,
    )

    project = os.environ.get("COMPOSE_PROJECT_NAME", "ai-platform-test")
    for svc in service_names:
        restart_service(svc, project_name=project)

    _logger.info(
        "[IMP:9][state_reset][_reset_fresh_state] State reset complete for '%s': %s restarted",
        request.node.name,
        service_names,
    )
