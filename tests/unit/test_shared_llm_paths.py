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

import pytest

from core.internal.shared.llm_paths import litellm_config_path, litellm_template_path

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · path-резолверы — единые пути (C6) + str-вход (параметризовано, F5)
# · Scenario: core_dir → канонический путь; str core_dir → Path результат
# · Last fail: 4 копии пути (context_deployer, deploy_orchestrator, llm_provision, phases)
# · Remove if: path-резолверы удалены
@pytest.mark.parametrize(
    ("resolver", "core_dir", "expected"),
    [
        (
            litellm_config_path,
            "/opt/platform/core",
            __import__("pathlib").Path("/opt/platform/core/modules/litellm/config/litellm-config.yml"),
        ),
        (
            litellm_template_path,
            "/opt/platform/core",
            __import__("pathlib").Path("/opt/platform/core/modules/litellm/config/litellm-config.yml.j2"),
        ),
        (litellm_config_path, "x", __import__("pathlib").Path("x/modules/litellm/config/litellm-config.yml")),
    ],
)
def test_litellm_path_resolvers(resolver, core_dir, expected) -> None:
    """litellm path-резолверы: единый путь вывода/шаблона (AC-C6), str core_dir (str | Path)."""
    p = resolver(core_dir)
    assert p == expected
    logger.info("[IMP:9][test] resolver=%s core_dir=%s path=%s", resolver.__name__, core_dir, p)
