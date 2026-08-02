#!/usr/bin/env python3
# GREP_SUMMARY: module-interface, invoke, bash-facade, module-hooks, shared, module-interface-sh, paths-sh
# STRUCTURE: ▶ ┌module + interface + *args┐ → ○ source paths.sh && module-interface.sh → ⚡ invoke_module_interface → ◇ rc==0? → ⎋ (True, stderr) │ ⎋ (False, stderr)
# region MODULE_CONTRACT
## @purpose  Единая bash-обёртка invoke_module_interface (DevPlan 118 C5) — единственный Python-канал
##           вызова модульных интерфейсов (healthcheck/install/deploy-hook/...) через shell-функцию
##           core/lib/module-interface.sh. Дедупликация двух идентичных bash -c сборок:
##           docker_orchestrator._invoke_healthcheck_full и deploy_orchestrator._invoke_module_interface
##           (различались таймаутами/возвратами). **Вход для B8 (Вариант 1 — wire module-hooks).**
## @scope    Импортируется docker_orchestrator.py и deploy_orchestrator.py (2 потребителя — критерий
##           shared/, AC-C5). Invoke через subprocess bash — НАМЕРЕННЫЙ (D4: shell-функция
##           module-interface.sh — тонкий слой поверх module.yaml#interfaces контракта).
## @invariants
##   1. bash -c: source paths.sh && source module-interface.sh && invoke_module_interface '<m>' '<i>' [args...]
##   2. Сигнатура: invoke(module_name, interface, *args, timeout=COMPOSE_UP_TIMEOUT) → tuple[bool, str]
##      — (success, stderr-output); никогда не raise (TimeoutExpired/OSError → (False, msg))
##   3. Пути paths.sh/module-interface.sh резолвятся относительно модуля (core/lib/), НЕ из env
##   4. args экранируются shlex.quote (безопасная передача строк с пробелами)
##   5. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
## @rationale C5 (DevPlan 118): две идентичные сборки `source paths.sh && source module-interface.sh &&
##            invoke_module_interface ...` — правка обёртки требовала 2 правок с риском расхождения
##            (таймауты/семантика возврата). Единый invoke() в shared/ устраняет дубль; B8 wire
##            module-hooks строится поверх этого канала.
## @changes  2026-08-02 | DevPlan 118 C5 — Created (единая bash-обёртка invoke_module_interface)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

logger = logging.getLogger(__name__)

# ── Пути shell-фасадов (core/lib/) — резолв относительно этого модуля ──
_LIB_DIR = Path(__file__).resolve().parent.parent.parent / "lib"
_PATHS_SH = _LIB_DIR / "paths.sh"
_MODULE_INTERFACE_SH = _LIB_DIR / "module-interface.sh"


# region FUNC_invoke
## @purpose  Вызвать интерфейс модуля через shell-функцию invoke_module_interface (DevPlan 118 C5).
## @io       ⇥ module_name: str; interface: str ("healthcheck"/"install"/...); *args: str;
##              timeout: int (default COMPOSE_UP_TIMEOUT=180)
##           ⎋ tuple[bool, str] — (success, stderr-output) — НИКОГДА не raise
## @complexity O(1) — single bash subprocess
## @invariants
##   - bash -c собирается из _PATHS_SH/_MODULE_INTERFACE_SH (source paths + module-interface)
##   - args экранируются shlex.quote (никакого инъекционного пробела в команду)
##   - rc==0 → (True, stderr); rc!=0 → (False, stderr); TimeoutExpired/OSError → (False, str(exc))
##   - timeout — канон shared/timeouts (потребитель передаёт свой: HEALTHCHECK_POLL_TIMEOUT / COMPOSE_UP_TIMEOUT)
def invoke(
    module_name: str,
    interface: str,
    *args: str,
    timeout: int = COMPOSE_UP_TIMEOUT,
) -> tuple[bool, str]:
    """Invoke a module interface via the bash facade (module-interface.sh, C5).

    ▶ ┌module + interface + args┐ → ○ build bash_cmd (source ×2 + invoke_module_interface) →
      → ⚡ subprocess.run(["bash","-c",cmd], capture, text, timeout) → ◇ rc==0? → ⎋ (True, stderr) │ (False, stderr)
    """
    bash_cmd = (
        f"source '{_PATHS_SH}' && "
        f"source '{_MODULE_INTERFACE_SH}' && "
        f"invoke_module_interface '{module_name}' '{interface}'"
    )
    if args:
        bash_cmd += " " + " ".join(shlex.quote(a) for a in args)
    logger.info("[IMP:8][module_interface][invoke] %s %s (timeout=%ds)", module_name, interface, timeout)
    try:
        result = subprocess.run(
            ["bash", "-c", bash_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("[IMP:7][module_interface][error] %s %s error: %s", module_name, interface, exc)
        return False, str(exc)
    if result.returncode != 0:
        logger.info(
            "[IMP:8][module_interface][fail] %s %s exit=%d: %s",
            module_name,
            interface,
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False, result.stderr
    logger.info("[IMP:9][module_interface][done] %s %s OK", module_name, interface)
    return True, result.stderr


# endregion FUNC_invoke
