# GREP_SUMMARY: test core-deliverer deliver_core deliver_platform_env deliver_makefile deliver_node_configs deliver_secrets ensure_remote_dirs rsync excludes dry-run mkdir phases LDD
# STRUCTURE: ▶ ┌resolve_remote_base (env chain)┐ → ◇ ensure_remote_dirs (cmd/fail) → ◇ deliver_core (excludes/fail) → ◇ platform-env/Makefile (skip) → ◇ node-configs/secrets (excludes/skip) → ◇ deliver_all (success/fail-fast) → ◇ dry-run → ⎋ CLI exit codes — 14 tests
# region MODULE_CONTRACT
## @purpose  Unit tests for core_deliverer.py — Core delivery channel (DevPlan 108 F4): resolve
##           remote bases, ensure_remote_dirs (ssh mkdir -p), 5 rsync фаз (core/platform-env/
##           Makefile/node-configs/secrets), fail-fast, DRY_RUN, CLI exit codes.
## @scope    14 тестов строго по DevPlan 108 §TEST_SPEC. tmp_path fixtures, mock subprocess.run,
##           caplog LDD IMP:9 траектория (local _assert_imp9, как в test_overlay_deliverer.py).
## @invariants — Все тесты используют tmp_path — zero hardcoded paths
##              — mock.patch for subprocess.run (no real SSH/rsync calls)
##              — Autouse env cleanup: PLATFORM_REMOTE_BASE/PLATFORM_ROOT/NODE_CONFIGS_REMOTE_BASE
##              — Exclude-паттерны assert'ятся точно (таблица AC7 DevPlan 108)
## @rationale Unit tests for new Python module core_deliverer.py. Покрытие AC1/AC5/AC6/AC7:
##            точные rsync-команды, fail-fast, dry-run (0 subprocess-вызовов), IMP:9 на успехе.
## @usecases pytest tests/unit/test_core_deliverer.py -s -v
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
from unittest import mock

import pytest

from core.internal.bootstrap.core_deliverer import (
    RSYNC_EXCLUDES_CORE,
    RSYNC_EXCLUDES_NODE,
    RSYNC_EXCLUDES_SECRETS,
    SSH_OPTS,
    CoreDeliveryError,
    cli,
    deliver_all,
    deliver_core,
    deliver_makefile,
    deliver_node_configs,
    deliver_platform_env,
    deliver_secrets,
    ensure_remote_dirs,
    resolve_remote_base,
)

logger = logging.getLogger(__name__)

EXPECTED_SSH_E = f"ssh {' '.join(SSH_OPTS)}"


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════

# region FIXTURES


@pytest.fixture(autouse=True)
def _clean_delivery_env(monkeypatch):
    """Deterministic default remote bases: /opt/platform + /opt/node-configs."""
    monkeypatch.delenv("PLATFORM_REMOTE_BASE", raising=False)
    monkeypatch.delenv("PLATFORM_ROOT", raising=False)
    monkeypatch.delenv("NODE_CONFIGS_REMOTE_BASE", raising=False)


@pytest.fixture
def delivery_tree(tmp_path):
    """Full local delivery tree: core/ + platform-env.yaml + Makefile + node-configs/<node>/secrets/.

    ## @purpose  Mirror bootstrap.sh layout: core_dir with sibling platform-env.yaml/Makefile,
    ##            node-configs/<node>/ with per-node secrets/ dir.
    """
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "entrypoint.sh").write_text("# core file")
    (tmp_path / "platform-env.yaml").write_text("DOMAIN: test")
    (tmp_path / "Makefile").write_text(".PHONY: test")
    ncd = tmp_path / "node-configs"
    secrets_dir = ncd / "test-node" / "secrets"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "enc.env").write_text("AGE-ENC")
    return {
        "core_dir": str(core_dir),
        "node_configs_dir": str(ncd),
        "node": "test-node",
    }


def _ok_run(*_args, **_kwargs):
    """subprocess.run mock return — success."""
    return mock.MagicMock(returncode=0, stdout="", stderr="")


# endregion FIXTURES


# ═══════════════════════════════════════════════════════════════════
# resolve_remote_base
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_resolve_remote_base_default
def test_resolve_remote_base_default(caplog) -> None:
    """resolve_remote_base: no env vars → /opt/platform."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_resolve_remote_base_default][start] BEGIN")
    assert resolve_remote_base() == "/opt/platform", "Default remote base must be /opt/platform"
    logger.info("[IMP:9][test_resolve_remote_base_default][done] Default base verified")
    # 🧪 TRAP[TEST] · Regression: default remote base fallback
    # · Scenario: PLATFORM_REMOTE_BASE/PLATFORM_ROOT unset → /opt/platform
    # · Last fail: N/A (new test)
    # · Remove if: resolve_remote_base default changes
    _assert_imp9(caplog)


# endregion FUNC_test_resolve_remote_base_default


# region FUNC_test_resolve_remote_base_chain
def test_resolve_remote_base_chain(monkeypatch, caplog) -> None:
    """resolve_remote_base: PLATFORM_REMOTE_BASE → /opt/platform (RC 121: PLATFORM_ROOT исключён)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_resolve_remote_base_chain][start] BEGIN")
    # RC 121: локальный PLATFORM_ROOT НЕ влияет на remote-базу
    monkeypatch.setenv("PLATFORM_ROOT", "/srv/platform")
    assert resolve_remote_base() == "/opt/platform"
    # PLATFORM_REMOTE_BASE → wins over default
    monkeypatch.setenv("PLATFORM_REMOTE_BASE", "/data/remote")
    assert resolve_remote_base() == "/data/remote"
    logger.info("[IMP:9][test_resolve_remote_base_chain][done] Env chain verified (REMOTE > default)")
    # 🧪 TRAP[TEST] · Regression: env chain priority order
    # · Scenario: PLATFORM_REMOTE_BASE overrides default; PLATFORM_ROOT не влияет (RC 121)
    # · Last fail: RC 121 — ложный VPS-self-detect из-за PLATFORM_ROOT в remote-цепочке
    # · Remove if: resolve_remote_base chain changes
    _assert_imp9(caplog)


# endregion FUNC_test_resolve_remote_base_chain


# ═══════════════════════════════════════════════════════════════════
# ensure_remote_dirs
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ensure_remote_dirs_command
def test_ensure_remote_dirs_command(caplog) -> None:
    """ensure_remote_dirs: ssh args = 3 dirs ({base}/core {ncb}/{node} {ncb}/secrets)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_ensure_remote_dirs_command][start] BEGIN")
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = ensure_remote_dirs("1.2.3.4", "test-node")
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd == [
        "ssh",
        *SSH_OPTS,
        "root@1.2.3.4",
        "mkdir -p /opt/platform/core /opt/node-configs/test-node /opt/node-configs/secrets",
    ], f"Unexpected mkdir cmd: {cmd}"
    assert mock_run.call_args.kwargs["timeout"] == 30, "mkdir timeout must be 30 (parity ssh_exec)"
    logger.info("[IMP:9][test_ensure_remote_dirs_command][done] mkdir cmd verified: %s", " ".join(cmd))
    # 🧪 TRAP[TEST] · Regression: mkdir -p target dirs drift
    # · Scenario: {base}/core, {ncb}/{node}, {ncb}/secrets missing from ssh mkdir
    # · Last fail: N/A (new test)
    # · Remove if: remote dir hierarchy changes
    _assert_imp9(caplog)


# endregion FUNC_test_ensure_remote_dirs_command


# region FUNC_test_ensure_remote_dirs_failure
def test_ensure_remote_dirs_failure(caplog) -> None:
    """ensure_remote_dirs: mkdir rc=1 → CoreDeliveryError + IMP:10 FATAL log."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_ensure_remote_dirs_failure][start] BEGIN")
    fail = mock.MagicMock(returncode=1, stderr="mkdir: cannot create directory")
    with (
        mock.patch.object(subprocess, "run", return_value=fail),
        pytest.raises(CoreDeliveryError, match=r"ssh mkdir -p failed for 1\.2\.3\.4"),
    ):
        ensure_remote_dirs("1.2.3.4", "test-node")
    assert "FATAL: ssh mkdir -p failed for 1.2.3.4" in caplog.text, "IMP:10 FATAL mkdir log missing"
    logger.info("[IMP:9][test_ensure_remote_dirs_failure][done] CoreDeliveryError + IMP:10 verified")
    # 🧪 TRAP[TEST] · Regression: mkdir failure silently ignored
    # · Scenario: failed ssh mkdir should raise CoreDeliveryError, not continue
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    _assert_imp9(caplog)


# endregion FUNC_test_ensure_remote_dirs_failure


# ═══════════════════════════════════════════════════════════════════
# deliver_core
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_core_excludes_exact
def test_deliver_core_excludes_exact(tmp_path, caplog) -> None:
    """deliver_core: ровно 5 exclude-паттернов (AC7) + -avz --delete, точная команда."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_core_excludes_exact][start] BEGIN")
    core_dir = str(tmp_path / "core")
    os.makedirs(core_dir)
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_core("1.2.3.4", core_dir)
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    expected = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        EXPECTED_SSH_E,
        *RSYNC_EXCLUDES_CORE,
        f"{core_dir}/",
        "root@1.2.3.4:/opt/platform/core/",
    ]
    assert cmd == expected, f"Rsync core/ cmd mismatch:\n  got={cmd}\n  exp={expected}"
    excludes = [a for a in cmd if a.startswith("--exclude=")]
    assert len(excludes) == 5, f"Expected exactly 5 excludes, got {excludes}"
    assert excludes == RSYNC_EXCLUDES_CORE, f"Exclude drift: {excludes}"
    assert mock_run.call_args.kwargs["timeout"] == 600, "rsync timeout must be 600 (deploy default)"
    logger.info("[IMP:9][test_deliver_core_excludes_exact][done] 5 excludes + flags verified")
    # 🧪 TRAP[TEST] · Regression: core/ rsync exclude patterns drift (AC7)
    # · Scenario: .git/__pycache__/.pytest_cache/default-user.xml/.env not all excluded
    # · Last fail: N/A (new test)
    # · Remove if: exclude list intentionally changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_core_excludes_exact


# region FUNC_test_deliver_core_failure
def test_deliver_core_failure(tmp_path, caplog) -> None:
    """deliver_core: rsync rc=1 → CoreDeliveryError («rsync core/ failed») + IMP:10."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_core_failure][start] BEGIN")
    core_dir = str(tmp_path / "core")
    os.makedirs(core_dir)
    fail = mock.MagicMock(returncode=1, stderr="rsync: connection unexpectedly closed")
    with (
        mock.patch.object(subprocess, "run", return_value=fail),
        pytest.raises(CoreDeliveryError, match=r"rsync core/ failed for 1\.2\.3\.4"),
    ):
        deliver_core("1.2.3.4", core_dir)
    assert "FATAL: rsync core/ failed for 1.2.3.4" in caplog.text, "IMP:10 FATAL core rsync log missing"
    logger.info("[IMP:9][test_deliver_core_failure][done] CoreDeliveryError + IMP:10 verified")
    # 🧪 TRAP[TEST] · Regression: core/ rsync failure silently ignored
    # · Scenario: failed rsync core/ should raise CoreDeliveryError, not return False
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_core_failure


# ═══════════════════════════════════════════════════════════════════
# deliver_platform_env / deliver_makefile (skip path)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_platform_env_missing_skips
def test_deliver_platform_env_missing_skips(tmp_path, caplog) -> None:
    """deliver_platform_env: файл отсутствует → IMP:8 SKIP, subprocess НЕ вызывается."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_platform_env_missing_skips][start] BEGIN")
    core_dir = str(tmp_path / "core")
    os.makedirs(core_dir)  # no platform-env.yaml at tmp_path level
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_platform_env("1.2.3.4", core_dir)
    assert result is True, "Skip must not be an error"
    mock_run.assert_not_called(), "subprocess must NOT run when platform-env.yaml missing"
    assert "Phase 1b/4: SKIP — platform-env.yaml not found" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_platform_env_missing_skips][done] SKIP verified, 0 rsync calls")
    # 🧪 TRAP[TEST] · Regression: skip path starts rsync for missing file
    # · Scenario: absent platform-env.yaml should skip with IMP:8, never exec
    # · Last fail: N/A (new test)
    # · Remove if: Phase 1b delivery logic changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_platform_env_missing_skips


# region FUNC_test_deliver_makefile_missing_skips
def test_deliver_makefile_missing_skips(tmp_path, caplog) -> None:
    """deliver_makefile: Makefile отсутствует → IMP:8 SKIP, без rsync."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_makefile_missing_skips][start] BEGIN")
    core_dir = str(tmp_path / "core")
    os.makedirs(core_dir)  # no Makefile at tmp_path level
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_makefile("1.2.3.4", core_dir)
    assert result is True
    mock_run.assert_not_called(), "subprocess must NOT run when Makefile missing"
    assert "Phase 1c/4: SKIP — Makefile not found" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_makefile_missing_skips][done] SKIP verified, 0 rsync calls")
    # 🧪 TRAP[TEST] · Regression: skip path starts rsync for missing Makefile
    # · Scenario: absent Makefile should skip with IMP:8, never exec
    # · Last fail: N/A (new test)
    # · Remove if: Phase 1c delivery logic changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_makefile_missing_skips


# ═══════════════════════════════════════════════════════════════════
# deliver_node_configs / deliver_secrets
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_node_configs_excludes
def test_deliver_node_configs_excludes(tmp_path, caplog) -> None:
    """deliver_node_configs: ровно 3 exclude-паттерна (AC7) + точное назначение."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_node_configs_excludes][start] BEGIN")
    ncd = str(tmp_path / "node-configs")
    os.makedirs(os.path.join(ncd, "test-node"))
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_node_configs("1.2.3.4", "test-node", ncd)
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    expected = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        EXPECTED_SSH_E,
        *RSYNC_EXCLUDES_NODE,
        f"{ncd}/test-node/",
        "root@1.2.3.4:/opt/node-configs/test-node/",
    ]
    assert cmd == expected, f"Rsync node-configs cmd mismatch:\n  got={cmd}\n  exp={expected}"
    excludes = [a for a in cmd if a.startswith("--exclude=")]
    assert len(excludes) == 3, f"Expected exactly 3 excludes, got {excludes}"
    logger.info("[IMP:9][test_deliver_node_configs_excludes][done] 3 excludes verified")
    # 🧪 TRAP[TEST] · Regression: node-configs rsync exclude patterns drift (AC7)
    # · Scenario: .git/__pycache__/.pytest_cache not all excluded in Phase 2
    # · Last fail: N/A (new test)
    # · Remove if: exclude list intentionally changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_node_configs_excludes


# region FUNC_test_deliver_secrets_excludes_and_skip
def test_deliver_secrets_excludes_and_skip(tmp_path, caplog) -> None:
    """deliver_secrets: dir есть → 1 exclude (.git); dir нет → IMP:8 SKIP без rsync."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_secrets_excludes_and_skip][start] BEGIN")

    # Scenario A: secrets dir present → 1 exclude
    ncd = str(tmp_path / "node-configs")
    os.makedirs(os.path.join(ncd, "test-node", "secrets"))
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_secrets("1.2.3.4", "test-node", ncd)
    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    expected = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        EXPECTED_SSH_E,
        *RSYNC_EXCLUDES_SECRETS,
        f"{ncd}/test-node/secrets/",
        "root@1.2.3.4:/opt/node-configs/secrets/",
    ]
    assert cmd == expected, f"Rsync secrets cmd mismatch:\n  got={cmd}\n  exp={expected}"
    excludes = [a for a in cmd if a.startswith("--exclude=")]
    assert excludes == ["--exclude=.git"], f"Expected exactly 1 exclude .git, got {excludes}"

    # Scenario B: no secrets dir → IMP:8 SKIP, no subprocess
    empty_ncd = str(tmp_path / "empty-configs")
    os.makedirs(empty_ncd)
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run2:
        result2 = deliver_secrets("1.2.3.4", "test-node", empty_ncd)
    assert result2 is True
    mock_run2.assert_not_called(), "subprocess must NOT run when secrets/ dir missing"
    assert "Phase 3/4: SKIP — no secrets/ directory" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_secrets_excludes_and_skip][done] 1 exclude + SKIP verified")
    # 🧪 TRAP[TEST] · Regression: per-node secrets path drift (TRAP[BUG] 2026-07-23 P0)
    # · Scenario: secrets source must be node-configs/<node>/secrets/, dst /opt/node-configs/secrets/
    # · Last fail: 2026-07-23 (P0 — Phase 3 always SKIP, encrypted secrets not delivered)
    # · Remove if: secrets delivery layout changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_secrets_excludes_and_skip


# ═══════════════════════════════════════════════════════════════════
# deliver_all (orchestration)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_all_success_ldd
def test_deliver_all_success_ldd(delivery_tree, caplog) -> None:
    """deliver_all: mock rc=0 → все 6 шагов в правильном порядке, ≥1 IMP:9 лог (caplog)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_all_success_ldd][start] BEGIN")
    calls: list[list[str]] = []

    def _recorder(*args, **_kwargs):
        calls.append(args[0])
        return _ok_run()

    with mock.patch.object(subprocess, "run", side_effect=_recorder):
        result = deliver_all(
            "1.2.3.4",
            delivery_tree["node"],
            delivery_tree["node_configs_dir"],
            delivery_tree["core_dir"],
        )
    assert result is True
    # 6 шагов: mkdir + core + platform-env + Makefile + node-configs + secrets
    assert len(calls) == 6, f"Expected 6 subprocess calls, got {len(calls)}"
    assert calls[0][0] == "ssh", f"Step 1 must be ssh mkdir: {calls[0]}"
    assert all(c[0] == "rsync" for c in calls[1:]), "Steps 2-6 must be rsync"
    assert calls[1][-1] == "root@1.2.3.4:/opt/platform/core/"
    assert calls[2][-1] == "root@1.2.3.4:/opt/platform/platform-env.yaml"
    assert calls[3][-1] == "root@1.2.3.4:/opt/platform/Makefile"
    assert calls[4][-1] == "root@1.2.3.4:/opt/node-configs/test-node/"
    assert calls[5][-1] == "root@1.2.3.4:/opt/node-configs/secrets/"
    logger.info("[IMP:9][test_deliver_all_success_ldd][done] 6 phases ordered + IMP:9 trajectory")
    # 🧪 TRAP[TEST] · Regression: phase ordering / count drift
    # · Scenario: mkdir→core→env→Makefile→node-configs→secrets order must hold
    # · Last fail: N/A (new test)
    # · Remove if: deliver_all orchestration changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_all_success_ldd


# region FUNC_test_deliver_all_fail_fast
def test_deliver_all_fail_fast(delivery_tree, caplog) -> None:
    """deliver_all: mkdir rc=1 → CoreDeliveryError, последующие фазы НЕ вызваны (fail-fast)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_all_fail_fast][start] BEGIN")
    fail = mock.MagicMock(returncode=1, stderr="ssh: Connection timed out")
    with (
        mock.patch.object(subprocess, "run", return_value=fail) as mock_run,
        pytest.raises(CoreDeliveryError, match=r"ssh mkdir -p failed"),
    ):
        deliver_all(
            "1.2.3.4",
            delivery_tree["node"],
            delivery_tree["node_configs_dir"],
            delivery_tree["core_dir"],
        )
    mock_run.assert_called_once(), "Fail-fast: only ensure_remote_dirs may run before first error"
    logger.info("[IMP:9][test_deliver_all_fail_fast][done] Fail-fast verified: 1 call, CoreDeliveryError")
    # 🧪 TRAP[TEST] · Regression: fail-fast broken — later phases run after mkdir error
    # · Scenario: failed mkdir must abort deliver_all immediately
    # · Last fail: N/A (new test)
    # · Remove if: fail-fast strategy changes
    _assert_imp9(caplog)


# endregion FUNC_test_deliver_all_fail_fast


# ═══════════════════════════════════════════════════════════════════
# DRY_RUN
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_dry_run_no_execution
def test_dry_run_no_execution(delivery_tree, caplog) -> None:
    """dry_run=True → 0 subprocess-вызовов (mock.assert_not_called), команды IMP:8, success."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_dry_run_no_execution][start] BEGIN")
    with mock.patch.object(subprocess, "run", return_value=_ok_run()) as mock_run:
        result = deliver_all(
            "1.2.3.4",
            delivery_tree["node"],
            delivery_tree["node_configs_dir"],
            delivery_tree["core_dir"],
            dry_run=True,
        )
    assert result is True, "Dry-run must succeed without executing"
    mock_run.assert_not_called(), "Dry-run must issue ZERO subprocess calls"
    assert "DRY-RUN" in caplog.text, "DRY-RUN commands must be printed (IMP:8)"
    assert "rsync" in caplog.text, "DRY-RUN must print rsync command"
    logger.info("[IMP:9][test_dry_run_no_execution][done] 0 subprocess calls, DRY-RUN printed")
    # 🧪 TRAP[TEST] · Regression: dry-run starts executing real commands
    # · Scenario: dry_run=True must print (IMP:8) and never exec rsync/ssh
    # · Last fail: N/A (new test)
    # · Remove if: dry-run mode is removed
    _assert_imp9(caplog)


# endregion FUNC_test_dry_run_no_execution


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cli_exit_codes
def test_cli_exit_codes(delivery_tree, monkeypatch, caplog) -> None:
    """CLI deliver: success → SystemExit(0); CoreDeliveryError → SystemExit(1)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cli_exit_codes][start] BEGIN")

    # Scenario A: success → exit 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "core_deliverer",
            "deliver",
            "--host",
            "1.2.3.4",
            "--node",
            delivery_tree["node"],
            "--node-configs-dir",
            delivery_tree["node_configs_dir"],
            "--core-dir",
            delivery_tree["core_dir"],
        ],
    )
    with mock.patch.object(subprocess, "run", return_value=_ok_run()):
        assert cli() == 0, "Success path must return 0 (T3.6: sys.exit → return)"

    # Scenario B: mkdir failure → exit 1
    fail = mock.MagicMock(returncode=1, stderr="ssh: Permission denied")
    with mock.patch.object(subprocess, "run", return_value=fail):
        assert cli() == 1, "Failure path must return 1 (T3.6: sys.exit → return)"
    logger.info("[IMP:9][test_cli_exit_codes][done] CLI exit codes 0/1 verified")
    # 🧪 TRAP[TEST] · Regression: CLI exit code semantics
    # · Scenario: deliver success → 0, any CoreDeliveryError → 1 (shell || return 1 parity)
    # · Last fail: N/A (new test)
    # · Remove if: CLI exit code strategy changes
    _assert_imp9(caplog)


# endregion FUNC_test_cli_exit_codes


# ═══════════════════════════════════════════════════════════════════
# HELPER
# ═══════════════════════════════════════════════════════════════════


def _assert_imp9(caplog) -> None:
    """LDD trajectory check: verify at least one IMP:9 log exists (Anti-Illusion, RULES.md §LDD)."""
    found = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_str = record.message.split("[IMP:")[1].split("]")[0]
            try:
                imp_level = int(imp_str)
            except ValueError:
                continue
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "Critical LDD Error: No IMP:9 business logic log found"
