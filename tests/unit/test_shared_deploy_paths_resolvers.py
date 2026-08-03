#!/usr/bin/env python3
# GREP_SUMMARY: test-shared-deploy-paths-resolvers letsencrypt-live node-configs-remote platform-remote-base projects-base unit C7
# STRUCTURE: ▶ test_letsencrypt_live (default + env) → test_node_configs_remote → test_platform_remote_base chain → test_projects_base
# region MODULE_CONTRACT
## @purpose  Unit tests for shared/deploy_paths.py резолверы (DevPlan 118 C7) — letsencrypt_live,
##           node_configs_remote, platform_remote_base, projects_base. Дедупликация литералов
##           /etc/letsencrypt/live (20 копий), /opt/node-configs, /opt/platform.
## @scope    Tests: резолверы с параметром env (без monkeypatch.setenv).
## @invariants
##   - env-переменные приоритетнее дефолтов; никогда не raise
##   - platform_remote_base chain: PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform
## @rationale DevPlan 118 C7 §TEST — unit-резолверы; grep-гейт (см. test_gate_deploy_paths).
## @changes 2026-08-02 | DevPlan 118 C7 — created
# endregion MODULE_CONTRACT

import logging

from core.internal.shared.deploy_paths import (
    DEFAULT_LETSENCRYPT_LIVE,
    DEFAULT_NODE_CONFIGS_REMOTE,
    DEFAULT_PLATFORM_BASE,
    letsencrypt_live,
    node_configs_remote,
    platform_remote_base,
    projects_base,
)

logger = logging.getLogger(__name__)


# 🧪 TRAP[TEST] · Regression · letsencrypt_live default + env override (C7)
# · Scenario: без env → /etc/letsencrypt/live; с LETSENCRYPT_LIVE → env
# · Last fail: 20 копий литерала /etc/letsencrypt/live (DevPlan 118 C7 факты)
# · Remove if: letsencrypt_live resolver removed
def test_letsencrypt_live() -> None:
    """letsencrypt_live → /etc/letsencrypt/live (default) или env override."""
    assert str(letsencrypt_live({})) == DEFAULT_LETSENCRYPT_LIVE
    assert str(letsencrypt_live({"LETSENCRYPT_LIVE": "/tmp/live"})) == "/tmp/live"
    logger.info("[IMP:9][test] letsencrypt_live default=%s", DEFAULT_LETSENCRYPT_LIVE)


# 🧪 TRAP[TEST] · Regression · node_configs_remote default + env (C7)
# · Scenario: без env → /opt/node-configs; с NODE_CONFIGS_REMOTE_BASE → env
# · Last fail: core_deliverer/overlay_deliverer литералы /opt/node-configs
# · Remove if: node_configs_remote resolver removed
def test_node_configs_remote() -> None:
    """node_configs_remote → /opt/node-configs (default) или NODE_CONFIGS_REMOTE_BASE."""
    assert str(node_configs_remote({})) == DEFAULT_NODE_CONFIGS_REMOTE
    assert str(node_configs_remote({"NODE_CONFIGS_REMOTE_BASE": "/tmp/nc"})) == "/tmp/nc"


# 🧪 TRAP[TEST] · Regression · platform_remote_base chain (C7 + RC 121 fix)
# · Scenario: PLATFORM_REMOTE_BASE → /opt/platform; PLATFORM_ROOT НЕ влияет на remote-базу
#   (RC 121: локальный PLATFORM_ROOT ложно детектил VPS-self — см. remote_executor TRAP[BUG])
# · Last fail: core_deliverer.resolve_remote_base + overlay_deliverer расходились (TRAP[BUG] 2026-07-31);
#   RC 121 — PLATFORM_ROOT исключён из remote-цепочки
# · Remove if: platform_remote_base resolver removed
def test_platform_remote_base_chain() -> None:
    """platform_remote_base: PLATFORM_REMOTE_BASE → /opt/platform (PLATFORM_ROOT не влияет)."""
    assert str(platform_remote_base({})) == DEFAULT_PLATFORM_BASE
    # RC 121: локальный PLATFORM_ROOT НЕ должен менять REMOTE-базу
    assert str(platform_remote_base({"PLATFORM_ROOT": "/tmp/root"})) == DEFAULT_PLATFORM_BASE
    assert str(platform_remote_base({"PLATFORM_REMOTE_BASE": "/tmp/remote", "PLATFORM_ROOT": "/tmp/root"})) == (
        "/tmp/remote"
    )


# 🧪 TRAP[TEST] · Regression · projects_base (A3)
# · Scenario: PROJECTS_BASE env → /opt/projects
# · Last fail: N/A (A3 — существующий резолвер)
# · Remove if: projects_base resolver removed
def test_projects_base() -> None:
    """projects_base → /opt/projects (default) или PROJECTS_BASE env."""
    assert str(projects_base({})) == "/opt/projects"
    assert str(projects_base({"PROJECTS_BASE": "/tmp/projects"})) == "/tmp/projects"
