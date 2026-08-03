"""
# GREP_SUMMARY: test on_project_deploy auto-create-db needs.database already-exists psql postgres-hook role GRANT credentials idempotent negative
# STRUCTURE: ▶ 4 сценария (нет yaml / нет needs / DB существует / успех) → ▶ негативные (invalid db_name, psql fail) → ▶ DevPlan 133 W2: роль/GRANT/credentials/идемпотентность → ▶ R5-negative (pgbouncer no-such-database) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/postgres/hooks/on_project_deploy.py (DevPlan 117 H D65
##           + DevPlan 133 W2: role/GRANT/credentials/password-injection).
##           Docker exec psql mocked via monkeypatch — no real docker calls.
## @scope    Tests all DevPlan scenarios (no ai-platform.yaml, no needs.database, database
##           already exists, successful creation) plus negative cases (invalid db_name, psql
##           CRITICAL), password-gate, and the W2 role-provisioning surface:
##           role created / exists / repeat (idempotent, password unchanged), GRANT commands,
##           credentials file (content/perms), .env.platform regen on first role creation.
##           R5-negative: pgbouncer wildcard — routing no longer depends on pgbouncer.ini list.
## @invariants
##   - subprocess.run mocked — zero real process spawns (command-routing fake psql)
##   - NodeYaml reads tmp_path ai-platform.yaml files (Zero Hardcode Rule)
##   - main() returns the hook status (0 = ok/skip, 1 = fatal)
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale DevPlan 09 §D65 + DevPlan 133 W2.4: unit coverage for the Python postgres deploy hook.
## @changes 2026-08-02 | Created (Brief H D65)
## @changes 2026-08-03 | DevPlan 133 W2 — role/GRANT/credentials tests (command-routing mocks)
# endregion MODULE_CONTRACT
"""

import logging
import os
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
# region MOCK_psql_router
## @purpose  Command-routing fake psql: возвращает разные результаты в зависимости от SQL,
##           имитируя реальный postgres (CREATE DATABASE / pg_roles / CREATE ROLE / GRANT).
##           GRANT-запросы (без "TO") и SELECT из pg_roles — успех; CREATE ROLE — успех.
## @param create_db_rc    returncode для CREATE DATABASE (0 = успех, 1 = exists/ошибка)
## @param role_exists     True → pg_roles SELECT вернёт "1" (роль существует)
## @param create_role_out stdout для CREATE ROLE
## @return  callable для monkeypatch subprocess.run
def _make_psql_router(create_db_rc: int = 0, role_exists: bool = False, create_role_out: str = "CREATE ROLE"):
    """Build a command-routing subprocess.run fake (psql-диалог postgres)."""

    def _router(cmd, *a, **k):
        joined = " ".join(str(x) for x in cmd)
        if "CREATE DATABASE" in joined:
            return _FakePsqlResult(
                stdout="CREATE DATABASE" if create_db_rc == 0 else "ERROR: already exists", returncode=create_db_rc
            )
        if "pg_roles" in joined:
            return _FakePsqlResult(stdout="1\n" if role_exists else "", returncode=0)
        if "CREATE ROLE" in joined:
            return _FakePsqlResult(stdout=create_role_out, returncode=0)
        if "GRANT" in joined:
            return _FakePsqlResult(stdout="GRANT", returncode=0)
        return _FakePsqlResult(stdout="", returncode=0)

    return _router


# endregion MOCK_psql_router


# ═══════════════════════════════════════════════════════════════════
# region Tests: базовые сценарии (D65 — без изменений)
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
        _make_psql_router(create_db_rc=1, role_exists=True),
    )

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_successful_creation(caplog, tmp_path, monkeypatch):
    """Scenario 4: psql succeeds with CREATE DATABASE → return 0."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(on_project_deploy.subprocess, "run", _make_psql_router())

    assert _invoke_hook(str(tmp_path), "myproj") == 0


# ═══════════════════════════════════════════════════════════════════
# region Tests: негативные (D65)
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


# ═══════════════════════════════════════════════════════════════════
# region Tests: DevPlan 133 W2 — роль + GRANT + credentials
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_role_created_with_grants_and_credentials(caplog, tmp_path, monkeypatch):
    """W2: роль отсутствует → CREATE ROLE + GRANT CONNECT + GRANT SCHEMA + .platform-db.env."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    calls: list[str] = []
    router = _make_psql_router(role_exists=False)

    def _recording_router(cmd, *a, **k):
        calls.append(" ".join(str(x) for x in cmd))
        return router(cmd, *a, **k)

    monkeypatch.setattr(on_project_deploy.subprocess, "run", _recording_router)

    assert _invoke_hook(str(tmp_path), "myproj") == 0

    # SQL-последовательность: CREATE DATABASE → pg_roles check → CREATE ROLE → 2 GRANT
    sql = " ".join(calls)
    assert "CREATE DATABASE myproj_db" in sql
    assert "pg_roles" in sql
    assert 'CREATE ROLE "myproj_user" LOGIN PASSWORD' in sql
    assert 'GRANT CONNECT ON DATABASE "myproj_db" TO "myproj_user"' in sql
    assert 'GRANT CREATE, USAGE ON SCHEMA public TO "myproj_user"' in sql

    # Credentials-файл: содержимое + perms 0600
    creds_file = tmp_path / ".platform-db.env"
    assert creds_file.is_file()
    content = creds_file.read_text()
    assert "PLATFORM_POSTGRES_DB=myproj_db" in content
    assert "PLATFORM_POSTGRES_USER=myproj_user" in content
    assert "PLATFORM_POSTGRES_PASSWORD=" in content
    assert (creds_file.stat().st_mode & 0o777) == 0o600

    logger.critical(
        "[IMP:9][test] role+GRANT+credentials OK — %d psql calls, .platform-db.env 0600 with %d bytes",
        len(calls),
        len(content),
    )


@ldd_trajectory
def test_role_exists_idempotent_password_unchanged(caplog, tmp_path, monkeypatch):
    """W2: роль существует → НЕ пересоздаётся; пароль из credentials не меняется."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    (tmp_path / ".platform-db.env").write_text(
        "PLATFORM_POSTGRES_DB=myproj_db\nPLATFORM_POSTGRES_USER=myproj_user\nPLATFORM_POSTGRES_PASSWORD=stable-pw\n"
    )
    os.chmod(tmp_path / ".platform-db.env", 0o600)

    calls: list[str] = []
    router = _make_psql_router(role_exists=True)

    def _recording_router(cmd, *a, **k):
        calls.append(" ".join(str(x) for x in cmd))
        return router(cmd, *a, **k)

    monkeypatch.setattr(on_project_deploy.subprocess, "run", _recording_router)

    assert _invoke_hook(str(tmp_path), "myproj") == 0

    sql = " ".join(calls)
    assert "CREATE ROLE" not in sql, "роль существует → CREATE ROLE не вызывается (идемпотентность)"
    assert "GRANT CONNECT ON DATABASE" in sql, "GRANT-ы применяются и для существующей роли"
    content = (tmp_path / ".platform-db.env").read_text()
    assert "PLATFORM_POSTGRES_PASSWORD=stable-pw" in content, "пароль существующей роли НЕ меняется"

    logger.critical("[IMP:9][test] idempotency OK — role exists → no CREATE ROLE, password preserved")


@ldd_trajectory
def test_role_exists_without_credentials_skips_refresh(caplog, tmp_path, monkeypatch):
    """W2: роль есть, credentials-файла нет → пароль неизвестен → skip (non-fatal, return 0)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(on_project_deploy.subprocess, "run", _make_psql_router(role_exists=True))

    assert _invoke_hook(str(tmp_path), "myproj") == 0
    assert not (tmp_path / ".platform-db.env").exists()


@ldd_trajectory
def test_credentials_regen_env_platform_on_first_role(caplog, tmp_path, monkeypatch):
    """W2: при первом создании роли — .env.platform перегенерируется (password-injection CLI)."""
    monkeypatch.setenv("POSTGRES_PASSWORD", _PASSWORD)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    monkeypatch.setattr(on_project_deploy.subprocess, "run", _make_psql_router(role_exists=False))

    # Перехват subprocess-вызова CLI регенерации (gen_env_platform.py --project-dir)
    regen_calls: list[list[str]] = []

    orig_run = on_project_deploy.subprocess.run

    def _router_with_regen_capture(cmd, *a, **k):
        joined = " ".join(str(x) for x in cmd)
        if "gen_env_platform.py" in joined:
            regen_calls.append([str(x) for x in cmd])
            return _FakePsqlResult(stdout="", returncode=0)
        return orig_run(cmd, *a, **k)

    monkeypatch.setattr(on_project_deploy.subprocess, "run", _router_with_regen_capture)

    assert _invoke_hook(str(tmp_path), "myproj") == 0
    assert len(regen_calls) == 1, "регенерация .env.platform вызывается ровно 1 раз (первое создание роли)"
    assert "--project-dir" in regen_calls[0]
    assert "--name" in regen_calls[0]

    logger.critical("[IMP:9][test] .env.platform regen OK — CLI вызван с --project-dir/--name")


# ═══════════════════════════════════════════════════════════════════
# region Tests: R5-negative (DevPlan 133 — баг «pgbouncer no such database»)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · NEGATIVE (R5) · pgbouncer wildcard — маршрутизация не зависит от pgbouncer.ini
# · Scenario: pgbouncer DATABASE_URLS в docker-compose.base.yml — ОДНА URL без имени БД
# ·   (wildcard '*'), а НЕ жёсткий список platform/litellm/langfuse (баг: новые БД →
# ·   «no such database» в pgbouncer)
# · Last fail: 2026-08-03 — pgbouncer.ini содержал жёсткий список → auth failure для новых БД
# · Remove if: pgbouncer конфигурация вернётся к явному списку БД (решение D5 отменено)
@ldd_trajectory
def test_r5_pgbouncer_wildcard_routing_not_hardcoded_list(caplog):
    """R5-negative: маршрутизация pgbouncer больше НЕ зависит от pgbouncer.ini-списка (D5)."""
    compose = (
        Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "postgres" / "docker-compose.base.yml"
    )
    assert compose.is_file(), f"docker-compose.base.yml not found: {compose}"
    text = compose.read_text()

    # Wildcard URL (без имени БД) присутствует
    assert "DATABASE_URLS" in text
    assert '@postgres:5432/"' in text or '@postgres:5432/" ' in text
    # Жёсткий список БД отсутствует (баг «no such database» закрыт — D5)
    assert "/platform,postgresql" not in text
    assert "5432/langfuse" not in text

    logger.critical("[IMP:9][test] R5-negative OK — pgbouncer wildcard URL, hardcoded DB list отсутствует")
