"""
# GREP_SUMMARY: e2e shared-db-access pgbouncer wildcard needs.database role-connect local-stack integration auth-failure negative
# STRUCTURE: ▶ fixtures (postgres/pgbouncer running + wildcard ini → FAIL R4) → ▶ hook main() → БД+роль → ▶ psql через pgbouncer:6432 с ролью (SELECT 1) → ▶ negative: несуществующая роль → auth failure (не «no such database») → ▶ cleanup DROP DATABASE/DROP ROLE → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  E2E-тест шаред-доступа к БД на ЛОКАЛЬНОМ docker-стеке (DevPlan 133 W2.5):
##           needs.database → хук postgres (БД + роль + GRANT + credentials) → подключение
##           через pgbouncer:6432 ролью проекта → SELECT 1; negative (R5): несуществующая
##           роль → auth failure, а НЕ «no such database» (баг жёсткого pgbouncer-списка).
## @scope    Local docker stack only (контейнеры postgres/pgbouncer из полного стека).
##           Маркер integration (docker suite, single-process). НЕ требует NODE/test-VPS.
## @invariants
##   - Контейнеры postgres/pgbouncer отсутствуют → pytest.fail (Rule R4: NO_SERVICE = FAIL, не skip)
##   - pgbouncer.ini без wildcard-записи ('* = ...') → pytest.fail с инструкцией
##     `docker compose up -d --force-recreate pgbouncer` (одноразовая миграция стека, D5)
##   - Cleanup (DROP DATABASE/DROP ROLE) — в finally: тест не оставляет мусор в postgres
##   - POSTGRES_PASSWORD читается из env → repo .env (пароль postgres для хука)
##   - Хук вызывается нативно (main(argv=...)), docker exec psql — реальные вызовы
## @rationale DevPlan 133 AC3: локальный e2e — единственный способ доказать, что wildcard
##            pgbouncer + роль/GRANT/credentials-канал работают end-to-end (unit-тесты
##            мокают docker exec). R5-negative фиксирует найденный баг «pgbouncer no such
##            database» (жёсткий список DATABASE_URLS).
## @changes 2026-08-03 | Created (DevPlan 133 W2.5)
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import textwrap

import pytest

from tests._conftest.honesty import require_docker_or_fail
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_POSTGRES_CONTAINER = "postgres"
_PGBOUNCER_CONTAINER = "pgbouncer"

_TEST_PROJECT = "e2e_dbproj"  # валидное имя проекта: роль = <project>_user (regex ^[a-zA-Z0-9_]+$)
_TEST_DB = "e2e_dbproj_db"
_TEST_ROLE = "e2e_dbproj_user"
_WILDCARD_LINE = "* = host=postgres port=5432 auth_user=postgres"


def _docker_exec(
    container: str, *args: str, env_extra: dict[str, str] | None = None, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run a command inside a container (docker exec) with optional extra env."""
    cmd = ["docker", "exec"]
    for k, v in (env_extra or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [container, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _psql_in_postgres(*psql_args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run psql inside the postgres container (superuser, local socket)."""
    return _docker_exec(_POSTGRES_CONTAINER, "psql", "-U", "postgres", *psql_args, env_extra=env_extra)


def _drop_test_artifacts() -> None:
    """Drop test DB + role. GRANT-ы создают dependency → REVOKE перед DROP (идемпотентно)."""
    # GRANT CREATE,USAGE ON SCHEMA public создаёт dependency → без REVOKE DROP ROLE падает
    _psql_in_postgres("-c", f'REVOKE CREATE, USAGE ON SCHEMA public FROM "{_TEST_ROLE}";')
    _psql_in_postgres("-c", f'REVOKE CONNECT ON DATABASE "{_TEST_DB}" FROM "{_TEST_ROLE}";')
    # WITH (FORCE) — pgbouncer держит pooled-соединения к БД
    _psql_in_postgres("-c", f"DROP DATABASE IF EXISTS {_TEST_DB} WITH (FORCE);")
    _psql_in_postgres("-c", f'DROP ROLE IF EXISTS "{_TEST_ROLE}";')


def _load_postgres_password() -> str:
    """POSTGRES_PASSWORD: env → repo .env (пароль для psql-gate хука)."""
    pw = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD")
    if pw:
        return pw
    env_file = repo_root() / ".env"
    if env_file.is_file():
        for line_raw in env_file.read_text(encoding="utf-8").splitlines():
            line = line_raw.strip()
            if line.startswith("POSTGRES_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return ""


# ═══════════════════════════════════════════════════════════════════
# region FIXTURE_shared_db_stack
@pytest.fixture()
def shared_db_stack() -> None:
    """Precondition: postgres+pgbouncer running with wildcard pgbouncer.ini (R4: FAIL, не skip)."""
    require_docker_or_fail(reason="shared-db-access e2e requires Docker daemon")

    for container in (_POSTGRES_CONTAINER, _PGBOUNCER_CONTAINER):
        inspect = _docker_exec(container, "sh", "-c", "echo alive")
        if inspect.returncode != 0:
            pytest.fail(
                f"Container '{container}' not running — запусти локальный стек (`make up`). "
                f"NO_SERVICE = FAIL (Rule R4), not skip. stderr: {inspect.stderr.strip()[-200:]}",
                pytrace=False,
            )

    # Wildcard-маршрутизация (DevPlan 133 D5): '*' entry в pgbouncer.ini
    ini = _docker_exec(_PGBOUNCER_CONTAINER, "cat", "/etc/pgbouncer/pgbouncer.ini")
    if ini.returncode != 0 or _WILDCARD_LINE not in ini.stdout:
        pytest.fail(
            "pgbouncer.ini не содержит wildcard-записи '* = host=postgres port=5432 auth_user=postgres'. "
            "Выполни одноразовую миграцию стека: `docker compose up -d --force-recreate pgbouncer` "
            f"(DevPlan 133 D5). Текущий ini: {ini.stdout.strip()[:400]}",
            pytrace=False,
        )

    logger.info("[IMP:9][fixture][shared_db_stack] postgres+pgbouncer ready (wildcard '%s')", _WILDCARD_LINE)


# endregion FIXTURE_shared_db_stack


# ═══════════════════════════════════════════════════════════════════
# region TEST_full_cycle
@pytest.mark.integration
@pytest.mark.local_stack
@pytest.mark.requires_docker
# 🧪 TRAP[TEST] · Scenario: needs.database → хук → БД+роль+GRANT+credentials → psql через pgbouncer
# · Regression: роль не создавалась / pgbouncer «no such database» для новых БД (жёсткий список)
# · Last fail: 2026-08-03 — шаред-доступ сломан в 2 точках (эмпирика DevPlan 133)
# · Remove if: хук postgres / wildcard pgbouncer удалены или заменены
def test_shared_db_full_cycle_via_pgbouncer(shared_db_stack, tmp_path, caplog) -> None:
    """Full cycle: hook provisions DB+role+GRANT+credentials → role connects via pgbouncer:6432."""
    caplog.set_level(logging.INFO)

    password = _load_postgres_password()
    assert password, "POSTGRES_PASSWORD не найден (env или repo .env) — psql-gate хука не пройдёт"

    # ── temp-проект с needs.database ──
    project_dir = tmp_path / _TEST_PROJECT
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text(
        textwrap.dedent(
            f"""\
            name: {_TEST_PROJECT}
            type: backend
            target_node: local
            needs:
              database: {_TEST_DB}
            """
        ),
        encoding="utf-8",
    )

    # ── pre-clean: стейт от предыдущих прогонов (тесты идут по nodeid-порядку) ──
    _drop_test_artifacts()

    # ── запуск хука (native, POSTGRES_PASSWORD в env) ──
    from core.modules.postgres.hooks import on_project_deploy

    os.environ["POSTGRES_PASSWORD"] = password
    try:
        rc = on_project_deploy.main(argv=[str(project_dir), _TEST_PROJECT, "local"])
    finally:
        os.environ.pop("POSTGRES_PASSWORD", None)
    assert rc == 0, f"postgres hook failed (rc={rc})"

    # ── assert: БД существует ──
    db_check = _psql_in_postgres("-tAc", f"SELECT 1 FROM pg_database WHERE datname='{_TEST_DB}'")
    assert db_check.returncode == 0 and db_check.stdout.strip() == "1", (
        f"Database '{_TEST_DB}' not created: {db_check.stdout}{db_check.stderr}"
    )

    # ── assert: роль существует ──
    role_check = _psql_in_postgres("-tAc", f"SELECT 1 FROM pg_roles WHERE rolname='{_TEST_ROLE}'")
    assert role_check.returncode == 0 and role_check.stdout.strip() == "1", (
        f"Role '{_TEST_ROLE}' not created: {role_check.stdout}{role_check.stderr}"
    )

    # ── assert: credentials-файл (0600) с паролем роли ──
    creds_file = project_dir / ".platform-db.env"
    assert creds_file.is_file(), ".platform-db.env не создан"
    assert (creds_file.stat().st_mode & 0o777) == 0o600
    creds = dict(
        line.split("=", 1)
        for line in creds_file.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    role_password = creds.get("PLATFORM_POSTGRES_PASSWORD", "")
    assert creds.get("PLATFORM_POSTGRES_USER") == _TEST_ROLE
    assert creds.get("PLATFORM_POSTGRES_DB") == _TEST_DB
    assert role_password, "PLATFORM_POSTGRES_PASSWORD отсутствует в .platform-db.env"

    # ── assert: .env.platform перегенерирован с реальным паролем (password-injection) ──
    env_platform = project_dir / ".env.platform"
    assert env_platform.is_file(), ".env.platform не перегенерирован"
    env_text = env_platform.read_text(encoding="utf-8")
    dsn_line = next((line for line in env_text.splitlines() if line.startswith("PLATFORM_POSTGRES_DSN=")), "")
    assert role_password in dsn_line, f"пароль не инжектирован в DSN: {dsn_line}"
    assert "***" not in dsn_line

    # ── assert: подключение через pgbouncer:6432 ролью проекта (SELECT 1) ──
    # psql-клиент из postgres-контейнера, идёт ЧЕРЕЗ pgbouncer (host=pgbouncer, shared-db-net)
    conn = _psql_in_postgres(
        "-h",
        "pgbouncer",
        "-p",
        "6432",
        "-U",
        _TEST_ROLE,
        "-d",
        _TEST_DB,
        "-tAc",
        "SELECT 1",
        env_extra={"PGPASSWORD": role_password},
    )
    assert conn.returncode == 0 and conn.stdout.strip() == "1", (
        f"psql через pgbouncer с ролью {_TEST_ROLE} failed: {conn.stdout}{conn.stderr}"
    )

    logger.critical("[IMP:9][e2e][shared_db] FULL CYCLE OK — БД+роль+GRANT+credentials → SELECT 1 через pgbouncer:6432")

    # ── cleanup (в конце теста — не оставляем мусор в postgres) ──
    _drop_test_artifacts()


# endregion TEST_full_cycle


# ═══════════════════════════════════════════════════════════════════
# region TEST_negative_r5
@pytest.mark.integration
@pytest.mark.local_stack
@pytest.mark.requires_docker
# 🧪 TRAP[TEST] · NEGATIVE (R5) · pgbouncer wildcard — несуществующая роль → auth failure
# · Scenario: подключение несуществующей ролью к существующей БД → auth failure,
# ·   а НЕ «no such database» (баг жёсткого pgbouncer-списка, DevPlan 133 эмпирика)
# · Last fail: 2026-08-03 — pgbouncer.ini со списком platform/litellm/langfuse отвечал
# ·   «no such database» на любую новую БД (маршрутизация ломалась ДО auth)
# · Remove if: wildcard-маршрутизация pgbouncer отменена (D5 пересмотр)
def test_negative_nonexistent_role_auth_failure_not_no_such_database(shared_db_stack, tmp_path, caplog) -> None:
    """R5-negative: wildcard — неизвестная роль → auth failure, НЕ «no such database»."""
    caplog.set_level(logging.INFO)

    # Создаём БД для проверки (через прямой psql), подключаемся несуществующей ролью
    _psql_in_postgres("-c", f"CREATE DATABASE {_TEST_DB} OWNER postgres;")
    try:
        conn = _psql_in_postgres(
            "-h",
            "pgbouncer",
            "-p",
            "6432",
            "-U",
            "definitely_missing_role",
            "-d",
            _TEST_DB,
            "-tAc",
            "SELECT 1",
            env_extra={"PGPASSWORD": "wrong-password"},
        )
    finally:
        _psql_in_postgres("-c", f"DROP DATABASE IF EXISTS {_TEST_DB};")

    output = (conn.stdout or "") + (conn.stderr or "")
    assert "no such database" not in output.lower(), (
        f"R5 FAIL: pgbouncer вернул «no such database» — жёсткий список жив: {output}"
    )
    # Auth failure: pgbouncer отклоняет неизвестную роль ДО маршрутизации
    # («no such user» — собственное сообщение pgbouncer для scram-auth против pg_shadow)
    assert conn.returncode != 0, "несуществующая роль НЕ должна подключиться"
    assert any(
        k in output.lower() for k in ("password authentication failed", "auth failed", "no such user", "no pg_hba.conf")
    ), f"ожидался auth failure, получено: {output}"

    logger.critical(
        "[IMP:9][e2e][shared_db] R5-negative OK — missing role → auth failure (не «no such database»): %s",
        output.strip()[:120],
    )


# endregion TEST_negative_r5


# ═══════════════════════════════════════════════════════════════════
# region TEST_idempotent_redeploy
@pytest.mark.integration
@pytest.mark.local_stack
@pytest.mark.requires_docker
# 🧪 TRAP[TEST] · Scenario: повторный деплой — роль НЕ пересоздаётся, пароль не меняется
# · Regression: идемпотентность хука (повторный запуск не ломает выданный credentials)
# · Last fail: N/A (new test)
# · Remove if: ensure_project_db_access() идемпотентность отменена
def test_idempotent_redeploy_role_unchanged(shared_db_stack, tmp_path, caplog) -> None:
    """Idempotency: повторный запуск хука → пароль роли НЕ меняется (credentials стабильны)."""
    caplog.set_level(logging.INFO)

    password = _load_postgres_password()
    assert password, "POSTGRES_PASSWORD не найден"

    project_dir = tmp_path / _TEST_PROJECT
    project_dir.mkdir()
    (project_dir / "ai-platform.yaml").write_text(
        textwrap.dedent(
            f"""\
            name: {_TEST_PROJECT}
            type: backend
            target_node: local
            needs:
              database: {_TEST_DB}
            """
        ),
        encoding="utf-8",
    )

    # ── pre-clean: стейт от предыдущих прогонов ──
    _drop_test_artifacts()

    from core.modules.postgres.hooks import on_project_deploy

    os.environ["POSTGRES_PASSWORD"] = password
    try:
        rc1 = on_project_deploy.main(argv=[str(project_dir), _TEST_PROJECT, "local"])
        creds1 = (project_dir / ".platform-db.env").read_text(encoding="utf-8")
        rc2 = on_project_deploy.main(argv=[str(project_dir), _TEST_PROJECT, "local"])
        creds2 = (project_dir / ".platform-db.env").read_text(encoding="utf-8")
    finally:
        os.environ.pop("POSTGRES_PASSWORD", None)

    assert rc1 == 0 and rc2 == 0
    pw1 = next(line.split("=", 1)[1] for line in creds1.splitlines() if line.startswith("PLATFORM_POSTGRES_PASSWORD="))
    pw2 = next(line.split("=", 1)[1] for line in creds2.splitlines() if line.startswith("PLATFORM_POSTGRES_PASSWORD="))
    assert pw1 == pw2, "повторный деплой НЕ должен менять пароль роли (иначе ломается выданный credentials)"

    # ── cleanup (assertive: REVOKE → DROP — GRANT-ы создают dependency на роль) ──
    try:
        _psql_in_postgres("-c", f'REVOKE CREATE, USAGE ON SCHEMA public FROM "{_TEST_ROLE}";')
        _psql_in_postgres("-c", f'REVOKE CONNECT ON DATABASE "{_TEST_DB}" FROM "{_TEST_ROLE}";')
        drop_db = _psql_in_postgres("-c", f"DROP DATABASE IF EXISTS {_TEST_DB} WITH (FORCE);")
        assert drop_db.returncode == 0, f"DROP DATABASE failed: {drop_db.stdout}{drop_db.stderr}"
        drop_role = _psql_in_postgres("-c", f'DROP ROLE IF EXISTS "{_TEST_ROLE}";')
        assert drop_role.returncode == 0, f"DROP ROLE failed: {drop_role.stdout}{drop_role.stderr}"
    finally:
        _drop_test_artifacts()

    logger.critical("[IMP:9][e2e][shared_db] IDEMPOTENCY OK — redeploy → password unchanged, cleanup done")


# endregion TEST_idempotent_redeploy
