# GREP_SUMMARY: test-render-monitoring-cli contract make render-monitoring PROJECT_DIR PROJECT argparse main monkeypatch
# STRUCTURE: ▶ main() ← monkeypatch sys.argv → ◇ валидные --project-dir/--project → exit 0 → ◇ отсутствие аргументов → SystemExit(2) → ⎋ контракт make-таргета
# region MODULE_CONTRACT
## @purpose  Контрактный CLI-тест для make render-monitoring (DevPlan 116 B7 T7, U-65 / D5):
##           main() рендерера с --project-dir/--project — exit 0 на валидном конфиге,
##           SystemExit(2) от argparse на отсутствии обязательных аргументов.
## @scope    НЕ запускает сервер, НЕ subprocess — прямой вызов функций (правило UI-тестов),
##           main() с monkeypatch sys.argv (правило testing.md: без subprocess для бизнес-логики).
## @invariants
##   - tmp_path вместо хардкод-путей
##   - Проект без monitoring-секции → exit 0 (backward compat, non-fatal)
##   - Отсутствие PROJECT_DIR/PROJECT → argparse SystemExit(2) (fail-fast контракт make-таргета)
##   - NODE необязателен (default "")
## @rationale Регистрация render-monitoring в Makefile требует проверяемого контракта exit-кодов:
##   make-таргет вызывает python3 renderer; отсутствие аргументов должно FAIL (не тихий no-op).
##   Заменяет R1-нарушающие pass-тесты (assert True/pass) в test_monitoring_config_renderer.py.
## @changes 2026-08-01 | Created (DevPlan 116 B7 T7, D5)
# endregion MODULE_CONTRACT

import logging
import sys

import pytest

from core.internal.monitoring_config_renderer import main

logger = logging.getLogger(__name__)


def _print_ldd_trajectory(caplog, test_name: str) -> bool:
    """Print IMP:7-10 LDD trajectory from caplog and return whether IMP:9+ was found.

    ## @purpose  Centralised LDD trajectory printer per RULES.md §TESTING.
    ## @io       ⇥ caplog, test_name → ⎋ bool (IMP:9+ found)
    ## @complexity O(N) — N caplog records
    """
    found = False
    print(f"\n--- LDD TRAJECTORY ({test_name}) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            imp_str = record.message.split("[IMP:")[1].split("]")[0]
            try:
                imp_level = int(imp_str)
            except ValueError:
                continue
            if imp_level >= 7:
                print(record.message)
            if imp_level >= 9:
                found = True
    print("--- END LDD TRAJECTORY ---")
    return found


# 🧪 TRAP[TEST] · render_monitoring_cli_valid · Contract · Regression: make render-monitoring exit code
# · Scenario: main([--project-dir, --project]) на проекте без monitoring → exit 0 (backward compat)
# · Last fail: N/A (новый контрактный тест, DevPlan 116 B7 T7)
# · Remove if: make render-monitoring удаляется из Makefile/манифеста
def test_render_monitoring_cli_valid_project(tmp_path, monkeypatch, caplog) -> None:
    """main() с валидными --project-dir/--project → exit 0 (даже без monitoring-секции)."""
    caplog.set_level(logging.INFO)

    # Проект без ai-platform.yaml — backward compat: build_merged_config → None → exit 0
    project_dir = tmp_path / "empty-project"
    project_dir.mkdir()

    monkeypatch.setattr(
        sys,
        "argv",
        ["monitoring_config_renderer.py", "--project-dir", str(project_dir), "--project", "test-app"],
    )

    result = main()

    assert result == 0, f"main() should exit 0 for valid project (no monitoring), got {result}"

    found = _print_ldd_trajectory(caplog, "test_render_monitoring_cli_valid_project")
    assert found, "No IMP:9 log found — LDD violation"
    logger.info("[IMP:9][cli-contract] main() exit 0 на валидном PROJECT_DIR/PROJECT ✓")


# 🧪 TRAP[TEST] · render_monitoring_cli_missing_args · Contract · Regression: fail-fast без аргументов
# · Scenario: main([]) без --project-dir/--project → argparse SystemExit(2) (make-таргет fail-fast)
# · Last fail: N/A (новый контрактный тест, DevPlan 116 B7 T7)
# · Remove if: argparse валидация обязательных аргументов меняется
def test_render_monitoring_cli_missing_args(monkeypatch, caplog) -> None:
    """Отсутствие обязательных аргументов → SystemExit(2) от argparse (fail-fast make-контракт)."""
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(sys, "argv", ["monitoring_config_renderer.py"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2, f"argparse should exit 2 on missing required args, got {exc_info.value.code}"

    _print_ldd_trajectory(caplog, "test_render_monitoring_cli_missing_args")
    logger.info("[IMP:9][cli-contract] main() без аргументов → SystemExit(2) ✓ (fail-fast)")


# 🧪 TRAP[TEST] · render_monitoring_cli_node_optional · Contract · Regression: NODE необязателен
# · Scenario: main() с --node → exit 0; без --node → exit 0 (NODE default "")
# · Last fail: N/A (новый контрактный тест)
# · Remove if: сигнатура make render-monitoring меняется (NODE становится обязательным)
def test_render_monitoring_cli_node_optional(tmp_path, monkeypatch, caplog) -> None:
    """NODE — опциональный аргумент: main() работает и с --node, и без него."""
    caplog.set_level(logging.INFO)

    project_dir = tmp_path / "empty-project"
    project_dir.mkdir()

    # Без --node
    monkeypatch.setattr(
        sys,
        "argv",
        ["monitoring_config_renderer.py", "--project-dir", str(project_dir), "--project", "test-app"],
    )
    assert main() == 0, "main() should work without --node (NODE default '')"

    # С --node
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "monitoring_config_renderer.py",
            "--project-dir",
            str(project_dir),
            "--project",
            "test-app",
            "--node",
            "test-node",
        ],
    )
    assert main() == 0, "main() should work with --node"

    _print_ldd_trajectory(caplog, "test_render_monitoring_cli_node_optional")
    logger.info("[IMP:9][cli-contract] NODE опционален (default '') — контракт make render-monitoring ✓")
