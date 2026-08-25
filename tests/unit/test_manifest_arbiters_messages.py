# GREP_SUMMARY: manifest-arbiters-messages arbiter-divide commit-vs-regenerate pytest-arbiter check-manifests-arbiter repair-message T2E DevPlan-16 R5-negative
# STRUCTURE: ▶ read оба арбитра-источника → ◇ строковые ассерты различителей вопросов → ⊕ R5-негатив (устаревшее сообщение детектируется) → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Контракт разведения арбитров манифестов (DevPlan 16 T2.E / проц.№1): два арбитра
##           отвечают на РАЗНЫЕ вопросы и их repair-сообщения обязаны вести к единственному
##           правильному действию:
##           - pytest-арбитр test_manifests_up_to_date: вопрос «дерево == HEAD» → COMMIT;
##           - check-suite арбитр manifest_driver check: вопрос «диск == генераторы» → REGENERATE.
## @scope    tests/unit: статические строковые ассерты по исходникам обоих арбитров + негатив.
## @invariants
##   - Сообщение pytest-арбитра содержит COMMIT-указание и явный запрет generate-manifests
##   - Recipe драйвера содержит Run: make generate-manifests
##   - Оба docstring несут фразы-различители вопросов
# endregion MODULE_CONTRACT

import logging

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_PYTEST_ARBITR = ROOT / "tests/gates/test_gate_manifests_up_to_date.py"
_SUITE_ARBITR = ROOT / "core/internal/scripts/manifest_driver.py"


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T2.E · pytest-арбитр ведёт к COMMIT
# · Last fail: аудит 15 проц.№1 — сообщение «Run: make generate-manifests» при divergence
#   «дерево != HEAD» вводило в заблуждение (регенерация no-op, нужен commit)
# · Scenario: исходник гейта содержит COMMIT-указание + явный запрет регенерации +
#   упоминание рукописной правки вне GENERATED-регионов
# · Remove if: арбитры консолидированы в один механизм с общим сообщением
def test_pytest_arbitr_message_commit() -> None:
    src = _PYTEST_ARBITR.read_text(encoding="utf-8")
    assert "СКОММИТИТЬ" in src and "git add" in src, "pytest-арбитр обязан вести к commit"
    assert "НЕ запускай make generate-manifests" in src, (
        "pytest-арбитр обязан явно запрещать регенерацию (её действие — парный арбитр)"
    )
    assert "GENERATED-регионов" in src, "случай рукописной правки вне GENERATED-регионов упомянут"
    assert "АРБИТР ВОПРОСА «дерево == HEAD»" in src, "docstring-различитель вопроса"
    logger.info("[IMP:9][arbiters][assert] pytest-арбитр: вопрос «дерево==HEAD», действие commit")


# 🧪 TRAP[TEST] · REGRESSION · DevPlan 16 T2.E · suite-арбитр ведёт к REGENERATE
# · Scenario: recipe manifest_driver содержит Run: make generate-manifests + предупреждение
#   о ручных правках; docstring несёт различитель «диск == генераторы»
# · Remove if: recipe переименован синхронно с этим тестом
def test_suite_arbitr_message_regenerate() -> None:
    src = _SUITE_ARBITR.read_text(encoding="utf-8")
    assert "Run: make generate-manifests" in src, "recipe обязан начинаться с регенерации"
    assert "АРБИТР ВОПРОСА «диск == генераторы»" in src, "docstring-различитель вопроса"
    assert "ВНЕ GENERATED-регионов" in src, "случай ручной правки упомянут в recipe"
    logger.info("[IMP:9][arbiters][assert] suite-арбитр: вопрос «диск==генераторы», действие regenerate")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · DevPlan 16 T2.E · неверное сообщение → RED
# · Last fail: аудит 15 проц.№1 — старое сообщение «Run: make generate-manifests» как
#   ЕДИНСТВЕННОЕ действие pytest-арбитра было самим дефектом
# · Scenario: simulated-сообщение со старой формой не проходит контрактный детектор
#   _is_commit_directed() (детектор = инвариант теста выше, вынесен для негатива)
# · Remove if: вместе с test_pytest_arbitr_message_commit
def test_wrong_arbitr_message_detected() -> None:
    def _is_commit_directed(msg_src: str) -> bool:
        """Контракт pytest-арбитра: commit-указание + запрет регенерации."""
        return "СКОММИТИТЬ" in msg_src and "НЕ запускай make generate-manifests" in msg_src

    old_defective_message = "Generated manifests are out of date.\nRun: make generate-manifests\n\nDiff output:\n..."
    assert not _is_commit_directed(old_defective_message), (
        "R5 FAIL: старое дефектное сообщение проходит контракт (детектор мёртв)"
    )
    logger.info("[IMP:9][arbiters][negative] устаревшее сообщение детектируется")
