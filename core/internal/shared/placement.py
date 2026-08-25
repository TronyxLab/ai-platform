#!/usr/bin/env python3
# GREP_SUMMARY: placement, placement.yaml, load-placement, resolve-node-modules, service-host, validate-topology, lint-drift, multi-node, module-placement, all-nodes, nodes-list, off, vpn-enforced, cgnat
# STRUCTURE: ▶ load_placement(path) → ◇ schema+host+vpn checks → ⊕ Placement(dataclass) → ⚡ resolve_node_modules / service_host → ⊕ validate_topology(modules/node-configs/projects) → ∑ lint_drift → ⎋ Placement | list[str] | raise ConfigValidationError
# region MODULE_CONTRACT
## @purpose  Загрузчик+резолвер+валидатор топологии размещения модулей платформы по серверам
##           одного контекста (DevPlan 010 T0.2/T0.4). placement.yaml — единственный файл
##           топологии (node-configs/<context>/placement.yaml); его отсутствие = single-node
##           легаси-резолв no-op (None). Резолв: singleton {node} / all-nodes / nodes[] (nginx
##           multi-ingress) / off. Формы follow/public_host УДАЛЕНЫ в r2 (DevPlan 010 §2.1).
## @scope    core/internal/shared/ — переиспользуемая бизнес-логика размещения. Потребители:
##           deploy_orchestrator (резолв модулей + validate_topology в _placement_for_node),
##           gen_env_platform (кросс-нодовые хосты), гейты схемы↔загрузчик (T0.6).
## @invariants
##   1. Single-node no-op: отсутствие placement.yaml → None; резолвер не вызывается (байт-
##      совместимость с легаси — DevPlan 010 §1.1).
##   2. vpn_enforced: true обязателен в multi-node (аттестация шифрованного канала, инвариант 7);
##      missing/false → ConfigValidationError (T2.0d enforcement).
##   3. nodes[].host — ТОЛЬКО RFC1918 (10/8, 172.16/12, 192.168/16) или CGNAT 100.64/10
##      (ipaddress-модуль, без regex); публичный/не-IP → ConfigValidationError (инвариант 7).
##   4. Формы размещения: {node} singleton / {mode: all-nodes} / {nodes:[...]} (v1 — только nginx,
##      multi-ingress) / {mode: off}; follow/public_host отсутствуют (r2, 0 потребителей).
##   5. Неизвестное имя ноды в resolve_node_modules / service_host / lint_drift →
##      ConfigValidationError (fail-fast, DevPlan 010 §1.3).
##   6. validate_topology fail-fast (T0.4): (a) каждый ключ modules существует в инвентаре
##      core/modules/ (фактический ls — захардкоженный список запрещён); (b) каждая нода имеет
##      node-configs/<name>/node.yaml и её contexts[0].name == placement.context; (c) ПОЛНОТА:
##      каждый модуль инвентаря обязан иметь запись (включая off) — fail-fast против опечаток;
##      (d) exposed-проект (expose+domain) обязан иметь target_node ∈ nginx-нод ({node} или
##      {nodes:[...]}), дубликат FQDN кросс-нодово → ошибка; (e) off запрещён для data-plane
##      зависимости размещённого модуля (module.yaml#depends_on ∩ DATA_PLANE_DEPS).
##   7. Off-зависимости проверяются ТОЛЬКО по DATA_PLANE_DEPS = {postgres, redis, minio,
##      clickhouse, logging}; инфра-зависимости (nginx и пр.) ИСКЛЮЧЕНЫ из кросс-нодовых
##      ограничений (DevPlan 010 §2.2 п.8 — иначе легитимные топологии false-RED).
##   8. lint_drift — WARNING-строки (НЕ ошибки): node.yaml#modules enabled, но не размещённые на
##      этой ноде, с repair-подсказкой; placement авторитетен (DevPlan 010 §1.2/§2.2 п.2).
## @rationale Q: почему shared/? A: потребители — deploy_orchestrator, gen_env_platform,
##            гейты (≥2) — правило shared/AGENTS.md правило 3.
##            Q: почему vpn_enforced/хост в загрузчике, а не в схеме? A: приватность (RFC1918/
##            CGNAT) невыразима в draft-07 без regex; решение плана T0.1 «семантика host — в
##            загрузчике, не в regex»; vpn_enforced — аттестация, а не структура.
##            Q: зачем completeness (c)? A: отсутствие записи = опечатка в имени модуля —
##            резолв молча даст пустой набор вместо явной ошибки (DevPlan 010 §2.1).
## @changes 2026-08-22 · DevPlan 010 W0 T0.2/T0.4 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

import ipaddress
import json
import logging
import pathlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import yaml

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.schema_validator import validate_dict_against_schema

logger = logging.getLogger(__name__)

# core/internal/shared/placement.py → parent ×3 = <repo>/core → schemas/
_SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "schemas" / "placement.schema.json"

# Data-plane сервисы, чей mode:off запрещён, когда на них ссылается размещённый модуль
# (DevPlan 010 §2.2 п.8). Инфра-зависимости (nginx и пр.) ИСКЛЮЧЕНЫ из кросс-нодовых ограничений —
# эвристика-константа, см. 🧐 TRAP[DECISION] в validate_topology.
DATA_PLANE_DEPS = frozenset({"postgres", "redis", "minio", "clickhouse", "logging"})

# Приватные/VPN диапазоны (DevPlan 010 инвариант 7): RFC1918 + CGNAT shared address space.
# ipaddress-network membership вместо regex — семантика T0.1 «в загрузчике, не в regex».
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
)


# region CLASS_Placement
@dataclass(frozen=True)
class Placement:
    """Типизированная модель топологии размещения (DevPlan 010 T0.2).

    GREP_SUMMARY: Placement, dataclass, context, vpn_enforced, nodes, modules
    STRUCTURE: ▶ Placement(context, vpn_enforced, nodes, modules) → ⎋ иммутабельная модель

    ## @purpose  Нормализованное представление placement.yaml для резолверов/валидаторов.
    ## @fields   context — имя контекста (= contexts[0].name каждой ноды, drift-гейт)
    ##           vpn_enforced — аттестация оператора (шифрованный канал; true обязателен)
    ##           nodes — mapping имя ноды → приватный/VPN host (RFC1918/CGNAT)
    ##           modules — mapping имя модуля → форма размещения ({node}/{mode}/{nodes})
    ## @invariants  Модель иммутабельна (frozen). Формы приходят только из load_placement
    ##              (уже schema-валидированные) или от тестов.
    """

    context: str
    vpn_enforced: bool
    nodes: dict[str, str]
    modules: dict[str, dict[str, object]]


# endregion CLASS_Placement


# region FUNC__load_schema
def _load_schema() -> dict[str, object]:
    """Load placement.schema.json (draft-07) for validate_dict_against_schema.

    ## @purpose  Прочитать schema один раз на вызов load_placement; объектность корня —
    ##            fail-fast (аналог TRAP[BUG] schema_validator non-dict root).
    ## @io — ⇥ → ⎋ dict[str, object] | raise ConfigParseError
    ## @complexity — O(S) где S = размер схемы
    ## @invariants  Non-dict root / невалидный JSON → ConfigParseError (exit 3).
    """
    try:
        with _SCHEMA_PATH.open(encoding="utf-8") as f:
            raw: object = cast(object, json.load(f))
    except json.JSONDecodeError as e:
        msg = f"placement.schema.json is not valid JSON: {_SCHEMA_PATH}: {e}"
        raise ConfigParseError(msg) from e
    if not isinstance(raw, dict):
        msg = f"placement.schema.json root must be an object: {_SCHEMA_PATH}"
        raise ConfigParseError(msg)
    return raw


# endregion FUNC__load_schema


# region FUNC__is_private_host
def _is_private_host(host: str) -> bool:
    """Check host is RFC1918 or CGNAT private address (ipaddress, no regex).

    ## @purpose  Аттестация узла: nodes[].host обязан быть приватным/VPN адресом
    ##            (DevPlan 010 инвариант 7). Публичный или не-IP → False.
    ## @io — ⇥ host: str → ⎋ bool
    ## @complexity — O(1)
    ## @invariants  Только ipaddress.ip_network membership — БЕЗ regex на диапазоны.
    ##              Не-IP (FQDN/мусор) → False через ipaddress.ip_address ValueError.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


# endregion FUNC__is_private_host


# region FUNC__parse_placement_nodes
def _parse_placement_nodes(raw_nodes: object) -> dict[str, str]:
    """nodes[] → name→host c приватностной проверкой (C901-extraction из load_placement).

    ## @raises — ConfigValidationError: не-список / не-объекты / missing name-host /
    ##           дубликаты / публичный host
    """
    if not isinstance(raw_nodes, list):
        msg = "placement.yaml: nodes must be a list"
        raise ConfigValidationError(msg)
    nodes: dict[str, str] = {}
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            msg = "placement.yaml: nodes[] entries must be objects"
            raise ConfigValidationError(msg)
        name = str(raw_node.get("name", ""))
        host = str(raw_node.get("host", ""))
        if not name or not host:
            msg = f"placement.yaml: node entry missing name/host: {raw_node}"
            raise ConfigValidationError(msg)
        if name in nodes:
            msg = f"placement.yaml: duplicate node name {name!r}"
            raise ConfigValidationError(msg)
        if not _is_private_host(host):
            msg = (
                f"placement.yaml: node {name!r} host {host!r} is not a private address "
                "(RFC1918 10/8, 172.16/12, 192.168/16 or CGNAT 100.64/10)"
            )
            raise ConfigValidationError(msg)
        nodes[name] = host
    return nodes


# endregion FUNC__parse_placement_nodes


# region FUNC__parse_placement_modules
def _parse_placement_modules(raw_modules: object) -> dict[str, dict[str, object]]:
    """modules map → имя→форма (формы уже schema-валидированы oneOf; C901-extraction)."""
    if not isinstance(raw_modules, dict):
        msg = "placement.yaml: modules must be an object"
        raise ConfigValidationError(msg)
    modules: dict[str, dict[str, object]] = {}
    for module_name, form in raw_modules.items():
        if not isinstance(form, dict):
            msg = f"placement.yaml: module {module_name!r} placement form must be an object"
            raise ConfigValidationError(msg)
        modules[str(module_name)] = dict(form)
    return modules


# endregion FUNC__parse_placement_modules


# region FUNC__validate_form_node_refs
def _validate_form_node_refs(
    modules: Mapping[str, Mapping[str, object]],
    nodes: Mapping[str, str],
) -> None:
    """Каждая ссылка формы ({node}/{nodes:[...]}) обязана указывать на известную ноду (DR-M1 fix).

    ## @purpose  Fail-fast против опечаток при ЗАГРУЗКЕ placement.yaml: {node: data-9} раньше
    ##            молча выпадал из резолва любой ноды (модуль исчезал из деплоя без ошибки);
    ##            частичный lazy-guard существовал только в service_host. Валидация в load_placement
    ##            закрывает ВСЕХ потребителей (resolver, firewall, gen_env, healthcheck).
    ## @io — ⇥ modules: имя→форма; nodes: имя→host → ⎋ None | raise ConfigValidationError
    ## @complexity O(M*K) где K = размер nodes[]-списков
    ## @invariants
    ##   - mode-формы ({mode: all-nodes}/{mode: off}) не ссылаются на имена — не проверяются
    ##   - Не-строковые элементы nodes[] отфильтрованы schema oneOf (тут только строки)
    """
    known = set(nodes)
    for module, form in modules.items():
        if "node" in form:
            ref = str(form.get("node", "") or "")
            if ref and ref not in known:
                msg = (
                    f"placement: module {module!r} references unknown node {ref!r} "
                    f"(known: {sorted(known)}) — typo in {{node}} form"
                )
                raise ConfigValidationError(msg)
        if "nodes" in form:
            raw_list = form.get("nodes")
            refs = [n for n in raw_list if isinstance(n, str)] if isinstance(raw_list, list) else []
            unknown = sorted(set(refs) - known)
            if unknown:
                msg = (
                    f"placement: module {module!r} references unknown nodes {unknown} "
                    f"(known: {sorted(known)}) — typo in {{nodes:[...]}} form"
                )
                raise ConfigValidationError(msg)


# endregion FUNC__validate_form_node_refs


# region FUNC_load_placement
def load_placement(path: str | pathlib.Path) -> Placement | None:
    """Load and validate placement.yaml → Placement, or None for single-node no-op.

    ▶ ┌path┐ → ◇ is_file()? (✗ → ⎋ None single-node no-op) → ○ yaml.safe_load →
      ◇ validate_dict_against_schema (единственная Draft7Validator-точка) →
      ◇ vpn_enforced + host-checks → ⊕ Placement → ⎋ Placement | None

    ## @purpose  Единственная точка загрузки placement.yaml (DevPlan 010 T0.2): отсутствие файла
    ##            = single-node легаси-резолв (None); парсинг YAML; валидация по
    ##            placement.schema.json (shared/schema_validator); приватность хостов (RFC1918/
    ##            CGNAT, публичный → ConfigValidationError); vpn_enforced обязателен true.
    ## @io — ⇥ path: str|Path → ⎋ Placement | None
    ## @raises — ConfigParseError (YAML-синтаксис/non-dict root), ConfigValidationError
    ##           (ошибки схемы, публичный host, vpn_enforced missing/false)
    ## @complexity — O(N + S*I) где N = размер YAML, S*I = schema-валидация
    ## @invariants
    ##   - Файла нет → None (single-node no-op, байт-совместимость — DevPlan 010 §1.1)
    ##   - vpn_enforced: true обязателен (missing/false → ConfigValidationError)
    ##   - hosts — только RFC1918/CGNAT; публичный → ConfigValidationError
    ##   - Нормализация nodes в name→host mapping, modules в имя→форма
    """
    path = pathlib.Path(path)
    if not path.is_file():
        logger.info("[IMP:7][load_placement][noop] No placement.yaml at %s — single-node legacy resolve", path)
        return None

    try:
        with path.open(encoding="utf-8") as f:
            raw: object = cast(object, yaml.safe_load(f))
    except yaml.YAMLError as e:
        msg = f"placement.yaml parse error in {path}: {e}"
        raise ConfigParseError(msg) from e

    if raw is None:
        data: dict[str, object] = {}
    elif isinstance(raw, dict):
        data = raw
    else:
        msg = f"placement.yaml root is not a dict: {type(raw)}"
        raise ConfigParseError(msg)

    errors = validate_dict_against_schema(data, _load_schema())
    if errors:
        msg = f"placement.yaml schema errors in {path}: {'; '.join(errors)}"
        raise ConfigValidationError(msg)

    # ── vpn_enforced: true обязателен в multi-node (DevPlan 010 инвариант 7, T2.0d) ──
    vpn_enforced = data.get("vpn_enforced")
    if not isinstance(vpn_enforced, bool) or not vpn_enforced:
        msg = (
            "placement.yaml: vpn_enforced must be true in a multi-node context "
            "(operator attestation: cross-node traffic over an encrypted channel)"
        )
        raise ConfigValidationError(msg)

    nodes = _parse_placement_nodes(data.get("nodes"))
    modules = _parse_placement_modules(data.get("modules"))

    # ── DR-M1 fix (аудит DevPlan 010): ссылки форм на ноды валидируются при загрузке ──
    # Раньше опечатка {node: data-9} молча выпадала из резолва любой ноды (частичный
    # lazy-guard был только в service_host) — fail-fast здесь закрывает ВСЕХ потребителей.
    _validate_form_node_refs(modules, nodes)

    logger.info(
        "[IMP:9][load_placement][ok] context=%s nodes=%d modules=%d vpn_enforced=%s",
        data.get("context"),
        len(nodes),
        len(modules),
        vpn_enforced,
    )
    return Placement(
        context=str(data.get("context", "")),
        vpn_enforced=vpn_enforced,
        nodes=nodes,
        modules=modules,
    )


# endregion FUNC_load_placement


# region FUNC__module_placed_on_node
def _module_placed_on_node(form: Mapping[str, object], node_name: str) -> bool:
    """Resolve whether a placement form places the module on node_name.

    ## @purpose  Единая форма-семантика (DevPlan 010 §2.1): {node} singleton-совпадение /
    ##            all-nodes / член nodes[] → True; mode:off → False. follow-форм в r2 нет —
    ##            единственный mode-вид размещения.
    ## @io — ⇥ form: dict, node_name: str → ⎋ bool
    ## @complexity — O(K) где K = длина nodes[]-списка
    ## @invariants  all-nodes: любой узел (mode != off); off: никогда; {nodes:[...]}:
    ##              только член списка.
    """
    if "mode" in form:
        return form.get("mode") != "off"
    if "node" in form:
        return form.get("node") == node_name
    if "nodes" in form:
        raw_list = form.get("nodes")
        return isinstance(raw_list, list) and node_name in raw_list
    return False


# endregion FUNC__module_placed_on_node


# region FUNC_resolve_node_modules
def resolve_node_modules(placement: Placement, node_name: str) -> list[str]:
    """Resolve effective module list for a node (DevPlan 010 §2.2 п.2).

    ▶ ┌(placement, node)┐ → ◇ node known? → ○ filter modules by form → ⊕ sorted(names) → ⎋ list[str]

    ## @purpose  Эффективные модули ноды = placement.modules, отфильтрованный по «эта нода»
    ##            (singleton-совпадение / all-nodes / член nodes[]; mode:off — исключён).
    ##            node.yaml#modules для деплоя НЕ читается при наличии placement (§2.2 п.2).
    ## @io — ⇥ placement: Placement, node_name: str → ⎋ list[str] (sorted, детерминированный)
    ## @raises — ConfigValidationError: неизвестное имя ноды (DevPlan 010 §1.3)
    ## @complexity — O(M * K) где M = модули, K = размер nodes[]-списков
    ## @invariants  Результат отсортирован (детерминизм для diff/гейтов); off исключён.
    """
    if node_name not in placement.nodes:
        msg = f"placement: unknown node {node_name!r} (known: {sorted(placement.nodes)})"
        raise ConfigValidationError(msg)
    resolved = sorted(module for module, form in placement.modules.items() if _module_placed_on_node(form, node_name))
    logger.info(
        "[IMP:9][resolve_node_modules][resolved] node=%s modules=%d -> %s",
        node_name,
        len(resolved),
        ",".join(resolved),
    )
    return resolved


# endregion FUNC_resolve_node_modules


# region FUNC_service_host
def service_host(placement: Placement, module: str, consumer_node: str) -> str | None:
    """Cross-node host of module for consumer_node, or None when co-located.

    ▶ ┌(placement, module, consumer)┐ → ◇ consumer known? → ◇ form: mode? → ◇ node/nodes →
      ◇ co-located? → ⎋ host | None

    ## @purpose  Адрес сервиса для .env.platform (DevPlan 010 T2.1): модуль размещён на ДРУГОЙ
    ##            ноде → host той ноды; на этой же / модуля нет (off/неизвестен) → None
    ##            (вызывающий оставляет Docker DNS alias).
    ## @io — ⇥ placement: Placement, module: str, consumer_node: str → ⎋ str | None
    ## @raises — ConfigValidationError: неизвестная consumer-нода; модуль сослался на ноду вне nodes
    ## @complexity — O(K) где K = размер nodes[]-списка (multi-ingress)
    ## @invariants
    ##   - all-nodes / off → None (co-located на каждой ноде / не размещён)
    ##   - {node: X}, X != consumer → host ноды X
    ##   - {nodes:[...]}: consumer внутри списка → None; вне списка → host ПЕРВОЙ ноды списка
    ##     (детерминированный выбор при multi-ingress — единственный кросс-нодовый адрес)
    """
    if consumer_node not in placement.nodes:
        msg = f"placement: unknown consumer node {consumer_node!r}"
        raise ConfigValidationError(msg)
    form = placement.modules.get(module)
    if form is None:
        logger.info("[IMP:7][service_host][none] module=%s not in placement — keep Docker DNS alias", module)
        return None
    if "mode" in form:
        # all-nodes → co-located на каждой ноде; off → не размещён — None в обоих случаях
        return None
    if "node" in form:
        host_node = str(form.get("node", ""))
        if host_node == consumer_node:
            return None
        if host_node not in placement.nodes:
            msg = f"placement: module {module!r} placed on unknown node {host_node!r}"
            raise ConfigValidationError(msg)
        host = placement.nodes[host_node]
        logger.info("[IMP:8][service_host][remote] module=%s → host=%s (node=%s)", module, host, host_node)
        return host
    if "nodes" in form:
        raw_list = form.get("nodes")
        node_list = [n for n in raw_list if isinstance(n, str)] if isinstance(raw_list, list) else []
        if consumer_node in node_list:
            return None
        if node_list:
            # DR-L4 fix: KeyError → ConfigValidationError (exit-code контракт 4; защита для
            # программно-сконструированных Placement — load_placement валидирует refs раньше)
            host_node = node_list[0]
            if host_node not in placement.nodes:
                msg = f"placement: module {module!r} nodes[0] {host_node!r} is not a known placement node"
                raise ConfigValidationError(msg)
            host = placement.nodes[host_node]
            logger.info("[IMP:8][service_host][multi] module=%s → host=%s (first ingress node)", module, host)
            return host
    return None


# endregion FUNC_service_host


# region FUNC__form_nodes
def _form_nodes(form: Mapping[str, object] | None, placement_nodes: Mapping[str, str]) -> set[str]:
    """Nodes covered by a placement form (nginx multi-ingress checks).

    ## @purpose  Материализация формы в набор нод: {node} → {X}; {nodes:[...]} → список;
    ##            all-nodes → все ноды placement; off/None → ∅.
    ## @io — ⇥ form: dict|None, placement_nodes: mapping name→host → ⎋ set[str]
    ## @complexity — O(K) где K = размер nodes[]-списка
    ## @invariants  off → ∅; неизвестная форма → ∅ (fail-fast обеспечивает schema/резолверы).
    """
    if form is None:
        return set()
    if "mode" in form:
        return set(placement_nodes) if form.get("mode") != "off" else set()
    if "node" in form:
        return {str(form["node"])} if form.get("node") else set()
    if "nodes" in form:
        raw_list = form.get("nodes")
        return {n for n in raw_list if isinstance(n, str)} if isinstance(raw_list, list) else set()
    return set()


# endregion FUNC__form_nodes


# region FUNC__module_off
def _module_off(form: Mapping[str, object]) -> bool:
    """True when the placement form is the explicit {mode: off} switch.

    ## @purpose  Единый предикат выключения модуля контекста (DevPlan 010 §2.1 off).
    ## @io — ⇥ form: dict → ⎋ bool
    ## @complexity — O(1)
    """
    return form.get("mode") == "off"


# endregion FUNC__module_off


# region FUNC__read_module_depends_on
def _read_module_depends_on(module_dir: pathlib.Path) -> list[str]:
    """Read module.yaml#depends_on from a module inventory directory.

    ## @purpose  Прочитать depends_on размещённого модуля (DevPlan 010 §2.2 п.8) для проверки
    ##            off-зависимостей. module.yaml отсутствует/не содержит depends_on → [].
    ##            Чтение module.yaml (НЕ node.yaml) — прямой yaml.safe_load допустим
    ##            (запрет 2 shared/AGENTS.md касается только node.yaml — NodeYaml-фасад).
    ## @io — ⇥ module_dir: Path (директория модуля) → ⎋ list[str]
    ## @raises — ConfigParseError: невалидный YAML в module.yaml
    ## @complexity — O(N) для YAML-парсинга
    ## @invariants  Нет module.yaml → [] (инвентарная директория без контракта — deps нет);
    ##              зависит только от строковых записей (не-строки отфильтрованы).
    """
    module_yaml_path = module_dir / "module.yaml"
    if not module_yaml_path.is_file():
        return []
    try:
        with module_yaml_path.open(encoding="utf-8") as f:
            raw: object = cast(object, yaml.safe_load(f))
    except yaml.YAMLError as e:
        msg = f"module.yaml parse error in {module_yaml_path}: {e}"
        raise ConfigParseError(msg) from e
    if not isinstance(raw, dict):
        return []
    deps = raw.get("depends_on", [])
    if not isinstance(deps, list):
        return []
    return [str(d) for d in deps if isinstance(d, str)]


# endregion FUNC__read_module_depends_on


# region FUNC__validate_exposed_projects
def _validate_exposed_projects(
    placement: Placement,
    projects: Sequence[Mapping[str, object]],
) -> None:
    """(d) Exposed-проекты ↔ nginx-ноды + кросс-нодовая FQDN-уникальность (C901-extraction)."""
    nginx_nodes = _form_nodes(placement.modules.get("nginx"), placement.nodes)
    exposed = [p for p in projects if p.get("expose") and str(p.get("domain") or "").strip()]
    if exposed and not nginx_nodes:
        msg = "placement: exposed projects found but nginx has no placement ({node} or {nodes:[...]})"
        raise ConfigValidationError(msg)
    seen_fqdn: dict[str, str] = {}
    for project in exposed:
        domain = str(project["domain"]).strip()
        target_node = str(project.get("target_node") or "").strip()
        if target_node not in nginx_nodes:
            msg = (
                f"placement: exposed project {domain!r} target_node {target_node!r} is not an "
                f"nginx node {sorted(nginx_nodes)}"
            )
            raise ConfigValidationError(msg)
        if domain in seen_fqdn and seen_fqdn[domain] != target_node:
            msg = (
                f"placement: duplicate exposed FQDN {domain!r} across nodes ({seen_fqdn[domain]!r} and {target_node!r})"
            )
            raise ConfigValidationError(msg)
        seen_fqdn[domain] = target_node


# endregion FUNC__validate_exposed_projects


# region FUNC__validate_off_data_plane_deps
def _validate_off_data_plane_deps(placement: Placement, modules_dir: pathlib.Path) -> None:
    """(e) mode:off запрещён data-plane зависимости размещённого модуля (C901-extraction)."""
    for module, form in placement.modules.items():
        if _module_off(form):
            continue
        depends_on = _read_module_depends_on(modules_dir / module)
        for dep in depends_on:
            if dep not in DATA_PLANE_DEPS:
                continue  # инфра-deps исключены (TRAP[DECISION] в validate_topology)
            dep_form = placement.modules.get(dep)
            if dep_form is not None and _module_off(dep_form):
                msg = (
                    f"placement: module {module!r} depends on data-plane service {dep!r} "
                    f"which is mode: off — cannot disable a data-plane dependency"
                )
                raise ConfigValidationError(msg)


# endregion FUNC__validate_off_data_plane_deps


# region FUNC__validate_inventory_and_nodes
def _validate_inventory_and_nodes(
    placement: Placement,
    modules_dir: pathlib.Path,
    node_configs_dir: pathlib.Path,
) -> set[str]:
    """(a)-(c): инвентарь, node.yaml/context-match, полнота записей (C901-extraction)."""
    # (a) Инвентарь: фактический ls (захардкоженный список запрещён — DevPlan 010 T0.4(a))
    if not modules_dir.is_dir():
        msg = f"validate_topology: modules_dir does not exist: {modules_dir}"
        raise ConfigValidationError(msg)
    inventory = {p.name for p in modules_dir.iterdir() if p.is_dir()}
    for module in placement.modules:
        if module not in inventory:
            msg = f"placement: module {module!r} not found in modules inventory {modules_dir}"
            raise ConfigValidationError(msg)

    # (b) Ноды: node.yaml существует + contexts[0].name == placement.context (T0.4(b))
    if not node_configs_dir.is_dir():
        msg = f"validate_topology: node_configs_dir does not exist: {node_configs_dir}"
        raise ConfigValidationError(msg)
    for node_name in placement.nodes:
        node_yaml_path = node_configs_dir / node_name / "node.yaml"
        if not node_yaml_path.is_file():
            msg = f"placement: node {node_name!r} has no node.yaml at {node_yaml_path}"
            raise ConfigValidationError(msg)
        # NodeYaml-фасад — единая точка чтения node.yaml (shared/AGENTS.md запрет 2)
        node_ctx = NodeYaml(str(node_yaml_path)).get_context()
        if node_ctx != placement.context:
            msg = (
                f"placement: node {node_name!r} contexts[0].name {node_ctx!r} != placement.context "
                f"{placement.context!r} (1 node = 1 context)"
            )
            raise ConfigValidationError(msg)

    # (c) Полнота: каждый модуль инвентаря обязан иметь запись (включая off) — T0.4(c)
    for module in sorted(inventory):
        if module not in placement.modules:
            msg = (
                f"placement completeness: module {module!r} from inventory has no placement record "
                f"(add it with a node/all-nodes/nodes/off form)"
            )
            raise ConfigValidationError(msg)
    return inventory


# endregion FUNC__validate_inventory_and_nodes


# region FUNC_validate_topology
def validate_topology(
    placement: Placement,
    *,
    modules_dir: str | pathlib.Path,
    node_configs_dir: str | pathlib.Path,
    projects_scan: Callable[[], Sequence[Mapping[str, object]]] | None = None,
) -> None:
    """Validate placement against module inventory, node-configs and projects (DevPlan 010 T0.4).

    ▶ ┌(placement, modules_dir, node_configs_dir, projects_scan)┐ → ○ inventory ls →
      ◇ (a) keys∈inventory → ◇ (b) node.yaml + contexts[0].name match → ◇ (c) completeness →
      ◇ (d) exposed↔nginx nodes + FQDN-uniq → ◇ (e) off data-plane deps → ⎋ None | raise

    ## @purpose  Fail-fast валидатор топологии (DevPlan 010 §1.3/§2.2): (a) каждый ключ modules
    ##            существует в инвентаре (фактический ls — захардкоженный список запрещён);
    ##            (b) каждая нода имеет node-configs/<name>/node.yaml с contexts[0].name ==
    ##            placement.context (1 нода = 1 контекст); (c) ПОЛНОТА: каждый модуль инвентаря
    ##            обязан иметь запись (включая off); (d) exposed-проект обязан иметь target_node
    ##            ∈ nginx-нод ({node} или {nodes:[...]}), дубликат FQDN кросс-нодово → ошибка
    ##            (статический скан всех node-configs — один репозиторий); (e) mode:off запрещён
    ##            для data-plane зависимости размещённого модуля (module.yaml#depends_on ∩
    ##            DATA_PLANE_DEPS; инфра-deps исключены).
    ## @io — ⇥ placement: Placement; modules_dir: Path (инвентарь core/modules/);
    ##         node_configs_dir: Path (node-configs/<context>/); projects_scan: Callable|None
    ##         (→ список проектов с полями domain/expose/target_node) → ⎋ None | raise
    ## @raises — ConfigValidationError на любую из (a)-(e) проверок
    ## @complexity — O(I + N + P + M*D) где I = инвентарь, N = ноды, P = проекты, M*D = deps
    ## @invariants
    ##   - Инвентарь = фактический ls modules_dir (захардкоженный список запрещён)
    ##   - Неполнота записей (включая off) → ConfigValidationError (fail-fast против опечаток)
    ##   - nginx-ноды = форма {node} или {nodes:[...]} модуля nginx; all-nodes/off → ∅
    ##   - Проверка exposed выполняется только если projects_scan передан (DI-seam, как NodeYaml)
    ##   - data-plane deps из DATA_PLANE_DEPS; инфра-deps исключены (TRAP[DECISION] ниже)
    """
    # 🧐 TRAP[DECISION] · 2026-08-22 · — · Деление module.yaml#depends_on на data-plane (DATA_PLANE_DEPS)
    # и infra-классы — эвристика-константа: только mode:off data-plane зависимости блокирует
    # размещённого потребителя; инфра-упорядочивающие зависимости (nginx и пр.) ИСКЛЮЧЕНЫ из
    # кросс-нодовых ограничений · Rejected: проверять ВСЕ depends_on без классов · Reason: инфра-deps
    # иначе false-RED легитимных топологий (DevPlan 010 §2.2 п.8: «nginx off при живом hermes» —
    # GREEN) · Rev: при появлении нового класса сервисов (broker/queue) — пересмотреть состав
    # DATA_PLANE_DEPS и, возможно, вынести классификацию в module.yaml (placement_units, §3 TRAP)
    modules_dir = pathlib.Path(modules_dir)
    node_configs_dir = pathlib.Path(node_configs_dir)

    inventory = _validate_inventory_and_nodes(placement, modules_dir, node_configs_dir)

    # (d) Exposed-проекты ↔ nginx-ноды + кросс-нодовая FQDN-уникальность (T0.4(d))
    if projects_scan is not None:
        _validate_exposed_projects(placement, list(projects_scan()))

    # (e) Off-зависимости: data-plane deps размещённых модулей не могут быть mode:off (T0.4(e))
    _validate_off_data_plane_deps(placement, modules_dir)

    logger.info(
        "[IMP:9][validate_topology][ok] context=%s nodes=%d modules=%d inventory=%d",
        placement.context,
        len(placement.nodes),
        len(placement.modules),
        len(inventory),
    )


# endregion FUNC_validate_topology


# region FUNC_lint_drift
def lint_drift(
    node_yaml_modules_enabled: Sequence[str],
    placement: Placement,
    node_name: str,
) -> list[str]:
    """Drift WARNINGs: node.yaml#modules enabled but not placed on this node.

    ▶ ┌(node_yaml_modules, placement, node)┐ → ◇ node known? → ○ placed = resolve(node) →
      ⊕ diff → ⊕ repair hints → ⎋ list[str] warnings

    ## @purpose  Drift-сигнал node.yaml ↔ placement (DevPlan 010 T1.2): модули enabled в node.yaml,
    ##            но не размещённые на этой ноде — WARNING-строки (НЕ ошибки; placement авторитетен,
    ##            §2.2 п.2 — node.yaml для деплоя не читается) с repair-подсказкой «удали из
    ##            node.yaml или перенеси в placement». Потребитель — другой агент (T1.2 lint-гейт).
    ## @io — ⇥ node_yaml_modules_enabled: list[str], placement: Placement, node_name: str → ⎋ list[str]
    ## @raises — ConfigValidationError: неизвестное имя ноды
    ## @complexity — O(M log M) где M = число модулей
    ## @invariants  Возвращает warnings, никогда не бросает по drift-факту (только unknown node);
    ##              placement не мутируется.
    """
    if node_name not in placement.nodes:
        msg = f"placement: unknown node {node_name!r} in lint_drift"
        raise ConfigValidationError(msg)
    placed = set(resolve_node_modules(placement, node_name))
    warnings = [
        (
            f"[PRACTICES] module {module!r} is enabled in node.yaml but not placed on node "
            f"{node_name!r} in placement.yaml — remove it from node.yaml or move it to placement"
        )
        for module in node_yaml_modules_enabled
        if module not in placed
    ]
    for warning in warnings:
        logger.warning("[IMP:7][lint_drift][warn] %s", warning)
    logger.info(
        "[IMP:9][lint_drift][ok] node=%s warnings=%d (drift is WARNING, not RED)",
        node_name,
        len(warnings),
    )
    return warnings


# endregion FUNC_lint_drift


# region FUNC_placement_node_relative_path
def placement_node_relative_path(node_yaml: str | pathlib.Path, context: str) -> pathlib.Path:
    """Единый резолвер пути placement.yaml относительно node.yaml ноды (DevPlan 16 T1.B).

    ▶ ┌(node_yaml, context)┐ → ○ parent(node dir).parent / <context> / placement.yaml → ⎋ Path

    ## @purpose  Убивает три независимые деривации (deploy_orchestrator._placement_for_node,
    ##           firewall_placement_args, modules_healthcheck._resolve_enabled_modules —
    ##           knowledge dedup P0-2): файл по этому пути создаёт deliver_placement
    ##           (core_deliverer) каналом core-push; remote-форма пути —
    ##           shared/deploy_paths.placement_remote_path (та же структура {ncb}/<context>/).
    ## @io — ⇥ node_yaml: путь к node.yaml ноды, context: имя контекста → ⎋ Path
    ## @complexity O(1)
    ## @invariants  Форма пути канонизирована: sibling контекстной директории рядом с нодами
    ##              ({root}/<context>/placement.yaml при нодах {root}/<node>/node.yaml);
    ##              существование файла НЕ проверяется (вызывающий решает no-op/fail —
    ##              load_placement вернёт None для отсутствующего пути).
    ## @rationale Q: почему функция, а не константа-шаблон? A: три потребителя с разными
    ##            стилями (resolve/не-resolve) — единая точка фиксирует .resolve() канон и
    ##            делает расползание невозможным.
    """
    return pathlib.Path(node_yaml).resolve().parent.parent / context / "placement.yaml"


# endregion FUNC_placement_node_relative_path


# region FUNC_firewall_placement_args
def firewall_placement_args(node_yaml: str | pathlib.Path) -> list[str]:
    """CLI-аргументы --placement для firewall.sh из node.yaml ноды ([] при single-node).

    ▶ ┌node.yaml┐ → ◇ context? → ⊕ path = ROOT/<context>/placement.yaml → ◇ exists? →
      ⎋ ["--placement", path] | []

    ## @purpose  DR-H1 fix (DevPlan 010 T2.3 follow-up): peer-firewall правила применяются
    ##            только если фазы φ1/φ11 передают --placement в firewall.sh. Единая деривация
    ##            пути — здесь (рядом с load_placement); фасад firewall.sh пробрасывает "$@".
    ## @io — ⇥ node_yaml: путь к node.yaml ноды → ⎋ list[str] ([] = single-node no-op флаг)
    ## @complexity 1 — NodeYaml context read + file existence check
    ## @invariants
    ##   - Деривация идентична deploy_orchestrator._placement_for_node:
    ##     placement.yaml = parent(node.yaml dir).parent / <context> / "placement.yaml"
    ##     (DevPlan 16 T1.B: единый резолвер placement_node_relative_path)
    ##   - Fail-open ([]): нет context / нечитаемый node.yaml / отсутствующий файл →
    ##     флаг не добавляется; валидность переданного файла проверит load_placement
    ##     внутри firewall.py (invalid → ConfigValidationError, loud)
    ## @rationale Q: почему в shared/? A: 2 потребителя (φ1 system_bootstrap, φ11 registry_update)
    ##            + единая деривация пути рядом с загрузчиком — правило shared/AGENTS.md п.3.
    """
    try:
        context = NodeYaml(str(node_yaml)).get_context()
    except (ConfigNotFoundError, ConfigParseError, OSError) as exc:
        # fail-open: bootstrap φ1 может идти до полной валидации node.yaml — peer-rules
        # применятся на следующем прогоне; отсутствие флага = прежнее поведение
        # (OSError покрывает пустую строку → IsADirectoryError('.') и нечитаемые пути)
        logger.warning("[IMP:7][firewall_placement_args][skip] node.yaml unreadable: %s", exc)
        return []
    if not context:
        logger.info("[IMP:8][firewall_placement_args][noop] no context in %s — no flag", node_yaml)
        return []
    # DevPlan 16 T1.B: единый резолвер (была локальная деривация parent.parent/context)
    placement_path = placement_node_relative_path(node_yaml, context)
    if not placement_path.is_file():
        logger.info(
            "[IMP:8][firewall_placement_args][noop] no placement.yaml at %s — single-node",
            placement_path,
        )
        return []
    logger.info("[IMP:9][firewall_placement_args][ok] %s", placement_path)
    return ["--placement", str(placement_path)]


# endregion FUNC_firewall_placement_args
