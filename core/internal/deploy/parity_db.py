#!/usr/bin/env python3
# GREP_SUMMARY: parity-db, parity database, create, drop, privileged-path, docker-exec-psql, ssh, DSN, CommandRunner, idempotent, already-exists, CREATEDB-isolation, token-urlsafe, pgbouncer
# STRUCTURE: ▶ main(argv) → ◇ fail-fast (action/project/node) → ◇ resolve host (DI seam) → ◇ create: pg_roles/pg_database SELECT → CREATE|ALTER ROLE → CREATE DATABASE (skip exists) → GRANT+REVOKE (в parity-БД) → print DSN → ◇ drop: DROP DATABASE IF EXISTS + DROP ROLE IF EXISTS → ⎋ int exit
# region MODULE_CONTRACT
## @purpose  Temporary parity-database CLI for PG-parity прогонов ai-project (DevPlan 019 TASK-6, AC5):
##           create/drop БД "parity_\<project\>" + роли "parity_\<project\>_user" через ПРИВИЛЕГИРОВАННЫЙ
##           путь (ssh root@\<node\> → docker exec postgres psql -U postgres на НОДЕ) — проектные роли
##           НЕ получают CREATEDB (изоляция канона, TRAP[BUSINESS] 019). create: stdout = РОВНО одна
##           DSN-строка (машиночитаемый контракт parity-инструментария ai-project); idempotent
##           (повторный create → ALTER ROLE rotation пароля + DSN re-print). drop: idempotent IF EXISTS.
## @scope    CLI: `python3 -m core.internal.deploy.parity_db --action create|drop --project \<name\> --node \<node\>`
##           (или `make parity-db ACTION=... PROJECT=... NODE=...` → core/entrypoints/parity-db.sh).
##           Каждая psql-команда = отдельный ssh-вызов (timeout=60, REF-0002 W1 канон).
## @invariants
##   - Проектные роли НИКОГДА не получают CREATEDB (TRAP[BUSINESS] 019) — parity-БД создаётся
##     привилегированным путём: docker exec postgres psql -U postgres на ноде
##   - SQL-идентификаторы ВСЕГДА в double quotes; password — одинарные кавычки SQL-литерала
##     (token_urlsafe charset [A-Za-z0-9_-] НЕ содержит ' \ — инвариант экранирования SQL/DSN)
##   - Пароль НЕ в логах: IMP-логи содержат только desc-блоки операций; DSN печатается
##     ТОЛЬКО в stdout (ровно одна строка, exit 0)
##   - Idempotent: role_exists → ALTER ROLE (ensure-convergence rotation); db_exists → skip CREATE;
##     already-exists паттерн проверяется в выводе ДО returncode (TRAP[BUG] on_project_deploy)
##   - drop: DROP DATABASE IF EXISTS ... WITH (FORCE) + DROP ROLE IF EXISTS; отсутствие — exit 0
##   - Business functions never call sys.exit — main() -> int; sys.exit only in __main__
##   - DI (160/E1): runner: CommandRunner | None (execute-функции + main); resolve_host: Callable — только
##     в main (None → реальная резолв-цепочка node_resolver)
## @rationale Q: почему parity-БД отдельным verb, а не расширением postgres-хука?
##            A: parity-прогон — не project-deploy (хук триггерится деплоем и ждёт needs.database
##            в манифесте проекта); parity-БД по определению временная и вне lifecycle проектов;
##            отдельный путь честнее флага-костыля в хуке (DevPlan 019 Design Decisions).
##            Q: почему каждая команда — отдельный ssh-вызов?
##            A: идемпотентный диалог требует ветвления по ответу psql (SELECT pg_roles → CREATE|ALTER)
##            — невозможно в одном статичном bash-скрипте без разбора rc/вывода; прецедент
##            on_project_deploy (_psql per-statement). timeout=60 — REF-0002 W1 единый psql-канон.
##            Q: почему shlex.quote, а не ssh_cmd_builder.printf_q (bash %q)?
##            A: shlex.quote (POSIX single-quote) безопасен для printable-ASCII SQL-домена (без
##            control-символов/новых строк) и сохраняет читаемость SQL-подстрок в argv — тесты
##            ассертят 'CREATE ROLE "x" LOGIN PASSWORD' буквально; printf_q backslash-экранирует
##            пробелы (аргументы не читаемы). %q остаётся каноном build_*_ssh_cmd (пути/переменные).
## @changes 2026-08-31 | DevPlan 019 TASK-6 — Created (AC5)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Callable
from typing import ClassVar

from core.internal.shared.contracts import EXIT_CONFIG_NOT_FOUND, EXIT_GENERIC, EXIT_OK
from core.internal.shared.deploy_paths import DEFAULT_PLATFORM_BASE as DEFAULT_REMOTE_PLATFORM
from core.internal.shared.exceptions import PlatformError
from core.internal.shared.node_resolver import extract_node_host, resolve_node_yaml
from core.internal.shared.project_registry import validate_project_name
from core.internal.shared.ssh_opts import SSH_OPTS
from core.internal.shared.subprocess_io import CommandRunner

logger = logging.getLogger(__name__)

# REF-0002 W1 (on_project_deploy): единый psql-timeout канон во ВСЕХ ветках вызова
_PSQL_TIMEOUT: int = 60

__all__ = ["main"]


# ═══════════════════════════════════════════════════════════════════
# region FUNC__default_resolve_host
## @purpose  Реальная резолв-цепочка node → SSH host (DI-default для resolve_host параметра main):
##           resolve_node_yaml(platform_root=PLATFORM_ROOT|/opt/platform) → extract_node_host.
##           Паттерн RemoteExecutor._resolve_host (remote_executor.py:193-208).
## @param node_name  Node name (bare, из node.yaml)
## @return  str — host ("" если node.host отсутствует)
## @raises  PlatformError (ConfigNotFoundError/ConfigParseError) — node.yaml не найден/битый
## @complexity O(P + N) — 3-path search + YAML parse
## @invariants
##   - platform_root: env PLATFORM_ROOT (make экспортирует) → DEFAULT_REMOTE_PLATFORM (/opt/platform)
##   - resolve_node_yaml/extract_node_host — shared-фасад (единая точка чтения node.yaml)
def _default_resolve_host(node_name: str) -> str:
    """Resolve node.yaml → SSH host (реальная цепочка; DI-default, RemoteExecutor паттерн)."""
    platform_root = os.environ.get("PLATFORM_ROOT") or str(DEFAULT_REMOTE_PLATFORM)
    logger.info(
        "[IMP:8][parity_db][resolve] Resolving node.yaml for node=%s (platform_root=%s)", node_name, platform_root
    )
    yaml_path = resolve_node_yaml(node_name, platform_root=platform_root)
    host = extract_node_host(yaml_path)
    logger.info("[IMP:9][parity_db][resolve] Node %s → host=%s", node_name, host)
    return host


# endregion FUNC__default_resolve_host


# ═══════════════════════════════════════════════════════════════════
# region FUNC__build_remote_psql
## @purpose  Сборка remote-команды `docker exec postgres psql -U postgres [-d db] -Atc|-c \<sql\>`
##           для ssh root@host. Каждый аргумент shlex.quote'ится (POSIX single-quote) — psql получает
##           SQL одним аргументом; double-quoted идентификаторы и single-quoted пароль сохраняются.
## @param sql          SQL-выражение (identifiers double-quoted; password literal single-quoted)
## @param database     kwarg-only: `-d \<database\>` — таргетинг DDL ВНУТРИ parity-БД (GRANT/REVOKE)
## @param tuples_only  True → `-Atc` (unaligned+tuples-only — SELECT-детект); False → `-c` (DDL-теги)
## @return  str — remote-команда для remote-оболочки (без секретов НЕ гарантировано — SQL может
##          содержать пароль; логированию НЕ подлежит — см. _psql desc-контракт)
## @complexity O(L) — длина SQL
## @invariants
##   - shlex.quote — POSIX-safe для printable-ASCII домена (token_urlsafe пароль без ' \)
##   - -Atc для SELECT-детекта; -c для GRANT/REVOKE (spec 019 TASK-6 шаг 5)
def _build_remote_psql(sql: str, *, database: str | None = None, tuples_only: bool = True) -> str:
    """Build remote shell command: docker exec postgres psql -U postgres [-d db] -Atc|-c <sql>."""
    parts = ["docker", "exec", "postgres", "psql", "-U", "postgres"]
    if database:
        parts += ["-d", database]
    parts.append("-Atc" if tuples_only else "-c")
    parts.append(sql)
    return " ".join(shlex.quote(part) for part in parts)


# endregion FUNC__build_remote_psql


# ═══════════════════════════════════════════════════════════════════
# region FUNC__run_remote
## @purpose  Один ssh-вызов: `ssh *SSH_OPTS root@host <remote_cmd>` (timeout=60, graceful check=False).
##           DI-шов: runner=None → subprocess.run (prod), runner задан → runner.run (fake в тестах).
## @param runner    CommandRunner DI (None = subprocess.run default) — for testability
## @param host      SSH host (из node.yaml)
## @param remote_cmd  Remote-команда (см. _build_remote_psql) — НЕ логируется (может содержать пароль)
## @return  subprocess.CompletedProcess — rc/stdout/stderr
## @complexity O(1) — один subprocess
## @invariants
##   - remote_cmd НЕ логируется (пароль-гигиена) — лог только host + timeout
##   - timeout=60 — REF-0002 W1 psql-канон
##   - runner.run(cmd, timeout=..., check=False) — полный argv виден fake (тесты)
def _run_remote(runner: CommandRunner | None, host: str, remote_cmd: str) -> subprocess.CompletedProcess[str]:
    """Run one ssh root@host with the remote command (timeout=60, graceful)."""
    cmd = ["ssh", *SSH_OPTS, f"root@{host}", remote_cmd]
    logger.info("[IMP:8][parity_db][ssh] ssh root@%s (timeout=%ds)", host, _PSQL_TIMEOUT)
    if runner is None:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=_PSQL_TIMEOUT, check=False)
    return runner.run(cmd, timeout=_PSQL_TIMEOUT, check=False)


# endregion FUNC__run_remote


# ═══════════════════════════════════════════════════════════════════
# region FUNC__psql
## @purpose  Привилегированный psql-канал на НОДЕ (docker exec postgres -U postgres по ssh).
##           Возвращает (rc, merged stdout+stderr) — graceful; already-exists разбор в вызывающем.
## @param runner    CommandRunner DI (None = subprocess.run default)
## @param host      SSH host
## @param sql       SQL — НИКОГДА не логируется (может содержать пароль)
## @param database  kwarg-only: -d \<database\> (DDL ВНУТРИ parity-БД)
## @param tuples_only  True → -Atc; False → -c
## @param desc      Лог-блок операции (без SQL): [IMP:8][parity_db][psql] \<desc\> rc=N @ root@host
## @return  (int, str) — (returncode, stdout+stderr merged)
## @complexity O(1) — один ssh-вызов
## @invariants
##   - SQL не логируется; лог: desc + rc + host (пароль-гигиена, контракт 019)
##   - Graceful: rc≠0 возвращается как есть; subprocess-исключения → (127, текст) никогда не raise
def _psql(
    runner: CommandRunner | None,
    host: str,
    sql: str,
    *,
    database: str | None = None,
    tuples_only: bool = True,
    desc: str = "psql",
) -> tuple[int, str]:
    """Run one psql command on the node via ssh; return (rc, merged stdout+stderr)."""
    remote_cmd = _build_remote_psql(sql, database=database, tuples_only=tuples_only)
    try:
        result = _run_remote(runner, host, remote_cmd)
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.error("[IMP:9][parity_db][psql] %s — ssh/psql exec failed: %s", desc, exc)
        return 127, str(exc)
    output = (result.stdout or "") + (result.stderr or "")
    logger.info("[IMP:8][parity_db][psql] %s rc=%d @ root@%s", desc, result.returncode, host)
    return result.returncode, output


# endregion FUNC__psql


# ═══════════════════════════════════════════════════════════════════
# region FUNC__psql_failed
## @purpose  Единая проверка результата psql-операции: already-exists паттерн в выводе ДО returncode
##           (TRAP[BUG] on_project_deploy 2026-08-02 — порядок проверок сохраняет идемпотентность);
##           rc≠0 без already-exists → IMP:10 + True (вызывающий → EXIT_GENERIC).
## @param rc    returncode psql
## @param out   merged stdout+stderr
## @param what  Что за операция (для логов: "role"/"database"/"grant"/...)
## @return  bool — True = операция провалилась (fail-fast EXIT_GENERIC)
## @complexity O(1)
## @invariants
##   - already exists (case-insensitive) → False (идемпотентный skip), даже при rc≠0
##   - rc≠0 без паттерна → True + IMP:10 (stderr-деталь, до 300 символов)
def _psql_failed(rc: int, out: str, what: str) -> bool:
    """True if psql failed (rc≠0 без already-exists); already-exists → False (идемпотентно)."""
    if re.search(r"already exists", out, re.IGNORECASE):
        logger.info("[IMP:8][parity_db][idempotent] %s already exists — treating as ok", what)
        return False
    if rc != 0:
        logger.error("[IMP:10][parity_db][psql] %s operation failed rc=%d: %s", what, rc, out.strip()[:300])
        return True
    return False


# endregion FUNC__psql_failed


# ═══════════════════════════════════════════════════════════════════
# region FUNC__action_create
## @purpose  Create временной parity-БД + роли через привилегированный путь (идемпотентный диалог):
##           1) SELECT pg_roles → role_exists; 2) SELECT pg_database → db_exists;
##           3) role_exists → ALTER ROLE PASSWORD (ensure-convergence rotation) | CREATE ROLE;
##           4) db_exists → skip | CREATE DATABASE OWNER role;
##           5) GRANT ALL ON SCHEMA public (ВНУТРИ parity-БД, -d) + REVOKE CONNECT FROM PUBLIC (hygiene);
##           6) stdout: РОВНО одна DSN-строка postgresql://parity_\<p\>_user:\<pw\>\@pgbouncer:6432/parity_\<p\>.
## @param runner  CommandRunner DI (None = subprocess.run default)
## @param host    SSH host
## @param project  Project name (уже lowercase-нормализован и валидирован в main)
## @return  int — EXIT_OK | EXIT_GENERIC
## @complexity O(1) — фиксированный диалог ≤6 psql-вызовов
## @invariants
##   - SQL-идентификаторы double-quoted ВСЕГДА; password single-quoted (token_urlsafe без ' \)
##   - Пароль НЕ логируется; DSN — только в stdout (print, контракт)
##   - Повторный create: ALTER ROLE (новый token_urlsafe) + DSN re-print (пароль валиден)
##   - Права роли: ALL на parity-БД only; CONNECT/USAGE вне неё — NOTHING (REVOKE PUBLIC)
def _action_create(runner: CommandRunner | None, host: str, project: str) -> int:
    """Create parity DB + role (privileged path), print DSN to stdout. Returns exit code."""
    db = f"parity_{project}"
    role = f"parity_{project}_user"
    password = secrets.token_urlsafe(24)

    # ── 1-2. role_exists / db_exists (SELECT, -Atc) ──
    rc, out = _psql(runner, host, f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'", desc="role-exists")
    if rc != 0:
        logger.error("[IMP:10][parity_db][create] role-exists check failed rc=%d: %s", rc, out.strip()[:300])
        return EXIT_GENERIC
    role_exists = bool(out.strip())
    rc, out = _psql(runner, host, f"SELECT 1 FROM pg_database WHERE datname = '{db}'", desc="db-exists")
    if rc != 0:
        logger.error("[IMP:10][parity_db][create] db-exists check failed rc=%d: %s", rc, out.strip()[:300])
        return EXIT_GENERIC
    db_exists = bool(out.strip())

    # ── 3. Роль: CREATE | ALTER (ensure-convergence rotation, 019 TASK-6) ──
    if role_exists:
        rc, out = _psql(runner, host, f"ALTER ROLE \"{role}\" WITH LOGIN PASSWORD '{password}'", desc="alter-role")
        logger.info("[IMP:8][parity_db][create] Role %s exists — ALTER ROLE PASSWORD (rotation)", role)
    else:
        rc, out = _psql(runner, host, f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'", desc="create-role")
        logger.info("[IMP:9][parity_db][create] Role %s created", role)
    if _psql_failed(rc, out, "role"):
        return EXIT_GENERIC

    # ── 4. БД: skip | CREATE DATABASE OWNER (параллельно существование проверено выше) ──
    if db_exists:
        logger.info("[IMP:8][parity_db][create] Database %s already exists — skipping", db)
    else:
        rc, out = _psql(runner, host, f'CREATE DATABASE "{db}" OWNER "{role}"', desc="create-db")
        if _psql_failed(rc, out, "database"):
            return EXIT_GENERIC
        logger.info("[IMP:9][parity_db][create] Database %s created (owner=%s)", db, role)

    # ── 5. GRANT/REVOKE — ВНУТРИ parity-БД only (-d <db>); REVOKE PUBLIC — гигиена (SEC-0008 rider) ──
    rc, out = _psql(runner, host, f'GRANT ALL ON SCHEMA public TO "{role}"', database=db, desc="grant-public")
    if _psql_failed(rc, out, "grant"):
        return EXIT_GENERIC
    rc, out = _psql(runner, host, f'REVOKE CONNECT ON DATABASE "{db}" FROM PUBLIC', database=db, desc="revoke-public")
    if _psql_failed(rc, out, "revoke"):
        return EXIT_GENERIC
    logger.info(
        "[IMP:9][parity_db][create] Grants ensured: ALL ON SCHEMA public → %s (inside %s), CONNECT revoked from PUBLIC",
        role,
        db,
    )

    # ── 6. DSN — РОВНО одна строка в stdout (машиночитаемый контракт parity-инструментария) ──
    print(f"postgresql://{role}:{password}@pgbouncer:6432/{db}")
    logger.info("[IMP:9][parity_db][create] Parity DB ready: %s (DSN printed to stdout)", db)
    return EXIT_OK


# endregion FUNC__action_create


# ═══════════════════════════════════════════════════════════════════
# region FUNC__action_drop
## @purpose  Drop временной parity-БД + роли (идемпотентно): DROP DATABASE IF EXISTS ... WITH (FORCE)
##           затем DROP ROLE IF EXISTS (роль-owner обязана лишиться БД первой). Отсутствие — НЕ ошибка
##           (exit 0, IF EXISTS). stdout остаётся ПУСТЫМ (drop не печатает DSN).
## @param runner  CommandRunner DI (None = subprocess.run default)
## @param host    SSH host
## @param project  Project name (уже нормализован/валидирован в main)
## @return  int — EXIT_OK | EXIT_GENERIC
## @complexity O(1) — 2 psql-вызова
## @invariants
##   - WITH (FORCE) — закрывает активные соединения (PostgreSQL ≥13; платформа 18.4)
##   - rc≠0 → IMP:10 + EXIT_GENERIC; отсутствие (rc=0 при IF EXISTS) — не ошибка
##   - stdout пуст — drop не нарушает DSN-контракт create
def _action_drop(runner: CommandRunner | None, host: str, project: str) -> int:
    """Drop parity DB + role (IF EXISTS; absence is not an error). stdout stays empty."""
    db = f"parity_{project}"
    role = f"parity_{project}_user"
    rc, out = _psql(runner, host, f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)', desc="drop-db")
    if rc != 0:
        logger.error("[IMP:10][parity_db][drop] DROP DATABASE %s failed rc=%d: %s", db, rc, out.strip()[:300])
        return EXIT_GENERIC
    logger.info("[IMP:9][parity_db][drop] Database %s dropped (if existed)", db)
    rc, out = _psql(runner, host, f'DROP ROLE IF EXISTS "{role}"', desc="drop-role")
    if rc != 0:
        logger.error("[IMP:10][parity_db][drop] DROP ROLE %s failed rc=%d: %s", role, rc, out.strip()[:300])
        return EXIT_GENERIC
    logger.info("[IMP:9][parity_db][drop] Role %s dropped (if existed)", role)
    return EXIT_OK


# endregion FUNC__action_drop


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
class _ParityDbArgs(argparse.Namespace):
    """Typed argparse namespace (W11-G4: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты
    (прецедент _GenEnvArgs в gen_env_platform.py).
    """

    action: ClassVar[str]
    project: ClassVar[str]
    node: ClassVar[str]


## @purpose  CLI entry: `--action create|drop --project <name> --node <node>`. Fail-fast валидации:
##           action ∈ {create, drop} → EXIT_GENERIC; project по validate_project_name (lowercase-
##           нормализация) → EXIT_GENERIC; node обязателен → EXIT_CONFIG_NOT_FOUND. Резолв host →
##           dispatch create|drop. Логи в stderr; stdout — ТОЛЬКО DSN (create).
## @param argv        CLI args (None = sys.argv[1:])
## @param runner      CommandRunner DI (None = subprocess.run default) — тесты передают fake
## @param resolve_host  Callable(node) → host DI (None = реальная цепочка) — тесты передают lambda
## @return  int — EXIT_OK/EXIT_GENERIC/EXIT_CONFIG_NOT_FOUND/exit_code PlatformError
## @exitcode 0  Успех (create: DSN в stdout; drop: пусто)
## @exitcode 1  Generic: invalid action/project, пустой host, psql-ошибка
## @exitcode 2  ConfigNotFound: --node не задан / node.yaml не найден (EXIT_CONFIG_NOT_FOUND-семантика)
## @complexity O(1) — валидации + диспатч
## @invariants
##   - Business-функции не вызывают sys.exit — main() возвращает int (канон core/AGENTS.md)
##   - sys.exit — только в __main__
##   - IMP:9 START/DONE вокруг диспатча (LDD-телеметрия успешных путей)
##   - Пароль НЕ логируется; DSN — только print в stdout (ровно одна строка)
def main(
    argv: list[str] | None = None,
    *,
    runner: CommandRunner | None = None,
    resolve_host: Callable[[str], str] | None = None,
) -> int:
    """CLI: parity-db --action create|drop --project <name> --node <node> (returns exit code)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(
        description="Parity DB CLI — temporary parity database via privileged path (DevPlan 019 TASK-6)"
    )
    parser.add_argument("--action", default=None, help="create | drop")
    parser.add_argument("--project", default=None, help="Project name (kebab-case; lowercase-normalized)")
    parser.add_argument("--node", default=None, help="Node name (node.yaml → SSH host)")
    args = parser.parse_args(argv, namespace=_ParityDbArgs())

    # ── Fail-fast валидации (EXIT_GENERIC / EXIT_CONFIG_NOT_FOUND-семантика) ──
    if args.action not in {"create", "drop"}:
        logger.error("[IMP:10][parity_db][cli] Invalid --action=%r — expected create|drop", args.action)
        return EXIT_GENERIC
    if not args.project:
        logger.error(
            "[IMP:10][parity_db][cli] --project is required — usage: parity-db --action create|drop "
            "--project <name> --node <node>"
        )
        return EXIT_GENERIC
    project = args.project.lower()
    if not validate_project_name(project):
        logger.error(
            "[IMP:10][parity_db][cli] Invalid project name %r (канон ^[a-zA-Z0-9][a-zA-Z0-9_-]*$, "
            "verb-reserve) — no SSH executed",
            args.project,
        )
        return EXIT_GENERIC
    if not args.node:
        logger.error(
            "[IMP:10][parity_db][cli] --node is required — cannot resolve parity DB host "
            "(EXIT_CONFIG_NOT_FOUND semantics)"
        )
        return EXIT_CONFIG_NOT_FOUND

    # 🧐 TRAP[DECISION] · 2026-08-31 · — · resolve_host DI-шов вместо monkeypatch.setattr модуля
    # · Rejected: monkeypatch.setattr(parity_db, "_default_resolve_host", ...) на уровне модуля
    # · Reason: DI-канон 160/E1 (runner/facts/deliverer параметры) — тесты передают
    # ·   resolve_host=lambda node: "1.2.3.4"; prod-дефолт — реальная резолв-цепочка без изменений
    # · Rev: если резолв-цепочка станет классом (NodeResolver) — параметр станет его инстансом
    resolve = resolve_host if resolve_host is not None else _default_resolve_host
    try:
        host = resolve(args.node)
    except PlatformError as exc:
        logger.error("[IMP:10][parity_db][resolve] %s", exc)
        return exc.exit_code
    if not host:
        logger.error("[IMP:10][parity_db][resolve] node=%s has empty node.host — cannot ssh (EXIT_GENERIC)", args.node)
        return EXIT_GENERIC

    logger.info(
        "[IMP:9][parity_db][cli] === parity-db %s START: project=%s node=%s host=%s ===",
        args.action,
        project,
        args.node,
        host,
    )
    rc = _action_create(runner, host, project) if args.action == "create" else _action_drop(runner, host, project)
    logger.info("[IMP:9][parity_db][cli] === parity-db %s DONE: project=%s (rc=%d) ===", args.action, project, rc)
    return rc


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
