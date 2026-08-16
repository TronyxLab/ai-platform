#!/usr/bin/env python3
# GREP_SUMMARY: content-hash, sha256, compute-content-hash, shared, idempotent, bootstrap
# STRUCTURE: ▶ ┌compute_content_hash(files)┐ → ○ for each: file? → hasher.update(content) → WARN if missing → ⎋ sha256.hexdigest()
# region MODULE_CONTRACT
## @purpose  Unified content hash computation for bootstrap idempotency.
##           Replaces 3 independent implementations: shell content-hash,
##           state_machine._step_hash(), add-vhost.sh compute_body_hash().
## @scope    Shared library consumed by state_machine.py, add-vhost.sh (thin wrapper),
##           and any other module needing content hash.
## @invariants
##   1. Always SHA-256 via hashlib (deterministic, cross-platform)
##   2. Files are read in the order specified in the input list
##   3. Missing files are logged at WARNING level and silently skipped (NOT fatal)
##   4. Empty file list → sha256("") (consistent empty hash)
##   5. No .dockerignore or directory scanning — explicit file list only
##   6. CLI support: python3 -m core.internal.shared.content_hash compute --files f1 f2
## @rationale  D3 from DevPlan: Different from deploy/content_hash.py which scans directories
##             and respects .dockerignore. Shared/content_hash.py is for explicit file list
##             hashing — bootstrap idempotency needs exact file semantics, not build-context.
## @changes    2026-07-25 | DevPlan 079 DRIFT-B4 — Created as shared module
# endregion MODULE_CONTRACT

import argparse
import hashlib
import logging
import pathlib
import sys
from dataclasses import dataclass
from typing import cast

logger = logging.getLogger(__name__)


# region FUNC_compute_content_hash


def compute_content_hash(files: list[str]) -> str:
    """Compute SHA-256 hex digest of concatenated file contents.

    ▶ ┌files list┐ → ○ for each: ◇ exists? → ⊕ hasher.update(content) → WARN skip → ⎋ hexdigest

    ## @purpose — Deterministic content hash for bootstrap idempotency checks.
    ## @io — ⇥ files: list[str] — explicit file paths → ⎋ str — 64-char hex SHA-256
    ## @complexity — O(N * S) where N = file count, S = file size
    ## @invariants
    ##   - Missing files: WARNING + skip (not fatal)
    ##   - Empty list → sha256("")
    ##   - Files processed in list order (order matters for hash)
    """
    hasher = hashlib.sha256()

    if not files:
        logger.info("[IMP:8][compute_content_hash] Empty file list — returning sha256('')")
        hasher.update(b"")
        digest = hasher.hexdigest()
        logger.info("[IMP:9][compute_content_hash] Digest: %s... (0 files)", digest[:12])
        return digest

    processed = 0
    for fpath in files:
        try:
            with pathlib.Path(fpath).open("rb") as f:
                hasher.update(f.read())
            processed += 1
            logger.debug("[IMP:6][compute_content_hash] Hashed: %s", fpath)
        except FileNotFoundError:  # ruff: ignore[PERF203] — per-file exception handling is required
            logger.warning("[IMP:7][compute_content_hash] File not found, skipping: %s", fpath)
        except OSError as e:
            logger.warning("[IMP:7][compute_content_hash] Cannot read %s: %s", fpath, e)

    digest = hasher.hexdigest()
    logger.info("[IMP:9][compute_content_hash] Digest: %s... (%d files processed)", digest[:12], processed)
    return digest


# endregion FUNC_compute_content_hash


# region CLI


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose — CLI entry for standalone content hash computation.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Compute SHA-256 content hash of specified files (DevPlan 079)",
    )
    subparsers = parser.add_subparsers(dest="command")
    compute_parser = subparsers.add_parser("compute", help="Compute content hash")
    compute_parser.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="File paths to hash (order matters)",
    )
    return parser


def main() -> int:
    """CLI entry point.

    ▶ ┌sys.argv┐ → ◇ parse → compute_content_hash(files) → print hash → ⎋ exit 0

    ## @purpose — CLI wrapper for compute_content_hash.
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success)
    ## @complexity — O(N * S)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    @dataclass
    class _CliArgs:
        command: str
        files: list[str]

    typed_args = cast(_CliArgs, cast(object, parser.parse_args()))

    if typed_args.command != "compute":
        parser.print_help()
        return 1

    digest = compute_content_hash(typed_args.files)
    print(digest)
    return 0


# endregion CLI

if __name__ == "__main__":
    sys.exit(main())
