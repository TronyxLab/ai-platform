#!/usr/bin/env python3
# GREP_SUMMARY: test-llm-provision render litellm-config provision-llm subprocess non-fatal CORE_DIR
# STRUCTURE: ┌6 test functions┐ → ◇ success (1) → ◇ render script missing (1) → ◇ provision missing (1)
#            → ◇ provision non-zero (1) → ◇ subprocess raises (2)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/deploy/llm_provision.py — render_and_provision_llm()
##           (DevPlan 117 G T58.5 extraction from context_deployer.py).
## @scope    No real subprocess — all runs mocked, CORE_DIR pointed at tmp_path fixtures.
## @invariants
##   - All subprocess calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T58.5 §TEST_SPEC — llm_provision direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T58.5 — created
# endregion MODULE_CONTRACT

from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.deploy.llm_provision import render_and_provision_llm


class TestRenderAndProvisionLlm:
    """Tests for render_and_provision_llm() — all subprocess mocked."""

    # 🧪 TRAP[TEST] · Regression · Scenario: both subprocess calls succeed
    # · Expect: IMP:9 logs for render + provision
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: llm pipeline flow changes
    def test_render_and_provision_success(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """Both steps succeed → IMP:9 logs present."""
        caplog.set_level(0)
        monkeypatch.setenv("CORE_DIR", str(tmp_path))
        core_dir = Path(str(tmp_path))
        renderer = core_dir / "internal" / "llm" / "config_renderer.py"
        renderer.parent.mkdir(parents=True, exist_ok=True)
        renderer.write_text("mock\n", encoding="utf-8")
        provision = core_dir / "entrypoints" / "provision-llm.sh"
        provision.parent.mkdir(parents=True, exist_ok=True)
        provision.write_text("mock\n", encoding="utf-8")

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="ok", stderr="")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", return_value=True),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm()

        assert len(calls) == 2
        assert any("litellm-config.yml rendered" in r.message for r in caplog.records)
        assert any("Key provisioning succeeded" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: config_renderer.py missing
    # · Expect: WARN (non-fatal), provision still attempted
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: render-missing handling changes
    def test_render_missing_script_non_fatal(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """config_renderer.py absent → WARN, no exception."""
        caplog.set_level(0)
        monkeypatch.setenv("CORE_DIR", str(tmp_path))

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", return_value=False),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run") as mock_run,
        ):
            render_and_provision_llm()

        mock_run.assert_not_called()
        assert any("config_renderer.py not found" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: provision-llm.sh missing
    # · Expect: render runs, provision WARN (non-fatal)
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: provision-missing handling changes
    def test_provision_missing_script(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """provision-llm.sh absent → WARN, only render runs."""
        caplog.set_level(0)
        monkeypatch.setenv("CORE_DIR", str(tmp_path))
        calls = []

        def isfile(path):
            return str(path).endswith("config_renderer.py")

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.MagicMock(returncode=0, stdout="ok", stderr="")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm()

        assert len(calls) == 1
        assert any("provision-llm.sh not found" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: provision returns non-zero
    # · Expect: WARN with stderr excerpt, no exception
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: provision-nonzero handling changes
    def test_provision_nonzero_warns(self, tmp_path: Path, caplog, monkeypatch) -> None:
        """provision-llm.sh returns 3 → WARN with stderr excerpt."""
        caplog.set_level(0)
        monkeypatch.setenv("CORE_DIR", str(tmp_path))

        def isfile(path):
            return True

        def mock_run(cmd, **kwargs):
            if "config_renderer.py" in cmd[1]:
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            return mock.MagicMock(returncode=3, stdout="", stderr="boom\nboom2\n")

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=mock_run),
        ):
            render_and_provision_llm()

        assert any("Key provisioning returned 3" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: subprocess raises during render
    # · Expect: WARN (non-fatal), provision still attempted
    # · Last fail: None (new test for DevPlan 117 G T58.5)
    # · Remove if: subprocess error handling changes
    @pytest.mark.parametrize(
        "exc",
        [
            __import__("subprocess").CalledProcessError(1, "python3"),
            OSError("boom"),
            FileNotFoundError("python3"),
        ],
        ids=["called-process", "os-error", "file-not-found"],
    )
    def test_render_subprocess_error(self, exc, tmp_path: Path, caplog, monkeypatch) -> None:
        """Render subprocess raises → WARN, pipeline continues."""
        caplog.set_level(0)
        monkeypatch.setenv("CORE_DIR", str(tmp_path))

        def isfile(path):
            return True

        with (
            mock.patch("core.internal.bootstrap.deploy.llm_provision.os.path.isfile", side_effect=isfile),
            mock.patch("core.internal.bootstrap.deploy.llm_provision.subprocess.run", side_effect=exc),
        ):
            render_and_provision_llm()

        assert any("Failed to render litellm-config.yml" in r.message for r in caplog.records)
