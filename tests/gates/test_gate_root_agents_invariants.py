#!/usr/bin/env python3
# GREP_SUMMARY: gate root-agents invariants trap-rev-lines module-contract 11-rules repair-recipe R5 anti-survivorship
# STRUCTURE: ▶ extract MODULE_CONTRACT region → ◇ 11 invariant key-phrase scan → ◇ TRAP[TYPE] · block split → ⊕ Rev: presence check → ⎋ verdict (+2 R5 negatives + repair recipe)
# region MODULE_CONTRACT
## @purpose  Gate: защита от дрейфа root AGENTS.md — 11 архитектурных инвариантов (## @invariants
##           в MODULE_CONTRACT) обязаны присутствовать, и каждый TRAP-блок обязан иметь строку
##           «· Rev:» (Rev-условие инвалидации решения). DevPlan 160 W3 T3.1.
## @scope    Статический скан root AGENTS.md (только MODULE_CONTRACT region для инвариантов,
##           весь файл для TRAP-Rev). Без Docker, без subprocess — чистый текст + regex.
## @invariants
##   - 11 инвариантов проверяются ПО КЛЮЧЕВЫМ ФРАЗАМ (устойчиво к переформулировке, НЕ точные копии)
##   - Каждый инвариант: OR по группам фраз; внутри группы — AND (все фразы должны быть)
##   - TRAP-блок = от строки с маркером «TRAP[TYPE] ·» до следующего маркера или EOF
##   - Inline-ссылки вида «see TRAP[INDEX] in ...» НЕ считаются маркерами (нет « · » после ])
##   - Каждый TRAP-блок обязан содержать «· Rev:» (Rev-условие из .kilo/rules/markup.md)
##   - R5 anti-survivorship: negative-тесты на синтетическом тексте (удалённый инвариант,
##     TRAP без Rev) — детектор обязан ловить
##   - Repair-подсказка в тексте ошибки: [GATE:FAIL] + REPAIR_RECIPE_START/END
## @rationale root AGENTS.md — единственный Source of Truth архитектурных инвариантов (инвариант 4,
##            core/AGENTS.md «Архитектурные инварианты»: «11 архитектурных инвариантов платформы
##            определены ТОЛЬКО в AGENTS.md (root)»). Дрейф инвариантов = тихая деградация контракта,
##            которую никакой другой гейт не ловит. TRAP без Rev = решение без условия инвалидации.
## @changes 2026-08-13 | DevPlan 160 W3 T3.1 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT_AGENTS_PATH: Path = repo_root() / "AGENTS.md"

# ── TRAP marker / Rev patterns ──
# Маркер определения TRAP: «TRAP[TYPE] ·» (пробел + точка-разделитель сразу после ]).
# Inline-ссылки («see TRAP[INDEX] in DevPlan 047») НЕ матчатся — после ] идёт не « · ».
_TRAP_MARKER_RE = re.compile(r"TRAP\[[A-Z]+\]\s*·")
_REV_LINE_RE = re.compile(r"·\s*Rev:")

# region CONSTANTS_INVARIANTS
# ── 12 канонических инвариантов root AGENTS.md (извлечены из MODULE_CONTRACT; +12 2026-08-16 D2) ──
# Каждый инвариант = (номер, [группы фраз]); группа = кортеж фраз с AND-семантикой,
# инвариант присутствует, если матчится ХОТЯ БЫ одна группа. Регистронезависимо.
_INVARIANTS: list[tuple[int, list[tuple[str, ...]]]] = [
    # 1. Makefile — единый фасад, все операции через make <target>
    (1, [("единый фасад",), ("make <target>",)]),
    # 2. Модель деплоя: git push → CI
    (2, [("git push",), ("forced-command",), ("Модель деплоя",)]),
    # 3. org = context; каждый контекст — отдельная GitHub-организация
    (3, [("GitHub-организац",), ("org = context",), ("исходный репозиторий",)]),
    # 4. AGENTS.md — 3 канонических файла (root, core/, core/modules/)
    (4, [("3 канонических",), ("канонических файла",), ("templates/template-",)]),
    # 5. core/entrypoint-manifest.yaml — YAML-реестр канонических операций
    (5, [("entrypoint-manifest.yaml",)]),
    # 6. make bootstrap-node — строго идемпотентный
    (6, [("bootstrap-node",), ("идемпотентн",)]),
    # 7. Полный локальный стек через docker compose up на macOS
    (7, [("docker compose up",), ("локальный стек",)]),
    # 8. LiteLLM — PostgreSQL во всех окружениях (никакого SQLite)
    (8, [("LiteLLM", "PostgreSQL"), ("LiteLLM", "SQLite"), ("PostgreSQL", "SQLite")]),
    # 9. Тестовый сервер может быть пересоздан заново
    (9, [("Тестовый сервер",), ("пересоздан",), ("обратная совместимость",)]),
    # 10. Сборка образов hermes: hermes-build-context / hermes-push-l2 (L1→L2 коллапс DevPlan 002)
    (10, [("hermes-build-context",), ("hermes-push-l2",)]),
    # 11. Manifest Generation Contract — generated files коммитятся, НЕ редактируются вручную
    (11, [("Manifest Generation Contract",), ("generated files",), ("check-manifests",)]),
    # 12. docs-in-code — вся операционная документация только в коде, каталог docs/ запрещён
    (12, [("docs-in-code",), ("docs/ запрещён",)]),
]

# endregion CONSTANTS_INVARIANTS


# region SCANNERS


def _extract_module_contract(content: str) -> str:
    """Извлечь текст region MODULE_CONTRACT (без маркеров region).

    ## @purpose — Сканировать инварианты ТОЛЬКО в MODULE_CONTRACT root AGENTS.md —
    ##            вне region фразы-инварианты не считаются (инвариант 4: SoT — root).
    ## @io — ⇥ content: str → ⎋ str (текст region) | "" если region не найден
    ## @complexity — O(N) где N = строки файла
    ## @invariants
    ##   - Region начинается с '# region MODULE_CONTRACT', заканчивается '# endregion MODULE_CONTRACT'
    ##   - Не найдено → "" (детектор обязан сообщить об отсутствии региона)
    """
    match = re.search(r"#\s*region\s+MODULE_CONTRACT\s*\n(.*?)#\s*endregion\s+MODULE_CONTRACT", content, re.DOTALL)
    return match.group(1) if match else ""


def _scan_invariants(contract_text: str) -> list[str]:
    """Проверить присутствие 11 инвариантов по ключевым фразам. Возвращает violations.

    ## @purpose — Детектор T3.1: каждый из 11 инвариантов обязан иметь хотя бы одну
    ##            группу ключевых фраз в MODULE_CONTRACT. Устойчив к переформулировке —
    ##            фразы-ключи, не точные копии.
    ## @io — ⇥ contract_text: str (MODULE_CONTRACT region) → ⎋ list[str] violations
    ## @complexity — O(11 * G * P) — константа
    ## @invariants
    ##   - Пустой текст → 11 violations (все инварианты отсутствуют)
    ##   - AND внутри группы, OR между группами
    ##   - Регистронезависимый поиск фраз
    """
    violations: list[str] = []
    lowered = contract_text.lower()
    for num, groups in _INVARIANTS:
        matched = any(all(phrase.lower() in lowered for phrase in group) for group in groups)
        if not matched:
            violations.append(f"инвариант #{num} отсутствует или искажён (ключевые фразы: {groups})")
    return violations


def _split_trap_blocks(content: str) -> list[tuple[int, list[int]]]:
    """Разбить текст на TRAP-блоки по маркерам «TRAP[TYPE] ·». Возвращает (start_line, строки).

    ## @purpose — Общий сплиттер для _scan_trap_rev_blocks и теста (DRY): блок = от строки
    ##            с маркером TRAP[TYPE] · до следующего маркера или EOF. Inline-ссылки
    ##            («see TRAP[INDEX] in DevPlan 047») маркерами не считаются.
    ## @io — ⇥ content: str → ⎋ list[(start_line_1based, [line_indices_1based])]
    ## @complexity — O(N) где N = строки
    """
    lines = content.splitlines()
    blocks: list[tuple[int, list[int]]] = []
    current: list[int] = []
    for i, line in enumerate(lines, 1):
        if _TRAP_MARKER_RE.search(line):
            if current:
                blocks.append((current[0], current))
            current = [i]
        elif current:
            current.append(i)
    if current:
        blocks.append((current[0], current))
    return blocks


def _scan_trap_rev_blocks(content: str) -> list[str]:
    """Проверить, что каждый TRAP-блок содержит строку «· Rev:». Возвращает violations.

    ## @purpose — Детектор T3.1: каждый TRAP-маркер (TRAP[TYPE] ·) обязан иметь Rev-строку
    ##            в своём блоке (до следующего маркера или EOF). TRAP без Rev = решение без
    ##            условия инвалидации — дрейф-вектор (.kilo/rules/markup.md).
    ## @io — ⇥ content: str (весь AGENTS.md) → ⎋ list[str] violations
    ## @complexity — O(B * L) где B = блоки, L = строки в блоке
    ## @invariants
    ##   - Маркер = «TRAP[TYPE] ·»; inline-ссылки «see TRAP[INDEX] in ...» не маркеры
    ##   - Блок = строки от маркера до следующего маркера/EOF (включительно)
    ##   - «· Rev:» ищется в объединённом тексте блока (многострочный TRAP поддерживается)
    """
    lines = content.splitlines()
    violations: list[str] = []
    for start, block in _split_trap_blocks(content):
        block_text = "\n".join(lines[j - 1] for j in block)
        if not _REV_LINE_RE.search(block_text):
            violations.append(f"TRAP-блок со строки {start} не содержит строки «· Rev:» (Rev-условие инвалидации)")
    return violations


# endregion SCANNERS


# region REPAIR_RECIPE


def _repair_recipe_invariants() -> str:
    """Machine-parsable repair-подсказка для инвариантов (DevPlan 060 M-ADE Envelope)."""
    return (
        "[GATE:FAIL][id:root-agents-invariants][class:L2]\n"
        ">>> REPAIR_RECIPE_START >>>\n"
        "Восстанови 11 архитектурных инвариантов в region MODULE_CONTRACT root AGENTS.md "
        "(## @invariants блок, пункты 1-11). Исходный текст — git show HEAD:AGENTS.md "
        "или core/AGENTS.md §«Архитектурные инварианты». Проверка по ключевым фразам — "
        "достаточно сохранить суть каждого правила, точные копии не обязательны.\n"
        "<<< REPAIR_RECIPE_END <<<"
    )


def _repair_recipe_trap_rev() -> str:
    """Machine-parsable repair-подсказка для TRAP Rev-строк."""
    return (
        "[GATE:FAIL][id:root-agents-trap-rev][class:L2]\n"
        ">>> REPAIR_RECIPE_START >>>\n"
        "Добавь строку «· Rev: <условие инвалидации>» в каждый TRAP-блок root AGENTS.md без неё. "
        "Формат: .kilo/rules/markup.md §Decision Trap — Rev обязателен для всех TRAP-типов.\n"
        "<<< REPAIR_RECIPE_END <<<"
    )


# endregion REPAIR_RECIPE


# region TESTS


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 11 инвариантов root AGENTS.md (DevPlan 160 W3 T3.1)
# · Scenario: дрейф MODULE_CONTRACT root AGENTS.md — инвариант удалён/переформулирован без сути
# · Last fail: N/A (preventive gate)
# · Remove if: инварианты мигрируют из AGENTS.md в machine-readable SoT (например, contracts.py)
def test_root_agents_11_invariants_present(caplog) -> None:
    """Все 11 архитектурных инвариантов присутствуют в MODULE_CONTRACT root AGENTS.md."""
    content = ROOT_AGENTS_PATH.read_text(encoding="utf-8")
    contract = _extract_module_contract(content)
    assert contract, "region MODULE_CONTRACT не найден в root AGENTS.md — инварианты отсутствуют"

    violations = _scan_invariants(contract)
    logger.info("[IMP:8][root-agents-invariants] Проверено %d инвариантов в MODULE_CONTRACT", len(_INVARIANTS))
    if violations:
        for v in violations:
            logger.warning("[IMP:7][root-agents-invariants] %s", v)
    assert not violations, _repair_recipe_invariants() + "\n\nОтсутствующие инварианты:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][root-agents-invariants] PASS: все %d инвариантов на месте", len(_INVARIANTS))


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · TRAP-блоки root AGENTS.md обязаны иметь Rev: (T3.1)
# · Scenario: новый TRAP[DECISION] добавлен без Rev-строки (решение без условия инвалидации)
# · Last fail: N/A (preventive gate)
# · Remove if: TRAP-формат перестаёт требовать Rev (смена .kilo/rules/markup.md)
def test_root_agents_traps_have_rev_lines(caplog) -> None:
    """Каждый TRAP-блок в root AGENTS.md содержит строку «· Rev:» (Rev-условие)."""
    content = ROOT_AGENTS_PATH.read_text(encoding="utf-8")
    blocks = _split_trap_blocks(content)
    violations = _scan_trap_rev_blocks(content)
    logger.info("[IMP:8][root-agents-trap-rev] Проверено TRAP-блоков: %d", len(blocks))
    if violations:
        for v in violations:
            logger.warning("[IMP:7][root-agents-trap-rev] %s", v)
    assert not violations, _repair_recipe_trap_rev() + "\n\nПроблемные TRAP-блоки:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][root-agents-trap-rev] PASS: все TRAP-блоки имеют «· Rev:»")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · root-agents-invariants — удалённый инвариант
# · Last fail: инвариант #1 («единый фасад») удалён из MODULE_CONTRACT
# · Remove if: инварианты мигрируют из AGENTS.md в machine-readable SoT
def test_negative_missing_invariant_detected(caplog, tmp_path: Path) -> None:
    """R5 negative: синтетический MODULE_CONTRACT без инварианта #1 → violations ≥ 1."""
    synthetic = (
        "# region MODULE_CONTRACT\n"
        "## @invariants\n"
        "##   2. Модель деплоя: git push → CI.\n"
        "##   3. org = context.\n"
        "# endregion MODULE_CONTRACT\n"
    )
    violations = _scan_invariants(synthetic)
    logger.info("[IMP:8][root-agents-invariants][negative] violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: детектор не поймал удалённый инвариант #1 — violations={violations!r}"
    assert any("#1" in v or "инвариант #1" in v for v in violations), (
        f"R5 FAIL: violations не указывают на инвариант #1: {violations!r}"
    )
    logger.info("[IMP:9][root-agents-invariants][negative] PASS: удалённый инвариант #1 детектируется")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · root-agents-trap-rev — TRAP без Rev
# · Last fail: TRAP[DECISION] добавлен без строки «· Rev:»
# · Remove if: TRAP-формат перестаёт требовать Rev (смена .kilo/rules/markup.md)
def test_negative_trap_without_rev_detected(caplog, tmp_path: Path) -> None:
    """R5 negative: синтетический TRAP-блок без «· Rev:» → violations ≥ 1."""
    synthetic = (
        "⚠️ TRAP[DECISION] · 2026-08-13 · HI · Тестовое решение без Rev\n"
        "· Rejected: альтернатива X\n"
        "· Reason: обоснование\n"
    )
    violations = _scan_trap_rev_blocks(synthetic)
    logger.info("[IMP:8][root-agents-trap-rev][negative] violations=%d", len(violations))
    assert len(violations) >= 1, f"R5 FAIL: детектор не поймал TRAP без Rev — violations={violations!r}"
    assert "Rev" in violations[0], f"R5 FAIL: violation не упоминает Rev: {violations!r}"
    logger.info("[IMP:9][root-agents-trap-rev][negative] PASS: TRAP без Rev детектируется")


# endregion TESTS
