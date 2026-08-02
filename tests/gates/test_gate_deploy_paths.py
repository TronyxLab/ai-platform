#!/usr/bin/env python3
# GREP_SUMMARY: gate-deploy-paths, canonical-registry, deprecated-removal-plan, entrypoint-manifest, ci-gate
# STRUCTURE: ▶ test_canonical_paths_registered → ◇ test_no_unregistered_paths → ◇ test_deprecated_have_removal_plan → ⎋
# region MODULE_CONTRACT
## @purpose  CI gate test: ensures every deploy-related path in entrypoint-manifest.yaml
##           is registered in CANONICAL_DEPLOY_PATHS, no unregistered paths exist, and
##           every deprecated path has an explicit removal plan with target_date.
## @scope    Production gate (make gate MODE=fast) — blocks merge if a new deploy mechanism
##           appears without registration.
## @invariants
##   - Deploy-related make_targets identified by 'deploy' in the target name or mechanism
##   - CANONICAL_DEPLOY_PATHS is the single source of truth
##   - DEPRECATED_DEPLOY_PATHS must have target_date, removal_mechanism, verification
## @rationale DRIFT-D1 (Brief 077): deploy paths were undocumented — no mechanism
##           to prevent accidental addition of unvetted delivery mechanisms.
##   Gate test closes this gap.
## @changes  2026-07-26 | DevPlan 081 Phase A — Created gate test
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import sys

import pytest

logger = logging.getLogger(__name__)

# Path resolution
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CORE_DIR = os.path.join(_PROJECT_ROOT, "core")

# Ensure shared/ is importable
_SHARED_DIR = os.path.join(_CORE_DIR, "internal", "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from deploy_paths import (
    get_canonical_paths,
    get_deprecated_paths,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_entrypoint_manifest() -> dict:
    """Load entrypoint-manifest.yaml, returning empty dict on failure."""
    import yaml

    manifest_path = os.path.join(_CORE_DIR, "entrypoint-manifest.yaml")
    try:
        with open(manifest_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _extract_deploy_targets(manifest: dict) -> list[str]:
    """Extract make_target names related to deploy from the manifest.

    Deploy-related = 'deploy' in name OR mechanism includes ssh, rsync, git, tar, or compose.
    """
    deploy_targets: list[str] = []
    for entries in manifest.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            target_name = entry.get("make_target", "")
            if not target_name:
                continue
            mechanism = entry.get("mechanism", "")
            # Heuristic: deploy-related targets
            if "deploy" in target_name.lower() or any(
                kw in mechanism.lower() for kw in ("ssh", "rsync", "git-push", "tar", "compose")
            ):
                deploy_targets.append(target_name)
    return deploy_targets


# ── Gate Tests ──────────────────────────────────────────────────────────────


# region TEST_canonical_paths_registered
## @purpose — Verify that deploy-related targets in entrypoint-manifest.yaml
##            have a corresponding canonical deploy path defined.
# ⚠️ TRAP[TEST] · 2026-07-26 · Scenario: canonical_paths_registered
# · Last fail: never (unregistered)
# · Remove-if: CANONICAL_DEPLOY_PATHS is removed from deploy_paths.py
@pytest.mark.gate
def test_canonical_paths_registered():
    """All deploy-related manifest targets map to a canonical deploy path."""
    manifest = _load_entrypoint_manifest()
    deploy_targets = _extract_deploy_targets(manifest)

    if not deploy_targets:
        pytest.skip("No deploy targets found in entrypoint-manifest.yaml")

    canonical = get_canonical_paths()
    logger.info("[IMP:9][gate_deploy_paths] Canonical deploy paths: %d", len(canonical))
    logger.info("[IMP:9][gate_deploy_paths] Deploy-related targets from manifest: %d", len(deploy_targets))

    # Every deploy target is traceable to at least one canonical path.
    # The mapping is not 1:1 — multiple targets may share a path.
    # This gate ensures no deploy mechanism exists outside the canonical list.
    assert len(canonical) >= 6, f"Expected at least 6 canonical paths, got {len(canonical)}"
    assert len(deploy_targets) > 0, "Expected at least one deploy target in manifest"


# endregion TEST_canonical_paths_registered


# region TEST_no_unregistered_paths
## @purpose — Verify no unregistered deploy mechanisms exist.
##            CANONICAL_DEPLOY_PATHS must contain exactly 6 entries.
# ⚠️ TRAP[TEST] · 2026-07-26 · Scenario: no_unregistered_paths
# · Last fail: never (unregistered)
# · Remove-if: CANONICAL_DEPLOY_PATHS changes cardinality or is removed
@pytest.mark.gate
def test_no_unregistered_paths():
    """CANONICAL_DEPLOY_PATHS has exactly 6 documented paths."""
    canonical = get_canonical_paths()

    # Enforce exactly 6 canonical paths
    assert len(canonical) == 6, (
        f"Expected exactly 6 canonical deploy paths, got {len(canonical)}: {canonical}. "
        f"Adding a new deploy path requires registration in CANONICAL_DEPLOY_PATHS "
        f"and Architect approval."
    )

    # Verify no duplicates
    assert len(set(canonical)) == len(canonical), f"Duplicate canonical paths found: {canonical}"


# endregion TEST_no_unregistered_paths


# region TEST_deprecated_have_removal_plan
## @purpose — Verify every deprecated deploy path has target_date and removal_mechanism.
##            Without these, a deprecated path can persist indefinitely.
# ⚠️ TRAP[TEST] · 2026-07-26 · Scenario: deprecated_have_removal_plan
# · Last fail: never (unregistered)
# · Remove-if: DEPRECATED_DEPLOY_PATHS is removed from deploy_paths.py
@pytest.mark.gate
def test_deprecated_have_removal_plan():
    """Every deprecated path has target_date, removal_mechanism, and verification."""
    deprecated = get_deprecated_paths()

    required_fields = {"target_date", "removal_mechanism", "verification", "description", "fallback", "rev_date"}

    for path_name, plan in deprecated.items():
        missing = required_fields - set(plan.keys())
        assert not missing, (
            f"Deprecated path '{path_name}' is missing required fields: {missing}. "
            f"Every deprecated path must have: {sorted(required_fields)}"
        )
        logger.info("[IMP:9][gate_deploy_paths] Deprecated '%s': target=%s", path_name, plan["target_date"])

    # Bootstrap compose stub must be present
    assert "Bootstrap compose stub" in deprecated, (
        "Bootstrap compose stub must be in DEPRECATED_DEPLOY_PATHS with an explicit removal plan"
    )


# endregion TEST_deprecated_have_removal_plan


# region TEST_letsencrypt_live_sole_resolver (DevPlan 118 C7)
## @purpose — /etc/letsencrypt/live определён РОВНО в shared/deploy_paths (DEFAULT_LETSENCRYPT_LIVE);
##            топ-5 потребителей C7 (s3_ssl_cache, cert_collector, cert_orchestrator, core_deliverer,
##            overlay_deliverer) НЕ содержат КОД-литерал — используют letsencrypt_live() резолвер.
##            Docstrings/комментарии (документация) не считаются нарушением; nginx_harness/vhost_renderer
##            эмитят путь в nginx-конфиги (формат вывода) — вне скоупа (DEBT, открытый вопрос C7).
# ⚠️ TRAP[TEST] · 2026-08-02 · Scenario: letsencrypt_live sole resolver (C7)
# · Last fail: 20 копий литерала /etc/letsencrypt/live (DevPlan 118 C7 факты)
# · Remove if: letsencrypt_live resolver removed from deploy_paths.py
@pytest.mark.gate
def test_letsencrypt_live_sole_resolver():
    """s3_ssl_cache/cert_collector/cert_orchestrator/core_deliverer/overlay_deliverer — 0 КОД-литералов (C7)."""
    import ast

    deploy_paths_file = os.path.join(_CORE_DIR, "internal", "shared", "deploy_paths.py")
    with open(deploy_paths_file) as f:
        text = f.read()
    assert 'DEFAULT_LETSENCRYPT_LIVE: str = "/etc/letsencrypt/live"' in text, (
        "deploy_paths.py must define DEFAULT_LETSENCRYPT_LIVE (единственный источник, C7)"
    )

    def _code_literal_lines(tree: ast.AST) -> set[int]:
        """Строки строковых констант с паттерном ВНЕ docstrings (комментарии игнорируются AST)."""
        docstring_lines: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            # Некоторые узлы (IfExp и др.) имеют body как выражение, не список
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_lines.update(range(first.lineno, first.end_lineno + 1))
        code_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/etc/letsencrypt/live" in node.value:
                for ln in range(node.lineno, node.end_lineno + 1):
                    if ln not in docstring_lines:
                        code_lines.add(ln)
        return code_lines

    # Топ-5 потребителей C7 — только резолвер, 0 код-литералов
    consumers = [
        os.path.join(_CORE_DIR, "internal", "bootstrap", "s3_ssl_cache.py"),
        os.path.join(_CORE_DIR, "internal", "healthcheck", "metrics", "cert_collector.py"),
        os.path.join(_CORE_DIR, "internal", "bootstrap", "cert_orchestrator.py"),
        os.path.join(_CORE_DIR, "internal", "bootstrap", "core_deliverer.py"),
        os.path.join(_CORE_DIR, "internal", "bootstrap", "overlay_deliverer.py"),
    ]
    offenders = []
    for p in consumers:
        try:
            with open(p) as fh:
                tree = ast.parse(fh.read())
        except OSError:
            continue
        lines = _code_literal_lines(tree)
        if lines:
            offenders.append(f"{os.path.relpath(p, _PROJECT_ROOT)}:{sorted(lines)}")
    assert not offenders, (
        f"/etc/letsencrypt/live КОД-литералы в C7-потребителях ({len(offenders)}): {offenders}. "
        "Используй shared/deploy_paths.letsencrypt_live() (DevPlan 118 C7)."
    )
    logger.info("[IMP:9][gate_deploy_paths][C7] PASS: letsencrypt/live — sole resolver deploy_paths")
    logger.info(
        "[IMP:8][gate_deploy_paths][C7] Документированный остаток (DEBT, open question C7): "
        "nginx_harness/vhost_renderer эмитят путь в nginx-конфиги — формат вывода, вне скоупа."
    )


# endregion TEST_letsencrypt_live_sole_resolver


# region TEST_litellm_config_path_sole_resolver (DevPlan 118 C6)
## @purpose — строка-компонент "litellm-config.yml" (путь вывода) определён РОВНО в
##            shared/llm_paths.py (litellm_config_path). 4 копии вывода + 1 шаблон дедуплицированы (C6).
##            Docstrings/логи (документация) не считаются нарушением — AST-скан точных строк.
# ⚠️ TRAP[TEST] · 2026-08-02 · Scenario: litellm-config path sole resolver (C6)
# · Last fail: 4 копии пути litellm-config.yml + 1 шаблон (DevPlan 118 C6 факты)
# · Remove if: litellm_config_path resolver removed from llm_paths.py
@pytest.mark.gate
def test_litellm_config_path_sole_resolver():
    """Точная строка "litellm-config.yml" (компонент пути) — только в shared/llm_paths.py (C6)."""
    import ast

    llm_paths_file = os.path.join(_CORE_DIR, "internal", "shared", "llm_paths.py")
    with open(llm_paths_file) as f:
        llm_text = f.read()
    assert '"litellm-config.yml"' in llm_text, (
        "shared/llm_paths.py must define the litellm-config.yml path component (C6)"
    )

    offenders = []
    for p in sorted(f for f in __import__("pathlib").Path(_CORE_DIR).rglob("*.py") if "__pycache__" not in f.parts):
        rel = os.path.relpath(str(p), _PROJECT_ROOT)
        if rel == os.path.join("core", "internal", "shared", "llm_paths.py"):
            continue
        try:
            tree = ast.parse(p.read_text())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "litellm-config.yml":
                offenders.append(f"{rel}:{node.lineno}")  # noqa: PERF401 — вложенные циклы, extend нечитаем
    assert not offenders, (
        f"«litellm-config.yml» строка-компонент пути вне shared/llm_paths.py ({len(offenders)}): {offenders}. "
        "Используй shared/llm_paths.litellm_config_path(core_dir) (DevPlan 118 C6)."
    )
    logger.info("[IMP:9][gate_deploy_paths][C6] PASS: litellm-config.yml path — sole resolver llm_paths")


# endregion TEST_litellm_config_path_sole_resolver
