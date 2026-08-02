#!/usr/bin/env python3
# GREP_SUMMARY: llm-provision litellm-config render provision-llm subprocess non-fatal post-deploy
# STRUCTURE: ▶ ┌CORE_DIR┐ → ◇ config_renderer.py exists? → ⊕ render → ◇ provision-llm.sh exists? → ⊕ provision → ⎋ None (non-fatal)
# region MODULE_CONTRACT
## @purpose  Post-deploy LiteLLM pipeline extracted from context_deployer.py (DevPlan 117 G T58.5):
##           regenerate litellm-config.yml from policy.yaml, then provision virtual keys for all
##           LLM consumers. Both steps are non-fatal on failure.
## @scope    Consumed by core/internal/bootstrap/deploy/context_deployer.py (lazy import) during
##           deploy_context(). Uses subprocess (consistent with state_machine.py pattern).
## @invariants
##   - Non-fatal: both steps log WARN on failure, never raise
##   - config_renderer.py missing → WARN, skip render
##   - provision-llm.sh missing → WARN, skip provision
##   - provision-llm.sh non-zero → WARN with stderr excerpt
## @rationale  DevPlan 117 G T58.5 — extracted verbatim (_render_and_provision_llm, ~56 LOC) with
##            all LDD logs and docstring preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T58.5 — extracted from context_deployer.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
from core.internal.shared.llm_paths import litellm_config_path

logger = logging.getLogger(__name__)

# PLATFORM_ROOT mirrored from context_deployer (os.environ override for tests)
_PLATFORM_ROOT = os.environ.get("PLATFORM_ROOT", "/opt/platform")


# region FUNC_render_and_provision_llm
def render_and_provision_llm() -> None:
    """Render litellm-config.yml from policy.yaml and provision virtual keys.

    ## @purpose  Post-deploy LLM pipeline: regenerate litellm-config.yml from policy
    ##            to pick up any new aliases/profiles, then provision virtual keys
    ##            for all LLM consumers. Both are non-fatal on failure.
    ##            Uses subprocess (consistent with state_machine.py pattern) to avoid
    ##            PYTHONPATH/dependency resolution issues with module-level imports.
    ## @io  ⎋ None (side-effect: writes litellm-config.yml, provisions keys)
    ## @complexity O(render + provision)
    """
    core_dir = os.environ.get("CORE_DIR", f"{_PLATFORM_ROOT}/core")

    # Step 1: Render litellm-config.yml via subprocess
    logger.info("[IMP:7][llm] Rendering litellm-config.yml from policy.yaml...")
    try:
        renderer_path = os.path.join(core_dir, "internal", "llm", "config_renderer.py")
        config_output = str(litellm_config_path(core_dir))  # C6: единый путь shared/llm_paths
        if os.path.isfile(renderer_path):
            subprocess.run(
                ["python3", renderer_path, "--output", config_output],
                capture_output=True,
                text=True,
                timeout=30,
            )
            logger.info("[IMP:9][llm] litellm-config.yml rendered via subprocess")
        else:
            logger.warning("[IMP:7][llm] config_renderer.py not found at %s", renderer_path)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][llm] Failed to render litellm-config.yml (non-fatal): %s", e)

    # Step 2: Provision virtual keys via subprocess
    logger.info("[IMP:7][llm] Provisioning LiteLLM virtual keys...")
    try:
        provision_entrypoint = os.path.join(core_dir, "entrypoints", "provision-llm.sh")
        if os.path.isfile(provision_entrypoint):
            result = subprocess.run(
                ["bash", provision_entrypoint],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                logger.info("[IMP:9][llm] Key provisioning succeeded via subprocess")
            else:
                logger.warning(
                    "[IMP:7][llm] Key provisioning returned %d: %s",
                    result.returncode,
                    result.stderr.strip()[:200],
                )
        else:
            logger.warning("[IMP:7][llm] provision-llm.sh not found at %s", provision_entrypoint)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][llm] Failed to provision keys (non-fatal): %s", e)


# endregion FUNC_render_and_provision_llm
