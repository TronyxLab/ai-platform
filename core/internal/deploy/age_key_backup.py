#!/usr/bin/env python3
# GREP_SUMMARY: age-key-backup, DR, sops, S3, sha256, off-node-backup, master-key, AGE_RECIPIENT, dry-run, no-upload, S-13
# STRUCTURE: ▶ detect_age_key (node_detect) → ◇ validate recipient → ◇ sops encrypt (tmpfs 0600 dd-wipe) → ◇ [--output-enc] → ◇ upload S3 (private ACL) → ⚡ sha256 verify → ⎋ exit 0|1|2|10
# region MODULE_CONTRACT
## @purpose  DevPlan 147 W2 (D-136-W10-S-13): off-node encrypted backup AGE мастер-ключа.
##           Автоматизация процедуры docs/age-master-key-dr.md §2: ключ (env-цепочка
##           node_detect) → sops encrypt для age-реципиента → выгрузка в приватный S3 bucket
##           (S3_ENDPOINT_URL, ACL private) → sha256-сверка загруженного с локальным
##           (целостность ДО удаления локального plaintext, dr.md §2 шаг 4).
## @scope    Тонкий Makefile-таргет `make age-key-backup` → python3 -m core.internal.deploy.age_key_backup.
##           ТОЛЬКО off-node backup; восстановление — процедура §3 (restore-first, W3.1, окно 2026-08-31).
## @invariants
##   1. Ключ в логах/выводе — ТОЛЬКО маскированный (первые 8 символов, паттерн node_detect _log_masked);
##      S3_SECRET_KEY/шифротекст НИКОГДА не выводятся — 0 секретов в stdout/stderr
##   2. plaintext-ключ не оседает на диске: temp-файл на tmpfs (/dev/shm при наличии) 0600 + dd-wipe (S-13)
##   3. В S3 уходит ТОЛЬКО sops-encrypted контейнер; объект создаётся с ACL=private
##   4. sha256-сверка загруженного объекта с локальным шифротекстом ДО exit 0 (целостность)
##   5. exit-коды по shared/contracts.py (0=ok, 1=generic, 2=ConfigNotFound, 10=Fatal);
##      sys.exit ТОЛЬКО в main(); business-функции возвращают/кидают PlatformError-иерархию
##   6. --dry-run: ключ читается и шифруется (валидация цепочки + sops + реципиента),
##      но выгрузка и запись файлов ПРОПУСКАЮТСЯ — 0 мутаций (тест dry-run не мутирует)
##   7. --no-upload: только шифрование (с --output-enc сохраняет .enc локально для SCP на ноду)
## @rationale DevPlan 147 W2 (TRAP[DECISION] S-13): ручная процедура §2 невоспроизводима —
##           CLI + make-таргет делают DR-drill (W3.1) воспроизводимым и проверяемым.
##           Размещение в core/internal/deploy/ — по XML код-графа 147 (path=core/internal/deploy/age_key_backup.py).
## @changes  2026-08-11 | DevPlan 147 W2 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from core.internal.shared.contracts import (
    EXIT_CONFIG_NOT_FOUND,
    EXIT_FATAL,
    EXIT_GENERIC,
    EXIT_OK,
)
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)
from core.internal.shared.node_detect import detect_age_key

logger = logging.getLogger(__name__)

# S-13 (dr.md §2, decrypt_secrets.py DD5-1): temp-ключ на tmpfs /dev/shm — RAM-backed, не диск
_TMPFS_DIR = "/dev/shm"  # nosec B108 — hardcoded /dev/shm НАМЕРЕН (S-13 tmpfs, канон decrypt_secrets.py:235)
_SOPS_TIMEOUT_S = 120
_AGE_RECIPIENT_ENV = "AGE_RECIPIENT"


# region FUNC__mask_key
## @purpose — Маскирование ключа для логов: первые 8 символов (паттерн node_detect _log_masked).
## @io — ⇥ key: str → ⎋ str (masked)
## @complexity — O(1)
## @invariants — Возвращает "<8 chars>..." или весь ключ если короче 8 символов
def _mask_key(key: str) -> str:
    """Mask a secret key to its first 8 characters (node_detect masking pattern)."""
    return f"{key[:8]}..." if len(key) >= 8 else key


# endregion FUNC__mask_key


# region FUNC__wipe_and_remove
## @purpose — Безопасное удаление temp-файла с ключом: dd-wipe нулями до rm (S-13, DD5-2).
## @io — ⇥ path: str → ⎋ None
## @complexity — O(1) — single dd invocation + os.remove
## @invariants
##   - dd if=/dev/zero of=path bs=1 count=file_size; os.remove всегда пробуется
##   - No-op если файл не существует; ошибки wipe только логгируются (best-effort)
## @rationale Не импортируем secrets/decrypt_secrets (его импорт регистрирует atexit+signal
##           handlers — нежелательно в DR-CLI); 10-строчный санитарный примитив продублирован
##           намеренно (Small Simple Blocks, не бизнес-логика)
def _wipe_and_remove(path: str) -> None:
    """Wipe a temp key file with dd (zeros) then remove it — best-effort (S-13)."""
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
        if size > 0:
            subprocess.run(
                ["dd", "if=/dev/zero", f"of={path}", "bs=1", f"count={size}"],
                capture_output=True,
                timeout=10,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][age_key_backup][wipe] dd wipe failed for %s: %s", path, exc)
    try:
        os.remove(path)
    except OSError as exc:
        logger.warning("[IMP:7][age_key_backup][wipe] os.remove failed for %s: %s", path, exc)


# endregion FUNC__wipe_and_remove


# region FUNC__sha256_bytes
## @purpose — SHA256 hex-дайджест бинарных данных (локальный шифротекст и загруженный объект).
## @io — ⇥ data: bytes → ⎋ str (hex digest)
## @complexity — O(N) — N = размер шифротекста
def _sha256_bytes(data: bytes) -> str:
    """Return the SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


# endregion FUNC__sha256_bytes


# region FUNC_encrypt_age_key
## @purpose  Зашифровать AGE мастер-ключ sops'ом для age-реципиента (dr.md §2 шаг 2).
## @io       ⇥ age_key: str, recipient: str → ⎋ bytes (sops-encrypted контейнер)
## @raises   PlatformFatalError: sops отсутствует (10); sops вернул ошибку (10 — ручное вмешательство)
## @complexity O(1) — single sops subprocess
## @invariants
##   - Temp-файл ключа на tmpfs /dev/shm (fallback TMPDIR) с 0o600; dd-wipe после (S-13)
##   - sops stderr санитизируется (redact temp-путь, truncate) — S-13
##   - Ключ логируется только маскированным (_mask_key)
def encrypt_age_key(age_key: str, recipient: str) -> bytes:
    """Encrypt the AGE master key with sops --age <recipient>; returns encrypted bytes."""
    if shutil.which("sops") is None:
        raise PlatformFatalError(
            "sops command not found — install sops (go.mozilla.org/sops) или fallback age-native (DevPlan 147 TRAP[DECISION] S-13)"
        )

    tmp_dir = _TMPFS_DIR if os.path.isdir(_TMPFS_DIR) else None  # nosec B108: S-13 tmpfs канон
    fd, tmp_key_path = tempfile.mkstemp(prefix="age-key-backup-", suffix=".key", dir=tmp_dir)
    os.close(fd)
    os.chmod(tmp_key_path, 0o600)
    try:
        with open(tmp_key_path, "w") as f:
            f.write(age_key)
            f.write("\n")

        logger.info("[IMP:8][age_key_backup][encrypt] sops encrypt --age %s (key %s)", recipient, _mask_key(age_key))
        result = subprocess.run(
            ["sops", "encrypt", "--age", recipient, tmp_key_path],
            capture_output=True,
            text=True,
            timeout=_SOPS_TIMEOUT_S,
        )
        if result.returncode != 0:
            # S-13: sanitize — redact temp-ключ path + truncate
            stderr_clean = (result.stderr or "").replace(tmp_key_path, "<redacted-age-key-path>")
            stderr_clean = stderr_clean[:500] + ("…" if len(stderr_clean) > 500 else "")
            raise PlatformFatalError(f"sops encrypt failed (rc={result.returncode}): {stderr_clean}")

        enc_bytes = result.stdout.encode("utf-8")
        logger.info(
            "[IMP:9][age_key_backup][encrypt] sops encrypt OK: %d bytes encrypted for recipient %s",
            len(enc_bytes),
            recipient,
        )
        return enc_bytes
    except subprocess.TimeoutExpired as exc:
        raise PlatformFatalError(f"sops encrypt timed out after {_SOPS_TIMEOUT_S}s") from exc
    finally:
        _wipe_and_remove(tmp_key_path)


# endregion FUNC_encrypt_age_key


# region FUNC_upload_backup
## @purpose  Выгрузить sops-encrypted backup в приватный S3 bucket + sha256-сверка (dr.md §2 шаги 3-4).
## @io       ⇥ enc_bytes: bytes, sha256_hex: str, s3_key: str, dry_run: bool → ⎋ None
## @raises   ConfigNotFoundError (2): S3_BUCKET не задан; PlatformFatalError (10): sha256 mismatch;
##           PlatformError (1): ClientError/прочая ошибка выгрузки
## @complexity O(N) — put_object + get_object (сверка контента)
## @invariants
##   - Объект создаётся с ACL='private' и Metadata.sha256
##   - Сверка: get_object → sha256(body) == локальный sha256; mismatch → FATAL (10)
##   - dry_run: выгрузка пропускается, лог [IMP:9] DRY-RUN (0 мутаций)
##   - S3_SECRET_KEY никогда не логируется (клиент через shared/s3_client env-fallback)
def upload_backup(enc_bytes: bytes, sha256_hex: str, s3_key: str, dry_run: bool = False) -> None:
    """Upload encrypted backup to a private S3 bucket and verify sha256 integrity."""
    bucket = os.environ.get("S3_BUCKET", "").strip()

    if dry_run:
        logger.info(
            "[IMP:9][age_key_backup][upload] DRY-RUN: upload skipped (bucket=%s key=%s sha256=%s)",
            bucket or "<unset>",
            s3_key,
            sha256_hex[:16],
        )
        return

    if not bucket:
        raise ConfigNotFoundError("S3_BUCKET env not set — off-node backup requires a private bucket (dr.md §2)")

    try:
        from botocore.exceptions import ClientError as BotoClientError  # type: ignore[import-untyped]

        from core.internal.shared.s3_client import get_s3_client

        client = get_s3_client()  # env-fallback: S3_ENDPOINT_URL/S3_ACCESS_KEY/S3_SECRET_KEY/S3_REGION
        logger.info("[IMP:8][age_key_backup][upload] put_object ACL=private bucket=%s key=%s", bucket, s3_key)
        client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=enc_bytes,
            ACL="private",
            Metadata={"sha256": sha256_hex},
        )

        # ── sha256-сверка загруженного с локальным (dr.md §2 шаг 4) ──
        obj = client.get_object(Bucket=bucket, Key=s3_key)
        remote_bytes = obj["Body"].read()
        remote_sha = _sha256_bytes(remote_bytes)
        logger.info("[IMP:9][age_key_backup][verify] sha256 local=%s remote=%s", sha256_hex[:16], remote_sha[:16])
        if remote_sha != sha256_hex:
            raise PlatformFatalError(
                f"SHA256 MISMATCH after upload: local={sha256_hex[:16]}... remote={remote_sha[:16]}... — "
                "не удалять локальный plaintext, перезапустить backup"
            )
        logger.info(
            "[IMP:9][age_key_backup][upload] UPLOAD VERIFIED: s3://%s/%s (sha256 %s...)",
            bucket,
            s3_key,
            sha256_hex[:16],
        )
    except PlatformError:
        raise
    except BotoClientError as exc:
        logger.error("[IMP:9][age_key_backup][upload] S3 ClientError: %s", exc)
        raise PlatformError(f"S3 upload failed: {exc}") from exc


# endregion FUNC_upload_backup


# region FUNC__default_s3_key
## @purpose — Дефолтный S3-ключ backup: age-key-backup/age-master-key-UTC-timestamp.enc.
## @io — ⇥ None → ⎋ str
## @complexity — O(1)
def _default_s3_key() -> str:
    """Build a timestamped default S3 key for the encrypted backup."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"age-key-backup/age-master-key-{ts}.enc"


# endregion FUNC__default_s3_key


# region FUNC_run_backup
## @purpose  Оркестрация backup: ключ → recipient → encrypt → [output-enc] → upload+verify.
## @io       ⇥ args: argparse.Namespace → ⎋ int (exit code)
## @complexity O(N) — encrypt + (upload+verify)
## @invariants
##   - detect_age_key None → EXIT_FATAL (10) — ключ не найден, ручное вмешательство (канон decrypt_secrets)
##   - recipient из --recipient или AGE_RECIPIENT env; пусто → EXIT_CONFIG_NOT_FOUND (2)
##   - --dry-run: шифрование выполняется, output-enc/upload пропускаются
##   - --no-upload: только шифрование (+ --output-enc для сохранения .enc)
def run_backup(args: argparse.Namespace) -> int:
    """Run the off-node encrypted backup pipeline; returns the process exit code."""
    # ── 1. Ключ: env-цепочка node_detect (AGE_SECRET_KEY → SOPS_AGE_KEY → файлы → /etc/age) ──
    age_key = detect_age_key()
    if age_key is None:
        logger.error(
            "[IMP:9][age_key_backup][key] FAILED: AGE master key not found "
            "(AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE → ~/.config/age/keys.txt → /etc/age/key.txt)"
        )
        return EXIT_FATAL
    logger.info("[IMP:8][age_key_backup][key] AGE master key found (%s)", _mask_key(age_key))

    # ── 2. Реципиент: --recipient или AGE_RECIPIENT env ──
    recipient = (args.recipient or os.environ.get(_AGE_RECIPIENT_ENV, "")).strip()
    if not recipient:
        logger.error(
            "[IMP:9][age_key_backup][recipient] AGE_RECIPIENT not set — usage: make age-key-backup AGE_RECIPIENT=<pubkey>"
        )
        return EXIT_CONFIG_NOT_FOUND

    # ── 3. Шифрование (sops --age) ──
    enc_bytes = encrypt_age_key(age_key, recipient)
    sha256_hex = _sha256_bytes(enc_bytes)
    logger.info("[IMP:9][age_key_backup][encrypt] encrypted=%d bytes sha256=%s...", len(enc_bytes), sha256_hex[:16])

    # ── 4. Локальное сохранение .enc (для SCP на ноду при restore-first, dr.md §3) ──
    if args.output_enc:
        if args.dry_run:
            logger.info(
                "[IMP:9][age_key_backup][output] DRY-RUN: would write %s (%d bytes)", args.output_enc, len(enc_bytes)
            )
        else:
            with open(args.output_enc, "wb") as f:
                f.write(enc_bytes)
            logger.info(
                "[IMP:9][age_key_backup][output] Wrote encrypted backup: %s (%d bytes)", args.output_enc, len(enc_bytes)
            )

    # ── 5. Выгрузка в S3 + sha256-сверка ──
    if args.no_upload:
        logger.info("[IMP:9][age_key_backup][upload] --no-upload: S3 upload skipped")
        return EXIT_OK
    s3_key = args.s3_key or _default_s3_key()
    upload_backup(enc_bytes, sha256_hex, s3_key, dry_run=args.dry_run)
    logger.info("[IMP:9][age_key_backup][done] Off-node AGE backup complete (sha256 %s...)", sha256_hex[:16])
    return EXIT_OK


# endregion FUNC_run_backup


# region FUNC__build_parser
## @purpose — CLI parser: recipient/output-enc/no-upload/dry-run/s3-key.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="core.internal.deploy.age_key_backup",
        description="Off-node encrypted backup of the AGE master key (dr.md §2, DevPlan 147 W2).",
    )
    parser.add_argument(
        "--recipient",
        default=None,
        help="AGE recipient public key (env fallback: AGE_RECIPIENT).",
    )
    parser.add_argument(
        "--output-enc",
        default=None,
        help="Write the encrypted backup to this local path (for SCP to node during restore-first).",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Encrypt only — skip the S3 upload.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate key/recipient/sops and encrypt, but skip upload and file writes (0 mutations).",
    )
    parser.add_argument(
        "--s3-key",
        default=None,
        help="Explicit S3 object key (default: age-key-backup/age-master-key-<UTC>.enc).",
    )
    return parser


# endregion FUNC__build_parser


# region FUNC_main
## @purpose — CLI entrypoint: parse args, run pipeline, map exceptions to exit codes.
## @io — ⇥ argv: list[str] | None → ⎋ int (0=ok, 1=generic, 2=ConfigNotFound, 10=Fatal)
## @complexity — O(N) — delegates to run_backup
## @invariants
##   - sys.exit вызывается ТОЛЬКО в __main__ (main() -> int контракт core/AGENTS.md)
##   - Логи в stderr; stdout — только краткое резюме БЕЗ секретов
def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        return run_backup(args)
    except (ConfigNotFoundError, PlatformFatalError, PlatformError) as e:
        logger.error("[IMP:10][age_key_backup] ERROR (exit=%d): %s", e.exit_code, e)
        return e.exit_code
    except Exception as e:  # noqa: EXC — top-level CLI handler (unexpected)
        logger.error("[IMP:10][age_key_backup] UNEXPECTED FAILURE: %s", e)
        return EXIT_GENERIC


# endregion FUNC_main


# region FUNC_CLI
if __name__ == "__main__":
    sys.exit(main())
# endregion FUNC_CLI
