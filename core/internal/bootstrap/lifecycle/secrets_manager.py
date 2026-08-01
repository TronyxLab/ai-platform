#!/usr/bin/env python3
# GREP_SUMMARY: secrets-manager, autogen-secrets, manifest, ensure-secrets, sops, htpasswd, cleanup-proxy, tor-enabled, secrets-env-parser, salt-idempotent
# STRUCTURE: ▶ ensure_secrets → source_secrets_env → _read_manifest → _generate_secret → _persist_to_sops → _ensure_htpasswd → ⎋ CLI
#            ▶ cleanup_secrets_env → ◇ parse → ◇ TOR_ENABLED≠"true"? → ⊕ filter proxy → ⊕ atomic write (0o600) → ⎋ dict
#            ▶ _write_htpasswd_file → ◇ existing? → ⊕ extract $apr1$SALT$ → ⊕ recompute fixed-salt → ◇ match? → ⎋ no-op|write
# region MODULE_CONTRACT
## @purpose  Auto-generate missing tier=generated secrets from secrets-manifest.yaml or fallback hardcoded list.
##           Port of core/lib/secrets.sh:step_12b_ensure_secrets() lines 298-411 plus source_secrets_env()
##           and htpasswd generation. Designed for bootstrap pipeline step 12b.
## @scope    core/internal/bootstrap/lifecycle/ — secrets management for bootstrap pipeline.
##           Responsibilities: (1) read manifest and fill gaps, (2) parse secrets.env,
##           (3) generate htpasswd from platform credentials, (4) proxy-var cleanup of secrets.env
##           (DevPlan 102 — cleanup_secrets_env + htpasswd CLI for thin shell facades).
## @invariants
##   1. Non-fatal: returns partial list on failure, NEVER raises exceptions
##   2. Existing secrets are NOT overwritten — only missing (empty) secrets are generated
##   3. gen_command executed via subprocess (bash -c) with 30s timeout
##   4. sops --set persistence is non-fatal on failure
##   5. htpasswd generation called after secrets (requires PLATFORM_MASTER_PASSWORD)
##   6. sourced secrets.env values take precedence over manifest/hardcoded defaults
##   7. htpasswd idempotency: existing file salt ($apr1$SALT$) is reused for deterministic
##      comparison — never rewrites on unchanged credentials (TRAP[BUG] 2026-07-31)
##   8. cleanup_secrets_env: no-op on missing file (returns {}), never raises — logs warnings
## @rationale  Python port of shell secrets logic. Enables unit-testing, typed returns,
##             and consistent error handling without relying on bash eval() for secret generation.
## @changes  2026-07-25 | W5-E6 secrets_manager — created from secrets.sh step_12b decomposition
## @changes  2026-07-30 | DevPlan 086 — source_secrets_env() delegates to shared secrets_env_parser.parse()
## @changes  2026-07-31 | DevPlan 102 — cleanup_secrets_env(), htpasswd CLI, salt-extraction idempotency fix;
##             import: canonical core.internal.shared form kept (gate test_gate_secrets_parser_import) +
##             ModuleNotFoundError fallback to shared-dir bootstrap so the script runs standalone as CLI
##             (the bare package import previously crashed outside pytest — ModuleNotFoundError)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ── Shared modules import ──
# Canonical package import (DevPlan 086 — gate test_gate_secrets_parser_import enforces the
# `core.internal.shared.secrets_env_parser` form for all direct consumers).
# Fallback: standalone CLI execution (shell facades: python3 .../secrets_manager.py) runs
# outside pytest where the `core` package is NOT importable — bootstrap the shared dir and
# import the module directly (pattern: decrypt_secrets.py L44-54).
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Module-level `core.internal` import crashed standalone CLI
# · Symptom: `python3 secrets_manager.py cleanup|htpasswd|ensure` → ModuleNotFoundError:
# ·   No module named 'core' (script sys.path[0] = script dir, `core` package unreachable).
# ·   The plan's shell facades (step_10 cleanup, htpasswd) depend on standalone invocation.
# · Root: (a) bare `from core.internal.shared.secrets_env_parser import` — works only under
# ·   pytest (rootdir in sys.path); (b) legacy `_ensure_htpasswd` sys.path bootstrap used
# ·   4× dirname from lifecycle/ → `core/shared` (NONEXISTENT) → `from crypto import ...`
# ·   failed whenever _ensure_htpasswd was actually invoked (production step_12b).
# · Fix: canonical import kept for the gate + ModuleNotFoundError fallback to shared-dir
# ·   bootstrap; _SHARED_DIR computed with 3× dirname (core/internal/shared) and reused
# ·   by _write_htpasswd_file.
# · Rev: if secrets_manager.py moves out of core/internal/bootstrap/lifecycle/, recompute
# ·   _SHARED_DIR relative path; if the gate test's import pattern changes, sync both arms.
# ⚠️ TRAP[BUG] · 2026-08-01 · P1 · bare-fallback загрузка shared-модулей ломает их канонические
# · импорты (T2: shared-модули импортируют core.internal.shared.exceptions на module level)
# · Symptom: `python3 secrets_manager.py htpasswd` → ModuleNotFoundError: No module named 'core'
# ·   внутри secrets_env_parser.py (bare-имя из shared dir не даёт core на sys.path).
# · Fix: безусловный bootstrap project root (паттерн deploy_orchestrator TRAP[BUG] 2026-07-31) —
# ·   канонический импорт работает всегда; fallback остаётся defensive.
_SHARED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "shared",
)
_PLATFORM_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

try:
    from core.internal.shared.secrets_env_parser import parse as parse_secrets_env
    from core.internal.shared.secrets_env_parser import write as write_secrets_env
    from core.internal.shared.secrets_manifest_reader import iter_secrets as _iter_manifest_secrets
except ModuleNotFoundError:
    if _SHARED_DIR not in sys.path:
        sys.path.insert(0, _SHARED_DIR)
    from secrets_env_parser import parse as parse_secrets_env
    from secrets_env_parser import write as write_secrets_env
    from secrets_manifest_reader import iter_secrets as _iter_manifest_secrets

logger = logging.getLogger(__name__)


# region FUNC_source_secrets_env
## @purpose — Parse a secrets.env file into a dict. Delegates to shared secrets_env_parser.parse()
##            (DevPlan 086). Preserves backward-compat: returns empty dict on failure (never raises).
## @io — ⇥ secrets_env: path to secrets.env file → ⎋ dict[str, str]
## @complexity — O(N) where N = lines in file (delegated)
## @invariants
##   - Returns empty dict if file not found or unreadable (backward compat wrapper)
##   - Actual parsing logic in shared secrets_env_parser module
def source_secrets_env(secrets_env: str) -> dict[str, str]:
    """Parse secrets.env key=value file into dict. Returns empty dict on failure.
    Delegates to shared secrets_env_parser.parse() (DevPlan 086)."""
    logger.info("[IMP:7][secrets_manager] Delegating source_secrets_env to shared secrets_env_parser.parse()")
    try:
        result = parse_secrets_env(secrets_env)
        logger.info(
            "[IMP:9][secrets_manager] source_secrets_env: parsed %d entries via shared module",
            len(result),
        )
        return result
    except FileNotFoundError:
        logger.info("[IMP:7][secrets_manager] Secrets env file not found: %s — returning empty dict", secrets_env)
        return {}
    except (OSError, ValueError) as e:
        logger.warning("[IMP:7][secrets_manager] Cannot read %s: %s — returning empty dict", secrets_env, e)
        return {}


# endregion FUNC_source_secrets_env


# region FUNC_cleanup_secrets_env
## @purpose — Read secrets.env, conditionally strip HTTP_PROXY/HTTPS_PROXY when
##            TOR_ENABLED != "true", write back atomically (tmp+rename, 0o600).
##            DevPlan 102 TASK-2 — replaces shell source+sed logic in step_10_decrypt_secrets.
## @io — ⇥ secrets_env_path: str, tor_enabled: str (default "false") → ⎋ dict[str, str]
##       (parsed secrets AFTER cleanup; {} if file missing)
## @complexity — O(N) where N = vars in secrets.env (parse + write delegated)
## @invariants
##   - No-op if file doesn't exist — returns {} without error
##   - Never raises — logs warnings on parse/write I/O errors
##   - Only HTTP_PROXY/HTTPS_PROXY (uppercase) are removed, matching legacy sed behavior
##   - Atomic write via shared secrets_env_parser.write() (tempfile + os.replace, 0o600)
##   - File is NOT rewritten when nothing is removed (byte-identical preservation)
## @rationale — Proxy cleanup was shell sed logic in step_10 (DevPlan 102 P1). Moving to
##              Python makes it testable and reuses the canonical secrets_env_parser.
def cleanup_secrets_env(
    secrets_env_path: str,
    tor_enabled: str = "false",
) -> dict[str, str]:
    """Read secrets.env, conditionally strip proxy vars, write back atomically.

    ▶ ┌secrets_env_path┐ → ◇ parse → ◇ TOR_ENABLED≠"true"? → filter proxy →
      ⊕ atomic write (tmp+rename, 0o600) → ⎋ dict[str, str]

    Returns: parsed secrets dict AFTER cleanup.
    No-op if file doesn't exist (returns empty dict).
    Never raises — logs warnings on I/O errors.
    """
    env_path = Path(secrets_env_path)
    if not env_path.is_file():
        logger.info("[IMP:7][secrets_manager] cleanup: %s not found — no-op", secrets_env_path)
        return {}

    try:
        env_vars = parse_secrets_env(str(env_path))
    except (OSError, ValueError) as e:
        logger.warning("[IMP:7][secrets_manager] cleanup: cannot parse %s: %s", secrets_env_path, e)
        return {}

    removed: list[str] = []
    if tor_enabled != "true":
        for proxy_var in ("HTTP_PROXY", "HTTPS_PROXY"):
            if proxy_var in env_vars:
                del env_vars[proxy_var]
                removed.append(proxy_var)

    if removed:
        try:
            write_secrets_env(str(env_path), env_vars)
        except (OSError, TypeError) as e:
            logger.warning("[IMP:7][secrets_manager] cleanup: cannot write %s: %s", secrets_env_path, e)
            return {}
        logger.info(
            "[IMP:9][secrets_manager] cleanup: removed %s from %s (TOR_ENABLED=%s)",
            ", ".join(removed),
            secrets_env_path,
            tor_enabled,
        )
    else:
        logger.info(
            "[IMP:8][secrets_manager] cleanup: no proxy vars to remove in %s (TOR_ENABLED=%s)",
            secrets_env_path,
            tor_enabled,
        )

    return env_vars


# endregion FUNC_cleanup_secrets_env


# region FUNC__read_manifest
## @purpose — Read secrets-manifest.yaml and extract tier=generated secrets as a list of dicts.
##            Delegates to shared secrets_manifest_reader.iter_secrets (DevPlan 116 T4, U-33).
##            STRICT: missing/malformed manifest RAISES — hardcoded fallback list removed
##            (invariant 7 — fail-visible instead of silent divergence).
## @io — ⇥ manifest_path: str → ⎋ list[dict[str, Any]] ⚡ raise FileNotFoundError/ValueError
## @complexity — O(N) where N = YAML document size (delegated)
## @invariants
##   - Raises on missing/malformed manifest (no `return []` fallback)
##   - Filters only entries with tier == "generated"
##   - Each entry requires name and gen_command keys
def _read_manifest(manifest_path: str) -> list[dict[str, Any]]:
    """Read secrets-manifest.yaml, return tier=generated secrets. Raises on missing manifest."""
    logger.info("[IMP:8][secrets_manager] Reading generated secrets from manifest: %s", manifest_path)
    secrets = _iter_manifest_secrets(manifest_path)
    generated: list[dict[str, Any]] = [
        s for s in secrets if s.get("tier") == "generated" and s.get("name") and s.get("gen_command")
    ]
    logger.info("[IMP:9][secrets_manager] Manifest has %d tier=generated secrets", len(generated))
    return generated


# endregion FUNC__read_manifest


# region FUNC__generate_secret
## @purpose — Generate a single secret value via subprocess. Executes gen_command as a bash command.
## @io — ⇥ var_name: str, gen_command: str → ⎋ str | None (None on failure)
## @complexity — O(1) + subprocess
## @invariants
##   - Returns None on any failure (never raises)
##   - Uses bash -c with 30s timeout
##   - Strips trailing newline from output
def _generate_secret(var_name: str, gen_command: str) -> str | None:
    """Generate a secret value via subprocess. Returns None on failure."""
    logger.info("[IMP:8][secrets_manager] Generating %s via: %s", var_name, gen_command)
    try:
        result = subprocess.run(
            ["bash", "-c", gen_command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][secrets_manager] gen_command for %s failed (exit=%d): %s",
                var_name,
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        value = result.stdout.strip()
        if not value:
            logger.warning("[IMP:7][secrets_manager] gen_command for %s returned empty", var_name)
            return None
        logger.info("[IMP:9][secrets_manager] Generated %s successfully", var_name)
        return value
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][secrets_manager] gen_command for %s timed out", var_name)
        return None
    except FileNotFoundError as e:
        logger.warning("[IMP:7][secrets_manager] Command not found for %s: %s", var_name, e)
        return None
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] OS error generating %s: %s", var_name, e)
        return None


# endregion FUNC__generate_secret


# region FUNC__persist_to_sops
## @purpose — Persist a single generated secret to the SOPS-encrypted file via sops --set.
##            Non-fatal: logs warning on failure.
## @io — ⇥ var_name: str, var_value: str, enc_file: str → ⎋ bool
## @complexity — O(1) + subprocess
## @invariants
##   - Returns False on any failure (never raises)
##   - Requires enc_file to exist and sops binary to be available
def _persist_to_sops(var_name: str, var_value: str, enc_file: str) -> bool:
    """Persist a secret to SOPS via sops --set. Returns True on success."""
    if not var_value:
        return False
    if not os.path.isfile(enc_file):
        logger.warning(
            "[IMP:7][secrets_manager] sops enc file not found: %s — generated secrets NOT persisted",
            enc_file,
        )
        return False
    try:
        result = subprocess.run(
            ["sops", "--set", f'["{var_name}"] "{var_value}"', enc_file],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][secrets_manager] sops --set failed for %s — value in env but NOT persisted: %s",
                var_name,
                result.stderr.strip()[:200],
            )
            return False
        logger.info("[IMP:9][secrets_manager] sops --set succeeded for %s", var_name)
        return True
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][secrets_manager] sops --set timed out for %s", var_name)
        return False
    except FileNotFoundError:
        logger.warning("[IMP:7][secrets_manager] sops binary not found — generated secrets NOT persisted")
        return False
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] sops --set OS error for %s: %s", var_name, e)
        return False


# endregion FUNC__persist_to_sops


# region FUNC_ensure_secrets
## @purpose — Main entrypoint: read manifest (or fallback), generate missing tier=generated secrets,
##            set them in os.environ, persist to secrets.env and optionally to SOPS.
##            After all secrets, calls _ensure_htpasswd() for status-page auth.
## @io — ⇥ manifest_path: str, secrets_env: str, persist_to_sops: bool → ⎋ list[str] (generated var names)
## @complexity — O(N * M) where N = secrets to check, M = subprocess per generation
## @invariants
##   - Never raises — returns partial list on failure
##   - Existing secrets (already in os.environ or secrets.env) are NOT overwritten
##   - Appends generated VAR=VALUE pairs to secrets_env file
##   - sops persistence is optional (persist_to_sops param)
##   - Calls _ensure_htpasswd after all secrets generated
def ensure_secrets(
    manifest_path: str = "",
    secrets_env: str = "/run/platform/secrets.env",
    persist_to_sops: bool = True,
) -> list[str]:
    """Ensure all required secrets exist. Generates missing ones. Returns list of generated names."""
    generated: list[str] = []

    # ── Step 1: Source existing secrets.env into os.environ ──
    env_vars = source_secrets_env(secrets_env)
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value

    # ── Step 2: Read manifest for tier=generated secrets (STRICT — raises if missing) ──
    # Hardcoded fallback list removed (DevPlan 116 T4, U-33): manifest is always
    # delivered with core/ — silent fallback was a drift vector («gate зелёный, система врёт»).
    secrets_to_process: list[dict[str, Any]] = _read_manifest(manifest_path)
    logger.info(
        "[IMP:9][secrets_manager] Processing %d generated secrets from manifest",
        len(secrets_to_process),
    )

    # ⚠️ TRAP[BUG] · 2026-07-25 · P1 · Append-mode → duplicate secrets on repeated --force runs
    # · Symptom: secrets.env grew with duplicate lines (same VAR=value appended on each run).
    # ·   `source secrets.env` reads the LAST occurrence → first bootstrap's key lost.
    # · Root: `open(secrets_env, "a")` in per-secret loop (line 312, old code). Each generated
    # ·   secret was appended individually. On --force re-run, os.environ was empty → all 7
    # ·   secrets regenerated → appended AGAIN. After 3 runs: 21 lines, 3 values per key.
    # · Fix (DevPlan 072): collect all generated values → merge with existing env_vars →
    # ·   atomic write (tmp + rename). Single `open(..., "w")`, not per-secret append.
    # · Prevention: test_ensure_secrets_idempotent verifies file unchanged after 3 calls.
    # ── Step 3: For each secret, check if present; if not, generate ──
    # 💼 TRAP[BUSINESS] · 2026-07-25 · HI · Secrets overwrite MUST preserve non-generated entries
    generated_vars: dict[str, str] = {}
    for secret in secrets_to_process:
        var_name: str = secret["name"]
        gen_command: str = secret.get("gen_command", "")

        # Check existing env var
        current = os.environ.get(var_name, "")
        if current:
            logger.info("[IMP:8][secrets_manager] %s already set — skipping", var_name)
            continue

        if not gen_command:
            logger.warning("[IMP:7][secrets_manager] %s has no gen_command — skipping", var_name)
            continue

        value = _generate_secret(var_name, gen_command)
        if value is None:
            logger.warning("[IMP:7][secrets_manager] Failed to generate %s — continuing", var_name)
            continue

        # Set in os.environ
        os.environ[var_name] = value
        generated.append(var_name)
        generated_vars[var_name] = value

        logger.info(
            "[IMP:9][secrets_manager] Auto-generated %s (MUST be added to SOPS for production)",
            var_name,
        )

    # ── Step 3.5: Atomic overwrite — merge existing + generated → write once ──
    if generated_vars:
        try:
            secrets_path = Path(secrets_env)
            secrets_path.parent.mkdir(parents=True, exist_ok=True)

            # Build the complete env file content: existing + newly generated
            # env_vars from Step 1 already contains ALL existing entries
            merged: dict[str, str] = dict(env_vars)  # copy existing (non-generated + previously generated)
            merged.update(generated_vars)  # add/overwrite newly generated

            # Atomic write: write to tmp, then rename
            tmp_path = secrets_path.with_suffix(".env.tmp")
            with open(tmp_path, "w") as f:
                for key, val in merged.items():
                    f.write(f"{key}={val}\n")

            # Preserve file permissions if file exists
            if secrets_path.exists():
                existing_mode = secrets_path.stat().st_mode
                tmp_path.chmod(existing_mode)
            else:
                tmp_path.chmod(0o600)

            tmp_path.replace(secrets_path)
            logger.info(
                "[IMP:9][secrets_manager] Atomic write: %d entries → %s (%d new)",
                len(merged),
                secrets_env,
                len(generated_vars),
            )
        except OSError as e:
            logger.warning(
                "[IMP:7][secrets_manager] Cannot write secrets.env: %s — "
                "secrets are in os.environ but NOT persisted to file",
                e,
            )

    # ── Step 4: sops --set persistence (optional, non-fatal) ──
    if persist_to_sops and generated:
        node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
        node_name = os.environ.get("NODE_NAME", "")
        if node_name:
            enc_file = os.path.join(node_configs_dir, "secrets", f"{node_name}.enc.yaml")
            for gvar in generated:
                gval = os.environ.get(gvar, "")
                if gval:
                    _persist_to_sops(gvar, gval, enc_file)
        else:
            logger.info(
                "[IMP:7][secrets_manager] NODE_NAME not set — skipping sops persistence",
            )

    # ── Step 5: Generate htpasswd (needs PLATFORM_MASTER_PASSWORD) ──
    _ensure_htpasswd(secrets_env)

    if generated:
        logger.info(
            "[IMP:9][secrets_manager] Generated %d secrets: %s",
            len(generated),
            ", ".join(generated),
        )
        logger.info(
            "[IMP:7][secrets_manager] These are EPHEMERAL — re-encrypt SOPS with real values for production",
        )
    else:
        logger.info("[IMP:9][secrets_manager] All required secrets present — nothing to generate")

    return generated


# endregion FUNC_ensure_secrets


# region FUNC__extract_apr1_salt
## @purpose — Extract salt from an existing APR1 htpasswd entry ($apr1$SALT$HASH).
##            Returns "" when the entry is missing or not an apr1 hash (unparseable).
## @io — ⇥ entry: str → ⎋ str (salt) | "" (unparseable)
## @complexity — O(1) — single split
## @invariants
##   - Empty entry → ""
##   - Only accepts apr1 structure: parts[0]=user, parts[1]="apr1", parts[2]=salt, parts[3]=hash
##   - Non-apr1 hashes (bcrypt $2y$, etc.) → "" → caller regenerates
## @rationale — Port of shell `cut -d'$' -f3` (secrets.sh L225) with added apr1 validation:
##              field-3 extraction without structure check would treat a bcrypt salt as apr1 salt.
def _extract_apr1_salt(entry: str) -> str:
    """Extract salt from an APR1 htpasswd entry ($apr1$SALT$HASH). '' if unparseable."""
    if not entry:
        return ""
    parts = entry.split("$")
    if len(parts) >= 4 and parts[1] == "apr1" and parts[2]:
        return parts[2]
    return ""


# endregion FUNC__extract_apr1_salt


# region FUNC__write_htpasswd_file
## @purpose — Generate .htpasswd-platform from explicit credentials. Core of both
##            _ensure_htpasswd() (env-based) and the htpasswd CLI action (DevPlan 102 TASK-1).
##            Idempotent: existing file salt is extracted and reused for deterministic
##            comparison — file is only rewritten when credentials changed.
## @io — ⇥ email: str, password: str, htpasswd_file: str (default /run/platform/.htpasswd-platform)
##       → ⎋ bool
## @complexity — O(1) + hash_apr1 from shared.crypto
## @invariants
##   - Non-fatal: returns False on failure, never raises (OSError caught)
##   - First call (no file): random salt via generate_htpasswd_entry(email, password)
##   - Subsequent calls: extract salt from existing file → recompute with fixed salt →
##     skip if identical (idempotent, md5-stable)
##   - Unparseable existing entry (no apr1 salt) → regenerate with random salt
##   - Exports HTPASSWD_FILE into os.environ on success
def _write_htpasswd_file(
    email: str,
    password: str,
    htpasswd_file: str = "/run/platform/.htpasswd-platform",
) -> bool:
    """Generate .htpasswd-platform from explicit credentials. Returns True on success."""
    # Import shared crypto — sys.path insert for module-level availability
    if _SHARED_DIR not in sys.path:
        sys.path.insert(0, _SHARED_DIR)
    from crypto import generate_htpasswd_entry  # type: ignore[import-untyped]

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Random salt breaks idempotency
    # · Symptom: повторный вызов перезаписывает .htpasswd-platform (md5 меняется).
    # · Root: crypto.generate_htpasswd_entry(email, password) без соли = случайный salt
    # ·   каждый вызов → existing == expected всегда False → вечная перезапись.
    # · Fix: при существующем файле извлекаем соль ($apr1$SALT$...), пересчитываем
    # ·   entry с фиксированной солью, сравниваем.
    # · Ported from: shell _ensure_htpasswd_generated() L221-241
    try:
        htpasswd_path = Path(htpasswd_file)
        existing = ""
        existing_salt = ""
        if htpasswd_path.exists():
            existing = htpasswd_path.read_text().strip()
            existing_salt = _extract_apr1_salt(existing)

        if existing_salt:
            expected_entry = generate_htpasswd_entry(email, password, salt=existing_salt)
            if expected_entry is None:
                logger.warning("[IMP:7][secrets_manager] shared crypto generate_htpasswd_entry failed")
                return False
            if existing == expected_entry:
                logger.info(
                    "[IMP:8][secrets_manager] htpasswd already up-to-date for %s — skipping",
                    email,
                )
                os.environ["HTPASSWD_FILE"] = htpasswd_file
                return True
            logger.info("[IMP:8][secrets_manager] htpasswd credentials changed — regenerating")
        else:
            # No file or unparseable entry — generate with fresh random salt
            expected_entry = generate_htpasswd_entry(email, password)
            if expected_entry is None:
                logger.warning("[IMP:7][secrets_manager] shared crypto generate_htpasswd_entry failed")
                return False

        # Write htpasswd file
        htpasswd_path.parent.mkdir(parents=True, exist_ok=True)
        htpasswd_path.write_text(expected_entry + "\n")
        htpasswd_path.chmod(0o644)
        os.environ["HTPASSWD_FILE"] = htpasswd_file
        logger.info("[IMP:9][secrets_manager] htpasswd generated at %s for %s", htpasswd_file, email)
        return True

    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] htpasswd OS error: %s", e)
        return False


# endregion FUNC__write_htpasswd_file


# region FUNC__ensure_htpasswd
## @purpose — Generate /run/platform/.htpasswd-platform from PLATFORM_MASTER_EMAIL and
##            PLATFORM_MASTER_PASSWORD (env or sourced from secrets.env). Thin wrapper
##            over _write_htpasswd_file() — all hashing/idempotency logic in the core.
## @io — ⇥ secrets_env: str (for sourcing PLATFORM_MASTER_* if not in os.environ),
##       htpasswd_file: str (optional override for tests) → ⎋ bool
## @complexity — O(1) + _write_htpasswd_file
## @invariants
##   - Non-fatal: returns False on failure, never raises
##   - Idempotent: salt-extraction in _write_htpasswd_file prevents rewrite on unchanged creds
##   - Requires both PLATFORM_MASTER_EMAIL and PLATFORM_MASTER_PASSWORD to be set
def _ensure_htpasswd(
    secrets_env: str = "/run/platform/secrets.env",
    htpasswd_file: str = "/run/platform/.htpasswd-platform",
) -> bool:
    """Generate .htpasswd-platform from platform master credentials. Returns True on success."""
    # Source secrets.env into os.environ if not already set
    if not os.environ.get("PLATFORM_MASTER_PASSWORD") or not os.environ.get("PLATFORM_MASTER_EMAIL"):
        env_vars = source_secrets_env(secrets_env)
        for key, value in env_vars.items():
            if key not in os.environ:
                os.environ[key] = value

    email = os.environ.get("PLATFORM_MASTER_EMAIL", "")
    password = os.environ.get("PLATFORM_MASTER_PASSWORD", "")

    if not email or not password:
        logger.info(
            "[IMP:7][secrets_manager] PLATFORM_MASTER_EMAIL or PLATFORM_MASTER_PASSWORD not set — "
            "skipping htpasswd generation",
        )
        return False

    return _write_htpasswd_file(email, password, htpasswd_file)


# endregion FUNC__ensure_htpasswd


# region FUNC_CLI
## @purpose — CLI entrypoint for ensure/source/cleanup/htpasswd actions.
##            Parses argparse subcommands, dispatches to the matching function.
##            Usage:
##              python3 secrets_manager.py ensure [--manifest <path>] [--secrets-env <path>]
##              python3 secrets_manager.py source [--secrets-env <path>]
##              python3 secrets_manager.py cleanup --secrets-env <path> [--tor-enabled <true|false>]
##              python3 secrets_manager.py htpasswd --email <e> --password <p> [--htpasswd-file <path>]
## @io — ⇥ sys.argv → ⎋ None (exits with 0 on success, 1 on error)
## @complexity — O(1) dispatch
## @invariants
##   - cleanup: exit 0 + "OK"/"SKIP" on success; exit 1 on missing/unreadable file
##   - htpasswd: exit 0 on success, exit 1 on generation failure
##   - ensure/source: exit 0 (source prints KEY=VALUE lines to stdout)
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Secrets Manager — ensure/source/cleanup/htpasswd secrets")
    subparsers = parser.add_subparsers(dest="action", required=True)

    ensure_parser = subparsers.add_parser("ensure", help="Generate missing tier=generated secrets")
    ensure_parser.add_argument(
        "--manifest", required=True, help="Path to secrets-manifest.yaml (required — fail-fast, DevPlan 116 T4)"
    )
    ensure_parser.add_argument("--secrets-env", default="/run/platform/secrets.env", help="Path to secrets.env")

    source_parser = subparsers.add_parser("source", help="Print parsed secrets.env KEY=VALUE lines to stdout")
    source_parser.add_argument("--secrets-env", default="/run/platform/secrets.env", help="Path to secrets.env")

    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Strip HTTP_PROXY/HTTPS_PROXY from secrets.env when TOR_ENABLED != true"
    )
    cleanup_parser.add_argument("--secrets-env", default="/run/platform/secrets.env", help="Path to secrets.env")
    cleanup_parser.add_argument("--tor-enabled", default="false", help="TOR_ENABLED flag (true keeps proxy vars)")

    htpasswd_parser = subparsers.add_parser("htpasswd", help="Generate .htpasswd-platform from explicit credentials")
    htpasswd_parser.add_argument("--email", required=True, help="Username/email for htpasswd entry")
    htpasswd_parser.add_argument("--password", required=True, help="Password for htpasswd entry")
    htpasswd_parser.add_argument(
        "--htpasswd-file", default="/run/platform/.htpasswd-platform", help="Target htpasswd file path"
    )

    args = parser.parse_args()

    if args.action == "ensure":
        generated = ensure_secrets(args.manifest, args.secrets_env)
        if generated:
            print(f"Generated: {','.join(generated)}")
    elif args.action == "source":
        env_vars = source_secrets_env(args.secrets_env)
        for k, v in env_vars.items():
            print(f"{k}={v}")
    elif args.action == "cleanup":
        if not os.path.isfile(args.secrets_env):
            print(f"SKIP: file not found: {args.secrets_env}", file=sys.stderr)
            sys.exit(1)
        try:
            before = parse_secrets_env(args.secrets_env)
        except (OSError, ValueError) as e:
            print(f"ERROR: cannot read {args.secrets_env}: {e}", file=sys.stderr)
            sys.exit(1)
        after = cleanup_secrets_env(args.secrets_env, args.tor_enabled)
        if args.tor_enabled != "true" and ("HTTP_PROXY" in before or "HTTPS_PROXY" in before):
            if "HTTP_PROXY" in after or "HTTPS_PROXY" in after:
                print(f"ERROR: proxy vars still present after cleanup: {args.secrets_env}", file=sys.stderr)
                sys.exit(1)
            print("OK")
        else:
            print("SKIP")
    elif args.action == "htpasswd":
        ok = _write_htpasswd_file(args.email, args.password, args.htpasswd_file)
        if not ok:
            print("ERROR: htpasswd generation failed", file=sys.stderr)
            sys.exit(1)

    sys.exit(0)
# endregion FUNC_CLI
