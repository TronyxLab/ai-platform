#!/usr/bin/env python3
# GREP_SUMMARY: secrets-manifest-reader, iter-secrets, tier, consumers, charset, gen-command, strict, single-source-of-truth
# STRUCTURE: ▶ ┌path┐ → ◇ exists? ⚡ raise FileNotFoundError → ◇ yaml.safe_load → ◇ is-dict/list? ⚡ raise ValueError → ⊕ iter_secrets → ◇ filters (tier/consumers/charset) → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Single source of truth for reading secrets-manifest.yaml across the ai-platform.
##           Replaces 3 independent parsers (secrets_manager._read_manifest,
##           secrets_validator._check_env_requires, secrets_validator._validate_secret_charsets).
##           STRICT mode: missing/malformed manifest raises — no silent `return []` fallbacks
##           (DevPlan 116 T4, U-33; invariant 7 — «gate зелёный, система врёт»).
## @scope    Shared library in core/internal/shared/ consumed by bootstrap/lifecycle,
##           bootstrap/deploy, and any other module needing secrets-manifest I/O.
##           The manifest is always delivered with core/ (rsync core-deploy) — greenfield
##           servers never run without it (invariant 9 of the hardening program).
## @invariants
##   1. iter_secrets() raises FileNotFoundError if path missing; ValueError if not a
##      dict or secrets key not a list (STRICT — no graceful degradation)
##   2. iter_secrets() returns list[dict[str, Any]] of ALL manifest entries (no filtering)
##   3. Filtering is done by typed helpers: tier(secret), consumers(secret), charset(secret),
##      gen_command(secret) — each returns a safe default for absent fields
##   4. Malformed entries (non-dict items in secrets list) are SKIPPED with a warning
##      (they would otherwise crash downstream dict access)
##   5. Never mutates the manifest file — read-only
## @rationale DevPlan 116 T4: 3 parsers with subtly different graceful-degradation semantics
##            (one returned hardcoded fallback list — silent drift vector). One canonical
##            strict reader makes absence a loud failure, not a silent empty list.
## @changes    2026-07-31 | DevPlan 116 T4 — Created as shared module (U-33/U-43)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# region FUNC_iter_secrets
## @purpose  Read secrets-manifest.yaml and return ALL secret entries. Strict mode:
##            missing file / non-dict document / secrets-not-a-list → raise (invariant 7).
## @io       ⇥ path: Path | str → ⎋ list[dict[str, Any]]: all manifest entries
##           ⚡ raise FileNotFoundError (absent), ValueError (malformed)
## @complexity O(N) — single YAML load + linear pass
## @invariants
##   - STRICT: no `return []` on missing/malformed — manifest always delivered with core/
##   - Non-dict items inside secrets list are skipped with WARN (defensive, not degradation)
##   - Returns ALL entries — filtering via tier()/consumers()/charset() helpers
def iter_secrets(path: Path | str) -> list[dict[str, Any]]:
    """Return all secret entries from secrets-manifest.yaml (strict reader)."""
    manifest_path = Path(path)
    logger.info("[IMP:7][iter_secrets][start] Reading manifest: %s", manifest_path)

    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"[IMP:10][iter_secrets] secrets-manifest.yaml not found at {manifest_path} — "
            "manifest is always delivered with core/ (rsync core-deploy). Run: "
            "`make generate-secrets-manifest` (DevPlan 116 T4, U-33)."
        )

    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    if data is None:
        raise ValueError(f"[IMP:10][iter_secrets] Manifest {manifest_path} is empty — expected dict with 'secrets' key")
    if not isinstance(data, dict):
        raise ValueError(
            f"[IMP:10][iter_secrets] Manifest {manifest_path} is {type(data).__name__}, expected dict — "
            "secrets-manifest.yaml is malformed"
        )

    secrets = data.get("secrets", [])
    if not isinstance(secrets, list):
        raise ValueError(
            f"[IMP:10][iter_secrets] Manifest {manifest_path} 'secrets' key is {type(secrets).__name__}, "
            "expected list — secrets-manifest.yaml is malformed"
        )

    # Defensive: skip non-dict items (they would crash downstream dict access)
    result: list[dict[str, Any]] = []
    for idx, entry in enumerate(secrets):
        if isinstance(entry, dict):
            result.append(entry)
        else:
            logger.warning(
                "[IMP:7][iter_secrets][skip] Entry %d is %s (expected dict) — skipped",
                idx,
                type(entry).__name__,
            )

    logger.info("[IMP:9][iter_secrets][ok] Loaded %d secret entries from %s", len(result), manifest_path)
    return result


# endregion FUNC_iter_secrets


# region FUNC_tier
## @purpose  Typed accessor: tier field of a secret entry.
## @io       ⇥ secret: dict → ⎋ str ("" if absent)
## @complexity O(1)
def tier(secret: dict[str, Any]) -> str:
    """Return the secret's tier (required|generated), '' if absent."""
    return str(secret.get("tier", ""))


# endregion FUNC_tier


# region FUNC_consumers
## @purpose  Typed accessor: consumers list of a secret entry.
## @io       ⇥ secret: dict → ⎋ list[str] ([] if absent/non-list)
## @complexity O(1)
def consumers(secret: dict[str, Any]) -> list[str]:
    """Return the secret's consumer module names, [] if absent."""
    raw = secret.get("consumers", [])
    if isinstance(raw, list):
        return [str(c) for c in raw]
    return []


# endregion FUNC_consumers


# region FUNC_charset
## @purpose  Typed accessor: charset regex of a secret entry.
## @io       ⇥ secret: dict → ⎋ str ("" if absent)
## @complexity O(1)
def charset(secret: dict[str, Any]) -> str:
    """Return the secret's charset constraint regex, '' if absent."""
    return str(secret.get("charset", ""))


# endregion FUNC_charset


# region FUNC_gen_command
## @purpose  Typed accessor: gen_command of a secret entry.
## @io       ⇥ secret: dict → ⎋ str ("" if absent)
## @complexity O(1)
def gen_command(secret: dict[str, Any]) -> str:
    """Return the secret's generation command, '' if absent."""
    return str(secret.get("gen_command", ""))


# endregion FUNC_gen_command
