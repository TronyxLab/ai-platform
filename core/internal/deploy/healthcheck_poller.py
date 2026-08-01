#!/usr/bin/env python3
"""
Shared healthcheck poller for DeployOrchestrator. Extracted from context_deployer._shared_healthcheck_poll() + docker_compose.py.
"""
# GREP_SUMMARY: healthcheck, poll, http, docker-inspect, retry, timeout, health-status
# STRUCTURE: ▶ HealthcheckPoller.__init__(timeout, interval, max_retries) → ○ poll_project(project_name) → ◇ HTTP GET /health → ◇ docker inspect → ⎋ str(healthy|unhealthy)
# region MODULE_CONTRACT
## @purpose  Shared healthcheck polling utility. Supports two protocols:
##           1. HTTP GET /health → 200 (web services)
##           2. docker inspect → State.Health.Status (workers/daemons)
##           Extracted from context_deployer._shared_healthcheck_poll() + docker_compose.py.healthcheck_poll().
## @scope    Used by DeployOrchestrator after deploy to verify health. Single poll, not a lifecycle manager.
## @invariants
##   1. timeout: 30s per check by default
##   2. retry interval: 10s between attempts
##   3. max retries: 6 (total ~60s polling window)
##   4. HTTP check: GET /health endpoint, 200 = healthy
##   5. Docker check: inspect State.Health.Status == "healthy" OR State.Status == "running" (no healthcheck)
##   6. Non-fatal: returns "unhealthy" on failure, does NOT raise
## @rationale DevPlan 089 DD4: context_deployer AND DeployEngine both do healthcheck →
##            double work. Single HealthcheckPoller used once by DeployOrchestrator.
## @changes 2026-07-30 | DevPlan 089 T5 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from core.internal.shared.timeouts import HEALTHCHECK_POLL_MAX_RETRIES, PROJECT_HEALTHCHECK_PORTS

logger = logging.getLogger(__name__)

DEFAULT_POLL_TIMEOUT = 30
DEFAULT_POLL_INTERVAL = 10
# Единый реестр retry-политик — timeouts.py (DevPlan 117 D34). Число retry-попыток
# healthcheck-poll импортируется из timeouts.HEALTHCHECK_POLL_MAX_RETRIES.
DEFAULT_MAX_RETRIES = HEALTHCHECK_POLL_MAX_RETRIES


@dataclass
class HealthcheckResult:
    """Result of a healthcheck poll.

    ## @purpose — Encapsulate healthcheck outcome and metadata.
    ## @io — ⇥ constructor params → ⎋ HealthcheckResult
    ## @complexity — O(1)
    """

    status: str  # "healthy", "unhealthy", "timeout"
    project: str
    method: str  # "http", "docker", "unknown"
    attempts: int = 0
    detail: str = ""


# region CLASS_HealthcheckPoller


class HealthcheckPoller:
    """Poll project health via HTTP or Docker inspect.

    ## @purpose — Verify project health after deploy. Supports HTTP GET /health
    ##            for web services and docker inspect for workers/daemons.
    ## @io — ⇥ project_name → ⎋ HealthcheckResult
    ## @complexity — O(max_retries) — each attempt is O(1)
    ## @invariants
    ##   - Non-fatal: returns "unhealthy" on any failure
    ##   - HTTP check: 200 on /health = healthy
    ##   - Docker check: State.Health.Status == "healthy" or running without healthcheck
    ##   - Total poll window = interval × max_retries
    """

    def __init__(
        self,
        timeout: int = DEFAULT_POLL_TIMEOUT,
        interval: int = DEFAULT_POLL_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.timeout = timeout
        self.interval = interval
        self.max_retries = max_retries

    def poll_project(self, project_name: str, project_dir: str | None = None) -> HealthcheckResult:
        """Poll project health until healthy or retries exhausted.

        Args:
            project_name: Project name for container/URL resolution.
            project_dir: Optional project directory for docker compose operations.

        Returns:
            HealthcheckResult with status.
        """
        # Try HTTP healthcheck first
        http_result = self._try_http(project_name)
        if http_result:
            logger.info("[IMP:9][HealthcheckPoller][http] %s healthy via HTTP", project_name)
            return HealthcheckResult(status="healthy", project=project_name, method="http", attempts=1)

        # Fall back to Docker inspect
        if project_dir:
            return self._try_docker(project_name, project_dir)

        logger.warning("[IMP:7][HealthcheckPoller][unknown] %s: no healthcheck method available", project_name)
        return HealthcheckResult(
            status="unhealthy",
            project=project_name,
            method="unknown",
            attempts=1,
            detail="No healthcheck method available",
        )

    def poll_until_healthy(self, project_name: str, project_dir: str | None = None) -> HealthcheckResult:
        """Poll repeatedly until healthy or max_retries exhausted.

        Args:
            project_name: Project name.
            project_dir: Optional project directory.

        Returns:
            HealthcheckResult with final status.
        """
        for attempt in range(1, self.max_retries + 1):
            result = self.poll_project(project_name, project_dir)
            if result.status == "healthy":
                return result

            logger.info(
                "[IMP:8][HealthcheckPoller][retry] %s attempt %d/%d: %s — retrying in %ds",
                project_name,
                attempt,
                self.max_retries,
                result.status,
                self.interval,
            )
            time.sleep(self.interval)

        return HealthcheckResult(
            status="timeout",
            project=project_name,
            method="unknown",
            attempts=self.max_retries,
            detail=f"Healthcheck timeout after {self.max_retries * self.interval}s",
        )

    def _try_url(self, url: str) -> bool:
        """Try a single healthcheck URL. Returns True if 200 OK.

        ## @purpose — Isolate try-except from loop to avoid PERF203.
        ## @io — ⇥ url: str → ⎋ bool
        ## @complexity — O(1)
        """
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — internal healthcheck endpoint
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            return False

    def _try_http(self, project_name: str) -> bool:
        """Try HTTP GET /health for a project.

        Args:
            project_name: Project/container name for host resolution.

        Returns:
            True if HTTP /health returns 200.
        """
        # Эвристические HTTP /health порты — из timeouts.PROJECT_HEALTHCHECK_PORTS (DevPlan 117 D36).
        urls = [f"http://{project_name}:{port}/health" for port in PROJECT_HEALTHCHECK_PORTS]
        urls.append(f"http://{project_name}/health")

        return any(self._try_url(url) for url in urls)

    def _try_docker(self, project_name: str, project_dir: str) -> HealthcheckResult:
        """Try Docker health via shared healthcheck_poll (sole path — DevPlan 116 B5 T7.3).

        Args:
            project_name: Project/container name.
            project_dir: Project directory (unused — shared docker-path is global via docker ps).

        Returns:
            HealthcheckResult with Docker health status.
        """
        # Единый docker-критерий живёт ТОЛЬКО в shared/docker_compose.healthcheck_poll (D5, T3.4).
        # HTTP-путь (GET /health) остаётся в poller — это отдельная HTTP-политика, не docker-критерий.
        from core.internal.shared.docker_compose import healthcheck_poll as _shared_healthcheck_poll

        status = _shared_healthcheck_poll(project_name, timeout=self.timeout, interval=self.interval)
        if status == "healthy":
            logger.info("[IMP:9][HealthcheckPoller][docker] %s healthy (inspect criterion)", project_name)
            return HealthcheckResult(
                status="healthy",
                project=project_name,
                method="docker",
                attempts=1,
                detail="docker inspect State.Health (shared criterion)",
            )

        logger.info(
            "[IMP:8][HealthcheckPoller][docker] %s not healthy after %ds poll window",
            project_name,
            self.timeout,
        )
        return HealthcheckResult(
            status="timeout",
            project=project_name,
            method="docker",
            attempts=self.max_retries,
            detail="Docker healthcheck timeout (shared criterion)",
        )


# endregion CLASS_HealthcheckPoller
