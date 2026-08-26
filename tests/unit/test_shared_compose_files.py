"""
# GREP_SUMMARY: test-shared-compose-files, COMPOSE_FILENAMES, resolve-compose-file, requires-compose-project, A2, canon, docker-compose-base
# STRUCTURE: ▶ test_canonical_tuple (exact order, no compose.yml) → ◇ test_resolve_priority [compose.yaml|base.yml|none] →
# region MODULE_CONTRACT
## @purpose  Unit tests for shared/compose_files.py — единый SoT списков compose-файлов (DevPlan 118 A2).
## @scope    Verifies the canonical COMPOSE_FILENAMES tuple, resolve_compose_file priority order,
##           and resolve_compose_file semantics. No Docker required.
## @invariants
##   - Canonical order: compose.yaml → docker-compose.yaml → docker-compose.yml → docker-compose.base.yml
##   - compose.yml — НЕ канонический (0 модулей в ФС + git-истории)
##   - docker-compose.base.yml — последний в приоритете, НО резолвится (реальные модули — только base)
## @rationale DevPlan 118 A2: converge лечил фантомные имена (compose.yml), docker_orchestrator деплоил
##            base-compose — канон совмещает оба сценария. Тест фиксирует решение по открытому вопросу A2.
## @changes  2026-08-02 | DevPlan 118 A2 — Created
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import logging

import pytest

from core.internal.shared.compose_files import (
    COMPOSE_FILENAMES,
    PROJECT_COMPOSE_FILENAMES,
    resolve_compose_file,
)
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · A2 — canonical tuple exact order (open-question resolution)
# · Scenario: COMPOSE_FILENAMES == (compose.yaml, docker-compose.yaml, docker-compose.yml, docker-compose.base.yml);
# ·   compose.yml (не-канонический) отсутствует
# · Last fail: 6 расходящихся кортежей (docker_orchestrator, converge×2, orphan_reconciler, payload_deliverer, project_adopter)
# · Remove if: canon tuple is deliberately changed by Architect
@ldd_trajectory
def test_canonical_tuple_exact_order(caplog) -> None:
    """Canonical COMPOSE_FILENAMES order and composition (DevPlan 118 A2)."""
    caplog.set_level(logging.INFO)

    assert COMPOSE_FILENAMES == (
        "compose.yaml",
        "docker-compose.yaml",
        "docker-compose.yml",
        "docker-compose.base.yml",
    ), f"A2 FAIL: canonical tuple diverged: {COMPOSE_FILENAMES}"
    assert "compose.yml" not in COMPOSE_FILENAMES, (
        "A2 FAIL: compose.yml is non-canonical — 0 real modules with compose.yml (ФС core/modules/ + git-история пусты)"
    )
    assert "docker-compose.base.yml" in COMPOSE_FILENAMES, (
        "A2 FAIL: docker-compose.base.yml must be resolvable — реальные модули имеют ТОЛЬКО base-compose"
    )
    assert PROJECT_COMPOSE_FILENAMES == ("docker-compose.yml", "compose.yaml"), (
        f"A2 FAIL: payload compose subset diverged: {PROJECT_COMPOSE_FILENAMES}"
    )
    logger.critical("[IMP:9][test] canonical COMPOSE_FILENAMES order — %s — OK", COMPOSE_FILENAMES)


# 🧪 TRAP[TEST] · Regression · A2 — resolve_compose_file priority: compose.yaml wins
# · Scenario: dir with compose.yaml + docker-compose.base.yml → compose.yaml
# · Last fail: N/A (canon resolution)
# · Remove if: resolve_compose_file changes
@ldd_trajectory
def test_resolve_priority_compose_yaml_wins(caplog, tmp_path) -> None:
    """compose.yaml has priority over docker-compose.base.yml (canon order)."""
    caplog.set_level(logging.INFO)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    (tmp_path / "docker-compose.base.yml").write_text("services: {}\n")

    resolved = resolve_compose_file(str(tmp_path))

    assert resolved == tmp_path / "compose.yaml"
    logger.critical("[IMP:9][test] resolve priority — compose.yaml wins — OK")


# 🧪 TRAP[TEST] · Regression · A2 — resolve_compose_file finds docker-compose.base.yml (module case)
# · Scenario: dir with ONLY docker-compose.base.yml → resolves it (реальные модули — только base)
# · Last fail: converge использовал ("compose.yaml", "compose.yml", "docker-compose.yml") —
# ·   не видел base-compose → пропускал ВСЕ docker-модули как «not docker»
# · Remove if: module compose pattern changes
@ldd_trajectory
def test_resolve_finds_docker_compose_base_yml(caplog, tmp_path) -> None:
    """docker-compose.base.yml must resolve (real modules carry only base-compose)."""
    caplog.set_level(logging.INFO)
    (tmp_path / "docker-compose.base.yml").write_text("services: {}\n")

    resolved = resolve_compose_file(str(tmp_path))

    assert resolved == tmp_path / "docker-compose.base.yml"
    logger.critical("[IMP:9][test] resolve finds docker-compose.base.yml — OK")


# 🧪 TRAP[TEST] · Regression · A2 — resolve_compose_file returns None when no canonical file
# · Scenario: empty dir → None
# · Last fail: N/A
# · Remove if: resolve_compose_file changes
@ldd_trajectory
def test_resolve_none_when_missing(caplog, tmp_path) -> None:
    """Empty directory → resolve_compose_file returns None (not a docker module)."""
    caplog.set_level(logging.INFO)

    resolved = resolve_compose_file(str(tmp_path / "empty-module"))

    assert resolved is None
    logger.critical("[IMP:9][test] resolve missing → None — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A2 — compose.yml must NOT resolve (non-canonical name)
# · Scenario: dir with ONLY compose.yml → resolve_compose_file returns None (канон не резолвит фантом)
# · Last fail: converge лечил модули с compose.yml, которых никогда не существовало (0 в ФС+git)
# · Remove if: compose.yml is re-added to canon by Architect
@ldd_trajectory
def test_compose_yml_non_canonical_negative(caplog, tmp_path) -> None:
    """R5 negative: compose.yml must NOT be resolved (non-canonical, removed in A2)."""
    caplog.set_level(logging.INFO)
    (tmp_path / "compose.yml").write_text("services: {}\n")

    resolved = resolve_compose_file(str(tmp_path))

    assert resolved is None, (
        "A2 FAIL: compose.yml must not resolve — 0 реальных модулей с этим именем (фантом converge)"
    )
    logger.critical("[IMP:9][test] compose.yml non-canonical → None — OK")
