#!/usr/bin/env python3
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


# endregion


# region TEST_guard_already_installed
def test_guard_docker_cli_present() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_guard_docker_cli_present — DevPlan 118 E migration unit test
    """guard: docker --version outputs → already installed."""
    assert di.guard_already_installed("Docker version 26.1.3", "") is True


def test_guard_dpkg_docker_ce_present() -> None:
    """guard: dpkg -s docker-ce lists it → already installed."""
    assert di.guard_already_installed("", "Package: docker-ce\nStatus: install ok installed") is True


def test_guard_nothing_present() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_guard_nothing_present — DevPlan 118 E migration unit test
    """guard: neither CLI nor dpkg → not installed (proceed)."""
    assert di.guard_already_installed("", "") is False


# endregion


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


# endregion


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


# endregion


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


# endregion


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
    """run(): docker already installed (guard via fake _sh) → guard-skip log, verify skipped in dry-run."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("DOCKER_INSTALLER_DRY_RUN", "1")
    # force guard: _sh returns docker version for the first docker --version call (guard probe)
    calls: list[str] = []

    def fake_sh(*args: str, dry: bool = False) -> str:
        calls.append(" ".join(args))
        if args[:2] == ("docker", "--version"):
            return "Docker version 26.1.3\n"  # guard sees docker installed
        return ""

    monkeypatch.setattr(di, "_sh", fake_sh)
    ok = di.run()
    assert ok is True
    assert any("[IMP:8]" in r.message and "already installed" in r.message for r in caplog.records), (
        "guard-skip log expected"
    )
    # docker-compose install must NOT be attempted
    assert not any("apt-get install" in c and "docker-ce" in c for c in calls), "install must be skipped on guard"


# endregion


# region TEST_build_repo_command
def test_build_repo_command() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_repo_command — DevPlan 118 E migration unit test
    """build_repo_command: deb [arch=.. signed-by=..] URL codename stable."""
    cmd = di.build_repo_command("amd64", "noble", "/etc/apt/keyrings/docker.gpg", "/etc/apt/sources.list.d/docker.list")
    assert cmd[0] == "deb"
    assert "[arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg]" in cmd
    assert "https://download.docker.com/linux/ubuntu" in cmd
    assert cmd[-2:] == ["noble", "stable"]


# endregion
