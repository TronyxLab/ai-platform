# GREP_SUMMARY: reuse, foreign-container, docker-inspect, compose-project, container-reuse, guard
# STRUCTURE: ┌check_foreign_containers(docker inspect → compose project label)┐ → ◇ foreign project != own → ⊕ list[str] foreign → ⎋ reuse decision
# region MODULE_CONTRACT
## @purpose  Universal foreign container detection for module-scoped Docker test fixtures.
##           Extracted from test_smoke_postgres.py inline foreign guard pattern.
##           Before starting a compose stack, check if required container names are already
##           running under a DIFFERENT compose project (typically platform_services session).
##           If so, skip compose up/down and reuse the existing containers.
## @scope    Used by all module-scoped Docker fixtures that share container names with
##           the session-scoped platform_services fixture.
## @invariants
##   - check_foreign_containers inspects com.docker.compose.project label on each container
##   - Returns list of container names that belong to a foreign project
##   - Empty list → no foreign containers → safe to start own compose stack
##   - Non-empty list → caller should skip compose lifecycle and reuse existing containers
##   - All functions are pure (no side effects beyond docker inspect subprocess)
## @rationale 70% of test time (~350s) is compose up/down cycles. Module fixtures that
##            reuse platform_services containers eliminate duplicate compose ops.
##            Extracting shared logic into reuse.py prevents copy-paste drift across 7 fixtures.
## @changes CREATED: 2026-07-22 | DevPlan 040 Wave 1: Container Name Conflict Resolution
# endregion MODULE_CONTRACT

import logging
import subprocess

_logger = logging.getLogger(__name__)


def check_foreign_containers(container_names: list[str], own_project: str) -> dict[str, str]:
    """Check if any containers belong to a different compose project.

    ## @purpose — Before starting a compose stack, verify that the target container names
    ##            are not already running under a different compose project.
    ##            Uses docker inspect to read com.docker.compose.project label.
    ##            Foreign containers cause "container name already in use" errors on compose up.
    ## @io — ⇥ container_names: list[str] — names to check (e.g. ["postgres-test", "pgbouncer-test"])
    ##       ⇥ own_project: str — this fixture's compose project name
    ##       → ⎋ dict[str, str] — {container_name: compose_project} for foreign containers (empty → safe to start)
    ## @complexity — O(N) where N = len(container_names), each call is a docker inspect subprocess
    ## @invariants
    ##   - inspect format = {{index .Config.Labels "com.docker.compose.project"}}
    ##   - returncode != 0 → container does not exist → not foreign
    ##   - empty project label → unlabeled container → not foreign
    ##   - project == own_project → same project → not foreign
    ## @changes 2026-07-22 | Return type changed from list[str] to dict[str,str] —
    ##          fixtures need the foreign project name for docker compose commands in reuse mode.
    ## @rationale — Centralizing foreign container detection eliminates 6 copies of the same
    ##              docker inspect pattern across module fixtures, preventing drift and bugs
    ##              like the clickhouse-test/redis-test gap (2 stale containers not in _STALE_...).
    """
    foreign: dict[str, str] = {}
    for name in container_names:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    name,
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project"}}',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            _logger.warning(
                "[IMP:7][reuse][check_foreign_containers] docker inspect %s failed: %s",
                name,
                exc,
            )
            continue

        if result.returncode != 0:
            # Container does not exist — not foreign
            continue

        project = result.stdout.strip()
        if project and project != own_project:
            foreign[name] = project
            _logger.info(
                "[IMP:8][reuse][check_foreign_containers] Container '%s' belongs to foreign project '%s' "
                "(own='%s') — will reuse",
                name,
                project,
                own_project,
            )

    if foreign:
        _logger.info(
            "[IMP:7][reuse][check_foreign_containers] %d foreign container(s) detected: %s — reuse mode active",
            len(foreign),
            dict(foreign),
        )
    else:
        _logger.info(
            "[IMP:7][reuse][check_foreign_containers] No foreign containers detected — safe to start own stack",
        )

    return foreign


def wait_for_containers_healthy(
    container_names: list[str],
    max_retries: int = 20,
    retry_interval: int = 3,
    logger: logging.Logger | None = None,
) -> dict[str, str]:
    """Poll docker inspect health status until all containers are healthy or timeout.

    ## @purpose — After detecting foreign containers (reuse mode), wait for them to become
    ##            healthy before yielding the fixture. Polls each container's
    ##            .State.Health.Status field.
    ## @io — ⇥ container_names, max_retries, retry_interval, logger
    ##       → ⎋ dict[str, str] — {container_name: health_status} after polling
    ## @complexity — O(N * R) where N = containers, R = retries
    ## @invariants
    ##   - Returns status dict regardless of health (caller decides pass/fail)
    ##   - Typical healthy timeout: 20 * 3s = 60s per container
    ##   - Missing container (docker inspect fails) → status "not_found"
    ## @rationale — Shared health-poll logic eliminates 7 copies of the same polling
    ##              loop across module fixtures.
    """
    log = logger or _logger

    for attempt in range(1, max_retries + 1):
        statuses: dict[str, str] = {}
        for cname in container_names:
            try:
                result = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Health.Status}}", cname],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                statuses[cname] = result.stdout.strip() if result.returncode == 0 else "not_found"
            except (subprocess.TimeoutExpired, OSError) as exc:
                statuses[cname] = f"error: {exc}"

        if all(s == "healthy" for s in statuses.values()):
            log.info(
                "[IMP:9][reuse][wait_for_containers_healthy] All containers healthy (attempt %d): %s",
                attempt,
                statuses,
            )
            return statuses

        log.info(
            "[IMP:7][reuse][wait_for_containers_healthy] Waiting for containers (attempt %d/%d): %s",
            attempt,
            max_retries,
            statuses,
        )

        if attempt < max_retries:
            import time

            time.sleep(retry_interval)

    log.warning(
        "[IMP:9][reuse][wait_for_containers_healthy] Containers NOT healthy after %ds: %s",
        max_retries * retry_interval,
        {c: s for c, s in statuses.items() if s != "healthy"},  # type: ignore[possibly-undefined]
    )
    return statuses  # type: ignore[possibly-undefined]
