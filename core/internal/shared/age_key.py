#!/usr/bin/env python3
# GREP_SUMMARY: age-key, detect-age-key, AGE_SECRET_KEY, SOPS_AGE_KEY, AGE_SECRET_KEY_FILE, shared
# STRUCTURE: ▶ detect_age_key() → ◇ AGE_SECRET_KEY env? → ◇ SOPS_AGE_KEY env? → ◇ AGE_SECRET_KEY_FILE file? → ⊕ masked log → ⎋ str | None
# region MODULE_CONTRACT
## @purpose  Detect AGE secret key from environment chain:
##           AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE content.
##           Single source of truth replacing duplicate shell detect_age_key() functions
##           in bootstrap.sh and node-update.sh.
## @scope    Called from entrypoint shell scripts that need AGE key for SOPS decryption.
##           No subprocess calls — pure env/file I/O.
## @invariants
##   1. Detection chain: AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE (first non-empty wins)
##   2. Returns None (not empty string) when no key found — caller distinguishes "not set" from "empty"
##   3. Logs masked (first 8 chars) key source at IMP:8 for audit
##   4. No side effects — does NOT export env vars
##   5. AGE_SECRET_KEY_FILE is read as first line (head -1 equivalent)
##   6. No AGE key value ever appears in plaintext logs (only first 8 chars masked)
## @canonical_format AGE-key format:
##           • AGE_SECRET_KEY=AGE-SECRET-KEY-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
##           • The key is always stored as an environment variable (never bare)
##           • AGE_SECRET_KEY_FILE contains the raw key (first line, no prefix — same AGE-SECRET-KEY-xxxxxxxx… format)
##           • SOPS_AGE_KEY is a deprecated fallback (same AGE-SECRET-KEY-xxxxxxxx… format)
##           • All three locations (age_key.py, decrypt-secrets.sh, platform-secrets/install.sh) MUST use the same format
## @rationale DevPlan 078 T1: DRIFT-S1 — shell had 2 identical detect_age_key() functions
##            (bootstrap.sh + node-update.sh). Centralizing in shared/age_key.py eliminates
##            copy-paste drift and enables unit testing.
## @changes  2026-07-25 | DevPlan 078 Phase B T1 — Created shared detect_age_key module
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# region FUNC_detect_age_key
## @purpose — Detect AGE secret key from env chain. Mirrors shell detect_age_key() logic:
##            AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE content.
## @io — ⇥ None → ⎋ str | None (None = not found)
## @complexity — O(1) — 3 env/file lookups
## @invariants
##   - Returns None (never empty string) on not found
##   - Logs masked (first 8 chars) at IMP:8
##   - AGE_SECRET_KEY_FILE is read as single line (strip newline)
def detect_age_key() -> str | None:
    """Detect AGE secret key from env chain.

    ▶ ◇ AGE_SECRET_KEY env? → ◇ SOPS_AGE_KEY env? → ◇ AGE_SECRET_KEY_FILE? → ⎋ str | None

    Returns the key string, or None if not found through any mechanism.
    """
    # ── Check 1: AGE_SECRET_KEY env ──
    # Returns key in canonical AGE-SECRET-KEY-xxxxxxxx… format (with prefix)
    key = os.environ.get("AGE_SECRET_KEY", "")
    if key:
        _log_masked("AGE_SECRET_KEY", key, "environment")
        return key

    # ── Check 2: SOPS_AGE_KEY env (fallback) ──
    # SOPS_AGE_KEY uses the same AGE-SECRET-KEY-xxxxxxxx… canonical format
    key = os.environ.get("SOPS_AGE_KEY", "")
    if key:
        _log_masked("AGE_SECRET_KEY", key, "SOPS_AGE_KEY env fallback")
        return key

    # ── Check 3: AGE_SECRET_KEY_FILE content ──
    # File contains the raw key (first line, no prefix — same AGE-SECRET-KEY-xxxxxxxx… format)
    file_path = os.environ.get("AGE_SECRET_KEY_FILE", "")
    if file_path:
        try:
            with open(file_path) as f:
                key = f.readline().strip()
            if key:
                _log_masked("AGE_SECRET_KEY", key, f"file {file_path}")
                return key
            logger.warning(
                "[IMP:8][age_key] AGE_SECRET_KEY_FILE=%s is empty",
                file_path,
            )
        except (OSError, FileNotFoundError) as e:
            logger.warning(
                "[IMP:8][age_key] Cannot read AGE_SECRET_KEY_FILE=%s: %s",
                file_path,
                e,
            )

    logger.warning("[IMP:8][age_key] AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy")
    return None


# endregion FUNC_detect_age_key


# region FUNC__log_masked
## @purpose — Log AGE key discovery with masked value (first 8 chars).
##            Prevents full key leakage in logs.
## @io — ⇥ key_name: str, key_value: str, source: str → ⎋ None
## @complexity — O(1)
def _log_masked(key_name: str, key_value: str, source: str) -> None:
    """Log AGE key discovery with masked value."""
    masked = key_value[:8] if len(key_value) >= 8 else key_value
    logger.info(
        "[IMP:8][age_key] %s found in %s (%s...)",
        key_name,
        source,
        masked,
    )


# endregion FUNC__log_masked


# region FUNC_CLI
## @purpose — CLI entrypoint for standalone testing. Prints key to stdout or exits 1.
## @io — ⇥ sys.argv → ⎋ exit code (0 = key found, 1 = not found)
## @complexity — O(1)
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    result = detect_age_key()
    if result:
        print(result)
        sys.exit(0)
    else:
        print("AGE_SECRET_KEY not found", file=sys.stderr)
        sys.exit(1)
# endregion FUNC_CLI
