# GREP_SUMMARY: checklist anti-loop errors escalation external-help reflection common-errors
# STRUCTURE: ┌_CHECKLIST┐ → _print_checklist ◇ _print_external_help ◇ _print_reflection ◇ _print_escalation → ⎋ stderr output
# region MODULE_CONTRACT
## @purpose  Anti-Loop Protocol CHECKLIST — common error checklist items and escalation helpers for pytest sessions
## @scope    CHECKLIST data + 4 print helpers. Consumed by conftest.py anti-loop counter escalation logic.
## @invariants
##   - All items in _CHECKLIST are plain strings (one TRAP[DECISION] comment appended)
##   - All print helpers write to sys.stderr
##   - No pytest imports needed — standalone module
## @rationale Extract CHECKLIST from conftest.py to reduce file size and keep escalation logic modular.
##            Preserves all original content verbatim for compatibility.
# endregion MODULE_CONTRACT

import logging

logger = logging.getLogger(__name__)

# region CHECKLIST

_CHECKLIST = [
    "tmp_path not used — hardcoded paths in tests",
    "caplog level not set — IMP:7-10 logs not captured",
    "File not found — expected template or config file missing",
    "Semantic markup absent (GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT)",
    "Dockerfile FROM placeholder not replaced (__BASE_IMAGE__)",
    "Secret patterns (sk-*, Bearer, token) leaked in .env.example values",
    "restart: always used instead of restart: unless-stopped",
    "approvals.mode set to off instead of manual",
    "TELEGRAM_ALLOWED_USERS missing where TELEGRAM_BOT_TOKEN is set",
    "HERMES_DASHBOARD_INSECURE set to 1 (forbidden)",
    "Healthcheck missing in docker-compose.yml",
    "Trace assertion missing or IMP:9 not found in logs",
    "Dockerfile build-arg (ARG BASE_IMAGE) not declared for compose compatibility",
    "pytest.skip used to mask failure instead of environment absence — skip ONLY for no Docker, no env vars, no network; everything else → pytest.fail with diagnostic output",
    "_handle_e2e_error not used — ALL E2E tests MUST use _handle_e2e_error for HTTP error handling (ConnectionError, SSLError, ProxyError, Timeout)",
    # 🧐 TRAP[DECISION] · 2026-07-07 · — · AUTOMATIC_SKIP_GATE — skip only for env absence
    # · Rejected: Allow pytest.skip for compose failures, timeout errors, container-not-running
    # · Reason: Skip masks real bugs. If Docker is available but container fails, that’s a bug
    # ·   (compose config, env wiring, healthcheck), not an env issue. Rule: skip ONLY for
    # ·   Docker not installed, required env vars missing, no network. Everything else → fail.
    # · Rev: If CI introduces runtime environments where containers legitimately cannot start,
    # ·       revisit but add explicit env-marker check (e.g., INTEGRATION_MODE=offline).
]


def _print_checklist() -> None:
    """Print common error checklist for attempts 1-2."""
    logger.info("\n=== ATTEMPT CHECKLIST (common errors) ===")
    for i, item in enumerate(_CHECKLIST, 1):
        logger.info("%s", f"  {i}. {item}")
    logger.info("=== END CHECKLIST ===")


def _print_external_help() -> None:
    """Print external help suggestion for attempt 3."""
    logger.info("\nUse MCP tavily or Context 7 to find a solution online.")


def _print_reflection() -> None:
    """Print looping risk warning for attempt 4."""
    logger.info(
        "\nWARNING: Looping risk! Pause and reflect. Are you repeating a failed strategy? Consider alternatives (Superposition)."
    )


def _print_escalation() -> None:
    """Print critical escalation for attempt 5+."""
    logger.info("\nCRITICAL ERROR: Agent looping detected. STOP. Formulate a help request for an operator.")


# endregion CHECKLIST
