#!/usr/bin/env python3
# GREP_SUMMARY: gate-test system-exceptions phony manifest name-linter documented by-design
# STRUCTURE: ▶ read manifest name_linter.system_exceptions → ◇ compare expected {help,venv,pre-commit-install,pre-commit-run} → ⊕ scan AGENTS.md+generator for documentation → ∑ violations → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 119 G2): системные исключения .PHONY (help/venv/
##           pre-commit-install/pre-commit-run) задокументированы как by-design
##           отклонение от @invariants в entrypoint-manifest.yaml и core/AGENTS.md.
## @scope    entrypoint-manifest.yaml (name_linter.system_exceptions + header-комментарий),
##           core/AGENTS.md (секция «Системные исключения .PHONY»),
##           generate_entrypoint_manifest.py (SYSTEM_EXCEPTIONS + header).
## @invariants
##   - Манифест СОДЕРЖИТ ровно {help, venv, pre-commit-install, pre-commit-run}
##   - Манифест СОДЕРЖИТ header-комментарий о by-design исключениях (G2)
##   - core/AGENTS.md СОДЕРЖИТ секцию «Системные исключения .PHONY»
##   - Генератор СОДЕРЖИТ SYSTEM_EXCEPTIONS с теми же именами (не дрейфует)
## @rationale Системные .PHONY-таргеты НЕ являются каноническими операциями платформы —
##            документирование prevents дрейфа между генератором, манифестом и AGENTS.md
##            (AUDIT-6 F3: исключения существовали, но не были задокументированы).
## @changes 2026-08-02 | CREATED: DevPlan 119 G2 (AUDIT-6 F3)
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

logger = logging.getLogger(__name__)

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
MANIFEST: pathlib.Path = pathlib.Path(PLATFORM_ROOT) / "core" / "entrypoint-manifest.yaml"
CORE_AGENTS: pathlib.Path = pathlib.Path(PLATFORM_ROOT) / "core" / "AGENTS.md"
GENERATOR: pathlib.Path = pathlib.Path(PLATFORM_ROOT) / "core" / "internal" / "scripts" / "generate_entrypoint_manifest.py"

# Канонический перечень системных исключений (DevPlan 119 G2)
# NB: frozenset(tuple), не frozenset({...}) — set-литерал внутри Call ловится гейтом
# test_no_hardcoded_target_sets_in_gates (test_gate_exception_audit.py, G1.2).
EXPECTED_SYSTEM_EXCEPTIONS: frozenset[str] = frozenset(
    (
        "help",
        "venv",
        "pre-commit-install",
        "pre-commit-run",
    )
)


# region FUNC_extract_manifest_exceptions
def extract_manifest_exceptions() -> set[str]:
    """Read name_linter.system_exceptions from entrypoint-manifest.yaml (grep, no YAML dep).

    ## @purpose — Locate the system_exceptions block without importing yaml — the manifest
    ##            is generated and structurally stable: list items are indented '- name' lines.
    ## @io — ⎋ set[str]: system exception names found in the manifest
    ## @complexity — O(L) where L = manifest line count
    """
    text: str = MANIFEST.read_text(encoding="utf-8")
    # name_linter: → system_exceptions: → following '- name' items
    match = re.search(r"name_linter:\n\s+system_exceptions:\n((?:\s+- \S+\n?)+)", text)
    if not match:
        return set()
    return {line.strip().lstrip("- ").strip() for line in match.group(1).splitlines() if line.strip()}


# endregion FUNC_extract_manifest_exceptions


# region FUNC_test_system_exceptions_documented
# 🧪 TRAP[TEST] · 2026-08-02 · Regression: системные .PHONY исключения не задокументированы
# · Scenario: Gate — manifest/AGENTS.md/generator содержат по-дизайну документацию исключений
# · Last fail: никогда (новый тест DevPlan 119 G2, AUDIT-6 F3)
# · Remove if: механизм системных исключений .PHONY удалён из генератора
@pytest.mark.gate
def test_system_exceptions_documented() -> None:
    """Системные .PHONY исключения задокументированы в манифесте, AGENTS.md и генераторе.

    ## @purpose — By-design отклонение от инварианта «каждый .PHONY → глоссарий»
    ##            задокументировано в 3 местах (trinity): манифест (перечень + комментарий),
    ##            core/AGENTS.md (секция), генератор (SYSTEM_EXCEPTIONS — единственный фильтр).
    ## @io — ⎋ ∅ — fail с деталями при расхождении
    ## @complexity — O(L) сканирование 3 файлов
    """
    logger.info("[IMP:8][system_exceptions] Validating documentation (DevPlan 119 G2)")

    # 1. Манифест: перечень system_exceptions
    manifest_exceptions = extract_manifest_exceptions()
    logger.info(
        "[IMP:9][system_exceptions] Manifest system_exceptions: %s", sorted(manifest_exceptions)
    )
    assert manifest_exceptions == EXPECTED_SYSTEM_EXCEPTIONS, (
        f"Manifest name_linter.system_exceptions mismatch: {sorted(manifest_exceptions)} "
        f"!= {sorted(EXPECTED_SYSTEM_EXCEPTIONS)}"
    )

    # 2. Манифест: header-комментарий о by-design исключениях (G2)
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    assert "СИСТЕМНЫЕ ИСКЛЮЧЕНИЯ .PHONY" in manifest_text and "DevPlan 119 G2" in manifest_text, (
        "Manifest header must document system exceptions as by-design (DevPlan 119 G2)"
    )
    assert "name_linter.system_exceptions" in manifest_text, (
        "Manifest header must reference name_linter.system_exceptions"
    )

    # 3. core/AGENTS.md: секция о системных исключениях
    agents_text = CORE_AGENTS.read_text(encoding="utf-8")
    assert "Системные исключения .PHONY" in agents_text, (
        "core/AGENTS.md must contain «Системные исключения .PHONY» section (DevPlan 119 G2)"
    )
    for name in EXPECTED_SYSTEM_EXCEPTIONS:
        assert f"`{name}`" in agents_text, f"core/AGENTS.md must document `{name}` in system exceptions section"

    # 4. Генератор: SYSTEM_EXCEPTIONS константа (единственный фильтр — не дрейфует)
    gen_text = GENERATOR.read_text(encoding="utf-8")
    assert "SYSTEM_EXCEPTIONS" in gen_text, "Generator must define SYSTEM_EXCEPTIONS constant"
    for name in EXPECTED_SYSTEM_EXCEPTIONS:
        assert f'"{name}"' in gen_text, f"Generator SYSTEM_EXCEPTIONS missing {name}"

    logger.info(
        "[IMP:9][system_exceptions] PASS — system exceptions documented in manifest+AGENTS.md+generator"
    )


# endregion FUNC_test_system_exceptions_documented
