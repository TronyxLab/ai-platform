#!/usr/bin/env python3
"""
# GREP_SUMMARY: discover_modules, compose include, module auto-discovery, docker-compose generation
# STRUCTURE: ▶ scan core/modules/*/module.yaml → ◇ filter install_type:docker → ⊕ generate include: paths → ∑ write docker-compose.yml → ⎋ report changes
# region MODULE_CONTRACT
## @purpose  Auto-discover Docker modules and regenerate docker-compose.yml include section
## @scope    Scans core/modules/*/module.yaml, filters docker-type modules, updates root compose
## @invariants
##   - Only modifies the include: section of docker-compose.yml
##   - Preserves all other sections (networks, volumes, etc.)
##   - Idempotent: running twice = no-op if modules unchanged
##   - Excludes modules with install_type: system
## @rationale Eliminates manual compose updates when adding/removing modules
# endregion MODULE_CONTRACT
"""

import re
import sys
from pathlib import Path

import yaml


# region FUNC_discover_modules
## @purpose  Scan module.yaml files, return sorted list of docker compose paths
## @io       Path (modules_dir) → list[str]
## @complexity 2 — file I/O with sorted glob
def discover_modules(modules_dir: Path) -> list[str]:
    """Scan module.yaml files, return sorted list of docker compose paths."""
    modules = []
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        try:
            with open(module_yaml) as f:
                data = yaml.safe_load(f)
            install_type = data.get("install_type", "docker")
            if install_type == "system":
                continue  # Skip system modules (e.g., platform-secrets)
            module_name = module_yaml.parent.name
            compose_path = f"core/modules/{module_name}/docker-compose.base.yml"
            modules.append(compose_path)
        except Exception as e:
            print(f"WARNING: Skipping {module_yaml}: {e}", file=sys.stderr)
    return modules


# endregion FUNC_discover_modules


# region FUNC_update_compose_include
## @purpose  Update docker-compose.yml include section. Returns True if changed.
## @io       Path, list[str] → bool
## @complexity 2 — file I/O with regex substitution
def update_compose_include(compose_path: Path, modules: list[str]) -> bool:
    """Update docker-compose.yml include section. Returns True if changed."""
    with open(compose_path) as f:
        content = f.read()

    # Generate new include section (2-space indent — style of committed docker-compose.yml)
    include_lines = [f"  - path: {mod}" for mod in modules]
    # Trailing newline preserves the blank-line separation from subsequent sections (networks:, volumes:)
    new_include = "include:\n" + "\n".join(include_lines) + "\n"

    # ⚠️ TRAP[BUG] · 2026-07-15 · P1 · Тихий no-op: regex ожидал 6-пробельный отступ, реальный файл использует 2
    # · Symptom: make discover-modules никогда не обновляет docker-compose.yml —
    #   генерация и regex в update_compose_include() используют 6-пробельный отступ,
    #   закоммиченный файл — 2 пробела. Regex не матчится → changed=False всегда.
    # · Root: fixture-код-дрейф — unit-фикстуры повторяли отступ кода (6 пробелов),
    #   а не реального закоммиченного файла (2 пробела). Тесты вакуумно-зелёные.
    # · Fix: indent-толерантный pattern + генерация в стиле реального файла (2 пробела)
    # · Prevention: gate test прогоняет update_compose_include на закоммиченном файле
    pattern = r"include:\n(?:[ \t]+- path: .+\n?)+"
    new_content = re.sub(pattern, new_include, content)

    changed = new_content != content
    if changed:
        with open(compose_path, "w") as f:
            f.write(new_content)
    return changed


# endregion FUNC_update_compose_include


# region FUNC_main
## @purpose  CLI entry point: discover modules, update docker-compose.yml
## @io       sys.argv → int exit code
## @complexity 2
def main() -> int:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    modules_dir = project_root / "core" / "modules"
    compose_file = project_root / "docker-compose.yml"

    if not compose_file.exists():
        print(f"ERROR: {compose_file} not found", file=sys.stderr)
        return 1

    modules = discover_modules(modules_dir)

    if not modules:
        print("ERROR: No Docker modules found", file=sys.stderr)
        return 1

    print(f"[IMP:8][discover-modules] Found {len(modules)} Docker modules")
    for m in modules:
        print(f"  - {m}")

    changed = update_compose_include(compose_file, modules)
    if changed:
        print(f"[IMP:9][discover-modules] docker-compose.yml updated with {len(modules)} modules")
    else:
        print("[IMP:8][discover-modules] docker-compose.yml already up-to-date")

    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
