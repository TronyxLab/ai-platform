"""
# GREP_SUMMARY: test-converge-audit, r2, reconcile-audit-log, symlink-guard, audit-log-creation, ci-deploy-group, setfacl, acl, fallback-0660, idempotent, read-contract
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R2 reconcile_audit_log 8× (symlink-fail/missing-file/acl-converged/ci-deploy-group/primary-acl/fallback-group/idempotent/read-contract) → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for converge/audit.py via reconciler.reconcile_audit_log (R2).
## @scope    Tests audit-log reconciliation: symlink attack prevention, creation,
##           converged-state check (ACL/0660 target, P1 fix 2026-08-27), ci-deploy group
##           membership, setfacl primary branch, chgrp+0660 fallback branch, idempotency,
##           read-contract regression.
##           Does NOT require a real docker daemon or root privileges.
## @invariants
##   - All docker-dependent tests mock subprocess.run to avoid real docker calls
##   - File operations use tmp_path exclusively
##   - Each test validates IMP:9 business logic log presence via caplog
## @rationale Direct function testing with tmp_path and mock subprocess.run.
##   Вынесен из монолита test_reconciler.py (DevPlan 118 F6).
## @changes 2026-08-02 · F6 split — R2 audit (DevPlan 118)
## @changes 2026-08-27 · P1 fix — новые тесты target-состояния ACL/0660 (primary/fallback/
##            idempotent/read-contract); converged-тест переведён с stat 0664 на getfacl ACL
# endregion MODULE_CONTRACT
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Load the LDD trajectory decorator from shared conftest
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "converge"
sys.path.insert(0, str(_MODULE_DIR))
import reconciler

import core.internal.bootstrap.converge.audit as _converge_audit
import core.internal.shared.audit_logger as _shared_audit_logger
from core.internal.bootstrap.converge import infra
from core.internal.shared.audit_logger import read_audit_log

# Re-export for fixture cleanups
MODULE = reconciler


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run to return successful responses for docker/system commands.

    Returns a callable that can be further configured per-test via .side_effect.
    """

    def _default_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

        # docker info → success
        if "docker info" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker network inspect proxy-net → simulates network not found
        if "network inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Error: No such network")
        # docker network create → success
        if "network create" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="proxy-net\n", stderr="")
        # docker ps → empty
        if "docker ps" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker inspect container → no networks
        if "docker inspect" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # docker exec nginx nginx -t → success
        if "nginx -t" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="nginx: the configuration file ... syntax is ok", stderr=""
            )
        # id -nG ci-deploy → success with adm group
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        # stat → return 644 0:0
        if "stat" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="644\n", stderr="")
        # usermod → success
        if "usermod" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # chmod/chown/mkdir → success
        if any(x in cmd_str for x in ("chmod", "chown", "mkdir", "touch")):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=_default_mock) as mock:
        yield mock


@pytest.fixture(autouse=True)
def _restore_audit_globals():
    """Restore converge/audit module globals after each test.

    R2 tests monkeypatch _converge_audit.AUDIT_LOG_DIR/AUDIT_LOG_FILE to tmp_path.
    These are module-level globals shared process-wide — without restore they leak
    into test_converge_c8_audit_log_file.py (asserts converge_audit.AUDIT_LOG_FILE
    == shared DEFAULT_LOG_FILE). The monolith hid this via alphabetical ordering;
    the F6 split made the pollution visible. (DevPlan 118 F6)
    """
    from core.internal.shared.audit_logger import DEFAULT_LOG_FILE

    orig_dir = _converge_audit.AUDIT_LOG_DIR
    orig_file = _converge_audit.AUDIT_LOG_FILE
    yield
    _converge_audit.AUDIT_LOG_DIR = orig_dir
    _converge_audit.AUDIT_LOG_FILE = orig_file
    assert _converge_audit.AUDIT_LOG_FILE in {DEFAULT_LOG_FILE, orig_file}


# endregion Fixtures


# region FUNC_test_reconcile_audit_log_symlink_fail
## 🧪 TRAP[TEST] · R2 symlink fail · Scenario: /var/log/platform is a symlink → fail
## · Regression: converge.sh lines 354-365 — symlink attack prevention
## · Last fail: never
## · Remove if: reconciler.R2 symlink detection removed
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_symlink_dir_fail(tmp_path, monkeypatch, caplog):
    """R2: Symlink log directory → status=fail."""
    caplog.set_level(logging.INFO)

    # Monkeypatch AUDIT_LOG_DIR to a symlink in tmp_path
    fake_dir = tmp_path / "var" / "log" / "platform"
    fake_link = tmp_path / "var" / "log" / "platform_link"
    fake_dir.mkdir(parents=True)
    fake_link.symlink_to(fake_dir)

    _converge_audit.AUDIT_LOG_DIR = str(fake_link)
    _converge_audit.AUDIT_LOG_FILE = str(fake_link / "audit.log")

    entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert entry["status"] == "fail"
    assert "Symlink" in entry["detail"]


# endregion FUNC_test_reconcile_audit_log_symlink_dir_fail


# region FUNC_test_reconcile_audit_log_missing_file
## 🧪 TRAP[TEST] · R2 missing audit.log · Scenario: audit.log does not exist → created
## · Regression: converge.sh lines 411-424
## · Last fail: never
## · Remove if: reconciler.R2 creation logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_missing_file(tmp_path, monkeypatch, caplog):
    """R2: audit.log missing → created (writable by root + ci-deploy via ensure_audit_writable)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(log_dir / "audit.log")

    entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"

    # Verify file was created
    assert (log_dir / "audit.log").is_file(), "audit.log should have been created"


# endregion FUNC_test_reconcile_audit_log_missing_file


# region FUNC_test_reconcile_audit_log_converged
## 🧪 TRAP[TEST] · R2 converged (ACL) · Scenario: audit.log already ACL u:ci-deploy:rw + mask rw → SKIP
## · Regression: P1 fix 2026-08-27 — audit_permissions_status() == "acl" (stat 0664 больше не детектор)
## · Last fail: никогда (тест переведён на getfacl-детект)
## · Remove if: reconciler.R2 converged check changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_converged(tmp_path, monkeypatch, caplog):
    """R2: audit.log already converged (ACL u:ci-deploy:rw + mask rw) → status=converged."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")  # empty file exists

    # getfacl доступен (иначе на dev-машине ветка getfacl пропускается → state=none)
    monkeypatch.setattr(
        _shared_audit_logger.shutil, "which", lambda name: "/usr/bin/getfacl" if name == "getfacl" else None
    )

    # Mock getfacl → ACL state: named user ci-deploy rw + mask rw; id -nG → ci-deploy in adm
    def acl_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "getfacl" in cmd_str:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="user::rw-\nuser:ci-deploy:rw-\ngroup::r--\nmask::rw-\nother::---\n",
                stderr="",
            )
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    with patch.object(subprocess, "run", side_effect=acl_mock):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert entry["status"] == "converged"
    assert not infra.has_errors
    assert not infra.has_warnings, "ACL-converged file должен быть no-op (idempotent, без мутаций)"


# endregion FUNC_test_reconcile_audit_log_converged


# region FUNC_test_reconcile_audit_log_ci_deploy_group
## 🧪 TRAP[TEST] · R2 ci-deploy group · Scenario: ci-deploy not in adm → usermod
## · Regression: converge.sh lines 368-391
## · Last fail: never
## · Remove if: reconciler.R2 group logic changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_ci_deploy_group(tmp_path, monkeypatch, caplog):
    """R2: ci-deploy NOT in adm group → calls usermod."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    usermod_called = []

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # ci-deploy exists but NOT in adm
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy docker\n", stderr="")
        if "usermod" in cmd_str:
            usermod_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "stat" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="664\n", stderr="")
        if "nginx" in cmd_str or "docker" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=mock_run):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert len(usermod_called) > 0, "usermod should have been called"
    assert "adm" in " ".join(usermod_called[0])


# endregion FUNC_test_reconcile_audit_log_ci_deploy_group


# region FUNC_test_reconcile_audit_log_primary_acl_branch
## 🧪 TRAP[TEST] · R2 setfacl primary · Scenario: setfacl доступен + euid=0, файл 0644 → ACL
## · Regression: P1 fix 2026-08-27 — ensure_audit_writable primary-ветка (setfacl -m u:ci-deploy:rw,m::rw);
## ·   P1 fix 2026-09-01 (F-07) — + access ACL traversal u:ci-deploy:--x на КАТАЛОГ
## · Last fail: никогда (новый целевой контракт)
## · Remove if: primary-ветка заменена на иной механизм записи
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_primary_acl_branch(tmp_path, monkeypatch, caplog):
    """R2: setfacl available + euid=0 + non-converged file → setfacl -m/-d/-m(dir traversal) applied (primary)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # euid=0 (root-нода) + setfacl доступен
    monkeypatch.setattr(_shared_audit_logger.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_shared_audit_logger.shutil, "which", lambda _: "/usr/bin/setfacl")

    setfacl_called: list[list[str]] = []
    chgrp_called: list[list[str]] = []

    def acl_fix_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "getfacl" in cmd_str:
            # non-converged: named-user отсутствует
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="user::rw-\ngroup::r--\nmask::r--\nother::---\n", stderr=""
            )
        if "setfacl" in cmd_str:
            setfacl_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "chgrp" in cmd_str:
            chgrp_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=acl_fix_mock):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    # R2 final return — "converged" при отсутствии ошибок; мутация фиксируется has_warnings + drift
    assert not infra.has_errors
    assert infra.has_warnings, "мутация (set_exit(1)) должна выставить has_warnings"
    mutated = [d for d in infra.drifts if d["status"] == "mutated"]
    assert any("acl" in d["detail"] for d in mutated), f"drift detail: {infra.drifts}"
    # primary: setfacl -m u:ci-deploy:rw,m::rw <file> + setfacl -d (default ACL на dir)
    #          + setfacl -m u:ci-deploy:--x <dir> (access ACL traversal, P1 fix 2026-09-01 F-07)
    assert len(setfacl_called) == 3, f"ожидались -m(файл) -d(dir) -m(dir traversal), получено {setfacl_called}"
    assert "u:ci-deploy:rw" in " ".join(setfacl_called[0])
    assert setfacl_called[0][1] == "-m" and setfacl_called[1][1] == "-d"
    assert setfacl_called[2][1] == "-m"
    assert "u:ci-deploy:--x" in " ".join(setfacl_called[2]), f"dir traversal --x обязателен: {setfacl_called[2]}"
    assert setfacl_called[2][-1] == str(log_dir), f"traversal-ACL должен целиться в dir: {setfacl_called[2]}"
    # fallback НЕ задействован
    assert not chgrp_called, "primary-ветка не должна вызывать chgrp"
    logger.info("[IMP:9][test] R2 primary ACL: setfacl -m/-d/-m(dir traversal) корректно применены")


# endregion FUNC_test_reconcile_audit_log_primary_acl_branch


# region FUNC_test_reconcile_audit_log_fallback_group_branch
## 🧪 TRAP[TEST] · R2 fallback group · Scenario: setfacl НЕ доступен + euid=0 → chgrp ci-deploy + chmod 0660 (файл)
##   + chgrp ci-deploy + chmod 0710 (КАТАЛОГ — traversal, P1 fix 2026-09-01 F-07)
## · Regression: P1 fix 2026-08-27 — graceful fallback (honest trade-off, TRAP[DECISION]);
## ·   P1 fix 2026-09-01 — dir traversal fallback (chgrp + 0710, group --x other ---)
## · Last fail: никогда (новый целевой контракт)
## · Remove if: fallback-ветка заменена на иной механизм записи
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_fallback_group_branch(tmp_path, monkeypatch, caplog):
    """R2: setfacl НЕ доступен + euid=0 + non-converged file → chgrp ci-deploy + chmod 0660 (file) / 0710 (dir)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # euid=0, НО setfacl недоступен (и getfacl недоступен — статус через stat)
    monkeypatch.setattr(_shared_audit_logger.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_shared_audit_logger.shutil, "which", lambda _: None)
    # симуляция ноды: пользователь ci-deploy существует (на dev-машине его нет → KeyError)
    import pwd as _pwd
    import types

    monkeypatch.setattr(_pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_name=name))

    chgrp_called: list[list[str]] = []
    chmod_called: list[list[str]] = []

    def fallback_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "chgrp" in cmd_str:
            chgrp_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "chmod" in cmd_str:
            chmod_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=fallback_mock):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert entry["unit"] == "R2"
    assert not infra.has_errors
    assert infra.has_warnings, "мутация (set_exit(1)) должна выставить has_warnings"
    mutated = [d for d in infra.drifts if d["status"] == "mutated"]
    assert any("group" in d["detail"] for d in mutated), f"drift detail: {infra.drifts}"
    # fallback: chgrp ci-deploy на ФАЙЛ + КАТАЛОГ (P1 fix 2026-09-01 F-07: dir traversal),
    #           chmod 0660 на файл + 0710 на каталог (group --x other ---, traversal-only)
    assert len(chgrp_called) == 2, f"fallback обязан вызвать chgrp ci-deploy (file + dir), получено {chgrp_called}"
    assert chgrp_called[0] == ["chgrp", "ci-deploy", str(log_dir / "audit.log")], f"chgrp файла: {chgrp_called}"
    assert chgrp_called[1] == ["chgrp", "ci-deploy", str(log_dir)], f"chgrp каталога обязателен: {chgrp_called}"
    assert len(chmod_called) == 2, f"fallback обязан вызвать chmod 0660 (file) + 0710 (dir), получено {chmod_called}"
    assert chmod_called[0] == ["chmod", "0710", str(log_dir)], (
        f"dir chmod 0710 (traversal-only) обязателен: {chmod_called}"
    )
    assert chmod_called[1] == ["chmod", "0660", str(log_dir / "audit.log")], f"file chmod 0660 сохранён: {chmod_called}"
    logger.info("[IMP:9][test] R2 fallback: chgrp ci-deploy + chmod 0660 (file) / 0710 (dir) корректно применены")


# endregion FUNC_test_reconcile_audit_log_fallback_group_branch


# region FUNC_test_reconcile_audit_log_idempotent
## 🧪 TRAP[TEST] · R2 idempotent ACL · Scenario: два прогона на ACL-converged файле → оба no-op
## · Regression: P1 fix 2026-08-27 — audit_permissions_status детектирует acl → второй прогон без дрейфа
## · Last fail: никогда (новый целевой контракт)
## · Remove if: идемпотентность R2 переведена на state-based reconciler
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_idempotent(tmp_path, monkeypatch, caplog):
    """R2: повторный прогон на ACL-converged файле → converged, без мутаций (идемпотентность)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # getfacl доступен (иначе на dev-машине ветка getfacl пропускается → state=none)
    monkeypatch.setattr(
        _shared_audit_logger.shutil, "which", lambda name: "/usr/bin/getfacl" if name == "getfacl" else None
    )

    acl_out = "user::rw-\nuser:ci-deploy:rw-\ngroup::r--\nmask::rw-\nother::---\n"
    setfacl_called: list[list[str]] = []

    def idempotent_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "getfacl" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=acl_out, stderr="")
        if "setfacl" in cmd_str:
            setfacl_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=idempotent_mock):
        first = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)
        infra.reset_state()
        infra.node_name = "test-node"
        infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
        second = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)

    assert first["status"] == "converged"
    assert second["status"] == "converged"
    assert not infra.has_warnings, "повторный прогон не должен порождать мутации"
    assert not setfacl_called, "converged-файл не требует setfacl (no-op)"
    logger.info("[IMP:9][test] R2 идемпотентен: повторный прогон на ACL-состоянии — no-op")


# endregion FUNC_test_reconcile_audit_log_idempotent


# region FUNC_test_reconcile_audit_log_read_contract
## 🧪 TRAP[TEST] · R2 read-contract · Scenario: converged (ACL) файл остаётся ЧИТАЕМЫМ — read_audit_log
## · Regression: P1 fix 2026-08-27 — смена target-состояния не должна ломать контракт чтения
## · Last fail: никогда (новый целевой контракт)
## · Remove if: read_audit_log контракт изменён
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_read_contract(tmp_path, monkeypatch, caplog):
    """R2: после конвергенции (ACL) read_audit_log по-прежнему возвращает записи (read-контракт)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.log"
    audit_file.write_text(json.dumps({"ts": "2026-08-27T00:00:00Z", "tag": "test:read", "status": "DEPLOYED"}) + "\n")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # getfacl доступен (иначе на dev-машине ветка getfacl пропускается → state=none)
    monkeypatch.setattr(
        _shared_audit_logger.shutil, "which", lambda name: "/usr/bin/getfacl" if name == "getfacl" else None
    )

    acl_out = "user::rw-\nuser:ci-deploy:rw-\ngroup::r--\nmask::rw-\nother::---\n"

    def read_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "getfacl" in cmd_str:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=acl_out, stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=read_mock):
        entry = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)
        assert entry["status"] == "converged"

    # Read-contract: файл остаётся читаемым и парсимым после конвергенции
    entries = read_audit_log(log_file=str(audit_file), limit=10)
    assert len(entries) == 1
    assert entries[0]["tag"] == "test:read"
    assert entries[0]["status"] == "DEPLOYED"
    logger.info("[IMP:9][test] R2 read-contract: записи читаемы после конвергенции (ACL)")


# endregion FUNC_test_reconcile_audit_log_read_contract
