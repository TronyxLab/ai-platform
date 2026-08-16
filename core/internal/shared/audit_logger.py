#!/usr/bin/env python3
# GREP_SUMMARY: audit-logger, json-lines, write-audit-entry, read-audit-log, platform-audit, extra-fields, permissions
# STRUCTURE: ▶ write_audit_entry(tag, status, msg, **extra) → ◇ mkdir -p → ◇ chmod 640/chown :adm → ◇ JSON-lines append → ⊕ read_audit_log(limit) → ⊕ CLI → ⎋
# region MODULE_CONTRACT
## @purpose  Unified audit logger with JSON-lines format — ЕДИНСТВЕННЫЙ writer платформы (D1, DevPlan 116 B11 T2):
##           заменяет прямой file.write, deploy/audit_logger.py (удалён) и reporting.py free-text pipe.
##           Усиленная схема: ts/tag/status/msg + source (uid/process) + optional extra-поля
##           (operation/project/channel/result/duration_s/snapshot_id/... через **extra).
##           Uses /var/log/platform/audit.jsonl (JSON-lines).
##           DevPlan 136 W10 T10.5 (S-6/S-15): fsync после append (аудит не теряется при краше);
##           CLI write exit≠0 при OSError (fail, не silent-drop); ALERT при malformed JSON в read;
##           source-поле (UID/process) в схеме — атрибуция каждой записи.
## @scope    Shared library consumed by context_deployer.py, DeployOrchestrator (adapter), reporting.py,
##           and any other module needing structured audit logging. Python-importable for direct calls;
##           CLI accessible via `python3 -m core.internal.shared.audit_logger`.
## @invariants
##   1. JSON-lines format: one JSON object per line (not JSON array)
##   2. Thread-safe via O_APPEND (atomic for lines < PIPE_BUF on POSIX)
##   3. Creates log directory if absent (os.makedirs with exist_ok=True)
##   4. fsync после append — запись дюрабельна до возврата (W10 T10.5)
##   5. Default log file is /var/log/platform/audit.jsonl (единый файл — deploy-записи тоже сюда, D1)
##   6. Timestamp in ISO8601 UTC format via datetime.now(timezone.utc).strftime
##   7. Permissions on first write: chmod 640, chown :adm (если euid=0) — консолидировано из deploy/audit_logger.py (D1)
##   8. Extended schema: write_audit_entry(..., **extra) — extra-поля сериализуются в ту же JSON-строку;
##      base-схема всегда содержит source {"uid": euid, "proc": basename(argv[0])} (W10 T10.5)
##   9. write_audit_entry возвращает bool (True=записано); OSError → False + raise_on_error=True → проброс
##      (CLI write: raise_on_error → exit 1 — fail при OSError, W10 T10.5). Library-вызовы в failure-путях
##      (W9: _audit_failed/write_audit_log в except/finally) остаются non-raising — не маскируют оригинал.
##   10. read_audit_log: malformed JSON → ALERT-лог (ERROR, IMP:9 marker) — тампер аудит-журнала виден
## @rationale DevPlan 081B5: unified JSON-lines audit trail. DevPlan 116 B11 T2 (U-10, D1):
##            полная консолидация — 3 writer'а (shared, deploy/audit_logger.py, reporting pipe) → один.
## @changes  2026-07-26 | DevPlan 081B5 — Created audit logger module
##           2026-08-01 | DevPlan 116 B11 T2 (U-10, D1) — extended schema (**extra),
##                      permissions chmod 640/chown :adm, единый файл audit.jsonl
##           2026-08-05 | DevPlan 136 W10 T10.5 — fsync, raise_on_error (CLI exit≠0), ALERT malformed,
##                      source-поле в схеме
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "/var/log/platform/audit.jsonl"


# region DATACLASS_CliArgs
@dataclass
class _CliArgs:
    """Типизированная граница argparse.Namespace CLI (W11, DevPlan 170).
    Аннотации без значений — cast no-op, argparse ставит свои дефолты."""

    command: str
    tag: str
    status: str
    msg: str
    log_file: str
    limit: int


# endregion DATACLASS_CliArgs


# region FUNC_write_audit_entry


# region FUNC__plw_body_write_audit_entry
## @purpose  Тело try-блока (PLW0717 extraction из write_audit_entry) — семантика except не меняется.
## @io       ⇥ line, log_file, status, tag → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_write_audit_entry(line: str, log_file: str, status: str, tag: str) -> None:
    line_bytes = line.encode("utf-8")
    with pathlib.Path(log_file).open("ab") as f:
        f.write(line_bytes)
        f.flush()
        os.fsync(f.fileno())
    logger.info("[IMP:9][write_audit_entry] Wrote audit entry: tag=%s status=%s", tag, status)
    if log_file not in _PERMISSIONS_SET:
        _set_audit_permissions(log_file)
        _PERMISSIONS_SET.add(log_file)


# endregion FUNC__plw_body_write_audit_entry


def write_audit_entry(
    tag: str,
    status: str,
    message: str,
    log_file: str = DEFAULT_LOG_FILE,
    raise_on_error: bool = False,
    **extra: object,
) -> bool:
    """Append a JSON-lines audit entry to the log file. Returns True on success.

    ▶ ┌tag, status, msg, **extra┐ → ◇ mkdir -p log_dir → ◇ chmod 640/chown :adm (first write)
      → ⊕ build JSON line (base + source + extra) → ⊕ O_APPEND write → ⊕ fsync → ⎋ bool

    ## @purpose — Append a single structured audit entry in JSON-lines format.
    ##            Thread-safe via O_APPEND on POSIX (atomic for lines < PIPE_BUF).
    ##            fsync после append — дюрабельность (W10 T10.5).
    ##            Возвращает bool; raise_on_error=True → OSError пробрасывается (CLI exit≠0).
    ## @io — ⇥ tag: str — logical tag (e.g. "deploy:deploy", "bootstrap:init")
    ##       ⇥ status: str — status code (e.g. "DEPLOYED", "FAILED", "DONE", "WARN")
    ##       ⇥ message: str — human-readable description
    ##       ⇥ log_file: str — path to JSON-lines log file (default /var/log/platform/audit.jsonl)
    ##       ⇥ raise_on_error: bool — False (default): OSError → False; True: OSError пробрасывается
    ##       ⇥ **extra: dict — расширенная схема (D1): operation, project, channel, result,
    ##            duration_s, snapshot_id, projects, per_project_results, error_info, ...
    ##       → ⎋ bool — True = записано и fsync'нуто
    ## @complexity — O(1)
    ## @invariants
    ##   - Creates parent directory if absent (os.makedirs exist_ok=True)
    ##   - Uses O_APPEND mode for thread-safe writes
    ##   - Sets chmod 640 / chown :adm on first write (если euid==0; non-fatal)
    ##   - fsync после write (W10 T10.5): запись дюрабельна до возврата
    ##   - OSError → False (raise_on_error=False) или проброс (raise_on_error=True)
    ##   - Timestamp in ISO8601 UTC
    ##   - JSON serialization failure logged at ERROR, returns False
    ##   - source-поле {"uid": euid, "proc": basename(argv[0])} — в КАЖДОЙ записи (W10 T10.5)
    ##   - extra-поля сериализуются в ту же JSON-строку (не отдельной записью)
    """
    log_dir = pathlib.Path(log_file).parent

    # ── Ensure log directory exists ──
    if not os.path.isdir(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
            logger.info("[IMP:7][write_audit_entry] Created log directory: %s", log_dir)
        except OSError as e:
            if raise_on_error:
                raise
            logger.warning(
                "[IMP:7][write_audit_entry] Cannot create log directory %s: %s — audit entry dropped",
                log_dir,
                e,
            )
            return False

    # ── Build JSON entry (base schema + source + extended extra fields, D1 + W10) ──
    entry: dict[str, object] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": tag,
        "status": status,
        "msg": message,
        "source": {"uid": os.geteuid() if hasattr(os, "geteuid") else os.getuid(), "proc": _process_name()},
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
        return False

    # ── Append via O_APPEND (thread-safe on POSIX) + fsync (W10 T10.5) ──
    try:
        _plw_body_write_audit_entry(line, log_file, status, tag)
    except OSError as e:
        if raise_on_error:
            raise
        logger.warning(
            "[IMP:7][write_audit_entry] Cannot write to %s: %s — audit entry dropped",
            log_file,
            e,
        )
        return False
    else:
        return True


def _process_name() -> str:
    """Short process name for the audit source field (W10 T10.5) — basename of argv[0]."""
    try:
        return pathlib.Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "python"
    except (AttributeError, IndexError):  # pragma: no cover — defensive
        return "python"


# Permissions applied per log file (once) — module-level guard for _set_audit_permissions
_PERMISSIONS_SET: set[str] = set()


# region FUNC__plw_body__set_audit_permissions
## @purpose  Тело try-блока (PLW0717 extraction из _set_audit_permissions) — семантика except не меняется.
## @io       ⇥ grp, log_file → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body__set_audit_permissions(log_file: str) -> None:
    os.chmod(log_file, 0o640)
    if os.geteuid() == 0:
        import grp

        try:
            adm_gid = grp.getgrnam("adm").gr_gid
            os.chown(log_file, -1, adm_gid)
        except (KeyError, OSError):
            pass


# endregion FUNC__plw_body__set_audit_permissions


def _set_audit_permissions(log_file: str) -> None:
    """Set log file permissions (chmod 640, chown :adm) — non-fatal.

    ▶ ┌log_file┐ → ◇ chmod 0o640 → ◇ euid==0 ? chown :adm → ⎋ None
    ## @purpose  Consolidated from deploy/audit_logger.py (D1): audit.jsonl имеет те же
    ##            пермишены, что прежний audit.log (640 root:adm).
    ## @complexity O(1)
    """
    try:
        _plw_body__set_audit_permissions(log_file)
    except OSError as e:
        logger.warning("[IMP:7][_set_audit_permissions] Cannot set permissions on %s: %s", log_file, e)


# endregion FUNC_write_audit_entry


# region FUNC_read_audit_log


def read_audit_log(
    log_file: str = DEFAULT_LOG_FILE,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Read the last `limit` entries from a JSON-lines log file.

    ▶ ┌log_file, limit┐ → ◇ exists? → ⊕ reverse-read last (limit×N) lines → ○ parse JSON → ◇ skip malformed → ⎋ list[dict]

    ## @purpose — Retrieve the most recent audit entries. Uses reverse-line reading
    ##            from the end of the file for efficiency.
    ##            W10 T10.5 (S-15): malformed JSON — ALERT-лог (ERROR, [IMP:9][audit][ALERT]) —
    ##            тампер/порча аудит-журнала становится видимым, а не молча пропускается.
    ## @io — ⇥ log_file: str — path to JSON-lines log file (default /var/log/platform/audit.jsonl)
    ##       ⇥ limit: int — max entries to return (default 100)
    ##       → ⎋ list[dict] — parsed JSON entries in chronological order (oldest first)
    ## @complexity — O(L) where L = lines scanned from end (approximately limit + malformed)
    ## @invariants
    ##   - Returns empty list if file doesn't exist or is empty
    ##   - Skips malformed JSON lines (logs ALERT at ERROR, continues) — W10 T10.5
    ##   - Returns entries in chronological order (oldest first within the returned window)
    ##   - Scans from end of file for efficiency on large logs
    """
    if not os.path.isfile(log_file):
        logger.info("[IMP:8][read_audit_log] Log file not found: %s — returning empty list", log_file)
        return []

    entries: list[dict[str, object]] = []

    try:
        with pathlib.Path(log_file).open(encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("[IMP:7][read_audit_log] Cannot read %s: %s — returning empty list", log_file, e)
        return []

    if not lines:
        logger.info("[IMP:8][read_audit_log] Log file is empty: %s", log_file)
        return []

    # ── Parse from end, collect reversed, then re-reverse for chronological order ──
    parsed = 0
    malformed = 0
    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            # json.loads → Any; объектная граница записи аудита (W11)
            record = cast(dict[str, object], json.loads(line))
        except json.JSONDecodeError:
            malformed += 1
            logger.error(
                "[IMP:9][audit][ALERT] Malformed JSON line in %s: %.80s — audit trail integrity issue "
                "(tamper or corruption) — %d line(s) affected",
                log_file,
                line[:80],
                malformed,
            )
            continue

        entries.append(record)
        parsed += 1
        if parsed >= limit:
            break

    # Reverse back to chronological order
    entries.reverse()

    if malformed:
        logger.error(
            "[IMP:9][audit][ALERT] %s: %d malformed JSON line(s) skipped — audit integrity degraded (W10 T10.5)",
            log_file,
            malformed,
        )

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
    ##            W10 T10.5: write с raise_on_error=True — OSError → exit 1 (fail, не silent-drop).
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
    ## @complexity — O(L) for read, O(1) for write
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    args = cast(_CliArgs, cast(object, parser.parse_args()))

    if args.command == "write":
        try:
            ok = write_audit_entry(
                tag=args.tag,
                status=args.status,
                message=args.msg,
                log_file=args.log_file,
                raise_on_error=True,
            )
        except OSError as exc:
            print(f"[FAIL] audit write failed: {exc}", file=sys.stderr)
            return 1
        return 0 if ok else 1

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
