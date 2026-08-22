# GREP_SUMMARY: test-tor-setup install-packages webtunnel-degradation obfs4-fallback apt-plan dry-run detect-transports package-install
# STRUCTURE: ┌present/repo package sets┐ → ◇ plan_install (pure: webtunnel degradation) → ◇ install_tor_packages (apt flow, subprocess mock) → ◇ CLI --install/--detect → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/tor_setup.py (DevPlan 119 D2 — TEST-FIRST:
##           тесты задают контракт ПЕРЕД миграцией install_packages() бизнес-логики из
##           install-tor-proxy.sh в Python). Деградационная state-machine webtunnel→obfs4.
## @scope    Tests: plan_install (pure планирование + деградация webtunnel из репозиториев),
##           detect_available_transports (apt-cache probe), install_tor_packages (полный apt-flow
##           через мок subprocess: apt-get update → install → retry без webtunnel при провале).
## @invariants
##   - plan_install — чистая функция (no subprocess, no filesystem)
##   - install_tor_packages тестируется через patch tor_setup.subprocess.run (нет реального apt)
##   - R5 anti-survivorship: negative-тест test_tor_package_fallback_obfs4_negative (webtunnel
##     отсутствует в репозиториях → obfs4 выбран) — исходный вход TRAP[DECISION] 2026-07-17
##   - LDD: IMP:9 в успешных сценариях
## @rationale D2 (DevPlan 119): install_packages() (52-116) — деградационная state-machine
##   (webtunnel→obfs4 fallback), >3 if-веток бизнес-логики (Tier-1 Strangler trigger).
##   Условие DevPlan D2 step 1: unit-тесты ПЕРЕД миграцией — выполнено (test-first).
## @changes  2026-08-02 | DevPlan 119 D2 — Created (test-first)
# endregion MODULE_CONTRACT

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.internal.bootstrap import tor_setup
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# region TEST_plan_install (чистое планирование + деградация)


@pytest.mark.parametrize(
    ("present", "repo", "expected"),
    [
        # Все пакеты (tor/privoxy/obfs4proxy/webtunnel) установлены → пустой план (shell SKIP-ветка)
        ({"tor", "privoxy", "obfs4proxy", "webtunnel"}, {"webtunnel", "obfs4proxy"}, []),
        # Частичная установка: только tor на месте → недостающие базовые + webtunnel
        ({"tor"}, {"webtunnel", "obfs4proxy"}, ["privoxy", "obfs4proxy", "webtunnel"]),
        # webtunnel отсутствует на диске, но есть в репозиториях → план включает webtunnel
        ({"tor", "privoxy", "obfs4proxy"}, {"webtunnel", "obfs4proxy"}, ["webtunnel"]),
        # Всё отсутствует, webtunnel доступен → полный план из 4 пакетов (порядок сохранён)
        (set(), {"webtunnel", "obfs4proxy"}, ["tor", "privoxy", "obfs4proxy", "webtunnel"]),
    ],
)
# 🧪 TRAP[TEST] · 2026-08-02 · Regression · plan_install: present/repo → план (D2)
# · Scenario: (present, repo) → ожидаемый список пакетов (все present / partial / webtunnel-only / full)
# · Last fail: N/A (new — D2 test-first)
# · Remove if: plan_install semantics change
def test_plan_install(present, repo, expected) -> None:
    """plan_install: чистое планирование пакетов по (present, repo) → список к установке."""
    plan = tor_setup.plan_install(present=present, repo=repo)
    assert plan == expected


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_tor_package_fallback_obfs4 — webtunnel отсутствует в репо → obfs4 (D2)
# · Scenario: webtunnel нет ни на диске, ни в репозиториях → план БЕЗ webtunnel (деградация obfs4-only)
# · Last fail: TRAP[DECISION] 2026-07-17 — apt пакет webtunnel недоступен для noble → degrade, не abort
# · Remove if: деградация webtunnel удалена из plan_install
# GUARD-PRESERVE (168): R5 anti-survivorship — negative-пара test_tor_package_fallback_obfs4 (webtunnel деградирован)
def test_tor_package_fallback_obfs4_negative() -> None:
    """R5 negative: webtunnel отсутствует в apt-репозиториях → деградация, obfs4 выбран."""
    plan = tor_setup.plan_install(
        present={"tor", "privoxy", "obfs4proxy"},
        repo={"obfs4proxy"},  # webtunnel НЕ доступен в репозиториях
    )
    assert "webtunnel" not in plan, f"webtunnel должен быть деградирован: {plan}"
    assert plan == [], "ничего не требуется (tor/privoxy/obfs4proxy уже установлены)"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · plan_install: всё отсутствует + webtunnel НЕ в репо (D2)
# · Scenario: пустой present, repo без webtunnel → план = 3 базовых (webtunnel деградирован)
# · Last fail: N/A (new — D2 test-first)
# · Remove if: деградация webtunnel удалена
def test_tor_package_all_absent_no_webtunnel_repo() -> None:
    """Всё отсутствует, webtunnel НЕ в репозиториях → план без webtunnel (деградация)."""
    plan = tor_setup.plan_install(present=set(), repo={"obfs4proxy"})
    assert plan == ["tor", "privoxy", "obfs4proxy"]
    assert "webtunnel" not in plan


# endregion TEST_plan_install


# region TEST_install_tor_packages (полный apt-flow, мок subprocess)


def _make_fake_run(
    *,
    dpkg_installed: set[str] | None = None,
    webtunnel_in_repo: bool = True,
    obfs4_in_repo: bool = True,
    webtunnel_install_fails: bool = False,
) -> MagicMock:
    """Фабрика fake subprocess.run для apt-flow (dpkg -s / apt-cache show / apt-get)."""
    import subprocess as _sp

    installed = dpkg_installed or set()

    def _run(cmd, **kwargs):
        if cmd[0] == "dpkg":
            # dpkg -s <pkg> → rc 0 если установлен
            return _sp.CompletedProcess(cmd, 0 if cmd[-1] in installed else 1, stdout="", stderr="")
        if cmd[0] == "apt-cache":
            pkg = cmd[-1]
            in_repo = (pkg == "webtunnel" and webtunnel_in_repo) or (pkg == "obfs4proxy" and obfs4_in_repo)
            return _sp.CompletedProcess(cmd, 0 if in_repo else 1, stdout="", stderr="")
        if cmd[0] == "apt-get" and cmd[1] == "update":
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "apt-get" and cmd[1] == "install":
            pkgs = set(cmd[3:])
            if webtunnel_install_fails and "webtunnel" in pkgs:
                return _sp.CompletedProcess(cmd, 1, stdout="", stderr="apt webtunnel failed")
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        return _sp.CompletedProcess(cmd, 1, stdout="", stderr="")

    return MagicMock(side_effect=_run)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · install_tor_packages: webtunnel доступен → установлен (D2)
# · Scenario: tor/privoxy/obfs4proxy отсутствуют, webtunnel в репо → план установлен, мы получим 4 пакета
# · Last fail: N/A (new — D2 test-first; DevPlan test_install_tor_packages_webtunnel)
# · Remove if: install_tor_packages semantics change
def test_install_tor_packages_webtunnel(caplog: pytest.LogCaptureFixture) -> None:
    """Полный flow: все 4 пакета установлены (webtunnel доступен в репозиториях)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(webtunnel_in_repo=True)
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        installed = tor_setup.install_tor_packages(dry_run=False)
    assert installed == ["tor", "privoxy", "obfs4proxy", "webtunnel"]
    # apt-get install вызван ОДИН раз (без деградационного retry)
    install_calls = [c for c in fake_run.call_args_list if c.args[0][0:2] == ["apt-get", "install"]]
    assert len(install_calls) == 1
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · install_tor_packages: провал webtunnel → retry без него (D2)
# · Scenario: apt-get install падает когда webtunnel в списке → деградационный retry без webtunnel
# · Last fail: TRAP[DECISION] 2026-07-17 · MED — webtunnel apt failure → degradation, not abort
# · Remove if: деградационный retry удалён из install_tor_packages
def test_install_tor_packages_webtunnel_fail_degrades(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: apt-get install webtunnel падает → retry без webtunnel (обfs4-only), не abort."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(webtunnel_in_repo=True, webtunnel_install_fails=True)
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        installed = tor_setup.install_tor_packages(dry_run=False)
    assert installed == ["tor", "privoxy", "obfs4proxy", "webtunnel"]  # план заявлен (retry выполнен)
    install_calls = [c for c in fake_run.call_args_list if c.args[0][0:2] == ["apt-get", "install"]]
    assert len(install_calls) == 2, "ожидается 2 вызова apt-get install (первый + деградационный retry)"
    assert "webtunnel" not in install_calls[1].args[0], "retry должен исключить webtunnel"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · install_tor_packages: webtunnel НЕ в репо → без webtunnel (D2)
# · Scenario: webtunnel отсутствует в apt-cache → план без webtunnel с самого начала
# · Last fail: TRAP[DECISION] 2026-07-17 · — · apt preferred, static binary reserved
# · Remove if: repo-деградация webtunnel удалена
def test_install_tor_packages_webtunnel_not_in_repo(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: webtunnel нет в apt-репозиториях → не запрашивается (деградация до установки)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(webtunnel_in_repo=False)
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        installed = tor_setup.install_tor_packages(dry_run=False)
    assert "webtunnel" not in installed
    assert installed == ["tor", "privoxy", "obfs4proxy"]
    install_calls = [c for c in fake_run.call_args_list if c.args[0][0:2] == ["apt-get", "install"]]
    assert "webtunnel" not in install_calls[0].args[0]


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · install_tor_packages: всё установлено → [] (D2)
# · Scenario: все 4 пакета присутствуют → [] (shell SKIP «All packages already installed»)
# · Last fail: N/A (new — D2 test-first)
# · Remove if: install_tor_packages semantics change
def test_install_tor_packages_all_present(caplog: pytest.LogCaptureFixture) -> None:
    """Все пакеты установлены → пустой список (никаких apt-get вызовов)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(dpkg_installed={"tor", "privoxy", "obfs4proxy", "webtunnel"})
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        installed = tor_setup.install_tor_packages(dry_run=False)
    assert installed == []
    apt_calls = [c for c in fake_run.call_args_list if c.args[0][0] == "apt-get"]
    assert apt_calls == [], "при полной установке apt-get не вызывается"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · install_tor_packages: dry_run не трогает apt (D2)
# · Scenario: dry_run=True → apt-get update/install НЕ выполняются, план возвращён
# · Last fail: N/A (new — D2 test-first)
# · Remove if: dry_run семантика меняется
def test_install_tor_packages_dry_run(caplog: pytest.LogCaptureFixture) -> None:
    """dry_run=True → план возвращён, никаких мутаций (apt-get не вызывается)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run()
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        installed = tor_setup.install_tor_packages(dry_run=True)
    assert installed == ["tor", "privoxy", "obfs4proxy", "webtunnel"]
    apt_calls = [c for c in fake_run.call_args_list if c.args[0][0] == "apt-get"]
    assert apt_calls == [], "dry_run не должен выполнять apt-get"


# endregion TEST_install_tor_packages


# region TEST_detect_available_transports + CLI


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · detect_available_transports (D2)
# · Scenario: apt-cache show webtunnel/obfs4proxy → dict {webtunnel: bool, obfs4proxy: bool}
# · Last fail: N/A (new — D2 test-first)
# · Remove if: detect_available_transports удалён
def test_detect_available_transports(caplog: pytest.LogCaptureFixture) -> None:
    """detect_available_transports: возвращает доступность транспортов в apt-репозиториях."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(webtunnel_in_repo=True, obfs4_in_repo=False)
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        transports = tor_setup.detect_available_transports()
    assert transports == {"webtunnel": True, "obfs4proxy": False}


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --install (D2, shell вызов python3 tor_setup.py --install)
# · Scenario: main(["--install"]) → печатает установленные пакеты, exit 0
# · Last fail: N/A (new — D2 test-first)
# · Remove if: CLI удалён
def test_cli_install(caplog: pytest.LogCaptureFixture, capsys) -> None:
    """CLI --install: печатает установленные пакеты на stdout (shell читает список)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(webtunnel_in_repo=True)
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        rc = tor_setup.main(["--install"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "tor privoxy obfs4proxy webtunnel"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --install: всё установлено → пустой stdout (D2)
# · Scenario: все пакеты present → stdout пустой (shell SKIP-ветка)
# · Last fail: N/A (new — D2 test-first)
# · Remove if: CLI удалён
def test_cli_install_all_present_empty_output(caplog: pytest.LogCaptureFixture, capsys) -> None:
    """CLI --install: все пакеты установлены → пустой stdout (shell: SKIP all installed)."""
    caplog.set_level(logging.INFO)
    fake_run = _make_fake_run(dpkg_installed={"tor", "privoxy", "obfs4proxy", "webtunnel"})
    with patch("core.internal.bootstrap.tor_setup.subprocess.run", fake_run):
        rc = tor_setup.main(["--install"])
    out = capsys.readouterr().out
    assert rc == 0
    assert not out.strip()


# endregion TEST_detect_available_transports + CLI
