# GREP_SUMMARY: secrets, scanner, secret-patterns, hardcoded-credentials, predeploy-gate
# STRUCTURE: ┌SECRET_PATTERNS (regex list)┐ → ┌scan_for_secrets(file) → [(line, match)]┐ → ┌scan_directory_for_secrets(dir) → {file: [(line, match)]}┐
# region MODULE_CONTRACT
## @purpose  Canonical secret scanner for detecting hardcoded credentials in any text file, extracted from conftest.py
## @scope    Shared across test_predeploy_gate.py and test_secrets_validation.py
## @invariants
##   - SECRET_PATTERNS match common credential patterns (password, token, api_key, secret, credential)
##   - scan_for_secrets reads a single file, returns list of (line, match) tuples
##   - scan_directory_for_secrets scans all files matching a glob pattern in a directory
## @rationale Eliminate duplicate secret scanning logic across test files
# endregion MODULE_CONTRACT

import pathlib
import re

# region SECRET_SCANNER
## @purpose — Canonical secret scanner for detecting hardcoded credentials in any text file.
## @scope — Shared across test_predeploy_gate.py and test_secrets_validation.py.
## @invariants
##   - SECRET_PATTERNS match common credential patterns (password, token, api_key, secret, credential)
##   - scan_for_secrets reads a single file, returns list of (line, match) tuples
##   - scan_directory_for_secrets scans all files matching a glob pattern in a directory
## @rationale — Eliminate duplicate secret scanning logic across test files.

SECRET_PATTERNS = [
    r"(?i)password\s*[=:]\s*['\"][^'\"]+['\"]",
    r"(?i)token\s*[=:]\s*['\"][^'\"]+['\"]",
    r"(?i)api_key\s*[=:]\s*['\"][^'\"]+['\"]",
    r"(?i)secret\s*[=:]\s*['\"][^'\"]+['\"]",
    r"(?i)credential[s]?\s*[=:]\s*['\"][^'\"]+['\"]",
]

# Known false-positive variable names that look like secrets but are legit references
EXCLUDED_PATTERNS = [
    r"NEXTAUTH_SECRET",
    # Add any other env-var names that look like secrets but are legit references
]


def scan_for_secrets(file_path, patterns=None):
    """Scan a file for potential hardcoded secrets.

    ## @io
    ## - input: file_path (str or Path), patterns (list of str regex or None)
    ## - output: list of (line_number, matched_text) tuples
    ## @complexity: O(n * p) where n = lines, p = patterns
    """
    if patterns is None:
        patterns = SECRET_PATTERNS

    findings = []
    file_path = pathlib.Path(file_path)
    if not file_path.exists():
        return findings

    with pathlib.Path(file_path).open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    matched_text = match.group().strip()[:80]
                    # Skip env var references like ${VAR} — not hardcoded secrets
                    if "${" in matched_text:
                        continue
                    # Skip known false-positive variable names (e.g. NEXTAUTH_SECRET)
                    if any(re.search(ep, line) for ep in EXCLUDED_PATTERNS):
                        continue
                    findings.append((i, matched_text))
                    break  # one finding per line

    return findings


def scan_directory_for_secrets(directory, patterns=None, glob_pattern="**/docker-compose*.yml"):
    """Scan all matching files in a directory for hardcoded secrets.

    ## @io
    ## - input: directory (str or Path), patterns (list or None), glob_pattern (str)
    ## - output: dict of {file_path: [(line, match), ...]}
    ## @complexity: O(f * n * p) where f = files
    """
    if patterns is None:
        patterns = SECRET_PATTERNS

    results = {}
    for file_path in sorted(pathlib.Path(directory).glob(glob_pattern)):
        findings = scan_for_secrets(str(file_path), patterns)
        if findings:
            results[str(file_path)] = findings

    return results


# endregion SECRET_SCANNER
