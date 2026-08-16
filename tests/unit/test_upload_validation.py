# GREP_SUMMARY: test-upload-validation cli-inputs s3-env spool-rm exit-2 config-error DevPlan-118-E9
# STRUCTURE: ┌tmp files + env fixtures┐ → ◇ validate_cli_inputs (empty-file / missing-file / missing-creds / ok) → ◇ remove_spool_file (removes / missing best-effort) → ⎋ exit-2 assertions + LDD
# region MODULE_CONTRACT
## @purpose  Unit tests for upload.py validation + spool cleanup (DevPlan 118 E9 — merge upload-s3.sh
##           validation into upload.py). Native import via sys.path.insert (module-specific path).
## @scope    Tests: validate_cli_inputs exit-2 paths (empty local_file, empty s3_key, missing file,
##           missing S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY), success path, remove_spool_file
##           (removal + best-effort on missing file).
## @invariants
##   - sys.path.insert for core/modules/backup-cron/scripts (module-specific, tests/AGENTS.md)
##   - No boto3 client creation — only validation/cleanup functions (no network)
##   - Exit-2 paths asserted via pytest.raises(SystemExit)
## @rationale E9 Strangler: upload-s3.sh валидация (84 LOC) merged в upload.py — тестируема без S3.
## @changes  2026-08-02 | DevPlan 118 E9 — Created
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"))
import upload

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region TEST_validate_cli_inputs
# Полный S3-env дикт для валидного сценария (DI — DevPlan 160 E2, env= параметр)
_S3_ENV = {"S3_BUCKET": "b", "S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "s"}


def test_validate_empty_local_file_exits_2(caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_empty_local_file_exits_2 — DevPlan 118 E migration unit test
    """validate_cli_inputs: empty local_file → SystemExit(2) (config error)."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc:
        upload.validate_cli_inputs("", "key", env=_S3_ENV)
    assert exc.value.code == 2
    assert any("No local file" in r.message for r in caplog.records)


def test_validate_empty_s3_key_exits_2(caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_empty_s3_key_exits_2 — DevPlan 118 E migration unit test
    """validate_cli_inputs: empty s3_key → SystemExit(2)."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc:
        upload.validate_cli_inputs("/tmp/somefile", "", env=_S3_ENV)
    assert exc.value.code == 2
    assert any("No S3 key" in r.message for r in caplog.records)


def test_validate_missing_file_exits_2(caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_missing_file_exits_2 — DevPlan 118 E migration unit test
    """validate_cli_inputs: local file does not exist → SystemExit(2)."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc:
        upload.validate_cli_inputs("/nonexistent/backup.sql.gz", "key", env=_S3_ENV)
    assert exc.value.code == 2
    assert any("not found" in r.message for r in caplog.records)


@pytest.mark.parametrize("missing_var", ["S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_validate_missing_s3_env_exits_2(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_missing_s3_env_exits_2 — DevPlan 118 E migration unit test
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    missing_var: str,
) -> None:
    """validate_cli_inputs: each missing S3_* env → SystemExit(2) (wrapper contract)."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"data")
    env = {k: v for k, v in _S3_ENV.items() if k != missing_var}  # отсутствие ключа = unset var (DI)

    with pytest.raises(SystemExit) as exc:
        upload.validate_cli_inputs(str(f), "key", env=env)
    assert exc.value.code == 2, f"{missing_var} must exit 2"
    assert any(missing_var in r.message for r in caplog.records)


def test_validate_ok_passes(caplog, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ok_passes — DevPlan 118 E migration unit test
    """validate_cli_inputs: valid file + full S3 env → no exit (returns None)."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"data")

    upload.validate_cli_inputs(str(f), "key", env=_S3_ENV)  # must not raise
    assert any("[IMP:9]" in r.message and "validated" in r.message for r in caplog.records), (
        "IMP:9 validation success log expected"
    )


# endregion TEST_validate_cli_inputs


# region TEST_remove_spool_file
def test_remove_spool_file_removes(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_remove_spool_file_removes — DevPlan 118 E migration unit test
    """remove_spool_file: existing file → removed + IMP:8 log."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"data")

    upload.remove_spool_file(str(f))
    assert not f.exists(), "spool file must be removed after successful upload"
    assert any("Removed spool file" in r.message for r in caplog.records)


def test_remove_spool_file_missing_best_effort(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_remove_spool_file_missing_best_effort — DevPlan 118 E migration unit test
    """remove_spool_file: missing file → best-effort warning, no raise (post-success cleanup)."""
    caplog.set_level(logging.WARNING)
    upload.remove_spool_file(str(tmp_path / "never-existed.sql.gz"))  # must not raise
    found_warn = any("Failed to remove spool file" in r.message for r in caplog.records)
    assert found_warn, "IMP:8 warning expected on missing spool file"


# endregion TEST_remove_spool_file


# region TEST_main_integration_validation_gate
def test_main_validate_gate_before_upload(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_main_validate_gate_before_upload — DevPlan 118 E migration unit test
    """main(): missing S3_BUCKET → exit 2 BEFORE any upload attempt (validate_cli_inputs gate)."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"data")
    # env= {} — пустой env-дикт = все S3_* отсутствуют (DI); argv — CLI-аргументы без prog (DI, AF-4)
    with pytest.raises(SystemExit) as exc:
        upload.main(env={}, argv=[str(f), "key"])
    assert exc.value.code == 2
    assert any("S3_BUCKET" in r.message for r in caplog.records), "S3_BUCKET gate must fire before upload"


# endregion TEST_main_integration_validation_gate


# region TEST_upload_verify_split (E8 R5)
def test_upload_verify_split_negative(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · R5 · E8 split — upload+verify work as composition
    """E8 R5: _upload_and_verify = _upload → _verify composition (phase split preserved)."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x" * 1024)

    fake_client = object()
    calls = {"upload": 0, "verify": 0}

    def _fake_upload(client, bucket, local_file, full_key, local_sha256, max_retries, interval_sec):
        calls["upload"] += 1
        return True

    def _fake_verify(client, bucket, local_file, full_key, local_sha256):
        calls["verify"] += 1
        return True

    result = upload._upload_and_verify(
        fake_client,
        "bucket",
        str(f),
        "backups/key",
        "deadbeef",
        max_retries=3,
        interval_sec=1,
        upload_fn=_fake_upload,
        verify_fn=_fake_verify,
    )
    assert result is True
    assert calls["upload"] == 1, "_upload must be called exactly once"
    assert calls["verify"] == 1, "_verify must be called exactly once"
    assert upload._upload is not None and upload._verify is not None


def test_upload_verify_split_upload_fail_shortcircuits(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · R5 · E8 split — upload failure short-circuits verify
    """E8 R5: _upload False → _verify NOT called (composition short-circuit)."""
    caplog.set_level(logging.INFO)
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x")

    fake_client = object()
    calls = {"upload": 0, "verify": 0}

    def _fake_upload(client, bucket, local_file, full_key, local_sha256, max_retries, interval_sec):
        calls["upload"] += 1
        return False

    def _fake_verify(client, bucket, local_file, full_key, local_sha256):
        calls["verify"] += 1
        return True

    result = upload._upload_and_verify(
        fake_client,
        "bucket",
        str(f),
        "backups/key",
        "deadbeef",
        max_retries=3,
        interval_sec=1,
        upload_fn=_fake_upload,
        verify_fn=_fake_verify,
    )
    assert result is False
    assert calls["upload"] == 1
    assert calls["verify"] == 0, "verify must NOT run when upload fails (short-circuit)"
    logger.critical("[IMP:9][test] upload_verify_split short-circuit verified")


# endregion TEST_upload_verify_split (E8 R5)
