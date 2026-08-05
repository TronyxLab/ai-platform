#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S1-S9 unattended-upgrades image-freshness digest-drift pending-security ufw sshd maxstartups drop-in apply-sshd docker live-restore world-writable forced-command listening-ports docker-proxy 0-0-0-0 exit-0-1-2 json DevPlan-134
# STRUCTURE: ▶ root-check → ◇ --apply-sshd? → ⚡ apply drop-in (content-match no-op → reload systemctl→service) → ⎋ exit 0|1 ┤
#            ○ 8 checks (S1-S8, каждая pure+subprocess probe; S4: maxstartups ≥ 30:50:200) → ○ aggregate (FAIL→2, WARN→1) → ○ text|json report → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Security posture check ноды (DevPlan 134 L2) — 9 проверок S1-S9, закрывающих главные
##           векторы автоматизированных ИИ-атак: автопатчинг (S1/S2), сетевой периметр (S3),
##           SSH-поверхность (S4, включая эффективный MaxStartups ≥ 30:50:200 + 9 директив), docker-демон (S5),
##           локальные привилегии (S6, включая критичные пути вне /opt/platform), целостность forced-command
##           канала деплоя (S7, per-line + perms), реальные LISTEN-порты (S9, docker-proxy на 0.0.0.0).
##           Выполняется НА ноде как root (sshd -T). DevPlan 136 W3: +apply_sshd_dropin()
##           (идемпотентный sshd_config.d drop-in MaxStartups, вызов из φ1 бутстрапа).
## @scope    Вызывается: make check-security NODE=<name> (remote через SSH-канал converge),
##           локально на ноде (rc=2 fallback). Импортирует firewall.parse_ufw_status (0 дублирования),
##           shared/subprocess_io (канон B4/C10), shared/atomic_writer (канон E5) и shared/timeouts (гейт U-11).
##           --apply-sshd — opt-in apply-режим (мутация /etc/ssh/sshd_config.d), вызывается ТОЛЬКО
##           из φ1 phase_system_bootstrap (lifecycle/phases/system.py, шаг 5.6).
## @invariants
##   - Exit: 0 = healthy, 1 = warnings (S2 pending security-апдейты — норма между daily-кронами),
##     2 = errors (любой FAIL: конфиг сломан, периметр открыт, канал деплоя повреждён)
##   - Каждая проверка — pure-функция check_*(ctx) -> CheckResult(status, message); subprocess
##     через run_subprocess (check=False, graceful); таймауты из shared/timeouts
##   - Root-check fail-fast: euid != 0 → exit 2 (sshd -T / --apply-sshd требуют root) — без половины отчёта
##   - --json: {"node", "exit_code", "checks": [{id, status, message}]} — фундамент L5-мониторинга
##   - По умолчанию НЕ мутирует систему (read-only диагностика, безопасен для прямого запуска);
##     --apply-sshd — единственная мутация (идемпотентная, content-match no-op, reload только при изменении)
##   - S4 проверяет ЭФФЕКТИВНЫЙ конфиг (sshd -T включает drop-in из sshd_config.d): 13 директив
##     (root-login/password/pubkey/maxstartups + 9 расширенных W10 T10.4); каждая — только если
##     директива ВЫВОДИТСЯ sshd -T (ненаблюдаемые → skip, graceful — тест-фикстуры без строки)
##   - S7: FAIL при ЛЮБОЙ строке authorized_keys БЕЗ канонического forced-command prefix
##     (command="...orchestrator_cli dispatch",restrict) + perms 0600 + owner ci-deploy (W10 T10.3)
##   - S9: FAIL если docker-proxy слушает 0.0.0.0 на порту вне {80,443} (nginx public by-design) —
##     реальный LISTEN-кросс-чек с compose (S-7/S-3/S-5, W10 T10.2)
## @rationale L2 security-гэпа (DevPlan 134): check-suite.yaml — только code-quality чеки;
##            security-постур ноды не проверялся ничем. Набор S1-S9 — минимально достаточный
##            (DevPlan D4), без мониторинг-тяжести (fail2ban/auditd — L5 follow-up).
## @rationale MaxStartups (DevPlan 136 W3): ручной конфиг — источник повторяющихся инцидентов
##            (свежий бутстрап не воспроизводил 30:50:200 → SSH connection-storm при параллельных
##            деплоях). drop-in в sshd_config.d — НЕ правка основного sshd_config (канон drop-in,
##            переживает apt-обновления sshd_config); эффективное значение читает sshd -T в S4.
## @changes 2026-08-04 | DevPlan 134 W2 — Created
## @changes 2026-08-05 | DevPlan 136 W3 — S4 +MaxStartups effective check; +apply_sshd_dropin()
##            (+CLI --apply-sshd, вызов из φ1 phase_system_bootstrap)
## @changes 2026-08-05 | DevPlan 136 W10 — S4 +9 sshd-директив (T10.4); S7 per-line+perms (T10.3);
##            S6 критичные пути (T10.8); +S9 real-LISTEN docker-proxy 0.0.0.0 (T10.2)
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
from core.internal.shared import docker_ops  # W1: docker ps/inspect/manifest примитивы (гейт docker_sole_path)
from core.internal.shared import subprocess_io as io
from core.internal.shared.atomic_writer import atomic_write_text  # E5: канон атомарной записи (drop-in)

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

# ── sshd MaxStartups (DevPlan 136 W3) ──
# drop-in в sshd_config.d (канон drop-in, НЕ правка основного sshd_config) — переживает
# apt-обновления sshd_config; sshd -T (S4) читает эффективное значение ВКЛЮЧАЯ drop-in.
SSHD_MAXSTARTUPS_DROPIN = "/etc/ssh/sshd_config.d/99-platform-maxstartups.conf"
# Минимально допустимое эффективное значение MaxStartups (start:rate:full).
# 30:50:200 — защита SSH от connection-storm при параллельных деплоях/healthcheck-прокидываниях.
# Дефолт OpenSSH = 10:30:100 < минимума → FAIL, пока drop-in не применён бутстрапом.
SSHD_MAXSTARTUPS_MIN = (30, 50, 200)
SSHD_MAXSTARTUPS_STR = "30:50:200"
_MAXSTARTUPS_RE = re.compile(r"^(\d+):(\d+):(\d+)$")

_APT_CHECK_RE = re.compile(r"(\d+)\s+updates can be applied immediately")
_APT_CHECK_SEC_RE = re.compile(r"(\d+)\s+of these updates are security updates")

# ── S9 (W10 T10.2): внутренние порты модулей — НИКОГДА 0.0.0.0 LISTEN (docker-proxy) ──
# Реестр берётся из firewall.MODULE_PORTS_DENY + DENY_PORT (SoT platform-infra.yaml) — единый
# источник с ufw-политикой. nginx 80/443 и user-проекты (произвольные web-порты, напр. 8080
# test-project-web на test-VPS) публикуются ПО ДИЗАЙНУ — вне реестра, не флагаются.
# Внутренние сервисы (postgres/minio/clickhouse/...) обязаны биндить 127.0.0.1 или не публиковаться
# (compose base.yml: NO ports / 127.0.0.1 bindings — верификация W10 на test-VPS, ss -tlnp).

# ── S4 (W10 T10.4): расширенные sshd-директивы (проверяемы через sshd -T) ──
# Каждая директива: (ключ sshd -T, ожидание, fail-сообщение). Проверяется ТОЛЬКО если директива
# присутствует в выводе sshd -T (ненаблюдаемые → skip, graceful — фикстуры без строки не падают).
# AllowUsers: отсутствие строки в sshd -T = нода БЕЗ allowlist (Ubuntu печатает allowusers только
# при явной настройке) → skip (graceful — не ложнопозитивный FAIL на дефолтных нодах);
# allowusers задан ПУСТЫМ (директива присутствует без списка) → FAIL (явная политика нарушена).
_SSHD_WEAK_KEX = ("diffie-hellman-group1-sha1", "diffie-hellman-group14-sha1", "diffie-hellman-group-exchange-sha1")
_SSHD_WEAK_CIPHERS = ("arcfour", "3des-cbc", "aes128-cbc", "aes192-cbc", "aes256-cbc", "des-cbc", "blowfish-cbc")
_SSHD_WEAK_MACS = ("hmac-md5", "hmac-md5-96", "hmac-sha1", "hmac-sha1-96", "umac-64", "umac-64@openssh.com")
SSHD_CLIENT_ALIVE_INTERVAL_MIN = 300
SSHD_LOGIN_GRACE_TIME_MAX = 120
# (sshd -T key, expected-or-checker, label)
# checker-формы: ("eq", value) — равенство; ("gte", n) — >=; ("lte", n) — <=;
# ("not_contains_any", weak_list) — ни один слабый алгоритм; ("present_nonempty",) — не пуст
_SSHD_EXTRA_DIRECTIVES: list[tuple[str, tuple, str]] = [
    ("allowusers", ("present_nonempty",), "AllowUsers unset (no user allowlist — every user may ssh)"),
    (
        "clientaliveinterval",
        ("gte", SSHD_CLIENT_ALIVE_INTERVAL_MIN),
        f"ClientAliveInterval < {SSHD_CLIENT_ALIVE_INTERVAL_MIN}s (idle connections linger)",
    ),
    ("permituserenvironment", ("eq", "no"), "PermitUserEnvironment=yes (env injection into sshd session)"),
    ("x11forwarding", ("eq", "no"), "X11Forwarding=yes (X11 channel exposure)"),
    ("allowtcpforwarding", ("eq", "no"), "AllowTcpForwarding=yes (TCP tunnel via ssh)"),
    ("kexalgorithms", ("not_contains_any", _SSHD_WEAK_KEX), "weak KexAlgorithms present (diffie-hellman-*-sha1)"),
    ("ciphers", ("not_contains_any", _SSHD_WEAK_CIPHERS), "weak Ciphers present (arcfour/cbc/3des)"),
    ("macs", ("not_contains_any", _SSHD_WEAK_MACS), "weak MACs present (md5/sha1/umac-64)"),
    (
        "logingracetime",
        ("lte", SSHD_LOGIN_GRACE_TIME_MAX),
        f"LoginGraceTime > {SSHD_LOGIN_GRACE_TIME_MAX}s (slow-brute window)",
    ),
]
# UsePAM сознательно НЕ проверяется (9 директив ≥ 8 по T10.4): самостоятельной security-ценности
# не имеет — связка «PasswordAuthentication=no + PubkeyAuthentication=yes» уже закрывает парольный
# вход; ожидание UsePAM зависит от PAM-стека (ложно-позитивный риск, документировано W10 T10.4).


# region DATACLS_CheckResult
@dataclass(frozen=True)
class CheckResult:
    """Результат одной проверки S1-S8."""

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
##           prohibit-password|no, PasswordAuthentication no, PubkeyAuthentication yes,
##           MaxStartups ≥ 30:50:200 (покомпонентно; DevPlan 136 W3) + 9 расширенных директив
##           (AllowUsers, ClientAliveInterval, PermitUserEnvironment, X11Forwarding,
##           AllowTcpForwarding, KexAlgorithms, Ciphers, MACs, LoginGraceTime — DevPlan 136 W10 T10.4).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — один subprocess + regex
## @invariants  Требует root (sshd -T) — гарантируется root-check в main
##              sshd -T печатает ЭФФЕКТИВНЫЙ конфиг (включая drop-in sshd_config.d) —
##              проверяем именно эффективное значение, не исходный sshd_config
##              Ненаблюдаемое значение (нет maxstartups в выводе) → PASS (graceful:
##              тест-фикстуры без строки; реальный sshd -T всегда печатает дефолт 10:30:100 → FAIL)
##              Расширенные директивы проверяются ТОЛЬКО при наличии в выводе sshd -T;
##              allowusers задан пустым (директива есть без списка) → FAIL; отсутствует строка → skip
def check_sshd() -> CheckResult:
    """S4: sshd effective config — root login restricted, password auth off, pubkey on,
    MaxStartups >= 30:50:200, +9 hardening-директив (W10 T10.4)."""
    result = _probe(["sshd", "-T"], timeout=30)
    if result.returncode != 0:
        return CheckResult("S4", STATUS_FAIL, f"sshd -T failed (rc={result.returncode})")
    text = str(getattr(result, "stdout", ""))
    settings: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        # W10 T10.4: пусто-значные директивы (allowusers без списка) тоже фиксируются — value ""
        # (исторический парсер требовал ≥2 частей → present_nonempty для AllowUsers был мёртвым кодом)
        settings[parts[0].lower()] = parts[1].lower() if len(parts) >= 2 else ""
    problems: list[str] = []
    root_login = settings.get("permitrootlogin", "")
    if root_login not in ("no", "prohibit-password"):
        problems.append(f"PermitRootLogin={root_login or 'unset'} (expected no|prohibit-password)")
    if settings.get("passwordauthentication", "") == "yes":
        problems.append("PasswordAuthentication=yes (password auth enabled)")
    if settings.get("pubkeyauthentication", "") != "yes":
        problems.append("PubkeyAuthentication != yes")
    # MaxStartups (DevPlan 136 W3): sshd -T = ЭФФЕКТИВНЫЙ конфиг (включая drop-in из
    # sshd_config.d) — проверяем именно эффективное значение. Дефолт OpenSSH 10:30:100
    # < 30:50:200 → FAIL, пока 99-platform-maxstartups.conf не применён (apply_sshd_dropin).
    maxstartups_raw = settings.get("maxstartups", "")
    if maxstartups_raw:
        ms = _parse_maxstartups(maxstartups_raw)
        if ms is None:
            problems.append(f"MaxStartups={maxstartups_raw} (unparseable — expected start:rate:full)")
        elif any(a < b for a, b in zip(ms, SSHD_MAXSTARTUPS_MIN, strict=True)):
            problems.append(
                f"MaxStartups={maxstartups_raw} < {SSHD_MAXSTARTUPS_STR} "
                "(drop-in 99-platform-maxstartups.conf missing or too low)"
            )
    # Расширенные директивы (W10 T10.4) — только присутствующие в sshd -T
    for key, check, fail_msg in _SSHD_EXTRA_DIRECTIVES:
        if key not in settings:
            logger.info("[IMP:8][posture][S4] %s not in sshd -T output — skipped (graceful)", key)
            continue
        value = settings[key]
        kind = check[0]
        if kind == "eq" and value != check[1]:
            problems.append(f"{fail_msg} (current: {value})")
        elif kind == "gte":
            try:
                if int(value) < check[1]:
                    problems.append(f"{fail_msg} (current: {value})")
            except ValueError:
                problems.append(f"{key}={value} (unparseable integer)")
        elif kind == "lte":
            try:
                if int(value) > check[1]:
                    problems.append(f"{fail_msg} (current: {value})")
            except ValueError:
                problems.append(f"{key}={value} (unparseable integer)")
        elif kind == "present_nonempty" and not value:
            problems.append(fail_msg)
        elif kind == "not_contains_any":
            weak = [w for w in check[1] if w in value]
            if weak:
                problems.append(f"{fail_msg} (found: {', '.join(weak[:4])})")
    if problems:
        return CheckResult("S4", STATUS_FAIL, "; ".join(problems))
    detail = f", MaxStartups={maxstartups_raw}" if maxstartups_raw else ""
    logger.info("[IMP:9][posture][S4] SSH surface hardened%s", detail)
    return CheckResult("S4", STATUS_PASS, f"sshd: root login restricted, password auth off, pubkey on{detail}")


# endregion FUNC_check_sshd


# region FUNC__parse_maxstartups
## @purpose  Парсер эффективного MaxStartups 'start:rate:full' → (start, rate, full).
## @io       ⇥ value: str (lowercased из sshd -T) → ⎋ tuple[int,int,int] | None (malformed)
## @complexity O(1) — regex
## @invariants  Не-числовой формат (напр. 'random:50:200', OpenSSH ≥9.6) → None → FAIL в S4
##              (политика должна быть явной; числовой канон платформы — 30:50:200)
def _parse_maxstartups(value: str) -> tuple[int, int, int] | None:
    """Parse 'start:rate:full' → (start, rate, full); None if malformed."""
    m = _MAXSTARTUPS_RE.match(value.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# endregion FUNC__parse_maxstartups


# region FUNC_desired_maxstartups_dropin
## @purpose  Желаемое содержимое /etc/ssh/sshd_config.d/99-platform-maxstartups.conf.
## @io       ⇥ — → ⎋ str — drop-in (комментарий + директива MaxStartups)
## @complexity O(1)
## @invariants  Файл помечен «Generated — DO NOT EDIT MANUALLY» (политика управления —
##              файлы перезаписываются платформой, канон security_updates.py)
##              Директива — ТОЛЬКО MaxStartups (другие sshd-директивы — вне скоупа W3)
def desired_maxstartups_dropin() -> str:
    """99-platform-maxstartups.conf: MaxStartups 30:50:200 (защита от connection-storm)."""
    return (
        "# Generated by ai-platform security_posture.py (DevPlan 136 W3) — DO NOT EDIT MANUALLY\n"
        + "# MaxStartups 30:50:200 — защита SSH от connection-storm при параллельных деплоях/\n"
        + "# healthcheck-прокидываниях. sshd_config.d drop-in — НЕ правка основного sshd_config;\n"
        + "# sshd -T (S4) читает эффективное значение ВКЛЮЧАЯ drop-in.\n"
        + f"MaxStartups {SSHD_MAXSTARTUPS_STR}\n"
    )


# endregion FUNC_desired_maxstartups_dropin


# region FUNC__write_if_changed
## @purpose  Content-match idempotent write: существующий файл с идентичным содержимым → no-op.
## @io       ⇥ path: Path, desired: str → ⎋ (changed: bool, ok: bool) — changed=потребуется reload
## @complexity O(1) — одно чтение + при необходимости атомарная запись
## @invariants  НИКОГДА не пишет на диск при совпадении содержимого (строгая идемпотентность)
##              Атомарная запись через shared/atomic_writer (temp + fsync + os.replace, 0644)
##              Ошибка записи → (False, False) — вызывающий решает fatal/non-fatal
def _write_if_changed(path: Path, desired: str) -> tuple[bool, bool]:
    """Write file only when content differs. Returns (changed, ok)."""
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if existing == desired:
        logger.info("[IMP:8][posture][maxstartups][noop] %s unchanged — no-op (idempotent)", path)
        return False, True
    try:
        atomic_write_text(str(path), desired, mode=0o644)
    except OSError as e:
        logger.error("[IMP:10][posture][maxstartups][write] Cannot write %s: %s", path, e)
        return False, False
    logger.info("[IMP:9][posture][maxstartups][write] %s %s", path, "updated" if existing else "created")
    return True, True


# endregion FUNC__write_if_changed


# region FUNC__reload_sshd
## @purpose  Reload sshd: systemctl reload sshd → fallback service ssh reload. True = успех.
## @io       ⇥ — → ⎋ bool
## @complexity O(2) — до двух subprocess-проб
## @invariants  fallback на `service ssh reload` — systemd-отсутствие (container/chroot) не
##              должно ломать apply; обе пробы через _probe (graceful, никогда не raise)
def _reload_sshd() -> bool:
    """Reload sshd effective config — systemctl reload sshd, fallback service ssh reload."""
    for cmd in (["systemctl", "reload", "sshd"], ["service", "ssh", "reload"]):
        result = _probe(cmd, timeout=30)
        if result.returncode == 0:
            logger.info("[IMP:9][posture][reload] sshd reloaded via %s", " ".join(cmd))
            return True
        logger.warning("[IMP:8][posture][reload] %s failed (rc=%s) — trying fallback", " ".join(cmd), result.returncode)
    logger.error("[IMP:10][posture][reload] sshd reload failed (systemctl + service both non-zero)")
    return False


# endregion FUNC__reload_sshd


# region FUNC_apply_sshd_dropin
## @purpose  Применить sshd MaxStartups drop-in идемпотентно (DevPlan 136 W3): content-match
##           no-op; при изменении — атомарная запись + reload sshd. Вызывается из φ1
##           phase_system_bootstrap (CLI --apply-sshd), НЕ из check-потока.
## @io       ⇥ — → ⎋ bool (True = применено/no-op; False = ошибка записи ИЛИ reload)
## @complexity O(1) + до 2 reload-проб
## @invariants  no-op при совпадении содержимого (reload НЕ вызывается)
##              reload — только при изменении содержимого (systemctl → service fallback)
##              Запись удалась, но reload не удался → False (конфиг не активен — честный отказ)
## @rationale  apply в security_posture (не в phases/system.py): sshd-политика живёт в одном
##             модуле с S4-проверкой (единый SoT эффективного значения); фаза вызывает CLI.
def apply_sshd_dropin() -> bool:
    """Apply sshd MaxStartups drop-in idempotently (content-match no-op; reload on change)."""
    path = Path(SSHD_MAXSTARTUPS_DROPIN)
    changed, ok = _write_if_changed(path, desired_maxstartups_dropin())
    if not ok:
        logger.error("[IMP:10][posture][maxstartups] Drop-in apply aborted — write failed")
        return False
    if changed:
        if not _reload_sshd():
            logger.error(
                "[IMP:10][posture][maxstartups] Drop-in written but sshd reload FAILED — "
                "новый MaxStartups не активен до перезапуска sshd"
            )
            return False
        logger.info("[IMP:9][posture][maxstartups] Drop-in applied + sshd reloaded")
    else:
        logger.info("[IMP:8][posture][maxstartups] Drop-in already current — no-op (idempotent)")
    return True


# endregion FUNC_apply_sshd_dropin


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
##           повышения привилегий) + world-readable файлы в /opt/platform/secrets (age-ключи/пароли)
##           + world-writable НА КРИТИЧНЫХ ПУТЯХ вне платформы (W10 T10.8, S-10):
##           ~ci-deploy/.ssh, /etc/sudoers.d, /var/log/platform, /etc/age (файлы и директории).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(n) — find-пробы по дереву платформы + критические пути
## @invariants  Отсутствие /opt/platform → WARN (нода не развёрнута — не security-ошибка)
##              Критичные пути: world-writable (perm -0002) — файл И директория (вектор
##              подмены authorized_keys / sudoers-инъекции / audit-тампера); отсутствие пути → skip
def check_file_perms() -> CheckResult:
    """S6: file permissions — no world-writable files, secrets not world-readable, critical paths safe."""
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
    # W10 T10.8 (S-10): критические пути вне /opt/platform — world-writable файл ИЛИ директория
    # = локальная эскалация (подмена authorized_keys / sudoers.d / audit-журнала / AGE-ключа).
    problems.extend(_check_critical_paths_world_writable())
    if problems:
        return CheckResult("S6", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S6] File permissions clean (incl. critical paths)")
    return CheckResult("S6", STATUS_PASS, "no world-writable files, secrets not world-readable, critical paths safe")


# endregion FUNC_check_file_perms


# region FUNC_check_critical_paths_world_writable
## @purpose  Проба world-writable (perm -0002, файлы И директории) по критичным путям (W10 T10.8):
##           ~ci-deploy/.ssh (подмена authorized_keys), /etc/sudoers.d (sudoers-инъекция),
##           /var/log/platform (audit-тампер), /etc/age (AGE-ключ). Отсутствующий путь → skip.
## @io       ⇥ — → ⎋ list[str] — найденные нарушения (пусто = чисто)
## @complexity O(P) — P = критичных путей, каждая find-проба O(дерево пути)
def _check_critical_paths_world_writable() -> list[str]:
    """Find world-writable files/dirs under critical security paths (T10.8, S-10)."""
    problems: list[str] = []
    ci_deploy_ssh = os.path.expanduser("~ci-deploy/.ssh")
    for path in (ci_deploy_ssh, "/etc/sudoers.d", "/var/log/platform", "/etc/age"):
        if not os.path.exists(path):
            logger.info("[IMP:8][posture][S6] critical path absent — skipped: %s", path)
            continue
        ww = _probe(["find", path, "-perm", "-0002"], timeout=30)
        if ww.returncode == 0:
            hits = [ln for ln in str(getattr(ww, "stdout", "")).splitlines() if ln.strip()]
            if hits:
                problems.append(
                    f"world-writable under {path}: {', '.join(hits[:4])}" + ("..." if len(hits) > 4 else "")
                )
    return problems


# endregion FUNC_check_critical_paths_world_writable


# region FUNC_check_forced_command
## @purpose  S7: целостность forced-command канала деплоя — КАЖДАЯ строка ci-deploy authorized_keys
##           содержит command="...orchestrator_cli dispatch",restrict (W10 T10.3/S-4): ЛЮБАЯ строка
##           без канонического prefix = открытый SSH-канал (root-экспозиция деплоя).
##           + проверка perms 0600 и owner ci-deploy (authorized_keys не должен быть world-readable
##           или подменён другим владельцем).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — одно чтение файла + stat
## @invariants  Сверка с каноном phases/system.py φ2 (DevPlan 116 B1 + волна 117 D1):
##              command="cd {base} && PYTHONPATH={base} python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict
##              Пустые строки и комментарии (начинающиеся с #) — не ключи — пропускаются
##              Файл отсутствует → FAIL; нечитаем → FAIL; perms != 0600 → FAIL; owner != ci-deploy → FAIL
def check_forced_command() -> CheckResult:
    """S7: ci-deploy forced-command dispatch intact per-line + perms 0600/owner (T10.3)."""
    keys_file = Path(CI_DEPLOY_AUTHORIZED_KEYS)
    if not keys_file.is_file():
        return CheckResult("S7", STATUS_FAIL, f"{CI_DEPLOY_AUTHORIZED_KEYS} missing")
    try:
        lines = keys_file.read_text().splitlines()
    except OSError as e:
        return CheckResult("S7", STATUS_FAIL, f"cannot read authorized_keys: {e}")
    # ── Perms/owner (W10 T10.3) ──
    try:
        st = keys_file.stat()
        mode = st.st_mode & 0o777
        if mode != 0o600:
            return CheckResult("S7", STATUS_FAIL, f"authorized_keys mode {oct(mode)} != 0600 (world-readable key file)")
        import pwd

        owner = pwd.getpwuid(st.st_uid).pw_name
        if owner != "ci-deploy":
            return CheckResult("S7", STATUS_FAIL, f"authorized_keys owner '{owner}' != ci-deploy")
    except (KeyError, OSError) as e:
        return CheckResult("S7", STATUS_FAIL, f"cannot stat authorized_keys: {e}")
    # ── Per-line forced-command (W10 T10.3): ЛЮБАЯ строка без канона = FAIL ──
    violations: list[str] = []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith('command="') and "orchestrator_cli dispatch" in stripped and "restrict" in stripped:
            continue
        violations.append(f"line {idx}: {stripped[:60]}")
    if violations:
        return CheckResult(
            "S7", STATUS_FAIL, f"{len(violations)} line(s) WITHOUT forced-command prefix: {'; '.join(violations[:3])}"
        )
    logger.info("[IMP:9][posture][S7] Forced-command dispatch intact (all lines, perms 0600, owner ci-deploy)")
    return CheckResult(
        "S7", STATUS_PASS, "ci-deploy key restricted to orchestrator_cli dispatch (all lines, perms 0600)"
    )


# endregion FUNC_check_forced_command


# region FUNC__collect_manifest_digests
## @purpose  Извлечь набор digest'ов из `docker manifest inspect --verbose` (один манифест ИЛИ
##           multi-arch список — Descriptor.digest в обоих формах).
## @io       ⇥ data: object (json.loads результат) → ⎋ set[str] — sha256:... digest'ы
## @complexity O(n) — n = число Descriptor'ов
## @invariants  Пустой результат → registry-digest не определён (WARN, не ложный FAIL)
def _collect_manifest_digests(data: object) -> set[str]:
    """Collect digest values from manifest inspect --verbose (dict or list form)."""
    result: set[str] = set()
    if isinstance(data, dict):
        desc = data.get("Descriptor") or {}
        digest = desc.get("digest") if isinstance(desc, dict) else None
        if digest:
            result.add(str(digest))
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            desc = item.get("Descriptor") or {}
            digest = desc.get("digest") if isinstance(desc, dict) else None
            if digest:
                result.add(str(digest))
    return result


# endregion FUNC__collect_manifest_digests


# region FUNC_check_image_freshness
## @purpose  S8: docker image freshness — для каждого запущенного контейнера сравнить локальный
##           digest (RepoDigests) с текущим digest'ом тега в registry (docker manifest inspect —
##           использует существующий docker auth на ноде: ghcr φ6, Docker Hub φ3). Отклонение =
##           «образ устарел, апстрим опубликовал новый (вероятно, security-фиксы)» (DevPlan 134 L4).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(C + R) — C = контейнеры (2 docker-вызова), R = registry-запросов (1/образ)
## @invariants  Digest-pinned ref (tag + sha256-суффикс): tag_ref = часть до суффикса — сравнивается digest тега
##              Локально-собранные образы (пустой RepoDigests) → skip (не трекаются)
##              Локальные имена (manifest unknown) → PASS (registry их не знает — не дрейф)
##              Registry недоступен/timeout/rate-limit → WARN (graceful, как apt-check в S2)
##              Только WARN (никогда FAIL по дрейфу) — digest-pin — осознанная политика
##              (гейт image_tag_form): дрейф = «пора обновлять», не «сломано»
## @rationale L4-детекция (DevPlan 134): content-hash skip не подхватывает фиксы базовых образов;
##            digest-drift ловит любой опубликованный апстрим-фикс дешевле trivy на ноде
##            (CVE-точность — CI-скан L3). Docker manifest inspect не тянет слои — дешёвый запрос.
def check_image_freshness() -> CheckResult:
    """S8: image freshness — local digest vs registry digest (drift = update available)."""
    # W1 (DevPlan 128): docker ps/inspect/manifest — shared/docker_ops (non-fatal)
    ps = docker_ops.docker_ps(format="{{.ID}}", timeout=DOCKER_CMD_TIMEOUT)
    if ps.returncode != 0:
        return CheckResult("S8", STATUS_FAIL, f"docker ps failed (rc={ps.returncode}) — cannot assess images")
    cids = [ln.strip() for ln in str(getattr(ps, "stdout", "")).splitlines() if ln.strip()]
    if not cids:
        logger.info("[IMP:9][posture][S8] No running containers — nothing to track")
        return CheckResult("S8", STATUS_PASS, "no running containers — nothing to track")

    inspect = docker_ops.docker_inspect_many(
        cids,
        format="{{.Config.Image}}|{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}",
        timeout=DOCKER_CMD_TIMEOUT,
    )
    if inspect.returncode != 0:
        return CheckResult("S8", STATUS_FAIL, f"docker inspect failed (rc={inspect.returncode})")

    stale: list[str] = []
    skipped = 0
    checked = 0
    for line in str(getattr(inspect, "stdout", "")).splitlines():
        ref, _, local_digest = line.strip().partition("|")
        if not ref or not local_digest:
            skipped += 1  # локально-собранный образ — registry-digest базиса нет
            continue
        tag_ref = ref.split("@")[0]
        if not tag_ref:
            skipped += 1
            continue
        registry = docker_ops.docker_manifest_inspect_raw(tag_ref, timeout=DOCKER_CMD_TIMEOUT, flags=["--verbose"])
        if registry.returncode == 124:
            stale.append(f"{ref} (registry query timed out)")
            continue
        if registry.returncode != 0:
            stderr = str(getattr(registry, "stderr", "")).strip()
            if any(token in stderr.lower() for token in ("no such manifest", "manifest unknown", "not found")):
                skipped += 1  # локальное имя — registry его не знает
                continue
            stale.append(
                f"{ref} (registry query failed: {stderr.splitlines()[0] if stderr else f'rc={registry.returncode}'})"
            )
            continue
        try:
            registry_digests = _collect_manifest_digests(json.loads(str(getattr(registry, "stdout", ""))))
        except json.JSONDecodeError:
            registry_digests = set()
        if not registry_digests:
            stale.append(f"{ref} (registry digest not parseable)")
            continue
        checked += 1
        if local_digest not in registry_digests:
            if "@" in ref:
                stale.append(
                    f"{ref}: pin устарел — registry выдаёт другой digest (апстрим опубликовал новый образ, "
                    "вероятно с security-фиксами); обновите пин в compose + node-update"
                )
            else:
                stale.append(
                    f"{ref}: в registry более свежий образ (digest отличен) — пересобрать L2 "
                    "(make hermes-build-context) и задеплоить"
                )

    if stale:
        logger.info("[IMP:8][posture][S8] %d stale image(s) of %d checked", len(stale), checked)
        return CheckResult("S8", STATUS_WARN, "; ".join(stale))
    logger.info("[IMP:9][posture][S8] All %d images current (skipped local-built: %d)", checked, skipped)
    return CheckResult("S8", STATUS_PASS, f"all {checked} tracked images current in registry")


# endregion FUNC_check_image_freshness


# region FUNC_check_listening_ports
## @purpose  S9 (W10 T10.2, S-7): реальный LISTEN-кросс-чек — docker-proxy НЕ должен слушать 0.0.0.0
##           на ВНУТРЕННИХ портах модулей (реестр MODULE_PORTS_DENY из firewall.py — SoT
##           platform-infra.yaml). Внутренние сервисы (postgres/minio/clickhouse/redis/...) обязаны
##           биндить 127.0.0.1 или не публиковаться (compose base.yml: NO ports / 127.0.0.1 bindings —
##           верификация W10 на test-VPS, ss -tlnp). docker-proxy 0.0.0.0 на внутреннем порту =
##           утечка сервиса наружу в обход loopback-контроля.
##           user-проекты публикуют web-порты (напр. test-project-web 0.0.0.0:8080) ПО ДИЗАЙНУ —
##           они НЕ флагаются (не входят в реестр внутренних портов; верификация test-VPS).
## @io       ⇥ — → ⎋ CheckResult
## @complexity O(1) — один `ss -tlnp` probe + regex
## @invariants  Слушает только `ss -tlnp` (реальный LISTEN, не compose-декларация — кросс-чек S-7)
##              [::] wildcard эквивалентен 0.0.0.0 (IPv6-дубль) — тоже FAIL для внутренних портов
##              Строки без process docker-proxy пропускаются (sshd 22, systemd-resolve 53 — вне скоупа)
##              Реестр внутренних портов — MODULE_PORTS_DENY + DENY_PORT (firewall.py, SoT) —
##              единый источник с ufw-политикой (не дублировать литералы)
def check_listening_ports() -> CheckResult:
    """S9: no docker-proxy listening on 0.0.0.0 for module-internal ports (real LISTEN cross-check)."""
    # Единый реестр внутренних портов модулей (SoT platform-infra.yaml) — кросс-чек с compose.
    # Импорт из firewall.py: тот же пакет core/internal/bootstrap, никакого цикла (firewall не
    # импортирует security_posture). Публичные порты nginx 80/443 в реестр НЕ входят (by-design),
    # user-проекты публикуют произвольные порты — тоже вне реестра.
    from core.internal.bootstrap.firewall import DENY_PORT as _FIREWALL_DENY_PORT
    from core.internal.bootstrap.firewall import MODULE_PORTS_DENY as _MODULE_PORTS

    internal_ports = set(_MODULE_PORTS) | {_FIREWALL_DENY_PORT}
    ss = _probe(["ss", "-tlnp"], timeout=DOCKER_CMD_TIMEOUT)
    if ss.returncode != 0:
        return CheckResult("S9", STATUS_FAIL, f"ss -tlnp failed (rc={ss.returncode}) — cannot assess listeners")
    ss_out = str(getattr(ss, "stdout", ""))
    violations: list[str] = []
    for line in ss_out.splitlines():
        if "docker-proxy" not in line:
            continue
        m = re.search(r"(0\.0\.0\.0|\[::\]):(\d+)", line)
        if not m:
            continue
        port = int(m.group(2))
        if port not in internal_ports:
            # user-проекты / прочие публичные порты — вне реестра внутренних сервисов
            continue
        violations.append(f"0.0.0.0:{port}")
    if violations:
        return CheckResult(
            "S9",
            STATUS_FAIL,
            "docker-proxy listening on 0.0.0.0 for module-internal port(s): "
            + ", ".join(sorted(set(violations)))
            + f" (registry: {', '.join(str(p) for p in sorted(internal_ports))})",
        )
    logger.info("[IMP:9][posture][S9] No docker-proxy on 0.0.0.0 for module-internal ports")
    return CheckResult(
        "S9", STATUS_PASS, "no docker-proxy on 0.0.0.0 for module-internal ports (internal services loopback-bound)"
    )


# endregion FUNC_check_listening_ports


# region FUNC_run_all_checks
## @purpose  Прогон всех 9 проверок (S1-S9).
## @io       ⇥ — → ⎋ list[CheckResult]
## @complexity O(9) — константное число проверок
def run_all_checks() -> list[CheckResult]:
    """Run all S1-S9 posture checks."""
    return [
        check_unattended_upgrades(),
        check_pending_security_updates(),
        check_ufw(),
        check_sshd(),
        check_docker(),
        check_file_perms(),
        check_forced_command(),
        check_image_freshness(),
        check_listening_ports(),
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
## @purpose  CLI: security_posture.py [--node NAME] [--json] | [--apply-sshd]. Root fail-fast.
##           Check-режим: exit 0|1|2. Apply-режим (--apply-sshd, DevPlan 136 W3): exit 0|1.
## @io       ⇥ argv → ⎋ int (exit code)
## @complexity O(9) — прогон всех проверок; apply — O(1) + reload
def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns exit code 0/1/2 — sys.exit handled by __main__."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Security posture check (DevPlan 134 L2)")
    parser.add_argument("--node", default="", help="Node name (informational, for report)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report (L5 monitoring)")
    parser.add_argument(
        "--apply-sshd",
        action="store_true",
        help="Apply sshd MaxStartups drop-in (DevPlan 136 W3) — bootstrap φ1; exit 0 ok / 1 error",
    )
    args = parser.parse_args(argv)

    if os.geteuid() != 0:
        print(
            "[FAIL] security_posture must run as root (sshd -T / --apply-sshd require root) — exit 2", file=sys.stderr
        )
        return 2

    if args.apply_sshd:
        # Apply-режим (DevPlan 136 W3): идемпотентный drop-in + reload при изменении.
        # НЕ check-режим — exit 0 = применено/no-op, 1 = ошибка (канон security_updates.py).
        if apply_sshd_dropin():
            return 0
        print("[FAIL] sshd MaxStartups drop-in apply failed (see logs)", file=sys.stderr)
        return 1

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
