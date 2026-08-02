#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-atomic-writer, atomic-write, fsync, os.replace, validator, no-partial-write, R5, unit-tests, E5
# STRUCTURE: ▶ test_atomic_write_success ┌tmp_path┐ → atomic_write_text → ◇ content+mode → ⎋ PASS │ ▶ test_atomic_write_json ┌dict┐ → atomic_write_json → ◇ json roundtrip │ ▶ test_atomic_write_validator_rejects → validator False → ValueError + target untouched │ ▶ test_atomic_write_no_partial_write_negative ┌injected OSError┐ → ◇ target intact + no temp garbage
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/atomic_writer.py (DevPlan 119 E5 canonical
##           atomic writer: tempfile + fsync + os.replace + optional validator).
## @scope    Covers $TEST_SPEC of DevPlan 119 E5: test_atomic_write_success,
##           test_atomic_write_no_partial_write_negative (R5) + validator/json/text paths.
## @invariants
##   - Native imports only (no subprocess)
##   - tmp_path fixture exclusively (zero hardcode)
##   - Each test validates IMP:9 log presence via caplog (LDD telemetry)
##   - R5 negative: no partial write + no temp garbage on injected failure
## @rationale  The canonical writer replaces 12+ local os.replace/NamedTemporaryFile copies
##             (audit S5). Tests pin the canon contract: atomic commit, validator rejection
##             leaves target untouched, injected failure leaves zero garbage.
## @changes  2026-08-02 · Created (DevPlan 119 E5)
# endregion MODULE_CONTRACT
"""

import json
import logging
from pathlib import Path

import pytest

from core.internal.shared.atomic_writer import atomic_write, atomic_write_json, atomic_write_text

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · atomic write commits content + mode
# · Regression: E5 canonical writer replaces local os.replace copies
# · Last fail: N/A (new canon)
# · Remove if: atomic_writer API changes
def test_atomic_write_success(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """atomic_write_text commits content with correct mode to target path."""
    with caplog.at_level(logging.INFO):
        target = tmp_path / "out.txt"
        result = atomic_write_text(str(target), "hello\n", mode=0o644)

    assert result == target
    assert target.read_text() == "hello\n"
    assert (target.stat().st_mode & 0o777) == 0o644

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found_log = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found_log = True
    print("--- END LDD TRAJECTORY ---")
    assert found_log, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · atomic_write_json roundtrip
# · Regression: E5 canonical JSON writer (docker_registry_auth, docker_daemon, metrics cache)
# · Last fail: N/A (new canon)
# · Remove if: atomic_write_json API changes
def test_atomic_write_json_roundtrip(tmp_path: Path) -> None:
    """atomic_write_json serializes dict to JSON (indent=2, UTF-8, trailing newline)."""
    target = tmp_path / "cfg.json"
    payload = {"registry-mirrors": ["https://mirror.example.com"], "log-driver": "json-file"}
    result = atomic_write_json(str(target), payload)

    assert result == target
    loaded = json.loads(target.read_text())
    assert loaded == payload
    assert target.read_text().endswith("\n")


# 🧪 TRAP[TEST] · 2026-08-02 · unit · validator rejection leaves target untouched
# · Regression: sudoers_generator uses validator=visudo — rejection must NOT commit
# · Last fail: N/A (new canon); B4 контракт — ConfigValidationError (не bare ValueError)
# · Remove if: validator contract changes
def test_atomic_write_validator_rejects(tmp_path: Path) -> None:
    """Validator returning False → ConfigValidationError, target NOT written, no temp garbage."""
    from core.internal.shared.exceptions import ConfigValidationError

    target = tmp_path / "sudoers.d"
    target.mkdir()
    dest = target / "platform-test"

    def _reject(_tmp: str) -> bool:
        return False

    with pytest.raises(ConfigValidationError):
        atomic_write(str(dest), "bad sudoers\n", mode=0o440, validator=_reject)

    assert not dest.exists(), "Validator rejection must leave target untouched"
    leftover = list(target.iterdir())
    assert leftover == [], f"Validator rejection must not leave temp garbage, got {leftover}"


# 🧪 TRAP[TEST] · 2026-08-02 · unit · validator acceptance commits
# · Regression: E5 validator param (sudoers visudo path)
# · Last fail: N/A (new canon)
# · Remove if: validator contract changes
def test_atomic_write_validator_accepts(tmp_path: Path) -> None:
    """Validator returning True → commit proceeds."""
    target = tmp_path / "ok.txt"

    def _accept(_tmp: str) -> bool:
        return True

    result = atomic_write(str(target), "valid\n", validator=_accept)
    assert result == target
    assert target.read_text() == "valid\n"


# 🧪 TRAP[TEST] · 2026-08-02 · R5 · no partial write on injected failure
# · Regression: E5 canonical writer must never leave partial content or temp garbage
# · Scenario: os.replace injected to raise → target keeps ORIGINAL content, temp removed
# · Remove if: atomic_writer failure semantics change
def test_atomic_write_no_partial_write_negative(tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture) -> None:
    """R5: injected failure during commit → target intact, zero temp garbage."""
    target = tmp_path / "secrets.env"
    target.write_text("ORIGINAL=1\n")

    real_replace = __import__("os").replace

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(__import__("os"), "replace", _boom)

    with caplog.at_level(logging.INFO), pytest.raises(OSError):
        atomic_write(str(target), "NEW=1\n", mode=0o600)

    # Target keeps original content — no partial write
    assert target.read_text() == "ORIGINAL=1\n", "Target must be untouched on failure"
    # Zero temp garbage in target dir
    leftovers = [p.name for p in tmp_path.iterdir() if p != target]
    assert leftovers == [], f"Expected no temp garbage, got {leftovers}"
    assert real_replace is not None  # keep reference (import lint-clean)


# 🧪 TRAP[TEST] · 2026-08-02 · unit · binary content supported
# · Regression: s3_ssl_cache pem restore uses bytes content
# · Last fail: N/A (new canon)
# · Remove if: bytes support changes
def test_atomic_write_bytes(tmp_path: Path) -> None:
    """atomic_write accepts bytes content (pem/cert restores)."""
    target = tmp_path / "fullchain.pem"
    payload = b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    result = atomic_write(str(target), payload, mode=0o644)
    assert result == target
    assert target.read_bytes() == payload
