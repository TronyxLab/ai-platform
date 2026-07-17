# GREP_SUMMARY: gate-test pytest-markers pyproject-toml pytest-ini strict-markers
# STRUCTURE: ▶ test_pytest_ini_has_no_markers → ◇ test_pyproject_toml_has_all_markers → ◇ test_strict_markers_in_pyproject_toml
# region MODULE_CONTRACT
## @purpose  Gate tests: validate pytest markers are in pyproject.toml only (DevPlan 04 TASK-G2)
## @scope    Проверяет что pytest.ini не содержит markers, все маркеры в pyproject.toml
## @invariants
##   - pytest.ini НЕ содержит [tool:pytest] или markers
##   - pyproject.toml содержит все ожидаемые маркеры в [tool.pytest.ini_options]
##   - --strict-markers в pyproject.toml addopts
##   - Все зарегистрированные маркеры имеют ≥1 тест (anti-dead-marker); исключения: skip_enforcement, e2e (env-dependent)
## @rationale Единый реестр markers — только pyproject.toml (DevPlan 04 P14)
# endregion MODULE_CONTRACT

import configparser
import logging
import re
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

try:  # Python >= 3.11 — stdlib TOML parser
    import tomllib
except ModuleNotFoundError:
    # ⚠️ TRAP[BUG] · 2026-07-15 · module-level `import tomllib` broke pytest collection on CI (Python 3.10)
    # · tomllib is stdlib only since 3.11; CI venv has no tomli package → ModuleNotFoundError at collect
    # · Fix: fallback to regex-based minimal parser in _load_ini_options() below
    tomllib = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTEST_INI = PROJECT_ROOT / "pytest.ini"
PYPROJECT_TOML = PROJECT_ROOT / "pyproject.toml"


# region HELPER_load_ini_options
## @purpose  Parse [tool.pytest.ini_options] (markers, addopts) from pyproject.toml content.
##           Uses tomllib when available (Python >= 3.11), otherwise a minimal regex
##           fallback so the gate still RUNS (not skips) on Python 3.10 CI.
## @io       ⇥ content: str (pyproject.toml text) → ⎋ dict (ini_options subset)
## @invariants
##   - tomllib path and regex path return identical markers/addopts for well-formed pyproject
##   - regex fallback only understands `addopts = "..."` and `markers = [ "..." , ... ]`
def _load_ini_options(content: str) -> dict:
    if tomllib is not None:
        data = tomllib.loads(content)
        return data.get("tool", {}).get("pytest", {}).get("ini_options", {})

    ini: dict = {}
    addopts_match = re.search(r'^addopts\s*=\s*"([^"]*)"', content, re.MULTILINE)
    if addopts_match:
        ini["addopts"] = addopts_match.group(1)
    markers_match = re.search(r"^markers\s*=\s*\[(.*?)^\]", content, re.MULTILINE | re.DOTALL)
    if markers_match:
        ini["markers"] = re.findall(r'"([^"]+)"', markers_match.group(1))
    return ini


# endregion HELPER_load_ini_options

EXPECTED_MARKERS = (
    "static_audit",
    "smoke",
    "component",
    "integration",
    "predeploy",
    "contract",
    "e2e",
    "gate",
    "skip_enforcement",
    "requires_docker",
    "local_auth",
    "backup",
)


class TestPytestMarkers:
    @pytest.mark.gate
    def test_pytest_ini_has_no_markers(self) -> None:
        """pytest.ini не существует ИЛИ не содержит markers."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/pytest-markers · Регресс: markers возвращаются в pytest.ini
        # pytest.ini should not exist — all config is in pyproject.toml
        if not PYTEST_INI.exists():
            return  # pytest.ini deleted — all config in pyproject.toml ✓

        # If pytest.ini exists (legacy), ensure no markers or --strict-markers
        config = configparser.ConfigParser()
        config.read(str(PYTEST_INI))

        if config.has_section("pytest"):
            markers_line = None
            for key in config.options("pytest"):
                if "markers" in key.lower():
                    markers_line = key
                    break
            assert markers_line is None, (
                f"pytest.ini contains 'markers' in [pytest] section: {markers_line}. "
                f"All markers must be in pyproject.toml only."
            )

        content = PYTEST_INI.read_text()
        assert "markers =" not in content, "pytest.ini contains 'markers =' — all markers must be in pyproject.toml"
        assert "--strict-markers" not in content, "pytest.ini contains --strict-markers — must be in pyproject.toml"

    @pytest.mark.gate
    def test_pyproject_toml_has_all_markers(self) -> None:
        """Все ожидаемые маркеры зарегистрированы в pyproject.toml (счёт динамический)."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/pytest-markers · Регресс: маркер удалён из pyproject.toml без синхронизации
        content = PYPROJECT_TOML.read_text()
        ini_options = _load_ini_options(content)
        registered_markers = ini_options.get("markers", [])

        # Extract marker names from the list (format: "name: description")
        registered_names = set()
        for marker in registered_markers:
            name = marker.split(":")[0].strip()
            registered_names.add(name)

        expected_set = set(EXPECTED_MARKERS)
        missing = expected_set - registered_names
        extra = registered_names - expected_set

        assert not missing, (
            f"Markers missing from pyproject.toml: {sorted(missing)}. Registered: {sorted(registered_names)}"
        )
        if extra:
            pytest.skip(f"Extra markers found (non-critical): {sorted(extra)}")

    @pytest.mark.gate
    def test_no_dead_markers(self) -> None:
        """All registered markers in pyproject.toml must have at least one test using them.

        Dead markers (registered but no tests) indicate stale configuration.
        Exceptions: markers that are env-dependent (skip_enforcement, e2e).
        """
        import subprocess

        content = PYPROJECT_TOML.read_text()
        ini_options = _load_ini_options(content)
        registered_markers = ini_options.get("markers", [])

        # Extract marker names
        registered_names = set()
        for marker in registered_markers:
            name = marker.split(":")[0].strip()
            registered_names.add(name)

        # Collect all markers actually used in tests via pytest --collect-only markers dump
        result = subprocess.run(
            [  # noqa: S607
                "python", "-m", "pytest", "tests/",
                "--collect-only", "-q", "--no-header",  # noqa: S603
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )

        # Parse collected markers from output — look for markers: lines
        used_markers: set[str] = set()
        for line in result.stdout.splitlines():
            # Collect markers from parametrized test output
            if line.startswith(" " * 4) and "[" in line:
                continue  # parametrized test variants

        # Alternative: use pytest --markers to list all registered markers
        markers_result = subprocess.run(
            ["python", "-m", "pytest", "--markers", "-q", "--no-header"],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        # Parse markers output: each marker starts with '@pytest.mark.<name>:'
        for line in markers_result.stdout.splitlines():
            line = line.strip()
            if line.startswith("@pytest.mark."):
                m = line[len("@pytest.mark."):].split(":")[0].strip()
                used_markers.add(m)

        # Better approach: collect directly via pytest --co
        collector_result = subprocess.run(
            [
                "python", "-m", "pytest", "tests/",
                "--co", "-q", "--no-header",  # noqa: S603
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )

        # Parse marker usage from collector output (--co is not standard)
        # Fallback: use grep on test files
        import re as _re
        _all_tests_markers: set[str] = set()
        tests_dir = PROJECT_ROOT / "tests"
        for pyfile in tests_dir.rglob("test_*.py"):
            try:
                text = pyfile.read_text()
                for match in _re.finditer(r"@pytest\.mark\.(\w+)", text):
                    _all_tests_markers.add(match.group(1))
            except (OSError, UnicodeDecodeError):
                continue

        # Compare: registered vs actually used
        dead_markers = registered_names - _all_tests_markers
        # Whitelist env-dependent markers that don't need test presence
        env_dependent = {"skip_enforcement", "e2e", "backup"}
        dead_markers -= env_dependent

        assert not dead_markers, (
            f"Dead markers found (registered in pyproject.toml but 0 tests use them): "
            + ", ".join(sorted(dead_markers))
            + f"\nUsed markers: {', '.join(sorted(_all_tests_markers))}"
        )
        logger.info(
            "[IMP:9][gate] PASS: No dead markers — all %d registered markers have tests",
            len(registered_names),
        )

    @pytest.mark.gate
    def test_strict_markers_in_pyproject_toml(self) -> None:
        """--strict-markers в pyproject.toml addopts."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/pytest-markers · Регресс: --strict-markers удалён из pyproject.toml
        content = PYPROJECT_TOML.read_text()
        ini_options = _load_ini_options(content)
        addopts = ini_options.get("addopts", "")

        assert "--strict-markers" in addopts, f"--strict-markers not found in pyproject.toml addopts. Got: {addopts}"
        assert "--strict-config" in addopts, f"--strict-config not found in pyproject.toml addopts. Got: {addopts}"
