# GREP_SUMMARY: test core-deliverer deliver_core deliver_platform_env deliver_makefile deliver_node_configs deliver_secrets ensure_remote_dirs rsync excludes dry-run mkdir phases LDD DI fake-runner W4d
# STRUCTURE: ▶ ┌resolve_remote_base (env chain)┐ → ◇ ensure_remote_dirs (cmd/fail) → ◇ deliver_core (excludes/fail) → ◇ platform-env/Makefile (skip) → ◇ node-configs/secrets (excludes/skip) → ◇ deliver_all (success/fail-fast) → ◇ dry-run → ⎋ CLI exit codes — 14 tests
# region MODULE_CONTRACT
## @purpose  Unit tests for core_deliverer.py — Core delivery channel (DevPlan 108 F4): resolve
##           remote bases, ensure_remote_dirs (ssh mkdir -p), 5 rsync фаз (core/platform-env/
##           Makefile/node-configs/secrets), fail-fast, DRY_RUN, CLI exit codes.
## @scope    14 тестов строго по DevPlan 108 §TEST_SPEC. tmp_path fixtures, caplog LDD IMP:9
##           траектория (local _assert_imp9, как в test_overlay_deliverer.py).
##           W4d (160 T4.4): monkeypatch subprocess.run УБРАН — FakeCommandRunner (scripted +
##           запись вызовов) через runner= (DI-канон W4b); sys.argv патчи убраны — cli(argv=...).
##           W5 (T5.2): файл переписывается как KEEP-контракт — здесь меняется ТОЛЬКО DI-механика,
##           семантика (команды/timeouts/excludes/order/exit-коды) не изменена.
## @invariants — Все тесты используют tmp_path — zero hardcoded paths
##              — FakeCommandRunner вместо патчей subprocess (no real SSH/rsync calls)
##              — Autouse env cleanup: PLATFORM_REMOTE_BASE/PLATFORM_ROOT/NODE_CONFIGS_REMOTE_BASE
##              — Exclude-паттерны assert'ятся точно (таблица AC7 DevPlan 108)
## @rationale Unit tests for new Python module core_deliverer.py. Покрытие AC1/AC5/AC6/AC7:
##            точные rsync-команды, fail-fast, dry-run (0 subprocess-вызовов), IMP:9 на успехе.
## @usecases pytest tests/unit/test_core_deliverer.py -s -v
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest

from core.internal.bootstrap.core_deliverer import (
    RSYNC_EXCLUDES_CORE,
    RSYNC_EXCLUDES_NODE,
    RSYNC_EXCLUDES_SECRETS,
    SSH_OPTS,
    CoreDeliveryError,
    cli,
    deliver_all,
    deliver_ci,
    deliver_core,
    deliver_makefile,
    deliver_makefiles,
    deliver_node_configs,
    deliver_platform_env,
    deliver_scripts,
    deliver_secrets,
    ensure_remote_dirs,
    resolve_remote_base,
)
from tests.helpers.fakes import FakeCommandRunner
from tests.helpers.fakes import make_proc as _proc
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

EXPECTED_SSH_E = f"ssh {' '.join(SSH_OPTS)}"


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════

# region FIXTURES


def _ok_runner() -> FakeCommandRunner:
    """Fake-раннер успеха: все команды → rc=0 (эквивалент _ok_run mock)."""
    return FakeCommandRunner(default=_proc(0))


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


# endregion FIXTURES


# ═══════════════════════════════════════════════════════════════════
# resolve_remote_base
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_resolve_remote_base
# 🧪 TRAP[TEST] · Regression: default fallback + env chain priority order
# · Scenario: env unset → /opt/platform; PLATFORM_ROOT не влияет (RC 121); PLATFORM_REMOTE_BASE wins
# · Last fail: RC 121 — ложный VPS-self-detect из-за PLATFORM_ROOT в remote-цепочке
# · Remove if: resolve_remote_base chain changes
@pytest.mark.parametrize(
    ("env_updates", "expected"),
    [
        pytest.param({}, "/opt/platform", id="default"),
        pytest.param({"PLATFORM_ROOT": "/srv/platform"}, "/opt/platform", id="root-ignored-rc121"),
        pytest.param({"PLATFORM_REMOTE_BASE": "/data/remote"}, "/data/remote", id="remote-wins"),
    ],
)
def test_resolve_remote_base(monkeypatch, caplog, env_updates: dict, expected: str) -> None:
    """resolve_remote_base: default fallback + PLATFORM_REMOTE_BASE wins (RC 121: PLATFORM_ROOT исключён)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_resolve_remote_base][start] BEGIN")
    for key, value in env_updates.items():
        monkeypatch.setenv(key, value)
    assert resolve_remote_base() == expected, f"Expected remote base {expected}"
    logger.info("[IMP:9][test_resolve_remote_base][done] base=%s verified", expected)
    assert_ldd_imp9(caplog)


# endregion FUNC_test_resolve_remote_base


# ═══════════════════════════════════════════════════════════════════
# ensure_remote_dirs
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_ensure_remote_dirs_command
def test_ensure_remote_dirs_command(caplog) -> None:
    """ensure_remote_dirs: ssh args = 4 dirs ({base}/core {base}/scripts {ncb}/{node} {ncb}/secrets)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_ensure_remote_dirs_command][start] BEGIN")
    fake = _ok_runner()
    result = ensure_remote_dirs("1.2.3.4", "test-node", runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
    assert cmd == [
        "ssh",
        *SSH_OPTS,
        "root@1.2.3.4",
        "mkdir -p /opt/platform/core /opt/platform/scripts /opt/node-configs/test-node /opt/node-configs/secrets",
    ], f"Unexpected mkdir cmd: {cmd}"
    assert fake.last_kwargs["timeout"] == 15, "mkdir timeout must be 15 (FILE_OP_TIMEOUT canon, W1-A1 plan 170)"
    logger.info("[IMP:9][test_ensure_remote_dirs_command][done] mkdir cmd verified: %s", " ".join(cmd))
    # 🧪 TRAP[TEST] · Regression: mkdir -p target dirs drift
    # · Scenario: {base}/core, {base}/scripts (REQ_FIX 141 r2), {ncb}/{node}, {ncb}/secrets
    # · Last fail: timeout=30 (MKDIR_TIMEOUT локальный дубль) — W1-A1: MKDIR_TIMEOUT=30 → FILE_OP_TIMEOUT=15
    # ·   (канон файловых мутаций converge, DevPlan 119 B7); прежний 30 = дубль SoT без импорта
    # · Remove if: remote dir hierarchy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_ensure_remote_dirs_command


# region FUNC_test_ensure_remote_dirs_failure
# GUARD-PRESERVE (168): единственное покрытие ошибки ensure_remote_dirs (CoreDeliveryError + IMP:10 FATAL mkdir)
def test_ensure_remote_dirs_failure(caplog) -> None:
    """ensure_remote_dirs: mkdir rc=1 → CoreDeliveryError + IMP:10 FATAL log."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_ensure_remote_dirs_failure][start] BEGIN")
    fake = FakeCommandRunner(default=_proc(1, stderr="mkdir: cannot create directory"))
    with pytest.raises(CoreDeliveryError, match=r"ssh mkdir -p failed for 1\.2\.3\.4"):
        ensure_remote_dirs("1.2.3.4", "test-node", runner=fake)
    assert "FATAL: ssh mkdir -p failed for 1.2.3.4" in caplog.text, "IMP:10 FATAL mkdir log missing"
    logger.info("[IMP:9][test_ensure_remote_dirs_failure][done] CoreDeliveryError + IMP:10 verified")
    # 🧪 TRAP[TEST] · Regression: mkdir failure silently ignored
    # · Scenario: failed ssh mkdir should raise CoreDeliveryError, not continue
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_ensure_remote_dirs_failure


# ═══════════════════════════════════════════════════════════════════
# deliver_core
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_core_excludes_exact
def test_deliver_core_excludes_exact(tmp_path, caplog) -> None:
    """deliver_core: ровно 6 exclude-паттернов (AC7 + docker-compose.test.yml, 162 W10-2) + -avz --delete, точная команда."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_core_excludes_exact][start] BEGIN")
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)
    fake = _ok_runner()
    result = deliver_core("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
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
    assert len(excludes) == 6, f"Expected exactly 6 excludes, got {excludes}"
    assert excludes == RSYNC_EXCLUDES_CORE, f"Exclude drift: {excludes}"
    assert "--exclude=docker-compose.test.yml" in excludes, (
        "162 W10-2: docker-compose.test.yml не исключается из core-доставки"
    )
    assert fake.last_kwargs["timeout"] == 600, "rsync timeout must be 600 (deploy default)"
    logger.info("[IMP:9][test_deliver_core_excludes_exact][done] 6 excludes + flags verified")
    # 🧪 TRAP[TEST] · Regression: core/ rsync exclude patterns drift (AC7 + 162 W10-2)
    # · Scenario: .git/__pycache__/.pytest_cache/default-user.xml/.env/docker-compose.test.yml not all excluded
    # · Last fail: N/A (new test; docker-compose.test.yml added DevPlan 162 W10-2)
    # · Remove if: exclude list intentionally changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_core_excludes_exact


# region FUNC_test_deliver_core_failure
def test_deliver_core_failure(tmp_path, caplog) -> None:
    """deliver_core: rsync rc=1 → CoreDeliveryError («rsync core/ failed») + IMP:10."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_core_failure][start] BEGIN")
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)
    fake = FakeCommandRunner(default=_proc(1, stderr="rsync: connection unexpectedly closed"))
    with pytest.raises(CoreDeliveryError, match=r"rsync core/ failed for 1\.2\.3\.4"):
        deliver_core("1.2.3.4", core_dir, runner=fake)
    assert "FATAL: rsync core/ failed for 1.2.3.4" in caplog.text, "IMP:10 FATAL core rsync log missing"
    logger.info("[IMP:9][test_deliver_core_failure][done] CoreDeliveryError + IMP:10 verified")
    # 🧪 TRAP[TEST] · Regression: core/ rsync failure silently ignored
    # · Scenario: failed rsync core/ should raise CoreDeliveryError, not return False
    # · Last fail: N/A (new test)
    # · Remove if: error handling strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_core_failure


# ═══════════════════════════════════════════════════════════════════
# deliver_platform_env / deliver_makefile (skip path)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_platform_env_missing_skips
def test_deliver_platform_env_missing_skips(tmp_path, caplog) -> None:
    """deliver_platform_env: файл отсутствует → IMP:8 SKIP, runner НЕ вызывается."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_platform_env_missing_skips][start] BEGIN")
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)  # no platform-env.yaml at tmp_path level
    fake = _ok_runner()
    result = deliver_platform_env("1.2.3.4", core_dir, runner=fake)
    assert result is True, "Skip must not be an error"
    assert len(fake.calls) == 0, "runner must NOT run when platform-env.yaml missing"
    assert "Phase 1b/4: SKIP — platform-env.yaml not found" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_platform_env_missing_skips][done] SKIP verified, 0 runner calls")
    # 🧪 TRAP[TEST] · Regression: skip path starts rsync for missing file
    # · Scenario: absent platform-env.yaml should skip with IMP:8, never exec
    # · Last fail: N/A (new test)
    # · Remove if: Phase 1b delivery logic changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_platform_env_missing_skips


# region FUNC_test_deliver_makefile_missing_skips
def test_deliver_makefile_missing_skips(tmp_path, caplog) -> None:
    """deliver_makefile: Makefile отсутствует → IMP:8 SKIP, без rsync."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_makefile_missing_skips][start] BEGIN")
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)  # no Makefile at tmp_path level
    fake = _ok_runner()
    result = deliver_makefile("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 0, "runner must NOT run when Makefile missing"
    assert "Phase 1c/4: SKIP — Makefile not found" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_makefile_missing_skips][done] SKIP verified, 0 rsync calls")
    # 🧪 TRAP[TEST] · Regression: skip path starts rsync for missing Makefile
    # · Scenario: absent Makefile should skip with IMP:8, never exec
    # · Last fail: N/A (new test)
    # · Remove if: Phase 1c delivery logic changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_makefile_missing_skips


# region FUNC_test_deliver_scripts_missing_skips
# 🧪 TRAP[TEST] · NEGATIVE (R5) · deliver_scripts — REQ_FIX (141 r2, ci-ops)
# · Last fail: /opt/platform/scripts/make-log-shell.sh не доставлялся ни одним каналом →
# ·   Makefile:80 SHELL → make на ноде Error 127 (provision).
# · Remove if: scripts/ перестанет доставляться Core-каналом (запрещено — Makefile зависимость)
def test_deliver_scripts_missing_skips(tmp_path, caplog) -> None:
    """deliver_scripts: scripts/ отсутствует → IMP:8 SKIP, без rsync."""
    caplog.set_level(logging.DEBUG)
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)  # no scripts/ at tmp_path level
    fake = _ok_runner()
    result = deliver_scripts("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 0, "runner must NOT run when scripts/ missing"
    assert "Phase 1d/4: SKIP — scripts/ not found" in caplog.text, "IMP:8 SKIP log missing"
    logger.critical("[IMP:9][test][deliver_scripts] missing-skip verified (R5 REQ_FIX)")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_scripts_missing_skips


# region FUNC_test_deliver_scripts_rsync_destination
def test_deliver_scripts_rsync_destination(tmp_path, caplog) -> None:
    """deliver_scripts: scripts/ присутствует → rsync в {base}/scripts/ (без --delete)."""
    caplog.set_level(logging.DEBUG)
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "make-log-shell.sh").write_text("#!/bin/bash\necho log")
    fake = _ok_runner()
    result = deliver_scripts("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
    assert cmd[0] == "rsync" and "-avz" in cmd
    assert f"{scripts_dir}/" in cmd, f"src scripts/ missing: {cmd}"
    assert "root@1.2.3.4:/opt/platform/scripts/" in cmd, f"dest /opt/platform/scripts/ missing: {cmd}"
    assert "--delete" not in cmd, "scripts-синк без --delete (старые скрипты безвредны)"
    logger.critical("[IMP:9][test][deliver_scripts] rsync destination verified")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_scripts_rsync_destination


# ═══════════════════════════════════════════════════════════════════
# deliver_makefiles (TRAP[BUG] 2026-08-12 — parity с CI core-deploy.yml)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_makefiles_missing_skips
# 🧪 TRAP[TEST] · NEGATIVE (R5) · deliver_makefiles — TRAP[BUG] 2026-08-12
# · Last fail: /opt/platform/makefiles отсутствовал на tronyx-vps после core-deliver →
# ·   `make provision` падал "makefiles/loadtest.mk: No such file or directory"
def test_deliver_makefiles_missing_skips(tmp_path, caplog) -> None:
    """deliver_makefiles: makefiles/ отсутствует → IMP:8 SKIP, без rsync."""
    caplog.set_level(logging.DEBUG)
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)
    fake = _ok_runner()
    result = deliver_makefiles("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 0
    assert "SKIP" in caplog.text
    logger.critical("[IMP:9][test][deliver_makefiles] missing-skip verified (R5 TRAP[BUG] 2026-08-12)")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_makefiles_missing_skips


# region FUNC_test_deliver_makefiles_rsync_destination
def test_deliver_makefiles_rsync_destination(tmp_path, caplog) -> None:
    """deliver_makefiles: makefiles/ присутствует → rsync БЕЗ trailing slash в {base}/.

    # 🧪 TRAP[TEST] · Regression · Scenario: TRAP[BUG] 2026-07-23 P0 (trailing slash)
    # · Expect: src = сама директория makefiles (не содержимое), dest = {base}/
    # · Last fail: rsync ./makefiles/ копировал *.mk в /opt/platform/ → include fail
    # · Remove if: makefiles rsync semantics intentionally change
    """
    caplog.set_level(logging.DEBUG)
    core_dir = str(tmp_path / "core")
    pathlib.Path(core_dir).mkdir(parents=True)
    makefiles_dir = tmp_path / "makefiles"
    makefiles_dir.mkdir()
    (makefiles_dir / "loadtest.mk").write_text("# loadtest")
    fake = _ok_runner()
    result = deliver_makefiles("1.2.3.4", core_dir, runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
    assert cmd[0] == "rsync" and "-avz" in cmd
    # БЕЗ trailing slash — копируется директория, а не её содержимое
    assert str(makefiles_dir) in cmd, f"src makefiles dir missing: {cmd}"
    assert not str(makefiles_dir).endswith("/"), f"src must NOT have trailing slash: {cmd}"
    assert "root@1.2.3.4:/opt/platform/" in cmd, f"dest /opt/platform/ missing: {cmd}"
    assert "--delete" not in cmd, "makefiles-синк без --delete (CI-parity)"
    logger.critical("[IMP:9][test][deliver_makefiles] rsync destination verified")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_makefiles_rsync_destination


# ═══════════════════════════════════════════════════════════════════
# deliver_node_configs / deliver_secrets
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_node_configs_excludes
def test_deliver_node_configs_excludes(tmp_path, caplog) -> None:
    """deliver_node_configs: ровно 3 exclude-паттерна (AC7) + точное назначение."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_node_configs_excludes][start] BEGIN")
    ncd = str(tmp_path / "node-configs")
    pathlib.Path(Path(ncd) / "test-node").mkdir(parents=True)
    fake = _ok_runner()
    result = deliver_node_configs("1.2.3.4", "test-node", ncd, runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
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
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_node_configs_excludes


# region FUNC_test_deliver_secrets_excludes_and_skip
def test_deliver_secrets_excludes_and_skip(tmp_path, caplog) -> None:
    """deliver_secrets: dir есть → 1 exclude (.git); dir нет → IMP:8 SKIP без rsync."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_secrets_excludes_and_skip][start] BEGIN")

    # Scenario A: secrets dir present → 1 exclude
    ncd = str(tmp_path / "node-configs")
    pathlib.Path(Path(ncd) / "test-node" / "secrets").mkdir(parents=True)
    fake = _ok_runner()
    result = deliver_secrets("1.2.3.4", "test-node", ncd, runner=fake)
    assert result is True
    assert len(fake.calls) == 1
    cmd = fake.last_cmd
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

    # Scenario B: no secrets dir → IMP:8 SKIP, no runner call
    empty_ncd = str(tmp_path / "empty-configs")
    pathlib.Path(empty_ncd).mkdir(parents=True)
    fake2 = _ok_runner()
    result2 = deliver_secrets("1.2.3.4", "test-node", empty_ncd, runner=fake2)
    assert result2 is True
    assert len(fake2.calls) == 0, "runner must NOT run when secrets/ dir missing"
    assert "Phase 3/4: SKIP — no secrets/ directory" in caplog.text, "IMP:8 SKIP log missing"
    logger.info("[IMP:9][test_deliver_secrets_excludes_and_skip][done] 1 exclude + SKIP verified")
    # 🧪 TRAP[TEST] · Regression: per-node secrets path drift (TRAP[BUG] 2026-07-23 P0)
    # · Scenario: secrets source must be node-configs/<node>/secrets/, dst /opt/node-configs/secrets/
    # · Last fail: 2026-07-23 (P0 — Phase 3 always SKIP, encrypted secrets not delivered)
    # · Remove if: secrets delivery layout changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_secrets_excludes_and_skip


# ═══════════════════════════════════════════════════════════════════
# deliver_all (orchestration)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_all_success_ldd
def test_deliver_all_success_ldd(delivery_tree, caplog) -> None:
    """deliver_all: fake rc=0 → все 6 шагов в правильном порядке, ≥1 IMP:9 лог (caplog)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_all_success_ldd][start] BEGIN")
    fake = _ok_runner()
    result = deliver_all(
        "1.2.3.4",
        delivery_tree["node"],
        delivery_tree["node_configs_dir"],
        delivery_tree["core_dir"],
        runner=fake,
    )
    assert result is True
    # 6 шагов: mkdir + core + platform-env + Makefile + node-configs + secrets
    calls = fake.calls
    assert len(calls) == 6, f"Expected 6 runner calls, got {len(calls)}"
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
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_all_success_ldd


# region FUNC_test_deliver_all_fail_fast
# GUARD-PRESERVE (168): единственное покрытие fail-fast ветки deliver_all (mkdir rc≠0 → abort, 1 call)
def test_deliver_all_fail_fast(delivery_tree, caplog) -> None:
    """deliver_all: mkdir rc=1 → CoreDeliveryError, последующие фазы НЕ вызваны (fail-fast)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_all_fail_fast][start] BEGIN")
    fake = FakeCommandRunner(default=_proc(1, stderr="ssh: Connection timed out"))
    with pytest.raises(CoreDeliveryError, match=r"ssh mkdir -p failed"):
        deliver_all(
            "1.2.3.4",
            delivery_tree["node"],
            delivery_tree["node_configs_dir"],
            delivery_tree["core_dir"],
            runner=fake,
        )
    assert len(fake.calls) == 1, "Fail-fast: only ensure_remote_dirs may run before first error"
    logger.info("[IMP:9][test_deliver_all_fail_fast][done] Fail-fast verified: 1 call, CoreDeliveryError")
    # 🧪 TRAP[TEST] · Regression: fail-fast broken — later phases run after mkdir error
    # · Scenario: failed mkdir must abort deliver_all immediately
    # · Last fail: N/A (new test)
    # · Remove if: fail-fast strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_all_fail_fast


# ═══════════════════════════════════════════════════════════════════
# DRY_RUN
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_dry_run_no_execution
def test_dry_run_no_execution(delivery_tree, caplog) -> None:
    """dry_run=True → 0 вызовов runner (calls пуст), команды IMP:8, success."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_dry_run_no_execution][start] BEGIN")
    fake = _ok_runner()
    result = deliver_all(
        "1.2.3.4",
        delivery_tree["node"],
        delivery_tree["node_configs_dir"],
        delivery_tree["core_dir"],
        dry_run=True,
        runner=fake,
    )
    assert result is True, "Dry-run must succeed without executing"
    assert len(fake.calls) == 0, "Dry-run must issue ZERO runner calls"
    assert "DRY-RUN" in caplog.text, "DRY-RUN commands must be printed (IMP:8)"
    assert "rsync" in caplog.text, "DRY-RUN must print rsync command"
    logger.info("[IMP:9][test_dry_run_no_execution][done] 0 runner calls, DRY-RUN printed")
    # 🧪 TRAP[TEST] · Regression: dry-run starts executing real commands
    # · Scenario: dry_run=True must print (IMP:8) and never exec rsync/ssh
    # · Last fail: N/A (new test)
    # · Remove if: dry-run mode is removed
    assert_ldd_imp9(caplog)


# endregion FUNC_test_dry_run_no_execution


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_cli_exit_codes
def test_cli_exit_codes(delivery_tree, caplog) -> None:
    """CLI deliver: success → 0; CoreDeliveryError → 1 (argv параметром — без sys.argv патча)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cli_exit_codes][start] BEGIN")

    args = [
        "deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--node-configs-dir",
        delivery_tree["node_configs_dir"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]

    # Scenario A: success → exit 0
    assert cli(argv=args, runner=_ok_runner()) == 0, "Success path must return 0 (T3.6: sys.exit → return)"

    # Scenario B: mkdir failure → exit 1
    fail_runner = FakeCommandRunner(default=_proc(1, stderr="ssh: Permission denied"))
    assert cli(argv=args, runner=fail_runner) == 1, "Failure path must return 1 (T3.6: sys.exit → return)"
    logger.info("[IMP:9][test_cli_exit_codes][done] CLI exit codes 0/1 verified")
    # 🧪 TRAP[TEST] · Regression: CLI exit code semantics
    # · Scenario: deliver success → 0, any CoreDeliveryError → 1 (shell || return 1 parity)
    # · Last fail: N/A (new test)
    # · Remove if: CLI exit code strategy changes
    assert_ldd_imp9(caplog)


# endregion FUNC_test_cli_exit_codes


# ═══════════════════════════════════════════════════════════════════
# fallback-deliver (142 W5)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_fallback_deliver_success
def test_fallback_deliver_success(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """fallback-deliver: rsync-фазы + provision + node-update → True (2 ssh вызова)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_fallback_deliver_success][start] BEGIN")

    # QA C5 (DevPlan 14 T1.4): ключ через env-цепочку node_detect (CLI-флаг удалён)
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-KEY-123")
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    fake = _ok_runner()
    assert cli(argv=args, runner=fake) == 0, "fallback-deliver success must return 0"
    # rsync-фазы (5) + provision (1) + node-update (1) — все через runner
    ssh_calls = [c for c in fake.calls if c and c[0] == "ssh"]
    assert len(ssh_calls) == 2, f"Expected 2 ssh calls (provision + node-update), got {len(ssh_calls)}"
    assert "provision" in ssh_calls[0][-1], "First ssh call must run make provision"
    # REF-0007: node-update = `ssh ... 'bash -s'` — ключ в argv ОТСУТСТВУЕТ
    assert ssh_calls[1] == ["ssh", *SSH_OPTS, "root@1.2.3.4", "bash -s"], (
        f"REF-0007: node-update must be bash -s without key in argv, got {ssh_calls[1]}"
    )
    # REF-0007: ключ доставляется stdin-скриптом (export + make node-update)
    update_input = fake.kwargs[-1].get("input") or ""
    assert "AGE-KEY-123" not in ssh_calls[1][-1], "REF-0007: AGE key must NOT be in argv"
    assert "export AGE_SECRET_KEY=AGE-KEY-123" in update_input, "stdin script must carry the AGE export"
    assert "DEPLOY_PARALLEL=true make node-update NODE=test-node" in update_input
    logger.info("[IMP:9][test_fallback_deliver_success][done] Success path: 5 rsync + 2 ssh verified")
    # 🧪 TRAP[TEST] · 2026-08-07 · 142 W5 — fallback-деплой: ssh-вызовы provision/node-update
    # · Scenario: успешный прогон — все фазы; REF-0007: AGE_SECRET_KEY через stdin prelude,
    # ·   НЕ в argv (`bash -s`)
    # · Last fail: REF-0007 red→green — env-префикс в argv заменён stdin-транспортом;
    # ·   2026-08-25 QA C5 — ключ теперь из env node_detect (argv-флаг удалён)
    # · Remove if: fallback-deliver subcommand removed
    assert_ldd_imp9(caplog)


# endregion FUNC_test_fallback_deliver_success


# region FUNC_test_fallback_deliver_provision_fail
def test_fallback_deliver_provision_fail(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """fallback-deliver: provision FAIL → cli() возвращает 1 (fail-fast)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_fallback_deliver_provision_fail][start] BEGIN")

    # QA C5: без ключа deliver_fallback падает ДО provision — для сценария provision-fail даём ключ
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-PROVISION-TEST-KEY")
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    # 3 rsync (core, platform-env, makefile; scripts/ + root-compose skip — нет файлов) + provision(fail)
    fake = FakeCommandRunner(results=[_proc(0), _proc(0), _proc(0), _proc(1, stderr="make: *** provision FAILED")])
    assert cli(argv=args, runner=fake) == 1, "provision failure must return 1"
    # node-update НЕ должен вызываться после провала provision
    ssh_calls = [c for c in fake.calls if c and c[0] == "ssh"]
    assert len(ssh_calls) == 1, f"Expected only provision ssh call, got {len(ssh_calls)}"
    logger.info("[IMP:9][test_fallback_deliver_provision_fail][done] Fail-fast on provision verified")
    # 🧪 TRAP[TEST] · 2026-08-07 · 142 W5 — fail-fast: provision failure останавливает pipeline
    # · Scenario: provision exit!=0 → return False → cli()=1, node-update не выполняется
    # · Last fail: N/A (new test)
    # · Remove if: fallback-deliver subcommand removed
    assert_ldd_imp9(caplog)


# endregion FUNC_test_fallback_deliver_provision_fail


# region FUNC_test_fallback_deliver_dry_run
def test_fallback_deliver_dry_run(delivery_tree, caplog) -> None:
    """fallback-deliver --dry-run: 0 вызовов runner (R5 142 W5), WOULD-команды печатаются."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_fallback_deliver_dry_run][start] BEGIN")

    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
        "--dry-run",
    ]
    fake = _ok_runner()
    assert cli(argv=args, runner=fake) == 0, "Dry-run must return 0"
    assert len(fake.calls) == 0, "Dry-run must issue ZERO runner calls"
    assert "dry-run" in caplog.text.lower(), "Dry-run WOULD commands must be printed"
    logger.info("[IMP:9][test_fallback_deliver_dry_run][done] 0 runner calls, WOULD printed")
    # 🧪 TRAP[TEST] · 2026-08-07 · 142 W5 — dry-run: R5 без мутаций
    # · Scenario: --dry-run → печать команд, 0 runner-вызовов, exit 0
    # · Last fail: N/A (new test)
    # · Remove if: dry-run mode is removed
    assert_ldd_imp9(caplog)


# endregion FUNC_test_fallback_deliver_dry_run


# ═══════════════════════════════════════════════════════════════════
# REF-0007: secret-transport + redaction (TEST-07 стиль)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_fallback_deliver_redacts_key_from_stderr_logs
# 🧪 TRAP[TEST] · NEGATIVE (R5) · TEST-07-стиль (REF-0007): ключ не попадает в stderr/логи
# · Scenario: node-update падает; remote stderr СОДЕРЖИТ значение ключа (echo $AGE_SECRET_KEY)
# ·   → error-лог deliver_fallback обязан содержать ***REDACTED*** и НИКОГДА само значение;
# ·   argv ssh-вызова тоже без ключа (`bash -s` stdin-транспорт)
# · Last fail: 2026-08-24 — AGE_SECRET_KEY светился в argv И в dry-run логах deliver_fallback;
# ·   2026-08-25 QA C5 — ключ теперь из env node_detect (argv-флаг удалён)
# · Remove if: redact_secrets() удалён/переименован или транспорт вернул key-in-argv
def test_fallback_deliver_redacts_key_from_stderr_logs(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """REF-0007 (TEST-07): fallback node-update failure — ключ redact'ится в логах."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_fallback_deliver_redacts][start] BEGIN")
    # QA C5 (T1.4): ключ через env (CLI-флаг --age-secret-key удалён)
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-SUPERSECRET-VALUE-42")
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    # core + platform-env + Makefile (scripts/makefiles/root-compose skip; mkdir не входит
    # в fallback-канал) + provision ok, затем node-update FAIL с ключом в remote stderr
    fake = FakeCommandRunner(
        results=[
            _proc(0),  # rsync core/
            _proc(0),  # rsync platform-env.yaml
            _proc(0),  # rsync Makefile
            _proc(0),  # provision
            _proc(1, stderr="fatal: cannot decrypt, key was AGE-SUPERSECRET-VALUE-42"),
        ]
    )
    assert cli(argv=args, runner=fake) == 1, "node-update failure must return 1"
    # argv ssh-вызова node-update БЕЗ ключа
    update_call = fake.calls[-1]
    assert update_call == ["ssh", *SSH_OPTS, "root@1.2.3.4", "bash -s"], f"unexpected cmd: {update_call}"
    assert "AGE-SUPERSECRET-VALUE-42" not in " ".join(update_call)
    # Ключ НИГДЕ в логах; redacted-маркер присутствует
    assert "AGE-SUPERSECRET-VALUE-42" not in caplog.text, f"TEST-07 FAIL: key leaked into logs:\n{caplog.text[-2000:]}"
    assert "***REDACTED***" in caplog.text, "redaction marker missing in failure log"
    logger.info("[IMP:9][test_fallback_deliver_redacts][done] Key redacted from stderr logs (TEST-07)")


# endregion FUNC_test_fallback_deliver_redacts_key_from_stderr_logs


# region FUNC_test_fallback_deliver_dry_run_no_key_in_output
# 🧪 TRAP[TEST] · NEGATIVE (R5) · REF-0007: dry-run НЕ печатает значение prelude
# · Scenario: --dry-run с --age-secret-key → WOULD-лог содержит размер скрипта, не значение
# · Last fail: 2026-08-24 — " ".join(update_cmd) печатал ключ в dry-run лог
# · Remove if: dry-run контракт меняется
def test_fallback_deliver_dry_run_no_key_in_output(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """REF-0007: fallback --dry-run с ключом — значение отсутствует в выводе."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_fallback_dryrun_nokey][start] BEGIN")
    # QA C5 (T1.4): ключ через env (CLI-флаг --age-secret-key удалён)
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-DRYRUN-SECRET-99")
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
        "--dry-run",
    ]
    fake = _ok_runner()
    assert cli(argv=args, runner=fake) == 0
    assert len(fake.calls) == 0, "dry-run must issue ZERO runner calls"
    assert "AGE-DRYRUN-SECRET-99" not in caplog.text, "TEST-07 FAIL: key leaked into dry-run output"
    assert "[redacted]" in caplog.text, "dry-run должен помечать stdin-скрипт как [redacted]"
    logger.info("[IMP:9][test_fallback_dryrun_nokey][done] Dry-run output is key-free")


# endregion FUNC_test_fallback_deliver_dry_run_no_key_in_output


# ═══════════════════════════════════════════════════════════════════
# QA C5/R6/R7 (DevPlan 14 T1.4): redact-before-truncate + argv-чистота
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_redact_before_truncate_boundary
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R6 — redact ДО truncate (boundary)
# · Scenario: stderr, где значение ключа попадает в последние 500 символов — старый порядок
#   (strip()[-500:] → redact) резал окно ПЕРЕД redact'ом: суффикс/префикс ключа на границе
#   окна уходил в лог неповреждённым
# · Last fail: core_deliverer.py:822 — redact_secrets(r.stderr.strip()[-500:], key)
# · Remove if: error-канал перестанет обрезать stderr
def test_redact_before_truncate_boundary(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ целиком внутри последних 500 символов stderr → в лог ни ключ, ни его суффикс."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_redact_boundary][start] BEGIN")
    secret = "AGE-BOUNDARY-SUFFIX-7777"
    monkeypatch.setenv("AGE_SECRET_KEY", secret)
    # Ключ начинается ~на границе окна [-500:] и продолжается за неё:
    # 600 filler + "key=" + secret + 40 filler → старый код брал последние 500 символов,
    # куда попадал хвост ключа; новый код redact'ит ДО окна.
    crafted_stderr = "x" * 600 + f"decrypt failed near key={secret} " + "y" * 40

    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    fake = FakeCommandRunner(
        results=[
            _proc(0),  # rsync core/
            _proc(0),  # rsync platform-env.yaml
            _proc(0),  # rsync Makefile
            _proc(0),  # provision
            _proc(1, stderr=crafted_stderr),  # node-update FAIL
        ]
    )
    assert cli(argv=args, runner=fake) == 1, "node-update failure must return 1"
    # Ни полный ключ, ни его суффикс не присутствуют в логах
    assert secret not in caplog.text, f"R6 FAIL: ключ утёк в лог:\n{caplog.text[-1500:]}"
    assert secret[-10:] not in caplog.text, f"R6 FAIL: суффикс ключа утёк на границе окна:\n{caplog.text[-1500:]}"
    assert "***REDACTED***" in caplog.text, "redaction marker missing"
    logger.info("[IMP:9][test_redact_boundary][done] redact-before-truncate verified on boundary window")


# endregion FUNC_test_redact_before_truncate_boundary


# region FUNC_test_no_key_in_argv_and_missing_key_fatal
# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA C5 — argv-чистота всех runner-вызовов
# · Scenario: fallback-deliver c env-ключом — /proc-argv НИ ОДНОГО runner-процесса (rsync/ssh)
#   не содержит значения ключа; CLI-флаг --age-secret-key больше не существует
# · Last fail: core-deliver.sh:107 передавал detected key флагом --age-secret-key (argv python)
# · Remove if: появится другой argv-транспорт секретов (запрещён инвариантом REF-0007/C5)
def test_no_key_in_argv(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни один runner-cmd не содержит значение ключа; флаг --age-secret-key отвергнут."""
    caplog.set_level(logging.DEBUG)
    secret = "AGE-ARGV-PURITY-CHECK-42"
    monkeypatch.setenv("AGE_SECRET_KEY", secret)
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    fake = _ok_runner()
    assert cli(argv=args, runner=fake) == 0
    for i, call in enumerate(fake.calls):
        assert secret not in call, f"C5 FAIL: секрет в argv вызова #{i}: {call}"
    # stdin node-update несёт export (транспорт — НЕ argv)
    update_input = fake.kwargs[-1].get("input") or ""
    assert f"export AGE_SECRET_KEY={secret}" in update_input
    # CLI-флаг удалён: argparse отвергает неизвестный аргумент (SystemExit 2)
    with pytest.raises(SystemExit) as exc_info:
        cli(argv=["fallback-deliver", "--host", "h", "--node", "n", "--core-dir", "c", "--age-secret-key", "x"])
    assert exc_info.value.code == 2, "удалённый флаг обязан давать argparse usage-error (exit 2)"
    logger.info("[IMP:9][test_no_key_in_argv][done] 0 argv hits across %d calls; flag removed", len(fake.calls))
    assert_ldd_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA C5 — отсутствие ключа → явный FATAL
# · Scenario: env/file цепочка пуста → deliver_fallback возвращает False ДО любых remote-
#   действий (не тихий skip φ9)
# · Last fail: shell-версия WARN'ила и продолжала (φ9 молча пропускался)
# · Remove if: появятся легитимные сценарии core-deliver без AGE-ключа
def test_missing_age_key_fails_fast(delivery_tree, caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Без ключа в env — rc=1, ноль runner-вызовов, IMP:10 FATAL."""
    caplog.set_level(logging.DEBUG)
    # Детерминизм: dev-машина имеет ~/.config/age/keys.txt (default-file цепочки) —
    # подменяем саму детекцию на «не найдено» (тестируется ветка FATAL deliver_fallback)
    import core.internal.bootstrap.core_deliverer as _cd

    monkeypatch.setattr(_cd, "detect_age_key", lambda *_args, **_kwargs: None)
    args = [
        "fallback-deliver",
        "--host",
        "1.2.3.4",
        "--node",
        delivery_tree["node"],
        "--core-dir",
        delivery_tree["core_dir"],
    ]
    fake = _ok_runner()
    assert cli(argv=args, runner=fake) == 1, "missing key must fail the delivery"
    assert len(fake.calls) == 0, f"fail-fast до remote-действий, было вызовов: {len(fake.calls)}"
    assert any("[IMP:10][deliver_fallback][age] FATAL" in r.message for r in caplog.records), (
        "ожидается явный FATAL-лог об отсутствии ключа"
    )
    logger.info("[IMP:9][test_missing_age_key_fails_fast][done] fail-fast before any remote action")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_no_key_in_argv_and_missing_key_fatal


# ═══════════════════════════════════════════════════════════════════
# HELPER
# ═══════════════════════════════════════════════════════════════════


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9


# ═══════════════════════════════════════════════════════════════════
# deliver_ci (REF-0112): CI core-deploy file-delivery — один owner exclude-set'ов
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_deliver_ci_full_sequence
## @purpose  REF-0112: полный сценарий deliver_ci на дереве раннера (core/ + platform-env.yaml +
##           Makefile + makefiles/ + scripts/ + node-configs/) — mkdir → core --delete (owner
##           excludes) → platform-env/Makefile/makefiles → scripts → node-configs БЕЗ --delete.
## @io       tmp_path, caplog, FakeCommandRunner → None (эффект-ассерты по argv-последовательности)
def test_deliver_ci_full_sequence(tmp_path, caplog) -> None:
    """deliver_ci: 7 runner-вызовов в порядке фаз; core-rsync несёт RSYNC_EXCLUDES_CORE; node-configs без --delete."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_ci_full_sequence][start] BEGIN")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "internal").mkdir()
    (tmp_path / "platform-env.yaml").write_text("DOMAIN: test")
    (tmp_path / "Makefile").write_text(".PHONY: test")
    (tmp_path / "makefiles").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "node-configs").mkdir()

    fake = _ok_runner()
    assert deliver_ci("1.2.3.4", str(core_dir), runner=fake) is True

    # Фазы: ssh mkdir → rsync core → rsync platform-env → rsync Makefile → rsync makefiles →
    # rsync scripts → rsync node-configs
    assert len(fake.calls) == 7, f"Expected 7 delivery commands, got {len(fake.calls)}: {fake.calls}"
    assert fake.calls[0] == [
        "ssh",
        *SSH_OPTS,
        "root@1.2.3.4",
        "mkdir -p /opt/platform/core /opt/platform/scripts",
    ], f"Unexpected mkdir phase: {fake.calls[0]}"

    core_cmd = fake.calls[1]
    assert core_cmd[0] == "rsync" and "--delete" in core_cmd, "core phase must use rsync --delete"
    for exclude in RSYNC_EXCLUDES_CORE:
        assert exclude in core_cmd, f"core phase missing owner exclude {exclude} (REF-0112 single-owner)"
    assert core_cmd[-1] == "root@1.2.3.4:/opt/platform/core/"

    nc_cmd = fake.calls[-1]
    assert nc_cmd[0] == "rsync", "last phase must be node-configs rsync"
    assert "--delete" not in nc_cmd, "node-configs sync must NOT use --delete (org repo is not wiped)"
    for exclude in RSYNC_EXCLUDES_NODE:
        assert exclude in nc_cmd, f"node-configs phase missing owner exclude {exclude}"
    logger.info(
        "[IMP:9][test_deliver_ci_full_sequence][done] %d phases verified (single-owner excludes)", len(fake.calls)
    )
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_ci_full_sequence


# region FUNC_test_deliver_ci_empty_core_guard_skips_delete
## @purpose  Guard-parity с прежним workflow-шагом (TRAP[BUG] DevPlan 125 T4): пустой/отсутствующий
##           core/ на раннере → rsync --delete ПРОПУЩЕН (против уноса прод-дерева), остальные фазы живут.
def test_deliver_ci_empty_core_guard_skips_delete(tmp_path, caplog) -> None:
    """Пустой core/ → нет --delete-rsync; skip-лог присутствует; config-фазы исполняются."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_ci_empty_core_guard][start] BEGIN")
    core_dir = tmp_path / "core"
    core_dir.mkdir()  # пустая директория
    (tmp_path / "platform-env.yaml").write_text("DOMAIN: test")
    (tmp_path / "Makefile").write_text(".PHONY: test")
    (tmp_path / "makefiles").mkdir()

    fake = _ok_runner()
    assert deliver_ci("1.2.3.4", str(core_dir), runner=fake) is True

    rsync_cmds = [c for c in fake.calls if c[0] == "rsync"]
    assert all("--delete" not in c for c in rsync_cmds), "No --delete rsync may run on empty core source"
    assert any("ПРОПУЩЕН" in c for c in [str(rsync_cmds)]) or "отсутствует/пуст" in caplog.text, (
        "Skip-log for empty core/ expected"
    )
    assert len(fake.calls) >= 4, f"Config phases must still run, got {len(fake.calls)} calls"
    logger.info("[IMP:9][test_deliver_ci_empty_core_guard][done] delete-rsync skipped on empty source")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_ci_empty_core_guard_skips_delete


# region FUNC_test_deliver_ci_node_configs_conditional_and_mkdir_failure
## @purpose  (а) node-configs отсутствует (gitignored орг-репо) → rsync не вызывается, skip-лог;
##           (б) mkdir-фаза упала → CoreDeliveryError (fail-fast parity CI-шага).


def test_deliver_ci_node_configs_conditional_and_mkdir_failure(tmp_path, caplog) -> None:
    """Без node-configs/: 6 вызовов (нет последней фазы); mkdir rc=1 → CoreDeliveryError."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_deliver_ci_conditional_failfast][start] BEGIN")
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "f.txt").write_text("x")  # непустой core/

    fake = _ok_runner()
    assert deliver_ci("1.2.3.4", str(core_dir), runner=fake) is True
    assert "gitignored" in caplog.text, "node-configs skip-log expected"
    assert all(c[0] != "rsync" or "/opt/node-configs/" not in c[-1] for c in fake.calls if c[0] == "rsync"), (
        "No node-configs rsync without local node-configs/"
    )

    failing = FakeCommandRunner(default=_proc(1, stderr="ssh: Connection refused"))
    with pytest.raises(CoreDeliveryError, match="mkdir -p failed"):
        deliver_ci("1.2.3.4", str(core_dir), runner=failing)
    logger.info("[IMP:9][test_deliver_ci_conditional_failfast][done] conditional skip + fail-fast verified")
    assert_ldd_imp9(caplog)


# endregion FUNC_test_deliver_ci_node_configs_conditional_and_mkdir_failure
