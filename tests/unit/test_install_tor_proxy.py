# GREP_SUMMARY: test-install-tor-proxy install_tor_proxy.py W1 idempotency exit-codes DI monkeypatch subprocess torrc privoxy systemd iptables cron circuit-verify LDD IMP:9
# STRUCTURE: ▶ 24 tests → ○ root/args (exit 1) → ○ write_torrc (template/fallback/bridges/fail-fast/idempotent) → ○ privoxy (idempotent)
#            → ○ systemd (sequence/fatal) → ○ verify active/circuit → ○ cron (write/skip/idempotent) → ○ firewall (-C/-I guard) → ○ main flow (0/1, order) → ⎋ PASS

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(TOR-INSTALL-PYTHON):3; TECH(PYTEST):2]
## @purpose  Unit-тесты core/internal/bootstrap/install_tor_proxy.py (DevPlan 127 W1, S2) —
##           оркестрация Tor+Privoxy перенесена из install-tor-proxy.sh (321 LOC) в Python.
##           Покрытие: exit-контракт (0/1, root/unknown-arg), идемпотентность шагов
##           (torrc/privoxy/cron/firewall), systemd sequence + fatal restart, iptables -C/-I guard,
##           circuit-verify retry/skip, порядок main-оркестрации.
## @scope    Native pytest, НИКАКИХ subprocess для бизнес-логики: subprocess-канал изолирован
##           monkeypatch-заменой install_tor_proxy.run_command (DI-шов). tmp_path — Zero Hardcode.
## @invariants
##   - Каждый тест: caplog.set_level(INFO) + assert IMP:9-лог присутствует (LDD telemetry)
##   - Файлы конфигов — tmp_path; реальные /etc/... НЕ трогаются
##   - tor_setup.install_tor_packages / tor_transport.resolve_available_binaries monkeypatch-замены
##   - time.sleep monkeypatch-заменён (no-op) — retry-циклы не замедляют тесты
## @rationale W1 (DevPlan 127): бизнес-логика оркестрации тестируема только в Python (DI);
##            shell-фасад остаётся байт-совместимым по exit-кодам (0/1) и аргументам.
## @modulemap
##   - test_main_*                  [W:120] exit-контракт + порядок оркестрации (monkeypatch шагов)
##   - test_write_torrc_*           [W:100] template/fallback/bridges/fail-fast/идемпотентность
##   - test_write_privoxy_config_*  [W:40]  idempotent no-op (реальный privoxy_config)
##   - test_enable_services_*       [W:60]  systemctl sequence + fatal restart
##   - test_verify_services_active_* [W:30]  active-статусы
##   - test_verify_tor_circuit_*    [W:70]  retry 12×5s / skip
##   - test_install_cron_*          [W:60]  write/chmod/skip/idempotent
##   - test_configure_firewall_*    [W:50]  iptables -C/-I guard
## @usecases  Разработчик: pytest tests/unit/test_install_tor_proxy.py — регрессия после правок
##            оркестрации; QA: проверка exit-контракта и идемпотентности без root/VPS.
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.bootstrap import install_tor_proxy, tor_transport
from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK


def _module_contract():
    pass


# ── Test data ───────────────────────────────────────────────────────────────────

_FALLBACK_TORRC = install_tor_proxy.FALLBACK_TORRC


class FakeResult:
    """Fake subprocess.CompletedProcess (DI-канал run_command)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# region FUNC__assert_imp9
def _assert_imp9(caplog: pytest.LogCaptureFixture) -> None:
    """LDD telemetry: печать IMP:7-10 траектории + assert найден IMP:9-лог.

    ## @purpose  Anti-Illusion (RULES.md §TESTING): тест не молчит — печатает траекторию
    ##            и требует присутствия как минимум одного IMP:9-лога.
    ## @io — ⇥ caplog → ⎋ None (assert found)
    ## @complexity — O(R) — R = записи caplog
    """
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC__assert_imp9


# region FUNC__make_recorder
def _make_recorder(results: list[tuple[int, str]] | None = None):
    """Построить (calls, fake_run_command): recorder-функцию run_command с предзаданными rc/stdout.

    ▶ ┌results┐ → ⎋ (calls: list[list[str]], fake: Callable)

    ## @purpose — DI: подмена install_tor_proxy.run_command; results pop-аются по порядку
    ##            (rc, stdout); check=True при rc!=0 → CommandFailedError (как в проде).
    """
    calls: list[list[str]] = []
    queue = list(results) if results else []

    def fake(cmd: list[str], *, check: bool = False, timeout: int | None = None) -> FakeResult:
        calls.append(list(cmd))
        if queue:
            rc, out = queue.pop(0)
        else:
            rc, out = 0, ""
        if check and rc != 0:
            # Мимикрия продового run_command: IMP:10-лог перед raise (LDD telemetry в тестах)
            install_tor_proxy.logger.error("[IMP:10][tor-proxy][exec] Command failed (exit=%d): %s", rc, " ".join(cmd))
            raise install_tor_proxy.CommandFailedError(f"Command failed (exit={rc}): {' '.join(cmd)}")
        return FakeResult(rc, out)

    return calls, fake


# endregion FUNC__make_recorder


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
    ##            Exact-string ассерты краснеют от безобидных правок формулировок
    ##            и молчат при семантических поломках; структурные — устойчивы.
    ## @io — ⇥ caplog; levelno (logging.*), imp (int), keyword (событие) → ⎋ None (assert)
    ## @complexity — O(R) — R = записи caplog
    """
    assert any(r.levelno == levelno and f"[IMP:{imp}]" in r.message and keyword in r.message for r in caplog.records), (
        f"Лог-событие не найдено: levelno={levelno} [IMP:{imp}] keyword={keyword!r}\n---\n{caplog.text}"
    )


# endregion FUNC__assert_log_event


# ── exit-контракт: root / аргументы ────────────────────────────────────────────


# region FUNC_test_main_requires_root
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 root-guard: exit 1 без root
# · Scenario: os.geteuid()!=0 → main() == 1, IMP:10 "must run as root"
# · Last fail: N/A (preventive — контракт shell guard root)
# · Remove if: root-требование снято (небезопасно — systemctl/iptables требуют root)
def test_main_requires_root(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 1000)

    rc = install_tor_proxy.main([])

    assert rc == EXIT_GENERIC, f"Expected 1 (root required), got {rc}"
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="must run as root")
    _assert_imp9(caplog)


# endregion FUNC_test_main_requires_root


# region FUNC_test_main_unknown_argument
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 unknown-arg: exit 1 (shell byte-compat)
# · Scenario: main(["--bogus"]) → 1; IMP:10 "Unknown argument: --bogus"
# · Last fail: N/A (preventive — shell parse_args exit 1 канон)
# · Remove if: CLI-формат изменён
def test_main_unknown_argument(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 0)

    rc = install_tor_proxy.main(["--bogus"])

    assert rc == EXIT_GENERIC
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="Unknown argument")
    _assert_imp9(caplog)


# endregion FUNC_test_main_unknown_argument


# ── main-оркестрация ───────────────────────────────────────────────────────────


# region FUNC_test_main_flow_success_and_order
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 main: порядок шагов + exit 0
# · Scenario: все шаги успешны → main==0; порядок: packages → torrc → privoxy → services
# ·   → verify-active → firewall → cron → circuit
# · Last fail: N/A (preventive — оркестрационный контракт shell main)
# · Remove if: порядок оркестрации изменён намеренно
def test_main_flow_success_and_order(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())
    order: list[str] = []

    def step(name: str):
        def fake(*_a, **_k):
            order.append(name)
            return True

        return fake

    monkeypatch.setattr(install_tor_proxy, "install_packages", step("install_packages"))
    monkeypatch.setattr(install_tor_proxy, "write_torrc", step("write_torrc"))
    monkeypatch.setattr(install_tor_proxy, "write_privoxy_config", step("write_privoxy_config"))
    monkeypatch.setattr(install_tor_proxy, "enable_services", step("enable_services"))
    monkeypatch.setattr(install_tor_proxy, "verify_services_active", step("verify_services_active"))
    monkeypatch.setattr(install_tor_proxy, "configure_firewall_docker", step("configure_firewall_docker"))
    monkeypatch.setattr(install_tor_proxy, "install_cron_healthcheck", step("install_cron_healthcheck"))
    monkeypatch.setattr(install_tor_proxy, "verify_tor_circuit", step("verify_tor_circuit"))

    rc = install_tor_proxy.main([])

    assert rc == EXIT_OK, f"Expected 0, got {rc}"
    assert order == [
        "install_packages",
        "write_torrc",
        "write_privoxy_config",
        "enable_services",
        "verify_services_active",
        "configure_firewall_docker",
        "install_cron_healthcheck",
        "verify_tor_circuit",
    ], f"Order mismatch: {order}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="installation complete")
    _assert_imp9(caplog)


# endregion FUNC_test_main_flow_success_and_order


# region FUNC_test_main_flow_circuit_failure
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit-fail: exit 1 + CRITICAL
# · Scenario: verify_tor_circuit False → main==1; CRITICAL-логи (non-fatal для bootstrap)
# · Last fail: N/A (preventive — shell exit 1 канон)
# · Remove if: поведение при недоступной цепи изменено
def test_main_flow_circuit_failure(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install_tor_proxy, "install_packages", lambda: None)
    monkeypatch.setattr(install_tor_proxy, "write_torrc", lambda *a, **k: None)
    monkeypatch.setattr(install_tor_proxy, "write_privoxy_config", lambda *a, **k: None)
    monkeypatch.setattr(install_tor_proxy, "enable_services", lambda: None)
    monkeypatch.setattr(install_tor_proxy, "verify_services_active", lambda: True)
    monkeypatch.setattr(install_tor_proxy, "configure_firewall_docker", lambda: None)
    monkeypatch.setattr(install_tor_proxy, "install_cron_healthcheck", lambda *a, **k: None)
    monkeypatch.setattr(install_tor_proxy, "verify_tor_circuit", lambda **k: False)

    rc = install_tor_proxy.main([])

    assert rc == EXIT_GENERIC
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="circuit failed to establish")
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="Telegram notifications will be unavailable")
    _assert_imp9(caplog)


# endregion FUNC_test_main_flow_circuit_failure


# region FUNC_test_main_step_failure_exit1
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 fail-fast шаг: exit 1
# · Scenario: install_packages raises CommandFailedError → main==1 (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: fail-fast политика изменена
def test_main_step_failure_exit1(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 0)

    def boom(*_a, **_k):
        raise install_tor_proxy.CommandFailedError("systemctl restart tor failed (exit=1)")

    monkeypatch.setattr(install_tor_proxy, "install_packages", boom)
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())

    rc = install_tor_proxy.main([])

    assert rc == EXIT_GENERIC
    _assert_log_event(caplog, levelno=logging.ERROR, imp=10, keyword="systemctl restart tor failed")
    _assert_imp9(caplog)


# endregion FUNC_test_main_step_failure_exit1


# region FUNC_test_main_twice_idempotent
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 идемпотентность: main дважды = тот же результат
# · Scenario: полный успешный main, повторный запуск → оба 0, одинаковое число вызовов шагов
# · Last fail: N/A (preventive — идемпотентность канон bootstrap)
# · Remove if: оркестрация перестанет быть идемпотентной (недопустимо)
def test_main_twice_idempotent(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(install_tor_proxy.os, "geteuid", lambda: 0)
    counts: dict[str, int] = {}

    def counted(name: str):
        def fake(*_a, **_k):
            counts[name] = counts.get(name, 0) + 1
            return True

        return fake

    for name in [
        "install_packages",
        "write_torrc",
        "write_privoxy_config",
        "enable_services",
        "verify_services_active",
        "configure_firewall_docker",
        "install_cron_healthcheck",
        "verify_tor_circuit",
    ]:
        monkeypatch.setattr(install_tor_proxy, name, counted(name))
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())

    assert install_tor_proxy.main([]) == EXIT_OK
    first = dict(counts)
    assert install_tor_proxy.main([]) == EXIT_OK
    # Идемпотентность: каждый запуск выполняет КАЖДЫЙ шаг ровно один раз (детерминированно)
    expected = {k: v + 1 for k, v in first.items()}
    assert counts == expected, f"Повторный запуск выполнил шаги не 1:1: {first} → {counts}"
    _assert_imp9(caplog)


# endregion FUNC_test_main_twice_idempotent


# ── write_torrc ─────────────────────────────────────────────────────────────────


# region FUNC_test_write_torrc_template_used
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: template база без мостов
# · Scenario: template существует, bridges=None → torrc == template; "No bridges file"
# · Last fail: N/A (preventive)
# · Remove if: формат torrc изменён
def test_write_torrc_template_used(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n")
    tor_config = tmp_path / "torrc"

    install_tor_proxy.write_torrc(tor_config, None, template)

    assert tor_config.read_text() == "SOCKSPort 127.0.0.1:9050\nDataDirectory /var/lib/tor\n"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="No bridges file")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_template_used


# region FUNC_test_write_torrc_fallback_inline
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: fallback inline при отсутствии template
# · Scenario: template отсутствует → torrc == FALLBACK_TORRC (shell heredoc parity)
# · Last fail: N/A (preventive)
# · Remove if: fallback-ветка удалена
def test_write_torrc_fallback_inline(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    tor_config = tmp_path / "torrc"
    missing_template = tmp_path / "no-template.template"

    install_tor_proxy.write_torrc(tor_config, None, missing_template)

    assert tor_config.read_text() == _FALLBACK_TORRC
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Template not found")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_fallback_inline


# region FUNC_test_write_torrc_bridges_appended
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: мосты аппендятся (obfs4)
# · Scenario: bridges file c obfs4 Bridge; resolve_available_binaries={"obfs4"}
# ·   → torrc содержит UseBridges 1 + ClientTransportPlugin obfs4 + Bridge line
# · Last fail: N/A (preventive — контракт tor_transport 118 E1)
# · Remove if: формат bridge-секции изменён
def test_write_torrc_bridges_appended(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0\n")
    tor_config = tmp_path / "torrc"
    monkeypatch.setattr(tor_transport, "resolve_available_binaries", lambda: {"obfs4"})

    install_tor_proxy.write_torrc(tor_config, str(bridges), template)

    content = tor_config.read_text()
    assert "UseBridges 1" in content, content
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in content, content
    assert "Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0" in content, content
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Bridges appended")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_bridges_appended


# region FUNC_test_write_torrc_unknown_transport_failfast
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: unknown transport → fail-fast
# · Scenario: Bridge c неизвестным транспортом → TorTransportError (exit 1 канон)
# · Last fail: N/A (preventive — fail-fast канон 118 E1)
# · Remove if: unknown transport перестанет быть фатальным
def test_write_torrc_unknown_transport_failfast(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge mystery 1.2.3.4:443 XYZ\n")
    tor_config = tmp_path / "torrc"

    with pytest.raises(tor_transport.TorTransportError):
        install_tor_proxy.write_torrc(tor_config, str(bridges), template)
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Unknown transport")
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_unknown_transport_failfast


# region FUNC_test_write_torrc_idempotent
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 torrc: повторный запуск = тот же конфиг
# · Scenario: write_torrc дважды с мостами → содержимое байт-идентично
# · Last fail: N/A (preventive — идемпотентность канон)
# · Remove if: write_torrc перестанет быть детерминированным
def test_write_torrc_idempotent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    template = tmp_path / "torrc.template"
    template.write_text("SOCKSPort 127.0.0.1:9050\n")
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge obfs4 1.2.3.4:443 ABC cert=XYZ iat-mode=0\n")
    tor_config = tmp_path / "torrc"
    monkeypatch.setattr(tor_transport, "resolve_available_binaries", lambda: {"obfs4"})

    install_tor_proxy.write_torrc(tor_config, str(bridges), template)
    first = tor_config.read_text()
    install_tor_proxy.write_torrc(tor_config, str(bridges), template)

    assert tor_config.read_text() == first, "Повторный запуск изменил torrc (не идемпотентно)"
    _assert_imp9(caplog)


# endregion FUNC_test_write_torrc_idempotent


# ── privoxy ─────────────────────────────────────────────────────────────────────


# region FUNC_test_write_privoxy_config_idempotent
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 privoxy: повторный запуск = no-op
# · Scenario: первый вызов мутирует конфиг (listen-address 0.0.0.0), второй — не меняет
# · Last fail: N/A (preventive — идемпотентный мутатор 119 D3)
# · Remove if: privoxy_config перестанет быть идемпотентным
def test_write_privoxy_config_idempotent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    config = tmp_path / "config"
    config.write_text("forward-socks5t / 127.0.0.1:9050 .\n")

    install_tor_proxy.write_privoxy_config(config)
    first = config.read_text()
    assert "listen-address 0.0.0.0:8118" in first, first

    install_tor_proxy.write_privoxy_config(config)

    assert config.read_text() == first, "Повторный запуск изменил privoxy-конфиг (не идемпотентно)"
    assert not any("FAIL" in r.message for r in caplog.records), caplog.text
    _assert_imp9(caplog)


# endregion FUNC_test_write_privoxy_config_idempotent


# region FUNC_test_write_privoxy_config_dpkg_double_space_upgrade
@pytest.mark.unit
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

    install_tor_proxy.write_privoxy_config(config)
    first = config.read_text()
    assert "listen-address 0.0.0.0:8118" in first, f"dpkg 127.0.0.1 (2 пробела) должен быть апгрейждён: {first}"
    assert "listen-address  127.0.0.1:8118" not in first, "старый 127.0.0.1 не должен остаться"

    # Идемпотентность: повторный вызов — no-op
    install_tor_proxy.write_privoxy_config(config)
    assert config.read_text() == first, "повторный запуск изменил конфиг (не идемпотентно)"
    _assert_imp9(caplog)


# endregion FUNC_test_write_privoxy_config_dpkg_double_space_upgrade


# ── systemd ─────────────────────────────────────────────────────────────────────


# region FUNC_test_enable_services_command_sequence
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 systemd: sequence enable→restart tor→sleep→restart privoxy
# · Scenario: recorder run_command; time.sleep no-op → 4 команды в канонном порядке
# · Last fail: N/A (preventive)
# · Remove if: последовательность systemd изменена
def test_enable_services_command_sequence(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder()
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())

    install_tor_proxy.enable_services()

    cmds = list(calls)
    assert cmds == [
        ["systemctl", "enable", "tor", "--quiet"],
        ["systemctl", "enable", "privoxy", "--quiet"],
        ["systemctl", "restart", "tor"],
        ["systemctl", "restart", "privoxy"],
    ], cmds
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="restarted")
    _assert_imp9(caplog)


# endregion FUNC_test_enable_services_command_sequence


# region FUNC_test_enable_services_restart_failure_fatal
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 systemd: restart tor fail → CommandFailedError
# · Scenario: enable rc=0; restart tor rc=1 c check=True → CommandFailedError (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: restart перестанет быть фатальным
def test_enable_services_restart_failure_fatal(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(0, ""), (0, ""), (1, "Failed to restart tor.service"), (0, "")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())

    with pytest.raises(install_tor_proxy.CommandFailedError):
        install_tor_proxy.enable_services()
    assert len(calls) == 3, f"Остановка после restart tor fail: {calls}"
    _assert_imp9(caplog)


# endregion FUNC_test_enable_services_restart_failure_fatal


# ── verify services active ──────────────────────────────────────────────────────


# region FUNC_test_verify_services_active_both
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 verify-active: оба active → True
# · Scenario: is-active tor/privoxy rc=0 → True
# · Last fail: N/A (preventive)
# · Remove if: критерий активного сервиса изменён
def test_verify_services_active_both(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(0, ""), (0, "")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    assert install_tor_proxy.verify_services_active() is True
    assert len(calls) == 2
    _assert_imp9(caplog)


# endregion FUNC_test_verify_services_active_both


# region FUNC_test_verify_services_active_tor_inactive
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 verify-active: tor NOT active → False
# · Scenario: is-active tor rc=1 → False (set -e канон → main exit 1)
# · Last fail: N/A (preventive)
# · Remove if: критерий активного сервиса изменён
def test_verify_services_active_tor_inactive(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    _calls, fake = _make_recorder([(1, ""), (0, "")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    assert install_tor_proxy.verify_services_active() is False
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Tor: NOT active")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_services_active_tor_inactive


# ── verify tor circuit ──────────────────────────────────────────────────────────


# region FUNC_test_verify_tor_circuit_success
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: "Congratulations" → True
# · Scenario: curl stdout содержит Congratulations на 1-й попытке → True, IMP:9 established
# · Last fail: N/A (preventive)
# · Remove if: критерий проверки цепи изменён
def test_verify_tor_circuit_success(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(0, "Congratulations. This browser is configured to use Tor.")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)
    monkeypatch.setattr(install_tor_proxy, "time", _FakeTime())

    assert install_tor_proxy.verify_tor_circuit() is True
    assert len(calls) == 1, f"Успех должен быть на 1-й попытке: {calls}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="circuit established")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_success


# region FUNC_test_verify_tor_circuit_retries_then_fails
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: 12 попыток без успеха → False
# · Scenario: stdout пуст → 12 curl-вызовов, 11 sleep, False, FAIL-лог
# · Last fail: N/A (preventive)
# · Remove if: retry-политика проверки цепи изменена
def test_verify_tor_circuit_retries_then_fails(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(0, "")] * install_tor_proxy.VERIFY_MAX_ATTEMPTS)
    fake_time = _FakeTime()
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)
    monkeypatch.setattr(install_tor_proxy, "time", fake_time)

    assert install_tor_proxy.verify_tor_circuit() is False
    assert len(calls) == install_tor_proxy.VERIFY_MAX_ATTEMPTS, f"Ожидалось 12 попыток: {len(calls)}"
    assert fake_time.sleep_calls == install_tor_proxy.VERIFY_MAX_ATTEMPTS - 1, "sleep между попытками"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="failed to establish circuit")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_retries_then_fails


# region FUNC_test_verify_tor_circuit_skipped
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 circuit: --skip-tor-verify → True без curl
# · Scenario: skip=True → True; run_command НЕ вызывается
# · Last fail: N/A (preventive)
# · Remove if: флаг skip удалён
def test_verify_tor_circuit_skipped(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder()
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    assert install_tor_proxy.verify_tor_circuit(skip=True) is True
    assert calls == [], f"skip=True не должен вызывать curl: {calls}"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="verification skipped")
    _assert_imp9(caplog)


# endregion FUNC_test_verify_tor_circuit_skipped


# ── cron healthcheck ────────────────────────────────────────────────────────────


# region FUNC_test_install_cron_healthcheck_writes
@pytest.mark.unit
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

    install_tor_proxy.install_cron_healthcheck(core_dir, cron_file)

    expected = f"{install_tor_proxy.CRON_SCHEDULE} {hc}\n"
    assert cron_file.read_text() == expected, cron_file.read_text()
    assert cron_file.stat().st_mode & 0o777 == 0o644, oct(cron_file.stat().st_mode)
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Healthcheck cron installed")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_writes


# region FUNC_test_install_cron_healthcheck_idempotent
@pytest.mark.unit
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

    install_tor_proxy.install_cron_healthcheck(core_dir, cron)

    assert cron.read_text() == "CUSTOM-UNTOUCHED\n", "Существующий cron НЕ должен перезаписываться"
    _assert_log_event(caplog, levelno=logging.INFO, imp=9, keyword="already installed")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_idempotent


# region FUNC_test_install_cron_healthcheck_missing_script
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 cron: hc-скрипт отсутствует → SKIP, файл не создаётся
# · Scenario: hc_script нет → cron_file не создаётся; SKIP-лог
# · Last fail: N/A (preventive)
# · Remove if: guard по hc-скрипту удалён
def test_install_cron_healthcheck_missing_script(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    cron_file = tmp_path / "cron"

    install_tor_proxy.install_cron_healthcheck(core_dir, cron_file)

    assert not cron_file.exists(), "Без hc-скрипта cron не должен создаваться"
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="Healthcheck script not found")
    _assert_imp9(caplog)


# endregion FUNC_test_install_cron_healthcheck_missing_script


# ── firewall ────────────────────────────────────────────────────────────────────


# region FUNC_test_configure_firewall_docker_adds_once
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: -C guard → -I add один раз
# · Scenario: 1-й вызов -C rc=1 → -I; 2-й вызов -C rc=0 → без -I (идемпотентность)
# · Last fail: N/A (preventive)
# · Remove if: iptables-guard изменён
def test_configure_firewall_docker_adds_once(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(1, "")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    install_tor_proxy.configure_firewall_docker()
    assert len(calls) == 2 and calls[0][0] == "iptables" and calls[0][1] == "-C" and calls[1][1] == "-I", calls
    first_iptables = list(calls)

    calls.clear()
    install_tor_proxy.configure_firewall_docker()
    assert len(calls) == 1 and calls[0][1] == "-C", f"2-й вызов должен быть no-op (-C rc=0): {calls}"

    assert len(first_iptables) == 2
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_adds_once


# region FUNC_test_configure_firewall_docker_rule_exists
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: правило существует → только -C
# · Scenario: -C rc=0 → правило уже есть, -I не вызывается
# · Last fail: N/A (preventive)
# · Remove if: iptables-guard изменён
def test_configure_firewall_docker_rule_exists(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(0, "")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    install_tor_proxy.configure_firewall_docker()

    assert len(calls) == 1 and calls[0][1] == "-C", calls
    _assert_log_event(caplog, levelno=logging.INFO, imp=8, keyword="rule already exists")
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_rule_exists


# region FUNC_test_configure_firewall_docker_add_fatal
@pytest.mark.unit
# 🧪 TRAP[TEST] · 2026-08-04 · REGRESSION · W1 firewall: iptables -I fail → CommandFailedError
# · Scenario: -C rc=1, -I rc=1 c check=True → CommandFailedError (set -e канон)
# · Last fail: N/A (preventive)
# · Remove if: iptables add перестанет быть фатальным
def test_configure_firewall_docker_add_fatal(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    caplog.set_level(logging.INFO)
    calls, fake = _make_recorder([(1, ""), (1, "iptables: Permission denied")])
    monkeypatch.setattr(install_tor_proxy, "run_command", fake)

    with pytest.raises(install_tor_proxy.CommandFailedError):
        install_tor_proxy.configure_firewall_docker()
    assert len(calls) == 2, calls
    _assert_imp9(caplog)


# endregion FUNC_test_configure_firewall_docker_add_fatal


# ── helpers ─────────────────────────────────────────────────────────────────────


# region CLASS__FakeTime
class _FakeTime:
    """Заглушка time: sleep no-op + счётчик вызовов (DI для retry/sleep-циклов).

    ## @purpose — Подмена install_tor_proxy.time без реальных пауз в тестах.
    """

    def __init__(self) -> None:
        self.sleep_calls = 0

    def sleep(self, _seconds: float) -> None:
        self.sleep_calls += 1


# endregion CLASS__FakeTime
