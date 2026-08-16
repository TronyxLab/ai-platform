#!/usr/bin/env python3
# GREP_SUMMARY: parallel-runner, fork, slot-waiter, drain, deploy-docker-group, pre-pull, atomic-rollback, D1, docker-orchestrator-decomposition
# STRUCTURE: ▶ pre_pull_images ┌entries┐ → fork-per-module (slot=parallel_limit) → drain → ⎋ (ok, fail) │ ▶ deploy_docker_group ┌entries┐ → fork-per-module → drain → ◇ rollback? → docker compose down → fork-HC → ⎋ (deployed, failed, names, rolled_back) │ ▶ drain_completed_count/drain_all_count — WNOHANG/blocking drain
# region MODULE_CONTRACT
## @purpose  Fork-based параллельное выполнение docker-деплоев и pre-pull'ов — экстракция из
##           docker_orchestrator.py (DevPlan 118 D1): fork/slot-waiter/drain (888-1070),
##           pre_pull_images (775-868), pull_module_images (718-772), deploy_docker_group (876-1032).
##           Единственная реализация fork-параллелизма в деплой-стеке (AC-D1: 0 конкурирующих).
## @scope    bootstrap/deploy — вызывается docker_orchestrator CLI (--action pre-pull|deploy-group)
##           и deploy_orchestrator._deploy_parallel (через docker_orchestrator re-export).
## @invariants
##   1. Fork-based параллелизм с slot-limit (parallel_limit, default 4) — WNOHANG-drain в цикле
##   2. Child-процессы используют os._exit() (НЕ sys.exit) — pytest не перехватывает SystemExit
##   3. deploy_docker_group: atomic rollback при любом fail — docker compose down для ВСЕХ модулей группы
##   4. Healthcheck-цикл после deploy-drain (fork-per-module, run_healthcheck из healthcheck_runner)
##   5. pre_pull_images non-fatal: pull fail → лог, compose up retries (best-effort)
##   6. pull_module_images: модули с build: секцией SKIP (нет registry-образа)
## @rationale DevPlan 118 D1 (AC-D1): docker_orchestrator 1397 LOC → оркестратор <900 LOC.
##            Fork-параллелизм — независимая подсистема; экстракция без смены контрактов.
##            170 W10-B: compose-хелперы (build_compose_args/resolve_compose_file) — leaf
##            compose_args.py (module-level импорт); deploy_docker_module — DI-параметр
##            deploy_module_fn (инжектит фасад docker_orchestrator.deploy_docker_group).
##            Цикл parallel_runner ↔ docker_orchestrator разорван (0 рёбер вниз).
## @changes  2026-08-02 | DevPlan 118 D1 — экстракция из docker_orchestrator.py
## @changes  2026-08-15 | DevPlan 170 W10-B — цикл разорван: compose_args leaf + DI deploy_module_fn
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import time
from collections.abc import Callable

from core.internal.bootstrap.deploy import healthcheck_runner
from core.internal.bootstrap.deploy.compose_args import (
    build_compose_args,
    resolve_compose_file,
)
from core.internal.shared.docker_compose import (
    docker_compose_down as _shared_docker_compose_down,
)
from core.internal.shared.docker_compose import (
    retry_pull as _shared_retry_pull,
)
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.timeouts import (
    DOCKER_STOP_TIMEOUT,
    PULL_TIMEOUT,
)

logger = logging.getLogger(__name__)

DEFAULT_PARALLEL_LIMIT = 4

# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Цикл parallel_runner ↔ docker_orchestrator разорван (170 W10-B)
# · Rejected: вынос deploy_docker_module в parallel_runner/compose_args (ядро оркестратора,
# ·   зависит от PHASES/orphan_reconciler/audit)
# · Reason: compose-хелперы (_build_compose_args/_resolve_compose_file) вынесены в leaf
# ·   compose_args.py (module-level импорт); deploy_docker_module инжектится DI-параметром
# ·   deploy_module_fn из docker_orchestrator.deploy_docker_group (фасад) — parallel_runner
# ·   больше НЕ импортирует docker_orchestrator (0 рёбер, acyclic-internal-domains green).
# · Rev: если появится второй caller deploy_docker_group без фасада — пересмотреть
# ·   fail-fast требование deploy_module_fn


# region FUNCpull_module_images
## @purpose  Pull images for a single docker module via shared docker_compose_pull().
##           Skips modules that have a local build: section (no registry image).
## @io       ⇥ mod_name: str, overlay_dir: str | None, secrets_env_file: str | None,
##           platform_root: str | None, modules_dir: str
##           ⎋ bool: True if pull succeeded or skipped
## @complexity 2 — compose file resolution + build: section check + shared pull delegate
## @invariants
##   - Module with `build:` section in compose file is SKIPPED (no registry image)
##   - Missing compose file is SKIPPED (logged, returns True)
##   - Failure is logged but returns True (non-fatal — compose up retries pull)
##   - Delegates pull execution to core.internal.shared.docker_compose.retry_pull()
## @changes 2026-07-26 · DevPlan 079 TASK-9 — Replaced inline subprocess.run with
##           shared docker_compose_pull (T4.5: retry [5,10,20])
def pull_module_images(
    mod_name: str,
    overlay_dir: str | None,
    secrets_env_file: str | None,
    platform_root: str | None,
    modules_dir: str,
) -> bool:
    module_dir = pathlib.Path(modules_dir) / mod_name
    compose_file = resolve_compose_file(str(module_dir))
    if compose_file is None:
        logger.info("[IMP:7][pull_module_images][skip] No compose file for %s — skipping pull", mod_name)
        return True

    # ── Skip modules with local build: section ──
    try:
        content = compose_file.read_text()
        if "build:" in content:
            logger.info("[IMP:7][pull_module_images][skip] Local build detected for %s — skipping pull", mod_name)
            return True
    except OSError:
        pass

    # ── Build pull args and delegate to shared retry_pull (T4.5: retry [5,10,20]) ──
    pull_args = build_compose_args(
        compose_file=compose_file,
        secrets_env_file=secrets_env_file,
        platform_root=platform_root,
        overlay_dir=overlay_dir,
        module_name=mod_name,
    )
    compose_dir = pathlib.Path(str(compose_file)).parent
    logger.info("[IMP:7][pull_module_images][pull] Pulling images for %s", mod_name)
    success = _shared_retry_pull(str(compose_dir), timeout=PULL_TIMEOUT, compose_args=pull_args)
    if success:
        logger.info("[IMP:9][pull_module_images][done] Images pulled for %s", mod_name)
    else:
        logger.warning("[IMP:5][pull_module_images][fail] Pull failed for %s — compose up will retry", mod_name)
    return True  # Non-fatal: compose up -d retries pull internally


# endregion FUNCpull_module_images


# region FUNC__drain_pull_slots
## @purpose  Reap completed pull children until a parallel slot is free (PLR1702 extraction).
##           Non-blocking WNOHANG drain; sleeps when the slot is still full.
## @io       ⇥ pids/names (in-place mutation), parallel_limit → ⎋ (ok, fail) reaped counts
## @complexity O(N) per drain pass; N = running children
## @invariants — Семантика идентична inline-waiter pre_pull_images (плодятся pull_ok/pull_fail)
def _drain_pull_slots(pids: list[int], names: list[str], parallel_limit: int) -> tuple[int, int]:
    """Reap completed child pulls until a slot is free, return (ok, fail) deltas."""
    ok = 0
    fail = 0
    while len(pids) >= parallel_limit:
        for i in range(len(pids) - 1, -1, -1):
            pid = pids[i]
            # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализир...
            try:
                # Non-blocking wait with WNOHANG
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid != pid:
                    continue
                if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                    ok += 1
                else:
                    fail += 1
                _ = pids.pop(i)
                _ = names.pop(i)
            except ChildProcessError:
                fail += 1
                _ = pids.pop(i)
                _ = names.pop(i)
        if len(pids) >= parallel_limit:
            time.sleep(1)
    return ok, fail


# endregion FUNC__drain_pull_slots


# region FUNC_pre_pull_images
## @purpose  Parallel pre-pull of all docker module images BEFORE topo-sorted compose up.
##           Executes docker compose pull for each module in parallel with slot limiting.
##           Uses same parallel slot pattern as deploy_docker_group (subprocess PIDs via threading).
## @io       ⇥ entries: list[str] ("module:overlay" format),
##           modules_dir: str, secrets_env_file: str | None, platform_root: str | None,
##           parallel_limit: int
##           ⎋ tuple[int, int] — (success_count, fail_count)
## @complexity 3 — parallel dispatch with threading-based slot limiting
## @invariants
##   - parallel_limit controls max concurrent pull operations (default 4)
##   - Pull failure is LOGGED but NOT fatal — compose up -d retries pull internally
##   - Already-cached images return immediately (docker compose pull is no-op)
## @rationale Q: Why pull separately from up -d? A: docker compose up -d pulls images
##   sequentially within each project even when modules are parallel. A dedicated pull
##   phase batches ALL image downloads at once, utilizing full network bandwidth.
##   Q: Why non-fatal? A: compose up -d already retries pull — pre-pull is optimization,
##   not correctness. Failing here and succeeding in up -d is harmless.
def pre_pull_images(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
) -> tuple[int, int]:
    logger.info(
        "[IMP:7][pre_pull_images][start] Pre-pulling for %d modules (parallel: %d)",
        len(entries),
        parallel_limit,
    )
    pull_ok = 0
    pull_fail = 0
    pids: list[int] = []
    names: list[str] = []

    for entry in entries:
        mod_name, _, mod_overlay = entry.partition(":")
        if not mod_overlay or mod_overlay == mod_name:
            mod_overlay = ""

        # ── Parallel slot waiter (PLR1702: вынесено в _drain_pull_slots) ──
        drained_ok, drained_fail = _drain_pull_slots(pids, names, parallel_limit)
        pull_ok += drained_ok
        pull_fail += drained_fail

        # ── Fork subprocess for pull ──
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() NOT sys.exit() to avoid pytest
            # intercepting SystemExit in forked children (SystemExit inherits
            # BaseException, not Exception, so bare Exception catch misses it)
            try:
                success = pull_module_images(
                    mod_name, mod_overlay or None, secrets_env_file, platform_root, modules_dir
                )
                os._exit(0 if success else 1)
            # ruff: ignore[BLE001] — forked-child: любой сбой модуля → os._exit(1), best-effort (DEPLOY_BEST_EFFORT)
            except Exception:  # noqa: EXC — forked child: catch all to prevent base exception propagation (best-effort: DEPLOY_BEST_EFFORT policy)
                os._exit(1)
        else:
            pids.append(pid)
            names.append(mod_name)

    # ── Drain remaining PIDs ──
    for i in range(len(pids) - 1, -1, -1):
        try:
            _pid, status = os.waitpid(pids[i], 0)
            if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                pull_ok += 1
            else:
                pull_fail += 1
        except ChildProcessError:  # ruff: ignore[PERF203]
            pull_fail += 1

    logger.info("[IMP:9][pre_pull_images][done] Pre-pull complete: success=%d failed=%d", pull_ok, pull_fail)
    return (pull_ok, pull_fail)


# endregion FUNC_pre_pull_images


# region FUNC_deploy_docker_group
## @purpose  Deploy a group of docker modules in parallel with slot limiting.
##           Each module is deployed via deploy_docker_module in a child process.
##           After all deploys complete, runs healthchecks in parallel for each module.
## @io       ⇥ entries: list[str] ("module:overlay" format),
##           modules_dir: str, secrets_env_file: str | None, platform_root: str | None,
##           parallel_limit: int
##           ⎋ tuple[int, int, list[str], list[str]] — (deployed, failed, failed_names, rolled_back)
## @usecases (W5-E1) Atomic rollback: if any module fails, ALL modules in the group are shut down
##           via docker compose down. Rolled_back list contains names of modules that were
##           successfully shut down. Healthcheck still runs after rollback to verify recovery.
## @complexity 4 — parallel deploy with fork-based slot limiting + parallel healthcheck
## @invariants
##   - parallel_limit controls max concurrent deploy operations (default 4)
##   - Healthchecks run AFTER all deploys in the group complete
##   - Healthcheck failures are logged but do NOT affect deploy return count
##   - Failed module names are tracked for severity-based exit code aggregation
## @rationale Q: Why fork() instead of threading? A: Bash uses subshell (& + wait).
##   Fork-based parallelism preserves the exact same semantics: each deploy has its
##   own process context, environment isolation, and independent failure handling.
def deploy_docker_group(
    entries: list[str],
    modules_dir: str,
    secrets_env_file: str | None = None,
    platform_root: str | None = None,
    parallel_limit: int = DEFAULT_PARALLEL_LIMIT,
    *,
    drain_all_fn: Callable[[list[int], dict[int, str]], tuple[int, int, list[str]]] | None = None,
    resolve_compose_fn: Callable[[str], pathlib.Path | None] | None = None,
    compose_down_fn: Callable[..., bool] | None = None,
    deploy_module_fn: Callable[..., bool] | None = None,
) -> tuple[int, int, list[str], list[str]]:
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · drain/resolve/compose_down инъекция (167 D3/D6)
    # · Rejected: тест патчил drain_all_count/_resolve_compose_file/_shared_docker_compose_down
    # · Reason: seam = тестируемость реального вызова — rollback-ветка (W5-E1) и drain-семантика
    # ·   наблюдаются fake-функциями без monkeypatch; os.fork-ядро остаётся keep (TRAP[DI-KEEP])
    # · Rev: при извлечении process-pool runner'а — синхронизировать протокол
    # 🧐 TRAP[DI-SEAM] · 2026-08-15 · — · deploy_module_fn инъекция (170 W10-B)
    # · Rejected: lazy-импорт deploy_docker_module из docker_orchestrator (держал цикл)
    # · Reason: fork-дети вызывают deploy через DI-параметр; фасад docker_orchestrator.
    # ·   deploy_docker_group инжектит реальный deploy_docker_module; fail-fast при None
    # ·   (единственный production-caller — фасад; параллельных путей нет)
    # · Rev: при втором production-caller deploy_docker_group напрямую
    if deploy_module_fn is None:
        msg = (
            "deploy_module_fn is required (DI seam, 170 W10-B) — "
            "docker_orchestrator.deploy_docker_group injects deploy_docker_module"
        )
        raise ConfigValidationError(msg)

    logger.info(
        "[IMP:7][deploy_docker_group][start] Deploying %d modules in parallel (limit: %d)",
        len(entries),
        parallel_limit,
    )
    pids: list[int] = []
    pid_to_name: dict[int, str] = {}
    group_deployed = 0
    group_failed = 0
    failed_names: list[str] = []

    for entry in entries:
        mod_name, _, mod_overlay = entry.partition(":")
        if not mod_overlay or mod_overlay == mod_name:
            mod_overlay = ""

        # ── Parallel slot waiter ──
        while len(pids) >= parallel_limit:
            deployed, failed, fnames = drain_completed_count(pids, pid_to_name)
            group_deployed += deployed
            group_failed += failed
            failed_names.extend(fnames)
            if len(pids) >= parallel_limit:
                time.sleep(1)

        # ── Fork subprocess for deploy ──
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() NOT sys.exit() to avoid pytest
            # intercepting SystemExit in forked children
            try:
                success = deploy_module_fn(
                    mod_name,
                    mod_overlay or None,
                    secrets_env_file,
                    platform_root,
                    modules_dir,
                )
                os._exit(0 if success else 1)
            # ruff: ignore[BLE001] — forked-child: любой сбой модуля → os._exit(1), best-effort (DEPLOY_BEST_EFFORT)
            except Exception:  # noqa: EXC — forked child: catch all to prevent base exception propagation (best-effort: DEPLOY_BEST_EFFORT policy)
                os._exit(1)
        else:
            pids.append(pid)
            pid_to_name[pid] = mod_name

    # ── Drain remaining PIDs ──
    drain = drain_all_fn if drain_all_fn is not None else drain_all_count
    d, f, fn = drain(pids, pid_to_name)
    group_deployed += d
    group_failed += f
    failed_names.extend(fn)

    all_names = list(pid_to_name.values())
    logger.info(
        "[IMP:8][deploy_docker_group][deploy] Deploy phase done: deployed=%d failed=%d total=%d",
        group_deployed,
        group_failed,
        len(all_names),
    )

    # ── Atomic rollback on failure (W5-E1) — shut down ALL modules in the group ──
    rolled_back: list[str] = []
    if group_failed > 0:
        logger.info(
            "[IMP:8][deploy_docker_group][rollback] %d module(s) failed — initiating atomic rollback of all %d module(s)",
            group_failed,
            len(all_names),
        )
        for entry in entries:
            mod_name, _, _ = entry.partition(":")
            resolve = resolve_compose_fn if resolve_compose_fn is not None else resolve_compose_file
            compose_file = resolve(str(pathlib.Path(modules_dir) / mod_name))
            if compose_file:
                # Shared docker_compose_down — sole path (DevPlan 116 B5 T4)
                down = compose_down_fn if compose_down_fn is not None else _shared_docker_compose_down
                if down(
                    str(compose_file.parent),
                    timeout=DOCKER_STOP_TIMEOUT,
                    compose_args=["-f", str(compose_file), "--profile", mod_name],
                ):
                    rolled_back.append(mod_name)
                    logger.info("[IMP:8][deploy_docker_group][rollback] Module shut down: %s", mod_name)
                else:
                    logger.warning("[IMP:5][deploy_docker_group][rollback] Failed to shut down %s", mod_name)
        logger.info(
            "[IMP:9][deploy_docker_group][rollback] Atomic rollback: %d modules rolled back: %s",
            len(rolled_back),
            rolled_back,
        )

    # ── Parallel healthcheck (T5.1) — fork-per-module after deploy drain ──
    # Runs healthcheck on ALL modules in the group (both deployed and failed)
    # using the same fork pattern as the deploy phase. Failures are collected
    # (not blocking) for post-deploy summary.
    hc_pids: list[int] = []
    hc_names: list[str] = []
    hc_pass_count = 0
    hc_fail_count = 0
    hc_fail_names: list[str] = []

    logger.info("[IMP:7][deploy_docker_group][hc_start] Running healthchecks for %d modules", len(all_names))
    for mod_name in all_names:
        pid = os.fork()
        if pid == 0:
            # Child process — use os._exit() to avoid SystemExit in forked children
            try:
                success = healthcheck_runner.run_healthcheck(mod_name, "docker")
                os._exit(0 if success else 1)
            # ruff: ignore[BLE001] — forked-child: любой сбой модуля → os._exit(1), best-effort (DEPLOY_BEST_EFFORT)
            except Exception:  # noqa: EXC — forked child: catch all to prevent base exception propagation (best-effort: DEPLOY_BEST_EFFORT policy)
                os._exit(1)
        else:
            hc_pids.append(pid)
            hc_names.append(mod_name)

    # Drain HC children and track per-module results
    for i in range(len(hc_pids) - 1, -1, -1):
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            _wpid, status = os.waitpid(hc_pids[i], 0)
            mod_name = hc_names[i]
            if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                hc_pass_count += 1
                logger.info("[IMP:9][deploy_docker_group][hc_pass] Healthcheck PASS for %s", mod_name)
            else:
                hc_fail_count += 1
                hc_fail_names.append(mod_name)
                logger.warning("[IMP:5][deploy_docker_group][hc_fail] Healthcheck FAIL for %s", mod_name)
        except ChildProcessError:  # ruff: ignore[PERF203]
            hc_fail_count += 1
            hc_fail_names.append(hc_names[i])
            logger.warning("[IMP:5][deploy_docker_group][hc_error] Healthcheck error for %s", hc_names[i])

    if hc_fail_count > 0:
        logger.warning(
            "[IMP:5][deploy_docker_group][hc_summary] Healthcheck: %d passed, %d failed: %s",
            hc_pass_count,
            hc_fail_count,
            hc_fail_names,
        )
    else:
        logger.info(
            "[IMP:9][deploy_docker_group][hc_summary] Healthcheck: ALL %d modules PASSED",
            hc_pass_count,
        )

    logger.info(
        "[IMP:9][deploy_docker_group][done] Group complete: deployed=%d failed=%d names=%s rolled_back=%d hc_fail=%d",
        group_deployed,
        group_failed,
        failed_names,
        len(rolled_back),
        hc_fail_count,
    )
    return (group_deployed, group_failed, failed_names, rolled_back)


# endregion FUNC_deploy_docker_group


# region FUNCdrain_completed_count
## @purpose  Non-blocking drain of completed child processes, returning success/fail counts.
##           Used by deploy_docker_group slot-waiter loop to free slots and track results.
## @io       ⇥ pids: list[int] (mutated in place), pid_to_name: dict[int, str] (mutated)
##           ⎋ tuple[int, int, list[str]] — (success_count, fail_count, fail_names)
def drain_completed_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    deployed = 0
    failed = 0
    failed_names: list[str] = []
    for i in range(len(pids) - 1, -1, -1):
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            wpid, status = os.waitpid(pids[i], os.WNOHANG)
            if wpid == pids[i]:
                mod_name = pid_to_name.pop(pids[i], "?")
                if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                    deployed += 1
                else:
                    failed += 1
                    failed_names.append(mod_name)
                _ = pids.pop(i)
        except ChildProcessError:  # ruff: ignore[PERF203]
            mod_name = pid_to_name.pop(pids[i], "?")
            failed += 1
            failed_names.append(mod_name)
            _ = pids.pop(i)
    return (deployed, failed, failed_names)


# endregion FUNCdrain_completed_count


# region FUNCdrain_all_count
## @purpose  Blocking drain of all remaining child processes with result tracking.
## @io       ⇥ pids: list[int] (cleared), pid_to_name: dict[int, str] (cleared)
##           ⎋ tuple[int, int, list[str]] — (success_count, fail_count, fail_names)
def drain_all_count(
    pids: list[int],
    pid_to_name: dict[int, str],
) -> tuple[int, int, list[str]]:
    deployed = 0
    failed = 0
    failed_names: list[str] = []
    for i in range(len(pids) - 1, -1, -1):
        try:
            _ = os.waitpid(pids[i], 0)
            mod_name = pid_to_name.pop(pids[i], "?")
            # Success — waitpid returned without error means process exited
            deployed += 1
        except ChildProcessError:  # ruff: ignore[PERF203]
            mod_name = pid_to_name.pop(pids[i], "?")
            failed += 1
            failed_names.append(mod_name)
    pids.clear()
    return (deployed, failed, failed_names)


# endregion FUNCdrain_all_count
