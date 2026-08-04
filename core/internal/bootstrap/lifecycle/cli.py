#!/usr/bin/env python3
# GREP_SUMMARY: lifecycle-cli, build-parser, main, run-init-mode, run-update-mode, state-machine, node-lifecycle
# STRUCTURE: ▶ build_parser ┌--mode/--dry-run/--resume/--force/--run-phase/--node-*┐ → ⚡ main ┌env inject → StateMachine → validate env → dispatch┐ → ◇ run_init_mode (9 φ) │ ◇ run_update_mode (5 φ) → ⎋ exit {0,1}
# region MODULE_CONTRACT
## @purpose  CLI/main слой lifecycle state machine — build_parser, main, run_init_mode,
##           run_update_mode. Извлечён из state_machine.py (B9 T1, U-08) — state_machine.py
##           остаётся чистой оркестрацией, CLI живёт здесь.
## @scope    cli.py: build_parser (:1191), main (:1260), run_init_mode (ex-_run_init_mode :1390),
##           run_update_mode (ex-_run_update_mode :1465). Вызывается через
##           `python3 lifecycle/cli.py --mode ...` (node-lifecycle.sh CS-7) и через
##           compat-заглушку state_machine.py (`python3 lifecycle/state_machine.py --mode ...`).
## @invariants
##   - main() -> int; sys.exit ТОЛЬКО в __main__ (контракт core/AGENTS.md)
##   - run_init/run_update: 9/5 фаз последовательно с dependency-checking; done-фазы SKIP
##   - Фаза не начинает выполнение при невыполненных dependency/precondition (BLOCKING)
##   - Audit log + Telegram notification вызываются после полного run (helpers/reporting)
## @rationale DevPlan 116 B9 D1/D2: CLI (~340 LOC) вынесен из state_machine-монолита —
##            state_machine.py ≤ 1200 LOC под LOC-гейтом (T6.2).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

from core.internal.bootstrap.lifecycle.helpers.reporting import send_telegram, write_audit_log
from core.internal.bootstrap.lifecycle.state_machine import (
    BootstrapPhase,
    PhaseDependencyError,
    PhasePreconditionError,
    StateMachine,
    StepState,
    phase_is_done,
)

# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"


# region FUNC_build_parser
## @purpose — CLI argument parser for state_machine.py lifecycle CLI.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Bootstrap/Update State Machine — node lifecycle orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Init mode (full bootstrap):
  python3 cli.py --mode init --node-name server1 --node-yaml /opt/node-configs/server1/node.yaml

  # Update mode (incremental):
  python3 cli.py --mode update --node-name server1

  # Resume from last checkpoint:
  python3 cli.py --mode init --resume

  # Force reset + run:
  python3 cli.py --mode init --force --node-name server1

  # Dry run (print plan, no mutations):
  python3 cli.py --mode init --node-name server1 --dry-run
""",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to state JSON file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--mode",
        choices=["init", "update"],
        default="init",
        help="Run mode: init (full bootstrap) or update (incremental)",
    )
    parser.add_argument(
        "--run-phase",
        type=str,
        help="Run a specific phase by name (e.g., system_bootstrap, secrets_provision, deploy_services)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print execution plan, no mutations")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--force", action="store_true", help="Clear all checkpoints before running")
    parser.add_argument("--node-name", help="Node name (NODE_NAME)")
    parser.add_argument("--node-yaml", help="Path to node.yaml (NODE_YAML)")
    parser.add_argument("--owner-key", help="Platform owner SSH public key (PLATFORM_OWNER_KEY)")
    parser.add_argument("--ci-deploy-key", help="CI deploy SSH public key (PLATFORM_CI_DEPLOY_KEY)")
    parser.add_argument("--tor-enabled", choices=["true", "false"], default=None, help="Override TOR_ENABLED")
    parser.add_argument("--tor-bridges-file", help="Path to Tor bridges file")
    parser.add_argument("--skip-tor-verify", action="store_true", help="Skip Tor circuit verification")
    parser.add_argument("--ghcr-token", help="GHCR pull token for docker login")
    parser.add_argument("--extra-ports", help="Extra firewall ports (space-separated)")
    parser.add_argument("--bot-token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Telegram chat ID")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8118", help="Telegram proxy URL")
    parser.add_argument("--auto-reconcile", action="store_true", help="Auto-reconcile after converge")
    parser.add_argument("--context", help="Deployment context name (CONTEXT — DevPlan 047)")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
## @purpose — Top-level orchestrator for state machine CLI.
##            Handles: --dry-run, --force, --resume, --run-phase, mode dispatch.
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(N * M) where N = phases, M = per-phase operations
def main() -> int:
    """CLI entry point. Parses args, creates StateMachine, dispatches to mode."""
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # ── Inject env vars from CLI args ──
    if args.node_name:
        os.environ.setdefault("NODE_NAME", args.node_name)
    if args.node_yaml:
        os.environ.setdefault("NODE_YAML", args.node_yaml)
    if args.owner_key:
        os.environ.setdefault("PLATFORM_OWNER_KEY", args.owner_key)
    if args.ci_deploy_key:
        os.environ.setdefault("PLATFORM_CI_DEPLOY_KEY", args.ci_deploy_key)
    if args.tor_enabled is not None:
        os.environ["TOR_ENABLED"] = args.tor_enabled
    if args.ghcr_token:
        os.environ.setdefault("GHCR_PULL_TOKEN", args.ghcr_token)
    if args.bot_token:
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", args.bot_token)
    if args.chat_id:
        os.environ.setdefault("TELEGRAM_CHAT_ID", args.chat_id)
    if args.proxy_url:
        os.environ.setdefault("TELEGRAM_PROXY_URL", args.proxy_url)
    if args.tor_bridges_file:
        os.environ.setdefault("TOR_BRIDGES_FILE", args.tor_bridges_file)
    if args.skip_tor_verify:
        os.environ["SKIP_TOR_VERIFY"] = "true"
    if args.extra_ports:
        os.environ.setdefault("FIREWALL_EXTRA_PORTS", args.extra_ports)
    if args.auto_reconcile:
        os.environ["AUTO_RECONCILE"] = "true"
    # DevPlan 047: --context CLI arg → CONTEXT env var
    if args.context:
        os.environ.setdefault("CONTEXT", args.context)

    # Create state machine
    sm = StateMachine(state_file_path=args.state_file)

    # Detect CORE_DIR from PLATFORM_ROOT or default
    platform_root = str(platform_remote_base())
    core_dir = os.environ.get("CORE_DIR", os.path.join(platform_root, "core"))
    sm.core_dir = core_dir

    # ── --force: clear state ──
    if args.force:
        logger.info("[IMP:9][main] --force: Clearing state")
        sm.reset()

    # ── --dry-run: print plan, no mutations ──
    if args.dry_run:
        sm.setup_state(mode=args.mode, node=os.environ.get("NODE_NAME"))
        print(sm.dry_run_plan(), file=sys.stderr)
        return 0

    # ── Validate env (init mode) ──
    if args.mode == "init":
        required_vars = ["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]
        # PLATFORM_CI_DEPLOY_KEY is semi-optional — warn if missing
        ci_key_present = bool(os.environ.get("PLATFORM_CI_DEPLOY_KEY", "").strip())
        if not ci_key_present:
            logger.warning("[IMP:7][main] PLATFORM_CI_DEPLOY_KEY not set — ci-deploy user will have no deploy key")
        if not sm.validate_bootstrap_env(required_vars):
            return 1

    # ── Setup state if fresh or mode changed ──
    if sm.state.mode != args.mode or sm.state.current_step == 0:
        sm.setup_state(mode=args.mode, node=os.environ.get("NODE_NAME"))
    elif args.resume:
        logger.info("[IMP:8][main] --resume: Continuing from step %d", sm.state.current_step)

    # ── --run-phase: execute single phase ──
    if args.run_phase:
        if args.run_phase not in BootstrapPhase.ALL_PHASES:
            logger.error(
                "[IMP:10][main] Unknown phase '%s'. Valid phases: %s",
                args.run_phase,
                ", ".join(sorted(BootstrapPhase.ALL_PHASES)),
            )
            return 1
        logger.info("[IMP:9][main] Running single phase: %s", args.run_phase)
        try:
            sm.execute_phase(args.run_phase)
            logger.info("[IMP:9][main] Phase '%s' completed successfully", args.run_phase)
            return 0
        except (PhaseDependencyError, PhasePreconditionError, PlatformFatalError) as e:
            logger.error("[IMP:10][main] Phase '%s' FAILED: %s", args.run_phase, e)
            return 1

    # ── D6 (волна 117): preflight — ТОЛЬКО при наличии pending/WARN-фаз ──
    # Если state.json показывает все фазы done (без done_with_warnings) — preflight
    # пропускается ([IMP:9] лог). Вызов переехал из node-lifecycle.sh:60-64 в cli.py
    # (node-lifecycle.sh остаётся тонким фасадом; решение по статусам — у state_machine).
    if args.mode == "init":
        preflight_rc = _maybe_run_preflight(sm)
        if preflight_rc != 0:
            return preflight_rc

    # ── Dispatch full mode run ──
    try:
        if args.mode == "init":
            return run_init_mode(sm)
        return run_update_mode(sm)
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


# endregion FUNC_main


# region FUNC_forced_command_smoke
## @purpose  Post-bootstrap forced-command ping smoke (DevPlan 125 T3, FL20) — проверить,
##           что CI-деплой канал не мёртв СРАЗУ после bootstrap, а не при первом CI-деплое.
##           Проверки: (1) статика — ci-deploy authorized_keys содержит forced-command entry
##           (orchestrator_cli dispatch + restrict — единственный писатель users.py, 117 D1);
##           (2) runtime — `python3 -m core.internal.deploy.orchestrator_cli dispatch ping`
##           локально: тот же код-путь, что sshd exec под forced-command (SSH-слой покрыт
##           vps_readiness pre-flight в deploy.mk).
## @io       ⇥ None → ⎋ bool (True = канал готов; НЕ блокирует bootstrap при False — warning)
## @complexity O(1) + 1 subprocess
## @invariants
##   - Non-blocking: FAIL → warning + КРУПНЫЙ print в stderr, exit-код bootstrap не меняется
##   - Статика падает gracefull: authorized_keys не читается → warning, канал считается dead
##   - Runtime использует платформенный python (sys.executable) — тот же интерпретатор
def _forced_command_smoke() -> bool:
    """Smoke forced-command ping после bootstrap (FL20, DevPlan 125 T3)."""
    base = str(platform_remote_base())
    ok = True

    # ── 1. Статика: authorized_keys entry ──
    # ~ci-deploy резолвится через passwd (os.path.expanduser) — без хардкод-литерала
    # (гейт test_gate_no_hardcoded_local_paths: /home/<user> — RED)
    auth_keys = os.path.join(os.path.expanduser("~ci-deploy"), ".ssh", "authorized_keys")
    try:
        content = Path(auth_keys).read_text()
        if "orchestrator_cli dispatch" in content and "restrict" in content:
            logger.info("[IMP:9][smoke] forced-command authorized_keys: entry OK (%s)", auth_keys)
        else:
            logger.warning(
                "[IMP:7][smoke] forced-command authorized_keys: entry MISSING (%s) — CI-деплой канал мёртв",
                auth_keys,
            )
            ok = False
    except OSError as e:
        logger.warning("[IMP:7][smoke] forced-command authorized_keys unreadable: %s — %s", auth_keys, e)
        ok = False

    # ── 2. Runtime: dispatch ping (тот же код-путь, что sshd forced-command) ──
    try:
        r = subprocess.run(
            [sys.executable, "-m", "core.internal.deploy.orchestrator_cli", "dispatch", "ping"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=base,
        )
        if "pong" in r.stdout:
            logger.info("[IMP:9][smoke] forced-command ping: OK (dispatch ping → pong)")
        else:
            logger.warning(
                "[IMP:7][smoke] forced-command ping: FAIL (rc=%s out=%r) — orchestrator_cli dispatch не отвечает",
                r.returncode,
                r.stdout[:80],
            )
            ok = False
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][smoke] forced-command ping: ERROR — %s", e)
        ok = False

    if ok:
        print("[IMP:9][smoke] FORCED-COMMAND PING: OK — CI-деплой канал готов", file=sys.stderr)
    else:
        # КРУПНО, но не блокирует bootstrap (vps_readiness pre-flight перепроверит при деплое)
        print(
            "🚨 [IMP:10][smoke] FORCED-COMMAND PING: FAIL — CI-деплой будет невозможен (см. лог выше)", file=sys.stderr
        )
    return ok


# endregion FUNC_forced_command_smoke


# region FUNC_run_init_mode
## @purpose — Execute all init mode phases (9 phases from BootstrapPhase enum).
## @io — ⇥ sm: StateMachine → ⎋ int exit code
## @complexity — O(N * M) where N = 9 phases, M = per-phase operations
def run_init_mode(sm: StateMachine) -> int:
    """Execute all init mode phases (9 phases from BootstrapPhase enum)."""
    init_phases = BootstrapPhase.INIT_PHASE_ORDER
    total = len(init_phases)
    for i, phase in enumerate(init_phases, 1):
        logger.info("[IMP:9][run_init] Phase %d/%d: %s", i, total, phase)

        # ── Check if already done ──
        phase_state = sm.state.steps.get(phase)
        if phase_state is not None:
            if isinstance(phase_state, dict):
                # done_with_warnings НЕ считается done (волна 117 D5) — фаза перевыполняется.
                # dict-представление: done-ключ true только при status == "done" (пишется ниже).
                if phase_state.get("status") == "done" or (
                    phase_state.get("done", False) and phase_state.get("status") in (None, "done")
                ):
                    logger.info("[IMP:7][run_init] Phase %s already done — skipping", phase)
                    continue
            elif phase_state.status == "done":
                logger.info("[IMP:7][run_init] Phase %s already done — skipping", phase)
                continue

        try:
            result = sm.execute_phase(phase)
            # ── Mark phase status (волна 117 D5): WARN-семантика ──
            # result=True → done; result=False (non-fatal issues) → done_with_warnings,
            # который НЕ считается done → фаза перевыполняется при следующем init.
            if result:
                _mark_phase_success(sm, phase, current_index=i)
                logger.info("[IMP:9][run_init] Phase %s completed successfully", phase)
            else:
                _mark_phase_with_warnings(sm, phase)
                logger.warning(
                    "[IMP:7][run_init] Phase %s completed WITH WARNINGS (done_with_warnings) — "
                    "will be re-executed on next init",
                    phase,
                )
        except PhaseDependencyError as e:
            logger.error("[IMP:10][run_init] Dependency error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code if hasattr(e, "exit_code") else 1
        except PhasePreconditionError as e:
            logger.error("[IMP:10][run_init] Precondition failed for phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return 1
        except PlatformFatalError as e:
            logger.critical("[IMP:10][run_init] Fatal error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code

    # ── Post-run: forced-command ping smoke (DevPlan 125 T3, FL20) ──
    # Мёртвый CI-канал обнаруживается в финале bootstrap, а не при первом CI-деплое.
    # Non-blocking: FAIL → warning + КРУПНЫЙ stderr-print, exit 0 сохраняется.
    _forced_command_smoke()

    # ── Post-run: audit log + Telegram notification ──
    write_audit_log(sm)
    send_telegram(sm)

    logger.info("[IMP:10][run_init] All %d init phases completed successfully", total)
    return 0


# endregion FUNC_run_init_mode


# region FUNC_run_update_mode
## @purpose — Execute all update mode phases (5 phases from BootstrapPhase enum).
## @io — ⇥ sm: StateMachine → ⎋ int exit code
## @complexity — O(N * M) where N = 5 phases, M = per-phase operations
def run_update_mode(sm: StateMachine) -> int:
    """Execute all update mode phases (5 phases from BootstrapPhase enum)."""
    update_phases = BootstrapPhase.UPDATE_PHASE_ORDER
    total = len(update_phases)
    for i, phase in enumerate(update_phases, 1):
        logger.info("[IMP:9][run_update] Phase %d/%d: %s", i, total, phase)

        # ── Check if already done ──
        phase_state = sm.state.steps.get(phase)
        if phase_state is not None:
            if isinstance(phase_state, dict):
                # done_with_warnings НЕ считается done (волна 117 D5)
                if phase_state.get("status") == "done" or (
                    phase_state.get("done", False) and phase_state.get("status") in (None, "done")
                ):
                    logger.info("[IMP:7][run_update] Phase %s already done — skipping", phase)
                    continue
            elif phase_state.status == "done":
                logger.info("[IMP:7][run_update] Phase %s already done — skipping", phase)
                continue

        try:
            result = sm.execute_phase(phase)
            # ── WARN-семантика (волна 117 D5): result=False → done_with_warnings (НЕ done) ──
            if result:
                _mark_phase_success(sm, phase, current_index=i)
                logger.info("[IMP:9][run_update] Phase %s completed successfully", phase)
            else:
                _mark_phase_with_warnings(sm, phase)
                logger.warning(
                    "[IMP:7][run_update] Phase %s completed WITH WARNINGS (done_with_warnings) — "
                    "will be re-executed on next update",
                    phase,
                )
        except PhaseDependencyError as e:
            logger.error("[IMP:10][run_update] Dependency error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code if hasattr(e, "exit_code") else 1
        except PhasePreconditionError as e:
            logger.error("[IMP:10][run_update] Precondition failed for phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return 1
        except PlatformFatalError as e:
            logger.critical("[IMP:10][run_update] Fatal error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code

    # ── Post-run: audit log + Telegram notification ──
    write_audit_log(sm)
    send_telegram(sm)

    logger.info("[IMP:10][run_update] All %d update phases completed successfully", total)
    return 0


# endregion FUNC_run_update_mode


# region FUNC__mark_phase_success
## @purpose — Mark a phase as completed successfully and advance current_step honestly.
##            Волна 117 D5: current_step больше НЕ всегда 0 — обновляется на индекс последней
##            успешно завершённой фазы (для resume-диагностики и детекции «уже init» в main()).
##            При фейле — не вызывается (текущее значение сохраняется для resume-диагностики).
## @io — ⇥ sm: StateMachine, phase: str, current_index: int (1-based) → ⎋ None
## @complexity — O(1) + state.save()
## @invariants
##   - current_step = 1-based индекс последней успешно завершённой фазы (0 = not started)
def _mark_phase_success(sm: StateMachine, phase: str, current_index: int) -> None:
    """Mark a phase done + advance current_step (волна 117 D5 — честный current_step)."""
    entry = sm.state.steps.get(phase)
    if isinstance(entry, dict):
        entry["done"] = True
        entry["status"] = "done"
    elif entry is not None:
        entry.status = "done"
    else:
        # StepState, НЕ raw dict: BootstrapState.to_dict() вызывает v.to_dict()
        # на каждом элементе steps — raw-dict крэшит save (латентный баг,
        # воспроизводится при отсутствующей фазе на resume).
        sm.state.steps[phase] = StepState(name=phase, status="done", done=True)
    sm.state.current_step = current_index
    sm.save()
    logger.info("[IMP:9][state_mark] Phase %s marked done (current_step=%d)", phase, current_index)


# endregion FUNC__mark_phase_success


# region FUNC__mark_phase_with_warnings
## @purpose — Mark a phase as done_with_warnings (НЕ done) and record the warning in state.
##            Волна 117 D5: фаза с non-fatal issues (return False) получает статус
##            done_with_warnings — НЕ считается done → перевыполняется при следующем init.
##            Предупреждение сохраняется в state (per-phase warnings + top-level list).
## @io — ⇥ sm: StateMachine, phase: str → ⎋ None
## @complexity — O(1) + state.save()
## @invariants
##   - current_step НЕ продвигается (фаза не завершена успешно — resume-диагностика)
##   - done-ключ dict-представления = False (единый phase_is_done контракт)
def _mark_phase_with_warnings(sm: StateMachine, phase: str) -> None:
    """Mark a phase done_with_warnings (re-run required) — волна 117 D5."""
    warn_msg = f"Phase {phase} completed with non-fatal issues (returned False) — will be re-executed on next run"
    entry = sm.state.steps.get(phase)
    if isinstance(entry, dict):
        entry["done"] = False
        entry["status"] = "done_with_warnings"
        entry.setdefault("warnings", []).append(warn_msg)
    elif entry is not None:
        entry.status = "done_with_warnings"
        entry.warnings.append(warn_msg)
    else:
        # StepState, НЕ raw dict (см. _mark_phase_success — to_dict() контракт).
        sm.state.steps[phase] = StepState(name=phase, status="done_with_warnings", done=False, warnings=[warn_msg])
    sm.state.warnings.append(warn_msg)
    sm.save()
    logger.warning("[IMP:7][state_mark] Phase %s marked done_with_warnings (re-run required)", phase)


# endregion FUNC__mark_phase_with_warnings


# region FUNC__maybe_run_preflight
## @purpose — Run preflight checks ONLY when there are pending/WARN phases (волна 117 D6).
##            Если state показывает все фазы done (без done_with_warnings) — preflight
##            пропускается с [IMP:9] логом. Вызов переехал из node-lifecycle.sh:60-64.
## @io — ⇥ sm: StateMachine → ⎋ int (0 = ok/skipped, 1 = preflight FAILED)
## @complexity — O(N) phases + O(1) preflight subprocess
## @invariants
##   - SKIP_PREFLIGHT env → skip (backward-compat с node-lifecycle.sh)
##   - preflight.py отсутствует → WARN + skip (non-fatal)
##   - Все фазы done (status == "done") → skip с [IMP:9]
##   - Preflight FAIL (FATAL probe) → return 1 (abort init, как node-lifecycle.sh:62)
##   - --parse-warnings второй вызов: warnings печатаются, НЕ влияют на exit
def _maybe_run_preflight(sm: StateMachine) -> int:
    """Run preflight unless all init phases are already done (D6, волна 117)."""
    if os.environ.get("SKIP_PREFLIGHT"):
        logger.info("[IMP:7][preflight] SKIP_PREFLIGHT set — skipping preflight")
        return 0

    core_dir = sm.core_dir or os.environ.get("CORE_DIR", str(platform_remote_base() / "core"))
    preflight_script = os.path.join(core_dir, "internal", "bootstrap", "preflight.py")
    if not os.path.isfile(preflight_script):
        logger.warning("[IMP:7][preflight] preflight.py not found at %s — skipping", preflight_script)
        return 0

    # ── D6: все фазы done (без WARN-статусов) → preflight не нужен ──
    all_done = all(phase_is_done(sm.state.steps.get(pv)) for pv in BootstrapPhase.phase_list("init"))
    if all_done:
        logger.info("[IMP:9][preflight] All init phases done — preflight skipped (D6)")
        return 0

    # ── Есть pending/WARN-фазы → выполнить (сохранённый путь node-lifecycle.sh:60-64) ──
    logger.info("[IMP:8][preflight] Running pre-flight checks (pending/WARN phases present)")
    node_yaml = os.environ.get("NODE_YAML", "")
    context = os.environ.get("CONTEXT", "")
    node_name = os.environ.get("NODE_NAME", "")
    try:
        proc = subprocess.run(
            [
                "python3",
                preflight_script,
                "--node-yaml",
                node_yaml,
                "--context",
                context,
                "--node-name",
                node_name,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("[IMP:10][preflight] Preflight execution failed: %s", e)
        return 1
    if proc.returncode != 0:
        logger.error(
            "[IMP:10][preflight] Pre-flight checks FAILED: %s",
            (proc.stderr or proc.stdout).strip()[-500:],
        )
        return 1
    # --parse-warnings: read JSON from stdin, print warnings to stderr (non-fatal)
    try:
        subprocess.run(
            ["python3", preflight_script, "--parse-warnings"],
            input=proc.stdout,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("[IMP:7][preflight] Preflight warnings parse failed (non-fatal): %s", e)
    return 0


# endregion FUNC__maybe_run_preflight


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PlatformError as e:
        logger.critical("[IMP:10][__main__] Platform error (exit=%d): %s", e.exit_code, e)
        print(f"{e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        logger.critical("[IMP:10][__main__] Unexpected error: %s", e)
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
