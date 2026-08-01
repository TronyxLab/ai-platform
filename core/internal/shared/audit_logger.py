#!/usr/bin/env python3
# GREP_SUMMARY: audit-logger, json-lines, write-audit-entry, read-audit-log, platform-audit, extra-fields, permissions
# STRUCTURE: ▶ write_audit_entry(tag, status, msg, **extra) → ◇ mkdir -p → ◇ chmod 640/chown :adm → ◇ JSON-lines append → ⊕ read_audit_log(limit) → ⊕ CLI → ⎋
# region MODULE_CONTRACT
## @purpose  Unified audit logger with JSON-lines format — ЕДИНСТВЕННЫЙ writer платформы (D1, DevPlan 116 B11 T2):
##           заменяет прямой file.write, deploy/audit_logger.py (удалён) и reporting.py free-text pipe.
##           Усиленная схема: ts/tag/status/msg + optional extra-поля (operation/project/channel/result/
##           duration_s/snapshot_id/... через **extra). Uses /var/log/platform/audit.jsonl (JSON-lines).
## @scope    Shared library consumed by context_deployer.py, DeployOrchestrator (adapter), reporting.py,
##           and any other module needing structured audit logging. Python-importable for direct calls;
##           CLI accessible via `python3 -m core.internal.shared.audit_logger`.
## @invariants
##   1. JSON-lines format: one JSON object per line (not JSON array)
##   2. Thread-safe via O_APPEND (atomic for lines < PIPE_BUF on POSIX)
##   3. Creates log directory if absent (os.makedirs with exist_ok=True)
##   4. Non-fatal on write failure: catches OSError, logs WARNING, does NOT raise
##   5. Default log file is /var/log/platform/audit.jsonl (единый файл — deploy-записи тоже сюда, D1)
##   6. Timestamp in ISO8601 UTC format via datetime.now(timezone.utc).strftime
##   7. Permissions on first write: chmod 640, chown :adm (если euid=0) — консолидировано из deploy/audit_logger.py (D1)
##   8. Extended schema: write_audit_entry(..., **extra) — extra-поля сериализуются в ту же JSON-строку;
##      backward-compat: вызовы без extra работают как раньше (только ts/tag/status/msg)
## @rationale DevPlan 081B5: unified JSON-lines audit trail. DevPlan 116 B11 T2 (U-10, D1):
##            полная консолидация — 3 writer'а (shared, deploy/audit_logger.py, reporting pipe) → один.
## @changes  2026-07-26 | DevPlan 081B5 — Created audit logger module
##           2026-08-01 | DevPlan 116 B11 T2 (U-10, D1) — extended schema (**extra),
##                      permissions chmod 640/chown :adm, единый файл audit.jsonl
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "/var/log/platform/audit.jsonl"


# region FUNC_write_audit_entry


def write_audit_entry(
    tag: str,
    status: str,
    message: str,
    log_file: str = DEFAULT_LOG_FILE,
    **extra,
) -> None:
    """Append a JSON-lines audit entry to the log file.

    ▶ ┌tag, status, msg, **extra┐ → ◇ mkdir -p log_dir → ◇ chmod 640/chown :adm (first write)
      → ⊕ build JSON line (base + extra) → ⊕ O_APPEND write → ⎋

    ## @purpose — Append a single structured audit entry in JSON-lines format.
    ##            Thread-safe via O_APPEND on POSIX (atomic for lines < PIPE_BUF).
    ##            Non-fatal: failures are logged at WARNING, never raised.
    ## @io — ⇥ tag: str — logical tag (e.g. "deploy:deploy", "bootstrap:init")
    ##       ⇥ status: str — status code (e.g. "DEPLOYED", "FAILED", "DONE", "WARN")
    ##       ⇥ message: str — human-readable description
    ##       ⇥ log_file: str — path to JSON-lines log file (default /var/log/platform/audit.jsonl)
    ##       ⇥ **extra: dict — расширенная схема (D1): operation, project, channel, result,
    ##            duration_s, snapshot_id, projects, per_project_results, error_info, ...
    ##       → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - Creates parent directory if absent (os.makedirs exist_ok=True)
    ##   - Uses O_APPEND mode for thread-safe writes
    ##   - Sets chmod 640 / chown :adm on first write (если euid==0; non-fatal)
    ##   - Catches OSError, logs WARNING, does NOT raise
    ##   - Timestamp in ISO8601 UTC
    ##   - JSON serialization failure logged at ERROR, still does NOT raise
    ##   - extra-поля сериализуются в ту же JSON-строку (не отдельной записью)
    """
    log_dir = os.path.dirname(log_file)

    # ── Ensure log directory exists ──
    if not os.path.isdir(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            logger.info("[IMP:7][write_audit_entry] Created log directory: %s", log_dir)
        except OSError as e:
            logger.warning(
                "[IMP:7][write_audit_entry] Cannot create log directory %s: %s — audit entry dropped",
                log_dir,
                e,
            )
            return

    # ── Build JSON entry (base schema + extended extra fields, D1) ──
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": tag,
        "status": status,
        "msg": message,
    }
    if extra:
        # Обратная совместимость: extra-поля НЕ перезаписывают базовую схему
        for key, value in extra.items():
            if key not in entry:
                entry[key] = value
            else:
                logger.warning("[IMP:7][write_audit_entry] extra key %r collides with base schema — skipped", key)

    try:
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    except (TypeError, ValueError) as e:
        logger.error(
            "[IMP:8][write_audit_entry] JSON serialization failed for tag=%s status=%s: %s",
            tag,
            status,
            e,
        )
        return

    # ── Append via O_APPEND (thread-safe on POSIX) ──
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
        logger.info("[IMP:9][write_audit_entry] Wrote audit entry: tag=%s status=%s", tag, status)
        # Permissions on first write (chmod 640 / chown :adm) — консолидировано из
        # deploy/audit_logger.py (D1, DevPlan 116 B11 T2). Non-fatal; set once per file.
        if log_file not in _PERMISSIONS_SET:
            _set_audit_permissions(log_file)
            _PERMISSIONS_SET.add(log_file)
    except OSError as e:
        logger.warning(
            "[IMP:7][write_audit_entry] Cannot write to %s: %s — audit entry dropped",
            log_file,
            e,
        )


# Permissions applied per log file (once) — module-level guard for _set_audit_permissions
_PERMISSIONS_SET: set[str] = set()


def _set_audit_permissions(log_file: str) -> None:
    """Set log file permissions (chmod 640, chown :adm) — non-fatal.

    ▶ ┌log_file┐ → ◇ chmod 0o640 → ◇ euid==0 ? chown :adm → ⎋ None
    ## @purpose  Consolidated from deploy/audit_logger.py (D1): audit.jsonl имеет те же
    ##            пермишены, что прежний audit.log (640 root:adm).
    ## @complexity O(1)
    """
    try:
        os.chmod(log_file, 0o640)
        if os.geteuid() == 0:
            import grp

            try:
                adm_gid = grp.getgrnam("adm").gr_gid
                os.chown(log_file, -1, adm_gid)
            except (KeyError, OSError):
                pass
    except OSError as e:
        logger.warning("[IMP:7][_set_audit_permissions] Cannot set permissions on %s: %s", log_file, e)


# endregion FUNC_write_audit_entry


# region FUNC_read_audit_log


def read_audit_log(
    log_file: str = DEFAULT_LOG_FILE,
    limit: int = 100,
) -> list[dict]:
    """Read the last `limit` entries from a JSON-lines log file.

    ▶ ┌log_file, limit┐ → ◇ exists? → ⊕ reverse-read last (limit×N) lines → ○ parse JSON → ◇ skip malformed → ⎋ list[dict]

    ## @purpose — Retrieve the most recent audit entries. Uses reverse-line reading
    ##            from the end of the file for efficiency.
    ## @io — ⇥ log_file: str — path to JSON-lines log file (default /var/log/platform/audit.jsonl)
    ##       ⇥ limit: int — max entries to return (default 100)
    ##       → ⎋ list[dict] — parsed JSON entries in chronological order (oldest first)
    ## @complexity — O(L) where L = lines scanned from end (approximately limit + malformed)
    ## @invariants
    ##   - Returns empty list if file doesn't exist or is empty
    ##   - Skips malformed JSON lines (logs WARN, continues)
    ##   - Returns entries in chronological order (oldest first within the returned window)
    ##   - Scans from end of file for efficiency on large logs
    """
    if not os.path.isfile(log_file):
        logger.info("[IMP:8][read_audit_log] Log file not found: %s — returning empty list", log_file)
        return []

    entries: list[dict] = []

    try:
        with open(log_file, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("[IMP:7][read_audit_log] Cannot read %s: %s — returning empty list", log_file, e)
        return []

    if not lines:
        logger.info("[IMP:8][read_audit_log] Log file is empty: %s", log_file)
        return []

    # ── Parse from end, collect reversed, then re-reverse for chronological order ──
    parsed = 0
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[IMP:7][read_audit_log] Skipping malformed JSON line: %.80s", line[:80])
            continue

        entries.append(record)
        parsed += 1
        if parsed >= limit:
            break

    # Reverse back to chronological order
    entries.reverse()

    logger.info(
        "[IMP:9][read_audit_log] Returned %d audit entries from %s (requested limit=%d)",
        len(entries),
        log_file,
        limit,
    )
    return entries


# endregion FUNC_read_audit_log


# region CLI


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose — CLI entry for standalone audit log operations.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Unified audit logger — write/read JSON-lines audit trail (DevPlan 081B5)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── write subcommand ──
    write_parser = subparsers.add_parser("write", help="Write an audit entry")
    write_parser.add_argument("--tag", required=True, help="Logical tag (e.g. context_deploy:myproj)")
    write_parser.add_argument("--status", required=True, help="Status code (e.g. DEPLOYED, FAILED)")
    write_parser.add_argument("--msg", required=True, help="Human-readable message")
    write_parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="JSON-lines log file path")

    # ── read subcommand ──
    read_parser = subparsers.add_parser("read", help="Read audit entries")
    read_parser.add_argument("--limit", type=int, default=100, help="Max entries to return (default 100)")
    read_parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="JSON-lines log file path")

    return parser


def main() -> int:
    """CLI entry point.

    ▶ ┌sys.argv┐ → ◇ parse → ◇ write/read dispatch → print → ⎋ exit 0/1

    ## @purpose — CLI wrapper for write_audit_entry and read_audit_log.
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
    ## @complexity — O(L) for read, O(1) for write
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "write":
        write_audit_entry(
            tag=args.tag,
            status=args.status,
            message=args.msg,
            log_file=args.log_file,
        )
        return 0

    if args.command == "read":
        entries = read_audit_log(
            log_file=args.log_file,
            limit=args.limit,
        )
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False))
        logger.info("[IMP:8][main] Printed %d audit entries to stdout", len(entries))
        return 0

    return 1


# endregion CLI

if __name__ == "__main__":
    sys.exit(main())
