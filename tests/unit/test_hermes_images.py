#!/usr/bin/env python3
# GREP_SUMMARY: test-hermes-images L1 L2 build guard context docker-build mock-subprocess buildkit
# STRUCTURE: ┌mock subprocess.run┐ → ◇ test build_l1 cmd → ◇ test build_l2 CONTEXT guard (empty → False) → ◇ test build_l2 cmd → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/build/hermes_images.py (DevPlan 118 E8 — Python-порт hermes-images.sh).
##           Native imports, mock subprocess.run — no real docker.
## @scope    Tests: L1 build command construction, L2 CONTEXT guard (fail-fast), L2 build command
##           with --build-arg, subprocess failure propagation, timeout handling.
## @invariants
##   - All tests use unittest.mock.patch("subprocess.run") — no real docker
##   - PLATFORM_ROOT overridden to tmp_path via monkeypatch (zero hardcoded repo paths)
##   - LDD: IMP:9 log on success, IMP:10 on CONTEXT guard failure
## @rationale E8 Strangler: docker build оркестрация → Python. Guard и build-команда — тестируемы.
## @changes  2026-08-02 | DevPlan 118 E8 — Created
# endregion MODULE_CONTRACT

import logging
import subprocess
from unittest import mock

import pytest

import core.internal.build.hermes_images as hermes_images


@pytest.fixture(autouse=True)
def _isolate_platform_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point PLATFORM_ROOT at tmp_path so build commands use temp paths."""
    fake_root = tmp_path / "repo"
    (fake_root / "core" / "modules" / "hermes-agent" / "build").mkdir(parents=True)
    (fake_root / "core" / "modules" / "hermes-agent" / "context").mkdir(parents=True)
    monkeypatch.setattr(hermes_images, "PLATFORM_ROOT", fake_root)
    monkeypatch.setattr(hermes_images, "_CACHE_DIR", str(tmp_path / "cache"))


# region TEST_build_l1
def test_build_l1_constructs_platform_amd64_cmd(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l1_constructs_platform_amd64_cmd — DevPlan 118 E migration unit test
    """build_l1: docker build --platform linux/amd64 with BuildKit cache, -t hermes-agent-base."""
    caplog.set_level(logging.INFO)

    captured: list[list[str]] = []
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=0))
    monkeypatch.setattr(subprocess, "run", lambda cmd, timeout: captured.append(cmd) or mock_run.return_value)

    ok = hermes_images.build_l1()
    assert ok is True

    cmd = captured[0]
    assert cmd[0] == "docker"
    assert "--platform" in cmd and "linux/amd64" in cmd
    assert "-t" in cmd and "hermes-agent-base" in cmd
    assert any("type=local,src=" in c for c in cmd), "BuildKit cache-from expected"
    assert any("type=local,dest=" in c and "mode=max" in c for c in cmd), "BuildKit cache-to mode=max expected"

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 build-complete log expected"


def test_build_l1_subprocess_failure_returns_false(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l1_subprocess_failure_returns_false — DevPlan 118 E migration unit test
    """build_l1: docker build non-zero exit → False + IMP:10 log."""
    caplog.set_level(logging.INFO)
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=1))
    monkeypatch.setattr(subprocess, "run", lambda cmd, timeout: mock_run.return_value)

    assert hermes_images.build_l1() is False
    found_imp10 = any("[IMP:10]" in r.message for r in caplog.records)
    assert found_imp10, "IMP:10 FAILED log expected"


# endregion


# region TEST_build_l2_guard
def test_build_l2_empty_context_fails_fast(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l2_empty_context_fails_fast — DevPlan 118 E migration unit test
    """build_l2: empty CONTEXT → False (guard), no docker build invoked."""
    caplog.set_level(logging.INFO)
    called = mock.MagicMock()
    monkeypatch.setattr(subprocess, "run", lambda cmd, timeout: called())

    ok = hermes_images.build_l2("")
    assert ok is False
    called.assert_not_called(), "docker build must NOT run when CONTEXT is empty (guard)"

    found_imp10 = any("[IMP:10]" in r.message and "CONTEXT" in r.message for r in caplog.records)
    assert found_imp10, "IMP:10 CONTEXT guard error expected"


def test_build_l2_constructs_cmd_with_build_arg(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_l2_constructs_cmd_with_build_arg — DevPlan 118 E migration unit test
    """build_l2: docker build with --build-arg CONTEXT=<ctx>, -t hermes-agent-context."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []
    mock_run = mock.MagicMock(return_value=mock.MagicMock(returncode=0))
    monkeypatch.setattr(subprocess, "run", lambda cmd, timeout: captured.append(cmd) or mock_run.return_value)

    ok = hermes_images.build_l2("my-org")
    assert ok is True

    cmd = captured[0]
    assert "--build-arg" in cmd and "CONTEXT=my-org" in cmd
    assert "-t" in cmd and "hermes-agent-context" in cmd
    assert "--platform" in cmd and "linux/amd64" in cmd

    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 build-complete log expected"


# endregion


# region TEST_main_dispatch
def test_main_build_platform_dispatches_l1(monkeypatch, capsys) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_build_platform_dispatches_l1 — DevPlan 118 E migration unit test
    """main(): build-platform → build_l1 (exit 0)."""
    monkeypatch.setattr(hermes_images, "build_l1", lambda: True)
    monkeypatch.setattr("sys.argv", ["hermes_images", "build-platform"])
    assert hermes_images.main() == 0


def test_main_build_context_guard_via_main(monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_build_context_guard_via_main — DevPlan 118 E migration unit test
    """main(): build-context without CONTEXT env → exit 1."""
    caplog.set_level(logging.INFO)
    monkeypatch.delenv("CONTEXT", raising=False)
    monkeypatch.setattr("sys.argv", ["hermes_images", "build-context"])
    assert hermes_images.main() == 1
    found = any("[IMP:10]" in r.message and "CONTEXT" in r.message for r in caplog.records)
    assert found, "IMP:10 CONTEXT guard error must be logged on main() dispatch"


# endregion
