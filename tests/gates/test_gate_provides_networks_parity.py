# GREP_SUMMARY: gate provides-networks-parity platform-infra provides networks attach compose hermes-agent-net host alias REF-0017 R5-negative
# STRUCTURE: ▶ ┌platform-infra.yaml provides┐ → ◇ resolve providing service (host ∈ container_name|alias|service) → ◇ provides.networks ⊆ attach? → ⊕ offenders → ⟦RED⟧ | 0 → PASS → ⎋ R5 negative (дрейф сети/фантомный host → обнаружен)
# region MODULE_CONTRACT
## @purpose  Gate: network placement truth (REF-0017, meta-refactoring В3) —
##           provides.<svc>.networks из core/platform-infra.yaml (SoT) обязаны быть
##           ПОДМНОЖЕСТВОМ фактических attach-сетей предоставляющего сервиса в
##           core/modules/<svc>/docker-compose.base.yml; provides.<svc>.host обязан
##           резолвиться в Docker DNS namespace модуля (container_name | network alias |
##           service name). Дрейф SoT↔рантайм («SoT говорит hermes-agent-net, рантайм сидит
##           на shared-db-net») и фантомные хосты («nginx-proxy» при реальном alias «nginx»)
##           = RED.
## @scope    Статический файловый анализ: core/platform-infra.yaml#provides +
##           core/modules/*/docker-compose.base.yml. Docker daemon не требуется.
## @invariants
##   - Направление сверки — ПОДМНОЖЕСТВО (⊆): SoT декларирует потребительски-значимые сети;
##     фактический attach может быть шире (аддитивный attach — канон REF-0017: ничего не убираем)
##   - provides.networks отсутствует/пуст → сервис пропускается (нет декларации — нечего сверять)
##   - Модуль без docker-compose.base.yml → RED (provides запись без рантайма — дрейф)
##   - Providing service резолвится по host (container_name/alias/service name), fallback —
##     имя provides-ключа; нерезолвимый host → отдельный фантом-host violation
##   - R5 negatives: probe-фикстуры в tmp_path вызывают РЕАЛЬНЫЕ детекторы (не инлайн-копии)
## @rationale SEC-0034/AI-0001: канон «изоляция data-plane» существовал только на бумаге —
##            litellm/langfuse/minio были присоединены не к тем сетям, а PLATFORM_LANGFUSE_URL
##            и smoke-host целились в несуществующие порт/hostname, и ни один гейт это не ловил.
##            Один маленький parity-gат делает размещение enforce-емым: смена сетей/хоста в
##            composes мимо SoT (или наоборот) → RED на make check/gate.
## @changes  2026-08-25 | REF-0017 (meta-refactoring 11-DevPlan, Волна 3) — Created
# endregion MODULE_CONTRACT

from pathlib import Path

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
MODULES_DIR = ROOT / "core" / "modules"


# region FUNC_load_compose_services
def _load_compose_services(compose_path: Path) -> dict[str, dict]:
    """Парсинг services секции одного docker-compose.base.yml.

    ## @io — ⇥ compose_path → ⎋ {service_name: service_dict} (пустой dict при отсутствии файла)
    ## @complexity — O(S)
    ## @invariants
    ##   - Отсутствующий файл → {} (вызывающий код трактует как дрейф provides-записи)
    """
    if not compose_path.is_file():
        return {}
    with compose_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    services = data.get("services") or {}
    return {name: svc for name, svc in services.items() if isinstance(svc, dict)}


# endregion FUNC_load_compose_services


# region FUNC_service_dns_names
def _service_dns_names(service: dict, service_name: str) -> set[str]:
    """Docker DNS имена сервиса: container_name + все network aliases + имя сервиса.

    ## @io — ⇥ service dict, service_name → ⎋ set[str] резолвимых имён
    ## @complexity — O(N) по сетям сервиса
    """
    names: set[str] = {service_name}
    container_name = service.get("container_name")
    if isinstance(container_name, str):
        names.add(container_name)
    net_config = service.get("networks")
    if isinstance(net_config, dict):
        for net_data in net_config.values():
            aliases = net_data.get("aliases") if isinstance(net_data, dict) else None
            if isinstance(aliases, list):
                names.update(str(a) for a in aliases if isinstance(a, str))
    return names


# endregion FUNC_service_dns_names


# region FUNC_service_networks
def _service_networks(service: dict) -> set[str]:
    """Фактические attach-сети сервиса (dict- и list-формы networks:).

    ## @io — ⇥ service dict → ⎋ set[str] имён сетей
    ## @complexity — O(N)
    """
    net_config = service.get("networks")
    if isinstance(net_config, dict):
        return set(net_config.keys())
    if isinstance(net_config, list):
        return {str(n) for n in net_config if n is not None}
    return set()


# endregion FUNC_service_networks


# region FUNC_find_provides_violations
def _find_provides_violations(
    infra: dict,
    compose_map: dict[str, dict[str, dict]],
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Ядро гейта: сверка provides.networks ⊆ attach и provides.host ∈ DNS namespace.

    ▶ ┌provides entries┐ → ◇ resolve providing service → ◇ networks ⊆ attach ∧ host ∈ DNS → ⊕ violations → ⎋ (network_drift, phantom_hosts)
    ## @purpose — Единый детектор обоих классов дрейфа размещения (REF-0017).
    ## @io — ⇥ infra: распарсенный platform-infra.yaml;
    ##       compose_map: {module: {service: service_dict}}
    ##      ⎋ (network_drift: {svc: missing_nets}, phantom_hosts: {svc: host})
    ## @complexity — O(P × S × N), P=|provides|, S=сервисы модуля, N=сети сервиса
    ## @invariants
    ##   - Направление сетей — подмножество: SoT ⊆ факт (аддитивный attach легален)
    ##   - host проверяется на резолвимость в ЛЮБОЙ сервис модуля-владельца
    ##   - Пустой compose_map для модуля → network_drift (provides без рантайма)
    """
    network_drift: dict[str, set[str]] = {}
    phantom_hosts: dict[str, str] = {}
    provides = infra.get("provides") or {}

    for svc_name, svc_data in provides.items():
        if not isinstance(svc_data, dict):
            continue
        declared_nets = {str(n) for n in (svc_data.get("networks") or [])}
        host = svc_data.get("host")

        services = compose_map.get(svc_name, {})
        if not services:
            # provides-запись без compose-рантайма — весь декларированный attach отсутствует
            if declared_nets:
                network_drift[svc_name] = declared_nets
            if host:
                phantom_hosts[svc_name] = str(host)
            continue

        all_dns: set[str] = set()
        all_attach: set[str] = set()
        resolved = False
        for svc_dict in services.values():
            dns_names = _service_dns_names(svc_dict, "")
            all_dns |= dns_names
            all_attach |= _service_networks(svc_dict)
            if host and host in dns_names:
                resolved = True

        missing = declared_nets - all_attach
        if missing:
            network_drift[svc_name] = missing
        if host and not resolved and host not in all_dns:
            phantom_hosts[svc_name] = str(host)

    return network_drift, phantom_hosts


# endregion FUNC_provides_violations


# region FUNC_build_runtime_map
def _build_runtime_map() -> tuple[dict, dict[str, dict[str, dict]]]:
    """Рабочая карта: platform-infra.yaml + base-composes всех provides-модулей.

    ## @io — ⎋ (infra, {module: {service: service_dict}})
    ## @invariants — читает ТОЛЬКО core/platform-infra.yaml и core/modules/*/docker-compose.base.yml
    """
    with PLATFORM_INFRA.open(encoding="utf-8") as f:
        infra = yaml.safe_load(f) or {}
    provides = infra.get("provides") or {}
    compose_map: dict[str, dict[str, dict]] = {}
    for module in provides:
        compose_map[module] = _load_compose_services(MODULES_DIR / module / "docker-compose.base.yml")
    return infra, compose_map


# endregion FUNC_build_runtime_map


@pytest.mark.gate
class TestGateProvidesNetworksParity:
    """Gate (REF-0017): SoT-размещение (networks/host) отражено в фактическом compose-attach."""

    # 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · сетевой дрейф SoT↔рантайм (REF-0017, SEC-0034/AI-0001)
    # · Last fail: langfuse/minio composes без hermes-agent-net при declares SoT —
    #   канон «изоляция data-plane» не существовал; компрометация tenant-контейнера давала
    #   сеть до backup/trace-хранилищ без эскалации
    # · Remove if: provides.networks контракт упразднён
    def test_provides_networks_subset_of_compose_attach(self):
        """provides.<svc>.networks ⊆ фактических attach-сетей сервиса в docker-compose.base.yml."""
        infra, compose_map = _build_runtime_map()
        network_drift, _ = _find_provides_violations(infra, compose_map)
        assert not network_drift, (
            "GATE_PROVIDES_NETWORKS_PARITY: provides.*.networks не покрыты фактическим attach:\n"
            + "\n".join(f"  {svc}: missing {sorted(nets)}" for svc, nets in sorted(network_drift.items()))
            + "\n\nАддитивный attach (REF-0017): добавь недостающие сети в compose сервиса, "
            "НЕ убирая текущие; либо синхронизируй SoT core/platform-infra.yaml."
        )

    # 🧪 TRAP[TEST] · 2026-08-25 · REGRESSION · фантомный host (REF-0017: smoke ходил на
    # · nginx-proxy при реальном alias nginx → постоянный false-pass TCP-probe)
    # · Remove if: provides.host контракт упразднён
    def test_provides_host_resolves_in_module_dns(self):
        """provides.<svc>.host резолвится (container_name | network alias | service name)."""
        infra, compose_map = _build_runtime_map()
        _, phantom_hosts = _find_provides_violations(infra, compose_map)
        assert not phantom_hosts, (
            "GATE_PROVIDES_NETWORKS_PARITY: provides.*.host — фантомные hostname (нет в "
            "container_name/aliases/service compose-модуля):\n"
            + "\n".join(f"  {svc}: '{host}'" for svc, host in sorted(phantom_hosts.items()))
            + "\n\nИспользуй реальный Docker DNS alias (или добавь alias аддитивно) — "
            "иначе PLATFORM_*_HOST/smoke-host не резолвятся из контейнеров."
        )

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · сетевой дрейф обнаруживается (REF-0017)
    # · Last fail: N/A (negative — исходный вход бага: SoT hermes-agent-net при attach без него)
    # · Remove if: networks parity гейт отменяется
    def test_missing_network_detected_negative(self):
        """R5 negative: декларированная сеть без фактического attach → детектор ловит."""
        fixture_infra = {"provides": {"langfuse": {"host": "langfuse", "port": 3000, "networks": ["hermes-agent-net"]}}}
        fixture_compose = {
            "langfuse": {
                "langfuse": {
                    "container_name": "langfuse",
                    "networks": {"shared-db-net": {}, "observability-net": {}},
                }
            }
        }
        network_drift, _ = _find_provides_violations(fixture_infra, fixture_compose)
        assert network_drift.get("langfuse") == {"hermes-agent-net"}, (
            "R5 FAIL: детектор не поймал исходный вход REF-0017 (attach без hermes-agent-net)"
        )

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · фантомный host обнаруживается (REF-0017)
    # · Last fail: N/A (negative — исходный вход бага: provides.nginx.host=nginx-proxy без alias)
    # · Remove if: host-resolve проверка отменяется
    def test_phantom_host_detected_negative(self):
        """R5 negative: host вне DNS namespace модуля → детектор ловит."""
        fixture_infra = {"provides": {"nginx": {"host": "nginx-proxy", "port": 443, "networks": ["proxy-net"]}}}
        fixture_compose = {
            "nginx": {
                "nginx": {
                    "container_name": "nginx",
                    "networks": {"proxy-net": {"aliases": ["nginx"]}},
                }
            }
        }
        _, phantom_hosts = _find_provides_violations(fixture_infra, fixture_compose)
        assert phantom_hosts.get("nginx") == "nginx-proxy", (
            "R5 FAIL: детектор не поймал фантомный host nginx-proxy (реальный alias — nginx)"
        )
