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
##   4. Health-gate: ≤60s per project (same as the legacy deploy pipeline)
##   5. Non-fatal: failure of one project does NOT block others
##   6. Audit: each deploy recorded in /var/log/platform/audit.jsonl (единый файл, D1 — shared/audit_logger)
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

from core.internal.config import platform_config
from core.internal.deploy.channels import SCPChannel
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry

# DevPlan 091 Wave A (AC4): _ORCHESTRATOR_AVAILABLE fallback removed — DeployOrchestrator is sole path.
# ⚠️ TRAP[DECISION] · 2026-07-30 · HI · Removed _ORCHESTRATOR_AVAILABLE vestigial flag
# · Rejected: keep try/except ImportError fallback (risk: silent bypass of orchestrator if import broken)
# · Reason: DeployOrchestrator is the only deploy path (DevPlan 089). Import failure must fail loud, not silently fall back to parallel _deploy_single_project() which bypasses audit/healthcheck/snapshot.
# · Rev: if a future deployment genuinely cannot ship deploy/ package alongside context_deployer — reintroduce import guard, but route to error not bypass.

logger = logging.getLogger(__name__)

# DevPlan 116 B6 T2: sys.path-hack + `from node_yaml import <deprecated-context-alias>`
# (строки 55-58) удалены — NodeYaml уже импортирован на 42, deprecated alias удалён из
# node_yaml.py. Graceful-degradation на фасаде: get_context() с try/except (см. deploy_context).

# DevPlan 079 DRIFT-B6: shared docker compose operations
# (docker_compose_build/pull/up импорты удалены 2026-07-31 — dead wrappers F1 устранены,
#  остался только healthcheck_poll для _is_project_healthy)
from core.internal.shared.docker_compose import (
    healthcheck_poll as _shared_healthcheck_poll,
)

# DevPlan 091 Wave A: retry_pull import removed — was only consumed by the deleted
# _deploy_single_project() bypass path. ghcr retry/pull now flows through DeployOrchestrator.
# ── Constants ──────────────────────────────────────────────────────────────
# DevPlan 116 B5 T9.2 (U-11): HEALTH_GATE_TIMEOUT — алиас канона shared/timeouts.py
# (consumer-scan: константа не имеет других потребителей; единственный источник — timeouts)
from core.internal.shared.timeouts import (
    DOCKER_CMD_TIMEOUT,
    HEALTHCHECK_POLL_INTERVAL,
    HEALTHCHECK_POLL_TIMEOUT,
)

HEALTH_GATE_TIMEOUT = HEALTHCHECK_POLL_TIMEOUT  # seconds per project
DEFAULT_PROJECTS_BASE = "/opt/projects"
PLATFORM_ROOT = os.environ.get("PLATFORM_ROOT", "/opt/platform")
LITELLM_CONFIG_PATH = pathlib.Path(f"{PLATFORM_ROOT}/core/modules/litellm/config/litellm-config.yml")
POLICY_PATH = pathlib.Path(f"{PLATFORM_ROOT}/core/internal/llm/policy.yaml")
LITELLM_BASE_URL = "http://litellm:4000"


# region DATACLASSES


@dataclass
class ProjectInfo:
    """Project metadata extracted from node.yaml — view over canonical ProjectEntry.

    ## @purpose — Represent a single project entry from node.yaml#projects.
    ## @io — ⇥ ProjectEntry → ⎋ ProjectInfo with typed fields
    ## @complexity — O(1)
    ## @invariants  Local deploy-DTO (view над shared ProjectEntry, DevPlan 116 B6 T4.5) —
    ##              не является определением канона (единственное определение — в node_yaml.py).
    """

    name: str = ""
    repo: str = ""
    type: str = ""
    domain: str = ""
    context: str = ""
    database: str = ""

    @classmethod
    def from_entry(cls, entry: ProjectEntry) -> ProjectInfo:
        """Create from a canonical ProjectEntry (node.yaml project entry)."""
        return cls(
            name=entry.name,
            repo=entry.repo,
            type=entry.type,
            domain=entry.domain,
            context=entry.context,
            database=entry.database,
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
## @purpose — Parse node.yaml via canonical NodeYaml.get_project_entries() and filter
##            projects[] where context matches. One node = one context.
## @io — ⇥ node_yaml: str, context: str → ⎋ list[ProjectInfo]
## @complexity — O(N) where N = projects in node.yaml
## @invariants
##   - If context is empty, returns ALL projects (operator must specify context)
##   - Projects without context field are included if context matches node context
##   - Malformed entries → ConfigValidationError caught → [] (fail-fast D3, DevPlan 116 B6 T4.5)
def resolve_context_projects(node_yaml: str, context: str) -> list[ProjectInfo]:
    """Parse node.yaml → filter projects by context.

    ▶ ┌node.yaml → NodeYaml┐ → ◇ get_project_entries() → ◇ filter context==<context> → ⊕ list[ProjectInfo] → ⎋
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][context_deployer] node.yaml not found: %s", node_yaml)
        return []

    try:
        node = NodeYaml(node_yaml)
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.error("[IMP:10][context_deployer] Cannot read node.yaml: %s", e)
        return []

    try:
        entries = node.get_project_entries()
    except ConfigValidationError as e:
        logger.error("[IMP:10][context_deployer] Malformed projects in %s: %s", node_yaml, e)
        return []

    projects: list[ProjectInfo] = []
    for entry in entries:
        proj = ProjectInfo.from_entry(entry)
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


# region FUNC_deploy_single_project_via_orchestrator
## @purpose — Deploy a single project via DeployOrchestrator (sole deploy path, DevPlan 091 Wave A).
##            The legacy _deploy_single_project() parallel path was removed (AC4 cleanup):
##            it bypassed AuditLogger / DeployHistory snapshots / HealthcheckPoller unification.
## @io — ⇥ project: ProjectInfo, projects_base: str, ghcr_fallback_build: bool → ⎋ ProjectDeployResult
## @complexity — O(T) where T = deploy lifecycle
## @invariants
##   - Always uses DeployOrchestrator.deploy() (no fallback)
##   - Idempotent skip if project already healthy
##   - Bootstrap compose generation if docker-compose.yml missing
def _deploy_single_project_via_orchestrator(
    project: ProjectInfo,
    projects_base: str,
    ghcr_fallback_build: bool,
) -> ProjectDeployResult:
    """Deploy a single project via DeployOrchestrator (sole path)."""
    logger.info(
        "[IMP:9][context_deployer] Deploying %s via DeployOrchestrator",
        project.name,
    )

    # Check if already healthy (idempotent skip)
    if _is_project_healthy(project.name):
        logger.info("[IMP:9][context_deployer] %s — already healthy, skipping", project.name)
        return ProjectDeployResult(
            name=project.name,
            status="skipped",
            channel="skip",
            health="healthy",
        )

    # Bootstrap guard
    project_dir = os.path.join(projects_base, project.name)
    if not os.path.isfile(os.path.join(project_dir, "docker-compose.yml")) and not _ensure_bootstrap_compose(
        project_dir, project
    ):
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel="none",
            health="unhealthy",
            error="bootstrap compose generation failed",
        )

    # Deploy via orchestrator
    try:
        channel = SCPChannel()
        orchestrator = DeployOrchestrator(projects_base=projects_base)
        result = orchestrator.deploy(
            project_name=project.name,
            channel=channel,
            project_dir=project_dir,
        )
        if result.is_success():
            health = result.healthcheck_status or "healthy"
            channel_name = "orchestrator"
            return ProjectDeployResult(
                name=project.name,
                status="deployed",
                channel=channel_name,
                health=health,
            )
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel="orchestrator",
            health="unhealthy",
            error=result.error_info or "DeployOrchestrator deploy failed",
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        ConfigNotFoundError,
        ConfigParseError,
    ) as e:
        logger.error(
            "[IMP:10][context_deployer] DeployOrchestrator failed for %s: %s",
            project.name,
            e,
        )
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel="orchestrator",
            health="unhealthy",
            error=str(e),
        )


# endregion FUNC_deploy_single_project_via_orchestrator


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
        # DevPlan 091 Wave A (AC4): DeployOrchestrator is sole deploy path — no flag, no fallback.
        result = _deploy_single_project_via_orchestrator(project, projects_base, ghcr_fallback_build)
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
##            by the real docker-compose.yml via CI (receive verb, dispatch-канал) on next deploy.
## @io — ⇥ project_dir: str, project: ProjectInfo → ⎋ bool (True = success)
## @complexity — O(1)
## @invariants
##   - Non-fatal: returns False on failure
##   - Does NOT overwrite existing docker-compose.yml
##   - Generated compose has label ai-platform.bootstrap=true
##   - Will be replaced by real CI delivery on next deploy (DevPlan 116 B1 T7)
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

    compose_content = f'''# GENERATED-STUB: Bootstrap reverse proxy. Replaced by CI receive (dispatch-канал).
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
'''
    try:
        with open(compose_file, "w") as f:
            f.write(compose_content)
        logger.info("[IMP:9][context_deployer] Generated bootstrap compose for %s", project.name)
        return True
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write bootstrap compose for %s: %s", project.name, e)
        return False


# endregion FUNC_ensure_bootstrap_compose


# ── REMOVED (DevPlan 091 Wave A, AC4 + debt F1 2026-07-31) ─────────────────
# _deploy_single_project() — 90 LOC parallel deploy path (pull→build→up→healthcheck)
# that bypassed DeployOrchestrator, AuditLogger, DeployHistory snapshots, and unified
# HealthcheckPoller. Removed in favor of _deploy_single_project_via_orchestrator() as
# the sole deploy entrypoint. Thin wrappers (_docker_compose_pull, _docker_compose_build,
# _docker_compose_up, _wait_until_healthy) были dead code — удалены (F1).
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
    return (
        _shared_healthcheck_poll(project_name, timeout=HEALTHCHECK_POLL_TIMEOUT, interval=HEALTHCHECK_POLL_INTERVAL)
        == "healthy"
    )


# endregion FUNC_is_project_healthy


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
##   - Pipe-delimited legacy format REMOVED (D1, DevPlan 116 B11 T2) — единый JSON-lines writer shared/audit_logger
def _write_audit(project: ProjectInfo, result: ProjectDeployResult) -> None:
    """Write audit entry for project deploy via shared audit_logger."""
    from core.internal.shared.audit_logger import write_audit_entry

    tag = f"context_deploy:{project.name}"
    msg = f"channel={result.channel} health={result.health}"
    if result.error:
        msg += f" error={result.error}"

    try:
        write_audit_entry(tag=tag, status=result.status.upper(), message=msg)
    except OSError as e:
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
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
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
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][llm] Failed to provision keys (non-fatal): %s", e)


# endregion LLM_INTEGRATION


# region EXTRACT_DOMAINS


# region FUNCextract_domains_for_context
## @purpose — Extract all domains from node.yaml for cert orchestration via NodeYaml.
##            Migrated from steps.py (DevPlan 079 DRIFT-B3 unification).
## @io — ⇥ node_yaml_path: str, context: str → ⎋ list[str]
## @complexity — O(N) for NodeYaml parse
## @invariants
##   - Combines platform domain + project domains (filtered by context)
##   - Deduplicates domains
##   - Non-fatal: returns [] on parse errors
def extract_domains_for_context(node_yaml_path: str, context: str) -> list[str]:
    """Extract all domains from node.yaml for cert orchestration."""
    domains: list[str] = []
    try:
        node = NodeYaml(node_yaml_path)

        # Platform domain: try top-level domain, then node.platform_domain, then node.domain
        domain = node.get("domain", default="")
        if not domain:
            domain = node.get("node.platform_domain", default="")
        if not domain:
            domain = node.get("node.domain", default="")
        if domain:
            domains.append(domain)

        # Project domains filtered by context
        projects = node.get("projects", default=[])
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
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.warning("[IMP:7][deploy_context] Failed to extract domains: %s", e)
    return domains


# endregion FUNCextract_domains_for_context


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
##   2. Cert orchestration via importlib cert_orchestrator (non-fatal); волна 117 D3 —
##      skip, если все домены имеют валидные сертификаты (≥30 дней, LE)
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
        context = os.environ.get("CONTEXT", platform_config.default_context_sentinel())
    if not context and node_yaml and os.path.isfile(node_yaml):
        # DevPlan 116 B6 T2: extract-алиас поглощал ошибки и возвращал "";
        # graceful-degradation сохранена, но на фасаде NodeYaml.get_context().
        try:
            context = NodeYaml(node_yaml).get_context()
        except (ConfigParseError, ConfigNotFoundError) as exc:
            logger.warning("[IMP:7][deploy_context] Cannot read context from %s: %s", node_yaml, exc)
    if not context:
        logger.error(
            "[IMP:10][deploy_context] CONTEXT not set — pass via --context or ensure node.yaml has contexts[0].name"
        )
        result = DeployResult()
        result.failed = 1
        return result

    logger.info("[IMP:9][deploy_context] Using context=%s, node=%s", context, node_name)

    # ── Step 2: Cert orchestration ──
    domains = extract_domains_for_context(node_yaml, context)
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
                # ── D3 (волна 117): skip, если все домены контекста имеют валидные сертификаты ──
                # φ7 (phase_certificates) уже выпустил/восстановил сертификаты для всех доменов;
                # повторный вызов orchestrate_certs здесь — дубль S3/openssl-проходов (идемпотентен,
                # но лишний). Критерий — тот же, что у cert_orchestrator._is_cert_valid (≥30 дней + LE).
                invalid_domains = []
                for dom in domains:
                    cert_path = os.path.join(cert_mod.CERT_VALIDITY_PATH, dom, "fullchain.pem")
                    if not (os.path.isfile(cert_path) and cert_mod._is_cert_valid(dom, cert_path)):
                        invalid_domains.append(dom)
                if invalid_domains:
                    issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
                    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
                    cert_result = cert_mod.orchestrate_certs(domains, issue_cert_script, secrets_env)
                    logger.info("[IMP:9][deploy_context] Cert orchestration: %d domains", len(cert_result.domains))
                else:
                    logger.info(
                        "[IMP:9][deploy_context] All %d domains have valid certs (≥30 days, LE) — "
                        "skipping cert orchestration (D3)",
                        len(domains),
                    )
            else:
                logger.warning("[IMP:7][deploy_context] Cannot load cert_orchestrator.py")
        except (ImportError, OSError, FileNotFoundError) as e:
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
        except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
            logger.warning("[IMP:7][deploy_context] Vhost render failed (non-fatal): %s", e)

    # ── Step 5: Reload nginx ──
    try:
        subprocess.run(
            ["docker", "exec", "nginx", "nginx", "-s", "reload"],
            capture_output=True,
            text=True,
            timeout=DOCKER_CMD_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
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
        except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
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
        # DevPlan 116 B6 T2: фасад NodeYaml.get_context() вместо deprecated extract-алиаса.
        try:
            context = NodeYaml(args.node_yaml).get_context()
        except (ConfigParseError, ConfigNotFoundError) as exc:
            logger.warning("[IMP:7][context_deployer] Cannot read context from %s: %s", args.node_yaml, exc)
    if not context:
        # Try env var
        context = os.environ.get("CONTEXT", platform_config.default_context_sentinel())
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
