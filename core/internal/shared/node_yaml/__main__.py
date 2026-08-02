#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-main, __main__, cli-entrypoint, python3 -m, 119-H
# STRUCTURE: ▶ python3 -m core.internal.shared.node_yaml → __main__.py → ◇ lazy import node_yaml_cli.main → ⎋ sys.exit(main())
# region MODULE_CONTRACT
## @purpose  CLI entrypoint для `python3 -m core.internal.shared.node_yaml` (DevPlan 119 H1).
##           Делегирует в node_yaml_cli.main() — CLI-логика живёт отдельно (DevPlan 117 G T51).
## @scope    Только прямой CLI-запуск. Не импортируется при `import core.internal.shared.node_yaml`
##           (агрегатор re-export'ит CLI-символы лениво через PEP 562 __getattr__).
## @invariants
##   1. Тонкий фасад: 3 строки, никакой бизнес-логики.
##   2. `python3 -m core.internal.shared.node_yaml --get node.host` работает (AC-G5, CLI-контракт).
## @rationale DevPlan 119 H1: node_yaml.py (файл) конвертирован в пакет node_yaml/ (паттерн E3
##            phases → phases/). Для пакета `python3 -m` ищет __main__.py — перенесено из
##            `if __name__ == "__main__"` монолита.
## @changes 2026-08-03 · DevPlan 119 H1 — создан (вынос из node_yaml.py __main__ блока)
# endregion MODULE_CONTRACT

import sys


def main() -> int:
    """Delegate to node_yaml_cli.main() (lazy import — CLI not loaded at package import).

    ## @purpose — Единственная точка входа для python3 -m core.internal.shared.node_yaml.
    ## @io — ⇥ sys.argv → ⎋ sys.exit(main())
    ## @complexity — O(1) + CLI dispatch
    """
    # Lazy import — node_yaml_cli загружается только при прямом CLI-запуске.
    from core.internal.shared.node_yaml_cli import main as _cli_main

    return _cli_main()


if __name__ == "__main__":
    sys.exit(main())
