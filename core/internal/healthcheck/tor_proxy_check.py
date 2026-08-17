#!/usr/bin/env python3
# GREP_SUMMARY: tor-proxy-healthcheck telegram getMe telegram_notifier shared-module proxy monitoring 3-stage socks5 privoxy DI runner facts
# STRUCTURE: ▶ check_tor_socks (curl SOCKS5) → check_privoxy (curl --proxy) → check_telegram_api (getMe) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Healthcheck for Tor→Privoxy→Telegram proxy chain (3-stage); logs to audit log,
##           exits 0 on success. Python-порт tor-proxy-healthcheck.sh (DevPlan 118 E5).
## @scope    Runs via cron */5 * * * * from /etc/cron.d/tor-proxy-healthcheck; standalone use supported.
##           Вызывается из thin facade core/internal/healthcheck/tor-proxy-healthcheck.sh.
## @invariants
##   - Each stage exits 1 immediately on failure with descriptive message
##   - Stage 3 (Telegram getMe) requires decrypted secrets at SECRETS_ENV_FILE
##   - TELEGRAM_PROXY_URL defaults to http://127.0.0.1:8118 if unset
##   - Канон-таймауты из shared/timeouts (TOR_PROXY_CURL_TIMEOUT)
##   - Telegram getMe делегирован в shared/telegram_notifier.get_me (E5)
##   - Non-fatal per check: first failure causes immediate exit 1
##   - E1 (160): runner/facts DI-параметры (None = реальные вызовы; поведение без изменений)
## @rationale Automated monitoring ensures Tor+Privoxy chain stays operational; without healthcheck,
##            IP-blocking of api.telegram.org would go undetected. Strangler E5: 3-stage в Python.
## @changes  2026-08-02 | DevPlan 118 E5 — Created (Python-порт tor-proxy-healthcheck.sh, 121 LOC)
## @changes  2026-08-14 | DevPlan 170 W1-A3 — proxy-URL порт из SoT firewall.PRIVOXY_PORT
##           2026-08-13 | DevPlan 160 E1 — +runner/facts DI (curl subprocess + os.path.isfile)
##           2026-08-17 | DevPlan 005 — 003 A3 наросты (state-file канарейка + RED audit-entry)
##                      удалены; возврат к чистому 3-stage ядру DevPlan 118 (dead-man's switch removal)
## @see      core/internal/healthcheck/tor-proxy-healthcheck.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# DevPlan 170 W1-A3: приватный порт Privoxy из SoT firewall.py (литерал 8118 удалён)
from core.internal.bootstrap.firewall import PRIVOXY_PORT
from core.internal.shared import deploy_paths, telegram_notifier
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.subprocess_io import CommandRunner
from core.internal.shared.timeouts import TOR_PROXY_CURL_TIMEOUT

logger = logging.getLogger(__name__)

# Канон-URL для проверки Tor-цепи (check.torproject.org возвращает "Congratulations" / HTTP 200)
CHECK_URL = "https://check.torproject.org/"

# W11: DI-тип getMe-канала (E1) — контракт shared/telegram_notifier.get_me
GetMeFn = Callable[..., bool]


# region FUNC_curl_http_code
## @purpose  curl-проверка с таймаутом: вернуть HTTP-код (или None при ошибке).
## @io       ⇥ args: list[str] (curl flags), runner: CommandRunner | None → ⎋ str | None
## @complexity O(1) — один curl subprocess
## @changes 2026-08-13 | E1 (160): +runner DI — runner=None → subprocess.run (default),
##            runner задан → runner.run (fake scripted)
def curl_http_code(args: list[str], *, runner: CommandRunner | None = None) -> str | None:
    """Run curl with --max-time TOR_PROXY_CURL_TIMEOUT and return HTTP code (None on error)."""
    cmd = ["curl", "-s", "--max-time", str(TOR_PROXY_CURL_TIMEOUT), "-o", "/dev/null", "-w", "%{http_code}", *args]
    try:
        if runner is None:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TOR_PROXY_CURL_TIMEOUT + 5, check=False
            )
        else:
            result = runner.run(cmd, timeout=TOR_PROXY_CURL_TIMEOUT + 5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# endregion FUNC_curl_http_code


# region FUNC_check_tor_socks
## @purpose  Stage 1: проверить Tor SOCKS5 по check.torproject.org (HTTP 200).
## @io       ⇥ runner: CommandRunner | None → ⎋ bool
## @complexity O(1)
def check_tor_socks(*, runner: CommandRunner | None = None) -> bool:
    """Verify Tor SOCKS5 proxy at 127.0.0.1:9050 (HTTP 200 expected)."""
    code = curl_http_code(["--socks5-hostname", "127.0.0.1:9050", CHECK_URL], runner=runner)
    if code == "200":
        logger.info("[IMP:9][tor-hc][tor-socks] Tor SOCKS5: connected (HTTP 200)")
        return True
    logger.warning("[IMP:9][tor-hc][tor-socks] FAIL: Tor SOCKS5 connection failed (code=%s)", code)
    return False


# endregion FUNC_check_tor_socks


# region FUNC_check_privoxy
## @purpose  Stage 2: проверить Privoxy HTTP-прокси → Tor (HTTP 200 через --proxy).
## @io       ⇥ proxy_url: str, runner: CommandRunner | None → ⎋ bool
## @complexity O(1)
def check_privoxy(proxy_url: str, *, runner: CommandRunner | None = None) -> bool:
    """Verify Privoxy forward proxy → Tor (HTTP 200 expected)."""
    code = curl_http_code(["--proxy", proxy_url, CHECK_URL], runner=runner)
    if code == "200":
        logger.info("[IMP:9][tor-hc][privoxy] Privoxy → Tor forward: working (HTTP 200)")
        return True
    logger.warning("[IMP:9][tor-hc][privoxy] FAIL: Privoxy → Tor forward failed (code=%s)", code)
    return False


# endregion FUNC_check_privoxy


# region FUNC_check_telegram_api
## @purpose  Stage 3: проверить Telegram Bot API getMe через прокси-цепь (shared/telegram_notifier.get_me).
##           Secrets: /var/lib/platform/run/secrets.env (TELEGRAM_BOT_TOKEN). Отсутствие → SKIP (return True).
## @io       ⇥ secrets_file: str, proxy_url: str, facts: EnvironmentFacts | None,
##           get_me_fn (DI для telegram_notifier.get_me) → ⎋ bool
## @complexity O(1) — чтение secrets + 1 HTTP GET
## @changes 2026-08-13 | E1 (160): +facts/get_me_fn DI (os.path.isfile → facts; getMe → параметр)
def check_telegram_api(
    secrets_file: str,
    proxy_url: str,
    *,
    facts: EnvironmentFacts | None = None,
    get_me_fn: GetMeFn | None = None,
) -> bool:
    """Verify Telegram getMe via proxy chain (delegated to shared telegram_notifier.get_me)."""
    if not (facts or default_env_facts()).path_isfile(secrets_file):
        logger.info("[IMP:8][tor-hc][telegram-api] SKIP: secrets file not found at %s", secrets_file)
        return True
    token = _read_secret(secrets_file, "TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info("[IMP:8][tor-hc][telegram-api] SKIP: TELEGRAM_BOT_TOKEN not set in secrets")
        return True
    get_me = get_me_fn if get_me_fn is not None else telegram_notifier.get_me
    ok = get_me(bot_token=token, proxy_url=proxy_url)
    if ok:
        logger.info("[IMP:9][tor-hc][telegram-api] Telegram API reachable through proxy")
        return True
    logger.warning("[IMP:9][tor-hc][telegram-api] FAIL: Telegram getMe request failed")
    return False


# endregion FUNC_check_telegram_api


# region FUNC_read_secret
## @purpose  Прочитать KEY=VALUE секрет из secrets.env (комментарии/пустые строки пропускаются).
## @io       ⇥ secrets_file: str, key: str → ⎋ str
## @complexity O(N) — N = строк
def _read_secret(secrets_file: str, key: str) -> str:
    """Read a KEY=VALUE secret from secrets.env (comments/blank skipped)."""
    try:
        for raw_line in Path(secrets_file).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip()
    except OSError as exc:
        logger.warning("[IMP:7][tor-hc][secrets] Cannot read %s: %s", secrets_file, exc)
    return ""


# endregion FUNC_read_secret


# region FUNC_run_all
## @purpose  Полный 3-stage прогон (каждый stage — immediate exit 1 на failure).
##           Чистое ядро DevPlan 118: без state-file канарейки и audit-entry (DevPlan 005 —
##           dead-man's switch удалён; нода алертит через notify_event на watchdog).
## @io       ⇥ proxy_url: str, secrets_file: str, runner: CommandRunner | None,
##           facts: EnvironmentFacts | None, get_me_fn → ⎋ bool
## @complexity O(1) — 3 subprocess/HTTP операции
## @changes 2026-08-13 | E1 (160): +runner/facts/get_me_fn DI (проброс в стадии)
## @changes 2026-08-17 | DevPlan 005: −state_file/−audit_fn DI и −_write_chain_state/−_write_chain_audit
##           (003 A3 наросты удалены; возврат к чистому ядру)
def run_all(
    proxy_url: str,
    secrets_file: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    get_me_fn: GetMeFn | None = None,
) -> bool:
    """Run all 3 stages; first failure → False (immediate exit semantics)."""
    if not check_tor_socks(runner=runner):
        return False
    if not check_privoxy(proxy_url, runner=runner):
        return False
    if not check_telegram_api(secrets_file, proxy_url, facts=facts, get_me_fn=get_me_fn):
        return False
    logger.info("[IMP:9][tor-hc][main] All healthchecks PASSED")
    return True


# endregion FUNC_run_all


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.healthcheck.tor_proxy_check`.

    ▶ ┌env (TELEGRAM_PROXY_URL, SECRETS_ENV_FILE)┐ → ○ run_all → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "") or f"http://127.0.0.1:{PRIVOXY_PORT}"
    secrets_file = os.environ.get("SECRETS_ENV_FILE", "") or str(deploy_paths.secrets_env_file())
    return 0 if run_all(proxy_url, secrets_file) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
