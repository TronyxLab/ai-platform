#!/usr/bin/env python3
"""Docker orchestration functions extracted from deploy-modules.sh: deploy, pull, healthcheck."""
# GREP_SUMMARY: docker-orchestrator, deploy-docker, compose-up, pre-pull, image-check, wait-readiness, healthcheck, hermes-agent, orphan-reconcile, parallel-runner, healthcheck-runner, hermes-workflow, D1
# STRUCTURE: ▶ _check_image_exists → deploy_docker_module [resolve_compose → build_args → hermes_special → orphan_reconcile → compose_up] → wait_for_readiness [N×invoke_interface] → run_healthcheck [N×retry] → CLI dispatch
# region MODULE_CONTRACT [DOMAIN(INFRA): bootstrap; CONCEPT(DOCKER): orchestration; TECH(PYTHON): subprocess+argparse+logging]
## @purpose  Deploy docker modules via docker compose, pre-pull images, check image existence,
##           wait for readiness, and run healthchecks — extracted from deploy-modules.sh.
## @scope    Called by deploy-modules.sh (shell façade) and directly via CLI. Covers all Docker
##           orchestration responsibilities previously in deploy-modules.sh (1664→<100 LOC after extraction).
##           DevPlan 118 D1: параллелизм (pre_pull_images, deploy_docker_group, drain) → parallel_runner.py;
##           healthcheck-инвокации → healthcheck_runner.py; hermes-agent спец-workflow → hermes_workflow.py.
##           Оркестратор остаётся: роутинг модулей (deploy_docker_module) + CLI (AC-D1 <900 LOC).
## @input    CLI: --action {deploy,pre-pull,deploy-group,wait,healthcheck,check-image} with module paths
## @output   stdout: LDD logs, healthcheck output; exit code 0/1
## @invariants
##   - All docker CLI calls go through subprocess.run — no direct socket/API calls
##   - Compose file resolution order: compose.yaml → docker-compose.yaml → docker-compose.base.yml
##   - Pre-pull failure is non-fatal (compose up -d retries pull internally)
##   - Healthcheck failure is non-fatal (logged, does not abort further deploy)
##   - Hermes-agent L1→L2 build fallback on image 404 (not FAIL — automatic rebuild)
##   - Orphan container reconciliation runs PER-MODULE before compose up -d
##   - --profile is always passed with module_name for standalone compose file deploy
##   - Fork-параллелизм и healthcheck — ТОЛЬКО через parallel_runner / healthcheck_runner (D1)
## @rationale Q: Why Python, not bash? A: deploy_docker_module has 5+ responsibilities (compose
##   resolution, hermes special case, orphan reconcile, env-file building, compose up) — bash
##   with nested conditionals made this ~120 LOC of hard-to-test shell. Python with isolated
##   helper functions is testable via mock subprocess and tmp_path.
##   Q: Why subprocess.run for docker? A: docker compose CLI is the supported interface —
##   direct Docker SDK calls would diverge from compose file semantics (profile resolution,
##   env-file handling, compose interpolation).
##   DevPlan 118 D1: монолит 1397 LOC → оркестратор <900 (параллелизм/HC/hermes вынесены).
## @changes   2026-07-22 · W4-E1 — extracted from deploy-modules.sh deploy_docker_module,
##   deploy_docker_group, pre_pull_images, _check_image_exists, wait_for_readiness, run_healthcheck
##   2026-07-23 · P0 fix — docker compose build before up -d for modules with build: section
##   (status-page served stale container after core-deploy rsync)
##   2026-07-24 · W2.T2.1 — added --action deploy-group CLI dispatch + handler in main()
##   2026-07-24 · W2.T2.4 — integrated build_cache (check_build_needed / save_build_hash)
##             into deploy_docker_module() build section for modules with build: section
##   2026-07-24 · W5.T5.1 — enhanced HC fork cycle in deploy_docker_group() with per-module
##             pass/fail tracking, IMP:9 logs per module, and summary log with failure names
##   2026-08-02 · DevPlan 118 D1 — pre_pull_images/deploy_docker_group/_drain_* → parallel_runner.py;
##             wait_for_readiness/run_healthcheck/_invoke_healthcheck* → healthcheck_runner.py;
##             _handle_hermes_agent → hermes_workflow.py (оркестратор: роутинг + CLI)
##
## ⚠️ TRAP[DEBT] · 2026-07-22 · P2 · 5 test-side failures in test_docker_orchestrator.py (DevPlan 043-B5)
## · Root: mock subprocess.run returns bytes, code expects str via text=True
## · Impact: 5 unit-тестов падали (test_cleanup_legacy_container_found/not_found,
##   test_deploy_docker_module_hermes_agent, testpre_pull_images_single,
##   orphan-reconcile тест). Production-код корректен:
##   docker stop/rm присутствуют в _cleanup_legacy_container; os._exit() в pre_pull_images
##   корректен для forked child. P2 TypeGuard на bytes работает в production.
## · Fix: адаптировать моки в test_docker_orchestrator.py (DevPlan 042 Phase 4)
## · Non-blocking: production-код корректен, тесты требуют адаптации моков
## · Note: orphan-реконсиляция делегирована в orphan_reconciler (DevPlan 117 D18)
## @modulemap
##   _check_image_exists [W:1] — docker manifest inspect via subprocess → bool
##   _resolve_compose_file [W:1] — find compose.yaml → docker-compose.yaml → docker-compose.base.yml in module dir
##   _build_compose_args [W:2] — build docker compose arg list from env-files, overlay, --profile
##   deploy_docker_module [W:5] — deploy single docker module: build (if build:) + compose up -d
##   _cleanup_legacy_container [W:1] — hermes-agent legacy container cleanup
##   _cleanup_observability_containers [W:2] — observability pre-deploy cleanup
##   _pull_module_images [W:2] — pull images for one module (delegate → parallel_runner.pre_pull_images)
##   main [W:2] — CLI entry point with argparse
## @usecases
##   - deploy-modules.sh → docker_orchestrator.py --action deploy --module-name postgres ...
##   - deploy-modules.sh → docker_orchestrator.py --action pre-pull --module-entries ...
##   - deploy-modules.sh → docker_orchestrator.py --action deploy-group --module-entries "mod1 mod2" ...
##   - deploy-modules.sh → docker_orchestrator.py --action wait --module-name postgres
##   - deploy-modules.sh → docker_orchestrator.py --action healthcheck --module-name postgres
##   - deploy-modules.sh → docker_orchestrator.py --action check-image --image-ref ghcr.io/...
## @links    CALLED_BY(core/internal/bootstrap/deploy-modules.sh), DEPENDS_ON(core/lib/module-interface.sh),
##           RELATED(core/internal/bootstrap/deploy/orphan_reconciler.py),
##           SIBLINGS(parallel_runner.py, healthcheck_runner.py, hermes_workflow.py — DevPlan 118 D1)
# endregion MODULE_CONTRACT

import argparse
import contextlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Local imports (build_cache.py lives in same deploy/ directory; волна 118 B2:
#    content_hash.py переименован в build_cache.py — устранение коллизии имён с shared/content_hash.py) ──
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
from build_cache import check_build_needed, compute_source_hash, save_build_hash

# DevPlan 117 D18: единый канон orphan-реконсиляции — orphan_reconciler (batch-подход,
# один docker ps -a). Локальный per-module orphan-cleanup удалён (дубль логики).
# DevPlan 118 D1: healthcheck-инвокации и hermes-workflow вынесены в отдельные модули.
# DevPlan 119 E1: observability-фаза — отдельный модуль (экстракция _cleanup_observability_containers).
from core.internal.bootstrap.deploy import (
    healthcheck_runner,
    hermes_workflow,
    observability,
    orphan_reconciler,
    parallel_runner,
)
from core.internal.shared.audit_logger import write_audit_entry as _shared_write_audit_entry

# DevPlan 081 Phase C (TASK-081C3): shared audit_logger for JSON-lines audit
# DRIFT-D6 resolved: unified JSON-lines audit format
# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base

# DevPlan 079 DRIFT-B6 + 116 B5 T4: shared docker compose operations — ЕДИНСТВЕННЫЙ путь
# (docker compose up/build/pull/config/down живут в shared/docker_compose.py, гейт docker_sole_path).
from core.internal.shared.docker_compose import (
    check_image_exists as _shared_check_image_exists,
)
from core.internal.shared.docker_compose import (
    docker_compose_build as _shared_docker_compose_build,
)
from core.internal.shared.docker_compose import (
    docker_compose_up as _shared_docker_compose_up,
)

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11, гейт timeout_literals)
from core.internal.shared.timeouts import (
    BUILD_TIMEOUT,
    COMPOSE_UP_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
    DOCKER_STOP_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Constants ──
# DevPlan 118 D1: L1_BASE_IMAGE/GHCR_ORG перенесены в hermes_workflow.py (спец-workflow hermes).
# DevPlan 118 A2: единый канон списков compose-файлов — shared/compose_files.py (гейт
# compose_files_sole_path). Локальный COMPOSE_FILENAMES УДАЛЁН (6 копий → 1 SoT).
from core.internal.shared.compose_files import COMPOSE_FILENAMES as _CANON_COMPOSE_FILENAMES
from core.internal.shared.compose_files import resolve_compose_file as _resolve_compose_file_shared

# DevPlan 118 C3: единый loader COMPOSE_PROFILES — shared/compose_profiles.py (SoT platform-infra.yaml).
from core.internal.shared.compose_profiles import load_profiles as compose_profiles_load_profiles

# DevPlan 118 C5: единая bash-обёртка invoke_module_interface — shared/module_interface.py (вход для B8).

# DevPlan 118 D1: константы параллелизма/healthcheck re-export из parallel_runner / healthcheck_runner
# (обратная совместимость для deploy_orchestrator.py и тестов).
DEFAULT_PARALLEL_LIMIT = parallel_runner.DEFAULT_PARALLEL_LIMIT
DEFAULT_READINESS_MAX_ATTEMPTS = healthcheck_runner.DEFAULT_READINESS_MAX_ATTEMPTS
DEFAULT_READINESS_INTERVAL_SEC = healthcheck_runner.DEFAULT_READINESS_INTERVAL_SEC
DEFAULT_HEALTHCHECK_MAX_RETRIES = healthcheck_runner.DEFAULT_HEALTHCHECK_MAX_RETRIES
DEFAULT_HEALTHCHECK_RETRY_INTERVAL = healthcheck_runner.DEFAULT_HEALTHCHECK_RETRY_INTERVAL

# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · COMPOSE_PROFILES hardcoded here diverged from SoT (U-02)
# · Symptom: 12-item setdefault (без status-page) vs platform-infra.yaml 13-item env_defaults —
#   `docker compose config --services` в deploy-orchestrator не видел status-page профиль.
# · Root: хардкод-копия списка профилей (8-я копия из аудита U-02).
# · Fix: runtime-чтение core/platform-infra.yaml env_defaults.COMPOSE_PROFILES (SoT).
# · Verification: platform-infra.yaml доставляется с core/ (core-deploy rsync включает
#   core/platform-infra.yaml — подтверждено 2026-07-31). Fail-fast (raise) при отсутствии.
# · Rev: если VPS-деплой когда-либо окажется без platform-infra.yaml — fallback на
#   os.environ.setdefault без хардкода (dev-ветка проверки), см. DevPlan 116 §4 риски.

# Path to invoke_module_interface shell function — used for readiness and healthcheck.
# C5 (DevPlan 118): сборка bash -c делегирована в shared/module_interface.invoke — локальные
# константы путей УДАЛЕНЫ (пути резолвятся в shared-модуле, единый источник).

# DevPlan 119 E1: dispatch-таблица спец-фаз deploy_docker_module по имени модуля
# определяется ПОСЛЕ определений фаз (см. PHASES после FUNC__phase_up).


# region FUNC__resolve_compose_profiles_from_infra
## @purpose  Resolve COMPOSE_PROFILES из единого loader'а shared/compose_profiles (SoT platform-infra.yaml,
##           DevPlan 117 D23 + 118 C3). Удалён сырой yaml.safe_load platform-infra.yaml и прямой вызов
##           platform_config.get_default (дубли loader'а). Fail-fast: raise при отсутствии SoT/ключа.
## @io       ⇥ None → ⎋ str: comma-separated profile list ⚡ raise FileNotFoundError/KeyError (fail-fast)
## @complexity O(1) — single config lookup
## @invariants
##   - shared/compose_profiles читает platform-infra.yaml env_defaults.COMPOSE_PROFILES (SoT)
##   - Raises if COMPOSE_PROFILES absent (fail-fast, no silent fallback)
##   - Caller keeps os.environ.setdefault semantics — explicit env COMPOSE_PROFILES wins
def _resolve_compose_profiles_from_infra() -> str:
    """Return COMPOSE_PROFILES from shared loader (SoT platform-infra.yaml, C3)."""
    profiles = compose_profiles_load_profiles()
    if not profiles:
        raise KeyError(
            "[IMP:10][docker_orchestrator] env_defaults.COMPOSE_PROFILES missing in platform-infra.yaml (SoT) — "
            "run `make generate-platform-env` (DevPlan 116 T2, U-02)."
        )
    result = ",".join(profiles)
    logger.info("[IMP:9][_resolve_compose_profiles_from_infra][OK] COMPOSE_PROFILES from SoT: %s", result)
    return result


# endregion FUNC__resolve_compose_profiles_from_infra


# region FUNC__check_image_exists
## @purpose  Check if a Docker image exists in registry via shared module (DevPlan 079).
##           Delegates to check_image_exists() from core.internal.shared.docker_compose.
## @io       ⇥ image_ref: str → ⎋ bool: True if image exists
## @complexity 1 — delegates to shared module
## @invariants
##   - Uses shared check_image_exists which wraps docker manifest inspect
def _check_image_exists(image_ref: str) -> bool:
    """Check if a Docker image exists via shared check_image_exists."""
    return _shared_check_image_exists(image_ref)


# endregion FUNC__check_image_exists


# region FUNC__resolve_compose_file
## @purpose  Find the first existing compose file in a module directory.
##           Resolution order: canonical COMPOSE_FILENAMES from shared/compose_files.py
##           (compose.yaml → docker-compose.yaml → docker-compose.yml → docker-compose.base.yml,
##           DevPlan 118 A2) — thin delegating wrapper (публичный API модуля сохраняется).
## @io       ⇥ module_dir: str (path to module directory)
##           ⎋ Path | None — resolved compose file path, or None if none found
## @complexity 1 — linear scan of canonical tuple (delegates to shared)
def _resolve_compose_file(module_dir: str) -> Path | None:
    logger.info("[IMP:7][_resolve_compose_file][scan] Resolving compose file in %s", module_dir)
    resolved = _resolve_compose_file_shared(module_dir)
    if resolved is not None:
        logger.info("[IMP:8][_resolve_compose_file][found] Using compose file: %s", resolved)
    else:
        logger.warning(
            "[IMP:5][_resolve_compose_file][missing] No compose file found in %s (tried %s)",
            module_dir,
            _CANON_COMPOSE_FILENAMES,
        )
    return resolved


# endregion FUNC__resolve_compose_file


# region FUNC__build_compose_args
## @purpose  Build docker compose argument list from compose file, env files, overlay, and profile.
## @io       ⇥ compose_file: Path, secrets_env_file: str | None, platform_root: str | None,
##           overlay_dir: str | None, module_name: str
##           ⎋ list[str] — docker compose arguments
## @complexity 1 — linear arg building
## @invariants
##   - --env-file for secrets.env is added only if the file exists
##   - --env-file for platform .env is added only if the file exists
##   - -f for overlay compose.override.yaml is added only if it exists
##   - --profile is always passed with module_name
def _build_compose_args(
    compose_file: Path,
    secrets_env_file: str | None,
    platform_root: str | None,
    overlay_dir: str | None,
    module_name: str,
) -> list[str]:
    logger.info("[IMP:7][_build_compose_args][build] Building compose args for %s", module_name)
    args: list[str] = []

    # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · root compose ПЕРВЫМ и ЕДИНСТВЕННЫМ -f (RC 121, U-49 regression)
    # · Symptom 1: изолированный модульный -f: "refers to undefined volume backup-spool"
    # · Symptom 2: root + модульный -f вместе: "security_opt items at 0 and 1 are equal" —
    #   root compose УЖЕ include'ит модульные base.yml; двойное включение конкатенирует списки.
    # · Fix: при наличии root compose (U-49 доставка) модуль деплоится ТОЛЬКО через root
    #   (+ --profile module); модульный файл отдельно — только когда root отсутствует (fallback).
    root_compose = os.path.join(platform_root or str(platform_remote_base()), "docker-compose.yml")
    if os.path.isfile(root_compose):
        args.extend(["-f", root_compose])
        logger.info("[IMP:8][_build_compose_args][root-compose] Adding root compose ONLY: %s", root_compose)
    else:
        args.extend(["-f", str(compose_file)])
        logger.info("[IMP:8][_build_compose_args][module-compose] Root compose absent — module file only: %s", compose_file)

    # Secrets env file
    env_file = secrets_env_file or "/run/platform/secrets.env"
    if os.path.isfile(env_file):
        args.extend(["--env-file", env_file])
        logger.info("[IMP:8][_build_compose_args][env] Adding secrets env-file: %s", env_file)

    # Platform root .env
    platform_env = os.path.join(platform_root or str(platform_remote_base()), ".env")
    if os.path.isfile(platform_env):
        args.extend(["--env-file", platform_env])
        logger.info("[IMP:8][_build_compose_args][env] Adding platform env-file: %s", platform_env)

    # Overlay compose override
    if overlay_dir:
        override = Path(overlay_dir) / "compose.override.yaml"
        if override.is_file():
            args.extend(["-f", str(override)])
            logger.info("[IMP:8][_build_compose_args][overlay] Adding overlay compose: %s", override)

    # Profile — required for standalone base.yml deploy
    args.extend(["--profile", module_name])
    logger.info("[IMP:8][_build_compose_args][profile] Adding profile: %s", module_name)

    return args


# endregion FUNC__build_compose_args


# region FUNC__handle_hermes_agent
## @purpose  Handle hermes-agent special case — DevPlan 118 D1: реализация вынесена в
##           hermes_workflow.handle_hermes_agent (спец-workflow). Тонкий фасад сохраняет
##           публичное имя для обратной совместимости (тесты, deploy_docker_module).
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 1 — delegate to hermes_workflow
## @invariants
##   - Вся логика (L1 pull/build fallback) — в hermes_workflow.py (D1)
##   - Фасад не дублирует логику — только делегирование
def _handle_hermes_agent(compose_args: list[str], module_dir: str, module_name: str) -> bool:
    return hermes_workflow.handle_hermes_agent(compose_args, module_dir, module_name)


# endregion FUNC__handle_hermes_agent


# region FUNC_deploy_docker_module
## @purpose  Deploy a single Docker module via docker compose build (if build: section) + up -d --remove-orphans.
##           DevPlan 119 E1: монолит (195 LOC, CC=25) разбит на фазы с dispatch-таблицей
##           PHASES (hermes → hermes_workflow, observability → observability.py, rebuild/up → локально).
##           Оркестратор: resolve compose → build args → dispatch спец-фаз → orphan reconcile →
##           nginx overlay → rebuild → up.
## @io       ⇥ module_name: str, overlay_dir: str | None, secrets_env_file: str | None,
##           platform_root: str | None, modules_dir: str | None
##           ⎋ bool: True if deploy succeeded
## @complexity 5 — multi-step: compose resolve → args build → phase dispatch → orphan → rebuild → up
## @invariants
##   - Returns False (not exception) on failure — caller decides abort vs continue
##   - Спец-фазы (hermes/observability) диспатчатся через PHASES (E1) — 0 inline if-каскадов
##   - Observability module gets per-service container cleanup before compose up
##   - COMPOSE_PROFILES env var is set to full profile list for config --services calls
##   - Modules with build: section (except hermes-agent) get `docker compose build` before up -d
##     to pick up source changes from core-deploy rsync (docker compose up -d is no-op for
##     already-running containers with unchanged config)
## @rationale Q: Why phase dispatch? A: E1 (DevPlan 119, AUDIT-2 M7) — deploy_docker_module CC=25,
##   13 if-веток. Разбиение по фазам + PHASES-таблица снижает CC до ≤10 и даёт изолированные
##   тесты фаз (test_phase_hermes_build / test_deploy_docker_module_phases_negative).
## @changes   2026-07-23 · Added docker compose build step for modules with build: section
##             (P0 bug: status-page showing old container after core-deploy)
##            2026-08-02 · DevPlan 119 E1 — phased decomposition (PHASES dispatch)
def deploy_docker_module(
    module_name: str,
    overlay_dir: str | None = None,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    modules_dir: str | None = None,
) -> bool:
    module_dir = modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules")
    logger.info("[IMP:7][deploy_docker_module][start] Deploying docker module: %s", module_name)

    # DevPlan 081 Phase C: audit deploy start (TASK-081C3)
    with contextlib.suppress(Exception):
        _shared_write_audit_entry(
            tag=f"docker_orchestrator:deploy:{module_name}",
            status="START",
            message=f"Deploying docker module {module_name}",
        )

    # ── Resolve compose file ──
    compose_file = _resolve_compose_file(os.path.join(module_dir, module_name))
    if compose_file is None:
        logger.error(
            "[IMP:10][deploy_docker_module][no_compose] Compose file not found for %s in %s",
            module_name,
            os.path.join(module_dir, module_name),
        )
        with contextlib.suppress(Exception):
            _shared_write_audit_entry(
                tag=f"docker_orchestrator:deploy:{module_name}",
                status="FAILED",
                message=f"Compose file not found for {module_name}",
            )
        return False

    # ── Build compose args ──
    compose_args = _build_compose_args(
        compose_file=compose_file,
        secrets_env_file=secrets_env_file,
        platform_root=platform_root,
        overlay_dir=overlay_dir,
        module_name=module_name,
    )

    # ── PHASE DISPATCH (E1): спец-фазы по имени модуля ──
    phase_fn = PHASES.get(module_name)
    if phase_fn is not None:
        phase_ok = phase_fn(
            module_name=module_name,
            module_dir=module_dir,
            compose_file=compose_file,
            compose_args=compose_args,
        )
        if phase_ok is False:
            logger.error("[IMP:10][deploy_docker_module][phase_fail] Phase %s failed for %s", module_name, module_name)
            return False

    # ── COMPOSE_PROFILES for config --services calls ──
    # SoT: core/platform-infra.yaml env_defaults (DevPlan 116 T2, U-02). Explicit env
    # COMPOSE_PROFILES (from Makefile export / CI) takes precedence — setdefault semantics.
    os.environ.setdefault("COMPOSE_PROFILES", _resolve_compose_profiles_from_infra())

    # ── Orphan container reconciliation ──
    # DevPlan 117 D18: делегирование в orphan_reconciler (единый канон). batch_orphan_reconciliation
    # работает per-module (один module_entries) и batch-путь (deploy_orchestrator) — batch-подход
    # эффективнее (один docker ps -a); remove_orphans удаляет найденные orphan-контейнеры.
    orphans = orphan_reconciler.batch_orphan_reconciliation([module_name], module_dir)
    if orphans:
        removed = orphan_reconciler.remove_orphans(orphans)
        logger.info("[IMP:9][deploy_docker_module][orphan] Removed %d orphan container(s) for %s", removed, module_name)

    # ── NGINX overlay env ──
    if module_name == "nginx" and overlay_dir:
        os.environ["NGINX_OVERLAY_DIR"] = overlay_dir
        logger.info("[IMP:8][deploy_docker_module][nginx] Set NGINX_OVERLAY_DIR=%s", overlay_dir)

    # ── PHASE: rebuild (build:-modules) ──
    rebuild_ok, has_local_build = _phase_rebuild(
        module_name=module_name,
        module_dir=module_dir,
        compose_file=compose_file,
        compose_args=compose_args,
    )
    if not rebuild_ok:
        return False

    # ── PHASE: compose up -d ──
    return _phase_up(
        module_name=module_name,
        module_dir=module_dir,
        compose_args=compose_args,
        has_local_build=has_local_build,
    )


# endregion FUNC_deploy_docker_module


# region FUNC__phase_hermes
## @purpose  E1 hermes-agent phase: legacy container cleanup + pre-deploy image check/build.
##           Делегирует в hermes_workflow.handle_hermes_agent (D1). Возвращает False на
##           фатальном сбое (build/pull failure).
## @io       ⇥ module_name, module_dir, compose_file, compose_args → ⎋ bool (True = ready)
## @complexity 1 — delegation (legacy cleanup + hermes_workflow)
## @invariants
##   - Legacy container "hermes-base-agent" clean-up перед hermes-проверками
##   - False → деплой abort (hermes-critical)
def _phase_hermes(
    module_name: str,
    module_dir: str,
    compose_file,
    compose_args: list[str],
) -> bool:
    """E1 phase: hermes-agent legacy cleanup + pre-deploy image check/build."""
    _cleanup_legacy_container("hermes-base-agent")
    logger.info("[IMP:8][_phase_hermes][legacy] Legacy container check done")
    return _handle_hermes_agent(compose_args, module_dir, module_name)


# endregion FUNC__phase_hermes


# region FUNC__phase_observability
## @purpose  E1 observability phase: pre-deploy container cleanup (name-conflict prevention).
##           Делегирует в observability.cleanup_observability_containers (E1). Всегда True
##           (cleanup best-effort, не блокирует деплой).
## @io       ⇥ module_name, module_dir, compose_file, compose_args → ⎋ bool (True)
## @complexity 1 — delegation
## @invariants
##   - Сбой очистки → WARN внутри observability.py, НЕ блокирует деплой
def _phase_observability(
    module_name: str,
    module_dir: str,
    compose_file,
    compose_args: list[str],
) -> bool:
    """E1 phase: observability pre-deploy container cleanup (best-effort)."""
    observability.cleanup_observability_containers(compose_file)
    return True


# endregion FUNC__phase_observability


# region FUNC__phase_rebuild
## @purpose  E1 rebuild phase: content-hash skip → docker compose build → save hash.
##           Возвращает (ok, has_local_build): ok=False → deploy abort; has_local_build →
##           флаг для --force-recreate в up-фазе.
## @io       ⇥ module_name, module_dir, compose_file, compose_args
##           ⎋ tuple[bool, bool] — (success, has_local_build)
## @complexity 3 — build: detection + content-hash skip + build/save
## @invariants
##   - hermes-agent исключён (свой workflow в _phase_hermes)
##   - Content-hash skip: source unchanged → только up --force-recreate
##   - Сбой build → False (deploy abort)
def _phase_rebuild(
    module_name: str,
    module_dir: str,
    compose_file,
    compose_args: list[str],
) -> tuple[bool, bool]:
    """E1 phase: rebuild image for modules with build: section (content-hash skip)."""
    # · Rationale: docker compose up -d is a no-op for already-running containers
    #   with unchanged config. Modules with build: (status-page, backup-cron) need
    #   explicit rebuild to pick up source changes from core-deploy rsync.
    # · Hermes-agent excluded — has its own image workflow via _phase_hermes.
    # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · status-page showing old container after deploy
    # · Symptom: https://platform.tronyx.ru/ shows stale page after successful core-deploy
    # · Root: docker compose up -d no-op — bind-mounted app.py updated on disk but
    #   Python process already loaded old code; templates/ only in image (COPY, no bind
    #   mount) — image never rebuilt. Docker Compose local image tag doesn't change
    #   → config hash unchanged → container never recreated.
    # · Fix 1: docker compose build before up -d for modules with build: section.
    # · Fix 2: --force-recreate after build — Docker Compose does NOT detect local
    #   image ID change for same-tag images (status-page:latest rebuilt → same tag,
    #   different ID, but compose uses tag, not ID, for change detection).
    # · Rev: if build time exceeds 60s for status-page → consider content-hash-based skip.
    has_local_build = False
    if module_name != "hermes-agent":
        try:
            compose_content = compose_file.read_text()
        except OSError:
            compose_content = ""
        if "build:" in compose_content:
            has_local_build = True

            # ── W3.T3.3: Content-hash skip — rebuild only if source changed ──
            # ⚠️ TRAP[BUG] · 2026-07-24 · P2 · check_build_needed receives modules root, not specific module
            # · Fix: use os.path.join(module_dir, module_name) to target the specific module subdirectory
            build_needed = check_build_needed(os.path.join(module_dir, module_name))
            if not build_needed:
                logger.info(
                    "[IMP:9][_phase_rebuild][build_skip] Build skipped for %s — source unchanged (content-hash match)",
                    module_name,
                )
                # Source unchanged — skip build, still need --force-recreate in
                # case compose config changed (env files, compose override, etc.)
                logger.info(
                    "[IMP:8][_phase_rebuild][up_skip_build] Running compose up --force-recreate for %s (build skipped)",
                    module_name,
                )
                return True, has_local_build

            logger.info("[IMP:7][_phase_rebuild][build] Rebuilding image for %s (build: detected)", module_name)
            # T4.3 (DevPlan 116 B5): shared docker_compose_build — sole path (timeout BUILD_TIMEOUT)
            if not _shared_docker_compose_build(
                os.path.join(module_dir, module_name),
                timeout=BUILD_TIMEOUT,
                compose_args=compose_args,
            ):
                logger.error("[IMP:10][_phase_rebuild][build_fail] docker compose build failed for %s", module_name)
                return False, has_local_build
            logger.info("[IMP:9][_phase_rebuild][build] Image rebuilt for %s", module_name)
            # Save hash after successful build (W3.T3.3) — бизнес-логика остаётся здесь
            # ⚠️ TRAP[BUG] · 2026-07-24 · P2 · compute_source_hash/save_build_hash receives modules root
            # · Fix: use os.path.join(module_dir, module_name) for specific module subdirectory
            try:
                new_hash = compute_source_hash(os.path.join(module_dir, module_name))
                if new_hash:
                    save_build_hash(os.path.join(module_dir, module_name), new_hash)
            except (OSError, FileNotFoundError) as exc:
                logger.warning(
                    "[IMP:7][_phase_rebuild][hash] Failed to save build hash for %s: %s",
                    module_name,
                    exc,
                )
    return True, has_local_build


# endregion FUNC__phase_rebuild


# region FUNC__phase_up
## @purpose  E1 up phase: docker compose up -d --remove-orphans [--force-recreate] + audit.
## @io       ⇥ module_name, module_dir, compose_args, has_local_build → ⎋ bool
## @complexity 1 — single shared docker_compose_up call + audit
## @invariants
##   - --force-recreate added for build:-modules (bypass same-tag no-op)
##   - Audit DEPLOYED/FAILED через shared audit_logger (D6)
##   - Различение TIMEOUT/ERROR/FAILED схлопывается в FAILED (детали в shared-логах)
def _phase_up(
    module_name: str,
    module_dir: str,
    compose_args: list[str],
    has_local_build: bool,
) -> bool:
    """E1 phase: docker compose up -d --remove-orphans [--force-recreate] + audit."""
    # · --force-recreate added for build:-modules to bypass Docker Compose's
    #   local-image-same-tag no-op (build creates new image under same tag,
    #   compose doesn't detect the change → container not recreated).
    flags = ["--remove-orphans"] + (["--force-recreate"] if has_local_build else [])
    logger.info(
        "[IMP:8][_phase_up][up] Running compose up for %s (flags=%s)",
        module_name,
        " ".join(flags),
    )
    # T4.4 (DevPlan 116 B5): shared docker_compose_up — sole path; audit DEPLOYED/FAILED (D6).
    if _shared_docker_compose_up(
        os.path.join(module_dir, module_name),
        timeout=COMPOSE_UP_TIMEOUT,
        compose_args=compose_args,
        flags=flags,
    ):
        logger.info("[IMP:9][_phase_up][done] Module deployed: %s", module_name)
        # DevPlan 081 Phase C: audit via shared audit_logger (TASK-081C3)
        with contextlib.suppress(Exception):
            _shared_write_audit_entry(
                tag=f"docker_orchestrator:deploy:{module_name}",
                status="DEPLOYED",
                message=f"docker compose up succeeded for {module_name}",
            )
        time.sleep(1)
        return True
    logger.error("[IMP:10][_phase_up][up_fail] docker compose up failed for %s", module_name)
    # DevPlan 081 Phase C: audit deploy fail (TASK-081C3) — D6: FAILED (не TIMEOUT/ERROR)
    with contextlib.suppress(Exception):
        _shared_write_audit_entry(
            tag=f"docker_orchestrator:deploy:{module_name}",
            status="FAILED",
            message=f"docker compose up failed for {module_name}",
        )
    return False


# endregion FUNC__phase_up


# region PHASES_DISPATCH
## @purpose  DevPlan 119 E1: dispatch-таблица спец-фаз deploy_docker_module по имени модуля.
##           Фазы-кандидаты: hermes-agent (legacy cleanup + image check/build) и observability
##           (pre-deploy container cleanup). Каждая фаза: (module_name, module_dir, compose_file,
##           compose_args) -> bool. False → abort деплоя (hermes-critical). rebuild/up фазы
##           вызываются отдельно (общие для всех модулей, см. deploy_docker_module).
## @invariants
##   - Добавление новой спец-фазы = регистрация здесь + определение функции
##   - Порядок dispatch: PHASES проверяется ПОСЛЕ compose args, ДО orphan/rebuild/up
PHASES: dict[str, object] = {
    "hermes-agent": _phase_hermes,
    "observability": _phase_observability,
}


# endregion PHASES_DISPATCH


# region FUNC__cleanup_legacy_container
## @purpose  Stop and remove a legacy container by name (used for hermes-agent migration).
## @io       ⇥ container_name: str
##           ⎋ None (side-effect: docker stop + rm)
## @complexity 1 — two subprocess calls with graceful error handling
def _cleanup_legacy_container(container_name: str) -> None:
    logger.info("[IMP:7][_cleanup_legacy_container][check] Checking for legacy container: %s", container_name)
    try:
        ps_result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_CMD_TIMEOUT,
        )
        # ⚠️ TRAP[BUG] · 2026-07-22 · P2 · str/bytes type safety in subprocess stdout
        # · Symptom: container_name in stdout.splitlines() silently fails when mock returns bytes
        # · Root: subprocess.run with text=True returns str, but mock tests pass bytes.
        #   `str in bytes_list` is always False in Python 3.
        # · Fix: decode bytes to str before comparison.
        # · Prevention: always normalize stdout before string operations.
        stdout = ps_result.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8")
        if container_name in stdout.splitlines():
            logger.info("[IMP:8][_cleanup_legacy_container][stop] Stopping legacy container: %s", container_name)
            subprocess.run(
                ["docker", "stop", container_name], capture_output=True, timeout=DOCKER_STOP_TIMEOUT, check=False
            )
            subprocess.run(
                ["docker", "rm", container_name], capture_output=True, timeout=DOCKER_STOP_TIMEOUT, check=False
            )
            logger.info("[IMP:9][_cleanup_legacy_container][removed] Legacy container removed: %s", container_name)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:5][_cleanup_legacy_container][error] Failed to clean up %s: %s", container_name, exc)


# endregion FUNC__cleanup_legacy_container


# region FUNC__cleanup_observability_containers
## @purpose  Clean up pre-existing containers for observability module services
##           before compose up (prevents name conflict on re-deploy).
##           DevPlan 119 E1: реализация вынесена в observability.cleanup_observability_containers.
##           Тонкий фасад сохраняет публичное имя для обратной совместимости (тесты).
## @io       ⇥ compose_file: Path
##           ⎋ None (side-effect: docker stop + rm for each service container)
## @complexity 1 — delegate to observability module
## @invariants
##   - Вся логика — в observability.py (E1)
##   - Фасад не дублирует логику — только делегирование
def _cleanup_observability_containers(compose_file: Path) -> None:
    observability.cleanup_observability_containers(compose_file)


# endregion FUNC__cleanup_observability_containers


# region FUNC__pull_module_images
## @purpose  Pull images for a single docker module — DevPlan 118 D1: реализация вынесена в
##           parallel_runner.pull_module_images (fork-параллелизм). Фасад сохраняет публичное
##           имя для обратной совместимости (тесты, pre_pull_images re-export).
## @io       ⇥ mod_name, overlay_dir, secrets_env_file, platform_root, modules_dir → ⎋ bool
## @complexity 1 — delegate to parallel_runner
def _pull_module_images(
    mod_name: str,
    overlay_dir: str | None,
    secrets_env_file: str | None,
    platform_root: str | None,
    modules_dir: str,
) -> bool:
    return parallel_runner.pull_module_images(mod_name, overlay_dir, secrets_env_file, platform_root, modules_dir)


# endregion FUNC__pull_module_images


# region FUNC_pre_pull_images
## @purpose  Parallel pre-pull of all docker module images — DevPlan 118 D1: реализация вынесена
##           в parallel_runner.pre_pull_images (fork-параллелизм). Фасад для обратной совместимости.
## @io       ⇥ entries, modules_dir, ... → ⎋ tuple[int, int] (success_count, fail_count)
## @complexity 1 — delegate to parallel_runner
def pre_pull_images(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[int, int]:
    return parallel_runner.pre_pull_images(
        entries,
        modules_dir,
        secrets_env_file,
        platform_root,
        parallel_limit,
    )


# endregion FUNC_pre_pull_images


# region FUNC_deploy_docker_group
## @purpose  Deploy a group of docker modules in parallel — DevPlan 118 D1: реализация вынесена
##           в parallel_runner.deploy_docker_group (fork + atomic rollback + parallel HC).
##           Фасад сохраняет публичное имя и контракт (deploy_orchestrator.py:477).
## @io       ⇥ entries, modules_dir, ... → ⎋ tuple[int, int, list[str], list[str]]
## @complexity 1 — delegate to parallel_runner
def deploy_docker_group(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[int, int, list[str], list[str]]:
    return parallel_runner.deploy_docker_group(
        entries,
        modules_dir,
        secrets_env_file,
        platform_root,
        parallel_limit,
    )


# endregion FUNC_deploy_docker_group


# region FUNC__drain_completed_count
## @purpose  Non-blocking drain — DevPlan 118 D1: реализация в parallel_runner.drain_completed_count.
## @io       ⇥ pids, pid_to_name → ⎋ tuple[int, int, list[str]]
## @complexity 1 — delegate
def _drain_completed_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    return parallel_runner.drain_completed_count(pids, pid_to_name)


# endregion FUNC__drain_completed_count


# region FUNC__drain_all_count
## @purpose  Blocking drain — DevPlan 118 D1: реализация в parallel_runner.drain_all_count.
## @io       ⇥ pids, pid_to_name → ⎋ tuple[int, int, list[str]]
## @complexity 1 — delegate
def _drain_all_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    return parallel_runner.drain_all_count(pids, pid_to_name)


# endregion FUNC__drain_all_count


# region FUNC_wait_for_readiness
## @purpose  Poll module readiness — DevPlan 118 D1: реализация вынесена в
##           healthcheck_runner.wait_for_readiness. Фасад сохраняет публичное имя.
## @io       ⇥ module_name, max_attempts, interval_sec → ⎋ bool
## @complexity 1 — delegate to healthcheck_runner
def wait_for_readiness(
    module_name: str,
    max_attempts: int = DEFAULT_READINESS_MAX_ATTEMPTS,
    interval_sec: int = DEFAULT_READINESS_INTERVAL_SEC,
) -> bool:
    return healthcheck_runner.wait_for_readiness(module_name, max_attempts, interval_sec)


# endregion FUNC_wait_for_readiness


# region FUNC_run_healthcheck
## @purpose  Run healthcheck — DevPlan 118 D1: реализация вынесена в
##           healthcheck_runner.run_healthcheck. Фасад сохраняет публичное имя.
## @io       ⇥ module_name, install_type, max_retries, retry_interval → ⎋ bool
## @complexity 1 — delegate to healthcheck_runner
def run_healthcheck(
    module_name: str,
    install_type: str,
    max_retries: int = DEFAULT_HEALTHCHECK_MAX_RETRIES,
    retry_interval: int = DEFAULT_HEALTHCHECK_RETRY_INTERVAL,
) -> bool:
    return healthcheck_runner.run_healthcheck(module_name, install_type, max_retries, retry_interval)


# endregion FUNC_run_healthcheck


# region FUNC__invoke_healthcheck
## @purpose  Call invoke_module_interface for healthcheck — DevPlan 118 D1: реализация в
##           healthcheck_runner.invoke_healthcheck. Фасад сохраняет публичное имя.
## @io       ⇥ module_name, check_type → ⎋ bool
## @complexity 1 — delegate
def _invoke_healthcheck(module_name: str, check_type: str) -> bool:
    return healthcheck_runner.invoke_healthcheck(module_name, check_type)


# endregion FUNC__invoke_healthcheck


# region FUNC__invoke_healthcheck_full
## @purpose  Call invoke_module_interface for healthcheck — DevPlan 118 D1: реализация в
##           healthcheck_runner.invoke_healthcheck_full (делегирует в shared/module_interface, C5).
## @io       ⇥ module_name, check_type → ⎋ tuple[bool, str]
## @complexity 1 — delegate
def _invoke_healthcheck_full(module_name: str, check_type: str) -> tuple[bool, str]:
    return healthcheck_runner.invoke_healthcheck_full(module_name, check_type)


# endregion FUNC__invoke_healthcheck_full


# region FUNC_main
## @purpose  CLI entry point: dispatch to action handlers based on --action flag.
## @io       sys.argv → stdout/logs, int exit code
## @complexity 2 — argparse dispatch with per-action argument validation
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Docker orchestration: deploy, pre-pull, deploy-group, wait, healthcheck, check-image"
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["deploy", "pre-pull", "deploy-group", "wait", "healthcheck", "check-image"],
        help="Action to perform",
    )
    parser.add_argument("--module-name", help="Module name (for deploy, wait, healthcheck)")
    parser.add_argument(
        "--module-entries",
        nargs="*",
        default=[],
        help="Module entries in module:overlay format (for pre-pull, deploy-group)",
    )
    parser.add_argument("--node-yaml", help="Path to node.yaml (unused in docker_orchestrator)")
    parser.add_argument("--modules-dir", help="Path to modules directory")
    parser.add_argument("--secrets-env-file", help="Path to secrets.env file")
    parser.add_argument("--platform-root", help="Platform root directory (default: /opt/platform)")
    parser.add_argument(
        "--overlay-dir",
        help="Overlay directory for context-specific compose override",
    )
    parser.add_argument(
        "--image-ref",
        help="Image reference for check-image action",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_READINESS_MAX_ATTEMPTS,
        help="Max attempts for readiness check (default: 15)",
    )
    parser.add_argument(
        "--install-type",
        default="docker",
        choices=["docker", "system"],
        help="Install type for healthcheck (default: docker)",
    )
    parser.add_argument(
        "--parallel-limit",
        type=int,
        default=DEFAULT_PARALLEL_LIMIT,
        help="Parallel deploy/pull limit (default: 4)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:7][main][start] Action: %s", args.action)

    if args.action == "deploy":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for deploy action")
            return 1
        success = deploy_docker_module(
            module_name=args.module_name,
            overlay_dir=args.overlay_dir,
            secrets_env_file=args.secrets_env_file,
            platform_root=args.platform_root,
            modules_dir=args.modules_dir,
        )
        logger.info("[IMP:9][main][result] Deploy %s: %s", args.module_name, "OK" if success else "FAIL")
        return 0 if success else 1

    if args.action == "pre-pull":
        if not args.module_entries:
            logger.error("[IMP:10][main][error] --module-entries required for pre-pull action")
            return 1
        ok, fail = pre_pull_images(
            entries=list(args.module_entries),
            modules_dir=args.modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules"),
            secrets_env_file=args.secrets_env_file,
            platform_root=args.platform_root,
            parallel_limit=args.parallel_limit,
        )
        logger.info("[IMP:9][main][result] Pre-pull: success=%d failed=%d", ok, fail)
        return 0

    if args.action == "deploy-group":
        if not args.module_entries:
            logger.error("[IMP:10][main][error] --module-entries required for deploy-group action")
            return 1
        deployed, failed, failed_names, rolled_back = deploy_docker_group(
            entries=list(args.module_entries),
            modules_dir=args.modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules"),
            secrets_env_file=args.secrets_env_file,
            platform_root=args.platform_root,
            parallel_limit=args.parallel_limit,
        )
        logger.info(
            "[IMP:9][main][result] Deploy group: deployed=%d failed=%d rolled_back=%d names=%s",
            deployed,
            failed,
            len(rolled_back),
            failed_names,
        )
        return 0 if failed == 0 else 1

    if args.action == "wait":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for wait action")
            return 1
        ready = wait_for_readiness(
            module_name=args.module_name,
            max_attempts=args.max_attempts,
        )
        return 0 if ready else 1

    if args.action == "healthcheck":
        if not args.module_name:
            logger.error("[IMP:10][main][error] --module-name required for healthcheck action")
            return 1
        passed = run_healthcheck(
            module_name=args.module_name,
            install_type=args.install_type,
        )
        return 0 if passed else 1

    if args.action == "check-image":
        if not args.image_ref:
            logger.error("[IMP:10][main][error] --image-ref required for check-image action")
            return 1
        exists = _check_image_exists(args.image_ref)
        return 0 if exists else 1

    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
