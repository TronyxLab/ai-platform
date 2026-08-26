# GREP_SUMMARY: test-age-key-backup, node-matrix, F-033, AGE_RECIPIENT, S3_BUCKET, resolve-setting, env-override, plan-012 T6
# STRUCTURE: ▶ tmp_path matrix fixture → ◇ test_env_resolved_from_node_matrix → ◇ test_explicit_env_overrides_matrix → ◇ test_missing_everywhere_readable_error → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Unit-тесты матричного резолва age_key_backup (plan 012 T6 / F-033):
##           AGE_RECIPIENT/S3_* резолвятся из sops-матрицы ноды (secrets.env — тот же
##           носитель, что потребляет backup-cron); явный env приоритетнее; без всего —
##           читаемая ошибка, не тихий skip.
## @scope    Pure unit — resolve_setting() напрямую + run_backup/upload_backup через DI-швы
##           (age_key/which_fn/run_cmd/s3_client), 0 реальных sops/boto3 вызовов.
## @invariants
##   - Матрица-фикстура в tmp_path (Zero Hardcode); DI через SECRETS_ENV_FILE env
##   - Значения секретов не появляются в логах (инвариант 1 модуля)
##   - Каждый тест — @ldd_trajectory с IMP:9 бизнес-ассертами
## @rationale F-033: make age-key-backup требовал ручных AGE_RECIPIENT/S3_* env — матрица
##            ноды не использовалась; автоматизация убирает ручной шаг one-command DR.
## @changes   CREATED 2026-08-26 | DevPlan 012 T6 — node matrix resolution tests
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest

from core.internal.deploy.age_key_backup import (
    EXIT_CONFIG_NOT_FOUND,
    _CliArgs,
    resolve_setting,
    run_backup,
    upload_backup,
)
from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


def _write_matrix(tmp_path: pathlib.Path, entries: dict[str, str]) -> str:
    """Write a minimal decrypted-matrix fixture; return its path."""
    matrix_path = tmp_path / "secrets.env"
    lines = [f"{key}={value}" for key, value in entries.items()]
    matrix_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(matrix_path)


def _cli_args(**overrides: object) -> _CliArgs:
    """Minimal typed CLI args for run_backup DI calls."""
    defaults: dict[str, object] = {
        "recipient": "",
        "output_enc": "",
        "no_upload": True,
        "dry_run": False,
        "s3_key": "",
    }
    defaults.update(overrides)
    return _CliArgs(**defaults)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: values resolved from node matrix ($TEST_SPEC: test_env_resolved_from_node_matrix)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_env_resolved_from_node_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """AGE_RECIPIENT absent from env → resolved from node matrix secrets.env.

    ## @purpose — F-033 AC(a): sops-матрица ноды используется автоматически (backup-cron parity).
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts source == matrix)
    ## @complexity — O(1)
    ## @scenario — T6/F-033: матрица ноды как источник backup-env
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · F-033 node matrix resolution
    # · Scenario: env пуст → AGE_RECIPIENT/S3_BUCKET читаются из secrets.env матрицы
    # · Last fail: make age-key-backup требовал 4 ручные env-переменные (F-033)
    # · Remove if: матричный резолв перенесён в другой слой (shared resolver)
    monkeypatch.delenv("AGE_RECIPIENT", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    matrix = _write_matrix(
        tmp_path,
        {"AGE_RECIPIENT": "age1testrecipient0000000000000000000000000000000000000000", "S3_BUCKET": "dr-backup-bucket"},
    )

    recipient, source = resolve_setting("AGE_RECIPIENT", secrets_env_path=matrix)
    bucket, bucket_source = resolve_setting("S3_BUCKET", secrets_env_path=matrix)

    assert recipient.startswith("age1") and source == "matrix", f"Matrix resolution expected: {source}"
    assert bucket == "dr-backup-bucket" and bucket_source == "matrix"
    logger.critical("[IMP:9][test] AGE_RECIPIENT/S3_BUCKET resolved from node matrix")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: explicit env overrides matrix (AC T6b)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_explicit_env_overrides_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Explicit CLI/env value keeps priority over the node matrix.

    ## @purpose — AC(b): явные CLI-env сохраняют приоритет override.
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts env wins)
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · T6 override precedence
    # · Scenario: и env, и матрица заданы → значение из env
    # · Last fail: N/A (контракт-тест нового резолва)
    # · Remove if: приоритет env отменён владельцем
    monkeypatch.setenv("AGE_RECIPIENT", "age1explicitoverride")
    matrix = _write_matrix(tmp_path, {"AGE_RECIPIENT": "age1frommatrix"})

    recipient, source = resolve_setting("AGE_RECIPIENT", secrets_env_path=matrix)

    assert recipient == "age1explicitoverride", "Explicit env must override node matrix"
    assert source == "env"
    logger.critical("[IMP:9][test] Explicit env overrides node matrix")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: nothing anywhere → readable error, not silent skip (AC T6c)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_missing_everywhere_readable_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """No env, no matrix → readable error with repair hint (exit 2), not silent skip.

    ## @purpose — AC(c): без матрицы → читаемая ошибка (EXIT_CONFIG_NOT_FOUND),
    ##            сообщение называет оба источника (env + матрица).
    ## @io — ⇥ monkeypatch, caplog → ⎋ None (asserts exit code + message)
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · T6 fail-loud without matrix
    # · Scenario: env чист, SECRETS_ENV_FILE указывает на отсутствующий файл →
    #             run_backup вернет EXIT_CONFIG_NOT_FOUND с подсказкой про матрицу
    # · Last fail: F-033 — отсутствие значений падало глубже в sops с невнятной ошибкой
    # · Remove if: семантика ошибки изменена (например, интерактивный prompt)
    monkeypatch.delenv("AGE_RECIPIENT", raising=False)
    missing = "/nonexistent-f025/secrets.env"
    monkeypatch.setenv("SECRETS_ENV_FILE", missing)

    rc = run_backup(_cli_args(), age_key="AGE-SECRET-KEY-testkey000")

    assert rc == EXIT_CONFIG_NOT_FOUND, f"Expected exit {EXIT_CONFIG_NOT_FOUND}, got {rc}"
    assert any("node matrix" in r.getMessage() for r in caplog.records), (
        "Readable error must mention node matrix as a checked source"
    )
    logger.critical("[IMP:9][test] Missing everywhere → readable error exit=2")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: upload_backup resolves S3_BUCKET from matrix (bucket gate before client)
# ═══════════════════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_upload_bucket_from_node_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """upload_backup takes S3_BUCKET from the node matrix when env is unset.

    ## @purpose — F-033: S3_* часть матричного контракта; bucket гейт до создания клиента.
    ## @io — ⇥ monkeypatch, tmp_path → ⎋ None (asserts no raise + dry-run path)
    ## @complexity — O(1)
    """
    # 🧪 TRAP[TEST] · 2026-08-26 · REGRESSION · F-033 S3_BUCKET from matrix
    # · Scenario: env без S3_BUCKET, матрица с ним → upload_backup(dry_run) проходит без
    #             ConfigNotFoundError (bucket найден в матрице)
    # · Last fail: F-033 — S3_BUCKET только из env
    # · Remove if: bucket-гейт переносится на уровень run_backup
    monkeypatch.delenv("S3_BUCKET", raising=False)
    matrix = _write_matrix(tmp_path, {"S3_BUCKET": "matrix-bucket"})

    class _FakeClient:
        def __init__(self) -> None:
            self.puts: list[dict] = []

        def put_object(self, **kwargs: object) -> None:
            self.puts.append(kwargs)

        def get_object(self, **_kwargs: object) -> dict:
            return {}

    monkeypatch.setenv("SECRETS_ENV_FILE", matrix)
    payload = b"ciphertext"
    import hashlib

    payload_sha = hashlib.sha256(payload).hexdigest()
    fake = _FakeClient()

    class _FakeBody:
        def read(self) -> bytes:
            return payload

    fake.get_object = lambda **_kw: {"Body": _FakeBody()}  # type: ignore[assignment]
    upload_backup(payload, payload_sha, "k.enc", dry_run=False, s3_client=fake)

    assert fake.puts and fake.puts[0]["Bucket"] == "matrix-bucket", f"Bucket must come from node matrix: {fake.puts}"
    logger.critical("[IMP:9][test] S3_BUCKET resolved from node matrix in upload path")
