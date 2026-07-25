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
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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


# region FUNC_extract_context_from_node_yaml
## @purpose — Extract context name from node.yaml. One node = one context.
##            Reads context (string) or contexts[0].name (array, first element).
## @io — ⇥ node_yaml_path: str → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
## @invariants
##   - Primary: top-level context field (string)
##   - Fallback: contexts[0].name (array, first element)
##   - Returns empty string on parse error
def extract_context_from_node_yaml(node_yaml_path: str) -> str:
    """Extract context name from node.yaml."""
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return ""
        # Primary: context field (string)
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][context_deployer] Context from node.yaml context field: %s", ctx)
            return ctx
        # Fallback: contexts array (first element)
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
            elif isinstance(first, str):
                ctx = first
            if ctx:
                logger.info("[IMP:8][context_deployer] Context from node.yaml contexts[0].name: %s", ctx)
                return ctx
    except Exception as e:
        logger.warning("[IMP:7][context_deployer] Failed to parse %s: %s", node_yaml_path, e)
    return ""


# endregion FUNC_extract_context_from_node_yaml


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

    # Step 2: Try ghcr.io pull (primary channel)
    channel = "ghcr"
    pull_ok = _docker_compose_pull(project_dir)
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
## @purpose — Check if a project's containers are healthy via docker compose ps.
## @io — ⇥ project_name: str → ⎋ bool (True if healthy)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses docker inspect for health status
##   - Non-fatal: if docker unavailable, returns False
def _is_project_healthy(project_name: str) -> bool:
    """Check if project containers are healthy."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={project_name}", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        # If no containers running → not healthy
        if not result.stdout.strip():
            return False
        # Check all lines for "healthy" or "Up"
        lines = result.stdout.strip().splitlines()
        if not lines:
            return False
        # Consider healthy if all containers are "Up" and none are "unhealthy"
        return not any("unhealthy" in line.lower() or "restarting" in line.lower() for line in lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_is_project_healthy


# region FUNC_docker_compose_pull
## @purpose — Run docker compose pull in project directory.
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + network
## @invariants
##   - Non-fatal: returns False on failure
##   - Timeout: 120s
def _docker_compose_pull(project_dir: str) -> bool:
    """Pull images for project via docker compose pull."""
    if not os.path.isdir(project_dir):
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "pull"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_dir,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_docker_compose_pull


# region FUNC_docker_compose_build
## @purpose — Run docker compose build in project directory (fallback channel).
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + build time
## @invariants
##   - Non-fatal: returns False on failure
##   - Timeout: 300s (builds can be slow)
def _docker_compose_build(project_dir: str) -> bool:
    """Build images for project via docker compose build."""
    if not os.path.isdir(project_dir):
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "build"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=project_dir,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_docker_compose_build


# region FUNC_docker_compose_up
## @purpose — Run docker compose up -d in project directory.
## @io — ⇥ project_dir: str → ⎋ bool (True = success)
## @complexity — O(1) + startup time
## @invariants
##   - Non-fatal: returns False on failure
##   - Timeout: 120s
def _docker_compose_up(project_dir: str) -> bool:
    """Start project via docker compose up -d."""
    if not os.path.isdir(project_dir):
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_dir,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC_docker_compose_up


# region FUNC_wait_until_healthy
## @purpose — Poll project health until healthy or timeout.
## @io — ⇥ project_name: str, timeout: int → ⎋ str ("healthy" | "unhealthy")
## @complexity — O(T/I) where T = timeout, I = poll interval
## @invariants
##   - Polls every HEALTH_POLL_INTERVAL seconds
##   - Returns "unhealthy" if timeout reached
def _wait_until_healthy(project_name: str, timeout: int) -> str:
    """Wait until project is healthy or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_project_healthy(project_name):
            logger.info("[IMP:9][context_deployer] %s — healthy", project_name)
            return "healthy"
        time.sleep(HEALTH_POLL_INTERVAL)
    logger.warning("[IMP:7][context_deployer] %s — health-gate timeout (%ds)", project_name, timeout)
    return "unhealthy"


# endregion FUNC_wait_until_healthy


# endregion DOCKER_OPERATIONS


# region AUDIT


# region FUNC_write_audit
## @purpose — Write audit log entry for project deploy.
## @io — ⇥ project: ProjectInfo, result: ProjectDeployResult → ⎋ None
## @complexity — O(1)
## @invariants
##   - Non-fatal: if audit log write fails, logs WARN
##   - Appends to /var/log/platform/audit.log
def _write_audit(project: ProjectInfo, result: ProjectDeployResult) -> None:
    """Write audit entry for project deploy."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(
                f"[{ts}] context_deploy:{project.name} "
                f"status={result.status} channel={result.channel} health={result.health}\n"
            )
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write audit log: %s", e)


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
        context = extract_context_from_node_yaml(args.node_yaml)
    if not context:
        # Try env var
        context = os.environ.get("CONTEXT", "")
    if not context:
        logger.error("[IMP:10][context_deployer] CONTEXT not set and cannot be extracted from node.yaml")
        return 1

    results = deploy_context_projects(
        node_yaml=args.node_yaml,
        context=context,
        projects_base=args.projects_base,
        ghcr_fallback_build=not args.no_fallback_build,
    )

    deploy_result = DeployResult()
    for r in results:
        deploy_result.add(r)
    print(json.dumps(deploy_result.to_dict(), indent=2, ensure_ascii=False))

    return 0 if deploy_result.failed == 0 else 1


# endregion FUNC_main


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
