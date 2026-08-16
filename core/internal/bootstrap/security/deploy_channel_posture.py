#!/usr/bin/env python3
# GREP_SUMMARY: security-posture S7 forced-command deploy-channel authorized_keys orchestrator_cli dispatch restrict perms 0600 owner ci-deploy
# STRUCTURE: ▶ read ~ci-deploy/.ssh/authorized_keys → ◇ missing/readable? → FAIL → ○ stat (perms 0600 + owner ci-deploy) → ○ per-line scan (command="...dispatch",restrict) → ◇ violations → FAIL / PASS → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  S7: целостность forced-command канала деплоя (DevPlan 134 L2, W10 T10.3/S-4):
##           КАЖДАЯ строка ci-deploy authorized_keys содержит command="...orchestrator_cli dispatch",
##           restrict (ЛЮБАЯ строка без канонического prefix = открытый SSH-канал — root-экспозиция
##           деплоя) + perms 0600 и owner ci-deploy (ключевой файл не world-readable и не подменён).
##           Извлечено из монолита security_posture.py (план 170 W6-D1).
## @scope    Вызывается run_all_checks (run.py) и напрямую (DI-тесты). Импортирует только _shared
##           (CheckResult/STATUS/_probe) — циклических зависимостей нет.
## @invariants
##   - Сверка с каноном phases/system.py φ2 (DevPlan 116 B1 + волна 117 D1):
##     command="cd {base} && PYTHONPATH={base} python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict
##   - Пустые строки и комментарии (начинающиеся с #) — не ключи — пропускаются
##   - Файл отсутствует → FAIL; нечитаем → FAIL; perms != 0600 → FAIL; owner != ci-deploy → FAIL
##   - LDD: PASS-лог IMP:9 с маркером S7 (контракт теста test_full_run_logs_imp9)
## @rationale Разделение по бизнес-домену: канал деплоя (SSH forced-command) — отдельный модуль
##            от файловой постуры (S6): разные SoT (authorized_keys vs find-дерево), разные
##            триггеры дрейфа (правки ключей оператором vs произвольные файлы).
## @changes 2026-08-15 | план 170 W6-D1 — извлечено из security_posture.py (S7, 1:1 тела)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pwd  # W11-G3: тип struct_passwd для DI-аннотации getpwuid

from ._shared import STATUS_FAIL, STATUS_PASS, CheckResult
from ._shared import probe as _probe

logger = logging.getLogger(__name__)

# Канон cli.py:261 — expanduser("~ci-deploy") вместо хардкода /home/ci-deploy (гейт no_hardcoded_local_paths)
CI_DEPLOY_AUTHORIZED_KEYS = os.path.expanduser("~ci-deploy/.ssh/authorized_keys")
# S7: каноническая маска authorized_keys (0600) — владелец-чтение/запись, никаких других прав
AUTHORIZED_KEYS_MODE: int = 0o600


# region FUNC_check_forced_command
## @purpose  S7: целостность forced-command канала деплоя — КАЖДАЯ строка ci-deploy authorized_keys
##           содержит command="...orchestrator_cli dispatch",restrict (W10 T10.3/S-4): ЛЮБАЯ строка
##           без канонического prefix = открытый SSH-канал (root-экспозиция деплоя).
##           + проверка perms 0600 и owner ci-deploy (authorized_keys не должен быть world-readable
##           или подменён другим владельцем).
## @io       ⇥ probe: Callable | None (lazy default _probe), paths: Mapping | None
##              (override CI_DEPLOY_AUTHORIZED_KEYS — E3 DI),
##              getpwuid: Callable | None (lazy default pwd.getpwuid — E3 DI, owner-резолв)
##              → ⎋ CheckResult
## @complexity O(1) — одно чтение файла + stat
## @invariants  Сверка с каноном phases/system.py φ2 (DevPlan 116 B1 + волна 117 D1):
##              command="cd {base} && PYTHONPATH={base} python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict
##              Пустые строки и комментарии (начинающиеся с #) — не ключи — пропускаются
##              Файл отсутствует → FAIL; нечитаем → FAIL; perms != 0600 → FAIL; owner != ci-deploy → FAIL
def check_forced_command(
    *,
    probe: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    paths: Mapping[str, str] | None = None,
    getpwuid: Callable[[int], pwd.struct_passwd] | None = None,  # DI: pwd.getpwuid → struct_passwd (T10.3)
) -> CheckResult:
    """S7: ci-deploy forced-command dispatch intact per-line + perms 0600/owner (T10.3)."""
    probe = probe or _probe  # DI-канал сохранён (сигнатура run_all_checks); S7 — file I/O + stat, без subprocess
    paths_ = paths or {}
    keys_file_str = paths_.get("CI_DEPLOY_AUTHORIZED_KEYS") or CI_DEPLOY_AUTHORIZED_KEYS
    keys_file = Path(keys_file_str)
    if not keys_file.is_file():
        return CheckResult("S7", STATUS_FAIL, f"{keys_file_str} missing")
    try:
        lines = keys_file.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CheckResult("S7", STATUS_FAIL, f"cannot read authorized_keys: {e}")
    # ── Perms/owner (W10 T10.3) ──
    # ruff: ignore[PLW0717] — try мутирует параметр функции без возврата — извлечение теряет мутацию
    try:
        st = keys_file.stat()
        mode = st.st_mode & 0o777
        if mode != AUTHORIZED_KEYS_MODE:
            return CheckResult("S7", STATUS_FAIL, f"authorized_keys mode {oct(mode)} != 0600 (world-readable key file)")
        if getpwuid is None:
            import pwd

            getpwuid = pwd.getpwuid
        owner = getpwuid(st.st_uid).pw_name
        if owner != "ci-deploy":
            return CheckResult("S7", STATUS_FAIL, f"authorized_keys owner '{owner}' != ci-deploy")
    except (KeyError, OSError) as e:
        return CheckResult("S7", STATUS_FAIL, f"cannot stat authorized_keys: {e}")
    # ── Per-line forced-command (W10 T10.3): ЛЮБАЯ строка без канона = FAIL ──
    violations: list[str] = []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith('command="') and "orchestrator_cli dispatch" in stripped and "restrict" in stripped:
            continue
        violations.append(f"line {idx}: {stripped[:60]}")
    if violations:
        return CheckResult(
            "S7", STATUS_FAIL, f"{len(violations)} line(s) WITHOUT forced-command prefix: {'; '.join(violations[:3])}"
        )
    logger.info("[IMP:9][posture][S7] Forced-command dispatch intact (all lines, perms 0600, owner ci-deploy)")
    return CheckResult(
        "S7", STATUS_PASS, "ci-deploy key restricted to orchestrator_cli dispatch (all lines, perms 0600)"
    )


# endregion FUNC_check_forced_command
