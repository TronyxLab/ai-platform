#!/usr/bin/env python3
# GREP_SUMMARY: gate secrets-parser-import secrets_env_parser import-registry anti-drift DevPlan-086
# STRUCTURE: ▶ ┌_CONSUMERS table┐ → ○ for each: ◇ grep for import pattern → ◇ import present? → ⟦fail with missing imports⟧ → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate test: verify ALL 6 direct consumers import from the canonical shared module
##           core.internal.shared.secrets_env_parser. Ensures DevPlan 086 unification
##           is complete — no legacy inline parsing remains in any consumer.
##           NOTE: docker_auth.py reads creds from env vars (indirect consumption via
##           node-lifecycle.sh sourcing secrets.env) — not a direct import consumer.
## @scope    Scans all 6 known direct import consumers (Python + shell) for the canonical
##           import pattern:
##           - Python: "from core.internal.shared.secrets_env_parser import"
##           - Shell (node-lifecycle.sh): "from core.internal.shared.secrets_env_parser import export_shell"
## @invariants
##   - ALL 6 direct import consumers MUST import from secrets_env_parser
##   - Missing import → FAIL with clear message
##   - Consumers: secrets_manager.py, secrets_validator.py, compose_preflight.py,
##     agent_watchdog.py, cert_orchestrator.py, node-lifecycle.sh
##   - docker_auth.py excluded (indirect env var consumer — reads from sourced env)
##   - node-lifecycle.sh uses inline python3 -c with import, checked via grep at shell level
## @rationale DevPlan 086 unified 7 inline parsers into one shared module.
##            This gate prevents any consumer from drifting back to inline parsing.
##            docker_auth.py is excluded because it reads credentials via os.environ
##            (sourced from secrets.env by node-lifecycle.sh), not via direct import.
##            If a new direct consumer is added, it must be added to this test's consumer list.
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.conftest import ldd_trajectory

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

# ── Consumer registry: all files that MUST import from secrets_env_parser ──

# (relative path, description, Python/shell, expected import regex)
_CONSUMERS: list[dict[str, str]] = [
    {
        "file": "core/internal/bootstrap/lifecycle/secrets_manager.py",
        "description": "Secrets manager — delegates source_secrets_env to shared parser",
        "type": "python",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import",
    },
    {
        "file": "core/internal/bootstrap/deploy/secrets_validator.py",
        "description": "Secrets validator — validates secrets.env content via shared parser",
        "type": "python",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import",
    },
    {
        "file": "core/internal/bootstrap/deploy/compose_preflight.py",
        "description": "Compose preflight — loads secrets.env for compose up-safe check",
        "type": "python",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import",
    },
    {
        "file": "core/modules/hermes-agent/watchdog/agent_watchdog.py",
        "description": "Agent watchdog — loads tokens via shared parser (DevPlan T8)",
        "type": "python",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import",
    },
    {
        "file": "core/internal/bootstrap/cert_orchestrator.py",
        "description": "Cert orchestrator — loads WEBNAMES_API_KEY via shared parser",
        "type": "python",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import",
    },
    {
        "file": "core/internal/bootstrap/node-lifecycle.sh",
        "description": "Node lifecycle shell facade — calls export_shell via python3 -c",
        "type": "shell",
        "pattern": r"from\s+core\.internal\.shared\.secrets_env_parser\s+import\s+export_shell",
    },
]


# region FUNC_test_all_consumers_import_secrets_env_parser

## @purpose — Verify every consumer listed in _CONSUMERS imports from the shared
##            secrets_env_parser module via the expected import pattern.

# 🧪 TRAP[TEST] · 2026-07-30 · gate/secrets-parser-import · REGRESSION(086)
# · SCENARIO(grep for canonical import pattern in all 6 direct consumer files)
# · LAST_FAIL(N/A — new gate)
# · REMOVE_IF(consumer list changes or import pattern changes)


@pytest.mark.gate
@ldd_trajectory
def test_all_consumers_import_secrets_env_parser(caplog) -> None:
    """Verify ALL 6 direct import consumers import from core.internal.shared.secrets_env_parser.

## @purpose — DevPlan 086 regression gate: checks that every known consumer
##            of secrets.env parsing functionality imports from the canonical
##            shared module. Missing import = regression to inline parsing.
##            NOTE: docker_auth.py excluded (indirect env var consumer).
## @io — ⎋ None (assert side-effect via pytest.fail on missing imports)
## @complexity — O(C * F) where C = 6 consumers, F = file read size per consumer
"""
    logger.info(
        "[IMP:8][test_all_consumers_import_secrets_env_parser] "
        "Checking %d direct import consumers for canonical import from secrets_env_parser",
        len(_CONSUMERS),
    )

    missing_imports: list[dict[str, str]] = []

    for consumer in _CONSUMERS:
        filepath = _PROJECT_ROOT / consumer["file"]
        description = consumer["description"]
        pattern_str = consumer["pattern"]

        logger.debug(
            "[IMP:5][test_all_consumers_import_secrets_env_parser] "
            "Checking %s (%s)",
            consumer["file"],
            description,
        )

        if not filepath.is_file():
            missing_imports.append({
                "file": consumer["file"],
                "description": description,
                "issue": "FILE NOT FOUND",
            })
            logger.warning(
                "[IMP:7][test_all_consumers_import_secrets_env_parser] "
                "FILE NOT FOUND: %s",
                consumer["file"],
            )
            continue

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as e:
            missing_imports.append({
                "file": consumer["file"],
                "description": description,
                "issue": f"CANNOT READ: {e}",
            })
            continue

        if not re.search(pattern_str, content):
            missing_imports.append({
                "file": consumer["file"],
                "description": description,
                "issue": f"Missing import: '{pattern_str}'",
            })
            logger.warning(
                "[IMP:7][test_all_consumers_import_secrets_env_parser] "
                "MISSING IMPORT in %s: %s",
                consumer["file"],
                pattern_str,
            )
        else:
            logger.info(
                "[IMP:9][test_all_consumers_import_secrets_env_parser] "
                "OK: %s — canonical import found",
                consumer["file"],
            )

    if missing_imports:
        logger.error(
            "[IMP:9][test_all_consumers_import_secrets_env_parser] "
            "FAIL: %d consumer(s) missing canonical import",
            len(missing_imports),
        )
        failure_msg_lines: list[str] = [
            f"[IMP:10] FAIL: {len(missing_imports)} consumer(s) missing import "
            f"from core.internal.shared.secrets_env_parser:"
        ]
        for m in missing_imports:
            failure_msg_lines.append(f"\n  {m['file']} ({m['description']})")
            failure_msg_lines.append(f"    Issue: {m['issue']}")

        failure_msg = "\n".join(failure_msg_lines)
        print(failure_msg)

        pytest.fail(
            f"{len(missing_imports)} consumer(s) missing canonical import "
            f"from core.internal.shared.secrets_env_parser:\n"
            + "\n".join(
                f"  {m['file']} — {m['issue']}"
                for m in missing_imports
            )
            + "\n\nAll consumers MUST import via:\n"
            "  from core.internal.shared.secrets_env_parser import parse\n"
            "  from core.internal.shared.secrets_env_parser import export_shell  # shell facades\n"
        )

    logger.info(
        "[IMP:9][test_all_consumers_import_secrets_env_parser] "
        "PASS — all %d consumers import from canonical module",
        len(_CONSUMERS),
    )


# endregion FUNC_test_all_consumers_import_secrets_env_parser
