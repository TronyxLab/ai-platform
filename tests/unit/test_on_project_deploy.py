"""
# GREP_SUMMARY: test on_project_deploy auto-create-db needs.database already-exists psql postgres-hook
# STRUCTURE: ▶ 4 сценария (нет yaml / нет needs / DB существует / успех) → ▶ негативные (invalid db_name, psql fail) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/postgres/hooks/on_project_deploy.py (DevPlan 117 H D65).
##           Docker exec psql mocked via monkeypatch — no real docker calls.
## @scope    Tests all 4 DevPlan scenarios (no ai-platform.yaml, no needs.database,
##           database already exists, successful creation) plus negative cases
##           (invalid db_name, psql CRITICAL) and password-gate.
##           Scenarios are routed through main() so the wrapper's IMP:9 START/DONE
##           logs are present on early-return paths too (LDD Anti-Illusion rule).
## @invariants
##   - subprocess.run mocked — zero real process spawns
##   - NodeYaml reads tmp_path ai-platform.yaml files (Zero Hardcode Rule)
##   - main() returns the hook status (0 = ok/skip, 1 = fatal)
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale DevPlan 09 §D65: unit coverage for the Python postgres deploy hook.
## @changes 2026-08-02 | Created (Brief H D65)
# endregion MODULE_CONTRACT
"""

import logging
import textwrap
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (canonical package import — DevPlan 118 F5) ──
# Package structure core/modules/postgres/hooks/__init__.py (F5): dotted import works
# from ANY CWD via the conftest addsitedir chain — no sys.path.insert hack, no
# dependence on process working directory (VPS watchdog PYTHONPATH-safe).
from core.modules.postgres.hooks import on_project_deploy

_PASSWORD = "test-password"


class _FakePsqlResult:
    """Fake subprocess.CompletedProcess for docker exec psql output."""

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _write_yaml(tmp_path: Path, content: str) -> None:
    """Write an ai-platform.yaml fixture into tmp_path."""
    (tmp_path / "ai-platform.yaml").write_text(textwrap.dedent(content))


def _invoke_hook(project_dir: str, project: str) -> int:
    """Route through main() so IMP:9 START/DONE logs are emitted on every path."""
    return on_project_deploy.main(argv=[project_dir, project, "tronyx-vps"])


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_no_ai_platform_yaml_skips(caplog, tmp_path, monkeypatch):
    """Scenario 1: project dir without ai-platform.yaml → skip (return 0)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    empty_dir = tmp_path / "no-yaml"
    empty_dir.mkdir()

    assert _invoke_hook(str(empty_dir), "myproj") == 0


@ldd_trajectory
def test_no_needs_database_skips(caplog, tmp_path, monkeypatch):
    """Scenario 2: ai-platform.yaml without needs.database → skip (return 0)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\n")

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_false_database_skips(caplog, tmp_path, monkeypatch):
    """needs.database: false (explicit YAML false) → treated as absent → skip."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: false\n")

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_database_already_exists_skips(caplog, tmp_path, monkeypatch):
    """Scenario 3: psql says 'already exists' → skip (return 0), not CRITICAL."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(
        on_project_deploy.subprocess,
        "run",
        lambda *a, **k: _FakePsqlResult(stdout='ERROR:  database "myproj_db" already exists', returncode=1),
    )

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_successful_creation(caplog, tmp_path, monkeypatch):
    """Scenario 4: psql succeeds with CREATE DATABASE → return 0."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(
        on_project_deploy.subprocess,
        "run",
        lambda *a, **k: _FakePsqlResult(stdout="CREATE DATABASE"),
    )

    assert _invoke_hook(str(tmp_path), "myproj") == 0


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_invalid_db_name_fatal(caplog, tmp_path, monkeypatch):
    """db_name violating ^[a-zA-Z0-9_]+$ → fatal (return 1) before psql."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: 'bad-name!'")

    assert _invoke_hook(str(tmp_path), "myproj") == 1


@ldd_trajectory
def test_psql_failure_critical(caplog, tmp_path, monkeypatch):
    """psql returns non-zero without 'already exists' → CRITICAL (return 1)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(
        on_project_deploy.subprocess,
        "run",
        lambda *a, **k: _FakePsqlResult(stdout="connection refused", returncode=1),
    )

    assert _invoke_hook(str(tmp_path), "myproj") == 1


@ldd_trajectory
def test_psql_error_output_fatal(caplog, tmp_path, monkeypatch):
    """psql rc=0 but output contains ERROR → failed (return 1)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(
        on_project_deploy.subprocess,
        "run",
        lambda *a, **k: _FakePsqlResult(stdout="ERROR: permission denied", returncode=0),
    )

    assert _invoke_hook(str(tmp_path), "myproj") == 1


@ldd_trajectory
def test_missing_password_skips(caplog, tmp_path, monkeypatch):
    """POSTGRES_PASSWORD absent → skip DB creation (return 0), per hook contract."""
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("PGPASSWORD", raising=False)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    assert _invoke_hook(str(tmp_path), "myproj") == 0


def test_main_missing_args_exits_zero(caplog):
    """main() with missing PROJECT_DIR/PROJECT → exit 0 (backward-compat skip).

    No @ldd_trajectory: this early-return path deliberately emits only IMP:6
    (missing args — nothing to do), so IMP:9 assertion would be a forced semantic.
    """
    caplog.set_level(logging.DEBUG)
    import sys as _sys

    _sys.argv = ["on_project_deploy.py"]

    try:
        assert on_project_deploy.main() == 0
    finally:
        _sys.argv = ["pytest"]
