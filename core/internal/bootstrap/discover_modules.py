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
## @rationale Eliminates manual compose updates when adding/removing modules.
##            🧐 TRAP[DECISION] · 2026-07-31 · — · Изоляция ОТМЕНЕНА (DevPlan 116 T7, D3)
##            · Rejected: собственный YAML-предикат (exact) в bootstrap-критическом пути
##            · Reason: решение пользователя 2026-07-31 (U-59): предикаты разошлись —
##              substring (scripts/module_discovery) vs exact YAML (здесь). Дублирование
##              кода опаснее изоляции. Канонический предикат: scripts/module_discovery.py
##              ::discover_docker_modules — импортируется с fallback (см. импорты ниже).
##            · Rev: если module_discovery.py переедет — обновить fallback-путь _SCRIPTS_DIR
# endregion MODULE_CONTRACT
"""

import json
import re
import sys
from pathlib import Path
from typing import TypedDict, cast

import yaml

# ── Canonical predicate import (DevPlan 116 T7, D3) ──
# Паттерн импорта как в secrets_manager.py:54-70 — canonical package import +
# sys.path fallback для script-инвокации (python3 discover_modules.py вне pytest).
try:
    from core.internal.scripts.module_discovery import discover_docker_modules
except ModuleNotFoundError:
    # ⚠️ TRAP[BUG] · 2026-08-13 · P1 · Path-объект в sys.path ломал --test-infra (DevPlan 163 W-G)
    # · Symptom: discover_modules.py --test-infra --json → ModuleNotFoundError: No module named
    # ·   'module_discovery' → 4 collection-errors pytest (tests/_conftest/infra.py) → exit 2
    # · Root: PTH-автофикс W-B конвертировал os.path.join → Path(_SCRIPTS_DIR); sys.path требует
    # ·   str — Path-элемент молча не матчится importlib (класс sync_env_defaults, 163 W-A handoff)
    # · Fix: str(_SCRIPTS_DIR) в sys.path.insert
    # · Prevention: запрет не-str в sys.path.insert (см. files/ruff_policy.md; TID251-кандидат)
    _SCRIPTS_DIR = Path(Path(Path(Path(__file__).resolve()).parent).parent, "scripts")
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    from module_discovery import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] — W11-G3: module_discovery (scripts/) без типов
        discover_docker_modules,  # pyright: ignore[reportUnknownVariableType] — W11-G3: module_discovery (scripts/) без типов
    )

# ── Custom YAML tag constructors ──────────────────────────────────────────────
# docker-compose.test.yml files use !override tag for network/port/container
# overrides (DevPlan 017 W3.6). safe_load would fail on this custom tag.
# Resolve !override as identity — the test.yml already contains the final value.


_PORT_PARTS_MIN: int = 2  # port mapping host:container[:proto]


def _yaml_override_constructor(loader: yaml.SafeLoader, node: yaml.Node):
    """Resolve !override YAML tag — return the underlying data as-is.

    Handles all node types: MappingNode (dict), SequenceNode (list), ScalarNode (str).
    docker-compose.test.yml uses !override for container_name, networks, ports.
    """
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    # MappingNode/SequenceNode уже обработаны — остаток гарантированно ScalarNode
    return loader.construct_scalar(cast(yaml.ScalarNode, node))


yaml.SafeLoader.add_constructor("!override", _yaml_override_constructor)


# region TYPEDEF_TestInfra
class _TestInfraEntry(TypedDict):
    """Одна запись discover_test_infra (W11-G3: YAML/JSON-граница)."""

    module: str
    container_names: list[str]
    service_containers: dict[str, str]
    networks: list[str]
    ports: dict[str, list[dict[str, int]]]
    compose_base: str
    compose_test: str


# endregion TYPEDEF_TestInfra


# region FUNC__parse_service_ports
## @purpose  Parse ports of a single compose service into {internal, external} dicts (PLR1702 extraction).
## @io       ⇥ svc: dict (compose service node) → ⎋ list[dict[str, int]] (empty when unparsable)
## @complexity O(P) — P port mappings
## @invariants — "external:internal" or "host:external:internal[:proto]" format (см. _PORT_PARTS_MIN)
def _parse_service_ports(svc: dict[str, object]) -> list[dict[str, int]]:
    """Parse service ports list; returns [] on malformed mappings."""
    parsed: list[dict[str, int]] = []
    for port_mapping in cast("list[object]", svc.get("ports") or []):  # W11-G3: YAML-граница — ports список
        parts = str(port_mapping).split(":")
        if len(parts) < _PORT_PARTS_MIN:  # host:container[:proto]
            continue
        try:
            external_val = int(parts[-2]) if len(parts) > _PORT_PARTS_MIN else int(parts[0])
            internal_val = int(parts[-1])
        except (ValueError, IndexError):
            continue
        parsed.append({"internal": internal_val, "external": external_val})
    return parsed


# endregion FUNC__parse_service_ports


# region FUNC_discover_test_infra
## @purpose  Scan core/modules/*/docker-compose.test.yml, extract container_name, networks, ports
## @io       ⇥ None (scans filesystem) → ⎋ list[dict]: module test info sorted by module_name
## @complexity — O(M * S) where M=modules, S=services per module
## @invariants
##   - Only processes modules with docker-compose.test.yml
##   - container_names sorted alphabetically within each module
##   - Ports parsed from "external:internal" or "host:external:internal" format
##   - Networks extracted from service-level networks key (both dict and list formats)
def discover_test_infra() -> list[_TestInfraEntry]:
    """Scan core/modules/*/docker-compose.test.yml, extract test infrastructure metadata.

    Returns sorted list of dicts: module_name, container_names, networks, ports, compose paths.
    """
    modules: list[_TestInfraEntry] = []
    modules_dir = Path(__file__).resolve().parent.parent.parent.parent / "core" / "modules"

    for mod_dir in sorted(modules_dir.iterdir()):
        test_compose = mod_dir / "docker-compose.test.yml"
        if not test_compose.exists():
            continue

        compose_data = cast(
            "dict[str, object] | None", yaml.safe_load(test_compose.read_text())
        )  # W11-G3: yaml.safe_load → Any; YAML-граница compose
        mod_name = mod_dir.name
        container_names: list[str] = []
        # Service-to-container-name mapping for multi-service modules (e.g., postgres→pgbouncer-test, postgres-test)
        service_containers: dict[str, str] = {}
        networks: set[str] = set()
        # Ports stored as list per service name (services can have multiple ports).
        # Format: { "nginx": [{"internal": 80, "external": 18080}, {"internal": 443, "external": 18443}] }
        ports: dict[str, list[dict[str, int]]] = {}
        base_compose = mod_dir / "docker-compose.base.yml"

        for svc_name, svc in cast("dict[str, object]", compose_data.get("services") or {}).items():
            svc = cast("dict[str, object]", svc)  # W11-G3: YAML-граница — svc-node compose
            if "container_name" in svc:
                cn = str(svc["container_name"])
                container_names.append(cn)
                service_containers[svc_name] = cn
            for net in cast("list[object]", svc.get("networks") or []):
                if isinstance(net, dict):
                    networks.update(
                        str(k) for k in cast("dict[object, object]", net)
                    )  # W11-G3: YAML-граница — dict-форма сети (ключи → str)
                else:
                    networks.add(str(net))  # W11-G3: YAML-граница — str-форма сети
            svc_ports = _parse_service_ports(svc)
            if svc_ports:
                ports[svc_name] = svc_ports

        modules.append({
            "module": mod_name,
            "container_names": sorted(container_names),
            "service_containers": service_containers,
            "networks": sorted(networks),
            "ports": ports,
            "compose_base": str(base_compose) if base_compose.exists() else "",
            "compose_test": str(test_compose),
        })

    print(f"[IMP:8][discover_test_infra] Found {len(modules)} test modules", file=sys.stderr)
    return modules


# endregion FUNC_discover_test_infra


# region FUNC_discover_modules
## @purpose  Discover docker compose paths (repo-relative strings) via the CANONICAL
##            predicate scripts/module_discovery.discover_docker_modules (DevPlan 116 T7, D3).
##            Thin adapter: canonical returns Path objects → mapped to repo-relative
##            `core/modules/<name>/docker-compose.base.yml` strings for include-секции.
## @io       Path (modules_dir) → list[str]
## @complexity 2 — file I/O with sorted glob (delegated)
def _to_repo_relative(path: Path, project_root: Path) -> str:
    """Map a compose Path to a repo-relative string (fallback: as-is for test fixtures).

    ## @purpose  Canonical predicate returns Path objects relative to the given modules_dir.
    ##            include-секция docker-compose.yml ожидает repo-relative `core/modules/...`.
    ## @io        ⇥ path, project_root → ⎋ str
    ## @complexity O(1)
    """
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        # modules_dir вне repo (тестовая фикстура) — используем путь как есть
        return str(path)


def discover_modules(modules_dir: Path) -> list[str]:
    """Scan module.yaml files via canonical predicate, return repo-relative compose paths."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    compose_files = cast(
        "list[Path]", discover_docker_modules(modules_dir)
    )  # W11-G3: module_discovery (scripts/) без типов → Unknown
    result = [_to_repo_relative(compose_file, project_root) for compose_file in compose_files]
    print(
        f"[IMP:9][discover_modules] Canonical predicate discovered {len(result)} docker modules",
        file=sys.stderr,
    )
    return result


# endregion FUNC_discover_modules


# region FUNC_update_compose_include
## @purpose  Update docker-compose.yml include section. Returns True if changed.
## @io       Path, list[str] → bool
## @complexity 2 — file I/O with regex substitution
def update_compose_include(compose_path: Path, modules: list[str]) -> bool:
    """Update docker-compose.yml include section. Returns True if changed."""
    with Path(compose_path).open(encoding="utf-8") as f:
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
        with Path(compose_path).open("w", encoding="utf-8") as f:
            f.write(new_content)
    return changed


# endregion FUNC_update_compose_include


# region FUNC_main
## @purpose  CLI entry point: discover modules, update docker-compose.yml, or --test-infra
## @io       sys.argv → int exit code
## @complexity 2
def main() -> int:
    # ── Parse CLI args ─────────────────────────────────────────────────
    flags = [a for a in sys.argv[1:] if a.startswith("-")]

    if "--test-infra" in flags:
        result = discover_test_infra()
        if "--json" in flags:
            print(json.dumps(result, indent=2))
        else:
            for mod in result:
                print(
                    f"[IMP:7][discover-test-infra] {mod['module']}: "
                    f"{len(mod['container_names'])} container(s), "
                    f"{len(mod['networks'])} network(s), "
                    f"{len(mod['ports'])} port(s)"
                )
        return 0

    # ── Existing behavior (no --test-infra) ─────────────────────────────
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
