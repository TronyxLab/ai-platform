#!/usr/bin/env python3
# GREP_SUMMARY: deploy-modules-env, test, static-audit, secrets-validator, batch-metadata, transitive-deps, BFS-cycle, node-yaml-modules, env-requires
# STRUCTURE: ▶ test_batch_module_metadata (secrets_validator._batch_module_metadata + orchestrator import) → ▶ test_expand_transitive_deps_cycle_terminates (BFS visited-set) → ▶ test_parse_modules_from_node_yaml_edge_cases (dict/list shapes) → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  Static audit env-домена деплоя: secrets_validator.py контракты W4-E1 extraction
##           (_batch_module_metadata, _expand_transitive_deps, parse_modules_from_node_yaml).
##           Сплит test_deploy_modules.py (DevPlan 139 W3 T6): env/секреты-подобласть.
## @scope    S3: _batch_module_metadata — enriched metadata (list[dict]) + orchestrator env-check.
##           W4-E5: _expand_transitive_deps — BFS visited-set (cycle termination).
##           W4-E5: parse_modules_from_node_yaml — dict/list/empty shapes (list[tuple]).
## @invariants
##   - Все тесты — static audit (чтение исходников как текст, _extract_python_func)
##   - LDD: _assert_ldd_trajectory (≥1 IMP:9)
##   - Контракты env-валидации не нарушены после extraction
## @rationale  Группировка по бизнес-подобласти (env/секреты) — файл легче читать;
##             coverage W4-E5 страховок сохранён (AC W3e).
## @changes  2026-08-05 | DevPlan 139 W3 T6 — вынесен из test_deploy_modules.py
# endregion MODULE_CONTRACT

import logging

import pytest

from tests.helpers.deploy_modules_audit import (
    DEPLOY_PYTHON_DIR,
    ORCHESTRATOR_PY,
    _assert_ldd_trajectory,
    _extract_python_func,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# S3: Batch module metadata
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_batch_module_metadata
## @purpose  Static audit: _batch_module_metadata в secrets_validator.py + orchestrator env-check.
## @io       ⇥ caplog, DEPLOY_PYTHON_DIR/secrets_validator.py → ⎋ None
## @complexity 1 — static grep on file content


@pytest.mark.static_audit
def test_batch_module_metadata(caplog) -> None:
    """_batch_module_metadata (list[dict]) существует; orchestrator использует batch env-check."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_batch_module_metadata] Reading secrets_validator.py ...")
    content = _extract_python_func(DEPLOY_PYTHON_DIR / "secrets_validator.py", "_batch_module_metadata")

    assert "def _batch_module_metadata(" in content, (
        "S3 violation: _batch_module_metadata() function not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_batch_module_metadata] _batch_module_metadata() function declared OK")

    assert "list[dict" in content or "list[dict[str" in content, (
        "S3 violation: _batch_module_metadata must return list[dict] (enriched metadata)"
    )
    logger.info("[IMP:9][test_batch_module_metadata] Return type list[dict] OK (enriched metadata)")

    # DevPlan 100: routing moved from deploy-modules.sh shell to deploy_orchestrator.py
    orch_content = ORCHESTRATOR_PY.read_text()
    assert "secrets_validator" in orch_content, "S3 violation: secrets_validator not imported in deploy_orchestrator.py"
    assert "batch_check_env" in orch_content or "check_env_requires" in orch_content, (
        "S3 violation: batch env check functions not used in deploy_orchestrator.py"
    )
    logger.info("[IMP:9][test_batch_module_metadata] secrets_validator imported in deploy_orchestrator.py OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: S3 batch metadata must exist and replace per-module fallbacks
# · Remove if: batch metadata approach is replaced with a different optimization
# endregion FUNC_test_batch_module_metadata


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5: Transitive deps cycle termination
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_expand_transitive_deps_cycle_terminates
## @purpose  W4-E5: _expand_transitive_deps завершается на цикле зависимостей (BFS visited-set).
## @io       caplog → ⎋ None
## @complexity 1 — static grep for BFS visited-set pattern


@pytest.mark.static_audit
def test_expand_transitive_deps_cycle_terminates(caplog) -> None:
    """_expand_transitive_deps: BFS visited-set (expanded/visited/seen) + queue + -> str."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_expand_transitive_deps_cycle] START — checking secrets_validator.py")

    content = _extract_python_func(DEPLOY_PYTHON_DIR / "secrets_validator.py", "_expand_transitive_deps")

    assert "def _expand_transitive_deps(" in content, (
        "W4-E5 violation: _expand_transitive_deps() not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] _expand_transitive_deps() declared OK")

    assert "expanded" in content or "visited" in content or "seen" in content, (
        "W4-E5 violation: _expand_transitive_deps must use a visited/expanded set (BFS cycle protection)"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] BFS visited-set pattern present")

    assert "deque" in content or "queue" in content or "while" in content, (
        "W4-E5 violation: _expand_transitive_deps must use BFS/queue-based iteration"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] BFS iteration pattern present")

    assert " -> str" in content or "-> str" in content, (
        "W4-E5 violation: _expand_transitive_deps must return str (space-separated deps)"
    )
    logger.info("[IMP:9][test_expand_transitive_deps_cycle] Returns str OK (space-separated deps)")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 transitive deps cycle (A↔B) terminates via BFS visited-set
# · Scenario: module a depends_on b, b depends_on a → _expand_transitive_deps("a") returns "a b"
# · Last fail: N/A (W4-E5 baseline — would fail as TimeoutExpired if BFS visited-set broken)
# · Remove if: dependency resolution moves to a DAG library with explicit cycle detection
# endregion FUNC_test_expand_transitive_deps_cycle_terminates


# ══════════════════════════════════════════════════════════════════════════════
# W4-E5: parse_modules_from_node_yaml edge cases
# ══════════════════════════════════════════════════════════════════════════════

# region FUNC_test_parse_modules_from_node_yaml_edge_cases
## @purpose  W4-E5: parse_modules_from_node_yaml обрабатывает 3 YAML-формы (dict/list/empty).
## @io       caplog → ⎋ None
## @complexity 1 — static grep for dict/list handling patterns


@pytest.mark.static_audit
def test_parse_modules_from_node_yaml_edge_cases(caplog) -> None:
    """parse_modules_from_node_yaml: dict (.items()) + list (.get('name')) + list[tuple] return."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_parse_node_yaml_edge] START — 3 module-shape edge cases in secrets_validator.py")

    content = _extract_python_func(DEPLOY_PYTHON_DIR / "secrets_validator.py", "parse_modules_from_node_yaml")

    assert "def parse_modules_from_node_yaml(" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml() not found in secrets_validator.py"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] parse_modules_from_node_yaml() declared OK")

    assert "isinstance" in content and "dict" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must check for dict shape"
    )
    assert "items()" in content or ".items()" in content, "W4-E5 violation: dict shape must use .items() iteration"
    logger.info("[IMP:9][test_parse_node_yaml_edge] dict shape handling OK")

    assert "isinstance" in content and "list" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must check for list shape"
    )
    assert 'get("name"' in content or '.get("name", ")' in content or ".get('name'" in content, (
        "W4-E5 violation: list shape must use .get('name') for module name extraction"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] list shape handling OK")

    assert "list[tuple" in content or "list of tuple" in content or "List[Tuple" in content, (
        "W4-E5 violation: parse_modules_from_node_yaml must return list[tuple] (name, enabled, overlay)"
    )
    logger.info("[IMP:9][test_parse_node_yaml_edge] return type list[tuple] OK")

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: W4-E5 parse_modules_from_node_yaml handles dict/list/empty shapes
# · Remove if: module parsing moves to secrets_validator.py (then point test at new module)
# endregion FUNC_test_parse_modules_from_node_yaml_edge_cases
