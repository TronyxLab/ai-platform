#!/usr/bin/env python3
# GREP_SUMMARY: hermes-workflow-main, __main__, cli-entrypoint, python3 -m, handle-hermes-agent
# STRUCTURE: ▶ python3 -m core.internal.bootstrap.deploy.hermes_workflow → __main__.py → handle_hermes_agent CLI (module_dir/module_name/-f) → ◇ ok? → ⎋ sys.exit(main())
# region MODULE_CONTRACT
## @purpose  CLI entrypoint для `python3 -m core.internal.bootstrap.deploy.hermes_workflow` (T3.7:
##           -m запуск сохранён при split hermes_workflow.py → пакет). Ручной pre-deploy-прогон
##           hermes-agent image check/build: module_dir + module_name (+ опциональный -f compose-файл).
## @scope    Только прямой CLI-запуск. Не импортируется при `import core.internal.bootstrap.deploy.hermes_workflow`.
## @invariants
##   1. Тонкий фасад — делегирует в пакетный handle_hermes_agent (wrapper с _shared_* fallback)
##   2. main() -> int контракт core/AGENTS.md: sys.exit только в __main__-блоке
##   3. exit 0 = образы готовы/собраны, exit 1 = фатальный сбой (config fail / build fail)
## @rationale Прежний модуль CLI не имел (библиотечная функция); __main__.py добавлен при конвертации
##            в пакет (node_yaml/__main__.py прецедент) — минимальный аргументный контракт зеркалит
##            сигнатуру handle_hermes_agent(compose_args, module_dir, module_name).
## @changes  2026-08-22 | T3.7 simplify — создан при split (пакет hermes_workflow/)
# endregion MODULE_CONTRACT

import argparse
import logging
import sys

from core.internal.bootstrap.deploy.hermes_workflow import handle_hermes_agent

logger = logging.getLogger(__name__)


# region FUNC_main
## @purpose  CLI main: разбор аргументов и вызов handle_hermes_agent; exit-контракт 0/1.
## @io       ⇥ argv: list[str] | None → ⎋ int (0 = ok, 1 = fatal)
## @complexity 1 — argparse + один вызов workflow
## @invariants
##   - module_dir/module_name — позиционные; -f/--compose-file → compose_args=["-f", path]
##   - IMP-логи — stderr (logger), stdout чист (exit-контракт для shell-потребителей)
def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    class _Args(argparse.Namespace):
        """Typed namespace (W11): аннотации без значений — parse_args(namespace=...) заполняет."""

        module_dir: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills
        module_name: str  # pyright: ignore[reportUninitializedInstanceVariable]
        compose_file: str | None  # pyright: ignore[reportUninitializedInstanceVariable]

    parser = argparse.ArgumentParser(description="Hermes-agent pre-deploy image check/build")
    parser.add_argument("module_dir", help="Родительский каталог модуля (core/modules)")
    parser.add_argument("module_name", help="Имя модуля (hermes-agent)")
    parser.add_argument("-f", "--compose-file", help="Явный compose-файл (→ compose_args=[-f, path])")
    args = parser.parse_args(argv, namespace=_Args())
    compose_args: list[str] = ["-f", args.compose_file] if args.compose_file else []
    logger.info(
        "[IMP:7][main][start] hermes pre-deploy check: module_dir=%s module_name=%s", args.module_dir, args.module_name
    )
    ok = handle_hermes_agent(compose_args, args.module_dir, args.module_name)
    logger.info("[IMP:9][main][result] %s", "ready" if ok else "failed")
    return 0 if ok else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
