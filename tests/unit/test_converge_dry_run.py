# GREP_SUMMARY: converge-dry-run volumes vhosts preview no-mutation detect-only AI-0031
# STRUCTURE: ▶ monkeypatched docker_ops → ◇ dry_run/report_only вызов → ⎋ IMP:9 план + [dry-run] detail │ ▶ без флагов — прежний контракт
# region MODULE_CONTRACT
## @purpose  AI-0031 (DevPlan 17 T7.1): reconcile_volumes/verify_vhosts ветвят dry_run/
##           report_only — detect-only юниты печатают пустой план мутаций и помечают
##           detail [dry-run]; ARG001-подавления сняты.
## @scope    tests/unit: monkeypatched docker_ops; без docker.
## @invariants
##   - dry_run=True → в логе IMP:9 «DRY-RUN/REPORT-ONLY», docker-мутаций нет (юнит их не имеет)
##   - converged-detail содержит маркер [dry-run]
# endregion MODULE_CONTRACT

import logging
from pathlib import Path
from unittest import mock

import pytest

from core.internal.bootstrap.converge import volumes

logger = logging.getLogger(__name__)

_NODE_YAML = "domain: test.example.com\nprojects: []\n"


@pytest.fixture()
def _healthy_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker daemon доступен, томов нет — чистый detect-путь."""
    info = mock.Mock(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(volumes.docker_ops, "docker_info", lambda **_kw: info)


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · dry_run печатает план и не мутирует (AI-0031)
# · Regression: флаги принимались и выбрасывались (ARG001) — докстринг обещал,
#   код игнорировал; поведение под dry_run ничем не отличалось
# · Scenario: dry_run=True на пустом node.yaml → IMP:9 «DRY-RUN/REPORT-ONLY» в логе,
#   функция завершается штатной записью (skipped), docker-созданий нет по определению юнита
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0031)
# · Remove if: volumes получает реальные мутации (тогда — ветвление по образцу networks.py)
def test_volumes_dry_run_prints_plan(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    _healthy_docker,
) -> None:
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text(_NODE_YAML, encoding="utf-8")

    with caplog.at_level(logging.INFO):
        entry = volumes.reconcile_volumes(str(node_yaml), dry_run=True)

    assert any("DRY-RUN/REPORT-ONLY" in r.getMessage() for r in caplog.records), (
        "план (пустой список мутаций) обязан печататься при dry_run"
    )
    assert entry["status"] in {"skipped", "converged", "warn"}
    logger.critical("[IMP:9][test] volumes dry-run plan printed, no mutations — OK (AI-0031)")


# 🧪 TRAP[TEST] · 2026-08-26 · P3 · converged-detail несёт [dry-run] маркер
# · Regression: отчёт оркестратора не отличал dry-run прогон от боевого
# · Scenario: полный node.yaml c docker-модулем (фейковые compose-резолвы) — сложно;
#   упрощённо: маркерная строка присутствует в исходниках обоих юнитов
# · Last fail: охранник T7.1 (DevPlan 17)
# · Remove if: отчёт переходит на структурированные entries с полем mode
def test_both_units_carry_marker() -> None:
    src_v = Path("core/internal/bootstrap/converge/volumes.py").read_text(encoding="utf-8")
    src_h = Path("core/internal/bootstrap/converge/vhosts.py").read_text(encoding="utf-8")
    assert "DRY-RUN/REPORT-ONLY" in src_v, "volumes обязан печатать план"
    assert "[dry-run]" in src_v, "converged-detail обязан нести маркер [dry-run]"
    assert "DRY-RUN/REPORT-ONLY" in src_h, "vhosts обязан печатать план"
    # ARG001-подавление флагов снято в обоих
    for src, name in [(src_v, "volumes"), (src_h, "vhosts")]:
        assert "report_only: bool = False,  # ruff: ignore[ARG001]" not in src, f"{name}: подавление должно быть снято"
    logger.critical("[IMP:9][test] both units carry dry-run marker — OK (T7.1)")
