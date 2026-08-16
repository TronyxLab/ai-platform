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
##           2026-08-16 | DevPlan 003 A3 — RED → audit-entry (tag=tor:chain_down) + state-file
##                      канарейка (/var/lib/platform/run/tor-chain-state.json, out-of-band)
## @see      core/internal/healthcheck/tor-proxy-healthcheck.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
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


# region FUNC__write_chain_state
## @purpose  State-file Tor-цепи (DevPlan 003 A3): при RED пишется
##           /var/lib/platform/run/tor-chain-state.json (persistent 142 W2) — out-of-band
##           канарейка, читаемая внешним чекером/оператором (нода НЕ может слать Telegram
##           при мёртвом Tor — прямая доставка запрещена, TRAP[BUG] 141: утечка IP).
##           Зелёный прогон пишет status=green (сброс канарейки).
## @io       ⇥ status: str, details: list[str], state_file: str | None (DI, тесты) → ⎋ bool
## @complexity O(1) — атомарная запись JSON
## @invariants
##   - Путь: env TOR_CHAIN_STATE_FILE > /var/lib/platform/run/tor-chain-state.json
##   - Atomic write: tempfile + os.replace (паттерн watchdog.save_state)
##   - Ошибка записи → WARN (non-fatal, healthcheck не роняет из-за state-файла)
##   - Body: {"status": "red"|"green", "ts": ISO8601, "stages": [...]}
# region FUNC__plw_body_chain_state
## @purpose  Тело try-блока (PLW0717 extraction из _write_chain_state) — семантика except
##           не меняется: atomic write tempfile + replace (паттерн watchdog.save_state).
## @io       ⇥ target: Path, payload: dict → ⎋ None
## @complexity O(1) — извлечение управляющего потока
def _plw_body_chain_state(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    tmp.replace(target)


# endregion FUNC__plw_body_chain_state


def _write_chain_state(status: str, details: list[str], state_file: str | None = None) -> bool:
    """Atomically write tor-chain state-file (red/green canary). Returns True on success."""
    from core.internal.shared.deploy_paths import tor_chain_state_file

    path = state_file or str(tor_chain_state_file())
    payload: dict[str, object] = {
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
        "stages": details,
    }
    try:
        _plw_body_chain_state(Path(path), payload)
    except OSError as e:
        logger.warning("[IMP:7][tor-hc][state] Cannot write %s (non-fatal): %s", path, e)
        return False
    logger.info("[IMP:9][tor-hc][state] Chain state written: %s status=%s", path, status)
    return True


# endregion FUNC__write_chain_state


# region FUNC__write_chain_audit
## @purpose  Audit-entry при RED (DevPlan 003 A3, D-2 реконструируемость): канарейка
##           не молчит — провал Tor-цепи фиксируется в едином audit-логе.
##           Best-effort: сбой аудита → WARN (не маскирует основной RED-вердикт).
## @io       ⇥ stage: str (деталь провала), audit_fn: Callable | None (DI) → ⎋ None
## @complexity O(1) — одна JSON-lines запись
def _write_chain_audit(stage: str, audit_fn: Callable[..., None] | None) -> None:
    """Write audit entry for the RED chain (tag=tor:chain_down). Non-fatal."""
    try:
        if audit_fn is not None:
            audit_fn(stage)
            return
        from core.internal.shared.audit_logger import write_audit_entry  # лениво

        write_audit_entry(
            tag="tor:chain_down",
            status="ERROR",
            message=f"Tor→Privoxy→Telegram chain RED: {stage}",
            operation="tor-proxy-check",
        )
    # ruff: ignore[BLE001] — best-effort audit: не маскирует RED-вердикт (003 A3)
    except Exception as e:  # noqa: EXC — best-effort audit-fallback (DevPlan 003 A3)
        logger.warning("[IMP:7][tor-hc][audit] Audit entry failed (non-fatal): %s", e)


# endregion FUNC__write_chain_audit


# region FUNC_run_all
## @purpose  Полный 3-stage прогон (каждый stage — immediate exit 1 на failure).
##           DevPlan 003 A3: при RED — audit-entry (write_audit_entry tag="tor:chain_down")
##           + state-file (out-of-band канарейка). Зелёный прогон сбрасывает state-file.
## @io       ⇥ proxy_url: str, secrets_file: str, runner: CommandRunner | None,
##           facts: EnvironmentFacts | None, get_me_fn, state_file: str | None (DI),
##           audit_fn: Callable | None (DI) → ⎋ bool
## @complexity O(1) — 3 subprocess/HTTP операции
## @changes 2026-08-13 | E1 (160): +runner/facts/get_me_fn DI (проброс в стадии)
## @changes 2026-08-16 | DevPlan 003 A3: +state-file +audit-entry при RED (канарейка
##           перестаёт молчать; прямая доставка из ноды невозможна — Tor мёртв)
def run_all(
    proxy_url: str,
    secrets_file: str,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
    get_me_fn: GetMeFn | None = None,
    state_file: str | None = None,
    audit_fn: Callable[..., None] | None = None,
) -> bool:
    """Run all 3 stages; first failure → False (immediate exit semantics)."""
    if not check_tor_socks(runner=runner):
        _write_chain_state("red", ["tor-socks: FAIL"], state_file)
        _write_chain_audit("Tor SOCKS5 failed", audit_fn)
        return False
    if not check_privoxy(proxy_url, runner=runner):
        _write_chain_state("red", ["tor-socks: ok", "privoxy: FAIL"], state_file)
        _write_chain_audit("Privoxy forward failed", audit_fn)
        return False
    if not check_telegram_api(secrets_file, proxy_url, facts=facts, get_me_fn=get_me_fn):
        _write_chain_state("red", ["tor-socks: ok", "privoxy: ok", "telegram-api: FAIL"], state_file)
        _write_chain_audit("Telegram getMe failed through chain", audit_fn)
        return False
    _write_chain_state("green", ["tor-socks: ok", "privoxy: ok", "telegram-api: ok"], state_file)
    logger.info("[IMP:9][tor-hc][main] All healthchecks PASSED")
    return True


# endregion FUNC_run_all


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.healthcheck.tor_proxy_check`.

    ▶ ┌env (TELEGRAM_PROXY_URL, SECRETS_ENV_FILE, TOR_CHAIN_STATE_FILE)┐ → ○ run_all → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "") or f"http://127.0.0.1:{PRIVOXY_PORT}"
    secrets_file = os.environ.get("SECRETS_ENV_FILE", "") or str(deploy_paths.secrets_env_file())
    # 003 A3: state-file канарейки (out-of-band); env-override для тестов/оператора
    state_file = os.environ.get("TOR_CHAIN_STATE_FILE") or None
    return 0 if run_all(proxy_url, secrets_file, state_file=state_file) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
