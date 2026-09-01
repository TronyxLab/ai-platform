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
##   - Hermes-agent build from source fallback on image 404 (not FAIL — automatic rebuild)
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
##   2026-08-22 · T2.3 — ретайрмент 6 delegation-фасадов «for tests» (_cleanup_observability_containers,
##             _pull_module_images, _drain_completed_count, _drain_all_count, _invoke_healthcheck,
##             _invoke_healthcheck_full): 0 прод-потребителей (делегируют в parallel_runner /
##             observability / healthcheck_runner — реальные модули D1/E1); тесты перенацелены
##             на реальные модули; lint-suppressions (pyright reportUnusedFunction) удалены вместе
##
## @modulemap
##   _check_image_exists [W:1] — docker manifest inspect via subprocess → bool
##   _resolve_compose_file [W:1] — find compose.yaml → docker-compose.yaml → docker-compose.base.yml in module dir
##   _build_compose_args [W:2] — build docker compose arg list from env-files, overlay, --profile
##   deploy_docker_module [W:5] — deploy single docker module: build (if build:) + compose up -d
##   _cleanup_stale_container [W:1] — hermes-agent stale container cleanup
##   _read_init_services [W:1] — read declarative module.yaml#deploy.init_services (D8/P19)
##   _recreate_init_services [W:2] — force-recreate one-shot init-сервисов после up -d (D8/P19)
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
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast

import yaml

# ⚠️ TRAP[BUG] · 2026-08-05 · HI · Standalone-инвокация docker_orchestrator.py без PYTHONPATH → ModuleNotFoundError
# · Symptom: `env -i python3 docker_orchestrator.py --help` из cwd≠root падал на `from core.internal...`
# ·   (свежий бутстрап/ручной запуск без экспортированного PYTHONPATH — латентный класс A, DevPlan 136 W2 T2.1)
# · Root: sys.path-инъекция была только для build_cache (та же deploy/ директория), core.* импорты
# ·   требовали PYTHONPATH корня репо (deploy-modules.sh:43 экспортирует — но standalone-запуск нет).
# · Fix (170 W10-C): self-bootstrap корня репо (канон config_renderer.py:44-45) ВЫШЕ всех импортов —
# ·   включая пакетный импорт build_cache (core.internal.bootstrap.deploy.build_cache); sys.path-
# ·   инъекция deploy/ директории удалена (пакет deploy/ импортируется по корню репо).
# ·   Файл: core/internal/bootstrap/deploy/docker_orchestrator.py → корень = 5 уровней parent.
# · Prevention: standalone-инвокация любого core.*-модуля обязана иметь self-bootstrap корня.
# · DevPlan 136 W2 T2.1: тест env -i python3 docker_orchestrator.py → exit 0 (или осмысленная ошибка,
# ·   но НЕ ModuleNotFoundError по core.*).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Local imports (build_cache.py lives in same deploy/ directory; волна 118 B2:
#    content_hash.py переименован в build_cache.py — устранение коллизии имён с shared/content_hash.py;
#    170 W10-C: пакетный импорт через корень репо вместо sys.path-инъекции директории) ──
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
from core.internal.bootstrap.deploy.build_cache import (
    check_build_needed,
    compute_source_hash,
    save_build_hash,
)

# DevPlan 128 W1 (P2-5/D6): docker ps/stop/rm примитивы — shared/docker_ops
# (единственный слой, гейт docker_sole_path).
# DevPlan 081 Phase C (TASK-081C3): shared audit_logger for JSON-lines audit
# DRIFT-D6 resolved: unified JSON-lines audit format
# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared import docker_ops
from core.internal.shared.audit_logger import write_audit_entry as _shared_write_audit_entry
from core.internal.shared.compose_profiles import resolve_infra_path
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
from core.internal.shared.docker_compose import (
    docker_prebuild_pull as _shared_docker_prebuild_pull,
)

# DevPlan 116 B5 T1: таймауты — единый реестр shared/timeouts.py (U-11, гейт timeout_literals)
from core.internal.shared.timeouts import (
    BUILD_TIMEOUT,
    COMPOSE_UP_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
)

logger = logging.getLogger(__name__)

# pyright: reportUnusedParameter = false
# PHASES-контракт (E1): фазы обязаны иметь точные имена параметров (module_name, module_dir,
# compose_file, compose_args) — structural-тест их ассертит; ruff: ignore[ARG001] на неиспользуемых
# параметрах — тот же контракт (tool-conflict: pyright требует pyright-лидирующий комментарий,
# ruff — ruff-лидирующий).

# ── Constants ──
# DevPlan 118 D1: спец-workflow hermes перенесён в hermes_workflow.py.
# DevPlan 002 W2 T2.2: L1-механика (L1_BASE_IMAGE/GHCR_ORG/docker_tag) удалена из hermes_workflow.
# DevPlan 118 A2: единый канон списков compose-файлов — shared/compose_files.py (гейт
# compose_files_sole_path). Локальный COMPOSE_FILENAMES УДАЛЁН (6 копий → 1 SoT).
# DevPlan 170 W10-B: резолв compose-файла и сборка compose-args — leaf compose_args.py
# (вынос _resolve_compose_file/_build_compose_args; приватные алиасы U-07 — тесты
# dorch._resolve_compose_file/_build_compose_args и deploy_docker_module не меняются).
from core.internal.bootstrap.deploy.compose_args import build_compose_args as _build_compose_args
from core.internal.bootstrap.deploy.compose_args import resolve_compose_file as _resolve_compose_file

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


# region PROTOCOL_OrphanReconciler
class _OrphanReconcilerProto(Protocol):
    """Structural DI-контракт orphan-реконсиляции (167 D3) — модуль orphan_reconciler или fake тестов.

    ## @purpose  Типизация DI-параметра orphan_reconciler_impl (вместо Any): два метода,
    ##            используемых deploy_docker_module. Реализации: модуль orphan_reconciler
    ##            (прод) и fake-объекты тестов (structural-match).
    ## @io       ⇥ batch_orphan_reconciliation(module_entries, modules_dir) → list[dict[str, str]];
    ##            remove_orphans(orphans) → int
    ## @complexity O(1) — декларация протокола
    """

    def batch_orphan_reconciliation(self, module_entries: list[str], modules_dir: str) -> list[dict[str, str]]: ...

    def remove_orphans(self, orphans: list[dict[str, str]]) -> int: ...


# endregion PROTOCOL_OrphanReconciler


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
        msg = (
            "[IMP:10][docker_orchestrator] env_defaults.COMPOSE_PROFILES missing in platform-infra.yaml (SoT) — "
            "run `make generate-platform-env` (DevPlan 116 T2, U-02)."
        )
        raise KeyError(msg)
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


# region FUNC_ensure_nginx_overlay_env
## @purpose  Безусловный экспорт NGINX_OVERLAY_DIR до первого compose-вызова ЛЮБОГО модуля
##           (plan 012 T8 / F-015a). Цепочка значения: параметр overlay_dir (caller резолвит
##           из node.yaml#config_overlay/контекст-оверлеев) → существующий env → env_defaults
##           platform-infra.yaml (только непустой). Пустой результат НЕ экспортируется.
## @io       ⇥ overlay_dir: str | None, env: Mapping | None (DI) → ⎋ str (итоговое значение)
## @complexity O(1) (+1 yaml read при env_defaults-fallback)
## @invariants
##   - Экспорт выполняется для ЛЮБОГО module_name (nginx-гейт устранён — F-015)
##   - Вызов ДО фаз rebuild/up/hermes — интерполяция ${NGINX_OVERLAY_DIR:?} защищена на пути
##   - Явный param приоритетен (deploy-контракт per-node); setdefault для env-источника
def ensure_nginx_overlay_env(
    overlay_dir: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    source_env = os.environ if env is None else dict(env)

    value = (overlay_dir or "").strip()
    source = "param"
    if not value:
        existing = str(source_env.get("NGINX_OVERLAY_DIR", "")).strip()
        if existing:
            value, source = existing, "env"
    if not value:
        infra_path = resolve_infra_path(source_env)
        if infra_path is not None:
            try:
                with Path(infra_path).open(encoding="utf-8") as fh:
                    defaults = (yaml.safe_load(fh) or {}).get("env_defaults") or {}
            except OSError as exc:
                logger.warning("[IMP:7][ensure_nginx_overlay_env] Cannot read %s: %s", infra_path, exc)
                defaults = {}
            candidate = str(defaults.get("NGINX_OVERLAY_DIR", "")).strip()
            if candidate:
                value, source = candidate, "env_defaults"

    if not value:
        logger.info(
            "[IMP:8][ensure_nginx_overlay_env] No NGINX_OVERLAY_DIR source (param/env/env_defaults) — not exported"
        )
        return ""

    # param перезаписывает env (per-node авторитет); env/env_defaults — setdefault-семантика
    if source == "param" or not source_env.get("NGINX_OVERLAY_DIR"):
        os.environ["NGINX_OVERLAY_DIR"] = value
    logger.info("[IMP:9][ensure_nginx_overlay_env] NGINX_OVERLAY_DIR=%s (source=%s)", value, source)
    return value


# endregion FUNC_ensure_nginx_overlay_env


# region FUNC__handle_hermes_agent
## @purpose  Handle hermes-agent special case — DevPlan 118 D1: реализация вынесена в
##           hermes_workflow.handle_hermes_agent (спец-workflow). Тонкий фасад сохраняет
##           публичное имя для обратной совместимости (тесты, deploy_docker_module).
## @io       ⇥ compose_args: list[str], module_dir: str, module_name: str
##           ⎋ bool: True if images are ready or built, False on fatal failure
## @complexity 1 — delegate to hermes_workflow
## @invariants
##   - Вся логика (build-from-source fallback) — в hermes_workflow.py (D1; L1 коллапс DevPlan 002)
##   - Фасад не дублирует логику — только делегирование
def _handle_hermes_agent(compose_args: list[str], module_dir: str, module_name: str) -> bool:
    return hermes_workflow.handle_hermes_agent(compose_args, module_dir, module_name)


# endregion FUNC__handle_hermes_agent


# region FUNC_deploy_docker_module
## @purpose  Deploy a single Docker module via docker compose build (if build: section) + up -d --remove-orphans.
##           DevPlan 119 E1: монолит (195 LOC, CC=25) разбит на фазы с dispatch-таблицей
##           PHASES (hermes → hermes_workflow, observability → observability.py, rebuild/up → локально).
##           Оркестратор: resolve compose → build args → dispatch спец-фаз → orphan reconcile →
##           nginx overlay → rebuild → up → init-service force-recreate (D8/P19).
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
##   - One-shot init-сервисы (module.yaml#deploy.init_services) force-recreate при КАЖДОМ
##     прохождении модуля (D8/P19): exited one-shot контейнер не пересоздаётся при изменении
##     смонтированного шаблона (шаблон не меняет compose-конфиг → up no-op SKIP) — рекреация
##     идемпотентна (one-shot, секунды) и выполняется в общем финале ОБЕИХ веток (deployed/skip)
## @rationale Q: Why phase dispatch? A: E1 (DevPlan 119, AUDIT-2 M7) — deploy_docker_module CC=25,
##   13 if-веток. Разбиение по фазам + PHASES-таблица снижает CC до ≤10 и даёт изолированные
##   тесты фаз (test_phase_hermes_build / test_deploy_docker_module_phases_negative).
## @changes   2026-07-23 · Added docker compose build step for modules with build: section
##             (P0 bug: status-page showing old container after core-deploy)
##            2026-08-02 · DevPlan 119 E1 — phased decomposition (PHASES dispatch)
##            2026-08-14 · DevPlan 167 D3 — +orphan_reconciler_impl (DI-объект, 0 monkeypatch в тестах)
##            2026-08-26 · Plan 012 T8 (F-015a) — ensure_nginx_overlay_env: безусловный экспорт
##            NGINX_OVERLAY_DIR для любого модуля до первого compose-вызова
##            2026-09-01 · D8/P19 — после up -d: force-recreate декларированных one-shot init-сервисов
##            (module.yaml#deploy.init_services, monitoring: prometheus-config-init) с ТЕМ ЖЕ собранным
##            env (compose_args) — exited init-контейнер не пересоздаётся при изменении шаблона
##            2026-09-01 · D8/P19 follow-up — recreate вынесен в общий финал deploy_docker_module:
##            выполняется и на skip-пути (up-to-date, up no-op) — шаблонные изменения не меняют
##            compose-конфиг, рекреация — единственный канал распространения шаблона
def deploy_docker_module(
    module_name: str,
    overlay_dir: str | None = None,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    modules_dir: str | None = None,
    *,
    orphan_reconciler_impl: _OrphanReconcilerProto | None = None,
) -> bool:
    module_dir = modules_dir or str(Path(__file__).resolve().parent.parent.parent / "modules")
    logger.info("[IMP:7][deploy_docker_module][start] Deploying docker module: %s", module_name)

    # DevPlan 081 Phase C: audit deploy start (TASK-081C3)
    with contextlib.suppress(Exception):
        _ = _shared_write_audit_entry(
            tag=f"docker_orchestrator:deploy:{module_name}",
            status="START",
            message=f"Deploying docker module {module_name}",
        )

    # ── Resolve compose file ──
    compose_file = _resolve_compose_file(str(Path(module_dir) / module_name))
    if compose_file is None:
        logger.error(
            "[IMP:10][deploy_docker_module][no_compose] Compose file not found for %s in %s",
            module_name,
            Path(module_dir) / module_name,
        )
        with contextlib.suppress(Exception):
            _ = _shared_write_audit_entry(
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

    # ── NGINX overlay env (plan 012 T8 / F-015a): безусловно, ДО первого compose-вызова ──
    # Любой модуль на «голой» ноде получает интерполяцию ${NGINX_OVERLAY_DIR:?} — гейт
    # module_name == "nginx" устранён; цепочка param → env → env_defaults.
    ensure_nginx_overlay_env(overlay_dir)

    # ── PHASE DISPATCH (E1): спец-фазы по имени модуля ──
    phase_fn = PHASES.get(module_name)
    if phase_fn is not None:
        phase_ok = phase_fn(module_name, module_dir, compose_file, compose_args)
        if phase_ok is False:
            logger.error("[IMP:10][deploy_docker_module][phase_fail] Phase %s failed for %s", module_name, module_name)
            return False

    # ── COMPOSE_PROFILES for config --services calls ──
    # SoT: core/platform-infra.yaml env_defaults (DevPlan 116 T2, U-02). Explicit env
    # COMPOSE_PROFILES (from Makefile export / CI) takes precedence — setdefault semantics.
    _ = os.environ.setdefault("COMPOSE_PROFILES", _resolve_compose_profiles_from_infra())

    # ── Orphan container reconciliation ──
    # DevPlan 117 D18: делегирование в orphan_reconciler (единый канон). batch_orphan_reconciliation
    # работает per-module (один module_entries) и batch-путь (deploy_orchestrator) — batch-подход
    # эффективнее (один docker ps -a); remove_orphans удаляет найденные orphan-контейнеры.
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · orphan_reconciler через DI-объект (167 D3)
    # · Rejected: прямой module-level вызов orphan_reconciler.batch_orphan_reconciliation/remove_orphans
    # · Reason: seam = тестируемость реального orphan-вызова (тест передаёт fake-объект вместо
    # ·   monkeypatch.setattr(dorch.orphan_reconciler, ...); прод — module fallback без изменений)
    # · Rev: если orphan_reconciler станет классом-сервисом — impl станет его инстансом
    reconciler = orphan_reconciler_impl if orphan_reconciler_impl is not None else orphan_reconciler
    orphans = reconciler.batch_orphan_reconciliation([module_name], module_dir)
    if orphans:
        removed = reconciler.remove_orphans(orphans)
        logger.info("[IMP:9][deploy_docker_module][orphan] Removed %d orphan container(s) for %s", removed, module_name)

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
    up_ok = _phase_up(
        module_name=module_name,
        module_dir=module_dir,
        compose_args=compose_args,
        has_local_build=has_local_build,
    )

    # ── PHASE: init-service force-recreate (D8/P19) — общий финал ОБЕИХ веток ──
    # One-shot init-контейнеры (exited, restart:"no") НЕ пересоздаются `docker compose up -d` при
    # неизменном compose-конфиге: изменение смонтированного шаблона (prometheus.yml.tmpl) НЕ меняет
    # compose-конфиг → up уходит в no-op SKIP (deployed-путь не активен), exited init-контейнер не
    # пересоздаётся → generated-конфиг (named volume) остаётся stale. Рекреация выполняется при
    # КАЖДОМ прохождении модуля через deploy_docker_module (deployed И skip/up-to-date ветки) —
    # она идемпотентна (one-shot, секунды), а её результат гейтит return. Механика: декларативное
    # deploy.init_services в module.yaml + force-recreate с ТЕМ ЖЕ собранным env (compose_args, B23) —
    # ручной compose-вызов с ноды невозможен (интерполяция ${NGINX_OVERLAY_DIR:?}/секретов требует
    # env-ассемблинга deploy_docker_module).
    recreate_ok = _recreate_init_services(str(Path(module_dir) / module_name), compose_args)
    if not up_ok:
        return False
    return recreate_ok


# endregion FUNC_deploy_docker_module


# region FUNC__phase_hermes
## @purpose  E1 hermes-agent phase: stale container cleanup + pre-deploy image check/build.
##           Делегирует в hermes_workflow.handle_hermes_agent (D1). Возвращает False на
##           фатальном сбое (build/pull failure).
## @io       ⇥ module_name, module_dir, compose_file, compose_args → ⎋ bool (True = ready)
## @complexity 1 — delegation (stale cleanup + hermes_workflow)
## @invariants
##   - stale container "hermes-base-agent" clean-up перед hermes-проверками
##   - False → деплой abort (hermes-critical)
def _phase_hermes(
    module_name: str,
    module_dir: str,
    compose_file: Path,  # ruff: ignore[ARG001] — контракт PHASES-диспатча (keyword-вызовы, structural-тест)
    compose_args: list[str],
) -> bool:
    """E1 phase: hermes-agent stale cleanup + pre-deploy image check/build."""
    _cleanup_stale_container("hermes-base-agent")
    logger.info("[IMP:8][_phase_hermes][stale] stale container check done")
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
    module_name: str,  # ruff: ignore[ARG001] — контракт PHASES-диспатча (keyword-вызовы, structural-тест сигнатуры)
    module_dir: str,  # ruff: ignore[ARG001] — контракт PHASES-диспатча (keyword-вызовы, structural-тест сигнатуры)
    compose_file: Path,
    compose_args: list[str],  # ruff: ignore[ARG001] — контракт PHASES-диспатча (keyword-вызовы, structural-тест сигнатуры)
) -> bool:
    """E1 phase: observability pre-deploy container cleanup (best-effort)."""
    observability.cleanup_observability_containers(compose_file)
    return True


# endregion FUNC__phase_observability


# region FUNC__phase_rebuild
## @purpose  E1 rebuild phase: content-hash skip → pre-pull пинненных баз (F-03) → docker compose
##           build → save hash. Возвращает (ok, has_local_build): ok=False → deploy abort;
##           has_local_build → флаг для --force-recreate в up-фазе.
## @io       ⇥ module_name, module_dir, compose_file, compose_args
##           ⎋ tuple[bool, bool] — (success, has_local_build)
## @complexity 3 — build: detection + content-hash skip + pre-pull + build/save
## @invariants
##   - hermes-agent исключён (свой workflow в _phase_hermes)
##   - Content-hash skip: source unchanged → только up --force-recreate
##   - Pre-pull баз (docker_prebuild_pull) — перед каждым реальным build; best-effort:
##     провал НЕ абортит сборку (build — арбитр, образ может быть в локальном кеше)
##   - Сбой build → False (deploy abort)
def _phase_rebuild(
    module_name: str,
    module_dir: str,
    compose_file: Path,
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
            compose_content = compose_file.read_text(encoding="utf-8")
        except OSError:
            compose_content = ""
        if "build:" in compose_content:
            has_local_build = True

            # ── W3.T3.3: Content-hash skip — rebuild only if source changed ──
            # ⚠️ TRAP[BUG] · 2026-07-24 · P2 · check_build_needed receives modules root, not specific module
            # · Fix: use Path(module_dir) / module_name to target the specific module subdirectory
            build_needed = check_build_needed(str(Path(module_dir) / module_name))
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

            # ── F-03 (017-launch-validation P0): pre-pull пинненных баз ДО сборки ──
            # BuildKit НЕ ретраит docker-pull внутри build — первый массовый пул базовых
            # образов с docker.io на голой ноде транзиентно падает (троттлинг); pre-pull
            # с retry 5/15/45 (docker_prebuild_pull) детерминизирует холодный bootstrap.
            # Best-effort: провал pre-pull НЕ абортит сборку (база может быть уже в
            # локальном кеше — build остаётся арбитром; fail-fast был бы регрессией).
            if not _shared_docker_prebuild_pull(str(Path(module_dir) / module_name)):
                logger.error(
                    "[IMP:10][_phase_rebuild][prebuild_pull_fail] Pre-pull of base images failed for %s — build proceeds (may fail on pull)",
                    module_name,
                )
            logger.info("[IMP:8][_phase_rebuild][prebuild_pull] Base-image pre-pull finished for %s", module_name)

            # T4.3 (DevPlan 116 B5): shared docker_compose_build — sole path (timeout BUILD_TIMEOUT)
            if not _shared_docker_compose_build(
                str(Path(module_dir) / module_name),
                timeout=BUILD_TIMEOUT,
                compose_args=compose_args,
            ):
                logger.error("[IMP:10][_phase_rebuild][build_fail] docker compose build failed for %s", module_name)
                return False, has_local_build
            logger.info("[IMP:9][_phase_rebuild][build] Image rebuilt for %s", module_name)
            # Save hash after successful build (W3.T3.3) — бизнес-логика остаётся здесь
            # ⚠️ TRAP[BUG] · 2026-07-24 · P2 · compute_source_hash/save_build_hash receives modules root
            # · Fix: use Path(module_dir) / module_name for specific module subdirectory
            try:
                new_hash = compute_source_hash(str(Path(module_dir) / module_name))
                if new_hash:
                    save_build_hash(str(Path(module_dir) / module_name), new_hash)
            except (OSError, FileNotFoundError) as exc:
                logger.warning(
                    "[IMP:7][_phase_rebuild][hash] Failed to save build hash for %s: %s",
                    module_name,
                    exc,
                )
    return True, has_local_build


# endregion FUNC__phase_rebuild


# region FUNC__phase_up
## @purpose  E1 up phase: docker compose up -d [--force-recreate] + audit.
## @io       ⇥ module_name, module_dir, compose_args, has_local_build → ⎋ bool
## @complexity 1 — single shared docker_compose_up call + audit
## @invariants
##   - --force-recreate added for build:-modules (bypass same-tag no-op)
##   - Audit DEPLOYED/FAILED через shared audit_logger (D6)
##   - Различение TIMEOUT/ERROR/FAILED схлопывается в FAILED (детали в shared-логах)
##   - --remove-orphans НЕ передаётся (TRAP[BUG] 141 B18): при неполном активном наборе
##     профилей (COMPOSE_PROFILES env / --profile модуля) каскадно удаляет контейнеры
##     проекта вне config. Orphan-политика — только orphan_reconciler (точечный docker rm -f).
def _phase_up(
    module_name: str,
    module_dir: str,
    compose_args: list[str],
    has_local_build: bool,
) -> bool:
    """E1 phase: docker compose up -d [--force-recreate] + audit."""
    # · --force-recreate added for build:-modules to bypass Docker Compose's
    #   local-image-same-tag no-op (build creates new image under same tag,
    #   compose doesn't detect the change → container not recreated).
    # ⚠️ TRAP[BUG] · 2026-08-06 · HI · --remove-orphans удалён из модульного up (141 B18)
    # · Symptom: контейнеры модулей (project=platform, root compose) исчезали после
    # ·   node-update: up --profile <module> --remove-orphans при неполном COMPOSE_PROFILES
    # ·   считал контейнеры остальных модулей orphans и удалял их каскадно.
    # · Fix: orphan-управление только через orphan_reconciler (deploy_docker_module pre-up
    # ·   + deploy_orchestrator _postflight batch) — точечный docker rm -f с явным expected project.
    # · Rev: если у root compose появится name: — пересмотреть (project name в config "name").
    flags = ["--force-recreate"] if has_local_build else []
    logger.info(
        "[IMP:8][_phase_up][up] Running compose up for %s (flags=%s)",
        module_name,
        " ".join(flags),
    )
    # T4.4 (DevPlan 116 B5): shared docker_compose_up — sole path; audit DEPLOYED/FAILED (D6).
    if _shared_docker_compose_up(
        str(Path(module_dir) / module_name),
        timeout=COMPOSE_UP_TIMEOUT,
        compose_args=compose_args,
        flags=flags,
    ):
        logger.info("[IMP:9][_phase_up][done] Module deployed: %s", module_name)
        # DevPlan 081 Phase C: audit via shared audit_logger (TASK-081C3)
        with contextlib.suppress(Exception):
            _ = _shared_write_audit_entry(
                tag=f"docker_orchestrator:deploy:{module_name}",
                status="DEPLOYED",
                message=f"docker compose up succeeded for {module_name}",
            )
        time.sleep(1)
        return True
    logger.error("[IMP:10][_phase_up][up_fail] docker compose up failed for %s", module_name)
    # DevPlan 081 Phase C: audit deploy fail (TASK-081C3) — D6: FAILED (не TIMEOUT/ERROR)
    with contextlib.suppress(Exception):
        _ = _shared_write_audit_entry(
            tag=f"docker_orchestrator:deploy:{module_name}",
            status="FAILED",
            message=f"docker compose up failed for {module_name}",
        )
    return False


# endregion FUNC__phase_up


# region FUNC__read_init_services
## @purpose  Read declarative deploy.init_services from module.yaml — one-shot init-сервисы модуля
##           (D8/P19), которым нужен --force-recreate после compose up -d. Graceful-контракт:
##           отсутствие module.yaml/поля/битый YAML → [] (механика не ломает модули без init-сервисов).
## @io       ⇥ module_dir: str (директория модуля) → ⎋ list[str] (имена init-сервисов)
## @complexity O(1) — один yaml.safe_load
## @invariants
##   - Поле: module.yaml#deploy.init_services (list[str]); отсутствует → [] (backward-compat)
##   - Битый YAML → warning + [] (non-fatal — прежний деплой-путь сохраняется)
##   - Не-str элементы списка отбрасываются (schema-валидация ловит на validate-modules)
def _read_init_services(module_dir: str) -> list[str]:
    module_yaml = Path(module_dir) / "module.yaml"
    if not module_yaml.is_file():
        return []
    try:
        data = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("[IMP:7][_read_init_services][yaml] Cannot parse %s: %s", module_yaml, exc)
        return []
    if not isinstance(data, dict):
        return []
    deploy_cfg = data.get("deploy")
    if not isinstance(deploy_cfg, dict):
        return []
    init_services = deploy_cfg.get("init_services")
    if not isinstance(init_services, list):
        return []
    services = [str(svc) for svc in init_services if isinstance(svc, str) and svc]
    logger.info(
        "[IMP:8][_read_init_services][found] %d init service(s) for %s: %s",
        len(services),
        Path(module_dir).name,
        services,
    )
    return services


# endregion FUNC__read_init_services


# region FUNC__recreate_init_services
## @purpose  Force-recreate one-shot init-сервисов модуля (D8/P19): на КАЖДОМ прохождении модуля
##           через deploy_docker_module перезапускает декларированные init-сервисы с
##           --force-recreate и ТЕМ ЖЕ собранным env (compose_args) — ручной compose-вызов с ноды
##           невозможен (интерполяция ${NGINX_OVERLAY_DIR:?}/секретов требует env-ассемблинга
##           deploy_docker_module, B23). Без force-recreate exited one-shot контейнер не
##           пересоздаётся при изменении смонтированного шаблона → generated-конфиг (named
##           volume) остаётся stale; up no-op (неизменный compose-конфиг) — НЕ причина
##           пропускать рекреацию.
## @io       ⇥ module_dir: str (директория модуля), compose_args: list[str] (собранный env)
##           ⎋ bool: True если нет init-сервисов ИЛИ все force-recreate успешны
## @complexity O(S) — S init-сервисов × docker compose up -d --force-recreate
## @invariants
##   - Выполняется при КАЖДОМ прохождении модуля через deploy_docker_module (deployed И
##     skip/up-to-date ветки): шаблонные изменения (prometheus.yml.tmpl) не меняют compose-конфиг
##     → up no-op; рекреация — единственный канал распространения шаблона; идемпотентна
##     (one-shot, секунды), деплой-результат гейтится её исходом
##   - Переиспользует shared docker_compose_up (service+flags) — docker_sole_path соблюдён
##   - Провал force-recreate → False (deploy FAILED) — stale config хуже деплой-ошибки
##   - compose_args несёт --profile module (init-сервисы под профилем) + --env-file список
def _recreate_init_services(module_dir: str, compose_args: list[str]) -> bool:
    init_services = _read_init_services(module_dir)
    if not init_services:
        return True
    # ⚠️ TRAP[BUG] · 2026-09-01 · P1 · one-shot init-контейнер не пересоздаётся при изменении шаблона
    # · Symptom: prometheus-config-init (exited, restart:"no") — `docker compose up -d` НЕ пересоздаёт
    # ·   exited one-shot контейнер при изменении смонтированного шаблона → generated prometheus.yml
    # ·   (volume prometheus-config-gen) остаётся со СТАРЫМ шаблоном (static status-page job → вечный
    # ·   DOWN-таргет; live-нода tronyx-vps, E2 валидация).
    # · Root: P19-класс бага — конфиг-монтаж без --force-recreate; compose детектит изменение
    # ·   конфига только для RUNNING контейнеров, exited one-shot не входит в up-diff.
    # · Fix: декларативное deploy.init_services в module.yaml + generic force-recreate ПОСЛЕ up -d
    # ·   с тем же собранным env (compose_args, B23) — ручной `compose up --force-recreate <init>`
    # ·   с ноды падал на интерполяции (NGINX_OVERLAY_DIR/S3_ACCESS_KEY required, env собирает deploy).
    # · Prevention: init-сервисы декларируются в module.yaml (deploy.init_services); рекреация —
    # ·   часть deploy-пути docker-модулей; гейт restart-policies требует restart:"no" для init.
    # · Rev выполнен 2026-09-01: skip-путь тоже рекреитт — шаблонные изменения не меняют
    # ·   compose-конфиг, up уходит в no-op SKIP; рекреация вынесена в общий финал
    # ·   deploy_docker_module (обе ветки: deployed и skip/up-to-date).
    # · Rev (актуальный): prometheus читает --config.file при СТАРТЕ (без --web.enable-lifecycle) —
    # ·   init-рекреация гарантирует свежий generated-конфиг в volume; применение требует
    # ·   рестарта/пересоздания самого prometheus (покрывается следующим up/restart циклом
    # ·   модуля или converge).
    for svc in init_services:
        logger.info("[IMP:8][_recreate_init_services][recreate] Force-recreating init service: %s", svc)
        if not _shared_docker_compose_up(
            module_dir,
            timeout=COMPOSE_UP_TIMEOUT,
            compose_args=compose_args,
            service=svc,
            flags=["--force-recreate"],
        ):
            logger.error("[IMP:10][_recreate_init_services][fail] Force-recreate failed for init service %s", svc)
            return False
        logger.info("[IMP:9][_recreate_init_services][done] Init service recreated: %s", svc)
    return True


# endregion FUNC__recreate_init_services


# region PHASES_DISPATCH
## @purpose  DevPlan 119 E1: dispatch-таблица спец-фаз deploy_docker_module по имени модуля.
##           Фазы-кандидаты: hermes-agent (stale cleanup + image check/build) и observability
##           (pre-deploy container cleanup). Каждая фаза: (module_name, module_dir, compose_file,
##           compose_args) -> bool. False → abort деплоя (hermes-critical). rebuild/up фазы
##           вызываются отдельно (общие для всех модулей, см. deploy_docker_module).
## @invariants
##   - Добавление новой спец-фазы = регистрация здесь + определение функции
##   - Порядок dispatch: PHASES проверяется ПОСЛЕ compose args, ДО orphan/rebuild/up
PHASES: dict[str, Callable[..., bool]] = {
    "hermes-agent": _phase_hermes,
    "observability": _phase_observability,
}


# endregion PHASES_DISPATCH


# region FUNC__cleanup_stale_container
## @purpose  Stop and remove a stale container by name (used for hermes-agent migration).
## @io       ⇥ container_name: str
##           ⎋ None (side-effect: docker stop + rm)
## @complexity 1 — два docker-вызова (ps/stop/rm) через shared/docker_ops (W1, non-fatal)
def _cleanup_stale_container(container_name: str) -> None:
    logger.info("[IMP:7][_cleanup_stale_container][check] Checking for stale container: %s", container_name)
    # W1 (DevPlan 128): примитивы docker ps/stop/rm — shared/docker_ops (non-fatal, bytes→str
    # нормализация внутри; TRAP[BUG] type-safety 2026-07-22 снят shared-слоем).
    names = docker_ops.ps_container_names(all=True, timeout=DOCKER_CMD_TIMEOUT)
    if container_name in names:
        logger.info("[IMP:8][_cleanup_stale_container][stop] Stopping stale container: %s", container_name)
        _ = docker_ops.docker_stop(container_name)
        _ = docker_ops.docker_rm(container_name)
        logger.info("[IMP:9][_cleanup_stale_container][removed] stale container removed: %s", container_name)


# endregion FUNC__cleanup_stale_container


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
##           170 W10-B: инъекция deploy_module_fn=deploy_docker_module (DI-seam — parallel_runner
##           не импортирует docker_orchestrator; цикл разорван).
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
        deploy_module_fn=deploy_docker_module,
    )


# endregion FUNC_deploy_docker_group


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


# region FUNC_main
class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    action: str
    module_name: str | None
    module_entries: list[str] | None
    modules_dir: str | None
    secrets_env_file: str | None
    platform_root: str | None
    overlay_dir: str | None
    image_ref: str | None
    max_attempts: int
    install_type: str
    parallel_limit: int


## @purpose  CLI entry point: dispatch to action handlers based on --action flag.
## @io       sys.argv → stdout/logs, int exit code
## @complexity 2 — argparse dispatch with per-action argument validation
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Docker orchestration: deploy, pre-pull, deploy-group, wait, healthcheck, check-image"
    )
    _ = parser.add_argument(
        "--action",
        required=True,
        choices=["deploy", "pre-pull", "deploy-group", "wait", "healthcheck", "check-image"],
        help="Action to perform",
    )
    _ = parser.add_argument("--module-name", help="Module name (for deploy, wait, healthcheck)")
    _ = parser.add_argument(
        "--module-entries",
        nargs="*",
        default=None,  # 170 W1-A2: None вместо [] (ловушка mutable default в argparse; None → `or []` при чтении)
        help="Module entries in module:overlay format (for pre-pull, deploy-group)",
    )
    _ = parser.add_argument("--modules-dir", help="Path to modules directory")
    _ = parser.add_argument("--secrets-env-file", help="Path to secrets.env file")
    _ = parser.add_argument(
        "--platform-root",
        help=f"Platform root directory (default: {platform_remote_base()})",
    )
    _ = parser.add_argument(
        "--overlay-dir",
        help="Overlay directory for context-specific compose override",
    )
    _ = parser.add_argument(
        "--image-ref",
        help="Image reference for check-image action",
    )
    _ = parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_READINESS_MAX_ATTEMPTS,
        help="Max attempts for readiness check (default: 15)",
    )
    _ = parser.add_argument(
        "--install-type",
        default="docker",
        choices=["docker", "system"],
        help="Install type for healthcheck (default: docker)",
    )
    _ = parser.add_argument(
        "--parallel-limit",
        type=int,
        default=DEFAULT_PARALLEL_LIMIT,
        help="Parallel deploy/pull limit (default: 4)",
    )

    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args()))

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
            entries=list(args.module_entries or []),
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
            entries=list(args.module_entries or []),
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
