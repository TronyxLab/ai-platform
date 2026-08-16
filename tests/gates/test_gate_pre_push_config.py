# GREP_SUMMARY: pre-push, pass-filenames-false, quick-check, fan-out, OOM, pre-commit-config, R5-negative
# STRUCTURE: ▶ parse .pre-commit-config.yaml → ◇ pre-push-gate hook → ⊕ pass_filenames:false + always_run:true (иначе meltdown) → ◇ R5-negative (probe без pass_filenames → RED) → ⎋ PASS|FAIL
# region MODULE_CONTRACT
## @purpose  Gate: pre-push-gate hook обязан иметь pass_filenames: false (v1.0.1 0.8b).
##           TRAP[BUG] 2026-08-15: БЕЗ pass_filenames: false pre-commit фан-аутит хук по пачкам
##           staged-файлов (orphan-baseline ~2000 ADD → сотни параллельных инвокаций, каждая
##           гоняла вложенный pre-commit run --all-files → meltdown, 2 зависания macOS).
##           Хук — whole-tree quick check, файловые аргументы ему не нужны.
## @scope    Read-only статический анализ .pre-commit-config.yaml (+ probe для R5-негатива).
## @invariants
##   - pre-push-gate: pass_filenames: false И always_run: true — оба обязательны
##   - R5-negative: probe-конфиг БЕЗ pass_filenames: false → RED (детектор фальсифицируем)
## @rationale Первопричина двух OOM-зависаний (2026-08-14/15). Конфиг-регрессия (снятие
##            pass_filenames: false) = meltdown на крупном push — защита должна быть гейтом,
##            не комментарием (инвариант 2: защита в коде).
## @changes 2026-08-15 | Created (v1.0.1 0.8b — fan-out bug fix)
# endregion MODULE_CONTRACT

import logging
import pathlib

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
_PRECOMMIT_YML: pathlib.Path = ROOT / ".pre-commit-config.yaml"

_HOOK_ID = "pre-push-gate"


# region HELPER__find_hook
def _find_hook(config_path: pathlib.Path) -> dict | None:
    """Найти pre-push-gate hook в конфиге (или probe-файле); None если нет."""
    with pathlib.Path(config_path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    for repo in data.get("repos") or []:
        for hook in repo.get("hooks") or []:
            if hook.get("id") == _HOOK_ID:
                return hook
    return None


# endregion HELPER__find_hook


# 🧪 TRAP[TEST] · v1.0.1 0.8b · pre-push-gate обязан быть pass_filenames: false + always_run
# · Scenario: реальный .pre-commit-config.yaml → hook имеет оба флага; R5-negative —
# ·   probe без pass_filenames → RED (фальсифицируемость детектора)
# · Last fail: N/A (preventive — закрывает TRAP[BUG] fan-out 2026-08-15)
# · Remove if: pre-push-gate hook удалён из pre-commit
@pytest.mark.gate
def test_pre_push_gate_pass_filenames_false(caplog) -> None:
    """pre-push-gate hook обязан иметь pass_filenames: false + always_run: true (OOM-защита)."""
    caplog.set_level(logging.INFO)
    hook = _find_hook(_PRECOMMIT_YML)
    if hook is None:
        pytest.fail(f"{_PRECOMMIT_YML.relative_to(ROOT)}: hook '{_HOOK_ID}' не найден — quick check обязателен")
    assert hook.get("pass_filenames") is False, (
        "TRAP[BUG] 2026-08-15: БЕЗ pass_filenames: false pre-commit фан-аутит хук по пачкам файлов "
        "(сотни параллельных инвокаций → meltdown/OOM). Фикс: pass_filenames: false."
    )
    assert hook.get("always_run") is True, "always_run: true обязателен — хук должен исполняться всегда"
    assert hook.get("stages") == ["pre-push"], "stages: [pre-push] обязателен"
    logger.critical("[IMP:9][test] pre-push-gate: pass_filenames:false + always_run:true + stages:[pre-push]")


# 🧪 TRAP[TEST] · v1.0.1 0.8b · R5-negative: probe без pass_filenames → RED
# · Scenario: probe-конфиг с тем же hook БЕЗ pass_filenames: false → детектор ловит нарушение
# · Last fail: N/A
# · Remove if: предыдущий тест удалён
@pytest.mark.gate
def test_pre_push_gate_fan_out_negative_probe(caplog, tmp_path) -> None:
    """R5-negative: hook без pass_filenames: false должен детектироваться (фальсифицируемость)."""
    caplog.set_level(logging.INFO)
    probe = tmp_path / "pre-commit-probe.yaml"
    probe.write_text(
        yaml.safe_dump({
            "repos": [
                {
                    "repo": "local",
                    "hooks": [
                        {
                            "id": "pre-push-gate",
                            "name": "Quick check before push",
                            "entry": "core/entrypoints/pre-push-gate.sh",
                            "language": "script",
                            "stages": ["pre-push"],
                            "always_run": True,
                        }
                    ],
                }
            ]
        }),
        encoding="utf-8",
    )
    hook = _find_hook(probe)
    assert hook is not None, "probe-хук не распарсился"
    assert hook.get("pass_filenames") is not False, "детектор обязан ловить отсутствие pass_filenames: false"
    logger.critical("[IMP:9][test] R5-negative: probe без pass_filenames:false детектируется")
