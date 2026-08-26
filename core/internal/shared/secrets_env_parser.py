#!/usr/bin/env python3
# GREP_SUMMARY: secrets-env-parser, parse-secrets-env, write-secrets-env, merge-secrets-env, export-shell, atomic-write, single-source-of-truth
# STRUCTURE: ▶ ┌path┐ → ○ exists? → ⊕ _parse_line(line) per line → ◇ prefix_filter? → ⎋ dict[str,str]
# region MODULE_CONTRACT
## @purpose  Single source of truth for parsing/writing secrets.env files across the ai-platform.
##           Replaces 7 existing inline parsers found in shell scripts and Python modules.
##           Provides 4 public functions: parse, write, merge, export_shell.
## @scope    Shared library in core/internal/shared/ consumed by bootstrap, deploy, lifecycle,
##           and any other module needing secrets.env I/O. No Docker dependency.
## @invariants
##   1. parse() raises FileNotFoundError if input path does not exist (caller must handle)
##   2. write() uses atomic tempfile+rename pattern (no partial writes on crash)
##   3. write() defaults to 0o600 permissions (secrets must not be world-readable)
##   4. merge() uses last-wins semantics — each subsequent path overwrites duplicates
##   5. export_shell() produces lines with single-quoted values and proper '\'' escaping
##   6. _parse_line() handles export prefix, single/double quotes, inline comments, spaces
##   7. Lines without '=' are silently skipped (not an error)
##   8. Empty file → empty dict; only-comments file → empty dict
## @rationale DevPlan 086: 7 inline parsers scattered across the codebase parsing secrets.env
##            in subtly incompatible ways (quote handling, comment stripping, export prefix).
##            A single canonical module eliminates drift and enables consistent behavior.
## @changes    2026-07-30 | DevPlan 086 — Created as shared module
# endregion MODULE_CONTRACT

import logging
import os
import pathlib

# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (tempfile+fsync+replace).
# Локальная tempfile+os.replace реализация write() УДАЛЕНА (дубль канона).
from core.internal.shared.atomic_writer import atomic_write as _atomic_write
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)

# Default permissions for secrets file (owner read/write only)
DEFAULT_SECRETS_MODE = 0o600


# region FUNC__find_unquoted_hash


_QUOTED_MIN_LEN: int = 2  # quoted-значение минимально: "" или ''


def _find_unquoted_hash(value: str) -> int:
    """Find the position of the first '#' that is NOT inside single or double quotes.

    ▶ ┌value string┐ → ○ scan chars tracking quote state → ◇ # outside quotes? → ⎋ position | -1

    ## @purpose — Distinguish inline comments from hash characters inside quoted values.
    ##            A # is a comment delimiter only when not inside a quoted string.
    ## @io — ⇥ value: str — raw value string (may contain quotes and #)
    ##       → ⎋ int — position of first unquoted #, or -1 if none
    ## @complexity — O(N) where N = len(value)
    ## @rationale — Simple state machine: track single/double quote open/close.
    ##              Escaped quotes (\\' or \\") are treated as literal characters.
    """
    in_single = False
    in_double = False

    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return i

    return -1


# endregion FUNC__find_unquoted_hash


# region FUNC__parse_line


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse a single line from a secrets.env file into a (key, value) tuple.

    ▶ ┌line┐ → ○ strip → ◇ empty/comment? → ⊕ strip export prefix → ◇ '=' exists? → ⊕ split key=value → ⊕ strip quotes → ⊕ strip inline comments → ⎋ (key, value) | None

    ## @purpose — Parse one line. Handles: export prefix, single/double quotes,
    ##            inline comments (# outside quotes), spaces around '=', empty values.
    ## @io — ⇥ line: str — a single line from the file (may include trailing newline)
    ##       → ⎋ tuple[str, str] | None — (key, value) or None if line is skipped
    ## @complexity — O(N)
    ## @invariants
    ##   - Returns None for: empty line, comment-only line (starts with #), no '=' sign
    ##   - Strips surrounding single or double quotes from value
    ##   - Strips inline comments (unquoted # and everything after)
    ##   - Strips leading/trailing whitespace from key and value
    ##   - Strips 'export ' prefix (case-sensitive, exactly 'export ')
    ##   - Never returns empty key (value may be empty if KEY=)
    """
    # ── Step 1: Strip and skip empty/comment lines ──
    stripped = line.strip()
    if not stripped:
        logger.debug("[IMP:3][_parse_line] Skipping empty line")
        return None

    if stripped.startswith("#"):
        logger.debug("[IMP:3][_parse_line] Skipping comment line: %.60s", stripped[:60])
        return None

    # ── Step 2: Strip 'export ' prefix ──
    remaining = stripped
    if remaining.startswith("export "):
        remaining = remaining[7:].lstrip()
        logger.debug("[IMP:4][_parse_line] Stripped 'export ' prefix")

    # ── Step 3: Find first '=' and split ──
    eq_pos = remaining.find("=")
    if eq_pos == -1:
        logger.debug("[IMP:4][_parse_line] No '=' found, skipping line: %.60s", remaining[:60])
        return None

    key = remaining[:eq_pos].strip()
    raw_value = remaining[eq_pos + 1 :]

    if not key:
        logger.debug("[IMP:4][_parse_line] Empty key after split, skipping")
        return None

    # ── Step 4: Strip inline comments from value ──
    comment_pos = _find_unquoted_hash(raw_value)
    if comment_pos != -1:
        raw_value = raw_value[:comment_pos]
        logger.debug("[IMP:4][_parse_line] Stripped inline comment from value")

    value = raw_value.strip()

    # ── Step 5: Strip surrounding quotes ──
    if len(value) >= _QUOTED_MIN_LEN and (
        (value[0] == "'" and value[-1] == "'") or (value[0] == '"' and value[-1] == '"')
    ):
        inner = value[1:-1]
        logger.debug("[IMP:4][_parse_line] Stripped surrounding quotes from value")
        value = inner

    # ⚠️ TRAP[BUG] 2026-08-03 · SECRET LEAK в логи: значение выводилось в INFO-лог
    # · Symptom: bootstrap-лог печатал 'ROOT_PASS=...', 'GHCR_PULL_TOKEN=...' и т.д.
    #   (secrets.env парсится на каждой ноде — утечка всех секретов в syslog/CI-логи)
    # · Fix: логируется только ключ + длина значения (маскирование значений).
    # · Rev: если потребуется отладка значений — DEBUG-уровень с явным флагом.
    logger.info("[IMP:7][_parse_line] Parsed key: %s (value len=%d)", key, len(value))
    return (key, value)


# endregion FUNC__parse_line


# region FUNC_parse_line
def parse_line(line: str) -> tuple[str, str] | None:
    """Публичный контракт построчного разбора dotenv-грамматики (AI-0055, DevPlan 17 T5.5).

    ## @purpose  Единая грамматика для всех читателей env-строк: env_reader и
    ##            decrypt_secrets._yaml_to_env переиспользуют её вместо локальных копий.
    ## @io        ⇥ line: str → ⎋ tuple[str, str] | None (None = строка пропущена)
    """
    return _parse_line(line)


# endregion FUNC_parse_line


# region FUNC_escape_single_quotes
def escape_single_quotes(value: str) -> str:
    """Shell-escaping одиночных кавычек: ' → '\'' (канон export_shell).

    ## @purpose  Одна имплементация '\'' -escaping для export_shell и decrypt_secrets.
    """
    return value.replace("'", "'\\''")


# endregion FUNC_escape_single_quotes


# region FUNC_parse


# region FUNC__plw_body_parse
## @purpose  Тело try-блока (PLW0717 extraction из parse) — семантика except не меняется.
## @io       ⇥ path, result, collect_bad: bool → ⎋ список номеров не-комментарий строк без
##           валидного key= (пуст при collect_bad=False — backward compat)
## @complexity O(N) — N = строк файла
def _plw_body_parse(path, result, *, collect_bad: bool = False):
    bad_lines: list[int] = []
    with pathlib.Path(path).open(encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parsed = _parse_line(raw_line)
            if parsed is not None:
                key, value = parsed
                result[key] = value
                logger.debug("[IMP:5][parse] Line %d: %s='%.60s'", line_no, key, value[:60])
            elif collect_bad:
                bad_lines.append(line_no)
    return bad_lines


# endregion FUNC__plw_body_parse


def parse(path: str, prefix_filter: str | None = None, *, strict: bool = False) -> dict[str, str]:
    """Parse a secrets.env file into a dictionary.

    ▶ ┌path┐ → ◇ FileNotFoundError if absent → ⊕ _parse_line per line → ◇ strict∧bad-lines? → ⎋ dict

    ## @purpose — Read and parse a secrets.env file. Raises FileNotFoundError if the
    ##            file does not exist — caller is responsible for existence checks.
    ##            QA R5 (DevPlan 14 T2.A): strict=True — непустая не-комментарий строка
    ##            без валидного key=value → ConfigValidationError со списком номеров строк
    ##            (fail-closed ДО merge-guard'ов потребителей: файл нетронут).
    ## @io — ⇥ path: str — absolute or relative path to secrets.env file
    ##       ⇥ prefix_filter: Optional[str] — if set, only return vars whose key starts
    ##                           with this prefix (case-sensitive)
    ##       ⇥ strict: bool — kwarg-only; False (default) = legacy skip-garbage семантика
    ##       → ⎋ dict[str, str] — parsed key-value pairs (ordered by file line order)
    ## @complexity — O(N * L) where N = lines, L = avg line length
    ## @raises FileNotFoundError — if the file does not exist
    ## @raises ConfigValidationError — strict=True и есть нераспарсенный не-комментарий контент
    ## @invariants
    ##   - File must exist (FileNotFoundError if missing)
    ##   - Empty file → empty dict
    ##   - Only-comments file → empty dict
    ##   - Duplicate keys: last occurrence wins
    ##   - prefix_filter: case-sensitive prefix matching
    ##   - strict=False: строки без '=' молча пропускаются (все прочие потребители не тронуты)
    """
    logger.info("[IMP:7][parse] Opening secrets.env: %s (strict=%s)", path, strict)

    if not os.path.isfile(path):
        logger.error("[IMP:9][parse] File not found: %s", path)
        msg = f"Secrets file not found: {path}"
        raise FileNotFoundError(msg)

    result: dict[str, str] = {}

    try:
        bad_lines = _plw_body_parse(path, result, collect_bad=strict)
    except UnicodeDecodeError as e:
        logger.error("[IMP:9][parse] Unicode decode error in %s: %s", path, e)
        # Re-raise as ValueError to be explicit about encoding issues
        msg = f"Unicode decode error in {path}: {e}"
        raise ConfigValidationError(msg) from e
    except OSError as e:
        logger.error("[IMP:9][parse] OS error reading %s: %s", path, e)
        raise

    if strict and bad_lines:
        # QA R5 (T2.A): fail-closed — garbage в секрет-файле НЕ глотается молча
        logger.error(
            "[IMP:10][parse] STRICT FAIL: %s has unparsable non-comment content at line(s) %s",
            path,
            bad_lines,
        )
        msg = (
            f"Strict parse failed for {path}: unparsable non-comment content at "
            f"line(s) {bad_lines} — refusing to proceed (merge/persist would be unsafe)"
        )
        raise ConfigValidationError(msg)

    # ── Apply prefix filter if specified ──
    if prefix_filter is not None:
        filtered = {k: v for k, v in result.items() if k.startswith(prefix_filter)}
        logger.info(
            "[IMP:8][parse] prefix_filter='%s': %d/%d entries matched",
            prefix_filter,
            len(filtered),
            len(result),
        )
        result = filtered

    logger.info("[IMP:9][parse] Parsed %d entries from %s", len(result), path)
    return result


# endregion FUNC_parse


# region FUNC_write


def write(path: str, data: dict[str, str], mode: int = DEFAULT_SECRETS_MODE) -> None:
    """Atomically write a dict as a secrets.env file via shared atomic_writer (E5).

    ▶ ┌path, data, mode┐ → ⊕ build key=value lines → ⊕ atomic_write (temp+fsync+replace) → ⎋

    ## @purpose — Atomic write to secrets.env file. Делегирует в shared/atomic_writer
    ##            (DevPlan 119 E5 — единый канон tempfile+fsync+os.replace). Failures
    ##            during write do not corrupt the target file (temp cleaned up).
    ## @io — ⇥ path: str — target file path
    ##       ⇥ data: dict[str, str] — key-value pairs to write
    ##       ⇥ mode: int — file permissions (default 0o600 = owner rw only)
    ##       → ⎋ None
    ## @complexity — O(N) where N = number of entries
    ## @invariants
    ##   - Atomic: tempfile in same directory, then os.replace (shared canon, E5)
    ##   - Creates parent directory if absent
    ##   - Default mode 0o600 (secrets must not be world-readable)
    ##   - Keys with '=' or newline in value are written as-is (caller responsibility)
    ##   - Empty dict writes an empty file
    """
    dir_path = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_path, exist_ok=True)
    logger.info("[IMP:7][write] Atomic write to %s (%d entries, mode=0%o)", path, len(data), mode)

    # Fail-fast: значения обязаны быть str (write() raise'ил TypeError на value[:80]
    # в debug-логе — контракт теста test_secrets_pipeline_write_roundtrip; валидация ДО
    # tempfile — target не трогается при невалидном типе).
    for key, value in data.items():
        if not isinstance(value, str):
            msg = f"secrets_env value for {key!r} must be str, got {type(value).__name__}"
            raise TypeError(msg)

    content = "".join(f"{key}={value}\n" for key, value in data.items())
    try:
        _atomic_write(path, content, mode=mode)
        logger.info("[IMP:9][write] Atomically wrote %d entries to %s", len(data), path)
    except (OSError, TypeError) as e:
        logger.error("[IMP:9][write] Write failed for %s: %s", path, e)
        raise


# endregion FUNC_write


# region FUNC_merge


def merge(*paths: str) -> dict[str, str]:
    """Merge multiple secrets.env files with last-wins semantics.

    ▶ ┌paths...┐ → ○ for each: ◇ exists? → ⊕ parse(path) → ⊕ update(result) → ⎋ merged dict

    ## @purpose — Merge multiple secrets.env files. Each subsequent path overwrites
    ##            duplicate keys from previous ones. Useful for layering base secrets
    ##            with environment-specific overrides.
    ## @io — ⇥ *paths: str — one or more paths to secrets.env files
    ##       → ⎋ dict[str, str] — merged key-value pairs
    ## @complexity — O(N * M) where N = total lines across all files, M = number of files
    ## @raises FileNotFoundError — if any path does not exist
    ## @invariants
    ##   - All paths must exist (raises FileNotFoundError if any missing)
    ##   - Last-wins: for duplicate keys, the last path's value wins
    ##   - Empty dict if no paths provided
    ##   - Single path → equivalent to parse(path)
    """
    result: dict[str, str] = {}

    if not paths:
        logger.info("[IMP:8][merge] No paths provided — returning empty dict")
        return result

    logger.info("[IMP:7][merge] Merging %d secrets.env files", len(paths))

    for i, path in enumerate(paths, start=1):
        logger.debug("[IMP:5][merge] Merging file %d/%d: %s", i, len(paths), path)
        parsed = parse(path)
        before = len(result)
        result.update(parsed)
        new_keys = len(result) - before
        overridden = len(parsed) - new_keys
        logger.info("[IMP:8][merge] File %s: %d new keys, %d overridden", path, new_keys, overridden)

    logger.info("[IMP:9][merge] Merged %d total entries from %d files", len(result), len(paths))
    return result


# endregion FUNC_merge


# region FUNC_export_shell


def export_shell(path: str) -> str:
    """Generate shell-compatible 'export VAR=value' output from a secrets.env file.

    ▶ ┌path┐ → ⊕ parse(path) → ○ for each key, value: escape single quotes → ⊕ "export KEY='VALUE'" → ⎋ str

    ## @purpose — Produce shell-compatible output suitable for `source` or eval.
    ##            Values are single-quoted with proper escaping of embedded single
    ##            quotes via the '\'' sequence (end quote, escaped quote, reopen quote).
    ##            Used by node-lifecycle.sh and other shell scripts that need to
    ##            source secrets.env values.
    ## @io — ⇥ path: str — path to secrets.env file
    ##       → ⎋ str — shell-compatible export lines (newline-terminated)
    ## @complexity — O(N) where N = number of entries
    ## @invariants
    ##   - Each line: export KEY='escaped_value'
    ##   - Embedded single quotes escaped as: '\'' (end quote, literal quote, reopen quote)
    ##   - Trailing newline at end of output
    ##   - Empty dict → empty string
    ##   - Raises FileNotFoundError if path does not exist (via parse)
    """
    data = parse(path)
    if not data:
        logger.info("[IMP:8][export_shell] No entries in %s — returning empty string", path)
        return ""

    lines: list[str] = []
    for key, value in data.items():
        escaped = escape_single_quotes(value)
        lines.append(f"export {key}='{escaped}'")

    result = "\n".join(lines) + "\n"
    logger.info("[IMP:9][export_shell] Generated %d export lines from %s", len(lines), path)
    return result


# endregion FUNC_export_shell
