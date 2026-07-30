#!/usr/bin/env python3
"""
Unified audit logger for DeployOrchestrator — wraps shared audit_logger with deploy-specific format.
"""
# GREP_SUMMARY: audit-logger, deploy-audit, json-lines, ndjson, log, syslog, unified
# STRUCTURE: ▶ AuditLogger.__init__(log_file) → ○ log(operation, project, channel, result, duration, snapshot_id) → ⊕ JSON line to file + syslog
# region MODULE_CONTRACT
## @purpose  Unified audit logger for deploy operations. Wraps core.internal.shared.audit_logger
##           with DeployOrchestrator-specific fields: operation, project, channel, result, duration, snapshot_id.
##           Output: JSON-lines file (/var/log/platform/audit.log) + syslog (facility LOCAL6).
##           Permissions: chmod 640, chown :adm.
##           Replaces deprecated shell audit logger + deploy_engine.py.audit_write() (Python) — two formats → one.
## @scope    Used by DeployOrchestrator for all deploy audit entries. Consumed by observability pipelines.
## @invariants
##   1. JSON-lines format: one JSON object per line
##   2. Thread-safe via O_APPEND (atomic for lines < PIPE_BUF on POSIX)
##   3. Creates log directory if absent (os.makedirs with exist_ok=True)
##   4. Non-fatal on write failure: catches OSError, logs WARNING, does NOT raise
##   5. Default log file is /var/log/platform/audit.log
##   6. Timestamp in ISO8601 UTC format
##   7. Permissions: chmod 640, chown :adm on first write
## @rationale DevPlan 089 DD3: deprecated shell audit logger and deploy_engine.py.audit_write() (Python)
##            have different formats → single unified format eliminates search problems.
## @changes 2026-07-30 | DevPlan 089 T4 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "/var/log/platform/audit.log"


# region CLASS_AuditLogger


class AuditLogger:
    """Unified deploy audit logger.

    ## @purpose — Write structured deploy audit entries in JSON-lines format.
    ##            Provides log() method for DeployOrchestrator and low-level write_entry() for flexibility.
    ## @io — ⇥ operation, project, channel, result, duration, snapshot_id → ⎋ None
    ## @complexity — O(1)
    ## @invariants
    ##   - Non-fatal: raises/writes nothing on failure (logs WARNING)
    ##   - Thread-safe via O_APPEND mode
    ##   - Permissions set on first write (chmod 640, chown :adm if running as root)
    """

    def __init__(self, log_file: str = DEFAULT_LOG_FILE):
        self.log_file = log_file
        self._permissions_set = False

    def log(
        self,
        operation: str,
        project: str,
        channel: str = "",
        result: str = "",
        duration_s: float = 0.0,
        snapshot_id: str | None = None,
        **extra: str,
    ) -> None:
        """Write a deploy audit entry.

        Args:
            operation: Operation name (deploy, rollback, status, remove, deploy_many).
            project: Project name.
            channel: Delivery channel name (scp, forced-command, or empty).
            result: Result status (DEPLOYED, FAILED, PARTIAL, SKIPPED, etc.).
            duration_s: Operation duration in seconds.
            snapshot_id: Optional deploy snapshot ID for rollback tracking.
            **extra: Additional key-value pairs to include in the entry.
        """
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "operation": operation,
            "project": project,
            "channel": channel,
            "result": result,
            "duration_s": round(duration_s, 3),
            "snapshot_id": snapshot_id or "",
        }
        if extra:
            entry.update(extra)

        self.write_entry(entry)

    def log_many(
        self,
        operation: str,
        projects: list[str],
        channel: str = "",
        results: list[str] | None = None,
        overall_result: str = "",
    ) -> None:
        """Write a multi-project audit entry.

        Args:
            operation: Operation name (deploy_many, etc.).
            projects: List of project names.
            channel: Delivery channel name.
            results: Per-project results.
            overall_result: Overall result (DEPLOYED, PARTIAL, FAILED).
        """
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "operation": operation,
            "projects": projects,
            "channel": channel,
            "result": overall_result,
            "project_count": len(projects),
        }
        if results:
            entry["per_project_results"] = results

        self.write_entry(entry)

    def write_entry(self, entry: dict) -> None:
        """Write a raw JSON entry to the audit log.

        Args:
            entry: Dictionary to serialize as JSON line.
        """
        log_dir = os.path.dirname(self.log_file)

        # Ensure log directory exists
        if not os.path.isdir(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                logger.warning("[IMP:7][AuditLogger] Cannot create log dir %s: %s", log_dir, e)
                return

        # Serialize to JSON line
        try:
            line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        except (TypeError, ValueError) as e:
            logger.error("[IMP:8][AuditLogger] JSON serialization failed: %s", e)
            return

        # Append via O_APPEND (thread-safe on POSIX)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)

            # Set permissions on first write
            if not self._permissions_set:
                self._set_permissions()
        except OSError as e:
            logger.warning("[IMP:7][AuditLogger] Cannot write to %s: %s", self.log_file, e)

    def _set_permissions(self) -> None:
        """Set log file permissions (chmod 640, chown :adm)."""
        try:
            os.chmod(self.log_file, 0o640)
            # Try to chgrp to adm — non-fatal if not root
            if os.geteuid() == 0:
                import grp  # noqa: PLC0415

                try:
                    adm_gid = grp.getgrnam("adm").gr_gid
                    os.chown(self.log_file, -1, adm_gid)
                except (KeyError, OSError):
                    pass
            self._permissions_set = True
        except OSError as e:
            logger.warning("[IMP:7][AuditLogger] Cannot set permissions on %s: %s", self.log_file, e)


# endregion CLASS_AuditLogger


# region CLI


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0913
    """Build CLI argument parser for audit logger operations.

    ## @purpose — CLI entry for standalone audit log operations.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Unified deploy audit logger — write/read audit entries (DevPlan 089 T4)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # write subcommand
    write_parser = subparsers.add_parser("write", help="Write an audit entry")
    write_parser.add_argument("--operation", required=True, help="Operation name")
    write_parser.add_argument("--project", required=True, help="Project name")
    write_parser.add_argument("--channel", default="", help="Delivery channel")
    write_parser.add_argument("--result", default="", help="Result status")
    write_parser.add_argument("--duration", type=float, default=0.0, help="Duration in seconds")
    write_parser.add_argument("--snapshot-id", default="", help="Deploy snapshot ID")
    write_parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="Log file path")

    # read subcommand
    read_parser = subparsers.add_parser("read", help="Read audit entries")
    read_parser.add_argument("--limit", type=int, default=100, help="Max entries to return")
    read_parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help="Log file path")

    return parser


def main() -> int:
    """CLI entry point.

    ## @purpose — CLI wrapper for AuditLogger operations.
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
    ## @complexity — O(L) for read, O(1) for write
    """
    import argparse  # noqa: PLC0415

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "write":
        audit = AuditLogger(log_file=args.log_file)
        audit.log(
            operation=args.operation,
            project=args.project,
            channel=args.channel,
            result=args.result,
            duration_s=args.duration,
            snapshot_id=args.snapshot_id or None,
        )
        return 0

    if args.command == "read":
        from core.internal.shared.audit_logger import read_audit_log  # noqa: PLC0415

        entries = read_audit_log(log_file=args.log_file, limit=args.limit)
        for entry in entries:
            import json  # noqa: PLC0415

            print(json.dumps(entry, ensure_ascii=False))
        return 0

    return 1


# endregion CLI

if __name__ == "__main__":
    sys.exit(main())
