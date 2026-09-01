#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-resolve, ResolveMixin, resolve, 3-path, node-configs, glob, 119-H
# STRUCTURE: ▶ ResolveMixin.resolve(node_name, config_dir) → ◇ env NODE_NAME/hostname → ◇ 3-path glob (platform_root/~/projects//opt) → ◇ isfile → ⎋ NodeYaml | raise ConfigNotFoundError
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — 3-path резолв node.yaml (DevPlan 119 H1).
##           resolve() ищет node.yaml в 3 путях и возвращает загруженный NodeYaml-инстанс.
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           node-resolver.sh (фасад), remote_executor, e2e conftest (Path 1).
## @invariants
##   1. 3-path search (в порядке): {platform_root}/node-configs/{node_name}/node.yaml,
##      $HOME/projects/*/node-configs/{node_name}/node.yaml (glob), /opt/node-configs/{node_name}/node.yaml.
##   2. node_name: аргумент → env NODE_NAME → socket.gethostname().
##   3. config_dir: аргумент → env PLATFORM_ROOT → platform_remote_base() (shared/deploy_paths, B3).
##   4. Raises ConfigNotFoundError если не найдено ни в одном пути.
##   5. classmethod: возвращает cls(path) — агрегатор NodeYaml (MRO подставляет конкретный класс).
## @rationale DevPlan 119 H1 (AUDIT-2 M1): 3-path resolve выделен из монолита node_yaml.py (T2).
##            Делегирование platform_remote_base сохранено (единый канон путей, DevPlan 118 B3).
## @changes 2026-08-03 · DevPlan 119 H1 — извлечено из node_yaml.py (resolve) в node_yaml/resolve.py
##           без изменения логики
## @changes 2026-07-30 · DevPlan 088 — resolve created (T2)
# endregion MODULE_CONTRACT

import glob as glob_module
import logging
import os
from collections.abc import Mapping
from typing import TypeVar

from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import ConfigNotFoundError

logger = logging.getLogger(__name__)

# Self-тип миксина (W11): возврат cls(path) — конкретный агрегатор (NodeYaml) через MRO;
# TypeVar вместо Any — потребители получают типизированный инстанс без circular-импорта.
_TNodeYaml = TypeVar("_TNodeYaml", bound="ResolveMixin")


# region CLASS_ResolveMixin
class ResolveMixin:
    """Доменный миксин NodeYaml: 3-path resolve node.yaml (DevPlan 119 H1).

    GREP_SUMMARY: ResolveMixin, resolve, 3-path, node-configs
    STRUCTURE: ▶ ResolveMixin.resolve(node_name, config_dir) → ◇ 3-path → ⎋ NodeYaml
    """

    # region FUNC_resolve
    ## @purpose  Resolve node.yaml via 3-path search and return loaded NodeYaml instance.
    ## @io — ⇥ node_name: Optional[str], config_dir: Optional[str] → ⎋ NodeYaml
    ## @complexity — O(P) where P = number of glob candidates
    ## @invariants
    ##   Searches 3 paths in order:
    ##     1. {platform_root}/node-configs/{node_name}/node.yaml
    ##     2. $HOME/projects/*/node-configs/{node_name}/node.yaml (glob)
    ##     3. /opt/node-configs/{node_name}/node.yaml
    ##   Raises ConfigNotFoundError if not found in any path.
    @classmethod
    def resolve(
        cls: type[_TNodeYaml],
        node_name: str | None = None,
        config_dir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> _TNodeYaml:
        """Resolve node.yaml via 3-path search and return loaded NodeYaml instance.

        Returns the concrete subclass via MRO (NodeYaml when called as NodeYaml.resolve).

        Searches 3 paths in order:
          1. {platform_root}/node-configs/{node_name}/node.yaml
          2. $HOME/projects/*/node-configs/{node_name}/node.yaml (glob)
          3. /opt/node-configs/{node_name}/node.yaml

        Args:
            node_name: Node name. If None, tries from env NODE_NAME, then hostname.
            config_dir: Base config directory. If None, tries PLATFORM_ROOT env, then /opt/platform.
            env: env-дикт DI (W-H DevPlan 163) — override NODE_NAME/PLATFORM_ROOT;
                None = os.environ (поведение без изменений).

        Returns:
            Loaded NodeYaml instance

        Raises:
            ConfigNotFoundError: if node.yaml not found in any path
        """
        source: Mapping[str, str] = os.environ if env is None else env
        if node_name is None:
            node_name = source.get("NODE_NAME", "")
        if not node_name:
            import socket

            node_name = socket.gethostname()

        if config_dir is None:
            config_dir = source.get("PLATFORM_ROOT", str(platform_remote_base()))

        logger.info("[IMP:8][NodeYaml.resolve] Resolving node.yaml for node=%s", node_name)

        # Path 1: platform_root/node-configs/{node_name}/node.yaml
        candidates: list[str] = [
            os.path.join(config_dir, "node-configs", node_name, "node.yaml"),
        ]

        # Path 2: ~/projects/*/node-configs/{node_name}/node.yaml (glob)
        # os.path.expanduser — тестовый seam: test_domain_verifier мокает его (Path.expanduser обходит мок)
        projects_dir = os.path.expanduser("~/projects")
        candidates.extend(
            sorted(glob_module.glob(os.path.join(projects_dir, "*", "node-configs", node_name, "node.yaml")))
        )
        # 📝 TRAP[DEBT] · 2026-09-01 · MED · Glob не покрывает канонический вложенный layout
        #   `~/projects/*/platform/node-configs/{node}/node.yaml` (DevPlan 022 Option A —
        #   overlay-контейнер platform/) · Observed: миграция tronyx-lab (022 TASK-5) — node-update
        #   для NODE=tronyx-vps резолвит legacy-фикстуру ~/projects/ai-platform/node-configs/…, а не
        #   overlay ~/projects/tronyx-lab/platform/node-configs/… (glob сортирует ai-platform первым);
        #   repos.core доставляется из фикстуры, содержимое overlay node.yaml (projects, monitoring,
        #   postgres_init_databases) до VPS не доходит · Suspected: нужен кандидат
        #   `projects/*/platform/node-configs/` ПЕРЕД legacy sibling-путём (канон overlay = единственный
        #   источник контекстных данных) · Impact: расхождение desired-state фиксстура↔overlay
        #   сохраняется; после full-миграции контекстов резолв контекстных нод останется legacy ·
        # · When: обнаружено при миграции tronyx-lab (DevPlan 022 TASK-5, вне скоупа плана)
        # · Rev: первый сбой доставки node.yaml контекстной ноды → добавить platform/-кандидат
        #   с тестами резолва (fixture↔overlay сверка)

        # Path 3: /opt/node-configs/{node_name}/node.yaml
        candidates.append(f"/opt/node-configs/{node_name}/node.yaml")

        for p in candidates:
            if os.path.isfile(p):
                logger.info("[IMP:9][NodeYaml.resolve] Found: %s", p)
                return cls(p)  # pyright: ignore[reportCallIssue] — миксин-классметод: cls = NodeYaml (MRO-агрегатор), __init__(path) известен в композиции; ResolveMixin сам не декларирует __init__

        searched = ", ".join(candidates)
        logger.error("[IMP:10][NodeYaml.resolve] Not found for node=%s (searched: %s)", node_name, searched)
        msg = f"node.yaml not found for node={node_name}"
        raise ConfigNotFoundError(msg)

    # endregion FUNC_resolve


# endregion CLASS_ResolveMixin
