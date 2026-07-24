# GREP_SUMMARY: module_discovery, docker-module-list, compose-discovery, CI-helper, zero-deps
# STRUCTURE: ▶ discover_docker_modules(modules_dir) → ◇ glob */module.yaml → ⊕ filter install_type:system → ⎋ List[Path]
#            ▶ main() → ◇ argparse(--format json|lines|--count) → ⊕ discover_docker_modules → ◇ --count? ⊕ print(len) : print(json|lines)
# region MODULE_CONTRACT
## @purpose  Typed API + CLI для поиска docker-compose модулей. Заменяет inline `python3 -c` блоки
##           в platform-test.yml (дублирование ×3). Zero-dependency (text search).
## @scope    Чтение core/modules/*/module.yaml, фильтрация system-модулей, возврат списка compose-файлов.
##           Read-only — не мутирует файлы.
## @invariants
##   - Модули с `install_type: system` исключаются из результата (text-search, не YAML parse)
##   - Только модули с docker-compose.base.yml рядом с module.yaml включаются
##   - CLI: `--format json` (JSON array of strings) / `--format lines` (one file per line) / `--count` (int)
##   - API: возвращает list[pathlib.Path], отсортированный по имени модуля
##   - Сортировка стабильна: alphabetical по имени директории модуля
## @rationale Тот же inline-блок `python3 -c "import json; from pathlib..."` дублирован в 1 workflow-файле
##           (platform-test.yml ×3). Экстракция в typed-модуль даёт тестируемость,
##           единую валидацию, устранение дублирования. Zero-dependency: text search `'install_type: system'
##           not in content` вместо PyYAML — CI-раннер не требует PyYAML.
## @see      core/internal/bootstrap/discover_modules.py — полный аналог для bootstrap-окружения (YAML-based,
##           обновляет docker-compose.yml include-секцию). Новый CI-модуль изолирован: баг в CI не сломает
##           bootstrap critical path. Rejected: расширение bootstrap-модуля флагом --ci-list — добавляет
##           conditional complexity в критичный bootstrap-код (SRP violation).
## @changes
##   LAST_CHANGE: 2026-07-22 | Created (StatusReport 046 T2 — CICD-01a inline extraction)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import json
import pathlib
import sys

# endregion IMPORTS


# region CONSTANTS

MODULES_DIR = pathlib.Path("core/modules")
SYSTEM_INSTALL_MARKER = "install_type: system"

# endregion CONSTANTS


# region PUBLIC_API


def discover_docker_modules(modules_dir: pathlib.Path = MODULES_DIR) -> list[pathlib.Path]:
    """Discover docker-compose modules, excluding system-install modules.

    ▶ modules_dir → ◇ glob */module.yaml → ⊕ filter system + compose exists → ⎋ sorted List[Path]

    ## @purpose  Вернуть список docker-compose.base.yml для всех не-system модулей.
    ## @io       in: modules_dir (Path with */module.yaml entries) → out: list[Path]
    ## @complexity O(N) где N = число module.yaml, линейный scan + text search
    """
    print(f"[IMP:7][module_discovery] scanning {modules_dir}", file=sys.stderr)
    modules: list[pathlib.Path] = []
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        content = module_yaml.read_text()
        if SYSTEM_INSTALL_MARKER in content:
            print(f"[IMP:8][module_discovery] skip system module: {module_yaml.parent.name}", file=sys.stderr)
            continue
        compose_file = module_yaml.parent / "docker-compose.base.yml"
        if compose_file.exists():
            modules.append(compose_file)
        else:
            print(f"[IMP:8][module_discovery] skip (no compose): {module_yaml.parent.name}", file=sys.stderr)
    print(f"[IMP:9][module_discovery] discovered {len(modules)} docker modules", file=sys.stderr)
    return modules


# endregion PUBLIC_API


# region CLI


def main() -> int:
    """CLI entrypoint — prints discovered modules to stdout.

    ▶ argparse → ◇ discover → ⊕ format(json|lines) → ⎋ print → exit 0
    """
    parser = argparse.ArgumentParser(
        prog="module_discovery.py",
        description="Discover docker-compose modules (excludes install_type: system). Zero-dependency.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "lines"],
        default="lines",
        help="Output format: json array or one file per line (default: lines)",
    )
    parser.add_argument(
        "--count",
        action="store_true",
        help="Print only the integer count of discovered modules (e.g. 13)",
    )
    parser.add_argument(
        "--modules-dir",
        default=str(MODULES_DIR),
        help=f"Path to modules directory (default: {MODULES_DIR})",
    )
    args = parser.parse_args()

    modules = discover_docker_modules(pathlib.Path(args.modules_dir))

    if args.count:
        print(len(modules))
    elif args.format == "json":
        print(json.dumps([str(m) for m in modules]))
    else:
        for m in modules:
            print(str(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
