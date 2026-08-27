#!/usr/bin/env python3
# GREP_SUMMARY: vps-readiness preflight ssh docker forced-command remediation node-host-map readiness-check
# STRUCTURE: ▶ check_vps_ready(node, output_mode, quick_mode) → ◇ resolve NODE_HOST_MAP → ○ SSH(30s) → ○ ping→"pong" → ○ /opt/projects/ OK → ○ docker(skip --quick) → ⊕ failures list[dict] → ⎋ (all_ok, diagnostics)
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of core/lib/vps-readiness.sh (181 LOC bash → Python).
##           Runs 4 pre-flight VPS readiness checks (SSH accessibility, forced-command ping,
##           /opt/projects/ exists+writable, Docker daemon) with fail-fast semantics and
##           per-failure remediation hints. CLI: python3 -m core.internal.shared.vps_readiness NODE [--json|--quick].
## @scope    Called from core/lib/vps-readiness.sh shell facade (sourced by makefiles/deploy.mk:37-38).
##           Provides: check_vps_ready(), default_ssh_runner(), _resolve_node_host(),
##           _build_ready_diagnostics(), _build_json_diagnostics(), CLI __main__.
## @invariants
##   - 4 checks ordered by failure probability: SSH → forced-command ping → /opt/projects → Docker
##   - Fail-fast: first failing check stops the chain (all_ok boolean, DevPlan 105 D3)
##   - JSON diagnostics built via data structures (list[dict] → json.dumps) — NO string concatenation
##   - SSH goes through lib/ssh.sh ssh_read via subprocess-bash (single source of truth, D1)
##   - NODE_HOST_MAP parsed via json.loads — no yaml_query.py subprocess (D2, kills P3)
##   - exit semantics: (True,  {"status": "ready", ...})    — all checks passed
##                     (False, {"status": "not_ready", ...}) — one or more checks failed
##   - remediation hints preserved for EVERY failure mode (AC7)
## @rationale Last lib-file with business logic in bash (language policy, root AGENTS.md).
##            Strangler-Fig: shell facade preserves check_vps_ready() API for deploy.mk;
##            business logic becomes unit-testable via DI ssh_runner. Fixes latent $first bug.
## @links    PRECEDENT: core/internal/scaffold/project_lister.py:287 (_ssh_read DI),
##           core/internal/scaffold/project_remover.py:253 (_default_ssh DI)
##           CALLED_BY: core/lib/vps-readiness.sh (facade) ← makefiles/deploy.mk:37-38
## @changes  2026-07-31 | Initial implementation (DevPlan 105)
# endregion MODULE_CONTRACT

# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Bash $first || json_diag+="," executes `false` → broken JSON
# · Symptom: vps-readiness.sh:170 `$first || json_diag+=","` — after first=false, bash tries to run
# ·          the command `false` → `false: command not found` + JSON accumulates stray ","
# · Root: bash treats `$first` as a COMMAND to execute; the intent was a string-comparison guard.
# · Fix: JSON built via Python data structures (list[dict] → json.dumps) — concatenation excluded.
# · Prevention: ANTI-SURVIVORSHIP test test_json_no_extra_commas (R5) covers ≥2-failure serialization.

# 🧐 TRAP[DECISION] · 2026-07-31 · — · ssh_read via subprocess-bash (not direct Python SSH)
# · Rejected: direct paramiko/subprocess ssh — duplicates SSH_OPTS_COMMON, timeout, exit=124 logic
# · Reason: lib/ssh.sh is the single source of truth for all SSH operations (TRAP[DECISION]
# ·         2026-07-21 in lib/ssh.sh, precedent project_lister.py:44/287, project_remover.py:253).
# ·         Precedent rule: extract Python SSH runner only when consumers > 3 or overhead matters.
# · Rev: if 4th consumer of ssh_read appears → extract shared Python SSH runner.

# ⚡ TRAP[DECISION] · 2026-07-31 · — · macOS без GNU timeout (DevPlan 105 R2)
# · Rejected: bash-level GNU `timeout` wrapper inside default_ssh_runner (missing on macOS dev)
# · Reason: Python-level subprocess.run(timeout=timeout+5) catches hangs portably. On macOS the
# ·         subprocess may surface TimeoutExpired instead of bash exit=124 — diagnosis less precise,
# ·         but pre-flight runs (4 checks × 30s) have bounded cost. Production (Linux) unaffected.
# · Rev: if macOS dev needs precise 124-detection → brew install coreutils && gtimeout in ssh.sh.

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# B8: SSH-таймаут — канон shared/timeouts.SSH_CONNECT_TIMEOUT (литерал SSH_CONNECT_TIMEOUT=30 удалён)
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170)."""

    node: str
    output_mode: str
    quick: bool


# endregion DATACLASS_CliArgs

# ── Constants ─────────────────────────────────────────────────────────────────
SSH_USER = "ci-deploy"
# Все 4 проверки используют timeout=SSH_CONNECT_TIMEOUT (QA Review F2: код авторитетнее Brief)
# NODE_HOST_MAP — JSON-строка node→host в env (K4/K5 pattern)
_NODE_HOST_MAP_ENV = "NODE_HOST_MAP"
_PROJECTS_CHECK_CMD = "test -d /opt/projects && test -w /opt/projects && echo OK || echo FAIL"
# Обычная строка (НЕ f-string): {{.ServerVersion}} должен дойти до remote-шелла дословно
_DOCKER_CHECK_CMD = "docker info --format '{{.ServerVersion}}' 2>/dev/null || echo FAIL"
# 📝 TRAP[DEBT] · 2026-08-27 · MED · Docker-check шага 4 — false-PASS через forced-command
# · Observed: при изучении SSH-канала для health-пробки (project_payload_delivery, B3) —
# ·   ci-deploy authorized_keys forced-command-restricted (S7, users.py:654) → `ssh ci-deploy@host
# ·   "docker info ..."` исполнит orchestrator_cli dispatch с SSH_ORIGINAL_COMMAND="docker info ..."
# ·   → unknown verb → JSON-ошибка + exit 4; docker_out.strip() = JSON-ошибка ≠ "FAIL" → шаг
# ·   проходит ложно («Docker OK: version <json-error>») — daemon реально не проверяется
# · Suspected: needs verification на живой ноде (зависит от того, каким ключом фактически
# ·   соединяется default_ssh_runner — agent/config ci_deploy_key vs personal key); шаг 3
# ·   (test -d /opt/projects) при том же канале фейлится раньше — см. поведение pre-flight
# · Impact: docker-готовность в pre-flight может подтверждаться без реальной проверки daemon
# · When: реализация B3 health-пробки (2026-08-27) — тот же forced-command канал
# · Fix-forward: read-only health/inspect verb в dispatch-реестре (orchestrator_cli) —
# ·   единый канал и для pre-flight, и для skip-health пробки
# R1: абсолютный путь к lib/ssh.sh — резолвится от __file__, не от cwd
_SSH_LIB_PATH = str(Path(__file__).resolve().parent.parent.parent / "lib" / "ssh.sh")

# Remediation hints — по одному на каждый failure mode (AC7), сохранены из bash-версии
_REMEDIATION_NO_MAP = 'Set NODE_HOST_MAP env var: export NODE_HOST_MAP=\'{"node":"host"}\''
_REMEDIATION_SSH = (
    "VPS unreachable. Check: ssh ci-deploy@<host> — verify network, SSH key, and ci-deploy user existence"
)
_REMEDIATION_PING = "Core not delivered. Run: make bootstrap-node NODE=<node> first"
_REMEDIATION_PROJECTS = "Project base missing. Run: make bootstrap-node NODE=<node>"
_REMEDIATION_DOCKER = "Docker not running. Run: systemctl start docker on VPS (or check Docker socket permissions)"
# Ключевые команды для mock-диспетчеризации в тестах (проверяется по содержимому cmd)
CMD_PING = "ping"
CMD_EXIT = "exit"


# region FUNC_ssh_runner
## @purpose  Default SSH runner — executes remote command via lib/ssh.sh::ssh_read through
##           subprocess-bash. Preserves the ssh.sh single-source-of-truth contract
##           (SSH_OPTS_COMMON, timeout wrapper, exit=124 detection).
## @param host        SSH host (IP or domain)
## @param user        SSH user
## @param cmd         Remote command to execute
## @param timeout     Bash-level ssh_read timeout in seconds
## @param ssh_lib_path  Override path to core/lib/ssh.sh (R1; default: resolved from __file__)
## @io        ⎋ (exit_code: int, stdout: str) — stderr намеренно отбрасывается (как в project_remover.py)
## @complexity O(1) — single subprocess call
## @invariants
##   - Python-level timeout = bash timeout + 5s (ловит зависание на macOS без GNU timeout, R2)
##   - subprocess.TimeoutExpired / FileNotFoundError → (1, message) — fail-verbose, без исключений
def default_ssh_runner(
    host: str,
    user: str,
    cmd: str,
    timeout: int,
    ssh_lib_path: str | None = None,
) -> tuple[int, str]:
    """Execute remote command via lib/ssh.sh ssh_read (subprocess-bash pattern).

    ▶ ┌host,user,cmd,timeout┐ → ⚡ subprocess(bash -c 'source ssh.sh && ssh_read ...') → ◇ rc → ⎋ (rc, stdout)
    """
    lib_path = ssh_lib_path or _SSH_LIB_PATH
    logger.info(
        "[IMP:7][vps_readiness][ssh_runner] ssh_read %s@%s (timeout=%ss, lib=%s)", user, host, timeout, lib_path
    )
    try:
        result = subprocess.run(
            ["bash", "-c", f'source "{lib_path}" && ssh_read "{host}" "{user}" "{cmd}" {timeout}'],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout + 5,
        )
        logger.info(
            "[IMP:8][vps_readiness][ssh_runner] ssh_read rc=%s stdout=%.120s",
            result.returncode,
            result.stdout.strip() or "<empty>",
        )
    except subprocess.TimeoutExpired:
        logger.info("[IMP:10][vps_readiness][ssh_runner] Python-level TimeoutExpired after %ss", timeout + 5)
        return 1, "SSH timeout (Python-level TimeoutExpired — macOS без GNU timeout, см. TRAP)"
    except FileNotFoundError as exc:
        logger.info("[IMP:10][vps_readiness][ssh_runner] bash not found: %s", exc)
        return 1, f"bash not found: {exc}"
    else:
        return result.returncode, result.stdout


# endregion FUNC_ssh_runner


# region FUNC__resolve_node_host
## @purpose  Resolve node_name → SSH host from NODE_HOST_MAP (JSON в env или injected dict).
##           Kills P3: прямой json.loads вместо subprocess yaml_query.py (D2).
## @param node_name       Node name to look up
## @param node_host_map   Injected dict for tests; None → читается из os.environ["NODE_HOST_MAP"]
## @io        ⎋ (host: str | None, error_msg: str, remediation: str)
## @complexity O(1) — single json.loads + dict lookup
## @invariants
##   - env unset / invalid JSON / node not found → (None, msg, remediation)
##   - node not found → remediation содержит available keys (как в bash-версии)
def _resolve_node_host(
    node_name: str,
    node_host_map: dict[str, str] | None = None,
) -> tuple[str | None, str, str]:
    """Resolve node→host from NODE_HOST_MAP.

    ▶ ◇ env/inject → ⚡ json.loads → ◇ lookup node_name → ⎋ (host | None, msg, remediation)
    """
    if node_host_map is None:
        raw_map = os.environ.get(_NODE_HOST_MAP_ENV)
        if not raw_map:
            logger.info("[IMP:10][vps_readiness][resolve] NODE_HOST_MAP not set — cannot resolve node to SSH host")
            return None, "NODE_HOST_MAP not set — cannot resolve node to SSH host", _REMEDIATION_NO_MAP
        try:
            # json.loads → Any; dict-граница NODE_HOST_MAP (W11)
            node_host_map = cast(dict[str, str], json.loads(raw_map))
        except json.JSONDecodeError as exc:
            logger.info("[IMP:10][vps_readiness][resolve] NODE_HOST_MAP is not valid JSON: %s", exc)
            return None, f"NODE_HOST_MAP is not valid JSON: {exc}", "Fix NODE_HOST_MAP env var to be valid JSON"
    if not isinstance(node_host_map, dict):
        logger.info("[IMP:10][vps_readiness][resolve] NODE_HOST_MAP is not a JSON object")
        return None, "NODE_HOST_MAP is not a JSON object", "Fix NODE_HOST_MAP to be a JSON object mapping node→host"

    host = node_host_map.get(node_name)
    if not host:
        keys = ", ".join(sorted(str(k) for k in node_host_map)) or "empty"
        logger.info("[IMP:10][vps_readiness][resolve] Node '%s' not found in NODE_HOST_MAP", node_name)
        return (
            None,
            f"Node '{node_name}' not found in NODE_HOST_MAP",
            (f"Check NODE_HOST_MAP for node '{node_name}'. Current keys: {keys}"),
        )

    logger.info("[IMP:9][vps_readiness][resolve] Resolved node '%s' → host %s", node_name, host)
    return host, "", ""


# endregion FUNC__resolve_node_host


# region FUNC__build_ready_diagnostics
## @purpose  Build the "ready" diagnostics dict (JSON-safe via json.dumps in CLI).
## @param node_name  Node name
## @param ssh_host   Resolved SSH host
## @io        ⎋ dict — {"status": "ready", ...}
## @complexity O(1)
def _build_ready_diagnostics(node_name: str, ssh_host: str) -> dict[str, object]:
    """Build ready diagnostics (checks list сохраняется как в bash-версии, включая quick mode)."""
    return {
        "status": "ready",
        "node": node_name,
        "host": ssh_host,
        "checks": ["ssh", "forced-command", "projects", "docker"],
    }


# endregion FUNC__build_ready_diagnostics


# region FUNC__build_json_diagnostics
## @purpose  Build the "not_ready" diagnostics dict with failures array.
##           Fix для бага $first: JSON строится через list[dict] → json.dumps (см. TRAP[BUG]).
## @param node_name  Node name
## @param ssh_host   Resolved SSH host (может быть пустым при Step-0 failure)
## @param failures   list[dict] с ключами {"check": str, "remediation": str}
## @io        ⎋ dict — {"status": "not_ready", ..., "failures": [...]}
## @complexity O(F) где F = число failures
def _build_json_diagnostics(
    node_name: str,
    ssh_host: str,
    failures: list[dict[str, str]],
) -> dict[str, object]:
    """Build not_ready diagnostics via data structures — NO string concatenation (AC6)."""
    return {"status": "not_ready", "node": node_name, "host": ssh_host, "failures": failures}


# endregion FUNC__build_json_diagnostics


# region FUNC_check_vps_ready
## @purpose  Run pre-flight VPS readiness checks (DevPlan 105 §3.5 data flow).
##           Step 0: resolve NODE_HOST_MAP; Steps 1-4: SSH, forced-command ping,
##           /opt/projects/, Docker (skip при quick_mode). Fail-fast через all_ok.
## @param node_name     Node name to check
## @param output_mode   "text" (default) | "json" — влияет только на логи; CLI рендерит вывод
## @param quick_mode    True → skip Docker check (AC4)
## @param ssh_runner    DI: (host, user, cmd, timeout) -> (exit_code, stdout); None → default_ssh_runner()
## @param node_host_map DI: dict node→host; None → os.environ["NODE_HOST_MAP"] (AC8)
## @io        ⎋ (bool, dict): (True, ready-diagnostics) | (False, not_ready-diagnostics)
## @complexity O(1) — 3-4 последовательных SSH-вызова × timeout 30s (fail-fast)
## @invariants
##   - Порядок проверок по вероятности отказа: SSH → forced-command → projects → Docker
##   - Первая же неудача останавливает цепочку (D3)
##   - Каждый failure имеет ровно одну remediation hint (AC7)
def check_vps_ready(
    node_name: str,
    *,
    output_mode: str = "text",
    quick_mode: bool = False,
    ssh_runner: Callable[[str, str, str, int], tuple[int, str]] | None = None,
    node_host_map: dict[str, str] | None = None,
) -> tuple[bool, dict[str, object]]:
    """Run pre-flight VPS readiness checks.

    ▶ resolve host → ○ SSH → ○ ping → ○ projects → ○ docker (quick?) → ⊕ failures → ⎋ (all_ok, diagnostics)

    Returns:
        (True,  {"status": "ready", ...})    — all checks passed
        (False, {"status": "not_ready", ...}) — one or more checks failed
    """
    logger.info(
        "[IMP:7][vps_readiness][check] Starting pre-flight checks node=%s output_mode=%s quick_mode=%s",
        node_name,
        output_mode,
        quick_mode,
    )

    if ssh_runner is None:
        ssh_runner = default_ssh_runner

    # ── Step 0: Resolve NODE_HOST_MAP ────────────────────────────────
    ssh_host, resolve_error, resolve_remediation = _resolve_node_host(node_name, node_host_map)
    if ssh_host is None:
        failures: list[dict[str, str]] = [{"check": resolve_error, "remediation": resolve_remediation}]
        logger.info("[IMP:10][vps_readiness][check] VPS NOT READY — node resolution failed")
        return False, _build_json_diagnostics(node_name, "", failures)

    all_ok = True
    failures = []

    # ── Step 1: SSH accessibility (user=ci-deploy, timeout=30s) ──────
    logger.info(
        "[IMP:8][vps_readiness][check] Check 1/4: SSH connectivity to ci-deploy@%s (timeout=30s via ssh_read)",
        ssh_host,
    )
    ssh_rc, _ssh_out = ssh_runner(ssh_host, SSH_USER, CMD_EXIT, SSH_CONNECT_TIMEOUT)
    if ssh_rc == 0:
        logger.info("[IMP:9][vps_readiness][check] SSH OK: ci-deploy@%s", ssh_host)
    else:
        all_ok = False
        failures.append({"check": f"SSH to ci-deploy@{ssh_host} failed (timeout=30s)", "remediation": _REMEDIATION_SSH})
        logger.info("[IMP:10][vps_readiness][check] SSH FAIL: ci-deploy@%s (rc=%s)", ssh_host, ssh_rc)

    # ── Step 2: Forced-command ping (fail-if-not-ready) ──────────────
    if all_ok:
        logger.info("[IMP:8][vps_readiness][check] Check 2/4: Forced-command ping (core delivered?)")
        ping_rc, ping_out = ssh_runner(ssh_host, SSH_USER, CMD_PING, SSH_CONNECT_TIMEOUT)
        if "pong" in ping_out:
            logger.info("[IMP:9][vps_readiness][check] Forced-command OK: ping responds with pong")
        else:
            all_ok = False
            failures.append({
                "check": "Forced-command 'ping' did not respond with pong (orchestrator_cli dispatch, DevPlan 116 B1)",
                "remediation": _REMEDIATION_PING.replace("<node>", node_name),
            })
            logger.info("[IMP:10][vps_readiness][check] Forced-command FAIL (rc=%s, out=%.80s)", ping_rc, ping_out)

    # ── Step 3: /opt/projects/ exists + writable (fail-if-not-ready) ─
    if all_ok:
        logger.info("[IMP:8][vps_readiness][check] Check 3/4: /opt/projects/ exists and writable")
        _pr_rc, projects_out = ssh_runner(ssh_host, SSH_USER, _PROJECTS_CHECK_CMD, SSH_CONNECT_TIMEOUT)
        if projects_out.strip() == "OK":
            logger.info("[IMP:9][vps_readiness][check] /opt/projects/ OK: exists and writable")
        else:
            all_ok = False
            failures.append({
                "check": "/opt/projects/ missing or not writable by ci-deploy",
                "remediation": _REMEDIATION_PROJECTS.replace("<node>", node_name),
            })
            logger.info("[IMP:10][vps_readiness][check] /opt/projects/ FAIL (out=%.80s)", projects_out)

    # ── Step 4: Docker daemon (skip if quick_mode) ───────────────────
    if all_ok and not quick_mode:
        logger.info("[IMP:8][vps_readiness][check] Check 4/4: Docker daemon responsiveness")
        _dk_rc, docker_out = ssh_runner(ssh_host, SSH_USER, _DOCKER_CHECK_CMD, SSH_CONNECT_TIMEOUT)
        if docker_out.strip() != "FAIL":
            logger.info("[IMP:9][vps_readiness][check] Docker OK: version %s", docker_out.strip())
        else:
            all_ok = False
            failures.append({"check": f"Docker daemon not reachable on {ssh_host}", "remediation": _REMEDIATION_DOCKER})
            logger.info("[IMP:10][vps_readiness][check] Docker FAIL on %s", ssh_host)
    elif all_ok and quick_mode:
        logger.info("[IMP:7][vps_readiness][check] Check 4/4: SKIP (--quick mode — Docker check skipped)")

    # ── Result ───────────────────────────────────────────────────────
    if all_ok:
        logger.info("[IMP:9][vps_readiness][check] ALL CHECKS PASSED — VPS ready for deployment")
        return True, _build_ready_diagnostics(node_name, ssh_host)

    logger.info("[IMP:10][vps_readiness][check] VPS NOT READY — %d check(s) failed", len(failures))
    for failure in failures:
        logger.info("[IMP:10][vps_readiness][check]   FAIL: %s", failure["check"])
        logger.info("[IMP:10][vps_readiness][check]   FIX:  %s", failure["remediation"])
    return False, _build_json_diagnostics(node_name, ssh_host, failures)


# endregion FUNC_check_vps_ready


# region FUNC_main
## @purpose  CLI entry point — печатает JSON/text в stdout, логи в stderr, exit 0/1 (D4).
## @io        stdout: JSON (--json) или текстовый отчёт; exit 0 = ready, 1 = not_ready
## @complexity O(1) — делегирует в check_vps_ready
def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher for VPS readiness pre-flight.

    ▶ argparse → ◇ check_vps_ready → ◇ render (json|text) → ⎋ exit 0|1
    """
    parser = argparse.ArgumentParser(
        description="VPS readiness pre-flight checks (Strangler-Fig migration of core/lib/vps-readiness.sh).",
    )
    parser.add_argument("node", metavar="NODE", help="Node name to check (resolved via NODE_HOST_MAP env)")
    parser.add_argument(
        "--json",
        dest="output_mode",
        action="store_const",
        const="json",
        default="text",
        help="Print JSON diagnostics to stdout (CI parsing)",
    )
    parser.add_argument("--quick", action="store_true", default=False, help="Skip Docker daemon check")
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    logger.info(
        "[IMP:7][vps_readiness][main] CLI: node=%s output_mode=%s quick_mode=%s",
        args.node,
        args.output_mode,
        args.quick,
    )

    all_ok, result = check_vps_ready(
        args.node,
        output_mode=args.output_mode,
        quick_mode=args.quick,
    )

    if args.output_mode == "json":
        print(json.dumps(result, ensure_ascii=False))
    elif all_ok:
        print(f"VPS {args.node} READY — all pre-flight checks passed")
    else:
        print(f"VPS {args.node} NOT READY")
        # result["failures"] — list[dict[str, str]] из _build_json_diagnostics (W11)
        for failure in cast(list[dict[str, str]], result.get("failures", [])):
            print(f"  ✗ {failure['check']}")
            print(f"    FIX: {failure['remediation']}")

    logger.info("[IMP:9][vps_readiness][main] DONE: all_ok=%s (exit=%d)", all_ok, 0 if all_ok else 1)
    return 0 if all_ok else 1


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        # getattr(logging, str) → Any (typeshed); уровень — int (logging.INFO) (W11)
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
