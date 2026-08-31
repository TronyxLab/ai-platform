"""
# GREP_SUMMARY: test-post-bootstrap-report, t17, stale-errors, awaiting-projects, deploy-classification, docker-probe, back-compat, state-json, mark-done-prune
# STRUCTURE: ▶ tmp_path state.json + node.yaml + projects_base-фейк → ◇ stale-фильтр отчёта (done-фаза → ошибка скрыта) → ◇ mark-done prune (state.errors честен) → ◇ awaiting-классификация (compose | docker live | unavailable) → ◇ back-compat legacy state.json (list[str]) → ⎋ LDD trajectory IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests для plan 012 T17-fix: post_bootstrap_report честность —
##           (1) stale state.errors/warnings между прогонами не показываются после успешного
##           перевыполнения фазы (отчёт-фильтр + mark-done prune); (2) "Awaiting project
##           deploy" — честная классификация по compose-файлу / live-контейнеру (DI-фейки,
##           0 реальных docker-вызовов); (3) back-compat: legacy state.json (errors/warnings
##           = list[str]) читается без исключения.
## @scope    post_bootstrap_report + _mark_phase_success (prune) + _is_stale_phase_message +
##           _classify_projects/_docker_container_live (через DI) из lifecycle/cli.py.
##           НЕ требует root/Docker/реальной ноды — всё через tmp_path и fake-проберы.
## @invariants
##   - 0 реальных subprocess/docker-вызовов: docker_check_fn — DI-фейки, projects_base — tmp_path
##   - state.json — только tmp_path (никогда /var/lib/platform)
##   - Каждый тест валидирует IMP:9 лог (ldd_trajectory + caplog)
## @rationale T17-fix (живой инцидент 2026-08-31): bootstrap run 3 прошёл 9/9 успешно, а отчёт
##            показывал «Failed: Phase deploy_services failed ... exit=10» (ошибка run 1) и
##            «Awaiting project deploy: tronyx-site, dance-site, botanika, oldapp» при всех
##            deployed/skip-healthy. Тесты фиксируют оба регресса + back-compat контракт.
# endregion MODULE_CONTRACT
"""

import json
import logging
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle import cli as lifecycle_cli
from core.internal.bootstrap.lifecycle.state_machine import StateMachine
from core.internal.bootstrap.lifecycle.state_store import BootstrapState, StepState, load_state
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def state_file(tmp_path):
    """Provide a temporary state file path for each test."""
    return tmp_path / "state.json"


def _make_sm(state_file: Path, *, steps: dict[str, StepState] | None = None) -> StateMachine:
    """Build StateMachine with an in-memory BootstrapState (no FS writes)."""
    sm = StateMachine(state_file_path=str(state_file))
    sm.state = BootstrapState(
        mode="init",
        node="test-node",
        steps=steps or {},
        errors=[],
        warnings=[],
    )
    return sm


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════════════
# plan 012 T17-fix: stale state.errors/warnings между прогонами
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · T17-fix — mark-done prune чистит stale-записи фазы
# · Scenario: state.errors/warnings содержат записи "Phase deploy_services ..." (run N);
#   _mark_phase_success(deploy_services) → записи фазы удалены, non-phase записи сохранены,
#   чистка персистится в state.json
# · Last fail: live run 3 — "Failed: Phase deploy_services failed ... exit=10" из run 1
# · Remove if: аккумуляция state.errors устранена структурно (не на уровне записи)
@ldd_trajectory
def test_mark_phase_success_prunes_stale_phase_records(caplog, state_file):
    """_mark_phase_success удаляет stale errors/warnings фазы (T17-fix prune)."""
    sm = _make_sm(
        state_file,
        steps={"deploy_services": StepState(name="deploy_services", status="failed")},
    )
    sm.state.errors = [
        "Phase deploy_services failed: Module deployment failed (exit=10)",
        "external error without phase",
    ]
    sm.state.warnings = [
        "Phase deploy_services completed with non-fatal issues (returned False) — will be re-executed on next run",
        "external warning",
    ]

    lifecycle_cli._mark_phase_success(sm, "deploy_services", current_index=8)

    assert sm.state.steps["deploy_services"].status == "done"
    assert sm.state.errors == ["external error without phase"], f"prune FAIL: stale error не удалён: {sm.state.errors}"
    assert sm.state.warnings == ["external warning"], f"prune FAIL: stale warning не удалён: {sm.state.warnings}"

    # Чистка персистится (sm.save() внутри _mark_phase_success)
    persisted = json.loads(Path(state_file).read_text(encoding="utf-8"))
    assert persisted["errors"] == ["external error without phase"], "prune не персистится в state.json"
    assert persisted["warnings"] == ["external warning"], "prune не персистится в state.json"
    logger.info("[IMP:9][test][prune] mark-done prune удаляет stale-записи фазы — PASS")


# 🧪 TRAP[TEST] · REGRESSION · T17-fix — отчёт-фильтр stale-ошибок по done-фазам
# · Scenario: state.errors содержит ошибку фазы deploy_services, статус которой СЕЙЧАС done
#   (успешно перевыполнена) → отчёт НЕ показывает её; ошибка фазы secrets_provision со
#   статусом failed (не done) → ПОКАЗЫВАЕТСЯ
# · Last fail: live run 3 — "Failed: Phase deploy_services failed ... exit=10" из run 1
# · Remove if: report-фильтр заменён структурной чисткой на уровне записи state
@ldd_trajectory
def test_report_filters_stale_phase_error_after_success(caplog, state_file, tmp_path, monkeypatch):
    """Отчёт не показывает ошибки done-фаз (stale), но показывает ошибки failed-фаз."""
    sm = _make_sm(
        state_file,
        steps={
            "deploy_services": StepState(name="deploy_services", status="done"),
            "secrets_provision": StepState(name="secrets_provision", status="failed"),
        },
    )
    sm.state.errors = [
        "Phase deploy_services failed: Module deployment failed (exit=10)",  # stale (фаза done)
        "Phase secrets_provision failed: AGE key missing",  # актуальна (фаза failed)
    ]
    sm.state.warnings = [
        "Phase deploy_services completed with non-fatal issues (returned False) — will be re-executed on next run",
    ]
    node_yaml_path = tmp_path / "node.yaml"
    node_yaml_path.write_text("projects: []\n", encoding="utf-8")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))
    monkeypatch.setenv("NODE_NAME", "test-node")

    lifecycle_cli.post_bootstrap_report(
        sm,
        projects_base=str(tmp_path / "projects"),
        docker_check_fn=lambda _name: False,
    )

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "Module deployment failed (exit=10)" not in combined, f"stale-ошибка done-фазы показана: {combined}"
    assert "Phase secrets_provision failed: AGE key missing" in combined, "актуальная ошибка failed-фазы скрыта"
    assert "Warnings: 0" in combined, f"stale-warning done-фазы не отфильтрован: {combined}"
    logger.info("[IMP:9][test][stale] отчёт фильтрует stale-ошибки done-фаз — PASS")


# ═══════════════════════════════════════════════════════════════════════════
# plan 012 T17-fix: честная awaiting-классификация
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · T17-fix — awaiting только реально-ожидающие
# · Scenario: проект с compose-файлом → deployed; docker-проба True → deployed; False → awaiting;
#   None (docker недоступен) → awaiting + "docker unavailable"; "Awaiting project deploy" —
#   только реально-ожидающие; строка "Projects deployed: N/M (live: ...)"
# · Last fail: live run 3 — "Awaiting project deploy: tronyx-site, dance-site, botanika, oldapp"
#   при всех deployed/skip-healthy
# · Remove if: awaiting-классификация перенесена в другой слой/источник
@ldd_trajectory
def test_report_awaiting_classification(caplog, state_file, tmp_path, monkeypatch):
    """awaiting = только проекты без compose-файла и без live-контейнера (T17-fix)."""
    projects_dir = tmp_path / "projects"
    (projects_dir / "aaa").mkdir(parents=True)
    (projects_dir / "aaa" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    sm = _make_sm(state_file)
    projects_yaml = "projects:\n  - name: aaa\n  - name: bbb\n  - name: ccc\n  - name: ddd\n"
    node_yaml_path = tmp_path / "node.yaml"
    node_yaml_path.write_text(projects_yaml, encoding="utf-8")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))
    monkeypatch.setenv("NODE_NAME", "test-node")

    fake_live = {"bbb": True, "ccc": False, "ddd": None}
    lifecycle_cli.post_bootstrap_report(
        sm,
        projects_base=str(projects_dir),
        docker_check_fn=lambda name: fake_live.get(name, False),
    )

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "Awaiting project deploy: ccc, ddd" in combined, (
        f"awaiting-список нечестен (должны быть только ccc, ddd): {combined}"
    )
    assert "aaa" not in combined.split("Awaiting project deploy:")[1].splitlines()[0], (
        "deployed-проект (compose) попал в awaiting"
    )
    assert "Projects deployed: 2/4 (live: aaa, bbb — docker unavailable)" in combined, (
        f"deployed N/M (live) строка неверна: {combined}"
    )
    logger.info("[IMP:9][test][awaiting] классификация compose|live|unavailable — PASS")


# ═══════════════════════════════════════════════════════════════════════════
# plan 012 T17-fix: back-compat legacy state.json (list[str] errors/warnings)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · T17-fix — back-compat: старый state.json читается без исключения
# · Scenario: state.json со СТАРОЙ структурой (errors/warnings = list[str], steps — raw dict)
#   читается load_state без исключения; post_bootstrap_report с таким state не падает и
#   фильтрует stale-ошибку done-фазы
# · Last fail: N/A (контракт-тест back-compat при введении stale-фикса)
# · Remove if: legacy-ветки from_dict удалены (все ноды на свежем формате)
@ldd_trajectory
def test_report_backcompat_legacy_state_json(caplog, state_file, tmp_path, monkeypatch):
    """Legacy state.json (list[str] errors) читается; отчёт не роняет и фильтрует stale."""
    legacy = {
        "mode": "init",
        "node": "legacy-node",
        "current_step": 9,
        "steps": {
            "deploy_services": {"name": "deploy_services", "status": "done"},
            "converge_services": {"name": "converge_services", "status": "done"},
        },
        "errors": ["Phase deploy_services failed: old error from run 1"],
        "warnings": [
            "Phase deploy_services completed with non-fatal issues (returned False) — will be re-executed on next run"
        ],
    }
    Path(state_file).write_text(json.dumps(legacy), encoding="utf-8")

    loaded = load_state(state_file)
    assert loaded.errors == ["Phase deploy_services failed: old error from run 1"], (
        "back-compat FAIL: legacy errors не прочитаны"
    )
    assert loaded.warnings == [
        "Phase deploy_services completed with non-fatal issues (returned False) — will be re-executed on next run"
    ], "back-compat FAIL: legacy warnings не прочитаны"

    sm = StateMachine(state_file_path=str(state_file))  # загрузка legacy state.json
    node_yaml_path = tmp_path / "node.yaml"
    node_yaml_path.write_text("projects: []\n", encoding="utf-8")
    monkeypatch.setenv("NODE_YAML", str(node_yaml_path))
    monkeypatch.setenv("NODE_NAME", "legacy-node")

    lifecycle_cli.post_bootstrap_report(
        sm,
        projects_base=str(tmp_path / "projects"),
        docker_check_fn=lambda _name: False,
    )

    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "old error from run 1" not in combined, "stale legacy-ошибка показана в отчёте"
    assert "Failed: (none)" in combined, "отчёт с legacy state не отфильтровал stale-ошибку"
    assert "Warnings: 0" in combined, "stale legacy-warning не отфильтрован"
    logger.info("[IMP:9][test][backcompat] legacy state.json читается и фильтруется — PASS")
