# GREP_SUMMARY: hardcoded-credentials security-scan password-detection shell-scripts ci-workflows install-scripts lint deployment-audit literal-secrets
# STRUCTURE: ▶ test_no_hardcoded_password_in_shell_scripts ∋ core/**/*.sh → ⊕ literal_assignments + password_args + literal_fallbacks → ◇ findings? → ⎋ fail|pass → ▶ test_no_hardcoded_credentials_in_ci_workflows ∋ .github/workflows/*.yml → ⊕ literal_credential_values → ◇ literal secrets? → ⎋ fail|pass → ▶ test_no_password_fallback_in_install_scripts ∋ core/**/install*.sh → ⊕ credential_fallbacks_using_:-literal → ◇ has_literal_fallback? → ⎋ fail|pass → ▶ test_no_token_in_git_url ∋ core/**/*.sh → ⊕ _scan_for_token_in_git_url → ◇ token-in-url? → ⎋ fail|pass → ▶ test_no_token_in_git_url_workflows ∋ .github/**/*.yml → ⊕ _scan_for_token_in_git_url → ◇ token-in-url? → ⎋ fail|pass
# @file test_no_hardcoded_credentials.py
# @purpose  Security regression tests that detect hardcoded passwords, tokens, API keys,
#           and secrets in shell scripts, CI workflow files, and install scripts across
#           the ai-platform codebase. All credential values must use $VAR, ${VAR}, or
#           ${{ secrets.* }} references — never literal values.
# @scope    Three independent scanners:
#           1. test_no_hardcoded_password_in_shell_scripts — scans core/**/*.sh for
#              patterns: PASSWORD=literal, --password literal, token=literal,
#              api_key=literal, secret=literal, ${VAR:-literal} credential fallbacks
#           2. test_no_hardcoded_credentials_in_ci_workflows — scans .github/workflows/*.yml
#              for literal credential values (not ${{ secrets.* }})
#           3. test_no_password_fallback_in_install_scripts — checks install scripts use
#              :? (fail-fast) instead of :-literal for critical credential variables
# @invariants
#   - All tests use @ldd_trajectory decorator from conftest
#   - All tests are marked @pytest.mark.predeploy
#   - test_no_hardcoded_password_in_shell_scripts scans core/**/*.sh recursively
#   - test_no_hardcoded_credentials_in_ci_workflows scans .github/workflows/*.yml
#   - test_no_password_fallback_in_install_scripts scans install*.sh (glob pattern)
#   - Credential variable patterns: PASSWORD, TOKEN, API_KEY, SECRET (case-insensitive)
#   - Lines with ${VAR}, $(cmd), ${{ secrets.* }} refs are excluded (not literals)
#   - Uses platform_root fixture from conftest (project root resolution)
#   - No subprocess calls — pure file I/O and regex parsing
# @rationale  AC-T7 from DevPlan §TASK-7 — security regression test ensuring no hardcoded
#             credentials enter the codebase. Complements conftest scan_directory_for_secrets
#             (which only scans docker-compose*.yml) with broader coverage. Detected P0 bug:
#             test2026 was hardcoded in 4 files before TASK-2 fix.
# @changes    CREATED: 2026-07-09 | Wave 1: TASK-7 security credential scanner
# @see        conftest.py :: SECRET_PATTERNS, scan_for_secrets, scan_directory_for_secrets
# @see        DevPlan §TASK-7, §TEST_SPEC
#
# region MODULE_CONTRACT
## @purpose  3 pre-deploy security regression tests validating no hardcoded credentials
##           exist in shell scripts, CI workflow YAML, or install scripts.
## @scope    Static file scanning of core/**/*.sh, .github/workflows/*.yml,
##           and core/**/install*.sh. Pure regex-based, no subprocess calls.
## @invariants
##   - Shell literal credential detection (PASSWORD=test2026, --password test2026)
##   - CI literal secret detection (key: value not ${{ secrets.* }})
##   - Install script :-literal detection for credential vars (should use :?)
##   - All three tests are @pytest.mark.predeploy for CI parallelisation
##   - None require Docker or any running service
##   - Uses conftest platform_root fixture for path resolution
## @rationale — AC-T7 from DevPlan: two QA audits independently flagged hardcoded test2026
##              password in 4 files (platform-test.yml, issue-cert.sh, test_e2e_hermes_auth.py,
##              gate-loop/SKILL.md). This test prevents regression.
## @changes   CREATED: 2026-07-09 | Wave 1: TASK-7 security credential scanner
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re

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
        with open(filepath) as f:
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
    _in_python_block: bool = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Track python -c inline blocks to skip Python code
        if re.search(r'(?:python|python3)\s+-c\s+"[^"]*$', stripped) or _in_python_block:
            if stripped.endswith('")'):
                _in_python_block = False
            elif not _in_python_block:
                _in_python_block = True
            # Skip lines inside Python inline code — they are not shell assignments
            if _in_python_block or stripped.endswith('")'):
                continue
            _in_python_block = bool(re.search(r'(?:python|python3)\s+-c\s+"[^"]*$', stripped))

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
    # endregion
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
        with open(filepath) as f:
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
    # endregion
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
        with open(filepath) as f:
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
    # endregion
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
        with open(filepath) as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    _EMPTY_PLACEHOLDERS: set[str] = {'""', "''", ""}

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

        if value in _EMPTY_PLACEHOLDERS:
            continue

        findings.append((i, m.group("key"), value))
    # endregion
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
        with open(filepath) as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError):
        return findings

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        findings.extend((i, m.group()) for m in pattern.finditer(stripped))
    # endregion
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
    # endregion


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
    core_dir: str = os.path.join(platform_root, "core")
    logger.info("[IMP:7][test_git_url_token] Scanning for token in git URL: %s", core_dir)

    all_findings: list[tuple[str, int, str]] = []
    # endregion

    # region BLOCK_ScanEntrypoints
    entrypoints_dir: str = os.path.join(core_dir, "entrypoints")
    findings = _scan_directory_for_token_in_git_url(entrypoints_dir, "*.sh", platform_root, logger)
    all_findings.extend(findings)
    # endregion

    # region BLOCK_ScanInternal
    internal_dir: str = os.path.join(core_dir, "internal")
    findings = _scan_directory_for_token_in_git_url(internal_dir, "*.sh", platform_root, logger)
    all_findings.extend(findings)
    # endregion

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
    # endregion


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
    github_dir: str = os.path.join(platform_root, ".github")
    logger.info("[IMP:7][test_git_url_workflows] Scanning for token in git URL: %s", github_dir)

    all_findings: list[tuple[str, int, str]] = []
    # endregion

    # region BLOCK_ScanWorkflows
    workflows_dir: str = os.path.join(github_dir, "workflows")
    if pathlib.Path(workflows_dir).exists():
        findings = _scan_directory_for_token_in_git_url(workflows_dir, "*.yml", platform_root, logger)
        all_findings.extend(findings)
        logger.info(
            "[IMP:8][test_git_url_workflows] Scanned workflows/: %d files checked",
            len(list(pathlib.Path(workflows_dir).rglob("*.yml"))),
        )
    else:
        logger.info("[IMP:4][test_git_url_workflows] workflows/ directory not found — skipping")
    # endregion

    # region BLOCK_ScanActions
    actions_dir: str = os.path.join(github_dir, "actions")
    if pathlib.Path(actions_dir).exists():
        findings = _scan_directory_for_token_in_git_url(actions_dir, "*.yml", platform_root, logger)
        all_findings.extend(findings)
        logger.info(
            "[IMP:8][test_git_url_workflows] Scanned actions/: %d files checked",
            len(list(pathlib.Path(actions_dir).rglob("*.yml"))),
        )
    else:
        logger.info("[IMP:4][test_git_url_workflows] actions/ directory not found — skipping")
    # endregion

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
    # endregion


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
    core_dir: str = os.path.join(platform_root, "core")
    logger.info("[IMP:7][test_shell_scripts] Scanning core shell scripts: %s", core_dir)

    shell_files: list[pathlib.Path] = _gather_files(core_dir, "*.sh")
    logger.info("[IMP:8][test_shell_scripts] Discovered %d shell scripts", len(shell_files))
    # endregion

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
    # endregion

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
    # endregion


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
    workflows_dir: str = os.path.join(platform_root, ".github", "workflows")
    logger.info("[IMP:7][test_ci_workflows] Scanning CI workflows: %s", workflows_dir)

    ci_files: list[pathlib.Path] = sorted(pathlib.Path(workflows_dir).glob("*.yml"))
    if not ci_files:
        logger.warning("[IMP:4][test_ci_workflows] No .yml files found in %s — skipping scan", workflows_dir)
        pytest.skip(f"No CI workflow files found in {workflows_dir}")
        return  # unreachable but satisfies type checker

    logger.info("[IMP:8][test_ci_workflows] Discovered %d CI workflow files", len(ci_files))
    # endregion

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
    # endregion

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
    # endregion


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
    core_dir: str = os.path.join(platform_root, "core")
    logger.info(
        "[IMP:7][test_install_scripts] Scanning install scripts for :-literal credential fallbacks: %s", core_dir
    )

    install_files: list[pathlib.Path] = _gather_files(core_dir, "install*.sh")
    if not install_files:
        logger.warning("[IMP:4][test_install_scripts] No install*.sh files found in %s — skipping scan", core_dir)
        pytest.skip(f"No install scripts found in {core_dir}")
        return

    logger.info("[IMP:8][test_install_scripts] Discovered %d install scripts", len(install_files))
    # endregion

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
    # endregion

    # region BLOCK_Assert
    total: int = len(all_findings)
    if total > 0:
        logger.error(
            "[IMP:9][test_install_scripts] ⛔ Found %d :-literal credential fallback(s) in install scripts", total
        )
        detail_lines: list[str] = []
        for fp, ln, var, val, suggest in sorted(all_findings):
            _desc = "${" + var + ":-" + val + "}"
            line_str: str = f"  {fp}:{ln} → {_desc} — use {suggest} instead"
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
    # endregion


# endregion FUNC_test_no_password_fallback_in_install_scripts

# endregion TESTS
