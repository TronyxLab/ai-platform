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
import sys

logger = logging.getLogger("ssh_command_parser")

# region FUNC__strip_prefixes


def _strip_prefixes(raw: str) -> str:
    """Strip known path prefixes and legacy wrapper tokens from raw SSH command.

    ▶ ┌raw┐ → ◇ strip /opt/.../deploy.sh   (with space)
    │           → ◇ strip /opt/.../deploy.sh (bare)
    │           → ◇ strip "platform-deploy " (with space)
    │           → ◇ strip "platform-deploy"  (bare)
    │           → ◇ .strip() whitespace     → ⎋ cleaned

    ## @purpose — Strip known path prefixes and legacy wrappers from SSH_ORIGINAL_COMMAND.
    ##            Aggregated from deploy.sh::_strip_command_prefixes and
    ##            deploy-project.sh::parse_ssh_command.
    ## @io — ⇥ raw: str → ⎋ cleaned: str
    ## @complexity — O(1), fixed number of string operations
    ## @invariants
    ##   - Order matters: path prefix first, then legacy platform-deploy
    ##   - Each step runs even if previous step changed the string
    ##   - Final .strip() removes leading/trailing whitespace
    ##   - Does NOT validate for empty — caller (parse_ssh_command) handles that
    """
    cleaned = raw

    # Step 1: Strip path prefix with trailing space (appleboy/ssh-action style)
    if cleaned.startswith("/opt/platform/core/entrypoints/deploy.sh "):
        cleaned = cleaned[len("/opt/platform/core/entrypoints/deploy.sh "):]

    # Step 2: Strip path prefix without trailing space (bare path)
    if cleaned.startswith("/opt/platform/core/entrypoints/deploy.sh"):
        cleaned = cleaned[len("/opt/platform/core/entrypoints/deploy.sh"):]

    # Step 3: Strip legacy "platform-deploy " prefix (with space)
    if cleaned.startswith("platform-deploy "):
        cleaned = cleaned[len("platform-deploy "):]

    # Step 4: Strip bare "platform-deploy" (without space)
    if cleaned == "platform-deploy" or (cleaned.startswith("platform-deploy") and not cleaned.startswith("platform-deploy ")):
        cleaned = cleaned[len("platform-deploy"):]

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
        raise ValueError("empty command after stripping")

    cleaned = _strip_prefixes(raw)

    if not cleaned:
        logger.warning(
            "[IMP:7][parse_ssh_command] Empty command after stripping (raw=%r)", raw
        )
        raise ValueError("empty command after stripping")

    verb = classify_verb(cleaned)

    # Extract args based on verb
    if verb in ("ping", "exit"):
        args = None
    elif verb in ("remove", "status", "verify", "platform-deliver", "platform-deploy"):
        prefix = verb + " "
        args = cleaned[len(prefix):].strip() if cleaned.startswith(prefix) else None
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
        verb, args, raw, cleaned,
    )

    return result


# endregion FUNC_parse_ssh_command


# region CLI_SUPPORT


def _cli_main() -> None:
    """CLI entry point for python3 -m invocation.

    ▶ ┌sys.argv┐ → ◇ parse mode → parse_ssh_command → ⊕ json.dumps → stdout
    │            → ◇ classify mode → classify_verb → ⊕ stdout
    """
    if len(sys.argv) < 3:
        print(
            "Usage: python3 -m core.internal.shared.ssh_command_parser "
            "parse|classify <command>",
            file=sys.stderr,
        )
        sys.exit(1)

    mode = sys.argv[1]
    command = " ".join(sys.argv[2:])

    if mode == "parse":
        try:
            result = parse_ssh_command(command)
            print(json.dumps(result, ensure_ascii=False))
        except ValueError as e:
            print(
                json.dumps({"error": str(e), "raw": command}, ensure_ascii=False)
            )
            sys.exit(1)
    elif mode == "classify":
        try:
            verb = classify_verb(command)
            print(verb)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Unknown mode: {mode} (expected parse or classify)", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()

# endregion CLI_SUPPORT
