#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-main, __main__, cli-entrypoint, python3 -m, 119-H, 170-cycle-break
# STRUCTURE: ▶ python3 -m core.internal.shared.node_yaml → __main__.py → cli.main (module-level, внутри пакета) → ⎋ sys.exit(main())
# region MODULE_CONTRACT
## @purpose  CLI entrypoint для `python3 -m core.internal.shared.node_yaml` (DevPlan 119 H1).
##           Делегирует в node_yaml.cli.main() — CLI-логика живёт в cli.py внутри пакета
##           (DevPlan 117 G T51 + 170 W10-B: перенос node_yaml_cli → node_yaml/cli.py).
## @scope    Только прямой CLI-запуск. Не импортируется при `import core.internal.shared.node_yaml`
##           (агрегатор re-export'ит CLI-символы лениво через PEP 562 __getattr__).
## @invariants
##   1. Тонкий фасад: 3 строки, никакой бизнес-логики.
##   2. `python3 -m core.internal.shared.node_yaml --get node.host` работает (AC-G5, CLI-контракт).
##   3. 170 W10-B: импорт cli.main module-level (sibling-ребро __main__→cli в одну сторону;
##      цикла нет — cli внутри пакета, импортирует только parent-агрегатор).
## @rationale DevPlan 119 H1: node_yaml.py (файл) конвертирован в пакет node_yaml/ (паттерн E3
##            phases → phases/). Для пакета `python3 -m` ищет __main__.py — перенесено из
##            `if __name__ == "__main__"` монолита.
## @changes 2026-08-03 · DevPlan 119 H1 — создан (вынос из node_yaml.py __main__ блока)
## @changes 2026-08-15 · DevPlan 170 W10-B — lazy node_yaml_cli → module-level node_yaml.cli
# endregion MODULE_CONTRACT

import sys

from core.internal.shared.node_yaml.cli import main as _cli_main


def main() -> int:
    """Delegate to node_yaml.cli.main() (module-level import — внутри пакета, без цикла).

    ## @purpose — Единственная точка входа для python3 -m core.internal.shared.node_yaml.
    ## @io — ⇥ sys.argv → ⎋ sys.exit(main())
    ## @complexity — O(1) + CLI dispatch
    """
    return _cli_main()


if __name__ == "__main__":
    sys.exit(main())
