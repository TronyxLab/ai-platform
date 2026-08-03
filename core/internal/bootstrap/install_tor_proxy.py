#!/usr/bin/env python3
# GREP_SUMMARY: install-tor-proxy tor privoxy systemd apt orchestration torrc cron-healthcheck firewall iptables circuit-verify idempotent
# STRUCTURE: ▶ guard root ┌argv (--tor-bridges-file/--skip-tor-verify)┐ → ○ install_packages (tor_setup) → ○ write_torrc (template + tor_transport bridges) → ○ write_privoxy_config (privoxy_config) → ○ enable_services (systemctl) → ◇ verify_services_active → ○ configure_firewall_docker (iptables -C/-I guard) → ○ install_cron_healthcheck (guard) → ◇ verify_tor_circuit (curl retry 12×5s) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Idempotent Python-оркестрация установки Tor + Privoxy (DevPlan 127 W1, S2).
##           Перенос оставшейся apt/systemd/iptables-оркестрации из install-tor-proxy.sh (321 LOC)
##           в тестируемый модуль: конфиг-генерация уже в Python (tor_setup 119 D2,
##           tor_transport 118 E1, privoxy_config 119 D2/D3) — здесь только оркестрация и
##           systemd/iptables/curl-каналы. Shell-фасад <50 LOC (guard root + exec python3 -m).
## @scope    Вызывается из lifecycle phases φ1 (system_bootstrap) через install-tor-proxy.sh
##           (tor_enabled=true, аргументы --tor-bridges-file/--skip-tor-verify); безопасен
##           для повторного запуска на provisioned ноде (идемпотентен по шагам).
## @invariants
##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__; business-функции raise
##   - Exit-контракт shared/contracts.py: EXIT_OK=0, EXIT_GENERIC=1 (байт-совместимость фасада)
##   - Идемпотентность: install_packages → [] при всех установленных (tor_setup);
##     privoxy_config → no-op при корректном конфиге; iptables -C guard; cron-guard по существованию файла
##   - Base torrc пишется первым (template core/bootstrap/tor/torrc.template; fallback inline);
##     bridges аппендятся только если --tor-bridges-file передан И файл существует
##   - Transport-парсинг/деградация/dedup → tor_transport.parse_bridges (118 E1);
##     unknown transport → TorTransportError → exit 1 (fail-fast канон)
##   - systemctl enable — non-fatal (|| true канон); restart/iptables -I — fatal (set -e канон)
##   - verify_services_active — оба сервиса active, иначе exit 1 (set -e канон)
##   - verify_tor_circuit — curl через SOCKS5 check.torproject.org, до 12 попыток × 5s;
##     failure → exit 1 (non-fatal для bootstrap, фаза ловит non_fatal=True)
##   - Функции никогда не пишут в stdout (только stderr-логи; stdout резервирован за CLI-данными)
## @rationale Q: Почему Python-модуль, а не продолжение shell?
##            A: Языковая политика (AGENTS.md) — новый код на Python; shell — тонкие фасады.
##            S2 (321 LOC) превышал порог фасада 150; вся бизнес-логика конфигов уже в Python —
##            остаток чистая оркестрация, которая тестируема только в Python (DI subprocess).
##            Q: Почему флаги --tor-bridges-file/--skip-tor-verify теперь обрабатываются?
##            A: В shell parse_args() была НИКОГДА не вызвана (main() её пропускал) — флаги
##            молча игнорировались, мосты не применялись при TOR_BRIDGES_FILE (латентный баг).
##            Python-модуль восстанавливает задокументированный контракт (torrc.template:7-11).
## @changes  2026-08-04 | DevPlan 127 W1 — Created (миграция install-tor-proxy.sh, S2)
## @see      core/internal/bootstrap/install-tor-proxy.sh (фасад), tor_setup.py, tor_transport.py,
##           privoxy_config.py, lifecycle/phases/system.py (tor_enabled фаза)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from core.internal.bootstrap import privoxy_config, tor_setup, tor_transport
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK

logger = logging.getLogger(__name__)

# ── Канонические пути/константы (совпадают с прежними литералами install-tor-proxy.sh) ──
DEFAULT_TOR_CONFIG: str = "/etc/tor/torrc"
DEFAULT_PRIVOXY_CONFIG: str = "/etc/privoxy/config"
DEFAULT_CRON_FILE: str = "/etc/cron.d/tor-proxy-healthcheck"
CRON_SCHEDULE: str = "*/5 * * * * root"
# Fallback inline torrc (shell heredoc, если template отсутствует)
FALLBACK_TORRC: str = "SOCKSPort 127.0.0.1:9050\nLog notice file /var/log/tor/notices.log\nDataDirectory /var/lib/tor\n"
# Template torrc — core/bootstrap/tor/torrc.template (shell "${SCRIPT_DIR}/../../bootstrap/tor/...")
TORRC_TEMPLATE: Path = Path(__file__).resolve().parent.parent.parent / "bootstrap" / "tor" / "torrc.template"
# Firewall: catch-all для всех Docker bridge-сетей (RFC 1918 172.16-31.x.x)
FIREWALL_COMMENT: str = "hermes-proxy-docker-bridges"
FIREWALL_SRC_NET: str = "172.16.0.0/12"
FIREWALL_DPORT: str = "8118"
# Tor circuit verification (curl через SOCKS5)
TOR_SOCKS_HOST: str = "127.0.0.1:9050"
VERIFY_URL: str = "https://check.torproject.org/"
VERIFY_MAX_ATTEMPTS: int = 12
VERIFY_SLEEP_SEC: int = 5
# Пауза между restart tor и restart privoxy (Tor должен поднять directory info)
SERVICE_RESTART_SLEEP_SEC: int = 3


# region EXCEPTIONS
class TorInstallUsageError(Exception):
    """Fail-fast: неизвестный аргумент CLI (shell exit 1 канон)."""


class CommandFailedError(Exception):
    """Fail-fast: обязательная команда (systemctl restart / iptables -I) вернула rc != 0."""


# endregion EXCEPTIONS


# region FUNC__log_step
def _log_step(step: str, status: str, msg: str) -> None:
    """log_step-эквивалент: [IMP:8][tor-proxy][<step>] <STATUS>: <msg> (logging.sh канон).

    ## @purpose  Байт-совместимый вывод шагов с прежним shell log_step (logging.sh:87-90).
    ## @io — ⇥ step, status, msg → ⎋ stderr via logger
    ## @complexity — O(1)
    """
    logger.info("[IMP:8][tor-proxy][%s] %s: %s", step, status, msg)


# endregion FUNC__log_step


# region FUNC_run_command
def run_command(cmd: list[str], *, check: bool = False, timeout: int | None = None) -> subprocess.CompletedProcess:
    """Единый subprocess-канал модуля (DI-шов для тестов).

    ## @purpose  systemctl/iptables/curl-канал. check=True → CommandFailedError на rc != 0
    ##            (set -e канон shell); check=False → graceful rc (|| true канон enable).
    ## @io — ⇥ cmd, check, timeout → ⎋ subprocess.CompletedProcess (check=False) ⚡ CommandFailedError
    ## @complexity — O(M) — время выполнения команды
    ## @invariants
    ##   - check=True: FileNotFoundError/TimeoutExpired/rc != 0 → CommandFailedError (fail-fast)
    ##   - check=False: rc возвращается как есть, никогда не raise (graceful)
    ##   - Все логи — stderr (logger), stdout команды НЕ логируется
    """
    logger.info("[IMP:8][tor-proxy][exec] Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        logger.error("[IMP:10][tor-proxy][exec] Binary not found: %s", cmd[0])
        if check:
            raise CommandFailedError(f"Command not found: {cmd[0]}") from None
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][tor-proxy][exec] Timed out: %s", " ".join(cmd))
        if check:
            raise CommandFailedError(f"Command timed out: {' '.join(cmd)}") from None
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")

    if result.returncode != 0 and check:
        logger.error(
            "[IMP:10][tor-proxy][exec] Command failed (exit=%d): %s — %s",
            result.returncode,
            " ".join(cmd),
            result.stderr.strip()[:300],
        )
        raise CommandFailedError(f"Command failed (exit={result.returncode}): {' '.join(cmd)}")
    return result


# endregion FUNC_run_command


# region FUNC_install_packages
def install_packages() -> None:
    """apt-установка tor/privoxy/obfs4proxy/[webtunnel] — тонкий фасад tor_setup (119 D2).

    ▶ ┌None┐ → ○ tor_setup.install_tor_packages() → ◇ installed? DONE │ SKIP → ⎋ None

    ## @purpose  install_packages() из install-tor-proxy.sh — бизнес-логика (webtunnel→obfs4
    ##            деградация) в tor_setup.py; здесь только DONE/SKIP-статус (byte-compat).
    ## @io — ⇥ None → ⎋ None
    ## @complexity — O(1) subprocess-цепочка tor_setup
    ## @invariants
    ##   - stdout tor_setup (список установленных) → DONE "Installed: X"; пусто → SKIP
    ##   - TorSetupError (провал базовых пакетов) → propagate → main → exit 1
    """
    installed = tor_setup.install_tor_packages()
    if installed:
        _log_step("packages", "DONE", f"Installed: {' '.join(installed)}")
        logger.info("[IMP:9][tor-install][packages] Tor/Privoxy packages installed: %s", " ".join(installed))
    else:
        _log_step("packages", "SKIP", "All packages already installed")
        logger.info("[IMP:9][tor-install][packages] Package state ensured (idempotent no-op)")


# endregion FUNC_install_packages


# region FUNC_write_torrc
def write_torrc(tor_config: Path, bridges_file: str | None, torrc_template: Path) -> None:
    """Запись base torrc (template | fallback) + аппенд bridge-секции (tor_transport, 118 E1).

    ▶ ┌tor_config, bridges_file, torrc_template┐ → ◇ template? base=template │ fallback inline
      → ○ write base → ◇ bridges_file ∧ isfile? → ○ parse_bridges/render (TorTransportError → exit 1)
      → ○ append section (| WARN empty) → ⎋ None

    ## @purpose  write_torrc() из install-tor-proxy.sh — transport-парсинг/деградация/dedup в
    ##            tor_transport.py; здесь I/O (чтение template, запись torrc, аппенд секции).
    ## @io — ⇥ tor_config: Path, bridges_file: str|None, torrc_template: Path → ⎋ None
    ## @complexity — O(L) — строк torrc
    ## @invariants
    ##   - base = template content если существует; иначе FALLBACK_TORRC (shell heredoc parity)
    ##   - bridges аппендятся только если bridges_file задан И является файлом
    ##   - tor_transport.parse_bridges → TorTransportError (unknown transport) → propagate (exit 1)
    ##   - Пустая секция (все мосты отброшены/нет мостов) → WARN, не ошибка
    ##   - Повторный запуск: base перезаписывается детерминированно (не портит конфиг)
    """
    _log_step("torrc", "START", f"Writing {tor_config}")

    if torrc_template.is_file():
        base = torrc_template.read_text(encoding="utf-8")
        _log_step("torrc", "INFO", f"Base config from template: {torrc_template}")
    else:
        base = FALLBACK_TORRC
        _log_step("torrc", "WARN", "Template not found — wrote inline base config")
    tor_config.write_text(base, encoding="utf-8")

    if bridges_file and Path(bridges_file).is_file():
        # ⚠️ TRAP[DECISION] · 2026-07-17 · MED · webtunnel binary absent → degradation to obfs4-only
        # · If webtunnel binary not found → drop webtunnel Bridge lines, continue with obfs4-only
        # · Rationale: webtunnel is optional; obfs4 is the primary transport for Telegram Bot API
        # · Rev: if context requires webtunnel → fail instead of degrade
        # ⚠️ TRAP[DECISION] · 2026-07-17 · — · dynamic ClientTransportPlugin detection (not hardcoded)
        # · Rejected: hardcoded ClientTransportPlugin obfs4 (cannot support mixed transports)
        # · Reason: single bridges.txt may contain obfs4 + webtunnel lines; Tor needs CTP per transport
        # · Rev: if transport count grows past 3 → move mapping to external config
        # DevPlan 118 E1 (D19): transport-парсинг/деградация/dedup → tor_transport.py (test-first).
        try:
            content = Path(bridges_file).read_text(encoding="utf-8")
            result = tor_transport.parse_bridges(content, available_binaries=tor_transport.resolve_available_binaries())
            section = tor_transport.render_torrc_section(result.filtered_bridges, result.transports_to_emit)
        except tor_transport.TorTransportError:
            _log_step("torrc", "ERROR", f"Unknown transport in {bridges_file} — no registered binary path")
            raise
        if section:
            with tor_config.open("a", encoding="utf-8") as f:
                f.write(section + "\n")
            _log_step("torrc", "INFO", f"Bridges appended from {bridges_file} (transport-parsing via tor_transport.py)")
        else:
            _log_step("torrc", "WARN", f"No usable bridges found in {bridges_file} (all dropped or empty)")
    else:
        _log_step("torrc", "INFO", "No bridges file — Tor will connect directly")

    _log_step("torrc", "DONE", f"{tor_config} written")
    logger.info("[IMP:9][tor-install][torrc] torrc ready: %s", tor_config)


# endregion FUNC_write_torrc


# region FUNC_write_privoxy_config
def write_privoxy_config(config_path: Path) -> None:
    """Идемпотентная запись Privoxy-конфига — тонкий фасад privoxy_config (119 D3).

    ▶ ┌config_path┐ → ○ privoxy_config.write_privoxy_config() → ◇ OSError? FAIL → ⎋ None

    ## @purpose  write_privoxy_config() из install-tor-proxy.sh — мутации (grep-guard + sed) в
    ##            privoxy_config.py; здесь DONE/FAIL-статус (byte-compat) + OSError → exit 1.
    ## @io — ⇥ config_path: Path → ⎋ None
    ## @complexity — O(L) — строк конфига
    ## @invariants — Повторный вызов с корректным конфигом = no-op (privoxy_config идемпотентен)
    """
    _log_step("privoxy-config", "START", f"Configuring {config_path}")
    try:
        privoxy_config.write_privoxy_config(str(config_path))
    except OSError:
        _log_step("privoxy-config", "FAIL", f"Failed to write {config_path}")
        raise
    _log_step("privoxy-config", "DONE", f"{config_path} ready")


# endregion FUNC_write_privoxy_config


# region FUNC_enable_services
def enable_services() -> None:
    """systemctl enable (non-fatal) + restart (fatal) tor/privoxy с паузой 3s.

    ▶ ┌None┐ → ○ enable tor/privoxy (|| true) → ○ restart tor (fatal) → ○ sleep 3 → ○ restart privoxy → ⎋ None

    ## @purpose  enable_services() из install-tor-proxy.sh — enable молча (2>/dev/null || true),
    ##            restart fatal (set -e канон); пауза 3s — Tor поднимает directory info до Privoxy.
    ## @io — ⇥ None → ⎋ None ⚡ CommandFailedError (restart tor/privoxy провалился)
    ## @complexity — O(1) subprocess × 4
    ## @invariants — enable rc игнорируется (|| true); restart rc != 0 → fatal (exit 1)
    """
    _log_step("services", "START", "Enabling and starting services")

    run_command(["systemctl", "enable", "tor", "--quiet"], check=False)
    run_command(["systemctl", "enable", "privoxy", "--quiet"], check=False)

    _log_step("services", "INFO", "Restarting Tor...")
    run_command(["systemctl", "restart", "tor"], check=True)
    # [IMP:9][tor-install][services] Give Tor time to bootstrap its directory info
    # before Privoxy tries to forward through it. 3s minimum for local Tor start.
    logger.info("[IMP:9][tor-install][services] Tor restarted — waiting %ds before Privoxy", SERVICE_RESTART_SLEEP_SEC)
    time.sleep(SERVICE_RESTART_SLEEP_SEC)

    _log_step("services", "INFO", "Restarting Privoxy...")
    run_command(["systemctl", "restart", "privoxy"], check=True)

    _log_step("services", "DONE", "Both services restarted")


# endregion FUNC_enable_services


# region FUNC_verify_services_active
def verify_services_active() -> bool:
    """Проверка active-статуса tor/privoxy (systemctl is-active --quiet).

    ▶ ┌None┐ → ○ is-active tor → ○ is-active privoxy → ◇ оба active? → ⎋ True │ False

    ## @purpose  verify_services_active() из install-tor-proxy.sh — оба сервиса active, иначе
    ##            main возвращает exit 1 (set -e канон shell).
    ## @io — ⇥ None → ⎋ bool
    ## @complexity — O(1) subprocess × 2
    """
    _log_step("verify-active", "START", "Checking Tor and Privoxy are active")
    fail = 0

    if run_command(["systemctl", "is-active", "--quiet", "tor"], check=False).returncode == 0:
        _log_step("verify-active", "OK", "Tor: active")
    else:
        _log_step("verify-active", "FAIL", "Tor: NOT active")
        fail = 1

    if run_command(["systemctl", "is-active", "--quiet", "privoxy"], check=False).returncode == 0:
        _log_step("verify-active", "OK", "Privoxy: active")
    else:
        _log_step("verify-active", "FAIL", "Privoxy: NOT active")
        fail = 1

    if fail:
        logger.error("[IMP:10][tor-install][verify-active] Tor or Privoxy NOT active — services check failed")
        return False
    _log_step("verify-active", "DONE", "Both services active")
    logger.info("[IMP:9][tor-install][verify-active] Tor and Privoxy are active")
    return True


# endregion FUNC_verify_services_active


# region FUNC_verify_tor_circuit
def verify_tor_circuit(skip: bool = False) -> bool:
    """Проверка Tor-цепи через SOCKS5 check.torproject.org (до 12 попыток × 5s).

    ▶ ┌skip┐ → ◇ skip? SKIP+True → ○ curl --socks5-hostname → ◇ "Congratulations"? DONE+True
      → ○ sleep 5 (retry) → ○ 12× FAIL+False → ⎋ bool

    ## @purpose  verify_tor_circuit() из install-tor-proxy.sh — [IMP:9] канон: проверка через
    ##            SOCKS5 127.0.0.1:9050; Tor может тратить время на directory info + circuit.
    ## @io — ⇥ skip: bool → ⎋ bool (circuit established)
    ## @complexity — O(A × C) — A=12 попыток, C=curl (--max-time 10)
    ## @invariants
    ##   - skip=True → SKIP-статус + True без curl
    ##   - Успех = stdout curl содержит "Congratulations"
    ##   - rc curl != 0 (сеть/таймаут) трактуется как не-успех → retry (не fatal)
    """
    if skip:
        _log_step("verify-tor", "SKIP", "Tor verification skipped (--skip-tor-verify)")
        logger.info("[IMP:9][tor-install][verify-tor] Verification skipped (--skip-tor-verify)")
        return True

    _log_step("verify-tor", "START", "Waiting for Tor circuit (up to 60s)")
    # [IMP:9][tor-install][verify-tor] Check via SOCKS5 against check.torproject.org
    # Retry loop: Tor may need time to bootstrap directory info and build circuit.
    for attempt in range(1, VERIFY_MAX_ATTEMPTS + 1):
        result = run_command(
            ["curl", "--socks5-hostname", TOR_SOCKS_HOST, "-s", "--max-time", "10", VERIFY_URL],
            check=False,
            timeout=15,
        )
        if "Congratulations" in (result.stdout or ""):
            _log_step("verify-tor", "DONE", f"Tor circuit established after {attempt}x{VERIFY_SLEEP_SEC}s")
            logger.info(
                "[IMP:9][tor-install][verify-tor] Tor circuit established after %dx%ds", attempt, VERIFY_SLEEP_SEC
            )
            return True
        if attempt < VERIFY_MAX_ATTEMPTS:
            time.sleep(VERIFY_SLEEP_SEC)

    _log_step("verify-tor", "FAIL", "Tor failed to establish circuit within 60s")
    logger.error("[IMP:10][tor-install][verify-tor] Tor circuit NOT established within 60s")
    return False


# endregion FUNC_verify_tor_circuit


# region FUNC_install_cron_healthcheck
def install_cron_healthcheck(core_dir: Path, cron_file: Path) -> None:
    """Установка cron-джобы healthcheck (guard: hc-скрипт существует, cron уже установлен).

    ▶ ┌core_dir, cron_file┐ → ◇ hc_script missing? SKIP → ◇ cron_file exists? SKIP
      → ○ write "*/5 * * * * root <core>/internal/healthcheck/tor-proxy-healthcheck.sh" + chmod 0644 → ⎋ None

    ## @purpose  install_cron_healthcheck() из install-tor-proxy.sh — паттерн install_cron_metrics
    ##            (helpers/system.py:186): идемпотентный cron-guard по существованию файла.
    ## @io — ⇥ core_dir: Path, cron_file: Path → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - hc_script отсутствует → SKIP (cron не ставится)
    ##   - cron_file существует → SKIP (идемпотентность — повторный запуск не перезаписывает)
    ##   - Строка cron = "<CRON_SCHEDULE> <core_dir>/internal/healthcheck/tor-proxy-healthcheck.sh";
    ##     mode 0644 (root-readable, как канон /etc/cron.d)
    """
    hc_script = core_dir / "internal" / "healthcheck" / "tor-proxy-healthcheck.sh"

    if not hc_script.is_file():
        _log_step("cron-hc", "SKIP", f"Healthcheck script not found at {hc_script} — cron not installed")
        logger.info("[IMP:9][tor-install][cron-hc] Healthcheck script missing — cron skipped (no-op)")
        return

    if cron_file.is_file():
        _log_step("cron-hc", "SKIP", "Cron healthcheck already installed")
        logger.info("[IMP:9][tor-install][cron-hc] Cron healthcheck already installed (idempotent no-op)")
        return

    # ⚠️ TRAP[BUG] heredoc без кавычек — переменная CORE_DIR раскрывается
    #   Раньше было:  'CRON'  и  ${PLATFORM_ROOT}/core/  — не работало после rsync в /opt/core/
    #   PLATFORM_ROOT from core/lib/paths.sh (SoT). В Python CORE_DIR — абсолютный путь
    #   развёрнутого core/ (Path(__file__) — детерминирован независимо от cwd).
    cron_line = f"{CRON_SCHEDULE} {hc_script}\n"
    cron_file.write_text(cron_line, encoding="utf-8")
    cron_file.chmod(0o644)
    _log_step("cron-hc", "DONE", f"Healthcheck cron installed: {cron_file}")
    logger.info("[IMP:9][tor-install][cron-hc] Cron healthcheck installed: %s", cron_file)


# endregion FUNC_install_cron_healthcheck


# region FUNC_configure_firewall_docker
def configure_firewall_docker() -> None:
    """iptables-правило Docker bridge → Privoxy:8118 (catch-all 172.16.0.0/12, идемпотентно).

    ▶ ┌None┐ → ○ iptables -C (guard) → ◇ rc!=0? iptables -I (fatal) │ уже существует → ⎋ None

    ## @purpose  configure_firewall_docker() из install-tor-proxy.sh — single catch-all rule
    ##            для ВСЕХ Docker bridge-сетей (172.16.0.0/12, RFC 1918) — per-interface правила
    ##            пропускают сети, созданные после bootstrap. Idempotency: iptables -C guard.
    ## @io — ⇥ None → ⎋ None ⚡ CommandFailedError (iptables -I провалился, set -e канон)
    ## @complexity — O(1) subprocess × 1-2
    ## @invariants
    ##   - -C (check) — graceful; rc!=0 (правила нет) → -I (add) — fatal при провале
    ##   - Повторный запуск: -C rc=0 → "rule already exists" (no-op)
    ##   - UFW НЕ трогается: iptables catch-all + Privoxy permit-access достаточно (TRAP[DECISION])
    """
    _log_step("firewall", "START", "Configuring firewall for Docker bridge → Privoxy:8118 (catch-all 172.16.0.0/12)")

    rule = [
        "-p",
        "tcp",
        "--dport",
        FIREWALL_DPORT,
        "-s",
        FIREWALL_SRC_NET,
        "-j",
        "ACCEPT",
        "-m",
        "comment",
        "--comment",
        FIREWALL_COMMENT,
    ]
    check = run_command(["iptables", "-C", "INPUT", *rule], check=False)

    if check.returncode != 0:
        run_command(["iptables", "-I", "INPUT", *rule], check=True)
        _log_step("firewall", "INFO", f"iptables: allowed all Docker bridges ({FIREWALL_SRC_NET} → :{FIREWALL_DPORT})")
        _log_step(
            "firewall", "DONE", f"Firewall: Docker bridges → Privoxy:{FIREWALL_DPORT} allowed ({FIREWALL_SRC_NET})"
        )
        logger.info(
            "[IMP:9][tor-install][firewall] iptables rule added: %s → :%s (comment=%s)",
            FIREWALL_SRC_NET,
            FIREWALL_DPORT,
            FIREWALL_COMMENT,
        )
    else:
        # 🧐 TRAP[DECISION] · 2026-06-27 · — · UFW rules skipped: iptables catch-all covers all Docker bridges
        # · Rejected: per-interface UFW rules · Reason: UFW per-interface rules don't cover networks
        #   created after bootstrap; iptables catch-all + Privoxy permit-access is sufficient
        # · Rev: if UFW is the only firewall (no iptables fallback), add ufw route allow
        _log_step("firewall", "INFO", f"iptables rule already exists for {FIREWALL_SRC_NET} → :{FIREWALL_DPORT}")
        _log_step("firewall", "INFO", "Firewall rule already present")
        logger.info("[IMP:9][tor-install][firewall] iptables rule already present (idempotent no-op)")


# endregion FUNC_configure_firewall_docker


# region FUNC__parse_args
def _parse_args(argv: list[str]) -> tuple[str | None, bool]:
    """Разбор CLI-аргументов (byte-compat с shell parse_args: exit 1 на unknown).

    ▶ ┌argv┐ → ○ --tor-bridges-file <f> / --skip-tor-verify → ◇ unknown? ERROR → ⎋ (bridges_file, skip_verify)

    ## @purpose  --tor-bridges-file/--skip-tor-verify (контракт install-tor-proxy.sh). Shell
    ##            parse_args была мёртвым кодом (main() её не вызывал) — здесь восстановлена.
    ## @io — ⇥ argv → ⎋ (str|None, bool) ⚡ TorInstallUsageError (unknown argument / missing value)
    ## @complexity — O(A) — A = аргументы
    ## @invariants — Ошибка формата = "[IMP:10][tor-install][args] ERROR: Unknown argument: X" (exit 1)
    """
    bridges_file: str | None = None
    skip_verify = False
    it = iter(argv)
    for arg in it:
        if arg == "--tor-bridges-file":
            try:
                bridges_file = next(it)
            except StopIteration:
                logger.error("[IMP:10][tor-install][args] ERROR: --tor-bridges-file requires a value")
                raise TorInstallUsageError("--tor-bridges-file requires a value") from None
        elif arg == "--skip-tor-verify":
            skip_verify = True
        else:
            logger.error("[IMP:10][tor-install][args] ERROR: Unknown argument: %s", arg)
            raise TorInstallUsageError(f"Unknown argument: {arg}")
    return bridges_file, skip_verify


# endregion FUNC__parse_args


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 -m core.internal.bootstrap.install_tor_proxy [--tor-bridges-file F] [--skip-tor-verify]`.

    ▶ ┌argv┐ → ◇ root? → ◇ args valid? → ⚡ banner → ○ install_packages → ○ write_torrc → ○ write_privoxy_config
      → ○ enable_services → ◇ verify_services_active? → ○ configure_firewall_docker → ○ install_cron_healthcheck
      → ◇ verify_tor_circuit? → ⎋ 0 │ ⎋ 1

    ## @purpose  Точка входа для install-tor-proxy.sh (фасад exec python3 -m). Exit-контракт
    ##            shared/contracts.py: 0=ok, 1=generic error (byte-compat shell exit 0/1).
    ## @io — ⇥ argv → ⎋ int
    ## @complexity — O(P + T) — P=шаги оркестрации, T=verify circuit retry
    ## @invariants
    ##   - sys.exit НЕ вызывается — main() возвращает int (канон core/AGENTS.md)
    ##   - Любой fail-fast шаг (TorSetupError/TorTransportError/CommandFailedError/OSError) → exit 1
    ##   - verify_services_active failure → exit 1 (set -e канон)
    ##   - verify_tor_circuit failure → exit 1 + CRITICAL (non-fatal для bootstrap-фазы)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        bridges_file, skip_verify = _parse_args(argv)
    except TorInstallUsageError:
        return EXIT_GENERIC

    if os.geteuid() != 0:
        logger.error("[IMP:10][tor-install][main] ERROR: must run as root")
        return EXIT_GENERIC

    logger.info("[IMP:9][tor-install][main] ====================================")
    logger.info("[IMP:9][tor-install][main] Tor + Privoxy Installer START")
    logger.info("[IMP:9][tor-install][main] ====================================")

    try:
        install_packages()
        write_torrc(Path(DEFAULT_TOR_CONFIG), bridges_file, TORRC_TEMPLATE)
        write_privoxy_config(Path(DEFAULT_PRIVOXY_CONFIG))
        enable_services()
        if not verify_services_active():
            return EXIT_GENERIC
        configure_firewall_docker()
        install_cron_healthcheck(Path(__file__).resolve().parent.parent.parent, Path(DEFAULT_CRON_FILE))
    except (tor_setup.TorSetupError, tor_transport.TorTransportError, CommandFailedError, OSError) as exc:
        logger.error("[IMP:10][tor-install][main] %s", exc)
        return EXIT_GENERIC

    if verify_tor_circuit(skip=skip_verify):
        logger.info("[IMP:9][tor-install][main] Tor + Privoxy installation complete — circuit verified")
        return EXIT_OK
    logger.error("[IMP:10][tor-install][main] CRITICAL: Tor circuit failed to establish")
    logger.error("[IMP:10][tor-install][main] Telegram notifications will be unavailable until bridges are configured")
    return EXIT_GENERIC


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
