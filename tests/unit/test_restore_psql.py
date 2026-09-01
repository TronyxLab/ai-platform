"""
# GREP_SUMMARY: test-restore-psql self-role-filter black-hole regression post-check DATA-504 expected-dbs R5-negative latch
# STRUCTURE: ▶ filter: self-role cut + контент после ролей жив → ▶ R5-negative (старый skip-latch) → ▶ multi-line ALTER ROLE → ▶ non-matching role → ▶ SCRAM `;` в пароле → ▶ db-check: missing→3 / present→0 / empty→3 → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Тесты DR-restore guardrails (P0: restore молча уничтожал данные):
##           1) restore_self_role_filter.awk — вырезает ТОЛЬКО self-role statements
##              (CREATE/DROP/ALTER ROLE <U>), сохраняет ВСЁ остальное, включая контент
##              ПОСЛЕ role-секции (regression на black-hole: старый фильтр защёлкивал
##              skip на `DROP ROLE IF EXISTS <U>;` и ронял весь остаток дампа — БД + данные).
##           2) restore_db_check.py — пост-проверка наличия БД (DATA-504): ожидаемые из
##              дампа vs кластер; нехватка → exit 3 + список; fail-closed на пустом списке.
## @scope    tests/unit; awk-фильтр исполняется через awk-subprocess — артефакт под тестом
##           И ЕСТЬ awk-программа (Python-native пути нет); это интеграционно-флейворный
##           класс (pytest-infra: subprocess запрещён для PYTHON-бизнес-логики; shell/awk
##           артефакты исполняются своим реальным интерпретатором). restore_db_check.py
##           тестируется нативно (pure функции + main с fake-runner DI, 0 патчей).
## @invariants
##   - Старый фильтр (skip-latch) закодирован константой _OLD_LATCH_FILTER — документирует
##     исходный P0-вход (R5-negative: негатив не может существовать без детектора).
##   - Новый фильтр ОБЯЗАН сохранять ВСЁ после self-role statements: DROP DATABASE,
##     CREATE DATABASE, CREATE TABLE, COPY + данные (иначе чёрная дыра возвращается).
##   - Multi-line ALTER ROLE U ... (до `;`/`);`) вырезается целиком; несовпадающая роль
##     (myplatform при U=platform) — сохраняется; SCRAM/`;` внутри пароля — корректен.
##   - Post-check: missing → exit 3 со списком; all present → 0; пустой expected → 3 (fail-closed).
##   - LDD IMP:9 trajectory — на каждом тесте (@ldd_trajectory + logger.info).
## @rationale P0-инцидент: round-trip на проде удалил langfuse/litellm/platform БД при rc=0
##            («restore complete» над пустым кластером). Тесты фиксируют и фикс, и форму бага.
## @changes 2026-09-01 | P0 fix — created
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from restore_db_check import SYSTEM_DBS, compare_dbs, main, read_expected_names

_FILTER_AWK = _SCRIPTS_DIR / "restore_self_role_filter.awk"

# Старый фильтр (skip-latch) — буквально из restore_psql.sh@5e34401 (DevPlan 017 F-19),
# awk-программа после bash-unescape. Документирует исходный P0-вход (R5-negative).
_OLD_LATCH_FILTER = (
    '(/^CREATE ROLE / || /^DROP ROLE /) && index($0, U ";") != 0 {skip=1; next} '
    '/^ALTER ROLE / && index($0, U ";") != 0 {instmt=1} '
    "{if(!skip) print} "
    "/^\\\\);[ ]*$/{if(instmt){instmt=0;next}}"
)

# pg_dumpall --clean --if-exists: DROP DATABASE стоят ДО role-дропов, `DROP ROLE IF EXISTS
# platform;` — РАНЬШЕ секций ролей и баз (точный порядок, поймавший баг на проде).
_SYNTH_DUMP = (
    "--\n"
    "-- PostgreSQL database cluster dump\n"
    "--\n"
    "SET statement_timeout = 0;\n"
    "-- Database Drops\n"
    "DROP DATABASE IF EXISTS langfuse;\n"
    "DROP DATABASE IF EXISTS litellm;\n"
    "DROP DATABASE IF EXISTS platform;\n"
    "-- Role Drops\n"
    "DROP ROLE IF EXISTS platform;\n"
    "DROP ROLE IF EXISTS langfuse;\n"
    "-- Roles\n"
    "CREATE ROLE platform;\n"
    "ALTER ROLE platform WITH NOSUPERUSER LOGIN PASSWORD 'SCRAM-SHA-256$4096:AA:BB=CC';\n"
    "CREATE ROLE langfuse;\n"
    "ALTER ROLE langfuse WITH NOSUPERUSER LOGIN PASSWORD 'SCRAM-SHA-256$4096:DD:EE=FF';\n"
    "-- Databases\n"
    "CREATE DATABASE langfuse;\n"
    "CREATE DATABASE litellm;\n"
    "CREATE DATABASE platform;\n"
    "-- Data\n"
    "\\connect langfuse\n"
    "CREATE TABLE public.t (id serial);\n"
    "COPY public.t (id) FROM stdin;\n"
    "1\n"
    "2\n"
    "3\n"
    "\\.\n"
)


def _run_awk_cmd(cmd: list[str], dump: str, expected_dbs_file: Path | None = None) -> str:
    """Execute an awk program (artifact under test) with the real awk interpreter.

    ## @purpose — единственный способ исполнения awk-артефакта; env EXPECTED_DBS_FILE
    ##             экспортируется только при явном запросе (как в restore_psql.sh).
    ## @complexity 2 — subprocess + env setup
    """
    env = dict(os.environ)
    if expected_dbs_file is not None:
        env["EXPECTED_DBS_FILE"] = str(expected_dbs_file)
    proc = subprocess.run(
        cmd,
        input=dump,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, f"awk failed: {proc.stderr}"
    return proc.stdout


def _run_filter(dump: str, user: str = "platform", expected_dbs_file: Path | None = None) -> str:
    """Run the PRODUCTION filter artifact (restore_self_role_filter.awk)."""
    return _run_awk_cmd(
        ["awk", "-v", f"U={user}", "-f", str(_FILTER_AWK)],
        dump,
        expected_dbs_file=expected_dbs_file,
    )


class _FakeRunner:
    """Fake subprocess-like runner for restore_db_check.main (docker exec DI-seam)."""

    def __init__(self, stdout: str, returncode: int = 0):
        self._stdout = stdout
        self._returncode = returncode
        self.calls: list[list[str]] = []

    def run(self, cmd: list, **_kwargs):
        self.calls.append(cmd)

        class _Result:
            pass

        res = _Result()
        res.returncode = self._returncode
        res.stdout = self._stdout
        res.stderr = ""
        return res


# region filter: self-role cut + контент после ролей жив


# 🧪 TRAP[TEST] · Regression · Scenario: P0 black-hole — self-role statements вырезаются,
# · ВСЁ остальное (DROP DATABASE, CREATE DATABASE, таблицы, COPY-данные) сохраняется ПОСЛЕ ролей
# · Last fail: 2026-09-01 prod round-trip — после `DROP ROLE IF EXISTS platform;` весь дамп
# ·   (CREATE DATABASE + 11k строк) отфильтровывался; базы удалены при rc=0
# · Remove if: фильтр перестанет вырезать self-role ИЛИ пост-проверка заменит фильтр
@ldd_trajectory
def test_filter_preserves_all_content_after_self_role_drop(caplog, tmp_path) -> None:
    """P0-regression: после self-role секции весь контент (БД + данные) жив, роли U вырезаны."""
    expected_file = tmp_path / "expected_dbs.txt"
    out = _run_filter(_SYNTH_DUMP, expected_dbs_file=expected_file)

    # Self-role statements для platform — вырезаны
    assert "DROP ROLE IF EXISTS platform;" not in out
    assert "CREATE ROLE platform;" not in out
    assert "ALTER ROLE platform WITH" not in out
    # Роли ДРУГИХ пользователей — сохранены
    assert "DROP ROLE IF EXISTS langfuse;" in out
    assert "CREATE ROLE langfuse;" in out
    assert "ALTER ROLE langfuse WITH" in out
    # DROP DATABASE — проходят (--clean семантика)
    assert "DROP DATABASE IF EXISTS langfuse;" in out
    assert "DROP DATABASE IF EXISTS litellm;" in out
    assert "DROP DATABASE IF EXISTS platform;" in out
    # ВЕСЬ контент после role-секции — сохранён (это и был black-hole)
    assert "CREATE DATABASE langfuse;" in out
    assert "CREATE DATABASE litellm;" in out
    assert "CREATE DATABASE platform;" in out
    assert "CREATE TABLE public.t (id serial);" in out
    assert "COPY public.t (id) FROM stdin;" in out
    assert "3" in out  # строка данных
    # Side-output ожидаемых БД (для post-check DATA-504)
    expected = read_expected_names(expected_file)
    assert sorted(expected) == ["langfuse", "litellm", "platform"], f"expected names: {expected}"
    logger.info("[IMP:9][test_restore_psql] filter: self-role cut, all content after roles preserved ✓")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · restore_self_role_filter — P0 skip-latch black-hole
# · Scenario: СТАРЫЙ фильтр (защёлка) на точном prod-входе: `DROP ROLE IF EXISTS platform;`
# ·   раньше секций ролей/баз → контент после него (CREATE DATABASE, данные) ПОТЕРЯН
# · Last fail: 2026-09-01 prod — rc=0 «restore complete» над пустым кластером (базы удалены)
# · Remove if: старый фильтр-строка удалён из теста ИЛИ история git недоступна для сверки
@ldd_trajectory
def test_filter_old_latch_blackhole_negative(caplog) -> None:
    """R5-negative: старый фильтр на исходном P0-входе теряет ВЕСЬ контент после DROP ROLE U."""
    out = _run_awk_cmd(["awk", "-v", "U=platform", _OLD_LATCH_FILTER], _SYNTH_DUMP)

    # Черная дыра: DROP DATABASE (ДО латча) прошли, а ВСЁ после `DROP ROLE IF EXISTS platform;`
    # (CREATE DATABASE + данные) — отфильтровано
    assert "DROP DATABASE IF EXISTS langfuse;" in out
    assert "DROP DATABASE IF EXISTS litellm;" in out
    assert "DROP DATABASE IF EXISTS platform;" in out
    assert "CREATE DATABASE langfuse;" not in out, "black-hole: CREATE DATABASE потерян"
    assert "CREATE DATABASE platform;" not in out, "black-hole: CREATE DATABASE потерян"
    assert "COPY public.t (id) FROM stdin;" not in out, "black-hole: данные потеряны"
    assert "CREATE ROLE langfuse;" not in out, "black-hole: даже чужие роли потеряны"
    logger.info("[IMP:9][test_restore_psql] R5-negative: old latch filter reproduces the P0 black-hole ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: multi-line ALTER ROLE platform ... до `;`/`);`
# · вырезается ЦЕЛИКОМ, следующий контент жив
# · Last fail: N/A (новое поведение; старый фильтр не ловил первую строку без `;`)
# · Remove if: фильтр перестанет поддерживать многострочные ALTER ROLE
@ldd_trajectory
def test_filter_multiline_alter_role_cut(caplog) -> None:
    """Multi-line ALTER ROLE U (первая строка без `;`, финал на `;`) — вырезан целиком."""
    dump = (
        "ALTER ROLE platform WITH\n"
        "    NOSUPERUSER\n"
        "    LOGIN PASSWORD 'SCRAM-SHA-256$4096:AA:BB=CC';\n"
        "CREATE DATABASE after;\n"
        "COPY public.t (id) FROM stdin;\n"
        "1\n"
        "\\.\n"
    )
    out = _run_filter(dump)
    assert "ALTER ROLE platform WITH" not in out
    assert "NOSUPERUSER" not in out
    assert "PASSWORD" not in out
    assert "CREATE DATABASE after;" in out
    assert "COPY public.t (id) FROM stdin;" in out
    logger.info("[IMP:9][test_restore_psql] multi-line ALTER ROLE cut entirely, stream alive ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: роль, НЕ совпадающая с U — сохраняется
# ·   (myplatform при U=platform; index-подход старого фильтра давал бы false-positive)
# · Last fail: N/A (новое поведение; прежний index($0, U ";") резал myplatform)
# · Remove if: фильтр перестанет различать имена ролей по позиции
@ldd_trajectory
def test_filter_nonmatching_role_preserved(caplog) -> None:
    """Роль с U как подстрокой имени (myplatform) при U=platform — НЕ вырезается."""
    dump = "CREATE ROLE myplatform;\nALTER ROLE myplatform WITH LOGIN;\nCREATE DATABASE kept;\n"
    out = _run_filter(dump)
    assert "CREATE ROLE myplatform;" in out
    assert "ALTER ROLE myplatform WITH LOGIN;" in out
    assert "CREATE DATABASE kept;" in out
    logger.info("[IMP:9][test_restore_psql] non-matching role preserved ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: `;` внутри пароля (SCRAM/произвольный) —
# · ALTER ROLE platform вырезан целиком, следующий контент жив
# · Last fail: N/A (новое поведение; старый фильтр при index-матче по `platform;` в первом
# ·   поле мог резать неверно/не дорезать)
# · Remove if: фильтр перестанет корректно обрабатывать `;` в строковых значениях
@ldd_trajectory
def test_filter_scram_semicolon_in_password(caplog) -> None:
    """Пароль с `;` внутри (однострочный и многострочный) — statement вырезан, поток жив."""
    single = "ALTER ROLE platform WITH LOGIN PASSWORD 'abc;def';\nCREATE DATABASE after1;\nCOPY x FROM stdin;\n9\n\\.\n"
    out_single = _run_filter(single)
    assert "ALTER ROLE platform WITH" not in out_single
    assert "CREATE DATABASE after1;" in out_single
    assert "COPY x FROM stdin;" in out_single

    multi = "ALTER ROLE platform WITH\n    LOGIN PASSWORD 'abc;def';\nCREATE DATABASE after2;\n"
    out_multi = _run_filter(multi)
    assert "ALTER ROLE platform WITH" not in out_multi
    assert "abc;def" not in out_multi
    assert "CREATE DATABASE after2;" in out_multi
    logger.info("[IMP:9][test_restore_psql] SCRAM/`;`-in-password handled, stream alive ✓")


# endregion filter


# region db_check: post-restore verification (DATA-504)


# 🧪 TRAP[TEST] · Regression · Scenario: post-check — дамп ожидает langfuse/litellm/platform,
# · кластер их не имеет → exit 3 со списком недостающих (DATA-504)
# · Last fail: 2026-09-01 prod — restore rc=0 над пустым кластером (базы удалены, данные потеряны)
# · Remove if: пост-проверка БД удалена/заменена иным механизмом гарантии применимости
@ldd_trajectory
def test_db_check_missing_databases_exit3(caplog, tmp_path) -> None:
    """main(): ожидаемые БД отсутствуют в кластере → rc 3, список в IMP:10."""
    expected_file = tmp_path / "expected.txt"
    expected_file.write_text("langfuse\nlitellm\nplatform\n")
    fake = _FakeRunner(stdout="postgres\ntemplate0\ntemplate1\n")  # кластер без user-БД

    rc = main(["restore_db_check.py", "--expected-file", str(expected_file)], runner=fake)

    assert rc == 3, f"ожидался DATA-504 exit 3, got {rc}"
    logs = [r.message for r in caplog.records if "restore-db-check" in r.name]
    assert any("MISSING DATABASES" in m and "langfuse, litellm, platform" in m for m in logs), f"logs: {logs}"
    assert fake.calls, "docker exec psql query должен быть вызван"
    logger.info("[IMP:9][test_restore_psql] db-check: missing DBs → exit 3 with list ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: post-check — все ожидаемые БД на месте → rc 0
# · Last fail: N/A (новое поведение)
# · Remove if: пост-проверка БД удалена/заменена
@ldd_trajectory
def test_db_check_all_present_rc0(caplog, tmp_path) -> None:
    """main(): все ожидаемые БД присутствуют → rc 0."""
    expected_file = tmp_path / "expected.txt"
    expected_file.write_text("langfuse\nlitellm\nplatform\n")
    fake = _FakeRunner(stdout="langfuse\nlitellm\nplatform\npostgres\ntemplate0\ntemplate1\n")

    rc = main(["restore_db_check.py", "--expected-file", str(expected_file)], runner=fake)

    assert rc == 0, f"ожидался rc 0, got {rc}"
    logs = [r.message for r in caplog.records if "restore-db-check" in r.name]
    assert any("post-restore DB check OK" in m for m in logs), f"logs: {logs}"
    logger.info("[IMP:9][test_restore_psql] db-check: all DBs present → rc 0 ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: fail-closed — пустой expected-файл (сбой сбора имён)
# · → exit 3, НЕ vacuous-pass
# · Last fail: N/A (новое поведение; без fail-closed пустой список = тихий успех = дыра)
# · Remove if: сбой сбора ожидаемых имён перестанет быть возможным (архитектурно)
@ldd_trajectory
def test_db_check_empty_expected_fail_closed(caplog, tmp_path) -> None:
    """main(): пустой expected-файл → rc 3 (сбой сбора ≠ успех проверки)."""
    expected_file = tmp_path / "expected.txt"
    expected_file.write_text("\n")  # пустой/пробельный
    fake = _FakeRunner(stdout="postgres\n")

    rc = main(["restore_db_check.py", "--expected-file", str(expected_file)], runner=fake)

    assert rc == 3, "fail-closed: пустой список ожидаемых обязан быть ошибкой"
    assert fake.calls == [], "при пустом expected docker query не нужен"
    logger.info("[IMP:9][test_restore_psql] db-check: empty expected → fail-closed exit 3 ✓")


# 🧪 TRAP[TEST] · Regression · Scenario: compare_dbs игнорирует системные БД (template0/1, postgres)
# ·   — их наличие ничего не доказывает о восстановлении user-данных
# · Last fail: N/A (чистая unit-проверка сравнения)
# · Remove if: SYSTEM_DBS политика меняется
@ldd_trajectory
def test_db_check_compare_ignores_system_dbs(caplog) -> None:
    """compare_dbs: postgres/template* не участвуют; missing = только user-БД."""
    missing = compare_dbs(
        expected=["postgres", "langfuse", "litellm"],
        actual=["postgres", "template0", "template1"],
    )
    assert missing == ["langfuse", "litellm"], f"missing: {missing}"
    assert frozenset({"template0", "template1", "postgres"}) == SYSTEM_DBS
    logger.info("[IMP:9][test_restore_psql] compare_dbs: system DBs excluded ✓")


# endregion db_check
