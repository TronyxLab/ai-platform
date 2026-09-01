#!/usr/bin/env python3
# GREP_SUMMARY: orchestrator-cli, cli, dispatch, receive, deliver, deploy-many, status, remove, verify, ping, health, rollback, entrypoint, SSH_ORIGINAL_COMMAND, verb-registry, handlers
# STRUCTURE: ▶ main() → argparse → thin dispatch ┌dispatch → _VERB_HANDLERS[verb] (реестр: ping|exit|status|health|verify|remove|receive|rollback) | deliver | deploy | deploy-many | rollback | status | remove | health┐ → sys.exit(0|1)
"""
CLI entrypoint for DeployOrchestrator. Commands: dispatch, deliver, receive, deploy, deploy-many, rollback, status, remove, health.

`dispatch` — VPS-side forced-command dispatcher (DevPlan 116 B1): reads SSH_ORIGINAL_COMMAND
  (фолбэк — CLI args), парсит через deploy/ssh_command_parser (T1, DevPlan 118 D3), маршрутизирует verb
  через реестр _VERB_HANDLERS (170 W4-B3, CANONICAL_VERBS из shared/verbs.py):
  ping → "pong"; exit → 0; status → ProjectStatus JSON (exit 0/1); health → docker inspect
  State.Health.Status (read-only слово-контракт: healthy|starting|unhealthy|missing|error);
  verify → мост на core.internal.verify.domain_verifier + verify_contracts;
  remove → DeployOrchestrator.remove(); receive → tar из stdin + DeployOrchestrator.receive();
  rollback → DeployOrchestrator.rollback() (snapshot-based, D8 launch-validation).

`deliver` — операторская сторона (T5): ассемблирует payload, доставляет через
  ForcedCommandChannel (remote_cmd "receive <project> <version>"), печатает JSON с VPS,
  exit 0/1 по нему. НЕ выполняет локальный compose (обход двойного канала, T5).

Usage:
    python3 -m core.internal.deploy.orchestrator_cli dispatch [<verb> <args>...]  # forced-command
    python3 -m core.internal.deploy.orchestrator_cli deliver --project <p> --version <sha> --host <h>
    python3 -m core.internal.deploy.orchestrator_cli receive [--project <p>] [--version <sha>]
    python3 -m core.internal.deploy.orchestrator_cli deploy ...
    python3 -m core.internal.deploy.orchestrator_cli deploy-many ...
    python3 -m core.internal.deploy.orchestrator_cli rollback ...
    python3 -m core.internal.deploy.orchestrator_cli status --project <p>
    python3 -m core.internal.deploy.orchestrator_cli remove --project <p>
"""
# region MODULE_CONTRACT
## @purpose  CLI entrypoint for DeployOrchestrator. Replaces the shell deploy pipeline and provides
##           direct access to all orchestrator operations from command line.
##           Command `dispatch` — VPS-side forced-command dispatcher по SSH_ORIGINAL_COMMAND (DevPlan 116
##           B1 T2, U-04): receive/status/health/verify/remove/rollback/ping/exit через реестр verb→handler
##           (_VERB_HANDLERS, 170 W4-B3 — декомпозиция _dispatch 163 LOC).
##           Command `receive` — читает Payload из stdin (tar) + версию из аргументов (D5), вызывает
##           DeployOrchestrator.receive().
##           Command `deliver` — операторская доставка через ForcedCommandChannel (T5).
##           Command `deploy-many` — project_names + channel, делегирует DeployOrchestrator.deploy_many().
##           Command `health` (main-alias) — read-only verb для операторского ручного вызова
##           (`orchestrator_cli health --project <p> [--service <s>]`).
## @scope    Entrypoint for SSH forced-command (dispatch/receive), shell scripts (deploy-many), операторов (deliver).
## @invariants
##   1. dispatch: SSH_ORIGINAL_COMMAND читается из env; фолбэк — CLI args; пусто → JSON-ошибка + exit 1
##   2. dispatch: неизвестный verb → ConfigValidationError → JSON-ошибка + exit 4 (D2: никакого дефолт-фолбэка)
##   3. dispatch status: found/stub → exit 0, not_found/error → exit 1 (ProjectStatus JSON — канон, D6)
##   4. receive: tar из stdin + version из аргументов (D5); JSON DeployResult с version; exit 0/1
##   5. deliver: ForcedCommandChannel receive \<project\> \<version\>; НЕ вызывает локальный compose (T5)
##   6. PlatformError → return e.exit_code (B4-контракт: sys.exit только в main())
##   7. Channel selection: --scp для SCPChannel, --forced-command для ForcedCommandChannel,
##      дефолт — LocalChannel при отсутствии host (D7, T6)
##   8. 170 W4-B3: реестр _VERB_HANDLERS — ровно CANONICAL_VERBS (8); handler'ы приватные,
##      семантика 1:1 (dispatch-ветки вынесены из _dispatch); main() тонкий, DeployOrchestrator
##      создаётся лениво (не для dispatch/deliver)
##   9. B3 fix-forward (health): read-only verb `health <project> [<service>]` — docker inspect
##      State.Health.Status через shared/docker_ops.docker_inspect (тот же слой, что engine);
##      stdout ровно ОДНО слово: healthy|starting|unhealthy|missing|error; rc=0 для
##      healthy|starting|unhealthy|missing (успешный запрос факта!), rc=1 только при внутренней
##      ошибке инспекта (daemon недоступен/нет docker/timeout). НЕ пишет audit-записей.
##      Потребитель: project_payload_delivery B3-предпробка «уже live» (ci-deploy
##      forced-command-restricted — произвольный docker inspect невозможен).
##   10. D8 launch-validation (rollback): forced-command `rollback <project> [<snapshot-id>]` —
##      тот же handler, что main-CLI rollback (унифицированная сигнатура (args, ctx), паттерн
##      status/remove/health); per-project flock (REF-0011), snapshot_id="" → latest snapshot
## @rationale DevPlan 089 T6.6: единый CLI entrypoint заменяет shell deploy pipeline.
##            DevPlan 116 B1 T2 (U-04): receive игнорировал SSH_ORIGINAL_COMMAND — CI-верификация
##            была фиктивна; dispatch диспетчеризует SSH_ORIGINAL_COMMAND (receive|status|verify|
##            remove|ping|exit) через единый парсер (T1).
## @changes 2026-07-30 | DevPlan 089 T6.6 — Created
##           2026-08-01 | DevPlan 116 B1 T2/T3/T5 — +dispatch, +deliver; status exit 0/1 (D6);
##                       receive с версией из аргументов (D5); deploy-many дефолт LocalChannel (D7)
##           2026-08-15 | 170 W4-B3 — _dispatch 163 LOC → реестр verb→handler (_VERB_HANDLERS,
##                       CANONICAL_VERBS из shared/verbs.py); verify-блок → _handle_verify (мост на
##                       verify_contracts); _deliver → _handle_deliver; удалён мёртвый
##                       _VERIFY_DOMAINS_SH; DeployOrchestrator в main() — ленивый
##           2026-08-27 | B3 fix-forward — +_handle_health (read-only verb: docker inspect
##                       State.Health.Status через shared/docker_ops.docker_inspect; слово-контракт
##                       stdout healthy|starting|unhealthy|missing|error, rc 0/1); +health в
##                       _VERB_HANDLERS/parse-args; +main-alias `health --project [--service]`;
##                       +docker_runner DI (W4d, _DispatchContext) — тесты 0 патчей
##           2026-09-01 | launch-validation D8 — +rollback в dispatch-контур: 8-й verb
##                       (forced-command `rollback <project> [<snapshot-id>]`); _handle_rollback
##                       унифицирован до (args: str, ctx: _DispatchContext) — тот же handler
##                       для main-CLI и dispatch (паттерн status/remove/health); +rollback в
##                       T9.7-валидацию project-name; _VERB_HANDLERS = CANONICAL_VERBS (8)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import BinaryIO, cast

from core.internal.deploy.channels import DeliveryChannel, ForcedCommandChannel, LocalChannel, SCPChannel
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.deploy.ssh_command_parser import parse_ssh_command
from core.internal.shared.deploy_paths import platform_remote_base, projects_base
from core.internal.shared.docker_ops import docker_inspect
from core.internal.shared.exceptions import ConfigValidationError, PlatformError

# T9.7 (L-10): валидация project_name в dispatch ДО маршрутизации (канон shared/project_registry)
from core.internal.shared.project_registry import validate_project_name as _validate_project_name

# W4d (160 T4.4): DI-протокол subprocess-канала — docker_runner для health verb (docker_inspect)
from core.internal.shared.subprocess_io import CommandRunner

# W1-A1 (план 170): timeout=600 литерал (verify-подвызов) → канон DEPLOY_TIMEOUT (SoT, 600)
from core.internal.shared.timeouts import DEPLOY_TIMEOUT

# 170 W4-B3: каноническое verb-множество (shared/verbs.py — единый SoT, U-56)
from core.internal.shared.verbs import CANONICAL_VERBS

logger = logging.getLogger(__name__)


# region CLASS__DispatchContext
@dataclass(frozen=True)
class _DispatchContext:
    """DI-каналы dispatch-handler'ов (W-H DevPlan 163): orchestrator + subprocess/stdin/фабрика.

    ## @purpose — Единый контейнер DI-каналов для реестра verb→handler (_VERB_HANDLERS).
    ##            None-поля → канонические os.environ/sys.stdin/DeployOrchestrator/subprocess.run
    ##            (поведение по умолчанию неизменно); тесты передают fake-каналы (0 патчей, W-H).
    ## @io — ⇥ orchestrator: DeployOrchestrator (обязателен), stdin_stream/orchestrator_factory/
    ##            runner/purge (DI, дефолты) → ⎋ _DispatchContext
    ## @complexity — O(1)
    ## @invariants
    ##   - runner=None-эквивалент = subprocess.run (verify-подвызов)
    ##   - docker_runner=None-эквивалент = прямой subprocess (docker_inspect, W4d-канон) —
    ##     health-verb; fake-раннер в тестах (0 патчей)
    ##   - purge — ТОЛЬКО для remove (main-путь передаёт args.purge; dispatch — False)
    """

    orchestrator: DeployOrchestrator
    stdin_stream: BinaryIO | None = None
    orchestrator_factory: Callable[..., DeployOrchestrator] | None = None
    runner: Callable[..., subprocess.CompletedProcess[str]] = field(default=subprocess.run)
    docker_runner: CommandRunner | None = None
    purge: bool = False


# endregion CLASS__DispatchContext


# region FUNC_build_parser
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose — Build argparse with subcommands for all deploy operations.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Deploy Orchestrator CLI — unified deploy entrypoint (DevPlan 089/116)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── dispatch — VPS-side forced-command dispatcher (SSH_ORIGINAL_COMMAND) ──
    disp = sub.add_parser("dispatch", help="Dispatch SSH_ORIGINAL_COMMAND (forced-command, DevPlan 116)")
    disp.add_argument(
        "dispatch_args",
        nargs="*",
        help="Fallback command string when SSH_ORIGINAL_COMMAND is empty (e.g. 'status proj')",
    )

    # ── deliver — operator-side ForcedCommandChannel delivery (T5) ──
    dl = sub.add_parser("deliver", help="Deliver payload via ForcedCommandChannel (operator path, T5)")
    dl.add_argument("--project", required=True, help="Project name")
    dl.add_argument("--version", default="latest", help="Version/sha to deliver")
    dl.add_argument("--host", default="", help="Remote host")
    dl.add_argument("--user", default="ci-deploy", help="SSH user")
    dl.add_argument("--key-file", default="", help="SSH key file path")
    dl.add_argument("--project-dir", default="", help="Project directory path")

    # ── receive — VPS-side tar reader (версия из аргументов, D5) ──
    rc = sub.add_parser("receive", help="Receive deploy payload via stdin (tar.gz)")
    rc.add_argument("--project", default="", help="Project name (override ai-platform.yaml name)")
    rc.add_argument("--version", default="latest", help="Version/sha to pin (D5)")

    # ── deploy — single project ──
    dep = sub.add_parser("deploy", help="Deploy a single project")
    dep.add_argument("--project", required=True, help="Project name")
    dep.add_argument("--version", default="latest", help="Version/tag to deploy")
    dep.add_argument("--service", default="", help="Docker Compose service name")
    dep.add_argument("--project-dir", default="", help="Project directory path")
    dep.add_argument("--scp", action="store_true", help="Use SCPChannel")
    dep.add_argument("--forced-command", action="store_true", help="Use ForcedCommandChannel")
    dep.add_argument("--host", default="", help="Remote host for channel delivery")
    dep.add_argument("--user", default="", help="SSH user")
    dep.add_argument("--key-file", default="", help="SSH key file path")
    dep.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan the deploy without executing (AC10)",
    )

    # ── deploy-many — multiple projects ──
    dm = sub.add_parser("deploy-many", help="Deploy multiple projects sequentially")
    dm.add_argument("--projects", required=True, help="Comma-separated project names")
    dm.add_argument("--version", default="latest", help="Version/tag")
    dm.add_argument("--scp", action="store_true", help="Use SCPChannel")
    dm.add_argument("--forced-command", action="store_true", help="Use ForcedCommandChannel")
    dm.add_argument("--host", default="", help="Remote host")
    dm.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Plan each deploy without executing (AC10)",
    )

    # ── rollback ──
    rb = sub.add_parser("rollback", help="Rollback a project")
    rb.add_argument("--project", required=True, help="Project name")
    rb.add_argument("--snapshot-id", default="", help="Specific snapshot ID (default: latest)")

    # ── status ──
    st = sub.add_parser("status", help="Get project status")
    st.add_argument("--project", required=True, help="Project name")

    # ── health — read-only verb alias (B3 fix-forward): docker inspect State.Health.Status ──
    he = sub.add_parser(
        "health",
        help="Container health status (read-only verb: docker inspect State.Health.Status; "
        "stdout healthy|starting|unhealthy|missing|error)",
    )
    he.add_argument("--project", required=True, help="Project name")
    he.add_argument("--service", default="", help="Docker Compose service name (default: project)")

    # ── remove ──
    rm = sub.add_parser("remove", help="Remove project containers")
    rm.add_argument("--project", required=True, help="Project name")
    rm.add_argument("--purge", action="store_true", help="Remove compose volumes (down -v)")

    return parser


# endregion FUNC_build_parser


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170) —
    зеркало всех subcommand-флагов build_parser(); значения из parser.parse_args().
    Аннотации без значений — cast no-op, argparse ставит свои дефолты."""

    command: str
    dispatch_args: list[str]
    project: str
    version: str
    host: str
    user: str
    key_file: str
    project_dir: str
    service: str
    scp: bool
    forced_command: bool
    dry_run: bool
    projects: str
    snapshot_id: str
    purge: bool


# endregion DATACLASS_CliArgs


# region FUNC_build_channel
def build_channel(args: _CliArgs) -> SCPChannel | ForcedCommandChannel | LocalChannel:
    """Build a delivery channel from CLI args.

    Args:
        args: Parsed CLI arguments (_CliArgs — типизированная граница, W11).

    Returns:
        SCPChannel, ForcedCommandChannel or LocalChannel.
        Дефолт (D7, DevPlan 116 B1 T6): LocalChannel при отсутствии host/--scp/--forced-command —
        на-ноде операция (bootstrap deploy-many), SCP-доставка самой себе бессмысленна.
    """
    use_forced = args.forced_command
    use_scp = args.scp
    host = args.host
    user = args.user
    key_file = args.key_file
    host = host or os.environ.get("DEPLOY_HOST", "")

    if use_forced:
        channel: SCPChannel | ForcedCommandChannel | LocalChannel = ForcedCommandChannel()
    elif use_scp:
        channel = SCPChannel()
    elif host:
        # Хост указан без явного флага → ForcedCommandChannel (операторский путь через verb-форму)
        channel = ForcedCommandChannel()
    else:
        # D7: на-ноде операция без транспорта (bootstrap deploy-many) — LocalChannel
        channel = LocalChannel()

    if host:
        channel.metadata_defaults = {"host": host}
        if user:
            channel.metadata_defaults["user"] = user
        if key_file:
            channel.metadata_defaults["key_file"] = key_file

    return channel


# endregion FUNC_build_channel


# ── Dispatch verb handlers (реестр _VERB_HANDLERS, CANONICAL_VERBS) ─────────────


# region FUNC__handle_ping
## @purpose  Verb handler: ping → "pong" + exit 0. Живой потребитель — vps_readiness CMD_PING
##           (DevPlan 116 B1 T2 п.1).
## @io       ⇥ args: str (ignored), ctx: _DispatchContext → ⎋ int (0)
## @complexity — O(1)
def _handle_ping(
    _args: str,
    _ctx: _DispatchContext,
) -> int:
    """SSH-connectivity ping: print "pong" (vps_readiness CMD_PING)."""
    print("pong")
    return 0


# endregion FUNC__handle_ping


# region FUNC__handle_exit
## @purpose  Verb handler: exit → 0 (SSH-connectivity no-op).
## @io       ⇥ args: str (ignored), ctx: _DispatchContext → ⎋ int (0)
## @complexity — O(1)
def _handle_exit(
    _args: str,
    _ctx: _DispatchContext,
) -> int:
    """SSH-connectivity no-op exit verb."""
    return 0


# endregion FUNC__handle_exit


# region FUNC__handle_status
## @purpose  Verb handler: status \<project\> → ProjectStatus JSON — канон (D6: found/stub → 0,
##           not_found/error → 1).
## @io       ⇥ args: str (project name, вся строка), ctx: _DispatchContext → ⎋ int exit code
## @complexity — O(1) — file reads + docker ps call
## @invariants
##   - JSON-канон ProjectStatus (to_dict), не raw compose-вывод (D6)
##   - dispatch: args = вся строка после verb; main: args = args.project
def _handle_status(args: str, ctx: _DispatchContext) -> int:
    """ProjectStatus JSON (found/stub → 0, not_found/error → 1, D6)."""
    project = args or ""
    status = ctx.orchestrator.status(project_name=project)
    print(json.dumps(status.to_dict()))
    return 0 if status.status in {"found", "stub"} else 1


# endregion FUNC__handle_status


# ── health verb (B3 fix-forward): read-only docker inspect State.Health.Status ──────
# Слово-контракт remote-probe (project_payload_delivery _build_default_health_probe):
# stdout ровно ОДНО слово; rc=0 для healthy|starting|unhealthy|missing (успешный запрос
# факта), rc=1 только при внутренней ошибке инспекта (daemon недоступен/нет docker/timeout).
_HEALTH_STATE_WORDS: frozenset[str] = frozenset({"healthy", "starting", "unhealthy"})
# Docker CLI: контейнер отсутствует → "Error: No such object: <id>" (факт «missing» получен
# успешно → rc=0); иной rc≠0 (daemon unreachable/нет docker) → внутренняя ошибка → rc=1.
_DOCKER_NO_SUCH_OBJECT = "No such object"


# region FUNC__handle_health
## @purpose  Verb handler: health \<project\> [\<service\>] → read-only docker inspect
##           State.Health.Status через shared/docker_ops.docker_inspect (ТОТ ЖЕ слой, что
##           engine/healthcheck_poll — никаких новых subprocess-обёрток). Слово-контракт:
##           healthy|starting|unhealthy|missing|error; rc=0 для всех кроме error (rc=1) —
##           контракт remote-probe (B3 fix-forward, project_payload_delivery).
##           Read-only гарантия: НЕ пишет audit-записей, НЕ мутирует ничего (только LDD-логи).
## @io       ⇥ args: str ("project [service]"; service опционален, дефолт = project),
##           ctx: _DispatchContext (docker_runner — W4d DI канал docker_inspect; None = прямой
##           subprocess) → ⎋ int exit code
## @complexity — O(1) — один docker inspect (argv, shell=False)
## @invariants
##   - project обязателен (первый токен) → иначе JSON ERROR + exit 1 (fail-fast, паттерн verify)
##   - service = tokens[1] или project (дефолт) — контейнер проекта canonical-name
##   - rc=0 И status ∈ {healthy, starting, unhealthy} → слово + exit 0
##   - rc=0 И иной stdout (""/"<no value>"/"none" — контейнер без healthcheck) → "missing" + exit 0
##   - rc≠0 + "No such object" в stderr → "missing" + exit 0 (контейнер отсутствует — факт получен)
##   - rc≠0 + иное → "error" + exit 1 (внутренняя ошибка инспекта: daemon недоступен, нет docker)
##   - Shell-injection исключён: docker_inspect строит argv-список (shell=False); project/service
##     валидируются dispatch T9.7-блоком ДО handler'а (тот же паттерн, что status/remove)
def _handle_health(args: str, ctx: _DispatchContext) -> int:
    """Read-only verb: docker inspect State.Health.Status → слово-контракт (B3 fix-forward)."""
    tokens = (args or "").split()
    project = tokens[0] if tokens else ""
    service = tokens[1] if len(tokens) > 1 else project
    if not project:
        print(json.dumps({"status": "ERROR", "error": "health requires <project>"}))
        return 1

    logger.info("[IMP:8][health][inspect] project=%s service=%s", project, service)
    result = docker_inspect(service, format="{{.State.Health.Status}}", runner=ctx.docker_runner)
    if result.returncode != 0:
        stderr = result.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if _DOCKER_NO_SUCH_OBJECT in stderr:
            # Контейнер отсутствует — честный факт «missing» (успешный запрос, rc=0 по контракту)
            logger.info("[IMP:7][health][missing] %s — container not found (rc=%d)", service, result.returncode)
            print("missing")
            return 0
        logger.error(
            "[IMP:10][health][error] %s — docker inspect failed (rc=%d): %.200s",
            service,
            result.returncode,
            stderr.strip() or "no stderr",
        )
        print("error")
        return 1

    status = (result.stdout or "").strip()
    if status in _HEALTH_STATE_WORDS:
        logger.info("[IMP:9][health][ok] %s — status=%s", service, status)
        print(status)
        return 0
    # rc=0, но нет health-факта (<no value>/none/пусто — контейнер без healthcheck) → "missing"
    logger.info("[IMP:7][health][missing] %s — no health status (stdout=%r) — treated as missing", service, status)
    print("missing")
    return 0


# endregion FUNC__handle_health


# region FUNC__handle_remove
## @purpose  Verb handler: remove \<project\> → DeployOrchestrator.remove() JSON + exit 0/1.
## @io       ⇥ args: str (project name), ctx: _DispatchContext (purge — только main-путь) → ⎋ int
## @complexity — O(1) — single docker compose call
## @invariants
##   - purge (down -v) доступен ТОЛЬКО из main-пути (dispatch-верб remove не имеет purge-флага)
def _handle_remove(args: str, ctx: _DispatchContext) -> int:
    """DeployOrchestrator.remove() JSON (exit 0/1)."""
    project = args or ""
    # REF-0011 (11-DevPlan W1): rollback/remove под тем же per-project локом, что и receive —
    # гонка «remove vs параллельный receive» ломала payload/state. Reentrant: вложенный
    # orchestrator-путь возьмёт тот же лок без дедлока.
    from core.internal.shared.file_lock import FileLock, FileLockError, platform_lock_path

    try:
        lock = FileLock(platform_lock_path(project), timeout=float(DEPLOY_TIMEOUT), poll_interval=0.5)
        lock.acquire()
    except FileLockError as e:
        print(json.dumps({"status": "FAILED", "error": f"Concurrent deploy blocked: {e}"}))
        return 1
    try:
        result = ctx.orchestrator.remove(project_name=project, purge=ctx.purge)
    finally:
        with contextlib.suppress(Exception):
            lock.release()
    print(json.dumps(result.to_dict()))
    return 0 if result.is_success() else 1


# endregion FUNC__handle_remove


# region FUNC__handle_verify
## @purpose  Verb handler: verify \<node\> [\<project\>] — тонкая оркестрация: HTTPS-верификация
##           доменов (core.internal.verify.domain_verifier) + контракты проекта ПОСЛЕ успешной
##           проверки (K3, DevPlan 137 §5 W4: verify_contracts, L1-блок → [PRACTICES:BLOCK]).
##           170 W4-B3: вынесен из _dispatch (verify-блок ~312-384).
## @io       ⇥ args: str ("node [project]"), ctx: _DispatchContext (runner — DI subprocess-канал)
##           → ⎋ int exit code
## @complexity — O(N) — subprocess verify + per-project contracts scan
## @invariants
##   - verify требует node → иначе JSON ERROR + exit 1 (fail-fast, R5 negative)
##   - D17 (8a4eb6d): split node/project — args целиком НЕ уходит в --node
##   - HTTPS-проверка НЕ прошла (rc != 0) → контракты НЕ исполняются (pass-through rc)
##   - verify без project → только домены (per-project контракты требуют project_dir)
##   - verify_contracts: has_blocking_violation → [PRACTICES:BLOCK] + exit 1; warnings → exit 0
def _handle_verify(args: str, ctx: _DispatchContext) -> int:
    """HTTPS-верификация доменов + контракты проекта (K3) — мост на verify_contracts."""
    parts = (args or "").split()
    node = parts[0] if parts else ""
    project = parts[1] if len(parts) > 1 else ""
    if not node:
        print(json.dumps({"status": "ERROR", "error": "verify requires <node>"}))
        return 1
    # H7 (security hardening): verify-verb валидирует project-name каноническим валидатором
    # ДО path-резолва (projects_base()/project ниже) — защита от path traversal
    # через SSH_ORIGINAL_COMMAND (в отличие от status/remove/receive, verify получал
    # project вторым токеном и обходил T9.7-проверку dispatch).
    if project and not _validate_project_name(project):
        logger.error("[IMP:10][verify][invalid_project] Invalid/reserved project name: %r (H7)", project)
        print(json.dumps({"status": "ERROR", "error": f"Invalid or reserved project name: {project}"}))
        return 1
    platform_root = str(platform_remote_base())
    verify_cmd: list[str] = [
        sys.executable,
        "-m",
        "core.internal.verify.domain_verifier",
        "verify",
        "--node",
        node,
        "--platform-root",
        platform_root,
    ]
    # CI-канал шлёт `verify <node> <project>` (деплой-проектный workflow) —
    # per-project верификация; ранее args целиком уходил в --node (баг: node
    # = "tronyx-vps tronyx-site"). Контракт parser'а (args="<node>") расширен.
    if project:
        verify_cmd += ["--project", project]
    # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
    try:
        proc = ctx.runner(
            verify_cmd,
            capture_output=True,
            text=True,
            timeout=DEPLOY_TIMEOUT,
            check=False,
        )
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        # HTTPS-проверка НЕ прошла → контракты проекта не исполняем (нет смысла блокировать
        # поверх сетевого сбоя; verify-domains — канон DevPlan 125 T1, НЕ трогаем)
        if proc.returncode != 0:
            return proc.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"status": "ERROR", "error": f"verify failed: {e}"}))
        return 1

    # ── W4 (K3, DevPlan 137 §5 W4): контракты проекта ПОСЛЕ успешной HTTPS-проверки ──
    # verify без project (make verify-domains NODE=...) — только домены (per-project контракты
    # требуют project_dir из projects_base). L1-блок → [PRACTICES:BLOCK] + exit 1;
    # warnings → [PRACTICES:PROPOSE]/[PRACTICES:UNMANAGED] + exit 0 (политика §4.5).
    if project:
        from core.internal.deploy.verify_contracts import verify_project_contracts

        report = verify_project_contracts(projects_base() / project)
        n_block = sum(1 for f in report.findings if f.severity == "block")
        logger.info(
            "[IMP:9][verify][contracts] project=%s state=%s findings=%d blocking=%d",
            project,
            report.state,
            len(report.findings),
            n_block,
        )
        if report.has_blocking_violation():
            logger.info(
                "[IMP:9][verify][contracts] BLOCKED project=%s (%d blocking violations)",
                project,
                n_block,
            )
            print(report.format_for_ssh())
            return 1
        if report.has_warnings():
            print(report.format_for_ssh())
        else:
            logger.info("[IMP:9][verify][contracts] OK project=%s — 0 findings", project)
    return 0


# endregion FUNC__handle_verify


# region FUNC__handle_receive
## @purpose  Verb handler: receive [project] [sha] — tar из stdin, версия из аргументов (D5).
## @io       ⇥ args: str ("project sha"), ctx: _DispatchContext (stdin_stream/orchestrator_factory DI)
##           → ⎋ int exit code
## @complexity — O(N) где N = tar entries
## @invariants
##   - version ТОЛЬКО из аргументов (D5): tokens[1] или "latest"
##   - project = tokens[0] или None (фолбэк на ai-platform.yaml name в ReceiveFlow)
def _handle_receive(args: str, ctx: _DispatchContext) -> int:
    """Receive tar из stdin + version из аргументов (D5)."""
    tokens = (args or "").split()
    project = tokens[0] if tokens else None
    version = tokens[1] if len(tokens) > 1 else "latest"
    return ctx.orchestrator.receive(
        project_name=project,
        version=version,
        stream=ctx.stdin_stream,
        orchestrator_factory=ctx.orchestrator_factory,
    )


# endregion FUNC__handle_receive


# region FUNC__handle_rollback
## @purpose  Rollback handler (dispatch verb + main-команда, D8 launch-validation):
##           DeployOrchestrator.rollback() (snapshot-based), JSON → stdout. Единая сигнатура
##           (args: str, ctx: _DispatchContext) — паттерн status/remove/health: dispatch
##           (args = строка после verb) и main (args = "project [snapshot-id]") — ОДИН handler.
## @io       ⇥ args: str ("project [snapshot-id]"), ctx: _DispatchContext → ⎋ int exit code
## @complexity — O(1) — snapshot read + compose rollback
## @invariants
##   - project = первый токен; snapshot_id = второй токен (опционален)
##   - snapshot_id="" → latest snapshot (rollback(project, None))
##   - project обязателен → иначе JSON ERROR + exit 1 (fail-fast, паттерн health)
##   - per-project flock (REF-0011) — тот же лок, что receive/remove
def _handle_rollback(args: str, ctx: _DispatchContext) -> int:
    """Rollback a project (dispatch verb + main-команда, JSON результат)."""
    tokens = (args or "").split()
    project = tokens[0] if tokens else ""
    snapshot_id = tokens[1] if len(tokens) > 1 else ""
    if not project:
        print(json.dumps({"status": "ERROR", "error": "rollback requires <project>"}))
        return 1
    # REF-0011 (11-DevPlan W1): rollback под тем же per-project локом, что и receive/remove —
    # гонка «rollback vs параллельный receive» ломала payload/state. Reentrant per-project.
    from core.internal.shared.file_lock import FileLock, FileLockError, platform_lock_path

    try:
        lock = FileLock(platform_lock_path(project), timeout=float(DEPLOY_TIMEOUT), poll_interval=0.5)
        lock.acquire()
    except FileLockError as e:
        print(json.dumps({"status": "FAILED", "error": f"Concurrent deploy blocked: {e}"}))
        return 1
    try:
        result = ctx.orchestrator.rollback(
            project_name=project,
            snapshot_id=snapshot_id or None,
        )
    finally:
        with contextlib.suppress(Exception):
            lock.release()
    print(json.dumps(result.to_dict()))
    return 0 if result.is_success() else 1


# endregion FUNC__handle_rollback


# region FUNC__VERB_HANDLERS (реестр verb→handler, CANONICAL_VERBS)
# 170 W4-B3: таблица {verb: handler-функция} — ровно CANONICAL_VERBS (shared/verbs.py, U-56).
# Единая сигнатура dispatch-handler'а: (args: str, ctx: _DispatchContext) → int.
_VERB_HANDLERS: dict[str, Callable[[str, _DispatchContext], int]] = {
    "ping": _handle_ping,
    "exit": _handle_exit,
    "status": _handle_status,
    "health": _handle_health,
    "verify": _handle_verify,
    "remove": _handle_remove,
    "receive": _handle_receive,
    "rollback": _handle_rollback,
}
# Runtime-парити: реестр = канонический verb-словарь (U-56, gate test_cli_subcommands_cover_verb_dictionary).
# Добавление verb в CANONICAL_VERBS без handler'а → AssertionError при импорте (fail-fast, D2).
assert set(_VERB_HANDLERS) == set(CANONICAL_VERBS), (
    f"Реестр verb→handler рассинхронизирован с shared/verbs.py: "
    f"handlers={sorted(_VERB_HANDLERS)}, canonical={list(CANONICAL_VERBS)}"
)
# endregion FUNC__VERB_HANDLERS


# region FUNC__dispatch
## @purpose  VPS-side forced-command dispatcher (DevPlan 116 B1 T2, U-04). Читает SSH_ORIGINAL_COMMAND
##           (фолбэк — CLI args), парсит через deploy/ssh_command_parser (T1, DevPlan 118 D3),
##           маршрутизирует verb через реестр _VERB_HANDLERS (170 W4-B3).
##           Тонкий: pre-парсинг + T9.7-валидация + вызов handler'а из реестра.
## @io       ⇥ argv: list[str] (fallback args), env: Mapping[str, str] | None = None (DI, W-H DevPlan 163 —
##           override SSH_ORIGINAL_COMMAND/PROJECTS_BASE; None = os.environ),
##           stdin_stream: BinaryIO | None = None (DI — stdin receive-канал; None = sys.stdin.buffer),
##           orchestrator_factory: Callable[..., DeployOrchestrator] | None = None (DI — фабрика
##           для receive/status; None = DeployOrchestrator),
##           run_cmd: Callable | None = None (DI — subprocess-канал verify; None = subprocess.run),
##           docker_runner: CommandRunner | None = None (DI — subprocess-канал docker_inspect health;
##           None = прямой subprocess, W4d)
##           → ⎋ int exit code
## @complexity — O(N) где N = tar entries для receive, иначе O(1)
## @invariants
##   - SSH_ORIGINAL_COMMAND пуст И args пусты → JSON {"status":"ERROR",...} + exit 1
##   - ConfigValidationError (unknown verb) → JSON-ошибка + exit e.exit_code (D2, инвариант 4)
##   - PlatformError → return e.exit_code (B4-контракт, sys.exit только в main())
##   - ping обязателен: vps_readiness CMD_PING — живой потребитель (DevPlan 116 B1 T2 п.1)
##   - DI-параметры (None → канонические os.environ/sys.stdin/DeployOrchestrator/subprocess.run) —
##     поведение по умолчанию неизменно; тесты передают fake-каналы (0 патчей, W-H)
##   - 170 W4-B3: verb ∉ _VERB_HANDLERS недостижим (parse_ssh_command — только CANONICAL_VERBS);
##     ветки ping/exit/status/health/verify/remove/receive/rollback вынесены в handler'ы
##     (реестр — единственный маршрут)
def _dispatch(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin_stream: BinaryIO | None = None,
    orchestrator_factory: Callable[..., DeployOrchestrator] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    docker_runner: CommandRunner | None = None,
) -> int:
    """Route SSH_ORIGINAL_COMMAND (or CLI fallback args) to the matching verb handler (реестр)."""
    source: Mapping[str, str] = os.environ if env is None else env
    runner = subprocess.run if run_cmd is None else run_cmd
    raw = source.get("SSH_ORIGINAL_COMMAND", "").strip()
    if not raw:
        raw = " ".join(argv).strip()  # фолбэк: CLI args (dispatch status nonexistent → "status nonexistent")

    if not raw:
        print(json.dumps({"status": "ERROR", "error": "empty SSH_ORIGINAL_COMMAND"}))
        return 1

    logger.info("[IMP:8][dispatch][raw] SSH_ORIGINAL_COMMAND=%r", raw)

    try:
        parsed = parse_ssh_command(raw)
    except ConfigValidationError as e:
        # D2: неизвестный verb → JSON-ошибка + честный exit-код (никакого deploy-фолбэка)
        logger.error("[IMP:10][dispatch][unknown_verb] %s", e)
        print(json.dumps({"status": "ERROR", "error": str(e)}))
        return e.exit_code
    except PlatformError as e:
        logger.error("[IMP:10][dispatch][error] %s", e)
        print(json.dumps({"status": "ERROR", "error": str(e)}))
        return e.exit_code

    verb = parsed["verb"]
    args = parsed["args"]
    # W-H (DevPlan 163): фабрика DI (None = DeployOrchestrator) — тесты инжектят
    # projects_base/субкласс вместо патча orchestrator_cli.DeployOrchestrator
    orchestrator = orchestrator_factory() if orchestrator_factory is not None else DeployOrchestrator()

    logger.info("[IMP:9][dispatch][route] verb=%s args=%r", verb, args)

    # ── T9.7 (L-10): validate_project_name ДО маршрутизации для verbs, принимающих проект.
    # Инъекция `;`/`../` в project_name (SSH_ORIGINAL_COMMAND) отсекается здесь — проект
    # не должен влиять на path-резолв/команды. Канон — shared/project_registry (U-56).
    # health — read-only verb, но тот же guard: первый токен = project (паттерн status/remove).
    # rollback (D8) — тот же guard: первый токен = project (первый токен args).
    if verb in {"status", "remove", "receive", "health", "rollback"}:
        project_token = (args or "").split()[0] if (args or "").split() else ""
        if project_token and not _validate_project_name(project_token):
            logger.error("[IMP:10][dispatch][invalid_project] Invalid/reserved project name: %r (T9.7)", project_token)
            print(json.dumps({"status": "ERROR", "error": f"Invalid or reserved project name: {project_token}"}))
            return 1

    # ── Реестр verb→handler (CANONICAL_VERBS, 170 W4-B3) — единственный маршрут диспетчеризации ──
    ctx = _DispatchContext(
        orchestrator=orchestrator,
        stdin_stream=stdin_stream,
        orchestrator_factory=orchestrator_factory,
        runner=runner,
        docker_runner=docker_runner,
    )
    handler = _VERB_HANDLERS[verb]  # parse_ssh_command возвращает только CANONICAL_VERBS (unreachable иначе)
    # args: str | None (ping/exit — None) → "" (все handler'ы нормализуют args or "")
    # 170 W12 C7: IMP:9 на каждом dispatch-завершении (LDD-пробел — только route/ошибки логировались)
    rc = handler(args or "", ctx)
    logger.info("[IMP:9][dispatch][done] verb=%s rc=%d", verb, rc)
    return rc


# endregion FUNC__dispatch


# region FUNC__handle_deliver
## @purpose  Operator-side delivery (DevPlan 116 B1 T5, D1-консистентно): ассемблирует payload через
##           PayloadDeliverer.assemble_payload (публичный API, единственный путь сборки tar.gz — A4),
##           доставляет через ForcedCommandChannel
##           (remote_cmd "receive \<project\> \<version\>" — T2 п.3), печатает JSON-результат с VPS
##           (парсинг stdout deliver), exit 0/1 по нему. НЕ вызывает локальный compose.
## @io       ⇥ args: argparse.Namespace (--project/--version/--host/--user/--key-file/--project-dir)
##           → ⎋ int exit code
## @complexity — O(1) — assemble + single channel deliver
## @invariants
##   - НЕ вызывает orchestrator.deploy() (обход двойного канала: deploy() шаг 4 — локальный compose)
##   - host обязателен; NODE→host резолв — в makefiles/deploy.mk (extract_node_host)
##   - JSON stdout VPS парсится и пробрасывается в stdout; exit по status результата
##     (REF-0003: rc=0 только DEPLOYED/SKIPPED — PARTIAL не успех, FAILED-healthcheck → exit 1)
##   🧐 TRAP[DECISION] · 2026-08-01 · — · deliver НЕ выполняет локальный compose
##   · Rejected: orchestrator.deploy() c ForcedCommandChannel (шаг 4 _deploy_compose — ЛОКАЛЬНЫЙ
##   ·   docker compose up; для remote-деплоя неприменим — compose на VPS выполняет receive)
##   · Reason: единый канал — CI и оператор шлют tar через receive verb; локальный compose
##   ·   после успешной доставки дублировал бы деплой на операторской машине (двойной канал).
##   · Rev: если receive перестанет выполнять compose на VPS — deliver вернёт локальный compose.
# 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · channel_factory DI-шов в _handle_deliver (167 D3)
# · Rejected: прямой вызов ForcedCommandChannel() (тест monkeypatch.setattr класса на модуле)
# · Reason: seam = тестируемость реального channel-вызова (тест передаёт channel_factory=
# ·   вместо monkeypatch.setattr("core.internal.deploy.orchestrator_cli.ForcedCommandChannel"));
# ·   прод — ForcedCommandChannel() по умолчанию, без изменений
# · Rev: если каналы станут фабрикой/реестром — factory заменится резолвером
def _handle_deliver(args: _CliArgs, *, channel_factory: Callable[[], DeliveryChannel] | None = None) -> int:
    """Assemble payload and deliver via ForcedCommandChannel (receive verb)."""
    from core.internal.deploy.payload_deliverer import PayloadDeliverer

    if not args.host:
        print(json.dumps({"status": "ERROR", "error": "deliver requires --host (resolve NODE via extract_node_host)"}))
        return 1

    project_dir = args.project_dir or os.path.join(
        str(projects_base()),
        args.project,
    )
    if not os.path.isdir(project_dir):
        print(json.dumps({"status": "ERROR", "error": f"Project directory not found: {project_dir}"}))
        return 1

    logger.info(
        "[IMP:8][deliver][start] Delivering %s (version=%s) to %s via ForcedCommandChannel",
        args.project,
        args.version,
        args.host,
    )

    deliverer = PayloadDeliverer(projects_base=str(projects_base()))
    payload = deliverer.assemble_payload(
        project_name=args.project,
        version=args.version,
        project_dir=project_dir,
        metadata={"host": args.host, "user": args.user, "key_file": args.key_file},
    )

    channel = channel_factory() if channel_factory is not None else ForcedCommandChannel()
    channel.metadata_defaults = {
        "host": args.host,
        "user": args.user or "ci-deploy",
    }
    if args.key_file:
        channel.metadata_defaults["key_file"] = args.key_file

    delivery_result = channel._retry_deliver(payload)

    if not delivery_result.success:
        logger.error("[IMP:10][deliver][failed] Delivery failed: %s", delivery_result.error_message)
        print(
            json.dumps({
                "status": "FAILED",
                "project": args.project,
                "version": args.version,
                "error": delivery_result.error_message,
            })
        )
        return 1

    # Парсинг JSON-результата receive с VPS (stdout deliver) — честный exit по нему
    vps_json = delivery_result.stdout.strip()
    try:
        result = json.loads(vps_json) if vps_json else {}
    except json.JSONDecodeError:
        logger.warning("[IMP:8][deliver][parse] VPS stdout не JSON — пробрасываю как есть: %.200s", vps_json)
        result = {}

    if result.get("status") == "ERROR" or result.get("status") == "FAILED":
        logger.error("[IMP:10][deliver][vps_failed] VPS receive FAILED: %s", result)
        print(vps_json or json.dumps(result))
        return 1

    # Пробрасываем полный JSON с VPS (содержит project/version/sha/status — AC2)
    print(vps_json if vps_json else json.dumps(result))
    # REF-0003: PARTIAL исключён из rc=0 — неуспешный healthcheck = честный exit 1
    status = result.get("status", "")
    rc = 0 if status in {"DEPLOYED", "SKIPPED"} else 1
    # 170 W12 C7: IMP:9 на завершении deliver (LDD-пробел)
    logger.info("[IMP:9][deliver][done] project=%s version=%s status=%s rc=%d", args.project, args.version, status, rc)
    return rc


# endregion FUNC__handle_deliver


# region FUNC__handle_deploy
## @purpose  main-команда deploy: build channel + orchestrator.deploy() (single project), JSON → stdout.
## @io       ⇥ args: argparse.Namespace (--project/--version/--service/--project-dir/--dry-run/канал),
##              orchestrator: DeployOrchestrator → ⎋ int exit code
## @complexity — O(N) — deploy steps (assemble → deliver → compose → healthcheck → audit)
## @invariants
##   - service = args.service or args.project; project_dir = args.project_dir or projects_base/project
##   - JSON OrchestratorDeployResult → stdout; exit по result.is_success()
def _handle_deploy(args: _CliArgs, orchestrator: DeployOrchestrator) -> int:
    """Deploy a single project (main-команда, JSON результат)."""
    channel = build_channel(args)
    project_dir = args.project_dir or os.path.join(
        str(projects_base()),
        args.project,
    )
    service = args.service or args.project

    result = orchestrator.deploy(
        project_name=args.project,
        channel=channel,
        version=args.version,
        service=service,
        project_dir=project_dir,
        dry_run=args.dry_run,
    )
    print(json.dumps(result.to_dict()))
    return 0 if result.is_success() else 1


# endregion FUNC__handle_deploy


# region FUNC__handle_deploy_many
## @purpose  main-команда deploy-many: build channel + orchestrator.deploy_many() (sequential),
##           JSON-массив → stdout.
## @io       ⇥ args: argparse.Namespace (--projects/--version/--dry-run/канал),
##              orchestrator: DeployOrchestrator → ⎋ int exit code
## @complexity — O(N * M) где N = проекты, M = deploy steps
## @invariants
##   - --projects — comma-separated; пустые токены отбрасываются
##   - exit 1 при ЛЮБОМ неуспешном проекте (1 if failed > 0 else 0)
def _handle_deploy_many(args: _CliArgs, orchestrator: DeployOrchestrator) -> int:
    """Deploy multiple projects sequentially (main-команда, JSON-массив результатов)."""
    channel = build_channel(args)
    projects = [p.strip() for p in args.projects.split(",") if p.strip()]

    results = orchestrator.deploy_many(
        project_names=projects,
        channel=channel,
        version=args.version,
        dry_run=args.dry_run,
    )
    output = [r.to_dict() for r in results]
    print(json.dumps(output))
    failed = sum(1 for r in results if not r.is_success())
    return 1 if failed > 0 else 0


# endregion FUNC__handle_deploy_many


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    ## @purpose — Тонкий диспетчер: argparse → handler (реестр/функция). DeployOrchestrator
    ##            создаётся ЛЕНИВО (170 W4-B3) — dispatch/deliver его не требуют.
    ## @io — ⇥ sys.argv → ⎋ exit code 0/1
    ## @complexity — O(N) for deploy-many, O(1) for other commands
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        # ── dispatch — VPS-side forced-command dispatcher (НЕ требует DeployOrchestrator на этом уровне) ──
        if args.command == "dispatch":
            return _dispatch(args.dispatch_args)
        # ── deliver — операторская доставка (собственный PayloadDeliverer/канал, НЕ DeployOrchestrator) ──
        if args.command == "deliver":
            return _handle_deliver(args)

        # ── Ленивый DeployOrchestrator: только для команд, которым он нужен (170 W4-B3) ──
        orchestrator = DeployOrchestrator()

        if args.command == "receive":
            project = args.project or None
            return orchestrator.receive(project_name=project, version=args.version)
        if args.command == "deploy":
            return _handle_deploy(args, orchestrator)
        if args.command == "deploy-many":
            return _handle_deploy_many(args, orchestrator)
        if args.command == "rollback":
            # D8: единый handler (args: str, ctx) — main-путь собирает строку "project [snapshot-id]"
            return _handle_rollback(
                f"{args.project} {args.snapshot_id}".strip(),
                _DispatchContext(orchestrator=orchestrator),
            )
        if args.command == "status":
            return _handle_status(args.project, _DispatchContext(orchestrator=orchestrator))
        if args.command == "health":
            # Read-only verb (B3 fix-forward): orchestrator в ctx не используется — единая
            # сигнатура handler'а (args: str, ctx). --service опционален (дефолт = project).
            health_args = f"{args.project} {args.service}".strip() if args.service else args.project
            return _handle_health(health_args, _DispatchContext(orchestrator=orchestrator))
        if args.command == "remove":
            return _handle_remove(
                args.project,
                _DispatchContext(orchestrator=orchestrator, purge=args.purge),
            )

    except PlatformError as e:
        # B4-контракт: business-функции не вызывают sys.exit; PlatformError → return e.exit_code
        logger.error("[IMP:10][main] Platform error (exit=%d): %s", e.exit_code, e)
        return e.exit_code

    return 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
