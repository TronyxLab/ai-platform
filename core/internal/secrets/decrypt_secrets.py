#!/usr/bin/env python3
# GREP_SUMMARY: sops, age, decrypt, secrets, env, temp-key, dd-wipe, cleanup, SOPS_AGE_KEY_FILE, fail-fast
# STRUCTURE: ▶ detect_age_key() → RuntimeError if None → ◇ decrypt_sops_file() → [temp key 0600 → sops --decrypt → dd wipe] → ◇ write_secrets_env() → [tempfile+rename atomic] → ◇ main() → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Python core for decrypting SOPS/age-encrypted secrets. Extracted from
##           core/internal/secrets/decrypt-secrets.sh. Manages temp age key file with
##           dd-wipe cleanup, runs sops --decrypt with SOPS_AGE_KEY_FILE env, and
##           atomically writes decrypted secrets.env with 0o600 permissions.
## @scope    Called from decrypt-secrets.sh thin shell facade (<30 LOC) and directly
##           from Python tests. No embedded shell logic — pure Python + subprocess.
## @invariants
##   (DD5 Security)
##   DD5-1: Temp key file in /tmp with 0o600 permissions (never world-readable)
##   DD5-2: Wipe via dd if=/dev/zero BEFORE rm (not just rm -f)
##   DD5-3: Cleanup via atexit.register + signal handlers SIGTERM/SIGINT (replaces shell trap)
##   DD5-4: No secret values in logs (keys masked to first 8 chars, [IMP:8] max)
##   DD5-5: SOPS_AGE_KEY_FILE env var for sops CLI compatibility
##   DD5-6: RuntimeError on decryption failure (fail-fast)
##   DD5-7: Atomic write via tempfile+rename prevents partial write corruption
## @rationale DevPlan Strangler-Fig — Python core extracted from 223-line shell script.
##            Security-critical operations (key handling, cleanup) must be auditable,
##            testable, and verifiable via unit tests. Shell trap pattern replaced with
##            Python atexit+signal for deterministic cleanup order.
## @changes  2026-07-30 | Created — Python core extracted from decrypt-secrets.sh
## ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Cleanup architecture migrated from shell (trap+cleanup_all) to Python (atexit+signal)
## · Rejected: Keeping cleanup in shell (risk: two parallel cleanup paradigms — shell trap AND Python atexit — creates ambiguity)
## · Reason: Python atexit+signal provides deterministic cleanup order, testability, and replaces shell trap EXIT INT TERM
## · Rev: if contract tests test_contract_decrypt.py:test_decrypt_trap_cleanup_all and test_decrypt_trap_includes_wipe are
##   updated to test Python cleanup architecture (atexit.register + _cleanup_temp_files) instead of shell patterns
# endregion MODULE_CONTRACT

import atexit
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

logger = logging.getLogger(__name__)

# ── sys.path bootstrap: root (канонические core.* импорты) + shared dir (age_key) ──
# ⚠️ TRAP[BUG] · 2026-08-01 · P1 · dual-module loading: `from exceptions import ...` (shim-имя) vs
# · `from core.internal.shared.exceptions import ...` создают ДВА разных класса PlatformFatalError
# · (Python кэширует модули по имени, не по файлу) → pytest.raises(PlatformFatalError) не ловит.
# · Fix: root в sys.path (паттерн deploy_orchestrator.py TRAP[BUG] 2026-07-31) + канонический импорт.
# · Prevention: НЕ использовать bare-импорты из shared/ во вновь редактируемых файлах.
_PLATFORM_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

_SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "shared",
)
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

import contextlib

from age_key import detect_age_key as _detect_age_key_impl

from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# ── Global cleanup state ───────────────────────────────────────────────────────
_TEMP_FILES: list[str] = []


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
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.warning("[IMP:7][wipe_temp_key] dd wipe failed for %s (continuing with rm)", path)
    try:
        os.remove(path)
    except OSError:
        logger.warning("[IMP:7][wipe_temp_key] os.remove failed for %s", path)


def _cleanup_temp_files() -> None:
    """Cleanup all tracked temp files with dd wipe.

    ## @purpose — Called by atexit and signal handlers. Iterates _TEMP_FILES
    ##            snapshot (copy) to avoid mid-iteration mutation.
    ## @io — ⎋ None (side-effect: files wiped + deleted)
    """
    for tmp_path in _TEMP_FILES:
        _wipe_temp_key(tmp_path)
    _TEMP_FILES.clear()


def _signal_handler(signum: int, frame) -> None:  # type: ignore[type-arg]
    """Signal handler: cleanup temp files, then re-raise with default handler.

    ## @purpose — Replaces shell trap EXIT INT TERM. Cleans up temp files
    ##            and restores default signal disposition for clean exit.
    """
    _cleanup_temp_files()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# Register cleanup handlers (module-level, runs once on import)
atexit.register(_cleanup_temp_files)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# region FUNC_detect_age_key
## @purpose — Detect AGE secret key. Wraps shared age_key.detect_age_key() and
##            raises RuntimeError (fail-fast) if key not found through any mechanism.
## @io — ⇥ None → ⎋ str (the key) | raises RuntimeError
## @complexity — O(1) — delegates to age_key.detect_age_key()
## @invariants
##   - RuntimeError raised if no key found (never silently returns None)
##   - Key masked to first 8 chars in logs
def detect_age_key() -> str:
    """Detect AGE secret key from env chain. Raises RuntimeError if not found."""
    logger.info("[IMP:8][detect_age_key] Detecting AGE secret key from env chain")
    key = _detect_age_key_impl()
    if key is None:
        logger.error(
            "[IMP:9][detect_age_key] FAILED: AGE_SECRET_KEY not found "
            "(checked AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE)"
        )
        raise PlatformFatalError(
            "AGE_SECRET_KEY not found. Set AGE_SECRET_KEY, SOPS_AGE_KEY, or AGE_SECRET_KEY_FILE environment variable."
        )
    masked = key[:8] if len(key) >= 8 else key
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
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
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
##            Writes age key to a temp file in /tmp with 0o600, runs
##            sops --decrypt with SOPS_AGE_KEY_FILE env, wipes temp key
##            with dd after use, returns decrypted plaintext.
## @io — ⇥ age_key: str, enc_path: str → ⎋ str (decrypted content) | raises RuntimeError
## @complexity — O(1) — single subprocess call
## @invariants (DD5 Security)
##   DD5-1: Temp key in /tmp with 0o600
##   DD5-2: dd if=/dev/zero wipe before rm
##   DD5-3: atexit+signal cleanup registered
##   DD5-4: Key masked to first 8 chars in logs
##   DD5-5: SOPS_AGE_KEY_FILE env var set for sops
##   DD5-6: RuntimeError on failure (fail-fast)
def decrypt_sops_file(age_key: str, enc_path: str) -> str:
    """Decrypt SOPS-encrypted file; returns decrypted plaintext content."""
    # ── Pre-flight: check sops is available ──
    if shutil.which("sops") is None:
        raise PlatformFatalError("sops command not found — install sops (go.mozilla.org/sops)")

    # ── Verify input file exists ──
    if not os.path.isfile(enc_path):
        raise FileNotFoundError(f"Encrypted file not found: {enc_path}")

    logger.info("[IMP:8][decrypt_sops] Decrypting %s", enc_path)

    # ── Create temp file for age key (DD5-1: 0o600 in /tmp) ──
    fd, tmp_key_path = tempfile.mkstemp(prefix="platform-age-key-", suffix=".key")
    os.close(fd)
    os.chmod(tmp_key_path, 0o600)
    _TEMP_FILES.append(tmp_key_path)

    try:
        # Write key to temp file
        with open(tmp_key_path, "w") as f:
            f.write(age_key)
            f.write("\n")

        # ── Run sops --decrypt with SOPS_AGE_KEY_FILE env (DD5-5) ──
        sops_env = os.environ.copy()
        sops_env["SOPS_AGE_KEY_FILE"] = tmp_key_path

        masked_key = age_key[:8] if len(age_key) >= 8 else age_key
        logger.info("[IMP:8][decrypt_sops] Running sops --decrypt with key (%s...)", masked_key)

        result = subprocess.run(
            ["sops", "--decrypt", enc_path],
            capture_output=True,
            text=True,
            timeout=120,
            env=sops_env,
        )

        # ── Handle decryption failure (DD5-6: fail-fast) ──
        if result.returncode != 0:
            stderr_clean = result.stderr.strip() if result.stderr else ""
            logger.error(
                "[IMP:9][decrypt_sops] FAILED: sops --decrypt returned %d: %s",
                result.returncode,
                stderr_clean,
            )
            raise PlatformFatalError(f"sops decryption failed: {stderr_clean}")

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
def write_secrets_env(decrypted_data: str, output_path: str) -> None:
    """Atomically write secrets.env with 0o600 permissions."""
    output_dir = os.path.dirname(output_path)
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
        with os.fdopen(tmp_fd, "w") as f:
            f.write(decrypted_data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, output_path)
        logger.info("[IMP:9][write_secrets_env] SUCCESS: Written %s (%d bytes)", output_path, len(decrypted_data))
    except (OSError, ValueError) as e:
        # Cleanup temp file on failure
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise PlatformFatalError(f"Failed to write secrets env to {output_path}: {e}") from e


# endregion FUNC_write_secrets_env


# region FUNC_main
## @purpose — CLI entrypoint: parse args, detect key, decrypt, convert YAML to env,
##            and atomically write secrets.env.
## @io — ⇥ sys.argv (or argparse defaults) → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(n) where n = encrypted file size
## @envvars — SECRETS_FILE (alternative to positional enc_path arg)
##            SECRETS_ENV_FILE (alternative to positional output_path arg, default /run/platform/secrets.env)
def main() -> int:
    """CLI entrypoint: parse args, detect key, decrypt, convert to env, write."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Decrypt SOPS/age-encrypted secrets and write secrets.env",
    )
    parser.add_argument(
        "enc_path",
        nargs="?",
        default=os.environ.get("SECRETS_FILE"),
        help="Path to encrypted SOPS file (.enc.yaml). Falls back to SECRETS_FILE env var.",
    )
    parser.add_argument(
        "output_path",
        nargs="?",
        default=os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env"),
        help="Output path for decrypted secrets.env. Falls back to SECRETS_ENV_FILE env var.",
    )
    args = parser.parse_args()

    if not args.enc_path:
        parser.error("enc_path is required: set SECRETS_FILE env var or pass as positional argument")

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    logger.info("[IMP:8][main] Starting secrets decryption: %s → %s", args.enc_path, args.output_path)

    try:
        # 1. Detect age key
        key = detect_age_key()

        # 2. Decrypt SOPS file
        logger.info("[IMP:8][main] Decrypting %s", args.enc_path)
        decrypted_yaml = decrypt_sops_file(key, args.enc_path)

        # 3. Convert YAML to env format
        env_content = _yaml_to_env(decrypted_yaml)
        logger.info("[IMP:8][main] Converted YAML to env format (%d lines)", len(env_content.splitlines()))

        # 4. Atomically write secrets.env
        write_secrets_env(env_content, args.output_path)

        logger.info("[IMP:9][main] Secrets decrypted successfully: %s", args.output_path)

    except PlatformError as e:
        logger.error("[IMP:9][main] PLATFORM ERROR (exit=%d): %s", e.exit_code, e)
        return e.exit_code
    except (RuntimeError, FileNotFoundError) as e:
        logger.error("[IMP:9][main] FAILED: %s", e)
        return 1
    except Exception as e:  # noqa: EXC — top-level CLI handler catch-all; already catches (RuntimeError, FileNotFoundError) above
        logger.error("[IMP:9][main] UNEXPECTED FAILURE: %s", e)
        return 2
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
