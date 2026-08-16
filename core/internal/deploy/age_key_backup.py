#!/usr/bin/env python3
# GREP_SUMMARY: age-key-backup, DR, sops, S3, sha256, off-node-backup, master-key, AGE_RECIPIENT, dry-run, no-upload, S-13
# STRUCTURE: ▶ detect_age_key (node_detect) → ◇ validate recipient → ◇ sops encrypt (tmpfs 0600 dd-wipe) → ◇ [--output-enc] → ◇ upload S3 (private ACL) → ⚡ sha256 verify → ⎋ exit 0|1|2|10
# region MODULE_CONTRACT
## @purpose  DevPlan 147 W2 (D-136-W10-S-13): off-node encrypted backup AGE мастер-ключа.
##           Автоматизация процедуры DR §2 (блок DR_PROCEDURE ниже; бывш. документ
##           age-master-key-dr.md, мигрирован Волной D DevPlan 164): ключ (env-цепочка
##           node_detect) → sops encrypt для age-реципиента → выгрузка в приватный S3 bucket
##           (S3_ENDPOINT_URL, ACL private) → sha256-сверка загруженного с локальным
##           (целостность ДО удаления локального plaintext, DR §2 шаг 4).
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


# region DR_PROCEDURE
## DR процедура AGE мастер-ключа (ПОЛНАЯ, операционная) — бывш. age-master-key-dr.md
## (мигрирован Волной D DevPlan 164, каталог документации удалён)
## Цель: пережить полную потерю ноды (reprovision, hoster ban, key-meltdown) без потери
## расшифровки secrets.env и проектных секретов. Реальные значения ключей НЕ фиксируются
## в репозитории (инвариант 1 MODULE_CONTRACT) — только имена, пути и процедуры.
##
## §1. Где хранится мастер-ключ (detect-цепочка node_detect.py::detect_age_key)
##      Первый непустой источник побеждает:
##       1. AGE_SECRET_KEY (env)       — CI (node-update, GitHub Secrets) и bootstrap
##                                       (ключ передаётся env-контентом, НЕ файлом) — канон
##       2. SOPS_AGE_KEY (env)         — sops-совместимость
##       3. AGE_SECRET_KEY_FILE (env)  — путь к файлу-ключу (bootstrap оператора)
##       4. ~/.config/age/keys.txt     — default key file dev-машины оператора (age CLI default;
##                                       на dev-машине — symlink на ~/.ssh/age-key-personal.txt)
##       5. /etc/age/key.txt           — restore-first fallback (НЕ канон): ручной перенос ключа
##                                       оператором при восстановлении ноды; читается только если
##                                       env-цепочка пуста и default key file не найден. φ4 НЕ
##                                       персистит ключ (persist-блок phases/secrets.py удалён).
##      На ноде bootstrap (φ4 secrets-provision) НЕ записывает ключ на диск — ключ приходит env
##      (AGE_SECRET_KEY/AGE_SECRET_KEY_FILE) и используется ТОЛЬКО для расшифровки через tmpfs
##      decrypt-only (decrypt_secrets.py: temp-key на /dev/shm 0600 + dd-wipe). Мастер-копия
##      живёт вне репозитория (секреты оператора / GitHub Secrets / password manager) — НЕ на
##      файловой системе ноды в plaintext. /etc/age/key.txt на ноде допустим исключительно как
##      restore-first fallback (ручной перенос оператором при восстановлении).
##
## §2. Off-node encrypted backup — АВТО-ПРОЦЕДУРА (verb age-key-backup, S3 bucket)
##      Инвариант: plaintext мастер-ключ за пределы ноды/оператора НЕ выходит; off-node backup —
##      ТОЛЬКО зашифрованный (sops age-реципиент или KMS) и в защищённом хранилище. Этой
##      процедуре соответствует `make age-key-backup` — тонкий Makefile-фасад настоящего файла
##      (python3 -m core.internal.deploy.age_key_backup): ключ (env-цепочка §1) →
##      sops encrypt --age AGE_RECIPIENT → выгрузка в приватный S3 bucket (ACL private) →
##      sha256-сверка загруженного с локальным (целостность ДО удаления локального plaintext).
##
##        # 1. Полный off-node backup (шифрование + выгрузка в приватный bucket + verify):
##        make age-key-backup AGE_RECIPIENT={recipient-pubkey}
##        # 2. Dry-run: 0 мутаций — валидация ключа/реципиента/sops без выгрузки и записи файлов:
##        make age-key-backup AGE_RECIPIENT={recipient-pubkey} DRY_RUN=1
##        # 3. Только шифрование (+ локальный .enc для SCP на ноду при restore-first, §3):
##        make age-key-backup AGE_RECIPIENT={recipient-pubkey} NO_UPLOAD=1 OUTPUT_ENC=/tmp/age-master-key.enc
##        # 4. Явный S3-объект-ключ (default: age-key-backup/age-master-key-{UTC}.enc):
##        make age-key-backup AGE_RECIPIENT={recipient-pubkey} S3_KEY=age-key-backup/manual-{ts}.enc
##
##      Параметры: AGE_RECIPIENT (обязателен; env-фолбэк AGE_RECIPIENT), DRY_RUN=1, NO_UPLOAD=1,
##      OUTPUT_ENC={path}, S3_KEY={key}. Хранилище: S3 bucket (timeweb.cloud, S3_ENDPOINT_URL) с
##      приватным ACL — bucket НЕ тот, что для SSL-кэша. Резервный слой: второй KMS-регион /
##      печатная копия в сейфе (defense-in-depth: один слой хранения ≠ DR). Периодичность:
##      при каждом bootstrap/ротации ключа + ежемесячная сверка (ротация ключа = немедленный
##      новый backup). Ручная (неавтоматизированная) эквивалентная процедура: sops encrypt
##      --age {recipient-pubkey} age-master-key.txt > age-master-key.enc → выгрузка в приватный
##      bucket → sha256sum-сверка загруженного с локальным до удаления локального plaintext.
##
## §3. Процедура восстановления (DR-drill, restore-first)
##      1. Новая нода бутстрапится до φ4 (make bootstrap-node NODE={new}), bootstrap
##         останавливается на secrets-provision (нет ключа) — ожидаемо.
##      2. Оператор доставляет зашифрованный backup (age-master-key.enc) на ноду по защищённому
##         каналу (SCP с операторским ключом).
##      3. На ноде ключ расшифровывается (sops --decrypt → temp-файл на tmpfs /dev/shm, 0600)
##         и передаётся в AGE_SECRET_KEY env для повторного запуска φ4. Plaintext не пересекает
##         сеть.
##      4. Верификация: make secrets-unlock NODE={new} расшифровывает secrets.env; сверка
##         известного значения (напр. POSTGRES_PASSWORD) с бэкапом — ключ корректен.
##      5. Персист: ключ сохраняется в password manager оператора / GitHub Secrets (как при
##         первичном bootstrap); temp-файл dd-wipe'ается (автоматика decrypt_secrets.py).
##      Быстрый drill на текущей ноде: make age-key-backup AGE_RECIPIENT={pubkey} NO_UPLOAD=1
##      OUTPUT_ENC={path} → локально sops --decrypt → сверить полученный ключ с фактическим.
##
## §4. Threat-model (сводка)
##      | Угроза                         | Митигация                                         | Остаточный риск |
##      | Потеря ноды (hoster/reprovision)| Off-node encrypted backup (sops/KMS, §2)           | Низкий: KMS-доступ оператора |
##      | Утечка ключа из env-логов      | Masked-логи (первые 8 символов, _mask_key);        | Низкий: маскирование не 100% |
##      |                                | sanitize sops stderr                               | |
##      | Ключ на диске ноды в plaintext | Temp-ключ tmpfs + dd-wipe; мастер-копия вне ноды  | Средний: fs crash до wipe |
##      | KMS-ключ скомпрометирован      | Отдельный KMS-ключ для AGE-бэкапа; ротация         | Средний: ротация не автоматизирована |
##      | Backup в облаке = атака        | sops/KMS ПЕРЕД выгрузкой; private ACL              | Низкий: шифрование = контроль доступа |
##
## §5. Completion status (операционные долги — Rev 2026-08-31)
##      - Реальный off-node encrypted backup мастер-ключа (sops/KMS) — НЕ выполнен → Debt
##        (DR-offnode-backup): требует операторского sops/KMS setup + приватный bucket;
##        verification-cost MEDIUM (процедура §2 + sha256-сверка).
##      - DR-drill на test-VPS (restore-first → verify secrets) — НЕ выполнен → Debt (DR-drill):
##        полный drill требует пересоздания ноды + операторского окна; verification-cost HIGH
##        (bootstrap до φ4 + restore).
##      - /etc/age/key.txt (plaintext на ноде, 0600 root): остаточный риск Средний (fs crash до
##        wipe), митигирован mode 0600 + tmpfs-temp-ключи при дешифровке (decrypt_secrets.py);
##        persist удалён — φ4 НЕ создаёт key.txt; файл допустим только как restore-first
##        fallback (ручной перенос).
## @links    core/internal/shared/node_detect.py (detect-цепочка, единый SoT),
##           core/internal/secrets/decrypt_secrets.py (tmpfs + dd-wipe + sanitize sops stderr),
##           core/secret-definitions.yaml (инвентарь секретов),
##           core/internal/bootstrap/lifecycle/helpers/users.py (ротация SSH/CI-ключей)
# endregion DR_PROCEDURE

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

# boto3/botocore — жёсткая зависимость age-key-backup (S3 upload/verify); module-level
# импорт гарантирует binding BotoClientError для except-ветки (reportPossiblyUnboundVariable).
from botocore.exceptions import ClientError as BotoClientError  # type: ignore[import-untyped]

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


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170).
    Аннотации без значений — cast no-op, argparse ставит свои дефолты."""

    recipient: str
    output_enc: str
    no_upload: bool
    dry_run: bool
    s3_key: str


# endregion DATACLASS_CliArgs


# S-13 (dr.md §2, decrypt_secrets.py DD5-1): temp-ключ на tmpfs /dev/shm — RAM-backed, не диск
_TMPFS_DIR = "/dev/shm"  # nosec B108 — hardcoded /dev/shm НАМЕРЕН (S-13 tmpfs, канон decrypt_secrets.py:235)
_SOPS_TIMEOUT_S = 120
_AGE_RECIPIENT_ENV = "AGE_RECIPIENT"
_KEY_MASK_LEN: int = 8  # сколько символов ключа показывать в маске (node_detect паттерн)
_STDERR_TRUNCATE_MAX: int = 500  # обрезка stderr sops в сообщении об ошибке


# region FUNC__mask_key
## @purpose — Маскирование ключа для логов: первые 8 символов (паттерн node_detect _log_masked).
## @io — ⇥ key: str → ⎋ str (masked)
## @complexity — O(1)
## @invariants — Возвращает "<8 chars>..." или весь ключ если короче 8 символов
def _mask_key(key: str) -> str:
    """Mask a secret key to its first 8 characters (node_detect masking pattern)."""
    return f"{key[:_KEY_MASK_LEN]}..." if len(key) >= _KEY_MASK_LEN else key


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
    if not pathlib.Path(path).exists():
        return
    try:
        size = pathlib.Path(path).stat().st_size
        if size > 0:
            subprocess.run(
                ["dd", "if=/dev/zero", f"of={path}", "bs=1", f"count={size}"],
                capture_output=True,
                timeout=10,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][age_key_backup][wipe] dd wipe failed for %s: %s", path, exc)
    try:
        os.unlink(path)
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
def encrypt_age_key(
    age_key: str,
    recipient: str,
    *,
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> bytes:
    """Encrypt the AGE master key with sops --age <recipient>; returns encrypted bytes.

    DI (W-H DevPlan 163): which_fn/run_cmd — None = shutil.which/subprocess.run (канон).
    """
    which_bin = shutil.which if which_fn is None else which_fn
    runner = subprocess.run if run_cmd is None else run_cmd
    if which_bin("sops") is None:
        msg = "sops command not found — install sops (go.mozilla.org/sops) или fallback age-native (DevPlan 147 TRAP[DECISION] S-13)"
        raise PlatformFatalError(msg)

    tmp_dir = _TMPFS_DIR if os.path.isdir(_TMPFS_DIR) else None  # nosec B108: S-13 tmpfs канон
    fd, tmp_key_path = tempfile.mkstemp(prefix="age-key-backup-", suffix=".key", dir=tmp_dir)
    os.close(fd)
    os.chmod(tmp_key_path, 0o600)
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        with pathlib.Path(tmp_key_path).open("w", encoding="utf-8") as f:
            f.write(age_key)
            f.write("\n")

        logger.info("[IMP:8][age_key_backup][encrypt] sops encrypt --age %s (key %s)", recipient, _mask_key(age_key))
        result = runner(
            ["sops", "encrypt", "--age", recipient, tmp_key_path],
            capture_output=True,
            text=True,
            timeout=_SOPS_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            # S-13: sanitize — redact temp-ключ path + truncate
            stderr_clean = (result.stderr or "").replace(tmp_key_path, "<redacted-age-key-path>")
            stderr_clean = stderr_clean[:_STDERR_TRUNCATE_MAX] + (
                "…" if len(stderr_clean) > _STDERR_TRUNCATE_MAX else ""
            )
            msg = f"sops encrypt failed (rc={result.returncode}): {stderr_clean}"
            raise PlatformFatalError(msg)

        enc_bytes = result.stdout.encode("utf-8")
        logger.info(
            "[IMP:9][age_key_backup][encrypt] sops encrypt OK: %d bytes encrypted for recipient %s",
            len(enc_bytes),
            recipient,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"sops encrypt timed out after {_SOPS_TIMEOUT_S}s"
        raise PlatformFatalError(msg) from exc
    else:
        return enc_bytes
    finally:
        _wipe_and_remove(tmp_key_path)


# endregion FUNC_encrypt_age_key


# region FUNC_upload_backup
## @purpose  Выгрузить sops-encrypted backup в приватный S3 bucket + sha256-сверка (dr.md §2 шаги 3-4).
## @io       ⇥ enc_bytes: bytes, sha256_hex: str, s3_key: str, dry_run: bool,
##              s3_client: object | None (W4b DI: fake-клиент в тестах; None → get_s3_client()) → ⎋ None
## @raises   ConfigNotFoundError (2): S3_BUCKET не задан; PlatformFatalError (10): sha256 mismatch;
##           PlatformError (1): ClientError/прочая ошибка выгрузки
## @complexity O(N) — put_object + get_object (сверка контента)
## @invariants
##   - Объект создаётся с ACL='private' и Metadata.sha256
##   - Сверка: get_object → sha256(body) == локальный sha256; mismatch → FATAL (10)
##   - dry_run: выгрузка пропускается, лог [IMP:9] DRY-RUN (0 мутаций)
##   - S3_SECRET_KEY никогда не логируется (клиент через shared/s3_client env-fallback)
##   - s3_client параметром (W4b): ленивый default = get_s3_client() (ровно текущее поведение)
## @changes 2026-08-13 | DevPlan 160 W4b — +s3_client (инъекция фабрики, убирает monkeypatch s3_client)
def upload_backup(
    enc_bytes: bytes,
    sha256_hex: str,
    s3_key: str,
    dry_run: bool = False,
    *,
    s3_client: object | None = None,
) -> None:
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
        msg = "S3_BUCKET env not set — off-node backup requires a private bucket (dr.md §2)"
        raise ConfigNotFoundError(msg)

    # ruff: ignore[PLW0717] — тело try >5 операторов (длинный upload-verify блок) — извлечение неразумно
    try:
        from core.internal.shared.s3_client import get_s3_client

        # W4b (160 T4.2): клиент параметром + ленивый default — ровно текущее поведение
        client = s3_client if s3_client is not None else get_s3_client()  # env-fallback
        logger.info("[IMP:8][age_key_backup][upload] put_object ACL=private bucket=%s key=%s", bucket, s3_key)
        client.put_object(  # pyright: ignore[reportAttributeAccessIssue] — boto3-клиент (stub-less); DI-переданный fake поддерживает тот же API
            Bucket=bucket,
            Key=s3_key,
            Body=enc_bytes,
            ACL="private",
            Metadata={"sha256": sha256_hex},
        )

        # ── sha256-сверка загруженного с локальным (dr.md §2 шаг 4) ──
        obj = client.get_object(Bucket=bucket, Key=s3_key)  # pyright: ignore[reportAttributeAccessIssue] — boto3-клиент (stub-less); DI-переданный fake поддерживает тот же API
        remote_bytes = obj["Body"].read()
        remote_sha = _sha256_bytes(remote_bytes)
        logger.info("[IMP:9][age_key_backup][verify] sha256 local=%s remote=%s", sha256_hex[:16], remote_sha[:16])
        if remote_sha != sha256_hex:
            msg = (
                f"SHA256 MISMATCH after upload: local={sha256_hex[:16]}... remote={remote_sha[:16]}... — "
                "не удалять локальный plaintext, перезапустить backup"
            )
            raise PlatformFatalError(msg)
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
        msg = f"S3 upload failed: {exc}"
        raise PlatformError(msg) from exc


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


# Sentinel: run_backup(age_key=...) DI-маркер — None является ВАЛИДНЫМ значением
# («ключ не найден»), поэтому отсутствие параметра отличаем от явного None.
_UNSET_AGE_KEY = object()


# region FUNC_run_backup
## @purpose  Оркестрация backup: ключ → recipient → encrypt → [output-enc] → upload+verify.
## @io       ⇥ args: argparse.Namespace, s3_client: object | None (W4b DI),
##           age_key: str | object | None (W5 T5.3 DI — _UNSET_AGE_KEY = detect_age_key();
##           None = «ключ не найден»; str = инжектированный ключ) → ⎋ int (exit code)
## @complexity O(N) — encrypt + (upload+verify)
## @invariants
##   - detect_age_key None → EXIT_FATAL (10) — ключ не найден, ручное вмешательство (канон decrypt_secrets)
##   - recipient из --recipient или AGE_RECIPIENT env; пусто → EXIT_CONFIG_NOT_FOUND (2)
##   - --dry-run: шифрование выполняется, output-enc/upload пропускаются
##   - --no-upload: только шифрование (+ --output-enc для сохранения .enc)
##   - W5 T5.3: age_key параметром (ленивый default detect_age_key) — убирает 8 monkeypatch
##     detect_age_key в тестах; поведение без параметра не меняется (composition root = CLI)
def run_backup(
    args: _CliArgs,
    *,
    s3_client: object | None = None,
    age_key: str | object | None = _UNSET_AGE_KEY,
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Run the off-node encrypted backup pipeline; returns the process exit code.

    DI (W-H DevPlan 163): which_fn/run_cmd — None = shutil.which/subprocess.run (канон).
    """
    # ── 1. Ключ: env-цепочка node_detect (AGE_SECRET_KEY → SOPS_AGE_KEY → файлы → /etc/age) ──
    if age_key is _UNSET_AGE_KEY:
        age_key = detect_age_key()
    if age_key is None:
        logger.error(
            "[IMP:9][age_key_backup][key] FAILED: AGE master key not found "
            "(AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE → ~/.config/age/keys.txt → /etc/age/key.txt)"
        )
        return EXIT_FATAL
    assert isinstance(age_key, str)  # _UNSET_AGE_KEY заменён на detect_age_key() выше; None отфильтрован
    logger.info("[IMP:8][age_key_backup][key] AGE master key found (%s)", _mask_key(age_key))

    # ── 2. Реципиент: --recipient или AGE_RECIPIENT env ──
    recipient = (args.recipient or os.environ.get(_AGE_RECIPIENT_ENV, "")).strip()
    if not recipient:
        logger.error(
            "[IMP:9][age_key_backup][recipient] AGE_RECIPIENT not set — usage: make age-key-backup AGE_RECIPIENT=<pubkey>"
        )
        return EXIT_CONFIG_NOT_FOUND

    # ── 3. Шифрование (sops --age) ──
    enc_bytes = encrypt_age_key(age_key, recipient, which_fn=which_fn, run_cmd=run_cmd)
    sha256_hex = _sha256_bytes(enc_bytes)
    logger.info("[IMP:9][age_key_backup][encrypt] encrypted=%d bytes sha256=%s...", len(enc_bytes), sha256_hex[:16])

    # ── 4. Локальное сохранение .enc (для SCP на ноду при restore-first, dr.md §3) ──
    if args.output_enc:
        if args.dry_run:
            logger.info(
                "[IMP:9][age_key_backup][output] DRY-RUN: would write %s (%d bytes)", args.output_enc, len(enc_bytes)
            )
        else:
            with pathlib.Path(args.output_enc).open("wb") as f:
                f.write(enc_bytes)
            logger.info(
                "[IMP:9][age_key_backup][output] Wrote encrypted backup: %s (%d bytes)", args.output_enc, len(enc_bytes)
            )

    # ── 5. Выгрузка в S3 + sha256-сверка ──
    if args.no_upload:
        logger.info("[IMP:9][age_key_backup][upload] --no-upload: S3 upload skipped")
        return EXIT_OK
    s3_key = args.s3_key or _default_s3_key()
    upload_backup(enc_bytes, sha256_hex, s3_key, dry_run=args.dry_run, s3_client=s3_client)
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
## @io — ⇥ argv: list[str] | None, s3_client: object | None (W4b DI),
##       age_key: str | object | None (W5 T5.3 DI — пробрасывается в run_backup) → ⎋ int
##       (0=ok, 1=generic, 2=ConfigNotFound, 10=Fatal)
## @complexity — O(N) — delegates to run_backup
## @invariants
##   - sys.exit вызывается ТОЛЬКО в __main__ (main() -> int контракт core/AGENTS.md)
##   - Логи в stderr; stdout — только краткое резюме БЕЗ секретов
def main(
    argv: list[str] | None = None,
    *,
    s3_client: object | None = None,
    age_key: str | object | None = _UNSET_AGE_KEY,
    which_fn: Callable[[str], str | None] | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Run the CLI and return the process exit code.

    DI (W-H DevPlan 163): which_fn/run_cmd — None = каноны; пробрасываются в run_backup.
    """
    from typing import cast as _cast

    args = _cast(_CliArgs, _cast(object, _build_parser().parse_args(argv)))
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        return run_backup(args, s3_client=s3_client, age_key=age_key, which_fn=which_fn, run_cmd=run_cmd)
    except (ConfigNotFoundError, PlatformFatalError, PlatformError) as e:
        logger.error("[IMP:10][age_key_backup] ERROR (exit=%d): %s", e.exit_code, e)
        return e.exit_code
    # ruff: ignore[BLE001] — top-level CLI handler (unexpected)
    except Exception as e:  # noqa: EXC — top-level CLI handler (unexpected)
        logger.error("[IMP:10][age_key_backup] UNEXPECTED FAILURE: %s", e)
        return EXIT_GENERIC


# endregion FUNC_main


# region FUNC_CLI
if __name__ == "__main__":
    sys.exit(main())
# endregion FUNC_CLI
