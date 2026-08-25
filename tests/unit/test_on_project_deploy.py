"""
# GREP_SUMMARY: test on_project_deploy auto-create-db needs.database already-exists psql postgres-hook role GRANT credentials idempotent negative DI FakeCommandRunner env
# STRUCTURE: ▶ 4 сценария (нет yaml / нет needs / DB существует / успех) → ▶ негативные (invalid db_name, psql fail) → ▶ DevPlan 133 W2: роль/GRANT/credentials/идемпотентность → ▶ R5-negative (pgbouncer no-such-database) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/postgres/hooks/on_project_deploy.py (DevPlan 117 H D65
##           + DevPlan 133 W2: role/GRANT/credentials/password-injection).
##           docker exec psql — через FakeCommandRunner (runner=) + env через параметр (E1 DI).
## @scope    Tests all DevPlan scenarios (no ai-platform.yaml, no needs.database, database
##           already exists, successful creation) plus negative cases (invalid db_name, psql
##           CRITICAL), password-gate, and the W2 role-provisioning surface:
##           role created / exists / repeat (idempotent, password unchanged), GRANT commands,
##           credentials file (content/perms), .env.platform regen on first role creation.
##           R5-negative: pgbouncer wildcard — routing no longer depends on pgbouncer.ini list.
## @invariants
##   - subprocess через FakeCommandRunner (runner=) — 0 monkeypatch subprocess.run
##   - POSTGRES_PASSWORD через env параметр (env=) — 0 monkeypatch.setenv
##   - NodeYaml reads tmp_path ai-platform.yaml files (Zero Hardcode Rule)
##   - main() returns the hook status (0 = ok/skip, 1 = fatal)
##   - @ldd_trajectory asserts IMP:9 log presence
## @rationale DevPlan 09 §D65 + DevPlan 133 W2.4: unit coverage for the Python postgres deploy hook.
## @changes 2026-08-02 | Created (Brief H D65)
## @changes 2026-08-03 | DevPlan 133 W2 — role/GRANT/credentials tests (command-routing mocks)
## @changes 2026-08-13 | E1 (160) — DI-конвертация (setattr 9 → 0, setenv 12 → 0, −100%)
## @changes 2026-08-24 | REF-0002 В0 — orphan-role ветка: skip → ensure-convergence
##           (детальное покрытие — tests/unit/test_postgres_ensure_convergence.py)
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import textwrap
from pathlib import Path

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test (canonical package import — DevPlan 118 F5) ──
import pytest

from core.modules.postgres.hooks import on_project_deploy

pytestmark = pytest.mark.static_audit

_PASSWORD = "test-password"
_ENV: dict[str, str] = {"POSTGRES_PASSWORD": _PASSWORD}


class FakeCommandRunner:
    """Command-routing FakeCommandRunner (E1 DI): psql-диалог postgres по содержимому SQL.

    ## @purpose — Замена monkeypatch on_project_deploy.subprocess.run: каждый вызов
    ##            записывается (calls), результат выбирается по содержимому команды
    ##            (CREATE DATABASE / pg_roles / CREATE ROLE / GRANT / regen CLI).
    ## @io — ⇥ router: callable(cmd) → CompletedProcess; default → ⎋ CompletedProcess
    ## @complexity — O(1) — routing по подстроке
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


def _make_psql_router(create_db_rc: int = 0, role_exists: bool = False, create_role_out: str = "CREATE ROLE"):
    """Build a command-routing psql fake (psql-диалог postgres)."""

    def _router(cmd):
        joined = " ".join(str(x) for x in cmd)
        if "CREATE DATABASE" in joined:
            return subprocess.CompletedProcess(
                [],
                create_db_rc,
                "CREATE DATABASE" if create_db_rc == 0 else "ERROR: already exists",
                "",
            )
        if "pg_roles" in joined:
            return subprocess.CompletedProcess([], 0, "1\n" if role_exists else "", "")
        if "CREATE ROLE" in joined:
            return subprocess.CompletedProcess([], 0, create_role_out, "")
        if "GRANT" in joined:
            return subprocess.CompletedProcess([], 0, "GRANT", "")
        return subprocess.CompletedProcess([], 0, "", "")

    return _router


def _write_yaml(tmp_path: Path, content: str) -> None:
    """Write an ai-platform.yaml fixture into tmp_path."""
    (tmp_path / "ai-platform.yaml").write_text(textwrap.dedent(content))


def _invoke_hook(project_dir: str, project: str, runner: FakeCommandRunner | None = None) -> int:
    """Route through main() with env + runner DI (E1): IMP:9 START/DONE logs on every path."""
    return on_project_deploy.main(argv=[project_dir, project, "tronyx-vps"], runner=runner, env=_ENV)


# ═══════════════════════════════════════════════════════════════════
# region Tests: базовые сценарии (D65 — без изменений)
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_no_ai_platform_yaml_skips(caplog, tmp_path):
    """Scenario 1: project dir without ai-platform.yaml → skip (return 0)."""
    empty_dir = tmp_path / "no-yaml"
    empty_dir.mkdir()

    assert _invoke_hook(str(empty_dir), "myproj") == 0


@ldd_trajectory
def test_no_needs_database_skips(caplog, tmp_path):
    """Scenario 2: ai-platform.yaml without needs.database → skip (return 0)."""
    _write_yaml(tmp_path, "name: myproj\n")

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_false_database_skips(caplog, tmp_path):
    """needs.database: false (explicit YAML false) → treated as absent → skip."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: false\n")

    assert _invoke_hook(str(tmp_path), "myproj") == 0


@ldd_trajectory
def test_database_already_exists_skips(caplog, tmp_path):
    """Scenario 3: psql says 'already exists' → skip (return 0), not CRITICAL."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(router=_make_psql_router(create_db_rc=1, role_exists=True))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0


@ldd_trajectory
def test_successful_creation(caplog, tmp_path):
    """Scenario 4: psql succeeds with CREATE DATABASE → return 0."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(router=_make_psql_router())

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0


# endregion Tests: базовые сценарии

# ═══════════════════════════════════════════════════════════════════
# region Tests: негативные (D65)
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_invalid_db_name_fatal(caplog, tmp_path):
    """db_name violating ^[a-zA-Z0-9_]+$ → fatal (return 1) before psql."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: 'bad-name!'")

    assert _invoke_hook(str(tmp_path), "myproj") == 1


@ldd_trajectory
def test_psql_failure_critical(caplog, tmp_path):
    """psql returns non-zero without 'already exists' → CRITICAL (return 1)."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(default=subprocess.CompletedProcess([], 1, "connection refused", ""))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 1


@ldd_trajectory
def test_psql_error_output_fatal(caplog, tmp_path):
    """psql rc=0 but output contains ERROR → failed (return 1)."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(default=subprocess.CompletedProcess([], 0, "ERROR: permission denied", ""))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 1


@ldd_trajectory
def test_missing_password_skips(caplog, tmp_path):
    """POSTGRES_PASSWORD absent → skip DB creation (return 0), per hook contract."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    # env без POSTGRES_PASSWORD/PGPASSWORD → gate не пройден → skip
    assert on_project_deploy.main(argv=[str(tmp_path), "myproj", "tronyx-vps"], env={}) == 0


# GUARD-PRESERVE (168): единственное покрытие backward-compat ветки main() — missing PROJECT_DIR/PROJECT
# → exit 0 (skip); контракт hook'а «не ломать вызовы без аргументов» (ранние интеграции/CLI-вызовы)
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


# endregion Tests: негативные

# ═══════════════════════════════════════════════════════════════════
# region Tests: DevPlan 133 W2 — роль + GRANT + credentials
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_role_created_with_grants_and_credentials(caplog, tmp_path):
    """W2: роль отсутствует → CREATE ROLE + GRANT CONNECT + GRANT SCHEMA + .platform-db.env."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    router = _make_psql_router(role_exists=False)
    runner = FakeCommandRunner(router=router)

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0

    # SQL-последовательность: CREATE DATABASE → pg_roles check → CREATE ROLE → 2 GRANT
    sql = " ".join(" ".join(c) for c in runner.calls)
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
        len(runner.calls),
        len(content),
    )


@ldd_trajectory
def test_role_exists_idempotent_password_unchanged(caplog, tmp_path):
    """W2: роль существует → НЕ пересоздаётся; пароль из credentials не меняется."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    (tmp_path / ".platform-db.env").write_text(
        "PLATFORM_POSTGRES_DB=myproj_db\nPLATFORM_POSTGRES_USER=myproj_user\nPLATFORM_POSTGRES_PASSWORD=stable-pw\n"
    )
    Path(tmp_path / ".platform-db.env").chmod(0o600)

    runner = FakeCommandRunner(router=_make_psql_router(role_exists=True))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0

    sql = " ".join(" ".join(c) for c in runner.calls)
    assert "CREATE ROLE" not in sql, "роль существует → CREATE ROLE не вызывается (идемпотентность)"
    assert "GRANT CONNECT ON DATABASE" in sql, "GRANT-ы применяются и для существующей роли"
    content = (tmp_path / ".platform-db.env").read_text()
    assert "PLATFORM_POSTGRES_PASSWORD=stable-pw" in content, "пароль существующей роли НЕ меняется"

    logger.critical("[IMP:9][test] idempotency OK — role exists → no CREATE ROLE, password preserved")


@ldd_trajectory
def test_role_exists_without_credentials_converges(caplog, tmp_path):
    """REF-0002 В0: роль есть, credentials-файла нет → ensure-convergence: ALTER ROLE PASSWORD +
    creds записываются (ранний return удалён — BUG-0605/DATA-201). Полное покрытие ветки —
    tests/unit/test_postgres_ensure_convergence.py."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(router=_make_psql_router(role_exists=True))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0

    sql = " ".join(" ".join(c) for c in runner.calls)
    assert 'ALTER ROLE "myproj_user" WITH LOGIN PASSWORD' in sql, "orphan-role → ALTER ROLE PASSWORD (converge)"
    assert (tmp_path / ".platform-db.env").exists(), "credentials пишутся после конвергенции"


@ldd_trajectory
def test_credentials_regen_env_platform_on_first_role(caplog, tmp_path):
    """W2: при первом создании роли — .env.platform перегенерируется (password-injection CLI)."""
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")

    regen_calls: list[list[str]] = []

    def _router_with_regen_capture(cmd):
        joined = " ".join(str(x) for x in cmd)
        if "gen_env_platform.py" in joined:
            regen_calls.append([str(x) for x in cmd])
            return subprocess.CompletedProcess([], 0, "", "")
        return _make_psql_router(role_exists=False)(cmd)

    runner = FakeCommandRunner(router=_router_with_regen_capture)

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0
    assert len(regen_calls) == 1, "регенерация .env.platform вызывается ровно 1 раз (первое создание роли)"
    assert "--project-dir" in regen_calls[0]
    assert "--name" in regen_calls[0]

    logger.critical("[IMP:9][test] .env.platform regen OK — CLI вызван с --project-dir/--name")


# endregion Tests: W2 (роль + GRANT + credentials)

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


# endregion Tests: R5-negative


# ═══════════════════════════════════════════════════════════════════
# region Tests: QA R15/G4 (DevPlan 14 T1.6) — GRANT таргетинг в целевую БД
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · QA R15/G4 — DDL без -d <db> не проходит
# · Scenario: CREATE DATABASE ... OWNER postgres → проектная роль НЕ owner БД →
#   GRANT CREATE,USAGE ON SCHEMA public в admin-DB (postgres) НЕ даёт прав на схему
#   public целевой БД (pg_database_owner неприменим) — грант обязан исполниться В целевой БД
# · Last fail: 2026-08-25 — все psql-вызовы шли кластерно (без -d), гранты оседали в postgres DB
# · Remove if: CREATE DATABASE сменит OWNER на проектную роль (гранты станут избыточными)
@ldd_trajectory
def test_grant_targets_project_db(caplog, tmp_path):
    """Каждый GRANT/REVOKE-вызов содержит `-d myproj_db`; точный argv schema-grant зафиксирован."""
    caplog.set_level(logging.DEBUG)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    runner = FakeCommandRunner(router=_make_psql_router(role_exists=False))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0

    ddl_calls = [c for c in runner.calls if "GRANT" in " ".join(c) or "REVOKE" in " ".join(c)]
    assert len(ddl_calls) == 3, f"ожидается ровно 3 DDL-вызова (2×GRANT + REVOKE): {ddl_calls}"
    for call in ddl_calls:
        joined = " ".join(call)
        assert " -d myproj_db " in f" {joined} ", f"G4 FAIL: DDL без таргетинга в целевую БД: {joined}"
    # Точный argv schema-grant зафиксирован (регрессия против тихого возврата к кластерным вызовам)
    schema_grant = next(c for c in ddl_calls if "SCHEMA public" in " ".join(c))
    assert schema_grant == [
        "docker",
        "exec",
        "postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "myproj_db",
        "-c",
        'GRANT CREATE, USAGE ON SCHEMA public TO "myproj_user"',
    ], f"argv schema-grant изменился: {schema_grant}"
    # Ролевые операции остаются КЛАСТЕРНЫМИ (pg_roles SELECT / CREATE ROLE — без -d)
    role_calls = [c for c in runner.calls if "pg_roles" in " ".join(c) or "CREATE ROLE" in " ".join(c)]
    assert role_calls, "ролевые операции ожидаются в диалоге"
    for call in role_calls:
        assert "-d" not in call, f"ролевая операция обязана быть кластерной: {call}"
    logger.critical(
        "[IMP:9][test] G4 OK: %d DDL таргетированы в myproj_db, ролевые операции кластерные", len(ddl_calls)
    )


# 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · идемпотентность повторного деплоя после T1.6
# · Scenario: второй прогон (БД существует, роль существует, creds на месте) → GRANT no-op,
#   critical_failures=0, rc=0
# · Last fail: N/A (preventive — таргетинг не должен ломать идемпотентность)
# · Remove if: hook-семантика изменится на неидемпотентную
@ldd_trajectory
def test_grant_idempotent_rerun(caplog, tmp_path):
    """Повторный деплой → GRANT исполняется повторно как no-op без critical_failures."""
    caplog.set_level(logging.DEBUG)
    _write_yaml(tmp_path, "name: myproj\nneeds:\n  database: myproj_db\n")
    (tmp_path / ".platform-db.env").write_text("PLATFORM_POSTGRES_PASSWORD=known-pass\n", encoding="utf-8")
    runner = FakeCommandRunner(router=_make_psql_router(create_db_rc=1, role_exists=True))

    assert _invoke_hook(str(tmp_path), "myproj", runner) == 0

    ddl_calls = [c for c in runner.calls if "GRANT" in " ".join(c) or "REVOKE" in " ".join(c)]
    assert len(ddl_calls) == 3 and all(" -d myproj_db " in f"{' '.join(c)} " for c in ddl_calls), (
        f"повторные DDL обязаны остаться таргетированными: {ddl_calls}"
    )
    assert "critical_failures=0" in caplog.text, f"ожидаются нулевые critical_failures:\n{caplog.text[-1500:]}"
    logger.critical("[IMP:9][test] idempotent rerun OK — grants re-applied as no-op, 0 critical failures")


# endregion Tests: QA R15/G4
