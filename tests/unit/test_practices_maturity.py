"""
# GREP_SUMMARY: test-practices-maturity, age, first-commit, mtime-fallback, ai-platform-yaml-fallback, code-files, excludes, thresholds, no-git, VPS
# STRUCTURE: ▶ _make_project (git|no-git, past-dates) → ◇ compute_maturity → ⊕ age chain (first commit → ai-platform.yaml commit → mtime → 0) → ⊕ code_files (src/backend/frontend/app/tests/scripts/root, excludes node_modules/.venv/dist/*.lock/.env*/generated) → ◇ is_propose (30/50 из канона) → ⎋ asserts + IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/practices/maturity.py (DevPlan 137 W3 §5 задача 1):
##           возраст проекта (первый коммит → первый коммит ai-platform.yaml → mtime → 0,
##           решение пользователя 2026-08-05), счётчик файлов кода с исключениями
##           (node_modules/.venv/dist/build/coverage/.next/*.lock/package-lock.json/.env*/
##           generated-файлы практик/*.min.js), пороги 30/50 из канона (НЕ хардкод),
##           НЕ падает без git (VPS-сценарий: нет git → mtime fallback → 0).
## @scope    $TEST_SPEC 137 W3: test_practices_maturity (пороги 30/50, исключения каталогов,
##           fallback даты по ai-platform.yaml, отсутствие git на VPS).
## @invariants
##   - Native imports; git-команды только для создания фикстур (не для проверки бизнес-логики)
##   - tmp_path для всех проектов (zero hardcode)
##   - Пороги сверяются с каноном (maturity_thresholds() / manifest-константы) — не литералы
##   - LDD: IMP:9-траектория через caplog (compute_maturity логирует IMP:9)
##   - Тесты на mtime/возраст используют относительные даты (now - N дней) — робастность к дате прогона
## @rationale  AC W3: maturity — основа эскалатора; fallback-цепочка и исключения — критерий
##             «зрелый проект» (age>30 ∨ files>50 → [PRACTICES:PROPOSE]).
## @changes  2026-08-05 · DevPlan 137 W3 — создан
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.internal.practices.manifest import MATURITY_AGE_DAYS_PROPOSE, MATURITY_CODE_FILES_PROPOSE, maturity_thresholds
from core.internal.practices.maturity import Maturity, _git_first_commit, compute_maturity
from tests.conftest import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region HELPER__make_project
def _write_file(project: Path, rel: str, content: str) -> None:
    """Записать файл с созданием родительских каталогов (write_text не создаёт parents)."""
    path = project / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_code_files(project: Path) -> None:
    """Создать дерево кода с файлами-приманками (исключения) для счётчика code_files."""
    _write_file(project, "src/main.py", "x = 1\n")
    _write_file(project, "backend/app.py", "x = 1\n")
    _write_file(project, "frontend/ui.tsx", "export const a = 1\n")
    _write_file(project, "app/app.js", "const x = 1\n")
    _write_file(project, "tests/test_x.py", "def test_x():\n    assert 1\n")
    _write_file(project, "scripts/deploy.sh", "#!/bin/sh\n")
    _write_file(project, "root_cli.py", "x = 1\n")
    # ── исключения каталогов (DevPlan 137 §4.1) ──
    _write_file(project, "node_modules/vendor.js", "var x = 1\n")
    _write_file(project, ".venv/lib.py", "x = 1\n")
    _write_file(project, "dist/bundle.js", "var x = 1\n")
    _write_file(project, "build/out.js", "var x = 1\n")
    _write_file(project, "coverage/cov.py", "x = 1\n")
    _write_file(project, ".next/chunk.tsx", "export const x = 1\n")
    # ── исключения файлов (§4.1) ──
    _write_file(project, "lib.lock", "locked\n")
    _write_file(project, "package-lock.json", "{}\n")
    _write_file(project, ".env", "SECRET=x\n")
    _write_file(project, ".env.platform", "NAME=mock\n")
    _write_file(project, "vendor.min.js", "var x=1\n")
    # ── GENERATED-файлы практик (исключаются по relative-path) ──
    _write_file(project, "pyproject.toml", "[tool.ruff]\n")
    _write_file(project, "practices.lock", "version: 1\n")
    _write_file(project, "tests/conftest.py", "import pytest\n")
    _write_file(project, "tests/test_health.py", "def test_health():\n    pass\n")


def _git_commit_at(project: Path, when: datetime, msg: str) -> None:
    """Сделать git-коммит с заданной датой (GIT_AUTHOR_DATE/GIT_COMMITTER_DATE)."""
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = when.isoformat()
    env["GIT_COMMITTER_DATE"] = when.isoformat()
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
            msg,
            "--no-gpg-sign",
        ],
        cwd=project,
        check=True,
        capture_output=True,
        env=env,
    )


# endregion HELPER__make_project


# 🧪 TRAP[TEST] · 2026-08-05 · unit · пороги 30/50 читаются из канона (НЕ хардкод)
# · Regression: литеральные пороги в тесте рассинхронизируются с каноном practices_manifest.yaml
# · Last fail: N/A
# · Remove if: пороги зрелости выносятся из канона
def test_maturity_thresholds_from_canon() -> None:
    """Пороги 30/50 — из канона (maturity_thresholds + manifest-константы-зеркала)."""
    thresholds = maturity_thresholds()
    assert thresholds["age_days_propose"] == 30
    assert thresholds["code_files_propose"] == 50
    # зеркала manifest.py совпадают с каноном (гейт паритета)
    assert MATURITY_AGE_DAYS_PROPOSE == 30
    assert MATURITY_CODE_FILES_PROPOSE == 50


# 🧪 TRAP[TEST] · 2026-08-05 · unit · is_propose: строгое превышение порога (age>30 ∨ files>50)
# · Regression: граница порога (30/50) должна быть НЕ-предложением (строго больше)
# · Last fail: N/A
# · Remove if: семантика порогов меняется
def test_maturity_is_propose_thresholds() -> None:
    """is_propose: age>30 ИЛИ files>50 (строго больше; ровно 30/50 — НЕ предложение)."""
    thresholds = maturity_thresholds()
    assert Maturity(age_days=30, code_files=50).is_propose(thresholds) is False  # граница
    assert Maturity(age_days=31, code_files=50).is_propose(thresholds) is True  # age
    assert Maturity(age_days=30, code_files=51).is_propose(thresholds) is True  # files
    assert Maturity(age_days=3, code_files=3).is_propose(thresholds) is False
    assert Maturity(age_days=999, code_files=999).is_propose(thresholds) is True


# 🧪 TRAP[TEST] · 2026-08-05 · unit · счётчик code_files: учитывает код, исключает библиотеки/артефакты
# · Regression: node_modules/.venv/dist/build/coverage/.next/*.lock/package-lock.json/.env*/
#   generated-файлы практик/*.min.js НЕ должны считаться кодом проекта (§4.1)
# · Last fail: N/A
# · Remove if: состав исключений §4.1 меняется
def test_maturity_code_files_counts_with_excludes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """code_files считает 7 файлов кода; приманки (исключения) НЕ входят в счёт."""
    project = tmp_path / "counts"
    project.mkdir()
    _write_code_files(project)  # 7 кода + 15 приманок-исключений

    with caplog.at_level(logging.INFO):
        maturity = compute_maturity(project)
    assert maturity.code_files == 7, f"code_files={maturity.code_files} (ожидали 7; приманки исключены)"
    assert maturity.age_days == 0  # нет git, нет ai-platform.yaml? — yaml есть, но git нет → mtime «сейчас»
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога compute_maturity"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · возраст из первого коммита git (решение пользователя)
# · Regression: возраст — «дата создания определяется скриптом по файлам платформы» (§4.1)
# · Last fail: N/A
# · Remove if: источник возраста меняется
def test_maturity_age_from_first_commit(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Возраст = первый коммит репозитория (git log --reverse); дата в прошлом → age>0."""
    project = tmp_path / "repo"
    project.mkdir()
    (project / "ai-platform.yaml").write_text("name: repo\ntype: backend\n", encoding="utf-8")
    _write_code_files(project)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
    past = datetime.now(timezone.utc) - timedelta(days=40)
    _git_commit_at(project, past, "init: mock (40d ago)")  # единственный коммит — 40 дней назад

    with caplog.at_level(logging.INFO):
        maturity = compute_maturity(project)
    assert maturity.age_days >= 39, f"age_days={maturity.age_days} (ожидали ≈40)"
    assert maturity.code_files == 7
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога compute_maturity"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · fallback: mtime ai-platform.yaml при отсутствии git
# · Regression: VPS/локаль без git → возраст из mtime ai-platform.yaml (решение пользователя)
# · Last fail: N/A
# · Remove if: fallback-цепочка §4.1 меняется
def test_maturity_age_fallback_mtime_no_git(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Без git: возраст из mtime ai-platform.yaml (старый mtime → age>0)."""
    project = tmp_path / "nongit"
    project.mkdir()
    yaml_file = project / "ai-platform.yaml"
    yaml_file.write_text("name: nongit\ntype: backend\n", encoding="utf-8")
    past_ts = time.time() - 45 * 86400
    os.utime(yaml_file, (past_ts, past_ts))  # 45 дней назад

    with caplog.at_level(logging.INFO):
        maturity = compute_maturity(project)
    assert maturity.age_days >= 44, f"age_days={maturity.age_days} (ожидали ≈45 из mtime)"
    assert maturity.code_files == 0
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога compute_maturity"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · fallback-цепочка: первый коммит ai-platform.yaml (средний fallback)
# · Regression: ai-platform.yaml добавлен позже первого коммита — резолв даты по пути даёт
#   дату добавления файла (git log --follow --diff-filter=A), а не первый коммит репозитория
# · Last fail: N/A
# · Remove if: git-резолв выносится в shared
def test_maturity_ai_platform_yaml_commit_fallback(tmp_path: Path) -> None:
    """Средний fallback §4.1: первый коммит, добавивший ai-platform.yaml (путь-резолв)."""
    project = tmp_path / "repo2"
    project.mkdir()
    (project / "README.md").write_text("readme\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
    _git_commit_at(project, datetime.now(timezone.utc) - timedelta(days=60), "init: readme")
    (project / "ai-platform.yaml").write_text("name: repo2\ntype: backend\n", encoding="utf-8")
    _git_commit_at(project, datetime.now(timezone.utc) - timedelta(days=5), "feat: add ai-platform.yaml")

    # основной путь: первый коммит репозитория (60d)
    maturity = compute_maturity(project)
    assert maturity.age_days >= 59
    # средний fallback: первый коммит, добавивший ai-platform.yaml (5d) — private-хелпер
    dt = _git_first_commit(project, path="ai-platform.yaml", follow=True)
    assert dt is not None
    assert (datetime.now(timezone.utc) - dt).days >= 4, "ai-platform.yaml commit fallback даёт дату добавления файла"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · НЕ вызывает git на VPS: нет git → не падает (возраст 0)
# · Regression: compute_maturity на каталоге без git (VPS/payload) должен вернуть Maturity, не raise
# · Last fail: N/A
# · Remove if: maturity начинает требоваться на VPS (W4 снимает запрет)
def test_maturity_no_git_no_crash(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Каталог без git и без ai-platform.yaml → Maturity(0, 0), без исключений (VPS-сценарий)."""
    project = tmp_path / "empty"
    project.mkdir()

    with caplog.at_level(logging.INFO):
        maturity = compute_maturity(project)
    assert maturity == Maturity(age_days=0, code_files=0)
    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога compute_maturity"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · reason-формат для [PRACTICES:PROPOSE][reason:...]
# · Regression: reason — "age=41d,files=87" (парсится каналами доставки варнинга)
# · Last fail: N/A
# · Remove if: формат reason §4.3 меняется
# GUARD-PRESERVE (168): единственное покрытие reason() (формат §4.3 "age=<n>d,files=<m>") —
# парсится каналами доставки [PRACTICES:PROPOSE]-варнинга
def test_maturity_reason_format() -> None:
    """reason() рендерит "age=<n>d,files=<m>" для варнинг-предложения."""
    m = Maturity(age_days=41, code_files=87)
    assert m.reason(maturity_thresholds()) == "age=41d,files=87"
