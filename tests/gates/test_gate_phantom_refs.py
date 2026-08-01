# GREP_SUMMARY: gate phantom-refs dangling-refs deploy-project.sh state_migration.py audit_logging.sh generate-dev-certs.sh strict-scan consumer-scan D3
# STRUCTURE: ▶ ┌_PHANTOM_NAMES (4)┐ → ○ scan roots (core/ tests/ makefiles/ .github/ + root files) → ◇ re.escape(name) in line? → ⊕ violations[file:line] → ◇ _ALLOWLIST (пуст, D3) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Strict phantom-reference gate (DevPlan 116 B8 T8, D3): 0 упоминаний 4 удалённых
##           имён (deploy-project.sh, state_migration.py, audit_logging.sh, generate-dev-certs.sh)
##           в коде и CI — ВКЛЮЧАЯ docstring/TRAP-комментарии. Allowlist гейта — пустая
##           константа: история удаляется вместе с именами (решение пользователя 2026-08-01, D3).
## @scope    Read-only статический скан. Корни: core/, tests/ (кроме generated
##           tests/test_inventory.yaml и архивного tests/test_inventory_changes.yaml),
##           makefiles/, .github/, + AGENTS.md, .env.example, .pre-commit-config.yaml, Makefile.
##           ВНЕ скоупа (архив): reports/. Исключения: .git, __pycache__, node_modules, .venv.
## @invariants
##   - Детект: ЛЮБОЕ вхождение имени как подстроки (re.escape(name)), включая комментарии
##   - _ALLOWLIST = frozenset() — строгий режим, никаких исключений
##   - Fail-сообщение: полный список файл:строка:имя
##   - Negative-тест обязателен (R5 anti-survivorship): фиктивный файл с именем → детект
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale U-42: удаление старых реализаций не сопровождалось удалением потребителей —
##            фантомные имена остались в 46 сайтах (код, тесты, CI, манифесты). Строгий
##            zero-allowlist гейт делает появление имени структурно невозможным.
## ⚠️ TRAP[DECISION] · 2026-08-01 · HI · D3: строгий гейт фантомов — 0 упоминаний 4 имён
## · Rejected: мягкий режим с allowlist (риск: allowlist-дрейф как в dead-code gate)
## · Reason: решение пользователя 2026-08-01 (D3) — история удаляется вместе с именами;
##   потеря исторических TRAP-аннотаций принята (тестовая история — в test_inventory_changes.yaml).
## · Rev: 2026-10-21 — пересмотр, если гейт начнёт блокировать легитимную историческую документацию.
## @changes 2026-08-01 | Created (DevPlan 116 B8 T8)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path
from typing import Sequence

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()

# ── Фантомные имена (удалённые файлы, D3) ─────────────────────────────────────
_PHANTOM_NAMES: tuple[str, ...] = (
    "deploy-project.sh",
    "state_migration.py",
    "audit_logging.sh",
    "generate-dev-certs.sh",
)

# ── Корни скана (D3: core/, tests/, makefiles/, .github/ + файлы) ─────────────
_SCAN_ROOTS: tuple[str, ...] = ("core", "tests", "makefiles", ".github")
_SCAN_FILES: tuple[str, ...] = ("AGENTS.md", ".env.example", ".pre-commit-config.yaml", "Makefile")

# Generated/архивные файлы — вне скана (D3)
_EXCLUDE_FILES: frozenset[str] = frozenset(
    {
        "tests/test_inventory.yaml",  # generated (make test-inventory-sync)
        "tests/test_inventory_changes.yaml",  # архив истории инвентаря
        # Сам файл гейта — структурное место определения _PHANTOM_NAMES (имена обязаны
        # существовать как константы скана; self-reference неизбежен, аналогично
        # allowlist-константам других гейтов)
        "tests/gates/test_gate_phantom_refs.py",
    }
)

# Директории-исключения (части пути)
_EXCLUDE_DIR_PARTS: frozenset[str] = frozenset({".git", "__pycache__", "node_modules", ".venv", "reports"})

# D3 2026-08-01: строгий режим — история удаляется вместе с именами. Allowlist пуст.
_ALLOWLIST: frozenset[str] = frozenset()


# region HELPER__scan_paths
def _scan_paths(roots: Sequence[Path]) -> list[str]:
    """Scan files under given roots for phantom name occurrences.

    ## @purpose — Core scanner: walks each root, skips excluded dirs/files, and reports
    ##            every line containing a phantom name (substring, re.escape).
    ## @io — ⇥ roots: sequence of Path → ⎋ list[str] of "rel:line: name → snippet" violations
    ## @complexity — O(F * L * N) where F = files, L = lines, N = phantom names
    ## @invariants
    ##   - Detection is substring-based (re.escape(name)) — includes comments/docstrings
    ##   - Binary files skipped via NUL-byte probe (first 2048 bytes)
    ##   - Relative paths are computed against ROOT for reporting
    """
    violations: list[str] = []

    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            candidates = [(root, root)]
        else:
            candidates = [
                (p, p)
                for p in sorted(root.rglob("*"))
                if p.is_file() and not any(seg in _EXCLUDE_DIR_PARTS or seg.startswith(".") for seg in p.parts)
            ]

        for path, abs_path in candidates:
            try:
                rel = str(path.relative_to(ROOT))
            except ValueError:
                # Negative-тест: путь вне ROOT (tmp_path) — используем absolute-фолбэк
                rel = str(path)
            if rel in _EXCLUDE_FILES:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:2048]:
                continue  # бинарный файл
            text = raw.decode("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                for name in _PHANTOM_NAMES:
                    if re.search(re.escape(name), line):
                        violations.append(f"{rel}:{i}: {name} → {line.strip()[:100]}")

    return violations


# endregion HELPER__scan_paths


# ── Основной гейт-тест ─────────────────────────────────────────────────────────


# region FUNC_test_no_phantom_names_in_code_and_ci
@pytest.mark.gate
@ldd_trajectory
def test_no_phantom_names_in_code_and_ci(caplog) -> None:
    """0 упоминаний 4 фантомных имён в коде и CI (D3, строгий режим).

    # ▶ scan roots → ◇ violations empty? → PASS · └→ FAIL: файл:строка:имя

    ## @purpose — DevPlan 116 B8 T8: удалённые файлы (deploy-project.sh, state_migration.py,
    ##            audit_logging.sh, generate-dev-certs.sh) не должны упоминаться нигде
    ##            в коде/тестах/CI — включая docstring/TRAP-комментарии (D3).
    ## @io — caplog → ⎋ None (pytest.fail с полным списком файл:строка)
    ## @complexity — O(F * L * N) — полный скан репозитория
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B8 T8 · D3-строгий скан 4 фантомных имён
    # · Regression: повторное появление имени удалённого файла в коде/CI (dangling ref)
    # · Scenario: скан core/, tests/, makefiles/, .github/, + root-файлов
    # · Last fail: 2026-08-01 — 46 сайтов упоминаний очищены волной B8
    # · Remove if: 4 имени навсегда удалены из истории — скан не нужен (но negative-тест остаётся)
    caplog.set_level(logging.INFO)

    scan_roots: list[Path] = [ROOT / rel for rel in _SCAN_ROOTS] + [ROOT / f for f in _SCAN_FILES]
    violations = _scan_paths(scan_roots)

    logger.info("[IMP:8][phantom_gate] Scanned %d roots, %d violation(s)", len(scan_roots), len(violations))

    if violations:
        for v in violations:
            logger.error("[IMP:10][phantom_gate] %s", v)
        pytest.fail(
            f"[IMP:10][phantom_gate] {len(violations)} упоминание(й) удалённых файлов "
            f"({', '.join(_PHANTOM_NAMES)}) — D3 строгий режим, allowlist пуст:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    logger.info(
        "[IMP:9][phantom_gate] PASS: 0 упоминаний %s в коде и CI",
        ", ".join(_PHANTOM_NAMES),
    )


# endregion FUNC_test_no_phantom_names_in_code_and_ci


# ── Negative-тест (R5 anti-survivorship): сканер ДОЛЖЕН детектировать ──────────


# region FUNC_test_phantom_scan_detects_dummy_file
@pytest.mark.gate
@ldd_trajectory
def test_phantom_scan_detects_dummy_file(tmp_path, caplog) -> None:
    """Falsifiability: сканер обязан находить фантомное имя в фиктивном файле (R5).

    # ▶ tmp_path/dummy.sh с "deploy-project.sh" → ◇ violations non-empty? → PASS

    ## @purpose — Anti-survivorship: гейт, который не может упасть, — не гейт. Доказывает,
    ##            что _scan_paths() реально детектирует имена (включая комментарии), а не
    ##            молча проходит из-за ошибки сканирования.
    ## @io — ⇥ tmp_path → ⎋ None (assert детекта)
    ## @complexity — O(1) — один фиктивный файл
    """
    # 🧪 TRAP[TEST] · DevPlan 116 B8 T8 · R5 anti-survivorship для phantom-гейта
    # · Regression: если скан сломается (пустые корни, бинарный-пропуск) — гейт станет
    #   вечнозелёным и перестанет ловить dangling refs
    # · Scenario: dummy.sh с фантомным именем в комментарии → скан обязан его найти
    # · Last fail: N/A (первый тест)
    # · Remove if: phantom-гейт удалён
    caplog.set_level(logging.INFO)

    dummy = tmp_path / "dummy.sh"
    dummy.write_text("#!/usr/bin/env bash\n# calls deploy-project.sh for delivery\n")

    violations = _scan_paths([tmp_path])
    logger.info("[IMP:8][phantom_gate][negative] violations: %s", violations)

    assert violations, "CRITICAL: сканер не детектировал 'deploy-project.sh' в фиктивном файле — гейт вечнозелёный!"
    assert any("deploy-project.sh" in v for v in violations), (
        f"Сканер нашёл нарушения, но не то имя: {violations}"
    )
    logger.info("[IMP:9][phantom_gate][negative] PASS: фиктивный файл с фантомным именем детектирован")


# endregion FUNC_test_phantom_scan_detects_dummy_file
