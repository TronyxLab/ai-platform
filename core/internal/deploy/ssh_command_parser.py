#!/usr/bin/env python3
# GREP_SUMMARY: ssh-command-parser, parse-ssh-command, classify-verb, strip-prefixes, forced-command, verbs, dispatch
# STRUCTURE: ▶ parse_ssh_command(raw) → ◇ _strip_prefixes → ◇ classify_verb(cleaned) → ⊕ dict → ⎋
#            ▶ CLI: python3 -m core.internal.deploy.ssh_command_parser parse|classify <command>
# region MODULE_CONTRACT
## @purpose  Unified SSH_ORIGINAL_COMMAND parser — canonical implementation for the forced-command
##           dispatcher (`orchestrator_cli dispatch`, DevPlan 116 B1). Classifies verb against the
##           closed verb dictionary (shared/verbs.py, D1) with EXACT-match semantics: unknown verb →
##           ConfigValidationError (никакого дефолт-фолбэка на deploy, D2).
## @scope    Core/internal/deploy — рядом с единственным Python-потребителем (orchestrator_cli).
##           DevPlan 118 D3: перенесён из core/internal/shared/ (1 прод-потребитель — not ≥2).
##           Два public API: parse_ssh_command(raw) и classify_verb(cleaned).
##           CLI mode for testing and direct invocation.
## @invariants
##   1. Always returns dict with verb/args/raw/cleaned fields from parse_ssh_command
##   2. Empty input to parse_ssh_command raises ConfigValidationError
##   3. classify_verb returns one of CANONICAL_VERBS (verbs.py) or raises ConfigValidationError —
##      НИКОГДА не возвращает "deploy" как фолбэк (D2: legacy-формат `deploy <project> <sha> [env]` удалён)
##   4. Stripping order is deterministic: path prefix first, then trim. legacy strip-префиксы УДАЛЕНЫ (D2)
##   5. CLI outputs JSON for parse mode, bare string for classify mode; unknown verb → JSON error + exit 4
##   6. Non-fatal: I/O errors in CLI cause sys.exit(1), not silent failures
##   7. Verb-словарь — из shared/verbs.py (CANONICAL_VERBS); reserve-имена для проектов (U-56)
## @rationale  DevPlan 081: два дублирующихся парсера консолидированы в единый Python-модуль.
##             DevPlan 116 B1 T1 (D2): legacy strip-префиксы и дефолт-фолбэк deploy
##             удалены — неизвестный verb обязан давать JSON-ошибку (честные exit-коды, B4),
##             а не тихо деплоить чужой проект. Голый `status`/`remove`/`verify`/`receive` теперь
##             классифицируется как verb (U-56: раньше голый `status` уходил в deploy).
##             DevPlan 118 D3: переезд в deploy/ — правило shared «≥2 потребителя» не выполнено
##             (единственный Python-импорт — orchestrator_cli); CLI-потребитель deploy.sh обновлён.
## @changes    2026-07-26 | DevPlan 081 TASK-081B1 — Created as shared Python module
##             2026-08-01 | DevPlan 116 B1 T1 — D2: legacy strip-префиксы удалены, classify_verb
##                         exact-match по CANONICAL_VERBS, unknown → ConfigValidationError;
##                         parse_ssh_command: receive <project> [<sha>], status/remove <project>, verify <node>
##             2026-08-02 | DevPlan 118 D3 — перенесён shared/ → deploy/ (рядом с потребителем orchestrator_cli)
# endregion MODULE_CONTRACT

import json
import logging
import sys

from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import ConfigValidationError, PlatformError
from core.internal.shared.verbs import CANONICAL_VERBS

logger = logging.getLogger(__name__)

# Canonical deploy.sh path — constructed from PLATFORM_ROOT env var.
# B3: резолвер канона shared/deploy_paths (PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform).
_PLATFORM_ROOT = str(platform_remote_base())
_DEPLOY_SCRIPT_PATH = f"{_PLATFORM_ROOT}/core/entrypoints/deploy.sh"

# region FUNC__strip_prefixes


def _strip_prefixes(raw: str) -> str:
    """Strip known path prefixes from raw SSH command.

    ▶ ┌raw┐ → ◇ strip /opt/.../deploy.sh   (with space)
    │           → ◇ strip /opt/.../deploy.sh (bare)
    │           → ◇ strip "deploy "          (with space)
    │           → ◇ strip "deploy"           (bare)
    │           → ◇ trim whitespace
    │           → ⎋ cleaned string
    ## @purpose  Remove known wrapper prefixes from SSH_ORIGINAL_COMMAND so the
    ##           remaining string is a plain verb + args (e.g. "receive proj abc123").
    ##           The appleboy/ssh-action's forced-command wraps the original command
    ##           with the full deploy.sh path.
    ## @io — ┌raw: str┐ → ⎋ cleaned: str
    ## @complexity — O(1) — fixed number of prefix checks (max 5)
    ## @invariants
    ##   - Does NOT validate for empty — caller (parse_ssh_command) handles that
    ##   - legacy strip-префиксы УДАЛЕНЫ (D2, DevPlan 116 B1) — префиксы не распознаются
    ##     намеренно: legacy-команда остаётся как есть → classify_verb → unknown
    """
    cleaned = raw

    # Step 1: Strip path prefix with trailing space (appleboy/ssh-action style)
    deploy_prefix_space = f"{_DEPLOY_SCRIPT_PATH} "
    if cleaned.startswith(deploy_prefix_space):
        cleaned = cleaned[len(deploy_prefix_space) :]

    # Step 2: Strip path prefix without trailing space (bare path)
    if cleaned.startswith(_DEPLOY_SCRIPT_PATH):
        cleaned = cleaned[len(_DEPLOY_SCRIPT_PATH) :]

    # Step 3: Trim whitespace
    return cleaned.strip()


# endregion FUNC__strip_prefixes


# region FUNC_classify_verb


def classify_verb(cleaned: str) -> str:
    """Classify a cleaned SSH command string into a canonical verb (exact-match, D2).

    ▶ ┌cleaned┐ → ◇ exact match (ping|exit|status|verify|remove|receive) → ⎋ verb
    │           → ◇ prefix match (verb + " ") → ⎋ verb
    │           → ✗ unknown → raise ConfigValidationError

    ## @purpose — Map a cleaned SSH command string to a canonical verb token.
    ##            NO default fallback: unrecognized input raises ConfigValidationError
    ##            (честные exit-коды B4, legacy `deploy <project> <sha> [env]` удалён — D2).
    ## @io — ⇥ cleaned: str → ⎋ verb: str (one of CANONICAL_VERBS from shared/verbs.py)
    ## @complexity — O(N) where N = len(CANONICAL_VERBS) (6)
    ## @invariants
    ##   - Exact match (bare verb) checked BEFORE prefix match — голый `status` → verb status (U-56)
    ##   - Prefix match: verb + " " (аргументы после пробела)
    ##   - Unknown → ConfigValidationError (никогда не "deploy" как фолбэк)
    ##   - Returns only strings from CANONICAL_VERBS, always lowercase
    """
    # Exact matches (verb alone — no arguments): голый `status` теперь verb, НЕ проект (U-56)
    if cleaned in CANONICAL_VERBS:
        return cleaned

    # Prefix matches (verb followed by a space and arguments)
    for verb in CANONICAL_VERBS:
        prefix = verb + " "
        if cleaned.startswith(prefix):
            return verb

    # Unknown verb → error (D2: никакого дефолт-фолбэка на deploy)
    logger.warning(
        "[IMP:7][classify_verb] Unknown verb in cleaned command: %r",
        cleaned[:80],
    )
    raise ConfigValidationError(
        f"unknown verb in SSH command: {cleaned!r} (expected one of: {', '.join(CANONICAL_VERBS)})"
    )


# endregion FUNC_classify_verb


# region FUNC_parse_ssh_command


def parse_ssh_command(raw: str) -> dict:
    """Parse a raw SSH_ORIGINAL_COMMAND string into a structured result dict.

    ▶ ┌raw┐ → ◇ _strip_prefixes(raw) → cleaned
    │           → ◇ classify_verb(cleaned) → verb
    │           → ◇ extract args per verb → ⊕ dict{verb, args, raw, cleaned} → ⎋

    ## @purpose — Parse raw SSH_ORIGINAL_COMMAND into a structured dict with
    ##            verb, args, raw, and cleaned fields. Single entry point for
    ##            all SSH forced-command parsing (dispatcher, DevPlan 116 B1 T2).
    ## @io — ⇥ raw: str → ⎋ dict{verb: str, args: str|None, raw: str, cleaned: str}
    ## @complexity — O(1) fixed string operations + O(N) classify
    ## @invariants
    ##   - Always returns dict with exactly 4 keys: verb, args, raw, cleaned
    ##   - raw.value == raw (the original input is preserved)
    ##   - Empty input → raises ConfigValidationError("empty command after stripping")
    ##   - args is None for ping/exit verbs, str for all others
    ##   - receive: args = "<project> [<sha>]" (два токена, D5 — версия из аргументов)
    ##   - status/remove: args = "<project>"; verify: args = "<node>"
    ##   - Unknown verb → ConfigValidationError propagates (никогда не deploy-фолбэк)
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

    # Extract args based on verb (аргументы verb'а — по контракту T1)
    if verb in ("ping", "exit"):
        args = None
    elif verb == "receive":
        # receive <project> [<sha>] — два токена; версия (sha) из аргументов (D5)
        prefix = verb + " "
        args = cleaned[len(prefix) :].strip() if cleaned.startswith(prefix) else None
    elif verb in ("status", "remove") or verb == "verify":
        prefix = verb + " "
        args = cleaned[len(prefix) :].strip() if cleaned.startswith(prefix) else None
    else:  # pragma: no cover — CANONICAL_VERBS закрыто, unreachable
        args = None

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
    ##   classify <command>           — bare verb string (unknown → error + exit 4)
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
            "Usage: python3 -m core.internal.deploy.ssh_command_parser [--format json|lines] parse|classify <command>",
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
        except PlatformError as e:
            print(f"Error: {e}", file=sys.stderr)
            return e.exit_code
    else:
        print(f"Unknown mode: {mode} (expected parse or classify)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())

# endregion CLI_SUPPORT
