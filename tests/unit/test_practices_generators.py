"""
# GREP_SUMMARY: test-practices-generators, determinism, generator-hash, GENERATED-header, render-pyproject, render-precommit, render-lock, atomic-write, manual-file-skip
# STRUCTURE: ▶ render determinism (double render byte-identical) → ◇ hash determinism (same in → same out; level change → diff) → ◇ GENERATED headers → ◇ pyproject baseline/full → ◇ precommit upstream-only + pre-push → ◇ lock maturity-snapshot → ◇ manual file skip (atomic)
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/practices/generators.py (DevPlan 137 W1): детерминизм
##           рендеров (гейт байт-сверки двойного рендера), compute_generator_hash (§2.1B),
##           GENERATED-шапка в каждом файле, pyproject baseline vs full (§3.4),
##           pre-commit upstream-only + pre-push K5 (§3.3), practices.lock maturity-снапшот,
##           R5-negative: skip ручного файла (без шапки) не перезаписывает.
## @scope    $TEST_SPEC 137 W1: test_practices_generators (детерминизм, hash).
## @invariants
##   - Native imports (no subprocess для бизнес-логики)
##   - tmp_path для write-тестов (zero hardcode)
##   - LDD: IMP:9-траектория через caplog
##   - R5: negative-тест на ручной файл (skip, не перезапись)
## @rationale  Генераторы — единственный источник GENERATED-файлов; детерминизм = нулевой дрейф.
## @changes  2026-08-05 · DevPlan 137 W1 — создан
##           2026-08-12 · DevPlan 160 W2 T2.3 — test_render_deterministic/test_generator_hash_deterministic
##           УДАЛЕНЫ (дубли test_gate_practices_manifest.py, куда смержены уникальные ассерты)
# endregion MODULE_CONTRACT
"""

import logging
from pathlib import Path

import pytest
import yaml

from core.internal.practices.escalator import evaluate
from core.internal.practices.generators import (
    GENERATED_FILE_PATHS,
    GENERATED_HEADER,
    compute_generator_hash,
    render_conftest,
    render_lock,
    render_precommit,
    render_project_files,
    render_pyproject,
    render_test_health,
    write_generated_file,
)
from core.internal.practices.manifest import load_manifest
from core.internal.practices.maturity import Maturity
from tests.conftest import _print_ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PINS = load_manifest().pins


# 🧪 TRAP[TEST] · 2026-08-05 · unit · GENERATED-шапка в каждом отрендеренном файле
# · Regression: шапка — маркер дрейф-детекта и защиты ручных файлов
# · Last fail: N/A
# · Remove if: маркер GENERATED меняется
def test_generated_header_present() -> None:
    """Каждый отрендеренный файл начинается с GENERATED-шапки."""
    files = render_project_files("demo", "backend", "baseline", _PINS)
    for rel, content in files.items():
        assert content.startswith(GENERATED_HEADER), f"{rel} не имеет GENERATED-шапки"
    assert render_conftest("demo").startswith(GENERATED_HEADER)
    assert render_test_health().startswith(GENERATED_HEADER)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · pyproject baseline vs full (§3.4)
# · Regression: baseline — select=[] (ruff check выключен); full — полный набор правил
# · Last fail: N/A
# · Remove if: §3.4 конфиг ruff меняется
def test_render_pyproject_baseline_vs_full() -> None:
    """BASELINE: select = [] (только format); FULL: полный набор + strict-pytest."""
    baseline = render_pyproject("demo", "backend", "baseline")
    assert "select = []" in baseline
    assert "--strict-markers" not in baseline

    full = render_pyproject("demo", "backend", "full")
    assert '"E"' in full and '"F"' in full and '"RET"' in full
    assert "--strict-markers --strict-config" in full

    # non-python язык → пусто (pyproject не генерируется)
    assert not render_pyproject("demo", "frontend", "baseline")


# 🧪 TRAP[TEST] · 2026-08-05 · unit · pre-commit: только upstream + pre-push K5 (§3.3)
# · Regression: платформенные shell-хуки отклонены аудитом 137 (дубли upstream)
# · Last fail: N/A
# · Remove if: §3.3 рендер pre-commit меняется
def test_render_precommit_upstream_only() -> None:
    """pre-commit содержит только upstream-репозитории + local pre-push K5 (0 core/ путей)."""
    content = render_precommit("baseline", "backend", _PINS)
    assert "https://github.com/pre-commit/pre-commit-hooks" in content
    assert "https://github.com/gitleaks/gitleaks" in content
    assert "https://github.com/compilerla/conventional-pre-commit" in content
    assert "https://github.com/astral-sh/ruff-pre-commit" in content
    # pre-push K5 хук (делегирование make project-check)
    assert "project-push-check" in content
    assert "make project-check" in content
    assert "stages: [pre-push]" in content
    # НЕТ платформенных скриптов (аудит 137: hygiene.sh/commit_msg.sh отклонены —
    # пути core/ в хуках отсутствуют; Source-комментарий — только метаданные)
    assert "core/entrypoints" not in content
    assert "hooks/hygiene.sh" not in content
    assert "hooks/commit_msg.sh" not in content
    # версии из pins (паритет)
    assert _PINS["pre_commit_hooks"] in content
    assert _PINS["gitleaks"] in content
    # sh-only секция (репозиторий shellcheck-py) — только для sh-языка
    assert "https://github.com/shellcheck-py/shellcheck-py" not in render_precommit("baseline", "backend", _PINS)
    assert "https://github.com/shellcheck-py/shellcheck-py" in render_precommit("baseline", "sh", _PINS)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · practices.lock — maturity-снапшот + hash (носитель VPS)
# · Regression: lock должен нести maturity для K3 (на VPS нет git)
# · Last fail: N/A
# · Remove if: формат lock (§2.1B) меняется
def test_render_lock_contains_maturity_snapshot() -> None:
    """render_lock: version/level/state/maturity/generator_hash/files в YAML."""
    manifest = load_manifest()
    files = render_project_files("demo", "backend", "auto", _PINS)
    maturity = Maturity(age_days=41, code_files=87)
    decision = evaluate(maturity, "auto", None)
    content = render_lock(manifest, "auto", decision, maturity, files, "backend", generated_at="2026-08-05T03:00:00Z")
    data = yaml.safe_load(content)
    assert data["version"] == 1
    assert data["level"] == "auto"
    assert data["state"] == "proposed"  # maturity 41d/87 files > пороги → proposed
    assert data["maturity"] == {"age_days": 41, "code_files": 87}
    assert data["generator_hash"].startswith("sha256:")
    assert data["language"] == "backend"
    assert "pyproject.toml" in data["files"]
    # hash сверяем: generator_hash == compute_generator_hash(files, version, level)
    assert data["generator_hash"] == compute_generator_hash(files, manifest.version, "auto")
    assert content.startswith(GENERATED_HEADER)


# 🧪 TRAP[TEST] · 2026-08-05 · unit · R5-negative: ручной файл (без шапки) НЕ перезаписывается
# · Regression: перезапись пользовательского pyproject — потеря кода (риск §7)
# · Last fail: N/A (negative-тест на новый код)
# · Remove if: политика skip ручных файлов меняется
def test_write_generated_file_skips_manual(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Существующий файл без GENERATED-шапки → skip + содержимое сохранено; force → перезапись."""
    target = tmp_path / "pyproject.toml"
    manual = "[tool.ruff]\nline-length = 100  # мой конфиг\n"
    target.write_text(manual, encoding="utf-8")

    rendered = render_pyproject("demo", "backend", "baseline")
    with caplog.at_level(logging.INFO):
        status = write_generated_file(target, rendered, force=False)
    assert status == "skipped"
    assert target.read_text(encoding="utf-8") == manual  # пользовательский файл цел

    status_force = write_generated_file(target, rendered, force=True)
    assert status_force == "updated"
    assert target.read_text(encoding="utf-8") == rendered

    assert _print_ldd_trajectory(caplog), "LDD: нет IMP:9 лога записи"


# 🧪 TRAP[TEST] · 2026-08-05 · unit · GENERATED_FILE_PATHS — фиксированный набор (5 файлов)
# · Regression: состав GENERATED-файлов — контракт AC W1 (5 файлов)
# · Last fail: N/A
# · Remove if: состав файлов практик меняется
# GUARD-PRESERVE (168): единственное покрытие состава GENERATED_FILE_PATHS (контракт AC W1,
# 5 GENERATED-файлов) — носитель inventory/drift-детекта GENERATED-практик
def test_generated_file_paths_set() -> None:
    """GENERATED_FILE_PATHS перечисляет все 5 GENERATED-файлов (AC W1)."""
    assert set(GENERATED_FILE_PATHS) == {
        "pyproject.toml",
        ".pre-commit-config.yaml",
        "tests/conftest.py",
        "tests/test_health.py",
        "practices.lock",
    }


# 🧪 TRAP[TEST] · 2026-08-16 · unit · static-профиль test_health (аудит релиза 1.0.0)
# · Regression: frontend-статики (раздаёт nginx платформы, НЕТ /health//ready) —
# ·   generic test_health латентно падал (404 при поднятом nginx); static → TCP-probe только
# · Last fail: type=frontend + nginx up → GET /health = 404 → FAIL
# · Remove if: static-профиль в render_test_health убирается
def test_render_test_health_static_profile() -> None:
    """Static-профиль (frontend/typescript/react): TCP-probe до nginx, без GET /health//ready."""
    static = render_test_health("frontend")
    assert "test_nginx_reachable" in static
    assert "PLATFORM_NGINX_HOST" in static and "PLATFORM_NGINX_PORT" in static
    assert "socket.socket" in static, "static-профиль обязан делать TCP-probe"
    # generic-GET-механика отсутствует (GET по /health//ready → 404 на статике)
    assert "_get_status" not in static and "urllib" not in static, "static: без GET-проверок"
    assert "pytest.skip" in static, "static: skip при недоступном nginx (AC1: свежий мок не падает)"

    for ptype in ("typescript", "react"):
        assert "test_nginx_reachable" in render_test_health(ptype), f"{ptype} должен давать static-профиль"
    # backend/python/"" → generic (собственный HTTP-сервис: /health+/ready)
    generic = render_test_health("backend")
    assert "/health" in generic and "/ready" in generic and "urllib" in generic
    assert render_test_health() == render_test_health("backend"), "'' и backend — один generic-профиль"


# 🧪 TRAP[TEST] · 2026-08-16 · unit · static-профиль conftest (без health_url фикстуры)
# · Regression: static-профиль не рендерит health_url (нет /health+/ready — фикстура мертва)
# · Last fail: N/A (new test)
# · Remove if: static-профиль conftest меняется
def test_render_conftest_static_profile() -> None:
    """Static-профиль conftest: env-loading без health_url фикстуры (TCP-probe в test_health)."""
    static = render_conftest("demo", "frontend")
    assert "def _load_env_platform" in static
    assert "def health_url" not in static, "static: health_url фикстура не рендерится"
    assert "import socket" not in static, "static conftest: socket не нужен (probe в test_health.py)"
    assert "import pytest" not in static, "static conftest: pytest-импорт не нужен (нет фикстуры)"

    generic = render_conftest("demo")
    assert "def health_url" in generic
    assert "pytest.skip" in generic


# 🧪 TRAP[TEST] · 2026-08-16 · unit · render_project_files пробрасывает project_type в профиль
# · Regression: frontend-проект получает static test_health через render_project_files
# · Last fail: N/A (new test)
# · Remove if: проброс project_type в render_project_files убирается
def test_render_project_files_static_profile_threaded() -> None:
    """render_project_files(project_type=...) → test_health/conftest static для frontend."""
    static_files = render_project_files("demo", "typescript", "baseline", _PINS, project_type="frontend")
    assert "test_nginx_reachable" in static_files["tests/test_health.py"]
    assert "def health_url" not in static_files["tests/conftest.py"]
    # без pyproject (typescript-язык не python-семейство)
    assert "pyproject.toml" not in static_files

    backend_files = render_project_files("demo", "python", "baseline", _PINS, project_type="backend")
    assert "/health" in backend_files["tests/test_health.py"]
    assert "def health_url" in backend_files["tests/conftest.py"]

    # дефолт (project_type не передан) — обратная совместимость: generic-профиль
    default_files = render_project_files("demo", "typescript", "baseline", _PINS)
    assert "/health" in default_files["tests/test_health.py"]
