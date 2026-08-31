# GREP_SUMMARY: test scaffold_ai_project gen_ai_platform_yaml ai-project template-ai-project choices validation needs.database default monitoring regression
# STRUCTURE: ┌tmp_path fixtures┐ → ○ test_scaffold_accepts_ai_project_template (unknown→ERROR / ai-project→safe-stop) → ○ test_gen_yaml_ai_project_needs_database_default (default=name + false-suppress + override) → ○ test_gen_yaml_monitoring_regression (backend/frontend drift-guard) → ⊕ LDD trajectory IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты легализации template-ai-project в скаффолд-канале (DevPlan 019 TASK-8,
##           F13/F14/F15 → AC6): choices-валидация project_scaffolder.main (негатив unknown →
##           ERROR, позитив ai-project → проходит), gen_ai_platform_yaml для ptype=ai-project
##           (needs.database default=name, monitoring kernel 8787/7d), регресс-защита веток
##           monitoring backend/frontend от дрейфа. LDD IMP:9 + Anti-Loop + R1-R5.
## @scope    tests/unit (без Docker). Нативные импорты (main/gen_ai_platform_yaml), tmp_path
##           строго (Zero Hardcode — реальный PROJECTS_ROOT не участвует), capsys+caplog.
## @invariants
##   - Все тесты используют tmp_path (R1). pytestmark = static_audit.
##   - Негативный кейс main(): валидация template стоит ДО копирования/ФС-мутаций
##     (project_scaffolder.py:678 < copy_template:729) — возврат 1 безопасен.
##   - Позитивный кейс main(): безопасная остановка на confirm (confirm→False) — 0 ФС-мутаций.
##   - gen_ai_platform_yaml: database="" → default=name; "false"/"False" → needs.database
##     отсутствует; явное имя → переопределяет дефолт (существующая normalization сохранена).
## @rationale AC6: скаффолд-канал template-ai-project — hard-prerequisite W7 T0 (4 будущих
##            проекта ai-project через make new-project); дефолт needs.database=name переносит
##            провижининг БД из рук агентов в postgres-хук (устранение первопричины инцидента).
## @changes 2026-08-31 · DevPlan 019 TASK-8 — initial implementation (F13/F15 → AC6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import pathlib

import pytest
import yaml

from core.internal.scaffold.project_scaffolder import main
from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── project_scaffolder.main choices-валидация (F13 → AC6) ─────────────────


@ldd_trajectory
def test_scaffold_accepts_ai_project_template(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
    caplog,
) -> None:
    """Choices-валидация main(): unknown → ERROR (return 1); ai-project → проходит (return 0).

    # 🧪 TRAP[TEST] · Regression: template-ai-project канал (F13) · Scenario: --template=ai-project
    # должен проходить валидацию choices в main(); --template=unknown → "Invalid template" ERROR
    # · Last fail: N/A — канал физически не существовал (choices {frontend, backend}) · Remove if:
    # словарь choices валидации template меняется/выносится из main()
    ## @purpose — Негатив: unknown → return 1 + ERROR (валидация ДО ФС-мутаций — безопасно).
    ##            Позитив: ai-project проходит валидацию → return 0 на confirm (сухой режим,
    ##            ноль ФС-мутаций) — честный минимальный контракт без полного scaffold.
    ## @io — ⇥ tmp_path, monkeypatch, capsys, caplog → ⎋ None (asserts return code + output)
    ## @complexity — O(1) — только парсинг/валидация/confirm
    """
    logger.info("[IMP:9][test][scaffold_ai] test_scaffold_accepts_ai_project_template")

    # (а) Негатив: unknown template → ERROR на валидации (до copy_template, до любых ФС-мутаций)
    rc_unknown = main(argv=["--name", "test", "--template", "unknown", "--projects-root", str(tmp_path)])
    assert rc_unknown == 1, f"Expected return 1 for unknown template, got {rc_unknown}"
    out_unknown = capsys.readouterr().out
    assert "ERROR: Invalid template type: 'unknown'" in out_unknown
    assert "frontend | backend | ai-project" in out_unknown, "ERROR must advertise the new choices set"
    assert "[IMP:10][scaffold][main] Invalid template" in caplog.text, "IMP:10 log must be emitted"
    assert not (tmp_path / "personal" / "test").exists(), "No FS mutation before validation fails"

    # (б) Позитив: ai-project проходит валидацию → безопасная остановка на confirm (return 0)

    def _decline_confirm(*, dry_run: bool = False, ci_mode: str | None = None) -> bool:
        """Отказ от scaffold: confirm-контракт (dry_run/ci_mode) не влияет на отказ."""
        del dry_run, ci_mode  # параметры confirm-контракта семантически не используются
        return False

    monkeypatch.setattr("core.internal.scaffold.project_scaffolder.confirm", _decline_confirm)
    rc_ai = main(argv=["--name", "test", "--template", "ai-project", "--projects-root", str(tmp_path)])
    assert rc_ai == 0, f"Expected return 0 (cancelled at confirm), got {rc_ai}"
    out_ai = capsys.readouterr().out
    assert "Invalid template" not in out_ai, "ai-project must NOT trigger template ERROR"
    assert "template-ai-project" in out_ai, "show_plan must display template-ai-project"
    assert not (tmp_path / "personal" / "test").exists(), "Stopped at confirm — no FS mutation"


# ── gen_ai_platform_yaml для ptype=ai-project (F15 → AC6) ─────────────────


@ldd_trajectory
def test_gen_yaml_ai_project_needs_database_default(tmp_path: pathlib.Path, caplog) -> None:
    """ai-project: needs.database default=name; monitoring kernel 8787/7d; false-suppress; override.

    # 🧪 TRAP[TEST] · Regression: needs.database default для ai-project (F15) · Scenario: ptype=
    # ai-project с пустым database → needs.database == name; database="false" → отсутствует;
    # database="custom_db" → переопределяет · Last fail: N/A — ветка monitoring знала только
    # frontend/backend, database не имел дефолта · Remove if: логика дефолта/monitoring ai-project
    # меняется (см. kernel-контракт W3: /metrics на HEALTH_PORT 8787)
    ## @purpose — Полный манифест ai-project: needs.database=имя проекта (боты ВСЕГДА в Postgres —
    ##            kernel-стейт; переопределяется --database или подавляется --database=false),
    ##            monitoring {metrics: true, metrics_port: 8787, logs_retention: 7d, alerting:
    ##            false, dashboard: false}, target_node/needs.domain/expose/quality корректны.
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts yaml payload)
    ## @complexity — O(1) — 3 генерации yaml
    """
    logger.info("[IMP:9][test][scaffold_ai] test_gen_yaml_ai_project_needs_database_default")

    # Основной сценарий: database="" (не задан) → needs.database default = имя проекта
    yaml_path = tmp_path / "ai-platform.yaml"
    result = gen_ai_platform_yaml(
        name="managers",
        ptype="ai-project",
        node="asi-team-vps",
        domain="managers.asiteam.ru",
        database="",
        output_path=yaml_path,
        minimal=False,
    )
    assert result == "generated"
    parsed = yaml.safe_load(yaml_path.read_text())
    assert parsed["needs"]["database"] == "managers", "needs.database must default to project name"
    assert parsed["needs"]["domain"] == "managers.asiteam.ru"
    assert parsed["needs"]["expose"] is True
    assert parsed["target_node"] == "asi-team-vps"
    assert parsed["monitoring"] == {
        "metrics": True,
        "metrics_port": 8787,
        "logs_retention": "7d",
        "alerting": False,
        "dashboard": False,
    }, "ai-project monitoring must match kernel /metrics contract (W3)"
    assert parsed["quality"]["level"] == "auto", "quality.level must be present"
    assert "ai-project: needs.database default=name (managers)" in caplog.text, "IMP:8 default log"

    # Доп. кейс 1: явный --database=false → needs.database ОТСУТСТВУЕТ (existing normalization)
    yaml_false = tmp_path / "ai-platform-false.yaml"
    gen_ai_platform_yaml(
        name="managers",
        ptype="ai-project",
        node="asi-team-vps",
        domain="managers.asiteam.ru",
        database="false",
        output_path=yaml_false,
        minimal=False,
    )
    parsed_false = yaml.safe_load(yaml_false.read_text())
    assert "database" not in parsed_false["needs"], "explicit --database=false must suppress"

    # Доп. кейс 2: явный --database=custom_db → переопределяет дефолт
    yaml_custom = tmp_path / "ai-platform-custom.yaml"
    gen_ai_platform_yaml(
        name="managers",
        ptype="ai-project",
        node="asi-team-vps",
        domain="managers.asiteam.ru",
        database="custom_db",
        output_path=yaml_custom,
        minimal=False,
    )
    parsed_custom = yaml.safe_load(yaml_custom.read_text())
    assert parsed_custom["needs"]["database"] == "custom_db", "explicit database must override default"


@ldd_trajectory
def test_gen_yaml_monitoring_regression(tmp_path: pathlib.Path, caplog) -> None:
    """Регресс-защита: ветки monitoring backend/frontend НЕ изменились при добавлении ai-project.

    # 🧪 TRAP[TEST] · Regression: drift-guard мониторинговых веток · Scenario: ptype=backend →
    # metrics True, port 8000, retention 14d; ptype=frontend → metrics False, port 80, retention
    # 3d (без ai-project-ветки изменения не затронут прежние типы) · Last fail: N/A · Remove if:
    # контракт monitoring backend/frontend меняется (DevPlan 141 Q2)
    ## @purpose — elif-ветка ai-project не должна дрейфовать существующие ветки frontend/backend
    ##            (DevPlan 141 Q2: порты = реальные порты сервисов: nginx 80, FastAPI 8000).
    ## @io — ⇥ tmp_path, caplog → ⎋ None (asserts yaml payload)
    ## @complexity — O(1) — 2 генерации yaml
    """
    logger.info("[IMP:9][test][scaffold_ai] test_gen_yaml_monitoring_regression")

    backend_path = tmp_path / "ai-platform-backend.yaml"
    gen_ai_platform_yaml(
        name="api",
        ptype="backend",
        node="asi-team-vps",
        domain="api.example.com",
        output_path=backend_path,
        minimal=False,
    )
    backend = yaml.safe_load(backend_path.read_text())
    assert backend["monitoring"]["metrics"] is True
    assert backend["monitoring"]["metrics_port"] == 8000
    assert backend["monitoring"]["logs_retention"] == "14d"

    frontend_path = tmp_path / "ai-platform-frontend.yaml"
    gen_ai_platform_yaml(
        name="web",
        ptype="frontend",
        node="asi-team-vps",
        domain="web.example.com",
        output_path=frontend_path,
        minimal=False,
    )
    frontend = yaml.safe_load(frontend_path.read_text())
    assert frontend["monitoring"]["metrics"] is False
    assert frontend["monitoring"]["metrics_port"] == 80
    assert frontend["monitoring"]["logs_retention"] == "3d"
