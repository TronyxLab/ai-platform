#!/usr/bin/env python3
# GREP_SUMMARY: firewall ufw declarative idempotent 22 80 443 5432-deny extra_ports deny-incoming allow-outgoing port-validation
# STRUCTURE: ▶ parse extra_ports args → ○ validate_ports (1-65535, forbid 2375/2376) → ○ apply_rules (ufw reset→defaults→baseline→extra→deny 5432→enable) → ○ verify (ufw status) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Declarative ufw baseline firewall: deny all incoming, allow outgoing, open exactly
##           22/80/443 + extra_ports, explicitly deny 5432. Python-порт firewall.sh (DevPlan 118 E3).
## @scope    Called during bootstrap phase φ1 (phases.py) via thin facade core/internal/bootstrap/firewall.sh.
## @invariants
##   - Full declarative reset on each run (not additive) ensures deterministic state
##   - Ports 2375/2376 (Docker API) are NEVER added regardless of extra_ports (validation rejects)
##   - Port 5432 (PostgreSQL) is explicitly DENIED regardless of extra_ports
##   - extra_ports validated as integers 1-65535; non-numeric/out-of-range → fail-fast exit 1
##   - exit 0 only if ufw status shows expected ports (baseline ALLOW, 5432 DENY, no 2375/2376)
##   - subprocess ufw — тестируемость: validate_ports/build_rules/parse_ufw_status pure functions
## @rationale Additive ufw rules accumulate over re-runs; declarative replace guarantees idempotency.
##            Strangler E3: ufw-оркестрация в Python (порты из node.yaml firewall-поддомен — TODO E3).
## @changes  2026-08-02 | DevPlan 118 E3 — Created (Python-порт firewall.sh, 167 LOC)
## @see      core/internal/bootstrap/firewall.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

# Baseline ports always open
BASELINE_PORTS: tuple[int, ...] = (22, 80, 443)
# Forbidden ports — Docker API must never be exposed
FORBIDDEN_PORTS: tuple[int, ...] = (2375, 2376)
# Explicit deny — managed PostgreSQL provider may host-forward
DENY_PORT = 5432
_PORT_RE = re.compile(r"^[0-9]+$")


# region FUNC_validate_ports
## @purpose  Валидация extra_ports: integer 1-65535, запрет 2375/2376 (fail-fast).
## @io       ⇥ ports: list[str] → ⎋ list[int] — валидные порты
## @complexity O(P) — P = число портов
## @raises   ValueError на невалидный порт / Docker API port (контракт: exit 1 через main)
def validate_ports(ports: list[str]) -> list[int]:
    """Validate extra_ports (1-65535, no 2375/2376). Raises ConfigValidationError on violation."""
    result: list[int] = []
    for port in ports:
        if not _PORT_RE.match(port) or not (1 <= int(port) <= 65535):
            raise ConfigValidationError(f"Invalid port '{port}' — must be integer 1-65535")
        if int(port) in FORBIDDEN_PORTS:
            raise ConfigValidationError(f"SECURITY: Port {port} is a Docker API port — forbidden in extra_ports")
        result.append(int(port))
    logger.info("[IMP:8][firewall][validate] extra_ports validated: %s", ports or "none")
    return result


# endregion FUNC_validate_ports


# region FUNC_build_rules
## @purpose  Построить упорядоченный список ufw-команд декларативной политики (reset→defaults→baseline→extra→deny→enable).
## @io       ⇥ extra_ports: list[int] → ⎋ list[list[str]] — команды для subprocess
## @complexity O(B + P) — B = baseline, P = extra
def build_rules(extra_ports: list[int]) -> list[list[str]]:
    """Build the ordered ufw command list (declarative full-set replacement)."""
    rules: list[list[str]] = [
        ["ufw", "--force", "disable"],
        ["ufw", "--force", "reset"],
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
    ]
    rules.extend(["ufw", "allow", f"{port}/tcp", "comment", "platform-baseline"] for port in BASELINE_PORTS)
    rules.extend(["ufw", "allow", f"{port}/tcp", "comment", "platform-extra"] for port in extra_ports)
    rules.append(["ufw", "deny", f"{DENY_PORT}/tcp", "comment", "explicit-deny-postgresql"])
    rules.append(["ufw", "--force", "enable"])
    return rules


# endregion FUNC_build_rules


# region FUNC_parse_ufw_status
## @purpose  Разобрать `ufw status verbose` на статус-активность + allow/deny-порты (verify-критерий).
## @io       ⇥ status_text: str → ⎋ tuple[bool, dict[int, str]] — (active, port→action map)
## @complexity O(L) — L = строк статуса
def parse_ufw_status(status_text: str) -> tuple[bool, dict[int, str]]:
    """Parse ufw status verbose → (active, {port: ALLOW|DENY})."""
    active = "Status: active" in status_text
    port_actions: dict[int, str] = {}
    for line in status_text.splitlines():
        m = re.match(r"^(\d+)/tcp\s+(\S+)", line.strip())
        if m:
            port_actions[int(m.group(1))] = m.group(2)
    return active, port_actions


# endregion FUNC_parse_ufw_status


# region FUNC_verify_firewall
## @purpose  Verify ufw status: active, baseline ALLOW, forbidden NOT ALLOW, 5432 DENY.
## @io       ⇥ status_text: str → ⎋ bool
## @complexity O(1) — parse + 4 проверки
def verify_firewall(status_text: str) -> bool:
    """Verify ufw status against the declarative policy. True = compliant."""
    active, port_actions = parse_ufw_status(status_text)
    if not active:
        logger.error("[IMP:10][firewall][verify] ufw is NOT active after apply")
        return False
    for port in BASELINE_PORTS:
        if port_actions.get(port) != "ALLOW":
            logger.error("[IMP:10][firewall][verify] Expected port %d/tcp ALLOW not found", port)
            return False
    for port in FORBIDDEN_PORTS:
        if port_actions.get(port) == "ALLOW":
            logger.error("[IMP:10][firewall][verify] SECURITY: Docker API port %d is open in ufw", port)
            return False
    if port_actions.get(DENY_PORT) != "DENY":
        logger.error("[IMP:10][firewall][verify] SECURITY: Port %d is not DENIED in ufw", DENY_PORT)
        return False
    logger.info("[IMP:9][firewall][verify] Firewall verified: active, 22/80/443 open, Docker ports closed, 5432 denied")
    return True


# endregion FUNC_verify_firewall


# region FUNC_apply_rules_subprocess
## @purpose  Применить ufw-команды через subprocess (best-effort disable, fail-fast остальные).
## @io       ⇥ rules: list[list[str]] → ⎋ bool
## @complexity O(R) — R = команд
def _apply_rules_subprocess(rules: list[list[str]]) -> bool:
    """Run ufw commands via subprocess. disable best-effort; reset/allow/deny/enable fail-fast."""
    for idx, cmd in enumerate(rules):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:
            logger.error("[IMP:10][firewall][apply] ufw not available: %s", exc)
            return False
        if result.returncode != 0 and idx == 0:
            logger.warning(
                "[IMP:7][firewall][apply] ufw disable failed (non-fatal, reset re-establishes state): %s",
                result.stderr.strip(),
            )
            continue
        if result.returncode != 0:
            logger.error("[IMP:10][firewall][apply] ufw command failed: %s %s", " ".join(cmd), result.stderr.strip())
            return False
    logger.info("[IMP:9][firewall][apply] Declarative ufw policy applied")
    return True


# endregion FUNC_apply_rules_subprocess


# region FUNC_run
## @purpose  Полный прогон: validate → build → apply → verify.
## @io       ⇥ extra_ports: list[str] → ⎋ bool
## @complexity O(R + L)
def run(extra_ports: list[str]) -> bool:
    """Full firewall pipeline: validate ports, apply rules, verify status."""
    try:
        ports = validate_ports(extra_ports)
    except ConfigValidationError as exc:
        logger.error("[IMP:10][firewall][run] %s", exc)
        return False
    rules = build_rules(ports)
    if not _apply_rules_subprocess(rules):
        return False
    try:
        status = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True)
        status_text = status.stdout if status.returncode == 0 else ""
    except OSError:
        status_text = ""
    return verify_firewall(status_text)


# endregion FUNC_run


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.bootstrap.firewall [extra_ports...]`.

    ▶ ┌argv extra_ports (space-separated)┐ → ○ run() → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Declarative ufw firewall (DevPlan 118 E3)")
    parser.add_argument("extra_ports", nargs="*", help="Extra ports to allow (space-separated)")
    args = parser.parse_args()
    return 0 if run(args.extra_ports) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
