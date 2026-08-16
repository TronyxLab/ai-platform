# GREP_SUMMARY: test-render-monitoring-cli contract make render-monitoring PROJECT_DIR PROJECT argparse main argv-DI
# STRUCTURE: ▶ main(argv) ← DI argv → ◇ валидные --project-dir/--project → exit 0 → ◇ отсутствие аргументов → SystemExit(2) → ⎋ контракт make-таргета
# region MODULE_CONTRACT
## @purpose  Контрактный CLI-тест для make render-monitoring (DevPlan 116 B7 T7, U-65 / D5):
##           main() рендерера с --project-dir/--project — exit 0 на валидном конфиге,
##           SystemExit(2) от argparse на отсутствии обязательных аргументов.
## @scope    НЕ запускает сервер, НЕ subprocess — прямой вызов функций (правило UI-тестов),
##           main(argv=[...]) — DI argv (правило testing.md: без subprocess для бизнес-логики;
##           DevPlan 167 D1: setattr sys.argv → argv-параметр).
## @invariants
##   - tmp_path вместо хардкод-путей
##   - Проект без monitoring-секции → exit 0 (backward compat, non-fatal)
##   - Отсутствие PROJECT_DIR/PROJECT → argparse SystemExit(2) (fail-fast контракт make-таргета)
##   - NODE необязателен (default "")
## @rationale Регистрация render-monitoring в Makefile требует проверяемого контракта exit-кодов:
##   make-таргет вызывает python3 renderer; отсутствие аргументов должно FAIL (не тихий no-op).
##   Заменяет R1-нарушающие pass-тесты (assert True/pass) в test_monitoring_config_renderer.py.
## @changes 2026-08-01 | Created (DevPlan 116 B7 T7, D5)
## @changes 2026-08-14 | DevPlan 167 D1 — setattr sys.argv → main(argv) DI
# endregion MODULE_CONTRACT

import logging

import pytest
from _conftest.ldd import _print_ldd_trajectory

from core.internal.monitoring.config_renderer import main

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · render_monitoring_cli_valid · Contract · Regression: make render-monitoring exit code
# · Scenario: main([--project-dir, --project]) на проекте без monitoring → exit 0 (backward compat)
# · Last fail: N/A (новый контрактный тест, DevPlan 116 B7 T7)
# · Remove if: make render-monitoring удаляется из Makefile/манифеста
def test_render_monitoring_cli_valid_project(tmp_path, caplog) -> None:
    """main() с валидными --project-dir/--project → exit 0 (даже без monitoring-секции)."""
    caplog.set_level(logging.INFO)

    # Проект без ai-platform.yaml — backward compat: build_merged_config → None → exit 0
    project_dir = tmp_path / "empty-project"
    project_dir.mkdir()

    result = main(["--project-dir", str(project_dir), "--project", "test-app"])

    assert result == 0, f"main() should exit 0 for valid project (no monitoring), got {result}"

    found = _print_ldd_trajectory(caplog, "test_render_monitoring_cli_valid_project")
    assert found, "No IMP:9 log found — LDD violation"
    logger.info("[IMP:9][cli-contract] main() exit 0 на валидном PROJECT_DIR/PROJECT ✓")


# 🧪 TRAP[TEST] · render_monitoring_cli_missing_args · Contract · Regression: fail-fast без аргументов
# · Scenario: main([]) без --project-dir/--project → argparse SystemExit(2) (make-таргет fail-fast)
# · Last fail: N/A (новый контрактный тест, DevPlan 116 B7 T7)
# · Remove if: argparse валидация обязательных аргументов меняется
# GUARD-PRESERVE (168): единственное покрытие fail-fast ветки main() (SystemExit(2) без аргументов) —
# exit-код контракта make render-monitoring (make-таргет обязан FAIL, не тихий no-op)
def test_render_monitoring_cli_missing_args(caplog) -> None:
    """Отсутствие обязательных аргументов → SystemExit(2) от argparse (fail-fast make-контракт)."""
    caplog.set_level(logging.INFO)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2, f"argparse should exit 2 on missing required args, got {exc_info.value.code}"

    _print_ldd_trajectory(caplog, "test_render_monitoring_cli_missing_args")
    logger.info("[IMP:9][cli-contract] main() без аргументов → SystemExit(2) ✓ (fail-fast)")


# 🧪 TRAP[TEST] · render_monitoring_cli_node_optional · Contract · Regression: NODE необязателен
# · Scenario: main() с --node → exit 0; без --node → exit 0 (NODE default "")
# · Last fail: N/A (новый контрактный тест)
# · Remove if: сигнатура make render-monitoring меняется (NODE становится обязательным)
def test_render_monitoring_cli_node_optional(tmp_path, caplog) -> None:
    """NODE — опциональный аргумент: main() работает и с --node, и без него."""
    caplog.set_level(logging.INFO)

    project_dir = tmp_path / "empty-project"
    project_dir.mkdir()

    # Без --node
    assert main(["--project-dir", str(project_dir), "--project", "test-app"]) == 0, (
        "main() should work without --node (NODE default '')"
    )

    # С --node
    assert (
        main([
            "--project-dir",
            str(project_dir),
            "--project",
            "test-app",
            "--node",
            "test-node",
        ])
        == 0
    ), "main() should work with --node"

    _print_ldd_trajectory(caplog, "test_render_monitoring_cli_node_optional")
    logger.info("[IMP:9][cli-contract] NODE опционален (default '') — контракт make render-monitoring ✓")
