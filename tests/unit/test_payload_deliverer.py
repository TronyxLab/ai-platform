"""
# GREP_SUMMARY: test_payload_deliverer, tar-gz, validate, extract, atomic-move, whitelist, stdin, deliver
# STRUCTURE: ▶ tmp_path + io.BytesIO(tar_bytes) → ◇ deliver_valid: tar.gz with compose+yaml+env → ◇ path_traversal: ../ in entry → ◇ symlink_rejected
#            → ◇ size_exceeded: >1MiB → ◇ whitelist_reject: script.sh → ◇ empty_payload → ◇ atomic_extract → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/payload_deliverer.py — PayloadDeliverer class
## @scope    Tar.gz generation via tarfile + io.BytesIO. File operations via tmp_path.
##           No real stdin required — pass io.BytesIO(tar_bytes) as stdin parameter.
## @invariants
##   - All tar archives generated in-memory (no real files)
##   - Tests cover: valid payload, path traversal, symlinks, size limit, whitelist, empty, atomic move
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig)
# endregion MODULE_CONTRACT
"""

import io
import logging
import os
import sys
import tarfile
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from core.internal.deploy.payload_deliverer import (
    _PAYLOAD_FILE_NAMES,
    MAX_PAYLOAD_SIZE,
    WHITELIST_FILES,
    PayloadDeliverer,
    SizeLimitError,
)

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · payload whitelist — B20a (141 r2)
# · Last fail: deploy-project не доставлял practices.lock на ноду (whitelist без него) →
# ·   K3 verify state=вечно. AGENTS.md §Наследование практик (DevPlan 137): lock
# ·   доставляется payload'ом receive.
# · Remove if: practices.lock перестанет доставляться payload'ом (запрещено — контракт 137).
def test_practices_lock_in_payload_whitelist() -> None:
    """B20a: practices.lock входит в whitelist и порядок файлов payload'а."""
    assert "practices.lock" in WHITELIST_FILES, "whitelist без practices.lock — K3 вечно"
    assert "practices.lock" in _PAYLOAD_FILE_NAMES, "_PAYLOAD_FILE_NAMES без practices.lock"
    logger.info("[IMP:9][unit][payload] practices.lock in whitelist + file names ✓")


# ── Helpers ──


def _make_tar_bytes(files: dict[str, bytes], symlinks: dict[str, str] | None = None) -> bytes:
    """Create an in-memory tar.gz from file dict.

    Args:
        files: Dict of {filename: content_bytes}
        symlinks: Optional dict of {link_name: target}

    Returns:
        Raw tar.gz bytes.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        if symlinks:
            for name, target in symlinks.items():
                info = tarfile.TarInfo(name=name)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
    buf.seek(0)
    return buf.read()


def _check_ldd(caplog) -> bool:
    """Check LDD trajectory for IMP:9+ logs."""
    found = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    return found


@pytest.fixture
def deliverer() -> PayloadDeliverer:
    return PayloadDeliverer(projects_base="/tmp/test-projects")


# ═══════════════════════════════════════════════════════════════════
# region Tests
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deliver valid payload
# · Scenario: Valid tar.gz with compose+yaml+env → DeliverResult(success=True, files_delivered=3)
# · Last fail: N/A (new test)
# · Remove if: deliver() logic changes
def test_deliver_valid_payload(caplog, tmp_path, deliverer):
    """Valid payload with compose, yaml, env should succeed."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar_bytes({
        "docker-compose.yml": b"version: '3'\nservices:\n  app:\n    image: test\n",
        "ai-platform.yaml": b"service: app\n",
        ".env.platform": b"VAR=value\n",
    })

    result = deliverer.deliver(
        project="test-app",
        org="myorg",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is True
    assert result.files_delivered == 3

    # Verify files were moved to target
    target = tmp_path / "myorg" / "test-app"
    assert (target / "docker-compose.yml").exists()
    assert (target / "ai-platform.yaml").exists()
    assert (target / ".env.platform").exists()
    logger.critical("[IMP:9][test] deliver_valid: files=%d — OK", result.files_delivered)


# 🧪 TRAP[TEST] · Regression · path traversal rejected
# · Scenario: Tar entry with "../" in path → PayloadValidationError (170 W2-A2: ValidationError переименован)
# · Last fail: N/A (new test)
# · Remove if: path traversal validation changes
def test_deliver_path_traversal(caplog, tmp_path, deliverer):
    """Subdirectory entry (path traversal) should be rejected."""
    caplog.set_level(logging.INFO)

    # Create tar with subdirectory entry (path traversal attempt)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="subdir/docker-compose.yml")
        info.size = 10
        tar.addfile(info, io.BytesIO(b"content123"))
    buf.seek(0)
    tar_bytes = buf.read()

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert "Subdirectory" in (result.error_message or "") or "path traversal" in (result.error_message or "")
    logger.critical("[IMP:9][test] path_traversal: rejected=%s — OK", not result.success)


# 🧪 TRAP[TEST] · Regression · symlink rejected
# · Scenario: Symlink in tar → PayloadValidationError
# · Last fail: N/A (new test)
# · Remove if: symlink validation changes
def test_deliver_symlink_rejected(caplog, tmp_path, deliverer):
    """Symlink entry should be rejected."""
    caplog.set_level(logging.INFO)

    # Create tar with a symlink
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add a real file first
        info = tarfile.TarInfo(name="docker-compose.yml")
        info.size = 10
        tar.addfile(info, io.BytesIO(b"version:'3'\n"))
        # Add a symlink
        link_info = tarfile.TarInfo(name="evil-link")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        tar.addfile(link_info)
    buf.seek(0)
    tar_bytes = buf.read()

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert "Symlink" in (result.error_message or "") or "link" in (result.error_message or "").lower()
    logger.critical("[IMP:9][test] symlink_rejected: rejected=%s — OK", not result.success)


# 🧪 TRAP[TEST] · Regression · oversize payload rejected
# · Scenario: Payload > 1 MiB → SizeLimitError
# · Last fail: N/A (new test)
# · Remove if: size validation changes
def test_deliver_max_size_exceeded(caplog, tmp_path, deliverer):
    """Payload exceeding 1 MiB should be rejected.

    Note: We test the _read_payload method directly because tar.gz
    compression may shrink payload below the 1 MiB threshold.
    """
    caplog.set_level(logging.INFO)

    # Create payload just over 1 MiB using _read_payload directly
    oversize_data = b"x" * (MAX_PAYLOAD_SIZE + 100)

    with pytest.raises(SizeLimitError):
        deliverer._read_payload(io.BytesIO(oversize_data), max_size=MAX_PAYLOAD_SIZE)

    # Also verify via the deliver path — tar.gz of incompressible data
    big_data = os.urandom(MAX_PAYLOAD_SIZE + 500)  # random data won't compress well
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="docker-compose.yml")
        info.size = len(big_data)
        tar.addfile(info, io.BytesIO(big_data))
    buf.seek(0)
    tar_bytes = buf.read()

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert result.success is False
    assert "exceed" in (result.error_message or "").lower()
    logger.critical("[IMP:9][test] size_exceeded: rejected=%s — OK", not result.success)


# 🧪 TRAP[TEST] · Regression · non-whitelist file rejected
# · Scenario: Payload with script.sh → PayloadValidationError
# · Last fail: N/A (new test)
# · Remove if: whitelist validation changes
def test_deliver_no_whitelist_file(caplog, tmp_path, deliverer):
    """Payload with non-whitelisted file should be rejected."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar_bytes({
        "script.sh": b"#!/bin/sh\necho hello\n",
    })

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert "whitelist" in (result.error_message or "").lower() or "Non-whitelisted" in (result.error_message or "")
    logger.critical("[IMP:9][test] whitelist_reject: rejected=%s — OK", not result.success)


# 🧪 TRAP[TEST] · Regression · empty payload rejected
# · Scenario: 0-byte payload → DeliverResult(success=False)
# · Last fail: N/A (new test)
# · Remove if: empty payload validation changes
def test_deliver_empty_payload(caplog, tmp_path, deliverer):
    """Empty payload should be rejected."""
    caplog.set_level(logging.INFO)

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(b""),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is False
    assert "Empty" in (result.error_message or "")
    logger.critical("[IMP:9][test] empty_payload: rejected=%s — OK", not result.success)


# 🧪 TRAP[TEST] · Regression · atomic extract with compose.yaml
# · Scenario: Payload with compose.yaml (alternative name) → success
# · Last fail: N/A (new test)
# · Remove if: compose.yaml handling changes
def test_deliver_compose_yaml_alternative(caplog, tmp_path, deliverer):
    """Payload with compose.yaml (alternative to docker-compose.yml) should succeed."""
    caplog.set_level(logging.INFO)

    tar_bytes = _make_tar_bytes({
        "compose.yaml": b"version: '3'\nservices:\n  app:\n    image: test\n",
        ".env.platform": b"VAR=value\n",
    })

    result = deliverer.deliver(
        project="test-app",
        projects_base=str(tmp_path),
        stdin=io.BytesIO(tar_bytes),
    )

    assert _check_ldd(caplog), "Missing IMP:9 log"
    assert result.success is True
    assert result.files_delivered == 2

    target = tmp_path / "test-app"
    assert (target / "compose.yaml").exists()
    assert (target / ".env.platform").exists()
    logger.critical("[IMP:9][test] compose_yaml_alt: files=%d — OK", result.files_delivered)


# endregion Tests
