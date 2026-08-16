# GREP_SUMMARY: hardcoded-credentials security-scan password-detection shell-scripts ci-workflows install-scripts lint deployment-audit literal-secrets
# STRUCTURE: ▶ test_no_hardcoded_password_in_shell_scripts ∋ core/**/*.sh → ⊕ literal_assignments + password_args + literal_fallbacks → ◇ findings? → ⎋ fail|pass → ▶ test_no_hardcoded_credentials_in_ci_workflows ∋ .github/workflows/*.yml → ⊕ literal_credential_values → ◇ literal secrets? → ⎋ fail|pass → ▶ test_no_password_fallback_in_install_scripts ∋ core/**/install*.sh → ⊕ credential_fallbacks_using_:-literal → ◇ has_literal_fallback? → ⎋ fail|pass → ▶ test_no_token_in_git_url ∋ core/**/*.sh → ⊕ _scan_for_token_in_git_url → ◇ token-in-url? → ⎋ fail|pass → ▶ test_no_token_in_git_url_workflows ∋ .github/**/*.yml → ⊕ _scan_for_token_in_git_url → ◇ token-in-url? → ⎋ fail|pass
# @file test_no_hardcoded_credentials.py
# @purpose  Security regression tests that detect hardcoded passwords, tokens, API keys,
#           and secrets in shell scripts, CI workflow files, install scripts, Python-модулях
#           и YAML-конфигах across the ai-platform codebase. All credential values must use
#           $VAR, ${VAR}, or ${{ secrets.* }} references — never literal values.
# @scope    Пять независимых сканеров:
#           1. test_no_hardcoded_password_in_shell_scripts — scans core/**/*.sh for
#              patterns: PASSWORD=literal, --password literal, token=literal,
#              api_key=literal, secret=literal, ${VAR:-literal} credential fallbacks
#           2. test_no_hardcoded_credentials_in_ci_workflows — scans .github/workflows/*.yml
#              for literal credential values (not ${{ secrets.* }})
#           3. test_no_password_fallback_in_install_scripts — checks install scripts use
#              :? (fail-fast) instead of :-literal for critical credential variables
#           4. test_no_hardcoded_credentials_in_python_files (W3 T3.4) — scans core/**/*.py
#              for VAR = "literal" / "key": "literal" (quoted literals only; test fixtures
#              в tests/ не сканируются — production core/ только)
#           5. test_no_hardcoded_credentials_in_yaml_files (W3 T3.4) — scans core/**/*.{yaml,yml}
#              + root *.yaml/*.yml; исключаются GENERATED/SoT файлы с намеренными CI-значениями
#              (platform-infra.yaml, platform-env.yaml, secrets-manifest.yaml, .env.example)
# @invariants
#   - All tests use @ldd_trajectory decorator from conftest
#   - All tests are marked @pytest.mark.predeploy
#   - test_no_hardcoded_password_in_shell_scripts scans core/**/*.sh recursively
#   - test_no_hardcoded_credentials_in_ci_workflows scans .github/workflows/*.yml
#   - test_no_password_fallback_in_install_scripts scans install*.sh (glob pattern)
#   - test_no_hardcoded_credentials_in_python_files scans core/**/*.py (production; не tests/)
#   - test_no_hardcoded_credentials_in_yaml_files сканирует core/** + root, исключая
#     GENERATED/SoT (намеренные CI-test значения env_defaults)
#   - Credential variable patterns: PASSWORD, TOKEN, API_KEY, SECRET (case-insensitive)
#   - Lines with ${VAR}, $(cmd), ${{ secrets.* }} refs are excluded (not literals)
#   - Uses platform_root fixture from conftest (project root resolution)
#   - No subprocess calls — pure file I/O and regex parsing
# @rationale  AC-T7 from DevPlan §TASK-7 — security regression test ensuring no hardcoded
#             credentials enter the codebase. Complements conftest scan_directory_for_secrets
#             (which only scans docker-compose*.yml) with broader coverage. Detected P0 bug:
#             test2026 was hardcoded in 4 files before TASK-2 fix.
#             W3 T3.4 (DevPlan 160): расширение на .py и .yaml — секреты утекали в Python-модули
#             и YAML-конфиги, не покрытые shell/workflow-сканерами.
# @changes    CREATED: 2026-07-09 | Wave 1: TASK-7 security credential scanner
# @changes    2026-08-13 | DevPlan 160 W3 T3.4 — +.py и .yaml сканеры (+R5 negative)
# @see        conftest.py :: SECRET_PATTERNS, scan_for_secrets, scan_directory_for_secrets
# @see        DevPlan §TASK-7, §TEST_SPEC
#
# region MODULE_CONTRACT
## @purpose  5 pre-deploy security regression tests validating no hardcoded credentials
##           exist in shell scripts, CI workflow YAML, install scripts, Python-модулях и
##           YAML-конфигах.
## @scope    Static file scanning of core/**/*.sh, .github/workflows/*.yml,
##           core/**/install*.sh, core/**/*.py, core/**/*.{yaml,yml} + root YAML.
##           Pure regex-based, no subprocess calls.
## @invariants
##   - Shell literal credential detection (PASSWORD=test2026, --password test2026)
##   - CI literal secret detection (key: value not ${{ secrets.* }})
##   - Install script :-literal detection for credential vars (should use :?)
##   - Python literal detection (VAR = "literal" / "key": "literal" — quoted only)
##   - YAML literal detection (credential_key: literal, с исключением SoT/GENERATED)
##   - All five tests are @pytest.mark.predeploy for CI parallelisation
##   - None require Docker or any running service
##   - Uses conftest platform_root fixture for path resolution
## @rationale — AC-T7 from DevPlan: two QA audits independently flagged hardcoded test2026
##              password in 4 files (platform-test.yml, issue-cert.sh, test_e2e_hermes_auth.py,
##              gate-loop/SKILL.md). This test prevents regression.
## @changes   CREATED: 2026-07-09 | Wave 1: TASK-7 security credential scanner
## @changes   2026-08-13 | DevPlan 160 W3 T3.4 — +.py/.yaml сканеры
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────────────────────────

# region CONSTANTS

#: Substrings identifying credential-related variable names (case-insensitive match).
#: Used across all three test scanners to focus on password/token/api_key/secret patterns
#: and avoid false positives on non-credential variables.
CREDENTIAL_VAR_SUBSTRINGS: list[str] = [
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "SECRET",
]

#: Variable name suffix patterns that indicate path/URL configuration rather than a
#: credential value. Variables ending with these suffixes reference file paths or URLs
#: and should not trigger a credential finding.
NON_CREDENTIAL_VAR_SUFFIXES: list[str] = [
    "_FILE",
    "_DIR",
    "_PATH",
    "_URL",
    "_ENDPOINT",
    "_HOST",
    "_PORT",
    "_BUCKET",
    "_PREFIX",
]

#: Known safe default values that are not credentials (compared case-insensitively).
SAFE_FALLBACK_DEFAULTS: set[str] = {
    "0",
    "1",
    "true",
    "false",
    "none",
    "unknown",
    "null",
    "yes",
    "no",
    "on",
    "off",
}

#: W3 T3.4 — .py сканер: VAR = "literal" (assign) и "key": "literal" (dict). Значение ОБЯЗАНО
#: быть кавычковым литералом — переменные/вызовы/списки не матчатся (значение после = не в кавычках).
_PY_ASSIGN_CRED_RE = re.compile(
    r"""(?i)(?:^|[\s(,=])(?P<var>[\w.]*(?:password|token|api_key|secret)[\w]*)\s*=\s*(['"])(?P<val>.*?)\2"""
)
_PY_DICT_CRED_RE = re.compile(
    r"""(?i)(['"])(?P<var>[\w]*(?:password|token|api_key|secret)[\w]*)\1\s*:\s*(['"])(?P<val>.*?)\3"""
)

#: W3 T3.4 — .yaml сканер: credential_key: literal (YAML-стиль, colon-space).
_YAML_CRED_RE = re.compile(
    r"""(?i)(?:^|\s)(?P<var>[\w]*(?:password|token|api_key|secret)[\w]*)\s*:\s*(['"]?)(?P<val>[^#\n]*?)\2\s*(?:#.*)?$"""
)

#: Файлы с НАМЕРЕННЫМИ CI/test значениями или GENERATED (инвариант 11): НЕ сканируются
#: .yaml-сканером. platform-infra.yaml — SoT env_defaults (CI-test значения); GENERATED —
#: platform-env.yaml/secrets-manifest.yaml/.env.example/entrypoint-manifest.yaml.
_YAML_CRED_FILE_EXCLUDES: set[str] = {
    "platform-infra.yaml",
    "platform-env.yaml",
    "secrets-manifest.yaml",
    ".env.example",
    "entrypoint-manifest.yaml",
}

# endregion CONSTANTS


# ── Helpers ───────────────────────────────────────────────────────────────────────────────────────

# region HELPERS


def _is_shell_variable_ref(value: str) -> bool:
    """Check if a value is a shell variable reference (${VAR}, $(cmd), $VAR, ${{ ... }}).

    Strips leading/trailing quotes before checking to handle cases like
    ``VAR="$2"`` where the captured value includes the opening quote.

    ## @purpose — Distinguish literal credential values from safe variable references.
    ## @io — ⇥ value: str → ⎋ bool (True if value is a variable reference)
    ## @complexity — O(1) — prefix checks + strip quotes
    ## @invariants
    ##   - Strips leading/trailing quotes before checking
    ##   - Returns True if value starts with $ (${VAR}, $VAR, $(cmd))
    ##   - Returns True if value contains ${{ (GitHub Actions secrets syntax)
    ##   - Returns False for literal strings like "test2026", "abc123"
    """
    cleaned = value.strip("\"'")
    return cleaned.startswith("$") or "${" in cleaned or "${{" in cleaned


def _truncate(value: str, max_len: int = 30) -> str:
    """Truncate a string for safe logging, never exceeding max_len chars.

    ## @purpose — Prevent secret leakage in test output while still providing context.
    ## @io — ⇥ value: str, max_len: int → ⎋ str (truncated)
    ## @complexity — O(1)
    """
    if len(value) <= max_len:
        return value
    return value[:max_len] + "..."


def _scan_shell_for_literal_assignments(
    filepath: str,
    varname_substrings: list[str],
) -> list[tuple[int, str, str]]:
    """Scan a shell script for literal credential assignments (VAR=literal_value).

    ## @purpose — Detect patterns like PASSWORD=test2026 where value is a literal
    ##            string, not a shell variable reference.
    ## @io — ⇥ filepath: str, varname_substrings: list[str] → ⎋ list of (line_no, varname, value)
    ## @complexity — O(L * V) where L = lines, V = varname_substrings length
    ## @invariants
    ##   - Skips comment lines (# prefix)
    ##   - Matches: [export] VAR=value (or VAR=value after whitespace/;)
    ##   - Excludes values that are shell variable refs (${VAR}, $VAR, $(cmd), ${{ ... }})
    ##   - Returns empty list if file cannot be read
    ##   - One finding per line per matched varname (first matching pattern wins)
    """
    # region BLOCK_ScanAssignment
    findings: list[tuple[int, str, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    # Track inline Python blocks (e.g., python3 -c "...") to skip
    # False positive mitigation: Python code embedded in bash heredocs
    # is not shell credential assignment and should not be scanned.
    # ⚠️ TRAP[BUG] · 2026-07-20 · P2 · False positives in python -c inline code
    # · Symptom: Secrets manifest Python inline code (Plan 018 TASK-5) triggered
    #   false positives on lines like "secrets = data.get('secrets', [])"
    # · Root: Scanner regex matched Python assignment syntax inside bash heredoc
    # · Fix: Skip lines inside python3/python -c blocks (heredoc via double-quote)
    # · Prevention: If new false positives appear from Python inline code in shell,
    #   extend the _in_python_block tracking below
    in_python_block: bool = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Track python -c inline blocks to skip Python code
        if re.search(r'(?:python|python3)\s+-c\s+"[^"]*$', stripped) or in_python_block:
            if stripped.endswith('")'):
                in_python_block = False
            elif not in_python_block:
                in_python_block = True
            # Skip lines inside Python inline code — they are not shell assignments
            if in_python_block or stripped.endswith('")'):
                continue
            in_python_block = bool(re.search(r'(?:python|python3)\s+-c\s+"[^"]*$', stripped))

        for varname in varname_substrings:
            # Match: [export] [local] [readonly] VAR=<value> with flexible prefix
            # The value cannot be a shell variable reference (${, $(, $ prefix)
            pattern = re.compile(
                r"(?i)(?:^|\s)(?:export|local|readonly)?\s*(?P<fullvar>\w*"
                + re.escape(varname)
                + r"\w*)\s*=\s*(?P<value>\S+)"
            )
            m = pattern.search(stripped)
            if not m:
                continue

            fullvar: str = m.group("fullvar")
            raw_value: str = m.group("value").rstrip(";#\"'")

            # Skip if the variable name indicates a path/config (not credential value)
            if any(fullvar.upper().endswith(suffix) for suffix in NON_CREDENTIAL_VAR_SUFFIXES):
                continue

            # Skip if value is a shell variable reference (after quote stripping)
            if _is_shell_variable_ref(raw_value):
                continue

            # Skip shell array declarations (VAR=(...) — array values are not credentials)
            if raw_value.startswith("("):
                continue

            # Strip leading quotes for path/value inspection
            stripped_value: str = raw_value.lstrip("\"'")
            # Skip path-like values (absolute paths starting with /)
            if stripped_value.startswith("/"):
                continue

            findings.append((i, varname, raw_value))
            break  # one finding per line for first matched varname
    # endregion BLOCK_ScanAssignment
    return findings


def _scan_shell_for_password_argument(
    filepath: str,
) -> list[tuple[int, str]]:
    """Scan a shell script for --password LITERAL arguments (not variable refs).

    ## @purpose — Detect --password test2026 where the argument is a literal string.
    ## @io — ⇥ filepath: str → ⎋ list of (line_no, value)
    ## @complexity — O(L) where L = lines
    ## @invariants
    ##   - Matches: --password LITERAL where LITERAL does not start with $ or ${
    ##   - Skips quoted variable references ("$password", '${PW}') — the value is a
    ##     shell variable reference, not a literal (2026-07-31, plan 102)
    ##   - Quoted literals ("test2026") are STILL detected — the post-filter only
    ##     skips values whose unquoted inner content starts with $
    ##   - Skips comment lines
    ##   - Returns empty list if file cannot be read
    ## @changes  2026-07-31 | plan 102 | false positive fix: core/lib/secrets.sh:62
    ##           `--password "$password"` matched as literal — regex lookahead only
    ##           excluded a bare leading $; quoted variable refs now post-filtered
    ##           via _is_shell_variable_ref (quote-strip + $ prefix check)
    """
    # region BLOCK_ScanPasswordArg
    findings: list[tuple[int, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Match --password followed by a non-whitespace value that is NOT a $ ref
        pattern = re.compile(r"(?i)--password\s+(?P<value>(?!\$\{|\$\(|\$)\S+)")
        for m in pattern.finditer(stripped):
            raw_value: str = m.group("value")
            # ⚠️ TRAP[BUG] · 2026-07-31 · P2 · False positive: quoted variable refs
            #   ("$password", '${PW}') matched as literal --password values
            # · Symptom: core/lib/secrets.sh:62 `--password "$password"` failed the
            #   scanner — negative lookahead only excludes a bare leading $, so a
            #   value starting with a quote passed as "literal"
            # · Root: regex lookahead (?!\$\{|\$\(|\$) checks the first char only;
            #   quoted variable references were indistinguishable from quoted literals
            # · Fix: post-filter via _is_shell_variable_ref — strip surrounding quotes,
            #   if inner content starts with $ it is a variable reference → skip
            # · Prevention: real quoted literals ("test2026") still match — the filter
            #   only excludes values whose unquoted content is a $ reference
            if _is_shell_variable_ref(raw_value):
                logger.info(
                    "[IMP:8][scan_password_arg][skip-var-ref] %s:%d --password %s (quoted variable reference, not literal)",
                    filepath,
                    i,
                    _truncate(raw_value),
                )
                continue
            findings.append((i, raw_value))
    # endregion BLOCK_ScanPasswordArg
    return findings


def _scan_shell_for_literal_fallback(
    filepath: str,
    varname_substrings: list[str],
) -> list[tuple[int, str, str]]:
    """Scan a shell script for ${VAR:-literal} fallback with literal credential value.

    ## @purpose — Detect ${MONITORING_AUTH_PASSWORD:-test2026} patterns where
    ##            a credential variable has a literal default value.
    ##            Should use :? (fail-fast) instead of :-literal.
    ## @io — ⇥ filepath: str, varname_substrings: list[str] → ⎋ list of (line_no, full_var_name, literal_value)
    ## @complexity — O(L * V) where L = lines, V = varname_substrings length
    ## @invariants
    ##   - Matches: ${VAR:-literal} where VAR contains one of varname_substrings
    ##   - Skips fallbacks where value contains $ (nested variable reference)
    ##   - Skips common non-secret defaults: 0, 1, true, false, none, unknown
    ##   - Returns empty list if file cannot be read
    """
    # region BLOCK_ScanFallback
    findings: list[tuple[int, str, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        for varname in varname_substrings:
            # Match ${VARNAME:-something} where something is a literal word
            pattern = re.compile(r"\$\{(?P<fullvar>\w*" + re.escape(varname) + r"\w*):-(?P<value>[^}]*[a-zA-Z][^}]*)\}")
            for m in pattern.finditer(line):
                fullvar: str = m.group("fullvar")
                value: str = m.group("value").strip()

                # Skip if the variable name indicates a path/config (not credential value)
                if any(fullvar.upper().endswith(suffix) for suffix in NON_CREDENTIAL_VAR_SUFFIXES):
                    continue

                # Skip if the fallback value contains a variable reference
                if "$" in value:
                    continue

                # Skip known non-secret defaults
                if value.lower() in SAFE_FALLBACK_DEFAULTS:
                    continue

                # Skip URL patterns (URL defaults are configuration, not credentials)
                if value.startswith(("http://", "https://")):
                    continue

                # Skip path-like values (absolute paths starting with /)
                if value.startswith("/"):
                    continue

                findings.append((i, fullvar, value))
    # endregion BLOCK_ScanFallback
    return findings


def _scan_ci_for_literal_secrets(
    filepath: str,
) -> list[tuple[int, str, str]]:
    """Scan a CI workflow YAML for literal credential values.

    ## @purpose — Detect PASSWORD: test2026 in YAML where value is not ${{ secrets.* }}.
    ## @io — ⇥ filepath: str → ⎋ list of (line_no, key, value)
    ## @complexity — O(L) where L = lines
    ## @invariants
    ##   - Matches: credential_key: literal_value (colon-space separator, YAML style)
    ##   - Skips values using ${{ secrets.* }}, ${{ env.* }}, ${{ vars.* }}
    ##   - Skips values containing ${} (bash variable refs in run steps)
    ##   - Skips commented lines (# prefix)
    ##   - Skips empty/placeholder values ("", '', etc.)
    ##   - Returns empty list if file cannot be read
    """
    # region BLOCK_ScanCI
    findings: list[tuple[int, str, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    EMPTY_PLACEHOLDERS: set[str] = {'""', "''", ""}

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Match: key: value where key implies credential and value is literal
        pattern = re.compile(r"(?i)(?P<key>\w*(?:password|token|api_key|secret)\w*)\s*:\s*(?P<value>\S+)")
        m = pattern.search(stripped)
        if not m:
            continue

        value: str = m.group("value").rstrip()

        # Skip if value uses GitHub Actions secrets/vars syntax
        if "${{" in value:
            continue

        # Skip if value contains bash variable refs ($VAR or ${VAR}) — these are
        # not literal secrets but variable references in run steps
        if "${" in value or re.search(r"(?<!\$)\$\w+", value):
            continue

        if value in EMPTY_PLACEHOLDERS:
            continue

        # Skip GitHub permission levels (id-token: write, packages: read, ...) — H13
        # provenance добавил id-token: write/attestations: write; это гранты прав OIDC,
        # НЕ credential-значения (настоящие секреты не бывают "read"/"write"/"none").
        if value.lower() in {"read", "write", "none"}:
            continue

        findings.append((i, m.group("key"), value))
    # endregion BLOCK_ScanCI
    return findings


def _scan_for_token_in_git_url(
    filepath: str,
) -> list[tuple[int, str]]:
    """Scan a file for token embedded in git URL: https://${TOKEN}@github.com/...

    Detects patterns like:
      https://x-access-token:${GIT_MIRROR_TOKEN}@github.com/...
      TARGET_URL="https://${GIT_MIRROR_TOKEN}@github.com/..."
      git push --mirror "https://${TOKEN}@..."

    ## @purpose — Prevent credentials from appearing in git URLs (visible in ps aux,
    ##            shell history, CI logs). All tokens must use GIT_ASKPASS mechanism.
    ## @io — ⇥ filepath: str → ⎋ list of (line_no, matched_text)
    ## @complexity — O(L) where L = lines in file
    ## @invariants
    ##   - Matches: https://[...]${...}@ — любой URL с переменной в позиции креденшала
    ##     (включая форму user:${TOKEN}@host, не только ${TOKEN}@host)
    ##   - Returns empty list if file cannot be read
    ## @changes — 2026-07-16 | F2 (DevPlan 019 QAAudit): расширен на user:${TOKEN}@ — исходный
    ##            regex матчил только ${TOKEN} сразу после https://, пропуская
    ##            https://x-access-token:${GIT_MIRROR_TOKEN}@github.com/...
    """
    # region BLOCK_ScanGitToken
    findings: list[tuple[int, str]] = []
    # Regex: https://[optional_user:]${VARIABLE}@
    # Matches both:
    #   https://${GIT_MIRROR_TOKEN}@github.com/...
    #   https://x-access-token:${GIT_MIRROR_TOKEN}@github.com/...
    # [^\s"'@]* — любой префикс (username, username:password)
    # \$\{[^}]+\} — одна переменная в позиции креденшала
    # @ — завершает зону аутентификации
    pattern = re.compile(r"https://[^\s\"'@]*\$\{[^}]+\}@")
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        findings.extend((i, m.group()) for m in pattern.finditer(stripped))
    # endregion BLOCK_ScanGitToken
    return findings


def _gather_files(root_dir: str, glob_pattern: str) -> list[pathlib.Path]:
    """Gather files matching a glob pattern under root_dir, sorted.

    ## @purpose — Centralised file discovery for all three test scanners.
    ## @io — ⇥ root_dir: str, glob_pattern: str → ⎋ list[Path]
    ## @complexity — O(F) where F = number of matching files
    """
    return sorted(pathlib.Path(root_dir).rglob(glob_pattern))


def _scan_directory_for_token_in_git_url(
    root_dir: str,
    glob_pattern: str,
    platform_root: str,
    logger: logging.Logger,
) -> list[tuple[str, int, str]]:
    """Scan a directory for token-in-git-url across all matching files.

    ## @purpose — Shared scanner for both core shell scripts and .github workflows.
    ##            Uses _scan_for_token_in_git_url per file, aggregates findings
    ##            with relative paths for pytest.fail output (DRY).
    ## @io — ⇥ root_dir, glob_pattern, platform_root, logger → ⎋ list of (rel_path, line_no, matched_text)
    ## @complexity — O(F * L) where F = files, L = lines
    ## @invariants
    ##   - Delegates per-file scanning to _scan_for_token_in_git_url (single regex)
    ##   - Returns findings with paths relative to platform_root for readable output
    ##   - Logs file count at IMP:8, each finding at IMP:8
    """
    # region BLOCK_ScanDirectory
    findings: list[tuple[str, int, str]] = []
    files = sorted(pathlib.Path(root_dir).rglob(glob_pattern))
    logger.info("[IMP:8][scan_git_url] Scanning %d files matching '%s' in %s", len(files), glob_pattern, root_dir)

    for file in files:
        rel_path = os.path.relpath(str(file), platform_root)
        file_findings = _scan_for_token_in_git_url(str(file))
        for line_no, matched_text in file_findings:
            logger.info("[IMP:8][scan_git_url][found] %s:%d %s", rel_path, line_no, matched_text)
            findings.append((rel_path, line_no, matched_text))

    return findings
    # endregion BLOCK_ScanDirectory


# ── W3 T3.4 — .py/.yaml сканеры ──────────────────────────────────────────────────────────────────

# region HELPERS_PY_YAML


def _norm_credential(value: str) -> str:
    """Нормализация для self-referential сравнения: только [a-z0-9], lowercase.

    ## @purpose — SECRETS_PROVISION="secrets_provision" — значение зеркалит имя переменной
    ##            (state-фаза, не секрет) → пропускается. test2026 vs PASSWORD — не зеркало → ловится.
    ## @io — ⇥ value: str → ⎋ str
    ## @complexity — O(N)
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_literal_credential_value(fullvar: str, value: str) -> bool:
    """Общий фильтр «литеральный секрет?» для .py/.yaml сканеров.

    ## @purpose — Секрет = кавычковый литерал: не путь/конфиг-суффикс, не переменная-ссылка,
    ##            не os.environ-темплейт, не self-referential значение, не pure-идентификатор.
    ## @io — ⇥ fullvar: str (имя переменной/ключа), value: str (значение) → ⎋ bool (True = секрет)
    ## @complexity — O(1)
    ## @invariants
    ##   - *_FILE/_DIR/_PATH/_URL/_ENDPOINT/_HOST/_PORT/_BUCKET/_PREFIX/_ENV — не секреты
    ##   - Пустые/булевы/SAFE_FALLBACK_DEFAULTS — не секреты
    ##   - os.environ/... / env.... — темплейты (LiteLLM), не литералы
    ##   - value == зеркало fullvar (нормализованно) — state-фазы, не секреты
    ##   - Pure-идентификатор БЕЗ цифр (BWS_ACCESS_TOKEN) — имя переменной, не секрет;
    ##     test2026 содержит цифры → НЕ pure-идентификатор → ловится (U-T7)
    """
    if any(fullvar.upper().endswith(suffix) for suffix in NON_CREDENTIAL_VAR_SUFFIXES):
        return False
    if not value:
        return False
    if value.lower() in SAFE_FALLBACK_DEFAULTS:
        return False
    if _is_shell_variable_ref(value):
        return False  # ${VAR} / $VAR / ${{ ... }} / $(cmd) — ссылки, не литералы
    if value.startswith(("os.environ", "env.")):
        return False
    if _norm_credential(value) == _norm_credential(fullvar):
        return False  # self-referential: SECRETS_PROVISION="secrets_provision"
    # pure-идентификатор без цифр (BWS_ACCESS_TOKEN) — имя переменной, не секрет;
    # test2026 содержит цифры → НЕ pure-идентификатор → ловится (U-T7)
    return not (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) and not re.search(r"\d", value))


def _scan_python_for_literal_credentials(filepath: str) -> list[tuple[int, str, str]]:
    """Scan a .py file for literal credential assignments (VAR = "literal" / "key": "literal").

    ## @purpose — W3 T3.4: Python-модули (core/**/*.py) — PASSWORD = "test2026",
    ##            {"password": "test2026"}. Значение ОБЯЗАНО быть кавычковым литералом.
    ## @io — ⇥ filepath: str → ⎋ list[(line_no, varname, value)]
    ## @complexity — O(L) где L = строки
    ## @invariants
    ##   - Только «= "..."» и «"key": "..."» (quoted literal) — переменные/вызовы не матчатся
    ##   - Комментарии и пустые строки пропускаются
    ##   - Общий фильтр _is_literal_credential_value (пути/булевы/os.environ/self-ref/identifier)
    ##   - Тестовые fixture-данные (tests/) НЕ в скоупе — сканируется только core/
    """
    findings: list[tuple[int, str, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for m in _PY_ASSIGN_CRED_RE.finditer(stripped):
            fullvar, value = m.group("var"), m.group("val")
            if _is_literal_credential_value(fullvar, value):
                findings.append((i, fullvar, value))
        for m in _PY_DICT_CRED_RE.finditer(stripped):
            fullvar, value = m.group("var"), m.group("val")
            if _is_literal_credential_value(fullvar, value):
                findings.append((i, fullvar, value))
    return findings


def _scan_yaml_for_literal_credentials(filepath: str) -> list[tuple[int, str, str]]:
    """Scan a .yaml/.yml file for literal credential values (credential_key: literal).

    ## @purpose — W3 T3.4: YAML-конфиги — password: test2026, api_key: sk-xxx. Значение
    ##            обязано быть литералом (не ${VAR}, не ${{ secrets.* }}, не os.environ/X).
    ## @io — ⇥ filepath: str → ⎋ list[(line_no, varname, value)]
    ## @complexity — O(L) где L = строки
    ## @invariants
    ##   - Числовые (max_tokens: 4096), URL/пути, массивы/объекты, '*' — не секреты
    ##   - Комментарии/пустые строки пропускаются
    ##   - Файлы с намеренными значениями (SoT platform-infra.yaml, GENERATED) исключаются
    ##     вызывающим тестом (_YAML_CRED_FILE_EXCLUDES)
    """
    findings: list[tuple[int, str, str]] = []
    try:
        with pathlib.Path(filepath).open(encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _YAML_CRED_RE.search(stripped)
        if not m:
            continue
        fullvar = m.group("var")
        value = m.group("val").strip().strip("'\"")
        if not _is_literal_credential_value(fullvar, value):
            continue
        if re.fullmatch(r"\d+(\.\d+)?", value):
            continue  # max_tokens: 4096
        if value.startswith(("/", "http://", "https://")):
            continue  # URL/пути — конфигурация
        if value.startswith(("(", "[", "{")) or "*" in value:
            continue  # структуры/placeholder
        findings.append((i, fullvar, value))
    return findings


# endregion HELPERS_PY_YAML


# endregion HELPERS


# ── Tests ─────────────────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 4: No token embedded in git URL (AC6/AC7)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_token_in_git_url
## @purpose — Scan core/entrypoints/*.sh and core/internal/**/*.sh for tokens
##            embedded in git URLs like https://${TOKEN}@github.com/.... All tokens
##            must use GIT_ASKPASS mechanism instead.
##            AC6 from DevPlan §TASK-3.6.
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on token in git URL)
## @complexity — O(F * L) where F = shell files, L = lines per file
## @invariants
##   - Scans core/entrypoints/*.sh and core/internal/**/*.sh
##   - Detects https://${...TOKEN...}@ patterns
##   - pytest.fail with line detail on detection


@pytest.mark.predeploy
@ldd_trajectory
def test_no_token_in_git_url(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/entrypoints/*.sh + core/internal/**/*.sh → ⊕ _scan_directory_for_token_in_git_url
    #   → ◇ token in git URL? → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    core_dir: str = Path(platform_root) / "core"
    logger.info("[IMP:7][test_git_url_token] Scanning for token in git URL: %s", core_dir)

    all_findings: list[tuple[str, int, str]] = []
    # endregion BLOCK_Setup

    # region BLOCK_ScanEntrypoints
    entrypoints_dir: str = Path(core_dir) / "entrypoints"
    findings = _scan_directory_for_token_in_git_url(entrypoints_dir, "*.sh", platform_root, logger)
    all_findings.extend(findings)
    # endregion BLOCK_ScanEntrypoints

    # region BLOCK_ScanInternal
    internal_dir: str = Path(core_dir) / "internal"
    findings = _scan_directory_for_token_in_git_url(internal_dir, "*.sh", platform_root, logger)
    all_findings.extend(findings)
    # endregion BLOCK_ScanInternal

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_git_url_token] ⛔ Found %d token(s) in git URL(s)", total)
        detail_lines: list[str] = []
        for fp, ln, match in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → {match}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_git_url_token] %s", line_str)

        pytest.fail(
            f"Found {total} token(s) embedded in git URL(s).\n"
            f"All tokens must use GIT_ASKPASS mechanism, not embedded in URL.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_git_url_token] ✅ No tokens embedded in git URLs in core shell scripts")
    # endregion BLOCK_Assert


# endregion FUNC_test_no_token_in_git_url


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 5: No token embedded in git URL in .github workflows/actions (TASK-4 scope extension)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_token_in_git_url_workflows
## @purpose — Scan .github/workflows/**/*.yml and .github/actions/**/*.yml for tokens
##            embedded in git URLs like https://${TOKEN}@github.com/.... Extends the
##            credential gate scope from core/**/*.sh to .github/**/*.yml (TASK-4).
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on token in git URL)
## @complexity — O(F * L) where F = YAML files, L = lines per file
## @invariants
##   - Scans .github/workflows/**/*.yml and .github/actions/**/*.yml
##   - Detects https://${...TOKEN...}@ patterns
##   - Uses the same _scan_for_token_in_git_url helper (DRY)
##   - pytest.fail with line detail on detection


@pytest.mark.predeploy
@ldd_trajectory
def test_no_token_in_git_url_workflows(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ .github/workflows/**/*.yml + .github/actions/**/*.yml
    #   → ⊕ _scan_directory_for_token_in_git_url → ◇ token in git URL?
    #   → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    github_dir: str = Path(platform_root) / ".github"
    logger.info("[IMP:7][test_git_url_workflows] Scanning for token in git URL: %s", github_dir)

    all_findings: list[tuple[str, int, str]] = []
    # endregion BLOCK_Setup

    # region BLOCK_ScanWorkflows
    workflows_dir: str = Path(github_dir) / "workflows"
    if pathlib.Path(workflows_dir).exists():
        findings = _scan_directory_for_token_in_git_url(workflows_dir, "*.yml", platform_root, logger)
        all_findings.extend(findings)
        logger.info(
            "[IMP:8][test_git_url_workflows] Scanned workflows/: %d files checked",
            len(list(pathlib.Path(workflows_dir).rglob("*.yml"))),
        )
    else:
        logger.info("[IMP:4][test_git_url_workflows] workflows/ directory not found — skipping")
    # endregion BLOCK_ScanWorkflows

    # region BLOCK_ScanActions
    actions_dir: str = Path(github_dir) / "actions"
    if pathlib.Path(actions_dir).exists():
        findings = _scan_directory_for_token_in_git_url(actions_dir, "*.yml", platform_root, logger)
        all_findings.extend(findings)
        logger.info(
            "[IMP:8][test_git_url_workflows] Scanned actions/: %d files checked",
            len(list(pathlib.Path(actions_dir).rglob("*.yml"))),
        )
    else:
        logger.info("[IMP:4][test_git_url_workflows] actions/ directory not found — skipping")
    # endregion BLOCK_ScanActions

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_git_url_workflows] ⛔ Found %d token(s) in git URL(s) in .github/", total)
        detail_lines: list[str] = []
        for fp, ln, match in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → {match}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_git_url_workflows] %s", line_str)

        pytest.fail(
            f"Found {total} token(s) embedded in git URL(s) in .github/.\n"
            f"All tokens must use GIT_ASKPASS mechanism, not embedded in URL.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_git_url_workflows] ✅ No tokens embedded in git URLs in .github YAML files")
    # endregion BLOCK_Assert


# endregion FUNC_test_no_token_in_git_url_workflows


# region TESTS

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 1: No hardcoded password in shell scripts (AC-T7.1)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_hardcoded_password_in_shell_scripts
## @purpose — Scan core/**/*.sh for hardcoded credential values (PASSWORD=literal,
##            --password literal, token=literal, api_key=literal, secret=literal,
##            ${VAR:-literal} fallback). All credentials must use $VAR references.
##            AC-T7.1 from DevPlan §TASK-7.
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on hardcoded credentials)
## @complexity — O(F * L * V) where F = shell files, L = lines per file, V = credential patterns
## @invariants
##   - Scans core/ recursively, not just top-level scripts
##   - Uses three scanners: literal_assignments, password_args, literal_fallbacks
##   - Each finding reports: (file, line, description) for actionable failure messages
##   - Findings sorted by file:line for readable output
##   - pytest.fail with full detail on detection


@pytest.mark.predeploy
@ldd_trajectory
def test_no_hardcoded_password_in_shell_scripts(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/**/*.sh → ⊕ _scan_shell_for_literal_assignments + _scan_shell_for_password_argument
    #   + _scan_shell_for_literal_fallback → ◇ any findings? → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    core_dir: str = Path(platform_root) / "core"
    logger.info("[IMP:7][test_shell_scripts] Scanning core shell scripts: %s", core_dir)

    shell_files: list[pathlib.Path] = _gather_files(core_dir, "*.sh")
    logger.info("[IMP:8][test_shell_scripts] Discovered %d shell scripts", len(shell_files))
    # endregion BLOCK_Setup

    # region BLOCK_Scan
    all_findings: list[tuple[str, int, str, str]] = []
    # (relative_path, line_no, category, truncated_value)
    # Categories: "assign" (VAR=literal), "arg" (--password literal), "fallback" (${VAR:-literal})

    for sh_file in shell_files:
        rel_path: str = os.path.relpath(str(sh_file), platform_root)
        filepath: str = str(sh_file)

        # Scanner 1: literal credential assignments (PASSWORD=test2026)
        assignments = _scan_shell_for_literal_assignments(filepath, CREDENTIAL_VAR_SUBSTRINGS)
        for line_no, varname, value in assignments:
            logger.info("[IMP:8][test_shell_scripts][assign] %s:%d %s=%s", rel_path, line_no, varname, _truncate(value))
            all_findings.append((rel_path, line_no, "assign", f"{varname}={_truncate(value)}"))

        # Scanner 2: --password literal arguments
        password_args = _scan_shell_for_password_argument(filepath)
        for line_no, value in password_args:
            logger.info("[IMP:8][test_shell_scripts][arg] %s:%d --password %s", rel_path, line_no, _truncate(value))
            all_findings.append((rel_path, line_no, "arg", f"--password {_truncate(value)}"))

        # Scanner 3: ${VAR:-literal} credential fallbacks
        fallbacks = _scan_shell_for_literal_fallback(filepath, CREDENTIAL_VAR_SUBSTRINGS)

        # Scanner 4: token embedded in git URL
        git_url_tokens = _scan_for_token_in_git_url(filepath)
        for line_no, fullvar, value in fallbacks:
            logger.info(
                "[IMP:8][test_shell_scripts][fallback] %s:%d ${{%s}}:-%s", rel_path, line_no, fullvar, _truncate(value)
            )
            desc = "${" + fullvar + ":-" + _truncate(value) + "}"
            all_findings.append((rel_path, line_no, "fallback", desc))

        # Scanner 4 findings: token in git URL
        for line_no, matched_text in git_url_tokens:
            logger.info(
                "[IMP:8][test_shell_scripts][git_url_token] %s:%d %s", rel_path, line_no, _truncate(matched_text)
            )
            all_findings.append((rel_path, line_no, "git_url_token", _truncate(matched_text)))
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_shell_scripts] ⛔ Found %d hardcoded credential(s) in %s", total, core_dir)
        detail_lines: list[str] = []
        for fp, ln, cat, desc in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → [{cat}] {desc}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_shell_scripts] %s", line_str)

        pytest.fail(
            f"Found {total} hardcoded credential(s) in shell scripts.\n"
            f"All credential values must use ${{VAR}} references, never literal values.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_shell_scripts] ✅ No hardcoded credentials in %d shell scripts", len(shell_files))
    # endregion BLOCK_Assert


# endregion FUNC_test_no_hardcoded_password_in_shell_scripts


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 2: No hardcoded credentials in CI workflows (AC-T7.2)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_hardcoded_credentials_in_ci_workflows
## @purpose — Scan .github/workflows/*.yml for literal credential values (not ${{ secrets.* }}).
##            All CI credential values must reference GitHub Actions secrets.
##            AC-T7.2 from DevPlan §TASK-7.
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on literal secrets)
## @complexity — O(F * L) where F = YAML files, L = lines
## @invariants
##   - Scans all .yml files in .github/workflows/
##   - Detects: key: literal_value where key implies credential (password/token/api_key/secret)
##   - Skips: values using ${{ secrets.* }}, ${{ env.* }}, ${{ vars.* }}
##   - pytest.fail with sorted detail per file:line


@pytest.mark.predeploy
@ldd_trajectory
def test_no_hardcoded_credentials_in_ci_workflows(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ .github/workflows/*.yml → ⊕ _scan_ci_for_literal_secrets → ◇ literal
    #   credential values? → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    workflows_dir: str = Path(platform_root) / ".github" / "workflows"
    logger.info("[IMP:7][test_ci_workflows] Scanning CI workflows: %s", workflows_dir)

    ci_files: list[pathlib.Path] = sorted(pathlib.Path(workflows_dir).glob("*.yml"))
    if not ci_files:
        logger.warning("[IMP:4][test_ci_workflows] No .yml files found in %s — skipping scan", workflows_dir)
        pytest.skip(f"No CI workflow files found in {workflows_dir}")
        return  # unreachable but satisfies type checker

    logger.info("[IMP:8][test_ci_workflows] Discovered %d CI workflow files", len(ci_files))
    # endregion BLOCK_Setup

    # region BLOCK_Scan
    all_findings: list[tuple[str, int, str, str]] = []
    # (relative_path, line_no, key, truncated_value)

    for ci_file in ci_files:
        rel_path: str = os.path.relpath(str(ci_file), platform_root)
        filepath: str = str(ci_file)

        findings = _scan_ci_for_literal_secrets(filepath)
        for line_no, key, value in findings:
            logger.info("[IMP:8][test_ci_workflows][literal] %s:%d %s: %s", rel_path, line_no, key, _truncate(value))
            all_findings.append((rel_path, line_no, key, _truncate(value)))
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_ci_workflows] ⛔ Found %d literal credential(s) in CI workflows", total)
        detail_lines: list[str] = []
        for fp, ln, key, val in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → {key}: {val}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_ci_workflows] %s", line_str)

        pytest.fail(
            f"Found {total} literal credential(s) in CI workflows.\n"
            f"All credential values must use ${{{{ secrets.* }}}} syntax.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_ci_workflows] ✅ No literal credentials in %d CI workflow files", len(ci_files))
    # endregion BLOCK_Assert


# endregion FUNC_test_no_hardcoded_credentials_in_ci_workflows


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 3: No :-literal fallback for credential vars in install scripts (AC-T7.3)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_password_fallback_in_install_scripts
## @purpose — Check that install scripts use :? (fail-fast) instead of :-literal for
##            critical credential variable fallbacks. A literal default value for
##            PASSWORD/TOKEN/API_KEY/SECRET is a security risk — the script should fail
##            immediately if the env var is not set.
##            AC-T7.3 from DevPlan §TASK-7.
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on :-literal credential fallback)
## @complexity — O(F * L * V) where F = install files, L = lines, V = credential patterns
## @invariants
##   - Scans core/**/install*.sh for credential variable fallback patterns
##   - A finding is ${VAR:-literal} where VAR matches CREDENTIAL_VAR_SUBSTRINGS
##   - Skips non-credential defaults (0, 1, true, false, none, unknown, URLs)
##   - Skips fallbacks containing nested variable references
##   - The fix is: replace ${VAR:-literal} with ${VAR:?VAR not set}


@pytest.mark.predeploy
@ldd_trajectory
def test_no_password_fallback_in_install_scripts(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/**/install*.sh → ⊕ _scan_shell_for_literal_fallback → ◇ credential
    #   :-literal fallback exists? → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    core_dir: str = Path(platform_root) / "core"
    logger.info(
        "[IMP:7][test_install_scripts] Scanning install scripts for :-literal credential fallbacks: %s", core_dir
    )

    install_files: list[pathlib.Path] = _gather_files(core_dir, "install*.sh")
    if not install_files:
        logger.warning("[IMP:4][test_install_scripts] No install*.sh files found in %s — skipping scan", core_dir)
        pytest.skip(f"No install scripts found in {core_dir}")
        return

    logger.info("[IMP:8][test_install_scripts] Discovered %d install scripts", len(install_files))
    # endregion BLOCK_Setup

    # region BLOCK_Scan
    all_findings: list[tuple[str, int, str, str, str]] = []
    # (relative_path, line_no, full_var_name, literal_value, suggestion)

    for install_file in install_files:
        rel_path: str = os.path.relpath(str(install_file), platform_root)
        filepath: str = str(install_file)

        # Scan for ${VAR:-literal} with credential variable names
        fallbacks = _scan_shell_for_literal_fallback(filepath, CREDENTIAL_VAR_SUBSTRINGS)
        for line_no, fullvar, value in fallbacks:
            suggestion: str = "${" + fullvar + ":?" + fullvar + " not set}"
            logger.info(
                "[IMP:8][test_install_scripts][:-fallback] %s:%d $%s:-%s → suggest %s",
                rel_path,
                line_no,
                fullvar,
                _truncate(value),
                suggestion,
            )
            all_findings.append((rel_path, line_no, fullvar, _truncate(value), suggestion))
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error(
            "[IMP:9][test_install_scripts] ⛔ Found %d :-literal credential fallback(s) in install scripts", total
        )
        detail_lines: list[str] = []
        for fp, ln, var, val, suggest in sorted(all_findings):
            desc = "${" + var + ":-" + val + "}"
            line_str: str = f"  {fp}:{ln} → {desc} — use {suggest} instead"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_install_scripts] %s", line_str)

        pytest.fail(
            f"Found {total} credential variable(s) using :-literal fallback in install scripts.\n"
            f"Critical credential variables must use :? (fail-fast) instead of :-literal.\n"
            f"This ensures the script fails immediately if the environment variable is not set,\n"
            f"rather than silently using a hardcoded fallback value.\n"
            f"Replace: ${{{{VAR}}:-literal}} → ${{{{VAR}}:?{{{{VAR}}}} not set}}\n" + "\n".join(detail_lines)
        )

    logger.info(
        "[IMP:9][test_install_scripts] ✅ No :-literal credential fallbacks in %d install scripts", len(install_files)
    )
    # endregion BLOCK_Assert


# endregion FUNC_test_no_password_fallback_in_install_scripts


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 6: No hardcoded credentials in Python modules (W3 T3.4, DevPlan 160)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_hardcoded_credentials_in_python_files
## @purpose — Scan core/**/*.py for literal credential values (VAR = "literal" / "key": "literal").
##            Значение обязано быть кавычковым литералом — переменные/вызовы не считаются.
##            W3 T3.4: секреты утекали в Python-модули вне скоупа shell/workflow-сканеров.
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on hardcoded credentials)
## @complexity — O(F * L) где F = py-файлы, L = строки
## @invariants
##   - Сканируется ТОЛЬКО core/ (production) — tests/ fixture-данные с намеренными секретами
##     вне скоупа (тестовые fixture-данные не ломаются, W3 T3.4)
##   - *_generated.py исключаются (GENERATED, инвариант 11)
##   - pytest.fail с деталями file:line на детекцию


@pytest.mark.predeploy
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · .py сканер литеральных секретов (W3 T3.4)
# · Scenario: core/**/*.py содержит VAR = "literal" / "key": "literal" — секрет в Python-модуле
# · Last fail: N/A (preventive — расширение T3.4 на .py)
# · Remove if: py-сканер консолидируется в другой механизм детекции
def test_no_hardcoded_credentials_in_python_files(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/**/*.py → ⊕ _scan_python_for_literal_credentials → ◇ literal секрет?
    #   → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    core_dir: str = Path(platform_root) / "core"
    logger.info("[IMP:7][test_py_files] Scanning core Python modules: %s", core_dir)

    py_files: list[pathlib.Path] = _gather_files(core_dir, "*.py")
    # GENERATED-файлы (инвариант 11) исключаются
    py_files = [p for p in py_files if "generated" not in p.name.lower()]
    logger.info("[IMP:8][test_py_files] Discovered %d Python files (generated excluded)", len(py_files))
    # endregion BLOCK_Setup

    # region BLOCK_Scan
    all_findings: list[tuple[str, int, str, str]] = []

    for py_file in py_files:
        rel_path: str = os.path.relpath(str(py_file), platform_root)
        findings = _scan_python_for_literal_credentials(str(py_file))
        for line_no, varname, value in findings:
            logger.info("[IMP:8][test_py_files][literal] %s:%d %s=%s", rel_path, line_no, varname, _truncate(value))
            all_findings.append((rel_path, line_no, varname, _truncate(value)))
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_py_files] ⛔ Found %d hardcoded credential(s) in Python modules", total)
        detail_lines: list[str] = []
        for fp, ln, var, val in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → {var}={val}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_py_files] %s", line_str)

        pytest.fail(
            f"Found {total} hardcoded credential(s) in Python modules.\n"
            f"All credential values must use ${{VAR}} / os.environ / config references, "
            f"never literal values.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_py_files] ✅ No hardcoded credentials in %d Python modules", len(py_files))
    # endregion BLOCK_Assert


# endregion FUNC_test_no_hardcoded_credentials_in_python_files


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Test 7: No hardcoded credentials in YAML configs (W3 T3.4, DevPlan 160)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_no_hardcoded_credentials_in_yaml_files
## @purpose — Scan core/**/*.{yaml,yml} + root *.yaml/*.yml for literal credential values.
##            Исключаются SoT/GENERATED файлы с намеренными CI-test значениями
##            (platform-infra.yaml, platform-env.yaml, secrets-manifest.yaml, .env.example).
## @io — ⇥ caplog, platform_root → ⎋ None (pytest.fail on hardcoded credentials)
## @complexity — O(F * L) где F = YAML-файлы, L = строки
## @invariants
##   - _YAML_CRED_FILE_EXCLUDES + '*generated*' — вне скоупа (намеренные CI-значения)
##   - .github/workflows/*.yml — уже покрыты CI-сканером (test 2), не дублируются
##   - pytest.fail с деталями file:line на детекцию


@pytest.mark.predeploy
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · .yaml сканер литеральных секретов (W3 T3.4)
# · Scenario: YAML-конфиг содержит credential_key: literal — секрет вне shell/workflow-скоупа
# · Last fail: N/A (preventive — расширение T3.4 на .yaml)
# · Remove if: yaml-сканер консолидируется в другой механизм детекции
def test_no_hardcoded_credentials_in_yaml_files(
    caplog: pytest.LogCaptureFixture,
    platform_root: str,
) -> None:
    """
    # ▶ core/**/*.{yaml,yml} + root *.yaml/*.yml → ⊕ _scan_yaml_for_literal_credentials
    #   → ◇ literal секрет? → ⎋ fail with detail | pass
    """
    # region BLOCK_Setup
    logger.info("[IMP:7][test_yaml_files] Scanning YAML configs for literal credentials")

    yaml_files: list[pathlib.Path] = []
    core_dir: str = Path(platform_root) / "core"
    yaml_files.extend(sorted(pathlib.Path(core_dir).rglob("*.yaml")))
    yaml_files.extend(sorted(pathlib.Path(core_dir).rglob("*.yml")))
    # Root-level конфиги (docker-compose*.yml, ai-instructions.yaml) — НЕ .github/workflows
    yaml_files.extend(sorted(pathlib.Path(platform_root).glob("*.yaml")))
    yaml_files.extend(sorted(pathlib.Path(platform_root).glob("*.yml")))

    # Исключаем SoT/GENERATED (намеренные CI-test значения) и дубли
    filtered: list[pathlib.Path] = []
    seen: set[str] = set()
    for p in yaml_files:
        if p.name in _YAML_CRED_FILE_EXCLUDES or "generated" in p.name.lower():
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        filtered.append(p)
    logger.info("[IMP:8][test_yaml_files] Discovered %d YAML files (SoT/GENERATED excluded)", len(filtered))
    # endregion BLOCK_Setup

    # region BLOCK_Scan
    all_findings: list[tuple[str, int, str, str]] = []

    for yaml_file in filtered:
        rel_path: str = os.path.relpath(str(yaml_file), platform_root)
        findings = _scan_yaml_for_literal_credentials(str(yaml_file))
        for line_no, varname, value in findings:
            logger.info("[IMP:8][test_yaml_files][literal] %s:%d %s: %s", rel_path, line_no, varname, _truncate(value))
            all_findings.append((rel_path, line_no, varname, _truncate(value)))
    # endregion BLOCK_Scan

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error("[IMP:9][test_yaml_files] ⛔ Found %d hardcoded credential(s) in YAML configs", total)
        detail_lines: list[str] = []
        for fp, ln, var, val in sorted(all_findings):
            line_str: str = f"  {fp}:{ln} → {var}: {val}"
            detail_lines.append(line_str)
            logger.warning("[IMP:7][test_yaml_files] %s", line_str)

        pytest.fail(
            f"Found {total} hardcoded credential(s) in YAML configs.\n"
            f"All credential values must use ${{{{VAR}}}} / ${{{{ secrets.* }}}} / os.environ refs, "
            f"never literal values.\n" + "\n".join(detail_lines)
        )

    logger.info("[IMP:9][test_yaml_files] ✅ No hardcoded credentials in %d YAML configs", len(filtered))
    # endregion BLOCK_Assert


# endregion FUNC_test_no_hardcoded_credentials_in_yaml_files


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# R5 negative: .py/.yaml сканеры ловят исходный паттерн bug (W3 T3.4, U-T7 test2026)
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# region FUNC_test_negative_python_literal_credential_detected
## @purpose — R5 anti-survivorship: .py сканер обязан поймать PASSWORD = "test2026"
##            (исходный вход U-T7: test2026 хардкодился в 4 файлах).
## @io — ⇥ tmp_path → ⎋ None (assert finding)
## @complexity — O(1)


@pytest.mark.predeploy
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · py-сканер — PASSWORD = "test2026" (U-T7)
# · Last fail: test2026 хардкодился в 4 файлах (platform-test.yml, issue-cert.sh,
# ·   test_e2e_hermes_auth.py, gate-loop/SKILL.md) — U-T7
# · Remove if: py-сканер удаляется (детекция консолидирована в gitleaks)
def test_negative_python_literal_credential_detected(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """R5 negative: PASSWORD = "test2026" в .py → детектируется."""
    py_file = tmp_path / "leaky_module.py"
    py_file.write_text('PASSWORD = "test2026"\n', encoding="utf-8")

    findings = _scan_python_for_literal_credentials(str(py_file))
    logger.info("[IMP:8][test_py_files][negative] findings=%d", len(findings))
    assert findings, f"R5 FAIL: literal credential в .py не детектирован: {findings!r}"
    assert any("test2026" in v for _, _, v in findings), f"R5 FAIL: violation не про test2026: {findings!r}"
    logger.info('[IMP:9][test_py_files][negative] PASS: PASSWORD = "test2026" детектируется')


# endregion FUNC_test_negative_python_literal_credential_detected


# region FUNC_test_negative_yaml_literal_credential_detected
## @purpose — R5 anti-survivorship: .yaml сканер обязан поймать password: test2026.
## @io — ⇥ tmp_path → ⎋ None (assert finding)
## @complexity — O(1)


@pytest.mark.predeploy
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · yaml-сканер — password: test2026 (U-T7)
# · Last fail: test2026 хардкодился в 4 файлах (U-T7) — исходный вход для yaml-детекции
# · Remove if: yaml-сканер удаляется (детекция консолидирована в gitleaks)
def test_negative_yaml_literal_credential_detected(
    caplog: pytest.LogCaptureFixture,
    tmp_path: pathlib.Path,
) -> None:
    """R5 negative: password: test2026 в .yaml → детектируется."""
    yaml_file = tmp_path / "leaky_config.yaml"
    yaml_file.write_text("password: test2026\n", encoding="utf-8")

    findings = _scan_yaml_for_literal_credentials(str(yaml_file))
    logger.info("[IMP:8][test_yaml_files][negative] findings=%d", len(findings))
    assert findings, f"R5 FAIL: literal credential в .yaml не детектирован: {findings!r}"
    assert any("test2026" in v for _, _, v in findings), f"R5 FAIL: violation не про test2026: {findings!r}"
    logger.info("[IMP:9][test_yaml_files][negative] PASS: password: test2026 детектируется")


# endregion FUNC_test_negative_yaml_literal_credential_detected

# endregion TESTS
