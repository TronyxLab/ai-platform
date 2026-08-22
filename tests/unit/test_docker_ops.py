"""
# GREP_SUMMARY: test docker-ops shared ps inspect exec stop rm tag image network volume stats info manifest cli-shell LDD DI fake-runner W4d
# STRUCTURE: ▶ FakeCommandRunner (scripted, запись вызовов) → ◇ test_docker_ps [default|all|quiet|filters|format] → ◇ test_ps_container_names → ◇ test_docker_inspect/inspect_state_health → ◇ test_docker_exec → ◇ test_docker_stop/rm/tag → ◇ test_docker_image_inspect(_many) → ◇ test_docker_manifest_inspect(_raw) → ◇ test_docker_network/volume → ◇ test_docker_info/stats → ◇ test_cli_shell → ⎋ LDD trajectory assert
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/docker_ops.py (DevPlan 128 W1 $TEST_SPEC) —
##           единый слой docker-операций. W4d (160 T4.4): monkeypatch subprocess.run УБРАН —
##           все тесты передают FakeCommandRunner (scripted результаты + запись вызовов)
##           через runner= параметр (DI-канон W4b) — 0 патчей subprocess в файле.
## @scope    Все функции docker_ops: docker_ps/ps_container_names/docker_inspect(_many)/
##           inspect_state_health/docker_exec/docker_stop/docker_rm/docker_tag/
##           docker_image_inspect(_many)/docker_manifest_inspect(_raw)/
##           docker_network_inspect(_create)/docker_volume_inspect/docker_info/docker_stats/CLI --shell.
##           (docker_pull/docker_image_inspect_exists удалены как мёртвый API — аудит 2026-08-22.)
## @invariants
##   - FakeCommandRunner — scripted: дефолт/последовательность CompletedProcess, запись calls/kwargs
##   - Non-fatal контракт: сбой/таймаут → False/failed CompletedProcess/[] (никогда raise);
##     failed-запуск имитируется rc=124 (timeout) / rc=127 (no-docker) scripted-результатами
##   - LDD trajectory (IMP:7-10) verified on every test via _assert_ldd
##   - stdout bytes→str нормализация (TRAP[BUG] type-safety) покрыта bytes-результатами fake
## @changes  2026-08-04 | DevPlan 128 W1 — Created (P2-5/D6 unit-тесты)
## @changes  2026-08-13 | DevPlan 160 W4d — mock_run (патч subprocess.run) → FakeCommandRunner DI (T4.4)
# endregion MODULE_CONTRACT
"""

import logging

import pytest

from core.internal.shared import docker_ops

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# region FIXTURES / HELPERS
# ────────────────────────────────────────────────────────────
# T2.16c: FakeCommandRunner/_proc — общие тест-двойники из tests/helpers/fakes.py
# T2.16a: _assert_ldd — консолидирован в gate_helpers.assert_ldd_imp9
from tests.helpers.fakes import FakeCommandRunner
from tests.helpers.fakes import make_proc as _proc
from tests.helpers.gate_helpers import assert_ldd_imp9 as _assert_ldd

# endregion FIXTURES / HELPERS


# ────────────────────────────────────────────────────────────
# region TEST_docker_ps
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker ps default command shape · Last fail: N/A · Remove if: docker ps interface changes
def test_docker_ps_default(caplog) -> None:
    """docker_ps builds canonical ['docker','ps'] and returns CompletedProcess."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "abc123\n"))

    result = docker_ops.docker_ps(runner=fake)

    assert result.returncode == 0
    assert result.stdout == "abc123\n"
    assert fake.last_cmd == ["docker", "ps"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker ps -a -q --filter --format flags · Last fail: N/A · Remove if: flag ordering changes
def test_docker_ps_flags(caplog) -> None:
    """docker_ps(all, quiet, filters, format) builds flags in canonical order."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner()
    docker_ops.docker_ps(
        all=True,
        quiet=True,
        filters=["name=test"],
        format="{{.Names}}",
        runner=fake,
    )

    cmd = fake.last_cmd
    assert cmd == ["docker", "ps", "-a", "-q", "--filter", "name=test", "--format", "{{.Names}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_ps non-fatal failures (timeout/no-docker) → failed process · Last fail: N/A · Remove if: non-fatal contract changes
@pytest.mark.parametrize(
    ("rc", "stderr"),
    [
        (124, "timeout"),  # timeout
        (127, "docker: command not found"),  # docker binary missing
    ],
)
def test_docker_ps_nonfatal_failure(caplog, rc: int, stderr: str) -> None:
    """docker_ps returns failed CompletedProcess on timeout/missing-docker — never raises."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(rc=rc, stderr=stderr))

    result = docker_ops.docker_ps(runner=fake)

    assert result.returncode != 0
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · ps_container_names parses names, bytes→str · Last fail: N/A · Remove if: ps_container_names changes
def test_ps_container_names_bytes_stdout(caplog) -> None:
    """ps_container_names normalizes bytes stdout (TRAP[BUG] type-safety) → list[str]."""
    caplog.set_level(logging.INFO)
    # bytes stdout — моки/исторический код (TRAP[BUG] 2026-07-22)
    fake = FakeCommandRunner(default=_proc(0, stdout=b"nginx\npostgres\n", stderr=b""))

    names = docker_ops.ps_container_names(all=True, runner=fake)

    assert names == ["nginx", "postgres"]
    # docker ps -a --format {{.Names}}
    assert fake.last_cmd == ["docker", "ps", "-a", "--format", "{{.Names}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · ps_container_names failure → [] (graceful) · Last fail: N/A · Remove if: graceful degradation changes
def test_ps_container_names_failure_empty(caplog) -> None:
    """ps_container_names returns [] on docker ps failure (never raises)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(1, stderr="error"))

    assert docker_ops.ps_container_names(runner=fake) == []
    _assert_ldd(caplog, require_imp9=False)


# endregion TEST_docker_ps


# ────────────────────────────────────────────────────────────
# region TEST_docker_inspect / state_health
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker inspect --format before identifier · Last fail: N/A · Remove if: inspect interface changes
def test_docker_inspect_format_order(caplog) -> None:
    """docker_inspect builds ['docker','inspect','--format',F,id]."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner()
    docker_ops.docker_inspect("postgres", format="{{.State.Status}}", runner=fake)

    assert fake.last_cmd == ["docker", "inspect", "--format", "{{.State.Status}}", "postgres"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · inspect_state_health parse variants (D5 канон) · Last fail: N/A · Remove if: D5 criterion changes
@pytest.mark.parametrize(
    ("stdout", "expected_state", "expected_health"),
    [
        ("running|healthy", "running", "healthy"),
        ("running|", "running", ""),  # running без healthcheck (Health.Status == '')
    ],
)
def test_inspect_state_health_parse(caplog, stdout: str, expected_state: str, expected_health: str) -> None:
    """inspect_state_health parses 'state|health' variants (D5 канон)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, stdout))

    state, health = docker_ops.inspect_state_health("nginx", runner=fake)

    assert state == expected_state
    assert health == expected_health
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · inspect_state_health failure → ('', '') — never raises · Last fail: N/A · Remove if: non-fatal contract changes
def test_inspect_state_health_timeout(caplog) -> None:
    """inspect_state_health returns ('', '') on docker failure (never raises)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(rc=124, stderr="timeout"))

    state, health = docker_ops.inspect_state_health("nginx", runner=fake)

    assert not state
    assert not health
    _assert_ldd(caplog, require_imp9=False)


# endregion TEST_docker_inspect / state_health


# ────────────────────────────────────────────────────────────
# region TEST_docker_exec / stop / rm / tag
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker exec command args after container · Last fail: N/A · Remove if: exec interface changes
def test_docker_exec_command(caplog) -> None:
    """docker_exec builds ['docker','exec',container,*cmd]."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0))

    docker_ops.docker_exec("nginx", ["nginx", "-t"], runner=fake)

    assert fake.last_cmd == ["docker", "exec", "nginx", "nginx", "-t"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_stop success True · Last fail: N/A · Remove if: stop interface changes
def test_docker_stop_ok(caplog) -> None:
    """docker_stop returns True on rc==0."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0))

    assert docker_ops.docker_stop("nginx", runner=fake) is True
    assert fake.last_cmd == ["docker", "stop", "nginx"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_stop failure False (non-fatal) · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_stop_fail(caplog) -> None:
    """docker_stop returns False on rc!=0 (never raises)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(1, stderr="error"))

    assert docker_ops.docker_stop("nginx", runner=fake) is False
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_rm -f (orphan self-heal) · Last fail: N/A · Remove if: rm interface changes
def test_docker_rm_force(caplog) -> None:
    """docker_rm(force=True) builds ['docker','rm','-f',container]."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0))

    assert docker_ops.docker_rm("orphan", force=True, runner=fake) is True
    assert fake.last_cmd == ["docker", "rm", "-f", "orphan"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_tag image→tag · Last fail: N/A · Remove if: tag interface changes
def test_docker_tag(caplog) -> None:
    """docker_tag builds ['docker','tag',image_id,tag] and returns True on rc==0."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0))

    assert docker_ops.docker_tag("abc123", "svc:previous-rollback", runner=fake) is True
    assert fake.last_cmd == ["docker", "tag", "abc123", "svc:previous-rollback"]
    _assert_ldd(caplog)


# endregion TEST_docker_exec / stop / rm / tag


# ────────────────────────────────────────────────────────────
# region TEST_docker_image_inspect / manifest / pull
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker_image_inspect returns tag (RepoTags) · Last fail: N/A · Remove if: image inspect interface changes
def test_docker_image_inspect_tag(caplog) -> None:
    """docker_image_inspect returns stripped stdout (image tag lookup)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "myapp:latest\n"))

    tag = docker_ops.docker_image_inspect("sha256:abc", "{{index .RepoTags 0}}", runner=fake)

    assert tag == "myapp:latest"
    assert fake.last_cmd == [
        "docker",
        "image",
        "inspect",
        "sha256:abc",
        "--format",
        "{{index .RepoTags 0}}",
    ]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_image_inspect failure → None · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_image_inspect_fail_none(caplog) -> None:
    """docker_image_inspect returns None on rc!=0 (first-deploy detection)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(1, stderr="error"))

    assert docker_ops.docker_image_inspect("abc", "{{.Id}}", runner=fake) is None
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_image_inspect_many batch (--format after ids) · Last fail: N/A · Remove if: batch inspect changes
def test_docker_image_inspect_many(caplog) -> None:
    """docker_image_inspect_many builds ['docker','image','inspect',*ids,'--format',F]."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner()
    docker_ops.docker_image_inspect_many(["a", "b"], "{{json .}}", runner=fake)

    assert fake.last_cmd == ["docker", "image", "inspect", "a", "b", "--format", "{{json .}}"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_manifest_inspect registry check · Last fail: N/A · Remove if: manifest inspect interface changes
def test_docker_manifest_inspect_found(caplog) -> None:
    """docker_manifest_inspect True when rc==0 (registry image exists)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "{}"))

    assert docker_ops.docker_manifest_inspect("ghcr.io/org/app:latest", runner=fake) is True
    assert fake.last_cmd == ["docker", "manifest", "inspect", "ghcr.io/org/app:latest"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Edge-case · docker_manifest_inspect not-found → False · Last fail: N/A · Remove if: non-fatal contract changes
def test_docker_manifest_inspect_not_found(caplog) -> None:
    """docker_manifest_inspect False on rc!=0 (never raises)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(1, stderr="no such manifest"))

    assert docker_ops.docker_manifest_inspect("ghcr.io/org/missing:latest", runner=fake) is False
    _assert_ldd(caplog, require_imp9=False)


# 🧪 TRAP[TEST] · Regression · docker_manifest_inspect_raw --verbose flags (security_posture) · Last fail: N/A · Remove if: raw variant changes
def test_docker_manifest_inspect_raw_flags(caplog) -> None:
    """docker_manifest_inspect_raw passes flags (--verbose) before ref."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "{}"))

    result = docker_ops.docker_manifest_inspect_raw("app:1.0", flags=["--verbose"], runner=fake)

    assert result.returncode == 0
    assert fake.last_cmd == ["docker", "manifest", "inspect", "--verbose", "app:1.0"]
    _assert_ldd(caplog)


# endregion TEST_docker_image_inspect / manifest


# ────────────────────────────────────────────────────────────
# region TEST_docker_network / volume / info / stats
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · docker_network_inspect exists / create · Last fail: N/A · Remove if: network interface changes
def test_docker_network_inspect_and_create(caplog) -> None:
    """docker_network_inspect (exists) + docker_network_create (--driver bridge)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(results=[_proc(1, stderr=""), _proc(0)])

    assert docker_ops.docker_network_inspect("proxy-net", runner=fake) is False
    assert docker_ops.docker_network_create("proxy-net", "bridge", runner=fake) is True
    assert fake.calls[0] == ["docker", "network", "inspect", "proxy-net"]
    assert fake.calls[1] == ["docker", "network", "create", "--driver", "bridge", "proxy-net"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_volume_inspect detect-only (converge R7) · Last fail: N/A · Remove if: volume inspect interface changes
def test_docker_volume_inspect(caplog) -> None:
    """docker_volume_inspect True when volume exists."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0))

    assert docker_ops.docker_volume_inspect("postgres-data", runner=fake) is True
    assert fake.last_cmd == ["docker", "volume", "inspect", "postgres-data"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_info daemon check · Last fail: N/A · Remove if: info interface changes
def test_docker_info(caplog) -> None:
    """docker_info returns CompletedProcess (daemon reachability)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "Server Version: 27.0\n"))

    result = docker_ops.docker_info(runner=fake)

    assert result.returncode == 0
    assert fake.last_cmd == ["docker", "info"]
    _assert_ldd(caplog)


# 🧪 TRAP[TEST] · Regression · docker_stats --no-stream --format · Last fail: N/A · Remove if: stats interface changes
def test_docker_stats(caplog) -> None:
    """docker_stats builds ['docker','stats','--no-stream','--format',F]."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, '{"Name":"nginx"}\n'))

    result = docker_ops.docker_stats("{{json .}}", runner=fake)

    assert result.returncode == 0
    assert fake.last_cmd == ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
    _assert_ldd(caplog)


# endregion TEST_docker_network / volume / info / stats


# ────────────────────────────────────────────────────────────
# region TEST_CLI_shell
# ────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · CLI --shell ps prints docker ps stdout · Last fail: N/A · Remove if: CLI interface changes
def test_cli_shell_ps(caplog, capsys) -> None:
    """CLI --shell ps: docker ps stdout печатается, exit 0."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(default=_proc(0, "nginx\npostgres\n"))

    rc = docker_ops.main(["--shell", "ps"], runner=fake)

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
