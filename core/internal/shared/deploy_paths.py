#!/usr/bin/env python3
# GREP_SUMMARY: deploy-paths, canonical, deprecated, registry, bootstrap-compose-stub, removal-plan, projects-base, resolver, letsencrypt-live, node-configs-remote, platform-remote-base
# STRUCTURE: ▶ CANONICAL_DEPLOY_PATHS (6 paths) → ◇ DEPRECATED_DEPLOY_PATHS (1 stub) → ⊕ get_canonical_paths() →
#            ▶ projects_base() ┌env┐ → ◇ PROJECTS_BASE → ⎋ Path (default /opt/projects) →
#            ▶ letsencrypt_live() / node_configs_remote() / platform_remote_base() (C7) → ⎋ Path
# region MODULE_CONTRACT
## @purpose  Canonical deploy path registry — single source of truth for all code delivery
##           mechanisms. Gate test validates every deploy path in entrypoint-manifest.yaml
##           is registered here. DEPRECATED_DEPLOY_PATHS includes explicit removal plans.
##           DevPlan 118 C7: +реальные прод-резолверы (letsencrypt_live, node_configs_remote,
##           platform_remote_base) — дедупликация литералов /etc/letsencrypt/live (20 копий),
##           /opt/node-configs, /opt/platform у топ-5 потребителей.
## @scope    Read by tests/gates/test_gate_deploy_paths.py for CI enforcement.
##           Prod-потребители резолверов: s3_ssl_cache, cert_orchestrator, cert_collector,
##           core_deliverer, overlay_deliverer (C7). No runtime dependencies — pure data module.
## @invariants
##   1. Every deploy-related make_target in entrypoint-manifest.yaml must map to a
##      canonical path defined here. Adding a new deploy mechanism without registering
##      it in CANONICAL_DEPLOY_PATHS blocks CI merge.
##   2. Every DEPRECATED entry must have target_date, removal_mechanism, and verification.
##   3. This module has zero imports from other shared modules — it is Phase A independent.
##   4. Резолверы (C7): дефолты определены ТОЛЬКО здесь; env-переменные приоритетнее;
##      никогда не raise — всегда возвращают Path.
## @rationale DRIFT-D1 (Brief 077): deploy pipelines are the most critical production domain,
##           yet there was no canonical inventory of deploy paths. CI gate enforcement
##           prevents accidental divergence between manifest, code, and documentation.
##           C7 (DevPlan 118): литералы путей размножены (20 копий /etc/letsencrypt/live,
##           /opt/node-configs, /opt/platform) — резолверы централизуют канон.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created deploy path registry
##           2026-08-02 | DevPlan 118 C7 — +letsencrypt_live/node_configs_remote/platform_remote_base
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
from pathlib import Path

# ── Canonical Deploy Paths ──────────────────────────────────────────────────
# These are the 6 documented code delivery mechanisms. Every deploy-related
# make_target in entrypoint-manifest.yaml must be traceable to one of these paths.

CANONICAL_DEPLOY_PATHS: list[str] = [
    # 1. CI → receive verb + dispatcher (DevPlan 116 B1)
    #    git push → GitHub CI → tar via SSH forced-command → orchestrator_cli dispatch receive
    "CI → receive + dispatcher (orchestrator_cli dispatch)",
    # 2. make deploy-project (direct)
    #    deliver (ForcedCommandChannel receive <project> <version>), bypass CI, emergency fallback
    "make deploy-project (deliver, ForcedCommandChannel receive)",
    # 3. context_deployer.py (Python)
    #    ghcr.io pull (primary) + build-on-node fallback, idempotent health-gate
    "context_deployer.py (Python)",
    # 4. deploy-modules.sh (system modules)
    #    docker compose up for system modules (install.sh path)
    "deploy-modules.sh (system modules)",
    # 5. Core SCP/rsync
    #    CI workflow core-deploy → SCP/rsync core/ to VPS /opt/platform/core/
    "Core SCP/rsync",
    # 6. Context-overlay git
    #    git clone/pull via ensure_context_repo() on VPS
    "Context-overlay git",
]

# ── Deprecated Deploy Paths ────────────────────────────────────────────────
# Each entry must have a removal plan with target_date, removal_mechanism,
# verification, fallback, and rev_date. Gate test enforces this structure.

DEPRECATED_DEPLOY_PATHS: dict[str, dict[str, str]] = {
    "Bootstrap compose stub": {
        "description": (
            "Temporary nginx:alpine container generated during node bootstrap, "
            "replaced by first real project deployment via "
            "context_deployer._deploy_single_project_via_orchestrator()"
        ),
        "removal_mechanism": ("docker compose up -d на реальный проект заменяет заглушку автоматически"),
        "verification": "docker compose ps --format '{{.Image}}' | grep -c 'nginx:alpine' returns 0",
        "target_date": "2026-08-15",
        "fallback": "docker compose down nginx-stub && docker rm nginx-stub",
        "rev_date": "2026-09-01",
    },
}


# region FUNC_get_canonical_paths
## @purpose — Return immutable list of canonical deploy paths.
## @io — ⇥ None → ⎋ list[str] (6 paths)
## @complexity — O(1)
def get_canonical_paths() -> list[str]:
    """Return the list of canonical deploy paths."""
    return list(CANONICAL_DEPLOY_PATHS)


# endregion FUNC_get_canonical_paths


# region FUNC_get_deprecated_paths
## @purpose — Return immutable dict of deprecated deploy paths with removal plans.
## @io — ⇥ None → ⎋ dict[str, dict[str, str]]
## @complexity — O(1)
def get_deprecated_paths() -> dict[str, dict[str, str]]:
    """Return the dict of deprecated deploy paths with removal plans."""
    return dict(DEPRECATED_DEPLOY_PATHS)


# endregion FUNC_get_deprecated_paths


# ── PROJECTS_BASE resolver (DevPlan 118 A3) ──────────────────────────────────

DEFAULT_PROJECTS_BASE: str = "/opt/projects"
"""## @invariant Канонический дефолт PROJECTS_BASE (совпадает с orchestrator/deploy_history/context_deployer)."""


# region FUNC_projects_base
## @purpose — Резолвер PROJECTS_BASE из env-цепочки (PROJECTS_BASE env → /opt/projects).
##            Единый резолвер для reconciler_projects и будущих потребителей (C7 — активация deploy_paths).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env PROJECTS_BASE приоритетнее дефолта (тот же канон, что orchestrator_cli/receive)
##   - Никогда не raise — всегда возвращает Path
##   - Параметр env позволяет тестировать без monkeypatch.setenv (tmp_path в тестах)
def projects_base(env: dict | None = None) -> Path:
    """Resolve PROJECTS_BASE from the environment chain (env → /opt/projects)."""
    source = os.environ if env is None else env
    return Path(str(source.get("PROJECTS_BASE", DEFAULT_PROJECTS_BASE)))


# endregion FUNC_projects_base


# ── Remote path resolvers (DevPlan 118 C7 — активация deploy_paths) ──────────
# Реальные прод-потребители: s3_ssl_cache, cert_orchestrator, cert_collector,
# core_deliverer, overlay_deliverer (топ-5 по DevPlan 118 C7).

DEFAULT_LETSENCRYPT_LIVE: str = "/etc/letsencrypt/live"
"""## @invariant Каноническая директория Let's Encrypt live certs (VPS-дефолт)."""

DEFAULT_NODE_CONFIGS_REMOTE: str = "/opt/node-configs"
"""## @invariant Каноническая remote-директория node-configs (core_deliverer NODE_CONFIGS_REMOTE_BASE)."""

DEFAULT_PLATFORM_BASE: str = "/opt/platform"
"""## @invariant Канонический remote platform base (PLATFORM_REMOTE_BASE → /opt/platform; PLATFORM_ROOT исключён из remote-цепочки — TRAP[BUG] 2026-08-03)."""


# region FUNC_letsencrypt_live
## @purpose — Резолвер /etc/letsencrypt/live (DevPlan 118 C7). Дедупликация 20 копий литерала.
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env LETSENCRYPT_LIVE приоритетнее дефолта (тесты/альтернативные окружения)
##   - Никогда не raise — всегда возвращает Path
def letsencrypt_live(env: dict | None = None) -> Path:
    """Resolve Let's Encrypt live dir (env → /etc/letsencrypt/live, C7)."""
    source = os.environ if env is None else env
    return Path(str(source.get("LETSENCRYPT_LIVE", DEFAULT_LETSENCRYPT_LIVE)))


# endregion FUNC_letsencrypt_live


# region FUNC_node_configs_remote
## @purpose — Резолвер remote node-configs base (DevPlan 118 C7). Тот же канон, что
##            core_deliverer.resolve_node_configs_base (NODE_CONFIGS_REMOTE_BASE → /opt/node-configs).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env NODE_CONFIGS_REMOTE_BASE приоритетнее дефолта (мигрировано из core_deliverer)
##   - Никогда не raise — всегда возвращает Path
def node_configs_remote(env: dict | None = None) -> Path:
    """Resolve remote node-configs base (env → /opt/node-configs, C7)."""
    source = os.environ if env is None else env
    return Path(str(source.get("NODE_CONFIGS_REMOTE_BASE", DEFAULT_NODE_CONFIGS_REMOTE)))


# endregion FUNC_node_configs_remote


# region FUNC_platform_remote_base
## @purpose — Резолвер remote platform base (DevPlan 118 C7). Цепочка:
##            PLATFORM_REMOTE_BASE → /opt/platform (тот же канон, что
##            core_deliverer.resolve_remote_base / scp-deliver.sh:129; PLATFORM_ROOT
##            исключён из remote-цепочки — TRAP[BUG] 2026-08-03 ниже).
## @io — ⇥ env: dict | None (None = os.environ) → ⎋ Path
## @complexity — O(1)
## @invariants
##   - env PLATFORM_REMOTE_BASE — единственный env-override remote-базы (PLATFORM_ROOT НЕ участвует)
##   - Никогда не raise — всегда возвращает Path
def platform_remote_base(env: dict | None = None) -> Path:
    """Resolve remote platform base (PLATFORM_REMOTE_BASE → /opt/platform, C7).

    ⚠️ TRAP[BUG] · 2026-08-03 · P1 · PLATFORM_ROOT УБРАН из remote-цепочки (RC 121 e2e)
    · Symptom: remote_executor VPS_NODE_LIFECYCLE ложно детектил «мы на VPS» на dev-машине —
    ·   make передаёт PLATFORM_ROOT=<локальный>, node-lifecycle.sh существует локально.
    · Root: локальный PLATFORM_ROOT не должен влиять на REMOTE-резолюцию; PLATFORM_REMOTE_BASE
    ·   — единственный env-override remote-базы (задаётся явно, не наследует локальный корень).
    · Fix: цепочка PLATFORM_REMOTE_BASE → /opt/platform. Локальный поиск node.yaml использует
    ·   PLATFORM_ROOT напрямую (см. remote_executor._resolve_host).
    """
    source = os.environ if env is None else env
    return Path(str(source.get("PLATFORM_REMOTE_BASE") or DEFAULT_PLATFORM_BASE))


# endregion FUNC_platform_remote_base
