#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S1-S7 unattended-upgrades pending-security ufw sshd docker live-restore world-writable forced-command exit-0-1-2 json DevPlan-134
# STRUCTURE: ▶ root-check → ○ 7 checks (S1-S7, каждая pure+subprocess probe) → ○ aggregate (FAIL→2, WARN→1) → ○ text|json report → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Security posture check ноды (DevPlan 134 L2) — 7 проверок S1-S7, закрывающих главные
##           векторы автоматизированных ИИ-атак: автопатчинг (S1/S2), сетевой периметр (S3),
##           SSH-поверхность (S4), docker-демон (S5), локальные привилегии (S6), целостность
##           forced-command канала деплоя (S7). Выполняется НА ноде как root (sshd -T).
## @scope    Вызывается: make check-security NODE=<name> (remote через SSH-канал converge),
##           локально на ноде (rc=2 fallback). Импортирует firewall.parse_ufw_status (0 дублирования),
##           shared/subprocess_io (канон B4/C10) и shared/timeouts (гейт U-11).
## @invariants
##   - Exit: 0 = healthy, 1 = warnings (S2 pending security-апдейты — норма между daily-кронами),
##     2 = errors (любой FAIL: конфиг сломан, периметр открыт, канал деплоя повреждён)
##   - Каждая проверка — pure-функция check_*(ctx) -> CheckResult(status, message); subprocess
##     через run_subprocess (check=False, graceful); таймауты из shared/timeouts
##   - Root-check fail-fast: euid != 0 → exit 2 (sshd -T требует root) — без половины отчёта
##   - --json: {"node", "exit_code", "checks": [{id, status, message}]} — фундамент L5-мониторинга
##   - Не мутирует систему (read-only диагностика) — безопасен для прямого запуска на ноде
## @rationale L2 security-гэпа (DevPlan 134): check-suite.yaml — только code-quality чеки;
##            security-постур ноды не проверялся ничем. Набор S1-S7 — минимально достаточный
##            (DevPlan D4), без мониторинг-тяжести (fail2ban/auditd — L5 follow-up).
## @changes 2026-08-04 | DevPlan 134 W2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from core.internal.bootstrap.firewall import (
    BASELINE_PORTS,
    DENY_PORT,
    FORBIDDEN_PORTS,
    parse_ufw_status,
)
from core.internal.shared import subprocess_io as io

# DevPlan 119 B2/B3 канон: /opt/platform литерал запрещён (гейт timeout_literals) —
# platform_remote_base() (PLATFORM_REMOTE_BASE → /opt/platform, PLATFORM_ROOT исключён, RC 121)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.timeouts import APT_TIMEOUT, DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

# Пути — модульные константы (тесты переопределяют через monkeypatch)
AUTO_UPDATES_FILE = "/etc/apt/apt.conf.d/20auto-upgrades"
UNATTENDED_FILE = "/etc/apt/apt.conf.d/50unattended-upgrades"
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"
PLATFORM_BASE = str(platform_remote_base())
# Канон cli.py:261 — expanduser("~ci-deploy") вместо хардкода /home/ci-deploy (гейт no_hardcoded_local_paths)
CI_DEPLOY_AUTHORIZED_KEYS = os.path.expanduser("~ci-deploy/.ssh/authorized_keys")
APT_CHECK_BIN = "/usr/lib/update-notifier/apt-check"

_APT_CHECK_RE = re.compile(r"(\d+)\s+updates can be applied immediately")
_APT_CHECK_SEC_RE = re.compile(r"(\d+)\s+of these updates are security updates")


# region DATACLS_CheckResult
@dataclass(frozen=True)
class CheckResult:
    """Результат одной проверки S1-S7."""

    check_id: str
    status: str  # PASS | WARN | FAIL
    message: str


# endregion DATACLS_CheckResult


# region FUNC__probe
## @purpose  Graceful subprocess probe: run_subprocess (check=False, канон B4/C10).
## @io       ⇥ cmd: list[str], timeout: int → ⎋ CompletedProcess (rc 0/124/127/иное, никогда не raise)
## @complexity O(1) — делегирование
def _probe(cmd: list[str], timeout: int) -> object:
    """Run a subprocess gracefully (never raises)."""
    return io.run_subprocess(cmd, timeout=timeout, check=False)


# endregion FUNC__probe


# region FUNC_check_unattended_upgrades
## @purpose  S1: unattended-upgrades активен — пакет + 20auto-upgrades (Unattended-Upgrade "1")
##           + 50unattended-upgrades (security-only origins). FAIL = автопатчинг сломан/отсутствует.
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — dpkg probe + 2 file reads
## @invariants  Директивы сверяются с каноном security_updates.py (DevPlan 134 W1) — дрейф = FAIL
def check_unattended_upgrades() -> CheckResult:
    """S1: unattended-upgrades policy active (package + both config files)."""
    pkg = _probe(["dpkg", "-s", "unattended-upgrades"], timeout=30)
    if pkg.returncode != 0:
        return CheckResult("S1", STATUS_FAIL, "unattended-upgrades package NOT installed")
    auto = Path(AUTO_UPDATES_FILE)
    unattended = Path(UNATTENDED_FILE)
    problems: list[str] = []
    if not auto.is_file() or 'APT::Periodic::Unattended-Upgrade "1"' not in auto.read_text():
        problems.append(f"{AUTO_UPDATES_FILE} missing or Unattended-Upgrade disabled")
    if not unattended.is_file() or "-security" not in unattended.read_text():
        problems.append(f"{UNATTENDED_FILE} missing or no security origins")
    if problems:
        return CheckResult("S1", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S1] Unattended-upgrades active")
    return CheckResult("S1", STATUS_PASS, "unattended-upgrades active (security-only origins)")


# endregion FUNC_check_unattended_upgrades


# region FUNC_check_pending_security_updates
## @purpose  S2: pending security-апдейты через update-notifier apt-check. >0 → WARN (норма между
##           daily-кронами, unattended-upgrades применит; алерт оператору). Недоступность apt-check → WARN.
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — один subprocess
def check_pending_security_updates() -> CheckResult:
    """S2: pending security updates (update-notifier apt-check --human-readable)."""
    result = _probe([APT_CHECK_BIN, "--human-readable"], timeout=APT_TIMEOUT)
    if result.returncode != 0:
        return CheckResult("S2", STATUS_WARN, f"apt-check unavailable (rc={result.returncode}) — cannot assess")
    output = str(getattr(result, "stdout", ""))
    total_m = _APT_CHECK_RE.search(output)
    sec_m = _APT_CHECK_SEC_RE.search(output)
    total = int(total_m.group(1)) if total_m else 0
    security = int(sec_m.group(1)) if sec_m else 0
    if security > 0:
        logger.info("[IMP:8][posture][S2] %d security updates pending of %d total", security, total)
        return CheckResult("S2", STATUS_WARN, f"{security} security updates pending (of {total} total)")
    logger.info("[IMP:9][posture][S2] No pending security updates")
    return CheckResult("S2", STATUS_PASS, f"no pending security updates (total pending: {total})")


# endregion FUNC_check_pending_security_updates


# region FUNC_check_ufw
## @purpose  S3: ufw активен + baseline 22/80/443 ALLOW + 5432 DENY + нет 2375/2376 (Docker API).
##           Переиспользует parse_ufw_status из firewall.py (канон, 0 дублирования).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — один subprocess + parse
def check_ufw() -> CheckResult:
    """S3: firewall posture — active, baseline ports, 5432 deny, no Docker API ports."""
    result = _probe(["ufw", "status", "verbose"], timeout=30)
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


# region FUNC_check_sshd
## @purpose  S4: SSH-поверхность через sshd -T (эффективный конфиг): PermitRootLogin
##           prohibit-password|no, PasswordAuthentication no, PubkeyAuthentication yes.
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — один subprocess + regex
## @invariants  Требует root (sshd -T) — гарантируется root-check в main
def check_sshd() -> CheckResult:
    """S4: sshd effective config — no root password login, no password auth, pubkey only."""
    result = _probe(["sshd", "-T"], timeout=30)
    if result.returncode != 0:
        return CheckResult("S4", STATUS_FAIL, f"sshd -T failed (rc={result.returncode})")
    text = str(getattr(result, "stdout", ""))
    settings: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            settings[parts[0].lower()] = parts[1].lower()
    problems: list[str] = []
    root_login = settings.get("permitrootlogin", "")
    if root_login not in ("no", "prohibit-password"):
        problems.append(f"PermitRootLogin={root_login or 'unset'} (expected no|prohibit-password)")
    if settings.get("passwordauthentication", "") == "yes":
        problems.append("PasswordAuthentication=yes (password auth enabled)")
    if settings.get("pubkeyauthentication", "") != "yes":
        problems.append("PubkeyAuthentication != yes")
    if problems:
        return CheckResult("S4", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S4] SSH surface hardened")
    return CheckResult("S4", STATUS_PASS, "sshd: root login restricted, password auth off, pubkey on")


# endregion FUNC_check_sshd


# region FUNC_check_docker
## @purpose  S5: docker-демон — live-restore=true (переживает рестарты/ребуты), iptables НЕ отключён
##           (daemon.json "iptables": false = FAIL), Docker API порты 2375/2376 не слушают.
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(2) — daemon.json read + ss probe
def check_docker() -> CheckResult:
    """S5: docker daemon hardening — live-restore, iptables enabled, no exposed API."""
    daemon = Path(DOCKER_DAEMON_JSON)
    problems: list[str] = []
    if not daemon.is_file():
        problems.append(f"{DOCKER_DAEMON_JSON} missing — live-restore unconfirmed")
    else:
        try:
            config = json.loads(daemon.read_text())
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"daemon.json unparseable: {e}")
            config = {}
        if config.get("live-restore") is not True:
            problems.append("live-restore != true (containers die on daemon restart)")
        if config.get("iptables") is False:
            problems.append("iptables disabled in daemon.json")
    ss = _probe(["ss", "-tlnp"], timeout=DOCKER_CMD_TIMEOUT)
    if ss.returncode == 0:
        ss_out = str(getattr(ss, "stdout", ""))
        problems.extend(
            f"Docker API port {port} LISTENING" for port in FORBIDDEN_PORTS if re.search(rf":{port}\s", ss_out)
        )
    if problems:
        return CheckResult("S5", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S5] Docker daemon hardened")
    return CheckResult("S5", STATUS_PASS, "live-restore on, iptables on, no Docker API exposed")


# endregion FUNC_check_docker


# region FUNC_check_file_perms
## @purpose  S6: локальные привилегии — world-writable файлы в /opt/platform (вектор локального
##           повышения привилегий) + world-readable файлы в /opt/platform/secrets (age-ключи/пароли).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(n) — два find по дереву платформы
## @invariants  Отсутствие /opt/platform → WARN (нода не развёрнута — не security-ошибка)
def check_file_perms() -> CheckResult:
    """S6: file permissions — no world-writable files, secrets not world-readable."""
    base = Path(PLATFORM_BASE)
    if not base.is_dir():
        return CheckResult("S6", STATUS_WARN, f"{PLATFORM_BASE} missing — platform not deployed")
    problems: list[str] = []
    ww = _probe(["find", PLATFORM_BASE, "-type", "f", "-perm", "-0002"], timeout=30)
    if ww.returncode == 0:
        writable = [ln for ln in str(getattr(ww, "stdout", "")).splitlines() if ln.strip()]
        if writable:
            problems.append(f"world-writable files: {', '.join(writable[:5])}" + ("..." if len(writable) > 5 else ""))
    secrets_dir = base / "secrets"
    if secrets_dir.is_dir():
        wr = _probe(["find", str(secrets_dir), "-type", "f", "-perm", "/004"], timeout=30)
        if wr.returncode == 0:
            readable = [ln for ln in str(getattr(wr, "stdout", "")).splitlines() if ln.strip()]
            if readable:
                problems.append(
                    f"world-readable secrets: {', '.join(readable[:5])}" + ("..." if len(readable) > 5 else "")
                )
    if problems:
        return CheckResult("S6", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S6] File permissions clean")
    return CheckResult("S6", STATUS_PASS, "no world-writable files, secrets not world-readable")


# endregion FUNC_check_file_perms


# region FUNC_check_forced_command
## @purpose  S7: целостность forced-command канала деплоя — ci-deploy authorized_keys содержит
##           строку с command="...orchestrator_cli dispatch",restrict (единственный писатель ключа —
##           lifecycle φ2; потеря command= = открытый SSH-канал без ограничений).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — одно чтение файла
## @invariants  Сверка с каноном phases/system.py φ2 (DevPlan 116 B1 + волна 117 D1)
def check_forced_command() -> CheckResult:
    """S7: ci-deploy forced-command dispatch intact (orchestrator_cli, restrict)."""
    keys_file = Path(CI_DEPLOY_AUTHORIZED_KEYS)
    if not keys_file.is_file():
        return CheckResult("S7", STATUS_FAIL, f"{CI_DEPLOY_AUTHORIZED_KEYS} missing")
    try:
        lines = keys_file.read_text().splitlines()
    except OSError as e:
        return CheckResult("S7", STATUS_FAIL, f"cannot read authorized_keys: {e}")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('command="') and "orchestrator_cli dispatch" in stripped and "restrict" in stripped:
            logger.info("[IMP:9][posture][S7] Forced-command dispatch intact")
            return CheckResult("S7", STATUS_PASS, "ci-deploy key restricted to orchestrator_cli dispatch")
    return CheckResult(
        "S7", STATUS_FAIL, "no forced-command (command=...orchestrator_cli dispatch,restrict) in authorized_keys"
    )


# endregion FUNC_check_forced_command


# region FUNC_run_all_checks
## @purpose  Прогон всех 7 проверок (S1-S7).
## @io       ⇥ — → ⎋ list[CheckResult]
## @complexity O(7) — константное число проверок
def run_all_checks() -> list[CheckResult]:
    """Run all S1-S7 posture checks."""
    return [
        check_unattended_upgrades(),
        check_pending_security_updates(),
        check_ufw(),
        check_sshd(),
        check_docker(),
        check_file_perms(),
        check_forced_command(),
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


# region FUNC_main
## @purpose  CLI: security_posture.py [--node NAME] [--json]. Root fail-fast, exit 0|1|2.
## @io       ⇥ argv → ⎋ int (exit code)
## @complexity O(7) — прогон всех проверок
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns exit code 0/1/2 — sys.exit handled by __main__."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Security posture check (DevPlan 134 L2)")
    parser.add_argument("--node", default="", help="Node name (informational, for report)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report (L5 monitoring)")
    args = parser.parse_args(argv)

    if os.geteuid() != 0:
        print("[FAIL] security_posture must run as root (sshd -T requires root) — exit 2", file=sys.stderr)
        return 2

    results = run_all_checks()
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


if __name__ == "__main__":
    sys.exit(main())
