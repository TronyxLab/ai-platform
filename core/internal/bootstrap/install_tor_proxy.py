#!/usr/bin/env python3
# GREP_SUMMARY: install-tor-proxy tor privoxy systemd apt orchestration torrc cron-healthcheck firewall iptables circuit-verify idempotent DI runner facts clock generators compose-torrc render-cron-line build-firewall-rule TorProxyInstaller W4c
# STRUCTURE: ▶ ┌runner/facts/clock DI (TorProxyInstaller)┐ → ○ guard root → ○ install_packages (tor_setup) → ○ write_torrc (compose_torrc + tor_transport bridges)
#            → ○ write_privoxy_config (privoxy_config) → ○ enable_services (systemctl + clock sleep 3) → ◇ verify_services_active
#            → ○ configure_firewall_docker (build_firewall_rule + iptables -C/-I guard) → ○ install_cron_healthcheck (render_cron_line guard)
#            → ◇ verify_tor_circuit (curl retry 12×5s, clock) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Idempotent Python-оркестрация установки Tor + Privoxy (DevPlan 127 W1 S2 + 160 W4c T4.3).
##           Перенос оставшейся apt/systemd/iptables-оркестрации из install-tor-proxy.sh (321 LOC)
##           в тестируемый модуль: конфиг-генерация уже в Python (tor_setup 119 D2,
##           tor_transport 118 E1, privoxy_config 119 D2/D3) — здесь оркестрация и
##           systemd/iptables/curl-каналы. W4c: декомпозиция 566 LOC — чистые генераторы
##           конфигов (compose_torrc/render_cron_line/build_firewall_rule) + оркестратор-класс
##           TorProxyInstaller с конструкторной DI (runner/facts/clock) — тесты без monkeypatch.
##           Shell-фасад <50 LOC (guard root + exec python3 -m) НЕ меняется.
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
##   - W4c DI: runner/facts/clock — конструкторные параметры (None = ленивые дефолты
##     default_command_runner()/default_env_facts()/time.sleep); единственные изменения логики —
##     вызовы через runner/facts/clock вместо прямых subprocess/os/time; поведение байт-эквивалентно
##   - Чистые генераторы (compose_torrc/render_cron_line/build_firewall_rule) НЕ делают I/O
## @rationale Q: Почему Python-модуль, а не продолжение shell?
##            A: Языковая политика (AGENTS.md) — новый код на Python; shell — тонкие фасады.
##            S2 (321 LOC) превышал порог фасада 150; вся бизнес-логика конфигов уже в Python —
##            остаток чистая оркестрация, которая тестируема только в Python (DI subprocess).
##            Q: Почему флаги --tor-bridges-file/--skip-tor-verify теперь обрабатываются?
##            A: В shell parse_args() была НИКОГДА не вызвана (main() её пропускал) — флаги
##            молча игнорировались, мосты не применялись при TOR_BRIDGES_FILE (латентный баг).
##            Python-модуль восстанавливает задокументированный контракт (torrc.template:7-11).
##            Q (W4c): Зачем оркестратор-класс с DI?
##            A: DevPlan 160 AF-4: god-модуль 566 LOC требовал 67 monkeypatch-патчей на тест.
##            Чистые генераторы + конструкторная DI (runner/facts/clock) делают шаги
##            тестируемыми через Fake-объекты (FakeCommandRunner/FakeFacts/FakeClock) —
##            monkeypatch.setattr в тестах падает с 37 до <5.
## @changes  2026-08-04 | DevPlan 127 W1 — Created (миграция install-tor-proxy.sh, S2)
## @changes  2026-08-13 | DevPlan 160 W4b — +facts: EnvironmentFacts | None (root-guard DI)
## @changes  2026-08-13 | DevPlan 160 W4c — Декомпозиция (T4.3): +TorProxyInstaller (DI
##            runner/facts/clock), +чистые генераторы compose_torrc/render_cron_line/
##            build_firewall_rule; run_command → self._run (PlatformFatalError → CommandFailedError);
##            time.sleep → clock; Path.is_file → facts.path_isfile; resolve_available_binaries(facts=self.facts)
## @changes  2026-08-14 | DevPlan 170 W1-A3 — FIREWALL_DPORT из SoT firewall.PRIVOXY_PORT (только 8118-строки)
## @changes  2026-08-15 | DevPlan 170 W6-D2 — build_firewall_rule/FIREWALL_COMMENT/FIREWALL_SRC_NET
##                      консолидированы в docker_user_policy.py (iptables-домен); здесь — тонкая
##                      обёртка с дефолтами (тест-контракт install_tor_proxy.build_firewall_rule
##                      и FIREWALL_* module-attr сохранены); FIREWALL_DPORT остаётся (SoT PRIVOXY_PORT)
## @see      core/internal/bootstrap/install-tor-proxy.sh (фасад), tor_setup.py, tor_transport.py,
##           privoxy_config.py, docker_user_policy.py (iptables-домен),
##           lifecycle/phases/system.py (tor_enabled фаза)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from core.internal.bootstrap import privoxy_config, tor_setup, tor_transport

# DevPlan 170 W6-D2: iptables-генератор privoxy INPUT-правила — единая реализация в
# docker_user_policy.py (leaf iptables-домен); здесь обёртка с дефолтами (test-контракт).
from core.internal.bootstrap.docker_user_policy import (
    FIREWALL_COMMENT,
    FIREWALL_SRC_NET,
)
from core.internal.bootstrap.docker_user_policy import (
    build_firewall_rule as _build_firewall_rule_impl,
)

# DevPlan 170 W1-A3: приватный порт Privoxy из SoT firewall.py (литерал 8118 удалён)
from core.internal.bootstrap.firewall import PRIVOXY_PORT
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.exceptions import PlatformFatalError
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner

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
# FIREWALL_COMMENT/FIREWALL_SRC_NET — из docker_user_policy.py (iptables-домен, W6-D2);
# FIREWALL_DPORT — str для iptables --dport (аргумент команды); значение из SoT PRIVOXY_PORT
FIREWALL_DPORT: str = f"{PRIVOXY_PORT}"
# Tor circuit verification (curl через SOCKS5)
TOR_SOCKS_HOST: str = "127.0.0.1:9050"
VERIFY_URL: str = "https://check.torproject.org/"
VERIFY_MAX_ATTEMPTS: int = 12
VERIFY_SLEEP_SEC: int = 5
# Пауза между restart tor и restart privoxy (Tor должен поднять directory info)
SERVICE_RESTART_SLEEP_SEC: int = 3

# ── Privoxy Restart=on-failure drop-in (DevPlan 162 W3-3) ──
# privoxy.service Restart=no на проде: краш privoxy молча убивает telegram-нотификации
# (тор-прокси вниз) до ручного systemctl start. Drop-in Restart=on-failure — systemd
# сам поднимает privoxy после краха (паттерн docker_installer.configure_systemd_override).
PRIVOXY_RESTART_DROPIN_DEFAULT: str = "/etc/systemd/system/privoxy.service.d/99-platform-restart.conf"
PRIVOXY_RESTART_DROPIN_CONTENT: str = "[Service]\nRestart=on-failure\n"

# Clock-канал оркестратора: callable(seconds) → None (реальный default = time.sleep; тесты — fake)
Clock = Callable[[float], None]


# region EXCEPTIONS
class TorInstallUsageError(Exception):
    """Fail-fast: неизвестный аргумент CLI (shell exit 1 канон)."""


class CommandFailedError(Exception):
    """Fail-fast: обязательная команда (systemctl restart / iptables -I) вернула rc != 0.

    ## @rationale W4c: единый модульный тип ошибки сохранён — runner (run_subprocess) raise'ит
    ##            PlatformFatalError, а _run транслирует его в CommandFailedError (байт-совместимость
    ##            except-контракта run()/main() — поведение без изменений).
    """


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


# ═══════════════════════════════════════════════════════════════════════════
# Чистые генераторы конфигов (W4c T4.3) — НИКАКОГО I/O, только параметры → строки/структуры
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_compose_torrc
def compose_torrc(base_content: str | None, bridges_section: str | None) -> str:
    """Собрать финальный torrc: base (template | FALLBACK_TORRC) + bridge-секция (опционально).

    ▶ ┌base_content, bridges_section┐ → ◇ base? │ FALLBACK_TORRC → ◇ section? +section+"\n" │ → ⎋ str

    ## @purpose  Чистая генерация torrc-содержимого (W4c T4.3) — заменяет write_text + append
    ##            в write_torrc (файловый I/O остаётся в оркестраторе).
    ## @io — ⇥ base_content: str | None (None = fallback inline), bridges_section: str | None
    ##          → ⎋ str (байт-эквивалент прежней записи: base [+ section + "\\n"])
    ## @complexity — O(B + S) — B/S = длина base/section
    ## @invariants
    ##   - base_content=None → FALLBACK_TORRC (shell heredoc parity)
    ##   - bridges_section задан → base + section + "\\n" (прежний append f.write(section + "\\n"))
    ##   - bridges_section пуст/None → base как есть
    """
    base = base_content if base_content is not None else FALLBACK_TORRC
    if bridges_section:
        return base + bridges_section + "\n"
    return base


# endregion FUNC_compose_torrc


# region FUNC_render_cron_line
def render_cron_line(schedule: str, healthcheck_script: Path) -> str:
    """Собрать cron-строку healthcheck: "<schedule> <script>\\n" (канон /etc/cron.d).

    ▶ ┌schedule, script┐ → ⊕ f"{schedule} {script}\\n" → ⎋ str

    ## @purpose  Чистый генератор cron-записи (W4c T4.3) — ранее inline-f-string в
    ##            install_cron_healthcheck; вынесен для точного теста строки.
    ## @io — ⇥ schedule: str, healthcheck_script: Path → ⎋ str (строка cron + "\\n")
    ## @complexity — O(1)
    ## @invariants — Результат = "{schedule} {healthcheck_script}\\n" (без экранирования пути)
    """
    return f"{schedule} {healthcheck_script}\n"


# endregion FUNC_render_cron_line


# region FUNC_build_firewall_rule
def build_firewall_rule(
    *,
    dport: str = FIREWALL_DPORT,
    src_net: str = FIREWALL_SRC_NET,
    comment: str = FIREWALL_COMMENT,
) -> list[str]:
    """Собрать iptables-правило Docker bridge → Privoxy (для -C/-I INPUT).

    ▶ ┌dport, src_net, comment┐ → ⊕ _build_firewall_rule_impl (docker_user_policy) → ⎋ list[str]

    ## @purpose  Тонкая обёртка над единой реализацией docker_user_policy.build_firewall_rule
    ##            (DevPlan 170 W6-D2): дефолты из локальных констант (FIREWALL_DPORT из SoT
    ##            PRIVOXY_PORT); контракт install_tor_proxy.build_firewall_rule сохранён
    ##            (module-attr тестов + configure_firewall_docker вызов без аргументов).
    ## @io — ⇥ dport/src_net/comment (keyword-only, дефолты = канонные константы) → ⎋ list[str]
    ## @complexity — O(1)
    ## @invariants — Порядок аргументов канона shell: -p tcp --dport D -s NET -j ACCEPT -m comment
    ##               --comment C (совпадает с прежним литералом configure_firewall_docker)
    """
    return _build_firewall_rule_impl(dport=dport, src_net=src_net, comment=comment)


# endregion FUNC_build_firewall_rule


# ═══════════════════════════════════════════════════════════════════════════
# Оркестратор-класс (W4c T4.3) — конструкторная DI runner/facts/clock
# ═══════════════════════════════════════════════════════════════════════════


# region CLASS_TorProxyInstaller
# region FUNC_configure_privoxy_restart_dropin
def configure_privoxy_restart_dropin(dropin: Path) -> None:
    """Идемпотентная запись systemd drop-in Restart=on-failure для privoxy (DevPlan 162 W3-3).

    ▶ ┌dropin path┐ → ◇ exists? SKIP │ mkdir parents + write → ⎋ None

    ## @purpose  privoxy.service Restart=no на проде: при падении privoxy тор-прокси вниз и
    ##            telegram-нотификации мертвы до ручного systemctl start. Drop-in
    ##            Restart=on-failure (паттерн docker_installer.configure_systemd_override:
    ##            write if absent, idempotent) — systemd сам поднимет privoxy после краха.
    ## @io — ⇥ dropin: Path → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - Существующий drop-in НЕ перезаписывается (идемпотентность)
    ##   - OSError → propagate (main → exit 1, fail-fast канон)
    ##   - daemon-reload вызывается в enable_services (drop-in активен до restart)
    """
    if dropin.is_file():
        _log_step("privoxy-dropin", "SKIP", f"Restart drop-in already exists at {dropin}")
        logger.info("[IMP:9][tor-install][privoxy-dropin] Restart=on-failure drop-in exists (idempotent no-op)")
        return
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text(PRIVOXY_RESTART_DROPIN_CONTENT, encoding="utf-8")
    _log_step("privoxy-dropin", "DONE", f"Restart=on-failure drop-in written: {dropin}")
    logger.info("[IMP:9][tor-install][privoxy-dropin] Restart=on-failure drop-in written: %s", dropin)


# endregion FUNC_configure_privoxy_restart_dropin

# 🧐 TRAP[DECISION] · 2026-08-13 · MED · docker.service override simplification (LimitNOFILE-only) — deferred
# · Rejected: убрать Restart=always/RestartSec=10s из docker.service override сейчас (162 W3-3)
# · Reason: deferred — требует инспекции ноды (зачем override дублирует Restart, каков реальный
# ·   дефолт docker.service на проде); упрощение без inspection рискует изменить restart-политику
# ·   daemon (live-restore инвариант S5). LimitNOFILE-only — отдельный W8-2 кандидат (nofile 1024).
# · Rev: первый доступ к проду после 162 — systemctl show docker.service -p Restart; если
# ·   Restart=always уже в дефолте юнита → удалить дубли из SYSTEMD_OVERRIDE (docker_installer.py)


class TorProxyInstaller:
    """Оркестратор установки Tor+Privoxy с конструкторной DI (DevPlan 160 W4c T4.3).

    ## @purpose — Все шаги установки (packages/torrc/privoxy/services/verify/firewall/cron/circuit)
    ##            как методы; I/O-каналы инъектируются: runner (subprocess), facts (root/isfile/
    ##            which), clock (sleep) — тесты передают fakes, поведение без изменений.
    ## @io — ⇥ runner/facts/clock (None = ленивые дефолты) → ⎋ экземпляр оркестратора
    ## @complexity — O(1) конструкция; методы — O(шаг)
    ## @invariants
    ##   - runner=None → default_command_runner() (канон run_subprocess, C10/B4)
    ##   - facts=None → default_env_facts() (is_root/which/path_isfile)
    ##   - clock=None → time.sleep (реальный; тесты — fake, без пауз)
    ##   - _run транслирует PlatformFatalError → CommandFailedError (модульный except-контракт)
    ## @rationale — W4c (DevPlan 160 AF-4): god-модуль → класс + чистые генераторы; monkeypatch
    ##            в тестах заменён Fake-объектами (конструкторная DI).
    ## @changes 2026-08-13 | DevPlan 160 W4c — created (T4.3)
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        facts: EnvironmentFacts | None = None,
        clock: Clock | None = None,
        dropin_fn: Callable[[Path], None] | None = None,
    ) -> None:
        """Инициализация с ленивыми дефолтами (default_command_runner/default_env_facts/time.sleep).

        ▶ ┌runner?, facts?, clock?, dropin_fn?┐ → ◇ None → дефолт → ⊕ self.* → ⎋ None

        ## @invariants
        ##   - dropin_fn=None → configure_privoxy_restart_dropin (канон); тесты передают fake
        ##     (0 патчей модульной функции, W-H DevPlan 163)
        """
        self.runner: CommandRunner = runner if runner is not None else default_command_runner()
        self.facts: EnvironmentFacts = facts if facts is not None else default_env_facts()
        self.clock: Clock = clock if clock is not None else time.sleep
        self._dropin_fn: Callable[[Path], None] = configure_privoxy_restart_dropin if dropin_fn is None else dropin_fn

    # region FUNC_TorProxyInstaller_run
    def _run(
        self, cmd: list[str], *, check: bool = False, timeout: int | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Единый subprocess-канал оркестратора (DI через self.runner).

        ▶ ┌cmd, check, timeout┐ → ○ runner.run → ◇ PlatformFatalError? IMP:10 + CommandFailedError │ ⎋ CompletedProcess

        ## @purpose  Замена прежнего run_command: канал runner'а + трансляция PlatformFatalError →
        ##            CommandFailedError (check=True fail-fast set -e канон; check=False graceful rc).
        ## @io — ⇥ cmd, check, timeout (None = без таймаута, как прежний subprocess timeout=None)
        ##          → ⎋ subprocess.CompletedProcess ⚡ CommandFailedError (check=True / fatal)
        ## @complexity — O(M) — время выполнения команды
        ## @invariants
        ##   - check=False: rc возвращается как есть, никогда не raise (|| true канон enable)
        ##   - check=True: PlatformFatalError (rc!=0/not-found/timeout) → CommandFailedError (fail-fast)
        ##   - timeout=None передаётся как есть — прежнее поведение «без таймаута» (не 30s default)
        """
        logger.info("[IMP:8][tor-proxy][exec] Running: %s", " ".join(cmd))
        try:
            return self.runner.run(cmd, timeout=timeout, check=check)
        except PlatformFatalError as exc:
            logger.error("[IMP:10][tor-proxy][exec] Command failed: %s — %s", " ".join(cmd), exc)
            raise CommandFailedError(str(exc)) from None

    # endregion FUNC_TorProxyInstaller_run

    # region FUNC_TorProxyInstaller_install_packages
    @staticmethod
    def install_packages() -> None:
        """apt-установка tor/privoxy/obfs4proxy/[webtunnel] — тонкий фасад tor_setup (119 D2).

        ▶ ┌None┐ → ○ tor_setup.install_tor_packages() → ◇ installed? DONE │ SKIP → ⎋ None

        ## @purpose  install_packages() из install-tor-proxy.sh — бизнес-логика (webtunnel→obfs4
        ##            деградация) в tor_setup.py; здесь только DONE/SKIP-статус (byte-compat).
        ## @io — ⇥ None → ⎋ None
        ## @complexity — O(1) subprocess-цепочка tor_setup
        ## @invariants
        ##   - stdout tor_setup (список установленных) → DONE "Installed: X"; пусто → SKIP
        ##   - TorSetupError (провал базовых пакетов) → propagate → run() → exit 1
        """
        installed = tor_setup.install_tor_packages()
        if installed:
            _log_step("packages", "DONE", f"Installed: {' '.join(installed)}")
            logger.info("[IMP:9][tor-install][packages] Tor/Privoxy packages installed: %s", " ".join(installed))
        else:
            _log_step("packages", "SKIP", "All packages already installed")
            logger.info("[IMP:9][tor-install][packages] Package state ensured (idempotent no-op)")

    # endregion FUNC_TorProxyInstaller_install_packages

    # region FUNC_TorProxyInstaller_write_torrc
    def write_torrc(self, tor_config: Path, bridges_file: str | None, torrc_template: Path) -> None:
        """Запись base torrc (template | fallback) + аппенд bridge-секции (tor_transport, 118 E1).

        ▶ ┌tor_config, bridges_file, torrc_template┐ → ◇ template? base │ fallback inline
          → ○ compose_torrc(base, section) → ○ write_text → ⎋ None

        ## @purpose  write_torrc() из install-tor-proxy.sh — transport-парсинг/деградация/dedup в
        ##            tor_transport.py; здесь I/O (чтение template, запись torrc) + факты (isfile).
        ## @io — ⇥ tor_config: Path, bridges_file: str|None, torrc_template: Path → ⎋ None
        ## @complexity — O(L) — строк torrc
        ## @invariants
        ##   - base = template content если существует; иначе FALLBACK_TORRC (shell heredoc parity)
        ##   - bridges аппендятся только если bridges_file задан И является файлом (facts.path_isfile)
        ##   - tor_transport.parse_bridges → TorTransportError (unknown transport) → propagate (exit 1)
        ##   - Пустая секция (все мосты отброшены/нет мостов) → WARN, не ошибка
        ##   - Повторный запуск: torrc перезаписывается детерминированно (не портит конфиг)
        """
        _log_step("torrc", "START", f"Writing {tor_config}")

        if self.facts.path_isfile(torrc_template):
            base = torrc_template.read_text(encoding="utf-8")
            _log_step("torrc", "INFO", f"Base config from template: {torrc_template}")
        else:
            base = None  # → FALLBACK_TORRC в compose_torrc (shell heredoc parity)
            _log_step("torrc", "WARN", "Template not found — wrote inline base config")

        if bridges_file and self.facts.path_isfile(bridges_file):
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
                result = tor_transport.parse_bridges(
                    content, available_binaries=tor_transport.resolve_available_binaries(facts=self.facts)
                )
                section = tor_transport.render_torrc_section(result.filtered_bridges, result.transports_to_emit)
            except tor_transport.TorTransportError:
                _log_step("torrc", "ERROR", f"Unknown transport in {bridges_file} — no registered binary path")
                raise
            if section:
                _log_step(
                    "torrc", "INFO", f"Bridges appended from {bridges_file} (transport-parsing via tor_transport.py)"
                )
            else:
                _log_step("torrc", "WARN", f"No usable bridges found in {bridges_file} (all dropped or empty)")
        else:
            section = None
            _log_step("torrc", "INFO", "No bridges file — Tor will connect directly")

        tor_config.write_text(compose_torrc(base, section), encoding="utf-8")
        _log_step("torrc", "DONE", f"{tor_config} written")
        logger.info("[IMP:9][tor-install][torrc] torrc ready: %s", tor_config)

    # endregion FUNC_TorProxyInstaller_write_torrc

    # region FUNC_TorProxyInstaller_write_privoxy_config
    @staticmethod
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

    # endregion FUNC_TorProxyInstaller_write_privoxy_config

    # region FUNC_TorProxyInstaller_enable_services
    def enable_services(self) -> None:
        """systemctl enable (non-fatal) + restart (fatal) tor/privoxy с паузой 3s (clock).

        ▶ ┌None┐ → ○ drop-in Restart=on-failure (W3-3) → ○ enable tor/privoxy (|| true) → ○ daemon-reload
          → ○ restart tor (fatal) → ○ clock(3) → ○ restart privoxy → ⎋ None

        ## @purpose  enable_services() из install-tor-proxy.sh — enable молча (2>/dev/null || true),
        ##            restart fatal (set -e канон); пауза 3s — Tor поднимает directory info до Privoxy.
        ##            W3-3 (162): перед restart пишется drop-in Restart=on-failure (privoxy.service
        ##            Restart=no — краш privoxy молча убивает нотификации) + daemon-reload.
        ## @io — ⇥ None → ⎋ None ⚡ CommandFailedError (restart tor/privoxy провалился)
        ## @complexity — O(1) subprocess × 4
        ## @invariants — enable rc игнорируется (|| true); restart rc != 0 → fatal (exit 1);
        ##               sleep 3 через self.clock (W4c DI — тесты не спят)
        """
        _log_step("services", "START", "Enabling and starting services")

        # DevPlan 162 W3-3: drop-in ДО enable/restart — systemd должен увидеть override
        # при первом же restart (иначе краш до следующего bootstrap не восстановится).
        # W-H (DevPlan 163): dropin_fn DI (тесты передают fake, 0 патчей модульной функции)
        self._dropin_fn(Path(PRIVOXY_RESTART_DROPIN_DEFAULT))

        self._run(["systemctl", "enable", "tor", "--quiet"], check=False)
        self._run(["systemctl", "enable", "privoxy", "--quiet"], check=False)
        # W3-3: drop-in создан — daemon-reload обязателен (systemd не увидит Restart=on-failure
        # до reload; переживает ли юнит приватную настройку — нет).
        self._run(["systemctl", "daemon-reload"], check=False)

        _log_step("services", "INFO", "Restarting Tor...")
        self._run(["systemctl", "restart", "tor"], check=True)
        # [IMP:9][tor-install][services] Give Tor time to bootstrap its directory info
        # before Privoxy tries to forward through it. 3s minimum for local Tor start.
        logger.info(
            "[IMP:9][tor-install][services] Tor restarted — waiting %ds before Privoxy", SERVICE_RESTART_SLEEP_SEC
        )
        self.clock(SERVICE_RESTART_SLEEP_SEC)

        _log_step("services", "INFO", "Restarting Privoxy...")
        self._run(["systemctl", "restart", "privoxy"], check=True)

        _log_step("services", "DONE", "Both services restarted")

    # endregion FUNC_TorProxyInstaller_enable_services

    # region FUNC_TorProxyInstaller_verify_services_active
    def verify_services_active(self) -> bool:
        """Проверка active-статуса tor/privoxy (systemctl is-active --quiet).

        ▶ ┌None┐ → ○ is-active tor → ○ is-active privoxy → ◇ оба active? → ⎋ True │ False

        ## @purpose  verify_services_active() из install-tor-proxy.sh — оба сервиса active, иначе
        ##            run() возвращает exit 1 (set -e канон shell).
        ## @io — ⇥ None → ⎋ bool
        ## @complexity — O(1) subprocess × 2
        """
        _log_step("verify-active", "START", "Checking Tor and Privoxy are active")
        fail = 0

        if self._run(["systemctl", "is-active", "--quiet", "tor"], check=False).returncode == 0:
            _log_step("verify-active", "OK", "Tor: active")
        else:
            _log_step("verify-active", "FAIL", "Tor: NOT active")
            fail = 1

        if self._run(["systemctl", "is-active", "--quiet", "privoxy"], check=False).returncode == 0:
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

    # endregion FUNC_TorProxyInstaller_verify_services_active

    # region FUNC_TorProxyInstaller_verify_tor_circuit
    def verify_tor_circuit(self, skip: bool = False) -> bool:
        """Проверка Tor-цепи через SOCKS5 check.torproject.org (до 12 попыток × 5s, clock).

        ▶ ┌skip┐ → ◇ skip? SKIP+True → ○ curl --socks5-hostname → ◇ "Congratulations"? DONE+True
          → ○ clock(5) (retry) → ○ 12× FAIL+False → ⎋ bool

        ## @purpose  verify_tor_circuit() из install-tor-proxy.sh — [IMP:9] канон: проверка через
        ##            SOCKS5 127.0.0.1:9050; Tor может тратить время на directory info + circuit.
        ## @io — ⇥ skip: bool → ⎋ bool (circuit established)
        ## @complexity — O(A × C) — A=12 попыток, C=curl (--max-time 10, runner timeout 15)
        ## @invariants
        ##   - skip=True → SKIP-статус + True без curl
        ##   - Успех = stdout curl содержит "Congratulations"
        ##   - rc curl != 0 (сеть/таймаут) трактуется как не-успех → retry (не fatal)
        ##   - sleep 5 между попытками через self.clock (W4c DI)
        """
        if skip:
            _log_step("verify-tor", "SKIP", "Tor verification skipped (--skip-tor-verify)")
            logger.info("[IMP:9][tor-install][verify-tor] Verification skipped (--skip-tor-verify)")
            return True

        _log_step("verify-tor", "START", "Waiting for Tor circuit (up to 60s)")
        # [IMP:9][tor-install][verify-tor] Check via SOCKS5 against check.torproject.org
        # Retry loop: Tor may need time to bootstrap directory info and build circuit.
        for attempt in range(1, VERIFY_MAX_ATTEMPTS + 1):
            result = self._run(
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
                self.clock(VERIFY_SLEEP_SEC)

        _log_step("verify-tor", "FAIL", "Tor failed to establish circuit within 60s")
        logger.error("[IMP:10][tor-install][verify-tor] Tor circuit NOT established within 60s")
        return False

    # endregion FUNC_TorProxyInstaller_verify_tor_circuit

    # region FUNC_TorProxyInstaller_install_cron_healthcheck
    def install_cron_healthcheck(self, core_dir: Path, cron_file: Path) -> None:
        """Установка cron-джобы healthcheck (guard: hc-скрипт существует, cron уже установлен).

        ▶ ┌core_dir, cron_file┐ → ◇ hc_script missing? SKIP → ◇ cron_file exists? SKIP
          → ○ write render_cron_line(...) + chmod 0644 → ⎋ None

        ## @purpose  install_cron_healthcheck() из install-tor-proxy.sh — паттерн install_cron_metrics
        ##            (helpers/system.py:186): идемпотентный cron-guard по существованию файла.
        ## @io — ⇥ core_dir: Path, cron_file: Path → ⎋ None
        ## @complexity — O(1)
        ## @invariants
        ##   - hc_script отсутствует (facts.path_isfile) → SKIP (cron не ставится)
        ##   - cron_file существует (facts.path_isfile) → SKIP (идемпотентность — не перезаписывает)
        ##   - Строка cron = render_cron_line(CRON_SCHEDULE, hc_script); mode 0644 (канон /etc/cron.d)
        ## @rationale — В Python CORE_DIR — абсолютный путь развёрнутого core/ (Path(__file__)),
        ##   детерминирован независимо от cwd (прежний shell-heredoc с PLATFORM_ROOT раскрывал
        ##   переменную и ломался после rsync в /opt/core/).
        """
        hc_script = core_dir / "internal" / "healthcheck" / "tor-proxy-healthcheck.sh"

        if not self.facts.path_isfile(hc_script):
            _log_step("cron-hc", "SKIP", f"Healthcheck script not found at {hc_script} — cron not installed")
            logger.info("[IMP:9][tor-install][cron-hc] Healthcheck script missing — cron skipped (no-op)")
            return

        if self.facts.path_isfile(cron_file):
            _log_step("cron-hc", "SKIP", "Cron healthcheck already installed")
            logger.info("[IMP:9][tor-install][cron-hc] Cron healthcheck already installed (idempotent no-op)")
            return

        cron_line = render_cron_line(CRON_SCHEDULE, hc_script)
        cron_file.write_text(cron_line, encoding="utf-8")
        cron_file.chmod(0o644)
        _log_step("cron-hc", "DONE", f"Healthcheck cron installed: {cron_file}")
        logger.info("[IMP:9][tor-install][cron-hc] Cron healthcheck installed: %s", cron_file)

    # endregion FUNC_TorProxyInstaller_install_cron_healthcheck

    # region FUNC_TorProxyInstaller_configure_firewall_docker
    def configure_firewall_docker(self) -> None:
        """iptables-правило Docker bridge → Privoxy:8118 (catch-all 172.16.0.0/12, идемпотентно).

        ▶ ┌None┐ → ○ iptables -C (guard) → ◇ rc!=0? iptables -I (fatal) │ уже существует → ⎋ None

        ## @purpose  configure_firewall_docker() из install-tor-proxy.sh — single catch-all rule
        ##            для ВСЕХ Docker bridge-сетей (172.16.0.0/12, RFC 1918) — per-interface правила
        ##            пропускают сети, созданные после bootstrap. Idempotency: iptables -C guard.
        ##            Структура правила — build_firewall_rule() (чистый генератор, W4c).
        ## @io — ⇥ None → ⎋ None ⚡ CommandFailedError (iptables -I провалился, set -e канон)
        ## @complexity — O(1) subprocess × 1-2
        ## @invariants
        ##   - -C (check) — graceful; rc!=0 (правила нет) → -I (add) — fatal при провале
        ##   - Повторный запуск: -C rc=0 → "rule already exists" (no-op)
        ##   - UFW НЕ трогается: iptables catch-all + Privoxy permit-access достаточно (TRAP[DECISION])
        """
        _log_step(
            "firewall",
            "START",
            f"Configuring firewall for Docker bridge → Privoxy:{PRIVOXY_PORT} (catch-all 172.16.0.0/12)",
        )

        rule = build_firewall_rule()
        check = self._run(["iptables", "-C", "INPUT", *rule], check=False)

        if check.returncode != 0:
            self._run(["iptables", "-I", "INPUT", *rule], check=True)
            _log_step(
                "firewall", "INFO", f"iptables: allowed all Docker bridges ({FIREWALL_SRC_NET} → :{FIREWALL_DPORT})"
            )
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

    # endregion FUNC_TorProxyInstaller_configure_firewall_docker

    # region FUNC_TorProxyInstaller_run
    def run(self, bridges_file: str | None = None, skip_verify: bool = False) -> int:
        """Полная оркестрация установки (экс-тело main) — exit-контракт shared/contracts.py.

        ▶ ┌bridges_file, skip_verify┐ → ◇ root? → ⚡ banner → ○ install_packages → ○ write_torrc
          → ○ write_privoxy_config → ○ enable_services → ◇ verify_services_active? → ○ configure_firewall_docker
          → ○ install_cron_healthcheck → ◇ verify_tor_circuit? → ⎋ 0 │ ⎋ 1

        ## @purpose  Шаги установки в канонном порядке (shell main-порядок сохранён байт-в-байт);
        ##            root-guard и обработка fail-fast исключений — внутри run() (W4c: тестируемо
        ##            через DI без monkeypatch шагов).
        ## @io — ⇥ bridges_file: str|None, skip_verify: bool → ⎋ int (0 = ok, 1 = generic error)
        ## @complexity — O(P + T) — P=шаги оркестрации, T=verify circuit retry
        ## @invariants
        ##   - sys.exit НЕ вызывается — run() возвращает int (канон core/AGENTS.md)
        ##   - Любой fail-fast шаг (TorSetupError/TorTransportError/CommandFailedError/OSError) → exit 1
        ##   - verify_services_active failure → exit 1 (set -e канон)
        ##   - verify_tor_circuit failure → exit 1 + CRITICAL (non-fatal для bootstrap-фазы)
        ##   - facts.is_root() — root-guard (W4b/W4c DI: тесты передают fake)
        """
        if not self.facts.is_root():
            logger.error("[IMP:10][tor-install][main] ERROR: must run as root")
            return EXIT_GENERIC

        logger.info("[IMP:9][tor-install][main] ====================================")
        logger.info("[IMP:9][tor-install][main] Tor + Privoxy Installer START")
        logger.info("[IMP:9][tor-install][main] ====================================")

        # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
        try:
            self.install_packages()
            self.write_torrc(Path(DEFAULT_TOR_CONFIG), bridges_file, TORRC_TEMPLATE)
            self.write_privoxy_config(Path(DEFAULT_PRIVOXY_CONFIG))
            self.enable_services()
            if not self.verify_services_active():
                return EXIT_GENERIC
            self.configure_firewall_docker()
            self.install_cron_healthcheck(Path(__file__).resolve().parent.parent.parent, Path(DEFAULT_CRON_FILE))
        except (tor_setup.TorSetupError, tor_transport.TorTransportError, CommandFailedError, OSError) as exc:
            logger.error("[IMP:10][tor-install][main] %s", exc)
            return EXIT_GENERIC

        if self.verify_tor_circuit(skip=skip_verify):
            logger.info("[IMP:9][tor-install][main] Tor + Privoxy installation complete — circuit verified")
            return EXIT_OK
        logger.error("[IMP:10][tor-install][main] CRITICAL: Tor circuit failed to establish")
        logger.error(
            "[IMP:10][tor-install][main] Telegram notifications will be unavailable until bridges are configured"
        )
        return EXIT_GENERIC

    # endregion FUNC_TorProxyInstaller_run


# endregion CLASS_TorProxyInstaller


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
                msg = "--tor-bridges-file requires a value"
                raise TorInstallUsageError(msg) from None
        elif arg == "--skip-tor-verify":
            skip_verify = True
        else:
            logger.error("[IMP:10][tor-install][args] ERROR: Unknown argument: %s", arg)
            msg = f"Unknown argument: {arg}"
            raise TorInstallUsageError(msg)
    return bridges_file, skip_verify


# endregion FUNC__parse_args


# region FUNC_main
def main(
    argv: list[str] | None = None,
    *,
    facts: EnvironmentFacts | None = None,
    installer_cls: type[TorProxyInstaller] | None = None,
) -> int:
    """CLI: `python3 -m core.internal.bootstrap.install_tor_proxy [--tor-bridges-file F] [--skip-tor-verify]`.

    ▶ ┌argv┐ → ◇ args valid? → ⊕ TorProxyInstaller(facts) → ○ installer.run(bridges_file, skip_verify) → ⎋ 0 │ ⎋ 1

    ## @purpose  Composition root (W4c T4.3): парсит аргументы, создаёт TorProxyInstaller
    ##            (facts из параметра/дефолтов, runner/clock — дефолты) и делегирует run().
    ##            Exit-контракт shared/contracts.py: 0=ok, 1=generic error (byte-compat shell exit 0/1).
    ## @io — ⇥ argv: list[str] | None, facts: EnvironmentFacts | None (None = реальные системные),
    ##          installer_cls: type | None (DI, W-H DevPlan 163 — класс installer'а; None = TorProxyInstaller)
    ##          → ⎋ int
    ## @complexity — O(P + T) — P=шаги оркестрации, T=verify circuit retry
    ## @invariants
    ##   - sys.exit НЕ вызывается — main() возвращает int (канон core/AGENTS.md)
    ##   - TorInstallUsageError (unknown arg) → exit 1 до создания installer'а
    ##   - facts=None → default_env_facts() (реальные системные факты)
    ##   - installer_cls=None → TorProxyInstaller (поведение без изменений); тесты передают
    ##     субкласс-инсталлер (0 патчей модульного класса, W-H)
    ## @changes 2026-08-04 | DevPlan 127 W1 — Created
    ## @changes 2026-08-13 | DevPlan 160 W4b — +facts: EnvironmentFacts | None (root-guard DI)
    ## @changes 2026-08-13 | DevPlan 160 W4c — тело оркестрации перенесено в TorProxyInstaller.run()
    ## @changes 2026-08-13 | DevPlan 163 W-H — +installer_cls (DI вместо патча модульного класса)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        bridges_file, skip_verify = _parse_args(argv)
    except TorInstallUsageError:
        return EXIT_GENERIC

    installer_cls_impl = TorProxyInstaller if installer_cls is None else installer_cls
    installer = installer_cls_impl(facts=facts)
    return installer.run(bridges_file=bridges_file, skip_verify=skip_verify)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
