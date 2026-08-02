#!/usr/bin/env python3
# GREP_SUMMARY: hermes-images L1 L2 hermes-agent-base hermes-agent-context build platform-amd64 buildkit cache CONTEXT-guard
# STRUCTURE: ▶ parse action (build-platform|build-context) → ◇ L2: CONTEXT guard → ○ docker build (--platform linux/amd64, BuildKit cache) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Build hermes-agent Docker images: L1 (hermes-agent-base) and L2 (hermes-agent-context).
##           Python-порт hermes-images.sh (DevPlan 118 E8): docker build через subprocess
##           (docker_orchestrator-стиль), CONTEXT guard в Python.
## @scope    Called by core/entrypoints/build.sh (make hermes-build-platform / hermes-build-context)
##           via thin facade core/internal/build/hermes-images.sh.
## @invariants
##   - L1 builds locally as hermes-agent-base — NEVER pushed to registry (R1)
##   - L2 requires CONTEXT env var — builds as hermes-agent-context (guard exit 1)
##   - --platform linux/amd64 forced (QEMU on ARM64, native no-op on x86_64)
##   - BuildKit local cache /tmp/.hermes-build-cache (mode=max both directions)
##   - No GHCR push logic — images built locally only
## @rationale L1 images pushed to ghcr.io as DR backup (hermes-push-l1); L2 pushed by context CI.
##            Strangler E8: subprocess-оркестрация docker build — тестируемый CONTEXT guard.
## @changes  2026-08-02 | DevPlan 118 E8 — Created (Python-порт hermes-images.sh, 77 LOC)
## @see      core/internal/build/hermes-images.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

L1_IMAGE = "hermes-agent-base"
L2_IMAGE = "hermes-agent-context"
BUILD_PLATFORM = "linux/amd64"
_CACHE_DIR = "/tmp/.hermes-build-cache"  # nosec B108 — BuildKit local cache dir (legacy hermes-images.sh канон)
# Канон-таймаут docker build (hermes-сборки L1/L2 — тяжёлые, BUILD_TIMEOUT=300 из shared/timeouts)
from core.internal.shared.timeouts import BUILD_TIMEOUT

PLATFORM_ROOT = Path(__file__).resolve().parents[3]  # repo root (core/internal/build → root)


# region FUNC_build_l1
## @purpose  Собрать L1 (hermes-agent-base): BuildKit cache dir + docker build --platform linux/amd64.
## @io       ⇥ None → ⎋ bool (success)
## @complexity O(1) — один docker build subprocess
def build_l1() -> bool:
    """Build L1 image (hermes-agent-base) with BuildKit local cache."""
    dockerfile = PLATFORM_ROOT / "core" / "modules" / "hermes-agent" / "build" / "Dockerfile"
    context = PLATFORM_ROOT / "core" / "modules" / "hermes-agent" / "build"
    os.makedirs(_CACHE_DIR, exist_ok=True)
    logger.info("[IMP:9][hermes-images][L1] BuildKit cache directory ready: %s", _CACHE_DIR)

    cmd = [
        "docker",
        "build",
        "--platform",
        BUILD_PLATFORM,
        "-t",
        L1_IMAGE,
        "--cache-from",
        f"type=local,src={_CACHE_DIR}",
        "--cache-to",
        f"type=local,dest={_CACHE_DIR},mode=max",
        "-f",
        str(dockerfile),
        str(context),
    ]
    logger.info("[IMP:8][hermes-images][L1] docker build: %s", " ".join(cmd))
    return _run(cmd, "L1")


# endregion FUNC_build_l1


# region FUNC_build_l2
## @purpose  Собрать L2 (hermes-agent-context): CONTEXT guard (fail-fast) + docker build с --build-arg CONTEXT.
## @io       ⇥ context: str (из CONTEXT env) → ⎋ bool (success)
## @complexity O(1) — один docker build subprocess
def build_l2(context: str) -> bool:
    """Build L2 image (hermes-agent-context). Requires non-empty CONTEXT (guard)."""
    if not context:
        logger.error("[IMP:10][hermes-images][L2] ERROR: CONTEXT env var is required for L2 build")
        return False
    dockerfile = PLATFORM_ROOT / "core" / "modules" / "hermes-agent" / "context" / "Dockerfile"
    os.makedirs(_CACHE_DIR, exist_ok=True)
    logger.info("[IMP:9][hermes-images][L2] BuildKit cache directory ready: %s", _CACHE_DIR)

    cmd = [
        "docker",
        "build",
        "--platform",
        BUILD_PLATFORM,
        "-t",
        L2_IMAGE,
        "--build-arg",
        f"CONTEXT={context}",
        "--cache-from",
        f"type=local,src={_CACHE_DIR}",
        "--cache-to",
        f"type=local,dest={_CACHE_DIR},mode=max",
        "-f",
        str(dockerfile),
        str(PLATFORM_ROOT),
    ]
    logger.info("[IMP:8][hermes-images][L2] docker build (context=%s)", context)
    return _run(cmd, "L2")


# endregion FUNC_build_l2


# region FUNC_run
## @purpose  Выполнить docker build через subprocess с канон-таймаутом (BUILD_TIMEOUT=300).
## @io       ⇥ cmd: list[str], stage: str ("L1"/"L2") → ⎋ bool
## @complexity O(1) — single subprocess.run
def _run(cmd: list[str], stage: str) -> bool:
    """Run docker build subprocess with BUILD_TIMEOUT; False on failure/timeout."""
    try:
        result = subprocess.run(cmd, timeout=BUILD_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("[IMP:9][hermes-images][%s] docker build error: %s", stage, exc)
        return False
    if result.returncode != 0:
        logger.error("[IMP:10][hermes-images][%s] docker build FAILED (exit %d)", stage, result.returncode)
        return False
    logger.info("[IMP:9][hermes-images][%s] === build complete: %s ===", stage, L1_IMAGE if stage == "L1" else L2_IMAGE)
    return True


# endregion FUNC_run


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.build.hermes_images {build-platform|build-context}`.

    ▶ ┌argv┐ → ◇ action dispatch → ◇ L2 CONTEXT guard → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Build hermes-agent Docker images")
    parser.add_argument(
        "action",
        choices=["build-platform", "L1", "build-context", "L2"],
        help="build-platform (L1) | build-context (L2, requires CONTEXT env)",
    )
    args = parser.parse_args()

    if args.action in ("build-platform", "L1"):
        return 0 if build_l1() else 1
    return 0 if build_l2(os.environ.get("CONTEXT", "")) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
