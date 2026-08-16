#!/usr/bin/env python3
# GREP_SUMMARY: security-posture run-all-checks aggregate exit-code render-report main CLI --json --apply-sshd root-check ufw S3 parse_ufw_status
# STRUCTURE: ▶ main (argparse --node/--json/--apply-sshd) → ◇ root-check (facts.is_root) → ◇ --apply-sshd? → apply_sshd_dropin → exit 0|1 ┤
#            ○ run_all_checks (S1-S9: apt/sshd/docker/perms/deploy-channel + check_ufw) → ○ aggregate_exit_code (FAIL→2 WARN→1) → ○ render_report|json → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Оркестрация security-постуры ноды (DevPlan 134 L2): run_all_checks (прогон S1-S9),
##           агрегация exit-кода, рендер отчёта (text/json) и CLI entrypoint (включая opt-in
##           apply-режим --apply-sshd для bootstrap φ1). Доменные проверки живут в модулях
##           пакета; здесь — композиция + сетевой периметр S3 (ufw). Извлечено из монолита
##           security_posture.py (план 170 W6-D1).
## @scope    Вызывается: make check-security NODE=<name> (remote через SSH-канал converge),
##           локально на ноде (rc=2 fallback), lifecycle φ1 (--apply-sshd), напрямую main()/run_all_checks
##           (DI-тесты). Импортирует доменные check-модули пакета + firewall (S3) + shared/env_facts.
## @invariants
##   - Exit: 0 = healthy, 1 = warnings (S2 pending security-апдейты — норма между daily-кронами),
##     2 = errors (любой FAIL: конфиг сломан, периметр открыт, канал деплоя повреждён)
##   - Root-check fail-fast: euid != 0 → exit 2 (sshd -T / --apply-sshd требуют root) — без половины отчёта
##   - --json: {"node", "exit_code", "checks": [{id, status, message}]} — фундамент L5-мониторинга
##   - По умолчанию НЕ мутирует систему (read-only диагностика); --apply-sshd — единственная
##     мутация (идемпотентная, content-match no-op, reload только при изменении) — exit 0|1
##   - S3 (check_ufw): ufw активен + baseline 22/80/443 ALLOW + 5432 DENY + нет 2375/2376;
##     parse_ufw_status — из firewall.py (канон, 0 дублирования); identity re-export
##     (security_posture.parse_ufw_status is firewall.parse_ufw_status — контракт теста)
##   - run_all_checks — ПОРЯДОК S1..S9 фиксирован (контракт теста test_main_json_structure: ids)
## @rationale Оркестрация отделена от доменных проверок (AI-First, принцип 8): run.py —
##            единственное место композиции S1-S9 + CLI; домены живут в своих модулях
##            (apt/sshd/docker/fs/deploy-channel) с независимыми SoT. S3 размещён здесь
##            (единственный «не-доменный» сетевой чек в рамках структуры W6-D1).
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S3 + run_all_checks +
##            aggregate + render + main, 1:1 тела)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import pwd  # W11-G3: тип struct_passwd для DI-аннотации getpwuid

from core.internal.bootstrap.firewall import (
    BASELINE_PORTS,
    DENY_PORT,
    FORBIDDEN_PORTS,
    parse_ufw_status,
)
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT

from ._shared import STATUS_FAIL, STATUS_PASS, STATUS_WARN, CheckResult
from ._shared import probe as _probe
from .apt_security import check_pending_security_updates, check_unattended_upgrades
from .deploy_channel_posture import check_forced_command
from .docker_posture import check_docker, check_image_freshness, check_listening_ports
from .fs_perms import check_file_perms
from .sshd_policy import apply_sshd_dropin, check_sshd

logger = logging.getLogger(__name__)


# region FUNC_check_ufw
## @purpose  S3: ufw активен + baseline 22/80/443 ALLOW + 5432 DENY + нет 2375/2376 (Docker API).
##           Переиспользует parse_ufw_status из firewall.py (канон, 0 дублирования).
##           Размещён в run.py: единственный «не-доменный» сетевой чек структуры W6-D1
##           (ufw-политика живёт в firewall.py, который декомпозирует агент D2 — не трогаем).
## @io       ⇥ probe: Callable | None (lazy default _probe) → ⎋ CheckResult
## @complexity O(1) — один subprocess + parse
# 🧐 TRAP[DECISION] · 2026-08-15 · — · S3 (check_ufw) размещён в run.py (оркестрация) · Rejected: отдельный
# · модуль security/firewall_posture.py (чистая доменная граница) · Reason: структура W6-D1 не выделяет
# · домена под ufw (firewall.py декомпозирует агент D2 — пересечение недопустимо); S3 — единственный
# · non-domain чек, run.py — естественный агрегатор · Rev: при появлении >1 ufw/firewall-чека в пакете
# · → вынести в security/firewall_posture.py
def check_ufw(*, probe: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> CheckResult:
    """S3: firewall posture — active, baseline ports, 5432 deny, no Docker API ports."""
    probe = probe or _probe
    result = probe(["ufw", "status", "verbose"], timeout=CONVERGE_DOCKER_TIMEOUT)
    if result.returncode != 0:
        return CheckResult("S3", STATUS_FAIL, f"ufw status failed (rc={result.returncode})")
    active, ports = parse_ufw_status(str(getattr(result, "stdout", "")))
    if not active:
        return CheckResult("S3", STATUS_FAIL, "ufw NOT active (deny-all incoming disabled)")
    problems: list[str] = [f"{port}/tcp not ALLOW" for port in BASELINE_PORTS if ports.get(port) != "ALLOW"]
    if ports.get(DENY_PORT) != "DENY":
        problems.append(f"{DENY_PORT}/tcp not DENY")
    problems.extend(f"Docker API port {port} OPEN" for port in FORBIDDEN_PORTS if port in ports)
    if problems:
        return CheckResult("S3", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S3] UFW active with expected rules")
    return CheckResult("S3", STATUS_PASS, "ufw active; 22/80/443 ALLOW, 5432 DENY, no Docker API ports")


# endregion FUNC_check_ufw


# region FUNC_run_all_checks
## @purpose  Прогон всех 9 проверок (S1-S9).
## @io       ⇥ probe: Callable | None, paths: Mapping | None, ops: object | None,
##              getpwuid: Callable | None, path_exists: Callable | None
##              (все — lazy defaults, E3 DI; None = реальные каналы) → ⎋ list[CheckResult]
## @complexity O(9) — константное число проверок
def run_all_checks(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
    ops: ModuleType | None = None,  # DI: lazy default docker_ops (модуль) — E3 DevPlan 160
    getpwuid: Callable[[int], pwd.struct_passwd] | None = None,  # DI: pwd.getpwuid → struct_passwd (T10.3)
    path_exists: Callable[[str], bool] | None = None,
) -> list[CheckResult]:
    """Run all S1-S9 posture checks."""
    return [
        check_unattended_upgrades(probe=probe, paths=paths),
        check_pending_security_updates(probe=probe),
        check_ufw(probe=probe),
        check_sshd(probe=probe),
        check_docker(probe=probe, paths=paths),
        check_file_perms(probe=probe, paths=paths, path_exists=path_exists),
        check_forced_command(probe=probe, paths=paths, getpwuid=getpwuid),
        check_image_freshness(ops=ops),
        check_listening_ports(probe=probe),
    ]


# endregion FUNC_run_all_checks


# region FUNC_aggregate_exit_code
## @purpose  Агрегация: любой FAIL → 2, иначе любой WARN → 1, иначе 0.
## @io       ⇥ results: list[CheckResult] → ⎋ int (0|1|2)
## @complexity O(n) — один проход
def aggregate_exit_code(results: list[CheckResult]) -> int:
    """Aggregate check statuses → exit code (0 healthy, 1 warnings, 2 errors)."""
    if any(r.status == STATUS_FAIL for r in results):
        return 2
    if any(r.status == STATUS_WARN for r in results):
        return 1
    return 0


# endregion FUNC_aggregate_exit_code


# region FUNC_render_report
## @purpose  Рендер отчёта: текст (stdout, LDD-совместимый) или JSON (--json, для L5-мониторинга).
## @io       ⇥ node: str, results: list[CheckResult] → ⎋ str — готовый отчёт
## @complexity O(n)
def render_report(node: str, results: list[CheckResult]) -> str:
    """Render human-readable or JSON report."""
    lines = [f"Security posture report — node={node}", "=" * 60]
    lines += [f"[{r.status}] {r.check_id}: {r.message}" for r in results]
    lines += ["=" * 60, f"Exit: {aggregate_exit_code(results)} (0=healthy 1=warnings 2=errors)"]
    return "\n".join(lines)


# endregion FUNC_render_report


# region TYPEDEF_ApplyKwargs
class _ApplyKwargs(TypedDict, total=False):
    """DI-kwargs для apply_sshd_dropin (W11-G3: замена dict[str, Any])."""

    probe_fn: Callable[..., subprocess.CompletedProcess[str]]
    hardening_dropin: str | None
    superseded_dropin: str | None


# endregion TYPEDEF_ApplyKwargs


# region FUNC_main
## @purpose  CLI: security_posture.py [--node NAME] [--json] | [--apply-sshd]. Root fail-fast.
##           Check-режим: exit 0|1|2. Apply-режим (--apply-sshd, DevPlan 136 W3 + 162 W2-1): exit 0|1.
## @io       ⇥ argv → ⎋ int (exit code); facts: EnvironmentFacts | None (W4b DI root-guard);
##              probe/paths/ops/getpwuid/path_exists: lazy-default DI (E3, DevPlan 160) —
##              threading в run_all_checks (тесты main без monkeypatch модульных каналов)
## @complexity O(9) — прогон всех проверок; apply — O(1) + reload
def main(
    argv: list[str] | None = None,
    *,
    facts: EnvironmentFacts | None = None,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
    ops: ModuleType | None = None,  # DI: lazy default docker_ops (модуль) — E3 DevPlan 160
    getpwuid: Callable[[int], pwd.struct_passwd] | None = None,  # DI: pwd.getpwuid → struct_passwd (T10.3)
    path_exists: Callable[[str], bool] | None = None,
) -> int:
    """CLI entrypoint. Returns exit code 0/1/2 — sys.exit handled by __main__."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Security posture check (DevPlan 134 L2)")
    parser.add_argument("--node", default="", help="Node name (informational, for report)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report (L5 monitoring)")
    parser.add_argument(
        "--apply-sshd",
        action="store_true",
        help="Apply sshd hardening drop-in (DevPlan 136 W3 MaxStartups + 162 W2-1) — bootstrap φ1; exit 0 ok / 1 error",
    )

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.node: str
            self.json: bool
            self.apply_sshd: bool

    args = parser.parse_args(argv, namespace=_CliArgs())

    facts = facts or default_env_facts()
    if not facts.is_root():
        print(
            "[FAIL] security_posture must run as root (sshd -T / --apply-sshd require root) — exit 2", file=sys.stderr
        )
        return 2

    if args.apply_sshd:
        # Apply-режим (DevPlan 136 W3 + 162 W2-1): идемпотентный hardening drop-in + reload
        # при изменении. НЕ check-режим — exit 0 = применено/no-op, 1 = ошибка (канон security_updates.py).
        # W-H (DevPlan 163): probe/paths-каналы DI пробрасываются в apply (0 патчей модуля)
        apply_kwargs: _ApplyKwargs = {}  # runtime-диспетчер kwargs (probe_fn/hardening_dropin/superseded_dropin)
        if probe is not None:
            apply_kwargs["probe_fn"] = probe
        if paths is not None:
            apply_kwargs["hardening_dropin"] = paths.get("sshd_hardening_dropin")
            apply_kwargs["superseded_dropin"] = paths.get("sshd_maxstartups_dropin")
        if apply_sshd_dropin(**apply_kwargs):
            return 0
        print("[FAIL] sshd hardening drop-in apply failed (see logs)", file=sys.stderr)
        return 1

    results = run_all_checks(probe=probe, paths=paths, ops=ops, getpwuid=getpwuid, path_exists=path_exists)
    exit_code = aggregate_exit_code(results)
    if args.json:
        payload = {
            "node": args.node,
            "exit_code": exit_code,
            "checks": [{"id": r.check_id, "status": r.status, "message": r.message} for r in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(args.node, results))
    return exit_code


# endregion FUNC_main
