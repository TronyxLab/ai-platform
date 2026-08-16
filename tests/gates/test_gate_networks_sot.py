#!/usr/bin/env python3
# GREP_SUMMARY: gate networks-sot platform-infra networks parity compose network-names canon allowlist default R5-negative DevPlan-119-A4
# STRUCTURE: ▶ scan docker-compose*.yml (root + modules base/test) networks → ◇ name ∈ platform-infra.yaml canon ∪ {default}? → ⊕ offenders → ⟦RED: file:name⟧ | 0 → PASS → ⎋ R5 negative (неканоничное имя → обнаружено)
# region MODULE_CONTRACT
## @purpose  Gate: networks Single-Source-of-Truth parity (DevPlan 119 A4, AUDIT-4 K3) —
##           имена сетей во всех docker-compose файлах (root + модули base/test) должны
##           быть в каноне core/platform-infra.yaml#networks или в allowlist.
##           По образцу test_gate_volumes_sot.py (асимметрия покрытия закрыта).
## @scope    Статический файловый анализ — docker-compose.yml (root), docker-compose.macos.yml,
##           docker-compose.platform-dev.yml, core/modules/*/docker-compose.base.yml,
##           core/modules/*/docker-compose.test.yml. Docker daemon не требуется.
## @invariants
##   - Канон имён сетей — core/platform-infra.yaml#networks (список {name, driver})
##   - Каждое имя сети (top-level networks: + service networks:) ∈ канон ∪ allowlist
##   - Allowlist: {"default"} — compose автоматическая сеть (сервис без networks)
##   - YAML !override tag (test.yml) обрабатывается конструктором (как test_gate_structural_consistency SMOKE_ISOLATION)
##   - R5 negative: неканоничное имя сети → RED (anti-survivorship)
## @rationale  AUDIT-4 K3: volumes_sot гейт есть, networks-parity нет — асимметрия покрытия.
##             Единый канон (platform-infra.yaml) + гейт делают имена сетей grepable
##             и enforce-емыми; дрейф (новое имя сети в compose без канона) → RED.
## @changes  2026-08-02 | DevPlan 119 A4 — Created (по образцу test_gate_volumes_sot.py)
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLATFORM_INFRA = PROJECT_ROOT / "core" / "platform-infra.yaml"
ROOT_COMPOSE = PROJECT_ROOT / "docker-compose.yml"
MACOS_COMPOSE = PROJECT_ROOT / "docker-compose.macos.yml"
PLATFORM_DEV = PROJECT_ROOT / "docker-compose.platform-dev.yml"
MODULES_DIR = PROJECT_ROOT / "core" / "modules"

# Compose автоматическая сеть — сервис без networks: получает network "default".
_ALLOWLIST_NETWORKS: set[str] = {"default"}


# YAML !override tag (test.yml) — Docker Compose extends YAML с array-replacement
# семантикой; конструктор возвращает значение как есть (merge-семантика не нужна).
def _yaml_override_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> list | dict:
    """YAML constructor for !override tag — returns the value unchanged."""
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_sequence(node)


yaml.add_constructor("!override", _yaml_override_constructor, Loader=yaml.SafeLoader)


def _read_file(path: Path) -> str:
    """Read file content, return empty string if not found."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return ""


# region FUNC_canonical_networks
def _canonical_networks() -> set[str]:
    """Read canonical network names from core/platform-infra.yaml#networks (SoT).

    ## @purpose — Единственный источник канонических имён сетей (DevPlan 119 A4).
    ## @io — ⎋ set[str]: имена сетей из platform-infra.yaml
    ## @complexity — O(N) — YAML-парсинг списка
    ## @invariants
    ##   - platform-infra.yaml#networks — список {name, driver} записей
    ##   - Отсутствие файла/секции → AssertionError (SoT обязателен)
    """
    assert PLATFORM_INFRA.is_file(), f"platform-infra.yaml not found: {PLATFORM_INFRA}"
    data = yaml.safe_load(_read_file(PLATFORM_INFRA)) or {}
    networks = data.get("networks", []) or []
    return {entry["name"] for entry in networks if isinstance(entry, dict) and entry.get("name")}


# endregion FUNC_canonical_networks


# region FUNC_discover_compose_files
def _discover_compose_files() -> list[Path]:
    """Discover all compose files subject to the networks parity contract.

    ## @purpose — Root docker-compose*.yml + модульные base/test compose-файлы.
    ## @io — ⎋ list[Path]: отсортированный список compose-файлов
    ## @complexity — O(F) — glob
    """
    files: list[Path] = [ROOT_COMPOSE]
    files.extend(p for p in (MACOS_COMPOSE, PLATFORM_DEV) if p.is_file())
    files.extend(sorted(MODULES_DIR.glob("*/docker-compose.base.yml")))
    files.extend(sorted(MODULES_DIR.glob("*/docker-compose.test.yml")))
    return [p for p in files if p.is_file()]


# endregion FUNC_discover_compose_files


# region FUNC_network_names_in_file
def _network_names_in_file(path: Path) -> set[str]:
    """Extract all network names from a single compose file.

    ## @purpose — top-level `networks:` ключи + service-level `networks:` ссылки
    ##            (dict {name: {...}} и list [name, ...] формы).
    ## @io — ⇥ path: Path → ⎋ set[str] имён сетей
    ## @complexity — O(S * N) где S = сервисы, N = networks на сервис
    ## @invariants
    ##   - YAML parse error → пустое множество (файл пропускается — не наш домен)
    ##   - !override tag обработан конструктором
    """
    names: set[str] = set()
    try:
        data = yaml.safe_load(_read_file(path)) or {}
    except yaml.YAMLError:
        return names

    top_level = data.get("networks")
    if isinstance(top_level, dict):
        names.update(top_level.keys())

    services = data.get("services") or {}
    for svc in services.values():
        if not isinstance(svc, dict):
            continue
        net_config = svc.get("networks")
        if isinstance(net_config, dict):
            names.update(net_config.keys())
        elif isinstance(net_config, list):
            names.update(str(n) for n in net_config if n is not None)
    return names


# endregion FUNC_network_names_in_file


# region FUNC_find_non_canonical_networks
def _find_non_canonical_networks(compose_files: list[Path] | None = None) -> dict[str, set[str]]:
    """Найти имена сетей вне канона (platform-infra.yaml) ∪ allowlist.

    ▶ ┌compose files┐ → ○ _network_names_in_file → ◇ name ∈ canon ∪ {default}? → ⊕ offenders → ⎋ dict
    ## @purpose — Ядро networks-parity гейта: {файл → {неканоничные имена}}.
    ## @io — ⇥ compose_files (опциональный список для тестируемости) → ⎋ dict[str, set[str]]
    ## @complexity — O(F * S * N)
    ## @invariants
    ##   - Имя сети в каноне ИЛИ в _ALLOWLIST_NETWORKS — допустимо
    ##   - Всё остальное → неканоничное (RED)
    """
    if compose_files is None:
        compose_files = _discover_compose_files()
    canon = _canonical_networks()
    offenders: dict[str, set[str]] = {}
    for cf in compose_files:
        names = _network_names_in_file(cf)
        bad = {n for n in names if n not in canon and n not in _ALLOWLIST_NETWORKS}
        if bad:
            try:
                rel = str(cf.relative_to(PROJECT_ROOT))
            except ValueError:
                rel = str(cf)  # probe-файлы в tmp_path (R5-negative) — полный путь
            offenders[rel] = bad
    return offenders


# endregion FUNC_find_non_canonical_networks


@pytest.mark.gate
class TestGateNetworksSot:
    """Gate: имена сетей в compose ⊆ канон platform-infra.yaml ∪ allowlist (DevPlan 119 A4)."""

    # 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · networks parity (DevPlan 119 A4, AUDIT-4 K3)
    # · Last fail: N/A (новый гейт — асимметрия: volumes_sot был, networks-parity не было)
    # · Remove if: сетевой SoT меняется (другой источник имён сетей)
    def test_all_network_names_in_canon(self):
        """Каждое имя сети во всех compose-файлах ∈ канон platform-infra.yaml ∪ allowlist."""
        canon = _canonical_networks()
        assert canon, "platform-infra.yaml#networks empty — SoT обязателен (DevPlan 119 A4)"
        offenders = _find_non_canonical_networks()

        assert not offenders, (
            f"NON_CANONICAL_NETWORKS: {len(offenders)} compose file(s) ссылаются на сети вне канона:\n"
            + "\n".join(f"  {cf}: {sorted(names)}" for cf, names in sorted(offenders.items()))
            + f"\n\nКанон (core/platform-infra.yaml#networks): {sorted(canon)}. "
            f"Allowlist: {sorted(_ALLOWLIST_NETWORKS)}. (DevPlan 119 A4, AUDIT-4 K3)"
        )

    # 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R5) · неканоничное имя сети → RED (DevPlan 119 A4)
    # · Scenario: probe-файл с networks: {evil-net: ...} → _network_names_in_file извлекает,
    #   _find_non_canonical_networks детектирует (DevPlan 171 W4.4: досылка — negative
    #   вызывает РЕАЛЬНЫЙ детектор, не его инлайн-копию)
    # · Last fail: N/A (новый negative-тест)
    # · Remove if: networks parity гейт отменяется
    def test_non_canonical_network_detected_negative(self, tmp_path: Path):
        """R5 negative: неканоничное имя сети в compose-файле → обнаружено (RED)."""
        probe = tmp_path / "docker-compose.probe.yml"
        probe.write_text(
            "services:\n"
            "  probe-svc:\n"
            "    image: busybox\n"
            "    networks:\n"
            "      - evil-custom-net\n"
            "networks:\n"
            "  evil-custom-net:\n"
            "    driver: bridge\n"
        )
        names = _network_names_in_file(probe)
        assert "evil-custom-net" in names, "R5 FAIL: probe network name not extracted"
        canon = _canonical_networks()
        assert "evil-custom-net" not in canon and "evil-custom-net" not in _ALLOWLIST_NETWORKS, (
            "R5 FAIL: probe name must be non-canonical (test integrity)"
        )
        # R5: реальный детектор (DevPlan 171 W4.4) — _find_non_canonical_networks с
        # probe-файлом; relative_to-fallback обрабатывает tmp-пути.
        offenders = _find_non_canonical_networks([probe])
        assert offenders, "R5 FAIL: non-canonical network name was NOT detected by _find_non_canonical_networks"
        assert "evil-custom-net" in next(iter(offenders.values())), (
            f"R5 FAIL: offender set does not contain probe name: {offenders}"
        )
