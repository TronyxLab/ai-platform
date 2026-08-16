#!/usr/bin/env python3
# GREP_SUMMARY: hermes-images L1 L2 hermes-agent-base hermes-agent-context build platform-amd64 buildkit cache CONTEXT-guard
# STRUCTURE: ▶ parse action (build-platform|build-context) → ◇ L2: CONTEXT guard → ○ docker build (--platform linux/amd64, BuildKit cache) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Build hermes-agent Docker images: L1 (hermes-agent-base) and L2 (hermes-agent-context).
##           Python-порт hermes-images.sh (DevPlan 118 E8): docker build через subprocess
##           (docker_orchestrator-стиль), CONTEXT guard в Python.
## @scope    Called by core/entrypoints/build.sh (make hermes-build-platform / hermes-build-context)
##           напрямую (`python3 -m core.internal.build.hermes_images`) — middle-hop
##           hermes-images.sh схлопнут (DevPlan 173 W1.1).
## @invariants
##   - L1 builds locally as hermes-agent-base — NEVER pushed to registry (R1)
##   - L2 requires CONTEXT env var — builds as hermes-agent-context (guard exit 1)
##   - --platform linux/amd64 forced (QEMU on ARM64, native no-op on x86_64)
##   - BuildKit local cache /tmp/.hermes-build-cache (mode=max both directions)
##   - No GHCR push logic — images built locally only
## @rationale L1 images pushed to ghcr.io as DR backup (hermes-push-l1); L2 pushed by context CI.
##            Strangler E8: subprocess-оркестрация docker build — тестируемый CONTEXT guard.
## @changes  2026-08-02 | DevPlan 118 E8 — Created (Python-порт hermes-images.sh, 77 LOC)
## @changes  2026-08-16 | DevPlan 173 W1.1 — middle-hop hermes-images.sh удалён; entrypoint вызывает напрямую
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

L1_IMAGE = "hermes-agent-base"
L2_IMAGE = "hermes-agent-context"
BUILD_PLATFORM = "linux/amd64"
_CACHE_DIR = "/tmp/.hermes-build-cache"  # nosec B108 — BuildKit local cache dir (канон 118 E8)
# Канон-таймаут docker build (hermes-сборки L1/L2 — тяжёлые, BUILD_TIMEOUT=300 из shared/timeouts)
from core.internal.shared.timeouts import BUILD_TIMEOUT

PLATFORM_ROOT = Path(__file__).resolve().parents[3]  # repo root (core/internal/build → root)

# W11: DI-тип runner (DevPlan 167 D1) — subprocess.run-контракт (fake-раннеры тестов — тот же)
BuildRunner = Callable[..., subprocess.CompletedProcess[str]]


# region DATA_CliArgs
class CliArgs(argparse.Namespace):
    """Типизированный namespace CLI (W11): ТОЛЬКО аннотации без значений.

    ## @purpose  Значения НЕ задаются class-атрибутами — hasattr(namespace, dest)
    ##            перебивает parser-дефолты; поля заполняет parse_args(namespace=CliArgs()).
    """

    action: str  # pyright: ignore[reportUninitializedInstanceVariable] — W11 argparse fills (без class-value дефолтов)


# endregion DATA_CliArgs


# region FUNC_build_l1
## @purpose  Собрать L1 (hermes-agent-base): BuildKit cache dir + docker build --platform linux/amd64.
## @io       ⇥ runner: Callable | None (subprocess.run override, DI — DevPlan 167 D1),
##           env: dict[str, str] | None (PLATFORM_ROOT/CACHE_DIR override) → ⎋ bool (success)
## @complexity O(1) — один docker build subprocess
def build_l1(runner: BuildRunner | None = None, env: dict[str, str] | None = None) -> bool:
    """Build L1 image (hermes-agent-base) with BuildKit local cache.

    Args:
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional path-env override (DI — DevPlan 167 D1): keys PLATFORM_ROOT/CACHE_DIR.
            None/пусто = модульные константы (backward compat).
    """
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · runner + env-dict DI (DevPlan 167 D1)
    # · Rejected: прямой subprocess.run + патч модульных PLATFORM_ROOT/_CACHE_DIR
    # · Reason: seam = тестируемость реального вызова (unit-тесты передают fake runner/tmp пути) · Rev:
    # ·   при изменении docker build контракта
    env = env if env is not None else {}
    platform_root = Path(env.get("PLATFORM_ROOT", str(PLATFORM_ROOT)))
    cache_dir = env.get("CACHE_DIR", _CACHE_DIR)
    dockerfile = platform_root / "core" / "modules" / "hermes-agent" / "build" / "Dockerfile"
    context = platform_root / "core" / "modules" / "hermes-agent" / "build"
    Path(cache_dir).mkdir(exist_ok=True, parents=True)
    logger.info("[IMP:9][hermes-images][L1] BuildKit cache directory ready: %s", cache_dir)

    cmd = [
        "docker",
        "build",
        "--platform",
        BUILD_PLATFORM,
        "-t",
        L1_IMAGE,
        "--cache-from",
        f"type=local,src={cache_dir}",
        "--cache-to",
        f"type=local,dest={cache_dir},mode=max",
        "-f",
        str(dockerfile),
        str(context),
    ]
    logger.info("[IMP:8][hermes-images][L1] docker build: %s", " ".join(cmd))
    return _run(cmd, "L1", runner=runner)


# endregion FUNC_build_l1


# region FUNC_build_l2
## @purpose  Собрать L2 (hermes-agent-context): CONTEXT guard (fail-fast) + docker build с --build-arg CONTEXT.
## @io       ⇥ context: str (из CONTEXT env), runner: Callable | None, env: dict[str, str] | None
##           → ⎋ bool (success)
## @complexity O(1) — один docker build subprocess
def build_l2(
    context: str,
    runner: BuildRunner | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Build L2 image (hermes-agent-context). Requires non-empty CONTEXT (guard).

    Args:
        context: CONTEXT value (must be non-empty — guard).
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional path-env override (DI — DevPlan 167 D1): keys PLATFORM_ROOT/CACHE_DIR.
    """
    if not context:
        logger.error("[IMP:10][hermes-images][L2] ERROR: CONTEXT env var is required for L2 build")
        return False
    env = env if env is not None else {}
    platform_root = Path(env.get("PLATFORM_ROOT", str(PLATFORM_ROOT)))
    cache_dir = env.get("CACHE_DIR", _CACHE_DIR)
    dockerfile = platform_root / "core" / "modules" / "hermes-agent" / "context" / "Dockerfile"
    Path(cache_dir).mkdir(exist_ok=True, parents=True)
    logger.info("[IMP:9][hermes-images][L2] BuildKit cache directory ready: %s", cache_dir)

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
        f"type=local,src={cache_dir}",
        "--cache-to",
        f"type=local,dest={cache_dir},mode=max",
        "-f",
        str(dockerfile),
        str(platform_root),
    ]
    logger.info("[IMP:8][hermes-images][L2] docker build (context=%s)", context)
    return _run(cmd, "L2", runner=runner)


# endregion FUNC_build_l2


# region FUNC_run
## @purpose  Выполнить docker build через subprocess с канон-таймаутом (BUILD_TIMEOUT=300).
## @io       ⇥ cmd: list[str], stage: str ("L1"/"L2"), runner: Callable | None → ⎋ bool
## @complexity O(1) — single subprocess.run
def _run(cmd: list[str], stage: str, runner: BuildRunner | None = None) -> bool:
    """Run docker build subprocess with BUILD_TIMEOUT; False on failure/timeout.

    Args:
        cmd: docker build command list.
        stage: "L1"/"L2" — log stage tag.
        runner: Optional subprocess.run override (DI — DevPlan 167 D1). None = real subprocess.run.
    """
    run = runner if runner is not None else subprocess.run
    try:
        result = run(cmd, timeout=BUILD_TIMEOUT, check=False)
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
def main(
    argv: list[str] | None = None,
    runner: BuildRunner | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """CLI entry: `python3 -m core.internal.build.hermes_images {build-platform|build-context}`.

    ▶ ┌argv┐ → ◇ action dispatch → ◇ L2 CONTEXT guard → ⎋ exit 0|1

    Args:
        argv: Optional CLI args override (DI — DevPlan 167 D1, AF-4). None = sys.argv.
        runner: Optional subprocess.run override (DI). None = real subprocess.run.
        env: Optional env override (DI — DevPlan 167 D1): CONTEXT/PLATFORM_ROOT/CACHE_DIR.
            None/пусто = os.environ fallback для CONTEXT + модульные константы.
    """
    env = env if env is not None else {}
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Build hermes-agent Docker images")
    parser.add_argument(
        "action",
        choices=["build-platform", "L1", "build-context", "L2"],
        help="build-platform (L1) | build-context (L2, requires CONTEXT env)",
    )
    args = parser.parse_args(argv, namespace=CliArgs())  # W11: типизированный namespace

    if args.action in {"build-platform", "L1"}:
        return 0 if build_l1(runner=runner, env=env) else 1
    context = env.get("CONTEXT", os.environ.get("CONTEXT", ""))
    return 0 if build_l2(context, runner=runner, env=env) else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
