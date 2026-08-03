#!/usr/bin/env python3
# GREP_SUMMARY: node-resolver resolve-node-yaml extract-node-host 3-path-search node-yaml-facade resolve host CLI exit-contract shared
# STRUCTURE: ▶ resolve_node_yaml(node_name, platform_root?, projects_dir?) → NodeYaml.resolve (env PLATFORM_ROOT/HOME) → ◇ ConfigNotFoundError → ⎋ path | raise → ▶ extract_node_host(yaml_path) → NodeYaml.get(node.host) → ⎋ str → ◇ CLI: resolve | host → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Python-резолв node.yaml (DevPlan 127 W2, S8/P2-1): чистые функции
##           resolve_node_yaml()/extract_node_host() поверх NodeYaml-фасада (node_yaml/
##           resolve.py, 3-path search) + CLI для shell-фасада core/lib/node-resolver.sh
##           (<100 LOC). Перенос логики из shell-библиотеки node-resolver.sh (215 LOC,
##           из них ~130 LOC документация) — резолв и LDD-логи теперь в Python, тестируемы
##           native pytest без subprocess.
## @scope    Вызывается только через `python3 -m core.internal.shared.node_resolver`
##           (shell-фасад node-resolver.sh: resolve/host subcommands) или импортом чистых
##           функций. Потребители node-resolver.sh: bootstrap.sh, node-update.sh,
##           node-lifecycle.sh, converge.sh, deploy-context.sh, makefiles/deploy.mk.
## @invariants
##   - 3-path search (порядок канона NodeYaml.resolve): {platform_root}/node-configs/{n}/node.yaml
##     → $HOME/projects/*/node-configs/{n}/node.yaml (glob) → /opt/node-configs/{n}/node.yaml
##   - platform_root: аргумент → config_dir (hermetic DI); None → env PLATFORM_ROOT → platform_remote_base()
##   - projects_dir: vestigial (сигнатура-совместимость с прежней shell-библиотекой; glob
##     управляется env $HOME — как было в node_yaml --resolve, byte-compat)
##   - resolve CLI: stdout = РОВНО ОДНА строка (путь) — shell $() потребители; exit 1 = not found
##   - host CLI: stdout = host или пустая строка; exit 0; exit 1 = file missing/parse error
##   - Exit-коды CLI: 0=ok, 1=generic (документированный контракт shell-библиотеки);
##     ConfigNotFoundError/ConfigParseError → читаемая ошибка в stderr + exit 1
##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__
##   - LDD: IMP:9 на успешный резолв/извлечение; IMP:10 на not-found/ошибку
## @rationale Q: Почему shared/node_resolver.py, а не расширение node_yaml_cli.py?
##            A: node_yaml_cli — CLI-слой конкретного фасада (--get/--get-many/...). Резолв
##            ноды — отдельная доменная операция с 5+ shell-потребителями (критерий shared/
##            ≥2 потребителей, shared/AGENTS.md); чистые функции изолированы от argparse,
##            CLI — тонкая обёртка. Фасад node-resolver.sh сохраняет имена функций и
##            LDD-логи (log_imp) — байт-совместимость с потребителями (регрессия
##            tests/test_lib_node_resolver.py зелёная).
## @changes  2026-08-04 | DevPlan 127 W2 — Created (миграция node-resolver.sh, S8/P2-1)
## @see      core/lib/node-resolver.sh (shell-фасад), core/internal/shared/node_yaml/resolve.py,
##           core/internal/shared/node_yaml_cli.py (T6-нормализация — локальная копия _format_cli_value)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
from core.internal.shared.node_yaml import NodeYaml

logger = logging.getLogger(__name__)


# region FUNC__format_cli_value
def _format_cli_value(value: Any) -> str:
    """T6-нормализация скалярного вывода (bool → "true"/"false", числа → str).

    ▶ ┌value┐ → ◇ bool? "true"/"false" → ◇ int/float? str() → ⎋ str(value)

    ## @purpose  Parity с node_yaml_cli._format_cli_value (DevPlan 123 T6) — bool → lowercase
    ##            "true"/"false" (НЕ Python "True"), int/float → десятичная строка. Локальная
    ##            копия (7 LOC): cross-module private import (_format_cli_value) запрещён гейтом
    ##            test_gate_no_private_cross_module_imports (allowlist пуст) — дублирование
    ##            санкционировано как единственный путь без изменения публичного API node_yaml_cli.
    ## @io — ⇥ value: Any → ⎋ str (CLI-безопасное представление)
    ## @complexity — O(1)
    ## @invariants — isinstance(value, bool) проверяется ПЕРЕД (int, float) — bool subclass int
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


# endregion FUNC__format_cli_value


# region FUNC_resolve_node_yaml
def resolve_node_yaml(
    node_name: str | None = None,
    platform_root: str | None = None,
    projects_dir: str | None = None,
) -> str:
    """Резолв node.yaml через 3-path search (NodeYaml.resolve) → абсолютный путь.

    ▶ ┌node_name, platform_root?, projects_dir?┐ → NodeYaml.resolve (env PLATFORM_ROOT/HOME/NODE_NAME)
      → ◇ ConfigNotFoundError → ⎋ path | raise

    ## @purpose  resolve_node_yaml() из node-resolver.sh — 3-path search делегируется
    ##            NodeYaml.resolve (node_yaml/resolve.py, единая точка чтения node.yaml).
    ##            Чистая функция (no subprocess) — native pytest без bash.
    ## @io — ⇥ node_name: Optional[str] (None → env NODE_NAME → hostname, как NodeYaml.resolve);
    ##         platform_root: Optional[str] (аргумент → config_dir; None → env PLATFORM_ROOT);
    ##         projects_dir: Optional[str] — VESTIGIAL (glob управляется env $HOME, byte-compat)
    ##       → ⎋ str — абсолютный путь найденного node.yaml
    ## @complexity — O(P + N) — P=кандидаты 3-path, N=YAML parse (NodeYaml)
    ## @raises — ConfigNotFoundError: node.yaml не найден ни в одном пути (читаемое сообщение)
    ## @invariants
    ##   - Порядок поиска: platform_root → ~/projects glob → /opt (канон NodeYaml.resolve)
    ##   - platform_root передан → env PLATFORM_ROOT ИГНОРИРУЕТСЯ (hermetic DI);
    ##     None → env PLATFORM_ROOT → platform_remote_base() (byte-compat с shell)
    ##   - projects_dir принимается для сигнатурной совместимости, НЕ участвует в поиске
    ##   - stdout-контракт CLI: ровно одна строка (shell $() потребители)
    """
    logger.info("[IMP:8][node-resolver][resolve] Resolving node.yaml for node=%s", node_name)
    resolved = NodeYaml.resolve(node_name=node_name, config_dir=platform_root)
    path = str(resolved._path)
    logger.info("[IMP:9][node-resolver][resolve] Resolved node.yaml: %s", path)
    return path


# endregion FUNC_resolve_node_yaml


# region FUNC_extract_node_host
def extract_node_host(yaml_path: str) -> str:
    """Извлечение node.host из node.yaml (NodeYaml.get) → str.

    ▶ ┌yaml_path┐ → NodeYaml(yaml_path) → ○ get(node.host, default="") → ○ _format_cli_value → ⎋ str

    ## @purpose  extract_node_host() из node-resolver.sh — host-извлечение через единый
    ##            NodeYaml-фасад; нормализация вывода — _format_cli_value (DevPlan 123 T6:
    ##            bool → "true"/"false", числа → str — byte-parity с node_yaml --get).
    ## @io — ⇥ yaml_path: str — абсолютный путь к node.yaml → ⎋ str (host или "")
    ## @complexity — O(N) — YAML parse
    ## @raises — ConfigNotFoundError (файл отсутствует), ConfigParseError (битый YAML)
    ## @invariants
    ##   - Отсутствующий node.host → "" (НЕ ошибка; exit 0 — shell-совместимость)
    ##   - Несуществующий файл/parse-ошибка → исключение → CLI exit 1
    """
    logger.info("[IMP:8][node-resolver][host] Extracting host from: %s", yaml_path)
    node = NodeYaml(yaml_path)
    host = node.get("node.host", default="")
    value = _format_cli_value(host)
    if value:
        logger.info("[IMP:9][node-resolver][host] Extracted host: %s", value)
    else:
        logger.info("[IMP:9][node-resolver][host] No host field in node.yaml: %s (empty output)", yaml_path)
    return value


# endregion FUNC_extract_node_host


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 -m core.internal.shared.node_resolver resolve --node X [--platform-root P]` | `host --file F`.

    ▶ ┌argv┐ → ◇ resolve? → resolve_node_yaml → print(path) 0 | ConfigNotFoundError → stderr + 1
      → ◇ host? → extract_node_host → print(host) 0 | ConfigNotFound/Parse → stderr + 1 → ⎋ exit

    ## @purpose  Интерфейс для shell-фасада node-resolver.sh (DevPlan 127 W2): CLI-обёртка
    ##            чистых функций с байт-совместимым exit-контрактом (0/1) и читаемыми ошибками.
    ## @io — ⇥ argv → ⎋ int (0 = ok, 1 = not found / parse error / file missing)
    ## @invariants
    ##   - resolve: stdout ровно одна строка (путь); not-found → stderr + exit 1
    ##   - host: stdout host|"" ; exit 0; file missing/parse → stderr + exit 1
    ##   - sys.exit НЕ вызывается — main() возвращает int (канон core/AGENTS.md)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="NodeYaml resolver CLI (DevPlan 127 W2)")
    sub = parser.add_subparsers(dest="action", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve node.yaml via 3-path search")
    p_resolve.add_argument("--node", required=True, help="Node name")
    p_resolve.add_argument(
        "--platform-root",
        default=None,
        help="Base config dir (config_dir). Default: env PLATFORM_ROOT → platform_remote_base()",
    )

    p_host = sub.add_parser("host", help="Extract node.host from a node.yaml file")
    p_host.add_argument("--file", required=True, help="Absolute path to node.yaml")

    args = parser.parse_args(argv)

    if args.action == "resolve":
        try:
            path = resolve_node_yaml(node_name=args.node, platform_root=args.platform_root)
        except ConfigNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(path)
        return 0

    if args.action == "host":
        try:
            host = extract_node_host(args.file)
        except ConfigNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except ConfigParseError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(host)
        return 0

    parser.error(f"Unknown action: {args.action}")
    return 1  # unreachable (parser.error exits)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
