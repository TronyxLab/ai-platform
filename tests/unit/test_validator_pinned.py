# GREP_SUMMARY: validator-pinned fake-ajv python-draft7 detect-validator AI-0054 spec-row
# STRUCTURE: ▶ фейковый ajv в PATH (facts DI) → ◇ jsonschema доступен → ⎋ "python" (окружение не выбирает движок)
# region MODULE_CONTRACT
## @purpose  AI-0054 (DevPlan 17 T5.6, $TEST_SPEC row): валидатор схем pinned к
##           python-Draft7 — наличие ajv в окружении НЕ переключает движок.
##           Специфицированный артефакт $TEST_SPEC (сценарий также покрыт
##           test_validate_orchestrator.py::test_detect_validator_priority).
## @scope    tests/unit: facts-DI без subprocess.
## @invariants
##   - which('ajv') непустой → результат всё равно "python"
##   - jsonschema отсутствует + ajv есть → PlatformFatalError (ajv больше не fallback)
# endregion MODULE_CONTRACT

import logging

from core.internal.validate import validate_orchestrator

logger = logging.getLogger(__name__)


class _FakeFacts:
    """which-факты: ajv «установлен», остальное — как в реальном окружении."""

    def __init__(self, ajv_path: str | None) -> None:
        self._ajv_path = ajv_path

    def which(self, binary: str) -> str | None:
        if binary == "ajv":
            return self._ajv_path
        return f"/usr/bin/{binary}"


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · фейковый ajv игнорируется (AI-0054)
# · Regression: detect_validator выбирал ajv по наличию в PATH — один YAML валидировался
#   разными движками dev-vs-CI («единственная Draft7-точка» обходилась окружением)
# · Scenario: which('ajv') → /usr/local/bin/ajv + jsonschema доступен → "python"
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0054)
# · Remove if: появится явный config-выбор движка валидации
def test_fake_ajv_on_path_ignored() -> None:
    verdict = validate_orchestrator.detect_validator(
        facts=_FakeFacts(ajv_path="/usr/local/bin/ajv"),
        find_spec_fn=lambda name: object() if name == "jsonschema" else None,
    )
    print(f"[IMP:8][validator-pinned] verdict={verdict} при ajv в PATH")
    assert verdict == "python", "движок обязан быть pinned к python-Draft7 независимо от ajv"
    logger.critical("[IMP:9][test] fake ajv ignored, engine pinned — OK (AI-0054)")
