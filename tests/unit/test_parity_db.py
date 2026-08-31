"""
# GREP_SUMMARY: test parity_db parity database create drop DSN privileged-path docker-exec-psql ssh CommandRunner DI idempotent already-exists password-hygiene R5 negative resolve_host lambda
# STRUCTURE: ▶ FakeCommandRunner (routing по SQL-подстрокам в remote_cmd) → ▶ create idempotent (DSN ровно 1 строка; repeat → ALTER ROLE) → ▶ drop (DROP DATABASE/ROLE IF EXISTS ×2) → ▶ негатив (psql rc≠0 / invalid project no-ssh) → ▶ password hygiene (pw не в caplog) → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/parity_db.py (DevPlan 019 TASK-6, AC5): create/drop
##           временной parity-БД через привилегированный путь (ssh root@host → docker exec postgres
##           psql). Покрытие $TEST_SPEC: create → DSN в stdout; повторный create → ALTER ROLE rotation;
##           drop → DROP DATABASE+ROLE IF EXISTS; негативы (R5) + пароль-гигиена.
## @scope    Tests the CLI contract: main(argv, *, runner, resolve_host) — runner/resolve_host DI
##           (fake вместо monkeypatch subprocess.run / setattr — канон 160/E1).
## @invariants
##   - subprocess через FakeCommandRunner (runner=) — 0 monkeypatch subprocess.run
##   - resolve_host DI-параметр (lambda node: "1.2.3.4") — 0 monkeypatch.setattr
##   - main() returns int (0 ok / 1 generic / 2 config-not-found)
##   - stdout-контракт: create → РОВНО одна DSN-строка; drop → пусто
##   - @ldd_trajectory на успешных путях (main пишет IMP:9 START/DONE)
## @changes 2026-08-31 | DevPlan 019 TASK-6 — Created
# endregion MODULE_CONTRACT
"""

import logging
import subprocess

import pytest

from core.internal.deploy import parity_db
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

_NODE = "asi-team-vps"
_HOST = "1.2.3.4"


class FakeCommandRunner:
    """Command-routing FakeCommandRunner (E1 DI): psql-диалог ноды по содержимому remote_cmd.

    ## @purpose — Замена monkeypatch parity_db.subprocess.run: каждый вызов записывается (calls),
    ##            результат выбирается по содержимому команды (pg_roles / pg_database / CREATE ROLE /
    ##            ALTER ROLE / CREATE DATABASE / GRANT / REVOKE) — эталон test_on_project_deploy.
    ## @io — ⇥ router: callable(cmd) → CompletedProcess; default → ⎋ CompletedProcess
    ## @complexity — O(1) — routing по подстроке
    """

    def __init__(self, router=None, default=None):
        self._router = router
        self.default = default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=60, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        if self._router is not None:
            return self._router(cmd)
        return self.default


def _make_router(*, role_exists: bool = False, db_exists: bool = False):
    """Build a command-routing fake (psql-диалог ноды по SQL-подстрокам в remote_cmd)."""

    def _router(cmd):
        joined = " ".join(str(x) for x in cmd)
        if "pg_roles" in joined:
            return subprocess.CompletedProcess([], 0, "1\n" if role_exists else "", "")
        if "pg_database" in joined:
            return subprocess.CompletedProcess([], 0, "1\n" if db_exists else "", "")
        if "CREATE ROLE" in joined:
            return subprocess.CompletedProcess([], 0, "", "")
        if "ALTER ROLE" in joined:
            return subprocess.CompletedProcess([], 0, "", "")
        if "CREATE DATABASE" in joined:
            return subprocess.CompletedProcess([], 0, "", "")
        if "GRANT" in joined:
            return subprocess.CompletedProcess([], 0, "GRANT", "")
        if "REVOKE" in joined:
            return subprocess.CompletedProcess([], 0, "REVOKE", "")
        return subprocess.CompletedProcess([], 0, "", "")

    return _router


def _invoke(action: str, project: str, runner: FakeCommandRunner | None = None) -> int:
    """Route through main() with runner + resolve_host DI (E1): ssh → root@1.2.3.4."""
    return parity_db.main(
        argv=["--action", action, "--project", project, "--node", _NODE],
        runner=runner,
        resolve_host=lambda _node: _HOST,
    )


def _assert_ssh_argv(runner: FakeCommandRunner) -> None:
    """Каждая psql-команда идёт через ssh root@host (полный argv — ["ssh", *SSH_OPTS, root@host, remote_cmd])."""
    ssh_calls = [c for c in runner.calls if c[0] == "ssh"]
    assert ssh_calls, "каждая psql-команда обязана идти через ssh root@host"
    for call in ssh_calls:
        assert "-o" in call, f"SSH_OPTS обязаны присутствовать в argv: {call}"
        assert f"root@{_HOST}" in call, f"target root@host: {call}"
        assert len(call) >= 2 and call[-1].startswith("docker exec postgres psql"), f"remote_cmd: {call}"


# ═══════════════════════════════════════════════════════════════════
# region Tests: create → DSN (AC5, $TEST_SPEC)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · parity-db create — идемпотентность + DSN-контракт (AC5)
# · Scenario: первый create (роли/БД нет) → CREATE ROLE + CREATE DATABASE OWNER + GRANT/REVOKE →
#   stdout РОВНО одна DSN-строка postgresql://parity_<p>_user:...@pgbouncer:6432/parity_<p>;
#   повторный create (роль/БД есть) → ALTER ROLE rotation (НЕ CREATE), DSN повторно напечатан
# · Last fail: N/A (preventive — контракт 019 TASK-6: повторный create обязан остаться идемпотентным)
# · Remove if: parity-db CLI контракт изменится (не-DSN вывод / неидемпотентность)
@ldd_trajectory
def test_parity_db_create_prints_dsn_idempotent(caplog, capsys):
    """Первый create → DSN; повторный create → ALTER ROLE rotation + DSN re-print."""
    project = "managers-bot"

    # ── Первый create: роль/БД отсутствуют → CREATE ROLE + CREATE DATABASE + GRANT/REVOKE ──
    runner = FakeCommandRunner(router=_make_router(role_exists=False, db_exists=False))
    rc = _invoke("create", project, runner)
    assert rc == 0, f"первый create обязан завершиться exit 0, got {rc}"

    out = capsys.readouterr().out
    dsn_lines = [line for line in out.splitlines() if line.strip()]
    assert len(dsn_lines) == 1, f"stdout обязан содержать РОВНО одну DSN-строку, got {dsn_lines!r}"
    dsn = dsn_lines[0]
    assert dsn.startswith("postgresql://parity_managers-bot_user:"), f"DSN-префикс роли: {dsn}"
    assert "@pgbouncer:6432/parity_managers-bot" in dsn, f"DSN host/db: {dsn}"

    joined = " ".join(" ".join(c) for c in runner.calls)
    assert 'CREATE ROLE "parity_managers-bot_user" LOGIN PASSWORD' in joined, joined
    assert 'CREATE DATABASE "parity_managers-bot" OWNER "parity_managers-bot_user"' in joined, joined
    assert 'GRANT ALL ON SCHEMA public TO "parity_managers-bot_user"' in joined, joined
    assert 'REVOKE CONNECT ON DATABASE "parity_managers-bot" FROM PUBLIC' in joined, joined
    _assert_ssh_argv(runner)

    # ── Повторный create: роль/БД существуют → ALTER ROLE rotation, НЕ CREATE; DSN повторно ──
    runner2 = FakeCommandRunner(router=_make_router(role_exists=True, db_exists=True))
    rc2 = _invoke("create", project, runner2)
    assert rc2 == 0, f"повторный create обязан завершиться exit 0, got {rc2}"

    out2 = capsys.readouterr().out
    dsn2_lines = [line for line in out2.splitlines() if line.strip()]
    assert len(dsn2_lines) == 1, f"повторный create обязан повторно напечатать DSN, got {dsn2_lines!r}"
    assert dsn2_lines[0].startswith("postgresql://parity_managers-bot_user:")

    joined2 = " ".join(" ".join(c) for c in runner2.calls)
    assert "ALTER ROLE" in joined2, "повторный create обязан ротировать пароль через ALTER ROLE"
    assert "CREATE ROLE" not in joined2, "повторный create НЕ пересоздаёт роль"
    assert "CREATE DATABASE" not in joined2, "существующая БД НЕ пересоздаётся"
    logger.critical("[IMP:9][test] create idempotent OK — DSN 1 строка ×2, repeat → ALTER ROLE")


# endregion Tests: create → DSN


# ═══════════════════════════════════════════════════════════════════
# region Tests: drop (AC5, $TEST_SPEC)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · parity-db drop — DROP DATABASE + DROP ROLE IF EXISTS
# · Scenario: drop → DROP DATABASE IF EXISTS "parity_<p>" WITH (FORCE) + DROP ROLE IF EXISTS;
#   stdout пуст; повторный drop → exit 0 (отсутствие — не ошибка, IF EXISTS)
# · Last fail: N/A (preventive — контракт 019 TASK-6: drop обязан быть идемпотентным)
# · Remove if: drop-семантика изменится (отсутствие станет ошибкой)
@ldd_trajectory
def test_parity_db_drop_removes_db_and_role(caplog, capsys):
    """drop → DROP DATABASE IF EXISTS ... WITH (FORCE) + DROP ROLE IF EXISTS; повторный drop exit 0."""
    project = "managers-bot"

    runner = FakeCommandRunner(router=_make_router())
    rc = _invoke("drop", project, runner)
    assert rc == 0, f"drop обязан завершиться exit 0, got {rc}"

    joined = " ".join(" ".join(c) for c in runner.calls)
    assert 'DROP DATABASE IF EXISTS "parity_managers-bot" WITH (FORCE)' in joined, joined
    assert 'DROP ROLE IF EXISTS "parity_managers-bot_user"' in joined, joined
    _assert_ssh_argv(runner)
    assert not capsys.readouterr().out, "drop не печатает stdout (DSN-контракт только у create)"

    # ── Повторный drop: отсутствие — не ошибка (IF EXISTS → rc 0) ──
    runner2 = FakeCommandRunner(router=_make_router())
    rc2 = _invoke("drop", project, runner2)
    assert rc2 == 0, f"повторный drop обязан быть exit 0 (отсутствие не ошибка), got {rc2}"
    logger.critical("[IMP:9][test] drop OK — DROP DATABASE+ROLE IF EXISTS, повторный drop exit 0")


# endregion Tests: drop


# ═══════════════════════════════════════════════════════════════════
# region Tests: негатив (Test Honesty R5)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · NEGATIVE (R5) · parity-db — psql rc≠0 без already-exists → EXIT_GENERIC + IMP:10
# · Scenario: первый SELECT (role-exists) падает (rc=1, "connection refused") → main возвращает
#   EXIT_GENERIC (1), [IMP:10] в caplog; диалог прекращается на первом же ssh-вызове
# · Last fail: N/A (preventive — контракт «Ошибки psql → IMP:10 + EXIT_GENERIC»)
# · Remove if: error-семантика parity-db изменится (graceful вместо fail-fast)
@ldd_trajectory
def test_parity_db_psql_failure_returns_generic(caplog):
    """psql rc≠0 → EXIT_GENERIC (1) + IMP:10 в caplog."""
    runner = FakeCommandRunner(default=subprocess.CompletedProcess([], 1, "", "connection refused"))
    rc = _invoke("create", "x", runner)
    assert rc == 1, f"psql-ошибка обязана дать EXIT_GENERIC, got {rc}"
    assert "[IMP:10]" in caplog.text, f"ожидается IMP:10 в логе:\n{caplog.text[-1500:]}"
    assert "role-exists check failed" in caplog.text
    assert len(runner.calls) == 1, "диалог обязан остановиться на первой psql-ошибке"


# 🧪 TRAP[TEST] · NEGATIVE (R5) · parity-db — невалидное имя проекта → NO SSH
# · Scenario: --project ../evil (path traversal) → EXIT_GENERIC (1) ДО резолва/ssh;
#   runner.calls пустые (ssh не вызывался); [IMP:10] + "Invalid project name" в caplog
# · Last fail: N/A (preventive — fail-fast валидация имени, канон validate_project_name)
# · Remove if: валидация имени проекта будет перенесена в другой слой
def test_parity_db_invalid_project_no_ssh(caplog, capsys):
    """--project ../evil → EXIT_GENERIC, ssh НЕ вызывается (calls пустые)."""
    runner = FakeCommandRunner(router=_make_router())
    rc = _invoke("create", "../evil", runner)
    assert rc == 1, f"невалидное имя проекта обязано дать EXIT_GENERIC, got {rc}"
    assert runner.calls == [], f"ssh НЕ должен вызываться при невалидном имени проекта: {runner.calls}"
    assert "[IMP:10]" in caplog.text and "Invalid project name" in caplog.text
    assert not capsys.readouterr().out, "невалидный ввод не печатает DSN"


# endregion Tests: негатив


# ═══════════════════════════════════════════════════════════════════
# region Tests: пароль-гигиена
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · REGRESSION · parity-db — пароль НЕ протекает в логи (инвариант безопасности)
# · Scenario: create → DSN извлекается из stdout; пароль (token_urlsafe(24)) из DSN не встречается
#   в caplog.text (IMP-логи не содержат pw; DSN печатается только в stdout контракта)
# · Last fail: N/A (preventive — инвариант «Пароль НЕ в логах», DevPlan 019 TASK-6)
# · Remove if: DSN/log-контракт parity-db изменится
@ldd_trajectory
def test_parity_db_password_not_in_logs(caplog, capsys):
    """Пароль из DSN отсутствует в caplog; DSN печатается ровно один раз."""
    runner = FakeCommandRunner(router=_make_router(role_exists=False, db_exists=False))
    rc = _invoke("create", "pwcheck", runner)
    assert rc == 0

    dsn_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(dsn_lines) == 1, f"DSN обязан быть ровно одной строкой, got {dsn_lines!r}"
    pw = dsn_lines[0].split("@", 1)[0].rsplit(":", 1)[1]
    assert pw, "пароль не извлечён из DSN"
    assert len(pw) >= 20, f"пароль — token_urlsafe(24), got len={len(pw)}"
    assert pw not in caplog.text, f"пароль {pw[:4]}... протёк в логи: {caplog.text[-800:]}"
    logger.critical("[IMP:9][test] password hygiene OK — DSN 1 строка, пароль отсутствует в caplog")


# endregion Tests: пароль-гигиена
