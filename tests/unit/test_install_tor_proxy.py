# GREP_SUMMARY: test-install-tor-proxy install_tor_proxy.py W1 W4c idempotency exit-codes DI runner facts clock FakeCommandRunner FakeFacts FakeClock generators compose-torrc render-cron-line build-firewall-rule torrc privoxy systemd iptables cron circuit-verify LDD IMP:9
# STRUCTURE: ▶ 30+ tests → ○ генераторы (compose_torrc/render_cron_line/build_firewall_rule — чистые, точные строки) → ○ root/args (exit 1)
#            → ○ main-оркестрация (subclass-оверрайды, 1 patch) → ○ полный сценарий (FakeCommandRunner, sequence/idempotency)
#            → ○ write_torrc (template/fallback/bridges/fail-fast/idempotent) → ○ privoxy (idempotent) → ○ systemd (sequence/fatal)
#            → ○ verify active/circuit → ○ cron (write/skip/idempotent) → ○ firewall (-C/-I guard) → ⎋ PASS

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(TOR-INSTALL-PYTHON):3; TECH(PYTEST):2]
## @purpose  Unit-тесты core/internal/bootstrap/install_tor_proxy.py (DevPlan 127 W1 + 160 W4c T4.3).
##           W1-покрытие: exit-контракт (0/1, root/unknown-arg), идемпотентность шагов
##           (torrc/privoxy/cron/firewall), systemd sequence + fatal restart, iptables -C/-I guard,
##           circuit-verify retry/skip, порядок main-оркестрации. W4c-покрытие: чистые генераторы
##           конфигов (compose_torrc/render_cron_line/build_firewall_rule) + оркестратор
##           TorProxyInstaller через DI (FakeCommandRunner/FakeFacts/FakeClock)
##           в файле: 4 (замена TorProxyInstaller-класса в main-тестах) против 37 до W4c.
## @scope    Native pytest, НИКАКИХ subprocess для бизнес-логики: subprocess-канал изолирован
##           FakeCommandRunner (DI-конструктор TorProxyInstaller). tmp_path — Zero Hardcode.
## @invariants
##   - Каждый тест: caplog.set_level(INFO) + assert IMP:9-лог присутствует (LDD telemetry)
##   - Файлы конфигов — tmp_path; реальные /etc/... НЕ трогаются
##   - tor_setup.install_tor_packages в полном сценарии — оверрайд subclass'а (не вызов apt)
##   - FakeClock — sleep no-op (retry-циклы не замедляют тесты)
##   - nodeid'ы всех 26 прежних тестов сохранены (inventory-гейт Anti-Tamper T18)
## @rationale W1 (DevPlan 127): бизнес-логика оркестрации тестируема только в Python (DI);
##            shell-фасад остаётся байт-совместимым по exit-кодам (0/1) и аргументам.
##            W4c (DevPlan 160 AF-4/T4.3): конструкторная DI убирает патчи шагов —
##            тесты оркестратора прогоняют реальные методы с Fake-каналами.
## @modulemap
##   - test_compose_torrc_*             [W:30] чистый генератор torrc (base/fallback/section)
##   - test_render_cron_line_*          [W:10] чистый генератор cron-строки
##   - test_build_firewall_rule_*       [W:20] чистый генератор iptables-структуры
##   - test_main_*                      [W:120] exit-контракт + порядок оркестрации (subclass)
##   - test_full_flow_*                 [W:100] полный сценарий через FakeCommandRunner
##   - test_write_torrc_*               [W:100] template/fallback/bridges/fail-fast/идемпотентность
##   - test_write_privoxy_config_*      [W:40]  idempotent no-op (реальный privoxy_config)
##   - test_enable_services_*           [W:60]  systemctl sequence + fatal restart
##   - test_verify_services_active_*    [W:30]  active-статусы
##   - test_verify_tor_circuit_*        [W:70]  retry 12×5s / skip
##   - test_install_cron_*              [W:60]  write/chmod/skip/idempotent
##   - test_configure_firewall_*        [W:50]  iptables -C/-I guard
## @usecases  Разработчик: pytest tests/unit/test_install_tor_proxy.py — регрессия после правок
##            оркестрации; QA: проверка exit-контракта и идемпотентности без root/VPS.
# endregion MODULE_CONTRACT

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from core.internal.bootstrap import install_tor_proxy, tor_transport
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


def _module_contract():
    pass


# ── Test data ───────────────────────────────────────────────────────────────────

_FALLBACK_TORRC = install_tor_proxy.FALLBACK_TORRC

# Канонный iptables-rule (build_firewall_rule default) — для точных ассертов полного сценария
_IPTABLES_RULE = install_tor_proxy.build_firewall_rule()


@dataclass
class FakeResult:
    """Fake subprocess.CompletedProcess (DI-канал runner)."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


# region CLASS_FakeCommandRunner
class FakeCommandRunner:
    """CommandRunner-fake: записывает команды + scripted результаты (W4c DI).

    queue-режим: results=[(rc, stdout), ...] — pop по порядку;
    matcher-режим: results=callable(cmd) -> (rc, stdout) — per-command script.
    check=True + rc!=0 → PlatformFatalError (как run_subprocess канон) — _run транслирует
    в CommandFailedError (тестируется реальный путь трансляции).

    ## @purpose — Полная замена run_command: эффект-ассерты через runner.calls (DI).
    ## @io — ⇥ run(cmd, timeout, check, ...) → ⎋ FakeResult ⚡ PlatformFatalError (check=True rc!=0)
    ## @complexity — O(1) per вызов
    """

    def __init__(self, results: list | None = None, default: tuple[int, str] = (0, "")) -> None:
        self.calls: list[list[str]] = []
        self._results = results
        self._default = default

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int | None = None,  # ruff: ignore[ARG002]
        check: bool = False,
        non_fatal: bool = False,  # ruff: ignore[ARG002]
        fatal_rc: tuple[int, ...] = (),  # ruff: ignore[ARG002]
    ) -> FakeResult:
        self.calls.append(list(cmd))
        if isinstance(self._results, list):
            rc, out = self._results.pop(0) if self._results else self._default
        elif callable(self._results):
            rc, out = self._results(cmd)
        else:
            rc, out = self._default
        if check and rc != 0:
            msg = f"Command {' '.join(cmd)} failed (exit={rc}): {out}"
            raise PlatformFatalError(msg)
        return FakeResult(rc, out)


# endregion CLASS_FakeCommandRunner


# region CLASS_FakeFacts
class FakeFacts:
    """EnvironmentFacts-fake (W4b/W4c): is_root/which/path_isfile — без os/shutil-патчей.

    ## @purpose — DI для facts-параметров TorProxyInstaller: root-guard, binary-детекция
    ##            транспортов (which), path_isfile (по умолчанию реальный os.path.isfile —
    ##            tmp_path-файлы видны без дополнительной настройки).
    ## @io — ⇥ is_root: bool, binaries: set[str], path_isfile: callable → ⎋ fake
    """

    def __init__(self, is_root: bool = True, binaries: set[str] | None = None, path_isfile=None) -> None:
        self._is_root = is_root
        self._binaries = set(binaries or ())
        self._path_isfile = path_isfile if path_isfile is not None else os.path.isfile

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:
        return binary if binary in self._binaries else None

    def path_isfile(self, path) -> bool:
        return self._path_isfile(str(path))


# endregion CLASS_FakeFacts


# region CLASS_FakeClock
class FakeClock:
    """clock-fake: sleep no-op + счётчик (W4c DI — тесты не спят).

    ## @purpose — Подмена time.sleep через clock-параметр TorProxyInstaller:
    ##            enable_services (3s) и verify_tor_circuit (5s) вызывают clock(seconds).
    ## @io — ⇥ __call__(seconds) → ⎋ None (счётчик sleep_calls/sleep_seconds)
    """

    def __init__(self) -> None:
        self.sleep_calls = 0
        self.sleep_seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleep_calls += 1
        self.sleep_seconds.append(seconds)


# endregion CLASS_FakeClock


# region FUNC__make_installer
def _make_installer(
    runner: FakeCommandRunner | None = None,
    *,
    is_root: bool = True,
    binaries: set[str] | None = None,
    clock: FakeClock | None = None,
    dropin_fn: Callable[[Path], None] | None = None,
) -> install_tor_proxy.TorProxyInstaller:
    """Собрать TorProxyInstaller с fakes (DI-конструктор; 0 патчей).

    ▶ ┌runner?, is_root, binaries, clock, dropin_fn┐ → ⊕ TorProxyInstaller(...) → ⎋ installer

    ## @purpose — Единая фабрика installer'а для всех тестов оркестратора.
    ## @io — ⇥ runner (None = пустой FakeCommandRunner), is_root, binaries, clock (None = FakeClock),
    ##          dropin_fn (None = реальный configure_privoxy_restart_dropin) → ⎋ installer
    """
    return install_tor_proxy.TorProxyInstaller(
        runner=runner if runner is not None else FakeCommandRunner(),
        facts=FakeFacts(is_root=is_root, binaries=binaries),
        clock=clock if clock is not None else FakeClock(),
        dropin_fn=dropin_fn,
    )


# endregion FUNC__make_installer


# region FUNC__assert_imp9
def _assert_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """LDD telemetry: печать IMP:7-10 траектории + assert найден IMP:9-лог.

    ## @purpose  Anti-Illusion (RULES.md §TESTING): тест не молчит — печатает траекторию
    ##            и требует присутствия как минимум одного IMP:9-лога.
    ## @io — ⇥ caplog → ⎋ None (assert found)
    ## @complexity — O(R) — R = записи caplog
    """
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC__assert_imp9


# region FUNC__assert_log_event
def _assert_log_event(
    caplog: pytest.LogCaptureFixture,
    *,
    levelno: int,
    imp: int,
    keyword: str,
) -> None:
    """Структурная проверка лог-события: severity + IMP-код + факт события (DevPlan 139 W2).

    ## @purpose  Замена assert'ов на ТОЧНЫЕ строки логов: проверяем УРОВЕНЬ (levelno),
    ##            IMP-код и короткий факт события — НЕ полный форматированный текст.
    ## @io — ⇥ caplog; levelno (logging.*), imp (int), keyword (событие) → ⎋ None (assert)
    ## @complexity — O(R) — R = записи caplog
    """
    assert any(r.levelno == levelno and f"[IMP:{imp}]" in r.message and keyword in r.message for r in caplog.records), (
        f"Лог-событие не найдено: levelno={levelno} [IMP:{imp}] keyword={keyword!r}\n---\n{caplog.text}"
    )


# endregion FUNC__assert_log_event


# ── Чистые генераторы конфигов (W4c T4.3) ──────────────────────────────────────


# region FUNC_test_compose_torrc
@pytest.mark.parametrize(
    ("base", "section", "expected"),
    [
        # W4c template-ветка: base как есть при отсутствии section
        (
            "SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n",
            None,
            "SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n",
        ),
        # W4c fallback-ветка: base=None → FALLBACK_TORRC (shell heredoc parity)
        (None, None, _FALLBACK_TORRC),
        # W4c bridge-append: base + section + "\n" (прежний append f.write(section + "\n"))
        (
            "SOCKSPort 127.0.0.1:9050\n",
            "\nUseBridges 1\nClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy\n",
            "SOCKSPort 127.0.0.1:9050\n\nUseBridges 1\nClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy\n\n",
        ),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4c генератор compose_torrc: template/fallback/bridge-append
# · Scenario: (base, section) → ожидаемая строка: base-only, FALLBACK_TORRC, base+section+"\n"
# · Last fail: N/A (preventive — W1 контракты torrc template/fallback/append)
# · Remove if: write_torrc перестанет использовать compose_torrc
def test_compose_torrc(base, section, expected) -> None:
    """Чистая функция (no I/O, no logs) — точный строковый контракт без LDD (паттерн mutate_config)."""
    result = install_tor_proxy.compose_torrc(base, section)

    assert result == expected, f"compose_torrc({base!r}, {section!r}) → {result!r}"


# endregion FUNC_test_compose_torrc


# region FUNC_test_render_cron_line
# GUARD-PRESERVE (168): единственное покрытие функции render_cron_line (чистый генератор cron-строки)
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4c генератор: render_cron_line — точная строка cron
# · Scenario: schedule + script → "{schedule} {script}\n" (канон /etc/cron.d)
# · Last fail: N/A (preventive — W1 cron-контракт)
# · Remove if: cron-механика изменена
def test_render_cron_line() -> None:
    """Чистая функция — точная cron-строка без LDD (паттерн mutate_config)."""
    script = Path("/opt/platform/core/internal/healthcheck/tor-proxy-healthcheck.sh")

    result = install_tor_proxy.render_cron_line(install_tor_proxy.CRON_SCHEDULE, script)

    assert result == f"{install_tor_proxy.CRON_SCHEDULE} {script}\n", result


# endregion FUNC_test_render_cron_line


# region FUNC_test_build_firewall_rule
@pytest.mark.parametrize(
    ("dport", "src_net", "comment"),
    [
        # W4c дефолты: канонный iptables-args список (dport/src_net/comment из констант)
        (install_tor_proxy.FIREWALL_DPORT, install_tor_proxy.FIREWALL_SRC_NET, install_tor_proxy.FIREWALL_COMMENT),
        # W4c явные args: dport/src_net/comment переопределены
        ("9999", "10.0.0.0/8", "custom"),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4c генератор build_firewall_rule: дефолты + явные args
# · Scenario: (dport, src_net, comment) → точный iptables-args список
# · Last fail: N/A (preventive — W1 firewall-контракт)
# · Remove if: формат iptables-правила изменён
def test_build_firewall_rule(dport, src_net, comment) -> None:
    """Чистая функция — структура iptables-args без LDD (паттерн mutate_config)."""
    result = install_tor_proxy.build_firewall_rule(dport=dport, src_net=src_net, comment=comment)

    assert result == [
        "-p",
        "tcp",
        "--dport",
        dport,
        "-s",
        src_net,
        "-j",
        "ACCEPT",
        "-m",
        "comment",
        "--comment",
        comment,
    ], result


# endregion FUNC_test_build_firewall_rule


# ── exit-контракт: root / аргументы ────────────────────────────────────────────


# region FUNC_test_main_exit_contract
@pytest.mark.parametrize(
    ("args", "facts", "keyword"),
    [
        # W1 root-guard (security): exit 1 без root — IMP:10 "must run as root"
        ([], FakeFacts(is_root=False), "must run as root"),
        # W1 unknown-arg: exit 1 (shell parse_args byte-compat) — IMP:10 "Unknown argument: --bogus"
        (["--bogus"], FakeFacts(is_root=True), "Unknown argument"),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 exit-контракт main: root-guard + unknown-arg → exit 1
# · Scenario: (args, facts, keyword) → main() == EXIT_GENERIC + IMP:10-лог с keyword
# · Last fail: N/A (preventive — shell byte-compat exit 1)
# · Remove if: root-требование снято (небезопасно — systemctl/iptables требуют root) ИЛИ CLI-формат изменён
def test_main_exit_contract(caplog: pytest.LogCaptureFixture, args, facts, keyword) -> None:
    caplog.set_level(logging.INFO)

    rc = install_tor_proxy.main(args, facts=facts)

    assert rc == EXIT_GENERIC, f"Expected 1, got {rc}"
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword=keyword)
    _assert_imp9(caplog)


# endregion FUNC_test_main_exit_contract


# ── main-оркестрация (subclass-оверрайды; installer_cls DI) ─────────────────────


# region CLASS__StepOrderInstaller
class _StepOrderInstaller(install_tor_proxy.TorProxyInstaller):
    """Запись порядка шагов run() в class-level список (доступен после main()).

    ## @purpose — Проверка порядка оркестрации БЕЗ патчей шагов: main() создаёт
    ##            installer'а через installer_cls DI (0 патчей, W-H DevPlan 163),
    ##            шаги переопределены наследованием.
    """

    order: ClassVar[list[str]] = []

    def _record(self, name: str) -> bool:
        type(self).order.append(name)
        return True

    def install_packages(self) -> None:
        self._record("install_packages")

    def write_torrc(self, *_a, **_k) -> None:
        self._record("write_torrc")

    def write_privoxy_config(self, *_a, **_k) -> None:
        self._record("write_privoxy_config")

    def enable_services(self) -> None:
        self._record("enable_services")

    def verify_services_active(self) -> bool:
        return self._record("verify_services_active")

    def configure_firewall_docker(self) -> None:
        self._record("configure_firewall_docker")

    def install_cron_healthcheck(self, *_a, **_k) -> None:
        self._record("install_cron_healthcheck")

    def verify_tor_circuit(self, **_k) -> bool:
        return self._record("verify_tor_circuit")


# endregion CLASS__StepOrderInstaller


# region FUNC_test_main_flow_success_and_order
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 main: порядок шагов + exit 0
# · Scenario: все шаги успешны → main==0; порядок: packages → torrc → privoxy → services
# ·   → verify-active → firewall → cron → circuit
# · Last fail: N/A (preventive — оркестрационный контракт shell main)
# · Remove if: порядок оркестрации изменён намеренно
def test_main_flow_success_and_order(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _StepOrderInstaller.order = []

    rc = install_tor_proxy.main([], facts=FakeFacts(is_root=True), installer_cls=_StepOrderInstaller)

    assert rc == EXIT_OK, f"Expected 0, got {rc}"
    assert _StepOrderInstaller.order == [
        "install_packages",
        "write_torrc",
        "write_privoxy_config",
        "enable_services",
        "verify_services_active",
        "configure_firewall_docker",
        "install_cron_healthcheck",
        "verify_tor_circuit",
    ], f"Order mismatch: {_StepOrderInstaller.order}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="installation complete")
    _assert_imp9(caplog)


# endregion FUNC_test_main_flow_success_and_order


# region CLASS__CircuitFailInstaller
class _CircuitFailInstaller(install_tor_proxy.TorProxyInstaller):
    """Все шаги успешны, кроме verify_tor_circuit → False (main exit 1 + CRITICAL)."""

    def install_packages(self) -> None:
        pass

    def write_torrc(self, *_a, **_k) -> None:
        pass

    def write_privoxy_config(self, *_a, **_k) -> None:
        pass

    def enable_services(self) -> None:
        pass

    def verify_services_active(self) -> bool:
        return True

    def configure_firewall_docker(self) -> None:
        pass

    def install_cron_healthcheck(self, *_a, **_k) -> None:
        pass

    def verify_tor_circuit(self, **_k) -> bool:
        return False


# endregion CLASS__CircuitFailInstaller


# region FUNC_test_main_flow_circuit_failure
# GUARD-PRESERVE (168): единственное покрытие circuit-fail ветки main (exit 1 + CRITICAL)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit-fail: exit 1 + CRITICAL
# · Scenario: verify_tor_circuit False → main==1; CRITICAL-логи (non-fatal для bootstrap)
# · Last fail: N/A (preventive — shell exit 1 канон)
# · Remove if: поведение при недоступной цепи изменено
def test_main_flow_circuit_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rc = install_tor_proxy.main([], facts=FakeFacts(is_root=True), installer_cls=_CircuitFailInstaller)

    assert rc == EXIT_GENERIC
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="circuit failed to establish")
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="Telegram notifications will be unavailable")
    _assert_imp9(caplog)


# endregion FUNC_test_main_flow_circuit_failure


# region CLASS__StepFailureInstaller
class _StepFailureInstaller(install_tor_proxy.TorProxyInstaller):
    """install_packages fail-fast — CommandFailedError (main exit 1, set -e канон)."""

    def install_packages(self) -> None:
        msg = "systemctl restart tor failed (exit=1)"
        raise install_tor_proxy.CommandFailedError(msg)


# endregion CLASS__StepFailureInstaller


# region FUNC_test_main_step_failure_exit1
# GUARD-PRESERVE (168): единственное покрытие fail-fast ветки main (CommandFailedError → exit 1)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 fail-fast шаг: exit 1
# · Scenario: install_packages raises CommandFailedError → main==1 (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: fail-fast политика изменена
def test_main_step_failure_exit1(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rc = install_tor_proxy.main([], facts=FakeFacts(is_root=True), installer_cls=_StepFailureInstaller)

    assert rc == EXIT_GENERIC
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="systemctl restart tor failed")
    _assert_imp9(caplog)


# endregion FUNC_test_main_step_failure_exit1


# region CLASS__CountingInstaller
class _CountingInstaller(install_tor_proxy.TorProxyInstaller):
    """Подсчёт вызовов шагов run() (идемпотентность: каждый шаг ровно 1 раз за запуск)."""

    counts: ClassVar[dict[str, int]] = {}

    def _count(self, name: str) -> bool:
        type(self).counts[name] = type(self).counts.get(name, 0) + 1
        return True

    def install_packages(self) -> None:
        self._count("install_packages")

    def write_torrc(self, *_a, **_k) -> None:
        self._count("write_torrc")

    def write_privoxy_config(self, *_a, **_k) -> None:
        self._count("write_privoxy_config")

    def enable_services(self) -> None:
        self._count("enable_services")

    def verify_services_active(self) -> bool:
        return self._count("verify_services_active")

    def configure_firewall_docker(self) -> None:
        self._count("configure_firewall_docker")

    def install_cron_healthcheck(self, *_a, **_k) -> None:
        self._count("install_cron_healthcheck")

    def verify_tor_circuit(self, **_k) -> bool:
        return self._count("verify_tor_circuit")


# endregion CLASS__CountingInstaller


# region FUNC_test_main_twice_idempotent
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 идемпотентность: main дважды = тот же результат
# · Scenario: полный успешный main, повторный запуск → оба 0, одинаковое число вызовов шагов
# · Last fail: N/A (preventive — идемпотентность канон bootstrap)
# · Remove if: оркестрация перестанет быть идемпотентной (недопустимо)
def test_main_twice_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _CountingInstaller.counts = {}

    assert install_tor_proxy.main([], facts=FakeFacts(is_root=True), installer_cls=_CountingInstaller) == EXIT_OK
    first = dict(_CountingInstaller.counts)
    assert install_tor_proxy.main([], facts=FakeFacts(is_root=True), installer_cls=_CountingInstaller) == EXIT_OK
    # Идемпотентность: каждый запуск выполняет КАЖДЫЙ шаг ровно один раз (детерминированно)
    expected = {k: v + 1 for k, v in first.items()}
    assert _CountingInstaller.counts == expected, (
        f"Повторный запуск выполнил шаги не 1:1: {first} → {dict(_CountingInstaller.counts)}"
    )
    _assert_imp9(caplog)


# endregion FUNC_test_main_twice_idempotent


# ── Полный сценарий через FakeCommandRunner (реальные runner-методы) ───────────


# region CLASS__FullFlowInstaller
class _FullFlowInstaller(install_tor_proxy.TorProxyInstaller):
    """Полный сценарий: I/O-шаги (packages/torrc/privoxy/cron) — record; runner-шаги — реальные.

    ## @purpose — Полный прогон installer.run() БЕЗ системных вызовов: tor_setup-шаг и
    ##            файловые шаги с фиксированными /etc-путями оверрайдятся (иначе писали бы
    ##            в реальную ФС), а systemd/verify/firewall/circuit исполняются реально через
    ##            FakeCommandRunner — эффект-ассерты на последовательность команд.
    """

    steps: ClassVar[list[str]] = []

    def install_packages(self) -> None:
        type(self).steps.append("install_packages")

    def write_torrc(self, *_a, **_k) -> None:
        type(self).steps.append("write_torrc")

    def write_privoxy_config(self, *_a, **_k) -> None:
        type(self).steps.append("write_privoxy_config")

    def install_cron_healthcheck(self, *_a, **_k) -> None:
        type(self).steps.append("install_cron_healthcheck")


# endregion CLASS__FullFlowInstaller


# region FUNC_test_full_flow_command_sequence
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4c полный сценарий: последовательность команд
# · Scenario: FakeCommandRunner (matcher) + FakeClock → run() == 0; runner.calls == канонный
# ·   порядок systemctl → is-active → iptables -C/-I → curl; clock.sleep_calls == 1 (3s)
# · Last fail: N/A (preventive — W1 порядок main-оркестрации через DI)
# · Remove if: последовательность systemd/firewall/circuit изменена
def test_full_flow_command_sequence(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    # 162 W3-3: drop-in пишется на реальный /etc/systemd — изолируем через DI-заглушку
    _FullFlowInstaller.steps = []

    def matcher(cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "curl":
            return (0, "Congratulations. This browser is configured to use Tor.")
        if cmd[0] == "iptables" and cmd[1] == "-C":
            return (1, "")  # правило отсутствует → -I
        return (0, "")

    runner = FakeCommandRunner(results=matcher)
    clock = FakeClock()
    installer = _FullFlowInstaller(runner=runner, facts=FakeFacts(is_root=True), clock=clock, dropin_fn=lambda _p: None)

    rc = installer.run()

    assert rc == EXIT_OK, f"Полный сценарий должен завершиться 0, got {rc}"
    assert runner.calls == [
        ["systemctl", "enable", "tor", "--quiet"],
        ["systemctl", "enable", "privoxy", "--quiet"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "tor"],
        ["systemctl", "restart", "privoxy"],
        ["systemctl", "is-active", "--quiet", "tor"],
        ["systemctl", "is-active", "--quiet", "privoxy"],
        ["iptables", "-C", "INPUT", *_IPTABLES_RULE],
        ["iptables", "-I", "INPUT", *_IPTABLES_RULE],
        [
            "curl",
            "--socks5-hostname",
            install_tor_proxy.TOR_SOCKS_HOST,
            "-s",
            "--max-time",
            "10",
            install_tor_proxy.VERIFY_URL,
        ],
    ], runner.calls
    assert clock.sleep_calls == 1, f"Пауза 3s между restart tor/privoxy: {clock.sleep_seconds}"
    assert _FullFlowInstaller.steps == [
        "install_packages",
        "write_torrc",
        "write_privoxy_config",
        "install_cron_healthcheck",
    ]
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="installation complete")
    _assert_imp9(caplog)


# endregion FUNC_test_full_flow_command_sequence


# region FUNC_test_full_flow_idempotent_firewall_guard
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4c идемпотентность: повторный run() — firewall no-op
# · Scenario: 1-й run: iptables -C rc=1 → -I; 2-й run: -C rc=0 → без -I (реальный -C/-I guard)
# · Last fail: N/A (preventive — W1 идемпотентность канон bootstrap)
# · Remove if: iptables-guard изменён
def test_full_flow_idempotent_firewall_guard(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    # 162 W3-3: drop-in пишется на реальный /etc/systemd — изолируем через DI-заглушку
    state = {"added": False}

    def matcher(cmd: list[str]) -> tuple[int, str]:
        if cmd[0] == "curl":
            return (0, "Congratulations.")
        if cmd[0] == "iptables" and cmd[1] == "-C":
            return (1, "") if not state["added"] else (0, "")
        if cmd[0] == "iptables" and cmd[1] == "-I":
            state["added"] = True
            return (0, "")
        return (0, "")

    runner = FakeCommandRunner(results=matcher)
    installer = _FullFlowInstaller(
        runner=runner, facts=FakeFacts(is_root=True), clock=FakeClock(), dropin_fn=lambda _p: None
    )

    assert installer.run() == EXIT_OK
    first = list(runner.calls)
    assert any(c[1] == "-I" for c in first), "1-й запуск должен добавить iptables-правило (-I)"

    assert installer.run() == EXIT_OK
    second = runner.calls[len(first) :]
    assert not any(c[1] == "-I" for c in second), "повторный запуск не должен добавлять правило (no-op)"
    assert any(c[1] == "-C" for c in second), "повторный запуск всё же проверяет правило (-C guard)"
    _assert_imp9(caplog)


# endregion FUNC_test_full_flow_idempotent_firewall_guard


# ── write_torrc ─────────────────────────────────────────────────────────────────


# region FUNC_test_write_torrc_template_used
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: template база без мостов
# · Scenario: template существует, bridges=None → torrc == template; "No bridges file"
# · Last fail: N/A (preventive)
# · Remove if: формат torrc изменён
def test_write_torrc_template_used(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n")
    tor_config = tmp_path / "torrc"
    installer = _make_installer()

    installer.write_torrc(tor_config, None, template)

    assert tor_config.read_text() == "SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="No bridges file")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_template_used


# region FUNC_test_write_torrc_fallback_inline
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: fallback inline при отсутствии template
# · Scenario: template отсутствует → torrc == FALLBACK_TORRC (shell heredoc parity)
# · Last fail: N/A (preventive)
# · Remove if: fallback-ветка удалена
def test_write_torrc_fallback_inline(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    tor_config = tmp_path / "torrc"
    missing_template = tmp_path / "no-template.template"
    installer = _make_installer()

    installer.write_torrc(tor_config, None, missing_template)

    assert tor_config.read_text() == _FALLBACK_TORRC
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Template not found")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_fallback_inline


# region FUNC_test_write_torrc_bridges_appended
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: мосты аппендятся (obfs4)
# · Scenario: bridges file c obfs4 Bridge; FakeFacts(binaries={"obfs4"}) — which DI
# ·   → torrc содержит UseBridges 1 + ClientTransportPlugin obfs4 + Bridge line
# · Last fail: N/A (preventive — контракт tor_transport 118 E1)
# · Remove if: формат bridge-секции изменён
def test_write_torrc_bridges_appended(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0\n")
    tor_config = tmp_path / "torrc"
    installer = _make_installer(binaries={"/usr/bin/obfs4proxy"})

    installer.write_torrc(tor_config, str(bridges), template)

    content = tor_config.read_text()
    assert "UseBridges 1" in content, content
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in content, content
    assert "Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0" in content, content
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Bridges appended")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_bridges_appended


# region FUNC_test_write_torrc_unknown_transport_failfast
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: unknown transport → fail-fast
# · Scenario: Bridge c неизвестным транспортом → TorTransportError (exit 1 канон)
# · Last fail: N/A (preventive — fail-fast канон 118 E1)
# · Remove if: unknown transport перестанет быть фатальным
def test_write_torrc_unknown_transport_failfast(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge mystery 1.2.3.4:443 XYZ\n")
    tor_config = tmp_path / "torrc"
    installer = _make_installer()

    with pytest.raises(tor_transport.TorTransportError):
        installer.write_torrc(tor_config, str(bridges), template)
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Unknown transport")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_unknown_transport_failfast


# region FUNC_test_write_torrc_idempotent
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: повторный запуск = тот же конфиг
# · Scenario: write_torrc дважды с мостами → содержимое байт-идентично
# · Last fail: N/A (preventive — идемпотентность канон)
# · Remove if: write_torrc перестанет быть детерминированным
def test_write_torrc_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0\n")
    tor_config = tmp_path / "torrc"
    installer = _make_installer(binaries={"/usr/bin/obfs4proxy"})

    installer.write_torrc(tor_config, str(bridges), template)
    first = tor_config.read_text()
    installer.write_torrc(tor_config, str(bridges), template)

    assert tor_config.read_text() == first, "Повторный запуск изменил torrc (не идемпотентно)"
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_idempotent


# ── privoxy ─────────────────────────────────────────────────────────────────────


# region FUNC_test_write_privoxy_config_idempotent
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 privoxy: повторный запуск = no-op
# · Scenario: первый вызов мутирует конфиг (listen-address 0.0.0.0), второй — не меняет
# · Last fail: N/A (preventive — идемпотентный мутатор 119 D3)
# · Remove if: privoxy_config перестанет быть идемпотентным
def test_write_privoxy_config_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    config = tmp_path / "config"
    config.write_text("forward-socks5t / 127.0.0.1:9050 .\n")
    installer = _make_installer()

    installer.write_privoxy_config(config)
    first = config.read_text()
    assert "listen-address 0.0.0.0:8118" in first, first

    installer.write_privoxy_config(config)

    assert config.read_text() == first, "Повторный запуск изменил privoxy-конфиг (не идемпотентно)"
    assert not any("FAIL" in r.message for r in caplog.records), caplog.text
    _assert_imp9(caplog)


# endregion FUNC_test_write_privoxy_config_idempotent


# region FUNC_test_write_privoxy_config_dpkg_double_space_upgrade
# 🧪 TRAP[TEST] · 2026-08-07 · 142 B33 (R5) · dpkg-конфиг «listen-address  127.0.0.1:8118» (ДВА пробела)
# · Scenario: точный replace (один пробел) не матчил dpkg-формат → upgrade молча пропускался →
# ·   φ11 W6 re-apply «No changes needed» при протухшем 127.0.0.1 (grafana telegram мёртв)
# · Last fail: 2026-08-07 (node-update --force, privoxy-config idempotent no-op)
# · Remove if: mutate_config перестанет апгрейдить listen-address через regex
def test_write_privoxy_config_dpkg_double_space_upgrade(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """B33: dpkg-конфиг (двойной пробел) — listen-address upgrade до 0.0.0.0:8118."""
    caplog.set_level(logging.INFO)
    config = tmp_path / "config"
    # Ubuntu dpkg-формат: два пробела после listen-address
    config.write_text(
        "listen-address  127.0.0.1:8118\nlisten-address  [::1]:8118\nforward-socks5t / 127.0.0.1:9050 .\n"
    )
    installer = _make_installer()

    installer.write_privoxy_config(config)
    first = config.read_text()
    assert "listen-address 0.0.0.0:8118" in first, f"dpkg 127.0.0.1 (2 пробела) должен быть апгрейждён: {first}"
    assert "listen-address  127.0.0.1:8118" not in first, "старый 127.0.0.1 не должен остаться"

    # Идемпотентность: повторный вызов — no-op
    installer.write_privoxy_config(config)
    assert config.read_text() == first, "повторный запуск изменил конфиг (не идемпотентно)"
    _assert_imp9(caplog)


# endregion FUNC_test_write_privoxy_config_dpkg_double_space_upgrade


# ── systemd ─────────────────────────────────────────────────────────────────────


# region FUNC_test_enable_services_command_sequence
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 systemd: sequence enable→restart tor→sleep→restart privoxy
# · Scenario: FakeCommandRunner; FakeClock — 4 команды в канонном порядке + 1 sleep (3s)
# · Last fail: N/A (preventive)
# · Remove if: последовательность systemd изменена
def test_enable_services_command_sequence(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner()
    clock = FakeClock()
    installer = _make_installer(runner=runner, clock=clock, dropin_fn=lambda _p: None)
    # 162 W3-3: drop-in пишется на реальный /etc/systemd — изолируем через DI-заглушку

    installer.enable_services()

    assert runner.calls == [
        ["systemctl", "enable", "tor", "--quiet"],
        ["systemctl", "enable", "privoxy", "--quiet"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "tor"],
        ["systemctl", "restart", "privoxy"],
    ], runner.calls
    assert clock.sleep_calls == 1 and clock.sleep_seconds == [install_tor_proxy.SERVICE_RESTART_SLEEP_SEC]
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="restarted")
    _assert_imp9(caplog)


# endregion FUNC_test_enable_services_command_sequence


# region FUNC_test_enable_services_restart_failure_fatal
# GUARD-PRESERVE (168): единственное покрытие error-ветки enable_services (restart fail → CommandFailedError)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 systemd: restart tor fail → CommandFailedError
# · Scenario: enable rc=0; restart tor rc=1 c check=True → PlatformFatalError → CommandFailedError (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: restart перестанет быть фатальным
def test_enable_services_restart_failure_fatal(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(0, ""), (0, ""), (0, ""), (1, "Failed to restart tor.service"), (0, "")])
    installer = _make_installer(runner=runner, dropin_fn=lambda _p: None)
    # 162 W3-3: drop-in пишется на реальный /etc/systemd — изолируем через DI-заглушку

    with pytest.raises(install_tor_proxy.CommandFailedError):
        installer.enable_services()
    # 4 вызова до fail: enable tor, enable privoxy, daemon-reload (W3-3), restart tor
    assert len(runner.calls) == 4, f"Остановка после restart tor fail: {runner.calls}"
    _assert_imp9(caplog)


# endregion FUNC_test_enable_services_restart_failure_fatal


# region FUNC_test_configure_privoxy_restart_dropin (DevPlan 162 W3-3)
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W3-3 privoxy drop-in: создание Restart=on-failure
# · Scenario: drop-in отсутствует → mkdir parents + write [Service]\nRestart=on-failure
# · Last fail: 2026-08-13 — privoxy.service Restart=no на проде (тор вниз → нотификации мертвы)
# · Remove if: restart-политика privoxy изменена
def test_configure_privoxy_restart_dropin_creates(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    dropin = tmp_path / "privoxy.service.d" / "99-platform-restart.conf"

    install_tor_proxy.configure_privoxy_restart_dropin(dropin)

    content = dropin.read_text()
    assert content == "[Service]\nRestart=on-failure\n", content
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="drop-in written")
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W3-3 privoxy drop-in: идемпотентность (no-op)
# · Scenario: drop-in уже существует → не перезаписывается; SKIP-лог
# · Last fail: N/A (новый кейс DevPlan 162 W3-3 — паттерн configure_systemd_override)
# · Remove if: guard по существованию удалён
def test_configure_privoxy_restart_dropin_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    dropin = tmp_path / "privoxy.service.d" / "99-platform-restart.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("CUSTOM-UNTOUCHED\n")

    install_tor_proxy.configure_privoxy_restart_dropin(dropin)

    assert dropin.read_text() == "CUSTOM-UNTOUCHED\n", "существующий drop-in не перезаписывается"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="already exists")
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W3-3 enable_services пишет drop-in ПЕРЕД restart
# · Scenario: enable_services() вызывает configure_privoxy_restart_dropin с канонным путём
# · Last fail: 2026-08-13 — drop-in нигде не создавался (privoxy Restart=no)
# · Remove if: W3-3 механика изменена
def test_enable_services_writes_restart_dropin_before_restart(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner()
    clock = FakeClock()
    seen: list[Path] = []
    installer = _make_installer(runner=runner, clock=clock, dropin_fn=lambda p: seen.append(Path(p)))

    installer.enable_services()

    assert seen == [Path(install_tor_proxy.PRIVOXY_RESTART_DROPIN_DEFAULT)], (
        "drop-in должен писаться с канонным путём (99-platform-restart.conf)"
    )
    assert sum(1 for c in runner.calls if "daemon-reload" in c) == 1, "daemon-reload обязан вызываться"
    assert runner.calls[-1] == ["systemctl", "restart", "privoxy"], "restart privoxy ПОСЛЕ daemon-reload"
    _assert_imp9(caplog)


# endregion FUNC_test_configure_privoxy_restart_dropin


# ── verify services active ──────────────────────────────────────────────────────


# region FUNC_test_verify_services_active
@pytest.mark.parametrize(
    ("results", "expected", "log_keyword"),
    [
        # W1: оба сервиса active (is-active rc=0) → True
        ([(0, ""), (0, "")], True, None),
        # W1: tor NOT active (is-active rc=1) → False (set -e канон → main exit 1)
        ([(1, ""), (0, "")], False, "Tor: NOT active"),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 verify-active: оба active → True / tor NOT active → False
# · Scenario: (results, expected) → verify_services_active() == expected; ровно 2 is-active вызова
# · Last fail: N/A (preventive)
# · Remove if: критерий активного сервиса изменён
def test_verify_services_active(results, expected, log_keyword, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=results)
    installer = _make_installer(runner=runner)

    assert installer.verify_services_active() is expected
    assert len(runner.calls) == 2
    if log_keyword:
        _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword=log_keyword)
    _assert_imp9(caplog)


# endregion FUNC_test_verify_services_active


# ── verify tor circuit ──────────────────────────────────────────────────────────


# region FUNC_test_verify_tor_circuit_success
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: "Congratulations" → True
# · Scenario: curl stdout содержит Congratulations на 1-й попытке → True, IMP:9 established
# · Last fail: N/A (preventive)
# · Remove if: критерий проверки цепи изменён
def test_verify_tor_circuit_success(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(0, "Congratulations. This browser is configured to use Tor.")])
    installer = _make_installer(runner=runner)

    assert installer.verify_tor_circuit() is True
    assert len(runner.calls) == 1, f"Успех должен быть на 1-й попытке: {runner.calls}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="circuit established")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_success


# region FUNC_test_verify_tor_circuit_retries_then_fails
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: 12 попыток без успеха → False
# · Scenario: stdout пуст → 12 curl-вызовов, 11 sleep, False, FAIL-лог
# · Last fail: N/A (preventive)
# · Remove if: retry-политика проверки цепи изменена
def test_verify_tor_circuit_retries_then_fails(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(0, "")] * install_tor_proxy.VERIFY_MAX_ATTEMPTS)
    clock = FakeClock()
    installer = _make_installer(runner=runner, clock=clock)

    assert installer.verify_tor_circuit() is False
    assert len(runner.calls) == install_tor_proxy.VERIFY_MAX_ATTEMPTS, f"Ожидалось 12 попыток: {len(runner.calls)}"
    assert clock.sleep_calls == install_tor_proxy.VERIFY_MAX_ATTEMPTS - 1, "sleep между попытками"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="failed to establish circuit")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_retries_then_fails


# region FUNC_test_verify_tor_circuit_skipped
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: --skip-tor-verify → True без curl
# · Scenario: skip=True → True; runner НЕ вызывается
# · Last fail: N/A (preventive)
# · Remove if: флаг skip удалён
def test_verify_tor_circuit_skipped(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner()
    installer = _make_installer(runner=runner)

    assert installer.verify_tor_circuit(skip=True) is True
    assert runner.calls == [], f"skip=True не должен вызывать curl: {runner.calls}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="verification skipped")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_skipped


# ── cron healthcheck ────────────────────────────────────────────────────────────


# region FUNC_test_install_cron_healthcheck_writes
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 cron: запись + chmod 0644
# · Scenario: hc-скрипт существует → cron_file со строкой "*/5 * * * * root <core>/internal/healthcheck/...", mode 0644
# · Last fail: N/A (preventive)
# · Remove if: cron-механика изменена
def test_install_cron_healthcheck_writes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    hc = core_dir / "internal" / "healthcheck" / "tor-proxy-healthcheck.sh"
    hc.parent.mkdir(parents=True)
    hc.write_text("#!/usr/bin/env bash\n")
    cron_file = tmp_path / "cron" / "tor-proxy-healthcheck"
    cron_file.parent.mkdir()
    installer = _make_installer()

    installer.install_cron_healthcheck(core_dir, cron_file)

    expected = f"{install_tor_proxy.CRON_SCHEDULE} {hc}\n"
    assert cron_file.read_text() == expected, cron_file.read_text()
    assert cron_file.stat().st_mode & 0o777 == 0o644, oct(cron_file.stat().st_mode)
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Healthcheck cron installed")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_writes


# region FUNC_test_install_cron_healthcheck_idempotent
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 cron: повторный запуск = SKIP (no-op)
# · Scenario: cron_file уже существует → второй вызов SKIP, содержимое не меняется
# · Last fail: N/A (preventive — идемпотентность канон)
# · Remove if: cron-guard удалён
def test_install_cron_healthcheck_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    hc = core_dir / "internal" / "healthcheck" / "tor-proxy-healthcheck.sh"
    hc.parent.mkdir(parents=True)
    hc.write_text("#!/usr/bin/env bash\n")
    cron_file = tmp_path / "cron"
    cron_file.mkdir()
    cron = cron_file / "tor-proxy-healthcheck"
    cron.write_text("CUSTOM-UNTOUCHED\n")
    installer = _make_installer()

    installer.install_cron_healthcheck(core_dir, cron)

    assert cron.read_text() == "CUSTOM-UNTOUCHED\n", "Существующий cron НЕ должен перезаписываться"
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="already installed")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_idempotent


# region FUNC_test_install_cron_healthcheck_missing_script
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 cron: hc-скрипт отсутствует → SKIP, файл не создаётся
# · Scenario: hc_script нет → cron_file не создаётся; SKIP-лог
# · Last fail: N/A (preventive)
# · Remove if: guard по hc-скрипту удалён
def test_install_cron_healthcheck_missing_script(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    cron_file = tmp_path / "cron"
    installer = _make_installer()

    installer.install_cron_healthcheck(core_dir, cron_file)

    assert not cron_file.exists(), "Без hc-скрипта cron не должен создаваться"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Healthcheck script not found")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_missing_script


# ── firewall ────────────────────────────────────────────────────────────────────


# region FUNC_test_configure_firewall_docker_adds_once
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: -C guard → -I add один раз
# · Scenario: 1-й вызов -C rc=1 → -I; 2-й вызов -C rc=0 → без -I (идемпотентность)
# · Last fail: N/A (preventive)
# · Remove if: iptables-guard изменён
def test_configure_firewall_docker_adds_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(1, "")])
    installer = _make_installer(runner=runner)

    installer.configure_firewall_docker()
    assert len(runner.calls) == 2 and runner.calls[0][1] == "-C" and runner.calls[1][1] == "-I", runner.calls
    first_iptables = list(runner.calls)

    # 2-й вызов (свежий runner: правило уже существует) → только -C, без -I (идемпотентность)
    runner2 = FakeCommandRunner(results=[(0, "")])
    installer2 = _make_installer(runner=runner2)
    installer2.configure_firewall_docker()
    assert len(runner2.calls) == 1 and runner2.calls[0][1] == "-C", f"no-op (-C rc=0): {runner2.calls}"

    assert len(first_iptables) == 2
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_adds_once


# region FUNC_test_configure_firewall_docker_rule_exists
# GUARD-PRESERVE (168): единственное покрытие ветки "rule already exists" (лог IMP:8), дополняет adds_once
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: правило существует → только -C
# · Scenario: -C rc=0 → правило уже есть, -I не вызывается
# · Last fail: N/A (preventive)
# · Remove if: iptables-guard изменён
def test_configure_firewall_docker_rule_exists(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(0, "")])
    installer = _make_installer(runner=runner)

    installer.configure_firewall_docker()

    assert len(runner.calls) == 1 and runner.calls[0][1] == "-C", runner.calls
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="rule already exists")
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_rule_exists


# region FUNC_test_configure_firewall_docker_add_fatal
# GUARD-PRESERVE (168): единственное покрытие fatal-ветки iptables -I (set -e канон)
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: iptables -I fail → CommandFailedError
# · Scenario: -C rc=1, -I rc=1 c check=True → CommandFailedError (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: iptables add перестанет быть фатальным
def test_configure_firewall_docker_add_fatal(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    runner = FakeCommandRunner(results=[(1, ""), (1, "iptables: Permission denied")])
    installer = _make_installer(runner=runner)

    with pytest.raises(install_tor_proxy.CommandFailedError):
        installer.configure_firewall_docker()
    assert len(runner.calls) == 2, runner.calls
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_add_fatal
