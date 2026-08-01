"""
# GREP_SUMMARY: test_stub_detection_shared, stub-detection, GENERATED-STUB, is-stub-ai-platform-yaml, shared, empty-file, missing-file
# STRUCTURE: ▶ tmp_path fixtures → ◇ stub file (GENERATED-STUB first line) → True │ ◇ real config → False │ ◇ missing file → False │ ◇ empty file → False → ⎋ LDD IMP:9 trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for shared/stub_detection.is_stub_ai_platform_yaml (B9 T4, U-28) —
##           единая реализация is_stub-детекции (заменяет inline-bash копии из test_stub_detection.py, CS-6).
## @scope    Тесты: stub / real / missing / empty файлы. Нативные импорты, tmp_path, LDD-траектория.
## @invariants
##   - stub: первая строка содержит "GENERATED-STUB" → True
##   - real: без маркера → False; missing: файл не существует → False; empty: st_size==0 → False
##   - Никогда не raise (OSError/IndexError → False)
## @rationale CS-6: test_stub_detection.py:56-67/112-121/160-169 тестировали несуществующую
##            shell-функцию _is_stub (inline-bash копии). Единая реализация — shared/stub_detection.
## @changes  2026-08-01 · Created (B9 T4, CS-6)
# endregion MODULE_CONTRACT
"""

import logging

import pytest

from core.internal.shared.stub_detection import is_stub_ai_platform_yaml

logger = logging.getLogger(__name__)


# region FUNC_test_is_stub_detects_stub
# 🧪 TRAP[TEST] · Regression · stub file (GENERATED-STUB header) → True
# · Scenario: ai-platform.yaml первая строка содержит GENERATED-STUB → is_stub_ai_platform_yaml == True
# · Last fail: N/A (B9 T4 unit-test of shared/stub_detection, replacing CS-6 bash copy)
# · Remove if: is_stub-детекция консолидирована иначе
@pytest.mark.parametrize(
    "content",
    [
        "# GENERATED-STUB by converge — overwritten by CI deliver\nproject: test-project\n",
        "# GENERATED-STUB\n",
    ],
)
def test_is_stub_detects_stub(tmp_path, caplog, content):
    """File whose first line contains GENERATED-STUB → True."""
    caplog.set_level(logging.INFO)
    stub_file = tmp_path / "ai-platform.yaml"
    stub_file.write_text(content)

    assert is_stub_ai_platform_yaml(stub_file) is True
    assert is_stub_ai_platform_yaml(str(stub_file)) is True  # str-path contract
    logger.info("[IMP:9][test][is_stub] STUB_FILE=IS_STUB — OK")


# endregion FUNC_test_is_stub_detects_stub


# region FUNC_test_is_stub_detects_real
# 🧪 TRAP[TEST] · Regression · real config file (no GENERATED-STUB) → False
# · Scenario: реальный ai-platform.yaml без маркера → False
# · Last fail: N/A (B9 T4 unit-test, replacing CS-6 bash copy)
# · Remove if: is_stub-детекция консолидирована иначе
def test_is_stub_detects_real(tmp_path, caplog):
    """Real config file (no GENERATED-STUB in first line) → False."""
    caplog.set_level(logging.INFO)
    real_file = tmp_path / "ai-platform.yaml"
    real_file.write_text("project: test-project\nservice: test-project\ndomain: example.com\n")

    assert is_stub_ai_platform_yaml(real_file) is False
    logger.info("[IMP:9][test][is_stub] REAL_FILE=NOT_STUB — OK")


# endregion FUNC_test_is_stub_detects_real


# region FUNC_test_is_stub_missing_file
# 🧪 TRAP[TEST] · Regression · missing file → False
# · Scenario: файл не существует → False (не raise)
# · Last fail: N/A (B9 T4 unit-test, replacing CS-6 bash copy)
# · Remove if: is_stub-детекция консолидирована иначе
def test_is_stub_missing_file(tmp_path, caplog):
    """Missing file → False (never raises)."""
    caplog.set_level(logging.INFO)
    missing_file = tmp_path / "nonexistent.yaml"

    assert is_stub_ai_platform_yaml(missing_file) is False
    logger.info("[IMP:9][test][is_stub] MISSING_FILE=NOT_STUB — OK")


# endregion FUNC_test_is_stub_missing_file


# region FUNC_test_is_stub_empty_file
# 🧪 TRAP[TEST] · Regression · empty file (st_size==0) → False
# · Scenario: пустой файл — splitlines() не вызывается (IndexError-защита) → False
# · Last fail: N/A (B9 T4 unit-test — edge-case пустого файла)
# · Remove if: is_stub-детекция консолидирована иначе
def test_is_stub_empty_file(tmp_path, caplog):
    """Empty file (0 bytes) → False (IndexError-защита)."""
    caplog.set_level(logging.INFO)
    empty_file = tmp_path / "ai-platform.yaml"
    empty_file.write_text("")

    assert is_stub_ai_platform_yaml(empty_file) is False
    logger.info("[IMP:9][test][is_stub] EMPTY_FILE=NOT_STUB — OK")


# endregion FUNC_test_is_stub_empty_file
