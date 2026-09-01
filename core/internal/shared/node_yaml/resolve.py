#!/usr/bin/env python3
# GREP_SUMMARY: node-yaml-resolve, ResolveMixin, resolve, 4-path, node-configs, platform-node-configs, overlay-glob, glob, 119-H, 024
# STRUCTURE: ▶ ResolveMixin.resolve(node_name, config_dir) → ◇ env NODE_NAME/hostname → ◇ 4-path glob (platform_root → projects/*/platform → projects-legacy → /opt) → ◇ isfile → ⎋ NodeYaml | raise ConfigNotFoundError
# region MODULE_CONTRACT
## @purpose  Доменный миксин NodeYaml — 4-path резолв node.yaml (DevPlan 119 H1 + 024 TASK-1).
##           resolve() ищет node.yaml в 4 путях и возвращает загруженный NodeYaml-инстанс.
## @scope    Миксин для NodeYaml-агрегатора (node_yaml/__init__.py). Потребители:
##           node-resolver.sh (фасад), remote_executor, e2e conftest (Path 1).
## @invariants
##   1. 4-path search (в порядке): {platform_root}/node-configs/{node_name}/node.yaml,
##      $HOME/projects/*/platform/node-configs/{node_name}/node.yaml (overlay-канон glob, DevPlan 022 Option A),
##      $HOME/projects/*/node-configs/{node_name}/node.yaml (legacy sibling glob — миграционное окно,
##      IMP:7 WARN при срабатывании), /opt/node-configs/{node_name}/node.yaml.
##      Групповой порядок детерминирован: все platform-матчи предшествуют всем legacy-матчам;
##      алфавитный порядок внутри группы сохранён.
##   2. node_name: аргумент → env NODE_NAME → socket.gethostname().
##   3. config_dir: аргумент → env PLATFORM_ROOT → platform_remote_base() (shared/deploy_paths, B3).
##   4. Raises ConfigNotFoundError если не найдено ни в одном пути.
##   5. classmethod: возвращает cls(path) — агрегатор NodeYaml (MRO подставляет конкретный класс).
## @rationale DevPlan 119 H1 (AUDIT-2 M1): 3-path resolve выделен из монолита node_yaml.py (T2).
##            Делегирование platform_remote_base сохранено (единый канон путей, DevPlan 118 B3).
##            DevPlan 024 TASK-1: platform-glob кандидат ПЕРЕД legacy — канон «overlay = единственный
##            источник контекстных данных» (022 Option A); explicit config_dir остаётся первым
##            (контракт e2e conftest Path 1 не ломается).
## @changes 2026-09-01 · DevPlan 024 TASK-1 — platform-glob кандидат (overlay-first) + IMP:7 WARN
##           при legacy-резолве (миграционный сигнал; glob-группа удалится после миграции asi-group)
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
    """Доменный миксин NodeYaml: 4-path resolve node.yaml (DevPlan 119 H1 + 024 TASK-1).

    GREP_SUMMARY: ResolveMixin, resolve, 4-path, node-configs, platform-node-configs
    STRUCTURE: ▶ ResolveMixin.resolve(node_name, config_dir) → ◇ 4-path → ⎋ NodeYaml
    """

    # region FUNC_resolve
    ## @purpose  Resolve node.yaml via 4-path search and return loaded NodeYaml instance.
    ## @io — ⇥ node_name: Optional[str], config_dir: Optional[str] → ⎋ NodeYaml
    ## @complexity — O(P) where P = number of glob candidates
    ## @invariants
    ##   Searches 4 paths in order (group order deterministic):
    ##     1. {platform_root}/node-configs/{node_name}/node.yaml  (explicit — e2e contract)
    ##     2. $HOME/projects/*/platform/node-configs/{node_name}/node.yaml (overlay canon, 022)
    ##     3. $HOME/projects/*/node-configs/{node_name}/node.yaml (legacy sibling — IMP:7 WARN)
    ##     4. /opt/node-configs/{node_name}/node.yaml
    ##   Raises ConfigNotFoundError if not found in any path.
    @classmethod
    def resolve(
        cls: type[_TNodeYaml],
        node_name: str | None = None,
        config_dir: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> _TNodeYaml:
        """Resolve node.yaml via 4-path search and return loaded NodeYaml instance.

        Returns the concrete subclass via MRO (NodeYaml when called as NodeYaml.resolve).

        Searches 4 paths in order:
          1. {platform_root}/node-configs/{node_name}/node.yaml
          2. $HOME/projects/*/platform/node-configs/{node_name}/node.yaml (overlay canon, DevPlan 022)
          3. $HOME/projects/*/node-configs/{node_name}/node.yaml (legacy sibling — migration window)
          4. /opt/node-configs/{node_name}/node.yaml

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

        # Path 1: platform_root/node-configs/{node_name}/node.yaml (explicit — ПЕРВЫЙ, контракт e2e)
        candidates: list[str] = [
            os.path.join(config_dir, "node-configs", node_name, "node.yaml"),
        ]

        # Path 2 (канон overlay, DevPlan 022 Option A): ~/projects/*/platform/node-configs/…
        # os.path.expanduser — тестовый seam: test_domain_verifier мокает его (Path.expanduser обходит мок)
        projects_dir = os.path.expanduser("~/projects")
        platform_matches = sorted(
            glob_module.glob(os.path.join(projects_dir, "*", "platform", "node-configs", node_name, "node.yaml"))
        )
        candidates.extend(platform_matches)

        # Path 3 (legacy sibling — миграционное окно): ~/projects/*/node-configs/…
        # Групповой порядок детерминирован: platform-матчи ВСЕГДА предшествуют legacy-матчам (D1).
        # ⚠️ TRAP[BUG] · 2026-09-01 · P1 · Glob-резолв доставлял legacy-фикстуру вместо overlay
        #   node.yaml (silent wrong-source) · Root: смена канона layout (DevPlan 022 Option A) не
        #   доведена до читателя — glob искал только legacy sibling projects/*/node-configs/
        # · Symptom: node-update NODE=tronyx-vps резолвил ~/projects/ai-platform/node-configs/…
        #   (glob sorted: ai-platform < tronyx-lab), содержимое overlay
        #   ~/projects/tronyx-lab/platform/node-configs/… (projects, monitoring,
        #   postgres_init_databases) до VPS не доходило — desired-state фиксстура↔overlay расходился
        # · Fix: DevPlan 024 TASK-1 — кандидат ~/projects/*/platform/node-configs/ ПЕРЕД
        #   legacy-glob + [IMP:7] WARN при legacy-резолве (миграционный сигнал)
        # · Prevention: смена канона layout = обход ВСЕХ glob-потребителей канона тем же планом
        #   (читатель resolve.py + создатель context_initializer.py)
        # · Rev: удалить legacy sibling-glob (и WARN) после миграции asi-group (R5 — не «по пути»)
        legacy_matches = sorted(
            glob_module.glob(os.path.join(projects_dir, "*", "node-configs", node_name, "node.yaml"))
        )
        candidates.extend(legacy_matches)

        # Path 4: /opt/node-configs/{node_name}/node.yaml
        candidates.append(f"/opt/node-configs/{node_name}/node.yaml")

        for p in candidates:
            if os.path.isfile(p):
                if p in legacy_matches:
                    # Миграционный сигнал: overlay-канонический путь не найден, резолв через
                    # legacy sibling-фикстуру. Удаляется вместе с legacy-glob после миграции
                    # asi-group (Rev TRAP[BUG] ниже).
                    logger.warning(
                        "[IMP:7][NodeYaml.resolve][legacy-fallback] node=%s resolved via legacy sibling "
                        "glob — overlay-canonical ~/projects/*/platform/node-configs/ not found "
                        "(migration debt, DevPlan 024 TASK-1)",
                        node_name,
                    )
                logger.info("[IMP:9][NodeYaml.resolve] Found: %s", p)
                return cls(p)  # pyright: ignore[reportCallIssue] — миксин-классметод: cls = NodeYaml (MRO-агрегатор), __init__(path) известен в композиции; ResolveMixin сам не декларирует __init__

        searched = ", ".join(candidates)
        logger.error("[IMP:10][NodeYaml.resolve] Not found for node=%s (searched: %s)", node_name, searched)
        msg = f"node.yaml not found for node={node_name}"
        raise ConfigNotFoundError(msg)

    # endregion FUNC_resolve


# endregion CLASS_ResolveMixin
