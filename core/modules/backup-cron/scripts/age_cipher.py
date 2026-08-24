#!/usr/bin/env python3
# GREP_SUMMARY: age-cipher encrypt dumps age-recipient fail-closed REF-0009 SEC-0018
# STRUCTURE: ▶ age_encrypt ┌src┐ → ◇ recipient? → ⎋ error │ ◇ age -r → ⊕ verify dst non-empty → ⎋ ok
# region MODULE_CONTRACT
"""
Client-side encryption of backup dumps with age (REF-0009, SEC-0018).

@purpose  Encrypt a local dump file with `age -r <recipient>` BEFORE it leaves the
          node to S3. Plaintext client databases must never be uploaded behind a
          single bucket key (SEC-0018 ≡ DATA-503).
@scope    core/modules/backup-cron/scripts/; imported by backup_postgres.py (nightly
          pipeline) and spool_retry.py (daily spool rescan). Container module —
          0 imports from core/internal (same contract as backup_config.py).
@input    src path, dst path, recipient (age public key string), runner DI-seam.
@output   bool success; on success dst exists and is non-empty.
@invariants
  - Fail-closed encryption: no AGE_RECIPIENT → NO upload of plaintext anywhere;
    caller keeps the dump in spool and alerts (C1 semantics: local backup safe).
  - Recipient is a PUBLIC key (safe for env); the private key NEVER enters the
    backup-cron container. Name AGE_RECIPIENT reuses the age-key-backup convention
    (AGE_SECRET_KEY stays frozen/untouched).
  - On encryption failure the plaintext source file is NOT deleted by this module.
  - DI-seam runner (DevPlan 167 D2 pattern): fake-subprocess object in tests,
    real subprocess module in production.
@rationale Q: why `age` CLI and not a Python lib? A: stdlib has no age support;
          adding a crypto dependency to a container module violates minimal-diff;
          Debian bookworm ships `age`; CLI is auditable and matches restore runbook.
@changes  2026-08-25 | REF-0009 (meta-refactoring W2) — created
"""
# endregion MODULE_CONTRACT

import logging
import pathlib
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = ["age_encrypt", "verify_encrypted"]


# region DATA_SubprocessLike
class _RunResultLike(Protocol):
    """Контракт subprocess.CompletedProcess (run-only)."""

    returncode: int


class RunnerLike(Protocol):
    """Контракт DI-runner (subprocess-модуль | fake): run + DEVNULL.

    ## @purpose  Типизированная граница runner-параметра age_encrypt: реальный
    ##            subprocess-модуль или fake тестов. DEVNULL — read-only property
    ##            (ковариантность: int-константа subprocess совместима).
    """

    @property
    def DEVNULL(self) -> object: ...

    def run(self, args: list[str], **kwargs: object) -> _RunResultLike: ...


# endregion DATA_SubprocessLike


# region FUNC_age_encrypt
## @purpose  Encrypt src → dst via `age -r <recipient>` (runner DI-seam).
## @io       ⇥ (src, dst, recipient, runner=None) → ⎋ bool (dst verified non-empty)
## @complexity O(1) + 1 subprocess
## @invariants  rc != 0 или пустой dst → False (plaintext НЕ удаляется вызывающим кодом
##              только по True); отсутствие age-бинарника → FileNotFoundError → False+CRITICAL
def age_encrypt(
    src: str,
    dst: str,
    recipient: str,
    runner: RunnerLike | None = None,
) -> bool:
    """Encrypt *src* into *dst* with ``age -r``; verify dst exists and is non-empty.

    Returns True only when age exited 0 AND dst is a non-empty regular file.
    Never deletes the plaintext source (caller decides after True).
    """
    if not recipient:
        logger.critical("[IMP:9][age_cipher][encrypt] No AGE_RECIPIENT — refusing to produce unencrypted artifact")
        return False
    runner_mod = runner if runner is not None else _default_runner()
    try:
        rc = runner_mod.run(
            ["age", "-r", recipient, "-o", dst, src],
            stdout=runner_mod.DEVNULL,
            stderr=runner_mod.DEVNULL,
            check=False,
        ).returncode
    except FileNotFoundError as exc:
        logger.critical("[IMP:9][age_cipher][encrypt] age binary not found: %s", exc)
        return False
    if rc != 0:
        logger.error("[IMP:9][age_cipher][encrypt] age exited %d: src=%s dst=%s", rc, src, dst)
        return False
    return verify_encrypted(dst)


def _default_runner() -> RunnerLike:
    """Импорт реального subprocess отложенно (контейнерный контракт, тестовый DI)."""
    import subprocess

    return subprocess  # pyright: ignore[reportReturnType] — модуль удовлетворяет RunnerLike структурно


# endregion FUNC_age_encrypt


# region FUNC_verify_encrypted
## @purpose  Post-encrypt guard: dst существует и не пуст (0-байтовый .age = битый артефакт).
## @io       ⇥ dst: str → ⎋ bool
## @complexity O(1)
def verify_encrypted(dst: str) -> bool:
    """True iff dst is a regular non-empty file."""
    try:
        size = pathlib.Path(dst).stat().st_size
    except OSError as exc:
        logger.error("[IMP:9][age_cipher][verify] Encrypted artifact missing: %s (%s)", dst, exc)
        return False
    if size == 0:
        logger.error("[IMP:9][age_cipher][verify] Encrypted artifact is empty: %s", dst)
        return False
    logger.info("[IMP:8][age_cipher][verify] Encrypted artifact OK: %s (%d bytes)", dst, size)
    return True


# endregion FUNC_verify_encrypted
