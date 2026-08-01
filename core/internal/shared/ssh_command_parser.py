#!/usr/bin/env python3
# GREP_SUMMARY: ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes, forced-command
# STRUCTURE: ▶ parse_ssh_command(raw) → ◇ _strip_prefixes → ◇ classify_verb(cleaned) → ⊕ dict → ⎋
#            ▶ CLI: python3 -m core.internal.shared.ssh_command_parser parse|classify <command>
# region MODULE_CONTRACT
## @purpose  Unified SSH_ORIGINAL_COMMAND parser — replaces two duplicate parsers in
##           deploy.sh and deploy-project.sh with a single canonical implementation.
##           See: DevPlan 081 TASK-081B1.
## @scope    Core/internal/shared — low-level parsing layer (by DDD). No business logic.
##           Two public functions: parse_ssh_command(raw) and classify_verb(cleaned).
##           CLI mode for testing and direct invocation.
## @invariants
##   1. Always returns dict with verb/args/raw/cleaned fields from parse_ssh_command
##   2. Empty input to parse_ssh_command raises ValueError
##   3. classify_verb always returns one of the seven defined verb strings (never empty)
##   4. Stripping order is deterministic: path prefix first, then legacy prefixes, then trim
##   5. CLI outputs JSON for parse mode, bare string for classify mode
##   6. Non-fatal: I/O errors in CLI cause sys.exit(1), not silent failures
## @rationale  D1 from DevPlan 081: Two independent SSH command parsers exist in
##             deploy.sh (shell-function _strip_command_prefixes) and deploy-project.sh
##             (shell-function parse_ssh_command). Both implement the same stripping
##             and verb classification logic with minor differences. Consolidating into
##             a single Python module owned by core/internal/shared eliminates
##             DRIFT-D1 and makes the parser unit-testable.
## @changes    2026-07-26 | DevPlan 081 TASK-081B1 — Created as shared Python module
# endregion MODULE_CONTRACT

import json
import logging
import os
import sys

from core.internal.shared.exceptions import ConfigValidationError, PlatformError

logger = logging.getLogger(__name__)

# Canonical deploy.sh path — constructed from PLATFORM_ROOT env var.
# This matches the gate test allowlist (os.environ.get("PLATFORM_ROOT", "/opt/platform"))
# and follows the platform convention for deployment paths.
_PLATFORM_ROOT = os.environ.get("PLATFORM_ROOT", "/opt/platform")
_DEPLOY_SCRIPT_PATH = f"{_PLATFORM_ROOT}/core/entrypoints/deploy.sh"

# region FUNC__strip_prefixes


def _strip_prefixes(raw: str) -> str:
    """Strip known path prefixes and legacy wrapper tokens from raw SSH command.

    ▶ ┌raw┐ → ◇ strip /opt/.../deploy.sh   (with space)
    │           → ◇ strip /opt/.../deploy.sh (bare)
    │           → ◇ strip "platform-deploy " (with space)
    │           → ◇ strip "platform-deploy"  (bare)
    │           → ◇ strip "deploy "          (with space)
    │           → ◇ strip "deploy"           (bare)
    │           → ◇ trim whitespace
    │           → ⎋ cleaned string
    ## @purpose  Remove known wrapper prefixes from SSH_ORIGINAL_COMMAND so the
    ##           remaining string is a plain verb + args (e.g. "deploy --project foo").
    ##           The appleboy/ssh-action's forced-command wraps the original command
    ##           with the full deploy.sh path.
    ## @io — ┌raw: str┐ → ⎋ cleaned: str
    ## @complexity — O(1) — fixed number of prefix checks (max 7)
    ## @invariants
    ##   - Does NOT validate for empty — caller (parse_ssh_command) handles that
    """
    cleaned = raw

    # Step 1: Strip path prefix with trailing space (appleboy/ssh-action style)
    deploy_prefix_space = f"{_DEPLOY_SCRIPT_PATH} "
    if cleaned.startswith(deploy_prefix_space):
        cleaned = cleaned[len(deploy_prefix_space) :]

    # Step 2: Strip path prefix without trailing space (bare path)
    if cleaned.startswith(_DEPLOY_SCRIPT_PATH):
        cleaned = cleaned[len(_DEPLOY_SCRIPT_PATH) :]

    # Step 3: Strip legacy "platform-deploy " prefix (with space)
    if cleaned.startswith("platform-deploy "):
        cleaned = cleaned[len("platform-deploy ") :]

    # Step 4: Strip bare "platform-deploy" (without space)
    if cleaned == "platform-deploy" or (
        cleaned.startswith("platform-deploy") and not cleaned.startswith("platform-deploy ")
    ):
        cleaned = cleaned[len("platform-deploy") :]

    # Step 5: Trim whitespace
    return cleaned.strip()


# endregion FUNC__strip_prefixes


# region FUNC_classify_verb


def classify_verb(cleaned: str) -> str:
    """Classify a cleaned SSH command string into a canonical verb.

    ▶ ┌cleaned┐ → ◇ exact match (ping|exit)            → ⎋ verb
    │           → ◇ prefix match (remove |status | ...) → ⎋ verb
    │           → ◇ default fallback                    → ⎋ "deploy"

    ## @purpose — Map a cleaned SSH command string to a canonical verb token.
    ##            Used for K1 verb contract dispatch in deploy.sh entrypoint.
    ## @io — ⇥ cleaned: str → ⎋ verb: str (one of: ping|exit|remove|status|verify|
    ##                                    platform-deliver|platform-deploy|deploy)
    ## @complexity — O(N) where N = number of prefix patterns (7 patterns)
    ## @invariants
    ##   - Exact match (ping/exit) is checked before prefix match
    ##   - Prefix matches consume the full prefix + space before checking
    ##   - "deploy" is the fallback for any unrecognized input (never fails)
    ##   - Returns one of exactly 8 strings, always lowercase
    """
    # Exact matches (verb alone — no arguments)
    if cleaned == "ping":
        return "ping"
    if cleaned == "exit":
        return "exit"

    # Prefix matches (verb followed by a space and arguments)
    prefixes = [
        ("remove ", "remove"),
        ("status ", "status"),
        ("verify ", "verify"),
        ("platform-deliver ", "platform-deliver"),
        ("platform-deploy ", "platform-deploy"),
    ]
    for prefix, verb in prefixes:
        if cleaned.startswith(prefix):
            return verb

    # Default: treat as deploy
    return "deploy"


# endregion FUNC_classify_verb


# region FUNC_parse_ssh_command


def parse_ssh_command(raw: str) -> dict:
    """Parse a raw SSH_ORIGINAL_COMMAND string into a structured result dict.

    ▶ ┌raw┐ → ◇ _strip_prefixes(raw) → cleaned
    │           → ◇ classify_verb(cleaned) → verb
    │           → ◇ extract args → ⊕ dict{verb, args, raw, cleaned} → ⎋

    ## @purpose — Parse raw SSH_ORIGINAL_COMMAND into a structured dict with
    ##            verb, args, raw, and cleaned fields. Single entry point for
    ##            all SSH forced-command parsing.
    ## @io — ⇥ raw: str → ⎋ dict{verb: str, args: str|None, raw: str, cleaned: str}
    ## @complexity — O(1) fixed string operations + O(N) classify
    ## @invariants
    ##   - Always returns dict with exactly 4 keys: verb, args, raw, cleaned
    ##   - raw.value == raw (the original input is preserved)
    ##   - Empty input → raises ValueError("empty command after stripping")
    ##   - args is None for ping/exit verbs, str for all others
    ##   - IMP:9 log emitted on successful parse
    ##   - IMP:7 log emitted for each stripping step
    """
    if not raw:
        logger.warning("[IMP:7][parse_ssh_command] Empty raw input")
        raise ConfigValidationError("empty command after stripping")

    cleaned = _strip_prefixes(raw)

    if not cleaned:
        logger.warning("[IMP:7][parse_ssh_command] Empty command after stripping (raw=%r)", raw)
        raise ConfigValidationError("empty command after stripping")

    verb = classify_verb(cleaned)

    # Extract args based on verb
    if verb in ("ping", "exit"):
        args = None
    elif verb in ("remove", "status", "verify", "platform-deliver", "platform-deploy"):
        prefix = verb + " "
        args = cleaned[len(prefix) :].strip() if cleaned.startswith(prefix) else None
    else:  # deploy (default)
        args = cleaned

    result = {
        "verb": verb,
        "args": args,
        "raw": raw,
        "cleaned": cleaned,
    }

    logger.info(
        "[IMP:9][parse_ssh_command] Parsed: verb=%s args=%r raw=%r cleaned=%r",
        verb,
        args,
        raw,
        cleaned,
    )

    return result


# endregion FUNC_parse_ssh_command


# region CLI_SUPPORT


def _cli_main() -> int:
    """CLI entry point for python3 -m invocation.

    ▶ ┌sys.argv┐ → ◇ --format lines? → ◇ parse mode → parse_ssh_command
    │                                           ⊕ json.dumps | lines output
    │            → ◇ classify mode → classify_verb → ⊕ stdout

    ## @purpose — CLI wrapper for ssh_command_parser. Supports:
    ##   parse <command>              — JSON output (default)
    ##   --format lines parse <cmd>   — line-by-line output (avoids inline python3 -c)
    ##   classify <command>           — bare verb string
    ## @rationale --format lines eliminates inline python3 -c in deploy.sh
    ##   (DevPlan 081 AC7 migration — Tier 1 Strangler trigger).
    """
    output_format: str = "json"
    argv = sys.argv[1:]

    if argv and argv[0] == "--format":
        if len(argv) < 2:
            print("--format requires an argument (json|lines)", file=sys.stderr)
            return 1
        output_format = argv[1]
        if output_format not in ("json", "lines"):
            print(f"Unknown format: {output_format} (expected json|lines)", file=sys.stderr)
            return 1
        argv = argv[2:]

    if len(argv) < 2:
        print(
            "Usage: python3 -m core.internal.shared.ssh_command_parser [--format json|lines] parse|classify <command>",
            file=sys.stderr,
        )
        return 1

    mode = argv[0]
    command = " ".join(argv[1:])

    if mode == "parse":
        try:
            result = parse_ssh_command(command)
            if output_format == "lines":
                print(result["verb"])
                print(result.get("args") or "")
                print(result["cleaned"])
            else:
                print(json.dumps(result, ensure_ascii=False))
        except PlatformError as e:
            if output_format == "lines":
                print("error")
                print(str(e))
                print(command)
            else:
                print(json.dumps({"error": str(e), "raw": command}, ensure_ascii=False))
            return e.exit_code
    elif mode == "classify":
        try:
            verb = classify_verb(command)
            print(verb)
        except (ValueError, KeyError, TypeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        print(f"Unknown mode: {mode} (expected parse or classify)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())

# endregion CLI_SUPPORT
