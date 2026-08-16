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
##      НИКОГДА не возвращает "deploy" как фолбэк (D2: формат `deploy <project> <sha> [env]` удалён)
##   4. Stripping order is deterministic: path prefix first, then trim. strip-префиксы УДАЛЕНЫ (D2)
##   5. CLI outputs JSON for parse mode, bare string for classify mode; unknown verb → JSON error + exit 4
##   6. Non-fatal: I/O errors in CLI cause sys.exit(1), not silent failures
##   7. Verb-словарь — из shared/verbs.py (CANONICAL_VERBS); reserve-имена для проектов (U-56)
## @rationale  DevPlan 081: два дублирующихся парсера консолидированы в единый Python-модуль.
##             DevPlan 116 B1 T1 (D2): strip-префиксы и дефолт-фолбэк deploy
##             удалены — неизвестный verb обязан давать JSON-ошибку (честные exit-коды, B4),
##             а не тихо деплоить чужой проект. Голый `status`/`remove`/`verify`/`receive` теперь
##             классифицируется как verb (U-56: раньше голый `status` уходил в deploy).
##             DevPlan 118 D3: переезд в deploy/ — правило shared «≥2 потребителя» не выполнено
##             (единственный Python-импорт — orchestrator_cli).
## @changes    2026-07-26 | DevPlan 081 TASK-081B1 — Created as shared Python module
##             2026-08-01 | DevPlan 116 B1 T1 — D2: strip-префиксы удалены, classify_verb
##                         exact-match по CANONICAL_VERBS, unknown → ConfigValidationError;
##                         parse_ssh_command: receive <project> [<sha>], status/remove <project>, verify <node>
##             2026-08-02 | DevPlan 118 D3 — перенесён shared/ → deploy/ (рядом с потребителем orchestrator_cli)
##             2026-08-13 | DevPlan 164 W3-1 — deploy.sh удалён: _deploy_script_path и path-strip
##                         удалены; _strip_prefixes = trim-only; config-параметр удалён
# endregion MODULE_CONTRACT

import json
import logging
import sys
from typing import TypedDict

from core.internal.shared.exceptions import ConfigValidationError, PlatformError
from core.internal.shared.verbs import CANONICAL_VERBS

logger = logging.getLogger(__name__)


# region TYPEDDICT_ParsedSshCommand
class ParsedSshCommand(TypedDict):
    """Структурированный результат parse_ssh_command (W11, DevPlan 170) —
    типизированная граница между parser'ом и dispatch-реестром."""

    verb: str
    args: str | None
    raw: str
    cleaned: str


# endregion TYPEDDICT_ParsedSshCommand

# W4a (DevPlan 160 T4.1): import-time env-чтение УБРАНО. 164 W3-1: ветка
# _deploy_script_path УДАЛЕНА — deploy.sh не существует (dispatch — единственный канал,
# authorized_keys command= → orchestrator_cli; SSH_ORIGINAL_COMMAND уже чистый verb).


# region FUNC__strip_prefixes


_ARGV_FORMAT_ARGS_MIN: int = 2  # argv: --format <value>
_ARGV_CMD_ARGS_MIN: int = 2  # argv: <verb> <command>


def _strip_prefixes(raw: str) -> str:
    """Strip known path prefixes from raw SSH command.

    ▶ ┌raw┐ → ◇ trim whitespace → ⎋ cleaned string
    ## @purpose  Нормализация SSH_ORIGINAL_COMMAND перед classify_verb. Path-префиксы
    ##           (deploy.sh) НЕ стрипятся — deploy.sh удалён (164 W3-1, прямое замещение):
    ##           dispatch-канал получает чистый verb от authorized_keys (orchestrator_cli).
    ##           Команда с префиксом остаётся как есть → unknown verb (честный отказ).
    ## @io — ⇥ raw: str → ⎋ cleaned: str
    ## @complexity — O(1)
    ## @invariants
    ##   - Does NOT validate for empty — caller (parse_ssh_command) handles that
    ##   - strip-префиксы УДАЛЕНЫ (D2, DevPlan 116 B1; deploy.sh-путь — 164 W3-1):
    ##     префиксы не распознаются намеренно — команда уходит в unknown
    """
    return raw.strip()


# endregion FUNC__strip_prefixes


# region FUNC_classify_verb


def classify_verb(cleaned: str) -> str:
    """Classify a cleaned SSH command string into a canonical verb (exact-match, D2).

    ▶ ┌cleaned┐ → ◇ exact match (ping|exit|status|verify|remove|receive) → ⎋ verb
    │           → ◇ prefix match (verb + " ") → ⎋ verb
    │           → ✗ unknown → raise ConfigValidationError

    ## @purpose — Map a cleaned SSH command string to a canonical verb token.
    ##            NO default fallback: unrecognized input raises ConfigValidationError
    ##            (честные exit-коды B4, `deploy <project> <sha> [env]` удалён — D2).
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
    msg = f"unknown verb in SSH command: {cleaned!r} (expected one of: {', '.join(CANONICAL_VERBS)})"
    raise ConfigValidationError(msg)


# endregion FUNC_classify_verb


# region FUNC_parse_ssh_command


def parse_ssh_command(raw: str) -> ParsedSshCommand:
    """Parse a raw SSH_ORIGINAL_COMMAND string into a structured result dict.

    ▶ ┌raw┐ → ◇ _strip_prefixes(raw) → cleaned
    │           → ◇ classify_verb(cleaned) → verb
    │           → ◇ extract args per verb → ⊕ dict{verb, args, raw, cleaned} → ⎋

    ## @purpose — Parse raw SSH_ORIGINAL_COMMAND into a structured dict with
    ##            verb, args, raw, and cleaned fields. Single entry point for
    ##            all SSH forced-command parsing (dispatcher, DevPlan 116 B1 T2).
    ## @io — ⇥ raw: str (deploy.sh-путь не стрипится — 164 W3-1)
    ##         → ⎋ dict{verb: str, args: str|None, raw: str, cleaned: str}
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
        msg = "empty command after stripping"
        raise ConfigValidationError(msg)

    cleaned = _strip_prefixes(raw)

    if not cleaned:
        logger.warning("[IMP:7][parse_ssh_command] Empty command after stripping (raw=%r)", raw)
        msg = "empty command after stripping"
        raise ConfigValidationError(msg)

    verb = classify_verb(cleaned)

    # Extract args based on verb (аргументы verb'а — по контракту T1)
    if verb in {"ping", "exit"}:
        args = None
    elif verb == "receive":
        # receive <project> [<sha>] — два токена; версия (sha) из аргументов (D5)
        prefix = verb + " "
        args = cleaned[len(prefix) :].strip() if cleaned.startswith(prefix) else None
    elif verb in {"status", "remove"} or verb == "verify":
        prefix = verb + " "
        args = cleaned[len(prefix) :].strip() if cleaned.startswith(prefix) else None
    else:  # pragma: no cover — CANONICAL_VERBS закрыто, unreachable
        args = None

    # TypedDict-граница (W11): литерал со значениями str | None — явная аннотация
    result: ParsedSshCommand = {
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for python3 -m invocation (argv-канон, W4a).

    ▶ ┌argv┐ → ◇ --format lines? → ◇ parse mode → parse_ssh_command
    │                                           ⊕ json.dumps | lines output
    │            → ◇ classify mode → classify_verb → ⊕ stdout

    ## @purpose — CLI wrapper for ssh_command_parser. Supports:
    ##   parse <command>              — JSON output (default)
    ##   --format lines parse <cmd>   — line-by-line output (avoids inline python3 -c)
    ##   classify <command>           — bare verb string (unknown → error + exit 4)
    ## @io — ⇥ argv: list[str] | None (None = sys.argv[1:]) → ⎋ int (0 ok, 1 usage, 4 verb)
    ## @rationale --format lines устраняет inline python3 -c в shell-потребителях
    ##   (DevPlan 081 AC7 migration — Tier 1 Strangler trigger).
    ##   W4a (DevPlan 160 T4.1): _cli_main() → main(argv) — единый argv-канон core/.
    """
    output_format: str = "json"
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "--format":
        if len(argv) < _ARGV_FORMAT_ARGS_MIN:
            print("--format requires an argument (json|lines)", file=sys.stderr)
            return 1
        output_format = argv[1]
        if output_format not in {"json", "lines"}:
            print(f"Unknown format: {output_format} (expected json|lines)", file=sys.stderr)
            return 1
        argv = argv[2:]

    if len(argv) < _ARGV_CMD_ARGS_MIN:
        print(
            "Usage: python3 -m core.internal.deploy.ssh_command_parser [--format json|lines] parse|classify <command>",
            file=sys.stderr,
        )
        return 1

    mode = argv[0]
    command = " ".join(argv[1:])

    if mode == "parse":
        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
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
    sys.exit(main())

# endregion CLI_SUPPORT
