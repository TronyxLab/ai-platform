#!/usr/bin/env python3
# GREP_SUMMARY: gate docs-dir-forbidden docs-in-code invariant-12 tracked-md-allowlist tripwire R5 anti-survivorship zero-hardcode DevPlan-164-WD-4
# STRUCTURE: ▶ _find_docs_dir(root) → ◇ (root/docs).is_dir()? → ⟦RED⟧ → ▶ git ls-files "*.md" (read-only, test-only) → ◇ _check_md_allowlist → ⟦RED: offenders⟧ → ⎋ PASS ‖ R5-negative: tmp_path/docs → детектор RED
# region MODULE_CONTRACT
## @purpose  Gate-тринити запрета каталога docs/ (инвариант №12, DevPlan 164 WD-4):
##           RED при существовании каталога docs/ в корне репо ИЛИ при tracked .md вне
##           allowlist. Tripwire-дизайн: RED пока docs/ существует (удаление — WD-5),
##           зелёный после полной миграции docs-in-code.
## @scope    Статический скан репо. git ls-files "*.md" (read-only subprocess) — ТОЛЬКО
##           внутри test_tracked_md_allowlist; логика вынесена в чистые функции
##           _find_docs_dir/_check_md_allowlist с параметром root для unit-теста (Zero
##           Hardcode Rule — tmp_path, не хардкод-пути репо).
## @invariants
##   - allowlist фиксируется в тесте из ФАКТИЧЕСКОГО git ls-files (2026-08-14); docs/**
##     намеренно НЕ входит (запрещён инвариантом №12)
##   - git ls-files — read-only; недоступность git → pytest.fail (не skip — Test Honesty R4)
##   - Детекторы — чистые функции (root: Path), R5-негатив на tmp_path (анти-survivorship:
##     детектор обязан ловить docs/, а не быть no-op)
##   - Регистрация тринити: файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest.yaml
##     (авто-генерация из pytest markers) + покрытие записью `gates` core/check-suite.yaml
##     (tier pytest, gate_modes [fast, full] — директорный pytest tests/gates/ подхватывает файл)
## @rationale Инвариант без гейта дрейфует (исторический класс дефектов, DevPlan 164 WD-4).
##            Отдельная per-gate запись в core/check-suite.yaml НЕ добавляется: schema v1 не
##            имеет test_file-поля, а gate_modes-запись сломала бы golden-паритет
##            test_gate_check_suite_consistency.py::_GOLDEN_FAST/_GOLDEN_FULL (список шагов
##            gate зафиксирован как константа). Директорный pytest записей `gates` —
##            канонический канал регистрации gate-файлов с DevPlan 120.
## @changes 2026-08-14 | DevPlan 164 WD-4 — created
# endregion MODULE_CONTRACT

from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT: Path = repo_root()

# region CONSTANTS_ALLOWLIST
# ── Allowlist tracked .md (собран из ФАКТИЧЕСКОГО `git ls-files "*.md"` 2026-08-14) ──
# docs/** намеренно ОТСУТСТВУЕТ — каталог запрещён инвариантом №12; его tracked-файлы
# остаются вне allowlist до удаления (WD-5), после чего исчезают из git ls-files.
_ALLOWED_MD_PATTERNS: tuple[str, ...] = (
    # AGENTS.md-семейство (канонические + вспомогательные, инвариант 4)
    "AGENTS.md",
    "core/AGENTS.md",
    "core/modules/AGENTS.md",
    "core/modules/nginx/AGENTS.md",
    "core/internal/bootstrap/AGENTS.md",
    "core/internal/shared/AGENTS.md",
    "tests/AGENTS.md",
    "tests/gates/AGENTS.md",
    # README.md (root) + systemd-модуль + e2e-инвентарь
    "README.md",
    "core/bootstrap/systemd/README.md",
    "tests/e2e/README.md",
    # hermes payload (L1 build: SOUL.md + Makefile.audit.md + skills + профили-шаблоны)
    "core/modules/hermes-agent/build/Makefile.audit.md",
    "core/modules/hermes-agent/build/config/SOUL.md",
    "core/modules/hermes-agent/build/skills/**",
    "core/modules/hermes-agent/build/templates/**",
    # templates/ (payload new-project/new-context — README.md + AGENTS.md)
    "templates/**",
    # сгенерированные (kilo) и процессные артефакты (планы)
    ".kilo/**",
    ".ai/**",
)

# endregion CONSTANTS_ALLOWLIST


# region FUNC_find_docs_dir
def _find_docs_dir(root: Path) -> bool:
    """Детектор каталога docs/ в root. Возвращает True если существует (RED).

    ▶ ┌root┐ → ○ (root/docs).is_dir() → ◇ True? → ⟦RED⟧ → ⎋ bool
    ## @purpose — Логика test_docs_dir_absent: существование docs/ в заданном корне —
    ##            нарушение инварианта №12. Чистая функция (root-параметр) — unit-тест
    ##            на tmp_path без хардкода путей репо (Zero Hardcode Rule).
    ## @io — ⇥ root: Path → ⎋ bool (True = docs/ существует)
    ## @complexity — O(1) (is_dir)
    ## @invariants — Ищет только <root>/docs (не вложенные); файл docs (не каталог) — False
    """
    return (root / "docs").is_dir()


# endregion FUNC_find_docs_dir


# region FUNC_check_md_allowlist
def _check_md_allowlist(root: Path, tracked_md: list[str]) -> list[str]:
    """Проверить tracked .md против allowlist-паттернов. Возвращает offenders (RED).

    ▶ ┌tracked_md┐ → ○ normalize (posix, strip ./) → ◇ fnmatch ⊆ _ALLOWED_MD_PATTERNS? → ⊕ offenders → ⎋ list[str]
    ## @purpose — Логика test_tracked_md_allowlist: любой tracked .md вне allowlist —
    ##            нарушение docs-in-code (новый .md обязан быть документирован в allowlist).
    ##            root — единая точка скана (совместимость сигнатуры с _find_docs_dir).
    ## @io — ⇥ root: Path, tracked_md: list[str] (пути из git ls-files) → ⎋ list[str] offenders
    ## @complexity — O(N * P) где N = tracked-файлов, P = allowlist-паттернов
    ## @invariants — Паттерн с ** матчит подкаталоги (fnmatch: * пересекает /);
    ##               только префикс "./" нормализуется (lstrip нарушил бы .ai/.kilo —
    ##               точка dot-директории значима); пустой tracked_md → [] (не RED)
    """
    offenders: list[str] = []
    for rel in sorted(tracked_md):
        norm = rel.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        if not any(fnmatch.fnmatch(norm, pat) for pat in _ALLOWED_MD_PATTERNS):
            offenders.append(norm)
    return offenders


# endregion FUNC_check_md_allowlist


# region TESTS


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · docs-dir-forbidden — возврат каталога docs/ (инвариант №12)
# · Scenario: docs/ создан в корне репо после миграции docs-in-code (WD-5 удаляет; tripwire RED)
# · Last fail: N/A (preventive gate; DevPlan 164 WD-4; исходный триггер — docs/ = 7 файлов, 1271 строка)
# · Remove if: инвариант №12 мигрирует из AGENTS.md в machine-readable SoT
def test_docs_dir_absent(caplog) -> None:
    """Инвариант №12: каталог docs/ НЕ существует в корне репозитория."""
    found = _find_docs_dir(ROOT)
    logger.info("[IMP:8][docs-forbidden][docs-dir] docs/ в корне репо: %s", found)
    assert not found, (
        "[IMP:10][docs-forbidden][docs-dir] RED: каталог docs/ существует в корне репо — "
        "нарушение инварианта №12 (docs-in-code). Удаление: WD-5 (git rm -r docs/) после "
        "полной миграции контента."
    )
    logger.critical("[IMP:9][docs-forbidden][docs-dir] PASS: каталог docs/ отсутствует в корне репо")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · docs-dir-forbidden — tracked .md вне allowlist
# · Scenario: новый tracked .md добавлен без allowlist-записи (или возвращён docs/*.md)
# · Last fail: N/A (preventive gate; allowlist собран из git ls-files 2026-08-14)
# · Remove if: инвариант №12 мигрирует из AGENTS.md в machine-readable SoT
def test_tracked_md_allowlist(caplog) -> None:
    """Все tracked .md обязаны покрываться allowlist-паттернами (docs/** — вне allowlist).

    git ls-files — единственный read-only subprocess этого файла (разрешён только здесь).
    Недоступность git → pytest.fail (R4: environmental absence = configuration error, не skip).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.md"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=30,
            check=False,
        )
    except OSError as exc:  # FileNotFoundError и пр. — git недоступен
        pytest.fail(f"[IMP:10][docs-forbidden][git] git ls-files недоступен: {exc}")

    if result.returncode != 0:
        pytest.fail(
            "[IMP:10][docs-forbidden][git] git ls-files завершился ошибкой "
            f"(rc={result.returncode}): {result.stderr.strip()}"
        )

    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    offenders = _check_md_allowlist(ROOT, tracked)

    logger.info("[IMP:8][docs-forbidden][allowlist] tracked .md: %d, offenders: %d", len(tracked), len(offenders))
    if offenders:
        for o in offenders:
            logger.error("[IMP:10][docs-forbidden][allowlist] RED: tracked .md вне allowlist: %s", o)
    assert not offenders, (
        "[IMP:10][docs-forbidden][allowlist] tracked .md вне allowlist (docs-in-code, инвариант №12):\n  "
        + "\n  ".join(offenders)
        + "\nДобавь легитимный файл в _ALLOWED_MD_PATTERNS (docs/** — НЕ добавлять; docs/ удаляется WD-5)."
    )
    logger.critical("[IMP:9][docs-forbidden][allowlist] PASS: все %d tracked .md в allowlist", len(tracked))


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · docs-dir-forbidden — детектор ловит docs/ в tmp_path
# · Scenario: docs/ создан во временном каталоге (tmp_path) → _find_docs_dir обязан вернуть True
# · Last fail: детектор-заглушка (no-op, всегда False) — gate всегда зелёный, инвариант без защиты
# · Remove if: инвариант №12 мигрирует из AGENTS.md в machine-readable SoT
def test_docs_dir_detector_negative(caplog, tmp_path: Path) -> None:
    """R5 anti-survivorship: _find_docs_dir ловит docs/ в tmp_path (логика, не fs репо)."""
    (tmp_path / "docs").mkdir()
    found = _find_docs_dir(tmp_path)
    logger.info("[IMP:8][docs-forbidden][negative] docs/ в tmp_path детектирован: %s", found)
    assert found, (
        "[IMP:10][docs-forbidden][negative] R5 FAIL: _find_docs_dir не поймал docs/ в tmp_path — "
        "детектор no-op, gate ложно-зелёный"
    )
    logger.critical("[IMP:9][docs-forbidden][negative] PASS: docs/ детектируется (tripwire активен)")


# endregion TESTS
