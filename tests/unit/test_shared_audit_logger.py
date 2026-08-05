#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-audit-logger audit-logger write-audit-entry read-audit-log json-lines tmp-path
# STRUCTURE: ┌tmp_path fixtures┐ → ○ test scenarios: create_file → json_valid → limit → empty → multiple → timestamp
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/audit_logger.py
##           Verifies write_audit_entry() and read_audit_log() with JSON-lines format.
## @scope    Tests: file creation, JSON validity, read limit, empty log, multiple entries,
##           ISO8601 timestamp format with Z timezone.
## @invariants
##   - All tests use tmp_path (no hardcoded paths)
##   - No Docker dependency (pure Python)
##   - LDD: at least one IMP:9 log in each successful write operation
##   - No hardcoded /var/log/platform/ paths anywhere
# endregion MODULE_CONTRACT

import json
import logging
import re
from pathlib import Path

import pytest

from core.internal.shared.audit_logger import read_audit_log, write_audit_entry

logger = logging.getLogger(__name__)

# ── Tests ───────────────────────────────────────────────────────────────────


# region FUNC_test_write_entry_creates_file
## @purpose — Verify first call to write_audit_entry creates the log file.
##            AC: after write, file exists on disk.
## @complexity — O(1)
def test_write_entry_creates_file(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """First call to write_audit_entry creates the log file."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: first write creates file
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes file creation semantics

    log_file = tmp_path / "audit.jsonl"
    assert not log_file.exists(), "Precondition: file must not exist before first write"

    write_audit_entry("test:proj", "OK", "First entry", log_file=str(log_file))

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert log_file.exists(), "File must be created after first write"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_write_entry_json_valid
## @purpose — Verify written entry is valid JSON with the expected four fields.
##            AC: write → read raw file → JSON parse → assert ts, tag, status, msg keys.
## @complexity — O(1)
def test_write_entry_json_valid(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Written entry must be valid JSON with ts, tag, status, msg fields."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: JSON-lines format validity
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes wire format

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry("test:myproj", "DEPLOYED", "Deployment completed", log_file=str(log_file))

    # Read raw file and parse JSON
    raw = log_file.read_text(encoding="utf-8").strip()
    record = json.loads(raw)

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record_log in caplog.records:
        if "[IMP:" in record_log.message:
            imp_level = int(record_log.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record_log.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert isinstance(record, dict), "Parsed JSON must be a dict"
    assert "ts" in record, "Entry must contain 'ts' field"
    assert "tag" in record, "Entry must contain 'tag' field"
    assert "status" in record, "Entry must contain 'status' field"
    assert "msg" in record, "Entry must contain 'msg' field"
    assert record["tag"] == "test:myproj"
    assert record["status"] == "DEPLOYED"
    assert record["msg"] == "Deployment completed"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_read_entries_limit
## @purpose — Verify read_audit_log respects the limit parameter.
##            AC: write 3 entries → read(limit=2) → returns exactly 2 entries.
## @complexity — O(1)
def test_read_entries_limit(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """read_audit_log(limit=2) must return only the 2 most recent entries."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: limit parameter
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes read semantics

    log_file = tmp_path / "audit.jsonl"
    for i in range(3):
        write_audit_entry("test:limit", f"EVT{i}", f"Entry number {i}", log_file=str(log_file))

    entries = read_audit_log(log_file=str(log_file), limit=2)

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert len(entries) == 2, f"Expected 2 entries, got {len(entries)}"
    # Were they limited correctly (should return entries 1 and 2, not 0 and 1)?
    # read_audit_log returns last N from end → chronological order
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_read_empty_log
## @purpose — Verify read_audit_log on a non-existent file returns [].
##            AC: non-existent path → returns empty list.
## @complexity — O(1)
def test_read_empty_log(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """read_audit_log on non-existent file must return []."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: missing log file
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes missing-file behavior

    log_file = tmp_path / "nonexistent.jsonl"
    assert not log_file.exists(), "Precondition: file must not exist"

    entries = read_audit_log(log_file=str(log_file))

    # LDD trajectory (IMP:8 expected — file not found, no IMP:9)
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
    print("--- END LDD TRAJECTORY ---")

    assert entries == [], "Non-existent file must return empty list"


# endregion


# region FUNC_test_multiple_entries
## @purpose — Verify writing 5 entries and reading all returns 5 valid JSON entries.
##            AC: 5 writes → read_audit_log → 5 entries, each parsable JSON with correct fields.
## @complexity — O(1)
def test_multiple_entries(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Writing 5 entries then reading must return 5 valid JSON entries."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: batch write-read roundtrip
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes multi-entry semantics

    log_file = tmp_path / "audit.jsonl"
    expected = []
    for i in range(5):
        tag = f"test:multi:{i}"
        status = "OK" if i % 2 == 0 else "FAIL"
        msg = f"Batch entry #{i}"
        write_audit_entry(tag, status, msg, log_file=str(log_file))
        expected.append({"tag": tag, "status": status, "msg": msg})

    entries = read_audit_log(log_file=str(log_file))

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    assert len(entries) == 5, f"Expected 5 entries, got {len(entries)}"

    for i, entry in enumerate(entries):
        assert isinstance(entry, dict), f"Entry {i} must be a dict, got {type(entry)}"
        assert "ts" in entry, f"Entry {i} missing 'ts'"
        assert "tag" in entry, f"Entry {i} missing 'tag'"
        assert "status" in entry, f"Entry {i} missing 'status'"
        assert "msg" in entry, f"Entry {i} missing 'msg'"
        assert entry["tag"] == expected[i]["tag"], f"Entry {i} tag mismatch"
        assert entry["status"] == expected[i]["status"], f"Entry {i} status mismatch"
        assert entry["msg"] == expected[i]["msg"], f"Entry {i} msg mismatch"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_entry_timestamp_format
## @purpose — Verify entry contains ts in ISO8601 format with Z timezone.
##            AC: ts matches YYYY-MM-DDTHH:MM:SSZ regex.
## @complexity — O(1)
def test_entry_timestamp_format(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Each entry must have ts in ISO8601 format with Z timezone."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: ISO8601 timestamp with Z
    # · Last fail: N/A (new test)
    # · Remove if: audit logger changes timestamp format

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry("test:ts", "OK", "Timestamp check", log_file=str(log_file))

    entries = read_audit_log(log_file=str(log_file))
    assert len(entries) == 1, "Expected exactly one entry"

    ts = entries[0]["ts"]

    # LDD trajectory
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_imp9 = True
    print("--- END LDD TRAJECTORY ---")

    # ISO8601 with Z timezone: 2026-07-26T12:00:00Z
    iso8601_z_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert iso8601_z_re.match(ts), f"Timestamp '{ts}' does not match ISO8601 with Z format"
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion


# region FUNC_test_write_entry_extended_schema
## @purpose — DevPlan 116 B11 T2 (U-10, D1): расширенная схема — extra-поля
##            (operation/project/channel/result/duration_s/snapshot_id) в той же JSON-строке.
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_entry_extended_schema(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Extended schema: extra-поля сериализуются в ту же JSON-строку (D1)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · DevPlan 116 B11 T2 (D1) — extended schema
    # · Scenario: write_audit_entry(..., operation=..., project=..., channel=..., result=..., duration_s=..., snapshot_id=...)
    # · Last fail: N/A (new schema)
    # · Remove if: audit schema superseded

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry(
        "deploy:deploy",
        "DEPLOYED",
        "deploy project=myproj channel=scp",
        log_file=str(log_file),
        operation="deploy",
        project="myproj",
        channel="scp",
        result="DEPLOYED",
        duration_s=5.25,
        snapshot_id="snap-001",
    )

    entries = read_audit_log(log_file=str(log_file))
    assert len(entries) == 1
    entry = entries[0]
    # Base schema
    assert entry["tag"] == "deploy:deploy"
    assert entry["status"] == "DEPLOYED"
    assert "ts" in entry and "msg" in entry
    # Extended schema (D1)
    assert entry["operation"] == "deploy"
    assert entry["project"] == "myproj"
    assert entry["channel"] == "scp"
    assert entry["result"] == "DEPLOYED"
    assert entry["duration_s"] == 5.25
    assert entry["snapshot_id"] == "snap-001"
    logger.info("[IMP:9][test][extended-schema] ✅ extra-поля сериализованы в единую JSON-строку")


# endregion


# region FUNC_test_write_entry_backward_compat
## @purpose — Базовая схема без extra: ts/tag/status/msg + source (W10 T10.5).
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_entry_backward_compat(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Backward-compat: без extra — базовая схема + source (W10 T10.5 атрибуция)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · backward-compat без extra
    # · Scenario: старый вызов write_audit_entry(tag, status, msg) — базовая схема стабильна
    # · Last fail: 2026-08-05 — W10 T10.5 добавил source-поле (атрибуция записи)
    # · Remove if: audit schema superseded

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry("context_deploy:proj", "DEPLOYED", "legacy call", log_file=str(log_file))

    entries = read_audit_log(log_file=str(log_file))
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry.keys()) == {"ts", "tag", "status", "msg", "source"}, (
        f"Schema drift: unexpected keys {set(entry.keys()) - {'ts', 'tag', 'status', 'msg', 'source'}}"
    )
    # W10 T10.5: source-поле = {uid, proc} — атрибуция каждой записи
    assert isinstance(entry["source"], dict)
    assert "uid" in entry["source"] and "proc" in entry["source"], f"source schema broken: {entry['source']}"
    logger.info("[IMP:9][test][backward-compat] ✅ базовый вызов — 5 ключей (с source)")


# endregion


# region FUNC_test_write_entry_source_field
## @purpose — W10 T10.5 (S-15): source-поле в КАЖДОЙ записи — {uid, proc}.
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_entry_source_field(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T10.5: source {uid, proc} присутствует и non-empty."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.5 — запись без атрибуции
    # · Scenario: убрать source — нельзя отличить запись CI-деплоя от ручной
    # · Last fail: 2026-08-05 — W10: source добавлен в схему
    # · Remove if: audit schema superseded

    log_file = tmp_path / "audit.jsonl"
    ok = write_audit_entry("test:src", "OK", "with source", log_file=str(log_file))
    assert ok is True, "write_audit_entry должен возвращать True при успехе (T10.5)"

    entry = read_audit_log(log_file=str(log_file))[0]
    assert entry["source"]["uid"] is not None
    assert entry["source"]["proc"], f"proc пуст: {entry['source']}"
    logger.info("[IMP:9][test][source] ✅ source={%s}", entry["source"])


# endregion


# region FUNC_test_write_raises_on_oserror_when_raise_on_error
## @purpose — W10 T10.5 (S-6): raise_on_error=True → OSError пробрасывается (CLI exit≠0).
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_raises_on_oserror_when_raise_on_error(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T10.5: raise_on_error=True → OSError propagates (fail, не silent-drop)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.5 — audit write failure
    # · Scenario: журнал недоступен (dir как файл) — CLI должен УПАСТЬ, не потерять запись молча
    # · Last fail: 2026-08-05 — W10: OSError логировался WARNING и молча дропал запись
    # · Remove if: raise_on_error контракт изменён

    blocker = tmp_path / "blocker"
    blocker.write_text("dir placeholder")
    log_file = blocker / "audit.jsonl"  # parent — файл, не директория → OSError при makedirs/write

    with pytest.raises(OSError):
        write_audit_entry("test:fail", "FAILED", "cannot write", log_file=str(log_file), raise_on_error=True)
    logger.info("[IMP:9][test][raise_on_error] ✅ OSError проброшен при raise_on_error=True")


# endregion


# region FUNC_test_write_returns_false_on_oserror_default
## @purpose — W10 T10.5: по умолчанию (raise_on_error=False) OSError → False (non-raising — W9 failure-пути).
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_returns_false_on_oserror_default(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T10.5: default (False) → OSError возвращает False, НЕ бросает (W9-совместимость)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · REGRESSION · DevPlan 136 W10 T10.5 + W9 failure-пути
    # · Scenario: audit в except/finally (W9) не должен маскировать оригинальное исключение
    # · Last fail: 2026-08-05 — W10: контракт возврата изменён None → bool
    # · Remove if: raise_on_error контракт изменён

    blocker = tmp_path / "blocker"
    blocker.write_text("dir placeholder")
    log_file = blocker / "audit.jsonl"

    ok = write_audit_entry("test:fail", "FAILED", "cannot write", log_file=str(log_file))
    assert ok is False, "default: OSError → False (non-raising)"
    logger.info("[IMP:9][test][default-false] ✅ OSError → False без проброса")


# endregion


# region FUNC_test_read_alerts_on_malformed_json
## @purpose — W10 T10.5 (S-15): malformed JSON в read → ALERT-лог (ERROR, [IMP:9][audit][ALERT]).
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_read_alerts_on_malformed_json(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T10.5: read пропускает malformed-строку и ПОДНИМАЕТ ALERT (тампер/порча видимы)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.5 — тампер аудит-журнала
    # · Scenario: злоумышленник/битый fs пишет мусорную строку — раньше молча пропускалась
    # · Last fail: 2026-08-05 — W10: malformed логировался WARNING IMP:7 (невидим в L5)
    # · Remove if: read контракт изменён

    log_file = tmp_path / "audit.jsonl"
    log_file.write_text('{"ts":"t1","tag":"ok","status":"OK","msg":"good"}\nTHIS-IS-NOT-JSON\n')
    with caplog.at_level(logging.ERROR, logger="core.internal.shared.audit_logger"):
        entries = read_audit_log(log_file=str(log_file))
    assert len(entries) == 1, "valid entries должны возвращаться"
    alerts = [r.message for r in caplog.records if "[audit][ALERT]" in r.message]
    assert alerts, "ALERT-лог на malformed JSON обязателен (T10.5)"
    logger.info("[IMP:9][test][malformed] ✅ ALERT поднят: %s", alerts[0][:80])


# endregion


# region FUNC_test_write_entry_multi_project
## @purpose — Мульти-проектная запись (log_many-эквивалент) через extra: projects list.
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_entry_multi_project(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Multi-project запись: projects/per_project_results сериализуются (list → JSON)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · multi-project entry (ex-log_many, D1)
    # · Scenario: write_audit_entry(..., projects=[...], per_project_results=[...])
    # · Last fail: N/A
    # · Remove if: audit schema superseded

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry(
        "deploy:deploy_many",
        "PARTIAL",
        "deploy_many 2 project(s)",
        log_file=str(log_file),
        operation="deploy_many",
        projects=["proj-a", "proj-b"],
        project_count=2,
        per_project_results=["DEPLOYED", "FAILED"],
    )

    entries = read_audit_log(log_file=str(log_file))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["projects"] == ["proj-a", "proj-b"]
    assert entry["per_project_results"] == ["DEPLOYED", "FAILED"]
    assert entry["project_count"] == 2
    logger.info("[IMP:9][test][multi-project] ✅ projects/per_project_results — JSON-совместимы")


# endregion


# region FUNC_test_write_entry_permissions
## @purpose — Пермишены: после первой записи файл имеет mode 640 (консолидация из deploy/audit_logger.py, D1).
## @io — ⇥ caplog, tmp_path → ⎋ None
## @complexity — O(1)
def test_write_entry_permissions(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Permissions: chmod 640 после первой записи (D1, consolidated)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · permissions 640 (D1)
    # · Scenario: первый write → os.chmod 0o640
    # · Last fail: N/A
    # · Remove if: permissions policy changed

    log_file = tmp_path / "audit.jsonl"
    write_audit_entry("test:perm", "OK", "permissions check", log_file=str(log_file))
    mode = log_file.stat().st_mode & 0o777
    assert mode == 0o640, f"Expected mode 640, got {oct(mode)}"
    logger.info("[IMP:9][test][permissions] ✅ audit.jsonl mode = 640")


# endregion
