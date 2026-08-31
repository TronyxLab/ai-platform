#!/usr/bin/env python3
# GREP_SUMMARY: backup-cron entrypoint render_env_lines write_env_file /etc/environment cron -f os.execvp PID1
# STRUCTURE: load os.environ → render_env_lines (KEY=VALUE, skip multiline) → write_env_file (mode 0600) → os.execvp cron -f
# region MODULE_CONTRACT
"""
Container entrypoint for backup-cron: dumps container env to /etc/environment
before exec'ing cron in foreground.

@purpose  Bridge the architectural gap (DevPlan 143 W1B D1): Debian cron (vixie,
          bookworm) does NOT inherit the container env for its jobs — only the
          env present at daemon startup. cron jobs (03:00 pg_dumpall, hourly
          wal_sync) ran WITHOUT POSTGRES_HOST/S3_*/WAL_* → backup-by-schedule
          failed while manual `docker exec` worked (env inherited via exec).
          Debian cron reads /etc/environment at daemon start and exports its
          variables to all jobs (Debian-specific patch). This entrypoint writes
          the container env to /etc/environment (mode 0600) then replaces itself
          with `cron -f` via os.execvp (PID 1 = cron → healthcheck `pgrep cron`
          semantics unchanged).
@scope    core/modules/backup-cron/scripts/entrypoint.py → COPY'd to
          /usr/local/bin/entrypoint.py in the image; CMD in Dockerfile.
@input    os.environ (container env from compose `environment:` block):
          POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD,
          S3_*, WAL_*, BACKUP_SPOOL_DIR, NODE_NAME, PLATFORM_CONTEXT, ...
@output   /etc/environment (mode 0600, owner root) — KEY=VALUE lines; then
          process image replaced by cron (PID 1).
@invariants
  - render_env_lines: KEY=VALUE format; values containing "\n" are SKIPPED
    (newline would break /etc/environment line-oriented parsing → silent
    truncation). All other values (including empty) are written as-is.
  - write_env_file: atomic write (write temp + os.replace), mode 0600
    (root-only readable — /etc/environment contains POSTGRES_PASSWORD,
    S3_SECRET_KEY). Path configurable (unit tests use tmp_path).
  - main: writes /etc/environment, then os.execvp("cron", ["cron", "-f"]).
    execvp replaces the process image — PID 1 becomes cron (healthcheck
    `pgrep cron` semantics unchanged, no child-process management).
  - No secrets logged (CONSTITUTION §2): render_env_lines output is NOT
    logged; only key names + count are logged (IMP:7/9).
@rationale Q: why /etc/environment and not crontab-shim or shell entrypoint?
          A: DevPlan 143 W1B TRAP[DECISION] — (a) crontab-shim `. /etc/container-env`
          changes crontab (test_backup_cron.py pins it, 5 edits), worse idempotency;
          (b) shell entrypoint `env > /etc/environment; exec cron -f` violates
          language policy (new code = Python); (c) build-time env — compose secrets
          are runtime, not in image. Python entrypoint is the language-policy-
          compliant solution with a unit-testable pure function (render_env_lines).
          Q: why os.execvp and not subprocess + sys.exit?
          A: cron must be PID 1 (Docker forwards signals to PID 1; `pgrep cron`
          healthcheck). execvp replaces the process image in-place — PID stays 1,
          no orphan/zombie risk. subprocess would make cron a child of python
          (PID 1 = python), breaking signal forwarding and healthcheck semantics.
@changes 2026-08-08 | DevPlan 143 W1B — created
@changes 2026-08-31 | 018 W7 F-23 — mkdir /run/lock перед exec cron: все cron-задачи
          (flock-guarded) на tronyx-vps падали мгновенно ("cannot open lock file")
          — /run/lock отсутствует в debian:bookworm-slim без systemd; ночные
          бэкапы/retention/WAL-sync не выполнялись НИ РАЗУ с бутстрапа (RPO 24ч
          фиктивен). Fix: entrypoint гарантирует директору flock-локов.
"""
# endregion MODULE_CONTRACT

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Canonical target for env dump (Debian cron reads this at daemon start).
_ENV_FILE_PATH = "/etc/environment"
# flock lock dir — cron-задачи в /etc/cron.d/platform-backup все flock-guarded.
# debian:bookworm-slim без systemd НЕ создаёт /run/lock → flock падает мгновенно.
_LOCK_DIR = "/run/lock"
# exec target — PID 1 becomes cron (foreground, no daemonize).
_CRON_ARGV = ["cron", "-f"]


# region render_env_lines


def render_env_lines(env: dict[str, str]) -> list[str]:
    """Render environment dict to /etc/environment lines (KEY=VALUE).

    Pure function (no I/O) — unit-testable with synthetic dicts.

    Rules:
      - Format: ``KEY=VALUE`` (no quoting — Debian /etc/environment is
        line-oriented, shells parse it via PAM; values with spaces are OK
        because cron passes them to the job env verbatim).
      - Values containing ``\\n`` are SKIPPED: a newline in a value would
        inject a spurious line into /etc/environment, silently corrupting
        subsequent entries (line-oriented parser splits on ``\\n``).
      - Empty-string values ARE included (``KEY=``) — explicit empty env var
        is semantically meaningful (e.g. ``PLATFORM_CONTEXT=``).
      - Order: sorted by key for deterministic output (idempotent writes,
        stable tests, reproducible /etc/environment across restarts).

    Args:
        env: Environment mapping (typically ``os.environ``). Keys must be
            non-empty strings; values are strings.

    Returns:
        List of ``KEY=VALUE`` lines (no trailing newline — caller joins with
        ``\\n``). Multiline values excluded.

    """
    lines: list[str] = []
    skipped = 0
    for key in sorted(env):
        value = env[key]
        # Skip values containing newline — would break line-oriented parsing.
        if isinstance(value, str) and "\n" in value:
            skipped += 1
            continue
        lines.append(f"{key}={value}")
    logger.info(
        "[IMP:7][backup-cron-entrypoint][render_env_lines] rendered %d env lines (%d skipped multiline)",
        len(lines),
        skipped,
    )
    return lines


# endregion render_env_lines


# region write_env_file


def write_env_file(env: dict[str, str], path: str | Path) -> int:
    """Write environment dict to file in KEY=VALUE format (mode 0600).

    Atomic write: content is written to a temp file in the same directory,
    then ``os.replace`` swaps it into place (POSIX atomic rename). Mode 0600
    is set BEFORE the rename so the file is never world-readable at the
    final path (it contains POSTGRES_PASSWORD, S3_SECRET_KEY).

    Args:
        env: Environment mapping (see :func:`render_env_lines`).
        path: Target file path (e.g. ``/etc/environment``). Parent directory
            must exist and be writable.

    Returns:
        Number of lines written (for observability / test assertions).

    """
    target = Path(path)
    lines = render_env_lines(env)
    content = "\n".join(lines) + "\n"

    # Atomic write: temp file in same dir → chmod 0600 → os.replace.
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    Path(tmp_path).chmod(0o600)
    Path(tmp_path).replace(target)

    logger.info(
        "[IMP:9][backup-cron-entrypoint][write_env_file] wrote %d lines to %s (mode 0600)",
        len(lines),
        target,
    )
    return len(lines)


# endregion write_env_file


# region main


def main() -> int:
    """Entrypoint: dump env to /etc/environment, then exec cron -f.

    Replaces the process image with ``cron -f`` via :func:`os.execvp` —
    PID 1 becomes cron (healthcheck ``pgrep cron`` semantics unchanged).
    This function does NOT return on success (exec replaces the image);
    it returns an exit code only if exec fails.

    Returns:
        Exit code (only on exec failure — normally never returns).

    """
    count = write_env_file(dict(os.environ), _ENV_FILE_PATH)
    # ⚠️ TRAP[BUG] · 2026-08-31 · P1 · ночные бэкапы fail-closed: flock без /run/lock
    # · Symptom: /var/log/platform/backup/*.log содержат ТОЛЬКО "flock: cannot open
    # ·   lock file /run/lock/platform-*.lock: No such file or directory" — каждая
    # ·   cron-задача (dump/app-data/cleanup/retention/wal-sync/spool-retry) умирала
    # ·   мгновенно, nightly uploads отсутствовали (017 §5 класс: RPO фиктивен).
    # · Root: debian:bookworm-slim без systemd не создаёт /run/lock; cron-задачи
    # ·   guard'ятся `flock -n /run/lock/*.lock` → flock(2) ENOENT → job не стартует.
    # ·   Manual `docker exec ... backup-postgres.sh` работал (flock-обёртка — только в cron).
    # · Fix: entrypoint mkdir -p /run/lock до exec cron (runtime-гарантия при любом
    # ·   деплое/пересоздании, не зависит от base-image обновлений).
    # · Prevention: startup-prereq canon — PID-1 entrypoint обязан создавать runtime-каталоги,
    # ·   от которых зависят его child-процессы (проверялось бы pre-flight на ноде).
    Path(_LOCK_DIR).mkdir(exist_ok=True, parents=True)
    logger.info(
        "[IMP:9][backup-cron-entrypoint][main] env dumped (%d lines), lock dir %s ready, exec'ing cron -f as PID 1",
        count,
        _LOCK_DIR,
    )
    # execvp replaces the process image — PID 1 becomes cron.
    # If execvp returns, it raised OSError (e.g. cron binary missing).
    os.execvp("cron", _CRON_ARGV)
    return 1  # pragma: no cover — execvp raises on failure, never returns


# endregion main


if __name__ == "__main__":
    raise SystemExit(main())
