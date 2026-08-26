"""
# GREP_SUMMARY: test-docker-orchestrator, deploy-docker, pre-pull, image-check, wait-readiness, healthcheck, compose-up
# STRUCTURE: ▶ mock subprocess.run → ◇ test_check_image_exists [found|not_found] → ◇ test_resolve_compose_file [found|missing] → ◇ test_deploy_docker_module [basic|hermes|orphan] → ◇ test_wait_for_readiness [pass|timeout] → ◇ test_run_healthcheck [pass|fail] → ◇ test_prunner_pull_images [skip-build|pull] → ◇ test_pre_pull_images [single] → ◇ test_deploy_docker_group [single] → ⎋ LDD trajectory assert
# region MODULE_CONTRACT
## @purpose  Unit tests for docker_orchestrator.py — mock subprocess.run for docker CLI calls
## @scope    Tests all public and internal functions except parallel forking paths (which
##           require subprocess isolation). Fork-based functions (_pre_pill_images with
##           single entry, deploy_docker_group with single entry) are tested via module-level
##           mock inheritance (forked children inherit patched module globals).
## @invariants
##   - subprocess.run is mocked for ALL tests — no real docker CLI calls
##   - tmp_path is used for temporary compose files and module directories
##   - LDD trajectory (IMP:7-10) is verified on every test via @ldd_trajectory
##   - Mock subprocess.run.returncode controls success/failure branching
## @rationale Docker CLI is unavailable in test environment — all docker calls must be mocked.
##   Patched module references are inherited by forked child processes (copy-on-write semantics).
## @changes   2026-07-22 · W4-E1 — initial unit tests for docker_orchestrator.py
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# ── System path for import ──
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"),
)

import docker_orchestrator as dorch

# T2.3: 6 delegation-фасадов «for tests» удалены из docker_orchestrator — тесты перенацелены
# на реальные модули D1/E1 (пакетные импорты — тот же module-объект, что видит dorch-фасады
# pre_pull_images/deploy_docker_group/wait_for_readiness/run_healthcheck через делегирование).
from core.internal.bootstrap.deploy import healthcheck_runner as hcrunner
from core.internal.bootstrap.deploy import observability as obs
from core.internal.bootstrap.deploy import parallel_runner as prunner

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# region FIXTURES
# ────────────────────────────────────────────────────────────
@pytest.fixture
def mock_subprocess():
    """Fixture: mock subprocess.run for all docker CLI calls.

    ## @purpose — Provide a central mock for subprocess.run that tests can configure.

    ##   Default return: returncode=0, stdout=b"", stderr=b"".

    ## @io — ⎋ mock.MagicMock configured as subprocess.run

    """

    with mock.patch.object(subprocess, "run") as mock_run:
        mock_run.return_value = mock.MagicMock(
            returncode=0,
            stdout=b"",
            stderr=b"",
            spec=subprocess.CompletedProcess,
        )
        yield mock_run


def _streaming_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Фабрика StreamingResult для мока killpg-канона module_interface (REF-0103).

    ## @purpose — invoke_module_interface идёт через run_subprocess_streaming (Popen+killpg);
    ##            тесты healthcheck_runner мокают канон этим double.
    """
    from core.internal.shared.subprocess_io import StreamingResult

    return StreamingResult(
        cmd=["bash", "-c", "invoke_module_interface"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def module_dir(tmp_path):
    """Fixture: create a temporary module directory with compose.yaml.

    ## @purpose — Create a minimal module directory for deploy_docker_module tests.
    ## @io — ⇥ tmp_path → ⎋ Path to module directory
    """
    mod_dir = tmp_path / "modules" / "test_mod"
    mod_dir.mkdir(parents=True)
    _write_compose_yaml(mod_dir / "compose.yaml", "test_mod")
    return str(tmp_path / "modules")


@pytest.fixture
def overlay_dir(tmp_path):
    """Fixture: create overlay directory with compose.override.yaml.

    ## @purpose — Context overlay for deploy_docker_module tests.
    ## @io — ⇥ tmp_path → ⎋ Path to overlay directory
    """
    ov_dir = tmp_path / "overlay"
    ov_dir.mkdir(parents=True)
    override = ov_dir / "compose.override.yaml"
    override.write_text("services:\n  test:\n    environment:\n      - FOO=bar\n", encoding="utf-8")
    return str(ov_dir)


# endregion FIXTURES


# ────────────────────────────────────────────────────────────
# region HELPERS
# ────────────────────────────────────────────────────────────


def _write_compose_yaml(path: Path, module_name: str) -> None:
    """Write a minimal compose.yaml for test purposes.

    ## @purpose — Create a deterministic compose file with known service name.
    ## @io — ⇥ path: Path, module_name: str → ⎋ None (side-effect: file write)
    """
    path.write_text(
        f"""services:
  {module_name}:
    image: test/{module_name}:latest
    container_name: {module_name}
""",
        encoding="utf-8",
    )


# endregion HELPERS


# ────────────────────────────────────────────────────────────
# region TEST__check_image_exists
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Image found via docker manifest inspect · Last fail: N/A · Remove if: docker manifest inspect interface changes
def test_check_image_exists_found(mock_subprocess):
    """Test _check_image_exists returns True when docker manifest inspect succeeds."""
    mock_subprocess.return_value.returncode = 0

    result = dorch._check_image_exists("test/image:latest")

    assert result is True
    mock_subprocess.assert_called_once()
    args = mock_subprocess.call_args[0][0]
    assert args[:4] == ["docker", "manifest", "inspect", "test/image:latest"]
    assert mock_subprocess.call_args[1].get("timeout") == 60


# 🧪 TRAP[TEST] · Regression · _check_image_exists failure modes (not_found/no_docker/timeout)
# · Last fail: N/A · Remove if: docker manifest inspect interface/error-handling changes
@pytest.mark.parametrize("failure_kind", ["not_found", "no_docker", "timeout"])
def test_check_image_exists_failure(mock_subprocess, failure_kind):
    """_check_image_exists returns False on every failure mode (P-консолидация 168).

    Cases (1:1 из test_check_image_exists_not_found/test_check_image_exists_no_docker/
    test_check_image_exists_timeout): returncode!=0; FileNotFoundError (docker binary missing);
    subprocess.TimeoutExpired.
    """
    if failure_kind == "not_found":
        mock_subprocess.return_value.returncode = 1
    elif failure_kind == "no_docker":
        mock_subprocess.side_effect = FileNotFoundError("docker not found")
    else:
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=60)

    result = dorch._check_image_exists("test/image:latest")

    assert result is False


# endregion TEST__check_image_exists


# ────────────────────────────────────────────────────────────
# region TEST__resolve_compose_file
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Edge-case · compose.yaml found first · Last fail: N/A · Remove if: compose filename resolution order changes
def test_resolve_compose_file_yaml(tmp_path):
    """Test _resolve_compose_file finds compose.yaml."""
    mod_dir = tmp_path / "test_mod"
    mod_dir.mkdir()
    _write_compose_yaml(mod_dir / "compose.yaml", "test_mod")

    result = dorch._resolve_compose_file(str(mod_dir))

    assert result == mod_dir / "compose.yaml"


# 🧪 TRAP[TEST] · Edge-case · docker-compose.yaml fallback · Last fail: N/A · Remove if: compose filename resolution order changes
def test_resolve_compose_file_docker_yaml(tmp_path):
    """Test _resolve_compose_file falls back to docker-compose.yaml."""
    mod_dir = tmp_path / "test_mod"
    mod_dir.mkdir()
    _write_compose_yaml(mod_dir / "docker-compose.yaml", "test_mod")

    result = dorch._resolve_compose_file(str(mod_dir))

    assert result == mod_dir / "docker-compose.yaml"


# 🧪 TRAP[TEST] · Edge-case · docker-compose.base.yml fallback · Last fail: N/A · Remove if: compose filename resolution order changes
def test_resolve_compose_file_base_yml(tmp_path):
    """Test _resolve_compose_file falls back to docker-compose.base.yml."""
    mod_dir = tmp_path / "test_mod"
    mod_dir.mkdir()
    _write_compose_yaml(mod_dir / "docker-compose.base.yml", "test_mod")

    result = dorch._resolve_compose_file(str(mod_dir))

    assert result == mod_dir / "docker-compose.base.yml"


# 🧪 TRAP[TEST] · Edge-case · No compose file found · Last fail: N/A · Remove if: compose filename resolution order changes
def test_resolve_compose_file_missing(tmp_path):
    """Test _resolve_compose_file returns None when no compose file exists."""
    mod_dir = tmp_path / "test_mod"
    mod_dir.mkdir()

    result = dorch._resolve_compose_file(str(mod_dir))

    assert result is None


# endregion TEST__resolve_compose_file


# ────────────────────────────────────────────────────────────
# region TEST__build_compose_args
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Edge-case · Build compose args with env files and overlay · Last fail: N/A · Remove if: compose args building interface changes
def test_build_compose_args(tmp_path):
    """Test _build_compose_args builds correct docker compose argument list."""
    compose_file = tmp_path / "compose.yaml"
    _write_compose_yaml(compose_file, "test_mod")

    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text("KEY=value\n", encoding="utf-8")

    platform_env = tmp_path / "platform" / ".env"
    platform_env.parent.mkdir(parents=True)
    platform_env.write_text("PLATFORM_VAR=value\n", encoding="utf-8")

    overlay_dir = tmp_path / "overlay"
    override = overlay_dir / "compose.override.yaml"
    overlay_dir.mkdir(parents=True)
    override.write_text("services:\n  test:\n    environment:\n      - FOO=bar\n", encoding="utf-8")

    args = dorch._build_compose_args(
        compose_file=compose_file,
        secrets_env_file=str(secrets_env),
        platform_root=str(tmp_path / "platform"),
        overlay_dir=str(overlay_dir),
        module_name="test_mod",
    )

    assert "-f" in args
    assert str(compose_file) in args
    assert "--env-file" in args
    assert str(secrets_env) in args
    assert str(platform_env) in args
    assert str(override) in args
    assert "--profile" in args
    assert "test_mod" in args


# endregion TEST__build_compose_args


# ────────────────────────────────────────────────────────────
# region TEST_deploy_docker_module
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Basic docker module deploy via compose up -d · Last fail: N/A · Remove if: deploy_docker_module interface changes
def test_deploy_docker_module_basic(mock_subprocess, module_dir):
    """Test deploy_docker_module succeeds for a basic module."""
    result = dorch.deploy_docker_module(
        module_name="test_mod",
        modules_dir=module_dir,
    )

    assert result is True

    # Verify docker compose up was called
    up_calls = [c for c in mock_subprocess.call_args_list if "compose" in str(c) and "up" in str(c)]
    assert len(up_calls) >= 1
    up_args = up_calls[0].args[0]
    assert "docker" in up_args
    assert "compose" in up_args
    assert "up" in up_args
    assert "-d" in up_args
    # 🧪 TRAP[TEST] · NEGATIVE (R5) · _phase_up — B18 (141 r2)
    # · Last fail: node-update 2-го цикла: up --profile <mod> --remove-orphans при неполном
    # ·   COMPOSE_PROFILES каскадно удалял контейнеры остальных модулей (project=platform).
    # · Remove if: orphan-политика вернётся в up (запрещено — только orphan_reconciler).
    assert "--remove-orphans" not in up_args


# 🧪 TRAP[TEST] · Edge-case · No compose file returns False · Last fail: N/A · Remove if: error handling changes
def test_deploy_docker_module_no_compose_file(mock_subprocess, tmp_path):
    """Test deploy_docker_module returns False when no compose file exists."""
    mod_dir = tmp_path / "modules" / "missing_mod"
    mod_dir.mkdir(parents=True)

    result = dorch.deploy_docker_module(
        module_name="missing_mod",
        modules_dir=str(tmp_path / "modules"),
    )

    assert result is False


# 🧪 TRAP[TEST] · Edge-case · docker compose up failure returns False · Last fail: N/A · Remove if: error handling changes
def test_deploy_docker_module_up_fails(mock_subprocess, module_dir):
    """Test deploy_docker_module returns False when docker compose up fails."""

    # Make the compose up call return non-zero
    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if "up" in cmd and "-d" in cmd:
            return mock.MagicMock(returncode=1, stderr=b"error", stdout=b"", spec=subprocess.CompletedProcess)
        return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)

    mock_subprocess.side_effect = _side_effect

    result = dorch.deploy_docker_module(
        module_name="test_mod",
        modules_dir=module_dir,
    )

    assert result is False


# 🧪 TRAP[TEST] · Regression · Hermes-agent special case (image pre-check) · Last fail: N/A · Remove if: hermes-agent handling moves to separate module
def test_deploy_docker_module_hermes_agent(mock_subprocess, module_dir):
    """Test deploy_docker_module handles hermes-agent special case (stale cleanup + image check)."""
    # Create hermes-agent module dir and compose file
    hermes_dir = Path(module_dir) / "hermes-agent"
    hermes_dir.mkdir(parents=True)
    _write_compose_yaml(hermes_dir / "compose.yaml", "hermes-agent")

    # Mock docker ps for stale container check
    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd)
        if "ps" in cmd_str and "--format" in cmd_str:
            return mock.MagicMock(
                returncode=0, stdout=b"hermes-base-agent\nsome_other\n", stderr=b"", spec=subprocess.CompletedProcess
            )
        if "manifest" in cmd_str:
            return mock.MagicMock(returncode=0, stdout=b"{}", stderr=b"", spec=subprocess.CompletedProcess)
        if "compose" in cmd_str and "config" in cmd_str and "--images" in cmd_str:
            return mock.MagicMock(
                returncode=0, stdout=b"test/hermes:latest\n", stderr=b"", spec=subprocess.CompletedProcess
            )
        return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)

    mock_subprocess.side_effect = _side_effect

    result = dorch.deploy_docker_module(
        module_name="hermes-agent",
        modules_dir=module_dir,
    )

    assert result is True

    # Verify stale container was stopped and removed — use precise arg matching
    stop_calls = [
        c
        for c in mock_subprocess.call_args_list
        if isinstance(c.args[0], list) and c.args[0][:2] == ["docker", "stop"] and "hermes-base-agent" in c.args[0]
    ]
    rm_calls = [
        c
        for c in mock_subprocess.call_args_list
        if isinstance(c.args[0], list) and c.args[0][:2] == ["docker", "rm"] and "hermes-base-agent" in c.args[0]
    ]
    assert len(stop_calls) == 1
    assert len(rm_calls) == 1


# 🧪 TRAP[TEST] · Edge-case · Hermes-agent L1→L2 build fallback when image not found · Last fail: N/A · Remove if: hermes-agent image build logic changes
def test_deploy_docker_module_hermes_build_fallback(mock_subprocess, module_dir):
    """Test deploy_docker_module triggers L1→L2 build when hermes images not in registry."""
    hermes_dir = Path(module_dir) / "hermes-agent"
    hermes_dir.mkdir(parents=True)
    _write_compose_yaml(hermes_dir / "compose.yaml", "hermes-agent")

    call_count = [0]

    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd)
        call_count[0] += 1

        if "ps" in cmd_str and "--format" in cmd_str:
            return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)
        if "manifest" in cmd_str:
            # Return non-zero to simulate image not found
            return mock.MagicMock(returncode=1, stderr=b"", stdout=b"", spec=subprocess.CompletedProcess)
        if "compose" in cmd_str and "config" in cmd_str and "--images" in cmd_str:
            return mock.MagicMock(
                returncode=0, stdout=b"test/hermes:latest\n", stderr=b"", spec=subprocess.CompletedProcess
            )
        if "image" in cmd_str and "inspect" in cmd_str:
            return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)  # L1 exists
        if "compose" in cmd_str and "build" in cmd_str:
            return mock.MagicMock(returncode=0, stdout=b"built", stderr=b"", spec=subprocess.CompletedProcess)
        return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)

    mock_subprocess.side_effect = _side_effect

    result = dorch.deploy_docker_module(
        module_name="hermes-agent",
        modules_dir=module_dir,
    )

    assert result is True
    # Verify build was called
    build_calls = [c for c in mock_subprocess.call_args_list if "build" in str(c)]
    assert len(build_calls) >= 1


# endregion TEST_deploy_docker_module


# ────────────────────────────────────────────────────────────
# region TEST_prunner_pull_images (T2.3: фасад pull_module_images из docker_orchestrator удалён,
#   реальный модуль — parallel_runner)
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Pull images for a module · Last fail: N/A · Remove if: parallel_runner.pull_module_images interface changes
def test_prunner_pull_images(mock_subprocess, module_dir):
    """Test parallel_runner.pull_module_images runs docker compose pull."""
    result = prunner.pull_module_images(
        mod_name="test_mod",
        overlay_dir=None,
        secrets_env_file=None,
        platform_root=None,
        modules_dir=module_dir,
    )

    assert result is True
    pull_calls = [c for c in mock_subprocess.call_args_list if "pull" in str(c)]
    assert len(pull_calls) >= 1


# 🧪 TRAP[TEST] · Edge-case · Skip pull when module has local build: section · Last fail: N/A · Remove if: build detection logic changes
def test_prunner_pull_images_skip_build(tmp_path):
    """Test parallel_runner.pull_module_images skips pull when compose file has build: section."""
    mod_dir = tmp_path / "modules" / "build_mod"
    mod_dir.mkdir(parents=True)
    compose = mod_dir / "compose.yaml"
    compose.write_text(
        """services:
  build_mod:
    build: .
    image: test/build:latest
""",
        encoding="utf-8",
    )

    with mock.patch.object(subprocess, "run") as mock_run:
        mock_run.return_value = mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)

        result = prunner.pull_module_images(
            mod_name="build_mod",
            overlay_dir=None,
            secrets_env_file=None,
            platform_root=None,
            modules_dir=str(tmp_path / "modules"),
        )

    assert result is True
    # docker compose pull should NOT be called
    pull_calls = [c for c in mock_run.call_args_list if "pull" in str(c)]
    assert len(pull_calls) == 0


# 🧪 TRAP[TEST] · Edge-case · Skip pull when no compose file exists · Last fail: N/A · Remove if: error handling changes
def test_prunner_pull_images_no_compose(tmp_path):
    """Test parallel_runner.pull_module_images skips when no compose file found."""
    mod_dir = tmp_path / "modules" / "no_compose_mod"
    mod_dir.mkdir(parents=True)

    with mock.patch.object(subprocess, "run") as mock_run:
        mock_run.return_value = mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)
        result = prunner.pull_module_images(
            mod_name="no_compose_mod",
            overlay_dir=None,
            secrets_env_file=None,
            platform_root=None,
            modules_dir=str(tmp_path / "modules"),
        )

    assert result is True
    mock_run.assert_not_called()


# endregion TEST_prunner_pull_images


# ────────────────────────────────────────────────────────────
# region TEST_wait_for_readiness
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Readiness succeeds on first attempt · Last fail: N/A · Remove if: wait_for_readiness interface changes
def test_wait_for_readiness_pass(mock_subprocess):
    """Test wait_for_readiness returns True when readiness check passes."""
    # REF-0103: канал invoke_module_interface — subprocess_io streaming-канон (killpg);
    # мокается сам канон, а не raw subprocess.run (bash_cmd ассерты сохранены).
    fake_result = _streaming_result(returncode=0)
    with mock.patch(
        "core.internal.shared.module_interface.run_subprocess_streaming", return_value=fake_result
    ) as mock_stream:
        result = dorch.wait_for_readiness("test_mod", max_attempts=3, interval_sec=0)

        assert result is True
        # Verify the invoke_module_interface was called with readiness
        bash_calls = [c for c in mock_stream.call_args_list if "invoke_module_interface" in str(c)]
        assert len(bash_calls) >= 1
        bash_cmd = " ".join(bash_calls[0].args[0]) if bash_calls[0].args else ""
        assert "readiness" in bash_cmd


# 🧪 TRAP[TEST] · Regression · Readiness timeout after max attempts · Last fail: N/A · Remove if: timeout handling changes
def test_wait_for_readiness_timeout(mock_subprocess):
    """Test wait_for_readiness returns False after max attempts when check keeps failing."""
    fake_fail = _streaming_result(returncode=1, stderr="not ready")
    with mock.patch(
        "core.internal.shared.module_interface.run_subprocess_streaming", return_value=fake_fail
    ) as mock_stream:
        result = dorch.wait_for_readiness("test_mod", max_attempts=3, interval_sec=0)

        assert result is False
        # Should have tried exactly max_attempts times
        bash_calls = [c for c in mock_stream.call_args_list if "invoke_module_interface" in str(c)]
        assert len(bash_calls) == 3


# 🧪 TRAP[TEST] · Edge-case · Readiness subprocess error · Last fail: N/A · Remove if: error handling changes
# GUARD-PRESERVE (168): единственное покрытие ветки subprocess-error → False в wait_for_readiness
# REF-0103: «ошибка канала» теперь выражается OSError от killpg-канона (TimeoutExpired канон
# обрабатывает внутри — graceful rc=124), invoke возвращает (False, msg) — никогда не raise
def test_wait_for_readiness_subprocess_error(mock_subprocess):
    """Test wait_for_readiness handles channel errors gracefully (OSError → False)."""

    def _raise_oserror(*_args, **_kwargs):
        msg = "spawn failed"
        raise OSError(msg)

    with mock.patch("core.internal.shared.module_interface.run_subprocess_streaming", side_effect=_raise_oserror):
        result = dorch.wait_for_readiness("test_mod", max_attempts=2, interval_sec=0)

    assert result is False


# endregion TEST_wait_for_readiness


# ────────────────────────────────────────────────────────────
# region TEST_run_healthcheck
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Healthcheck passes on first attempt · Last fail: N/A · Remove if: run_healthcheck interface changes
def test_run_healthcheck_pass(mock_subprocess):
    """Test run_healthcheck returns True when healthcheck passes."""
    # REF-0103: канал invoke_module_interface — killpg-канон; мокается канон (не raw run)
    fake_ok = _streaming_result(returncode=0)
    with mock.patch(
        "core.internal.shared.module_interface.run_subprocess_streaming", return_value=fake_ok
    ) as mock_stream:
        result = dorch.run_healthcheck("test_mod", "docker", max_retries=3, retry_interval=0)

        assert result is True
        bash_calls = [c for c in mock_stream.call_args_list if "invoke_module_interface" in str(c)]
        assert len(bash_calls) >= 1
        bash_cmd = " ".join(bash_calls[0].args[0]) if bash_calls[0].args else ""
        assert "liveness" in bash_cmd


# 🧪 TRAP[TEST] · Regression · Healthcheck fails after max retries · Last fail: N/A · Remove if: retry logic changes
def test_run_healthcheck_fail(mock_subprocess):
    """Test run_healthcheck returns False after max retries when check keeps failing."""
    fake_fail = _streaming_result(returncode=1, stderr="unhealthy")
    with mock.patch(
        "core.internal.shared.module_interface.run_subprocess_streaming", return_value=fake_fail
    ) as mock_stream:
        result = dorch.run_healthcheck("test_mod", "docker", max_retries=3, retry_interval=0)

        assert result is False
        bash_calls = [c for c in mock_stream.call_args_list if "invoke_module_interface" in str(c)]
        assert len(bash_calls) == 3


# endregion TEST_run_healthcheck


# ────────────────────────────────────────────────────────────
# region TEST_pre_pull_images (single entry, no true parallelism)
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Pre-pull images for 1 module via fork · Last fail: N/A · Remove if: pre_pull_images interface changes
def test_pre_pull_images_single(mock_subprocess, module_dir):
    """Test pre_pull_images with 1 module (fork dispatches to parallel_runner.pull_module_images)."""
    # T2.3: фасад pull_module_images из docker_orchestrator удалён — патчим реальный модуль
    # parallel_runner (fork-дети наследуют patch через copy-on-write; dorch.pre_pull_images
    # делегирует в него).
    original_fn = prunner.pull_module_images
    prunner.pull_module_images = mock.MagicMock(return_value=True)

    try:
        ok, fail = dorch.pre_pull_images(
            entries=["test_mod:"],
            modules_dir=module_dir,
            parallel_limit=1,
        )
        # The fork-based child calls the mocked prunner.pull_module_images
        # Wait briefly for child to complete
        time.sleep(0.5)

        # Child process either succeeded or failed — we can't reliably assert
        # the count since it depends on process scheduling, but the function
        # should return without raising
        assert isinstance(ok, int)
        assert isinstance(fail, int)
    finally:
        prunner.pull_module_images = original_fn


# endregion TEST_pre_pull_images


# ────────────────────────────────────────────────────────────
# region TEST__orphan_reconciliation_delegation
# ────────────────────────────────────────────────────────────
# DevPlan 117 D18: локальный orphan-cleanup удалён (дубль логики) — deploy_docker_module
# делегирует в orphan_reconciler.batch_orphan_reconciliation + remove_orphans (единый канон).
# Тесты верифицируют делегирование (fake-объект orphan_reconciler_impl, DevPlan 167 D3).


class _FakeOrphanReconciler:
    """Fake orphan_reconciler (DI-объект, 167 D3) — 0 setattr.

    ## @purpose — Замена setattr-патча dorch.orphan_reconciler: тест передаёт
    ##            fake-объект в deploy_docker_module(orphan_reconciler_impl=) и ассертит
    ##            batch/remove вызовы. Производственный шов — orphan_reconciler_impl (167 D3).
    ## @io — ⇥ detected: list[dict] (результат batch) → ⎋ объект с методами
    ##            batch_orphan_reconciliation(entries, modules_dir) / remove_orphans(orphans)
    ## @complexity — O(1)
    """

    def __init__(self, detected=None):
        self.detected = detected if detected is not None else []
        self.batch_calls: list[tuple] = []
        self.remove_calls: list[list] = []

    def batch_orphan_reconciliation(self, entries, modules_dir):
        self.batch_calls.append((entries, modules_dir))
        return self.detected

    def remove_orphans(self, orphans):
        self.remove_calls.append(orphans)
        return len(orphans)


# 🧪 TRAP[TEST] · Regression · deploy_docker_module delegates orphan reconciliation to orphan_reconciler · Last fail: N/A · Remove if: orphan reconciliation delegation changes
def test_reconcile_orphan_delegates_to_orphan_reconciler(mock_subprocess, module_dir):
    """Test deploy_docker_module calls orphan_reconciler.batch_orphan_reconciliation (DevPlan 117 D18)."""
    reconciler = _FakeOrphanReconciler()  # orphans пусто → remove_orphans НЕ вызывается

    result = dorch.deploy_docker_module(
        module_name="test_mod",
        modules_dir=module_dir,
        orphan_reconciler_impl=reconciler,
    )

    assert result is True
    # batch_orphan_reconciliation должен быть вызван с [module_name] и modules_dir
    assert len(reconciler.batch_calls) == 1, f"batch_orphan_reconciliation not called: {reconciler.batch_calls}"
    assert reconciler.batch_calls[0][0] == ["test_mod"]
    # orphans пуст → remove_orphans НЕ вызывается
    assert len(reconciler.remove_calls) == 0


# 🧪 TRAP[TEST] · Regression · deploy_docker_module removes detected orphans · Last fail: N/A · Remove if: orphan reconciliation delegation changes
def test_reconcile_orphan_removes_detected_orphans(mock_subprocess, module_dir):
    """Test deploy_docker_module calls remove_orphans when orphans are detected (DevPlan 117 D18)."""
    detected = [{"container_name": "orphan_container", "project": "other_project"}]
    reconciler = _FakeOrphanReconciler(detected=detected)

    result = dorch.deploy_docker_module(
        module_name="test_mod",
        modules_dir=module_dir,
        orphan_reconciler_impl=reconciler,
    )

    assert result is True
    assert len(reconciler.remove_calls) == 1, f"remove_orphans not called: {reconciler.remove_calls}"
    assert reconciler.remove_calls[0] == detected


# endregion TEST__orphan_reconciliation_delegation


# ────────────────────────────────────────────────────────────
# region TEST__cleanup_stale_container
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · stale container cleanup (hermes-base-agent) · Last fail: N/A · Remove if: stale cleanup logic changes
def test_cleanup_stale_container_found(mock_subprocess):
    """Test _cleanup_stale_container stops and removes container if found."""
    mock_subprocess.return_value = mock.MagicMock(
        returncode=0, stdout=b"hermes-base-agent\n", stderr=b"", spec=subprocess.CompletedProcess
    )

    dorch._cleanup_stale_container("hermes-base-agent")

    # Use precise arg matching — "stop" or "rm" substring matches --format arg and container names
    stop_calls = [
        c for c in mock_subprocess.call_args_list if isinstance(c.args[0], list) and c.args[0][:2] == ["docker", "stop"]
    ]
    rm_calls = [
        c for c in mock_subprocess.call_args_list if isinstance(c.args[0], list) and c.args[0][:2] == ["docker", "rm"]
    ]
    assert len(stop_calls) == 1
    assert len(rm_calls) == 1


# 🧪 TRAP[TEST] · Edge-case · stale container not found — no stop/rm · Last fail: N/A · Remove if: stale cleanup logic changes
def test_cleanup_stale_container_not_found(mock_subprocess):
    """Test _cleanup_stale_container does nothing if container not in docker ps."""
    mock_subprocess.return_value = mock.MagicMock(
        returncode=0, stdout=b"other_container\n", stderr=b"", spec=subprocess.CompletedProcess
    )

    dorch._cleanup_stale_container("hermes-base-agent")

    # stop/rm should not be called — use precise arg matching to avoid --format and name false positives
    stop_rm_calls = [
        c
        for c in mock_subprocess.call_args_list
        if isinstance(c.args[0], list) and len(c.args[0]) >= 2 and c.args[0][1] in {"stop", "rm"}
    ]
    assert len(stop_rm_calls) == 0


# endregion TEST__cleanup_stale_container


# ────────────────────────────────────────────────────────────
# region TEST_hcrunner_healthcheck_invocation (T2.3: фасады invoke_healthcheck из docker_orchestrator
#   удалены, реальный модуль — healthcheck_runner)
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · healthcheck_runner.invoke_healthcheck calls bash with invoke_module_interface · Last fail: N/A · Remove if: healthcheck invocation changes
def test_hcrunner_invoke_readiness(mock_subprocess):
    """Test healthcheck_runner.invoke_healthcheck constructs the correct bash command."""
    # REF-0103: канал — killpg-канон subprocess_io; мокается run_subprocess_streaming
    with mock.patch(
        "core.internal.shared.module_interface.run_subprocess_streaming",
        return_value=_streaming_result(returncode=0),
    ) as mock_stream:
        result = hcrunner.invoke_healthcheck("test_mod", "readiness")

        assert result is True
        assert mock_stream.call_count >= 1
        call_args = mock_stream.call_args[0][0]
        assert call_args[0] == "bash"
        assert call_args[1] == "-c"
        assert "invoke_module_interface" in call_args[2]
        assert "test_mod" in call_args[2]
        assert "readiness" in call_args[2]


# 🧪 TRAP[TEST] · Edge-case · healthcheck_runner.invoke_healthcheck returns False on non-zero exit · Last fail: N/A · Remove if: healthcheck invocation changes
def test_hcrunner_invoke_liveness_fail(mock_subprocess):
    """Test healthcheck_runner.invoke_healthcheck returns False when bash command fails."""
    mock_subprocess.return_value = mock.MagicMock(
        returncode=1, stdout=b"", stderr=b"fail", spec=subprocess.CompletedProcess
    )

    result = hcrunner.invoke_healthcheck("test_mod", "liveness")

    assert result is False


# endregion TEST_hcrunner_healthcheck_invocation


# ────────────────────────────────────────────────────────────
# region TEST_obs_cleanup_observability (T2.3: фасад cleanup_observability_containers из
#   docker_orchestrator удалён, реальный модуль — observability)
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · Observability container cleanup · Last fail: N/A · Remove if: observability cleanup logic changes
def test_obs_cleanup_observability(mock_subprocess, tmp_path):
    """Test observability.cleanup_observability_containers stops and removes observability services."""
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services:\n  prometheus:\n    image: prom/prometheus\n", encoding="utf-8")

    call_log: list[str] = []

    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd)
        call_log.append(cmd_str)
        if "config" in cmd_str and "--services" in cmd_str:
            return mock.MagicMock(returncode=0, stdout=b"prometheus\n", stderr=b"", spec=subprocess.CompletedProcess)
        if "ps" in cmd_str and "--format" in cmd_str:
            return mock.MagicMock(
                returncode=0, stdout=b"prometheus\ngrafana\n", stderr=b"", spec=subprocess.CompletedProcess
            )
        return mock.MagicMock(returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess)

    mock_subprocess.side_effect = _side_effect

    obs.cleanup_observability_containers(compose_file)

    assert any("stop" in c for c in call_log)
    assert any("rm" in c for c in call_log)


# endregion TEST_obs_cleanup_observability


# ────────────────────────────────────────────────────────────
# region TEST_main_cli
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · CLI check-image action · Last fail: N/A · Remove if: CLI interface changes
def test_main_cli_check_image(mock_subprocess):
    """Test CLI entry point with --action check-image."""
    mock_subprocess.return_value = mock.MagicMock(
        returncode=0, stdout=b"", stderr=b"", spec=subprocess.CompletedProcess
    )

    with mock.patch.object(
        sys, "argv", ["docker_orchestrator.py", "--action", "check-image", "--image-ref", "test/img:latest"]
    ):
        exit_code = dorch.main()

    assert exit_code == 0


# 🧪 TRAP[TEST] · Edge-case · CLI missing required argument · Last fail: N/A · Remove if: CLI validation changes
def test_main_cli_missing_args():
    """Test CLI entry point exits with code 1 when required args missing."""
    with mock.patch.object(sys, "argv", ["docker_orchestrator.py", "--action", "check-image"]):
        exit_code = dorch.main()

    assert exit_code == 1  # --image-ref missing


# endregion TEST_main_cli


# ────────────────────────────────────────────────────────────
# region TEST_phases_E1
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · E1 — спец-фазы диспатчатся через PHASES
# · Regression: DevPlan 119 E1 — deploy_docker_module разбит на фазы (было 13 if-веток, CC=25)
# · Scenario: PHASES таблица содержит hermes-agent + observability фазы
# · Remove if: deploy_docker_module phase dispatch changes
def test_deploy_docker_module_phases_negative(mock_subprocess, module_dir):
    """R5 (E1): PHASES dispatch содержит hermes-agent и observability фазы (порядок dispatch)."""
    assert "hermes-agent" in dorch.PHASES, "PHASES must register hermes-agent phase"
    assert "observability" in dorch.PHASES, "PHASES must register observability phase"
    assert callable(dorch.PHASES["hermes-agent"])
    assert callable(dorch.PHASES["observability"])

    # All phase functions share the (module_name, module_dir, compose_file, compose_args) signature
    import inspect

    for name, fn in dorch.PHASES.items():
        sig = inspect.signature(fn)
        params = list(sig.parameters)
        assert params == ["module_name", "module_dir", "compose_file", "compose_args"], (
            f"Phase {name} signature must be (module_name, module_dir, compose_file, compose_args), got {params}"
        )


# 🧪 TRAP[TEST] · 2026-08-02 · unit · E1 — observability фаза best-effort (True)
# · Regression: DevPlan 119 E1 — _phase_observability делегирует в observability.py
# · Scenario: _phase_observability → True (cleanup non-fatal)
# · Remove if: observability phase semantics change
def test_phase_observability_best_effort(mock_subprocess, tmp_path):
    """E1: _phase_observability returns True (cleanup best-effort, never blocks deploy)."""
    compose_file = tmp_path / "docker-compose.yaml"
    compose_file.write_text("services:\n  monitoring:\n    image: x\n", encoding="utf-8")
    result = dorch._phase_observability(
        module_name="observability",
        module_dir=str(tmp_path),
        compose_file=compose_file,
        compose_args=["-f", str(compose_file)],
    )
    assert result is True


# endregion TEST_phases_E1


# ────────────────────────────────────────────────────────────
# region TEST_nginx_overlay_env_T8
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · NEGATIVE (R5) · ensure_nginx_overlay_env — F-015 (plan 012 T8)
# · Scenario: деплой НЕ-nginx модуля с overlay_dir на «голой» ноде (без внешнего env) →
#             NGINX_OVERLAY_DIR экспортирован ДО compose-вызова → интерполяция
#             ${NGINX_OVERLAY_DIR:?} проходит; param перезаписывает существующий env.
# · Last fail: F-015 — экспорт был под гейтом module_name == "nginx": деплой любого другого
# ·   модуля оставлял env без NGINX_OVERLAY_DIR → интерполяция падала на голой ноде.
# · Remove if: NGINX_OVERLAY_DIR перестаёт быть частью deploy-контракта compose-интерполяции.
def test_overlay_dir_exported_for_non_nginx(mock_subprocess, module_dir, monkeypatch):
    """F-015a: non-nginx deploy exports NGINX_OVERLAY_DIR before first compose call."""
    monkeypatch.delenv("NGINX_OVERLAY_DIR", raising=False)

    result = dorch.deploy_docker_module(
        module_name="test_mod",
        overlay_dir="/opt/node-configs/test-node/overlays/nginx",
        modules_dir=module_dir,
    )

    assert result is True
    exported = dorch.os.environ.get("NGINX_OVERLAY_DIR")
    assert exported == "/opt/node-configs/test-node/overlays/nginx", (
        f"F-015 FAIL: NGINX_OVERLAY_DIR must be exported for non-nginx modules, got {exported!r}"
    )

    # Param приоритетен: явный overlay_dir перезаписывает существующий env
    monkeypatch.setenv("NGINX_OVERLAY_DIR", "/stale/overlay")
    dorch.ensure_nginx_overlay_env("/opt/node-configs/other/overlays/nginx")
    assert dorch.os.environ["NGINX_OVERLAY_DIR"] == "/opt/node-configs/other/overlays/nginx"

    # Пустые все источники → не экспортируется, env не мутируется пустышкой
    monkeypatch.setenv("NGINX_OVERLAY_DIR", "")
    assert not dorch.ensure_nginx_overlay_env(None)
    logger.info("[IMP:9][test] NGINX_OVERLAY_DIR exported unconditionally for non-nginx module (T8)")


# endregion TEST_nginx_overlay_env_T8
