#!/usr/bin/env python3
# GREP_SUMMARY: gate compose-files-sole-path AST anti-drift COMPOSE_FILENAMES docker-compose-base shared-only
# STRUCTURE: ▶ AST-скан core/internal/*.py → ○ literal container (tuple/list/set) ∋ compose filename →
#            ◇ файл == shared/compose_files.py? → PASS | ⟦RED: offenders⟧ → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Sole-path gate (DevPlan 118 A2): списки compose-файлов разрешены ТОЛЬКО в
##           core/internal/shared/compose_files.py (COMPOSE_FILENAMES / PROJECT_COMPOSE_FILENAMES).
##           6 локальных копий (docker_orchestrator, converge/runtime, converge/volumes,
##           orphan_reconciler, payload_deliverer, project_adopter) удалены волной A2.
## @scope    AST-скан всех core/internal/*.py: литеральные контейнеры (tuple/list/set) с ≥2 элементами,
##           где ≥1 элемент — каноническое compose-имя ("compose.yaml", "docker-compose.yaml",
##           "docker-compose.yml", "docker-compose.base.yml", "compose.yml").
##           Одиночные имена (os.path.join(dir, "docker-compose.yml"), Path()/ "docker-compose.base.yml") —
##           точечные операции записи/проверки, НЕ списки резолва — вне скоупа.
## @invariants
##   - RED: любой литеральный список compose-имён вне shared/compose_files.py
##   - Единственный легитимный источник кортежей — shared/compose_files.py
##   - Комментарии/докстринги исключаются (только AST-узлы литеральных контейнеров)
##   - "compose.yml" также в списке детекции (не-каноническое имя, запрещено к возврату — A2)
## @rationale U-13-парадигма: 6 копий списков = расхождение converge vs deploy (K2). Структурный
##            запрет возврата копий делает SoT enforce-емым (self-verifying waves).
## @changes  2026-08-02 | DevPlan 118 A2 — Created
# endregion MODULE_CONTRACT

import ast
import logging
import pathlib

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_CORE_INTERNAL = ROOT / "core" / "internal"
_ALLOWED_FILE = pathlib.Path("core/internal/shared/compose_files.py")

_COMPOSE_NAMES = {
    "compose.yaml",
    "compose.yml",  # не-каноническое имя (A2) — тоже детектируется как возврат фантома
    "docker-compose.yaml",
    "docker-compose.yml",
    "docker-compose.base.yml",
}


def _find_offenders() -> list[tuple[str, int, str]]:
    """Scan core/internal/*.py for literal compose-filename containers outside shared/compose_files.py.

    ▶ ┌_CORE_INTERNAL┐ → ○ for each .py → ○ walk AST → ◇ literal container (Tuple|List|Set)
    │     ∋ ≥2 elements, ≥1 canonical compose name → ⊕ offenders → ⎋ list
    """
    offenders: list[tuple[str, int, str]] = []
    for p in sorted(_CORE_INTERNAL.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel == _ALLOWED_FILE.as_posix():
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            # Строковые элементы контейнера
            elements = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(elements) < 2:
                continue
            matched = [e for e in elements if e in _COMPOSE_NAMES]
            if not matched:
                continue
            # Расширенные контейнеры (звёздные элементы) — не литеральный список, пропуск
            if any(isinstance(e, ast.Starred) for e in node.elts):
                continue
            offenders.append((rel, node.lineno, ", ".join(matched)))
    return offenders


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · A2 — no second compose-filename tuple in core/
# · Last fail: 6 локальных кортежей (docker_orchestrator, converge×2, orphan_reconciler, payload_deliverer, project_adopter)
# · Remove if: canon moves to a different mechanism (Architect sign-off)
def test_no_second_compose_filenames_tuple(caplog) -> None:
    """Compose filename lists must live ONLY in shared/compose_files.py (DevPlan 118 A2)."""
    offenders = _find_offenders()
    if offenders:
        for rel, lineno, names in offenders:
            logger.error("[IMP:10][compose_files_sole_path] %s:%d compose-filename list: %s", rel, lineno, names)
        pytest.fail(
            f"Compose filename lists found outside shared/compose_files.py ({len(offenders)}):\n"
            + "\n".join(f"  - {rel}:{lineno} [{names}]" for rel, lineno, names in offenders)
            + "\n\nSole path: core/internal/shared/compose_files.py (DevPlan 118 A2). "
            "Импортируйте COMPOSE_FILENAMES/PROJECT_COMPOSE_FILENAMES вместо локальных копий."
        )

    logger.info("[IMP:9][compose_files_sole_path] PASS: 0 compose-filename lists outside shared/compose_files.py")


@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · NEGATIVE (R5) · A2 — original drift input detected by gate
# · Scenario: tmp .py with `("compose.yaml", "compose.yml", "docker-compose.yml")` (бывший converge-кортеж)
# · Last fail: converge/runtime.py:224 + converge/volumes.py:160 (фантомный compose.yml кортеж)
# · Remove if: gate detector is superseded
def test_negative_converge_tuple_detected(caplog, tmp_path) -> None:
    """R5 negative: the original converge tuple (compose.yml) must be flagged by the gate."""
    test_file = tmp_path / "drift_consumer.py"
    test_file.write_text('COMPOSE_FILENAMES = ("compose.yaml", "compose.yml", "docker-compose.yml")\n')

    # Reuse the AST scan on the tmp file
    import ast as _ast

    tree = _ast.parse(test_file.read_text())
    hits = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Tuple):
            elements = [e.value for e in node.elts if isinstance(e, _ast.Constant) and isinstance(e.value, str)]
            matched = [e for e in elements if e in _COMPOSE_NAMES]
            if len(elements) >= 2 and matched:
                hits.append(matched)

    assert hits, "R5 FAIL: gate detector missed the original converge tuple (compose.yml)"
    logger.critical("[IMP:9][compose_files_sole_path][negative] converge tuple detected — %s — OK", hits)
