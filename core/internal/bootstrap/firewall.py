#!/usr/bin/env python3
# GREP_SUMMARY: firewall ufw declarative idempotent 22 80 443 5432-deny extra_ports deny-incoming allow-outgoing port-validation
# STRUCTURE: ▶ parse extra_ports args → ○ validate_ports (1-65535, forbid 2375/2376) → ○ apply_rules (ufw reset→defaults→baseline→extra→deny 5432→enable) → ○ verify (ufw status) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Declarative ufw baseline firewall: deny all incoming, allow outgoing, open exactly
##           22/80/443 + extra_ports (только с `from <ip>` — НИКОГДА 0.0.0.0/Anywhere), explicitly
##           deny module-internal ports (реестр platform-infra.yaml). Python-порт firewall.sh (DevPlan 118 E3).
##           DevPlan 136 W10: (T10.6/S-8) extra_ports IP-scoped + FORBIDDEN/CHECK по реестру портов модулей;
##           (T10.10/S-14) инкрементальный apply БЕЗ disable+reset — enable+default-deny ПЕРВЫМИ,
##           stale platform-правила удаляются точечно (ufw delete) — окна «firewall выключен» нет.
## @scope    Called during bootstrap phase φ1 (phases.py) via thin facade core/internal/bootstrap/firewall.sh.
## @invariants
##   - НИКОГДА `ufw disable`/`ufw reset` — firewall не отключается (S-14, T10.10): enable+default-deny
##     применяются ПЕРВЫМИ, затем allow-правила (ssh 22 первым — lockout-safe), затем deny модульных
##     портов; stale allow-правила с комментарием platform-* удаляются точечно (идемпотентность без reset)
##   - Порты 2375/2376 (Docker API) НИКОГДА не добавляются (FORBIDDEN — validate rejects)
##   - Модульные внутренние порты (реестр platform-infra.yaml provides/env_defaults: postgres 5432,
##     redis 6379, clickhouse 8123/9000, minio 9000/9001, litellm 4000, langfuse 3001, loki 3100,
##     grafana 3000, prometheus 9090, hermes 9119/8642, nginx-exporter 9113, node-exporter 9100) —
##     DENY на уровне ufw (defense-in-depth) И запрещены в extra_ports (FORBIDDEN) — S-8, T10.6
##   - 8080 НЕ входит в deny-реестр: cadvisor/status-page слушают 127.0.0.1 (loopback = контроль),
##     user-проекты часто публикуют 8080 (тест-проект test-project-web на test-VPS) — не ломать их
##   - extra_ports валидируются как integers 1-65535; non-numeric/out-of-range/forbidden → fail-fast
##   - extra_ports ТРЕБУЮТ `--source-ip <ip>` (allow from <ip>, НЕ Anywhere) — S-8, T10.6;
##     extra_ports без source-ip → ConfigValidationError (fail-fast)
##   - exit 0 только если ufw status показывает ожидаемые порты (baseline ALLOW, модульные DENY,
##     нет 2375/2376, нет module-port ALLOW)
##   - subprocess ufw — тестируемость: validate_ports/build_rules/parse_ufw_status/verify_firewall pure
## @rationale Additive ufw rules accumulate over re-runs; declarative replace guarantees idempotency.
##            disable+reset создавал окно без файрвола (S-14) — инкрементальный apply закрывает его.
##            Strangler E3: ufw-оркестрация в Python (порты из node.yaml firewall-поддомен).
## @changes  2026-08-02 | DevPlan 118 E3 — Created (Python-порт firewall.sh, 167 LOC)
## @changes  2026-08-05 | DevPlan 136 W10 — T10.6/T10.10: --source-ip, MODULE_PORTS_DENY,
##                      инкрементальный apply (без disable/reset), stale-reconcile
## @see      core/internal/bootstrap/firewall.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
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
# Модульные внутренние порты — реестр platform-infra.yaml (provides + env_defaults):
#   postgres 5432 (DENY_PORT), redis 6379, clickhouse 8123/9000, minio 9000/9001, litellm 4000,
#   langfuse 3001, loki 3100, grafana 3000, prometheus 9090, hermes 9119/8642,
#   nginx-exporter 9113, node-exporter 9100.
# S-8/T10.6: DENY на уровне ufw (defense-in-depth поверх 127.0.0.1-bind в compose) И запрещены в
# extra_ports (FORBIDDEN расширен). 8080 НЕ включён: cadvisor/status-page 127.0.0.1-bound,
# user-проекты часто публикуют 8080 (тест-проект на test-VPS) — не блокировать.
MODULE_PORTS_DENY: tuple[int, ...] = (
    6379,
    8123,
    9000,
    9001,
    4000,
    3001,
    3100,
    9090,
    3000,
    9119,
    8642,
    9113,
    9100,
)
# Полный запрет extra_ports: Docker API + модульные порты + явный deny 5432
FORBIDDEN_EXTRA_PORTS: tuple[int, ...] = (*FORBIDDEN_PORTS, DENY_PORT, *MODULE_PORTS_DENY)
_PORT_RE = re.compile(r"^[0-9]+$")

# ⚠️ 142 W6 (A2/A3): Privoxy/Tor доступ с docker-мостов.
# · 2-й цикл 141: после reboot/переустановки privoxy слушал только 127.0.0.1, а ufw-правило
# · `allow 172.16.0.0/12:8118` добавлялось ВРУЧНУЮ (A2/A3). Теперь — декларативный baseline:
# · при TOR_ENABLED=1 открываем 8118 для docker-моста 172.16.0.0/12 (grafana/контейнеры ходят
# · на host.docker.internal:8118 = docker-gateway), verify сверяет правило (W6 Фикс 2).
TOR_PRIVOXY_NET: str = "172.16.0.0/12"
TOR_PRIVOXY_PORT: int = 8118


# region FUNC_validate_ports
## @purpose  Валидация extra_ports: integer 1-65535, запрет FORBIDDEN_EXTRA_PORTS (Docker API +
##           модульные внутренние порты, fail-fast) — S-8/T10.6.
## @io       ⇥ ports: list[str] → ⎋ list[int] — валидные порты
## @complexity O(P) — P = число портов
## @raises   ConfigValidationError на невалидный/запрещённый порт (контракт: exit 1 через main)
def validate_ports(ports: list[str]) -> list[int]:
    """Validate extra_ports (1-65535, no Docker API, no module-internal ports)."""
    result: list[int] = []
    for port in ports:
        if not _PORT_RE.match(port) or not (1 <= int(port) <= 65535):
            raise ConfigValidationError(f"Invalid port '{port}' — must be integer 1-65535")
        if int(port) in FORBIDDEN_PORTS:
            raise ConfigValidationError(f"SECURITY: Port {port} is a Docker API port — forbidden in extra_ports")
        if int(port) in FORBIDDEN_EXTRA_PORTS:
            raise ConfigValidationError(
                f"SECURITY: Port {port} is a module-internal port (platform-infra.yaml registry) — "
                "forbidden in extra_ports (S-8, T10.6)"
            )
        result.append(int(port))
    logger.info("[IMP:8][firewall][validate] extra_ports validated: %s", ports or "none")
    return result


# endregion FUNC_validate_ports


# region FUNC_build_rules
## @purpose  Построить упорядоченный список ufw-команд инкрементальной политики (S-14, T10.10):
##           enable→defaults→ssh-first→baseline→extra(from ip)→tor-privoxy(142 W6)→deny модульных→stale-reconcile.
##           НИКАКОГО disable/reset — firewall активен на всём протяжении (нет окна без файрвола).
## @io       ⇥ extra_ports: list[int], source_ip: str|None, tor_enabled: bool = False →
##              ⎋ list[list[str]] — команды для subprocess
## @complexity O(B + P + M) — B = baseline, P = extra, M = module-deny
## @raises   ConfigValidationError если extra_ports заданы без source_ip (S-8: никогда Anywhere)
def build_rules(extra_ports: list[int], source_ip: str | None = None, tor_enabled: bool = False) -> list[list[str]]:
    """Build the ordered ufw command list (incremental, firewall никогда не выключается)."""
    if extra_ports and not source_ip:
        raise ConfigValidationError(
            "SECURITY: extra_ports require --source-ip <ip> (allow from <ip>, никогда 0.0.0.0/Anywhere) — S-8, T10.6"
        )
    rules: list[list[str]] = [
        # 1. Firewall активен с первой команды (S-14) — нет окна disable/reset
        ["ufw", "--force", "enable"],
        # 2. Default-deny ПЕРЕД allow-правилами — ничего не открыто по умолчанию
        ["ufw", "default", "deny", "incoming"],
        ["ufw", "default", "allow", "outgoing"],
        # 3. SSH первым — lockout-safe при переконфигурации
        ["ufw", "allow", "22/tcp", "comment", "platform-baseline"],
    ]
    rules.extend(["ufw", "allow", f"{port}/tcp", "comment", "platform-baseline"] for port in (80, 443))
    # extra_ports — ТОЛЬКО с явным источником (S-8): allow from <ip> to any port <p>/tcp
    rules.extend(
        ["ufw", "allow", "from", source_ip, "to", "any", "port", f"{port}/tcp", "comment", "platform-extra"]
        for port in extra_ports
    )
    # 142 W6 (A2/A3): Tor/Privoxy — доступ с docker-моста (172.16.0.0/12) к privoxy :8118.
    # Контейнеры (grafana telegram-канал, B14: host.docker.internal = docker-gateway) ходят
    # на privoxy; правило было ручным ufw allow — теперь декларативный baseline при TOR_ENABLED.
    if tor_enabled:
        rules.append(
            [
                "ufw",
                "allow",
                "from",
                TOR_PRIVOXY_NET,
                "to",
                "any",
                "port",
                str(TOR_PRIVOXY_PORT),
                "proto",
                "tcp",
                "comment",
                "platform-tor-privoxy",
            ]
        )
    # Модульные внутренние порты — явный deny (defense-in-depth поверх 127.0.0.1-bind)
    rules.extend(
        ["ufw", "deny", f"{port}/tcp", "comment", "platform-module-deny"] for port in sorted(MODULE_PORTS_DENY)
    )
    rules.append(["ufw", "deny", f"{DENY_PORT}/tcp", "comment", "explicit-deny-postgresql"])
    return rules


# endregion FUNC_build_rules


# region FUNC_collect_stale_platform_rules
## @purpose  Детекция stale allow-правил платформы (комментарий platform-*): порты, которые больше
##           НЕ в желаемом allow-наборе (baseline + extra) и не в deny-наборе → подлежат удалению.
##           Идемпотентность БЕЗ `ufw reset` (S-14): удаляем точечно ТОЛЬКО свои правила.
## @io       ⇥ status_text: str (текущий `ufw status verbose`), desired_allow: set[int] → ⎋ list[list[str]]
## @complexity O(L) — L = строк статуса
def collect_stale_platform_rules(status_text: str, desired_allow: set[int]) -> list[list[str]]:
    """Delete-команды для platform-* allow-правил, чьи порты вышли из желаемого набора."""
    deletes: list[list[str]] = []
    denied = set(MODULE_PORTS_DENY) | {DENY_PORT}
    for line in status_text.splitlines():
        # Формат: `9000/tcp ALLOW IN Anywhere  # platform-extra` (комментарий в конце)
        if "# platform-" not in line:
            continue
        m = re.match(r"^(\d+)/tcp\s+ALLOW", line.strip())
        if not m:
            continue
        port = int(m.group(1))
        if port in desired_allow or port in denied:
            continue
        deletes.append(["ufw", "delete", "allow", f"{port}/tcp"])
        logger.info("[IMP:8][firewall][reconcile] Stale platform allow %d/tcp → delete", port)
    return deletes


# endregion FUNC_collect_stale_platform_rules


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
## @purpose  Verify ufw status: active, baseline ALLOW, forbidden NOT ALLOW, 5432 DENY,
##           модульные порты NOT ALLOW (S-8/T10.6 CHECK по реестру модулей),
##           tor-privoxy правило 8118 при TOR_ENABLED (142 W6).
## @io       ⇥ status_text: str, tor_enabled: bool = False → ⎋ bool
## @complexity O(1) — parse + проверки
def verify_firewall(status_text: str, tor_enabled: bool = False) -> bool:
    """Verify ufw status against the policy. True = compliant."""
    active, port_actions = parse_ufw_status(status_text)
    if not active:
        logger.error("[IMP:10][firewall][verify] ufw is NOT active after apply")
        return False
    for port in BASELINE_PORTS:
        if port_actions.get(port) != "ALLOW":
            logger.error("[IMP:10][firewall][verify] Expected port %d/tcp ALLOW not found", port)
            return False
    # 142 W6 (A3): при TOR_ENABLED правило privoxy (172.16.0.0/12 → 8118) ОБЯЗАНО быть в статусе.
    # ufw status verbose показывает его как `8118/tcp ALLOW IN 172.16.0.0/12  # platform-tor-privoxy`.
    if tor_enabled and not re.search(rf"^\s*{TOR_PRIVOXY_PORT}/tcp\s+ALLOW", status_text, re.M):
        logger.error(
            "[IMP:10][firewall][verify] SECURITY: tor-privoxy rule %s→%d ALLOW missing (142 W6)",
            TOR_PRIVOXY_NET,
            TOR_PRIVOXY_PORT,
        )
        return False
    for port in FORBIDDEN_PORTS:
        if port_actions.get(port) == "ALLOW":
            logger.error("[IMP:10][firewall][verify] SECURITY: Docker API port %d is open in ufw", port)
            return False
    if port_actions.get(DENY_PORT) != "DENY":
        logger.error("[IMP:10][firewall][verify] SECURITY: Port %d is not DENIED in ufw", DENY_PORT)
        return False
    for port in MODULE_PORTS_DENY:
        if port_actions.get(port) == "ALLOW":
            logger.error("[IMP:10][firewall][verify] SECURITY: module-internal port %d is ALLOW in ufw (S-8)", port)
            return False
    logger.info(
        "[IMP:9][firewall][verify] Firewall verified: active, 22/80/443 open, Docker ports closed, module ports denied"
        + (" + tor-privoxy 8118 allow" if tor_enabled else "")
    )
    return True


# endregion FUNC_verify_firewall


# region FUNC_apply_rules_subprocess
## @purpose  Применить ufw-команды через subprocess. Первая (enable) fail-fast; остальные fail-fast
##           (инкрементальный apply — любая ошибка = политика не применена, честный отказ).
## @io       ⇥ rules: list[list[str]] → ⎋ bool
## @complexity O(R) — R = команд
def _apply_rules_subprocess(rules: list[list[str]]) -> bool:
    """Run ufw commands via subprocess. All fail-fast (S-14 — no best-effort disable window)."""
    for cmd in rules:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:
            logger.error("[IMP:10][firewall][apply] ufw not available: %s", exc)
            return False
        if result.returncode != 0:
            logger.error("[IMP:10][firewall][apply] ufw command failed: %s %s", " ".join(cmd), result.stderr.strip())
            return False
    logger.info("[IMP:9][firewall][apply] Incremental ufw policy applied (no disable/reset window)")
    return True


# endregion FUNC_apply_rules_subprocess


# region FUNC_run
## @purpose  Полный прогон: validate → build (incremental) → stale-reconcile → apply → verify.
##           142 W6: tor_enabled=None → os.environ TOR_ENABLED (φ1-процесс имеет env; параметр
##           для тестируемости чистых функций).
## @io       ⇥ extra_ports: list[str], source_ip: str|None, tor_enabled: bool|None → ⎋ bool
## @complexity O(R + L)
def run(extra_ports: list[str], source_ip: str | None = None, tor_enabled: bool | None = None) -> bool:
    """Full firewall pipeline: validate ports, build incremental rules, reconcile stale, apply, verify."""
    if tor_enabled is None:
        tor_enabled = os.environ.get("TOR_ENABLED", "false").lower() == "true"
    try:
        ports = validate_ports(extra_ports)
        rules = build_rules(ports, source_ip, tor_enabled=tor_enabled)
    except ConfigValidationError as exc:
        logger.error("[IMP:10][firewall][run] %s", exc)
        return False
    # Stale-reconcile (S-14): удалить platform-* allow-правила, вышедшие из желаемого набора —
    # идемпотентность без ufw reset. Читаем статус ДО apply (текущее состояние).
    try:
        status_before = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True)
        before_text = status_before.stdout if status_before.returncode == 0 else ""
    except OSError:
        before_text = ""
    desired_allow = set(BASELINE_PORTS) | set(ports)
    if tor_enabled:
        desired_allow.add(TOR_PRIVOXY_PORT)
    rules.extend(collect_stale_platform_rules(before_text, desired_allow))
    if not _apply_rules_subprocess(rules):
        return False
    try:
        status = subprocess.run(["ufw", "status", "verbose"], capture_output=True, text=True)
        status_text = status.stdout if status.returncode == 0 else ""
    except OSError:
        status_text = ""
    return verify_firewall(status_text, tor_enabled=tor_enabled)


# endregion FUNC_run


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.bootstrap.firewall [--source-ip <ip>] [extra_ports...]`.

    ▶ ┌argv extra_ports (space-separated)┐ → ○ run() → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Incremental ufw firewall (DevPlan 118 E3 + 136 W10)")
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Source IP for extra_ports allow rules (S-8: extra_ports никогда не 0.0.0.0/Anywhere)",
    )
    parser.add_argument("extra_ports", nargs="*", help="Extra ports to allow from --source-ip (space-separated)")
    args = parser.parse_args()
    # 142 W6: TOR_ENABLED читается из env внутри run() (None → env); CLI не принимает флаг —
    # фасад firewall.sh вызывается из φ1, где TOR_ENABLED уже в окружении.
    return 0 if run(args.extra_ports, source_ip=args.source_ip) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
