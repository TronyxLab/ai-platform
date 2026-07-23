# GREP_SUMMARY: gate http-retry-policy requests retry-loop handle-e2e-error prevention drift transient-failure
# STRUCTURE: ▶ scan tests/test_*.py → ◇ allowlist _handle_e2e_error → ◇ check requests.get/post lines → ◇ verify retry loop within 10 preceding lines → ∑ violations → ⎋ verdict
# region MODULE_CONTRACT
## @purpose — Gate test: verify that all `requests.get()` and `requests.post()` calls
##            in test files are protected by a retry loop (`for attempt in range(...)`).
##            Transient network/container startup failures cause non-deterministic CI
##            failures when HTTP calls lack retry logic. The gate enforces retry policy.
## @scope — Scans all tests/test_*.py files (excluding tests/gates/) for HTTP calls
##          without retry protection. Files that use `_handle_e2e_error()` (which
##          provides retry/timeout handling + error routing) are allowlisted.
## @invariants
##   - Detects: `requests.get(` or `requests.post(` outside a retry loop
##   - Allowlisted: files that import/use `_handle_e2e_error` (e2e error handler
##     with built-in retry-and-reraise pattern)
##   - Excluded: tests/gates/ files (gate tests are static scanners, not HTTP clients)
##   - Retry pattern: `for attempt in range(` (or `for i in range(`, `for retry in range(`)
##     within the preceding 10 lines of each HTTP call
##   - NOT flagged: files with no HTTP calls at all
##   - NOT flagged: HTTP calls inside `try: ... except` blocks are still flagged
##     if there's no retry loop — a bare `try/except` without retry (e.g. hard
##     `pytest.fail()`) does NOT provide transient-failure resilience
## @rationale — UF10 prevention gate (DevPlan 062 CI drift superposition). Multiple
##            UF findings (UF3-UF8) identified HTTP calls without retry causing
##            non-deterministic CI failures on transient errors (ConnectionResetError,
##            ReadTimeout, ConnectionError during container startup window).
##            This gate prevents recurrence: any new test with HTTP calls MUST
##            include retry logic or use `_handle_e2e_error`. Existing unprotected
##            calls found during initial scan (UF3-UF8 except UF3/UF5-UF8 which
##            already use _handle_e2e_error or retry — see below) remain as debt
##            tracked in the DevPlan unfixed delta.
## @changes
##   CREATED: 2026-07-23 | UF10 — Prevention gate for HTTP calls without retry
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Patterns
# ═══════════════════════════════════════════════════════════════════════════

# Pattern: HTTP call — matches requests.get( and requests.post(
_HTTP_CALL_RE: re.Pattern = re.compile(r"requests\.(?:get|post)\(")

# Pattern: retry loop — matches for attempt/_attempt/i/retry in range(
_RETRY_LOOP_RE: re.Pattern = re.compile(r"for\s+_?(?:attempt|i|retry|_attempt)\s+in\s+range\(")

# ═══════════════════════════════════════════════════════════════════════════
# Scan logic
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_is_inside_retry_context
def _is_inside_retry_context(lines: list[str], line_num: int) -> bool:
    """Check if an HTTP call at `line_num` (1-indexed) is inside a retry loop.

    Examines up to 10 lines before the HTTP call for a `for attempt in range(`
    or equivalent retry pattern.

    ## @purpose — Determine if a `requests.get()` or `requests.post()` call
    ##            is protected by a retry loop by scanning preceding lines.
    ##            This is a line-level heuristic that checks the last 10 lines
    ##            for a `for ... in range(` pattern. It does not track
    ##            indentation levels — the 10-line window is sufficient for
    ##            all observed retry patterns in the test suite.
    ## @io — ⇥ lines: list[str] (file content), line_num: int (1-indexed line)
    ##       ⎋ bool: True if preceding lines contain a retry pattern
    ## @complexity — O(W) where W = window size (10 lines)
    """
    start: int = max(0, line_num - 11)  # line_num is 1-indexed, so preceding ends at line_num-1
    preceding: list[str] = lines[start : line_num - 1]

    for prev_line in preceding:
        stripped: str = prev_line.strip()
        # Skip comment and docstring lines
        if stripped.startswith(("#", '"""', "'''")):
            continue
        if _RETRY_LOOP_RE.search(stripped):
            return True

    return False


# endregion FUNC_is_inside_retry_context


# region FUNC_scan_for_unprotected_requests
def _scan_for_unprotected_requests() -> list[tuple[str, int, str]]:
    """Scan all tests/test_*.py for HTTP calls without retry protection.

    ## @purpose — Find `requests.get()` and `requests.post()` calls that are
    ##            NOT wrapped in a retry loop and NOT in files that use the
    ##            `_handle_e2e_error` handler (allowlisted). The scan excludes
    ##            gate test files (tests/gates/) as they are static scanners.
    ## @io — ⎋ list[(file_path, line_number, raw_line_text)]
    ## @complexity — O(F * L) where F = test files scanned, L = lines per file
    ## @invariants
    ##   - Gate files (tests/gates/) are excluded
    ##   - Files using `_handle_e2e_error` are allowlisted entirely
    ##   - Each HTTP call is checked via _is_inside_retry_context()
    ##   - All findings are logged at IMP:7 for traceability
    """
    findings: list[tuple[str, int, str]] = []
    test_dir: pathlib.Path = repo_root() / "tests"

    for py_file in sorted(test_dir.glob("test_*.py")):
        # Skip gate test files — they are static scanners, not HTTP clients
        if py_file.parent.name == "gates":
            logger.info("[IMP:8][scan][skip-gate] %s (in gates/)", py_file.name)
            continue

        rel_path: str = str(py_file.relative_to(repo_root()))

        try:
            content: str = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("[IMP:7][scan][error] Cannot read %s: %s", rel_path, exc)
            continue

        lines: list[str] = content.split("\n")

        # ═══════════════════════════════════════════════════════
        # Phase 1: Allowlist check — files using _handle_e2e_error
        # ═══════════════════════════════════════════════════════
        # _handle_e2e_error provides retry/timeout handling with proper
        # error routing (pytest.fail for ConnectionError, pytest.skip for
        # Timeout). Files using it are considered as having retry handling.
        if "_handle_e2e_error" in content:
            logger.info("[IMP:8][scan][allowlisted] %s uses _handle_e2e_error, skipping", rel_path)
            continue

        # ═══════════════════════════════════════════════════════
        # Phase 2: Find HTTP call lines
        # ═══════════════════════════════════════════════════════
        http_lines: list[int] = []
        for i, line in enumerate(lines, 1):
            stripped: str = line.strip()
            # Skip comments and docstrings
            if stripped.startswith(("#", '"""', "'''")):
                continue
            if _HTTP_CALL_RE.search(stripped):
                http_lines.append(i)

        if not http_lines:
            logger.info("[IMP:8][scan][clean] %s: no HTTP calls", rel_path)
            continue

        # ═══════════════════════════════════════════════════════
        # Phase 3: Check each HTTP call for retry context
        # ═══════════════════════════════════════════════════════
        logger.info("[IMP:8][scan][check] %s: %d HTTP call(s) to verify", rel_path, len(http_lines))

        for line_num in http_lines:
            if _is_inside_retry_context(lines, line_num):
                logger.info(
                    "[IMP:8][scan][retry-ok] %s:%d — HTTP call has retry protection",
                    rel_path,
                    line_num,
                )
                continue

            raw_line: str = lines[line_num - 1].strip()
            findings.append((rel_path, line_num, raw_line))
            logger.warning(
                "[IMP:7][scan][no-retry] %s:%d — `%s` without retry loop",
                rel_path,
                line_num,
                raw_line[:80],
            )

    return findings


# endregion FUNC_scan_for_unprotected_requests


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_test_http_calls_have_retry
# 🧪 TRAP[TEST] · 2026-07-23 · Regression: any tests/test_*.py contains
# · requests.get() or requests.post() without retry loop or _handle_e2e_error
# · Scenario: Gate — scans all test files (excl. gates/) for unprotected HTTP calls
# · Last fail: never
# · Remove if: all HTTP calls in test files have retry protection (either
# ·            _handle_e2e_error or explicit for attempt in range())
#
# ⚠️ TRAP[BUG] · 2026-07-23 · P1 · UF3-UF8 root cause: test files with
# · requests.get/post without retry fail non-deterministically on transient
# · network/container startup errors
# · Symptom: CI tests fail intermittently with ConnectionResetError(104),
# ·           ReadTimeout, or ConnectionError when containers are restarting
# ·           or network is briefly unstable
# · Root: HTTP calls without retry logic — a single transient error kills
# ·        the entire test, requiring a CI retry. 5+ test files affected (UF3-UF8).
# ·        This is a systemic pattern, not an individual bug.
# · Fix: Each HTTP call MUST be wrapped in `for attempt in range(N)` with
# ·       exponential backoff, or the file must use `_handle_e2e_error` which
# ·       provides automatic retry/timeout handling with error routing.
# · Prevention: This gate test catches any new test file with unprotected
# ·             HTTP calls. Existing flagged files remain as debt tracked
# ·             in the DevPlan 062 unfixed delta (Wave 2: UF5-UF8).
@pytest.mark.gate
def test_http_calls_have_retry(caplog) -> None:
    """Verify all test files use retry protection for HTTP calls.

    ## @purpose — Prevent non-deterministic CI failures from transient
    ##            network/container errors by enforcing retry policy.
    ##            Every `requests.get()` or `requests.post()` call in test
    ##            files MUST be protected by a retry loop (`for attempt in range(...)`)
    ##            OR the file must use `_handle_e2e_error` (which provides
    ##            built-in retry/timeout handling).
    ## @rationale — UF10 from DevPlan 062: 6 unfixed findings (UF3-UF8) identify
    ##            HTTP calls without retry as a systemic CI stability risk.
    ##            Without a prevention gate, new test files will repeat the same
    ##            pattern, perpetuating non-deterministic failures.
    ##            This gate complements G1 (hardcoded paths) and G2 (checkout order)
    ##            as the third prevention gate in the CI drift remediation suite.
    ## @io — ⎋ None (assert side-effect)
    ## @complexity — O(F * L) deferred to _scan_for_unprotected_requests()
    """
    caplog.set_level(logging.INFO)
    findings: list[tuple[str, int, str]] = _scan_for_unprotected_requests()

    if findings:
        detail_lines: list[str] = [f"  {fp}:{ln} — {line_text[:100]}" for fp, ln, line_text in sorted(findings)]
        logger.error(
            "[IMP:9][gate][http-retry] ⛔ Found %d HTTP call(s) without retry protection",
            len(findings),
        )
        pytest.fail(
            f"Found {len(findings)} HTTP call(s) without retry protection in test files.\n"
            f"Each `requests.get()` or `requests.post()` call MUST be wrapped in:\n"
            f"  for attempt in range(N):\n"
            f"      try:\n"
            f"          r = requests.get(...)\n"
            f"          break\n"
            f"      except requests.RequestException:\n"
            f"          if attempt < N-1: time.sleep(2**attempt); continue\n"
            f"          else: raise\n"
            f"\n"
            f"OR the file should import and use `_handle_e2e_error()` which provides\n"
            f"automatic retry/timeout handling with proper error routing.\n"
            f"\n"
            f"See DevPlan 062 UF10 and test_smoke_langfuse.py (lines 97-121) for the\n"
            f"canonical retry pattern with _handle_e2e_error fallback.\n"
            f"\n" + "\n".join(detail_lines),
        )

    logger.info(
        "[IMP:9][gate][http-retry] ✅ All HTTP calls in test files have retry protection — no transient-failure risk"
    )


# endregion FUNC_test_http_calls_have_retry
