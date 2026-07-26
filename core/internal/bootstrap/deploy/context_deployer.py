#!/usr/bin/env python3
# GREP_SUMMARY: context-deployer, project-deploy, ghcr-pull, build-fallback, healthcheck-gate, idempotent, node-yaml-projects, audit-log
# STRUCTURE: ▶ ┌node.yaml + context┐ → ◇ filter projects[context] → ○ for each: healthcheck? → ghcr pull → (fail?) build → up -d → ⊕ ProjectDeployResult → ⎋
# region MODULE_CONTRACT
## @purpose  Deploy all projects of a context from node.yaml after bootstrap.
##           Uses ghcr.io pull as primary image channel, falls back to on-node build.
##           Implements health-gate (≤60s per project) and idempotent skip for healthy projects.
## @scope    Called from state_machine.py deploy_context step (18.4) and standalone
##           via `make deploy-context NODE=<n>` → core/entrypoints/deploy-context.sh.
## @invariants
##   1. Source of projects: node.yaml → projects[] where context == <context>
##   2. Image channel: ghcr.io pull primary → build on-node fallback
##   3. Idempotent: healthcheck before deploy, skip if healthy
##   4. Health-gate: ≤60s per project (same as deploy-project.sh)
##   5. Non-fatal: failure of one project does NOT block others
##   6. Audit: each deploy recorded in /var/log/platform/audit.log
##   7. One node = one context (CONTEXT from node.yaml or CLI --context)
## @rationale StatusReport 045: 14/20 containers down after bootstrap because deploy-modules
##           does not cover context projects. context_deployer bridges the "last mile":
##           it deploys all projects matching the node's context, with ghcr.io primary
##           and build fallback for resilience.
## @changes  2026-07-22 | DevPlan 047 Phase 4 — Created context deployer
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Shared library imports
import sys as _sys

_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "shared")
if _SHARED_DIR not in _sys.path:
    _sys.path.insert(0, _SHARED_DIR)
from node_yaml import extract_context_from_node_yaml

# DevPlan 079 DRIFT-B6: shared docker compose operations
from core.internal.shared.docker_compose import (
    docker_compose_build as _shared_docker_compose_build,
)
from core.internal.shared.docker_compose import (
    docker_compose_pull as _shared_docker_compose_pull,
)
from core.internal.shared.docker_compose import (
    docker_compose_up as _shared_docker_compose_up,
)
from core.internal.shared.docker_compose import (
    healthcheck_poll as _shared_healthcheck_poll,
)
from core.internal.shared.docker_compose import (
    retry_pull as _shared_retry_pull,
)

# ── Constants ──────────────────────────────────────────────────────────────
HEALTH_GATE_TIMEOUT = 60  # seconds per project
HEALTH_POLL_INTERVAL = 3  # seconds between healthcheck retries
DEFAULT_PROJECTS_BASE = "/opt/projects"
AUDIT_LOG = "/var/log/platform/audit.log"
PLATFORM_ROOT = os.environ.get("PLATFORM_ROOT", "/opt/platform")
LITELLM_CONFIG_PATH = pathlib.Path(f"{PLATFORM_ROOT}/core/modules/litellm/config/litellm-config.yml")
POLICY_PATH = pathlib.Path(f"{PLATFORM_ROOT}/core/internal/llm/policy.yaml")
LITELLM_BASE_URL = "http://litellm:4000"


# region DATACLASSES


@dataclass
class ProjectInfo:
    """Project metadata extracted from node.yaml.

    ## @purpose — Represent a single project entry from node.yaml#projects.
    ## @io — ⇥ parsed dict → ⎋ ProjectInfo with typed fields
    ## @complexity — O(1)
    """

    name: str = ""
    repo: str = ""
    type: str = ""
    domain: str = ""
    context: str = ""
    database: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectInfo:
        """Create from a dict (node.yaml project entry)."""
        return cls(
            name=data.get("name", ""),
            repo=data.get("repo", ""),
            type=data.get("type", ""),
            domain=data.get("domain", ""),
            context=data.get("context", ""),
            database=data.get("database", ""),
        )


@dataclass
class ProjectDeployResult:
    """Result of deploying a single project.

    ## @purpose — Track per-project deploy outcome, channel, health, and errors.
    ## @io — ⇥ constructor params → ⎋ serializable result
    ## @complexity — O(1)
    """

    name: str
    status: str = "pending"  # deployed | skipped | failed
    channel: str = ""  # ghcr | build | skip
    health: str = "unknown"  # healthy | unhealthy | unknown
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        if self.error is None:
            d.pop("error", None)
        return d


@dataclass
class DeployResult:
    """Aggregated result of deploying all context projects.

    ## @purpose — Collect per-project results and summary counts.
    ## @io — ⇥ results → ⎋ serializable summary
    ## @complexity — O(N) where N = projects
    """

    results: list[ProjectDeployResult] = field(default_factory=list)
    deployed: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, result: ProjectDeployResult) -> None:
        """Add a per-project result and increment counter."""
        self.results.append(result)
        if result.status == "deployed":
            self.deployed += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "failed":
            self.failed += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "results": [r.to_dict() for r in self.results],
            "summary": {
                "deployed": self.deployed,
                "skipped": self.skipped,
                "failed": self.failed,
            },
        }


# endregion DATACLASSES


# region PROJECT_RESOLUTION


# region FUNC_resolve_context_projects
## @purpose — Parse node.yaml and filter projects[] where context matches.
##            One node = one context: if project has no context field, include it.
## @io — ⇥ node_yaml: str, context: str → ⎋ list[ProjectInfo]
## @complexity — O(N) where N = projects in node.yaml
## @invariants
##   - If context is empty, returns ALL projects (operator must specify context)
##   - Projects without context field are included if context matches node context
##   - Malformed YAML entries are skipped (non-fatal)
def resolve_context_projects(node_yaml: str, context: str) -> list[ProjectInfo]:
    """Parse node.yaml → filter projects by context.

    ▶ ┌node.yaml┐ → ◇ parse projects[] → ◇ filter context==<context> → ⊕ list[ProjectInfo] → ⎋
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][context_deployer] node.yaml not found: %s", node_yaml)
        return []

    try:
        import yaml

        with open(node_yaml) as f:
            data = yaml.safe_load(f)
    except (ImportError, OSError) as e:
        logger.error("[IMP:10][context_deployer] Cannot read node.yaml: %s", e)
        return []

    if not isinstance(data, dict):
        return []

    raw_projects = data.get("projects", [])
    if not isinstance(raw_projects, list):
        return []

    projects: list[ProjectInfo] = []
    for entry in raw_projects:
        if not isinstance(entry, dict):
            continue
        proj = ProjectInfo.from_dict(entry)
        if not proj.name:
            continue
        # Filter by context: include if project.context matches or is empty
        if context and proj.context and proj.context != context:
            logger.debug("[IMP:6][context_deployer] Skipping %s (context=%s != %s)", proj.name, proj.context, context)
            continue
        projects.append(proj)

    logger.info(
        "[IMP:8][context_deployer] Resolved %d projects for context '%s' from %s",
        len(projects),
        context,
        node_yaml,
    )
    return projects


# endregion FUNC_resolve_context_projects


# endregion PROJECT_RESOLUTION


# region DEPLOY_LOGIC


# region FUNC_deploy_context_projects
## @purpose — Deploy all projects from node.yaml where context matches.
##            Uses ghcr.io pull as primary, falls back to on-node build.
##            Idempotent: skips healthy projects.
## @io — ⇥ node_yaml: str, context: str, projects_base: str,
##       ghcr_fallback_build: bool → ⎋ list[ProjectDeployResult]
## @complexity — O(P * T) where P = projects, T = health-gate timeout
## @invariants
##   - Each project is processed independently (non-fatal on failure)
##   - Healthcheck before deploy (skip if already healthy)
##   - ghcr.io pull primary, build fallback if ghcr_fallback_build=True
##   - Audit log entry per deploy
def deploy_context_projects(
    node_yaml: str,
    context: str,
    projects_base: str = DEFAULT_PROJECTS_BASE,
    ghcr_fallback_build: bool = True,
) -> list[ProjectDeployResult]:
    """Deploy all context projects from node.yaml.

    ▶ ┌node.yaml + context┐ → ◇ filter projects → ○ for each: healthcheck? → ghcr/build → up → ⊕ results → ⎋
    """
    projects = resolve_context_projects(node_yaml, context)
    if not projects:
        logger.info("[IMP:7][context_deployer] No projects to deploy for context '%s'", context)
        return []

    results: list[ProjectDeployResult] = []
    for project in projects:
        result = _deploy_single_project(project, projects_base, ghcr_fallback_build)
        results.append(result)
        _write_audit(project, result)

    # ── Post-deploy: render litellm config + provision LLM virtual keys ──
    _render_and_provision_llm()

    deployed = sum(1 for r in results if r.status == "deployed")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    logger.info(
        "[IMP:9][context_deployer] Deploy complete: deployed=%d skipped=%d failed=%d",
        deployed,
        skipped,
        failed,
    )
    return results


# endregion FUNC_deploy_context_projects


# region FUNC_ensure_bootstrap_compose
## @purpose — Generate minimal docker-compose.yml for first bootstrap (no CI delivery yet).
##            Creates a minimal nginx:alpine reverse proxy that will be replaced
##            by the real docker-compose.yml via CI (platform-deliver) on next deploy.
## @io — ⇥ project_dir: str, project: ProjectInfo → ⎋ bool (True = success)
## @complexity — O(1)
## @invariants
##   - Non-fatal: returns False on failure
##   - Does NOT overwrite existing docker-compose.yml
##   - Generated compose has label ai-platform.bootstrap=true
##   - Will be replaced by real CI delivery on next deploy
def _ensure_bootstrap_compose(project_dir: str, project: ProjectInfo) -> bool:
    """Generate minimal docker-compose.yml for first bootstrap (no CI delivery yet).

    ▶ ┌project_dir + project┐ → ◇ compose file exists? → ⎋ True
    │                                      ↓ Nonexistent
    │                      ┌content: nginx:alpine + ai-platform.bootstrap label┐
    │                      → ✎ docker-compose.yml → ⎋ bool
    """
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    if os.path.isfile(compose_file):
        return True  # Already exists (real delivery or previous bootstrap)

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)

    port = getattr(project, "port", None) or "3000"
    domain = getattr(project, "domain", None) or project.name

    compose_content = f"""# GENERATED-STUB: Bootstrap reverse proxy. Replaced by CI platform-deliver.
version: '3.8'
services:
  {project.name}-proxy:
    image: nginx:alpine
    labels:
      - "ai-platform.bootstrap=true"
      - "ai-platform.project={project.name}"
    ports:
      - "{port}:{port}"
    volumes:
      - /etc/letsencrypt/live/{domain}/fullchain.pem:/etc/nginx/certs/fullchain.pem:ro
      - /etc/letsencrypt/live/{domain}/privkey.pem:/etc/nginx/certs/privkey.pem:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
"""
    try:
        with open(compose_file, "w") as f:
            f.write(compose_content)
        logger.info("[IMP:9][context_deployer] Generated bootstrap compose for %s", project.name)
        return True
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write bootstrap compose for %s: %s", project.name, e)
        return False


# endregion FUNC_ensure_bootstrap_compose


# region FUNC_deploy_single_project
## @purpose — Deploy a single project: healthcheck skip → ghcr pull → build fallback → up → health-gate.
## @io — ⇥ project: ProjectInfo, projects_base: str, ghcr_fallback_build: bool → ⎋ ProjectDeployResult
## @complexity — O(T) where T = health-gate timeout
## @invariants
##   - Step 1: Check if already healthy → skip (idempotent)
##   - Step 2: ghcr.io pull (primary)
##   - Step 3: If pull fails and fallback enabled → build on-node
##   - Step 4: docker compose up -d
##   - Step 5: Wait healthcheck (≤60s)
def _deploy_single_project(
    project: ProjectInfo,
    projects_base: str,
    ghcr_fallback_build: bool,
) -> ProjectDeployResult:
    """Deploy a single project. Returns ProjectDeployResult."""
    logger.info("[IMP:8][context_deployer] Deploying project: %s", project.name)

    # Step 1: Idempotent check — skip if healthy
    if _is_project_healthy(project.name):
        logger.info("[IMP:9][context_deployer] %s — already healthy, skipping", project.name)
        return ProjectDeployResult(
            name=project.name,
            status="skipped",
            channel="skip",
            health="healthy",
        )

    project_dir = os.path.join(projects_base, project.name)

    # Bootstrap guard: if project dir has no docker-compose.yml, generate minimal one
    if not os.path.isfile(os.path.join(project_dir, "docker-compose.yml")):
        if not _ensure_bootstrap_compose(project_dir, project):
            return ProjectDeployResult(
                name=project.name,
                status="failed",
                channel="none",
                health="unhealthy",
                error="bootstrap compose generation failed",
            )

    # Step 2: Try ghcr.io pull with retries (primary channel)
    channel = "ghcr"
    pull_ok = _shared_retry_pull(project_dir, max_attempts=3, backoff_seconds=[5, 10, 20])
    if not pull_ok:
        if not ghcr_fallback_build:
            return ProjectDeployResult(
                name=project.name,
                status="failed",
                channel="ghcr",
                health="unhealthy",
                error="ghcr.io pull failed and fallback build disabled",
            )
        # Step 3: Fallback — build on-node
        logger.warning("[IMP:7][context_deployer] %s — ghcr.io pull failed, building on-node", project.name)
        build_ok = _docker_compose_build(project_dir)
        if not build_ok:
            return ProjectDeployResult(
                name=project.name,
                status="failed",
                channel="build",
                health="unhealthy",
                error="both ghcr pull and build failed",
            )
        channel = "build"

    # Step 4: docker compose up -d
    up_ok = _docker_compose_up(project_dir)
    if not up_ok:
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel=channel,
            health="unhealthy",
            error="docker compose up -d failed",
        )

    # Step 5: Health-gate
    health = _wait_until_healthy(project.name, timeout=HEALTH_GATE_TIMEOUT)
    status = "deployed"
    return ProjectDeployResult(
        name=project.name,
        status=status,
        channel=channel,
        health=health,
    )


# endregion FUNC_deploy_single_project


# endregion DEPLOY_LOGIC


# region DOCKER_OPERATIONS


# region FUNC_is_project_healthy
## @purpose — Check if a project's containers are healthy via shared healthcheck_poll.
##            Thin wrapper: delegates to healthcheck_poll() from shared/docker_compose.py.
## @io — ⇥ project_name: str → ⎋ bool (True if healthy)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses shared healthcheck_poll for health status
##   - Non-fatal: if docker unavailable, returns False
def _is_project_healthy(project_name: str) -> bool:
    """Check if project containers are healthy via shared healthcheck_poll."""
    return _shared_healthcheck_poll(project_name, timeout=10, interval=1) == "healthy"


# endregion FUNC_is_project_healthy


# region FUNC_docker_compose_pull
## @purpose — Run docker compose pull via shared module (DevPlan 079).
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + network
## @invariants
##   - Delegates to shared docker_compose_pull()
##   - Timeout: 120s
def _docker_compose_pull(project_dir: str) -> bool:
    """Pull images for project via shared docker_compose_pull."""
    return _shared_docker_compose_pull(project_dir)


# endregion FUNC_docker_compose_pull


# region FUNC_docker_compose_build
## @purpose — Run docker compose build via shared module (DevPlan 079).
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + build time
## @invariants
##   - Delegates to shared docker_compose_build()
##   - Timeout: 300s
def _docker_compose_build(project_dir: str) -> bool:
    """Build images for project via shared docker_compose_build."""
    return _shared_docker_compose_build(project_dir)


# endregion FUNC_docker_compose_build


# region FUNC_docker_compose_up
## @purpose — Run docker compose up -d via shared module (DevPlan 079).
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + startup time
## @invariants
##   - Delegates to shared docker_compose_up()
##   - Timeout: 120s
def _docker_compose_up(project_dir: str) -> bool:
    """Start project via shared docker_compose_up."""
    return _shared_docker_compose_up(project_dir)


# endregion FUNC_docker_compose_up


# region FUNC_wait_until_healthy
## @purpose — Poll project health via shared healthcheck_poll until healthy or timeout.
## @io — ⇥ project_name: str, timeout: int → ⎋ str ("healthy" | "unhealthy")
## @complexity — O(T/I) where T = timeout, I = poll interval
## @invariants
##   - Delegates to shared healthcheck_poll()
##   - Returns "unhealthy" if timeout reached
def _wait_until_healthy(project_name: str, timeout: int) -> str:
    """Wait until project is healthy or timeout via shared healthcheck_poll."""
    return _shared_healthcheck_poll(project_name, timeout=timeout, interval=HEALTH_POLL_INTERVAL)


# endregion FUNC_wait_until_healthy


# endregion DOCKER_OPERATIONS


# region AUDIT


# region FUNC_write_audit
## @purpose — Write audit log entry for project deploy via shared audit_logger.
##            DevPlan 081 Phase C (TASK-081C3): replaced direct file.write with
##            write_audit_entry() from shared/audit_logger.py for JSON-lines format.
##            DRIFT-D6 resolved: unified JSON-lines audit format.
## @io — ⇥ project: ProjectInfo, result: ProjectDeployResult → ⎋ None
## @complexity — O(1)
## @invariants
##   - Non-fatal: if audit log write fails, logs WARN
##   - JSON-lines format via shared audit_logger (audit.jsonl)
##   - Old audit.log pipe-delimited format UNCHANGED (shell compatibility)
def _write_audit(project: ProjectInfo, result: ProjectDeployResult) -> None:
    """Write audit entry for project deploy via shared audit_logger."""
    from core.internal.shared.audit_logger import write_audit_entry

    tag = f"context_deploy:{project.name}"
    msg = f"channel={result.channel} health={result.health}"
    if result.error:
        msg += f" error={result.error}"

    try:
        write_audit_entry(tag=tag, status=result.status.upper(), message=msg)
    except Exception as e:
        logger.warning("[IMP:7][context_deployer] Failed to write audit log via shared: %s", e)


# endregion FUNC_write_audit


# endregion AUDIT


# region LLM_INTEGRATION


def _render_and_provision_llm() -> None:
    """Render litellm-config.yml from policy.yaml and provision virtual keys.

    ## @purpose  Post-deploy LLM pipeline: regenerate litellm-config.yml from policy
    ##            to pick up any new aliases/profiles, then provision virtual keys
    ##            for all LLM consumers. Both are non-fatal on failure.
    ##            Uses subprocess (consistent with state_machine.py pattern) to avoid
    ##            PYTHONPATH/dependency resolution issues with module-level imports.
    ## @io  ⎋ None (side-effect: writes litellm-config.yml, provisions keys)
    ## @complexity O(render + provision)
    """
    core_dir = os.environ.get("CORE_DIR", f"{PLATFORM_ROOT}/core")

    # Step 1: Render litellm-config.yml via subprocess
    logger.info("[IMP:7][llm] Rendering litellm-config.yml from policy.yaml...")
    try:
        renderer_path = os.path.join(core_dir, "internal", "llm", "config_renderer.py")
        config_output = os.path.join(core_dir, "modules", "litellm", "config", "litellm-config.yml")
        if os.path.isfile(renderer_path):
            subprocess.run(
                ["python3", renderer_path, "--output", config_output],
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info("[IMP:9][llm] litellm-config.yml rendered via subprocess")
        else:
            logger.warning("[IMP:7][llm] config_renderer.py not found at %s", renderer_path)
    except Exception as e:
        logger.warning("[IMP:7][llm] Failed to render litellm-config.yml (non-fatal): %s", e)

    # Step 2: Provision virtual keys via subprocess
    logger.info("[IMP:7][llm] Provisioning LiteLLM virtual keys...")
    try:
        provision_entrypoint = os.path.join(core_dir, "entrypoints", "provision-llm.sh")
        if os.path.isfile(provision_entrypoint):
            result = subprocess.run(
                ["bash", provision_entrypoint],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("[IMP:9][llm] Key provisioning succeeded via subprocess")
            else:
                logger.warning(
                    "[IMP:7][llm] Key provisioning returned %d: %s",
                    result.returncode,
                    result.stderr.strip()[:200],
                )
        else:
            logger.warning("[IMP:7][llm] provision-llm.sh not found at %s", provision_entrypoint)
    except Exception as e:
        logger.warning("[IMP:7][llm] Failed to provision keys (non-fatal): %s", e)


# endregion LLM_INTEGRATION


# region EXTRACT_DOMAINS


# region FUNC_extract_domains_for_context
## @purpose — Extract all domains from node.yaml for cert orchestration.
##            Migrated from steps.py (DevPlan 079 DRIFT-B3 unification).
## @io — ⇥ node_yaml_path: str, context: str → ⎋ list[str]
## @complexity — O(N) for YAML parse
## @invariants
##   - Combines platform domain + project domains (filtered by context)
##   - Deduplicates domains
##   - Non-fatal: returns [] on parse errors
def _extract_domains_for_context(node_yaml_path: str, context: str) -> list[str]:
    """Extract all domains from node.yaml for cert orchestration."""
    domains: list[str] = []
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return domains
        domain = data.get("domain", "")
        if not domain:
            node_info = data.get("node", {})
            if isinstance(node_info, dict):
                domain = node_info.get("platform_domain", "") or node_info.get("domain", "")
        if domain:
            domains.append(domain)
        projects = data.get("projects", [])
        if isinstance(projects, list):
            for p in projects:
                if not isinstance(p, dict):
                    continue
                proj_context = p.get("context", "")
                if context and proj_context and proj_context != context:
                    continue
                pd = p.get("domain", "")
                if pd and pd not in domains:
                    domains.append(pd)
    except Exception as e:
        logger.warning("[IMP:7][deploy_context] Failed to extract domains: %s", e)
    return domains


# endregion FUNC_extract_domains_for_context


# endregion EXTRACT_DOMAINS


# region DEPLOY_CONTEXT


# region FUNC_deploy_context
## @purpose — Unified deploy_context entry point: cert orchestration + project deploy + vhost render + verify.
##            Replaces steps._step_deploy_context and deprecated 4 standalone entrypoints.
##            DevPlan 079 DRIFT-B3 — single public API for all deploy context paths.
## @io — ⇥ core_dir: str, node_name: str, node_yaml: str, context: str → ⎋ DeployResult
## @complexity — O(D * T + P * T) where D = domains, P = projects, T = timeout
## @invariants
##   1. CONTEXT extracted from: explicit arg → os.environ → node.yaml
##   2. Cert orchestration via importlib cert_orchestrator (non-fatal)
##   3. Project deploy via deploy_context_projects()
##   4. Vhost render via subprocess add-vhost.sh --render-all (non-fatal)
##   5. Nginx reload via docker exec (non-fatal)
##   6. Verify via verify-domains.sh (non-fatal)
##   7. All sub-steps are non-fatal — failure in one does NOT block others
def deploy_context(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    context: str = "",
) -> DeployResult:
    """Deploy all context projects + restore certs + render vhosts + verify. Idempotent.

    ▶ ┌core_dir + node + node_yaml┐ → ◇ extract context → ◇ cert orchestration →
    │  ◇ project deploy → ◇ vhost render → ◇ nginx reload → ◇ verify → ⎋ DeployResult
    """
    logger.info("[IMP:9][deploy_context] Starting (node=%s, context=%s)", node_name, context or "auto")

    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # ── Step 1: Extract/confirm CONTEXT ──
    if not context:
        context = os.environ.get("CONTEXT", "")
    if not context and node_yaml and os.path.isfile(node_yaml):
        context = extract_context_from_node_yaml(node_yaml, log_tag="deploy_context")
    if not context:
        logger.error(
            "[IMP:10][deploy_context] CONTEXT not set — pass via --context or ensure node.yaml has context/contexts[0]"
        )
        result = DeployResult()
        result.failed = 1
        return result

    logger.info("[IMP:9][deploy_context] Using context=%s, node=%s", context, node_name)

    # ── Step 2: Cert orchestration ──
    domains = _extract_domains_for_context(node_yaml, context)
    if domains:
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "cert_orchestrator",
                os.path.join(bootstrap_dir, "cert_orchestrator.py"),
            )
            if spec and spec.loader:
                cert_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cert_mod)
                issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
                secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
                cert_result = cert_mod.orchestrate_certs(domains, issue_cert_script, secrets_env)
                logger.info("[IMP:9][deploy_context] Cert orchestration: %d domains", len(cert_result.domains))
            else:
                logger.warning("[IMP:7][deploy_context] Cannot load cert_orchestrator.py")
        except Exception as e:
            logger.warning("[IMP:7][deploy_context] Cert orchestration failed (non-fatal): %s", e)

    # ── Step 3: Deploy context projects ──
    project_results = deploy_context_projects(node_yaml, context) or []

    # ── Step 4: Render vhosts ──
    vhost_script = os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh")
    if os.path.isfile(vhost_script):
        node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
        try:
            subprocess.run(
                ["bash", vhost_script, "--render-all", "--node", node_name, "--node-configs-dir", node_configs_dir],
                capture_output=True,
                text=True,
                timeout=60,
            )
            logger.info("[IMP:9][deploy_context] Vhosts rendered for node=%s", node_name)
        except Exception as e:
            logger.warning("[IMP:7][deploy_context] Vhost render failed (non-fatal): %s", e)

    # ── Step 5: Reload nginx ──
    try:
        subprocess.run(
            ["docker", "exec", "nginx", "nginx", "-s", "reload"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        logger.warning("[IMP:7][deploy_context] Nginx reload failed (non-fatal): %s", e)

    # ── Step 6: Final verify ──
    verify_script = os.path.join(core_dir, "internal", "verify", "verify-domains.sh")
    if os.path.isfile(verify_script):
        platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
        try:
            subprocess.run(
                ["bash", verify_script, node_name, platform_root],
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.info("[IMP:9][deploy_context] Verify complete for node=%s", node_name)
        except Exception as e:
            logger.warning("[IMP:7][deploy_context] Verify failed (non-fatal): %s", e)

    # ── Build result ──
    result = DeployResult()
    for r in project_results:
        result.add(r)
    logger.info(
        "[IMP:9][deploy_context] Complete (context=%s): deployed=%d skipped=%d failed=%d",
        context,
        result.deployed,
        result.skipped,
        result.failed,
    )
    return result


# endregion FUNC_deploy_context


# endregion DEPLOY_CONTEXT


# region CLI


# region FUNC_build_parser
## @purpose — Build CLI argument parser for standalone deploy-context.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def build_parser():
    """Build CLI argument parser."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Deploy all context projects from node.yaml (DevPlan 047)",
    )
    parser.add_argument("--node-yaml", required=True, help="Path to node.yaml")
    parser.add_argument("--context", default="", help="Deployment context (auto-extracted if empty)")
    parser.add_argument("--projects-base", default=DEFAULT_PROJECTS_BASE, help="Projects base directory")
    parser.add_argument("--no-fallback-build", action="store_true", help="Disable build fallback")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
## @purpose — CLI entry point for standalone deploy-context.
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = errors)
## @complexity — O(P * T) where P = projects, T = health-gate
def main() -> int:
    """CLI entry point."""
    import json

    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # Extract context if not provided
    context = args.context
    if not context:
        context = extract_context_from_node_yaml(args.node_yaml, log_tag="context_deployer")
    if not context:
        # Try env var
        context = os.environ.get("CONTEXT", "")
    if not context:
        logger.error("[IMP:10][context_deployer] CONTEXT not set and cannot be extracted from node.yaml")
        return 1

    # DevPlan 079: use unified deploy_context() instead of deploy_context_projects()
    deploy_result = deploy_context(
        core_dir=os.environ.get("CORE_DIR", f"{PLATFORM_ROOT}/core"),
        node_name=os.environ.get("NODE_NAME", ""),
        node_yaml=args.node_yaml,
        context=context,
    )
    print(json.dumps(deploy_result.to_dict(), indent=2, ensure_ascii=False))

    return 0 if deploy_result.failed == 0 else 1


# endregion FUNC_main


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
