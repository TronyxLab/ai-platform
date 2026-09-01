#!/usr/bin/env python3
# GREP_SUMMARY: audit-logger, json-lines, write-audit-entry, read-audit-log, platform-audit, extra-fields, permissions, ensure-audit-writable, setfacl, acl, ci-deploy, fallback, 0660, dir-traversal, 0710
# STRUCTURE: ▶ write_audit_entry(tag, status, msg, **extra) → ◇ mkdir -p → ◇ ensure_audit_writable ┌setfacl u:ci-deploy:rw + mask rw (primary) │ chgrp ci-deploy + chmod 0660 (fallback)┐ → ◇ dir traversal ┌setfacl u:ci-deploy:--x <dir> (primary) │ chgrp ci-deploy + chmod 0710 <dir> (fallback)┐ → ◇ JSON-lines append + fsync → ⊕ read_audit_log(limit) → ⊕ audit_permissions_status (acl|group|none) → ⊕ CLI → ⎋
# region MODULE_CONTRACT
## @purpose  Unified audit logger with JSON-lines format — ЕДИНСТВЕННЫЙ writer платформы (D1, DevPlan 116 B11 T2):
##           заменяет прямой file.write, deploy/audit_logger.py (удалён) и reporting.py free-text pipe.
##           Усиленная схема: ts/tag/status/msg + source (uid/process) + optional extra-поля
##           (operation/project/channel/result/duration_s/snapshot_id/... через **extra).
##           Uses /var/log/platform/audit.jsonl (JSON-lines).
##           P1 fix 2026-08-27: права аудит-файла — канонический SoT ensure_audit_writable()
##           (POSIX ACL u:ci-deploy:rw primary / chgrp ci-deploy + chmod 0660 fallback);
##           converge R2 (audit.py) сходится к ТОМУ ЖЕ состоянию — антагонизм R2↔logger устранён.
##           P1 fix 2026-09-01 (F-07 asi-team-vps): КАТАЛОГ /var/log/platform — ci-deploy получает
##           traversal (+x, БЕЗ чтения списка): access ACL u:ci-deploy:--x на dir (primary) /
##           chgrp ci-deploy + chmod 0710 (fallback). Без +x на родителе rw-ACL файла бесполезна
##           (700-каталог root:root → Errno 13 Permission denied → audit entry dropped).
##           DevPlan 136 W10 T10.5 (S-6/S-15): fsync после append (аудит не теряется при краше);
##           CLI write exit≠0 при OSError (fail, не silent-drop); ALERT при malformed JSON в read;
##           source-поле (UID/process) в схеме — атрибуция каждой записи.
## @scope    Shared library consumed by context_deployer.py, DeployOrchestrator (adapter), reporting.py,
##           converge/audit.py (R2 — ensure_audit_writable/audit_permissions_status),
##           and any other module needing structured audit logging. Python-importable for direct calls;
##           CLI accessible via `python3 -m core.internal.shared.audit_logger`.
## @invariants
##   1. JSON-lines format: one JSON object per line (not JSON array)
##   2. Thread-safe via O_APPEND (atomic for lines < PIPE_BUF on POSIX)
##   3. Creates log directory if absent (os.makedirs with exist_ok=True)
##   4. fsync после append — запись дюрабельна до возврата (W10 T10.5)
##   5. Default log file is /var/log/platform/audit.jsonl (единый файл — deploy-записи тоже сюда, D1)
##   6. Timestamp in ISO8601 UTC format via datetime.now(timezone.utc).strftime
##   7. Целевые права (P1 fix 2026-08-27 + dir-traversal 2026-09-01): владелец root, запись
##      разрешена root И главному писателю (ci-deploy). PRIMARY — POSIX ACL: setfacl -m
##      u:ci-deploy:rw,m::rw \\<file\\> + default ACL на dir (setfacl -d -m u:ci-deploy:rw,m::rw \\<dir\\>)
##      для ротаций + access ACL traversal на dir (setfacl -m u:ci-deploy:--x \\<dir\\> — +x БЕЗ r,
##      mask НЕ задаётся явно: setfacl пересчитывает её как union(group::, named) и не затирает
##      group:: (0750 root:adm от R2 сохраняет adm-чтение)).
##      FALLBACK без setfacl — chgrp ci-deploy + chmod 0660 на файл + chgrp ci-deploy + chmod 0710
##      на dir (group --x, other ---: члены группы ci-deploy получают traversal; НЕ o+x — world-
##      травера нет; honest trade-off: adm-читатели теряют group-read → sudo/root-канон;
##      TRAP[DECISION]). Non-root (dev/receive) → no-op.
##   8. Extended schema: write_audit_entry(..., **extra) — extra-поля сериализуются в ту же JSON-строку;
##      base-схема всегда содержит source {"uid": euid, "proc": basename(argv[0])} (W10 T10.5)
##   9. write_audit_entry возвращает bool (True=записано); OSError → False + raise_on_error=True → проброс
##      (CLI write: raise_on_error → exit 1 — fail при OSError, W10 T10.5). Library-вызовы в failure-путях
##      (W9: _audit_failed/write_audit_log в except/finally) остаются non-raising — не маскируют оригинал.
##   10. read_audit_log: malformed JSON → ALERT-лог (ERROR, IMP:9 marker) — тампер аудит-журнала виден
## @rationale DevPlan 081B5: unified JSON-lines audit trail. DevPlan 116 B11 T2 (U-10, D1):
##            полная консолидация — 3 writer'а (shared, deploy/audit_logger.py, reporting pipe) → один.
##            P1 fix 2026-08-27: R2 (converge 0664 root:adm) БОРОЛСЯ с runtime (chmod 640 при
##            root-записи) → ci-deploy терял запись → аудит постбутстрапных деплоев молча терялся.
##            Единый SoT прав (ensure_audit_writable) — оба слоя сходятся к одному состоянию.
## @changes  2026-07-26 | DevPlan 081B5 — Created audit logger module
##           2026-08-01 | DevPlan 116 B11 T2 (U-10, D1) — extended schema (**extra),
##                      permissions chmod 640/chown :adm, единый файл audit.jsonl
##           2026-08-05 | DevPlan 136 W10 T10.5 — fsync, raise_on_error (CLI exit≠0), ALERT malformed,
##                      source-поле в схеме
##           2026-08-27 | P1 fix — ensure_audit_writable (setfacl primary / chgrp+0660 fallback),
##                      audit_permissions_status, _set_audit_permissions делегирует SoT;
##                      прежний chmod 640 (антагонист R2) удалён
##           2026-09-01 | P1 fix (F-07 asi-team-vps) — dir traversal для ci-deploy: access ACL
##                      u:ci-deploy:--x на /var/log/platform (primary) / chgrp ci-deploy + chmod
##                      0710 (fallback); 700-каталог блокировал открытие audit.jsonl (Errno 13)
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import os
import pathlib
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from core.internal.shared.file_lock import CI_DEPLOY_USER
from core.internal.shared.subprocess_io import run_subprocess
from core.internal.shared.timeouts import FILE_OP_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_LOG_FILE = "/var/log/platform/audit.jsonl"

# Канонический mode fallback-ветки (P1 fix 2026-08-27): 0660 — owner rw + group rw.
# Группа-владелец = primary-группа главного писателя (ci-deploy); other ---.
_FALLBACK_MODE = 0o660
"""## @invariant Fallback mode (no setfacl) — chgrp <writer> + chmod 0660."""


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

    ▶ ┌tag, status, msg, **extra┐ → ◇ mkdir -p log_dir → ◇ ensure_audit_writable (ACL/0660, first write)
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
    ##   - Sets canonical permissions on first write via _set_audit_permissions →
    ##     ensure_audit_writable (ACL u:ci-deploy:rw primary / chgrp ci-deploy + 0660 fallback,
    ##     P1 fix 2026-08-27; non-root → skip; non-fatal)
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


# region FUNC_ensure_audit_writable
# ⚠️ TRAP[BUG] · 2026-08-27 · P2 · doxygen zero-warnings (DevPlan 097): неэкранированные <file>/<writer>/<dir>
#              в ## @purpose парсились как xml/html-теги → «Unsupported xml/html tag» ×6, суит был красный
# · Symptom: make check MARKER=doxygen-check — 6 «Unsupported xml/html tag» warnings (ensure_audit_writable
#           @purpose ×5 + test_shared_audit_logger.py:560) → FAIL без диффа собственных doxygen-сигналов
# · Root: литералы setfacl-синтаксиса (u:<writer>:rw <file>/<dir>) в doxygen-док-комментариях без esc
# · Fix: экранирование \\<…\\> по канону (образец project_payload_delivery.py) — 6 точек → 0 warnings
# · Prevention: литералы <> в ## @purpose/@io — только с экранированием \\<…\\>; doxygen-check ловит
## @purpose  КАНОНИЧЕСКИЙ SoT прав аудит-файла И его каталога (P1 fix 2026-08-27 + dir 2026-09-01).
##           Целевое состояние: владелец root, запись разрешена root И главному писателю
##           (ci-deploy), ci-deploy имеет traversal (+x) на родительский каталог.
##           PRIMARY (setfacl доступен + euid=0): POSIX ACL
##             setfacl -m u:\\<writer\\>:rw,m::rw \\<file\\>          — named user + mask rw (файл)
##             setfacl -d -m u:\\<writer\\>:rw,m::rw \\<dir\\>        — default ACL на dir (ротации)
##             setfacl -m u:\\<writer\\>:--x \\<dir\\>               — access ACL traversal (dir, F-07)
##           FALLBACK (без setfacl + euid=0): chgrp \\<writer\\> + chmod 0660 (файл)
##           и на КАТАЛОГ: chgrp \\<writer\\> + chmod 0710 (dir, group --x other --- — traversal
##           для группы, TRAP[DECISION] ниже). Non-root → no-op ("skip").
##           Идемпотентна (setfacl -m / chmod — повторный вызов no-op). Никогда не raise.
## @io       ⇥ log_file: str, writer_user: str = CI_DEPLOY_USER → ⎋ str ("acl"|"group"|"skip")
## @complexity O(1) — 2-5 subprocess вызова
## @invariants
##   - euid!=0 → "skip" (dev/receive: доступ уже есть через owner/ACL; chown чужого файла невозможен)
##   - setfacl доступен, но rc!=0 (ФС без ACL) → graceful fallback в group-ветку
##   - writer_user не существует (dev) → chmod 0660 best-effort, WARN (dir не трогается)
##   - dir traversal — non-fatal: failure логируется WARNING, статус возврата не меняется
##   - НИКОГДА не raise (run_subprocess check=False канон converge)
def ensure_audit_writable(log_file: str = DEFAULT_LOG_FILE, writer_user: str = CI_DEPLOY_USER) -> str:
    """Converge audit log permissions to the canonical target state (ACL/0660) — P1 fix 2026-08-27."""
    log_path = pathlib.Path(log_file)
    log_dir = log_path.parent

    if os.geteuid() != 0:
        logger.info(
            "[IMP:7][ensure_audit_writable] non-root euid=%d — permission convergence skipped for %s",
            os.geteuid(),
            log_file,
        )
        return "skip"

    # ── PRIMARY: POSIX ACL через setfacl ──
    if shutil.which("setfacl"):
        acl_r = run_subprocess(
            ["setfacl", "-m", f"u:{writer_user}:rw", "m::rw", str(log_path)],
            timeout=FILE_OP_TIMEOUT,
        )
        if acl_r.returncode == 0:
            if log_dir.is_dir():
                # default ACL на dir — новые файлы ротаций наследуют запись для писателя
                # (default mask rw обязателен: без него маска нового файла = group-биты mode,
                #  что делает named-user entry неэффективной при mode 0644)
                _ = run_subprocess(
                    ["setfacl", "-d", "-m", f"u:{writer_user}:rw", "m::rw", str(log_dir)],
                    timeout=FILE_OP_TIMEOUT,
                )
                # P1 fix 2026-09-01 (F-07): access ACL traversal на КАТАЛОГ. Без +x на родителе
                # rw-ACL файла бесполезна — 700-каталог root:root давал ci-deploy Errno 13.
                # Только +x (--x, БЕЗ r — чтение списка каталога ci-deploy не нужно). mask НЕ
                # задаём явно: setfacl пересчитает её как union(group::, named) и НЕ затрёт
                # group:: (0750 root:adm от R2 сохраняет adm-чтение каталога). Идемпотентно.
                # ⚠️ TRAP[BUG] · 2026-09-01 · P1 · dir traversal (700-каталог блокировал audit)
                # · Symptom: «Cannot write to /var/log/platform/audit.jsonl: [Errno 13] Permission
                #   denied — audit entry dropped» при deploy/rollback через ci-deploy (forced-command receive)
                # · Root: ensure_audit_writable чинил ТОЛЬКО файл (chgrp+0660 / ACL rw), но не КАТАЛОГ —
                #   ci-deploy не имел traversal (+x) через drwx------ root:root, созданный mkdir -p
                #   под umask 077 → открыть файл невозможно несмотря на rw-права самого файла
                # · Fix: при root-записи давать ci-deploy traversal на родителя audit.jsonl —
                #   setfacl -m u:ci-deploy:--x \\<dir\\> (primary) / chgrp ci-deploy + chmod 0710 (fallback)
                # · Prevention: права каталога и файла сходятся в ЕДИНОМ SoT ensure_audit_writable
                _ = run_subprocess(
                    ["setfacl", "-m", f"u:{writer_user}:--x", str(log_dir)],
                    timeout=FILE_OP_TIMEOUT,
                )
            logger.info(
                "[IMP:9][ensure_audit_writable] %s → ACL u:%s:rw + mask rw (setfacl primary); "
                "dir %s → traversal u:%s:--x",
                log_file,
                writer_user,
                log_dir,
                writer_user,
            )
            return "acl"
        logger.warning(
            "[IMP:8][ensure_audit_writable] setfacl failed (rc=%d) for %s — falling back to group branch",
            acl_r.returncode,
            log_file,
        )

    # ── FALLBACK: без setfacl — групповая запись через primary-группу писателя ──
    # 🧐 TRAP[DECISION] · 2026-08-27 · — · Fallback без setfacl: chgrp ci-deploy + chmod 0660 (файл)
    # ·   + chgrp ci-deploy + chmod 0710 (КАТАЛОГ, P1 fix 2026-09-01 — traversal для группы)
    # · Rejected: прежний 0664 root:adm (P1 root cause — групповой write зависел от adm-членства
    # ·   и ломался chmod 0640 от audit_logger при root-записи; adm-читатели теряют group-read);
    # ·   для каталога отвергнут o+x (world-traversal — ослабление безопасности, F-07)
    # · Reason: honest trade-off — ci-deploy получает запись через группу-владельца (файл 0660)
    # ·   и traversal через группу-владельца (каталог 0710, group --x other ---), adm-читатели
    # ·   читают через sudo/root-канон (read-контракт сужен сознательно в fallback)
    # · Rev: если на ноде появится setfacl (пакет acl) — primary-ветка активируется автоматически
    try:
        _ensure_audit_fallback_group(log_path, log_dir, writer_user)
    except (KeyError, ImportError):
        logger.warning(
            "[IMP:7][ensure_audit_writable] writer %r unknown (dev) — chmod 0660 best-effort only",
            writer_user,
        )
    _ = run_subprocess(["chmod", f"{_FALLBACK_MODE:04o}", str(log_path)], timeout=FILE_OP_TIMEOUT)
    logger.info(
        "[IMP:9][ensure_audit_writable] %s → 0660 %s (fallback, no setfacl); dir %s → 0710 %s",
        log_file,
        writer_user,
        log_dir,
        writer_user,
    )
    return "group"


# endregion FUNC_ensure_audit_writable


# region FUNC__ensure_audit_fallback_group
## @purpose  P1 fix 2026-08-27 + 09-01 (F-07): fallback-ветка без setfacl — групповая запись через
##           primary-группу писателя. Файл: chgrp \\<writer\\> (chmod 0660 — единый, в вызывающем
##           после try). КАТАЛОГ: chgrp \\<writer\\> + chmod 0710 (owner rwx, group --x, other ---) —
##           члены группы писателя проходят +x без чтения списка; world-травера НЕТ (не o+x).
##           Non-fatal: rc игнорируется; каталог может отсутствовать (skip). Идемпотентно.
## @io       ⇥ log_path: pathlib.Path, log_dir: pathlib.Path, writer_user: str → ⎋ None
##           ⚡ KeyError/ImportError — writer не существует (dev) → вызывающий логирует WARN
## @complexity O(1) — 2-4 subprocess вызова
def _ensure_audit_fallback_group(log_path: pathlib.Path, log_dir: pathlib.Path, writer_user: str) -> None:
    """Apply group-channel fallback perms (file chgrp + dir traversal chgrp/chmod 0710)."""
    import pwd

    _ = pwd.getpwnam(writer_user)
    chgrp_r = run_subprocess(["chgrp", writer_user, str(log_path)], timeout=FILE_OP_TIMEOUT)
    if chgrp_r.returncode != 0:
        logger.warning(
            "[IMP:8][ensure_audit_writable] chgrp %s failed (rc=%d) — chmod 0660 only",
            writer_user,
            chgrp_r.returncode,
        )
    if log_dir.is_dir():
        _ = run_subprocess(["chgrp", writer_user, str(log_dir)], timeout=FILE_OP_TIMEOUT)
        _ = run_subprocess(["chmod", "0710", str(log_dir)], timeout=FILE_OP_TIMEOUT)


# endregion FUNC__ensure_audit_fallback_group


# region FUNC_audit_permissions_status
## @purpose  Инспекция текущего состояния прав аудит-файла для идемпотентного детекта дрейфа
##           (потребитель — converge R2). Возвращает: "acl" (named-user rw + mask rw через
##           getfacl), "group" (0660 + группа-владелец == writer), "none" (иное), "missing".
## @io       ⇥ log_file: str, writer_user: str = CI_DEPLOY_USER → ⎋ str
## @complexity O(1) — getfacl/stat
## @invariants
##   - getfacl недоступен или rc!=0 → проверка только group-состояния (stat)
##   - Никогда не raise
def audit_permissions_status(log_file: str = DEFAULT_LOG_FILE, writer_user: str = CI_DEPLOY_USER) -> str:
    """Inspect audit log permission state — 'acl' | 'group' | 'none' | 'missing'."""
    log_path = pathlib.Path(log_file)
    if not log_path.is_file():
        return "missing"

    # ACL-детект: named-user entry u:<writer>:rw + mask rw (mask без named-user — не считается)
    if shutil.which("getfacl"):
        g_r = run_subprocess(["getfacl", "-c", str(log_path)], timeout=FILE_OP_TIMEOUT)
        if g_r.returncode == 0:
            out = g_r.stdout
            if f"user:{writer_user}:rw" in out and "mask::rw" in out:
                return "acl"

    # Group-детект: mode 0660 + группа-владелец == writer_user
    try:
        st = log_path.stat()
    except OSError:
        return "none"
    mode = st.st_mode & 0o777
    try:
        import grp

        gname = grp.getgrgid(st.st_gid).gr_name
    except (KeyError, ImportError):
        gname = str(st.st_gid)
    if mode == _FALLBACK_MODE and gname == writer_user:
        return "group"
    return "none"


# endregion FUNC_audit_permissions_status


def _set_audit_permissions(log_file: str) -> None:
    """Converge audit log permissions to the canonical target state — non-fatal (P1 fix 2026-08-27).

    ▶ ┌log_file┐ → ◇ ensure_audit_writable (ACL primary / 0660 fallback / non-root skip) → ⎋ None
    ## @purpose  Делегирует в ensure_audit_writable (единый SoT прав) — прежний chmod 640/chown :adm
    ##            удалён: он сбрасывал ACL-mask/group-write и был антагонистом converge R2.
    ## @complexity O(1)
    """
    try:
        status = ensure_audit_writable(log_file)
        logger.info("[IMP:7][_set_audit_permissions] %s permissions → %s", log_file, status)
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
