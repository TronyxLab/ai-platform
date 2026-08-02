#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-llm-paths litellm-config path resolver unit C6
# STRUCTURE: ▶ test_litellm_config_path (core_dir → path) → test_litellm_template_path → test_no_string_concat
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/llm_paths.py — единый путь litellm-config.yml (DevPlan 118 C6).
## @scope    Tests: litellm_config_path(), litellm_template_path(). Чистые резолверы — no I/O.
## @invariants
##   - litellm_config_path(core_dir) = <core_dir>/modules/litellm/config/litellm-config.yml
##   - litellm_template_path(core_dir) = <core_dir>/modules/litellm/config/litellm-config.yml.j2
## @rationale DevPlan 118 C6 §TEST — unit: единый путь; grep-гейт «1 источник пути» (см. gates).
## @changes 2026-08-02 | DevPlan 118 C6 — created
# endregion MODULE_CONTRACT

import logging

from core.internal.shared.llm_paths import litellm_config_path, litellm_template_path

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · litellm_config_path — единый путь вывода (C6)
# · Scenario: core_dir → <core_dir>/modules/litellm/config/litellm-config.yml
# · Last fail: 4 копии пути (context_deployer, deploy_orchestrator, llm_provision, phases)
# · Remove if: litellm_config_path resolver removed
def test_litellm_config_path() -> None:
    """litellm_config_path — единый путь вывода litellm-config.yml (AC-C6)."""
    p = litellm_config_path("/opt/platform/core")
    assert p == __import__("pathlib").Path("/opt/platform/core/modules/litellm/config/litellm-config.yml")
    logger.info("[IMP:9][test] litellm_config_path=%s", p)


# 🧪 TRAP[TEST] · Regression · litellm_template_path — единый путь шаблона (C6)
# · Scenario: core_dir → <core_dir>/modules/litellm/config/litellm-config.yml.j2
# · Last fail: config_renderer._TEMPLATE_REL_PATH (5-я копия)
# · Remove if: litellm_template_path resolver removed
def test_litellm_template_path() -> None:
    """litellm_template_path — единый путь Jinja2-шаблона (AC-C6)."""
    import pathlib

    p = litellm_template_path("/opt/platform/core")
    assert p == pathlib.Path("/opt/platform/core/modules/litellm/config/litellm-config.yml.j2")
    logger.info("[IMP:9][test] litellm_template_path=%s", p)


# 🧪 TRAP[TEST] · Regression · str input тоже работает (str | Path)
# · Scenario: str core_dir → Path результат
# · Last fail: N/A (C6 unit)
# · Remove if: сигнатура резолверов меняется
def test_accepts_str_core_dir() -> None:
    """Резолверы принимают str core_dir (потребители передают os.path.join строки)."""
    assert litellm_config_path("x") == __import__("pathlib").Path("x/modules/litellm/config/litellm-config.yml")
