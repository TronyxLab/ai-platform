"""
# GREP_SUMMARY: test-practices-escalator, states, baseline, proposed, active-full, no-autopromote, force, downgrade-audit, warning-format, PracticesState, EscalatorDecision
# STRUCTURE: ▶ evaluate(maturity, level, lock) → ◇ force baseline/full → ◇ auto fresh → BASELINE → ◇ auto mature → PROPOSED + [PRACTICES:PROPOSE] → ◇ lock stable (proposed/active-full, NO autopromote) → ◇ set-practices baseline → откат + аудит (from/to/reason) → ⎋ asserts + IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/practices/escalator.py + set_practices-аудит (DevPlan 137 W3
##           §5 задача 2): 3 состояния (baseline|proposed|active-full), форс level=baseline|full,
##           level=auto → maturity решает (age>30 ∨ files>50 → proposed + warning [PRACTICES:PROPOSE]),
##           БЕЗ автопромоута (R5-negative: active-full НЕ достижим автоматически из proposed),
##           ручной откат set-practices baseline → аудит-запись (audit_logger, from/to/reason §4.6).
## @scope    $TEST_SPEC 137 W3: test_practices_escalator (3 состояния, форс, откат с аудитом,
##           отсутствие автопромоута, warning-формат).
## @invariants
##   - Native imports; evaluate() — чистая функция (пороги из канона, НЕ хардкод)
##   - Для отката с аудитом: mock-проект (ai-platform.yaml + git) + monkeypatch audit_logger
##     (реальный writer пишет в /var/log/platform — недоступно на dev-машине)
##   - PracticesLock строится через dataclass (generators) — не фикстуры с диска
##   - LDD: IMP:9-траектория через caplog (evaluate/set_practices логируют IMP:9)
##   - R5: negative-тест отсутствия автопромоута (proposed + зрелость → НЕ active-full)
## @rationale  AC W3: эскалатор — «плавное включение»; отсутствие автопромоута — решение
##             пользователя 2026-08-05; переходы аудируются (audit_logger единый writer).
## @changes  2026-08-05 · DevPlan 137 W3 — создан
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.practices.escalator import (
    LEVEL_AUTO,
    LEVEL_BASELINE,
    LEVEL_FULL,
    PracticesState,
    evaluate,
)
from core.internal.practices.generators import PracticesLock, read_lock
from core.internal.practices.maturity import Maturity
from core.internal.practices.set_practices import set_practices
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.project_yaml import load_project_yaml
from tests.conftest import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER_mock_project
def _make_mock_project(tmp_path: Path) -> Path:
    """Создать минимальный backend-проект (ai-platform.yaml + src + git commit «сейчас»)."""
    project = tmp_path / "mockproj"
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        "name: mockproj\ntype: backend\ntarget_node: test-node\n", encoding="utf-8"
    )
    src = project / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "# GREP_SUMMARY: mock, app\n\n\n"
        '"""Mock app (test fixture)."""\n\n\n'
        "def main() -> None:\n"
        '    """Run mock app."""\n'
        '    print("mock app")\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "-m",
            "init: mock",
            "--no-gpg-sign",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    return project


def _lock_with_state(state: str) -> PracticesLock:
    """PracticesLock с заданным state (для тестов стабильности/terminal)."""
    return PracticesLock(
        version=1,
        level="auto",
        state=state,
        language="backend",
        generator_hash="sha256:test",
        maturity={"age_days": 999, "code_files": 999},
        files={},
        generated_at="2026-08-05T03:00:00Z",
    )


# endregion HELPER_mock_project


# 🧪 TRAP[TEST] · 2026-08-05 · unit · 3 состояния эскалатора (явно, для аудита/тестируемости)
# · Regression: shadow-full/proposed объединены; автопромоут отклонён → ровно 3 состояния §4.2
# · Last fail: N/A
# · Remove if: state-машина пересматривается
# GUARD-PRESERVE (168): единственное покрытие enum PracticesState (ровно baseline|proposed|active-full) —
# контракт state-машины §4.2, база для всех переходов эскалатора
def test_escalator_three_states() -> None:
    """PracticesState содержит ровно baseline|proposed|active-full."""
    assert {s.value for s in PracticesState} == {"baseline", "proposed", "active-full"}


# 🧪 TRAP[TEST] · 2026-08-05 · unit · форс level=baseline (откат-форс, независимо от maturity)
# · Regression: baseline-форс — единственный ручной откат из active-full (§4.6)
# · Last fail: N/A
# · Remove if: таблица переходов §4.6 меняется
def test_escalator_force_baseline(caplog: pytest.LogCaptureFixture) -> None:
    """level=baseline → FORCED baseline; warning=None даже при зрелости 999/999."""
    with caplog.at_level(logging.INFO):
        decision = evaluate(Maturity(999, 999), LEVEL_BASELINE, None)
    assert decision.state == PracticesState.BASELINE
    assert decision.reason == "manual: baseline"
    assert decision.warning is None
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога evaluate (force baseline)"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · форс level=full → active-full (согласие пользователя)
# · Regression: active-full ТОЛЬКО по явному set-practices full (автопромоута нет)
# · Last fail: N/A
# · Remove if: таблица переходов §4.6 меняется
def test_escalator_force_full(caplog: pytest.LogCaptureFixture) -> None:
    """level=full → FORCED active-full; warning=None (информация, не предложение)."""
    with caplog.at_level(logging.INFO):
        decision = evaluate(Maturity(0, 0), LEVEL_FULL, None)
    assert decision.state == PracticesState.ACTIVE_FULL
    assert decision.reason == "manual: full"
    assert decision.warning is None
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога evaluate (force full)"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · level=auto, свежий проект → baseline без варнингов
# · Regression: AC W3 — мок-проект (3 файла) ведёт себя как baseline (эскалатор жив)
# · Last fail: N/A
# · Remove if: семантика auto меняется
def test_escalator_auto_fresh_baseline(caplog: pytest.LogCaptureFixture) -> None:
    """auto + молодой проект (3 дня, 3 файла) → baseline, без warning."""
    with caplog.at_level(logging.INFO):
        decision = evaluate(Maturity(3, 3), LEVEL_AUTO, None)
    assert decision.state == PracticesState.BASELINE
    assert decision.reason == "fresh"
    assert decision.warning is None
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога evaluate (auto fresh)"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · level=auto, зрелый проект → proposed + [PRACTICES:PROPOSE]
# · Regression: AC W3 — возраст>30 ∨ файлы>50 → предложение full (non-blocking)
# · Last fail: N/A
# · Remove if: пороги/поведение proposed меняется
def test_escalator_auto_mature_proposed(caplog: pytest.LogCaptureFixture) -> None:
    """auto + зрелый проект (41 день, 87 файлов) → proposed + warning [PRACTICES:PROPOSE]."""
    with caplog.at_level(logging.INFO):
        decision = evaluate(Maturity(41, 87), LEVEL_AUTO, None)
    assert decision.state == PracticesState.PROPOSED
    assert decision.reason == "age=41d,files=87"
    assert decision.warning is not None and decision.warning.startswith("[PRACTICES:PROPOSE]")
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога evaluate (auto proposed)"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · warning-формат [PRACTICES:PROPOSE] (единый, §4.3)
# · Regression: каналы доставки (AI-PLATFORM.md/pre-push/CI/verify) парсят единый формат
# · Last fail: N/A
# · Remove if: §4.3 варнинг-формат меняется
# GUARD-PRESERVE (168): единственное покрытие ТОЧНОГО формата warning §4.3
# ("[PRACTICES:PROPOSE][level:full][reason:...]" + RECOMMEND-строка) — парсится каналами доставки
def test_escalator_propose_warning_format() -> None:
    """warning: '[PRACTICES:PROPOSE][level:full][reason:...]' + RECOMMEND-строка."""
    decision = evaluate(Maturity(41, 87), LEVEL_AUTO, None)
    assert decision.warning == (
        "[PRACTICES:PROPOSE][level:full][reason:age=41d,files=87]"
        "\n>>> RECOMMEND: make project-set-practices full (или make project-sync-practices для обновления канона)"
    )


# 🧪 TRAP[TEST] · 2026-08-05 · unit · NEGATIVE (R5): автопромоута НЕТ — proposed не становится active-full
# · Regression: решение пользователя 2026-08-05 «варнинга хватит» — даже при зрелости 999/999
#   proposed стабилен до ручного действия; active-full достижим ТОЛЬКО set-practices full
# · Last fail: N/A (negative-тест на отсутствие автопромоута)
# · Remove if: автопромоут пересматривается пользователем
def test_escalator_no_autopromote_from_proposed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """NEGATIVE: auto + максимальная зрелость + lock.state=proposed → ОСТАЁТСЯ proposed."""
    with caplog.at_level(logging.INFO):
        decision = evaluate(Maturity(999, 999), LEVEL_AUTO, _lock_with_state("proposed"))
    assert decision.state == PracticesState.PROPOSED, "автопромоута НЕТ: proposed не переходит в active-full сам"
    assert decision.warning is not None  # варнинг-предложение остаётся (не silent)
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога evaluate (no-autopromote)"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · auto + active-full — терминально (только ручной откат)
# · Regression: active-full не сбрасывается автоматически при падении зрелости
# · Last fail: N/A
# · Remove if: таблица переходов §4.6 меняется
def test_escalator_auto_terminal_active_full() -> None:
    """auto + lock.state=active-full → стабильно active-full (terminal, ручной откат)."""
    decision = evaluate(Maturity(1, 1), LEVEL_AUTO, _lock_with_state("active-full"))
    assert decision.state == PracticesState.ACTIVE_FULL
    assert decision.warning is None


# 🧪 TRAP[TEST] · 2026-08-05 · unit · proposed стабилен до ручного действия (maturity упала)
# · Regression: proposed не откатывается автоматически в baseline (риск: тихий downgrade)
# · Last fail: N/A
# · Remove if: стабильность proposed пересматривается
def test_escalator_proposed_stable_when_maturity_drops() -> None:
    """auto + lock.state=proposed + низкая maturity → остаётся proposed (до ручного действия)."""
    decision = evaluate(Maturity(2, 2), LEVEL_AUTO, _lock_with_state("proposed"))
    assert decision.state == PracticesState.PROPOSED
    assert decision.warning is not None


# 🧪 TRAP[TEST] · 2026-08-05 · unit · невалидный level → ConfigValidationError (fail-fast, exit 4)
# · Regression: bare ValueError запрещён в core (гейт no_bare_raise) — типизированная ошибка
# · Last fail: N/A
# · Remove if: валидация level переносится
def test_escalator_invalid_level_raises() -> None:
    """level='bogus' → ConfigValidationError (exit 4 семантика), не тихий fallback."""
    with pytest.raises(ConfigValidationError):
        evaluate(Maturity(0, 0), "bogus", None)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · ручной откат set-practices baseline → state=baseline + аудит-запись
# · Regression: AC W3 — откат форсирует baseline независимо от maturity; переход аудируется
#   (event=practices_state_transition from=<prev> to=baseline reason=manual:baseline, §4.6)
# · Last fail: N/A
# · Remove if: формат аудита переходов меняется
def test_escalator_downgrade_audit(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """set-practices full → active-full; set-practices baseline → откат + аудит from/to/reason."""
    entries: list[dict] = []

    def fake_write(tag: str, status: str, message: str, **extra) -> bool:
        entries.append({"tag": tag, "status": status, "message": message, **extra})
        return True

    project = _make_mock_project(tmp_path)

    # ── upgrade: fresh → full (согласие) ──
    with caplog.at_level(logging.INFO):
        report_up = set_practices(project, "full", audit_writer=fake_write)
    assert report_up.sync.state == "active-full"
    assert read_lock(project).state == "active-full"
    assert entries[-1]["tag"] == "practices_state_transition"
    assert entries[-1]["to"] == "active-full"

    # ── downgrade: active-full → baseline (ручной откат-форс) ──
    with caplog.at_level(logging.INFO):
        report_down = set_practices(project, "baseline", audit_writer=fake_write)
    assert report_down.sync.state == "baseline"

    lock = read_lock(project)
    assert lock.state == "baseline"
    assert lock.level == "baseline"
    data = load_project_yaml(project)
    assert data["quality"]["level"] == "baseline"

    last = entries[-1]
    assert last["tag"] == "practices_state_transition"
    assert last["from"] == "active-full"
    assert last["to"] == "baseline"
    assert last["reason"] == "manual:baseline"
    assert last["project"] == "mockproj"
    assert last["level"] == "baseline"

    # upgrade-запись: from=none (lock отсутствовал до первого sync) → active-full
    assert entries[0]["from"] == "none"
    assert entries[0]["to"] == "active-full"

    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога set_practices"
