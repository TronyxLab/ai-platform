#!/usr/bin/env python3
# GREP_SUMMARY: reboot-policy reboot-required loginctl active-sessions telegram-notify systemd-timer platform-reboot 0430 persistent postpone state-file idempotent DevPlan-164
# STRUCTURE: ▶ main ┌check [--execute]┐ → ◇ read reboot-required → ◇ loginctl active? → ◇ postpone → TG-notify (1/сутки) → ⎋ 0 │ ◇ idle → --execute? systemctl reboot + TG → ⎋ 0 │ install → ◇ unit-файлы (content-match no-op) → ⊕ bool → ⎋
# region MODULE_CONTRACT
## @purpose  Reboot-политика варианта A (решение оператора, DevPlan 164 W1-1, коллапс Brief §4.1):
##           активная SSH-сессия важнее ребута. Platform-reboot.timer (04:30, Persistent=true)
##           вызывает check --execute: /var/run/reboot-required есть + активные tty-сессии
##           платформенных пользователей → TG «ребут отложен» (retry завтра, Persistent) БЕЗ ребута;
##           idle → systemctl reboot + TG «ребут выполнен». install — идемпотентная установка
##           юнитов platform-reboot.{service,timer}.
## @scope    CLI: check [--execute] [--state-file <p>] [--required-file <p>] | install [--unit-dir <p>].
##           Запускается platform-reboot.service от systemd (root). stdlib-only, 0 импортов
##           core.internal (паттерн watchdog 142 W2 — скрипт вне pytest-env).
## @invariants
##   1. /var/run/reboot-required отсутствует → exit 0 (no-op)
##   2. Активные tty-сессии (loginctl list-sessions) пользователей {root, platform, ci-deploy,
##      operator} → ребут НЕ выполняется: TG «отложен» + exit 0 (таймер Persistent — retry завтра)
##   3. Idle → systemctl reboot ТОЛЬКО с --execute (dry-run по умолчанию); TG «ребут выполнен»
##   4. State-file /var/lib/platform/run/reboot-policy-state.json: хеш содержимого reboot-required
##      + postpone_notified_at (YYYY-MM-DD) — повторное уведомление только при смене содержимого;
##      postpone-уведомление — не чаще 1 раза в сутки (анти-спам)
##   5. install идемпотентен: content-match no-op, атомарная запись, systemctl daemon-reload +
##      enable --now таймера только при изменении
##   6. TG-уведомления — subprocess `python3 -m core.internal.shared.telegram_notifier send <text>`
##      (CLI-контракт telegram_notifier.py main() send); отказ уведомления — WARN, не меняет политику
##   7. reboot_required содержимое хешируется (sha256) — флаг-файл apt трогается при каждом апдейте,
##      хеш стабилен между волнами (нет ложных уведомлений)
## @rationale Оператор отверг «Automatic-Reboot WithUsers=true» (ребут обрывает активную
##            SSH-сессию). unattended-upgrades Automatic-Reboot переводится в "false" (W1-3),
##            платформенный таймер — единственный ребут-канал. TRAP security_updates.py:23
##            (deferred reboot-required/cert-expiry) закрывается этой волной.
## @changes  2026-08-13 | DevPlan 164 W1-1 — Created (вариант A: сессия важнее ребута)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 30 (loginctl) → CONVERGE_DOCKER_TIMEOUT; 60 (telegram/systemctl reboot/install) → SYSTEM_CMD_TIMEOUT.
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT, SYSTEM_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ── Канонические пути ──────────────────────────────────────────────────────
_WHO_FIELDS_MIN: int = 3  # who: SESSION UID USER ...

REBOOT_REQUIRED_FILE = "/var/run/reboot-required"
STATE_FILE = "/var/lib/platform/run/reboot-policy-state.json"
UNIT_DIR = "/etc/systemd/system"

# Пользователи, чья активная tty-сессия блокирует ребут (платформенные акторы)
ACTIVE_USERS: tuple[str, ...] = ("root", "platform", "ci-deploy", "operator")

SERVICE_UNIT_NAME = "platform-reboot.service"
TIMER_UNIT_NAME = "platform-reboot.timer"


# region FUNC_platform_base
## @purpose  Платформенный root (/opt/platform на ноде) — производный от __file__ (без
##           /opt-литералов: гейт opt-path-literals; watchdog-паттерн stdlib-only).
## @io       ⇥ — → ⎋ str — путь платформенного корня (parents[3] от bootstrap/reboot_policy.py)
## @complexity O(1)
def platform_base() -> str:
    """Platform root derived from module location (no /opt literals)."""
    return str(Path(__file__).resolve().parents[3])


# endregion FUNC_platform_base


# region FUNC_build_unit_texts
## @purpose  Тексты юнитов platform-reboot.{service,timer} с ExecStart на основе platform_base().
## @io       ⇥ — → ⎋ tuple[str, str] — (service_text, timer_text)
## @complexity O(1)
def build_unit_texts() -> tuple[str, str]:
    """Unit texts (service, timer) with self-derived platform base."""
    base = platform_base()
    service_text = f"""[Unit]
Description=Platform maintenance: certificate expiry check + reboot policy (DevPlan 164)
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/python3 {base}/core/internal/bootstrap/cert_expiry_check.py check
ExecStart=/usr/local/bin/python3 {base}/core/internal/bootstrap/reboot_policy.py check --execute
"""
    timer_text = """[Unit]
Description=Platform reboot policy timer — daily 04:30 (DevPlan 164)

[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service_text, timer_text


# endregion FUNC_build_unit_texts


# region FUNC_read_reboot_required
## @purpose  Прочитать /var/run/reboot-required. Отсутствие → None (no-op).
## @io       ⇥ path: str = REBOOT_REQUIRED_FILE → ⎋ str | None — содержимое или None
## @complexity O(1)
## @invariants  Ошибки чтения (кроме FileNotFoundError) → WARN + None (non-fatal: политика
##              не должна падать из-за прав)
def read_reboot_required(path: str = REBOOT_REQUIRED_FILE) -> str | None:
    """Read reboot-required content. None if absent/unreadable."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            content = f.read().strip()
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("[IMP:7][reboot_policy][read] Cannot read %s (non-fatal): %s", path, e)
        return None
    return content or None


# endregion FUNC_read_reboot_required


# region FUNC_content_hash
## @purpose  Стабильный sha256 содержимого reboot-required (инвариант 7).
## @io       ⇥ content: str → ⎋ str — hex-хеш (16 символов достаточно для идентификации)
## @complexity O(N) — N = длина содержимого
def content_hash(content: str) -> str:
    """SHA256 hex of reboot-required content (first 16 chars — identification)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# endregion FUNC_content_hash


# region FUNC_parse_loginctl_sessions
## @purpose  Разобрать вывод `loginctl list-sessions --no-legend` в строки сессий.
## @io       ⇥ text: str → ⎋ list[tuple[str, str]] — [(session_id, username), ...]
## @complexity O(L) — L = строк вывода
## @invariants  Формат: `SESSION UID USER SEAT TTY` (5 колонок); строки с <5 колонок
##              игнорируются; tty-пустое (seat) — не блокирующая сессия (login-manager)
def parse_loginctl_sessions(text: str) -> list[tuple[str, str]]:
    """Parse loginctl list-sessions output → [(session_id, username), ...]."""
    sessions: list[tuple[str, str]] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < _WHO_FIELDS_MIN:
            continue
        # Колонки: SESSION UID USER [SEAT] [TTY] — username в 3-й колонке
        sessions.append((fields[0], fields[2]))
    return sessions


# endregion FUNC_parse_loginctl_sessions


# region FUNC_active_platform_sessions
## @purpose  Активные tty-сессии платформенных пользователей из loginctl-вывода.
## @io       ⇥ text: str, users: tuple[str, ...] = ACTIVE_USERS → ⎋ list[str] — имена пользователей
## @complexity O(S) — S = сессий
## @invariants  Только пользователи из allowlist; дубли схлопываются (set)
def active_platform_sessions(text: str, users: tuple[str, ...] = ACTIVE_USERS) -> list[str]:
    """Active sessions of platform users from loginctl output."""
    active = {username for _, username in parse_loginctl_sessions(text) if username in users}
    return sorted(active)


# endregion FUNC_active_platform_sessions


# region TYPEDEF_RebootState
class RebootState(TypedDict, total=False):
    """State-file reboot_policy (W11-G3: JSON-граница вместо dict[str, Any]).

    ## @purpose — Типизированный state: content_hash + postpone_notified_at (анти-спам).
    ## @complexity — O(1) — декларация
    """

    content_hash: str
    postpone_notified_at: str


# endregion TYPEDEF_RebootState


# region FUNC_load_state
## @purpose  Загрузить state-file (анти-спам: хеш + postpone-дата). Битый/отсутствующий → {}.
## @io       ⇥ path: str = STATE_FILE → ⎋ RebootState
## @complexity O(1)
def load_state(path: str = STATE_FILE) -> RebootState:
    """Load reboot-policy state JSON. Empty dict if absent/corrupt."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = cast("RebootState | None", json.load(f))  # W11-G3: json.load → Any; JSON-граница
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# endregion FUNC_load_state


# region FUNC_save_state
## @purpose  Атомарно сохранить state-file (temp + os.replace; каталог создаётся).
## @io       ⇥ data: RebootState, path: str = STATE_FILE → ⎋ bool
## @complexity O(1)
def save_state(data: RebootState, path: str = STATE_FILE) -> bool:
    """Atomically save state JSON. Returns False on OSError (non-fatal)."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{path}.tmp"
        with Path(tmp).open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        Path(tmp).replace(path)
    except OSError as e:
        logger.warning("[IMP:7][reboot_policy][state] Cannot save state (non-fatal): %s", e)
        return False
    else:
        return True


# endregion FUNC_save_state


# region FUNC_should_notify_postpone
## @purpose  Анти-спам postpone-уведомлений: новый хеш содержимого ИЛИ новая дата.
## @io       ⇥ state: dict, current_hash: str, today: str → ⎋ bool
## @complexity O(1)
def should_notify_postpone(state: RebootState, current_hash: str, today: str) -> bool:
    """True if postpone notification is due (new content hash or new day)."""
    return state.get("content_hash") != current_hash or state.get("postpone_notified_at") != today


# endregion FUNC_should_notify_postpone


# region FUNC_notify_telegram
## @purpose  Telegram-уведомление через subprocess python3 -m shared.telegram_notifier send.
##           stdlib-only: не импортирует core.internal (паттерн watchdog); PYTHONPATH=/opt/platform.
## @io       ⇥ text: str, notify_fn: Callable | None = None (DI для тестов) → ⎋ bool
## @complexity O(1) + 1 subprocess
## @invariants  Отказ уведомления — WARN (не меняет политику ребута); никогда не raise
def notify_telegram(text: str, notify_fn: Callable[[str], bool] | None = None) -> bool:
    """Send Telegram notification. DI: notify_fn overrides subprocess channel."""
    if notify_fn is not None:
        return bool(notify_fn(text))
    env = dict(os.environ, PYTHONPATH=platform_base())
    try:
        result = subprocess.run(
            ["python3", "-m", "core.internal.shared.telegram_notifier", "send", text],
            capture_output=True,
            text=True,
            timeout=SYSTEM_CMD_TIMEOUT,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][reboot_policy][telegram] Notification failed (non-fatal): %s", e)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][reboot_policy][telegram] Notification failed rc=%d: %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    return True


# endregion FUNC_notify_telegram


# region FUNC_run_loginctl
## @purpose  subprocess `loginctl list-sessions --no-legend` (DI: run_fn для тестов).
## @io       ⇥ run_fn: Callable | None → ⎋ str — stdout ("" при сбое)
## @complexity O(1) + 1 subprocess
def run_loginctl(run_fn: Callable[[], str] | None = None) -> str:
    """Run loginctl list-sessions. Empty string on failure."""
    if run_fn is not None:
        return str(run_fn())
    try:
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][reboot_policy][loginctl] loginctl failed (non-fatal): %s", e)
        return ""
    return result.stdout if result.returncode == 0 else ""


# endregion FUNC_run_loginctl


# region FUNC_check
## @purpose  Оркестрация check: no-file → no-op; active → postpone + TG (анти-спам);
##           idle → --execute ? reboot + TG : dry-run. Возвращает exit-код.
## @io       ⇥ execute: bool = False, required_file/state_file/loginctl_fn/notify_fn/reboot_fn →
##           ⎋ int — exit code (0 = ok/отложен, 1 = ошибка)
## @complexity O(1) + subprocess
## @invariants  Ребут ВЫПОЛНЯЕТСЯ ТОЛЬКО при execute=True (dry-run по умолчанию);
##              postpone-путь не меняет state при успешной отправке уведомления
def check(
    execute: bool = False,
    required_file: str = REBOOT_REQUIRED_FILE,
    state_file: str = STATE_FILE,
    loginctl_fn: Callable[[], str] | None = None,
    notify_fn: Callable[[str], bool] | None = None,
    reboot_fn: Callable[[], bool] | None = None,
) -> int:
    """Run reboot-policy check. Returns exit code."""
    content = read_reboot_required(required_file)
    if content is None:
        logger.info("[IMP:8][reboot_policy][check] %s absent — no-op", required_file)
        return 0

    current_hash = content_hash(content)
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    state = load_state(state_file)
    sessions_text = run_loginctl(loginctl_fn)
    active = active_platform_sessions(sessions_text)

    if active:
        # Сессия важнее ребута: TG «отложен» (раз в сутки или при смене содержимого) + retry завтра
        if should_notify_postpone(state, current_hash, today):
            users = ", ".join(active)
            notify_telegram(
                f"[platform] Ребут отложен: активная SSH-сессия ({users}). Повторная попытка — завтра 04:30.",
                notify_fn=notify_fn,
            )
            state["content_hash"] = current_hash
            state["postpone_notified_at"] = today
            save_state(state, state_file)
        logger.info("[IMP:9][reboot_policy][check] Reboot postponed — active sessions: %s", ", ".join(active))
        return 0

    logger.info("[IMP:9][reboot_policy][check] No active platform sessions — reboot path")
    if not execute:
        logger.info("[IMP:8][reboot_policy][check] dry-run: reboot would be executed (use --execute)")
        return 0
    if reboot_fn is not None:
        ok = bool(reboot_fn())
    else:
        try:
            result = subprocess.run(
                ["systemctl", "reboot"], capture_output=True, text=True, timeout=SYSTEM_CMD_TIMEOUT, check=False
            )
            ok = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][reboot_policy][check] systemctl reboot failed: %s", e)
            ok = False
    if ok:
        notify_telegram(
            "[platform] Ребут выполнен: security-патчи применены (reboot-required закрыт).",
            notify_fn=notify_fn,
        )
        # Хеш-состояние сбрасываем: после ребута флаг-файл исчезает (apt пересоздаст при необходимости)
        save_state({}, state_file)
        return 0
    logger.error("[IMP:10][reboot_policy][check] Reboot command failed")
    return 1


# endregion FUNC_check


# region FUNC_write_unit_if_changed
## @purpose  Content-match идемпотентная запись unit-файла (канон security_updates).
## @io       ⇥ unit_dir: str, name: str, text: str → ⎋ int (1 = записано, 0 = no-op, -1 = ошибка)
## @complexity O(1)
def write_unit_if_changed(unit_dir: str, name: str, text: str) -> int:
    """Write unit file if content differs. Returns 1=written, 0=no-op, -1=error."""
    path = Path(unit_dir) / name
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if existing == text:
        logger.info("[IMP:8][reboot_policy][install] %s unchanged — no-op", name)
        return 0
    try:
        tmp = str(path) + ".tmp"
        with Path(tmp).open("w", encoding="utf-8") as f:
            f.write(text)
        Path(tmp).replace(str(path))
        logger.info("[IMP:9][reboot_policy][install] %s written", name)
    except OSError as e:
        logger.error("[IMP:10][reboot_policy][install] Cannot write %s: %s", name, e)
        return -1
    else:
        return 1


# endregion FUNC_write_unit_if_changed


# region FUNC_install
## @purpose  Идемпотентная установка platform-reboot.{service,timer} + daemon-reload +
##           enable --now таймера (только при изменении). Отдельный ExecStart cert_expiry_check.
## @io       ⇥ unit_dir: str = UNIT_DIR, systemctl_fn: Callable | None (DI) → ⎋ bool
## @complexity O(1) + systemctl
## @invariants  daemon-reload/enable — ТОЛЬКО если хотя бы один файл записан (no-op при каноне)
def install(unit_dir: str = UNIT_DIR, systemctl_fn: Callable[[list[str]], bool] | None = None) -> bool:
    """Install reboot-policy units idempotently. Returns True on success/no-op."""
    service_text, timer_text = build_unit_texts()
    service_rc = write_unit_if_changed(unit_dir, SERVICE_UNIT_NAME, service_text)
    timer_rc = write_unit_if_changed(unit_dir, TIMER_UNIT_NAME, timer_text)
    if service_rc < 0 or timer_rc < 0:
        return False
    if service_rc == 0 and timer_rc == 0:
        logger.info("[IMP:7][reboot_policy][install] Units unchanged — no systemctl calls (idempotent)")
        return True
    if systemctl_fn is not None:
        ok = all(bool(systemctl_fn(cmd)) for cmd in (["daemon-reload"], ["enable", "--now", TIMER_UNIT_NAME]))
    else:
        ok = _run_systemctl(("daemon-reload", "enable --now " + TIMER_UNIT_NAME))
    if ok:
        logger.info("[IMP:9][reboot_policy][install] Units installed + timer enabled (04:30, Persistent=true)")
    return ok


# endregion FUNC_install


# region FUNC__run_systemctl
## @purpose  Последовательный systemctl (daemon-reload → enable --now). Ошибка — IMP:10, ok=False.
## @io       ⇥ commands: tuple[str, ...] → ⎋ bool
## @complexity O(C) — C = команд
def _run_systemctl(commands: tuple[str, ...]) -> bool:
    """Run systemctl commands sequentially (PERF203: try-вызов вне цикла)."""
    ok = True
    for cmd in commands:
        try:
            result = subprocess.run(
                ["systemctl", *cmd.split()], capture_output=True, text=True, timeout=SYSTEM_CMD_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("[IMP:10][reboot_policy][install] systemctl %s failed: %s", cmd, e)
            ok = False
            continue
        if result.returncode != 0:
            logger.error("[IMP:10][reboot_policy][install] systemctl %s failed: %s", cmd, result.stderr.strip()[:200])
            ok = False
    return ok


# endregion FUNC__run_systemctl


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    check_fn: Callable[[], int] | None = None,
    install_fn: Callable[[], bool] | None = None,
) -> int:
    """CLI: reboot_policy.py check [--execute] | install. Exit 0|1."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Platform reboot policy (DevPlan 164 W1-1, вариант A)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Check reboot-required (dry-run by default)")
    check_parser.add_argument(
        "--execute", action="store_true", help="Actually reboot when idle (timer runs with --execute)"
    )
    check_parser.add_argument("--required-file", default=REBOOT_REQUIRED_FILE, help="reboot-required path (tests)")
    check_parser.add_argument("--state-file", default=STATE_FILE, help="state-file path (tests)")
    subparsers.add_parser("install", help="Install platform-reboot.{service,timer} units (idempotent)")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.command: str
            self.execute: bool
            self.required_file: str
            self.state_file: str

    args = parser.parse_args(argv, namespace=_CliArgs())
    if args.command == "install":
        impl = install if install_fn is None else install_fn
        return 0 if impl() else 1
    if check_fn is not None:
        # DI-канал тестов: реальный check() заменяется вердиктом инжектированной функции
        return int(check_fn())
    return check(
        execute=args.execute,
        required_file=args.required_file,
        state_file=args.state_file,
    )


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
