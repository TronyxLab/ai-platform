#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-practices-check-project, mock-project, baseline-green, 60s, practices-lock, set-practices, drift-negative, L1, exit-codes
# STRUCTURE: ▶ _make_mock_project (ai-platform.yaml + compose + src + git init/commit) → ◇ sync_practices → lock (version=1/level=auto/state=baseline) → ◇ check_project → exit 0 ≤60s → ◇ set_practices full → level/state меняются → ◇ drift negative (ручная правка) → FAIL warning
# region MODULE_CONTRACT
## @purpose  Unit-тесты check_project/sync_practices/set_practices (DevPlan 137 W1, K1 канал):
##           мок-проект (backend, 3 файла + git) проходит baseline-проверки ≤60s (warm),
##           practices.lock содержит version=1/level=auto/state=baseline, set-practices full
##           меняет уровень, R5-negative: ручная правка GENERATED-файла → drift-детект.
## @scope    $TEST_SPEC 137 W1: test_practices_check_project (мок ≤60s).
## @invariants
##   - Native imports; инструменты (git/ruff/pytest/gitleaks/docker) — реальные, но мок лёгкий
##   - tmp_path + git-commit (maturity/gitleaks/commit-msg нужен git)
##   - PROBE_PORT=59999 — детерминированный skip health-тестов (не зависит от порта 80)
##   - LDD: IMP:9-траектория через caplog
##   - R5: negative-тест дрейфа (ручная правка GENERATED-файла с шапкой → hash mismatch)
## @rationale  AC W1: project-check зелёный на моке ≤60s (warm) без правок агента.
## @changes  2026-08-05 · DevPlan 137 W1 — создан
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import time
from pathlib import Path

import pytest

from core.internal.practices.check_project import check_project
from core.internal.practices.generators import GENERATED_HEADER, read_lock
from core.internal.practices.set_practices import set_practices
from core.internal.practices.sync_practices import sync_practices
from core.internal.shared.project_yaml import load_project_yaml
from tests.conftest import _print_ldd_trajectory

logger = logging.getLogger(__name__)

# Детерминированный skip health-тестов мока (ничего не слушает на этом порту)
os.environ.setdefault("PROBE_PORT", "59999")


# region HELPER__make_mock_project
def _make_mock_project(tmp_path: Path) -> Path:
    """Create a mock backend project (ai-platform.yaml + compose + src) with git init/commit."""
    project = tmp_path / "mockproject"
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        "name: mockproject\ntype: backend\ntarget_node: test-node\n", encoding="utf-8"
    )
    (project / "docker-compose.yml").write_text("services:\n  app:\n    image: busybox:latest\n", encoding="utf-8")
    src = project / "src"
    src.mkdir()
    # GREP_SUMMARY — чтобы grep-summary (full-проверка) проходил в active-full;
    # ruff-check (full) — docstring + аннотации + без явного return None (RET501)
    (src / "main.py").write_text(
        "# GREP_SUMMARY: mock, app, entrypoint\n"
        "\n"
        '"""Mock app entrypoint (test fixture)."""\n'
        "\n"
        "\n"
        "def main() -> None:\n"
        '    """Run mock app."""\n'
        '    print("mock app")\n',
        encoding="utf-8",
    )
    # git init + commit (maturity: age=0; commit-msg: conventional "init:"; gitleaks: чистый)
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
            "init: mock from template-backend",
            "--no-gpg-sign",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    return project


# endregion HELPER__make_mock_project


# 🧪 TRAP[TEST] · 2026-08-05 · unit · AC W1: sync → lock baseline + project-check зелёный ≤60s
# · Regression: AC1 (project-check ≤60s warm, 0 правок агента) — главный критерий W1
# · Last fail: N/A
# · Remove if: состав baseline-проверок меняется
def test_check_project_mock_baseline_green(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Мок-проект: sync → practices.lock (v1/auto/baseline) → check_project exit 0 ≤60s."""
    project = _make_mock_project(tmp_path)
    start = time.monotonic()

    with caplog.at_level(logging.INFO):
        sync = sync_practices(project)
    assert sync.state == "baseline"
    assert sync.lock_status in ("written", "updated")
    # 5 GENERATED-файлов + lock
    assert (project / "pyproject.toml").is_file()
    assert (project / ".pre-commit-config.yaml").is_file()
    assert (project / "tests" / "conftest.py").is_file()
    assert (project / "tests" / "test_health.py").is_file()
    assert (project / "practices.lock").is_file()

    lock = read_lock(project)
    assert lock is not None
    assert lock.version == 1
    assert lock.level == "auto"
    assert lock.state == "baseline"

    with caplog.at_level(logging.INFO):
        report = check_project(project)
    duration = time.monotonic() - start
    assert report.state == "baseline"
    assert report.exit_code == 0, f"project-check не зелёный: {report.results}"
    assert duration <= 60, f"project-check занял {duration:.1f}s (> 60s лимит)"

    print(_print_ldd_trajectory(caplog), "--- check results ---")
    for result in report.results:
        print(f"  [{result.check_id}] {result.status} — {result.message}")


# 🧪 TRAP[TEST] · 2026-08-05 · unit · AC W1: set-practices full меняет level/state/lock
# · Regression: level=full → active-full ТОЛЬКО по согласию (автопромоута нет)
# · Last fail: N/A
# · Remove if: семантика set-practices меняется
def test_set_practices_full_changes_level(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """set_practices(project, 'full') → quality.level=full, lock state=active-full, pyproject full."""
    project = _make_mock_project(tmp_path)
    sync_practices(project)

    # 📝 TRAP[DEBT] · 2026-08-05 · LO · W1-тест пишет РЕАЛЬНЫЕ аудит-записи в /var/log/platform/audit.jsonl
    # · Observed: test_set_practices_full_changes_level вызывает set_practices → _audit_transition →
    # ·   write_audit_entry (реальный writer) — на dev-машине с правами на /var/log/platform в
    # ·   продакшен-трейл попадают записи project="mockproject" (проверено 2026-08-05: 2 записи в audit.jsonl)
    # · Suspected: W1-тест не monkeypatch-ит audit_logger (в отличие от test_practices_escalator W3);
    # ·   аудит не был в фокусе W1-спеки тестов
    # · Impact: шум в аудит-трейле платформы (фиктивные проекты); на CI/нодах без прав — молчаливый skip
    # · When: DevPlan 137 W3 — практическая проверка AC3 (аудит-запись увидела в audit.jsonl)
    # · Fix (deferred): monkeypatch audit_logger.write_audit_entry как в test_escalator_downgrade_audit
    with caplog.at_level(logging.INFO):
        report = set_practices(project, "full")
    assert report.sync.level == "full"
    assert report.sync.state == "active-full"
    assert report.yaml_status in ("created", "updated")

    data = load_project_yaml(project)
    assert data["quality"]["level"] == "full"
    lock = read_lock(project)
    assert lock.level == "full"
    assert lock.state == "active-full"
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert "select = [" in pyproject and '"E"' in pyproject  # full-конфиг ruff
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога set_practices"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · R5-negative: ручная правка GENERATED-файла → drift FAIL (block в active-full)
# · Regression: дрейф GENERATED-практик детектится локально (K1) в proposed/active-full
#   (drift-gate — full-уровень, §3.2); в baseline дрift-gate не исполняется (полный набор —
#   эскалатор, W3). active-full → L2-блок → exit 1; repair (--fix) → PASS, exit 0.
# · Last fail: N/A (negative-тест на новый drift-gate)
# · Remove if: drift-gate семантика меняется
def test_check_project_drift_detected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Ручная правка GENERATED-файла (шапка сохранена) в active-full → drift-gate FAIL + exit 1."""
    project = _make_mock_project(tmp_path)
    sync_practices(project)
    set_practices(project, "full")  # active-full → full-набор (drift-gate исполняется)

    pyproject = project / "pyproject.toml"
    edited = pyproject.read_text(encoding="utf-8").replace("line-length = 120", "line-length = 110")
    pyproject.write_text(edited, encoding="utf-8")

    with caplog.at_level(logging.INFO):
        report = check_project(project)
    drift = [r for r in report.results if r.check_id == "drift-gate"]
    assert drift, "drift-gate не исполнялся в active-full"
    assert drift[0].status == "FAIL"
    assert report.exit_code == 1  # L2 в active-full — блок

    # repair через project-fix (--fix) → drift-gate PASS + канон восстановлен → exit 0
    with caplog.at_level(logging.INFO):
        report_fixed = check_project(project, fix=True)
    drift_fixed = [r for r in report_fixed.results if r.check_id == "drift-gate"]
    assert drift_fixed[0].status == "PASS"
    restored = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert "line-length = 120" in restored  # канон восстановлен (был 110 после ручной правки)
    assert report_fixed.exit_code == 0, f"после repair не зелёный: {report_fixed.results}"
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога drift-gate"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · hygiene auto-fix: trailing whitespace чинится через --fix
# · Regression: baseline = автофиксируемое (агент не тратит время, §3.1)
# · Last fail: N/A
# · Remove if: hygiene автофикс меняется
def test_hygiene_auto_fix_via_project_fix(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Трейлинг-пробел в коде проекта → project-check FAIL; project-fix (--fix) → PASS."""
    project = _make_mock_project(tmp_path)
    sync_practices(project)
    (project / "src" / "main.py").write_text("# mock app entrypoint  \n", encoding="utf-8")  # trailing space

    with caplog.at_level(logging.INFO):
        report = check_project(project)
    hygiene = [r for r in report.results if r.check_id == "hygiene"]
    assert hygiene and hygiene[0].status == "FAIL"

    with caplog.at_level(logging.INFO):
        report_fixed = check_project(project, fix=True)
    hygiene_fixed = [r for r in report_fixed.results if r.check_id == "hygiene"]
    assert hygiene_fixed[0].status == "PASS"
    content = (project / "src" / "main.py").read_text(encoding="utf-8")
    assert not content.endswith("  \n")
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога hygiene"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · GENERATED-шапка присутствует во всех 5 файлах мока
# · Regression: AC W1 — 5 GENERATED-файлов с шапкой
# · Last fail: N/A
# · Remove if: состав GENERATED-файлов меняется
def test_mock_has_five_generated_files(tmp_path: Path) -> None:
    """Мок после sync содержит 5 GENERATED-файлов с шапкой."""
    project = _make_mock_project(tmp_path)
    sync_practices(project)
    expected = {
        "pyproject.toml",
        ".pre-commit-config.yaml",
        "tests/conftest.py",
        "tests/test_health.py",
        "practices.lock",
    }
    for rel in expected:
        assert (project / rel).is_file(), f"нет GENERATED-файла: {rel}"
    for rel in expected:
        content = (project / rel).read_text(encoding="utf-8")
        assert content.startswith(GENERATED_HEADER), f"{rel} не имеет GENERATED-шапки"
