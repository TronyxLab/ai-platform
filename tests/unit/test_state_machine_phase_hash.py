# GREP_SUMMARY: state-machine-phase-hash phases-invalidation AI-0038 needs-rerun stability sha256 tmp-phases-dir
# STRUCTURE: ▶ tmp phases dir ┌a.py b.py┐ → hash H1 → mutate a.py bytes → hash H2≠H1 (needs_rerun) │ unchanged dir → H1==H1' (replay идемпотентен)
# region MODULE_CONTRACT
## @purpose  AI-0038 (DevPlan 17 T1.4): _phase_input_hash обязан включать байты
##           lifecycle/phases/*.py — правка кода фазы инвалидирует done-фазу;
##           неизменённое дерево даёт стабильный hash (replay идемпотентен).
## @scope    tests/unit: DI phases_dir=tmp_path; без записей в реальное дерево репо.
## @invariants
##   - Мутация байт одного phases/*.py → другой hash
##   - Повторный вызов на неизменённом дереве → тот же hash
##   - Отсутствие phases-dir → детерминированный fallback, не исключение
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path

from core.internal.bootstrap.lifecycle.state_machine import StateMachine

logger = logging.getLogger(__name__)


def _make_phases_dir(tmp_path: Path) -> Path:
    """tmp-аналог lifecycle/phases с двумя .py (включая __init__.py)."""
    phases = tmp_path / "phases"
    phases.mkdir()
    (phases / "__init__.py").write_text("# package\n", encoding="utf-8")
    (phases / "docker.py").write_text("PHASE_CODE = 1\n", encoding="utf-8")
    (phases / "system.py").write_text("PHASE_CODE = 2\n", encoding="utf-8")
    return phases


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · правка кода фаз инвалидирует phase-hash (AI-0038)
# · Regression: хэшировались только node.yaml поля + state_machine.py → правки
#   lifecycle/phases/*.py НЕ инвалидирали done-φ8/φ11/φ12/φ13 на bootstrap/update
# · Scenario: hash(tmp phases) → мутация байта docker.py → hash другой;
#   повторный вызов без изменений → hash тот же; отсутствие dir → fallback без raise
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0038)
# · Remove if: phase-инвалидация переезжает в отдельный механизм (например, content_hash CLI)
def test_phase_code_edit_invalidates(tmp_path: Path) -> None:
    """Мутация phases/*.py меняет hash; стабильность на неизменённом дереве."""
    phases = _make_phases_dir(tmp_path)

    h1 = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=phases)
    h1_again = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=phases)
    print(f"[IMP:8][hash] unchanged-tree stability: {h1[:12]} == {h1_again[:12]}")
    assert h1 == h1_again, "неизменённое дерево обязано давать стабильный hash (replay идемпотентен)"

    # Мутация байт одной фазы → hash меняется
    (phases / "docker.py").write_text("PHASE_CODE = 1  # edited\n", encoding="utf-8")
    h2 = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=phases)
    print(f"[IMP:8][hash] after mutation: {h2[:12]}")
    assert h1 != h2, "правка кода фазы обязана менять phase-hash (AI-0038)"

    # Добавление НОВОГО файла фазы тоже инвалидирует
    (phases / "extra_phase.py").write_text("PHASE_CODE = 3\n", encoding="utf-8")
    h3 = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=phases)
    assert h3 != h2, "новый файл в phases/ обязан менять hash"

    # Отсутствующий phases-dir → детерминированный fallback, не падает
    missing = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=tmp_path / "nope")
    missing_again = StateMachine._phase_input_hash("deploy_services", env={}, phases_dir=tmp_path / "nope")
    assert missing == missing_again, "fallback отсутствующего dir детерминирован"
    logger.critical("[IMP:9][test] phase-code edit invalidates hash, replay stable — OK (AI-0038)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · дефолтный phases_dir резолвится рядом с __file__ (back-compat)
# · Regression: DI-параметр не должен менять прод-поведение (None → lifecycle/phases модуля)
# · Scenario: два вызова без phases_dir → равные hash; hash с реальным phases-dir совпадает
#   с дефолтным (резолв того же каталога)
# · Last fail: охранник DI-рефакторинга T1.4 (DevPlan 17)
# · Remove if: сигнатура _phase_input_hash меняется несовместимо
def test_default_phases_dir_is_module_relative() -> None:
    default_h1 = StateMachine._phase_input_hash("deploy_update")
    default_h2 = StateMachine._phase_input_hash("deploy_update")
    assert default_h1 == default_h2, "дефолтный вызов стабилен"

    module_file = sys.modules[StateMachine.__module__].__file__
    assert module_file is not None, "модуль state_machine обязан иметь __file__"
    module_dir = Path(module_file).resolve().parent
    explicit_h = StateMachine._phase_input_hash("deploy_update", phases_dir=module_dir / "phases")
    assert explicit_h == default_h1, "DI None обязан резолвиться в lifecycle/phases модуля"
    logger.critical("[IMP:9][test] default phases dir == module-relative — OK (T1.4)")
