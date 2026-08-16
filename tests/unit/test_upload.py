# GREP_SUMMARY: test upload s3-boto3 fake DI effect spool retry ClientError permanent-error FileNotFound upload_with_retries exit-code
# STRUCTURE: fixtures(fake_s3) → test_create_s3_client_config → test_upload_file_success → test_upload_file_not_found → test_upload_file_boto_error → test_perm_error_403_failfast → test_transient_500_retries → test_retry_exhausted → test_retry_succeeds → test_upload_verify → test_main_success → test_main_verify_fail_exit1
# region MODULE_CONTRACT
## @purpose  Unit tests for upload.py — S3 upload with retry logic. DevPlan 139 W2:
##           проверяем ЭФФЕКТ (файл в spool, exit code, retry-поведение, ответ публичного API),
##           а НЕ внутренности мока (fake._uploads/_call_count удалены из утверждений).
## @scope    FakeS3Client — DI-контракт (сигнатура boto3 upload_file/head_object), НЕ объект
##           утверждений; никаких реальных S3-соединений; no unittest.mock для boto3-логики.
## @invariants
##   - ВСЕ тесты: @ldd_trajectory + IMP:9-лог (LDD telemetry, DevPlan 139 инвариант 3)
##   - tmp_path для локальных файлов (Zero Hardcode)
##   - Эффекты: spool-файл НЕ удаляется при неуспехе (TRAP[BUSINESS] spool-never-delete),
##     удаляется только при успешном main() (remove_spool_file); retry-поведение — через
##     caplog-события (WARNING+[IMP:8]+Retry), НЕ счётчики мока
##   - Exit-коды: main() → 0 (успех + spool rm), 1 (verify fail, файл в spool), 2 (config)
## @rationale upload.py (817 LOC, 3 retries × 30min) критичен для надёжности бэкапов; fake-based
##   тестирование покрывает ветвления без 90-минутного ожидания или реального S3. Мок как
##   DI-контракт (сигнатура) + утверждения на наблюдаемый эффект (не внутреннее состояние).
## @changes 2026-07-08 | MagicMock→FakeS3Client (Wave 2.1)
## @changes 2026-08-05 | DevPlan 139 W2 — REWRITE: внутренности моков → эффекты (spool/exit/retry/API)
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

import pytest
from conftest import ldd_trajectory

# Module-specific path (tests/AGENTS.md §sys.path policy): core/modules/backup-cron/scripts.
# Абсолютный Path(__file__)-based (xdist-инвариант 4, DevPlan 139 W2) — относительные
# пути зависели бы от CWD воркера. Оригинальный файл НЕ имел этого пути — `import upload`
# падал ModuleNotFoundError (исправлено в рамках W2 REWRITE).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"))

logger = logging.getLogger(__name__)


# region FAKE_S3_CLIENT


class FakeS3Client:
    """Fake S3 client — DI-контракт (сигнатура boto3 upload_file/head_object), без сети.

    DevPlan 139 W2: внутренние списки загрузок (fake._uploads) и счётчики НЕ являются
    объектом утверждений. Fake сохраняет только поведенческий контракт:
    - upload_file(Filename, Bucket, Key, **kwargs) — при fail_on/fail_count эмулирует
      ClientError (fail_status), иначе КОПИРУЕТ локальный файл в бакет (эффект: объект
      виден через head_object — как boto3)
    - head_object(Bucket, Key) — отдаёт ContentLength/Metadata для загруженных объектов
    """

    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        fail_on: str | None = None,
        fail_count: int = 1,
        fail_status: int = 500,
        metadata: dict[str, dict] | None = None,
    ):
        self.objects = objects or {}
        self.fail_on = fail_on  # key substring, триггерящий ошибку
        self.fail_count = fail_count  # сколько раз фейлить до успеха
        self.fail_status = fail_status  # HTTP-статус эмулируемого ClientError
        self.metadata = metadata or {}  # object key → Metadata (boto3 ExtraArgs.Metadata контракт)
        self._attempts = 0  # внутренний механизм fail-симуляции (НЕ для утверждений)

    def upload_file(self, Filename: str, _Bucket: str, Key: str, **kwargs) -> None:
        from botocore.exceptions import ClientError

        self._attempts += 1
        if self.fail_on and self.fail_on in Key and self._attempts <= self.fail_count:
            raise ClientError(
                {
                    "Error": {"Code": str(self.fail_status), "Message": "Simulated error"},
                    "ResponseMetadata": {"HTTPStatusCode": self.fail_status},
                },
                "upload_file",
            )
        # Эффект (как boto3): локальный файл скопирован в бакет + ExtraArgs.Metadata сохранён
        data = Path(Filename).read_bytes()
        self.objects[Key] = data
        extra = kwargs.get("ExtraArgs") or {}
        md = extra.get("Metadata") or {}
        if md:
            self.metadata[Key] = md

    def head_object(self, Bucket: str, Key: str) -> dict:  # ruff: ignore[ARG002]
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "head_object",
            )
        return {"ContentLength": len(self.objects[Key]), "Metadata": self.metadata.get(Key, {})}


# endregion FAKE_S3_CLIENT


# region HELPERS


def _make_config(**overrides) -> dict:
    """Create a standard backup config dict, overridable."""
    config = {
        "endpoint_url": "https://s3.timeweb.cloud",
        "aws_access_key_id": "test-access-key",
        "aws_secret_access_key": "test-secret-key",
        "bucket": "test-bucket",
        "region": "us-east-1",
        "prefix": "platform/backups",
        "node_name": "test-node",
    }
    config.update(overrides)
    return config


def _write_file(tmp_path, name: str = "test_backup.sql.gz", data: str = "test data") -> str:
    """Создать локальный spool-файл в tmp_path (эффект: файл в spool)."""
    path = tmp_path / name
    with Path(path).open("w", encoding="utf-8") as f:
        f.write(data)
    return str(path)


def _assert_retry_warning(caplog, *, present: bool) -> None:
    """Структурная проверка retry-поведения: WARNING + [IMP:8] + 'Retry' (DevPlan 139 W2).

    ## @purpose — retry-поведение проверяется по caplog-событиям (severity+IMP+факт),
    ##            НЕ по внутренним счётчикам мока. present=True → ретрай был;
    ##            present=False → fail-fast (перманентная ошибка не ретраится).
    """
    found = any(
        r.levelno == logging.WARNING and "[IMP:8]" in r.message and "Retry" in r.message for r in caplog.records
    )
    assert found is present, (
        f"retry-warning {'не найден (ожидался)' if present else 'найден (не ожидался)'}\n---\n{caplog.text}"
    )


# endregion HELPERS


# region TESTS


@pytest.mark.static_audit
@ldd_trajectory
def test_create_s3_client_config(caplog) -> None:
    """create_s3_client constructs boto3 client with correct config.
    Uses unittest.mock.patch only for boto3 import — legitimate use (фабрика = публичный API).
    """
    from unittest.mock import patch as mock_patch

    with caplog.at_level(logging.DEBUG), mock_patch("upload.boto3") as mock_boto3:
        from upload import create_s3_client

        config = _make_config()
        create_s3_client(config)

        mock_boto3.client.assert_called_once()
        call_kwargs = mock_boto3.client.call_args[1]
        logger.critical(
            "[IMP:9][test_upload][create_client] ASSERT: endpoint=%s bucket=%s region=%s",
            call_kwargs.get("endpoint_url"),
            config["bucket"],
            call_kwargs.get("region_name"),
        )
        assert call_kwargs["endpoint_url"] == config["endpoint_url"]
        assert call_kwargs["aws_access_key_id"] == config["aws_access_key_id"]
        assert call_kwargs["aws_secret_access_key"] == config["aws_secret_access_key"]
        assert call_kwargs["region_name"] == config["region"]


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_success(tmp_path, caplog) -> None:
    """Эффект: upload_file возвращает (True, None), файл ОСТАЁТСЯ в spool, объект виден в S3."""
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client()

        from upload import upload_file

        local_path = _write_file(tmp_path)
        key = "platform/backups/test.sql.gz"

        result = upload_file(fake, "test-bucket", local_path, key)

        logger.critical("[IMP:9][test_upload][upload_success] ASSERT: result=%s exc=%s", result[0], result[1])
        assert result[0] is True
        assert result[1] is None
        # Эффект 1: spool-файл НЕ удаляется upload_file'ом (TRAP[BUSINESS] spool-never-delete)
        assert Path(local_path).is_file(), "upload_file НЕ должен удалять spool-файл"
        # Эффект 2: объект загружен в S3 (head_object видит размер)
        meta = fake.head_object("test-bucket", key)
        assert meta["ContentLength"] == len(b"test data"), "S3-объект должен отражать размер файла"


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_not_found(tmp_path, caplog) -> None:
    """Эффект: отсутствующий локальный файл → FileNotFoundError (fail-fast, публичный контракт)."""
    with caplog.at_level(logging.DEBUG):
        from upload import upload_file

        fake = FakeS3Client()
        missing_path = tmp_path / "nonexistent.sql.gz"

        with pytest.raises(FileNotFoundError, match="Local file not found"):
            upload_file(fake, "test-bucket", missing_path, "some/key")

        logger.critical("[IMP:9][test_upload][file_not_found] ASSERT: FileNotFoundError raised")


@pytest.mark.static_audit
@ldd_trajectory
def test_upload_file_boto_error(tmp_path, caplog) -> None:
    """Эффект: сбой S3 → (False, exc), файл остаётся в spool, объект НЕ загружен."""
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client(fail_on="some/key", fail_count=999)

        from upload import upload_file

        local_path = _write_file(tmp_path, "test.sql.gz", "data")
        key = "some/key"

        result = upload_file(fake, "test-bucket", local_path, key)

        logger.critical(
            "[IMP:9][test_upload][boto_error] ASSERT: result=%s exc=%s",
            result[0],
            type(result[1]).__name__ if result[1] else None,
        )
        assert result[0] is False
        assert result[1] is not None
        # Эффект: spool-файл сохранён (нет потери данных при сбое загрузки)
        assert Path(local_path).is_file(), "spool-файл должен сохраняться при сбое upload"
        # Эффект: объект НЕ появился в S3
        assert key not in fake.objects, "сбой upload не должен создавать S3-объект"


@pytest.mark.static_audit
@ldd_trajectory
def test_is_permanent_error_403(tmp_path, caplog) -> None:
    """Эффект: перманентная ошибка (403) → False на ПЕРВОЙ попытке, БЕЗ ретрая (fail-fast)."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=999, fail_status=403)
        local_path = _write_file(tmp_path, "test.sql.gz", "data")

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            "some/key",
            max_retries=3,
            interval_sec=0,
        )

        logger.critical("[IMP:9][test_upload][perm_403] ASSERT: result=%s (expected False)", result)
        assert result is False
        # Fail-fast: перманентная ошибка НЕ ретраится (нет retry-warning'ов)
        _assert_retry_warning(caplog, present=False)
        # Эффект: spool-файл сохранён
        assert Path(local_path).is_file()


@pytest.mark.static_audit
@ldd_trajectory
def test_is_permanent_error_500(tmp_path, caplog) -> None:
    """Эффект: транзиентная ошибка (500) → ретраи (retry-warning'и есть) → False после max_retries."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=999, fail_status=500)
        local_path = _write_file(tmp_path, "test.sql.gz", "data")

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            "some/key",
            max_retries=3,
            interval_sec=0,
        )

        logger.critical("[IMP:9][test_upload][transient_500] ASSERT: result=%s (expected False)", result)
        assert result is False
        # Ретрай-поведение: транзиентная ошибка ретраится (WARNING+[IMP:8]+Retry присутствует)
        _assert_retry_warning(caplog, present=True)
        # Эффект: spool-файл сохранён после исчерпания ретраев
        assert Path(local_path).is_file()


@pytest.mark.static_audit
@ldd_trajectory
def test_retry_exhausted(tmp_path, caplog) -> None:
    """Эффект: все ретраи исчерпаны → False, spool-файл НЕ удалён (нет потери данных)."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=999)
        local_path = _write_file(tmp_path, "test.sql.gz", "data")

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            "some/key",
            max_retries=3,
            interval_sec=0,
        )

        logger.critical("[IMP:9][test_upload][retry_exhausted] ASSERT: result=%s (expected False)", result)
        assert result is False
        # Ретрай-поведение: попытки были (WARNING+[IMP:8]+Retry)
        _assert_retry_warning(caplog, present=True)
        # Эффект: файл остаётся в spool — «file remains in spool» (бизнес-инвариант)
        assert Path(local_path).is_file(), "файл должен остаться в spool после исчерпания ретраев"


@pytest.mark.static_audit
@ldd_trajectory
def test_retry_succeeds_on_retry(tmp_path, caplog) -> None:
    """Эффект: 1-я попытка фейл, ретрай успешен → True, объект загружен, spool-файл сохранён."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        fake = FakeS3Client(fail_on="some/key", fail_count=1)
        local_path = _write_file(tmp_path, "test.sql.gz", "data")
        key = "some/key"

        result = upload_module.upload_with_retries(
            fake,
            "test-bucket",
            local_path,
            key,
            max_retries=3,
            interval_sec=0,
        )

        logger.critical("[IMP:9][test_upload][retry_succeeds] ASSERT: result=%s (expected True)", result)
        assert result is True
        # Ретрай-поведение: перед успехом была неудачная попытка (retry-warning)
        _assert_retry_warning(caplog, present=True)
        # Эффект: объект загружен в S3 с корректным размером
        meta = fake.head_object("test-bucket", key)
        assert meta["ContentLength"] == len(b"data"), "ретрай должен завершиться загрузкой объекта"
        # Эффект: upload_with_retries НЕ удаляет spool-файл (удаление — ответственность main)
        assert Path(local_path).is_file()


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_success(caplog) -> None:
    """Публичный API get_s3_metadata: размер + sha256 из head_object."""
    with caplog.at_level(logging.DEBUG):
        fake = FakeS3Client(
            objects={"some/key": b"x" * 12345},
            metadata={"some/key": {"sha256": "simulated-sha256-for-testing"}},
        )

        from upload import get_s3_metadata

        meta = get_s3_metadata(fake, "test-bucket", "some/key")

        logger.critical(
            "[IMP:9][test_upload][s3_meta] ASSERT: size=%s sha256=%s",
            meta["size"],
            meta["sha256"],
        )
        assert meta["size"] == 12345
        assert meta["sha256"] == "simulated-sha256-for-testing"


@pytest.mark.static_audit
@ldd_trajectory
def test_main_upload_success(tmp_path, caplog) -> None:
    """Эффект: upload+verify цепочка через публичный API — загрузка затем верификация размера."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        config = _make_config()
        full_key = f"{config['prefix']}/backups/test.sql.gz".replace("//", "/")
        local_file = _write_file(tmp_path, "fake.sql.gz", "x" * 1000)
        fake = FakeS3Client()
        local_sha256 = upload_module.compute_sha256(local_file)

        success = upload_module.upload_with_retries(fake, config["bucket"], local_file, full_key, sha256=local_sha256)
        assert success is True

        s3_meta = upload_module.get_s3_metadata(fake, config["bucket"], full_key)
        assert s3_meta["size"] == 1000, "размер S3-объекта должен совпадать с локальным файлом"
        assert s3_meta["sha256"] == local_sha256, "sha256-метаданные должны roundtrip через S3 (verify-контракт)"

        logger.critical("[IMP:9][test_upload][main_success] ASSERT: upload+verify pipeline OK")


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_client_error(caplog) -> None:
    """get_s3_metadata возвращает {size: None, sha256: None} с CRITICAL log на ClientError."""
    with caplog.at_level(logging.DEBUG):
        from upload import get_s3_metadata

        # FakeS3Client raises ClientError(404) для отсутствующих ключей
        fake = FakeS3Client(objects={})  # empty objects — head_object raises ClientError
        meta = get_s3_metadata(fake, "test-bucket", "nonexistent/key")

        logger.critical(
            "[IMP:9][test_upload][meta_client_error] ASSERT: size=%s sha256=%s (expected None)",
            meta["size"],
            meta["sha256"],
        )
        assert meta["size"] is None
        assert meta["sha256"] is None

        # Структурная проверка: CRITICAL + [IMP:9] + факт события "Cannot verify S3 object"
        assert any(
            r.levelno == logging.CRITICAL and "[IMP:9]" in r.message and "Cannot verify S3 object" in r.message
            for r in caplog.records
        ), f"нет CRITICAL-лога verify: {caplog.text}"


@pytest.mark.static_audit
@ldd_trajectory
def test_get_s3_metadata_unexpected_error(caplog) -> None:
    """get_s3_metadata propagates non-ClientError exceptions (no silent masking)."""
    with caplog.at_level(logging.DEBUG):
        from upload import get_s3_metadata

        class BrokenClient:
            """Fake client that raises a non-ClientError on head_object."""

            def head_object(self, Bucket: str, Key: str) -> dict:  # ruff: ignore[ARG002]
                msg = "Simulated network failure"
                raise ConnectionError(msg)

        with pytest.raises(ConnectionError, match="Simulated network failure"):
            get_s3_metadata(BrokenClient(), "test-bucket", "some/key")

        logger.critical("[IMP:9][test_upload][meta_unexpected] ASSERT: ConnectionError propagated")


# region TESTS_MAIN_PUBLIC_PATH (exit-code + spool-эффекты через main())


class _UploadOkVerifyFailClient:
    """DI-контракт: upload успешен, но verify (head_object) фейлит — S3-неконсистентность.

    Эмулирует реальный сценарий: загрузка прошла, но метаданные объекта недоступны
    (get_s3_metadata → {size: None} → verification failure → exit 1).
    """

    def upload_file(self, _Filename: str, _Bucket: str, _Key: str, **_kwargs) -> None:
        return None  # успех — объект «загружен», но head_object ниже фейлит

    def head_object(self, Bucket: str, Key: str) -> dict:  # ruff: ignore[ARG002]
        from botocore.exceptions import ClientError

        raise ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "head_object",
        )


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path, client, s3_key: str) -> tuple[str, dict]:
    """Подготовить запуск upload.main() через публичный путь; вернуть (local_file, main_kwargs).

    ## @purpose — exit-code-тесты идут через main(): argv/config_fn/client_factory передаются
    ##            DI-параметрами (DevPlan 167 D1 — setattr→DI), env S3_* через
    ##            setenv-фикстуру (НЕ setattr); никаких приватных _upload/_verify вызовов.
    ## @io — ⇥ monkeypatch, tmp_path, client, s3_key → ⎋ (local_file: str, main_kwargs: dict)
    """
    config = _make_config()
    local_file = _write_file(tmp_path, "fake.sql.gz", "x" * 1000)

    monkeypatch.setenv("S3_BUCKET", config["bucket"])
    monkeypatch.setenv("S3_ACCESS_KEY", config["aws_access_key_id"])
    monkeypatch.setenv("S3_SECRET_KEY", config["aws_secret_access_key"])

    return local_file, {
        "argv": [local_file, s3_key],
        "config_fn": lambda: config,
        "client_factory": lambda _cfg: client,
    }


@pytest.mark.static_audit
@ldd_trajectory
def test_main_success_removes_spool_file(tmp_path, caplog, monkeypatch) -> None:
    """Эффект: main() успех → exit 0 + spool-файл УДАЛЁН (remove_spool_file после успеха)."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        key = "test.sql.gz"
        config = _make_config()
        full_key = f"{config['prefix']}/{key}".replace("//", "/")
        fake = FakeS3Client()
        local_file, main_kwargs = _run_main(monkeypatch, tmp_path, fake, key)

        with pytest.raises(SystemExit) as exc_info:
            upload_module.main(**main_kwargs)

        logger.critical("[IMP:9][test_upload][main_success] ASSERT: exit=%s", exc_info.value.code)
        assert exc_info.value.code == 0, "успешный upload+verify должен завершиться exit 0"
        # Эффект: spool-файл удалён после подтверждённого успеха (upload-s3.sh контракт)
        assert not Path(local_file).exists(), "после успешного main() spool-файл должен быть удалён"
        # Эффект: объект загружен и верифицирован в S3
        assert fake.head_object(config["bucket"], full_key)["ContentLength"] == 1000


@pytest.mark.static_audit
@ldd_trajectory
def test_main_verification_fails_on_none_metadata(tmp_path, caplog, monkeypatch) -> None:
    """Эффект: verify-фейл (head_object недоступен) → exit 1 + spool-файл СОХРАНЁН."""
    with caplog.at_level(logging.DEBUG):
        import upload as upload_module

        local_file, main_kwargs = _run_main(
            monkeypatch, tmp_path, _UploadOkVerifyFailClient(), "test_verify_fail.sql.gz"
        )

        with pytest.raises(SystemExit) as exc_info:
            upload_module.main(**main_kwargs)

        logger.critical("[IMP:9][test_upload][verify_fail] ASSERT: exit=%s (expected 1)", exc_info.value.code)
        assert exc_info.value.code == 1, "verify-fail должен завершиться exit 1"
        # Эффект: файл остаётся в spool (нет потери данных при verification failure)
        assert Path(local_file).exists(), "файл должен остаться в spool при verify-fail"


# endregion TESTS_MAIN_PUBLIC_PATH

# endregion TESTS
