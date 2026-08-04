"""
# GREP_SUMMARY: test docker-ops shared ps inspect exec stop rm tag image network volume stats info manifest pull cli-shell LDD
# STRUCTURE: ▶ mock subprocess.run → ◇ test_docker_ps [default|all|quiet|filters|format] → ◇ test_ps_container_names → ◇ test_docker_inspect/inspect_state_health → ◇ test_docker_exec → ◇ test_docker_stop/rm/tag → ◇ test_docker_image_inspect(_exists/_many) → ◇ test_docker_manifest_inspect(_raw) → ◇ test_docker_pull → ◇ test_docker_network/volume → ◇ test_docker_info/stats → ◇ test_cli_shell → ⎋ LDD trajectory assert
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_ops.py (DevPlan 128 W1 $TEST_SPEC) —
##           единый слой docker-операций: mock subprocess.run, LDD IMP:9.
## @scope    Все функции docker_ops: docker_ps/ps_container_names/docker_inspect(_many)/
##           inspect_state_health/docker_exec/docker_stop/docker_rm/docker_tag/
##           docker_image_inspect(_exists/_many)/docker_manifest_inspect(_raw)/docker_pull/
##           docker_network_inspect(_create)/docker_volume_inspect/docker_info/docker_stats/CLI --shell.
## @invariants
##   - subprocess.run mocked for ALL tests — no real docker CLI calls
##   - Non-fatal контракт: сбой/таймаут → False/failed CompletedProcess/[] (никогда raise)
##   - LDD trajectory (IMP:7-10) verified on every test via _assert_ldd
##   - stdout bytes→str нормализация (TRAP[BUG] type-safety) покрыта bytes-моками
## @changes  2026-08-04 | DevPlan 128 W1 — Created (P2-5/D6 unit-тесты)
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
from unittest import mock

import pytest

from core.internal.shared import docker_ops

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# region FIXTURES / HELPERS
# ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_run():
    """Fixture: mock subprocess.run for all docker_ops calls (text=True → str stdout канон)."""
    with mock.patch.object(subprocess, "run") as m:
        m.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")
        yield m


def _assert_ldd(caplog, require_imp9: bool = True) -> None:
    """Print IMP:7-10 trajectory; assert IMP:9 only for success-path tests (LDD protocol)."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        msg = getattr(record, "message", "")
        if "[IMP:" in str(msg):
            imp_level = int(str(msg).split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(msg)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    if require_imp9:
        assert found, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FIXTURES / HELPERS


# ────────────────────────────────────────────────────────────
# region TEST_docker_ps
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker ps default command shape · Last fail: N/A · Remove if: docker ps interface changes
def test_docker_ps_default(mock_run, caplog) -> None:
    """docker_ps builds canonical ['docker','ps'] and returns CompletedProcess."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="abc123\n", stderr="")

    result = docker_ops.docker_ps()

    assert result.returncode == 0
    assert result.stdout == "abc123\n"
    assert mock_run.call_args[0][0] == ["docker", "ps"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker ps -a -q --filter --format flags · Last fail: N/A · Remove if: flag ordering changes
def test_docker_ps_flags(mock_run, caplog) -> None:
    """docker_ps(all, quiet, filters, format) builds flags in canonical order."""
    caplog.set_level(logging.INFO)
    docker_ops.docker_ps(
        all=True,
        quiet=True,
        filters=["name=test"],
        format="{{.Names}}",
    )

    cmd = mock_run.call_args[0][0]
    assert cmd == ["docker", "ps", "-a", "-q", "--filter", "name=test", "--format", "{{.Names}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker ps timeout → failed CompletedProcess (never raise) · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_ps_timeout_nonfatal(mock_run, caplog) -> None:
    """docker_ps returns failed CompletedProcess on TimeoutExpired — never raises."""
    caplog.set_level(logging.INFO)
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=10)

    result = docker_ops.docker_ps()

    assert result.returncode != 0
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Edge-case · docker_ps FileNotFoundError → failed process · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_ps_no_docker(mock_run, caplog) -> None:
    """docker_ps returns failed CompletedProcess when docker binary missing."""
    caplog.set_level(logging.INFO)
    mock_run.side_effect = FileNotFoundError("docker not found")

    result = docker_ops.docker_ps()

    assert result.returncode != 0
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · ps_container_names parses names, bytes→str · Last fail: N/A · Remove if: ps_container_names changes
def test_ps_container_names_bytes_stdout(mock_run, caplog) -> None:
    """ps_container_names normalizes bytes stdout (TRAP[BUG] type-safety) → list[str]."""
    caplog.set_level(logging.INFO)
    # bytes stdout — моки/исторический код (TRAP[BUG] 2026-07-22)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout=b"nginx\npostgres\n", stderr=b"")

    names = docker_ops.ps_container_names(all=True)

    assert names == ["nginx", "postgres"]
    # docker ps -a --format {{.Names}}
    assert mock_run.call_args[0][0] == ["docker", "ps", "-a", "--format", "{{.Names}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · ps_container_names failure → [] (graceful) · Last fail: N/A · Remove if: graceful degradation changes
def test_ps_container_names_failure_empty(mock_run, caplog) -> None:
    """ps_container_names returns [] on docker ps failure (never raises)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="error")

    assert docker_ops.ps_container_names() == []
    _assert_ldd(caplog, require_imp9=False)


# endregion TEST_docker_ps


# ────────────────────────────────────────────────────────────
# region TEST_docker_inspect / state_health
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker inspect --format before identifier · Last fail: N/A · Remove if: inspect interface changes
def test_docker_inspect_format_order(mock_run, caplog) -> None:
    """docker_inspect builds ['docker','inspect','--format',F,id]."""
    caplog.set_level(logging.INFO)
    docker_ops.docker_inspect("postgres", format="{{.State.Status}}")

    assert mock_run.call_args[0][0] == ["docker", "inspect", "--format", "{{.State.Status}}", "postgres"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · inspect_state_health parses state|health (D5 канон) · Last fail: N/A · Remove if: D5 criterion changes
def test_inspect_state_health_parse(mock_run, caplog) -> None:
    """inspect_state_health returns (state, health) from 'running|healthy'."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="running|healthy", stderr="")

    state, health = docker_ops.inspect_state_health("nginx")

    assert state == "running"
    assert health == "healthy"
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · inspect_state_health empty health ("" — running без healthcheck) · Last fail: N/A · Remove if: D5 criterion changes
def test_inspect_state_health_no_health(mock_run, caplog) -> None:
    """inspect_state_health: 'running|' (Health.Status == '') → ('running', '')."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="running|", stderr="")

    state, health = docker_ops.inspect_state_health("svc")

    assert state == "running"
    assert health == ""
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · inspect_state_health failure → ('', '') — never raises · Last fail: N/A · Remove if: non-fatal contract changes
def test_inspect_state_health_timeout(mock_run, caplog) -> None:
    """inspect_state_health returns ('', '') on docker failure (never raises)."""
    caplog.set_level(logging.INFO)
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=10)

    state, health = docker_ops.inspect_state_health("nginx")

    assert state == ""
    assert health == ""
    _assert_ldd(caplog, require_imp9=False)


# endregion TEST_docker_inspect / state_health


# ────────────────────────────────────────────────────────────
# region TEST_docker_exec / stop / rm / tag
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker exec command args after container · Last fail: N/A · Remove if: exec interface changes
def test_docker_exec_command(mock_run, caplog) -> None:
    """docker_exec builds ['docker','exec',container,*cmd]."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    docker_ops.docker_exec("nginx", ["nginx", "-t"])

    assert mock_run.call_args[0][0] == ["docker", "exec", "nginx", "nginx", "-t"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_stop success True · Last fail: N/A · Remove if: stop interface changes
def test_docker_stop_ok(mock_run, caplog) -> None:
    """docker_stop returns True on rc==0."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_stop("nginx") is True
    assert mock_run.call_args[0][0] == ["docker", "stop", "nginx"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_stop failure False (non-fatal) · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_stop_fail(mock_run, caplog) -> None:
    """docker_stop returns False on rc!=0 (never raises)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="error")

    assert docker_ops.docker_stop("nginx") is False
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_rm -f (orphan self-heal) · Last fail: N/A · Remove if: rm interface changes
def test_docker_rm_force(mock_run, caplog) -> None:
    """docker_rm(force=True) builds ['docker','rm','-f',container]."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_rm("orphan", force=True) is True
    assert mock_run.call_args[0][0] == ["docker", "rm", "-f", "orphan"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_tag image→tag · Last fail: N/A · Remove if: tag interface changes
def test_docker_tag(mock_run, caplog) -> None:
    """docker_tag builds ['docker','tag',image_id,tag] and returns True on rc==0."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_tag("abc123", "svc:previous-rollback") is True
    assert mock_run.call_args[0][0] == ["docker", "tag", "abc123", "svc:previous-rollback"]
    _assert_ldd(caplog)


# endregion TEST_docker_exec / stop / rm / tag


# ────────────────────────────────────────────────────────────
# region TEST_docker_image_inspect / manifest / pull
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker_image_inspect returns tag (RepoTags) · Last fail: N/A · Remove if: image inspect interface changes
def test_docker_image_inspect_tag(mock_run, caplog) -> None:
    """docker_image_inspect returns stripped stdout (image tag lookup)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="myapp:latest\n", stderr="")

    tag = docker_ops.docker_image_inspect("sha256:abc", "{{index .RepoTags 0}}")

    assert tag == "myapp:latest"
    assert mock_run.call_args[0][0] == [
        "docker",
        "image",
        "inspect",
        "sha256:abc",
        "--format",
        "{{index .RepoTags 0}}",
    ]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_image_inspect failure → None · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_image_inspect_fail_none(mock_run, caplog) -> None:
    """docker_image_inspect returns None on rc!=0 (first-deploy detection)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="error")

    assert docker_ops.docker_image_inspect("abc", "{{.Id}}") is None
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_image_inspect_exists local image · Last fail: N/A · Remove if: image inspect exists interface changes
def test_docker_image_inspect_exists(mock_run, caplog) -> None:
    """docker_image_inspect_exists True when local image present (hermes L1)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_image_inspect_exists("hermes-base:latest") is True
    assert mock_run.call_args[0][0] == ["docker", "image", "inspect", "hermes-base:latest"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_image_inspect_many batch (--format after ids) · Last fail: N/A · Remove if: batch inspect changes
def test_docker_image_inspect_many(mock_run, caplog) -> None:
    """docker_image_inspect_many builds ['docker','image','inspect',*ids,'--format',F]."""
    caplog.set_level(logging.INFO)
    docker_ops.docker_image_inspect_many(["a", "b"], "{{json .}}")

    assert mock_run.call_args[0][0] == ["docker", "image", "inspect", "a", "b", "--format", "{{json .}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_manifest_inspect registry check · Last fail: N/A · Remove if: manifest inspect interface changes
def test_docker_manifest_inspect_found(mock_run, caplog) -> None:
    """docker_manifest_inspect True when rc==0 (registry image exists)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="{}", stderr="")

    assert docker_ops.docker_manifest_inspect("ghcr.io/org/app:latest") is True
    assert mock_run.call_args[0][0] == ["docker", "manifest", "inspect", "ghcr.io/org/app:latest"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_manifest_inspect not-found → False · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_manifest_inspect_not_found(mock_run, caplog) -> None:
    """docker_manifest_inspect False on rc!=0 (never raises)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="no such manifest")

    assert docker_ops.docker_manifest_inspect("ghcr.io/org/missing:latest") is False
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_manifest_inspect_raw --verbose flags (security_posture) · Last fail: N/A · Remove if: raw variant changes
def test_docker_manifest_inspect_raw_flags(mock_run, caplog) -> None:
    """docker_manifest_inspect_raw passes flags (--verbose) before ref."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="{}", stderr="")

    result = docker_ops.docker_manifest_inspect_raw("app:1.0", flags=["--verbose"])

    assert result.returncode == 0
    assert mock_run.call_args[0][0] == ["docker", "manifest", "inspect", "--verbose", "app:1.0"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_pull success · Last fail: N/A · Remove if: pull interface changes
def test_docker_pull(mock_run, caplog) -> None:
    """docker_pull returns True on rc==0."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_pull("ghcr.io/org/base:latest") is True
    assert mock_run.call_args[0][0] == ["docker", "pull", "ghcr.io/org/base:latest"]
    _assert_ldd(caplog)


# endregion TEST_docker_image_inspect / manifest / pull


# ────────────────────────────────────────────────────────────
# region TEST_docker_network / volume / info / stats
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker_network_inspect exists / create · Last fail: N/A · Remove if: network interface changes
def test_docker_network_inspect_and_create(mock_run, caplog) -> None:
    """docker_network_inspect (exists) + docker_network_create (--driver bridge)."""
    caplog.set_level(logging.INFO)
    mock_run.side_effect = [
        subprocess.CompletedProcess([], returncode=1, stdout="", stderr=""),  # network absent
        subprocess.CompletedProcess([], returncode=0, stdout="", stderr=""),  # create ok
    ]

    assert docker_ops.docker_network_inspect("proxy-net") is False
    assert docker_ops.docker_network_create("proxy-net", "bridge") is True
    assert mock_run.call_args_list[0][0][0] == ["docker", "network", "inspect", "proxy-net"]
    assert mock_run.call_args_list[1][0][0] == ["docker", "network", "create", "--driver", "bridge", "proxy-net"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_volume_inspect detect-only (converge R7) · Last fail: N/A · Remove if: volume inspect interface changes
def test_docker_volume_inspect(mock_run, caplog) -> None:
    """docker_volume_inspect True when volume exists."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    assert docker_ops.docker_volume_inspect("postgres-data") is True
    assert mock_run.call_args[0][0] == ["docker", "volume", "inspect", "postgres-data"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_info daemon check · Last fail: N/A · Remove if: info interface changes
def test_docker_info(mock_run, caplog) -> None:
    """docker_info returns CompletedProcess (daemon reachability)."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="Server Version: 27.0\n", stderr="")

    result = docker_ops.docker_info()

    assert result.returncode == 0
    assert mock_run.call_args[0][0] == ["docker", "info"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_stats --no-stream --format · Last fail: N/A · Remove if: stats interface changes
def test_docker_stats(mock_run, caplog) -> None:
    """docker_stats builds ['docker','stats','--no-stream','--format',F]."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout='{"Name":"nginx"}\n', stderr="")

    result = docker_ops.docker_stats("{{json .}}")

    assert result.returncode == 0
    assert mock_run.call_args[0][0] == ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
    _assert_ldd(caplog)


# endregion TEST_docker_network / volume / info / stats


# ────────────────────────────────────────────────────────────
# region TEST_CLI_shell
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · CLI --shell ps prints docker ps stdout · Last fail: N/A · Remove if: CLI interface changes
def test_cli_shell_ps(mock_run, caplog, capsys) -> None:
    """CLI --shell ps: docker ps stdout печатается, exit 0."""
    caplog.set_level(logging.INFO)
    mock_run.return_value = subprocess.CompletedProcess([], returncode=0, stdout="nginx\npostgres\n", stderr="")

    rc = docker_ops.main(["--shell", "ps"])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "nginx\npostgres\n"
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · CLI --shell без op → usage exit 2 · Last fail: N/A · Remove if: CLI validation changes
def test_cli_shell_no_op(capsys) -> None:
    """CLI без --shell/op → usage, exit 2."""
    rc = docker_ops.main(["--shell"])
    assert rc == 2


# 🧪 TRAP[TEST] · Edge-case · CLI --shell exec без команды → error exit 2 · Last fail: N/A · Remove if: CLI validation changes
def test_cli_shell_exec_missing_args(capsys) -> None:
    """CLI --shell exec без container+cmd → SystemExit 2 (argparse.error)."""
    with pytest.raises(SystemExit) as exc:
        docker_ops.main(["--shell", "exec", "nginx"])
    assert exc.value.code == 2


# endregion TEST_CLI_shell
