# GREP_SUMMARY: lock, ai-instructions.lock, sha256, drift-check, write-lock, check-drift, yaml, git-sha
# STRUCTURE: ┌emitted files┐ → ○ sha256 each → ⊕ YAML ai-instructions.lock → ○ check: re-hash → ◇ drift/missing ? ⊕ messages : ⊕ clean → ⎋ (ok, messages)
# region MODULE_CONTRACT
## @purpose  Persist a deterministic lock manifest (path, sha256, source) per emitted file
##   and detect drift between the lock and the on-disk outputs
## @scope    ai-instructions.lock write/read, sha256 computation, git HEAD resolution
## @invariants
##   - Lock YAML lists: canon_version, platform_version (consumer git HEAD or "unknown"), files
##   - check_drift returns (False, [...]) when the lock is missing, any file is missing,
##     or any file's sha256 differs; messages use exact drift:/missing: prefixes
##   - No timestamps in the lock — the file must be byte-deterministic for the same inputs
## @rationale The lock is the drift contract between the compiler and the consumer:
##   check_drift turns silent manual edits of emitted files into an exit-1 signal
# endregion MODULE_CONTRACT

import hashlib
import logging
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

LOCK_FILENAME = "ai-instructions.lock"


class LockError(Exception):
    """Raised when the lock file cannot be written or parsed."""


def _norm_version(value: str) -> str:
    return value.strip().removeprefix("v")


def sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file, chunked for memory safety."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha(consumer_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(consumer_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# region FUNC_write_lock
## @purpose  Write ai-instructions.lock describing every managed output file
## @io       in: consumer root, canon version, [(rel_path, source)], optional platform sha; out: lock Path
## @complexity O(files × size)
def write_lock(
    consumer_root: Path,
    canon_version: str,
    files: list[tuple[str, str]],
    platform_version: str | None = None,
) -> Path:
    """▶ ┌files┐ → ○ git sha → ○ sha256 each → ⊕ YAML dump → ⎋ lock path"""
    platform_sha = platform_version if platform_version is not None else _git_sha(consumer_root)
    records: list[dict[str, str]] = []
    for rel, source in files:
        p = consumer_root / rel
        digest = sha256_file(p) if p.is_file() else ""
        records.append({"path": rel, "sha256": digest, "source": source})
    data = {
        "canon_version": _norm_version(canon_version),
        "platform_version": platform_sha,
        "files": records,
    }
    lock_path = consumer_root / LOCK_FILENAME
    try:
        lock_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write lock file {lock_path}: {exc}"
        raise LockError(msg) from exc
    logger.info("[IMP:9][LOCK][WRITTEN] %s (%d files)", lock_path, len(records))
    return lock_path
# endregion FUNC_write_lock


# region FUNC_check_drift
## @purpose  Recompute output hashes and compare against the lock manifest
## @io       in: consumer root; out: (ok: bool, messages: list[str])
## @complexity O(files × size)
def check_drift(consumer_root: Path) -> tuple[bool, list[str]]:
    """▶ ┌lock?┐ → ◇ missing ? ⊕ [lock file missing] : ○ re-hash each → ◇ drift/missing ? ⊕ messages : ⊕ clean → ⎋ (ok, messages)"""
    lock_path = consumer_root / LOCK_FILENAME
    if not lock_path.is_file():
        logger.warning("[IMP:7][LOCK][CHECK] lock file missing: %s", lock_path)
        logger.info("[IMP:9][LOCK][CHECK] lock missing (1 problem)")
        return False, [f"lock file missing: {lock_path}"]

    try:
        data = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        logger.error("[IMP:10][LOCK][CHECK] cannot parse lock: %s", exc)
        return False, [f"lock parse error: {exc}"]

    messages: list[str] = []
    ok = True
    for record in (data or {}).get("files", []):
        rel = str(record.get("path", ""))
        expected = str(record.get("sha256", ""))
        target = consumer_root / rel
        if not target.is_file():
            messages.append(f"missing: {rel}")
            ok = False
            continue
        actual = sha256_file(target)
        if actual != expected:
            messages.append(f"drift: {rel} expected={expected} actual={actual}")
            ok = False
    logger.info("[IMP:9][LOCK][CHECK] %s", "clean" if ok else f"{len(messages)} problem(s)")
    return ok, messages
# endregion FUNC_check_drift
