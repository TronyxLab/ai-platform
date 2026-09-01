#!/usr/bin/env python3
# GREP_SUMMARY: context-deployer, project-deploy, ghcr-pull, build-fallback, healthcheck-gate, idempotent, node-yaml-projects, audit-log, DI, runner, facts, vhost-count-guard, exposed-projects
# STRUCTURE: ▶ ┌node.yaml + context┐ → ◇ filter projects[context] → ○ for each: healthcheck? → ghcr pull → (fail?) build → up -d → ⊕ ProjectDeployResult │ ▶ deploy_context → _step_certs → _step_deploy_projects → _step_vhosts → _step_nginx_reload → _step_verify (D6) → ⎋ ContextDeployResult
# region MODULE_CONTRACT
## @purpose  Deploy all projects of a context from node.yaml after bootstrap.
##           Uses ghcr.io pull as primary image channel, falls back to on-node build.
##           Implements health-gate (≤60s per project) and idempotent skip for healthy projects.
##           DevPlan 118 D6: deploy_context god-function разбита на typed-шаги
##           (_step_certs/_step_deploy_projects/_step_vhosts/_step_nginx_reload/_step_verify);
##           nginx reload делегирует в shared/docker_compose.nginx_reload (единственный docker CLI путь).
## @scope    Called from state_machine.py deploy_context step (18.4) and standalone
##           via `make deploy-context NODE=<n>` → core/entrypoints/deploy-context.sh.
## @invariants
##   1. Source of projects: node.yaml → projects[] where context == <context>
##   2. Image channel: ghcr.io pull primary → build on-node fallback
##   3. Idempotent: healthcheck before deploy, skip if healthy
##   4. Health-gate: ≤60s per project
##   5. Non-fatal: failure of one project does NOT block others
##   6. Audit: each deploy recorded in /var/log/platform/audit.jsonl (единый файл, D1 — shared/audit_logger)
##   7. One node = one context (CONTEXT from node.yaml or CLI --context)
##   8. Sub-steps non-fatal (D6): шаг не блокирует последующие; НО _step_vhosts НЕуспех после
##      retry агрегируется в ContextDeployResult.failed (exit deploy-context ≠ 0) — «лог success
##      без файлов на диске» исключён (холодный bootstrap R6-инцидент, 2026-08-31)
##   9. E1 (160): DI-параметры runner/facts/certs_fn/cert_validity_fn/deploy_projects_fn/
##      health_fn/audit_fn/deploy_impl/nginx_reload_fn/stub_detector_fn/healthcheck_poll_fn
##      (None = реальные вызовы; поведение/exit-коды/идемпотентность НЕ изменены)
## @rationale StatusReport 045: 14/20 containers down after bootstrap because deploy-modules
##           does not cover context projects. context_deployer bridges the "last mile":
##           it deploys all projects matching the node's context, with ghcr.io primary
##           and build fallback for resilience.
##           DevPlan 118 D6: god-function 606-735 (6 подсистем) → 5 typed-шагов —
##           по одному методу на инфраструктуру (SRP, AI-First).
## @changes  2026-07-22 | DevPlan 047 Phase 4 — Created context deployer
##           2026-08-02 | DevPlan 118 D6 — deploy_context → шаги с typed-контрактами;
##                      nginx reload → shared/docker_compose.nginx_reload
##           2026-08-13 | DevPlan 160 E1 — +DI-параметры (runner/facts/функции-зависимости)
##           2026-08-14 | DevPlan 170 W1-A3 — LITELLM_BASE_URL порт из shared/platform_ports
##           2026-09-01 | cache-drill fix — main(): --node аргумент + резолв node.name из
##                      node.yaml (цепочка --node → NODE_NAME → node.yaml); пусто → IMP:10
##                      fail-fast (standalone deploy-context терял node → vhost-рендер в
##                      некорректный путь)
##           2026-09-01 | silent-0 success-путь: _step_vhosts — stdout tail скрипта ВСЕГДА
##                      (IMP:8, transient «0 rendered» виден в bootstrap-логе) + guard
##                      rendered_count (stdout-паттерн «N vhost(s) generated») < expected
##                      (exposed-проекты node.yaml) → НЕ успех; overlay-файлы НЕ «rendered»
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol, cast

# ⚠️ TRAP[BUG] · 2026-08-05 · HI · Standalone-инвокация context_deployer.py без PYTHONPATH → ModuleNotFoundError
# · Symptom: `env -i python3 context_deployer.py --help` из чистого env падал на `from core.internal...`
# ·   (deploy-context.sh вызывает python3 без экспорта PYTHONPATH — core.* импорты держались
# ·   только на случайном глобальном PYTHONPATH ноды; латентный класс A, DevPlan 136 W2 T2.10).
# · Root: sys.path-hack удалён (DevPlan 116 B6 T2 — deprecated-алиас node_yaml), но каноничный
# ·   self-bootstrap корня репо добавлен не был.
# · Fix: self-bootstrap корня репо (канон config_renderer.py:44-45) ДО core.* импортов.
# ·   Файл: core/internal/bootstrap/deploy/context_deployer.py → корень = 5 уровней parent.
# · Prevention: core.*-модули не полагаются на внешний PYTHONPATH — self-bootstrap в источнике.
# · DevPlan 136 W2 T2.10: тест env -i python3 context_deployer.py --help → exit 0.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# DevPlan 118 A5: нормальный импорт cert_orchestrator — importlib-обход удалён (тихий полом
# системы импорта при рефакторинге cert-кода исключён). Модуль-уровневый импорт даёт обычный
# ImportError при отсутствии cert_orchestrator.py (loud failure), а не silent-деградацию.
from core.internal.bootstrap.cert_orchestrator import (
    CERT_VALIDITY_PATH,
    CertResult,
    orchestrate_certs,
)
from core.internal.config import platform_config
from core.internal.deploy.channels import LocalChannel
from core.internal.deploy.orchestrator import DeployOrchestrator, OrchestratorDeployResult
from core.internal.shared import deploy_paths, llm_paths
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry

# DevPlan 170 W1-A3: порт из единого реестра shared/platform_ports (литерал 4000 удалён)
from core.internal.shared.platform_ports import PLATFORM_PORT_LITELLM
from core.internal.shared.ssl_certs import DEFAULT_EXPIRY_THRESHOLD, cert_is_valid  # C9: единая комбинация
from core.internal.shared.subprocess_io import CommandRunner
from core.internal.shared.timeouts import SYSTEM_CMD_TIMEOUT

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
    HEALTHCHECK_POLL_INTERVAL,
    HEALTHCHECK_POLL_TIMEOUT,
)

HEALTH_GATE_TIMEOUT = HEALTHCHECK_POLL_TIMEOUT  # seconds per project
# B2/B3: канонические дефолты путей — shared/deploy_paths (литералы /opt/* удалены)
DEFAULT_PROJECTS_BASE = deploy_paths.DEFAULT_PROJECTS_BASE
PLATFORM_ROOT = str(deploy_paths.platform_remote_base())
# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
LITELLM_CONFIG_PATH = llm_paths.litellm_config_path(f"{PLATFORM_ROOT}/core")
POLICY_PATH = pathlib.Path(f"{PLATFORM_ROOT}/core/internal/llm/policy.yaml")
# DevPlan 170 W1-A3: порт из единого реестра shared/platform_ports (литерал 4000 удалён)
LITELLM_BASE_URL = f"http://litellm:{PLATFORM_PORT_LITELLM}"


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

    def to_dict(self) -> dict[str, str]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        if self.error is None:
            d.pop("error", None)
        return cast(dict[str, str], d)


@dataclass
class ContextDeployResult:
    """Aggregated result of deploying all context projects.

    ## @purpose — Collect per-project results and summary counts.
    ## @io — ⇥ results → ⎋ serializable summary
    ## @complexity — O(N) where N = projects
    ## @invariants — awaiting_deploy (DevPlan 153 T6): stub-проекты, ожидающие receive-доставку
    """

    results: list[ProjectDeployResult] = field(default_factory=list)
    deployed: int = 0
    skipped: int = 0
    failed: int = 0
    awaiting_deploy: int = 0

    def add(self, result: ProjectDeployResult) -> None:
        """Add a per-project result and increment counter."""
        self.results.append(result)
        if result.status == "deployed":
            self.deployed += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "failed":
            self.failed += 1
        elif result.status == "awaiting_deploy":
            self.awaiting_deploy += 1

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-compatible dict."""
        return {
            "results": [r.to_dict() for r in self.results],
            "summary": {
                "deployed": self.deployed,
                "skipped": self.skipped,
                "failed": self.failed,
                "awaiting_deploy": self.awaiting_deploy,
            },
        }


# endregion DATACLASSES


# region PROJECT_RESOLUTION


# region FUNC_resolve_context_projects
## @purpose — Parse node.yaml via canonical NodeYaml.get_project_entries() and filter
##            projects[] where context matches. One node = one context.
## @io — ⇥ node_yaml: str, context: str, facts: EnvironmentFacts | None → ⎋ list[ProjectInfo]
## @complexity — O(N) where N = projects in node.yaml
## @invariants
##   - If context is empty, returns ALL projects (operator must specify context)
##   - Projects without context field are included if context matches node context
##   - Malformed entries → ConfigValidationError caught → [] (fail-fast D3, DevPlan 116 B6 T4.5)
## @changes 2026-08-13 | E1 (160): +facts DI (os.path.isfile → facts.path_isfile)
def resolve_context_projects(
    node_yaml: str, context: str, *, facts: EnvironmentFacts | None = None
) -> list[ProjectInfo]:
    """Parse node.yaml → filter projects by context.

    ▶ ┌node.yaml → NodeYaml┐ → ◇ get_project_entries() → ◇ filter context==<context> → ⊕ list[ProjectInfo] → ⎋
    """
    if not node_yaml or not (facts or default_env_facts()).path_isfile(node_yaml):
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
##            Параллельный deploy-путь отсутствует:
##            it bypassed AuditLogger / DeployHistory snapshots / HealthcheckPoller unification.
## @io — ⇥ project: ProjectInfo, projects_base: str → ⎋ ProjectDeployResult
## @complexity — O(T) where T = deploy lifecycle
## @invariants
##   - Always uses DeployOrchestrator.deploy() (no fallback)
##   - Idempotent skip if project already healthy
##   - Bootstrap compose generation if docker-compose.yml missing
## @changes 2026-08-13 | E1 (160): +health_fn/orchestrator_deploy_fn DI (тесты без monkeypatch)
# region FUNC__plw_body__deploy_single_project_via_orchestrator
## @purpose  Тело try-блока (PLW0717 extraction из _deploy_single_project_via_orchestrator) — семантика except не меняется.
## @io       ⇥ orchestrator_deploy_fn, orchestrator_cls (DI, DevPlan 167 D3), project,
##           project_dir, projects_base → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__deploy_single_project_via_orchestrator(
    orchestrator_deploy_fn: Callable[..., OrchestratorDeployResult] | None,
    orchestrator_cls: type[DeployOrchestrator] | None,
    project: ProjectInfo,
    project_dir: str,
    projects_base: str,
) -> ProjectDeployResult:
    if orchestrator_deploy_fn is not None:
        result = orchestrator_deploy_fn(project_dir=project_dir, project_name=project.name)
    else:
        channel = LocalChannel()
        # 167 D3: orchestrator_cls DI (тест инжектит fake-класс, записывающий channel) —
        # None → DeployOrchestrator (прод-поведение без изменений)
        orch_cls = orchestrator_cls if orchestrator_cls is not None else DeployOrchestrator
        orchestrator = orch_cls(projects_base=projects_base)
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


# endregion FUNC__plw_body__deploy_single_project_via_orchestrator


def _deploy_single_project_via_orchestrator(
    project: ProjectInfo,
    projects_base: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    health_fn: Callable[[str], bool] | None = None,
    orchestrator_deploy_fn: Callable[..., OrchestratorDeployResult] | None = None,
    orchestrator_cls: type[DeployOrchestrator] | None = None,
) -> ProjectDeployResult:
    """Deploy a single project via DeployOrchestrator (sole path).

    DevPlan 167 D3: +orchestrator_cls — DI-шов класса DeployOrchestrator (тест инжектит
    fake-класс для проверки канала LocalChannel; 0 monkeypatch.setattr cd.DeployOrchestrator).
    """
    logger.info(
        "[IMP:9][context_deployer] Deploying %s via DeployOrchestrator",
        project.name,
    )
    facts_obj = facts or default_env_facts()

    # REF-0103: cold-skip gate — single-shot probe (attempts=1). Отсутствующий/стартующий
    # проект не должен сжигать полное окно поллинга в idempotent-проверке; полный
    # deadline-driven поллинг остаётся в DeployOrchestrator после деплоя.
    def _gate_single_shot(name: str) -> bool:
        return _is_project_healthy(name, single_shot=True)

    is_healthy: Callable[[str], bool] = health_fn if health_fn is not None else _gate_single_shot

    # Check if already healthy (idempotent skip)
    if is_healthy(project.name):
        logger.info("[IMP:9][context_deployer] %s — already healthy, skipping", project.name)
        return ProjectDeployResult(
            name=project.name,
            status="skipped",
            channel="skip",
            health="healthy",
        )

    # Bootstrap guard
    project_dir = os.path.join(projects_base, project.name)
    if not facts_obj.path_isfile(os.path.join(project_dir, "docker-compose.yml")) and not _ensure_bootstrap_compose(
        project_dir, project, runner=runner, facts=facts_obj
    ):
        return ProjectDeployResult(
            name=project.name,
            status="failed",
            channel="none",
            health="unhealthy",
            error="bootstrap compose generation failed",
        )

    # ── Stub guard (DevPlan 153 T6, N1): GENERATED-STUB compose/контейнер не означает ──
    # ── реальный проект — реальный payload доставляется receive-каналом (orchestrator_cli
    # ── receive / make deploy-project / CI). Возвращаем awaiting_deploy вместо deployed/skipped,
    # ── чтобы недоставленный проект был виден, а не маскировался как «здоровый». ──
    if _is_bootstrap_stub_compose(project_dir):
        logger.warning(
            "[IMP:9][context_deployer] %s — GENERATED-STUB compose (awaiting real payload delivery, receive-канал)",
            project.name,
        )
        return ProjectDeployResult(
            name=project.name,
            status="awaiting_deploy",
            channel="stub",
            health="awaiting_deploy",
        )

    # Deploy via orchestrator
    try:
        # ⚠️ TRAP[BUG] · 2026-08-02 · P1 · SCPChannel() → LocalChannel() — deploy-context всегда FAILED
        # · Symptom: deploy-context возвращал status="failed" для ВСЕХ проектов контекста;
        # ·   `DeployOrchestrator.deploy()` всегда получал failed от канала доставки.
        # · Root: channel = SCPChannel() без metadata — SCPChannel.deliver() требует
        # ·   payload.metadata["host"] (channels.py:225-230) → delivery всегда FAILED
        # ·   ("SCPChannel requires 'host' in payload.metadata"). Payload уже извлечён на VPS
        # ·   после context_overlay — транспортный канал бессмыслен на receive-стороне.
        # · Fix: LocalChannel() — contract-compliant no-op delivery (TRAP[DECISION] channels.py:327);
        # ·   полный пайплайн DeployOrchestrator (compose-up → healthcheck → snapshot → audit) сохраняется.
        # · Prevention: на VPS-стороне (receive/deploy-context) НИКОГДА не создавать SCPChannel —
        # ·   только LocalChannel; SCPChannel требует явный host в metadata.
        # · Rev: если появится реальный «deliver локально»-сценарий — расширять LocalChannel,
        # ·   не возвращать транспорт (channels.py:337).
        return _plw_body__deploy_single_project_via_orchestrator(
            orchestrator_deploy_fn, orchestrator_cls, project, project_dir, projects_base
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
##       runner: CommandRunner | None, facts: EnvironmentFacts | None,
##       health_fn/audit_fn/orchestrator_deploy_fn (DI) → ⎋ list[ProjectDeployResult]
## @complexity — O(P * T) where P = projects, T = health-gate timeout
## @invariants
##   - Each project is processed independently (non-fatal on failure)
##   - Healthcheck before deploy (skip if already healthy)
##   - Deploy через ghcr-образ (fallback-build удалён DevPlan 091/17 T4.3)
##   - Audit log entry per deploy
## @changes 2026-08-13 | E1 (160): +DI threading (runner/facts/health_fn/audit_fn/orchestrator_deploy_fn)
def deploy_context_projects(
    node_yaml: str,
    context: str,
    projects_base: str = DEFAULT_PROJECTS_BASE,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    health_fn: Callable[[str], bool] | None = None,
    audit_fn: Callable[[ProjectInfo, ProjectDeployResult], None] | None = None,
    orchestrator_deploy_fn: Callable[..., OrchestratorDeployResult] | None = None,
    llm_fn: Callable[[], None] | None = None,
) -> list[ProjectDeployResult]:
    """Deploy all context projects from node.yaml.

    ▶ ┌node.yaml + context┐ → ◇ filter projects → ○ for each: healthcheck? → ghcr/build → up → ⊕ results → ⎋

    DevPlan 167 D3: +llm_fn — DI-шов пост-деплой LLM-пайплайна (тест передаёт no-op вместо
    monkeypatch.setattr(cd, "_render_and_provision_llm"); None → module-level фасад).
    """
    projects = resolve_context_projects(node_yaml, context, facts=facts)
    if not projects:
        logger.info("[IMP:7][context_deployer] No projects to deploy for context '%s'", context)
        return []

    write_audit: Callable[[ProjectInfo, ProjectDeployResult], None] = audit_fn if audit_fn is not None else _write_audit

    results: list[ProjectDeployResult] = []
    for project in projects:
        # DevPlan 091 Wave A (AC4): DeployOrchestrator is sole deploy path — no flag, no fallback.
        result = _deploy_single_project_via_orchestrator(
            project,
            projects_base,
            runner=runner,
            facts=facts,
            health_fn=health_fn,
            orchestrator_deploy_fn=orchestrator_deploy_fn,
        )
        results.append(result)
        write_audit(project, result)

    # ── Post-deploy: render litellm config + provision LLM virtual keys ──
    # 167 D3: llm_fn DI (тест передаёт no-op; прод — _render_and_provision_llm)
    (llm_fn if llm_fn is not None else _render_and_provision_llm)()

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
## @io — ⇥ project_dir: str, project: ProjectInfo, runner: CommandRunner | None,
##       facts: EnvironmentFacts | None → ⎋ bool (True = success)
## @complexity — O(1)
## @invariants
##   - Non-fatal: returns False on failure
##   - Does NOT overwrite existing docker-compose.yml
##   - Generated compose has label ai-platform.bootstrap=true
##   - Will be replaced by real CI delivery on next deploy (DevPlan 116 B1 T7)
## @changes 2026-08-13 | E1 (160): +runner/facts DI (chown subprocess + os.path.isfile)
def _ensure_bootstrap_compose(
    project_dir: str,
    project: ProjectInfo,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """Generate minimal docker-compose.yml for first bootstrap (no CI delivery yet).

    ▶ ┌project_dir + project┐ → ◇ compose file exists? → ⎋ True
    │                                      ↓ Nonexistent
    │                      ┌content: nginx:alpine + ai-platform.bootstrap label┐
    │                      → ✎ docker-compose.yml → ⎋ bool
    """
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    if (facts or default_env_facts()).path_isfile(compose_file):
        return True  # Already exists (real delivery or previous bootstrap)

    if not os.path.isdir(project_dir):
        os.makedirs(project_dir, exist_ok=True)
        # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B19 (141 r2): проектный каталог бутстрапа root:root
        # · Symptom: receive-деплой под ci-deploy не мог писать .deploy-snapshots/payload →
        # ·   «Permission denied» (auditing FAILED). Канон владельца проектов — ci-deploy
        # ·   (ensure_projects_base users.py); бутстрап (root) обязан выставлять его сразу.
        # · Fix: chown ci-deploy:ci-deploy при создании каталога (non-fatal: dev-окружения без
        # ·   ci-deploy-юзера не блокируются).
        # · Rev: если владелец проектов сменится — синхронизировать здесь и в users.py.
        if runner is None:
            _ = subprocess.run(
                ["chown", "ci-deploy:ci-deploy", project_dir],
                capture_output=True,
                text=True,
                timeout=SYSTEM_CMD_TIMEOUT,
                check=False,
            )
        else:
            _ = runner.run(["chown", "ci-deploy:ci-deploy", project_dir], timeout=SYSTEM_CMD_TIMEOUT, check=False)

    domain = getattr(project, "domain", None) or project.name

    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — stub compose несовместим с DeployOrchestrator
    # · Symptom: холодный бутстрап — пул проекта падал «no such service» (3 попытки) →
    # ·   first-deploy FATAL (exit 10) → deploy_services FAILED. Ручной pull работал.
    # · Root: сервис stuba был ‹project.name›-proxy, а DeployOrchestrator пулит с
    # ·   service=project_name (orchestrator.py:334); вдобавок stub: host-порт (порт проекта)
    # ·   (конфликт), healthcheck curl (в nginx:alpine нет curl), нет proxy-net.
    # · Fix: stub повторяет конвенцию реальных compose (сервис = project.name,
    # ·   сети name-net + proxy-net external, wget healthcheck, без host-портов).
    # · Rev: если конвенция реальных compose изменится — синхронизировать stub.
    compose_content = f"""# GENERATED-STUB: Bootstrap reverse proxy. Replaced by CI receive (dispatch-канал).
version: '3.8'
services:
  {project.name}:
    image: nginx:alpine
    container_name: {project.name}
    labels:
      - "ai-platform.bootstrap=true"
      - "ai-platform.project={project.name}"
      - "platform.type=frontend"
      - "platform.domain={domain}"
    networks:
      - {project.name}-net
      - proxy-net
    healthcheck:
      test: ['CMD', 'wget', '-qO-', 'http://127.0.0.1/']
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
networks:
  {project.name}-net:
    name: {project.name}-net
    driver: bridge
  proxy-net:
    name: proxy-net
    external: true
"""
    try:
        with pathlib.Path(compose_file).open("w", encoding="utf-8") as f:
            _ = f.write(compose_content)
        logger.info("[IMP:9][context_deployer] Generated bootstrap compose for %s", project.name)
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write bootstrap compose for %s: %s", project.name, e)
        return False
    else:
        return True


# endregion FUNC_ensure_bootstrap_compose


# region FUNC_is_bootstrap_stub_compose
## @purpose — Проверка, что docker-compose.yml проекта — GENERATED-STUB (создан _ensure_bootstrap_compose,
##            а не реальная CI-доставка). DevPlan 153 T6 (N1): stub compose не является реальным
##            проектом — статус деплоя должен быть awaiting_deploy, а не deployed/skipped.
## @io — ⇥ project_dir: str → ⎋ bool (True = stub compose)
## @complexity — O(1) — чтение первой строки
## @invariants
##   - Маркер "GENERATED-STUB" ищется в первой строке (шапка stubs, канон stub_detection)
##   - Missing/empty/OSError → False (никогда не raise)
def _is_bootstrap_stub_compose(project_dir: str) -> bool:
    """Check whether project docker-compose.yml is a GENERATED-STUB (bootstrap placeholder)."""
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    try:
        with pathlib.Path(compose_file).open(encoding="utf-8") as f:
            first_line = f.readline()
    except (OSError, UnicodeDecodeError):
        return False
    else:
        return "GENERATED-STUB" in first_line


# endregion FUNC_is_bootstrap_stub_compose


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
##            DevPlan 153 T6 (N1): stub-контейнер (label ai-platform.bootstrap=true) НЕ считается
##            healthy — иначе GENERATED-STUB маскирует недоставленный проект в skip-логике.
## @io — ⇥ project_name: str, stub_detector_fn: Callable | None, healthcheck_poll_fn: Callable | None
##      → ⎋ bool (True if healthy AND not stub)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses shared healthcheck_poll for health status
##   - Stub guard: is_stub_container() → False (stub никогда не «здоров» для skip)
##   - Non-fatal: if docker unavailable, returns False
##   - REF-0103 single_shot=True: ОДНА проверка (attempts=1) — idempotent-skip gate на
##     отсутствующем проекте не сжигает полное окно поллинга (до фикса 60s на cold-проект)
## @changes 2026-08-13 | E1 (160): +stub_detector_fn/healthcheck_poll_fn DI (тесты без monkeypatch)
## @changes 2026-08-25 | REF-0103: +single_shot kwarg (cold-skip gate — одна проверка, не окно)
def _is_project_healthy(
    project_name: str,
    *,
    stub_detector_fn: Callable[[str], bool] | None = None,
    healthcheck_poll_fn: Callable[..., str] | None = None,
    single_shot: bool = False,
) -> bool:
    """Check if project containers are healthy via shared healthcheck_poll.

    Returns False for stub containers (ai-platform.bootstrap=true label) — DevPlan 153 T6.
    """
    from core.internal.shared.stub_detection import (
        is_stub_container,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: docker_inspect_fn=None нетипизирован
    )

    stub_detector = cast(
        Callable[[str], bool],
        stub_detector_fn if stub_detector_fn is not None else is_stub_container,
    )
    healthcheck_poll = cast(
        Callable[..., str],
        healthcheck_poll_fn if healthcheck_poll_fn is not None else _shared_healthcheck_poll,
    )

    # Stub guard (N1): контейнер-заглушка проходит healthcheck (wget → 200 welcome),
    # но не является реальным проектом — не должен скипаться в deploy-context.
    if stub_detector(project_name):
        logger.info(
            "[IMP:9][context_deployer] %s — stub container detected, treating as not-healthy",
            project_name,
        )
        return False
    # REF-0103 single-shot: одна проверка (attempts=1) — skip-gate не должен поллить полное
    # окно на отсутствующем контейнере; полный deadline-driven режим — только default-путь.
    if single_shot:
        return (
            healthcheck_poll(
                project_name,
                timeout=HEALTHCHECK_POLL_TIMEOUT,
                interval=HEALTHCHECK_POLL_INTERVAL,
                attempts=1,
            )
            == "healthy"
        )
    return (
        healthcheck_poll(project_name, timeout=HEALTHCHECK_POLL_TIMEOUT, interval=HEALTHCHECK_POLL_INTERVAL)
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
##   - Единый JSON-lines writer — shared/audit_logger (pipe-формата нет)
def _write_audit(project: ProjectInfo, result: ProjectDeployResult) -> None:
    """Write audit entry for project deploy via shared audit_logger."""
    from core.internal.shared.audit_logger import write_audit_entry

    tag = f"context_deploy:{project.name}"
    msg = f"channel={result.channel} health={result.health}"
    if result.error:
        msg += f" error={result.error}"

    try:
        _ = write_audit_entry(tag=tag, status=result.status.upper(), message=msg)
    except OSError as e:
        logger.warning("[IMP:7][context_deployer] Failed to write audit log via shared: %s", e)


# endregion FUNC_write_audit


# endregion AUDIT


# region LLM_INTEGRATION


def _render_and_provision_llm() -> None:
    """Lazy facade for llm_provision.render_and_provision_llm (DevPlan 117 G T58.5).

    ## @purpose  Post-deploy LLM pipeline: regenerate litellm-config.yml from policy
    ##            to pick up any new aliases/profiles, then provision virtual keys
    ##            for all LLM consumers. Both are non-fatal on failure.
    ## @io  ⎋ None (side-effect: writes litellm-config.yml, provisions keys)
    ## @complexity O(render + provision)
    """
    from core.internal.bootstrap.deploy.llm_provision import render_and_provision_llm as _impl

    _impl()


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
    # ruff: ignore[PLW0717] — внутри try есть break/continue/await/yield — извлечение ломает управляющий поток
    try:
        node = NodeYaml(node_yaml_path)

        # Platform domain: try top-level domain, then node.platform_domain, then node.domain
        # W11-G1 cross-file: NodeYaml.get → Any — аннотация str фиксирует доменную границу
        domain: str = node.get("domain", default="")
        if not domain:
            domain = node.get("node.platform_domain", default="")
        if not domain:
            domain = node.get("node.domain", default="")
        if domain:
            domains.append(domain)

        # Project domains filtered by context
        # W11-G1 cross-file: NodeYaml.get("projects") → Any — каст к object, isinstance-гейт сохраняется
        projects = cast(object, node.get("projects", default=[]))
        if isinstance(projects, list):
            for p in projects:  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: элемент projects[] — node_yaml.get → Any
                if not isinstance(p, dict):
                    continue
                # W11-G1 cross-file: элемент projects[] → Any — каст к словарю строковых полей
                proj = cast(dict[str, str], p)
                proj_context = proj.get("context", "")
                if context and proj_context and proj_context != context:
                    continue
                pd = proj.get("domain", "")
                if pd and pd not in domains:
                    domains.append(pd)
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.warning("[IMP:7][deploy_context] Failed to extract domains: %s", e)
    return domains


# endregion FUNCextract_domains_for_context


# endregion EXTRACT_DOMAINS


# region DEPLOY_CONTEXT


# region FUNC__resolve_context
## @purpose — E7 (DevPlan 119): резолв CONTEXT по цепочке explicit arg → os.environ → node.yaml.
##            Ветвления deploy_context вынесены в изолированный хелпер (dispatch-упрощение).
## @io — ⇥ context: str (уже переданный/пустой), node_yaml: str,
##       facts: EnvironmentFacts | None → ⎋ str (resolved, может быть "")
## @complexity — O(1) — env check + NodeYaml.get_context
## @invariants
##   - Explicit arg приоритетен (не пустой → возвращается как есть)
##   - os.environ CONTEXT → node.yaml contexts[0].name (fallback-цепочка)
##   - NodeYaml ошибки graceful-degradation (WARN, не raise)
## @changes 2026-08-13 | E1 (160): +facts DI (os.path.isfile → facts.path_isfile)
def _resolve_context(context: str, node_yaml: str, *, facts: EnvironmentFacts | None = None) -> str:
    """Resolve CONTEXT: explicit arg → os.environ → node.yaml (E7 helper)."""
    if context:
        return context
    context = os.environ.get("CONTEXT", platform_config.default_context_sentinel())
    if not context and node_yaml and (facts or default_env_facts()).path_isfile(node_yaml):
        # DevPlan 116 B6 T2: extract-алиас поглощал ошибки и возвращал "";
        # graceful-degradation сохранена, но на фасаде NodeYaml.get_context().
        try:
            context = NodeYaml(node_yaml).get_context()
        except (ConfigParseError, ConfigNotFoundError) as exc:
            logger.warning("[IMP:7][_resolve_context] Cannot read context from %s: %s", node_yaml, exc)
    return context


# endregion FUNC__resolve_context


# region FUNC_deploy_context
## @purpose — Unified deploy_context entry point: cert orchestration + project deploy + vhost render + verify.
##            Replaces steps._step_deploy_context and deprecated 4 standalone entrypoints.
##            DevPlan 079 DRIFT-B3 — single public API for all deploy context paths.
##            DevPlan 118 D6 — god-function (606-735) разбита на typed-шаги:
##            _step_certs / _step_deploy_projects / _step_vhosts / _step_nginx_reload / _step_verify.
##            deploy_context — тонкий оркестратор шагов (contract: по одному методу на инфраструктуру).
## @io — ⇥ core_dir: str, node_name: str, node_yaml: str, context: str,
##       runner: CommandRunner | None, facts: EnvironmentFacts | None,
##       certs_fn/cert_validity_fn/deploy_projects_fn/nginx_reload_fn (DI) → ⎋ ContextDeployResult
## @complexity — O(D * T + P * T) where D = domains, P = projects, T = timeout
## @invariants
##   1. CONTEXT extracted from: explicit arg → os.environ → node.yaml
##   2. Cert orchestration via НОРМАЛЬНЫЙ импорт cert_orchestrator (DevPlan 118 A5 — importlib-обход
##      удалён; ImportError при отсутствии cert_orchestrator.py — loud, не silent); волна 117 D3 —
##      skip, если все домены имеют валидные сертификаты (≥30 дней, LE через shared/ssl_certs)
##   3. Project deploy via deploy_context_projects()
##   4. Vhost render via subprocess add-vhost.sh --render-all; rc-capture + ОДИН retry +
##      файл-верификация (≥1 *.conf в overlays/nginx) — НЕуспех после retry → result.failed
##   5. Nginx reload via docker exec (non-fatal) — shared/docker_compose.nginx_reload (D6)
##   6. Verify via verify-domains.sh (non-fatal)
##   7. Sub-steps non-fatal (D6) — шаг не блокирует последующие; vhost-исключение: _step_vhosts
##      НЕуспех агрегируется в result.failed (деплой-фаза не отчитывается успехом при пустом overlay)
##   8. Каждый шаг — отдельный метод с typed-контрактом (D6); оркестратор не содержит бизнес-логики
## @changes 2026-08-13 | E1 (160): +runner/facts/certs_fn/cert_validity_fn/deploy_projects_fn/nginx_reload_fn DI
def deploy_context(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    context: str = "",
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    certs_fn: Callable[..., CertResult] | None = None,
    cert_validity_fn: Callable[..., bool] | None = None,
    deploy_projects_fn: Callable[..., list[ProjectDeployResult]] | None = None,
    nginx_reload_fn: Callable[[], None] | None = None,
) -> ContextDeployResult:
    """Deploy all context projects + restore certs + render vhosts + verify. Idempotent.

    ▶ ┌core_dir + node + node_yaml┐ → ◇ extract context → ◇ _step_certs →
    │  ◇ _step_deploy_projects → ◇ _step_vhosts → ◇ _step_nginx_reload → ◇ _step_verify → ⎋ ContextDeployResult
    """
    logger.info("[IMP:9][deploy_context] Starting (node=%s, context=%s)", node_name, context or "auto")

    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # ── Step 1: Extract/confirm CONTEXT (E7: ветвления вынесены в _resolve_context) ──
    context = _resolve_context(context, node_yaml, facts=facts)
    if not context:
        logger.error(
            "[IMP:10][deploy_context] CONTEXT not set — pass via --context or ensure node.yaml has contexts[0].name"
        )
        result = ContextDeployResult()
        result.failed = 1
        return result

    logger.info("[IMP:9][deploy_context] Using context=%s, node=%s", context, node_name)

    # ── Step 2: Cert orchestration (typed-шаг D6) ──
    _step_certs(
        bootstrap_dir,
        node_yaml,
        context,
        runner=runner,
        facts=facts,
        certs_fn=certs_fn,
        cert_validity_fn=cert_validity_fn,
    )

    # ── Step 3: Deploy context projects (typed-шаг D6) ──
    project_results = _step_deploy_projects(node_yaml, context, deploy_projects_fn=deploy_projects_fn)

    # ── Step 4: Render vhosts (typed-шаг D6) ──
    # Холодный bootstrap R6: rc-capture + retry + файл-верификация внутри шага; False →
    # агрегация в result.failed (деплой-фаза не отчитывается успехом при пустом overlay).
    # silent-0 (2026-09-01): node_yaml пробрасывается в шаг для expected-счётчика (exposed).
    vhosts_ok = _step_vhosts(core_dir, node_name, runner=runner, facts=facts, node_yaml=node_yaml)

    # ── Step 5: Reload nginx (typed-шаг D6, shared/docker_compose.nginx_reload) ──
    _step_nginx_reload(nginx_reload_fn=nginx_reload_fn)

    # ── Step 6: Final verify (typed-шаг D6) ──
    _step_verify(core_dir, node_name, runner=runner, facts=facts)

    # ── Build result ──
    result = ContextDeployResult()
    for r in project_results:
        result.add(r)
    if not vhosts_ok:
        # Паттерн неуспеха = _step_deploy_projects: failed-запись через канонический add()
        # → result.failed → exit deploy-context ≠ 0 (фаза не отчитывается успехом).
        result.add(
            ProjectDeployResult(
                name="<vhost-render>",
                status="failed",
                channel="render",
                health="unknown",
                error="add-vhost.sh --render-all failed or produced no vhost configs (see IMP:10 log)",
            )
        )
        logger.error("[IMP:10][deploy_context] Vhost render failed — deploy-context reports failure (exit≠0)")
    logger.info(
        "[IMP:9][deploy_context] Complete (context=%s): deployed=%d skipped=%d failed=%d",
        context,
        result.deployed,
        result.skipped,
        result.failed,
    )
    return result


# region FUNC__step_certs
## @purpose — Typed-шаг D6: cert orchestration для всех доменов контекста (non-fatal).
##            Извлечён из deploy_context god-function (строки 662-693). Отвечает ТОЛЬКО за сертификаты.
## @io — ⇥ bootstrap_dir: str, node_yaml: str, context: str, runner: CommandRunner | None,
##       facts: EnvironmentFacts | None, certs_fn/cert_validity_fn (DI) → ⎋ None
## @complexity — O(D * T) где D = доменов
## @invariants
##   - Skip, если все домены имеют валидные сертификаты (≥30 дней, LE через shared/ssl_certs)
##   - Невалидные домены → orchestrate_certs (нормальный импорт cert_orchestrator, A5)
##   - Non-fatal: исключения ловятся и логируются
## @changes 2026-08-13 | E1 (160): +runner/facts/certs_fn/cert_validity_fn DI
def _step_certs(
    bootstrap_dir: str,
    node_yaml: str,
    context: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    certs_fn: Callable[..., CertResult] | None = None,
    cert_validity_fn: Callable[..., bool] | None = None,
) -> None:
    """Cert orchestration step (D6) — issue/restore certificates for context domains."""
    domains = extract_domains_for_context(node_yaml, context)
    if not domains:
        logger.info("[IMP:7][_step_certs] No domains for context '%s' — skipping", context)
        return
    facts_obj = facts or default_env_facts()
    is_valid: Callable[..., bool] = cert_validity_fn if cert_validity_fn is not None else cert_is_valid
    do_certs: Callable[..., CertResult] = certs_fn if certs_fn is not None else orchestrate_certs
    # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
    try:
        # ⚠️ TRAP[BUG] · 2026-08-02 · P1 · importlib-обход cert_orchestrator → нормальный импорт (A5)
        # · Symptom: importlib.util.spec_from_file_location("cert_orchestrator", ...) + приватный
        # ·   cert_mod._is_cert_valid — тихий полом при рефакторинге cert-кода: система импорта
        # ·   обходилась, приватный API использовался кросс-модульно, ошибки импорта глотались.
        # · Root: обход системы импорта (spec_from_file_location) + приватный _is_cert_valid
        # ·   (дубль логики, уже консолидированной в shared/ssl_certs — DevPlan 117 D21).
        # · Fix: модуль-уровневый импорт cert_orchestrator (ImportError — loud, не silent);
        # ·   _is_cert_valid заменён на shared/ssl_certs.cert_is_valid (C9, DevPlan 118) —
        # ·   единая комбинация parseable+LE+expiry (та же семантика: ≥30 дней + LE issuer).
        # · Prevention: приватные API не вызываются кросс-модульно; cert-валидация — через ssl_certs.
        invalid_domains: list[str] = []
        for dom in domains:
            cert_path = os.path.join(CERT_VALIDITY_PATH, dom, "fullchain.pem")
            if not (facts_obj.path_isfile(cert_path) and is_valid(cert_path, DEFAULT_EXPIRY_THRESHOLD)):
                invalid_domains.append(dom)
        if invalid_domains:
            # W3.5-1 (164): issue_cert.py — Python-модуль (прежний issue-cert.sh удалён; диспетч
            # bash/python3 -m в cert_orchestrator._issue_cert по суффиксу)
            issue_cert_script = os.path.join(bootstrap_dir, "issue_cert.py")
            secrets_env = os.environ.get("SECRETS_ENV_FILE", str(deploy_paths.secrets_env_file()))
            cert_result = do_certs(domains, issue_cert_script, secrets_env, runner=runner, facts=facts_obj)
            logger.info("[IMP:9][_step_certs] Cert orchestration: %d domains", len(cert_result.domains))
            logger.info(
                "[IMP:9][_step_certs] All %d domains have valid certs (≥30 days, LE) — skipping cert orchestration (D3)",
                len(domains),
            )
    except (OSError, subprocess.CalledProcessError) as e:
        logger.warning("[IMP:7][_step_certs] Cert orchestration failed (non-fatal): %s", e)


# endregion FUNC__step_certs


# region FUNC__step_deploy_projects
## @purpose — Typed-шаг D6: деплой всех проектов контекста (non-fatal per-project).
## @io — ⇥ node_yaml: str, context: str, deploy_projects_fn (DI; None = deploy_context_projects)
##      → ⎋ list[ProjectDeployResult]
## @complexity — O(P * T) где P = проектов
## @invariants
##   - Делегирует в deploy_context_projects (idempotent skip healthy, ghcr primary, build fallback)
##   - Post-deploy: _render_and_provision_llm (lazy facade → llm_provision, DevPlan 117 G T58.5)
## @changes 2026-08-13 | E1 (160): +deploy_projects_fn DI (тесты без monkeypatch deploy_context_projects)
def _step_deploy_projects(
    node_yaml: str,
    context: str,
    *,
    deploy_projects_fn: Callable[..., list[ProjectDeployResult]] | None = None,
) -> list[ProjectDeployResult]:
    """Project deploy step (D6) — deploy all context projects + post-deploy LLM provisioning."""
    do_deploy: Callable[..., list[ProjectDeployResult]] = (
        deploy_projects_fn if deploy_projects_fn is not None else deploy_context_projects
    )
    project_results = do_deploy(node_yaml, context) or []
    logger.info("[IMP:9][_step_deploy_projects] Project deploy complete: %d results", len(project_results))
    return project_results


# endregion FUNC__step_deploy_projects


# region FUNC__tail_output
## @purpose — Диагностический tail вывода subprocess (последние N непустых строк) для IMP:10.
## @io — ⇥ output: str | bytes, limit: int = 5 → ⎋ str (joined tail)
## @complexity — O(L) где L = строк вывода
def _tail_output(output: str | bytes, limit: int = 5) -> str:
    """Last `limit` non-empty lines of subprocess output, joined (diagnostic tail)."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return " | ".join(lines[-limit:])


# endregion FUNC__tail_output


# Паттерн stdout vhost_renderer.py (add-vhost.sh --render-all):
#   print(f"  ✅ render-vhosts: {moved_count} vhost(s) generated")
_RENDERED_COUNT_RE = re.compile(r"(\d+)\s+vhost\(s\)\s+generated")


# region FUNC__parse_rendered_count
## @purpose — Извлечь rendered-счётчик из stdout add-vhost.sh (паттерн «N vhost(s) generated»).
##            ЕДИНСТВЕННЫЙ источник факта «rendered» — overlay-файлы НЕ считаются
##            (silent-0 инцидент 2026-09-01: rc=0 + 0 rendered, guard «≥1 *.conf» обходился
##            посторонним статическим nginx.conf в overlay).
## @io — ⇥ stdout: str | None → ⎋ int | None (None = паттерн не найден / stdout пуст)
## @complexity — O(L) где L = длина stdout
## @invariants — Совпадает с форматом vhost_renderer.py render_all (цифра + «vhost(s) generated»)
def _parse_rendered_count(stdout: str | None) -> int | None:
    """Extract rendered vhost count from add-vhost.sh stdout ('N vhost(s) generated')."""
    if not stdout:
        return None
    match = _RENDERED_COUNT_RE.search(stdout)
    if match is None:
        return None
    return int(match.group(1))


# endregion FUNC__parse_rendered_count


# region FUNC__count_exposed_projects
## @purpose — Ожидаемое число vhost = число exposed-проектов в node.yaml узла (expose:true).
##            Читает node.yaml напрямую через канонический NodeYaml — НЕ дублирует логику
##            vhost_renderer (там expose-фильтр по ai-platform.yaml проекта; здесь — грубая
##            верхняя граница «exposed-проекты узла» из node.yaml, 3-5 строк).
## @io — ⇥ node_yaml_path: str → ⎋ int | None (None = node.yaml недоступен/не парсится)
## @complexity — O(P) где P = проектов в node.yaml
## @invariants — node.yaml недоступен/malformed → None (guard откатывается на файл-верификацию)
def _count_exposed_projects(node_yaml_path: str) -> int | None:
    """Expected vhost count = node.yaml#projects with expose:true (None if node.yaml unreadable)."""
    try:
        node = NodeYaml(node_yaml_path)
        projects = node.get_projects()
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError, OSError) as e:
        logger.warning(
            "[IMP:7][_step_vhosts] Cannot read node.yaml for expected vhost count (%s): %s",
            node_yaml_path,
            e,
        )
        return None
    exposed = sum(1 for p in projects if isinstance(p, dict) and p.get("expose"))
    logger.info("[IMP:8][_step_vhosts] Expected vhost count (exposed projects in node.yaml): %d", exposed)
    return exposed


# endregion FUNC__count_exposed_projects


# region FUNC__step_vhosts
## @purpose — Typed-шаг D6: рендер vhost-конфигов nginx. НЕуспех после retry пропагируется
##            в ContextDeployResult.failed (exit deploy-context ≠ 0) — «лог success без файлов
##            на диске» исключён (холодный bootstrap R6-инцидент: render-all ТИХО падал,
##            exposed-проекты оставались без vhost, converge R6 FAIL ×3).
##            silent-0 (2026-09-01): rc=0 + «0 vhost(s) generated» при 3 exposed больше не
##            маскируется — success-путь логирует stdout tail (IMP:8) и сверяет
##            stdout-счётчик с expected (exposed в node.yaml); overlay-файлы НЕ «rendered».
## @io — ⇥ core_dir: str, node_name: str, runner: CommandRunner | None,
##       facts: EnvironmentFacts | None, node_yaml: str | None (None = derive
##       {node_configs_dir}/⟨node⟩/node.yaml — renderer-механика) → ⎋ bool
##       (True = отрендерено ожидаемое число vhost; False = неуспех)
## @complexity — O(V + P) где V = vhost'ов, P = проектов node.yaml
## @invariants
##   - Вызывает add-vhost.sh --render-all --node (subprocess, 60s timeout), rc/stdout/stderr захватываются
##   - rc!=0 → ОДИН retry; второй rc!=0 → IMP:10 + return False
##   - rc==0 → stdout tail ВСЕГДА логируется (IMP:8); guard: rendered_count (stdout-паттерн
##     «N vhost(s) generated») < expected (exposed-проекты node.yaml) → НЕ успех
##     (retry → IMP:10 + False); паттерн не найден → fallback на старый guard (≥1 *.conf на диске)
##   - Существующие .conf в overlay НЕ считаются «rendered» — только вывод скрипта
##   - Отсутствие скрипта → True (нечего рендерить — skip не является неуспехом)
## @changes 2026-08-13 | E1 (160): +runner/facts DI (subprocess + os.path.isfile)
## @changes 2026-08-31 | Холодный bootstrap: rc-capture + tail + retry + файл-верификация + bool-пропагация
## @changes 2026-09-01 | silent-0 success-путь: stdout tail (IMP:8) + guard rendered < expected
def _step_vhosts(
    core_dir: str,
    node_name: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    node_yaml: str | None = None,
) -> bool:
    """Vhost render step (D6) — generate nginx vhost configs; False after retry = phase failure."""
    vhost_script = os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh")
    facts_obj = facts or default_env_facts()
    if not facts_obj.path_isfile(vhost_script):
        logger.info("[IMP:7][_step_vhosts] add-vhost.sh not found — skipping vhost render")
        return True
    node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", str(deploy_paths.node_configs_remote()))
    overlay_dir = pathlib.Path(node_configs_dir) / node_name / "overlays" / "nginx"
    # Ожидаемый счётчик vhost = exposed-проекты node.yaml узла (renderer-механика:
    # node_configs_dir/⟨node⟩/node.yaml; deploy_context пробрасывает реальный node_yaml CLI).
    node_yaml_path = node_yaml or str(pathlib.Path(node_configs_dir) / node_name / "node.yaml")
    cmd = ["bash", vhost_script, "--render-all", "--node", node_name, "--node-configs-dir", node_configs_dir]
    # rc-capture + stdout tail (ВСЕГДА на success-пути, IMP:8) + ОДИН retry (транзиентный
    # отказ холодного bootstrap); второй rc!=0 или rc==0 при rendered < expected → IMP:10 + False.
    # ⚠️ TRAP[BUG] · 2026-09-01 · P1 · Silent-0 на success-пути: rc=0 + «0 vhost(s) generated»
    # · Symptom: «Vhosts rendered (1 .conf)» при 3 exposed — 0 новых vhost (transient), guard
    # ·   «≥1 *.conf» обходился посторонним статическим nginx.conf в overlay; ловится только
    # ·   по следствию в converge R6
    # · Root: success-путь выбрасывал вывод скрипта; rendered-факт брался из overlay-файлов,
    # ·   не из stdout (тот же silent-success класс, что F-06, теперь на success-пути)
    # · Fix: stdout tail ВСЕГДА (IMP:8) + guard rendered_count < expected (exposed в node.yaml);
    # ·   паттерн не найден → fallback на файл-верификацию
    # · Prevention: rendered-факт — из вывода скрипта, не из overlay-файлов
    last_err = ""
    for attempt in (1, 2):
        try:
            if runner is None:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            else:
                result = runner.run(cmd, timeout=60, check=False)
        # REF-0103: +subprocess.SubprocessError — TimeoutExpired (timeout=60). Таймаут = рендер
        # не завершился → неуспех-путь (retry → IMP:10), а не WARN-маскировка пустого overlay.
        except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as e:
            last_err = str(e)
            logger.warning("[IMP:7][_step_vhosts] Vhost render attempt %d failed: %s", attempt, e)
            continue
        if result.returncode != 0:
            last_err = _tail_output(result.stderr or result.stdout)
            logger.warning(
                "[IMP:7][_step_vhosts] Vhost render attempt %d failed (rc=%d): %s",
                attempt,
                result.returncode,
                last_err,
            )
            continue
        # Success-путь: tail stdout/stderr ВСЕГДА — transient «0 rendered» виден в bootstrap-логе
        # (одна-две строки: «render-vhosts: N vhost(s) generated» / «Output: <dir>»).
        logger.info(
            "[IMP:8][_step_vhosts] Vhost render rc=0 (attempt %d) stdout tail: %s",
            attempt,
            _tail_output(result.stdout or result.stderr, limit=4),
        )
        rendered_count = _parse_rendered_count(result.stdout)
        expected = _count_exposed_projects(node_yaml_path)
        # Guard: rendered (из stdout-паттерна) < expected → НЕ успех (retry ×1 → IMP:10 False).
        # Overlay-файлы (в т.ч. посторонний статический nginx.conf) НЕ считаются «rendered».
        if rendered_count is not None and expected is not None and rendered_count < expected:
            last_err = (
                f"rc=0 but rendered {rendered_count}/{expected} vhost(s) "
                f"(expected {expected} exposed projects in node.yaml)"
            )
            logger.warning("[IMP:7][_step_vhosts] Vhost render attempt %d: %s", attempt, last_err)
            continue
        if rendered_count is not None:
            logger.info(
                "[IMP:9][_step_vhosts] Vhosts rendered for node=%s (%d vhost(s) generated)",
                node_name,
                rendered_count,
            )
            return True
        # Паттерн «N vhost(s) generated» не найден в stdout → fallback на старый guard: ≥1 *.conf
        rendered = list(overlay_dir.glob("*.conf"))
        if rendered:
            logger.info(
                "[IMP:9][_step_vhosts] Vhosts rendered for node=%s (%d .conf in %s)",
                node_name,
                len(rendered),
                overlay_dir,
            )
            return True
        last_err = f"rc=0 but no *.conf rendered in {overlay_dir}"
        logger.warning("[IMP:7][_step_vhosts] Vhost render attempt %d: %s", attempt, last_err)
    logger.error(
        "[IMP:10][_step_vhosts] Vhost render FAILED after retry for node=%s: %s",
        node_name,
        last_err,
    )
    return False


# endregion FUNC__step_vhosts


# region FUNC__step_nginx_reload
## @purpose — Typed-шаг D6: reload nginx после рендера vhost'ов (non-fatal).
##            Делегирует в shared/docker_compose.nginx_reload (единый фасад, DevPlan 118 D6).
## @io — ⇥ None, nginx_reload_fn (DI; None = shared docker_compose.nginx_reload) → ⎋ None
## @complexity — O(1)
## @invariants
##   - Все docker CLI вызовы — через shared/docker_compose (гейт docker_sole_path)
##   - Non-fatal: ошибка → WARN
## @changes 2026-08-13 | E1 (160): +nginx_reload_fn DI (тесты без monkeypatch shared docker_compose)
def _step_nginx_reload(*, nginx_reload_fn: Callable[[], None] | None = None) -> None:
    """Nginx reload step (D6) — reload nginx via shared docker_compose facade."""
    from core.internal.shared.docker_compose import nginx_reload

    reload_fn: Callable[[], None] = nginx_reload_fn if nginx_reload_fn is not None else nginx_reload

    try:
        reload_fn()
    # REF-0103: +subprocess.SubprocessError — nginx_reload (docker exec, timeout) документирует
    # raise TimeoutExpired, но caller его не ловил → crash вместо non-fatal WARN
    except (OSError, subprocess.CalledProcessError, subprocess.SubprocessError) as e:
        logger.warning("[IMP:7][_step_nginx_reload] Nginx reload failed (non-fatal): %s", e)


# endregion FUNC__step_nginx_reload


# region FUNC__step_verify
## @purpose — Typed-шаг D6: финальная HTTPS-верификация доменов (non-fatal).
## @io — ⇥ core_dir: str, node_name: str, runner: CommandRunner | None,
##       facts: EnvironmentFacts | None → ⎋ None (side-effect: verify log)
## @complexity — O(D) где D = доменов
## @invariants
##   - Вызывает domain_verifier.py напрямую (subprocess, 120s timeout; DevPlan 173 W1.5 —
##     двух-хоповый фасад verify-domains.sh удалён)
##   - Non-fatal: отсутствие скрипта/ошибка → WARN
## @changes 2026-08-13 | E1 (160): +runner/facts DI (subprocess + os.path.isfile)
## @changes 2026-08-16 | DevPlan 173 W1.5: verify-domains.sh → domain_verifier.py напрямую
def _step_verify(
    core_dir: str,
    node_name: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> None:
    """Final verify step (D6) — HTTPS verification for all domains."""
    verify_script = os.path.join(core_dir, "internal", "verify", "domain_verifier.py")
    if not (facts or default_env_facts()).path_isfile(verify_script):
        logger.info("[IMP:7][_step_verify] domain_verifier.py not found — skipping verify")
        return
    platform_root = str(deploy_paths.platform_remote_base())
    verify_cmd = [
        sys.executable,
        verify_script,
        "verify",
        "--node",
        node_name,
        "--platform-root",
        platform_root,
    ]
    try:
        if runner is None:
            _ = subprocess.run(
                verify_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        else:
            _ = runner.run(
                verify_cmd,
                timeout=120,
                check=False,
            )
        logger.info("[IMP:9][_step_verify] Verify complete for node=%s", node_name)
    # REF-0103: +subprocess.SubprocessError — TimeoutExpired (timeout=120) вне кортежа
    # ронял deploy-context вместо non-fatal WARN
    except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError) as e:
        logger.warning("[IMP:7][_step_verify] Verify failed (non-fatal): %s", e)


# endregion FUNC__step_verify


# endregion FUNC_deploy_context


# endregion DEPLOY_CONTEXT


# region CLI


# region FUNC_build_parser
## @purpose — Build CLI argument parser for standalone deploy-context.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Deploy all context projects from node.yaml (DevPlan 047)",
    )
    _ = parser.add_argument("--node-yaml", required=True, help="Path to node.yaml")
    _ = parser.add_argument(
        "--node",
        default="",
        help="Node name (auto-resolved from node.yaml#node.name if empty)",
    )
    _ = parser.add_argument("--context", default="", help="Deployment context (auto-extracted if empty)")
    _ = parser.add_argument("--projects-base", default=DEFAULT_PROJECTS_BASE, help="Projects base directory")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    node_yaml: str
    node: str
    context: str
    projects_base: str


## @purpose — CLI entry point for standalone deploy-context.
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = errors)
## @complexity — O(P * T) where P = projects, T = health-gate
def main() -> int:
    """CLI entry point."""
    import json

    parser = build_parser()
    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (reportInvalidCast
    # обходится); runtime-атрибутов нет → parser-defaults применяются штатно
    args = cast(_CliArgs, cast(object, parser.parse_args()))

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # Resolve node name (⚠️ TRAP[BUG] · 2026-09-01 · HI · standalone deploy-context терял node →
    # vhost-рендер в некорректный путь; поймано cache-drill прогоном · Root: main() полагался
    # только на NODE_NAME env (пуст на VPS) → node_name="" · Fix: цепочка --node → env NODE_NAME
    # → node.yaml#node.name; пусто → fail-fast IMP:10 (node обязателен для _step_vhosts/_step_certs)
    # · Prevention: vhost/cert шаги требуют node — CLI не стартует без него)
    node_name = args.node or os.environ.get("NODE_NAME", "")
    if not node_name:
        try:
            node_name = str(NodeYaml(args.node_yaml).get("node.name", default="") or "")
        except (ConfigParseError, ConfigNotFoundError, ConfigValidationError) as exc:
            logger.warning("[IMP:7][context_deployer] Cannot read node.name from %s: %s", args.node_yaml, exc)
    if not node_name:
        logger.error(
            "[IMP:10][context_deployer] NODE not set and node.name missing from %s — "
            "node is required for vhost render / cert steps",
            args.node_yaml,
        )
        return 1
    logger.info("[IMP:9][context_deployer] Using node=%s", node_name)

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
        node_name=node_name,
        node_yaml=args.node_yaml,
        context=context,
    )
    print(json.dumps(deploy_result.to_dict(), indent=2, ensure_ascii=False))

    return 0 if deploy_result.failed == 0 else 1


# endregion FUNC_main


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
