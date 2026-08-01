#!/usr/bin/env python3
# GREP_SUMMARY: test-watchdog-docker-ops docker-manager compose-down compose-pull compose-up cleanup-images stop-container container-status run-docker
# STRUCTURE: ┌8 test functions┐ → ◇ _run_docker (2) → ◇ compose_* delegation (3) → ◇ cleanup_old_images (2) → ◇ stop_container (1)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/hermes-agent/watchdog/docker_ops.py — DockerManager extracted
##           from agent_watchdog.py (DevPlan 117 G T52). Characterization: reproduces pre-refactor behavior.
## @scope    No real Docker — subprocess and shared docker_compose mocked.
## @invariants
##   - All subprocess calls mocked
##   - Branch coverage target: ≥80% (AC-G3)
## @rationale  DevPlan 117 G T52 §TEST_SPEC — docker_ops direct tests after extraction.
## @changes  2026-08-01 · DevPlan 117 G T52 — created
# endregion MODULE_CONTRACT

import sys
from pathlib import Path
from unittest import mock

# watchdog/ dir import path (same pattern as test_agent_watchdog.py).
_WATCHDOG_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent" / "watchdog"
if str(_WATCHDOG_DIR) not in sys.path:
    sys.path.insert(0, str(_WATCHDOG_DIR))

from docker_ops import DockerManager


class TestDockerManager:
    """Tests for DockerManager — all docker commands mocked."""

    def _make(self) -> DockerManager:
        return DockerManager(
            "/opt/platform/core/modules/hermes-agent/docker-compose.base.yml",
            "hermes-agent",
            "/opt/platform/core/modules/hermes-agent",
        )

    # 🧪 TRAP[TEST] · Regression · Scenario: _run_docker success
    # · Expect: subprocess run called with sudo docker prefix
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: _run_docker logic changes
    def test_run_docker_success(self) -> None:
        """_run_docker → subprocess.run with sudo docker prefix."""
        dm = self._make()
        with mock.patch(
            "docker_ops.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout="out", stderr=""),
        ) as mock_run:
            result = dm._run_docker(["ps"])

        assert result.returncode == 0
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["sudo", "docker", "ps"]

    # 🧪 TRAP[TEST] · Regression · Scenario: _run_docker timeout
    # · Expect: returncode 124 CompletedProcess
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: _run_docker timeout handling changes
    def test_run_docker_timeout(self) -> None:
        """TimeoutExpired → returncode 124."""
        dm = self._make()
        with mock.patch(
            "docker_ops.subprocess.run",
            side_effect=__import__("subprocess").TimeoutExpired("docker", 30),
        ):
            result = dm._run_docker(["ps"])

        assert result.returncode == 124

    # 🧪 TRAP[TEST] · Regression · Scenario: _run_docker docker missing
    # · Expect: returncode 127
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: _run_docker FileNotFoundError handling changes
    def test_run_docker_not_found(self) -> None:
        """FileNotFoundError → returncode 127."""
        dm = self._make()
        with mock.patch("docker_ops.subprocess.run", side_effect=FileNotFoundError("docker")):
            result = dm._run_docker(["ps"])

        assert result.returncode == 127

    # 🧪 TRAP[TEST] · Regression · Scenario: compose_down delegates to shared
    # · Expect: docker_compose_down called with module_dir + compose_args
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: compose_down delegation changes
    def test_compose_down_delegates(self) -> None:
        """compose_down → shared docker_compose_down called."""
        dm = self._make()
        with mock.patch("docker_ops.docker_compose_down", return_value=True) as mock_down:
            ok = dm.compose_down("hermes-agent")

        assert ok is True
        mock_down.assert_called_once()
        args, kwargs = mock_down.call_args
        assert "hermes-agent" in kwargs.get("service", args[-1])
        assert kwargs["compose_args"][0] == "-f"

    # 🧪 TRAP[TEST] · Regression · Scenario: compose_pull delegates to shared
    # · Expect: docker_compose_pull called; non-zero → WARN not crash
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: compose_pull delegation changes
    def test_compose_pull_delegates(self, caplog) -> None:
        """compose_pull → shared docker_compose_pull called; False → WARN."""
        caplog.set_level(0)
        dm = self._make()
        with mock.patch("docker_ops.docker_compose_pull", return_value=False) as mock_pull:
            ok = dm.compose_pull()

        assert ok is False
        mock_pull.assert_called_once()
        assert any("CRITICAL: docker compose pull failed" in r.message for r in caplog.records)

    # 🧪 TRAP[TEST] · Regression · Scenario: compose_up delegates to shared
    # · Expect: docker_compose_up called
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: compose_up delegation changes
    def test_compose_up_delegates(self) -> None:
        """compose_up → shared docker_compose_up called."""
        dm = self._make()
        with mock.patch("docker_ops.docker_compose_up", return_value=True) as mock_up:
            ok = dm.compose_up("hermes-agent")

        assert ok is True
        mock_up.assert_called_once()

    # 🧪 TRAP[TEST] · Regression · Scenario: cleanup_old_images with many images
    # · Expect: keeps newest `keep`, removes rest
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: cleanup_old_images logic changes
    def test_cleanup_old_images(self) -> None:
        """3 images keep=1 → 2 removed."""
        dm = self._make()
        img_output = (
            "hermes-agent:v1 2026-08-01 10:00:00 +0000 UTC\n"
            "hermes-agent:v2 2026-08-01 11:00:00 +0000 UTC\n"
            "hermes-agent:v3 2026-08-01 12:00:00 +0000 UTC\n"
        )
        calls = []

        def mock_run(args, timeout=600, **kwargs):
            calls.append(args)
            if "image" in args and "ls" in args:
                return mock.MagicMock(returncode=0, stdout=img_output, stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with mock.patch("docker_ops.subprocess.run", side_effect=mock_run):
            removed = dm.cleanup_old_images(1)

        assert removed == 2
        rmi_calls = [c for c in calls if "rmi" in c]
        assert len(rmi_calls) == 2

    # 🧪 TRAP[TEST] · Regression · Scenario: cleanup with no images
    # · Expect: 0 removed
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: cleanup_old_images empty handling changes
    def test_cleanup_old_images_empty(self) -> None:
        """No images → 0 removed."""
        dm = self._make()
        with mock.patch(
            "docker_ops.subprocess.run",
            return_value=mock.MagicMock(returncode=0, stdout="", stderr=""),
        ):
            assert dm.cleanup_old_images(3) == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: stop_container already stopped
    # · Expect: True without stop call
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: stop_container logic changes
    def test_stop_container_not_running(self) -> None:
        """Container not in ps list → True (already stopped)."""
        dm = self._make()
        calls = []

        def mock_run(args, timeout=600, **kwargs):
            calls.append(args)
            if args == ["sudo", "docker", "ps", "--format", "{{.Names}}"]:
                return mock.MagicMock(returncode=0, stdout="nginx\npostgres\n", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with mock.patch("docker_ops.subprocess.run", side_effect=mock_run):
            result = dm.stop_container("redis")

        assert result is True
        stop_calls = [c for c in calls if c[2] == "stop"]
        assert len(stop_calls) == 0

    # 🧪 TRAP[TEST] · Regression · Scenario: stop_container running → stop succeeds
    # · Expect: True, stop called
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: stop_container logic changes
    def test_stop_container_running(self) -> None:
        """Container running → docker stop called, True."""
        dm = self._make()

        def mock_run(args, timeout=600, **kwargs):
            if args == ["sudo", "docker", "ps", "--format", "{{.Names}}"]:
                return mock.MagicMock(returncode=0, stdout="redis\n", stderr="")
            if args[2] == "stop":
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with mock.patch("docker_ops.subprocess.run", side_effect=mock_run):
            result = dm.stop_container("redis")

        assert result is True

    # 🧪 TRAP[TEST] · Regression · Scenario: stop fails → kill fallback
    # · Expect: kill called, True if kill succeeds
    # · Last fail: None (new test for DevPlan 117 G T52)
    # · Remove if: stop_container kill fallback changes
    def test_stop_container_kill_fallback(self) -> None:
        """stop fails → kill fallback → True."""
        dm = self._make()
        calls = []

        def mock_run(args, timeout=600, **kwargs):
            calls.append(args)
            if args == ["sudo", "docker", "ps", "--format", "{{.Names}}"]:
                return mock.MagicMock(returncode=0, stdout="redis\n", stderr="")
            if args[2] == "stop":
                return mock.MagicMock(returncode=1, stdout="", stderr="")
            if args[2] == "kill":
                return mock.MagicMock(returncode=0, stdout="", stderr="")
            return mock.MagicMock(returncode=0, stdout="", stderr="")

        with mock.patch("docker_ops.subprocess.run", side_effect=mock_run):
            result = dm.stop_container("redis")

        assert result is True
        assert any(c[2] == "kill" for c in calls)
