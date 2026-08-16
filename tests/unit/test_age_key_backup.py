# GREP_SUMMARY: test-age-key-backup, DR, sops-mock, s3-mock, sha256, dry-run, no-secrets-stdout, exit-codes, unit
# STRUCTURE: ▶ fake S3 client + sops subprocess mock → ◇ full pipeline (encrypt→upload→sha256) → ◇ dry-run (0 мутаций) → ◇ no-upload → ◇ no-secrets stdout → ◇ exit-codes (10/2) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/age_key_backup.py (DevPlan 147 W2) — the
##           off-node encrypted backup CLI of the AGE master key (dr.md §2). Covers:
##           encrypt→upload→sha256-verify pipeline (mocked sops/S3), dry-run zero-mutation,
##           --no-upload encrypt-only, absence of secrets in stdout/caplog, exit-code contract.
## @scope    tests/unit — native imports only, NO subprocess for business logic (sops/S3 mocked).
##           No Docker, no network, no real key material.
## @invariants
##   - Native imports + unittest.mock.patch (patrón test_deploy_orchestrator.py)
##   - tmp_path fixture for --output-enc (Zero Hardcode Rule)
##   - LDD caplog: set_level(logging.DEBUG) + ≥1 [IMP:9] log asserted (Anti-Illusion)
##   - Test Honesty R1/R2: real asserts on pipeline/exit-code properties (no pass-tests)
##   - Fake S3 client records put_object/get_object calls — verifies ACL=private + sha256 metadata
## @rationale DevPlan 147 W2: unit-verification of the DR automation without a node/S3/sops
##           (make age-key-backup --dry-run — операторская проверка на окне W3.1).
## @changes  2026-08-11 | DevPlan 147 W2 — Created
# endregion MODULE_CONTRACT

import io
import logging
from types import SimpleNamespace

import pytest

from core.internal.deploy import age_key_backup as akb

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── Тестовый ключ (формат-валидный AGE-SECRET-KEY-, но НЕ реальный материал) ──
_FAKE_KEY = "AGE-SECRET-KEY-1QXX0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0"
_FAKE_RECIPIENT = "age1qwertyuiopasdfghjklzxcvbnm1234567890"
_FAKE_ENC = "age-encrypted-container-ENCRYPTED:QG1hc3Rlci1rZXk="


# region CLASS_FakeS3Client
class FakeS3Client:
    """In-memory S3 client double — records calls, serves get_object after put_object.

    ## @purpose — Deterministic S3 mock: put_object stores Body by Key; get_object
    ##            returns the stored bytes (readable Body). Verifiable via .calls.
    ## @complexity — O(1) per op
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(("put_object", kwargs))
        self.objects[str(kwargs["Key"])] = kwargs["Body"]  # type: ignore[assignment]

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", kwargs))
        body = self.objects[str(kwargs["Key"])]
        return {"Body": io.BytesIO(body)}


# endregion CLASS_FakeS3Client


# region HELPER_assert_ldd_imp9
def _assert_ldd_imp9(caplog) -> None:
    """Print LDD trajectory (IMP:7-10) and assert ≥1 IMP:9 log present (Anti-Illusion)."""
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if hasattr(record, "message") and "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion HELPER_assert_ldd_imp9


# region FIXTURE_sops_ok
@pytest.fixture()
def sops_ok() -> tuple:
    """DI (W-H DevPlan 163): (which_fn, run_cmd) — передаются в run_backup/encrypt_age_key (0 патчей)."""

    def _fake_run(cmd: list[str], *args: object, **kwargs: object) -> SimpleNamespace:
        if cmd and cmd[0] == "sops":
            return SimpleNamespace(returncode=0, stdout=_FAKE_ENC, stderr="")
        # dd wipe (wipe_and_remove) — no-op success
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return (lambda name: f"/usr/local/bin/{name}", _fake_run)


# endregion FIXTURE_sops_ok


# region FIXTURE_s3_ready
@pytest.fixture()
def s3_ready(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    """S3_BUCKET env + возвращает fake-клиент (передаётся s3_client параметром, W4b — 0 патчей)."""
    fake = FakeS3Client()
    monkeypatch.setenv("S3_BUCKET", "chaos-dr-backup-bucket")
    return fake


# endregion FIXTURE_s3_ready


def _args(*argv: str) -> SimpleNamespace:
    """Parse CLI argv into the Namespace run_backup expects."""
    return akb._build_parser().parse_args(list(argv))


# region TEST_full_pipeline
def test_full_pipeline_encrypt_upload_sha256(caplog, sops_ok, s3_ready: FakeS3Client) -> None:
    """Полный пайплайн: ключ → sops encrypt → S3 put (ACL=private) → sha256-сверка.

    # 🧪 TRAP[TEST] · Scenario: DR off-node backup (dr.md §2 шаги 1-4)
    # · Regression: sha256 загруженного == локального; ACL=private; Metadata.sha256
    # · Last fail: N/A (new feature, DevPlan 147 W2)
    # · Remove if: DR-процедура §2 заменена другим каналом (KMS-only)
    """
    caplog.set_level(logging.DEBUG)
    # W5 T5.3: age_key= DI (вместо monkeypatch detect_age_key)
    rc = akb.run_backup(
        _args("--recipient", _FAKE_RECIPIENT, "--s3-key", "age-key-backup/test.enc"),
        s3_client=s3_ready,
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],
    )

    assert rc == 0
    put_calls = [c for c in s3_ready.calls if c[0] == "put_object"]
    assert len(put_calls) == 1, f"expected exactly one put_object: {s3_ready.calls}"
    put_kwargs = put_calls[0][1]
    assert put_kwargs["Bucket"] == "chaos-dr-backup-bucket"
    assert put_kwargs["Key"] == "age-key-backup/test.enc"
    assert put_kwargs["ACL"] == "private", "backup object MUST be private (dr.md §2)"
    local_sha = akb._sha256_bytes(_FAKE_ENC.encode("utf-8"))
    assert put_kwargs["Metadata"]["sha256"] == local_sha
    # sha256-сверка выполнена (get_object после put)
    assert any(c[0] == "get_object" for c in s3_ready.calls), "sha256 verify requires get_object"
    _assert_ldd_imp9(caplog)


# endregion TEST_full_pipeline


# region TEST_dry_run
def test_dry_run_no_mutation(caplog, sops_ok, tmp_path) -> None:
    """--dry-run: 0 мутаций — нет S3-вызовов, нет output-файла, rc=0 без S3_BUCKET.

    # 🧪 TRAP[TEST] · Scenario: операторский pre-flight (make age-key-backup --dry-run)
    # · Regression: dry-run НЕ вызывает S3 и НЕ создаёт файлы (0 мутаций)
    # · Last fail: N/A (new feature, DevPlan 147 W2)
    # · Remove if: dry-run семантика изменена в CLI
    """
    caplog.set_level(logging.DEBUG)
    fake = FakeS3Client()
    # S3_BUCKET НЕ задан — dry-run не должен требовать S3-конфиг

    out_enc = tmp_path / "age-master-key.enc"
    rc = akb.run_backup(
        _args("--recipient", _FAKE_RECIPIENT, "--output-enc", str(out_enc), "--dry-run"),
        s3_client=fake,
        age_key=_FAKE_KEY,  # W5 T5.3: age_key= DI (вместо monkeypatch detect_age_key)
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],
    )

    assert rc == 0
    assert fake.calls == [], f"dry-run must not touch S3: {fake.calls}"
    assert not out_enc.exists(), "dry-run must not write --output-enc file (0 mutations)"
    _assert_ldd_imp9(caplog)


# endregion TEST_dry_run


# region TEST_no_upload
def test_no_upload_encrypt_only(caplog, sops_ok) -> None:
    """--no-upload: только шифрование; S3 не вызывается; rc=0.

    # 🧪 TRAP[TEST] · Scenario: локальное шифрование для SCP на ноду (dr.md §3 restore-first)
    # · Regression: --no-upload не требует S3 и не выгружает
    # · Last fail: N/A (new feature)
    # · Remove if: флаг --no-upload удалён
    """
    caplog.set_level(logging.DEBUG)
    fake = FakeS3Client()

    rc = akb.run_backup(
        _args("--recipient", _FAKE_RECIPIENT, "--no-upload"),
        s3_client=fake,
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],
    )  # W5 T5.3: age_key= DI

    assert rc == 0
    assert fake.calls == [], f"--no-upload must not call S3: {fake.calls}"
    _assert_ldd_imp9(caplog)


# endregion TEST_no_upload


# region TEST_no_secrets_stdout
def test_no_secrets_in_stdout_and_caplog(caplog, sops_ok, s3_ready: FakeS3Client, capsys) -> None:
    """0 секретов: stdout пуст; caplog не содержит полного ключа (только маскированный).

    # 🧪 TRAP[TEST] · Scenario: инвариант 1 (dr.md threat-model — masked logs)
    # · Regression: полный AGE ключ никогда не появляется в stdout/stderr/caplog
    # · Last fail: N/A (new feature)
    # · Remove if: маскирование ключа отменено
    """
    caplog.set_level(logging.DEBUG)
    rc = akb.main(
        ["--recipient", _FAKE_RECIPIENT, "--s3-key", "age-key-backup/test.enc"],
        s3_client=s3_ready,
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],  # W5 T5.3: age_key= DI (вместо monkeypatch detect_age_key)
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert _FAKE_KEY not in captured.out, f"stdout leaked the full key: {captured.out!r}"
    assert _FAKE_KEY not in captured.err, f"stderr leaked the full key: {captured.err!r}"
    for record in list(caplog.records):
        msg = getattr(record, "message", "")
        assert _FAKE_KEY not in msg, f"caplog leaked the full key: {msg}"
    assert akb._mask_key(_FAKE_KEY) in " ".join(getattr(r, "message", "") for r in caplog.records), (
        "masked key (first 8 chars) should be logged"
    )
    _assert_ldd_imp9(caplog)


# endregion TEST_no_secrets_stdout


# region TEST_output_enc
def test_output_enc_written(caplog, sops_ok, tmp_path) -> None:
    """--output-enc: локальный .enc пишется (для restore-first SCP, dr.md §3).

    # 🧪 TRAP[TEST] · Scenario: оператор сохраняет .enc для доставки на новую ноду
    # · Regression: содержимое файла == sops-шифротекст; rc=0
    # · Last fail: N/A (new feature)
    # · Remove if: --output-enc удалён
    """
    caplog.set_level(logging.DEBUG)
    out_enc = tmp_path / "age-master-key.enc"

    rc = akb.run_backup(
        _args("--recipient", _FAKE_RECIPIENT, "--output-enc", str(out_enc), "--no-upload"),
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],  # W5 T5.3: age_key= DI
    )

    assert rc == 0
    assert out_enc.read_bytes() == _FAKE_ENC.encode("utf-8")
    _assert_ldd_imp9(caplog)


# endregion TEST_output_enc


# region TEST_exit_codes
def test_exit_code_key_missing(caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ не найден (env-цепочка пуста) → EXIT_FATAL (10) — ручное вмешательство.

    # 🧪 TRAP[TEST] · Scenario: неверный окружение оператора (канон decrypt_secrets)
    # · Regression: key-absent никогда не даёт ложный успех
    # · Last fail: N/A (new feature)
    # · Remove if: exit-контракт 10 для key-absent изменён
    """
    caplog.set_level(logging.DEBUG)
    # W5 T5.3: age_key=None (явно) — «ключ не найден» (вместо monkeypatch detect_age_key)
    rc = akb.run_backup(_args("--recipient", _FAKE_RECIPIENT), age_key=None)

    assert rc == 10, "missing AGE key must be FATAL (10) — operator must set AGE_SECRET_KEY"
    _assert_ldd_imp9(caplog)


def test_exit_code_recipient_missing(caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Реципиент не задан (--recipient/AGE_RECIPIENT пусты) → EXIT_CONFIG_NOT_FOUND (2).

    # 🧪 TRAP[TEST] · Scenario: оператор без AGE_RECIPIENT
    # · Regression: recipient-absent — recoverable config error (2), не тихий успех
    # · Last fail: N/A (new feature)
    # · Remove if: exit-контракт 2 для recipient-absent изменён
    """
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGE_RECIPIENT", raising=False)

    rc = akb.run_backup(_args(), age_key=_FAKE_KEY)  # W5 T5.3: age_key= DI

    assert rc == 2, "missing AGE_RECIPIENT must be ConfigNotFound (2)"
    _assert_ldd_imp9(caplog)


# GUARD-PRESERVE (168): единственное покрытие sops-missing exit-path (rc=10 FATAL, fallback age-native TRAP S-13) — sops-absent никогда не проходит молча
def test_exit_code_sops_missing(caplog) -> None:
    """sops отсутствует → EXIT_FATAL (10) (fallback age-native — DevPlan 147 TRAP S-13).

    # 🧪 TRAP[TEST] · Scenario: dev-машина без sops CLI
    # · Regression: sops-missing никогда не проходит молча
    # · Last fail: N/A (new feature)
    # · Remove if: fallback age-native реализован как канон
    """
    caplog.set_level(logging.DEBUG)
    # DI (W-H): which_fn → None (sops отсутствует) — 0 патчей модуля
    rc = akb.main(
        ["--recipient", _FAKE_RECIPIENT, "--no-upload"],
        age_key=_FAKE_KEY,
        which_fn=lambda _: None,
    )

    assert rc == 10, "missing sops must be FATAL (10)"
    _assert_ldd_imp9(caplog)


def test_exit_code_sha256_mismatch(caplog, sops_ok, monkeypatch: pytest.MonkeyPatch) -> None:
    """sha256-сверка не сошлась (загруженный объект повреждён) → EXIT_FATAL (10).

    # 🧪 TRAP[TEST] · Scenario: S3 вернул повреждённый объект (network corruption)
    # · Regression: целостность — hard gate: mismatch → FATAL, plaintext НЕ удалять
    # · Last fail: N/A (new feature)
    # · Remove if: сверка целостности отменена
    """
    caplog.set_level(logging.DEBUG)

    class _TamperingClient(FakeS3Client):
        """S3 double that returns TAMPERED content on get_object (upload corruption)."""

        def get_object(self, **kwargs: object) -> dict[str, object]:  # type: ignore[override]
            self.calls.append(("get_object", kwargs))
            return {"Body": io.BytesIO(b"TAMPERED-CONTENT-000")}

    tampered = _TamperingClient()
    monkeypatch.setenv("S3_BUCKET", "chaos-dr-backup-bucket")
    # DI (W-H): which_fn/run_cmd из sops_ok (0 патчей subprocess)
    rc = akb.main(
        ["--recipient", _FAKE_RECIPIENT, "--s3-key", "age-key-backup/corrupt.enc"],
        s3_client=tampered,
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],  # W5 T5.3
    )

    assert rc == 10, "sha256 mismatch after upload must be FATAL (10) — integrity gate"
    _assert_ldd_imp9(caplog)


# endregion TEST_exit_codes


# region TEST_main_entry
def test_main_returns_int_and_ok(caplog, sops_ok, s3_ready: FakeS3Client) -> None:
    """main() -> int: полный путь через main() возвращает 0 (sys.exit только в __main__).

    # 🧪 TRAP[TEST] · Scenario: CLI контракт main() -> int (core/AGENTS.md)
    # · Regression: main не вызывает sys.exit (тестируемость), возвращает код
    # · Last fail: N/A (new feature)
    # · Remove if: main-контракт изменён
    """
    caplog.set_level(logging.DEBUG)

    rc = akb.main(
        ["--recipient", _FAKE_RECIPIENT, "--s3-key", "age-key-backup/main.enc"],
        s3_client=s3_ready,
        age_key=_FAKE_KEY,
        which_fn=sops_ok[0],
        run_cmd=sops_ok[1],  # W5 T5.3: age_key= DI
    )

    assert isinstance(rc, int)
    assert rc == 0
    _assert_ldd_imp9(caplog)


def test_default_s3_key_timestamped() -> None:
    """Дефолтный S3-ключ — timestamped (age-key-backup/age-master-key-<UTC>.enc).

    # 🧪 TRAP[TEST] · Scenario: уникальность объектов без явного --s3-key
    # · Regression: формат ключа стабилен (парсится по prefix/suffix)
    # · Last fail: N/A (new feature)
    # · Remove if: формат ключа изменён
    """
    key = akb._default_s3_key()
    assert key.startswith("age-key-backup/age-master-key-")
    assert key.endswith(".enc")
    assert len(key) > len("age-key-backup/age-master-key-.enc")


# endregion TEST_main_entry
