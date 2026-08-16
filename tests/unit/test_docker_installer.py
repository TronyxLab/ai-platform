# GREP_SUMMARY: test-docker-installer package-selection guard verify daemon-live-restore systemd-override dry-run ports-2375
# STRUCTURE: ┌pure functions + dry-run run()┐ → ◇ select_missing_packages → ◇ guard_already_installed → ◇ verify_installation (2375/2376) → ◇ configure_daemon (merge/default) → ◇ configure_systemd_override → ◇ run() dry-run pipeline → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/docker_installer.py (DevPlan 118 E2 — Python-порт
##           install-docker.sh). Тест ДО/ПОСЛЕ: pure-функции фиксируют контракт старого shell
##           (пакеты, verify-логика), run() с DOCKER_INSTALLER_DRY_RUN=1 — полный pipeline без apt/systemd.
## @scope    Tests: package selection (missing filter), guard (docker CLI / dpkg), verify
##           (docker+compose+no 2375/2376), daemon.json default content + merge, systemd override content,
##           dry-run run() full-pipeline (после миграции).
## @invariants
##   - Pure function tests + run() with DOCKER_INSTALLER_DRY_RUN=1 (no real apt/systemd/docker)
##   - R5 anti-survivorship: negative-тесты (verify 2375 open → False, missing compose → False)
##   - LDD: IMP:9 on verify pass, IMP:10 on failures
## @rationale E2 Strangler: apt/systemd-оркестрация → Python. Пакеты/verify/daemon — тестируемые pure functions.
## @changes  2026-08-02 | DevPlan 118 E2 — Created
# endregion MODULE_CONTRACT

import json
import logging
from pathlib import Path

import pytest

from core.internal.bootstrap import docker_installer as di


# region TEST_select_missing_packages
def test_select_missing_packages() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_select_missing_packages — DevPlan 118 E migration unit test
    """select_missing_packages: only not-installed candidates returned."""
    installed = {"ca-certificates", "curl"}
    missing = di.select_missing_packages(di.APT_DEPS, installed)
    assert missing == ["gnupg", "lsb-release"]
    assert di.select_missing_packages(di.APT_DEPS, set(di.APT_DEPS)) == []


# endregion TEST_select_missing_packages


# region TEST_guard_already_installed
# 🧪 TRAP[TEST] · 2026-08-02 · test_guard_already_installed (CLI/dpkg/none) — DevPlan 118 E migration unit test
@pytest.mark.parametrize(
    "docker_out, dpkg_out, expected",
    [
        pytest.param("Docker version 26.1.3", "", True, id="docker_cli_present"),
        pytest.param("", "Package: docker-ce\nStatus: install ok installed", True, id="dpkg_docker_ce_present"),
        pytest.param("", "", False, id="nothing_present"),
    ],
)
def test_guard_already_installed(docker_out: str, dpkg_out: str, expected: bool) -> None:
    """guard_already_installed: CLI/dpkg outputs → installed, neither → proceed (P-консолидация 168).

    Cases (1:1 из test_guard_docker_cli_present/test_guard_dpkg_docker_ce_present/
    test_guard_nothing_present): docker --version output → True; dpkg -s docker-ce → True;
    empty CLI+dpkg → False (proceed with install).
    """
    assert di.guard_already_installed(docker_out, dpkg_out) is expected


# endregion TEST_guard_already_installed


# region TEST_verify_installation
def test_verify_ok() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_ok — DevPlan 118 E migration unit test
    """verify: docker+compose present, no 2375/2376 → (True, ok)."""
    ok, msg = di.verify_installation("Docker version 26.1.3", "Docker Compose version v2.27.0", "")
    assert ok is True
    assert "ports secure" in msg


def test_verify_missing_docker(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_missing_docker — DevPlan 118 E migration unit test
    """verify: docker --version empty → False."""
    ok, msg = di.verify_installation("", "Docker Compose version v2", "")
    assert ok is False
    assert "docker --version failed" in msg


def test_verify_missing_compose() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_missing_compose — DevPlan 118 E migration unit test
    """verify: compose version empty → False (plugin missing)."""
    ok, msg = di.verify_installation("Docker version 26.1.3", "", "")
    assert ok is False
    assert "Compose plugin missing" in msg


def test_verify_docker_ports_exposed() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_docker_ports_exposed — DevPlan 118 E migration unit test
    """verify: ss output with :2375 → False (SECURITY)."""
    ok, msg = di.verify_installation("Docker version 26.1.3", "Docker Compose version v2", "LISTEN 0.0.0.0:2375")
    assert ok is False
    assert "2375" in msg


def test_verify_docker_ports_2376_exposed() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_docker_ports_2376_exposed — DevPlan 118 E migration unit test
    """verify: ss output with :2376 → False (SECURITY)."""
    ok, msg = di.verify_installation("Docker version 26.1.3", "Docker Compose version v2", "LISTEN 0.0.0.0:2376")
    assert ok is False
    assert "2376" in msg


# endregion TEST_verify_installation


# region TEST_docker_user_dropin (DevPlan 162 W2-3)
def test_configure_docker_user_dropin_creates(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W2-3 · drop-in ExecStartPost создаётся при отсутствии
    # · Scenario: docker.service.d/99-platform-docker-user.conf отсутствует → запись с
    # ·   ExecStartPost firewall.py --apply-docker-user (DOCKER-USER после старта daemon)
    # · Last fail: 2026-08-13 — DOCKER-USER пуста; iptables-restore не переживает Docker 20.10+
    # · Remove if: persistence-механизм DOCKER-USER изменён
    caplog.set_level(logging.INFO)
    dropin = tmp_path / "docker.service.d" / "99-platform-docker-user.conf"

    assert di.configure_docker_user_dropin(dropin) is True
    content = dropin.read_text()
    assert "[Service]" in content
    assert "ExecStartPost=" in content
    assert "--apply-docker-user" in content
    assert "firewall.py" in content, "ExecStartPost обязан вызывать firewall.py (DOCKER-USER policy)"
    assert any("[IMP:9]" in r.message and "Drop-in written" in r.message for r in caplog.records)


def test_configure_docker_user_dropin_skips_existing(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W2-3 · существующий drop-in не перезаписывается
    # · Scenario: файл уже есть → skip (идемпотентность, канон configure_systemd_override)
    # · Last fail: N/A (новый кейс DevPlan 162 W2-3)
    # · Remove if: guard по существованию удалён
    dropin = tmp_path / "docker.service.d" / "99-platform-docker-user.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("KEEP-ME\n")
    assert di.configure_docker_user_dropin(dropin) is True
    assert dropin.read_text() == "KEEP-ME\n", "existing drop-in must not be overwritten"


# endregion TEST_docker_user_dropin (DevPlan 162 W2-3)


# region TEST_default_address_pools (DevPlan 162 W5-2)
# GUARD-PRESERVE (168): единственное покрытие политики default-address-pools (канон DAEMON_JSON_DEFAULT,
# 162 W5-2 REGRESSION — нода непересоздаваема без зафиксированных пулов); не редуцируется
def test_daemon_json_default_has_address_pools() -> None:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W5-2 · DAEMON_JSON_DEFAULT содержит default-address-pools
    # · Scenario: встроенный пул 172.17-31 исчерпан → 10.32.0.0/16 size 24 зафиксирован в каноне
    # · Last fail: 2026-08-13 — нода непересоздаваема (проектные сети нигде не зафиксированы)
    # · Remove if: политика address-pools изменена
    pools = di.DAEMON_JSON_DEFAULT["default-address-pools"]
    assert pools == [{"base": "10.32.0.0/16", "size": 24}], pools


def test_daemon_json_default_no_collision_with_tor_privoxy_net() -> None:
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · 162 W5-2 · 10.32.0.0/16 не пересекается с 172.16.0.0/12
    # · Scenario: TOR_PRIVOXY_NET=172.16.0.0/12 (firewall.py) — пересечение пулов = сетевой конфликт
    # · Last fail: N/A (новый кейс DevPlan 162 W5-2 — проверка выбора пула)
    # · Remove if: состав пулов изменён
    from core.internal.bootstrap import firewall

    pools = di.DAEMON_JSON_DEFAULT["default-address-pools"]
    assert isinstance(pools, list) and pools
    for pool in pools:
        base = str(pool["base"])
        assert not _nets_overlap(base, firewall.TOR_PRIVOXY_NET), (
            f"address-pool {base} пересекается с TOR_PRIVOXY_NET {firewall.TOR_PRIVOXY_NET}"
        )
        assert not _nets_overlap(base, "172.16.0.0/12"), f"address-pool {base} пересекается с docker default"
    # 10.32.0.0/16 — вне 172.16-31/12 (docker встроенный пул) — базовое инвариантное свойство
    assert not _nets_overlap("10.32.0.0/16", "172.16.0.0/12")  # 10.32/16 вне docker-пула 172.16/12


def _nets_overlap(cidr_a: str, cidr_b: str) -> bool:
    """IP-range overlap check для /16+/12 (native, без ipaddress-зависимостей в рантайме)."""
    import ipaddress

    return ipaddress.ip_network(cidr_a, strict=False).overlaps(ipaddress.ip_network(cidr_b, strict=False))


def test_configure_daemon_default_writes_address_pools(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · REGRESSION · 162 W5-2 · configure_daemon(default) пишет address-pools
    # · Scenario: новый daemon.json → default-address-pools присутствует в файле
    # · Last fail: N/A (новый кейс DevPlan 162 W5-2)
    # · Remove if: DAEMON_JSON_DEFAULT изменён
    daemon = tmp_path / "daemon.json"
    assert di.configure_daemon(daemon) is True
    data = json.loads(daemon.read_text())
    assert data["default-address-pools"] == [{"base": "10.32.0.0/16", "size": 24}]
    assert data["live-restore"] is True


# endregion TEST_default_address_pools (DevPlan 162 W5-2)


# region TEST_configure_daemon
def test_configure_daemon_default(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_configure_daemon_default — DevPlan 118 E migration unit test
    """configure_daemon: no existing daemon.json → default written (live-restore: true, iptables: true)."""
    caplog.set_level(logging.INFO)
    daemon = tmp_path / "daemon.json"

    assert di.configure_daemon(daemon) is True
    data = json.loads(daemon.read_text())
    assert data["live-restore"] is True
    assert data["iptables"] is True
    assert data["log-opts"] == {"max-size": "50m", "max-file": "5"}
    assert any("[IMP:9]" in r.message and "written" in r.message for r in caplog.records)


def test_configure_daemon_merge_existing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_configure_daemon_merge_existing — DevPlan 118 E migration unit test
    """configure_daemon: existing daemon.json → live-restore merged, other keys preserved."""
    caplog.set_level(logging.INFO)
    daemon = tmp_path / "daemon.json"
    daemon.write_text(json.dumps({"iptables": False, "log-driver": "journald"}))

    assert di.configure_daemon(daemon) is True
    data = json.loads(daemon.read_text())
    assert data["live-restore"] is True, "live-restore must be merged"
    assert data["iptables"] is False, "existing keys preserved"
    assert data["log-driver"] == "journald", "existing keys preserved"
    assert any("[IMP:9]" in r.message and "merged" in r.message for r in caplog.records)


# endregion TEST_configure_daemon


# region TEST_configure_systemd_override
def test_configure_systemd_override_creates(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_configure_systemd_override_creates — DevPlan 118 E migration unit test
    """configure_systemd_override: absent → writes Restart=always + RestartSec=10s."""
    override = tmp_path / "docker.service.d" / "restart.conf"
    assert di.configure_systemd_override(override) is True
    content = override.read_text()
    assert "[Service]" in content
    assert "Restart=always" in content
    assert "RestartSec=10s" in content


def test_configure_systemd_override_skips_existing(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_configure_systemd_override_skips_existing — DevPlan 118 E migration unit test
    """configure_systemd_override: exists → skip (no overwrite, idempotent)."""
    override = tmp_path / "docker.service.d" / "restart.conf"
    override.parent.mkdir(parents=True)
    override.write_text("KEEP-ME\n")
    assert di.configure_systemd_override(override) is True
    assert override.read_text() == "KEEP-ME\n", "existing override must not be overwritten"


# endregion TEST_configure_systemd_override


# region TEST_run_dry_run_pipeline
def test_run_full_pipeline_dry_run(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_full_pipeline_dry_run — DevPlan 118 E migration unit test
    """run(): full pipeline with DOCKER_INSTALLER_DRY_RUN=1 — no crash, verify pass (после-тест)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("DOCKER_INSTALLER_DRY_RUN", "1")

    ok = di.run()
    assert ok is True
    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 verify/daemon logs expected in dry-run pipeline"


def test_run_dry_run_guard_skip_install(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_dry_run_guard_skip_install — DevPlan 118 E migration unit test
    """run(): docker already installed (guard via fake sh_fn) → guard-skip log, verify skipped in dry-run."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("DOCKER_INSTALLER_DRY_RUN", "1")
    # force guard: sh_fn returns docker version for the first docker --version call (guard probe)
    calls: list[str] = []

    def fake_sh(*args: str, dry: bool = False) -> str:
        calls.append(" ".join(args))
        if args[:2] == ("docker", "--version"):
            return "Docker version 26.1.3\n"  # guard sees docker installed
        return ""

    ok = di.run(sh_fn=fake_sh)  # DI (167 D0): _sh-канал параметром вместо monkeypatch di._sh
    assert ok is True
    # W5 T5.4: level-agnostic content check (Docker already installed — IMP:8 flow-строка)
    assert any("Docker already installed" in r.message for r in caplog.records), "guard-skip log expected"
    # docker-compose install must NOT be attempted
    assert not any("apt-get install" in c and "docker-ce" in c for c in calls), "install must be skipped on guard"


# endregion TEST_run_dry_run_pipeline


# region TEST_build_repo_command
def test_build_repo_command() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_repo_command — DevPlan 118 E migration unit test
    """build_repo_command: deb [arch=.. signed-by=..] URL codename stable."""
    cmd = di.build_repo_command("amd64", "noble", "/etc/apt/keyrings/docker.gpg", "/etc/apt/sources.list.d/docker.list")
    assert cmd[0] == "deb"
    assert "[arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg]" in cmd
    assert "https://download.docker.com/linux/ubuntu" in cmd
    assert cmd[-2:] == ["noble", "stable"]


# endregion TEST_build_repo_command
