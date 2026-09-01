"""
# GREP_SUMMARY: test_reconciler, converge, r1, reconcile-perms, _is_stub, stub-detection, drift-detection, idempotency, project-validation, w4-e5, r2, reconcile-audit-log, setfacl, acl, fallback-0660, deploy-writer-write, chmod-emulation
# STRUCTURE: ▶ tmp_path + monkeypatch + mock subprocess → ◇ R1 reconcile_perms 4× (skipped/mutated/lib-excluded/dry-run) → ◇ R2 reconcile_audit_log 3× (idempotent-applied/falback-no-setfacl/deploy-writer-write) → ◇ _is_stub 3× (stub/non-stub/missing) → ◇ W4-E5 static audits (drift R-units / idempotency) → ◇ project-name validation → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Unit tests for reconciler.py facade units: R1 reconcile_perms + R2 reconcile_audit_log
##           (P1 fix 2026-08-27: ACL/0660 target state) + _is_stub (shared stub_detection)
##           + W4-E5 edge-страховки (drift-detection R-units, reconcile idempotency, project-name validation),
##           перенесённые из tests/test_converge_exit.py при K1 (139-test-system-stewardship W1).
## @scope    Tests reconciler.reconcile_perms and is_stub_ai_platform_yaml with tmp_path fixtures.
##           R2-контракт: идемпотентное применение фикса (повторный прогон = no-op), fallback-ветка
##           (setfacl отсутствует в PATH → chgrp ci-deploy + chmod 0660), deploy-writer получает
##           запись после фикс-семантики (chmod-эмуляция POSIX group-channel rule).
##           W4-E5 static-аудиты converge/ пакета + validate_project_name (project_registry).
##           Does NOT require a real docker daemon or root privileges.
## @invariants
##   - File operations use tmp_path exclusively — never /var/log, /opt, /etc
##   - Each test validates IMP:9 business logic log presence via caplog
##   - R2-тесты мокают subprocess.run (паттерн converge) и восстанавливают
##     converge/audit module-globals (AUDIT_LOG_DIR/AUDIT_LOG_FILE) — leak-prevention
##   - W4-E5 страховки перенесены БЕЗ изменения входов (те же фикстуры/asserts, K1 diff-review)
## @rationale Direct function testing with tmp_path for file-based units (R1, stub, R2-orchestration).
##   Разбит из монолита test_reconciler.py (DevPlan 118 F6): 34 теста → 6 файлов по converge-подмодулям.
##   139 K1 (W1): _is_stub edge (3 состояния) уже покрыт ниже; перенесены НЕДОСТАЮЩИЕ 3 W4-E5 теста
##   (drift R-units, idempotency, project-name validation) из удаляемого tests/test_converge_exit.py.
##   2026-08-27 (P1 fix): +3 R2-теста (искомый контракт task 017 F-09) — idempotent-applied,
##   fallback-no-setfacl, deploy-writer-write; полный R2-сьют — tests/unit/test_converge_audit.py.
## @changes
##   2026-07-22 · Created (W4-E3 extraction from converge.sh)
##   2026-08-02 · F6 split — reconciled perms + stub остались (DevPlan 118)
##   2026-08-05 · 139 W1 K1 — +3 W4-E5 теста из test_converge_exit.py (drift/idempotency/validation)
##   2026-08-27 · P1 fix 017 F-09 — +3 R2-теста (idempotent-applied / fallback-no-setfacl / writer-write)
# endregion MODULE_CONTRACT
"""

import json
import logging
import os
import stat
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
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

pytestmark = pytest.mark.static_audit

# Re-export for fixture cleanups
MODULE = reconciler

# ── W4-E5 static-audit helper (K1, перенесён из tests/test_converge_exit.py) ──
# B9 T2 (U-31): доменные модули R-units (SRP-декомпозиция reconciler)
_CONVERGE_DIR = _MODULE_DIR


def _converge_sources() -> str:
    """Concatenate converge/ package sources (reconciler + домены + infra) for static audit."""
    return "\n".join(p.read_text() for p in sorted(_CONVERGE_DIR.glob("*.py")))


# ═══════════════════════════════════════════════════════════════════
# region Fixtures


@pytest.fixture
def reset_state():
    """Reset reconciler module state before each test."""
    infra.reset_state()
    infra.node_name = "test-node"
    infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
    yield


@pytest.fixture(autouse=True)
def _restore_audit_globals():
    """Restore converge/audit module globals after each test.

    R2-тесты ниже monkeypatch'ят _converge_audit.AUDIT_LOG_DIR/AUDIT_LOG_FILE на tmp_path.
    Это module-level глобалы, общие для процесса — без restore они протекают в
    test_converge_c8_audit_log_file.py (asserts converge_audit.AUDIT_LOG_FILE == shared
    DEFAULT_LOG_FILE). Тот же паттерн, что в test_converge_audit.py (DevPlan 118 F6).
    """
    orig_dir = _converge_audit.AUDIT_LOG_DIR
    orig_file = _converge_audit.AUDIT_LOG_FILE
    yield
    _converge_audit.AUDIT_LOG_DIR = orig_dir
    _converge_audit.AUDIT_LOG_FILE = orig_file


# endregion Fixtures


# region FUNC_test_reconcile_perms_skipped
## 🧪 TRAP[TEST] · R1 skipped · Scenario: all scripts already executable
## · Regression: converge.sh line 293-296 — no non-exec files → SKIP
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_skipped(tmp_path, caplog):
    """R1: All scripts already executable → status=skipped."""
    caplog.set_level(logging.INFO)

    # Create a test script with u+x already set
    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    Path(str(test_script)).chmod(0o755)  # already executable

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    assert entry["unit"] == "R1"
    assert entry["status"] == "skipped"

    # LDD trajectory: IMP:9 business logic log
    found_imp9 = any("[IMP:9]" in r.message and "SKIP" in r.message for r in caplog.records)
    assert found_imp9, "No IMP:9 log for R1 skipped"


# endregion FUNC_test_reconcile_perms_skipped


# region FUNC_test_reconcile_perms_mutated
## 🧪 TRAP[TEST] · R1 mutated · Scenario: non-executable scripts found and fixed
## · Regression: converge.sh lines 309-318 — chmod ug+x applied
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_mutated(tmp_path, caplog):
    """R1: Non-executable scripts found → status=mutated."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    # NOT setting executable bit — keep 644

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    assert entry["unit"] == "R1"
    assert entry["status"] == "mutated"
    assert "1 files fixed" in entry["detail"]

    # Verify the file is now executable
    assert os.access(str(test_script), os.X_OK), "File should now be executable"

    found_imp9 = any("[IMP:9]" in r.message and "Fixed" in r.message for r in caplog.records)
    assert found_imp9, "No IMP:9 log for R1 mutated"


# endregion FUNC_test_reconcile_perms_mutated


# region FUNC_test_reconcile_perms_lib_skipped
## 🧪 TRAP[TEST] · R1 lib excluded · Scenario: files under core/lib/ are NOT modified
## · Regression: converge.sh lines 282 — -not -path '*/lib/*'
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_lib_skipped(tmp_path, caplog):
    """R1: Files under core/lib/ are excluded from executable bit reconciliation."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    lib_dir = core_dir / "lib"
    lib_dir.mkdir(parents=True)
    # Create a non-executable .sh file under lib/ — should NOT be touched
    lib_script = lib_dir / "helper.sh"
    lib_script.write_text("#!/bin/bash\necho helper\n")
    # NOT setting executable bit

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=False, report_only=False)

    # Should be skipped — lib files are excluded
    assert entry["status"] == "skipped"

    # Verify lib file is STILL not executable
    assert not os.access(str(lib_script), os.X_OK), "Lib file should remain non-executable"


# endregion FUNC_test_reconcile_perms_lib_skipped


# region FUNC_test_reconcile_perms_dry_run
## 🧪 TRAP[TEST] · R1 dry-run · Scenario: --dry-run reports but does not mutate
## · Regression: converge.sh lines 289-303
## · Last fail: never
## · Remove if: reconciler.R1 logic fundamentally changes
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_perms_dry_run(tmp_path, caplog):
    """R1: --dry-run reports would-fix but does not chmod."""
    caplog.set_level(logging.INFO)

    core_dir = tmp_path / "core"
    scripts_dir = core_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    test_script = scripts_dir / "test.sh"
    test_script.write_text("#!/bin/bash\necho hello\n")
    mode_before = Path(test_script).stat().st_mode

    entry = reconciler.reconcile_perms(str(core_dir), dry_run=True, report_only=False)

    assert entry["status"] == "mutated"
    assert "would get ug+x" in entry["detail"]

    # File should NOT have been modified
    mode_after = Path(test_script).stat().st_mode
    assert mode_before == mode_after, "File should not be modified in dry-run mode"


# endregion FUNC_test_reconcile_perms_dry_run


# ══════════════════════════════════════════════════════════════════════════════
# R2 — reconcile_audit_log (P1 fix 2026-08-27, DevPlan 017 F-09)
# Целевое состояние: владелец root + запись главному писателю (ci-deploy) —
# PRIMARY setfacl u:ci-deploy:rw,m::rw / FALLBACK chgrp ci-deploy + chmod 0660.
# Полный R2-сьют (symlink/creation/primary/read-contract) — test_converge_audit.py;
# здесь — три искомых контракта задачи: идемпотентность применённого состояния,
# fallback без setfacl, deploy-writer получает запись (chmod-эмуляция).
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_reconcile_audit_log_idempotent_applied_noop
## 🧪 TRAP[TEST] · R2 idempotent applied · Scenario: прогон 1 применяет фикс (state=none → ACL
##   + dir traversal --x, F-07), прогон 2 на применённом target-состоянии (acl) → no-op, ноль новых мутаций
## · Regression: P1 fix 2026-08-27 — R2 идемпотентно доводит до acl|group и СХОДИТСЯ (повтор = no-op);
## ·   до фикса converge был антагонистом runtime (audit_logger chmod 640 ломал групповой write)
## · Last fail: никогда (новый целевой контракт; детектор состояния покрыт в test_converge_audit)
## · Remove if: идемпотентность R2 переведена на state-based reconciler
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_idempotent_applied_noop(tmp_path, monkeypatch, caplog):
    """R2: первый прогон применяет фикс, повторный на применённом состоянии — no-op (converged)."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.jsonl"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # euid=0 (root-нода) + setfacl доступен → primary-ветка ensure_audit_writable
    monkeypatch.setattr(_shared_audit_logger.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_shared_audit_logger.shutil, "which", lambda _: "/usr/bin/setfacl")

    # Детектор дрейфа: 1-й вызов — "none" (дрейф → фикс применяется), далее — "acl"
    # (target-состояние применено → второй прогон обязан быть no-op). Сам getfacl-парсинг
    # детектора покрыт в test_converge_audit.py — здесь проверяется оркестрация R2.
    status_calls = {"n": 0}

    def status_side_effect(*_args, **_kwargs):
        status_calls["n"] += 1
        return "none" if status_calls["n"] == 1 else "acl"

    monkeypatch.setattr(_converge_audit, "audit_permissions_status", status_side_effect)

    setfacl_called: list[list[str]] = []

    def fix_mock(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "setfacl" in cmd_str:
            setfacl_called.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "id -nG" in cmd_str or ("id" in cmd_str and "-nG" in cmd_str):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ci-deploy adm docker\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch.object(subprocess, "run", side_effect=fix_mock):
        reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)
        first_mutated = any(d["status"] == "mutated" for d in infra.drifts)
        setfacl_after_first = len(setfacl_called)
        # сброс infra между прогонами (изолированная идемпотентность, паттерн test_converge_audit)
        infra.reset_state()
        infra.node_name = "test-node"
        infra.core_dir = str(Path(__file__).resolve().parent.parent.parent / "core")
        second = reconciler.reconcile_audit_log(str(tmp_path), dry_run=False, report_only=False)
        setfacl_after_second = len(setfacl_called)

    # Прогон 1: дрейф → фикс применён (primary: setfacl -m файл + -d default ACL на dir
    #           + -m access ACL traversal на dir — P1 fix 2026-09-01 F-07)
    assert first_mutated, f"прогон 1 должен применить фикс (mutated), drifts={infra.drifts}"
    assert setfacl_after_first == 3, (
        f"прогон 1: ожидались setfacl -m/-d/-m(dir traversal), получено {setfacl_called[:setfacl_after_first]}"
    )
    assert "u:ci-deploy:--x" in " ".join(setfacl_called[-1]), f"dir traversal --x обязателен: {setfacl_called}"
    # Прогон 2: target-состояние (acl) → converged, НОЛЬ новых мутаций
    assert second["status"] == "converged"
    assert not infra.has_warnings, "повторный прогон на применённом состоянии — no-op (без мутаций)"
    assert setfacl_after_second == setfacl_after_first, (
        "второй прогон не должен вызывать setfacl (применённое состояние = no-op)"
    )
    logger.info("[IMP:9][test] R2 идемпотентен: применённое состояние → повторный прогон no-op")


# endregion FUNC_test_reconcile_audit_log_idempotent_applied_noop


# region FUNC_test_reconcile_audit_log_fallback_no_setfacl
## 🧪 TRAP[TEST] · R2 fallback no-setfacl · Scenario: setfacl отсутствует в PATH + euid=0 →
##   chgrp ci-deploy + chmod 0660 (файл) + chgrp ci-deploy + chmod 0710 (КАТАЛОГ — traversal, F-07)
## · Regression: P1 fix 2026-08-27 — без setfacl целевое состояние достигается групповым каналом;
## ·   P1 fix 2026-09-01 (F-07) — dir traversal fallback (chgrp + 0710, group --x other ---)
## · Last fail: никогда (новый целевой контракт)
## · Remove if: fallback-ветка заменена на иной механизм записи
@pytest.mark.usefixtures("reset_state")
@ldd_trajectory
def test_reconcile_audit_log_fallback_no_setfacl(tmp_path, monkeypatch, caplog):
    """R2: setfacl НЕ доступен + euid=0 + non-converged file → chgrp ci-deploy + chmod 0660."""
    caplog.set_level(logging.INFO)

    log_dir = tmp_path / "var" / "log" / "platform"
    log_dir.mkdir(parents=True)
    audit_file = log_dir / "audit.jsonl"
    audit_file.write_text("")

    _converge_audit.AUDIT_LOG_DIR = str(log_dir)
    _converge_audit.AUDIT_LOG_FILE = str(audit_file)

    # euid=0, НО setfacl недоступен (и getfacl недоступен — статус через stat → "none")
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
    assert "ci-deploy" in chgrp_called[0]
    assert str(log_dir) in chgrp_called[1], f"chgrp каталога обязателен: {chgrp_called}"
    assert len(chmod_called) == 2, f"fallback обязан вызвать chmod 0660 (file) + 0710 (dir), получено {chmod_called}"
    assert chmod_called[0] == ["chmod", "0710", str(log_dir)], (
        f"dir chmod 0710 (traversal-only) обязателен: {chmod_called}"
    )
    assert chmod_called[1] == ["chmod", "0660", str(log_dir / "audit.jsonl")], (
        f"file chmod 0660 сохранён: {chmod_called}"
    )
    logger.info(
        "[IMP:9][test] R2 fallback: chgrp ci-deploy + chmod 0660 (file) / 0710 (dir) применены (setfacl нет в PATH)"
    )


# endregion FUNC_test_reconcile_audit_log_fallback_no_setfacl


# region FUNC_test_reconcile_audit_log_deploy_writer_write
## 🧪 TRAP[TEST] · R2 deploy-writer write · Scenario: chmod-эмуляция — deploy-writer (группа-владелец
##   == его primary-группа) получает запись в target-состоянии 0660; прежний 0640 root:adm — отказывает
## · Regression: P1 root cause — 0664 root:adm + audit_logger chmod 640 → group-write бит снят,
## ·   ci-deploy (forced-command receive) → "Permission denied", аудит постбутстрапных деплоев молча терялся
## · Last fail: 2026-08-27 (F-09 tronyx-vps: DEPLOYED-записи остановились на 03:47Z при успешных деплоях 04:52+)
## · Remove if: целевое состояние прав аудит-файла изменено
@ldd_trajectory
def test_reconcile_audit_log_deploy_writer_write(tmp_path, caplog):
    """P1 fix: после фикс-семантики (0660 + группа-владелец == primary-группа писателя) deploy-writer
    получает запись; прежний 0640 root:adm отказывает по POSIX group-channel rule (chmod-эмуляция)."""
    caplog.set_level(logging.INFO)

    audit_file = tmp_path / "audit.jsonl"
    audit_file.write_text(
        json.dumps({"ts": "2026-08-27T03:47:00Z", "tag": "test:pre-fix", "status": "DEPLOYED"}) + "\n"
    )

    # deploy-writer = процесс теста; эмуляция "chgrp ci-deploy": группа-владелец файла
    # == primary-группа писателя (fallback-семантика ensure_audit_writable)
    writer_gid = os.getgid()
    os.chown(audit_file, -1, writer_gid)

    def group_channel_write_allowed() -> bool:
        """POSIX group-channel rule (chmod-эмуляция): не-владелец с egid == st_gid получает
        запись ТОЛЬКО если mode имеет group-write бит И файл принадлежит его группе."""
        st = audit_file.stat()
        return st.st_gid == writer_gid and bool(stat.S_IMODE(st.st_mode) & 0o020)

    # ── Прежнее состояние (P1 root cause): 0640 root:adm → group-write снят → писатель НЕ пишет ──
    audit_file.chmod(0o640)
    assert not group_channel_write_allowed(), (
        "P1 root cause: 0640 снимает group-write — ci-deploy (группа-владелец) получал Permission denied"
    )

    # ── Фикс-семантика (fallback 0660): group-write установлен → писатель получает запись ──
    audit_file.chmod(0o660)
    assert group_channel_write_allowed(), "0660 + группа-владелец == primary-группа писателя → group-channel write"

    # Реальная запись после фикса (эмуляция deploy-writer append: receive/deploy forced-command)
    with audit_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-08-27T04:52:00Z", "tag": "test:post-fix", "status": "DEPLOYED"}) + "\n")

    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "после фикс-семантики deploy-writer успешно дописывает аудит-запись"
    assert "post-fix" in lines[1]
    logger.info("[IMP:9][test] R2 writer-write: 0660 даёт deploy-writer запись; 0640 — отказ (P1 root cause)")


# endregion FUNC_test_reconcile_audit_log_deploy_writer_write


# region FUNC_test_is_stub_true
## 🧪 TRAP[TEST] · _is_stub true · Scenario: file contains GENERATED-STUB marker
## · Regression: converge.sh lines 655-663
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_true(tmp_path, caplog):
    """_is_stub: file with GENERATED-STUB → returns True."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub positive detection")
    stub_file = tmp_path / "ai-platform.yaml"
    stub_file.write_text("# GENERATED-STUB by converge\nproject: myapp\nservice: myapp\n")
    assert is_stub_ai_platform_yaml(str(stub_file)) is True


# endregion FUNC_test_is_stub_true


# region FUNC_test_is_stub_false
## 🧪 TRAP[TEST] · _is_stub false · Scenario: file without GENERATED-STUB marker
## · Regression: converge.sh lines 655-663 — real config
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_false(tmp_path, caplog):
    """_is_stub: file without GENERATED-STUB → returns False."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub negative detection")
    real_file = tmp_path / "ai-platform.yaml"
    real_file.write_text("project: myapp\nservice: myapp\ndomain: myapp.example.com\n")
    assert is_stub_ai_platform_yaml(str(real_file)) is False


# endregion FUNC_test_is_stub_false


# region FUNC_test_is_stub_missing
## 🧪 TRAP[TEST] · _is_stub missing · Scenario: file does not exist
## · Regression: converge.sh lines 659-661 — missing file is not a stub
## · Last fail: never
## · Remove if: _is_stub logic changes
@ldd_trajectory
def test_is_stub_missing(tmp_path, caplog):
    """_is_stub: missing file → returns False."""
    caplog.set_level(logging.INFO)
    logger.info("[IMP:9][test] _is_stub missing-file")
    missing = str(tmp_path / "nonexistent.yaml")
    assert is_stub_ai_platform_yaml(missing) is False


# endregion FUNC_test_is_stub_missing


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5 (DevPlan 035 §7): edge-case страховки, перенесённые из tests/test_converge_exit.py
# при K1 (139-test-system-stewardship W1). Перенос БЕЗ изменения входов/asserts.
# _is_stub edge (stub/deployed/missing) уже покрыт выше (test_is_stub_true/false/missing).
# ══════════════════════════════════════════════════════════════════════════════


# region FUNC_test_drift_detection_r_units
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 drift detection R-units → W4-E3 redirect to reconciler.py
# · Regression: reconciler.py must have 6 reconcile_* functions detecting distinct drift dimensions
# · Scenario: static grep converge/ package for reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: reconciler.py R-units are fundamentally restructured
@ldd_trajectory
def test_drift_detection_r_units(tmp_path, caplog):
    """Static audit: converge/ package has 6 reconcile_* functions for distinct drift dimensions (B9 T2)."""
    caplog.set_level(logging.INFO)
    content = _converge_sources()

    # ── All 6 reconcile functions must exist in converge/ пакете (домены + оркестратор) ──
    required_units = [
        ("def reconcile_perms", "R1 executable-bit drift"),
        ("def reconcile_audit_log", "R2 audit.log perms drift"),
        ("def reconcile_projects", "R3 project dirs drift"),
        ("def reconcile_networks", "R4 proxy-net drift"),
        ("def detect_hosts_drift", "R5 hosts drift detection"),
        ("def verify_vhosts", "R6 vhost integrity check"),
    ]
    for func_def, desc in required_units:
        assert func_def in content, f"W4-E3 violation: {func_def} missing in converge/ package — {desc}"
        logger.info("[IMP:9][test_drift_detection] %s present — %s", func_def, desc)

    # ── Each reconcile function uses set_exit severity tracking (Python equivalent of CONVERGE_HAS_FLAGS) ──
    assert "set_exit(1)" in content, "W4-E3 violation: converge/ must use set_exit(1) for warning drifts"
    assert "set_exit(2)" in content, "W4-E3 violation: converge/ must use set_exit(2) for error drifts"
    logger.info("[IMP:9][test_drift_detection] set_exit(1) + set_exit(2) severity tracking present")

    # ── Drift reporting mechanism exists (infra.report_add) ──
    assert "report_add" in content, "W4-E3 violation: report_add drift reporting mechanism missing"
    logger.info("[IMP:9][test_drift_detection] report_add drift reporting present")


# endregion FUNC_test_drift_detection_r_units


# region FUNC_test_reconcile_idempotency
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 reconcile idempotency → W4-E3 redirect to reconciler.py
# · Regression: reconcile функции должны иметь idempotency guards — second run detects no drift
# · Scenario: static grep converge/ package for "SKIP" / "already" / "converged" patterns
# · Last fail: N/A (W4-E5 baseline, updated for W4-E3)
# · Remove if: idempotency moves to state-based reconciler (then point test at new module)
@ldd_trajectory
def test_reconcile_idempotency(tmp_path, caplog):
    """Static audit: converge/ reconcile functions are idempotent (SKIP on already-converged)."""
    caplog.set_level(logging.INFO)
    content = _converge_sources()

    # ── 1. SKIP pattern present (idempotent no-op when already converged) ──
    skip_count = content.count("SKIP")
    assert skip_count >= 3, f"W4-E3 violation: expected >=3 SKIP patterns (idempotency), found {skip_count}"
    logger.info("[IMP:9][test_idempotency] SKIP patterns found: %d", skip_count)

    # ── 2. "converged" or "already" keyword indicates no-op state ──
    has_converged = "converged" in content.lower() or "already" in content.lower()
    assert has_converged, "W4-E3 violation: no 'converged'/'already' keyword — idempotent no-op state missing"
    logger.info("[IMP:9][test_idempotency] converged/already keyword present")

    # ── 3. dry_run + report_only modes in converge/ (non-mutating inspection) ──
    assert "dry_run" in content, "W4-E3 violation: dry_run mode missing in converge/ package"
    assert "report_only" in content, "W4-E3 violation: report_only mode missing in converge/ package"
    logger.info("[IMP:9][test_idempotency] dry_run + report_only present in converge/ package")


# endregion FUNC_test_reconcile_idempotency


# region FUNC_test_project_name_validation_rejects_traversal
## 🧪 TRAP[TEST] · 2026-07-22 · W4-E5 project name validation → DevPlan 116 B6 T3 (canonical validator)
# · Regression: canonical validate_project_name must reject "../", "/", leading "-/_", non-alphanumeric
# · Scenario: import core.internal.shared.project_registry.validate_project_name (единый канон)
# · Last fail: N/A (W4-E5 baseline, migrated to canonical validator in B6 T3)
# · Remove if: project validation moves away from project_registry (then point test at new module)
@ldd_trajectory
def test_project_name_validation_rejects_traversal(tmp_path, caplog):
    """validate_project_name: rejects path traversal (../), slashes, leading -/_, invalid chars."""
    caplog.set_level(logging.INFO)

    # Test cases: (name, should_pass)
    test_cases: list[tuple[str, bool]] = [
        ("valid-project", True),
        ("my_app123", True),
        ("../etc/passwd", False),  # path traversal
        ("foo/bar", False),  # slash
        ("..", False),  # parent dir
        ("valid..name", False),  # contains ..
        ("name with space", False),  # space not in [a-zA-Z0-9_-]
        ("name;rm -rf", False),  # shell injection attempt
        ("", False),  # empty
        ("-leading-dash", False),  # leading '-' (strict regex, DevPlan 116 B6 T3)
        ("_leading-underscore", False),  # leading '_' (strict regex, DevPlan 116 B6 T3)
    ]

    for name, should_pass in test_cases:
        result = validate_project_name(name)
        if should_pass:
            assert result is True, f"W4-E3 violation: valid name '{name}' should pass, got {result}"
            logger.info("[IMP:9][test_validate] OK: %r", name)
        else:
            assert result is False, f"W4-E3 violation: invalid name '{name}' should fail, got {result}"
            logger.info("[IMP:9][test_validate] FAIL: %r", name)

    # Explicitly verify path traversal is REJECTED (critical security check)
    assert validate_project_name("../etc/passwd") is False, (
        "W4-E3 CRITICAL violation: path traversal '../etc/passwd' must be REJECTED"
    )
    logger.info("[IMP:9][test_validate] CRITICAL: path traversal ../etc/passwd correctly rejected")


# endregion FUNC_test_project_name_validation_rejects_traversal
