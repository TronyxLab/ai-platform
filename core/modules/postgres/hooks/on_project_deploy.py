#!/usr/bin/env python3
# GREP_SUMMARY: postgres hook on-project-deploy auto-create-db database project needs.database
# STRUCTURE: ▶ main(args) → ◇ auto_create_db(project_dir, project) → ◇ NodeYaml needs.database → ◇ validate db_name → ◇ docker exec psql → ⎋ log done
# region MODULE_CONTRACT
## @purpose  Post-deploy hook for postgres module: auto-create project database if declared in ai-platform.yaml needs.database.
##           Python port of core/modules/postgres/hooks/on-project-deploy.sh (DevPlan 117 Brief H D65).
## @scope    Invoked after successful project deploy via hooks/on-project-deploy.sh (thin wrapper);
##           receives PROJECT_DIR, PROJECT, NODE_NAME (NODE_NAME unused).
## @invariants
##   - Non-fatal: errors are logged but do not block deploy
##   - Only creates database if ai-platform.yaml has needs.database set to a valid name
##   - Database name validated: ^[a-zA-Z0-9_]+$
##   - POSTGRES_PASSWORD must be available (via environment or secrets)
##   - docker exec postgres psql used to create database
##   - Business functions never call sys.exit — return int status; sys.exit only in main()
## @rationale Language policy (Python-first): "False"-conversion, regex validation and
##            psql-output parsing are business logic → extracted from shell to Python.
##            NodeYaml imported directly (Python→Python, no subprocess).
## @changes  Ported from on-project-deploy.sh (2026-08-02, DevPlan 117 Brief H D65);
##           shell kept as thin wrapper (same path in module.yaml — no contract change)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
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
from core.internal.shared.node_yaml import NodeYaml

logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")

__all__ = ["auto_create_db", "main"]


# ═══════════════════════════════════════════════════════════════════
# region FUNC_auto_create_db
## @purpose  Create project database if needs.database is declared in ai-platform.yaml.
## @param project_dir  Path to the deployed project directory (contains ai-platform.yaml)
## @param project      Project name (for logs only)
## @param env          Environment dict override (defaults to os.environ) — for testability
## @return  int status: 0 = ok/skip, 1 = fatal (invalid db_name, psql failure)
## @rationale Mirrors _auto_create_db() from the shell hook 1:1, with one ordering fix:
##            psql returns non-zero when the database already exists, so the output is
##            checked for "already exists" BEFORE the returncode failure branch — preserves
##            the hook's idempotency intent (skip on existing DB, not CRITICAL).
def auto_create_db(
    project_dir: str,
    project: str,
    env: dict[str, str] | None = None,
) -> int:
    """Create project database if needs.database is declared in ai-platform.yaml."""
    ai_yaml = os.path.join(project_dir, "ai-platform.yaml")

    if not os.path.isfile(ai_yaml):
        logger.info("[IMP:8][db] No ai-platform.yaml found — skipping")
        return 0

    # NodeYaml direct import (Python→Python, D65). Note: database: false in YAML
    # returns bool False, not empty — handle missing key (default "") and explicit false.
    db_name = NodeYaml(ai_yaml).get("needs.database", default="")
    if db_name is None or db_name is False or str(db_name).lower() in ("false",):
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
        return 0

    if result.returncode != 0:
        logger.error("[IMP:10][db] CRITICAL: psql exec failed for database '%s': %s", db_name, db_output.strip())
        return 1

    if re.search(r"ERROR", db_output, re.IGNORECASE):
        logger.error("[IMP:9][db] Failed to create database '%s': %s", db_name, db_output.strip())
        return 1

    logger.info("[IMP:9][db] Database '%s' created for project '%s'", db_name, project)
    return 0


# endregion FUNC_auto_create_db


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  Main hook entry: orchestrate post-deploy DB creation.
## @io       stdin: positional args PROJECT_DIR PROJECT [NODE_NAME]
##           stderr: LDD logs
## @exitcode 0  Success or skip (missing args / no DB declared / no password)
## @exitcode 1  Fatal: invalid db_name or psql failure
def main(argv: list[str] | None = None) -> int:
    """Main hook entry: orchestrate post-deploy DB creation."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][postgres-hook] %(message)s",
        stream=sys.stderr,
    )

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or not args[0] or not args[1]:
        logger.info("[IMP:6][hook] Missing PROJECT_DIR or PROJECT — skipping postgres hook")
        return 0

    project_dir, project = args[0], args[1]
    logger.info("[IMP:9][hook] === postgres on-project-deploy START: %s ===", project)
    rc = auto_create_db(project_dir, project)
    logger.info("[IMP:9][hook] === postgres on-project-deploy DONE: %s ===", project)
    return rc


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
