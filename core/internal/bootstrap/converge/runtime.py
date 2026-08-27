#!/usr/bin/env python3
# GREP_SUMMARY: converge-runtime, reconcile-runtime-state, r9, container-state, compose-up, compose-down, self-heal, disabled-flow, cooldown, compose-project-label, compose-defined-containers, name-fallback, build-compose-args
# STRUCTURE: ▶ docker info → ▶ DISABLED-FLOW (enabled:false модули: ⚡ resolve_container_name → ◇ найдены? → ⚡ compose down <service> БЕЗ -v) → ◇ global cooldown (last_healed < 3 runs)? → ○ for each enabled docker module: ⚡ resolve_container_name [label=com.docker.compose.project=<module> → fallback: compose_defined_containers + docker ps --filter name=] → ⚡ get_container_state → ◇ in BAD_DOCKER_STATES? → ⚡ build_compose_args (root-first/env-file/profile) + compose up -d (shared, COMPOSE_UP_TIMEOUT) → ⊕ cooldown record → ⎋ drift entry {R9}
# region MODULE_CONTRACT
## @purpose  R9 reconcile_runtime_state — Docker container state check + compose up -d self-heal +
##           cooldown tracking (flapping-защита) + DISABLED-FLOW (Phase E 017): модули с
##           enabled:false в node.yaml останавливаются каноническим compose down (без -v).
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/runtime.py: reconcile_runtime_state, resolve_container_name, get_container_state,
##           load_cooldown, save_cooldown. Вызывается оркестратором reconciler.py.
## @invariants
##   - Self-heal ТОЛЬКО через docker compose up -d (shared/docker_compose, B5 T6/D8) — НЕ docker restart
##   - R9 argv — канонический build_compose_args (bootstrap/deploy/compose_args): root-compose-first,
##     --env-file secrets/platform .env, --profile module. REF-0014/BUG-0701: голый `-f base.yml`
##     ломал каждый docker-модуль (undefined volume / missing ${VAR:?} — 3 режима отказа живьём)
##   - Детекция контейнеров модуля — PRIMARY label=com.docker.compose.project=`<module>` (REF-0014:
##     substring name=monitoring давал 0 рядов; name=redis матчил langfuse-redis/redis-exporter);
##     FALLBACK (Phase E 017, F-017): label-miss → канонические имена из compose config
##     (compose_defined_containers, U-49 root-first) → docker ps -a --filter name=`<canonical>`
##     с пост-фильтром ТОЧНОГО совпадения. U-49 деплой даёт ВСЕМ контейнерам project='platform'
##     — label-запрос модуля слеп; fallback возвращает контейнеры модуля по имени.
##   - DISABLED-FLOW (Phase E 017): enabled:false docker-модуль с НАЙДЕННЫМИ контейнерами →
##     docker compose down `<service>` (build_compose_args + --profile, БЕЗ -v — volumes не трогаются;
##     O7/DD10) per service; контейнеров нет → converged (no-op). Выполняется ДО cooldown-шортката:
##     stop отключённого модуля — детерминированная коррекция дрифта (не heal), не обязан ждать
##     cooldown чужого heal (TRAP[DECISION] ниже). Cooldown-машинерия НЕ изменена.
##   - Cooldown: контейнер, вылеченный в течение 3 последних run'ов → global cooldown (skip healing)
##   - BAD_DOCKER_STATES: exited/restarting/dead/unhealthy/paused
##   - Runbook scheduled converge: автоматического таймера НЕТ (FAIL-0900; systemd timer —
##     отдельное решение) — самолечение по расписанию = ручной `make converge NODE=<node>`
##     (host-cron оператора); watchdog (*/5 cron) лечит только unhealthy-рестарты в этом окне
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям. Phase E 017: живая нода
##            показала 2 дефекта R9 — (1) label=project=`<module>` слеп на U-49 (все project=platform)
##            → «No running containers»/self-heal вслепую; (2) enabled:false не останавливал
##            работающий контейнер (дрифт не обнаружен). Fix: name-fallback + disabled-flow.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2)
##           2026-08-24 · REF-0014 (DevPlan meta-refactoring В1) — label-детекция проекта вместо
##           substring name=; argv через канонический build_compose_args (паритет с deploy/R7)
##           2026-08-27 · Phase E 017 (F-017) — name-fallback резолва контейнеров (label-miss на
##           U-49) + DISABLED-FLOW (compose down БЕЗ -v для enabled:false модулей)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

from core.internal.bootstrap.converge.infra import (
    BAD_DOCKER_STATES,
    COOLDOWN_FILE,
    report_add,
    set_exit,
)
from core.internal.bootstrap.converge.volumes import parse_node_modules_yaml

# REF-0014 (BUG-0701): канон compose-argv — leaf bootstrap/deploy/compose_args (bootstrap → deploy
# направление легально: bootstrap оркестрирует деплой; leaf импортирует только shared — без циклов)
# Phase E 017: compose_defined_containers/services — канонические имена из compose config
# (name-fallback резолва + сервисный путь disabled down).
from core.internal.bootstrap.deploy.compose_args import (
    build_compose_args,
    compose_defined_containers,
    compose_defined_services,
)
from core.internal.shared import docker_ops  # W1: docker ps/inspect/info примитивы (гейт docker_sole_path)
from core.internal.shared.compose_files import resolve_compose_file
from core.internal.shared.docker_compose import docker_compose_down as _shared_docker_compose_down
from core.internal.shared.docker_compose import docker_compose_up as _shared_docker_compose_up
from core.internal.shared.timeouts import (
    COMPOSE_UP_TIMEOUT,
    DOCKER_STOP_TIMEOUT,  # C4 канон: compose down --timeout (NO -v)
)

# R9-канон таймаута — прямой импорт из shared SoT (pyright reportPrivateLocalImportUsage)
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT as DOCKER_TIMEOUT

# Тип cooldown-файла (W11): {"run": int, "containers": {module: {"last_healed_run": int}}}
CooldownData = dict[str, int | dict[str, dict[str, int]]]

logger = logging.getLogger(__name__)


# region FUNC_resolve_container_name
## @purpose  Get container name(s) of a module via docker ps -a. PRIMARY: label
##           com.docker.compose.project=`<module>` (обратная совместимость REF-0014);
##           FALLBACK (Phase E 017, compose_file задан И label дал 0 рядов): канонические
##           имена из compose config (compose_defined_containers, U-49 root-first) →
##           docker ps -a --filter name=`<canonical>` с пост-фильтром ТОЧНОГО совпадения.
## ⚠️ TRAP[BUG] · 2026-08-06 · HI · B22 (141 r2): docker ps (без -a) не видел Exited/Created →
## ·   R9 self-heal мёртв (BAD-состояния не детектировались, converge «FULLY CONVERGED» при мёртвых nginx).
## · Fix: all=True — docker ps -a (Exited/dead/created видимы) → get_container_state → compose up -d.
## ⚠️ TRAP[BUG] · 2026-08-24 · HI · REF-0014 (BUG-0701): substring-фильтр name=`module` слеп и лжив:
## · Symptom: name=monitoring → 0 рядов (контейнеры называются иначе); name=redis → матчит
## ·   langfuse-redis/redis-exporter (чужие контейнеры) — R9 не детектировал и не лечил целевой проект.
## · Fix: точная детекция проекта — label=com.docker.compose.project=`module` (compose ставит label
## ·   каждому контейнеру проекта; module name == compose project name).
## · Prevention: test_reconciler_r9_runtime.py::test_r9_detects_module_by_compose_project_label
## ⚠️ TRAP[BUG] · 2026-08-27 · P1 · Phase E 017 (F-017): label=project=`<module>` слеп на U-49-нодах
## · Symptom: канон деплоя U-49 (root compose) даёт ВСЕМ контейнерам label project='platform' —
## ·   label-запрос каждого модуля возвращал 0 рядов → «No running containers» / self-heal вслепую.
## · Fix: FALLBACK по каноническим именам из compose config (compose_defined_containers) +
## ·   docker ps --filter name=`<canonical>` (НЕ label); пост-фильтр точного совпадения защищает
## ·   от substring-коллизий (status-page vs status-page-test) — BUG-0701-класс.
## · Prevention: test_r9_fallback_name_detection_selfheal / test_r9_disabled_module_*
## @param module_name  Module name (= compose project name)
## @param compose_file Path к compose-файлу модуля (для name-fallback); None → только label-путь
## @param cache        Caller-кэш compose-интроспекции {module_dir: {service: container_name}}
## @return  list[str] контейнеров проекта (включая не-running) | None = docker ps rc≠0
##          (QA R4/T2.D: None ≠ [] — пустой список при успехе ps значит «проекта нет на ноде»,
##          None значит «runtime-факт недоказуем»; caller обязан различать)
_COOLDOWN_RUNS: int = 3  # глобальный cooldown: heal в последних 3 прогонах


def resolve_container_name(
    module_name: str,
    compose_file: str | Path | None = None,
    *,
    cache: dict[str, dict[str, str]] | None = None,
) -> list[str] | None:
    """Resolve container names for a module: label-query primary, compose-config name fallback."""
    # W1: docker ps -a — shared/docker_ops (non-fatal); all=True: Exited/Created/restarting видимы (B22);
    # REF-0014: label=com.docker.compose.project=<module> — точная детекция проекта вместо substring
    ps_r = docker_ops.docker_ps(
        filters=[f"label=com.docker.compose.project={module_name}"],
        format="{{.Names}}",
        timeout=DOCKER_TIMEOUT,
        all=True,
    )
    if ps_r.returncode != 0:
        # QA R4/T2.D: rc≠0 → runtime UNVERIFIED (None), а не ложное «проекта нет» ([]):
        # прежнее [] схлопывалось в converged при транзиентном сбое docker ps.
        logger.warning(
            "[IMP:9][resolve_container_name] docker ps failed for module %s — runtime UNVERIFIED", module_name
        )
        return None
    containers = [c.strip() for c in ps_r.stdout.splitlines() if c.strip()]
    if containers or compose_file is None:
        logger.info("[IMP:7][resolve_container_name] Module %s → containers (label): %s", module_name, containers)
        return containers

    # ── FALLBACK (Phase E 017): label-miss (U-49: все контейнеры project=platform) → канонические
    # ── имена из compose config + docker ps --filter name=<canonical> (НЕ label).
    canonical = compose_defined_containers(Path(compose_file).parent, cache=cache)
    if not canonical:
        logger.info(
            "[IMP:8][resolve_container_name] Module %s — label-miss AND no canonical names from compose config",
            module_name,
        )
        return containers  # [] — проекта нет ИЛИ config нерезолвим (graceful, не UNVERIFIED)
    found: list[str] = []
    for cname in canonical:
        ps_name_r = docker_ops.docker_ps(
            filters=[f"name={cname}"],
            format="{{.Names}}",
            timeout=DOCKER_TIMEOUT,
            all=True,
        )
        if ps_name_r.returncode != 0:
            # R4/T2.D: сбой доп. запроса → runtime-факт недоказуем (fail-closed, не converged)
            logger.warning(
                "[IMP:9][resolve_container_name] name-fallback docker ps failed for %s (%s) — runtime UNVERIFIED",
                module_name,
                cname,
            )
            return None
        for row in ps_name_r.stdout.splitlines():
            name = row.strip().lstrip("/")
            # BUG-0701-класс: substring name= матчит чужие (status-page-test/langfuse-redis) —
            # пост-фильтр ТОЧНОГО совпадения с каноническим именем.
            if name == cname and name not in found:
                found.append(name)
    logger.info(
        "[IMP:7][resolve_container_name] Module %s → containers (name-fallback): %s (canonical=%s)",
        module_name,
        found,
        canonical,
    )
    return found


# endregion FUNC_resolve_container_name


# region FUNC_get_container_state
## @purpose  Get Docker container state via docker inspect.
## @param container_name  Container name to inspect
## @return  State string (e.g. "running", "exited"). "unknown" on failure.
def get_container_state(container_name: str) -> str:
    """Get container state via docker inspect --format '{{.State.Status}}'."""
    # W1: docker inspect — shared/docker_ops (non-fatal)
    inspect_r = docker_ops.docker_inspect(
        container_name,
        format="{{.State.Status}}",
        timeout=DOCKER_TIMEOUT,
    )
    if inspect_r.returncode != 0:
        logger.warning("[IMP:8][get_container_state] docker inspect failed for %s", container_name)
        return "unknown"
    state = inspect_r.stdout.strip()
    logger.info("[IMP:7][get_container_state] Container %s → state=%s", container_name, state)
    return state


# endregion FUNC_get_container_state


# region FUNC_get_container_restart_policy
## @purpose  142 B28a: restart-политика контейнера — отличие exited-oneshot (RestartPolicy=no)
##           от упавшего сервиса (unless-stopped). Возвращает "unknown" при ошибке inspect.
## @io       container_name → docker inspect HostConfig.RestartPolicy.Name → ⎋ str
## @complexity O(1) — один docker inspect
## @invariants — non-fatal: ошибка inspect → "unknown" (не блокирует R9)
def get_container_restart_policy(container_name: str) -> str:
    """Get container RestartPolicy.Name via docker inspect (142 B28a oneshot-guard)."""
    inspect_r = docker_ops.docker_inspect(
        container_name,
        format="{{.HostConfig.RestartPolicy.Name}}",
        timeout=DOCKER_TIMEOUT,
    )
    if inspect_r.returncode != 0:
        logger.warning("[IMP:8][get_container_restart_policy] docker inspect failed for %s", container_name)
        return "unknown"
    policy = inspect_r.stdout.strip()
    logger.info("[IMP:7][get_container_restart_policy] Container %s → restart_policy=%s", container_name, policy)
    return policy


# endregion FUNC_get_container_restart_policy


# region FUNC_load_cooldown
## @purpose  Load cooldown tracking data from JSON file.
## @return  Dict with structure: {"run": int, "containers": {name: {"last_healed_run": int}}}
def load_cooldown(cooldown_file: str | None = None) -> CooldownData:
    """Load cooldown tracking data from COOLDOWN_FILE.

    DI (W-H DevPlan 163): cooldown_file=None → COOLDOWN_FILE (канон); тесты передают tmp_path.
    Returns default structure if file is missing or corrupted.
    """
    filepath = Path(COOLDOWN_FILE if cooldown_file is None else cooldown_file)
    if filepath.is_file():
        try:
            # W11: json.loads → Any — каст к object, isinstance-гейт сохраняется
            data = cast(object, json.loads(filepath.read_text(encoding="utf-8")))
            if isinstance(data, dict):
                # W11: json.loads → Any — каст к структуре cooldown-файла
                return cast(CooldownData, data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[IMP:8][load_cooldown] Failed to read cooldown file: %s", exc)
    return {"run": 0, "containers": {}}


# endregion FUNC_load_cooldown


# region FUNC_save_cooldown
## @purpose  Save cooldown tracking data to JSON file.
## @param data  Dict with run counter and container cooldown entries
## @param cooldown_file  Переопределение пути cooldown-файла (DI, DevPlan 163 W-H); None → COOLDOWN_FILE
def save_cooldown(data: CooldownData, cooldown_file: str | None = None) -> None:
    """Save cooldown tracking data to COOLDOWN_FILE."""
    filepath = Path(COOLDOWN_FILE if cooldown_file is None else cooldown_file)
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        _ = filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[IMP:8][save_cooldown] Cooldown saved to %s", filepath)
    except OSError as exc:
        logger.warning("[IMP:8][save_cooldown] Failed to save cooldown: %s", exc)


# endregion FUNC_save_cooldown


# region FUNC__count_unlabeled_containers
## @purpose  QA R4/T2.D legacy-guard: однократный за прогон допрос всех контейнеров ноды
##           с label-колонкой — подсчёт контейнеров БЕЗ compose-label. Phase E 017: R9 видит
##           модульные контейнеры по label И каноническому имени (name-fallback); unlabeled
##           вне канона имён остаются невидимыми — warn сохраняется для честного вердикта.
## @io       ⇥ cache: dict-состояние прогона ("scanned"/"count"), unit → ⎋ int (unlabeled)
## @invariants  Один docker ps -a на прогон (кэш в cache-dict); rc≠0 → 0 (диагностика
##              не должна маскировать основной UNVERIFIED-канал); найденные unlabeled →
##              report-warn + set_exit(1) — «FULLY CONVERGED» при невидимых контейнерах ложь.
def _count_unlabeled_containers(cache: dict[str, object], unit: str) -> int:
    """Count containers without compose-label (one diagnostic query per run)."""
    if cache.get("scanned"):
        return cast(int, cache.get("count", 0))
    cache["scanned"] = True
    cache["count"] = 0
    diag_r = docker_ops.docker_ps(
        filters=[],
        format='{{.Names}}\t{{.Label "com.docker.compose.project"}}',
        timeout=DOCKER_TIMEOUT,
        all=True,
    )
    if diag_r.returncode != 0:
        return 0
    rows = [line for line in diag_r.stdout.splitlines() if line.strip()]
    unlabeled = sum(1 for line in rows if not line.rsplit("\t", 1)[-1].strip())
    cache["count"] = unlabeled
    if unlabeled:
        logger.warning(
            "[IMP:9][converge][%s] %d container(s) WITHOUT compose-label on node — "
            "R9 cannot see them (label+canonical-name detection)",
            unit,
            unlabeled,
        )
        report_add(unit, "warn", f"{unlabeled} container(s) without compose-label, R9 cannot see them")
        set_exit(1)
    return unlabeled


# endregion FUNC__count_unlabeled_containers


# region FUNC_reconcile_runtime_state
## @purpose  Reconcile Docker container runtime state. For each enabled docker module,
##           inspect container state. If state is bad (exited, restarting, dead,
##           unhealthy, paused), self-heal via `docker compose up -d`. Cooldown
##           tracking prevents repeated self-heal of flapping containers.
##           Phase E 017: disabled (enabled:false) docker-модули с живыми контейнерами
##           останавливаются каноническим `docker compose down <service>` (без -v).
## @complexity O(N×C) — N=modules, C=containers per module
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: docker compose up -d (self-heal), docker compose down (disabled-flow),
##           cooldown file update
## @param node_yaml_path  Path to node.yaml
## @param modules_dir     Path to modules/ directory
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @param cooldown_file   Override cooldown-file path (DI, DevPlan 163 W-H); None → COOLDOWN_FILE
## @return  Drift report entry dict
## @edge-cases
##   - Docker daemon unavailable → status=fail
##   - All containers running → status=converged
##   - Container exited → self-heal via docker compose up -d (NOT docker restart)
##   - Container in cooldown (healed within last 3 runs) → skip self-heal
##   - Disabled module (enabled:false) с живыми контейнерами → compose down `<service>` БЕЗ -v
##   - Disabled module без контейнеров → converged (no-op)
##   - Cooldown активен + disabled-stop произошёл → status=mutated (cooldown не маскирует)
def reconcile_runtime_state(
    node_yaml_path: str,
    modules_dir: str,
    cooldown_file: str | None = None,
    *,
    # QA-гигиена (T2.D-волна): булевы параметры — kw-only (FBT001/FBT002)
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile Docker container runtime state — self-heal via compose up -d.

    Returns a drift entry dict with status: ok|skipped|converged|mutated|warn|fail.
    """
    unit = "R9"
    logger.info("[IMP:8][converge][%s] START: reconcile_runtime_state — checking container states", unit)

    # ── Check docker daemon (W1: docker info — shared/docker_ops) ──
    docker_info_r = docker_ops.docker_info(timeout=DOCKER_TIMEOUT)
    if docker_info_r.returncode != 0:
        msg = "Docker daemon not available — skipping runtime reconciliation"
        logger.error("[IMP:10][converge][%s] FAIL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse modules from node.yaml ──
    modules = parse_node_modules_yaml(node_yaml_path)
    if not modules:
        logger.info("[IMP:9][converge][%s] SKIP: No modules defined in node.yaml", unit)
        report_add(unit, "skipped", "No modules defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No modules defined in node.yaml"}

    # ── Load cooldown data ──
    cooldown = load_cooldown(cooldown_file=cooldown_file)
    # W11: cooldown.get → int | dict — каст к счётчику run
    current_run = cast(int, cooldown.get("run", 0)) + 1
    cooldown["run"] = current_run
    if "containers" not in cooldown:
        cooldown["containers"] = {}
    # W11: containers — всегда dict (инициализирован выше) — каст из union int|dict
    containers_map = cast(dict[str, dict[str, int]], cooldown["containers"])

    # ── Check for global cooldown (any container healed in last 3 runs) ──
    global_cooldown = False
    for cname, cdata in containers_map.items():
        last_healed = cdata.get("last_healed_run", 0)
        if last_healed > 0 and current_run - last_healed < _COOLDOWN_RUNS:
            global_cooldown = True
            logger.info(
                "[IMP:7][converge][%s] Global cooldown active — %s healed at run %d (diff=%d < 3)",
                unit,
                cname,
                last_healed,
                current_run - last_healed,
            )
            break

    if global_cooldown:
        logger.info(
            "[IMP:9][converge][%s] COOLDOWN: Previously healed containers still in cooldown — skipping all healing",
            unit,
        )
        report_add(unit, "converged", "In cooldown — previously healed containers")
        return {"unit": unit, "status": "converged", "detail": "Cooldown active, no healing"}

    modules_dir_path = Path(modules_dir)
    healed = 0
    stopped = 0  # Phase E 017: disabled-flow compose down
    errors = 0
    # QA R4/T2.D: счётчик недоказанных runtime-фактов (docker ps rc≠0) — WARN-класс,
    # exit 2 зарезервирован за доказанным провалом heal/down.
    ps_unverified = 0
    unlabeled_cache: dict[str, object] = {}
    # Phase E 017: per-run кэш compose-интроспекции {module_dir: {service: container_name}} —
    # label-miss fallback и disabled down делят ОДИН docker compose config на модуль.
    compose_cache: dict[str, dict[str, str]] = {}

    # ── DISABLED-FLOW (Phase E 017 / F-017) ─────────────────────────────────────
    # enabled:false docker-модуль с НАЙДЕННЫМИ контейнерами → канонический compose down
    # (build_compose_args root-first + --profile, БЕЗ -v — volumes не трогаются, O7/DD10)
    # per service. Контейнеров нет → converged (no-op). Выполняется ДО global-cooldown
    # шортката: stop отключённого модуля — детерминированная коррекция дрифта (НЕ heal)
    # и не обязан ждать cooldown heal другого модуля; cooldown-машинерия не изменена.
    # 🧐 TRAP[DECISION] · 2026-08-27 · — · DISABLED-FLOW до cooldown-шортката (Phase E 017)
    # · Rejected: внутри heal-цикла после cooldown-check (stop ждал бы чужой cooldown до 3 run'ов)
    # · Reason: enabled:false — явная команда оператора, дрифт должен гаситься на КАЖДОМ converge;
    # ·   cooldown защищает от флапа heal (up/down), не от детерминированного desealing
    # · Rev: если понадобится cooldown-защита от циклических disable/enable — ввести отдельный счётчик
    for mod in modules:
        # W11: parse_node_modules_yaml → dict[str, object] — каст строкового поля
        mod_name = cast(str, mod.get("name", ""))
        if not mod_name or mod.get("enabled", True):
            continue  # только disabled-модули (enabled обрабатываются heal-циклом ниже)

        mod_dir = modules_dir_path / mod_name
        compose_file = resolve_compose_file(str(mod_dir))
        if not compose_file:
            logger.info(
                "[IMP:7][converge][%s] Disabled module %s has no compose file — nothing to stop", unit, mod_name
            )
            continue

        # Та же детекция, что у enabled-модулей: label primary + name-fallback (U-49).
        containers = resolve_container_name(mod_name, compose_file=compose_file, cache=compose_cache)
        if containers is None:
            # QA R4/T2.D: ps rc≠0 → runtime UNVERIFIED (не converged, не fail)
            logger.error(
                "[IMP:9][converge][%s] Runtime UNVERIFIED for disabled module %s (docker ps failed)", unit, mod_name
            )
            report_add(unit, "warn", f"{mod_name}: runtime UNVERIFIED (docker ps failed)")
            ps_unverified += 1
            set_exit(1)
            continue
        if not containers:
            logger.info("[IMP:7][converge][%s] Disabled module %s — no containers running (converged)", unit, mod_name)
            continue

        logger.warning(
            "[IMP:9][converge][%s] Disabled module %s has %d live container(s): %s — stopping via compose down",
            unit,
            mod_name,
            len(containers),
            containers,
        )
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD stop disabled module %s via docker compose down", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: would be stopped (enabled:false, compose down)")
            stopped += 1
            set_exit(1)
            continue

        # U-49 канон argv (root-compose-first + --profile module) — паритет с heal-веткой.
        compose_args = build_compose_args(
            compose_file=compose_file,
            secrets_env_file=None,  # канон deploy_paths.secrets_env_file() внутри (env-override SECRETS_ENV_FILE)
            platform_root=None,  # канон platform_remote_base() (/opt/platform) внутри (env-override PLATFORM_REMOTE_BASE)
            overlay_dir=None,
            module_name=mod_name,
        )
        services = compose_defined_services(str(mod_dir), cache=compose_cache)
        if not services:
            logger.error(
                "[IMP:10][converge][%s] Disabled module %s: no services from compose config — cannot stop canonically",
                unit,
                mod_name,
            )
            report_add(unit, "fail", f"{mod_name}: compose config failed (cannot resolve services)")
            errors += 1
            set_exit(2)
            continue

        down_ok = True
        for svc in services:
            # C4 канон: docker compose down --timeout <DOCKER_STOP_TIMEOUT> БЕЗ -v (O7/DD10).
            # Сервисный путь останавливает ТОЛЬКО контейнеры модуля — на U-49-ноде down без
            # сервисов снёс бы ВЕСЬ project=platform (все модули).
            if not _shared_docker_compose_down(
                str(compose_file.parent),
                timeout=COMPOSE_UP_TIMEOUT,
                compose_args=compose_args,
                flags=["--timeout", str(DOCKER_STOP_TIMEOUT)],
                service=svc,
            ):
                down_ok = False
                break
        if down_ok:
            logger.info("[IMP:9][converge][%s] Disabled module %s stopped (compose down, no -v)", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: stopped via compose down (enabled:false)")
            stopped += 1
            set_exit(1)
        else:
            logger.error("[IMP:10][converge][%s] Failed to stop disabled module %s via compose down", unit, mod_name)
            report_add(unit, "fail", f"{mod_name}: compose down failed")
            errors += 1
            set_exit(2)

    # ── Check for global cooldown (any container healed in last 3 runs) ──
    global_cooldown = False
    for cname, cdata in containers_map.items():
        last_healed = cdata.get("last_healed_run", 0)
        if last_healed > 0 and current_run - last_healed < _COOLDOWN_RUNS:
            global_cooldown = True
            logger.info(
                "[IMP:7][converge][%s] Global cooldown active — %s healed at run %d (diff=%d < 3)",
                unit,
                cname,
                last_healed,
                current_run - last_healed,
            )
            break

    if global_cooldown:
        # Phase E 017: cooldown-шорткат НЕ маскирует мутации/ошибки disabled-flow —
        # cooldown применяется к HEALING; disabled-stop уже выполнен выше.
        if errors > 0:
            logger.error(
                "[IMP:10][converge][%s] COOLDOWN active, but %d error(s) during disabled-module stop", unit, errors
            )
            report_add(unit, "fail", f"In cooldown; {errors} module(s) had errors")
            return {"unit": unit, "status": "fail", "detail": f"{errors} module(s) had errors"}
        if ps_unverified > 0:
            logger.info(
                "[IMP:8][converge][%s] COOLDOWN active, %d disabled module(s) runtime UNVERIFIED", unit, ps_unverified
            )
            report_add(unit, "warn", f"In cooldown; {ps_unverified} module(s) runtime UNVERIFIED")
            return {
                "unit": unit,
                "status": "warn",
                "detail": f"{ps_unverified} module(s) runtime UNVERIFIED",
            }
        if stopped > 0:
            logger.info(
                "[IMP:9][converge][%s] COOLDOWN active — %d disabled module(s) stopped, healing skipped", unit, stopped
            )
            report_add(unit, "mutated", f"{stopped} disabled module(s) stopped; cooldown — no healing")
            return {
                "unit": unit,
                "status": "mutated",
                "detail": f"{stopped} disabled module(s) stopped; cooldown active",
            }
        logger.info(
            "[IMP:9][converge][%s] COOLDOWN: Previously healed containers still in cooldown — skipping all healing",
            unit,
        )
        report_add(unit, "converged", "In cooldown — previously healed containers")
        return {"unit": unit, "status": "converged", "detail": "Cooldown active, no healing"}

    for mod in modules:
        # W11: parse_node_modules_yaml → dict[str, object] — каст строкового поля
        mod_name = cast(str, mod.get("name", ""))
        if not mod_name or not mod.get("enabled", True):
            continue  # disabled обработаны DISABLED-FLOW выше

        # Check if module has a compose file (docker module) — DevPlan 118 A2:
        # единый канон shared/compose_files.resolve_compose_file (порядок включает docker-compose.base.yml —
        # реальные модули имеют ТОЛЬКО base-compose; старый кортеж их не видел → converge пропускал все docker-модули)
        mod_dir = modules_dir_path / mod_name
        compose_file = resolve_compose_file(str(mod_dir))

        if not compose_file:
            logger.info("[IMP:7][converge][%s] %s has no compose file — skipping (not docker)", unit, mod_name)
            continue

        logger.info("[IMP:7][converge][%s] Checking module: %s", unit, mod_name)

        # Get container names for this module (label primary + name-fallback, Phase E 017)
        containers = resolve_container_name(mod_name, compose_file=compose_file, cache=compose_cache)
        if containers is None:
            # QA R4/T2.D: ps rc≠0 → runtime UNVERIFIED — НЕ converged, но и не fail
            # (exit 2 = доказанный провал heal); WARN + exit 1.
            logger.error(
                "[IMP:9][converge][%s] Runtime UNVERIFIED for module %s (docker ps failed) "
                "— status will not be converged",
                unit,
                mod_name,
            )
            report_add(unit, "warn", f"{mod_name}: runtime UNVERIFIED (docker ps failed)")
            ps_unverified += 1
            set_exit(1)
            continue
        if not containers:
            # QA R4/T2.D legacy-guard: rc==0, но 0 рядов (label И name-fallback пусты) —
            # однократный допрос всех контейнеров ноды: строки с пустым compose-label R9
            # не видит ни по label, ни по каноническому имени → warn; действительно пустая
            # нода (0 строк) остаётся зелёной.
            unlabeled_count = _count_unlabeled_containers(unlabeled_cache, unit)
            if unlabeled_count:
                logger.info(
                    "[IMP:8][converge][%s] Module %s → 0 labeled containers (unlabeled present)", unit, mod_name
                )
            else:
                logger.info("[IMP:7][converge][%s] No running containers for module %s", unit, mod_name)
            continue

        needs_heal = False
        for cname in containers:
            state = get_container_state(cname)
            if state in BAD_DOCKER_STATES:
                # 142 B28a: exited-oneshot (init/createbuckets, RestartPolicy=no) — штатное
                # состояние, НЕ self-heal (compose up -d вернёт fail без env-секретов → ложный
                # exit 2 на каждой converge). Сервисы с restart:unless-stopped в exited — реальная
                # проблема → heal.
                if state == "exited" and get_container_restart_policy(cname) == "no":
                    logger.info(
                        "[IMP:7][converge][%s] Container %s state=exited — oneshot (RestartPolicy=no), skip self-heal",
                        unit,
                        cname,
                    )
                    continue
                logger.warning(
                    "[IMP:9][converge][%s] Container %s state=%s — needs self-heal",
                    unit,
                    cname,
                    state,
                )
                needs_heal = True
            elif state == "running":
                logger.info("[IMP:7][converge][%s] Container %s OK (running)", unit, cname)

        if not needs_heal:
            logger.info("[IMP:9][converge][%s] Module %s all containers OK", unit, mod_name)
            continue

        # ── Self-heal via docker compose up -d ──
        if dry_run or report_only:
            logger.info("[IMP:9][converge][%s] WOULD heal module %s via docker compose up -d", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: would be restarted via compose up -d")
            healed += 1
            set_exit(1)
            continue

        logger.info("[IMP:8][converge][%s] Self-healing module %s via docker compose up -d", unit, mod_name)
        # T6 (DevPlan 116 B5, D8): shared docker_compose_up — sole path; timeout COMPOSE_UP_TIMEOUT=180
        # (DOCKER_TIMEOUT=30 был занижен для up с пуллом образов — стандартизация на канон).
        # REF-0014 (BUG-0701): argv через канонический build_compose_args — root-compose-first +
        # --env-file secrets/platform + --profile module (голый `-f base.yml` = 3 режима отказа).
        compose_args = build_compose_args(
            compose_file=compose_file,
            secrets_env_file=None,  # канон deploy_paths.secrets_env_file() внутри (env-override SECRETS_ENV_FILE)
            platform_root=None,  # канон platform_remote_base() (/opt/platform) внутри (env-override PLATFORM_REMOTE_BASE)
            overlay_dir=None,
            module_name=mod_name,
        )
        if _shared_docker_compose_up(str(compose_file.parent), timeout=COMPOSE_UP_TIMEOUT, compose_args=compose_args):
            logger.info("[IMP:9][converge][%s] Module %s healed successfully", unit, mod_name)
            report_add(unit, "mutated", f"{mod_name}: restarted via compose up -d")
            healed += 1
            set_exit(1)
            # Record heal in cooldown
            containers_map[mod_name] = {"last_healed_run": current_run}
        else:
            logger.error("[IMP:10][converge][%s] Failed to heal module %s via compose up -d", unit, mod_name)
            report_add(unit, "fail", f"{mod_name}: compose up -d failed")
            errors += 1
            set_exit(2)

    # ── Save cooldown data ──
    save_cooldown(cooldown, cooldown_file=cooldown_file)

    # ── Final report ──
    # QA R4/T2.D: приоритет агрегата — errors(fail) > ps_unverified(warn) > mutations(mutated)
    # > converged. UNVERIFIED НИКОГДА не схлопывается в converged: транзиентный сбой docker ps
    # после успешного docker_info не даёт ложного «FULLY CONVERGED».
    # Phase E 017: mutations = healed (compose up) + stopped (disabled compose down).
    if errors > 0:
        status = "fail"
        detail = f"{errors} module(s) had errors"
    elif ps_unverified > 0:
        status = "warn"
        detail = f"{ps_unverified} module(s) runtime UNVERIFIED (docker ps failed)"
    elif healed > 0 or stopped > 0:
        parts: list[str] = []
        if healed > 0:
            parts.append(f"{healed} module(s) healed via compose up -d")
        if stopped > 0:
            parts.append(f"{stopped} disabled module(s) stopped via compose down")
        status = "mutated"
        detail = "; ".join(parts)
    else:
        status = "converged"
        detail = "All containers running"

    logger.info(
        "[IMP:9][converge][%s] DONE: healed=%d stopped=%d errors=%d ps_unverified=%d",
        unit,
        healed,
        stopped,
        errors,
        ps_unverified,
    )
    return {"unit": unit, "status": status, "detail": detail}


# endregion FUNC_reconcile_runtime_state
