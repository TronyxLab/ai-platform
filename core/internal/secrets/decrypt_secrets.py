#!/usr/bin/env python3
# GREP_SUMMARY: sops, age, decrypt, secrets, env, temp-key, dd-wipe, cleanup, SOPS_AGE_KEY_FILE, fail-fast
# STRUCTURE: ▶ main() [register handlers (TERM/INT/HUP+atexit) → sweep stale /dev/shm] → ◇ resolve_enc_path [path | NODE name | glob] → ◇ detect_age_key ⚡ → ◇ decrypt_sops_file [temp key 0600 → sops --decrypt → dd wipe] → ◇ empty-parse guard ⚡PlatformFatalError → ◇ write_secrets_env [tempfile+rename atomic] → ⎋ exit 0|1|10
# region MODULE_CONTRACT
## @purpose  Python core for decrypting SOPS/age-encrypted secrets. Extracted from
##           core/internal/secrets/decrypt-secrets.sh. Manages temp age key file with
##           dd-wipe cleanup, runs sops --decrypt with SOPS_AGE_KEY_FILE env, and
##           atomically writes decrypted secrets.env with 0o600 permissions.
## @scope    Called directly from core/entrypoints/secrets.sh (make secrets-unlock), from
##           platform-secrets.service (ExecStart), from lib/secrets.sh step_10_decrypt_secrets,
##           and from Python tests. Резолв SECRETS_FILE перенесён из удалённого
##           decrypt-secrets.sh (DevPlan 173 W1.3) — no embedded shell logic.
## @invariants
##   (DD5 Security)
##   DD5-1: Temp key file on TMPFS (/dev/shm при наличии, fallback TMPDIR) с 0o600 — S-13, W10 T10.15
##   DD5-2: Wipe via dd if=/dev/zero BEFORE rm (not just rm -f)
##   DD5-3: Cleanup via atexit.register + signal handlers SIGTERM/SIGINT/SIGHUP — регистрация
##          ТОЛЬКО в main() (REF-0013: import-time signal.signal перехватывал диспозицию
##          импортёра); _cleanup_temp_files итерирует SNAPSHOT list(_TEMP_FILES) (DEP-0026)
##   DD5-4: No secret values in logs (keys masked to first 8 chars, [IMP:8] max)
##   DD5-5: SOPS_AGE_KEY_FILE env var for sops CLI compatibility
##   DD5-6: RuntimeError on decryption failure (fail-fast)
##   DD5-7: Atomic write via tempfile+rename prevents partial write corruption
##   DD5-8: sops stderr SANITIZED в логах/исключениях (truncate + redact пути temp-ключа
##          + redact значения age-ключа) — S-13, W10 T10.15, TEST-07
##   REF-0013 fail-fast: непустой decrypted payload + 0 распарсенных KEY → PlatformFatalError
##          (пустой secrets.env НЕ пишется, «decrypted successfully» при пустом результате
##          невозможен); явный enc-path/имя ноды, которых нет на диске → FileNotFoundError
##          БЕЗ glob-fallback (тихая расшифровка alphabetically-first чужой ноды устранена)
## @rationale DevPlan Strangler-Fig — Python core extracted from 223-line shell script.
##            Security-critical operations (key handling, cleanup) must be auditable,
##            testable, and verifiable via unit tests. Shell trap pattern replaced with
##            Python atexit+signal for deterministic cleanup order.
##            W10 T10.15 (S-13): temp-ключ на tmpfs (/dev/shm) — ключ в RAM, не на диске root-fs;
##            sanitize sops stderr — sops может печатать пути/контент в stderr на ошибке.
## @changes  2026-07-30 | Created — Python core extracted from decrypt-secrets.sh
## @changes  2026-08-05 | DevPlan 136 W10 T10.15 — tmpfs temp-key (S-13), sanitize sops stderr
## @changes  2026-08-24 | REF-0013 (Волна 0) — empty-parse→PlatformFatalError (пустой payload /
##             0 распарсенных KEY больше не пишут пустой secrets.env с «success»); signal/atexit-
##             регистрация перенесена из module-level в main() (+SIGHUP); _cleanup_temp_files
##             итерирует snapshot list(_TEMP_FILES) (DEP-0026); стартовый sweep stale temp-key'ев
##             /dev/shm; sops stderr redact также значения age-ключа (TEST-07); resolve_enc_path:
##             bare NODE=<name> → <secrets_dir>/<name>.enc.yaml, явный отсутствующий путь →
##             FileNotFoundError без glob-fallback (alphabetically-first нода устранена)
## ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Cleanup architecture migrated from shell (trap+cleanup_all) to Python (atexit+signal)
## · Rejected: Keeping cleanup in shell (risk: two parallel cleanup paradigms — shell trap AND Python atexit — creates ambiguity)
## · Reason: Python atexit+signal provides deterministic cleanup order, testability, and replaces shell trap EXIT INT TERM
## · Rev: if contract tests test_contract_decrypt.py:test_decrypt_trap_cleanup_all and test_decrypt_trap_includes_wipe are
##   updated to test Python cleanup architecture (atexit.register + _cleanup_temp_files) instead of shell patterns
# endregion MODULE_CONTRACT

import argparse
import atexit
import contextlib
import logging
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import ClassVar

# W1-A1 (план 170): литералы таймаутов → канон SoT (AMBER-зачистка research-D §D1).
# 10 (расшифровка префикса) → DOCKER_CMD_TIMEOUT; 120 (sops --decrypt) → LIFECYCLE_CMD_TIMEOUT.
from core.internal.shared.timeouts import DOCKER_CMD_TIMEOUT, LIFECYCLE_CMD_TIMEOUT

logger = logging.getLogger(__name__)

# ── sys.path bootstrap: root (канонические core.* импорты) ──
# ⚠️ TRAP[BUG] · 2026-08-01 · P1 · dual-module loading: `from exceptions import ...` (shim-имя) vs
# · `from core.internal.shared.exceptions import ...` создают ДВА разных класса PlatformFatalError
# · (Python кэширует модули по имени, не по файлу) → pytest.raises(PlatformFatalError) не ловит.
# · Fix: root в sys.path (паттерн deploy_orchestrator.py TRAP[BUG] 2026-07-31) + канонический импорт.
# · Prevention: НЕ использовать bare-импорты из shared/ во вновь редактируемых файлах.
# ── Константы ─────────────────────────────────────────────────────────────
_AGE_KEY_PREVIEW_LEN: int = 8  # сколько символов AGE-ключа показывать в логах (маскировка)
_QUOTED_VALUE_MIN_LEN: int = 2  # минимальная длина quoted-значения ("" / '')
_STDERR_CLEAN_MAX: int = 500  # обрезка stderr sops в ошибках
_PLATFORM_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)


# 142 W2: канонический резолвер run-артефактов (secrets.env → /var/lib/platform/run)
from core.internal.shared import deploy_paths
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# Детекция AGE-ключа делегируется в канонический node_detect.py.
from core.internal.shared.node_detect import detect_age_key as _detect_age_key_impl

# ── Global cleanup state ───────────────────────────────────────────────────────
_TEMP_FILES: list[str] = []

# REF-0013: префикс temp-key'ей и порог «stale» для стартового sweep /dev/shm —
# файлы, оставшиеся от crashed-прогонов (signal 9 / power loss), старше 1h удаляются на старте.
_AGE_KEY_TMP_PREFIX = "platform-age-key-"  # sync с mkstemp(prefix=...) в decrypt_sops_file
_STALE_TEMP_KEY_MAX_AGE_S = 3600
# Holder-list вместо `global`-флага (ruff PLW0603): одноэлементный список мутируется in-place.
_CLEANUP_REGISTERED: list[bool] = [False]


def _wipe_temp_key(path: str) -> None:
    """Wipe temp key file with dd if=/dev/zero, then remove.

    ## @purpose — Securely wipe age key from temp file before deletion.
    ##            Matches shell decrypt-secrets.sh behavior exactly.
    ##            Best-effort: failures are logged but do not raise.
    ## @io — ⇥ path: str → ⎋ None (side-effect: file zeroed + deleted)
    ## @complexity — O(1) — single dd invocation + os.remove
    ## @invariants
    ##   - dd if=/dev/zero of=<path> bs=1 count=<file_size>
    ##   - os.remove always attempted, even if dd fails
    ##   - No-op if path does not exist
    """
    if not pathlib.Path(path).exists():
        return
    try:
        size = pathlib.Path(path).stat().st_size
        if size > 0:
            subprocess.run(
                ["dd", "if=/dev/zero", f"of={path}", "bs=1", f"count={size}"],
                capture_output=True,
                timeout=DOCKER_CMD_TIMEOUT,
                check=False,
            )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning("[IMP:7][wipe_temp_key] dd wipe failed for %s (continuing with rm)", path)
    try:
        os.unlink(path)
    except OSError:
        logger.warning("[IMP:7][wipe_temp_key] os.remove failed for %s", path)


def _cleanup_temp_files() -> None:
    """Cleanup all tracked temp files with dd wipe.

    ## @purpose — Called by atexit and signal handlers. Iterates SNAPSHOT
    ##            list(_TEMP_FILES) to avoid mid-iteration mutation (DEP-0026:
    ##            итерация живого списка при мутации из wipe-путей пропускала
    ##            элементы и могла зациклиться).
    ## @io — ⎋ None (side-effect: files wiped + deleted)
    """
    # Snapshot ДО цикла — load-bearing (DEP-0026), не упрощать до итерации живого списка
    snapshot = list(_TEMP_FILES)
    for tmp_path in snapshot:
        _wipe_temp_key(tmp_path)
    _TEMP_FILES.clear()


def _signal_handler(signum: int, _frame: object) -> None:  # type: ignore[type-arg]
    """Signal handler: cleanup temp files, then re-raise with default handler.

    ## @purpose — Replaces shell trap EXIT INT TERM HUP. Cleans up temp files
    ##            and restores default signal disposition for clean exit.
    ##            Обрабатывает SIGTERM/SIGINT/SIGHUP (SIGHUP добавлен REF-0013 —
    ##            разрыв SSH-сессии оператора не должен оставлять ключ на tmpfs).
    """
    _cleanup_temp_files()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# region FUNC_register_cleanup_handlers
## @purpose — Register atexit + signal cleanup handlers. Вызывается ТОЛЬКО из main()
##            (REF-0013: module-level signal.signal перехватывал диспозицию ЛЮБОГО
##            импортёра модуля — тесты/CLI/сервисы получали чужие хендлеры на import).
def register_cleanup_handlers() -> None:
    """Register atexit + SIGTERM/SIGINT/SIGHUP handlers (idempotent)."""
    if _CLEANUP_REGISTERED[0]:
        return
    atexit.register(_cleanup_temp_files)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _signal_handler)
    _CLEANUP_REGISTERED[0] = True
    logger.info("[IMP:8][register_cleanup_handlers] Cleanup handlers registered (atexit + TERM/INT/HUP)")


# endregion FUNC_register_cleanup_handlers


# region FUNC_sweep_stale_temp_keys
## @purpose — Стартовый sweep /dev/shm (REF-0013): удалить temp-age-key'и, оставшиеся
##            от crashed-прогонов (mkstemp prefix platform-age-key-*; dd-wipe не успел).
##            Удаляются ТОЛЬКО файлы старше _STALE_TEMP_KEY_MAX_AGE_S — параллельный
##            живой процесс decrypt не пострадает. Best-effort: ошибки → warning.
def sweep_stale_temp_keys(tmp_dir: str = "/dev/shm") -> int:
    """Wipe stale platform-age-key-* leftovers older than the staleness threshold."""
    swept = 0
    now = time.time()
    try:
        candidates = sorted(pathlib.Path(tmp_dir).glob(f"{_AGE_KEY_TMP_PREFIX}*"))
    except OSError as e:
        logger.warning("[IMP:7][sweep_stale] Cannot scan %s: %s", tmp_dir, e)
        return 0
    for candidate in candidates:
        try:
            age_s = now - candidate.stat().st_mtime
        except OSError as e:
            logger.warning("[IMP:7][sweep_stale] Skip %s: %s", candidate, e)
            continue
        if age_s < _STALE_TEMP_KEY_MAX_AGE_S:
            continue
        _wipe_temp_key(str(candidate))
        swept += 1
        logger.info("[IMP:8][sweep_stale] Wiped stale temp key %s (age=%.0fs)", candidate.name, age_s)
    if swept:
        logger.warning(
            "[IMP:9][sweep_stale] Swept %d stale temp key(s) from %s — previous run crashed?", swept, tmp_dir
        )
    return swept


# endregion FUNC_sweep_stale_temp_keys


# region FUNC_detect_age_key
## @purpose — Detect AGE secret key. Wraps shared node_detect.detect_age_key() and
##            raises RuntimeError (fail-fast) if key not found through any mechanism.
## @io — ⇥ None → ⎋ str (the key) | raises RuntimeError
## @complexity — O(1) — delegates to node_detect.detect_age_key()
## @invariants
##   - RuntimeError raised if no key found (never silently returns None)
##   - Key masked to first 8 chars in logs
## @rationale Прямое делегирование в node_detect — единственный источник детекции AGE-ключа.
def detect_age_key() -> str:
    """Detect AGE secret key from env chain. Raises RuntimeError if not found."""
    logger.info("[IMP:8][detect_age_key] Detecting AGE secret key from env chain")
    key = _detect_age_key_impl()
    if key is None:
        logger.error(
            "[IMP:9][detect_age_key] FAILED: AGE_SECRET_KEY not found "
            "(checked AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE)"
        )
        msg = "AGE_SECRET_KEY not found. Set AGE_SECRET_KEY, SOPS_AGE_KEY, or AGE_SECRET_KEY_FILE environment variable."
        raise PlatformFatalError(msg)
    masked = key[:_AGE_KEY_PREVIEW_LEN] if len(key) >= _AGE_KEY_PREVIEW_LEN else key
    logger.info("[IMP:8][detect_age_key] AGE key found (%s...)", masked)
    return key


# endregion FUNC_detect_age_key


# region FUNC__yaml_to_env
## @purpose — Convert YAML key:value format to shell secrets.env KEY='value' format.
##            Handles comments, quoted values, empty values, and single-quote escaping.
## @io — ⇥ yaml_content: str → ⎋ env_content: str
## @complexity — O(n) where n = lines
## @invariants
##   - Skips empty lines and full-line comments (starting with #)
##   - Surrounding quotes (single or double) are stripped from values
##   - Single quotes in values are escaped as '\''
##   - Final line ends with \n
##   - Returns empty string for all-comment/empty input
## @rationale Shell's export_secrets_to_env() used bash regex + line-by-line printf.
##            Python version uses re.match + str.replace for the same semantics.
def _yaml_to_env(yaml_content: str) -> str:
    """Convert YAML key:value pairs to KEY='value' env format."""
    lines: list[str] = []
    for raw_line in yaml_content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", stripped)
        if match:
            key = match.group(1)
            value = match.group(2).strip()
            # Strip surrounding quotes (single or double)
            if len(value) >= _QUOTED_VALUE_MIN_LEN and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            # Escape single quotes in value
            escaped = value.replace("'", "'\\''")
            lines.append(f"{key}='{escaped}'")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# endregion FUNC__yaml_to_env


# region FUNC_decrypt_sops_file
## @purpose — Decrypt a SOPS-encrypted file using the provided age key.
##            Writes age key to a temp file on TMPFS (/dev/shm при наличии) with 0o600, runs
##            sops --decrypt with SOPS_AGE_KEY_FILE env, wipes temp key
##            with dd after use, returns decrypted plaintext. W10 T10.15 (S-13).
## @io — ⇥ age_key: str, enc_path: str → ⎋ str (decrypted content) | raises RuntimeError
## @complexity — O(1) — single subprocess call
## @invariants (DD5 Security)
##   DD5-1: Temp key on TMPFS (/dev/shm fallback TMPDIR) with 0o600
##   DD5-2: dd if=/dev/zero wipe before rm
##   DD5-3: atexit+signal cleanup registered
##   DD5-4: Key masked to first 8 chars in logs
##   DD5-5: SOPS_AGE_KEY_FILE env var set for sops
##   DD5-6: RuntimeError on failure (fail-fast)
##   DD5-8: sops stderr sanitized (truncate + redact temp-key path) — S-13
def decrypt_sops_file(age_key: str, enc_path: str) -> str:
    """Decrypt SOPS-encrypted file; returns decrypted plaintext content."""
    # ── Pre-flight: check sops is available ──
    if shutil.which("sops") is None:
        msg = "sops command not found — install sops (go.mozilla.org/sops)"
        raise PlatformFatalError(msg)

    # ── Verify input file exists ──
    if not os.path.isfile(enc_path):
        msg = f"Encrypted file not found: {enc_path}"
        raise FileNotFoundError(msg)

    logger.info("[IMP:8][decrypt_sops] Decrypting %s", enc_path)

    # ── Create temp file for age key (DD5-1: 0o600; W10 T10.15 S-13: TMPFS — /dev/shm) ──
    # tmpfs (RAM-backed) — ключ не оседает на диске root-fs; /dev/shm — tmpfs на Ubuntu (default),
    # fallback TMPDIR (обычно /tmp). Размер ключа ~сотни байт — лимит shm не проблема.
    # nosec B108: hardcoded /dev/shm НАМЕРЕН (S-13 tmpfs) — RAM-backed temp-ключ, не диск;
    #   fallback TMPDIR сохраняет 0o600 + dd-wipe (DD5-2). Альтернатива (env-параметризация)
    #   не оправдана: tmpfs — канон платформы, лишний конфиг создаёт вектор рассинхрона.
    tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None  # nosec B108
    fd, tmp_key_path = tempfile.mkstemp(prefix="platform-age-key-", suffix=".key", dir=tmp_dir)
    os.close(fd)
    os.chmod(tmp_key_path, 0o600)
    _TEMP_FILES.append(tmp_key_path)

    try:
        # Write key to temp file
        with pathlib.Path(tmp_key_path).open("w", encoding="utf-8") as f:
            f.write(age_key)
            f.write("\n")

        # ── Run sops --decrypt with SOPS_AGE_KEY_FILE env (DD5-5) ──
        sops_env = os.environ.copy()
        sops_env["SOPS_AGE_KEY_FILE"] = tmp_key_path

        masked_key = age_key[:_AGE_KEY_PREVIEW_LEN] if len(age_key) >= _AGE_KEY_PREVIEW_LEN else age_key
        logger.info("[IMP:8][decrypt_sops] Running sops --decrypt with key (%s...)", masked_key)

        result = subprocess.run(
            ["sops", "--decrypt", enc_path],
            capture_output=True,
            text=True,
            timeout=LIFECYCLE_CMD_TIMEOUT,
            env=sops_env,
            check=False,
        )

        # ── Handle decryption failure (DD5-6: fail-fast; TEST-07: sanitize sops stderr) ──
        if result.returncode != 0:
            # S-13/TEST-07: sanitize — truncate + redact пути temp-ключа И значения age-ключа
            # (sops может печатать пути/контент/ключ в stderr на ошибке — секреты не доходят
            # до stderr/логов/сообщения исключения).
            stderr_raw = result.stderr.strip() if result.stderr else ""
            stderr_clean = stderr_raw.replace(tmp_key_path, "<redacted-age-key-path>")
            stderr_clean = stderr_clean.replace(age_key, "<redacted-age-key>")
            stderr_clean = stderr_clean[:_STDERR_CLEAN_MAX] + ("…" if len(stderr_clean) > _STDERR_CLEAN_MAX else "")
            logger.error(
                "[IMP:9][decrypt_sops] FAILED: sops --decrypt returned %d: %s",
                result.returncode,
                stderr_clean,
            )
            msg = f"sops decryption failed: {stderr_clean}"
            raise PlatformFatalError(msg)

        decrypted = result.stdout
        logger.info(
            "[IMP:9][decrypt_sops] SUCCESS: Decrypted %s (%d bytes)",
            enc_path,
            len(decrypted),
        )
        return decrypted

    finally:
        # ── Wipe temp key with dd before rm (DD5-2) ──
        _wipe_temp_key(tmp_key_path)
        # Remove from tracking list (already cleaned)
        if tmp_key_path in _TEMP_FILES:
            _TEMP_FILES.remove(tmp_key_path)


# endregion FUNC_decrypt_sops_file


# region FUNC_write_secrets_env
## @purpose — Atomically write decrypted data to secrets.env with 0o600 permissions.
##            Uses tempfile+rename for atomicity — prevents partial write if interrupted.
## @io — ⇥ decrypted_data: str, output_path: str → ⎋ None (side-effect: file created)
## @complexity — O(n) where n = data size
## @invariants
##   - Atomic write via NamedTemporaryFile(dir=output_dir, delete=False) + os.replace
##   - Final file mode is 0o600 (owner read/write)
##   - Temp file created in same directory as output (same filesystem for atomic rename)
##   - Failure during write cleans up temp file (no partial output)
## @rationale Direct write risks partial file on disk if process is interrupted mid-write.
##            Atomic write ensures target file is either fully written or not touched.
# region FUNC__plw_body_write_secrets_env
## @purpose  Тело try-блока (PLW0717 extraction из write_secrets_env) — семантика except не меняется.
## @io       ⇥ decrypted_data, output_path, tmp_fd, tmp_path → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_write_secrets_env(decrypted_data: str, output_path: str, tmp_fd: int, tmp_path: str) -> None:
    with os.fdopen(tmp_fd, "w") as f:
        f.write(decrypted_data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp_path, 0o600)
    pathlib.Path(tmp_path).replace(output_path)
    logger.info("[IMP:9][write_secrets_env] SUCCESS: Written %s (%d bytes)", output_path, len(decrypted_data))


# endregion FUNC__plw_body_write_secrets_env


def write_secrets_env(decrypted_data: str, output_path: str) -> None:
    """Atomically write secrets.env with 0o600 permissions."""
    output_dir = pathlib.Path(output_path).parent
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, mode=0o700, exist_ok=True)

    logger.info("[IMP:8][write_secrets_env] Writing %d bytes to %s", len(decrypted_data), output_path)

    # ── Atomic write via tempfile + rename ──
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".secrets-",
        suffix=".tmp",
        dir=output_dir if output_dir else None,
    )
    try:
        _plw_body_write_secrets_env(decrypted_data, output_path, tmp_fd, tmp_path)
    except (OSError, ValueError) as e:
        # Cleanup temp file on failure
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        msg = f"Failed to write secrets env to {output_path}: {e}"
        raise PlatformFatalError(msg) from e


# endregion FUNC_write_secrets_env


# region FUNC_resolve_enc_path
# Канон-резолв входного enc-файла (перенесено из decrypt-secrets.sh, DevPlan 173 W1.3):
# env SECRETS_FILE / positional → точный путь → glob <secrets_dir>/*.enc.yaml fallback.
# AI-0025 (DevPlan 17 T4.1): secrets_dir резолвится каноном deploy_paths.node_configs_remote
# (env NODE_CONFIGS_REMOTE_BASE → /opt/node-configs); DI-параметр сохранён для тестов.
def resolve_enc_path(enc_path: str | None, *, secrets_dir: str | None = None) -> str:
    if secrets_dir is None:
        from core.internal.shared.deploy_paths import node_configs_remote

        secrets_dir = str(Path(node_configs_remote()) / "secrets")
    """Resolve encrypted secrets file path (env → explicit path → bare NODE name → glob fallback).

    ▶ ┌enc_path┐ → ◇ isfile? → ⎋ enc_path
      · ◇ bare NODE name? → <secrets_dir>/<NODE>.enc.yaml → ◇ isfile? → ⎋ · ✗ ⚡ FileNotFoundError
      · ◇ None/empty? → ◇ glob *.enc.yaml → ⊕ первый (sorted) → ⎋ · ✗ ⚡ FileNotFoundError
      · ◇ явный путь-подобный, но отсутствующий? → ⚡ FileNotFoundError БЕЗ glob-fallback

    ## @purpose — Порт резолва SECRETS_FILE (DevPlan 173 W1.3) + NODE-dispatch (REF-0013):
    ##            точный путь (или SECRETS_FILE env) → bare имя ноды (make secrets-unlock
    ##            NODE=<name> пробрасывает имя как позиционный аргумент) → иначе
    ##            <secrets_dir>/*.enc.yaml.
    ## @io — ⇥ enc_path: str | None (None/пусто = только glob),
    ##          secrets_dir: str (DI — тесты передают tmp_path-директорию) → ⎋ str (резолвленный путь)
    ## @complexity — O(1) — isfile + один glob
    ## ⚠️ TRAP[BUG] · 2026-08-24 · P1 · REF-0013: `make secrets-unlock NODE=X` расшифровывал чужую ноду
    ## · Symptom: позиционный аргумент «X» не существовал как файл → молча срабатывал glob-
    ## ·   fallback → расшифровывался alphabetically-first *.enc.yaml ДРУГОЙ ноды; оператор
    ## ·   получал чужие секреты без единого предупреждения (многонодовая ловушка).
    ## · Root: любой несуществующий вход тихо заменялся первым попавшимся enc-файлом.
    ## · Fix: (a) bare-имя (без '/' и суффикса .yaml) диспетчится в <dir>/<NODE>.enc.yaml;
    ## ·   (b) явный путь-подобный аргумент, которого нет на диске → FileNotFoundError БЕЗ
    ## ·   fallback; glob остаётся ТОЛЬКО для пустого входа (single-node канон).
    ## · Prevention: tests/unit/test_secrets_node_dispatch.py.
    ## @invariants
    ##   - enc_path существует как файл → возвращается как есть (без glob)
    ##   - bare NODE name → <secrets_dir>/<NODE>.enc.yaml; нет файла → FileNotFoundError
    ##   - Явный отсутствующий путь → FileNotFoundError (никакой тихой подмены чужим файлом)
    ##   - glob детерминированный (sorted) — только при пустом входе
    """
    if enc_path and os.path.isfile(enc_path):
        return enc_path

    # ── Bare NODE name dispatch (REF-0013): make secrets-unlock NODE=<name> ──
    # Имя ноды — одиночный токен без разделителей пути и без yaml-суффикса.
    if enc_path and "/" not in enc_path and os.sep not in enc_path and not enc_path.endswith(".yaml"):
        candidate = pathlib.Path(secrets_dir) / f"{enc_path}.enc.yaml"
        if candidate.is_file():
            logger.info("[IMP:8][resolve_enc_path] NODE '%s' → %s", enc_path, candidate)
            return str(candidate)
        msg: str = f"Encrypted secrets for node '{enc_path}' not found: {candidate} (no fallback to other nodes' files)"
        raise FileNotFoundError(msg)

    # ── Пустой вход → glob fallback (single-node канон) ──
    if not enc_path:
        matches = sorted(pathlib.Path(secrets_dir).glob("*.enc.yaml"))
        if matches:
            return str(matches[0])
        msg = f"No encrypted secrets file: set SECRETS_FILE env var or provide {secrets_dir}/*.enc.yaml"
        raise FileNotFoundError(msg)

    # ── Явный путь-подобный аргумент отсутствует → БЕЗ glob-fallback (REF-0013) ──
    msg = (
        f"Encrypted secrets file not found: {enc_path} "
        f"(explicit path is never silently replaced by another node's file; "
        f"for a node name use it without path separators)"
    )
    raise FileNotFoundError(msg)


# endregion FUNC_resolve_enc_path


# region FUNC_main
class _DecryptArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    enc_path: ClassVar[str | None]
    output_path: ClassVar[str]


## @purpose — CLI entrypoint: parse args, detect key, decrypt, convert YAML to env,
##            and atomically write secrets.env.
## @io — ⇥ sys.argv (or argparse defaults) → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(n) where n = encrypted file size
## @envvars — SECRETS_FILE (alternative to positional enc_path arg). ⚠️ v1.0.1 TRAP[BUG]:
##            SECRETS_FILE ≠ SECRETS_ENV_FILE: SECRETS_FILE = ВХОДНОЙ enc-файл (экспортится
##            node-side lib/secrets.sh step_10_decrypt_secrets); SECRETS_ENV_FILE = ВЫХОДНОЙ
##            путь secrets.env (потребители: secrets_manager cleanup, compose_preflight,
##            context_deployer, notify-hook). Research-D3 пометил чтение SECRETS_FILE как
##            drift — ЛОЖНОПОЛОЖИТЕЛЬНО; замена на SECRETS_ENV_FILE сломала bootstrap φ4
##            (enc_path required, exit 10). Возврат SECRETS_FILE — оба имени каноничны.
##            SECRETS_ENV_FILE (alternative to positional output_path arg, default /var/lib/platform/run/secrets.env)
##            173 W1.3: enc_path резолв (env → точный путь → /opt/node-configs/secrets/*.enc.yaml
##            glob) перенесён из decrypt-secrets.sh в resolve_enc_path().
# region FUNC__plw_body_main
## @purpose  Тело try-блока (PLW0717 extraction из main) — семантика except не меняется.
## @io       ⇥ args (output_path), enc_path (резолвленный входной файл) → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
## ⚠️ TRAP[BUG] · 2026-08-24 · P0 · REF-0013: пустой результат расшифровки рапортовался как success
## · Symptom: enc-файл с non-flat (вложенным) YAML → _yaml_to_env молча терял все ключи →
## ·   атомарно писался ПУСТОЙ secrets.env + лог «Secrets decrypted successfully» + exit 0;
## ·   φ4 помечала фазу done → skip навсегда; нода работала с нулём секретов до первого
## ·   использования (отложенный взрыв, повторение P0-класса 2026-07-23 уровнем глубже).
## · Root: success-marker до доказательства — отсутствие распарсенных ключей не проверялось.
## · Fix: fail-fast — пустой decrypted payload ИЛИ непустой payload с 0 распарсенных KEY →
## ·   PlatformFatalError ДО write_secrets_env; secrets.env НЕ перезаписывается.
## · Prevention: tests/unit/test_secrets_decrypt_failfast.py::test_empty_parse_fatal*.
def _plw_body_main(args: _DecryptArgs, enc_path: str) -> None:
    key = detect_age_key()
    logger.info("[IMP:8][main] Decrypting %s", enc_path)
    decrypted_yaml = decrypt_sops_file(key, enc_path)

    # ── REF-0013 fail-fast guard #1: sops вернул пустой payload ──
    if not decrypted_yaml.strip():
        logger.error(
            "[IMP:10][main] FAIL-FAST: %s decrypted to EMPTY payload — refusing to write empty secrets.env",
            enc_path,
        )
        msg = f"Decrypted payload of {enc_path} is empty — encrypted file is malformed or truncated"
        raise PlatformFatalError(msg)

    env_content = _yaml_to_env(decrypted_yaml)
    parsed_count = len(env_content.splitlines())
    unparsed_lines = (
        sum(1 for raw_line in decrypted_yaml.splitlines() if raw_line.strip() and not raw_line.strip().startswith("#"))
        - parsed_count
    )

    # ── REF-0013 fail-fast guard #2: непустой payload + 0 распарсенных KEY ──
    if parsed_count == 0:
        logger.error(
            "[IMP:10][main] FAIL-FAST: decrypted payload of %s (%d bytes) yielded 0 parsable "
            "KEY=VALUE entries — refusing to write empty secrets.env",
            enc_path,
            len(decrypted_yaml),
        )
        msg = (
            f"Decrypted YAML of {enc_path} yielded 0 parsable keys ({len(decrypted_yaml)} bytes) — "
            "non-flat/nested YAML or unexpected format"
        )
        raise PlatformFatalError(msg)

    logger.info(
        "[IMP:8][main] Converted YAML to env format (%d keys, %d unparsed non-comment lines)",
        parsed_count,
        max(unparsed_lines, 0),
    )
    write_secrets_env(env_content, args.output_path)
    logger.info("[IMP:9][main] Secrets decrypted successfully: %s", args.output_path)


# endregion FUNC__plw_body_main


def main() -> int:
    """CLI entrypoint: parse args, detect key, decrypt, convert to env, write."""
    parser = argparse.ArgumentParser(
        description="Decrypt SOPS/age-encrypted secrets and write secrets.env",
    )
    parser.add_argument(
        "enc_path",
        nargs="?",
        default=os.environ.get("SECRETS_FILE"),
        help="Path to encrypted SOPS file (.enc.yaml), OR bare node name (dispatched to "
        "<node-configs-secrets-dir>/<NODE>.enc.yaml — make secrets-unlock NODE=<name>). "
        "Falls back to SECRETS_FILE env var, then *.enc.yaml glob only when empty. "
        "An explicit missing path is NEVER replaced by another node's file (REF-0013).",
    )
    parser.add_argument(
        "output_path",
        nargs="?",
        # 142 W2 (B21): persistent /var/lib/platform/run (tmpfs /var/lib/platform/run не переживает reboot)
        default=os.environ.get("SECRETS_ENV_FILE", str(deploy_paths.secrets_env_file())),
        help="Output path for decrypted secrets.env. Falls back to SECRETS_ENV_FILE env var.",
    )
    args = parser.parse_args(namespace=_DecryptArgs())

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # ── REF-0013: side-effects только в main(), не на import (DD5-3) ──
    register_cleanup_handlers()
    swept = sweep_stale_temp_keys()
    if swept:
        logger.warning("[IMP:9][main] Startup sweep removed %d stale temp key(s) from /dev/shm", swept)

    # 173 W1.3: резолв SECRETS_FILE перенесён из decrypt-secrets.sh в Python —
    # env/positional → точный путь → bare NODE name → *.enc.yaml glob.
    try:
        resolved_enc_path = resolve_enc_path(args.enc_path)
    except FileNotFoundError as e:
        logger.error("[IMP:9][main] FAILED: %s", e)
        return 1

    logger.info("[IMP:8][main] Starting secrets decryption: %s → %s", resolved_enc_path, args.output_path)

    try:
        # 1. Detect age key
        _plw_body_main(args, resolved_enc_path)

    except PlatformError as e:
        logger.error("[IMP:9][main] PLATFORM ERROR (exit=%d): %s", e.exit_code, e)
        return e.exit_code
    except (RuntimeError, FileNotFoundError) as e:
        logger.error("[IMP:9][main] FAILED: %s", e)
        return 1
    # ruff: ignore[BLE001] — top-level CLI handler catch-all (RuntimeError/FileNotFoundError уже покрыты ветками выше)
    except Exception as e:  # noqa: EXC — top-level CLI handler catch-all; already catches (RuntimeError, FileNotFoundError) above
        logger.error("[IMP:9][main] UNEXPECTED FAILURE: %s", e)
        return 2
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
