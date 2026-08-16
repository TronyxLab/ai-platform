# GREP_SUMMARY: gate generated-marker-orphan marker-generator-whitelist scan R5-negative 171-W4.1
# STRUCTURE: ┌_scan_generated_markers(root)┐ → ◇ marker ∈ whitelist (generator-владелец)? → ◇ tests/** fixture? → ⊕ orphans → RED ‖ ▶ R5-negative (tmp_path orphan-маркер) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 171 W4.1): каждый маркер `<!-- GENERATED:START:<marker> -->` в
##           репозитории обязан иметь генератор-владельца (контракт пар generator→marker).
##           Orphan-маркер (GENERATED-секция без генератора) = RED — класс дефекта
##           мёртвых GENERATED-секций canon-operations (удалены W1.1).
## @scope    Скан *.md + *.py + *.yaml по корню репо, исключая .git/.venv/logs/.ai/plans.
## @invariants
##   - Whitelist — контракт генераторов (не исторический список): generate_agents_md.py
##     → {glossary, canon_table}; gen_project_platform_md.py → {platform_md, practices_md}
##     (маркеры в проектах, вне репо); tests/** — test-fixture-маркеры легитимны
##   - Новый генератор → добавляет пару в whitelist (правка гейта = регистрация контракта)
##   - R5-negative: orphan-маркер в tmp_path ловится сканером
## @rationale  «0 мёртвых GENERATED-секций» (Brief AC): после W1.1 маркеры canon-operations
##             удалены; гейт предотвращает возврат класса дефекта.
## @changes  2026-08-15 · Created (DevPlan 171 W4.1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import pathlib
import re

import pytest

# Контракт генераторов: marker → (файл-генератор, описание). Правка при регистрации
# нового генератора GENERATED-секций.
_GENERATOR_OWNERS: dict[str, tuple[str, str]] = {
    "canon_table": ("core/internal/scripts/generate_agents_md.py", "canonical operations table (core/AGENTS.md)"),
    "glossary": ("core/internal/scripts/generate_agents_md.py", "root glossary (AGENTS.md)"),
    "platform_md": ("core/internal/scaffold/gen_project_platform_md.py", "project AI-PLATFORM.md (проекты, вне репо)"),
    "practices_md": ("core/internal/scaffold/gen_project_platform_md.py", "project AI-PLATFORM.md practices (проекты)"),
}

_MARKER_RE = re.compile(r"<!--\s*GENERATED:START:([\w-]+)\s*-->")

# 177 W1.7: + .kilo — состояние Kilo-расширения (gitignored); .kilo/worktrees/* — чекауты
# ДРУГИХ веток с историческими маркерами — не репо-контент текущего дерева (CI их не имеет).
_SKIP_PARTS = {".git", ".venv", "logs", "node_modules", "__pycache__", ".kilo"}

# Файлы-генераторы имеют право упоминать произвольные маркеры (f-string-шаблоны инжекции).
_GENERATOR_FILES: frozenset[str] = frozenset(owner for owner, _ in _GENERATOR_OWNERS.values())


# region FUNC_scan_generated_markers
## @purpose  Скан GENERATED:START-маркеров по дереву; orphan (без владельца и вне tests/) → RED.
## @io       ⇥ root: Path → ⎋ list[tuple[Path, str]] — (файл, маркер) орфанов
## @complexity O(F * L) — файлы × строки
def _scan_generated_markers(root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Find GENERATED:START markers without a registered generator owner."""
    orphans: list[tuple[pathlib.Path, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".py", ".yaml", ".yml"}:
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_PARTS for part in rel.parts) or (rel.parts[0] == ".ai" and "plans" in rel.parts):
            continue
        rel_str = rel.as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _MARKER_RE.finditer(text):
            marker = m.group(1)
            is_test_fixture = rel.parts[0] == "tests"
            is_generator_file = rel_str in _GENERATOR_FILES
            if marker in _GENERATOR_OWNERS or is_test_fixture or is_generator_file:
                continue
            orphans.append((path, marker))
    return orphans


# endregion FUNC_scan_generated_markers


# 🧪 TRAP[TEST] · Regression · orphan GENERATED-маркер без генератора (DevPlan 171 W4.1)
# · Scenario: core/AGENTS.md содержал canon-operations секции БЕЗ активного генератора
# ·   (мёртвые дубли W1.1) — класс дефекта «GENERATED-секция без генератора».
# · Last fail: N/A (new gate)
# · Remove if: GENERATED-marker механизм отменяется
@pytest.mark.gate
def test_no_orphan_generated_markers() -> None:
    """RED: любой GENERATED:START-маркер без генератора-владельца (вне tests/)."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    orphans = _scan_generated_markers(root)
    if orphans:
        lines = "\n".join(f"  {p.relative_to(root)}: `{m}`" for p, m in orphans)
        pytest.fail(
            f"Orphan GENERATED markers detected ({len(orphans)}): marker without a registered "
            f"generator owner. Register the pair in _GENERATOR_OWNERS or remove the section.\n{lines}"
        )


# 🧪 TRAP[TEST] · R5-negative · 171-W4.1 · orphan-маркер ловится сканером
# · Original form: core/AGENTS.md canon-operations секции (маркер без владельца) —
# ·   точный вход класса дефекта W1.1.
# · Scenario: tmp_path с _probe.md содержащим `<!-- GENERATED:START:canon-operations -->`
# ·   → детектор обязан вернуть орфана.
@pytest.mark.gate
def test_generated_marker_negative_orphan_detected(tmp_path: pathlib.Path) -> None:
    """R5-negative: orphan-маркер (canon-operations-класс) детектируется."""
    probe = tmp_path / "_probe_orphan.md"
    probe.write_text(
        "<!-- GENERATED:START:canon-operations -->\n| dead | rows |\n<!-- GENERATED:END:canon-operations -->\n",
        encoding="utf-8",
    )

    orphans = _scan_generated_markers(tmp_path)

    assert len(orphans) == 1, f"Expected exactly 1 orphan, got {orphans}"
    assert orphans[0][1] == "canon-operations", f"Expected canon-operations marker, got {orphans[0][1]}"


# 🧪 TRAP[TEST] · R5-negative · 171-W4.1 · registered marker НЕ flagged (no false positives)
# · Scenario: tmp_path с canon_table маркером (владелец зарегистрирован) → 0 орфанов.
@pytest.mark.gate
def test_generated_marker_negative_registered_ok(tmp_path: pathlib.Path) -> None:
    """R5-negative: маркер с зарегистрированным владельцем не flagged."""
    probe = tmp_path / "_probe_registered.md"
    probe.write_text(
        "<!-- GENERATED:START:canon_table -->\n| row |\n<!-- GENERATED:END:canon_table -->\n",
        encoding="utf-8",
    )

    orphans = _scan_generated_markers(tmp_path)

    assert orphans == [], f"Expected no orphans, got {orphans}"
