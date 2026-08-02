#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-node, NodeMixin, node, get-node-info, NodeInfo, 119-H
# STRUCTURE: ▶ NodeMixin → ◇ _load() → ◇ data.get("node") dict-проверка → ◇ NodeInfo(fqdn/owner_key/docker_mirror) → ⎋ NodeInfo
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — поддомен `node` node.yaml (DevPlan 119 H1).
##           get_node_info() возвращает typed NodeInfo (fqdn, owner_key, docker_mirror).
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           preflight.py + CLI --node-info (типобезопасный аксессор, DevPlan 118 B3).
## @invariants
##   1. Returns NodeInfo with defaults if keys missing.
##   2. Non-dict 'node' section → NodeInfo с пустыми полями (graceful, no raise).
##   3. NOT the same as get("node.host") — get_node_info() читает node.fqdn (legacy field),
##      consumers (project_lister/project_remover) используют node.get("node.host").
## @rationale DevPlan 119 H1 (AUDIT-2 M1): поддомен node выделен из монолита node_yaml.py.
##            NodeInfo NamedTuple сохранён — типобезопасный аксессор с потребителями
##            preflight.py + CLI (118 B3 verify-then-delete: остальные typed-геттеры удалены).
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (get_node_info + NodeInfo)
##           в node_yaml/node.py без изменения логики
## @changes 2026-07-30 · DevPlan 088 — get_node_info + NodeInfo created (T1)
# endregion MODULE_CONTRACT

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)


# region NAMEDTUPLE_NodeInfo
class NodeInfo(NamedTuple):
    """Typed node metadata from node.yaml.

    ## @purpose  Structured representation of the node section in node.yaml.
    ## @fields   fqdn — fully qualified domain name of the node
    ##           owner_key — age key or SSH key of the node owner
    ##           docker_mirror — Docker registry mirror URL
    """

    fqdn: str = ""
    owner_key: str = ""
    docker_mirror: str = ""


# endregion NAMEDTUPLE_NodeInfo


# region CLASS_NodeMixin
class NodeMixin:
    """Доменный миксин NodeYaml: поддомен node (DevPlan 119 H1).

    GREP_SUMMARY: NodeMixin, node, get-node-info
    STRUCTURE: ▶ NodeMixin → ◇ get_node_info() → ⎋ NodeInfo
    """

    # region FUNC_get_node_info
    ## @purpose  Extract node metadata as a typed NamedTuple.
    ## @io — ⇥ → ⎋ NodeInfo
    ## @complexity — O(1) after _load()
    ## @invariants  Returns NodeInfo with defaults if keys missing.
    def get_node_info(self) -> NodeInfo:
        """Extract node metadata as a typed NamedTuple.

        Returns:
            NodeInfo(fqdn, owner_key, docker_mirror)
        """
        data = self._load()
        node = data.get("node", {})

        if not isinstance(node, dict):
            node = {}

        info = NodeInfo(
            fqdn=node.get("fqdn", ""),
            owner_key=node.get("owner_key", ""),
            docker_mirror=node.get("docker_mirror", ""),
        )
        logger.info("[IMP:8][NodeYaml] Node info: %s", info.fqdn)
        return info

    # endregion FUNC_get_node_info


# endregion CLASS_NodeMixin
