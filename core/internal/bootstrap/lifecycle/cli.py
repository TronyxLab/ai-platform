#!/usr/bin/env python3
# GREP_SUMMARY: lifecycle-cli, build-parser, main, run-init-mode, run-update-mode, run-phases, state-machine, node-lifecycle, inject-cli-env, recover-corrupt-state, run-single-phase, reset-state
# STRUCTURE: ▶ build_parser ┌--mode/--dry-run/--resume/--force/--run-phase/--node-*┐ → ⚡ main ┌_inject_cli_env → _recover_corrupt_state → _run_single_phase → dispatch┐ → ◇ _run_phases ┌done/skip → execute_phase → 3×except+_audit_failed┐ → ○ post_hooks → audit → notify → ⎋ exit {0,1,2,4}
# region MODULE_CONTRACT
## @purpose  CLI/main слой lifecycle state machine — build_parser, main (оркестратор ≤40 LOC),
##           run_init_mode/run_update_mode (тонкие фасады над общим _run_phases). Извлечён из
##           state_machine.py (B9 T1, U-08) — state_machine.py остаётся чистой оркестрацией,
##           CLI живёт здесь.
## @scope    cli.py: build_parser, main, run_init_mode (ex-_run_init_mode :1390),
##           run_update_mode (ex-_run_update_mode :1465), _run_phases, и приватные хелперы
##           (_inject_cli_env/_recover_corrupt_state/_reset_state/_validate_init_env/
##           _run_single_phase). Вызывается через
##           `python3 lifecycle/cli.py --mode ...` (node-lifecycle.sh CS-7) и через
##           единственный канал — node-lifecycle.sh → cli.py (compat-заглушка state_machine.py удалена, 164 W3).
## @invariants
##   - main() -> int; sys.exit ТОЛЬКО в __main__ (контракт core/AGENTS.md)
##   - run_init/run_update: 9/5 фаз последовательно с dependency-checking; done-фазы SKIP
##   - Фаза не начинает выполнение при невыполненных dependency/precondition (BLOCKING)
##   - Audit log + Telegram notification вызываются после полного run (helpers/reporting)
##   - _run_phases — семантика 1:1 с pre-refactor run_init/run_update (exit-коды, WARN-семантика
##     117 D5, hash-инвалидация T9.3, done/skip-проверка dict+StepState, audit в failure-путях T9.6)
## @rationale DevPlan 116 B9 D1/D2: CLI (~340 LOC) вынесен из state_machine-монолита —
##            state_machine.py ≤ 1200 LOC под LOC-гейтом (T6.2).
## @rationale DevPlan 170 W5-C2 (research-A §4): main 179 LOC/CC36 → оркестратор (env-inject,
##            corrupt-recovery, single-phase извлечены в хелперы); run_init/run_update ~90%
##            дубль (research-A §4) → общий _run_phases(sm, phases, post_hooks).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-13 · DevPlan 162 W7-2 — +_final_verification_pass (security_posture S1-S9
##           после bootstrap, non-blocking) в run_init_mode (между smoke и audit-log)
## @changes  2026-08-14 · DevPlan 170 W1-A3 — --proxy-url порт из SoT firewall.PRIVOXY_PORT
## @changes  2026-08-15 · DevPlan 170 W5-C2 — main → оркестратор: _inject_cli_env (18×env из
##           CLI-args, таблица пар), _recover_corrupt_state (B26-аудит + unlink + recreate),
##           _run_single_phase, _reset_state, _validate_init_env; run_init/run_update ~90%
##           дубль → _run_phases(sm, phases, post_hooks) (реализация дедуплицирована)
## @changes  2026-08-31 · P0 (F-01, asi-team-vps cold bootstrap) — re-exec lifecycle на
##           Python 3.14 после φ1 (python_deps): _should_reexec_python/_reexec_lifecycle +
##           pre-phase проверка в _run_phases; системный 3.12 без pydantic больше не выполняет
##           φ2..φ8 (module-level pydantic-импорты замораживали extract_domains_for_context=None)
## @changes  2026-09-01 · P0 (F-01 fix, asi-team-vps cold bootstrap) — re-exec argv fix:
##           +_reexec_argv (argv[0] восстановлен: file-путь/-m spec; [target, *sys.argv[1:]]
##           давал интерпретатору "--mode" как СВОЙ опцион — "Unknown option: --mode", φ2 died)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypedDict, cast

# DevPlan 170 W1-A3: приватный порт Privoxy из SoT firewall.py (литерал 8118 удалён)
from core.internal.bootstrap.firewall import PRIVOXY_PORT

# QA R2/T2.B: set_run_start_ts — run-start для freshness hc_done-маркера (φ11 reader)
from core.internal.bootstrap.lifecycle import state_machine as state_machine_mod
from core.internal.bootstrap.lifecycle.helpers.reporting import send_telegram, write_audit_log
from core.internal.bootstrap.lifecycle.state_machine import (
    BootstrapPhase,
    PhaseDependencyError,
    PhasePreconditionError,
    StateMachine,
    StepState,
    phase_is_done,
)

# T17-fix: честная awaiting-классификация — docker ps ТОЛЬКО через канонический sole-path
# (гейт docker_sole_path, allowlist пуст; прямые subprocess-вызовы docker запрещены).
from core.internal.shared import docker_ops
from core.internal.shared.audit_logger import write_audit_entry

# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE, platform_remote_base
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# W1-A1 (план 170): литералы таймаутов lifecycle-cli → канон SoT (AMBER-зачистка research-D §D1).
# 30 (dispatch ping) → CONVERGE_DOCKER_TIMEOUT; 120 (security_posture/preflight) →
# LIFECYCLE_CMD_TIMEOUT; 60 (preflight --parse-warnings) → SYSTEM_CMD_TIMEOUT.
from core.internal.shared.timeouts import (
    CONVERGE_DOCKER_TIMEOUT,
    DOCKER_CMD_TIMEOUT,
    LIFECYCLE_CMD_TIMEOUT,
    SYSTEM_CMD_TIMEOUT,
)

logger = logging.getLogger(__name__)

_DISK_WARN_PCT: float = 90.0  # порог предупреждения заполнения диска (%)

DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"

# ── P0 (F-01, 2026-08-31): re-exec lifecycle на Python 3.14 после установки python_deps ──
# Голая нода: системный python3 = 3.12 БЕЗ pydantic. φ1 (system_bootstrap) через python_deps.py
# ensure ставит 3.14 (deadsnakes) + платформенные deps и создаёт /usr/local/bin/python3 →
# /usr/bin/python3.14 (PATH-порядок). Текущий процесс cli.py остаётся на 3.12 — module-level
# импорты deploy-цепочки (pydantic) замораживали extract_domains_for_context=None (F-01 P0).
# Re-exec через os.execv (тот же PID → exit-код пробрасывается в node-lifecycle.sh); done-фазы
# (в т.ч. φ1) лежат в state.json — новый процесс скипает их (resume-семантика state_machine).
_REEXEC_PYTHON_TARGET = "/usr/local/bin/python3"  # канонический интерпретатор после python_deps (SoT: python_deps.py)
_REEXEC_MIN_VERSION = (3, 14)  # целевая версия (deadsnakes; python_deps.py — SoT версии)
_REEXEC_MARKER_ENV = "BOOTSTRAP_PYTHON_REEXEC"  # loop-guard: маркер ставится перед execv


# region CLASS_CliArgs
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...).

    ## @purpose — Устраняет Any-каскад argparse Namespace-атрибутов в main/_inject_cli_env.
    ## @invariants — ТОЛЬКО аннотации БЕЗ значений: argparse заполняет дефолты из
    ##              add_argument build_parser (class-значения ломали бы hasattr-defaults).
    ## @complexity — O(1) — декларация полей
    """

    def __init__(self) -> None:
        super().__init__()
        self.state_file: str
        self.mode: str
        self.run_phase: str | None
        self.dry_run: bool
        self.resume: bool
        self.force: bool
        self.node_name: str | None
        self.node_yaml: str | None
        self.owner_key: str | None
        self.ci_deploy_key: str | None
        self.ci_root_key: str | None
        self.tor_enabled: str | None
        self.tor_bridges_file: str | None
        self.skip_tor_verify: bool
        self.ghcr_token: str | None
        self.extra_ports: str | None
        self.bot_token: str | None
        self.chat_id: str | None
        self.proxy_url: str
        self.auto_reconcile: bool
        self.context: str | None


# endregion CLASS_CliArgs


# region TYPEDEF_PosturePayload
class _PostureCheck(TypedDict):
    """Одна проверка security_posture --json (S1-S9)."""

    id: str
    status: str
    message: str


class _PosturePayload(TypedDict):
    """Корневой JSON security_posture --json (W11-G3: граница json.loads)."""

    checks: list[_PostureCheck]


# endregion TYPEDEF_PosturePayload


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
    parser.add_argument(
        "--ci-root-key", help="CI root SSH public key — VPS_SSH_KEY pub-часть (PLATFORM_CI_ROOT_KEY, 142 W1)"
    )
    parser.add_argument("--tor-enabled", choices=["true", "false"], default=None, help="Override TOR_ENABLED")
    parser.add_argument("--tor-bridges-file", help="Path to Tor bridges file")
    parser.add_argument("--skip-tor-verify", action="store_true", help="Skip Tor circuit verification")
    parser.add_argument("--ghcr-token", help="GHCR pull token for docker login")
    parser.add_argument("--extra-ports", help="Extra firewall ports (space-separated)")
    parser.add_argument("--bot-token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Telegram chat ID")
    parser.add_argument("--proxy-url", default=f"http://127.0.0.1:{PRIVOXY_PORT}", help="Telegram proxy URL")
    parser.add_argument("--auto-reconcile", action="store_true", help="Auto-reconcile after converge")
    parser.add_argument("--context", help="Deployment context name (CONTEXT — DevPlan 047)")
    return parser


# endregion FUNC_build_parser


# ── CLI → env инъекции (W5-C2): (attr, env_name, mode) — таблица вместо 18×if/setdefault.
# mode ∈ {"setdefault", "override", "flag"}: setdefault — не перезаписывает существующий env;
# override — принудительная запись (tor_enabled: может быть "false"); flag — "true" по наличию.
_CLI_ENV_INJECTIONS: tuple[tuple[str, str, str], ...] = (
    ("node_name", "NODE_NAME", "setdefault"),
    ("node_yaml", "NODE_YAML", "setdefault"),
    ("owner_key", "PLATFORM_OWNER_KEY", "setdefault"),
    ("ci_deploy_key", "PLATFORM_CI_DEPLOY_KEY", "setdefault"),
    ("ci_root_key", "PLATFORM_CI_ROOT_KEY", "setdefault"),
    ("ghcr_token", "GHCR_PULL_TOKEN", "setdefault"),
    ("bot_token", "TELEGRAM_BOT_TOKEN", "setdefault"),
    ("chat_id", "TELEGRAM_CHAT_ID", "setdefault"),
    ("proxy_url", "TELEGRAM_PROXY_URL", "setdefault"),
    ("tor_bridges_file", "TOR_BRIDGES_FILE", "setdefault"),
    ("extra_ports", "FIREWALL_EXTRA_PORTS", "setdefault"),
    ("context", "CONTEXT", "setdefault"),  # DevPlan 047: --context CLI arg → CONTEXT env var
    ("tor_enabled", "TOR_ENABLED", "override"),
    ("skip_tor_verify", "SKIP_TOR_VERIFY", "flag"),
    ("auto_reconcile", "AUTO_RECONCILE", "flag"),
)


# region FUNC__inject_cli_env
## @purpose  Инжекция CLI-аргументов в os.environ (NODE_NAME/NODE_YAML/TOR_ENABLED/...).
##           W5-C2: 18×if/setdefault из main извлечены в таблицу _CLI_ENV_INJECTIONS —
##           семантика 1:1 (setdefault не перезаписывает уже установленный env;
##           override/flag — принудительные записи).
## @io       ⇥ args: argparse.Namespace → ⎋ None (side-effect: os.environ)
## @complexity O(15) — таблица пар, 0 ветвлений
def _inject_cli_env(args: argparse.Namespace) -> None:
    """Inject CLI args into os.environ (канон node-lifecycle.sh env-контракта)."""
    for attr, env_name, mode in _CLI_ENV_INJECTIONS:
        value = cast(
            object, getattr(args, attr)
        )  # W11-G3: getattr → Any (динамический attr-имя); общий тип str|None|bool
        if mode == "override":
            if value is not None:
                os.environ[env_name] = str(value)
        elif mode == "flag":
            if value:
                os.environ[env_name] = "true"
        elif value:
            os.environ.setdefault(env_name, str(value))


# endregion FUNC__inject_cli_env


# region FUNC__recover_corrupt_state
## @purpose  Создание StateMachine с force-recovery коррапт state.json (T9.2 + B26 142 W7).
##           Corrupt state → PlatformFatalError: без --force — abort (критический лог + exit_code);
##           с --force — B26-аудит-запись (state.json/removed), unlink файла, recreate с нуля.
##           ⚠️ Аудит-след защищает от бесследного исчезновения state.json (цикл 2 141).
## @io       ⇥ sm_factory: Callable[..., StateMachine], state_file: str, force: bool,
##              audit_impl: Callable[..., object] (DI, W-H DevPlan 163) → ⎋
##              (StateMachine, None) — успех | (None, int) — corrupt без --force (exit_code)
## @complexity O(1) + 1 файловая операция
## @invariants
##   - Без --force коррапт НЕ сбрасывается тихо — abort с PlatformFatalError.exit_code
##   - С --force: аудит ДО unlink; unlink(missing_ok=True); повторный create из sm_factory
##   - Аудит-запись best-effort: исключение → WARN, НЕ маскирует recovery
def _recover_corrupt_state(
    sm_factory: Callable[..., StateMachine],
    state_file: str,
    *,
    force: bool,
    audit_impl: Callable[..., object],
) -> tuple[StateMachine | None, int | None]:
    """Create StateMachine; corrupt state.json + --force → B26-аудит + unlink + recreate (T9.2)."""
    try:
        return sm_factory(state_file_path=state_file), None
    except PlatformFatalError as e:
        if not force:
            logger.critical("[IMP:10][main] %s", e)
            print(f"[FATAL] {e}", file=sys.stderr)
            return None, e.exit_code
        logger.warning("[IMP:8][main] Corrupt state + --force: removing %s and starting fresh", state_file)
        # ⚠️ 142 W7 (B26): аудит-запись при удалении state.json — защита от бесследного
        # исчезновения (цикл 2 141: /var/lib/platform/.bootstrap/state.json исчез,
        # механизм не выявлен; аудит-след позволяет реконструировать кто/когда).
        try:
            audit_impl(
                "state.json",
                "removed",
                f"Corrupt state file removed (--force recovery): {state_file}",
                operation="bootstrap-force",
            )
        # ruff: ignore[BLE001] — audit best-effort — никогда не маскирует --force recovery
        except Exception as audit_exc:  # noqa: EXC — audit best-effort, никогда не маскирует recovery
            logger.warning("[IMP:7][main] Audit entry for state removal failed (non-fatal): %s", audit_exc)
        Path(state_file).unlink(missing_ok=True)
        return sm_factory(state_file_path=state_file), None


# endregion FUNC__recover_corrupt_state


# region FUNC__reset_state
## @purpose  --force reset: B26-аудит-запись (state.json/reset) ДО sm.reset() (142 W7).
##           W5-C2: извлечено из main (--force clear state блок) — аудит-след операции сброса.
## @io       ⇥ sm: StateMachine, state_file: str, audit_impl: Callable[..., object] → ⎋ None
## @complexity O(1) + state.save (внутри reset)
## @invariants
##   - Аудит-запись пишется ДО reset() (порядок — контракт B26-теста)
##   - Аудит best-effort: исключение → WARN, НЕ маскирует reset
def _reset_state(sm: StateMachine, state_file: str, audit_impl: Callable[..., object]) -> None:
    """--force: audit reset + sm.reset() (B26: аудит-запись обязана быть ДО сброса)."""
    logger.info("[IMP:9][main] --force: Clearing state")
    try:
        audit_impl(
            "state.json",
            "reset",
            f"State reset via --force: {state_file}",
            operation="bootstrap-force",
        )
    # ruff: ignore[BLE001] — audit best-effort — никогда не маскирует основной сбой
    except Exception as audit_exc:  # noqa: EXC — audit best-effort
        logger.warning("[IMP:7][main] Audit entry for state reset failed (non-fatal): %s", audit_exc)
    sm.reset()


# endregion FUNC__reset_state


# region FUNC__validate_init_env
## @purpose  Валидация init-mode env (NODE_NAME/NODE_YAML/PLATFORM_OWNER_KEY) + semi-optional
##           предупреждения (PLATFORM_CI_DEPLOY_KEY, PLATFORM_CI_ROOT_KEY 142 W1).
##           W5-C2: извлечено из main (validate env блок) — main остаётся оркестратором.
## @io       ⇥ sm: StateMachine, source: Mapping[str, str] (env-дикт, DI W-H) → ⎋ bool
##              (True = валидно, False = missing required → exit 1)
## @complexity O(3) — required-проверки через sm.validate_bootstrap_env
## @invariants
##   - PLATFORM_CI_DEPLOY_KEY/PLATFORM_CI_ROOT_KEY semi-optional: отсутствие → WARN, не fail
##   - required_vars missing → False (main возвращает 1, контракт exit-кодов)
def _validate_init_env(sm: StateMachine, source: Mapping[str, str]) -> bool:
    """Validate init-mode required env + semi-optional warns (exit-код контракт)."""
    required_vars = ["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]
    if not bool(source.get("PLATFORM_CI_DEPLOY_KEY", "").strip()):
        logger.warning("[IMP:7][main] PLATFORM_CI_DEPLOY_KEY not set — ci-deploy user will have no deploy key")
    # 142 W1: PLATFORM_CI_ROOT_KEY semi-optional — warn (root authorized_keys для core-deploy)
    if not bool(source.get("PLATFORM_CI_ROOT_KEY", "").strip()):
        logger.warning(
            "[IMP:7][main] PLATFORM_CI_ROOT_KEY not set — root authorized_keys не получит CI-root ключ "
            "(core-deploy root-канал будет недоступен, 142 W1)"
        )
    return sm.validate_bootstrap_env(required_vars, env=source)


# endregion FUNC__validate_init_env


# region FUNC__run_single_phase
## @purpose  --run-phase: выполнение ОДНОЙ фазы по имени (валидация имени + execute_phase).
##           W5-C2: извлечено из main (run-phase ветка) — exit 0 = ok, 1 = unknown/failed.
## @io       ⇥ sm: StateMachine, phase: str → ⎋ int (0 = успех, 1 = unknown phase / exception)
## @complexity O(1) + execute_phase
## @invariants
##   - Неизвестная фаза (вне BootstrapPhase.ALL_PHASES) → [IMP:10] error + exit 1
##   - PhaseDependencyError/PhasePreconditionError/PlatformFatalError → error + exit 1
def _run_single_phase(sm: StateMachine, phase: str) -> int:
    """Run a single phase by name (--run-phase); 0 = ok, 1 = unknown phase / failure."""
    if phase not in BootstrapPhase.ALL_PHASES:
        logger.error(
            "[IMP:10][main] Unknown phase '%s'. Valid phases: %s",
            phase,
            ", ".join(sorted(BootstrapPhase.ALL_PHASES)),
        )
        return 1
    logger.info("[IMP:9][main] Running single phase: %s", phase)
    try:
        sm.execute_phase(phase)
        logger.info("[IMP:9][main] Phase '%s' completed successfully", phase)
    except (PhaseDependencyError, PhasePreconditionError, PlatformFatalError) as e:
        logger.error("[IMP:10][main] Phase '%s' FAILED: %s", phase, e)
        return 1
    else:
        return 0


# endregion FUNC__run_single_phase


# region FUNC_main
## @purpose — Top-level orchestrator for state machine CLI (≤40 LOC, W5-C2).
##            Handles: --dry-run, --force, --resume, --run-phase, mode dispatch.
##            Тяжёлая логика — в хелперах: _inject_cli_env, _recover_corrupt_state,
##            _reset_state, _validate_init_env, _run_single_phase.
## @io — ⇥ argv: list[str] | None (None = sys.argv), env: Mapping | None (DI, W-H DevPlan 163:
##          override-дикт env-переменных для validate/setup; None = os.environ), sm_class (DI:
##          фабрика StateMachine для тестов), run_init_fn/run_update_fn (DI: runner-функции),
##          audit_fn (DI: write_audit_entry для B26-теста) → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(N * M) where N = phases, M = per-phase operations
## @invariants
##   - DI-параметры НЕ меняют поведение по умолчанию (None → sys.argv/os.environ/StateMachine/
##     run_init_mode/run_update_mode/write_audit_entry) — публичная сигнатура main() не ломается
##   - env= дикт используется для validate_bootstrap_env + setup_state (вместо os.environ)
def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    sm_class: type[StateMachine] | None = None,
    run_init_fn: Callable[[StateMachine], int] | None = None,
    run_update_fn: Callable[[StateMachine], int] | None = None,
    audit_fn: Callable[..., object] | None = None,
) -> int:
    """CLI entry point. Parses args, creates StateMachine, dispatches to mode."""
    # REF-0007 (11-DevPlan Волна 1): umask 077 — файлы, созданные фазами lifecycle
    # (secrets.env и пр.), получают 0600 по умолчанию (страховка поверх atomic_writer).
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv, namespace=_CliArgs())
    # DI (W-H DevPlan 163): env-дикт override (тесты без monkeypatch.setenv); None = os.environ
    source: Mapping[str, str] = os.environ if env is None else env

    # φ4-диагностика (022-launch-validation): digest AGE-ключа в env на входе CLI
    # (НЕ содержимое) — трассировка «какой ключ дошёл до lifecycle» без раскрытия секрета.
    age_entry = source.get("AGE_SECRET_KEY", "")
    if age_entry:
        import hashlib

        logger.info(
            "[IMP:8][cli][diag] AGE_SECRET_KEY present at CLI entry: len=%d sha256=%s",
            len(age_entry),
            hashlib.sha256(age_entry.encode()).hexdigest()[:16],
        )

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    _inject_cli_env(args)

    # DI (W-H): фабрика StateMachine / runner-функции / audit-writer — для тестов (AF-4 паттерн)
    sm_factory = StateMachine if sm_class is None else sm_class
    run_init_impl = run_init_mode if run_init_fn is None else run_init_fn
    run_update_impl = run_update_mode if run_update_fn is None else run_update_fn
    audit_impl: Callable[..., object] = cast(
        Callable[..., object],
        write_audit_entry if audit_fn is None else audit_fn,
    )  # W11-G1 cross-file: write_audit_entry имеет **extra без аннотации (audit_logger, G1-файл не правим) — приведение к DI-контракту

    # T9.2 (DevPlan 136 W9): коррапт state.json → PlatformFatalError. Recovery: --force
    # удаляет файл и стартует заново (явный операторский reset, не тихий сброс).
    sm, corrupt_exit = _recover_corrupt_state(sm_factory, args.state_file, force=args.force, audit_impl=audit_impl)
    if sm is None:
        return corrupt_exit or 1

    # Detect CORE_DIR from PLATFORM_ROOT or default
    platform_root = str(platform_remote_base())
    sm.core_dir = os.environ.get("CORE_DIR", os.path.join(platform_root, "core"))

    # ── --force: clear state (B26-аудит ДО reset) ──
    if args.force:
        _reset_state(sm, args.state_file, audit_impl)

    # ── --dry-run: print plan, no mutations ──
    if args.dry_run:
        sm.setup_state(mode=args.mode, node=source.get("NODE_NAME"))
        print(sm.dry_run_plan(), file=sys.stderr)
        return 0

    # ── Validate env (init mode) ──
    if args.mode == "init" and not _validate_init_env(sm, source):
        return 1

    # ── Setup state if fresh or mode changed ──
    if sm.state.mode != args.mode or sm.state.current_step == 0:
        sm.setup_state(mode=args.mode, node=source.get("NODE_NAME"))
    elif args.resume:
        logger.info("[IMP:8][main] --resume: Continuing from step %d", sm.state.current_step)

    # ── --run-phase: execute single phase ──
    if args.run_phase:
        return _run_single_phase(sm, args.run_phase)

    # ── D6 (волна 117): preflight — ТОЛЬКО при наличии pending/WARN-фаз ──
    if args.mode == "init":
        preflight_rc = _maybe_run_preflight(sm)
        if preflight_rc != 0:
            return preflight_rc

    # ── Dispatch full mode run ──
    try:
        if args.mode == "init":
            return run_init_impl(sm)
        return run_update_impl(sm)
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
    ok_static = _smoke_check_authorized_keys()
    ok_ping = _smoke_check_dispatch_ping(str(platform_remote_base()))

    if ok_static and ok_ping:
        print("[IMP:9][smoke] FORCED-COMMAND PING: OK — CI-деплой канал готов", file=sys.stderr)
    else:
        # КРУПНО, но не блокирует bootstrap (vps_readiness pre-flight перепроверит при деплое)
        print(
            "🚨 [IMP:10][smoke] FORCED-COMMAND PING: FAIL — CI-деплой будет невозможен (см. лог выше)",
            file=sys.stderr,
        )
    return ok_static and ok_ping


# region FUNC__smoke_check_authorized_keys
## @purpose  Статическая половина smoke: ci-deploy authorized_keys содержит
##           orchestrator_cli dispatch + restrict entry.
## @io       ⇥ None → ⎋ bool (True = entry OK)
## @complexity O(len(authorized_keys))
## ⚠️ TRAP[BUG] · 2026-08-22 · P1 · smoke ВСЕГДА репортил провал на happy-path (T1.1, аудит)
# · Symptom: post-bootstrap smoke печатал «FORCED-COMMAND PING: FAIL» при полностью рабочем канале.
# · Root: FAIL-warning и ok=False выполнялись БЕЗУСЛОВНО внутри success-if (без else) —
# ·   success-лог и fail-лог соседствовали, ok всегда False.
# · Fix: else-ветки; извлечение в helper'ы (ruff too-many-statements-in-try-clause).
# · Prevention: пиннинг-тест test_forced_command_smoke_happy_path (test_lifecycle_cli_w5.py).
def _smoke_check_authorized_keys() -> bool:
    """Check ci-deploy authorized_keys for the forced-command entry."""
    # ~ci-deploy резолвится через passwd (os.path.expanduser) — без хардкод-литерала
    # (гейт test_gate_no_hardcoded_local_paths: /home/<user> — RED)
    auth_keys = os.path.join(os.path.expanduser("~ci-deploy"), ".ssh", "authorized_keys")
    try:
        content = Path(auth_keys).read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[IMP:7][smoke] forced-command authorized_keys unreadable: %s — %s", auth_keys, e)
        return False

    if "orchestrator_cli dispatch" in content and "restrict" in content:
        logger.info("[IMP:9][smoke] forced-command authorized_keys: entry OK (%s)", auth_keys)
        return True

    logger.warning(
        "[IMP:7][smoke] forced-command authorized_keys: entry MISSING (%s) — CI-деплой канал мёртв", auth_keys
    )
    return False


# endregion FUNC__smoke_check_authorized_keys


# region FUNC__smoke_check_dispatch_ping
## @purpose  Runtime-половина smoke: `orchestrator_cli dispatch ping` локально → pong.
## @io       ⇥ base: str (platform root для cwd) → ⎋ bool (True = pong получен)
## @complexity O(1) + 1 subprocess
def _smoke_check_dispatch_ping(base: str) -> bool:
    """Run dispatch ping locally; True when pong received."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "core.internal.deploy.orchestrator_cli", "dispatch", "ping"],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            cwd=base,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][smoke] forced-command ping: ERROR — %s", e)
        return False

    if "pong" in r.stdout:
        logger.info("[IMP:9][smoke] forced-command ping: OK (dispatch ping → pong)")
        return True

    logger.warning(
        "[IMP:7][smoke] forced-command ping: FAIL (rc=%s out=%r) — orchestrator_cli dispatch не отвечает",
        r.returncode,
        r.stdout[:80],
    )
    return False


# endregion FUNC__smoke_check_dispatch_ping


# endregion FUNC_forced_command_smoke


# region FUNC_final_verification_pass
## @purpose  Финальный verification-pass после bootstrap (DevPlan 162 W7-2): security_posture
##           S1-S9 + резюме PASS/FAIL/WARN. Non-blocking: FAIL → КРУПНЫЙ print в stderr,
##           exit-код bootstrap не меняется (аналогично _forced_command_smoke).
## @io       ⇥ core_dir: platform core directory → ⎋ None
## @complexity O(9) — прогон всех posture-проверок (security_posture --json)
## @invariants
##   - Root-only: euid != 0 → skip (security_posture сам fail-fast при non-root — boot-контекст root)
##   - Отсутствие security_posture.py (test-окружения/tmp CORE_DIR) → WARN + skip (не блокирует)
##   - JSON-формат: {"checks": [{id, status, message}]} — status ∈ PASS/WARN/FAIL
##   - Timeout 120s; OSError/TimeoutExpired/JSONDecodeError → WARN (non-blocking, exit 0 сохранён)
##   - Резюме печатается в stderr: SECURITY S1..S9: N PASS / M FAIL / K WARN + IMP:10 лог
## @rationale DevPlan 162 W7-2: verifier-проход (S1-S9) не вызывался из pipeline — после
##            bootstrap exit 0 ≠ «всё зелёное». Non-blocking по канону _forced_command_smoke
##            (FL20): FAIL информирует оператора, не валит повторные boot-прогоны.
def _final_verification_pass(core_dir: str) -> None:
    """Run security_posture S1-S9 verification pass (non-blocking, bootstrap-only)."""
    try:
        if os.geteuid() != 0:
            logger.info(
                "[IMP:7][verify] Non-root (euid=%d) — skipping security verification pass (bootstrap-only)",
                os.geteuid(),
            )
            return
    except AttributeError:  # pragma: no cover — non-POSIX
        logger.info("[IMP:7][verify] geteuid unavailable — skipping security verification pass")
        return

    posture_script = os.path.join(core_dir, "internal", "bootstrap", "security_posture.py")
    if not os.path.isfile(posture_script):
        logger.warning(
            "[IMP:7][verify] security_posture.py not found at %s — skipping final verification pass", posture_script
        )
        return

    try:
        r = subprocess.run(
            [sys.executable, posture_script, "--json"],
            capture_output=True,
            text=True,
            timeout=LIFECYCLE_CMD_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][verify] Final verification pass ERROR (non-fatal): %s", e)
        return

    try:
        payload = cast(
            _PosturePayload, json.loads(r.stdout)
        )  # W11-G3: json.loads → Any; TypedDict — граница JSON-ответа security_posture
        checks = payload.get("checks", [])
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("[IMP:7][verify] Final verification pass: unparseable output (rc=%s): %s", r.returncode, e)
        return

    statuses = [c.get("status", "?") for c in checks]
    n_pass = statuses.count("PASS")
    n_fail = statuses.count("FAIL")
    n_warn = statuses.count("WARN")
    summary = f"SECURITY S1..S9: {n_pass} PASS / {n_fail} FAIL / {n_warn} WARN"
    if n_fail:
        # КРУПНО, но не блокирует bootstrap (канон _forced_command_smoke: FAIL → exit 0 сохранён)
        print(f"🚨 [IMP:10][verify] {summary} — см. security_posture --json выше", file=sys.stderr)
        logger.error("[IMP:10][verify] %s (rc=%d)", summary, r.returncode)
    elif n_warn:
        logger.warning("[IMP:8][verify] %s", summary)
    else:
        logger.info("[IMP:9][verify] %s — all posture checks green", summary)


# endregion FUNC_final_verification_pass


# region FUNC__is_stale_phase_message
## @purpose  Stale-детектор записей state.errors/warnings (plan 012 T17-fix): сообщение вида
##           "Phase <phase> failed: ..." / "Phase <phase> completed with non-fatal issues ..."
##           ассоциируется с фазой; если фаза СЕЙЧАС done (успешно перевыполнена в этом или
##           прошлом прогоне) — запись stale (ошибка прошлого run) и не показывается в отчёте.
## @io       ⇥ msg: str, done_phases: set[str] → ⎋ bool (True = stale)
## @complexity O(1)
## @invariants
##   - Матч ТОЛЬКО по каноническому префиксу "Phase <name>" (единый формат писателей:
##     _audit_failed / _mark_phase_with_warnings) — детерминированный, без ложных срабатываний
##   - Записи БЕЗ фазового префикса (легаси/внешние) НИКОГДА не считаются stale (показываются)
##   - done_phases — множество фаз со статусом done (phase_is_done; done_with_warnings ≠ done)
def _is_stale_phase_message(msg: str, done_phases: set[str]) -> bool:
    """Return True if a state error/warning references a phase that is currently done (T17-fix)."""
    if not msg.startswith("Phase "):
        return False
    # "Phase <phase> failed: ..." / "Phase <phase> completed with non-fatal issues ..." →
    # первый токен после префикса = имя фазы (snake_case ключи steps — без пробелов).
    phase = msg[len("Phase ") :].split(" ", 1)[0]
    return phase in done_phases


# endregion FUNC__is_stale_phase_message


# region FUNC__prune_phase_records
## @purpose  Удалить stale-записи state.errors/warnings для фазы, успешно перевыполненной
##           (plan 012 T17-fix, mark-done prune). state.errors АККУМУЛИРУЕТ ошибки прошлых
##           прогонов: run N failed → "Phase X failed: ..." в state; run N+1 фаза X успешна →
##           запись stale для ЛЮБОГО потребителя (отчёт, write_audit_log, Telegram).
## @io       ⇥ sm: StateMachine, phase: str → ⎋ None (мутирует sm.state.errors/warnings)
## @complexity O(E + W) — фильтрация списков
## @invariants
##   - Удаляются ТОЛЬКО записи с префиксом "Phase {phase}" (детерминированный матч)
##   - Структура state.json НЕ меняется (errors/warnings остаются list[str]) — back-compatible
##     со старыми файлами на нодах; конверсия в (phase, message)-кортежи отклонена
##   - Записи без фазового префикса сохраняются (легаси/внешние)
##   - Вызывается из _mark_phase_success ДО sm.save() — чистка персистится атомарно
## @rationale  Q: Почему чистка в mark-done, а не полный сброс errors при успехе run?
##   A: Полный сброс маскировал бы «недовыполненные» фазы (failed/pending) с их текущими
##   ошибками; прицельная чистка по фазе сохраняет честный остаток для ещё-не-выполненных фаз.
def _prune_phase_records(sm: StateMachine, phase: str) -> None:
    """Remove stale errors/warnings for a phase that just completed successfully (T17-fix)."""
    prefix = f"Phase {phase}"
    sm.state.errors = [e for e in sm.state.errors if not e.startswith(prefix)]
    sm.state.warnings = [w for w in sm.state.warnings if not w.startswith(prefix)]
    logger.info(
        "[IMP:8][report][prune] Stale records for phase %s removed (errors=%d warnings=%d)",
        phase,
        len(sm.state.errors),
        len(sm.state.warnings),
    )


# endregion FUNC__prune_phase_records


# region FUNC__docker_container_live
## @purpose  Best-effort docker-проба контейнера проекта на ноде (plan 012 T17-fix):
##           docker ps --filter name=⟨project⟩ через канонический sole-path docker_ops.
##           Используется для честной классификации "Awaiting project deploy".
## @io       ⇥ project_name: str, timeout: int = DOCKER_CMD_TIMEOUT → ⎋ bool (live) |
##              False (нет контейнера) | None (docker недоступен/ошибка/таймаут)
## @complexity O(1) — 1 docker ps subprocess
## @invariants
##   - Never raise: docker отсутствует/ошибка/таймаут → None (best-effort контракт отчёта)
##   - Docker CLI ТОЛЬКО через shared/docker_ops (гейт docker_sole_path, allowlist пуст)
##   - Timeout из SoT shared/timeouts (DOCKER_CMD_TIMEOUT) — никаких литералов
def _docker_container_live(project_name: str, *, timeout: int = DOCKER_CMD_TIMEOUT) -> bool | None:
    """Best-effort docker probe: any container matching project running on the node (T17-fix)."""
    try:
        result = docker_ops.docker_ps(filters=[f"name={project_name}"], format="{{.Names}}", timeout=timeout)
    # ruff: ignore[BLE001] — best-effort (DI-fake может raise; никогда не raise)
    except Exception as exc:  # noqa: EXC — best-effort контракт отчёта
        logger.warning("[IMP:7][report][docker] probe failed for %s: %s", project_name, exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][report][docker] docker ps failed (rc=%d) for %s — docker unavailable",
            result.returncode,
            project_name,
        )
        return None
    return bool((result.stdout or "").strip())


# endregion FUNC__docker_container_live


# region FUNC__classify_projects
## @purpose  Честная классификация проектов node.yaml на deployed/awaiting (plan 012 T17-fix):
##           deployed = /opt/projects/⟨name⟩/docker-compose.yml существует ИЛИ live-контейнер
##           (docker ps); иначе awaiting (реально ожидает деплоя). Docker недоступен →
##           docker-факт unknown, compose-факт остаётся, флаг unavailable поднимается.
## @io       ⇥ project_names: list[str], projects_base: str = deploy_paths.DEFAULT_PROJECTS_BASE (DI: tmp_path),
##              docker_check_fn: Callable[[str], bool | None] | None = None
##              (DI: fake-пробер без реального docker; None = _docker_container_live)
##           → ⎋ (deployed: list[str], awaiting: list[str], docker_unavailable: bool)
## @complexity O(P) + O(P) docker-проб
## @invariants
##   - Порядок проверки детерминирован: compose-файл (без docker) → docker-проба
##   - docker_check_fn вернул None → docker_unavailable=True (не raise)
##   - Пустой project_names → ([], [], False) без docker-вызовов
##   - Never raise — любое исключение пробы → WARN + None (best-effort контракт отчёта)
def _classify_projects(
    project_names: list[str],
    *,
    projects_base: str = DEFAULT_PROJECTS_BASE,
    docker_check_fn: Callable[[str], bool | None] | None = None,
) -> tuple[list[str], list[str], bool]:
    """Classify projects as deployed/awaiting on the node (T17-fix honest report)."""
    check = _docker_container_live if docker_check_fn is None else docker_check_fn
    base = Path(projects_base)
    deployed: list[str] = []
    awaiting: list[str] = []
    docker_unavailable = False
    for name in project_names:
        if (base / name / "docker-compose.yml").is_file():
            deployed.append(name)
            continue
        try:
            live = check(name)
        # ruff: ignore[BLE001] — best-effort (DI-fake может raise; никогда не raise)
        except Exception as exc:  # noqa: EXC — best-effort контракт отчёта
            logger.warning("[IMP:7][report][probe] probe failed for %s: %s", name, exc)
            live = None
        if live is None:
            docker_unavailable = True
            awaiting.append(name)
        elif live:
            deployed.append(name)
        else:
            awaiting.append(name)
    logger.info(
        "[IMP:9][report][projects] classified: deployed=%d awaiting=%d docker_unavailable=%s",
        len(deployed),
        len(awaiting),
        docker_unavailable,
    )
    return deployed, awaiting, docker_unavailable


# endregion FUNC__classify_projects


# region FUNC_post_bootstrap_report
## @purpose  plan 012 T17: post-bootstrap report step — после φ8.5 печатается финальный
##           отчёт (IMP:9): модули deployed/failed, TLS-статус, проекты awaiting_deploy,
##           LLM keys, 3 suggested next commands. JSON-вариант под флагом REPORT_JSON=1.
##           Не влияет на exit-code (non-blocking контракт post_hook).
## @io       ⇥ sm: StateMachine,
##              projects_base: str = deploy_paths.DEFAULT_PROJECTS_BASE (DI: tmp_path в тестах),
##              docker_check_fn: Callable[[str], bool | None] | None = None
##              (DI: fake-пробер контейнеров без реального docker; None = _docker_container_live)
##           → ⎋ None
## @complexity O(P + E + D) — P проекты node.yaml, E ошибки state, D docker-пробы (per-project)
## @invariants
##   - Только init-режим (post_hook run_init_mode) — update-режим НЕ печатает (φ12/φ13)
##   - Никогда не raise — сбой чтения node.yaml → секция "awaiting_projects: (unavailable)"
##   - Stale-фильтр (T17-fix): ошибки/варнинги state, ассоциированные с фазами, которые
##     СЕЙЧАС done, НЕ показываются (аккумуляция state.errors между прогонами устранена) —
##     back-compatible, структура state.json не меняется (list[str] сохранён)
##   - Awaiting-классификация (T17-fix): проект = deployed если на ноде есть
##     /opt/projects/⟨name⟩/docker-compose.yml ИЛИ live-контейнер (docker ps best-effort);
##     docker недоступен → "(unavailable)" (по образцу best-effort контракта, никогда не raise)
##   - Не влияет на exit-code (исключение из report → WARN-лог, exit 0 сохранён)
##   - JSON (REPORT_JSON=1) — machine-readable, тот же контент
def post_bootstrap_report(
    sm: StateMachine,
    *,
    projects_base: str = DEFAULT_PROJECTS_BASE,
    docker_check_fn: Callable[[str], bool | None] | None = None,
) -> None:
    """Print the post-bootstrap summary report (plan 012 T17)."""
    # node_yaml/node_name — PhaseContext-поля (не атрибуты StateMachine); читаем из env
    # (NODE_YAML/NODE_NAME устанавливаются node-lifecycle.sh перед вызовом).
    node_yaml = os.environ.get("NODE_YAML", "")
    node_name = os.environ.get("NODE_NAME", "")

    # ── Deployed/failed: фазы + ошибки state ──
    # T17-fix: stale-фильтр — ошибки/варнинги фаз, которые СЕЙЧАС done (успешно перевыполнены),
    # не показываются. Аккумуляция state.errors между прогонами (run N failed → run N+1 успешен
    # → фаза done → её старая ошибка stale; live-кейс: "Phase deploy_services failed ... exit=10"
    # из run 1 при 9/9 успешных фазах в run 3) устранена.
    deployed_phases = [p for p in sm.state.steps if phase_is_done(sm.state.steps.get(p))]
    done_phases = set(deployed_phases)
    failed_msgs = [m for m in (sm.state.errors or []) if not _is_stale_phase_message(m, done_phases)]
    warning_msgs = [w for w in (sm.state.warnings or []) if not _is_stale_phase_message(w, done_phases)]
    warning_count = len(warning_msgs)

    # ── Projects awaiting deploy (честный статус на ноде, T17-fix) ──
    # Классификация: deployed = /opt/projects/⟨name⟩/docker-compose.yml есть ИЛИ live-контейнер;
    # иначе awaiting. Docker недоступен → "(unavailable)" по образцу best-effort контракта.
    project_names: list[str] = []
    projects_known = bool(node_yaml) and Path(node_yaml).is_file()
    if projects_known:
        try:
            from core.internal.shared.node_yaml import NodeYaml

            project_names = [p.name for p in NodeYaml(node_yaml).get_project_entries() if p.name]
        # ruff: ignore[BLE001] — report best-effort (T17 контракт: никогда не роняет bootstrap)
        except Exception as exc:  # noqa: EXC001 — report best-effort, non-blocking (T17 контракт)
            logger.warning("[IMP:7][report] node.yaml projects unreadable: %s", exc)
            projects_known = False
    deployed_projects, awaiting_projects, docker_unavailable = _classify_projects(
        project_names,
        projects_base=projects_base,
        docker_check_fn=docker_check_fn,
    )

    if not projects_known:
        awaiting_line = "(unavailable)"
        deployed_line = "Projects deployed: (unavailable)"
    elif not project_names:
        awaiting_line = "(none)"
        deployed_line = "Projects deployed: 0/0 (no projects in node.yaml)"
    else:
        awaiting_line = ", ".join(awaiting_projects) if awaiting_projects else "(none)"
        live_label = ", ".join(deployed_projects) if deployed_projects else "(none)"
        if docker_unavailable:
            live_label += " — docker unavailable"
        deployed_line = f"Projects deployed: {len(deployed_projects)}/{len(project_names)} (live: {live_label})"

    report_lines = [
        "──────────────────────────────────────────────",
        "  ✅ BOOTSTRAP REPORT (plan 012 T17)",
        f"  Node: {node_name or '(unknown)'}",
        f"  Phases done: {len(deployed_phases)}",
        f"  TLS: {'certificates phase done' if 'certificates' in sm.state.steps else 'see logs (φ7)'}",
        f"  Awaiting project deploy: {awaiting_line}",
        f"  {deployed_line}",
        "  LLM keys: provisioned in φ8/φ12 (make provision-llm для ручного повтора)",
        f"  Warnings: {warning_count}",
        f"  Failed: {', '.join(failed_msgs) if failed_msgs else '(none)'}",
        "  Next commands:",
        f"    make check-security NODE={node_name}",
        f"    make e2e-verify NODE={node_name}",
        f"    make project-list NODE={node_name}",
        "──────────────────────────────────────────────",
    ]

    if os.environ.get("REPORT_JSON") == "1":
        payload = {
            "node": node_name,
            "phases_done": len(deployed_phases),
            "tls_phase_done": "certificates" in sm.state.steps,
            "awaiting_projects": awaiting_projects,
            "deployed_projects": deployed_projects,
            "projects_total": len(project_names),
            "projects_unavailable": not projects_known,
            "docker_available": not docker_unavailable,
            "warnings": warning_count,
            "failed": failed_msgs,
            "next_commands": [
                f"make check-security NODE={node_name}",
                f"make e2e-verify NODE={node_name}",
                f"make project-list NODE={node_name}",
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))
        logger.info("[IMP:9][report] JSON report emitted (plan 012 T17)")
        return

    for line in report_lines:
        logger.info("[IMP:9][report] %s", line)
    print("\n".join(report_lines))


# endregion FUNC_post_bootstrap_report


# region FUNC_run_init_mode
## @purpose — Execute all init mode phases (9 phases from BootstrapPhase enum).
##            W5-C2: тело дедуплицировано → общий _run_phases(sm, phases, post_hooks);
##            init-специфика — post_hooks (smoke + final verification pass) перед audit/notify.
## @io — ⇥ sm: StateMachine, smoke_fn: Callable[[], bool] | None = None (DI, W-H DevPlan 163:
##          forced-command smoke; None = _forced_command_smoke), verify_fn: Callable[[str], None] | None
##          (DI: финальный security verification pass; None = _final_verification_pass),
##          audit_fn: Callable[[StateMachine], object] | None (DI: write_audit_log; None = канон),
##          notify_fn: Callable[[StateMachine], object] | None (DI: send_telegram; None = канон)
##          → ⎋ int exit code
## @complexity — O(N * M) where N = 9 phases, M = per-phase operations
## @invariants
##   - DI-параметры (None → канонические функции) — поведение по умолчанию неизменно;
##     тесты передают fake-коллбэки вместо monkeypatch.setattr (AF-4 паттерн, W-H)
##   - exit-коды/семантика делегируются _run_phases (1:1 с pre-refactor)
def run_init_mode(
    sm: StateMachine,
    *,
    smoke_fn: Callable[[], bool] | None = None,
    verify_fn: Callable[[str], None] | None = None,
    audit_fn: Callable[..., object]
    | None = None,  # write_audit_log(sm, result=...) — **kw совместим (тест-fakes lambda _, **kw)
    notify_fn: Callable[..., object] | None = None,
) -> int:
    """Execute all init mode phases (9 phases from BootstrapPhase enum)."""
    smoke_impl = _forced_command_smoke if smoke_fn is None else smoke_fn
    verify_impl = _final_verification_pass if verify_fn is None else verify_fn
    return _run_phases(
        sm,
        BootstrapPhase.INIT_PHASE_ORDER,
        mode_label="init",
        # ── Post-run: forced-command ping smoke (DevPlan 125 T3, FL20) + final security
        # verification pass (DevPlan 162 W7-2) — non-blocking, exit 0 сохраняется.
        # Вызываются ДО audit/notify (канон pre-refactor run_init_mode).
        post_hooks=[
            smoke_impl,
            lambda: verify_impl(sm.core_dir or ""),
            # plan 012 T17: post-bootstrap report после φ8.5 (non-blocking, exit 0 сохранён)
            lambda: post_bootstrap_report(sm),
        ],
        audit_fn=audit_fn,
        notify_fn=notify_fn,
    )


# endregion FUNC_run_init_mode


# region FUNC_run_update_mode
## @purpose — Execute all update mode phases (5 phases from BootstrapPhase enum).
##            W5-C2: тело дедуплицировано → общий _run_phases(sm, phases, post_hooks);
##            update-специфика — НЕТ post_hooks (только audit + notify).
## @io — ⇥ sm: StateMachine, audit_fn: Callable[[StateMachine], object] | None = None (DI, W-H),
##          notify_fn: Callable[[StateMachine], object] | None = None (DI) → ⎋ int exit code
## @complexity — O(N * M) where N = 5 phases, M = per-phase operations
## @invariants
##   - DI-параметры (None → канонические функции) — поведение по умолчанию неизменно
##   - exit-коды/семантика делегируются _run_phases (1:1 с pre-refactor)
def run_update_mode(
    sm: StateMachine,
    *,
    audit_fn: Callable[..., object]
    | None = None,  # write_audit_log(sm, result=...) — **kw совместим (тест-fakes lambda _, **kw)
    notify_fn: Callable[..., object] | None = None,
) -> int:
    """Execute all update mode phases (5 phases from BootstrapPhase enum)."""
    return _run_phases(
        sm,
        BootstrapPhase.UPDATE_PHASE_ORDER,
        mode_label="update",
        post_hooks=None,
        audit_fn=audit_fn,
        notify_fn=notify_fn,
    )


# endregion FUNC_run_update_mode


# region FUNC__should_reexec_python
## @purpose  Решение о re-exec на целевой интерпретатор (P0 F-01): текущий python СТАРШЕ 3.14
##           (системный 3.12 голой ноды), /usr/local/bin/python3 установлен python_deps (φ1)
##           и реально отдаёт 3.14 — вернуть путь для os.execv; иначе None (продолжать текущим).
## @io       ⇥ — → ⎋ str | None (путь целевого интерпретатора или None)
## @complexity O(1) + 1 subprocess-probe (один раз на процесс — кэш по пути)
## @invariants
##   - Версия-гейт sys.version_info >= (3, 14) → None (dev/CI/тесты на 3.14 — никогда не re-exec)
##   - Маркер _REEXEC_MARKER_ENV установлен → None (loop-guard: один re-exec за запуск)
##   - Целевой python обязан ОТДАВАТЬ 3.14 (subprocess-probe, кэш в _reexec_probe_cache):
##     случайный /usr/local/bin/python3 иной версии НЕ триггерит re-exec
##   - realpath-совпадение с sys.executable → None (уже на целевом)
##   - Тест-безопасность: venv 3.14 (dev/CI) и машины без /usr/local/bin/python3 — всегда None
_reexec_probe_cache: dict[str, str] = {}  # target path → version (subprocess-probe, один раз)


def _should_reexec_python() -> str | None:
    """Return the upgraded interpreter path when re-exec is warranted, else None (P0 F-01)."""
    if os.environ.get(_REEXEC_MARKER_ENV):
        return None
    if sys.version_info >= _REEXEC_MIN_VERSION:
        return None
    if not os.path.isfile(_REEXEC_PYTHON_TARGET):
        return None
    # Версия-проуба цели (кэш): re-exec ТОЛЬКО на genuine 3.14 (python_deps-артефакт).
    if _REEXEC_PYTHON_TARGET not in _reexec_probe_cache:
        try:
            probe = subprocess.run(
                [_REEXEC_PYTHON_TARGET, "--version"],
                capture_output=True,
                text=True,
                timeout=SYSTEM_CMD_TIMEOUT,
                check=False,
            )
            _reexec_probe_cache[_REEXEC_PYTHON_TARGET] = probe.stdout.strip() or probe.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:7][reexec] Interpreter probe failed (non-fatal): %s", e)
            _reexec_probe_cache[_REEXEC_PYTHON_TARGET] = ""
    target_version = _reexec_probe_cache[_REEXEC_PYTHON_TARGET]
    # Формат `python3.14 --version`: "Python 3.14.6" — префикс "Python {major}.{minor}"
    # (версия-проуба обязана совпасть с каноническим артефактом python_deps, SoT: python_deps.py)
    if not target_version.startswith(f"Python {_REEXEC_MIN_VERSION[0]}.{_REEXEC_MIN_VERSION[1]}"):
        return None
    try:
        same = Path(_REEXEC_PYTHON_TARGET).samefile(sys.executable)
    except OSError:
        same = os.path.realpath(_REEXEC_PYTHON_TARGET) == os.path.realpath(sys.executable)
    if same:
        return None
    return _REEXEC_PYTHON_TARGET


# endregion FUNC__should_reexec_python


# region FUNC__reexec_argv
## @purpose  Построение argv для re-exec (P0 F-01 fix, 2026-09-01): восстанавливает argv[0],
##           который _reexec_lifecycle терял — голый [target, *sys.argv[1:]] отдавал
##           интерпретатору "--mode" как СВОЙ опцион ("Unknown option: --mode", φ1 complete →
##           φ2 died, cold bootstrap asi-team-vps). File-запуск (канон node-lifecycle.sh:50
##           `python3 cli.py --mode ...`) → argv[1] = abs-путь скрипта; package-запуск
##           (`python3 -m core.internal.bootstrap.lifecycle.cli`) → ["-m", "<pkg>.cli"].
## @io       ⇥ target: str — путь интерпретатора (осознанный, фиксированный — 0 инъекции)
##           → ⎋ list[str] полный argv для os.execv (argv[0]=target + script/-m spec + CLI-args)
## @complexity O(1) — getattr + os.path.abspath (чистая функция, unit-тест без execv-guard)
## @invariants
##   - __main__.__package__ непустой (getattr default "") → package-режим: argv[1:3] = ["-m", f"{pkg}.cli"]
##   - иначе → file-режим: argv[1] = os.path.abspath(sys.argv[0]) (канон _delegate, node-lifecycle.sh)
##   - sys.argv[1:] пробрасывается байт-в-байт (--mode/--node-*/--owner-key...)
##   - Чистая функция: os.execv НЕ вызывается (тестируется без подмены процесса)
def _reexec_argv(target: str) -> list[str]:
    """Build os.execv argv preserving argv[0] (file- or -m-mode) — P0 F-01 fix."""
    main_pkg = getattr(sys.modules.get("__main__"), "__package__", "") or ""
    if main_pkg:
        argv = [target, "-m", f"{main_pkg}.cli", *sys.argv[1:]]
    else:
        # осознанный abspath (НЕ Path.resolve): argv[0] = путь запуска как есть, resolve
        # резолвил бы symlink и менял бы путь, которым был вызван скрипт (канон _delegate)
        argv = [target, os.path.abspath(sys.argv[0]), *sys.argv[1:]]  # ruff: ignore[PTH100] — осознанный abspath: argv[0] = путь запуска (resolve резолвил бы symlink, канон _delegate)
    logger.info("[IMP:9][reexec] Built re-exec argv: %s", argv)
    return argv


# endregion FUNC__reexec_argv


# region FUNC__reexec_lifecycle
## @purpose  Re-exec текущего процесса на целевой интерпретатор (P0 F-01). os.execv заменяет
##           процесс cli.py — функция НЕ возвращается (int для типизации). Состояние фаз уже
##           в state.json (done-фазы скипаются новым процессом — resume-семантика state_machine);
##           идемпотентность сохранена: второй прогон на 3.14 — no-op для done-фаз.
## @io       ⇥ target: str — путь интерпретатора → ⎋ int (unreachable; execv не возвращается)
## @complexity O(1) — env-marker + argv-build + os.execv
## @invariants
##   - Маркер _REEXEC_MARKER_ENV ставится ДО execv (loop-guard)
##   - argv строится _reexec_argv (argv[0] восстановлен: file-путь или -m spec — иначе
##     интерпретатор парсит --mode как СВОЙ опцион: "Unknown option: --mode", F-01 P0)
##   - state.json НЕ трогается (continuation через done-skip — главный инвариант идемпотентности)
def _reexec_lifecycle(target: str) -> int:
    """Re-exec the lifecycle CLI on the upgraded interpreter (os.execv — never returns)."""
    os.environ[_REEXEC_MARKER_ENV] = "1"
    reexec_argv = _reexec_argv(target)
    logger.critical(
        "[IMP:10][reexec] Switching lifecycle interpreter to %s (current %s < %s) — "
        "resuming from state.json (done phases skipped); argv=%s",
        target,
        sys.executable,
        ".".join(str(v) for v in _REEXEC_MIN_VERSION),
        reexec_argv,
    )
    os.execv(target, reexec_argv)  # ruff: ignore[S606] — осознанный exec без shell (re-exec, фиксированный путь, 0 инъекции)
    return 1  # pragma: no cover — execv никогда не возвращается


# endregion FUNC__reexec_lifecycle


# region FUNC__run_phases
## @purpose — Общий runner фаз init/update (W5-C2: run_init_mode/run_update_mode ~90% дубль,
##            research-A §4). Семантика 1:1 с pre-refactor: done/skip-проверка (dict + StepState,
##            done_with_warnings НЕ done — 117 D5), hash-инвалидация (T9.3/B-1), WARN-семантика
##            (result=False → done_with_warnings), 3 except-ветки с _audit_failed + sm.save()
##            (T9.6: audit в failure-путях), post_hooks → audit → notify. Разница init/update —
##            только список фаз и post_hooks (init: smoke+verify; update: пусто).
## @io — ⇥ sm: StateMachine, phases: Sequence[str] (INIT/UPDATE_PHASE_ORDER), mode_label: str
##          (лог-префикс "init"/"update" — [run_init]/[run_update] сохранены),
##          post_hooks: Sequence[Callable[[], object]] | None (после цикла, ДО audit/notify),
##          audit_fn/notify_fn: DI (None → write_audit_log/send_telegram) → ⎋ int exit code
## @complexity — O(N * M) where N = phases, M = per-phase operations
## @invariants
##   - exit-коды: PhaseDependencyError → e.exit_code|1; PhasePreconditionError → 1;
##     PlatformFatalError → e.exit_code; успех → 0
##   - done-фаза с изменённым hash (phase_needs_rerun) → re-run (status=pending);
##     done_with_warnings НЕ склипается — перевыполняется
##   - _audit_failed + sm.save() — во всех 3 failure-путях (T9.6)
##   - post_hooks — non-blocking контракт (smoke/verify): исключение из хука не меняет exit
##   - Лог-префиксы [run_init]/[run_update] и тексты сообщений идентичны pre-refactor
def _run_phases(
    sm: StateMachine,
    phases: Sequence[str],
    *,
    mode_label: str,
    post_hooks: Sequence[Callable[[], object]] | None = None,
    audit_fn: Callable[..., object] | None = None,
    notify_fn: Callable[..., object] | None = None,
) -> int:
    """Run phases sequentially with done/skip-check, WARN-семантика и audit (init/update общий)."""
    # QA R2 (DevPlan 14 T2.B): run-start timestamp — reader-side freshness hc_done-маркера
    # (φ11 принимает маркер только если marker mtime ≥ start прогона; маркер прошлого
    # прогона не глушит глубокий healthcheck).
    state_machine_mod.set_run_start_ts(time.time())
    audit_impl = write_audit_log if audit_fn is None else audit_fn
    notify_impl = send_telegram if notify_fn is None else notify_fn
    total = len(phases)
    for i, phase in enumerate(phases, 1):
        logger.info("[IMP:9][run_%s] Phase %d/%d: %s", mode_label, i, total, phase)

        # ── P0 (F-01, 2026-08-31): re-exec на Python 3.14, если текущий интерпретатор устарел ──
        # Срабатывает ДО любой pending-фазы: после φ1 (который через python_deps ставит 3.14) —
        # перед φ2; при resume/update — перед первой незавершённой фазой. done-фазы (в т.ч. φ1)
        # уже в state.json — новый процесс скипает их (resume-семантика state_machine, идемпотентно).
        reexec_target = _should_reexec_python()
        if reexec_target is not None:
            return _reexec_lifecycle(reexec_target)

        # ── Check if already done ──
        phase_state = sm.state.steps.get(phase)
        if phase_state is not None:
            if isinstance(phase_state, dict):
                phase_dict = cast(
                    "dict[str, object]", phase_state
                )  # W11-G3: raw-dict legacy ветка (state.json до StepState-миграции)
                # done_with_warnings НЕ считается done (волна 117 D5) — фаза перевыполняется.
                # dict-представление: done-ключ true только при status == "done" (пишется ниже).
                if phase_dict.get("status") == "done" or (
                    phase_dict.get("done", False) and phase_dict.get("status") in {None, "done"}
                ):
                    # T9.3 (L-4/B-1): content-hash инвалидация — done-фаза перевыполняется,
                    # если входы (modules/services node.yaml / код платформы) изменились.
                    if sm.phase_needs_rerun(phase):
                        logger.info(
                            "[IMP:8][run_%s] Phase %s done but inputs changed (hash) — re-running (T9.3)",
                            mode_label,
                            phase,
                        )
                        phase_dict["status"] = "pending"
                    else:
                        logger.info("[IMP:7][run_%s] Phase %s already done — skipping", mode_label, phase)
                        continue
            elif phase_state.status == "done":
                if sm.phase_needs_rerun(phase):
                    logger.info(
                        "[IMP:8][run_%s] Phase %s done but inputs changed (hash) — re-running (T9.3)",
                        mode_label,
                        phase,
                    )
                    phase_state.status = "pending"
                else:
                    logger.info("[IMP:7][run_%s] Phase %s already done — skipping", mode_label, phase)
                    continue

        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            result = sm.execute_phase(phase)
            # ── Mark phase status (волна 117 D5): WARN-семантика ──
            # result=True → done; result=False (non-fatal issues) → done_with_warnings,
            # который НЕ считается done → фаза перевыполняется при следующем run.
            if result:
                _mark_phase_success(sm, phase, current_index=i)
                logger.info("[IMP:9][run_%s] Phase %s completed successfully", mode_label, phase)
            else:
                _mark_phase_with_warnings(sm, phase)
                logger.warning(
                    "[IMP:7][run_%s] Phase %s completed WITH WARNINGS (done_with_warnings) — "
                    "will be re-executed on next %s",
                    mode_label,
                    phase,
                    mode_label,
                )
        except PhaseDependencyError as e:
            logger.error("[IMP:10][run_%s] Dependency error in phase %s: %s", mode_label, phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            # T9.6 (L-5/L-11): audit FAILED в failure-пути (не только при успешном завершении)
            _audit_failed(sm, phase, e, audit_fn=audit_impl)
            sm.save()
            return e.exit_code if hasattr(e, "exit_code") else 1
        except PhasePreconditionError as e:
            logger.error("[IMP:10][run_%s] Precondition failed for phase %s: %s", mode_label, phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            _audit_failed(sm, phase, e, audit_fn=audit_impl)
            sm.save()
            return 1
        except PlatformFatalError as e:
            logger.critical("[IMP:10][run_%s] Fatal error in phase %s: %s", mode_label, phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            _audit_failed(sm, phase, e, audit_fn=audit_impl)
            sm.save()
            return e.exit_code

    # ── Post-run: post_hooks (init: smoke + final verification; update: пусто) ──
    # Non-blocking контракт: хуки не меняют exit-код (FAIL → warning/print, exit 0 сохранён).
    for hook in post_hooks or ():
        hook()

    # ── Post-run: audit log + Telegram notification ──
    audit_impl(sm)
    notify_impl(sm)

    logger.info("[IMP:10][run_%s] All %d %s phases completed successfully", mode_label, total, mode_label)
    return 0


# endregion FUNC__run_phases


# region FUNC__audit_failed
## @purpose  Write FAILED audit entry for a phase that raised in run_init/run_update
##           (T9.6, L-5/L-11). Ранее audit писался ТОЛЬКО в успешном хвосте run_*_mode —
##           фейл фазы (PhaseDependencyError/PhasePreconditionError/PlatformFatalError)
##           покидал run без audit-следа. Здесь ошибка добавляется в sm.state.errors
##           (попадает в ERROR-записи write_audit_log) и пишется summary с result=FAILED.
## @io       ⇥ sm: StateMachine, phase: str, exc: Exception, audit_fn: Callable | None = None
##              (DI, W-H DevPlan 163 — audit-канал; None = write_audit_log) → ⎋ None (non-fatal)
## @complexity O(1) + audit write
## @invariants
##   - write_audit_log(sm, result="FAILED") — status FAILED в summary-записи
##   - Ошибка дублируется в sm.state.errors (ERROR-записи) — audit содержит контекст фейла
##   - Non-fatal: сбой audit-записи (OSError внутри write_audit_log) → WARN, exit-код не меняется
def _audit_failed(
    sm: StateMachine,
    phase: str,
    exc: Exception,
    audit_fn: Callable[..., object] | None = None,
) -> None:
    """Record a FAILED audit entry for a phase exception (T9.6 — audit in failure paths)."""
    audit_impl = write_audit_log if audit_fn is None else audit_fn
    try:
        sm.state.errors.append(f"Phase {phase} failed: {exc}")
        audit_impl(sm, result="FAILED")
        logger.info("[IMP:9][audit] FAILED audit entry written for phase %s", phase)
    # ruff: ignore[BLE001] — audit best-effort — не маскирует основной фейл фазы
    except Exception as e:  # noqa: EXC — best-effort: audit никогда не маскирует основной фейл
        logger.warning("[IMP:7][audit] Failed to write FAILED audit entry (non-fatal): %s", e)


# endregion FUNC__audit_failed


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
    # ⚠️ TRAP[BUG] · 2026-09-01 · HI · stale state.errors/warnings между прогонами bootstrap
    # · Symptom: run N упал в φ8 deploy_services ("Phase deploy_services failed: ... exit=10"
    #   в state.errors); run N+1 прошёл 9/9 успешно — отчёт всё ещё показывал
    #   "Failed: Phase deploy_services failed ..." и "Warnings: 1" (аккумулятор).
    # · Root: state.errors/warnings АККУМУЛИРУЮТ записи прошлых прогонов; при успешном
    #   перевыполнении фазы её старая ошибка не чистилась ни в state, ни в отчёте.
    # · Fix: (1) _prune_phase_records здесь (mark done) — state честен для ЛЮБОГО потребителя;
    #   (2) report-side stale-фильтр по done-фазам (post_bootstrap_report) — страховка для
    #   legacy state.json. Структура list[str] сохранена — back-compatible.
    # · Prevention: отчёт/аудит читают state.errors с учётом done-статуса фазы записи.
    _prune_phase_records(sm, phase)
    entry = sm.state.steps.get(phase)
    if isinstance(entry, dict):
        entry["done"] = True
        entry["status"] = "done"
        # T9.3 (L-4/B-1): content-hash входов сохраняется для hash-инвалидации при следующем run
        entry["hash"] = sm._phase_input_hash(phase)
    elif entry is not None:
        entry.status = "done"
        entry.hash = sm._phase_input_hash(phase)
    else:
        # StepState, НЕ raw dict: BootstrapState.to_dict() вызывает v.to_dict()
        # на каждом элементе steps — raw-dict крэшит save (латентный баг,
        # воспроизводится при отсутствующей фазе на resume).
        # ⚠️ TRAP[BUG] · 2026-08-05 · HI · StepState(name, status, done=True) → TypeError
        # · Symptom: resume БЕЗ setup_state (state.json с missing phase) → run_init_mode →
        #   _mark_phase_success → TypeError: StepState.__init__() got an unexpected keyword 'done'
        # · Root: StepState — dataclass БЕЗ поля done (поля: name/status/hash/started_at/error/
        #   reason/warnings); D8-фикс (67d9f10) передавал done=True/done=False в конструктор —
        #   ветка entry=None выполнялась только при missing phase на resume (DevPlan 136 W2 T2.7).
        # · Fix: убрать done= kwarg (status — единственный SoT; phase_is_done читает status).
        # · Prevention: StepState конструктор — только валидные поля dataclass.
        sm.state.steps[phase] = StepState(name=phase, status="done")
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
        warnings_list = cast(list[str], entry.setdefault("warnings", []))  # pyright: ignore[reportUnknownMemberType] — W11-G3: raw-dict ветка (dict[Unknown, Unknown]); каст к list[str]
        warnings_list.append(warn_msg)
    elif entry is not None:
        entry.status = "done_with_warnings"
        entry.warnings.append(warn_msg)
    else:
        # StepState, НЕ raw dict (см. _mark_phase_success — to_dict() контракт).
        # W2 T2.7 (DevPlan 136): done= kwarg убран — StepState dataclass без поля done.
        sm.state.steps[phase] = StepState(name=phase, status="done_with_warnings", warnings=[warn_msg])
    sm.state.warnings.append(warn_msg)
    sm.save()
    logger.warning("[IMP:7][state_mark] Phase %s marked done_with_warnings (re-run required)", phase)


# endregion FUNC__mark_phase_with_warnings


# region FUNC__maybe_run_preflight
## @purpose — Run preflight checks ONLY when there are pending/WARN phases (волна 117 D6).
##            Если state показывает все фазы done (без done_with_warnings) — preflight
##            пропускается с [IMP:9] логом. Вызов переехал из node-lifecycle.sh:60-64.
## @io — ⇥ sm: StateMachine, run_cmd: Callable | None = None (DI, W-H DevPlan 163:
##          subprocess-канал preflight; None = subprocess.run), docker_info_fn: Callable | None
##          (DI: liveness docker probe; None = docker_ops.docker_info) → ⎋ int
##          (0 = ok/skipped, 1 = preflight FAILED)
## @complexity — O(N) phases + O(1) preflight subprocess
## @invariants
##   - SKIP_PREFLIGHT env → skip (backward-compat с node-lifecycle.sh)
##   - preflight.py отсутствует → WARN + skip (non-fatal)
##   - Все фазы done (status == "done") → skip с [IMP:9]
##   - Preflight FAIL (FATAL probe) → return 1 (abort init, как node-lifecycle.sh:62)
##   - --parse-warnings второй вызов: warnings печатаются, НЕ влияют на exit
def _maybe_run_preflight(
    sm: StateMachine,
    *,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    docker_info_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Run preflight unless all init phases are already done (D6, волна 117)."""
    runner = subprocess.run if run_cmd is None else run_cmd
    if os.environ.get("SKIP_PREFLIGHT"):
        logger.info("[IMP:7][preflight] SKIP_PREFLIGHT set — skipping preflight")
        return 0

    core_dir = sm.core_dir or os.environ.get("CORE_DIR", str(platform_remote_base() / "core"))
    preflight_script = os.path.join(core_dir, "internal", "bootstrap", "preflight.py")
    if not os.path.isfile(preflight_script):
        logger.warning("[IMP:7][preflight] preflight.py not found at %s — skipping", preflight_script)
        return 0

    # ── D6 (волна 117): все фазы done (без WARN-статусов) → тяжёлый preflight не нужен ──
    # T9.17 (B-9, DevPlan 136 W9): НО no-op bootstrap не должен быть слепым — вместо тяжёлого
    # preflight выполняется ЛЁГКИЙ liveness-probe (docker info, диск, порт). Критический сбой
    # (docker daemon мёртв) → abort (нода с мёртвым docker = сломанная, no-op маскировал бы).
    all_done = all(phase_is_done(sm.state.steps.get(pv)) for pv in BootstrapPhase.phase_list("init"))
    if all_done:
        return _run_liveness_probe(sm, docker_info_fn=docker_info_fn)

    # ── Есть pending/WARN-фазы → выполнить (сохранённый путь node-lifecycle.sh:60-64) ──
    logger.info("[IMP:8][preflight] Running pre-flight checks (pending/WARN phases present)")
    node_yaml = os.environ.get("NODE_YAML", "")
    context = os.environ.get("CONTEXT", "")
    node_name = os.environ.get("NODE_NAME", "")
    try:
        proc = runner(
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
            timeout=LIFECYCLE_CMD_TIMEOUT,
            check=False,
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
        runner(
            ["python3", preflight_script, "--parse-warnings"],
            input=proc.stdout,
            capture_output=True,
            text=True,
            timeout=SYSTEM_CMD_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("[IMP:7][preflight] Preflight warnings parse failed (non-fatal): %s", e)
    return 0


# endregion FUNC__maybe_run_preflight


# region FUNC__run_liveness_probe
## @purpose  Lightweight liveness probe for no-op bootstrap (T9.17, B-9): все фазы done →
##           тяжёлый preflight заменяется быстрыми проверками «нода жива»: docker info
##           (критично) и заполненность диска на platform base (WARN).
##           Тяжёлые проверки preflight (сети, certs, registry) НЕ выполняются — фазы done,
##           их состояние верифицируется при следующем реальном деплое/healthcheck.
## @io       ⇥ sm: StateMachine, docker_info_fn: Callable | None = None (DI, W-H DevPlan 163 —
##              docker info probe; None = docker_ops.docker_info) → ⎋ int (0 = ok/warn, 1 = критический сбой)
## @complexity O(1) — 1 subprocess (docker info) + 1 statvfs
## @invariants
##   - docker info FAIL → IMP:10 + return 1 (abort — нода с мёртвым docker не «ок»)
##   - Диск на platform base заполнен более чем на 90% → WARN (non-fatal, return 0)
##   - Non-fatal лог: probe не дублирует тяжёлый preflight (D6-семантика сохранена)
# region FUNC__plw_body__run_liveness_probe
## @purpose  Тело try-блока (PLW0717 extraction из _run_liveness_probe) — семантика except не меняется.
## @io       ⇥ — → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__run_liveness_probe():
    import shutil

    base = str(platform_remote_base())
    usage = shutil.disk_usage(base)
    pct = usage.used / usage.total * 100
    if pct > _DISK_WARN_PCT:
        logger.warning("[IMP:7][liveness] Disk on %s at %.1f%% — above 90%% (non-fatal)", base, pct)
        logger.info("[IMP:8][liveness] Disk on %s: %.1f%% used (%.1f GiB free)", base, pct, usage.free / 2**30)


# endregion FUNC__plw_body__run_liveness_probe


def _run_liveness_probe(
    _sm: StateMachine,
    *,
    docker_info_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Run a lightweight liveness probe when all phases are done (T9.17)."""
    logger.info(
        "[IMP:9][liveness] All init phases done — running lightweight liveness probe (T9.17, not full preflight)"
    )
    failures = 0

    # ── 1. Docker daemon (критично) ──
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        if docker_info_fn is not None:
            docker_check = docker_info_fn()
        else:
            from core.internal.shared import docker_ops

            docker_check = docker_ops.docker_info()
        if docker_check.returncode == 0:
            logger.info("[IMP:9][liveness] docker daemon OK")
        else:
            logger.error(
                "[IMP:10][liveness] docker daemon NOT available: %s",
                (docker_check.stderr or docker_check.stdout).strip()[:200],
            )
            failures += 1
    # ruff: ignore[BLE001] — liveness probe — переживает любые сбои проверки
    except Exception as e:  # noqa: EXC — best-effort: probe должен переживать любые сбои проверки
        logger.error("[IMP:10][liveness] docker info probe error: %s", e)
        failures += 1

    # ── 2. Диск на platform base (не-fatal WARN) ──
    try:
        _plw_body__run_liveness_probe()
    except OSError as e:
        logger.warning("[IMP:7][liveness] Disk probe failed (non-fatal): %s", e)

    if failures:
        logger.error("[IMP:10][liveness] No-op bootstrap ABORTED: %d critical probe(s) failed", failures)
        return 1
    logger.info("[IMP:9][liveness] Liveness probe OK — no-op bootstrap continues")
    return 0


# endregion FUNC__run_liveness_probe


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PlatformError as e:
        logger.critical("[IMP:10][__main__] Platform error (exit=%d): %s", e.exit_code, e)
        print(f"{e}", file=sys.stderr)
        sys.exit(e.exit_code)
    # ruff: ignore[BLE001] — top-level __main__ handler for unexpected errors
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        logger.critical("[IMP:10][__main__] Unexpected error: %s", e)
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
