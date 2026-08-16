"""
# GREP_SUMMARY: test-practices-check-project, mock-project, baseline-green, 60s, practices-lock, set-practices, drift-negative, L1, exit-codes, audit-monkeypatch, docs-in-code, restart-policies, transition-traces-ban, agent-check, R5-negative
# STRUCTURE: ▶ _make_mock_project (ai-platform.yaml + compose + src + git init/commit) → ◇ sync_practices → lock (version=1/level=auto/state=baseline) → ◇ check_project → exit 0 ≤60s → ◇ set_practices full → level/state меняются (audit monkeypatched) → ◇ drift negative (ручная правка) → FAIL warning → ◇ (164 W5-1) _run_single_check: docs-in-code / restart-policies / transition-traces-ban / agent-check handlers (PASS + R5-negative)
# region MODULE_CONTRACT
## @purpose  Unit-тесты check_project/sync_practices/set_practices (DevPlan 137 W1, K1 канал):
##           мок-проект (backend, 3 файла + git) проходит baseline-проверки ≤60s (warm),
##           practices.lock содержит version=1/level=auto/state=baseline, set-practices full
##           меняет уровень, R5-negative: ручная правка GENERATED-файла → drift-детект.
##           164 W5-1-follow-up: + прямые тесты 4 новых handler'ов через _run_single_check
##           (docs-in-code, restart-policies, transition-traces-ban, agent-check) — PASS и
##           R5-negative по каждому (docs/, forbidden .md, missing restart, always без
##           обоснования, init always, legacy-след, .py без GREP_SUMMARY).
## @scope    $TEST_SPEC 137 W1: test_practices_check_project (мок ≤60s).
## @invariants
##   - Native imports; инструменты (git/ruff/pytest/gitleaks/docker) — реальные, но мок лёгкий
##   - tmp_path + git-commit (maturity/gitleaks/commit-msg нужен git)
##   - PROBE_PORT=59999 — детерминированный skip health-тестов (не зависит от порта 80)
##   - LDD: IMP:9-траектория через caplog
##   - R5: negative-тест дрейфа (ручная правка GENERATED-файла с шапкой → hash mismatch)
##   - D-I3 (DevPlan 145 W3): audit_logger.write_audit_entry monkeypatched — 0 side-effects
##     на /var/log/platform/audit.jsonl (фиктивные записи убраны)
## @rationale  AC W1: project-check зелёный на моке ≤60s (warm) без правок агента.
## @changes  2026-08-05 · DevPlan 137 W1 — создан
##            2026-08-11 · DevPlan 145 W3 D-I3 — monkeypatch audit_logger (side-effect устранён)
##            2026-08-14 · DevPlan 164 W5-1-follow-up — +17 тестов 4 новых handler'ов
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core.internal.practices.check_project import _run_check, check_project
from core.internal.practices.generators import GENERATED_HEADER, read_lock
from core.internal.practices.manifest import load_manifest
from core.internal.practices.set_practices import set_practices
from core.internal.practices.sync_practices import sync_practices
from core.internal.shared.project_yaml import load_project_yaml
from tests.conftest import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# Детерминированный skip health-тестов мока (ничего не слушает на этом порту)
os.environ.setdefault("PROBE_PORT", "59999")


# region FIXTURE_audit_logger_mock
# D-I3 (DevPlan 145 W3): monkeypatch audit_logger.write_audit_entry — предотвращает side-effect
# записи в /var/log/platform/audit.jsonl при set_practices (фиктивные проекты в продакшен-трейле).
# Паттерн заимствован из test_escalator_downgrade_audit (137 W3).
@pytest.fixture(autouse=True)
def _stub_audit_logger() -> mock.MagicMock:
    """Перехват audit_logger.write_audit_entry для всех тестов модуля (D-I3)."""
    with mock.patch(
        "core.internal.shared.audit_logger.write_audit_entry",
        return_value=None,
    ) as m:
        yield m


# endregion FIXTURE_audit_logger_mock


# region HELPER__make_mock_project
def _make_mock_project(tmp_path: Path) -> Path:
    """Create a mock backend project (ai-platform.yaml + compose + src) with git init/commit."""
    project = tmp_path / "mockproject"
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        "name: mockproject\ntype: backend\ntarget_node: test-node\n", encoding="utf-8"
    )
    # restart: unless-stopped — канон W1-4 (мок обязан быть restart-compliant:
    # иначе restart-policies (baseline L2) RED блокирует active-full exit 0)
    (project / "docker-compose.yml").write_text(
        "services:\n  app:\n    image: busybox:latest\n    restart: unless-stopped\n", encoding="utf-8"
    )
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
    assert sync.lock_status in {"written", "updated"}
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

    logger.info("%s %s", _print_ldd_trajectory(caplog), "--- check results ---")
    for result in report.results:
        logger.info("%s", f"  [{result.check_id}] {result.status} — {result.message}")


# 🧪 TRAP[TEST] · 2026-08-05 · unit · AC W1: set-practices full меняет level/state/lock
# · Regression: level=full → active-full ТОЛЬКО по согласию (автопромоута нет)
# · Last fail: N/A
# · Remove if: семантика set-practices меняется
def test_set_practices_full_changes_level(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, _stub_audit_logger: mock.MagicMock
) -> None:
    """set_practices(project, 'full') → quality.level=full, lock state=active-full, pyproject full."""
    project = _make_mock_project(tmp_path)
    sync_practices(project)

    # D-I3 (DevPlan 145 W3): audit_logger monkeypatched через _stub_audit_logger fixture —
    # 0 side-effects на /var/log/platform/audit.jsonl.
    with caplog.at_level(logging.INFO):
        report = set_practices(project, "full")
    assert report.sync.level == "full"
    assert report.sync.state == "active-full"
    assert report.yaml_status in {"created", "updated"}

    data = load_project_yaml(project)
    assert data["quality"]["level"] == "full"
    lock = read_lock(project)
    assert lock.level == "full"
    assert lock.state == "active-full"
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert "select = [" in pyproject and '"E"' in pyproject  # full-конфиг ruff
    # D-I3: audit вызван (transition зафиксирована), но НЕ записан в /var/log/platform/audit.jsonl
    assert _stub_audit_logger.called, "audit_logger.write_audit_entry должен вызываться при transition"
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

    # repair через project-check --fix (alias project-fix удалён, План 175 W4.1) → drift-gate PASS + канон восстановлен → exit 0
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
    """Трейлинг-пробел в коде проекта → project-check FAIL; project-check --fix (alias project-fix) → PASS."""
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


# ══════════════════════════════════════════════════════════════════════════════
# 164 W5-1-follow-up: 4 новых handler'а (docs-in-code, restart-policies,
# transition-traces-ban, agent-check) — прямые вызовы через _run_check (канон-объект).
# ══════════════════════════════════════════════════════════════════════════════


# region HELPER__run_single_check
def _run_single_check(check_id: str, project: Path) -> Any:
    """Исполнить ОДИН handler канона напрямую (минуя select_checks — full-проверки тоже)."""
    check = load_manifest().by_id()[check_id]
    return _run_check(check, project, fix=False)


# endregion HELPER__run_single_check


# region HELPER__git_commit_all
def _git_commit_all(project: Path, message: str) -> None:
    """git add -A + commit (no-op при пустом status — коммит без изменений запрещён)."""
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project, check=True, capture_output=True, text=True
    ).stdout
    if not status.strip():
        return
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
            message,
            "--no-gpg-sign",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )


# endregion HELPER__git_commit_all


# region HELPER__make_frontend_project
def _make_frontend_project(tmp_path: Path, app_tsx: str, *, with_git: bool = False) -> Path:
    """Фронтенд-мок (type: frontend → typescript/react) с src/App.tsx."""
    project = tmp_path / "mockfrontend"
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        "name: mockfrontend\ntype: frontend\ntarget_node: test-node\n", encoding="utf-8"
    )
    src = project / "src"
    src.mkdir()
    (src / "App.tsx").write_text(app_tsx, encoding="utf-8")
    if with_git:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
        _git_commit_all(project, "init: mock frontend")
    return project


# endregion HELPER__make_frontend_project


# region HELPER__make_empty_project
def _make_empty_project(tmp_path: Path, ptype: str = "frontend") -> Path:
    """Пустой проект (только ai-platform.yaml) — нет кода ни для одного языка."""
    project = tmp_path / "mockempty"
    project.mkdir()
    (project / "ai-platform.yaml").write_text(
        f"name: mockempty\ntype: {ptype}\ntarget_node: test-node\n", encoding="utf-8"
    )
    return project


# endregion HELPER__make_empty_project


# ── docs-in-code (baseline L3) ────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-08-14 · unit · docs-in-code: allowlist .md не RED (инв.12 для проектов)
# · Regression: README.md/AGENTS.md/AI-PLATFORM.md/.ai/**/.kilo/** — легитимные docs-in-code
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: allowlist docs-in-code меняется
def test_docs_in_code_allows_allowlisted_md(tmp_path: Path) -> None:
    """Tracked .md в allowlist (README/AGENTS/AI-PLATFORM/.ai/.kilo) → PASS."""
    project = _make_mock_project(tmp_path)
    for rel in [
        "README.md",
        "AGENTS.md",
        "AI-PLATFORM.md",
        ".ai/plans/164-w5-1.md",
        ".kilo/rules/communication.md",
    ]:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    _git_commit_all(project, "docs: allowlisted md files")

    result = _run_single_check("docs-in-code", project)
    assert result.status == "PASS", f"allowlist .md не должен быть RED: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: каталог docs/ в проекте → RED
# · Regression: инв.12 — docs/ запрещён (каталог доков = вне кода, гейт WD-4 платформы)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: docs-in-code разрешает docs/ каталог
def test_docs_in_code_detects_docs_dir(tmp_path: Path) -> None:
    """R5 negative: создание docs/ в проекте → детектор RED."""
    project = _make_mock_project(tmp_path)
    (project / "docs").mkdir()

    result = _run_single_check("docs-in-code", project)
    assert result.status == "FAIL", f"docs/ каталог должен быть RED: {result.message}"
    assert "docs" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: tracked .md вне allowlist → RED
# · Regression: инв.12 — только allowlist .md легитимен в git-проекте
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: docs-in-code расширяет allowlist
def test_docs_in_code_detects_forbidden_tracked_md(tmp_path: Path) -> None:
    """R5 negative: tracked notes.md (вне allowlist) → RED."""
    project = _make_mock_project(tmp_path)
    (project / "notes.md").write_text("# notes\n", encoding="utf-8")
    _git_commit_all(project, "docs: forbidden md")

    result = _run_single_check("docs-in-code", project)
    assert result.status == "FAIL", f"tracked .md вне allowlist должен быть RED: {result.message}"
    assert "notes.md" in result.message


# ── restart-policies (baseline L2) ────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-08-14 · unit · restart-policies: unless-stopped → PASS (канон W1-4)
# · Regression: default long-running restart = unless-stopped
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: канон restart-policies меняется
def test_restart_policies_unless_stopped_pass(tmp_path: Path) -> None:
    """Long-running сервис с restart: unless-stopped → PASS."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n  app:\n    image: busybox:latest\n    restart: unless-stopped\n", encoding="utf-8"
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "PASS", f"unless-stopped должен быть PASS: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: отсутствующий restart у long-running → RED
# · Regression: сервис без restart получит default "no" — контейнер не переживёт краш
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: канон restart-policies меняется
def test_restart_policies_missing_restart_fail(tmp_path: Path) -> None:
    """R5 negative: long-running без restart → RED."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text("services:\n  app:\n    image: busybox:latest\n", encoding="utf-8")

    result = _run_single_check("restart-policies", project)
    assert result.status == "FAIL", f"long-running без restart должен быть RED: {result.message}"
    assert "app" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · restart-policies: always + комментарий-обоснование → PASS
# · Regression: always допустим только с обоснованием (allowlist stateful/комментарий)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: семантика обоснования restart меняется
def test_restart_policies_always_justified_comment_pass(tmp_path: Path) -> None:
    """always с комментарием-обоснованием в блоке сервиса → PASS."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    image: busybox:latest\n"
        "    restart: always\n"
        "    # restart: always — stateful app data persists on disk\n",
        encoding="utf-8",
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "PASS", f"always с обоснованием должен быть PASS: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · restart-policies: always + volumes (stateful) → PASS
# · Regression: allowlist stateful — сервис с volumes сохраняет данные между перезапусками
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: семантика stateful-allowlist меняется
def test_restart_policies_always_stateful_volumes_pass(tmp_path: Path) -> None:
    """always у stateful-сервиса (volumes) → PASS."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n  app:\n    image: busybox:latest\n    restart: always\n    volumes:\n      - ./data:/data\n",
        encoding="utf-8",
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "PASS", f"always stateful должен быть PASS: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: always без обоснования → RED
# · Regression: always без stateful/комментария = скрытое решение владельца (канон: unless-stopped)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: канон restart-policies меняется
def test_restart_policies_always_no_justification_fail(tmp_path: Path) -> None:
    """R5 negative: always без обоснования (комментарий про healthcheck не считается) → RED."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    image: busybox:latest\n"
        "    restart: always\n"
        "    healthcheck:\n"
        "      # ⚠️ TRAP[BUG] use 127.0.0.1 — Alpine ::1 IPv6\n"
        '      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:80/health"]\n',
        encoding="utf-8",
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "FAIL", f"always без обоснования должен быть RED: {result.message}"
    assert "app" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · restart-policies: init-контейнер с "no" → PASS
# · Regression: init/one-shot (migrate) — restart: "no" (канон W1-4, service_completed_successfully)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: канон init-контейнеров меняется
def test_restart_policies_init_no_pass(tmp_path: Path) -> None:
    """Init-контейнер (migrate) с restart: "no" + long-running unless-stopped → PASS."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n"
        "  migrate:\n"
        "    image: busybox:latest\n"
        '    restart: "no"\n'
        "  app:\n"
        "    image: busybox:latest\n"
        "    restart: unless-stopped\n",
        encoding="utf-8",
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "PASS", f"init restart:no должен быть PASS: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: init-контейнер с always → RED
# · Regression: init не должен авто-перезапускаться (always у one-shot — ошибка)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: канон init-контейнеров меняется
def test_restart_policies_init_always_fail(tmp_path: Path) -> None:
    """R5 negative: init-контейнер (migrate) с restart: always → RED."""
    project = _make_mock_project(tmp_path)
    (project / "docker-compose.yml").write_text(
        "services:\n  migrate:\n    image: busybox:latest\n    restart: always\n", encoding="utf-8"
    )

    result = _run_single_check("restart-policies", project)
    assert result.status == "FAIL", f"init с always должен быть RED: {result.message}"
    assert "migrate" in result.message


# ── transition-traces-ban (full L3) ───────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-08-14 · unit · transition-traces-ban: чистый код → PASS (S4-аналог)
# · Regression: отсутствие legacy/deprecated/transition-следов = зелёный
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: набор transition-слов меняется
def test_transition_traces_ban_clean_pass(tmp_path: Path) -> None:
    """Код без transition-следов → PASS."""
    project = _make_mock_project(tmp_path)
    result = _run_single_check("transition-traces-ban", project)
    assert result.status == "PASS", f"чистый код должен быть PASS: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: legacy/deprecated в комментарии → RED
# · Regression: S4 — следы перехода (legacy/deprecated) вне allowlist блокируются
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: transition-traces-ban отключается
def test_transition_traces_ban_detects_legacy(tmp_path: Path) -> None:
    """R5 negative: '# legacy:' в исходнике → RED."""
    project = _make_mock_project(tmp_path)
    (project / "src" / "main.py").write_text(
        "# GREP_SUMMARY: mock, legacy-module\n# legacy: keep until migration to v2\nprint('x')\n",
        encoding="utf-8",
    )

    result = _run_single_check("transition-traces-ban", project)
    assert result.status == "FAIL", f"legacy-след должен быть RED: {result.message}"
    assert "legacy" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · transition-traces-ban: allowlist (.ai/, GENERATED) → PASS
# · Regression: артефакты процессов (.ai/) и GENERATED-файлы — вне скана следов
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: allowlist transition-traces-ban меняется
def test_transition_traces_ban_skips_allowlist_and_generated(tmp_path: Path) -> None:
    """Следы в .ai/** и GENERATED-файлах не считаются → PASS."""
    project = _make_mock_project(tmp_path)
    ai_script = project / ".ai" / "scripts" / "tmp.py"
    ai_script.parent.mkdir(parents=True, exist_ok=True)
    ai_script.write_text("# trace inside .ai\nprint(1)\n", encoding="utf-8")
    generated = project / "tests" / "conftest.py"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text(f"{GENERATED_HEADER}\n# deprecated marker in GENERATED\n", encoding="utf-8")

    result = _run_single_check("transition-traces-ban", project)
    assert result.status == "PASS", f"allowlist-следы не должны быть RED: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · transition-traces-ban: CSS/React transition-идиомы → PASS
# · Regression: transition-all / transition={{...}} — легитимный код, не след перехода
# · Last fail: N/A (новый handler, 164 W5-1; botanika src/App.tsx false-positive анализ)
# · Remove if: комментарий-контекст для transition убирается
def test_transition_traces_ban_ignores_css_react_transition_idioms(tmp_path: Path) -> None:
    """Frontend: transition-all/transition={{...}} (не комментарии) → PASS."""
    project = _make_frontend_project(
        tmp_path,
        "export function App() {\n"
        '  return <div className="transition-all duration-300" '
        'style={{ transition: "opacity 0.3s" }} />;\n'
        "}\n",
    )

    result = _run_single_check("transition-traces-ban", project)
    assert result.status == "PASS", f"CSS/React transition — не след: {result.message}"


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: переходн/временн в комментарии → RED
# · Regression: кириллические маркеры перехода ловятся (не путаются с «современн*» — \b)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: набор transition-слов меняется
def test_transition_traces_ban_detects_cyrillic_trace(tmp_path: Path) -> None:
    """R5 negative: комментарий '# переходный период' → RED; 'современных' → НЕ RED."""
    project = _make_mock_project(tmp_path)
    (project / "src" / "main.py").write_text(
        "# GREP_SUMMARY: mock, trace\n"
        "# переходный период: временно используем старый API\n"
        "text = 'современных решений'\n",
        encoding="utf-8",
    )

    result = _run_single_check("transition-traces-ban", project)
    assert result.status == "FAIL", f"переходный/временно — следы: {result.message}"
    assert "переходн" in result.message or "временн" in result.message
    assert "современных" not in result.message, "«современных» — ложное срабатывание"


# ── agent-check (full L3) ─────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · 2026-08-14 · unit · agent-check: GREP_SUMMARY-чистый python → PASS
# · Regression: адаптация 163 W-E — grep-summary + ruff advisory clean = зелёный
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: состав agent-check меняется
def test_agent_check_pass_grep_summary_clean(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Python-мок с GREP_SUMMARY-файлами → PASS (ruff advisory чист или не установлен)."""
    project = _make_mock_project(tmp_path)
    with caplog.at_level(logging.INFO):
        result = _run_single_check("agent-check", project)
    assert result.status == "PASS", f"agent-check должен быть PASS: {result.message}"
    assert "grep-summary" in result.message or "clean" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · R5-negative: .py без GREP_SUMMARY → RED
# · Regression: код проекта обязан нести GREP_SUMMARY (канон gate grep-summary)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: agent-check убирает grep-summary-шаг
def test_agent_check_fail_missing_grep_summary(tmp_path: Path) -> None:
    """R5 negative: python-файл без GREP_SUMMARY в первых 10 строках → RED."""
    project = _make_mock_project(tmp_path)
    (project / "src" / "main.py").write_text(
        '"""Mock app entrypoint missing header marker."""\n\n\ndef main() -> None:\n    print("mock app")\n',
        encoding="utf-8",
    )

    result = _run_single_check("agent-check", project)
    assert result.status == "FAIL", f"без GREP_SUMMARY должен быть RED: {result.message}"
    assert "grep-summary" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · agent-check: фронтенд (не python) — ruff not applicable
# · Regression: ruff advisory SLF/FBT/ARG — python-only; ts/react — grep-summary остаётся
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: agent-check меняет языковую применимость
def test_agent_check_frontend_ruff_not_applicable(tmp_path: Path) -> None:
    """Frontend-мок: ruff-шаг not applicable, grep-summary-шаг исполняется (→ FAIL без GREP_SUMMARY)."""
    project = _make_frontend_project(tmp_path, "export function App() { return null; }\n")

    result = _run_single_check("agent-check", project)
    assert result.status == "FAIL", f"ts-файл без GREP_SUMMARY должен быть RED: {result.message}"
    assert "grep-summary" in result.message
    assert "not applicable" in result.message


# 🧪 TRAP[TEST] · 2026-08-14 · unit · agent-check: нет кода → SKIP (не PASS — честность)
# · Regression: проверка без объекта не может быть PASS (Test Honesty R1)
# · Last fail: N/A (новый handler, 164 W5-1)
# · Remove if: agent-check меняет SKIP-семантику пустого проекта
def test_agent_check_skip_no_code_files(tmp_path: Path) -> None:
    """Проект без кода (только ai-platform.yaml) → SKIP 'not applicable'."""
    project = _make_empty_project(tmp_path, ptype="frontend")

    result = _run_single_check("agent-check", project)
    assert result.status == "SKIP", f"пустой проект должен быть SKIP: {result.message}"
    assert "not applicable" in result.message
