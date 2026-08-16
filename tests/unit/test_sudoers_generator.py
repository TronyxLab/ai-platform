# GREP_SUMMARY: test sudoers-generator visudo template-render role-mapping batch-sudoers
# STRUCTURE: ┌6 test scenarios┐ → ◇ role→username mapping → ◇ rendered line parsing → ◇ real template render (sudoers_dir DI) → ◇ visudo validation → ◇ atomic write (validator param) → ◇ batch generation (effect asserts)
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/deploy/sudoers_generator.py
## @scope    Tests: _map_role_to_username, _parse_rendered_lines, render_sudoers_rules,
##           _validate_with_visudo, _write_sudoers_file, generate_module_sudoers, batch_generate_sudoers
## @invariants
##   - subprocess.run is mocked ONLY for _validate_with_visudo (visudo contract) — render/write
##     идут РЕАЛЬНЫМ конвейером (E3, DevPlan 160): templates_dir fixture + sudoers_dir= override
##   - temp files use tmp_path fixture (no hardcoded paths)
##   - Each test function includes caplog-based LDD trajectory [IMP:7-10]
##   - Every test function has # 🧪 TRAP[TEST] with Regression/Scenario/Last fail/Remove if
## @changes 2026-08-13 | DevPlan 160 E3 — generate/batch через sudoers_dir= (реальный write в tmp,
##           ассерты на СОДЕРЖИМОЕ sudoers-файла, а не мок-вызовы); _write_sudoers_file через
##           validator-параметр (0 patch); render-tests на реальном шаблоне (0 monkeypatch _render_template)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Module under test
import core.internal.bootstrap.deploy.sudoers_generator as sg
from core.internal.bootstrap.deploy.sudoers_generator import (
    _MAKE_BIN,
    _map_role_to_username,
    _parse_rendered_lines,
    _validate_with_visudo,
    _write_sudoers_file,
    batch_generate_sudoers,
    generate_module_sudoers,
    render_sudoers_rules,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_rendered_text() -> str:
    """Produce realistic rendered template output as produced by template_engine.render_template."""
    return """# AUTO-GENERATED — do not edit directly.
# Module: test-module

# owner — full control (ALL make targets)
owner make:start    /opt/platform/core/modules/test-module/Makefile
owner make:stop     /opt/platform/core/modules/test-module/Makefile
owner make:restart  /opt/platform/core/modules/test-module/Makefile
owner make:status   /opt/platform/core/modules/test-module/Makefile
owner make:logs     /opt/platform/core/modules/test-module/Makefile
owner make:backup   /opt/platform/core/modules/test-module/Makefile

# agent — status, logs, restart, backup — NO stop
agent make:status   /opt/platform/core/modules/test-module/Makefile
agent make:logs     /opt/platform/core/modules/test-module/Makefile
agent make:restart  /opt/platform/core/modules/test-module/Makefile
agent make:backup   /opt/platform/core/modules/test-module/Makefile

# ci — start, stop, restart, status, logs
ci make:start       /opt/platform/core/modules/test-module/Makefile
ci make:stop        /opt/platform/core/modules/test-module/Makefile
ci make:restart     /opt/platform/core/modules/test-module/Makefile
ci make:status      /opt/platform/core/modules/test-module/Makefile
ci make:logs        /opt/platform/core/modules/test-module/Makefile

# monitor — read-only status and logs
monitor make:status /opt/platform/core/modules/test-module/Makefile
monitor make:logs   /opt/platform/core/modules/test-module/Makefile
"""


@pytest.fixture
def sample_rules_expected() -> list[str]:
    """Expected parsed rules from sample_rendered_text (with {MODULE_DIR} placeholder)."""
    make = _MAKE_BIN
    return [
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} start",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} stop",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} backup",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"platform-agent ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} backup",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} start",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} stop",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} restart",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"ci-deploy ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
        f"platform-monitor ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} status",
        f"platform-monitor ALL=(root) NOPASSWD: {make} -C {{MODULE_DIR}} logs",
    ]


@pytest.fixture
def modules_dir(tmp_path: Path) -> Path:
    """Create a temporary modules directory with a test-module subdirectory."""
    mods = tmp_path / "modules"
    mods.mkdir(parents=True)
    (mods / "test-module").mkdir(parents=True)
    return mods


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create a temporary templates directory with sudo-whitelist.template."""
    tmpl = tmp_path / "templates"
    tmpl.mkdir(parents=True)
    tmpl_file = tmpl / "sudo-whitelist.template"
    tmpl_file.write_text("""# Module: {{MODULE_NAME}}
owner make:start    {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:stop     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:restart  {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:status   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
owner make:logs     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
agent make:status   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
agent make:logs     {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
ci make:start       {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
ci make:stop        {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
monitor make:status {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
monitor make:logs   {{PLATFORM_ROOT}}/core/modules/{{MODULE_NAME}}/Makefile
""")
    return tmpl


# ── LDD trajectory helper ──────────────────────────────────────────────────


def _assert_ldd_trajectory(caplog: pytest.LogCaptureFixture) -> None:
    """Print IMP:7-10 log trajectory and assert at least one IMP:9 log is present."""

    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# ── Tests: _map_role_to_username ────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: role→username mapping · Scenario: known roles map to correct usernames;
#   unknown/empty roles pass-through as-is · Last fail: N/A · Remove if: role map is removed or redefined
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        pytest.param("owner", "platform", id="owner"),
        pytest.param("agent", "platform-agent", id="agent"),
        pytest.param("ci", "ci-deploy", id="ci"),
        pytest.param("monitor", "platform-monitor", id="monitor"),
        pytest.param("custom-role", "custom-role", id="unknown-pass-through"),
        pytest.param("", "", id="empty-pass-through"),
    ],
)
def test_map_role_to_username(caplog: pytest.LogCaptureFixture, role: str, expected: str) -> None:
    """All known roles map to the correct system usernames; unknown/empty pass-through."""
    caplog.set_level(logging.DEBUG)

    result = _map_role_to_username(role)
    assert result == expected

    _assert_ldd_trajectory(caplog)


# ── Tests: _parse_rendered_lines ────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: parse rendered template · Scenario: full template with all roles → all rules · Last fail: N/A · Remove if: parsing logic is replaced
def test_parse_rendered_lines_full(
    caplog: pytest.LogCaptureFixture,
    sample_rendered_text: str,
    sample_rules_expected: list[str],
) -> None:
    """Full rendered text produces correct sudoers rules for all roles."""
    caplog.set_level(logging.DEBUG)

    rules = _parse_rendered_lines(sample_rendered_text)

    assert len(rules) == len(sample_rules_expected)
    for expected, actual in zip(sample_rules_expected, rules, strict=False):
        assert actual == expected, f"Mismatch: expected={expected!r}, got={actual!r}"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: skip comments/blanks/non-make/malformed lines · Scenario: только
#   валидные make-строки дают правила; комментарии, пустые, non-make и malformed — отфильтрованы
# · Last fail: N/A · Remove if: parsing/filtering logic is replaced
@pytest.mark.parametrize(
    ("text", "expected_len", "expected_rules"),
    [
        pytest.param(
            """owner make:start /path/Makefile
owner docker:exec /path
owner ALL /path
agent make:status /path/Makefile
""",
            2,
            [],
            id="skips-non-make-actions",
        ),
        pytest.param(
            """owner
 make:start /path/Makefile

owner make:valid /path/Makefile
""",
            1,
            [],
            id="malformed-skipped",
        ),
        pytest.param(
            """
# This is a comment
# Another comment

owner make:start /path/Makefile

# Section header
agent make:status /path/Makefile


# Trailing comment
""",
            2,
            [
                "platform ALL=(root) NOPASSWD: /usr/bin/make -C {MODULE_DIR} start",
                "platform-agent ALL=(root) NOPASSWD: /usr/bin/make -C {MODULE_DIR} status",
            ],
            id="skips-comments-and-blanks",
        ),
    ],
)
def test_parse_rendered_lines_filters(
    caplog: pytest.LogCaptureFixture,
    text: str,
    expected_len: int,
    expected_rules: list[str],
) -> None:
    """Comment/blank/non-make/malformed lines are skipped; only valid make: lines produce rules."""
    caplog.set_level(logging.DEBUG)

    rules = _parse_rendered_lines(text)

    assert len(rules) == expected_len
    for rule in expected_rules:
        assert rule in rules

    _assert_ldd_trajectory(caplog)


# ── Tests: render_sudoers_rules ────────────────────────────────────────────


# 📝 TRAP[DEBT] · 2026-08-14 · MED · 5 тестов не собираются pytest из-за отсутствия "_" после "test"
# · Observed: testrender_sudoers_rules, testrender_sudoers_rules_template_failure,
# ·   testbatch_generate_sudoers_{ok,no_modules,all_fail} отсутствуют в `pytest --collect-only`
# ·   (подтверждено при F5-редукции 168 Batch 5; python_functions="test_*" в pyproject.toml:95)
# · Suspected: историческая опечатка имён (testrender_/testbatch_ вместо test_render_/test_batch_) —
# ·   функции выглядят как выполняющиеся тесты, но молча НЕ собираются (R1 honesty)
# · Impact: ложно-положительное покрытие render_sudoers_rules/batch_generate_sudoers (4 ветки без
# ·   реального прогона).
# · When: 2026-08-15 DevPlan 171 W2.2 — переименованы в test_-префикс.


# 🧪 TRAP[TEST] · Regression: render rules with real template · Scenario: real _render_template on
#   templates_dir fixture (sudo-whitelist.template) → parsed + resolved rules · Last fail: N/A
# · Remove if: render_sudoers_rules signature changes
def test_render_sudoers_rules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
) -> None:
    """render_sudoers_rules renders template (REAL render), parses, and resolves MODULE_DIR."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): РЕАЛЬНЫЙ рендер через templates_dir fixture (шаблон существует) —
    # 0 monkeypatch _render_template (паттерн test_render_template_native_render)
    rules = render_sudoers_rules(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert len(rules) > 0
    # Verify MODULE_DIR placeholder is resolved to the actual module path
    module_abs_dir = str((modules_dir / "test-module").resolve())
    for rule in rules:
        assert "{MODULE_DIR}" not in rule, f"Unresolved placeholder in: {rule}"
        assert module_abs_dir in rule, f"Module path not resolved in: {rule}"

    # Verify all roles present
    rule_text = " ".join(rules)
    assert "platform ALL=(root)" in rule_text
    assert "platform-agent ALL=(root)" in rule_text
    assert "ci-deploy ALL=(root)" in rule_text
    assert "platform-monitor ALL=(root)" in rule_text

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: render rules with missing template · Scenario: templates_dir БЕЗ
#   sudo-whitelist.template → _render_template возвращает None → render_sudoers_rules → []
# · Last fail: N/A · Remove if: error handling logic changes
def test_render_sudoers_rules_template_failure(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    tmp_path: Path,
) -> None:
    """When template is missing, render_sudoers_rules returns empty list (real render path)."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): реальный сбой рендера — пустая templates-директория (0 monkeypatch _render_template)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()

    rules = render_sudoers_rules(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=empty_templates,
        platform_root="/opt/platform",
    )

    assert rules == []

    _assert_ldd_trajectory(caplog)


# ── Tests: _validate_with_visudo ─────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: visudo validation passes · Scenario: subprocess.run returns 0 → True · Last fail: N/A · Remove if: validation logic is replaced
def test_validate_with_visudo_ok(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo -c returns 0, _validate_with_visudo returns True."""
    caplog.set_level(logging.DEBUG)

    with patch("core.internal.bootstrap.deploy.sudoers_generator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is True
        mock_run.assert_called_once_with(
            ["visudo", "-c", "-f", "/tmp/test-sudoers"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo validation fails · Scenario: subprocess.run returns 1 → False · Last fail: N/A · Remove if: validation error handling changes
def test_validate_with_visudo_fail(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo -c returns non-zero, _validate_with_visudo returns False."""
    caplog.set_level(logging.DEBUG)

    with patch("core.internal.bootstrap.deploy.sudoers_generator.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr=">>> sudoers parse error: line 3",
        )

        result = _validate_with_visudo("/tmp/bad-sudoers")

        assert result is False

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo not found · Scenario: FileNotFoundError → True (pass-through) · Last fail: N/A · Remove if: fallback behavior changes
def test_validate_with_visudo_not_found(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo binary is not found, returns True (pass-through for dev/test)."""
    caplog.set_level(logging.DEBUG)

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator.subprocess.run",
        side_effect=FileNotFoundError("visudo not found"),
    ):
        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is True

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: visudo timeout · Scenario: TimeoutExpired → False · Last fail: N/A · Remove if: timeout handling changes
def test_validate_with_visudo_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """When visudo times out, returns False."""
    caplog.set_level(logging.DEBUG)

    with patch(
        "core.internal.bootstrap.deploy.sudoers_generator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="visudo", timeout=15),
    ):
        result = _validate_with_visudo("/tmp/test-sudoers")

        assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: _write_sudoers_file ──────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: write + validate + atomic rename · Scenario: validator passes → file written with correct content and mode · Last fail: N/A · Remove if: _write_sudoers_file logic changes
def test_write_sudoers_file_ok(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """_write_sudoers_file writes, validates, and atomically renames sudoers file."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-test-module"
    rules = [
        "platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test start",
        "agent ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test status",
    ]

    # E3 (160): validator — параметр (DI), 0 patch(_validate_with_visudo)
    result = _write_sudoers_file(target, rules, "test-module", validator=lambda _: True)

    assert result is True
    assert target.exists(), "Target sudoers file was not created"

    # Verify content
    content = target.read_text()
    assert "# platform module sudoers — test-module" in content
    assert "platform ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test start" in content
    assert "agent ALL=(root) NOPASSWD: /usr/bin/make -C /opt/modules/test status" in content

    # Verify mode 0440 (owner read only) — on macOS tmp_path, root:root may not apply
    mode = Path(target).stat().st_mode & 0o777
    assert mode == 0o440, f"Expected 0440, got {oct(mode)}"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: write fails visudo → no file · Scenario: validator fails → no target file created · Last fail: N/A · Remove if: error handling changes
def test_write_sudoers_file_visudo_fail(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """When visudo validation fails, no sudoers file is written."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-test-module"
    rules = ["bad rule syntax"]

    # E3 (160): validator — параметр (DI), 0 patch(_validate_with_visudo)
    result = _write_sudoers_file(target, rules, "test-module", validator=lambda _: False)

    assert result is False
    assert not target.exists(), "File should not exist when visudo fails"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: write with empty rules · Scenario: empty rules list → file still written · Last fail: N/A · Remove if: empty rules guard is added upstream
def test_write_sudoers_file_empty_rules(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """With empty rules list, file is still created with just the header."""
    caplog.set_level(logging.DEBUG)

    target = tmp_path / "sudoers.d" / "platform-empty-module"

    # E3 (160): validator — параметр (DI), 0 patch(_validate_with_visudo)
    result = _write_sudoers_file(target, [], "empty-module", validator=lambda _: True)

    assert result is True
    assert target.exists()
    content = target.read_text()
    assert "# platform module sudoers — empty-module" in content

    _assert_ldd_trajectory(caplog)


# ── Tests: generate_module_sudoers ──────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: per-module sudoers generation · Scenario: full REAL generate flow with
#   sudoers_dir override → файл создан с валидным содержимым (эффект, не мок-вызовы)
# · Last fail: N/A · Remove if: generate_module_sudoers interface changes
def test_generate_module_sudoers_ok(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    tmp_path: Path,
) -> None:
    """generate_module_sudoers (REAL render+write) writes a valid sudoers file in sudoers_dir."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): реальный конвейер render → parse → visudo-validate → atomic write;
    # sudoers_dir= override уводит target из /etc/sudoers.d в tmp_path (0 monkeypatch)
    sudoers_root = tmp_path / "sudoers.d"
    result = generate_module_sudoers(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
        sudoers_dir=str(sudoers_root),
    )

    assert result is True
    target = sudoers_root / "platform-test-module"
    assert target.exists(), "sudoers файл обязан быть создан (эффект)"
    content = target.read_text()
    assert "# platform module sudoers — test-module" in content
    module_abs_dir = str((modules_dir / "test-module").resolve())
    assert f"platform ALL=(root) NOPASSWD: /usr/bin/make -C {module_abs_dir} start" in content
    assert f"ci-deploy ALL=(root) NOPASSWD: /usr/bin/make -C {module_abs_dir} stop" in content
    assert f"platform-monitor ALL=(root) NOPASSWD: /usr/bin/make -C {module_abs_dir} logs" in content
    # режим 0440 (sudoers-канон)
    mode = Path(target).stat().st_mode & 0o777
    assert mode == 0o440, f"Expected 0440, got {oct(mode)}"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: generate with no rules · Scenario: templates БЕЗ шаблона → render [] → False · Last fail: N/A · Remove if: empty-guard logic changes
def test_generate_module_sudoers_no_rules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    tmp_path: Path,
) -> None:
    """When no rules are generated, generate_module_sudoers returns False (real render)."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): реальный сбой рендера — пустая templates-директория (0 monkeypatch render)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()

    result = generate_module_sudoers(
        module_name="test-module",
        modules_dir=modules_dir,
        templates_dir=empty_templates,
        platform_root="/opt/platform",
    )

    assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: batch_generate_sudoers ───────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: batch generate all modules · Scenario: multiple modules → one file with all rules (REAL render+write, sudoers_dir) · Last fail: N/A · Remove if: batch_generate_sudoers logic changes
def test_batch_generate_sudoers_ok(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    tmp_path: Path,
) -> None:
    """batch_generate_sudoers collects rules from all modules and writes once (REAL flow)."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): реальный конвейер с sudoers_dir= override (0 monkeypatch render/write)
    sudoers_root = tmp_path / "sudoers.d"
    result = batch_generate_sudoers(
        module_names=["nginx", "postgres"],
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
        sudoers_dir=str(sudoers_root),
    )

    assert result is True
    target = sudoers_root / "platform-modules"
    assert target.exists(), "batch sudoers файл обязан быть создан (эффект)"
    content = target.read_text()
    assert "# platform module sudoers — platform-modules" in content
    nginx_dir = str((modules_dir / "nginx").resolve())
    postgres_dir = str((modules_dir / "postgres").resolve())
    assert f"-C {nginx_dir} start" in content, "правила nginx в batch-файле"
    assert f"-C {postgres_dir} start" in content, "правила postgres в batch-файле"
    assert f"-C {nginx_dir} status" in content, "несколько правил модуля в batch-файле"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: batch with no modules · Scenario: empty module_names → True (no-op) · Last fail: N/A · Remove if: empty-list handling changes
def test_batch_generate_sudoers_no_modules(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
) -> None:
    """Empty module list returns True without any writes."""
    caplog.set_level(logging.DEBUG)

    result = batch_generate_sudoers(
        module_names=[],
        modules_dir=modules_dir,
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert result is True

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: batch all modules fail render · Scenario: templates БЕЗ шаблона → all renders [] → False · Last fail: N/A · Remove if: empty-collection guard changes
def test_batch_generate_sudoers_all_fail(
    caplog: pytest.LogCaptureFixture,
    modules_dir: Path,
    templates_dir: Path,
    tmp_path: Path,
) -> None:
    """When all module renders return no rules, batch returns False (real render)."""
    caplog.set_level(logging.DEBUG)

    # E3 (160): реальный сбой рендера — пустая templates-директория (0 monkeypatch render)
    empty_templates = tmp_path / "empty-templates"
    empty_templates.mkdir()

    result = batch_generate_sudoers(
        module_names=["nginx", "postgres"],
        modules_dir=modules_dir,
        templates_dir=empty_templates,
        platform_root="/opt/platform",
    )

    assert result is False

    _assert_ldd_trajectory(caplog)


# ── Tests: integration — real _render_template with native template_engine render ──


# 🧪 TRAP[TEST] · Regression: _render_template native render · Scenario: real render via template_engine.render_template(dry_run=True) → str with substituted vars, no temp file · Last fail: N/A · Remove if: _render_template implementation changes
def test_render_template_native_render(
    caplog: pytest.LogCaptureFixture,
    templates_dir: Path,
) -> None:
    """_render_template renders via template_engine.render_template (native import) and returns rendered text."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    rendered = sg._render_template(
        module_name="test-module",
        templates_dir=templates_dir,
        platform_root="/opt/platform",
    )

    assert rendered is not None
    assert "owner make:start" in rendered
    assert "/opt/platform/core/modules/test-module/Makefile" in rendered
    # dry_run=True returns str directly — no placeholders left, no temp file involved
    assert "{{MODULE_NAME}}" not in rendered
    assert "{{PLATFORM_ROOT}}" not in rendered

    _assert_ldd_trajectory(caplog)


# ── Tests: CLI entrypoint ────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression: CLI generate action · Scenario: --action generate with --module-name → exit 0 · Last fail: N/A · Remove if: CLI interface changes
def test_cli_generate_action(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """CLI --action generate dispatches to generate_module_sudoers correctly."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "generate",
        "--module-name",
        "test-module",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    called_action = {"name": None}

    def mock_generate(module_name, modules_dir, templates_dir, platform_root):
        called_action["name"] = module_name
        return True

    # 167 D3: handlers DI-namespace (0 setattr sg.generate_module_sudoers)
    handlers = SimpleNamespace(generate_module_sudoers=mock_generate)
    assert sg.main(test_args[1:], handlers=handlers) == 0
    assert called_action["name"] == "test-module"

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI render-rules action · Scenario: --action render-rules → prints rules to stdout · Last fail: N/A · Remove if: CLI interface changes
def test_cli_render_rules_action(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """CLI --action render-rules prints rules to stdout."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "render-rules",
        "--module-name",
        "test-module",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    fake_rules = ["rule1", "rule2"]
    # 167 D3: handlers DI-namespace (0 setattr sg.render_sudoers_rules)
    handlers = SimpleNamespace(render_sudoers_rules=lambda *_a, **_k: fake_rules)
    assert sg.main(test_args[1:], handlers=handlers) == 0

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI batch-generate action · Scenario: --action batch-generate with --module-names → exit 0 · Last fail: N/A · Remove if: CLI interface changes
def test_cli_batch_generate_action(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """CLI --action batch-generate dispatches to batch_generate_sudoers correctly."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "batch-generate",
        "--module-names",
        "nginx,postgres,redis",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    called_names = {"names": None}
    # 167 D3: handlers DI-namespace (0 setattr sg.batch_generate_sudoers)
    handlers = SimpleNamespace(batch_generate_sudoers=lambda mn, *_a, **_k: called_names.update({"names": mn}) or True)
    assert sg.main(test_args[1:], handlers=handlers) == 0
    assert called_names["names"] == ["nginx", "postgres", "redis"]

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · Regression: CLI missing required args · Scenario: --action generate without --module-name → exit 1 · Last fail: N/A · Remove if: CLI argument validation changes
def test_cli_missing_module_name(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """CLI exits with 1 when --module-name is missing for generate action."""
    caplog.set_level(logging.DEBUG)

    import core.internal.bootstrap.deploy.sudoers_generator as sg

    mod_dir = tmp_path / "modules"
    tmpl_dir = tmp_path / "templates"
    mod_dir.mkdir()
    tmpl_dir.mkdir()

    test_args = [
        "sudoers_generator.py",
        "--action",
        "generate",
        "--modules-dir",
        str(mod_dir),
        "--templates-dir",
        str(tmpl_dir),
        "--platform-root",
        "/opt/platform",
    ]

    assert sg.main(test_args[1:]) == 1

    _assert_ldd_trajectory(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · E5 _safe_cleanup removal — DevPlan 119 E5
# · Regression: _safe_cleanup was dead after _write_sudoers_file migrated to atomic_writer (E5)
# · Last fail: N/A — removal was part of E5 migration (atomic_writer handles temp cleanup)
# · Remove if: atomic_writer or _write_sudoers_file semantics change
def test_safe_cleanup_removed_negative() -> None:
    """R5 negative: _safe_cleanup must NOT exist (cleanup delegated to atomic_writer)."""
    assert not hasattr(sg, "_safe_cleanup"), (
        "R5 FAIL: _safe_cleanup resurrected — temp cleanup must live in atomic_writer (E5)"
    )
    assert not hasattr(sg, "shutil"), "R5 FAIL: shutil import resurrected — atomic_writer owns temp lifecycle (E5)"
    assert not hasattr(sg, "tempfile"), "R5 FAIL: tempfile import resurrected — atomic_writer owns temp lifecycle (E5)"
