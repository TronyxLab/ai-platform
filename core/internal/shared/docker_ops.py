#!/usr/bin/env python3
# GREP_SUMMARY: docker-ops, shared, ps, inspect, exec, stop, rm, tag, image-inspect, manifest-inspect, network, volume, stats, info, pull, sole-path, cli-shell
# STRUCTURE: ▶ ┌cmd builder┐ → _run_docker (subprocess.run capture+text, non-fatal) → ◇ docker_ps/ps_container_names → ◇ docker_inspect/inspect_state_health → ◇ docker_exec → ◇ docker_stop/rm/tag → ◇ docker_image_inspect(_exists) → ◇ docker_manifest_inspect/pull → ◇ docker_network_inspect/create → ◇ docker_volume_inspect → ◇ docker_info/stats → ◇ CLI --shell (ps|inspect|exec) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Единый слой низкоуровневых docker-операций (DevPlan 128 W1, P2-5/D6).
##           docker ps/inspect/exec/stop/rm/tag/image/network/volume/stats/info/manifest/pull —
##           ЕДИНСТВЕННОЕ место прямых `docker <op>` subprocess-вызовов вне compose-домена
##           (гейт docker_sole_path, allowlist пуст). Дедуплицирует 3+ копии операций:
##           deploy_engine (image inspect/tag), docker_orchestrator (ps/stop/rm),
##           observability (ps/stop/rm), orphan_reconciler (ps/rm), converge/vhosts
##           (ps/exec), modules_healthcheck (inspect), docker_collector (ps/inspect/stats),
##           provisioner (network), reconciler_projects/preflight (manifest inspect),
##           hermes_workflow (image inspect/pull), deploy/orchestrator (tag),
##           lifecycle/phases/docker (exec), converge/networks+volumes+runtime,
##           security_posture, docker_registry_auth, state_store (info).
## @scope    Non-compose docker CLI операции core/internal. Compose-операции (up/pull/build/
##           down/config/ps/images) живут в shared/docker_compose.py (гейт compose-sole-path,
##           DevPlan 116 B5 T10). CLI `python3 -m ... --shell <op>` — для shell-фасадов
##           (паттерн ssh_opts 116 B5 D1; lib/docker.sh — тонкий фасад).
## @invariants
##   1. ВСЕ docker ps/inspect/exec subprocess-вызовы платформы живут ТОЛЬКО здесь
##      (гейт test_gate_docker_sole_path.py, allowlist пуст).
##   2. Non-fatal контракт: функции возвращают bool / CompletedProcess / пустые списки —
##      никогда не raise (caller решает severity). TimeoutExpired/FileNotFoundError/OSError →
##      _run_docker → None → вызывающая функция деградирует.
##   3. stdout нормализуется bytes→str (TRAP[BUG] type-safety: text=True в production,
##      но моки/исторический код могут давать bytes) — _stdout_str.
##   4. Timeout'ы — из shared/timeouts.py (единственный реестр, U-11); timeout параметр
##      каждого вызова переопределяем (converge-домен 30s окно, image-операции 60s).
##   5. Модуль не импортирует bootstrap/deploy/* и docker_compose (слой shared — только вниз,
##      без циклов).
##   6. CLI никогда не печатает секреты (команды не содержат credentials).
## @rationale P2-5/D6 (128 Brief): docker-операции в 3+ копиях — drift-акселератор (паттерн
##            «extract when consumers > 3», как SSH_OPTS 116 B5). Единый слой + гейт с пустым
##            allowlist'ом делают дедупликацию enforce-емой. docker_compose.py остаётся
##            compose-доменом (свои compose-функции), примитивы ps/inspect/exec берёт отсюда.
## @changes  2026-08-04 | DevPlan 128 W1 — Created (P2-5/D6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from core.internal.shared.timeouts import (
    DOCKER_CMD_TIMEOUT,
    DOCKER_STOP_TIMEOUT,
    IMAGE_CHECK_TIMEOUT,
    PULL_TIMEOUT,
)

logger = logging.getLogger(__name__)

# ── Docker command tokens (гейт docker_sole_path ps/inspect/exec-скан) ──────────
# Токены, отслеживаемые gate'ом: прямые subprocess-вызовы этих docker-подкоманд
# вне shared/docker_ops.py → RED (allowlist пуст). "compose" исключён — compose-домен
# живёт в shared/docker_compose.py (свой гейт).
DOCKER_OPS_TOKENS: tuple[str, ...] = ("ps", "inspect", "exec")


# region FUNC__failed_process
def _failed_process(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Build a synthetic failed CompletedProcess (для info-функций при сбое запуска).

    ▶ ┌cmd┐ → ⎋ CompletedProcess(returncode=1, stdout="", stderr="command failed")

    ## @purpose — Non-fatal контракт: если subprocess не запустился (нет docker/таймаут),
    ##            info-функции возвращают failed CompletedProcess вместо raise.
    ## @io — ⇥ cmd: list[str] → ⎋ subprocess.CompletedProcess[str]
    ## @complexity — O(1)
    """
    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="command failed to start")


# endregion FUNC__failed_process


# region FUNC__stdout_str
def _stdout_str(result: subprocess.CompletedProcess[str]) -> str:
    """Normalize CompletedProcess.stdout to str (bytes → decode, TRAP[BUG] type-safety).

    ▶ ┌result┐ → ◇ stdout bytes? → decode utf-8 → ⎋ str

    ## @purpose — subprocess.run(text=True) даёт str; моки/исторический код могут давать
    ##            bytes. Единая нормализация перед строковыми операциями (splitlines/partition).
    ## @io — ⇥ result: subprocess.CompletedProcess → ⎋ str
    ## @complexity — O(1)
    """
    out = result.stdout
    if isinstance(out, bytes):
        return out.decode("utf-8", errors="replace")
    return out


# endregion FUNC__stdout_str


# region FUNC__run_docker
def _run_docker(
    cmd: list[str],
    timeout: int = DOCKER_CMD_TIMEOUT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run a docker CLI command with unified non-fatal error handling.

    ▶ ┌cmd, timeout, env┐ → ◇ subprocess.run(cmd, capture, text, timeout) → ⎋ CompletedProcess|None

    ## @purpose — Единая обёртка subprocess для ВСЕХ docker-операций: capture_output+text,
    ##            timeout, логирование TimeoutExpired/FileNotFoundError/OSError.
    ## @io — ⇥ cmd: list[str], timeout: int, env: dict|None → ⎋ CompletedProcess[str] | None
    ## @complexity — O(1) + subprocess I/O
    ## @invariants
    ##   - None = не запустился (нет docker / таймаут / OSError) — caller маппит в False/failed.
    ##   - env=None → наследуется os.environ; env задан → полный dict (не ломает override).
    ##   - Никогда не raise (non-fatal контракт, INVARIANT 2).
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_ops][timeout] Command timed out (%ds): %s", timeout, " ".join(cmd))
        return None
    except FileNotFoundError:
        logger.error("[IMP:10][docker_ops][no-docker] docker command not found: %s", " ".join(cmd[:2]))
        return None
    except OSError as exc:
        logger.warning("[IMP:7][docker_ops][os-error] docker command failed to start: %s", exc)
        return None


# endregion FUNC__run_docker


# ── docker ps ────────────────────────────────────────────────────────────────────


# region FUNC_docker_ps
def docker_ps(
    *,
    all: bool = False,
    quiet: bool = False,
    filters: list[str] | None = None,
    format: str | None = None,
    timeout: int = DOCKER_CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker ps with optional -a/-q/filters/--format.

    ▶ ┌all, quiet, filters, format┐ → ◇ build cmd → _run_docker → ⎋ CompletedProcess (never raise)

    ## @purpose — Единая точка `docker ps` (списки контейнеров: legacy-cleanup, orphan-реконсиляция,
    ##            converge/vhosts, docker_collector, security_posture, healthcheck_poll).
    ## @io — ⇥ all: bool, quiet: bool, filters: list[str] | None, format: str | None,
    ##       timeout: int → ⎋ CompletedProcess[str] (failed при сбое запуска)
    ## @complexity — O(1) + docker ps I/O
    ## @invariants
    ##   - Порядок флагов: ["docker","ps"] + [-a] + [-q] + [--filter ...] + [--format F]
    ##   - Non-fatal: никогда не raise; сбой запуска → failed CompletedProcess
    """
    cmd = ["docker", "ps"]
    if all:
        cmd.append("-a")
    if quiet:
        cmd.append("-q")
    if filters:
        for f in filters:
            cmd.extend(["--filter", f])
    if format:
        cmd.extend(["--format", format])
    logger.info("[IMP:7][docker_ps] Running %s", " ".join(cmd))
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_ps] Command succeeded: %s", " ".join(cmd[:4]))
    return result


# endregion FUNC_docker_ps


# region FUNC_ps_container_names
def ps_container_names(*, all: bool = False, timeout: int = DOCKER_CMD_TIMEOUT) -> list[str]:
    """Return container names from docker ps (--format {{.Names}}).

    ▶ ┌all┐ → docker_ps(format="{{.Names}}") → ○ splitlines → ⊕ non-empty → ⎋ list[str]

    ## @purpose — Typed helper для списков имён контейнеров (legacy-cleanup, orphan-реконсиляция,
    ##            observability, converge/vhosts). Сбой → [] (graceful).
    ## @io — ⇥ all: bool, timeout: int → ⎋ list[str] (empty on failure)
    ## @complexity — O(L) — L строк docker ps
    ## @invariants — Non-fatal: возвращает [] при недоступности docker/таймауте
    """
    result = docker_ps(all=all, format="{{.Names}}", timeout=timeout)
    if result.returncode != 0:
        logger.warning("[IMP:7][ps_container_names] docker ps failed (rc=%d)", result.returncode)
        return []
    names = [line.strip() for line in _stdout_str(result).splitlines() if line.strip()]
    logger.info("[IMP:9][ps_container_names] Found %d container name(s)", len(names))
    return names


# endregion FUNC_ps_container_names


# ── docker inspect / exec ────────────────────────────────────────────────────────


# region FUNC_docker_inspect
def docker_inspect(
    identifier: str,
    format: str | None = None,
    timeout: int = IMAGE_CHECK_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker inspect for a single container/image id.

    ▶ ┌identifier, format┐ → ◇ build cmd → _run_docker → ⎋ CompletedProcess (never raise)

    ## @purpose — Единая точка `docker inspect` (State.Health, State.Restarting, ...).
    ## @io — ⇥ identifier: str, format: str | None, timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker inspect I/O
    ## @invariants — Non-fatal; --format вставляется ПЕРЕД identifier (docker inspect --format F ID)
    """
    cmd = ["docker", "inspect"]
    if format:
        cmd.extend(["--format", format])
    cmd.append(identifier)
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_inspect] Command succeeded: %s", identifier)
    return result


# endregion FUNC_docker_inspect


# region FUNC_docker_inspect_many
def docker_inspect_many(
    identifiers: list[str],
    format: str | None = None,
    timeout: int = IMAGE_CHECK_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker inspect for MANY identifiers in ONE subprocess call (batch-first).

    ▶ ┌identifiers, format┐ → _run_docker(["docker","inspect",(--format F),*ids]) → ⎋ CompletedProcess (never raise)

    ## @purpose — Batch docker inspect (docker_collector, security_posture: O(N) → O(1)).
    ## @io — ⇥ identifiers: list[str], format: str | None, timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) subprocess call; caller парсит JSON-массив/строки stdout
    ## @invariants — Non-fatal; пустой список → failed CompletedProcess (caller деградирует);
    ##               --format вставляется ПЕРЕД identifiers (docker inspect --format F ID...)
    """
    cmd = ["docker", "inspect"]
    if format:
        cmd.extend(["--format", format])
    cmd.extend(identifiers)
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_inspect_many] Batch inspect succeeded: %d identifier(s)", len(identifiers))
    return result


# endregion FUNC_docker_inspect_many


# region FUNC_inspect_state_health
def inspect_state_health(
    identifier: str,
    timeout: int = DOCKER_CMD_TIMEOUT,
) -> tuple[str, str]:
    """Return (State.Status, State.Health.Status) via docker inspect (D5 criterion).

    ▶ ┌identifier┐ → docker_inspect(format="{{.State.Status}}|{{.State.Health.Status}}") →
    │   partition("|") → ⎋ (state, health)

    ## @purpose — Единый источник State.Status|State.Health.Status (healthcheck_poll, D5 канон).
    ## @io — ⇥ identifier: str, timeout: int → ⎋ tuple[str, str] (state, health)
    ## @complexity — O(1) + docker inspect I/O
    ## @invariants — Сбой/пустой stdout → ("", "") (caller ждёт/деградирует, никогда не raise)
    """
    result = docker_inspect(
        identifier,
        format="{{.State.Status}}|{{.State.Health.Status}}",
        timeout=timeout,
    )
    status_line = _stdout_str(result).strip()
    state, _, health = status_line.partition("|")
    logger.info("[IMP:9][inspect_state_health] %s — state=%s health=%s", identifier, state, health or "<none>")
    return state, health


# endregion FUNC_inspect_state_health


# region FUNC_docker_exec
def docker_exec(
    container: str,
    command: list[str],
    timeout: int = DOCKER_CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker exec <container> <command...>.

    ▶ ┌container, command┐ → ◇ build cmd → _run_docker → ⎋ CompletedProcess (never raise)

    ## @purpose — Единая точка `docker exec` (nginx -t / nginx -s reload / service checks).
    ## @io — ⇥ container: str, command: list[str], timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker exec I/O
    ## @invariants — Non-fatal; command аргументы идут ПОСЛЕ container (docker exec C ARG...)
    """
    cmd = ["docker", "exec", container, *command]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_exec] Command succeeded: %s", container)
    return result


# endregion FUNC_docker_exec


# ── docker logs (142 W3 R10 — TSDB self-heal) ────────────────────────────────────


# region FUNC_docker_logs
def docker_logs(
    container: str,
    tail: int = 400,
    timeout: int = DOCKER_CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run `docker logs --tail <N> <container>` (142 W3 — R10 TSDB corruption scan).

    ▶ ┌container, tail┐ → _run_docker(["docker","logs","--tail",N,C]) → ⎋ CompletedProcess (never raise)

    ## @purpose — Единая точка `docker logs --tail` (converge/monitoring R10 детекция
    ##            коррапта TSDB prometheus; до 142 W3 примитив отсутствовал — R10 не мог
    ##            читать логи через docker_ops, гейт docker_sole_path allowlist пуст).
    ## @io — ⇥ container: str, tail: int (строк), timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker logs I/O
    ## @invariants — Non-fatal; stdout = последние N строк логов контейнера
    """
    cmd = ["docker", "logs", "--tail", str(tail), container]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_logs] Logs fetched (tail=%d): %s", tail, container)
    else:
        logger.warning("[IMP:7][docker_logs] docker logs failed for %s", container)
    return result


# endregion FUNC_docker_logs


# ── docker stop / rm / tag ────────────────────────────────────────────────────────


# region FUNC_docker_stop
def docker_stop(container: str, timeout: int = DOCKER_STOP_TIMEOUT) -> bool:
    """Stop a container by name (docker stop, graceful window DOCKER_STOP_TIMEOUT).

    ▶ ┌container┐ → _run_docker(["docker","stop",C]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker stop` (legacy-cleanup, observability, orphan).
    ## @io — ⇥ container: str, timeout: int → ⎋ bool (True = success)
    ## @complexity — O(1) + docker stop I/O
    ## @invariants — Non-fatal: False на сбое; timeout grace-period безопасный (30s канон)
    """
    cmd = ["docker", "stop", container]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_stop] Container stopped: %s", container)
        return True
    logger.warning("[IMP:7][docker_stop] Failed to stop container: %s", container)
    return False


# endregion FUNC_docker_stop


# region FUNC_docker_rm
def docker_rm(container: str, force: bool = False, timeout: int = DOCKER_STOP_TIMEOUT) -> bool:
    """Remove a container (docker rm [-f]).

    ▶ ┌container, force┐ → _run_docker(["docker","rm",(-f),C]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker rm` (legacy-cleanup, observability, orphan self-heal -f).
    ## @io — ⇥ container: str, force: bool, timeout: int → ⎋ bool (True = success)
    ## @complexity — O(1) + docker rm I/O
    ## @invariants — Non-fatal: False на сбое; force=True → docker rm -f (orphan self-heal)
    """
    cmd = ["docker", "rm"]
    if force:
        cmd.append("-f")
    cmd.append(container)
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_rm] Container removed: %s", container)
        return True
    logger.warning("[IMP:7][docker_rm] Failed to remove container: %s", container)
    return False


# endregion FUNC_docker_rm


# region FUNC_docker_tag
def docker_tag(image_id: str, tag: str, timeout: int = DOCKER_CMD_TIMEOUT) -> bool:
    """Tag an image (docker tag <id> <tag>).

    ▶ ┌image_id, tag┐ → _run_docker(["docker","tag",id,tag]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker tag` (deploy_engine rollback fallback-tag, orchestrator rollback).
    ## @io — ⇥ image_id: str, tag: str, timeout: int → ⎋ bool (True = success)
    ## @complexity — O(1) + docker tag I/O
    ## @invariants — Non-fatal: False на сбое (rollback logging; caller решает severity)
    """
    cmd = ["docker", "tag", image_id, tag]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_tag] Tagged %s → %s", image_id, tag)
        return True
    logger.warning("[IMP:7][docker_tag] Failed to tag %s → %s", image_id, tag)
    return False


# endregion FUNC_docker_tag


# ── docker image inspect ──────────────────────────────────────────────────────────


# region FUNC_docker_image_inspect
def docker_image_inspect(
    image_id: str,
    format: str,
    timeout: int = IMAGE_CHECK_TIMEOUT,
) -> str | None:
    """Inspect a local image with --format (docker image inspect --format F ID).

    ▶ ┌image_id, format┐ → _run_docker → ◇ rc==0? → ⊕ stdout strip → ⎋ str | None

    ## @purpose — Единая точка `docker image inspect` (deploy_engine _save_previous_image
    ##            RepoTags-поиск, docker_collector image-метрики).
    ## @io — ⇥ image_id: str, format: str, timeout: int → ⎋ str | None (None = сбой/пусто)
    ## @complexity — O(1) + docker image inspect I/O
    ## @invariants — Non-fatal: None на сбое/таймауте/пустом stdout
    """
    cmd = ["docker", "image", "inspect", image_id, "--format", format]
    result = _run_docker(cmd, timeout=timeout)
    if result is None or result.returncode != 0:
        logger.warning("[IMP:7][docker_image_inspect] Image inspect failed: %s", image_id)
        return None
    out = _stdout_str(result).strip()
    logger.info("[IMP:9][docker_image_inspect] Inspect OK: %s", image_id)
    return out


# endregion FUNC_docker_image_inspect


# region FUNC_docker_image_inspect_many
def docker_image_inspect_many(
    identifiers: list[str],
    format: str,
    timeout: int = IMAGE_CHECK_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker image inspect for MANY image ids in ONE subprocess call (batch-first).

    ▶ ┌identifiers, format┐ → _run_docker(["docker","image","inspect",*ids,"--format",F]) → ⎋ CompletedProcess

    ## @purpose — Batch docker image inspect (docker_collector get_image_sizes: O(N) → O(1)).
    ## @io — ⇥ identifiers: list[str], format: str, timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) subprocess call; caller парсит JSON-массив stdout
    ## @invariants — Non-fatal; --format вставляется ПОСЛЕ ids (docker image inspect ID... --format F)
    """
    cmd = ["docker", "image", "inspect", *identifiers, "--format", format]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_image_inspect_many] Batch image inspect succeeded: %d id(s)", len(identifiers))
    return result


# endregion FUNC_docker_image_inspect_many


# region FUNC_docker_image_inspect_exists
def docker_image_inspect_exists(image_ref: str, timeout: int = IMAGE_CHECK_TIMEOUT) -> bool:
    """Check whether an image exists LOCALLY (docker image inspect <ref> rc==0).

    ▶ ┌image_ref┐ → _run_docker(["docker","image","inspect",ref]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Локальная проверка образа (hermes_workflow L1 base existence).
    ## @io — ⇥ image_ref: str, timeout: int → ⎋ bool (True = local image exists)
    ## @complexity — O(1) + docker image inspect I/O
    ## @invariants — Non-fatal: False на сбое/таймауте (hermes fallback → pull/build)
    """
    cmd = ["docker", "image", "inspect", image_ref]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_image_inspect_exists] Local image exists: %s", image_ref)
        return True
    logger.warning("[IMP:7][docker_image_inspect_exists] Local image NOT found: %s", image_ref)
    return False


# endregion FUNC_docker_image_inspect_exists


# ── docker manifest inspect / pull ────────────────────────────────────────────────


# region FUNC_docker_manifest_inspect
def docker_manifest_inspect_raw(
    image_ref: str,
    timeout: int = IMAGE_CHECK_TIMEOUT,
    flags: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run docker manifest inspect and return the raw CompletedProcess.

    ▶ ┌image_ref, flags┐ → _run_docker(["docker","manifest","inspect",*flags,ref]) → ⎋ CompletedProcess (never raise)

    ## @purpose — Raw variant для caller'ов, которым нужен stderr/rc (preflight probe_docker_hub:
    ##            детекция 429/rate-limit по stderr; security_posture --verbose digest-drift).
    ##            Non-fatal: failed CompletedProcess на сбое.
    ## @io — ⇥ image_ref: str, timeout: int, flags: list[str] | None (e.g. ["--verbose"]) →
    ##       ⎋ CompletedProcess[str]
    ## @complexity — O(1) + network
    ## @invariants — Non-fatal; caller инспектирует .returncode/.stdout/.stderr
    """
    cmd = ["docker", "manifest", "inspect"]
    if flags:
        cmd.extend(flags)
    cmd.append(image_ref)
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_manifest_inspect_raw] Manifest query succeeded: %s", image_ref)
    return result


def docker_manifest_inspect(image_ref: str, timeout: int = IMAGE_CHECK_TIMEOUT) -> bool:
    """Check image existence in a registry (docker manifest inspect — не пуллит).

    ▶ ┌image_ref┐ → docker_manifest_inspect_raw → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker manifest inspect` (check_image_exists, reconciler_projects,
    ##            security_posture). Registry-check без pull.
    ## @io — ⇥ image_ref: str, timeout: int → ⎋ bool (True = image exists in registry)
    ## @complexity — O(1) + network
    ## @invariants — Non-fatal: False на сбое/таймауте; stderr не логируется как error (ожидаем
    ##               на несуществующих образах)
    """
    result = docker_manifest_inspect_raw(image_ref, timeout=timeout)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_manifest_inspect] Image exists: %s", image_ref)
        return True
    logger.warning("[IMP:5][docker_manifest_inspect] Image NOT found: %s", image_ref)
    return False


# endregion FUNC_docker_manifest_inspect


# region FUNC_docker_pull
def docker_pull(image_ref: str, timeout: int = PULL_TIMEOUT) -> bool:
    """Pull an image (docker pull <ref>).

    ▶ ┌image_ref┐ → _run_docker(["docker","pull",ref], timeout=PULL_TIMEOUT) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker pull` (hermes_workflow L1 pull fallback). Compose-пуллы
    ##            идут через docker_compose.docker_compose_pull (compose-домен).
    ## @io — ⇥ image_ref: str, timeout: int → ⎋ bool (True = pull succeeded)
    ## @complexity — O(1) + network I/O
    ## @invariants — Non-fatal: False на сбое/таймауте (hermes fallback → build from source)
    """
    cmd = ["docker", "pull", image_ref]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_pull] Pull succeeded: %s", image_ref)
        return True
    logger.warning("[IMP:7][docker_pull] Pull failed: %s", image_ref)
    return False


# endregion FUNC_docker_pull


# ── docker network / volume / info / stats ────────────────────────────────────────


# region FUNC_docker_network_inspect
def docker_network_inspect_raw(
    name: str,
    timeout: int = DOCKER_CMD_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run docker network inspect and return the raw CompletedProcess.

    ▶ ┌name┐ → _run_docker(["docker","network","inspect",name]) → ⎋ CompletedProcess (never raise)

    ## @purpose — Raw variant для caller'ов, которым нужен stdout (converge/networks driver-check).
    ## @io — ⇥ name: str, timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker network inspect I/O
    ## @invariants — Non-fatal; caller инспектирует .returncode/.stdout
    """
    cmd = ["docker", "network", "inspect", name]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_network_inspect_raw] Network inspected: %s", name)
    return result


def docker_network_inspect(name: str, timeout: int = DOCKER_CMD_TIMEOUT) -> bool:
    """Check whether a docker network exists (docker network inspect <name> rc==0).

    ▶ ┌name┐ → docker_network_inspect_raw → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker network inspect` (provisioner, converge/networks proxy-net).
    ## @io — ⇥ name: str, timeout: int → ⎋ bool (True = network exists)
    ## @complexity — O(1) + docker network inspect I/O
    ## @invariants — Non-fatal: False на сбое (caller решает create)
    """
    result = docker_network_inspect_raw(name, timeout=timeout)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_network_inspect] Network exists: %s", name)
        return True
    logger.warning("[IMP:7][docker_network_inspect] Network NOT found: %s", name)
    return False


# endregion FUNC_docker_network_inspect


# region FUNC_docker_network_create
def docker_network_create(name: str, driver: str = "bridge", timeout: int = DOCKER_CMD_TIMEOUT) -> bool:
    """Create a docker network (docker network create --driver <d> <name>).

    ▶ ┌name, driver┐ → _run_docker(["docker","network","create","--driver",d,name]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker network create` (provisioner, converge/networks proxy-net).
    ## @io — ⇥ name: str, driver: str, timeout: int → ⎋ bool (True = created)
    ## @complexity — O(1) + docker network create I/O
    ## @invariants — Non-fatal: False на сбое (caller логирует ошибку)
    """
    cmd = ["docker", "network", "create", "--driver", driver, name]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_network_create] Network created: %s (driver=%s)", name, driver)
        return True
    logger.warning("[IMP:7][docker_network_create] Failed to create network: %s", name)
    return False


# endregion FUNC_docker_network_create


# region FUNC_docker_volume_inspect
def docker_volume_inspect(name: str, timeout: int = DOCKER_CMD_TIMEOUT) -> bool:
    """Check whether a docker volume exists (docker volume inspect <name> rc==0).

    ▶ ┌name┐ → _run_docker(["docker","volume","inspect",name]) → ◇ rc==0? → ⎋ bool

    ## @purpose — Единая точка `docker volume inspect` (converge/volumes R7 detect-only check).
    ## @io — ⇥ name: str, timeout: int → ⎋ bool (True = volume exists)
    ## @complexity — O(1) + docker volume inspect I/O
    ## @invariants — Non-fatal: False на сбое (converge detect-only, НЕ создаёт)
    """
    cmd = ["docker", "volume", "inspect", name]
    result = _run_docker(cmd, timeout=timeout)
    if result is not None and result.returncode == 0:
        logger.info("[IMP:9][docker_volume_inspect] Volume exists: %s", name)
        return True
    logger.warning("[IMP:7][docker_volume_inspect] Volume NOT found: %s", name)
    return False


# endregion FUNC_docker_volume_inspect


# region FUNC_docker_info
def docker_info(timeout: int = DOCKER_CMD_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run docker info (daemon reachability check).

    ▶ ┌timeout┐ → _run_docker(["docker","info"]) → ⎋ CompletedProcess (never raise)

    ## @purpose — Единая точка `docker info` (docker_registry_auth daemon-readiness poll,
    ##            converge/networks+volumes+runtime daemon check, state_store).
    ## @io — ⇥ timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker info I/O
    ## @invariants — Non-fatal; caller инспектирует .returncode (rc!=0 = daemon недоступен)
    """
    cmd = ["docker", "info"]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_info] Docker daemon reachable")
    return result


# endregion FUNC_docker_info


# region FUNC_docker_stats
def docker_stats(format: str = "{{json .}}", timeout: int = DOCKER_CMD_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run docker stats --no-stream --format <F> (runtime metrics snapshot).

    ▶ ┌format, timeout┐ → _run_docker(["docker","stats","--no-stream","--format",F]) → ⎋ CompletedProcess

    ## @purpose — Единая точка `docker stats` (docker_collector CPU%/memory metrics).
    ## @io — ⇥ format: str, timeout: int → ⎋ CompletedProcess[str]
    ## @complexity — O(1) + docker stats I/O
    ## @invariants — Non-fatal; caller парсит JSON-строки stdout
    """
    cmd = ["docker", "stats", "--no-stream", "--format", format]
    result = _run_docker(cmd, timeout=timeout)
    if result is None:
        return _failed_process(cmd)
    if result.returncode == 0:
        logger.info("[IMP:9][docker_stats] docker stats snapshot succeeded")
    return result


# endregion FUNC_docker_stats


# ── CLI (--shell) ────────────────────────────────────────────────────────────────


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: --shell <op> для shell-фасадов (паттерн ssh_opts 116 B5 D1).

    ▶ ┌argv┐ → ◇ --shell ps [--all] → docker_ps → печать stdout → ⎋ 0
    │        ◇ --shell inspect <id> [--format F] → docker_inspect → печать stdout → ⎋ 0
    │        ◇ --shell exec <container> <cmd...> → docker_exec → печать stdout → ⎋ 0
    │        └ иначе → usage → ⎋ 2

    ## @purpose — Интерфейс для shell-фасадов: lib/docker.sh вызывает
    ##            `python3 .../docker_ops.py --shell <op> ...` и пробрасывает stdout/exit.
    ##            Покрывает ТОЛЬКО read-only операции (ps/inspect/exec) — мутации
    ##            (stop/rm/tag/network) остаются Python-only (безопасность).
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 ok | 1 docker failure | 2 usage)
    ## @invariants
    ##   - --shell ps: печатает stdout docker ps (или names при --format {{.Names}})
    ##   - --shell exec: команда передаётся аргументами (list, не shell-строка — XSS-безопасно)
    ##   - CLI никогда не печатает секреты (read-only операции)
    """
    parser = argparse.ArgumentParser(description="docker_ops — единый слой docker-операций (D6)")
    parser.add_argument("--shell", action="store_true", help="Shell-facade mode: <op> [args]")
    parser.add_argument("op", nargs="?", choices=["ps", "inspect", "exec"], help="Operation")
    parser.add_argument("--all", action="store_true", help="docker ps -a (with ps)")
    parser.add_argument("--format", default=None, help="--format value (ps/inspect)")
    parser.add_argument("rest", nargs="*", help="identifier / container + command args")
    args = parser.parse_args(argv)

    if not args.shell or not args.op:
        parser.print_usage()
        return 2

    if args.op == "ps":
        result = docker_ps(all=args.all, format=args.format)
        sys.stdout.write(_stdout_str(result))
        return 0 if result.returncode == 0 else 1

    if args.op == "inspect":
        if not args.rest:
            parser.error("inspect requires an identifier")
        result = docker_inspect(args.rest[0], format=args.format)
        sys.stdout.write(_stdout_str(result))
        return 0 if result.returncode == 0 else 1

    # exec <container> <cmd...>
    if len(args.rest) < 2:
        parser.error("exec requires a container and a command")
    result = docker_exec(args.rest[0], list(args.rest[1:]))
    sys.stdout.write(_stdout_str(result))
    return 0 if result.returncode == 0 else 1


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
