#!/usr/bin/env python3
# GREP_SUMMARY: cert-expiry acme openssl-enddate days-left threshold telegram-notify state-file hash idempotent DevPlan-164
# STRUCTURE: ▶ main check ┌--cert-dir --threshold-days┐ → ◇ scan <domain>/fullchain.cer → ◇ openssl x509 -enddate → ◇ days<14? → TG-notify (при смене списка) → ⊕ bool → ⎋
# region MODULE_CONTRACT
## @purpose  Проверка истечения TLS-сертификатов (закрытие deferred-TRAP 162 W6-2):
##           ежедневная проверка acme.sh-каталога — домены, чьи сертификаты истекают
##           раньше порога (14 дней), уведомляются в Telegram. stdlib-only (паттерн watchdog),
##           вызывается platform-reboot.service (первый ExecStart, до reboot_policy).
## @scope    CLI: check [--cert-dir <d>] [--threshold-days 14] [--state-file <p>].
##           Запускается systemd от root. Без импортов core.internal.
## @invariants
##   1. Read-only: НИЧЕГО не пишет в cert-каталог (только читает .cer и state-file)
##   2. Скан: подкаталоги <cert-dir>/<domain>*/ с fullchain.cer (acme.sh layout: <domain>[_ecc]);
##      имя домена — имя подкаталога без _ecc-суффикса
##   3. `openssl x509 -enddate -noout` → notAfter → days_left = ceil((enddate - now)/86400)
##   4. days_left < threshold → в отчёт; отчёт пуст → no-op exit 0
##   5. Анти-спам: state-file хеш отчёта (sha256) — уведомление только при смене списка/порога
##   6. openssl недоступен/ошибка парсинга — WARN, сертификат пропускается (не блокирует стек)
##   7. TG — тот же subprocess-канал, что reboot_policy (telegram_notifier send)
## @rationale Истечение сертификата = полный отказ HTTPS (аудит 162 Failure Matrix #23
##            «Certificate expired — GAP»). acme.sh renew покрывает обновление, но не алертинг
##            при сбое renewal (webnames API, rate-limit). Порог 14 дней = неделя на реакцию
##            + неделя на retry.
## @changes  2026-08-13 | DevPlan 164 W1-2 — Created (закрытие deferred-TRAP security_updates.py:23)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 30 (openssl x509 -enddate) → CONVERGE_DOCKER_TIMEOUT; 60 (python3 telegram) → SYSTEM_CMD_TIMEOUT.
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT, SYSTEM_CMD_TIMEOUT

logger = logging.getLogger(__name__)

ACME_CERT_DIR = "/root/.acme.sh"
# 🧐 TRAP[DECISION] · 2026-08-14 · — · Литерал state-файла сохранён (не deploy_paths.cert_expiry_state_file)
# · Rejected: импорт core.internal.shared.deploy_paths (резолвер существует — 170 W1-A2)
# · Reason: stdlib-only канон модуля (MODULE_CONTRACT: «Без импортов core.internal») —
# ·   platform-reboot.service (reboot_policy.build_unit_texts) вызывает скрипт БЕЗ PYTHONPATH;
# ·   импорт упал бы ModuleNotFoundError (регрессия-класс watchdog 142 W2, P1).
# ·   allowlist гейта test_gate_run_paths_sole.py.
# · Rev: systemd-юнит начнёт задавать PYTHONPATH (или env-инъекцию пути) — заменить на резолвер
STATE_FILE = "/var/lib/platform/run/cert-expiry-state.json"
DEFAULT_THRESHOLD_DAYS = 14

# acme.sh layout: <dir>/<domain>/<domain>.cer и <dir>/<domain>_ecc/fullchain.cer (ECC)
CERT_FILENAMES: tuple[str, ...] = ("fullchain.cer",)
_ENDDATE_RE = re.compile(r"notAfter=([A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+GMT")


# region FUNC_parse_enddate
## @purpose  Разобрать строку `notAfter=Sep 11 12:00:00 2026 GMT` в datetime (UTC-aware).
## @io       ⇥ line: str → ⎋ datetime | None (None = непарсится)
## @complexity O(1)
def parse_enddate(line: str) -> datetime | None:
    """Parse openssl -enddate line → UTC datetime. None if unparseable."""
    m = _ENDDATE_RE.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} +0000", "%b %d %H:%M:%S %Y %z")
    except ValueError:
        return None


# endregion FUNC_parse_enddate


# region FUNC_days_left
## @purpose  Дней до истечения (ceil). Отрицательное = истёк.
## @io       ⇥ enddate: datetime, now: datetime | None → ⎋ int
## @complexity O(1)
def days_left(enddate: datetime, now: datetime | None = None) -> int:
    """Whole days until expiry (ceil; negative = expired)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    delta = enddate - ref
    # ceil для положительного, floor для отрицательного (единый int; total_seconds() → float)
    return int(-(-delta.total_seconds() // 86400)) if delta.total_seconds() > 0 else int(delta.total_seconds() // 86400)


# endregion FUNC_days_left


# region FUNC_domain_from_dir
## @purpose  Имя домена из имени подкаталога acme.sh (`<domain>[_ecc]` → domain).
## @io       ⇥ dirname: str → ⎋ str
## @complexity O(1)
def domain_from_dir(dirname: str) -> str:
    """acme.sh dir name → domain (strip _ecc suffix)."""
    return dirname.removesuffix("_ecc")


# endregion FUNC_domain_from_dir


# region FUNC_read_enddate
## @purpose  openssl x509 -enddate для файла (subprocess; DI run_fn для тестов).
## @io       ⇥ cert_path: str, run_fn: Callable | None → ⎋ datetime | None
## @complexity O(1) + 1 subprocess
## @invariants  Ошибки (нет openssl/файла) → WARN + None — сертификат пропускается
def read_enddate(cert_path: str, run_fn: Callable[[list[str]], object] | None = None) -> datetime | None:
    """Get cert expiry via openssl. None on any failure (non-fatal)."""
    cmd = ["openssl", "x509", "-enddate", "-noout", "-in", cert_path]
    if run_fn is not None:
        result = run_fn(cmd)
        output = getattr(result, "stdout", "") if not isinstance(result, str) else result
        rc = getattr(result, "returncode", 0) if not isinstance(result, str) else 0
    else:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=CONVERGE_DOCKER_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("[IMP:7][cert_expiry][openssl] openssl failed (non-fatal): %s", e)
            return None
        output = result.stdout
        rc = result.returncode
    if rc != 0:
        logger.warning("[IMP:7][cert_expiry][openssl] openssl rc=%d for %s (non-fatal)", rc, cert_path)
        return None
    enddate = parse_enddate(output)
    if enddate is None:
        logger.warning("[IMP:7][cert_expiry][openssl] Unparseable enddate for %s: %r", cert_path, output.strip()[:80])
    return enddate


# endregion FUNC_read_enddate


# region FUNC_scan_expiring
## @purpose  Скан cert-каталога: домены с days_left < threshold → {domain: days_left}.
## @io       ⇥ cert_dir: str, threshold_days: int, now: datetime | None, run_fn → ⎋ dict[str, int]
## @complexity O(D) — D = подкаталогов
## @invariants  Только подкаталоги с fullchain.cer; сортировка по домену (детерминизм хеша)
def scan_expiring(
    cert_dir: str,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
    now: datetime | None = None,
    run_fn: Callable[[list[str]], object] | None = None,
) -> dict[str, int]:
    """Scan acme.sh dir for expiring certs → {domain: days_left}."""
    expiring: dict[str, int] = {}
    base = Path(cert_dir)
    try:
        entries = [e for e in base.iterdir() if e.is_dir()]
    except OSError as e:
        logger.warning("[IMP:7][cert_expiry][scan] Cannot list %s (non-fatal): %s", cert_dir, e)
        return {}
    for entry in sorted(entries, key=lambda p: p.name):
        cert_path: Path | None = None
        for name in CERT_FILENAMES:
            candidate = entry / name
            if candidate.is_file():
                cert_path = candidate
                break
        if cert_path is None:
            continue
        enddate = read_enddate(str(cert_path), run_fn=run_fn)
        if enddate is None:
            continue
        left = days_left(enddate, now)
        if left < threshold_days:
            expiring[domain_from_dir(entry.name)] = left
    return dict(sorted(expiring.items()))


# endregion FUNC_scan_expiring


# region FUNC_report_hash
## @purpose  Стабильный хеш отчёта для анти-спам state-file.
## @io       ⇥ expiring: dict[str, int], threshold_days: int → ⎋ str
## @complexity O(D)
def report_hash(expiring: dict[str, int], threshold_days: int = DEFAULT_THRESHOLD_DAYS) -> str:
    """SHA256 of sorted report (domains+days+threshold)."""
    payload = json.dumps({"threshold": threshold_days, "expiring": expiring}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# endregion FUNC_report_hash


# region FUNC_build_report_text
## @purpose  Человекочитаемый отчёт для TG.
## @io       ⇥ expiring: dict[str, int] → ⎋ str
## @complexity O(D)
def build_report_text(expiring: dict[str, int]) -> str:
    """TG report text from expiring dict."""
    lines = [f"⚠️ Сертификат {domain} истекает через {days} дн." for domain, days in expiring.items()]
    return "[platform] " + "\n".join(lines) if lines else ""


# endregion FUNC_build_report_text


# region TYPEDEF_CertExpiryState
class CertExpiryState(TypedDict, total=False):
    """State-file cert_expiry_check (W11-G3: JSON-граница вместо dict[str, Any]).

    ## @purpose — report_hash (анти-спам: один отчёт в сутки).
    ## @complexity — O(1) — декларация
    """

    report_hash: str


# endregion TYPEDEF_CertExpiryState


# region FUNC_load_state
## @purpose  Загрузить state-file (хеш отчёта). Битый/отсутствующий → {}.
## @io       ⇥ path: str → ⎋ CertExpiryState
## @complexity O(1)
def load_state(path: str = STATE_FILE) -> CertExpiryState:
    """Load cert-expiry state JSON. Empty dict if absent/corrupt."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = cast("CertExpiryState | None", json.load(f))  # W11-G3: json.load → Any; JSON-граница
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


# endregion FUNC_load_state


# region FUNC_save_state
## @purpose  Атомарно сохранить state-file.
## @io       ⇥ data: CertExpiryState, path: str → ⎋ bool
## @complexity O(1)
def save_state(data: CertExpiryState, path: str = STATE_FILE) -> bool:
    """Atomically save state JSON. Returns False on OSError (non-fatal)."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{path}.tmp"
        with Path(tmp).open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        Path(tmp).replace(path)
    except OSError as e:
        logger.warning("[IMP:7][cert_expiry][state] Cannot save state (non-fatal): %s", e)
        return False
    else:
        return True


# endregion FUNC_save_state


# region FUNC_notify_telegram
## @purpose  Telegram через subprocess telegram_notifier send (тот же канал, что reboot_policy).
## @io       ⇥ text: str, notify_fn | None → ⎋ bool
## @complexity O(1) + 1 subprocess
def notify_telegram(text: str, notify_fn: Callable[[str], bool] | None = None) -> bool:
    """Send Telegram notification. DI: notify_fn overrides subprocess channel."""
    if notify_fn is not None:
        return bool(notify_fn(text))
    # Платформенный root — от __file__ (без /opt-литералов, гейт opt-path-literals)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]))
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
        logger.warning("[IMP:7][cert_expiry][telegram] Notification failed (non-fatal): %s", e)
        return False
    if result.returncode != 0:
        logger.warning(
            "[IMP:7][cert_expiry][telegram] Notification failed rc=%d: %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    return True


# endregion FUNC_notify_telegram


# region FUNC_check
## @purpose  Оркестрация: скан → пустой отчёт = no-op; иначе TG при новом хеше → exit 0;
##           ошибки скана не блокируют (exit 0 — best-effort ежедневная проверка).
## @io       ⇥ cert_dir, threshold_days, state_file, now, run_fn, notify_fn → ⎋ int
## @complexity O(D) + 1 subprocess per cert
def check(
    cert_dir: str = ACME_CERT_DIR,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
    state_file: str = STATE_FILE,
    now: datetime | None = None,
    run_fn: Callable[[list[str]], object] | None = None,
    notify_fn: Callable[[str], bool] | None = None,
) -> int:
    """Run cert-expiry check. Returns exit code (0 = ok/notified, 1 = report failed)."""
    expiring = scan_expiring(cert_dir, threshold_days, now=now, run_fn=run_fn)
    if not expiring:
        logger.info("[IMP:8][cert_expiry][check] No expiring certs (threshold=%dd) — no-op", threshold_days)
        return 0
    text = build_report_text(expiring)
    current_hash = report_hash(expiring, threshold_days)
    state = load_state(state_file)
    if state.get("report_hash") == current_hash:
        logger.info("[IMP:8][cert_expiry][check] Report unchanged — notification suppressed (anti-spam)")
        return 0
    logger.info("[IMP:9][cert_expiry][check] Expiring certs: %s", ", ".join(expiring))
    if notify_telegram(text, notify_fn=notify_fn):
        save_state({"report_hash": current_hash}, state_file)
        return 0
    logger.error("[IMP:10][cert_expiry][check] Notification failed — report not delivered")
    return 1


# endregion FUNC_check


# region FUNC_main
def main(argv: list[str] | None = None, *, check_fn: Callable[[], int] | None = None) -> int:
    """CLI: cert_expiry_check.py check [--cert-dir] [--threshold-days] [--state-file]. Exit 0|1."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Certificate expiry check (DevPlan 164 W1-2)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Check cert expiry (daily, anti-spam)")
    check_parser.add_argument("--cert-dir", default=ACME_CERT_DIR, help="acme.sh cert dir")
    check_parser.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS, help="Alert threshold")
    check_parser.add_argument("--state-file", default=STATE_FILE, help="state-file path")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.command: str
            self.cert_dir: str
            self.threshold_days: int
            self.state_file: str

    args = parser.parse_args(argv, namespace=_CliArgs())
    if check_fn is not None:
        return int(check_fn())
    return check(cert_dir=args.cert_dir, threshold_days=args.threshold_days, state_file=args.state_file)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
