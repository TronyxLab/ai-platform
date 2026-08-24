#!/usr/bin/env python3
# GREP_SUMMARY: crypto, htpasswd, apr1, hash, password, shared
# STRUCTURE: ▶ hash_apr1(password[, salt]) → ⊕ openssl passwd -apr1 → ⎋ str | None → ▶ generate_htpasswd_entry(user, pass) → ⎋ "user:hash"
# region MODULE_CONTRACT
## @purpose  Password hashing and htpasswd generation utilities.
##           Wraps openssl passwd -apr1 for APR1-hash compatible with nginx auth_basic.
##           Single source of truth replacing duplicate htpasswd logic in
##           secrets_manager.py and secrets.sh.
## @scope    Called from secrets_manager._ensure_htpasswd(). (secrets.sh shell-фасад
##           _ensure_htpasswd_generated удалён волна 118 B6 — 0 callers)
##           Uses subprocess for openssl — no pure-Python APR1 implementation needed.
## @invariants
##   1. hash_apr1(password) generates random salt each call — NOT idempotent
##   2. hash_apr1(password, salt) with fixed salt IS idempotent (deterministic)
##   3. generate_htpasswd_entry combines username + hash_apr1 result
##   4. Returns None on openssl failure (never raises)
##   5. No file I/O — pure hashing utility
## @rationale DevPlan 078 T3: DRIFT-S2 — htpasswd generation logic duplicated across
##            Python (secrets_manager.py) and shell (secrets.sh). Centralizing in
##            shared/crypto.py eliminates drift and enables unit tests.
## @changes  2026-07-25 | DevPlan 078 Phase B T3 — Created shared crypto module
##           2026-08-24 | REF-0007 (11-DevPlan Волна 1) — hash_apr1: пароль через stdin
##                      (openssl -stdin), значение больше НЕ в argv
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import cast

# W1-A1 (план 170): _OPENSSL_TIMEOUT=15 (локальный дубль) → DEFAULT_OPENSSL_TIMEOUT=10
# (канон shared/ssl_certs, DevPlan 117 D21/B5) — фикс рассинхрона B5-канона (см. TRAP[BUG] ниже).
from core.internal.shared.ssl_certs import DEFAULT_OPENSSL_TIMEOUT

logger = logging.getLogger(__name__)


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170)."""

    command: str
    password: str
    username: str
    salt: str


# endregion DATACLASS_CliArgs

# ⚠️ TRAP[BUG] · 2026-08-14 · P1 · _OPENSSL_TIMEOUT=15 обходил канон DEFAULT_OPENSSL_TIMEOUT=10
# · Symptom: crypto.py:31 объявлял _OPENSSL_TIMEOUT=15, тогда как B5-канон
# ·   DEFAULT_OPENSSL_TIMEOUT=10 (shared/ssl_certs, DevPlan 117 D21) применялся к openssl-вызовам
# ·   cert_orchestrator/s3_ssl_cache/nginx_harness. B5-гейт (test_openssl_timeout_uses_canon)
# ·   проверял только эти 2 файла — crypto.py был вне скоупа, рассинхрон не детектировался.
# · Root: crypto.py (модуль htpasswd/APR1) написан ранее канона D21; константа 15 скопирована
# ·   из исходного secrets_manager-кода, канонизация B5 не была применена к этому файлу.
# · Fix: литерал-константа удалена; используется DEFAULT_OPENSSL_TIMEOUT (10) — импорт из SoT.
# ·   Значение 15 в этом файле больше не существует.
# · Prevention: B5-гейт расширен на crypto.py (test_openssl_timeout_uses_canon) — канон
# ·   DEFAULT_OPENSSL_TIMEOUT enforce-ится для ВСЕХ openssl-вызовов shared-слоя.


# region FUNC_hash_apr1
## @purpose — Generate APR1 password hash via openssl passwd -apr1.
##            If salt is provided, uses deterministic salt (for idempotent verification).
##            If salt is None, generates random salt (for new password creation).
## @io — ⇥ password: str, salt: str | None → ⎋ str | None
## @complexity — O(1) + subprocess
## @invariants
##   - Returns None on openssl failure (never raises)
##   - Fixed salt → deterministic output (idempotent)
##   - Auto salt → random output each call
##   - REF-0007: password delivered via stdin (-stdin) — НИКОГДА в argv (/proc visibility)
def hash_apr1(password: str, salt: str | None = None) -> str | None:
    """Generate APR1 password hash. Returns None on failure.

    ▶ ┌password(stdin)+optional_salt┐ → ⊕ openssl passwd -apr1 -stdin → ⎋ str | None
    """
    if not password:
        logger.warning("[IMP:7][crypto] hash_apr1: empty password")
        return None

    # REF-0007 (11-DevPlan Волна 1): пароль больше НЕ в argv (`openssl passwd -apr1 <password>`
    # светился в /proc/<pid>/cmdline локальным аккаунтам) — `-stdin` + subprocess input.
    cmd = ["openssl", "passwd", "-apr1"]
    if salt:
        cmd.extend(["-salt", salt])
    cmd.append("-stdin")

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        result = subprocess.run(
            cmd,
            input=password,
            capture_output=True,
            text=True,
            timeout=DEFAULT_OPENSSL_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][crypto] openssl passwd -apr1 failed (exit=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        apr1_hash = result.stdout.strip()
        if not apr1_hash:
            logger.warning("[IMP:7][crypto] openssl passwd returned empty hash")
            return None
        logger.info("[IMP:8][crypto] APR1 hash generated successfully")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][crypto] openssl passwd timed out")
        return None
    except FileNotFoundError:
        logger.warning("[IMP:7][crypto] openssl not found — cannot generate APR1 hash")
        return None
    except OSError as e:
        logger.warning("[IMP:7][crypto] openssl OS error: %s", e)
        return None
    else:
        return apr1_hash


# endregion FUNC_hash_apr1


# region FUNC_generate_htpasswd_entry
## @purpose — Generate a full htpasswd entry (username:hash) from email/username and password.
##            Delegates to hash_apr1() for the hash.
## @io — ⇥ username: str, password: str, salt: str | None → ⎋ str | None
## @complexity — O(1) + hash_apr1
## @invariants
##   - Returns None if hash_apr1 fails
##   - Returns "username:hash" string on success
def generate_htpasswd_entry(
    username: str,
    password: str,
    salt: str | None = None,
) -> str | None:
    """Generate htpasswd entry 'username:APR1hash'. Returns None on failure.

    ▶ ┌username+password┐ → ⊕ hash_apr1 → ○ str.format → ⎋ "user:hash" | None
    """
    if not username:
        logger.warning("[IMP:7][crypto] generate_htpasswd_entry: empty username")
        return None

    password_hash = hash_apr1(password, salt)
    if password_hash is None:
        logger.warning("[IMP:7][crypto] generate_htpasswd_entry: hash_apr1 failed")
        return None

    entry = f"{username}:{password_hash}"
    logger.info("[IMP:8][crypto] htpasswd entry generated for %s", username)
    return entry


# endregion FUNC_generate_htpasswd_entry


# region FUNC_CLI
## @purpose — CLI entrypoint for standalone testing.
##            Usage: python3 crypto.py hash <password> [--salt <salt>]
##                   python3 crypto.py entry <username> <password> [--salt <salt>]
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(1) dispatch
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Password hashing utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    hash_parser = subparsers.add_parser("hash", help="Generate APR1 hash")
    hash_parser.add_argument("password", help="Password to hash")
    hash_parser.add_argument("--salt", help="Optional salt (deterministic hash)")

    entry_parser = subparsers.add_parser("entry", help="Generate htpasswd entry")
    entry_parser.add_argument("username", help="Username/email")
    entry_parser.add_argument("password", help="Password")
    entry_parser.add_argument("--salt", help="Optional salt (deterministic hash)")

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    typed_args = cast(_CliArgs, cast(object, parser.parse_args()))

    if typed_args.command == "hash":
        result = hash_apr1(typed_args.password, typed_args.salt)
        if result:
            print(result)
            sys.exit(0)
        sys.exit(1)
    elif typed_args.command == "entry":
        result = generate_htpasswd_entry(typed_args.username, typed_args.password, typed_args.salt)
        if result:
            print(result)
            sys.exit(0)
        sys.exit(1)
# endregion FUNC_CLI
