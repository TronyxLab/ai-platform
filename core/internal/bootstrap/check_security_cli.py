#!/usr/bin/env python3
# GREP_SUMMARY: check-security-cli entrypoint posture remote-executor auto-detect-node dry-run json local-fallback
# STRUCTURE: ▶ parse (--node/--dry-run/--json + passthrough) → ◇ auto-detect node → ◇ resolve host (rc=2 дискриминация) → ⊕ remote_executor.execute_check_security | local security_posture.py → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  CLI-слой `make check-security` (DevPlan 173 W2.4) — Python-порт check-security.sh
##           (90 LOC shell): arg-парсинг, node auto-detect, remote SSH proxy (remote_executor),
##           локальный fallback (no-SSH-host → security_posture.py). Фасад
##           core/entrypoints/check-security.sh → `exec python3 -m ...check_security_cli`.
## @scope    Вызывается из core/entrypoints/check-security.sh (make check-security NODE=<name>).
## @invariants
##   - --node recommended; missing → auto_detect_node_name (node_detect)
##   - --dry-run: remote_executor dry_run (печатает команды, не выполняет)
##   - --json passthrough — JSON-отчёт security_posture.py (L5-мониторинг)
##   - no SSH host / VPS self-detect → локальный exec security_posture.py (rc=2 дискриминация:
##     remote rc=2 = legit errors, НЕ маскируется — v1.0.1 TRAP[BUG] Фаза 6 d61e071)
##   - Exit codes: 0=healthy 1=warnings 2=errors (НЕ маскируются)
## @rationale Языковая политика: бизнес-логика (парсинг/детекция/fallback) → Python; entrypoint = exec.
## @changes  2026-08-16 | DevPlan 173 W2.4 — Created (порт check-security.sh)
## @changes  2026-08-16 | merge 173→main — rc=2 дискриминация (резолв хоста до вызова, канон remote_dispatch.py)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# self-bootstrap корня репо (канон context_deployer.py) — `from core.internal...` в любом cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.internal.bootstrap.overlay_deliverer import (
    NodeYamlNotFoundError,
    extract_node_host,
    resolve_node_yaml,
)
from core.internal.bootstrap.remote_executor import VPS_NODE_LIFECYCLE, RemoteExecutor
from core.internal.shared.env_facts import default_env_facts
from core.internal.shared.node_detect import NodeDetectionError, auto_detect_node_name
from core.internal.shared.ssh_cmd_builder import build_check_security_ssh_cmd

logger = logging.getLogger(__name__)

# security_posture.py — тот же каталог (bootstrap/)
_SECURITY_POSTURE = Path(__file__).resolve().parent / "security_posture.py"


# region FUNC__resolve_ssh_host
## @purpose  resolve node.yaml + extract host (rc=2 дискриминация, канон remote_dispatch.py).
## @io       ⇥ node: str → ⎋ host: str ("" = node.yaml не найден / host отсутствует)
## @complexity  O(n) — делегирует resolve_node_yaml + extract_node_host (≤4 кандидата)
def _resolve_ssh_host(node: str) -> str:
    """Resolve SSH host for rc=2 discrimination. Returns "" if unresolvable (no-host fallback)."""
    try:
        yaml_path = resolve_node_yaml(node)
        return extract_node_host(yaml_path)
    except NodeYamlNotFoundError:
        logger.info("[IMP:8][check-security][resolve] node.yaml/host unresolvable for node=%s — host=empty", node)
        return ""


# endregion FUNC__resolve_ssh_host


# region FUNC__local_fallback
## @purpose  Локальный fallback security check (no SSH host / on-VPS): exec security_posture.py.
## @io       ⇥ node_name, passthrough → ⎋ int exit code (проброс rc security_posture.py)
def _local_fallback(node_name: str, passthrough: list[str]) -> int:
    """Run security_posture.py LOCALLY (no SSH host / VPS self-detect)."""
    logger.info("[IMP:9][check-security][entrypoint] No SSH host — executing security_posture.py LOCALLY")
    cmd = [sys.executable, str(_SECURITY_POSTURE), "--node", node_name, *passthrough]
    return subprocess.run(cmd, check=False).returncode


# endregion FUNC__local_fallback


# region DATA_CliArgs
@dataclass
class CliArgs:
    """Typed CLI-аргументы check-security (W11: dataclass-граница argparse)."""

    node: str
    dry_run: bool
    json: bool


# endregion DATA_CliArgs


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `make check-security` (remote SSH proxy or local fallback).

    ▶ ┌argv┐ → ◇ auto-detect node → ◇ resolve host (rc=2 дискриминация) → ⊕ execute_check_security | local → ⎋ rc
    """
    parser = argparse.ArgumentParser(description="Security posture check (DevPlan 173 W2.4)")
    parser.add_argument("--node", "--node-name", dest="node", default="", help="Node name (or auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Print SSH command without executing")
    parser.add_argument("--json", dest="json", action="store_true", help="Emit JSON report (L5 monitoring)")
    args, unknown = parser.parse_known_args(argv)
    cli = cast(CliArgs, cast(object, args))

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    # ── Auto-detect if --node not provided ──
    node_name = cli.node
    if not node_name:
        logger.info("[IMP:9][check-security][entrypoint] --node not provided — attempting auto-detect")
        try:
            node_name = auto_detect_node_name()
        except NodeDetectionError as e:
            logger.error("[IMP:10][check-security][entrypoint] FATAL: --node is required (%s)", e)
            return 1
        logger.info("[IMP:9][check-security][entrypoint] Auto-detected NODE=%s", node_name)

    logger.info("[IMP:9][check-security][entrypoint] Starting security check for NODE=%s", node_name)

    passthrough = (["--json"] if cli.json else []) + list(unknown)

    # ── rc=2 дискриминация (v1.0.1 TRAP[BUG] Фаза 6, d61e071): remote rc=2 — ЛЕГИТИМНЫЙ
    #    вердикт (errors найдены, контракт 0=healthy 1=warnings 2=errors), НЕ сигнал «нет хоста».
    #    Fallback — только по резолву хоста ДО вызова (канон remote_dispatch.py): host пуст
    #    ИЛИ VPS self-detect (node-lifecycle.sh на диске) → локальный security_posture.py.
    ssh_host = _resolve_ssh_host(node_name)
    if not ssh_host or default_env_facts().path_isfile(VPS_NODE_LIFECYCLE):
        return _local_fallback(node_name, passthrough)

    # ── SSH proxy (host есть, не на VPS): remote rc пробрасывается как есть ──
    remote_cmd = build_check_security_ssh_cmd(node_name, passthrough)
    dry_run = cli.dry_run or os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    return RemoteExecutor(dry_run=dry_run).execute_check_security(node_name, remote_cmd, " ".join(passthrough))


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
