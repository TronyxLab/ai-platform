# GREP_SUMMARY: gate no-disabled-files backup-orig-rej tracked R5-negative 171-W4.2
# STRUCTURE: ┌_scan_disabled_files(root)┐ → ◇ tracked *.disabled|*.bak|*.orig|*.rej? → ⊕ violations → RED ‖ ▶ R5-negative (tmp_path) → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate (DevPlan 171 W4.2): в отслеживаемом дереве НЕ должно быть файлов с
##           суффиксами *.disabled / *.bak / *.orig / *.rej — выключенные/резервные
##           артефакты рядом с активными конфигами = мёртвый груз и источник путаницы
##           (класс дефекта: contact-points.yml.disabled, удалён W1.4).
## @scope    Скан всего дерева, исключая .git/.venv/logs/.ai/plans/node_modules/__pycache__.
## @invariants
##   - Категорийное правило (суффикс-класс), не перечень имён
##   - R5-negative: *.disabled в tmp_path ловится сканером
##   - Новый тип мусорного суффикса → добавить в категорию (не в перечень имён)
## @rationale  W1.4 удалил contact-points.yml.disabled; гейт предотвращает возврат класса.
## @changes  2026-08-15 · Created (DevPlan 171 W4.2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import pathlib

import pytest

_DISABLED_SUFFIXES: tuple[str, ...] = (".disabled", ".bak", ".orig", ".rej")

_SKIP_PARTS = {".git", ".venv", "logs", "node_modules", "__pycache__"}


# region FUNC_scan_disabled_files
## @purpose  Скан на мусорные суффиксы в отслеживаемом дереве.
## @io       ⇥ root: Path → ⎋ list[Path] — найденные файлы
## @complexity O(F) — файлы
def _scan_disabled_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Find tracked files with disabled/backup suffixes (*.disabled, *.bak, *.orig, *.rej)."""
    violations: list[pathlib.Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        if rel.parts[0] == ".ai" and "plans" in rel.parts:
            continue
        if path.name.endswith(_DISABLED_SUFFIXES):
            violations.append(path)
    return violations


# endregion FUNC_scan_disabled_files


# 🧪 TRAP[TEST] · Regression · disabled/backup-файлы в дереве (DevPlan 171 W4.2)
# · Scenario: contact-points.yml.disabled существовал рядом с активным contact-points.yml
# ·   (выключенный конфиг в provisioning-каталоге) — класс дефекта W1.4.
# · Last fail: N/A (new gate)
# · Remove if: disabled/backup-суффиксы легализуются политикой
@pytest.mark.gate
def test_no_disabled_files() -> None:
    """RED: *.disabled / *.bak / *.orig / *.rej файлы в отслеживаемом дереве."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    violations = _scan_disabled_files(root)
    if violations:
        lines = "\n".join(f"  {p.relative_to(root)}" for p in violations)
        pytest.fail(
            f"Disabled/backup files detected ({len(violations)}): tracked files with "
            f"{', '.join(_DISABLED_SUFFIXES)} suffixes. Remove them (git rm) — "
            f"активный конфиг + отключенный дубль = дрейф.\n{lines}"
        )


# 🧪 TRAP[TEST] · R5-negative · 171-W4.2 · *.disabled ловится сканером
# · Original form: contact-points.yml.disabled (monitoring alerting provisioning) —
# ·   точный вход класса дефекта W1.4.
# · Scenario: tmp_path с _probe.yml.disabled → детектор обязан поймать.
@pytest.mark.gate
def test_disabled_files_negative_detected(tmp_path: pathlib.Path) -> None:
    """R5-negative: *.disabled файл детектируется сканером."""
    probe = tmp_path / "_probe.yml.disabled"
    probe.write_text("enabled: false\n", encoding="utf-8")

    violations = _scan_disabled_files(tmp_path)

    assert len(violations) == 1, f"Expected exactly 1 violation, got {violations}"
    assert violations[0].name.endswith(".disabled")


# 🧪 TRAP[TEST] · R5-negative · 171-W4.2 · нормальные файлы НЕ flagged (no false positives)
# · Scenario: tmp_path с обычными .yml/.md/.py → 0 нарушений.
@pytest.mark.gate
def test_disabled_files_negative_normal_ok(tmp_path: pathlib.Path) -> None:
    """R5-negative: файлы без мусорных суффиксов не flagged."""
    (tmp_path / "contact-points.yml").write_text("points: []\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# ok\n", encoding="utf-8")
    (tmp_path / "script.py").write_text("print(1)\n", encoding="utf-8")

    violations = _scan_disabled_files(tmp_path)

    assert violations == [], f"Expected no violations, got {violations}"
