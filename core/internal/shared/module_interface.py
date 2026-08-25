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
##      — (success, stderr-output); никогда не raise (OSError → (False, msg); таймаут обрабатывает
##      канон subprocess_io — graceful rc=124)
##   3. Пути paths.sh/module-interface.sh резолвятся относительно модуля (core/lib/), НЕ из env
##   4. args экранируются shlex.quote (безопасная передача строк с пробелами)
##   5. Модуль не импортирует bootstrap/deploy/* (слой shared — только вниз)
##   6. REF-0103: исполнение через subprocess_io streaming-канон (Popen+start_new_session+killpg
##      при таймауте) — прежний subprocess.run убивал ТОЛЬКО bash; внуки (docker/healthcheck-скрипты)
##      оставались орфанами и держали ресурсы после «завершения» invoke.
## @rationale C5 (DevPlan 118): две идентичные сборки `source paths.sh && source module-interface.sh &&
##            invoke_module_interface ...` — правка обёртки требовала 2 правок с риском расхождения
##            (таймауты/семантика возврата). Единый invoke() в shared/ устраняет дубль; B8 wire
##            module-hooks строится поверх этого канона.
## @changes  2026-08-02 | DevPlan 118 C5 — Created (единая bash-обёртка invoke_module_interface)
##           2026-08-02 | DevPlan 119 D4 — +dispatch()/CLI invoke (dual-SoT устранён):
##                      module-interface.sh → тонкий фасад; validate+dispatch логика здесь
##           2026-08-25 | REF-0103 — invoke/_run_module_script переведены на
##                      run_subprocess_streaming (killpg всей группы процессов при таймауте)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from core.internal.shared.subprocess_io import run_subprocess_streaming
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
##   - rc==0 → (True, stderr); rc!=0 → (False, stderr); OSError → (False, str(exc));
##     таймаут — graceful rc=124 через subprocess_io canon (killpg группы, REF-0103)
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
    # REF-0103: killpg через subprocess_io canon — start_new_session + os.killpg(SIGKILL) всей
    # группы при таймауте (stream=False: tee-вывод не нужен; heartbeat=0: без heartbeat-шума).
    # Таймаут больше НЕ бросает TimeoutExpired наверх — канон возвращает graceful rc=124.
    try:
        result = run_subprocess_streaming(["bash", "-c", bash_cmd], timeout=timeout, stream=False, heartbeat=0)
    except OSError as exc:
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


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 119 D4 — full dispatch (dual-SoT устранён): module-interface.sh становится
# тонким фасадом, ВСЯ логика (validate interfaces → dispatch) живёт здесь.
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_resolve_module_dir
def resolve_module_dir(module_name: str, modules_dir: str | None = None) -> Path:
    """Директория модуля (эквивалент ${PATHS_MODULES_DIR}/${module}).

    ## @purpose — D4 (DevPlan 119): единый резолв модульной директории для dispatch.
    ## @io — ⇥ module_name: str, modules_dir: str | None (default core/modules) → ⎋ Path
    ## @complexity O(1)
    """
    if modules_dir is None:
        modules_dir = str(Path(__file__).resolve().parent.parent.parent / "modules")
    return Path(modules_dir) / module_name


# endregion FUNC_resolve_module_dir


# region FUNC__read_module_yaml
def _read_module_yaml(module_yaml: Path) -> dict[str, object]:
    """Читает module.yaml (интерфейсы/hooks) — yaml.safe_load канон (как env_requires._load_yaml_file).

    ## @purpose — D4: единая точка чтения module.yaml для validate/dispatch.
    ## @io — ⇥ module_yaml: Path → ⎋ dict (пустой при ошибке — graceful, как yaml_get_list)
    """
    try:
        with module_yaml.open(encoding="utf-8") as f:
            # yaml.safe_load → Any; object-граница — isinstance-check ниже (W11)
            data: object = cast(object, yaml.safe_load(f))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[IMP:7][module_interface][yaml] Cannot parse %s: %s", module_yaml, exc)
        return {}


# endregion FUNC__read_module_yaml


# region FUNC__registered_interfaces
def _registered_interfaces(module_yaml: Path) -> list[str]:
    """Список интерфейсов из module.yaml#interfaces (пустой при отсутствии поля).

    ## @purpose — D4: эквивалент shell _invoke_validate_interface (yaml_get_list).
    ## @io — ⇥ module_yaml: Path → ⎋ list[str]
    ## @complexity O(n) — n = интерфейсы
    """
    data = _read_module_yaml(module_yaml)
    interfaces = data.get("interfaces")
    if not isinstance(interfaces, list):
        return []
    return [str(i) for i in interfaces]


# endregion FUNC__registered_interfaces


# region FUNC__run_module_script
def _run_module_script(
    script: Path,
    args: tuple[str, ...],
    timeout: int,
) -> tuple[int, str]:
    """Выполнить скрипт модуля через `bash <script> [args...]`.

    ## @purpose — D4: диспетчеризация на скрипт модуля (эквивалент shell `bash "$script" "$@"`).
    ## @io — ⇥ script: Path, args, timeout → ⎋ (rc, stderr) — никогда не raise (таймаут → rc 124 каноном)
    ## @complexity O(1) — single bash subprocess
    """
    if not script.is_file():
        logger.info("[IMP:8][module_interface][dispatch] Script not found — skipping: %s", script)
        return 0, ""
    # REF-0103: killpg через subprocess_io canon (паритет с invoke) — внуки скрипта
    # (docker/psql-процессы healthcheck.sh) умирают вместе с группой при таймауте.
    try:
        result = run_subprocess_streaming(["bash", str(script), *args], timeout=timeout, stream=False, heartbeat=0)
    except OSError as exc:
        logger.warning("[IMP:7][module_interface][dispatch] Cannot run %s: %s", script, exc)
        return 1, str(exc)
    if result.returncode != 0:
        logger.info(
            "[IMP:8][module_interface][dispatch] %s exit=%d: %s",
            script.name,
            result.returncode,
            result.stderr.strip()[:200],
        )
    else:
        logger.info("[IMP:9][module_interface][dispatch] %s OK", script.name)
    return result.returncode, result.stderr


# endregion FUNC__run_module_script


# region FUNC_dispatch
def dispatch(
    module_name: str,
    interface: str,
    *args: str,
    timeout: int = COMPOSE_UP_TIMEOUT,
    modules_dir: str | None = None,
) -> tuple[int, str]:
    """Полный dispatch модульного интерфейса (DevPlan 119 D4) — единый канон.

    ▶ ┌module + interface + args┐ → ◇ module.yaml? rc 2 │ ◇ interface ∈ interfaces? rc 0 (skip)
      → ◇ dispatch: healthcheck/install/deploy-hook/remove-hook → bash script | unknown → rc 0
      → ⎋ (rc, stderr)

    ## @purpose — Замена shell-логики module-interface.sh (invoke_module_interface +
    ##            _invoke_validate_interface + _invoke_dispatch_*) — dual-SoT устранён.
    ## @io — ⇥ module_name: str, interface: str, *args: str, timeout: int, modules_dir: str | None
    ##           ⎋ tuple[int, str] — (rc, stderr); rc: 0=success/skip, 1=script failed, 2=invalid config
    ## @complexity O(n) — n = зарегистрированные интерфейсы (validate) + 1 subprocess
    ## @invariants
    ##   - module.yaml отсутствует → rc 2 (invalid config, как shell)
    ##   - interface не зарегистрирован в module.yaml#interfaces → rc 0 (graceful skip)
    ##   - unknown interface (в interfaces, вне case) → rc 0 (skip)
    ##   - healthcheck/install/deploy-hook/remove-hook → bash script; script отсутствует → rc 0
    ##   - deploy-hook/remove-hook читают hooks.on_project_deploy/on_project_remove из module.yaml
    ##   - Никогда не raise — OSError → rc 1; таймаут → graceful rc=124 (killpg canon, REF-0103)
    """
    module_dir = resolve_module_dir(module_name, modules_dir)
    module_yaml = module_dir / "module.yaml"

    # ── Module exists? ──
    if not module_yaml.is_file():
        logger.info(
            "[IMP:9][module_interface][invoke] INVALID: module.yaml not found for '%s' at %s", module_name, module_yaml
        )
        return 2, f"module.yaml not found for '{module_name}'"

    # ── Validate interface is registered (graceful skip if not) ──
    interfaces = _registered_interfaces(module_yaml)
    if interface not in interfaces:
        logger.info(
            "[IMP:8][module_interface][skip] Interface '%s' not registered for module '%s' — skipping",
            interface,
            module_name,
        )
        return 0, ""

    # ── Dispatch ──
    logger.info("[IMP:8][module_interface][invoke] Invoking module=%s interface=%s", module_name, interface)
    if interface == "healthcheck":
        return _run_module_script(module_dir / "healthcheck.sh", args, timeout)
    if interface == "install":
        return _run_module_script(module_dir / "install.sh", (), timeout)
    if interface in {"deploy-hook", "remove-hook"}:
        field = "hooks.on_project_deploy" if interface == "deploy-hook" else "hooks.on_project_remove"
        data = _read_module_yaml(module_yaml)
        # hooks — dict-секция module.yaml; hook_path — имя скрипта (object-граница, W11)
        hooks = data.get("hooks")
        hook_path: object | None = hooks.get(field.split(".")[1]) if isinstance(hooks, dict) else None
        if not hook_path:
            logger.info("[IMP:8][module_interface][dispatch] Hook field '%s' not found — skipping", field)
            return 0, ""
        return _run_module_script(module_dir / str(hook_path), args, timeout)
    # Unknown interface (зарегистрирован, но вне канона) — graceful skip
    logger.info(
        "[IMP:9][module_interface][invoke] SKIP: Unknown interface '%s' for module '%s'", interface, module_name
    )
    return 0, ""


# endregion FUNC_dispatch


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 -m core.internal.shared.module_interface invoke <module> <interface> [args...]`.

    ▶ ┌argv┐ → ○ invoke <module> <interface> [args...] → ○ dispatch → ⎋ exit rc (0/1/2)

    ## @purpose — Интерфейс для тонкого фасада module-interface.sh (DevPlan 119 D4):
    ##            shell-библиотека вызывает `python3 -m core.internal.shared.module_interface invoke "$@"`.
    ## @io — ⇥ argv → ⎋ int (0=success/skip, 1=script failed, 2=invalid config)
    ## @invariants — exit-code канон shell invoke_module_interface (0/1/2) сохранён байт-в-байт
    """
    parser = argparse.ArgumentParser(description="Module interface dispatch (DevPlan 119 D4)")
    sub = parser.add_subparsers(dest="command", required=True)
    invoke_p = sub.add_parser("invoke", help="Invoke <module> <interface> [args...]")
    invoke_p.add_argument("module", help="Module name (directory under core/modules)")
    invoke_p.add_argument("interface", help="Interface name (healthcheck/install/deploy-hook/remove-hook)")
    invoke_p.add_argument("args", nargs="*", help="Additional arguments passed to the module script")
    invoke_p.add_argument("--modules-dir", help="Override modules dir (default core/modules; для тестов)")

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    @dataclass
    class _CliArgs:
        command: str
        module: str
        interface: str
        args: list[str]
        modules_dir: str

    typed_args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    if typed_args.command == "invoke":
        rc, _output = dispatch(
            typed_args.module,
            typed_args.interface,
            *typed_args.args,
            modules_dir=typed_args.modules_dir,
        )
        return rc
    return 2  # unreachable (argparse required)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
