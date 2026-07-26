#!/usr/bin/env python3
# GREP_SUMMARY: audit-logger, json-lines, write-audit-entry, read-audit-log, platform-audit
# STRUCTURE: ▶ write_audit_entry(tag, status, msg) → ◇ JSON-lines append → ⊕ read_audit_log(limit) → ⊕ CLI → ⎋
# region MODULE_CONTRACT
## @purpose  Unified audit logger with JSON-lines format — replaces direct file.write in
##           context_deployer.py and docker_orchestrator.py with standardized machine-parseable
##           audit trail. Uses /var/log/platform/audit.jsonl (JSON-lines) separate from the
##           existing shell audit.log (pipe-delimited) to avoid breaking existing consumers.
## @scope    Shared library consumed by context_deployer.py, docker_orchestrator.py, and any
##           other module needing structured audit logging. Python-importable for direct calls;
##           CLI accessible via `python3 -m core.internal.shared.audit_logger`.
## @invariants
##   1. JSON-lines format: one JSON object per line (not JSON array)
##   2. Thread-safe via O_APPEND (atomic for lines < PIPE_BUF on POSIX)
##   3. Creates log directory if absent (os.makedirs with exist_ok=True)
##   4. Non-fatal on write failure: catches OSError, logs WARNING, does NOT raise
##   5. Default log file is /var/log/platform/audit.jsonl (separate from pipe-delimited audit.log)
##   6. Timestamp in ISO8601 UTC format via datetime.utcnow().strftime
## @rationale DevPlan 081B5: Existing context_deployer.py writes ad-hoc audit entries via direct
##            file.write(). A unified JSON-lines format enables machine-parseable audit trails
##            that can be consumed by observability pipelines. Separate .jsonl extension prevents
##            breaking existing shell consumers of audit.log.
## @changes  2026-07-26 | DevPlan 081B5 — Created audit logger module
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger("audit_logger")

DEFAULT_LOG_FILE = "/var/log/platform/audit.jsonl"


# region FUNC_write_audit_entry


def write_audit_entry(
    tag: str,
    status: str,
    message: str,
    log_file: str = DEFAULT_LOG_FILE,
) -> None:
    """Append a JSON-lines audit entry to the log file.

    ▶ ┌tag, status, msg┐ → ◇ mkdir -p log_dir → ⊕ build JSON line → ⊕ O_APPEND write → ⎋

    ## @purpose — Append a single structured audit entry in JSON-lines format.
    ##            Thread-safe via O_APPEND on POSIX (atomic for lines < PIPE_BUF).
    ##            Non-fatal: failures are logged at WARNING, never raised.
    ## @io — ⇥ tag: str — logical tag (e.g. "context_deploy:myproj")
    ##       ⇥ status: str — status code (e.g. "DEPLOYED", "FAILED")
    ##       ⇥ message: str — human-readable description
    ##       ⇥ log_file: str — path to JSON-lines log file (default /var/log/platform/audit.jsonl)
    ##       → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - Creates parent directory if absent (os.makedirs exist_ok=True)
    ##   - Uses O_APPEND mode for thread-safe writes
    ##   - Catches OSError, logs WARNING, does NOT raise
    ##   - Timestamp in ISO8601 UTC
    ##   - JSON serialization failure logged at ERROR, still does NOT raise
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

    # ── Build JSON entry ──
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": tag,
        "status": status,
        "msg": message,
    }

    try:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
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
    except OSError as e:
        logger.warning(
            "[IMP:7][write_audit_entry] Cannot write to %s: %s — audit entry dropped",
            log_file,
            e,
        )


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
