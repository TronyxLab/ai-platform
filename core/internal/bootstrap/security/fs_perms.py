#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S6 file-perms world-writable world-readable secrets critical-paths sudoers.d age ci-deploy-ssh find
# STRUCTURE: ▶ /opt/platform exists? → ◇ no → WARN (not deployed) → ⚡ find -perm -0002 (files) + find /004 (secrets) + ○ критичные пути (T10.8) → ◇ problems → FAIL / PASS → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  S6: локальные привилегии (DevPlan 134 L2, W10 T10.8/S-10): world-writable файлы
##           в /opt/platform (вектор локального повышения привилегий) + world-readable файлы
##           в /opt/platform/secrets (age-ключи/пароли) + world-writable НА КРИТИЧНЫХ ПУТЯХ вне
##           платформы: ~ci-deploy/.ssh, /etc/sudoers.d, /var/log/platform, /etc/age.
##           Извлечено из монолита security_posture.py (план 170 W6-D1).
## @scope    Вызывается run_all_checks (run.py) и напрямую (DI-тесты). Импортирует _shared
##           (CheckResult/STATUS/_probe/лимиты) + shared/deploy_paths — циклических зависимостей нет.
## @invariants
##   - Отсутствие /opt/platform → WARN (нода не развёрнута — не security-ошибка)
##   - Критичные пути: world-writable (perm -0002) — файл И директория (вектор подмены
##     authorized_keys / sudoers-инъекции / audit-тампера); отсутствие пути → skip (graceful)
##   - /etc/age — non-canonical (DevPlan 140 W4): файл допустим ТОЛЬКО при restore-first;
##     проверка world-writable остаётся (fallback-файл, существуя, обязан быть защищён)
##   - subprocess через _probe; таймаут CONVERGE_DOCKER_TIMEOUT (W1-A1 план 170)
## @rationale Разделение по бизнес-домену: файловая постура — отдельный модуль (find-пробы
##            дерева + критичные пути вне /opt/platform — разные SoT/охваты).
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S6, 1:1 тела)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT

from ._shared import AUDIT_DIR_LIST_MAX, AUDIT_LIST_MAX, STATUS_FAIL, STATUS_PASS, STATUS_WARN, CheckResult
from ._shared import probe as _probe

logger = logging.getLogger(__name__)

# DevPlan 119 B2/B3 канон: /opt/platform литерал запрещён (гейт timeout_literals) —
# platform_remote_base() (PLATFORM_REMOTE_BASE → /opt/platform, PLATFORM_ROOT исключён, RC 121)
PLATFORM_BASE = str(platform_remote_base())


# region FUNC__check_critical_paths_world_writable
## @purpose  Проба world-writable (perm -0002, файлы И директории) по критичным путям (W10 T10.8):
##           ~ci-deploy/.ssh (подмена authorized_keys), /etc/sudoers.d (sudoers-инъекция),
##           /var/log/platform (audit-тампер), /etc/age (AGE-ключ). Отсутствующий путь → skip.
## @io       ⇥ probe: Callable | None (lazy default _probe), path_exists: Callable | None
##              (lazy default os.path.exists — E3 DI) → ⎋ list[str] — найденные нарушения (пусто = чисто)
## @complexity O(P) — P = критичных путей, каждая find-проба O(дерево пути)
def _check_critical_paths_world_writable(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    path_exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Find world-writable files/dirs under critical security paths (T10.8, S-10)."""
    probe = probe or _probe
    path_exists = path_exists or os.path.exists
    problems: list[str] = []
    ci_deploy_ssh = os.path.expanduser("~ci-deploy/.ssh")
    # /etc/age — non-canonical (DevPlan 140 W4): файл допустим ТОЛЬКО при restore-first
    # (ручной перенос ключа оператором при восстановлении ноды); канон — env → tmpfs
    # decrypt-only (S-13). Проверка world-writable остаётся: fallback-файл, существуя,
    # обязан быть защищён (0600) — пермишн-контроль не ослабляется.
    for path in (ci_deploy_ssh, "/etc/sudoers.d", "/var/log/platform", "/etc/age"):
        if not path_exists(path):
            logger.info("[IMP:8][posture][S6] critical path absent — skipped: %s", path)
            continue
        ww = probe(["find", path, "-perm", "-0002"], timeout=CONVERGE_DOCKER_TIMEOUT)
        if ww.returncode == 0:
            hits = [ln for ln in str(getattr(ww, "stdout", "")).splitlines() if ln.strip()]
            if hits:
                problems.append(
                    f"world-writable under {path}: {', '.join(hits[:AUDIT_DIR_LIST_MAX])}"
                    + ("..." if len(hits) > AUDIT_DIR_LIST_MAX else "")
                )
    return problems


# endregion FUNC__check_critical_paths_world_writable


# region FUNC_check_file_perms
## @purpose  S6: локальные привилегии — world-writable файлы в /opt/platform (вектор локального
##           повышения привилегий) + world-readable файлы в /opt/platform/secrets (age-ключи/пароли)
##           + world-writable НА КРИТИЧНЫХ ПУТЯХ вне платформы (W10 T10.8, S-10):
##           ~ci-deploy/.ssh, /etc/sudoers.d, /var/log/platform, /etc/age (файлы и директории).
## @io       ⇥ probe: Callable | None (lazy default _probe), paths: Mapping | None
##              (override PLATFORM_BASE — E3 DI), path_exists: Callable | None (lazy default
##              os.path.exists — критичные пути вне платформы) → ⎋ CheckResult
## @complexity O(n) — find-пробы по дереву платформы + критические пути
## @invariants  Отсутствие /opt/platform → WARN (нода не развёрнута — не security-ошибка)
##              Критичные пути: world-writable (perm -0002) — файл И директория (вектор
##              подмены authorized_keys / sudoers-инъекции / audit-тампера); отсутствие пути → skip
def check_file_perms(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
    path_exists: Callable[[str], bool] | None = None,
) -> CheckResult:
    """S6: file permissions — no world-writable files, secrets not world-readable, critical paths safe."""
    probe = probe or _probe
    paths_ = paths or {}
    platform_base_str = paths_.get("PLATFORM_BASE") or PLATFORM_BASE
    base = Path(platform_base_str)
    if not base.is_dir():
        return CheckResult("S6", STATUS_WARN, f"{platform_base_str} missing — platform not deployed")
    problems: list[str] = []
    ww = probe(["find", platform_base_str, "-type", "f", "-perm", "-0002"], timeout=CONVERGE_DOCKER_TIMEOUT)
    if ww.returncode == 0:
        writable = [ln for ln in str(getattr(ww, "stdout", "")).splitlines() if ln.strip()]
        if writable:
            problems.append(
                f"world-writable files: {', '.join(writable[:AUDIT_LIST_MAX])}"
                + ("..." if len(writable) > AUDIT_LIST_MAX else "")
            )
    secrets_dir = base / "secrets"
    if secrets_dir.is_dir():
        wr = probe(["find", str(secrets_dir), "-type", "f", "-perm", "/004"], timeout=CONVERGE_DOCKER_TIMEOUT)
        if wr.returncode == 0:
            readable = [ln for ln in str(getattr(wr, "stdout", "")).splitlines() if ln.strip()]
            if readable:
                problems.append(
                    f"world-readable secrets: {', '.join(readable[:AUDIT_LIST_MAX])}"
                    + ("..." if len(readable) > AUDIT_LIST_MAX else "")
                )
    # W10 T10.8 (S-10): критические пути вне /opt/platform — world-writable файл ИЛИ директория
    # = локальная эскалация (подмена authorized_keys / sudoers.d / audit-журнала / AGE-ключа).
    problems.extend(_check_critical_paths_world_writable(probe=probe, path_exists=path_exists))
    if problems:
        return CheckResult("S6", STATUS_FAIL, "; ".join(problems))
    logger.info("[IMP:9][posture][S6] File permissions clean (incl. critical paths)")
    return CheckResult("S6", STATUS_PASS, "no world-writable files, secrets not world-readable, critical paths safe")


# endregion FUNC_check_file_perms
