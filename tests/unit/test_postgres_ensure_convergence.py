# GREP_SUMMARY: test ensure-convergence orphan-role ALTER ROLE PASSWORD postgres hook REF-0002 role_exists no-creds idempotent regen non-fatal FakeCommandRunner
# STRUCTURE: ▶ 5 сценариев converge → orphan(no-creds)→ALTER+creds+GRANT+regen · идемпотентность(2й прогон без ALTER) · ALTER fail(rc≠0) non-fatal · ALTER ERROR-output non-fatal · ALTER TimeoutExpired non-fatal → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for the ensure-convergence branch of core/modules/postgres/hooks/
##           on_project_deploy.py::ensure_project_db_access (REF-0002, 11-DevPlan Волна 0):
##           role_exists + no-creds (orphan-role) → ALTER ROLE PASSWORD + запись кредов +
##           GRANT + реген .env.platform — вместо прежнего раннего return (BUG-0605/DATA-201).
## @scope    Only the convergence surface added by REF-0002: ALTER issued / creds written with
##           rotated password / GRANTs still applied / regen triggered on convergence /
##           idempotency on retry / non-fatal failure paths of ALTER ROLE.
##           Базовые сценарии хука — tests/unit/test_on_project_deploy.py.
## @invariants
##   - subprocess через FakeCommandRunner (runner=) — 0 monkeypatch subprocess.run
##   - POSTGRES_PASSWORD через env параметр (env=) — 0 monkeypatch.setenv
##   - NodeYaml reads tmp_path ai-platform.yaml files (Zero Hardcode Rule)
##   - main() returns 0 на всех converge-путях (non-fatal контракт хука)
##   - Пароль в creds == пароль в ALTER ROLE SQL (единый источник ротации)
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale REF-0002: «idempotency реализована как early-return вместо ensure-convergence» —
##            потеря .platform-db.env означала перманентную потерю доступа (только DROP ROLE
##            вручную). Тесты фиксируют новый контракт: сходимость за один прогон хука.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import subprocess
import textwrap
from pathlib import Path

import pytest

from core.modules.postgres.hooks import on_project_deploy
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_PASSWORD = "test-password"
_ENV: dict[str, str] = {"POSTGRES_PASSWORD": _PASSWORD}


# ═══════════════════════════════════════════════════════════════════
# region Helpers (E1 DI: FakeCommandRunner + psql-router)
# ═══════════════════════════════════════════════════════════════════


class FakeCommandRunner:
    """Command-routing fake (E1 DI): psql-диалог по содержимому SQL, все вызовы записываются.

    ## @purpose — Замена monkeypatch subprocess.run; маршрутизация CREATE DATABASE /
    ##            pg_roles / CREATE ROLE / ALTER ROLE / GRANT / gen_env_platform CLI.
    ## @io — ⇥ router: callable(cmd) → CompletedProcess | raises; default → ⎋ CompletedProcess
    """

    def __init__(self, router=None, default=None):
        self._router = router
        self.default = default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        if self._router is not None:
            return self._router(cmd)
        return self.default


def _make_psql_router(role_exists: bool = True, alter_result: subprocess.CompletedProcess | None = None):
    """psql-fake: роль существует; результат ALTER ROLE управляется параметрами.

    Семантика отказа как у реального psql: rc≠0 → текст ошибки в stderr
    («psql: error: …»), rc=0 + ERROR — серверная ошибка в stdout.
    """
    if alter_result is None:
        alter_result = subprocess.CompletedProcess([], 0, "ALTER ROLE", "")

    def _router(cmd):
        joined = " ".join(str(x) for x in cmd)
        if "CREATE DATABASE" in joined:
            return subprocess.CompletedProcess([], 1, "ERROR: already exists", "")
        if "pg_roles" in joined:
            return subprocess.CompletedProcess([], 0, "1\n" if role_exists else "", "")
        if "ALTER ROLE" in joined:
            return alter_result
        if "CREATE ROLE" in joined:
            return subprocess.CompletedProcess([], 0, "CREATE ROLE", "")
        if "GRANT" in joined:
            return subprocess.CompletedProcess([], 0, "GRANT", "")
        return subprocess.CompletedProcess([], 0, "", "")

    return _router


def _write_yaml(tmp_path: Path, content: str) -> None:
    """Write an ai-platform.yaml fixture into tmp_path."""
    (tmp_path / "ai-platform.yaml").write_text(textwrap.dedent(content))


def _invoke(project_dir: Path, runner: FakeCommandRunner) -> int:
    """Route through main() with env + runner DI."""
    return on_project_deploy.main(argv=[str(project_dir), "myproj", "tronyx-vps"], runner=runner, env=_ENV)


def _extract_alter_password(sql: str) -> str:
    """Extract rotated password from ALTER ROLE ... PASSWORD '<pw>' SQL text."""
    marker = 'ALTER ROLE "myproj_user" WITH LOGIN PASSWORD \''
    start = sql.index(marker) + len(marker)
    return sql[start : sql.index("'", start)]


# endregion Helpers


# ═══════════════════════════════════════════════════════════════════
# region Tests: ensure-convergence (REF-0002 В0)
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_orphan_role_converges_alter_creds_grant_regen(caplog, tmp_path):
    """Core scenario: role exists + no credentials → ALTER ROLE PASSWORD + creds + GRANT + regen."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    regen_calls: list[list[str]] = []

    def _router(cmd):
        joined = " ".join(str(x) for x in cmd)
        if "gen_env_platform.py" in joined:
            regen_calls.append([str(x) for x in cmd])
            return subprocess.CompletedProcess([], 0, "", "")
        return _make_psql_router(role_exists=True)(cmd)

    runner = FakeCommandRunner(router=_router)

    assert _invoke(tmp_path, runner) == 0

    sql = " ".join(" ".join(c) for c in runner.calls)
    # 1. Роль НЕ пересоздаётся — конвергенция через ALTER
    assert 'ALTER ROLE "myproj_user" WITH LOGIN PASSWORD' in sql
    assert 'CREATE ROLE "myproj_user"' not in sql
    # 2. GRANT-ы применяются (раньше early-return их пропускал)
    assert 'GRANT CONNECT ON DATABASE "myproj_db" TO "myproj_user"' in sql
    assert 'GRANT CREATE, USAGE ON SCHEMA public TO "myproj_user"' in sql

    # 3. Credentials записаны, пароль == пароль из ALTER SQL (единая ротация)
    creds_file = tmp_path / ".platform-db.env"
    assert creds_file.is_file()
    content = creds_file.read_text()
    rotated = _extract_alter_password(sql)
    assert f"PLATFORM_POSTGRES_PASSWORD={rotated}" in content
    assert "PLATFORM_POSTGRES_DB=myproj_db" in content
    assert "PLATFORM_POSTGRES_USER=myproj_user" in content
    assert (creds_file.stat().st_mode & 0o777) == 0o600

    # 4. Реген .env.platform выполнен при конвергенции (password-injection)
    assert len(regen_calls) == 1
    assert "--project-dir" in regen_calls[0]

    logger.critical(
        "[IMP:9][test] orphan-role converged — ALTER issued, creds 0600 written with rotated pw, GRANTs applied, regen x1"
    )


@ldd_trajectory
def test_convergence_idempotent_second_run_no_alter(caplog, tmp_path):
    """Идемпотентность: повторный прогон после конвергенции — без ALTER, пароль стабильный, без регена."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(router=_make_psql_router(role_exists=True))

    assert _invoke(tmp_path, runner) == 0
    first_pw = _extract_alter_password(" ".join(" ".join(c) for c in runner.calls))

    runner2 = FakeCommandRunner(router=_make_psql_router(role_exists=True))
    assert _invoke(tmp_path, runner2) == 0

    sql2 = " ".join(" ".join(c) for c in runner2.calls)
    assert "ALTER ROLE" not in sql2, "повторный прогон с валидными creds → без ALTER (идемпотентность)"
    assert "gen_env_platform.py" not in sql2, "реген не выполняется без создания/конвергенции"
    assert f"PLATFORM_POSTGRES_PASSWORD={first_pw}" in (tmp_path / ".platform-db.env").read_text()

    logger.critical("[IMP:9][test] convergence idempotent — second run: no ALTER, password stable, no regen")


@ldd_trajectory
def test_alter_role_failure_non_fatal_no_credentials(caplog, tmp_path):
    """ALTER ROLE rc≠0 (psql: error в stderr) → non-fatal (return 0), credentials НЕ пишутся."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(
        router=_make_psql_router(
            role_exists=True,
            alter_result=subprocess.CompletedProcess([], 1, "", "psql: error: could not connect to server"),
        )
    )

    assert _invoke(tmp_path, runner) == 0
    assert not (tmp_path / ".platform-db.env").exists(), "при неудачном ALTER креды не пишутся"

    logger.critical("[IMP:9][test] ALTER failure non-fatal — rc=0, no credentials written")


@ldd_trajectory
def test_alter_role_error_output_non_fatal(caplog, tmp_path):
    """ALTER ROLE rc=0 но ERROR в выводе psql (серверная ошибка) → non-fatal, креды НЕ пишутся."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(
        router=_make_psql_router(
            role_exists=True,
            alter_result=subprocess.CompletedProcess([], 0, "ERROR: permission denied", ""),
        )
    )

    assert _invoke(tmp_path, runner) == 0
    assert not (tmp_path / ".platform-db.env").exists()

    logger.critical("[IMP:9][test] ALTER ERROR output non-fatal — rc=0, no credentials written")


@ldd_trajectory
def test_alter_role_timeout_non_fatal(caplog, tmp_path):
    """TimeoutExpired на ALTER ROLE (_psql → None) → non-fatal, креды НЕ пишутся."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    def _timeout_on_alter(cmd):
        if "ALTER ROLE" in " ".join(str(x) for x in cmd):
            raise subprocess.TimeoutExpired(cmd, timeout=60)
        return _make_psql_router(role_exists=True)(cmd)

    runner = FakeCommandRunner(router=_timeout_on_alter)

    assert _invoke(tmp_path, runner) == 0
    assert not (tmp_path / ".platform-db.env").exists()

    logger.critical("[IMP:9][test] ALTER timeout non-fatal — rc=0, no credentials written")


# endregion Tests: ensure-convergence
