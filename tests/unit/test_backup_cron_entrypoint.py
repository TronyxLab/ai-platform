#!/usr/bin/env python3
# GREP_SUMMARY: test-backup-cron-entrypoint render_env_lines write_env_file multiline-skip 0600 exec-target
# STRUCTURE: ┌sys.path insert (backup-cron/scripts)┐ → ◇ render_env_lines (KEY=VALUE, multiline skip, sorted) → ◇ write_env_file (mode 0600, tmp_path, atomic) → ◇ exec-таргет константа → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/backup-cron/scripts/entrypoint.py (DevPlan 143 W1B):
##           render_env_lines (pure function) + write_env_file (I/O, tmp_path) +
##           exec-таргет константа (_CRON_ARGV).
## @scope    Pure unit tests (0 Docker, 0 subprocess). tmp_path for write_env_file.
## @invariants
##   - render_env_lines: KEY=VALUE format, values with \n SKIPPED, sorted by key, empty values included
##   - write_env_file: mode 0600 (root-only), atomic write, correct content
##   - _CRON_ARGV constant: ["cron", "-f"] (PID 1 = cron after exec)
##   - LDD [IMP:9] trajectory verified (caplog)
## @rationale  DevPlan 143 W1B §TEST_SPEC — unit-testable pure function render_env_lines;
##            entrypoint bridges architectural gap (Debian cron не наследует container env).
## @changes  2026-08-08 | DevPlan 143 W1B — created
# endregion MODULE_CONTRACT

import logging
import os
import stat

# Module-specific sys.path (tests/AGENTS.md sys.path policy): backup-cron/scripts
# НЕ покрыт conftest-хуком (он в core/modules/, не core/internal/).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"))

from entrypoint import _CRON_ARGV, _ENV_FILE_PATH, render_env_lines, write_env_file

logger = logging.getLogger(__name__)


# region render_env_lines


# 🧪 TRAP[TEST] · Regression · Scenario: render_env_lines базовый формат (143 W1B)
# · Expect: KEY=VALUE lines, sorted by key
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: render_env_lines меняет формат вывода
def test_render_env_lines_basic_format(caplog) -> None:
    """render_env_lines: KEY=VALUE, отсортировано по ключу."""
    caplog.set_level(logging.INFO)
    env = {"POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432", "S3_BUCKET": "backups"}
    lines = render_env_lines(env)
    assert lines == ["POSTGRES_HOST=postgres", "POSTGRES_PORT=5432", "S3_BUCKET=backups"], f"unexpected lines: {lines}"
    # LDD trajectory: IMP:7 log present
    found = any("[IMP:7]" in r.message and "render_env_lines" in r.message for r in caplog.records)
    assert found, "render_env_lines должен логировать [IMP:7]"
    logger.info("[IMP:9][test_backup_cron_entrypoint] render_env_lines basic format PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: multiline значения пропускаются (143 W1B)
# · Expect: значения с \n SKIPPED (newline break line-oriented /etc/environment parsing)
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: multiline-skip логика меняется
def test_render_env_lines_skips_multiline_values(caplog) -> None:
    """render_env_lines: значения с \\n пропускаются (не ломают /etc/environment)."""
    caplog.set_level(logging.INFO)
    env = {
        "GOOD_VAR": "value1",
        "BAD_MULTILINE": "line1\nline2",
        "ALSO_GOOD": "value2",
    }
    lines = render_env_lines(env)
    rendered_keys = {line.split("=", 1)[0] for line in lines}
    assert "BAD_MULTILINE" not in rendered_keys, f"multiline значение НЕ должно попасть в /etc/environment: {lines}"
    assert "GOOD_VAR" in rendered_keys and "ALSO_GOOD" in rendered_keys, f"обычные значения должны остаться: {lines}"
    logger.info("[IMP:9][test_backup_cron_entrypoint] render_env_lines multiline-skip PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: пустые значения включаются (143 W1B)
# · Expect: KEY= для пустой строки (явный пустой env var семантически значим)
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: empty-value политика меняется
def test_render_env_lines_includes_empty_values(caplog) -> None:
    """render_env_lines: пустые значения включаются как KEY= (явный пустой env var)."""
    caplog.set_level(logging.INFO)
    env = {"EMPTY_VAR": "", "NONEMPTY": "x"}
    lines = render_env_lines(env)
    assert "EMPTY_VAR=" in lines, f"пустое значение должно быть KEY=: {lines}"
    assert "NONEMPTY=x" in lines, f"непустое значение: {lines}"
    logger.info("[IMP:9][test_backup_cron_entrypoint] render_env_lines empty-value PASS")


# endregion render_env_lines


# region write_env_file


# 🧪 TRAP[TEST] · Regression · Scenario: write_env_file mode 0600 + content (143 W1B)
# · Expect: файл mode 0600, содержимое KEY=VALUE\n
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: write_env_file меняет контракт (mode/content)
def test_write_env_file_mode_and_content(tmp_path: Path, caplog) -> None:
    """write_env_file: mode 0600, корректный контент, возвращает число строк."""
    caplog.set_level(logging.INFO)
    env_file = tmp_path / "environment"
    env = {"POSTGRES_HOST": "postgres", "POSTGRES_PASSWORD": "secret123"}
    count = write_env_file(env, env_file)

    assert env_file.exists(), "файл должен быть создан"
    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600, f"mode должен быть 0600 (root-only), got {oct(mode)}"
    content = env_file.read_text(encoding="utf-8")
    assert "POSTGRES_HOST=postgres" in content, f"контент должен содержать KEY=VALUE: {content}"
    assert "POSTGRES_PASSWORD=secret123" in content, f"секрет в контенте: {content}"
    assert count == 2, f"должно вернуть 2 строки, got {count}"
    # LDD trajectory: IMP:9 log present
    found = any("[IMP:9]" in r.message and "write_env_file" in r.message for r in caplog.records)
    assert found, "write_env_file должен логировать [IMP:9]"
    logger.info("[IMP:9][test_backup_cron_entrypoint] write_env_file mode 0600 + content PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: write_env_file atomic (no tmp leftover) (143 W1B)
# · Expect: temp file cleaned up after os.replace
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: atomic-write реализация меняется
def test_write_env_file_atomic_no_tmp_leftover(tmp_path: Path, caplog) -> None:
    """write_env_file: atomic write — temp file не остаётся после os.replace."""
    caplog.set_level(logging.INFO)
    env_file = tmp_path / "environment"
    write_env_file({"KEY": "val"}, env_file)
    # Only the target file should exist, no .tmp leftover
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name == "environment", f"temp file не должен остаться: {files}"
    logger.info("[IMP:9][test_backup_cron_entrypoint] write_env_file atomic (no tmp) PASS")


# endregion write_env_file


# region exec_target


# 🧪 TRAP[TEST] · Regression · Scenario: exec-таргет константа _CRON_ARGV (143 W1B)
# · Expect: ["cron", "-f"] — PID 1 becomes cron after os.execvp
# · Last fail: N/A (new test — DevPlan 143 W1B)
# · Remove if: entrypoint меняет exec-таргет (cron → другой демон)
def test_cron_argv_constant(caplog) -> None:
    """_CRON_ARGV = ["cron", "-f"] — PID 1 becomes cron (healthcheck pgrep cron)."""
    caplog.set_level(logging.INFO)
    assert _CRON_ARGV == ["cron", "-f"], f"_CRON_ARGV должен быть ['cron', '-f'], got {_CRON_ARGV}"
    assert _ENV_FILE_PATH == "/etc/environment", f"_ENV_FILE_PATH должен быть /etc/environment, got {_ENV_FILE_PATH}"
    logger.info("[IMP:9][test_backup_cron_entrypoint] _CRON_ARGV constant PASS")


# endregion exec_target


# Prevent os.execvp from being called during import/test (safety — main() is never
# invoked in unit tests, but guard against accidental import side-effects).
_ = os
