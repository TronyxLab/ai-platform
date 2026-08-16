#!/usr/bin/env python3
# GREP_SUMMARY: remote-dispatch converge node-update unified-verb rc2-rc3-discrimination ssh-proxy local-fallback passthrough age-key self-bootstrap
# STRUCTURE: ▶ CLI --verb converge|update → parse (--node/--dry-run/--reconcile/--age-secret-key-file + passthrough) → ⚡ converge: resolve host → RemoteExecutor.execute_converge → ◇ rc=2 ∧ host? → exit 2 (R-unit) │ host∅ → local converge.sh → ⎋ 0|1|2
#            ▶ update: detect_age_key (rc=3 non-fatal) → deliver_vhost_overlays → RemoteExecutor.execute_update → ◇ rc=2 → local node-lifecycle.sh → ⎋ 0|1|2
# region MODULE_CONTRACT
## @purpose  Единый Python-dispatch для entrypoints converge.sh / node-update.sh (DevPlan 170 W9-F2,
##           research-A §9): CLI `--verb converge|update` + общие флаги; вся бизнес-логика rc=2/rc=3
##           дискриминации, passthrough-сборки, SSH-прокси-вызова и локального fallback перенесена
##           из shell-двойников. SSH через СУЩЕСТВУЮЩИЕ каналы: ssh_cmd_builder.build_*_ssh_cmd
##           (printf %q) + RemoteExecutor (subprocess ssh, SSH_OPTS) — SSH НЕ переизобретается.
## @scope    core/internal/bootstrap/remote_dispatch.py — импортируется converge.sh/node-update.sh
##           (script-path self-bootstrap) или `python3 -m core.internal.bootstrap.remote_dispatch`.
##           Локальные fallback'и — субагенты: core/internal/bootstrap/converge.sh (converge) и
##           core/internal/bootstrap/node-lifecycle.sh --mode update (update).
## @invariants
##   - --verb required: converge | update (argparse, usage exit 2 — rc=2 usage-семантика)
##   - converge: --node опционален (auto-detect через node_detect.auto_detect_node_name, fail → exit 1)
##   - update: --node ОБЯЗАТЕЛЕН (fail → exit 1, usage-подсказка)
##   - rc=2 дискриминация (142 B28b, TRAP[BUG] 2026-08-07): converge — host из node.yaml ДО вызова;
##     host есть → rc=2 = R-unit errors ноды (exit 2, БЕЗ локального прогона); host пуст → no-SSH-host
##     (локальный fallback). update — любой rc=2 → локальный fallback (как shell node-update.sh).
##   - rc=3 семантика (node_detect, DevPlan 104 D3): age-key absent = non-fatal → age_key="" (Python
##     detect_age_key() → None; CLI-код 3 не воспроизводится — сигнал выражается значением None).
##   - --age-secret-key-file → os.environ["AGE_SECRET_KEY_FILE"] (export-эквивалент shell)
##   - --reconcile и неизвестные флаги → passthrough (семантика 1:1 с shell PASSTHROUGH_ARGS)
##   - Exit-коды сохраняются: 0=ok, 1=fatal, 2=remote R-unit errors / usage, 124=ssh timeout
##   - DRY_RUN: remote_executor печатает команды (exit 0); локальный fallback update — печать + exit 0
##   - SSH-каналы НЕ дублируются: build_*_ssh_cmd + RemoteExecutor — единственные исполнители
## @rationale Strangler-Fig (research-A §9): converge.sh (124) + node-update.sh (119) — двойники
##            с общей rc-протоколикой → единый Python-модуль с unit-тестами; shell остаётся тонкими
##            фасадами (<60 LOC, 0 бизнес-логики). rc=2/rc=3 дискриминация — testable в Python.
## @changes 2026-08-15 | Created (DevPlan 170 W9-F2) — логика перенесена из converge.sh/node-update.sh
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import pathlib
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

# ── self-bootstrap (script-path канон, паттерн converge.sh:64): script-path exec НЕ добавляет
# CWD в sys.path — корень вставляется явно (идемпотентно). Работает и при python3 -m, и при
# импорте тестами (корень уже в sys.path — insert no-op).
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.bootstrap.overlay_deliverer import (
    DeliveryError,
    NodeYamlNotFoundError,
    deliver_vhost_overlays,
    extract_node_host,
    resolve_node_yaml,
)
from core.internal.bootstrap.remote_executor import RemoteExecutor
from core.internal.shared.node_detect import (
    NodeDetectionError,
    auto_detect_node_name,
    detect_age_key,
)
from core.internal.shared.ssh_cmd_builder import (
    build_converge_ssh_cmd,
    build_update_ssh_cmd,
)

# LDD-канал entrypoint'а: shell-версии (converge.sh/node-update.sh) печатали IMP:7-10 через echo >&2
# ВСЕГДА — INFO-уровень воспроизводит контракт (оператор/тесты видят траекторию). remote_executor
# (WARNING) остаётся тихим исполнителем — dispatch поверх него отвечает за видимость.
# ВАЖНО: basicConfig(INFO) БЕЗ force — импорт remote_executor уже применил basicConfig(WARNING);
# безопасный паттерн: root level=INFO, handlers НЕ перезаписываются (у StreamHandler level=0 —
# фильтрация на уровне логгера; caplog/handler'ы тестов не ломаются).
# ⚠️ TRAP[BUG] · 2026-08-15 · P1 · LDD-логи entrypoint'а подавлялись basicConfig(WARNING) зависимого импорта
# · Symptom: node-update.sh --node test-e2e --dry-run → rc=0, stderr ПУСТ → контракт-тест
# ·   test_node_lifecycle_dry_run_contract («Entrypoint must reach main()») падал; оператор не видел
# ·   [IMP:7-10] траекторию converge/node-update (shell-версии печатали echo >&2 всегда).
# · Root: remote_executor.py:60 вызывает logging.basicConfig(level=WARNING) на МОДУЛЬНОМ уровне →
# ·   при импорте remote_dispatch root уже имеет handlers → basicConfig(INFO) в этом модуле — no-op
# ·   (basicConfig применяется ТОЛЬКО если handlers ещё нет), INFO-логи отфильтрованы уровнем WARNING.
# · Fix: паттерн «root level без перезаписи handlers»: if not root.handlers → basicConfig(INFO);
# ·   else → root.setLevel(INFO) (StreamHandler level=0 — фильтрация на логгере, caplog не ломается).
# · Prevention: entrypoint-модули, импортирующие зависимости с basicConfig, НЕ полагаются на
# ·   basicConfig() — явный root.setLevel (или явный handler на своём логгере с propagate=False).
# · Rejected: basicConfig(level=INFO, force=True) — перезаписывает чужие handlers (риск для
# ·   pytest-caplog и соседних модулей в том же процессе) — альтернатива отклонена.
_ROOT_LOGGER = logging.getLogger()
if not _ROOT_LOGGER.handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
else:
    _ROOT_LOGGER.setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# ── Локальные субагенты (mirror PATHS_INTERNAL_DIR из lib/paths.sh) ─────────
_INTERNAL_DIR = _PROJECT_ROOT / "core" / "internal" / "bootstrap"
_CONVERGE_INTERNAL = _INTERNAL_DIR / "converge.sh"
_NODE_LIFECYCLE_INTERNAL = _INTERNAL_DIR / "node-lifecycle.sh"

# ── rc-протокол remote_executor (SoT: remote_executor.py @invariants) ───────
# 0=success, 1=fatal, 2=local fallback / R-unit errors (дискриминация по ssh_host), 124=ssh timeout
RC_LOCAL_FALLBACK = 2


# region CLS_Args
@dataclass
class Args:
    """Разобранный CLI-вход remote_dispatch.

    ## @purpose  Неизменяемый носитель разобранных аргументов для run_converge/run_update.
    ## @io       field'ы: verb/node/dry_run/passthrough/age_secret_key_file
    ## @complexity  O(1)
    """

    verb: str = ""
    node: str = ""
    dry_run: bool = False
    passthrough: list[str] = field(default_factory=list)
    age_secret_key_file: str = ""


# endregion CLS_Args


# region CLASS_ArgsNamespace
class _ArgsNamespace(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_known_args(namespace=...)."""

    def __init__(self) -> None:
        super().__init__()
        self.verb: str
        self.node: str
        self.dry_run: bool
        self.reconcile: bool
        self.age_secret_key_file: str


# endregion CLASS_ArgsNamespace


# region FUNC_parse_args
## @purpose  Разбор CLI: --verb (required), общие флаги, passthrough-аккумуляция.
##           Argparse.parse_known_args: --reconcile + неизвестные флаги → passthrough (семантика 1:1
##           с shell PASSTHROUGH_ARGS; --report-only/--units converge уходят в passthrough).
## @io       ⇥ argv: Sequence[str] → ⎋ Args
## @complexity  O(n) — parse_known_args
## @invariants  --help/-h → argparse печатает usage, SystemExit(0) (help smoke-контракт);
##              --verb missing/invalid → SystemExit(2) (rc=2 usage-семантика, как argparse-канон)
def parse_args(argv: Sequence[str]) -> Args:
    """Parse CLI args (--verb + общие флаги + passthrough)."""
    p = argparse.ArgumentParser(
        prog="remote_dispatch.py",
        description="Единый dispatch converge/node-update (DevPlan 170 W9-F2).",
        epilog="Passthrough-флаги (--report-only, --units, ...) форвардятся в субагенты без изменений.",
    )
    p.add_argument("--verb", required=True, choices=("converge", "update"), help="Операция")
    p.add_argument("--node", "--node-name", dest="node", default="", help="Node name (converge: auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="Печать команд без выполнения")
    p.add_argument("--reconcile", action="store_true", help="Passthrough: reconcile stub projects (W4)")
    p.add_argument(
        "--age-secret-key-file",
        dest="age_secret_key_file",
        default="",
        help="Path to AGE secret key file (update; export AGE_SECRET_KEY_FILE)",
    )
    known, unknown = p.parse_known_args(list(argv), namespace=_ArgsNamespace())
    passthrough: list[str] = ["--reconcile"] if known.reconcile else []
    passthrough.extend(unknown)
    return Args(
        verb=known.verb,
        node=known.node or "",
        dry_run=bool(known.dry_run),
        passthrough=passthrough,
        age_secret_key_file=known.age_secret_key_file or "",
    )


# endregion FUNC_parse_args


# region FUNC__resolve_ssh_host
## @purpose  resolve node.yaml + extract host (rc=2 дискриминация converge). Ошибки → "" (нет хоста).
## @io       ⇥ node: str → ⎋ host: str ("" = node.yaml не найден / host отсутствует)
## @complexity  O(n) — делегирует NodeYaml.resolve (≤4 кандидата)
## @invariants  Тот же канал, что remote_executor._resolve_host (overlay_deliverer), но с мягким
##              fallback на "" — shell-семантика converge.sh:90 (`... 2>/dev/null) || ssh_host=""`)
def _resolve_ssh_host(node: str) -> str:
    """Resolve SSH host for rc=2 discrimination. Returns "" if unresolvable."""
    try:
        yaml_path = resolve_node_yaml(node)
        return extract_node_host(yaml_path)
    except NodeYamlNotFoundError:
        logger.info("[IMP:8][remote_dispatch][resolve] node.yaml/host unresolvable for node=%s — host=empty", node)
        return ""


# endregion FUNC__resolve_ssh_host


# region FUNC__local_converge_fallback
## @purpose  Локальный fallback converge (no SSH host): exec bash core/internal/bootstrap/converge.sh
##           --node ... [--dry-run] +passthrough-args+ (внутренний обрабатывает --dry-run сам).
## @io       ⇥ node: str, args: Args → ⎋ int exit code (проброс rc внутреннего)
## @complexity  O(1) — is_file + subprocess.call
## @invariants  Internal script обязателен (missing → exit 1, как shell converge.sh:102-105);
##              --dry-run → флаг субагенту (НЕ ранний exit — TRAP[BUG] 2026-07-23 P0 закрыт)
def _local_converge_fallback(node: str, args: Args) -> int:
    """Execute internal bootstrap/converge.sh LOCALLY (backward-compatible fallback)."""
    if not _CONVERGE_INTERNAL.is_file():
        logger.error("[IMP:10][remote_dispatch][converge] FATAL: Internal script not found at %s", _CONVERGE_INTERNAL)
        return 1
    cmd = ["bash", str(_CONVERGE_INTERNAL), "--node", node]
    if args.dry_run:
        cmd.append("--dry-run")
    cmd.extend(args.passthrough)
    logger.info("[IMP:8][remote_dispatch][converge] Delegating to %s", _CONVERGE_INTERNAL)
    return subprocess.call(cmd)


# endregion FUNC__local_converge_fallback


# region FUNC__local_update_fallback
## @purpose  Локальный fallback update (no SSH host): resolve node.yaml (fatal on fail) →
##           bash node-lifecycle.sh --mode update --node-name ... --node-yaml ... [--dry-run] +passthrough-args+.
##           DRY_RUN → печать команды + exit 0 (shell node-update.sh:108-112 семантика).
## @io       ⇥ node: str, args: Args → ⎋ int exit code
## @complexity  O(n) — resolve + subprocess.call
## @invariants  node.yaml resolve обязателен (missing → exit 1, shell :101-104);
##              passthrough форвардится 1:1 (в т.ч. --reconcile — как делал shell)
def _local_update_fallback(node: str, args: Args) -> int:
    """Execute node-lifecycle.sh --mode update LOCALLY (backward-compatible fallback)."""
    if not _NODE_LIFECYCLE_INTERNAL.is_file():
        logger.error(
            "[IMP:10][remote_dispatch][update] FATAL: Internal script not found at %s", _NODE_LIFECYCLE_INTERNAL
        )
        return 1
    try:
        node_yaml = resolve_node_yaml(node)
    except NodeYamlNotFoundError as exc:
        logger.error("[IMP:10][remote_dispatch][update] FATAL: Cannot resolve node.yaml for node=%s: %s", node, exc)
        return 1
    cmd = ["bash", str(_NODE_LIFECYCLE_INTERNAL), "--mode", "update", "--node-name", node, "--node-yaml", node_yaml]
    if args.dry_run:
        cmd.append("--dry-run")
        logger.info("[IMP:8][remote_dispatch][update][dry-run] DRY-RUN: %s", " ".join(cmd))
        logger.info("[IMP:9][remote_dispatch][update][dry-run] DRY-RUN complete")
        return 0
    cmd.extend(args.passthrough)
    logger.info("[IMP:8][remote_dispatch][update] Delegating to %s --mode update", _NODE_LIFECYCLE_INTERNAL)
    return subprocess.call(cmd)


# endregion FUNC__local_update_fallback


# region FUNC_run_converge
## @purpose  Полный converge-цикл: node resolve (auto-detect) → ssh_host (rc=2 дискриминация) →
##           build_converge_ssh_cmd → RemoteExecutor.execute_converge → rc=2 ветвление (R-unit vs fallback).
## @io       ⇥ args: Args, executor: RemoteExecutor | None (DI-шов) → ⎋ int exit code
## @complexity  O(1) + O(ssh) — resolve + ssh-вызов
## @invariants  executor=None → RemoteExecutor(dry_run=args.dry_run) (prod-канал);
##              --node пуст → auto_detect_node_name (NodeDetectionError → exit 1, shell :63-71);
##              ssh_host определён ДО вызова (TRAP[BUG] 2026-08-07 P1 — различение rc=2)
def run_converge(args: Args, *, executor: RemoteExecutor | None = None) -> int:
    """Run converge verb: resolve host, SSH proxy, rc=2 discrimination, local fallback."""
    node = args.node
    if not node:
        try:
            node = auto_detect_node_name()
        except NodeDetectionError as exc:
            logger.error("[IMP:10][remote_dispatch][converge] FATAL: --node is required (auto-detect failed: %s)", exc)
            sys.stderr.write("  Usage: converge.sh --node <name> [--dry-run]\n")
            return 1
        logger.info("[IMP:9][remote_dispatch][converge] Auto-detected NODE=%s", node)
    logger.info("[IMP:9][remote_dispatch][converge] Starting converge for NODE=%s", node)

    # ⚠️ TRAP[BUG] · 2026-08-07 · P1 · rc=2 от REMOTE converge ложно трактовался как self-detect
    # (перенесено из converge.sh:82-87): host из node.yaml ДО вызова — host есть → rc=2 = R-unit
    # errors ноды (exit 2, БЕЗ локального прогона); host пуст → rc=2 = no-SSH-host (fallback).
    ssh_host = _resolve_ssh_host(node)
    remote_cmd = build_converge_ssh_cmd(node, args.passthrough)
    exec_instance = executor if executor is not None else RemoteExecutor(dry_run=args.dry_run)
    remote_rc = exec_instance.execute_converge(node, remote_cmd, " ".join(args.passthrough))
    logger.info("[IMP:8][remote_dispatch][converge] remote_executor rc=%s", remote_rc)

    if remote_rc == RC_LOCAL_FALLBACK:
        if ssh_host:
            logger.info(
                "[IMP:8][remote_dispatch][converge] Remote converge on %s returned rc=2 (R-unit errors) — forwarding, NO local fallback",
                ssh_host,
            )
            return 2
        logger.info("[IMP:9][remote_dispatch][converge] No SSH host — executing converge.sh LOCALLY")
        return _local_converge_fallback(node, args)
    return remote_rc


# endregion FUNC_run_converge


# region FUNC_run_update
## @purpose  Полный update-цикл: --node валидация → AGE_SECRET_KEY_FILE export → detect_age_key
##           (rc=3 non-fatal) → deliver_vhost_overlays (S2, skip на dry-run) → build_update_ssh_cmd →
##           RemoteExecutor.execute_update → rc=2 → локальный fallback node-lifecycle.sh.
## @io       ⇥ args: Args, executor: RemoteExecutor | None (DI-шов) → ⎋ int exit code
## @complexity  O(f + m + ssh) — deliver overlays + ssh
## @invariants  --node обязателен (exit 1, shell :58-62);
##              detect_age_key() → None = rc=3 non-fatal (age_key="", shell :68-75);
##              deliver_vhost_overlays только при !dry_run (shell :77-83); DeliveryError → exit 1
def run_update(args: Args, *, executor: RemoteExecutor | None = None) -> int:
    """Run update verb: age key detect, vhost overlays, SSH proxy, local fallback."""
    node = args.node
    if not node:
        logger.error("[IMP:10][remote_dispatch][update] FATAL: --node is required")
        sys.stderr.write("  Usage: node-update.sh --node <name> [--dry-run]\n")
        return 1
    if args.age_secret_key_file:
        # export-эквивалент shell (--age-secret-key-file → AGE_SECRET_KEY_FILE)
        os.environ["AGE_SECRET_KEY_FILE"] = args.age_secret_key_file
    logger.info("[IMP:9][remote_dispatch][update] Starting node-update for NODE=%s", node)

    # rc=3 семантика (DevPlan 104 D3): module OK + key absent = non-fatal → age_key=""
    age_key = detect_age_key()
    if age_key:
        logger.info("[IMP:9][remote_dispatch][update] AGE key detected")
    else:
        logger.info(
            "[IMP:8][remote_dispatch][update] AGE key absent (rc=3 non-fatal) — Docker modules requiring secrets may fail"
        )

    # ── S2 (DevPlan 019): deliver generated vhost overlays (только не dry-run) ──
    if not args.dry_run:
        try:
            deliver_vhost_overlays(node)
        except DeliveryError as exc:
            logger.error("[IMP:10][remote_dispatch][update] FATAL: Vhost overlay delivery failed: %s", exc)
            return 1

    remote_cmd = build_update_ssh_cmd(node, age_key or "", args.passthrough)
    exec_instance = executor if executor is not None else RemoteExecutor(dry_run=args.dry_run)
    remote_rc = exec_instance.execute_update(node, remote_cmd, " ".join(args.passthrough))
    logger.info("[IMP:8][remote_dispatch][update] remote_executor rc=%s", remote_rc)

    if remote_rc == RC_LOCAL_FALLBACK:
        logger.info("[IMP:9][remote_dispatch][update] No SSH host — executing node-lifecycle.sh --mode update LOCALLY")
        return _local_update_fallback(node, args)
    return remote_rc


# endregion FUNC_run_update


# region FUNC_main
## @purpose  CLI entrypoint: parse_args → dispatch converge|update → int exit code.
## @io       ⇥ argv: Sequence[str] | None (default sys.argv[1:]), executor: RemoteExecutor | None (DI)
##           → ⎋ int exit code (0/1/2/124)
## @complexity  O(1) — dispatch-only
def main(argv: Sequence[str] | None = None, *, executor: RemoteExecutor | None = None) -> int:
    """Parse args and dispatch converge|update. Returns exit code — sys.exit в __main__."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.verb == "converge":
        return run_converge(args, executor=executor)
    return run_update(args, executor=executor)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
