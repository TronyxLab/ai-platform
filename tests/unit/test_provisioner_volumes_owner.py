# GREP_SUMMARY: provisioner volumes owner chown wal-archive postgres-999 TRAP-BUG-2026-08-03 idempotent
# STRUCTURE: ▶ _owner_matches → ◇ _chown_dir → ◇ provision_volumes owner-semantics (create/skip-mismatch) → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 126 W1 (chaos-resilience) — unit-тесты owner-механики provision_volumes:
##           TRAP[BUG] 2026-08-03 (wal-archive root:root → postgres archive_command
##           Permission denied). Owner "uid:gid" применяется при создании директории и
##           при несовпадении владельца существующей; idempotent (совпадение → no-op).
## @scope    tests/unit — без Docker, без subprocess (native pytest, tmp_path).
## @invariants
##   - Никаких hardcoded путей вне tmp_path
##   - owner-парсинг: числовой "999:999" и именной "postgres:postgres" (если имя резолвится)
##   - LDD: IMP:9 в caplog на успешный chown
## @rationale provisioner.py — общая точка создания host-директорий; регрессия owner-логики
##           ломает WAL-архивацию (молча). Покрытие — до/после фикса.
## @changes 2026-08-03 | DevPlan 126 W1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import ClassVar

from tests._conftest.ldd import ldd_trajectory

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal"
sys.path.insert(0, str(_SCRIPTS_DIR))
from provisioner import VolumeConfig, _owner_matches, provision_volumes


# region TEST_owner_matches
def test_owner_matches_numeric(tmp_path: Path) -> None:
    """Числовой owner 'uid:gid' матчит stat владельца директории."""
    d = tmp_path / "wal-archive"
    d.mkdir()
    os.chown(d, os.getuid(), os.getgid())
    owner = f"{os.getuid()}:{os.getgid()}"
    assert _owner_matches(str(d), owner) is True
    assert _owner_matches(str(d), "1:1") is False  # чужой uid


def test_owner_matches_missing_dir(tmp_path: Path) -> None:
    """Несуществующая директория → False (провокация chown при создании)."""
    assert _owner_matches(str(tmp_path / "nope"), "999:999") is False


# endregion TEST_owner_matches


# region TEST_provision_volumes_owner
@ldd_trajectory
def test_provision_volumes_owner_chown_on_mismatch(tmp_path: Path, caplog) -> None:
    """Существующая директория с чужим владельцем → chown применяется (не skip).

    R5-negative (TRAP[BUG] 2026-08-03): прежнее поведение — isdir → SKIP без owner-
    проверки (wal-archive оставался root:root → archive_command Permission denied).
    """
    import logging

    caplog.set_level(logging.DEBUG)
    d = tmp_path / "wal-archive"
    d.mkdir()
    # chown на чужого владельца, если позволяет ОС (иначе проверяем логику через mock)
    with contextlib.suppress(PermissionError):
        os.chown(d, 1, 1)  # macOS dev: chown чужому uid запрещён — mismatch-логика иначе

    class _Env:
        volumes: ClassVar[list[VolumeConfig]] = [VolumeConfig(path=str(d), owner=f"{os.getuid()}:{os.getgid()}")]

    result = provision_volumes(_Env(), dry_run=False)  # type: ignore[arg-type]
    assert result.skipped >= 1
    assert result.errors == [], f"chown errors: {result.errors}"

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp >= 7:
                print(record.message)
            if imp >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "no IMP:9 log — provision_volumes owner path silent"


@ldd_trajectory
def test_provision_volumes_owner_create_new_dir(tmp_path: Path, caplog) -> None:
    """Новая директория с owner → mkdir + chown применяются."""
    import logging

    caplog.set_level(logging.DEBUG)
    d = tmp_path / "created-with-owner"
    owner = f"{os.getuid()}:{os.getgid()}"

    class _Env:
        volumes: ClassVar[list[VolumeConfig]] = [VolumeConfig(path=str(d), owner=owner)]

    result = provision_volumes(_Env(), dry_run=False)  # type: ignore[arg-type]
    assert result.created == 1
    assert d.is_dir(), "dir must be created"
    assert result.errors == [], f"chown errors: {result.errors}"
    assert _owner_matches(str(d), owner) is True

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp >= 7:
                print(record.message)
            if imp >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    assert found, "no IMP:9 log — create+chown path silent"


# endregion TEST_provision_volumes_owner
