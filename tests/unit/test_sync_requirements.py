"""
# GREP_SUMMARY: test_sync_requirements, load_dependencies, generate_requirements, check_mode, pyproject-dependencies, tmp_path, requirements.txt
# STRUCTURE: ▶ generate (probe pyproject → 8 pkgs + header) → ▶ check identical (rc 0) → ▶ check divergence (rc 1) → ▶ missing section (rc 2) → ▶ order preserved → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for sync_requirements.py (DevPlan 123 T11 FL7) — load_dependencies(),
##           generate_requirements(), and main() --check mode. Native imports, no subprocess.
## @scope    Tests [project].dependencies parsing from pyproject.toml (tomllib), requirements.txt
##           generation (8 runtime pkgs + GENERATED header), byte-level --check (0 identical /
##           1 divergent), fail-fast on missing section (exit 2), and SoT order preservation.
## @invariants
##   - All tests import the module directly via sys.path.insert (tests/AGENTS.md module-specific paths)
##   - Each test decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for probe pyproject.toml and output requirements.txt (zero hardcoded paths)
##   - Dev-only packages (requests, python-dotenv) и транзитивный httpcore НЕ попадают в output
## @rationale DevPlan 123 T11 (FL7): единый SoT runtime-зависимостей — pyproject.toml
##            [project].dependencies → requirements.txt генерируется. Тест фиксирует контракт
##            генератора (8 пакетов + header) и --check семантику (по образцу sync_env_defaults).
## @changes 2026-08-03 | Created (DevPlan 123 T11)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (tests/AGENTS.md: core/internal/scripts — module-specific path) ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import sync_requirements as sr

# ═══════════════════════════════════════════════════════════════════
# region Helpers / fixtures
# ═══════════════════════════════════════════════════════════════════

# 8 runtime-зависимостей — зеркало реального [project].dependencies (pyproject.toml L28-37).
_PROBE_DEPENDENCIES = [
    "boto3>=1.28.0",
    "botocore>=1.31.0",
    "cryptography>=41.0.0",
    "httpx>=0.27.0",
    "jinja2>=3.1.0",
    "jsonschema>=4.17.0",
    "pydantic>=2.0.0",
    "pyyaml>=6.0",
]

# Dev-only / transitive пакеты, которые НЕ должны попадать в requirements.txt (T11).
_FORBIDDEN_PACKAGES = ["requests", "python-dotenv", "httpcore"]


def _write_probe_pyproject(tmp_path: Path, dependencies: list[str] | None = None) -> Path:
    """Write a minimal pyproject.toml probe with [project].dependencies into tmp_path."""
    deps = _PROBE_DEPENDENCIES if dependencies is None else dependencies
    lines = ["[project]", 'name = "probe"', 'version = "0.0.1"', "dependencies = ["]
    lines.extend(f'    "{d}",' for d in deps)
    lines.append("]")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pyproject


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: generation
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · sync_requirements генерирует requirements.txt из probe-pyproject
# · Scenario: probe pyproject с 8 runtime-зависимостями → main() → rc 0, файл: header + 8 пакетов,
# ·           dev/транзитивные (requests, python-dotenv, httpcore) отсутствуют
# · Last fail: N/A (new test, DevPlan 123 T11)
# · Remove if: формат requirements.txt (header/порядок) меняется
@ldd_trajectory
def test_generates_requirements_file(caplog, tmp_path):
    """main() без --check пишет requirements.txt: GENERATED header + 8 пакетов."""
    pyproject = _write_probe_pyproject(tmp_path)
    output = tmp_path / "requirements.txt"

    rc = sr.main(["--pyproject", str(pyproject), "--output", str(output)])

    assert rc == 0, f"main() returned {rc}, expected 0"
    assert output.is_file(), "requirements.txt not written"
    content = output.read_text(encoding="utf-8")

    # Header-контракт (инвариант 11, DevPlan 123 T11)
    assert "GENERATED from pyproject.toml [project].dependencies" in content
    assert "НЕ редактировать вручную" in content
    assert "DevPlan 123 T11" in content

    # Ровно 8 runtime-пакетов, по одному на строку, порядок = SoT
    pkg_lines = [ln for ln in content.splitlines() if not ln.startswith("#") and ln.strip()]
    assert pkg_lines == _PROBE_DEPENDENCIES, f"Package lines mismatch: {pkg_lines}"

    # Dev-only / транзитивные пакеты НЕ попадают (T11: requests/dotenv — dev extra, httpcore — транзитив httpx)
    for forbidden in _FORBIDDEN_PACKAGES:
        assert forbidden not in content, f"Dev/transitive package {forbidden} leaked into requirements.txt"

    logger.critical("[IMP:9][test] generate: %d runtime pkgs + GENERATED header written", len(pkg_lines))


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: --check mode
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · --check: идентичный файл → exit 0
# · Scenario: requirements.txt сгенерирован генератором → повторный main() с --check → rc 0
# · Last fail: N/A (new test, DevPlan 123 T11)
# · Remove if: check mode logic in main() changes
@ldd_trajectory
def test_check_identical_returns_zero(caplog, tmp_path):
    """--check с актуальным файлом → exit 0 (byte-level сравнение)."""
    pyproject = _write_probe_pyproject(tmp_path)
    output = tmp_path / "requirements.txt"
    assert sr.main(["--pyproject", str(pyproject), "--output", str(output)]) == 0

    rc = sr.main(["--pyproject", str(pyproject), "--output", str(output), "--check"])

    assert rc == 0, f"--check returned {rc}, expected 0 for identical file"
    logger.critical("[IMP:9][test] check identical: byte-level compare → rc 0")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · --check: изменённый файл → exit 1 + diff в stderr
# · Scenario: ручная правка requirements.txt (добавлен лишний пакет) → --check → rc 1
# · Last fail: N/A (new test, DevPlan 123 T11) — ловит ручное редактирование generated-файла
# · Remove if: check mode logic in main() changes
@ldd_trajectory
def test_check_divergence_returns_one(caplog, tmp_path):
    """--check с изменённым файлом → exit 1 (ручная правка generated-файла детектируется)."""
    pyproject = _write_probe_pyproject(tmp_path)
    output = tmp_path / "requirements.txt"
    assert sr.main(["--pyproject", str(pyproject), "--output", str(output)]) == 0

    # Имитация ручной правки — добавляем легаси-пакет (как было ДО T11)
    output.write_text(output.read_text(encoding="utf-8") + "httpcore>=1.0.0\n", encoding="utf-8")

    rc = sr.main(["--pyproject", str(pyproject), "--output", str(output), "--check"])

    assert rc == 1, f"--check returned {rc}, expected 1 for divergent file"
    logger.critical("[IMP:9][test] check divergence: ручная правка детектирована → rc 1")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: fail-fast
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · отсутствие [project].dependencies → exit 2 (fail-fast)
# · Scenario: probe pyproject без секции dependencies → main() → rc 2 (usage error)
# · Last fail: N/A (new test, DevPlan 123 T11)
# · Remove if: fail-fast semantics change
@ldd_trajectory
def test_missing_dependencies_section_fails(caplog, tmp_path):
    """[project] без dependencies → exit 2 (SoT отсутствует — громкий fail, не пустой список)."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "probe"\nversion = "0.0.1"\n', encoding="utf-8")
    output = tmp_path / "requirements.txt"

    rc = sr.main(["--pyproject", str(pyproject), "--output", str(output)])

    assert rc == 2, f"main() returned {rc}, expected 2 (usage error)"
    assert not output.exists(), "requirements.txt must not be written on fail-fast"
    logger.critical("[IMP:9][test] fail-fast: отсутствие [project].dependencies → rc 2")


# 🧪 TRAP[TEST] · Regression · отсутствующий pyproject.toml → exit 2
# · Scenario: несуществующий путь pyproject → main() → rc 2 (fail-fast до парсинга)
# · Last fail: N/A (new test, DevPlan 123 T11)
# · Remove if: fail-fast semantics change
@ldd_trajectory
def test_missing_pyproject_fails(caplog, tmp_path):
    """Несуществующий pyproject.toml → exit 2 (fail-fast, явная ошибка)."""
    output = tmp_path / "requirements.txt"

    rc = sr.main(["--pyproject", str(tmp_path / "nope.toml"), "--output", str(output)])

    assert rc == 2, f"main() returned {rc}, expected 2"
    logger.critical("[IMP:9][test] fail-fast: отсутствующий pyproject.toml → rc 2")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: SoT order + render
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · порядок пакетов сохраняется из pyproject.toml
# · Scenario: load_dependencies возвращает deps в порядке объявления; generate_requirements
# ·           рендерит их 1:1 (алфавитный порядок SoT — детерминированный вывод)
# · Last fail: N/A (new test, DevPlan 123 T11)
# · Remove if: load_dependencies/generate_requirements contract changes
@ldd_trajectory
def test_load_dependencies_order_preserved(caplog, tmp_path):
    """load_dependencies сохраняет порядок pyproject.toml; generate_requirements рендерит без сортировки."""
    pyproject = _write_probe_pyproject(tmp_path)

    deps = sr.load_dependencies(pyproject)
    assert deps == _PROBE_DEPENDENCIES, f"Order not preserved: {deps}"

    rendered = sr.generate_requirements(deps)
    assert rendered.startswith(sr.GENERATED_HEADER)
    for d in deps:
        assert f"\n{d}\n" in rendered or rendered.endswith(f"{d}\n")
    assert rendered.endswith("\n"), "Trailing newline required (byte-level --check contract)"

    logger.critical("[IMP:9][test] SoT order preserved: %d deps rendered 1:1", len(deps))


# endregion
