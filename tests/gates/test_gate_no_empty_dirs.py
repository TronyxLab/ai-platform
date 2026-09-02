# GREP_SUMMARY: gate no-empty-dirs gitkeep runtime-mount category R5-negative 171-W4.3
# STRUCTURE: ┌_scan_empty_dirs(root)┐ → ◇ пустой каталог? → ◇ runtime-категория? → ⊕ violations → RED ‖ ▶ R5-negative (tmp_path пустой / .gitkeep) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 171 W4.3): пустые каталоги в рабочем дереве = RED. Пустой
##           каталог — мусор (класс дефекта init/ — удалён W6.1) и невидимая в git
##           структура, вводящая в заблуждение агентов.
## @scope    Скан рабочего дерева, исключая категории: .git/.venv/logs/.ai/node_modules/
##           __pycache__/типографские артефакты (категорийное правило, не список имён).
## @invariants
##   - Пустой каталог = каталог без единого файла/подкаталога (find -type d -empty)
##   - Каталог с .gitkeep не пуст по определению
##   - R5-negative: пустой каталог ловится; с .gitkeep — нет
## @rationale  W6.1 удаляет пустой init/; гейт предотвращает возврат класса дефекта.
## @changes  2026-08-15 · Created (DevPlan 171 W4.3)
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
import pathlib

import pytest

# Категории runtime/локальных каталогов (создаются инструментами, не репо-контентом).
# 177 W1.7: + .kilo (состояние Kilo-расширения, gitignored) и load-results (gitignored
# артефакты load-тестов, core/AGENTS.md §Нагрузочное тестирование) — dev-локали имели
# пустые каталоги-артефакты; CI (fresh checkout) их не имеет по построению.
_SKIP_PARTS = {
    ".git",
    ".venv",
    "logs",
    ".ai",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".kilo",
    "load-results",
    ".worktrees",
    # F-04 (2026-09-02): .local — gitignored dev-runtime зона (PROMETHEUS_TARGETS_DIR
    # dev-default <root>/.local/prometheus-targets; docker bind-mount автосоздаёт
    # каталог при `make up` — пустым, fresh-checkout его не имеет). Тот же класс,
    # что load-results/.kilo (177 W1.7).
    ".local",
    # 2026-08-27: projects/ — gitignored операторская зона контекстов (~/projects
    # канон живёт вне репо, но на dev-машинах scaffolder'ы создают projects/<ctx>/
    # внутри дерева); внешний процесс может пересоздавать каталоги в этой зоне
    # во время гейта (замечено live: projects/asi-faq ×3 за 40 мин).
    "projects",
    # 017: R5-probe каталог test_gate_marker_location создаётся/удаляется в реальном
    # tests/ НАМЕРЕННО (TRAP там) — xdist-гонка со сканом пустых каталогов того же
    # батча ловила мгновение существования; конвенция _EXCLUDED_DIRS для жертв.
    "_gate_probe_marker_tmp",
}


# region FUNC_scan_empty_dirs
## @purpose  Найти пустые каталоги в рабочем дереве (вне runtime-категорий).
## @io       ⇥ root: Path → ⎋ list[Path] — пустые каталоги
## @complexity O(D) — каталоги дерева
def _scan_empty_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Find empty directories outside runtime categories (os.walk — не полагается на find)."""
    empty: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dir_path = pathlib.Path(dirpath)
        rel = dir_path.relative_to(root)
        if any(part in _SKIP_PARTS for part in rel.parts):
            dirnames[:] = []
            continue
        if not dirnames and not filenames:
            empty.append(dir_path)
    return empty


# endregion FUNC_scan_empty_dirs


# 🧪 TRAP[TEST] · Regression · пустые каталоги в дереве (DevPlan 171 W4.3)
# · Scenario: пустой каталог init/ существовал в корне репо (мусор W6.1) — git его
# ·   не отслеживает, агенты видят phantom-структуру.
# · Last fail: N/A (new gate)
# · Remove if: политика пустых каталогов изменяется
@pytest.mark.gate
def test_no_empty_dirs() -> None:
    """RED: пустые каталоги в рабочем дереве (вне runtime-категорий)."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    empty = _scan_empty_dirs(root)
    if empty:
        lines = "\n".join(f"  {p.relative_to(root)}" for p in empty)
        pytest.fail(
            f"Empty directories detected ({len(empty)}): remove them (rmdir) — git не "
            f"отслеживает пустые каталоги, они не доставляются и вводят в заблуждение.\n{lines}"
        )


# 🧪 TRAP[TEST] · R5-negative · 171-W4.3 · пустой каталог ловится
# · Original form: пустой init/ в корне (W6.1) — точный вход класса дефекта.
# · Scenario: tmp_path с пустым каталогом _probe_empty/ → детектор обязан поймать.
@pytest.mark.gate
def test_empty_dirs_negative_detected(tmp_path: pathlib.Path) -> None:
    """R5-negative: пустой каталог детектируется."""
    (tmp_path / "_probe_empty").mkdir()

    violations = _scan_empty_dirs(tmp_path)

    assert len(violations) == 1, f"Expected exactly 1 empty dir, got {violations}"
    assert violations[0].name == "_probe_empty"


# 🧪 TRAP[TEST] · R5-negative · 171-W4.3 · .gitkeep-каталог НЕ flagged
# · Scenario: каталог с .gitkeep — не пуст по определению → 0 нарушений.
@pytest.mark.gate
def test_empty_dirs_negative_gitkeep_ok(tmp_path: pathlib.Path) -> None:
    """R5-negative: каталог с .gitkeep не считается пустым."""
    keep_dir = tmp_path / "hermes-agent"
    keep_dir.mkdir()
    (keep_dir / ".gitkeep").write_text("", encoding="utf-8")

    violations = _scan_empty_dirs(tmp_path)

    assert violations == [], f"Expected no violations, got {violations}"
