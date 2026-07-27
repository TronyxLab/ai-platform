#!/usr/bin/env python3
# GREP_SUMMARY: secrets-manager, autogen-secrets, manifest, ensure-secrets, sops, htpasswd
# STRUCTURE: ▶ ensure_secrets → source_secrets_env → _read_manifest → _generate_secret → _persist_to_sops → _ensure_htpasswd → ⎋ CLI
# region MODULE_CONTRACT
## @purpose  Auto-generate missing tier=generated secrets from secrets-manifest.yaml or fallback hardcoded list.
##           Port of core/lib/secrets.sh:step_12b_ensure_secrets() lines 298-411 plus source_secrets_env()
##           and htpasswd generation. Designed for bootstrap pipeline step 12b.
## @scope    core/internal/bootstrap/lifecycle/ — secrets management for bootstrap pipeline.
##           Three responsibilities: (1) read manifest and fill gaps, (2) parse secrets.env,
##           (3) generate htpasswd from platform credentials.
## @invariants
##   1. Non-fatal: returns partial list on failure, NEVER raises exceptions
##   2. Existing secrets are NOT overwritten — only missing (empty) secrets are generated
##   3. gen_command executed via subprocess (bash -c) with 30s timeout
##   4. sops --set persistence is non-fatal on failure
##   5. htpasswd generation called after secrets (requires PLATFORM_MASTER_PASSWORD)
##   6. sourced secrets.env values take precedence over manifest/hardcoded defaults
## @rationale  Python port of shell secrets logic. Enables unit-testing, typed returns,
##             and consistent error handling without relying on bash eval() for secret generation.
## @changes  2026-07-25 | W5-E6 secrets_manager — created from secrets.sh step_12b decomposition
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Hardcoded fallback secrets (when manifest unavailable) ──
_FALLBACK_SECRETS: list[dict[str, str]] = [
    {"name": "LITELLM_MASTER_KEY", "gen_command": 'echo "sk-$(openssl rand -hex 32)"'},
    {"name": "LANGFUSE_INIT_ORG_ID", "gen_command": 'echo "org_$(openssl rand -hex 4)"'},
    {"name": "LANGFUSE_INIT_PROJECT_ID", "gen_command": 'echo "proj_$(openssl rand -hex 4)"'},
    {"name": "LANGFUSE_PUBLIC_KEY", "gen_command": 'echo "pk-lf_$(openssl rand -hex 16)"'},
    {"name": "LANGFUSE_SECRET_KEY", "gen_command": 'echo "sk-lf_$(openssl rand -hex 16)"'},
    {"name": "NEXTAUTH_SECRET", "gen_command": "openssl rand -hex 32"},
    {"name": "SALT", "gen_command": "openssl rand -hex 16"},
]


# region FUNC_source_secrets_env
## @purpose — Parse a secrets.env file into a dict. Handles comments (#), empty lines,
##            quoted values, and inline `export` prefix. NEVER raises — returns empty dict on failure.
## @io — ⇥ secrets_env: path to secrets.env file → ⎋ dict[str, str]
## @complexity — O(N) where N = lines in file
## @invariants
##   - Returns empty dict if file not found or unreadable
##   - Strips surrounding quotes from values
##   - Removes leading `export ` prefix
def source_secrets_env(secrets_env: str) -> dict[str, str]:
    """Parse secrets.env key=value file into dict. Returns empty dict on failure."""
    env_vars: dict[str, str] = {}
    if not os.path.isfile(secrets_env):
        logger.info("[IMP:7][secrets_manager] Secrets env file not found: %s", secrets_env)
        return env_vars

    logger.info("[IMP:8][secrets_manager] Sourcing secrets env: %s", secrets_env)
    try:
        with open(secrets_env) as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Strip leading 'export ' prefix
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                # Split on first '='
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip surrounding quotes
                if len(value) >= 2 and value[0] in ("'", '"') and value[0] == value[-1]:
                    value = value[1:-1]
                if key:
                    env_vars[key] = value
        logger.info("[IMP:9][secrets_manager] Sourced %d variables from %s", len(env_vars), secrets_env)
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] Cannot read %s: %s", secrets_env, e)

    return env_vars


# endregion FUNC_source_secrets_env


# region FUNC__read_manifest
## @purpose — Read secrets-manifest.yaml and extract tier=generated secrets as a list of dicts.
##            Returns empty list on any error (file not found, YAML parse error, missing key).
## @io — ⇥ manifest_path: str → ⎋ list[dict[str, Any]]
## @complexity — O(N) where N = YAML document size
## @invariants
##   - Returns empty list on any error (never raises)
##   - Filters only entries with tier == "generated"
##   - Each entry requires name and gen_command keys
def _read_manifest(manifest_path: str) -> list[dict[str, Any]]:
    """Read secrets-manifest.yaml, return tier=generated secrets. Returns [] on error."""
    if not manifest_path or not os.path.isfile(manifest_path):
        logger.info("[IMP:7][secrets_manager] Manifest not found: %s — fallback to hardcoded list", manifest_path)
        return []

    logger.info("[IMP:8][secrets_manager] Reading generated secrets from manifest: %s", manifest_path)
    try:
        import yaml

        with open(manifest_path) as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            logger.warning("[IMP:7][secrets_manager] Manifest is not a dict — fallback to hardcoded")
            return []

        secrets: list[dict[str, Any]] = data.get("secrets", [])
        if not isinstance(secrets, list):
            logger.warning("[IMP:7][secrets_manager] Manifest secrets is not a list — fallback to hardcoded")
            return []

        generated: list[dict[str, Any]] = [
            s
            for s in secrets
            if isinstance(s, dict) and s.get("tier") == "generated" and s.get("name") and s.get("gen_command")
        ]

        if not generated:
            logger.info("[IMP:7][secrets_manager] Manifest has no generated secrets — fallback to hardcoded list")

        return generated

    except ImportError:
        logger.warning("[IMP:7][secrets_manager] PyYAML not available — fallback to hardcoded list")
        return []
    except (yaml.YAMLError, OSError, FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("[IMP:7][secrets_manager] Manifest parse error: %s — fallback to hardcoded list", e)
        return []


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
    manifest_generated: list[dict[str, Any]] = []

    # ── Step 1: Source existing secrets.env into os.environ ──
    env_vars = source_secrets_env(secrets_env)
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value

    # ── Step 2: Read manifest for tier=generated secrets ──
    manifest_generated = _read_manifest(manifest_path)

    secrets_to_process: list[dict[str, Any]] = []
    if manifest_generated:
        secrets_to_process = manifest_generated
        logger.info(
            "[IMP:9][secrets_manager] Processing %d generated secrets from manifest",
            len(secrets_to_process),
        )
    else:
        secrets_to_process = list(_FALLBACK_SECRETS)
        logger.info(
            "[IMP:9][secrets_manager] Processing %d secrets from hardcoded fallback",
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


# region FUNC__ensure_htpasswd
## @purpose — Generate /run/platform/.htpasswd-platform from PLATFORM_MASTER_EMAIL and
##            PLATFORM_MASTER_PASSWORD. Uses shared/crypto.py for APR1 hashing (DevPlan 078 T4).
##            Idempotent: checks existing file hash matches current credentials.
## @io — ⇥ secrets_env: str (for sourcing PLATFORM_MASTER_* if not in os.environ) → ⎋ bool
## @complexity — O(1) + hash_apr1 from shared.crypto
## @invariants
##   - Non-fatal: returns False on failure, never raises
##   - Idempotent: if existing htpasswd entry matches current credentials, skip
##   - Requires both PLATFORM_MASTER_EMAIL and PLATFORM_MASTER_PASSWORD to be set
def _ensure_htpasswd(secrets_env: str = "/run/platform/secrets.env") -> bool:
    """Generate .htpasswd-platform from platform master credentials. Returns True on success."""
    # Import shared crypto — sys.path insert for module-level availability
    _shared_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "shared"
    )
    if _shared_dir not in sys.path:
        sys.path.insert(0, _shared_dir)
    from crypto import generate_htpasswd_entry  # type: ignore[import-untyped]

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

    htpasswd_file = "/run/platform/.htpasswd-platform"

    try:
        # Generate htpasswd entry via shared crypto (DevPlan 078 T4)
        expected_entry = generate_htpasswd_entry(email, password)
        if expected_entry is None:
            logger.warning("[IMP:7][secrets_manager] shared crypto generate_htpasswd_entry failed")
            return False

        # Check idempotency: compare with existing file content
        htpasswd_path = Path(htpasswd_file)
        if htpasswd_path.exists():
            existing = htpasswd_path.read_text().strip()
            if existing == expected_entry:
                logger.info(
                    "[IMP:8][secrets_manager] htpasswd already up-to-date for %s — skipping",
                    email,
                )
                os.environ["HTPASSWD_FILE"] = htpasswd_file
                return True
            logger.info(
                "[IMP:8][secrets_manager] htpasswd credentials changed — regenerating",
            )

        # Write htpasswd file
        htpasswd_path.parent.mkdir(parents=True, exist_ok=True)
        htpasswd_path.write_text(expected_entry + "\n")
        htpasswd_path.chmod(0o644)
        os.environ["HTPASSWD_FILE"] = htpasswd_file
        logger.info(
            "[IMP:9][secrets_manager] htpasswd generated at %s for %s",
            htpasswd_file,
            email,
        )
        return True

    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] htpasswd OS error: %s", e)
        return False


# endregion FUNC__ensure_htpasswd


# region FUNC_CLI
## @purpose — CLI entrypoint for ensure/source actions.
##            Parses argparse, dispatches to appropriate function, prints results to stdout.
##            Usage:
##              python3 secrets_manager.py ensure [--manifest <path>] [--secrets-env <path>]
##              python3 secrets_manager.py source [--secrets-env <path>]
## @io — ⇥ sys.argv → ⎋ None (exits with 0 on success, 1 on error)
## @complexity — O(1) dispatch
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Secrets Manager — generate/ensure/source secrets")
    parser.add_argument("action", choices=["ensure", "source"], help="Action to perform")
    parser.add_argument("--manifest", default="", help="Path to secrets-manifest.yaml")
    parser.add_argument("--secrets-env", default="/run/platform/secrets.env", help="Path to secrets.env")
    args = parser.parse_args()

    if args.action == "ensure":
        generated = ensure_secrets(args.manifest, args.secrets_env)
        if generated:
            print(f"Generated: {','.join(generated)}")
    elif args.action == "source":
        env_vars = source_secrets_env(args.secrets_env)
        for k, v in env_vars.items():
            print(f"{k}={v}")

    sys.exit(0)
# endregion FUNC_CLI
