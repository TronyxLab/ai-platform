#!/usr/bin/env python3
# GREP_SUMMARY: postgres hook on-project-deploy auto-create-db database project needs.database role GRANT credentials platform-db.env idempotent ensure-convergence ALTER ROLE orphan DI runner env
# STRUCTURE: ▶ main(args) → ◇ auto_create_db(project_dir, project) → ◇ NodeYaml needs.database → ◇ validate db_name → ◇ docker exec psql CREATE DATABASE → ◇ ensure_project_db_access (role ∨ ALTER-ROLE-converge + GRANT + credentials + .env.platform regen) → ⎋ log done
# region MODULE_CONTRACT
## @purpose  Post-deploy hook for postgres module: auto-create project database if declared in
##           ai-platform.yaml needs.database, and provision shared-DB access (DevPlan 133 W2):
##           role ${project}_user + password + GRANTs + credentials file .platform-db.env +
##           .env.platform password-injection (regen). Python-порт shell хука (DevPlan 117 H D65).
## @scope    Invoked post-deploy (operator-инвокация / DeployOrchestrator deploy-hook chain);
##           receives PROJECT_DIR, PROJECT, NODE_NAME (NODE_NAME unused).
## @invariants
##   - Non-fatal: роль/GRANT/credentials ошибки log'ятся, НЕ блокируют деплой
##   - invalid db_name (не ^[a-zA-Z0-9_]+$) → FATAL (return 1), как раньше
##   - Idempotent: существующая БД → skip CREATE; существующая роль с валидными creds →
##     skip CREATE/ALTER (пароль НЕ меняется, иначе ломается уже выданный credentials);
##     существующая роль БЕЗ creds (orphan) → ensure-convergence: ALTER ROLE PASSWORD +
##     creds + GRANT + реген (REF-0002 В0; ранний return удалён — BUG-0605/DATA-201)
##   - Роль: ${project}_user; GRANT CONNECT ON DATABASE + GRANT CREATE, USAGE ON SCHEMA public (D6)
##   - .platform-db.env (0600): PLATFORM_POSTGRES_DB/USER/PASSWORD — атомарная запись
##   - .env.platform перегенерируется при создании ИЛИ конвергенции роли (password-injection)
##   - POSTGRES_PASSWORD должен быть доступен (env) для psql-gate
##   - Business functions never call sys.exit — return int status; sys.exit only in main()
##   - Cross-layer (модули→internal) — только allowlisted shared.node_yaml импорт (D1);
##     перегенерация .env.platform — subprocess CLI (НЕ импорт scaffold, см. TRAP[DECISION])
##   - E1 (160): runner: CommandRunner DI (None = реальный subprocess; поведение без изменений);
##     env прокидывается параметром из main (AppConfig-паттерн W4a)
## @rationale Language policy (Python-first): "False"-conversion, regex validation и psql-output
##            parsing — business logic → Python. DevPlan 133 W2: шаред-доступ к БД был сломан
##            в 2 точках (pgbouncer жёсткий список; роль не создавалась) — хук теперь создаёт
##            роль/гранты/credentials и прокидывает пароль в DSN через password-injection.
##            REF-0002 В0: хук зарегистрирован в post-deploy chain (hooks.on_project_deploy),
##            идемпотентность = ensure-convergence, а не early-return.
## @changes  Ported from on-project-deploy.sh (2026-08-02, DevPlan 117 H D65)
## @changes  2026-08-03 · DevPlan 133 W2 — +role/GRANT/credentials/.env.platform regen (D4/D6)
## @changes  2026-08-13 · DevPlan 160 E1 — +runner DI + env через параметры (main/env)
## @changes  2026-08-24 · REF-0002 (11-DevPlan В0) — регистрация hooks.on_project_deploy +
##            ensure-convergence orphan-role ветки (ALTER ROLE PASSWORD вместо early-return)
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-08-03 · — · .env.platform regen через subprocess CLI, не library-import
# · Rejected: `from core.internal.scaffold.gen_env_platform import generate_env_platform` —
# ·   modules→internal импорт = новое cross-layer нарушение; allowlist «не растёт» (B8 D3)
# · Reason: хук уже использует subprocess (docker exec psql); CLI gen_env_platform.py
# ·   --project-dir читает .platform-db.env автоматически (password-injection канал, D4).
# · Rev: если cross-layer allowlist получит механизм легальных модуль→scaffold импортов — переключить.

from __future__ import annotations

import contextlib
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

# ── sys.path bootstrap: project root (repo root) для импорта core.internal.* ──
# Хук выполняется как `python3 <script>` — Python добавляет в sys.path только
# каталог скрипта; project root добавляем вручную (родители: hooks→postgres→modules→core→root).
_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# LINT-EXEMPT: postgres-hook; shared.node_yaml — by design (D1, cross-layer allowlist 116 B11 T1)
# E1 (160): runner DI — тесты передают FakeCommandRunner вместо monkeypatch subprocess.run
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.subprocess_io import CommandRunner

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_ROLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")

__all__ = ["auto_create_db", "ensure_project_db_access", "main"]


# ═══════════════════════════════════════════════════════════════════
# region FUNC_auto_create_db
## @purpose  Create project database if needs.database is declared in ai-platform.yaml,
##           then provision shared-DB access (role+GRANT+credentials, DevPlan 133 W2).
## @param project_dir  Path to the deployed project directory (contains ai-platform.yaml)
## @param project      Project name (для роли ${project}_user)
## @param env          Environment dict override (defaults to os.environ) — for testability
## @param runner       CommandRunner DI (None = subprocess.run default) — for testability
## @return  int status: 0 = ok/skip, 1 = fatal (invalid db_name, psql failure)
## @rationale Mirrors _auto_create_db() from the shell hook 1:1, with one ordering fix:
##            psql returns non-zero when the database already exists, so the output is
##            checked for "already exists" BEFORE the returncode failure branch — preserves
##            the hook's idempotency intent (skip on existing DB, not CRITICAL).
_HOOK_ARGS_MIN: int = 2  # args: PROJECT_DIR PROJECT


def auto_create_db(
    project_dir: str,
    project: str,
    env: dict[str, str] | None = None,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Create project database (if needs.database declared) + provision role/GRANT/credentials."""
    ai_yaml = os.path.join(project_dir, "ai-platform.yaml")

    if not os.path.isfile(ai_yaml):
        logger.info("[IMP:8][db] No ai-platform.yaml found — skipping")
        return 0

    # NodeYaml direct import (Python→Python, D65). Note: database: false in YAML
    # returns bool False, not empty — handle missing key (default "") and explicit false.
    db_name = NodeYaml(ai_yaml).get("needs.database", default="")
    if (
        db_name is None
        or db_name is False
        or str(db_name).lower()
        in {
            "false",
        }
    ):
        db_name = ""
    db_name = str(db_name)

    if not db_name:
        logger.info("[IMP:7][db] No database declared in needs.database — skipping")
        return 0

    logger.info("[IMP:8][db] Creating database '%s' for project '%s'...", db_name, project)

    if not _DB_NAME_RE.match(db_name):
        logger.error("[IMP:10][db] FATAL: invalid db_name: %s", db_name)
        return 1

    effective_env = os.environ if env is None else env
    pg_password = effective_env.get("PGPASSWORD") or effective_env.get("POSTGRES_PASSWORD") or ""
    if not pg_password:
        logger.info("[IMP:6][db] POSTGRES_PASSWORD not available — skipping DB creation")
        return 0

    if runner is None:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-c",
                f"CREATE DATABASE {db_name} OWNER postgres;",
            ],
            capture_output=True,
            text=True,
            timeout=60,  # REF-0002 W1: единый psql-timeout канон во ВСЕХ ветках вызова
            check=False,
        )
    else:
        result = runner.run(
            [
                "docker",
                "exec",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-c",
                f"CREATE DATABASE {db_name} OWNER postgres;",
            ],
            timeout=60,
            check=False,
        )
    db_output = (result.stdout or "") + (result.stderr or "")

    # ⚠️ TRAP[BUG] · 2026-08-02 · P2 · already-exists недостижим в shell-оригинале
    # · Symptom: psql возвращает rc≠0 при существующей БД → shell `|| { CRITICAL; return 1; }`
    # ·   срабатывал раньше grep «already exists» → ветка skip была мёртвой
    # · Root: порядок проверок в on-project-deploy.sh (rc-проверка до парсинга вывода)
    # · Fix: в Python-порте вывод проверяется на «already exists» ДО проверки returncode —
    # ·   сохраняет интент идемпотентности хука (DevPlan 09 §D65, сценарий «DB существует»)
    if re.search(r"already exists", db_output, re.IGNORECASE):
        logger.info("[IMP:8][db] Database '%s' already exists — skipping", db_name)
    elif result.returncode != 0:
        logger.error("[IMP:10][db] CRITICAL: psql exec failed for database '%s': %s", db_name, db_output.strip())
        return 1
    elif re.search(r"ERROR", db_output, re.IGNORECASE):
        logger.error("[IMP:9][db] Failed to create database '%s': %s", db_name, db_output.strip())
        return 1
    else:
        logger.info("[IMP:9][db] Database '%s' created for project '%s'", db_name, project)

    # ── DevPlan 133 W2: роль + GRANT + credentials (non-fatal) ──
    ensure_project_db_access(project_dir, project, db_name, env, runner=runner)
    return 0


# endregion FUNC_auto_create_db


# ═══════════════════════════════════════════════════════════════════
# region FUNC_ensure_project_db_access
## @purpose  Idempotent ensure-convergence role+GRANT+credentials provisioning
##           (DevPlan 133 W2 D4/D6 + REF-0002 В0):
##           1) роль ${project}_user (CREATE при отсутствии; ALTER ROLE PASSWORD при
##              orphan-role: роль есть, credentials потеряны — сходимость вместо early-return);
##           2) GRANT CONNECT ON DATABASE + GRANT CREATE, USAGE ON SCHEMA public;
##           3) .platform-db.env (0600, атомарно): PLATFORM_POSTGRES_DB/USER/PASSWORD;
##           4) регенерация .env.platform при СОЗДАНИИ или КОНВЕРГЕНЦИИ роли (password-injection).
##           Non-fatal: любая ошибка → log, return 0 (деплой не блокируется).
## @param project_dir  Project directory (target: .platform-db.env, .env.platform)
## @param project      Project name (роль ${project}_user)
## @param db_name      Database name (GRANT CONNECT ON DATABASE)
## @param env          Environment dict override (defaults to os.environ)
## @param runner       CommandRunner DI (None = subprocess.run default) — for testability
## @return  int status: всегда 0 (non-fatal семантика сохранена)
## @complexity O(1) — 3-5 psql вызова + файловая запись
## @invariants
##   - Ensure-convergence: role_exists+no-creds → ALTER ROLE PASSWORD → продолжение вниз
##     (GRANT/creds/реген) — НЕ ранний return (BUG-0605/DATA-201 закрыт, REF-0002 В0)
##   - Роль существует И credentials валидны → CREATE/ALTER не вызываются, пароль не меняется
##   - GRANT-ы идемпотентны (повторный GRANT — no-op); REF-0002 W1: результат КАЖДОЙ
##     GRANT/REVOKE операции проверяется, сбои агрегируются в critical_failures
##     (IMP:10-лог; non-fatal для деплоя — счётчик в логе, не raise)
##   - REVOKE CONNECT ... FROM PUBLIC (SEC-0008 residual): выполняется идемпотентно после
##     GRANT'ов; отказ → critical_failures, не фатал
##   - Password: secrets.token_urlsafe(24) — URL-safe charset (без экранирования в DSN/SQL)
##   - .platform-db.env пишется атомарно (tempfile+fsync+os.replace), mode 0600
## @changes 2026-08-24 | REF-0002 (11-DevPlan В0) — orphan-role early-return → ensure-convergence
## @changes 2026-08-24 | REF-0002 W1 — GRANT/REVOKE result-checks + critical_failures,
##            timeout=60 во всех psql-ветках, REVOKE PUBLIC rider (SEC-0008)
def ensure_project_db_access(
    project_dir: str,
    project: str,
    db_name: str,
    env: dict[str, str] | None = None,
    *,
    runner: CommandRunner | None = None,
) -> int:
    """Provision role + GRANT + credentials for a project DB (idempotent, non-fatal)."""
    role = f"{project}_user"
    if not _ROLE_NAME_RE.match(role):
        logger.error("[IMP:10][db] FATAL: invalid role name %r — skipping role provisioning", role)
        return 0

    effective_env = os.environ if env is None else env
    pg_password = effective_env.get("PGPASSWORD") or effective_env.get("POSTGRES_PASSWORD") or ""
    if not pg_password:
        logger.info("[IMP:6][db] POSTGRES_PASSWORD not available — skipping role provisioning")
        return 0

    # ── 1. Роль: существует? ──
    # nosec B608: роль валидирована _ROLE_NAME_RE (^[a-zA-Z0-9_]+$) выше — не пользовательский ввод
    exists_out = _psql("-tA", "-c", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'", runner=runner)  # nosec B608
    role_exists = bool(exists_out and exists_out.strip())

    created = False
    converged = False  # REF-0002: orphan-role repaired via ALTER ROLE PASSWORD
    password = ""
    if role_exists:
        # Пароль известен только из credentials-файла (если он есть)
        creds = _read_credentials(project_dir)
        password = creds.get("PLATFORM_POSTGRES_PASSWORD", "")
        if not password:
            # 🧐 TRAP[DECISION] · 2026-08-24 · — · Orphan-role repair = ротация пароля (ALTER ROLE PASSWORD),
            # ·   не восстановление старого · Rejected: прежний early-return «skip + ручной DROP ROLE»
            # ·   (BUG-0605/DATA-201: retry навсегда пропускал GRANT/реген) и попытка вытащить пароль
            # ·   из pg_authid · Reason: пароль необратим (scram/md5-хэш); ротация + запись кредов +
            # ·   GRANT + реген .env.platform (password-injection) — единственный идемпотентный путь
            # ·   привести систему в согласованное состояние · Rev: если появится side-channel
            # ·   с plaintext-паролями (secrets-manager для ролей БД) — заменить ротацию на re-inject
            logger.warning(
                "[IMP:8][db] Role '%s' exists but credentials missing (%s) — converging: ALTER ROLE PASSWORD",
                role,
                os.path.join(project_dir, ".platform-db.env"),
            )
            password = secrets.token_urlsafe(24)
            alter_out = _psql("-c", f"ALTER ROLE \"{role}\" WITH LOGIN PASSWORD '{password}'", runner=runner)
            if alter_out is None:
                # psql failure — non-fatal (деплой не блокируем), credentials не пишем
                logger.error("[IMP:9][db] CRITICAL: ALTER ROLE %s failed (non-fatal)", role)
                return 0
            if re.search(r"ERROR", alter_out, re.IGNORECASE):
                logger.error("[IMP:9][db] ALTER ROLE %s error: %s (non-fatal)", role, alter_out.strip())
                return 0
            converged = True
            logger.info("[IMP:9][db] Orphan-role '%s' converged: password rotated, provisioning continues", role)
        else:
            logger.info("[IMP:8][db] Role '%s' already exists — SKIP creation (password unchanged)", role)
    else:
        password = secrets.token_urlsafe(24)
        logger.info("[IMP:8][db] Creating role '%s' with generated password...", role)
        create_out = _psql("-c", f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'", runner=runner)
        if create_out is None:
            # psql failure — non-fatal (деплой не блокируем), credentials не пишем
            logger.error("[IMP:9][db] CRITICAL: CREATE ROLE %s failed (non-fatal)", role)
            return 0
        if re.search(r"ERROR", create_out, re.IGNORECASE):
            logger.error("[IMP:9][db] CREATE ROLE %s error: %s (non-fatal)", role, create_out.strip())
            return 0
        created = True
        logger.info("[IMP:9][db] Role '%s' created", role)

    # ── 2. GRANT-ы (идемпотентны) + REVOKE PUBLIC rider — REF-0002 W1: проверка результата
    #    КАЖДОЙ операции, сбои АГРЕГИРУЮТСЯ в critical_failures (IMP:10), не тихий continue.
    #    Non-fatal семантика сохранена: счётчик не блокирует деплой, но честно рапортуется. ──
    critical_failures = 0
    ddl_statements: list[tuple[str, str]] = [
        (f"GRANT CONNECT ON {db_name} → {role}", f'GRANT CONNECT ON DATABASE "{db_name}" TO "{role}"'),
        (f"GRANT CREATE,USAGE ON public → {role}", f'GRANT CREATE, USAGE ON SCHEMA public TO "{role}"'),
        # SEC-0008 residual: PUBLIC не должен иметь CONNECT на проектную БД.
        # Идемпотентно: повторный REVOKE — no-op; отказ → счётчик, не фатал деплоя.
        (f"REVOKE CONNECT ON {db_name} FROM PUBLIC", f'REVOKE CONNECT ON DATABASE "{db_name}" FROM PUBLIC'),
    ]
    for desc, sql in ddl_statements:
        out = _psql("-c", sql, runner=runner)
        if out is None:
            critical_failures += 1
            logger.error("[IMP:10][db] CRITICAL: %s — psql exec failed", desc)
        elif re.search(r"ERROR", out, re.IGNORECASE):
            critical_failures += 1
            logger.error("[IMP:10][db] CRITICAL: %s error: %s", desc, out.strip())
    logger.info(
        "[IMP:9][db] GRANTs ensured: CONNECT ON %s + CREATE,USAGE ON SCHEMA public → %s; REVOKE PUBLIC done (critical_failures=%d)",
        db_name,
        role,
        critical_failures,
    )

    # ── 3. Credentials-файл (0600, атомарно) ──
    if not _write_credentials(project_dir, db_name, role, password):
        logger.error("[IMP:9][db] .platform-db.env write failed for %s (non-fatal)", project)
        return 0
    logger.info("[IMP:9][db] .platform-db.env written: %s", os.path.join(project_dir, ".platform-db.env"))

    # ── 4. .env.platform перегенерация — при создании роли ИЛИ её конвергенции (password-injection):
    #    в обоих случаях DSN в .env.platform мог остаться со старым/отсутствующим паролем ──
    if created or converged:
        _regenerate_env_platform(project_dir, project, runner=runner)

    logger.info("[IMP:9][db] Shared-DB access ensured: db=%s role=%s project=%s", db_name, role, project)
    return 0


# endregion FUNC_ensure_project_db_access


# ═══════════════════════════════════════════════════════════════════
# region FUNC_psql_helper
## @purpose  Run psql inside the postgres container (docker exec). Returns stdout or None on error.
## @param psql_args  Args after `psql -U postgres` (e.g. ["-c", "SELECT 1"])
## @param runner     CommandRunner DI (None = subprocess.run default) — for testability
## @return  str (stdout+stderr merged) | None — None = exec failure (non-fatal caller)
## @complexity O(1) — single subprocess
## @changes 2026-08-13 | E1 (160): +runner DI — runner=None → subprocess.run (default),
##            runner задан → runner.run (fake scripted)
def _psql(*psql_args: str, runner: CommandRunner | None = None) -> str | None:
    """Execute psql inside the postgres container; return merged output or None on failure."""
    try:
        if runner is None:
            result = subprocess.run(
                ["docker", "exec", "postgres", "psql", "-U", "postgres", *psql_args],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        else:
            result = runner.run(
                ["docker", "exec", "postgres", "psql", "-U", "postgres", *psql_args],
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.error("[IMP:9][db] psql exec failed: %s", exc)
        return None
    return (result.stdout or "") + (result.stderr or "")


# endregion FUNC_psql_helper


# ═══════════════════════════════════════════════════════════════════
# region FUNC_credentials_io
## @purpose  .platform-db.env read/write helpers (inline — shared.secrets_env_parser НЕ
##           импортируем: modules→internal только allowlisted node_yaml, см. TRAP[DECISION]).
## @complexity O(N) — N = строки файла


def _read_credentials(project_dir: str) -> dict[str, str]:
    """Read .platform-db.env → dict ({} если файл отсутствует/битый)."""
    path = Path(project_dir) / ".platform-db.env"
    creds: dict[str, str] = {}
    if not path.is_file():
        return creds
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()
    except OSError as exc:
        logger.warning("[IMP:7][db] .platform-db.env read failed: %s", exc)
    return creds


# region FUNC__plw_body__write_credentials
## @purpose  Тело try-блока (PLW0717 extraction из _write_credentials) — семантика except не меняется.
## @io       ⇥ content, path, tmp → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__write_credentials(content: str, path: Path, tmp: Path) -> None:
    with Path(tmp).open("w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    Path(tmp).replace(path)


# endregion FUNC__plw_body__write_credentials


def _write_credentials(project_dir: str, db_name: str, role: str, password: str) -> bool:
    """Write .platform-db.env atomically (tempfile + fsync + os.replace), mode 0600."""
    path = Path(project_dir) / ".platform-db.env"
    content = (
        "# GENERATED by postgres hook — DO NOT EDIT (секрет, 0600, вне payload whitelist)\n"
        f"PLATFORM_POSTGRES_DB={db_name}\n"
        f"PLATFORM_POSTGRES_USER={role}\n"
        f"PLATFORM_POSTGRES_PASSWORD={password}\n"
    )
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        _plw_body__write_credentials(content, path, tmp)
    except OSError as exc:
        logger.error("[IMP:10][db] .platform-db.env write failed: %s", exc)
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    else:
        return True


# endregion FUNC_credentials_io


# ═══════════════════════════════════════════════════════════════════
# region FUNC_regenerate_env_platform
## @purpose  Regenerate .env.platform of the project on the node with real DB password
##           (password-injection, DevPlan 133 W2.3/D4). Subprocess CLI: gen_env_platform.py
##           --project-dir читает project_dir/.platform-db.env автоматически.
##           Best-effort: сбой → WARN, не блокирует (non-fatal).
## @param project_dir  Project directory on the node
## @param project      Project name (--name для ${NAME} подстановки)
## @return  bool — True если регенерация выполнена
## @complexity O(1) — single subprocess (CLI)
## @invariants
##   - platform root: env PLATFORM_REMOTE_BASE → parents[4] (== /opt/platform на ноде,
##     == repo root на dev-машине) — канон deploy_paths.platform_remote_base недоступен
##     из modules-слоя (TRAP[DECISION] выше)
##   - domain: ai-platform.yaml needs.domain → env PLATFORM_DOMAIN → ai-platform.local
##   - CLI: sys.executable + script path, timeout 60s, check=False
## @changes 2026-08-13 | E1 (160): +runner DI (subprocess CLI через runner)
## @param runner       CommandRunner DI (None = subprocess.run default) — for testability
def _regenerate_env_platform(project_dir: str, project: str, *, runner: CommandRunner | None = None) -> bool:
    """Regenerate .env.platform with password-injection (best-effort, non-fatal)."""
    platform_root = os.environ.get("PLATFORM_REMOTE_BASE", "") or str(Path(__file__).resolve().parents[4])
    gen_script = os.path.join(platform_root, "core", "internal", "scaffold", "gen_env_platform.py")
    platform_env = os.path.join(platform_root, "platform-env.yaml")

    if not (os.path.isfile(gen_script) and os.path.isfile(platform_env)):
        logger.warning(
            "[IMP:7][db] .env.platform regen skipped: gen_env_platform.py/platform-env.yaml not found under %s",
            platform_root,
        )
        return False

    domain = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")
    ai_yaml = os.path.join(project_dir, "ai-platform.yaml")
    if os.path.isfile(ai_yaml):
        try:
            d = NodeYaml(ai_yaml).get("needs.domain", default="")
            if d:
                domain = str(d)
        except Exception as exc:  # noqa: EXC — graceful, non-fatal (best-effort)  # ruff: ignore[BLE001]
            logger.info("[IMP:6][db] needs.domain read skipped: %s", exc)

    try:
        if runner is None:
            result = subprocess.run(
                [
                    sys.executable,
                    gen_script,
                    "--project-dir",
                    project_dir,
                    "--name",
                    project,
                    "--domain",
                    domain,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        else:
            result = runner.run(
                [
                    sys.executable,
                    gen_script,
                    "--project-dir",
                    project_dir,
                    "--name",
                    project,
                    "--domain",
                    domain,
                ],
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        logger.warning("[IMP:7][db] .env.platform regen failed (non-fatal): %s", exc)
        return False

    if result.returncode == 0:
        logger.info("[IMP:9][db] .env.platform regenerated with DB credentials for %s", project)
        return True
    logger.warning(
        "[IMP:7][db] .env.platform regen returned %d (non-fatal): %s",
        result.returncode,
        (result.stderr or result.stdout or "").strip()[-300:],
    )
    return False


# endregion FUNC_regenerate_env_platform


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Main hook entry: orchestrate post-deploy DB creation + shared-DB access.
## @io       stdin: positional args PROJECT_DIR PROJECT [NODE_NAME]
##           stderr: LDD logs
## @exitcode 0  Success or skip (missing args / no DB declared / no password / non-fatal role errors)
## @exitcode 1  Fatal: invalid db_name or psql failure
## @changes 2026-08-13 | E1 (160): +runner/env DI (тесты передают FakeCommandRunner + env mapping)
def main(
    argv: list[str] | None = None, *, runner: CommandRunner | None = None, env: dict[str, str] | None = None
) -> int:
    """Main hook entry: orchestrate post-deploy DB creation + role provisioning."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][postgres-hook] %(message)s",
        stream=sys.stderr,
    )

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < _HOOK_ARGS_MIN or not args[0] or not args[1]:
        logger.info("[IMP:6][hook] Missing PROJECT_DIR or PROJECT — skipping postgres hook")
        return 0

    project_dir, project = args[0], args[1]
    logger.info("[IMP:9][hook] === postgres on-project-deploy START: %s ===", project)
    rc = auto_create_db(project_dir, project, env, runner=runner)
    logger.info("[IMP:9][hook] === postgres on-project-deploy DONE: %s ===", project)
    return rc


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
