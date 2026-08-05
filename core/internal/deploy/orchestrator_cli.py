#!/usr/bin/env python3
# GREP_SUMMARY: orchestrator-cli, cli, dispatch, receive, deliver, deploy-many, status, remove, verify, ping, entrypoint, SSH_ORIGINAL_COMMAND
# STRUCTURE: ▶ main() → argparse → dispatch(receive|status|verify|remove|ping|exit) | deliver | receive | deploy | deploy-many | rollback | status | remove → sys.exit(0|1)
"""
CLI entrypoint for DeployOrchestrator. Commands: dispatch, deliver, receive, deploy, deploy-many, rollback, status, remove.

`dispatch` — VPS-side forced-command dispatcher (DevPlan 116 B1): reads SSH_ORIGINAL_COMMAND
  (фолбэк — CLI args), парсит через deploy/ssh_command_parser (T1, DevPlan 118 D3), маршрутизирует verb:
  ping → "pong"; exit → 0; status → ProjectStatus JSON (exit 0/1); verify → verify-domains.sh;
  remove → DeployOrchestrator.remove(); receive → tar из stdin + DeployOrchestrator.receive().

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
## @purpose  CLI entrypoint for DeployOrchestrator. Replaces the legacy shell deploy pipeline and provides
##           direct access to all orchestrator operations from command line.
##           Command `dispatch` — VPS-side forced-command dispatcher по SSH_ORIGINAL_COMMAND (DevPlan 116
##           B1 T2, U-04): receive/status/verify/remove/ping/exit через единый dispatcher.
##           Command `receive` — читает Payload из stdin (tar) + версию из аргументов (D5), вызывает
##           DeployOrchestrator.receive().
##           Command `deliver` — операторская доставка через ForcedCommandChannel (T5).
##           Command `deploy-many` — project_names + channel, делегирует DeployOrchestrator.deploy_many().
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
## @rationale DevPlan 089 T6.6: единый CLI entrypoint заменяет legacy shell deploy pipeline.
##            DevPlan 116 B1 T2 (U-04): receive игнорировал SSH_ORIGINAL_COMMAND — CI-верификация
##            была фиктивна; dispatch диспетчеризует SSH_ORIGINAL_COMMAND (receive|status|verify|
##            remove|ping|exit) через единый парсер (T1).
## @changes 2026-07-30 | DevPlan 089 T6.6 — Created
##           2026-08-01 | DevPlan 116 B1 T2/T3/T5 — +dispatch, +deliver; status exit 0/1 (D6);
##                       receive с версией из аргументов (D5); deploy-many дефолт LocalChannel (D7)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from core.internal.deploy.channels import ForcedCommandChannel, LocalChannel, SCPChannel
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.deploy.ssh_command_parser import parse_ssh_command
from core.internal.shared.deploy_paths import platform_remote_base, projects_base
from core.internal.shared.exceptions import ConfigValidationError, PlatformError

# T9.7 (L-10): валидация project_name в dispatch ДО маршрутизации (канон shared/project_registry)
from core.internal.shared.project_registry import validate_project_name as _validate_project_name

logger = logging.getLogger(__name__)

# Путь к shell-фасаду verify (языковая политика: тонкая оркестрация shell-фасада допустима)
_VERIFY_DOMAINS_SH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "internal",
    "verify",
    "verify-domains.sh",
)


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

    # ── remove ──
    rm = sub.add_parser("remove", help="Remove project containers")
    rm.add_argument("--project", required=True, help="Project name")
    rm.add_argument("--purge", action="store_true", help="Remove compose volumes (down -v)")

    return parser


# endregion FUNC_build_parser


# region FUNC_build_channel
def build_channel(args: argparse.Namespace) -> SCPChannel | ForcedCommandChannel | LocalChannel:
    """Build a delivery channel from CLI args.

    Args:
        args: Parsed CLI arguments.

    Returns:
        SCPChannel, ForcedCommandChannel or LocalChannel.
        Дефолт (D7, DevPlan 116 B1 T6): LocalChannel при отсутствии host/--scp/--forced-command —
        на-ноде операция (bootstrap deploy-many), SCP-доставка самой себе бессмысленна.
    """
    use_forced = args.forced_command if hasattr(args, "forced_command") else False
    use_scp = args.scp if hasattr(args, "scp") else False
    host = getattr(args, "host", "")
    user = getattr(args, "user", "")
    key_file = getattr(args, "key_file", "")
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


# region FUNC__dispatch
## @purpose  VPS-side forced-command dispatcher (DevPlan 116 B1 T2, U-04). Читает SSH_ORIGINAL_COMMAND
##           (фолбэк — CLI args), парсит через deploy/ssh_command_parser (T1, DevPlan 118 D3), маршрутизирует verb:
##           ping → "pong" 0; exit → 0; status \<project\> → ProjectStatus JSON (exit 0/1, D6);
##           remove \<project\> → DeployOrchestrator.remove() exit 0/1; verify \<node\> →
##           verify-domains.sh (pass-through exit); receive [project] [sha] → DeployOrchestrator.receive().
## @io       ⇥ argv: list[str] (fallback args) → ⎋ int exit code
## @complexity — O(N) где N = tar entries для receive, иначе O(1)
## @invariants
##   - SSH_ORIGINAL_COMMAND пуст И args пусты → JSON {"status":"ERROR",...} + exit 1
##   - ConfigValidationError (unknown verb) → JSON-ошибка + exit e.exit_code (D2, инвариант 4)
##   - PlatformError → return e.exit_code (B4-контракт, sys.exit только в main())
##   - ping обязателен: vps_readiness CMD_PING — живой потребитель (DevPlan 116 B1 T2 п.1)
def _dispatch(argv: list[str]) -> int:
    """Route SSH_ORIGINAL_COMMAND (or CLI fallback args) to the matching verb handler."""
    raw = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
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
    orchestrator = DeployOrchestrator()

    logger.info("[IMP:9][dispatch][route] verb=%s args=%r", verb, args)

    # ── T9.7 (L-10): validate_project_name ДО маршрутизации для verbs, принимающих проект.
    # Инъекция `;`/`../` в project_name (SSH_ORIGINAL_COMMAND) отсекается здесь — проект
    # не должен влиять на path-резолв/команды. Канон — shared/project_registry (U-56).
    if verb in ("status", "remove", "receive"):
        project_token = (args or "").split()[0] if (args or "").split() else ""
        if project_token and not _validate_project_name(project_token):
            logger.error("[IMP:10][dispatch][invalid_project] Invalid/reserved project name: %r (T9.7)", project_token)
            print(json.dumps({"status": "ERROR", "error": f"Invalid or reserved project name: {project_token}"}))
            return 1

    # ── ping: vps_readiness CMD_PING (живой потребитель) ──
    if verb == "ping":
        print("pong")
        return 0

    # ── exit: SSH-connectivity no-op ──
    if verb == "exit":
        return 0

    # ── status \<project\>: ProjectStatus JSON — канон (D6: found/stub → 0, not_found/error → 1) ──
    if verb == "status":
        project = args or ""
        status = orchestrator.status(project_name=project)
        print(json.dumps(status.to_dict()))
        return 0 if status.status in ("found", "stub") else 1

    # ── remove \<project\> ──
    if verb == "remove":
        project = args or ""
        result = orchestrator.remove(project_name=project)
        print(json.dumps(result.to_dict()))
        return 0 if result.is_success() else 1

    # ── verify \<node\> [\<project\>]: thin orchestration of shell facade (language policy allows) ──
    if verb == "verify":
        parts = (args or "").split()
        node = parts[0] if parts else ""
        project = parts[1] if len(parts) > 1 else ""
        if not node:
            print(json.dumps({"status": "ERROR", "error": "verify requires <node>"}))
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
        try:
            proc = subprocess.run(
                verify_cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, end="", file=sys.stderr)
            return proc.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            print(json.dumps({"status": "ERROR", "error": f"verify failed: {e}"}))
            return 1

    # ── receive [project] [sha]: tar из stdin, версия из аргументов (D5) ──
    if verb == "receive":
        tokens = (args or "").split()
        project = tokens[0] if tokens else None
        version = tokens[1] if len(tokens) > 1 else "latest"
        return orchestrator.receive(project_name=project, version=version)

    # Unreachable: parse_ssh_command возвращает только CANONICAL_VERBS
    return 1


# endregion FUNC__dispatch


# region FUNC__deliver
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
##   🧐 TRAP[DECISION] · 2026-08-01 · — · deliver НЕ выполняет локальный compose
##   · Rejected: orchestrator.deploy() c ForcedCommandChannel (шаг 4 _deploy_compose — ЛОКАЛЬНЫЙ
##   ·   docker compose up; для remote-деплоя неприменим — compose на VPS выполняет receive)
##   · Reason: единый канал — CI и оператор шлют tar через receive verb; локальный compose
##   ·   после успешной доставки дублировал бы деплой на операторской машине (двойной канал).
##   · Rev: если receive перестанет выполнять compose на VPS — deliver вернёт локальный compose.
def _deliver(args: argparse.Namespace) -> int:
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

    channel = ForcedCommandChannel()
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
            json.dumps(
                {
                    "status": "FAILED",
                    "project": args.project,
                    "version": args.version,
                    "error": delivery_result.error_message,
                }
            )
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
    status = result.get("status", "")
    return 0 if status in ("DEPLOYED", "PARTIAL", "SKIPPED") else 1


# endregion FUNC__deliver


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    ## @purpose — Dispatch to command handler based on argparse.
    ## @io — ⇥ sys.argv → ⎋ exit code 0/1
    ## @complexity — O(N) for deploy-many, O(1) for other commands
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    args = parser.parse_args(argv)

    orchestrator = DeployOrchestrator()

    try:
        # ── dispatch — VPS-side forced-command dispatcher ──
        if args.command == "dispatch":
            return _dispatch(args.dispatch_args)

        # ── deliver — operator-side ForcedCommandChannel ──
        if args.command == "deliver":
            return _deliver(args)

        # ── receive — VPS-side tar reader (версия из аргументов, D5) ──
        if args.command == "receive":
            project = args.project or None
            return orchestrator.receive(project_name=project, version=args.version)

        # ── deploy ──
        if args.command == "deploy":
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

        # ── deploy-many ──
        if args.command == "deploy-many":
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

        # ── rollback ──
        if args.command == "rollback":
            result = orchestrator.rollback(
                project_name=args.project,
                snapshot_id=args.snapshot_id or None,
            )
            print(json.dumps(result.to_dict()))
            return 0 if result.is_success() else 1

        # ── status — честные exit-коды (D6: found/stub → 0, not_found/error → 1) ──
        if args.command == "status":
            status = orchestrator.status(project_name=args.project)
            print(json.dumps(status.to_dict()))
            return 0 if status.status in ("found", "stub") else 1

        # ── remove ──
        if args.command == "remove":
            result = orchestrator.remove(
                project_name=args.project,
                purge=getattr(args, "purge", False),
            )
            print(json.dumps(result.to_dict()))
            return 0 if result.is_success() else 1

    except PlatformError as e:
        # B4-контракт: business-функции не вызывают sys.exit; PlatformError → return e.exit_code
        logger.error("[IMP:10][main] Platform error (exit=%d): %s", e.exit_code, e)
        return e.exit_code

    return 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
